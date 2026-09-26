"""Lokale Kopie über den echten Weg: Server ↔ HttpSink ↔ Agent, mit Dateien in Temp-Ordnern."""

import hashlib

import pytest
from conftest import make_disk
from sqlalchemy import select
from test_authz import World

from diskatlas.agent import transfer
from diskatlas.agent.agent import Agent
from diskatlas.agent.sinks import HttpSink
from diskatlas.config import AgentConfig
from diskatlas.db.models import Client, CopyItem, Disk
from diskatlas.services import copies


class Adapter:
    def __init__(self, client):
        self.client = client

    def post(self, url, **kwargs):
        return self.client.post("/api/v1" + url, **kwargs)

    def get(self, url, **kwargs):
        return self.client.get("/api/v1" + url, **kwargs)

    def close(self):
        pass


@pytest.fixture
def world(db, tmp_path) -> World:
    w = World(db, tmp_path)
    w.app.state.config.server.copy_enabled = True
    return w


class Rig:
    """Annas Rechner mit zwei „Platten“ (Ordner) und einem laufbereiten Agenten."""

    def __init__(self, world, tmp_path, allow=True):
        self.world = world
        self.src = tmp_path / "quelle"
        self.dst = tmp_path / "ziel"
        (self.src / "film").mkdir(parents=True)
        self.dst.mkdir()
        (self.src / "film" / "a.txt").write_bytes(b"hallo " * 1000)
        (self.src / "doc.txt").write_text("doc")
        self.disks = [make_disk("sn:A1", mountpoint=str(self.src), label="Quelle"),
                      make_disk("sn:A2", mountpoint=str(self.dst), label="Ziel")]
        self.sink = HttpSink("http://x", client=Adapter(world.agent("anna")))
        self.agent = Agent(AgentConfig(allow_transfer=allow, poll_interval=0.05), self.sink,
                           prober=lambda: self.disks, host="pc-anna",
                           smart_reader=lambda dev: make_disk().smart)
        self.agent.scan_all(index_files=True)  # meldet Platten, Heartbeat (mit Fähigkeit), Index

    def volume(self, key):
        with self.world.db.session() as s:
            return s.scalar(select(Disk).where(Disk.disk_key == key)).volumes[0].id

    def request(self, sel, path="Anforderungen", who="anna"):
        with self.world.db.session() as s:
            client_id = s.scalar(select(Client.id).where(Client.user_id == self.world.ids[who]))
        return self.world.web[who].post("/requests", data={
            "sel": sel, "target": f"{client_id}:{self.volume('sn:A2')}", "path": path})

    def run_jobs(self):
        jobs = self.sink.fetch_transfers()
        for job in jobs:
            self.agent.run_transfer(job)
        return jobs


def states(world):
    with world.db.session() as s:
        return {i.source_path: (i.state, i.result_name, i.message)
                for i in s.scalars(select(CopyItem))}


def test_agent_copies_file_and_folder_and_reports_done(world, tmp_path):
    rig = Rig(world, tmp_path)
    src = rig.volume("sn:A1")
    assert rig.request([f"f:{src}:doc.txt", f"d:{src}:film"]).status_code == 303
    jobs = rig.run_jobs()
    assert {j["source"]["path"] for j in jobs} == {"doc.txt", "film/a.txt"}
    assert (rig.dst / "Anforderungen" / "doc.txt").read_text() == "doc"
    copied = (rig.dst / "Anforderungen" / "a.txt").read_bytes()
    assert copied == b"hallo " * 1000
    assert {v[0] for v in states(world).values()} == {"done"}
    with world.db.session() as s:
        item = s.scalar(select(CopyItem).where(CopyItem.source_path == "film/a.txt"))
        assert item.sha256 == hashlib.sha256(copied).hexdigest() and item.bytes_done == len(copied)
    assert not list((rig.dst / "Anforderungen").glob(".diskatlas-*")), "keine Teildateien"
    assert rig.sink.fetch_transfers() == [], "nichts wird doppelt ausgeliefert"


def test_existing_target_file_is_never_overwritten(world, tmp_path):
    rig = Rig(world, tmp_path)
    (rig.dst / "Anforderungen").mkdir()
    (rig.dst / "Anforderungen" / "doc.txt").write_text("VORHER")
    rig.request([f"f:{rig.volume('sn:A1')}:doc.txt"])
    rig.run_jobs()
    assert (rig.dst / "Anforderungen" / "doc.txt").read_text() == "VORHER"
    assert (rig.dst / "Anforderungen" / "doc (1).txt").read_text() == "doc"
    assert states(world)["doc.txt"][:2] == ("done", "doc (1).txt")


def test_agent_without_the_switch_gets_no_jobs(world, tmp_path):
    rig = Rig(world, tmp_path, allow=False)
    rig.request([f"f:{rig.volume('sn:A1')}:doc.txt"])
    assert rig.sink.fetch_transfers() == []
    assert states(world)["doc.txt"][0] == "queued"
    with world.db.session() as s:
        assert s.scalar(select(Client.transfer_enabled).where(
            Client.user_id == world.ids["anna"])) is False


def test_other_clients_cannot_claim_or_report(world, tmp_path):
    rig = Rig(world, tmp_path)
    rig.request([f"f:{rig.volume('sn:A1')}:doc.txt"])
    bob = world.agent("bob")
    assert bob.get("/api/v1/ingest/transfers").json() == []
    (job,) = rig.sink.fetch_transfers()
    for kind, body in (("progress", {"bytes_done": 1}),):
        r = bob.post(f"/api/v1/ingest/transfers/{job['id']}/{kind}", json=body)
        assert r.json() == {"abort": True}, "fremder Client erfährt nichts, soll aufhören"
    for kind, body in (("done", {"sha256": "x", "size": 1, "result_name": "x"}),
                       ("fail", {"message": "x"})):
        assert bob.post(f"/api/v1/ingest/transfers/{job['id']}/{kind}",
                        json=body).status_code == 404
    assert states(world)["doc.txt"][0] == "running"


def test_cancelling_aborts_a_running_copy(world, tmp_path, monkeypatch):
    rig = Rig(world, tmp_path)
    monkeypatch.setattr(transfer, "CHUNK", 1024)
    rig.agent.progress_interval = 0  # jeden Block melden
    rig.request([f"f:{rig.volume('sn:A1')}:film/a.txt"])
    (job,) = rig.sink.fetch_transfers()
    with world.db.session() as s:
        rid = s.scalar(select(CopyItem.request_id))
    world.web["anna"].post(f"/requests/{rid}/cancel")
    rig.agent.run_transfer(job)
    assert states(world)["film/a.txt"][0] == "cancelled"
    assert not (rig.dst / "Anforderungen" / "a.txt").exists()
    assert not list((rig.dst / "Anforderungen").glob(".diskatlas-*"))


def test_system_volumes_are_refused(world, tmp_path):
    rig = Rig(world, tmp_path)
    rig.disks[0].volumes[0].is_system = True
    rig.request([f"f:{rig.volume('sn:A1')}:doc.txt"])
    rig.run_jobs()
    state, _, message = states(world)["doc.txt"]
    assert state == "failed" and "Systemvolumes" in message
    assert not (rig.dst / "Anforderungen" / "doc.txt").exists()


def test_source_change_is_copied_with_a_warning(world, tmp_path):
    rig = Rig(world, tmp_path)
    rig.request([f"f:{rig.volume('sn:A1')}:doc.txt"])
    (rig.src / "doc.txt").write_text("doc, aber länger geworden")
    rig.run_jobs()
    state, _, message = states(world)["doc.txt"]
    assert state == "done" and "geändert" in message


def test_missing_source_disk_is_retried_then_fails(world, tmp_path):
    rig = Rig(world, tmp_path)
    rig.request([f"f:{rig.volume('sn:A1')}:doc.txt"])
    rig.disks = [rig.disks[1]]  # die Quelle ist abgesteckt, der Server weiß es noch nicht
    for expected in ("queued", "queued", "failed"):
        for job in rig.sink.fetch_transfers():
            rig.agent.run_transfer(job)
        assert states(world)["doc.txt"][0] == expected
    assert copies.MAX_ATTEMPTS == 3


def test_notice_command_reaches_the_agent_and_is_limited_to_one_per_day(world, tmp_path):
    rig = Rig(world, tmp_path)
    rig.request([f"f:{rig.volume('sn:A1')}:doc.txt"])
    with world.db.session() as s:
        s.scalar(select(Disk).where(Disk.disk_key == "sn:A1")).is_connected = False
        s.commit()
        copies.sweep(s)
        copies.sweep(s)
        s.commit()
    (notice,) = rig.sink.fetch_commands("pc-anna")
    assert notice["kind"] == "notice" and "anschließen" in notice["payload"]["text"]
    assert "Bitte Platte" in world.web["anna"].get("/requests").text
    text = "Bitte Platte „Quelle“ anschließen: 1 Datei(en) warten."
    rig.agent.execute_command({"id": 1, "kind": "notice", "payload": {"text": text}})
    assert rig.agent.notices == [text]
