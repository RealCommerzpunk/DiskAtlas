import threading

from conftest import make_disk
from fastapi.testclient import TestClient

from diskatlas.agent.agent import Agent
from diskatlas.agent.sinks import DatabaseSink, HttpSink
from diskatlas.config import AgentConfig
from diskatlas.probe.types import SmartInfo
from diskatlas.services import queries


def _fake_smart(device):
    return SmartInfo(available=True, health="ok", passed=True, temperature_c=30)


def _tree(tmp_path):
    """Legt einen kleinen Verzeichnisbaum an (getrennt von der Test-DB in tmp_path)."""
    root = tmp_path / "mnt"
    (root / "Musik").mkdir(parents=True)
    (root / "Musik" / "lied.flac").write_bytes(b"x" * 1234)
    (root / "notiz.txt").write_text("hallo")
    return root


def test_scan_all_with_database_sink(db, tmp_path):
    disk = make_disk(mountpoint=str(_tree(tmp_path)))
    agent = Agent(AgentConfig(), DatabaseSink(db), prober=lambda: [disk],
                  smart_reader=_fake_smart, host="testhost")
    assert agent.scan_all() == 1

    with db.session() as s:
        (stored,) = queries.load_disks(s)
        assert stored.is_connected
        assert stored.last_host == "testhost"
        assert stored.temperature_c == 30
        assert stored.file_count == 2
        rows, _ = queries.search_files(s, queries.FileQuery(extension="flac"))
        assert rows[0][0].path == "Musik/lied.flac"


def test_system_volume_skipped_by_default(db, tmp_path):
    disk = make_disk(mountpoint=str(_tree(tmp_path)))
    disk.volumes[0].is_system = True
    Agent(AgentConfig(), DatabaseSink(db), prober=lambda: [disk], smart_reader=_fake_smart,
          host="h").scan_all()
    with db.session() as s:
        assert queries.load_disks(s)[0].file_count == 0


def test_http_sink_roundtrip(client: TestClient, db, tmp_path):
    """Agent -> HTTP-Ingest -> Datenbank, wie später mit dem Server auf dem Unraid."""
    sink = HttpSink("http://testserver", client=_prefixed(client))
    disk = make_disk(mountpoint=str(_tree(tmp_path)))
    Agent(AgentConfig(), sink, prober=lambda: [disk], smart_reader=_fake_smart,
          host="remote").scan_all()
    data = client.get("/api/v1/files", params={"q": "lied"}).json()
    assert data["total"] == 1
    assert data["items"][0]["disk_connected"] is True


def test_watch_detects_hotplug(db, tmp_path):
    mnt = _tree(tmp_path)
    present: list = []
    scanned = threading.Event()

    class RecordingSink(DatabaseSink):
        def finish_index(self, *args, **kwargs):
            super().finish_index(*args, **kwargs)
            scanned.set()

    agent = Agent(AgentConfig(poll_interval=0.05), RecordingSink(db),
                  prober=lambda: list(present), smart_reader=_fake_smart, host="h")
    thread = threading.Thread(target=agent.watch, daemon=True)
    thread.start()
    try:
        present.append(make_disk(mountpoint=str(mnt)))  # „Einstecken“
        assert scanned.wait(5), "neue Festplatte wurde nicht gescannt"
        present.clear()  # „Abziehen“
        for _ in range(100):
            with db.session() as s:
                if not queries.load_disks(s)[0].is_connected:
                    break
            threading.Event().wait(0.05)
        with db.session() as s:
            assert not queries.load_disks(s)[0].is_connected
    finally:
        agent.stop()
        thread.join(5)


class _prefixed:
    """Leitet HttpSink-Aufrufe (relativ zu /api/v1) an den TestClient weiter."""

    def __init__(self, client):
        self.client = client

    def post(self, path, json):
        return self.client.post("/api/v1" + path, json=json)

    def close(self):
        pass


def test_usage_watcher_triggers_after_settling():
    from diskatlas.agent.agent import UsageWatcher

    w = UsageWatcher(settle=30)
    assert not w.update("v", 100, 0), "Ausgangswert löst nichts aus"
    assert not w.update("v", 100, 100), "unverändert"
    assert not w.update("v", 150, 110), "Änderung: erst abwarten"
    assert not w.update("v", 200, 120), "Kopiervorgang läuft noch"
    assert not w.update("v", 200, 140), "noch keine 30 s Ruhe"
    assert w.update("v", 200, 151), "30 s stabil -> Neuindex"
    assert not w.update("v", 200, 200), "nur einmal"


def test_usage_watcher_ignores_small_fluctuations():
    from diskatlas.agent.agent import UsageWatcher

    w = UsageWatcher(settle=30, min_delta=1000)
    assert not w.update("v", 10_000, 0)
    for t, used in ((10, 10_100), (50, 10_300), (90, 9_800)):  # Protokolle, Caches: klein
        assert not w.update("v", used, t)
        assert not w.update("v", used, t + 40), "auch nach Ruhe kein Auslöser"
    assert not w.update("v", 20_000, 200), "echte Änderung: erst abwarten"
    assert w.update("v", 20_000, 231)
    # der Bezugswert wurde nachgezogen: erneut nur bei neuer, deutlicher Änderung
    assert not w.update("v", 20_400, 300) and not w.update("v", 20_400, 340)
    assert not w.update("v", 30_000, 400) and w.update("v", 30_000, 431)


def test_watch_auto_mounts_unmounted_volumes_once(db, tmp_path, monkeypatch):
    from diskatlas.services import mounting

    monkeypatch.setattr(mounting, "_holds_dir", lambda: tmp_path / "holds")
    disk = make_disk(mountpoint=None)
    disk.volumes[0].fs_type = "ntfs"
    disk.volumes[0].device = "/dev/sdx1"
    mounted: list[str] = []
    seen = threading.Event()

    def fake_mount(device):
        mounted.append(device)
        seen.set()

    agent = Agent(AgentConfig(poll_interval=0.05), DatabaseSink(db), prober=lambda: [disk],
                  smart_reader=_fake_smart, host="h", mounter=fake_mount)
    thread = threading.Thread(target=agent.watch, daemon=True)
    thread.start()
    try:
        assert seen.wait(5), "nicht eingehängtes Volume wurde nicht eingehängt"
        threading.Event().wait(0.5)  # mehrere Polls: nicht wiederholt versuchen
        assert mounted == ["/dev/sdx1"]
        with mounting.hold("/dev/sdx1"):
            assert not agent._mountable(disk.volumes[0]), "gesperrte Geräte werden übersprungen"
    finally:
        agent.stop()
        thread.join(5)


def test_auto_mount_can_be_disabled_and_skips_system_volumes(db):
    disk = make_disk(mountpoint=None)
    disk.volumes[0].fs_type, disk.volumes[0].device = "ntfs", "/dev/sdx1"
    off = Agent(AgentConfig(auto_mount=False), DatabaseSink(db), prober=lambda: [disk],
                smart_reader=_fake_smart, host="h", mounter=lambda d: None)
    assert not off._wants_mount(disk)
    on = Agent(AgentConfig(), DatabaseSink(db), prober=lambda: [disk],
               smart_reader=_fake_smart, host="h", mounter=lambda d: None)
    assert on._wants_mount(disk)
    disk.volumes[0].is_system = True
    assert not on._wants_mount(disk)
