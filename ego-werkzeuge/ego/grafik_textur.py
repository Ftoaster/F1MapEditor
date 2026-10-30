"""Texturen (GfxSurfaceRes) als DDS exportieren und als Bild dekodieren.

- DDS mit DX10-Kopf (DXGI-Format aus grafik.TEXTUR_FORMATE), optional mit den großen
  Mip-Stufen aus der externen .mipmaps-Datei (LZ4-Blöcke, Codec 3).
- Dekodieren über Pillow (liest DDS mit BC1–BC7/BC6H ab Pillow 9/10), dazu ein eigener
  BC1-Dekoder in numpy als unabhängige Gegenprüfung.
"""

import glob
import os
import struct
from typing import List, Optional, Tuple

import numpy as np

from . import grafik

DDS_MAGIC = b"DDS "
DDSD_CAPS, DDSD_HEIGHT, DDSD_WIDTH, DDSD_PIXELFORMAT, DDSD_MIPMAPCOUNT, DDSD_LINEARSIZE = (
    0x1, 0x2, 0x4, 0x1000, 0x20000, 0x80000)
DDSCAPS_COMPLEX, DDSCAPS_TEXTURE, DDSCAPS_MIPMAP = 0x8, 0x1000, 0x400000
DDSCAPS2_CUBEMAP_ALL = 0xFE00
DDPF_FOURCC = 0x4
D3D10_RESOURCE_DIMENSION_TEXTURE2D = 3
D3D11_RESOURCE_MISC_TEXTURECUBE = 0x4


class TexturFehler(ValueError):
    pass


# --------------------------------------------------------------------------- LZ4

def lz4_block_entpacken(src: bytes, groesse: Optional[int] = None) -> bytes:
    """LZ4-Blockformat (ohne Frame-Kopf), wie in den .mipmaps-Dateien (Codec 3)."""
    dst = bytearray()
    i, n = 0, len(src)
    while i < n:
        tok = src[i]
        i += 1
        lit = tok >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        dst += src[i:i + lit]
        i += lit
        if i >= n:
            break
        off = src[i] | (src[i + 1] << 8)
        i += 2
        if off == 0:
            raise TexturFehler("LZ4: Offset 0")
        ml = tok & 15
        if ml == 15:
            while True:
                b = src[i]
                i += 1
                ml += b
                if b != 255:
                    break
        ml += 4
        start = len(dst) - off
        if start < 0:
            raise TexturFehler("LZ4: Verweis vor Datenbeginn")
        if off >= ml:
            dst += dst[start:start + ml]
        else:  # überlappende Kopie: Muster wiederholen
            muster = bytes(dst[start:])
            dst += (muster * (ml // off + 1))[:ml]
    if groesse is not None and len(dst) != groesse:
        raise TexturFehler("LZ4: %d statt %d Byte entpackt" % (len(dst), groesse))
    return bytes(dst)


# --------------------------------------------------------------------------- externe Mips

def extern_datei_finden(textur: grafik.Textur, suchordner: List[str]) -> Optional[str]:
    """Sucht die .mipmaps-Datei: <ordner>/<extern_pfad> oder (Mods) <ordner>/**/<hash>_<name>."""
    if not textur.extern_pfad:
        return None
    name = textur.extern_pfad.rsplit("/", 1)[-1]
    for ordner in suchordner:
        p = os.path.join(ordner, *textur.extern_pfad.split("/"))
        if os.path.isfile(p):
            return p
    for ordner in suchordner:
        treffer = glob.glob(os.path.join(ordner, "**", "*_" + name), recursive=True)
        if treffer:
            return treffer[0]
    return None


def extern_stufen(textur: grafik.Textur, pfad: str) -> List[bytes]:
    """Entpackte externe Mip-Stufen (größte zuerst)."""
    stufen = []
    with open(pfad, "rb") as f:
        for em in textur.extern_mips:
            f.seek(em.offset)
            roh = f.read(em.gepackt)
            if em.codec == 3:
                stufen.append(lz4_block_entpacken(roh, em.entpackt))
            elif em.gepackt == em.entpackt:
                stufen.append(roh)
            else:
                raise TexturFehler("Unbekannter Codec %d" % em.codec)
    return stufen


# --------------------------------------------------------------------------- DDS

def dds_bytes(breite: int, hoehe: int, dxgi: int, mips: int, daten: bytes, schichten: int = 1,
              wuerfel: bool = False, block: bool = True, bytes_je_block: int = 16) -> bytes:
    flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT | DDSD_MIPMAPCOUNT
    if block:
        flags |= DDSD_LINEARSIZE
        pitch = max(1, (breite + 3) // 4) * max(1, (hoehe + 3) // 4) * bytes_je_block
    else:
        pitch = breite * bytes_je_block
    caps = DDSCAPS_TEXTURE | (DDSCAPS_MIPMAP | DDSCAPS_COMPLEX if mips > 1 else 0)
    caps2 = DDSCAPS2_CUBEMAP_ALL if wuerfel else 0
    if wuerfel:
        caps |= DDSCAPS_COMPLEX
    pf = struct.pack("<II4sIIIII", 32, DDPF_FOURCC, b"DX10", 0, 0, 0, 0, 0)
    kopf = struct.pack("<IIIIIII", 124, flags, hoehe, breite, pitch, 1, mips) + b"\0" * 44 + pf + struct.pack(
        "<IIIII", caps, caps2, 0, 0, 0)
    dx10 = struct.pack("<IIIII", dxgi, D3D10_RESOURCE_DIMENSION_TEXTURE2D,
                       D3D11_RESOURCE_MISC_TEXTURECUBE if wuerfel else 0,
                       (schichten // 6) if wuerfel else schichten, 0)
    return DDS_MAGIC + kopf + dx10 + daten


# Pillow dekodiert die sRGB-Varianten von BC1/BC3 nicht; die Bits sind identisch.
DEKODIER_ERSATZ = {72: 71, 78: 77}


def textur_dds(textur: grafik.Textur, extern: Optional[List[bytes]] = None, dxgi_ersatz: Optional[dict] = None) -> bytes:
    """DDS einer Textur. Mit externen Stufen (größte zuerst) wird die volle Auflösung erzeugt.
    Nur für Texturen mit einer Schicht werden externe Stufen vorangestellt."""
    info = textur.format_info
    if info is None:
        raise TexturFehler("Unbekannter Formatcode %d" % textur.format)
    dxgi, bpb, block, _, _ = info
    if dxgi_ersatz:
        dxgi = dxgi_ersatz.get(dxgi, dxgi)
    wuerfel = textur.dimension == 4
    if extern and textur.schichten == 1 and not wuerfel:
        faktor = 1 << len(extern)
        return dds_bytes(textur.breite * faktor, textur.hoehe * faktor, dxgi, textur.mips + len(extern),
                         b"".join(extern) + textur.daten, 1, False, block, bpb)
    return dds_bytes(textur.breite, textur.hoehe, dxgi, textur.mips, textur.daten, textur.schichten, wuerfel,
                     block, bpb)


def textur_bild(textur: grafik.Textur, extern: Optional[List[bytes]] = None):
    """Höchste verfügbare Stufe als Pillow-Bild (über DDS)."""
    import io
    from PIL import Image
    return Image.open(io.BytesIO(textur_dds(textur, extern, DEKODIER_ERSATZ))).copy()


# --------------------------------------------------------------------------- eigener BC1-Dekoder

def _rgb565(c: np.ndarray) -> np.ndarray:
    r = ((c >> 11) & 31).astype(np.float32) * (255 / 31)
    g = ((c >> 5) & 63).astype(np.float32) * (255 / 63)
    b = (c & 31).astype(np.float32) * (255 / 31)
    return np.stack([r, g, b], -1)


def bc1_dekodieren(daten: bytes, breite: int, hoehe: int) -> np.ndarray:
    """BC1 (DXT1) -> RGBA uint8 (hoehe, breite, 4). Unabhängig von Pillow, zur Gegenprüfung."""
    bw, bh = max(1, (breite + 3) // 4), max(1, (hoehe + 3) // 4)
    bl = np.frombuffer(daten[:bw * bh * 8], np.uint8).reshape(bh * bw, 8)
    c0 = bl[:, 0].astype(np.uint32) | (bl[:, 1].astype(np.uint32) << 8)
    c1 = bl[:, 2].astype(np.uint32) | (bl[:, 3].astype(np.uint32) << 8)
    idx = bl[:, 4].astype(np.uint32) | (bl[:, 5].astype(np.uint32) << 8) | (bl[:, 6].astype(np.uint32) << 16) | (
        bl[:, 7].astype(np.uint32) << 24)
    f0, f1 = _rgb565(c0), _rgb565(c1)
    vier = (c0 > c1)[:, None]
    pal = np.zeros((len(bl), 4, 4), np.float32)
    pal[:, 0, :3], pal[:, 1, :3] = f0, f1
    pal[:, 2, :3] = np.where(vier, (2 * f0 + f1) / 3, (f0 + f1) / 2)
    pal[:, 3, :3] = np.where(vier, (f0 + 2 * f1) / 3, 0)
    pal[:, :, 3] = 255
    pal[:, 3, 3] = np.where(vier[:, 0], 255, 0)
    pix = (idx[:, None] >> (2 * np.arange(16, dtype=np.uint32))[None, :]) & 3
    farben = np.take_along_axis(pal, pix[:, :, None].astype(np.int64).repeat(4, 2), 1)  # (B, 16, 4)
    bild = farben.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    return np.clip(np.round(bild[:hoehe, :breite]), 0, 255).astype(np.uint8)
