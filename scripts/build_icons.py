"""Erzeugt alle Programmsymbole aus der Glyphe packaging/icons/hard-disk.svg.

Aufruf (braucht Inkscape zum Rendern und Pillow):  python scripts/build_icons.py
Die Ergebnisse werden eingecheckt; Build und Programm brauchen Inkscape nicht.

Zwei Familien:
- App-Symbol: weiße Glyphe auf abgerundetem Quadrat in der Akzentfarbe (Browser, Windows-Exe
  und -Fenster, iPhone, Web-App). Für ≤ 32 px eine Fassung mit größerer Glyphe.
- Tray-Glyphe: nur die Glyphe als Graustufen-Maske in den Größen, die Windows und Linux im
  Infobereich brauchen. Farbe (hell/dunkel) und Statuspunkt setzt das Programm zur Laufzeit.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "packaging" / "icons" / "hard-disk.svg"
STATIC = ROOT / "src" / "diskatlas" / "web" / "static"
TRAY = ROOT / "src" / "diskatlas" / "tray" / "icons"

ACCENT = "#3b6cf6"  # --accent aus style.css
GLYPH_UNITS = 14.0  # viewBox der Glyphe
TRAY_SIZES = (16, 20, 24, 32, 40, 48, 64)
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def glyph_path() -> str:
    """Pfad-Element der Glyphe (ohne Farbe) als SVG-Text."""
    ns = {"svg": "http://www.w3.org/2000/svg"}
    path = ET.parse(SOURCE).getroot().find("svg:path", ns)
    return (f'<path d="{path.get("d")}" fill-rule="{path.get("fill-rule", "nonzero")}" '
            f'clip-rule="{path.get("clip-rule", "nonzero")}"/>')


def app_svg(*, rounded: bool = True, glyph: float = 0.6, small: bool = False) -> str:
    """App-Symbol auf 256er-Raster; `glyph` = Anteil der Glyphenhöhe an der Kantenlänge."""
    size = 256.0
    if small:
        glyph = 0.74
    radius = size * (0.2 if small else 0.225) if rounded else 0
    scale = glyph * size / GLYPH_UNITS
    offset = (size - GLYPH_UNITS * scale) / 2
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size:g} {size:g}">'
        f'<rect width="{size:g}" height="{size:g}" rx="{radius:g}" fill="{ACCENT}"/>'
        f'<g fill="#fff" transform="translate({offset:g} {offset:g}) scale({scale:g})">'
        f"{glyph_path()}</g></svg>\n"
    )


def tray_svg() -> str:
    """Glyphe mit 1/16 Rand: bei 16 px entspricht eine Glypheneinheit genau einem Pixel."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
        f'<g fill="#000" transform="translate(1 1)">{glyph_path()}</g></svg>\n'
    )


def render(svg: str, size: int, target: Path) -> Path:
    with tempfile.NamedTemporaryFile("w", suffix=".svg", delete=False) as tmp:
        tmp.write(svg)
    try:
        subprocess.run(
            ["inkscape", tmp.name, "--export-type=png", f"--export-filename={target}",
             f"--export-width={size}", f"--export-height={size}"],
            check=True, capture_output=True,
        )
    finally:
        Path(tmp.name).unlink()
    return target


def main() -> int:
    if not shutil.which("inkscape"):
        print("Inkscape fehlt (sudo apt install inkscape).", file=sys.stderr)
        return 2
    TRAY.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)

        # Browser: Vektor-Favicon (kleine Fassung) plus PNG für Browser ohne SVG-Favicons.
        (STATIC / "favicon.svg").write_text(app_svg(small=True), encoding="utf-8")
        render(app_svg(small=True), 32, STATIC / "favicon-32.png")

        # Web-App/Android: normal und „maskable“ (randlos, Motiv in der sicheren Zone).
        render(app_svg(), 192, STATIC / "icon-192.png")
        render(app_svg(), 512, STATIC / "icon-512.png")
        render(app_svg(rounded=False, glyph=0.5), 512, STATIC / "icon-maskable-512.png")
        # iPhone: randlos und deckend, iOS rundet selbst ab.
        render(app_svg(rounded=False), 180, STATIC / "apple-touch-icon.png")
        # Fenster unter Linux, Autostart-Eintrag.
        render(app_svg(), 256, STATIC / "icon_256.png")

        # Windows: ICO mit jeder Größe einzeln gerendert (kleine Größen mit größerer Glyphe).
        frames = [
            Image.open(render(app_svg(small=size <= 32), size, tmp / f"ico-{size}.png"))
            for size in ICO_SIZES
        ]
        largest = frames[-1]
        largest.save(STATIC / "favicon.ico", format="ICO",
                     sizes=[f.size for f in frames], append_images=frames[:-1])

        # Tray: nur der Alphakanal zählt.
        for size in TRAY_SIZES:
            png = render(tray_svg(), size, tmp / f"tray-{size}.png")
            Image.open(png).getchannel("A").save(TRAY / f"glyph-{size}.png", optimize=True)

    print("Symbole erzeugt in", STATIC.relative_to(ROOT), "und", TRAY.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
