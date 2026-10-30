"""pkg-Objektgraphen der EGO-Engine verlustfrei lesen und schreiben.

Alle Zahlen sind little-endian. Der Kopf besteht aus ``!pkg``, einer
Vierzeichenkennung und zwei 32-Bit-Verweisen (Wurzel, Wertetabellen).
Ein Verweis enthält oben ein Typbyte und unten 24 Bit: Typ 0x80 bezeichnet
einen absoluten Blockoffset, sonst ist das Typbyte der Index in !vca.
Der untere Teil ist dann ein Elementindex, bei Strings ein Byteoffset.

Blöcke: !idi = Wörterbuch (Anzahl u24, Version u8, je Feld Name/Wert und
bei Version 1 FNV-1a-Hash); !ili/!vca = Anzahl u32 + Verweise;
!sbi = Byteanzahl + Stringdaten; !vbi = Datentyp, Anzahl, Elementgröße,
Rohdaten; !iar = Bereichsverweis (dstr, Anzahl, bei Anzahl > 0 Startverweis).
Strings: Längenbyte, UTF-8-Daten, Nullbyte. Daten und Zwischenräume bleiben
roh erhalten, insbesondere NaN-Bitmuster und unbekannte Wertetypen.

Der logische Blockbaum kann gemeinsame Teilbäume enthalten. Die physische
Blockreihenfolge wird separat bewahrt. schreiben(lesen(daten)) rekonstruiert
alle Köpfe und Verweise; es gibt keinen Rückgriff auf eine Originaldatei.
Details, Nachweise und Grenzen: docs/formate/pkg.md.
"""

import copy
import math
import struct
import uuid
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Tuple


class PkgFormatError(ValueError):
    """Ungültiger oder noch nicht unterstützter pkg-Aufbau."""


MASK = 0xFFFFFF
FORMATE = {
    b"bool": "B", b"fp32": "f",
    b"si32": "i", b"ui32": "I", b"si64": "q", b"ui64": "Q",
    b"ui16": "H", b"ui08": "B", b"vec3": "3f", b"vec4": "4f",
    b"quat": "4f", b"mat4": "16f", b"bbox": "6f", b"rgba": "4B",
}


def fnv1a(text: str) -> int:
    """FNV-1a-32 über die UTF-8-Bytes eines Feldnamens."""
    wert = 0x811C9DC5
    for byte in text.encode("utf-8", "surrogateescape"):
        wert = ((wert ^ byte) * 0x01000193) & 0xFFFFFFFF
    return wert


@dataclass
class Verweis:
    typ: int
    index: int
    ziel: Optional["Block"] = field(default=None, repr=False)

    @classmethod
    def aus_wort(cls, wert: int) -> "Verweis":
        return cls(wert >> 24, wert & MASK)

    def wort(self, offsets=None) -> int:
        index = self.index
        if self.typ == 0x80 and offsets is not None:
            if self.ziel is None or id(self.ziel) not in offsets:
                raise PkgFormatError("Blockverweis ohne registriertes Ziel")
            index = offsets[id(self.ziel)]
        if not 0 <= self.typ <= 255 or not 0 <= index <= MASK:
            raise PkgFormatError("Verweis überschreitet Typbyte oder 24-Bit-Offset")
        return (self.typ << 24) | index


@dataclass
class Feld:
    name: Verweis
    wert: Verweis
    hash: Optional[int] = None


@dataclass(eq=False)
class Block:
    kennung: bytes
    offset: int = 0
    version: int = 0
    felder: List[Feld] = field(default_factory=list)
    werte: List[Verweis] = field(default_factory=list)
    datentyp: bytes = b""
    elementgroesse: int = 0
    daten: bytes = b""
    davor: bytes = b""
    bereichslaenge: int = 0

    def verweise(self) -> Iterator[Verweis]:
        for feld in self.felder:
            yield feld.name
            yield feld.wert
        yield from self.werte

    @property
    def laenge(self) -> int:
        """Physische Blockgröße ohne Kinder und vorangestelltes Padding."""
        if self.kennung == b"!idi":
            return 8 + len(self.felder) * (12 if self.version else 8)
        if self.kennung in (b"!ili", b"!vca"):
            return 8 + len(self.werte) * 4
        if self.kennung == b"!sbi":
            return 8 + len(self.daten)
        if self.kennung == b"!vbi":
            return 16 + len(self.daten)
        if self.kennung == b"!iar":
            return 16 if self.bereichslaenge else 12
        raise PkgFormatError("Unbekannte Blockkennung")

    @property
    def anzahl(self) -> int:
        if self.kennung == b"!idi":
            return len(self.felder)
        if self.kennung in (b"!ili", b"!vca"):
            return len(self.werte)
        if self.kennung == b"!vbi":
            if not self.elementgroesse or len(self.daten) % self.elementgroesse:
                raise PkgFormatError("Ungültige Länge der Wertetabelle")
            return len(self.daten) // self.elementgroesse
        if self.kennung == b"!iar":
            return self.bereichslaenge
        return len(self.daten)

    @property
    def ausrichtung(self) -> int:
        if self.kennung == b"!vbi":
            # Für unbekannte Typen die vorhandene Ausrichtung konservativ erhalten.
            return {b"mat4": 64, b"vec4": 16, b"quat": 16,
                    b"vec3": 16, b"bbox": 32, b"woid": 16, b"shnm": 16,
                    b"ui64": 8, b"si64": 8}.get(self.datentyp, 4)
        return 4

    def schreiben(self, offsets=None) -> bytes:
        kopf = self.kennung
        if self.kennung == b"!idi":
            if self.version not in (0, 1) or len(self.felder) > MASK:
                raise PkgFormatError("Unzulässige Wörterbuchversion oder Feldanzahl")
            teile = [kopf, struct.pack("<I", (self.version << 24) | len(self.felder))]
            for f in self.felder:
                teile.append(struct.pack("<II", f.name.wort(offsets), f.wert.wort(offsets)))
                if self.version == 1:
                    if f.hash is None:
                        raise PkgFormatError("Wörterbuch Version 1 braucht Feldhashes")
                    teile.append(struct.pack("<I", f.hash))
            return b"".join(teile)
        if self.kennung in (b"!ili", b"!vca"):
            return (kopf + struct.pack("<I", len(self.werte))
                    + b"".join(struct.pack("<I", v.wort(offsets)) for v in self.werte))
        if self.kennung == b"!sbi":
            return kopf + struct.pack("<I", len(self.daten)) + self.daten
        if self.kennung == b"!vbi":
            return (kopf + self.datentyp + struct.pack("<II", self.anzahl, self.elementgroesse)
                    + self.daten)
        if self.kennung == b"!iar":
            if self.datentyp != b"dstr" or len(self.werte) != int(self.bereichslaenge > 0):
                raise PkgFormatError("Ungültiger !iar-dstr-Bereich")
            return (kopf + self.datentyp + struct.pack("<I", self.bereichslaenge)
                    + b"".join(struct.pack("<I", r.wort(offsets)) for r in self.werte))
        raise PkgFormatError("Unbekannte Blockkennung: %r" % self.kennung)


@dataclass
class Paket:
    kennung: bytes
    wurzel: Verweis
    tabellen: Verweis
    bloecke: List[Block]
    nachlauf: bytes = b""

    def tabelle(self, ref: Verweis) -> Block:
        vc = self.tabellen.ziel
        if vc is None or vc.kennung != b"!vca" or ref.typ >= len(vc.werte):
            raise PkgFormatError("Wertetabellenindex außerhalb von !vca")
        block = vc.werte[ref.typ].ziel
        if block is None or block.kennung not in (b"!sbi", b"!vbi"):
            raise PkgFormatError("Verweis bezeichnet keine Wertetabelle")
        return block

    def wert(self, ref: Verweis):
        """Einzelwert auflösen; komplexe Verweise liefern einen Block."""
        if ref.typ == 0x80:
            return ref.ziel
        b = self.tabelle(ref)
        if b.kennung == b"!sbi":
            i = ref.index
            if i >= len(b.daten):
                raise PkgFormatError("Stringoffset außerhalb der Tabelle")
            n = b.daten[i]
            if i + n + 2 > len(b.daten) or b.daten[i + n + 1] != 0:
                raise PkgFormatError("Stringlänge oder Nullabschluss ungültig")
            return b.daten[i + 1:i + n + 1].decode("utf-8", "surrogateescape")
        if ref.index >= b.anzahl:
            raise PkgFormatError("Elementindex außerhalb der Wertetabelle")
        start = ref.index * b.elementgroesse
        raw = b.daten[start:start + b.elementgroesse]
        if b.datentyp == b"woid":
            return raw.hex()
        if b.datentyp == b"shnm":
            return raw.split(b"\0", 1)[0].decode("utf-8", "surrogateescape")
        if b.datentyp == b"vec3" and b.elementgroesse == 16:
            return list(struct.unpack_from("<3f", raw))
        if b.datentyp == b"bbox" and b.elementgroesse == 32:
            return list(struct.unpack_from("<3f", raw)) + list(struct.unpack_from("<3f", raw, 16))
        fmt = FORMATE.get(b.datentyp)
        if fmt is None or struct.calcsize("<" + fmt) != b.elementgroesse:
            return {"datentyp": b.datentyp.decode("ascii", "replace"), "hex": raw.hex()}
        werte = struct.unpack("<" + fmt, raw)
        return werte[0] if len(werte) == 1 else list(werte)

    def feld(self, block: Block, name: str) -> Feld:
        treffer = [f for f in block.felder if self.wert(f.name) == name]
        if len(treffer) != 1:
            raise PkgFormatError("Feld %r fehlt oder ist mehrdeutig" % name)
        return treffer[0]

    def bereichswerte(self, block: Block) -> list:
        """!iar dstr verweist auf aufeinanderfolgende Strings in !sbi."""
        if block.kennung != b"!iar" or block.datentyp != b"dstr":
            raise PkgFormatError("Kein unterstützter Tabellenbereich")
        if not block.bereichslaenge:
            return []
        ref = copy.copy(block.werte[0])
        tabelle = self.tabelle(ref)
        if tabelle.kennung != b"!sbi" or block.bereichslaenge > len(tabelle.daten) // 2:
            raise PkgFormatError("Ungültiger Stringbereich")
        werte = []
        for _ in range(block.bereichslaenge):
            werte.append(self.wert(ref))
            ref.index += tabelle.daten[ref.index] + 2
        return werte

    def inhalt(self, block: Optional[Block] = None):
        """Logischen Baum als Python-Werte liefern (keine verlustfreie JSON-Repräsentation)."""
        aktiv = set()

        def aufloesen(ref):
            if ref.typ != 0x80:
                return self.wert(ref)
            b = ref.ziel
            if b is None or id(b) in aktiv:
                raise PkgFormatError("Zyklischer oder leerer Blockverweis")
            aktiv.add(id(b))
            if b.kennung == b"!idi":
                obj = {}
                for f in b.felder:
                    key = self.wert(f.name)
                    if key in obj:
                        raise PkgFormatError("Doppelter Feldname im JSON-Export: %s" % key)
                    obj[key] = aufloesen(f.wert)
            elif b.kennung in (b"!ili", b"!vca"):
                obj = [aufloesen(v) for v in b.werte]
            elif b.kennung == b"!iar":
                obj = self.bereichswerte(b)
            else:
                obj = {"kennung": b.kennung.decode("ascii"), "hex": b.daten.hex()}
            aktiv.remove(id(b))
            return obj

        return aufloesen(Verweis(0x80, 0, block) if block is not None else self.wurzel)

    def pruefen(self) -> dict:
        """Alle Verweise, Feldnamen und gespeicherten FNV-Hashes untersuchen."""
        hashes = fehler = refs = 0
        namen, hashwerte, geprueft = {}, {}, set()
        for b in self.bloecke:
            for r in b.verweise():
                refs += 1
                key = (r.typ, r.index)
                if r.typ == 0x80:
                    if r.ziel is None:
                        raise PkgFormatError("Blockverweis ohne Ziel")
                elif key not in geprueft:
                    self.wert(r)
                    geprueft.add(key)
            if b.kennung == b"!iar":
                self.bereichswerte(b)
            for f in b.felder:
                key = (f.name.typ, f.name.index)
                if key not in namen:
                    namen[key] = self.wert(f.name)
                name = namen[key]
                if not isinstance(name, str):
                    raise PkgFormatError("Feldname ist kein String")
                if f.hash is not None:
                    hashes += 1
                    if name not in hashwerte:
                        hashwerte[name] = fnv1a(name)
                    fehler += f.hash != hashwerte[name]
        return {"verweise": refs, "feldhashes": hashes, "hashabweichungen": fehler}

    def neuer_wert(self, vorlage: Verweis, daten: bytes) -> Verweis:
        """Wertetabelle erweitern, bestehende gemeinsam genutzte Werte nicht ändern."""
        b = self.tabelle(vorlage)
        if b.kennung == b"!sbi":
            index = len(b.daten)
        else:
            if len(daten) != b.elementgroesse:
                raise PkgFormatError("Falsche Elementgröße beim Hinzufügen")
            index = b.anzahl
        if index > MASK:
            raise PkgFormatError("Wertetabelle überschreitet 24-Bit-Index")
        b.daten += daten
        return Verweis(vorlage.typ, index)

    def instanzen(self) -> List["Instanz"]:
        if self.kennung != b"INST":
            raise PkgFormatError("Instanzen erfordern pkg-Kennung INST")
        liste = self.feld(self.wurzel.ziel, "instances").wert.ziel
        if liste is None or liste.kennung != b"!ili":
            raise PkgFormatError("instances ist keine Liste")
        if any(v.ziel is None or v.ziel.kennung != b"!idi" for v in liste.werte):
            raise PkgFormatError("Instanzliste enthält kein Wörterbuch")
        return [Instanz(self, v.ziel, i) for i, v in enumerate(liste.werte)]


def _pfad(paket: Paket, block: Block, *namen) -> Feld:
    for i, name in enumerate(namen):
        f = paket.feld(block, name)
        if i != len(namen) - 1:
            block = f.wert.ziel
            if block is None or block.kennung != b"!idi":
                raise PkgFormatError("%s ist kein Wörterbuch" % name)
    return f


@dataclass
class Instanz:
    """Sicht auf eine INST-Instanz; fehlende Transform-Overrides bleiben unbekannt."""
    paket: Paket
    block: Block
    index: int

    def _wert(self, *pfad):
        try:
            return self.paket.wert(_pfad(self.paket, self.block, *pfad).wert)
        except PkgFormatError:
            return None

    @property
    def name(self):
        return self._wert("name")

    @property
    def uniqueid(self):
        return self._wert("uniqueid")

    @property
    def modell(self):
        return self._wert("overrides", "Model", "model")

    @property
    def matrix(self):
        wert = self._wert("overrides", "WorldObject", "transform")
        return wert if isinstance(wert, list) and len(wert) == 16 else None

    @property
    def position(self):
        m = self.matrix
        return m[12:15] if m is not None else None

    def export(self) -> dict:
        m = self.matrix
        return {"index": self.index, "name": self.name, "uniqueid": self.uniqueid,
                "template": self._wert("template"), "modell": self.modell,
                "position": self.position, "matrix": m,
                "achsenlaengen": [math.sqrt(sum(m[i + j] ** 2 for j in range(3)))
                                  for i in (0, 4, 8)] if m else None,
                "daten": self.paket.inhalt(self.block)}


def _positionsdaten(instanz: Instanz, position) -> Tuple[Feld, bytes]:
    xyz = tuple(float(x) for x in position)
    if len(xyz) != 3 or not all(math.isfinite(x) for x in xyz):
        raise ValueError("Position muss aus drei endlichen Zahlen bestehen")
    feld = _pfad(instanz.paket, instanz.block, "overrides", "WorldObject", "transform")
    tabelle = instanz.paket.tabelle(feld.wert)
    if tabelle.datentyp != b"mat4" or tabelle.elementgroesse != 64:
        raise PkgFormatError("WorldObject.transform ist keine mat4-Tabelle")
    start = feld.wert.index * 64
    if start + 64 > len(tabelle.daten):
        raise PkgFormatError("Transform-Index außerhalb der Tabelle")
    raw = bytearray(tabelle.daten[start:start + 64])
    struct.pack_into("<3f", raw, 48, *xyz)
    return feld, bytes(raw)


def instanz_verschieben(paket: Paket, index: int, position) -> Instanz:
    """Absolute Position (x,y,z) setzen. Rotation/Skalierung bleiben bitgleich.

    Ein neuer Matrixwert verhindert Nebenwirkungen bei geteilten Tabellenwerten.
    Ein fehlender Transform-Override wird bewusst nicht als Identität erfunden.
    """
    instanzen = paket.instanzen()
    if not 0 <= index < len(instanzen):
        raise IndexError("Instanzindex außerhalb der Liste")
    instanz = instanzen[index]
    feld, raw = _positionsdaten(instanz, position)
    # Geteilte Zahlenwerte sind erlaubt; geteilte Strukturblöcke würden dagegen
    # mehrere Instanzen zugleich verändern. In diesem Sonderfall abbrechen.
    eingang = {}
    for b in paket.bloecke:
        for ref in b.verweise():
            if ref.typ == 0x80:
                eingang[id(ref.ziel)] = eingang.get(id(ref.ziel), 0) + 1
    b = instanz.block
    for schritt in ("overrides", "WorldObject", "transform"):
        if eingang.get(id(b), 0) > 1:
            raise PkgFormatError("Verschieben eines geteilten Strukturblocks ist nicht unterstützt")
        f = paket.feld(b, schritt)
        b = f.wert.ziel
    feld.wert = paket.neuer_wert(feld.wert, raw)
    return instanz


def instanz_hinzufuegen(paket: Paket, vorlage_index: int, position, name: str,
                       uniqueid: Optional[bytes] = None) -> Instanz:
    """Instanz eines vorhandenen Modells samt Komponenten aus Vorlage klonen.

    Modell, Material und Komponenten werden übernommen. Name und 128-Bit-ID
    sind neu. Externe Verweise anderer Ressourcen (EventGraph/PVS usw.) werden
    nicht ergänzt. uniqueid bezeichnet 16 rohe Bytes, keine zugesicherte UUID-
    Byteordnung; ohne Vorgabe werden zufällige, im Layer noch freie Bytes genutzt.
    """
    instanzen = paket.instanzen()
    if not 0 <= vorlage_index < len(instanzen):
        raise IndexError("Vorlagenindex außerhalb der Liste")
    vorlage = instanzen[vorlage_index]
    if not isinstance(vorlage.modell, str) or not vorlage.modell.startswith("eaid://"):
        raise PkgFormatError("Vorlage besitzt keinen expliziten Modellverweis")
    if not name or any(i.name == name for i in instanzen):
        raise ValueError("Instanzname muss nichtleer und im Layer eindeutig sein")
    encoded = name.encode("utf-8")
    if len(encoded) > 255 or b"\0" in encoded:
        raise ValueError("Instanzname überschreitet 255 UTF-8-Bytes oder enthält Null")
    uid = bytes(uniqueid) if uniqueid is not None else uuid.uuid4().bytes
    if len(uid) != 16 or any(i.uniqueid == uid.hex() for i in instanzen):
        raise ValueError("uniqueid muss aus 16 im Layer noch freien Bytes bestehen")
    _, matrixdaten = _positionsdaten(vorlage, position)
    id_feld = _pfad(paket, vorlage.block, "uniqueid")
    id_tabelle = paket.tabelle(id_feld.wert)
    if id_tabelle.datentyp != b"woid" or id_tabelle.elementgroesse != 16:
        raise PkgFormatError("uniqueid ist keine 16-Byte-woid")
    name_feld = _pfad(paket, vorlage.block, "name")
    if paket.tabelle(name_feld.wert).kennung != b"!sbi":
        raise PkgFormatError("Instanzname ist kein String")
    neue_bloecke, gesehen = [], {}

    def klonen(block):
        if id(block) in gesehen:
            return gesehen[id(block)]
        if block.kennung not in (b"!idi", b"!ili", b"!iar"):
            raise PkgFormatError("Unerwarteter Block im Instanz-Teilbaum")
        neu = copy.copy(block)
        neu.offset, neu.davor = 0, b""
        gesehen[id(block)] = neu
        neue_bloecke.append(neu)

        def ref_kopie(r):
            return Verweis(r.typ, r.index, klonen(r.ziel) if r.typ == 0x80 else None)

        neu.felder = [Feld(ref_kopie(f.name), ref_kopie(f.wert), f.hash) for f in block.felder]
        neu.werte = [ref_kopie(r) for r in block.werte]
        return neu

    neu = Instanz(paket, klonen(vorlage.block), len(instanzen))
    nf = _pfad(paket, neu.block, "name")
    uf = _pfad(paket, neu.block, "uniqueid")
    mf = _pfad(paket, neu.block, "overrides", "WorldObject", "transform")
    nf.wert = paket.neuer_wert(nf.wert, bytes([len(encoded)]) + encoded + b"\0")
    uf.wert = paket.neuer_wert(uf.wert, uid)
    mf.wert = paket.neuer_wert(mf.wert, matrixdaten)
    stelle = paket.bloecke.index(paket.tabellen.ziel)
    paket.bloecke[stelle:stelle] = neue_bloecke
    liste = paket.feld(paket.wurzel.ziel, "instances").wert.ziel
    liste.werte.append(Verweis(0x80, 0, neu.block))
    return neu


def instanz_matrix_setzen(paket: Paket, instanz: "Instanz", matrix) -> None:
    """Vollständige 4×4-Lagematrix (16 Werte, Zeilenvektor-Layout, Translation 12–14) als neuen mat4-Wert setzen.
    Für frisch geklonte Instanzen (``instanz_hinzufuegen``): deren Strukturblöcke sind nicht geteilt."""
    werte = [float(x) for x in matrix]
    if len(werte) != 16 or not all(math.isfinite(x) for x in werte):
        raise ValueError("Matrix muss aus 16 endlichen Zahlen bestehen")
    feld = _pfad(paket, instanz.block, "overrides", "WorldObject", "transform")
    tabelle = paket.tabelle(feld.wert)
    if tabelle.datentyp != b"mat4" or tabelle.elementgroesse != 64:
        raise PkgFormatError("WorldObject.transform ist keine mat4-Tabelle")
    feld.wert = paket.neuer_wert(feld.wert, struct.pack("<16f", *werte))


def punkt_transformieren(position, matrix) -> List[float]:
    """Zeilenvektor [x,y,z,1] mit der gespeicherten affinen Matrix multiplizieren."""
    return [sum(position[j] * matrix[j * 4 + k] for j in range(3)) + matrix[12 + k]
            for k in range(3)]


def zuschauer(paket: Paket, zonenmatrizen=None) -> Iterator[dict]:
    """CRWP-Mitglieder mit lokaler Position, optional mit Weltposition liefern.

    zonenmatrizen ordnet CrowdZone.id aus INST genau einer Transform-Matrix zu.
    Nicht aufgelöste Zonen erhalten weltposition=None.
    """
    if paket.kennung != b"CRWP":
        raise PkgFormatError("Zuschauer erfordern CRWP")
    daten = paket.inhalt()
    for zone in daten.get("zones", []):
        matrix = (zonenmatrizen or {}).get(zone["id"])
        for i, mitglied in enumerate(zone.get("members", [])):
            pos = mitglied.get("position")
            yield {"zone": zone["id"], "index": i, **mitglied,
                   "weltposition": punkt_transformieren(pos, matrix)
                   if matrix is not None and pos is not None else None}


def lesen(daten: bytes) -> Paket:
    """Streng begrenzter Parser: folgt Verweisen, sucht niemals blind nach Magic-Bytes."""
    daten = bytes(daten)
    if len(daten) < 16 or daten[:4] != b"!pkg":
        raise PkgFormatError("Kein vollständiger pkg-Kopf")
    kopf = [Verweis.aus_wort(x) for x in struct.unpack_from("<II", daten, 8)]
    if any(r.typ != 0x80 for r in kopf):
        raise PkgFormatError("pkg-Kopf erwartet zwei Blockverweise")
    besucht, enden = {}, {}
    offen = list(kopf)

    def bereich(pos, n):
        if pos < 16 or n < 0 or pos + n > len(daten):
            raise PkgFormatError("Blockbereich außerhalb der Datei: %d + %d" % (pos, n))

    while offen:
        ref = offen.pop()
        if ref.typ != 0x80:
            continue
        p = ref.index
        if p in besucht:
            ref.ziel = besucht[p]
            continue
        bereich(p, 8)
        b = Block(daten[p:p + 4], offset=p)
        besucht[p] = ref.ziel = b
        n = struct.unpack_from("<I", daten, p + 4)[0]
        if b.kennung == b"!idi":
            b.version, n = n >> 24, n & MASK
            if b.version not in (0, 1):
                raise PkgFormatError("Unbekannte !idi-Version %d" % b.version)
            schritt = 12 if b.version else 8
            laenge = 8 + n * schritt
            bereich(p, laenge)
            for i in range(n):
                q = p + 8 + i * schritt
                name, wert = struct.unpack_from("<II", daten, q)
                h = struct.unpack_from("<I", daten, q + 8)[0] if b.version else None
                b.felder.append(Feld(Verweis.aus_wort(name), Verweis.aus_wort(wert), h))
        elif b.kennung in (b"!ili", b"!vca"):
            laenge = 8 + n * 4
            bereich(p, laenge)
            b.werte = [Verweis.aus_wort(x[0]) for x in
                       struct.iter_unpack("<I", daten[p + 8:p + laenge])]
        elif b.kennung == b"!sbi":
            laenge = 8 + n
            bereich(p, laenge)
            b.daten = daten[p + 8:p + laenge]
        elif b.kennung == b"!vbi":
            bereich(p, 16)
            b.datentyp = daten[p + 4:p + 8]
            n, b.elementgroesse = struct.unpack_from("<II", daten, p + 8)
            if not b.elementgroesse:
                raise PkgFormatError("Wertetabelle mit Elementgröße null")
            laenge = 16 + n * b.elementgroesse
            bereich(p, laenge)
            b.daten = daten[p + 16:p + laenge]
        elif b.kennung == b"!iar":
            bereich(p, 12)
            b.datentyp = daten[p + 4:p + 8]
            b.bereichslaenge = struct.unpack_from("<I", daten, p + 8)[0]
            if b.datentyp != b"dstr":
                raise PkgFormatError("Unbekannter !iar-Datentyp %r" % b.datentyp)
            laenge = 16 if b.bereichslaenge else 12
            bereich(p, laenge)
            if b.bereichslaenge:
                b.werte = [Verweis.aus_wort(struct.unpack_from("<I", daten, p + 12)[0])]
        else:
            raise PkgFormatError("Unbekannter Block %r bei %d" % (b.kennung, p))
        enden[p] = p + laenge
        offen.extend(b.verweise())
    bloecke = sorted(besucht.values(), key=lambda b: b.offset)
    ende = 16
    for b in bloecke:
        if b.offset < ende:
            raise PkgFormatError("Überlappende Blöcke bei Offset %d" % b.offset)
        b.davor = daten[ende:b.offset]
        ende = enden[b.offset]
    paket = Paket(daten[4:8], kopf[0], kopf[1], bloecke, daten[ende:])
    if paket.tabellen.ziel.kennung != b"!vca":
        raise PkgFormatError("Zweiter Kopfverweis zeigt nicht auf !vca")
    return paket


def _blockverweise(daten: bytes, pos: int, anzahl: int, schritt: int = 1) -> List[int]:
    """Offsets der Blockverweise (Typ 0x80) unter ``anzahl`` Einträgen à ``schritt`` Worten ab ``pos``;
    je Eintrag zählen die ersten zwei Worte (Feldname, Wert) bzw. das eine Wort."""
    if not anzahl:
        return []
    worte = struct.unpack_from("<%dI" % (anzahl * schritt), daten, pos)
    if schritt > 2:                     # !idi Version 1: Feldhash überspringen
        worte = [w for i, w in enumerate(worte) if i % schritt < 2]
    return [w & MASK for w in worte if w >> 24 == 0x80]


def zeichenketten_bloecke(daten: bytes) -> List[bytes]:
    """Daten aller erreichbaren !sbi-Blöcke in Offset-Reihenfolge – dieselben Blöcke wie ``lesen(daten).bloecke``,
    aber ohne Objektgraph (26.10.2026, für die Verweisprüfung beim Umbenennen; ≈ 10× schneller als ``lesen``).
    Prüft Blockbereiche, Blockarten, Überlappung und den !vca-Kopfverweis wie ``lesen``."""
    daten = bytes(daten)
    if len(daten) < 16 or daten[:4] != b"!pkg":
        raise PkgFormatError("Kein vollständiger pkg-Kopf")
    kopf = struct.unpack_from("<II", daten, 8)
    if any(w >> 24 != 0x80 for w in kopf):
        raise PkgFormatError("pkg-Kopf erwartet zwei Blockverweise")

    def bereich(pos, n):
        if pos < 16 or n < 0 or pos + n > len(daten):
            raise PkgFormatError("Blockbereich außerhalb der Datei: %d + %d" % (pos, n))

    enden, sbi, offen = {}, {}, [w & MASK for w in kopf]
    while offen:
        p = offen.pop()
        if p in enden:
            continue
        bereich(p, 8)
        kennung = daten[p:p + 4]
        n = struct.unpack_from("<I", daten, p + 4)[0]
        if kennung == b"!idi":
            version, n = n >> 24, n & MASK
            if version not in (0, 1):
                raise PkgFormatError("Unbekannte !idi-Version %d" % version)
            laenge = 8 + n * (12 if version else 8)
            bereich(p, laenge)
            offen += _blockverweise(daten, p + 8, n, 3 if version else 2)
        elif kennung in (b"!ili", b"!vca"):
            laenge = 8 + n * 4
            bereich(p, laenge)
            offen += _blockverweise(daten, p + 8, n)
        elif kennung == b"!sbi":
            laenge = 8 + n
            bereich(p, laenge)
            sbi[p] = daten[p + 8:p + laenge]
        elif kennung == b"!vbi":
            bereich(p, 16)
            n, groesse = struct.unpack_from("<II", daten, p + 8)
            if not groesse:
                raise PkgFormatError("Wertetabelle mit Elementgröße null")
            laenge = 16 + n * groesse
            bereich(p, laenge)
        elif kennung == b"!iar":
            bereich(p, 12)
            if daten[p + 4:p + 8] != b"dstr":
                raise PkgFormatError("Unbekannter !iar-Datentyp %r" % daten[p + 4:p + 8])
            bereichslaenge = struct.unpack_from("<I", daten, p + 8)[0]
            laenge = 16 if bereichslaenge else 12
            bereich(p, laenge)
            if bereichslaenge:
                offen += _blockverweise(daten, p + 12, 1)
        else:
            raise PkgFormatError("Unbekannter Block %r bei %d" % (kennung, p))
        enden[p] = p + laenge
    ende = 16
    for p in sorted(enden):
        if p < ende:
            raise PkgFormatError("Überlappende Blöcke bei Offset %d" % p)
        ende = enden[p]
    if daten[(kopf[1] & MASK):(kopf[1] & MASK) + 4] != b"!vca":
        raise PkgFormatError("Zweiter Kopfverweis zeigt nicht auf !vca")
    return [sbi[p] for p in sorted(sbi)]


def schreiben(paket: Paket) -> bytes:
    """Köpfe, Nutzdaten und Verweise neu zusammensetzen; Originalpadding erhalten.

Bei Größenänderungen wird die bisherige Ausrichtungsrestklasse erhalten.
Das bewahrt auch nicht standardmäßig ausgerichtete Community-Dateien.
"""
    if len(paket.kennung) != 4:
        raise PkgFormatError("pkg-Kennung muss vier Bytes lang sein")
    offsets, luecken, pos = {}, {}, 16
    for b in paket.bloecke:
        if id(b) in offsets:
            raise PkgFormatError("Block mehrfach in physischer Blockliste")
        gap = b.davor
        pos += len(gap)
        extra = (b.offset - pos) % b.ausrichtung
        gap += b"\0" * extra
        pos += extra
        offsets[id(b)], luecken[id(b)] = pos, gap
        pos += b.laenge
    teile = [b"!pkg" + paket.kennung,
             struct.pack("<II", paket.wurzel.wort(offsets), paket.tabellen.wort(offsets))]
    for b in paket.bloecke:
        teile.extend((luecken[id(b)], b.schreiben(offsets)))
    teile.append(paket.nachlauf)
    return b"".join(teile)


# Entspricht den englischen Einstiegspunkten der ERP-Bibliothek.
loads = lesen
dumps = schreiben
