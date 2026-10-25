# DiskAtlas – Roadmap

> **Wozu diese Datei?** Sie zeigt, was erledigt ist, was offen ist und **in welcher Reihenfolge**
> wir es angehen. Details zu jeder Änderung stehen im [CHANGELOG](CHANGELOG.md), Architektur und
> Entscheidungen in [PROJECT.md](../PROJECT.md).
>
> **Pflege:** Erledigte Punkte werden hier abgehakt (mit Version), neue Wünsche einsortiert. Die
> Reihenfolge legt der Betreiber fest.

| | |
|---|---|
| **Aktuelle Version** | 0.9.1 (26.10.2026) |
| **Nächster Meilenstein** | MV – Vertrauen und Datenschutz (parallel: M1 im echten Betrieb) |
| **Zuletzt aktualisiert** | 26.10.2026 |

**Zeichen:** ✅ erledigt · 🔜 als Nächstes · ⏳ geplant · 💡 Idee, noch nicht eingeplant ·
👤 Aufgabe des Betreibers (kein Code)

---

## Reihenfolge auf einen Blick

| # | Meilenstein | Version | Inhalt | Stand |
|---|---|---|---|---|
| M0 | Betreiber-Aufgaben | – | Unraid auf 0.9.1 mit Datenpfad `/mnt/cache`, Altbestand importieren, SignPath-Antrag, Beispiel-PDF von nwipe | 👤 offen |
| M1 | Im echten Betrieb prüfen | 0.9.x | „Datei anfordern“ und Relay mit echten Rechnern, Windows auf echter Hardware, Reverse-Proxy | 🔜 |
| M2 | Leere Platten und Löschprotokolle | 0.8 | Label für leere Platten ✅, nwipe-PDF an Platte hängen ✅, automatisch per Seriennummer zuordnen ✅; Methode/Ergebnis auslesen (wartet auf Beispiel-PDF) | ✅ bis auf Rest |
| M3 | Bedienung und Überblick | 0.8 | „Jetzt neu scannen“, Agenten-Übersicht, Kopierfortschritt, Schlüssel-Fingerabdruck, Fortsetzen im Relay, Export | ✅ umgesetzt |
| **MV** | **Vertrauen und Datenschutz** | 1.0 | Admin sieht fremde Daten nur mit Erlaubnis; an fremden Platten wird nie etwas gelöscht oder geändert; Übergabe ganzer Bestände nur mit Zustimmung; Prüfprotokoll; Sicherheitsprüfung | 🔜 als Nächstes (vor M4) |
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
3. **MV vor M4 (entschieden):** Sobald andere Personen DiskAtlas und den Relay
   nutzen, ist Vertrauen wichtiger als neue Auswertungen. MV ändert die Rechte des Admins
   grundlegend; das sollte vor weiteren Funktionen stehen, die auf diesen Rechten aufbauen.
4. **M3 und M4** sind Komfort und Wachstum. Sie werden erst dringend, wenn mehr Rechner, Benutzer
   oder Dateien dazukommen.

---

## M0 – Betreiber-Aufgaben 👤

Kein Code; wartet auf dich.

- [x] **Unraid auf 0.7.0+ aktualisiert**, Anmeldung als Admin (Server lief am 25.10. mit 0.9.0).
- [ ] **Unraid auf 0.9.1 aktualisieren und Datenpfad umstellen:** Container stoppen, Pfad *Daten*
      von `/mnt/user/appdata/diskatlas` auf `/mnt/cache/appdata/diskatlas`, starten. Behebt die
      „database is locked“-Fehler beim Indizieren der Systemplatte (Agent muss nicht neu).
- [x] **Einrichtung nach dem Umstieg auf Mehrbenutzer:** Client „LinuxMint“ angelegt, Agent meldet
      sich (Stand 25.10.).
- [ ] **Altbestand importieren** (46 Platten aus der früheren lokalen Datenbank): Werkzeug ist da
      (ab 0.9.0: *Verwaltung → Altbestand importieren*, siehe [UNRAID.md](UNRAID.md)); einmal ausführen
      – in dein eigenes Konto. (Mit MV.3 importiert der Admin später nur noch mit Zustimmung des
      Empfängers.)
- [ ] **SignPath-Antrag** für die Signatur der Windows-Datei
      ([CODE_SIGNING.md](CODE_SIGNING.md)). Der Build ist vorbereitet, es fehlen die Zugangsdaten.
- [ ] **Ein echtes nwipe-PDF bereitstellen** (gern mit geschwärzten Angaben). Gebraucht für den Rest von M2.3 (Methode und Ergebnis),
      damit die automatische Zuordnung an echten Dateien statt an Vermutungen gebaut wird.

## M1 – Im echten Betrieb prüfen 🔜 (0.9.x)

Ziel: Was es schon gibt (seit 0.7: Datei anfordern, Relay; seit 0.8/0.9: Löschprotokolle, Import),
funktioniert mit echter Hardware. Gefundene Fehler werden als 0.9.x behoben.

- [ ] **Indizierung der Systemplatte nach dem Update auf 0.9.1** läuft durch und das Banner
      verschwindet; *Verwaltung → Server-Meldungen* bleibt ohne „database is locked“.

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
- [x] **Hängende Indizierung der Systemplatte** (gemeldet am 25.10.) ✅ behoben, in 0.8.0:
      abgebrochene Läufe werden erkannt, der Agent versucht es nach 10 min erneut.
- [x] **Server antwortete beim Indizieren der Systemplatte mit Fehler 500** ✅ (0.9.1): Ursache
      „database is locked“ – große Löschungen in einer Transaktion, verstärkt durch `/mnt/user`
      (FUSE). Jetzt Aufräumen im Hintergrund in kleinen Schritten; Datenpfad
      `/mnt/cache/appdata/diskatlas` empfohlen.
- [x] **Indizierungs-Banner verdeckte das Benutzermenü** ✅ (0.9.1): jetzt kompakte Anzeige in der
      Kopfleiste (pulsierender Punkt, Plattenname, Dateizahl, Tooltip „nicht abziehen“).
- [ ] **Hinweis „Bitte Platte anschließen“:** kommt die Tray-Meldung an, und nur einmal am Tag?

## M2 – Leere Platten und Löschprotokolle ⏳ (0.8)

### M2.1 Label für leere Platten ✅ (umgesetzt, in 0.8.0)

**Ziel:** Komplett leere Platten bekommen automatisch ein Label. Der Name ist **je Benutzer** in den
Einstellungen festlegbar, Standard **„leer“**.

**Vorschlag:**
- **Wann ist eine Platte „komplett leer“?** Wenn der Agent (a) **keine Partition und kein
  Dateisystem** findet (der Normalfall nach nwipe) oder (b) **jedes** Dateisystem eingehängt und
  indiziert ist und zusammen **0 Dateien** enthält. Ausgeschlossene Ordner wie `lost+found` zählen
  nicht. Platten mit unlesbaren Dateisystemen (verschlüsselt, unbekannt, nicht eingehängt) gelten
  **nicht** als leer: lieber kein Label als ein falsches.
- **Einstellung:** Seite *Labels* → „Label für leere Platten“ (Standard „leer“; ein leeres Feld
  schaltet die Funktion ab), die Konto-Seite zeigt den Wert mit Link dorthin. So funktioniert es
  auch im lokalen Betrieb ohne Anmeldung. Es gilt für die eigenen Platten; herrenlose Platten
  bekommen kein Label.
- **Absicherung:** Ein Index mit 0 Dateien bei deutlich belegtem Speicher (mehr als 1 GiB + 0,1 %
  der Größe) gilt als „unbekannt“, nicht als leer. An deinen echten Daten geprüft: 34 frisch
  formatierte NTFS-Platten (128–372 MB Verwaltungsdaten) und 6 Platten ohne Partition wären
  „leer“, 5 belegt, 1 unbekannt.
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

### M2.2 Löschprotokoll (PDF) an die Platte hängen ✅ (umgesetzt, in 0.8.0)

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

### M2.3 Protokolle automatisch zuordnen ✅ (Grundstufe umgesetzt, in 0.8.0)

**Ziel:** Weniger Klickarbeit, wenn mit ShredOS viele Platten auf einmal gelöscht wurden.

**Umgesetzt, ohne Beispiel-PDF:**
- Neue Seite **Löschprotokolle** (Hauptmenü): bis zu 50 PDFs auf einmal, auch vom iPhone. Der
  Server liest den Text (pypdf, nur die ersten Seiten) und sucht darin die **Seriennummer einer
  Platte, die du ändern darfst**. Verglichen wird ohne Leer- und Sonderzeichen; bei
  ineinander enthaltenen Seriennummern gewinnt die längere. Genau ein Treffer: Das Protokoll hängt
  an der Platte. Kein oder mehrere Treffer: Es bleibt **offen** und wird dort per Auswahlliste
  zugeordnet oder verworfen. Offene Protokolle sieht nur, wer sie hochgeladen hat.
- Das **Erstellungsdatum** kommt aus den PDF-Metadaten, die Plattenseite zeigt es und markiert
  Protokolle mit „✓ Seriennummer“. Beim Einzel-Upload gibt es einen Hinweis, wenn die
  Seriennummer der Platte nicht im PDF steht.
- Bewusst ohne Annahmen über den Aufbau des nwipe-PDFs: Es wird nur nach den eigenen
  Seriennummern gesucht.

**Offen (braucht ein echtes nwipe-PDF, M0):**
- [ ] **Methode und Ergebnis** auslesen, „Zuletzt gelöscht am … – Ergebnis …“ auf der Plattenseite,
      **„fehlgeschlagen“** deutlich hervorgehoben, Filter „gelöscht / nicht gelöscht“ im Dashboard.

## M3 – Bedienung und Überblick ✅ (umgesetzt, in 0.8.0)

- [x] **Knopf „Jetzt neu scannen“** auf der Plattenseite ✅ (in 0.8.0). Dabei behoben:
      Umbenennen war im Server-Betrieb seit 0.6.0 gesperrt.
- [x] **Agenten-Übersicht** ✅ (in 0.8.0): je Client online/offline, Version (mit
      „veraltet“), System, „Kopieren erlaubt“, Fingerabdruck.
- [x] **Schlüssel-Fingerabdruck** ✅ (in 0.8.0): Der sendende Agent merkt sich den
      Schlüssel jedes Zielrechners beim ersten Senden und verweigert später einen anderen
      (Einstellungsfenster → *Sicherheit*: eigener Fingerabdruck, bekannte Ziele, „vergessen“).
      Rest-Lücke: Beim allerersten Senden muss man dem Server vertrauen oder die Fingerabdrücke
      an beiden Rechnern vergleichen.
- [x] **Fortschritt laufender Kopien** und **Relay-Belegung** für den Admin ✅ (in 0.8.0).
- [x] **Relay: Fortsetzen statt Neustart** ✅ (in 0.8.0).
- [x] **CSV/JSON-Export** von Platten und Dateilisten ✅ (in 0.8.0).

## MV – Vertrauen und Datenschutz 🔜 (neu, 25.10.2026; vor M4)

**Ziel (Wunsch des Betreibers):** Wer DiskAtlas mitbenutzt (auch für Relay und Dateiaustausch),
muss sich darauf verlassen können, dass seine Daten privat bleiben und an seinen Platten nichts
kaputtgehen kann. Die Schutzmechanismen müssen klar im Code stehen, mit Tests belegt sein und
einer unabhängigen Code-Prüfung standhalten.

**Heutiger Stand (ehrlich):** Der Admin ist bisher „unbeschränkt“ (`Viewer.unrestricted` in
`services/authz.py`): Er sieht und ändert alle Platten, Dateien, Labels und Freigaben aller
Benutzer. Clients und Aufträge anderer sieht er nicht. Dateien auf Platten löscht DiskAtlas nirgends;
der Agent schreibt nur neue Dateien in Zielordner eigener Platten (nie überschreiben) und ändert
Bezeichnungen nur im Auftrag des Besitzers.

### MV.1 Der Admin ist Verwalter, nicht Mitleser

- [ ] Die Admin-Rolle umfasst nur noch die **Benutzerverwaltung**: Anträge freischalten oder
      ablehnen, Konten sperren, Server-Zustand (Relay-Belegung, Versionen), herrenlose Platten.
      Er sieht **keine** Platten, Dateien, Labels, Clients, Freigaben, Anfragen oder Protokolle
      anderer Benutzer und ändert oder verschiebt nichts davon.
- [ ] **Freiwillige Vollmacht je Benutzer:** Unter *Konto* kann ein Benutzer ausdrücklich
      einstellen „Der Admin darf meine Daten sehen und verwalten“. Standard aus, jederzeit
      widerrufbar, der Admin sieht nur Bestände mit Vollmacht. Der Betreiber selbst, der zugleich
      Admin ist, behält seine eigenen Daten natürlich.
- [ ] **Umsetzung „sicher durch Bauart“:** Die Sonderrechte stecken heute an einer Stelle
      (`Viewer.unrestricted`). Sie werden ersetzt durch eine zentrale Prüfung „wem gehört das, und
      gibt es eine Freigabe oder Vollmacht?“, die jede Abfrage und jede Änderung durchläuft
      (Grundsatz: verweigern, wenn nichts ausdrücklich erlaubt). Der bestehende Leck-Test über
      alle Seiten und API-Routen wird auf den Admin ausgeweitet: Er muss bei fremden Daten
      dasselbe sehen wie ein Fremder (404).

### MV.2 Nie etwas an fremden Platten löschen oder ändern

- [ ] **Schreib-Erlaubnisliste im Agenten** statt verteilter Einzelprüfungen. Der Agent kennt
      genau drei schreibende Vorgänge:
  - eine **neue** Datei im Zielordner einer eigenen Kopie anlegen (nie überschreiben);
  - eigene Teildateien `.diskatlas-*.part` löschen;
  - die Bezeichnung eines Volumes ändern, nur im Auftrag des Besitzers.

  Alles andere (Löschen, Umbenennen oder Ändern bestehender Dateien, Rechte, Zeitstempel fremder
  Dateien) gibt es im Code schlicht nicht, und ein Test sichert ab, dass keine weitere schreibende
  Dateisystem-Funktion hinzukommt.
- [ ] **Doppelte Prüfung:** Der Server gibt Aufträge nur an Clients des Besitzers. Der Agent
      prüft selbst gegen seinen eigenen Stand, dass die Zielplatte seinem Benutzer gehört und kein
      Systemvolume ist; ein gefälschter Auftrag vom Server läuft damit ins Leere.
- [ ] **Datenbank-Seite:** Auch Namen, Notizen, Labels, Lagerorte und Löschprotokolle fremder Platten
      ändert niemand außer dem Besitzer (mit MV.1 auch nicht mehr der Admin). Freigaben bleiben
      lesend.

### MV.3 Ganze Bestände übergeben – nur mit Zustimmung

- [ ] **Übergabe von Benutzer zu Benutzer:** Ein Besitzer gibt einzelne oder alle Platten (samt
      Dateiindex, Labels, Notizen, Protokollen) an einen anderen Benutzer ab. Der Empfänger stellt ein,
      ob er Übergaben **nie**, **nach Rückfrage** oder **immer** annimmt (wie die
      Kopierberechtigung). Ohne Annahme passiert nichts.
- [ ] **Zuordnung von Client zu Client:** Ein Benutzer ordnet Platten einem seiner anderen Clients
      zu (z. B. nach einem Rechnerwechsel), inklusive Schachtzuordnung.
- [ ] **Import von Altbeständen** (wie am 25.10. mit deinen 46 Platten): nur noch in das eigene
      Konto oder mit Zustimmung des Empfängers, nicht mehr vom Admin in fremde Konten.

### MV.4 Nachvollziehbarkeit und Prüfung

- [ ] **Prüfprotokoll** für jeden Benutzer: wer wann auf seine Daten zugegriffen oder etwas geändert
      hat (Freigaben, Vollmacht, Übergaben, Admin-Handlungen). Einträge kann niemand löschen, auch
      nicht der Admin.
- [ ] **Bedrohungsmodell** in `docs/SECURITY.md`: wer was sehen und tun kann, welche Sperren das
      sichern, und was sie **nicht** leisten (siehe unten).
- [ ] **Sicherheitsprüfung** des Codes vor dem Release (automatisierte Prüfung plus gezielte
      Durchsicht aller Stellen, die Rechte prüfen oder Dateien schreiben).

### Was Verschlüsselung leisten kann – und was nicht

Die Sperren aus MV.1–MV.3 schützen gegen alles, was über die Weboberfläche, die API und die Agenten
läuft, also auch gegen einen neugierigen Admin. Wer dagegen **den Server selbst betreibt** (Zugriff
auf den Container oder die Datei `diskatlas.db`), kann eine Datenbank grundsätzlich lesen. Echte
Abschirmung auch davor ginge nur mit Verschlüsselung, deren Schlüssel ausschließlich auf den
Rechnern der Benutzer liegen:

- **Heute schon so:** Dateien im Relay sind Ende-zu-Ende verschlüsselt (der Server sieht weder
  Inhalt noch Dateinamen).
- **Möglich als Ausbaustufe (MV.5, 💡):** Dateinamen und Pfade im Index verschlüsselt speichern, mit
  Schlüsseln nur in den Agenten. Preis dafür: Die Suche müsste in den Agenten bzw. im Browser mit
  dem Schlüssel des Benutzers laufen, und die Weboberfläche dürfte nicht mehr vom selben Server
  kommen, dem man misstraut. Das ist ein großer Umbau, deshalb zuerst MV.1–MV.4.

**Entschieden (25.10.2026):**
- MV kommt **vor M4**.
- Der Admin sieht **herrenlose** Platten weiterhin und ordnet sie zu – aber nur an Benutzer, die
  Übergaben annehmen.
- Der Admin darf Konten **sperren**; **löschen** nur, wenn der Benutzer zustimmt oder nichts mehr
  besitzt.

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
| **0.9.1** | 26.10.2026 | Fix „database is locked“ (Aufräumen im Hintergrund statt großer Löschungen), Indizierung als kompakte Anzeige in der Kopfleiste, Server-Meldungen in der Verwaltung, Empfehlung `/mnt/cache` für Unraid |
| 0.9.0 | 25.10.2026 | **Altbestand importieren** (Verwaltung bzw. `diskatlas db import`): Platten einer alten DiskAtlas-Datenbank in die laufende übernehmen |
| 0.8.0 | 25.10.2026 | Label „leer“ für leere Platten; **Löschprotokolle** (PDF) an Platten, Sammel-Upload mit Zuordnung per Seriennummer; „Jetzt neu scannen“; Agenten-Übersicht und gemerkte Schlüssel der Zielrechner; Kopierfortschritt; Relay setzt nach Abbruch fort; CSV/JSON-Export; Fix: hängende Indizierung, gesperrtes Umbenennen im Server-Betrieb; Roadmap und Doku in `docs/` |
| 0.7.0 | 27.09.2026 | Dateibrowser mit Ordnern und Checkboxen; **„Datei anfordern“** mit Kopierberechtigung je Freigabe (nie / nachfragen / immer), Zustimmung des Besitzers, Hinweis „Platte anschließen“; Agent kopiert nur mit Haken am Rechner, mit Pfadschutz; **Relay** zwischen Rechnern, Ende-zu-Ende verschlüsselt, seriell, mit begrenztem Platz; Benutzermenü; „Master“ heißt „Admin“; Startpasswort nur beim ersten Start; Schutz vor Fremdseiten-Anfragen |
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
