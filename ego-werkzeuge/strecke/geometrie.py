"""Dreiecksnetz der Strecke mit Oberflächenklassen; Export als OBJ, NPZ und JSON.

Bodenfläche: Je Station ein Querschnitt mit 14 seitlichen Stützabständen (von der rechten
Barriere bis zur linken Barriere), dazwischen 13 Bänder:
    rechts: Auslauf, Gras, Randstein, Boxengasse, Grünkeil | Randlinie, Asphalt,
    Randlinie | links: Grünkeil, Boxengasse, Randstein, Gras, Auslauf.
Jedes Band ist ein eigener Dreiecksstreifen (eigene Vertices, damit UVs je Oberfläche
passen). Bänder ohne Breite erzeugen nur entartete Dreiecke, die entfernt werden.
Die erste Station liegt auf der Start-Ziel-Linie, eine zweite bei s = Linienbreite; die
Fahrbahn dazwischen erhält die Oberfläche "linie". Die letzte Station wiederholt die erste
(Naht), damit die V-Koordinate stetig bleibt.

Wände: Barrieren links/rechts als Prisma (Innenseite, Oberseite, Außenseite), Boxenmauer
als Prisma mit Endkappen. Oberfläche "mauer".

UV: Fahrbahn u = 0 (rechter Rand) .. 1 (linker Rand), sonst u = Abstand vom Fahrbahnrand /
Texturlänge; v = s / Texturlänge (Randstein: eigene Länge). Normalen je Vertex
(flächengewichtet) – Dreiecke sind so orientiert, dass Bodenflächen nach oben zeigen.
"""

import json
import os

import numpy as np

from .definition import OBERFLAECHE, OBERFLAECHEN, achsen_matrix
from .mittellinie import laeufe

FARBEN = {  # nur für OBJ-Material und Vorschau
    "asphalt": (0.33, 0.33, 0.35), "randstein": (0.85, 0.15, 0.15), "gras": (0.35, 0.6, 0.3),
    "kies": (0.82, 0.74, 0.55), "mauer": (0.75, 0.75, 0.75), "boxengasse": (0.45, 0.45, 0.47),
    "linie": (0.95, 0.95, 0.95), "auslauf": (0.40, 0.40, 0.43), "startbox": (0.97, 0.97, 0.97),
    "gebaeude": (0.62, 0.62, 0.66),
}

#: Startboxen liegen so weit über dem Asphalt (eigene Klasse, nur sichtbar; kein Z-Fighting)
STARTBOX_HOEHE_M = 0.015


#: Startmarkierung im Rahmen des Startplatzes (x quer, links positiv, z längs, vorne positiv), bei allen Plätzen und
#: Strecken gleich (por_gridbox_a_01/_02, sin_gridbox_*): Rahmen um das Auto, 2,2 m breit, nach hinten dünn auslaufend,
#: dazu ein Querbalken. Der Platz (Gruppe GridWheelMarker) ist nicht die Fahrzeugmitte.
STARTBOX_FORM = ((-1.70, 0.30, -0.10, 0.10), (-0.25, 1.95, 0.40, 0.50), (1.80, 1.95, 0.00, 0.40),
                 (-0.25, -0.10, 0.10, 0.40), (-0.25, -0.20, -3.00, -0.10), (1.90, 1.95, -3.00, 0.00))
#: Mitte des Rahmens links vom Platz; der Platz rückt um diesen Wert nach rechts, damit das Auto mittig in der Reihe steht
STARTPLATZ_QUER_M = 0.85


def startbox_flaechen(param):
    """Rechtecke der Startboxen als (i, s0, s1, vz, d0, d1) – s ab Start-Ziel-Linie (negativ = dahinter), d quer zur
    Reihenmitte (positiv = links). Platzlage wie die Startplätze der Logik (``logik.py``, Startaufstellung)."""
    pg = param["startaufstellung"]
    if not pg.get("startboxen", True):
        return []
    rechtecke = []
    for i in range(int(pg["anzahl"])):
        ss = -pg["abstand_pole_m"] - i * pg["abstand_m"]
        vz = (1 if pg["pole_seite"] == "links" else -1) * (-1 if i % 2 else 1)
        for x0, x1, z0, z1 in STARTBOX_FORM:
            rechtecke.append((i, ss + z0, ss + z1, vz, x0 - STARTPLATZ_QUER_M, x1 - STARTPLATZ_QUER_M))
    return rechtecke


class Netz:
    """Sammelt Dreiecksstreifen; ``fertig()`` liefert die Arrays."""

    def __init__(self):
        self._v, self._uv, self._f, self._o = [], [], [], []
        self.nv = 0
        self.entfernt = 0

    def streifen(self, A, B, uvA, uvB, oberflaeche, hinweis):
        """Quads (A_i, A_i+1, B_i, B_i+1). oberflaeche (m-1,), hinweis (m-1, 3) gewünschte Normale."""
        A, B = np.asarray(A, float), np.asarray(B, float)
        m = len(A)
        if m < 2:
            return
        verts = np.vstack([A, B])
        ia = np.arange(m)
        ib = m + ia
        t = np.vstack([np.column_stack([ia[:-1], ia[1:], ib[:-1]]),
                       np.column_stack([ib[:-1], ia[1:], ib[1:]])])
        o = np.concatenate([oberflaeche, oberflaeche])
        h = np.broadcast_to(np.asarray(hinweis, float), (m - 1, 3))
        h = np.vstack([h, h])
        p0, p1, p2 = verts[t[:, 0]], verts[t[:, 1]], verts[t[:, 2]]
        nrm = np.cross(p1 - p0, p2 - p0)
        flaeche = 0.5 * np.linalg.norm(nrm, axis=1)
        dreh = np.einsum("ij,ij->i", nrm, h) < 0
        t[dreh] = t[dreh][:, [0, 2, 1]]
        gut = flaeche > 1e-6
        self.entfernt += int((~gut).sum())
        self._v.append(verts)
        self._uv.append(np.vstack([uvA, uvB]))
        self._f.append(t[gut] + self.nv)
        self._o.append(o[gut])
        self.nv += 2 * m

    def fertig(self):
        v = np.vstack(self._v)
        uv = np.vstack(self._uv)
        f = np.vstack(self._f).astype(np.int64)
        o = np.concatenate(self._o).astype(np.int16)
        # unbenutzte Vertices entfernen
        benutzt = np.zeros(len(v), bool)
        benutzt[f.ravel()] = True
        neu = np.cumsum(benutzt) - 1
        v, uv, f = v[benutzt], uv[benutzt], neu[f]
        fn = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
        n = np.zeros_like(v)
        for k in range(3):
            np.add.at(n, f[:, k], fn)
        n /= np.maximum(np.linalg.norm(n, axis=1), 1e-12)[:, None]
        return {"vertices": v, "uv": uv, "normalen": n, "dreiecke": f, "oberflaeche": o,
                "entfernt_entartet": self.entfernt}


def netz_erzeugen(ml, qp, param):
    """Erzeugt das Netz der ganzen Strecke (interne Koordinaten)."""
    pg = param["geometrie"]
    L = ml.laenge
    m = max(16, int(round(L / pg["schritt_m"])))
    s = list(np.arange(m) * (L / m))
    lb = float(param["start_ziel"]["linienbreite"])
    if 0 < lb < s[1]:
        s.insert(1, lb)
    s_geo = np.array(s + [L])
    st = ml.stationen(s_geo)
    prof, mauer = qp.an(s_geo, L)
    M = len(s_geo)
    wl, wr = st.wl, st.wr
    lw = float(param["fahrbahn"].get("randlinie_breite", 0.0) or 0.0)
    if lw >= np.min(wl+wr)/2:
        raise ValueError("Edge lines are wider than the road")
    eR, eL = prof["rechts"]["e"], prof["links"]["e"]
    D = np.column_stack([-(wr[:, None] + eR[:, ::-1]),              # 6 Spalten: e5..e0 rechts
                         -wr + lw, wl - lw,
                         wl[:, None] + eL])                          # 6 Spalten: e0..e5 links
    # Spalte 5 = -wr, Spalte 8 = wl
    linie, asph = OBERFLAECHE["linie"], OBERFLAECHE["asphalt"]
    bR, bL = prof["rechts"]["band"], prof["links"]["band"]
    baender = [bR[:, 4], bR[:, 3], bR[:, 2], bR[:, 1], bR[:, 0],
               np.full(M, linie), np.full(M, asph), np.full(M, linie),
               bL[:, 0], bL[:, 1], bL[:, 2], bL[:, 3], bL[:, 4]]
    startlinie = s_geo < lb - 1e-9
    tl = float(pg["textur_laenge_m"])
    tk = float(pg["randstein_textur_m"])
    netz = Netz()
    oben = np.array([0.0, 1.0, 0.0])

    def punkte(d):
        p = st.punkt(d)
        p[:, 1] = ml.hoehe_quer(st, d)
        return p

    for j in range(13):
        A, B = punkte(D[:, j]), punkte(D[:, j + 1])
        o = baender[j][:-1].astype(np.int16).copy()
        if 5 <= j <= 7:
            o[startlinie[:-1]] = linie
        if 5 <= j <= 7:
            uA = (D[:, j] + wr) / (wl + wr)
            uB = (D[:, j + 1] + wr) / (wl + wr)
        else:
            rand = np.where(D[:, j] < 0, -wr, wl)
            uA = np.abs(D[:, j] - rand) / tl
            uB = np.abs(D[:, j + 1] - rand) / tl
        laenge_v = tk if j in (2, 10) else tl
        v = s_geo / laenge_v
        netz.streifen(A, B, np.column_stack([uA, v]), np.column_stack([uB, v]), o, oben)

    # ---- Startboxen: eigene Klasse knapp über dem Asphalt (nur sichtbar)
    pga = param["startaufstellung"]
    for i, s0, s1, vz, q0, q1 in startbox_flaechen(param):
        k = max(2, int(np.ceil((s1 - s0) / 0.5)) + 1)
        ss = np.linspace(s0, s1, k)
        sub = ml.stationen(ss)
        off = pga["seitenversatz_m"]
        mitte = vz * (np.full(k, float(off)) if off is not None else (sub.wl + sub.wr) / 4)

        def kante(q):
            p = sub.punkt(mitte + q)
            p[:, 1] = ml.hoehe_quer(sub, mitte + q) + STARTBOX_HOEHE_M
            return p
        v = (ss - s0)[:, None]
        netz.streifen(kante(q0), kante(q1), np.column_stack([np.zeros(k), v[:, 0]]),
                      np.column_stack([np.ones(k), v[:, 0]]), np.full(k - 1, OBERFLAECHE["startbox"], np.int16), oben)

    # ---- Boxengasse: Linien (Tempolimit, Ein-/Ausfahrt) und Garagengebäude
    pbox_ = param["boxengasse"]
    if pbox_.get("aktiv") and qp.box is not None:
        _boxengasse_ausstattung(netz, ml, qp, pbox_, oben, tl)

    # ---- Barrieren
    pb = param["barriere"]
    mauer_o = np.full(M - 1, OBERFLAECHE["mauer"], np.int16)
    for vz, d in ((1.0, D[:, 13]), (-1.0, D[:, 0])):
        innen = punkte(d)
        aussen = punkte(d + vz * pb["dicke"])
        hoch_i = innen + [0, pb["hoehe"], 0]
        hoch_a = aussen + [0, pb["hoehe"], 0]
        nach_innen = np.column_stack([-vz * st.nl[:-1, 0], np.zeros(M - 1), -vz * st.nl[:-1, 1]])
        v = s_geo / tl
        netz.streifen(innen, hoch_i, np.column_stack([np.zeros(M), v]),
                      np.column_stack([np.full(M, pb["hoehe"] / tl), v]), mauer_o, nach_innen)
        netz.streifen(hoch_i, hoch_a, np.column_stack([np.zeros(M), v]),
                      np.column_stack([np.full(M, pb["dicke"] / tl), v]), mauer_o, oben)
        netz.streifen(aussen, hoch_a, np.column_stack([np.zeros(M), v]),
                      np.column_stack([np.full(M, pb["hoehe"] / tl), v]), mauer_o, -nach_innen)

    # ---- Boxenmauer
    pbox = param["boxengasse"]
    if pbox.get("aktiv"):
        vz = 1.0 if pbox["seite"] == "links" else -1.0
        w = wl if vz > 0 else wr
        da = np.isfinite(mauer[:-1])
        for a, e in laeufe(da):
            idx = np.arange(a, e + 1) % (M - 1)          # e (exkl.) + 1 Station für das letzte Quad
            if len(idx) < 2:
                continue
            sub = ml.stationen(s_geo[idx])
            mi = mauer[idx]
            mi = np.where(np.isfinite(mi), mi, np.nanmax(mi))
            d0 = vz * (sub.wl if vz > 0 else sub.wr) + vz * (mi - pbox["mauer_dicke"] / 2)
            d1 = d0 + vz * pbox["mauer_dicke"]
            P0 = sub.punkt(d0)
            P1 = sub.punkt(d1)
            P0[:, 1] = P1[:, 1] = ml.hoehe_quer(sub, d0)
            H0, H1 = P0 + [0, pbox["mauer_hoehe"], 0], P1 + [0, pbox["mauer_hoehe"], 0]
            k = len(idx)
            o = np.full(k - 1, OBERFLAECHE["mauer"], np.int16)
            zur_bahn = np.column_stack([-vz * sub.nl[:-1, 0], np.zeros(k - 1), -vz * sub.nl[:-1, 1]])
            sv = np.arange(k) * 1.0
            uv0 = np.column_stack([np.zeros(k), sv / tl])
            uv1 = np.column_stack([np.ones(k) * 0.1, sv / tl])
            netz.streifen(P0, H0, uv0, uv1, o, zur_bahn)
            netz.streifen(H0, H1, uv0, uv1, o, oben)
            netz.streifen(P1, H1, uv0, uv1, o, -zur_bahn)
            for j, richt in ((0, -sub.t3[0]), (k - 1, sub.t3[-1])):   # Endkappen
                netz.streifen(np.array([P0[j], P1[j]]), np.array([H0[j], H1[j]]),
                              np.zeros((2, 2)), np.ones((2, 2)) * 0.1,
                              np.array([OBERFLAECHE["mauer"]], np.int16), richt[None, :])
    return netz.fertig()


def _boxengasse_ausstattung(netz, ml, qp, pbox, oben, tl):
    """Linien der Boxengasse (eigene Klasse ``startbox`` 1,5 cm über dem Boden, nur sichtbar) und ein einfaches
    Garagengebäude hinter den Boxen (Klasse ``gebaeude``): Rückwand, Trennwände je Box, Dach; vorne offen.

    Lagen wie in der Logik: Tempolimit ab ``anlauf_m`` nach Einfahrtsbeginn bis ``anlauf_m`` vor dem Ende
    (``logik.py``, Zonen ``pit_lim_on``), Boxen nach ``querprofil.boxen_u``."""
    L = ml.laenge
    seite = pbox["seite"]
    vz = 1.0 if seite == "links" else -1.0
    s_ein, U = qp.box["s_einfahrt"], qp.box["laenge_entlang_strecke"]
    an, ue = pbox["anlauf_m"], pbox["uebergang_m"]

    def profil(u):
        ss = s_ein + np.asarray(u, float)
        sub = ml.stationen(ss)
        prof, _ = qp.an(sub.s, L)
        w = sub.wl if vz > 0 else sub.wr
        e = prof[seite]["e"]
        return sub, w, e

    def flaeche(sub, d0, d1, hoehe, klasse, normale, v=None):
        k = len(sub.s)
        A, B = sub.punkt(d0), sub.punkt(d1)
        A[:, 1] = ml.hoehe_quer(sub, d0) + hoehe
        B[:, 1] = ml.hoehe_quer(sub, d1) + hoehe
        v = np.arange(k, dtype=float) if v is None else v
        netz.streifen(A, B, np.column_stack([np.zeros(k), v]), np.column_stack([np.ones(k), v]),
                      np.full(k - 1, OBERFLAECHE[klasse], np.int16), normale)

    if pbox.get("linien", True):
        lw = float(pbox.get("linie_breite_m", 0.3))
        # Querlinien über die ganze Boxengasse an Beginn und Ende des Tempolimits
        for u0 in (an, U - an):
            sub, w, e = profil(np.linspace(u0 - lw / 2, u0 + lw / 2, 2))
            flaeche(sub, vz * (w + e[:, 1]), vz * (w + e[:, 2]), STARTBOX_HOEHE_M, "startbox", oben, v=np.array([0, lw]))
        # Linie am inneren Rand der Boxengasse über Ein- und Ausfahrt (trennt Gasse und Strecke)
        for ua, ub in ((0.0, an + ue), (U - an - ue, U)):
            uu = np.linspace(ua, ub, max(2, int(np.ceil((ub - ua) / 1.0)) + 1))
            sub, w, e = profil(uu)
            d = w + e[:, 1]
            flaeche(sub, vz * d, vz * (d + lw), STARTBOX_HOEHE_M, "startbox", oben, v=uu - ua)

    gu = qp.box.get("garagen_u")
    if gu is None or pbox.get("gebaeude_modell", "generiert") == "traeger":
        return                               # "traeger": echte Garagenmodule setzt der Bau (spiel_welt)
    tiefe, H, dw = (float(pbox[k]) for k in ("gebaeude_tiefe_m", "gebaeude_hoehe_m", "gebaeude_wand_m"))
    uu = np.linspace(gu[0], gu[1], max(2, int(np.ceil((gu[1] - gu[0]) / 2.0)) + 1))
    sub, w, e = profil(uu)
    vorn = w + e[:, 2]                       # Außenrand der Boxengasse = Garagentor
    hinten = vorn + tiefe
    zur_gasse = np.column_stack([-vz * sub.nl[:-1, 0], np.zeros(len(uu) - 1), -vz * sub.nl[:-1, 1]])
    unten = -oben
    v = (uu - uu[0]) / tl

    def senkrecht(sub_, d, h0, h1, normale, v_):
        k = len(sub_.s)
        A = sub_.punkt(d)
        A[:, 1] = ml.hoehe_quer(sub_, d) + h0
        B = A + [0, h1 - h0, 0]
        netz.streifen(A, B, np.column_stack([np.zeros(k), v_]), np.column_stack([np.full(k, (h1 - h0) / tl), v_]),
                      np.full(k - 1, OBERFLAECHE["gebaeude"], np.int16), normale)

    # Rückwand (innen, oben, außen)
    senkrecht(sub, vz * hinten, 0, H, zur_gasse, v)
    senkrecht(sub, vz * (hinten + dw), 0, H, -zur_gasse, v)
    flaeche(sub, vz * hinten, vz * (hinten + dw), H, "gebaeude", oben, v=v)
    # Dach: Unter- und Oberseite, Vorderkante
    flaeche(sub, vz * vorn, vz * (hinten + dw), H, "gebaeude", unten, v=v)
    flaeche(sub, vz * vorn, vz * (hinten + dw), H + dw, "gebaeude", oben, v=v)
    senkrecht(sub, vz * vorn, H, H + dw, zur_gasse, v)
    # Trennwände zwischen den Garagen (inkl. Stirnwände an beiden Enden)
    grenzen = np.arange(gu[0], gu[1] - 1.0, float(pbox["box_abstand_m"]))
    grenzen = np.append(grenzen, gu[1])                 # Stirnwand am Ende (auch bei gekürzter letzter Garage)
    for ug in grenzen:
        for seite_s, richt in ((-dw / 2, -1.0), (dw / 2, 1.0)):
            st1, w1, e1 = profil([ug + seite_s])
            q = np.linspace(w1[0] + e1[0, 2], w1[0] + e1[0, 2] + tiefe, 2)
            A = np.array([st1.punkt(np.array([vz * qq]))[0] for qq in q])
            A[:, 1] = [ml.hoehe_quer(st1, np.array([vz * qq]))[0] for qq in q]
            B = A + [0, H, 0]
            nrm = richt * np.array([st1.t3[0, 0], 0.0, st1.t3[0, 2]])
            netz.streifen(A, B, np.array([[0, 0], [tiefe / tl, 0]]), np.array([[0, H / tl], [tiefe / tl, H / tl]]),
                          np.array([OBERFLAECHE["gebaeude"]], np.int16), nrm[None, :])
        sw, ww, ew = profil(np.array([ug - dw / 2, ug + dw / 2]))
        senkrecht(sw, vz * (ww + ew[:, 2]), 0, H, zur_gasse[:1], np.array([0, dw / tl]))


# =========================================================================== Export
def _transformieren(netz, achsen):
    Mx = achsen_matrix(achsen)
    v = netz["vertices"] @ Mx.T
    n = netz["normalen"] @ Mx.T
    f = netz["dreiecke"].copy()
    if np.linalg.det(Mx) < 0:
        f = f[:, [0, 2, 1]]
    return v, n, f


def statistik(netz, codes):
    v, f, o = netz["vertices"], netz["dreiecke"], netz["oberflaeche"]
    fl = 0.5 * np.linalg.norm(np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]), axis=1)
    erg = []
    for i, name in enumerate(OBERFLAECHEN):
        m = o == i
        c = codes.get(name, {})
        erg.append({"id": i, "name": name, "code": c.get("code"), "code_status": c.get("status"),
                    "dreiecke": int(m.sum()), "flaeche_m2": round(float(fl[m].sum()), 1)})
    return erg


def obj_schreiben(pfad, netz, achsen):
    v, n, f = _transformieren(netz, achsen)
    uv, o = netz["uv"], netz["oberflaeche"]
    mtl = os.path.splitext(os.path.basename(pfad))[0] + ".mtl"
    with open(pfad, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# Streckengenerator AP6, Achsen %s, Einheit m\nmtllib %s\no strecke\n" % (achsen, mtl))
        fh.write("".join("v %.4f %.4f %.4f\n" % tuple(p) for p in v))
        fh.write("".join("vt %.5f %.5f\n" % tuple(t) for t in uv))
        fh.write("".join("vn %.4f %.4f %.4f\n" % tuple(q) for q in n))
        for i, name in enumerate(OBERFLAECHEN):
            ff = f[o == i] + 1
            if not len(ff):
                continue
            fh.write("g %s\nusemtl %s\n" % (name, name))
            fh.write("".join("f %d/%d/%d %d/%d/%d %d/%d/%d\n" % (a, a, a, b, b, b, c, c, c) for a, b, c in ff))
    with open(os.path.join(os.path.dirname(pfad), mtl), "w", encoding="utf-8", newline="\n") as fh:
        for name in OBERFLAECHEN:
            fh.write("newmtl %s\nKd %.3f %.3f %.3f\n\n" % ((name,) + FARBEN[name]))


def npz_schreiben(pfad, netz, achsen, codes):
    v, n, f = _transformieren(netz, achsen)
    np.savez_compressed(pfad, vertices=v.astype(np.float32), normalen=n.astype(np.float32),
                        uv=netz["uv"].astype(np.float32), dreiecke=f.astype(np.uint32),
                        oberflaeche=netz["oberflaeche"].astype(np.uint8),
                        oberflaechen_namen=np.array(OBERFLAECHEN),
                        oberflaechen_codes=np.array([codes.get(o, {}).get("code") or "" for o in OBERFLAECHEN]))


def json_schreiben(pfad, netz, achsen, codes, voll=True, dateien=None):
    v, n, f = _transformieren(netz, achsen)
    daten = {
        "format": "ego-strecke-netz", "version": 1,
        "koordinatensystem": {"achsen": achsen, "einheit": "m",
                              "hinweis": "Dreiecke gegen den Uhrzeigersinn von der Normalenseite gesehen "
                                         "(bei rechtshändigen Achsen); Bodenflächen zeigen nach oben"},
        "anzahl_vertices": int(len(v)), "anzahl_dreiecke": int(len(f)),
        "oberflaechen": statistik(netz, codes),
        "dateien": dateien or {},
        "felder": {"vertices": "[x, y, z] je Vertex", "normalen": "[x, y, z] je Vertex",
                   "uv": "[u, v] je Vertex", "dreiecke": "[i0, i1, i2] je Dreieck (0-basiert)",
                   "oberflaeche": "Oberflächen-Index je Dreieck (siehe oberflaechen)"},
    }
    if voll:
        daten["vertices"] = np.round(v, 4).tolist()
        daten["normalen"] = np.round(n, 4).tolist()
        daten["uv"] = np.round(netz["uv"], 5).tolist()
        daten["dreiecke"] = f.tolist()
        daten["oberflaeche"] = netz["oberflaeche"].tolist()
    with open(pfad, "w", encoding="utf-8") as fh:
        json.dump(daten, fh, ensure_ascii=False, separators=(",", ":") if voll else (",", ": "),
                  indent=None if voll else 1)
