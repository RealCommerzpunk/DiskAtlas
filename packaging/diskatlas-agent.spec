# PyInstaller-Bauanleitung für den Tray-Agenten (eine einzelne ausführbare Datei).
# Aufruf im Projektverzeichnis:  pyinstaller --noconfirm packaging/diskatlas-agent.spec
# Ergebnis: dist/DiskAtlas-Agent.exe (Windows) bzw. dist/diskatlas-agent (Linux)
#
# Enthält beide Betriebsarten: Agent für einen DiskAtlas-Server und den lokalen Betrieb mit
# eigener Weboberfläche und Datenbank.
# Windows: liegt build/smartmontools/ vor (packaging/fetch_smartctl.py), wird smartctl.exe samt
# Lizenz mitgeliefert.
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
PKG = ROOT / "src" / "diskatlas"
IS_WIN = sys.platform == "win32"
SMARTMONTOOLS = ROOT / "build" / "smartmontools"

hiddenimports = [
    "diskatlas.tray.window",
    # uvicorn lädt diese Module per Name (lokaler Betrieb).
    "uvicorn.logging",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.lifespan.on",
]
hooksconfig = {}
if IS_WIN:
    hiddenimports += ["webview.platforms.winforms", "webview.platforms.edgechromium", "clr"]
else:
    hiddenimports += [
        "webview.platforms.gtk",
        "gi.repository.Gtk",
        "gi.repository.WebKit2",
        "gi.repository.AyatanaAppIndicator3",
    ]
    hooksconfig = {"gi": {
        "module-versions": {"Gtk": "3.0", "WebKit2": "4.1"},
        "icons": [], "themes": [], "languages": [],
    }}

datas = [
    (str(PKG / "web" / "static"), "diskatlas/web/static"),
    (str(PKG / "web" / "templates"), "diskatlas/web/templates"),
    # Alembic liest die Migrationsskripte als Dateien.
    (str(PKG / "db" / "migrations" / "env.py"), "diskatlas/db/migrations"),
    (str(PKG / "db" / "migrations" / "script.py.mako"), "diskatlas/db/migrations"),
    (str(PKG / "db" / "migrations" / "versions" / "*.py"), "diskatlas/db/migrations/versions"),
]
if IS_WIN:
    if (SMARTMONTOOLS / "smartctl.exe").is_file():
        datas.append((str(SMARTMONTOOLS / "*"), "smartmontools"))
    else:
        print("WARNUNG: build/smartmontools fehlt – smartctl.exe wird nicht mitgeliefert")

a = Analysis(
    [str(ROOT / "packaging" / "agent_entry.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=hiddenimports,
    hooksconfig=hooksconfig,
    # Nicht benötigt; würde sonst über optionale Importe bzw. Systempakete mitgezogen.
    excludes=[
        "tkinter", "pytest", "IPython", "numpy", "torch", "scipy", "pandas", "matplotlib",
        "PyQt5", "PyQt6", "PySide2", "PySide6", "pkg_resources", "setuptools",
        "pygments", "rich", "chardet", "cryptography", "OpenSSL", "brotli", "brotlicffi",
        "h2", "socksio", "zstandard", "pydoc_data", "greenlet", "psycopg", "psycopg2",
        "uvloop", "httptools", "websockets", "wsproto", "watchfiles", "dotenv", "yaml",
        "PIL.ImageQt", "PIL.ImageTk", "PIL._avif", "PIL._webp", "PIL._imagingcms",
    ],
    noarchive=False,
)

if not IS_WIN:
    # GTK, AppIndicator und WebKit kommen vom System (auf jedem Linux-Desktop vorhanden).
    # Gemischt aus Paket und System passen die Bibliotheken nicht sicher zusammen, und das
    # Paket würde dreimal so groß. Mitgeliefert wird nur Python selbst.
    # (Die zugehörigen Umgebungsvariablen setzt packaging/agent_entry.py zurück.)
    def _from_system(src):
        return src.startswith(("/usr/lib", "/lib"))

    a.binaries = [
        entry for entry in a.binaries
        if entry[2] != "BINARY" or not _from_system(entry[1])
        or Path(entry[0]).name.startswith("libpython")
    ]
    a.datas = [
        entry for entry in a.datas
        if not entry[0].startswith(("gi_typelibs/", "share/", "lib/gdk-pixbuf", "gio_modules/",
                                    "etc/", "lib/girepository"))
    ]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="DiskAtlas-Agent" if IS_WIN else "diskatlas-agent",
    console=False,
    icon=str(ROOT / "packaging" / "windows" / "app.ico") if IS_WIN else None,
    upx=False,
)
