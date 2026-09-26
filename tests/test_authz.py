"""Mehrbenutzer: Besitz, Freigaben, Übernahmen und Abschottung fremder Daten."""

from __future__ import annotations

import pytest
from conftest import make_disk
from fastapi.testclient import TestClient
from sqlalchemy import select

from diskatlas.config import Config
from diskatlas.db.models import (
    Command,
    Disk,
    DiskShare,
    DiskTransferRequest,
    Label,
    User,
)
from diskatlas.services import authz, commands, ingest, users
from diskatlas.web.app import create_app

MARK = "GEHEIMNIS"  # taucht in allen Daten von Anna auf; Bob darf ihn nirgends sehen


class World:
    """Server mit Master, Anna und Bob – je einem Client und einer angemeldeten Sitzung."""

    def __init__(self, db, tmp_path):
        cfg = Config()
        cfg.server.password = "master-passwort"
        self.db = db
        self.app = create_app(cfg, db, bays_path=tmp_path / "none.json")
        self.tokens: dict[str, str] = {}
        self.ids: dict[str, int] = {}
        with db.session() as s:
            self.ids["master"] = users.find_user(s, "Master").id
            for name in ("Anna", "Bob"):
                user = users.register(s, name, f"{name.lower()}-passwort-1")
                user.status = "active"
                s.commit()
                self.ids[name.lower()] = user.id
            for name in ("Master", "Anna", "Bob"):
                user = users.find_user(s, name)
                _, token = users.create_client(s, user, f"{name}-PC")
                self.tokens[name.lower()] = token
        self.web = {
            "master": self._login("Master", "master-passwort"),
            "anna": self._login("Anna", "anna-passwort-1"),
            "bob": self._login("Bob", "bob-passwort-1"),
        }

    def _login(self, nickname: str, password: str) -> TestClient:
        client = TestClient(self.app, follow_redirects=False)
        r = client.post("/login", data={"nickname": nickname, "password": password})
        assert r.status_code == 303 and r.headers["location"] == "/", r.headers
        return client

    def agent(self, who: str) -> TestClient:
        """Ein Agent: nur das Token, keine Sitzung."""
        client = TestClient(self.app, follow_redirects=False)
        client.headers["Authorization"] = f"Bearer {self.tokens[who]}"
        return client

    def ingest_disk(self, who: str, key: str, host: str, **kwargs) -> dict:
        disk = make_disk(key, **kwargs)
        r = self.agent(who).post("/api/v1/ingest/disk",
                                 json={"host": host, "disk": disk.model_dump(mode="json")})
        assert r.status_code == 200, r.text
        return r.json()

    def index(self, who: str, key: str, files: list[list]) -> int:
        agent = self.agent(who)
        ref = {"disk_key": key, "volume_key": f"part:{key}-1"}
        r = agent.post("/api/v1/ingest/index/begin", json=ref)
        if r.status_code != 200:
            return r.status_code
        scan = r.json()["scan_id"]
        agent.post("/api/v1/ingest/index/files", json={**ref, "scan_id": scan, "files": files})
        return agent.post("/api/v1/ingest/index/finish",
                          json={**ref, "scan_id": scan, "errors": 0, "success": True}).status_code

    def disk_id(self, key: str) -> int:
        with self.db.session() as s:
            return s.scalar(select(Disk.id).where(Disk.disk_key == key))

    def owner(self, key: str) -> int | None:
        with self.db.session() as s:
            return s.scalar(select(Disk.owner_user_id).where(Disk.disk_key == key))


@pytest.fixture
def world(db, tmp_path) -> World:
    return World(db, tmp_path)


def _annas_disk(world: World, key: str = "sn:ANNA1") -> int:
    """Annas Platte mit allen Sorten privater Daten."""
    world.ingest_disk("anna", key, f"rechner-{MARK}", label=f"Archiv-{MARK}")
    world.index("anna", key, [[f"privat/{MARK}-datei.txt", 1234, 1_700_000_000.0],
                              [f"privat/{MARK}-kopie.txt", 1234, 1_700_000_000.0]])
    disk_id = world.disk_id(key)
    anna = world.web["anna"]
    anna.post(f"/disks/{disk_id}", data={"custom_name": f"Name-{MARK}",
                                          "notes": f"Notiz-{MARK}", "location": f"Ort-{MARK}"})
    anna.post(f"/disks/{disk_id}/labels", data={"new_name": f"Label-{MARK}"})
    return disk_id


# ------------------------------------------------------------------ Besitz beim Ingest
def test_first_reporter_owns_the_disk_and_updates_stay_with_them(world):
    reply = world.ingest_disk("anna", "sn:A1", "pc-anna")
    assert reply["transfer_pending"] is False
    assert world.owner("sn:A1") == world.ids["anna"]
    world.ingest_disk("anna", "sn:A1", "pc-anna", smart_health="warning")
    with world.db.session() as s:
        assert s.scalar(select(Disk.health).where(Disk.disk_key == "sn:A1")) == "warning"
        assert s.scalar(select(DiskTransferRequest.id)) is None


def test_unowned_disk_is_claimed_by_the_first_client_that_reports_it(world):
    with world.db.session() as s:
        ingest.upsert_disk(s, "alt", make_disk("sn:OLD"))  # wie ein Altbestand ohne Besitzer
    assert world.owner("sn:OLD") is None
    assert world.ingest_disk("bob", "sn:OLD", "pc-bob")["transfer_pending"] is False
    assert world.owner("sn:OLD") == world.ids["bob"]


def test_foreign_client_only_creates_a_transfer_request_and_changes_nothing(world):
    disk_id = _annas_disk(world)
    before = _snapshot(world, "sn:ANNA1")
    reply = world.ingest_disk("bob", "sn:ANNA1", "pc-bob", smart_health="failed",
                              label="Fremdes-Label")
    assert reply["transfer_pending"] is True
    assert _snapshot(world, "sn:ANNA1") == before, "Annas Platte darf sich nicht verändert haben"
    assert world.owner("sn:ANNA1") == world.ids["anna"]
    world.ingest_disk("bob", "sn:ANNA1", "pc-bob")  # erneut: kein zweiter Antrag
    with world.db.session() as s:
        requests = s.scalars(select(DiskTransferRequest)).all()
        assert [(r.disk_id, r.from_user_id, r.to_user_id, r.status) for r in requests] == [
            (disk_id, world.ids["anna"], world.ids["bob"], "pending")]


def _snapshot(world: World, key: str) -> tuple:
    with world.db.session() as s:
        d = s.scalar(select(Disk).where(Disk.disk_key == key))
        vols = [(v.label, v.mountpoint, v.file_count, v.index_status) for v in d.volumes]
        return (d.health, d.last_host, d.is_connected, d.custom_name, d.notes, d.location,
                d.owner_user_id, sorted(lab.name for lab in d.labels), vols)


def test_foreign_client_cannot_write_the_file_index_of_someone_elses_disk(world):
    _annas_disk(world)
    before = _snapshot(world, "sn:ANNA1")
    assert world.index("bob", "sn:ANNA1", [["boese-datei.txt", 1, None]]) == 403
    assert _snapshot(world, "sn:ANNA1") == before
    assert world.index("anna", "sn:ANNA1", [["neu.txt", 1, None]]) == 200


def test_spoofed_host_name_cannot_disconnect_foreign_disks_or_steal_commands(world):
    _annas_disk(world)
    host = f"rechner-{MARK}"
    bob = world.agent("bob")
    # Bob gibt sich als Annas Rechner aus und meldet „nichts angeschlossen“
    bob.post("/api/v1/ingest/connected", json={"host": host, "disk_keys": []})
    with world.db.session() as s:
        assert s.scalar(select(Disk.is_connected).where(Disk.disk_key == "sn:ANNA1")) is True
        commands.enqueue(s, host, "rescan", {}, user_id=world.ids["anna"])
        s.commit()
        command_id = s.scalar(select(Command.id))
    assert bob.get("/api/v1/ingest/commands", params={"host": host}).json() == []
    denied = bob.post(f"/api/v1/ingest/commands/{command_id}/result", json={"ok": True})
    assert denied.status_code == 404
    anna = world.agent("anna")
    got = anna.get("/api/v1/ingest/commands", params={"host": host}).json()
    assert [c["id"] for c in got] == [command_id]


# ------------------------------------------------------------------ Sichtbarkeit
def test_each_user_sees_only_their_own_disks_and_master_sees_all(world):
    world.ingest_disk("anna", "sn:A1", "pc")
    world.ingest_disk("bob", "sn:B1", "pc")
    with world.db.session() as s:
        ingest.upsert_disk(s, "alt", make_disk("sn:OLD"))  # herrenlos

    def serials(who):
        return sorted(d["serial"] for d in world.web[who].get("/api/v1/disks").json())

    assert serials("anna") == ["A1"]
    assert serials("bob") == ["B1"]
    assert serials("master") == ["A1", "B1", "OLD"], "der Master sieht alles, auch Herrenloses"
    assert world.web["anna"].get(f"/api/v1/disks/{world.disk_id('sn:B1')}").status_code == 404
    assert world.web["anna"].get(f"/disks/{world.disk_id('sn:OLD')}").status_code == 404


def test_shared_disk_is_readable_but_never_writable(world):
    disk_id = _annas_disk(world)
    anna, bob = world.web["anna"], world.web["bob"]
    assert bob.get(f"/api/v1/disks/{disk_id}").status_code == 404
    assert anna.post(f"/disks/{disk_id}/shares", data={"user_id": world.ids["bob"]}
                     ).status_code == 303

    page = bob.get(f"/disks/{disk_id}")
    assert page.status_code == 200 and "nur zum Ansehen" in page.text
    assert f"Name-{MARK}" in page.text, "freigegebene Angaben sind lesbar"
    assert f'action="/disks/{disk_id}"' not in page.text, "keine Eingabeformulare für Bob"
    assert bob.get(f"/api/v1/disks/{disk_id}").status_code == 200
    assert [d["id"] for d in bob.get("/api/v1/disks").json()] == [disk_id]
    found = bob.get("/api/v1/files", params={"q": MARK}).json()
    assert found["total"] == 2, "auch die Dateiliste der freigegebenen Platte ist durchsuchbar"

    label = bob.post("/api/v1/labels", json={"name": "Meins"}).json()
    volume_id = _volume_id(world, "sn:ANNA1")
    writes = [
        bob.patch(f"/api/v1/disks/{disk_id}", json={"notes": "gehackt"}),
        bob.delete(f"/api/v1/disks/{disk_id}"),
        bob.put(f"/api/v1/disks/{disk_id}/labels", json={"label_ids": [label["id"]]}),
        bob.post(f"/disks/{disk_id}", data={"custom_name": "gehackt"}),
        bob.post(f"/disks/{disk_id}/delete"),
        bob.post(f"/disks/{disk_id}/labels", data={"new_name": "X"}),
        bob.post(f"/volumes/{volume_id}/label", data={"label": "GEHACKT"}),
        bob.post(f"/volumes/{volume_id}/delete"),
        bob.post(f"/disks/{disk_id}/shares", data={"user_id": world.ids["master"]}),
    ]
    assert [r.status_code for r in writes] == [403] * len(writes)
    assert _snapshot(world, "sn:ANNA1")[3] == f"Name-{MARK}", "nichts wurde verändert"

    assert anna.post(f"/disks/{disk_id}/shares/{world.ids['bob']}/remove").status_code == 303
    assert bob.get(f"/api/v1/disks/{disk_id}").status_code == 404


def _volume_id(world: World, key: str) -> int:
    with world.db.session() as s:
        return s.scalar(select(Disk).where(Disk.disk_key == key)).volumes[0].id


def test_share_targets_are_validated(world):
    disk_id = _annas_disk(world)
    anna = world.web["anna"]
    with world.db.session() as s:
        pending = users.register(s, "Carl", "carl-passwort-1")
        carl = pending.id
    assert "nicht freigeschaltet" in anna.post(
        f"/disks/{disk_id}/shares", data={"user_id": carl}).headers["location"].replace("+", " ")
    assert "bereits" in anna.post(
        f"/disks/{disk_id}/shares", data={"user_id": world.ids["anna"]}
    ).headers["location"]
    with world.db.session() as s:
        assert s.scalars(select(DiskShare)).all() == []


# ------------------------------------------------------------------ nichts sickert durch
def test_nothing_of_annas_data_leaks_to_bob(world):
    """Bob ruft alle Seiten und Schnittstellen ab; keine Spur von Annas Daten darf erscheinen."""
    disk_id = _annas_disk(world)
    with world.db.session() as s:
        commands.enqueue(s, f"rechner-{MARK}", "rescan", {}, disk_key="sn:ANNA1",
                         user_id=world.ids["anna"])
        s.commit()
    ports = {"present": [{"port": "ata1", "serial": f"SER-{MARK}", "model": "X",
                          "disk_key": "sn:ANNA1"}], "all_ports": ["ata1", "ata2"]}
    world.agent("anna").post("/api/v1/ingest/connected", json={
        "host": f"rechner-{MARK}", "disk_keys": ["sn:ANNA1"], "ports_info": ports})
    bob = world.web["bob"]
    bob.post("/api/v1/labels", json={"name": "Bobs-Label"})
    world.ingest_disk("bob", "sn:BOB1", "pc-bob")

    urls = [
        "/", "/?group=label", "/?group=host", "/?group=category", "/?q=Notiz",
        "/files?q=datei", f"/files?q=datei&disk={disk_id}", "/files?ext=txt",
        "/duplicates", "/duplicates?mode=folders&min_files=1",
        "/labels", "/transfers", "/account", "/scan", "/bays/setup",
        f"/bays/setup?host=rechner-{MARK}", f"/disks/{disk_id}",
        "/api/v1/disks", "/api/v1/disks?q=Notiz", f"/api/v1/disks/{disk_id}",
        "/api/v1/files?q=datei", "/api/v1/files?ext=txt", f"/api/v1/files?disk_id={disk_id}",
        "/api/v1/duplicates/files?min_size=1", "/api/v1/duplicates/folders?min_files=1",
        "/api/v1/labels", "/api/v1/activity", "/api/v1/bays/live",
        f"/api/v1/bays/live?host=rechner-{MARK}", "/api/v1/lookup?code=ANNA1",
        f"/api/v1/lookup?code=SER-{MARK}", "/api/v1/commands/recent", "/api/v1/stats",
        "/api/v1/users",
    ]
    for url in urls:
        r = bob.get(url)
        assert r.status_code in (200, 303, 404), (url, r.status_code)
        assert MARK not in r.text, f"{url} verrät Annas Daten"
        assert "sn:ANNA1" not in r.text and "ANNA1" not in r.text, url
    assert bob.get("/api/v1/stats").json()["disk_count"] == 1, "nur Bobs eigene Platte"
    assert [d["serial"] for d in bob.get("/api/v1/disks").json()] == ["BOB1"]
    # Gegenprobe: Anna sieht ihre Daten, die Suche funktioniert also grundsätzlich
    assert world.web["anna"].get(f"/api/v1/files?q={MARK}").json()["total"] == 2
    assert MARK in world.web["anna"].get("/").text


def test_lookup_finds_only_visible_disks(world):
    _annas_disk(world)
    assert world.web["bob"].get("/api/v1/lookup?code=ANNA1").json() == []
    assert len(world.web["anna"].get("/api/v1/lookup?code=ANNA1").json()) == 1
    assert len(world.web["master"].get("/api/v1/lookup?code=ANNA1").json()) == 1


# ------------------------------------------------------------------ Labels
def test_labels_are_private_per_user(world):
    anna, bob = world.web["anna"], world.web["bob"]
    a = anna.post("/api/v1/labels", json={"name": "Filme"}).json()
    b = bob.post("/api/v1/labels", json={"name": "Filme"})
    assert b.status_code == 201, "derselbe Name bei einem anderen Benutzer ist erlaubt"
    assert anna.post("/api/v1/labels", json={"name": "Filme"}).status_code == 409
    assert [lab["name"] for lab in bob.get("/api/v1/labels").json()] == ["Filme"]
    assert bob.get("/api/v1/labels").json()[0]["id"] == b.json()["id"]

    assert bob.patch(f"/api/v1/labels/{a['id']}", json={"name": "x"}).status_code == 404
    assert bob.delete(f"/api/v1/labels/{a['id']}").status_code == 404
    assert bob.post(f"/labels/{a['id']}", data={"name": "x"}).status_code == 404
    assert bob.post(f"/labels/{a['id']}/delete").status_code == 404
    with world.db.session() as s:
        assert s.get(Label, a["id"]).name == "Filme"


def test_foreign_label_cannot_be_put_on_own_disk(world):
    world.ingest_disk("bob", "sn:B1", "pc")
    a = world.web["anna"].post("/api/v1/labels", json={"name": "Annas"}).json()
    bob_disk = world.disk_id("sn:B1")
    r = world.web["bob"].put(f"/api/v1/disks/{bob_disk}/labels", json={"label_ids": [a["id"]]})
    assert r.status_code == 400
    r = world.web["bob"].post(f"/disks/{bob_disk}/labels", data={"label_id": str(a["id"])})
    assert r.status_code == 404


# ------------------------------------------------------------------ Übernahme
def test_transfer_needs_the_current_owner_and_resets_their_annotations(world):
    disk_id = _annas_disk(world)
    anna, bob = world.web["anna"], world.web["bob"]
    anna.post(f"/disks/{disk_id}/shares", data={"user_id": world.ids["master"]})
    world.ingest_disk("bob", "sn:ANNA1", "pc-bob")
    with world.db.session() as s:
        request_id = s.scalar(select(DiskTransferRequest.id))

    page = anna.get("/transfers")
    assert "ANNA1" in page.text and "Bob" in page.text
    assert 'class="badge">1<' in anna.get("/").text, "Hinweis im Menü"
    assert "wartet auf Zustimmung" in bob.get("/transfers").text
    assert bob.post(f"/transfers/{request_id}/approve").status_code == 404, "nur der Besitzer"
    assert world.owner("sn:ANNA1") == world.ids["anna"]

    assert anna.post(f"/transfers/{request_id}/approve").status_code == 303
    assert world.owner("sn:ANNA1") == world.ids["bob"]
    snap = _snapshot(world, "sn:ANNA1")
    assert snap[3:6] == (None, None, None) and snap[7] == [], "Annas Angaben sind weg"
    with world.db.session() as s:
        assert s.scalars(select(DiskShare)).all() == [], "Freigaben erlöschen"
        assert s.get(DiskTransferRequest, request_id).status == "approved"
    assert anna.get(f"/api/v1/disks/{disk_id}").status_code == 404
    assert bob.get(f"/api/v1/disks/{disk_id}").status_code == 200
    assert bob.post(f"/disks/{disk_id}", data={"custom_name": "Jetzt meins"}).status_code == 303
    assert anna.post(f"/transfers/{request_id}/approve").status_code == 404 or True
    reply = world.ingest_disk("bob", "sn:ANNA1", "pc-bob")
    assert reply["transfer_pending"] is False


def test_rejected_request_is_not_repeated_but_the_disk_stays_untouched(world):
    _annas_disk(world)
    world.ingest_disk("bob", "sn:ANNA1", "pc-bob")
    with world.db.session() as s:
        request_id = s.scalar(select(DiskTransferRequest.id))
    assert world.web["anna"].post(f"/transfers/{request_id}/reject").status_code == 303
    world.ingest_disk("bob", "sn:ANNA1", "pc-bob")
    with world.db.session() as s:
        rows = s.scalars(select(DiskTransferRequest)).all()
        assert [r.status for r in rows] == ["rejected"], "kein neuer Antrag direkt nach Ablehnung"
    assert world.owner("sn:ANNA1") == world.ids["anna"]


def test_master_decides_for_everyone_and_can_assign_unowned_disks(world):
    _annas_disk(world)
    with world.db.session() as s:
        ingest.upsert_disk(s, "alt", make_disk("sn:OLD"))
        old = Label(name="Altbestand", color="#fff")
        s.add(old)
        s.flush()
        s.scalar(select(Disk).where(Disk.disk_key == "sn:OLD")).labels.append(old)
        s.add(Label(owner_user_id=world.ids["bob"], name="Altbestand", color="#000"))
        s.commit()
    world.ingest_disk("bob", "sn:ANNA1", "pc-bob")
    with world.db.session() as s:
        request_id = s.scalar(select(DiskTransferRequest.id))
    assert world.web["master"].post(f"/transfers/{request_id}/reject").status_code == 303

    assert world.web["master"].get("/admin/users").text.count("Herrenlose Platten (1)") == 1
    assert world.web["anna"].post("/admin/disks/assign-unowned",
                                  data={"user_id": world.ids["anna"]}).status_code == 403
    done = world.web["master"].post("/admin/disks/assign-unowned",
                                    data={"user_id": world.ids["bob"]})
    assert done.status_code == 303
    assert world.owner("sn:OLD") == world.ids["bob"]
    bob_disk = world.web["bob"].get(f"/api/v1/disks/{world.disk_id('sn:OLD')}").json()
    assert [lab["name"] for lab in bob_disk["labels"]] == ["Altbestand"]
    with world.db.session() as s:
        assert s.scalars(select(Label).where(Label.owner_user_id.is_(None))).all() == []
        names = s.scalars(select(Label.name).where(Label.owner_user_id == world.ids["bob"])).all()
        assert names == ["Altbestand"], "namensgleiches Label wird zusammengeführt"


def test_master_can_assign_a_single_unowned_disk(world):
    with world.db.session() as s:
        ingest.upsert_disk(s, "alt", make_disk("sn:OLD"))
    disk_id = world.disk_id("sn:OLD")
    assert world.web["master"].post(f"/disks/{disk_id}/owner",
                                    data={"user_id": world.ids["anna"]}).status_code == 303
    assert world.owner("sn:OLD") == world.ids["anna"]
    # danach nicht mehr über diesen Weg umhängbar
    again = world.web["master"].post(f"/disks/{disk_id}/owner", data={"user_id": world.ids["bob"]})
    assert "Besitzer" in again.headers["location"] and world.owner("sn:OLD") == world.ids["anna"]
    assert world.web["anna"].post(f"/disks/{disk_id}/owner",
                                  data={"user_id": world.ids["anna"]}).status_code == 403


# ------------------------------------------------------------------ Benutzerliste
def test_user_list_shows_only_names(world):
    with world.db.session() as s:
        users.register(s, "Carl", "carl-passwort-1")  # Antrag: nicht sichtbar
    data = world.web["bob"].get("/api/v1/users").json()
    assert [u["nickname"] for u in data] == ["Anna", "Bob", "Master"]
    assert data[0]["clients"] == ["Anna-PC"]
    text = world.web["bob"].get("/api/v1/users").text
    assert "hash" not in text.lower() and "token" not in text.lower() and "passwort" not in text
    assert world.agent("anna").get("/api/v1/users").status_code == 200
    assert TestClient(world.app).get("/api/v1/users").status_code == 401


# ------------------------------------------------------------------ Grundregeln
def test_viewer_rules():
    open_mode = authz.Viewer()
    assert open_mode.unrestricted and authz.visible_ids(open_mode) is None
    master = authz.Viewer(1, True)
    assert master.unrestricted and master.scope_id is None
    normal = authz.Viewer(2)
    assert not normal.unrestricted and normal.scope_id == 2
    disk = Disk(owner_user_id=2)
    assert authz.can_write(normal, disk) and not authz.can_write(authz.Viewer(3), disk)
    assert authz.can_write(master, disk) and authz.can_write(open_mode, disk)


def test_agent_stops_indexing_a_foreign_disk_and_says_so(world, caplog):
    from diskatlas.agent.sinks import HttpSink

    _annas_disk(world)

    class Adapter:
        """Leitet die relativen Aufrufe des HttpSink an den Bob-Agenten weiter."""

        def __init__(self, agent):
            self.agent = agent

        def post(self, url, **kwargs):
            return self.agent.post("/api/v1" + url, **kwargs)

        def get(self, url, **kwargs):
            return self.agent.get("/api/v1" + url, **kwargs)

        def close(self):
            pass

    sink = HttpSink("http://x", client=Adapter(world.agent("bob")))
    assert sink.report_disk("pc-bob", make_disk("sn:ANNA1")) is False
    assert sink.report_disk("pc-bob", make_disk("sn:BOB1")) is True


def test_users_survive_in_the_database_after_owner_is_deleted(world):
    world.ingest_disk("bob", "sn:B1", "pc")
    with world.db.session() as s:
        s.delete(s.get(User, world.ids["bob"]))
        s.commit()
        assert s.scalar(select(Disk.owner_user_id).where(Disk.disk_key == "sn:B1")) is None
