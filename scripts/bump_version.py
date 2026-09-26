#!/usr/bin/env python3
"""Erhöht die Version und schreibt den [Unreleased]-Abschnitt im CHANGELOG fest.

Aufruf:  python scripts/bump_version.py patch|minor|major|X.Y.Z [--dry-run]
Danach:  git commit -am "Release vX.Y.Z" && git tag vX.Y.Z
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "src" / "diskatlas" / "__init__.py"
CHANGELOG = ROOT / "CHANGELOG.md"
VERSION_RE = re.compile(r'^__version__ = "(\d+)\.(\d+)\.(\d+)"$', re.M)
UNRELEASED = "## [Unreleased]"


def next_version(current: tuple[int, int, int], part: str) -> str:
    major, minor, patch = current
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    if re.fullmatch(r"\d+\.\d+\.\d+", part):
        return part
    raise SystemExit(f"Ungültige Angabe: {part}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("part", help="patch, minor, major oder eine konkrete Version")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    init_text = INIT.read_text(encoding="utf-8")
    match = VERSION_RE.search(init_text)
    if not match:
        raise SystemExit(f"__version__ nicht gefunden in {INIT}")
    old = ".".join(match.groups())
    new = next_version(tuple(int(x) for x in match.groups()), args.part)

    changelog = CHANGELOG.read_text(encoding="utf-8")
    if UNRELEASED not in changelog:
        raise SystemExit("CHANGELOG.md enthält keinen Abschnitt '## [Unreleased]'")
    head, rest = changelog.split(UNRELEASED, 1)
    body, sep, tail = rest.partition("\n## [")
    if not body.strip():
        raise SystemExit("[Unreleased] ist leer – erst Änderungen im CHANGELOG eintragen.")
    new_changelog = (
        f"{head}{UNRELEASED}\n\n## [{new}] - {date.today().isoformat()}{body.rstrip()}\n"
        + (f"\n## [{tail}" if sep else "")
    )

    print(f"Version: {old} -> {new}")
    if args.dry_run:
        return 0
    INIT.write_text(VERSION_RE.sub(f'__version__ = "{new}"', init_text), encoding="utf-8")
    CHANGELOG.write_text(new_changelog, encoding="utf-8")
    print(f'Nächste Schritte:\n  git commit -am "Release v{new}"\n  git tag v{new}')
    return 0


if __name__ == "__main__":
    sys.exit(main())
