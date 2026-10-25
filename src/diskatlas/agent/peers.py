"""Gemerkte Schlüssel der Zielrechner („trust on first use“).

Beim ersten Senden an einen Client merkt sich der Agent dessen öffentlichen Schlüssel. Liefert
der Server später einen anderen, sendet der Agent nicht: Entweder wurde der Agent dort neu
eingerichtet (dann im Einstellungsfenster „vergessen“), oder jemand versucht, einen Schlüssel
unterzuschieben. Das schützt gegen einen später manipulierten Server, nicht beim ersten Kontakt;
dafür lässt sich der Fingerabdruck an beiden Rechnern vergleichen.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

from diskatlas.agent.relaycrypto import fingerprint

NEW, KNOWN, CHANGED = "new", "known", "changed"


class PeerStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def load(self) -> dict[str, dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def check(self, client_id: int, pubkey: str, label: str = "") -> str:
        """`new` (gemerkt), `known` (passt) oder `changed` (anderer Schlüssel: nicht senden)."""
        key = str(client_id)
        with self._lock:
            data = self.load()
            entry = data.get(key)
            if entry is None:
                data[key] = {"pubkey": pubkey, "label": label[:120],
                             "since": datetime.now(UTC).strftime("%Y-%m-%d %H:%M")}
                self._save(data)
                return NEW
            if entry.get("pubkey") == pubkey:
                if label and entry.get("label") != label:
                    entry["label"] = label[:120]
                    self._save(data)
                return KNOWN
            return CHANGED

    def forget(self, client_id: str | int) -> bool:
        with self._lock:
            data = self.load()
            if data.pop(str(client_id), None) is None:
                return False
            self._save(data)
            return True

    def listing(self) -> list[dict]:
        return [{"id": key, "label": entry.get("label") or f"Client {key}",
                 "fingerprint": fingerprint(entry.get("pubkey")), "since": entry.get("since", "")}
                for key, entry in sorted(self.load().items(), key=lambda kv: kv[0])]
