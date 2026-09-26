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


def test_login_flow(db, tmp_path):
    c = _client(db, tmp_path, password="geheim", api_token="tok")
    r = c.get("/files?q=x")
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=%2Ffiles")
    assert c.get("/api/v1/disks").status_code == 401
    assert c.get("/api/v1/health").status_code == 200, "Health bleibt offen (Docker-Healthcheck)"
    assert c.get("/static/style.css").status_code == 200
    assert c.get("/login").status_code == 200

    bad = c.post("/login", data={"password": "falsch", "next": "/"})
    assert "Falsches" in bad.headers["location"]
    assert auth.COOKIE not in bad.headers.get("set-cookie", "")

    ok = c.post("/login", data={"password": "geheim", "next": "https://evil.example/"})
    assert ok.status_code == 303 and ok.headers["location"] == "/", "kein Open-Redirect"
    cookie = ok.headers["set-cookie"]
    assert "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    assert c.get("/").status_code == 200
    assert c.get("/api/v1/disks").status_code == 200

    c.post("/logout")
    assert c.get("/").status_code == 303


def test_cookie_is_bound_to_password_and_forgery_fails(db, tmp_path):
    c = _client(db, tmp_path, password="geheim", api_token="tok")
    c.cookies.set(auth.COOKIE, "9999999999.deadbeef")
    assert c.get("/").status_code == 303, "gefälschte Signatur"
    c.cookies.clear()
    c.post("/login", data={"password": "geheim"})
    assert c.get("/").status_code == 200
    c.app.state.config.server.password = "neues-passwort"  # Passwortwechsel meldet alle ab
    assert c.get("/").status_code == 303


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
        c.post("/login", data={"password": "x"})
    blocked = c.post("/login", data={"password": "geheim"})
    assert "Zu+viele" in blocked.headers["location"], "auch das richtige Passwort wird gebremst"


def test_no_password_means_open_locally(client):
    assert client.get("/").status_code == 200
    assert client.get("/login", follow_redirects=False).status_code == 303
