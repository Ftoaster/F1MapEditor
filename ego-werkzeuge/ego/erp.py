"""ERP-Archive der EGO-Engine (Formatversion 3, F1 25) lesen und schreiben.

Aufbau einer ERP-Datei (alle Zahlen little-endian), ermittelt durch Analyse
der Originaldateien von F1 25:

    Kopf (48 Byte)
        0   4  Magic "ERPK"
        4   4  Version (3)
        8   8  reserviert (0)
       16   8  Offset des Info-Blocks (48)
       24   8  Größe des Info-Blocks
       32   8  Beginn des Datenblocks (= Info-Offset + Info-Größe)
       40   8  reserviert (0)
    Info-Block
        4  Anzahl Ressourcen
        4  Anzahl Fragmente insgesamt
        je Ressource:
            4  Länge des Eintrags ohne dieses Feld
               (Original-Dateien: exakt; manche Community-Werkzeuge schreiben +1)
            2  Länge der Kennung inkl. Nullbyte
            n  Kennung, z. B. "eaid://environment_package/tracks/..."
           16  Ressourcentyp, mit Nullbytes aufgefüllt (z. B. "GfxMeshRes")
            4  Wert mit noch unbekannter Bedeutung (wird unverändert übernommen;
               je Ressourcentyp fest, vermutlich eine Typ-Version, z. B. GfxMeshRes 0x60000)
            1  Anzahl Fragmente
            je Fragment (33 Byte):
                4  Name ("temp", "syst", "node", "link", "neo_")
                8  Offset relativ zum Datenblock
                8  entpackte Größe
                4  Flags (meist 16)
                1  Kompression: 0x11 / 0x10 = zstd, 0x91 = unkomprimiert
                8  gepackte Größe
           16  MD5 über die entpackten Daten aller Fragmente der Ressource
    Datenblock
        Fragmentdaten lückenlos in Tabellenreihenfolge, ohne Ausrichtung.
"""

import hashlib
import os
import re
import struct
import threading
from dataclasses import dataclass, field
from typing import BinaryIO, Iterable, Iterator, List, Optional, Sequence, Tuple, Union

from . import compression

MAGIC = b"ERPK"
SUPPORTED_VERSION = 3
HEADER = struct.Struct("<4siQQQQQ")  # 48 Byte
COUNTS = struct.Struct("<ii")  # 8 Byte
FRAGMENT = struct.Struct("<4sQQiBQ")  # 33 Byte
HASH_SIZE = 16
TYPE_SIZE = 16

COMP_ZSTD = 0x11  # zstd, so schreibt das Spiel selbst
COMP_ZSTD_TOOL = 0x10  # zstd, so schreiben es Community-Werkzeuge
COMP_STORED = 0x91  # unkomprimiert
ZSTD_CODES = (COMP_ZSTD, COMP_ZSTD_TOOL)

COPY_CHUNK = 8 * 1024 * 1024

PathLike = Union[str, "os.PathLike[str]"]


class ErpFormatError(ValueError):
    """Die Datei entspricht nicht dem erwarteten ERP-Format."""


def compression_name(code: int) -> str:
    return {COMP_ZSTD: "zstd", COMP_ZSTD_TOOL: "zstd(0x10)", COMP_STORED: "unkomprimiert"}.get(code, "0x%02x" % code)


def fragment_packen(data: bytes, compress: bool = True,
                    level: int = compression.DEFAULT_LEVEL) -> Tuple[bytes, int]:
    """(gespeicherte Bytes, Kompressionscode) wie ``ErpFragment.set_data``: zstd nur, wenn es kleiner wird.
    Ohne Seiteneffekte, daher auch in Arbeitsprozessen und Threads nutzbar (``gepackt=`` bei set_data)."""
    data = bytes(data)
    if compress and data:
        candidate = compression.compress(data, level)
        if len(candidate) < len(data):
            return candidate, COMP_ZSTD
    return data, COMP_STORED


def fragment_entpacken(gepackt: bytes, groesse: int, kompression: int) -> bytes:
    """Entpackte Daten aus (gepackte Bytes, entpackte Größe, Kompressionscode) wie ``ErpFragment.read``.
    Für Arbeitsprozesse: gepackt übertragen ist viel kleiner als entpackt."""
    if kompression in ZSTD_CODES:
        return compression.decompress(gepackt, groesse)
    if len(gepackt) == groesse:
        return gepackt
    raise ErpFormatError("Unbekannte Kompression %s bei gepackter Größe %d / entpackter Größe %d"
                         % (compression_name(kompression), len(gepackt), groesse))


def md5_falsch(ressourcen) -> List[str]:
    """Kennungen mit falscher MD5; entpacken und prüfen in Threads (zstd und hashlib geben die GIL frei)."""
    from . import parallel
    ressourcen = list(ressourcen)
    gut = parallel.threads(lambda r: r.verify(), ressourcen)
    return [r.identifier for r, ok in zip(ressourcen, gut) if not ok]


def fragmente_setzen(paare, compress: bool = True, level: int = compression.DEFAULT_LEVEL) -> None:
    """Mehrere Fragmente ersetzen: ``paare`` = [(ErpFragment, Daten), …]; zstd läuft parallel in Threads.
    Ergebnis wie ``set_data`` je Paar (MD5 der Ressourcen danach mit ``update_hash`` nachführen)."""
    from . import parallel
    paare = [(fr, bytes(d)) for fr, d in paare]
    gepackt = parallel.threads(lambda d: fragment_packen(d, compress, level), [d for _, d in paare])
    for (fr, d), g in zip(paare, gepackt):
        fr.set_data(d, gepackt=g)


@dataclass
class ErpFragment:
    """Ein Datenblock einer Ressource (eine Ressource hat 1 bis 255 Fragmente)."""

    name: bytes
    size: int
    flags: int
    compression: int
    packed_size: int
    source_offset: Optional[int] = None  # absoluter Offset in der Quelldatei; None = neue Daten
    _packed: Optional[bytes] = field(default=None, repr=False)
    _archive: "Optional[ErpFile]" = field(default=None, repr=False)

    @property
    def modified(self) -> bool:
        return self._packed is not None

    @property
    def name_str(self) -> str:
        return self.name.decode("latin-1")

    def read_packed(self) -> bytes:
        """Rohdaten so, wie sie in der Datei stehen."""
        if self._packed is not None:
            return self._packed
        if self._archive is None or self.source_offset is None:
            raise ErpFormatError("Fragment hat keine Daten")
        return self._archive._read_at(self.source_offset, self.packed_size)

    def read(self) -> bytes:
        """Entpackte Daten."""
        raw = self.read_packed()
        if self.compression in ZSTD_CODES:
            return compression.decompress(raw, self.size)
        if self.packed_size == self.size:
            return raw
        raise ErpFormatError(
            "Unbekannte Kompression %s bei gepackter Größe %d / entpackter Größe %d"
            % (compression_name(self.compression), self.packed_size, self.size)
        )

    def set_data(self, data: bytes, compress: bool = True, level: int = compression.DEFAULT_LEVEL,
                 gepackt: Optional[Tuple[bytes, int]] = None) -> None:
        """Ersetzt den Inhalt. Komprimiert nur, wenn es tatsächlich kleiner wird
        (so verfahren auch die Originaldateien: kleine Fragmente bleiben unkomprimiert).
        ``gepackt``: schon berechnetes Ergebnis von ``fragment_packen(data, …)`` (z. B. aus einem Arbeitsprozess)."""
        data = bytes(data)
        self._packed, self.compression = gepackt if gepackt is not None else fragment_packen(data, compress, level)
        self.size = len(data)
        self.packed_size = len(self._packed)
        self.source_offset = None


@dataclass
class ErpResource:
    """Eine Ressource, adressiert über ihre Kennung (eaid://...)."""

    identifier: str
    resource_type: str
    unknown: int = 1
    fragments: List[ErpFragment] = field(default_factory=list)
    hash: bytes = b"\0" * HASH_SIZE
    entry_length_delta: int = 0  # Abweichung des gespeicherten Längenfelds (Werkzeug-Eigenheit)
    _identifier_raw: Optional[bytes] = field(default=None, repr=False)
    _type_raw: Optional[bytes] = field(default=None, repr=False)

    def identifier_bytes(self) -> bytes:
        raw = self._identifier_raw
        if raw is not None and _decode_cstr(raw) == self.identifier:
            return raw
        return self.identifier.encode("utf-8") + b"\0"

    def type_bytes(self) -> bytes:
        raw = self._type_raw
        if raw is not None and _decode_cstr(raw) == self.resource_type:
            return raw
        enc = self.resource_type.encode("ascii")
        if len(enc) > TYPE_SIZE:
            raise ErpFormatError("Ressourcentyp länger als 16 Zeichen: %r" % self.resource_type)
        return enc.ljust(TYPE_SIZE, b"\0")

    @property
    def modified(self) -> bool:
        return any(fr.modified for fr in self.fragments)

    def data(self) -> bytes:
        return b"".join(fr.read() for fr in self.fragments)

    def compute_hash(self) -> bytes:
        md5 = hashlib.md5()
        for fr in self.fragments:
            md5.update(fr.read())
        return md5.digest()

    def update_hash(self) -> None:
        self.hash = self.compute_hash()

    def verify(self) -> bool:
        return self.compute_hash() == self.hash

    def set_fragment_data(self, index: int, data: bytes, compress: bool = True,
                          level: int = compression.DEFAULT_LEVEL, gepackt: Optional[Tuple[bytes, int]] = None) -> None:
        self.fragments[index].set_data(data, compress=compress, level=level, gepackt=gepackt)
        self.update_hash()

    def entry_bytes(self, data_offset: int) -> Tuple[bytes, int]:
        """Info-Eintrag dieser Ressource; gibt (Bytes, Offset nach den Daten) zurück."""
        ident = self.identifier_bytes()
        if len(ident) > 0x7FFF:
            raise ErpFormatError("Kennung zu lang")
        if len(self.fragments) > 255:
            raise ErpFormatError("Mehr als 255 Fragmente")
        body = bytearray()
        body += struct.pack("<h", len(ident)) + ident
        body += self.type_bytes()
        body += struct.pack("<iB", self.unknown, len(self.fragments))
        offset = data_offset
        for fr in self.fragments:
            if len(fr.name) != 4:
                raise ErpFormatError("Fragmentname muss 4 Byte lang sein: %r" % fr.name)
            body += FRAGMENT.pack(fr.name, offset, fr.size, fr.flags, fr.compression, fr.packed_size)
            offset += fr.packed_size
        if len(self.hash) != HASH_SIZE:
            raise ErpFormatError("Hash muss 16 Byte lang sein")
        body += self.hash
        return struct.pack("<I", len(body) + self.entry_length_delta) + bytes(body), offset


def _decode_cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("utf-8", "surrogateescape")


class ErpFile:
    """Ein ERP-Archiv. Beim Öffnen wird nur das Inhaltsverzeichnis gelesen;
    Fragmentdaten werden erst bei Bedarf aus der Datei geholt."""

    def __init__(self, version: int = SUPPORTED_VERSION):
        self.version = version
        self.reserved0 = 0
        self.info_offset = HEADER.size
        self.reserved1 = 0
        self.resources: List[ErpResource] = []
        self.path: Optional[str] = None
        self.layout_contiguous = True  # Daten lückenlos in Tabellenreihenfolge?
        self.declared_fragment_count: Optional[int] = None
        self._fh: Optional[BinaryIO] = None
        self._source_size: Optional[int] = None
        self._lock = threading.Lock()          # Lesen aus mehreren Threads (seek + read)

    # ------------------------------------------------------------------ Lesen
    @classmethod
    def open(cls, path: PathLike) -> "ErpFile":
        erp = cls()
        erp.path = os.fspath(path)
        erp._fh = open(erp.path, "rb")
        try:
            erp._source_size = os.fstat(erp._fh.fileno()).st_size
            erp._read_info(erp._fh)
        except Exception:
            erp.close()
            raise
        return erp

    def _read_info(self, f: BinaryIO) -> None:
        head = f.read(HEADER.size + COUNTS.size)
        if len(head) < HEADER.size + COUNTS.size:
            raise ErpFormatError("Datei zu kurz für einen ERP-Kopf")
        magic, version, res0, info_off, info_size, data_off, res1 = HEADER.unpack_from(head, 0)
        if magic != MAGIC:
            raise ErpFormatError("Keine ERP-Datei (Magic %r)" % magic)
        if version != SUPPORTED_VERSION:
            raise ErpFormatError("ERP-Version %d wird nicht unterstützt (nur %d)" % (version, SUPPORTED_VERSION))
        if info_off != HEADER.size or data_off != info_off + info_size:
            raise ErpFormatError("Unerwarteter Kopf: Info-Offset %d, Info-Größe %d, Daten-Offset %d"
                                 % (info_off, info_size, data_off))
        self.version, self.reserved0, self.info_offset, self.reserved1 = version, res0, info_off, res1
        n_res, n_frag = COUNTS.unpack_from(head, HEADER.size)
        self.declared_fragment_count = n_frag
        table = f.read(data_off - HEADER.size - COUNTS.size)
        if len(table) != data_off - HEADER.size - COUNTS.size:
            raise ErpFormatError("Info-Block unvollständig")
        pos = 0
        expected = 0
        for _ in range(n_res):
            (entry_len,) = struct.unpack_from("<I", table, pos)
            start = pos + 4
            pos = start
            (id_len,) = struct.unpack_from("<h", table, pos)
            pos += 2
            ident_raw = table[pos:pos + id_len]
            pos += id_len
            type_raw = table[pos:pos + TYPE_SIZE]
            pos += TYPE_SIZE
            unknown, n_fr = struct.unpack_from("<iB", table, pos)
            pos += 5
            frags = []
            for _k in range(n_fr):
                name, off, size, flags, comp, packed = FRAGMENT.unpack_from(table, pos)
                pos += FRAGMENT.size
                if off != expected:
                    self.layout_contiguous = False
                expected = off + packed
                frags.append(ErpFragment(name=name, size=size, flags=flags, compression=comp,
                                         packed_size=packed, source_offset=data_off + off, _archive=self))
            digest = table[pos:pos + HASH_SIZE]
            pos += HASH_SIZE
            if pos > len(table):
                raise ErpFormatError("Info-Block endet mitten in einem Eintrag")
            self.resources.append(ErpResource(
                identifier=_decode_cstr(ident_raw), resource_type=_decode_cstr(type_raw), unknown=unknown,
                fragments=frags, hash=digest, entry_length_delta=entry_len - (pos - start),
                _identifier_raw=ident_raw, _type_raw=type_raw))
        if pos != len(table):
            raise ErpFormatError("Info-Block hat %d unerwartete Restbytes" % (len(table) - pos))
        if self._source_size is not None and data_off + expected != self._source_size:
            self.layout_contiguous = False

    def _read_at(self, offset: int, length: int) -> bytes:
        if self._fh is None:
            raise ErpFormatError("Quelldatei ist geschlossen")
        with self._lock:
            self._fh.seek(offset)
            data = self._fh.read(length)
        if len(data) != length:
            raise ErpFormatError("Unerwartetes Dateiende bei Offset %d" % offset)
        return data

    def _iter_source(self, offset: int, length: int) -> Iterator[bytes]:
        self._fh.seek(offset)
        remaining = length
        while remaining:
            chunk = self._fh.read(min(COPY_CHUNK, remaining))
            if not chunk:
                raise ErpFormatError("Unerwartetes Dateiende bei Offset %d" % (offset + length - remaining))
            remaining -= len(chunk)
            yield chunk

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "ErpFile":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------ Abfragen
    @property
    def fragment_count(self) -> int:
        return sum(len(r.fragments) for r in self.resources)

    def __iter__(self) -> Iterator[ErpResource]:
        return iter(self.resources)

    def __len__(self) -> int:
        return len(self.resources)

    def find(self, identifier: str) -> Optional[ErpResource]:
        for res in self.resources:
            if res.identifier == identifier:
                return res
        return None

    def find_all(self, pattern: str) -> List[ErpResource]:
        rx = re.compile(pattern)
        return [r for r in self.resources if rx.search(r.identifier) or rx.search(r.resource_type)]

    # ------------------------------------------------------------ Ändern
    def add_resource(self, identifier: str, resource_type: str, fragments: Sequence[Tuple[bytes, bytes]],
                     unknown: int = 1, flags: int = 16, compress: bool = True) -> ErpResource:
        """Fügt eine neue Ressource hinzu. fragments = [(b"temp", daten), ...]"""
        if self.find(identifier) is not None:
            raise ValueError("Ressource existiert bereits: %s" % identifier)
        res = ErpResource(identifier=identifier, resource_type=resource_type, unknown=unknown)
        for name, data in fragments:
            fr = ErpFragment(name=name, size=0, flags=flags, compression=COMP_STORED, packed_size=0, _archive=self)
            fr.set_data(data, compress=compress)
            res.fragments.append(fr)
        res.update_hash()
        self.resources.append(res)
        return res

    def remove_resource(self, identifier: str) -> None:
        res = self.find(identifier)
        if res is None:
            raise KeyError(identifier)
        self.resources.remove(res)

    # ----------------------------------------------------------- Schreiben
    def info_block(self) -> bytes:
        parts = [COUNTS.pack(len(self.resources), self.fragment_count)]
        offset = 0
        for res in self.resources:
            entry, offset = res.entry_bytes(offset)
            parts.append(entry)
        return b"".join(parts)

    def header_block(self) -> bytes:
        info = self.info_block()
        head = HEADER.pack(MAGIC, self.version, self.reserved0, self.info_offset, len(info),
                           self.info_offset + len(info), self.reserved1)
        return head + info

    def iter_bytes(self) -> Iterator[bytes]:
        """Liefert die komplette neue Datei stückweise (speicherschonend)."""
        yield self.header_block()
        for res in self.resources:
            for fr in res.fragments:
                if fr._packed is not None:
                    yield fr._packed
                else:
                    if fr._archive is not self:
                        yield fr.read_packed()
                    else:
                        yield from self._iter_source(fr.source_offset, fr.packed_size)

    def write(self, target: Union[PathLike, BinaryIO], overwrite: bool = False) -> int:
        """Schreibt das Archiv. Ziel darf nicht die Quelldatei sein."""
        if hasattr(target, "write"):
            total = 0
            for chunk in self.iter_bytes():
                target.write(chunk)
                total += len(chunk)
            return total
        out_path = os.path.abspath(os.fspath(target))
        if self.path and os.path.exists(out_path) and os.path.samefile(out_path, self.path):
            raise ValueError("Ziel ist die Quelldatei selbst – das wird nicht überschrieben.")
        if os.path.exists(out_path) and not overwrite:
            raise FileExistsError("Zieldatei existiert bereits: %s" % out_path)
        total = 0
        with open(out_path, "wb") as f:
            for chunk in self.iter_bytes():
                f.write(chunk)
                total += len(chunk)
        return total


def open_erp(path: PathLike) -> ErpFile:
    return ErpFile.open(path)
