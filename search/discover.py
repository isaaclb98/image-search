"""
search/discover.py

Discovery rabbithole: a read-only, ephemeral, two-image pick flow
that uses the Qdrant Recommend API to gradually converge on a
user's taste without any text input.

  - Rounds 1-SEED_ROUNDS use random pairs (no signal yet).
  - From round SEED_ROUNDS+1 onward, the rabbithole runs in
    bursts of RABBITHOLE_BURST_SIZE rounds each. Every burst
    starts with one fresh recommend(positive=liked,
    negative=disliked) over the top RECOMMEND_OVERFETCH
    unseen, re-ranks that pool with Maximal Marginal
    Relevance (MMR) to pick a diverse subset of MMR_POOL_SIZE
    ids to cache as the burst pool, then draws 2 unseen per
    round from that cached pool using stratified sampling (1
    from the top third, 1 from the bottom third) for the rest
    of the burst. When the burst completes, the next round
    triggers a fresh recommend with all accumulated
    likes/dislikes. The MMR step is the diversity-amplifier:
    without it, the top-N of a recommend call against a tight
    liked-centroid are essentially the same cluster, so even
    top-third/bottom-third stratified sampling hands the user
    pairs of near-duplicate photos. With MMR, the cached pool
    spans more of the liked neighbourhood, so subsequent
    pairs feel different even though they all come from one
    recommend() call.
  - The session never auto-ends; the user is the only one who
    can finalize.
  - Nothing is written to Qdrant and nothing is persisted to disk
    — the session is a module-level dict, lost on server restart.

Public surface:

  start_session(qdrant) -> (session_id, DiscoveryPair)
    Create a session, draw the first random pair.

  submit_pick(qdrant, session_id, picked_id) -> DiscoveryPair | None
    Record the pick, advance the round, return the next pair.
    Returns None if the session doesn't exist.

  get_session(qdrant, session_id) -> DiscoverySession | None
    Inspect a session (used by the gallery endpoint).

  list_liked(qdrant, session_id) -> list[DiscoveryImage] | None
    Return the picked images for the gallery. None if session is
    gone.

Why a module-level dict and not a file/sqlite? The feature is
deliberately ephemeral. The cost of a 100-line module is nothing
compared to the simplicity of "kill the process, lose all
sessions, no cleanup needed." If we later want persistence, swap
the dict for a sqlite backend without changing the public API.
"""

from __future__ import annotations

import logging
import random
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from search.models import DiscoveryImage, DiscoveryPair

if TYPE_CHECKING:
    from search.index_db import IndexDB
    from search.qdrant_client import QdrantSearch

logger = logging.getLogger(__name__)


# Rounds 1-SEED_ROUNDS are random; from round SEED_ROUNDS+1
# onward, pair source is recommend-based. The seed phase
# accumulates SEED_ROUNDS positives + SEED_ROUNDS implicit
# negatives so the first recommend call has real signal.
SEED_ROUNDS: int = 10

# Sample this many from the top of recommend and pick 2 unseen. The
# burst sampler then picks 1 from the top third and 1 from the
# bottom third of the pool, so each pair has one tight match and
# one looser match (rather than 2 from the same tight top). The
# previous 20 was too narrow on a real NAS — the top 20 of
# thousands of images is essentially the same cluster. Now this
# value is the INPUT to MMR; the actual burst pool is the much
# smaller MMR-filtered subset. Bumped to 200 so MMR has real
# headroom to diversify — at 50, every candidate is so close in
# the embedding that the relevance term barely distinguishes
# them.
RECOMMEND_OVERFETCH: int = 200

# Maximal Marginal Relevance trade-off: the burst pool picks
# candidates that maximise
#     relevance_to_query - LAMBDA * max_similarity_to_already_picked
# LAMBDA=0  -> pure relevance (the original clustered behavior).
# LAMBDA=1  -> pure diversity (essentially random).
# LAMBDA=0.5 balances the two. The score the recommend call returns
# is already cosine similarity to the liked-mean vector, so we use
# it directly as the relevance term rather than re-computing it.
RECOMMEND_DIVERSIFY_LAMBDA: float = 0.5

# How many ids to cache as the burst pool after MMR re-ranking.
# Sized to feed one full burst without re-recommending mid-burst:
# 2 ids per round x RABBITHOLE_BURST_SIZE rounds = 10 ids.
MMR_POOL_SIZE: int = 10

# How many rabbithole rounds share the same recommend() query.
# A burst starts with a fresh recommend (top-OVER_FETCH unseen is
# cached as the burst pool); the next RABBITHOLE_BURST_SIZE - 1
# rounds draw 2 unseen from that same pool, so the user explores
# more of one cluster before the recommend is refined. The first
# round of the next burst then runs a fresh recommend with all
# accumulated likes/dislikes. The previous "1 round = 1 fresh
# recommend" behavior over-narrowed the cluster on every pick.
# Timeline: seed (10 random) -> burst (5) -> burst (5) -> ...
RABBITHOLE_BURST_SIZE: int = 5

# A session is dropped after this many seconds of inactivity. Pure
# garbage collection — not a "you should finalize" hint. A user who
# is actively swiping will never hit this; a user who walks away
# from the tab loses their session, which is the intended behavior
# of the "ephemeral" contract.
SESSION_TTL_SECONDS: int = 30 * 60


@dataclass
class DiscoverySession:
    id: str
    round: int = 0
    liked: list[str] = field(default_factory=list)        # in order of picking
    disliked: list[str] = field(default_factory=list)     # the OTHER image in each pair
    seen: set[str] = field(default_factory=set)           # dedupe across all rounds
    # Pre-fetched random pool for the seed phase. Drained round by
    # round; once empty, we just fetch another random window. This
    # keeps the first 5 rounds from hammering Qdrant with random
    # scroll calls.
    random_pool: list[str] = field(default_factory=list)
    # The pair currently shown to the user. submit_pick reads this
    # to know which image was the "other" one (the implicit
    # negative). Set by _next_pair, cleared by submit_pick.
    current_pair: tuple[str | None, str | None] | None = None
    last_active: float = field(default_factory=time.time)
    # Cached top-of-recommend pool for the current rabbithole
    # burst. Filled when a fresh recommend() runs at the start of
    # a burst; drained as pairs are sampled from it. Reset to []
    # when a new burst begins (or when the recommend fallback
    # to random fires).
    burst_pool: list[str] = field(default_factory=list)
    # How many rabbithole rounds have been shown in the current
    # burst. 0 means "the next call should run a fresh recommend."
    # Reset to 0 whenever a burst completes or the recommend
    # falls back to random.
    burst_rounds_shown: int = 0
    # Number of rabbithole bursts that have actually started
    # (i.e. a fresh recommend that produced >= 2 unseen
    # candidates). 0 means we haven't entered the rabbithole
    # yet; 1 means the first burst is in progress; 2+ means
    # subsequent bursts.
    bursts_started: int = 0
    # Size of the CURRENT burst (rounds per recommend). 0 when
    # no burst is active. Set when a fresh recommend succeeds
    # and the new burst starts. Every burst uses
    # RABBITHOLE_BURST_SIZE — there is no first-burst special
    # case (timeline is uniform: seed then burst then burst ...).
    current_burst_size: int = 0

    def touch(self) -> None:
        self.last_active = time.time()


# Module-level store. Thread-safe via a single lock. The lock is
# held only across the dict lookup + the (small) state mutation;
# Qdrant calls happen outside the lock.
_sessions: dict[str, DiscoverySession] = {}
_lock = threading.Lock()


def _gc_expired(now: float) -> None:
    """Drop sessions idle for longer than SESSION_TTL_SECONDS. Caller holds the lock."""
    expired = [
        sid for sid, s in _sessions.items()
        if now - s.last_active > SESSION_TTL_SECONDS
    ]
    for sid in expired:
        del _sessions[sid]
    if expired:
        logger.info("discover: gc'd %d expired sessions", len(expired))


# ---------------------- Public API ----------------------


def start_session(
    qdrant: "QdrantSearch",
    index_db: "IndexDB | None" = None,
) -> tuple[str, DiscoveryPair]:
    """
    Create a new discovery session and return the first pair.

    The first pair is always random — no signal exists yet, so a
    recommend() call would be meaningless. The user is implicitly
    starting fresh; the seed is just 10 random images presented 2
    at a time.
    """
    session = DiscoverySession(id=str(uuid.uuid4()))
    with _lock:
        _gc_expired(time.time())
        _sessions[session.id] = session
    pair = _next_pair(qdrant, session, index_db)
    return session.id, pair


def submit_pick(
    qdrant: "QdrantSearch",
    session_id: str,
    picked_id: str,
    index_db: "IndexDB | None" = None,
) -> DiscoveryPair | None:
    """
    Record the user's pick for this round and return the next pair.

    The OTHER image in the pair (the one the user didn't pick) is
    recorded as an implicit negative — this is the same comparison
    the user made when they chose one over the other. After 5
    rounds the negative set has 5 entries and the positive set has
    5, giving the first recommend() call real signal to work with.

    Returns None if the session doesn't exist (expired, server
    restart, fake id). The frontend treats None as "session ended,
    start over."
    """
    with _lock:
        session = _sessions.get(session_id)
        if session is None:
            return None
        if session.current_pair is None:
            # Defensive: client sent a pick before the pair was set.
            # Treat it as a no-op.
            return _next_pair(qdrant, session, index_db)
        left_id, right_id = session.current_pair
        if picked_id not in (left_id, right_id):
            # Picked id isn't from the current pair. Could be a
            # stale click after rapid succession. Ignore.
            session.touch()
            return _next_pair(qdrant, session, index_db)
        other_id = right_id if picked_id == left_id else left_id
        session.liked.append(picked_id)
        session.disliked.append(other_id)
        session.round += 1
        session.touch()
        session.current_pair = None
    return _next_pair(qdrant, session, index_db)


def get_session(session_id: str) -> DiscoverySession | None:
    """Inspect a session (used for the gallery endpoint and tests)."""
    with _lock:
        return _sessions.get(session_id)


def list_liked(
    qdrant: "QdrantSearch",
    session_id: str,
    web_ui_url: str = "",
) -> list[DiscoveryImage] | None:
    """
    Return the user's picked images for the gallery view, in the
    order they were picked. None if the session doesn't exist.
    """
    with _lock:
        session = _sessions.get(session_id)
        if session is None:
            return None
        liked_ids = list(session.liked)
    if not liked_ids:
        return []
    hits = qdrant.retrieve_batch(liked_ids)
    # Preserve pick order. retrieve_batch may omit missing points
    # (e.g. if the qdrant collection was reindexed mid-session).
    by_id = {h.id: h for h in hits}
    images: list[DiscoveryImage] = []
    for i, pid in enumerate(liked_ids):
        h = by_id.get(pid)
        if h is None:
            # Point disappeared between picks. Skip rather than
            # render a broken tile.
            continue
        from search.image_resolver import resolve_url
        images.append(DiscoveryImage(
            id=h.id,
            path=h.path,
            url=resolve_url(h.id, web_ui_url),
            picked_round=i + 1,
        ))
    return images


def reset_for_tests() -> None:
    """Drop all sessions. Test-only."""
    with _lock:
        _sessions.clear()


# ---------------------- Pair generation ----------------------


def _next_pair(
    qdrant: "QdrantSearch",
    session: DiscoverySession,
    index_db: "IndexDB | None" = None,
) -> DiscoveryPair:
    """
    Compute the next pair for `session`, update its state, and
    return the pair. The caller is responsible for holding the
    store lock (or not — we mutate session in place, which is fine
    since each session is owned by the single thread that called
    start_session/submit_pick on it).

    Pair source:
      - rounds 1..SEED_ROUNDS  -> random (from session.random_pool,
        refilled via IndexDB.pick_unseen when empty)
      - round SEED_ROUNDS+1+   -> recommend, in bursts of
        RABBITHOLE_BURST_SIZE rounds each. The first round of a
        burst runs a fresh recommend(positive, negative), re-ranks
        the top-OVER_FETCH unseen with MMR, and caches the
        diversified MMR_POOL_SIZE ids as the burst pool.
        Subsequent rounds in the burst draw 2 unseen from the
        cached pool using stratified sampling (1 from the top
        third, 1 from the bottom third) so each pair has one
        tight match and one looser match within the cluster.
        When the burst completes, the next call runs a fresh
        recommend with all accumulated likes/dislikes. There is
        no first-burst special case — every burst is the same
        size, so the timeline is uniform (seed -> burst -> burst
        -> ...) and there's no jarring size transition.
      - if a recommend returns fewer than 2 unseen (or the pool
        gets drained) -> fall back to random and clear the burst
        state, so the next round retries a fresh recommend. The
        user never gets stuck on "no new images."
    """
    # Decide source.
    use_recommend = session.round >= SEED_ROUNDS and bool(session.liked)

    if use_recommend:
        # A fresh recommend is needed when:
        #  - we don't have a cached burst pool yet (first
        #    rabbithole round), or
        #  - we've already shown current_burst_size rounds in the
        #    current burst and need to refresh the query. Every
        #    burst uses RABBITHOLE_BURST_SIZE — no first-burst
        #    special case.
        if not session.burst_pool or session.burst_rounds_shown >= session.current_burst_size:
            new_burst_size = RABBITHOLE_BURST_SIZE
            hits = qdrant.recommend(
                positive=session.liked,
                negative=session.disliked,
                limit=RECOMMEND_OVERFETCH,
            )
            unseen_hits = [h for h in hits if h.id not in session.seen]
            if len(unseen_hits) < 2:
                # Top-N mostly already seen, or recommend returned
                # nothing. Drop down to random and clear the burst
                # state so the next round re-tries a fresh recommend.
                # We do NOT increment bursts_started — the new
                # burst hasn't actually begun yet.
                logger.info(
                    "discover: recommend returned %d unseen, falling back to random",
                    len(unseen_hits),
                )
                use_recommend = False
                session.burst_pool = []
                session.burst_rounds_shown = 0
            else:
                # Re-rank the recommend top-N with Maximal Marginal
                # Relevance so the cached burst pool spans more of
                # the liked neighbourhood instead of being N
                # near-duplicates of the same tight cluster.
                # Without this, every pair in the burst feels like
                # the same photo because all N candidates are
                # close in embedding space.
                candidate_ids = [h.id for h in unseen_hits]
                vectors = dict(
                    qdrant.retrieve_batch_with_vectors(candidate_ids)
                )
                pool_candidates = [
                    (h.id, h.score, vectors[h.id])
                    for h in unseen_hits
                    if h.id in vectors
                ]
                if len(pool_candidates) < 2:
                    # Vectors missing for almost every candidate
                    # (retrieve failure, orphans). Fall back to the
                    # plain top-by-score list so the user still
                    # sees something this burst rather than being
                    # pushed all the way to random.
                    logger.warning(
                        "discover: vectors missing for %d of %d "
                        "recommend candidates; skipping MMR",
                        len(unseen_hits) - len(pool_candidates),
                        len(unseen_hits),
                    )
                    pool = [h.id for h in unseen_hits][:MMR_POOL_SIZE]
                else:
                    pool = _mmr_select(
                        pool_candidates,
                        k=min(MMR_POOL_SIZE, len(pool_candidates)),
                        lambda_=RECOMMEND_DIVERSIFY_LAMBDA,
                    )
                # Cache the diversified pool for the new burst. The
                # size is what we just computed (FIRST for burst 1,
                # standard for burst 2+). Counter resets to 0; the
                # sample step below increments it to 1.
                session.burst_pool = pool
                session.burst_rounds_shown = 0
                session.bursts_started += 1
                session.current_burst_size = new_burst_size

    if use_recommend and session.burst_pool:
        # Draw 2 unseen from the cached pool. Filtering against
        # `seen` is defensive: the pool was unseen when cached, but
        # in a long session the user could have already seen some
        # pool entries via the random fallback path.
        available = [pid for pid in session.burst_pool if pid not in session.seen]
        if len(available) < 2:
            logger.info("discover: burst pool exhausted, falling back to random")
            use_recommend = False
            session.burst_pool = []
            session.burst_rounds_shown = 0
        else:
            # Stratified sampling: 1 from the top third (the
            # tightest matches in the cluster) and 1 from the
            # bottom third (the looser matches). This forces each
            # pair to have variety within the cluster, instead of
            # two nearly-identical images from the same top-20
            # band. With small pools, fall back to random.sample.
            n = len(available)
            third = n // 3
            if third >= 1 and 2 * third < n:
                img_a = random.choice(available[:third])
                img_b = random.choice(available[2 * third:])
            else:
                sampled = random.sample(available, k=2)
                img_a, img_b = sampled[0], sampled[1]
            session.burst_rounds_shown += 1
            chosen = [img_a, img_b]
            session.seen.add(chosen[0])
            session.seen.add(chosen[1])
            session.current_pair = (chosen[0], chosen[1])
            return _build_pair(
                qdrant,
                session.round + 1,
                chosen[0],
                chosen[1],
                source="recommend",
            )

    if not use_recommend:
        # Drain the pool, filtering against `seen` on every pop so
        # the pool can't hand us an already-shown id. Refill from the
        # SQLite index cache when available; fall back to the legacy
        # Qdrant sampler only for direct unit-test callers that have
        # not been migrated to pass IndexDB yet.
        chosen: list[str | None] = [None, None]
        for i in range(2):
            while chosen[i] is None:
                while session.random_pool:
                    candidate = session.random_pool.pop(0)
                    if candidate not in session.seen and candidate not in chosen:
                        chosen[i] = candidate
                        break
                if chosen[i] is not None:
                    break

                if index_db is not None:
                    new_pool = index_db.pick_unseen(RECOMMEND_OVERFETCH, session.seen)
                else:
                    window = qdrant.random_window(limit=RECOMMEND_OVERFETCH)
                    new_pool = [h.id for h in window if h.id not in session.seen]
                new_pool = [pid for pid in new_pool if pid not in chosen]
                random.shuffle(new_pool)
                if not new_pool:
                    break
                session.random_pool.extend(new_pool)

        if chosen[0]:
            session.seen.add(chosen[0])
        if chosen[1]:
            session.seen.add(chosen[1])
        session.current_pair = (chosen[0], chosen[1])  # type: ignore[assignment]
        return _build_pair(qdrant, session.round + 1, chosen[0], chosen[1], source="random")


def _mmr_select(
    candidates: list[tuple[str, float, list[float]]],
    k: int,
    lambda_: float,
) -> list[str]:
    """Greedy Maximal Marginal Relevance.

    WHAT this is: a re-ranking that picks k items maximising
        relevance_to_query - LAMBDA * max_similarity_to_already_picked
    The first term keeps the burst pool on-topic (close to what the
    user has liked); the second term pushes each new pick away from
    ones we've already chosen, so the cached burst pool spans more
    of the liked neighbourhood instead of being 20 near-duplicates.

    `candidates` is a list of (id, relevance_score, vector) sorted
    by relevance desc. `relevance_score` is already cosine
    similarity to the liked-mean (the recommend() call returns it).
    Vectors are assumed unit-length (SigLIP2 embeddings in Qdrant
    are stored normalised, so dot product == cosine similarity).
    Falls back to the top-k by score if k >= len(candidates).
    """
    if k >= len(candidates):
        return [c[0] for c in candidates]

    # Lazy numpy import: keeps discover.py from forcing a numpy
    # import on every test that touches the module, even when no
    # burst ever fires.
    import numpy as np

    ids = [c[0] for c in candidates]
    scores = np.asarray([c[1] for c in candidates], dtype=np.float32)
    vecs = np.asarray([c[2] for c in candidates], dtype=np.float32)

    selected: list[int] = []
    # Pre-compute the full pairwise sim matrix once — k is small
    # (≤ MMR_POOL_SIZE) but candidates can be up to
    # RECOMMEND_OVERFETCH (200). One matmul vs O(k*n) per pick.
    sim = vecs @ vecs.T  # (n, n) cosine similarities

    while len(selected) < k:
        if not selected:
            # First pick: highest relevance.
            best = int(np.argmax(scores))
        else:
            # For each remaining candidate, MMR value = its
            # relevance - LAMBDA * (max similarity to any already-
            # selected candidate). The -inf mask drops already-
            # picked ones.
            sel = np.asarray(selected, dtype=np.int32)
            max_sim_to_sel = sim[:, sel].max(axis=1)
            mmr_value = scores - lambda_ * max_sim_to_sel
            mmr_value[selected] = -np.inf
            best = int(np.argmax(mmr_value))
        selected.append(best)

    return [ids[i] for i in selected]


def _build_pair(
    qdrant: "QdrantSearch",
    round_number: int,
    left_id: str | None,
    right_id: str | None,
    source: str,
) -> DiscoveryPair:
    """
    Resolve two point ids into DiscoveryImage objects, return them
    as a DiscoveryPair. If an id is None (collection exhausted),
    the corresponding DiscoveryImage is None.
    """
    ids = [i for i in (left_id, right_id) if i is not None]
    hits = qdrant.retrieve_batch(ids) if ids else []
    by_id = {h.id: h for h in hits}

    def to_image(pid: str | None) -> DiscoveryImage | None:
        if pid is None:
            return None
        h = by_id.get(pid)
        if h is None:
            return None
        return DiscoveryImage(
            id=h.id,
            path=h.path,
            url="",  # the route layer fills this with resolve_url(..., web_ui_url)
        )

    return DiscoveryPair(
        round=round_number,
        left=to_image(left_id),
        right=to_image(right_id),
        source=source,
    )
