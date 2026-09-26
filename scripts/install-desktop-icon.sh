#!/usr/bin/env bash
# Richtet den DiskAtlas-Menüeintrag/Launcher unter Linux ein (Cinnamon/GNOME/KDE).
# Voraussetzung: `pip install -e ".[gui]"` wurde bereits ausgeführt.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"
APPS_DIR="$HOME/.local/share/applications"
mkdir -p "$ICON_DIR" "$APPS_DIR"
install -m 0644 "$ROOT/packaging/icons/icon_256.png" "$ICON_DIR/diskatlas.png"

EXE="$ROOT/.venv/bin/diskatlas-gui"
if [[ ! -x "$EXE" ]]; then
  echo "Nicht gefunden: $EXE – bitte zuerst 'pip install -e \".[gui]\"' in der venv ausführen." >&2
  exit 1
fi

sed "s|^Exec=diskatlas-gui|Exec=$EXE|" "$ROOT/deploy/linux/diskatlas.desktop" \
  > "$APPS_DIR/diskatlas.desktop"
update-desktop-database "$APPS_DIR" 2>/dev/null || true
echo "Fertig. DiskAtlas erscheint im Anwendungsmenü (ggf. abmelden/anmelden nötig)."
