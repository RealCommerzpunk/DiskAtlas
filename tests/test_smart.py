from conftest import load_fixture

from diskatlas.probe.smart import health_reasons, parse_smartctl


def test_sata_ok():
    info = parse_smartctl(load_fixture("smartctl_sata_ok.json"), 0)
    assert info.available
    assert info.health == "ok"
    assert info.temperature_c == 36
    assert info.power_on_hours == 2811
    assert info.power_cycles == 57
    assert info.reallocated_sectors == 0
    assert info.serial == "TESTHDD0002"


def test_sata_reallocated_sectors_warn():
    info = parse_smartctl(load_fixture("smartctl_sata_warning.json"), 64)
    assert info.health == "warning"
    assert info.reallocated_sectors == 12
    assert info.pending_sectors == 2


def test_failing_exit_bit():
    data = load_fixture("smartctl_sata_ok.json")
    assert parse_smartctl(data, 0x08).health == "failed"
    data["smart_status"]["passed"] = False
    assert parse_smartctl(data, 0).health == "failed"


def test_nvme():
    info = parse_smartctl(load_fixture("smartctl_nvme.json"), 0)
    assert info.health == "ok"
    assert info.percentage_used == 3
    assert info.power_on_hours == 6120
    assert info.temperature_c == 44


def test_nvme_worn_out_warns():
    data = load_fixture("smartctl_nvme.json")
    data["nvme_smart_health_information_log"]["percentage_used"] = 95
    assert parse_smartctl(data, 0).health == "warning"


def test_unavailable():
    info = parse_smartctl(load_fixture("smartctl_open_failed.json"), 2)
    assert not info.available
    assert info.health == "unknown"
    assert "Permission denied" in info.error
    assert info.raw is None


def test_health_reasons_explain_selftest_failure():
    data = load_fixture("smartctl_sata_ok.json")
    data["power_on_time"] = {"hours": 42666}
    data["ata_smart_self_test_log"] = {"standard": {"table": [{
        "type": {"string": "Extended offline"},
        "status": {"string": "Completed: read failure", "passed": False},
        "lifetime_hours": 42550, "lba": 9437257,
    }]}}
    assert parse_smartctl(data, 0x80).health == "warning"
    (reason,) = health_reasons(data, 0x80)
    assert "Extended offline" in reason and "42.550" in reason and "116" in reason
    assert "9437257" in reason


def test_health_reasons_exist_whenever_not_ok():
    for name, rc in (("smartctl_sata_warning.json", 64), ("smartctl_sata_ok.json", 0x08)):
        data = load_fixture(name)
        assert parse_smartctl(data, rc).health != "ok"
        assert health_reasons(data, rc), name
    assert health_reasons(load_fixture("smartctl_sata_ok.json"), 0) == []
