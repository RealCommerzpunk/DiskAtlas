"""Relay: Ende-zu-Ende-Verschlüsselung, strikt seriell, begrenzter Platz – mit echten Agenten."""

import threading

import pytest
from conftest import make_disk
from sqlalchemy import select
from test_authz import World
from test_transfer_agent import Adapter

from diskatlas.agent import relaycrypto, transfer
from diskatlas.agent.agent import Agent
from diskatlas.agent.sinks import HttpSink
from diskatlas.config import AgentConfig
from diskatlas.db.models import Client, CopyItem, CopyRequest, Disk, DiskShare
from diskatlas.services import copies, relay

SECRET = b"streng geheimer Inhalt " * 400  # ~9 KB, mehrere Stücke bei CHUNK=1024


@pytest.fixture
def world(db, tmp_path, monkeypatch) -> World:
    monkeypatch.setattr(transfer, "CHUNK", 1024)
    monkeypatch.setattr(transfer, "POLL", 0.005)
    w = World(db, tmp_path)
    w.app.state.config.server.copy_enabled = True
    relay.configure(relay.RelayStore(tmp_path / "relay", 10 * 1024**2))
    return w


class Machine:
    """Ein Rechner mit einer Quell- und einer Zielplatte (Ordner) und einem Agenten."""

    def __init__(self, world, tmp_path, who, monkeypatch):
        self.world, self.who = world, who
        self.src = tmp_path / who / "quelle"
        self.dst = tmp_path / who / "ziel"
        self.src.mkdir(parents=True)
        self.dst.mkdir()
        (self.src / "geheim.bin").write_bytes(SECRET)
        (self.src / "leer.txt").write_bytes(b"")
        self.disks = [make_disk(f"sn:{who}-S", mountpoint=str(self.src), label="Q"),
                      make_disk(f"sn:{who}-Z", mountpoint=str(self.dst), label="Z")]
        self.sink = HttpSink("http://x", client=_PutAdapter(world.agent(who)))
        monkeypatch.setattr("diskatlas.agent.agent.default_data_dir", lambda: tmp_path / who / "d")
        self.agent = Agent(AgentConfig(allow_transfer=True, poll_interval=0.05), self.sink,
                           prober=lambda: self.disks, host=f"pc-{who}",
                           smart_reader=lambda dev: make_disk().smart)
        self.agent.progress_interval = 0
        self.agent.scan_all(index_files=True)

    def volume(self, suffix):
        with self.world.db.session() as s:
            return s.scalar(select(Disk).where(Disk.disk_key == f"sn:{self.who}-{suffix}")
                            ).volumes[0].id

    def client_id(self):
        with self.world.db.session() as s:
            return s.scalar(select(Client.id).where(Client.user_id == self.world.ids[self.who]))

    def heartbeat(self):
        self.agent.sink.report_connected("pc", [d.key for d in self.disks])

    def jobs(self):
        return self.sink.fetch_transfers()

    def run(self, job):
        self.agent.run_transfer(job)


class _PutAdapter(Adapter):
    def put(self, url, content=None, headers=None):
        return self.client.put("/api/v1" + url, content=content, headers=headers)


def two_machines(world, tmp_path, monkeypatch, mode="always"):
    anna = Machine(world, tmp_path, "anna", monkeypatch)
    bob = Machine(world, tmp_path, "bob", monkeypatch)
    world.web["anna"].post(f"/disks/{world.disk_id('sn:anna-S')}/shares",
                           data={"user_id": world.ids["bob"], "copy_mode": mode})
    return anna, bob


def bob_requests(world, anna, bob, paths=("geheim.bin",), target="Von Anna"):
    src = anna.volume("S")
    return world.web["bob"].post("/requests", data={
        "sel": [f"f:{src}:{p}" for p in paths],
        "target": f"{bob.client_id()}:{bob.volume('Z')}", "path": target})


def states(world):
    with world.db.session() as s:
        return {i.source_path: (i.state, i.phase) for i in s.scalars(select(CopyItem))}


def run_both(anna, bob):
    """Sender und Empfänger gleichzeitig, wie im echten Betrieb."""
    (send,) = [j for j in anna.jobs() if j["role"] == "send"]
    threads = [threading.Thread(target=anna.run, args=(send,))]
    threads[0].start()
    receive = None
    for _ in range(400):
        found = [j for j in bob.jobs() if j["role"] == "receive"]
        if found:
            receive = found[0]
            break
        threading.Event().wait(0.01)
    assert receive is not None, "der Empfänger bekommt den Auftrag, sobald der Sender läuft"
    threads.append(threading.Thread(target=bob.run, args=(receive,)))
    threads[1].start()
    for t in threads:
        t.join(30)
        assert not t.is_alive()
    return send, receive


# ------------------------------------------------------------------ Ablauf
def test_file_travels_encrypted_serial_and_arrives_intact(world, tmp_path, monkeypatch):
    anna, bob = two_machines(world, tmp_path, monkeypatch)
    assert bob_requests(world, anna, bob).status_code == 303
    assert states(world) == {"geheim.bin": ("queued", "upload")}

    seen, peak = [], [0]
    real_write = relay.RelayStore.write

    def spy(self, data):
        seen.append(data)
        name = real_write(self, data)
        peak[0] = max(peak[0], len(self.names()))
        return name

    monkeypatch.setattr(relay.RelayStore, "write", spy)
    run_both(anna, bob)

    assert (bob.dst / "Von Anna" / "geheim.bin").read_bytes() == SECRET
    assert states(world) == {"geheim.bin": ("done", "upload")}
    assert peak[0] == 1, "immer höchstens ein Stück im Relay"
    assert len(seen) > 5, "in Stücken übertragen"
    assert all(b"geheimer" not in blob and b"geheim.bin" not in blob for blob in seen), \
        "weder Inhalt noch Dateiname im Klartext"
    assert relay.STORE.names() == set(), "nach dem Ende bleibt nichts liegen"
    assert not list((bob.dst / "Von Anna").glob(".diskatlas-*"))


def test_empty_file_and_collision_rename(world, tmp_path, monkeypatch):
    anna, bob = two_machines(world, tmp_path, monkeypatch)
    (bob.dst / "Von Anna").mkdir()
    (bob.dst / "Von Anna" / "leer.txt").write_text("vorher")
    bob_requests(world, anna, bob, ("leer.txt",))
    run_both(anna, bob)
    assert (bob.dst / "Von Anna" / "leer.txt").read_text() == "vorher"
    assert (bob.dst / "Von Anna" / "leer (1).txt").read_bytes() == b""


def test_only_one_file_at_a_time_uses_the_relay(world, tmp_path, monkeypatch):
    anna, bob = two_machines(world, tmp_path, monkeypatch)
    bob_requests(world, anna, bob, ("geheim.bin", "leer.txt"))
    first = [j for j in anna.jobs() if j["role"] == "send"]
    assert len(first) == 1, "nur eine Datei wird übernommen"
    assert [j for j in anna.jobs() if j["role"] == "send"] == [], "Slot ist belegt"
    with world.db.session() as s:
        assert relay.active_uploads(s) == 1


def test_sender_waits_while_the_target_agent_is_not_ready(world, tmp_path, monkeypatch):
    anna, bob = two_machines(world, tmp_path, monkeypatch)
    bob_requests(world, anna, bob)
    with world.db.session() as s:
        s.scalar(select(Client).where(Client.id == bob.client_id())).transfer_enabled = False
        s.commit()
    assert anna.jobs() == []
    with world.db.session() as s:
        s.scalar(select(Client).where(Client.id == bob.client_id())).transfer_enabled = True
        s.scalar(select(Client).where(Client.id == bob.client_id())).pubkey = None
        s.commit()
    assert anna.jobs() == []
    assert states(world)["geheim.bin"][0] == "queued"


# ------------------------------------------------------------------ Angriffe und Fehler
def test_tampered_chunk_is_rejected_and_nothing_is_written(world, tmp_path, monkeypatch):
    anna, bob = two_machines(world, tmp_path, monkeypatch)
    bob_requests(world, anna, bob)
    real_read = relay.RelayStore.read

    def flipped(self, name):
        data = bytearray(real_read(self, name))
        data[len(data) // 2] ^= 0xFF
        return bytes(data)

    monkeypatch.setattr(relay.RelayStore, "read", flipped)
    (send,) = [j for j in anna.jobs() if j["role"] == "send"]
    sender = threading.Thread(target=anna.run, args=(send,), daemon=True)
    sender.start()
    receive = None
    while receive is None:
        receive = next((j for j in bob.jobs() if j["role"] == "receive"), None)
    bob.run(receive)
    sender.join(30)
    assert states(world)["geheim.bin"][0] == "failed"
    assert not (bob.dst / "Von Anna").exists() or not list((bob.dst / "Von Anna").iterdir())
    assert relay.STORE.names() == set()


def test_relay_endpoints_check_the_role(world, tmp_path, monkeypatch):
    anna, bob = two_machines(world, tmp_path, monkeypatch)
    bob_requests(world, anna, bob)
    (send,) = [j for j in anna.jobs() if j["role"] == "send"]
    item = send["id"]
    carol = world.agent("master")  # ein dritter Client
    bob_api, anna_api = world.agent("bob"), world.agent("anna")
    hdr = {"X-Epk": "AAAA", "X-Final": "0"}
    for who in (carol, bob_api):  # nur der Sender darf hochladen
        assert who.put(f"/api/v1/ingest/relay/{item}/chunks/0", content=b"x",
                       headers=hdr).status_code == 404
    assert anna_api.put(f"/api/v1/ingest/relay/{item}/chunks/0", content=b"x" * 40,
                        headers=hdr).status_code == 200
    for who in (carol, anna_api):  # nur der Empfänger darf abholen und quittieren
        assert who.get(f"/api/v1/ingest/relay/{item}/chunks/0").status_code == 404
        assert who.post(f"/api/v1/ingest/relay/{item}/chunks/0/ack").status_code == 404
    got = bob_api.get(f"/api/v1/ingest/relay/{item}/chunks/0")
    assert got.status_code == 200 and got.content == b"x" * 40
    for who in (carol, bob_api):  # Status nur für den Sender
        assert who.get(f"/api/v1/ingest/relay/{item}/status").status_code == 404
    assert anna_api.put(f"/api/v1/ingest/relay/{item}/chunks/0", content=b"neu",
                        headers=hdr).status_code == 409, "ein noch liegendes Stück wird nie ersetzt"
    assert len(relay.STORE.names()) == 1
    # zweites Stück erst nach dem Quittieren, falsche Nummern und Größen werden abgelehnt
    assert anna_api.put(f"/api/v1/ingest/relay/{item}/chunks/1", content=b"y",
                        headers=hdr).status_code == 409
    assert bob_api.post(f"/api/v1/ingest/relay/{item}/chunks/0/ack").status_code == 200
    assert anna_api.put(f"/api/v1/ingest/relay/{item}/chunks/5", content=b"y",
                        headers=hdr).status_code == 409
    big = b"z" * (relay.MAX_CHUNK + 1)
    assert anna_api.put(f"/api/v1/ingest/relay/{item}/chunks/1", content=big,
                        headers=hdr).status_code == 413
    assert relay.STORE.names() == set()


def test_relay_space_is_limited(world, tmp_path, monkeypatch):
    anna, bob = two_machines(world, tmp_path, monkeypatch)
    bob_requests(world, anna, bob)
    relay.STORE.max_bytes = 100
    (send,) = [j for j in anna.jobs() if j["role"] == "send"]
    api = world.agent("anna")
    hdr = {"X-Epk": "AAAA", "X-Final": "0"}
    assert api.put(f"/api/v1/ingest/relay/{send['id']}/chunks/0", content=b"x" * 101,
                   headers=hdr).status_code == 507
    assert relay.STORE.names() == set()
    relay.STORE.max_bytes = 101
    assert api.put(f"/api/v1/ingest/relay/{send['id']}/chunks/0", content=b"x" * 101,
                   headers=hdr).status_code == 200


def test_cancel_clears_the_relay_and_stops_both_sides(world, tmp_path, monkeypatch):
    anna, bob = two_machines(world, tmp_path, monkeypatch)
    bob_requests(world, anna, bob)
    (send,) = [j for j in anna.jobs() if j["role"] == "send"]
    api = world.agent("anna")
    api.put(f"/api/v1/ingest/relay/{send['id']}/chunks/0", content=b"x" * 40,
            headers={"X-Epk": "AAAA", "X-Final": "0"})
    assert len(relay.STORE.names()) == 1
    with world.db.session() as s:
        rid = s.scalar(select(CopyRequest.id))
    world.web["bob"].post(f"/requests/{rid}/cancel")
    assert relay.STORE.names() == set()
    assert api.get(f"/api/v1/ingest/relay/{send['id']}/status").status_code == 404
    assert states(world)["geheim.bin"][0] == "cancelled"


def test_revoked_share_stops_the_relay(world, tmp_path, monkeypatch):
    anna, bob = two_machines(world, tmp_path, monkeypatch)
    bob_requests(world, anna, bob)
    (send,) = [j for j in anna.jobs() if j["role"] == "send"]
    api = world.agent("anna")
    api.put(f"/api/v1/ingest/relay/{send['id']}/chunks/0", content=b"x" * 40,
            headers={"X-Epk": "AAAA", "X-Final": "0"})
    with world.db.session() as s:
        s.get(DiskShare, (world.disk_id("sn:anna-S"), world.ids["bob"])).copy_mode = "never"
        s.commit()
    assert api.get(f"/api/v1/ingest/relay/{send['id']}/status").json()["abort"] is True
    assert relay.STORE.names() == set()


def test_expired_lease_requeues_and_cleans_the_relay(world, tmp_path, monkeypatch):
    anna, bob = two_machines(world, tmp_path, monkeypatch)
    bob_requests(world, anna, bob)
    (send,) = [j for j in anna.jobs() if j["role"] == "send"]
    world.agent("anna").put(f"/api/v1/ingest/relay/{send['id']}/chunks/0", content=b"x" * 40,
                            headers={"X-Epk": "AAAA", "X-Final": "0"})
    from datetime import timedelta

    from diskatlas.services.ingest import utcnow

    with world.db.session() as s:
        s.get(CopyItem, send["id"]).lease_until = utcnow() - timedelta(minutes=1)
        s.commit()
        copies.sweep(s)
        s.commit()
        item = s.get(CopyItem, send["id"])
        assert (item.state, item.relay_pending, item.relay_next) == ("queued", False, 0)
    assert relay.STORE.names() == set()


def test_recover_removes_orphans_and_requeues_lost_chunks(world, tmp_path, monkeypatch):
    anna, bob = two_machines(world, tmp_path, monkeypatch)
    bob_requests(world, anna, bob)
    (send,) = [j for j in anna.jobs() if j["role"] == "send"]
    world.agent("anna").put(f"/api/v1/ingest/relay/{send['id']}/chunks/0", content=b"x" * 40,
                            headers={"X-Epk": "AAAA", "X-Final": "0"})
    orphan = relay.STORE.write(b"waise")
    (relay.STORE.path / "abc.tmp").write_bytes(b"halb")
    with world.db.session() as s:
        blob = s.get(CopyItem, send["id"]).relay_blob
        relay.STORE.delete(blob)  # Datei verloren
        relay.recover(s)
        s.commit()
        assert s.get(CopyItem, send["id"]).state == "queued"
    assert orphan not in relay.STORE.names() and not list(relay.STORE.path.glob("*.tmp"))


# ------------------------------------------------------------------ Kryptografie
def test_crypto_roundtrip_and_tamper_detection():
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    from diskatlas.agent.relaycrypto import Opener, Sealer

    priv = X25519PrivateKey.generate()
    pub = relaycrypto._b64(relaycrypto._raw_public(priv.public_key()))
    sealer = Sealer(pub, "abc")
    opener = Opener(priv, sealer.epk, "abc")
    c0, c1 = sealer.seal(0, b"eins"), sealer.seal(1, b"zwei")
    assert opener.open(0, c0) == b"eins" and opener.open(1, c1) == b"zwei"
    for bad in (lambda: opener.open(1, c0), lambda: opener.open(0, c1),
                lambda: opener.open(0, c0, final=True),
                lambda: opener.open(0, c0[:-1] + bytes([c0[-1] ^ 1]))):
        with pytest.raises(relaycrypto.CryptoError):
            bad()
    trailer = sealer.seal_trailer(2, b"h" * 32, 8)
    assert opener.open_trailer(2, trailer) == (b"h" * 32, 8)
    with pytest.raises(relaycrypto.CryptoError):
        Opener(priv, sealer.epk, "anderer-auftrag").open(0, c0)
    with pytest.raises(relaycrypto.CryptoError):
        Opener(X25519PrivateKey.generate(), sealer.epk, "abc").open(0, c0)


def test_key_file_is_created_once_and_private(tmp_path):
    path = tmp_path / "d" / "relay.key"
    _, first = relaycrypto.load_or_create_key(path)
    _, again = relaycrypto.load_or_create_key(path)
    assert first == again
    import stat
    import sys

    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
