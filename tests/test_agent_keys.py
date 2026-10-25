"""Agenten-Übersicht, Schlüssel-Fingerabdruck und gemerkte Zielrechner (Roadmap M3)."""

import base64

import pytest
from sqlalchemy import select
from test_authz import World
from test_relay import Machine, bob_requests, run_both, states, two_machines  # noqa: F401
from test_relay import world as relay_world  # noqa: F401

from diskatlas import __version__
from diskatlas.agent import relaycrypto, transfer
from diskatlas.agent.peers import CHANGED, KNOWN, NEW, PeerStore
from diskatlas.db.models import Client
from diskatlas.services import relay

KEY = base64.b64encode(bytes(32)).decode()


def test_fingerprint_format():
    fp = relaycrypto.fingerprint(KEY)
    assert len(fp) == 39 and fp.count(" ") == 7 and fp == fp.lower()
    assert relaycrypto.fingerprint(KEY) == fp, "stabil"
    assert relaycrypto.fingerprint(base64.b64encode(bytes([1]) * 32).decode()) != fp
    for bad in (None, "", "kein base64!", base64.b64encode(b"zu kurz").decode()):
        assert relaycrypto.fingerprint(bad) == ""


def test_peer_store_trusts_on_first_use(tmp_path):
    store = PeerStore(tmp_path / "peers.json")
    assert store.check(7, "A", "Bob · Laptop") == NEW
    assert store.check(7, "A", "Bob · Laptop") == KNOWN
    assert store.check(7, "B", "Bob · Laptop") == CHANGED
    assert store.check(7, "A", "Bob · Neu") == KNOWN
    assert [p["label"] for p in store.listing()] == ["Bob · Neu"]
    assert store.forget(7) and not store.forget(7)
    assert store.check(7, "B") == NEW


def test_read_public_never_creates_a_key(tmp_path):
    path = tmp_path / "relay.key"
    assert relaycrypto.read_public(path) is None and not path.exists()
    _, pub = relaycrypto.load_or_create_key(path)
    assert relaycrypto.read_public(path) == pub


# ------------------------------------------------------------------ Übersicht im Konto
@pytest.fixture
def world(db, tmp_path) -> World:
    return World(db, tmp_path)


def test_account_shows_agent_state(world):
    world.agent("anna").post("/api/v1/ingest/connected", json={
        "host": "pc", "disk_keys": [], "transfer": True, "pubkey": KEY,
        "version": __version__, "platform": "linux"})
    world.agent("bob").post("/api/v1/ingest/connected", json={
        "host": "pc-bob", "disk_keys": [], "version": "0.1.0", "platform": "win32"})
    page = world.web["anna"].get("/account").text
    assert f"Version {__version__}" in page and "Linux" in page and "online" in page
    assert "Kopieren erlaubt" in page and relaycrypto.fingerprint(KEY) in page
    assert "veraltet" not in page
    bob = world.web["bob"].get("/account").text
    assert "Version 0.1.0" in bob and "veraltet" in bob and "Windows" in bob
    assert "Kopieren aus" in bob and relaycrypto.fingerprint(KEY) not in bob
    with world.db.session() as s:
        client = s.scalar(select(Client).where(Client.user_id == world.ids["anna"]))
        assert (client.agent_version, client.agent_platform) == (__version__, "linux")


# ------------------------------------------------------------------ Schutz beim Senden
def test_sender_refuses_a_changed_target_key(relay_world, tmp_path, monkeypatch):  # noqa: F811
    world = relay_world
    anna, bob = two_machines(world, tmp_path, monkeypatch)
    bob_requests(world, anna, bob, target="Eins")
    run_both(anna, bob)
    assert (bob.dst / "Eins" / "geheim.bin").exists()
    assert [p["label"] for p in anna.agent.peers.listing()] == ["Bob · Bob-PC"]
    # Der Server liefert plötzlich einen anderen Schlüssel für Bobs Rechner
    other = base64.b64encode(bytes([9]) * 32).decode()
    with world.db.session() as s:
        s.get(Client, bob.client_id()).pubkey = other
        s.commit()
    bob_requests(world, anna, bob, target="Zwei")
    (job,) = [j for j in anna.jobs() if j["role"] == "send"]
    anna.run(job)
    with world.db.session() as s:
        from diskatlas.db.models import CopyItem

        item = s.get(CopyItem, job["id"])
        assert item.state == "failed" and "Schlüssel des Zielrechners" in item.message
    assert relay.STORE.names() == set(), "nichts wurde hochgeladen"
    assert any("Schlüssel des Zielrechners" in n for n in anna.agent.notices)
    # Nach dem Vergessen wird der neue Schlüssel gemerkt
    anna.agent.peers.forget(bob.client_id())
    assert anna.agent.peers.check(bob.client_id(), other) == NEW
    assert transfer.TransferFailed("x", notify=True).notify


def test_settings_window_lists_keys(tmp_path, monkeypatch):
    from diskatlas.tray import window

    monkeypatch.setattr(window, "default_data_dir", lambda: tmp_path)
    api = window.SettingsApi(tmp_path / "config.toml")
    assert api.get_keys() == {"own": "", "peers": []}
    _, pub = relaycrypto.load_or_create_key(tmp_path / "relay.key")
    PeerStore(tmp_path / "relay-peers.json").check(3, KEY, "Bob · Laptop")
    keys = api.get_keys()
    assert keys["own"] == relaycrypto.fingerprint(pub)
    assert [(p["label"], p["fingerprint"]) for p in keys["peers"]] == [
        ("Bob · Laptop", relaycrypto.fingerprint(KEY))]
    assert api.forget_peer("3")["peers"] == []
