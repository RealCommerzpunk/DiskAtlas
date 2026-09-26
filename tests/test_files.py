import os

import pytest

from diskatlas.probe.files import iter_files


def _touch(path, size=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def test_iter_files_relative_paths_and_excludes(tmp_path):
    _touch(tmp_path / "film.mkv", 10)
    _touch(tmp_path / "Serien" / "S01" / "E01.mp4", 5)
    _touch(tmp_path / "$RECYCLE.BIN" / "trash.dat", 1)
    _touch(tmp_path / ".Trash-1000" / "files" / "old.txt", 1)

    records = sorted(iter_files(str(tmp_path), ["$recycle.bin", ".trash-*"]))
    assert [r.path for r in records] == ["Serien/S01/E01.mp4", "film.mkv"]
    assert records[1].size == 10
    assert records[1].mtime is not None


@pytest.mark.skipif(os.name == "nt", reason="Symlinks benötigen unter Windows Sonderrechte")
def test_iter_files_skips_symlinks(tmp_path):
    _touch(tmp_path / "real" / "a.txt")
    os.symlink(tmp_path / "real", tmp_path / "link")
    os.symlink(tmp_path / "real" / "a.txt", tmp_path / "b.txt")
    assert [r.path for r in iter_files(str(tmp_path))] == ["real/a.txt"]


def test_iter_files_stop(tmp_path):
    for i in range(3):
        _touch(tmp_path / f"d{i}" / "f.txt")
    assert list(iter_files(str(tmp_path), should_stop=lambda: True)) == []


def test_iter_files_missing_root_raises(tmp_path):
    # Ein fehlender Einhängepunkt ist ein harter Fehler (Scan wird als fehlgeschlagen markiert).
    with pytest.raises(FileNotFoundError):
        list(iter_files(str(tmp_path / "missing")))


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="chmod wirkt nicht")
def test_iter_files_reports_unreadable_dirs(tmp_path):
    _touch(tmp_path / "ok.txt")
    locked = tmp_path / "locked"
    _touch(locked / "secret.txt")
    locked.chmod(0)
    errors = []
    try:
        records = list(iter_files(str(tmp_path), on_error=lambda p, e: errors.append(p)))
    finally:
        locked.chmod(0o755)
    assert [r.path for r in records] == ["ok.txt"]
    assert errors == [str(locked)]
