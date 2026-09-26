"""„Zuletzt an Client X“: Vermerk, Filter für Platten und Dateien, Gruppierung, Abschottung."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_authz import MARK, World, _annas_disk

from diskatlas.config import Config
from diskatlas.db.models import Client, Disk
from diskatlas.services import queries, users
from diskatlas.web.app import create_app


@pytest.fixture
def world(db, tmp_path) -> World:
    return World(db, tmp_path)


def _client_id(world: World, user: str, name: str) -> int:
    with world.db.session() as s:
        return s.scalar(select(Client.id).join(Client.user).where(
            Client.nickname == name, users.User.nickname == user))


def _last_client(world: World, key: str) -> int | None:
    with world.db.session() as s:
        return s.scalar(select(Disk.last_client_id).where(Disk.disk_key == key))


def _second_client(world: World, user: str, name: str) -> str:
    """Zweiter Client desselben Benutzers; gibt das Token zurück."""
    with world.db.session() as s:
        _, token = users.create_client(s, users.find_user(s, user), name)
    return token


def _agent(world: World, token: str) -> TestClient:
    client = TestClient(world.app, follow_redirects=False)
    client.headers["Authorization"] = f"Bearer {token}"
    return client


def _ingest(agent: TestClient, key: str, host: str) -> None:
    from conftest import make_disk

    disk = make_disk(key)
    r = agent.post("/api/v1/ingest/disk", json={"host": host, "disk": disk.model_dump(mode="json")})
    assert r.status_code == 200, r.text


# ------------------------------------------------------------------ Erfassung
def test_disks_remember_the_client_that_saw_them_last(world):
    pc = _client_id(world, "Anna", "Anna-PC")
    world.ingest_disk("anna", "sn:ALPHA1", "pc-anna")
    assert _last_client(world, "sn:ALPHA1") == pc

    nas_token = _second_client(world, "Anna", "Anna-NAS")
    nas = _client_id(world, "Anna", "Anna-NAS")
    _ingest(_agent(world, nas_token), "sn:ALPHA1", "nas")
    assert _last_client(world, "sn:ALPHA1") == nas, "der letzte Client gewinnt"

    # Heartbeat: „angeschlossen“ meldet den Client ebenfalls
    world.agent("anna").post("/api/v1/ingest/connected", json={"host": "pc-anna",
                                                               "disk_keys": ["sn:ALPHA1"]})
    assert _last_client(world, "sn:ALPHA1") == pc


def test_a_foreign_client_never_becomes_the_last_client(world):
    world.ingest_disk("anna", "sn:ALPHA1", "pc-anna")
    pc = _client_id(world, "Anna", "Anna-PC")
    world.ingest_disk("bob", "sn:ALPHA1", "pc-bob")  # nur Übernahmeantrag
    world.agent("bob").post("/api/v1/ingest/connected",
                            json={"host": "pc-bob", "disk_keys": ["sn:ALPHA1"]})
    assert _last_client(world, "sn:ALPHA1") == pc


def test_deleting_a_client_forgets_it_on_the_disks(world):
    world.ingest_disk("anna", "sn:ALPHA1", "pc")
    client_id = _client_id(world, "Anna", "Anna-PC")
    assert world.web["anna"].post(f"/account/clients/{client_id}/delete").status_code == 303
    assert _last_client(world, "sn:ALPHA1") is None
    token = _second_client(world, "Anna", "Neu")  # kann Id des gelöschten erben
    _ingest(_agent(world, token), "sn:BRAVO2", "neu")
    assert _last_client(world, "sn:ALPHA1") is None


# ------------------------------------------------------------------ Filter
def _two_clients_two_disks(world: World):
    world.ingest_disk("anna", "sn:ALPHA1", "pc-anna")
    world.index("anna", "sn:ALPHA1", [["film-eins.mkv", 10, None]])
    nas = _agent(world, _second_client(world, "Anna", "Anna-NAS"))
    _ingest(nas, "sn:BRAVO2", "nas")
    ref = {"disk_key": "sn:BRAVO2", "volume_key": "part:sn:BRAVO2-1"}
    scan = nas.post("/api/v1/ingest/index/begin", json=ref).json()["scan_id"]
    nas.post("/api/v1/ingest/index/files",
             json={**ref, "scan_id": scan, "files": [["film-zwei.mkv", 20, None]]})
    nas.post("/api/v1/ingest/index/finish", json={**ref, "scan_id": scan, "errors": 0,
                                                  "success": True})
    with world.db.session() as s:  # eine Platte ohne bekannten Client (Altbestand)
        disk = s.scalar(select(Disk).where(Disk.disk_key == "sn:ALPHA1"))
        from conftest import make_disk

        from diskatlas.services import ingest

        ingest.upsert_disk(s, "alt", make_disk("sn:OLDONE"))
        old = s.scalar(select(Disk).where(Disk.disk_key == "sn:OLDONE"))
        old.owner_user_id = disk.owner_user_id


def test_filter_disks_and_files_by_client(world):
    _two_clients_two_disks(world)
    anna = world.web["anna"]
    pc = _client_id(world, "Anna", "Anna-PC")
    nas = _client_id(world, "Anna", "Anna-NAS")

    def serials(query: str) -> list[str]:
        return sorted(d["serial"] for d in anna.get(f"/api/v1/disks?{query}").json())

    assert serials("") == ["ALPHA1", "BRAVO2", "OLDONE"]
    assert serials(f"client={pc}") == ["ALPHA1"]
    assert serials(f"client={nas}") == ["BRAVO2"]
    assert serials("client=none") == ["OLDONE"]
    assert serials("client=99999") == []
    assert serials("client=abc") == []
    assert serials("q=Anna-NAS") == ["BRAVO2"], "auch die Textsuche kennt den Client"

    def files(query: str) -> list[str]:
        items = anna.get(f"/api/v1/files?q=film&{query}").json()["items"]
        return sorted(f["name"] for f in items)

    assert files("") == ["film-eins.mkv", "film-zwei.mkv"]
    assert files(f"client={pc}") == ["film-eins.mkv"]
    assert files(f"client={nas}") == ["film-zwei.mkv"]

    assert "film-zwei.mkv" in anna.get(f"/files?client={nas}").text
    assert "film-eins.mkv" not in anna.get(f"/files?client={nas}").text
    dashboard = anna.get(f"/?client={nas}").text
    assert "sn:BRAVO2" in dashboard or "BRAVO2" in dashboard
    assert "ALPHA1" not in dashboard.split("<tbody>", 1)[-1].replace("Anna-NAS", "")


def test_client_options_list_only_clients_of_visible_disks(world):
    _two_clients_two_disks(world)
    with world.db.session() as s:
        options = queries.client_options(s, None)
    assert [(o.label) for o in options] == ["Anna / Anna-NAS", "Anna / Anna-PC", "(unbekannt)"]
    anna_page = world.web["anna"].get("/").text
    assert 'name="client"' in anna_page and "Anna / Anna-NAS" in anna_page
    bob_page = world.web["bob"].get("/").text
    assert 'name="client"' not in bob_page and "Anna-NAS" not in bob_page, "Bob sieht keine"


def test_group_by_client(world):
    _two_clients_two_disks(world)
    page = world.web["anna"].get("/?group=client").text
    for heading in ("Anna / Anna-PC", "Anna / Anna-NAS", "(unbekannt)"):
        assert heading in page
    assert 'value="client"' in page


def test_shared_disk_shows_its_client_name_and_bob_can_filter_by_it(world):
    _two_clients_two_disks(world)
    pc = _client_id(world, "Anna", "Anna-PC")
    disk_id = world.disk_id("sn:ALPHA1")
    world.web["anna"].post(f"/disks/{disk_id}/shares", data={"user_id": world.ids["bob"]})
    bob = world.web["bob"]
    world.ingest_disk("bob", "sn:B1", "pc-bob")

    assert [d["serial"] for d in bob.get(f"/api/v1/disks?client={pc}").json()] == ["ALPHA1"]
    nas = _client_id(world, "Anna", "Anna-NAS")
    assert bob.get(f"/api/v1/disks?client={nas}").json() == [], "unfreigegebene bleiben verborgen"
    page = bob.get(f"/disks/{disk_id}").text
    assert "Anna / Anna-PC" in page
    assert "Anna / Anna-PC" in bob.get("/").text
    assert "Anna-NAS" not in bob.get("/").text, "Client ohne sichtbare Platten fehlt in der Liste"


def test_leaks_nothing_through_the_client_filter(world):
    _annas_disk(world)
    pc = _client_id(world, "Anna", "Anna-PC")
    bob = world.web["bob"]
    for url in (f"/?client={pc}", f"/files?client={pc}", f"/api/v1/disks?client={pc}",
                f"/api/v1/files?client={pc}", "/?group=client", "/api/v1/disks?client=none"):
        r = bob.get(url)
        assert r.status_code == 200
        assert MARK not in r.text and "Anna-PC" not in r.text, url
    assert bob.get(f"/api/v1/files?client={pc}").json()["total"] == 0


def test_no_client_filter_in_local_mode(db, tmp_path):
    client = TestClient(create_app(Config(), db, bays_path=tmp_path / "b.json"))
    from conftest import make_disk

    from diskatlas.services import ingest

    with db.session() as s:
        ingest.upsert_disk(s, "pc", make_disk("sn:LOCAL"))
    page = client.get("/").text
    assert 'name="client"' not in page and 'value="client"' not in page
    assert "LOCAL" in page
    assert client.get("/api/v1/disks?client=none").json()[0]["serial"] == "LOCAL"
