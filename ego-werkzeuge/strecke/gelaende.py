"""Adaptive Grundfläche (Schritt E): eingeschränkte Delaunay-Triangulierung, nur NumPy + Python.

Statt eines festen 8-m-Rasters unter der Strecke wird die Grundfläche als Netz erzeugt, das

* genau an den äußersten Streckenrändern anschließt (Zwangskanten = Randkanten des verschweißten
  Streckennetzes, deren Außenseite in der Draufsicht frei ist; dieselben Vertices, gleiche Höhen,
  keine Lücken und keine T-Stöße),
* am Rand des 2048-m-Lochs genau die Innenrand-Vertices des Außenterrains verwendet (16 m Abstand),
* nahe der Strecke fein (``h_min``) und mit dem Abstand gröber wird (bis ``h_max``), gesteuert über
  einen Quadtree mit größenbegrenzender Funktion h(p) = max(h_min, d(p) / abstufung),
* Höhen harmonisch (Laplace, Kotangentengewichte ≥ 0, CG-Verfahren) zwischen Strecken- und Lochrand
  interpoliert.

Verfahren: Quadtree-Eckpunkte + Rand-/Streckenpunkte → Delaunay (inkrementell, Lawson-Flips) →
Zwangskanten per Kantenflip einarbeiten (Sloan 1993) → Delaunay-Eigenschaft wiederherstellen →
Gebiete per Paritätsfüllung (außerhalb Loch / Strecke entfernen). Deterministisch, ohne SciPy/Triangle.

Koordinaten: Spielwelt (x, y, z), y oben; die Triangulierung arbeitet in (x, z) relativ zur Lochecke.
"""

import math
import time
from collections import deque
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np


class GelaendeFehler(ValueError):
    pass


# ============================================================================ Streckenrand
def _punkt_in_dreiecken(pkt: np.ndarray, T2: np.ndarray, zelle: float = 8.0) -> np.ndarray:
    """Für jeden Punkt (x, z): liegt er (streng) in einem der 2D-Dreiecke T2 (n, 3, 2)?"""
    lo = np.floor(T2.min(1) / zelle).astype(np.int64)
    hi = np.floor(T2.max(1) / zelle).astype(np.int64)
    span = hi - lo + 1
    paare_c, paare_t = [], []
    for di in range(int(span[:, 0].max(initial=1))):
        for dj in range(int(span[:, 1].max(initial=1))):
            m = np.flatnonzero((di < span[:, 0]) & (dj < span[:, 1]))
            paare_c.append((lo[m, 0] + di) * 1000003 + (lo[m, 1] + dj))
            paare_t.append(m)
    c = np.concatenate(paare_c)
    t = np.concatenate(paare_t)
    o = np.argsort(c, kind='stable')
    c, t = c[o], t[o]
    pc = np.floor(pkt / zelle).astype(np.int64)
    pk = pc[:, 0] * 1000003 + pc[:, 1]
    a = np.searchsorted(c, pk, 'left')
    b = np.searchsorted(c, pk, 'right')
    n = b - a
    pid = np.repeat(np.arange(len(pkt)), n)
    if not len(pid):
        return np.zeros(len(pkt), bool)
    off = np.arange(len(pid)) - np.repeat(np.cumsum(n) - n, n)
    tid = t[np.repeat(a, n) + off]
    A, B, C = T2[tid, 0], T2[tid, 1], T2[tid, 2]
    P = pkt[pid]

    def o2(u, v, w):
        return (v[:, 0] - u[:, 0]) * (w[:, 1] - u[:, 1]) - (v[:, 1] - u[:, 1]) * (w[:, 0] - u[:, 0])

    d = o2(A, B, C)
    s = np.sign(d)
    innen = (s * o2(A, B, P) > 1e-12) & (s * o2(B, C, P) > 1e-12) & (s * o2(C, A, P) > 1e-12) & (s != 0)
    erg = np.zeros(len(pkt), bool)
    erg[pid[innen]] = True
    return erg


def streckenrand(pos: np.ndarray, tri: np.ndarray, raster: float = 0.001, versatz: float = 0.05):
    """Äußerste Ränder der Strecke: Randkanten des (1 mm) verschweißten Netzes, deren eine Seite
    in der Draufsicht von keinem Streckendreieck überdeckt ist.

    Rückgabe: P (k, 3) Positionen (je verschweißtem Vertex die Position eines Originalvertex),
    schleifen (Liste von Indexfolgen in P, geschlossen), info.
    """
    pos = np.asarray(pos, np.float64)
    tri = np.asarray(tri, np.int64)
    key = np.round(pos / raster).astype(np.int64)
    _, erst, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    inv = inv.ravel()
    P = pos[erst]
    tt = inv[tri]
    tt = tt[(tt[:, 0] != tt[:, 1]) & (tt[:, 1] != tt[:, 2]) & (tt[:, 0] != tt[:, 2])]
    e = np.sort(np.concatenate([tt[:, [0, 1]], tt[:, [1, 2]], tt[:, [2, 0]]]), axis=1)
    u, cnt = np.unique(e, axis=0, return_counts=True)
    rand = u[cnt == 1]
    # Überdeckung in der Draufsicht: nur nicht senkrechte Dreiecke
    fn = np.cross(P[tt[:, 1]] - P[tt[:, 0]], P[tt[:, 2]] - P[tt[:, 0]])
    flach = np.abs(fn[:, 1]) > 1e-6
    T2 = P[tt[flach]][:, :, (0, 2)]
    a2, b2 = P[rand[:, 0]][:, (0, 2)], P[rand[:, 1]][:, (0, 2)]
    d = b2 - a2
    lang = np.linalg.norm(d, axis=1)
    ok = lang > 1e-6
    nrm = np.zeros_like(d)
    nrm[ok] = np.c_[-d[ok, 1], d[ok, 0]] / lang[ok, None]
    mitte = 0.5 * (a2 + b2)
    links = _punkt_in_dreiecken(mitte + versatz * nrm, T2)
    rechts = _punkt_in_dreiecken(mitte - versatz * nrm, T2)
    aussen = ok & (links != rechts)
    kanten = rand[aussen]
    # Schleifen zusammensetzen (Grad 2 je Vertex verlangt)
    nb: Dict[int, List[int]] = {}
    for a, b in kanten.tolist():
        nb.setdefault(a, []).append(b)
        nb.setdefault(b, []).append(a)
    falsch = [k for k, v in nb.items() if len(v) != 2]
    if falsch:
        raise GelaendeFehler('Track edge ambiguous: %d vertices with degree != 2 (e.g. at %s)'
                             % (len(falsch), np.round(P[falsch[0]], 2).tolist()))
    gesehen, schleifen = set(), []
    for s in sorted(nb):
        if s in gesehen:
            continue
        sch, vor, cur = [s], -1, s
        gesehen.add(s)
        while True:
            n0, n1 = nb[cur]
            n = n1 if n0 == vor else n0
            if n == s:
                break
            if n in gesehen:
                raise GelaendeFehler('Track edge: loop not closed')
            sch.append(n)
            gesehen.add(n)
            vor, cur = cur, n
        if len(sch) >= 3:
            schleifen.append(np.array(sch, np.int64))
    info = dict(randkanten=int(len(rand)), aussenkanten=int(len(kanten)), schleifen=len(schleifen),
                schleifen_punkte=[int(len(s)) for s in schleifen])
    return P, schleifen, info


# ============================================================================ Delaunay
class _CDT:
    """Inkrementelle Delaunay-Triangulierung (Lawson) mit Zwangskanten (Sloan).

    Dreieck t: Vertices V[3t..3t+2] gegen den Uhrzeigersinn (x, z), N[3t+i] = Nachbar gegenüber
    Vertex i (Kante V[3t+i+1], V[3t+i+2]), −1 = keiner. Vertices sind Indizes in x/y.
    """

    def __init__(self, x: List[float], y: List[float]):
        self.x, self.y = x, y
        n = len(x)
        mx, my = (min(x) + max(x)) / 2, (min(y) + max(y)) / 2
        r = max(max(x) - min(x), max(y) - min(y)) * 3 + 100
        self.x += [mx - r, mx + r, mx]
        self.y += [my - r, my - r, my + r]
        self.sv = (n, n + 1, n + 2)
        self.V = [n, n + 1, n + 2]
        self.N = [-1, -1, -1]
        self.vt = [0] * (n + 3)
        self.zwang = set()
        self.letztes = 0
        self.flips = 0

    # --- Prädikate
    def orient(self, a, b, c):
        x, y = self.x, self.y
        return (x[b] - x[a]) * (y[c] - y[a]) - (y[b] - y[a]) * (x[c] - x[a])

    def inkreis(self, a, b, c, d):
        x, y = self.x, self.y
        adx, ady = x[a] - x[d], y[a] - y[d]
        bdx, bdy = x[b] - x[d], y[b] - y[d]
        cdx, cdy = x[c] - x[d], y[c] - y[d]
        la, lb, lc = adx * adx + ady * ady, bdx * bdx + bdy * bdy, cdx * cdx + cdy * cdy
        det = la * (bdx * cdy - cdx * bdy) - lb * (adx * cdy - cdx * ady) + lc * (adx * bdy - bdx * ady)
        m = max(la, lb, lc)
        return det - 1e-12 * m * m          # relativ kleine Werte gelten als kozirkular (kein Kippen)

    # --- Hilfen
    def _setze(self, t, a, b, c, na, nb, nc):
        V, N = self.V, self.N
        i = 3 * t
        V[i], V[i + 1], V[i + 2] = a, b, c
        N[i], N[i + 1], N[i + 2] = na, nb, nc
        self.vt[a] = self.vt[b] = self.vt[c] = t

    def _neu(self):
        t = len(self.V) // 3
        self.V += [-1, -1, -1]
        self.N += [-1, -1, -1]
        return t

    def _ersetze(self, u, alt, neu):
        if u < 0:
            return
        N = self.N
        i = 3 * u
        if N[i] == alt:
            N[i] = neu
        elif N[i + 1] == alt:
            N[i + 1] = neu
        elif N[i + 2] == alt:
            N[i + 2] = neu
        else:
            raise GelaendeFehler('Triangulation inconsistent (neighbour)')

    # --- Punktsuche
    def suchen(self, p):
        V, N = self.V, self.N
        t = self.letztes
        schritte = 0
        start = 0
        while True:
            schritte += 1
            if schritte > 10 * len(V) + 100:
                raise GelaendeFehler('Point search does not converge')
            i = 3 * t
            weiter = -1
            for k0 in range(3):
                k = (k0 + start) % 3
                a, b = V[i + (k + 1) % 3], V[i + (k + 2) % 3]
                if self.orient(a, b, p) < 0:
                    weiter = N[i + k]
                    break
            start = (start + 1) % 3
            if weiter < 0:
                return t
            t = weiter

    # --- Einfügen
    def einfuegen(self, p):
        t = self.suchen(p)
        V = self.V
        i = 3 * t
        a, b, c = V[i], V[i + 1], V[i + 2]
        x, y = self.x, self.y
        for q in (a, b, c):
            if abs(x[q] - x[p]) < 1e-9 and abs(y[q] - y[p]) < 1e-9:
                return False                                      # doppelt
        oa, ob, oc = self.orient(b, c, p), self.orient(c, a, p), self.orient(a, b, p)
        stapel = []
        if oa == 0 or ob == 0 or oc == 0:
            k = 0 if oa == 0 else 1 if ob == 0 else 2
            self._kante_teilen(t, k, p, stapel)
        else:
            self._dreieck_teilen(t, p, stapel)
        self._legalisieren(stapel)
        return True

    def _dreieck_teilen(self, t, p, stapel):
        V, N = self.V, self.N
        i = 3 * t
        a, b, c = V[i], V[i + 1], V[i + 2]
        na, nb, nc = N[i], N[i + 1], N[i + 2]
        t1, t2 = self._neu(), self._neu()
        self._setze(t, a, b, p, t1, t2, nc)
        self._setze(t1, b, c, p, t2, t, na)
        self._setze(t2, c, a, p, t, t1, nb)
        self._ersetze(na, t, t1)
        self._ersetze(nb, t, t2)
        stapel += [(t, 2), (t1, 2), (t2, 2)]
        self.letztes = t

    def _kante_teilen(self, t, k, p, stapel):
        (a, b, c), (_, nb, nc) = self._gedreht_k(t, k)
        u = self.N[3 * t + k]
        if u < 0:
            raise GelaendeFehler('Point on the hull boundary')
        (d, c2, b2), (_, ub, uc) = self._gedreht(u, None, t)
        t2, t4 = self._neu(), self._neu()
        T1, T3 = t, u
        self._setze(T1, a, b, p, t4, t2, nc)
        self._setze(t2, a, p, c, T3, nb, T1)
        self._setze(T3, d, c, p, t2, t4, uc)
        self._setze(t4, d, p, b, T1, ub, T3)
        self._ersetze(nb, t, t2)
        self._ersetze(nc, t, T1)
        self._ersetze(uc, u, T3)
        self._ersetze(ub, u, t4)
        stapel += [(T1, 2), (t2, 1), (T3, 2), (t4, 1)]
        self.letztes = t

    def _gedreht_k(self, t, k):
        V, N = self.V, self.N
        i = 3 * t
        return ([V[i + k], V[i + (k + 1) % 3], V[i + (k + 2) % 3]],
                [N[i + k], N[i + (k + 1) % 3], N[i + (k + 2) % 3]])

    def _gedreht(self, t, v, nachbar=None):
        """Vertices/Nachbarn von t gedreht: v an Stelle 0 bzw. (nachbar gegeben) der Vertex gegenüber nachbar."""
        V, N = self.V, self.N
        i = 3 * t
        if nachbar is not None:
            k = 0 if N[i] == nachbar else 1 if N[i + 1] == nachbar else 2
            if N[i + k] != nachbar:
                raise GelaendeFehler('Triangulation inconsistent (rotation)')
        else:
            k = 0 if V[i] == v else 1 if V[i + 1] == v else 2
        return self._gedreht_k(t, k)

    def _flip(self, t, k):
        """Kante gegenüber Vertex k von t kippen. Rückgabe (t, u) mit neuen Dreiecken
        t = (p, b, d), u = (p, d, c) (p = V[t,k]) oder None, falls nicht konvex."""
        (p, b, c), (u, tb, tc) = self._gedreht_k(t, k)
        (d, c2, b2), (_, uc, ub) = self._gedreht(u, None, t)
        if self.orient(p, b, d) <= 0 or self.orient(p, d, c) <= 0:
            return None
        self._setze(t, p, b, d, uc, u, tc)
        self._setze(u, p, d, c, ub, tb, t)
        self._ersetze(uc, u, t)
        self._ersetze(tb, t, u)
        self.flips += 1
        return t, u

    def _legalisieren(self, stapel):
        V, N = self.V, self.N
        zwang = self.zwang
        grenze = self.flips + 100000
        while stapel:
            if self.flips > grenze:
                raise GelaendeFehler('Legalisation does not converge')
            t, k = stapel.pop()
            i = 3 * t
            u = N[i + k]
            if u < 0:
                continue
            p, b, c = V[i + k], V[i + (k + 1) % 3], V[i + (k + 2) % 3]
            if zwang and (min(b, c), max(b, c)) in zwang:
                continue
            j = 3 * u
            d = V[j] if N[j] == t else V[j + 1] if N[j + 1] == t else V[j + 2]
            if self.inkreis(p, b, c, d) > 0:
                r = self._flip(t, k)
                if r is not None:
                    stapel.append((r[0], 0))
                    stapel.append((r[1], 0))

    # --- Zwangskanten
    def _hat_kante(self, a, b):
        return self._dreieck_mit_kante(a, b) is not None

    def _faecher(self, a):
        """Alle Dreiecke um Vertex a (gegen den Uhrzeigersinn)."""
        t0 = self.vt[a]
        V, N = self.V, self.N
        if a not in V[3 * t0:3 * t0 + 3]:
            raise GelaendeFehler('Vertex-triangle mapping out of date')
        erg, t = [], t0
        while True:
            erg.append(t)
            (_, v1, v2), (_, n1, n2) = self._gedreht(t, a)
            t = n1                     # über Kante (v2, a) – gegenüber v1
            if t < 0:
                raise GelaendeFehler('Vertex on the hull boundary')
            if t == t0 or len(erg) > 10000:
                return erg

    def _dreieck_mit_kante(self, a, b):
        for t in self._faecher(a):
            i = 3 * t
            if b in self.V[i:i + 3]:
                return t
        return None

    def zwangskante(self, a, b):
        if a == b:
            return 0
        if self._hat_kante(a, b):
            self.zwang.add((min(a, b), max(a, b)))
            return 0
        # Startdreieck: Strahl a→b verlässt a durch die Gegenkante
        start = None
        for t in self._faecher(a):
            (_, v1, v2), _ = self._gedreht(t, a)
            o1, o2 = self.orient(a, v1, b), self.orient(a, v2, b)
            if o1 > 0 and o2 < 0:
                start = (t, v1, v2)
                break
            for v, o in ((v1, o1), (v2, o2)):
                if o == 0 and (self.x[v] - self.x[a]) * (self.x[b] - self.x[a]) + \
                        (self.y[v] - self.y[a]) * (self.y[b] - self.y[a]) > 0:
                    raise GelaendeFehler('Point lies on a constraint edge (does the track edge touch itself?)')
        if start is None:
            raise GelaendeFehler('Constraint edge cannot be found')
        # schneidende Kanten sammeln
        kanten = deque()
        t, v1, v2 = start
        while True:
            if (min(v1, v2), max(v1, v2)) in self.zwang:
                raise GelaendeFehler('Constraint edges intersect (does the track edge overlap itself?)')
            kanten.append((v1, v2))
            u = self._nachbar_ueber(t, v1, v2)
            j = 3 * u
            w = [q for q in self.V[j:j + 3] if q != v1 and q != v2][0]
            if w == b:
                break
            o = self.orient(a, b, w)
            if o == 0:
                raise GelaendeFehler('Point lies on a constraint edge (does the track edge touch itself?)')
            # v1 links von a→b (orient(a,b,v1) > 0)? Dann ersetzt w die Seite mit gleichem Vorzeichen
            if (o > 0) == (self.orient(a, b, v1) > 0):
                v1 = w
            else:
                v2 = w
            t = u
        neue = []
        runden = 0
        while kanten:
            runden += 1
            if runden > 100000:
                raise GelaendeFehler('Constraint edge: flipping does not converge')
            v1, v2 = kanten.popleft()
            t = self._dreieck_mit_kante(v1, v2)
            k = [q for q in range(3) if self.V[3 * t + q] not in (v1, v2)][0]
            r = self._flip(t, k)
            if r is None:
                kanten.append((v1, v2))
                continue
            p = self.V[3 * r[0]]
            d = self.V[3 * r[0] + 2]
            if self._schneidet(a, b, p, d):
                kanten.append((p, d))
            else:
                neue.append((p, d))
        self.zwang.add((min(a, b), max(a, b)))
        return len(neue) + 1

    def _nachbar_ueber(self, t, v1, v2):
        i = 3 * t
        for k in range(3):
            if self.V[i + k] not in (v1, v2):
                return self.N[i + k]
        raise GelaendeFehler('Edge not in triangle')

    def _schneidet(self, a, b, p, d):
        if p in (a, b) or d in (a, b):
            return False
        o1, o2 = self.orient(a, b, p), self.orient(a, b, d)
        o3, o4 = self.orient(p, d, a), self.orient(p, d, b)
        return (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0) and o1 != 0 and o2 != 0

    def delaunay_wiederherstellen(self, max_runden=50):
        """Lawson-Durchläufe über alle Kanten, Zwangskanten bleiben."""
        V, N = self.V, self.N
        for runde in range(max_runden):
            gekippt = 0
            for t in range(len(V) // 3):
                for k in range(3):
                    i = 3 * t
                    u = N[i + k]
                    if u < t:
                        continue
                    p, b, c = V[i + k], V[i + (k + 1) % 3], V[i + (k + 2) % 3]
                    if (min(b, c), max(b, c)) in self.zwang:
                        continue
                    j = 3 * u
                    d = V[j] if N[j] == t else V[j + 1] if N[j + 1] == t else V[j + 2]
                    if self.inkreis(p, b, c, d) > 0 and self._flip(t, k) is not None:
                        gekippt += 1
            if not gekippt:
                return runde
        return max_runden

    def gebiete(self):
        """Parität je Dreieck: 0 = außerhalb (Superdreieck), +1 je überquerter Zwangskante."""
        V, N = self.V, self.N
        nt = len(V) // 3
        par = [-1] * nt
        sv = set(self.sv)
        start = next(t for t in range(nt) if sv & set(V[3 * t:3 * t + 3]))
        par[start] = 0
        q = deque([start])
        while q:
            t = q.popleft()
            i = 3 * t
            for k in range(3):
                u = N[i + k]
                if u < 0:
                    continue
                b, c = V[i + (k + 1) % 3], V[i + (k + 2) % 3]
                w = par[t] + (1 if (min(b, c), max(b, c)) in self.zwang else 0)
                if par[u] < 0:
                    par[u] = w
                    q.append(u)
                elif (par[u] - w) % 2:
                    raise GelaendeFehler('Region parity contradictory (open chain of constraint edges)')
        return np.array(par), np.array(V, np.int64).reshape(-1, 3)


# ============================================================================ Quadtree
def _zeilenkreuzungen(segs: np.ndarray, zeilen_z: float):
    """Kreuzungen der Segmente (n, 2, 2) mit den Geraden z = k + zeilen_z (k ganzzahlig).
    Halboffene Regel. Rückgabe Zeile k und x."""
    za, zb = segs[:, 0, 1], segs[:, 1, 1]
    lo, hi = np.minimum(za, zb), np.maximum(za, zb)
    k0 = np.ceil(lo - zeilen_z).astype(np.int64)
    k1 = np.ceil(hi - zeilen_z).astype(np.int64) - 1           # k + z0 < hi
    n = np.maximum(k1 - k0 + 1, 0)
    sid = np.repeat(np.arange(len(segs)), n)
    k = np.repeat(k0, n) + (np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n))
    zz = k + zeilen_z
    a, b = segs[sid, 0], segs[sid, 1]
    f = (zz - a[:, 1]) / (b[:, 1] - a[:, 1])
    return k, a[:, 0] + f * (b[:, 0] - a[:, 0])


def _innen(segs, px, pz_ganz):
    """Even-odd: Punkte (px, pz) mit ganzzahligem pz innerhalb der geschlossenen Polygone."""
    k, xc = _zeilenkreuzungen(segs, 0.0)
    schl = np.sort(k * 8192.0 + xc)
    q = pz_ganz * 8192.0
    anzahl = np.searchsorted(schl, q + px) - np.searchsorted(schl, q - 4096.0)
    return (anzahl % 2) == 1


def _prefix(gitter):
    s = np.zeros((gitter.shape[0] + 1, gitter.shape[1] + 1), np.int64)
    s[1:, 1:] = gitter.cumsum(0).cumsum(1)
    return s


def _summe(S, i0, i1, j0, j1):
    """Summe über Pixel [i0, i1) × [j0, j1) (x-Index i, z-Index j), geklammert."""
    nx, nz = S.shape[1] - 1, S.shape[0] - 1
    i0, i1 = np.clip(i0, 0, nx), np.clip(i1, 0, nx)
    j0, j1 = np.clip(j0, 0, nz), np.clip(j1, 0, nz)
    return S[j1, i1] - S[j0, i1] - S[j1, i0] + S[j0, i0]


def _quadtree_punkte(segs, kante, h_min, h_max, rand_h, abstufung, rand_abstand):
    """Eckpunkte eines Quadtrees über [0, kante]² (lokal), fein an den Streckenrändern."""
    K = int(round(kante))
    # Rasterproben der Ränder (1 m) und Überdeckung (Pixelmitten, even-odd)
    laenge = np.linalg.norm(segs[:, 1] - segs[:, 0], axis=1)
    n = np.maximum(np.ceil(laenge / 0.5).astype(np.int64), 1)
    sid = np.repeat(np.arange(len(segs)), n)
    f = (np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n)) / np.repeat(n, n)
    proben = segs[sid, 0] + f[:, None] * (segs[sid, 1] - segs[sid, 0])
    pi = np.clip(np.floor(proben).astype(np.int64), 0, K - 1)
    nahe = np.zeros((K, K), np.int64)
    np.add.at(nahe, (pi[:, 1], pi[:, 0]), 1)
    S_nahe = _prefix(nahe)
    k, xc = _zeilenkreuzungen(segs, 0.5)
    ok = (k >= 0) & (k < K)
    diff = np.zeros((K, K + 1), np.int64)
    np.add.at(diff, (k[ok], np.clip(np.ceil(xc[ok] - 0.5).astype(np.int64), 0, K)), 1)
    bedeckt = (diff.cumsum(1)[:, :K] % 2).astype(np.int64)
    S_bed = _prefix(bedeckt)
    blaetter = []
    s = K
    zellen = np.zeros((1, 2), np.int64)                        # (i, j) in Einheiten s
    while len(zellen):
        x0, z0 = zellen[:, 0] * s, zellen[:, 1] * s
        r = int(math.ceil(abstufung * s))
        nah = _summe(S_nahe, x0 - r, x0 + s + r, z0 - r, z0 + s + r) > 0
        voll = _summe(S_bed, x0, x0 + s, z0, z0 + s) == s * s
        d_rand = np.minimum(np.minimum(x0, K - x0 - s), np.minimum(z0, K - z0 - s))
        teilen = (s > h_max) | ((s > rand_h + d_rand / abstufung) & (s > rand_h / 2))
        teilen |= nah & (s > h_min) & ~voll
        if s // 2 < 1:
            teilen[:] = False
        blaetter.append((zellen[~teilen], s))
        z = zellen[teilen] * 2
        zellen = np.concatenate([z, z + [1, 0], z + [0, 1], z + [1, 1]])
        s //= 2
    ecken = []
    for z, s in blaetter:
        for di, dj in ((0, 0), (1, 0), (0, 1), (1, 1)):
            ecken.append(np.c_[(z[:, 0] + di) * s, (z[:, 1] + dj) * s])
    ecken = np.unique(np.concatenate(ecken), axis=0)
    # Lochrand: wird durch die Innenrand-Vertices des Außenterrains ersetzt
    innen = (ecken[:, 0] > 0) & (ecken[:, 0] < K) & (ecken[:, 1] > 0) & (ecken[:, 1] < K)
    ecken = ecken[innen]
    # zu nahe am Lochrand (Randvertices liegen nicht exakt auf dem Raster)
    d_r = np.minimum(np.minimum(ecken[:, 0], K - ecken[:, 0]), np.minimum(ecken[:, 1], K - ecken[:, 1]))
    ecken = ecken[d_r >= rand_abstand]
    # in der Strecke oder zu nahe am Streckenrand
    r = int(math.ceil(h_min / 2))
    x, z = ecken[:, 0], ecken[:, 1]
    zu_nah = _summe(S_nahe, x - r, x + r, z - r, z + r) > 0
    in_strecke = _innen(segs, x.astype(np.float64), z.astype(np.float64))
    ecken = ecken[~zu_nah & ~in_strecke]
    stat = {int(s): int(len(z)) for z, s in blaetter if len(z)}
    return ecken.astype(np.float64), stat


# ============================================================================ Höhen
def _laplace_cg(pos, tri, fest, toleranz=1e-9, max_iter=20000):
    """Harmonische Höhen (Kotangentengewichte, auf ≥ 0 begrenzt) für nicht feste Vertices."""
    p2 = pos[:, (0, 2)]
    nv = len(pos)
    w_i, w_j, w_w = [], [], []
    for k in range(3):
        i, j, o = tri[:, (k + 1) % 3], tri[:, (k + 2) % 3], tri[:, k]
        u, v = p2[i] - p2[o], p2[j] - p2[o]
        kreuz = np.abs(u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0])
        cot = (u * v).sum(1) / np.maximum(kreuz, 1e-12)
        w_i.append(i); w_j.append(j); w_w.append(0.5 * cot)
    I, J, W = np.concatenate(w_i), np.concatenate(w_j), np.concatenate(w_w)
    lo, hi = np.minimum(I, J), np.maximum(I, J)
    schl = lo * nv + hi
    u, inv = np.unique(schl, return_inverse=True)
    W = np.bincount(inv.ravel(), W, len(u))
    negativ = int((W < 0).sum())
    W = np.maximum(W, 1e-9)
    I, J = u // nv, u % nv
    y = pos[:, 1].copy()
    frei = ~fest
    # A x = b für freie Vertices: sum_j w_ij (x_i - x_j) = 0
    diag = np.bincount(I, W, nv) + np.bincount(J, W, nv)

    def A(x):
        voll = np.zeros(nv)
        voll[frei] = x
        r = diag * voll
        r -= np.bincount(I, W * voll[J], nv) + np.bincount(J, W * voll[I], nv)
        return r[frei]

    yb = np.where(fest, y, 0.0)
    b = np.bincount(I, W * yb[J], nv) + np.bincount(J, W * yb[I], nv)
    b = b[frei]
    M = 1.0 / diag[frei]
    x = np.full(int(frei.sum()), float(np.mean(y[fest])))
    r = b - A(x)
    z = M * r
    p = z.copy()
    rz = float(r @ z)
    nb = float(np.linalg.norm(b)) or 1.0
    it = 0
    for it in range(1, max_iter + 1):
        Ap = A(p)
        alpha = rz / float(p @ Ap)
        x += alpha * p
        r -= alpha * Ap
        if float(np.linalg.norm(r)) < toleranz * nb:
            break
        z = M * r
        rz_neu = float(r @ z)
        p = z + (rz_neu / rz) * p
        rz = rz_neu
    y[frei] = x
    return y, it, negativ


# ============================================================================ Hauptfunktion
def adaptive_grundflaeche(pos: np.ndarray, tri: np.ndarray, loch: dict, *, h_min: float = 4.0,
                          h_max: float = 64.0, rand_h: float = 16.0, abstufung: float = 1.0,
                          neigung_warnung: float = 45.0) -> dict:
    """Grundfläche zwischen Streckenrand und Lochrand (Spielkoordinaten).

    pos/tri: platziertes Streckennetz (alle Klassen inkl. Mauern). loch: ``spiel_geometrie.loch_lesen``.
    Rückgabe wie ``spiel_geometrie.grundflaeche``: positionen, render, physik (+ Kennwerte).
    """
    t_start = time.perf_counter()
    zeiten = {}
    x0, z0, kante = float(loch['x0']), float(loch['z0']), float(loch['kante'])
    K = int(round(kante))
    if K & (K - 1):
        raise GelaendeFehler('Hole edge %.3f m is not a power of two' % kante)
    P, schleifen, rand_info = streckenrand(pos, tri)
    if not schleifen:
        raise GelaendeFehler('No track edge found')
    zeiten['streckenrand'] = time.perf_counter() - t_start
    # Lochrand-Vertices (exakt aus dem Außenterrain); Ersatz: 16-m-Raster auf Höhe 0
    rand = np.asarray(loch.get('rand', np.zeros((0, 3))), np.float64)
    if len(rand) < 4:
        t = np.arange(0, K, 16.0)
        rand = np.concatenate([np.c_[t, np.zeros_like(t), np.zeros_like(t)],
                               np.c_[np.full_like(t, K), np.zeros_like(t), t],
                               np.c_[K - t, np.zeros_like(t), np.full_like(t, K)],
                               np.c_[np.zeros_like(t), np.zeros_like(t), K - t]]) + [x0, 0, z0]
    # Lochrand im Umlaufsinn ordnen (Winkel um die Mitte)
    mitte = np.array([x0 + kante / 2, z0 + kante / 2])
    winkel = np.arctan2(rand[:, 2] - mitte[1], rand[:, 0] - mitte[0])
    rand = rand[np.argsort(winkel, kind='stable')]
    # Lokale Koordinaten
    loc = lambda q: np.c_[q[:, 0] - x0, q[:, 2] - z0]
    segs = []
    for s in schleifen:
        q = loc(P[s])
        segs.append(np.stack([q, np.roll(q, -1, 0)], 1))
    segs = np.concatenate(segs)
    sp = loc(P[np.concatenate(schleifen)])
    abstand_loch = float(min(sp.min(), K - sp.max()))
    if abstand_loch < rand_h:
        raise GelaendeFehler('Track comes within %.1f m of the edge of the outer terrain hole' % abstand_loch)
    innen_pkt, qt_stat = _quadtree_punkte(segs, K, h_min, h_max, rand_h, abstufung, rand_abstand=rand_h / 2)
    zeiten['quadtree'] = time.perf_counter() - t_start
    # Punktliste: Streckenrand, Lochrand, Innenpunkte
    sch_idx = np.concatenate(schleifen)
    n_s, n_r = len(sch_idx), len(rand)
    xy = np.concatenate([sp, loc(rand), innen_pkt])
    welt_y = np.concatenate([P[sch_idx, 1], rand[:, 1], np.zeros(len(innen_pkt))])
    welt_xz = np.concatenate([P[sch_idx][:, (0, 2)], rand[:, (0, 2)],
                              np.c_[innen_pkt[:, 0] + x0, innen_pkt[:, 1] + z0]])
    # Einfügereihenfolge: Morton-Ordnung (räumlich zusammenhängend, deterministisch)
    q = np.clip((xy / max(K, 1) * 65535).astype(np.int64), 0, 65535)

    def spreizen(v):
        v = (v | (v << 8)) & 0x00FF00FF
        v = (v | (v << 4)) & 0x0F0F0F0F
        v = (v | (v << 2)) & 0x33333333
        return (v | (v << 1)) & 0x55555555

    morton = spreizen(q[:, 0]) | (spreizen(q[:, 1]) << 1)
    reihenfolge = np.argsort(morton, kind='stable')
    cdt = _CDT(xy[:, 0].tolist(), xy[:, 1].tolist())
    doppelt = 0
    for p in reihenfolge.tolist():
        if not cdt.einfuegen(p):
            doppelt += 1
    if doppelt:
        raise GelaendeFehler('%d duplicate points in the ground surface' % doppelt)
    zeiten['delaunay'] = time.perf_counter() - t_start
    # Zwangskanten: Streckenschleifen und Lochrand
    fehlend = 0
    basis = 0
    zwang_liste = []
    for s in schleifen:
        m = len(s)
        for a in range(m):
            zwang_liste.append((basis + a, basis + (a + 1) % m))
        basis += m
    for a in range(n_r):
        zwang_liste.append((n_s + a, n_s + (a + 1) % n_r))
    for a, b in zwang_liste:
        if cdt.zwangskante(a, b):
            fehlend += 1
    runden = cdt.delaunay_wiederherstellen()
    zeiten['zwangskanten'] = time.perf_counter() - t_start
    par, T = cdt.gebiete()
    behalten = (par % 2 == 1) & np.all(T < len(xy), axis=1)
    T = T[behalten]
    # Wicklung: (x, z) gegen den Uhrzeigersinn → Normale −Y; für +Y umdrehen
    T = T[:, [0, 2, 1]]
    benutzt = np.unique(T)
    neu = np.full(len(xy), -1, np.int64)
    neu[benutzt] = np.arange(len(benutzt))
    T = neu[T]
    fest = np.zeros(len(xy), bool)
    fest[:n_s + n_r] = True
    posw = np.c_[welt_xz[:, 0], welt_y, welt_xz[:, 1]][benutzt]
    fest = fest[benutzt]
    hy, cg_iter, negativ = _laplace_cg(posw, T, fest)
    posw[:, 1] = hy
    zeiten['hoehen'] = time.perf_counter() - t_start
    # Kennwerte
    fn = np.cross(posw[T[:, 1]] - posw[T[:, 0]], posw[T[:, 2]] - posw[T[:, 0]])
    flaeche = 0.5 * np.linalg.norm(fn, axis=1)
    ny = fn[:, 1] / np.maximum(np.linalg.norm(fn, axis=1), 1e-12)
    neigung = np.degrees(np.arccos(np.clip(ny, -1, 1)))
    kl = np.stack([np.linalg.norm(posw[T[:, (k + 1) % 3]] - posw[T[:, k]], axis=1) for k in range(3)], 1)
    p2 = posw[:, (0, 2)]
    winkel = []
    for k in range(3):
        u = p2[T[:, (k + 1) % 3]] - p2[T[:, k]]
        v = p2[T[:, (k + 2) % 3]] - p2[T[:, k]]
        c = (u * v).sum(1) / np.maximum(np.linalg.norm(u, axis=1) * np.linalg.norm(v, axis=1), 1e-12)
        winkel.append(np.degrees(np.arccos(np.clip(c, -1, 1))))
    winkel = np.stack(winkel, 1)
    ergebnis = dict(
        positionen=posw, render=T, physik=T, verfahren='adaptiv',
        parameter=dict(h_min=h_min, h_max=h_max, rand_h=rand_h, abstufung=abstufung),
        streckenrand=rand_info, lochrand_vertices=int(n_r), quadtree_blaetter=qt_stat,
        punkte=dict(streckenrand=int(n_s), lochrand=int(n_r), innen=int(len(innen_pkt)),
                    gesamt=int(len(posw))),
        dreiecke_gesamt=int(len(T)), zwangskanten=len(zwang_liste), zwangskanten_eingearbeitet=fehlend,
        delaunay_runden=int(runden), flips=int(cdt.flips), cg_iterationen=int(cg_iter),
        negative_kotangenten=negativ,
        kante_m=dict(min=float(kl.min()), median=float(np.median(kl)), max=float(kl.max())),
        winkel_grad=dict(min=float(winkel.min()), p01=float(np.quantile(winkel.min(1), 0.01))),
        flaeche_min_m2=float(flaeche.min()), flaeche_m2=float(flaeche.sum()),
        normalen_nach_unten=int((ny <= 0).sum()),
        neigung_grad_max=float(neigung.max()), neigung_grad_p99=float(np.quantile(neigung, 0.99)),
        steil_ueber_warnung=int((neigung > neigung_warnung).sum()), neigung_warnung=neigung_warnung,
        hoehe=(float(hy.min()), float(hy.max())),
        abstand_strecke_lochrand_m=abstand_loch,
        sekunden={k: round(v, 3) for k, v in zeiten.items()})
    ergebnis['sekunden']['gesamt'] = round(time.perf_counter() - t_start, 3)
    return ergebnis
