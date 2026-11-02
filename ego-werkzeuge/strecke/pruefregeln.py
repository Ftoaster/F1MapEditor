"""Prüfregeln für neue Strecken mit stabilen Meldungs-IDs (AP24).

Die im Spiel teuer gelernten Regeln (Bremslinien in Kurven, Zeitfahrzonen relativ zur Ziellinie,
Boxen-Spline-Verlängerung, Boxengassen-Regeln R1–R5, Minimap-Rundung, ±1024 m, Streckencode …)
stehen hier **an einer Stelle**: als Konstanten, die der Bau (``spiel_welt``, ``spiel_geometrie``,
``spiel_minimap``) selbst verwendet, und als Prüffunktionen, die Meldungen liefern.

Stufen (``Regel.stufe``):

- ``eingabe``   Definition allein (Mittellinie, Ränder, Lage) – schnell, für den Editor live.
- ``generator`` zusätzlich Generatorausgabe (``strecke_logik.json``/``erzeugung.berechnen``).
- ``profil``    Bauoptionen (Streckencode, PVS-Variante …).
- ``logik``     platzierte Logik nach ``spiel_welt.logik_vorbereiten`` (``logik_platziert.json``)
                oder aus einer gebauten ERP (``logik_aus_erp``).
- ``bau``       Prüfergebnisse des Baus (ERP neu gelesen, Plätze auf dem Netz, TRN~).
- ``paket``     Paketprüfung (``paket_tool.py pruefen --tief``), Manifest, Minimap.

Einstieg: ``vorab_pruefen`` (Stufe a, ohne Bau), ``logik_pruefen`` (Stufe b), ``katalog()``.
Eine Meldung ist ein ``dict`` (JSON-fähig, Schema in ``docs/editor_schnittstelle.md``).
Abhängigkeiten: nur numpy und ``strecke``-Module; ERP-Zugriffe nur in ``logik_aus_erp``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np

SCHEMA_MELDUNG = "ego-meldung/1"
FEHLER, WARNUNG, HINWEIS = "fehler", "warnung", "hinweis"
SCHWEREN = (FEHLER, WARNUNG, HINWEIS)
STUFEN = ("eingabe", "generator", "profil", "logik", "bau", "paket")

# =============================================================================== Regelkonstanten
# Diese Werte verwendet der Bau selbst (eine Quelle für Bau und Prüfung).

#: AO-Atlas des Portimão-Trägers deckt das Terrain in X/Z ±1024 m ab (docs/formate/licht.md, platzierung.py).
AO_HALBBREITE_M = 1024.0
#: Loch des Außenterrains im Portimão-Träger (x0, z0, Kante in m): Die Strecke muss vollständig darin liegen
LOCH_AUSSENTERRAIN = (-920.13, -1008.23, 2048.0)
#: Bremslinien der Hauptroute paarweise mindestens so viele Gates auseinander (Spieltest 15.10.2026).
BREMSLINIEN_MIN_ABSTAND_GATES = 3
#: Haupt-Bremslinien im Portimão-Träger (ID-Satz der 38 ``.brakelinesettings``: 15 Haupt + 3 Box).
PORTIMAO_BREMSLINIEN_HAUPT = 15
PORTIMAO_BREMSLINIEN_IDS = frozenset((0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 135, 909, 910, 911))
#: Verlängerung der Boxen-Spline vor die Einfahrt / hinter die Ausfahrt (Absturz v8 bei der Übergabe).
BOXEN_VERLAENGERUNG_VOR_M = 300.0
BOXEN_VERLAENGERUNG_NACH_M = 200.0
#: Mindestwerte für die Prüfung: Spline reicht über Einfahrts-/Ausfahrtszone hinaus (Absturz v8: 0 m).
#: 30 Originale: Vorlauf 35…551 m (Monaco 35), Nachlauf ab 40 m (Brasilien); Bau: 300 m / 200 m.
BOXEN_VORLAUF_MIN_M = 25.0
BOXEN_NACHLAUF_MIN_M = 25.0
#: Anfang/Ende der Boxen-Spline höchstens so weit neben der Hauptlinie (Originale bis 3,4 m, Texas).
BOXEN_ENDE_ABSTAND_MAX_M = 5.0
#: Minimap-Eckpunkte auf mm (Installer „Ordered PKG roundtrip failed“, v1).
MINIMAP_NACHKOMMA = 3
#: Zonen mit Meterabständen zur Ziellinie wie in der Vorlage (Absturz v3 und bei ERS-Wechsel, v8).
ZEITFAHR_ZONEN = ("rollingstart_tt", "rollingstart_fly", "rollingstart_osq", "ai_flying_lap_trigger",
                  "tt_invalidate_lap", "safety_car_main", "safety_car_player_control_countdown",
                  # Spieltest 15.10.2026: Absturz, als die ERS-Anzeige auf „Overtake“ wechselte (Boxenausfahrt,
                  # KI-Anlauf) → ERS-Override- und X-Mode-Zonen ebenfalls wie in der Vorlage
                  "ers_override_detection_1", "ers_override_activation_1", "x_mode_1", "x_mode_2")
#: Meterabstände der Zeitfahrzonen zur Ziellinie im Portimão-Original (Start, Ende; negativ = vor der
#: Linie; 0 und 1 bleiben Fortschrittswerte). Abgeleitet mit ``zeitfahr_vorlage`` aus dem unveränderten
#: Portimão (Hauptlinie 4.649,34 m), siehe tests/pruefregeln_test.py.
PORTIMAO_ZEITFAHR_M = {
    "rollingstart_tt": (-1162.33, -767.14), "rollingstart_fly": (-2045.71, -1599.37),
    "rollingstart_osq": (-720.65, 0.0), "tt_invalidate_lap": (-813.63, 1.0), "safety_car_main": (0.0, 474.0),
    "safety_car_player_control_countdown": (-1069.35, 0.0), "ai_flying_lap_trigger": (-1129.79, -939.17),
    "x_mode_1": (-246.41, 446.34), "x_mode_2": (1148.39, 1520.33),
    "ers_override_detection_1": (-563.96, -552.34), "ers_override_activation_1": (-350.56, -336.61)}
ZEITFAHR_TOLERANZ_M = 1.0
#: Höchstwerte aller 28 Spielstrecken (Abfrage 16.10.2026): KI-Gates (Madrid), PVS-Gruppe-A-Zellen (Losail).
ORIGINAL_MAX_GATES = 571
#: Haupt-Bremslinien-IDs des Portimão-Trägers (.brakelinesettings: 0, 10 … 130, 135). Alle 28 Originale haben
#: mindestens so viele Haupt-Bremslinien wie Hauptkurven; der Bau legt überzählige Kurven zusammen.
TRAEGER_BREMSLINIEN = sum(1 for i in PORTIMAO_BREMSLINIEN_IDS if i < 900)
#: Geometrie-Kennwerte aller 28 Spielstrecken (24.10.2026, TrackSpace-Hauptlinie, Messung ``geometrie_kennwerte``):
#: kleinster Radius 7,5 m (Bahrain), größte Krümmungsänderung 1,05e-2 /m² (Monaco), Länge bis 7.007 m (Spa).
ORIGINAL_MIN_RADIUS_M = 7.5
ORIGINAL_MAX_DKAPPA = 1.05e-2
ORIGINAL_MAX_LAENGE_M = 7007.0
#: FIA-Kennwerte Grade 1 (Fachpresse, docs/recherche_generierung.md §4) – nur Hinweise, das Spiel verlangt sie nicht.
FIA_MIN_BREITE_M = 12.0
FIA_MIN_GRID_BREITE_M = 15.0
FIA_LAENGE_M = (3500.0, 7000.0)
FIA_MAX_GERADE_M = 2000.0
FIA_MAX_QUERNEIGUNG_GRAD = 5.7
FIA_MAX_START_STEIGUNG = 0.02
ORIGINAL_MAX_PVS_ZELLEN = 36409
#: Zwei bekannte falsche MD5 des Portimão-Mod-Trägers (Hash vom Geschwister kopiert, CLAUDE.md).
TRAEGER_MD5_ALTLAST = ("portimao_f1_previous_story.wep", "portimao_f2_26.wep")
#: Boxengassen-Regeln (AP21): Anteil der Boxen-Spline im Limiterbereich innerhalb der Haupt-TS (Originale ≤ 20,5 %).
BOX_IN_HAUPT_TS_MAX_ANTEIL = 0.25
#: Querversatzsprung der Boxen-Spline je Knoten, der auf der Hauptlinie (< 1 m) beginnt (R3: v17 0 → 5,9 m;
#: Originale dort ≤ 2,2 m).
BOX_SPRUNG_MAX_M = 3.0
BOX_SPRUNG_NAH_M = 1.0

QUELLEN = {
    "absturz": "docs/zwischenstand/absturz_analyse.md",
    "bremslinien": "docs/zwischenstand/absturz_analyse.md#ursache-gefunden-15102026-nachts-bremslinien-außerhalb-der-kurven",
    "ap21": "docs/zwischenstand/ap21.md",
    "swofty": "docs/swofty_launcher.md",
    "streckencode": "docs/streckencode.md",
    "schritt2": "docs/schritt2_ergebnisse.md",
    "platzierung": "ego-werkzeuge/strecke/platzierung.py",
    "definition": "ego-werkzeuge/strecke/definition.py",
    "mittellinie": "ego-werkzeuge/strecke/mittellinie.py",
    "ux": "docs/mapeditor/ux_konzept.md#9-validierung-und-fehlerbehandlung",
    "paket": "ego-werkzeuge/paket_tool.py",
    "recherche": "docs/recherche_generierung.md",
    "umgebung": "docs/zwischenstand/umgebung_vertrag.md",
}


@dataclass(frozen=True)
class Regel:
    id: str
    kurz: str          # Kürzel des UX-Regelkatalogs (docs/mapeditor/ux_konzept.md §9)
    schwere: str
    stufe: str
    titel: str
    quelle: str
    beleg: str         # was die Regel belegt (Spieltest, Originalvergleich, Formatbefund)


_R = [
    # ---------------------------------------------------------------- Eingabe / Generator
    Regel("DEF_UNGUELTIG", "IN-01", FEHLER, "eingabe", "Definition kann nicht geladen werden",
          QUELLEN["definition"], "Ladeprüfung der Definition"),
    Regel("DEF_WARNUNG", "IN-02", WARNUNG, "eingabe", "Hinweis des Definitionsladers/der Mittellinie (z. B. Schließfehler verteilt)",
          QUELLEN["definition"], "Ladeprüfung der Definition"),
    Regel("GEO_RAND_FALTUNG", "GE-01", FEHLER, "eingabe", "Fahrbahnrand faltet sich (Radius kleiner als Halbbreite)",
          QUELLEN["mittellinie"], "Generator lehnt gefaltete Ränder ab (erzeugung.berechnen)"),
    Regel("GEO_RAENDER_SCHNITT", "GE-01", FEHLER, "eingabe", "Fahrbahnränder schneiden sich in der Draufsicht",
          QUELLEN["mittellinie"], "Generator lehnt geschnittene Ränder ab"),
    # Warnung statt Fehler (27.10.2026, Test der maximalen Ausdehnung im Spiel). Harte Grenze bleibt das Loch des
    # Außenterrains (x -920…1128, z -1008…1040): dort brechen spiel_geometrie/gelaende den Bau ab.
    Regel("LAGE_AUSSERHALB_AO", "KO-01", WARNUNG, "eingabe", "Strecke/Logik außerhalb X/Z ±1024 m (AO-Abdeckung des Trägers)",
          QUELLEN["platzierung"], "AO-Atlas des Portimão-Trägers; trägerbezogen (Baku, Monza … liegen weiter außen)"),
    Regel("LAGE_AUSSERHALB_LOCH", "KO-02", FEHLER, "generator", "Strecke liegt nicht vollständig im Loch des Außenterrains",
          QUELLEN["schritt2"], "spiel_geometrie.geometrie_einsetzen bricht sonst ab"),
    Regel("DRS_ANZAHL", "DR-01", WARNUNG, "generator", "Weniger DRS-Zonen als gewünscht",
          "ego-werkzeuge/strecke/erzeugung.py", "Generatorbericht"),
    Regel("DRS_ZONE_UNGUELTIG", "DR-02", FEHLER, "eingabe",
          "Eigene DRS-Zone unbrauchbar (zu kurz, länger als die halbe Runde oder Erkennung nicht vor der Aktivierung)",
          "ego-werkzeuge/strecke/zonen.py", "Originale: Aktivierung 300–1000 m, Erkennung 50–600 m davor"),
    Regel("DRS_ZONEN_UEBERLAPPEN", "DR-03", WARNUNG, "eingabe", "Eigene DRS-Aktivierungszonen überlappen sich",
          "ego-werkzeuge/strecke/zonen.py", "Originale: Aktivierungszonen verschiedener IDs überlappen nie"),
    Regel("XMODE_ZONE_UNGUELTIG", "XM-01", FEHLER, "eingabe",
          "Straight-Mode-Zone unbrauchbar (kürzer als 50 m oder länger als die halbe Runde)",
          "ego-werkzeuge/strecke/zonen.py", "Originale: 180–1750 m je Zone"),
    Regel("XMODE_ZONEN_UEBERLAPPEN", "XM-02", WARNUNG, "eingabe", "Straight-Mode-Zonen überlappen sich",
          "ego-werkzeuge/strecke/zonen.py", "Originale: Full-Zonen überlappen nie"),
    Regel("XMODE_IN_KURVE", "XM-03", WARNUNG, "eingabe",
          "Straight-Mode-Zone reicht in eine enge Kurve (Radius < 250 m)",
          "ego-werkzeuge/strecke/zonen.py", "Straight Mode gehört auf Geraden; Originale enden vor dem Bremspunkt"),
    Regel("XMODE_EIGENE_UNGETESTET", "XM-04", HINWEIS, "eingabe",
          "Eigene Straight-Mode-Zonen ersetzen die Träger-Zonen – Spielwirkung noch nicht im Spiel bestätigt",
          QUELLEN["absturz"], "Bisher wurden x_mode_1/2 auf Portimãos Meterabstände gesetzt (Spieltest v8)"),
    Regel("GEO_RADIUS_KLEIN", "GE-02", WARNUNG, "eingabe",
          "Kurvenradius kleiner als bei jeder Originalstrecke (7,5 m, Bahrain)", QUELLEN["recherche"],
          "Messung aller 28 Spielstrecken 24.10.2026"),
    Regel("GEO_KRUEMMUNG_SPRUNG", "GE-03", WARNUNG, "eingabe",
          "Krümmung ändert sich abrupter als bei jeder Originalstrecke (Knick) – Approximation/Klothoiden nutzen",
          QUELLEN["recherche"], "Messung aller 28 Spielstrecken: max. 1,05e-2 /m² (Monaco)"),
    Regel("LAENGE_UEBER_ORIGINALEN", "GE-04", WARNUNG, "eingabe",
          "Strecke länger als jede Originalstrecke (7.007 m, Spa)", QUELLEN["schritt2"],
          "8-km-Editor-Bau stürzte beim Laden ab (16.10.2026, Ursache nicht eindeutig)"),
    Regel("FIA_KENNWERT", "FI-01", HINWEIS, "eingabe",
          "Abweichung von FIA-Grade-1-Kennwerten (Breite, Gridbreite, Länge, Gerade, Querneigung, Start-Steigung)",
          QUELLEN["recherche"], "Fachpresse; für das Spiel nicht nötig, nur Realismus"),
    Regel("EINGABE_PUNKTLUECKE", "IN-03", WARNUNG, "eingabe",
          "Große Lücke zwischen zwei Kontrollpunkten (Spline überbrückt sie frei, z. B. nach Rahmen-Löschen)",
          QUELLEN["definition"], "Editor-Bau 16.10.2026: 392 m / 693 m Lücken zwischen sonst 5 m Abstand"),
    Regel("KI_GATES_VIELE", "KI-05", WARNUNG, "generator",
          "Mehr KI-Gates als jede Originalstrecke (max. 571, Madrid)", QUELLEN["schritt2"],
          "Abfrage aller 28 Spielstrecken 16.10.2026; Editor-Bau mit 598 Gates stürzte beim Laden ab"),
    Regel("KI_KURVEN_ZUSAMMENGELEGT", "KI-06", HINWEIS, "generator",
          "Mehr Hauptkurven als Bremslinien-IDs des Trägers (15) – der Bau legt die dichtesten Kurven zusammen",
          QUELLEN["schritt2"], "Alle 28 Originale: Bremslinien ≥ Kurven; Editor-Strecke mit 16 Kurven stürzte ab, "
          "testedit5_kurven15 (zusammengelegt) läuft (25.10.2026)"),
    Regel("PVS_ZELLEN_VIELE", "PV-01", WARNUNG, "generator",
          "Mehr PVS-Zellen (Korridor) als jede Originalstrecke (max. 36.409, Losail)", QUELLEN["schritt2"],
          "Editor-Bau mit 59.557 Zellen stürzte beim Laden ab; v20 lief mit 37.917"),
    Regel("BOX_FEHLT", "BO-00", FEHLER, "generator", "Keine Boxengasse (offene Boxen-Spline) in der Generatorlogik",
          "ego-werkzeuge/strecke/spiel_welt.py", "Träger verlangt Boxenlinie, Boxenzonen und Box-Bremslinien"),
    Regel("KI_KURVEN_FUER_BREMSLINIEN", "KI-04", FEHLER, "generator",
          "Zu wenige Kurven-Gates für alle Haupt-Bremslinien des Trägers",
          QUELLEN["bremslinien"], "spiel_welt._bremslinien_in_kurven bricht sonst ab"),
    Regel("BOX_MAUER_AUF_BOXENLINIE", "BO-02", HINWEIS, "generator",
          "Mauerdreiecke auf der Boxenlinie (werden beim Bau entfernt)",
          QUELLEN["absturz"], "Spieltest v6: Mauer an der Boxenausfahrt; Korrektur spiel_geometrie"),
    # ---------------------------------------------------------------- Profil
    Regel("STRECKENCODE_UNZULAESSIG", "CO-01", FEHLER, "profil", "Streckencode unzulässig",
          QUELLEN["streckencode"], "Installer-/Spielregeln (AP16)"),
    Regel("STRECKENCODE_NICHT_IN_DB", "CO-02", WARNUNG, "profil",
          "Streckencode nicht in der Spieldatenbank – Strecke im Spiel nicht auswählbar",
          QUELLEN["streckencode"], "Installer: „Track not found in database“"),
    Regel("STRECKENCODE_TRAEGER", "CO-03", HINWEIS, "profil",
          "Paket behält den Trägercode portimao (kollidiert mit einer installierten Portimão-Mod)",
          QUELLEN["streckencode"], "Empfohlen: sochi (Spieltest v18 ✔)"),
    Regel("PVS_PROFIL", "PL-01", WARNUNG, "profil",
          "PVS nicht entlang der Strecke (Original-PVS bzw. „alles sichtbar“)",
          QUELLEN["schritt2"], "v20: eigene PVS-Zellen beheben unsichtbare Welt neben der Box"),
    Regel("GEOMETRIE_ABGESCHALTET", "PK-03", WARNUNG, "profil",
          "Bau ohne neue Geometrie bzw. Minimap – kein vollständiges Fahrpaket", "ego-werkzeuge/neue_strecke_tool.py",
          "Diagnoseoption"),
    # ---------------------------------------------------------------- Logik
    Regel("KI_BREMSLINIE_AUSSERHALB_KURVE", "KI-01", FEHLER, "logik",
          "Haupt-Bremslinie liegt nicht innerhalb einer Kurve (corner_start … nächstes corner_end)",
          QUELLEN["bremslinien"], "Alle 30 Originalstrecken; Absturzursache bis v16, behoben in v17"),
    Regel("KI_BREMSLINIEN_ABSTAND", "KI-02", FEHLER, "logik",
          "Haupt-Bremslinien zu dicht (gleiches Gate bzw. < 3 Gates Abstand)",
          QUELLEN["absturz"], "Spieltest 15.10.2026 (Doppelkurve)"),
    Regel("KI_BREMSLINIEN_ID_SATZ", "KI-02", FEHLER, "logik",
          "Bremslinien-IDs doppelt oder nicht gleich dem ID-Satz der .brakelinesettings",
          "ego-werkzeuge/strecke/spielformat.py", "Schreiber prüft gegen alle .brakelinesettings"),
    Regel("KI_VERWEIS_UNGUELTIG", "KI-05", FEHLER, "logik", "KI-Linie verweist auf ein nicht vorhandenes Gate",
          "docs/formate/", "Formatkonsistenz"),
    Regel("KI_BOX_KURVEN_FEHLEN", "KI-03", WARNUNG, "logik", "KI-Boxenroute ohne Kurvenlinien (R4)",
          QUELLEN["ap21"], "30/30 Originale haben 1–5 Kurven auf der Boxenroute; v17 lief ohne"),
    Regel("KI_BOX_BREMSLINIE_AUSSERHALB_KURVE", "KI-03", WARNUNG, "logik",
          "Box-Bremslinie nicht innerhalb einer Kurve der Boxenroute (R4)", QUELLEN["ap21"], "30/30 Originale"),
    Regel("KI_BOX_PAIR_ID_FEHLT", "KI-03", HINWEIS, "logik",
          "Kein pair_id zwischen Boxenausfahrts- und Streckenkurve (R5)", QUELLEN["ap21"], "25/30 Originale"),
    Regel("KI_ROUTE_OHNE_RAUMWECHSEL", "KI-07", WARNUNG, "logik",
          "KI-Route ohne Wechsel des Überholraums", QUELLEN["swofty"],
          "Swofty Launcher: Absturz, sobald ein KI-Auto eine solche Route fährt, oft die Boxengasse"),
    Regel("ZONE_ZEITFAHR_TRIGGER_AUSSERHALB", "ZO-01", WARNUNG, "logik",
          "ai_flying_lap_trigger überlappt rollingstart_tt nicht", QUELLEN["absturz"],
          "Portimão: Trigger in rollingstart_tt; Absturz v1/v2 ohne Überlappung (mit Vorlagen-Fortschritt), "
          "aber 8/30 Originale ohne Überlappung → keine Engine-Pflicht; Pflicht ist ZONE_ZEITFAHR_ABSTAND"),
    Regel("ZONE_ZEITFAHR_ABSTAND", "ZO-01", FEHLER, "logik",
          "Zeitfahr-/ERS-/X-Mode-Zone nicht im Meterabstand der Vorlage zur Ziellinie", QUELLEN["absturz"],
          "Absturz v3 (Vorlagen-Fortschritt) und beim ERS-Wechsel (v8)"),
    Regel("ZONE_BEREICH_UNGUELTIG", "ZO-03", FEHLER, "logik",
          "Zone mit Fortschritt außerhalb 0…1 oder unbekannter Spline", QUELLEN["ux"], "Formatkonsistenz"),
    Regel("ZONE_NAME_MEHRDEUTIG", "ZO-02", FEHLER, "logik", "Gleicher Zonenname mit verschiedenen Bereichen",
          "ego-werkzeuge/strecke/spielformat.py", "Schreiber verlangt eindeutige Bereiche je Name"),
    Regel("ZONE_DRS_PAARE", "ZO-02", FEHLER, "logik", "DRS-Erkennung/-Aktivierung ohne eindeutiges ID-Paar",
          QUELLEN["absturz"], "AP13: DRS-IDs wie Vorlage"),
    Regel("ZONE_DRL_UEBERLAPPUNG", "ZO-04", WARNUNG, "logik", "drl-Zonen der Hauptlinie überlappen sich",
          QUELLEN["schritt2"], "Absturz beim Start der fliegenden Runde (15.10.2026, zusammen mit neuer aispline); "
          "29/30 Originale berühren sich höchstens (Ausnahme Singapur)"),
    Regel("TRACKSPACE_FORM", "ZO-03", FEHLER, "logik",
          "TrackSpace braucht genau eine geschlossene Hauptlinie (ohne Doppelpunkt) und eine offene Boxenlinie",
          "docs/formate/", "Formatkonsistenz"),
    Regel("BOX_SPLINE_NICHT_VERLAENGERT", "BO-01", FEHLER, "logik",
          "Boxen-Spline reicht nicht weit genug vor die Einfahrt/hinter die Ausfahrt bzw. beginnt/endet neben der Hauptlinie",
          QUELLEN["absturz"], "Absturz bei Übergabe am Boxenende (v8: 300 m/200 m verlängert); Originale ≥ 35 m/40 m"),
    Regel("BOX_SPLINE_SPRUNG", "BO-03", WARNUNG, "logik", "Boxen-Spline springt quer (nicht stetig, R3)",
          QUELLEN["ap21"], "30/30 Originale stetig; v17 Sprung 5,3 m"),
    Regel("BOX_TS_ABDECKUNG", "BO-04", WARNUNG, "logik", "TS-Box-Volumes decken die Boxen-Spline nicht ganz ab (R1)",
          QUELLEN["ap21"], "30/30 Originale"),
    Regel("BOX_IN_HAUPT_TS", "BO-05", WARNUNG, "logik",
          "Boxengasse (Limiterbereich) liegt in den Haupt-TS-Volumes (R2)", QUELLEN["ap21"],
          "25/30 Originale 0 %, max. 20,5 %; vermutete Ursache der Limiter-Anzeige auf der Strecke"),
    Regel("LOGIK_HOEHE_KONVENTION", "PH-02", HINWEIS, "logik",
          "Logik liegt auf der Fahrphysik (Portimão: +0,20 m, Plätze +0,03 m)", QUELLEN["absturz"],
          "Originalunterschied, keine bewiesene Absturzursache"),
    # ---------------------------------------------------------------- Bau / Paket
    Regel("PLATZ_NICHT_AUF_FLAECHE", "PH-01", FEHLER, "bau",
          "Fahrzeugplatz liegt nicht auf passender Fläche des neuen Netzes", "ego-werkzeuge/neue_strecke_tool.py",
          "logik_pruefen (Startplätze auf Asphalt, Boxen/Garagen in der Boxengasse)"),
    Regel("TRN_HOEHE_ABWEICHEND", "PH-03", WARNUNG, "bau",
          "Fahrphysik weicht auf der Fahrbahn > 1 m vom Generatornetz ab", "ego-werkzeuge/neue_strecke_tool.py",
          "trn_pruefen"),
    Regel("PHYSIK_VERBLIEBEN", "PH-04", WARNUNG, "bau", "Verbliebene Träger-Instanzen mit Kollision",
          "ego-werkzeuge/strecke/spiel_welt.py", "verbliebene_physik"),
    Regel("ERP_PARSE_FEHLER", "PK-01", FEHLER, "bau", "Geänderte Ressource lässt sich nicht neu lesen",
          "ego-werkzeuge/neue_strecke_tool.py", "ressourcen_neu_parsen"),
    Regel("ERP_MD5_FALSCH", "PK-02", FEHLER, "bau", "Ressource mit falschem MD5", "ego-werkzeuge/ego/erp.py",
          "Neue Hashfehler sind nie erlaubt"),
    Regel("ERP_MD5_TRAEGER_ALTLAST", "PK-02", WARNUNG, "bau",
          "Bekannte falsche MD5 des unveränderten Portimão-Trägers („mit Trägerabweichung geprüft“)",
          "CLAUDE.md", "2 WEP-Ressourcen der Original-Mod"),
    Regel("MINIMAP_ECKEN_UNGERUNDET", "MM-01", FEHLER, "paket", "Minimap-Eckpunkte nicht auf mm gerundet",
          QUELLEN["absturz"], "Installer-Fehler „Ordered PKG roundtrip failed“ (v1)"),
    Regel("ZONE_MANIFEST_ERS", "ZO-02", FEHLER, "paket", "Manifest-ERS-Werte passen nicht zu den ERS-Zonen",
          "ego-werkzeuge/neue_strecke_tool.py", "Manifest wird aus den TM-Zonen gesetzt"),
    Regel("PAKET_PRUEFUNG", "PK-01", FEHLER, "paket", "Tiefe Paketprüfung meldet einen Fehler", QUELLEN["paket"],
          "paket_tool.py pruefen --tief"),
    # ---------------------------------------------------------------- Umgebung (AP-U1, strecke/umgebung.py)
    Regel("UMG_DATEI", "UM-01", FEHLER, "eingabe", "umgebung.json nicht lesbar oder ungültig", QUELLEN["umgebung"],
          "Vertrag Abschnitt 1 (Schema 1, Pflichtfelder, Maßstab 0,2–5)"),
    Regel("UMG_MODELL_UNBEKANNT", "UM-02", FEHLER, "eingabe", "Umgebungsmodell nicht im Katalog der Spielinstallation",
          QUELLEN["umgebung"], "Katalog aus den Strecken-ERPs (umgebung.katalog); Bau kopiert nur Katalogmodelle"),
    Regel("UMG_AUSSERHALB", "UM-03", FEHLER, "eingabe",
          "Umgebungsobjekt außerhalb des Streckenbereichs (Loch des Außenterrains, Welt x −920…1128, z −1008…1040)",
          QUELLEN["umgebung"], "Außerhalb des Lochs gibt es keine eigene Fahrphysik/Grundfläche für die Höhe"),
    Regel("UMG_AUF_FAHRBAHN", "UM-04", WARNUNG, "generator",
          "Umgebungsobjekt auf Asphalt, Randstein, Linie, Kies, Boxengasse oder näher als 2 m an einer Mauer",
          QUELLEN["umgebung"], "Objekte sind reine Kulisse ohne Kollision; auf der Fahrbahn fährt man hindurch"),
    Regel("UMG_VIELE", "UM-05", HINWEIS, "eingabe", "Mehr als 3.000 Umgebungsobjekte (Leistung)", QUELLEN["umgebung"],
          "Portimão-Träger: 1.274 Bäume, 16.642 Instanzen insgesamt"),
    Regel("UMG_HUEGEL_LAGE", "UM-06", FEHLER, "eingabe", "Hügel/Senke mit Mitte außerhalb des Streckenbereichs",
          QUELLEN["umgebung"], "Hügel verformen nur die Grundfläche im Loch des Außenterrains (Spieltest T4 ✔)"),
    Regel("UMG_BERG_LAGE", "UM-07", FEHLER, "eingabe",
          "Berg mit Mitte im Streckenbereich oder mehr als 12 km von der Mitte entfernt", QUELLEN["umgebung"],
          "Berge verformen nur das Außenterrain (Spieltest T5 ✔); außen ist es grob aufgelöst"),
    Regel("UMG_BERG_RAND", "UM-08", HINWEIS, "eingabe",
          "Berg reicht an den Rand des Streckenbereichs und wird dort abgeschnitten", QUELLEN["umgebung"],
          "Lochrand bleibt fest (10 m + 150 m Übergang), sonst Lücke zur Grundfläche"),
]
REGELN: Dict[str, Regel] = {r.id: r for r in _R}
assert len(REGELN) == len(_R), "doppelte Regel-ID"


def katalog() -> List[dict]:
    """Regelkatalog als JSON-fähige Liste (für Editor und Doku)."""
    return [dict(r.__dict__) for r in _R]


def katalog_markdown() -> str:
    z = ["| ID | UX | Schwere | Stufe | Regel | Beleg / Quelle |", "|---|---|---|---|---|---|"]
    for r in _R:
        z.append("| `%s` | %s | %s | %s | %s | %s (`%s`) |" % (r.id, r.kurz, r.schwere, r.stufe, r.titel, r.beleg,
                                                               r.quelle))
    return "\n".join(z)


# =============================================================================== Meldungen
def _r(v, n=2):
    return None if v is None else round(float(v), n)


def ort(spline=None, station_m=None, welt=None, objekt=None) -> Optional[dict]:
    o = {}
    if spline is not None:
        o["spline"] = spline
    if station_m is not None:
        o["station_m"] = _r(station_m)
    if welt is not None:
        o["welt"] = [_r(x, 3) for x in list(welt)[:3]]
    if objekt is not None:
        o["objekt"] = objekt
    return o or None


def meldung(regel_id: str, text: str, ort_=None, ist=None, soll=None, schwere=None, stufe=None) -> dict:
    r = REGELN[regel_id]
    m = {"schema": SCHEMA_MELDUNG, "id": r.id, "kurz": r.kurz, "schwere": schwere or r.schwere,
         "stufe": stufe or r.stufe, "text": text, "ort": ort_, "quelle": r.quelle}
    if ist is not None:
        m["ist"] = ist
    if soll is not None:
        m["soll"] = soll
    return m


def zusammenfassen(meldungen: Iterable[dict]) -> dict:
    zahl = {s: 0 for s in SCHWEREN}
    for m in meldungen:
        zahl[m["schwere"]] = zahl.get(m["schwere"], 0) + 1
    zahl["ok"] = zahl[FEHLER] == 0
    return zahl


def bericht(meldungen: List[dict], geprueft: Iterable[str], stufen: Iterable[str]) -> dict:
    """Prüfbericht: Meldungen + welche Regeln geprüft/nicht geprüft wurden (UX: „Nicht geprüft“)."""
    geprueft = sorted(set(geprueft))
    return {"schema": "ego-pruefbericht/1", "stufen": list(stufen), "meldungen": meldungen,
            "geprueft": geprueft, "nicht_geprueft": sorted(set(REGELN) - set(geprueft)),
            "zusammenfassung": zusammenfassen(meldungen)}


# =============================================================================== Geometriehilfen
def _polylinie(P, geschlossen):
    P = np.asarray(P, float)
    Q = np.vstack([P, P[:1]]) if geschlossen else P
    cum = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(Q, axis=0), axis=1))])
    return Q, cum


def _projektion(Q, cum, q):
    """(Station s auf der Polylinie Q, Abstand in der Draufsicht) des Punktes q."""
    A, B = Q[:-1], Q[1:]
    ab = (B - A)[:, [0, 2]]
    aq = (np.asarray(q, float)[[0, 2]] - A[:, [0, 2]])
    t = np.clip((aq * ab).sum(1) / np.maximum((ab * ab).sum(1), 1e-12), 0, 1)
    d = np.linalg.norm(aq - t[:, None] * ab, axis=1)
    i = int(np.argmin(d))
    return float(cum[i] + t[i] * (cum[i + 1] - cum[i])), float(d[i])


def _punkt_bei(Q, cum, s):
    i = min(max(int(np.searchsorted(cum, s, side="right")) - 1, 0), len(cum) - 2)
    t = (s - cum[i]) / max(cum[i + 1] - cum[i], 1e-12)
    return Q[i] + t * (Q[i + 1] - Q[i])


def _im_viereck(q, poly):
    inn = False
    for i in range(4):
        x1, z1 = poly[i]
        x2, z2 = poly[(i + 1) % 4]
        if (z1 > q[1]) != (z2 > q[1]) and q[0] < x1 + (q[1] - z1) * (x2 - x1) / (z2 - z1):
            inn = not inn
    return inn


def _ueberlappen(a0, a1, b0, b1):
    """Zyklische Intervalle [a0, a1], [b0, b1] auf [0, 1) überlappen sich (mehr als ein Berührpunkt)."""
    def teile(s, e):
        s, e = s % 1.0 if s != 1.0 else 1.0, e % 1.0 if e != 1.0 else 1.0
        return [(s, e)] if s <= e else [(s, 1.0), (0.0, e)]
    return any(min(x1, y1) - max(x0, y0) > 1e-9 for x0, x1 in teile(a0, a1) for y0, y1 in teile(b0, b1))


def _in_zyklisch(x, a, b):
    """x im zyklischen Intervall [a, b] auf [0, 1)."""
    x, a, b = x % 1.0, a % 1.0, b % 1.0
    return a <= x <= b if a <= b else (x >= a or x <= b)


# =============================================================================== Logikmodell
class _Logik:
    """Leseansicht auf ``spielformate`` (aus logik_platziert.json oder ``neutral_aus_strecke``)."""

    def __init__(self, lg):
        self.lg = lg
        sp = lg["spielformate"] if "spielformate" in lg else lg
        self.sp = sp
        ts = sp["TrackSpace"]["splines"]
        self.haupt_splines = [s for s in ts if s.get("closed")]
        self.box_splines = [s for s in ts if not s.get("closed")]
        self.haupt = self.haupt_splines[0] if self.haupt_splines else None
        self.box = self.box_splines[0] if self.box_splines else None
        self.HQ, self.Hcum = _polylinie(self.haupt["position"], True) if self.haupt else (None, None)
        self.L = float(self.Hcum[-1]) if self.haupt else 0.0
        self.BQ, self.Bcum = _polylinie(self.box["position"], False) if self.box else (None, None)
        self.Lb = float(self.Bcum[-1]) if self.box else 0.0
        ai = sp.get("AISplineData", {})
        self.ai = ai
        self.gates = {int(g["id"]): g for g in ai.get("gates", [])}
        self.links = {int(l["id"]): (int(l["from_gate"]), int(l["to_gate"])) for l in ai.get("links", [])}
        self.route_haupt, self.route_box = self._routen()
        self._fort = {}

    def _routen(self):
        haupt, box = None, None
        for r in self.ai.get("routes", []):
            try:
                von, bis = int(r["from_link"]), int(r["to_link"])
                folge = [self.links[von][0]] if r.get("type") == "pit" else []
                folge += [self.links[i][1] for i in range(von, bis + 1)]
            except (KeyError, ValueError):
                continue
            if r.get("type") == "track" and haupt is None:
                haupt = folge
            elif r.get("type") == "pit" and box is None:
                box = folge
        if haupt is None:  # Fallback: Nicht-Box-Gates nach Fortschritt
            haupt = sorted((i for i, g in self.gates.items() if "pit" not in g.get("name", "")), key=self.fortschritt)
        if box is None:
            box = sorted((i for i, g in self.gates.items() if "pit" in g.get("name", "")), key=self.fortschritt)
        return haupt, box

    def ist_box_gate(self, gid):
        return "pit" in self.gates[gid].get("name", "")

    def fortschritt(self, gid):
        """Fortschritt eines Gates auf seiner Spline (Hauptlinie bzw. Boxenlinie)."""
        if gid in self._fort:
            return self._fort[gid]
        g = self.gates[gid]
        box = self.ist_box_gate(gid)
        if "progress" in g:
            f = float(g["progress"])
        elif box and self.box is not None:
            f = _projektion(self.BQ, self.Bcum, g["position"])[0] / max(self.Lb, 1e-9)
        else:
            f = _projektion(self.HQ, self.Hcum, g["position"])[0] / max(self.L, 1e-9)
        self._fort[gid] = f
        return f

    def gate_ort(self, gid, objekt):
        g = self.gates[gid]
        box = self.ist_box_gate(gid)
        spline = (self.box if box else self.haupt) or {}
        laenge = self.Lb if box else self.L
        return ort(spline.get("name"), self.fortschritt(gid) * laenge, g.get("position"), objekt)

    def zonen(self):
        return self.sp.get("TM", {}).get("markup", [])


def _linien(ai, name):
    return [dict(l, id=int(l["id"]), gate_id=int(l["gate_id"])) for l in ai.get(name, [])]


def _kurven_auf_route(route, cs, ce, zyklisch):
    """Kurven als Indexpaare (Anfang, Ende) auf ``route`` (Gate-Folge)."""
    idx = {g: i for i, g in enumerate(route)}
    n = len(route)
    starts = sorted(idx[l["gate_id"]] for l in cs if l["gate_id"] in idx)
    ends = sorted(idx[l["gate_id"]] for l in ce if l["gate_id"] in idx)
    kurven = []
    for s in starts:
        if zyklisch:
            e = next((x for x in ends if x > s), (ends[0] + n) if ends else None)
        else:
            e = next((x for x in ends if x >= s), None)
        if e is not None:
            kurven.append((s, e))
    return kurven, idx


def _in_kurve(i, kurven, n, zyklisch):
    """(in Kurve?, Abstand in Gates zum nächsten Kurvenanfang danach) für Routenindex i."""
    for s, e in kurven:
        if zyklisch:
            if (i - s) % n <= (e - s):
                return True, 0
        elif s <= i <= e:
            return True, 0
    naechst = [((s - i) % n if zyklisch else s - i) for s, _ in kurven]
    naechst = [x for x in naechst if x > 0]
    return False, (min(naechst) if naechst else None)


# =============================================================================== Stufe b: Logik
def logik_pruefen(lg, vorlage_zeitfahr_m=None, bremslinien_ids=None, manifest=None, minimap=None,
                  pruefe_box=True) -> dict:
    """Prüft platzierte Logik (``logik_platziert.json`` bzw. ``neutral_aus_strecke``).

    ``vorlage_zeitfahr_m``: Meterabstände der Zeitfahrzonen der Vorlage (Standard Portimão;
    ``zeitfahr_vorlage(strecke)`` für einen anderen Träger, ``False`` = nicht prüfen).
    ``bremslinien_ids``: erwarteter ID-Satz (Standard: nur Eindeutigkeit prüfen).
    ``manifest``/``minimap``: Paketdaten für ZONE_MANIFEST_ERS/MINIMAP_ECKEN_UNGERUNDET (optional)."""
    m: List[dict] = []
    gp = set()
    lo = _Logik(lg)
    gp |= _trackspace_form(lo, m)
    if lo.haupt is None:
        return bericht(m, gp, ["logik"])
    gp |= _lage(lo, m)
    gp |= _ki_haupt(lo, m, bremslinien_ids)
    vorlage = PORTIMAO_ZEITFAHR_M if vorlage_zeitfahr_m is None else vorlage_zeitfahr_m
    vorlage = dict(vorlage) if vorlage else {}
    if any(z.get("quelle") == "eingabe" for z in lg.get("x_mode") or []):
        # eigene Straight-Mode-Zonen (Definition x_mode.zonen) behalten ihre Lage (strecke/zonen.py)
        vorlage = {k: v for k, v in vorlage.items() if not k.startswith("x_mode")}
    gp |= _zonen(lo, m, vorlage)
    if pruefe_box and lo.box is not None:
        gp |= _box(lo, m)
    plz = lg.get("platzierung") if isinstance(lg, dict) else None
    if plz is not None:
        gp.add("LOGIK_HOEHE_KONVENTION")
        if float(plz.get("logik_dy", 0.0)) < 0.1:
            m.append(meldung("LOGIK_HOEHE_KONVENTION",
                             "TrackSpace/KI-Gates/TS liegen %+.2f m über der Fahrphysik (Portimão +0,20 m); "
                             "fahrbar bis v20, Wirkung von +0,20 m im Spiel ungeprüft." % float(plz.get("logik_dy", 0.0)),
                             ist=float(plz.get("logik_dy", 0.0)), soll=0.2))
    stufen = ["logik"]
    if manifest is not None:
        gp |= manifest_ers_pruefen(lo.zonen(), manifest, m)
        stufen.append("paket")
    if minimap is not None:
        gp |= minimap_pruefen(minimap, m)
        if "paket" not in stufen:
            stufen.append("paket")
    return bericht(m, gp, stufen)


def _trackspace_form(lo, m):
    if len(lo.haupt_splines) != 1 or len(lo.box_splines) > 1:
        m.append(meldung("TRACKSPACE_FORM", "TrackSpace hat %d geschlossene und %d offene Splines (erwartet 1 und 1)"
                         % (len(lo.haupt_splines), len(lo.box_splines))))
    if lo.haupt is not None:
        P = np.asarray(lo.haupt["position"], float)
        if len(P) < 4:
            m.append(meldung("TRACKSPACE_FORM", "Hauptlinie hat nur %d Knoten" % len(P)))
        elif np.linalg.norm(P[0] - P[-1]) < 0.01:
            m.append(meldung("TRACKSPACE_FORM", "Geschlossene Hauptlinie wiederholt den ersten Knoten am Ende",
                             ort(lo.haupt["name"], 0.0, P[0], "Knoten %d" % (len(P) - 1))))
    return {"TRACKSPACE_FORM"}


def _lage(lo, m):
    punkte = [("TrackSpace " + s["name"], p) for s in lo.haupt_splines + lo.box_splines for p in s["position"]]
    punkte += [("KI-Gate %d" % i, g["position"]) for i, g in lo.gates.items()]
    punkte += [(p.get("name", "Platz"), p["position"]) for p in lo.sp.get("vehicles_layer", {}).get("placements", [])
               if "position" in p]
    aussen = [(n, p) for n, p in punkte if max(abs(float(p[0])), abs(float(p[2]))) > AO_HALBBREITE_M]
    if aussen:
        n, p = max(aussen, key=lambda t: max(abs(float(t[1][0])), abs(float(t[1][2]))))
        m.append(meldung("LAGE_AUSSERHALB_AO", "%d Logikpunkte außerhalb X/Z ±%d m; äußerster: %s"
                         % (len(aussen), AO_HALBBREITE_M, n), ort(welt=p, objekt=n), ist=len(aussen), soll=0,
                         stufe="logik"))
    return {"LAGE_AUSSERHALB_AO"}


def _ki_haupt(lo, m, erwartete_ids):
    ai = lo.ai
    gp = {"KI_VERWEIS_UNGUELTIG", "KI_BREMSLINIEN_ID_SATZ", "KI_BREMSLINIE_AUSSERHALB_KURVE", "KI_BREMSLINIEN_ABSTAND"}
    bl, cs, ce = _linien(ai, "brake_lines"), _linien(ai, "corner_start_lines"), _linien(ai, "corner_end_lines")
    for name, liste in (("brake_lines", bl), ("corner_start_lines", cs), ("corner_end_lines", ce),
                        ("retire_lines", _linien(ai, "retire_lines"))):
        for l in liste:
            if l["gate_id"] not in lo.gates:
                m.append(meldung("KI_VERWEIS_UNGUELTIG", "%s id %d verweist auf fehlendes Gate %d"
                                 % (name, l["id"], l["gate_id"]), ort(objekt="%s %d" % (name, l["id"]))))
            if "pair_id" in l and int(l["pair_id"]) not in lo.gates:
                m.append(meldung("KI_VERWEIS_UNGUELTIG", "%s id %d: pair_id %s ist kein Gate"
                                 % (name, l["id"], l["pair_id"]), ort(objekt="%s %d" % (name, l["id"]))))
    for i, (a, b) in lo.links.items():
        if a not in lo.gates or b not in lo.gates:
            m.append(meldung("KI_VERWEIS_UNGUELTIG", "Link %d verbindet fehlendes Gate (%d → %d)" % (i, a, b),
                             ort(objekt="link %d" % i)))
    ids = [l["id"] for l in bl]
    doppelt = sorted({i for i in ids if ids.count(i) > 1})
    if doppelt:
        m.append(meldung("KI_BREMSLINIEN_ID_SATZ", "Bremslinien-IDs doppelt: %s" % doppelt, ist=doppelt))
    if erwartete_ids is not None and set(ids) != set(int(x) for x in erwartete_ids):
        fehlt = sorted(set(int(x) for x in erwartete_ids) - set(ids))
        zuviel = sorted(set(ids) - set(int(x) for x in erwartete_ids))
        m.append(meldung("KI_BREMSLINIEN_ID_SATZ", "Bremslinien-IDs ≠ ID-Satz der .brakelinesettings (fehlen %s, "
                         "überzählig %s)" % (fehlt, zuviel), ist=sorted(ids), soll=sorted(int(x) for x in erwartete_ids)))
    route = [g for g in lo.route_haupt if g in lo.gates]
    n = len(route)
    if not n:
        return gp
    kurven, idx = _kurven_auf_route(route, cs, ce, True)
    haupt_bl = [l for l in bl if l["gate_id"] in idx and not lo.ist_box_gate(l["gate_id"])]
    for l in haupt_bl:
        i = idx[l["gate_id"]]
        drin, vor = _in_kurve(i, kurven, n, True)
        if not drin:
            text = ("Haupt-Bremslinie %d (Gate %d) liegt außerhalb jeder Kurve. F1 25 erwartet Haupt-Bremslinien innerhalb "
                    "corner_start … corner_end." % (l["id"], l["gate_id"]))
            if vor is not None:
                g_k = route[(i + vor) % n]
                meter = ((lo.fortschritt(g_k) - lo.fortschritt(l["gate_id"])) % 1.0) * lo.L
                text = ("Haupt-Bremslinie %d (Gate %d) liegt außerhalb jeder Kurve, %.0f m vor dem nächsten Kurvenanfang "
                        "(Gate %d). F1 25 erwartet Haupt-Bremslinien innerhalb corner_start … corner_end."
                        % (l["id"], l["gate_id"], meter, g_k))
            m.append(meldung("KI_BREMSLINIE_AUSSERHALB_KURVE", text,
                             lo.gate_ort(l["gate_id"], "brake_line %d" % l["id"])))
    belegt = sorted((idx[l["gate_id"]], l["id"]) for l in haupt_bl)
    for k, (i, bid) in enumerate(belegt):
        j, bid2 = belegt[(k + 1) % len(belegt)]
        if len(belegt) < 2:
            break
        d = (j - i) % n
        if d < BREMSLINIEN_MIN_ABSTAND_GATES:
            m.append(meldung("KI_BREMSLINIEN_ABSTAND", "Bremslinien %d und %d nur %d Gates auseinander (mind. %d)"
                             % (bid, bid2, d, BREMSLINIEN_MIN_ABSTAND_GATES),
                             lo.gate_ort(route[j], "brake_line %d" % bid2), ist=d, soll=BREMSLINIEN_MIN_ABSTAND_GATES))
    return gp


def _zone_meter(b, L):
    if b in (0.0, 1.0):
        return b
    return -(1.0 - b) * L if b > 0.5 else b * L


def zeitfahr_vorlage(strecke_oder_lg) -> dict:
    """Meterabstände der ``ZEITFAHR_ZONEN`` zur Ziellinie aus einer Vorlage (Strecke aus ``streckenlogik.lade``
    oder Logik-dict); Werte wie ``spiel_welt._vorlagenabstaende`` sie überträgt."""
    if hasattr(strecke_oder_lg, "zonen") and hasattr(strecke_oder_lg, "hauptlinie"):
        L, hl = strecke_oder_lg.hauptlinie.laenge, strecke_oder_lg.hauptlinie.name
        zonen = [(z.name, z.spline, z.start, z.ende) for z in strecke_oder_lg.zonen]
    else:
        lo = _Logik(strecke_oder_lg)
        L, hl = lo.L, lo.haupt["name"]
        zonen = [(z["Name"], z["SplineName"], float(z["Start"]), float(z["End"])) for z in lo.zonen()]
    out = {}
    for name, spline, s, e in zonen:
        if name in ZEITFAHR_ZONEN and spline == hl and name not in out:
            out[name] = (round(_zone_meter(float(s), L), 2), round(_zone_meter(float(e), L), 2))
    return out


def _zonen(lo, m, vorlage_m):
    gp = {"ZONE_BEREICH_UNGUELTIG", "ZONE_NAME_MEHRDEUTIG", "ZONE_DRS_PAARE", "ZONE_DRL_UEBERLAPPUNG",
          "ZONE_ZEITFAHR_TRIGGER_AUSSERHALB"}
    zonen = lo.zonen()
    splines = {s["name"]: s for s in lo.haupt_splines + lo.box_splines}
    hl = lo.haupt["name"]
    bereiche = {}
    for z in zonen:
        s, e = float(z["Start"]), float(z["End"])
        sp = z.get("SplineName")
        laenge = lo.L if sp == hl else lo.Lb
        if sp not in splines or not (math.isfinite(s) and math.isfinite(e) and 0 <= s <= 1 and 0 <= e <= 1):
            m.append(meldung("ZONE_BEREICH_UNGUELTIG", "Zone %s (%s): Spline %s, Bereich %.4f…%.4f"
                             % (z["Name"], z["Type"], sp, s, e), ort(sp, None, None, "Zone " + z["Name"])))
            continue
        key = (sp, round(s, 9), round(e, 9))
        if bereiche.setdefault(z["Name"], key) != key:
            m.append(meldung("ZONE_NAME_MEHRDEUTIG", "Zonenname %s mit verschiedenen Bereichen" % z["Name"],
                             ort(sp, s * laenge, None, "Zone " + z["Name"])))
    # DRS
    det = [z for z in zonen if z["Type"] == "drs_det"]
    act = [z for z in zonen if z["Type"] == "drs_act"]
    di = [str(z.get("Properties", {}).get("ID")) for z in det]
    ai_ = [str(z.get("Properties", {}).get("ID")) for z in act]
    # Originale: je Erkennung genau eine ID, aber teils mehrere Aktivierungszonen je ID (Ungarn, Mexiko …)
    if len(set(di)) != len(di) or any(i not in ai_ for i in di):
        m.append(meldung("ZONE_DRS_PAARE", "DRS-IDs Erkennung %s / Aktivierung %s: Erkennungs-ID doppelt oder ohne "
                         "Aktivierung" % (di, ai_), ist={"erkennung": di, "aktivierung": ai_}))
    # drl (Hauptlinie)
    drl = sorted(((float(z["Start"]), float(z["End"]), z["Name"]) for z in zonen
                  if z["Type"] == "drl" and z["SplineName"] == hl), key=lambda t: t[0])
    for k in range(len(drl)):
        for j in range(k + 1, len(drl)):
            a, b = drl[k], drl[j]
            if _ueberlappen(a[0], a[1], b[0], b[1]):  # Berühren (Ende = Anfang) ist erlaubt (Originale)
                m.append(meldung("ZONE_DRL_UEBERLAPPUNG", "drl-Zonen %s und %s überlappen" % (a[2], b[2]),
                                 ort(hl, b[0] * lo.L, None, "Zone " + b[2])))
    # Zeitfahren
    nach_name = {z["Name"]: z for z in zonen if z["SplineName"] == hl}
    tt, trig = nach_name.get("rollingstart_tt"), nach_name.get("ai_flying_lap_trigger")
    if tt is not None and trig is not None:
        a, b = float(tt["Start"]), float(tt["End"])
        # Originale: Trigger überlappt rollingstart_tt (Mexiko beginnt 0,001 davor, viele enden dahinter);
        # Absturz v1/v2: Trigger (Vorlage, −1130…−939 m) und Generatorzone (−300…−20 m) ohne Überlappung.
        if not _ueberlappen(float(trig["Start"]), float(trig["End"]), a, b):
            m.append(meldung("ZONE_ZEITFAHR_TRIGGER_AUSSERHALB",
                             "ai_flying_lap_trigger (%.4f…%.4f) überlappt rollingstart_tt (%.4f…%.4f) nicht"
                             % (float(trig["Start"]), float(trig["End"]), a, b),
                             ort(hl, float(trig["Start"]) * lo.L, _punkt_bei(lo.HQ, lo.Hcum, float(trig["Start"]) * lo.L),
                                 "Zone ai_flying_lap_trigger")))
    if vorlage_m:
        gp.add("ZONE_ZEITFAHR_ABSTAND")
        for name, (vs, ve) in vorlage_m.items():
            z = nach_name.get(name)
            if z is None:
                continue
            for feld, soll in (("Start", vs), ("End", ve)):
                ist_b = float(z[feld])
                if soll in (0.0, 1.0):
                    ok = ist_b == soll or (soll == 0.0 and ist_b == 1.0) or (soll == 1.0 and ist_b == 0.0)
                    ist_m = ist_b
                else:
                    ist_m = _zone_meter(ist_b, lo.L)
                    if ist_b in (0.0, 1.0):
                        ok = False
                    else:
                        ok = abs(ist_m - soll) <= ZEITFAHR_TOLERANZ_M or \
                            abs(abs(ist_m - soll) - lo.L) <= ZEITFAHR_TOLERANZ_M
                if not ok:
                    m.append(meldung("ZONE_ZEITFAHR_ABSTAND",
                                     "Zone %s.%s: %.1f m zur Ziellinie, Vorlage %.1f m" % (name, feld, ist_m, soll),
                                     ort(hl, ist_b * lo.L, _punkt_bei(lo.HQ, lo.Hcum, ist_b * lo.L), "Zone " + name),
                                     ist=_r(ist_m), soll=soll))
    return gp


#: ``ai_track_config.space_lines`` aller Strecken laut Swofty Launcher (activation/ailine.rs), eigene Umsetzung
RAUM_ABSOLUT_M, RAUM_MITTE_ANTEIL, RAUM_SEITE_ANTEIL = 2.2, 0.35, 0.15
RAUM_ENG_LINKS, RAUM_ENG_RECHTS, RAUM_BEIDE, RAUM_GEMISCHT = "eng_links", "eng_rechts", "beide", "gemischt"


def raumklasse(links, rechts):
    """Überholraum eines Tors aus den Abständen seiner weißen Linien."""
    l, r = abs(links), abs(rechts)
    summe = l + r
    if not summe:
        return RAUM_GEMISCHT
    al, ar = l / summe, r / summe
    if (l <= RAUM_ABSOLUT_M < r) or al < RAUM_SEITE_ANTEIL:
        return RAUM_ENG_LINKS
    if (r <= RAUM_ABSOLUT_M < l) or ar < RAUM_SEITE_ANTEIL:
        return RAUM_ENG_RECHTS
    return RAUM_GEMISCHT if min(al, ar) < RAUM_MITTE_ANTEIL else RAUM_BEIDE


def raum_wechselt(klassen):
    """Mindestens ein Wechsel; gemischte Tore zählen nur als Anfang einer Route."""
    return len({k for k in klassen if k != RAUM_GEMISCHT} | set(klassen[:1])) > 1


def _raumklassen(lo, route):
    klassen = []
    for g in route:
        weg = {w.get("type"): float(w.get("length", 0)) for w in lo.gates[g].get("waypoints", [])}
        links = weg.get("left_white_line", weg.get("left_track_limit"))
        rechts = weg.get("right_white_line", weg.get("right_track_limit"))
        if links is None or rechts is None:
            return None
        klassen.append(raumklasse(links, rechts))
    return klassen


def _raumwechsel(lo, m):
    for name, route in (("track", lo.route_haupt), ("pit", lo.route_box)):
        klassen = _raumklassen(lo, [g for g in route if g in lo.gates])
        if klassen and not raum_wechselt(klassen):
            m.append(meldung("KI_ROUTE_OHNE_RAUMWECHSEL",
                             "KI-Route %s: Der Überholraum ändert sich auf keinem der %d Tore. Das Spiel stürzt ab, sobald ein KI-Auto "
                             "sie fährt (der Swofty Launcher korrigiert das beim Bau)" % (name, len(klassen)),
                             ort(lo.box["name"] if name == "pit" and lo.box else None, None, None, "route " + name),
                             ist=0, soll="≥ 1"))


def _box(lo, m):
    gp = {"BOX_SPLINE_NICHT_VERLAENGERT", "BOX_SPLINE_SPRUNG", "BOX_TS_ABDECKUNG", "BOX_IN_HAUPT_TS",
          "KI_BOX_KURVEN_FEHLEN", "KI_BOX_BREMSLINIE_AUSSERHALB_KURVE", "KI_BOX_PAIR_ID_FEHLT", "KI_ROUTE_OHNE_RAUMWECHSEL"}
    _raumwechsel(lo, m)
    pit = lo.box
    pn = pit["name"]
    P = np.asarray(pit["position"], float)
    # BO-01: Anfang/Ende auf der Hauptlinie, Vorlauf/Nachlauf um die Boxenzonen
    for k, wo in ((0, "Anfang"), (len(P) - 1, "Ende")):
        _, d = _projektion(lo.HQ, lo.Hcum, P[k])
        if d > BOXEN_ENDE_ABSTAND_MAX_M:
            m.append(meldung("BOX_SPLINE_NICHT_VERLAENGERT", "Boxen-Spline-%s liegt %.1f m neben der Hauptlinie" % (wo, d),
                             ort(pn, lo.Bcum[k], P[k], "Knoten %d" % k), ist=_r(d),
                             soll="≤ %.0f m" % BOXEN_ENDE_ABSTAND_MAX_M))
    zonen = [z for z in lo.zonen() if z["SplineName"] == pn]
    ein = [float(z["Start"]) for z in zonen if z["Type"] == "pit_lane_entry_zone"]
    aus = [float(z["End"]) for z in zonen if z["Type"] == "pitlane_exit_zone"]
    if ein and min(ein) * lo.Lb < BOXEN_VORLAUF_MIN_M:
        m.append(meldung("BOX_SPLINE_NICHT_VERLAENGERT", "Boxen-Spline beginnt nur %.0f m vor der Einfahrtszone "
                         "(mind. %.0f m; Bau %.0f m)" % (min(ein) * lo.Lb, BOXEN_VORLAUF_MIN_M, BOXEN_VERLAENGERUNG_VOR_M),
                         ort(pn, 0.0, P[0], "pit_lane_entry_zone"), ist=_r(min(ein) * lo.Lb), soll=BOXEN_VORLAUF_MIN_M))
    if aus and (1 - max(aus)) * lo.Lb < BOXEN_NACHLAUF_MIN_M:
        m.append(meldung("BOX_SPLINE_NICHT_VERLAENGERT", "Boxen-Spline endet nur %.0f m hinter der Ausfahrtszone "
                         "(mind. %.0f m; Bau %.0f m)" % ((1 - max(aus)) * lo.Lb, BOXEN_NACHLAUF_MIN_M, BOXEN_VERLAENGERUNG_NACH_M),
                         ort(pn, lo.Lb, P[-1], "pitlane_exit_zone"), ist=_r((1 - max(aus)) * lo.Lb), soll=BOXEN_NACHLAUF_MIN_M))
    # R3: Querversatz zur Hauptlinie springt
    quer = np.array([_projektion(lo.HQ, lo.Hcum, p)[1] for p in P])
    dq = np.abs(np.diff(quer))
    ds = np.diff(lo.Bcum)
    spr = np.where((dq > BOX_SPRUNG_MAX_M) & (np.minimum(quer[:-1], quer[1:]) < BOX_SPRUNG_NAH_M))[0]
    if len(spr):
        k = int(spr[np.argmax(dq[spr])])
        m.append(meldung("BOX_SPLINE_SPRUNG", "Boxen-Spline springt an %d Stelle(n) von der Hauptlinie quer, max. "
                         "%.1f m auf %.1f m Länge" % (len(spr), dq[k], ds[k]), ort(pn, lo.Bcum[k + 1], P[k + 1], "Knoten %d" % (k + 1)),
                         ist=_r(dq[k]), soll="stetig (≤ %.0f m je Knoten)" % BOX_SPRUNG_MAX_M))
    # R1/R2: TS
    listen = lo.sp.get("TS", {}).get("volume_lists", [])
    box_vl = next((v for v in listen if v.get("type") == "pit"), None)
    haupt_vl = next((v for v in listen if v.get("type") == "main"), None)
    if box_vl and box_vl["volumes"]:
        def mitte(t):
            return (np.asarray(t["point2"], float) + np.asarray(t["point3"], float)) / 2
        s0 = _projektion(lo.BQ, lo.Bcum, mitte(box_vl["volumes"][0]["gates"][0]))[0]
        s1 = _projektion(lo.BQ, lo.Bcum, mitte(box_vl["volumes"][-1]["gates"][-1]))[0]
        if s0 > 5.0 or lo.Lb - s1 > 5.0:
            m.append(meldung("BOX_TS_ABDECKUNG", "TS-Box-Volumes decken nur %.0f…%.0f m der %.0f m langen Boxen-Spline ab"
                             % (s0, s1, lo.Lb), ort(pn, s0 if s0 > 5.0 else s1, None, "TS " + box_vl.get("name", "pit")),
                             ist=[_r(s0, 1), _r(s1, 1)], soll=[0, _r(lo.Lb, 1)]))
    lim = next((z for z in zonen if z["Type"] == "pit_lim_on"), None)
    if haupt_vl and lim is not None:
        polys = [np.array([np.asarray(a[k], float)[[0, 2]] for a, k in
                           ((v["gates"][0], "point1"), (v["gates"][0], "point4"), (v["gates"][1], "point4"),
                            (v["gates"][1], "point1"))]) for v in haupt_vl["volumes"]]
        mitten = np.array([p.mean(0) for p in polys])
        f = lo.Bcum / max(lo.Lb, 1e-9)
        sel = np.where((f >= float(lim["Start"])) & (f <= float(lim["End"])))[0]
        drin = 0
        for k in sel:
            q = P[k][[0, 2]]
            nah = np.where(np.linalg.norm(mitten - q, axis=1) < 150)[0]
            drin += any(_im_viereck(q, polys[j]) for j in nah)
        if len(sel) and drin / len(sel) > BOX_IN_HAUPT_TS_MAX_ANTEIL:
            m.append(meldung("BOX_IN_HAUPT_TS", "%.0f %% der Boxen-Spline im Limiterbereich liegen in Haupt-TS-Volumes "
                             "(Originale meist 0 %%, max. 20,5 %%)" % (100 * drin / len(sel)),
                             ort(pn, lo.Bcum[sel[0]], P[sel[0]], "pit_lim_on"), ist=_r(drin / len(sel), 3),
                             soll="≤ %.2f" % BOX_IN_HAUPT_TS_MAX_ANTEIL))
    # R4/R5: KI-Boxenroute
    ai = lo.ai
    route = [g for g in lo.route_box if g in lo.gates]
    cs, ce, bl = _linien(ai, "corner_start_lines"), _linien(ai, "corner_end_lines"), _linien(ai, "brake_lines")
    box_gates = {g for g in route if lo.ist_box_gate(g)}
    cs_b = [l for l in cs if l["gate_id"] in box_gates]
    kurven, idx = _kurven_auf_route(route, cs_b, [l for l in ce if l["gate_id"] in box_gates], False)
    if not kurven:
        m.append(meldung("KI_BOX_KURVEN_FEHLEN", "KI-Boxenroute ohne Kurvenlinien (Originale: 1–5 Kurven)",
                         ort(pn, None, None, "route pit"), ist=0, soll="1–5"))
    for l in bl:
        if l["gate_id"] in box_gates:
            drin, _ = _in_kurve(idx[l["gate_id"]], kurven, len(route), False) if l["gate_id"] in idx else (False, None)
            if not drin:
                m.append(meldung("KI_BOX_BREMSLINIE_AUSSERHALB_KURVE",
                                 "Box-Bremslinie %d (Gate %d) liegt in keiner Kurve der Boxenroute" % (l["id"], l["gate_id"]),
                                 lo.gate_ort(l["gate_id"], "brake_line %d" % l["id"])))
    if not any("pair_id" in l for l in cs_b):
        m.append(meldung("KI_BOX_PAIR_ID_FEHLT", "Keine Kurve der Boxenroute mit pair_id zur Streckenkurve "
                         "(25/30 Originale haben eine)", ort(pn, None, None, "corner_start_lines")))
    return gp


# =============================================================================== Paket
def minimap_pruefen(minimap: dict, m: list) -> set:
    for gruppe in ("textures", "textures_om_xm"):
        ecken = (minimap.get(gruppe) or {}).get("corners") or {}
        for name, wert in ecken.items():
            for v in wert:
                if round(float(v), MINIMAP_NACHKOMMA) != float(v):
                    m.append(meldung("MINIMAP_ECKEN_UNGERUNDET", "Minimap %s.%s = %r nicht auf mm gerundet"
                                     % (gruppe, name, wert), ort(objekt="ui/minimap.json %s.%s" % (gruppe, name)),
                                     ist=wert))
                    break
    return {"MINIMAP_ECKEN_UNGERUNDET"}


def manifest_ers_pruefen(zonen, manifest, m) -> set:
    ers = (manifest or {}).get("ers", {})
    tm = {z["Type"]: z for z in zonen}
    for feld, typ in (("OverrideDetectionLapProgress", "ers_over_det"), ("OverrideActivationLapProgress", "ers_over_act")):
        if feld in ers and typ in tm:
            soll = round(float(tm[typ]["Start"]), 4)
            if abs(float(ers[feld]) - soll) > 1e-4:
                m.append(meldung("ZONE_MANIFEST_ERS", "manifest.ers.%s = %s, Zone %s beginnt bei %s"
                                 % (feld, ers[feld], typ, soll), ort(objekt="manifest.json ers." + feld),
                                 ist=ers[feld], soll=soll))
    return {"ZONE_MANIFEST_ERS"}


def paket_pruefung_bewerten(pruefung: Sequence, m: list) -> set:
    """Zeilen (name, ok, text) aus ``paket_tool.py pruefen --tief`` → Meldungen; bekannte Träger-MD5 als Warnung."""
    for name, ok, text in pruefung:
        if ok:
            continue
        if "MD5" in name:
            falsch = [t.strip() for t in text.split(":", 1)[-1].split(";") if t.strip()]
            fremd = [f for f in falsch if not f.endswith(TRAEGER_MD5_ALTLAST)]
            if falsch and not fremd:
                m.append(meldung("ERP_MD5_TRAEGER_ALTLAST", "Paket: %d bekannte falsche MD5 des Portimão-Trägers" % len(falsch),
                                 ort(objekt="; ".join(falsch)), stufe="paket"))
                continue
        m.append(meldung("PAKET_PRUEFUNG", "Paketprüfung „%s“: %s" % (name, text[:300]), ort(objekt=name)))
    return {"PAKET_PRUEFUNG", "ERP_MD5_TRAEGER_ALTLAST"}


def bau_pruefungen_bewerten(pr: dict, m: list) -> set:
    """Ergebnisse von ``neue_strecke_tool.cmd_bauen`` (bericht['pruefungen']) → Meldungen."""
    gp = set()
    for variante in ("haupt", "original_pvs"):
        p = pr.get(variante)
        if not p:
            continue
        gp |= {"ERP_MD5_FALSCH", "ERP_MD5_TRAEGER_ALTLAST", "ERP_PARSE_FEHLER"}
        alt = [k for k in p.get("md5_falsch", []) if k.endswith(TRAEGER_MD5_ALTLAST)]
        neu = [k for k in p.get("md5_falsch", []) if k not in alt]
        for k in neu:
            m.append(meldung("ERP_MD5_FALSCH", "ERP %s: falscher MD5 %s" % (variante, k), ort(objekt=k)))
        if alt:
            m.append(meldung("ERP_MD5_TRAEGER_ALTLAST", "ERP %s: %d bekannte falsche MD5 des Trägers" % (variante, len(alt)),
                             ort(objekt="; ".join(alt))))
        for f in p.get("parse_fehler", [])[:50]:
            m.append(meldung("ERP_PARSE_FEHLER", "ERP %s: %s" % (variante, f), ort(objekt=f.split(" ", 1)[0])))
    lg = pr.get("logik")
    if lg:
        gp.add("PLATZ_NICHT_AUF_FLAECHE")
        for p in lg.get("plaetze", []):
            if not p.get("ok"):
                m.append(meldung("PLATZ_NICHT_AUF_FLAECHE", "%s (%s) liegt auf %s, %.2f m von der Mittellinie"
                                 % (p["name"].split("$")[-1], p["layer"], p.get("flaeche"), p["abstand_mittellinie_m"]),
                                 ort(None, None, p["position"], p["name"]), ist=p.get("flaeche")))
    t = pr.get("trn")
    if t and "auf_fahrbahn_hoehe_abweichend_gt_1m" in t:
        gp.add("TRN_HOEHE_ABWEICHEND")
        if t["auf_fahrbahn_hoehe_abweichend_gt_1m"]:
            m.append(meldung("TRN_HOEHE_ABWEICHEND", "%d Fahrphysik-Dreiecke auf der Fahrbahn > 1 m vom Netz entfernt"
                             % t["auf_fahrbahn_hoehe_abweichend_gt_1m"], ist=t["auf_fahrbahn_hoehe_abweichend_gt_1m"],
                             soll=0))
    return gp


# =============================================================================== Profil
def profil_pruefen(streckencode=None, pvs_variante=None, nur_original_pvs=False, ohne_geometrie=False,
                   ohne_minimap=False, m=None, ziel="ego") -> set:
    from . import streckencode as sc
    m = m if m is not None else []
    gp = set()
    if streckencode is not None:
        gp |= {"STRECKENCODE_UNZULAESSIG", "STRECKENCODE_NICHT_IN_DB", "STRECKENCODE_TRAEGER"}
        if streckencode == sc.STANDARD_ALT:
            m.append(meldung("STRECKENCODE_TRAEGER", "Code %s bleibt; eine installierte Portimão-Mod wird ersetzt. "
                             "Empfohlen: --streckencode sochi" % streckencode, ist=streckencode, soll="sochi"))
        else:
            ok, hinweis = sc.code_bewerten(streckencode, ziel)
            if not ok:
                m.append(meldung("STRECKENCODE_UNZULAESSIG", "Streckencode %s: %s" % (streckencode, hinweis),
                                 ist=streckencode))
            elif hinweis.startswith("WARNUNG"):
                m.append(meldung("STRECKENCODE_NICHT_IN_DB", "Streckencode %s: %s" % (streckencode, hinweis),
                                 ist=streckencode))
    if pvs_variante is not None:
        gp.add("PVS_PROFIL")
        if nur_original_pvs or pvs_variante != "strecke":
            m.append(meldung("PVS_PROFIL", "PVS-Variante %s: neue Objekte im alten Portimão-Bereich können unsichtbar "
                             "sein/flackern; empfohlen --pvs-variante strecke (v20)"
                             % ("Original" if nur_original_pvs else pvs_variante), ist=pvs_variante, soll="strecke"))
    gp.add("GEOMETRIE_ABGESCHALTET")
    if ohne_geometrie or ohne_minimap:
        m.append(meldung("GEOMETRIE_ABGESCHALTET", "Bau %s – nur Diagnose, kein vollständiges Fahrpaket"
                         % " und ".join(t for t, an in (("ohne Geometrie", ohne_geometrie),
                                                       ("ohne Minimap", ohne_minimap)) if an)))
    return gp


# =============================================================================== Mauer auf Boxenlinie
def mauer_auf_boxenlinie(vertices, dreiecke, oberflaeche, namen, pit_xz, breite) -> np.ndarray:
    """Indizes der Mauerdreiecke, deren Mittelpunkt (Draufsicht) näher als ``breite/2 − 0,5 m`` an der
    Boxen-Spline liegt (Spieltest v6). ``spiel_geometrie`` entfernt genau diese Dreiecke."""
    if "mauer" not in namen:
        return np.zeros(0, np.int64)
    P = np.asarray(pit_xz, np.float64)
    grenze = breite / 2 - 0.5
    km = namen.index("mauer")
    idx = np.where(oberflaeche == km)[0]
    mitte = vertices[dreiecke[idx]][:, :, [0, 2]].mean(1)
    a, ab = P[:-1], P[1:] - P[:-1]
    laenge2 = np.maximum((ab * ab).sum(1), 1e-12)
    weg = []
    for k, q in zip(idx, mitte):
        t = np.clip(((q - a) * ab).sum(1) / laenge2, 0, 1)
        if np.min(np.linalg.norm(a + ab * t[:, None] - q, axis=1)) < grenze:
            weg.append(k)
    return np.asarray(weg, np.int64)


# =============================================================================== Stufe a: ohne Bau
def vorab_pruefen(definition, basisordner=".", versatz=None, generator=None, mit_generator=False,
                  bauoptionen=None, umgebung=None, umgebung_katalog=None) -> dict:
    """Schnelle Prüfung ohne Bau.

    ``definition``: Pfad, dict (Rohdefinition wie im Editor) oder ``Streckendefinition``.
    ``versatz``: Generator → Spielwelt (Standard ``platzierung.VERSATZ``).
    ``generator``: Ergebnis von ``erzeugung.berechnen`` oder Ordner mit ``strecke_logik.json``/``strecke.npz``;
    ``mit_generator=True`` berechnet ihn (≈ 2–5 s). ``bauoptionen``: dict für ``profil_pruefen``.
    ``umgebung``: Pfad oder dict einer ``umgebung.json`` (Regeln UMG_*, ``strecke/umgebung.py``); ``UMG_AUF_FAHRBAHN``
    genau mit Generatorausgabe, sonst Näherung über die Mittellinie. ``umgebung_katalog``: Ergebnis von
    ``umgebung.katalog()`` (ohne Angabe der zwischengespeicherte Katalog; ohne Spielordner bleibt UMG_MODELL_UNBEKANNT
    ungeprüft)."""
    from . import definition as dfn, mittellinie, platzierung
    m: List[dict] = []
    gp = {"DEF_UNGUELTIG", "DEF_WARNUNG"}
    stufen = ["eingabe"]
    v = np.asarray(platzierung.VERSATZ if versatz is None else versatz, float)
    try:
        if isinstance(definition, dfn.Streckendefinition):
            d = definition
        elif isinstance(definition, dict):
            d = dfn.aus_dict(definition, basisordner=basisordner)
        else:
            d = dfn.laden(definition)
        ml = mittellinie.Mittellinie(d)
    except (ValueError, KeyError, TypeError, OSError) as e:
        m.append(meldung("DEF_UNGUELTIG", str(e)))
        return bericht(m, gp, stufen)
    for w in dict.fromkeys(str(x) for x in list(d.warnungen) + list(ml.warnungen)):  # ohne Doppelte
        m.append(meldung("DEF_WARNUNG", str(w)))
    st = ml.ref
    hl = "mittellinie"
    pr = mittellinie.raender_pruefen(ml)
    gp |= {"GEO_RAND_FALTUNG", "GEO_RAENDER_SCHNITT", "LAGE_AUSSERHALB_AO"}

    def welt_bei(s):
        i = int(np.argmin(np.abs(st.s - s)))
        return st.pos[i] + v
    for s in pr["faltung_s"]:
        m.append(meldung("GEO_RAND_FALTUNG", "Fahrbahnrand faltet sich bei %.0f m (Radius kleiner als Halbbreite)" % s,
                         ort(hl, s, welt_bei(s))))
    for sch in pr["schnitte"][:20]:
        m.append(meldung("GEO_RAENDER_SCHNITT", "Rand %s bei %.0f m schneidet Rand %s bei %.0f m"
                         % (sch["linie_a"], sch["s_a"], sch["linie_b"], sch["s_b"]), ort(hl, sch["s_a"], welt_bei(sch["s_a"]))))
    # Lage: Ränder inkl. Versatz (Netz mit Auslauf folgt mit Generator)
    rand = np.vstack([st.punkt(st.wl), st.punkt(-st.wr)]) + v
    aussen = np.where(np.max(np.abs(rand[:, [0, 2]]), axis=1) > AO_HALBBREITE_M)[0]
    if len(aussen):
        i = int(aussen[np.argmax(np.max(np.abs(rand[aussen][:, [0, 2]]), axis=1))])
        s = float(st.s[i % len(st.s)])
        m.append(meldung("LAGE_AUSSERHALB_AO", "Fahrbahnrand an %d Stationen außerhalb X/Z ±%d m (inkl. Versatz %s)"
                         % (len(aussen), AO_HALBBREITE_M, [float(x) for x in v]), ort(hl, s, rand[i]),
                         ist=_r(float(np.max(np.abs(rand[:, [0, 2]])))), soll=AO_HALBBREITE_M, stufe="eingabe"))
    gp |= _zonen_eingabe_pruefen(ml, d.param, m, welt_bei)
    gp |= _geometrie_pruefen(ml, d.param, m, welt_bei)
    gp.add("EINGABE_PUNKTLUECKE")
    if len(d.punkte) >= 8 and not d.dicht:
        P = np.asarray(d.punkte, float)[:, [0, 2]]
        abst = np.linalg.norm(np.roll(P, -1, axis=0) - P, axis=1)
        med = float(np.median(abst))
        for i in np.flatnonzero((abst > max(100.0, 10 * med)))[:10]:
            m.append(meldung("EINGABE_PUNKTLUECKE", "Lücke von %.0f m zwischen Punkt %d und %d (sonst ~%.0f m)"
                             % (abst[i], i + 1, (i + 1) % len(P) + 1, med),
                             ort(None, None, np.r_[P[i, 0], 0.0, P[i, 1]] + v, "Punkt %d" % (i + 1)),
                             ist=_r(float(abst[i])), soll="< %.0f m" % max(100.0, 10 * med)))
    if bauoptionen is not None:
        stufen.append("profil")
        gp |= profil_pruefen(m=m, **bauoptionen)
    erg = generator
    if erg is None and mit_generator and pr["ok"]:
        from . import erzeugung
        try:
            erg = erzeugung.berechnen(d)
        except ValueError as e:
            m.append(meldung("DEF_UNGUELTIG", "Generator: %s" % e))
            return bericht(m, gp, stufen)
    if erg is not None:
        stufen.append("generator")
        gp |= _generator_pruefen(erg, d, v, m)
    if umgebung is not None:
        from . import umgebung as umg
        mu, gu = umg.pruefen_mit_geprueft(umgebung, erg if erg is not None else d, v, umgebung_katalog)
        m += mu
        gp |= gu
    return bericht(m, gp, stufen)


def geometrie_kennwerte(pos_xz, sigma_m=6.0, ds=1.0):
    """Kleinster Radius und größte Krümmungsänderung einer geschlossenen Linie (wie bei der Messung der
    Originale): gleichmäßig abtasten, Gauß-Glättung σ (Messrauschen der Knoten), Krümmung, nochmals 3 m glätten.
    Rückgabe (r_min, dkappa_max, s_rmin, s_dkappa) mit s in Metern ab Anfang der Linie."""
    from .mittellinie import PeriodischerSpline, periodisch_gauss
    P = np.asarray(pos_xz, float)
    sp = PeriodischerSpline(np.column_stack([P[:, 0], np.zeros(len(P)), P[:, 1]]))
    s = np.arange(0, sp.laenge, ds)
    Q = sp.auswerten(sp.param_aus_s(s))[:, [0, 2]]
    Q = periodisch_gauss(Q, sigma_m / ds)
    d1 = (np.roll(Q, -1, 0) - np.roll(Q, 1, 0)) / (2 * ds)
    d2 = (np.roll(Q, -1, 0) - 2 * Q + np.roll(Q, 1, 0)) / ds ** 2
    k = (d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]) / np.maximum(np.linalg.norm(d1, axis=1) ** 3, 1e-9)
    kg = periodisch_gauss(k, 3.0 / ds)
    dk = np.abs(np.roll(kg, -1) - np.roll(kg, 1)) / (2 * ds)
    i, j = int(np.argmax(np.abs(k))), int(np.argmax(dk))
    return 1.0 / max(abs(k[i]), 1e-9), float(dk[j]), float(s[i]), float(s[j])


def _geometrie_pruefen(ml, param, m, welt_bei):
    """Radius/Krümmungsänderung gegen die Originale, Länge, FIA-Kennwerte (Hinweise)."""
    from .mittellinie import laeufe
    gp = {"GEO_RADIUS_KLEIN", "GEO_KRUEMMUNG_SPRUNG", "LAENGE_UEBER_ORIGINALEN", "FIA_KENNWERT"}
    hl = "mittellinie"
    st = ml.ref
    L = float(ml.laenge)
    pos = st.pos[:: max(1, int(round(2.0 / ml.ds)))]
    r_min, dk_max, s_r, s_k = geometrie_kennwerte(pos[:, [0, 2]])
    if r_min < ORIGINAL_MIN_RADIUS_M:
        m.append(meldung("GEO_RADIUS_KLEIN", "Kleinster Radius %.1f m bei %.0f m (Originale ≥ %.1f m)"
                         % (r_min, s_r, ORIGINAL_MIN_RADIUS_M), ort(hl, s_r, welt_bei(s_r)),
                         ist=_r(r_min), soll=">= %.1f m" % ORIGINAL_MIN_RADIUS_M))
    if dk_max > ORIGINAL_MAX_DKAPPA:
        m.append(meldung("GEO_KRUEMMUNG_SPRUNG", "Krümmungsänderung %.1e /m² bei %.0f m (Originale ≤ %.1e) – "
                         "mittellinie.approximation_m (z. B. 6–8) oder Klothoiden-Übergänge verwenden"
                         % (dk_max, s_k, ORIGINAL_MAX_DKAPPA), ort(hl, s_k, welt_bei(s_k)),
                         ist=float("%.3g" % dk_max), soll="<= %.2g" % ORIGINAL_MAX_DKAPPA))
    if L > ORIGINAL_MAX_LAENGE_M:
        m.append(meldung("LAENGE_UEBER_ORIGINALEN", "Streckenlänge %.0f m (längste Originalstrecke %.0f m)"
                         % (L, ORIGINAL_MAX_LAENGE_M), ist=_r(L), soll="<= %.0f m" % ORIGINAL_MAX_LAENGE_M))
    # FIA-Kennwerte (Hinweise)
    breite = st.wl + st.wr
    i = int(np.argmin(breite))
    if breite[i] < FIA_MIN_BREITE_M - 1e-6:
        s = float(st.s[i])
        m.append(meldung("FIA_KENNWERT", "Fahrbahnbreite %.1f m bei %.0f m (FIA ≥ %.0f m)" % (breite[i], s, FIA_MIN_BREITE_M),
                         ort(hl, s, welt_bei(s)), ist=_r(float(breite[i])), soll=FIA_MIN_BREITE_M))
    pg = param.get("startaufstellung", {}) or {}
    grid_m = float(pg.get("abstand_pole_m", 6.0)) + (int(pg.get("anzahl", 22)) - 1) * float(pg.get("abstand_m", 8.0))
    maske = (st.s >= L - grid_m) | (st.s <= 100.0)
    if maske.any() and float(breite[maske].min()) < FIA_MIN_GRID_BREITE_M - 1e-6:
        m.append(meldung("FIA_KENNWERT", "Startaufstellung nur %.1f m breit (FIA ≥ %.0f m bis zur ersten Kurve)"
                         % (float(breite[maske].min()), FIA_MIN_GRID_BREITE_M), ort(hl, 0.0, welt_bei(0.0)),
                         ist=_r(float(breite[maske].min())), soll=FIA_MIN_GRID_BREITE_M))
    if not FIA_LAENGE_M[0] <= L <= FIA_LAENGE_M[1]:
        m.append(meldung("FIA_KENNWERT", "Streckenlänge %.0f m (FIA %.1f–%.0f km)" % (L, FIA_LAENGE_M[0] / 1000, FIA_LAENGE_M[1] / 1000),
                         ist=_r(L), soll="%.0f–%.0f m" % FIA_LAENGE_M))
    gerade = np.abs(ml.kappa_glatt) <= 1.0 / 700.0
    for a, e in laeufe(gerade):
        if (e - a) * ml.ds > FIA_MAX_GERADE_M:
            s = float(st.s[a % len(st.s)])
            m.append(meldung("FIA_KENNWERT", "Gerade von %.0f m ab %.0f m (FIA ≤ %.0f m)" % ((e - a) * ml.ds, s, FIA_MAX_GERADE_M),
                             ort(hl, s, welt_bei(s)), ist=_r((e - a) * ml.ds), soll=FIA_MAX_GERADE_M))
    q = float(np.degrees(np.abs(ml.bank)).max())
    if q > FIA_MAX_QUERNEIGUNG_GRAD:
        s = float(st.s[int(np.argmax(np.abs(ml.bank)))])
        m.append(meldung("FIA_KENNWERT", "Querneigung %.1f° bei %.0f m (FIA ≤ %.1f°)" % (q, s, FIA_MAX_QUERNEIGUNG_GRAD),
                         ort(hl, s, welt_bei(s)), ist=_r(q), soll=FIA_MAX_QUERNEIGUNG_GRAD))
    nahe = (st.s <= 150.0) | (st.s >= L - 150.0)
    steig = float(np.abs(ml.steigung[nahe]).max()) if nahe.any() else 0.0
    if steig > FIA_MAX_START_STEIGUNG:
        m.append(meldung("FIA_KENNWERT", "Steigung an Start/Ziel %.1f %% (FIA ≤ %.0f %%)" % (steig * 100, FIA_MAX_START_STEIGUNG * 100),
                         ort(hl, 0.0, welt_bei(0.0)), ist=_r(steig * 100), soll=FIA_MAX_START_STEIGUNG * 100))
    return gp


def _zonen_eingabe_pruefen(ml, param, m, welt_bei):
    """Eigene DRS-/Straight-Mode-Zonen der Definition (Meter ab Ziel)."""
    from . import zonen as zn
    gp = {"DRS_ZONE_UNGUELTIG", "DRS_ZONEN_UEBERLAPPEN", "XMODE_ZONE_UNGUELTIG", "XMODE_ZONEN_UEBERLAPPEN",
          "XMODE_IN_KURVE", "XMODE_EIGENE_UNGETESTET"}
    L = float(ml.laenge)
    hl = "mittellinie"

    def ueber(a0, a1, b0, b1):
        def teile(a, b):
            return [(a, b)] if a <= b else [(a, L), (0.0, b)]
        return any(x0 < y1 and y0 < x1 for x0, x1 in teile(a0, a1) for y0, y1 in teile(b0, b1))
    if param.get("drs", {}).get("zonen"):
        drs = zn.drs_zonen(ml, param)
        for z in drs:
            vor = (z["aktivierung_s_m"] - z["erkennung_s_m"]) % L
            if not zn.MIN_ZONE_M <= z["laenge_m"] <= L / 2 or not 0 < vor <= L / 2:
                m.append(meldung("DRS_ZONE_UNGUELTIG", "DRS-Zone %d: Aktivierung %.0f m lang, Erkennung %.0f m davor"
                                 % (z["ID"] + 1, z["laenge_m"], vor),
                                 ort(hl, z["aktivierung_s_m"], welt_bei(z["aktivierung_s_m"]), "DRS-Zone %d" % (z["ID"] + 1)),
                                 ist=_r(z["laenge_m"]), soll=">= %.0f m" % zn.MIN_ZONE_M))
        for i in range(len(drs)):
            for j in range(i + 1, len(drs)):
                a, b = drs[i], drs[j]
                if ueber(a["aktivierung_s_m"], a["ende_s_m"], b["aktivierung_s_m"], b["ende_s_m"]):
                    m.append(meldung("DRS_ZONEN_UEBERLAPPEN", "DRS-Zonen %d und %d überlappen" % (a["ID"] + 1, b["ID"] + 1),
                                     ort(hl, b["aktivierung_s_m"], welt_bei(b["aktivierung_s_m"]), "DRS-Zone %d" % (b["ID"] + 1))))
    xm = zn.xmode_zonen(ml, param)
    if xm:
        m.append(meldung("XMODE_EIGENE_UNGETESTET", "%d eigene Straight-Mode-Zone(n) statt der Träger-Zonen" % len(xm)))
        kappa = np.abs(ml.kappa_glatt)
        for z in xm:
            name = "Straight Mode %d" % z["ID"]
            if not zn.MIN_ZONE_M <= z["laenge_m"] <= L / 2:
                m.append(meldung("XMODE_ZONE_UNGUELTIG", "%s: %.0f m lang" % (name, z["laenge_m"]),
                                 ort(hl, z["von_s_m"], welt_bei(z["von_s_m"]), name), ist=_r(z["laenge_m"]),
                                 soll="%.0f … %.0f m" % (zn.MIN_ZONE_M, L / 2)))
                continue
            innen = ((ml.s - z["von_s_m"]) % L) <= z["laenge_m"]
            if innen.any() and float(kappa[innen].max()) > 1.0 / 250.0:
                k = np.flatnonzero(innen)[int(np.argmax(kappa[innen]))]
                s = float(ml.s[k])
                m.append(meldung("XMODE_IN_KURVE", "%s reicht bei %.0f m in eine Kurve mit Radius %.0f m"
                                 % (name, s, 1.0 / float(kappa[k])), ort(hl, s, welt_bei(s), name),
                                 ist=_r(1.0 / float(kappa[k])), soll=">= 250 m"))
        for i in range(len(xm)):
            for j in range(i + 1, len(xm)):
                a, b = xm[i], xm[j]
                if ueber(a["von_s_m"], a["bis_s_m"], b["von_s_m"], b["bis_s_m"]):
                    m.append(meldung("XMODE_ZONEN_UEBERLAPPEN", "Straight-Mode-Zonen %d und %d überlappen" % (a["ID"], b["ID"]),
                                     ort(hl, b["von_s_m"], welt_bei(b["von_s_m"]), "Straight Mode %d" % b["ID"])))
    return gp


def _generator_pruefen(erg, d, v, m):
    import json
    import os
    gp = {"DRS_ANZAHL", "BOX_FEHLT", "KI_KURVEN_FUER_BREMSLINIEN", "BOX_MAUER_AUF_BOXENLINIE", "LAGE_AUSSERHALB_AO",
          "LAGE_AUSSERHALB_LOCH"}
    netz = None
    if isinstance(erg, (str, os.PathLike)):
        with open(os.path.join(erg, "strecke_logik.json"), encoding="utf-8") as f:
            logik = json.load(f)
        npz = os.path.join(erg, "strecke.npz")
        if os.path.exists(npz):
            z = np.load(npz)
            netz = {"vertices": z["vertices"], "dreiecke": z["dreiecke"].astype(np.int64),
                    "oberflaeche": z["oberflaeche"].astype(np.int64), "namen": [str(n) for n in z["oberflaechen_namen"]]}
    else:
        logik = erg["logik"]
        n = erg.get("netz")
        if n is not None:
            netz = {"vertices": np.asarray(n["vertices"]), "dreiecke": np.asarray(n["dreiecke"], np.int64),
                    "oberflaeche": np.asarray(n["oberflaeche"], np.int64),
                    "namen": list(n.get("oberflaechen_namen") or n.get("namen") or [])}
    soll = 0 if d.param.get("drs", {}).get("zonen") else int(d.param.get("drs", {}).get("anzahl", 0) or 0)
    if len(logik.get("drs", [])) < soll:
        m.append(meldung("DRS_ANZAHL", "Nur %d von %d DRS-Zonen erzeugt: keine weitere geeignete Gerade"
                         % (len(logik.get("drs", [])), soll), ist=len(logik.get("drs", [])), soll=soll))
    sp = logik["spielformate"]
    gp |= {"KI_GATES_VIELE", "PVS_ZELLEN_VIELE", "KI_KURVEN_ZUSAMMENGELEGT"}
    n_kurven = len(sp["AISplineData"].get("corner_start_lines") or [])
    if n_kurven > TRAEGER_BREMSLINIEN:
        m.append(meldung("KI_KURVEN_ZUSAMMENGELEGT", "%d Kurven, der Träger hat %d Bremslinien – der Bau legt %d "
                         "Kurvenpaare mit der kürzesten Geraden dazwischen zusammen (wie Schikanen im Original)"
                         % (n_kurven, TRAEGER_BREMSLINIEN, n_kurven - TRAEGER_BREMSLINIEN),
                         ist=n_kurven, soll=TRAEGER_BREMSLINIEN))
    n_gates = len(sp["AISplineData"]["gates"])
    if n_gates > ORIGINAL_MAX_GATES:
        m.append(meldung("KI_GATES_VIELE", "%d KI-Gates (Originale höchstens %d) – Strecke kürzer oder "
                         "logik.gate_schritt_m größer" % (n_gates, ORIGINAL_MAX_GATES), ist=n_gates, soll=ORIGINAL_MAX_GATES))
    try:
        from . import spiel_pvs
        zellen = len(spiel_pvs.zellen_berechnen(spiel_pvs.linien_aus_logik(logik), 60.0))
    except Exception:  # noqa: BLE001  (Schätzung ist nur ein Hinweis)
        zellen = 0
    if zellen > ORIGINAL_MAX_PVS_ZELLEN:
        m.append(meldung("PVS_ZELLEN_VIELE", "%d PVS-Zellen bei 60-m-Korridor (Originale höchstens %d) – "
                         "Bau mit kleinerem --pvs-korridor" % (zellen, ORIGINAL_MAX_PVS_ZELLEN),
                         ist=zellen, soll=ORIGINAL_MAX_PVS_ZELLEN))
    pit = [s for s in sp["TrackSpace"]["splines"] if not s["closed"]]
    if not pit:
        m.append(meldung("BOX_FEHLT", "Generatorlogik ohne Boxengasse; der Träger braucht Boxenlinie und Boxenzonen"))
    else:
        from . import spiel_welt
        try:
            spiel_welt._bremslinien_in_kurven(sp["AISplineData"], PORTIMAO_BREMSLINIEN_HAUPT,
                                              BREMSLINIEN_MIN_ABSTAND_GATES, {})
        except spiel_welt.WeltFehler as e:
            m.append(meldung("KI_KURVEN_FUER_BREMSLINIEN", "%s (Träger Portimão: %d Haupt-Bremslinien)"
                             % (e, PORTIMAO_BREMSLINIEN_HAUPT)))
        if netz is not None and netz["namen"]:
            breite = float((d.param.get("boxengasse") or {}).get("breite", 12.0) or 12.0)
            mitte = (logik.get("boxengasse") or {}).get("mitte_position") or pit[0]["position"]
            weg = mauer_auf_boxenlinie(netz["vertices"].astype(np.float64), netz["dreiecke"], netz["oberflaeche"],
                                       netz["namen"], np.asarray(mitte, float)[:, [0, 2]], breite)
            if len(weg):
                c = netz["vertices"][netz["dreiecke"][weg[0]]].mean(0) + v
                m.append(meldung("BOX_MAUER_AUF_BOXENLINIE", "%d Mauerdreiecke liegen auf der Boxenlinie; der Bau "
                                 "entfernt sie (Spieltest v6)" % len(weg), ort(None, None, c, "Boxenmauer"), ist=len(weg)))
    if netz is not None:
        V = netz["vertices"][:, [0, 2]] + v[[0, 2]]
        aussen = np.max(np.abs(V), axis=1) > AO_HALBBREITE_M
        if aussen.any():
            i = int(np.argmax(np.max(np.abs(V), axis=1)))
            m.append(meldung("LAGE_AUSSERHALB_AO", "Generatornetz: %d Vertices außerhalb X/Z ±%d m (inkl. Versatz)"
                             % (int(aussen.sum()), AO_HALBBREITE_M), ort(None, None, netz["vertices"][i] + v, "Netz"),
                             ist=int(aussen.sum()), soll=0, stufe="generator"))
        x0, z0, kante = LOCH_AUSSENTERRAIN
        lo, hi = V.min(axis=0), V.max(axis=0)
        if lo[0] <= x0 or hi[0] >= x0 + kante or lo[1] <= z0 or hi[1] >= z0 + kante:
            groesse = hi - lo
            if max(groesse) >= kante:
                text = ("Strecke ist mit %.0f × %.0f m größer als das Loch des Außenterrains (Kante %.0f m): Der Bau bricht ab, "
                        "Strecke verkleinern" % (groesse[0], groesse[1], kante))
            else:
                ziel = (x0 + kante / 2 - (lo[0] + hi[0]) / 2 + v[0], z0 + kante / 2 - (lo[1] + hi[1]) / 2 + v[2])
                text = ("Strecke ragt über das Loch des Außenterrains hinaus: Der Bau bricht ab. Unter Werkzeuge den "
                        "Spielweltversatz auf [%.0f, %.0f, %.0f] stellen" % (ziel[0], v[1], ziel[1]))
            m.append(meldung("LAGE_AUSSERHALB_LOCH", text, ist=_r(float(max(groesse))), soll=kante, stufe="generator"))
    return gp


# =============================================================================== ERP
def logik_aus_erp(pfad_oder_archiv, code=None) -> dict:
    """Platzierte Logik einer gebauten ERP im Schema von ``logik_platziert.json`` (nur ``spielformate``)."""
    import re
    from ego import erp, streckenlogik
    from .spielformat import neutral_aus_strecke

    def laden(a):
        c = code
        if c is None:
            ai = next(r for r in a if r.resource_type == "AISplineData")
            c = re.search(r"/tracks/([^/]+)/ai/", ai.identifier).group(1)
        return neutral_aus_strecke(streckenlogik.lade(a, c))
    if isinstance(pfad_oder_archiv, (str, bytes)) or hasattr(pfad_oder_archiv, "__fspath__"):
        with erp.open_erp(pfad_oder_archiv) as a:
            return laden(a)
    return laden(pfad_oder_archiv)


def _main(argv=None):
    import argparse
    import json
    import sys
    ap = argparse.ArgumentParser(description="Prüfregeln (AP24): Katalog ausgeben oder Logik prüfen")
    ap.add_argument("--katalog", choices=("json", "md"))
    ap.add_argument("--logik", help="logik_platziert.json prüfen")
    ap.add_argument("--erp", help="gebaute ERP prüfen (Logik neu gelesen)")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if a.katalog == "md":
        print(katalog_markdown())
    elif a.katalog == "json":
        print(json.dumps(katalog(), ensure_ascii=False, indent=1))
    elif a.logik or a.erp:
        if a.logik:
            with open(a.logik, encoding="utf-8") as f:
                lg = json.load(f)
        else:
            lg = logik_aus_erp(a.erp)
        print(json.dumps(logik_pruefen(lg), ensure_ascii=False, indent=1))
    else:
        ap.print_help()


if __name__ == "__main__":
    _main()
