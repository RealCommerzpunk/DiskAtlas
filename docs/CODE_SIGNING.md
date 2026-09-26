# Code signing policy

Free code signing provided by [SignPath.io](https://about.signpath.io/), certificate by
[SignPath Foundation](https://signpath.org/).

*Deutsch:* Die Windows-Programmdatei des DiskAtlas-Agenten wird kostenlos über SignPath.io
signiert; das Zertifikat stellt die SignPath Foundation aus.

## Was signiert wird

- `DiskAtlas-Agent-windows-x64.exe`, gebaut von GitHub Actions
  ([`.github/workflows/agent.yml`](../.github/workflows/agent.yml)) aus dem Quellcode dieses
  Repositorys, ausschließlich für Versions-Tags (`v*`). Jede Signatur wird einzeln von Hand
  freigegeben.
- Nicht von diesem Projekt signiert wird das enthaltene `smartctl.exe` aus
  [smartmontools](https://www.smartmontools.org/) (GPL v2, unverändert aus dem offiziellen
  Windows-Installer übernommen, siehe [AGENT.md](AGENT.md#enthaltene-fremdsoftware)).

## Rollen

| Rolle | Personen |
|---|---|
| Autoren (Committer) | [RealCommerzpunk](https://github.com/RealCommerzpunk) |
| Prüfer (Reviewer) | [RealCommerzpunk](https://github.com/RealCommerzpunk) |
| Freigabe (Approver) | [RealCommerzpunk](https://github.com/RealCommerzpunk) |

Änderungen von Personen außerhalb dieser Liste werden nur per Pull Request nach Prüfung
übernommen. Für GitHub und SignPath ist bei allen Beteiligten die Zwei-Faktor-Anmeldung aktiv.

## Datenschutz

Dieses Programm überträgt keine Informationen an andere vernetzte Systeme, außer an den
DiskAtlas-Server, den der Benutzer selbst in den Einstellungen einträgt (Betriebsart *Mit
DiskAtlas-Server verbinden*). Im Betrieb *Nur dieser PC* bleiben alle Daten auf dem Rechner;
die Weboberfläche ist dann nur lokal (`127.0.0.1`) erreichbar.

*English:* This program will not transfer any information to other networked systems unless
specifically requested by the user: it only sends data to the DiskAtlas server the user enters in
its settings.
