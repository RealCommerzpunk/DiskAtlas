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
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert not autostart.is_enabled()
    autostart.set_enabled(True)
    entry = (tmp_path / "autostart" / autostart.DESKTOP_FILE).read_text(encoding="utf-8")
    assert "Exec=" in entry and "diskatlas.tray.app" in entry
    icon = tmp_path / "data" / "diskatlas" / "diskatlas.png"
    assert f"Icon={icon}" in entry and icon.is_file()
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


# ------------------------------------------------------------------ Lokaler Betrieb
def test_controller_local_mode_runs_web_ui(tmp_path, monkeypatch):
    monkeypatch.delenv("DISKATLAS_SERVER_URL", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    class IdleAgent:
        host = "pc"

        def __init__(self):
            self.sink = _FakeSink()
            self.stopped = threading.Event()

        def watch(self):
            self.sink.report_connected(self.host, ["a", "b"])
            self.stopped.wait(5)

        def stop(self):
            self.stopped.set()

    monkeypatch.setattr(ctl, "make_agent", lambda config: IdleAgent())
    path = tmp_path / "config.toml"
    db_url = f"sqlite:///{(tmp_path / 'local.db').as_posix()}"
    path.write_text(f'database_url = "{db_url}"\n[agent]\nserver_url = ""\n[server]\nport = 0\n',
                    encoding="utf-8")
    controller = ctl.AgentController(path)
    controller.start()
    try:
        deadline = time.time() + 5
        while controller.status().state != "online" and time.time() < deadline:
            time.sleep(0.02)
        status = controller.status()
        assert (status.mode, status.state, status.disks) == ("local", "online", 2)
        assert status.label == "Läuft lokal"
        assert status.server_url.startswith("http://127.0.0.1:")
        response = httpx.get(f"{status.server_url}/api/v1/health", timeout=5)
        assert response.status_code == 200
        server_thread = controller._local.thread
    finally:
        controller.stop()
    assert not server_thread.is_alive()
    assert controller.status().state == "stopped"


def test_controller_local_mode_start_failure(tmp_path, monkeypatch):
    monkeypatch.delenv("DISKATLAS_SERVER_URL", raising=False)

    def broken(config):
        raise RuntimeError("Port kaputt")

    monkeypatch.setattr(ctl, "start_local_server", broken)
    path = tmp_path / "config.toml"
    path.write_text("[agent]\nindex_files = false\n", encoding="utf-8")
    controller = ctl.AgentController(path)
    controller.start()
    status = controller.status()
    assert (status.mode, status.state) == ("local", "error") and "Port kaputt" in status.detail


def test_status_mode_survives_file_roundtrip(tmp_path):
    path = tmp_path / "s.json"
    write_status(TrayStatus(state="online", mode="local", server_url="http://127.0.0.1:8765"),
                 path)
    status = read_status(path)
    assert (status.mode, status.label) == ("local", "Läuft lokal")
    assert TrayStatus(state="online").label == "Verbunden"


# ------------------------------------------------------------------ smartctl / Fenster
def test_find_smartctl_uses_bundled_copy_on_windows(tmp_path, monkeypatch):
    from diskatlas.probe import smart

    exe = tmp_path / "smartmontools" / "smartctl.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    monkeypatch.setattr(smart, "WINDOWS_SMARTCTL", tmp_path / "nicht-installiert.exe")
    assert smart.bundled_smartctl() is None  # ohne Programmdatei (_MEIPASS) nichts
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(smart.shutil, "which", lambda name: None)
    monkeypatch.setattr(sys, "platform", "win32")
    assert smart.find_smartctl() == str(exe)


def test_settings_api_state(tmp_path):
    from diskatlas.tray.window import SettingsApi

    path = tmp_path / "config.toml"
    state = SettingsApi(path).get_state()
    assert state["configured"] is False and state["smartctl_bundled"] is False
    if sys.platform != "win32":
        assert state["admin"] is None
    settings.save(path, settings.validate({"server_url": ""}))
    state = SettingsApi(path).get_state()
    assert state["configured"] is True and state["values"]["server_url"] == ""


def test_window_icon_matches_platform(monkeypatch):
    from diskatlas import runtime

    assert runtime.window_icon().name == "icon_256.png" or sys.platform == "win32"
    monkeypatch.setattr(sys, "platform", "win32")
    icon = runtime.window_icon()
    assert icon.name == "favicon.ico" and icon.read_bytes()[:4] == b"\x00\x00\x01\x00"


def test_tray_icon_rendering():
    pytest.importorskip("PIL")
    from diskatlas.tray import icon

    for size in (*icon.GLYPH_SIZES, 18, 96):  # vorhandene Größen und umgerechnete
        image = icon.render_icon("online", "light", size)
        assert image.size == (size, size) and image.mode == "RGBA"
    light = icon.render_icon("offline", "light", 32)
    dark = icon.render_icon("offline", "dark", 32)
    corner = (32 - 6, 32 - 6)  # Statuspunkt unten rechts: rot, in beiden Varianten
    assert light.getpixel(corner)[:3] == dark.getpixel(corner)[:3] == icon.DEFAULT_DOT
    # Glyphe: hell bzw. dunkel, Hintergrund transparent
    opaque = [p for p in light.getdata() if p[3] == 255 and p[:3] != icon.DEFAULT_DOT]
    assert opaque and all(p[:3] == icon.GLYPH_COLORS["light"] for p in opaque)
    assert light.getpixel((0, 31))[3] == 0 or light.getpixel((0, 0))[3] == 0
    # Linux: Glyphe mit Rand (sichtbar so groß wie die 16-px-Systemsymbole im 24er-Feld)
    padded = icon.render_icon("online", "light", 48, 36)
    left, top, right, bottom = padded.getchannel("A").getbbox()
    assert left >= 6 and top >= 6 and right <= 42 and bottom <= 42


def test_tray_icon_color_setting(monkeypatch):
    from diskatlas.tray import icon

    assert icon.resolve_color("dark") == "dark" and icon.resolve_color("light") == "light"
    monkeypatch.setattr(icon, "taskbar_is_light", lambda: True)
    assert icon.resolve_color("auto") == "dark"
    assert settings.validate({"tray_icon_color": "dark"}) == {"tray_icon_color": "dark"}
    with pytest.raises(ValueError, match="Farbe des Symbols"):
        settings.validate({"tray_icon_color": "lila"})
