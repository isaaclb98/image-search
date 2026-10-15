"""
search/random_session.py — per-process store of /api/random shuffled decks.

Extracted from `search/routers/random.py` in round-93 PR 2
(`refactor/plan-002-module-layout`). The split keeps the router file
HTTP-only and puts the stateful session bookkeeping in a module
named for what it stores.

The store is deliberately process-local: a /random shuffle is cheap
to rebuild (an O(N) Qdrant scroll), so the operational cost of
sticking it in a shared store would outweigh the benefit. A
load balancer sticky-session becomes necessary if you ever share
this across processes — and that's the opposite of what /random
wants, because the user expects a fresh shuffle per call when
they ask for one.

Two non-obvious shapes this module owns:

1. **Splitting lookup from creation.** `get(session_id)` returns
   the live session if any (and clears expired ones); `put_new(ids)`
   materializes a new one. Splitting these lets the caller run
   the Qdrant materialize off the event loop
   (`asyncio.to_thread(...)`) without weaving sync/async into the
   store.

2. **TTL by `time.monotonic`.** The session expires after
   `RANDOM_SESSION_TTL_S` of wall-clock-less time. `monotonic` is
   the right clock for this — wall-clock jumps (NTP, daylight
   savings) can't accidentally drop a session or extend its life.
"""

from __future__ import annotations

import secrets
import time

# Default session lifetime. After this many seconds the shuffled
# deck is discarded and the next call gets a fresh shuffle.
RANDOM_SESSION_TTL_S = 30 * 60


class RandomSession:
    """One shuffled walk through the library.

    Named without a leading underscore because it's part of the
    module's public surface — `search/routers/random.py` and
    tests construct and inspect these directly.
    """

    __slots__ = ("created_at", "ids", "ttl_s")

    def __init__(self, ids: list[str], ttl_s: float):
        self.ids = ids
        self.created_at = time.monotonic()
        self.ttl_s = ttl_s

    def is_alive(self) -> bool:
        return (time.monotonic() - self.created_at) < self.ttl_s


class RandomSessionStore:
    """Process-local map of session_id → shuffled deck.

    Deliberately NOT shared across processes — see module
    docstring for why.
    """

    def __init__(self, ttl_s: float = RANDOM_SESSION_TTL_S):
        self._sessions: dict[str, RandomSession] = {}
        self._ttl_s = ttl_s

    def get(
        self,
        session_id: str | None,
    ) -> tuple[str, RandomSession] | None:
        """Look up a live session by id. Returns None if the id is
        missing, or if the session has expired (in which case the
        entry is removed).
        """
        if not session_id:
            return None
        sess = self._sessions.get(session_id)
        if sess is None:
            return None
        if not sess.is_alive():
            del self._sessions[session_id]
            return None
        return session_id, sess

    def put_new(self, ids: list[str]) -> tuple[str, RandomSession]:
        """Materialize a fresh session with a new id and the given
        shuffled deck. The caller is responsible for having run
        the materialize (possibly in a thread).
        """
        new_id = secrets.token_urlsafe(16)
        sess = RandomSession(ids, self._ttl_s)
        self._sessions[new_id] = sess
        return new_id, sess

    def clear(self) -> None:
        """Drop all sessions. Used by tests."""
        self._sessions.clear()

    def __len__(self) -> int:
        return len(self._sessions)


__all__ = ["RANDOM_SESSION_TTL_S", "RandomSession", "RandomSessionStore"]
