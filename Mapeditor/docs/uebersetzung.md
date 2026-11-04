# Übersetzung des Mapeditors (Englisch / Deutsch)

Stand 4.11.2026: **English First.** Englisch ist Quellsprache und Standard, Deutsch ist eine Übersetzung. Ohne
gespeicherte Wahl startet der Editor englisch; fehlt eine deutsche Übersetzung, erscheint der englische Text. Auch die
Meldungen und Prüfregeltitel des Werkzeugkastens (`ego-werkzeuge/strecke`) sind englisch zuerst. Doku, Commits und
Arbeitssprache bleiben deutsch (Entscheidung des Nutzers, 4.11.2026). Vorher (26.10.2026) war Deutsch Quelle und Standard. Bild der englischen Oberfläche (26.10.2026, nach dem Wechsel im laufenden Fenster aufgenommen, mit
Vorschau und Fahrlinie): `docs/screenshots/englisch.png`.

## Sprache wählen
- Beim Start: `--sprache en|de` (nur dieser Start), sonst die gespeicherte Wahl, sonst **Englisch**
  (`__main__.py`, `ansicht.load_language`). Die Systemsprache von Windows spielt keine Rolle.
- Menü **View → Language** (English / Deutsch). Die Wahl wird in den Benutzereinstellungen gespeichert
  (`QSettings`, Schlüssel `sprache`) und **wirkt sofort, ohne Neustart und ohne Rückfrage** (seit 26.10.2026).
- So funktioniert es (`MainWindow.retranslate`): Dasselbe Fenster baut Menüs, Werkzeugleiste, Seitenleisten, Karte,
  Reiter und Statuszeile in der neuen Sprache neu auf (`_rebuild_ui`); alte Menüs, Aktionen und Zeitgeber werden
  entsorgt. Daten und Abläufe bleiben unberührt: Dokument samt **Rückgängig-Verlauf**, ungespeicherte Änderungen,
  Vorschau, Fahrlinie (auch eine laufende Berechnung), Prüfergebnis, Kartenausschnitt (Zoom und Mitte), Station,
  gewählter Reiter, Eigenschaften-Bereich, Suchfelder, Ansichtsschalter, Teiler. Offene Fenster „Ansicht anpassen“
  und „Bedienung“ öffnen sich in der neuen Sprache neu; ein offener Baudialog ebenfalls, mit Bauname, „Was ist
  neu?“, Streckencode und Bauoptionen. Ein **laufender Bau oder eine laufende Übertragung läuft weiter** (der
  Baudialog behält dann bis zum Schließen die alte Sprache).
- Verzögerte Aufrufe auf Bedienelemente sind an ihr Zielobjekt gebunden (`QTimer.singleShot(0, self.map, …)`), damit
  sie nach einem Neuaufbau nicht die gelöschte alte Karte treffen.
- Startoption für einen Start: `python -m mapeditor --sprache en` bzw. `Mapeditor.exe --sprache en`.

## Aufbau
| Datei | Inhalt |
|---|---|
| `src/mapeditor/i18n/__init__.py` | `tr()`, `trn()`, `N_()`, `setze_sprache()`, `qlocale()`, `dezimal()`, `html()`, `rule_title()` |
| `src/mapeditor/i18n/texte.csv` | **Übersetzungstabelle** aller Oberflächentexte: Spalten `en;de;ort` (1.223 Einträge) |
| `src/mapeditor/i18n/regeln.csv` | Titel der 66 Prüfregeln des Werkzeugkastens: `id;kurz;en;de` |
| `src/mapeditor/i18n/meldungen.csv` | Meldungsvorlagen des Werkzeugkastens: `en;de;ort` (Zeilen `wort`: deutsche Datenwörter) |
| `src/mapeditor/i18n/hilfe_en.html`, `hilfe_de.html` | Bedienungshilfe (F1) je Sprache, Rückfall Englisch |
| `src/mapeditor/i18n/pflege.py` | Schlüssel im Quelltext finden, Tabelle prüfen und abgleichen |

**Warum eine gemeinsame CSV mit dem englischen Text als Schlüssel?**
- Im Code steht der englische Text lesbar und auffindbar (`tr("Save")`), wie bei Qt-`tr()`/gettext; kein künstlicher
  Schlüssel muss gepflegt werden, und Englisch bleibt automatisch wortgleich.
- Fehlt eine Übersetzung, erscheint der englische Text – die Oberfläche bricht nie.
- Eine Tabelle mit beiden Sprachen nebeneinander ist leicht zu prüfen und zu übersetzen; Semikolon und UTF-8 mit BOM,
  damit ein deutsches Excel/LibreOffice sie direkt öffnet. Mehrzeilige Texte stehen in Anführungszeichen.
- Lange Texte (Hilfe) sind eigene HTML-Dateien je Sprache statt riesiger Tabellenzellen.

### Regeln im Code
- `tr("Text")` für jeden sichtbaren Text, **englisch**; Werte über **benannte Platzhalter**:
  `tr("Project saved: {folder}", folder=pfad)` – nie Sätze aus Teilstücken zusammensetzen.
- **Mehrzahl**: `trn(n, "{n} warning", "{n} warnings")`; in der Tabelle eine Zeile `Einzahl|Mehrzahl` in beiden
  Spalten (`{n} Warnung|{n} Warnungen`).
- **Konstanten auf Modulebene** (z. B. `LABELS`, `ansicht.FEATURES`, `neigung.SIDES`) mit `N_("…")` markieren und
  erst bei der Anzeige `tr(konstante)` aufrufen; so wirkt ein Sprachwechsel ohne Neuimport.
- **Gespeicherte Werte bleiben deutsch** (Definition, Projekt, Spieltest-Protokoll): Auswahlwerte (`links`, `bogen`
  …) werden nur angezeigt übersetzt (`ux.choice_text`) und beim Eintippen zurückübersetzt (`ux.choice_value`),
  Spieltest-Status (`offen/ok/Absturz/Problem`) und Meldungsstufen (`Fehler/Warnung/Hinweis`) über `itemData`.
  Solche Listen (`ux.CHOICE_WORDS`, `build.PLAYTEST_STATES`/`BUILD_STATES`, `window.ISSUE_LEVELS`/`CHECK_STAGES`) tragen
  die **deutschen Datenwerte**; `tr()` findet sie in der Spalte `de` und zeigt auf Englisch den Schlüssel der Zeile.
  `pflege` meldet solche Werte nicht als fehlend.
- **Meldungen des Werkzeugkastens** sind englisch (`ego-werkzeuge/strecke`, auch `raise …Fehler("…")`); `meldung()`
  übersetzt sie auf Deutsch über `meldungen.csv` und auf Englisch die eingesetzten deutschen Datenwörter
  (`rechts` → `right`, `randstein` → `kerb`). Arbeiterprozesse bekommen die Sprache über `MAPEDITOR_SPRACHE`.
- **Zahlen**: `ux.zahl()` formatiert nach Sprache (deutsch `4.903,1 m`, englisch `4,903.1 m`); `i18n.dezimal()` für
  mit `f"{x:.1f}"` erzeugte Texte. Eingabefelder akzeptieren weiter Punkt **und** Komma (auch `yes/no`, `left/right`).

## Texte ergänzen oder ändern
1. Text im Code mit `tr(...)`/`trn(...)`/`N_(...)` schreiben.
2. Im Ordner `Mapeditor/src`: `..\.venv\Scripts\python -m mapeditor.i18n.pflege --schreiben` – neue Schlüssel
   kommen mit leerer Spalte `de` in `texte.csv`, `ort` wird erneuert. Unbenutzte Zeilen bleiben stehen (Bericht).
3. Deutsch eintragen (Excel/Editor), dann `python -m mapeditor.i18n.pflege` → „OK“.
4. Test `tests/test_sprache.py` prüft: jeder Schlüssel in beiden Sprachen vorhanden, keine leeren Einträge, gleiche
   Platzhalter, Mehrzahl mit zwei Formen, Regelkatalog vollständig; außerdem den Wechsel ohne Neustart (dasselbe
   Fenster, Ausschnitt/Reiter/Verlauf erhalten, keine übrig gebliebenen Menüs/Aktionen).
   `tests/test_streckencodes.py` prüft den Wechsel mit offenem Baudialog.

Weitere Sprache: Spalte (z. B. `fr`) in `texte.csv` und `regeln.csv`, Eintrag in `i18n.SPRACHEN`,
`hilfe_fr.html`, Gebietsschema in `i18n.qlocale()`.

## Glossar (Fachbegriffe)
| Deutsch | Englisch |
|---|---|
| Ideallinie / Fahrlinie | racing line |
| Fahrlinienlauf | racing-line run |
| Bremspunkt / Bremsbeginn | braking point |
| Bremszone, Bremsweg | braking zone, braking distance |
| Entfernungstafel | distance board |
| Querneigung | banking |
| Kurveninnenseite / -außenseite tiefer | corner inside / outside lower |
| Höhenprofil, Steigung | height profile, gradient |
| Querschnitt | cross-section |
| Mittellinie | centreline |
| Kontrollpunkt, Segment, Gerade, Bogen, Klothoide | control point, segment, straight, arc, clothoid |
| Runde schließen | close lap |
| Scheitel | apex |
| Boxengasse, Boxen, Boxenmauer | pit lane, pit boxes, pit wall |
| Startaufstellung, Startplätze | starting grid, grid slots |
| Sektor | sector |
| Spielbereich | play area |
| Prüfen / Prüfung / Vorabprüfung | check / pre-check |
| Bauen, Bau, Bauname | build, build name |
| Träger / Trägerpaket | carrier package |
| Streckencode | track code |
| Schnellaustausch | quick swap |
| Ins Spiel übertragen | transfer to game |
| Spieltest, Spieltest-Protokoll | playtest, playtest log |
| Werkzeugkasten | toolkit |
| Vorlage, Vorschlag | template, suggestion |
| Spielweltversatz | game world offset |
| Autosicherung, Wiederherstellung | autosave, recovery |

## Grenzen / offene Punkte
- **Meldungen des Werkzeugkastens** (`ego-werkzeuge/strecke`) sind deutsch und werden fertig formatiert geliefert. Auf Englisch
  bildet `i18n.meldung(text)` sie über `meldungen.csv` ab: Spalte `de` ist die Vorlage mit `{}`, Spalte `en` die Übersetzung (`{2}`,
  wenn die Reihenfolge abweicht). Werte werden erneut übersetzt, so greifen auch verschachtelte Meldungen. Unbekannte Texte bleiben
  stehen. Der Editor stellt den übersetzten **Regeltitel** aus `regeln.csv` davor. `tr(…, error=…)` übersetzt `error` automatisch.
  `python -m mapeditor.i18n.pflege --schreiben` nimmt neue Vorlagen auf (aus `meldung(…)`, Fehlern, `warnungen.append(…)` und den
  Variablen `text`, `grund`, dazu die Bauphasen), der Test meldet fehlende Übersetzungen. Neue Regeln im Werkzeugkasten → Zeile in `regeln.csv` ergänzen.
  Nicht übersetzt sind das Bauprotokoll und Fehlermeldungen aus `ego/`.
- Gespeicherte Daten (Projektname „Neue Strecke“ bei neuen Projekten, Segmentnamen, Protokolltexte) bleiben in
  der Sprache, in der sie entstanden.
- Befehlszeilenhilfe (`--help`) und technische Protokollzeilen (Prozessgruppe, Abbruchdatei) bleiben deutsch.
- Die deutsche Prüfzusammenfassung „1 Warnungen“ blieb absichtlich wortgleich; die englische Fassung nutzt
  „Errors: … · warnings: …“ ohne Mehrzahlproblem.

## Umstellung auf English First (4.11.2026)

Werkzeug (einmalig, nicht im Repository): Alle Literale in `tr`/`N_`/`trn` (1.375 im Editor) und alle Meldungen des
Werkzeugkastens (490 in `strecke/*.py` samt Prüfregeltiteln und Bauphasen) wurden per AST durch die englischen Werte der
Tabellen ersetzt, die Tabellen umgedreht (`en` als Schlüssel). Sonderfälle von Hand: drei Meldungen mit anderer
Satzstellung (Formatargumente umgestellt, deutsche Vorlage mit `{2}`), vier Meldungen mit wörtlichem `{…}`, zwei
zusammengesetzte KI-Meldungen. Sieben englische Werte fielen zusammen und wurden getrennt (z. B. „Prüfung“ → „Checks“,
„Bau“ → „Build run“, „Saat“ → „Random seed“, „Skala“ → „Object scale“). Die Tests des Editors laufen weiter auf Deutsch
(`conftest.TESTSPRACHE`) und prüfen so auch den Übersetzungsweg; `test_sprache.py` prüft Englisch als Standard und Rückfall.
