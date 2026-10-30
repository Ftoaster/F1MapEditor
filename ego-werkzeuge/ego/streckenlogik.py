"""Streckenlogik (BXML) auswerten und ändern. Bedeutung der Felder: docs/formate/streckenlogik.md.

Koordinaten: Meter, Y oben, rechtshändig; Draufsicht = x nach rechts, z nach unten.
Die Normale eines KI-Gates zeigt in Fahrtrichtung nach rechts; Wegpunkt-Längen sind
seitliche Abstände entlang der Normalen (links negativ). Trackmarkup-Zonen (Start/End)
sind normierte Bogenlängen 0…1 entlang der genannten TrackSpace-Spline ab Knoten 0.

Ressourcen einer Strecke (Kennungen, <code> = Streckencode):
    …/ai/<code>_ai_track.aispline                       AISplineData  KI-Ideallinie, Grenzen
    …/ai/<code>_ai_track.trackspacespline               TrackSpace    Mittellinie, Boxengasse
    …/track_space_data/<code>_common.trackmarkup        TM            Zonen (Sektoren, DRS …)
    …/track_space_data/<code>_common.trackspacedata     TS            Streckenraum-Volumes
    …/wep/<code>-vehicles.layer                         WOInstances   Startplätze, Boxen (pkg)
"""

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from . import bxml

Vec = List[float]
UP = (0.0, 1.0, 0.0)


def parse_vec(text: str) -> Vec:
    """"x, y, z" (TrackSpace/TS-Schreibweise) → [x, y, z]."""
    return [float(v) for v in text.split(",")]


def _xyz(el: bxml.Element) -> Vec:
    return [float(el.get(c)) for c in "xyz"]


def _sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _add(a, b, s=1.0):
    return [a[0] + s * b[0], a[1] + s * b[1], a[2] + s * b[2]]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _norm(a):
    n = math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])
    return [a[0] / n, a[1] / n, a[2] / n] if n else [0.0, 0.0, 0.0]


def rechts(tangente: Sequence[float]) -> Vec:
    """Waagrechter Einheitsvektor quer zur Fahrtrichtung nach rechts (= cross(t, oben))."""
    return _norm(_cross(list(tangente), list(UP)))


# ------------------------------------------------------------------- Splines
@dataclass
class Spline:
    """Polylinie mit Bogenlänge. Geschlossen: das Segment letzter → erster Knoten zählt mit."""

    name: str
    punkte: List[Vec]
    geschlossen: bool
    kum: List[float] = field(default_factory=list)  # Bogenlänge bis Knoten i (3D)

    def __post_init__(self):
        self.kum = [0.0]
        pts = self._kette()
        for a, b in zip(pts, pts[1:]):
            self.kum.append(self.kum[-1] + math.dist(a, b))

    def _kette(self):
        return self.punkte + [self.punkte[0]] if self.geschlossen else self.punkte

    @property
    def laenge(self) -> float:
        return self.kum[-1]

    def fortschritt_knoten(self) -> List[float]:
        return [k / self.laenge for k in self.kum[:len(self.punkte)]]

    def punkt(self, fortschritt: float) -> Tuple[Vec, Vec]:
        """Punkt und Tangente (Einheitsvektor) beim Fortschritt 0…1."""
        if self.geschlossen:
            fortschritt %= 1.0
        s = min(max(fortschritt, 0.0), 1.0) * self.laenge
        pts = self._kette()
        lo, hi = 0, len(self.kum) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self.kum[mid] <= s:
                lo = mid
            else:
                hi = mid
        a, b = pts[lo], pts[hi]
        seg = self.kum[hi] - self.kum[lo]
        t = (s - self.kum[lo]) / seg if seg else 0.0
        return _add(a, _sub(b, a), t), _norm(_sub(b, a))

    def abschnitt(self, start: float, ende: float, schritt: float = 5.0) -> List[Vec]:
        """Punkte von start bis ende (Fortschritt; bei geschlossener Spline über 0 hinweg)."""
        if self.geschlossen and ende < start:
            ende += 1.0
        n = max(2, int((ende - start) * self.laenge / schritt) + 1)
        return [self.punkt(start + (ende - start) * i / (n - 1))[0] for i in range(n)]

    def fortschritt_von(self, p: Sequence[float]) -> Tuple[float, float]:
        """Nächster Punkt der Spline zu p (in der Draufsicht x/z): (Fortschritt, Abstand in m)."""
        pts = self._kette()
        best = (float("inf"), 0.0)
        for i, (a, b) in enumerate(zip(pts, pts[1:])):
            dx, dz = b[0] - a[0], b[2] - a[2]
            l2 = dx * dx + dz * dz
            t = 0.0 if not l2 else min(max(((p[0] - a[0]) * dx + (p[2] - a[2]) * dz) / l2, 0.0), 1.0)
            d = math.hypot(a[0] + t * dx - p[0], a[2] + t * dz - p[2])
            if d < best[0]:
                best = (d, (self.kum[i] + t * (self.kum[i + 1] - self.kum[i])) / self.laenge)
        return best[1], best[0]


# ---------------------------------------------------------------- Strecke
@dataclass
class Gate:
    id: int
    name: str
    position: Vec           # Punkt der Ideallinie
    normal: Vec             # quer nach rechts
    wegpunkte: Dict[str, float]

    def seitenpunkt(self, typ: str) -> Optional[Vec]:
        if typ not in self.wegpunkte:
            return None
        return _add(self.position, self.normal, self.wegpunkte[typ])

    @property
    def boxengasse(self) -> bool:
        return "pit" in self.name


@dataclass
class Zone:
    name: str
    typ: str
    start: float
    ende: float
    spline: str
    eigenschaften: Dict[str, str]


@dataclass
class Strecke:
    code: str
    splines: Dict[str, Spline] = field(default_factory=dict)
    gates: List[Gate] = field(default_factory=list)
    links: List[Tuple[int, int]] = field(default_factory=list)          # (von_gate, nach_gate), Index = Link-id
    routen: List[Dict[str, str]] = field(default_factory=list)
    linien: Dict[str, List[Dict[str, str]]] = field(default_factory=dict)  # brake_lines, corner_* …
    ts_listen: List[Dict] = field(default_factory=list)                 # name, type, closed, tore
    zonen: List[Zone] = field(default_factory=list)
    plaetze: List[Dict] = field(default_factory=list)                   # name, art, nummer, position
    quellen: Dict[str, str] = field(default_factory=dict)

    @property
    def hauptlinie(self) -> Spline:
        for sp in self.splines.values():
            if sp.geschlossen:
                return sp
        return next(iter(self.splines.values()))

    def gate(self, gate_id: int) -> Gate:
        return self.gates_nach_id[gate_id]

    @property
    def gates_nach_id(self) -> Dict[int, Gate]:
        return {g.id: g for g in self.gates}

    def routen_gates(self, route: Dict[str, str]) -> List[int]:
        """Gate-Folge einer Route über die Links (inkl. Abzweig- und Einmündungsgate)."""
        von, bis = int(route["from_link"]), int(route["to_link"])
        folge = [self.links[von][0]] if route.get("type") == "pit" else []
        folge += [self.links[i][1] for i in range(von, bis + 1)]
        return folge


def lade_aispline(st: Strecke, doc: bxml.Document) -> None:
    for g in doc.iter("gate"):
        st.gates.append(Gate(int(g.get("id")), g.get("name"), _xyz(g.find("position")), _xyz(g.find("normal")),
                             {w.get("type"): float(w.get("length")) for w in g.iter("waypoint")}))
    st.links = [(int(l.get("from_gate")), int(l.get("to_gate"))) for l in doc.iter("link")]
    st.routen = [dict(r.attrib) for r in doc.iter("route")]
    for liste in doc.root.elements:
        if liste.name not in ("gates", "links", "routes"):
            st.linien[liste.name] = [dict(e.attrib) for e in liste.elements]


def lade_trackspace(st: Strecke, doc: bxml.Document) -> None:
    for sp in doc.iter("TrackSpaceSpline"):
        name = sp.get("name")
        st.splines[name] = Spline(name, [parse_vec(k.get("position")) for k in sp.iter("Knot")],
                                  geschlossen="pit" not in name)


def lade_trackmarkup(st: Strecke, doc: bxml.Document) -> None:
    for me in doc.iter("MarkupElement"):
        for at in me.iter("Attribute"):
            st.zonen.append(Zone(me.get("Name"), at.get("Type"), float(me.get("Start")), float(me.get("End")),
                                 me.get("SplineName"), {p.get("Type"): p.get("Value") for p in at.iter("Property")}))


def lade_trackspacedata(st: Strecke, doc: bxml.Document) -> None:
    for vl in doc.iter("VolumeList"):
        tore = []
        for vol in vl.iter("Volume"):
            for k, gt in enumerate(vol.iter("Gate")):
                punkte = [parse_vec(gt.get("point%d" % i)) for i in range(1, 5)]
                if k == 0 and tore and tore[-1]["punkte"] == punkte:
                    continue  # Anfangstor = Endtor des vorigen Volumes
                tore.append({"volume": int(vol.get("id")), "punkte": punkte})
        st.ts_listen.append({"name": vl.get("name"), "typ": vl.get("type"), "geschlossen": vl.get("closed") == "True",
                             "volumes": len(vl.findall("Volume")), "tore": tore})


PLATZ_RE = re.compile(r"\$(GridSlot|PitBox|PitGarage|F1Garage)(\d+)$", re.I)


def lade_fahrzeugplaetze(st: Strecke, layer_daten: bytes) -> None:
    """Startplätze, Boxen und Garagen aus dem pkg-Layer <code>-vehicles.layer (braucht ego.pkg)."""
    from . import pkg
    for inst in pkg.lesen(layer_daten).instanzen():
        m = PLATZ_RE.search(inst.name or "")
        if m and inst.position is not None:
            st.plaetze.append({"name": inst.name, "art": m.group(1), "nummer": int(m.group(2)),
                               "position": [float(v) for v in inst.position]})
    st.plaetze.sort(key=lambda p: (p["art"], p["nummer"]))


def lade(archiv, code: str, mit_plaetzen: bool = True) -> Strecke:
    """Lädt die Streckenlogik einer Strecke aus einem geöffneten ERP-Archiv (erp.ErpFile)."""
    st = Strecke(code)
    teile = [
        (r"/ai/%s_ai_track\.aispline$" % code, lade_aispline),
        (r"/ai/%s_ai_track\.trackspacespline$" % code, lade_trackspace),
        (r"\.trackmarkup$", lade_trackmarkup),
        (r"\.trackspacedata$", lade_trackspacedata),
    ]
    for muster, funktion in teile:
        treffer = [r for r in archiv.resources if re.search(muster, r.identifier) and "/%s/" % code in r.identifier]
        if not treffer:
            raise KeyError("Ressource %s für %s nicht gefunden" % (muster, code))
        st.quellen[funktion.__name__.replace("lade_", "")] = treffer[0].identifier
        funktion(st, bxml.read_resource(treffer[0]))
    if mit_plaetzen:
        layer = [r for r in archiv.resources if re.search(r"/%s-vehicles\.layer$" % code, r.identifier, re.I)]
        if layer:
            st.quellen["fahrzeugplaetze"] = layer[0].identifier
            lade_fahrzeugplaetze(st, layer[0].fragments[0].read())
    return st


# ------------------------------------------------------------------- Ändern
def ki_gates(doc: bxml.Document) -> List[Dict]:
    """KI-Gates einer aispline als Wörterbücher: id, name, position, normal, wegpunkte {typ: länge}."""
    out = []
    for g in doc.iter("gate"):
        out.append({"id": int(g.get("id")), "name": g.get("name"), "position": _xyz(g.find("position")),
                    "normal": _xyz(g.find("normal")),
                    "wegpunkte": {w.get("type"): float(w.get("length")) for w in g.iter("waypoint")}})
    return out


def ki_linie_verschieben(doc: bxml.Document, von: int, bis: int, meter: float, rampe: int = 4,
                         rand: float = 0.5) -> List[Tuple[int, float]]:
    """Verschiebt die KI-Ideallinie auf den Strecken-Gates von..bis (ids) seitlich um `meter`
    (positiv = nach rechts). Ein- und Auslauf über `rampe` Gates (weicher Übergang). Die
    Verschiebung wird je Gate auf die racing_limit-Grenzen minus `rand` begrenzt. Alle
    Wegpunkte außer racing_line werden gegenläufig korrigiert, damit Grenzen und weiße Linien
    ortsfest bleiben. Gibt [(gate_id, tatsächliche Verschiebung)] zurück."""
    geaendert = []
    for g in doc.iter("gate"):
        if "pit" in (g.get("name") or ""):
            continue
        i = int(g.get("id"))
        if i < von - rampe or i > bis + rampe:
            continue
        if i < von:
            w = (i - (von - rampe)) / float(rampe + 1)
        elif i > bis:
            w = ((bis + rampe) - i) / float(rampe + 1)
        else:
            w = 1.0
        w = w * w * (3 - 2 * w)  # weicher Verlauf
        wp = {x.get("type"): x for x in g.iter("waypoint")}
        lo = float(wp["left_racing_limit"].get("length")) + rand if "left_racing_limit" in wp else -1e9
        hi = float(wp["right_racing_limit"].get("length")) - rand if "right_racing_limit" in wp else 1e9
        s = max(lo, min(hi, meter * w))
        if s == 0.0:
            continue
        p, n = g.find("position"), _xyz(g.find("normal"))
        for k, c in enumerate("xyz"):
            p.set(c, "%g" % (float(p.get(c)) + s * n[k]))
        for typ, x in wp.items():
            if typ != "racing_line":
                x.set("length", "%g" % (float(x.get("length")) - s))
        geaendert.append((i, s))
    return geaendert
