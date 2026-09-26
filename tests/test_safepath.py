"""Pfadsicherheit der Agenten-Übertragungen (bösartige Eingaben)."""

import os
import sys

import pytest

from diskatlas.agent import safepath

BAD = ["../x", "a/../../x", "a/..", "..", "/etc/passwd", "\\windows", "C:\\x", "c:x", "a\0b",
       "datei:stream", "a/b:c", "CON", "nul.txt", "a/COM1.log", "name.", "name ", "a//..//b",
       "x" * 300, "", ".", "tab\tname", "pipe|x", "q?x", "star*x"]


@pytest.mark.parametrize("path", BAD)
def test_clean_parts_rejects_dangerous_paths(path):
    with pytest.raises(safepath.UnsafePath):
        safepath.clean_parts(path)


def test_clean_parts_accepts_normal_paths():
    assert safepath.clean_parts("a/b/c.txt") == ["a", "b", "c.txt"]
    assert safepath.clean_parts("a\\b//c") == ["a", "b", "c"]
    assert safepath.clean_parts("Ünï cödé (1)/日本語.mkv") == ["Ünï cödé (1)", "日本語.mkv"]
    assert safepath.clean_parts("", allow_empty=True) == []


def test_source_cannot_escape_through_symlink(tmp_path):
    root, outside = tmp_path / "vol", tmp_path / "secret"
    root.mkdir(), outside.mkdir()
    (outside / "geheim.txt").write_text("nein")
    (root / "ok.txt").write_text("ja")
    os.symlink(outside, root / "link")
    os.symlink(outside / "geheim.txt", root / "file-link")
    handle, _ = safepath.open_source(str(root), "ok.txt")
    handle.close()
    for rel in ("link/geheim.txt", "file-link"):
        with pytest.raises(safepath.UnsafePath):
            safepath.open_source(str(root), rel)


def test_source_must_be_a_regular_file(tmp_path):
    (tmp_path / "d").mkdir()
    with pytest.raises(safepath.UnsafePath):
        safepath.open_source(str(tmp_path), "d")
    if hasattr(os, "mkfifo"):
        os.mkfifo(tmp_path / "pipe")
        with pytest.raises(safepath.UnsafePath):
            safepath.open_source(str(tmp_path), "pipe")
    with pytest.raises(safepath.UnsafePath):
        safepath.open_source(str(tmp_path), "fehlt.txt")


def test_target_dir_creates_inside_and_refuses_symlinks(tmp_path):
    root = tmp_path / "vol"
    root.mkdir()
    made = safepath.target_dir(str(root), "a/b")
    assert os.path.isdir(made) and made.startswith(str(root))
    assert safepath.target_dir(str(root), "") == str(root)
    outside = tmp_path / "out"
    outside.mkdir()
    os.symlink(outside, root / "escape")
    with pytest.raises(safepath.UnsafePath):
        safepath.target_dir(str(root), "escape/x")
    assert not (outside / "x").exists()
    for bad in ("../out", "a/../../out"):
        with pytest.raises(safepath.UnsafePath):
            safepath.target_dir(str(root), bad)


def test_finalize_never_overwrites(tmp_path):
    (tmp_path / "film.mkv").write_text("alt")
    for n, expected in enumerate(["film (1).mkv", "film (2).mkv"]):
        part = tmp_path / f"p{n}.part"
        part.write_text("neu")
        assert safepath.finalize(str(part), str(tmp_path), "film.mkv") == expected
        assert not part.exists()
    assert (tmp_path / "film.mkv").read_text() == "alt"
    with pytest.raises(safepath.UnsafePath):
        safepath.finalize(str(tmp_path / "x"), str(tmp_path), "../evil")


@pytest.mark.skipif(sys.platform == "win32", reason="Hardlink-Verhalten")
def test_finalize_falls_back_when_links_are_unsupported(tmp_path, monkeypatch):
    part = tmp_path / "p.part"
    part.write_text("x")
    monkeypatch.setattr(os, "link", lambda *a: (_ for _ in ()).throw(OSError("kein Link")))
    assert safepath.finalize(str(part), str(tmp_path), "a.txt") == "a.txt"
    assert (tmp_path / "a.txt").read_text() == "x"
