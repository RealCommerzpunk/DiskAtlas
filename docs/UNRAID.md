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

Du brauchst einen geheimen Wert:

| Wert | Wofür |
|---|---|
| `DISKATLAS_PASSWORD` | Startpasswort des Benutzers **Admin** (Anmeldung mit Name „Admin“) |

Das Passwort wird nur beim allerersten Start übernommen; danach ändert der Admin es unter
*Konto*. Weitere Benutzer beantragen den Zugang unter `/register`, der Admin schaltet sie unter
*Verwaltung* frei. Die Agenten weisen sich nicht mehr mit einem gemeinsamen Token aus, sondern
jeder **Client** mit seinem eigenen (siehe Abschnitt 4).

Beim allerersten Start **startet der Container nicht ohne** Passwort – bewusst, damit die Oberfläche
nie unbeabsichtigt offen im Netz steht. Sobald der Admin existiert, ist die Variable **nicht mehr
nötig** und darf leer bleiben (Aktualisierungen des Images brauchen sie nicht). Ein Konto „Master“
aus Version 0.6 und älter heißt nach dem Update „Admin“.

> **Umstieg von Version 0.4.0 und älter:** Der Container-Wert `DISKATLAS_API_TOKEN` wird nicht mehr
> gebraucht (harmlos, wenn er stehen bleibt). Deine Agenten melden sich erst wieder, wenn du dich
> als **Admin** anmeldest, unter *Konto* einen Client anlegst und das angezeigte Token in die
> Konfiguration des Agenten einträgst (`api_token`).

## 2. Container auf Unraid einrichten

Das Image wird bei jeder Version (`v*`-Tag) von GitHub gebaut und liegt unter
`ghcr.io/realcommerzpunk/diskatlas`. Repository und Paket sind öffentlich, Unraid lädt das Image
ohne Anmeldung.

Dann den Container anlegen, am einfachsten mit der mitgelieferten Vorlage:

1. Vorlage im Unraid-Terminal herunterladen:
   `wget -O /boot/config/plugins/dockerMan/templates-user/my-diskatlas.xml https://raw.githubusercontent.com/RealCommerzpunk/DiskAtlas/main/deploy/unraid/diskatlas.xml`
2. Unraid → *Docker* → *Add Container* → Vorlage **DiskAtlas** wählen.
3. Port (Standard 8765), Datenpfad, Passwort eintragen → *Apply*.
   **Datenpfad:** Mit Cache-Pool besser `/mnt/cache/appdata/diskatlas` statt
   `/mnt/user/appdata/diskatlas`. Unter `/mnt/user` liegt Unraids FUSE-Dateisystem; SQLite ist dort
   deutlich langsamer, was bei großen Dateiindizes zu „database is locked“ führen kann. Liegt
   `appdata` auf dem Cache (Standard), sind das dieselben Dateien – nur der Weg ist direkter.
   Umstellen: Container stoppen, Pfad *Daten* ändern, starten.

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
`DiskAtlas-Agent-windows-x64.exe` und `diskatlas-agent-linux-x86_64`. Programm starten → rechts
unten erscheint das DiskAtlas-Symbol, beim ersten Start öffnet sich das Einstellungsfenster:

1. **Mit DiskAtlas-Server verbinden** wählen.
2. **Server-Adresse** (`https://tower.tail1234.ts.net` oder `http://<unraid-ip>:8765`) und
   **Client-Token** (aus *Konto* → *Client anlegen*) eintragen.
3. **Verbindung testen** → „Verbindung in Ordnung …“ → **Speichern**.
4. Haken bei **Beim Anmelden automatisch starten** setzen (Windows mit SMART: stattdessen die
   geplante Aufgabe aus [AGENT.md](AGENT.md)).

Alles Weitere (Adminrechte für SMART unter Windows, SmartScreen, Protokolle, Betrieb ganz ohne
Server): **[AGENT.md](AGENT.md)**.

**Aus dem Quellcode (Linux):**

```bash
cd ~/diskatlas && . .venv/bin/activate
diskatlas config --init          # legt ~/.config/diskatlas/config.toml an
```

**Token besorgen:** In der Weboberfläche anmelden, *Konto* → *Client anlegen* (Name z. B. „Arbeits-PC“).
Das Token erscheint nur einmal; kopiere es sofort. Jeder Rechner bekommt einen eigenen Client.

In `config.toml` eintragen:

```toml
[agent]
server_url = "https://tower.tail1234.ts.net"     # oder http://<unraid-ip>:8765 im Heimnetz
api_token  = "<Token deines Clients, siehe unten>"
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

## Lokale Daten übernehmen (Altbestand importieren)

Wer DiskAtlas vorher nur lokal betrieben hat („Nur dieser PC“), übernimmt den bisherigen Bestand
(Platten, Partitionen, Dateiindex, SMART-Verlauf, Namen, Lagerorte, Notizen, Labels) in den
Container, **ohne** dort etwas zu ersetzen. Platten, die der Container schon kennt, werden
übersprungen; ihr neuerer Stand bleibt. Die Platten gehören danach dem gewählten Benutzer.

**Die alte Datenbank** liegt unter `~/.local/share/diskatlas/diskatlas.db` (Windows:
`%LOCALAPPDATA%\diskatlas\diskatlas.db`). Vorher das lokale Agent-Programm beenden. Liegt daneben
eine `diskatlas.db-wal`, gehört sie dazu; der Import liest den Stand mit dem SQLite-Backup
konsistent aus. Am einfachsten: erst eine einzelne Datei erzeugen, z. B. mit
`sqlite3 ~/.local/share/diskatlas/diskatlas.db ".backup ~/diskatlas-alt.db"`.

**Weg 1 – im Browser:** als Admin *Verwaltung → Altbestand importieren*, Datei wählen, Besitzer
wählen, *Importieren*. Der Import läuft im Hintergrund (bei 580 000 Dateien rund eine halbe
Minute), die Seite zeigt den Fortschritt. Die hochgeladene Datei wird danach gelöscht. Hinter
einem Reverse-Proxy kann eine Upload-Grenze greifen (Datei oft > 100 MB) – dann Weg 2 oder direkt
über die IP des Servers.

**Weg 2 – auf dem Server:** Datei nach `/mnt/user/appdata/diskatlas/` kopieren (SMB-Freigabe
`appdata` oder `scp`) und im Unraid-Terminal:

```bash
docker exec DiskAtlas diskatlas db import /data/diskatlas-alt.db --user Admin
rm /mnt/user/appdata/diskatlas/diskatlas-alt.db
```

**Vorher sichern:** `/mnt/user/appdata/diskatlas/diskatlas.db` kopieren (oder *Appdata Backup*).
Bricht der Import ab, einfach erneut starten: Fertige Platten werden übersprungen, die
unterbrochene wird vervollständigt. Eine alte Schachtzuordnung (`bays.json`) wird nicht
übernommen; Schächte stellt jeder Client unter *Konto* ein.

## Benutzer, Platten und Freigaben

- **Jede Platte gehört dem Benutzer, dessen Client sie zuerst meldet.** Benutzer sehen nur ihre
  eigenen und die ihnen freigegebenen Platten; nur der Besitzer (und der Admin) darf ändern.
- **Freigeben:** auf der Plattenseite unter *Besitz & Freigabe* einzelne Benutzer wählen – sie
  sehen die Platte dann *nur lesend*.
- **Platte weitergeben:** Steckt sie an einem Rechner eines anderen Benutzers, entsteht dort ein
  Übernahmeantrag. Der bisherige Besitzer bestätigt oder lehnt ihn unter *Übernahmen* ab; bis dahin
  bleibt alles unverändert. Bei Zustimmung entfallen seine Notizen, Labels und Freigaben dazu.
- **Der Admin sieht alles**, verwaltet Benutzer und Anträge und übergibt Altbestand.
- **Nach dem Umstieg** sind alle bisherigen Platten *herrenlos* (nur für den Admin sichtbar):
  *Verwaltung → Herrenlose Platten* übergibt sie samt Labels einem Benutzer.

## Dateien anfordern (Kopieren zwischen Platten und Rechnern)

Unter *Dateien* lassen sich Festplatten durchklicken; vor Dateien und Ordnern (auch in den
Suchergebnissen) steht eine Checkbox, dazu der Knopf **„Datei anfordern“**.

1. **Ziel wählen:** ein Ordner auf einer *angeschlossenen, eigenen* Platte eines deiner Clients
   (Systemplatten nicht). Der Ordner wird bei Bedarf angelegt und pro Client als Standard gemerkt.
2. **Wer darf was?** Eigene Dateien werden direkt eingeplant. Für Dateien anderer Benutzer legt der
   Besitzer an der Freigabe (Plattenseite → *Besitz & Freigabe*) pro Benutzer fest: **nie erlauben**
   (Standard), **immer nachfragen** (er stimmt unter *Anfragen* zu) oder **immer erlauben**. Der Admin
   hat hier keinen Sonderstatus.
3. **Platte fehlt?** Dann wartet die Datei bis zu 14 Tage. Der Besitzer der fehlenden Platte bekommt
   einen Hinweis (Weboberfläche unter *Anfragen*, Tray-Meldung des Agenten), höchstens einmal je Platte
   und Tag. Sobald die Platte angeschlossen ist, geht es automatisch weiter.
4. **Ausgeführt** wird es vom Agenten – **nur wenn du es an dem Rechner erlaubst**: Tray →
   Einstellungen → *„Angeforderte Dateien kopieren erlauben“* (`allow_transfer = true`, Standard aus).
   Der Agent leitet Laufwerke und Pfade aus seiner eigenen Erkennung ab, fasst Systemvolumes nie an,
   verlässt das Volume nie (kein `..`, keine Symlinks/Junctions nach draußen), überschreibt nichts
   (`name (1).ext`), schreibt erst in eine Teildatei und prüft die Prüfsumme.
5. **Zwischen verschiedenen Rechnern** läuft die Datei über den Server (Relay), **Ende-zu-Ende
   verschlüsselt**: Der Server sieht nur Chiffretext unter Zufallsnamen – weder Inhalt noch Dateinamen.
   Es geht **strikt seriell**: eine Datei zur Zeit, davon immer nur ein 8-MiB-Stück im Relay; das
   nächste folgt erst, wenn der Empfänger das vorige abgeholt hat. So kann der Relay nicht volllaufen;
   zusätzlich begrenzt `DISKATLAS_RELAY_MAX_BYTES` (Standard 20 GiB, Ablage neben der Datenbank unter
   `relay/`, oder `DISKATLAS_RELAY_DIR`) den Platz. Grenze: Ein Server, der aktiv manipuliert, könnte
   dem Sender einen falschen öffentlichen Schlüssel unterschieben; gegen Mitlesen schützt das Verfahren.
6. **Abschalten:** `DISKATLAS_COPY_ENABLED=false` am Server blendet die Funktion aus.

## Schächte und Clients

Wechselschächte (Hot-Swap-Rahmen) sind Sache des jeweiligen Rechners, also des **Clients**:

1. Weboberfläche → *Konto* → *Meine Clients*. Standard ist **„Keine Wechselschächte“**; dann stehen
   die Laufwerke dieses Clients einfach in der Liste.
2. Haken entfernen, **Anzahl der Schächte** eintragen, *Speichern*.
3. *Schächte einrichten*: im Assistenten für jeden Schacht die Platte herausziehen und wieder
   einstecken – der Port wird automatisch zugeordnet. (Der Agent muss laufen und SATA-Ports
   melden: Linux, `diskatlas agent`.) Zum Schluss *Speichern*.

Im Dashboard erscheint je Client mit Wechselschächten ein eigener Schachtblock. Nach dem
Update übernimmt der passende Client seine frühere Zuordnung beim ersten Heartbeat selbst. Über
*Client* im Filter (Dashboard und Dateisuche) suchst du nach dem Rechner, an dem eine Platte
zuletzt hing.

## Aktualisieren

Neue Version: Tag `vX.Y.Z` pushen → GitHub baut das Image → in Unraid den Container *aktualisieren*.
Die Datenbank wird beim Start automatisch migriert. Das Agent-Programm durch die neue Datei aus dem
Release ersetzen (bzw. aus dem Quellcode: `git pull` und `pip install -e .`). **Server und Agenten sollten dieselbe Version haben.**

## Sicherung

Alles Wichtige liegt in `/mnt/user/appdata/diskatlas/`: die Datenbank `diskatlas.db` (SQLite) und
der Ordner `attachments/` mit den hochgeladenen Löschprotokollen (`relay/` enthält nur kurzzeitig
verschlüsselte Stücke und muss nicht gesichert werden). Den ganzen Ordner in die
Unraid-Sicherung aufnehmen (z. B. mit dem Plugin *Appdata Backup*, das den Container dafür kurz
stoppt). Bei laufendem Container gehören `diskatlas.db-wal` und `diskatlas.db-shm` dazu; einzeln
kopiert fehlen sonst die jüngsten Änderungen.
