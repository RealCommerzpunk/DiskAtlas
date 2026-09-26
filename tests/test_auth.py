import pytest
from fastapi.testclient import TestClient

from diskatlas.config import Config
from diskatlas.web import auth
from diskatlas.web.app import create_app


def _client(db, tmp_path, **server):
    cfg = Config()
    for key, value in server.items():
        setattr(cfg.server, key, value)
    return TestClient(create_app(cfg, db, bays_path=tmp_path / "none.json"), follow_redirects=False)


def test_exposed_server_requires_password_and_token(db, tmp_path):
    with pytest.raises(RuntimeError, match="DISKATLAS_PASSWORD und DISKATLAS_API_TOKEN"):
        _client(db, tmp_path, host="0.0.0.0")
    with pytest.raises(RuntimeError, match="DISKATLAS_API_TOKEN"):
        _client(db, tmp_path, host="0.0.0.0", password="pw")
    _client(db, tmp_path, host="0.0.0.0", password="pw", api_token="tok")  # ok
    _client(db, tmp_path, host="0.0.0.0", allow_insecure=True)  # ausdrücklich erlaubt
    _client(db, tmp_path)  # Loopback ohne Passwort bleibt möglich


def _login(c, nickname="Master", password="geheim", next="/"):
    return c.post("/login", data={"nickname": nickname, "password": password, "next": next})


def test_login_flow(db, tmp_path):
    c = _client(db, tmp_path, password="geheim", api_token="tok")
    r = c.get("/files?q=x")
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=%2Ffiles")
    assert c.get("/api/v1/disks").status_code == 401
    assert c.get("/api/v1/health").status_code == 200, "Health bleibt offen (Docker-Healthcheck)"
    assert c.get("/static/style.css").status_code == 200
    assert c.get("/login").status_code == 200

    bad = _login(c, password="falsch")
    assert "Falscher" in bad.headers["location"]
    assert auth.COOKIE not in bad.headers.get("set-cookie", "")
    assert "Falscher" in _login(c, nickname="niemand").headers["location"]

    ok = _login(c, nickname="master", next="https://evil.example/")  # Name ohne Groß/Klein
    assert ok.status_code == 303 and ok.headers["location"] == "/", "kein Open-Redirect"
    cookie = ok.headers["set-cookie"]
    assert "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    assert c.get("/").status_code == 200
    assert c.get("/api/v1/disks").status_code == 200

    c.post("/logout")
    assert c.get("/").status_code == 303


def test_master_is_created_once_from_bootstrap_password(db, tmp_path):
    from sqlalchemy import select

    from diskatlas.db.models import User
    from diskatlas.web import security

    _client(db, tmp_path, password="geheim")
    _client(db, tmp_path, password="anderes")  # zweiter Start ändert nichts
    with db.session() as s:
        rows = s.scalars(select(User)).all()
    assert [(u.nickname, u.is_master, u.status) for u in rows] == [("Master", True, "active")]
    assert rows[0].password_hash != "geheim"
    assert security.verify_password("geheim", rows[0].password_hash)


def test_cookie_is_bound_to_user_and_password(db, tmp_path):
    from diskatlas.services import users

    c = _client(db, tmp_path, password="geheim")
    for forged in ("9999999999.deadbeef", "1.9999999999.deadbeef", "x.y.z"):
        c.cookies.set(auth.COOKIE, forged)
        assert c.get("/").status_code == 303, "gefälschte Signatur"
    c.cookies.clear()
    _login(c)
    assert c.get("/").status_code == 200
    with db.session() as s:
        master = users.find_user(s, "Master")
        users.change_password(s, master, "geheim", "neues-passwort-1")  # meldet alle ab
    assert c.get("/").status_code == 303
    assert _login(c, password="geheim").status_code == 303
    assert "Falscher" in _login(c, password="geheim").headers["location"]
    assert _login(c, password="neues-passwort-1").headers["location"] == "/"


def test_token_and_ingest_paths(db, tmp_path):
    c = _client(db, tmp_path, password="geheim", api_token="tok")
    assert c.get("/api/v1/disks", headers={"Authorization": "Bearer tok"}).status_code == 200
    assert c.get("/api/v1/disks", headers={"Authorization": "Bearer nope"}).status_code == 401
    # Ingest prüft sein eigenes Token (nicht die Sitzung)
    body = {"host": "h", "disk_keys": []}
    assert c.post("/api/v1/ingest/connected", json=body).status_code == 401
    ok = c.post("/api/v1/ingest/connected", json=body, headers={"Authorization": "Bearer tok"})
    assert ok.status_code == 200


def test_login_throttle(db, tmp_path):
    c = _client(db, tmp_path, password="geheim", api_token="tok")
    for _ in range(auth.MAX_FAILURES):
        _login(c, password="x")
    blocked = _login(c)
    assert "Zu+viele" in blocked.headers["location"], "auch das richtige Passwort wird gebremst"


def _register(c, nickname="Anna", password="anna-passwort-1", note=""):
    return c.post("/register", data={"nickname": nickname, "password": password, "note": note})


def test_registration_needs_approval_by_master(db, tmp_path):
    c = _client(db, tmp_path, password="geheim")
    assert c.get("/register").status_code == 200, "ohne Anmeldung erreichbar"
    r = _register(c, note="Ich bin Anna")
    assert r.status_code == 200 and "Antrag gesendet" in r.text

    pending = _login(c, "Anna", "anna-passwort-1")
    assert "wartet" in pending.headers["location"]
    assert auth.COOKIE not in pending.headers.get("set-cookie", "")
    assert c.get("/").status_code == 303

    _login(c)  # Master
    page = c.get("/admin/users")
    assert page.status_code == 200 and "Anna" in page.text and "Ich bin Anna" in page.text
    from sqlalchemy import select

    from diskatlas.db.models import User

    with db.session() as s:
        anna_id = s.scalar(select(User.id).where(User.nickname == "Anna"))
    assert c.post(f"/admin/users/{anna_id}/approve").status_code == 303
    assert c.post(f"/admin/users/{anna_id}/approve").status_code == 404, "kein offener Antrag mehr"

    c.post("/logout")
    assert _login(c, "anna", "anna-passwort-1").headers["location"] == "/"
    assert c.get("/").status_code == 200
    assert c.get("/account").status_code == 200
    assert c.get("/admin/users").status_code == 403, "nur der Master verwaltet"
    assert c.post(f"/admin/users/{anna_id}/reject").status_code == 403


def test_registration_rejects_bad_input_and_duplicates(db, tmp_path):
    c = _client(db, tmp_path, password="geheim")
    assert "vergeben" in _register(c, "master").text, "Groß-/Kleinschreibung zählt nicht"
    assert "mindestens 10" in _register(c, "Bob", "kurz").text
    assert "2–40 Zeichen" in _register(c, "<b>", "bob-passwort-1").text
    assert "Antrag gesendet" in _register(c, "Bob", "bob-passwort-1").text
    assert "vergeben" in _register(c, "BOB", "bob-passwort-1").text


def test_registration_reject_deletes_request(db, tmp_path):
    from sqlalchemy import select

    from diskatlas.db.models import User

    c = _client(db, tmp_path, password="geheim")
    _register(c, "Carl")
    _login(c)
    with db.session() as s:
        carl = s.scalar(select(User.id).where(User.nickname == "Carl"))
    assert c.post(f"/admin/users/{carl}/reject").status_code == 303
    with db.session() as s:
        assert s.get(User, carl) is None
    assert "Antrag gesendet" in _register(c, "Carl").text, "Name ist wieder frei"


def test_registration_is_throttled(db, tmp_path):
    c = _client(db, tmp_path, password="geheim")
    for i in range(auth.MAX_FAILURES):
        _register(c, f"Nutzer{i}", "passwort-lang-genug")
    assert "Zu viele" in _register(c, "Zuviel", "passwort-lang-genug").text


def _make_client(c, name="PC"):
    r = c.post("/account/clients", data={"nickname": name})
    assert r.status_code == 200
    import re

    match = re.search(r'id="new-token">([^<]+)<', r.text)
    return match.group(1) if match else None


def test_client_token_authenticates_api_and_is_stored_hashed(db, tmp_path):
    from sqlalchemy import select

    from diskatlas.db.models import Client

    c = _client(db, tmp_path, password="geheim")
    _login(c)
    token = _make_client(c, "Arbeits-PC")
    assert token and len(token) >= 40
    assert token not in c.get("/account").text, "Token wird nur einmal angezeigt"
    assert "Arbeits-PC" in c.get("/account").text
    with db.session() as s:
        stored = s.scalar(select(Client))
        assert stored.token_hash != token and len(stored.token_hash) == 64
        assert stored.last_seen is None
        client_id = stored.id

    fresh = _client(db, tmp_path, password="geheim")  # anderer Browser, ohne Sitzung
    assert fresh.get("/api/v1/disks").status_code == 401
    ok = fresh.get("/api/v1/disks", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200
    assert fresh.get("/api/v1/disks", headers={"Authorization": "Bearer falsch"}).status_code == 401
    with db.session() as s:
        assert s.get(Client, client_id).last_seen is not None

    again = c.post("/account/clients", data={"nickname": "arbeits-pc"})
    assert "schon einen Client" in again.text
    assert c.post(f"/account/clients/{client_id}/delete").status_code == 303
    gone = fresh.get("/api/v1/disks", headers={"Authorization": f"Bearer {token}"})
    assert gone.status_code == 401


def test_clients_of_other_users_cannot_be_deleted(db, tmp_path):
    from diskatlas.db.models import Client

    c = _client(db, tmp_path, password="geheim")
    _login(c)
    _make_client(c)
    with db.session() as s:
        client_id = s.query(Client).one().id
    _register(c, "Dora", "dora-passwort-1")
    with db.session() as s:
        from diskatlas.db.models import User

        s.query(User).filter_by(nickname="Dora").one().status = "active"
        s.commit()
    c.post("/logout")
    _login(c, "Dora", "dora-passwort-1")
    assert c.post(f"/account/clients/{client_id}/delete").status_code == 404


def test_account_password_change(db, tmp_path):
    c = _client(db, tmp_path, password="geheim")
    _login(c)
    other = _client(db, tmp_path, password="geheim")
    _login(other)
    new = "neues-passwort-1"

    def change(current, repeat):
        data = {"current": current, "new": new, "repeat": repeat}
        return c.post("/account/password", data=data)

    assert "stimmt nicht" in change("x", new).text
    assert "nicht gleich" in change("geheim", "anders").text
    done = change("geheim", new)
    assert "Passwort geändert" in done.text
    assert c.get("/").status_code == 200, "die aktuelle Sitzung bleibt gültig"
    assert other.get("/").status_code == 303, "andere Sitzungen sind abgemeldet"


def test_no_password_means_open_locally(client):
    assert client.get("/").status_code == 200
    assert client.get("/login", follow_redirects=False).status_code == 303
    assert client.get("/register").status_code == 404, "ohne Anmeldung keine Benutzerverwaltung"
    assert client.get("/account").status_code == 404
    assert client.get("/admin/users").status_code == 404


def test_create_app_without_explicit_paths_and_legacy_import(db, tmp_path, monkeypatch):
    """Regression: `diskatlas serve` ruft create_app ohne Testpfade auf."""
    from diskatlas.config import default_data_dir
    from diskatlas.services import bays

    # Datenverzeichnis plattformübergreifend umlenken (Linux: XDG, Windows: LOCALAPPDATA)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    legacy = default_data_dir() / "bays.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text('{"ports": ["ata6", "ata5"], "reverse": true}', encoding="utf-8")
    client = TestClient(create_app(Config(), db))
    assert client.get("/").status_code == 200
    with db.session() as s:
        cfg = bays.load_config(s)
    assert cfg.ports[:2] == ["ata6", "ata5"] and cfg.reverse is True
