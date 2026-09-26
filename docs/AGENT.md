# Das Agent-Programm (Windows und Linux)

Der DiskAtlas-Agent ist eine einzelne Programmdatei ohne Installation. Er zeigt sich als Symbol
im Infobereich der Taskleiste; ein Klick darauf öffnet das Menü mit dem Einstellungsfenster.

| Datei (unter *Releases* auf GitHub) | System | Größe |
|---|---|---|
| `DiskAtlas-Agent-windows-x64.exe` | Windows 10/11 (64 Bit) | ca. 30 MB |
| `diskatlas-agent-linux-x86_64` | Linux Mint 22 / Ubuntu 24.04 und neuer | ca. 20 MB |

Unter Linux braucht das Programm die Desktop-Bibliotheken des Systems (GTK 3, WebKitGTK 4.1,
AyatanaAppIndicator). Auf Linux Mint und Ubuntu sind sie vorhanden, sonst:
`sudo apt install gir1.2-webkit2-4.1 gir1.2-ayatanaappindicator3-0.1`.

## Betriebsarten

Beim ersten Start öffnet sich das Einstellungsfenster. Dort wählst du eine von zwei Betriebsarten:

- **Mit DiskAtlas-Server verbinden** (z. B. Docker auf dem NAS, siehe [UNRAID.md](UNRAID.md)):
  Server-Adresse und API-Token eintragen, **Verbindung testen**, **Speichern**. Der Agent meldet
  die Festplatten an den Server; die Oberfläche kommt vom Server.
- **Nur dieser PC**: Oberfläche und Datenbank laufen im Programm selbst, ganz ohne Server.
  **Dashboard öffnen** (im Menü oder im Fenster) zeigt die Oberfläche im Browser
  (`http://127.0.0.1:8765`). Die Datenbank liegt unter `%LOCALAPPDATA%\diskatlas\diskatlas.db`
  bzw. `~/.local/share/diskatlas/diskatlas.db`.

Der Punkt am Symbol zeigt den Zustand: grün = verbunden bzw. läuft lokal, gelb = wartet bzw.
nicht eingerichtet, rot = gestört. Das Symbol ist hell (für dunkle Leisten) oder dunkel; unter
Windows richtet es sich nach der Taskleiste, unter Linux ist es hell. Umstellen im
Einstellungsfenster unter *Erweitert* → *Farbe des Symbols*. Änderungen im Einstellungsfenster übernimmt der Agent nach
dem Speichern von selbst.

## Windows

**SmartScreen-Warnung beim ersten Start** („Der Computer wurde durch Windows geschützt“): Die
Datei ist (noch) nicht digital signiert. *Weitere Informationen* → *Trotzdem ausführen*.
Die Signatur über SignPath ist vorbereitet ([CODE_SIGNING.md](CODE_SIGNING.md)); auch signiert
kann SmartScreen anfangs noch warnen, bis das Zertifikat genug Downloads gesammelt hat.

**SMART-Werte brauchen Administratorrechte.** Ohne sie kommen Modell, Seriennummer, Belegung und
Dateien trotzdem an, nur die Gesundheitswerte fehlen; das Einstellungsfenster weist darauf hin.
Das Programm zum Auslesen (`smartctl.exe`) ist bereits enthalten.

Damit der Agent bei jeder Anmeldung mit Adminrechten startet, eine geplante Aufgabe anlegen.
Vorher die Programmdatei an einen festen Ort legen (z. B. `C:\Programme\DiskAtlas\`), nicht im
Download-Ordner lassen.

1. Im Einstellungsfenster den Haken **Beim Anmelden automatisch starten** entfernen (sonst startet
   ein zweiter Agent ohne Adminrechte, und nur einer darf laufen).
2. *Aufgabenplanung* öffnen (Startmenü → „Aufgabenplanung“) → **Aufgabe erstellen …**
   (nicht „Einfache Aufgabe“).
3. Reiter **Allgemein**: Name `DiskAtlas Agent`, Haken bei **Mit höchsten Privilegien ausführen**,
   *Nur ausführen, wenn der Benutzer angemeldet ist* bleibt ausgewählt.
4. Reiter **Trigger** → *Neu …* → *Aufgabe starten:* **Bei Anmeldung**, eigener Benutzer.
5. Reiter **Aktionen** → *Neu …* → *Programm starten* → die `DiskAtlas-Agent-windows-x64.exe`
   auswählen.
6. Reiter **Bedingungen**: Haken bei *Aufgabe nur starten, falls Computer im Netzbetrieb läuft*
   entfernen (Laptops). Reiter **Einstellungen**: Haken bei *Aufgabe beenden, falls Ausführung
   länger als …* entfernen.
7. OK. Zum Ausprobieren: Rechtsklick auf die Aufgabe → **Ausführen**.

Dasselbe in einer Administrator-PowerShell (Pfad anpassen):

```powershell
$exe = "C:\Programme\DiskAtlas\DiskAtlas-Agent-windows-x64.exe"
Register-ScheduledTask -TaskName "DiskAtlas Agent" -Force `
  -Action (New-ScheduledTaskAction -Execute $exe) `
  -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME) `
  -Principal (New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest -LogonType Interactive) `
  -Settings (New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
             -ExecutionTimeLimit ([TimeSpan]::Zero))
```

Einmalig von Hand geht auch: Rechtsklick auf die Programmdatei → **Als Administrator ausführen**.

## Linux

Nach dem Download ausführbar machen: `chmod +x diskatlas-agent-linux-x86_64`. SMART braucht
root-Rechte über eine sudo-Regel nur für `smartctl` (siehe README, *SMART-Berechtigungen*);
`smartmontools` muss installiert sein (`sudo apt install smartmontools`).

## Wenn etwas nicht klappt

Menü → **Protokolle anzeigen** öffnet den Ordner mit den Protokollen:
`agent-tray.log` (Agent und Symbol) und `agent-settings.log` (Einstellungsfenster).
Unter Windows `%LOCALAPPDATA%\diskatlas`, unter Linux `~/.local/share/diskatlas`.
Die Konfiguration steht in `%APPDATA%\diskatlas\config.toml` bzw. `~/.config/diskatlas/config.toml`;
beim Speichern bleibt die vorherige Fassung als `config.toml.bak` erhalten.

## Entfernen

Das Programm installiert nichts. Zum Entfernen:

1. Im Einstellungsfenster den Haken **Beim Anmelden automatisch starten** entfernen (bzw. unter
   Windows die geplante Aufgabe `DiskAtlas Agent` in der Aufgabenplanung löschen).
2. Im Menü **Beenden**, dann die Programmdatei löschen.
3. Wer auch Einstellungen und Daten loswerden will: die Ordner `%APPDATA%\diskatlas` und
   `%LOCALAPPDATA%\diskatlas` (Windows) bzw. `~/.config/diskatlas` und `~/.local/share/diskatlas`
   (Linux) löschen. Im lokalen Betrieb liegt dort auch die Datenbank.

## Enthaltene Fremdsoftware

Die Windows-Datei enthält **smartctl.exe** aus [smartmontools](https://www.smartmontools.org/)
(unverändert aus dem offiziellen Windows-Installer, Version siehe `packaging/fetch_smartctl.py`),
lizenziert unter der **GNU General Public License v2 oder später**. smartctl ist ein eigenständiges
Programm, das der Agent nur aufruft. Lizenztext und Herkunft zeigt das Einstellungsfenster
(*Lizenz und Quellcode*); der zugehörige Quellcode (`smartmontools-<Version>.tar.gz`) liegt bei
jeder Version auf der Release-Seite neben der Programmdatei.

Das Programmsymbol stammt von [Streamline](https://github.com/webalys-hq/streamline-vectors)
(CC BY 4.0, für App- und Tray-Symbol verändert). Übersicht: [THIRD_PARTY.md](../THIRD_PARTY.md).

DiskAtlas selbst steht unter der [MIT-Lizenz](../LICENSE). Zur Signatur der Windows-Datei:
[CODE_SIGNING.md](CODE_SIGNING.md).
