import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from diskatlas.config import load_config
from diskatlas.tray import controller as ctl
from diskatlas.tray import settings
from diskatlas.tray.status import (
    STALE_SECONDS,
    TrayStatus,
    age_text,
    read_status,
    strip_credentials,
    write_status,
)


# ------------------------------------------------------------------ Statusdatei
def test_status_roundtrip_and_stale(tmp_path):
    path = tmp_path / "status.json"
    status = TrayStatus(state="online", server_url="http://s:8765", disks=3, last_ok=1.0)
    write_status(status, path)
    back = read_status(path)
    assert (back.state, back.server_url, back.disks) == ("online", "http://s:8765", 3)

    old = read_status(path, now=time.time() + STALE_SECONDS + 5)
    assert old.state == "stopped" and old.server_url == "http://s:8765"


def test_status_missing_or_corrupt_means_stopped(tmp_path):
    assert read_status(tmp_path / "gibt-es-nicht.json").state == "stopped"
    broken = tmp_path / "kaputt.json"
    broken.write_text("{nicht json", encoding="utf-8")
    assert read_status(broken).state == "stopped"


def test_strip_credentials_and_age_text():
    assert strip_credentials("http://user:pw@host:8765/x") == "http://host:8765/x"
    assert strip_credentials("http://host:8765") == "http://host:8765"
    assert age_text(None) == "noch nie"
    assert age_text(100.0, now=130.0) == "vor 30 s"
    assert age_text(0.0, now=180.0) == "vor 3 min"


# ------------------------------------------------------------------ Konfiguration
def test_validate_cleans_and_rejects():
    ok = settings.validate({"server_url": " http://unraid:8765/ ", "poll_interval": "5",
                            "index_files": True})
    assert ok == {"server_url": "http://unraid:8765", "poll_interval": 5.0, "index_files": True}
    with pytest.raises(ValueError, match="http://"):
        settings.validate({"server_url": "unraid:8765"})
    with pytest.raises(ValueError, match="Zahl"):
        settings.validate({"poll_interval": "abc"})
    with pytest.raises(ValueError, match="zu klein"):
        settings.validate({"poll_interval": 0})
    with pytest.raises(ValueError):
        settings.validate({"index_files": "ja"})
    assert settings.validate({"server_url": ""}) == {"server_url": ""}


def test_save_keeps_other_options_and_is_loadable(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '# mein Kommentar\ndatabase_url = "sqlite:///x.db"\n\n[agent]\n'
        'exclude_dirs = ["a", "b"]\nauto_mount = false\n\n[server]\nport = 9000\n',
        encoding="utf-8",
    )
    token = 'ge"heim\\ü'
    settings.save(path, settings.validate({
        "server_url": "http://unraid:8765", "api_token": token, "poll_interval": 7,
    }))

    config = load_config(path)
    assert config.agent.server_url == "http://unraid:8765"
    assert config.agent.api_token == token
    assert config.agent.poll_interval == 7.0
    assert config.agent.exclude_dirs == ["a", "b"]
    assert config.agent.auto_mount is False
    assert config.server.port == 9000
    assert config.database_url == "sqlite:///x.db"
    assert "mein Kommentar" in (tmp_path / "config.toml.bak").read_text(encoding="utf-8")


def test_save_creates_missing_file_and_load_values(tmp_path):
    path = tmp_path / "neu" / "config.toml"
    assert settings.load_values(path) == settings.default_values()
    settings.save(path, settings.validate({"server_url": "https://a.b", "index_files": False}))
    values = settings.load_values(path)
    assert values["server_url"] == "https://a.b" and values["index_files"] is False
    assert values["poll_interval"] == settings.default_values()["poll_interval"]


def test_save_refuses_to_overwrite_broken_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[agent\nkaputt", encoding="utf-8")
    with pytest.raises(ValueError, match="nicht lesbar"):
        settings.save(path, {"server_url": "http://x"})
    assert path.read_text(encoding="utf-8") == "[agent\nkaputt"


# ------------------------------------------------------------------ Verbindungstest
def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_check_connection_ok_and_token_rejected():
    def handler(request):
        if request.url.path == "/api/v1/health":
            return httpx.Response(200, json={"status": "ok", "version": "0.3.0"})
        if request.headers.get("authorization") == "Bearer gut":
            return httpx.Response(200, json=[])
        return httpx.Response(401, json={"detail": "nein"})

    ok, message = settings.check_connection("http://s", "gut", client=_client(handler))
    assert ok and "0.3.0" in message
    ok, message = settings.check_connection("http://s", "falsch", client=_client(handler))
    assert not ok and "Token" in message


def test_check_connection_failures():
    def down(request):
        raise httpx.ConnectError("refused")

    def slow(request):
        raise httpx.ReadTimeout("zu langsam")

    def redirect(request):
        return httpx.Response(301, headers={"location": "https://s/"})

    def other(request):
        return httpx.Response(200, text="<html>")

    assert "nicht erreichbar" in settings.check_connection("http://s", client=_client(down))[1]
    assert "Zeitüberschreitung" in settings.check_connection("http://s", client=_client(slow))[1]
    assert "https://s/" in settings.check_connection("http://s", client=_client(redirect))[1]
    assert "DiskAtlas" in settings.check_connection("http://s", client=_client(other))[1]
    assert not settings.check_connection("  ")[0]


# ------------------------------------------------------------------ Statusverfolgung
class _FakeSink:
    def __init__(self):
        self.fail = None
        self.closed = False

    def report_connected(self, host, disk_keys, ports_info=None):
        if self.fail:
            raise self.fail
        return "ok"

    def fetch_commands(self, host):
        if self.fail:
            raise self.fail
        return []

    def close(self):
        self.closed = True


def _status_error(code):
    request = httpx.Request("GET", "http://s")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError("x", request=request, response=response)


def test_status_sink_tracks_success_and_failures():
    tracker = ctl.StatusTracker("http://u:p@s:8765", "pc")
    inner = _FakeSink()
    sink = ctl.StatusSink(inner, tracker)
    assert tracker.snapshot().server_url == "http://s:8765"

    assert sink.report_connected("pc", ["a", "b"], None) == "ok"
    snap = tracker.snapshot()
    assert (snap.state, snap.disks) == ("online", 2) and snap.last_ok

    inner.fail = _status_error(401)
    with pytest.raises(httpx.HTTPStatusError):
        sink.fetch_commands("pc")
    assert tracker.snapshot().state == "auth_error"

    inner.fail = httpx.ConnectError("weg")
    with pytest.raises(httpx.ConnectError):
        sink.fetch_commands("pc")
    assert tracker.snapshot().state == "offline"

    inner.fail = _status_error(500)
    with pytest.raises(httpx.HTTPStatusError):
        sink.fetch_commands("pc")
    snap = tracker.snapshot()
    assert snap.state == "offline" and "500" in snap.detail

    inner.fail = None
    sink.fetch_commands("pc")
    assert tracker.snapshot().state == "online"
    assert tracker.snapshot().disks == 2, "Anzahl bleibt, wenn der Aufruf sie nicht meldet"

    sink.close()  # zählt nicht als Verbindungsaufruf
    assert inner.closed


# ------------------------------------------------------------------ Controller
def test_controller_states(tmp_path, monkeypatch):
    monkeypatch.delenv("DISKATLAS_SERVER_URL", raising=False)
    path = tmp_path / "config.toml"

    controller = ctl.AgentController(path)
    controller.start()
    assert controller.status().state == "unconfigured"

    path.write_text("[agent]\nbogus = 1\n", encoding="utf-8")
    controller.restart()
    assert controller.status().state == "config_error"

    class FakeAgent:
        host = "testpc"

        def __init__(self):
            self.sink = _FakeSink()
            self.stopped = threading.Event()

        def watch(self):
            self.sink.report_connected(self.host, ["x"])
            self.stopped.wait(5)

        def stop(self):
            self.stopped.set()

    agents = []

    def fake_make_agent(config):
        agents.append(FakeAgent())
        return agents[0]

    monkeypatch.setattr(ctl, "make_agent", fake_make_agent)
    path.write_text('[agent]\nserver_url = "http://s:8765"\n', encoding="utf-8")
    controller.restart()
    deadline = time.time() + 5
    while controller.status().state != "online" and time.time() < deadline:
        time.sleep(0.02)
    status = controller.status()
    assert (status.state, status.host, status.disks) == ("online", "testpc", 1)

    controller.stop()
    assert controller.status().state == "stopped"
    assert agents[0].stopped.is_set() and agents[0].sink._inner.closed


def test_controller_reports_crashed_agent(tmp_path, monkeypatch):
    class Crashing:
        host = "pc"
        sink = _FakeSink()

        def watch(self):
            raise RuntimeError("kaputt")

        def stop(self):
            pass

    monkeypatch.setattr(ctl, "make_agent", lambda config: Crashing())
    path = tmp_path / "config.toml"
    path.write_text('[agent]\nserver_url = "http://s"\n', encoding="utf-8")
    controller = ctl.AgentController(path)
    controller.start()
    controller._thread.join(5)
    status = controller.status()
    assert status.state == "error" and "kaputt" in status.detail


# ------------------------------------------------------------------ Autostart
@pytest.mark.skipif(sys.platform == "win32", reason="XDG-Autostart gibt es nur unter Linux")
def test_autostart_linux(tmp_path, monkeypatch):
    from diskatlas.tray import autostart

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert not autostart.is_enabled()
    autostart.set_enabled(True)
    entry = (tmp_path / "autostart" / autostart.DESKTOP_FILE).read_text(encoding="utf-8")
    assert "Exec=" in entry and "diskatlas.tray.app" in entry
    assert autostart.is_enabled()
    autostart.set_enabled(False)
    autostart.set_enabled(False)  # zweimal ausschalten ist kein Fehler
    assert not autostart.is_enabled()


def test_autostart_frozen_uses_executable(monkeypatch):
    from diskatlas.tray import autostart
    from diskatlas.tray.app import settings_command

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/opt/DiskAtlas Agent/diskatlas-agent")
    assert autostart.launch_command() == ["/opt/DiskAtlas Agent/diskatlas-agent"]
    assert settings_command(Path("/x/config.toml"))[:2] == [
        "/opt/DiskAtlas Agent/diskatlas-agent", "--settings"]
