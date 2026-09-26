"""Symbol im Infobereich: einfarbige Glyphe (hell/dunkel) mit farbigem Statuspunkt.

Die Glyphen-Masken erzeugt scripts/build_icons.py in allen benötigten Größen, damit das Symbol
nicht vom System hoch- oder heruntergerechnet wird.
"""

from __future__ import annotations

import sys
from pathlib import Path

GLYPH_DIR = Path(__file__).resolve().parent / "icons"
GLYPH_SIZES = (16, 20, 24, 32, 40, 48, 64)
COLOR_CHOICES = ("auto", "light", "dark")
# Glyphenfarbe: hell für dunkle Leisten, dunkel für helle Leisten (wie die Systemsymbole).
# Linux: Farbton der Cinnamon-Symbole (#e1e1e1); Windows-Symbole sind auf dunkler Leiste weiß.
GLYPH_COLORS = {
    "light": (255, 255, 255) if sys.platform == "win32" else (225, 225, 225),
    "dark": (32, 32, 32),
}
DOT_COLORS = {
    "online": (34, 197, 94),
    "starting": (245, 158, 11),
    "unconfigured": (245, 158, 11),
}
DEFAULT_DOT = (239, 68, 68)
LINUX_SIZE = 48  # die Leiste skaliert selbst; 48 = doppelte Größe der üblichen 24 px

_WIN_THEME_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"


def taskbar_is_light() -> bool:
    """Windows: helle Taskleiste? Linux: meist dunkle Leisten (Mint), daher False."""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_THEME_KEY) as key:
            return winreg.QueryValueEx(key, "SystemUsesLightTheme")[0] == 1
    except OSError:
        return False


def resolve_color(setting: str) -> str:
    """Einstellung (auto/light/dark) → tatsächliche Glyphenfarbe."""
    if setting in GLYPH_COLORS:
        return setting
    return "dark" if taskbar_is_light() else "light"


def glyph_mask(size: int):
    """Graustufen-Maske der Glyphe in genau dieser Größe (sonst aus der nächstgrößeren)."""
    from PIL import Image

    source = next((s for s in GLYPH_SIZES if s >= size), GLYPH_SIZES[-1])
    with Image.open(GLYPH_DIR / f"glyph-{source}.png") as image:
        mask = image.convert("L")
    if source != size:
        mask = mask.resize((size, size), Image.Resampling.LANCZOS)
    return mask


def _circle(size: int, cx: float, cy: float, radius: float):
    """Kantenglatte Kreismaske (vierfach gezeichnet, dann verkleinert)."""
    from PIL import Image, ImageDraw

    factor = 4
    big = Image.new("L", (size * factor, size * factor), 0)
    ImageDraw.Draw(big).ellipse(
        [(cx - radius) * factor, (cy - radius) * factor,
         (cx + radius) * factor, (cy + radius) * factor],
        fill=255,
    )
    return big.resize((size, size), Image.Resampling.LANCZOS)


def render_icon(state: str, color: str = "light", size: int = LINUX_SIZE):
    """Glyphe in der Glyphenfarbe, Statuspunkt unten rechts (wie Mint- und Windows-Symbole)
    mit freigestelltem Rand."""
    from PIL import Image, ImageChops

    radius = size * 0.17
    cx = cy = size - radius
    gap = max(1.0, size * 0.06)
    glyph = ImageChops.subtract(glyph_mask(size), _circle(size, cx, cy, radius + gap))

    image = Image.new("RGBA", (size, size), GLYPH_COLORS.get(color, GLYPH_COLORS["light"]) + (0,))
    image.putalpha(glyph)
    dot = Image.new("RGBA", (size, size), DOT_COLORS.get(state, DEFAULT_DOT) + (0,))
    dot.putalpha(_circle(size, cx, cy, radius))
    return Image.alpha_composite(image, dot)


# ---------------------------------------------------------------------------- Windows
def enable_dpi_awareness() -> None:
    """Windows: echte Pixelgrößen statt vom System hochskalierter (unscharfer) Symbole."""
    if sys.platform != "win32":
        return
    import contextlib
    import ctypes

    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # Per-Monitor v2
    except (AttributeError, OSError):
        with contextlib.suppress(AttributeError, OSError):
            ctypes.windll.shcore.SetProcessDpiAwareness(2)


def tray_size() -> int:
    """Größe, in der das System das Symbol zeigt (Windows: 16/20/24/32 je nach Skalierung)."""
    if sys.platform != "win32":
        return LINUX_SIZE
    import ctypes

    try:
        size = int(ctypes.windll.user32.GetSystemMetrics(49))  # SM_CXSMICON
    except (AttributeError, OSError):
        size = 0
    return size if 12 <= size <= 128 else 16


def patch_pystray_win32() -> None:
    """pystray lädt das Symbol in Standard-Symbolgröße, Windows verkleinert es dann (unscharf).

    Stattdessen genau die Bildgröße als einziges Bild der ICO-Datei laden.
    """
    if sys.platform != "win32":
        return
    import os
    import tempfile

    try:
        from pystray import _win32
        from pystray._util import win32
    except ImportError:
        return
    if not hasattr(_win32.Icon, "_assert_icon_handle"):
        return

    def _assert_icon_handle(self):
        if self._icon_handle:
            return
        size = self.icon.width
        fd, path = tempfile.mkstemp(suffix=".ico")
        os.close(fd)
        try:
            self.icon.save(path, format="ICO", sizes=[(size, size)])
            self._icon_handle = win32.LoadImage(
                None, path, win32.IMAGE_ICON, size, size, win32.LR_LOADFROMFILE
            )
        finally:
            os.unlink(path)

    _win32.Icon._assert_icon_handle = _assert_icon_handle
