"""Ortsgebundene Reste des Trägers an die neue Strecke bringen (docs/traeger_reste.md, 13.11.2026).

Der Bau versetzt Boxenplätze, Garagen und Wiederholungskameras. Andere Ebenen des Trägers bleiben sonst an
Portimão-Orten stehen. Dieses Modul führt sie nach:

1. **Boxen** (``_box_abbildung``): Garagenzubehör (Regenblocker, Tropfen, Klangräume, Lichtsonden, Szenen an der Mauer),
   Siegerehrung und Zwischensequenzen. Jede Instanz gehört zur nächsten alten Box und behält ihre Lage zu dieser Box
   (dieselbe Abbildung wie ``spiel_welt.garagen_versetzen``).
2. **Strecke** (``_linien_abbildung``): Intro-Kameras mit ihren Kamerafahrten, Kunstkameras, Intro-Flagge und die
   Lichtsonden an der Strecke. Jede Gruppe wird starr versetzt: gleicher Rundenanteil, gleiche Lage zur Mittellinie.
3. **Klangverdecker** der Portimão-Bauten (Gebäude, Tribünen, Hügel) werden abgeschaltet (weit unter die Strecke).
4. **Trainingsprogramm** ``<code>.taprogram``: Kurven und Scheitel aus der neuen Streckenlogik.

Nur Lagematrizen und Kamerafahrt-Knoten ändern sich; Namen, Kennungen und der EventGraph bleiben.
"""
from __future__ import annotations

import copy
import re
from collections import Counter
from typing import Dict, List, Optional

import numpy as np

from ego import bxml, pkg
from . import spiel_kameras, spiel_welt

#: Instanzen dieser Ebenen folgen immer den Boxen (Siegerehrung, Zwischensequenzen, Zuschauerkamera an der Box)
BOX_IMMER = ("podium", "cutsceneplacement", "cutsceneplacementcareer", "cameras_spectator")
#: Instanzen dieser Ebenen folgen den Boxen, wenn sie höchstens ``BOX_RADIUS_M`` neben einer alten Box liegen
BOX_NAH = ("vfx", "lightprobes", "audio_occlusion", "audio_occlusion_next_gen", "audio_env")
BOX_RADIUS_M = 45.0
#: ``audio_env``: diese Namen gehören zur Boxengasse, auch weiter weg (Boxenhupe, Lautsprecher der Siegerehrung)
AUDIO_ENV_BOX = re.compile(r"^(audio_garage|pit_horn|tannoy_podium|parc_ferme)", re.IGNORECASE)
#: Ebenen, die der Strecke folgen; eine Gruppe je Kamera (mit Fahrt und Ziel) bzw. je Ebene
STRECKE_JE_KAMERA = ("cameras_introplacement", "art_cameras")
STRECKE_JE_EBENE = ("animations-objects",)
#: Übrige Instanzen dieser Ebenen folgen einzeln der Strecke bzw. werden abgeschaltet
STRECKE_REST = ("lightprobes",)
AUS_REST = ("audio_occlusion", "audio_occlusion_next_gen")
#: Instanzen unterhalb dieser Höhe sind im Träger schon versteckt (z. B. ``CutscenePlacementHospUpWorld`` bei −50 m)
VERSTECKT_Y = -20.0
#: Kameras und Sonden, die der Strecke folgen, liegen mindestens so hoch über der neuen Mittellinie
KAMERA_MINDESTHOEHE_M = 2.0
Y180 = np.diag([-1.0, 1.0, -1.0])
#: Trainingsprogramm: Tempoangaben je Scheitel wie in allen Einträgen des Trägers
TA_KENNUNG = "eaid://game_package/practice_programs/track_acclimatisation/%s.taprogram"
TA_KURVE_KURZ = 0.012        # Rundenanteil; kürzere Kurven bekommen nur einen Scheitel (Träger: 1–3 je Kurve)


class ResteFehler(ValueError):
    pass


def _kurzname(inst) -> str:
    return inst.name.split("$", 1)[-1]


def _box_abbildung(M, alt, neu, spiegeln):
    """Matrix einer Instanz von ihrer alten Box (Rahmen ``alt``) an die neue (``neu``); Lage zur Box bleibt."""
    (bpos, bR), (npos, nR) = alt, neu
    lokal = (M[3, :3] - bpos) @ bR.T
    rel = M[:3, :3] @ bR.T
    if spiegeln:
        lokal = lokal * np.array([-1.0, 1.0, -1.0])
        rel = rel @ Y180
    out = np.eye(4)
    out[:3, :3] = rel @ nR
    out[3, :3] = npos + lokal @ nR
    out[:, 3] = M[:, 3]
    return out


class _Starr:
    """Starre Abbildung einer Gruppe: gleicher Rundenanteil ihres Schwerpunkts, gleiche Lage zur Mittellinie."""

    def __init__(self, punkte, alt: spiel_kameras.Linie, neu: spiel_kameras.Linie):
        self.f = alt.naechster(np.mean(np.asarray(punkte, float), axis=0))[0]
        self.c0, self.F0 = alt.punkt(self.f)
        self.c1, self.F1 = neu.punkt(self.f)
        self.dy = 0.0

    def anheben(self, punkte, mindestens=None):
        """Gruppe so weit heben, dass kein Punkt tiefer als ``mindestens`` über der neuen Mittellinie liegt (der Träger ist
        hügelig: Was dort unter der Linie stand, läge auf einer flachen Strecke im Boden)."""
        mindestens = KAMERA_MINDESTHOEHE_M if mindestens is None else mindestens
        tiefster = min(float(self.punkt(q)[1]) for q in punkte)
        self.dy += max(0.0, float(self.c1[1]) + mindestens - tiefster)

    def punkt(self, q):
        return self.c1 + ((np.asarray(q, float) - self.c0) @ self.F0.T) @ self.F1 + np.array([0.0, self.dy, 0.0])

    def matrix(self, M):
        out = np.array(M, float)
        out[:3, :3] = (M[:3, :3] @ self.F0.T) @ self.F1
        out[3, :3] = self.punkt(M[3, :3])
        return out


def _spline_stamm(kennung: str) -> str:
    return re.sub(r"_target$", "", kennung.rsplit("/", 1)[-1].rsplit(".", 1)[0])


def _spline_verweise(daten: dict) -> List[str]:
    """Kennungen der Kamerafahrten, die eine Instanz nennt (eigene Fahrt, Quelle und Ziel einer Fahrtkamera)."""
    out = []
    for komp, feld in (("Spline", "Spline asset"), ("CameraBehaviour_SplineFollower", "Source spline file"),
                       ("CameraBehaviour_SplineFollower", "Target spline file")):
        w = (daten.get(komp) or {}).get(feld)
        if isinstance(w, str) and w:
            out.append(w)
    return out


def _knoten(doc):
    return [k for sp in doc.root.elements for k in sp.elements if k.name == "Knot" and k.get("position")]


def _spline_absolut(doc) -> bool:
    rel = next((sp.get("relative_pos") for sp in doc.root.elements if sp.get("relative_pos")), "0,0,0")
    return not any(abs(float(x)) > 1e-6 for x in rel.split(","))


def _schreiben(r, p):
    p.pruefen()
    r.set_fragment_data(0, pkg.schreiben(p))


def _linien(zustand, lg):
    alt_h, _alt_b = spiel_kameras._linien_aus_strecke(zustand.schreiber.original)
    sp = lg["spielformate"]["TrackSpace"]["splines"]
    return alt_h, spiel_kameras.Linie(next(s for s in sp if s["closed"])["position"], True)


# ----------------------------------------------------------------------------- Ebenen
def ebenen_versetzen(archiv, zustand, lg) -> dict:
    """Boxen- und Streckenebenen des Trägers nachführen, Klangverdecker abschalten; Bericht je Ebene."""
    alt, neu, spiegeln = spiel_welt.boxrahmen(zustand, lg)
    alt_nr = sorted(alt)
    alt_pos = np.array([alt[k][0] for k in alt_nr])
    linie_alt, linie_neu = _linien(zustand, lg)
    res = spiel_welt.layer_ressourcen(archiv, zustand.code)
    bericht: Dict[str, dict] = {}
    splines: Dict[str, _Starr] = {}

    def naechste_box(M):
        d = np.linalg.norm(alt_pos[:, (0, 2)] - M[3, (0, 2)], axis=1)
        k = int(np.argmin(d))
        return alt_nr[k], float(d[k])

    for kurz in dict.fromkeys(BOX_IMMER + BOX_NAH + STRECKE_JE_KAMERA + STRECKE_JE_EBENE):
        r = res.get(kurz)
        if r is None:
            continue
        p = pkg.lesen(r.fragments[0].read())
        inst = [(i, np.asarray(i.matrix, float).reshape(4, 4)) for i in p.instanzen() if i.matrix is not None]
        z = Counter()
        gruppen: Dict[str, list] = {}
        for i, M in inst:
            if M[3, 1] < VERSTECKT_Y:
                z["versteckt_belassen"] += 1
                continue
            nr, abstand = naechste_box(M)
            an_box = kurz in BOX_IMMER or (kurz in BOX_NAH and abstand <= BOX_RADIUS_M) or (
                kurz == "audio_env" and AUDIO_ENV_BOX.match(_kurzname(i)))
            if an_box:
                pkg.instanz_matrix_setzen(p, i, [float(v) for v in _box_abbildung(M, alt[nr], neu[nr], spiegeln).ravel()])
                z["box"] += 1
            elif kurz in AUS_REST:
                M = M.copy()
                M[3, 1] -= spiel_kameras.AUS_TIEFE_M
                pkg.instanz_matrix_setzen(p, i, [float(v) for v in M.ravel()])
                z["aus"] += 1
            elif kurz in STRECKE_JE_EBENE:
                gruppen.setdefault(kurz, []).append((i, M, []))
            elif kurz in STRECKE_JE_KAMERA or kurz in STRECKE_REST:
                verweise = _spline_verweise(i.export()["daten"].get("overrides", {}))
                schluessel = _spline_stamm(verweise[0]) if verweise else i.name
                gruppen.setdefault(schluessel, []).append((i, M, verweise))
            else:
                z["belassen"] += 1
        for schluessel, mitglieder in gruppen.items():
            punkte = [M[3, :3] for _i, M, _v in mitglieder]
            docs = {}
            for _i, _M, verweise in mitglieder:
                for kennung in verweise:
                    sr = archiv.find(kennung)
                    if sr is None or kennung in docs or kennung in splines:
                        continue
                    doc = bxml.decode(sr.fragments[0].read())
                    if _spline_absolut(doc):
                        docs[kennung] = (sr, doc)
                        punkte += [[float(x) for x in k.get("position").split(",")] for k in _knoten(doc)]
            starr = _Starr(punkte, linie_alt, linie_neu)
            starr.anheben(punkte)
            for i, M, _v in mitglieder:
                pkg.instanz_matrix_setzen(p, i, [float(v) for v in starr.matrix(M).ravel()])
                z["strecke"] += 1
            for kennung, (sr, doc) in docs.items():
                for k in _knoten(doc):
                    q = starr.punkt([float(x) for x in k.get("position").split(",")])
                    k.set("position", "%.4f,%.4f,%.4f" % tuple(q))
                bxml.write_resource(sr, doc)
                splines[kennung] = starr
                z["kamerafahrten"] += 1
        if z.get("box") or z.get("aus") or z.get("strecke"):
            _schreiben(r, p)
        bericht[kurz] = dict(z)
    return {"spiegeln": bool(spiegeln), "ebenen": bericht, "kamerafahrten": len(splines)}


# ----------------------------------------------------------------------------- Trainingsprogramm
def _hauptkurven(lg) -> List[dict]:
    """Hauptkurven der Logik in Fahrtrichtung: ``{"anfang", "ende", "scheitel": [Rundenanteile]}``."""
    ai = lg["spielformate"]["AISplineData"]
    gates = {int(g["id"]): g for g in ai["gates"]}

    def haupt(l):
        return "pit" not in gates[int(l["gate_id"])]["name"]

    def prog(l):
        return float(gates[int(l["gate_id"])]["progress"]) % 1.0
    anfaenge = sorted(prog(l) for l in ai["corner_start_lines"] if haupt(l))
    enden = sorted(prog(l) for l in ai["corner_end_lines"] if haupt(l))
    bremsen = sorted(prog(l) for l in ai["brake_lines"] if haupt(l))
    kurven = []
    for a in anfaenge:
        if not enden:
            break
        e = min(enden, key=lambda x: (x - a) % 1.0)
        weite = (e - a) % 1.0
        innen = [b for b in bremsen if (b - a) % 1.0 <= weite]
        mitte = (a + weite / 2.0) % 1.0
        # lange Kurven tragen mehrere Bremslinien: die mittlere steht für den Scheitel
        kurven.append({"anfang": a, "ende": e, "weite": weite,
                       "scheitel": sorted(innen, key=lambda b: abs((b - a) % 1.0 - weite / 2.0)) or [mitte]})
    return kurven


def _karte(linie: spiel_kameras.Linie):
    """Weltpunkt → Kartenpunkt 0…1 für ``UIMarker``: Strecke mittig, Rand 8 %, +X rechts, +Z oben.

    Die Kartenlage des Trägers ist nicht entschlüsselt (Portimão: gedreht, gleicher Maßstab in x und y, gespiegelt);
    hier ohne Drehung, mit derselben Händigkeit."""
    xz = linie.p[:, (0, 2)]
    lo, hi = xz.min(0), xz.max(0)
    s = 0.84 / max(float((hi - lo).max()), 1.0)
    mitte = (lo + hi) / 2.0
    return lambda q: (0.5 + (q[0] - mitte[0]) * s, 0.5 - (q[2] - mitte[1]) * s)


def trainingsprogramm(archiv, zustand, lg) -> Optional[dict]:
    """``CornerData`` des Trainingsprogramms aus den Hauptkurven der neuen Strecke; None, wenn der Träger keines hat."""
    r = archiv.find(TA_KENNUNG % zustand.code)
    if r is None:
        return None
    doc = bxml.read_resource(r)
    alte = [e for e in doc.root.children if isinstance(e, bxml.Element) and e.name == "Corner"]
    kurven = _hauptkurven(lg)
    if not alte or not kurven:
        return {"kurven": 0, "vorher": len(alte), "unveraendert": True}
    muster = alte[0]
    apex_muster = muster.find("Apex")
    ui_muster = muster.find("UIMarker")
    text = muster.text
    if apex_muster is None or ui_muster is None or not re.match(r"^\s*1\s*$", text):
        raise ResteFehler("Practice programme has an unexpected layout")
    _alt, linie_neu = _linien(zustand, lg)
    karte = _karte(linie_neu)
    neue = []
    for nr, k in enumerate(kurven, 1):
        if k["weite"] < TA_KURVE_KURZ:
            scheitel = k["scheitel"][:1]
        else:
            scheitel = sorted({k["anfang"], k["scheitel"][0], k["ende"]}, key=lambda f: (f - k["anfang"]) % 1.0)
        el = bxml.Element("Corner", dict(muster.attrib), [text.replace("1", str(nr), 1)])
        for f in scheitel:
            a = copy.deepcopy(apex_muster)
            a.find("Position").set("progressDelta", "%.6f" % f)
            el.append(a)
        mitte = linie_neu.punkt((k["anfang"] + k["weite"] / 2.0) % 1.0)[0]
        ui = copy.deepcopy(ui_muster)
        x, y = karte(mitte)
        ui.find("Position").set("x", "%.6f" % min(max(x, 0.0), 1.0))
        ui.find("Position").set("y", "%.6f" % min(max(y, 0.0), 1.0))
        el.append(ui)
        neue.append(el)
    kinder, eingesetzt = [], False
    for c in doc.root.children:
        if isinstance(c, bxml.Element) and c.name == "Corner":
            if not eingesetzt:
                kinder += neue
                eingesetzt = True
            continue
        kinder.append(c)
    doc.root.children = kinder
    bxml.write_resource(r, doc)
    return {"kurven": len(neue), "vorher": len(alte), "scheitel": sum(len(e.findall("Apex")) for e in neue)}


def reste_anpassen(archiv, zustand, lg, protokoll=None) -> dict:
    """Alle Schritte dieses Moduls; muss nach ``logik_vorbereiten`` (neue Boxenplätze, begrenzte Kurven) laufen."""
    bericht = ebenen_versetzen(archiv, zustand, lg)
    bericht["trainingsprogramm"] = trainingsprogramm(archiv, zustand, lg)
    if protokoll is not None:
        protokoll["traeger_reste"] = bericht
    return bericht
