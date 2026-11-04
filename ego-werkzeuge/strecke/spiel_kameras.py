"""Kameras des Trägers für Wiederholungen und TV-Bilder auf die neue Strecke versetzen (4.11.2026).

Untersuchung: ``docs/mapeditor/kameras_wiederholung.md``. Wiederholungskameras schalten über den EventGraph um, sobald
ein Auto eine Auslösezone (``TriggerZoneBox`` quer über der Strecke) durchfährt; der EventGraph spricht die Zone über
ihre ``uniqueid`` und die Kamera über ihren Namen an. Bleiben Zonen und Kameras an den Stellen des Trägers, schaltet die
Regie auf einer neuen Strecke nie um.

Vorgehen ohne Änderung am EventGraph: jede Instanz der Kameraebenen wird relativ zur Trägerstrecke erfasst
(Rundenanteil, Lage und Achsen im Streckenrahmen quer/hoch/längs) und an derselben Stelle der neuen Strecke wieder
eingesetzt. Ebenso die Weltpunkte der Kameras (Zoom-, Fokus-, Start-/Endpunkte), die Meterschwellen
(``Begin/End progress threshold``) und die Knoten der Kamerafahrten (``splines/*.spline``, absolute Koordinaten).
Ebenen der Boxengasse beziehen sich auf die Boxenlinie.

Anpassungen je Gruppe (Name ohne ``_trigger``/``_cam…``, z. B. ``s07a``) aus der Definition ``kameras.gruppen``:
``aus`` (Auslösezone 1 km unter die Strecke, die Kamera schaltet nie), ``verschiebung_m`` (entlang der Strecke),
``seitlich_m`` (Kameras weiter weg von der Strecke, negativ näher), ``hoehe_m`` (Kameras höher).

Gemeinsam für Bau (``kameras_versetzen``) und Editor (``lage_erfassen`` einmal je Träger, ``plan`` für die Anzeige).
"""

from __future__ import annotations

import math
import os
import re
import struct
from typing import Dict, List, Optional

import numpy as np

#: Kameraebenen → Bezugslinie (``haupt`` = Hauptring, ``box`` = Boxenlinie)
KAMERA_LAYER = {"cameras_replay_main": "haupt", "cameras_replay_safety": "haupt", "cameras_hero": "haupt",
                "cameras_replay_pitbox": "box", "cameras_pitstop": "box"}
#: Weltpunkte in Kamerakomponenten (Komponente, Feld)
WELTPUNKTE = tuple([("CameraModifier_ProgressBasedStaticZoom", "Progress point %d" % k) for k in range(1, 6)] + [
    ("CameraBehaviour_ProgressBasedTargetInhibitor", "Begin focus point"),
    ("CameraBehaviour_ProgressBasedTargetInhibitor", "End focus point"),
    ("CameraBehaviour_ProgressBasedSplineFollower", "Start point"),
    ("CameraBehaviour_ProgressBasedSplineFollower", "End point")])
#: Streckenmeter (ab Ziellinie) in Kamerakomponenten
SCHWELLEN = (("CameraBehaviour_ProgressBasedTargetInhibitor", "Begin progress threshold"),
             ("CameraBehaviour_ProgressBasedTargetInhibitor", "End progress threshold"))
#: Zonen und Kameras, die je Gruppe zusammengehören
ZONEN = ("TriggerZoneBox", "TriggerZoneSphere")
KAMERAS = ("ReplayCam", "StaticCamera", "ProgressBasedSplineCamera", "SplineCamera")
#: Zonen dieser Ebenen setzt der Plan auf die Mittellinie (im Portimão-Träger liegen 9 von 39 Wiederholungszonen 20–60 m
#: daneben und erreichen eine neue Strecke nicht mehr) und macht sie mindestens so breit
ZONEN_AUF_MITTE = ("cameras_replay_main", "cameras_hero")
ZONEN_MINDESTBREITE_M = 40.0
#: Abgeschaltete Zonen liegen so weit unter der Strecke
AUS_TIEFE_M = 1000.0
UP = np.array([0.0, 1.0, 0.0])


class KameraFehler(ValueError):
    pass


def gruppe_von(name: str) -> str:
    """``cameras_replay_main$s07a_cam_heli`` → ``s07a``; ohne Muster der ganze Kurzname."""
    kurz = name.split("$", 1)[-1]
    m = re.match(r"^(.*?)(?:_hero)?(_trigger.*|_cam.*|_spline.*)$", kurz, re.IGNORECASE)
    return m.group(1) if m and m.group(1) else kurz


# ----------------------------------------------------------------------------- Linien
class Linie:
    """Polylinie (N×3, Weltkoordinaten) mit Bogenlänge; ``punkt(f)`` → (Punkt, Rahmen 3×3 quer/hoch/längs)."""

    def __init__(self, punkte, geschlossen: bool):
        p = np.asarray(punkte, float)
        if len(p) < 2:
            raise KameraFehler("Line needs at least 2 points")
        self.p = np.vstack([p, p[:1]]) if geschlossen else p
        self.geschlossen = geschlossen
        d = np.linalg.norm(np.diff(self.p, axis=0), axis=1)
        self.kum = np.r_[0.0, np.cumsum(d)]
        self.laenge = float(self.kum[-1])

    def punkt(self, f: float):
        f = f % 1.0 if self.geschlossen else min(max(f, 0.0), 1.0)
        s = f * self.laenge
        i = int(np.clip(np.searchsorted(self.kum, s, side="right") - 1, 0, len(self.p) - 2))
        seg = self.kum[i + 1] - self.kum[i]
        t = (s - self.kum[i]) / seg if seg > 0 else 0.0
        a, b = self.p[i], self.p[i + 1]
        return a + (b - a) * t, rahmen(b - a)

    def naechster(self, q):
        """(Rundenanteil, Abstand in der Draufsicht) des nächsten Punkts zu ``q``."""
        a, b = self.p[:-1], self.p[1:]
        ab = (b - a)[:, [0, 2]]
        l2 = np.maximum((ab * ab).sum(1), 1e-12)
        t = np.clip(((np.asarray(q, float)[[0, 2]] - a[:, [0, 2]]) * ab).sum(1) / l2, 0.0, 1.0)
        d = np.linalg.norm(a[:, [0, 2]] + t[:, None] * ab - np.asarray(q, float)[[0, 2]], axis=1)
        i = int(np.argmin(d))
        return float((self.kum[i] + t[i] * (self.kum[i + 1] - self.kum[i])) / self.laenge), float(d[i])


def rahmen(tangente) -> np.ndarray:
    """Zeilen: quer (rechts), hoch, längs – aus der waagerechten Tangente."""
    t = np.array([tangente[0], 0.0, tangente[2]], float)
    n = np.linalg.norm(t)
    t = t / n if n > 1e-9 else np.array([0.0, 0.0, 1.0])
    rechts = np.array([-t[2], 0.0, t[0]])
    return np.vstack([rechts, UP, t])


def _relativ(linie: Linie, q, f=None):
    if f is None:
        f, _ = linie.naechster(q)
    c, F = linie.punkt(f)
    return f, ((np.asarray(q, float) - c) @ F.T).tolist()


def _absolut(linie: Linie, f, lokal):
    c, F = linie.punkt(f)
    return c + np.asarray(lokal, float) @ F, F


# ----------------------------------------------------------------------------- Erfassen
def _linien_aus_strecke(st):
    """Haupt- und Boxenlinie einer ``ego.streckenlogik.Strecke``."""
    haupt = Linie(st.hauptlinie.punkte, True)
    box = next((s for s in st.splines.values() if not s.geschlossen), None)
    return haupt, (Linie(box.punkte, False) if box is not None else None)


def _kamera_layer(archiv, code):
    from . import spiel_welt
    res = spiel_welt.layer_ressourcen(archiv, code)
    return {k: res[k] for k in KAMERA_LAYER if k in res}


def lage_erfassen(archiv, code: str, strecke=None) -> dict:
    """Kameraebenen des Trägers relativ zu seiner Strecke (JSON-fähig, für Bau und Editor-Zwischenspeicher)."""
    from ego import bxml, pkg, streckenlogik
    st = strecke if strecke is not None else streckenlogik.lade(archiv, code)
    haupt, box = _linien_aus_strecke(st)
    out = {"schema": 2, "code": code, "laenge_haupt": haupt.laenge, "laenge_box": box.laenge if box else None,
           "instanzen": [], "splines": {}, "verknuepfung": {}}
    spline_assets = set()
    zonen_uid, kamera_je_name = {}, {}
    for kurz, r in _kamera_layer(archiv, code).items():
        bezug = KAMERA_LAYER[kurz] if box is not None else "haupt"
        linie = haupt if bezug == "haupt" else box
        p = pkg.lesen(r.fragments[0].read())
        for i in p.instanzen():
            m = i.matrix
            if m is None:
                continue
            M = np.asarray(m, float).reshape(4, 4)
            f, lokal = _relativ(linie, M[3, :3])
            _c, F = linie.punkt(f)
            e = {"layer": kurz, "name": i.name, "gruppe": gruppe_von(i.name), "template": i._wert("template"),
                 "bezug": bezug, "f": f, "lokal": lokal, "achsen": (M[:3, :3] @ F.T).tolist(), "punkte": {},
                 "schwellen": {}}
            daten = i.export()["daten"].get("overrides", {})
            if e["template"] in ZONEN:
                zonen_uid[i.uniqueid] = i.name
                groesse = (daten.get("StaticPhysics") or {}).get("size")
                if isinstance(groesse, list) and len(groesse) == 3:
                    e["groesse"] = [float(x) for x in groesse]
            else:
                kamera_je_name.setdefault(i.name.split("$", 1)[-1], i.name)
                anzeige = (daten.get("Name") or {}).get("name")
                if isinstance(anzeige, str):
                    kamera_je_name.setdefault(anzeige, i.name)
            for komp, feld in WELTPUNKTE:
                w = (daten.get(komp) or {}).get(feld)
                if isinstance(w, list) and len(w) == 3:
                    e["punkte"]["%s/%s" % (komp, feld)] = _relativ(linie, w)
            for komp, feld in SCHWELLEN:
                w = (daten.get(komp) or {}).get(feld)
                if isinstance(w, (int, float)):
                    e["schwellen"]["%s/%s" % (komp, feld)] = float(w) / haupt.laenge
            asset = (daten.get("Spline") or {}).get("Spline asset")
            if asset:
                e["spline_asset"] = asset
                spline_assets.add((asset, bezug))
            out["instanzen"].append(e)
    # Zone → Kameras aus dem EventGraph (ego/eventgraph.py): welche Kameras eine Zone schaltet, oft zufällig eine von mehreren
    from ego import eventgraph
    try:
        g = eventgraph.aus_archiv(archiv, code)
    except eventgraph.EventGraphFehler:
        g = None
    if g is not None:
        for woid, namen in g.kameras_je_weltobjekt().items():
            if woid in zonen_uid:
                ziele = [kamera_je_name[n] for n in namen if n in kamera_je_name]
                if ziele:
                    out["verknuepfung"][zonen_uid[woid]] = ziele
    for asset, bezug in sorted(spline_assets):
        res = archiv.find(asset)
        if res is None:
            continue
        linie = haupt if bezug == "haupt" else box
        doc = bxml.decode(res.fragments[0].read())
        knoten = []
        for sp in doc.root.elements:
            for k in sp.elements:
                if k.name == "Knot" and k.get("position"):
                    q = [float(x) for x in k.get("position").split(",")]
                    knoten.append(_relativ(linie, q))
        rel = next((sp.get("relative_pos") for sp in doc.root.elements if sp.get("relative_pos")), "0,0,0")
        out["splines"][asset] = {"bezug": bezug, "knoten": knoten,
                                 "relativ": any(abs(float(x)) > 1e-6 for x in rel.split(","))}
    return out


# ----------------------------------------------------------------------------- Plan
def _gruppen_einstellungen(einstellungen) -> Dict[str, dict]:
    g = (einstellungen or {}).get("gruppen") or {}
    return {str(k): (v or {}) for k, v in g.items()}


def plan(lage: dict, haupt: Linie, box: Optional[Linie], einstellungen=None) -> List[dict]:
    """Neue Lage jeder Instanz: ``{"index", "layer", "name", "gruppe", "template", "position", "matrix" (3×3 Achsen),
    "punkte": {feld: xyz}, "schwellen": {feld: m}, "aus"}``."""
    gruppen = _gruppen_einstellungen(einstellungen)
    objekte = (einstellungen or {}).get("objekte") or {}
    anker = _kamera_anker(lage, haupt, gruppen, objekte)
    out = []
    for k, e in enumerate(lage["instanzen"]):
        linie = haupt if (e["bezug"] == "haupt" or box is None) else box
        g = gruppen.get(e["gruppe"], {}) if e["bezug"] == "haupt" else {}
        df = float(g.get("verschiebung_m", 0.0) or 0.0) / linie.laenge
        if e["name"] in anker:              # Kamera an ihre auslösende Zone gebunden (Abstand in Metern wie im Träger)
            df = anker[e["name"]] - e["f"]
        lokal = np.asarray(e["lokal"], float).copy()
        hand = objekte.get(e["name"]) if e["bezug"] == "haupt" else None
        if hand and hand.get("s") is not None:   # Hand oder Automatik: Station ab Ziel (m), Kameras auch quer (m)
            df = float(hand["s"]) / haupt.laenge - e["f"]
            if e["template"] not in ZONEN:
                lokal = np.array([float(hand.get("quer", lokal[0])), lokal[1], 0.0])
        ist_kamera = e["template"] not in ZONEN
        if ist_kamera:
            seitlich = float(g.get("seitlich_m", 0.0) or 0.0)
            if seitlich:
                lokal[0] += math.copysign(seitlich, lokal[0] if lokal[0] else 1.0)
            lokal[1] += float(g.get("hoehe_m", 0.0) or 0.0)
        auf_mitte = not ist_kamera and e["layer"] in ZONEN_AUF_MITTE and e["template"] == "TriggerZoneBox"
        if auf_mitte:
            lokal[0] = 0.0
        aus = bool(g.get("aus")) and not ist_kamera
        if aus:
            lokal[1] -= AUS_TIEFE_M
        pos, F = _absolut(linie, e["f"] + df, lokal)
        achsen = np.asarray(e["achsen"], float) @ F
        groesse = None
        if auf_mitte and e.get("groesse"):
            groesse = list(e["groesse"])
            norm = np.linalg.norm(achsen, axis=1)
            quer_achse = int(np.argmax(np.abs(achsen @ F[0]) / np.maximum(norm, 1e-9)))
            groesse[quer_achse] = max(groesse[quer_achse], ZONEN_MINDESTBREITE_M / max(norm[quer_achse], 1e-9))
            if groesse == e["groesse"]:
                groesse = None
        punkte = {}
        for feld, (f, l) in e["punkte"].items():
            punkte[feld] = _absolut(linie, f + df, l)[0].tolist()
        schwellen = {feld: ((f + (df if linie is haupt else 0.0)) % 1.0) * haupt.laenge
                     for feld, f in e["schwellen"].items()}
        out.append({"index": k, "layer": e["layer"], "name": e["name"], "gruppe": e["gruppe"],
                    "kameras": (lage.get("verknuepfung") or {}).get(e["name"], []), "groesse": groesse,
                    "template": e["template"], "bezug": e["bezug"], "position": pos.tolist(),
                    "achsen": achsen.tolist(), "punkte": punkte, "schwellen": schwellen, "aus": aus,
                    "spline_asset": e.get("spline_asset"), "df": df, "quer": F[0].tolist(),
                    "s": ((e["f"] + df) % 1.0) * linie.laenge if linie is haupt else None})
    return out


#: Kameras höchstens so weit (m, entlang der Strecke) vor ihrer Zone gelten noch als „davor“
ANKER_VOR_M = 100.0


def _kamera_anker(lage: dict, haupt: Linie, gruppen, objekte=None) -> Dict[str, float]:
    """Neuer Rundenanteil je Kamera der Hauptstrecke, die eine Zone laut EventGraph schaltet.

    Die Regie schaltet beim Durchfahren der Zone auf diese Kamera; im Träger steht sie meist 50–300 m dahinter. Werden
    Zone und Kamera unabhängig nach Rundenanteil versetzt, liegen auf einer anders geformten Strecke ganz andere Teile
    dazwischen (Spieltest 4.11.2026: Schnitt kurz nach dem Start auf eine Kamera an Turn 3). Deshalb behält die Kamera
    ihren Abstand in Metern zur nächstgelegenen auslösenden Zone davor; ihr Querabstand und ihre Höhe bleiben."""
    L_alt = float(lage.get("laenge_haupt") or haupt.laenge)
    je_name = {e["name"]: e for e in lage["instanzen"]}
    beste: Dict[str, tuple] = {}
    for zname, kameras in (lage.get("verknuepfung") or {}).items():
        z = je_name.get(zname)
        if z is None or z["bezug"] != "haupt":
            continue
        g = gruppen.get(z["gruppe"], {})
        f_zone = z["f"] + float(g.get("verschiebung_m", 0.0) or 0.0) / haupt.laenge
        hand = (objekte or {}).get(zname)
        if hand and hand.get("s") is not None:
            f_zone = float(hand["s"]) / haupt.laenge
        for kname in kameras:
            kam = je_name.get(kname)
            if kam is None or kam["bezug"] != "haupt":
                continue
            d = ((kam["f"] - z["f"]) % 1.0) * L_alt              # Meter hinter der Zone (Träger)
            if d > L_alt - ANKER_VOR_M:
                d -= L_alt                                        # knapp davor
            if kname not in beste or abs(d) < abs(beste[kname][0]):
                beste[kname] = (d, f_zone)
    return {k: f + d / haupt.laenge for k, (d, f) in beste.items()}


# ----------------------------------------------------------------------------- Automatik
#: Automatik: Zone so weit vor dem Kurvenscheitel, Kameras so weit hinter der Zone (je weitere Kamera +60 m), so weit
#: neben der Mittellinie (Kurvenaußenseite)
AUTO_ZONE_VOR_SCHEITEL_M = 150.0
AUTO_KAMERA_HINTER_ZONE_M = 110.0
AUTO_KAMERA_ABSTAND_M = 60.0
AUTO_KAMERA_QUER_M = 28.0
AUTO_KURVE_RADIUS_M = 250.0


def _kruemmung(linie: Linie, schritt=5.0):
    """(Stationen, vorzeichenbehaftete Krümmung in der Draufsicht) entlang der geschlossenen Linie, geglättet."""
    n = max(16, int(linie.laenge / schritt))
    s = np.arange(n) * linie.laenge / n
    p = np.array([linie.punkt(x / linie.laenge)[0] for x in s])[:, [0, 2]]
    h = 4
    a, b = np.roll(p, h, axis=0), np.roll(p, -h, axis=0)
    t1, t2 = p - a, b - p
    kreuz = t1[:, 0] * t2[:, 1] - t1[:, 1] * t2[:, 0]
    winkel = np.arctan2(kreuz, (t1 * t2).sum(1))
    k = winkel / np.maximum(np.linalg.norm(t1, axis=1) + np.linalg.norm(t2, axis=1), 1e-6) * 2.0
    g = np.ones(5) / 5.0
    k = np.convolve(np.r_[k[-2:], k, k[:2]], g, mode="valid")
    return s, k


def automatisch(lage: dict, haupt: Linie) -> Dict[str, dict]:
    """Neue Lage aller Zonen der Hauptstrecke mit Kameras und ihrer Kameras für die aktuelle Strecke:
    ``{Instanzname: {"s": Station ab Ziel}}`` für Zonen, ``{"s", "quer"}`` für Kameras.

    Zonen kommen etwa 150 m vor die Kurvenscheitel (Reihenfolge wie im Träger), überzählige Zonen gleichmäßig in die
    größten Lücken; jede Kamera steht 110 m (+60 m je weitere Kamera) hinter ihrer Zone an der Kurvenaußenseite."""
    L = haupt.laenge
    je_name = {e["name"]: e for e in lage["instanzen"]}
    zonen = sorted([je_name[z] for z in (lage.get("verknuepfung") or {}) if z in je_name
                    and je_name[z]["bezug"] == "haupt" and je_name[z]["template"] == "TriggerZoneBox"],
                   key=lambda e: e["f"])
    if not zonen:
        return {}
    s, k = _kruemmung(haupt)
    # Kurvenscheitel: lokale Maxima der Krümmung über 1/R, zusammengefasst innerhalb 80 m
    stark = np.abs(k) > 1.0 / AUTO_KURVE_RADIUS_M
    scheitel = []
    for i in np.flatnonzero(stark):
        if abs(k[i]) >= abs(k[i - 1]) and abs(k[i]) >= abs(k[(i + 1) % len(k)]):
            if scheitel and (s[i] - scheitel[-1][0]) < 80.0:
                if abs(k[i]) > scheitel[-1][1]:
                    scheitel[-1] = (s[i], abs(k[i]))
                continue
            scheitel.append((s[i], abs(k[i])))
    n = len(zonen)
    if len(scheitel) > n:
        scheitel = sorted(sorted(scheitel, key=lambda x: -x[1])[:n])
    ziele = sorted(((a - AUTO_ZONE_VOR_SCHEITEL_M) % L) for a, _ in scheitel)
    while len(ziele) < n:                                 # größte Lücke halbieren
        if not ziele:
            ziele = [0.0]
            continue
        luecken = [((ziele[(i + 1) % len(ziele)] - ziele[i]) % L or L, i) for i in range(len(ziele))]
        g, i = max(luecken)
        ziele.append((ziele[i] + g / 2.0) % L)
        ziele.sort()
    # Reihenfolge des Trägers beibehalten: Drehung der Zuordnung mit der kleinsten Verschiebung
    alt = np.array([z["f"] * L for z in zonen])
    beste = min(range(n), key=lambda r: sum(abs(((ziele[(i + r) % n] - alt[i]) + L / 2) % L - L / 2) for i in range(n)))
    out: Dict[str, dict] = {}
    vergeben = set()
    for i, z in enumerate(zonen):
        sz = ziele[(i + beste) % n]
        out[z["name"]] = {"s": round(float(sz), 1)}
        j = 0
        for kname in lage["verknuepfung"].get(z["name"], []):
            kam = je_name.get(kname)
            if kam is None or kname in vergeben or kam["bezug"] != "haupt" or kam["template"] not in KAMERAS:
                continue
            vergeben.add(kname)
            sk = (sz + AUTO_KAMERA_HINTER_ZONE_M + AUTO_KAMERA_ABSTAND_M * j) % L
            idx = int(round(sk / L * len(s))) % len(s)
            seite = -np.sign(k[idx]) if abs(k[idx]) > 1.0 / 1000.0 else (np.sign(kam["lokal"][0]) or 1.0)
            # Vorzeichen: Rahmen quer = rechts; k > 0 = Kurve zur rechten Seite → außen = links (negativ)
            out[kname] = {"s": round(float(sk), 1), "quer": round(float(seite) * AUTO_KAMERA_QUER_M, 1)}
            j += 1
    return out


def gruppen_uebersicht(lage: dict) -> List[dict]:
    """Gruppen der Hauptlinie (nur diese sind im Editor einstellbar): Name, Anzahl Zonen/Kameras, Rundenanteil."""
    g = {}
    for e in lage["instanzen"]:
        if e["bezug"] != "haupt":
            continue
        x = g.setdefault(e["gruppe"], {"gruppe": e["gruppe"], "zonen": 0, "kameras": 0, "f": None,
                                       "layer": set()})
        x["layer"].add(e["layer"])
        if e["template"] in ZONEN:
            x["zonen"] += 1
            x["f"] = e["f"] if x["f"] is None else x["f"]
        elif e["template"] in KAMERAS:
            x["kameras"] += 1
    out = []
    for x in g.values():
        if not x["zonen"]:
            continue                       # Kameras ohne eigene Zone (Startbilder) bleiben automatisch
        x["layer"] = sorted(x["layer"])
        out.append(x)
    return sorted(out, key=lambda x: x["f"])


# ----------------------------------------------------------------------------- Bau
def _setzen(p, inst, komp, feld, werte):
    from ego import pkg
    fe = pkg._pfad(p, inst.block, "overrides", komp, feld)
    tab = p.tabelle(fe.wert)
    if tab.datentyp == b"vec3":
        alt = tab.daten[fe.wert.index * tab.elementgroesse:(fe.wert.index + 1) * tab.elementgroesse]
        roh = struct.pack("<3f", *[float(x) for x in werte]) + alt[12:]
    elif tab.datentyp == b"fp32":
        roh = struct.pack("<f", float(werte))
    else:
        raise KameraFehler("Field %s/%s has an unexpected type %r" % (komp, feld, tab.datentyp))
    fe.wert = p.neuer_wert(fe.wert, roh)


def kameras_versetzen(archiv, zustand, lg, einstellungen=None, protokoll=None) -> dict:
    """Kameraebenen des Trägers an die neue Strecke (``lg``: platzierte Logik mit TrackSpace) versetzen."""
    from ego import bxml, pkg
    if einstellungen is not None and einstellungen.get("mitfuehren") is False:
        return {"mitgefuehrt": False}
    lage = lage_erfassen(archiv, zustand.code, zustand.schreiber.original)
    sp = lg["spielformate"]["TrackSpace"]["splines"]
    haupt = Linie(next(s for s in sp if s["closed"])["position"], True)
    offen = [s for s in sp if not s["closed"]]
    box = Linie(offen[0]["position"], False) if offen else None
    neu = plan(lage, haupt, box, einstellungen)
    je_layer: Dict[str, Dict[str, dict]] = {}
    for e in neu:
        je_layer.setdefault(e["layer"], {})[e["name"]] = e
    bericht = {"mitgefuehrt": True, "layer": {}, "splines": 0, "aus": sorted({e["gruppe"] for e in neu if e["aus"]})}
    for kurz, r in _kamera_layer(archiv, zustand.code).items():
        p = pkg.lesen(r.fragments[0].read())
        n = 0
        for i in p.instanzen():
            e = je_layer.get(kurz, {}).get(i.name)
            if e is None or i.matrix is None:
                continue
            M = np.asarray(i.matrix, float).reshape(4, 4)
            M[:3, :3] = np.asarray(e["achsen"], float)
            M[3, :3] = e["position"]
            pkg.instanz_matrix_setzen(p, i, [float(v) for v in M.ravel()])
            for feld, w in e["punkte"].items():
                _setzen(p, i, *feld.split("/", 1), w)
            for feld, w in e["schwellen"].items():
                _setzen(p, i, *feld.split("/", 1), w)
            if e.get("groesse"):
                _setzen(p, i, "StaticPhysics", "size", e["groesse"])
            n += 1
        p.pruefen()
        r.set_fragment_data(0, pkg.schreiben(p))
        bericht["layer"][kurz] = n
    # Kamerafahrten (absolute Knoten): Knoten wie die Instanzen abbilden, Versatz der Gruppe des Spline-Besitzers
    df_je_asset = {e["spline_asset"]: e["df"] for e in neu if e.get("spline_asset")}
    for asset, s in lage["splines"].items():
        res = archiv.find(asset)
        if res is None or s["relativ"]:
            continue
        linie = haupt if (s["bezug"] == "haupt" or box is None) else box
        df = df_je_asset.get(asset, 0.0)
        doc = bxml.decode(res.fragments[0].read())
        k = 0
        for el in doc.root.elements:
            for kn in el.elements:
                if kn.name == "Knot" and kn.get("position"):
                    f, lokal = s["knoten"][k]
                    q = _absolut(linie, f + df, lokal)[0]
                    kn.set("position", "%.4f,%.4f,%.4f" % tuple(q))
                    k += 1
        fragmente = [bxml.encode(doc)]
        for fr in res.fragments[1:]:
            roh = fr.read()
            fragmente.append(bxml.compute_tsmd(doc) if bxml.is_tsmd(roh) else roh)
        for j, daten in enumerate(fragmente):
            res.set_fragment_data(j, daten)
        bericht["splines"] += 1
    if protokoll is not None:
        protokoll["kameras"] = bericht
    return bericht


# ----------------------------------------------------------------------------- Zwischenspeicher (Editor)
def lage_aus_traeger(traeger_pfad, cache_ordner=None) -> dict:
    """``lage_erfassen`` für ein Trägerpaket (ZIP oder ERP), zwischengespeichert je Datei (Größe, Zeitstempel)."""
    import hashlib
    import json
    import tempfile
    import zipfile
    from ego import erp
    pfad = os.fspath(traeger_pfad)
    st = os.stat(pfad)
    schluessel = hashlib.sha1(("%s|%d|%d|3" % (os.path.abspath(pfad), st.st_size, int(st.st_mtime))).encode()).hexdigest()[:16]
    ordner = cache_ordner or os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "ego-werkzeuge",
                                          "kameras")
    datei = os.path.join(ordner, schluessel + ".json")
    if os.path.exists(datei):
        with open(datei, encoding="utf-8") as f:
            return json.load(f)
    if pfad.lower().endswith(".zip"):
        with zipfile.ZipFile(pfad) as z:
            name = next((n for n in z.namelist() if n.lower().endswith(".erp") and "/" not in n.strip("/")), None)
            if name is None:
                raise KameraFehler("No ERP in the carrier package")
            code = os.path.splitext(name)[0]
            with tempfile.TemporaryDirectory() as tmp:
                erp_pfad = z.extract(name, tmp)
                with erp.open_erp(erp_pfad) as a:
                    lage = lage_erfassen(a, code)
    else:
        code = os.path.splitext(os.path.basename(pfad))[0]
        with erp.open_erp(pfad) as a:
            lage = lage_erfassen(a, code)
    os.makedirs(ordner, exist_ok=True)
    with open(datei, "w", encoding="utf-8") as f:
        json.dump(lage, f)
    return lage
