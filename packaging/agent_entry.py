"""Einstiegspunkt für die ausführbare Datei des Tray-Agenten (PyInstaller)."""

import os
import sys

if sys.platform.startswith("linux") and getattr(sys, "frozen", False):
    # GTK, AppIndicator und WebKit werden nicht mitgeliefert (siehe diskatlas-agent.spec).
    # PyInstallers Laufzeit-Hooks lenken sie trotzdem ins (leere) Paketverzeichnis um – zurück
    # auf die Systemeinstellungen, bevor GTK geladen wird.
    for _name in ("GI_TYPELIB_PATH", "GTK_DATA_PREFIX", "GTK_EXE_PREFIX", "GTK_PATH",
                  "PANGO_LIBDIR", "PANGO_SYSCONFDIR", "GIO_MODULE_DIR", "GDK_PIXBUF_MODULE_FILE"):
        os.environ.pop(_name, None)
    _bundled = os.path.join(sys._MEIPASS, "share")
    _dirs = [d for d in os.environ.get("XDG_DATA_DIRS", "").split(os.pathsep) if d != _bundled]
    os.environ["XDG_DATA_DIRS"] = os.pathsep.join(_dirs) or "/usr/local/share:/usr/share"

from diskatlas.tray.app import main  # noqa: E402

raise SystemExit(main())
