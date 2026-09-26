from conftest import load_fixture

from diskatlas.probe.linux import parse_lsblk


def test_parse_lsblk_disks_and_volumes():
    disks = {d.device: d for d in parse_lsblk(load_fixture("lsblk.json"))}

    assert set(disks) == {"/dev/sda", "/dev/sdb", "/dev/sdc"}, "loop-Geräte werden ignoriert"

    system = disks["/dev/sda"]
    assert system.key == "sn:TESTSSD0001"
    assert system.is_system
    assert system.rotational is False
    assert [v.mountpoint for v in system.volumes] == ["/boot/efi", "/"]
    assert all(v.is_system for v in system.volumes)

    data = disks["/dev/sdb"]
    assert data.removable, "hotplug zählt als wechselbar"
    (vol,) = data.volumes
    assert vol.key == "part:e3b0c442-98fc-1c14-9afb-f4c8996fb924"
    assert vol.label == "AVneu"
    assert vol.fs_type == "ntfs"
    assert vol.free_bytes == 6001561120768
    assert not vol.is_system


def test_parse_lsblk_luks_and_fallback_key():
    disk = next(d for d in parse_lsblk(load_fixture("lsblk.json")) if d.device == "/dev/sdc")
    assert disk.key.startswith("fp:"), "ohne Seriennummer/WWN wird ein Fingerabdruck verwendet"
    assert disk.rotational is True, "ältere lsblk-Versionen liefern '1'"
    (vol,) = disk.volumes
    assert vol.fs_type == "ext4", "LUKS-Container selbst ist kein Volume, der Inhalt schon"
    assert vol.mountpoint == "/media/user/Backup"
    assert vol.key == "fs:c0ffee00-0000-4000-8000-000000000001"


def test_signature_changes_on_mount():
    disk = parse_lsblk(load_fixture("lsblk.json"))[1]
    before = disk.signature()
    disk.volumes[0].mountpoint = None
    assert disk.signature() != before
