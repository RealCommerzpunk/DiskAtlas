# Hinweise für KI-Assistenten

- Projektbeschreibung: `PROJECT.md` (lebendes Dokument), Änderungen: `CHANGELOG.md`.
- **Bei jeder Verhaltensänderung** im selben Schritt `CHANGELOG.md` unter `[Unreleased]` ergänzen
  und `PROJECT.md` gemäß §10 „Pflegeregeln“ aktualisieren.
- Version nur über `python scripts/bump_version.py` ändern.
- Modelländerung (`src/diskatlas/db/models.py`) immer mit Alembic-Migration (Anleitung in PROJECT.md §9).
- Vor Abschluss: `.venv/bin/ruff check src tests` und `.venv/bin/pytest` müssen grün sein.
- Sprache: Oberfläche, Doku und Kommentare deutsch; Bezeichner im Code englisch.
  Ausnahmen (für internationale Leser und die SignPath-Prüfung): `README.md` und
  `docs/CODE_SIGNING.md` englisch; `README.de.md` ist die deutsche Fassung und wird bei jeder
  README-Änderung mitgepflegt.
