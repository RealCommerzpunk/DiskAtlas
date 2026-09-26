#!/usr/bin/env bash
# Erlaubt dem aktuellen Benutzer, smartctl ohne Passwort per sudo auszuführen.
# DiskAtlas ruft dann automatisch "sudo -n smartctl ..." auf. Nur smartctl wird freigegeben.
set -euo pipefail

SMARTCTL="$(command -v smartctl || true)"
if [[ -z "$SMARTCTL" ]]; then
  echo "smartctl nicht gefunden – bitte zuerst installieren: sudo apt install smartmontools" >&2
  exit 1
fi

USER_NAME="${SUDO_USER:-$USER}"
RULE="$USER_NAME ALL=(root) NOPASSWD: $SMARTCTL"
TARGET=/etc/sudoers.d/diskatlas-smartctl

echo "Folgende Regel wird nach $TARGET geschrieben:"
echo "  $RULE"
TMP="$(mktemp)"
echo "$RULE" > "$TMP"
sudo visudo -cf "$TMP"
sudo install -m 0440 -o root -g root "$TMP" "$TARGET"
rm -f "$TMP"
echo "Fertig. Test: sudo -n smartctl -H /dev/sda"
