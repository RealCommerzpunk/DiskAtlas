# DiskAtlas

**English** | [Deutsch](README.de.md)

**An inventory for hard drives.** DiskAtlas automatically records every hard drive that is
connected or hot-plugged (model, serial number, SMART health, capacity, used and free space, and
every file on it) and stores it all in a database. Your overview stays complete even when a drive
has long been put back in the cupboard.

- 🔌 **Hot-plug**: new drives are detected and scanned within seconds
- 🩺 **SMART**: health, temperature, power-on hours, bad sectors, history
- 🗂️ **File index**: "Which drive holds this file?" – searchable even while the drive is offline
- 🏷️ **Labels & categories**: e.g. `Location: Basement`, `Content: Movies`, plus notes
- 📊 **Dashboard**: key figures, search, filters, grouping, sorting (in the browser)
- 📱 **iPhone web app**: scan a drive's serial-number barcode to see where it is
- 🐧🪟 **Linux and Windows**; optional central server as a **Docker container** (e.g. Unraid)

> The user interface and the detailed guides are currently in German.

Architecture, decisions and roadmap: **[PROJECT.md](PROJECT.md)** (German) ·
Changes: **[CHANGELOG.md](CHANGELOG.md)** (German)

---

## Installation

### Ready-made program (recommended)

The [Releases](https://github.com/RealCommerzpunk/DiskAtlas/releases) page has the agent as a
single file for Windows and Linux: an icon in the system tray and a settings window. It runs
either **on this PC only** (web interface and database inside the program) or **connected to a
DiskAtlas server** (Docker/Unraid). Guide: **[docs/AGENT.md](docs/AGENT.md)** (German).

Code signing of the Windows file: Free code signing provided by
[SignPath.io](https://about.signpath.io/), certificate by
[SignPath Foundation](https://signpath.org/) (setup in progress; policy:
[docs/CODE_SIGNING.md](docs/CODE_SIGNING.md)).

### From source: Linux Mint / Ubuntu

```bash
sudo apt install python3-venv smartmontools git
git clone https://github.com/RealCommerzpunk/DiskAtlas.git ~/diskatlas
cd ~/diskatlas
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/diskatlas run
```

Then open **http://127.0.0.1:8765** in the browser.

### From source: Windows 10/11

1. Install [Python 3.11+](https://www.python.org/downloads/) (tick *Add to PATH*).
2. Install [smartmontools for Windows](https://www.smartmontools.org/wiki/Download).
3. In an **administrator** PowerShell (SMART requires admin rights):

```powershell
cd C:\path\to\diskatlas
py -m venv .venv
.venv\Scripts\pip install -e .
.venv\Scripts\diskatlas run
```

Start at logon (with admin rights):
`powershell -ExecutionPolicy Bypass -File deploy\windows\install-autostart.ps1`

## SMART permissions (Linux)

`smartctl` needs root rights. Without them everything else works, but the health status stays
"unknown". Recommended: a sudo rule that allows **only** `smartctl`:

```bash
./scripts/setup-smartctl-sudo.sh
```

DiskAtlas then tries `sudo -n smartctl …` automatically. Alternatively run the agent as root
(`sudo .venv/bin/diskatlas watch`).

## Usage

| Command | Description |
|---|---|
| `diskatlas run` | start dashboard **and** monitoring (normal operation) |
| `diskatlas scan` | scan all connected drives once (`--no-files`, `--disk /dev/sdb`) |
| `diskatlas disks --smart` | show detected drives without saving |
| `diskatlas serve` | dashboard/API only (e.g. in the container) |
| `diskatlas agent` | agent for a central server (sends to `server_url`, runs server commands) |
| `diskatlas watch` | monitoring only (local database or a server) |
| `diskatlas config --init` | create an example configuration |
| `diskatlas db copy --to URL` | move all data to another database |

Add `-v` for verbose logs. The REST API is documented at **/docs**.

**Default locations**

| | Linux | Windows |
|---|---|---|
| Database | `~/.local/share/diskatlas/diskatlas.db` | `%LOCALAPPDATA%\diskatlas\diskatlas.db` |
| Configuration | `~/.config/diskatlas/config.toml` | `%APPDATA%\diskatlas\config.toml` |

All options: [config.example.toml](config.example.toml).

### Database

DiskAtlas stores everything in **SQLite** – a single file, no database server needed. This applies
to the local installation, the agent program in *This PC only* mode and the Docker container
(`/data/diskatlas.db`; on Unraid `/mnt/user/appdata/diskatlas/diskatlas.db`). **PostgreSQL** is
supported as an alternative (`DISKATLAS_DATABASE_URL`, install extra `postgres`). Schema changes
are applied automatically at startup (Alembic migrations). Moving local data into the Docker
container: [docs/UNRAID.md](docs/UNRAID.md#lokale-daten-übernehmen) (German).

### Autostart on Linux (systemd)

```bash
mkdir -p ~/.config/systemd/user
cp deploy/linux/diskatlas.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now diskatlas
```

## Central server (Docker / Unraid)

The container runs the dashboard, API and ingest. The computers with the drives send their scans
to it via the agent. Setup on Unraid including HTTPS for the iPhone:
**[docs/UNRAID.md](docs/UNRAID.md)** (German).

```bash
DISKATLAS_PASSWORD=... docker compose up -d
```

On each computer, in `config.toml`:

```toml
[agent]
server_url = "http://unraid.local:8765"
api_token  = "token-of-your-client"   # web interface: Konto → Client anlegen
```

then start `diskatlas agent` (or use the [agent program](docs/AGENT.md)). To move existing local
data:

```bash
diskatlas db copy --to "postgresql+psycopg://diskatlas:pw@unraid.local:5432/diskatlas"
```

(For PostgreSQL, run `pip install -e ".[postgres]"` once locally.)

The web interface requires a login. On the very first start `DISKATLAS_PASSWORD` becomes the
password of the user **Admin**; other people apply for access at `/register` and the Admin
approves them under *Verwaltung*. Each user creates **clients** (one per computer running an agent)
with their own token under *Konto*; agents authenticate with that token. Every drive belongs to
the user whose client reported it first: users see only their own drives plus those shared with
them (per drive, read-only), and only the owner (or the Admin) can change anything. If another
user's client reports an owned drive, nothing changes – the owner just gets a transfer request.
Labels are private per user. The container needs
`DISKATLAS_PASSWORD` only for the very first start.

### Requesting files

The *Files* page lets you browse every indexed drive folder by folder and tick files or folders
(also in search results) to **request** them: they are copied to a folder on one of your own connected
drives. Your own files are copied directly; for files of other users the owner sets a per-drive copy
permission next to the read share (never / always ask / always allow). If a drive is not connected, the
owner is asked to plug it in (web UI and tray notification). The agent only copies when it is switched
on locally (`allow_transfer`, off by default), never touches system volumes and never leaves the volume.
Between different computers the file travels through the server, **end-to-end encrypted** (X25519 +
AES-256-GCM, random object names, strictly one chunk at a time, capped by `DISKATLAS_RELAY_MAX_BYTES`).

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check src tests
```

Project structure, data model, migrations and release process:
[PROJECT.md › Entwicklung](PROJECT.md#9-entwicklung) (German).

## License

[MIT](LICENSE). Exceptions (third-party parts): the program icon is by
[Streamline](https://github.com/webalys-hq/streamline-vectors) (CC BY 4.0, modified); the Windows
program file includes `smartctl.exe` from smartmontools (GPL v2). Details:
[THIRD_PARTY.md](THIRD_PARTY.md). Code signing: [docs/CODE_SIGNING.md](docs/CODE_SIGNING.md).
