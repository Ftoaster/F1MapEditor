"""TSQuadTree (QT02) der EGO-Engine lesen, schreiben und erzeugen.

Gesicherter Aufbau, alle Zahlen little-endian:
    0   24  min x,y,z und max x,y,z (6 × float32)
   24    4  Zahl P der Stützpunkte (29 Originale: 255; Monaco: 220)
   28    4  Zahl N der Knoten
   32 12*P  Stützpunkte (float32 x,y,z)
    …    4  b"QT02"
    … 16*N  Knoten, Wurzel = Index 0
             innen: 4 × u16 Kindindex, float32 Teilung x,z
             Blatt: u16 0, u16 Anzahl (0…8), Anzahl × u8 Index,
                    Rest des 16-Byte-Satzes = Füllbytes

Der Baum über vorgegebenen Stützpunkten ist für alle 30 Originale bitgenau
erzeugbar: Mittelwert in float32, Quadranten (-x,-z), (-x,+z), (+x,-z),
(+x,+z), Tiefensuche, feste Punkt-AABBs mit ±20 m. Die Indizes sind aufsteigend.
Die Herleitung der Stützpunkte aus TrackSpace ist noch nicht bitgenau geklärt.
Der Standardgenerator verwendet deshalb ausdrücklich eine Näherung: lineare
Interpolation nach geschlossener 3D-Polylinienlänge. Optional kann eine uniforme
Catmull-Rom-Kurve nach numerischer Bogenlänge verwendet werden. Beide Verfahren
sind eigenständige Erzeuger, keine Behauptung über die Originalimplementierung.
Messwerte und offene Fragen: docs/formate/tsquadtree.md.
"""

import bisect
import math
import struct
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

MAGIC = b"QT02"
KOPF = struct.Struct("<6fII")
PUNKT = struct.Struct("<3f")
INNERER_KNOTEN = struct.Struct("<4H2f")
BLATTKOPF = struct.Struct("<HH")
F32 = struct.Struct("<f")
BLATTKAPAZITAET = 8
PUNKTRAND = 20.0
MAX_STUETZPUNKTE = 255
Vec = Tuple[float, float, float]


class TSQuadTreeFormatError(ValueError):
    """Die Daten oder Eingaben entsprechen nicht dem unterstützten QT02-Format."""


def _f32(wert: float) -> float:
    try:
        result = F32.unpack(F32.pack(wert))[0]
    except (OverflowError, struct.error) as exc:
        raise TSQuadTreeFormatError("Wert liegt außerhalb des float32-Bereichs") from exc
    if not math.isfinite(result):
        raise TSQuadTreeFormatError("Koordinate ist nicht endlich")
    return result


def _vektor(p: Sequence[float]) -> Vec:
    if len(p) != 3:
        raise TSQuadTreeFormatError("Ein Punkt muss drei Koordinaten haben")
    return tuple(_f32(float(v)) for v in p)


@dataclass
class Knoten:
    """Entweder innerer Knoten (kinder/teilung) oder Blatt (indizes/fuellbytes)."""

    kinder: Optional[Tuple[int, int, int, int]] = None
    teilung: Optional[Tuple[float, float]] = None
    indizes: Tuple[int, ...] = ()
    fuellbytes: bytes = b""

    @property
    def blatt(self) -> bool:
        return self.kinder is None


@dataclass
class TSQuadTree:
    """Editierbarer Baum; auch unbenutzte Blattbytes werden im Rundlauf erhalten."""

    minimum: Vec
    maximum: Vec
    punkte: List[Vec]
    knoten: List[Knoten] = field(default_factory=list)

    def to_bytes(self) -> bytes:
        """Serialisiert die Felder nach Prüfung; keine Rückgabe gespeicherter Rohdaten."""
        return schreiben(self)

    def pruefen(self) -> None:
        """Prüft Größen, endliche Werte, Referenzen und einen zusammenhängenden Baum."""
        p, n = len(self.punkte), len(self.knoten)
        if not 1 <= p <= 256 or not 1 <= n <= 65536:
            raise TSQuadTreeFormatError("Ungültige Zahl von Punkten oder Knoten")
        lo, hi = _vektor(self.minimum), _vektor(self.maximum)
        if any(a > b for a, b in zip(lo, hi)):
            raise TSQuadTreeFormatError("Begrenzungsbox ist umgekehrt")
        for punkt in self.punkte:
            punkt = _vektor(punkt)
            if any(v < a or v > b for v, a, b in zip(punkt, lo, hi)):
                raise TSQuadTreeFormatError("Stützpunkt liegt außerhalb der Begrenzungsbox")
        for k in self.knoten:
            if k.blatt:
                if k.teilung is not None or len(k.indizes) > BLATTKAPAZITAET:
                    raise TSQuadTreeFormatError("Ungültiges Blatt")
                if any(not isinstance(i, int) or not 0 <= i < p for i in k.indizes):
                    raise TSQuadTreeFormatError("Stützpunktindex außerhalb des gültigen Bereichs")
                if len(set(k.indizes)) != len(k.indizes):
                    raise TSQuadTreeFormatError("Doppelter Index im Blatt")
                if k.fuellbytes and len(k.fuellbytes) != 12 - len(k.indizes):
                    raise TSQuadTreeFormatError("Falsche Zahl von Blattfüllbytes")
            else:
                if len(k.kinder) != 4 or k.teilung is None or len(k.teilung) != 2:
                    raise TSQuadTreeFormatError("Ungültiger innerer Knoten")
                if k.indizes or k.fuellbytes:
                    raise TSQuadTreeFormatError("Innerer Knoten enthält Blattdaten")
                for v in k.teilung:
                    _f32(v)
                if any(not isinstance(i, int) or not 1 <= i < n for i in k.kinder):
                    raise TSQuadTreeFormatError("Kindindex außerhalb des gültigen Bereichs")
        besucht = set()
        offen = [0]
        while offen:
            i = offen.pop()
            if i in besucht:
                raise TSQuadTreeFormatError("Zyklus oder mehrfach referenzierter Knoten")
            besucht.add(i)
            if not self.knoten[i].blatt:
                offen.extend(self.knoten[i].kinder)
        if len(besucht) != n:
            raise TSQuadTreeFormatError("Nicht erreichbare Knoten")

    def kandidaten(self, x: float, z: float) -> Tuple[int, ...]:
        """Liefert das Blatt einer x/z-Position, auch außerhalb der Kopfbox.

        Dies ist eine Baumabfrage, keine garantierte globale Nächster-Punkt-Suche.
        Auf der Teilungsebene wählen wir den positiven Quadranten; die genaue
        Grenzkonvention des Spiels ist mit den Originalen nicht unterscheidbar.
        """
        if not math.isfinite(x) or not math.isfinite(z):
            raise ValueError("Abfragekoordinaten müssen endlich sein")
        i, besucht = 0, set()
        while True:
            if not 0 <= i < len(self.knoten) or i in besucht:
                raise TSQuadTreeFormatError("Ungültiger Baum bei der Abfrage")
            besucht.add(i)
            k = self.knoten[i]
            if k.blatt:
                return k.indizes
            q = 2 * int(x >= k.teilung[0]) + int(z >= k.teilung[1])
            i = k.kinder[q]


def lesen(daten: bytes) -> TSQuadTree:
    """Liest genau eine Ressource und prüft sie vollständig (ohne Nachlaufbytes)."""
    daten = bytes(daten)
    if len(daten) < KOPF.size:
        raise TSQuadTreeFormatError("TSQuadTree-Kopf abgeschnitten")
    values = KOPF.unpack_from(daten)
    p, n = values[6:]
    if not 1 <= p <= 256 or not 1 <= n <= 65536:
        raise TSQuadTreeFormatError("Ungültige Zahl von Punkten oder Knoten")
    off = KOPF.size + PUNKT.size * p
    if len(daten) != off + 4 + 16 * n:
        raise TSQuadTreeFormatError("Dateilänge passt nicht zu den Zählern")
    if daten[off:off + 4] != MAGIC:
        raise TSQuadTreeFormatError("QT02-Marke fehlt")
    punkte = [PUNKT.unpack_from(daten, 32 + 12 * i) for i in range(p)]
    knoten = []
    for i in range(n):
        pos = off + 4 + 16 * i
        erstes, anzahl = BLATTKOPF.unpack_from(daten, pos)
        if erstes == 0:
            if anzahl > BLATTKAPAZITAET:
                raise TSQuadTreeFormatError("Blattkapazität überschritten")
            knoten.append(Knoten(indizes=tuple(daten[pos + 4:pos + 4 + anzahl]),
                                 fuellbytes=daten[pos + 4 + anzahl:pos + 16]))
        else:
            v = INNERER_KNOTEN.unpack_from(daten, pos)
            knoten.append(Knoten(kinder=v[:4], teilung=v[4:]))
    baum = TSQuadTree(values[:3], values[3:6], punkte, knoten)
    baum.pruefen()
    return baum


def schreiben(baum: TSQuadTree) -> bytes:
    """Schreibt einen Baum; neue Blätter erhalten Nullbytes als Füllung."""
    baum.pruefen()
    out = bytearray(KOPF.pack(*baum.minimum, *baum.maximum, len(baum.punkte), len(baum.knoten)))
    for p in baum.punkte:
        out.extend(PUNKT.pack(*p))
    out.extend(MAGIC)
    for k in baum.knoten:
        if k.blatt:
            out.extend(BLATTKOPF.pack(0, len(k.indizes)))
            out.extend(bytes(k.indizes))
            out.extend(k.fuellbytes or bytes(12 - len(k.indizes)))
        else:
            out.extend(INNERER_KNOTEN.pack(*k.kinder, *k.teilung))
    return bytes(out)


def aus_stuetzpunkten(punkte: Sequence[Sequence[float]], rand: float = PUNKTRAND,
                     maximale_tiefe: int = 64) -> TSQuadTree:
    """Baut QT02 aus fertigen Stützpunkten (alle 30 Originale bitgenau).

    Jeder Index steht in allen Quadranten, die sein Quadrat ±rand berührt.
    Neun untrennbare Punkte können nicht in acht Byte-Indizes abgebildet werden:
    Der Generator meldet einen Fehler statt unendlich zu rekursieren oder
    Kandidaten abzuschneiden. Ein solches Original ist nicht beobachtet worden.
    """
    pts = [_vektor(p) for p in punkte]
    if not 1 <= len(pts) <= MAX_STUETZPUNKTE:
        raise TSQuadTreeFormatError("Der Erzeuger benötigt 1…255 Stützpunkte")
    rand = _f32(float(rand))
    if rand < 0 or not isinstance(maximale_tiefe, int) or not 1 <= maximale_tiefe <= 256:
        raise ValueError("Rand oder maximale Tiefe ist ungültig")
    lo = tuple(min(p[d] for p in pts) for d in range(3))
    hi = tuple(max(p[d] for p in pts) for d in range(3))
    knoten = []

    def bauen(ids: Tuple[int, ...], tiefe: int) -> int:
        if len(knoten) >= 65536:
            raise TSQuadTreeFormatError("Kindindizes benötigen mehr als 16 Bit")
        index = len(knoten)
        knoten.append(Knoten())
        if len(ids) <= BLATTKAPAZITAET:
            knoten[index] = Knoten(indizes=ids)
            return index
        if tiefe >= maximale_tiefe:
            raise TSQuadTreeFormatError("Maximale Baumtiefe bei mehr als acht Kandidaten erreicht")
        sx = sz = 0.0
        for i in ids:
            sx = _f32(sx + pts[i][0])
            sz = _f32(sz + pts[i][2])
        sx, sz = _f32(sx / len(ids)), _f32(sz / len(ids))
        gruppen = [[], [], [], []]
        for i in ids:
            x, _, z = pts[i]
            xm, xp = _f32(x - rand) <= sx, _f32(x + rand) >= sx
            zm, zp = _f32(z - rand) <= sz, _f32(z + rand) >= sz
            for q, drin in enumerate((xm and zm, xm and zp, xp and zm, xp and zp)):
                if drin:
                    gruppen[q].append(i)
        if any(len(g) == len(ids) for g in gruppen):
            raise TSQuadTreeFormatError("Mehr als acht Kandidaten sind mit diesem Rand untrennbar")
        kinder = tuple(bauen(tuple(g), tiefe + 1) for g in gruppen)
        knoten[index] = Knoten(kinder=kinder, teilung=(sx, sz))
        return index

    bauen(tuple(range(len(pts))), 0)
    return TSQuadTree(lo, hi, pts, knoten)


def _catmull(punkte: Sequence[Vec], t: float) -> Vec:
    n = len(punkte)
    i = int(math.floor(t))
    u = t - math.floor(t)
    a, b, c, d = (punkte[(i + j) % n] for j in (-1, 0, 1, 2))
    return tuple(b[k] + 0.5 * u * (c[k] - a[k] + u * (
        2 * a[k] - 5 * b[k] + 4 * c[k] - d[k] + u * (
            3 * (b[k] - c[k]) + d[k] - a[k]))) for k in range(3))


def stuetzpunkte_berechnen(trackspace_punkte: Sequence[Sequence[float]],
                          anzahl: Optional[int] = None, interpolation: str = "linear",
                          schritte_pro_segment: int = 20) -> List[Vec]:
    """Näherung aus einer geschlossenen Hauptlinie ohne doppelten Endpunkt.

    'linear': exakte 3D-Bogenlänge der Polylinie, Punkt i bei i*L/P.
    'catmull_rom': uniforme periodische Catmull-Rom-Kurve, Bogenlängen-LUT
    mit schritte_pro_segment Unterteilungen. Geometrie in float64, Ausgabe f32.
    Ohne anzahl: min(255, max(3, floor(L/15))). Diese Anzahlregel ist eine
    Vermutung, keine gesicherte Engine-Regel; beim Originalvergleich explizit
    P übergeben (insbesondere Monaco). Ein doppelter Endpunkt wird entfernt.
    """
    pts = [_vektor(p) for p in trackspace_punkte]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts.pop()
    if len(pts) < 3:
        raise ValueError("Eine geschlossene Hauptlinie benötigt mindestens drei Punkte")
    if any(a == b for a, b in zip(pts, pts[1:] + pts[:1])):
        raise ValueError("Aufeinanderfolgende TrackSpace-Punkte sind identisch")
    if interpolation not in ("linear", "catmull_rom"):
        raise ValueError("Interpolation muss 'linear' oder 'catmull_rom' sein")
    if not isinstance(schritte_pro_segment, int) or not 1 <= schritte_pro_segment <= 1000:
        raise ValueError("Ungültige Zahl von Spline-Unterteilungen")
    if interpolation == "linear":
        kette = pts + [pts[0]]
        parameter = list(range(len(kette)))
    else:
        parameter = [i / schritte_pro_segment
                     for i in range(len(pts) * schritte_pro_segment + 1)]
        kette = [_catmull(pts, t) for t in parameter]
    kum = [0.0]
    for a, b in zip(kette, kette[1:]):
        kum.append(kum[-1] + math.dist(a, b))
    laenge = kum[-1]
    if not math.isfinite(laenge) or laenge <= 0:
        raise ValueError("Die Hauptlinie hat keine endliche positive Länge")
    if anzahl is None:
        anzahl = min(MAX_STUETZPUNKTE, max(3, int(laenge / 15.0)))
    if not isinstance(anzahl, int) or not 3 <= anzahl <= MAX_STUETZPUNKTE:
        raise ValueError("Stützpunktzahl muss im Bereich 3…255 liegen")
    out = []
    for i in range(anzahl):
        s = i * laenge / anzahl
        j = min(bisect.bisect_right(kum, s) - 1, len(kette) - 2)
        l = kum[j + 1] - kum[j]
        u = (s - kum[j]) / l if l else 0.0
        if interpolation == "linear":
            p = tuple(a + u * (b - a) for a, b in zip(kette[j], kette[j + 1]))
        else:
            p = _catmull(pts, parameter[j] + u * (parameter[j + 1] - parameter[j]))
        out.append(_vektor(p))
    return out


def tsquadtree_bauen(trackspace_punkte: Sequence[Sequence[float]],
                     anzahl: Optional[int] = None, interpolation: str = "linear",
                     rand: float = PUNKTRAND, schritte_pro_segment: int = 20) -> bytes:
    """Erzeugt eine neue QT02-Ressource aus der Hauptlinie (Sampling-Näherung).

    Bitgenauer Neubau aus vorhandenen Stützpunkten:
    schreiben(aus_stuetzpunkten(lesen(original).punkte)).
    """
    punkte = stuetzpunkte_berechnen(trackspace_punkte, anzahl, interpolation, schritte_pro_segment)
    return schreiben(aus_stuetzpunkten(punkte, rand=rand))
