import pytest

from diskatlas.services import fslabel
from diskatlas.services.fslabel import LabelError, normalize_label


def test_normalize_by_filesystem():
    assert normalize_label("ntfs", "  Archiv 2024 ") == "Archiv 2024"
    assert normalize_label("vfat", "daten") == "DATEN"
    with pytest.raises(LabelError, match="zu lang"):
        normalize_label("ext4", "x" * 17)
    with pytest.raises(LabelError, match="zu lang"):
        normalize_label("ext4", "ä" * 9)  # Umlaute zählen als 2 Bytes
    with pytest.raises(LabelError, match="nicht unterstützt"):
        normalize_label("zfs_member", "x")
    with pytest.raises(LabelError, match="Steuerzeichen"):
        normalize_label("ntfs", "a\nb")
    with pytest.raises(LabelError, match="Sonderzeichen"):
        normalize_label("vfat", "A/B")


def test_linux_call_and_error_mapping(monkeypatch):
    calls = []

    class Done:
        def __init__(self, rc, err=""):
            self.returncode, self.stderr, self.stdout = rc, err, ""

    def fake_run(cmd):
        calls.append(cmd)
        return Done(0)

    monkeypatch.setattr(fslabel, "_run", fake_run)
    monkeypatch.setattr(fslabel.sys, "platform", "linux")
    assert fslabel.set_label("/dev/sdb1", "ntfs", None, "Ed's Film") == "Ed's Film"
    cmd = calls[0]
    assert "/org/freedesktop/UDisks2/block_devices/sdb1" in cmd
    assert "'Ed\\'s Film'" in cmd

    monkeypatch.setattr(fslabel, "_run", lambda cmd: Done(1, "Error: target is mounted"))
    with pytest.raises(LabelError, match="eingehängt"):
        fslabel.set_label("/dev/sdb1", "ntfs", None, "X")
    with pytest.raises(LabelError, match="Gerätename"):
        fslabel.set_label("/dev/../etc", "ntfs", None, "X")


class _Done:
    def __init__(self, rc=0, err=""):
        self.returncode, self.stderr, self.stdout = rc, err, ""


def test_rename_mounted_ntfs_unmounts_renames_remounts(monkeypatch, tmp_path):
    from diskatlas.services import mounting

    monkeypatch.setattr(mounting, "_holds_dir", lambda: tmp_path / "holds")
    steps = []
    monkeypatch.setattr(fslabel.sys, "platform", "linux")
    monkeypatch.setattr(mounting, "unmount", lambda d: steps.append(("unmount", d)))
    monkeypatch.setattr(mounting, "mount", lambda d: steps.append(("mount", d)))

    def fake_run(cmd):
        steps.append(("setlabel", mounting.is_held("/dev/sdb1")))  # Sperre aktiv?
        return _Done()

    monkeypatch.setattr(fslabel, "_run", fake_run)
    assert fslabel.set_label("/dev/sdb1", "ntfs", "/media/x/AV", "Neu") == "Neu"
    assert steps == [("unmount", "/dev/sdb1"), ("setlabel", True), ("mount", "/dev/sdb1")]
    assert not mounting.is_held("/dev/sdb1"), "Sperre wird wieder aufgehoben"


def test_rename_failure_still_remounts_and_ext4_tries_directly(monkeypatch, tmp_path):
    from diskatlas.services import mounting

    monkeypatch.setattr(mounting, "_holds_dir", lambda: tmp_path / "holds")
    monkeypatch.setattr(fslabel.sys, "platform", "linux")
    steps = []
    monkeypatch.setattr(mounting, "unmount", lambda d: steps.append("unmount"))
    monkeypatch.setattr(mounting, "mount", lambda d: steps.append("mount"))

    monkeypatch.setattr(fslabel, "_run", lambda cmd: _Done(1, "Error: target is busy"))
    with pytest.raises(LabelError):
        fslabel.set_label("/dev/sdb1", "ntfs", "/media/x/AV", "Neu")
    assert steps == ["unmount", "mount"], "nach Fehlschlag wird wieder eingehängt"

    # ext4: erst direkt; nur bei „eingehängt“-Fehler wird ausgehängt
    steps.clear()
    calls = iter([_Done(1, "is mounted"), _Done(0)])
    monkeypatch.setattr(fslabel, "_run", lambda cmd: next(calls))
    fslabel.set_label("/dev/sdb2", "ext4", "/mnt/x", "Neu")
    assert steps == ["unmount", "mount"]


def test_unmount_error_is_reported_and_nothing_is_renamed(monkeypatch, tmp_path):
    from diskatlas.services import mounting

    monkeypatch.setattr(mounting, "_holds_dir", lambda: tmp_path / "holds")
    monkeypatch.setattr(fslabel.sys, "platform", "linux")

    def busy(device):
        raise mounting.MountError("Aushängen nicht möglich: Das Volume wird noch verwendet")

    monkeypatch.setattr(mounting, "unmount", busy)
    monkeypatch.setattr(fslabel, "_run", lambda cmd: pytest.fail("darf nicht umbenennen"))
    with pytest.raises(LabelError, match="verwendet"):
        fslabel.set_label("/dev/sdb1", "ntfs", "/media/x/AV", "Neu")
