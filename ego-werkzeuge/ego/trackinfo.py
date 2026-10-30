"""AP14: TrackInfoBinary v3 und TrackInfoTex lesen, schreiben und erzeugen.

Tabellen sind Big-Endian, Eigenschaftsnutzdaten und Höhenraster Little-Endian.
Das Raster ist ein Welt-Höhenfeld, kein TrackSpace: 128 m je Kachel, 128² u16.
Kachel (0, 0) liegt implizit in Block 0. Alle Bytes werden strukturell gelesen;
unbekannte Eigenschafts-Typkennungen bleiben ausdrücklich uninterpretiert.
Belege und Grenzen der Rekonstruktion: docs/formate/trackinfo.md.
"""

import math
import struct
from dataclasses import dataclass, field
from typing import List, Sequence, Tuple

import numpy as np

from .licht import TrackInfoTex  # Bestehenden, bitgenauen TGA-Leser weiterverwenden.

KACHELKANTE = 128
KACHEL_BYTES = KACHELKANTE * KACHELKANTE * 2
SONNENWINKEL_KENNUNG = 0xA2A112A7


class TrackInfoFehler(ValueError):
    """Ungültige oder nicht unterstützte TrackInfo-Daten."""


@dataclass
class Eigenschaft:
    """Pfad, 32-Bit-Typkennung, u16 Nutzdatenlänge und Nutzdaten.

    In allen vorhandenen Dateien: keine Eigenschaft oder genau ein Sonnenwinkel,
    Kennung A2A112A7, vier Byte IEEE-f32 Little-Endian. Die Kennung ist weder
    nachgewiesener Pfad-Hash noch nachgewiesenes Typ-Enum; nicht neu berechnen.
    """

    pfad: str
    kennung: int
    daten: bytes

    @property
    def float_wert(self) -> float:
        if len(self.daten) != 4:
            raise TrackInfoFehler("Eigenschaft hat keine vier Byte Nutzdaten")
        return struct.unpack('<f', self.daten)[0]

    @classmethod
    def sonnenwinkel(cls, grad: float = 120.0):
        if not math.isfinite(grad):
            raise TrackInfoFehler("Sonnenwinkel muss endlich sein")
        return cls('/sky/sunRotationAngle', SONNENWINKEL_KENNUNG, struct.pack('<f', grad))


@dataclass(frozen=True)
class Kachel:
    x: int
    z: int
    block: int


@dataclass
class TrackInfoBinary:
    version: int
    eigenschaften: List[Eigenschaft]
    kacheln: List[Kachel]
    hoehe_min: float
    hoehe_max: float
    hoehen_schritt: float
    minimum: Tuple[float, float]  # x, z; Meter
    maximum: Tuple[float, float]
    raster: np.ndarray = field(repr=False)  # (Block, x, z), Little-Endian u16

    @classmethod
    def aus_bytes(cls, daten: bytes):
        """Vollständig parsen; kein Rohdaten-Rückfall und kein ignorierter Rest."""
        d = memoryview(daten)
        o = 0

        def lesen(fmt):
            nonlocal o
            s = struct.Struct(fmt)
            if o + s.size > len(d):
                raise TrackInfoFehler("TrackInfoBinary abgeschnitten bei Byte %d" % o)
            wert = s.unpack_from(d, o)
            o += s.size
            return wert

        version, ne = lesen('>II')
        if version != 3:
            raise TrackInfoFehler("TrackInfoBinary-Version %d statt 3" % version)
        if ne > (len(d) - o) // 10:
            raise TrackInfoFehler("Unplausible Eigenschaftszahl")
        props = []
        for _ in range(ne):
            (n,) = lesen('>I')
            if o + n > len(d):
                raise TrackInfoFehler("Eigenschaftspfad abgeschnitten")
            pfad = bytes(d[o:o+n]).decode('utf-8', 'surrogateescape')
            o += n
            kennung, laenge = lesen('>IH')
            if o + laenge > len(d):
                raise TrackInfoFehler("Eigenschaftsnutzdaten abgeschnitten")
            props.append(Eigenschaft(pfad, kennung, bytes(d[o:o+laenge])))
            o += laenge
        (nk,) = lesen('>I')
        if nk > (len(d) - o) // 8:
            raise TrackInfoFehler("Kacheltabelle abgeschnitten")
        kacheln = [Kachel(*lesen('>HHI')) for _ in range(nk)]
        hmin, hmax, schritt, xmin, zmin, xmax, zmax, nb = lesen('>7fI')
        if len(d) - o != nb * KACHEL_BYTES:
            raise TrackInfoFehler("Raster: %d Byte statt %d" % (len(d)-o, nb*KACHEL_BYTES))
        raster = np.frombuffer(d[o:], dtype='<u2').reshape(nb, KACHELKANTE, KACHELKANTE).copy()
        obj = cls(version, props, kacheln, hmin, hmax, schritt,
                  (xmin, zmin), (xmax, zmax), raster)
        obj.pruefen()
        return obj

    def pruefen(self):
        """Strukturinvarianten aller 30 untersuchten Datensätze; Fehler werfen."""
        if self.version != 3:
            raise TrackInfoFehler("Nur TrackInfoBinary-Version 3 wird unterstützt")
        a = np.asarray(self.raster)
        if (a.ndim != 3 or a.shape[1:] != (128, 128) or a.dtype.kind != 'u'
                or a.dtype.itemsize != 2 or a.shape[0] != len(self.kacheln)+1):
            raise TrackInfoFehler("Raster benötigt (Kachelzahl+1, 128, 128) UInt16")
        felder = [self.hoehe_min, self.hoehe_max, self.hoehen_schritt, *self.minimum, *self.maximum]
        if len(self.minimum) != 2 or len(self.maximum) != 2 or not np.isfinite(felder).all():
            raise TrackInfoFehler("Rastergrenzen und Höhen müssen endlich sein")
        if (self.hoehe_max <= self.hoehe_min or self.hoehen_schritt <= 0
                or np.any(np.asarray(self.maximum) <= self.minimum)):
            raise TrackInfoFehler("Raster-/Höhenbereich muss positiv sein")
        if not math.isclose(self.hoehen_schritt, (self.hoehe_max-self.hoehe_min)/65535, rel_tol=2e-6):
            raise TrackInfoFehler("Höhenschritt passt nicht zum UInt16-Bereich")
        coords, bloecke = set(), set()
        for k in self.kacheln:
            if not (0 <= k.x <= 65535 and 0 <= k.z <= 65535 and 1 <= k.block < len(a)):
                raise TrackInfoFehler("Kachelkoordinate oder Blockindex ungültig")
            if (k.x, k.z) == (0, 0) or (k.x, k.z) in coords or k.block in bloecke:
                raise TrackInfoFehler("Doppelte Kachel, implizite Nullkachel oder doppelter Block")
            coords.add((k.x, k.z))
            bloecke.add(k.block)
        for p in self.eigenschaften:
            if not 0 <= p.kennung <= 0xffffffff or len(p.daten) > 65535:
                raise TrackInfoFehler("Ungültige Eigenschaftskennung/-länge")
        return True

    def to_bytes(self) -> bytes:
        self.pruefen()
        teile = [struct.pack('>II', self.version, len(self.eigenschaften))]
        for p in self.eigenschaften:
            name = p.pfad.encode('utf-8', 'surrogateescape')
            teile.extend((struct.pack('>I', len(name)), name,
                          struct.pack('>IH', p.kennung, len(p.daten)), bytes(p.daten)))
        teile.append(struct.pack('>I', len(self.kacheln)))
        teile.extend(struct.pack('>HHI', k.x, k.z, k.block) for k in self.kacheln)
        teile.append(struct.pack('>7fI', self.hoehe_min, self.hoehe_max, self.hoehen_schritt,
                                 *self.minimum, *self.maximum, len(self.raster)))
        teile.append(np.ascontiguousarray(self.raster, dtype='<u2').tobytes())
        return b''.join(teile)

    def blocktabelle(self):
        return {(0, 0): 0, **{(k.x, k.z): k.block for k in self.kacheln}}

    def abdeckung(self, punkte: Sequence[Sequence[float]]) -> dict:
        """Punkte (N,3) prüfen; fehlende Kacheln und äußere Grenzen unterscheiden.

        Nullcodes sind Höhenminimum und kein gesichertes Ungültigkeitsbit.
        Wie das Spiel auf fehlende/äußere Zellen reagiert, ist unbekannt.
        """
        p = np.asarray(punkte, dtype=float)
        if p.ndim != 2 or p.shape[1] != 3 or not np.isfinite(p).all():
            raise TrackInfoFehler("Endliche XYZ-Punkte (N,3) erwartet")
        xz = p[:, (0, 2)]
        innen = np.all((xz >= self.minimum) & (xz < self.maximum), axis=1)
        ij = np.floor((xz - self.minimum)/128).astype(np.int64)
        tabelle = self.blocktabelle()
        vorhanden = np.array([tuple(q) in tabelle for q in ij], dtype=bool)
        return {'anzahl': len(p), 'innerhalb_grenzen': int(innen.sum()),
                'ausserhalb_grenzen': int((~innen).sum()),
                'fehlende_kachel_innerhalb': int((innen & ~vorhanden).sum()),
                'abgedeckt': int((innen & vorhanden).sum())}

    def hoehen(self, punkte: Sequence[Sequence[float]]) -> np.ndarray:
        """Nächste rekonstruierte Höhenprobe; NaN außerhalb/bei fehlender Kachel.

        Empirische Probenlage: min + (128*Kachel + Pixel + 1) m in x/z.
        Dies ist keine Nachbildung der unbekannten Laufzeit-Interpolation.
        """
        p = np.asarray(punkte, dtype=float)
        self.abdeckung(p)  # Form und Endlichkeit prüfen.
        ij = np.maximum(0, np.rint(p[:, (0, 2)] - self.minimum - 1).astype(np.int64))
        innen = np.all((p[:, (0, 2)] >= self.minimum) & (p[:, (0, 2)] < self.maximum), axis=1)
        out = np.full(len(p), np.nan)
        tabelle = self.blocktabelle()
        for k in set(map(tuple, ij//128)):
            if k not in tabelle:
                continue
            sel = innen & np.all(ij//128 == k, axis=1)
            q = ij[sel] % 128
            out[sel] = self.hoehe_min + self.raster[tabelle[k], q[:, 0], q[:, 1]] * self.hoehen_schritt
        return out


def hoehenfeld_aus_netz(positionen, dreiecke, *, eigenschaften=(), rand_m=32.0,
                        grundhoehe=0.0, zusaetzliche_punkte=(), max_rasterpunkte=16_777_216):
    """Dichtes Raster aus der obersten Dreiecksfläche in XZ erzeugen.

    Vertikale/entartete Flächen werden ausgelassen. In nicht überdeckten Proben
    gilt grundhoehe. Mehrstöckige Geometrie ergibt die oberste Fläche, wie ein
    Höhenfeld es verlangt. Vollständige Rechteckabdeckung vermeidet neue Lücken.
    """
    p = np.asarray(positionen, dtype=float)
    tri = np.asarray(dreiecke)
    extra = np.asarray(zusaetzliche_punkte, dtype=float).reshape(-1, 3)
    if (p.ndim != 2 or p.shape[1] != 3 or not len(p) or not np.isfinite(p).all()
            or not np.isfinite(extra).all()):
        raise TrackInfoFehler("Netz benötigt endliche XYZ-Positionen")
    if (tri.ndim != 2 or tri.shape[1] != 3 or tri.dtype.kind not in 'iu'
            or not len(tri) or tri.min() < 0 or tri.max() >= len(p)):
        raise TrackInfoFehler("Ungültige Dreiecksindices")
    if not math.isfinite(rand_m) or rand_m < 2 or not math.isfinite(grundhoehe):
        raise TrackInfoFehler("Rand muss mindestens 2 m, Grundhöhe endlich sein")
    alle = np.vstack((p, extra))
    minimum = np.floor(alle[:, (0, 2)].min(0) - rand_m)
    wunsch_max = np.ceil(alle[:, (0, 2)].max(0) + rand_m)
    rasterzahl = np.ceil((wunsch_max-minimum)/128)
    if not np.isfinite(rasterzahl).all() or np.any(rasterzahl > 65536):
        raise TrackInfoFehler("Höhenraster überschreitet den Koordinatenbereich")
    anzahl = np.maximum(1, rasterzahl.astype(np.int64))
    if int(anzahl[0])*int(anzahl[1])*128**2 > max_rasterpunkte:
        raise TrackInfoFehler("Höhenraster überschreitet die erlaubte Größe")
    maximum = minimum + anzahl*128
    nx, nz = (int(v)*128 for v in anzahl)
    h = np.full((nx, nz), -np.inf)
    # Kleine Dreiecke werden lokal abgetastet; große Grundflächen zeilenweise,
    # damit ein einziges großes Dreieck keine riesigen Hilfsarrays benötigt.
    for a, b, c in p[tri]:
        ab, ac = b-a, c-a
        den = ab[0]*ac[2] - ab[2]*ac[0]
        if abs(den) < 1e-12:
            continue
        lo = np.maximum(0, np.ceil(np.minimum(np.minimum(a, b), c)[[0, 2]] - minimum - 1).astype(int))
        hi = np.minimum([nx-1, nz-1], np.floor(np.maximum(np.maximum(a, b), c)[[0, 2]] - minimum - 1).astype(int))
        if np.any(lo > hi):
            continue
        z = minimum[1] + np.arange(lo[1], hi[1]+1) + 1 - a[2]
        for start in range(lo[0], hi[0]+1, 64):
            stop = min(hi[0]+1, start+64)
            x = minimum[0] + np.arange(start, stop)[:, None] + 1 - a[0]
            v = (x*ac[2] - z*ac[0])/den
            w = (ab[0]*z - ab[2]*x)/den
            maske = (v >= -1e-8) & (w >= -1e-8) & (v+w <= 1+1e-8)
            hoehe = np.where(maske, a[1]+v*ab[1]+w*ac[1], -np.inf)
            ziel = h[start:stop, lo[1]:hi[1]+1]
            np.maximum(ziel, hoehe, out=ziel)
    h[~np.isfinite(h)] = grundhoehe
    # Ein Meter Puffer auch bei flacher Strecke: kein Nullschritt, kein 0-Code
    # für die neutrale Fläche. f32-Werte vor dem Quantisieren festlegen.
    hmin = float(np.float32(h.min()-1.0))
    hmax = float(np.float32(h.max()+1.0))
    schritt = float(np.float32((hmax-hmin)/65535))
    codes = np.clip(np.rint((h-hmin)/schritt), 0, 65535).astype('<u2')
    kacheln, bloecke = [], []
    for x in range(int(anzahl[0])):
        for z in range(int(anzahl[1])):
            if x or z:
                kacheln.append(Kachel(x, z, len(bloecke)))
            bloecke.append(codes[x*128:(x+1)*128, z*128:(z+1)*128])
    obj = TrackInfoBinary(3, list(eigenschaften), kacheln, hmin, hmax, schritt,
                          tuple(minimum), tuple(maximum), np.array(bloecke, dtype='<u2'))
    obj.pruefen()
    return obj


def textur_neutral(vorlage: TrackInfoTex) -> TrackInfoTex:
    """Keine vorgebackene Ideallinie; BGRA=(0,239,0,105) aus Original-Grundwerten.

    Kanal-Semantik im Shader bleibt unbewiesen. G=239 ist der gerundete globale
    Bahrain-Mittelwert, A=105 dessen beobachteter Sockel. Keine neue Nässephysik.
    """
    return vorlage.gleichmaessig((0, 239, 0, 105))
