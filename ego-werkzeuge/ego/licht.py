"""Licht- und Wetterdaten einer Strecke (AP10): lesen, bitgenau schreiben, Platzhalter erzeugen.

Ressourcen (Bahrain, Portimão-Slot):

- ``LightMapLocRes`` (``ego_package/lightmap_locations[_radiance]``): Zuordnung Instanz ->
  Rechteck im Licht-Atlas. Aufbau gesichert (siehe ``docs/formate/licht.md``).
- ``GfxSurfaceRes`` ``ego_package/baked_maps_ao`` (BC4, 4096², 4 Schichten) und
  ``baked_maps_radiance`` (BC6H, 4096², 4 Schichten): Atlanten. Lesen über ``grafik.Textur``.
- ``TrackInfoTex``: eine vollständige **TGA-Datei** (unkomprimiert, 32 bit, 16384 × 64).
- ``DDGIRes``, ``IBLConfig``: XML-Text.
- ``IBLSH`` (``SHPR``-Kopf), ``BakedLightList`` (``LIST``/``TRIS``-Blöcke),
  ``TrackInfoBinary`` (Big-Endian): nur Kopf gelesen, Inhalt als Rohdaten (bitgenau).
"""

import io
import struct
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from . import erp, grafik

AO_KENNUNG = "eaid://ego_package/baked_maps_ao"
RADIANCE_KENNUNG = "eaid://ego_package/baked_maps_radiance"
LML_KENNUNG = "eaid://ego_package/lightmap_locations"
LML_RADIANCE_KENNUNG = "eaid://ego_package/lightmap_locations_radiance"
LICHTLISTE_KENNUNG = "eaid://ego_package/baked_light_list"
DDGI_KENNUNG = "eaid://ego_package/ddgi_resource"

LICHT_TYPEN = ("LightMapLocRes", "BakedLightList", "DDGIRes", "IBLSH", "IBLConfig",
               "TrackInfoBinary", "TrackInfoTex")


class LichtFormatFehler(ValueError):
    pass


# --------------------------------------------------------------------------- LightMapLocRes

LML_EINTRAG = struct.Struct("<16sIfff")  # 32 B


@dataclass
class LichtkartenEintrag:
    """Ein Eintrag: Instanz-``uniqueid`` (16 B, wie in den .layer-Dateien) -> Rechteck.

    ``groesse`` ist die Kantenlänge als Anteil der Atlaskante (× 4096 = ``Render.lightmap_size``),
    ``u``/``v`` die linke obere Ecke (0…1), ``schicht`` die Atlasschicht (0 = Terrain)."""

    uid: bytes
    schicht: int
    groesse: float
    u: float
    v: float

    def pixel(self, kante: int = 4096) -> Tuple[int, int, int, int]:
        """(x0, y0, x1, y1) in Pixeln der Schicht."""
        x0, y0 = int(round(self.u * kante)), int(round(self.v * kante))
        g = int(round(self.groesse * kante))
        return x0, y0, x0 + g, y0 + g


@dataclass
class Lichtkarte:
    """``LightMapLocRes``: u32 Anzahl + Anzahl × 32 B. Bitgenauer Rundlauf über ``to_bytes``."""

    eintraege: List[LichtkartenEintrag] = field(default_factory=list)

    @classmethod
    def aus_bytes(cls, d: bytes) -> "Lichtkarte":
        if len(d) < 4:
            raise LichtFormatFehler("LightMapLocRes zu kurz")
        (n,) = struct.unpack_from("<I", d, 0)
        if len(d) != 4 + n * LML_EINTRAG.size:
            raise LichtFormatFehler("LightMapLocRes: %d Byte, erwartet %d" % (len(d), 4 + n * 32))
        return cls([LichtkartenEintrag(*LML_EINTRAG.unpack_from(d, 4 + i * 32)) for i in range(n)])

    def to_bytes(self) -> bytes:
        teile = [struct.pack("<I", len(self.eintraege))]
        for e in self.eintraege:
            if len(e.uid) != 16:
                raise LichtFormatFehler("uid muss 16 Byte haben")
            teile.append(LML_EINTRAG.pack(e.uid, e.schicht, e.groesse, e.u, e.v))
        return b"".join(teile)

    def nach_uid(self) -> Dict[bytes, LichtkartenEintrag]:
        return {e.uid: e for e in self.eintraege}

    def hinzufuegen(self, uid: bytes, schicht: int, groesse: float, u: float, v: float) -> LichtkartenEintrag:
        if any(e.uid == uid for e in self.eintraege):
            raise LichtFormatFehler("uid %s schon vorhanden" % uid.hex())
        e = LichtkartenEintrag(bytes(uid), schicht, groesse, u, v)
        self.eintraege.append(e)
        return e

    def statistik(self) -> dict:
        from collections import Counter
        return {
            "eintraege": len(self.eintraege),
            "schichten": dict(sorted(Counter(e.schicht for e in self.eintraege).items())),
            "groessen_px": dict(sorted(Counter(int(round(e.groesse * 4096)) for e in self.eintraege).items())),
            "geteilte_rechtecke": sum(1 for _, n in Counter((e.schicht, e.groesse, e.u, e.v)
                                                            for e in self.eintraege).items() if n > 1),
        }


def terrain_uv(x: float, z: float, mitte_u: float = 0.5, mitte_v: float = 0.5, kante_m: float = 2048.0
               ) -> Tuple[float, float]:
    """Welt -> Atlasschicht 0 (Terrain). Bahrain: u = x/2048 + 0,5, v = z/2048 + 0,5 (gesichert durch
    Überlagerung). Portimão hat andere Mitte (Terrain-Strom 4.zw, siehe grafik.md)."""
    return x / kante_m + mitte_u, z / kante_m + mitte_v


# --------------------------------------------------------------------------- BC4-Atlas (AO)

def bc4_konstant(wert: int) -> bytes:
    """Ein BC4-Block mit überall demselben Wert (r0 = wert, Index 0 für alle 16 Pixel)."""
    if not 0 <= wert <= 255:
        raise ValueError("wert 0…255")
    return bytes([wert, wert]) + b"\0" * 6  # r0 = r1 -> Index 0 liefert r0


def atlas_konstant(textur: grafik.Textur, wert: int) -> bytes:
    """Komplette Mip-Kette aller Schichten einer BC4-Textur, überall ``wert``."""
    if textur.format != 63:
        raise LichtFormatFehler("Nur BC4 (Formatcode 63) wird konstant erzeugt, nicht %d" % textur.format)
    block = bc4_konstant(wert)
    bloecke = textur.erwartete_groesse() // 8
    return block * bloecke


def schicht_daten(textur: grafik.Textur, schicht: int, mip: int = 0) -> bytes:
    """Rohdaten einer Mip-Stufe einer Schicht (Schichten liegen nacheinander mit voller Kette)."""
    stufen = textur.stufen_groessen()
    je_schicht = sum(g for _, _, g in stufen)
    o = schicht * je_schicht + sum(g for _, _, g in stufen[:mip])
    return textur.daten[o:o + stufen[mip][2]]


def schicht_bild(textur: grafik.Textur, schicht: int, mip: int = 0):
    """Eine Schicht als Pillow-Bild (BC4 -> L, BC6H -> RGB, 8 bit von Pillow abgebildet)."""
    from PIL import Image
    from . import grafik_textur as gt
    w, h, _ = textur.stufen_groessen()[mip]
    dxgi, bpb, block, _, _ = textur.format_info
    dds = gt.dds_bytes(w, h, dxgi, 1, schicht_daten(textur, schicht, mip), 1, False, block, bpb)
    bild = Image.open(io.BytesIO(dds))
    bild.load()
    return bild


def weisse_rechtecke(textur: grafik.Textur, schicht: int, kante_px: int, belegt: Iterable[LichtkartenEintrag],
                     anzahl: int = 1, schwelle: int = 255) -> List[Tuple[float, float]]:
    """Sucht freie, vollständig weiße (AO = 1) Rechtecke einer Schicht, ausgerichtet an ``kante_px``.
    Liefert (u, v)-Ecken. Für neue Objekte in einer sonst unveränderten AO-Karte."""
    a = np.asarray(schicht_bild(textur, schicht))
    if a.ndim == 3:
        a = a.min(axis=2)
    n = textur.breite // kante_px
    kacheln = a.reshape(n, kante_px, n, kante_px).min(axis=(1, 3)) >= schwelle
    for e in belegt:
        if e.schicht != schicht:
            continue
        x0, y0, x1, y1 = e.pixel(textur.breite)
        kacheln[y0 // kante_px:max(y0 // kante_px + 1, -(-y1 // kante_px)),
                x0 // kante_px:max(x0 // kante_px + 1, -(-x1 // kante_px))] = False
    ys, xs = np.nonzero(kacheln)
    return [(float(x * kante_px) / textur.breite, float(y * kante_px) / textur.hoehe)
            for y, x in list(zip(ys, xs))[:anzahl]]


# --------------------------------------------------------------------------- TrackInfoTex (TGA)

TGA_KOPF = struct.Struct("<BBBHHBHHHHBB")  # 18 B
TGA_FUSS = b"TRUEVISION-XFILE.\0"


@dataclass
class TrackInfoTex:
    """``TrackInfoTex`` = TGA (Typ 2, unkomprimiert, 32 bit BGRA, Ursprung unten links laut
    Deskriptor 8). Breite 16384 = Länge entlang der Strecke, Höhe 64 = quer (Vermutung aus dem Bild:
    die Ideallinie verläuft als helle Linie längs). Kopf/Pixel/Fuß bitgenau getrennt gehalten."""

    kopf: tuple
    pixel: bytes
    rest: bytes  # TGA-Fuß (26 B) bzw. alles nach den Pixeln

    @classmethod
    def aus_bytes(cls, d: bytes) -> "TrackInfoTex":
        k = TGA_KOPF.unpack_from(d, 0)
        idlen, cmtyp, typ, _, _, _, _, _, breite, hoehe, bpp, _ = k
        if typ != 2 or cmtyp != 0 or bpp not in (24, 32):
            raise LichtFormatFehler("TrackInfoTex: kein unkomprimiertes TGA (Typ %d, %d bit)" % (typ, bpp))
        start = 18 + idlen
        ende = start + breite * hoehe * bpp // 8
        if ende > len(d):
            raise LichtFormatFehler("TrackInfoTex zu kurz")
        return cls(k, d[18:ende], d[ende:])

    def to_bytes(self) -> bytes:
        return TGA_KOPF.pack(*self.kopf) + self.pixel + self.rest

    @property
    def breite(self) -> int:
        return self.kopf[8]

    @property
    def hoehe(self) -> int:
        return self.kopf[9]

    @property
    def bpp(self) -> int:
        return self.kopf[10]

    def bgra(self) -> np.ndarray:
        """(hoehe, breite, 4) in Dateireihenfolge (Zeile 0 = erste gespeicherte Zeile)."""
        idlen = self.kopf[0]
        return np.frombuffer(self.pixel[idlen:], np.uint8).reshape(self.hoehe, self.breite, self.bpp // 8)

    def mit_bgra(self, a: np.ndarray) -> "TrackInfoTex":
        a = np.ascontiguousarray(a, np.uint8)
        if a.shape != (self.hoehe, self.breite, self.bpp // 8):
            raise LichtFormatFehler("Falsche Bildgröße %r" % (a.shape,))
        idlen = self.kopf[0]
        return TrackInfoTex(self.kopf, self.pixel[:idlen] + a.tobytes(), self.rest)

    def gleichmaessig(self, bgra: Sequence[int]) -> "TrackInfoTex":
        a = np.empty((self.hoehe, self.breite, self.bpp // 8), np.uint8)
        a[...] = np.array(bgra[:self.bpp // 8], np.uint8)
        return self.mit_bgra(a)


# --------------------------------------------------------------------------- weitere Typen (grob)

def xml_text(d: bytes) -> str:
    """DDGIRes und IBLConfig sind UTF-8-XML (Rundlauf: Bytes unverändert)."""
    return d.decode("utf-8")


def ibl_sh_kopf(d: bytes) -> dict:
    """``IBLSH``: 'SHPR', u32 Version (6), u32 0, u32 2, 'GRID', danach Gitter-Floats
    (erste drei = Ursprung in Weltkoordinaten, Vermutung). Rest roh."""
    if d[:4] != b"SHPR":
        raise LichtFormatFehler("IBLSH ohne SHPR")
    magic, ver, a, b, gitter = struct.unpack_from("<4sIII4s", d, 0)
    return {"version": ver, "feld2": a, "feld3": b, "block": gitter.decode("latin-1"),
            "floats": [round(x, 4) for x in struct.unpack_from("<12f", d, 20)], "groesse": len(d)}


def lichtliste_kopf(d: bytes) -> dict:
    """``BakedLightList``: 'LIST' + u32 (4), 'TRIS' + u32 Größe + u32 Dreieckszahl, danach
    Dreiecke (3 × vec3 Position, Normale …). Der Bereich nach TRIS ist offen."""
    if d[:4] != b"LIST":
        raise LichtFormatFehler("BakedLightList ohne LIST")
    (ver,) = struct.unpack_from("<I", d, 4)
    tris, groesse, anzahl = struct.unpack_from("<4sII", d, 8)
    return {"version": ver, "block": tris.decode("latin-1"), "blockgroesse": groesse, "dreiecke": anzahl,
            "erstes_dreieck": [round(x, 3) for x in struct.unpack_from("<12f", d, 20)], "groesse": len(d)}


def trackinfo_binaer_kopf(d: bytes) -> dict:
    """``TrackInfoBinary``: beginnt mit Big-Endian-u32 (Bahrain: 3, 0, 63, 8, dann Paare). Offen."""
    return {"groesse": len(d), "be_u32": list(struct.unpack_from(">12I", d, 0))}


# --------------------------------------------------------------------------- Rundlauf / Platzhalter

def rundlauf(res: erp.ErpResource) -> Optional[bool]:
    """Bitgenauer Rundlauf der lesbaren Licht-Ressourcen; None, wenn nur roh behandelt."""
    t = res.resource_type
    if t == "LightMapLocRes":
        d = res.data()
        return Lichtkarte.aus_bytes(d).to_bytes() == d
    if t == "TrackInfoTex":
        d = res.data()
        return TrackInfoTex.aus_bytes(d).to_bytes() == d
    if t in ("DDGIRes", "IBLConfig"):
        d = res.data()
        return xml_text(d).encode("utf-8") == d
    if t == "GfxSurfaceRes" and res.identifier in (AO_KENNUNG, RADIANCE_KENNUNG):
        tx = grafik.Textur.aus_ressource(res)
        return len(tx.daten) == tx.erwartete_groesse() and not tx.pruefen()
    return None


def ao_ersetzen(archiv: erp.ErpFile, wert: int = 255, kennung: str = AO_KENNUNG) -> grafik.Textur:
    """Setzt den AO-Atlas überall auf ``wert`` (255 = keine Verdeckung). Kopf bleibt gleich."""
    res = archiv.find(kennung)
    if res is None:
        raise LichtFormatFehler("%s fehlt" % kennung)
    tx = grafik.Textur.aus_ressource(res, mit_daten=False)
    res.set_fragment_data(1, atlas_konstant(tx, wert))
    return tx


def neue_lichtkarte(terrain_uid: Optional[bytes], objekt_uids: Iterable[bytes], schicht: int = 1,
                    groesse_px: int = 32, u: float = 0.0, v: float = 0.0) -> Lichtkarte:
    """Lichtkarte für eine neue Strecke mit neutralem (weißem) AO-Atlas: Terrain auf ganze Schicht 0,
    alle Objekte teilen sich ein Rechteck (geteilte Rechtecke kommen im Original vor)."""
    k = Lichtkarte()
    if terrain_uid is not None:
        k.hinzufuegen(terrain_uid, 0, 1.0, 0.0, 0.0)
    for uid in objekt_uids:
        k.hinzufuegen(uid, schicht, groesse_px / 4096.0, u, v)
    return k


def bestand(archiv: erp.ErpFile) -> List[dict]:
    """Licht-/Wetter-Ressourcen eines Archivs (Typ, Kennung, Größe)."""
    out = []
    for r in archiv.resources:
        if r.resource_type in LICHT_TYPEN or r.identifier in (AO_KENNUNG, RADIANCE_KENNUNG):
            out.append({"typ": r.resource_type, "kennung": r.identifier,
                        "groesse": sum(f.size for f in r.fragments)})
    return out
