# PyInstaller-Bauanleitung für den Tray-Agenten (eine einzelne ausführbare Datei).
# Aufruf im Projektverzeichnis:  pyinstaller --noconfirm packaging/diskatlas-agent.spec
# Ergebnis: dist/DiskAtlas-Agent.exe (Windows) bzw. dist/diskatlas-agent (Linux)
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
STATIC = ROOT / "src" / "diskatlas" / "web" / "static"
IS_WIN = sys.platform == "win32"

hiddenimports = ["diskatlas.tray.window"]
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
    # Nur das Standard-Thema mitnehmen, sonst landen alle installierten Symbol-Themes im Paket.
    hooksconfig = {"gi": {
        "module-versions": {"Gtk": "3.0", "WebKit2": "4.1"},
        "icons": ["Adwaita", "hicolor"],
        "themes": ["Adwaita"],
        "languages": ["de", "en"],
    }}

a = Analysis(
    [str(ROOT / "packaging" / "agent_entry.py")],
    pathex=[str(ROOT / "src")],
    datas=[(str(STATIC / "icon_256.png"), "diskatlas/web/static")],
    hiddenimports=hiddenimports,
    hooksconfig=hooksconfig,
    # Server-Teile braucht der Agent nicht (er sendet nur per HTTP).
    # (numpy/torch & Co. würden sonst über Systempakete mitgezogen.)
    excludes=[
        "uvicorn", "tkinter", "pytest", "IPython", "numpy", "torch", "scipy", "pandas",
        "matplotlib", "PyQt5", "PyQt6", "PySide2", "PySide6", "pkg_resources", "setuptools",
    ],
    noarchive=False,
)
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
