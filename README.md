# DiskAtlas

**Inventar für Festplatten.** DiskAtlas erfasst automatisch jede angeschlossene oder per
Hot-Plug angesteckte Festplatte (Bezeichnung, Seriennummer, SMART-Gesundheit, Größe,
belegter und freier Speicher, alle Dateien) und speichert alles in einer Datenbank. So bleibt
die Übersicht vollständig, auch wenn eine Platte längst wieder im Schrank liegt.

- 🔌 **Hot-Plug**: neue Festplatten werden innerhalb weniger Sekunden erkannt und gescannt
- 🩺 **SMART**: Gesundheit, Temperatur, Betriebsstunden, defekte Sektoren, Verlauf
- 🗂️ **Dateiindex**: „Auf welcher Platte liegt diese Datei?“, auch offline durchsuchbar
- 🏷️ **Labels & Kategorien**: z. B. `Standort: Keller`, `Inhalt: Filme`, plus Notizen
- 📊 **Dashboard**: Kennzahlen, Suche, Filter, Gruppierung, Sortierung (Browser)
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

### Aus dem Quellcode: Linux Mint / Ubuntu

```bash
sudo apt install python3-venv smartmontools git
git clone https://github.com/OWNER/diskatlas.git ~/diskatlas   # oder vorhandenen Ordner nutzen
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

### Autostart unter Linux (systemd)

```bash
mkdir -p ~/.config/systemd/user
cp deploy/linux/diskatlas.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now diskatlas
```

## Zentraler Server (Docker / Unraid)

Der Container betreibt Dashboard, API und Ingest. Die Rechner mit den Festplatten senden
ihre Scans per Agent dorthin.

```bash
docker compose up -d        # Token in docker-compose.yml vorher ändern!
```

Auf jedem Rechner in `config.toml`:

```toml
[agent]
server_url = "http://unraid.local:8765"
api_token  = "gleiches-token-wie-im-container"
```

und dann `diskatlas watch` starten. Bestehende lokale Daten übernehmen:

```bash
diskatlas db copy --to "postgresql+psycopg://diskatlas:pw@unraid.local:5432/diskatlas"
```

(Für PostgreSQL lokal einmal `pip install -e ".[postgres]"`.)

> ⚠️ Dashboard und API haben in Version 0.1 noch **keinen Login**, nur die Ingest-Endpunkte
> sind per Token geschützt. Nur im eigenen Netz betreiben.

## Entwicklung

```bash
pip install -e ".[dev]"
pytest
ruff check src tests
```

Projektstruktur, Datenmodell, Migrationen und Release-Ablauf: siehe
[PROJECT.md › Entwicklung](PROJECT.md#9-entwicklung).
