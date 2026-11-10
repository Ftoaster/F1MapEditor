"""Streckendefinition: JSON-Eingabe, Segmente, Kontrollpunkte und CSV-Mittellinien.

Eine Streckendefinition ist eine JSON-Datei. Pflicht ist nur der Abschnitt
``mittellinie``; alle anderen Werte haben Standardwerte (``STANDARD``), die mit den
Angaben der Datei zusammengeführt werden.

Die Mittellinie kann auf drei Arten angegeben werden:

``"art": "segmente"``
    Start (x, z, Richtung) und eine Folge von Segmenten: ``gerade`` (Länge),
    ``bogen`` (Radius + Winkel oder Länge, Richtung links/rechts) und ``klothoide``
    (Länge, Radius am Anfang/Ende). Der Restfehler beim Schließen wird verteilt.
``"art": "kontrollpunkte"``
    Liste von Punkten ``[x, z]``, ``[x, z, y]`` oder ``{"x":…, "z":…, "y":…, …}``.
    Durch die Punkte wird ein geschlossener kubischer Spline gelegt.
``"art": "csv"``
    Datei mit Spalten ``x,y,z[,breite_links,breite_rechts]`` (y = Höhe).

Konventionen (intern): Meter, y oben, Fahrbahn in der x/z-Ebene, rechtshändig.
In der Draufsicht zeigt x nach rechts und z nach unten. Richtungswinkel werden in
der Draufsicht gegen den Uhrzeigersinn gemessen: 0° = +x, 90° = -z.
Die Reihenfolge der Punkte bzw. Segmente ist die Fahrtrichtung.
"""

import copy
import json
import math
import os
from dataclasses import dataclass, field

import numpy as np

# --------------------------------------------------------------------------- Oberflächen
#: Oberflächenklassen in fester Reihenfolge (Index = Oberflächen-Nummer im Netz).
OBERFLAECHEN = ["asphalt", "randstein", "gras", "kies", "mauer", "boxengasse", "linie", "auslauf", "startbox",
                "gebaeude"]
OBERFLAECHE = {name: i for i, name in enumerate(OBERFLAECHEN)}

#: Abbildung Oberflächenklasse -> 4-Zeichen-Code der Fahrphysik (TRN~).
#: Beleg: docs/formate/trn.md, Abschnitt 4; Häufigkeiten in ergebnisse/trn_analyse.md.
#: Codevorkommen sind gesichert, Materialbedeutung und Reibwerte nicht im Spiel geprüft.
STANDARD_CODES = {
    "asphalt": {"code": "SDT+", "status": "sehr plausibel",
                "hinweis": "Hauptfahrbahn im Export; Bahrain 42694, Portimão 60550 Dreiecke"},
    "randstein": {"code": "RMBL", "status": "sehr plausibel",
                  "hinweis": "Lage am Fahrbahnrand; Bahrain 44400, Portimão 18351 Dreiecke"},
    "gras": {"code": "GRS+", "status": "sehr plausibel",
             "hinweis": "Grünflächen im Export; Bahrain 8190, Portimão 27597 Dreiecke"},
    "kies": {"code": "HDG+", "status": "sehr plausibel",
             "hinweis": "Auslauf im Export; Bahrain 19071, Portimão 14648 Dreiecke"},
    "mauer": {"code": "SCON", "status": "vermutet",
              "hinweis": "Betonbarriere vermutet; Bahrain 2760, Portimão 630 Dreiecke"},
    "boxengasse": {"code": "SDT+", "status": "sehr plausibel",
                   "hinweis": "Wie Hauptfahrbahn, Lage an Boxenanlage im Portimão-Materialtest"},
    "linie": {"code": "MKNG", "status": "sehr plausibel",
              "hinweis": "Schmale Markierungen; Bahrain 33226 Dreiecke auf nur 2358 m²"},
    "auslauf": {"code": "SDT+", "status": "sehr plausibel",
                "hinweis": "Asphalt-Auslaufzone (Seitenraum-Abschnitt, 27.10.2026); Code wie Fahrbahn"},
    "startbox": {"code": "MKNG", "status": "sehr plausibel",
                 "hinweis": "Bodenmarkierungen über dem Asphalt (Startboxen, Boxengassen-Linien; 27.10.2026); nur "
                            "sichtbar, nicht in der Fahrphysik"},
    "gebaeude": {"code": "SCON", "status": "vermutet",
                 "hinweis": "Garagengebäude der Boxengasse (27.10.2026); Beton wie die Mauern"},
}

# --------------------------------------------------------------------------- Achsen
#: Umrechnung intern (x, y oben, z) -> Ausgabe. Determinante < 0 = Spiegelung,
#: dann wird beim Netz die Dreiecksreihenfolge umgedreht (Normalen bleiben oben).
ACHSEN = {
    # intern = Ausgabe: x rechts, y oben, z in der Draufsicht nach unten
    "y_oben_rechtshaendig": np.eye(3),
    # wie DirectX: y oben, linkshändig; z wird gespiegelt, Form bleibt in der Draufsicht gleich
    "y_oben_linkshaendig": np.diag([1.0, 1.0, -1.0]),
    # Blender: z oben, rechtshändig (X = x, Y = -z, Z = y)
    "z_oben_rechtshaendig": np.array([[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]]),
}


def achsen_matrix(name):
    if name not in ACHSEN:
        raise ValueError("Unknown axis system '%s' (possible: %s)" % (name, ", ".join(ACHSEN)))
    return ACHSEN[name]


# --------------------------------------------------------------------------- Standardwerte
STANDARD = {
    "name": "Unbenannte Strecke",
    "beschreibung": "",
    "mittellinie": {
        "art": None,                 # segmente | kontrollpunkte | csv
        "abtastung_m": 1.0,          # Abstand der Stützpunkte der Logik
        "glaettung_m": 0.0,          # Gauß-Glättung der Eingabepunkte (Sigma in m), 0 = aus
        # Glättende Approximation (Sigma in m, z. B. 6–10), 0 = aus: Polygon gleichmäßig neu abtasten,
        # glätten, Spline durch ausgedünnte Knoten – unabhängig von Punktabständen/Lücken (strecke/mittellinie.py)
        "approximation_m": 0.0,
        "hoehen_glaettung_m": 40.0,  # gleitender Mittelwert der Höhe (Fensterbreite in m)
        "schliessen": "verteilen",   # Restfehler bei Segmenten: verteilen | fehler
        "max_schliessfehler_m": 50.0,
        # Automatische Klothoiden-Übergänge im Segmentmodus (Krümmung wächst linear statt zu springen).
        # Standard aus: vorhandene Strecken bleiben bitgleich. Je Segment überschreibbar mit
        # "uebergang": false | true | Länge | {"anfang": …, "ende": …}. Details: docs/streckengenerator.md
        "uebergaenge": {
            "aktiv": False,
            "laenge_m": None,        # feste Länge für alle Übergänge; None = aus Radius (A = a_faktor · R)
            "a_faktor": 0.5,         # Klothoidenparameter A = a_faktor · kleinster Radius (Faustregel R/3 … R)
            "min_laenge_m": 20.0,    # Grenzen der automatischen Länge
            "max_laenge_m": 150.0,
            "max_anteil": 0.4,       # Übergang höchstens dieser Anteil des kürzeren Nachbarsegments
        },
        "umkehren": False,           # Fahrtrichtung umdrehen
        "csv_achsen": "y_oben_rechtshaendig",  # Achsensystem einer CSV-Datei
    },
    "fahrbahn": {
        "breite": 12.0,              # Gesamtbreite (links + rechts)
        "breite_links": None,        # überschreibt breite/2
        "breite_rechts": None,
        "abschnitte": [],            # [{"von_s":…, "bis_s":…, "breite"|"breite_links"|"breite_rechts"}]
        "uebergang_m": 40.0,         # Breitenänderungen werden über diese Länge geglättet
        "querneigung_grad": 0.0,     # positiv = linke Seite höher
        "randlinie_breite": 0.25,    # weiße Randlinie innerhalb der Fahrbahn (0 = keine)
    },
    "randstein": {
        "breite": 1.5,
        "kruemmung_schwelle": 1.0 / 400.0,  # Randsteine ab dieser Krümmung (1/m)
        "vorlauf_m": 10.0,           # innen: vor Kurvenbeginn
        "nachlauf_m": 10.0,          # innen: nach Kurvenende
        "aussen_nachlauf_m": 40.0,   # außen: vom Scheitel bis Kurvenende + diese Länge
        "rampe_m": 4.0,              # Breite läuft über diese Länge an/aus
    },
    "gruenstreifen": {"breite": 2.0},
    # Seitenraum je Streckenbereich (27.10.2026, Mapeditor-Reiter Querschnitt): Abschnitte überschreiben die
    # Automatik zwischen von_s und bis_s (Meter ab Eingabeursprung wie fahrbahn.abschnitte) auf einer Seite:
    # {"von_s", "bis_s", "seite": "links"|"rechts"|"beide", "randstein_breite" (0 = keiner),
    #  "gruenstreifen_breite", "auslauf": "gras"|"kies"|"asphalt", "barriere_abstand",
    #  "rand_neigung": "strecke" (Fahrbahnneigung läuft weiter) | Grad (positiv = steigt nach außen)};
    #  fehlende Felder = Automatik, ohne rand_neigung bleibt der Rand waagerecht
    # rand_breite_m: so weit neben der Fahrbahn wirkt der Randwinkel, danach waagerecht (sonst summiert er sich bei weit
    # entfernter Barriere auf; LongBoy: bis 13 m Höhe)
    "seitenraum": {"abschnitte": [], "rand_breite_m": 12.0},
    "kiesbett": {
        "aktiv": True,
        "kruemmung_schwelle": 1.0 / 200.0,  # Kurven, die ein Kiesbett bekommen
        "vorlauf_m": 20.0,           # vor dem Scheitel
        "nachlauf_m": 80.0,          # nach dem Kurvenende
    },
    "barriere": {
        "abstand": 20.0,             # Abstand Fahrbahnrand -> Barriere (Auslaufzone)
        "hoehe": 1.2,
        "dicke": 0.6,
        "mindestabstand": 2.0,       # nie näher an der Fahrbahn
        "luecke_m": 2.0,             # Abstand zwischen Barrieren benachbarter Streckenteile
    },
    "start_ziel": {
        "s": 0.0,                    # Bogenlänge auf der Eingabelinie (ab erstem Punkt)
        "punkt": None,               # alternativ [x, z]: nächster Punkt der Mittellinie
        "linienbreite": 0.6,
    },
    "bremsmarker": {                 # 25.10.2026: Entfernungstafeln (50/100/150/200/250 m) vor Kurven mit starker Bremsung
        "aktiv": True,
        "min_abbau_kmh": 50.0,       # Geschwindigkeitsabbau in der Bremszone, ab dem eine Kurve Tafeln bekommt
        "abstaende_m": [50, 100, 150, 200, 250],   # Varianten der Träger-Tafel (por_dressing_brake_marker_a)
        "rand_m": 6.0,               # Abstand vom Fahrbahnrand (höchstens bis 1,2 m vor die Barriere)
    },
    "startaufstellung": {
        "anzahl": 22,
        "abstand_m": 8.0,            # Abstand aufeinanderfolgender Plätze (längs)
        "abstand_pole_m": 6.0,       # Pole-Position hinter der Startlinie
        "pole_seite": "links",
        "seitenversatz_m": None,     # None = ein Viertel der Fahrbahnbreite
        "fahrzeuglaenge_m": 5.6,
        # Startboxen auf dem Asphalt (27.10.2026): Querlinie vor jedem Platz mit zwei Schenkeln nach hinten
        "startboxen": True,
    },
    "boxengasse": {
        "aktiv": False,
        "seite": "rechts",
        "einfahrt_vor_start_m": 400.0,   # Beginn der Einfahrt vor der Startlinie
        "ausfahrt_nach_start_m": 400.0,  # Ende der Ausfahrt nach der Startlinie
        "breite": 12.0,              # schnelle Spur + Arbeitsspur
        "abstand_fahrbahn": 20.0,    # Grünfläche zwischen Fahrbahn und Boxengasse (bis 27.10.2026: 12)
        "anlauf_m": 50.0,            # Breite der Boxengasse läuft am Fahrbahnrand an
        "uebergang_m": 120.0,        # seitliches Abwandern auf den vollen Abstand
        # KI-Boxenroute nach dem Ende der Boxengasse (3.11.2026): Bei allen Spielstrecken läuft sie nach dem Tempolimit noch
        # 380–660 m weiter und fädelt langsam ein; ohne diese Spur zog die KI sofort quer zur Ideallinie (bei Boxen links aufs Gras)
        "ausfahrt_spur_m": 250.0,
        "hinter_box_m": 4.0,         # Fläche zwischen Boxengasse und Garagenwand
        "boxen": 11,
        "box_abstand_m": 14.0,
        # Tempolimit in der Boxengasse: Die Spielstrecken nutzen nur 60 oder 80 km/h (TrackPitData, 3.11.2026); der Bau
        # schreibt es mit --renndaten in die Spieldatenbank (strecke/renndaten.py), die KI-Fahrdaten rechnen damit
        "tempolimit_kmh": 80.0,
        "mauer_hoehe": 1.0,
        "mauer_dicke": 0.4,
        "mauer_abstand_m": 0.6,      # Boxenmauer vor dem inneren Rand der Boxengasse
        # Boxenlinie auf der schnellen Spur (30.10.2026): Bei allen Originalen liegt die Box außerhalb der Fahrspur, die
        # Linie 4,8–14,7 m neben der Box, die Spur ±2–4 m breit. Versatz aus der Gassenmitte Richtung Strecke, Spur je Seite.
        "spur_versatz_m": 3.0,
        "spur_halbbreite_m": 2.5,
        # Markierungen (27.10.2026): Querlinien an Beginn/Ende des Tempolimits, Linien an Ein- und Ausfahrt
        "linien": True,
        "linie_breite_m": 0.3,
        # Garagengebäude hinter den Boxen (27.10.2026): je Box eine Garage (Breite = box_abstand_m), vorne offen
        "gebaeude": True,
        "gebaeude_tiefe_m": 15.0,
        "gebaeude_hoehe_m": 6.0,
        "gebaeude_wand_m": 0.3,
        # Woher die Garagen kommen (29.10.2026, Video des Nutzers: Betonschalen wirken kahl):
        # "traeger" = Portimãos Garagenmodule (garage_main_a, Teamfarben, Tafeln, Kommandostände) je Box versetzt
        # (spiel_welt.garagen_versetzen); "generiert" = Betonschalen des Generators (bis 29.10.2026)
        "gebaeude_modell": "traeger",
    },
    "sektoren": {"anzahl": 3, "suchbereich_anteil": 0.08},
    "drs": {
        "anzahl": 2,
        "kruemmung_gerade": 1.0 / 700.0,  # Gerade = |Krümmung| darunter
        "mindestlaenge_m": 400.0,
        "erkennung_vor_m": 150.0,    # Erkennungspunkt vor Beginn der Geraden
        "aktivierung_nach_m": 60.0,  # Aktivierung nach Beginn der Geraden
        "ende_vor_m": 0.0,           # Ende vor dem Ende der Geraden
        # Eigene Zonen ersetzen die Automatik: [{"erkennung_m", "aktivierung_m", "ende_m"}], Meter ab Ziel
        "zonen": [],
    },
    # Straight Mode (2026er Aero, Spiel-TM ``x_mode``): [{"von_m", "bis_m", "partial", "partial_versatz_m"}],
    # Meter ab Ziel; leer = Zonen des Trägers (strecke/zonen.py). Partial-Zone (Standard an, 60 m nach dem
    # Zonenbeginn bis zum Zonenende wie in den Originalen); partial=false oder Versatz null = ohne.
    "x_mode": {"zonen": []},
    # Kameras des Trägers (Wiederholung, TV, Boxenstopp; strecke/spiel_kameras.py): mitfuehren = an die neue Strecke
    # versetzen; gruppen = {Gruppe: {"aus", "verschiebung_m", "seitlich_m", "hoehe_m"}} (Gruppe z. B. "s07a")
    "kameras": {"mitfuehren": True, "gruppen": {}},
    "kurven": {
        "kruemmung_schwelle": 1.0 / 500.0,
        "mindestwinkel_grad": 15.0,
        "zusammenfassen_m": 25.0,    # gleichsinnige Abschnitte mit kleinerer Lücke = eine Kurve
        "glaettung_m": 20.0,
    },
    "ideallinie": {
        "schritt_m": 3.0,            # Stützstellenabstand der Optimierung
        "sicherheitsabstand_m": 1.5, # Fahrzeugmitte mindestens so weit vom Fahrbahnrand
        "kruemmung_glaettung_m": 4.0,
        # Schritt C (24.10.2026): iterative Minimal-Krümmung, Abbruch bei Verschiebung < Toleranz,
        # feine Rückprojektion ohne Kappen an Mittellinien-Normalen, optionale Krümmungsgrenze (1/m, ADMM).
        # Standard seit 25.10.2026 (vorher 1 / False): die einmal um die Mittellinie linearisierte Lösung
        # klebte in engen Kurven am Außenrand und hatte Knicke aus dem 3-m-Raster → Scheinbremszonen
        # (Editor-Strecke: 57 Bremszonen bei 16 Kurven; TUM-Referenz bei gleicher Glättung +3,0 % statt +1,8 %).
        "iterationen": 15,
        "iterationen_toleranz_m": 0.01,
        "feine_rueckprojektion": True,
        "kruemmung_max": None,
        # 25.10.2026: Mindestzeit-Feinschliff nach der Minimal-Krümmung (rennlinie.mindestzeit_verfeinern): glatte
        # Verformungen je Kurve, nur übernommen, wenn die Runde mit dem Bewertungsfahrzeug schneller wird
        "mindestzeit": True,
        "mindestzeit_fahrzeug": "f1_25_ki_portimao",   # an der Spiel-KI kalibriert; None = fahrzeug-Abschnitt
    },
    "fahrzeug": {                    # grob F1-ähnliche Punktmasse
        "masse_kg": 800.0,
        "leistung_kw": 600.0,             # wirksam am Rad
        "cw_a_m2": 1.5,              # Luftwiderstand c_w * A
        "ca_a_m2": 4.0,              # Abtrieb c_A * A
        "luftdichte": 1.2,
        "reibwert": 1.5,             # Reifen (quer und beim Bremsen)
        "traktion_anteil": 0.6,      # Anteil der Normalkraft auf der Antriebsachse
        "rollwiderstand": 0.015,
        "v_max_kmh": 345.0,
        # Schritt D (24.10.2026): "punktmasse" (bisher, Standard) | "ggv" (g-g-v-Modell,
        # getrennte Reibwerte, Reibungsellipse, Steigung/vertikale Krümmung). Ein
        # Parametersatz aus strecke/fahrzeuge/<name>.json überschreibt die Werte oben.
        "modell": "punktmasse",
        "parametersatz": None,       # z. B. "f1_2025_fastf1", "f1_25_ki_portimao"
        "ggv": {
            "reibwert_quer": None,       # None = reibwert
            "reibwert_bremsen": None,    # None = reibwert
            "reibwert_antrieb": None,    # None = traktion_anteil * reibwert
            "ellipse_exponent": 2.0,     # 2 = Ellipse, größer = eckiger (mehr Kombi-Griff)
            "verzoegerung_max_ms2": None,  # Grenze der Bremsanlage (None = keine)
            "hoeheneinfluss": True,      # Steigung und vertikale Krümmung
            "hoehe_glaettung_m": 10.0,
        },
    },
    "geometrie": {
        "schritt_m": 2.0,            # Stationsabstand des Netzes
        "textur_laenge_m": 10.0,     # V-Koordinate wiederholt sich alle x m
        "randstein_textur_m": 4.0,
    },
    "logik": {"trackspace_schritt_m": 9.0, "gate_schritt_m": 15.0,
              "volume_schritt_m": 30.0},
    "ausgabe": {
        "achsen": "y_oben_rechtshaendig",
        "netz_json_voll": True,      # Netz zusätzlich als JSON mit allen Werten
    },
    "oberflaechen_codes": copy.deepcopy(STANDARD_CODES),
}


def _fahrzeug_pruefen(f):
    """Schritt D: Fahrzeugmodell, Parametersatz und g-g-v-Werte prüfen."""
    if f.get('modell', 'punktmasse') not in ('punktmasse', 'ggv'):
        raise ValueError('fahrzeug.modell must be punktmasse or ggv')
    if f.get('parametersatz') is not None:
        if not isinstance(f['parametersatz'], str) or not f['parametersatz'].replace('_', '').isalnum():
            raise ValueError('fahrzeug.parametersatz must be a name from strecke/fahrzeuge/')
        from .rennlinie import parametersatz_laden
        parametersatz_laden(f['parametersatz'])
    g = f.get('ggv') or {}
    if not isinstance(g, dict):
        raise ValueError('fahrzeug.ggv must be an object')
    for name in ('reibwert_quer', 'reibwert_bremsen', 'reibwert_antrieb', 'verzoegerung_max_ms2'):
        if g.get(name) is not None and not float(g[name]) > 0:
            raise ValueError('fahrzeug.ggv.%s must be > 0 (or empty)' % name)
    if not 1.0 <= float(g.get('ellipse_exponent', 2.0)) <= 10.0:
        raise ValueError('fahrzeug.ggv.ellipse_exponent must be within [1, 10]')
    if not float(g.get('hoehe_glaettung_m', 10.0)) >= 0:
        raise ValueError('fahrzeug.ggv.hoehe_glaettung_m must not be negative')
    if not isinstance(g.get('hoeheneinfluss', True), bool):
        raise ValueError('fahrzeug.ggv.hoeheneinfluss must be true or false')


def zusammenfuehren(basis, zusatz):
    """Tiefe Zusammenführung zweier Wörterbücher (zusatz gewinnt)."""
    erg = copy.deepcopy(basis)
    for k, v in (zusatz or {}).items():
        if isinstance(v, dict) and isinstance(erg.get(k), dict):
            erg[k] = zusammenfuehren(erg[k], v)
        else:
            erg[k] = copy.deepcopy(v)
    return erg


# --------------------------------------------------------------------------- Datenklasse
@dataclass
class Streckendefinition:
    """Ausgewertete Definition: Eingabepunkte + Parameter.

    punkte:    (N, 3) x, y (Höhe), z – geschlossen, letzter Punkt ungleich erstem
    attribute: je Punkt optionale Werte (NaN = nicht angegeben):
               breite_links, breite_rechts, querneigung_grad
    dicht:     True, wenn die Punkte dicht liegen (Segmente, CSV)
    """

    name: str
    param: dict
    punkte: np.ndarray
    attribute: dict
    dicht: bool
    quelle: str
    basisordner: str = "."
    warnungen: list = field(default_factory=list)
    roh: dict = field(default_factory=dict)
    #: Segmentmodus: Punktindex des Anfangs jedes Eingabesegments (bei Übergängen die Übergangsmitte)
    segment_anfaenge: list = None
    #: Segmentmodus: eingefügte Übergänge [{"nach_segment", "laenge_m", "dk"}]
    uebergaenge: list = None


# --------------------------------------------------------------------------- Laden
SEITENRAUM_AUSLAUF = ("gras", "kies", "asphalt")


#: Größter Randwinkel in Grad (seitenraum.abschnitte[].rand_neigung)
RAND_NEIGUNG_MAX = 30.0


def seitenraum_pruefen(seitenraum):
    """Abschnitte des Seitenraums prüfen (Felder siehe STANDARD["seitenraum"])."""
    abschnitte = (seitenraum or {}).get("abschnitte") or []
    if not isinstance(abschnitte, list):
        raise ValueError("seitenraum.abschnitte must be a list")
    for i, ab in enumerate(abschnitte):
        ort = "seitenraum.abschnitte[%d]" % i
        if not isinstance(ab, dict) or ab.get("von_s") is None or ab.get("bis_s") is None:
            raise ValueError("%s needs von_s and bis_s" % ort)
        if ab.get("seite", "beide") not in ("links", "rechts", "beide"):
            raise ValueError("%s.seite must be links, rechts or beide" % ort)
        if ab.get("auslauf") is not None and ab["auslauf"] not in SEITENRAUM_AUSLAUF:
            raise ValueError("%s.auslauf must be gras, kies or asphalt" % ort)
        for name, minimum in (("randstein_breite", 0.0), ("gruenstreifen_breite", 0.0), ("barriere_abstand", 0.5)):
            if ab.get(name) is not None and not float(ab[name]) >= minimum:
                raise ValueError("%s.%s must be ≥ %g" % (ort, name, minimum))
        rand = ab.get("rand_neigung")
        if rand is not None and rand != "strecke":
            if isinstance(rand, str) or not -RAND_NEIGUNG_MAX <= float(rand) <= RAND_NEIGUNG_MAX:
                raise ValueError("%s.rand_neigung must be \"strecke\" or an angle up to ±%g°" % (ort, RAND_NEIGUNG_MAX))


def laden(pfad):
    """Lädt eine Streckendefinition aus einer JSON-Datei."""
    with open(pfad, "r", encoding="utf-8-sig") as f:
        daten = json.load(f)
    return aus_dict(daten, basisordner=os.path.dirname(os.path.abspath(pfad)), quelle=pfad)


def aus_dict(daten, basisordner=".", quelle="<dict>"):
    """Wertet ein Definitions-Wörterbuch aus (Standardwerte + Mittellinie)."""
    param = zusammenfuehren(STANDARD, daten)
    _parameter_pruefen(param)
    ml = param["mittellinie"]
    art = ml.get("art")
    warnungen = []
    info = {}
    if art == "segmente":
        punkte, attr, w = segmente_zu_punkten(ml.get("start", {}), ml.get("segmente", []),
                                              schliessen=ml["schliessen"],
                                              max_fehler=ml["max_schliessfehler_m"],
                                              uebergaenge=ml.get("uebergaenge"), info=info)
        warnungen += w
        dicht = True
    elif art == "kontrollpunkte":
        punkte, attr = kontrollpunkte_lesen(ml.get("punkte", []))
        dicht = False
    elif art == "csv":
        datei = ml.get("datei")
        if not datei:
            raise ValueError("mittellinie.datei is missing (art = csv)")
        if not os.path.isabs(datei):
            datei = os.path.join(basisordner, datei)
        punkte, attr = csv_lesen(datei, achsen=ml.get("csv_achsen", "y_oben_rechtshaendig"))
        dicht = True
    else:
        raise ValueError("mittellinie.art must be 'segmente', 'kontrollpunkte' or 'csv' (given: %r)" % (art,))
    if len(punkte) < 4:
        raise ValueError("Centreline needs at least 4 points (has %d)" % len(punkte))
    if not np.all(np.isfinite(punkte)):
        raise ValueError("Centreline points must be finite")
    if ml.get("umkehren"):
        info.pop("segment_anfaenge", None)   # Indizes gelten nur für die Eingaberichtung
        punkte = punkte[::-1].copy()
        attr = {k: v[::-1].copy() for k, v in attr.items()}
        # links/rechts tauschen, Querneigung kehrt das Vorzeichen um
        attr["breite_links"], attr["breite_rechts"] = attr["breite_rechts"], attr["breite_links"]
        attr["querneigung_grad"] = -attr["querneigung_grad"]
    return Streckendefinition(name=param.get("name", "Strecke"), param=param, punkte=punkte,
                              attribute=attr, dicht=dicht, quelle=quelle,
                              basisordner=basisordner, warnungen=warnungen, roh=daten,
                              segment_anfaenge=info.get("segment_anfaenge"),
                              uebergaenge=info.get("uebergaenge"))


#: Tempolimits der Boxengasse, die das Spiel bei seinen Strecken nutzt (TrackPitData.pitSpeedLimit)
TEMPOLIMITS_KMH = (60.0, 80.0)


def _parameter_pruefen(p):
    """Verhindert unbrauchbare Abstände, Fahrzeugwerte und Oberflächencodes früh."""
    def endlich(obj):
        if isinstance(obj,dict):
            for v in obj.values():
                endlich(v)
        elif isinstance(obj,list):
            for v in obj:
                endlich(v)
        elif isinstance(obj,(int,float)) and not math.isfinite(obj):
            raise ValueError('Definition values must be finite')
    endlich(p)
    achsen_matrix(p['ausgabe']['achsen'])
    for gruppe, namen in {
        'mittellinie': ('abtastung_m',), 'geometrie': ('schritt_m', 'textur_laenge_m', 'randstein_textur_m'),
        'ideallinie': ('schritt_m',), 'logik': ('trackspace_schritt_m','gate_schritt_m','volume_schritt_m'),
        'fahrzeug': ('masse_kg','leistung_kw','cw_a_m2','ca_a_m2','luftdichte','reibwert','v_max_kmh'),
        'startaufstellung': ('abstand_m','abstand_pole_m','fahrzeuglaenge_m'),
        'boxengasse': ('breite','anlauf_m','uebergang_m','box_abstand_m'),
    }.items():
        for name in namen:
            wert = float(p[gruppe][name])
            if not math.isfinite(wert) or wert <= 0:
                raise ValueError('%s.%s must be finite and > 0' % (gruppe,name))
    _uebergaenge_pruefen(p['mittellinie'].get('uebergaenge'))
    if not 0 < p['fahrzeug']['traktion_anteil'] <= 1:
        raise ValueError('fahrzeug.traktion_anteil must be within (0, 1]')
    _fahrzeug_pruefen(p['fahrzeug'])
    for gruppe,name in (('startaufstellung','anzahl'),('boxengasse','boxen'),('drs','anzahl')):
        wert = p[gruppe][name]
        if not isinstance(wert,int) or isinstance(wert,bool) or wert < (0 if gruppe=='drs' else 1):
            raise ValueError('%s.%s must be a valid whole number' % (gruppe,name))
    if float(p['boxengasse']['tempolimit_kmh']) not in TEMPOLIMITS_KMH:
        raise ValueError('boxengasse.tempolimit_kmh must be %s (the values of the game\'s tracks)'
                         % '/'.join('%g' % v for v in TEMPOLIMITS_KMH))
    seitenraum_pruefen(p.get('seitenraum'))
    from . import zonen
    zonen.eingabe_pruefen(p)
    pi = p['ideallinie']
    if not isinstance(pi['iterationen'], int) or isinstance(pi['iterationen'], bool) or pi['iterationen'] < 1:
        raise ValueError('ideallinie.iterationen must be a whole number ≥ 1')
    if not float(pi['iterationen_toleranz_m']) > 0:
        raise ValueError('ideallinie.iterationen_toleranz_m must be > 0')
    if pi.get('kruemmung_max') is not None and not float(pi['kruemmung_max']) > 0:
        raise ValueError('ideallinie.kruemmung_max must be > 0 (or empty)')
    for gruppe,name in (('boxengasse','seite'),('startaufstellung','pole_seite')):
        if p[gruppe][name] not in ('links','rechts'):
            raise ValueError('%s.%s must be links or rechts' % (gruppe,name))
    if p['ideallinie']['sicherheitsabstand_m'] < 0:
        raise ValueError('Safety margin must not be negative')
    if p['fahrbahn']['randlinie_breite'] < 0 or p['start_ziel']['linienbreite'] < 0:
        raise ValueError('Line widths must not be negative')
    for name in OBERFLAECHEN:
        code = p['oberflaechen_codes'][name]['code']
        if not isinstance(code,str) or len(code)!=4 or not code.isascii():
            raise ValueError('Surface code for %s must contain four ASCII characters' % name)


def _leere_attribute(n):
    return {k: np.full(n, np.nan) for k in ("breite_links", "breite_rechts", "querneigung_grad")}


def _breiten_aus_angabe(eintrag):
    """Liefert (links, rechts, querneigung) aus einem Segment/Punkt (NaN = nicht angegeben)."""
    bl = br = np.nan
    if eintrag.get("breite") is not None:
        bl = br = float(eintrag["breite"]) / 2.0
    if eintrag.get("breite_links") is not None:
        bl = float(eintrag["breite_links"])
    if eintrag.get("breite_rechts") is not None:
        br = float(eintrag["breite_rechts"])
    qn = float(eintrag["querneigung_grad"]) if eintrag.get("querneigung_grad") is not None else np.nan
    return bl, br, qn


# --------------------------------------------------------------------------- Segmente
def _segment_kruemmung(seg):
    """Länge und Krümmung am Anfang/Ende eines Segments (Krümmung > 0 = Linkskurve)."""
    typ = seg.get("typ")
    if typ == "gerade":
        return float(seg["laenge"]), 0.0, 0.0
    vorz = {"links": 1.0, "rechts": -1.0}.get(seg.get("richtung"))
    if vorz is None:
        raise ValueError("Segment %r: direction must be 'links' or 'rechts'" % seg)
    if typ == "bogen":
        r = float(seg["radius"])
        if r <= 0:
            raise ValueError("Segment %r: radius must be > 0" % seg)
        if seg.get("winkel") is not None:
            laenge = r * math.radians(float(seg["winkel"]))
        else:
            laenge = float(seg["laenge"])
        return laenge, vorz / r, vorz / r
    if typ == "klothoide":
        r0 = seg.get("radius_anfang")
        r1 = seg.get("radius_ende")
        k0 = 0.0 if not r0 else vorz / float(r0)
        k1 = 0.0 if not r1 else vorz / float(r1)
        return float(seg["laenge"]), k0, k1
    raise ValueError("Unknown segment type %r (gerade | bogen | klothoide)" % typ)


#: Standardwerte der Übergänge (gleich STANDARD["mittellinie"]["uebergaenge"]).
UEBERGANG_STANDARD = {"aktiv": False, "laenge_m": None, "a_faktor": 0.5, "min_laenge_m": 20.0,
                      "max_laenge_m": 150.0, "max_anteil": 0.4}
#: Kürzere Übergänge werden weggelassen (Krümmungssprung bleibt).
UEBERGANG_MIN_M = 2.0


def _uebergaenge_pruefen(regeln):
    """Prüft mittellinie.uebergaenge (None = Standard)."""
    if regeln is None:
        return
    if not isinstance(regeln, dict):
        raise ValueError("mittellinie.uebergaenge must be an object")
    r = dict(UEBERGANG_STANDARD, **regeln)
    if not isinstance(r["aktiv"], bool):
        raise ValueError("mittellinie.uebergaenge.aktiv must be true or false")
    for name in ("a_faktor", "min_laenge_m", "max_laenge_m", "max_anteil"):
        if isinstance(r[name], bool) or not isinstance(r[name], (int, float)) or not r[name] > 0:
            raise ValueError("mittellinie.uebergaenge.%s must be a number > 0" % name)
    if r["laenge_m"] is not None and (isinstance(r["laenge_m"], bool) or
                                      not isinstance(r["laenge_m"], (int, float)) or r["laenge_m"] <= 0):
        raise ValueError("mittellinie.uebergaenge.laenge_m must be empty or > 0")
    if r["max_laenge_m"] < r["min_laenge_m"]:
        raise ValueError("mittellinie.uebergaenge.max_laenge_m must be >= min_laenge_m")
    if not r["max_anteil"] <= 0.9:
        raise ValueError("mittellinie.uebergaenge.max_anteil must be within (0, 0.9]")


def _uebergang_angabe(seg, ende):
    """Segmentangabe "uebergang" für Anfang (ende=False) bzw. Ende: False | None (auto) | Länge."""
    wert = seg.get("uebergang")
    if isinstance(wert, dict):
        wert = wert.get("ende" if ende else "anfang")
    if wert is None or wert is True:
        return None
    if wert is False:
        return False
    if isinstance(wert, (int, float)) and math.isfinite(wert) and wert > 0:
        return float(wert)
    raise ValueError("Segment %r: transition must be true, false, a length > 0 or "
                     "{\"anfang\": …, \"ende\": …}" % seg)


def uebergang_laenge(ka, kb, regeln):
    """Automatische Übergangslänge zwischen den Krümmungen ka und kb (1/m).

    Klothoide: Krümmung ändert sich linear, A² = L / |Δk|. Mit A = a_faktor · R
    (R = kleinerer Radius der beiden Seiten) folgt L = (a_faktor · R)² · |Δk|,
    für Gerade → Bogen also L = a_faktor² · R (Standard 0,5 → L = R/4), begrenzt
    auf [min_laenge_m, max_laenge_m]. Eine feste laenge_m ersetzt die Formel.
    """
    r = dict(UEBERGANG_STANDARD, **(regeln or {}))
    if r.get("laenge_m"):
        return float(r["laenge_m"])
    dk = abs(kb - ka)
    kmax = max(abs(ka), abs(kb))
    if dk < 1e-12 or kmax < 1e-12:
        return 0.0
    a = float(r["a_faktor"]) / kmax
    return min(max(a * a * dk, float(r["min_laenge_m"])), float(r["max_laenge_m"]))


def segmente_mit_uebergaengen(segmente, regeln):
    """Zerlegt die (geschlossene) Segmentfolge in Stücke mit Klothoiden-Übergängen.

    Jeder Übergang der Länge L liegt mittig auf der Grenze zweier Segmente: er
    nimmt L/2 vom Ende des vorigen und L/2 vom Anfang des folgenden Segments und
    lässt die Krümmung linear von k_vor nach k_nach laufen. Gesamtlänge und
    Gesamtdrehung bleiben damit exakt erhalten; die Linie hinter dem Übergang
    verschiebt sich seitlich um Δk·L²/24 (Gerade → Bogen: L²/(24 R), die
    klassische Abrückung). Diese Versätze summieren sich zum Schließfehler, den
    segmente_zu_punkten wie bisher verteilt.

    Rückgabe: (stuecke, uebergaenge, warnungen). Stück = dict(laenge, k0, k1,
    quelle (Segmentindex), von, bis (Anteile der Segmentlänge)).
    Grenzen an einer Klothoide (vom Nutzer gesetzt) erhalten keinen Übergang.
    """
    r = dict(UEBERGANG_STANDARD, **(regeln or {}))
    n = len(segmente)
    form = [_segment_kruemmung(seg) for seg in segmente]
    for seg, (laenge, _, _) in zip(segmente, form):
        if laenge <= 0:
            raise ValueError("Segment %r has length <= 0" % seg)
    L = [0.0] * n            # Übergang an der Grenze i -> i+1 (Index i)
    uebergaenge, gekuerzt, weggelassen = [], [], []
    for i in range(n if n > 1 else 0):
        j = (i + 1) % n
        a, b = segmente[i], segmente[j]
        ka, kb = form[i][2], form[j][1]
        if abs(kb - ka) < 1e-9 or "klothoide" in (a.get("typ"), b.get("typ")):
            continue
        angaben = [_uebergang_angabe(a, True), _uebergang_angabe(b, False)]
        if False in angaben:
            continue
        explizit = [v for v in angaben if v is not None]
        laenge = max(explizit) if explizit else uebergang_laenge(ka, kb, r)
        grenze = float(r["max_anteil"]) * min(form[i][0], form[j][0])
        if laenge > grenze:
            gekuerzt.append(i + 1)
            laenge = grenze
        if laenge < UEBERGANG_MIN_M:
            weggelassen.append(i + 1)
            continue
        L[i] = laenge
        uebergaenge.append({"nach_segment": i, "laenge_m": laenge, "dk": kb - ka,
                            "k_vor": ka, "k_nach": kb})
    stuecke = []
    for i, (laenge, k0, k1) in enumerate(form):
        vorne = L[i - 1] / 2.0 if n > 1 else 0.0
        hinten = L[i] / 2.0
        if vorne > 0:    # zweite Hälfte des Übergangs vom vorigen Segment
            kv = form[i - 1][2]
            stuecke.append({"laenge": vorne, "k0": (kv + k0) / 2.0, "k1": k0, "quelle": i,
                            "von": 0.0, "bis": vorne / laenge})
        stuecke.append({"laenge": laenge - vorne - hinten, "k0": k0, "k1": k1, "quelle": i,
                        "von": vorne / laenge, "bis": 1.0 - hinten / laenge})
        if hinten > 0:   # erste Hälfte des Übergangs zum nächsten Segment
            kn = form[(i + 1) % n][1]
            stuecke.append({"laenge": hinten, "k0": k1, "k1": (k1 + kn) / 2.0, "quelle": i,
                            "von": 1.0 - hinten / laenge, "bis": 1.0})
    warnungen = []
    if gekuerzt:
        warnungen.append("Clothoid transition shortened to max_anteil after segment %s"
                         % ", ".join(map(str, gekuerzt)))
    if weggelassen:
        warnungen.append("Clothoid transition too short (< %.0f m), curvature jump remains after segment %s"
                         % (UEBERGANG_MIN_M, ", ".join(map(str, weggelassen))))
    return stuecke, uebergaenge, warnungen


def segmente_zu_punkten(start, segmente, schritt=1.0, schliessen="verteilen", max_fehler=50.0,
                        uebergaenge=None, info=None):
    """Integriert eine Segmentfolge zu dichten Punkten (Schritt <= 1 m).

    Rückgabe: punkte (N, 3), attribute, warnungen. Der Endpunkt wird nicht
    wiederholt; der Schließfehler wird linear über die Bogenlänge verteilt.

    uebergaenge: Regeln für automatische Klothoiden-Übergänge (mittellinie.uebergaenge);
    nur mit "aktiv": true wirksam, sonst bitgleich zur früheren Ausgabe.
    info: optionales dict, erhält "segment_anfaenge" (Punktindex je Segment) und
    "uebergaenge" (Liste der eingefügten Übergänge).
    """
    if not segmente:
        raise ValueError("No segments given")
    _uebergaenge_pruefen(uebergaenge)
    x = float(start.get("x", 0.0))
    z = float(start.get("z", 0.0))
    y = float(start.get("hoehe", start.get("y", 0.0)))
    phi0 = math.radians(float(start.get("richtung_grad", 0.0)))
    warnungen = []
    eingefuegt = []
    if uebergaenge and uebergaenge.get("aktiv"):
        stuecke, eingefuegt, w = segmente_mit_uebergaengen(segmente, uebergaenge)
        warnungen += w
        # Höhen an den Segmentgrenzen wie ohne Übergänge; Stücke interpolieren linear darin.
        hoehen = [y]
        for seg in segmente:
            if seg.get("hoehe_ende") is not None:
                hoehen.append(float(seg["hoehe_ende"]))
            elif seg.get("steigung_prozent") is not None:
                laenge = _segment_kruemmung(seg)[0]
                hoehen.append(hoehen[-1] + laenge * float(seg["steigung_prozent"]) / 100.0)
            else:
                hoehen.append(hoehen[-1])
        for st in stuecke:
            y0, y1 = hoehen[st["quelle"]], hoehen[st["quelle"] + 1]
            st["hoehe"] = (y0 + (y1 - y0) * st["von"], y0 + (y1 - y0) * st["bis"])
        # Übergang über den Start: Die Integration beginnt in der Übergangsmitte. Richtung und
        # Lage so setzen, dass das erste Segment hinter dem Übergang genau auf der Eingabe liegt
        # (Mitte: Richtung +Δk·L/8, seitlich Δk·L²/48 gegenüber dem Endzustand).
        if eingefuegt and eingefuegt[-1]["nach_segment"] == len(segmente) - 1:
            dk, lu = eingefuegt[-1]["dk"], eingefuegt[-1]["laenge_m"]
            versatz = -dk * lu * lu / 48.0          # entlang der linken Normalen (-sin φ, -cos φ)
            x -= versatz * math.sin(phi0)
            z -= versatz * math.cos(phi0)
            phi0 += dk * lu / 8.0
    else:
        stuecke = []
        for i, seg in enumerate(segmente):
            laenge, k0, k1 = _segment_kruemmung(seg)
            stuecke.append({"laenge": laenge, "k0": k0, "k1": k1, "quelle": i, "hoehe": None})
    phi = phi0
    pts = [(x, y, z)]
    s_werte = [0.0]
    attr_l = [(np.nan, np.nan, np.nan)]
    s = 0.0
    anfaenge = []
    for st in stuecke:
        seg = segmente[st["quelle"]]
        if len(anfaenge) <= st["quelle"]:
            anfaenge.append(len(pts) - 1)
        laenge, k0, k1 = st["laenge"], st["k0"], st["k1"]
        if laenge <= 0:
            raise ValueError("Segment %r has length <= 0" % seg)
        if st["hoehe"] is not None:
            y_anf, y_ende = st["hoehe"]
        else:
            if seg.get("hoehe_ende") is not None:
                y_ende = float(seg["hoehe_ende"])
            elif seg.get("steigung_prozent") is not None:
                y_ende = y + laenge * float(seg["steigung_prozent"]) / 100.0
            else:
                y_ende = y
            y_anf = y
        attr = _breiten_aus_angabe(seg)
        n = max(1, int(math.ceil(laenge / schritt)))
        h = laenge / n
        for j in range(n):
            k = k0 + (k1 - k0) * (j + 0.5) / n
            dphi = k * h
            # Sehne eines Kreisbogens mit konstanter Krümmung (für Bögen exakt)
            sehne = h if abs(dphi) < 1e-12 else 2.0 * math.sin(dphi / 2.0) / k
            mitte = phi + dphi / 2.0
            x += sehne * math.cos(mitte)
            z -= sehne * math.sin(mitte)   # 90° zeigt nach -z
            phi += dphi
            s += h
            y = y_anf + (y_ende - y_anf) * (j + 1) / n
            pts.append((x, y, z))
            s_werte.append(s)
            attr_l.append(attr)
    pts = np.array(pts, dtype=float)
    s_werte = np.array(s_werte)
    attr_a = np.array(attr_l, dtype=float)
    fehler = pts[-1] - pts[0]
    lage_fehler = float(np.hypot(fehler[0], fehler[2]))
    drehung = math.degrees(phi - phi0)
    rest = (drehung + 180.0) % 360.0 - 180.0
    if abs(abs(drehung) - 360.0) > 5.0 and abs(rest) > 5.0:
        warnungen.append("Total turning %.1f° instead of ±360°: the track does not close in direction"
                         % drehung)
    elif abs(rest) > 1.0:
        warnungen.append("Direction error when closing: %.2f°" % rest)
    if lage_fehler > max_fehler and schliessen != "erzwingen":
        raise ValueError("Segments do not close: end point is %.1f m from the start (allowed %.1f m)" % (lage_fehler, max_fehler))
    if schliessen == "fehler" and lage_fehler > 0.01:
        raise ValueError("Segments do not close exactly (%.3f m)" % lage_fehler)
    if lage_fehler > 0.01:
        warnungen.append("Closing error %.2f m (x %.2f, z %.2f) was spread over %.0f m"
                         % (lage_fehler, fehler[0], fehler[2], s))
    # Lage- und Höhenfehler linear verteilen
    pts = pts - np.outer(s_werte / s, fehler)
    pts = pts[:-1]
    # Attribut k gehört zum Segment, das Punkt k erzeugt hat; Punkt 0 (= Endpunkt)
    # erhält das Attribut des letzten Segments.
    letztes = attr_a[-1].copy()
    attr_a = attr_a[:-1].copy()
    attr_a[0] = letztes
    attribute = {"breite_links": attr_a[:, 0], "breite_rechts": attr_a[:, 1],
                 "querneigung_grad": attr_a[:, 2]}
    if info is not None:
        info["segment_anfaenge"] = [min(i, len(pts) - 1) for i in anfaenge]
        info["uebergaenge"] = eingefuegt
        info["schliessfehler"] = fehler.tolist()   # vor der Verteilung (x, y, z)
    return pts, attribute, warnungen


def kruemmung_punkte(punkte):
    """Diskrete Krümmung (1/m, > 0 = links) einer geschlossenen Punktfolge (x, y, z) in der Draufsicht."""
    p = np.asarray(punkte, dtype=float)[:, [0, 2]] * np.array([1.0, -1.0])   # 90° zeigt nach -z
    a, b = np.roll(p, 1, axis=0), np.roll(p, -1, axis=0)
    u, v = p - a, b - p
    kreuz = u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]
    nenner = np.linalg.norm(u, axis=1) * np.linalg.norm(v, axis=1) * np.linalg.norm(b - a, axis=1)
    return 2.0 * kreuz / np.maximum(nenner, 1e-12)


# --------------------------------------------------------------------------- Kontrollpunkte
def kontrollpunkte_lesen(liste):
    """Kontrollpunkte: [x, z], [x, z, y] oder {"x", "z", "y", "breite"…, "ecke_radius_m"}.

    ``ecke_radius_m``: Eckpunkt; die Strecke läuft gerade auf ihn zu und biegt mit diesem Radius ab
    (``mittellinie.ecken_verdichten``)."""
    pts = []
    attr = _leere_attribute(len(liste))
    for i, p in enumerate(liste):
        if isinstance(p, dict):
            x, z = float(p["x"]), float(p["z"])
            y = float(p.get("y", p.get("hoehe", 0.0)))
            bl, br, qn = _breiten_aus_angabe(p)
            attr["breite_links"][i], attr["breite_rechts"][i] = bl, br
            attr["querneigung_grad"][i] = qn
            if p.get("ecke_radius_m") is not None:
                r = float(p["ecke_radius_m"])
                if not math.isfinite(r) or r <= 0:
                    raise ValueError("Control point %d: ecke_radius_m must be > 0" % i)
                attr.setdefault("ecke_radius_m", np.full(len(liste), np.nan))[i] = r
        else:
            if len(p) not in (2, 3):
                raise ValueError("Control point %d: [x, z] or [x, z, y] expected" % i)
            x, z = float(p[0]), float(p[1])
            y = float(p[2]) if len(p) == 3 else 0.0
        pts.append((x, y, z))
    pts = np.array(pts, dtype=float)
    if len(pts) > 2 and np.linalg.norm(pts[-1] - pts[0]) < 1e-3:
        pts = pts[:-1]
        attr = {k: v[:-1] for k, v in attr.items()}
    return pts, attr


# --------------------------------------------------------------------------- CSV
CSV_SPALTEN = ["x", "y", "z", "breite_links", "breite_rechts", "querneigung_grad"]


def csv_lesen(pfad, achsen="y_oben_rechtshaendig"):
    """Liest eine Mittellinie als CSV: x,y,z[,breite_links,breite_rechts[,querneigung_grad]].

    - y ist die Höhe. Trennzeichen Komma, Semikolon, Tabulator oder Leerzeichen.
    - Kopfzeile optional (dann werden die Spalten über die Namen zugeordnet).
    - Zeilen mit '#' am Anfang sind Kommentare.
    - ``achsen`` gibt das Achsensystem der Datei an; es wird ins interne System umgerechnet.
    """
    with open(pfad, "r", encoding="utf-8-sig") as f:
        zeilen = [z.strip() for z in f if z.strip() and not z.lstrip().startswith("#")]
    if not zeilen:
        raise ValueError("CSV %s is empty" % pfad)

    def teilen(z):
        for t in (";", ",", "\t"):
            if t in z:
                return [w.strip() for w in z.split(t)]
        return z.split()

    erste = teilen(zeilen[0])
    try:
        [float(w) for w in erste]
        namen = CSV_SPALTEN[:len(erste)]
        daten_zeilen = zeilen
    except ValueError:
        namen = [w.lower() for w in erste]
        daten_zeilen = zeilen[1:]
    werte = np.array([[float(w) for w in teilen(z)] for z in daten_zeilen], dtype=float)
    if werte.ndim != 2 or werte.shape[1] < 3:
        raise ValueError("CSV %s: at least 3 columns (x, y, z) expected" % pfad)
    spalte = {n: i for i, n in enumerate(namen)}
    for n in ("x", "y", "z"):
        if n not in spalte:
            raise ValueError("CSV %s: column '%s' is missing (header: %s)" % (pfad, n, namen))
    roh = werte[:, [spalte["x"], spalte["y"], spalte["z"]]]
    m = achsen_matrix(achsen)
    pts = roh @ m        # Ausgabe = M @ intern  ->  intern = M^T @ Ausgabe (M orthogonal)
    attr = _leere_attribute(len(pts))
    for n in ("breite_links", "breite_rechts", "querneigung_grad"):
        if n in spalte:
            attr[n] = werte[:, spalte[n]].copy()
    # Breiten und Querneigung beziehen sich auf die Fahrtrichtung und bleiben unverändert.
    if len(pts) > 2 and np.linalg.norm(pts[-1] - pts[0]) < 1e-3:
        pts = pts[:-1]
        attr = {k: v[:-1] for k, v in attr.items()}
    return pts, attr


def csv_schreiben(pfad, x, y, z, breite_links=None, breite_rechts=None, achsen="y_oben_rechtshaendig",
                  querneigung_grad=None):
    """Schreibt eine Mittellinie als CSV (Gegenstück zu ``csv_lesen``)."""
    m = achsen_matrix(achsen)
    p = np.column_stack([x, y, z]) @ m.T
    kopf = ["x", "y", "z"]
    spalten = [p[:, 0], p[:, 1], p[:, 2]]
    if breite_links is not None:
        kopf += ["breite_links", "breite_rechts"]
        spalten += [np.asarray(breite_links), np.asarray(breite_rechts)]
    if querneigung_grad is not None:
        kopf += ["querneigung_grad"]
        spalten += [np.asarray(querneigung_grad)]
    arr = np.column_stack(spalten)
    with open(pfad, "w", encoding="utf-8", newline="\n") as f:
        f.write("# Mittellinie, Achsen: %s, Einheit m, y = Höhe\n" % achsen)
        f.write(",".join(kopf) + "\n")
        for zeile in arr:
            f.write(",".join("%.4f" % v for v in zeile) + "\n")
