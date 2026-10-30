# Eigenständige Windows-Verteilung (AP23 / AP25)

Stand 16.10.2026. Ordnerdistribution (PyInstaller 6.22.3, `onedir`), gebaut und per Rauchtest geprüft.
AP25 ergänzt Prüfung und Paketbau über den eingefrorenen Interpreter. Ergebnis des
einmaligen echten EXE-Paketbaus: [stand.md](stand.md) und `ap25_exe_bautest.json`.

## Ergebnis

| Messwert | Wert |
|---|---|
| Ordner `Mapeditor/dist/Mapeditor/` | ca. 141 MB (vor Beschneiden 166 MB) |
| ZIP `Mapeditor-0.1.2-win64.zip` | ca. 56 MB |
| Werkzeugkasten (46 Dateien, nur Code + Beispiel-JSON) | ca. 1,2 MB |
| Start bis Fenster (offscreen, warm) | 0,3 s |
| Start bis fertige Generatorvorschau Teststrecke (eigener Arbeiterprozess) | 0,9 s gesamt |
| Arbeiterprozess allein (Teststrecke, 4,9 km) | 0,5 bis 1,5 s |
| Importtest aller 42 Werkzeugkasten-Module (ego, strecke, 4 CLIs) | 0 Fehler |

Erster Start nach frischem Entpacken kann wegen Virenscanner länger dauern (nicht gemessen).
Nicht getestet: frisches Windows ohne Python/VC-Runtime (VC-Runtime-DLLs liegen bei).

## Aufbau der Distribution

```text
Mapeditor/
  Mapeditor.exe            Fensteranwendung (Bootloader, console=False)
  LIESMICH.txt
  lizenzen/                Lizenztexte + HINWEISE.txt
  werkzeugkasten/          Streckengenerator als Quelltext (neben der EXE, austauschbar)
    strecke/ (inkl. beispiele/*.json)  ego/  neue_strecke_tool.py  paket_tool.py  erp_tool.py  strecke_tool.py
  _internal/               Python 3.10, numpy, Pillow, zstandard, PySide6 (nur Core/Gui/Widgets)
```

`adapter.toolkit_path()` findet `werkzeugkasten/` ohne Änderung (Kandidat `Path(sys.executable).parent / "werkzeugkasten"`).
Der Arbeiter (`Mapeditor.exe --vorschau-arbeiter`) lädt die Module zur Laufzeit per `sys.path`; Abhängigkeiten stehen deshalb als
`hiddenimports` in `packaging/mapeditor.spec`. Der Vorschlag aus `packaging/adapter_vorschlag.md`
ist umgesetzt: `--werkzeug-cli neue_strecke_tool.py ...` führt die CLI per runpy aus.
Der vom Werkzeug selbst gestartete Aufruf `Mapeditor.exe <Werkzeugkasten>/paket_tool.py ...`
wird ebenfalls geroutet. Es wird keine separate python.exe benötigt.

## Importanalyse des Werkzeugkastens

- **Vorschau** (heutige Adapterfunktion): `strecke.definition/mittellinie/querprofil`; `strecke/__init__` zieht `erzeugung`
  und damit `geometrie`, `logik`, `rennlinie`, `vorschau`, `platzierung` nach. Drittpakete: **numpy**, **Pillow** (`vorschau.py`).
- **Bau** (`neue_strecke_tool.py`, `paket_tool.py`): zusätzlich `strecke.spiel_*`, `streckencode`, das ganze Paket `ego/`
  (`compression` braucht **zstandard**, Fallback Kommandozeilenprogramm `zstd`), **Pillow** (Minimap/Grafik).
- Stdlib-Pflichtmodule (als hiddenimports eingetragen): `uuid, zlib, zipfile, hashlib, struct, traceback, html, importlib, dataclasses, subprocess` u. a.
- Nicht nötig und nicht gebündelt: `tests/`, `analyse/`, `ergebnisse/`, `testausgabe/`, ERPs, Pakete, `__pycache__`.
  `bauen.py` verwendet eine Whitelist und bricht bei Dateien über 1 MB ab. Der empfohlene
  AP24-Baupfad benötigt keine `analyse/`-Skripte; die benötigten Module liegen unter `strecke/` und `ego/`.

## Bauanleitung

```powershell
# einmalig (Umgebung Mapeditor/.venv existiert, siehe README)
Mapeditor\.venv\Scripts\python.exe -m pip install pyinstaller
# bauen (PyInstaller, Beschneiden, Werkzeugkasten kopieren, Rauchtest, ZIP; ca. 50 s)
Mapeditor\.venv\Scripts\python.exe Mapeditor\packaging\bauen.py [--ohne-zip] [--ohne-rauchtest]
# nur Rauchtest
Mapeditor\.venv\Scripts\python.exe Mapeditor\packaging\rauchtest.py
```

Dateien in `Mapeditor/packaging/`: `mapeditor.spec`, `start.py` (Einstieg, enthält Selbsttestmodi `MAPEDITOR_RAUCHTEST=1`
und `MAPEDITOR_IMPORTTEST=1`), `bauen.py`, `rauchtest.py`, `versionsinfo.txt`, `LIESMICH.txt`, `lizenzen/`, `adapter_vorschlag.md`.
`.gitignore`: `build/` und `dist/` waren schon eingetragen; `*.spec` ist ignoriert, `!packaging/mapeditor.spec` ergänzt.
Beschnitten werden ungenutzte Qt-Teile (Qt6Pdf, Qml, Quick, VirtualKeyboard, Übersetzungen außer de/en).

### Programmsymbol

`packaging/icon_erzeugen.py` zeichnet das Symbol aus Code (Pillow) und schreibt
`src/mapeditor/mapeditor.ico` (16–256 px, kleine Größen vereinfacht) sowie die Vorschau
`docs/screenshots/icon.png`. Motiv: stilisierter Rennkurs in der Farbe der Editor-Eingabe auf
dunklem, abgerundetem Quadrat, Start-Ziel-Karo und ein Kontrollpunkt als Editor-Griff.
Die Spec setzt es als EXE-Symbol (`icon=ICON`) und legt es als Daten neben das Paket; der
Editor setzt es zur Laufzeit als Fenster-/Taskleistensymbol (`__main__.py`, eigene
AppUserModelID, damit auch der Start aus dem Quelltext nicht das Python-Symbol zeigt).
Nach Änderungen am Motiv: `icon_erzeugen.py` ausführen, dann `bauen.py`.

![Programmsymbol](screenshots/icon.png)
PyInstaller-Cache und temporäre Dateien liegen seit AP25 ebenfalls unter `Mapeditor/build/`.
Weitere Einsparung möglich: `opengl32sw.dll` (20 MB, Software-OpenGL; bleibt vorsichtshalber).

Rauchtest (offscreen, `PYTHONPATH`/`MAPEDITOR_WERKZEUGKASTEN` gelöscht, sodass `ego-werkzeuge/` im Repository nicht erreichbar ist):
1. Arbeiterprozess mit `teststrecke.json` aus der Distribution; 2. Editor öffnet die Teststrecke, die Vorschau kommt aus dem
Arbeiter-Kindprozess, danach Selbstbeendigung; der Werkzeugkastenpfad muss in der Distribution liegen;
3. alle Werkzeugkasten-Module importierbar; 4. Regelkatalog und Profile über CLI-Shim;
5. `pruefen --mit-generator`; 6. Paket-Kindprozess ohne externen Interpreter.
7. Echte Paketprüfer-CLI mit winziger Fehlerattrappe: strikte UTF-8-Dekodierung,
erwarteter fachlicher Fehlercode und Prüfordner unter `build/`, kein Schreiben in die Installation.

Der Shim setzt die Streamkodierung ausdrücklich; PYTHONIOENCODING reicht bei der
Frozen-EXE nicht aus. Der feste relative Arbeitsordner der Paket-CLI wird zur Laufzeit
in den Bauordner umgeleitet. Quell- und EXE-Bauten nutzen deshalb denselben Shim.

`packaging/echter_bautest.py` ist ein separat auszuführender Einmaltest, kein Bestandteil
des regelmäßigen Rauchtests. Er baut genau einmal nach `build/testbau/`, sichert kleine
Nachweise unter `docs/ap25_exe_*` und löscht anschließend die komplette große Testausgabe.
Ein persistenter Startbeleg verhindert versehentliches Wiederholen. Nicht erneut ausführen.

## Sicherheitsgrundsatz Spielordner

Ein Nutzer des Editors besitzt F1 25 und den Track Installer. Der Editor **schreibt niemals in den Spielordner** (`…\steamapps\common\F1 25`).
Paketbau nur in einen vom Nutzer gewählten Ausgabeordner; das ZIP installiert der Nutzer selbst im Track Installer. Der Editor übergibt
**nie** `--spielordner-erlauben`. Trägerpakete und Spielinhalte werden nicht mitgeliefert;
der Nutzer wählt sein eigenes Träger-ZIP. Installation, Spielordner und Downloads werden
im Editor als Schreibziele abgelehnt. Projekt- und Baupfade liegen in eigenen Benutzerordnern.

## Drittkomponenten und Lizenzen

| Komponente | Version | Lizenz | Hinweis |
|---|---|---|---|
| Qt 6 / PySide6 / shiboken6 | 6.12.0 | LGPL-3.0-only (alternativ GPLv2/v3, kommerziell) | siehe unten |
| NumPy | 2.2.6 | BSD-3-Clause (+ OpenBLAS BSD, GCC-Laufzeit GPL mit Ausnahme) | Text in `lizenzen/` |
| Pillow | 12.3.0 | MIT-CMU (HPND) | Text in `lizenzen/` |
| zstandard | 0.25.0 | BSD-3-Clause | Text in `lizenzen/` |
| Python | 3.10 | PSF | |
| PyInstaller-Bootloader | 6.22.3 | GPLv2+ mit Ausnahme für beliebige Weitergabe | |
| MSVC-Laufzeit | | Microsoft, weiterverteilbar | |

LGPL-konforme Weitergabe bei `onedir`:
- Qt/PySide6 bleiben **dynamisch gelinkte, unveränderte DLLs** unter `_internal/PySide6/` und sind damit austauschbar (LGPLv3 §4); bei
  `onefile` wäre das schwerer, daher `onedir` beibehalten.
- LGPL-3.0- und GPL-3.0-Text, Urheberhinweis und Quellenangabe (qt.io, PyPI) liegen in `lizenzen/` und `LIESMICH.txt`.
- Eigener Code wird nicht LGPL-pflichtig, solange er Qt nur verwendet und der Nutzer Qt austauschen darf. Keine Änderungen an Qt vornehmen
  (sonst Quelltext dieser Änderungen anbieten). Nur Core/Gui/Widgets nutzen (Module wie Qt Charts sind in der Community-Version nur GPL).
- Keine Rechtsberatung: vor einer öffentlichen Weitergabe prüfen lassen. Die PySide6-Wheels bringen nur einen Verweis auf die
  kommerzielle Lizenz mit; die LGPL-Texte stammen von gnu.org.

## Offene Punkte

1. **Bau-CLI:** in AP25 umgesetzt und per erweitertem Rauchtest geprüft. Der einmalige
   Großbau scheiterte an der UTF-8-Dekodierung des Paketprüfers; der Fehler wurde danach
   korrigiert und gezielt geprüft. Kein zweiter Großbau, daher bleibt dessen vollständiger
   Erfolgsnachweis offen. Einzelheiten in [stand.md](stand.md).
2. **Code-Signatur:** unsigniert, daher SmartScreen-Warnung. Authenticode-Zertifikat nötig (`signtool`), EXE und DLLs signieren.
3. **Installer:** bisher nur ZIP zum Entpacken. Möglich: Inno Setup oder MSIX (Installation nach `%LOCALAPPDATA%\Programs` vermeidet Admin und Schreibsperre).
4. **Updates:** keine Auto-Updates; Versionsnummer in `versionsinfo.txt` und `pyproject.toml` synchron halten, Werkzeugkasten-Stand
   (Git-Commit) im Info-Dialog festhalten. Ein Update des Werkzeugkastens allein ist durch Ersetzen von `werkzeugkasten/` möglich.
5. ~~Icon~~ erledigt 25.10.2026 (siehe „Programmsymbol“). Dateizuordnung (Doppelklick auf `projekt.json`) fehlt noch.
6. Test auf frischem Windows (ohne Python/VC-Runtime, Pfade mit Leerzeichen/Umlauten) und mit echtem Display (bisher nur offscreen);
   Virenscanner-Fehlalarme bei PyInstaller-EXEs sind möglich.
7. Beispielprojekte im Installationsordner erzwingen seit AP25 „Speichern unter“ in einen Benutzerordner (getestet).
8. Bei Python-/PySide-Update `hiddenimports` und `PRUNE` in `bauen.py` prüfen (der Rauchtest fängt Fehler auf).

## Öffentlicher Stand für GitHub

`packaging/release_veroeffentlichen.py` klont das öffentliche Repository in einen Arbeitsordner neben den Quellen, erzeugt den Stand,
testet ihn, baut und prüft die ZIP und veröffentlicht sie mit `--ausfuehren`. Ohne Schalter ist es ein Probelauf. Der Ordner wird danach
gelöscht, es gibt also nur einen Stand. `packaging/release_vorbereiten.py` kopiert dabei nur eine Positivliste, die Texte kommen aus
`packaging/release/`, und es bricht ab bei persönlichen
Daten, festen Pfaden, Spieldateien oder zu großen Dateien. Aus dem Spiel abgeleitete Daten (`portimao_form.json`,
`kalibrierung_portimao*.json`) fehlen bewusst, die Tests dazu werden übersprungen.
