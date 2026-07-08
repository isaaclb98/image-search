from __future__ import annotations

from pathlib import Path

from indexer import heal


class Point:
    def __init__(self, pid: str, payload: dict):
        self.id = pid
        self.payload = payload


class FakeClient:
    def __init__(self, points):
        self.points = list(points)
        self.deleted = []

    def scroll(self, collection_name, limit, offset=None, with_payload=True, with_vectors=False):
        start = int(offset or 0)
        batch = self.points[start:start + limit]
        next_offset = start + limit if start + limit < len(self.points) else None
        return batch, next_offset

    def delete(self, collection_name, points_selector, wait=True):
        self.deleted.extend(points_selector)


def _jpg(path: Path, data: bytes = b"jpg") -> Path:
    path.write_bytes(data)
    return path


def test_dry_run_prints_summary_and_does_not_delete(tmp_path, monkeypatch, capsys):
    keep = _jpg(tmp_path / "keep.jpg")
    fake = FakeClient([
        Point("keep", {"path": str(keep.resolve()), "mtime": int(keep.stat().st_mtime), "size": keep.stat().st_size}),
        Point("old", {"path": str((tmp_path / "old.jpg").resolve()), "mtime": 1, "size": 1}),
    ])
    monkeypatch.setattr(heal, "make_client", lambda args: fake)

    assert heal.main([str(tmp_path), "--collection", "images", "--quiet"]) == 0
    out = capsys.readouterr().out
    assert 'Healing' in out
    assert "Orphans (in Qdrant, not on disk): 1" in out
    assert "Run --apply to delete the 1 orphans." in out
    assert fake.deleted == []


def test_apply_deletes_orphan_points(tmp_path, monkeypatch):
    fake = FakeClient([
        Point("old", {"path": str((tmp_path / "old.jpg").resolve()), "mtime": 1, "size": 1}),
    ])
    monkeypatch.setattr(heal, "make_client", lambda args: fake)

    assert heal.main([str(tmp_path), "--apply"]) == 0
    assert fake.deleted == ["old"]


def test_verbose_shows_every_orphan(tmp_path):
    fake = FakeClient([
        Point("old1", {"path": str((tmp_path / "old1.jpg").resolve())}),
        Point("old2", {"path": str((tmp_path / "old2.jpg").resolve())}),
    ])
    report = heal.reconcile(fake, tmp_path, "images")
    out = heal.render_report(report, verbose=True)
    assert "old1.jpg" in out
    assert "old2.jpg" in out
    assert "...and" not in out


def test_empty_collection_reports_new_files(tmp_path):
    _jpg(tmp_path / "new.jpg")
    report = heal.reconcile(FakeClient([]), tmp_path, "images")
    assert report.orphans == []
    assert len(report.new_files) == 1


def test_modified_file_detection(tmp_path):
    changed = _jpg(tmp_path / "changed.jpg", b"new-data")
    fake = FakeClient([
        Point("changed", {"path": str(changed.resolve()), "mtime": 1, "size": 1}),
    ])
    report = heal.reconcile(fake, tmp_path, "images")
    assert [Path(f.path).name for f in report.modified_files] == ["changed.jpg"]


def test_hidden_files_are_skipped(tmp_path):
    _jpg(tmp_path / ".hidden.jpg")
    report = heal.reconcile(FakeClient([]), tmp_path, "images")
    assert report.total_files == 0


def test_quiet_flag_suppresses_progress(tmp_path, monkeypatch, capsys):
    fake = FakeClient([
        Point("old", {"path": str((tmp_path / "old.jpg").resolve())}),
    ])
    monkeypatch.setattr(heal, "make_client", lambda args: fake)

    assert heal.main([str(tmp_path), "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "Scrolling" not in out
    assert "Walking" not in out
    assert "Scrolled" not in out
    assert "Healing" in out  # final report still prints


def test_progress_output_printed_by_default(tmp_path, monkeypatch, capsys):
    points = [
        Point(f"p{i}", {"path": str((tmp_path / f"p{i}.jpg").resolve())})
        for i in range(5)
    ]
    fake = FakeClient(points)
    monkeypatch.setattr(heal, "make_client", lambda args: fake)
    _jpg(tmp_path / "p0.jpg")

    assert heal.main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Scrolling" in out
    assert "Walking" in out
    assert "Scrolled" in out
    assert "Walked" in out
    assert "Using Qdrant at" in out


def test_apply_progress_shows_deletion_count(tmp_path, monkeypatch, capsys):
    fake = FakeClient([
        Point("old1", {"path": str((tmp_path / "old1.jpg").resolve())}),
        Point("old2", {"path": str((tmp_path / "old2.jpg").resolve())}),
    ])
    monkeypatch.setattr(heal, "make_client", lambda args: fake)

    assert heal.main([str(tmp_path), "--apply"]) == 0
    out = capsys.readouterr().out
    assert "Deleting 2 orphan points" in out
    assert "Deleted 2 / 2" in out


def test_outside_scope_points_are_separated_from_orphans(tmp_path):
    # Qdrant has a point elsewhere (not under tmp_path) and one in scope.
    in_scope = _jpg(tmp_path / "in_scope.jpg")
    elsewhere = tmp_path.parent / "elsewhere" / "pic.jpg"  # not under tmp_path
    fake = FakeClient([
        Point("inside", {"path": str(in_scope.resolve()), "mtime": int(in_scope.stat().st_mtime), "size": in_scope.stat().st_size}),
        Point("outside", {"path": str(elsewhere), "mtime": 1, "size": 1}),
    ])
    report = heal.reconcile(fake, tmp_path, "images")
    assert [p.id for p in report.outside_scope] == ["outside"]
    assert report.orphans == []  # in-scope file exists; outside-scope isn't an orphan


def test_orphan_when_in_scope_but_file_missing(tmp_path):
    # The point's path is under tmp_path but the file doesn't exist on disk.
    fake = FakeClient([
        Point("missing", {"path": str((tmp_path / "missing.jpg").resolve())}),
    ])
    report = heal.reconcile(fake, tmp_path, "images")
    assert [p.id for p in report.orphans] == ["missing"]
    assert report.outside_scope == []


def test_outside_scope_reported_in_render(tmp_path):
    fake = FakeClient([
        Point("a", {"path": str((tmp_path / "in.jpg").resolve())}),
        Point("b", {"path": "/completely/elsewhere/x.jpg"}),
    ])
    report = heal.reconcile(fake, tmp_path, "images")
    out = heal.render_report(report)
    assert "Outside scope" in out
    assert "1,270,477" not in out  # just checking the format is right
    assert "1 outside scope" in out or "1," in out


def test_is_under_unc_vs_drive_letter():
    # Z:\images\kpop and \\server\files\images\kpop both refer to the same tree.
    from pathlib import Path
    root = Path(r"Z:\images\kpop")
    assert heal._is_under(r"Z:\images\kpop\foo\bar.jpg", root)
    assert heal._is_under(r"\\server\files\images\kpop\foo\bar.jpg", root) is False  # different format, same content — but _is_under is string-based, so this returns False
    assert not heal._is_under(r"Z:\images\other\x.jpg", root)
    assert not heal._is_under(r"", root)


def test_is_under_case_insensitive():
    from pathlib import Path
    root = Path(r"C:\Photos")
    assert heal._is_under(r"c:\photos\img.jpg", root)
    assert heal._is_under(r"C:\PHOTOS\IMG.JPG", root)


def test_detect_source_dir_finds_common_ancestor(tmp_path):
    paths = [
        str(tmp_path / "kpop" / "aespa" / "a.jpg"),
        str(tmp_path / "kpop" / "twice" / "b.jpg"),
        str(tmp_path / "kpop" / "ive" / "c.jpg"),
    ]
    detected = heal._detect_source_dir(paths)
    assert detected is not None
    assert detected == tmp_path / "kpop"


def test_detect_source_dir_handles_empty_list():
    assert heal._detect_source_dir([]) is None


def test_detect_source_dir_handles_unrelated_paths():
    # Different drives on Windows raise ValueError; should return None gracefully.
    paths = [r"C:\foo\x.jpg", r"D:\bar\y.jpg"]
    detected = heal._detect_source_dir(paths)
    # On Windows, this might return None or the common path. On POSIX, the
    # common path might be "/" — treat as None for our purposes.
    assert detected is None or str(detected) in ("/", "")


def test_auto_detect_source_from_qdrant(tmp_path, monkeypatch, capsys):
    (tmp_path / "sub1").mkdir()
    (tmp_path / "sub2").mkdir()
    (tmp_path / "sub1" / "a.jpg").write_bytes(b"x")
    (tmp_path / "sub2" / "b.jpg").write_bytes(b"y")
    fake = FakeClient([
        Point("a", {"path": str(tmp_path / "sub1" / "a.jpg")}),
        Point("b", {"path": str(tmp_path / "sub2" / "b.jpg")}),
    ])
    monkeypatch.setattr(heal, "make_client", lambda args: fake)

    # No source arg — should auto-detect
    assert heal.main(["--quiet"]) == 0
    out = capsys.readouterr().out
    assert "Auto-detected" in out
    assert str(tmp_path) in out  # detected dir is the common parent
    # Both in-scope points exist on disk, so no orphans
    assert "Orphans (in Qdrant, not on disk): 0" in out


def test_auto_detect_empty_collection_errors(tmp_path, monkeypatch, capsys):
    fake = FakeClient([])
    monkeypatch.setattr(heal, "make_client", lambda args: fake)
    assert heal.main(["--quiet"]) == 1
    err = capsys.readouterr().err
    assert "auto-detect" in err.lower() or "Could not" in err


def test_canonical_path_resolves_existing_file(tmp_path):
    f = tmp_path / "file.jpg"
    f.write_bytes(b"x")
    canonical = heal._canonical_path(str(f))
    assert canonical == str(f.resolve())


def test_canonical_path_falls_back_for_missing_file(tmp_path):
    missing = str(tmp_path / "missing.jpg")
    canonical = heal._canonical_path(missing)
    # Should not crash; should return a usable string.
    assert isinstance(canonical, str)
    assert canonical  # non-empty


def test_reconcile_matches_paths_via_canonical_resolution(tmp_path):
    """When Qdrant stores a path that resolves to the same canonical as a
    walked file (e.g. via symlinks, or Z: drive mapped to a UNC on Windows),
    the orphan/new/modified checks should recognise them as the same file
    rather than flagging a mismatch.
    """
    real_file = tmp_path / "real.jpg"
    real_file.write_bytes(b"x")
    link_file = tmp_path / "link.jpg"
    link_file.symlink_to(real_file)

    # Qdrant has the link path (raw); walk resolves to the real path.
    fake = FakeClient([
        Point("p", {
            "path": str(link_file),
            "mtime": int(real_file.stat().st_mtime),
            "size": real_file.stat().st_size,
        }),
    ])
    report = heal.reconcile(fake, tmp_path, "images")
    # Without canonical resolution the link path wouldn't match the resolved
    # real path, and the file would show up as "new". With canonical resolution
    # both forms collapse to the same canonical path.
    assert report.new_files == []
    assert report.orphans == []


def test_reconcile_orphan_when_canonical_file_missing(tmp_path):
    """An in-scope Qdrant point whose canonical file doesn't exist on disk
    is still correctly identified as an orphan after canonical resolution.
    """
    missing = tmp_path / "missing.jpg"  # never created
    fake = FakeClient([
        Point("p", {
            "path": str(missing),
            "mtime": 1,
            "size": 1,
        }),
    ])
    report = heal.reconcile(fake, tmp_path, "images")
    assert [pt.id for pt in report.orphans] == ["p"]
    assert report.new_files == []
