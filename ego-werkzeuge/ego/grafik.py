"""Grafik-Ressourcen der EGO-Engine (F1 25) lesen, prüfen und schreiben.

Ermittelt durch Analyse der Originaldateien (Bahrain, Portimão-Mod). Alle Zahlen
little-endian. Was hier steht, ist durch Rundlauf (bitgenaues Neuschreiben) und
Rendering geprüft, sofern nicht ausdrücklich „Vermutung“ dabeisteht. Ausführliche
Beschreibung: docs/formate/grafik.md.

Ein 3D-Objekt besteht aus mehreren Ressourcen, die über ihre Kennung verknüpft sind:

    <basis>.idf?model           GfxModelRes   Szenengraph: Knoten (TRS), LOD-Abstände,
                                              Bounding Box, Verweise auf die Meshes je LOD
    <basis>.idf?lod=N&mesh      GfxMeshRes    Teilnetze (Indexbereich, Vertex-Ströme,
                                              Materialslot), Vertex-Deklaration
    <basis>.idf?lod=all&vb=K    GfxBufferRes  Vertexpuffer (ein Strom, z. B. nur Positionen)
    <basis>.idf?lod=all&ib=0    GfxBufferRes  Indexpuffer (u16, Dreiecksliste)
    <basis>.idf?render          ModelProxyRes Kopie des Szenengraphen ohne Geometrie
    <basis>.emb?context=X       GfxMatBindRes Materialslot-Name -> Material-Kennung
    <pfad>.material             GfxMaterialRes Shader, Parameter, Texturen
    <pfad>.tif                  GfxSRVResource Sicht auf eine Textur
    <pfad>.tif.image            GfxSurfaceRes Textur (Kopf + Mip-Stufen [+ externe Mips])
    <basis>.idf?context=physics_objects  PYRB Kollision (Starrkörper mit Grundformen)

Die Fahrbahn selbst ist kein GfxModelRes, sondern ein TerrainRes
(``terrain/idf/<strecke>_terrain.idf?context=render``) mit eigenem Aufbau, der
dieselben GfxBufferRes-Puffer nutzt.
"""

import math
import re
import struct
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from . import erp

# --------------------------------------------------------------------------- Konstanten

PUFFER_VERTEX = 0x1109  # Kopf-Feld 0 eines Vertexpuffers
PUFFER_INDEX = 0x110A  # Indexpuffer von Objekten
PUFFER_INDEX_TERRAIN = 0x1102  # Indexpuffer des Terrains
PUFFER_KOPF = struct.Struct("<4I")  # Art, Stride, Anzahl, Code

TYP_UNBEKANNT = {  # "unbekanntes Feld" im ERP-Eintrag je Ressourcentyp (aus den Originalen)
    "GfxModelRes": 0x20001,
    "GfxMeshRes": 0x60000,
    "GfxBufferRes": 0x4,
    "ModelProxyRes": 0x2,
    "GfxMatBindRes": 0x1,
    "GfxMaterialRes": 0x2,
    "GfxSurfaceRes": 0x9,
    "GfxSRVResource": 0x2,
}

TOPOLOGIE_DREIECKSLISTE = 4  # einziger Wert in allen untersuchten Dateien

# Verwendungszweck eines Vertex-Elements. 1/2/3/5/6 durch Werte und Rendering belegt,
# 7/8 Vermutung (nur in wenigen animierten Objekten).
VERWENDUNG = {
    1: "POSITION",
    2: "NORMAL",
    3: "TANGENT",
    5: "TEXCOORD",
    6: "COLOR",
    7: "BLENDINDICES?",
    8: "BLENDWEIGHT?",
}


@dataclass(frozen=True)
class ElementFormat:
    name: str
    dtype: str
    komponenten: int
    normiert: bool = False
    gesichert: bool = True

    @property
    def groesse(self) -> int:
        return np.dtype(self.dtype).itemsize * self.komponenten


# Formatcode eines Vertex-Elements (eigene Aufzählung der Engine, keine DXGI-Werte).
ELEMENT_FORMATE: Dict[int, ElementFormat] = {
    47: ElementFormat("float32x3", "<f4", 3),
    51: ElementFormat("float32x4", "<f4", 4),
    35: ElementFormat("float16x4", "<f2", 4),
    29: ElementFormat("float16x2", "<f2", 2),
    14: ElementFormat("unorm8x4", "u1", 4, normiert=True),
    13: ElementFormat("uint8x4", "u1", 4, gesichert=False),
    31: ElementFormat("uint16x4", "<u2", 4, gesichert=False),
}


class GrafikFormatFehler(ValueError):
    """Eine Grafik-Ressource entspricht nicht dem erwarteten Aufbau."""


def _cstr(daten: bytes, offset: int) -> str:
    ende = daten.index(b"\0", offset)
    return daten[offset:ende].decode("utf-8", "surrogateescape")


def _strings_blob(texte: Sequence[str]) -> Tuple[bytes, List[int]]:
    """Nullterminierte Zeichenketten hintereinander; liefert (Bytes, Offsets)."""
    offsets, teile, pos = [], [], 0
    for t in texte:
        b = t.encode("utf-8", "surrogateescape") + b"\0"
        offsets.append(pos)
        teile.append(b)
        pos += len(b)
    return b"".join(teile), offsets


def _strings_lesen(daten: bytes, start: int, ende: int) -> Tuple[List[str], List[int]]:
    texte, offsets, pos = [], [], start
    while pos < ende:
        e = daten.index(b"\0", pos)
        offsets.append(pos - start)
        texte.append(daten[pos:e].decode("utf-8", "surrogateescape"))
        pos = e + 1
    if pos != ende:
        raise GrafikFormatFehler("Zeichenkettenbereich endet nicht sauber (%d statt %d)" % (pos, ende))
    return texte, offsets


# --------------------------------------------------------------------------- Kennungen

def basis_kennung(kennung: str) -> str:
    """'eaid://…/x.idf?model' -> 'eaid://…/x.idf'"""
    return kennung.split("?", 1)[0]


def modell_kennung(basis: str) -> str:
    return basis + "?model"


def mesh_kennung(basis: str, lod: int = 0) -> str:
    return "%s?lod=%d&mesh" % (basis, lod)


def vb_kennung(basis: str, k: int, lod: str = "all") -> str:
    return "%s?lod=%s&vb=%d" % (basis, lod, k)


def ib_kennung(basis: str, k: int = 0, lod: str = "all") -> str:
    return "%s?lod=%s&ib=%d" % (basis, lod, k)


def proxy_kennung(basis: str) -> str:
    return basis + "?render"


# --------------------------------------------------------------------------- GfxBufferRes

@dataclass
class GfxPuffer:
    """GfxBufferRes: Fragment 0 = 16-Byte-Kopf (Art, Stride, Anzahl, Code),
    Fragment 1 = Rohdaten (Flags 4). Größe = Stride * Anzahl (in allen Dateien erfüllt)."""

    art: int
    stride: int
    anzahl: int
    code: int
    daten: bytes = b""

    @classmethod
    def aus_bytes(cls, kopf: bytes, daten: bytes) -> "GfxPuffer":
        if len(kopf) != PUFFER_KOPF.size:
            raise GrafikFormatFehler("Pufferkopf hat %d statt 16 Byte" % len(kopf))
        art, stride, anzahl, code = PUFFER_KOPF.unpack(kopf)
        return cls(art, stride, anzahl, code, daten)

    @classmethod
    def aus_ressource(cls, res: erp.ErpResource, mit_daten: bool = True) -> "GfxPuffer":
        if res.resource_type != "GfxBufferRes" or len(res.fragments) != 2:
            raise GrafikFormatFehler("Keine GfxBufferRes: %s" % res.identifier)
        kopf = res.fragments[0].read()
        daten = res.fragments[1].read() if mit_daten else b""
        p = cls.aus_bytes(kopf, daten)
        if not mit_daten:
            p.daten = None  # type: ignore[assignment]
        return p

    def kopf_bytes(self) -> bytes:
        return PUFFER_KOPF.pack(self.art, self.stride, self.anzahl, self.code)

    @property
    def ist_index(self) -> bool:
        return self.art in (PUFFER_INDEX, PUFFER_INDEX_TERRAIN)

    def indizes(self) -> np.ndarray:
        dt = {2: "<u2", 4: "<u4"}.get(self.stride)
        if dt is None:
            raise GrafikFormatFehler("Unbekannte Indexgröße %d" % self.stride)
        return np.frombuffer(self.daten, dt)

    def matrix(self) -> np.ndarray:
        """Rohdaten als (Anzahl, Stride)-Bytematrix."""
        return np.frombuffer(self.daten, np.uint8).reshape(self.anzahl, self.stride)

    def pruefen(self) -> List[str]:
        fehler = []
        if self.daten is not None and len(self.daten) != self.stride * self.anzahl:
            fehler.append("Puffergröße %d != Stride %d * Anzahl %d" % (len(self.daten), self.stride, self.anzahl))
        if self.art not in (PUFFER_VERTEX, PUFFER_INDEX, PUFFER_INDEX_TERRAIN):
            fehler.append("unbekannte Pufferart 0x%x" % self.art)
        return fehler


# --------------------------------------------------------------------------- GfxMeshRes

MESH_KOPF = struct.Struct("<19I")  # 76 Byte; alle Offsets im Mesh relativ zum Kopfende
TEILNETZ = struct.Struct("<6I4f4I8I")  # 88 Byte
MESH_KNOTEN = struct.Struct("<16f7I")  # 92 Byte
ZEICHNUNG = struct.Struct("<4I")  # 16 Byte
ELEMENT = struct.Struct("<5I")  # 20 Byte
PAAR = struct.Struct("<2I")  # 8 Byte


@dataclass
class VertexElement:
    slot: int  # Eingabe-Slot (D3D-Input-Slot), nicht der Pufferindex
    offset: int  # Byte-Offset im Stride des Puffers
    verwendung: int  # 1 POSITION, 2 NORMAL, 3 TANGENT, 5 TEXCOORD, 6 COLOR …
    index: int  # Verwendungsindex (TEXCOORD0/1 …)
    format: int  # siehe ELEMENT_FORMATE

    @property
    def name(self) -> str:
        return "%s%d" % (VERWENDUNG.get(self.verwendung, "U%d" % self.verwendung), self.index)


@dataclass
class Teilnetz:
    """Ein Teilnetz (88 Byte): ein Indexbereich mit einem Material."""

    strom_anzahl: int
    topologie: int
    indexgroesse: int
    start_index: int
    index_anzahl: int
    basis_vertex: int
    kugel: Tuple[float, float, float, float]  # Mittelpunkt (lokal) + Radius
    eintrag_offset: int  # Byte-Offset in die Tabelle "eintraege" (8 * i)
    eintrag_anzahl: int  # bisher immer 1
    material: int  # Index in GfxMesh.materialnamen (gespeichert als Byte-Offset)
    reserviert: int
    stroeme: List[int]  # 8 Slots: Indizes in vb_kennungen, nur die ersten strom_anzahl gültig


@dataclass
class MeshKnoten:
    """Zuordnung Modellknoten -> Zeichnungen (92 Byte). Matrix bisher immer Einheitsmatrix."""

    matrix: Tuple[float, ...]
    feld0: int
    modell_knoten: int  # Index des Knotens in GfxModelRes
    feld2: int
    erste_zeichnung: int
    zeichnung_anzahl: int
    feld5: int
    feld6: int


@dataclass
class Zeichnung:
    """Zeichenaufruf (16 Byte). flag 1/0: Bedeutung offen (Vermutung: Schattenwurf)."""

    flag: int
    teilnetz: int
    start_index: int
    index_anzahl: int


@dataclass
class GfxMesh:
    teilnetze: List[Teilnetz] = field(default_factory=list)
    knoten: List[MeshKnoten] = field(default_factory=list)
    zeichnungen: List[Zeichnung] = field(default_factory=list)
    vb_kennungen: List[str] = field(default_factory=list)
    ib_kennungen: List[str] = field(default_factory=list)
    materialnamen: List[str] = field(default_factory=list)  # Materialslots, z. B. "01", "11"
    eintraege: List[Tuple[int, int]] = field(default_factory=list)  # (1, i) je Teilnetz
    elemente: List[VertexElement] = field(default_factory=list)
    strom_tabelle: List[Tuple[int, int]] = field(default_factory=list)  # je VB: (Elementanzahl, erstes Element)
    reserviert16: int = 0
    reserviert17: int = 0
    lod: int = 0

    # ---------------------------------------------------------------- Lesen
    @classmethod
    def aus_bytes(cls, d: bytes) -> "GfxMesh":
        if len(d) < MESH_KOPF.size:
            raise GrafikFormatFehler("Mesh zu kurz")
        h = MESH_KOPF.unpack_from(d, 0)
        (n_tn, n_vb, n_ib, n_mat, n_ein, o_vb, o_ib, o_mat, o_ein, n_el, o_el, o_str,
         n_kn, o_kn, n_ze, o_ze, r16, r17, lod) = h
        b = MESH_KOPF.size
        erwartet = [
            (o_kn, TEILNETZ.size * n_tn, "Knoten-Offset"),
            (o_ze, o_kn + MESH_KNOTEN.size * n_kn, "Zeichnungs-Offset"),
            (o_vb, o_ze + ZEICHNUNG.size * n_ze, "Zeichenketten-Offset"),
            (o_el, o_ein + PAAR.size * n_ein, "Element-Offset"),
            (o_str, o_el + ELEMENT.size * n_el, "Strom-Offset"),
            (len(d), b + o_str + PAAR.size * n_vb, "Dateigröße"),
        ]
        for ist, soll, was in erwartet:
            if ist != soll:
                raise GrafikFormatFehler("%s %d, erwartet %d" % (was, ist, soll))
        m = cls(reserviert16=r16, reserviert17=r17, lod=lod)
        namen, offs = _strings_lesen(d, b + o_vb, b + o_ein)
        if len(namen) != n_vb + n_ib + n_mat:
            raise GrafikFormatFehler("Mesh: %d Zeichenketten statt %d" % (len(namen), n_vb + n_ib + n_mat))
        if offs[n_vb] != o_ib - o_vb or (n_mat and offs[n_vb + n_ib] != o_mat - o_vb):
            raise GrafikFormatFehler("Mesh: Zeichenketten-Offsets passen nicht")
        m.vb_kennungen = namen[:n_vb]
        m.ib_kennungen = namen[n_vb:n_vb + n_ib]
        m.materialnamen = namen[n_vb + n_ib:]
        mat_offsets = {o - (o_mat - o_vb): i for i, o in enumerate(offs[n_vb + n_ib:])}
        for i in range(n_tn):
            t = TEILNETZ.unpack_from(d, b + TEILNETZ.size * i)
            if t[12] not in mat_offsets:
                raise GrafikFormatFehler("Teilnetz %d: Materialname-Offset %d ungültig" % (i, t[12]))
            m.teilnetze.append(Teilnetz(t[0], t[1], t[2], t[3], t[4], t[5], tuple(t[6:10]), t[10], t[11],
                                        mat_offsets[t[12]], t[13], list(t[14:22])))
        for i in range(n_kn):
            k = MESH_KNOTEN.unpack_from(d, b + o_kn + MESH_KNOTEN.size * i)
            m.knoten.append(MeshKnoten(tuple(k[:16]), *k[16:]))
        for i in range(n_ze):
            m.zeichnungen.append(Zeichnung(*ZEICHNUNG.unpack_from(d, b + o_ze + ZEICHNUNG.size * i)))
        m.eintraege = [PAAR.unpack_from(d, b + o_ein + PAAR.size * i) for i in range(n_ein)]
        m.elemente = [VertexElement(*ELEMENT.unpack_from(d, b + o_el + ELEMENT.size * i)) for i in range(n_el)]
        m.strom_tabelle = [PAAR.unpack_from(d, b + o_str + PAAR.size * i) for i in range(n_vb)]
        return m

    # ---------------------------------------------------------------- Schreiben
    def to_bytes(self) -> bytes:
        n_tn, n_kn, n_ze = len(self.teilnetze), len(self.knoten), len(self.zeichnungen)
        n_vb, n_ib, n_mat = len(self.vb_kennungen), len(self.ib_kennungen), len(self.materialnamen)
        blob, offs = _strings_blob(self.vb_kennungen + self.ib_kennungen + self.materialnamen)
        o_kn = TEILNETZ.size * n_tn
        o_ze = o_kn + MESH_KNOTEN.size * n_kn
        o_vb = o_ze + ZEICHNUNG.size * n_ze
        o_ib = o_vb + (offs[n_vb] if n_ib else len(blob))
        o_mat = o_vb + (offs[n_vb + n_ib] if n_mat else len(blob))
        o_ein = o_vb + len(blob)
        o_el = o_ein + PAAR.size * len(self.eintraege)
        o_str = o_el + ELEMENT.size * len(self.elemente)
        mat_off = [o - offs[n_vb + n_ib] for o in offs[n_vb + n_ib:]] if n_mat else []
        teile = [MESH_KOPF.pack(n_tn, n_vb, n_ib, n_mat, len(self.eintraege), o_vb, o_ib, o_mat, o_ein,
                                len(self.elemente), o_el, o_str, n_kn, o_kn, n_ze, o_ze,
                                self.reserviert16, self.reserviert17, self.lod)]
        for t in self.teilnetze:
            if len(t.stroeme) != 8:
                raise GrafikFormatFehler("Teilnetz braucht genau 8 Strom-Slots")
            teile.append(TEILNETZ.pack(t.strom_anzahl, t.topologie, t.indexgroesse, t.start_index,
                                       t.index_anzahl, t.basis_vertex, *t.kugel, t.eintrag_offset,
                                       t.eintrag_anzahl, mat_off[t.material], t.reserviert, *t.stroeme))
        for k in self.knoten:
            teile.append(MESH_KNOTEN.pack(*k.matrix, k.feld0, k.modell_knoten, k.feld2, k.erste_zeichnung,
                                          k.zeichnung_anzahl, k.feld5, k.feld6))
        for z in self.zeichnungen:
            teile.append(ZEICHNUNG.pack(z.flag, z.teilnetz, z.start_index, z.index_anzahl))
        teile.append(blob)
        teile += [PAAR.pack(*e) for e in self.eintraege]
        teile += [ELEMENT.pack(e.slot, e.offset, e.verwendung, e.index, e.format) for e in self.elemente]
        teile += [PAAR.pack(*s) for s in self.strom_tabelle]
        return b"".join(teile)

    # ---------------------------------------------------------------- Hilfen
    def elemente_fuer_puffer(self, vb: int) -> List[VertexElement]:
        anzahl, erstes = self.strom_tabelle[vb]
        return self.elemente[erstes:erstes + anzahl]

    def layout(self, teilnetz: int) -> List[Tuple[int, VertexElement]]:
        """(Pufferindex, Element) für alle Ströme eines Teilnetzes."""
        t = self.teilnetze[teilnetz]
        return [(vb, e) for vb in t.stroeme[:t.strom_anzahl] for e in self.elemente_fuer_puffer(vb)]

    def pruefen(self) -> List[str]:
        fehler = []
        for i, t in enumerate(self.teilnetze):
            if t.topologie != TOPOLOGIE_DREIECKSLISTE:
                fehler.append("Teilnetz %d: Topologie %d" % (i, t.topologie))
            if t.index_anzahl % 3:
                fehler.append("Teilnetz %d: Indexzahl %d nicht durch 3 teilbar" % (i, t.index_anzahl))
            if not 0 < t.strom_anzahl <= 8:
                fehler.append("Teilnetz %d: %d Ströme" % (i, t.strom_anzahl))
            for vb in t.stroeme[:t.strom_anzahl]:
                if vb >= len(self.vb_kennungen):
                    fehler.append("Teilnetz %d: Strom %d existiert nicht" % (i, vb))
            if t.eintrag_offset % 8 or t.eintrag_offset // 8 + t.eintrag_anzahl > len(self.eintraege):
                fehler.append("Teilnetz %d: Eintrag-Offset %d ungültig" % (i, t.eintrag_offset))
        summe = 0
        for vb, (anz, erstes) in enumerate(self.strom_tabelle):
            if erstes != summe:
                fehler.append("Strom %d: erstes Element %d statt %d" % (vb, erstes, summe))
            summe += anz
        if summe != len(self.elemente):
            fehler.append("Strom-Tabelle deckt %d von %d Elementen ab" % (summe, len(self.elemente)))
        for e in self.elemente:
            if e.format not in ELEMENT_FORMATE:
                fehler.append("unbekanntes Elementformat %d" % e.format)
        for k in self.knoten:
            if k.erste_zeichnung + k.zeichnung_anzahl > len(self.zeichnungen):
                fehler.append("Knoten verweist auf Zeichnungen außerhalb")
        for z in self.zeichnungen:
            if z.teilnetz >= len(self.teilnetze):
                fehler.append("Zeichnung verweist auf Teilnetz %d" % z.teilnetz)
            else:
                t = self.teilnetze[z.teilnetz]
                if (z.start_index, z.index_anzahl) != (t.start_index, t.index_anzahl):
                    fehler.append("Zeichnung %d: Indexbereich weicht vom Teilnetz ab" % z.teilnetz)
        return fehler


# --------------------------------------------------------------------------- GfxModelRes / ModelProxyRes

MODELL_KOPF = struct.Struct("<4I4fI6fI")  # 64 Byte
MODELL_KNOTEN = struct.Struct("<Ii3f4f3f16f2I")  # 120 Byte
PROXY_KOPF = struct.Struct("<3I")


@dataclass
class ModellKnoten:
    """Knoten des Szenengraphen (120 Byte). Lokale Transformation = T * R(Quaternion xyzw) * S;
    Weltmatrix = Eltern-Kette (durch Vergleich mit der gespeicherten Bounding Box belegt).
    'matrix' war in allen untersuchten Dateien die Einheitsmatrix (Bedeutung offen)."""

    name: str
    eltern: int
    translation: Tuple[float, float, float]
    rotation: Tuple[float, float, float, float]
    skalierung: Tuple[float, float, float]
    matrix: Tuple[float, ...]
    hat_mesh: int  # 0 = Wurzel, 1 = Geometrieknoten (Vermutung)
    nummer: int  # bisher = eigener Index

    def lokale_matrix(self) -> np.ndarray:
        return trs_matrix(self.translation, self.rotation, self.skalierung)


def quaternion_matrix(q: Sequence[float]) -> np.ndarray:
    x, y, z, w = (float(v) for v in q)
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def trs_matrix(t: Sequence[float], q: Sequence[float], s: Sequence[float]) -> np.ndarray:
    m = np.eye(4)
    m[:3, :3] = quaternion_matrix(q) * np.asarray(s, float)[None, :]
    m[:3, 3] = t
    return m


def _knoten_lesen(d: bytes, offset: int, anzahl: int, namen_start: int) -> Tuple[List[ModellKnoten], List[int]]:
    knoten, name_offsets = [], []
    for i in range(anzahl):
        k = MODELL_KNOTEN.unpack_from(d, offset + MODELL_KNOTEN.size * i)
        name_offsets.append(k[0])
        knoten.append(ModellKnoten(_cstr(d, namen_start + k[0]), k[1], tuple(k[2:5]), tuple(k[5:9]),
                                   tuple(k[9:12]), tuple(k[12:28]), k[28], k[29]))
    return knoten, name_offsets


def _knoten_bytes(knoten: Sequence[ModellKnoten]) -> Tuple[bytes, bytes]:
    blob, offs = _strings_blob([k.name for k in knoten])
    teile = [MODELL_KNOTEN.pack(o, k.eltern, *k.translation, *k.rotation, *k.skalierung, *k.matrix,
                                k.hat_mesh, k.nummer) for k, o in zip(knoten, offs)]
    return b"".join(teile), blob


@dataclass
class GfxModell:
    lod_anzahl: int = 1
    knoten_anzahl: int = 0
    knochen_anzahl: int = 0  # obere 16 Bit von Feld 1, nur bei Skinning-Modellen != 0
    namen_anzahl: int = 0
    mesh_kennungen: List[str] = field(default_factory=list)
    kugel: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)  # Welt (Modellraum)
    reserviert0: int = 0
    aabb_min: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    aabb_max: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    reserviert1: int = 0
    lod_abstaende: Tuple[float, ...] = (16000.0,)  # 2*LOD-1 Werte, z. B. 80, 80, 200, 200, 800
    lod_wert: int = 2  # letztes Wort des LOD-Blocks (0/1/2, Bedeutung offen)
    knoten: List[ModellKnoten] = field(default_factory=list)
    knochen: List[int] = field(default_factory=list)  # Skinning: Knotenindizes der Knochen (u16)
    rest: Optional[bytes] = None  # nicht entschlüsselter Rest (bisher nie nötig)

    @property
    def mit_skinning(self) -> bool:
        return self.knochen_anzahl != 0

    @classmethod
    def aus_bytes(cls, d: bytes) -> "GfxModell":
        k = MODELL_KOPF.unpack_from(d, 0)
        nl, nk_raw, nn, nm = k[:4]
        m = cls(lod_anzahl=nl, knoten_anzahl=nk_raw & 0xFFFF, knochen_anzahl=nk_raw >> 16, namen_anzahl=nn,
                kugel=tuple(k[4:8]), reserviert0=k[8], aabb_min=tuple(k[9:12]), aabb_max=tuple(k[12:15]),
                reserviert1=k[15])
        if nl < 1:
            raise GrafikFormatFehler("Modell ohne LOD")
        o = MODELL_KOPF.size
        m.lod_abstaende = struct.unpack_from("<%df" % (2 * nl - 1), d, o)
        (m.lod_wert,) = struct.unpack_from("<I", d, o + 8 * nl - 4)
        o += 8 * nl
        if m.knoten_anzahl != nn:
            m.rest = d[o:]
            return m
        str_start = o + MODELL_KNOTEN.size * nn
        if m.knochen_anzahl:
            m.knochen = list(struct.unpack_from("<%dH" % m.knochen_anzahl, d, str_start))
            str_start += 2 * m.knochen_anzahl
        texte, offs = _strings_lesen(d, str_start, len(d))
        if len(texte) != nm + nn:
            raise GrafikFormatFehler("Modell: %d Zeichenketten statt %d" % (len(texte), nm + nn))
        m.mesh_kennungen = texte[:nm]
        namen_start = str_start + (offs[nm] if nn else 0)
        m.knoten, name_offs = _knoten_lesen(d, o, nn, namen_start)
        _, soll = _strings_blob([kn.name for kn in m.knoten])
        if name_offs != soll:
            raise GrafikFormatFehler("Modell: Namen-Offsets nicht fortlaufend")
        return m

    def to_bytes(self) -> bytes:
        if self.rest is not None:
            raise GrafikFormatFehler("Modell mit nicht entschlüsseltem Rest kann nicht geschrieben werden")
        if len(self.knochen) != self.knochen_anzahl:
            raise GrafikFormatFehler("Knochenliste passt nicht zur Knochenanzahl")
        if len(self.lod_abstaende) != 2 * self.lod_anzahl - 1:
            raise GrafikFormatFehler("LOD-Abstände passen nicht zur LOD-Anzahl")
        kn, namen = _knoten_bytes(self.knoten)
        mesh_blob, _ = _strings_blob(self.mesh_kennungen)
        nk = len(self.knoten)
        kopf = MODELL_KOPF.pack(self.lod_anzahl, nk | (self.knochen_anzahl << 16), nk, len(self.mesh_kennungen),
                                *self.kugel, self.reserviert0, *self.aabb_min, *self.aabb_max, self.reserviert1)
        lod = struct.pack("<%dfI" % len(self.lod_abstaende), *self.lod_abstaende, self.lod_wert)
        knochen = struct.pack("<%dH" % len(self.knochen), *self.knochen)
        return kopf + lod + kn + knochen + mesh_blob + namen

    def weltmatrizen(self) -> List[np.ndarray]:
        welt: List[np.ndarray] = []
        for i, k in enumerate(self.knoten):
            lokal = k.lokale_matrix()
            if k.eltern < 0:
                welt.append(lokal)
            elif k.eltern < i:
                welt.append(welt[k.eltern] @ lokal)
            else:
                raise GrafikFormatFehler("Elternknoten %d nach Kind %d" % (k.eltern, i))
        return welt


@dataclass
class ModellProxy:
    """ModelProxyRes (?render): Szenengraph ohne Geometrie, Knoten wie im Modell."""

    knoten: List[ModellKnoten] = field(default_factory=list)
    reserviert: int = 0
    schluss: int = -1  # letztes i32, bisher immer -1
    knochen: List[int] = field(default_factory=list)  # wie im Modell (u16 nach den Knoten)

    @classmethod
    def aus_bytes(cls, d: bytes) -> "ModellProxy":
        n_roh, namen_groesse, res = PROXY_KOPF.unpack_from(d, 0)
        n, nb = n_roh & 0xFFFF, n_roh >> 16
        o = PROXY_KOPF.size
        namen_start = o + MODELL_KNOTEN.size * n + 2 * nb
        if namen_start + namen_groesse + 4 != len(d):
            raise GrafikFormatFehler("ModelProxyRes: Größe passt nicht")
        knochen = list(struct.unpack_from("<%dH" % nb, d, namen_start - 2 * nb))
        knoten, offs = _knoten_lesen(d, o, n, namen_start)
        _, soll = _strings_blob([k.name for k in knoten])
        if offs != soll:
            raise GrafikFormatFehler("ModelProxyRes: Namen-Offsets nicht fortlaufend")
        (schluss,) = struct.unpack_from("<i", d, len(d) - 4)
        return cls(knoten, res, schluss, knochen)

    def to_bytes(self) -> bytes:
        kn, namen = _knoten_bytes(self.knoten)
        kopf = PROXY_KOPF.pack(len(self.knoten) | (len(self.knochen) << 16), len(namen), self.reserviert)
        return kopf + kn + struct.pack("<%dH" % len(self.knochen), *self.knochen) + namen + struct.pack(
            "<i", self.schluss)

    @classmethod
    def aus_modell(cls, modell: GfxModell) -> "ModellProxy":
        return cls([ModellKnoten(**vars(k)) for k in modell.knoten], knochen=list(modell.knochen))


# --------------------------------------------------------------------------- GfxMatBindRes

@dataclass
class MaterialBindung:
    """GfxMatBindRes (.emb?context=X): ordnet Materialslot-Namen (aus dem Mesh) Material-Kennungen zu.
    Aufbau: Slotzahl, Materialzahl, Größe des Slotteils (8*Slots + Namenbytes),
    je Slot (Namen-Offset, Material-Offset), Namen, Material-Kennungen."""

    slots: List[str] = field(default_factory=list)
    materialien: List[str] = field(default_factory=list)
    zuordnung: List[int] = field(default_factory=list)  # je Slot Index in materialien

    @classmethod
    def aus_bytes(cls, d: bytes) -> "MaterialBindung":
        ns, nm, groesse = struct.unpack_from("<3I", d, 0)
        paare = [PAAR.unpack_from(d, 12 + 8 * i) for i in range(ns)]
        o = 12 + 8 * ns
        texte, offs = _strings_lesen(d, o, len(d))
        if len(texte) != ns + nm:
            raise GrafikFormatFehler("MatBind: %d Zeichenketten statt %d" % (len(texte), ns + nm))
        namen_bytes = offs[ns] if nm else len(d) - o
        if groesse != 8 * ns + namen_bytes:
            raise GrafikFormatFehler("MatBind: Größenfeld %d != %d" % (groesse, 8 * ns + namen_bytes))
        name_offs = offs[:ns]
        mat_offs = [x - namen_bytes for x in offs[ns:]]
        b = cls(texte[:ns], texte[ns:])
        for i, (no, mo) in enumerate(paare):
            if no != name_offs[i] or mo not in mat_offs:
                raise GrafikFormatFehler("MatBind: Slot %d verweist ins Leere" % i)
            b.zuordnung.append(mat_offs.index(mo))
        return b

    def to_bytes(self) -> bytes:
        namen, no = _strings_blob(self.slots)
        mats, mo = _strings_blob(self.materialien)
        kopf = struct.pack("<3I", len(self.slots), len(self.materialien), 8 * len(self.slots) + len(namen))
        paare = b"".join(PAAR.pack(no[i], mo[self.zuordnung[i]]) for i in range(len(self.slots)))
        return kopf + paare + namen + mats

    def material_fuer(self, slot: str) -> Optional[str]:
        if slot in self.slots:
            return self.materialien[self.zuordnung[self.slots.index(slot)]]
        return None


# --------------------------------------------------------------------------- GfxMaterialRes

MATERIAL_KOPF = struct.Struct("<8I")
MATERIAL_PARAM = struct.Struct("<5Ii3I")  # 36 Byte

PARAM_ART = {1: "Konstante", 2: "Textur"}
PARAM_TYP = {3: "float", 0x30003: "float3", 0x40003: "float4", 0x20003: "float2", 6: "uint", 2: "texture"}


@dataclass
class MaterialParameter:
    name: str
    art: int  # 1 Konstante, 2 Textur
    typ: int  # 3 float, 0x30003 float3, 0x40003 float4, 6 uint, 2 Textur
    klasse: int  # 3 = Heap-Index einer Textur, sonst 1
    groesse: int  # Bytes (Konstante) bzw. Länge der Texturkennung inkl. Null
    slot: int  # -1 bei Texturen
    cb_offset: int  # Offset im Konstantenpuffer (GPU-Layout)
    wert_offset: int  # Offset im Wertebereich (Konstante) bzw. in den Texturkennungen
    heap_offset: int  # Heap-Index-Parameter: Offset der Textur in der zweiten Texturliste
    wert: object = None
    textur: Optional[str] = None


@dataclass
class Material:
    """GfxMaterialRes: Kopf (Parameterzahl, Konstantenpuffer-Zahl, dann 6 Offsets relativ zum
    Ende der Parametertabelle: Werte-Größe, Shader, Namen, Texturen, Heap-Texturen,
    Puffernamen), Parametertabelle (36 B), Werte, Konstantenpuffer (Name, Größe), Zeichenketten."""

    shader: str = ""
    parameter: List[MaterialParameter] = field(default_factory=list)
    werte: bytes = b""
    puffer: List[Tuple[str, int]] = field(default_factory=list)
    _roh: bytes = b""

    @classmethod
    def aus_bytes(cls, d: bytes) -> "Material":
        h = MATERIAL_KOPF.unpack_from(d, 0)
        n, n_cb, werte_groesse, o_sh, o_nam, o_tex, o_heap, o_cb = h
        basis = MATERIAL_KOPF.size + MATERIAL_PARAM.size * n
        if o_sh != werte_groesse + 8 * n_cb:
            raise GrafikFormatFehler("Material: Shader-Offset %d unerwartet" % o_sh)
        m = cls(shader=_cstr(d, basis + o_sh), werte=d[basis:basis + werte_groesse], _roh=d)
        for i in range(n_cb):
            no, gr = PAAR.unpack_from(d, basis + werte_groesse + 8 * i)
            m.puffer.append((_cstr(d, basis + o_cb + no), gr))
        for i in range(n):
            p = MATERIAL_PARAM.unpack_from(d, MATERIAL_KOPF.size + MATERIAL_PARAM.size * i)
            par = MaterialParameter(_cstr(d, basis + o_nam + p[0]), *p[1:])
            if par.art == 2:
                par.textur = _cstr(d, basis + o_tex + par.wert_offset)
            else:
                fmt = {3: "f", 0x20003: "2f", 0x30003: "3f", 0x40003: "4f"}.get(par.typ, "%dI" % (par.groesse // 4))
                par.wert = struct.unpack_from("<" + fmt, d, basis + par.wert_offset)
                if par.klasse == 3:
                    par.textur = _cstr(d, basis + o_heap + par.heap_offset)
            m.parameter.append(par)
        return m

    def to_bytes(self) -> bytes:
        return self._roh  # Schreiben neuer Materialien ist noch nicht nötig (vorhandene wiederverwenden)

    def texturen(self) -> Dict[str, str]:
        return {p.name: p.textur for p in self.parameter if p.art == 2}

    def konstanten(self) -> Dict[str, object]:
        return {p.name: p.wert for p in self.parameter if p.art == 1}


def _zeichenketten(bereich: bytes) -> Dict[int, str]:
    """Dicht gepackte, nullterminierte Zeichenketten eines Bereichs: {Offset: Text}."""
    erg, i = {}, 0
    while i < len(bereich):
        j = bereich.index(b"\0", i)
        erg[i] = bereich[i:j].decode("utf-8")
        i = j + 1
    return erg


def material_aendern(d: bytes, konstanten: Optional[Dict[str, Sequence[float]]] = None,
                     texturen: Optional[Dict[str, str]] = None) -> bytes:
    """Schreibt ein GfxMaterialRes mit geänderten Konstanten und/oder Textur-Kennungen (Welttexturen, 29.10.2026).

    ``konstanten``: {Parametername: Wert(e)}; Typ und Größe bleiben, der Wert wird an Ort und Stelle ersetzt.
    ``texturen``: {alte Kennung: neue Kennung} für Textur- und Heap-Texturkennungen (SRV-Kennungen ``….tif``).
    Aufbau (gesichert an allen 724 Träger-Materialien): nach Werten und Konstantenpuffer-Paaren folgen dicht
    gepackt Shader, Parameternamen, Texturen, Heap-Texturen, Puffernamen. Ohne Änderung bitgleich."""
    n, n_cb, wg, o_sh, o_nam, o_tex, o_heap, o_cb = MATERIAL_KOPF.unpack_from(d, 0)
    basis = MATERIAL_KOPF.size + MATERIAL_PARAM.size * n
    params = [list(MATERIAL_PARAM.unpack_from(d, MATERIAL_KOPF.size + MATERIAL_PARAM.size * i)) for i in range(n)]
    namen = [_cstr(d, basis + o_nam + q[0]) for q in params]
    werte = bytearray(d[basis:basis + wg])
    for name, wert in (konstanten or {}).items():
        treffer = [q for q, nm in zip(params, namen) if nm == name and q[1] == 1]
        if not treffer:
            raise KeyError("Materialkonstante fehlt: %s" % name)
        q = treffer[0]
        fmt = {3: "f", 0x20003: "2f", 0x30003: "3f", 0x40003: "4f"}.get(q[2])
        if fmt is None:
            raise GrafikFormatFehler("Konstante %s mit Typ 0x%x nicht schreibbar" % (name, q[2]))
        werte_liste = [float(wert)] if isinstance(wert, (int, float)) else [float(v) for v in wert]
        struct.pack_into("<" + fmt, werte, q[7], *werte_liste)
    kopf_rest = d[basis + wg:basis + o_tex]          # Konstantenpuffer-Paare, Shader, Namen
    tex = _zeichenketten(d[basis + o_tex:basis + o_heap])
    heap = _zeichenketten(d[basis + o_heap:basis + o_cb])
    tausch = texturen or {}

    def neu_packen(alt):
        bereich, abbild = bytearray(), {}
        for off, text in alt.items():
            abbild[off] = len(bereich)
            bereich += tausch.get(text, text).encode("utf-8") + b"\0"
        return bytes(bereich), abbild
    tex_neu, tex_abb = neu_packen(tex)
    heap_neu, heap_abb = neu_packen(heap)
    for q in params:
        if q[1] == 2:                                  # Textur: Offset und Länge inkl. Null
            q[4] = len(tausch.get(tex[q[7]], tex[q[7]]).encode("utf-8")) + 1
            q[7] = tex_abb[q[7]]
        elif q[3] == 3:                                # Heap-Index einer Textur
            q[8] = heap_abb[q[8]]
    o_heap_neu = o_tex + len(tex_neu)
    o_cb_neu = o_heap_neu + len(heap_neu)
    out = bytearray(MATERIAL_KOPF.pack(n, n_cb, wg, o_sh, o_nam, o_tex, o_heap_neu, o_cb_neu))
    for q in params:
        out += MATERIAL_PARAM.pack(*q)
    out += werte + kopf_rest + tex_neu + heap_neu + d[basis + o_cb:]
    return bytes(out)


# --------------------------------------------------------------------------- Texturen

TEXTUR_KOPF = struct.Struct("<10I")  # 40 Byte

# Formatcode -> (DXGI-Format, Bytes je 4x4-Block oder Pixel, Blockformat?, Name, gesichert?)
# gesichert = Blockformat durch Dekodierung belegt (Bilder in testausgabe/grafik/texturen). Ob sRGB
# oder linear, ist aus der Verwendung geschlossen (_d/_da = sRGB) und nicht prüfbar.
TEXTUR_FORMATE: Dict[int, Tuple[int, int, bool, str, bool]] = {
    15: (28, 4, False, "R8G8B8A8_UNORM", True),
    52: (71, 8, True, "BC1_UNORM", True),
    54: (72, 8, True, "BC1_UNORM_SRGB", True),
    55: (77, 16, True, "BC3_UNORM", True),
    57: (78, 16, True, "BC3_UNORM_SRGB", True),
    62: (78, 16, True, "BC3_UNORM_SRGB?", True),
    63: (80, 8, True, "BC4_UNORM", True),
    65: (83, 16, True, "BC5_UNORM", True),
    67: (95, 16, True, "BC6H_UF16", True),
    69: (98, 16, True, "BC7_UNORM", True),
    70: (99, 16, True, "BC7_UNORM_SRGB", True),
}


@dataclass
class ExterneMip:
    flag: int
    codec: int  # 3 = LZ4-Block (belegt durch Entpacken)
    offset: int
    gepackt: int
    entpackt: int


@dataclass
class Textur:
    """GfxSurfaceRes: Fragment 0 = Kopf (40 B), Fragment 1 = Mip-Stufen (kleinste Stufen, ab der
    höchsten im ERP vorhandenen), optional Fragment 2 = Verweis auf externe .mipmaps-Datei mit den
    größten Stufen (LZ4-Blöcke)."""

    dimension: int
    flags: int
    format: int
    breite: int
    hoehe: int
    tiefe: int
    mips: int
    schichten: int
    reserviert: Tuple[int, int]
    daten: bytes = b""
    extern_pfad: Optional[str] = None
    extern_mips: List[ExterneMip] = field(default_factory=list)
    extern_rest: bytes = b""

    @classmethod
    def aus_ressource(cls, res: erp.ErpResource, mit_daten: bool = True) -> "Textur":
        k = TEXTUR_KOPF.unpack(res.fragments[0].read())
        t = cls(*k[:8], reserviert=tuple(k[8:10]))
        if mit_daten:
            t.daten = res.fragments[1].read()
        if len(res.fragments) > 2:
            t._extern_lesen(res.fragments[2].read())
        return t

    def _extern_lesen(self, d: bytes) -> None:
        laenge = d[0]  # Länge der Kennung ohne Nullbyte
        self.extern_pfad = d[1:1 + laenge].decode("utf-8", "surrogateescape")
        o = 2 + laenge
        (n,) = struct.unpack_from("<I", d, o)
        o += 4
        for _ in range(n):
            fl, codec, off, gep, ent = struct.unpack_from("<BBQQQ", d, o)
            self.extern_mips.append(ExterneMip(fl, codec, off, gep, ent))
            o += 26
        self.extern_rest = d[o:]

    @property
    def format_info(self) -> Optional[Tuple[int, int, bool, str, bool]]:
        return TEXTUR_FORMATE.get(self.format)

    def stufen_groessen(self, breite: Optional[int] = None, hoehe: Optional[int] = None,
                        anzahl: Optional[int] = None) -> List[Tuple[int, int, int]]:
        """(Breite, Höhe, Bytes) je Mip-Stufe einer Schicht."""
        info = self.format_info
        if info is None:
            raise GrafikFormatFehler("Unbekanntes Texturformat %d" % self.format)
        _, bpb, block, _, _ = info
        w, h = breite or self.breite, hoehe or self.hoehe
        out = []
        for _ in range(anzahl or self.mips):
            if block:
                gr = max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * bpb
            else:
                gr = w * h * bpb
            out.append((w, h, gr))
            w, h = max(1, w // 2), max(1, h // 2)
        return out

    def erwartete_groesse(self) -> int:
        return sum(g for _, _, g in self.stufen_groessen()) * self.tiefe * self.schichten

    def pruefen(self) -> List[str]:
        fehler = []
        if self.format_info is None:
            fehler.append("unbekanntes Texturformat %d" % self.format)
        elif self.daten and len(self.daten) != self.erwartete_groesse() and self.dimension == 2:
            fehler.append("Datengröße %d != erwartet %d" % (len(self.daten), self.erwartete_groesse()))
        voll = len(self.extern_mips)
        for i, em in enumerate(self.extern_mips):
            w, h = self.breite << (voll - i), self.hoehe << (voll - i)
            soll = self.stufen_groessen(w, h, 1)[0][2] if self.format_info else None
            if soll is not None and em.entpackt != soll:
                fehler.append("externe Stufe %d: %d statt %d Byte" % (i, em.entpackt, soll))
        return fehler


@dataclass
class TexturSicht:
    """GfxSRVResource (.tif): Sichtart, Format, Mip-Zahl und Kennung der .image-Ressource."""

    art: int
    format: int
    feld2: int
    mips: int
    feld4: int
    feld5: int
    bild: str

    @classmethod
    def aus_bytes(cls, d: bytes) -> "TexturSicht":
        k = struct.unpack_from("<6I", d, 0)
        return cls(*k, bild=_cstr(d, 24))

    def to_bytes(self) -> bytes:
        return struct.pack("<6I", self.art, self.format, self.feld2, self.mips, self.feld4,
                           self.feld5) + self.bild.encode() + b"\0"


# --------------------------------------------------------------------------- PYRB (Kollision)

@dataclass
class Kollision:
    """PYRB: Starrkörper-Beschreibung (grob). Fragment 'syst' enthält längenpräfixierte
    Zeichenketten (Körpername, 'rbDefault', Knotenname '^…', Material 'sMetal' …) und
    Transformationen/Abmessungen einfacher Grundformen; Fragment 'temp' = 'RBLI' + Größe."""

    version: int
    zeichenketten: List[Tuple[int, str]]
    koerper: int
    materialien: List[str]
    rbli: bytes

    @classmethod
    def aus_ressource(cls, res: erp.ErpResource) -> "Kollision":
        d = res.fragments[0].read()
        rbli = res.fragments[1].read() if len(res.fragments) > 1 else b""
        (version,) = struct.unpack_from("<I", d, 0)
        texte = []
        for m in re.finditer(rb"[\x04-\x40]\x00\x00\x00([\^A-Za-z_][\x20-\x7e]{1,62})\x00", d):
            (laenge,) = struct.unpack_from("<I", d, m.start())
            text = m.group(1).decode()
            if laenge == ((len(text) + 1 + 3) // 4) * 4:
                texte.append((m.start(), text))
        koerper = sum(1 for _, t in texte if t == "rbDefault")
        mats = [t for _, t in texte if re.fullmatch(r"s[A-Z][A-Za-z]+", t)]
        return cls(version, texte, koerper, mats, rbli)


# --------------------------------------------------------------------------- TerrainRes

TERRAIN_KOPF = struct.Struct("<4I")
TERRAIN_KNOTEN = struct.Struct("<4f4f4f")  # 48 B: Kugel, AABB-Min (+0), AABB-Max (+0)


@dataclass
class TerrainZeichnung:
    start_index: int
    index_anzahl: int
    basis_vertex: int
    feld3: int
    material: int


@dataclass
class Terrain:
    """TerrainRes (?context=render): Fahrbahn und Gelände. Aufbau (alle Bereiche lückenlos):
    Kopf (Knoten, Materialien, Zeichnungen, Vertexpuffer), Knoten (48 B: Kugel + AABB),
    Knotenzuordnung (16 B: Zeichnungszahl, erste Zeichnung, erste Zeichnung, Raster-z<<16|Raster-x),
    Zeichnungen (16 B: Start, Anzahl, Basis-Vertex, 0), Material je Zeichnung (u32),
    Vertexpuffer je Material (8 x i32, -1 = leer), Stride je Vertexpuffer (u32),
    8 Byte je Knoten (bisher 0), Zeichenketten-Offsets, Zeichenketten (IB, VBs, Materialien)."""

    knoten: List[Tuple[float, ...]] = field(default_factory=list)
    knoten_zuordnung: List[Tuple[int, int, int, int]] = field(default_factory=list)
    zeichnungen: List[TerrainZeichnung] = field(default_factory=list)
    material_puffer: List[List[int]] = field(default_factory=list)
    strides: List[int] = field(default_factory=list)
    knoten_extra: bytes = b""
    ib_kennung: str = ""
    vb_kennungen: List[str] = field(default_factory=list)
    materialien: List[str] = field(default_factory=list)
    _zeichenketten: Optional[Tuple[List[str], bytes, List[int]]] = field(default=None, repr=False)

    @classmethod
    def aus_bytes(cls, d: bytes) -> "Terrain":
        nk, nm, nz, nv = TERRAIN_KOPF.unpack_from(d, 0)
        t = cls()
        o = TERRAIN_KOPF.size
        t.knoten = [TERRAIN_KNOTEN.unpack_from(d, o + 48 * i) for i in range(nk)]
        o += 48 * nk
        t.knoten_zuordnung = [struct.unpack_from("<4I", d, o + 16 * i) for i in range(nk)]
        o += 16 * nk
        roh = [struct.unpack_from("<4I", d, o + 16 * i) for i in range(nz)]
        o += 16 * nz
        mats = struct.unpack_from("<%dI" % nz, d, o)
        o += 4 * nz
        t.zeichnungen = [TerrainZeichnung(*z, material=m) for z, m in zip(roh, mats)]
        t.material_puffer = [list(struct.unpack_from("<8i", d, o + 32 * i)) for i in range(nm)]
        o += 32 * nm
        t.strides = list(struct.unpack_from("<%dI" % nv, d, o))
        o += 4 * nv
        t.knoten_extra = d[o:o + 8 * nk]
        o += 8 * nk
        ns = 1 + nv + nm
        offs = struct.unpack_from("<%dI" % ns, d, o)
        o += 4 * ns
        pool, ist = _strings_lesen(d, o, len(d))
        gueltige_offsets = set(ist)
        if any(v not in gueltige_offsets for v in offs):
            raise GrafikFormatFehler("TerrainRes: Zeichenketten passen nicht")
        # Einige Strecken teilen auch Zeichenketten (zwei Materialslots, eine Kennung).
        nach_offset = dict(zip(ist, pool))
        texte = [nach_offset[v] for v in offs]
        t._zeichenketten = (texte[:], d[o:], list(offs))
        t.ib_kennung, t.vb_kennungen, t.materialien = texte[0], texte[1:1 + nv], texte[1 + nv:]
        return t

    def to_bytes(self) -> bytes:
        nk, nm, nz, nv = len(self.knoten), len(self.materialien), len(self.zeichnungen), len(self.vb_kennungen)
        teile = [TERRAIN_KOPF.pack(nk, nm, nz, nv)]
        teile += [TERRAIN_KNOTEN.pack(*k) for k in self.knoten]
        teile += [struct.pack("<4I", *z) for z in self.knoten_zuordnung]
        teile += [struct.pack("<4I", z.start_index, z.index_anzahl, z.basis_vertex, z.feld3) for z in self.zeichnungen]
        teile.append(struct.pack("<%dI" % nz, *[z.material for z in self.zeichnungen]))
        teile += [struct.pack("<8i", *p) for p in self.material_puffer]
        teile.append(struct.pack("<%dI" % nv, *self.strides))
        teile.append(self.knoten_extra)
        texte = [self.ib_kennung] + self.vb_kennungen + self.materialien
        if self._zeichenketten is not None and texte == self._zeichenketten[0]:
            _, blob, offs = self._zeichenketten
        else:
            blob, offs = _strings_blob(texte)
        teile.append(struct.pack("<%dI" % len(offs), *offs))
        teile.append(blob)
        return b"".join(teile)


# --------------------------------------------------------------------------- Ressourcen-Index

class RessourcenIndex:
    """Sucht Ressourcen über mehrere ERP-Dateien hinweg (erste Fundstelle gewinnt).
    Modelle verweisen auf Materialien/Texturen, die in anderen Dateien liegen können."""

    def __init__(self, pfade: Iterable[str] = ()):
        self.archive: List[erp.ErpFile] = []
        self._index: Dict[str, erp.ErpResource] = {}
        for p in pfade:
            self.hinzufuegen(erp.open_erp(p))

    def hinzufuegen(self, archiv: erp.ErpFile) -> None:
        self.archive.append(archiv)
        for r in archiv.resources:
            self._index.setdefault(r.identifier, r)

    def neu_indizieren(self) -> None:
        self._index = {}
        for a in self.archive:
            for r in a.resources:
                self._index.setdefault(r.identifier, r)

    def get(self, kennung: str) -> Optional[erp.ErpResource]:
        return self._index.get(kennung)

    def __getitem__(self, kennung: str) -> erp.ErpResource:
        r = self._index.get(kennung)
        if r is None:
            raise KeyError(kennung)
        return r

    def alle(self, typ: Optional[str] = None) -> List[erp.ErpResource]:
        return [r for r in self._index.values() if typ is None or r.resource_type == typ]

    def schliessen(self) -> None:
        for a in self.archive:
            a.close()

    def __enter__(self) -> "RessourcenIndex":
        return self

    def __exit__(self, *exc) -> None:
        self.schliessen()


def _als_index(quelle) -> RessourcenIndex:
    if isinstance(quelle, RessourcenIndex):
        return quelle
    idx = RessourcenIndex()
    if isinstance(quelle, erp.ErpFile):
        idx.hinzufuegen(quelle)
    else:
        for a in quelle:
            idx.hinzufuegen(a)
    return idx


# --------------------------------------------------------------------------- Geometrie lesen

@dataclass
class Netz:
    """Dekodierte Geometrie eines Teilnetzes (Dreiecksliste)."""

    name: str
    material: str
    positionen: np.ndarray  # (N, 3) float32
    dreiecke: np.ndarray  # (M, 3) int
    normalen: Optional[np.ndarray] = None
    uvs: Optional[np.ndarray] = None
    farben: Optional[np.ndarray] = None
    attribute: Dict[str, np.ndarray] = field(default_factory=dict)


def element_lesen(puffer: GfxPuffer, element: VertexElement, zeilen: Optional[np.ndarray] = None) -> np.ndarray:
    fmt = ELEMENT_FORMATE.get(element.format)
    if fmt is None:
        raise GrafikFormatFehler("Unbekanntes Elementformat %d" % element.format)
    mat = puffer.matrix()
    if zeilen is not None:
        mat = mat[zeilen]
    teil = np.ascontiguousarray(mat[:, element.offset:element.offset + fmt.groesse])
    werte = teil.view(fmt.dtype).reshape(len(teil), fmt.komponenten)
    werte = werte.astype(np.float32)
    if fmt.normiert:
        werte /= 255.0
    return werte


def mesh_netze(idx: RessourcenIndex, mesh: GfxMesh, weltmatrizen: Optional[List[np.ndarray]] = None,
               knoten_namen: Optional[List[str]] = None, nur_positionen: bool = False) -> List[Netz]:
    """Alle Zeichnungen eines Meshs als Netze; mit Weltmatrizen in Modellraum transformiert."""
    puffer_cache: Dict[int, GfxPuffer] = {}

    def puffer(vb: int) -> GfxPuffer:
        if vb not in puffer_cache:
            puffer_cache[vb] = GfxPuffer.aus_ressource(idx[mesh.vb_kennungen[vb]])
        return puffer_cache[vb]

    ib = GfxPuffer.aus_ressource(idx[mesh.ib_kennungen[0]])
    alle_indizes = ib.indizes()
    zuordnung: List[Tuple[int, Optional[int]]] = []
    if mesh.knoten:
        for k in mesh.knoten:
            for z in range(k.erste_zeichnung, k.erste_zeichnung + k.zeichnung_anzahl):
                zuordnung.append((z, k.modell_knoten))
    else:
        zuordnung = [(i, None) for i in range(len(mesh.teilnetze))]
    netze = []
    for z, mk in zuordnung:
        if mesh.knoten:
            ze = mesh.zeichnungen[z]
            tn_i = ze.teilnetz
        else:
            tn_i = z
        tn = mesh.teilnetze[tn_i]
        ind = alle_indizes[tn.start_index:tn.start_index + tn.index_anzahl].astype(np.int64) + tn.basis_vertex
        if len(ind) == 0:
            continue
        benutzt, lokal = np.unique(ind, return_inverse=True)
        attr: Dict[str, np.ndarray] = {}
        for vb, el in mesh.layout(tn_i):
            p = puffer(vb)
            if el.format not in ELEMENT_FORMATE or (nur_positionen and el.verwendung != 1):
                continue
            attr[el.name] = element_lesen(p, el, benutzt)
        if "POSITION0" not in attr:
            continue
        pos = attr["POSITION0"][:, :3].copy()
        nrm = attr.get("NORMAL0")
        nrm = nrm[:, :3].copy() if nrm is not None else None
        if weltmatrizen is not None and mk is not None and mk < len(weltmatrizen):
            w = weltmatrizen[mk]
            pos = (pos @ w[:3, :3].T + w[:3, 3]).astype(np.float32)
            if nrm is not None:
                inv_t = np.linalg.inv(w[:3, :3]).T
                nrm = nrm @ inv_t.T
                laenge = np.linalg.norm(nrm, axis=1, keepdims=True)
                nrm = (nrm / np.maximum(laenge, 1e-12)).astype(np.float32)
        uv = attr.get("TEXCOORD0")
        name = knoten_namen[mk] if (knoten_namen and mk is not None and mk < len(knoten_namen)) else "teilnetz%d" % tn_i
        netze.append(Netz(name=name, material=mesh.materialnamen[tn.material] if mesh.materialnamen else "",
                          positionen=pos, dreiecke=lokal.reshape(-1, 3), normalen=nrm,
                          uvs=uv[:, :2].copy() if uv is not None else None,
                          farben=attr.get("COLOR0"), attribute=attr))
    return netze


@dataclass
class GeladenesModell:
    kennung: str
    modell: GfxModell
    meshes: List[GfxMesh]
    netze: List[Netz]


def modell_laden(quelle, kennung: str, lod: int = 0, welt: bool = True, nur_positionen: bool = False) -> GeladenesModell:
    """Lädt ein Modell (Kennung '...?model' oder Basis '...idf') mit Geometrie der gewünschten LOD."""
    idx = _als_index(quelle)
    if not kennung.endswith("?model"):
        kennung = modell_kennung(basis_kennung(kennung))
    modell = GfxModell.aus_bytes(idx[kennung].data())
    meshes = [GfxMesh.aus_bytes(idx[k].data()) for k in modell.mesh_kennungen]
    welt_m = modell.weltmatrizen() if (welt and modell.rest is None) else None
    namen = [k.name for k in modell.knoten] if modell.knoten else None
    netze = mesh_netze(idx, meshes[lod], welt_m, namen, nur_positionen) if lod < len(meshes) else []
    return GeladenesModell(kennung, modell, meshes, netze)


def terrain_netze(quelle, kennung: str, materialien: Optional[Sequence[int]] = None,
                  max_zeichnungen: Optional[int] = None,
                  bereich: Optional[Tuple[float, float, float]] = None) -> List[Netz]:
    """TerrainRes als Netze, eines je Material (nur Positionen; Strom 0 jedes Materials).
    bereich = (x, z, Radius): nur Dreiecke, deren erster Eckpunkt in diesem Kreis liegt."""
    idx = _als_index(quelle)
    t = Terrain.aus_bytes(idx[kennung].data())
    ib = GfxPuffer.aus_ressource(idx[t.ib_kennung]).indizes()
    pos_cache: Dict[int, np.ndarray] = {}

    def positionen(vb: int) -> np.ndarray:
        if vb not in pos_cache:
            p = GfxPuffer.aus_ressource(idx[t.vb_kennungen[vb]])
            pos_cache[vb] = np.frombuffer(p.daten, "<f4").reshape(-1, p.stride // 4)[:, :3]
        return pos_cache[vb]

    gruppen: Dict[int, List[np.ndarray]] = {}
    for n, z in enumerate(t.zeichnungen):
        if max_zeichnungen is not None and n >= max_zeichnungen:
            break
        if materialien is not None and z.material not in materialien:
            continue
        vbs = [v for v in t.material_puffer[z.material] if v >= 0]
        if not vbs:
            continue
        tri = (ib[z.start_index:z.start_index + z.index_anzahl].astype(np.int64) + z.basis_vertex).reshape(-1, 3)
        if bereich is not None:
            p = positionen(vbs[0])[tri[:, 0]]
            tri = tri[np.hypot(p[:, 0] - bereich[0], p[:, 2] - bereich[1]) < bereich[2]]
        if len(tri):
            gruppen.setdefault(z.material, []).append(tri.ravel())
    netze = []
    for m, teile in sorted(gruppen.items()):
        vb0 = [v for v in t.material_puffer[m] if v >= 0][0]
        ind = np.concatenate(teile)
        benutzt, lokal = np.unique(ind, return_inverse=True)
        netze.append(Netz(name="terrain_m%02d" % m, material=t.materialien[m],
                          positionen=positionen(vb0)[benutzt].astype(np.float32), dreiecke=lokal.reshape(-1, 3)))
    return netze


def terrain_positions_puffer(t: Terrain) -> List[int]:
    """Indizes der Positionspuffer (jeweils erster Puffer eines Material-Vertexsatzes)."""
    out = []
    for liste in t.material_puffer:
        vbs = [v for v in liste if v >= 0]
        if vbs and vbs[0] not in out:
            out.append(vbs[0])
    return sorted(out)


def terrain_verformen(archiv: erp.ErpFile, kennung: str, x: float, z: float, radius: float,
                      hoehe: float, gewicht=None) -> Dict[str, object]:
    """Hebt (hoehe > 0) oder senkt alle Terrain-Vertices im Kreis (x, z, radius) mit weichem
    Kosinus-Übergang an. Betrifft alle Vertexsätze, damit an Materialgrenzen keine Risse
    entstehen. Quadtree-Knoten, die den Kreis berühren, bekommen erweiterte Hüllkörper.
    Normalen und Fahrphysik (TRN~) bleiben unverändert (rein optischer Test).
    ``gewicht``: optional Funktion (Positionen N×3) → Faktor 0…1 je Vertex, z. B. um den Lochrand festzuhalten."""
    res = archiv.find(kennung)
    if res is None or res.resource_type != "TerrainRes":
        raise KeyError(kennung)
    t = Terrain.aus_bytes(res.data())
    bericht: Dict[str, object] = {"puffer": [], "knoten": 0}
    y_min, y_max = math.inf, -math.inf
    for vb in terrain_positions_puffer(t):
        pres = archiv.find(t.vb_kennungen[vb])
        p = GfxPuffer.aus_ressource(pres)
        if p.stride != 12:
            raise GrafikFormatFehler("Positionspuffer %d hat Stride %d" % (vb, p.stride))
        pos = np.frombuffer(p.daten, "<f4").reshape(-1, 3).copy()
        d = np.hypot(pos[:, 0] - x, pos[:, 2] - z)
        maske = d < radius
        if not maske.any():
            continue
        dy = hoehe * 0.5 * (1.0 + np.cos(np.pi * d[maske] / radius))
        if gewicht is not None:
            dy = dy * np.asarray(gewicht(pos[maske].astype(np.float64)), np.float64)
        pos[maske, 1] += dy.astype(np.float32)
        y_min = min(y_min, float(pos[maske, 1].min()))
        y_max = max(y_max, float(pos[maske, 1].max()))
        pres.set_fragment_data(1, pos.astype("<f4").tobytes())
        bericht["puffer"].append((pres.identifier, int(maske.sum())))
    if not bericht["puffer"]:
        raise ValueError("Keine Terrain-Vertices im Bereich")
    neu = []
    for k in t.knoten:
        k = list(k)
        mnx, mny, mnz, mxx, mxy, mxz = k[4], k[5], k[6], k[8], k[9], k[10]
        nx, nz = min(max(x, mnx), mxx), min(max(z, mnz), mxz)
        if math.hypot(nx - x, nz - z) < radius:
            k[5], k[9] = min(mny, y_min), max(mxy, y_max)
            cx, cy, cz = k[0], k[1], k[2]
            k[3] = max(k[3], max(math.dist((cx, cy, cz), (ex, ey, ez))
                                 for ex in (k[4], k[8]) for ey in (k[5], k[9]) for ez in (k[6], k[10])))
            bericht["knoten"] += 1
        neu.append(tuple(k))
    t.knoten = neu
    res.set_fragment_data(0, t.to_bytes())
    bericht.update({"y_min": y_min, "y_max": y_max})
    return bericht


# --------------------------------------------------------------------------- OBJ-Export

def obj_schreiben(netze: Sequence[Netz], obj_pfad: str, mtl_name: Optional[str] = None,
                  material_texturen: Optional[Dict[str, str]] = None, kommentar: str = "") -> None:
    """Schreibt Wavefront-OBJ (+MTL). Koordinaten unverändert (Engine: y oben)."""
    import os
    mtl_name = mtl_name or os.path.splitext(os.path.basename(obj_pfad))[0] + ".mtl"
    zeilen = ["# EGO-Werkzeuge: Export aus F1 25", "# " + kommentar if kommentar else "", "mtllib " + mtl_name]
    v_off = 1
    materialien = []
    for n in netze:
        zeilen.append("o %s" % re.sub(r"\s+", "_", n.name))
        zeilen += ["v %.6f %.6f %.6f" % tuple(p) for p in n.positionen]
        hat_vt = n.uvs is not None
        hat_vn = n.normalen is not None
        if hat_vt:
            zeilen += ["vt %.6f %.6f" % (u, 1.0 - v) for u, v in n.uvs]
        if hat_vn:
            zeilen += ["vn %.5f %.5f %.5f" % tuple(x) for x in n.normalen]
        mat = re.sub(r"[^\w.-]+", "_", n.material or "standard")
        if mat not in materialien:
            materialien.append(mat)
        zeilen.append("usemtl %s" % mat)
        for a, b, c in n.dreiecke + v_off:
            if hat_vt and hat_vn:
                zeilen.append("f %d/%d/%d %d/%d/%d %d/%d/%d" % (a, a, a, b, b, b, c, c, c))
            elif hat_vn:
                zeilen.append("f %d//%d %d//%d %d//%d" % (a, a, b, b, c, c))
            else:
                zeilen.append("f %d %d %d" % (a, b, c))
        v_off += len(n.positionen)
    with open(obj_pfad, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(z for z in zeilen if z is not None) + "\n")
    mtl = []
    for i, mat in enumerate(materialien):
        farbe = _farbe(i)
        mtl += ["newmtl %s" % mat, "Kd %.3f %.3f %.3f" % farbe, "Ka 0 0 0", "Ks 0 0 0", "d 1"]
        if material_texturen and mat in material_texturen:
            mtl.append("map_Kd %s" % material_texturen[mat])
        mtl.append("")
    with open(os.path.join(os.path.dirname(obj_pfad), mtl_name), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(mtl))


def _farbe(i: int) -> Tuple[float, float, float]:
    h = (i * 0.61803398875) % 1.0
    r, g, b = [abs(((h * 6 + k) % 6) - 3) - 1 for k in (0, 4, 2)]
    return tuple(0.35 + 0.55 * min(1.0, max(0.0, x)) for x in (r, g, b))


# --------------------------------------------------------------------------- Geometrie schreiben

def kugel_aus_punkten(p: np.ndarray) -> Tuple[float, float, float, float]:
    """Konservative Hüllkugel: Mittelpunkt der AABB, Radius bis zum fernsten Punkt."""
    mn, mx = p.min(0), p.max(0)
    c = (mn + mx) / 2.0
    r = float(np.sqrt(((p - c) ** 2).sum(1)).max())
    return (float(c[0]), float(c[1]), float(c[2]), r)


def _tangenten(pos: np.ndarray, nrm: np.ndarray, uv: np.ndarray, tri: np.ndarray) -> np.ndarray:
    tan = np.zeros_like(pos, dtype=np.float64)
    p0, p1, p2 = pos[tri[:, 0]], pos[tri[:, 1]], pos[tri[:, 2]]
    t0, t1, t2 = uv[tri[:, 0]], uv[tri[:, 1]], uv[tri[:, 2]]
    e1, e2 = p1 - p0, p2 - p0
    d1, d2 = t1 - t0, t2 - t0
    det = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    det = np.where(np.abs(det) < 1e-12, 1.0, det)
    t = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) / det[:, None]
    for k in range(3):
        np.add.at(tan, tri[:, k], t)
    tan -= nrm * (tan * nrm).sum(1, keepdims=True)
    laenge = np.linalg.norm(tan, axis=1)
    ersatz = np.cross(nrm, np.array([0.0, 1.0, 0.0]))
    ersatz[np.linalg.norm(ersatz, axis=1) < 1e-6] = (1.0, 0.0, 0.0)
    tan = np.where(laenge[:, None] > 1e-9, tan, ersatz)
    return tan / np.linalg.norm(tan, axis=1, keepdims=True)


def normalen_berechnen(pos: np.ndarray, tri: np.ndarray) -> np.ndarray:
    n = np.zeros_like(pos, dtype=np.float64)
    fn = np.cross(pos[tri[:, 1]] - pos[tri[:, 0]], pos[tri[:, 2]] - pos[tri[:, 0]])
    for k in range(3):
        np.add.at(n, tri[:, k], fn)
    laenge = np.linalg.norm(n, axis=1, keepdims=True)
    return n / np.maximum(laenge, 1e-12)


def _element_werte(el: VertexElement, pos, nrm, tan, uv, vorlage: Optional[np.ndarray]) -> np.ndarray:
    n = len(pos)
    fmt = ELEMENT_FORMATE[el.format]
    v = el.verwendung
    if v == 1:
        w = np.zeros((n, fmt.komponenten))
        w[:, :3] = pos
        if fmt.komponenten == 4:
            w[:, 3] = 1.0
    elif v == 2:
        w = np.zeros((n, fmt.komponenten))
        w[:, :3] = nrm
    elif v == 3:
        w = np.zeros((n, fmt.komponenten))
        w[:, :3] = tan
        if fmt.komponenten == 4:
            w[:, 3] = vorlage[0, 3] if vorlage is not None and len(vorlage) else 1.0
    elif v == 5:
        w = np.zeros((n, fmt.komponenten))
        w[:, :2] = uv
        if fmt.komponenten == 4 and vorlage is not None and len(vorlage):
            w[:, 2:] = vorlage[0, 2:4]  # Original: (u, v, 0, 1) bzw. (u, v, 0, 0)
    elif v == 6:
        w = np.ones((n, fmt.komponenten))
    else:
        w = np.zeros((n, fmt.komponenten))
        if vorlage is not None and len(vorlage):
            w[:] = vorlage[0]
    return w


def _element_bytes(el: VertexElement, werte: np.ndarray) -> np.ndarray:
    fmt = ELEMENT_FORMATE[el.format]
    if fmt.normiert:
        werte = np.clip(np.round(werte * 255.0), 0, 255)
    roh = np.ascontiguousarray(werte.astype(fmt.dtype))
    return roh.view(np.uint8).reshape(len(werte), fmt.groesse)


@dataclass
class NeueGeometrie:
    positionen: np.ndarray  # (N, 3) im lokalen Raum des Mesh-Knotens
    dreiecke: np.ndarray  # (M, 3)
    normalen: Optional[np.ndarray] = None
    uvs: Optional[np.ndarray] = None

    def vervollstaendigen(self) -> "NeueGeometrie":
        pos = np.asarray(self.positionen, np.float64)
        tri = np.asarray(self.dreiecke, np.int64)
        if tri.min() < 0 or tri.max() >= len(pos):
            raise ValueError("Dreiecksindizes außerhalb der Vertexliste")
        nrm = normalen_berechnen(pos, tri) if self.normalen is None else np.asarray(self.normalen, np.float64)
        uv = np.zeros((len(pos), 2)) if self.uvs is None else np.asarray(self.uvs, np.float64)
        return NeueGeometrie(pos, tri, nrm, uv)


def quader(breite: float, hoehe: float, tiefe: float, mitte=(0.0, 0.0, 0.0), uv_meter: float = 1.0) -> NeueGeometrie:
    """Quader mit 24 Vertices (harte Kanten) und UVs in Metern / uv_meter. y = oben."""
    bx, by, bz = breite / 2.0, hoehe / 2.0, tiefe / 2.0
    seiten = [  # Normale, Achse u, Achse v
        ((1, 0, 0), (0, 0, -1), (0, 1, 0)), ((-1, 0, 0), (0, 0, 1), (0, 1, 0)),
        ((0, 1, 0), (1, 0, 0), (0, 0, -1)), ((0, -1, 0), (1, 0, 0), (0, 0, 1)),
        ((0, 0, 1), (1, 0, 0), (0, 1, 0)), ((0, 0, -1), (-1, 0, 0), (0, 1, 0)),
    ]
    halb = np.array([bx, by, bz])
    pos, nrm, uv, tri = [], [], [], []
    for n, u, v in seiten:
        n, u, v = np.array(n, float), np.array(u, float), np.array(v, float)
        b = len(pos)
        for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            p = n * halb + su * u * halb + sv * v * halb
            pos.append(p + np.asarray(mitte))
            nrm.append(n)
            uv.append(((su + 1) / 2 * abs(u @ halb) * 2 / uv_meter, (1 - (sv + 1) / 2) * abs(v @ halb) * 2 / uv_meter))
        tri += [(b, b + 1, b + 2), (b, b + 2, b + 3)]
    return NeueGeometrie(np.array(pos), np.array(tri), np.array(nrm), np.array(uv))


def platte(laenge: float, breite: float, segmente: int = 10, mitte=(0.0, 0.0, 0.0), uv_meter: float = 4.0) -> NeueGeometrie:
    """Flache Fahrbahnplatte in der x-z-Ebene (Länge entlang z), Normale +y."""
    zs = np.linspace(-laenge / 2, laenge / 2, segmente + 1)
    pos, uv, tri = [], [], []
    for i, z in enumerate(zs):
        for s in (-1, 1):
            pos.append((s * breite / 2, 0.0, z))
            uv.append(((s + 1) / 2 * breite / uv_meter, (z + laenge / 2) / uv_meter))
    for i in range(segmente):
        a = 2 * i
        tri += [(a, a + 2, a + 1), (a + 1, a + 2, a + 3)]
    pos = np.array(pos) + np.asarray(mitte)
    return NeueGeometrie(pos, np.array(tri), np.tile([0.0, 1.0, 0.0], (len(pos), 1)), np.array(uv))


def _puffer_setzen(res: erp.ErpResource, puffer: GfxPuffer) -> None:
    res.fragments[0].set_data(puffer.kopf_bytes(), compress=False)
    res.fragments[1].set_data(puffer.daten)
    res.update_hash()


def geometrie_ersetzen(archiv: erp.ErpFile, kennung: str, geo: NeueGeometrie,
                       quelle: Optional[RessourcenIndex] = None) -> Dict[str, object]:
    """Ersetzt die Geometrie eines vorhandenen Modells im Archiv (alle LODs zeigen danach auf die
    neue Geometrie). Voraussetzung: alle Teilnetze des Modells nutzen dieselben Vertexpuffer.
    Ergebnis: ein Teilnetz mit dem Material des ersten Teilnetzes, im selben Vertexformat;
    Bounding-Kugeln, AABB und Zähler werden neu berechnet. Gibt einen Bericht zurück."""
    idx = quelle or _als_index(archiv)
    if not kennung.endswith("?model"):
        kennung = modell_kennung(basis_kennung(kennung))
    m_res = archiv.find(kennung)
    if m_res is None:
        raise KeyError(kennung)
    modell = GfxModell.aus_bytes(m_res.data())
    if modell.mit_skinning or modell.rest is not None:
        raise GrafikFormatFehler("Skinning-Modelle werden beim Ersetzen nicht unterstützt")
    geo = geo.vervollstaendigen()
    if len(geo.positionen) > 0xFFFF:
        raise ValueError("Mehr als 65535 Vertices brauchen mehrere Teilnetze (noch nicht unterstützt)")
    mesh_res = [archiv.find(k) for k in modell.mesh_kennungen]
    if any(r is None for r in mesh_res):
        raise KeyError("Mesh nicht im Archiv")
    meshes = [GfxMesh.aus_bytes(r.data()) for r in mesh_res]
    m0 = meshes[0]
    stroeme = m0.teilnetze[0].stroeme[:m0.teilnetze[0].strom_anzahl]
    for mesh in meshes:
        for t in mesh.teilnetze:
            if t.stroeme[:t.strom_anzahl] != stroeme or mesh.vb_kennungen != m0.vb_kennungen:
                raise GrafikFormatFehler("Teilnetze nutzen unterschiedliche Vertexpuffer (z. B. %s)" % mesh.lod)
    tri = geo.dreiecke.astype(np.int64)
    pos, nrm, uv = geo.positionen, geo.normalen, geo.uvs
    tan = _tangenten(pos, nrm, uv, tri)
    bericht = {"vertices": len(pos), "dreiecke": len(tri), "puffer": []}
    # Vertexpuffer neu aufbauen
    for vb in stroeme:
        res = archiv.find(m0.vb_kennungen[vb])
        alt = GfxPuffer.aus_ressource(res)
        zeilen = np.zeros((len(pos), alt.stride), np.uint8)
        for el in m0.elemente_fuer_puffer(vb):
            vorlage = element_lesen(alt, el, np.arange(min(1, alt.anzahl))) if alt.anzahl else None
            werte = _element_werte(el, pos, nrm, tan, uv, vorlage)
            zeilen[:, el.offset:el.offset + ELEMENT_FORMATE[el.format].groesse] = _element_bytes(el, werte)
        neu = GfxPuffer(alt.art, alt.stride, len(pos), alt.code, zeilen.tobytes())
        _puffer_setzen(res, neu)
        bericht["puffer"].append((res.identifier, alt.anzahl, neu.anzahl))
    # Indexpuffer
    ib_res = archiv.find(m0.ib_kennungen[0])
    ib_alt = GfxPuffer.aus_ressource(ib_res)
    if ib_alt.stride != 2:
        raise GrafikFormatFehler("Nur 16-Bit-Indexpuffer unterstützt")
    ib_neu = GfxPuffer(ib_alt.art, 2, tri.size, ib_alt.code, tri.astype("<u2").tobytes())
    _puffer_setzen(ib_res, ib_neu)
    bericht["puffer"].append((ib_res.identifier, ib_alt.anzahl, ib_neu.anzahl))
    # Meshes: ein Teilnetz, eine Zeichnung je Knoten (nur erster Knoten behält sie)
    kugel = kugel_aus_punkten(pos)
    for mesh, res in zip(meshes, mesh_res):
        t0 = mesh.teilnetze[0]
        mat_name = mesh.materialnamen[t0.material]
        mesh.materialnamen = [mat_name]
        mesh.eintraege = [(1, 0)]
        mesh.teilnetze = [Teilnetz(t0.strom_anzahl, TOPOLOGIE_DREIECKSLISTE, 2, 0, int(tri.size), 0, kugel,
                                   0, 1, 0, t0.reserviert, list(t0.stroeme))]
        if mesh.knoten:
            k0 = mesh.knoten[0]
            mesh.knoten = [MeshKnoten(k0.matrix, k0.feld0, k0.modell_knoten, k0.feld2, 0, 1, k0.feld5, k0.feld6)]
            mesh.zeichnungen = [Zeichnung(1, 0, 0, int(tri.size))]
        else:
            mesh.zeichnungen = []
        fehler = mesh.pruefen()
        if fehler:
            raise GrafikFormatFehler("Neues Mesh fehlerhaft: %s" % fehler)
        res.set_fragment_data(0, mesh.to_bytes())
    # Modell: AABB und Kugel im Modellraum
    welt = modell.weltmatrizen()
    mk = meshes[0].knoten[0].modell_knoten if meshes[0].knoten else 0
    w = welt[mk] if mk < len(welt) else np.eye(4)
    pw = pos @ w[:3, :3].T + w[:3, 3]
    modell.aabb_min = tuple(float(x) for x in pw.min(0))
    modell.aabb_max = tuple(float(x) for x in pw.max(0))
    modell.kugel = kugel_aus_punkten(pw)
    m_res.set_fragment_data(0, modell.to_bytes())
    bericht.update({"modell": kennung, "lods": len(meshes), "material_slot": meshes[0].materialnamen[0],
                    "aabb": (modell.aabb_min, modell.aabb_max), "kugel": modell.kugel})
    return bericht


def modell_kopieren(archiv: erp.ErpFile, vorlage: str, neue_basis: str, geo: Optional[NeueGeometrie] = None,
                    quelle: Optional[RessourcenIndex] = None) -> List[str]:
    """Legt ein neues Modell mit neuer Kennung als Kopie einer Vorlage an (Modell, Meshes,
    Puffer, Proxy) und ersetzt optional die Geometrie. Verweise in Modell/Mesh werden auf die
    neuen Kennungen umgeschrieben. Gibt die angelegten Kennungen zurück. Damit das Objekt im
    Spiel erscheint, braucht es zusätzlich eine Instanz in einem Layer (WOInstances, AP2) und
    ggf. eine Materialbindung (.emb)."""
    idx = quelle or _als_index(archiv)
    alt_basis = basis_kennung(vorlage)
    m_alt = idx[modell_kennung(alt_basis)]
    modell = GfxModell.aus_bytes(m_alt.data())

    def umbenennen(k: str) -> str:
        if not k.startswith(alt_basis + "?"):
            raise GrafikFormatFehler("Kennung %s gehört nicht zur Vorlage" % k)
        return neue_basis + k[len(alt_basis):]

    neu: List[str] = []
    puffer_kennungen: List[str] = []
    for mk in modell.mesh_kennungen:
        mesh = GfxMesh.aus_bytes(idx[mk].data())
        for k in mesh.vb_kennungen + mesh.ib_kennungen:
            if k not in puffer_kennungen:
                puffer_kennungen.append(k)
        mesh.vb_kennungen = [umbenennen(k) for k in mesh.vb_kennungen]
        mesh.ib_kennungen = [umbenennen(k) for k in mesh.ib_kennungen]
        archiv.add_resource(umbenennen(mk), "GfxMeshRes", [(b"temp", mesh.to_bytes())],
                            unknown=TYP_UNBEKANNT["GfxMeshRes"])
        neu.append(umbenennen(mk))
    for k in puffer_kennungen:
        r = idx[k]
        res = archiv.add_resource(umbenennen(k), "GfxBufferRes", [(b"temp", r.fragments[0].read()),
                                                                   (b"temp", r.fragments[1].read())],
                                  unknown=TYP_UNBEKANNT["GfxBufferRes"])
        res.fragments[0].set_data(r.fragments[0].read(), compress=False)
        res.fragments[1].flags = r.fragments[1].flags  # Flag 4 wie im Original
        res.update_hash()
        neu.append(umbenennen(k))
    modell.mesh_kennungen = [umbenennen(k) for k in modell.mesh_kennungen]
    archiv.add_resource(modell_kennung(neue_basis), "GfxModelRes", [(b"temp", modell.to_bytes())],
                        unknown=TYP_UNBEKANNT["GfxModelRes"])
    neu.append(modell_kennung(neue_basis))
    p_alt = idx.get(proxy_kennung(alt_basis))
    proxy = ModellProxy.aus_bytes(p_alt.data()) if p_alt is not None else ModellProxy.aus_modell(modell)
    archiv.add_resource(proxy_kennung(neue_basis), "ModelProxyRes", [(b"temp", proxy.to_bytes())],
                        unknown=TYP_UNBEKANNT["ModelProxyRes"])
    neu.append(proxy_kennung(neue_basis))
    if geo is not None:
        geometrie_ersetzen(archiv, modell_kennung(neue_basis), geo)
    return neu
