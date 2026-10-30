"""AP9b: PVS-Zellen für eine neue Strecke erzeugen (Format: docs/formate/pvs.md).

Gesichert (Bahrain, Monaco, Portimão geprüft): Gruppe-A-Zellen sind 2D-Säulen auf einem
4-m-Raster (i16 x = Welt-x, i16 y = Welt-z, je (x, z) genau eine Zelle); das dritte Feld ist
die Bodenhöhe in **Zentimetern** (Bahrain: Feld ≈ 99,85 · y_Barriere + 48, r = 0,999).
Die Reihenfolge der Zellen ist ungeordnet (aufeinanderfolgende Zellen im Median 500 m
auseinander) → das Spiel baut seine Suche vermutlich selbst auf (Vermutung).
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from ego import pvs, streckenlogik

RASTER_M = 4
HOEHE_ZUSATZ_CM = 50     # Original: Zellhöhe ≈ Barrierenfuß + 0,48 m


def linien_aus_logik(logik: dict) -> List[np.ndarray]:
    """TrackSpace-Linien (Welt x, y, z) aus platzierter Generatorlogik."""
    aus = []
    for s in logik["spielformate"]["TrackSpace"]["splines"]:
        p = np.asarray(s["position"], float)
        aus.append(np.vstack([p, p[:1]]) if s.get("closed") else p)
    return aus


def linien_aus_archiv(archiv, code: str) -> List[np.ndarray]:
    """TrackSpace-Linien aus dem Archiv (``ego.streckenlogik.lade``)."""
    st = streckenlogik.lade(archiv, code, mit_plaetzen=False)
    aus = []
    for sp in st.splines.values():
        p = np.asarray([tuple(v) for v in sp.punkte], float)
        aus.append(np.vstack([p, p[:1]]) if sp.geschlossen else p)
    return aus


def verdichten(p: np.ndarray, schritt: float = 1.0) -> np.ndarray:
    teile = [p[:1]]
    for a, b in zip(p[:-1], p[1:]):
        n = max(1, int(math.ceil(np.linalg.norm(b - a) / schritt)))
        teile.append(a + (b - a) * (np.arange(1, n + 1)[:, None] / n))
    return np.vstack(teile)


def zellen_berechnen(linien: Sequence[np.ndarray], korridor_m: float = 60.0,
                     flaeche: Optional[Tuple[float, float, float, float]] = None,
                     raster: int = RASTER_M, hoehe_zusatz_cm: int = HOEHE_ZUSATZ_CM) -> np.ndarray:
    """Rasterzellen (x, z, hoehe_cm) im Korridor um alle Linien bzw. auf der ganzen
    ``flaeche`` (xmin, xmax, zmin, zmax). Höhe = Höhe des nächsten Linienpunkts."""
    P = np.vstack([verdichten(l, 2.0) for l in linien])
    if flaeche:
        x0, x1, z0, z1 = flaeche
        gx = np.arange(int(np.floor(x0 / raster)), int(np.ceil(x1 / raster)) + 1)
        gz = np.arange(int(np.floor(z0 / raster)), int(np.ceil(z1 / raster)) + 1)
        G = np.stack(np.meshgrid(gx, gz, indexing="ij"), -1).reshape(-1, 2)
        H = np.empty(len(G))
        Pd = P[::2]
        for s in range(0, len(G), 2048):
            g = G[s:s + 2048] * raster
            d = (g[:, None, 0] - Pd[None, :, 0]) ** 2 + (g[:, None, 1] - Pd[None, :, 2]) ** 2
            H[s:s + 2048] = Pd[d.argmin(1), 1]
    else:
        r = int(math.ceil(korridor_m / raster)) + 1
        o = np.stack(np.meshgrid(np.arange(-r, r + 1), np.arange(-r, r + 1), indexing="ij"), -1).reshape(-1, 2)
        o = o[np.hypot(o[:, 0], o[:, 1]) * raster <= korridor_m + 2 * raster]
        basis = np.round(P[:, [0, 2]] / raster).astype(np.int64)
        kand = (basis[:, None, :] + o[None, :, :]).reshape(-1, 2)
        quelle = np.repeat(np.arange(len(P)), len(o))
        dist = np.hypot(kand[:, 0] * raster - P[quelle, 0], kand[:, 1] * raster - P[quelle, 2])
        ok = dist <= korridor_m
        kand, quelle, dist = kand[ok], quelle[ok], dist[ok]
        reihen = np.lexsort((dist, kand[:, 1], kand[:, 0]))
        kand, quelle = kand[reihen], quelle[reihen]
        erst = np.ones(len(kand), bool)
        erst[1:] = np.any(kand[1:] != kand[:-1], axis=1)
        G, H = kand[erst], P[quelle[erst], 1]
    xyz = np.column_stack([G[:, 0] * raster, G[:, 1] * raster, np.round(H * 100 + hoehe_zusatz_cm)])
    if np.abs(xyz).max() > 32767:
        raise ValueError("Zellkoordinate außerhalb i16")
    return xyz.astype(np.int64)


def pvs_fuer_strecke(archiv, logik: Optional[dict] = None, code: str = "portimao", korridor_m: float = 60.0,
                     flaeche=None, gruppe_b: str = "behalten", alle_absolut: bool = False,
                     mischen: bool = True) -> Dict:
    """Ersetzt im Archiv (``erp.ErpFile``, wird verändert) das PVS durch Zellen über der neuen Strecke.

    Linien: TrackSpace aus ``logik`` (platzierte Generatorlogik in Weltkoordinaten) oder aus dem Archiv.
    Sichtbarkeit: alle im Träger-PVS belegten Bits in allen Zellen und Teilströmen („alles sichtbar“).
    ``PVSObjIDRes`` und die Hüllkugeln bleiben unverändert (passen weiter zu den Bits)."""
    linien = linien_aus_logik(logik) if logik else linien_aus_archiv(archiv, code)
    xyz = zellen_berechnen(linien, korridor_m, flaeche)
    if mischen:                       # Original ist ungeordnet; feste Saat → reproduzierbar
        xyz = xyz[np.random.default_rng(9).permutation(len(xyz))]
    res = archiv.find_all(r"\.volumedata\.pvsbin$")
    if len(res) != 1 or len(archiv.find_all(r"\.header\.pvsbin$")) != 1:
        raise ValueError("Archiv braucht genau ein PVS-Paar")
    alt_daten = res[0].fragments[0].read()
    alt = pvs.vis_lesen(alt_daten)
    neu = pvs.zellen_neu_aufbauen(alt, [tuple(v) for v in xyz.tolist()], gruppe_b=gruppe_b,
                                  alle_absolut=alle_absolut)
    daten = pvs.vis_schreiben(neu)
    res[0].set_fragment_data(0, daten)
    return {"zellen_a": len(xyz), "zellen_b": neu.feld(56), "zellen_c": neu.feld(60, "<H"),
            "x": [int(xyz[:, 0].min()), int(xyz[:, 0].max())], "z": [int(xyz[:, 1].min()), int(xyz[:, 1].max())],
            "hoehe_cm": [int(xyz[:, 2].min()), int(xyz[:, 2].max())],
            "vis_bytes_alt": len(alt_daten), "vis_bytes_neu": len(daten), "linien": linien}


def abdeckung(vis: pvs.PvsVis, linien: Sequence[np.ndarray], raster: int = RASTER_M) -> Dict:
    """Größter x/z-Abstand eines Linienpunkts (1-m-Schritte) zur nächsten Gruppe-A-Zelle."""
    menge = {(z.x, z.y) for z in vis.zellen[:vis.feld(52)]}
    worst = []
    for l in linien:
        P = verdichten(np.asarray(l, float), 1.0)
        best = np.full(len(P), np.inf)
        for dx in range(-3, 4):
            for dz in range(-3, 4):
                gx = (np.floor(P[:, 0] / raster).astype(int) + dx) * raster
                gz = (np.floor(P[:, 2] / raster).astype(int) + dz) * raster
                vorh = np.fromiter(((int(a), int(b)) in menge for a, b in zip(gx, gz)), bool, len(P))
                best = np.minimum(best, np.where(vorh, np.hypot(gx - P[:, 0], gz - P[:, 2]), np.inf))
        worst.append(float(best.max()))
    return {"max_abstand_m": worst, "alle_hoechstens_5m": all(w <= 5 for w in worst)}
