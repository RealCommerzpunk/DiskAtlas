# DiskAtlas

[English](README.md) | **Deutsch**

**Inventar für Festplatten.** DiskAtlas erfasst automatisch jede angeschlossene oder per
Hot-Plug angesteckte Festplatte (Bezeichnung, Seriennummer, SMART-Gesundheit, Größe,
belegter und freier Speicher, alle Dateien) und speichert alles in einer Datenbank. So bleibt
die Übersicht vollständig, auch wenn eine Platte längst wieder im Schrank liegt.

- 🔌 **Hot-Plug**: neue Festplatten werden innerhalb weniger Sekunden erkannt und gescannt
- 🩺 **SMART**: Gesundheit, Temperatur, Betriebsstunden, defekte Sektoren, Verlauf
- 🗂️ **Dateiindex**: „Auf welcher Platte liegt diese Datei?“, auch offline durchsuchbar
- 🏷️ **Labels & Kategorien**: z. B. `Standort: Keller`, `Inhalt: Filme`, plus Notizen
- 📊 **Dashboard**: Kennzahlen, Suche, Filter, Gruppierung, Sortierung (Browser)
- 📱 **iPhone-Web-App**: Barcode der Seriennummer scannen und sehen, wo die Platte liegt
- 🐧🪟 **Linux Mint und Windows**; Server optional im **Docker-Container** (Unraid) oder in der Cloud

Details zu Architektur, Entscheidungen und Roadmap: **[PROJECT.md](PROJECT.md)** ·
Änderungen: **[CHANGELOG.md](CHANGELOG.md)**

---

## Installation

### Fertiges Programm (empfohlen)

Unter *Releases* liegt der Agent als einzelne Datei für Windows und Linux: Symbol im
Infobereich, Einstellungsfenster, wahlweise **nur auf diesem PC** (Oberfläche und Datenbank im
Programm) oder **mit einem DiskAtlas-Server** (Docker/Unraid). Anleitung:
**[docs/AGENT.md](docs/AGENT.md)**.

Code-Signatur der Windows-Datei: Free code signing provided by [SignPath.io](https://about.signpath.io/),
certificate by [SignPath Foundation](https://signpath.org/) (Einrichtung läuft; Richtlinie:
[docs/CODE_SIGNING.md](docs/CODE_SIGNING.md)).

### Aus dem Quellcode: Linux Mint / Ubuntu

```bash
sudo apt install python3-venv smartmontools git
git clone https://github.com/RealCommerzpunk/DiskAtlas.git ~/diskatlas
cd ~/diskatlas
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/diskatlas run
```

Dann im Browser **http://127.0.0.1:8765** öffnen.

### Aus dem Quellcode: Windows 10/11

1. [Python 3.11+](https://www.python.org/downloads/) installieren (Haken bei *Add to PATH*).
2. [smartmontools für Windows](https://www.smartmontools.org/wiki/Download) installieren.
3. In einer **Administrator**-PowerShell (SMART benötigt Adminrechte):

```powershell
cd C:\Pfad\zu\diskatlas
py -m venv .venv
.venv\Scripts\pip install -e .
.venv\Scripts\diskatlas run
```

Autostart bei der Anmeldung (mit Adminrechten):
`powershell -ExecutionPolicy Bypass -File deploy\windows\install-autostart.ps1`

## SMART-Berechtigungen (Linux)

`smartctl` braucht root-Rechte. Ohne sie funktioniert alles andere, der Gesundheitsstatus
bleibt aber „Unbekannt“. Empfohlen wird eine sudo-Regel, die **nur** `smartctl` freigibt:

```bash
./scripts/setup-smartctl-sudo.sh
```

DiskAtlas versucht danach automatisch `sudo -n smartctl …`. Alternative: den Agenten als root
starten (`sudo .venv/bin/diskatlas watch`).

## Benutzung

| Befehl | Beschreibung |
|---|---|
| `diskatlas run` | Dashboard **und** Überwachung starten (Normalbetrieb) |
| `diskatlas scan` | alle angeschlossenen Festplatten einmal scannen (`--no-files`, `--disk /dev/sdb`) |
| `diskatlas disks --smart` | erkannte Festplatten anzeigen, ohne zu speichern |
| `diskatlas serve` | nur Dashboard/API (z. B. im Container) |
| `diskatlas agent` | Agent für einen zentralen Server (sendet an `server_url`, führt Aufträge aus) |
| `diskatlas watch` | nur Überwachung (lokal oder an einen Server) |
| `diskatlas config --init` | Beispielkonfiguration anlegen |
| `diskatlas db copy --to URL` | Datenbestand in eine andere Datenbank umziehen |

Mit `-v` gibt es ausführliche Logs. Die REST-API ist unter **/docs** dokumentiert.

**Speicherorte (Standard)**

| | Linux | Windows |
|---|---|---|
| Datenbank | `~/.local/share/diskatlas/diskatlas.db` | `%LOCALAPPDATA%\diskatlas\diskatlas.db` |
| Konfiguration | `~/.config/diskatlas/config.toml` | `%APPDATA%\diskatlas\config.toml` |

Alle Optionen: [config.example.toml](config.example.toml).

### Datenbank

DiskAtlas speichert alles in **SQLite**, einer einzelnen Datei ohne eigenen Datenbankserver. Das
gilt für die lokale Installation, das Agent-Programm im Betrieb *Nur dieser PC* und den
Docker-Container (`/data/diskatlas.db`, auf Unraid `/mnt/user/appdata/diskatlas/diskatlas.db`).
Alternativ wird **PostgreSQL** unterstützt (`DISKATLAS_DATABASE_URL`, Extra `postgres`).
Änderungen am Datenbankschema werden beim Start automatisch angewendet (Alembic-Migrationen).
Lokale Daten in den Docker-Container übernehmen: [docs/UNRAID.md](docs/UNRAID.md#lokale-daten-übernehmen).

### Autostart unter Linux (systemd)

```bash
mkdir -p ~/.config/systemd/user
cp deploy/linux/diskatlas.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now diskatlas
```

## Zentraler Server (Docker / Unraid)

Der Container betreibt Dashboard, API und Ingest. Die Rechner mit den Festplatten senden
ihre Scans per Agent dorthin. Einrichtung auf Unraid inkl. HTTPS fürs iPhone:
**[docs/UNRAID.md](docs/UNRAID.md)**.

```bash
DISKATLAS_PASSWORD=... docker compose up -d
```

Auf jedem Rechner in `config.toml`:

```toml
[agent]
server_url = "http://unraid.local:8765"
api_token  = "token-deines-clients"   # Weboberfläche: Konto → Client anlegen
```

und dann `diskatlas agent` starten (oder das [Agent-Programm](docs/AGENT.md) nutzen).
Bestehende lokale Daten übernehmen:

```bash
diskatlas db copy --to "postgresql+psycopg://diskatlas:pw@unraid.local:5432/diskatlas"
```

(Für PostgreSQL lokal einmal `pip install -e ".[postgres]"`.)

Die Weboberfläche verlangt eine Anmeldung. Beim allerersten Start wird `DISKATLAS_PASSWORD` das
Passwort des Benutzers **Admin**; weitere Personen beantragen den Zugang unter `/register`, der
Admin schaltet sie unter *Verwaltung* frei. Jeder Benutzer legt unter *Konto* **Clients** an (einen
je Rechner mit Agent) mit eigenem Token; damit weisen sich die Agenten aus. Jede Platte gehört dem
Benutzer, dessen Client sie zuerst gemeldet hat: Benutzer sehen nur eigene und ihnen (je Platte,
nur lesend) freigegebene Platten, ändern darf nur der Besitzer (oder der Admin). Meldet der Client
eines anderen Benutzers eine vergebene Platte, ändert sich nichts – der Besitzer bekommt nur einen
Übernahmeantrag. Labels sind je Benutzer privat. Der Container braucht
`DISKATLAS_PASSWORD` nur beim allerersten Start.

### Dateien anfordern

Unter *Dateien* lassen sich alle indizierten Platten Ordner für Ordner durchklicken; Dateien und
Ordner (auch in Suchergebnissen) können angehakt und **angefordert** werden: Sie werden in einen Ordner
auf einer eigenen, angeschlossenen Platte kopiert. Eigene Dateien werden direkt kopiert; bei Dateien
anderer Benutzer legt der Besitzer an der Freigabe je Platte fest, ob das nie, nur nach Rückfrage oder
immer erlaubt ist. Ist eine Platte nicht angeschlossen, wird ihr Besitzer gebeten, sie anzuschließen
(Weboberfläche und Tray-Meldung). Der Agent kopiert nur, wenn es lokal eingeschaltet ist
(`allow_transfer`, Standard aus), fasst Systemvolumes nie an und verlässt das Volume nie. Zwischen
verschiedenen Rechnern läuft die Datei **Ende-zu-Ende verschlüsselt** über den Server (X25519 +
AES-256-GCM, Zufallsnamen, strikt ein Stück nach dem anderen, begrenzt durch `DISKATLAS_RELAY_MAX_BYTES`).

## Entwicklung

```bash
pip install -e ".[dev]"
pytest
ruff check src tests
```

Projektstruktur, Datenmodell, Migrationen und Release-Ablauf: siehe
[PROJECT.md › Entwicklung](PROJECT.md#9-entwicklung).

## Lizenz

[MIT](LICENSE). Ausnahmen (Fremdbestandteile): Das Programmsymbol stammt von
[Streamline](https://github.com/webalys-hq/streamline-vectors) (CC BY 4.0, verändert); die
Windows-Programmdatei enthält `smartctl.exe` aus smartmontools (GPL v2). Details:
[THIRD_PARTY.md](THIRD_PARTY.md).
Signatur der Windows-Datei: [docs/CODE_SIGNING.md](docs/CODE_SIGNING.md).
