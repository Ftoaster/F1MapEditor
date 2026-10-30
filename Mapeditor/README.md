# Mapeditor

Eigenes Windows-Desktop-Paket für Streckenentwürfe mit Qt/PySide6. Oberfläche, Projekte,
Undo und Darstellung liegen hier. Die Streckenberechnung übernimmt unverändert der
vorhandene Python-Werkzeugkasten. Stand: 16.10.2026, Stufe 1–3: Entwerfen, Prüfen, Bauen/Paket und Spieltest-Protokoll.

## Start

In PowerShell im Ordner `Mapeditor`:

```powershell
# Einmalig nach dem Kopieren/Anlegen der lokalen Umgebung:
.venv\Scripts\python einrichten.py

# Anwendung:
.venv\Scripts\python -m mapeditor

# Direkt eine vorhandene Definition öffnen:
.venv\Scripts\python -m mapeditor "..\ego-werkzeuge\strecke\beispiele\teststrecke.json"
.venv\Scripts\python -m mapeditor "..\ego-werkzeuge\strecke\beispiele\portimao_form.json"
```

Die bereitgestellte Umgebung enthält Python 3.10.7, PySide6 6.12.0, numpy, pytest,
zstandard und Pillow. `einrichten.py` schreibt ausschließlich eine `.pth`-Datei in
die eigene `.venv`. Es sind keine Downloads erforderlich. Der übliche Aufruf
`python -m pip install -e . --no-deps --no-build-isolation --no-index` scheitert in
dieser Umgebung an setuptools 63.2.0 ohne Editable-Hook; auch `wheel` fehlt.
Die lokale Einrichtung umgeht diese Installationsabhängigkeiten. In einer Umgebung
mit aktuellem setuptools und wheel ist eine reguläre Installation aus `pyproject.toml`
vorgesehen. Zum Weitergeben des Quellordners nach einem Verschieben erneut einrichten.

**Sprache / Language:** Deutsch (Standard) oder Englisch – Menü *Ansicht → Sprache / Language* (gespeichert, wirkt
sofort ohne Neustart; Projekt, Rückgängig-Verlauf, Vorschau und Kartenausschnitt bleiben) oder Startoption
`--sprache en`. Übersetzungstabelle
`src/mapeditor/i18n/texte.csv`, Pflege und Glossar: [docs/uebersetzung.md](docs/uebersetzung.md).

## Bedienung

**Aufbau des Fensters (überarbeitet 25.10.2026):**

- **Werkzeugleiste** von links nach rechts: Werkzeuge *Auswahl* (V) / *Punkte* (P) · Rückgängig/Wiederholen ·
  *Einpassen*, Vergrößern, Verkleinern · *Runde schließen/öffnen* · rechts *Prüfen* (Strg+Enter), **Bauen**
  (hervorgehoben) und *Spieltest*. Jede Schaltfläche erklärt sich im Tooltip samt Kurzbefehl.
- **Kopfzeile:** Streckenname, Speicherstand (● ungespeichert / gespeichert) und Projektordner.
- **Leeres Projekt:** Startbildschirm in der Karte mit *Strecke zeichnen*, *Aus Vorlage …*, *Vorschlag erzeugen …*,
  *Projekt öffnen …* und *Beispiel ansehen*.
- **Karte:** oben ein Schild mit Werkzeug und Vorschaustand (aktuell / veraltet – wird neu berechnet), unten genau
  ein Hinweis passend zur Situation (z. B. „Punkt ziehen: verschieben · Entf: löschen“), unten rechts ein Maßstab;
  der Spielbereich ±1024 m ist am Rahmen beschriftet.
- **Links:** Anzeige-Ebenen und die Objektliste (Segmente mit Art, Länge bzw. Radius/Winkel; Punkte mit X/Z und Höhe).
- **Rechts – Eigenschaften:** Bereichsauswahl in Gruppen (Objekt · ganze Strecke · Randbereiche · Rennbetrieb).
  Zahlen mit Dezimalkomma und höchstens drei Nachkommastellen; ja/nein- und Auswahlfelder (Segmentart,
  Kurvenrichtung, Seite …) öffnen eine Klappliste.
- **Statuszeile:** Ergebnis der Vorschau (Länge, kleinster Radius, Fahrtrichtung), während der Berechnung
  „Vorschau wird berechnet …“ mit *Abbrechen*, rechts die Prüfsumme (z. B. „0 Fehler · 2 Warnungen · 1 Hinweis“,
  Klick öffnet „Prüfung“) und das aktive Werkzeug.
- **F1** öffnet die gegliederte Bedienhilfe (Schnellstart, Karte, Segmente, Eigenschaften, Farben).
- **Höhe/Steigung (25.10.2026):** Im Reiter *Höhenprofil* ist jeder Kontrollpunkt bzw. jedes Segmentende ein
  Griff. Ziehen ändert die Höhe (0,1-m-Raster, ein Undo-Schritt; gewählte Punkte gemeinsam), Rahmen wählt mehrere,
  ↑/↓ 0,1 m (Umschalt 1 m), Rechtsklick: *Gleichmäßige Steigung (Rampe)*, *Auf gleiche Höhe*, *Glätten*,
  *Höhe eingeben …*. Steigung in % zwischen den Punkten (rot über 8 %). Mausrad zoomt waagerecht, mittlere Maustaste
  verschiebt, Doppelklick zeigt die ganze Runde; bei dicht liegenden Punkten (Importe) nur die Auswahl hervorgehoben.
  Segmentstrecken: beim ersten Ändern werden alle Endhöhen fest eingetragen (`hoehe_ende` statt `steigung_prozent`),
  damit sich die übrigen Punkte nicht mitbewegen. Bei `umkehren` nur lesend.
- **Querneigung (25.10.2026):** Feld *Querneigung* rechts über den Eigenschaften (Bereich „Ausgewähltes Objekt“).
  Kurve(n) oder Punkte wählen, Stärke einstellen (Schnellknöpfe *Flach*, *2°*, *4°*, *FIA-Max*), Seite in Klartext:
  *Kurveninnenseite tiefer* (passt sich jeder Kurve an), *Kurvenaußenseite tiefer*, *links/rechts höher* (Geraden).
  Wirkt nur auf die Auswahl, ein Undo-Schritt. *Alle Kurven automatisch* neigt jede Kurve nach Radius
  (R ≤ 80 m volle Stärke, ab R 500 m flach), *Alles flach* entfernt jede Querneigung (auch im Menü *Bearbeiten*).
  Die Objektliste zeigt gesetzte Neigungen mit „∠ 4,0°“. Bei Kontrollpunkten bleiben die Nachbarn auf ihrem
  bisherigen Wert, damit ein einzelner Punkt nicht mehr die ganze Runde neigt.
- **Querneigungsprofil bearbeiten (26.10.2026):** Reiter *Querneigung* mit denselben Werkzeugen wie das Höhenprofil:
  Griffe je Kontrollpunkt (Segmente: in der Segmentmitte) senkrecht ziehen (0,1°-Raster), waagerecht ziehen wählt
  den Bereich A→B, ↑↓ ±0,1° (Umschalt ±1°), Knopfleiste und Rechtsklick *Sanfter Übergang A→B*, *Rampe*,
  *Gleicher Wert*, *Glätten*, *Querneigung eingeben …*; je ein Undo-Schritt, Grenze ±30° wie im Querneigungsfeld.
  Auswahl synchron mit Karte, Liste und Höhenprofil. Das Profil bleibt nach einer Änderung auf der Stationsachse
  der letzten Vorschau (Zoom bleibt); die Griffe zeigen den neuen Wert sofort, die berechnete Kurve folgt mit der
  Vorschau.

- Neues Projekt beginnt als offene Kontrollpunktlinie. Im Punktmodus klicken oder über
  „Einfügen“ Punkte anlegen. Mindestens vier Punkte, anschließend „Runde schließen“.
- Karte: Mausrad zum Zoom, mittlere Maus oder Leertaste+Ziehen zum Verschieben. Punkte
  anklicken/ziehen; Doppelklick fügt nach der nächsten Eingabekante ein. Objektliste als
  gleichwertiger Auswahlweg. Bei Segmenten die Linie oder einen Endgriff wählen: Ende einer
  Geraden ziehen ändert die Länge, Bogenende ziehen ändert Radius/Winkel und gegebenenfalls
  Kurvenrichtung. Das gelbe Quadrat am ausgewählten Bogen ändert den Radius bei gleichem Winkel.
  Die Zeichnung bewegt sich schon während des Ziehens; Esc verwirft die Geste.
- **Neu aus Vorlage (Projekt → Neu aus Vorlage …):** Dialog mit Quelle, Datei („Durchsuchen …“)
  und – nur bei TUM und Spielstrecke – Punktabstand. Drei Quellen:
  **TUM-CSV** (Rennstrecken-Datenbank, Spalten `x_m,y_m,w_tr_right_m,w_tr_left_m`, ohne Höhen),
  **GPX-Spur** eines Rundkurses (auf 5 m neu abgetastet, Breite pauschal 14 m, Höhen aus `<ele>`;
  eine Lücke zwischen Anfang und Ende von mehr als 200 m wird abgelehnt) und **Spielstrecke**
  (die Streckenlogik-ERP `…/tracks/<code>/wep/<code>_common.erp` aus dem Spielordner; der Streckencode
  wird aus dem Dateinamen vorgeschlagen; Mittellinie, Höhen und Breiten aus den KI-Streckengrenzen,
  keine Boxengasse, kein Gelände). Quelldateien werden nur gelesen. Das Ergebnis öffnet als **neuer,
  ungespeicherter Entwurf** mit glättender Approximation 6 m, auf die Mitte zentriert (Versatz 0,
  Rahmen „Spielbereich ±1024 m“); `editor_import` in der Definition hält Quelle, Verschiebung und Maßstab fest.
  Ist die Vorlage größer als die Spielwelt (z. B. Monza, Spa), fragt der Editor, ob sie gleichmäßig
  verkleinert werden soll (Breiten und Höhen bleiben); bei „Nein“ meldet die Prüfung `LAGE_AUSSERHALB_AO`.
  Bekannte Grenzen: Suzuka ist eine Acht mit Brücke (die Fahrbahnränder schneiden sich → nicht baubar),
  enge Haarnadeln mit großer Originalbreite (Bahrain) falten die Ränder; dort Breite oder Punkte anpassen.
- **Vorschlag erzeugen (Projekt → Vorschlag erzeugen …):** Zufallsstrecke als neuer, ungespeicherter Entwurf.
  Eingaben: Seed (mit „Würfeln“), Länge 4000–6000 m, Kurvenzahl (Richtwert; die gezählte Zahl steht in
  der Statuszeile), Mindestradius (10–100 m, Standard 30 m). Gleicher Seed und gleiche Werte ergeben
  dieselbe Strecke. Jeder Vorschlag besteht die Vorab-Prüfregeln ohne Fehler und Warnungen, ist
  selbstschnittfrei und liegt innerhalb ±1024 m; der Start liegt auf dem geradesten Abschnitt. Die Berechnung
  dauert meist unter 3 s (Wartecursor, Arbeiterprozess wie die Vorschau). Die Linie ist rund, aber ohne Höhen
  und Boxengasse; sie ist als Ausgangspunkt gedacht (Punkte ziehen, Breiten und Höhen ergänzen).
  Beide Menüpunkte fragen vorher nach, wenn ungespeicherte Änderungen vorliegen. Beim Schließen erinnert
  der Editor an das Speichern des neuen Entwurfs (Projekt → Speichern unter …).
- Links bei Segmentstrecken: Gerade oder Bogen nach der Auswahl einfügen, teilen, löschen
  und Gerade ↔ Bogen wechseln. „In Kontrollpunkte umwandeln“ tastet die ungeschlossene
  Eingabekette mit höchstens etwa 5 m Abstand ab (bei sehr langen Segmenten gröber).
  Dieser ausdrückliche Befehl ist mit Strg+Z vollständig rückgängig zu machen. Die ursprünglichen
  Segmente bleiben außerdem als `editor_segmentquelle` in der Definition erhalten.
- **Rahmenauswahl im Auswahlmodus (V):** Auf freier Kartenfläche ziehen. Alle Punkte
  im Rechteck werden gewählt; bei Segmentstrecken deren Verlauf bzw. Griffe. Umschalt
  fügt hinzu, Strg schaltet die Treffer um. Das gilt auch beim Punkt-/Segmentklick.
  Ein Klick ohne Zusatztaste wählt ein einzelnes Objekt; ein Klick auf freie Fläche
  leert die Auswahl. Strg+A wählt alle Objekte. Die Liste unterstützt Mehrfachauswahl
  mit Strg/Umschalt und Strg+A und bleibt mit der Karte synchron.
- **Punktgruppe verschieben:** Einen bereits ausgewählten Punkt ziehen. Alle gewählten
  Punkte erhalten denselben X/Z-Vektor; ihre Höhen und übrigen Attribute bleiben erhalten.
  Pfeiltasten in der Karte verschieben die Auswahl um 0,1 m, mit Umschalt um 1 m.
  Die Eingabezeichnung und Profile reagieren bereits während des Ziehens; erst Loslassen
  erzeugt einen Undo-Schritt. Esc verwirft den Zug bzw. die laufende Rahmenauswahl.
  Bei mehreren Punkten zeigt das Eigenschaftsfeld nur die gemeinsame Höhe; „—“ bedeutet
  gemischte Höhen. Ein neuer Wert setzt alle gewählten Punkte auf diese Höhe in einem Schritt.
  Entf löscht die Auswahl gemeinsam. Der Rundenschluss bleibt beim Verschieben erhalten.
- **Drehen:** Sind mindestens zwei Kontrollpunkte gewählt (oder bei einer Segmentstrecke alle
  Segmente, z. B. Strg+A), erscheint um die Auswahl ein gestrichelter Kasten mit einem **runden
  Griff „drehen“** darüber. Griff ziehen dreht um die Kastenmitte (Winkel wird angezeigt),
  **Umschalt rastet auf 15°**, Esc bricht ab, Loslassen = ein Undo-Schritt. **R / Umschalt+R**
  drehen um ±1°. So lässt sich auch die **ganze Strecke** drehen (Strg+A, dann Griff).
  Bei Segmentstrecken werden dabei nur Startpunkt und Startrichtung geändert; die Segmente
  (Längen, Radien) bleiben unverändert.
- **Lücken füllen** (nur Kontrollpunktstrecken): Der Knopf „Lücken füllen (n)“ zeigt, wie viele große Lücken
  es gibt (Abstand > 4× üblicher Punktabstand und > 20 m, z. B. nach Rahmen-Löschen). Ein Klick füllt alle Lücken
  mit Punkten im üblichen Abstand **entlang der sichtbaren Strecke** (Generatorvorschau, Form bleibt gleich; Höhe
  zwischen den beiden Randpunkten). Sind genau zwei benachbarte Punkte gewählt, wird nur diese Lücke gefüllt.
  Ein Undo-Schritt. Braucht eine aktuelle Vorschau (sonst wird sie gestartet). Die Prüfung meldet Lücken als IN-03.
- **DRS und Straight Mode:** Die Karte zeigt DRS-Zonen (grün, seitlich, Kreis = Erkennungspunkt) und
  Straight-Mode-Zonen (violett); gestrichelt = Automatik bzw. Träger-Zonen. Ebene „DRS / Straight Mode“ schaltet
  sie aus. Rechts „DRS · eigene Zonen“ bzw. „Straight Mode · Zonen“ wählen, mit „DRS-Zone +“ / „Straight Mode +“
  eine Zone am **Stationscursor** anlegen (Cursor z. B. per Klick ins Höhenprofil setzen), Werte in Metern ab Ziel
  eintragen oder die **Quadrate auf der Karte entlang der Strecke ziehen**. Klick auf eine Zonenbeschriftung wählt
  die Zone. Eigene Zonen ersetzen die Automatik; Straight-Mode-Zonen bekommen wie im Original eine Partial-Zone
  (abschaltbar). Die Prüfung meldet zu kurze, überlappende oder in Kurven reichende Zonen.
- **Segmentgruppen:** Ist die ganze Segmentstrecke gewählt, verschiebt Ziehen/Pfeiltasten die
  ganze Strecke (Startpunkt) und der Griff dreht sie. Teilgruppen aus Segmenten können
  ausgewählt, aber nicht gemeinsam verschoben werden. Die Statuszeile erklärt dann „Für
  Gruppenverschiebung erst ‚In Kontrollpunkte umwandeln‘“ und bietet direkt den gleichnamigen Knopf. Die Umwandlung ist rückgängig zu machen;
  anschließend funktionieren Rahmenauswahl und Gruppenverschiebung mit den Kontrollpunkten.
  Einzelne Segmentgriffe behalten ihre bisherige Längen-/Radius-/Winkelbearbeitung.
- Rechts Geltungsbereich wählen: Objekt, globale Fahrbahn, Abschnitte, Höhenregeln,
  Start/Ziel, globale Randbänder, Boxen/Start oder automatische Logikregeln. Geänderte Werte
  per Doppelklick auf den Wert bearbeiten und mit Enter oder Verlassen des Feldes bestätigen.
  Jeder abgeschlossene Feldwert wirkt sofort und bildet einen Undo-Schritt. Es gibt keinen
  Übernehmen-Knopf. Dezimalkomma und Dezimalpunkt sind auch bei ganzzahlig gespeicherten Radien möglich.
  Kursiv steht für Standard/geerbt; ein leeres Feld entfernt die explizite Vorgabe.
  Cyan zeigt die aktuelle unglättete Eingabe, gepunktete Kanten die Eingabebreite.
  Während der Neuberechnung zeigen Profile und Querschnitt die aktuellen Eingabewerte.
  Nach Abschluss erscheinen dort die tatsächlichen Generatorwerte.
- Endhöhe hat beim Generator Vorrang vor Segmentsteigung; für Steigung eine vorhandene
  Endhöhe ausdrücklich leeren. Ebenso überschreibt ein Bogenwinkel die Bogenlänge.
  Punktattribute werden interpoliert, Abschnitte überschreiben sie; bei Überschneidungen
  gewinnt der spätere Abschnitt. Abschnittsstationen zählen ab Eingabeursprung.
- Die Generatorvorschau startet automatisch 400 ms nach der letzten Änderung. Sie läuft
  getrennt und abbrechbar; neue Eingaben ersetzen laufende Berechnungen. Veraltete Flächen
  werden blass und ausdrücklich als veraltet markiert. „Abbrechen“ (Statuszeile bzw. Werkzeuge →
  Vorschau abbrechen) hält die Berechnung an, bis die nächste Änderung oder „Werkzeuge → Vorschau neu
  berechnen“ sie wieder startet. Ein Schließfehler
  erscheint unter „Prüfung · Eingabe“ und in der Statuszeile; Eingabe und Speichern bleiben
  verfügbar. Ungültiger Zahlentext bleibt zur Korrektur im Feld.
- Eingabeprofile zählen ab Eingabeursprung und zeigen noch keine Schließkorrektur oder
  Glättung. Das berechnete Generatorprofil zählt ab Start/Ziel. Der jeweilige Stand ist beschriftet.
- Höhenprofil, Querneigung und Querschnitt verwenden denselben Stationscursor wie die Karte.
  Höhen werden an den vorhandenen Punkten bzw. Segmenten über die Eigenschaften geändert.
- **Prüfen:** Schnelle Live-Prüfung verwendet die AP24-Bibliothek `vorab_pruefen`.
  „Prüfen“ / Strg+Enter prüft zusätzlich mit Generator. Unter „Prüfung“ nach Schweregrad
  und Text/Regel-ID filtern; ein Klick oder Enter auf eine räumliche Meldung setzt
  Auswahl und Stationscursor in Karte, Höhenprofil, Querneigung und Querschnitt.
  Nicht ausgeführte Regeln erscheinen als „Nicht geprüft“.
  `KO-01` / `LAGE_AUSSERHALB_AO` bezeichnet ±1024 m; `PL-01` gehört jetzt zum PVS-Profil.
- **Bauen / Paket:** Arbeitsordner, Paketordner und eigenes Träger-ZIP wählen, Baunamen
  vergeben und „Prüfen und bauen“ starten. Profil „empfohlen“ setzt Strecken-PVS,
  Boxenkorrektur und Code `sochi`. Standardträger:
  `ego-werkzeuge/testausgabe/pakete/portimao_original_wiederhergestellt.zip`
  (in der Distribution unter `werkzeugkasten/`; das große ZIP wird nicht mitgeliefert).
  Das Projekt wird zuerst gespeichert, dann mit Generator geprüft; nur ohne Fehler
  startet der atomare Paketbau mit Eingabeschnappschuss und SHA-256.
  Arbeits- und Paketordner werden auf freien Speicher geprüft (zusammen etwa 4 GB).
  Vorhandene Bauten/Pakete werden abgelehnt; neuen Namen vergeben.
- Fortschritt zeigt die vom Werkzeug gemeldeten Phasengrenzen. Meldungen erscheinen
  live, stderr und Fachdetails im aufklappbaren Protokoll. „Abbrechen“ legt eine
  Abbruchdatei an. Lange Einzelschritte laufen bis zur nächsten Phasengrenze;
  nach 120 s wird der Prozessbaum beendet und `_paket_tmp` dieses Baus entfernt.
  Arbeitsdateien bleiben mit Abbruchstatus nachvollziehbar. Während des Baus bleibt
  der Baudialog offen; Projektwechsel und Schließen warten auf das Bauende.
- Ergebnis zeigt Paketpfad, Größe, SHA-256, Prüfbericht und technischen Bericht.
  „Im Explorer zeigen“ markiert das ZIP; „Prüfbericht öffnen“ öffnet die JSON-Datei.
  **Erstinstallation** eines Streckencodes extern im **Track Installer** (freier Slot), danach mit
  **`paket_tool.py installiert`** vergleichen.
- **Ins Spiel übertragen …** (Schnellaustausch, `injektor_tool.py`): zuletzt gebautes Paket des Projekts in den
  schon installierten Streckencode schreiben. Erst Plan, dann eigene Bestätigung je Übertragung; vorher Sicherung
  der betroffenen Spieldateien in `injektion_sicherung/` neben dem Arbeitsordner (zurücksetzen:
  `injektor_tool.py --sicherungsordner <Ordner> zuruecksetzen`). Danach öffnet sich der Spieltest-Eintrag des Baus.
- **Bauname** wird fortlaufend vorgeschlagen (letzter nummerierter Bau + 1, z. B. `testedit11`); die Zeile darunter
  zeigt Bedarf (etwa 4 GB) und freien Platz je Datenträger. Die Vorabprüfung nutzt das Ergebnis des letzten
  Fahrlinienlaufs/Prüfens, wenn sich Definition, Basis und Versatz nicht geändert haben.
- **Was ist neu?** und **Erweiterte Bauoptionen** (Streckencode, Streckenname, PVS-Variante) im Baudialog; der Dialog
  ist nicht modal – während des Baus kann weitergearbeitet werden (der Bau nutzt den gespeicherten Schnappschuss).
- **Aufräumen …** (Baudialog, Menü Werkzeuge): Bauten und Pakete mit Datum, Größe, Bau- und Spieltest-Status;
  vorausgewählt ist alles außer dem jüngsten Bau, dem jüngsten mit Spieltest „ok“ und dem zuletzt übertragenen.
  Gelöscht wird nach Bestätigung nur direkt in den beiden Ordnern; das Protokoll markiert die Einträge.
- **Spieltest:** Über die Werkzeugleiste oder das Menü das Protokoll öffnen, Bau wählen,
  Status `ok` / `Absturz` / `Problem` und Freitext speichern. Jeder gestartete Bau
  hat einen Eintrag mit Datum, Bau-ID, Paket und eigenem Baustatus; Spieltest zunächst
  `offen`. `spieltests.json` liegt im Projekt und wird durch Geometrie-Undo nicht verändert.
- **Fahrlinie:** Nach jeder Änderung rechnet der Generator im Hintergrund Ideallinie, Kurven, Bremszonen,
  Entfernungstafeln, Startplätze, Boxengasse, Sektoren und alle Prüfregeln (je nach Strecke 10–50 s; Schalter
  „Fahrlinie automatisch berechnen“, sonst „Prüfen“). Karte: Ideallinie rot/gelb/grün (Bremsen/Rollen/Vollgas),
  Bremsbeginn als rotes Dreieck, Tafeln als weiße Quadrate, Kurvennummern **T1 … wie im Spiel** (über 15 Kurven
  legt der Bau zusammen), S2/S3, Startplätze, Boxengasse und Boxen. Reiter **Geschwindigkeit** (km/h über die
  Runde, Bremszonen) und **Auswertung** (Rundenzeit, Kurventabelle; Zeile anklicken springt zur Kurve).
  Ergebnisse werden je Definitionsstand zwischengespeichert (Rückgängig zeigt sofort wieder die passende Linie).
- **Boxengasse:** Eigenschaften → Boxengasse zeigt Ein- und Ausfahrt als ziehbare Quadrate auf der Karte.
- **Kurvendetail:** Klick auf eine Kurvennummer (T4) der Karte oder Doppelklick in der Kurventabelle zeigt die Kurve
  groß mit Infoschild (Winkel, Radius, km/h, Bremsweg, Tafeln). Feld **Gehe zu** über der Liste: Meter, T4 oder #12;
  die Liste zeigt je Objekt Station und Kurve.
- **Alles ein- und ausblendbar:** Menü *Ansicht* (Kartenebenen, Bereiche, Reiter, Automatiken) und **Ansicht anpassen …**
  (Strg+Umschalt+A); Einstellungen bleiben gespeichert. Strg+M / Doppelklick auf einen Reiter maximiert den unteren Bereich.
- **Zuletzt geöffnet** (Menü Projekt, Startbildschirm), optional letztes Projekt beim Start öffnen. **Autosicherung**
  jede Minute in `.mapeditor_wiederherstellung.json` im Projektordner; nach einem Absturz bietet der Editor die
  Änderungen beim Öffnen an.
- Eigenschaftsbereich **Ideallinie** (Abstand zum Rand, Iterationen, Mindestzeit-Feinschliff, Bewertungsfahrzeug).
- „Werkzeuge → Generatorausgabe lesend öffnen“ zeigt eine vorhandene `strecke_logik.json` als Datenbaum im
  Reiter *Auswertung*. Die Zuordnung zum aktuellen Entwurf bleibt ungeprüft.
- „Ansicht“ wechselt zwischen dunklem und hellem Thema. „Werkzeuge“ erlaubt außerdem
  Werkzeugpfad und Spielweltversatz. Bei Teststrecke ist das bisherige Bauprofil
  `[-591, 0, -473]`, bei Portimão `[0, 0, 0]`. Der Editor wählt keinen Versatz stillschweigend.

Tastatur: Strg+N/O/S, Strg+Umschalt+S, Strg+Z/Y und Strg+Umschalt+Z;
Strg+Enter prüft/erneuert. In Karte/Liste: Strg+A alle Objekte, Umschalt Auswahl erweitern,
Strg Auswahl umschalten. In der Karte: V/P, Einfg/Entf, Pfeile 0,1 m für die Auswahl,
Umschalt+Pfeile 1 m, R / Umschalt+R Auswahl ±1° drehen, Pos1 einpassen, F Auswahl fokussieren, Esc Geste abbrechen.
F1 zeigt die Bedienhilfe. Ein Zug oder ein abgeschlossener Feldwert entspricht einem Undo-Schritt.
Ein leeres Projekt zeigt den Startbildschirm; Werkzeug und passende Aktionen stehen in der Karte
(oben Zustand, unten Hinweis) und in der Statuszeile. Deaktivierte Bearbeitungselemente
erklären den Grund per Tooltip.

## Projekte und Werkzeugkasten

Ein Projektordner enthält `projekt.json` und die darin referenzierte `definition.json`.
Optional kommt `spieltests.json` hinzu. Die Rohdefinition wird separat von den aufgelösten Generatorstandards gehalten.
Unbekannte Felder, Punktattribute und Metadaten werden erhalten. Unverändertes Öffnen
und Speichern ergänzt keine Standardwerte. Die Ursprungsdefinition wird beim Import
nicht überschrieben. Unbekannte Manifestfassungen öffnen nur lesend.

Relative CSV-Beilagen werden beim Speichern mitgenommen; absolute oder über `..`
referenzierte CSV-Dateien müssen vorher als relative Beilage bereitgestellt werden.
CSV-Geometrie ist lesbar, direkte CSV-Punktbearbeitung gehört noch nicht zum Editor.

Werkzeugpfad, in dieser Reihenfolge: Projektmanifest → Umgebungsvariable
`MAPEDITOR_WERKZEUGKASTEN` → späterer Ordner `werkzeugkasten/` neben der EXE → benachbartes
Repository-Verzeichnis `ego-werkzeuge/`. Ein ausdrücklich gesetzter ungültiger Pfad
führt zu einer Meldung. Werkzeugimporte sind ausschließlich in `adapter.py` gekapselt.

Spielordner unter jedem `…/steamapps/common/F1 25`, der Downloads-Ordner des Benutzers
und der Installationsordner sind Schreibschutz-Ziele. Das gilt für Projekte und alle
Bauziele, auch nach Auflösung von Verknüpfungen. Beispiele aus der Installation
verlangen beim Speichern einen eigenen Projektordner („Speichern unter“).

## EXE und Tests

Die Ordnerdistribution liegt nach `packaging/bauen.py` unter `dist/Mapeditor/`; gestartet wird
`dist/Mapeditor/Mapeditor.exe` (eigenes Programmsymbol, erzeugt von `packaging/icon_erzeugen.py`).
`--werkzeug-cli` führt die mitgelieferten Werkzeug-CLIs im eingefrorenen Interpreter aus;
auch die tiefe Paketprüfung braucht keine separate `python.exe`.
Der einmalige große EXE-Bau scheiterte an der Kodierung des Paketprüfers. Der Shim
wurde danach korrigiert und gezielt geprüft; ein zweiter großer Bau wurde nicht ausgeführt.
Anleitung und Rauchtest: [docs/verteilung.md](docs/verteilung.md).

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
$env:PYTHONDONTWRITEBYTECODE = '1'
.venv\Scripts\python -B -m pytest -q --basetemp=.pytest_tmp
.venv\Scripts\python -B tests\screenshots_erzeugen.py
```

AP22 umfasste 66 Tests; AP25 ergänzt Bau-, Prüf- und Schreibschutzreisen.
Der aktuelle vollständige Teststand und der einmalige echte EXE-Bau stehen in
[docs/stand.md](docs/stand.md).
Details und Grenzen: [docs/stand.md](docs/stand.md).
Offene Verbesserungsvorschläge (Durchsicht 25.10.2026): [docs/verbesserungen.md](docs/verbesserungen.md).
Trailer (40 s, 1080p, mit Musik): [docs/trailer/mapeditor_trailer.mp4](docs/trailer/mapeditor_trailer.mp4), neu erzeugen mit
`packaging/trailer.py`.
Die beiden echten Beispieldefinitionen werden beim Rundlauf inhaltlich verglichen;
Originaldateien bleiben unverändert. Qt-Maus, Tastatur, Undo, Prozessabbruch und
Veralten der Vorschau werden ohne Anzeige geprüft. Die Nutzerreisen prüfen zusätzlich
Definition, gezeichnete Eingabepfade und gerenderte Pixel sowie echte automatische Generatorantworten.
Die regulären pytest-Bauprozesse verwenden kleine Attrappen. Der echte EXE-Bau ist
ein separat ausgeführter Einmaltest mit anschließend gelöschter großer Ausgabe.
Screenshots stammen aus `QWidget.grab()`:

- [Hauptansicht](docs/screenshots/hauptansicht_teststrecke.png)
- [Höhenprofil](docs/screenshots/hoehenprofil_teststrecke.png)
- [Querschnitt](docs/screenshots/querprofil_teststrecke.png)
- [Helles Thema](docs/screenshots/hauptansicht_hell.png)
- [Bearbeitung vorher](docs/screenshots/bedienung_vorher.png)
- [Sofortige Änderung vor Generatorantwort](docs/screenshots/bedienung_nachher_eingabe.png)
- [Bearbeitung nach automatischer Vorschau](docs/screenshots/bedienung_nachher.png)
- [Prüfen mit Filter und Stationssprung](docs/screenshots/bauen_pruefung.png)
- [Baufortschritt (Attrappe)](docs/screenshots/bauen_fortschritt.png)
- [Abbruch (Attrappe)](docs/screenshots/bauen_abbruch.png)
- [Baufehler (Attrappe)](docs/screenshots/bauen_fehler.png)
- [Bauergebnis (Attrappe)](docs/screenshots/bauen_ergebnis.png)
- [Spieltest-Protokoll](docs/screenshots/bauen_spieltest.png)

Die Nutzerreise-Tests (z. B. `test_segment_map_drag_automatic_preview_and_undo` für die drei `bedienung_*.png`)
erzeugen diese Bilder neu, aber nur mit `MAPEDITOR_SCREENSHOTS=1` (seit 27.10.2026; normale Testläufe ändern keine
Doku-Dateien): `$env:MAPEDITOR_SCREENSHOTS = '1'` vor dem pytest-Aufruf.

## Aufbau und Grenzen

`src/mapeditor/` enthält Anwendung, Modell, Adapter und Ansichten; `tests/` die Prüfungen;
`docs/` die Entwicklerdokumentation. Abhängigkeiten stehen in `requirements.txt` und
`pyproject.toml`. `.venv`, Caches und Builds sind ausgeschlossen.

Freie Boxensplines, manuelle Sektoren/DRS, lokale Flächen,
3D, Autosicherung und Versionsverwaltung fehlen. Die Eingabeprüfung ersetzt keine
Spielabnahme. Architektur und Werkzeug-Schnittstellen:
[docs/architektur.md](docs/architektur.md).
