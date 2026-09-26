"""Erzeugt aus packaging/icons/diskatlas.svg alle Icon-Dateien (PNG für Web/GUI, ICO für Windows).

Benötigt Inkscape und Pillow. Aufruf: python scripts/build_icons.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SVG = ROOT / "packaging" / "icons" / "diskatlas.svg"
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]


def render(size: int, target: Path) -> None:
    subprocess.run(
        ["inkscape", str(SVG), "-o", str(target), "-w", str(size), "-h", str(size)],
        check=True, capture_output=True,
    )


def main() -> int:
    if not shutil.which("inkscape"):
        print("Inkscape nicht gefunden.", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        frames = []
        for size in ICO_SIZES:
            path = Path(tmp) / f"{size}.png"
            render(size, path)
            frames.append(Image.open(path).convert("RGBA"))
        frames[-1].save(
            ROOT / "packaging" / "windows" / "app.ico", format="ICO",
            sizes=[(s, s) for s in ICO_SIZES], append_images=frames[:-1],
        )
    render(256, ROOT / "packaging" / "icons" / "icon_256.png")
    render(256, ROOT / "src" / "diskatlas" / "web" / "static" / "icon_256.png")
    render(180, ROOT / "src" / "diskatlas" / "web" / "static" / "apple-touch-icon.png")
    shutil.copyfile(SVG, ROOT / "src" / "diskatlas" / "web" / "static" / "favicon.svg")
    print("Icons erzeugt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
