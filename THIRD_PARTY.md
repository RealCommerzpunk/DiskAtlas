# Fremdbestandteile

DiskAtlas selbst steht unter der [MIT-Lizenz](LICENSE). Folgende Bestandteile stammen von
anderen und stehen unter ihren eigenen Lizenzen:

| Bestandteil | Urheber | Lizenz | Verwendung |
|---|---|---|---|
| Symbol „hard-disk-remix“ (`packaging/icons/hard-disk.svg`) | [Streamline](https://github.com/webalys-hq/streamline-vectors) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | Programmsymbol, Tray-Symbol, Favicon |
| `smartctl.exe` aus [smartmontools](https://www.smartmontools.org/) | Bruce Allen, Christian Franke u. a. | [GPL v2 oder später](https://www.gnu.org/licenses/old-licenses/gpl-2.0.html) | nur in der Windows-Programmdatei, siehe [docs/AGENT.md](docs/AGENT.md#enthaltene-fremdsoftware) |

**Änderungen am Symbol:** Aus der Glyphe erzeugt `scripts/build_icons.py` das App-Symbol (weiß auf
blauem, abgerundetem Quadrat) und das Tray-Symbol (eingefärbt, mit Statuspunkt). Die Glyphe selbst
ist unverändert.

Python-Bibliotheken, die die Programmdateien mitbringen, stehen unter ihren eigenen (freien)
Lizenzen; siehe `pyproject.toml` für die Liste der Abhängigkeiten.
