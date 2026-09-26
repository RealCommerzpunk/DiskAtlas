import threading

from conftest import make_disk
from sqlalchemy import select

from diskatlas.agent.agent import Agent
from diskatlas.agent.sinks import DatabaseSink
from diskatlas.config import AgentConfig
from diskatlas.db.models import Command, Volume
from diskatlas.probe.types import SmartInfo, VolumeInfo
from diskatlas.services import commands, fslabel, hosts, ingest


def _smart(device):
    return SmartInfo(available=True, health="ok", passed=True)


def _disk_with_volume(mountpoint="/media/x/AV", is_system=False):
    disk = make_disk("sn:TEST1")
    disk.volumes = [VolumeInfo(key="part:g1", device="/dev/sdx1", fs_type="ntfs", label="Alt",
                               mountpoint=mountpoint, is_system=is_system)]
    return disk


def test_command_lifecycle(db):
    with db.session() as s:
        commands.enqueue(s, "pc", "rename_label", {"label": "A"}, disk_key="sn:1")
        commands.enqueue(s, "anderer-pc", "rescan", {})
        assert commands.open_count(s) == 2
        try:
            commands.enqueue(s, "pc", "rm -rf", {})
        except ValueError:
            pass
        else:
            raise AssertionError("unbekannte Aufträge werden abgelehnt")
    with db.session() as s:
        claimed = commands.claim_pending(s, "pc")
        assert [(c["kind"], c["payload"]) for c in claimed] == [("rename_label", {"label": "A"})]
        assert commands.claim_pending(s, "pc") == [], "nur einmal ausgeben"
        commands.finish(s, claimed[0]["id"], ok=False, message="belegt")
    with db.session() as s:
        (row,) = commands.recent(s, "sn:1")
        assert (row.status, row.result) == ("failed", "belegt")


def test_agent_executes_rename_from_own_state(db, monkeypatch):
    disk = _disk_with_volume()
    seen = {}

    def fake_set_label(device, fs_type, mountpoint, label):
        seen.update(device=device, fs=fs_type, mp=mountpoint, label=label)
        return label

    monkeypatch.setattr(fslabel, "set_label", fake_set_label)
    agent = Agent(AgentConfig(), DatabaseSink(db), prober=lambda: [disk], smart_reader=_smart,
                  host="pc")
    ok, msg = agent.execute_command({"id": 1, "kind": "rename_label", "payload": {
        "disk_key": "sn:TEST1", "volume_key": "part:g1", "label": "Neu",
        "device": "/dev/sda"}})  # das vom Server mitgeschickte Gerät wird ignoriert
    assert ok and "Neu" in msg
    assert seen == {"device": "/dev/sdx1", "fs": "ntfs", "mp": "/media/x/AV", "label": "Neu"}

    ok, msg = agent.execute_command({"kind": "rename_label", "payload": {
        "disk_key": "sn:TEST1", "volume_key": "part:unbekannt", "label": "X"}})
    assert not ok and "nicht gefunden" in msg
    disk.volumes[0].is_system = True
    ok, msg = agent.execute_command({"kind": "rename_label", "payload": {
        "disk_key": "sn:TEST1", "volume_key": "part:g1", "label": "X"}})
    assert not ok and "Systemvolumes" in msg
    assert agent.execute_command({"kind": "format_disk", "payload": {}}) == (
        False, "Unbekannter Auftrag: format_disk")


def test_web_to_agent_roundtrip(db, client, monkeypatch):
    disk = _disk_with_volume()
    monkeypatch.setattr(fslabel, "set_label", lambda d, f, m, label: label)
    agent = Agent(AgentConfig(poll_interval=0.05), DatabaseSink(db), prober=lambda: [disk],
                  smart_reader=_smart, host="pc")
    agent.scan_all(index_files=False)  # meldet Platte + Heartbeat (Agent gilt als online)

    disk_id = client.get("/api/v1/disks").json()[0]["id"]
    with db.session() as s:
        volume_id = s.scalar(select(Volume.id))
    r = client.post(f"/volumes/{volume_id}/label", data={"label": "Neu"}, follow_redirects=False)
    assert r.status_code == 303 and "Auftrag" in r.headers["location"]
    assert "wartet" in client.get(f"/disks/{disk_id}").text

    thread = threading.Thread(target=agent._command_loop, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            with db.session() as s:
                (row,) = s.scalars(select(Command)).all()
                if row.status in ("done", "failed"):
                    break
            threading.Event().wait(0.05)
        assert row.status == "done", row.result
    finally:
        agent.stop()
        thread.join(5)
    assert "fertig" in client.get(f"/disks/{disk_id}").text


def test_relabel_refused_when_agent_offline_or_invalid(db, client):
    disk = _disk_with_volume()
    with db.session() as s:
        ingest.upsert_disk(s, "pc", disk)  # Platte bekannt, aber noch kein Agenten-Heartbeat
    with db.session() as s:
        volume_id = s.scalar(select(Volume.id))
    r = client.post(f"/volumes/{volume_id}/label", data={"label": "X"}, follow_redirects=False)
    assert "meldet+sich+gerade+nicht" in r.headers["location"]
    with db.session() as s:
        hosts.record(s, "pc", None)
    r = client.post(f"/volumes/{volume_id}/label", data={"label": "x" * 200},
                    follow_redirects=False)
    assert "zu+lang" in r.headers["location"]
    with db.session() as s:
        assert s.scalar(select(Command.id)) is None


def test_ingest_api_ports_and_commands(client, db):
    with db.session() as s:
        ingest.upsert_disk(s, "pc", make_disk("sn:TEST1"))
    payload = {"host": "pc", "disk_keys": ["sn:TEST1"], "ports_info": {
        "all_ports": ["ata3", "ata4"],
        "present": [{"port": "ata3", "device": "/dev/sdb", "serial": "TEST1",
                     "disk_key": "sn:TEST1"}]}}
    assert client.post("/api/v1/ingest/connected", json=payload).status_code == 200
    live = client.get("/api/v1/bays/live?host=pc").json()
    assert live["online"] and live["ports"] == ["ata3", "ata4"]
    assert live["occupied"][0]["serial"] == "TEST1"
    with db.session() as s:
        commands.enqueue(s, "pc", "rescan", {"disk_key": "sn:TEST1"})
    got = client.get("/api/v1/ingest/commands", params={"host": "pc"}).json()
    assert got[0]["kind"] == "rescan"
    ok = client.post(f"/api/v1/ingest/commands/{got[0]['id']}/result",
                     json={"ok": True, "message": "ok"})
    assert ok.status_code == 200
    assert client.post("/api/v1/ingest/commands/999/result",
                       json={"ok": True}).status_code == 404


class _HttpAdapter:
    """Leitet die HttpSink-Aufrufe (relativ zu /api/v1) an den TestClient weiter."""

    def __init__(self, client):
        self.client = client

    def post(self, path, json):
        return self.client.post("/api/v1" + path, json=json)

    def get(self, path, params=None):
        return self.client.get("/api/v1" + path, params=params)

    def close(self):
        pass


def test_agent_over_http_sink_full_roundtrip(client, db, monkeypatch):
    """Der echte Betriebsweg: Agent -> HttpSink -> Server (Ports, Aufträge, Ergebnis)."""
    from diskatlas.agent.sinks import HttpSink

    disk = _disk_with_volume()
    disk.port = "ata3"
    monkeypatch.setattr(fslabel, "set_label", lambda d, f, m, label: label)
    monkeypatch.setattr("diskatlas.probe.ports.all_ports", lambda *a, **k: ["ata3", "ata4"])
    sink = HttpSink("http://server", client=_HttpAdapter(client))
    agent = Agent(AgentConfig(poll_interval=0.05), sink, prober=lambda: [disk],
                  smart_reader=_smart, host="pc")
    agent.scan_all(index_files=False)

    live = client.get("/api/v1/bays/live?host=pc").json()
    assert live["online"] and [p["port"] for p in live["occupied"]] == ["ata3"]

    with db.session() as s:
        volume_id = s.scalar(select(Volume.id))
    client.post(f"/volumes/{volume_id}/label", data={"label": "ViaHttp"})
    thread = threading.Thread(target=agent._command_loop, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            with db.session() as s:
                (row,) = s.scalars(select(Command)).all()
                if row.status in ("done", "failed"):
                    break
            threading.Event().wait(0.05)
    finally:
        agent.stop()
        thread.join(5)
    assert (row.status, row.result) == ("done", "Bezeichnung geändert: „ViaHttp“.")
