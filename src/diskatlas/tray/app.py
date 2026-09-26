"""Tray-Programm des Agenten: Symbol im Systembereich, Menü, Einstellungsfenster."""

from __future__ import annotations

import argparse
import contextlib
import logging
import logging.handlers
import os
import socket
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from diskatlas import __version__
from diskatlas.config import default_config_path, default_data_dir
from diskatlas.tray.controller import AgentController, resolve_config_path
from diskatlas.tray.status import TrayStatus, write_status

log = logging.getLogger("diskatlas.tray")

INSTANCE_PORT = 48653
REFRESH_SECONDS = 2.0
TRAY_LOG = "agent-tray.log"
SETTINGS_LOG = "agent-settings.log"
DOT_COLORS = {
    "online": (34, 197, 94),
    "starting": (245, 158, 11),
    "unconfigured": (245, 158, 11),
}
DEFAULT_DOT = (239, 68, 68)


class SingleInstance:
    """Verhindert einen zweiten Tray-Prozess (belegt einen lokalen Port bis zum Prozessende)."""

    def __init__(self, port: int = INSTANCE_PORT):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self._sock.bind(("127.0.0.1", port))
            self.acquired = True
        except OSError:
            self.acquired = False
            self._sock.close()


def config_mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def render_icon(state: str, size: int = 64):
    """Programmsymbol mit farbigem Statuspunkt (grün verbunden, gelb wartend, rot gestört)."""
    from PIL import Image, ImageDraw

    logo = Path(__file__).resolve().parent.parent / "web" / "static" / "icon_256.png"
    if logo.is_file():
        image = Image.open(logo).convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    else:
        image = Image.new("RGBA", (size, size), (60, 70, 90, 255))
    radius = size * 0.22
    cx = cy = size - radius - 1
    draw = ImageDraw.Draw(image)
    draw.ellipse((cx - radius - 2, cy - radius - 2, cx + radius + 2, cy + radius + 2),
                 fill=(255, 255, 255, 255))
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
                 fill=DOT_COLORS.get(state, DEFAULT_DOT) + (255,))
    return image


def tooltip(status: TrayStatus) -> str:
    text = f"DiskAtlas Agent – {status.label}"
    return text[:120]


def settings_command(config_path: Path) -> list[str]:
    """Kommando, das das Einstellungsfenster als eigenen Prozess startet."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--settings", "-c", str(config_path)]
    return [sys.executable, "-m", "diskatlas.tray.app", "--settings", "-c", str(config_path)]


class Tray:
    def __init__(self, controller: AgentController):
        self.controller = controller
        self._window: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._icon = None
        self._shown_state = ""
        self._config_stamp = config_mtime(controller.config_path)

    # ---------------------------------------------------------------- Menü
    def open_settings(self, *_args) -> None:
        if self._window is not None and self._window.poll() is None:
            return  # Fenster ist schon offen
        try:
            self._window = subprocess.Popen(settings_command(self.controller.config_path))
        except OSError:
            log.exception("Einstellungsfenster konnte nicht gestartet werden")

    def open_dashboard(self, *_args) -> None:
        url = self.controller.status().server_url
        if url:
            webbrowser.open(url)

    def open_logs(self, *_args) -> None:
        open_folder(default_data_dir())

    def quit(self, *_args) -> None:
        self._stop.set()
        if self._icon is not None:
            self._icon.stop()

    def _menu(self):
        import pystray

        return pystray.Menu(
            pystray.MenuItem(lambda _i: self.controller.status().label, None, enabled=False),
            pystray.MenuItem("Einstellungen …", self.open_settings, default=True),
            pystray.MenuItem(
                "Dashboard öffnen", self.open_dashboard,
                enabled=lambda _i: bool(self.controller.status().server_url),
            ),
            pystray.MenuItem("Protokolle anzeigen", self.open_logs),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Beenden", self.quit),
        )

    # ---------------------------------------------------------------- Hintergrund
    def _check_window(self) -> None:
        """Meldet, wenn das Einstellungsfenster mit Fehler endet (sonst sieht man nichts)."""
        if self._window is None or self._window.poll() is None:
            return
        code, self._window = self._window.returncode, None
        if code != 0 and self._icon is not None:
            log.error("Einstellungsfenster mit Code %s beendet", code)
            with contextlib.suppress(Exception):
                self._icon.notify(
                    f"Das Einstellungsfenster ließ sich nicht öffnen. Details: {SETTINGS_LOG} "
                    "(Menü → Protokolle anzeigen).",
                    "DiskAtlas Agent",
                )

    def _tick(self) -> None:
        self._check_window()
        stamp = config_mtime(self.controller.config_path)
        if stamp != self._config_stamp:
            self._config_stamp = stamp
            self.controller.restart()
        status = self.controller.status()
        try:
            write_status(status)
        except OSError:
            log.exception("Statusdatei nicht schreibbar")
        if self._icon is not None and status.state != self._shown_state:
            self._shown_state = status.state
            self._icon.icon = render_icon(status.state)
            self._icon.title = tooltip(status)
            self._icon.update_menu()

    def _loop(self, icon) -> None:
        icon.visible = True
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                log.exception("Aktualisierung fehlgeschlagen")
            self._stop.wait(REFRESH_SECONDS)

    def run(self) -> int:
        import pystray

        self.controller.start()
        status = self.controller.status()
        self._icon = pystray.Icon(
            "diskatlas-agent", render_icon(status.state), tooltip(status), self._menu()
        )
        if status.state == "unconfigured":
            self.open_settings()  # Erststart: gleich zur Einrichtung führen
        try:
            self._icon.run(setup=lambda icon: threading.Thread(
                target=self._loop, args=(icon,), name="tray-refresh", daemon=True
            ).start())
        finally:
            self._stop.set()
            self.controller.stop()
            with contextlib.suppress(OSError):
                write_status(self.controller.status())
            if self._window is not None and self._window.poll() is None:
                self._window.terminate()
        return 0


def open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform == "win32":
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        log.exception("Ordner %s lässt sich nicht öffnen", path)


def setup_logging(verbose: bool, log_name: str) -> None:
    log_dir = default_data_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.handlers.RotatingFileHandler(
        log_dir / log_name, maxBytes=512_000, backupCount=2, encoding="utf-8"
    )]
    if sys.stderr is not None:  # Windows-Programm ohne Konsole hat keinen stderr
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers, force=True,
    )
    # httpx meldet sonst jede Anfrage (alle paar Sekunden), Alembic jeden Start.
    for name in ("httpx", "alembic"):
        logging.getLogger(name).setLevel(logging.DEBUG if verbose else logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.INFO)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="diskatlas-tray",
        description="DiskAtlas-Agent mit Symbol im Systembereich und Einstellungsfenster.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "-c", "--config", help=f"Konfigurationsdatei (Standard: {default_config_path()})"
    )
    parser.add_argument("--settings", action="store_true",
                        help="nur das Einstellungsfenster öffnen (kein Tray, kein Agent)")
    parser.add_argument("-v", "--verbose", action="store_true", help="ausführliche Ausgabe")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose, SETTINGS_LOG if args.settings else TRAY_LOG)
    config_path = resolve_config_path(args.config)
    log.info("DiskAtlas %s (%s), Konfiguration %s", __version__,
             "Einstellungsfenster" if args.settings else "Tray", config_path)
    if args.settings:
        from diskatlas.tray.window import run_settings_window

        try:
            return run_settings_window(config_path)
        except Exception:
            log.exception("Einstellungsfenster fehlgeschlagen")
            return 3
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        log.error("pystray/Pillow fehlen. Installation: pip install -e \".[tray]\"")
        return 2
    instance = SingleInstance()
    if not instance.acquired:
        log.error("Der DiskAtlas-Agent läuft bereits (Symbol im Systembereich).")
        return 1
    return Tray(AgentController(config_path)).run()


if __name__ == "__main__":
    raise SystemExit(main())
