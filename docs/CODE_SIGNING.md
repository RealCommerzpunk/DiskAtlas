# Code signing policy

Free code signing provided by [SignPath.io](https://about.signpath.io/), certificate by
[SignPath Foundation](https://signpath.org/).

## What is signed

- `DiskAtlas-Agent-windows-x64.exe`, built by GitHub Actions
  ([`.github/workflows/agent.yml`](../.github/workflows/agent.yml)) from the source code in this
  repository, for version tags (`v*`) only. Every signing request is approved manually.
- Not signed by this project: the bundled `smartctl.exe` from
  [smartmontools](https://www.smartmontools.org/) (GPL v2), taken unmodified from the official
  Windows installer (see [THIRD_PARTY.md](../THIRD_PARTY.md)).

## Team roles

| Role | Members |
|---|---|
| Authors (committers) | [RealCommerzpunk](https://github.com/RealCommerzpunk) |
| Reviewers | [RealCommerzpunk](https://github.com/RealCommerzpunk) |
| Approvers | [RealCommerzpunk](https://github.com/RealCommerzpunk) |

Contributions from people not listed here are only accepted as pull requests after review. All
team members use multi-factor authentication for GitHub and SignPath.

## Privacy policy

This program will not transfer any information to other networked systems unless specifically
requested by the user or the person installing or operating it: it only sends data to the
DiskAtlas server that the user enters in its settings (mode *Connect to a DiskAtlas server*). In
the mode *This PC only*, all data stays on the computer and the web interface is only reachable
locally (`127.0.0.1`).

---

## Deutsch (Kurzfassung)

Die Windows-Programmdatei des DiskAtlas-Agenten wird kostenlos über SignPath.io signiert, das
Zertifikat stellt die SignPath Foundation aus. Signiert wird nur die aus diesem Repository per
GitHub Actions gebaute `.exe`, nur für Versions-Tags und jeweils nach manueller Freigabe; das
enthaltene `smartctl.exe` (smartmontools, GPL v2) bleibt unverändert. Alle Rollen (Autor, Prüfer,
Freigabe) liegen bei RealCommerzpunk. Datenschutz: Das Programm überträgt Daten nur an den
DiskAtlas-Server, den der Benutzer selbst einträgt; im Betrieb *Nur dieser PC* bleibt alles auf
dem Rechner.
