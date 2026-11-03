"""Streckenpunkte kopieren und einfügen (Issue #2), ohne Qt.

Eingefügt wird nur in eine offene Kontrollpunktstrecke: Das Stück bindet mit seinem passenden Ende an eines der beiden
offenen Enden (Abstand plus Knick zur Fahrtrichtung), hinten angehängt oder vorne vorangestellt. Vorne verschiebt sich
der Eingabeursprung; Streckenmeter ab dem ersten Punkt (``von_s``/``bis_s``, ``start_ziel.s``) wandern um die Länge des
Stücks mit. Analyse: ``docs/mapeditor/issue2_punkte_kopieren.md``.
"""

from __future__ import annotations

import copy
import json
import math

import numpy as np

from .i18n import tr
from .project import point_xyz

FORMAT = "mapeditor-punkte"
VERSION = 1
MIME = "application/x-mapeditor-punkte"
#: Liegt das Stück weiter weg, wird es an das offene Ende geschoben (Originalkoordinaten bleiben sonst erhalten)
ANSCHLUSS_MAX_M = 50.0
#: Gewicht des Knicks am Anschluss (Meter je Bogenmaß; 90° zählen wie 24 m Abstand)
KNICK_GEWICHT_M = 15.0
#: Punktwerte, die beim Kopieren mitgehen
PUNKTWERTE = ("x", "y", "z", "breite", "breite_links", "breite_rechts", "querneigung_grad")


def _punkt(p):
    """Kontrollpunkt als Dict mit allen bekannten Werten (Listenform ``[x, z, y]`` wird umgeschrieben)."""
    x, y, z = point_xyz(p)
    neu = {"x": x, "y": y, "z": z}
    if isinstance(p, dict):
        for k in PUNKTWERTE[3:]:
            if p.get(k) is not None:
                neu[k] = p[k]
    return neu


def kette(indices, n, geschlossen):
    """Gewählte Indizes als zusammenhängende Kette in Laufrichtung (bei geschlossener Runde über den Rundenschluss)
    oder None, wenn die Auswahl Lücken hat."""
    gewaehlt = sorted(set(indices))
    if not gewaehlt:
        return None
    if len(gewaehlt) == n:
        return gewaehlt
    luecken = [i for a, b in zip(gewaehlt, gewaehlt[1:]) for i in [b] if b != a + 1]
    if not luecken:
        return gewaehlt
    if geschlossen and len(luecken) == 1 and gewaehlt[0] == 0 and gewaehlt[-1] == n - 1:
        k = gewaehlt.index(luecken[0])
        return gewaehlt[k:] + gewaehlt[:k]
    return None


def kopie(document):
    """Zwischenablage-Inhalt der Auswahl: Kontrollpunkte oder die Punkte gewählter Segmente (umgerechnet)."""
    ml = document.ml
    auswahl = sorted(document.selected_indices)
    if not auswahl:
        raise ValueError(tr("Nichts gewählt: erst Punkte wählen (Rahmen ziehen oder Strg+A)."))
    if ml.get("art") == "segmente":
        from .input_geometry import input_geometry
        g = input_geometry(document)
        if g["error"]:
            raise ValueError(g["error"])
        folge = kette(auswahl, len(g["segments"]), False)
        if folge is None:
            raise ValueError(tr("Nur zusammenhängende Punkte oder Segmente lassen sich kopieren."))
        punkte = []
        for i in folge:
            e, seg = g["segments"][i], ml["segmente"][i]
            for x, y, z in g["pos"][e["first"]:e["last"] + 1][(1 if punkte else 0):]:
                p = {"x": round(x, 3), "y": round(y, 3), "z": round(z, 3)}
                for k in PUNKTWERTE[3:]:
                    if seg.get(k) is not None:
                        p[k] = seg[k]
                punkte.append(p)
    elif ml.get("art") == "kontrollpunkte":
        folge = kette(auswahl, len(document.points), document.project.closed)
        if folge is None:
            raise ValueError(tr("Nur zusammenhängende Punkte oder Segmente lassen sich kopieren."))
        punkte = [_punkt(document.points[i]) for i in folge]
    else:
        raise ValueError(tr("Diese Eingabeart lässt sich nicht kopieren."))
    return {"format": FORMAT, "version": VERSION, "punkte": punkte,
            "quelle": {"name": document.project.raw.get("name", ""), "geschlossen": bool(document.project.closed)}}


def als_text(daten):
    return json.dumps(daten, ensure_ascii=False)


def aus_text(text):
    """Zwischenablage-Text → Punktliste; fremder Inhalt gibt eine verständliche Meldung."""
    try:
        daten = json.loads(text)
    except (TypeError, ValueError):
        raise ValueError(tr("Die Zwischenablage enthält keine kopierten Streckenpunkte."))
    if not isinstance(daten, dict) or daten.get("format") != FORMAT:
        raise ValueError(tr("Die Zwischenablage enthält keine kopierten Streckenpunkte."))
    if int(daten.get("version", 0)) > VERSION:
        raise ValueError(tr("Die Punkte stammen aus einer neueren Mapeditor-Version."))
    punkte = []
    for p in daten.get("punkte") or []:
        try:
            neu = {"x": float(p["x"]), "y": float(p.get("y", 0)), "z": float(p["z"])}
            for k in PUNKTWERTE[3:]:
                if p.get(k) is not None:
                    neu[k] = float(p[k])
        except (TypeError, ValueError, KeyError):
            raise ValueError(tr("Die Zwischenablage enthält keine kopierten Streckenpunkte."))
        if not all(math.isfinite(neu[k]) for k in ("x", "y", "z")):
            raise ValueError(tr("Die Zwischenablage enthält keine kopierten Streckenpunkte."))
        punkte.append(neu)
    if not punkte:
        raise ValueError(tr("Die Zwischenablage enthält keine kopierten Streckenpunkte."))
    return punkte


# ------------------------------------------------------------------ Geometrie
def _xz(punkte):
    return np.array([[point_xyz(p)[0], point_xyz(p)[2]] for p in punkte], dtype=float).reshape(-1, 2)


def _laenge(punkte):
    """3D-Polylinienlänge wie die Eingabe-Stationen des Editors (``input_geometry``)."""
    pts = [point_xyz(p) for p in punkte]
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


def _richtung(a, b):
    d = np.asarray(b, float) - np.asarray(a, float)
    n = float(np.hypot(*d))
    return d / n if n > 1e-9 else None


def _winkel(u, v):
    if u is None or v is None:
        return 0.0
    return math.acos(max(-1.0, min(1.0, float(u @ v))))


def punktabstand(punkte):
    """Üblicher Punktabstand (Median, 2–20 m) für das Anrücken eines weit entfernten Stücks."""
    p = _xz(punkte)
    if len(p) < 2:
        return 10.0
    d = np.sort(np.hypot(*np.diff(p, axis=0).T))
    return max(2.0, min(20.0, float(d[len(d) // 2])))


def anschluss(strecke, stueck):
    """Wie das Stück an die offene Strecke bindet: (hinten?, umgedreht?, Abstand m, Knick Grad).

    Bewertet werden die vier Paare aus offenem Ende (Anfang/Ende der Strecke) und Ende des Stücks: Abstand plus Knick
    zwischen der Fahrtrichtung am offenen Ende und der Richtung, in der das Stück dort weiterläuft."""
    s, t = _xz(strecke), _xz(stueck)
    kandidaten = []
    for hinten in (True, False):
        for umgedreht in (False, True):
            k = t[::-1] if umgedreht else t
            if hinten:      # Strecke … E → Stück k[0] … k[-1]
                ende = s[-1]
                fahrt = _richtung(s[-2], s[-1]) if len(s) > 1 else None
                naechst, weiter = k[0], (_richtung(k[0], k[1]) if len(k) > 1 else None)
            else:           # Stück k[0] … k[-1] → S … Strecke
                ende = s[0]
                fahrt = _richtung(s[0], s[1]) if len(s) > 1 else None
                naechst, weiter = k[-1], (_richtung(k[-2], k[-1]) if len(k) > 1 else None)
            d = float(np.hypot(*(naechst - ende)))
            knick = _winkel(fahrt, weiter)
            kandidaten.append((d + KNICK_GEWICHT_M * knick, d, hinten, umgedreht, knick))
    _, d, hinten, umgedreht, knick = min(kandidaten, key=lambda k: (k[0], k[1]))
    return hinten, umgedreht, d, math.degrees(knick)


def einfuegen_plan(strecke, stueck, geschlossen):
    """Neue Punktliste und Angaben zum Einfügen von ``stueck`` in die offene ``strecke``.

    Rückgabe: dict(punkte, neu=(erster, letzter Index der eingefügten Punkte), hinten, umgedreht, verschoben (dx, dz)
    oder None, versatz_m (Länge, um die sich die Streckenmeter ab dem ersten Punkt verschieben), abstand_m, knick_grad)."""
    if geschlossen:
        raise ValueError(tr("Einfügen geht nur bei offener Strecke: erst „Runde öffnen“ oder „Runde hier öffnen“."))
    stueck = [copy.deepcopy(p) for p in stueck]
    strecke = list(strecke)
    if len(strecke) < 2:
        punkte = strecke + stueck
        return dict(punkte=punkte, neu=(len(strecke), len(punkte) - 1), hinten=True, umgedreht=False, verschoben=None,
                    versatz_m=0.0, abstand_m=0.0, knick_grad=0.0)
    hinten, umgedreht, abstand, knick = anschluss(strecke, stueck)
    if umgedreht:
        stueck.reverse()
    verschoben = None
    if abstand > ANSCHLUSS_MAX_M:
        s = _xz(strecke)
        schritt = punktabstand(strecke)
        if hinten:
            fahrt = _richtung(s[-2], s[-1])
            ziel, eigenes = s[-1] + fahrt * schritt, _xz(stueck[:1])[0]
        else:
            fahrt = _richtung(s[0], s[1])
            ziel, eigenes = s[0] - fahrt * schritt, _xz(stueck[-1:])[0]
        dx, dz = (ziel - eigenes).tolist()
        for p in stueck:
            p["x"], p["z"] = round(p["x"] + dx, 3), round(p["z"] + dz, 3)
        verschoben = (dx, dz)
    if hinten:
        punkte = strecke + stueck
        neu, versatz = (len(strecke), len(punkte) - 1), 0.0
    else:
        punkte = stueck + strecke
        neu = (0, len(stueck) - 1)
        versatz = _laenge(stueck + strecke[:1])
    return dict(punkte=punkte, neu=neu, hinten=hinten, umgedreht=umgedreht, verschoben=verschoben,
                versatz_m=versatz, abstand_m=abstand, knick_grad=knick)


def stationen_verschieben(raw, versatz, laenge=None):
    """Streckenmeter ab dem ersten Punkt um ``versatz`` verschieben (bei ``laenge`` modulo Rundenlänge).
    DRS- und Straight-Mode-Zonen zählen ab dem Ziel und bleiben."""
    if abs(versatz) < 1e-9:
        return
    def neu(s):
        s = float(s) + versatz
        return round(s % laenge if laenge else s, 1)
    for gruppe in ("fahrbahn", "seitenraum"):
        for ab in (raw.get(gruppe) or {}).get("abschnitte") or []:
            for k in ("von_s", "bis_s"):
                if ab.get(k) is not None:
                    ab[k] = neu(ab[k])
    sz = raw.get("start_ziel")
    if isinstance(sz, dict) and sz.get("s") is not None:
        sz["s"] = neu(sz["s"])


def oeffnen_plan(punkte, auswahl):
    """„Runde hier öffnen“: neue Punktfolge einer geschlossenen Runde mit der Lücke am Ende.

    Ein gewählter Punkt: Öffnung hinter ihm. Mehrere zusammenhängende: diese werden entfernt, dort öffnet sich die Runde.
    Rückgabe: (punkte, versatz_m, laenge_m) – Streckenmeter verschieben sich um −versatz modulo Rundenlänge."""
    n = len(punkte)
    folge = kette(auswahl, n, True)
    if folge is None or len(folge) >= n - 1:
        raise ValueError(tr("Zum Öffnen einen Punkt oder ein zusammenhängendes Stück wählen."))
    pts = [point_xyz(p) for p in punkte]
    stationen = [0.0]
    for a, b in zip(pts, pts[1:]):
        stationen.append(stationen[-1] + math.dist(a, b))
    laenge = stationen[-1] + math.dist(pts[-1], pts[0])
    erster = (folge[-1] + 1) % n
    if len(folge) == 1:
        behalten = [(erster + k) % n for k in range(n)]
    else:
        weg = set(folge)
        behalten = [(erster + k) % n for k in range(n) if (erster + k) % n not in weg]
    if len(behalten) < 2:
        raise ValueError(tr("Zum Öffnen einen Punkt oder ein zusammenhängendes Stück wählen."))
    return [punkte[i] for i in behalten], stationen[erster], laenge


# ------------------------------------------------------------------ Überschneidung
def _abstaende(punkte, segmente_a, segmente_b):
    """Abstand jedes Punkts (m, 2) zu jeder Strecke a→b (n, 2): (m, n)."""
    ab = segmente_b - segmente_a
    l2 = np.maximum((ab ** 2).sum(1), 1e-12)
    ap = punkte[:, None, :] - segmente_a[None, :, :]
    t = np.clip((ap * ab[None]).sum(2) / l2[None], 0.0, 1.0)
    naechst = segmente_a[None] + t[..., None] * ab[None]
    return np.hypot(*(punkte[:, None, :] - naechst).transpose(2, 0, 1))


def _schnitte(a1, a2, b1, b2):
    """Echte Schnitte der Strecken a (m) mit b (n): (m, n)-Maske."""
    def kreuz(u, v):
        return u[..., 0] * v[..., 1] - u[..., 1] * v[..., 0]
    r, q = (a2 - a1)[:, None], (b2 - b1)[None]
    nenner = kreuz(r, q)
    d = b1[None] - a1[:, None]
    with np.errstate(divide="ignore", invalid="ignore"):
        t, u = kreuz(d, q) / nenner, kreuz(d, r) / nenner
    return (np.abs(nenner) > 1e-12) & (t > 0) & (t < 1) & (u > 0) & (u < 1)


def mindestabstand(raw):
    """Mittellinien näher als dieser Abstand: Fahrbahnränder samt Randstein berühren sich (Fahrbahnbreite + 2 m)."""
    fb = raw.get("fahrbahn") or {}
    if fb.get("breite_links") is not None or fb.get("breite_rechts") is not None:
        breite = float(fb.get("breite_links") or 6.0) + float(fb.get("breite_rechts") or 6.0)
    else:
        breite = float(fb.get("breite", 12.0))
    return breite + 2.0


def ueberschneidungen(punkte, stueck, abstand, geschlossen=False):
    """Stellen, an denen das Stück (Indizes ``stueck`` = (erster, letzter)) der übrigen Strecke näher als ``abstand``
    kommt oder sie kreuzt: Liste von Indizes im Stück (aufsteigend). Die Umgebung beider Anschlüsse zählt nicht,
    ebenso das freie Ende des Stücks am anderen offenen Ende (gewolltes Schließen)."""
    a, b = stueck
    p = _xz(punkte)
    n = len(p)
    if b - a < 1 or n - (b - a + 1) < 2:
        return []
    innen = np.arange(a, b + 1)
    rest = {i for i in range(n) if i < a or i > b}
    # Segmente der übrigen Strecke (ohne die Verbindung ins Stück)
    paare = [(i, i + 1) for i in range(n - 1) if i in rest and i + 1 in rest]
    if geschlossen and n - 1 in rest and 0 in rest:
        paare.append((n - 1, 0))
    if not paare:
        return []
    sa, sb = p[[i for i, _ in paare]], p[[j for _, j in paare]]
    # Anschlüsse: Punkt des Stücks am Anschluss, und das gegenüberliegende offene Ende samt freiem Stückende
    nahe = 2.0 * abstand
    if a == 0:              # vorne eingefügt: Anschluss am Stückende b, freies Ende a liegt evtl. am Streckenende
        anschluss_s, frei_s, anschluss_r, frei_r = p[b], p[a], p[b + 1], p[n - 1]
    else:                   # hinten angehängt: Anschluss am Stückanfang a, freies Ende b evtl. am Streckenanfang
        anschluss_s, frei_s, anschluss_r, frei_r = p[a], p[b], p[a - 1], p[0]
    def entfernt(x, ort):
        return np.hypot(*(x - ort).T)
    d = _abstaende(p[innen], sa, sb)
    seg_mitte = (sa + sb) / 2
    ausgenommen = ((entfernt(p[innen], anschluss_s) < nahe)[:, None] & (entfernt(seg_mitte, anschluss_r) < nahe)[None]) | \
                  ((entfernt(p[innen], frei_s) < nahe)[:, None] & (entfernt(seg_mitte, frei_r) < nahe)[None])
    treffer = ((d < abstand) & ~ausgenommen).any(1)
    if len(innen) > 1:
        kreuzt = _schnitte(p[innen[:-1]], p[innen[1:]], sa, sb)
        m = ((entfernt(p[innen[:-1]], anschluss_s) < nahe)[:, None] & (entfernt(seg_mitte, anschluss_r) < nahe)[None]) | \
            ((entfernt(p[innen[:-1]], frei_s) < nahe)[:, None] & (entfernt(seg_mitte, frei_r) < nahe)[None])
        k = (kreuzt & ~m).any(1)
        treffer[:-1] |= k
    return [int(i) for i in innen[treffer]]


def ueberstand_plan(punkte, auswahl, abstand, geschlossen):
    """„Überstand abschneiden“: Das gewählte Stück muss an einem offenen Ende liegen. Ab der ersten Überschneidung
    (vom Anschluss aus gesehen) bis zum freien Ende werden die Punkte entfernt. Rückgabe: (punkte, entfernt, versatz_m)."""
    n = len(punkte)
    folge = kette(auswahl, n, False)
    if geschlossen or folge is None or not (folge[0] == 0 or folge[-1] == n - 1) or len(folge) >= n - 1:
        raise ValueError(tr("„Überstand abschneiden“ braucht eine offene Strecke und ein gewähltes Stück an einem ihrer Enden."))
    a, b = folge[0], folge[-1]
    treffer = ueberschneidungen(punkte, (a, b), abstand)
    if not treffer:
        raise ValueError(tr("Das gewählte Stück überschneidet die Strecke nicht."))
    if a == 0:              # Anschluss bei b, freies Ende vorne
        schnitt = max(treffer)
        weg = list(range(0, schnitt + 1))
        neu = punkte[schnitt + 1:]
        versatz = -_laenge(punkte[:schnitt + 2])
    else:                   # Anschluss bei a, freies Ende hinten
        schnitt = min(treffer)
        weg = list(range(schnitt, n))
        neu = punkte[:schnitt]
        versatz = 0.0
    if len(neu) < 2:
        raise ValueError(tr("Nach dem Abschneiden blieben zu wenige Punkte übrig."))
    return neu, weg, versatz
