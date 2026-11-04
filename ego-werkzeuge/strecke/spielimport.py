"""Spielstrecken laden (4.11.2026, ``docs/mapeditor/spielstrecken_laden.md``): Liste, robuste Breiten, Rennlogik,
Seitenbereiche aus der Fahrphysik und Kulisse aus den Objektebenen.

Ergänzt ``importe.spielstrecke`` (Mittellinie, Höhen, Breiten). Alles nur lesend; Stationen in Metern ab der
Start-Ziel-Linie (Fortschritt 0 der Spiel-Hauptlinie = erster Kontrollpunkt = Eingabeursprung), umgerechnet über den
Fortschritt auf die Länge der importierten Linie (``laenge_m``), damit kleine Längenunterschiede nicht wandern.

- ``strecken(spielordner)``: Spielstrecken mit Namen, Code und Datei (für die Auswahlliste im Editor).
- ``breiten_begrenzen``: Halbbreite innen höchstens ``RADIUS_ANTEIL`` × Kurvenradius (sonst faltet der Rand).
- ``rennlogik``: Boxengasse (Seite, Ein-/Ausfahrt, Abstand, Tempolimit), DRS- und Straight-Mode-Zonen, Startaufstellung.
- ``seitenraum``: Randstein, Grünstreifen, Auslauf (Gras/Kies/Asphalt) und Barrierenabstand je Seite aus ``TRN~``.
- ``kulisse``: Objekte der Strecke, deren Modell im Umgebungskatalog steht, als ``umgebung.json``-Objekte.
"""

from __future__ import annotations

import glob
import math
import os
import re
from collections import Counter
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

TRACKS_REL = os.path.join("2025_asset_groups", "environment_package", "tracks")

#: Anzeigenamen (Rennstrecken sind Eigennamen; deutsch, englisch)
NAMEN = {
    "abu_dhabi": ("Abu Dhabi – Yas Marina", "Abu Dhabi – Yas Marina"),
    "austria": ("Österreich – Spielberg", "Austria – Spielberg"),
    "bahrain": ("Bahrain – Sakhir", "Bahrain – Sakhir"),
    "baku": ("Aserbaidschan – Baku", "Azerbaijan – Baku"),
    "brazil": ("Brasilien – Interlagos", "Brazil – Interlagos"),
    "catalunya": ("Spanien – Barcelona", "Spain – Barcelona"),
    "hungaroring": ("Ungarn – Hungaroring", "Hungary – Hungaroring"),
    "imola": ("Emilia-Romagna – Imola", "Emilia-Romagna – Imola"),
    "jeddah": ("Saudi-Arabien – Dschidda", "Saudi Arabia – Jeddah"),
    "las_vegas": ("Las Vegas", "Las Vegas"),
    "losail": ("Katar – Lusail", "Qatar – Lusail"),
    "madrid": ("Spanien – Madrid", "Spain – Madrid"),
    "melbourne": ("Australien – Melbourne", "Australia – Melbourne"),
    "mexico": ("Mexiko – Mexiko-Stadt", "Mexico – Mexico City"),
    "miami": ("Miami", "Miami"),
    "monaco": ("Monaco", "Monaco"),
    "montreal": ("Kanada – Montreal", "Canada – Montreal"),
    "monza": ("Italien – Monza", "Italy – Monza"),
    "shanghai": ("China – Shanghai", "China – Shanghai"),
    "silverstone": ("Großbritannien – Silverstone", "Great Britain – Silverstone"),
    "singapore": ("Singapur – Marina Bay", "Singapore – Marina Bay"),
    "spa_francorchamps": ("Belgien – Spa-Francorchamps", "Belgium – Spa-Francorchamps"),
    "suzuka": ("Japan – Suzuka", "Japan – Suzuka"),
    "texas": ("USA – Austin", "USA – Austin"),
    "zandvoort": ("Niederlande – Zandvoort", "Netherlands – Zandvoort"),
}
#: Tempolimit der Boxengasse laut Spieldatenbank (``TrackPitData_0.pitSpeedLimit``, Datensatz f1, 3.11.2026); sonst 80
TEMPOLIMIT_60 = ("monaco", "silverstone", "singapore", "zandvoort")
#: Strecken mit Brücke (Kreuzung in der Draufsicht): kann der Generator nicht bauen
BRUECKEN = ("suzuka",)
#: Halbbreite innen höchstens dieser Anteil des Kurvenradius (Generator: Faltung ab 1,0)
RADIUS_ANTEIL = 0.85
#: Boxengasse: Abstand der Boxenlinie zur Fahrbahn, ab dem sie als getrennt gilt (m); Grenzen für ``abstand_fahrbahn``
BOX_TRENNUNG_M = 4.0
BOX_ABSTAND_GRENZEN = (6.0, 40.0)
#: Seitenbereiche: Abtastung (m) längs und quer, größte Suchweite neben dem Fahrbahnrand
SEITE_SCHRITT_M, SEITE_QUER_M, SEITE_WEITE_M = 5.0, 0.5, 70.0
SEITE_MIN_ABSCHNITT_M = 40.0
KERB_CODES = ("RMBL", "RMBO", "BUMP")
GRAS_CODES = ("GRS+",)
KIES_CODES = ("HDG+",)
ASPHALT_CODES = ("SDT+", "DDT+", "ASTR", "MKNG", "METL")
MAUER_CODES = ("SCON", "SMET", "TYRW", "DEFA", "SPLA")
#: Startaufstellung vor der Zeitlinie (Monza, Bahrain, Spa …): unsere Start-Ziel-Linie liegt so weit vor der Pole
ABSTAND_POLE_M = 6.0
#: Kulisse: keine Objekte näher an der Mittellinie als Halbbreite + dieser Abstand (die Fahrbahn ist neu erzeugt)
KULISSE_ABSTAND_M = 12.0
KULISSE_OHNE = ("flagge",)
#: Kulisse: Mindestabstand zum Rand des Streckenbereichs (m)
KULISSE_RAND_M = 5.0


class SpielimportFehler(ValueError):
    pass


# =============================================================================== Liste
def strecken(spielordner=None, sprache="de") -> List[dict]:
    """Spielstrecken im Spielordner: [{code, name, datei, rueckwaerts, bruecke}], sortiert nach Name."""
    from ego import spielpfade
    kandidaten = ([spielordner] if spielordner else []) + list(spielpfade.kandidaten())
    so = next((p for p in kandidaten if p and os.path.isdir(os.path.join(p, TRACKS_REL))), None)
    if so is None:
        raise SpielimportFehler("Game folder not found")
    out = []
    for datei in sorted(glob.glob(os.path.join(so, TRACKS_REL, "*", "wep", "*_common.erp"))):
        code = os.path.basename(datei)[:-len("_common.erp")]
        basis = code[:-len("_reverse")] if code.endswith("_reverse") else code
        namen = NAMEN.get(basis)
        name = (namen[0 if sprache == "de" else 1] if namen else basis.replace("_", " ").title())
        if basis != code:
            name += " (rückwärts)" if sprache == "de" else " (reverse)"
        out.append({"code": code, "name": name, "datei": datei, "rueckwaerts": basis != code,
                    "bruecke": basis in BRUECKEN})
    out.sort(key=lambda e: e["name"].lower())
    return out


# =============================================================================== Breiten
def _radien(xz: np.ndarray, reichweite_m: float = 8.0) -> Tuple[np.ndarray, np.ndarray]:
    """Kurvenradius und Richtung (+1 links, −1 rechts) je Punkt eines geschlossenen Linienzugs (Draufsicht x/z)."""
    n = len(xz)
    seg = np.linalg.norm(np.roll(xz, -1, axis=0) - xz, axis=1)
    schritt = max(1, int(round(reichweite_m / max(float(np.median(seg)), 1e-6))))
    a, b = np.roll(xz, schritt, axis=0), np.roll(xz, -schritt, axis=0)
    u, v = xz - a, b - xz
    kreuz = u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]
    sehne = np.linalg.norm(b - a, axis=1) * np.linalg.norm(u, axis=1) * np.linalg.norm(v, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(np.abs(kreuz) > 1e-12, sehne / (2 * np.abs(kreuz)), np.inf)
    # Draufsicht x rechts, z unten: (x, −z) rechtshändig → Kreuzprodukt in (x, z) hat umgekehrtes Vorzeichen
    return r, np.where(kreuz < 0, 1.0, -1.0)


def breiten_begrenzen(xz, links, rechts, anteil: float = RADIUS_ANTEIL):
    """Halbbreiten an der Kurveninnenseite auf ``anteil`` × Radius begrenzen (über ±3 Punkte geglättet).

    Rückgabe: (links, rechts, Zahl der geänderten Punkte). Die KI-Grenzen der Originale reichen an Haarnadeln über
    den Radius hinaus (Bahrain: 7,4 m), dort faltet sich der Rand im Generator."""
    xz = np.asarray(xz, float)
    links, rechts = np.array(links, float), np.array(rechts, float)
    r, richtung = _radien(xz)
    grenze = anteil * r
    grenze = np.minimum.reduce([np.roll(grenze, k) for k in range(-3, 4)])
    neu_l = np.where(richtung > 0, np.minimum(links, grenze), links)
    neu_r = np.where(richtung < 0, np.minimum(rechts, grenze), rechts)
    neu_l, neu_r = np.maximum(neu_l, 3.0), np.maximum(neu_r, 3.0)
    geaendert = int(np.sum((neu_l < links - 1e-6) | (neu_r < rechts - 1e-6)))
    return neu_l, neu_r, geaendert


# =============================================================================== Rennlogik
def _rechts(t):
    """Waagerechter Einheitsvektor nach rechts (x, z) zu einer Tangente (wie ``streckenlogik.rechts``)."""
    v = np.array([-t[2], t[0]], float)
    return v / max(np.linalg.norm(v), 1e-12)


def _seite(spline, punkt) -> Tuple[float, float]:
    """(Fortschritt, vorzeichenbehafteter Querabstand, positiv = rechts) eines Punkts zur Hauptlinie."""
    f, d = spline.fortschritt_von(punkt)
    c, t = spline.punkt(f)
    q = np.array([punkt[0] - c[0], punkt[2] - c[2]])
    return f, float(np.sign(q @ _rechts(t)) or 1.0) * d


def rennlogik(st, laenge_m: float, halbbreite=None, code: str = "") -> dict:
    """Boxengasse, DRS- und Straight-Mode-Zonen und Startaufstellung einer Spielstrecke als Definitionsteile.

    ``st``: ``ego.streckenlogik.Strecke``; ``laenge_m``: Länge der importierten Linie (für Fortschritt → Meter);
    ``halbbreite(f) -> (links, rechts)`` für die Boxengasse. Rückgabe: dict mit den Gruppen der Definition und
    ``hinweise`` (Liste)."""
    haupt = st.hauptlinie
    L = float(laenge_m)
    out, hinweise = {}, []
    # Start-Ziel: Die Zeitlinie der Spielstrecken liegt bei Fortschritt 0, die Startaufstellung oft davor in Fahrtrichtung
    # (Monza 308 m, Bahrain 246 m, Spa 120 m). Unser Generator kennt eine Linie mit der Pole dahinter: dann liegt sie
    # ABSTAND_POLE_M vor der Pole, alle Meter „ab Ziel“ zählen von dort.
    slots = sorted((p for p in st.plaetze if p["art"].lower() == "gridslot"), key=lambda p: p["nummer"])
    lage = [_seite(haupt, p["position"]) for p in slots]
    ziel_f = 0.0
    if lage and 0.0 < lage[0][0] < 0.5:
        ziel_f = (lage[0][0] + ABSTAND_POLE_M / L) % 1.0
        out["start_ziel"] = {"s": round(ziel_f * L, 1)}
        hinweise.append("Start/finish line placed at the starting grid (%.0f m after the game's timing line)."
                        % (ziel_f * L))
    meter = lambda f: round(((float(f) - ziel_f) % 1.0) * L, 1)
    # ---- DRS: Erkennung (drs_det) und Aktivierung (drs_act) je ID
    det = {z.eigenschaften.get("ID"): z for z in st.zonen if z.typ == "drs_det" and z.spline == haupt.name}
    act = {z.eigenschaften.get("ID"): z for z in st.zonen if z.typ == "drs_act" and z.spline == haupt.name}
    drs = []
    for kennung in sorted(act, key=lambda k: act[k].start):
        a = act[kennung]
        d = det.get(kennung)
        drs.append({"erkennung_m": meter(d.start if d else a.start - 150.0 / L), "aktivierung_m": meter(a.start),
                    "ende_m": meter(a.ende)})
    if drs:
        out["drs"] = {"zonen": drs}
    # ---- Straight Mode: Full-Zonen, Partial mit gleichem Ende → Versatz
    xm = [z for z in st.zonen if z.typ == "x_mode" and z.spline == haupt.name]
    full = [z for z in xm if (z.eigenschaften.get("type") or "Full") == "Full"]
    partial = [z for z in xm if z.eigenschaften.get("type") == "Partial"]
    xzonen = []
    for z in sorted(full, key=lambda z: z.start):
        p = next((q for q in partial if abs(q.ende - z.ende) < 1e-4), None)
        eintrag = {"von_m": meter(z.start), "bis_m": meter(z.ende)}
        if p is None:
            eintrag["partial"] = False
        else:
            eintrag["partial_versatz_m"] = round(((p.start - z.start) % 1.0) * L, 1)
        xzonen.append(eintrag)
    if xzonen:
        out["x_mode"] = {"zonen": xzonen}
    # ---- Startaufstellung: Pole-Seite und Abstände aus den GridSlot-Plätzen
    if len(slots) >= 4:
        hinter = [((ziel_f - f) % 1.0) * L for f, _q in lage]
        abstaende = np.diff(hinter)
        out["startaufstellung"] = {"pole_seite": "rechts" if lage[0][1] > 0 else "links",
                                   "abstand_pole_m": round(float(hinter[0]), 1),
                                   "abstand_m": round(float(np.median(abstaende[abstaende > 0])), 1),
                                   "anzahl": len(slots)}
    # ---- Boxengasse aus der Boxenlinie
    box = _boxengasse(st, L, halbbreite, code, ziel_f)
    if box.get("hinweis"):
        hinweise.append(box.pop("hinweis"))
    if box:
        out["boxengasse"] = box
    out["hinweise"] = hinweise
    return out


def _boxengasse(st, L, halbbreite, code, ziel_f=0.0) -> dict:
    haupt = st.hauptlinie
    boxen = [s for s in st.splines.values() if not s.geschlossen]
    if not boxen:
        return {"hinweis": "No pit line in the game track."}
    pit = boxen[0]
    punkte = pit.abschnitt(0.0, 1.0, 5.0)
    werte = []
    for p in punkte:
        f, q = _seite(haupt, p)
        wl, wr = halbbreite(f) if halbbreite else (6.0, 6.0)
        w = wr if q > 0 else wl
        werte.append((f, q, abs(q) - w))
    getrennt = [i for i, (_f, _q, frei) in enumerate(werte) if frei > BOX_TRENNUNG_M]
    if len(getrennt) < 4:
        return {"hinweis": "Pit line does not run beside the track; pit lane stays automatic as before."}
    i0, i1 = getrennt[0], getrennt[-1]
    f_ein, f_aus = werte[i0][0], werte[i1][0]
    vor = ((ziel_f - f_ein) % 1.0) * L
    nach = ((f_aus - ziel_f) % 1.0) * L
    if vor > 0.6 * L or nach > 0.6 * L or vor + nach > 0.8 * L:
        return {"hinweis": "Pit lane does not span the start/finish line (%.0f m before, %.0f m after); the generator "
                           "only knows pit lanes across the finish line, it stays automatic." % (vor, nach)}
    if vor + nach < 380.0:
        return {"hinweis": "Pit lane too short (%.0f m) for the generator; it stays automatic." % (vor + nach)}
    mitte = werte[i0:i1 + 1]
    seite = "rechts" if np.median([q for _f, q, _x in mitte]) > 0 else "links"
    abstand = float(np.median([frei for _f, _q, frei in mitte])) - 6.0     # Mitte der 12 m breiten Gasse
    abstand = float(np.clip(abstand, *BOX_ABSTAND_GRENZEN))
    return {"aktiv": True, "seite": seite, "einfahrt_vor_start_m": round(vor, 1), "ausfahrt_nach_start_m": round(nach, 1),
            "abstand_fahrbahn": round(abstand, 1),
            "tempolimit_kmh": 60.0 if code.replace("_reverse", "") in TEMPOLIMIT_60 else 80.0}


# =============================================================================== Seitenbereiche aus der Fahrphysik
class _Physik:
    """Fahrphysik (``TRN~``) als Böden (Punktabfrage in der Draufsicht) und Mauern (senkrechte Flächen als Strecken)."""

    def __init__(self, v, tri, codes, zelle=8.0):
        p = v[tri]
        n = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
        ny = np.abs(n[:, 1]) / np.maximum(np.linalg.norm(n, axis=1), 1e-12)
        mauer = np.isin(codes, MAUER_CODES) & (ny < 0.3)
        boden = ny >= 0.3
        self.zelle = zelle
        self.p, self.codes = p[boden], codes[boden]
        self.raster = self._raster(self.p[:, :, [0, 2]])
        # Mauern: je Dreieck die längste Kante in der Draufsicht
        m = p[mauer][:, :, [0, 2]]
        kanten = [(0, 1), (1, 2), (2, 0)]
        laengen = np.stack([np.linalg.norm(m[:, a] - m[:, b], axis=1) for a, b in kanten], axis=1)
        k = np.argmax(laengen, axis=1)
        self.ma = m[np.arange(len(m)), [kanten[i][0] for i in k]] if len(m) else np.zeros((0, 2))
        self.mb = m[np.arange(len(m)), [kanten[i][1] for i in k]] if len(m) else np.zeros((0, 2))
        self.my = p[mauer][:, :, 1].mean(axis=1) if len(m) else np.zeros(0)
        self.mraster = self._raster(np.stack([self.ma, self.mb], axis=1)) if len(m) else {}

    def _raster(self, flaechen):
        lo = np.floor(flaechen.min(axis=1) / self.zelle).astype(np.int64)
        hi = np.floor(flaechen.max(axis=1) / self.zelle).astype(np.int64)
        raster: Dict[Tuple[int, int], List[int]] = {}
        for i in range(len(flaechen)):
            for a in range(lo[i, 0], hi[i, 0] + 1):
                for b in range(lo[i, 1], hi[i, 1] + 1):
                    raster.setdefault((a, b), []).append(i)
        return {k: np.array(v) for k, v in raster.items()}

    def boden(self, x, z, y, toleranz=6.0) -> Optional[str]:
        kand = self.raster.get((int(math.floor(x / self.zelle)), int(math.floor(z / self.zelle))))
        if kand is None:
            return None
        p = self.p[kand]
        a, b, c = p[:, 0], p[:, 1], p[:, 2]
        det = (b[:, 0] - a[:, 0]) * (c[:, 2] - a[:, 2]) - (c[:, 0] - a[:, 0]) * (b[:, 2] - a[:, 2])
        ok = np.abs(det) > 1e-9
        det = np.where(ok, det, 1.0)
        l1 = ((x - a[:, 0]) * (c[:, 2] - a[:, 2]) - (c[:, 0] - a[:, 0]) * (z - a[:, 2])) / det
        l2 = ((b[:, 0] - a[:, 0]) * (z - a[:, 2]) - (x - a[:, 0]) * (b[:, 2] - a[:, 2])) / det
        l0 = 1 - l1 - l2
        innen = ok & (l0 >= -1e-6) & (l1 >= -1e-6) & (l2 >= -1e-6)
        if not innen.any():
            return None
        hy = l0 * a[:, 1] + l1 * b[:, 1] + l2 * c[:, 1]
        d = np.where(innen, np.abs(hy - y), np.inf)
        j = int(np.argmin(d))
        return str(self.codes[kand[j]]) if d[j] < toleranz else None

    def mauer(self, p0, p1, y, toleranz=8.0) -> Optional[float]:
        """Abstand (Anteil 0…1 von p0 nach p1) zur ersten Mauer auf der Strecke p0→p1, sonst None."""
        if not self.mraster:
            return None
        zellen = set()
        laenge = float(np.linalg.norm(p1 - p0))
        for t in np.linspace(0.0, 1.0, max(2, int(laenge / (self.zelle / 2)) + 1)):
            q = p0 + t * (p1 - p0)
            zellen.add((int(math.floor(q[0] / self.zelle)), int(math.floor(q[1] / self.zelle))))
        kand = np.unique(np.concatenate([self.mraster[k] for k in zellen if k in self.mraster] or [np.zeros(0, int)]))
        if not len(kand):
            return None
        a, b = self.ma[kand], self.mb[kand]
        r, s = p1 - p0, b - a
        nenner = r[0] * s[:, 1] - r[1] * s[:, 0]
        d = a - p0
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (d[:, 0] * s[:, 1] - d[:, 1] * s[:, 0]) / nenner
            u = (d[:, 0] * r[1] - d[:, 1] * r[0]) / nenner
        ok = (np.abs(nenner) > 1e-9) & (t >= 0) & (t <= 1) & (u >= 0) & (u <= 1) & (np.abs(self.my[kand] - y) < toleranz)
        return float(t[ok].min()) if ok.any() else None


def _physik_laden(archiv):
    from ego import trn
    res = next((r for r in archiv.resources if r.resource_type.startswith("TRN")
                and r.identifier.endswith("?context=physics_terrain")), None)
    if res is None:
        return None
    v, tri, cs, _attr, _a, _b = trn.mesh_mit_attributen(trn.open_trn(res.data()).geometry())
    codes = np.array([c if isinstance(c, str) else c.decode("latin1") for c in cs])
    return _Physik(np.asarray(v, float), np.asarray(tri), codes)


def _profil_seite(physik, mitte, rechts_n, y, w) -> dict:
    """Querschnitt einer Seite ab dem Fahrbahnrand: Randstein, Grünstreifen, Auslauf, Barrierenabstand."""
    rand = mitte + rechts_n * w
    weg = np.arange(0.0, SEITE_WEITE_M + 1e-6, SEITE_QUER_M)
    wand = physik.mauer(rand, rand + rechts_n * SEITE_WEITE_M, y)
    grenze = SEITE_WEITE_M if wand is None else wand * SEITE_WEITE_M
    codes = []
    for d in weg[weg < grenze]:
        q = rand + rechts_n * d
        codes.append(physik.boden(q[0], q[1], y))
    i = 0
    while i < len(codes) and i < 3 and codes[i] not in KERB_CODES:   # Randstein beginnt höchstens 1 m neben dem Rand
        i += 1
    kerb = 0
    if i < 3:
        while i + kerb < len(codes) and codes[i + kerb] in KERB_CODES:
            kerb += 1
    rest = codes[(i + kerb) if kerb else 0:]
    gruen = 0
    while gruen < len(rest) and rest[gruen] in GRAS_CODES:
        gruen += 1
    auslauf_codes = [c for c in rest[gruen:] if c is not None] or [c for c in rest if c is not None]
    zaehl = Counter("kies" if c in KIES_CODES else "asphalt" if c in ASPHALT_CODES else "gras"
                    for c in auslauf_codes)
    n = max(1, sum(zaehl.values()))
    auslauf = ("kies" if zaehl["kies"] / n >= 0.3 else "asphalt" if zaehl["asphalt"] / n >= 0.4 else "gras")
    # ohne Mauer: wo der Boden endet (Gelände außerhalb der Fahrphysik), sonst die größte Suchweite
    if wand is None:
        leer = [j for j, c in enumerate(codes) if c is None and j * SEITE_QUER_M > 2.0]
        grenze = leer[0] * SEITE_QUER_M if leer else SEITE_WEITE_M
    return {"randstein": kerb * SEITE_QUER_M, "gruen": gruen * SEITE_QUER_M, "auslauf": auslauf,
            "barriere": max(1.0, grenze)}


def _glaetten(werte, fenster):
    werte = np.asarray(werte, float)
    n = len(werte)
    h = fenster // 2
    idx = (np.arange(n)[:, None] + np.arange(-h, h + 1)[None]) % n
    return np.median(werte[idx], axis=1)


def _modus(werte, fenster):
    n = len(werte)
    h = fenster // 2
    return [Counter(werte[(i + k) % n] for k in range(-h, h + 1)).most_common(1)[0][0] for i in range(n)]


def seitenraum(archiv, st, laenge_m: float, halbbreite) -> Tuple[List[dict], dict]:
    """``seitenraum.abschnitte`` (beide Seiten) aus der Fahrphysik der Spielstrecke.

    Rückgabe: (Abschnitte, Info). Abschnitte unter ``SEITE_MIN_ABSCHNITT_M`` gehen im Vorgänger auf; Werte gerundet
    (Randstein 0,5 m, Grünstreifen 1 m, Barriere 2 m), damit die Liste im Editor überschaubar bleibt."""
    physik = _physik_laden(archiv)
    if physik is None:
        return [], {"hinweis": "Game track without driving physics (TRN~): side areas automatic."}
    haupt = st.hauptlinie
    L_spiel = haupt.laenge
    n = max(16, int(L_spiel / SEITE_SCHRITT_M))
    fs = np.arange(n) / n
    proben = {"links": [], "rechts": []}
    for f in fs:
        c, t = haupt.punkt(float(f))
        mitte = np.array([c[0], c[2]])
        rn = _rechts(t)
        wl, wr = halbbreite(float(f))
        proben["rechts"].append(_profil_seite(physik, mitte, rn, c[1], wr))
        proben["links"].append(_profil_seite(physik, mitte, -rn, c[1], wl))
    L = float(laenge_m)
    schritt_m = L / n
    min_n = max(1, int(round(SEITE_MIN_ABSCHNITT_M / schritt_m)))
    abschnitte = []
    for seite, werte in proben.items():
        k = np.round(2 * _glaetten([w["randstein"] for w in werte], 5)) / 2
        k = np.where(k < 0.5, 0.0, np.minimum(k, 3.0))
        g = np.minimum(np.round(_glaetten([w["gruen"] for w in werte], 5)), 10.0)
        b = np.clip(2 * np.round(_glaetten([w["barriere"] for w in werte], 7) / 2), 2.0, SEITE_WEITE_M)
        a = _modus([w["auslauf"] for w in werte], 7)
        schluessel = [(float(k[i]), float(g[i]) if a[i] != "gras" else None, a[i], float(b[i])) for i in range(n)]
        # Läufe gleicher Werte; zu kurze Läufe übernehmen den Wert des Vorgängers
        laeufe = []
        for i, s in enumerate(schluessel):
            if laeufe and laeufe[-1][1] == s:
                laeufe[-1][2] = i + 1
            else:
                laeufe.append([i, s, i + 1])
        zusammen = []
        for lauf in laeufe:
            if zusammen and (lauf[2] - lauf[0] < min_n):
                zusammen[-1][2] = lauf[2]
            elif zusammen and zusammen[-1][1] == lauf[1]:
                zusammen[-1][2] = lauf[2]
            else:
                zusammen.append(list(lauf))
        for a0, s, a1 in zusammen:
            kerb, gruen, auslauf, barriere = s
            ab = {"von_s": round(a0 * schritt_m, 1), "bis_s": round(min(a1 * schritt_m, L), 1), "seite": seite,
                  "randstein_breite": kerb, "auslauf": auslauf, "barriere_abstand": barriere}
            if gruen is not None:
                ab["gruenstreifen_breite"] = gruen
            abschnitte.append(ab)
    info = {"abschnitte": len(abschnitte), "stationen": n,
            "randstein_anteil": round(float(np.mean([w["randstein"] > 0 for s in proben.values() for w in s])), 3)}
    return abschnitte, info


# =============================================================================== Kulisse
def kulisse(archiv, code: str, transform, punkte_xz, halbbreiten, katalog_daten, ohne=KULISSE_OHNE,
            versatz=(0.0, 0.0, 0.0), netz=None) -> Tuple[dict, dict]:
    """Objekte der Spielstrecke als ``umgebung.json``-Daten (nur Modelle aus dem Umgebungskatalog).

    ``transform(xz) -> (xz_neu, drehung_grad)`` bildet Spielkoordinaten (N×2) auf die importierte Strecke ab
    (Zentrierung, Drehung, Maßstab). ``punkte_xz``/``halbbreiten``: importierte Mittellinie, um Objekte auf
    der neuen Fahrbahn auszulassen. ``versatz``: Spielweltversatz des Projekts; Objekte außerhalb des Streckenbereichs
    (Loch des Außenterrains, AO ±1024 m) entfallen. ``netz``: Generatornetz der neuen Strecke (``vertices``, ``dreiecke``,
    ``oberflaeche``); Objekte auf Fahrbahn, Auslauf, Boxengasse oder an Mauern entfallen wie in der Prüfregel
    UMG_AUF_FAHRBAHN. Höhe: auf dem Boden (``hoehe_m`` 0)."""
    from ego import pkg
    from . import umgebung as U
    index = U.katalog_index(katalog_daten)
    zaehler, ausgelassen = Counter(), Counter()
    objekte = []
    nah = _NahSuche(np.asarray(punkte_xz, float), np.asarray(halbbreiten, float))
    flaechen = kanten = None
    if netz is not None:
        from .definition import OBERFLAECHEN
        flaechen, kanten = U.flaechen_aus_netz((np.asarray(netz["vertices"], float), np.asarray(netz["dreiecke"], np.int64),
                                                np.asarray(netz["oberflaeche"], np.int64), list(OBERFLAECHEN)))
        fahrbahn = {OBERFLAECHEN.index(k) for k in U.FAHRBAHN_KLASSEN if k in OBERFLAECHEN}
    x0, z0, kante = U.LOCH
    lo = np.array([max(x0, -1024.0), max(z0, -1024.0)]) + KULISSE_RAND_M - np.array([versatz[0], versatz[2]], float)
    hi = np.array([min(x0 + kante, 1024.0), min(z0 + kante, 1024.0)]) - KULISSE_RAND_M - np.array([versatz[0], versatz[2]], float)
    dateien = [archiv]
    for res in (r for a in dateien for r in a.resources if r.identifier.endswith(".layer")):
        try:
            instanzen = pkg.lesen(res.fragments[0].read()).instanzen()
        except Exception:                          # fremde Layerformate überspringen
            continue
        for inst in instanzen:
            if not inst.modell or inst.matrix is None:
                continue
            basis = re.sub(r"\.idf(\?.*)?$", "", inst.modell)
            eintrag = index.get(basis)
            if eintrag is None:
                continue
            kat = eintrag["kategorie"]
            if kat in ohne:
                ausgelassen["kategorie"] += 1
                continue
            m = np.asarray(inst.matrix, float).reshape(4, 4)
            skala = float(np.linalg.norm(m[0, :3]))
            if not U.SKALA_MIN <= skala <= U.SKALA_MAX:
                ausgelassen["skala"] += 1
                continue
            xz, drehung = transform(np.array([[m[3, 0], m[3, 2]]]))
            x, z = float(xz[0, 0]), float(xz[0, 1])
            if not (lo[0] <= x <= hi[0] and lo[1] <= z <= hi[1]):
                ausgelassen["ausserhalb"] += 1
                continue
            if nah.zu_nah(x, z, KULISSE_ABSTAND_M) or (flaechen is not None and (
                    any(int(flaechen.klasse[i]) in fahrbahn for i, _ in flaechen.treffer(x, z))
                    or (kanten is not None and kanten.abstand(x, z, U.MAUER_ABSTAND_M) < U.MAUER_ABSTAND_M))):
                ausgelassen["fahrbahn"] += 1
                continue
            # lagematrix: Zeile 0 = (cos w, 0, −sin w), Zeile 2 = (sin w, 0, cos w); die Drehung der Welt um W
            # dreht die lokale x-Achse mit, also w' = w − W
            winkel = math.degrees(math.atan2(m[2, 0], m[0, 0])) - drehung
            objekte.append({"id": "spiel_%s_%d" % (kat, len(objekte) + 1), "modell": basis, "x": round(x, 2),
                            "z": round(z, 2), "hoehe_m": 0.0, "drehung_grad": round((winkel + 180) % 360 - 180, 1),
                            "skala": round(skala, 3), "gruppe": "Spiel: %s" % kat})
            zaehler[kat] += 1
    if len(objekte) > U.VIELE_OBJEKTE:
        # Richtwert der Prüfung (UMG_VIELE): die streckennächsten Objekte behalten, sie sind im Spiel zu sehen
        d = _abstand_zur_linie(np.array([[o["x"], o["z"]] for o in objekte]), np.asarray(punkte_xz, float))
        behalten = set(np.argsort(d, kind="stable")[:U.VIELE_OBJEKTE].tolist())
        ausgelassen["zu_viele"] = len(objekte) - len(behalten)
        for i, o in enumerate(objekte):
            if i not in behalten:
                zaehler[o["gruppe"].split(": ", 1)[-1]] -= 1
        objekte = [o for i, o in enumerate(objekte) if i in behalten]
    daten = U.normalisieren({"schema": 1, "bodenbewuchs": True, "objekte": objekte})
    return daten, {"objekte": len(objekte), "je_kategorie": {k: v for k, v in zaehler.items() if v},
                   "ausgelassen": dict(ausgelassen)}


def _abstand_zur_linie(p, xz, block=512) -> np.ndarray:
    """Kleinster Abstand jedes Punkts (M×2) zum geschlossenen Linienzug ``xz`` (N×2)."""
    a, b = xz, np.roll(xz, -1, axis=0)
    ab = b - a
    lang = np.maximum((ab * ab).sum(1), 1e-12)
    out = np.empty(len(p))
    for i in range(0, len(p), block):
        q = p[i:i + block, None, :]
        t = np.clip(((q - a) * ab).sum(2) / lang, 0.0, 1.0)
        out[i:i + block] = np.linalg.norm(a + t[..., None] * ab - q, axis=2).min(1)
    return out


class _NahSuche:
    """Abstandstest zur Mittellinie (Draufsicht) über ein Raster der Liniensegmente."""

    def __init__(self, xz, halb, zelle=25.0):
        self.a, self.b = xz, np.roll(xz, -1, axis=0)
        self.halb = halb
        self.zelle = zelle
        lo = np.floor(np.minimum(self.a, self.b) / zelle).astype(int)
        hi = np.floor(np.maximum(self.a, self.b) / zelle).astype(int)
        self.raster: Dict[Tuple[int, int], List[int]] = {}
        for i in range(len(xz)):
            for p in range(lo[i, 0] - 2, hi[i, 0] + 3):
                for q in range(lo[i, 1] - 2, hi[i, 1] + 3):
                    self.raster.setdefault((p, q), []).append(i)

    def zu_nah(self, x, z, abstand):
        kand = self.raster.get((int(math.floor(x / self.zelle)), int(math.floor(z / self.zelle))))
        if not kand:
            return False
        k = np.array(kand)
        a, b = self.a[k], self.b[k]
        ab = b - a
        t = np.clip(((np.array([x, z]) - a) * ab).sum(1) / np.maximum((ab * ab).sum(1), 1e-12), 0, 1)
        d = np.linalg.norm(a + t[:, None] * ab - np.array([x, z]), axis=1)
        return bool(np.any(d < self.halb[k] + abstand))
