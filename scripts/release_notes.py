"""Gibt den CHANGELOG-Abschnitt einer Version als Text für das GitHub-Release aus.

Aufruf: python scripts/release_notes.py 0.4.0 > release-notes.md
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
REPO = "https://github.com/RealCommerzpunk/DiskAtlas"

FOOTER = """
---

**Downloads** (unten unter *Assets*):
- `DiskAtlas-Agent-windows-x64.exe` – Agent für Windows 10/11 (inkl. smartctl)
- `diskatlas-agent-linux-x86_64` – Agent für Linux Mint 22 / Ubuntu 24.04 und neuer
- `smartmontools-*.tar.gz` – Quellcode des mitgelieferten smartctl (GPL)

Server als Docker-Image: `ghcr.io/realcommerzpunk/diskatlas:{version}`.
Anleitungen: [Agent]({repo}/blob/main/docs/AGENT.md) ·
[Unraid/Docker]({repo}/blob/main/docs/UNRAID.md)
"""


def section(version: str, text: str) -> str:
    match = re.search(
        rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)", text, re.S | re.M
    )
    if not match:
        raise SystemExit(f"Version {version} nicht im CHANGELOG gefunden")
    return match.group(1).strip()


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Aufruf: release_notes.py <version>")
    version = sys.argv[1].removeprefix("v")
    body = section(version, CHANGELOG.read_text(encoding="utf-8"))
    sys.stdout.reconfigure(encoding="utf-8")  # Windows-Konsole: sonst cp1252
    sys.stdout.write(body + "\n" + FOOTER.format(version=version, repo=REPO))
    return 0


if __name__ == "__main__":
    sys.exit(main())
