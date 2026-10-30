"""PVS-Sichtbarkeitsdaten (PVSObjIDRes ``*.header.pvsbin`` und PVSVisRes ``*.volumedata.pvsbin``).

Format siehe ``docs/formate/pvs.md``. Gesichert: Aufbau, Zellentabelle, beide
RLE-Varianten, XOR-Referenzen, Anhang. Vermutung: Bit ``i`` = Objekt ``i`` (ID ``i+1``).

Rundlauf: ``vis_schreiben(vis_lesen(b)) == b`` (Rohbytes unveränderter Zellen bleiben erhalten).
Geänderte Zellen werden mit dem eigenen Kodierer neu gepackt.
"""
from __future__ import annotations

import os
import re
import struct
import tempfile
from dataclasses import dataclass, field
from typing import List, Optional

_LAEUFE = re.compile(b"[^\x00]+")      # Folgen von Nicht-Null-Bytes


def _xor(a: bytes, b: bytes) -> bytes:
    return (int.from_bytes(a, "little") ^ int.from_bytes(b, "little")).to_bytes(len(a), "little")


def _oder(a: bytes, b: bytes) -> bytes:
    return (int.from_bytes(a, "little") | int.from_bytes(b, "little")).to_bytes(len(a), "little")


KOPF = 72
ZELLE = 32
TEILSTROEME = 5


class PvsFormatError(ValueError):
    pass


# --------------------------------------------------------------------------- Objektliste
@dataclass
class ObjEintrag:
    name: str
    id: int
    wert: int = 1          # bisher immer 1
    groesse: int = 0       # Feldgröße des Namens (für bitgenauen Rundlauf)


def obj_lesen(daten: bytes) -> List[ObjEintrag]:
    n = struct.unpack_from("<I", daten, 0)[0]
    o = 4
    aus = []
    for _ in range(n):
        groesse, laenge = struct.unpack_from("<II", daten, o)
        name = daten[o + 8:o + 8 + laenge].decode("utf-8")
        oid, wert = struct.unpack_from("<II", daten, o + 8 + groesse)
        aus.append(ObjEintrag(name, oid, wert, groesse))
        o += 16 + groesse
    if o != len(daten):
        raise PvsFormatError(f"Objektliste: {len(daten) - o} Restbytes")
    return aus


def obj_schreiben(eintraege: List[ObjEintrag]) -> bytes:
    teile = [struct.pack("<I", len(eintraege))]
    for e in eintraege:
        roh = e.name.encode("utf-8")
        groesse = e.groesse or ((len(roh) + 1 + 3) & ~3)
        teile.append(struct.pack("<II", groesse, len(roh)) + roh.ljust(groesse, b"\0")
                     + struct.pack("<II", e.id, e.wert))
    return b"".join(teile)


# --------------------------------------------------------------------------- Kodierung
def strom_dekodieren(s: bytes, absolut: bool, laenge: Optional[int] = None) -> bytes:
    """Ein Teilstrom: Steuerbyte c -> (c>>k) Nullbytes + (c & maske) Literalbytes; c==0 Ende."""
    k, m = (3, 7) if absolut else (2, 3)
    aus = bytearray()
    i = 0
    while True:
        c = s[i]
        i += 1
        if c == 0:
            break
        aus += bytes(c >> k)
        n = c & m
        aus += s[i:i + n]
        i += n
    if i != len(s):
        raise PvsFormatError(f"Teilstrom: Ende bei {i}, Länge {len(s)}")
    if laenge is not None and len(aus) != laenge:
        raise PvsFormatError(f"Teilstrom: {len(aus)} statt {laenge} Byte")
    return bytes(aus)


def strom_kodieren(roh: bytes, absolut: bool) -> bytes:
    """Gieriger Kodierer (nicht zwingend byte-identisch zum Spielwerkzeug, aber gültig).

    Je Steuerbyte höchstens ``maxnull`` Nullen, dann bis zu ``m`` Nicht-Null-Bytes. Seit 26.10.2026 läuft die
    Schleife über Folgen von Nicht-Null-Bytes statt über jedes Steuerbyte (gleiche Ausgabe, ≈ 2× schneller)."""
    k, m = (3, 7) if absolut else (2, 3)
    maxnull = 255 >> k
    nur_nullen = bytes([maxnull << k])
    aus = bytearray()
    p = 0
    for lauf in _LAEUFE.finditer(roh):
        a, e = lauf.span()
        g = a - p
        if g > maxnull:                  # volle Null-Steuerbytes, bis höchstens maxnull Nullen übrig sind
            v = (g - 1) // maxnull
            aus += nur_nullen * v
            g -= v * maxnull
        lit = min(m, e - a)
        aus.append((g << k) | lit)
        aus += roh[a:a + lit]
        a += lit
        while a < e:                     # Rest der Folge ohne Nullen davor
            lit = min(m, e - a)
            aus.append(lit)
            aus += roh[a:a + lit]
            a += lit
        p = e
    v, r = divmod(len(roh) - p, maxnull)
    aus += nur_nullen * v
    if r:
        aus.append(r << k)
    aus.append(0)
    return bytes(aus)


# --------------------------------------------------------------------------- Sichtbarkeit
@dataclass
class Zelle:
    w0: int
    w1: int
    x: int
    y: int
    z: int
    w: int
    grenzen: List[int]          # 4 Anfänge der Teilströme 2..5
    rest: bytes                 # 8 Byte (bisher 0)
    roh: bytes = b""            # gepackte Daten (alle 5 Teilströme)
    neu: Optional[List[bytes]] = None   # geänderte, vollständige Bitmengen (entpackt)

    @property
    def referenz(self) -> int:
        return (self.w1 << 5) | (self.w0 >> 27)

    @property
    def offset(self) -> int:
        return self.w0 & 0x7FFFFFF


@dataclass
class PvsVis:
    kopf: bytes                 # 72 Byte
    zusatz: bytes               # 40 Byte (5 Floats + Nullen)
    zellen: List[Zelle]
    anhang_block: bytes         # Byteblock (Größe Kopf+62)
    punkte: bytes               # Gruppe-C-Positionen (16 B je Punkt)
    kugeln: bytes               # Hüllkugeln (16 B je Objekt)
    _cache: dict = field(default_factory=dict, repr=False)
    quelle: bytes = field(default=b"", repr=False)      # gelesene Datei (vis_lesen), für Arbeitsprozesse

    def feld(self, off: int, fmt: str = "<I"):
        return struct.unpack_from(fmt, self.kopf, off)[0]

    @property
    def bits(self) -> int:
        return self.feld(36)

    @property
    def bytes_je_menge(self) -> int:
        return (self.bits + 7) // 8

    @property
    def objektzahl(self) -> int:
        return self.feld(16)

    def teilstroeme_roh(self, k: int) -> List[bytes]:
        z = self.zellen[k]
        g = [0] + list(z.grenzen) + [len(z.roh)]
        return [z.roh[g[j]:g[j + 1]] for j in range(TEILSTROEME)]

    def mengen(self, k: int) -> List[bytes]:
        """Vollständige (Referenz aufgelöste) Bitmengen der Zelle k, 5 × bytes."""
        if k in self._cache:
            return self._cache[k]
        z = self.zellen[k]
        if z.neu is not None:
            erg = z.neu
        else:
            ref = z.referenz
            roh = [strom_dekodieren(s, ref == 0, self.bytes_je_menge) for s in self.teilstroeme_roh(k)]
            if ref:
                basis = self.mengen(ref)
                roh = [_xor(x, y) for x, y in zip(roh, basis)]
            erg = roh
        self._cache[k] = erg
        return erg

    def setze_mengen(self, k: int, mengen: List[bytes]) -> None:
        self.mengen(k)
        abh = [i for i, z in enumerate(self.zellen) if z.referenz == k]
        for i in abh:                       # abhängige Zellen vorher vollständig sichern
            self.zellen[i].neu = self.mengen(i)
        self.zellen[k].neu = [bytes(m) for m in mengen]
        self._cache[k] = self.zellen[k].neu


def vis_lesen(daten: bytes) -> PvsVis:
    if daten[:4] != b"PVSD" or struct.unpack_from("<I", daten, 4)[0] != 5:
        raise PvsFormatError("kein PVSD Version 5")
    kopf = daten[:KOPF]
    if struct.unpack_from("<I", kopf, 20)[0] != len(daten) - KOPF:
        raise PvsFormatError("Größenfeld passt nicht")
    a, b = struct.unpack_from("<II", kopf, 52)
    c, blk = struct.unpack_from("<HH", kopf, 60)
    datgr = struct.unpack_from("<I", kopf, 64)[0]
    nobj = struct.unpack_from("<I", kopf, 16)[0]
    zusatz = daten[KOPF:KOPF + 40]
    nz = a + b + c
    tab = KOPF + 40
    basis = tab + nz * ZELLE
    zellen = []
    for k in range(nz):
        w0, w1, x, y, z, w, g1, g2, g3, g4 = struct.unpack_from("<IIhhhHHHHH", daten, tab + k * ZELLE)
        zellen.append(Zelle(w0, w1, x, y, z, w, [g1, g2, g3, g4], daten[tab + k * ZELLE + 24:tab + (k + 1) * ZELLE]))
    for k, zl in enumerate(zellen):
        ende = zellen[k + 1].offset if k + 1 < nz else datgr
        zl.roh = daten[basis + zl.offset:basis + ende]
    o = basis + datgr
    anhang_block = daten[o:o + blk]
    o += blk
    punkte = daten[o:o + 16 * c]
    o += 16 * c
    kugeln = daten[o:o + 16 * nobj]
    if o + 16 * nobj != len(daten):
        raise PvsFormatError(f"Anhang: {len(daten) - o - 16 * nobj} Restbytes")
    return PvsVis(kopf, zusatz, zellen, anhang_block, punkte, kugeln, quelle=bytes(daten))


def _zelle_packen(vis: PvsVis, k: int, kodiert: Optional[dict] = None) -> bytes:
    """``kodiert``: Merkliste {(absolut, Menge): Strom} über alle Zellen; beim Neuaufbau tragen alle Zellen
    dieselben Mengen, so wird jeder Strom nur einmal kodiert (26.10.2026, vorher ≈ 60 s je Bau)."""
    z = vis.zellen[k]
    if z.neu is None:
        return z.roh
    ref = z.referenz
    mengen = z.neu
    if ref:
        basis = vis.mengen(ref)
        # dieselbe Menge wie die Basis (Neuaufbau: leeres Delta) ergibt Nullen ohne Rechnung
        mengen = [bytes(len(x)) if x is y else _xor(x, y) for x, y in zip(mengen, basis)]
    if kodiert is None:
        kodiert = {}
    stroeme = []
    for m in mengen:
        s = kodiert.get((ref == 0, m))
        if s is None:
            s = strom_kodieren(m, ref == 0)
            if len(kodiert) < 1024:      # Speicher begrenzen, wenn jede Zelle andere Mengen hat
                kodiert[(ref == 0, m)] = s
        stroeme.append(s)
    g, pos = [], 0
    for s in stroeme[:-1]:
        pos += len(s)
        g.append(pos)
    if max(g) > 0xFFFF:
        raise PvsFormatError("Teilstromgrenze > 65535")
    z.grenzen = g
    return b"".join(stroeme)


def vis_schreiben(vis: PvsVis) -> bytes:
    kodiert = {}
    gepackt = [_zelle_packen(vis, k, kodiert) for k in range(len(vis.zellen))]
    tab = bytearray()
    off = 0
    for z, roh in zip(vis.zellen, gepackt):
        if off > 0x7FFFFFF:
            raise PvsFormatError("Datenblock > 128 MiB")
        w0 = (z.w0 & 0xF8000000) | off
        tab += struct.pack("<IIhhhHHHHH", w0, z.w1, z.x, z.y, z.z, z.w, *z.grenzen) + z.rest
        off += len(roh)
    daten = b"".join(gepackt)
    kopf = bytearray(vis.kopf)
    struct.pack_into("<I", kopf, 64, len(daten))
    rumpf = vis.zusatz + bytes(tab) + daten + vis.anhang_block + vis.punkte + vis.kugeln
    struct.pack_into("<I", kopf, 20, len(rumpf))
    return bytes(kopf) + rumpf


# --------------------------------------------------------------------------- Änderungen
def _bit_setzen(menge: bytes, bit: int, wert: bool) -> bytes:
    b = bytearray(menge)
    if wert:
        b[bit >> 3] |= 1 << (bit & 7)
    else:
        b[bit >> 3] &= ~(1 << (bit & 7)) & 0xFF
    return bytes(b)


def objekt_ueberall(vis: PvsVis, bit: int, sichtbar: bool = True) -> int:
    """Setzt Bit ``bit`` (= Objekt-ID − 1, Vermutung) in allen Zellen und Teilströmen.

    Gibt die Zahl geänderter Zellen zurück."""
    alle = [vis.mengen(k) for k in range(len(vis.zellen))]
    geaendert = 0
    for k, m in enumerate(alle):
        neu = [_bit_setzen(x, bit, sichtbar) for x in m]
        if neu != list(m):
            vis.zellen[k].neu = neu
            geaendert += 1
        else:
            vis.zellen[k].neu = list(m)
    vis._cache = {k: vis.zellen[k].neu for k in range(len(vis.zellen))}
    return geaendert


def alles_sichtbar(vis: PvsVis, nur_belegte_bits: bool = True) -> None:
    """Jede Zelle sieht alles: Zelle 1 absolut, alle anderen als leeres Delta auf Zelle 1.

    ``nur_belegte_bits``: nur Bits setzen, die in irgendeiner Zelle des Originals je
    Teilstrom vorkommen (vermeidet unbekannte Bedeutungen); sonst alle Bits."""
    nb = vis.bytes_je_menge
    if nur_belegte_bits:
        voll = [bytes(nb)] * TEILSTROEME
        for k in range(len(vis.zellen)):
            voll = [_oder(v, m) for v, m in zip(voll, vis.mengen(k))]
    else:
        rest = vis.bits & 7
        letzte = (1 << rest) - 1 if rest else 0xFF
        voll = [b"\xff" * (nb - 1) + bytes([letzte])] * TEILSTROEME
    for k, z in enumerate(vis.zellen):
        ref = 0 if k == 1 else 1
        z.w0 = (z.w0 & 0x7FFFFFF) | ((ref & 31) << 27)
        z.w1 = ref >> 5
        z.neu = list(voll)
    vis._cache = {k: z.neu for k, z in enumerate(vis.zellen)}


def kugeln(vis: PvsVis):
    """Liste (x, y, z, r) der Objekt-Hüllkugeln."""
    return [struct.unpack_from("<4f", vis.kugeln, 16 * i) for i in range(len(vis.kugeln) // 16)]


# --------------------------------------------------------------------------- Neuaufbau (AP9b)
_TEIL_VIS = None                # Arbeitsprozess: (Pfad der Zwischendatei, gelesenes PvsVis)


def _belegte_bits_teil(bereich):
    """Arbeitsprozess (``ego.parallel``): Vereinigung der Zellen ``bereich`` (Anfang, Ende); die Daten liest jeder
    Arbeitsprozess einmal aus der gemeinsamen Zwischendatei (schneller als 30 MB je Prozess über die Pipe)."""
    global _TEIL_VIS
    from . import parallel
    pfad = parallel.gemeinsam()
    if _TEIL_VIS is None or _TEIL_VIS[0] != pfad:
        with open(pfad, "rb") as f:
            _TEIL_VIS = (pfad, vis_lesen(f.read()))
    return _belegte_bits_bereich(_TEIL_VIS[1], *bereich)


def _belegte_bits_bereich(vis: PvsVis, anfang: int, ende: int) -> List[bytes]:
    voll = [bytes(vis.bytes_je_menge)] * TEILSTROEME
    for k in range(anfang, ende):
        voll = [_oder(v, m) for v, m in zip(voll, vis.mengen(k))]
    return voll


def belegte_bits(vis: PvsVis) -> List[bytes]:
    """Je Teilstrom die Vereinigung aller Zellen (= alle im Original je belegten Bits).

    Unverändert gelesene Daten (26.10.2026): Zellbereiche parallel entpacken und vereinigen (vorher ≈ 7 s für
    die 28.427 Zellen des Portimão-Trägers); die Vereinigung hängt nicht von der Reihenfolge ab."""
    from . import parallel
    n = len(vis.zellen)
    if n < 2000 or not vis.quelle or any(z.neu is not None for z in vis.zellen) or parallel.anzahl() < 2:
        return _belegte_bits_bereich(vis, 0, n)
    schritt = -(-n // (parallel.anzahl() * 2))
    fd, pfad = tempfile.mkstemp(prefix="pvs_", suffix=".bin")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(vis.quelle)
        teile = parallel.abbilden(_belegte_bits_teil, [(a, min(n, a + schritt)) for a in range(0, n, schritt)], pfad)
    finally:
        os.remove(pfad)
    voll = [bytes(vis.bytes_je_menge)] * TEILSTROEME
    for teil in teile:
        voll = [_oder(v, m) for v, m in zip(voll, teil)]
    return voll


def zellen_neu_aufbauen(vis: PvsVis, a_xyz, gruppe_b: str = "behalten", mengen: Optional[List[bytes]] = None,
                        alle_absolut: bool = False) -> PvsVis:
    """Neues PvsVis: Gruppe A = ``a_xyz`` [(x_m, z_m, hoehe_cm), …] (i16), B/C aus dem Original.

    Alle Zellen erhalten dieselben Mengen ``mengen`` (Standard: alle belegten Bits).
    Kodierung: Zellen 0 und 1 sowie Gruppe C absolut, alle übrigen als leeres Delta auf Zelle 1
    (Kettentiefe 1 wie bei 74 % der Originalzellen). ``alle_absolut`` kodiert jede Zelle absolut
    (≈ 19 KB je Zelle bei allen Bits; Datenblock höchstens 128 MiB).
    ``gruppe_b``: "behalten" (Originalzellen) oder "leer" (0 Zellen)."""
    if mengen is None:
        mengen = belegte_bits(vis)
    a, b = vis.feld(52), vis.feld(56)
    c = vis.feld(60, "<H")
    alt_b = vis.zellen[a:a + b] if gruppe_b == "behalten" else []
    alt_c = vis.zellen[a + b:a + b + c]
    neu = [Zelle(0, 0, int(x), int(y), int(z), 0x8000, [0, 0, 0, 0], bytes(8)) for x, y, z in a_xyz]
    neu += [Zelle(0, 0, zl.x, zl.y, zl.z, zl.w, [0, 0, 0, 0], zl.rest) for zl in alt_b + alt_c]
    na, nb = len(a_xyz), len(alt_b)
    for k, zl in enumerate(neu):
        ref = 0 if (alle_absolut or k < 2 or k >= na + nb) else 1
        zl.w0 = (ref & 31) << 27
        zl.w1 = ref >> 5
        zl.neu = list(mengen)
    kopf = bytearray(vis.kopf)
    struct.pack_into("<II", kopf, 52, na, nb)
    struct.pack_into("<H", kopf, 60, len(alt_c))
    erg = PvsVis(bytes(kopf), vis.zusatz, neu, vis.anhang_block, vis.punkte, vis.kugeln)
    erg._cache = {k: zl.neu for k, zl in enumerate(neu)}
    return erg
