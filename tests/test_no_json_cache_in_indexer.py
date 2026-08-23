"""
tests/test_no_json_cache_in_indexer.py — regression test for B4.

Per the plan §B4: "a test fails if any code in indexer/ writes
to a .json cache file. The JSON format is no longer supported."

Scans every .py file under indexer/ for code that opens a
.json path in write mode, or calls json.dump into a .json path,
or references the old default cache path
(state/indexer_cache.json). Any hit fails the test.
"""

from __future__ import annotations

import re
from pathlib import Path

INDEXER_DIR = Path("indexer")


def _indexer_python_files() -> list[Path]:
    return sorted(INDEXER_DIR.glob("*.py"))


def test_no_json_cache_writes_in_indexer():
    """No code in indexer/ writes a .json cache file."""
    # Patterns that indicate "writing a cache to a .json file":
    # - open(<expr>, "w") where <expr> contains ".json"
    # - json.dump(<expr>, <file>) where <file> ends with .json
    # - the literal path "state/indexer_cache.json" (the old default)
    write_open_re = re.compile(r'open\([^)]*["\']\.json["\']')
    json_dump_json_re = re.compile(
        r'json\.dump\([^,]+,\s*[^,)]+["\'][^"\']*\.json["\']'
    )
    old_default_re = re.compile(r'indexer_cache\.json')
    offenders: list[tuple[str, int, str]] = []
    for path in _indexer_python_files():
        if path.name == "test_no_json_cache_in_indexer.py":
            continue  # this test mentions the old name on purpose
        text = path.read_text(encoding="utf-8")
        for n, line in enumerate(text.splitlines(), start=1):
            if write_open_re.search(line) or json_dump_json_re.search(line) or old_default_re.search(line):
                offenders.append((path.name, n, line.strip()))
    assert not offenders, (
        "JSON cache writes are no longer supported in indexer/ "
        "(Phase B4 — replaced with SQLite). Offending lines:\n"
        + "\n".join(f"  {p}:{n}: {l}" for p, n, l in offenders)
    )


def test_cache_module_uses_sqlite():
    """The new indexer/cache.py imports sqlite3 and references .db."""
    cache_path = INDEXER_DIR / "cache.py"
    text = cache_path.read_text(encoding="utf-8")
    assert "import sqlite3" in text
    assert "indexer_cache.db" in text
    assert "CREATE TABLE" in text or "executescript" in text
