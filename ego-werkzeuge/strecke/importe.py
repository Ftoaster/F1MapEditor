"""Vorlagen-Import (Schritt F): fremde Streckenformen als Rohdefinition für den Generator/Mapeditor.

Drei Quellen, jeweils ein Wörterbuch im Format der Streckendefinition (``definition.aus_dict``):

- ``tum_csv(pfad)``      TUM-Rennstrecken-Datenbank (``x_m,y_m,w_tr_right_m,w_tr_left_m``, rund 5 m Abstand)
- ``gpx(pfad, ...)``     GPS-Spur (``trkpt``/``rtept``), auf gleichmäßigen Abstand neu abgetastet
- ``spielstrecke(erp, code)``  Mittellinie + Breiten einer Spielstrecke (nur lesend)

Gemeinsam: Punkte werden als ``kontrollpunkte`` mit ``approximation_m = 6`` (Schritt A: glättende
Approximation, senkt die Krümmungsänderung auf Originalniveau) ausgegeben, die Strecke wird auf die
Mitte ihrer Ausdehnung zentriert (Ziel: X/Z innerhalb ±1024 m, AO-Abdeckung des Trägers) und die
Verschiebung steht in ``editor_import`` (reine Metadaten, der Generator ignoriert sie).

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


def _definition(name: str, beschreibung: str, xz, hoehe, bl, br, quelle: dict, breite=None,
                hoehen_glaettung_m=None, fahrbahn=None, einpassen=False) -> dict:
    """Rohdefinition aus Punkten (N×2 Draufsicht), Höhen und Halbbreiten; zentriert auf die Bounding-Box."""
    xz = np.asarray(xz, float)
    if len(xz) < 8:
        raise ImportFehler("Zu wenige Punkte (%d) für eine Strecke." % len(xz))
    if not np.all(np.isfinite(xz)):
        raise ImportFehler("Die Quelle enthält ungültige Koordinaten.")
    lo, hi = xz.min(axis=0), xz.max(axis=0)
    mitte = (lo + hi) / 2.0
    xz = xz - mitte
    ausdehnung = hi - lo
    halb = float(np.max(ausdehnung) / 2.0)
    breit = 0.0
    if bl is not None:
        breit = float(max(np.max(bl), np.max(br)))
    elif breite is not None:
        breit = float(breite) / 2.0
    warnungen = []
    massstab = 1.0
    passt = float(np.max(np.abs(xz))) + breit <= AO_HALBBREITE_M
    if not passt and einpassen:
        massstab = (AO_HALBBREITE_M - breit - RAND_RESERVE_M) / float(np.max(np.abs(xz)))
        xz = xz * massstab
        passt = True
        warnungen.append("Strecke auf %.1f %% verkleinert (Ausdehnung %.0f × %.0f m passte nicht in X/Z ±%d m); Breiten und "
                         "Höhen unverändert." % (massstab * 100, ausdehnung[0], ausdehnung[1], AO_HALBBREITE_M))
    elif not passt:
        warnungen.append(
            "Ausdehnung %.0f × %.0f m (plus Fahrbahnrand) passt nicht in X/Z ±%d m; Strecke verkleinern "
            "(Import mit Einpassen) oder Punkte im Editor verschieben." % (ausdehnung[0], ausdehnung[1], AO_HALBBREITE_M))
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
                 "passt_in_ao": bool(passt), "warnungen": warnungen})
    d["editor_import"] = meta
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
        raise ImportFehler("TUM-CSV nicht lesbar: %s" % e)
    if werte.ndim != 2 or werte.shape[1] < 4 or len(werte) < 8:
        raise ImportFehler("TUM-CSV braucht die Spalten x_m, y_m, w_tr_right_m, w_tr_left_m und mindestens 8 Zeilen.")
    spalte = {n: i for i, n in enumerate(namen) if i < werte.shape[1]}
    try:
        x, y = werte[:, spalte["x_m"]], werte[:, spalte["y_m"]]
        rechts, links = werte[:, spalte["w_tr_right_m"]], werte[:, spalte["w_tr_left_m"]]
    except KeyError as e:
        raise ImportFehler("TUM-CSV: Spalte %s fehlt." % e)
    xz = np.column_stack([x, -y])               # x = x_TUM, z = -y_TUM
    xz, links, rechts = _geschlossen_bereinigen(xz, links, rechts)
    xz, links, rechts = _ausduennen(xz, punktabstand_m, links, rechts)
    name = os.path.splitext(os.path.basename(str(pfad)))[0]
    return _definition(
        "Vorlage %s (TUM)" % name,
        "Import aus der TUM-Rennstrecken-Datenbank (%s): Mittellinie und Breiten, ohne Höhen. "
        "Quelle: TUMFTM/racetrack-database, LGPL-3.0." % os.path.basename(str(pfad)),
        xz, np.zeros(len(xz)), links, rechts, {"art": "tum", "datei": os.path.basename(str(pfad))},
        hoehen_glaettung_m=0, einpassen=einpassen)


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
        raise ImportFehler("GPX nicht lesbar: %s" % e)
    for tag in ("trkpt", "rtept"):
        pts = wurzel.findall(".//{*}" + tag)
        if len(pts) >= 8:
            break
    else:
        raise ImportFehler("GPX enthält weniger als 8 Spurpunkte (trkpt/rtept).")
    lat, lon, ele = [], [], []
    for p in pts:
        try:
            lat.append(float(p.get("lat")))
            lon.append(float(p.get("lon")))
        except (TypeError, ValueError):
            raise ImportFehler("GPX-Punkt ohne gültige lat/lon.")
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
        raise ImportFehler("ziel_abstand_m muss mindestens 1 m sein.")
    lat, lon, ele = _gpx_punkte(pfad)
    ost, nord = _lokal_m(lat, lon)
    xz = np.column_stack([ost, -nord])           # Norden oben auf dem Bildschirm = -z
    # Doppelpunkte (Stillstand) entfernen
    d = np.linalg.norm(np.diff(xz, axis=0), axis=1)
    keep = np.r_[True, d > 0.5]
    xz, ele = xz[keep], ele[keep]
    if len(xz) < 8:
        raise ImportFehler("Die Spur bewegt sich kaum (weniger als 8 verschiedene Punkte).")
    luecke = float(np.linalg.norm(xz[-1] - xz[0]))
    if luecke > GPX_MAX_LUECKE_M:
        raise ImportFehler("Die Spur ist kein Rundkurs: Anfang und Ende liegen %.0f m auseinander (erlaubt %.0f m)."
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
    d = _definition(
        "Vorlage %s (GPX)" % name,
        "Import einer GPS-Spur (%s), %.1f km, Abstand %.1f m neu abgetastet; Breite pauschal %.0f m."
        % (os.path.basename(str(pfad)), gesamt / 1000.0, ziel_abstand_m, STANDARD_BREITE_M),
        neu, h, None, None, {"art": "gpx", "datei": os.path.basename(str(pfad)), "luecke_m": round(luecke, 1),
                              "ziel_abstand_m": float(ziel_abstand_m)},
        breite=STANDARD_BREITE_M, einpassen=einpassen)
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
            raise ImportFehler("Streckenlogik von %r nicht in dieser ERP: %s" % (code, e))
        quelle = self.original.quellen.get("fahrzeugplaetze")
        res = archiv.find(quelle) if quelle else None
        if res is None:
            raise ImportFehler("Fahrzeugplatz-Layer von %r fehlt in dieser ERP." % code)
        self.roh = {"vehicles_layer": (res.fragments[0].read(),)}


def spielstrecke(erp_pfad, code: str, punktabstand_m: Optional[float] = None, einpassen: bool = False) -> dict:
    """Mittellinie (TrackSpace-Knoten, Höhen) und Breiten (KI-Streckengrenzen) einer Spielstrecke → Rohdefinition.

    ``erp_pfad``: ERP, die die Streckenlogik enthält (Spiel: ``…/tracks/<code>/wep/<code>_common.erp``;
    Mod-ERP) oder ein Mod-Paket ``.zip`` (die ERP wird dann in einen temporären Ordner entpackt – langsam).
    Alles nur lesend."""
    from ego import erp as egoerp
    from . import spielgenerator
    erp_pfad = os.fspath(erp_pfad)
    if not os.path.isfile(erp_pfad):
        raise ImportFehler("Datei nicht gefunden: %s" % erp_pfad)
    if erp_pfad.lower().endswith(".zip"):
        from ego import paket
        with tempfile.TemporaryDirectory(prefix="strecke_import_") as tmp:
            with paket.ModPaket(erp_pfad) as p:
                erp_datei = p.erp_entpacken(os.path.join(tmp, "strecke.erp"))
            return spielstrecke(erp_datei, code, punktabstand_m, einpassen)
    try:
        with egoerp.open_erp(erp_pfad) as a:
            roh = spielgenerator.definition_aus_vorlage(_Vorlage(a, code))
    except ImportFehler:
        raise
    except (ValueError, KeyError, StopIteration, OSError) as e:
        raise ImportFehler("Spielstrecke %r nicht lesbar: %s" % (code, e))
    punkte = roh["mittellinie"]["punkte"]
    xz = np.array([[p["x"], p["z"]] for p in punkte], float)
    hoehe = np.array([p["y"] for p in punkte], float)
    links = np.array([p["breite_links"] for p in punkte], float)
    rechts = np.array([p["breite_rechts"] for p in punkte], float)
    xz, hoehe, links, rechts = _geschlossen_bereinigen(xz, hoehe, links, rechts)
    xz, hoehe, links, rechts = _ausduennen(xz, punktabstand_m, hoehe, links, rechts)
    return _definition(
        "Vorlage %s (Spiel)" % code,
        "Import der Spielstrecke %s: Mittellinie mit Höhen und Breiten aus den KI-Streckengrenzen (nur Form, "
        "keine Boxengasse)." % code, xz, hoehe, links, rechts,
        {"art": "spielstrecke", "datei": os.path.basename(erp_pfad), "code": code},
        hoehen_glaettung_m=0, fahrbahn=roh.get("fahrbahn") or {"uebergang_m": 0}, einpassen=einpassen)
