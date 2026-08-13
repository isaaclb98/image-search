"""
indexer/scan.py

Folder walking for the indexer.

Critical invariant: we return a list (snapshot), not a generator.
On Windows/NTFS and SMB mounts, `os.scandir` re-yields renamed files
mid-iteration — see the lesson in
~/.openclaw/workspace-task/memory/self-improving/2026-06-04-find-images-snapshot.md

The indexer renames/mutates (writes sidecars, etc.) — so we must
materialize the file set BEFORE any iteration that could trigger
mid-walk renames. Snapshot, then iterate.

Speed: uses `os.scandir` directly and filters on DirEntry attributes
(is_file, name, suffix) — no `Path.is_file()` per file, which would be
a redundant `os.stat` syscall. On a Windows + SMB mount, the naive
`Path.rglob` + `Path.is_file()` approach is ~2x slower because of the
extra stat call per file.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Image extensions we know how to embed.
IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".jfif", ".png", ".webp", ".heic", ".heif"}
)

# Names that should never be embedded.
SKIP_NAMES: frozenset[str] = frozenset(
    {"thumbs.db", ".ds_store"}
)


def is_image_suffix(suffix: str) -> bool:
    """True if `suffix` (e.g. ".jpg") is a known image extension."""
    return suffix.lower() in IMAGE_EXTENSIONS


def should_skip_name(name: str) -> bool:
    """True if `name` is junk (hidden, OS metadata, etc.)."""
    if name.startswith("."):  # hidden files, ._foo (macOS resource forks)
        return True
    return name.lower() in SKIP_NAMES


def is_image(path: Path) -> bool:
    """True if `path` has a known image extension."""
    return is_image_suffix(path.suffix)


def should_skip(path: Path) -> bool:
    """True if `path` is a junk file (hidden, OS metadata, etc.)."""
    return should_skip_name(path.name)


def snapshot(source: Path, progress_every: int = 10_000) -> list[Path]:
    """
    Return a stable, sorted list of image paths under `source`.

    Materialized as a list (not a generator) so that subsequent
    iteration is safe against mid-walk directory mutations.

    Recurses through subdirectories. Filters by extension and skips
    junk files.

    `progress_every` controls how often a progress line is logged
    (every N image files found). The walk is otherwise silent and can
    take many minutes on a network share, so the heartbeat keeps the
    user informed that it is still working.
    """
    if not source.exists():
        raise FileNotFoundError(f"source path does not exist: {source}")
    if not source.is_dir():
        raise NotADirectoryError(f"source is not a directory: {source}")

    logger.info("scan: walking %s ...", source)
    results: list[Path] = []
    state = {"count": 0, "next": progress_every}
    _walk_into(source, results, state, progress_every)
    results.sort()
    return results


def _walk_into(source: Path, out: list[Path], state: dict, progress_every: int) -> None:
    """
    Recursive scandir-based walk. Appends matching Path objects to `out`.

    Uses DirEntry attributes (is_file, name, suffix) which are already
    cached by the underlying syscall — no extra stat() per entry.

    `state` carries the running file count and the next threshold to
    log at; `progress_every` is the increment between thresholds.
    """
    try:
        with os.scandir(source) as it:
            for entry in it:
                try:
                    if entry.is_file():
                        name = entry.name
                        if should_skip_name(name):
                            continue
                        if is_image_suffix(Path(name).suffix):
                            out.append(Path(entry.path))
                            state["count"] += 1
                            if state["count"] >= state["next"]:
                                logger.info(
                                    "scan: %d image files so far (walking %s)",
                                    state["count"], source,
                                )
                                state["next"] += progress_every
                    elif entry.is_dir(follow_symlinks=False):
                        # Recurse into subdirectories. Skip symlinks to
                        # avoid loops on weird NAS layouts.
                        _walk_into(Path(entry.path), out, state, progress_every)
                except OSError:
                    # Permission errors, broken links, etc. — skip silently.
                    continue
    except OSError:
        # Top-level source unreadable. surface via the FileNotFoundError
        # checks in snapshot() before we ever get here; if it does fail
        # mid-walk, just stop — the partial result is still useful.
        pass
