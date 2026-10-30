"""Interne 2D-Texturen schreiben: RGBA8, BC1, BC3 und BC7 (Modus 6).

Die BC-Kodierer arbeiten blockweise in numpy. BC7 verwendet eine Teilmenge mit
RGBA-Endpunkten und 4-Bit-Indizes; keine externe Bibliothek/Datei ist nötig.
Unbekannte Formate, Arrays und Würfel werden ausdrücklich abgewiesen.
"""

import copy
import math
from typing import Optional

import numpy as np
from PIL import Image

from . import erp, grafik


def _rgba(bild) -> np.ndarray:
    a = np.asarray(bild)
    if a.dtype != np.uint8 or a.ndim != 3 or a.shape[2] != 4 or min(a.shape[:2]) < 1:
        raise ValueError("RGBA-Bild als uint8[Höhe, Breite, 4] erwartet")
    return np.ascontiguousarray(a)


def _bloecke(a: np.ndarray) -> np.ndarray:
    h, w, _ = a.shape
    a = np.pad(a, ((0, (-h) % 4), (0, (-w) % 4), (0, 0)), mode="edge")
    return a.reshape((h + 3) // 4, 4, (w + 3) // 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(-1, 16, 4)


def _endpunkte(p: np.ndarray):
    """Hauptachse statt kanalweiser Min/Max für gemeinsam variierende Farben."""
    mitte = p.mean(1, keepdims=True)
    q = p - mitte
    # Der am weitesten entfernte Pixel liefert auch bei Grau eine Startachse.
    achse = q[np.arange(len(q)), np.argmax(np.sum(q * q, axis=2), axis=1)]
    for _ in range(6):
        achse = np.einsum("bpc,bp->bc", q, np.einsum("bpc,bc->bp", q, achse))
        achse /= np.maximum(np.linalg.norm(achse, axis=1, keepdims=True), 1e-20)
    proj = np.einsum("bpc,bc->bp", q, achse)
    return (mitte[:, 0] + proj.min(1)[:, None] * achse,
            mitte[:, 0] + proj.max(1)[:, None] * achse)


def _indizes(p, palette):
    return np.argmin(np.sum((p[:, :, None, :] - palette[:, None, :, :]) ** 2, axis=3), axis=2)


def _rgb565(f):
    k = np.rint(np.clip(f, 0, 255) * np.array([31, 63, 31]) / 255).astype(np.uint16)
    return (k[:, 0] << 11) | (k[:, 1] << 5) | k[:, 2]


def _aus565(k):
    r, g, b = (k >> 11) & 31, (k >> 5) & 63, k & 31
    return np.stack(((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)), axis=1).astype(np.float32)


def _bc1_block(p, transparenz=True):
    rgb = p[:, :, :3].astype(np.float32)
    lo, hi = _endpunkte(rgb)
    c0, c1 = _rgb565(hi), _rgb565(lo)
    oben, unten = np.maximum(c0, c1), np.minimum(c0, c1)
    alpha = (p[:, :, 3] < 128).any(1) if transparenz else np.zeros(len(p), bool)
    # BC3 verlangt immer den Vierfarbenmodus, auch bei konstanten Blöcken.
    gleich = oben == unten
    oben = np.where(gleich & (oben < 65535), oben + 1, oben).astype(np.uint16)
    unten = np.where(gleich & (oben == 65535) & (unten == 65535), unten - 1, unten).astype(np.uint16)
    c0, c1 = np.where(alpha, unten, oben), np.where(alpha, oben, unten)
    f0, f1 = _aus565(c0), _aus565(c1)
    pal = np.stack((f0, f1, (2 * f0 + f1) / 3, (f0 + 2 * f1) / 3), axis=1)
    pal[alpha, 2] = (f0[alpha] + f1[alpha]) / 2
    pal[alpha, 3] = 0
    idx = _indizes(rgb, pal)
    if transparenz:
        # Index 3 bleibt im Dreifarbenmodus transparenten Pixeln vorbehalten.
        idx[alpha] = _indizes(rgb[alpha], pal[alpha, :3])
        idx[p[:, :, 3] < 128] = 3
    bits = np.sum(idx.astype(np.uint32) << (2 * np.arange(16, dtype=np.uint32)), axis=1, dtype=np.uint32)
    out = np.empty((len(p), 8), np.uint8)
    out[:, :2] = c0.astype("<u2").view(np.uint8).reshape(-1, 2)
    out[:, 2:4] = c1.astype("<u2").view(np.uint8).reshape(-1, 2)
    out[:, 4:] = bits.astype("<u4").view(np.uint8).reshape(-1, 4)
    return out


def bc1_kodieren(rgba) -> bytes:
    """BC1 mit 1-Bit-Alpha (Schwelle 128), Randblöcke werden aufgefüllt."""
    p = _bloecke(_rgba(rgba))
    return b"".join(_bc1_block(p[i:i + 4096]).tobytes() for i in range(0, len(p), 4096))


def bc3_kodieren(rgba) -> bytes:
    """BC3 mit separater Alpha-Palette und BC1-Vierfarbenblöcken."""
    p = _bloecke(_rgba(rgba))
    teile = []
    for i in range(0, len(p), 4096):
        b = p[i:i + 4096]
        a = b[:, :, 3].astype(np.float32)
        a0, a1 = a.max(1), a.min(1)
        pal = np.stack([a0, a1] + [((7 - j) * a0 + j * a1) / 7 for j in range(1, 7)], axis=1)
        idx = np.argmin((a[:, :, None] - pal[:, None, :]) ** 2, axis=2)
        bits = np.sum(idx.astype(np.uint64) << (3 * np.arange(16, dtype=np.uint64)), axis=1, dtype=np.uint64)
        out = np.empty((len(b), 16), np.uint8)
        out[:, 0], out[:, 1] = a0, a1
        out[:, 2:8] = bits.astype("<u8").view(np.uint8).reshape(-1, 8)[:, :6]
        out[:, 8:] = _bc1_block(b, transparenz=False)
        teile.append(out.tobytes())
    return b"".join(teile)


_BC7_GEWICHTE = np.array([0, 4, 9, 13, 17, 21, 26, 30, 34, 38, 43, 47, 51, 55, 60, 64], np.float32)


def _bc7_quant(e):
    # Je Endpunkt teilen alle vier Komponenten dasselbe niederwertige P-Bit.
    e = np.clip(e, 0, 255)
    q0 = np.clip(np.rint(e / 2), 0, 127) * 2
    q1 = np.clip(np.rint((e - 1) / 2), 0, 127) * 2 + 1
    eins = np.sum((q1 - e) ** 2, axis=1) < np.sum((q0 - e) ** 2, axis=1)
    return np.where(eins[:, None], q1, q0).astype(np.uint8)


def bc7_kodieren(rgba) -> bytes:
    """Gültige BC7-Modus-6-Blöcke: RGBA, 7-Bit-Endpunkte + P-Bits, 4-Bit-Indizes.

    Zwei Least-Squares-Schritte verbessern die Hauptachsen-Endpunkte. Das
    Ankerpixel hat nur drei Indexbits; gegebenenfalls werden Endpunkte getauscht.
    """
    p = _bloecke(_rgba(rgba)).copy()
    # Unsichtbares Schwarz zieht sonst die RGBA-Hauptachse von der sichtbaren
    # weißen Kante weg. RGB bei Alpha 0 darf die Farbe der sichtbaren Nachbarn
    # übernehmen. Vollständig transparente Blöcke behalten ihre Originalbytes.
    alpha = p[:, :, 3:4].astype(np.float32)
    summe = alpha.sum(1)
    farbe = np.rint((p[:, :, :3] * alpha).sum(1) / np.maximum(summe, 1))
    fuellen = (alpha == 0) & (summe[:, None] > 0)
    p[:, :, :3] = np.where(fuellen, farbe[:, None], p[:, :, :3])
    teile = []
    w = _BC7_GEWICHTE[None, :, None]
    for start in range(0, len(p), 2048):
        b = p[start:start + 2048].astype(np.float32)
        e0, e1 = _endpunkte(b)
        for schritt in range(3):
            q0, q1 = _bc7_quant(e0), _bc7_quant(e1)
            pal = np.floor(((64 - w) * q0[:, None, :] + w * q1[:, None, :] + 32) / 64)
            idx = _indizes(b, pal)
            if schritt == 2:
                break
            t = _BC7_GEWICHTE[idx] / 64
            s = 1 - t
            aa, bb, ab = (s * s).sum(1), (t * t).sum(1), (s * t).sum(1)
            av, bv = np.einsum("bp,bpc->bc", s, b), np.einsum("bp,bpc->bc", t, b)
            det = aa * bb - ab * ab
            ok = det > 1e-6
            e0[ok] = (av[ok] * bb[ok, None] - bv[ok] * ab[ok, None]) / det[ok, None]
            e1[ok] = (bv[ok] * aa[ok, None] - av[ok] * ab[ok, None]) / det[ok, None]
        swap = idx[:, 0] >= 8
        q0, q1 = np.where(swap[:, None], q1, q0), np.where(swap[:, None], q0, q1)
        idx = np.where(swap[:, None], 15 - idx, idx)
        out = np.zeros((len(b), 16), np.uint8)
        offset = 0

        def bits(values, anzahl):
            nonlocal offset
            values = np.asarray(values, dtype=np.uint16)
            for bit in range(anzahl):
                pos = offset + bit
                out[:, pos // 8] |= (((values >> bit) & 1) << (pos % 8)).astype(np.uint8)
            offset += anzahl

        bits(np.full(len(b), 64), 7)
        for kanal in range(4):
            bits(q0[:, kanal] >> 1, 7)
            bits(q1[:, kanal] >> 1, 7)
        bits(q0[:, 0] & 1, 1)
        bits(q1[:, 0] & 1, 1)
        for pixel in range(16):
            bits(idx[:, pixel], 3 if pixel == 0 else 4)
        assert offset == 128
        teile.append(out.tobytes())
    return b"".join(teile)


def bild_kodieren(rgba, format: int) -> bytes:
    """Einzelne Stufe im EGO-Formatcode kodieren."""
    a = _rgba(rgba)
    if format == 15:
        return a.tobytes()
    if format in (52, 54):
        return bc1_kodieren(a)
    if format in (55, 57, 62):
        return bc3_kodieren(a)
    if format in (69, 70):
        return bc7_kodieren(a)
    raise ValueError("Kein Kodierer für EGO-Texturformat %s" % format)


def mip_kette(rgba, anzahl: Optional[int] = None):
    """RGBA-Mips bis mindestens 4 px, oder ausdrücklich bis zur gewünschten Stufe.

    Pillow filtert RGBA mit vormultipliziertem Alpha: transparente Ränder erzeugen
    keine schwarzen Säume. Kanäle bleiben im vorhandenen Farbraum (UI-Konvention).
    """
    a = _rgba(rgba)
    h, w = a.shape[:2]
    if anzahl is None:
        anzahl = max(1, int(math.floor(math.log2(min(w, h)))) - 1)
    if not isinstance(anzahl, int) or not 1 <= anzahl <= int(math.floor(math.log2(max(w, h)))) + 1:
        raise ValueError("Ungültige Mip-Anzahl")
    stufen = [a]
    for _ in range(1, anzahl):
        w, h = max(1, w // 2), max(1, h // 2)
        stufen.append(np.array(Image.fromarray(stufen[-1]).resize((w, h), Image.Resampling.BOX)))
    return stufen


def textur_ersetzen(archiv: erp.ErpFile, kennung: str, rgba, *,
                    format: Optional[int] = None, mips=None) -> dict:
    """Ersetzt Surface + alle referenzierenden SRVs, vollständig intern.

    kennung darf auf .tif oder .tif.image zeigen. Standard: Format und gesamte
    Mip-Anzahl der Vorlage erhalten. mips='voll': Kette bis mindestens 4 px.
    Eventuelle externe Stufen werden durch die neue interne Kette ersetzt.
    Vor der ersten Änderung sind sämtliche neuen Fragmente fertig kodiert.
    """
    res = archiv.find(kennung)
    if res is None:
        raise KeyError(kennung)
    if res.resource_type == "GfxSRVResource":
        kennung = grafik.TexturSicht.aus_bytes(res.fragments[0].read()).bild
        res = archiv.find(kennung)
    if res is None or res.resource_type != "GfxSurfaceRes":
        raise ValueError("Keine 2D-Bildressource: %s" % kennung)
    t = grafik.Textur.aus_ressource(res)
    if t.dimension != 2 or t.tiefe != 1 or t.schichten != 1 or len(res.fragments) not in (2, 3):
        raise ValueError("Nur einfache 2D-Texturen mit zwei/drei Fragmenten unterstützt")
    a = _rgba(rgba)
    code = t.format if format is None else format
    n = t.mips + len(t.extern_mips) if mips is None else (None if mips == "voll" else mips)
    stufen = mip_kette(a, n)
    daten = b"".join(bild_kodieren(stufe, code) for stufe in stufen)
    h, w = a.shape[:2]
    kopf = grafik.TEXTUR_KOPF.pack(t.dimension, t.flags, code, w, h, 1, len(stufen), 1, *t.reserviert)
    neu = []
    for vorlage, roh in zip(res.fragments[:2], (kopf, daten)):
        fr = copy.copy(vorlage)
        fr.set_data(roh)
        neu.append(fr)
    sichten = []
    for srv in archiv:
        if srv.resource_type != "GfxSRVResource":
            continue
        sicht = grafik.TexturSicht.aus_bytes(srv.fragments[0].read())
        if sicht.bild == res.identifier:
            sicht.format, sicht.mips = code, len(stufen)
            roh = sicht.to_bytes()
            if roh != srv.fragments[0].read():
                fr = copy.copy(srv.fragments[0])
                fr.set_data(roh)
                sichten.append((srv, fr))
    res.fragments = neu
    res.update_hash()
    for srv, fr in sichten:
        srv.fragments[0] = fr
        srv.update_hash()
    return {"kennung": res.identifier, "format": code, "breite": w, "hoehe": h,
            "mips": len(stufen), "stufen": [[s.shape[1], s.shape[0]] for s in stufen],
            "bytes": len(daten), "srv_geaendert": len(sichten),
            "extern_entfernt": t.extern_pfad, "externe_dateien": []}
