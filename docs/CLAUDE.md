# Hinweise für KI-Assistenten

- Projektbeschreibung: `PROJECT.md` (lebendes Dokument), Änderungen: `docs/CHANGELOG.md`,
  Reihenfolge der Arbeit: `docs/ROADMAP.md`.
- **Bei jeder Verhaltensänderung** im selben Schritt `docs/CHANGELOG.md` unter `[Unreleased]` ergänzen
  und `PROJECT.md` gemäß §10 „Pflegeregeln“ aktualisieren.
- **Roadmap:** Wird ein Punkt aus `docs/ROADMAP.md` erledigt, dort abhaken (mit Version/PR), neue
  Wünsche des Betreibers dort einsortieren. Die Reihenfolge ändert nur der Betreiber.
- **Sitzungsende:** Mit `/SessionEnde` (`.claude/commands/SessionEnde.md`) werden Memory,
  `docs/CHANGELOG.md`, `docs/ROADMAP.md`, `PROJECT.md` und diese Datei auf den neuesten Stand gebracht.
- Version nur über `python scripts/bump_version.py` ändern.
- Modelländerung (`src/diskatlas/db/models.py`) immer mit Alembic-Migration (Anleitung in PROJECT.md §9).
- Vor Abschluss: `.venv/bin/ruff check src tests` und `.venv/bin/pytest` müssen grün sein.
- Sprache: Oberfläche, Doku und Kommentare deutsch; Bezeichner im Code englisch.
  Ausnahmen (für internationale Leser und die SignPath-Prüfung): `README.md` und
  `docs/CODE_SIGNING.md` englisch; `README.de.md` ist die deutsche Fassung und wird bei jeder
  README-Änderung mitgepflegt.

## Fallen (aus der Praxis)

- **SQLite hat einen Schreiber zur Zeit**, auf Unraid (`/mnt/user`, FUSE) besonders langsam: In
  Anfragen nie viele Zeilen in einer Transaktion löschen oder schreiben – große Aufräumarbeiten
  gehören in `services/maintenance.py` (Hintergrund, kleine Häppchen mit Pausen).
- **Ablage-Ordner** (Relay, Anhänge, Import) immer aus `db.url` ableiten, nie aus `Config()` –
  sonst fassen Tests das echte `~/.local/share/diskatlas` an.
- **Tests:** große Daten nie als `pytest.mark.parametrize`-Werte (pytest schreibt sie in jeden
  Bericht; unter Windows hing die CI daran).
- **Shell:** kein `pkill -f <muster>`, wenn das Muster in der eigenen Befehlszeile steht (beendet
  die eigene Shell); Prozesse per Port (`ss -ltnp`) oder PID beenden.
