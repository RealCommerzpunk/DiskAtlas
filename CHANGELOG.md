# Changelog

Alle nennenswerten Änderungen an DiskAtlas werden in dieser Datei dokumentiert.

Das Format basiert auf [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
die Versionierung folgt [Semantic Versioning](https://semver.org/lang/de/).

**Pflege:** Jede Änderung wird sofort unter `[Unreleased]` in der passenden Rubrik
(`Hinzugefügt`, `Geändert`, `Veraltet`, `Entfernt`, `Behoben`, `Sicherheit`) eingetragen.
Ein Release entsteht mit `python scripts/bump_version.py patch|minor|major`.

## [Unreleased]

### Hinzugefügt
- **Agent als Tray-Programm** (`diskatlas-tray`, als fertige Datei `DiskAtlas-Agent.exe` bzw.
  `diskatlas-agent`): Symbol im Systembereich mit farbigem Statuspunkt (grün verbunden, gelb
  wartend/nicht eingerichtet, rot Server nicht erreichbar oder Token abgelehnt), Menü mit Status,
  *Einstellungen …*, *Dashboard öffnen* und *Beenden*. Das Einstellungsfenster zeigt den
  Verbindungsstatus live (Server, Rechner, letzte Antwort, Anzahl Datenträger) und bearbeitet die
  `[agent]`-Optionen der `config.toml` (Server-Adresse, API-Token, Rechnername, Dateiindex,
  Auto-Einhängen, Intervalle, smartctl). *Verbindung testen* prüft Erreichbarkeit und Token getrennt.
  Nach dem Speichern startet der Agent mit den neuen Werten neu (kein Programmneustart nötig); die
  vorige Datei bleibt als `config.toml.bak` erhalten. Beim ersten Start ohne Server-Adresse öffnet
  sich das Fenster von selbst. Option *Beim Anmelden automatisch starten* (Linux: XDG-Autostart,
  Windows: `HKCU\…\Run`). Nur eine Instanz; Log in `agent-tray.log` im Datenverzeichnis.
- PyInstaller-Bauanleitung `packaging/diskatlas-agent.spec` und GitHub-Actions-Workflow
  `agent.yml`: baut die Programme für Windows (x64) und Linux (x86_64, Ubuntu 24.04/Mint 22) und
  hängt sie bei Versions-Tags an das Release.
- Neue optionale Abhängigkeitsgruppe `tray` (pystray, Pillow, pywebview).
- **Lokaler Betrieb im Agent-Programm**: Im Einstellungsfenster wählbar zwischen *Mit
  DiskAtlas-Server verbinden* und *Nur dieser PC*. Lokal startet das Programm Weboberfläche und
  SQLite-Datenbank selbst (`http://127.0.0.1:8765`, *Dashboard öffnen*); Status „Läuft lokal“.
  Ohne Konfigurationsdatei gilt das Programm als nicht eingerichtet und fragt nach der Betriebsart.
- Die Windows-Datei bringt **`smartctl.exe`** (smartmontools 7.5, GPL v2) mit; genutzt, wenn
  kein installiertes smartctl gefunden wird. Lizenztext und Herkunft liegen bei und sind im
  Einstellungsfenster verlinkt, der Quellcode wird bei jedem Release mitveröffentlicht
  (`packaging/fetch_smartctl.py` lädt und prüft beides per SHA-256).
- Einstellungsfenster weist unter Windows darauf hin, wenn SMART mangels Administratorrechten
  nicht lesbar ist; neue Anleitung [docs/AGENT.md](docs/AGENT.md) (Betriebsarten, geplante Aufgabe
  mit Adminrechten, SmartScreen, Protokolle).
- Tray-Menü *Protokolle anzeigen*; das Einstellungsfenster schreibt eigene Protokolle
  (`agent-settings.log`), und das Tray meldet per Benachrichtigung, wenn es sich nicht öffnen lässt.
- **MIT-Lizenz** (`LICENSE`); Windows-Datei mit Metadaten (Produkt, Version, Copyright).
- Signatur der Windows-Datei über **SignPath** vorbereitet: Schritt im Build (nur Versions-Tags,
  aktiv sobald die Zugangsdaten hinterlegt sind), Richtlinie [docs/CODE_SIGNING.md](docs/CODE_SIGNING.md),
  Artefakt-Konfiguration `packaging/signpath/`. Anleitung zum Entfernen in docs/AGENT.md.
- Build: Starttest unter Windows (lokale Oberfläche muss antworten, Einstellungsfenster offen
  bleiben; Protokolle im Build-Log, Bildschirmfoto als Artefakt).

### Behoben
- Lokale Weboberfläche startete im Windows-Programm ohne Konsole nicht (uvicorn richtete eigenes
  Logging auf das fehlende `stdout` ein); sie protokolliert jetzt in die Datei des Programms.
- Windows: Einstellungsfenster (und `diskatlas-gui`) stürzten beim Öffnen ab, weil WinForms als
  Fenstersymbol nur `.ico` akzeptiert (.NET-Fehler `0xE0434352` ohne Meldung). Das Symbol wird
  jetzt einmalig als `.ico` erzeugt.

### Geändert
- **Neues Programmsymbol** (Festplatten-Glyphe), alle Dateien aus einer Quelle
  `packaging/icons/hard-disk.svg` per `scripts/build_icons.py` (Inkscape) erzeugt: App-Symbol weiß
  auf Akzentblau (Browser, iPhone, Web-App inkl. eigener „maskable“-Variante, Windows-ICO mit
  jeder Größe einzeln gerendert, kleine Größen mit größerer Glyphe), PNG-Favicon als Rückfall.
- **Tray-Symbol einfarbig wie die Systemsymbole** (Linux im Cinnamon-Grauton, Windows weiß bzw.
  dunkel) mit Statuspunkt unten rechts; unter Windows passend zur Taskleiste hell/dunkel (auch
  beim Umschalten) und in exakt der Systemgröße (DPI-bewusst, keine Unschärfe durch Skalieren).
  Neue Option `[agent] tray_icon_color` (`auto`/`light`/`dark`), im Einstellungsfenster unter
  *Erweitert*. Autostart-Eintrag unter Linux zeigt jetzt das Programmsymbol.
- Linux-Programmdatei nutzt GTK, AppIndicator und WebKit des Systems statt eigener Kopien:
  **ca. 18 statt 55–70 MB**, und die Bibliotheken passen sicher zusammen (vorher: eigenes GTK,
  aber System-WebKit). Unnötige Pakete (pygments, rich, cryptography, greenlet …) ausgeschlossen.
- Der Start der lokalen Weboberfläche ist nach `runtime.start_local_server` gewandert
  (gemeinsam für `diskatlas-gui` und Tray).

## [0.3.0] - 2026-09-26

### Hinzugefügt
- **iPhone-Web-App:** installierbar über „Zum Home-Bildschirm“ (Manifest, Icons, Apple-Metadaten).
  Neue Seite **Scannen** (`/scan`): Kamera-Barcode-Scanner mit lokal ausgelieferter ZXing-Bibliothek
  (Apache-2.0, kein Internet/CDN nötig; Code128, Code39, DataMatrix, QR u. a.) und Handeingabe.
  Der Treffer zeigt die Platte und ihren Ort: „Steckt in Schacht N“, „Angeschlossen an …“ oder bei
  abgesteckten Platten der **Lagerort**. Der Lagerort lässt sich direkt am Handy eintragen
  (`PATCH /api/v1/disks/{id}`, Detailseite, Suche; sichtbar im Dashboard bei Offline-Platten).
  Abgleich über Seriennummer oder WWN, tolerant gegenüber Trennzeichen, Etikett-Zusatztext und
  Prüfziffern (`GET /api/v1/lookup?code=`). Die Kamera braucht HTTPS (Anleitung: `docs/UNRAID.md`).
- Neue Spalte `disks.location` (Migration 0003).
- Server-Paket für Unraid/Docker: `docker-compose.yml` verlangt Passwort und Token,
  Unraid-Vorlage `deploy/unraid/diskatlas.xml`, GitHub-Actions-Workflow, der das Image bei
  `v*`-Tags nach `ghcr.io/realcommerzpunk/diskatlas` veröffentlicht, systemd-User-Dienst für den
  Agenten (`deploy/linux/diskatlas-agent.service`) und die Anleitung `docs/UNRAID.md`
  (Container, Tailscale-HTTPS, Agent, Schächte, Update, Sicherung).

### Behoben
- Tests hängen nicht mehr von der lokalen Laufwerksdatenbank `drivedb.h` (smartmontools) ab; auf
  den CI-Rechnern fehlt sie, dort schlug `test_catalog` fehl. Die CI läuft jetzt auch auf
  `feature/**`- und `fix/**`-Branches.
- Ein ohne Testpfad gestarteter Server (`diskatlas serve`) stürzte beim Umbau ab
  (`default_bays_path` fehlte); durch Regressionstest abgesichert.

### Sicherheit
- **Anmeldung:** Die Weboberfläche und die API sind jetzt per Passwort geschützt
  (`DISKATLAS_PASSWORD` bzw. `[server] password`, Anmeldeseite `/login`, signiertes
  HttpOnly-Cookie, 30 Tage, Abmelden in der Kopfzeile; Passwortwechsel meldet alle ab;
  Fehlversuche werden gebremst). Agenten nutzen weiter das API-Token (`/api/v1/ingest/*`),
  dasselbe Token darf Skripte für die übrige API authentifizieren.
- Ein Server, der nicht nur lokal lauscht (`host` ≠ 127.0.0.1), startet **nur noch mit Passwort und
  API-Token** – sonst bricht er mit einer verständlichen Meldung ab (Ausnahme:
  `allow_insecure = true`). Lokaler Betrieb (GUI, `run` auf Loopback) bleibt ohne Passwort möglich.
- Das GUI-Fenster speichert die Sitzung dauerhaft (Anmeldung am Server nur einmal nötig).

### Geändert
- **Architektur Agent ↔ Server:** Alles, was einen Rechner betrifft, läuft im Agenten; der Server
  (Docker/Unraid) hält nur Weboberfläche, API und Datenbank.
  - Schachtbelegung: Der Agent meldet beim Heartbeat die SATA-Ports (`ports_info`); der Server
    speichert sie je Rechner (Tabelle `host_states`) und zeigt die Schächte daraus – ohne selbst
    auf sysfs zuzugreifen. Die Zuordnung Port → Schacht liegt jetzt in der Datenbank
    (Tabelle `settings`); eine vorhandene lokale `bays.json` wird einmalig übernommen.
  - Umbenennen der Bezeichnung ist jetzt ein **Auftrag** an den Agenten (Tabelle `commands`,
    `GET /api/v1/ingest/commands`, `POST …/commands/{id}/result`). Der Agent prüft den Auftrag
    gegen seinen eigenen Festplattenstand, ignoriert vom Server mitgeschickte Gerätepfade und
    meldet das Ergebnis zurück; die Festplattendetails zeigen den Status (wartet/läuft/fertig).
    Der Server führt nie selbst etwas auf Platten aus. Auch im Betrieb ohne Docker (`run`) läuft
    das über denselben Weg.
  - Neuer Befehl `diskatlas agent` (Agent für den Betrieb mit zentralem Server).
- Schema-Migration 0003: Tabellen `host_states`, `settings`, `commands`; Spalte `disks.location`
  (Lagerort, für die geplante iPhone-App).

## [0.2.0] - 2026-09-26

### Hinzugefügt
- Hersteller und Verkaufsbezeichnung (z. B. „Seagate IronWolf“, „WD Red“, „WD Red Pro“) werden
  aus der Modellnummer ermittelt und in der Datenbank gespeichert (neue Spalte
  `disks.product_line`, Migration 0002; `vendor` wird gefüllt, soweit das Betriebssystem
  keinen meldet). Quelle: `model_family` aus smartctl, sonst die Laufwerksdatenbank
  `drivedb.h` von smartmontools (lokal, kein Download), dazu eine Präfix-Tabelle für den
  Hersteller. Controller-Familien („SandForce Driven SSDs“) zählen nicht als Verkaufsbezeichnung.
  Bereits erfasste Platten werden beim Start nachgetragen. Anzeige im Dashboard, in den
  Schachtzeilen und in den Details; auch in Suche, Gruppierung (Hersteller, Serie) und API.
- Linux: Angeschlossene, nicht eingehängte Dateisysteme (ntfs, exfat, vfat, ext2/3/4, btrfs,
  xfs, f2fs; keine Systemvolumes) hängt der Agent selbst ein (`auto_mount = true`, udisks2 wie
  der Dateimanager, ohne sudo), damit Belegung und Dateien erfasst werden. Ein Fehlversuch
  wird höchstens alle 10 Minuten wiederholt.
- Umbenennen der Dateisystem-Bezeichnung hängt das Volume bei Bedarf selbst aus und danach
  wieder ein (NTFS/exFAT immer, andere erst nach direktem Versuch). Vorher fragt die Oberfläche
  ausdrücklich nach. Ein Sperr-Marker verhindert, dass der Agent es dazwischen wieder einhängt;
  scheitert das Umbenennen, wird trotzdem wieder eingehängt.
- Dashboard: Bei angeschlossenen Platten mit nicht eingehängtem Dateisystem steht in der Spalte
  „Belegung“ jetzt „nicht eingehängt“ statt „unbekannt“ (die Belegung ist nur bei eingehängtem
  Dateisystem messbar); Tooltip erklärt das.
- Dashboard: Filter „Dateisystem“ (inkl. „kein Dateisystem“) und „Belegung bekannt/unbekannt“,
  Sortierung und Gruppierung nach Dateisystem, neue Spalte „Dateisystem“; die Freitextsuche
  findet auch Dateisysteme. So lassen sich Platten mit unbekannter Belegung gezielt finden.
  Auch in `GET /api/v1/disks` (`fs`, `usage`).
- Wird eine Partition neu formatiert (gleiche Partition, neue Dateisystem-UUID), verwirft
  DiskAtlas den alten Dateiindex und die alte Belegung sofort, statt sie bis zum nächsten
  Einhängen anzuzeigen.
- Kennzahl „Kapazität“ zeigt zusätzlich „unbekannt“: die Größe von Platten ohne bekannte
  Belegung (nie eingehängt, ohne Partition). Damit gilt belegt + frei + unbekannt ≈ Kapazität;
  bisher fehlte dieser Teil scheinbar in der Summe (auch `unknown` in `/api/v1/stats`).
- Neues Programm-Icon (schwarz-weiße Festplatte: Gehäuse, Teller, Lesearm, SATA-Kontakte) für GUI-Fenster, Startmenü,
  Windows-`.ico`, Browser-Tab und Kopfzeile der Weboberfläche; Quelle
  `packaging/icons/diskatlas.svg`, erzeugt mit `python scripts/build_icons.py`.
- Schächte: Einstellung „Schacht 1 liegt unten“ im Assistenten (Anzeige dann von 4 nach 1).
- Dashboard: Haken „Systemplatten ausblenden“ (Systemdatenträger ignorieren) (Standard: an, wird im Browser/Fenster
  gemerkt). Festplatten mit dem Label „System“ verschwinden dann aus Dashboard, Kennzahlen,
  Dateisuche und Doublettenprüfung. Die API und die Detailseite bleiben vollständig; der
  Warnbanner „nicht abziehen“ gilt weiterhin für alle Platten.
- Hot-Swap-Schächte: die vier 3,5"-Einschübe (die Zeile selbst ist der schwarze Einschub, vorn
  eine Kappe mit Federhebel, Nummer und Status-LED) stehen als die
  ersten vier Einträge der Festplattenliste, unabhängig von Filter, Sortierung und
  Gruppierung, und sind optisch als Schächte erkennbar. Sie zeigen, ob und welche Platte
  steckt (LED: Gesundheit; pulsiert während der Indizierung mit Hinweis „nicht abziehen“).
  Platten in Schächten erscheinen nicht zusätzlich weiter unten. Die Zuordnung SATA-Port →
  Schacht wird einmalig über den Assistenten „Schächte einrichten“ (`/bays/setup`) erkannt
  (Platte ziehen und wieder einstecken) und in `bays.json` im Datenverzeichnis gespeichert.
  Nur Linux mit SATA; sonst entfallen die Schachtzeilen.
- Jede Festplattenzeile beginnt mit einer schmalen Spalte (Status-Punkt), damit die Namen von
  Schacht- und normalen Zeilen bündig untereinander stehen.
- Dashboard kompakter: schmale Kennzahlenzeile, Filter, Sortierung und der Haken
  „Systemplatten ausblenden“ in einer Zeile, Tabelle über die volle Breite mit engeren Zeilen.
- Datenänderungen werden erkannt: ändert sich der belegte Platz eines eingehängten Volumes und
  bleibt danach 30 s stabil (`change_settle_seconds`), wird der Dateiindex neu aufgebaut –
  z. B. nach dem Verschieben oder Kopieren von Daten. Bisher erst bei Neueinstecken oder
  nach 24 h. Verschieben innerhalb derselben Platte ändert die Belegung nicht und wird
  weiterhin erst beim regulären Neuindex (24 h) sichtbar.
- Dateisystem-Bezeichnung (die im Dateimanager angezeigte) lässt sich in den Festplattendetails
  ändern (Linux: udisks2/polkit, Windows: `Set-Volume`). Nur lokal und nur bei Server auf
  Loopback; Länge und Zeichen werden je Dateisystem geprüft.
- Festplattendetails erklären bei „Warnung“/„Kritisch“ den Auslöser im Klartext (z. B.
  fehlgeschlagener Selbsttest mit Betriebsstunde und Sektor, wiederzugewiesene Sektoren,
  NVMe-Verschleiß).
- Warnbanner auf jeder Seite, solange eine angeschlossene Festplatte indiziert wird („bitte nicht
  abziehen“, mit Zwischenstand); dazu `GET /api/v1/activity`.
- Doublettenprüfung (Menü „Doubletten“, API `/api/v1/duplicates/files` und `/folders`) auf Basis
  des Dateiindex, ohne Zugriff auf die Platten und auch für abgesteckte Festplatten:
  - **Dateien:** gleicher Name (ohne Groß-/Kleinschreibung) und gleiche Größe, sortiert nach
    vermeidbarem Speicherplatz; Filter nach Endung und Mindestgröße.
  - **Ordner:** identischer Inhalt (gleiche relative Pfade und Größen im ganzen Unterbaum),
    unabhängig vom Ordnernamen; Unterordner bereits gemeldeter Doubletten werden ausgeblendet,
    versteckte Ordner (`.xyz`) standardmäßig ebenfalls.

### Behoben
- Automatische Aktualisierung des Dashboards blockierte sich selbst: Auswahllisten ohne
  ausdrücklich markierten Eintrag galten immer als „vom Benutzer geändert“, sodass nie neu
  geladen wurde. Jetzt blockieren nur echte Eingaben; zusätzlich wird die Schachtbelegung
  geprüft (alle 3 s), sodass Ab-/Einstecken sofort sichtbar wird.
- Dashboard und Festplattendetails aktualisieren sich selbst (alle 5 s), wenn eine Festplatte
  ein-/abgesteckt wird oder sich Gesundheit, Belegung oder Dateianzahl ändern; bisher musste
  die Seite von Hand neu geladen werden. Die Live-Aktualisierung wartet nur bei tatsächlich
  geänderten Formularfeldern (das automatisch fokussierte Suchfeld blockiert sie nicht);
  statische Dateien werden per ETag revalidiert, damit die GUI kein altes `app.js` behält.
- Dateisuche: `*` und `?` werden als Platzhalter behandelt (z. B. `*.mkv`); bisher wurden
  sie wörtlich gesucht und lieferten keine Treffer.
- Server startet auch in venvs mit Zugriff auf System-Pakete, in denen ein veraltetes
  `websockets` das installierte verdeckt (WebSockets sind für uvicorn deaktiviert).

## [0.1.0] - 2026-09-23

### Hinzugefügt
- Festplattenerkennung unter Linux (`lsblk`) und Windows (PowerShell/Storage-Modul) mit
  plattformübergreifend stabiler Identität (Seriennummer, WWN, GPT-Partitions-GUID).
- SMART-Auswertung über `smartctl --json` (SATA/ATA und NVMe) mit Gesundheitsbewertung
  (Gut / Warnung / Kritisch / Unbekannt), Temperatur, Betriebsstunden, Sektorzählern und
  Verlauf; automatischer Versuch mit `sudo -n` und `-d sat` für USB-Gehäuse.
- Erfassung von Volumes: Datenträgerbezeichnung, Dateisystem, Größe, belegter/freier Speicher.
- Dateiindex je Volume (Pfad, Größe, Änderungsdatum) mit atomarem Austausch bei Neuindizierung.
- Hot-Plug-Überwachung: neue, entfernte und neu eingehängte Festplatten werden erkannt;
  periodische Auffrischung von SMART (60 min) und Dateiindex (24 h).
- Offline-Übersicht: Festplatten, Volumes und Dateien bleiben nach dem Abstecken erhalten.
- Eigene Labels mit Kategorie und Farbe, Anzeigename und Notizen je Festplatte.
- Web-Dashboard mit Kennzahlen, Suche, Filter, Gruppierung (Gesundheit, Verbindung, Label,
  Label-Kategorie, Rechner, Anschluss, Typ) und Sortierung; Detailseite je Festplatte;
  Dateisuche über alle Festplatten; Label-Verwaltung; helles und dunkles Farbschema.
- REST-API unter `/api/v1` inkl. OpenAPI-Dokumentation (`/docs`) und Ingest-Endpunkten
  für entfernte Agenten (optional per Bearer-Token geschützt).
- Agent kann direkt in die Datenbank oder per HTTP an einen zentralen Server schreiben.
- SQLite als Standard, PostgreSQL optional; Schema-Migrationen mit Alembic;
  `diskatlas db copy` zum Umzug des Datenbestands.
- Kommandozeile: `run`, `serve`, `watch`, `scan`, `disks`, `config`, `db upgrade`, `db copy`.
- Dockerfile und `docker-compose.yml` für den Serverbetrieb (z. B. Unraid).
- systemd-User-Dienst (Linux), Autostart-Skript (Windows), sudoers-Hilfsskript für smartctl.
- GitHub-Actions-CI (Linux + Windows, Python 3.11–3.13, Ruff, Pytest, Docker-Build).

[Unreleased]: https://github.com/OWNER/diskatlas/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/OWNER/diskatlas/releases/tag/v0.1.0
