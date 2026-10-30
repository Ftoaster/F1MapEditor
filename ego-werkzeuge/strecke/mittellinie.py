"""Mittellinie: periodischer kubischer Spline, gleichmäßige Abtastung und Auswertungen.

- ``PeriodischerSpline``: C2-stetiger, geschlossener kubischer Spline (Sehnenlängen-
  Parametrisierung) mit genauer Bogenlänge (Gauß-Legendre) und Umkehrung s -> Parameter.
- ``Mittellinie``: Abtastung in gleichen Abständen (Standard 1 m) ab der Start-Ziel-Linie
  (s = 0), Richtung, Normale, Krümmung, Höhe, Steigung, Querneigung, Breiten.
- Prüfungen: Faltung der Fahrbahnränder (Radius < Breite), Schnittpunkte der Ränder,
  freier Platz neben der Strecke, Kurvenerkennung.

Vorzeichen: Krümmung > 0 = Linkskurve. Die linke Normale ist n = (t_z, -t_x) in der
x/z-Ebene (rechtshändig, y oben).
"""

import math

import numpy as np

from .definition import OBERFLAECHE  # noqa: F401  (für Nutzer des Moduls bequem)

# Gauß-Legendre mit 3 Punkten auf [0, 1]
_GL_X = np.array([0.5 - math.sqrt(0.15), 0.5, 0.5 + math.sqrt(0.15)])
_GL_W = np.array([5.0, 8.0, 5.0]) / 18.0


# =========================================================================== Hilfen (periodisch)
def periodisch_interp(sq, s, werte, laenge):
    """Lineare Interpolation auf einem geschlossenen Gitter s (aufsteigend, in [0, L))."""
    sq = np.mod(np.asarray(sq, dtype=float), laenge)
    s_ext = np.concatenate([s, [s[0] + laenge]])
    w = np.asarray(werte)
    w_ext = np.concatenate([w, w[:1]], axis=0)
    if w.ndim == 1:
        return np.interp(sq, s_ext, w_ext)
    return np.column_stack([np.interp(sq, s_ext, w_ext[:, k]) for k in range(w.shape[1])])


def periodisch_mittel(werte, fenster):
    """Gleitender Mittelwert über ``fenster`` Stützstellen (geschlossen)."""
    fenster = int(fenster)
    if fenster <= 1:
        return np.asarray(werte, dtype=float).copy()
    w = np.asarray(werte, dtype=float)
    n = len(w)
    fenster = min(fenster, n)
    h = fenster // 2
    ext = np.concatenate([w[n - h:], w, w[:fenster - h - 1]]) if h > 0 else np.concatenate([w, w[:fenster - 1]])
    c = np.cumsum(np.concatenate([[0.0], ext]))
    return (c[fenster:] - c[:-fenster])[:n] / fenster


def periodisch_gauss(werte, sigma):
    """Gauß-Glättung (Sigma in Stützstellen), geschlossen; werte (n,) oder (n, d)."""
    w = np.asarray(werte, dtype=float)
    if sigma <= 0:
        return w.copy()
    n = len(w)
    r = int(min(n // 2, math.ceil(4 * sigma)))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    if w.ndim == 1:
        ext = np.concatenate([w[n - r:], w, w[:r]])
        return np.convolve(ext, k, mode="valid")
    return np.column_stack([periodisch_gauss(w[:, i], sigma) for i in range(w.shape[1])])


def periodisch_minimum(werte, halbfenster):
    """Laufendes Minimum über +-halbfenster Stützstellen (geschlossen)."""
    w = np.asarray(werte, dtype=float)
    h = int(halbfenster)
    if h <= 0:
        return w.copy()
    n = len(w)
    h = min(h, n // 2)
    ext = np.concatenate([w[n - h:], w, w[:h]])
    fenster = np.lib.stride_tricks.sliding_window_view(ext, 2 * h + 1)
    return fenster.min(axis=1)


def laeufe(maske):
    """Zusammenhängende True-Bereiche einer geschlossenen Maske als Liste (anfang, ende_exkl).

    Bereiche über das Ende hinweg werden zusammengefasst; ende kann dann > n sein.
    """
    m = np.asarray(maske, dtype=bool)
    n = len(m)
    if m.all():
        return [(0, n)]
    if not m.any():
        return []
    start = int(np.argmin(m))            # erste False-Stelle als Bezug
    r = np.roll(m, -start)
    d = np.diff(np.concatenate([[0], r.astype(int), [0]]))
    anf = np.where(d == 1)[0]
    end = np.where(d == -1)[0]
    return [(int(a + start), int(e + start)) for a, e in zip(anf, end)]


def _thomas(a, b, c, d):
    """Tridiagonales System (nicht zyklisch); d (n,) oder (n, k).

    Je Spalte mit Python-Floats statt NumPy-Skalaren (gleiche Rechenfolge, ~10× schneller)."""
    d = np.asarray(d, dtype=float)
    if d.ndim == 2:
        return np.column_stack([_thomas(a, b, c, d[:, j]) for j in range(d.shape[1])])
    a, b, c, d = (np.asarray(x, dtype=float).tolist() for x in (a, b, c, d))
    n = len(b)
    cp, dp = [0.0] * n, [0.0] * n
    cp[0] = c[0] / b[0]
    dp[0] = d[0] / b[0]
    for i in range(1, n):
        m = b[i] - a[i] * cp[i - 1]
        cp[i] = c[i] / m
        dp[i] = (d[i] - a[i] * dp[i - 1]) / m
    x = [0.0] * n
    x[-1] = dp[-1]
    for i in range(n - 2, -1, -1):
        x[i] = dp[i] - cp[i] * x[i + 1]
    return np.asarray(x)


def zyklisch_tridiagonal(a, b, c, d):
    """Löst a_i x_{i-1} + b_i x_i + c_i x_{i+1} = d_i mit zyklischen Indizes.

    Sherman-Morrison-Verfahren; d darf (n,) oder (n, k) sein.
    """
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float).copy()
    c = np.asarray(c, dtype=float)
    d = np.asarray(d, dtype=float)
    n = len(b)
    beta = a[0]          # Element (0, n-1)
    alpha = c[-1]        # Element (n-1, 0)
    gamma = -b[0]
    bb = b.copy()
    bb[0] = b[0] - gamma
    bb[-1] = b[-1] - alpha * beta / gamma
    x = _thomas(a, bb, c, d)
    u = np.zeros(n)
    u[0] = gamma
    u[-1] = alpha
    z = _thomas(a, bb, c, u)
    fakt = (x[0] + beta * x[-1] / gamma) / (1.0 + z[0] + beta * z[-1] / gamma)
    if d.ndim == 1:
        return x - fakt * z
    return x - np.outer(z, fakt)


# =========================================================================== Spline
def _catmull_rom_zentripetal(p, schritt_m):
    """Geschlossene zentripetale Catmull-Rom-Kurve (α = 0,5) durch p (n, 3), dicht abgetastet.

    Zentripetal: keine Spitzen/Schleifen innerhalb eines Segments und kein Überschwingen bei stark
    unterschiedlichen Punktabständen (Yuksel, Schaefer, Keyser 2011). Rückgabe: Punkte (k, 3) und
    Index des ersten Abtastpunkts jedes Segments (= Lage des Eingabepunkts)."""
    n = len(p)
    teile, start = [], []
    for i in range(n):
        p0, p1, p2, p3 = p[(i - 1) % n], p[i], p[(i + 1) % n], p[(i + 2) % n]
        t0 = 0.0
        t1 = t0 + max(np.linalg.norm(p1 - p0), 1e-6) ** 0.5
        t2 = t1 + max(np.linalg.norm(p2 - p1), 1e-6) ** 0.5
        t3 = t2 + max(np.linalg.norm(p3 - p2), 1e-6) ** 0.5
        k = max(2, int(math.ceil(np.linalg.norm(p2 - p1) / schritt_m)))
        tt = np.linspace(t1, t2, k, endpoint=False)[:, None]
        a1 = (t1 - tt) / (t1 - t0) * p0 + (tt - t0) / (t1 - t0) * p1
        a2 = (t2 - tt) / (t2 - t1) * p1 + (tt - t1) / (t2 - t1) * p2
        a3 = (t3 - tt) / (t3 - t2) * p2 + (tt - t2) / (t3 - t2) * p3
        b1 = (t2 - tt) / (t2 - t0) * a1 + (tt - t0) / (t2 - t0) * a2
        b2 = (t3 - tt) / (t3 - t1) * a2 + (tt - t1) / (t3 - t1) * a3
        start.append(sum(len(x) for x in teile))
        teile.append((t2 - tt) / (t2 - t1) * b1 + (tt - t1) / (t2 - t1) * b2)
    return np.vstack(teile), np.asarray(start)


def approximieren(punkte, sigma_m, schritt_m=1.0, knoten_m=4.0, basis="spline"):
    """Glättende Approximation einer geschlossenen Punktfolge (n, 3).

    1. Basiskurve gleichmäßig nach Bogenlänge abtasten (Schritt ``schritt_m``):
       ``basis="spline"`` (Standard) = der bisherige interpolierende kubische Spline, also die im Editor
       sichtbare Linie – er verteilt Richtungswechsel über große Lücken weich (global C2);
       ``basis="catmull_rom"`` = zentripetale Catmull-Rom-Kurve (lokal, kein Überschwingen, aber an
       Lücken mit Richtungsumkehr scharfe Knicke – Messung 24.10.2026, `docs/recherche_generierung.md`).
    2. Periodisch Gauß-glätten mit Sigma ``sigma_m`` in Metern: entfernt kleinräumige Unruhe aus
       unregelmäßig gesetzten Punkten (Schrumpfung in Kurven ≈ σ²/(2R)).
    3. Auf Knoten im Abstand ``knoten_m`` ausdünnen (danach interpolierender kubischer Spline).
    Rückgabe: (Knoten (m, 3), Splineparameter der Eingabepunkte; über ihre Lage auf der Basiskurve,
    keine Nächster-Punkt-Suche)."""
    p = np.asarray(punkte, dtype=float)
    keep = np.linalg.norm(np.roll(p, -1, axis=0) - p, axis=1) > 1e-9
    pk = p[keep]
    if len(pk) < 4:
        raise ValueError("Approximation braucht mindestens 4 verschiedene Punkte")
    if basis == "catmull_rom":
        c, start = _catmull_rom_zentripetal(pk, schritt_m / 2)
        seg = np.linalg.norm(np.roll(c, -1, axis=0) - c, axis=1)
        cum = np.concatenate([[0.0], np.cumsum(seg)])
        L = cum[-1]
        m = max(32, int(math.ceil(L / schritt_m)))
        s = np.arange(m) * (L / m)
        k = np.clip(np.searchsorted(cum, s, side="right") - 1, 0, len(c) - 1)
        u = (s - cum[k]) / np.maximum(seg[k], 1e-12)
        q = c[k] + (np.roll(c, -1, axis=0)[k] - c[k]) * u[:, None]
        s_eingabe = cum[start]
    else:
        sp = PeriodischerSpline(pk)
        L = sp.laenge
        m = max(32, int(math.ceil(L / schritt_m)))
        s = np.arange(m) * (L / m)
        q = sp.auswerten(sp.param_aus_s(s))
        s_eingabe = sp.bogenlaenge(sp.t[:-1])
    q = periodisch_gauss(q, sigma_m / (L / m))
    schritt = max(1, int(round(knoten_m / (L / m))))
    knoten = q[np.arange(0, m, schritt)]
    d = np.linalg.norm(np.roll(knoten, -1, axis=0) - knoten, axis=1)
    t = np.concatenate([[0.0], np.cumsum(d)])
    f = (s_eingabe / L * m) / schritt                     # Knotenindex (gebrochen) je Eingabepunkt
    eingabe = np.interp(f, np.arange(len(knoten) + 1), t)
    voll = np.zeros(len(p))
    voll[np.flatnonzero(keep)] = eingabe
    for i in np.flatnonzero(~keep):                       # doppelte Punkte: Parameter des Vorgängers
        voll[i] = voll[i - 1] if i > 0 else 0.0
    return knoten, voll


class PeriodischerSpline:
    """Geschlossener kubischer Interpolationsspline durch Punkte (n, d).

    Parameter = kumulierte Sehnenlänge. Ableitungen beziehen sich auf diesen Parameter.
    """

    def __init__(self, punkte, param=None):
        p = np.asarray(punkte, dtype=float)
        if p.ndim != 2 or len(p) < 3:
            raise ValueError("Spline braucht mindestens 3 Punkte")
        self.p = p
        n = len(p)
        if param is None:
            d = np.linalg.norm(np.roll(p, -1, axis=0) - p, axis=1)
            if np.any(d < 1e-9):
                raise ValueError("Doppelte aufeinanderfolgende Punkte in der Mittellinie")
            self.t = np.concatenate([[0.0], np.cumsum(d)])
        else:
            self.t = np.asarray(param, dtype=float)
        self.periode = self.t[-1]
        h = np.diff(self.t)                           # h_k: Punkt k -> k+1 (zyklisch)
        self.h = h
        hm = np.roll(h, 1)                            # h_{k-1}
        dy = (np.roll(p, -1, axis=0) - p) / h[:, None]
        rhs = 6.0 * (dy - np.roll(dy, 1, axis=0))
        self.m = zyklisch_tridiagonal(hm, 2.0 * (hm + h), h, rhs)
        self._tabelle()

    # ---------------------------------------------------------------- Auswertung
    def auswerten(self, tq, ableitung=0):
        tq = np.mod(np.atleast_1d(np.asarray(tq, dtype=float)), self.periode)
        k = np.clip(np.searchsorted(self.t, tq, side="right") - 1, 0, len(self.p) - 1)
        k1 = (k + 1) % len(self.p)
        h = self.h[k][:, None]
        a = (self.t[k + 1] - tq)[:, None]
        b = (tq - self.t[k])[:, None]
        m0, m1 = self.m[k], self.m[k1]
        y0, y1 = self.p[k], self.p[k1]
        if ableitung == 0:
            return ((m0 * a ** 3 + m1 * b ** 3) / (6 * h) + (y0 / h - m0 * h / 6) * a
                    + (y1 / h - m1 * h / 6) * b)
        if ableitung == 1:
            return ((-m0 * a ** 2 + m1 * b ** 2) / (2 * h) - (y0 / h - m0 * h / 6)
                    + (y1 / h - m1 * h / 6))
        if ableitung == 2:
            return (m0 * a + m1 * b) / h
        raise ValueError("ableitung 0, 1 oder 2")

    def _geschw(self, tq):
        return np.linalg.norm(self.auswerten(tq, 1), axis=1)

    # ---------------------------------------------------------------- Bogenlänge
    def _tabelle(self, max_schritt=0.5):
        # Je Abschnitt k: m_k Teilpunkte wie np.linspace(t_k, t_k+1, m_k, endpoint=False), vektorisiert.
        m = np.maximum(2, np.ceil(self.h / max_schritt).astype(int))
        k = np.repeat(np.arange(len(m)), m)
        j = np.arange(len(k)) - np.repeat(np.cumsum(m) - m, m)
        tau = np.concatenate([j * ((self.t[1:] - self.t[:-1]) / m)[k] + self.t[:-1][k], [self.periode]])
        a, b = tau[:-1], tau[1:]
        gx = a[:, None] + (b - a)[:, None] * _GL_X[None, :]
        v = self._geschw(gx.ravel()).reshape(gx.shape)
        teil = (v * _GL_W[None, :]).sum(axis=1) * (b - a)
        self._tau = tau
        self._s = np.concatenate([[0.0], np.cumsum(teil)])
        self.laenge = float(self._s[-1])

    def bogenlaenge(self, tq):
        """Bogenlänge vom Parameter 0 bis tq (tq in [0, Periode])."""
        tq = np.atleast_1d(np.asarray(tq, dtype=float))
        j = np.clip(np.searchsorted(self._tau, tq, side="right") - 1, 0, len(self._tau) - 2)
        a = self._tau[j]
        gx = a[:, None] + (tq - a)[:, None] * _GL_X[None, :]
        v = self._geschw(gx.ravel()).reshape(gx.shape)
        return self._s[j] + (v * _GL_W[None, :]).sum(axis=1) * (tq - a)

    def param_aus_s(self, sq):
        """Parameter zu Bogenlängen sq (periodisch), Genauigkeit << 1 mm."""
        sq = np.mod(np.atleast_1d(np.asarray(sq, dtype=float)), self.laenge)
        tq = np.interp(sq, self._s, self._tau)
        for _ in range(3):
            fehler = self.bogenlaenge(tq) - sq
            tq = np.clip(tq - fehler / self._geschw(tq), 0.0, self.periode)
        return tq


# =========================================================================== Mittellinie
class Stationen:
    """Werte der Mittellinie an Bogenlängen s (alle Felder als Arrays)."""

    def __init__(self, **kw):
        self.__dict__.update(kw)

    @property
    def anzahl(self):
        return len(self.s)

    def punkt(self, d):
        """Punkte im seitlichen Abstand d (n,) von der Mittellinie (d > 0 = links), (n, 3)."""
        d = np.asarray(d, dtype=float)
        p = self.pos.copy()
        p[:, 0] += d * self.nl[:, 0]
        p[:, 2] += d * self.nl[:, 1]
        return p


class Mittellinie:
    """Abgetastete Mittellinie ab der Start-Ziel-Linie (s = 0) in Fahrtrichtung."""

    def __init__(self, defn):
        P = defn.param
        mp = P["mittellinie"]
        self.defn = defn
        self.warnungen = list(defn.warnungen)
        pts = defn.punkte.copy()
        approx = float(mp.get("approximation_m") or 0.0)
        if approx > 0:
            # Glättende Approximation (Recherche 24.10.2026, TUM-Vorgehen): Punktdichte/Lücken bestimmen die Form nicht.
            knoten, eingabe_param = approximieren(pts, approx)
            self.spline = PeriodischerSpline(knoten)
            s_knoten = self.spline.bogenlaenge(eingabe_param)
        else:
            if mp.get("glaettung_m", 0) > 0:
                abst = np.mean(np.linalg.norm(np.roll(pts, -1, axis=0) - pts, axis=1))
                pts = periodisch_gauss(pts, mp["glaettung_m"] / abst)
            self.spline = PeriodischerSpline(pts)
            # Bogenlänge der Eingabepunkte (für Attribute)
            s_knoten = self.spline.bogenlaenge(self.spline.t[:-1])
        self.laenge = self.spline.laenge
        L = self.laenge
        self.s_eingabe = s_knoten          # Spline-Bogenlänge jedes Eingabepunkts (Editor, Attribute)

        # Start-Ziel-Linie
        sz = P["start_ziel"]
        if sz.get("punkt") is not None:
            px, pz = float(sz["punkt"][0]), float(sz["punkt"][1])
            fein = np.linspace(0, L, int(L) * 2, endpoint=False)
            fp = self.spline.auswerten(self.spline.param_aus_s(fein))
            self.s_start = float(fein[np.argmin((fp[:, 0] - px) ** 2 + (fp[:, 2] - pz) ** 2)])
        else:
            self.s_start = float(sz.get("s", 0.0)) % L

        n = max(16, int(round(L / mp["abtastung_m"])))
        self.ds = L / n
        self.s = np.arange(n) * self.ds
        s_e = np.mod(self.s + self.s_start, L)

        # ---- Attribute (Breiten, Querneigung) auf dem Bezugsgitter
        fb = P["fahrbahn"]
        wl0 = fb["breite_links"] if fb.get("breite_links") is not None else fb["breite"] / 2.0
        wr0 = fb["breite_rechts"] if fb.get("breite_rechts") is not None else fb["breite"] / 2.0
        basis = {"breite_links": float(wl0), "breite_rechts": float(wr0),
                 "querneigung_grad": float(fb.get("querneigung_grad", 0.0))}
        werte = {}
        ordnung = np.argsort(s_knoten)
        for name, b in basis.items():
            a = np.asarray(defn.attribute.get(name, np.full(len(s_knoten), np.nan)), dtype=float)
            gueltig = ~np.isnan(a)
            if defn.dicht:
                a = np.where(gueltig, a, b)
                werte[name] = periodisch_interp(s_e, s_knoten[ordnung], a[ordnung], L)
            elif gueltig.any():
                sk, av = s_knoten[gueltig], a[gueltig]
                o = np.argsort(sk)
                werte[name] = periodisch_interp(s_e, sk[o], av[o], L)
            else:
                werte[name] = np.full(n, b)
        for ab in fb.get("abschnitte", []) or []:
            von, bis = float(ab["von_s"]) % L, float(ab["bis_s"]) % L
            maske = (s_e >= von) & (s_e <= bis) if von <= bis else (s_e >= von) | (s_e <= bis)
            bl, br, qn = (np.nan, np.nan, np.nan)
            if ab.get("breite") is not None:
                bl = br = float(ab["breite"]) / 2.0
            if ab.get("breite_links") is not None:
                bl = float(ab["breite_links"])
            if ab.get("breite_rechts") is not None:
                br = float(ab["breite_rechts"])
            if ab.get("querneigung_grad") is not None:
                qn = float(ab["querneigung_grad"])
            for name, v in (("breite_links", bl), ("breite_rechts", br), ("querneigung_grad", qn)):
                if not np.isnan(v):
                    werte[name][maske] = v
        fenster = int(round(fb.get("uebergang_m", 0.0) / self.ds))
        for name in werte:
            werte[name] = periodisch_mittel(werte[name], fenster)
        self.wl = werte["breite_links"]
        self.wr = werte["breite_rechts"]
        self.bank = np.radians(werte["querneigung_grad"])
        if np.any(self.wl <= 0.5) or np.any(self.wr <= 0.5):
            raise ValueError("Fahrbahnbreite je Seite muss > 0,5 m sein")

        # ---- Höhe: Spline + gleitender Mittelwert
        tau = self.spline.param_aus_s(s_e)
        p = self.spline.auswerten(tau)
        fenster_h = int(round(mp.get("hoehen_glaettung_m", 0.0) / self.ds))
        self.y = periodisch_mittel(p[:, 1], fenster_h)
        self.steigung = (np.roll(self.y, -1) - np.roll(self.y, 1)) / (2 * self.ds)

        self.rand = None
        self.ref = self.stationen(self.s)
        self.kappa = self.ref.kappa
        P_k = P["kurven"]
        self.kappa_glatt = periodisch_mittel(self.kappa, int(round(P_k["glaettung_m"] / self.ds)))

    # ---------------------------------------------------------------- Auswertung
    def stationen(self, sq):
        """Alle Werte an Bogenlängen sq (ab Start-Ziel-Linie)."""
        sq = np.mod(np.asarray(sq, dtype=float), self.laenge)
        tau = self.spline.param_aus_s(np.mod(sq + self.s_start, self.laenge))
        p = self.spline.auswerten(tau)
        d1 = self.spline.auswerten(tau, 1)
        d2 = self.spline.auswerten(tau, 2)
        vx, vz = d1[:, 0], d1[:, 2]
        v2 = np.hypot(vx, vz)
        t = np.column_stack([vx / v2, vz / v2])
        kappa = (d2[:, 0] * vz - d2[:, 2] * vx) / v2 ** 3
        if hasattr(self, "y"):
            y = periodisch_interp(sq, self.s, self.y, self.laenge)
            steig = periodisch_interp(sq, self.s, self.steigung, self.laenge)
            wl = periodisch_interp(sq, self.s, self.wl, self.laenge)
            wr = periodisch_interp(sq, self.s, self.wr, self.laenge)
            bank = periodisch_interp(sq, self.s, self.bank, self.laenge)
        else:  # nur während der Initialisierung
            y, steig, wl, wr, bank = p[:, 1], np.zeros(len(sq)), None, None, None
        pos = np.column_stack([p[:, 0], y, p[:, 2]])
        t3 = np.column_stack([t[:, 0], steig, t[:, 1]])
        t3 /= np.linalg.norm(t3, axis=1)[:, None]
        return Stationen(s=sq, pos=pos, t=t, t3=t3, nl=np.column_stack([t[:, 1], -t[:, 0]]),
                         kappa=kappa, richtung=np.unwrap(np.arctan2(-t[:, 1], t[:, 0])),
                         steigung=steig, wl=wl, wr=wr, bank=bank)

    def hoehe_quer(self, st, d):
        """Höhe im seitlichen Abstand d: innerhalb der Fahrbahn die Querneigung, außerhalb waagerecht oder mit dem
        Randwinkel aus ``rand_setzen``."""
        d = np.asarray(d, dtype=float)
        dd = np.clip(d, -st.wr, st.wl)
        h = st.pos[:, 1] + dd * np.tan(st.bank)
        if self.rand is not None:
            links = periodisch_interp(st.s, self.s, self.rand[0], self.laenge)
            rechts = periodisch_interp(st.s, self.s, self.rand[1], self.laenge)
            h = h + np.clip(d - st.wl, 0.0, self.rand_breite) * links + np.clip(-st.wr - d, 0.0, self.rand_breite) * rechts
        return h

    def rand_setzen(self, links, rechts, breite):
        """Randwinkel je Station: Höhenänderung je Meter nach außen (positiv = steigt), links und rechts; wirkt auf
        ``breite`` Meter neben der Fahrbahn, danach geht es waagerecht weiter."""
        self.rand = (np.asarray(links, dtype=float), np.asarray(rechts, dtype=float))
        self.rand_breite = float(breite)

    @property
    def anzahl(self):
        return len(self.s)

    def laufrichtung(self):
        """'gegen_uhrzeigersinn' oder 'im_uhrzeigersinn' (Draufsicht)."""
        drehung = float(np.sum(self.kappa) * self.ds)
        return "gegen_uhrzeigersinn" if drehung > 0 else "im_uhrzeigersinn"


# =========================================================================== Prüfungen
def _segmente_schneiden(a1, a2, b1, b2):
    """Echte Schnitte von Strecken a1a2 und b1b2 (vektorisiert). Rückgabe Maske, Parameter u."""
    def kreuz(u, v):
        return u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]
    r = a2 - a1
    q = b2 - b1
    nenner = kreuz(r, q)
    ab = b1 - a1
    with np.errstate(divide="ignore", invalid="ignore"):
        t = kreuz(ab, q) / nenner
        u = kreuz(ab, r) / nenner
    ok = (np.abs(nenner) > 1e-12) & (t > 1e-9) & (t < 1 - 1e-9) & (u > 1e-9) & (u < 1 - 1e-9)
    return ok, t


def polylinien_schnitte(linien, nachbarn=2, zelle=None):
    """Sucht Schnittpunkte in einer Menge geschlossener Polylinien (je (n, 2)).

    Segmente derselben Linie mit Indexabstand <= ``nachbarn`` werden nicht verglichen.
    Rückgabe: Liste von Dicts {linie_a, index_a, linie_b, index_b, punkt}.
    """
    A, B, eigner, idx, laengen = [], [], [], [], []
    for li, p in enumerate(linien):
        p = np.asarray(p, dtype=float)
        A.append(p)
        B.append(np.roll(p, -1, axis=0))
        eigner.append(np.full(len(p), li))
        idx.append(np.arange(len(p)))
        laengen.append(len(p))
    A = np.vstack(A)
    B = np.vstack(B)
    eigner = np.concatenate(eigner)
    idx = np.concatenate(idx)
    seglaenge = np.linalg.norm(B - A, axis=1)
    if zelle is None:
        zelle = max(5.0, 2.0 * float(np.max(seglaenge)))
    lo = np.floor(np.minimum(A, B) / zelle).astype(np.int64)
    hi = np.floor(np.maximum(A, B) / zelle).astype(np.int64)
    sid = np.arange(len(A))
    schluessel, seg = [], []
    for dx in (0, 1):
        for dz in (0, 1):
            cx = lo[:, 0] + dx
            cz = lo[:, 1] + dz
            m = (cx <= hi[:, 0]) & (cz <= hi[:, 1])
            schluessel.append(cx[m] * 1_000_003 + cz[m])
            seg.append(sid[m])
    schluessel = np.concatenate(schluessel)
    seg = np.concatenate(seg)
    o = np.argsort(schluessel, kind="stable")
    schluessel, seg = schluessel[o], seg[o]
    grenzen = np.flatnonzero(np.diff(schluessel)) + 1
    gruppen = np.split(seg, grenzen)
    pi, pj = [], []
    for g in gruppen:
        if len(g) < 2:
            continue
        i, j = np.triu_indices(len(g), 1)
        pi.append(g[i])
        pj.append(g[j])
    if not pi:
        return []
    pi = np.concatenate(pi)
    pj = np.concatenate(pj)
    a = np.minimum(pi, pj)
    b = np.maximum(pi, pj)
    paare = np.unique(a * len(A) + b)
    pi, pj = paare // len(A), paare % len(A)
    gleiche = eigner[pi] == eigner[pj]
    n_l = np.array(laengen)[eigner[pi]]
    abst = np.abs(idx[pi] - idx[pj])
    abst = np.minimum(abst, n_l - abst)
    behalten = ~(gleiche & (abst <= nachbarn))
    pi, pj = pi[behalten], pj[behalten]
    ok, t = _segmente_schneiden(A[pi], B[pi], A[pj], B[pj])
    erg = []
    for i, j, tt in zip(pi[ok], pj[ok], t[ok]):
        pkt = A[i] + tt * (B[i] - A[i])
        erg.append({"linie_a": int(eigner[i]), "index_a": int(idx[i]),
                    "linie_b": int(eigner[j]), "index_b": int(idx[j]),
                    "punkt": [float(pkt[0]), float(pkt[1])]})
    return erg


def raender_pruefen(ml, abstand_links=None, abstand_rechts=None, name="fahrbahnraender"):
    """Prüft Fahrbahnränder (oder Linien im Abstand wl+abstand) auf Faltung und Schnitte.

    Rückgabe: Dict mit ok, faltungen_links/rechts, schnitte (Liste), min_radius_m.
    """
    st = ml.ref
    dl = st.wl + (0.0 if abstand_links is None else abstand_links)
    dr = st.wr + (0.0 if abstand_rechts is None else abstand_rechts)
    links = st.punkt(dl)[:, [0, 2]]
    rechts = st.punkt(-dr)[:, [0, 2]]
    k = st.kappa
    fl = np.flatnonzero(k * dl >= 1.0)
    fr = np.flatnonzero(-k * dr >= 1.0)
    schnitte = polylinien_schnitte([links, rechts], nachbarn=2)
    for sch in schnitte:
        sch["s_a"] = float(st.s[sch["index_a"]])
        sch["s_b"] = float(st.s[sch["index_b"]])
        sch["linie_a"] = ("links", "rechts")[sch["linie_a"]]
        sch["linie_b"] = ("links", "rechts")[sch["linie_b"]]
    return {
        "name": name,
        "ok": bool(len(fl) == 0 and len(fr) == 0 and len(schnitte) == 0),
        "faltungen_links": int(len(fl)),
        "faltungen_rechts": int(len(fr)),
        "faltung_s": [float(st.s[i]) for i in np.concatenate([fl, fr])[:20]],
        "schnitte": schnitte[:50],
        "anzahl_schnitte": len(schnitte),
        "min_radius_m": float(1.0 / max(np.max(np.abs(k)), 1e-12)),
    }


def freiraum(ml, max_abstand=200.0, fenster_m=30.0):
    """Abstand (entlang der Normalen) von der Mittellinie bis zu einem anderen Streckenteil.

    Rückgabe: (links, rechts, breite_gegenueber_links, breite_gegenueber_rechts) – jeweils (n,),
    np.inf, wenn bis ``max_abstand`` nichts getroffen wird. Stützstellen mit einem
    Bogenabstand < ``fenster_m`` zum Strahlursprung werden ignoriert.
    """
    st = ml.ref
    n = st.anzahl
    c = st.pos[:, [0, 2]]
    a = c
    b = np.roll(c, -1, axis=0)
    halb = 0.5 * (st.wl + st.wr)
    halb_seg = 0.5 * (halb + np.roll(halb, -1))
    fenster = int(math.ceil(fenster_m / ml.ds))
    erg = []
    for seite in (1.0, -1.0):
        dist = np.full(n, np.inf)
        gegen = np.zeros(n)
        richt = seite * st.nl
        block = 200
        for i0 in range(0, n, block):
            i1 = min(n, i0 + block)
            o = c[i0:i1]
            lo = o.min(axis=0) - max_abstand
            hi = o.max(axis=0) + max_abstand
            kand = np.flatnonzero(np.all((np.maximum(a, b) >= lo) & (np.minimum(a, b) <= hi), axis=1))
            if len(kand) == 0:
                continue
            ri = np.arange(i0, i1)
            O = o[:, None, :]
            D = richt[i0:i1][:, None, :]
            A = a[kand][None, :, :]
            E = (b[kand] - a[kand])[None, :, :]
            nenner = D[..., 0] * E[..., 1] - D[..., 1] * E[..., 0]
            AO = A - O
            with np.errstate(divide="ignore", invalid="ignore"):
                t = (AO[..., 0] * E[..., 1] - AO[..., 1] * E[..., 0]) / nenner
                u = (AO[..., 0] * D[..., 1] - AO[..., 1] * D[..., 0]) / nenner
            abst = np.abs(ri[:, None] - kand[None, :])
            abst = np.minimum(abst, n - abst)
            gueltig = (np.abs(nenner) > 1e-12) & (t > 0) & (t <= max_abstand) & (u >= 0) & (u <= 1) \
                & (abst > fenster)
            t = np.where(gueltig, t, np.inf)
            j = np.argmin(t, axis=1)
            tmin = t[np.arange(len(ri)), j]
            dist[i0:i1] = tmin
            gegen[i0:i1] = np.where(np.isfinite(tmin), halb_seg[kand[j]], 0.0)
        erg.append((dist, gegen))
    return erg[0][0], erg[1][0], erg[0][1], erg[1][1]


# =========================================================================== Kurven
def kurven_finden(ml, schwelle=1.0 / 500.0, mindestwinkel_grad=15.0, zusammenfassen_m=25.0):
    """Erkennt Kurven aus der geglätteten Krümmung.

    Eine Kurve ist ein zusammenhängender Bereich mit |Krümmung| > schwelle und gleichem
    Vorzeichen; gleichsinnige Bereiche mit kürzerer Lücke werden zusammengefasst.
    Rückgabe: Liste von Dicts (Indizes können > n sein, wenn eine Kurve über s = 0 reicht).
    """
    k = ml.kappa_glatt
    n = len(k)
    bereiche = []
    for vz in (1.0, -1.0):
        for a, e in laeufe(vz * k > schwelle):
            bereiche.append([a, e, vz])
    bereiche.sort(key=lambda b: b[0])
    # gleichsinnige Bereiche mit kleiner Lücke zusammenfassen (auch über das Ende hinweg)
    luecke = int(round(zusammenfassen_m / ml.ds))
    geaendert = True
    while geaendert and len(bereiche) > 1:
        geaendert = False
        for i in range(len(bereiche)):
            j = (i + 1) % len(bereiche)
            a, b = bereiche[i], bereiche[j]
            if a[2] != b[2] or a is b:
                continue
            start_b = b[0] if j > i else b[0] + n
            if 0 <= start_b - a[1] <= luecke:
                # keine gegensinnige Kurve dazwischen (sonst Schikane)
                a[1] = max(a[1], b[1] if j > i else b[1] + n)
                bereiche.pop(j)
                if j < i:
                    # Anfang wurde über das Ende verschoben: Indizes normieren
                    pass
                geaendert = True
                break
    kurven = []
    for a, e, vz in bereiche:
        idx = np.arange(a, e) % n
        winkel = float(np.degrees(np.sum(ml.kappa[idx]) * ml.ds))
        if abs(winkel) < mindestwinkel_grad:
            continue
        i_max = int(idx[np.argmax(np.abs(k[idx]))])
        kurven.append({
            "i_anfang": int(a % n), "i_ende": int((e - 1) % n), "i_scheitel": i_max,
            "laenge_idx": int(e - a),
            "richtung": "links" if vz > 0 else "rechts",
            "winkel_grad": winkel,
            "min_radius_m": float(1.0 / max(np.max(np.abs(ml.kappa[idx])), 1e-12)),
        })
    kurven.sort(key=lambda kv: kv["i_anfang"])
    return kurven
