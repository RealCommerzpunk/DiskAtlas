"""Holt smartctl.exe (smartmontools) für das Windows-Paket des Agenten.

Lädt den offiziellen Windows-Installer und den Quellcode der gleichen Version von GitHub,
prüft beide per SHA-256 und legt ab:

    build/smartmontools/smartctl.exe   64-Bit-Programm aus dem Installer
    build/smartmontools/COPYING.txt    GNU GPL v2 (Lizenz von smartmontools)
    build/smartmontools/LIESMICH.txt   Herkunft, Version, Bezug des Quellcodes
    build/smartmontools-<v>.tar.gz     Quellcode – wird neben der .exe veröffentlicht (GPL §3a)

Braucht 7-Zip (`7z`) zum Entpacken des Installers. Aufruf: python packaging/fetch_smartctl.py
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

VERSION = "7.5"
TAG = "RELEASE_" + VERSION.replace(".", "_")
BASE = f"https://github.com/smartmontools/smartmontools/releases/download/{TAG}"
INSTALLER = f"smartmontools-{VERSION}.win32-setup.exe"
SOURCE = f"smartmontools-{VERSION}.tar.gz"
SHA256 = {
    INSTALLER: "896337fcc253220614cf8cdbd5cf2321c5aa326a37a04160a672a281e6104c70",
    SOURCE: "690b83ca331378da9ea0d9d61008c4b22dde391387b9bbad7f29387f2595f76e",
}

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "build" / "smartmontools"

NOTICE = f"""smartctl.exe – smartmontools {VERSION}
====================================

Der DiskAtlas-Agent liefert unter Windows das Programm smartctl.exe aus dem Projekt
smartmontools mit, um SMART-Werte der Festplatten auszulesen. Es ist ein eigenständiges
Programm, das der Agent nur aufruft; es wurde unverändert aus dem offiziellen
Windows-Installer übernommen ({INSTALLER}, 64-Bit-Fassung).

Lizenz: GNU General Public License, Version 2 oder später (siehe COPYING.txt).
Urheber: Bruce Allen, Christian Franke und weitere Autoren von smartmontools.

Quellcode: Die Datei {SOURCE} liegt bei jeder DiskAtlas-Version auf derselben
Download-Seite wie das Agent-Programm (GitHub → RealCommerzpunk/DiskAtlas → Releases).
Außerdem beim Projekt selbst: https://www.smartmontools.org/ bzw.
{BASE}/{SOURCE}
"""


def download(name: str, target: Path) -> Path:
    path = target / name
    print(f"Lade {name} …")
    with urllib.request.urlopen(f"{BASE}/{name}", timeout=120) as response:
        path.write_bytes(response.read())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != SHA256[name]:
        raise SystemExit(f"Prüfsumme von {name} stimmt nicht: {digest}")
    return path


def seven_zip() -> str:
    for candidate in ("7z", r"C:\Program Files\7-Zip\7z.exe"):
        found = shutil.which(candidate)
        if found:
            return found
    raise SystemExit("7-Zip (7z) nicht gefunden – wird zum Entpacken des Installers gebraucht.")


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        installer = download(INSTALLER, tmp_path)
        subprocess.run(
            [seven_zip(), "x", "-y", f"-o{tmp_path / 'x'}", str(installer),
             "bin/smartctl.exe", "doc/COPYING.txt"],
            check=True, stdout=subprocess.DEVNULL,
        )
        shutil.copy2(tmp_path / "x" / "bin" / "smartctl.exe", OUT / "smartctl.exe")
        shutil.copy2(tmp_path / "x" / "doc" / "COPYING.txt", OUT / "COPYING.txt")
        source = download(SOURCE, tmp_path)
        shutil.copy2(source, OUT.parent / SOURCE)
    (OUT / "LIESMICH.txt").write_text(NOTICE, encoding="utf-8")
    print(f"Fertig: {OUT} und {OUT.parent / SOURCE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
