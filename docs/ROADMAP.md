# DiskAtlas – Roadmap

> **Wozu diese Datei?** Sie zeigt, was erledigt ist, was offen ist und **in welcher Reihenfolge**
> wir es angehen. Details zu jeder Änderung stehen im [CHANGELOG](CHANGELOG.md), Architektur und
> Entscheidungen in [PROJECT.md](../PROJECT.md).
>
> **Pflege:** Erledigte Punkte werden hier abgehakt (mit Version), neue Wünsche einsortiert. Die
> Reihenfolge legt der Betreiber fest.

| | |
|---|---|
| **Aktuelle Version** | 0.7.0 (27.09.2026) |
| **Nächster Meilenstein** | M1 – 0.7.0 im echten Betrieb prüfen |
| **Zuletzt aktualisiert** | 25.10.2026 |

**Zeichen:** ✅ erledigt · 🔜 als Nächstes · ⏳ geplant · 💡 Idee, noch nicht eingeplant ·
👤 Aufgabe des Betreibers (kein Code)

---

## Reihenfolge auf einen Blick

| # | Meilenstein | Version | Inhalt | Stand |
|---|---|---|---|---|
| M0 | Betreiber-Aufgaben | – | Unraid auf 0.7.0, SignPath-Antrag, Beispiel-PDF von nwipe | 👤 offen |
| M1 | 0.7.0 im echten Betrieb prüfen | 0.7.x | „Datei anfordern“ und Relay mit echten Rechnern, Windows auf echter Hardware, Reverse-Proxy | 🔜 |
| M2 | Leere Platten und Löschprotokolle | 0.8 | Label für leere Platten, nwipe-PDF an Platte hängen, automatisch zuordnen | ⏳ |
| M3 | Bedienung und Überblick | 0.9 | „Jetzt neu scannen“, Agenten-Übersicht, Kopierfortschritt, Schlüssel-Fingerabdruck, Fortsetzen im Relay, Export | ⏳ |
| M4 | Große Bestände und Auswertung | 1.0 | Volltextsuche, Doubletten mit Prüfsumme, Verlaufsdiagramme, SMART-Warnungen, Hot-Plug per Ereignis | ⏳ |
| – | Ideen | – | Cloud-Betrieb, fremde Platten am eigenen Rechner nutzen | 💡 |

**Warum diese Reihenfolge?**

1. **M1 zuerst:** 0.7.0 hat viel Neues gebracht, das auf Anweisung des Servers Dateien liest und
   schreibt (Kopieren, Relay). Bisher ist das nur mit automatischen Tests und Ordnern als „Platten“
   geprüft. Bevor neue Funktionen dazukommen, soll es mit echten Platten und zwei echten Rechnern
   laufen.
2. **M2 danach**, weil es deine neuen Wünsche sind und beide zusammengehören: Nach einem nwipe-Lauf
   ist die Platte leer. Das Label für leere Platten kommt zuerst (klein, für sich nützlich), dann die
   Protokolle, zuletzt deren automatische Zuordnung.
3. **M3 und M4** sind Komfort und Wachstum. Sie werden erst dringend, wenn mehr Rechner, Benutzer
   oder Dateien dazukommen.

---

## M0 – Betreiber-Aufgaben 👤

Kein Code; wartet auf dich.

- [ ] **Unraid auf 0.7.0 aktualisieren.** Danach als **Admin** anmelden (früher „Master“, das
      Passwort bleibt). Das Passwortfeld der Vorlage darf leer bleiben.
- [ ] **Einrichtung nach dem Umstieg auf Mehrbenutzer** (seit 0.5.0), falls noch nicht geschehen:
      je Rechner unter *Konto* einen Client anlegen und dessen Token im Agenten eintragen; unter
      *Verwaltung → Herrenlose Platten* den Altbestand (46 Platten) deinem Benutzer übergeben.
- [ ] **SignPath-Antrag** für die Signatur der Windows-Datei
      ([CODE_SIGNING.md](CODE_SIGNING.md)). Der Build ist vorbereitet, es fehlen die Zugangsdaten.
- [ ] **Ein echtes nwipe-PDF bereitstellen** (gern mit geschwärzten Angaben). Gebraucht für M2.3,
      damit die automatische Zuordnung an echten Dateien statt an Vermutungen gebaut wird.

## M1 – 0.7.0 im echten Betrieb prüfen 🔜 (0.7.x)

Ziel: Was es schon gibt, funktioniert mit echter Hardware. Gefundene Fehler werden als 0.7.x
behoben.

- [ ] **Datei anfordern an einem Rechner:** Datei und Ordner von einer eigenen Platte auf eine
      andere kopieren; Namenskonflikt (`name (1).ext`); Abbrechen während des Kopierens; Platte
      mitten im Kopieren abziehen.
- [ ] **Relay zwischen zwei Rechnern** (z. B. Linux ↔ Windows): kleine und große Datei (> 10 GB),
      Anfrage mit Rückfrage an den Besitzer, Ziel-Rechner zwischendurch aus, Server-Neustart während
      der Übertragung. Dabei beobachten, dass der Relay-Ordner nie mehr als ein Stück enthält.
- [ ] **Windows auf echter Hardware:** Erkennung und SMART (mit und ohne Administratorrechte),
      Tray mit dem Haken *„Angeforderte Dateien kopieren erlauben“*, Pfadschutz unter NTFS
      (Laufwerksbuchstaben, Junctions).
- [ ] **Hinter dem Reverse-Proxy (SWAG):** Die neue Prüfung gegen Fremdseiten-Anfragen darf
      Formulare nicht blockieren (Kopfzeile `X-Forwarded-Host`).
- [ ] **Hinweis „Bitte Platte anschließen“:** kommt die Tray-Meldung an, und nur einmal am Tag?

## M2 – Leere Platten und Löschprotokolle ⏳ (0.8)

### M2.1 Label für leere Platten (neuer Wunsch)

**Ziel:** Komplett leere Platten bekommen automatisch ein Label. Der Name ist **je Benutzer** in den
Einstellungen festlegbar, Standard **„leer“**.

**Vorschlag:**
- **Wann ist eine Platte „komplett leer“?** Wenn der Agent (a) **keine Partition und kein
  Dateisystem** findet (der Normalfall nach nwipe) oder (b) **jedes** Dateisystem eingehängt und
  indiziert ist und zusammen **0 Dateien** enthält. Ausgeschlossene Ordner wie `lost+found` zählen
  nicht. Platten mit unlesbaren Dateisystemen (verschlüsselt, unbekannt, nicht eingehängt) gelten
  **nicht** als leer: lieber kein Label als ein falsches.
- **Einstellung:** *Konto → Einstellungen* bekommt das Feld „Label für leere Platten“
  (Standard „leer“; ein leeres Feld schaltet die Funktion ab). Es gilt für die eigenen Platten.
  Herrenlose Platten bekommen kein Label.
- **Ablauf:** Bei jeder Meldung und nach jeder Indizierung prüft der Server die Platte. Fehlt dem
  Benutzer das Label, wird es angelegt und angeheftet.
- **Nebenbefund, wird mit erledigt:** Nach dem Löschen findet die Dateisuche heute noch die
  **alten Dateien**. Verschwundene Partitionen werden nur als „nicht vorhanden“ markiert, ihr Index
  bleibt erhalten. Für gelöschte Platten muss der alte Index aus Suche, Browser und Zählern
  verschwinden.

**Fertig, wenn:** eine frisch gelöschte Platte nach dem Anstecken das Label trägt und keine alten
Dateien mehr in der Suche auftauchen; eine Platte mit LUKS oder unbekanntem Dateisystem kein Label
bekommt; das Label in den Einstellungen umbenannt und abgeschaltet werden kann.

**Entschieden (25.10.2026):**
- Das Label **verschwindet wieder**, sobald Dateien auf der Platte sind – aber nur, wenn die
  Automatik es gesetzt hat. Von Hand gesetzte Labels fasst sie nie an.
- Ändert man den Namen in den Einstellungen, wird das bestehende Label **umbenannt**.
- Eine Platte mit Partition, aber **ohne Dateisystem** zählt als leer.

### M2.2 Löschprotokoll (PDF) an die Platte hängen (neuer Wunsch)

**Ziel:** Das PDF-Protokoll von nwipe (ab nwipe 0.35 bzw. in ShredOS als Löschzertifikat je Platte)
hochladen und dem Datenträger zuordnen, mit Verlauf: mehrere Protokolle je Platte.

**Vorschlag:**
- **Plattenseite**, neuer Abschnitt *Löschprotokolle*: Datei wählen, hochladen. Die Liste zeigt
  Datum, Dateiname, Größe und wer hochgeladen hat; ein Klick lädt das PDF herunter.
- **Ablage:** Ordner `attachments/` neben der Datenbank (Docker: `/data/attachments`), Dateien unter
  Zufallsnamen, Angaben dazu in einer neuen Tabelle (Platte, Dateiname, Größe, Prüfsumme, Datum).
- **Sicherheit:** nur echte PDFs (Dateianfang `%PDF-`), höchstens 20 MB, Auslieferung nur als
  Download mit sicheren Kopfzeilen (keine Anzeige im Seitenkontext). Hochladen und Löschen darf
  nur der Besitzer (und der Admin), ansehen jeder, der die Platte sehen darf.
- **Sicherung:** Der Ordner `attachments/` gehört ins Backup; die Anleitung
  ([UNRAID.md](UNRAID.md) › *Sicherung*) wird ergänzt.
- Wird eine Platte gelöscht, fallen ihre Protokolle mit weg.

**Fertig, wenn:** sich ein nwipe-PDF an einer Platte hochladen, herunterladen und löschen lässt;
Freigegebene es sehen, aber nicht ändern; Fremde es nicht abrufen können; andere Dateitypen und
zu große Dateien abgewiesen werden.

**Entschieden (25.10.2026):**
- Bei einem **Besitzwechsel** bleiben die Protokolle an der Platte (sie gehören zur Platte, nicht
  zum Besitzer); Notizen und Labels entfallen weiter wie bisher.
- Benutzer mit **Lesefreigabe** sehen und laden die Protokolle, ändern sie aber nicht.

### M2.3 Protokolle automatisch zuordnen

**Ziel:** Weniger Klickarbeit, wenn mit ShredOS viele Platten auf einmal gelöscht wurden.

**Vorschlag:**
- **Sammel-Upload** auf einer eigenen Seite: mehrere PDFs auf einmal (auch vom iPhone). Der
  Server liest **Seriennummer, Modell, Methode, Ergebnis und Zeitpunkt** aus dem PDF und ordnet es
  der passenden **eigenen** Platte zu. Was sich nicht zuordnen lässt, wählst du von Hand.
- Die Plattenseite zeigt dann „**Zuletzt gelöscht** am …, Methode …, Ergebnis …“.
  **„Fehlgeschlagen“** ist deutlich hervorgehoben. Optional ein Filter „gelöscht / nicht gelöscht“
  im Dashboard.
- Technik: Text aus dem PDF lesen (z. B. `pypdf`, reine Python-Bibliothek). Kein Programm aus dem
  PDF wird ausgeführt.

**Voraussetzung:** ein echtes nwipe-PDF (M0); Felder und Aufbau sind erst daran verlässlich.

## M3 – Bedienung und Überblick ⏳ (0.9)

- [ ] **Knopf „Jetzt neu scannen“** auf der Plattenseite. Der Agent kann den Auftrag schon
      ausführen; es fehlen Knopf und Server-Route.
- [ ] **Agenten-Übersicht:** je Client letzter Heartbeat, Programmversion, „Kopieren erlaubt“
      ja/nein. Heute steht unter *Konto* nur „zuletzt gemeldet“.
- [ ] **Schlüssel-Fingerabdruck** je Client anzeigen und vergleichen. Das schließt die bekannte
      Lücke des Relays: Ein manipulierter Server könnte einen falschen Schlüssel unterschieben.
- [ ] **Fortschritt laufender Kopien** auf der Anfragen-Seite (der Wert wird schon gemeldet) und
      **Relay-Belegung** für den Admin.
- [ ] **Relay: Fortsetzen statt Neustart.** Bricht eine Übertragung ab, beginnt sie heute beim
      nächsten Versuch von vorn.
- [ ] **CSV/JSON-Export** von Platten und Dateilisten.

## M4 – Große Bestände und Auswertung ⏳ (1.0)

- [ ] **Volltextsuche** (SQLite FTS5 / PostgreSQL) für Indizes mit vielen Millionen Dateien.
- [ ] **Doubletten mit Prüfsumme** (der Agent liest nur Kandidaten gleicher Größe) für sichere
      Treffer.
- [ ] **Diagramme:** Belegung und Temperatur über die Zeit, SMART-Trends.
- [ ] **Benachrichtigung bei SMART-Verschlechterung** (E-Mail/Push).
- [ ] **Hot-Plug per Ereignis** (udev / WMI) zusätzlich zur regelmäßigen Abfrage.

## Ideen 💡 (noch nicht eingeplant)

- Betrieb in der Cloud (gleicher Container, PostgreSQL als Dienst).
- Eine **fremde** Platte, die an deinem eigenen Rechner steckt, direkt als Quelle für „Datei
  anfordern“ nutzen. Heute läuft das bewusst nur über die Clients des Besitzers.

---

## Erledigt ✅

| Version | Datum | Wichtigste Inhalte |
|---|---|---|
| **0.7.0** | 27.09.2026 | Dateibrowser mit Ordnern und Checkboxen; **„Datei anfordern“** mit Kopierberechtigung je Freigabe (nie / nachfragen / immer), Zustimmung des Besitzers, Hinweis „Platte anschließen“; Agent kopiert nur mit Haken am Rechner, mit Pfadschutz; **Relay** zwischen Rechnern, Ende-zu-Ende verschlüsselt, seriell, mit begrenztem Platz; Benutzermenü; „Master“ heißt „Admin“; Startpasswort nur beim ersten Start; Schutz vor Fremdseiten-Anfragen |
| 0.6.0 | 27.09.2026 | Schächte je Client (Anzahl, Assistent, „Keine Wechselschächte“); Filter und Gruppierung „zuletzt an Client“ |
| 0.5.1 | 27.09.2026 | Dauer-Neuindex der Systemplatte behoben (Schwellen, Mindestabstand) |
| 0.5.0 | 26.09.2026 | **Mehrbenutzer:** Konten mit Freischaltung, Clients mit eigenem Token, Platten gehören Benutzern, Lesefreigabe je Platte, Übernahme nur mit Zustimmung; englisches README |
| 0.4.0 | 26.09.2026 | Agent als Tray-Programm (Windows/Linux, fertige Dateien, smartctl.exe), lokaler Betrieb ohne Server, Programmsymbol, MIT-Lizenz, SignPath vorbereitet |
| 0.3.0 | 26.09.2026 | iPhone-Web-App mit Barcode-Scanner, Anmeldung per Passwort, Docker-Image auf ghcr.io und Unraid-Vorlage |
| 0.2.0 | 26.09.2026 | Hersteller/Serie, Dateisystem-Filter, Auto-Einhängen, Systemplatten ausblenden, Hot-Swap-Schächte, Änderungserkennung, Volume umbenennen, Doubletten (Name + Größe, Ordner) |
| 0.1.0 | 23.09.2026 | Erkennung (Linux/Windows), SMART, Belegung, Dateiindex, Labels, Dashboard, Dateisuche, REST-API, SQLite/PostgreSQL, Docker, CI |

Aus der alten Roadmap in PROJECT.md §8 sind damit erledigt: Login (0.3.0/0.5.0), Docker-Image und
Unraid-Vorlage (0.3.0), Duplikatsuche nach Name und Größe (0.2.0). Der letzte Heartbeat je Client
wird angezeigt (Konto-Seite); die vollständige Agenten-Übersicht steht unter M3.
