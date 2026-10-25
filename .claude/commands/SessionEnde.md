---
description: Sitzung abschließen – Fortschritt ins Memory, CHANGELOG, ROADMAP und CLAUDE.md aktualisieren
argument-hint: "[optional: Hinweise, was besonders festgehalten werden soll]"
---

# Sitzung abschließen

Bringe am Ende dieser Sitzung alle Projektdokumente und das Memory auf den aktuellen Stand.
Zusätzliche Hinweise des Betreibers: $ARGUMENTS

Arbeite die Schritte der Reihe nach ab. Ändere **keinen Programmcode**, nur Dokumente und Memory.
Stelle Rückfragen nur, wenn etwas widersprüchlich ist.

## 1. Stand erheben

- `git status`, `git branch --show-current`, `git log --oneline -40` und
  `git describe --tags --abbrev=0` (letztes Release).
- Liste der in dieser Sitzung gemergten PRs: `~/.local/bin/gh pr list --state merged --limit 30`.
- Gibt es **uncommittete Änderungen oder offene PRs**, nenne sie im Abschlussbericht. Nicht
  ungefragt committen oder mergen, was nicht zu den Dokumenten gehört.
- Rufe dir den Verlauf dieser Sitzung in Erinnerung: umgesetzte Funktionen, behobene Fehler,
  Entscheidungen und Wünsche des Betreibers, offene Fragen, Erkenntnisse aus Fehlersuchen.

## 2. `docs/CHANGELOG.md`

- Jede Verhaltensänderung seit dem letzten Release steht unter `## [Unreleased]` in der passenden
  Rubrik (`Hinzugefügt`, `Geändert`, `Behoben`, `Sicherheit`, …). Fehlendes ergänzen, Doppeltes
  zusammenführen, je Rubrik nur eine Überschrift.
- Bereits veröffentlichte Versionsabschnitte nicht umschreiben (höchstens offensichtliche
  Tippfehler).
- Die Version nie von Hand ändern; Releases nur mit `python scripts/bump_version.py`.

## 3. `docs/ROADMAP.md`

- Erledigte Punkte abhaken, mit Version bzw. PR.
- Neue Wünsche und Ideen des Betreibers aus dieser Sitzung einsortieren: an der passenden Stelle,
  mit Ziel, Vorschlag zur Umsetzung, „Fertig, wenn“ und offenen Fragen samt Vorschlag. Die
  Reihenfolge ändert nur der Betreiber; eigene Vorschläge als solche kennzeichnen.
- Getroffene Entscheidungen unter „Entschieden (Datum)“ festhalten, beantwortete Fragen entfernen.
- Kopf aktualisieren: *Aktuelle Version*, *Nächster Meilenstein*, *Zuletzt aktualisiert*
  (absolutes Datum), Übersichtstabelle und Tabelle *Erledigt*.

## 4. `PROJECT.md`

- Gemäß §10 „Pflegeregeln“: Funktionsumfang (§2), Datenmodell (§4), neue Entscheidungen (§5,
  mit Datum anhängen), Konfiguration (§7), Kopfzeile (Version, Status, Datum).

## 5. `docs/CLAUDE.md`

- Nur **dauerhafte Arbeitsregeln** ergänzen oder korrigieren, die in dieser Sitzung neu entstanden
  sind (z. B. ein Vorgehen, das der Betreiber verlangt hat, oder eine Falle, die künftig jede
  Sitzung kennen muss). Kein Sitzungstagebuch, keine Wiederholung dessen, was im Code oder in
  PROJECT.md steht. Kurz halten.
- `CLAUDE.md` im Hauptordner bleibt der Einzeiler `@docs/CLAUDE.md`.

## 6. Memory

Aktualisiere das persönliche Memory (Verzeichnis und Regeln: Abschnitt „Memory“ im Systemprompt).

- **Projektstand:** die bestehende Projekt-Datei um einen Eintrag „Stand JJJJ-MM-TT“ ergänzen bzw.
  veraltete Aussagen darin korrigieren: aktuelle Version, was in `main` ist und noch nicht
  released, laufende Arbeit, offene Punkte für die nächste Sitzung. Absolute Daten verwenden.
- **Rückmeldungen und Vorlieben** des Betreibers (wie er arbeiten möchte, was er korrigiert oder
  ausdrücklich gelobt hat) als eigene `feedback`- bzw. `user`-Einträge – mit **Why:** und
  **How to apply:**.
- **Erkenntnisse aus Fehlersuchen**, die nicht aus dem Code hervorgehen (z. B. Eigenheiten der
  Umgebung beim Betreiber).
- Vorher prüfen, ob ein Eintrag schon existiert, und diesen aktualisieren statt zu doppeln;
  falsch gewordene Einträge löschen. Nichts speichern, was Repo oder Git-Verlauf ohnehin festhalten.
- `MEMORY.md` (Index) anpassen: eine Zeile je Eintrag, Kurzbeschreibungen auf dem neuesten Stand.

## 7. Prüfen und einreichen

- Relative Links in den geänderten Markdown-Dateien prüfen (Ziele müssen existieren).
- Haben sich nur Dokumente geändert, sind Tests nicht nötig; wurde in der Sitzung Code geändert,
  müssen `.venv/bin/ruff check src tests` und `.venv/bin/pytest` grün sein.
- Dokumentänderungen auf einem Branch `docs/sitzung-JJJJ-MM-TT` committen (Commit-Nachricht auf
  Deutsch, mit der Attributionszeile aus dem System-Hinweis), Pull Request anlegen, auf grüne CI
  warten und mergen – so wie im Projekt üblich. Kein Release bauen.

## 8. Abschlussbericht (kurz, auf Deutsch)

- Was in CHANGELOG, ROADMAP, PROJECT.md, CLAUDE.md und Memory aktualisiert wurde (je ein Satz).
- Offene Punkte für die nächste Sitzung: Aufgaben des Betreibers, offene Fragen, nicht gemergte
  Arbeit, ob ein Release aussteht.
