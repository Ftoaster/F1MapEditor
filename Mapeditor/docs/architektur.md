# Architektur · AP25

Stand 16.10.2026. Der Editor enthält Oberfläche, Dokumentzustand und Prozessadapter.
Alle verbindlichen Strecken- und Paketregeln kommen unverändert aus dem Werkzeugkasten.
Vertrag: `../../docs/editor_schnittstelle.md` (AP24), insbesondere §8.

## Module

| Modul | Verantwortung |
|---|---|
| `project.py` | Verlustfreie Rohdefinition, Manifestfassung 1, CSV-Beilagen, atomare Dateien, Spieltest-Protokoll |
| `document.py` | Eingaben, Auswahl, QUndoStack mit tief kopierten Vorher-/Nachherständen |
| `input_geometry.py` | Sofortige unglättete Eingabezeichnung, Profile, Segmentgriffe, Konvertierungsabtastung |
| `adapter.py` | Einzige Werkzeugimporte, Vorschau/Prüfung, CLI-Befehl, ProcessJob und BuildJob |
| `paths.py` | Gemeinsamer Schreibschutz für Projekt-, Bau-, Paket- und Cacheziele |
| `process_group.py` | Windows Job Object für Bauprozess und alle Kindprozesse |
| `mapview.py` | Karte: Ebenen-Zeichnung, Gesten (Ziehen, Rahmen, Drehen), Drehgriff |
| `views.py` | Höhen-/Neigungsprofil und Querschnitt |
| `window.py` | Projektabläufe, Eigenschaften, Meldungsfilter/-sprung, lesender Logikbaum |
| `build.py` | Bauvorbereitung, Fortschritt, Abbruch, Ergebnis, Explorer und Spieltestdialog; übergibt `--umgebung` |
| `umgebung.py` | Umgebung ohne Qt: `umgebung.json` (Normalisieren, Schreiben), Kennungen, Streuen, Abstand zur Strecke (`Hindernis`) |
| `umgebung_ui.py` | Reiter „Umgebung“: Katalog, Vorschau, Bepflanzen-Einstellungen, Auswahlbox (rechts bei den Eigenschaften) |
| `__main__.py` | Editorstart, Vorschauarbeiter und eingefrorener Werkzeug-CLI-Shim |
| `appearance.py` | Lesende Schriftregistrierung bei leerer Qt-Offscreen-Schriftliste |

## Daten und Undo

Ein Projekt enthält `projekt.json`, die relativ referenzierte `definition.json`, bei Bedarf
relative CSV-Beilagen und `spieltests.json`. Manifestfelder: `schema`, `definition`,
`runde_geschlossen`, optional `thema`, `versatz`, `werkzeugkasten`, `bauen` mit
`ausgabe`, `pakete`, `traeger`. Unbekannte Felder bleiben erhalten; fremde Fassungen öffnen lesend.
Die Rohdefinition wird nicht durch aufgelöste Generatorstandards ersetzt.

`spieltests.json` ist eine Liste. Je tatsächlich gestartetem Bau: `id`, `datum`, `name`,
`ausgabe`, `bau_status`, `exitcode`, `paket`, `pakete`, `eingabe` (Schnappschuss/Hash),
Berichtsreferenzen sowie `status` und `ergebnis` für den Spieltest. `status` ist zunächst
`offen`, später `ok`, `Absturz` oder `Problem`. Startfehler und abgebrochene Bauten haben
keinen fertigen Paketpfad. Der Baustatus ist von der Spielbeobachtung getrennt.
Das Protokoll liegt außerhalb der Undo-Schnappschüsse. Ein Spieltestnachtrag schreibt nur
seine Datei und speichert dabei keine ungespeicherte Geometrie. „Speichern unter“ nimmt
bestehende Einträge samt absoluten Bauverweisen mit.

Dateien werden im Zielordner temporär geschrieben, per fsync gesichert und ersetzt.
Projekt-Speicherfehler stellen die vorherigen Projektdateien wieder her. Es gibt weiterhin
keine crashsichere Mehrdateientransaktion und keine Autosicherung.

## Umgebung (Bäume, Gebäude …)

Vertrag: `../../docs/zwischenstand/umgebung_vertrag.md`. Die Datei `umgebung.json` liegt neben `definition.json`; `Project.umgebung`
hält sie als Wörterbuch (None = keine Datei). Der Undo-Zustand ist `(raw, manifest, umgebung)`; `Document.edit` ändert nur die
ersten beiden, `edit_env` die Umgebung. Ein Schritt, der nur die Umgebung ändert, setzt `document.env_only` und erhöht
`env_revision` statt `revision`; das Hauptfenster aktualisiert dann nur Karte, Reiter, Titel und die Umgebungsprüfung (die
Generatorvorschau und die Fahrlinie bleiben gültig). Katalog, Silhouetten und die UMG_*-Prüfung laufen als Aufgaben des
Vorschauarbeiters (`umgebung_katalog`, `umgebung_vorschau`, `umgebung_pruefen`), der Katalog je Fenster einmal beim ersten Blick
auf den Reiter. Die Karte (`mapview.py`) zeichnet die Ebene aus dem Dokument (kein eigener Zustand außer Gesten) und kennt nur
den Katalogindex für Radius und Farbe; Aktionen gehen als Signale an das Hauptfenster, das sie über `Document` ausführt.
Bepflanzen (`umgebung.streuen`) ist reine Logik mit Saatwert; der Abstand zur Strecke kommt aus der Vorschaugeometrie.


## Vorschau und Prüfung

Werkzeugauflösung: Projektpfad → `MAPEDITOR_WERKZEUGKASTEN` → `werkzeugkasten/` neben der
EXE → Repository-Nachbar `ego-werkzeuge/`. Ausdrückliche Fehlkonfigurationen schlagen fehl.
Werkzeugimporte stehen ausschließlich im Adapter. Bytecode-Schreiben ist abgeschaltet.

Ein QProcess-Arbeiter bekommt die Rohdefinition, Basisordner, Rundenschluss, Versatz
und optional `check_generator`. Nach 400 ms Eingaberuhe entsteht die Vorschau mit
`pruefregeln.vorab_pruefen(..., mit_generator=False)`. „Prüfen“ / Strg+Enter aktiviert
den Generator; im Bauvorlauf liefert `check_only` nur den Prüfbericht. Die Qt-Schleife
bleibt frei. Veraltete Vorschauantworten werden anhand der Editorrevision verworfen.
Ungültige Definitionen liefern `DEF_UNGUELTIG` mit Bericht statt Verlust der Meldungen.
Offene Entwürfe liefern nur Generatorstandards und einen Editorzustand „Runde offen“.

Meldungsabbildung: `id` bleibt die stabile Regel-ID, `kurz` das sichtbare UX-Kürzel,
`schwere` wird Fehler/Warnung/Hinweis. Es gibt keine eigenen GE-/PL-Prüfregeln mehr.
`LAGE_AUSSERHALB_AO` trägt KO-01; das frühere Adapter-PL-01 bezeichnete fälschlich AO.
`nicht_geprueft` erzeugt eigene, filterbare Einträge. Filter ändern keine Prüfergebnisse.
Tooltip zeigt stabile ID, Stufe, Ist/Soll und Quelle; Bau-Meldungen tragen die Bau-ID.

Stationen der `mittellinie` entsprechen der Generatorachse ab Start/Ziel. Bei Boxenlinien
und anderen Splineachsen wird zuerst die Weltposition nach Abzug des Versatzes auf die
Vorschau zugeordnet. Hauptlinien-Stationen sind ein Fallback. Klick/Enter selektiert das
nächste Eingabeobjekt und synchronisiert Karte, Höhen-/Neigungsprofil und Querschnitt.
Die Zuordnung zur gezeichneten Linie benutzt das begrenzte Zeichengitter (ca. 1.800 Stationen).

Die rohe Eingabezeichnung aus AP22 bleibt unabhängig von der Generatorberechnung:
keine Schließkorrektur, keine Exportgeometrie. Effektive Splineform, Randbänder und
Prüfungen stammen vom Werkzeugkasten. Die private Segmenthilfe
`definition._segment_kruemmung` wird weiterhin nur im Adapter benötigt; eine öffentliche
Objekt-/Stationsschnittstelle bleibt ein Vorschlag an den Werkzeugkasten.

## Bauprozess

1. Beschreibbares Projekt, geschlossene Runde, gültige Feldeingaben, Träger und Ziele prüfen.
2. Speicher prüfen (gleicher Datenträger 4 GB; getrennt Arbeit 1,1 GB / Paket 2,9 GB).
3. Einstellungen und Projekt speichern; Generator-Vorabprüfung als separater Arbeiter.
4. Nur ohne Fehler den AP24-CLI-Befehl ausführen: `bauen --ereignisse-json --profil empfohlen
   --projekt ... --atomar --name ... --ausgabe ... --paket-ordner ... --traeger ...
   --traeger-cache ... --abbruch-datei ... --pruefsummen --arbeit-aufraeumen`.
5. Eingabe wird vom Werkzeug nach `<Arbeit>/<Name>/eingabe/` kopiert. Generator, Berichte,
   temporäre Pakete und Trägercache liegen im individuellen Bauordner. Der eigene Cache
   je Bau verhindert eine Wiederverwendung einer gleich großen ERP eines anderen Trägers.
6. Nur ein konsistentes Ergebnisereignis mit Exitcode 0, geprüftem Paket und positivem Status
   wird als Erfolg angezeigt. Exitcode 1 = Fehler, 2 = Prüf-/Paketfehler, 3 = Abbruch.

`BuildJob` verwendet getrennte QProcess-Kanäle. stdout wird inkrementell als UTF-8 und
JSON Lines gelesen, eine letzte Zeile ohne Zeilenumbruch erst bei Prozessende. stderr
geht ausschließlich ins aufklappbare Diagnoseprotokoll. Unbekannte/ungültige Ereignisse
verhindern einen Erfolgsstatus. Fortschritt stammt aus `phase.fortschritt`, keine Zeitprognose.
Die UI zeigt Live-Meldungen und danach Paketpfad, Größe, SHA-256, Prüfbericht und Bericht.

Auch der Quellstart führt den Bau über `python -B -m mapeditor --werkzeug-cli` aus.
Der Shim routet dessen Paket-Kindprozess ebenfalls durch den Editor. Das ist nötig,
weil `paket_tool.py pruefen` noch einen festen Arbeitsordner relativ zu `HIER` verwendet.
Der Adapter lädt diese CLI per runpy als Bibliothek und setzt ihre Laufzeitvariable
`HIER` auf `<Bau>/werkzeug-pruefung`; der Prüfer schreibt damit ausschließlich dort.
Ein CLI-Parameter für den Prüfordner wäre die gewünschte Werkzeug-Erweiterung.
Werkzeugquellen werden weder geändert noch in bearbeiteter Form verteilt.

Abbruch schreibt zuerst die Abbruchdatei. Der Bau wird bei QProcess.started einem nativen
Windows Job Object zugewiesen; seine Kindprozesse gehören automatisch dazu. Nach 120 s
beendet TerminateJobObject die Gruppe einschließlich tiefer Paketprüfung. Das Schließen
des Job-Handles beendet verbleibende Kinder ebenfalls (KILL_ON_JOB_CLOSE).
Falls Windows die Gruppenzuweisung verweigert, erscheint dies im Diagnoseprotokoll;
taskkill /T /F und QProcess.kill dienen dann als Fallback.
Erst nach Prozessende wird ausschließlich `_paket_tmp` dieses Bauordners entfernt und
`bau_status.json` für den harten Abbruch geschrieben. Andere Arbeitsdateien bleiben zur
Diagnose erhalten. Im kooperativen Fall übernimmt das Werkzeug seine Aufräumarbeiten.
Der Baudialog ist während des Baus modal; Projektwechsel und App-Schließen warten.

## Schreibschutz und Distribution

`writable_path` löst Pfade auf und sperrt jeden Pfad unter `steamapps/common/F1 25`,
Downloads sowie die EXE-Installation. Der lokale Distributionsordner ist auch beim
Quellstart geschützt. Schutz gilt bereits vor dem Anlegen von Projekt-/Bauordnern;
`atomic_write` prüft zusätzlich das einzelne Ziel. Speichern eines installierten
Beispielprojekts erzwingt „Speichern unter“. Der Editor setzt keine Spielordner-Freigabe.

Die AP23-onedir-Distribution enthält die vollständigen Python-Module von `strecke/`,
`ego/` und vier CLIs. `__main__` führt über `--werkzeug-cli` ausschließlich diese
mitgelieferten CLIs per runpy aus. Die verschachtelte Paketprüfung startet
`sys.executable paket_tool.py ...`; auch dieser Aufruf wird auf denselben Shim geroutet.
Es ist keine zusätzliche python.exe nötig. Ein explizit gewählter Werkzeugkasten wird
über die Prozessumgebung an Kindprozesse vererbt. Im Frozen-Fall wird kein Quell-PYTHONPATH gesetzt.
stdout/stderr werden im Shim ausdrücklich auf UTF-8 gesetzt, da der eingefrorene
Interpreter PYTHONIOENCODING allein nicht zuverlässig übernimmt. Der empfohlene Baupfad
bis einschließlich ZIP-Erzeugung benötigt keine `analyse/`-Skripte. Die erste echte
tiefe Paketprüfung deckte den inzwischen korrigierten Kodierungsfehler auf; Details in stand.md.

Installation bleibt extern: Track Installer, freier Slot, danach lesender Vergleich mit
`paket_tool.py installiert`. Paketprüfung ist keine Spielabnahme. Verteilung und
Prüfnachweise: [verteilung.md](verteilung.md), [stand.md](stand.md).

## Leistung der Karte (16.10.2026)

Messung `tests/perf/drag_benchmark.py` (offscreen, je Mausbewegung inkl. erzwungenem Neuzeichnen):

| Geste | vorher | nachher |
|---|---:|---:|
| Rahmenauswahl über 669 Punkte (Portimão-Form) | 146 ms | 15 ms |
| Gruppe aus 669 Punkten ziehen | 52 ms | 15 ms |
| Segmentgriff ziehen (Teststrecke) | 28 ms | 8 ms |

Ursachen und Maßnahmen:
- Jede Mausbewegung baute die **ganze Szene** neu (Generator-Bänder mit tausenden Punkten). Jetzt:
  Generator-Ebene wird nur bei neuer Vorschau als Pfadliste aufgebaut und als **zwischengespeichertes
  Bild** gezeichnet (neu nur bei Zoom/Pan/Größe); die Eingabe-Ebene besteht aus festen Pfad-Elementen,
  deren Pfade ersetzt werden.
- Rahmenauswahl rechnete jeden Punkt per `mapFromScene` um und löste bei jeder Bewegung eine
  komplette Neuzeichnung plus Listen-Neuauswahl (je Zeile) aus. Jetzt: Kasten einmal in Szenen-
  koordinaten, Auswahl nur bei Änderung melden, Listenauswahl in einer Operation, Auswahlwechsel
  aktualisiert nur die Hervorhebung.
- Entwürfe während einer Geste: flache Kopien statt `deepcopy` der Mittellinie; Segmentgriffe
  nutzen die Geometrie vom Gestenbeginn.
- Punkte gesammelt per `drawPoints` statt einer Ellipse je Punkt; Profile während einer Geste
  höchstens ~15× pro Sekunde neu zeichnen (Daten sofort aktuell).
