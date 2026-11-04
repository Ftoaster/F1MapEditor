"""Zufälliger, gültiger Streckenvorschlag (Schritt F, „Vorschlag erzeugen“).

Ablauf je Versuch (alles NumPy, Zufall nur über ``numpy.random.default_rng``):

1. Punktwolke in einer Ellipse → konvexe Hülle (Monotone Chain).
2. Eckpunkte verschieben und – an der jeweils längsten Kante – Zusatzpunkte einfügen, bis 1,5 · ``kurven`` Eckpunkte
   da sind (in der ersten Hälfte der Versuche muss die gezählte Kurvenzahl ±25 % treffen); die Einfügepunkte werden quer zur Kante versetzt (so entstehen Einbuchtungen).
3. Geschlossene Linie in 5-m-Schritten neu abtasten, mit wachsender Gauß-Glättung (Periodisch, FFT) runden,
   bis der kleinste Radius ≥ ``min_radius_m`` ist; auf die Solllänge skalieren.
4. Prüfen (siehe ``pruefen_form``): selbstschnittfrei, Mindestabstand zwischen entfernten Streckenteilen, ±1024 m,
   Länge, und die Prüfregeln des Generators (``pruefregeln.vorab_pruefen`` ohne Fehler und ohne Warnungen).
   Schlägt etwas fehl, folgt der nächste Versuch (``default_rng([seed, versuch])``): gleicher Seed → gleiche Strecke.
5. Start auf dem geradesten Abschnitt, Ausgabe als ``kontrollpunkte`` (Abstand ``punktabstand_m``, 25 m) mit
   ``approximation_m = 6`` und 14 m Breite.

Ergebnis ist eine Rohdefinition wie bei ``strecke.importe`` (Metadaten in ``editor_import``).
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

import numpy as np

from .importe import APPROXIMATION_M, AO_HALBBREITE_M, STANDARD_BREITE_M

#: Abtastung der Arbeitslinie (m).
SCHRITT_M = 5.0
#: Voreinstellungen (Länge 4–6 km wie die meisten Spielstrecken; Radius deutlich über dem Originalminimum 7,5 m).
STANDARD_LAENGE_M = 5000.0
STANDARD_MIN_RADIUS_M = 30.0
STANDARD_KURVEN = 14
LAENGE_BEREICH_M = (2000.0, 7000.0)
KURVEN_BEREICH = (6, 40)
MIN_RADIUS_BEREICH_M = (10.0, 200.0)
LAENGE_TOLERANZ = 0.02
#: Streckenteile, die weiter als diese Strecke (Bogenlänge) auseinander liegen, müssen Abstand halten.
FREIHALTE_BOGEN_M = 250.0
#: Mindestabstand der Mittellinie zu entfernten Streckenteilen (Fahrbahn 14 m + Randstreifen + Barrieren).
MIN_ABSTAND_M = 70.0
#: Eckpunkte der Rohform je gewünschter Kurve (die Glättung verschmilzt einen Teil davon).
ECKEN_JE_KURVE = 1.5
#: Obergrenze für Versuche, bevor ``VorschlagFehler`` kommt.
MAX_VERSUCHE = 400


class VorschlagFehler(RuntimeError):
    """Es wurde innerhalb der Versuche keine gültige Strecke gefunden."""


# =============================================================================== Geometrie
def konvexe_huelle(punkte) -> np.ndarray:
    """Monotone Chain (Andrew), Rückgabe gegen den Uhrzeigersinn (mathematisch), ohne Wiederholung des Anfangs."""
    pts = sorted({(float(x), float(y)) for x, y in np.asarray(punkte, float)})
    if len(pts) <= 2:
        return np.array(pts)

    def kreuz(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    unten: List[Tuple[float, float]] = []
    for p in pts:
        while len(unten) >= 2 and kreuz(unten[-2], unten[-1], p) <= 0:
            unten.pop()
        unten.append(p)
    oben: List[Tuple[float, float]] = []
    for p in reversed(pts):
        while len(oben) >= 2 and kreuz(oben[-2], oben[-1], p) <= 0:
            oben.pop()
        oben.append(p)
    return np.array(unten[:-1] + oben[:-1])


def _umfang(P) -> float:
    return float(np.linalg.norm(np.roll(P, -1, axis=0) - P, axis=1).sum())


def neu_abtasten(P, schritt_m: float = SCHRITT_M, n: Optional[int] = None) -> np.ndarray:
    """Geschlossenes Polygon gleichmäßig nach Bogenlänge abtasten."""
    P = np.asarray(P, float)
    ring = np.vstack([P, P[:1]])
    s = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(ring, axis=0), axis=1))]
    n = n or max(16, int(round(s[-1] / schritt_m)))
    t = np.linspace(0.0, s[-1], n, endpoint=False)
    return np.column_stack([np.interp(t, s, ring[:, 0]), np.interp(t, s, ring[:, 1])])


def glaetten(P, sigma_m: float, schritt_m: float = SCHRITT_M) -> np.ndarray:
    """Periodische Gauß-Glättung (FFT) einer gleichmäßig abgetasteten geschlossenen Linie."""
    P = np.asarray(P, float)
    n = len(P)
    f = np.fft.fftfreq(n, d=1.0)                       # Zyklen je Abtastwert
    sigma = max(sigma_m / schritt_m, 1e-6)
    h = np.exp(-2.0 * (np.pi * sigma * f) ** 2)
    return np.column_stack([np.fft.ifft(np.fft.fft(P[:, k]) * h).real for k in range(2)])


def kruemmung(P, schritt_m: float = SCHRITT_M) -> np.ndarray:
    """Vorzeichenbehaftete Krümmung (1/m, > 0 = gegen den Uhrzeigersinn im x/y-Koordinatensystem)."""
    d1 = (np.roll(P, -1, axis=0) - np.roll(P, 1, axis=0)) / (2 * schritt_m)
    d2 = (np.roll(P, -1, axis=0) - 2 * P + np.roll(P, 1, axis=0)) / schritt_m ** 2
    return (d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]) / np.maximum(np.linalg.norm(d1, axis=1) ** 3, 1e-9)


def selbstschnitt(P) -> bool:
    """True, wenn zwei nicht benachbarte Strecken des geschlossenen Polygons sich kreuzen (vektorisiert)."""
    A = np.asarray(P, float)
    B = np.roll(A, -1, axis=0)
    n = len(A)
    if n < 4:
        return True
    d = B - A

    def orient(p, q, r):
        return (q[..., 0] - p[..., 0]) * (r[..., 1] - p[..., 1]) - (q[..., 1] - p[..., 1]) * (r[..., 0] - p[..., 0])
    a1, a2 = A[:, None, :], B[:, None, :]
    b1, b2 = A[None, :, :], B[None, :, :]
    o1, o2 = orient(a1, a2, b1), orient(a1, a2, b2)
    o3, o4 = orient(b1, b2, a1), orient(b1, b2, a2)
    kreuzt = (o1 * o2 < 0) & (o3 * o4 < 0)
    i = np.arange(n)
    nah = (np.abs(i[:, None] - i[None, :]) <= 1) | (np.abs(i[:, None] - i[None, :]) >= n - 1)
    return bool(np.any(kreuzt & ~nah))


def mindestabstand_entfernter_teile(P, schritt_m: float = SCHRITT_M, bogen_m: float = FREIHALTE_BOGEN_M) -> float:
    """Kleinster Abstand zweier Punkte, die entlang der Strecke mehr als ``bogen_m`` auseinander liegen."""
    P = np.asarray(P, float)
    n = len(P)
    D = np.linalg.norm(P[:, None, :] - P[None, :, :], axis=2)
    i = np.arange(n)
    bogen = np.minimum(np.abs(i[:, None] - i[None, :]), n - np.abs(i[:, None] - i[None, :])) * schritt_m
    D = np.where(bogen > bogen_m, D, np.inf)
    return float(D.min())


# =============================================================================== Zufallsform
def _rohform(rng, kurven: int, seitenverhaeltnis: float) -> np.ndarray:
    """Hülle einer Punktwolke, Eckpunkte verschoben, Zusatzpunkte quer zur längsten Kante eingefügt."""
    n0 = int(rng.integers(max(8, kurven), max(12, 2 * kurven) + 1))
    w = rng.uniform(0.0, 2 * np.pi, n0)
    r = np.sqrt(rng.uniform(0.0, 1.0, n0))
    wolke = np.column_stack([seitenverhaeltnis * r * np.cos(w), r * np.sin(w)])
    huelle = konvexe_huelle(wolke)
    if len(huelle) > kurven:                      # zu viele Ecken: zufällig ausdünnen
        huelle = huelle[np.sort(rng.choice(len(huelle), kurven, replace=False))]
    pts = [p for p in huelle]
    # Ecken leicht versetzen (Anteil der Nachbarabstände)
    for i in range(len(pts)):
        nachbar = min(np.linalg.norm(pts[i] - pts[i - 1]), np.linalg.norm(pts[i] - pts[(i + 1) % len(pts)]))
        pts[i] = pts[i] + rng.normal(0.0, 0.12 * nachbar, 2)
    while len(pts) < kurven:
        kanten = [np.linalg.norm(pts[(i + 1) % len(pts)] - pts[i]) for i in range(len(pts))]
        i = int(np.argmax(np.array(kanten) * rng.uniform(0.7, 1.0, len(kanten))))
        a, b = pts[i], pts[(i + 1) % len(pts)]
        t = rng.uniform(0.35, 0.65)
        m = a + (b - a) * t
        norm = np.array([-(b - a)[1], (b - a)[0]]) / max(np.linalg.norm(b - a), 1e-9)
        m = m + norm * rng.uniform(-0.38, 0.38) * np.linalg.norm(b - a)
        pts.insert(i + 1, m)
    return np.array(pts)


def _glatte_linie(roh, laenge_m: float, min_radius_m: float) -> Optional[np.ndarray]:
    """Roh-Polygon → glatte Linie der Solllänge mit kleinstem Radius ≥ ``min_radius_m`` (sonst None)."""
    ziel = laenge_m
    P = roh * (ziel / _umfang(roh))
    for sigma in (25.0, 35.0, 50.0, 70.0, 95.0, 125.0, 160.0):
        Q = glaetten(neu_abtasten(P), sigma)
        Q = Q * (ziel / _umfang(Q))
        Q = neu_abtasten(Q)                           # wieder exakt gleichmäßig
        Q = Q * (ziel / _umfang(Q))
        if np.abs(kruemmung(Q)).max() <= 1.0 / min_radius_m:
            return Q
    return None


def _start_auf_gerade(Q, schritt_m: float = SCHRITT_M, fenster_m: float = 300.0) -> int:
    """Index in der Mitte des geradesten Fensters."""
    k = np.abs(kruemmung(Q))
    w = max(3, int(round(fenster_m / schritt_m)))
    ker = np.ones(w) / w
    kz = np.convolve(np.r_[k[-w:], k, k[:w]], ker, mode="same")[w:-w]
    return int(np.argmin(kz))


def _kurven_zaehlen(Q, schritt_m: float = SCHRITT_M) -> int:
    """Zahl der Kurven: zusammenhängende Läufe mit |κ| > 1/250 m, mindestens 30 m lang."""
    k = np.abs(kruemmung(Q)) > 1.0 / 250.0
    if not k.any():
        return 0
    if k.all():
        return 1
    start = int(np.argmin(k))                          # an einer Nicht-Kurve beginnen
    k = np.roll(k, -start)
    d = np.diff(np.r_[0, k.astype(int), 0])
    anf, end = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
    return int(np.sum((end - anf) * schritt_m >= 30.0))


def pruefen_form(Q, laenge_m: float, schritt_m: float = SCHRITT_M) -> Optional[str]:
    """Gibt den Grund zurück, warum die Linie ungeeignet ist, sonst None."""
    if selbstschnitt(Q):
        return "Selbstschnitt"
    if mindestabstand_entfernter_teile(Q, schritt_m) < MIN_ABSTAND_M:
        return "Streckenteile zu nah"
    if np.abs(Q).max() + STANDARD_BREITE_M / 2 + 20.0 > AO_HALBBREITE_M:
        return "außerhalb ±1024 m"
    if abs(_umfang(Q) - laenge_m) > LAENGE_TOLERANZ * laenge_m:
        return "Länge"
    return None


def _definition(Q, name, beschreibung, meta, punktabstand_m: float) -> dict:
    """Linie (Zentrum im Ursprung, Start bei Index 0) → Rohdefinition (Arbeitsebene x/y = Draufsicht x/z)."""
    P = neu_abtasten(Q, n=max(16, int(round(_umfang(Q) / punktabstand_m))))
    punkte = [{"x": round(float(x), 3), "z": round(float(z), 3), "y": 0.0} for x, z in P]
    return {"name": name, "beschreibung": beschreibung,
            "mittellinie": {"art": "kontrollpunkte", "punkte": punkte, "approximation_m": APPROXIMATION_M,
                            "hoehen_glaettung_m": 0},
            "fahrbahn": {"breite": STANDARD_BREITE_M},
            "editor_import": meta}


def vorschlag(seed: int = 1, laenge_m: float = STANDARD_LAENGE_M, min_radius_m: float = STANDARD_MIN_RADIUS_M,
              kurven: int = STANDARD_KURVEN, punktabstand_m: float = 25.0, max_versuche: int = MAX_VERSUCHE,
              pruefen: bool = True) -> dict:
    """Erzeugt eine gültige Zufallsstrecke (Rohdefinition). Gleiche Parameter + Seed → gleiche Strecke.

    ``pruefen=False`` überspringt nur die Generator-Prüfregeln (``vorab_pruefen``), die Formprüfung bleibt."""
    if not LAENGE_BEREICH_M[0] <= laenge_m <= LAENGE_BEREICH_M[1]:
        raise ValueError("Length %.0f m outside %.0f–%.0f m." % ((laenge_m,) + LAENGE_BEREICH_M))
    if not KURVEN_BEREICH[0] <= kurven <= KURVEN_BEREICH[1]:
        raise ValueError("Number of corners %d outside %d–%d." % ((kurven,) + KURVEN_BEREICH))
    if not MIN_RADIUS_BEREICH_M[0] <= min_radius_m <= MIN_RADIUS_BEREICH_M[1]:
        raise ValueError("Minimum radius %.0f m outside %.0f–%.0f m." % ((min_radius_m,) + MIN_RADIUS_BEREICH_M))
    seed = int(seed)
    gruende: dict = {}
    for versuch in range(max_versuche):
        rng = np.random.default_rng([abs(seed), versuch])
        roh = _rohform(rng, int(round(ECKEN_JE_KURVE * kurven)), float(rng.uniform(1.0, 1.8)))
        Q = _glatte_linie(roh, laenge_m, min_radius_m)
        if Q is None:
            gruende["Radius"] = gruende.get("Radius", 0) + 1
            continue
        grund = pruefen_form(Q, laenge_m)
        if grund:
            gruende[grund] = gruende.get(grund, 0) + 1
            continue
        gezaehlt = _kurven_zaehlen(Q)
        if versuch < max_versuche // 2 and abs(gezaehlt - kurven) > max(2, 0.25 * kurven):
            gruende["Kurvenzahl"] = gruende.get("Kurvenzahl", 0) + 1     # erst in der 2. Hälfte nicht mehr verlangt
            continue
        i0 = _start_auf_gerade(Q)
        Q = np.roll(Q, -i0, axis=0)
        # Zentrum der Ausdehnung in den Ursprung, zufällige Fahrtrichtung
        Q = Q - (Q.min(axis=0) + Q.max(axis=0)) / 2.0
        if rng.random() < 0.5:
            Q = np.vstack([Q[:1], Q[:0:-1]])
        meta = {"schema": "ego-import/1", "art": "vorschlag", "seed": seed, "versuch": versuch + 1,
                "laenge_soll_m": float(laenge_m), "laenge_m": round(_umfang(Q), 1), "min_radius_m": float(min_radius_m),
                "kurven_soll": int(kurven), "kurven_gezaehlt": gezaehlt,
                "radius_min_m": round(float(1.0 / np.abs(kruemmung(Q)).max()), 1),
                "ausdehnung_m": [round(float(np.ptp(Q[:, 0])), 1), round(float(np.ptp(Q[:, 1])), 1)],
                "verschiebung_m": [0.0, 0.0, 0.0], "approximation_m": APPROXIMATION_M, "massstab": 1.0,
                "passt_in_ao": True, "warnungen": [], "verworfen": dict(gruende)}
        d = _definition(Q, "Vorschlag %d" % seed,
                        "Zufallsvorschlag (Seed %d, %.1f km, %d Kurven geplant, Radius ≥ %.0f m); Fahrbahn %.0f m breit, "
                        "keine Höhen." % (seed, _umfang(Q) / 1000, kurven, min_radius_m, STANDARD_BREITE_M),
                        meta, punktabstand_m)
        if pruefen:
            from . import pruefregeln
            b = pruefregeln.vorab_pruefen(d, versatz=[0, 0, 0])
            if b["meldungen"] and any(m["schwere"] in ("fehler", "warnung") for m in b["meldungen"]):
                for m in b["meldungen"]:
                    if m["schwere"] in ("fehler", "warnung"):
                        gruende[m["id"]] = gruende.get(m["id"], 0) + 1
                        break
                continue
        meta["verworfen"] = dict(gruende)
        return d
    raise VorschlagFehler(("No valid track after %d attempts (reasons: %s). Reduce the minimum radius or change the "
                           "length or number of corners.")
                          % (max_versuche, gruende))
