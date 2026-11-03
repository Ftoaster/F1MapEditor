# Mapeditor

Windows-Editor, mit dem du eigene Rennstrecken für **F1 25** entwirfst und zu installierbaren Paketen baust.

> Inoffizielles Fanprojekt. Keine Verbindung zu Electronic Arts, Codemasters, der Formula One Group oder der FIA. „F1“ und
> verwandte Namen sind Marken ihrer Inhaber. Dieses Projekt enthält keine Spieldateien.

English version: [README.md](README.md)

![Hauptfenster](Mapeditor/docs/screenshots/hauptansicht_teststrecke.png)

## Was der Editor kann

- Strecke aus Kontrollpunkten oder Geraden und Bögen zeichnen, Höhe, Querneigung und Breite bearbeiten
- Strecke auf einem Kartenbild nachzeichnen: wenige Klicks in Fahrtrichtung, der Editor findet die Fahrbahn dazwischen und prüft die Runde
- Strecken kombinieren und in der Größe ändern: Punkte zwischen Fenstern kopieren und einfügen, um einen Faktor oder auf eine Länge skalieren
- Ideallinie, Bremspunkte, Kurven, Sektoren, DRS- und Straight-Mode-Zonen berechnen
- Querschnitt je Bereich einstellen: Randsteine, Gras, Auslauf, Barrieren und Randwinkel
- Boxengasse und Startaufstellung setzen, Objekte aus dem Katalog des Spiels platzieren, Hügel und Berge anlegen
- Boden, Ideallinie und Umland aus der Strecke selbst erzeugen
- Strecke gegen Regeln prüfen, die aus den Originalstrecken abgeleitet sind
- Paket für den Track Installer bauen oder eine installierte Strecke zum Testen direkt austauschen
- Oder ein Addon für den [Swofty Launcher](https://f1.swofty.net/) bauen, mit dem eigenen Streckennamen im Menü des Spiels

Die Oberfläche gibt es auf Deutsch und Englisch.

## Voraussetzungen

| | Download | Hinweise |
|---|---|---|
| System | | Windows 10 oder 11, 64 Bit |
| Spiel | [F1 25 bei Steam](https://store.steampowered.com/app/3059520/F1_25/) | Wird nur gelesen, nötig für Katalog und Bau |
| Installer, einer von beiden | **[Swofty Launcher](https://f1.swofty.net/) (empfohlen)** | Baut ein Addon: eigener Streckenname im Menü, keine Grenze durch Installer-Plätze, Installieren und Starten aus dem Editor, Absturzauswertung nach jeder Sitzung. Gespielt wird offline und ohne EA AntiCheat |
| | [EGO GameDatabases Editor](https://apps.sztefek.eu/app/ego-gamedatabases-editor) | Version 1.6.0 oder neuer, um eine Strecke zu installieren und einmal zu aktivieren |
| Träger-Paket | [Algarve International Circuit (Portimao) for F1 25](https://www.overtake.gg/downloads/algarve-international-circuit-portimao-for-f1-25.87333/) | `portimao.zip`, **Version 1.0.0 oder 1.0.2** (1.0.2 ist der aktuelle Download, 1.0.0 steht im [Reiter „History“](https://www.overtake.gg/downloads/algarve-international-circuit-portimao-for-f1-25.87333/history) der Modseite). **Nicht enthalten**. Der Bau nutzt sie als Grundlage, du wählst sie im Baudialog. Nicht entpacken |

Zeichnen und Prüfen geht ohne das Spiel. Nur zum Bauen brauchst du Spiel und Träger-Paket.

Installer und Portimão-Mod sind Community-Projekte anderer Autoren, Bedingungen und Neuerungen stehen auf deren Seiten.
Version 1.0.0 der Mod kann mit AMD-Grafikkarten abstürzen, die Version 1.0.2 behebt das. Strecken, die Mapeditor auf 1.0.2 baut, sind im Zeitfahren und im Grand Prix mit KI-Boxenstopps getestet; ob sie auf AMD-Karten laufen, ist nicht getestet.

## Loslegen

1. `Mapeditor-<Version>-win64.zip` unter [Releases](../../releases) laden und den ganzen Ordner entpacken.
2. `Mapeditor.exe` starten. Windows warnt eventuell vor einem unbekannten Herausgeber, weil das Programm nicht signiert ist.
3. **Projekt → Neu aus Vorlage** wählen oder `werkzeugkasten/strecke/beispiele/teststrecke.json` öffnen.
4. **F1** öffnet die eingebaute Hilfe.

## Bekannte Grenzen

- Boxengasse links der Strecke ist über den Swofty Launcher getestet, noch nicht über den EGO-Installer.
- Licht und Garagen-Lichtkarten stammen von der Trägerstrecke.
- Sehr lange Strecken funktionieren, überschreiten aber die Größen aller Originale; die Prüfung zeigt dann Warnungen.
- Am besten getestet sind Zeitfahren und Grand Prix, andere Modi weniger.
- Swofty Launcher: Nach dem Entfernen eines Strecken-Addons einen neuen Grand Prix starten, nicht „Fortsetzen“. Ein Spielstand auf einer fehlenden Strecke lässt das Spiel abstürzen.
- Die Option für 26 Autos legt nur die Plätze auf der Strecke an. Die Autos selbst brauchen ein Raster-Addon, Strecken des Originalspiels haben 24 Plätze.
- Runden, Kraftstoff, Reifen und KI-Zeiten aus der Strecke sind über den Swofty Launcher getestet, noch nicht über den EGO-Installer. Die Reifenwahl ist eine Schätzung.
- Swofty Launcher: Beim manuellen Einfahren in die Box (Boxenassistent aus) zeigt das Spiel kein Tor zur eigenen Boxencrew. Unter dem EGO-Installer war es da. Die Ursache ist noch nicht gefunden.

## Aus dem Quelltext bauen

Dafür brauchst du [Python 3.10 oder neuer](https://www.python.org/downloads/).

```powershell
cd Mapeditor
python einrichten.py
.venv\Scripts\python.exe -m pytest tests -q
.venv\Scripts\python.exe packaging\bauen.py
```

Tests, die nicht enthaltene Dateien wie das Spiel brauchen, werden übersprungen.

## Sicherheit

Der Editor schreibt nie von selbst in den Spielordner. Das macht nur der ausdrücklich gestartete Schnellaustausch, und der legt
vorher eine Sicherung an. Installieren und Starten im Launcher übergeben an den Swofty Launcher, der den Spielordner nach jeder
Sitzung zurücksetzt. Nutze das offline, sichere vorher Spielstände und Spieldatenbank, und verwende es auf eigenes Risiko.

## Lizenz

Copyright (C) 2026 The Mapeditor authors

Dieses Programm ist freie Software. Du darfst es unter den Bedingungen der GNU General Public License, Version 3, weitergeben
und verändern. Es wird **ohne jede Gewährleistung** bereitgestellt. Maßgeblich ist der englische Text in [LICENSE](LICENSE). Die
Lizenz gilt nur für den Code dieses Projekts und gewährt keine Rechte am Spiel oder an dessen Daten.

Lizenzen der Drittkomponenten: `Mapeditor/packaging/lizenzen/`.
