"""Schächte als Eigenschaft des Clients: Einstellungen, Assistent, Anzeige, Abschottung."""

from __future__ import annotations

import json

import pytest
from conftest import make_disk
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_authz import MARK, World

from diskatlas.config import Config
from diskatlas.db.models import Client, HostState, Setting
from diskatlas.services import bays, hosts, ingest, users
from diskatlas.web.app import create_app

PORTS = ["ata1", "ata2", "ata3", "ata4", "ata5", "ata6"]


@pytest.fixture
def world(db, tmp_path) -> World:
    return World(db, tmp_path)


def _cid(world: World, user: str, name: str | None = None) -> int:
    with world.db.session() as s:
        stmt = select(Client.id).join(Client.user).where(users.User.nickname == user)
        if name:
            stmt = stmt.where(Client.nickname == name)
        return s.scalars(stmt.order_by(Client.id)).first()


def _client_row(world: World, client_id: int) -> Client:
    with world.db.session() as s:
        row = s.get(Client, client_id)
        s.expunge(row)
        return row


def _heartbeat(world: World, who: str, host: str, present: list[dict] | None = None,
               keys: list[str] | None = None, token: str | None = None):
    agent = world.agent(who) if token is None else TestClient(world.app, follow_redirects=False)
    if token is not None:
        agent.headers["Authorization"] = f"Bearer {token}"
    info = {"present": present or [], "all_ports": PORTS}
    r = agent.post("/api/v1/ingest/connected",
                   json={"host": host, "disk_keys": keys or [], "ports_info": info})
    assert r.status_code == 200, r.text


def _setup_bays(world: World, who: str, client_id: int, assignment: list[str]) -> None:
    """Schächte einschalten und Ports zuordnen (wie im Assistenten)."""
    web = world.web[who]
    assert web.post(f"/account/clients/{client_id}/bays",
                    data={"bay_count": len(assignment)}).status_code == 200
    r = web.post(f"/clients/{client_id}/bays", data={"port": assignment})
    assert r.status_code == 303 and r.headers["location"] == "/", r.headers


# ------------------------------------------------------------------ Standard und Einstellungen
def test_default_is_no_bays_and_dashboard_shows_the_disks_plainly(world):
    _heartbeat(world, "anna", "pc", present=[{"port": "ata1", "serial": "S1", "model": "X",
                                                 "disk_key": "sn:S1"}])
    world.ingest_disk("anna", "sn:S1", "pc")
    anna_client = _client_row(world, _cid(world, "Anna"))
    assert (anna_client.has_bays, anna_client.bay_count, anna_client.bay_ports) == (False, 4, None)
    page = world.web["anna"].get("/").text
    assert "Schacht 1" not in page and "bay-rows" not in page
    assert "S1" in page, "die Platte steht ganz normal in der Liste"
    account = world.web["anna"].get("/account").text
    assert "Keine Wechselschächte" in account and "checked" in account


def test_client_bay_settings_on_the_account_page(world):
    cid = _cid(world, "Anna")
    anna = world.web["anna"]
    ok = anna.post(f"/account/clients/{cid}/bays", data={"bay_count": 6})
    assert ok.status_code == 200 and "6 Wechselschächte" in ok.text
    row = _client_row(world, cid)
    assert (row.has_bays, row.bay_count, json.loads(row.bay_ports)) == (True, 6, [None] * 6)
    assert "0 von 6 Schächten zugeordnet" in anna.get("/account").text

    off = anna.post(f"/account/clients/{cid}/bays", data={"bay_count": 6, "no_bays": "true"})
    assert "Keine Wechselschächte." in off.text
    assert _client_row(world, cid).has_bays is False

    for bad in (0, 25, -1):
        r = anna.post(f"/account/clients/{cid}/bays", data={"bay_count": bad})
        assert "zwischen 1 und 24" in r.text, bad
    assert _client_row(world, cid).bay_count == 6, "ungültige Eingaben ändern nichts"


def test_reducing_the_count_never_drops_assigned_ports_silently(world):
    cid = _cid(world, "Anna")
    _heartbeat(world, "anna", "pc")
    _setup_bays(world, "anna", cid, ["ata1", "", "", "ata4"])
    r = world.web["anna"].post(f"/account/clients/{cid}/bays", data={"bay_count": 2})
    assert "ata4" in r.text and "wegfallen" in r.text
    assert _client_row(world, cid).bay_count == 4
    again = world.web["anna"].post(f"/account/clients/{cid}/bays", data={"bay_count": 3})
    assert "wegfallen" in again.text, "ata4 sitzt in Schacht 4"
    assert json.loads(_client_row(world, cid).bay_ports) == ["ata1", None, None, "ata4"]
    more = world.web["anna"].post(f"/account/clients/{cid}/bays", data={"bay_count": 6})
    assert "6 Wechselschächte" in more.text
    assert json.loads(_client_row(world, cid).bay_ports) == ["ata1", None, None, "ata4",
                                                              None, None]


def test_only_the_owner_manages_the_bays_of_a_client(world):
    anna_client = _cid(world, "Anna")
    for who in ("bob", "master"):
        web = world.web[who]
        assert web.get(f"/clients/{anna_client}/bays").status_code == 404
        assert web.post(f"/clients/{anna_client}/bays", data={"port": ["ata1"]}).status_code == 404
        assert web.post(f"/account/clients/{anna_client}/bays",
                        data={"bay_count": 8}).status_code == 404
        assert web.get(f"/api/v1/bays/live?client_id={anna_client}").status_code == 404
    assert _client_row(world, anna_client).bay_count == 4


# ------------------------------------------------------------------ Assistent
def test_wizard_assigns_ports_reported_by_this_client(world):
    cid = _cid(world, "Anna")
    anna = world.web["anna"]
    page = anna.get(f"/clients/{cid}/bays").text
    assert "Bisher meldet dieser Agent keine SATA-Ports" in page
    _heartbeat(world, "anna", "pc", present=[{"port": "ata3", "serial": "S9", "model": "Modell"}])
    page = anna.get(f"/clients/{cid}/bays").text
    assert "ata3 · Modell" in page and f"client_id={cid}" in page, "Assistent fragt diesen Client"

    anna.post(f"/account/clients/{cid}/bays", data={"bay_count": 4})
    r = anna.post(f"/clients/{cid}/bays", data={"port": ["ata3", "ata9", "", "ata1"],
                                                 "reverse": "true"})
    assert r.status_code == 303
    row = _client_row(world, cid)
    assert json.loads(row.bay_ports) == ["ata3", None, None, "ata1"], "ata9 wird nicht gemeldet"
    assert row.bay_reverse is True and row.has_bays is True

    dup = anna.post(f"/clients/{cid}/bays", data={"port": ["ata1", "ata1", "", ""]})
    assert dup.status_code == 303 and "err=" in dup.headers["location"]
    assert json.loads(_client_row(world, cid).bay_ports) == ["ata3", None, None, "ata1"]


def test_saved_assignments_survive_while_the_agent_is_offline(world):
    cid = _cid(world, "Anna")
    _heartbeat(world, "anna", "pc")
    _setup_bays(world, "anna", cid, ["ata1", "ata2", "", ""])
    with world.db.session() as s:
        s.delete(s.get(HostState, hosts.state_key(s.get(Client, cid), "")))  # noch nie gemeldet
        s.commit()
    r = world.web["anna"].post(f"/clients/{cid}/bays", data={"port": ["ata1", "ata2", "", ""]})
    assert r.status_code == 303
    assert json.loads(_client_row(world, cid).bay_ports) == ["ata1", "ata2", None, None]


# ------------------------------------------------------------------ Portzustand je Client
def test_port_state_is_kept_per_client_even_with_the_same_host_name(world):
    anna_pc, bob_pc = _cid(world, "Anna"), _cid(world, "Bob")
    nas_token = None
    with world.db.session() as s:
        _, nas_token = users.create_client(s, users.find_user(s, "Anna"), "Anna-NAS")
    _heartbeat(world, "anna", "nas", present=[{"port": "ata1", "serial": "A"}])
    _heartbeat(world, "bob", "nas", present=[{"port": "ata2", "serial": "B"}])
    _heartbeat(world, "anna", "nas", present=[{"port": "ata3", "serial": "C"}], token=nas_token)
    with world.db.session() as s:
        anna_state = s.get(HostState, f"client:{anna_pc}")
        bob_state = s.get(HostState, f"client:{bob_pc}")
        assert anna_state and bob_state, "jeder Client hat seinen Zustand, kein Verwerfen mehr"
        assert "ata1" in anna_state.data and "ata2" in bob_state.data
        assert s.get(HostState, "nas") is None, "kein Zustand unter dem freien Rechnernamen"
        assert hosts.known_hosts(s) == [], "Client-Zustände stehen nicht in der Rechnerliste"


def test_deleting_a_client_removes_its_port_state(world):
    cid = _cid(world, "Anna")
    _heartbeat(world, "anna", "pc", present=[{"port": "ata1", "serial": "A"}])
    world.web["anna"].post(f"/account/clients/{cid}/delete")
    with world.db.session() as s:
        assert s.get(HostState, f"client:{cid}") is None
        _, token = users.create_client(s, users.find_user(s, "Anna"), "Neu")
    new_id = _cid(world, "Anna", "Neu")
    assert world.web["anna"].get(f"/api/v1/bays/live?client_id={new_id}").json()["occupied"] == []


# ------------------------------------------------------------------ Dashboard
def test_dashboard_shows_a_bay_block_per_client_with_bays(world):
    a1 = _cid(world, "Anna")
    with world.db.session() as s:
        _, nas_token = users.create_client(s, users.find_user(s, "Anna"), "Anna-NAS")
        _, quiet_token = users.create_client(s, users.find_user(s, "Anna"), "Anna-Laptop")
    a2 = _cid(world, "Anna", "Anna-NAS")
    world.ingest_disk("anna", "sn:INBAY", "pc", label="Im-Schacht")
    world.ingest_disk("anna", "sn:LOOSE", "pc", label="Lose")
    world.ingest_disk("anna", "sn:GHOST", "pc", label="Woanders")
    _heartbeat(world, "anna", "pc", keys=["sn:INBAY", "sn:LOOSE"], present=[
        {"port": "ata2", "serial": "INBAY", "model": "M", "disk_key": "sn:INBAY"}])
    _heartbeat(world, "anna", "nas", token=nas_token)
    _heartbeat(world, "anna", "laptop", token=quiet_token)  # ohne Wechselschächte
    _setup_bays(world, "anna", a1, ["ata2", "ata5", "", ""])
    _setup_bays(world, "anna", a2, ["ata1", "", "", ""])
    # GHOST ist dem Schacht ata5 von Client 1 zugeordnet gewesen, steckt aber woanders (nicht im
    # Zustand dieses Clients): bleibt in der Liste
    page = world.web["anna"].get("/").text
    assert page.count("Schächte von „") == 2, "Überschriften erst bei mehreren Blöcken"
    assert "Schächte von „Anna-PC“" in page and "Schächte von „Anna-NAS“" in page
    assert "Schächte von „Anna-Laptop“" not in page, "ohne Wechselschächte kein Block"
    plain = page.split('<tbody>', 1)[-1]
    assert "LOOSE" in plain and "GHOST" in plain, "lose Platten stehen in der Liste"
    assert "INBAY" not in plain.replace("bay-rows", ""), "Platte im Schacht nur im Schachtblock"
    assert "Leer" in page and "Schacht 1" in page


def test_a_single_bay_client_has_no_extra_heading(world):
    cid = _cid(world, "Anna")
    _heartbeat(world, "anna", "pc")
    _setup_bays(world, "anna", cid, ["ata1", "", "", ""])
    page = world.web["anna"].get("/").text
    assert "Schacht 1" in page and "Schächte von „" not in page


def test_bob_never_sees_annas_bays_ports_or_client_ids(world):
    cid = _cid(world, "Anna")
    world.ingest_disk("anna", "sn:ANNA1", f"rechner-{MARK}", label=f"Label-{MARK}")
    _heartbeat(world, "anna", f"rechner-{MARK}", keys=["sn:ANNA1"], present=[
        {"port": "ata2", "serial": "ANNA1", "model": "M", "disk_key": "sn:ANNA1"}])
    _setup_bays(world, "anna", cid, ["ata2", "", "", ""])
    disk_id = world.disk_id("sn:ANNA1")
    world.web["anna"].post(f"/disks/{disk_id}/shares", data={"user_id": world.ids["bob"]})
    bob = world.web["bob"]
    for url in ("/", f"/disks/{disk_id}", "/account", "/api/v1/bays/live", "/bays/setup",
                f"/clients/{cid}/bays", f"/api/v1/bays/live?client_id={cid}"):
        r = bob.get(url)
        assert r.status_code in (200, 303, 404), (url, r.status_code)
        assert "Schächte von" not in r.text and "ata2" not in r.text, url
        assert f"client_id={cid}" not in r.text or url.endswith(f"client_id={cid}"), url
    live = bob.get("/api/v1/bays/live").json()
    assert live["clients"] == {} and live["occupied"] == []
    assert bob.get(f"/api/v1/bays/live?client_id={cid}").status_code == 404


# ------------------------------------------------------------------ Seriennummer-Suche
def test_lookup_reports_the_bay_of_the_client_that_saw_the_disk(world):
    cid = _cid(world, "Anna")
    world.ingest_disk("anna", "sn:FINDME1", "pc")
    _heartbeat(world, "anna", "pc", keys=["sn:FINDME1"], present=[
        {"port": "ata3", "serial": "FINDME1", "model": "M", "disk_key": "sn:FINDME1"}])
    anna = world.web["anna"]
    plain = anna.get("/api/v1/lookup?code=FINDME1").json()[0]
    assert (plain["state"], plain["bay"]) == ("connected", None), "ohne Wechselschächte"
    _setup_bays(world, "anna", cid, ["ata1", "ata2", "ata3", ""])
    found = anna.get("/api/v1/lookup?code=FINDME1").json()[0]
    assert (found["state"], found["bay"]) == ("bay", 3)
    world.web["anna"].post(f"/account/clients/{cid}/bays", data={"bay_count": 3, "no_bays": "true"})
    assert anna.get("/api/v1/lookup?code=FINDME1").json()[0]["state"] == "connected"


# ------------------------------------------------------------------ Live-Schnittstelle
def test_bays_live_for_own_clients(world):
    cid = _cid(world, "Anna")
    _heartbeat(world, "anna", "pc", present=[{"port": "ata2", "serial": "S", "model": "M"}])
    anna = world.web["anna"]
    one = anna.get(f"/api/v1/bays/live?client_id={cid}").json()
    assert one["host"] == "Anna-PC" and one["online"] is True and one["ports"] == PORTS
    assert [o["port"] for o in one["occupied"]] == ["ata2"]
    assert list(one["clients"]) == [str(cid)]

    empty = anna.get("/api/v1/bays/live").json()
    assert empty["clients"] == {} and empty["occupied"] == [], "nur Clients mit Wechselschächten"
    anna.post(f"/account/clients/{cid}/bays", data={"bay_count": 4})
    every = anna.get("/api/v1/bays/live").json()
    assert every["online"] is True and every["occupied"][0]["client_id"] == cid
    assert list(every["clients"]) == [str(cid)]


def test_local_mode_keeps_working_without_clients(db, tmp_path):
    client = TestClient(create_app(Config(), db, bays_path=tmp_path / "b.json"))
    with db.session() as s:
        ingest.upsert_disk(s, "pc", make_disk("sn:LOC1"))
        hosts.record(s, "pc", {"all_ports": ["ata3", "ata4"], "present": [
            {"port": "ata3", "serial": "LOC1", "model": "M", "disk_key": "sn:LOC1"}]})
        bays.save_config(s, bays.BayConfig(host="pc", ports=["ata3", "ata4", None, None]))
    page = client.get("/").text
    assert "Schacht 1" in page and "Schächte von" not in page
    assert "href=\"/bays/setup\"" in page
    live = client.get("/api/v1/bays/live?host=pc").json()
    assert [o["port"] for o in live["occupied"]] == ["ata3"] and live["online"] is True
    assert client.get("/bays/setup").status_code == 200
    saved = client.post("/bays/setup", data={"host": "pc", "port": ["ata4", "ata3", "", ""]},
                        follow_redirects=False)
    assert saved.status_code == 303
    with db.session() as s:
        assert bays.load_config(s).ports == ["ata4", "ata3", None, None]


# ------------------------------------------------------------------ Übernahme alter Zuordnungen
def _legacy(world: World, key: str, host: str | None, ports: list, reverse: bool = False):
    with world.db.session() as s:
        s.add(Setting(key=key, value=json.dumps({"host": host, "ports": ports,
                                                 "reverse": reverse})))
        s.commit()


def _setting(world: World, key: str) -> Setting | None:
    with world.db.session() as s:
        return s.get(Setting, key)


def test_legacy_user_setting_is_adopted_by_the_first_matching_client_only(world):
    anna = world.ids["anna"]
    _legacy(world, f"bays:{anna}", "pc", ["ata3", None, "ata5", None], reverse=True)
    with world.db.session() as s:
        _, second = users.create_client(s, users.find_user(s, "Anna"), "Zweiter")
    first, other = _cid(world, "Anna"), _cid(world, "Anna", "Zweiter")

    _heartbeat(world, "anna", "anderer-rechner")
    assert _setting(world, f"bays:{anna}") is not None, "falscher Rechner: nichts übernommen"
    assert _client_row(world, first).has_bays is False

    _heartbeat(world, "anna", "pc")
    row = _client_row(world, first)
    assert (row.has_bays, row.bay_count, row.bay_reverse) == (True, 4, True)
    assert json.loads(row.bay_ports) == ["ata3", None, "ata5", None]
    assert _setting(world, f"bays:{anna}") is None

    _heartbeat(world, "anna", "pc", token=second)
    assert _client_row(world, other).has_bays is False, "kein zweites Mal übernommen"


def test_legacy_setting_without_host_or_without_ports(world):
    anna, bob = world.ids["anna"], world.ids["bob"]
    _legacy(world, f"bays:{anna}", None, ["ata1", None, None, None])
    _legacy(world, f"bays:{bob}", "pc", [None, None, None, None])  # nie eingerichtet
    _heartbeat(world, "anna", "irgendein-name")
    _heartbeat(world, "bob", "pc")
    assert _client_row(world, _cid(world, "Anna")).has_bays is True, "ohne Rechner: passt"
    assert _client_row(world, _cid(world, "Bob")).has_bays is False, "ohne Ports: bleibt bei keine"


def test_only_the_master_adopts_the_orphaned_global_setting(world):
    _legacy(world, "bays", "pc", ["ata2", None, None, None])
    _heartbeat(world, "anna", "pc")
    assert _client_row(world, _cid(world, "Anna")).has_bays is False
    assert _setting(world, "bays") is not None
    _heartbeat(world, "master", "pc")
    row = _client_row(world, _cid(world, "Master"))
    assert row.has_bays is True and json.loads(row.bay_ports)[0] == "ata2"
    assert _setting(world, "bays") is None


def test_configured_clients_keep_their_assignment(world):
    cid = _cid(world, "Anna")
    _heartbeat(world, "anna", "pc")
    _setup_bays(world, "anna", cid, ["ata1", "", "", ""])
    _legacy(world, f"bays:{world.ids['anna']}", "pc", ["ata6", None, None, None])
    _heartbeat(world, "anna", "pc")
    assert json.loads(_client_row(world, cid).bay_ports)[0] == "ata1"
    assert _setting(world, f"bays:{world.ids['anna']}") is not None


# ------------------------------------------------------------------ Migration 0007
def test_migration_gives_existing_clients_no_bays(tmp_path):
    from alembic import command

    from diskatlas.db import Database, alembic_config

    db = Database(f"sqlite:///{(tmp_path / 'alt.db').as_posix()}")
    with db.engine.begin() as conn:
        command.upgrade(alembic_config(conn), "0006")
        conn.exec_driver_sql("INSERT INTO users (id, nickname, password_hash, is_master, status, "
                             "created_at) VALUES (1, 'Anna', 'x', 0, 'active', '2026-01-01')")
        conn.exec_driver_sql("INSERT INTO clients (id, user_id, nickname, token_hash, created_at) "
                             "VALUES (1, 1, 'PC', 'abc', '2026-01-01')")
    db.upgrade()
    with db.engine.connect() as conn:
        row = conn.exec_driver_sql("SELECT has_bays, bay_count, bay_ports, bay_reverse "
                                   "FROM clients").one()
    assert tuple(row) == (0, 4, None, 0)
    db.dispose()


def test_bay_config_helpers():
    assert bays.clean_ports(["ata1", "x", None], 4) == ["ata1", None, None, None]
    assert bays.clean_ports(["ata1"] * 3, 2) == ["ata1", "ata1"]
    assert bays.resize(["ata1", None], 4) == ["ata1", None, None, None]
    with pytest.raises(bays.BayError, match="ata3"):
        bays.resize([None, None, "ata3"], 2)
    with pytest.raises(bays.BayError):
        bays.check_count(bays.MAX_BAYS + 1)
    with pytest.raises(bays.BayError):
        bays.check_unique(["ata1", "ata1"])
