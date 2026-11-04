"""Vorlagen-Import (Schritt F): fremde Streckenformen als Rohdefinition für den Generator/Mapeditor.

Drei Quellen, jeweils ein Wörterbuch im Format der Streckendefinition (``definition.aus_dict``):

- ``tum_csv(pfad)``      TUM-Rennstrecken-Datenbank (``x_m,y_m,w_tr_right_m,w_tr_left_m``, rund 5 m Abstand)
- ``gpx(pfad, ...)``     GPS-Spur (``trkpt``/``rtept``), auf gleichmäßigen Abstand neu abgetastet
- ``spielstrecke(erp, code)``  Mittellinie + Breiten einer Spielstrecke (nur lesend)

Gemeinsam: Punkte werden als ``kontrollpunkte`` mit ``approximation_m = 6`` (Schritt A: glättende
Approximation, senkt die Krümmungsänderung auf Originalniveau) ausgegeben, die Strecke wird auf die
Mitte ihrer Ausdehnung zentriert (Ziel: Streckenbereich ``WELT_BEREICH`` = AO-Abdeckung ±1024 m im Loch des
Außenterrains; passt sie nicht, erst drehen, dann auf Wunsch verkleinern) und die Verschiebung steht in
``editor_import`` (reine Metadaten, der Generator ignoriert sie; ``versatz_m`` ist der passende Spielweltversatz).

Achsen: Draufsicht x nach rechts, z nach unten, y = Höhe. TUM-Daten haben y nach oben:
``x = x_TUM``, ``z = -y_TUM`` (Form und Fahrtrichtung bleiben, links/rechts der Breiten bleiben links/rechts,
**kein Tausch** – der Tausch gehört nur zu ``mittellinie.umkehren``).

Abhängigkeiten: nur NumPy und Standardbibliothek (Spielstrecke zusätzlich ``ego`` und ``strecke.spielgenerator``,
importiert erst beim Aufruf). Der Spielordner wird nur gelesen.
"""

from __future__ import annotations

import csv
import math
import os
import tempfile
import xml.etree.ElementTree as ET
from typing import List, Optional, Sequence, Tuple

import numpy as np

#: Glättende Approximation der Mittellinie (Sigma in m) für alle Importe.
APPROXIMATION_M = 6.0
#: AO-Abdeckung des Trägers (X/Z ±1024 m um den Ursprung).
AO_HALBBREITE_M = 1024.0
#: Abstand zum AO-Rand, den ``einpassen`` mindestens lässt (m).
RAND_RESERVE_M = 10.0
#: Streckenbereich der Spielwelt (Weltkoordinaten): AO-Abdeckung ±1024 m geschnitten mit dem Loch des Außenterrains
#: (``pruefregeln.LOCH_AUSSENTERRAIN``: x −920…1128, z −1008…1040). Passt eine Strecke nur mit Versatz hinein, setzt der
#: Import ``editor_import.versatz_m`` (4.11.2026, Baku gedreht).
WELT_BEREICH = ((-920.13, 1024.0), (-1008.23, 1024.0))
#: Fahrbahnbreite (Gesamt), wenn die Quelle keine Breiten liefert (GPX); FIA Grade 1: mindestens 12 m.
STANDARD_BREITE_M = 14.0
#: Größte Lücke zwischen Spurende und -anfang, die ein GPX-Rundkurs haben darf (m).
GPX_MAX_LUECKE_M = 200.0
#: Ab dieser Lücke (m) gibt es eine Warnung in ``editor_import``.
GPX_WARN_LUECKE_M = 30.0
_ERDRADIUS_M = 6378137.0


class ImportFehler(ValueError):
    """Quelle nicht lesbar oder nicht als Rundkurs brauchbar (Text ist für Nutzer gedacht)."""


# =============================================================================== gemeinsame Hilfen
def _geschlossen_bereinigen(xz, *spalten):
    """Entfernt Doppelpunkte hintereinander und einen Schlusspunkt, der (fast) auf dem Anfang liegt."""
    xz = np.asarray(xz, float)
    spalten = [np.asarray(s, float) for s in spalten]
    d = np.linalg.norm(np.diff(xz, axis=0), axis=1)
    keep = np.r_[True, d > 1e-3]
    xz, spalten = xz[keep], [s[keep] for s in spalten]
    if len(xz) > 3:
        mittel = float(np.median(np.linalg.norm(np.diff(xz, axis=0), axis=1)))
        if np.linalg.norm(xz[-1] - xz[0]) < max(1e-3, 0.5 * mittel):
            xz, spalten = xz[:-1], [s[:-1] for s in spalten]
    return (xz, *spalten)


def _laenge_geschlossen(xz) -> float:
    return float(np.linalg.norm(np.roll(xz, -1, axis=0) - xz, axis=1).sum())


def _drehen(xz, grad):
    w = math.radians(grad)
    c, s = math.cos(w), math.sin(w)
    return np.column_stack([xz[:, 0] * c - xz[:, 1] * s, xz[:, 0] * s + xz[:, 1] * c])


def _bereich():
    """Untere und obere Ecke des Streckenbereichs (x, z)."""
    return np.array([WELT_BEREICH[0][0], WELT_BEREICH[1][0]]), np.array([WELT_BEREICH[0][1], WELT_BEREICH[1][1]])


def _fuellung(xz) -> float:
    """Größtes Verhältnis Ausdehnung / Bereichsgröße je Achse (1 = füllt den Streckenbereich genau)."""
    a, b = _bereich()
    return float(np.max(np.ptp(xz, axis=0) / (b - a)))


def _beste_drehung(xz) -> Tuple[float, float]:
    """Drehwinkel (Grad, 0…180), bei dem die Strecke den Streckenbereich am wenigsten ausfüllt; (Winkel, Füllgrad)."""
    beste = (0.0, _fuellung(xz))
    for grad in np.arange(0.5, 180.0, 0.5):
        f = _fuellung(_drehen(xz, grad))
        if f < beste[1] - 1e-9:
            beste = (float(grad), f)
    return beste


def _lage(xz, breit) -> Tuple[bool, np.ndarray]:
    """(passt, Versatz x/z): Passt die zentrierte Strecke samt Fahrbahnrand in den Streckenbereich? Versatz 0, wenn sie
    schon so passt, sonst auf die Bereichsmitte (ganze Meter)."""
    a, b = _bereich()
    lo, hi = xz.min(axis=0) - breit, xz.max(axis=0) + breit
    if np.all(lo >= a) and np.all(hi <= b):
        return True, np.zeros(2)
    v = np.round((a + b) / 2.0 - (lo + hi) / 2.0)
    return bool(np.all(lo + v >= a - 0.5) and np.all(hi + v <= b + 0.5)), v


def import_transform(meta):
    """Abbildung Quellkoordinaten (N×2 Draufsicht) → importierte Strecke aus ``editor_import`` (Zentrieren, Drehen,
    Zentrieren, Maßstab) und der Drehwinkel in Grad: ``f(xz) -> (xz_neu, drehung_grad)``."""
    mitte = np.array([meta["verschiebung_m"][0], meta["verschiebung_m"][2]], float)
    grad = float(meta.get("drehung_grad", 0.0))
    nach = np.array(meta.get("verschiebung_nach_drehung_m", [0.0, 0.0]), float)
    massstab = float(meta.get("massstab", 1.0))

    def f(xz):
        q = _drehen(np.asarray(xz, float) - mitte, grad) - nach
        return q * massstab, grad
    return f


def _definition(name: str, beschreibung: str, xz, hoehe, bl, br, quelle: dict, breite=None,
                hoehen_glaettung_m=None, fahrbahn=None, einpassen=False, rand_m=0.0) -> dict:
    """Rohdefinition aus Punkten (N×2 Draufsicht), Höhen und Halbbreiten; zentriert auf die Bounding-Box.

    ``rand_m``: so weit ragt das Generatornetz (Auslauf, Barrieren, Boxengasse) über die Fahrbahn hinaus; zählt beim
    Einpassen in den Streckenbereich mit (``_mit_netzrand``)."""
    xz = np.asarray(xz, float)
    if len(xz) < 8:
        raise ImportFehler("Too few points (%d) for a track." % len(xz))
    if not np.all(np.isfinite(xz)):
        raise ImportFehler("The source contains invalid coordinates.")
    lo, hi = xz.min(axis=0), xz.max(axis=0)
    mitte = (lo + hi) / 2.0
    xz = xz - mitte
    ausdehnung = hi - lo
    breit = 0.0
    if bl is not None:
        breit = float(max(np.max(bl), np.max(br)))
    elif breite is not None:
        breit = float(breite) / 2.0
    breit += float(rand_m)
    warnungen = []
    massstab = 1.0
    a_ber, b_ber = _bereich()
    bx, bz = (float(v) for v in (b_ber - a_ber))
    passt, versatz = _lage(xz, breit)
    # Passt die Strecke nicht, zuerst drehen (Form und Größe bleiben), erst danach verkleinern (4.11.2026)
    grad, nach = 0.0, np.zeros(2)
    if not passt:
        # Drehen nur, wenn es allein reicht oder (Einpassen) weniger verkleinert werden muss
        grad, fuellung_neu = _beste_drehung(xz)
        gedreht = _drehen(xz, grad)
        gedreht = gedreht - (gedreht.min(axis=0) + gedreht.max(axis=0)) / 2.0
        passt_gedreht, versatz_gedreht = _lage(gedreht, breit)
        if grad and fuellung_neu < _fuellung(xz) - 1e-3 and (passt_gedreht or einpassen):
            nach = (_drehen(xz, grad).min(axis=0) + _drehen(xz, grad).max(axis=0)) / 2.0
            xz, passt, versatz = gedreht, passt_gedreht, versatz_gedreht
            if passt:
                warnungen.append("Track rotated by %.1f° so it fits into the track area of the game world (%.0f × %.0f "
                                 "m)." % (grad, bx, bz))
            else:
                warnungen.append("Track rotated by %.1f° so it needs to be scaled down as little as possible." % grad)
        else:
            grad = 0.0
    if not passt and einpassen:
        halb_ber = (b_ber - a_ber) / 2.0
        massstab = float(np.min((halb_ber - breit - RAND_RESERVE_M) / np.maximum(np.max(np.abs(xz), axis=0), 1e-6)))
        xz = xz * massstab
        passt, versatz = _lage(xz, breit)
        warnungen.append("Track scaled down to %.1f %% (extent %.0f × %.0f m did not fit into the track area of the "
                         "game world, %.0f × %.0f m); widths and heights unchanged."
                         % (massstab * 100, ausdehnung[0], ausdehnung[1], bx, bz))
    elif not passt:
        warnungen.append(
            "Extent %.0f × %.0f m (plus track edge) does not fit into the track area of the game world (%.0f × %.0f "
            "m); make the track smaller (import with fit) or move points in the editor."
            % (ausdehnung[0], ausdehnung[1], bx, bz))
    if passt and np.any(versatz):
        warnungen.append("Game world offset set to [%.0f, 0, %.0f] so the track lies in the hole of the outer terrain." % (versatz[0], versatz[1]))
    punkte = []
    for i in range(len(xz)):
        p = {"x": round(float(xz[i, 0]), 3), "z": round(float(xz[i, 1]), 3), "y": round(float(hoehe[i]), 3)}
        if bl is not None:
            p["breite_links"] = round(float(bl[i]), 3)
            p["breite_rechts"] = round(float(br[i]), 3)
        punkte.append(p)
    ml = {"art": "kontrollpunkte", "punkte": punkte, "approximation_m": APPROXIMATION_M}
    if hoehen_glaettung_m is not None:
        ml["hoehen_glaettung_m"] = hoehen_glaettung_m
    d = {"name": name, "beschreibung": beschreibung, "mittellinie": ml}
    if fahrbahn:
        d["fahrbahn"] = dict(fahrbahn)
    if breite is not None and bl is None:
        d.setdefault("fahrbahn", {})["breite"] = float(breite)
    meta = dict(quelle)
    meta.update({"schema": "ego-import/1", "verschiebung_m": [round(float(mitte[0]), 3), 0.0, round(float(mitte[1]), 3)],
                 "punkte": len(punkte), "laenge_m": round(_laenge_geschlossen(xz), 1),
                 "ausdehnung_m": [round(float(ausdehnung[0]), 1), round(float(ausdehnung[1]), 1)],
                 "approximation_m": APPROXIMATION_M, "massstab": round(massstab, 5),
                 "drehung_grad": grad, "verschiebung_nach_drehung_m": [round(float(nach[0]), 3), round(float(nach[1]), 3)],
                 "passt_in_ao": bool(passt), "versatz_m": [float(versatz[0]), 0.0, float(versatz[1])] if passt
                 else [0.0, 0.0, 0.0], "rand_netz_m": round(float(rand_m), 1), "warnungen": warnungen})
    d["editor_import"] = meta
    return d


def _netz_ueberstand(d) -> float:
    """0, wenn das Generatornetz (ohne Ideallinie) mit dem vorgeschlagenen Versatz im Streckenbereich liegt; sonst wie weit
    es über Mittellinie plus Fahrbahnbreite hinausragt (m, größter Wert über alle Seiten)."""
    netz = _netz_geometrie(d)
    if netz is None:
        return 0.0
    meta = d["editor_import"]
    v = np.array([meta["versatz_m"][0], meta["versatz_m"][2]], float)
    V = np.asarray(netz["vertices"], float)[:, [0, 2]]
    a, b = _bereich()
    if np.all(V.min(axis=0) + v >= a) and np.all(V.max(axis=0) + v <= b):
        return 0.0
    punkte = d["mittellinie"]["punkte"]
    p = np.array([[q["x"], q["z"]] for q in punkte], float)
    if "breite_links" in punkte[0]:
        breit = max(max(q["breite_links"], q["breite_rechts"]) for q in punkte)
    else:
        breit = float((d.get("fahrbahn") or {}).get("breite", STANDARD_BREITE_M)) / 2.0
    return float(max(np.max(p.min(axis=0) - breit - V.min(axis=0)), np.max(V.max(axis=0) - p.max(axis=0) - breit), 0.0))


def _mit_netzrand(bauen):
    """``bauen(rand_m) -> Rohdefinition``: erst ohne Rand; ragt das Generatornetz (Auslauf, Barrieren, Boxengasse) dann
    aus dem Streckenbereich, ein zweites Mal mit diesem Überstand (Drehen, Versatz und Verkleinern rechnen ihn mit;
    Imola, Spa 4.11.2026)."""
    d = bauen(0.0)
    ueber = _netz_ueberstand(d)
    if ueber > 0.5:
        d = bauen(ueber + 2.0)
    return d


def _ausduennen(xz, abstand_m, *spalten):
    """Behält höchstens alle ``abstand_m`` einen Punkt (Bogenlänge); Anfang bleibt."""
    if not abstand_m or abstand_m <= 0:
        return (xz, *spalten)
    xz = np.asarray(xz, float)
    s = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(xz, axis=0), axis=1))]
    n = max(8, int(round((s[-1] + np.linalg.norm(xz[0] - xz[-1])) / abstand_m)))
    ziel = np.linspace(0, s[-1] + 1e-9, n, endpoint=False)
    idx = np.unique(np.searchsorted(s, ziel, side="left").clip(0, len(xz) - 1))
    return (xz[idx], *[np.asarray(c)[idx] for c in spalten])


# =============================================================================== TUM
def tum_csv(pfad, punktabstand_m: Optional[float] = None, einpassen: bool = False) -> dict:
    """TUM-Rennstrecke (``x_m,y_m,w_tr_right_m,w_tr_left_m``) → Rohdefinition.

    Spalten stehen rechts vor links (Kopfzeile ``# x_m,y_m,w_tr_right_m,w_tr_left_m``; ohne Kopfzeile gilt
    dieselbe Reihenfolge). ``punktabstand_m`` dünnt aus (Standard: alle Punkte, rund 5 m). ``einpassen``: Strecke verkleinern, bis sie in X/Z ±1024 m passt."""
    namen = ["x_m", "y_m", "w_tr_right_m", "w_tr_left_m"]
    zeilen = []
    with open(pfad, "r", encoding="utf-8-sig", newline="") as f:
        for roh in f:
            z = roh.strip()
            if not z:
                continue
            if z.startswith("#"):
                kopf = [t.strip() for t in z.lstrip("# ").split(",")]
                if "x_m" in kopf and "y_m" in kopf:
                    namen = kopf
                continue
            zeilen.append(z)
    try:
        werte = np.array([[float(t) for t in next(csv.reader([z]))] for z in zeilen], float)
    except ValueError as e:
        raise ImportFehler("TUM CSV cannot be read: %s" % e)
    if werte.ndim != 2 or werte.shape[1] < 4 or len(werte) < 8:
        raise ImportFehler("TUM CSV needs the columns x_m, y_m, w_tr_right_m, w_tr_left_m and at least 8 rows.")
    spalte = {n: i for i, n in enumerate(namen) if i < werte.shape[1]}
    try:
        x, y = werte[:, spalte["x_m"]], werte[:, spalte["y_m"]]
        rechts, links = werte[:, spalte["w_tr_right_m"]], werte[:, spalte["w_tr_left_m"]]
    except KeyError as e:
        raise ImportFehler("TUM CSV: column %s is missing." % e)
    xz = np.column_stack([x, -y])               # x = x_TUM, z = -y_TUM
    xz, links, rechts = _geschlossen_bereinigen(xz, links, rechts)
    xz, links, rechts = _ausduennen(xz, punktabstand_m, links, rechts)
    name = os.path.splitext(os.path.basename(str(pfad)))[0]
    return _mit_netzrand(lambda rand_m: _definition(
        "Vorlage %s (TUM)" % name,
        "Import aus der TUM-Rennstrecken-Datenbank (%s): Mittellinie und Breiten, ohne Höhen. "
        "Quelle: TUMFTM/racetrack-database, LGPL-3.0." % os.path.basename(str(pfad)),
        xz, np.zeros(len(xz)), links, rechts, {"art": "tum", "datei": os.path.basename(str(pfad))},
        hoehen_glaettung_m=0, einpassen=einpassen, rand_m=rand_m))


# =============================================================================== GPX
def _lokal_m(lat, lon):
    """Breite/Länge in Grad → lokale Meter (Ost, Nord) um den Mittelpunkt (flache Erde, für < 20 km genau genug)."""
    lat0, lon0 = float(np.mean(lat)), float(np.mean(lon))
    ost = np.radians(lon - lon0) * math.cos(math.radians(lat0)) * _ERDRADIUS_M
    nord = np.radians(lat - lat0) * _ERDRADIUS_M
    return ost, nord


def _gpx_punkte(pfad) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    try:
        wurzel = ET.parse(pfad).getroot()
    except (ET.ParseError, OSError) as e:
        raise ImportFehler("GPX cannot be read: %s" % e)
    for tag in ("trkpt", "rtept"):
        pts = wurzel.findall(".//{*}" + tag)
        if len(pts) >= 8:
            break
    else:
        raise ImportFehler("GPX contains fewer than 8 track points (trkpt/rtept).")
    lat, lon, ele = [], [], []
    for p in pts:
        try:
            lat.append(float(p.get("lat")))
            lon.append(float(p.get("lon")))
        except (TypeError, ValueError):
            raise ImportFehler("GPX point without valid lat/lon.")
        e = p.find("{*}ele")
        try:
            ele.append(float(e.text) if e is not None and e.text else np.nan)
        except ValueError:
            ele.append(np.nan)
    return np.array(lat), np.array(lon), np.array(ele)


def gpx(pfad, ziel_abstand_m: float = 5.0, einpassen: bool = False) -> dict:
    """GPS-Spur eines Rundkurses → Rohdefinition (gleichmäßig auf ``ziel_abstand_m`` neu abgetastet).

    Höhen (``ele``) werden übernommen, fehlen sie, ist die Strecke flach. Breiten liefert GPX nicht:
    ``fahrbahn.breite`` = 14 m. Die Spur muss ungefähr geschlossen sein (Lücke ≤ 200 m)."""
    if not ziel_abstand_m or ziel_abstand_m < 1.0:
        raise ImportFehler("ziel_abstand_m must be at least 1 m.")
    lat, lon, ele = _gpx_punkte(pfad)
    ost, nord = _lokal_m(lat, lon)
    xz = np.column_stack([ost, -nord])           # Norden oben auf dem Bildschirm = -z
    # Doppelpunkte (Stillstand) entfernen
    d = np.linalg.norm(np.diff(xz, axis=0), axis=1)
    keep = np.r_[True, d > 0.5]
    xz, ele = xz[keep], ele[keep]
    if len(xz) < 8:
        raise ImportFehler("The trace barely moves (fewer than 8 different points).")
    luecke = float(np.linalg.norm(xz[-1] - xz[0]))
    if luecke > GPX_MAX_LUECKE_M:
        raise ImportFehler("The trace is not a closed circuit: start and end are %.0f m apart (allowed %.0f m)."
                           % (luecke, GPX_MAX_LUECKE_M))
    # Schlusspunkt = Anfangspunkt anhängen, dann entlang der Bogenlänge neu abtasten
    ring = np.vstack([xz, xz[:1]])
    hoehe = np.where(np.isfinite(ele), ele, np.nan)
    if np.all(np.isnan(hoehe)):
        hoehe = np.zeros(len(xz))
    else:
        idx = np.arange(len(hoehe))
        ok = np.isfinite(hoehe)
        hoehe = np.interp(idx, idx[ok], hoehe[ok])
    hoehe_ring = np.r_[hoehe, hoehe[0]]
    s = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(ring, axis=0), axis=1))]
    gesamt = float(s[-1])
    n = max(8, int(round(gesamt / ziel_abstand_m)))
    st = np.linspace(0.0, gesamt, n, endpoint=False)
    neu = np.column_stack([np.interp(st, s, ring[:, 0]), np.interp(st, s, ring[:, 1])])
    h = np.interp(st, s, hoehe_ring)
    name = os.path.splitext(os.path.basename(str(pfad)))[0]
    d = _mit_netzrand(lambda rand_m: _definition(
        "Vorlage %s (GPX)" % name,
        "Import einer GPS-Spur (%s), %.1f km, Abstand %.1f m neu abgetastet; Breite pauschal %.0f m."
        % (os.path.basename(str(pfad)), gesamt / 1000.0, ziel_abstand_m, STANDARD_BREITE_M),
        neu, h, None, None, {"art": "gpx", "datei": os.path.basename(str(pfad)), "luecke_m": round(luecke, 1),
                              "ziel_abstand_m": float(ziel_abstand_m)},
        breite=STANDARD_BREITE_M, einpassen=einpassen, rand_m=rand_m))
    if luecke > GPX_WARN_LUECKE_M:
        d["editor_import"]["warnungen"].append(
            "Anfang und Ende der Spur liegen %.0f m auseinander; die Runde wird dort geradlinig geschlossen." % luecke)
    return d


# =============================================================================== Spielstrecke
class _Vorlage:
    """Genau das, was ``spielgenerator.definition_aus_vorlage`` von einem ``Schreiber`` braucht
    (Streckenlogik + Fahrzeugplatz-Layer), ohne dessen Bremslinien-Prüfung (Spiel-``*_common.erp`` hat
    nicht in jedem Fall ``AIBrakeSettings``)."""

    def __init__(self, archiv, code):
        from ego import streckenlogik
        try:
            self.original = streckenlogik.lade(archiv, code)
        except KeyError as e:
            raise ImportFehler("Track logic of %r not in this ERP: %s" % (code, e))
        quelle = self.original.quellen.get("fahrzeugplaetze")
        res = archiv.find(quelle) if quelle else None
        if res is None:
            raise ImportFehler("Vehicle slot layer of %r is missing in this ERP." % code)
        self.roh = {"vehicles_layer": (res.fragments[0].read(),)}


def spielstrecke(erp_pfad, code: str, punktabstand_m: Optional[float] = None, einpassen: bool = False,
                 rennlogik: bool = True, seitenraum: bool = True, breiten_begrenzen: bool = True) -> dict:
    """Mittellinie (TrackSpace-Knoten, Höhen) und Breiten (KI-Streckengrenzen) einer Spielstrecke → Rohdefinition.

    ``erp_pfad``: ERP, die die Streckenlogik enthält (Spiel: ``…/tracks/<code>/wep/<code>_common.erp``;
    Mod-ERP) oder ein Mod-Paket ``.zip`` (die ERP wird dann in einen temporären Ordner entpackt – langsam).
    Alles nur lesend. Seit 4.11.2026 (``strecke/spielimport.py``): ``breiten_begrenzen`` hält die Halbbreite innen unter
    0,85 × Radius, ``rennlogik`` übernimmt Boxengasse, DRS-/Straight-Mode-Zonen und Startaufstellung, ``seitenraum``
    Randsteine, Grünstreifen, Auslauf und Barrierenabstand aus der Fahrphysik."""
    from ego import erp as egoerp
    from . import spielgenerator
    erp_pfad = os.fspath(erp_pfad)
    if not os.path.isfile(erp_pfad):
        raise ImportFehler("File not found: %s" % erp_pfad)
    if erp_pfad.lower().endswith(".zip"):
        from ego import paket
        with tempfile.TemporaryDirectory(prefix="strecke_import_") as tmp:
            with paket.ModPaket(erp_pfad) as p:
                erp_datei = p.erp_entpacken(os.path.join(tmp, "strecke.erp"))
            return spielstrecke(erp_datei, code, punktabstand_m, einpassen, rennlogik, seitenraum, breiten_begrenzen)
    from . import spielimport
    try:
        with egoerp.open_erp(erp_pfad) as a:
            vorlage = _Vorlage(a, code)
            roh = spielgenerator.definition_aus_vorlage(vorlage)
            st = vorlage.original
            punkte = roh["mittellinie"]["punkte"]
            f_knoten = np.asarray(st.hauptlinie.fortschritt_knoten(), float)
            wl0 = np.array([p["breite_links"] for p in punkte], float)
            wr0 = np.array([p["breite_rechts"] for p in punkte], float)

            def halbbreite(f):
                f = float(f) % 1.0
                return (float(np.interp(f, f_knoten, wl0, period=1.0)), float(np.interp(f, f_knoten, wr0, period=1.0)))
            xz = np.array([[p["x"], p["z"]] for p in punkte], float)
            hoehe = np.array([p["y"] for p in punkte], float)
            links, rechts = wl0.copy(), wr0.copy()
            xz, hoehe, links, rechts = _geschlossen_bereinigen(xz, hoehe, links, rechts)
            hinweise = []
            if breiten_begrenzen:
                links, rechts, geaendert = spielimport.breiten_begrenzen(xz, links, rechts)
                if geaendert:
                    hinweise.append("Track width limited at %d points in tight corners to %.0f %% of the radius "
                                    "(otherwise the edge folds)." % (geaendert, 100 * spielimport.RADIUS_ANTEIL))
            if code.replace("_reverse", "") in spielimport.BRUECKEN:
                hinweise.append("The track crosses itself on a bridge; the generator has no bridges, the check reports "
                                "the crossing as an error.")
            xz, hoehe, links, rechts = _ausduennen(xz, punktabstand_m, hoehe, links, rechts)
            teile_text = (", Boxengasse, Zonen, Startaufstellung" if rennlogik else "") + (
                " und Seitenbereichen aus der Fahrphysik" if seitenraum else "")

            def bauen(rand_m):
                d = _definition(
                    "Vorlage %s (Spiel)" % code,
                    "Import der Spielstrecke %s: Mittellinie mit Höhen und Breiten aus den KI-Streckengrenzen%s."
                    % (code, teile_text), xz, hoehe, links, rechts,
                    {"art": "spielstrecke", "datei": os.path.basename(erp_pfad), "code": code},
                    hoehen_glaettung_m=0, fahrbahn=roh.get("fahrbahn") or {"uebergang_m": 0}, einpassen=einpassen,
                    rand_m=rand_m)
                meta = d["editor_import"]
                meta["warnungen"].extend(hinweise)
                laenge = float(meta["laenge_m"])
                if rennlogik:
                    teile = spielimport.rennlogik(st, laenge, halbbreite, code)
                    meta["warnungen"].extend(teile.pop("hinweise", []))
                    for gruppe, werte in teile.items():
                        d.setdefault(gruppe, {}).update(werte)
                    meta["rennlogik"] = sorted(teile)
                if seitenraum:
                    abschnitte, info = spielimport.seitenraum(a, st, laenge, halbbreite)
                    if info.get("hinweis"):
                        meta["warnungen"].append(info.pop("hinweis"))
                    if abschnitte:
                        d["seitenraum"] = {"abschnitte": abschnitte}
                    meta["seitenraum"] = info
                return d
            d = _mit_netzrand(bauen)
    except ImportFehler:
        raise
    except (ValueError, KeyError, StopIteration, OSError) as e:
        raise ImportFehler("Game track %r cannot be read: %s" % (code, e))
    return d


def spielstrecke_kulisse(erp_pfad, code: str, d: dict, katalog_daten=None, ohne=None) -> Tuple[dict, dict]:
    """Objekte der Spielstrecke (nur Modelle aus dem Umgebungskatalog) passend zu ``d`` (Ergebnis von
    ``spielstrecke``: gleiche Zentrierung, Drehung und Verkleinerung) als ``umgebung.json``-Daten und Info."""
    from ego import erp as egoerp
    from . import spielimport, umgebung
    if katalog_daten is None:
        katalog_daten = umgebung.katalog()
    punkte = d["mittellinie"]["punkte"]
    xz = np.array([[p["x"], p["z"]] for p in punkte], float)
    halb = np.array([max(p.get("breite_links", 6.0), p.get("breite_rechts", 6.0)) for p in punkte], float)
    meta = d["editor_import"]
    with egoerp.open_erp(os.fspath(erp_pfad)) as a:
        return spielimport.kulisse(a, code, import_transform(meta), xz, halb, katalog_daten,
                                   ohne=spielimport.KULISSE_OHNE if ohne is None else ohne,
                                   versatz=meta.get("versatz_m", [0.0, 0.0, 0.0]), netz=_netz_geometrie(d))


def _netz_geometrie(d):
    """Generatornetz ohne Ideallinie und Logik (etwa 0,5 s) oder None, wenn die Definition nicht baubar ist."""
    from . import definition as dfn, geometrie, mittellinie, querprofil
    try:
        defn = dfn.aus_dict(d)
        ml = mittellinie.Mittellinie(defn)
        pk = defn.param["kurven"]
        kurven = mittellinie.kurven_finden(ml, pk["kruemmung_schwelle"], pk["mindestwinkel_grad"], pk["zusammenfassen_m"])
        return geometrie.netz_erzeugen(ml, querprofil.querprofil_berechnen(ml, defn.param, kurven), defn.param)
    except (ValueError, KeyError, TypeError, IndexError):
        return None
