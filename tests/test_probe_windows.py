from conftest import load_fixture

from diskatlas.probe.linux import parse_lsblk
from diskatlas.probe.windows import parse_windows_disks


def test_parse_windows_disks():
    disks = {d.device: d for d in parse_windows_disks(load_fixture("windows_disks.json"), "C:")}

    nvme = disks[r"\\.\PhysicalDrive0"]
    assert nvme.key == "sn:0025_3886_81B4_A1C2"
    assert nvme.smart_device == "/dev/pd0"
    assert nvme.transport == "nvme"
    assert nvme.rotational is False
    assert nvme.is_system
    assert len(nvme.volumes) == 2, "MSR-Partition wird übersprungen"
    windows = next(v for v in nvme.volumes if v.label == "Windows")
    assert windows.mountpoint == "C:\\"
    assert windows.is_system
    assert windows.used_bytes == 599000000000

    usb = disks[r"\\.\PhysicalDrive3"]
    assert usb.removable
    assert usb.key.startswith("fp:")
    assert usb.volumes[0].key == "winvol:\\\\?\\Volume{dddd}\\"
    assert usb.volumes[0].mountpoint is None


def test_same_disk_matches_across_platforms():
    """Dieselbe GPT-Platte muss unter Linux und Windows gleiche Schlüssel liefern."""
    windows_disks = parse_windows_disks(load_fixture("windows_disks.json"))
    win = next(d for d in windows_disks if d.model.startswith("ST8"))
    lin = next(d for d in parse_lsblk(load_fixture("lsblk.json")) if d.device == "/dev/sdb")
    assert win.key == lin.key
    assert [v.key for v in win.volumes] == [v.key for v in lin.volumes]
