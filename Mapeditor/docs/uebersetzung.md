# Übersetzung des Mapeditors (Deutsch / Englisch)

Stand 26.10.2026. Der Editor ist zweisprachig: **Deutsch** (Standard, wortgleich zur bisherigen Oberfläche) und
**Englisch**. Bild der englischen Oberfläche (26.10.2026, nach dem Wechsel im laufenden Fenster aufgenommen, mit
Vorschau und Fahrlinie): `docs/screenshots/englisch.png`.

## Sprache wählen
- Menü **Ansicht → Sprache / Language** (Deutsch / English). Die Wahl wird in den Benutzereinstellungen gespeichert
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
| `src/mapeditor/i18n/texte.csv` | **Übersetzungstabelle** aller Oberflächentexte: Spalten `de;en;ort` (829 Einträge) |
| `src/mapeditor/i18n/regeln.csv` | Titel der 56 Prüfregeln des Werkzeugkastens: `id;kurz;de;en` |
| `src/mapeditor/i18n/hilfe_de.html`, `hilfe_en.html` | Bedienungshilfe (F1) je Sprache |
| `src/mapeditor/i18n/pflege.py` | Schlüssel im Quelltext finden, Tabelle prüfen und abgleichen |

**Warum eine gemeinsame CSV mit dem deutschen Text als Schlüssel?**
- Im Code bleibt der deutsche Text lesbar und auffindbar (`tr("Speichern")`), wie bei Qt-`tr()`/gettext; kein
  künstlicher Schlüssel muss gepflegt werden, und Deutsch bleibt automatisch wortgleich.
- Fehlt eine Übersetzung, erscheint der deutsche Text – die Oberfläche bricht nie.
- Eine Tabelle mit beiden Sprachen nebeneinander ist leicht zu prüfen und zu übersetzen; Semikolon und UTF-8 mit BOM,
  damit ein deutsches Excel/LibreOffice sie direkt öffnet. Mehrzeilige Texte stehen in Anführungszeichen.
- Lange Texte (Hilfe) sind eigene HTML-Dateien je Sprache statt riesiger Tabellenzellen.

### Regeln im Code
- `tr("Text")` für jeden sichtbaren Text; Werte über **benannte Platzhalter**:
  `tr("Projekt gespeichert: {folder}", folder=pfad)` – nie Sätze aus Teilstücken zusammensetzen.
- **Mehrzahl**: `trn(n, "{n} Warnung", "{n} Warnungen")`; in der Tabelle eine Zeile `Einzahl|Mehrzahl` in beiden
  Spalten (`{n} warning|{n} warnings`).
- **Konstanten auf Modulebene** (z. B. `LABELS`, `ansicht.FEATURES`, `neigung.SIDES`) mit `N_("…")` markieren und
  erst bei der Anzeige `tr(konstante)` aufrufen; so wirkt ein Sprachwechsel ohne Neuimport.
- **Gespeicherte Werte bleiben deutsch** (Definition, Projekt, Spieltest-Protokoll): Auswahlwerte (`links`, `bogen`
  …) werden nur angezeigt übersetzt (`ux.choice_text`) und beim Eintippen zurückübersetzt (`ux.choice_value`),
  Spieltest-Status (`offen/ok/Absturz/Problem`) und Meldungsstufen (`Fehler/Warnung/Hinweis`) über `itemData`.
- **Zahlen**: `ux.zahl()` formatiert nach Sprache (deutsch `4.903,1 m`, englisch `4,903.1 m`); `i18n.dezimal()` für
  mit `f"{x:.1f}"` erzeugte Texte. Eingabefelder akzeptieren weiter Punkt **und** Komma (auch `yes/no`, `left/right`).

## Texte ergänzen oder ändern
1. Text im Code mit `tr(...)`/`trn(...)`/`N_(...)` schreiben.
2. Im Ordner `Mapeditor/src`: `..\.venv\Scripts\python -m mapeditor.i18n.pflege --schreiben` – neue Schlüssel
   kommen mit leerer Spalte `en` in `texte.csv`, `ort` wird erneuert. Unbenutzte Zeilen bleiben stehen (Bericht).
3. Englisch eintragen (Excel/Editor), dann `python -m mapeditor.i18n.pflege` → „OK“.
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
- **Meldungen des Werkzeugkastens** (`ego-werkzeuge/`, unverändert) sind nur deutsch: Prüfmeldungstexte,
  Bauphasen und -protokoll. Auf Englisch stellt der Editor den übersetzten **Regeltitel** aus `regeln.csv` (nach
  Regel-ID) vor den deutschen Originaltext und übersetzt Stufen/Prüfstufen; Zahlen bleiben im Originalformat.
  Neue Regeln im Werkzeugkasten → Zeile in `regeln.csv` ergänzen (der Test meldet fehlende).
- Gespeicherte Daten (Projektname „Neue Strecke“ bei neuen Projekten, Segmentnamen, Protokolltexte) bleiben in
  der Sprache, in der sie entstanden.
- Befehlszeilenhilfe (`--help`) und technische Protokollzeilen (Prozessgruppe, Abbruchdatei) bleiben deutsch.
- Die deutsche Prüfzusammenfassung „1 Warnungen“ blieb absichtlich wortgleich; die englische Fassung nutzt
  „Errors: … · warnings: …“ ohne Mehrzahlproblem.
