"""Fahrphysik TRN~: JPAK-Container und VCQTC-Kacheln aus F1 25.

JPAK (little-endian): 32 Byte Kopf, N Verzeichniseinträge zu 32 Byte,
nullterminierte Namen, auf 16 Byte ausgerichtete unkomprimierte Blöcke.
Kopf: 'JPAK', 0, N, 16, 0, Namenbeginn, 0, 0 (je 4 Byte).
Eintrag: Namenoffset, Größe, Datenoffset, gespeicherte Größe, 4 Nullwörter.
Alle Offsets sind absolut. Füllbytes und Dateinachlauf bleiben erhalten.

VCQTC: Version 0x20004 (Spiel/Portimão) oder 0x20002 (Sepang), 144 Byte
Kopf, Vertices aus 3 float32 + einem undekodierten 32-Bit-Attribut,
Quadtree-Knoten (big-endian u16!), 4-Byte-Dreiecke, Blattdaten.
Kopf: +4/+16 AABB min/max, +28 Dreiecke, +32 Vertices, +36 Materialien
(8), +40 Verticesoffset, +48 Knotenoffset, +56 Dreiecksoffset,
+64 Blattoffset; +80 acht FourCCs. +44/+52 sind bei Version 4 floats
für die Y-Extrema außerhalb der dritten Blattgruppe (keine Dateioffsets).
Bei ausschließlich Gruppe 3: 999999/-999999 als Initialisierungswerte.
Dreieck (a,b,c,d): i0=((a&3)<<8)|b, i1=i0+c, i2=i0+d,
Material=(a>>2)&7, weitere drei Bits=a>>5 (unverändert erhalten).

Der Schreiber rekonstruiert Container und Geometriesegmente. Er unterstützt
gezielte Materialänderungen bei unveränderter Geometrie. Freie Geometrie
erzeugt ego.trn_erzeugen ausdrücklich in Version 2; die zusätzlichen
Blattattribute der Version 4 sind noch nicht vollständig entschlüsselt.
Die Laufzeitakzeptanz geänderter Dateien muss im Spiel geprüft werden.
Formatdetails und Herkunft der Vergleichsquellen: docs/formate/trn.md.
"""

import os
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union

import numpy as np

HEADER = struct.Struct('<4s7I')
ENTRY = struct.Struct('<8I')
VERSIONEN = (0x20002, 0x20004)


class TrnFormatError(ValueError):
    """Ungültige oder noch nicht unterstützte TRN-Struktur."""


def _pruefe(bedingung, meldung):
    if not bedingung:
        raise TrnFormatError(meldung)


def _ausrichten(zahl, ausrichtung=16):
    return (zahl + ausrichtung - 1) // ausrichtung * ausrichtung


def _readonly(array):
    array.flags.writeable = False
    return array


def _code(code):
    raw = code.encode('ascii') if isinstance(code, str) else bytes(code)
    if len(raw) != 4 or (raw != b'\0' * 4 and any(c < 32 or c > 126 for c in raw)):
        raise ValueError('Oberflächencode muss aus vier druckbaren ASCII-Zeichen bestehen')
    return raw


@dataclass
class JpakEntry:
    """Benannter, unkomprimierter JPAK-Block; data kann ersetzt werden."""

    name: str
    data: bytes
    reserved: tuple = (0, 0, 0, 0)
    padding: bytes = b''
    source_offset: Optional[int] = None


@dataclass
class JpakFile:
    """JPAK ohne implizite Dekompression oder versteckte Originalkopie."""

    entries: List[JpakEntry] = field(default_factory=list)
    reserved: tuple = (0, 0, 0, 0)
    alignment: int = 16
    name_padding: bytes = b''
    trailer: bytes = b''

    @classmethod
    def from_bytes(cls, data):
        data = bytes(data)
        _pruefe(len(data) >= 32, 'JPAK-Kopf unvollständig')
        magic, r0, n, align, r1, strings, r2, r3 = HEADER.unpack_from(data)
        _pruefe(magic == b'JPAK', 'Keine JPAK-Datei')
        _pruefe(align == 16, 'Unbekannte JPAK-Ausrichtung')
        _pruefe(0 < n <= (len(data) - 32) // 32, 'Ungültige Blockanzahl')
        _pruefe(strings == 32 + n * 32, 'Unerwarteter Namenbeginn')
        rows = [ENTRY.unpack_from(data, 32 + i * 32) for i in range(n)]
        first = rows[0][2]
        _pruefe(strings < first <= len(data), 'Ungültiger Datenbeginn')
        _pruefe(data[strings:strings + 1] == b'\0', 'Fehlender leerer Anfangsname')
        pos = strings + 1
        entries = []
        for i, row in enumerate(rows):
            name_off, size, off, stored, *reserved = row
            _pruefe(name_off == pos, 'Namentabelle nicht fortlaufend')
            end = data.find(b'\0', pos, first)
            _pruefe(end >= pos, 'Nicht abgeschlossener Blockname')
            try:
                name = data[pos:end].decode('ascii')
            except UnicodeDecodeError as exc:
                raise TrnFormatError('Blockname ist kein ASCII') from exc
            pos = end + 1
            _pruefe(size == stored, 'Komprimierter JPAK-Block wird nicht unterstützt')
            _pruefe(off % align == 0 and off >= first and off + stored <= len(data),
                    'Ungültiger Blockbereich')
            next_off = rows[i + 1][2] if i + 1 < n else _ausrichten(off + stored, align)
            _pruefe(next_off == _ausrichten(off + stored, align) and next_off <= len(data),
                    'Überlappender Block oder unerwartete Blocklücke')
            entries.append(JpakEntry(name, data[off:off + stored], tuple(reserved),
                                     data[off + stored:next_off], off))
        _pruefe(first == _ausrichten(pos, align), 'Unerwartete Lücke nach der Namentabelle')
        obj = cls(entries, (r0, r1, r2, r3), align, data[pos:first], data[next_off:])
        return obj

    @classmethod
    def open(cls, path):
        return cls.from_bytes(Path(path).read_bytes())

    def to_bytes(self):
        """Berechnet Namen, Größen und Offsets neu; erhält passende Füllbytes."""
        _pruefe(self.alignment == 16 and bool(self.entries), 'Ungültiges JPAK-Layout')
        strings = 32 + 32 * len(self.entries)
        names = bytearray(b'\0')
        offsets = []
        for entry in self.entries:
            raw = entry.name.encode('ascii')
            _pruefe(bool(raw) and b'\0' not in raw, 'Ungültiger Blockname')
            offsets.append(strings + len(names))
            names += raw + b'\0'
        pad = _ausrichten(strings + len(names), self.alignment) - strings - len(names)
        names += self.name_padding if len(self.name_padding) == pad else bytes(pad)
        pos = strings + len(names)
        directory, blocks = bytearray(), bytearray()
        for name_off, entry in zip(offsets, self.entries):
            raw = bytes(entry.data)
            directory += ENTRY.pack(name_off, len(raw), pos, len(raw), *entry.reserved)
            blocks += raw
            pad = _ausrichten(pos + len(raw), self.alignment) - pos - len(raw)
            blocks += entry.padding if len(entry.padding) == pad else bytes(pad)
            pos = _ausrichten(pos + len(raw), self.alignment)
        r0, r1, r2, r3 = self.reserved
        return (HEADER.pack(b'JPAK', r0, len(self.entries), self.alignment, r1,
                            strings, r2, r3) + directory + names + blocks + self.trailer)

    def write(self, path, overwrite=False):
        """Schreibt in eine neue Datei; vorhandene Ziele nur auf ausdrücklichen Wunsch."""
        with open(path, 'wb' if overwrite else 'xb') as handle:
            return handle.write(self.to_bytes())

    def geometry(self):
        """Liest sämtliche Kacheln; unbekannte Blocktypen werden nicht übergangen."""
        return [Vcqtc.from_bytes(e.data, e.name) for e in self.entries]


@dataclass
class QuadLeaf:
    """Blattdaten; Version 4 mit Höhenkodierung und drei Referenzgruppen.

    secondary_count heißt aus AP4 weiterhin so, bezeichnet aber die Größe
    der ersten Gruppe. Offene Bits und Quantisierungsbytes bleiben roh.
    """

    offset: int
    triangles: np.ndarray
    raw: bytes
    secondary_count: Optional[int] = None

    @property
    def height_limit(self):
        """Version 4: quantisierte Maximalhöhe; Bit 15 ist separat und noch offen."""
        if self.secondary_count is None:
            return None
        word = struct.unpack_from('<H', self.raw)[0]
        return ((word & 0x7fff) - 16384) * .02

    @property
    def third_count(self):
        """Version 4: Größe der dritten, am Listenende stehenden Dreiecksgruppe."""
        if self.secondary_count is None:
            return None
        return struct.unpack_from('<H', self.raw, 2)[0] >> 10

    @property
    def triangle_groups(self):
        """Drei Version-4-Gruppen; deren exakte Geometrieklassifikation bleibt offen."""
        if self.secondary_count is None:
            return None
        split = len(self.triangles) - self.third_count
        return (self.triangles[:self.secondary_count],
                self.triangles[self.secondary_count:split], self.triangles[split:])


class Vcqtc:
    """Eine Kachel; Geometrie schreibgeschützt, Materialänderungen über set_surface."""

    @classmethod
    def from_bytes(cls, data, name=''):
        data = bytes(data)
        _pruefe(len(data) >= 144, 'VCQTC-Kopf unvollständig: ' + name)
        self = cls()
        self.name = name
        self.version = struct.unpack_from('<I', data)[0]
        _pruefe(self.version in VERSIONEN, 'Unbekannte VCQTC-Version: %#x' % self.version)
        nt, nv, nm, vo = struct.unpack_from('<4I', data, 28)
        no, to, ro = [struct.unpack_from('<I', data, off)[0] for off in (48, 56, 64)]
        _pruefe(nm == 8 and vo == 144 and nt > 0 and nv > 0, 'Unbekannter VCQTC-Kopf')
        _pruefe(no == vo + 16 * nv and no < to and (to - no) % 2 == 0,
                'Ungültiger Vertex-/Knotenbereich')
        expected_ro = _ausrichten(to + 4 * nt) if self.version == 0x20004 else to + 4 * nt
        _pruefe(ro == expected_ro and ro <= len(data), 'Ungültiger Dreiecks-/Blattbereich')
        self._header = data[:vo]
        self.bounds = _readonly(np.frombuffer(data, '<f4', 6, 4).copy().reshape(2, 3))
        self.height_fields = struct.unpack_from('<f', data, 44) + struct.unpack_from('<f', data, 52)
        # Strukturierter Datentyp verhindert Interpretation des vierten Wortes als NaN.
        dtype = np.dtype([('position', '<f4', (3,)), ('attribut', '<u4')])
        self._vertices = _readonly(np.frombuffer(data, dtype, nv, vo).copy())
        self.vertices = _readonly(self._vertices['position'])
        self.vertex_attributes = _readonly(self._vertices['attribut'])
        self.nodes = _readonly(np.frombuffer(data, '>u2', (to - no) // 2, no).copy())
        self._triangles = np.frombuffer(data, 'u1', nt * 4, to).copy().reshape(nt, 4)
        self.materials = [data[80 + i * 4:84 + i * 4].decode('ascii') for i in range(8)]
        for code in self.materials:
            _code(code)
        self._padding = data[to + nt * 4:ro]
        self._auxiliary = data[ro:]
        self.vertex_offset, self.node_offset = vo, no
        self.triangle_offset, self.leaf_offset = to, ro
        self.leaves = self._read_tree()
        self.validate()
        return self

    @property
    def triangles(self):
        t = self._triangles.astype(np.uint32)
        base = ((t[:, 0] & 3) << 8) | t[:, 1]
        return _readonly(np.column_stack((base, base + t[:, 2], base + t[:, 3])))

    @property
    def material_indices(self):
        return _readonly((self._triangles[:, 0] >> 2) & 7)

    @property
    def flags(self):
        return _readonly(self._triangles[:, 0] >> 5)

    @property
    def surfaces(self):
        labels = ['0x00000000' if code == '\0' * 4 else code for code in self.materials]
        return _readonly(np.asarray(labels)[self.material_indices])

    def _read_tree(self):
        seen, offsets, todo = set(), set(), [0]
        while todo:
            i = todo.pop()
            _pruefe(0 <= i < len(self.nodes) and i not in seen, 'Zyklischer/ungültiger Quadtree')
            seen.add(i)
            node = int(self.nodes[i])
            if node < 0x8000:
                todo.extend(range(node, node + 4))
            elif node != 0xffff:
                offsets.add((node & 0x7fff) * (4 if self.version == 0x20004 else 1))
        _pruefe(len(seen) == len(self.nodes), 'Nicht erreichbare Quadtree-Knoten')
        offsets = sorted(offsets)
        leaves = []
        raw = self._auxiliary
        _pruefe(bool(offsets) and offsets[0] == 0, 'Fehlende Blattdaten')
        for k, off in enumerate(offsets):
            end = offsets[k + 1] if k + 1 < len(offsets) else len(raw)
            _pruefe(0 <= off < end <= len(raw), 'Ungültiger Blattoffset')
            secondary = None
            if self.version == 0x20004:
                _pruefe(off + 6 <= end, 'Blattkopf unvollständig')
                count, secondary = raw[off + 4:off + 6]
                _pruefe(0 < count and secondary <= count, 'Ungültige Blattzähler')
                third = struct.unpack_from('<H', raw, off + 2)[0] >> 10
                _pruefe(third <= count - secondary, 'Dritte Blattgruppe überlappt die erste')
                expected = _ausrichten(off + 6 + count * 2, 4) + count * 4
                _pruefe(expected == end, 'Unerwartete Länge der Blattattribute')
                indices = np.frombuffer(raw, '<u2', count, off + 6).copy()
                aligned = _ausrichten(off + 6 + count * 2, 4)
                _pruefe(raw[off + 6 + count * 2:aligned] in (b'', b'\xff\xff'),
                        'Unerwartete Blattausrichtung')
            else:
                _pruefe(off + 3 <= end, 'Blattliste unvollständig')
                last = int.from_bytes(raw[off:off + 2], 'big')
                values, pos = [last], off + 2
                while pos < end and raw[pos] != 255:
                    delta = raw[pos]
                    last += delta
                    if delta != 254:
                        values.append(last)
                    pos += 1
                _pruefe(pos + 1 == end and raw[pos] == 255, 'Blattliste nicht abgeschlossen')
                indices = np.asarray(values, dtype=np.uint32)
            _pruefe(int(indices.max()) < len(self._triangles), 'Blatt verweist auf fehlendes Dreieck')
            leaves.append(QuadLeaf(off, _readonly(indices), raw[off:end], secondary))
        return leaves

    def validate(self):
        _pruefe(np.isfinite(self.vertices).all() and np.isfinite(self.bounds).all(),
                'Nicht endliche Koordinaten')
        _pruefe(np.all(self.bounds[0] <= self.bounds[1]), 'Vertauschte Begrenzungen')
        _pruefe(np.all(self.vertices >= self.bounds[0] - 0.001) and
                np.all(self.vertices <= self.bounds[1] + 0.001), 'Vertex außerhalb der AABB')
        _pruefe(int(self.triangles.max()) < len(self.vertices), 'Ungültiger Dreiecksindex')
        return True

    def to_bytes(self):
        """Serialisiert die interpretierten Segmente; Zusatzdaten bleiben bytegleich."""
        self.validate()
        header = bytearray(self._header)
        header[80:112] = b''.join(_code(code) for code in self.materials)
        return (bytes(header) + self._vertices.tobytes() + self.nodes.tobytes()
                + self._triangles.tobytes() + self._padding + self._auxiliary)

    def set_surface(self, triangle_indices, code):
        """Ändert ausschließlich Materialbits und bei Bedarf einen unbenutzten Palettenplatz.

        Keine Knoten-, Referenz-, Vertex- oder Flagänderung. Bei acht belegten
        Codes muss ein bereits vorhandener Code gewählt werden. Die Auswahl
        darf ein einzelner Index, eine Indexliste oder eine boolesche Maske sein.
        """
        code = _code(code).decode('ascii')
        selected = np.asarray(triangle_indices)
        if selected.dtype == np.bool_:
            if selected.shape != (len(self._triangles),):
                raise ValueError('Auswahlmaske hat die falsche Länge')
            selected = np.flatnonzero(selected)
        elif not np.issubdtype(selected.dtype, np.integer):
            raise ValueError('Dreiecksindizes müssen ganzzahlig sein')
        selected = np.atleast_1d(selected).astype(np.int64)
        if selected.ndim != 1 or np.any(selected < 0) or np.any(selected >= len(self._triangles)):
            raise ValueError('Dreiecksindex außerhalb der Kachel')
        if not len(selected):
            return
        if code in self.materials:
            slot = self.materials.index(code)
        else:
            remaining = np.ones(len(self._triangles), dtype=bool)
            remaining[selected] = False
            used = set(self.material_indices[remaining].tolist())
            free = [i for i in range(8) if i not in used]
            if not free:
                raise ValueError('Alle acht Palettenplätze sind noch in Gebrauch')
            slot = free[0]
            self.materials[slot] = code
        self._triangles[selected, 0] = (self._triangles[selected, 0] & 0xe3) | (slot << 2)

    def areas(self):
        """3D-Dreiecksflächen in Koordinateneinheiten² (Meterannahme: m²)."""
        points = self.vertices.astype(np.float64)[self.triangles]
        return np.linalg.norm(np.cross(points[:, 1] - points[:, 0],
                                       points[:, 2] - points[:, 0]), axis=1) * 0.5

    def statistics(self):
        areas, surfaces = self.areas(), self.surfaces
        return {str(code): {'dreiecke': int(np.count_nonzero(surfaces == code)),
                            'flaeche': float(areas[surfaces == code].sum())}
                for code in np.unique(surfaces)}

    def spatial_statistics(self, tolerance=None):
        """Prüft Referenzabdeckung und XZ-Überlappung im Quadtree.

        Kinderfolge: links unten, rechts unten, links oben, rechts oben
        (+Z nach unten in der Draufsicht). Die Originale referenzieren auch Dreiecke bis etwa
        0,01 Einheiten (Version 4) bzw. 0,1 (Version 2) außerhalb einer
        Zelle; deshalb Toleranz 0,011 bzw. 0,101 einschließlich float-Rundung.
        Nur AABB-Überlappung, kein vollständiger Dreieck/Rechteck-Schnitttest.
        """
        if tolerance is None:
            tolerance = 0.011 if self.version == 0x20004 else 0.101
        points = self.vertices.astype(np.float64)[self.triangles][:, :, [0, 2]]
        tri_lo, tri_hi = points.min(1), points.max(1)
        leaves = {leaf.offset: leaf for leaf in self.leaves}
        visited = np.zeros(len(self._triangles), dtype=bool)
        stack = [(0, self.bounds[0, [0, 2]].astype(float),
                  self.bounds[1, [0, 2]].astype(float), 0)]
        refs = misses = depth = 0
        distance = 0.0
        while stack:
            i, lo, hi, level = stack.pop()
            depth = max(depth, level)
            node = int(self.nodes[i])
            if node < 0x8000:
                mid = (lo + hi) / 2
                for child in range(4):
                    upper = np.array([child & 1, 1 - (child >> 1)], dtype=bool)
                    stack.append((node + child, np.where(upper, mid, lo),
                                  np.where(upper, hi, mid), level + 1))
            elif node != 0xffff:
                offset = (node & 0x7fff) * (4 if self.version == 0x20004 else 1)
                indices = leaves[offset].triangles
                visited[indices] = True
                refs += len(indices)
                gap = np.maximum(lo - tri_hi[indices], tri_lo[indices] - hi).max(1)
                distance = max(distance, float(gap.max()))
                misses += int(np.count_nonzero(gap > tolerance))
        return {'referenzen': refs, 'nicht_referenziert': int(np.count_nonzero(~visited)),
                'disjunkt': misses, 'maximaler_abstand': distance, 'tiefe': depth,
                'toleranz': tolerance}


def mesh(kacheln):
    """Verbindet Kacheln ohne Zusammenlegen von Vertices oder Dreiecken."""
    vertices, triangles, surfaces = [], [], []
    start = 0
    for tile in kacheln:
        vertices.append(tile.vertices)
        triangles.append(tile.triangles + start)
        surfaces.append(tile.surfaces)
        start += len(tile.vertices)
    if not vertices:
        raise ValueError('Keine Kacheln vorhanden')
    return np.concatenate(vertices), np.concatenate(triangles), np.concatenate(surfaces)


def mesh_mit_attributen(kacheln):
    """Exportiert auch Rohattribute, Flagbits und die ursprüngliche Kachelpartition.

    Rückgabe: vertices, dreiecke, codes, vertex_attribute, flags,
    kachelgruppen. Letztere sind (Name, globale Dreiecksindices). Für den
    Erzeuger sind sie optionale Vorgaben, keine erhaltenen Originalblöcke.
    """
    kacheln = list(kacheln)
    vertices, dreiecke, codes = mesh(kacheln)
    gruppen, start = [], 0
    for tile in kacheln:
        ende = start + len(tile.triangles)
        gruppen.append((tile.name, np.arange(start, ende, dtype=np.int64)))
        start = ende
    return (vertices, dreiecke, codes,
            np.concatenate([t.vertex_attributes for t in kacheln]),
            np.concatenate([t.flags for t in kacheln]), gruppen)


def open_trn(source: Union[bytes, str, os.PathLike]):
    """Öffnet entpackte TRN-Bytes oder eine extrahierte JPAK-Datei."""
    return JpakFile.from_bytes(source) if isinstance(source, (bytes, bytearray)) else JpakFile.open(source)
