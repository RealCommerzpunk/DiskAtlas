# DiskAtlas – Projektbeschreibung

> **Lebendes Dokument.** Diese Datei beschreibt Ziel, Architektur, Entscheidungen und Stand des
> Projekts. Sie wird bei **jeder** fachlichen oder architektonischen Änderung mitgepflegt
> (siehe [Pflegeregeln](#10-pflegeregeln)). Änderungen im Detail stehen im
> [CHANGELOG](CHANGELOG.md).

| | |
|---|---|
| **Aktuelle Version** | 0.3.0 (siehe `src/diskatlas/__init__.py`) |
| **Status** | Server auf Unraid (Docker) im Einsatz, Agent unter Linux Mint; Windows ungetestet auf echter Hardware |
| **Zuletzt aktualisiert** | 2026-09-26 |

---

## 1. Ziel und Vision

DiskAtlas ist ein **Inventar für Festplatten**. Jede Festplatte, die an einem Rechner hängt
oder im laufenden Betrieb angesteckt wird, wird automatisch erfasst:

- Datenträgerbezeichnung(en), Seriennummer, Modell, Größe, Anschluss, HDD/SSD
- SMART-Gesundheitszustand inkl. Temperatur, Betriebsstunden und kritischer Zähler
- belegter und freier Speicherplatz je Volume
- ein vollständiger **Dateiindex** (Pfad, Größe, Änderungsdatum)

Die Daten landen in einer Datenbank und bleiben **auch dann verfügbar, wenn die Festplatte nicht
mehr angeschlossen ist**. Über ein Web-Dashboard kann man suchen („Auf welcher Platte liegt
*Interstellar*?“), filtern, gruppieren und sortieren sowie eigene Labels/Kategorien anheften
(„Standort: Keller“, „Inhalt: Filme“, „Status: Backup“).

**Ausbaupfad der Datenhaltung:**

1. **Heute:** lokale SQLite-Datei, Agent und Dashboard auf demselben Rechner.
2. **Nächster Schritt:** zentraler DiskAtlas-Server im Docker-Container auf dem Unraid
   (SQLite oder PostgreSQL); Agenten auf Linux-/Windows-Rechnern senden per HTTP dorthin.
3. **Später:** Betrieb in der Cloud (gleicher Container, PostgreSQL als Managed Service,
   Authentifizierung für das Dashboard).

## 2. Funktionsumfang

| Bereich | Funktion | Stand |
|---|---|---|
| Erkennung | Festplatten & Volumes unter Linux (`lsblk`) | ✅ 0.1.0 |
| Erkennung | Festplatten & Volumes unter Windows (PowerShell `Get-Disk`/`Get-Partition`/`Get-Volume`) | ✅ 0.1.0 (nur mit Testdaten geprüft) |
| Erkennung | Hot-Plug per Polling (Standard 5 s), Reaktion auf Ein-/Aushängen | ✅ 0.1.0 |
| SMART | `smartctl --json`, SATA/ATA + NVMe, Bewertung + Verlauf | ✅ 0.1.0 |
| Belegung | Größe, belegt, frei je Volume; letzter Wert bleibt offline erhalten | ✅ 0.1.0 |
| Dateiindex | rekursiv, ohne Symlinks/fremde Dateisysteme, Ausschlussmuster | ✅ 0.1.0 |
| Labels | Name, Kategorie, Farbe; beliebig viele je Festplatte | ✅ 0.1.0 |
| Eigene Angaben | Anzeigename, Notizen | ✅ 0.1.0 |
| Dashboard | Kennzahlen, Suche, Filter, Gruppierung, Sortierung, Detailseite | ✅ 0.1.0 |
| Dateisuche | über alle (auch offline) Festplatten, Endung, Größe, Label | ✅ 0.1.0 |
| iPhone-Web-App | Scanner für Seriennummer-Barcodes (ZXing lokal), zeigt Schacht/Lagerort, Lagerort pflegbar | ✅ Unreleased |
| Hersteller/Serie | Hersteller und Verkaufsbezeichnung aus Modellnummer (smartctl-Familie, `drivedb.h`), gespeichert in `disks` | ✅ 0.2.0 |
| Dateisystem-Filter | Dashboard filtert/sortiert/gruppiert nach Dateisystem, Belegung bekannt/unbekannt | ✅ 0.2.0 |
| Auto-Einhängen | Linux: nicht eingehängte Dateisysteme selbst einhängen (`auto_mount`, udisks2), damit Belegung/Index möglich sind | ✅ 0.2.0 |
| Systemdatenträger ausblenden | Haken im Dashboard (Standard an), blendet Platten mit Label „System“ in UI-Listen und Suchen aus | ✅ 0.2.0 |
| Hot-Swap-Schächte | 4 grafische Einschübe als erste Zeilen der Festplattenliste, Port→Schacht per Assistent, Linux/SATA | ✅ 0.2.0 |
| Änderungserkennung | Neuindex bei geänderter Belegung + 30 s Ruhe (`change_settle_seconds`) | ✅ 0.2.0 |
| Volume umbenennen | Dateisystem-Label aus der GUI ändern (Linux udisks2/polkit, dabei bei Bedarf aus-/einhängen; Windows `Set-Volume`), nur lokal | ✅ 0.2.0 |
| Indizierungs-Warnbanner | „nicht abziehen“ mit Zwischenstand auf jeder Seite (`/api/v1/activity`) | ✅ 0.2.0 |
| Doubletten | Dateien (Name+Größe) und Ordner (identischer Inhalt) aus dem Index, ohne Prüfsummen | ✅ 0.2.0 |
| API | REST `/api/v1`, OpenAPI unter `/docs` | ✅ 0.1.0 |
| Verteilt | Agent → HTTP-Ingest → zentraler Server, Token-Schutz | ✅ 0.1.0 |
| Datenbank | SQLite, PostgreSQL, Migrationen (Alembic), `db copy` | ✅ 0.1.0 |
| Betrieb | Docker/Compose, systemd-Dienst, Windows-Autostart | ✅ 0.1.0 |
| Agent-Programm | Tray-Symbol mit Verbindungsstatus, Einstellungsfenster (config.toml), Autostart; Betriebsart „Server“ oder „nur dieser PC“ (Oberfläche + DB im Programm); fertige Datei für Windows (mit smartctl.exe) und Linux (PyInstaller, GitHub Actions) | ✅ Unreleased (Windows in Erprobung) |
| Sicherheit | Login für das Dashboard | ⏳ geplant |
| Suche | Volltext-Index (SQLite FTS5 / PostgreSQL `tsvector`) für sehr große Indizes | ⏳ geplant |
| Auswertung | Diagramme (Belegung/Temperatur über Zeit), Duplikatsuche | ⏳ geplant |
| Export | CSV/JSON-Export von Festplatten und Dateilisten | ⏳ geplant |
| Benachrichtigung | Warnung bei SMART-Verschlechterung (E-Mail/Push) | 💡 Idee |

## 3. Architektur

```
  Rechner A (Linux Mint)                  Rechner B (Windows)
 ┌───────────────────────────┐           ┌───────────────────────────┐
 │ Agent                     │           │ Agent                     │
 │  probe/linux  (lsblk)     │           │  probe/windows (PowerShell)│
 │  probe/smart  (smartctl)  │           │  probe/smart  (smartctl)  │
 │  probe/files  (scandir)   │           │  probe/files  (scandir)   │
 │        │                  │           │        │                  │
 │        ▼  Sink            │           │        ▼  Sink            │
 │  DatabaseSink ──┐         │           │  HttpSink ───────────┐    │
 └─────────────────┼─────────┘           └──────────────────────┼────┘
                   │                                            │ POST /api/v1/ingest/*
                   ▼                                            ▼
          ┌──────────────────────────────────────────────────────────────┐
          │ services/ingest   (einziger Schreibpfad für Scan-Ergebnisse) │
          │ services/queries  (Lesen, Filter, Gruppen, Suche)            │
          ├──────────────────────────────────────────────────────────────┤
          │ web/  FastAPI: Dashboard (Jinja2) + REST-API + Ingest-API    │
          ├──────────────────────────────────────────────────────────────┤
          │ db/   SQLAlchemy-Modelle + Alembic-Migrationen                │
          │       SQLite (Standard)  |  PostgreSQL (Docker/Cloud)         │
          └──────────────────────────────────────────────────────────────┘
```

**Betriebsarten** (alle aus demselben Paket):

| Befehl | Zweck |
|---|---|
| `diskatlas run` | Desktop-Betrieb: Dashboard + Agent in einem Prozess, lokale DB |
| `diskatlas serve` | nur Server (Dashboard, API, Ingest) – z. B. im Docker-Container |
| `diskatlas watch` | nur Agent – schreibt lokal oder (mit `server_url`) an einen Server |
| `diskatlas scan` | einmaliger Scan aller angeschlossenen Festplatten |

### 3.1 Verzeichnisstruktur

```
src/diskatlas/
  cli.py              Kommandozeile (argparse)
  config.py           Konfiguration: Defaults < TOML < Umgebungsvariablen
  probe/              Hardware-Erkennung, plattformabhängig, ohne Datenbankbezug
    types.py            Austauschformat DiskInfo/VolumeInfo/SmartInfo/FileRecord (Pydantic)
    linux.py            lsblk-Parser
    windows.py          PowerShell-Parser
    smart.py            smartctl-Aufruf + Auswertung
    files.py            Dateiindex (os.scandir)
  agent/
    agent.py            Scan-Ablauf und Hot-Plug-Schleife (Polling + Worker-Thread)
    sinks.py            DatabaseSink / HttpSink
  services/
    ingest.py           Upsert von Festplatten, Heartbeat, Dateiindex-Transaktionen
    queries.py          Dashboard-Statistik, Filter/Sortierung/Gruppierung, Dateisuche
    bays.py             Schachtzuordnung (SATA-Port → Schacht, bays.json) und Belegung
    lookup.py           Platte per Seriennummer/WWN finden, Ort ermitteln (iPhone-Scanner)
    hosts.py / commands.py   Heartbeat und Aufträge je Agent (Server ↔ Agent)
    (probe/catalog.py   Hersteller + Verkaufsbezeichnung aus Modellnummer)
    mounting.py         Ein-/Aushängen über udisksctl, Sperr-Marker gegen Auto-Einhängen
    fslabel.py          Dateisystem-Bezeichnung ändern (udisks2 / Set-Volume), Prüfung je Dateisystem
    duplicates.py       Doublettenprüfung (Dateien: Name+Größe; Ordner: Inhaltssignatur, on the fly)
  db/
    models.py           Datenmodell (SQLAlchemy 2.0)
    migrations/         Alembic (wird beim Start automatisch angewendet)
  web/
    app.py, api.py, views.py, schemas.py, formatting.py, templates/, static/
  tray/               Agent als Tray-Programm
    app.py              Tray-Symbol + Menü (pystray), Einzelinstanz, Neustart bei geänderter Konfiguration
    controller.py       Agent (und lokal: Weboberfläche) im Hintergrund, Status aus den Sink-Aufrufen
    window.py           Einstellungsfenster (pywebview, eigener Prozess)
    settings.py         config.toml lesen/prüfen/schreiben, Verbindungstest
    status.py           Statusdatei zwischen Tray und Fenster
    autostart.py        Start bei Anmeldung (XDG-Autostart / Run-Schlüssel)
  tools/dbcopy.py     Umzug zwischen Datenbanken
tests/                pytest; Fixtures mit echten lsblk/smartctl/PowerShell-Ausgaben
deploy/               systemd-Dienst, Windows-Autostart
packaging/            Icons, PyInstaller-Bauanleitung des Agent-Programms, smartctl-Download (fetch_smartctl.py)
scripts/              Versionierung, sudoers-Helfer für smartctl
```

## 4. Datenmodell

| Tabelle | Inhalt | Schlüssel |
|---|---|---|
| `disks` | Hardware-Stammdaten, letzter SMART-Stand, Verbindungsstatus, eigene Angaben | `disk_key` (eindeutig) |
| `host_states` | Heartbeat je Agenten-Rechner: belegte SATA-Ports (Grundlage der Schachtansicht) | `host` |
| `settings` | Einstellungen des Servers (z. B. Schachtzuordnung) | `key` |
| `commands` | Aufträge Server → Agent (`rename_label`, `rescan`) mit Status und Ergebnis | `id` |
| `volumes` | Partition/Dateisystem je Festplatte, Belegung, Indexstatus | (`disk_id`, `volume_key`) |
| `files` | Dateiindex (Pfad relativ zum Volume, Name, Endung, Größe, Änderungsdatum) | `volume_id` + `scan_id` |
| `smart_snapshots` | SMART-Verlauf inkl. Roh-JSON | `disk_id`, `taken_at` |
| `labels` | Name (eindeutig), Kategorie, Farbe | `name` |
| `disk_labels` | n:m-Zuordnung | (`disk_id`, `label_id`) |

**Identität einer Festplatte (`disk_key`):** `sn:<SERIENNUMMER>` → sonst `wwn:<wwn>` → sonst
`fp:<hash>` aus Modell, Größe und Volume-Schlüsseln. Die Seriennummer ist unter Linux und
Windows gleich, dadurch wird eine Platte an beiden Systemen als dieselbe erkannt.

**Identität eines Volumes (`volume_key`):** `part:<GPT-Partitions-GUID>` (unter Linux und Windows
identisch) → sonst `fs:<Dateisystem-UUID>` (Linux) bzw. `winvol:<Volume-ID>` (Windows).

**Dateiindex-Transaktion:** Ein Scan erhält eine `scan_id`, schreibt in Stapeln à 5000 Dateien
und setzt erst am Ende `volumes.active_scan_id`. Bis dahin sieht die Suche den vorherigen
vollständigen Stand. Bei Abbruch/Fehler wird der neue Stand verworfen.

## 5. Wichtige Entscheidungen (ADR-Kurzform)

| Datum | Entscheidung | Begründung |
|---|---|---|
| 2026-09-23 | **Python 3.11+, FastAPI, SQLAlchemy 2, Jinja2** | läuft unverändert unter Linux und Windows, gut wartbar, API und Oberfläche aus einem Prozess, einfach zu containerisieren. |
| 2026-09-23 | **Server-gerenderte Oberfläche ohne JS-Framework/Build-Schritt** | keine Node-Toolchain nötig, funktioniert offline und ohne CDN; kleine JS-Helfer nur als Progressive Enhancement. |
| 2026-09-23 | **Hot-Plug per Polling statt udev/WMI-Events** | eine Implementierung für beide Plattformen, robust, 5 s Latenz reicht; Events können später ergänzt werden. |
| 2026-09-23 | **SMART über smartmontools (`smartctl --json`)** | de-facto-Standard, auf Linux und Windows verfügbar, maschinenlesbar. |
| 2026-09-23 | **Agent/Sink-Trennung, HTTP-Ingest schon in 0.1** | Umzug auf den Unraid-Server erfordert nur Konfiguration (`server_url`), keinen Umbau. |
| 2026-09-23 | **Alembic-Migrationen ab der ersten Version** | Schemaänderungen bleiben für bestehende Datenbanken (SQLite wie PostgreSQL) sicher. |
| 2026-09-23 | **Volumes werden nie automatisch gelöscht**, nur als „nicht mehr vorhanden“ markiert | Windows sieht z. B. ext4-Partitionen nicht; ein Wechsel des Betriebssystems darf keinen Index löschen. Löschen erfolgt bewusst in der Oberfläche. |
| 2026-09-23 | **Unbekannte Werte überschreiben keine bekannten** (Label, Dateisystem, SMART, Belegung) | Offline-Übersicht soll den letzten bekannten Stand zeigen. |
| 2026-09-23 | **Systemvolumes (`/`, `/boot`, `C:\`) werden standardmäßig nicht indiziert** | Fokus auf Datenplatten; per `index_system_volumes = true` änderbar. |
| 2026-09-23 | **Dezimale Größeneinheiten (TB = 10¹²)** | entspricht der Herstellerangabe auf dem Etikett. |
| 2026-09-23 | **Doubletten ohne Prüfsummen, zur Laufzeit berechnet** | Kein Lesen der Platten nötig, funktioniert auch offline, keine Schemaänderung. Ordner-Signatur = Anzahl, Größe, Summe der Hashes (rel. Pfad, Größe). Nachteil: „Name+Größe“ ist ein starker Hinweis, aber kein Beweis; Inhaltshash wäre eine spätere Erweiterung (Roadmap). |
| 2026-09-23 | **Änderungserkennung über die Belegung (statvfs), nicht per inotify** | Billig, plattformübergreifend, kein Watcher je Verzeichnis. Blind für reines Verschieben innerhalb einer Platte (Belegung gleich) – das erfasst weiterhin der 24-h-Neuindex. |
| 2026-09-23 | **Umbenennen nur lokal und nur bei Loopback-Server** | Schreibt auf den Datenträger; solange es kein Login gibt, darf das nicht über das Netz auslösbar sein. Berechtigung über polkit statt sudoers. |
| 2026-09-23 | **Neu formatiert = gleiche Partition mit neuer Dateisystem-UUID → Index verwerfen; neue Partitions-GUID = neues Volume** | Volumes werden über die GPT-GUID identifiziert (MBR: Dateisystem-UUID); ein Neupartitionieren erzeugt also neue Volumes, das alte bleibt als „nicht mehr vorhanden“ stehen und kann manuell gelöscht werden. |
| 2026-09-23 | **Auto-Einhängen standardmäßig an, über udisks2 (schreibbar wie im Dateimanager)** | Ohne Einhängen sind Belegung und Dateiindex nicht erfassbar. Schreibgeschützt einhängen wurde verworfen, weil Daten zwischen Platten verschoben werden sollen. Abschaltbar mit `auto_mount = false`. |
| 2026-09-23 | **Hersteller/Serie aus smartmontools-`drivedb.h` statt eigener Datenpflege** | Die Liste wird von der Community gepflegt und liegt mit smartmontools bereits auf dem Rechner; `sudo update-smart-drivedb` aktualisiert sie. Ergänzt um eine kleine Präfix-Tabelle für den Hersteller. Grenzen: Familien sind teils technisch (Enterprise-Reihen) oder fehlen (neue Modelle) – dann bleibt die Serie leer. |
| 2026-09-26 | **Agent-Programm: pystray + pywebview in einem PyInstaller-Paket, Einstellungsfenster als eigener Prozess, Austausch über Statusdatei** | Beide Bibliotheken laufen unter Windows und Linux; pywebview ist schon im Projekt (GUI). Tray- und Fensterbibliothek wollen jeweils die Hauptschleife (unter Linux beide GTK, unter Windows Win32 vs. WinForms), deshalb getrennte Prozesse. Das Fenster schreibt nur `config.toml`; das Tray erkennt die Änderung und startet den Agenten neu. Der Verbindungsstatus wird aus den ohnehin stattfindenden Sink-Aufrufen abgeleitet, ohne zusätzlichen Netzverkehr. Ein Programm ohne Python-Installation ist für Windows-Nutzer die einzige zumutbare Verteilung. |
| 2026-09-26 | **Agent bleibt in Python; ein Programm für beide Betriebsarten; Linux nutzt System-GTK; smartctl.exe wird mitgeliefert** | Ein Neubau nur des Agenten in Go hätte kleinere Dateien gebracht, der lokale Betrieb bräuchte dann aber zusätzlich den Python-Server (doppelte Pflege, Größenvorteil weg). Oberfläche und DB-Bibliotheken kosten im Paket nur wenige MB. Unter Linux war das mitgelieferte GTK der Großteil der Datei (Symbole, ICU) und passte nicht sicher zum ohnehin vom System geladenen WebKit. smartctl.exe unverändert aus dem offiziellen Installer; GPL-Pflichten: Lizenz im Paket, Quellcode derselben Version neben der Datei im Release. Adminrechte für SMART richtet der Nutzer selbst per Aufgabenplanung ein (Anleitung). |
| 2026-09-26 | **MIT-Lizenz; Windows-Signatur über SignPath Foundation** | Kostenlose Signatur für Open-Source-Projekte, Signieren direkt aus GitHub Actions mit manueller Freigabe je Release. Voraussetzung ist eine OSI-Lizenz; MIT ist die einfachste und verträgt sich mit dem mitgelieferten (eigenständigen, GPL-lizenzierten) smartctl. Signiert wird nur die eigene .exe, smartctl bleibt Upstream-Binärdatei. |
| 2026-09-26 | **Server führt nichts auf Platten aus; Agenten holen Aufträge ab (Polling)** | Der Server läuft auf einem anderen Rechner (Unraid) und darf keine Kommandos an Rechner „durchreichen“. Der Agent verbindet sich ausgehend (NAT/Firewall-freundlich), prüft jeden Auftrag gegen seinen eigenen Stand und ignoriert Gerätepfade aus dem Auftrag. Aufträge sind auf eine feste Liste (`rename_label`, `rescan`) beschränkt. |
| 2026-09-26 | **Ein Passwort + signiertes Cookie statt Benutzerverwaltung; Server im Netz nur mit Passwort und Token** | Einzelnutzer-Heimnetz (Unraid, Tailscale). Kein Benutzerkonzept nötig; ein fehlendes Passwort darf den Server nicht unbemerkt öffnen. Das Passwort steckt in der Cookie-Signatur, ein Wechsel invalidiert alle Sitzungen. |
| 2026-09-26 | **Barcode-Erkennung im Browser mit lokal ausgelieferter ZXing-Bibliothek; Kamera nur über HTTPS (Tailscale)** | iOS-Safari hat kein `BarcodeDetector`; ZXing deckt Code128/39/DataMatrix/QR ab und läuft offline. `getUserMedia` verlangt einen sicheren Kontext, daher HTTPS über `tailscale serve`. Nur der erkannte Text geht an den Server. |
| 2026-09-23 | **Schächte über den SATA-Port (`/sys/block/sdX → ataN`), live aus sysfs, Zuordnung in `bays.json`** | Port ist an den Anschluss gebunden und ändert sich beim Plattenwechsel nicht (Gerätename/Mountpunkt schon). Keine Schemaänderung nötig; gilt nur für den Rechner, an dem die Schächte sitzen. |
| 2026-09-23 | **„Systemdatenträger ignorieren“ ist eine reine UI-Einstellung (Cookie), Kriterium = Label „System“** | Die API bleibt vollständig; das Label ist bereits das vorhandene Ordnungsmittel des Nutzers. |

## 6. Plattformhinweise

**Linux Mint / Ubuntu**
- Benötigt: `python3-venv`, `smartmontools`, `util-linux` (lsblk ≥ 2.37 für `MOUNTPOINTS`).
- SMART braucht root: entweder `scripts/setup-smartctl-sudo.sh` (sudoers-Regel nur für
  smartctl) oder den Agenten als root starten.
- Dateien werden nur auf **eingehängten** Volumes indiziert (Automount von Cinnamon genügt).
- Agent-Programm `diskatlas-agent`: gebaut für Ubuntu 24.04 / Linux Mint 22 (ca. 18 MB). Nutzt
  GTK 3, WebKitGTK 4.1 und AyatanaAppIndicator **des Systems** (nicht mitgeliefert). Tray-Symbol
  über AppIndicator (Cinnamon, KDE, XFCE; GNOME nur mit Erweiterung).

**Windows 10/11**
- Benötigt: Python 3.11+, smartmontools für Windows (`smartctl.exe`, wird auch unter
  `C:\Program Files\smartmontools\bin` gefunden).
- SMART braucht eine Administrator-Konsole bzw. die geplante Aufgabe aus
  `deploy/windows/install-autostart.ps1`.
- Noch nicht auf echter Hardware getestet (nur Parser-Tests mit Beispieldaten).
- Agent-Programm `DiskAtlas-Agent.exe` (ca. 30 MB): braucht die WebView2-Laufzeit (bei
  Windows 10/11 meist vorhanden) und bringt `smartctl.exe` (smartmontools, GPL v2) mit; ein
  installiertes smartmontools hat Vorrang. Ohne Administratorrechte ist **SMART nicht lesbar**;
  Einrichtung einer geplanten Aufgabe mit höchsten Rechten: [docs/AGENT.md](docs/AGENT.md).
  Noch nicht signiert (SmartScreen-Warnung beim ersten Start); Signatur über SignPath Foundation
  vorbereitet ([docs/CODE_SIGNING.md](docs/CODE_SIGNING.md)).

**Docker / Unraid**
- Container betreibt nur den Server. Scans erfolgen durch Agenten auf den Rechnern
  (`[agent] server_url = "http://unraid:8765"`, `api_token` wie im Container).
- Der Container startet **nur mit `DISKATLAS_PASSWORD` und `DISKATLAS_API_TOKEN`**.
- Daten unter `/data` (SQLite) oder PostgreSQL per `DISKATLAS_DATABASE_URL`.
- Image: `ghcr.io/realcommerzpunk/diskatlas` (GitHub Actions bei `v*`-Tags), Unraid-Vorlage
  `deploy/unraid/diskatlas.xml`. Einrichtung, Tailscale-HTTPS (Voraussetzung für die iPhone-
  Kamera) und Agent: [docs/UNRAID.md](docs/UNRAID.md).

## 7. Konfiguration

Siehe [`config.example.toml`](config.example.toml). Reihenfolge: Standardwerte < Datei
(`~/.config/diskatlas/config.toml` bzw. `%APPDATA%\diskatlas\config.toml`) < Umgebungsvariablen
(`DISKATLAS_DATABASE_URL`, `DISKATLAS_SERVER_URL`, `DISKATLAS_API_TOKEN`, `DISKATLAS_HOST`,
`DISKATLAS_PORT`, `DISKATLAS_HOST_NAME`, `DISKATLAS_SMARTCTL`). Unbekannte Optionen führen zu
einem Fehler, damit Tippfehler auffallen.

## 8. Roadmap

**0.2 – Komfort & Robustheit**
- [ ] Windows-Test auf echter Hardware, ggf. Parser-Korrekturen
- [ ] CSV/JSON-Export (Festplatten, Dateilisten)
- [ ] Button „Jetzt neu scannen“ im Dashboard (Agent-Befehlskanal)
- [ ] Anzeige des Agenten-Status (letzter Heartbeat je Rechner)

**0.3 – Zentraler Server**
- [ ] Login für Dashboard/API (Benutzer + Passwort, Session)
- [ ] Veröffentlichtes Docker-Image (GitHub Container Registry) + Unraid-Template
- [ ] Volltextsuche (FTS5 / PostgreSQL) für Indizes mit vielen Millionen Dateien

**Später**
- [ ] Doubletten: optionaler Inhaltshash (Agent liest nur Kandidaten mit gleicher Größe) für sichere Treffer
- [ ] Diagramme: Belegung und Temperatur über Zeit, SMART-Trends
- [ ] Duplikatsuche (Name + Größe, optional Hash)
- [ ] Benachrichtigungen bei SMART-Verschlechterung
- [ ] Ereignisgesteuertes Hot-Plug (udev / WMI) zusätzlich zum Polling

## 9. Entwicklung

```bash
python3 -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest                   # Tests
ruff check src tests     # Linting
diskatlas run -v         # Dashboard + Agent mit ausführlichem Log
```

**Schemaänderung:** Modell in `db/models.py` ändern, dann Migration erzeugen:

```bash
python - <<'EOF'
from alembic import command
from diskatlas.db import alembic_config
cfg = alembic_config(); cfg.set_main_option("sqlalchemy.url", "sqlite:///tmp-migration.db")
command.upgrade(cfg, "head"); command.revision(cfg, message="kurze beschreibung", autogenerate=True)
EOF
rm tmp-migration.db
```

Der Test `test_migrations_match_models` schlägt fehl, wenn eine Migration fehlt.

**Git-Workflow:** `main` ist stets lauffähig; Arbeit in Feature-Branches (`feature/…`,
`fix/…`), Pull Request, CI muss grün sein. Commit-Nachrichten im Imperativ, kurz und deutsch
oder englisch – aber einheitlich pro Projekt.

**Release:** `[Unreleased]` im CHANGELOG pflegen →
`python scripts/bump_version.py minor` → Commit → Tag `vX.Y.Z` → Push mit Tags.

## 10. Pflegeregeln

1. **Jede Änderung** am Verhalten wird im [CHANGELOG](CHANGELOG.md) unter `[Unreleased]`
   eingetragen – im selben Commit.
2. **Diese Datei** wird aktualisiert, wenn sich Funktionsumfang (§2), Architektur (§3),
   Datenmodell (§4), Entscheidungen (§5), Plattformhinweise (§6), Konfiguration (§7) oder
   Roadmap (§8) ändern. Neue Entscheidungen werden in §5 mit Datum **angehängt**, alte nicht
   gelöscht, sondern ggf. als „ersetzt durch …“ markiert.
3. Kopfzeile (Version, Status, Datum) bei jedem Release aktualisieren.
4. Versionsnummer ausschließlich über `scripts/bump_version.py` ändern (Semantic Versioning:
   MAJOR = inkompatible Änderung an API/Datenbank ohne Migration, MINOR = neue Funktion,
   PATCH = Fehlerbehebung).
