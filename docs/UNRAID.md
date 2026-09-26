# DiskAtlas auf Unraid betreiben

Aufbau: Auf dem **Unraid** läuft ein Docker-Container mit Weboberfläche, API und Datenbank. Auf
deinem **PC** läuft der **Agent** (`diskatlas agent`), erkennt Festplatten, liest SMART, indiziert
Dateien und meldet alles an den Container. Vom **iPhone** rufst du die Weboberfläche als Web-App auf.

```
 PC (Linux/Windows)              Unraid                       iPhone
 diskatlas agent  ── HTTP(S) ──▶ Docker: DiskAtlas  ◀── HTTPS ── Safari / Web-App
 (Platten, SMART, Index)         (Web-UI, API, SQLite)          (Kamera-Scanner)
```

Der Agent verbindet sich **ausgehend** zum Server und fragt dort nach Aufträgen (z. B. „Bezeichnung
ändern“). Der Server greift nie selbst auf deinen PC zu.

## 1. Zugangsdaten festlegen

Du brauchst zwei Werte, beide geheim:

| Wert | Wofür | Beispiel erzeugen |
|---|---|---|
| `DISKATLAS_PASSWORD` | Anmeldung an der Weboberfläche | ein Passwort, das du dir merkst |
| `DISKATLAS_API_TOKEN` | Ausweis der Agenten (und Skripte) | `openssl rand -hex 32` |

Der Container **startet nicht**, wenn eines davon fehlt – bewusst, damit die Oberfläche nie
unbeabsichtigt offen im Netz steht.

## 2. Container auf Unraid einrichten

Das Image wird bei jeder Version (`v*`-Tag) von GitHub gebaut und liegt unter
`ghcr.io/realcommerzpunk/diskatlas`. Repository und Paket sind öffentlich, Unraid lädt das Image
ohne Anmeldung.

Dann den Container anlegen, am einfachsten mit der mitgelieferten Vorlage:

1. Vorlage im Unraid-Terminal herunterladen:
   `wget -O /boot/config/plugins/dockerMan/templates-user/my-diskatlas.xml https://raw.githubusercontent.com/RealCommerzpunk/DiskAtlas/main/deploy/unraid/diskatlas.xml`
2. Unraid → *Docker* → *Add Container* → Vorlage **DiskAtlas** wählen.
3. Port (Standard 8765), Datenpfad (`/mnt/user/appdata/diskatlas`), Passwort und API-Token eintragen → *Apply*.

Alternativ per Compose: `docker-compose.yml` verwenden (siehe Kommentar in der Datei).

Test im Heimnetz: `http://<unraid-ip>:8765` öffnen – die Anmeldeseite erscheint.

## 3. HTTPS für das iPhone (Tailscale)

Der Kamera-Scanner im iPhone-Browser funktioniert **nur über HTTPS**. Mit Tailscale bekommst du
ein gültiges Zertifikat ohne eigene Domain und Zugriff auch unterwegs.

1. Tailscale auf dem **Unraid** (Plugin *Tailscale* aus den Community Applications) und auf dem
   **iPhone** (App Store) installieren, beide mit demselben Konto anmelden.
2. Im Tailscale-Adminbereich unter *DNS* **MagicDNS** und **HTTPS Certificates** aktivieren.
3. Auf dem Unraid im Terminal:
   ```bash
   tailscale serve --bg 8765
   tailscale serve status        # zeigt die Adresse, z. B. https://tower.tail1234.ts.net
   ```
4. Auf dem iPhone in Safari `https://tower.tail1234.ts.net` öffnen, anmelden, dann
   *Teilen → Zum Home-Bildschirm*. Das ist die Web-App.

Das Passwort schützt die Oberfläche zusätzlich; die Adresse ist nur im eigenen Tailnet erreichbar
(nicht öffentlich im Internet, solange du `tailscale funnel` nicht aktivierst).

## 4. Agent auf dem PC einrichten

**Am einfachsten: das fertige Programm.** Unter *Releases* auf der GitHub-Seite liegen
`DiskAtlas-Agent-windows-x64.exe` und `diskatlas-agent-linux-x86_64` (Linux: nach dem Download
`chmod +x diskatlas-agent-linux-x86_64`). Programm starten → rechts unten erscheint das
DiskAtlas-Symbol, beim ersten Start öffnet sich das Einstellungsfenster:

1. **Server-Adresse** (`https://tower.tail1234.ts.net` oder `http://<unraid-ip>:8765`) und
   **API-Token** (derselbe wie im Container) eintragen.
2. **Verbindung testen** → „Verbindung in Ordnung …“ → **Speichern**.
3. Haken bei **Beim Anmelden automatisch starten** setzen.

Der Punkt am Symbol zeigt den Zustand: grün verbunden, gelb wartend/nicht eingerichtet, rot
gestört. Ein Klick aufs Symbol öffnet das Menü (Einstellungen, Dashboard, Beenden).

Windows: Ohne Administratorrechte kann der Agent SMART nicht lesen. Die übrigen Daten kommen trotzdem an.

**Aus dem Quellcode (Linux):**

```bash
cd ~/diskatlas && . .venv/bin/activate
diskatlas config --init          # legt ~/.config/diskatlas/config.toml an
```

In `config.toml` eintragen:

```toml
[agent]
server_url = "https://tower.tail1234.ts.net"     # oder http://<unraid-ip>:8765 im Heimnetz
api_token  = "<dasselbe Token wie im Container>"
```

Testen mit `diskatlas agent -v`, dauerhaft als Dienst: `deploy/linux/diskatlas-agent.service`
(Anleitung im Kopf der Datei). Als *User*-Dienst hat der Agent dieselben Rechte wie dein Dateimanager –
nötig für das automatische Einhängen und das Umbenennen von Bezeichnungen.

**Windows:** wie oben, Konfiguration unter `%APPDATA%\diskatlas\config.toml`, Start mit
`diskatlas agent` (Autostart: `deploy/windows/install-autostart.ps1`). Schächte gibt es nur unter Linux.

Das **GUI-Fenster** (`diskatlas-gui`) zeigt, sobald `server_url` gesetzt ist, direkt die Oberfläche
des Servers; lokal läuft dann nur der Agent. Die Anmeldung wird einmal abgefragt und gemerkt.

## 5. Schächte einrichten

Nach dem ersten Start des Agenten in der Weboberfläche auf **⚙ Schächte** klicken und für jeden
Schacht die Platte ziehen und wieder einstecken. Die Zuordnung liegt auf dem Server.

## Aktualisieren

Neue Version: Tag `vX.Y.Z` pushen → GitHub baut das Image → in Unraid den Container *aktualisieren*.
Die Datenbank wird beim Start automatisch migriert. Das Agent-Programm durch die neue Datei aus dem
Release ersetzen (bzw. aus dem Quellcode: `git pull` und `pip install -e .`). **Server und Agenten sollten dieselbe Version haben.**

## Sicherung

Alles Wichtige liegt in `/mnt/user/appdata/diskatlas/diskatlas.db` (SQLite). Diese Datei in die
Unraid-Sicherung aufnehmen (z. B. mit dem Plugin *Appdata Backup*).
