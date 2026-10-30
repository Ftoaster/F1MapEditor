"""Binär-XML (BXML) der EGO-Engine (F1 25) lesen und schreiben.

BXML ist ein generisches, ereignisorientiertes Binärformat für XML-Dokumente. Es steckt
in den Streckenlogik-Ressourcen (KI-Linie, Trackspace, Trackmarkup, Kamera-Splines …),
aber auch in Fahrzeugdaten (VTF), Sequenzen, KI-Bremslinien und weiteren Typen.
Ermittelt durch Analyse aller 5.341 BXML-Fragmente des Spiels (siehe
ergebnisse/bxml_rundlauf.md); alle Zahlen little-endian.

Aufbau eines BXML-Fragments
    Kopf (5 Byte)
        0   1  0x01 (vermutlich Formatversion)
        1   4  "BXML"
    danach lückenlos Datensätze bis zum Ende des Fragments:
        2  Länge L des Datensatzes ohne dieses Feld (u16) = 4 + Länge der Zeichenketten
        2  Datensatztyp (u16)
        2  Anzahl (u16): Zahl der Attribute bei Typ 0, sonst 0
        L-4  Zeichenketten, jede mit Nullbyte abgeschlossen (UTF-8, im Spiel nur ASCII)

    Datensatztypen
        0  Element-Anfang   Name, dann Anzahl × (Attributname, Attributwert)
        1  Text             eine Zeichenkette (Textinhalt des umgebenden Elements)
        5  Element-Ende     keine Zeichenketten
        6  Endmarke         keine Zeichenketten; steht nur am Schluss. Das Spiel schreibt
                            zwei, ein Community-Werkzeug (Sepang-Mod) nur eine.

    Ein Dokument hat genau ein Wurzelelement. Leerraum zwischen Elementen wird nicht
    gespeichert; Text wird unverändert gespeichert (inklusive Zeilenumbrüchen \\r\\n und
    Einrückung). Die Reihenfolge der Attribute bleibt erhalten. Es gibt weder Kommentare
    noch Namensräume, Verarbeitungsanweisungen oder Entitäten.

Zweitfragment "TSMD" (120 Byte)
    Ressourcen mit Fragmentname "temp" (AISplineData, Spline, VTF, Sequence …) haben ein
    zweites Fragment mit einer Zeichenketten-Statistik des BXML-Dokuments, vermutlich zum
    Vorbelegen von Speicher beim Laden. Die "syst"-Ressourcen (TrackSpace, TM, TS) haben
    keines.
         0   4  "TSMD"
         4   4  Version (1)
         8 112  14 Paare (Anzahl u32, Zeichen u32); Zeichen = Summe der Längen ohne Nullbyte:
                 1 Attributnamen          8 Attributnamen, verschieden
                 2 Attributwerte          9 Attributwerte, verschieden
                 3 Elementnamen          10 Elementnamen, verschieden
                 4 Texte                 11 Texte, verschieden
                 5 Namen (1+3)           12 Namen, verschieden (Vereinigung 8, 10)
                 6 Werte (2+4)           13 Werte, verschieden (Vereinigung 9, 11)
                 7 alle (5+6)            14 alle, verschieden (Vereinigung 12, 13)
    compute_tsmd() berechnet es aus einem Dokument (bitgenau für alle Spieldateien geprüft).

Textform (XML)
    to_xml() erzeugt lesbares XML; from_xml() liest es zurück. Für alle Spieldateien gilt
    encode(from_xml(to_xml(decode(b)))) == b. Damit das gelingt:
      - Die Zahl der Endmarken steht in einer Verarbeitungsanweisung
        <?ego-bxml endmarken="2"?> vor dem Wurzelelement.
      - Elemente, die Text enthalten, werden ohne zusätzliche Einrückung geschrieben.
      - \\r im Text und \\t \\n \\r in Attributwerten werden als Zeichenreferenz geschrieben.
      - from_xml() übernimmt nur Text, der nicht ausschließlich aus Leerraum besteht
        (so verhält sich offenbar auch der Compiler des Spiels).
"""

import os
import struct
import xml.parsers.expat
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Union

MAGIC = b"\x01BXML"
RECORD = struct.Struct("<HHH")  # Länge, Typ, Anzahl

REC_START = 0
REC_TEXT = 1
REC_END = 5
REC_END_DOC = 6
RECORD_TYPE_NAMES = {REC_START: "Element-Anfang", REC_TEXT: "Text", REC_END: "Element-Ende",
                     REC_END_DOC: "Endmarke"}

GAME_END_MARKERS = 2  # so schreibt das Spiel

TSMD_MAGIC = b"TSMD"
TSMD_VERSION = 1
TSMD_SIZE = 120
TSMD_FIELDS = (
    "attributnamen", "attributwerte", "elementnamen", "texte", "namen", "werte", "alle",
    "attributnamen_verschieden", "attributwerte_verschieden", "elementnamen_verschieden",
    "texte_verschieden", "namen_verschieden", "werte_verschieden", "alle_verschieden",
)

XML_PI_TARGET = "ego-bxml"
XML_WS = " \t\r\n"


class BxmlFormatError(ValueError):
    """Die Daten entsprechen nicht dem erwarteten BXML-Format."""


def is_bxml(data: bytes) -> bool:
    return data[:len(MAGIC)] == MAGIC


def is_tsmd(data: bytes) -> bool:
    return data[:4] == TSMD_MAGIC


# ---------------------------------------------------------------------- Datenmodell
@dataclass
class Element:
    """Ein XML-Element. children enthält Elemente und Textknoten (str) in Dokumentreihenfolge."""

    name: str
    attrib: Dict[str, str] = field(default_factory=dict)  # Reihenfolge bleibt erhalten
    children: List[Union["Element", str]] = field(default_factory=list)

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return self.attrib.get(key, default)

    def set(self, key: str, value) -> None:
        """Setzt ein Attribut; ein vorhandenes behält seinen Platz in der Reihenfolge."""
        self.attrib[key] = str(value)

    @property
    def elements(self) -> List["Element"]:
        return [c for c in self.children if isinstance(c, Element)]

    @property
    def text(self) -> str:
        return "".join(c for c in self.children if isinstance(c, str))

    def find(self, name: str) -> Optional["Element"]:
        for c in self.children:
            if isinstance(c, Element) and c.name == name:
                return c
        return None

    def findall(self, name: str) -> List["Element"]:
        return [c for c in self.children if isinstance(c, Element) and c.name == name]

    def iter(self, name: Optional[str] = None) -> Iterator["Element"]:
        """Alle Elemente des Teilbaums (einschließlich self) in Dokumentreihenfolge."""
        stack = [self]
        while stack:
            el = stack.pop()
            if name is None or el.name == name:
                yield el
            stack.extend(reversed([c for c in el.children if isinstance(c, Element)]))

    def append(self, child: Union["Element", str]) -> Union["Element", str]:
        self.children.append(child)
        return child


@dataclass
class Document:
    """Ein BXML-Dokument: Wurzelelement + Zahl der Endmarken (Spiel: 2)."""

    root: Element
    end_markers: int = GAME_END_MARKERS

    def iter(self, name: Optional[str] = None) -> Iterator[Element]:
        return self.root.iter(name)


# ------------------------------------------------------------------------- Lesen
def _dec(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise BxmlFormatError("Zeichenkette ist kein UTF-8: %r" % raw[:40]) from e


def iter_records(data: bytes):
    """Liefert die Datensätze roh als (Offset, Typ, Anzahl, [Zeichenketten als Bytes])."""
    if not is_bxml(data):
        raise BxmlFormatError("Kein BXML (Kopf %r)" % data[:6])
    pos = len(MAGIC)
    n = len(data)
    while pos < n:
        if pos + RECORD.size > n:
            raise BxmlFormatError("Datensatzkopf bei Offset %d abgeschnitten" % pos)
        length, rtype, count = RECORD.unpack_from(data, pos)
        end = pos + 2 + length
        if length < 4 or end > n:
            raise BxmlFormatError("Ungültige Datensatzlänge %d bei Offset %d" % (length, pos))
        body = data[pos + RECORD.size:end]
        if body:
            if body[-1:] != b"\0":
                raise BxmlFormatError("Zeichenketten bei Offset %d nicht nullterminiert" % pos)
            strings = body[:-1].split(b"\0")
        else:
            strings = []
        yield pos, rtype, count, strings
        pos = end


def decode(data: bytes) -> Document:
    """Wandelt BXML-Bytes in ein Document um. Prüft die Struktur streng."""
    stack: List[Element] = []
    root: Optional[Element] = None
    end_markers = 0
    for pos, rtype, count, strings in iter_records(data):
        if end_markers and rtype != REC_END_DOC:
            raise BxmlFormatError("Datensatz nach der Endmarke bei Offset %d" % pos)
        if rtype == REC_START:
            if len(strings) != 1 + 2 * count:
                raise BxmlFormatError("Element bei Offset %d: %d Zeichenketten für %d Attribute"
                                      % (pos, len(strings), count))
            el = Element(_dec(strings[0]))
            for k in range(1, len(strings), 2):
                key = _dec(strings[k])
                if key in el.attrib:
                    raise BxmlFormatError("Attribut %r doppelt bei Offset %d" % (key, pos))
                el.attrib[key] = _dec(strings[k + 1])
            if stack:
                stack[-1].children.append(el)
            elif root is None:
                root = el
            else:
                raise BxmlFormatError("Zweites Wurzelelement bei Offset %d" % pos)
            stack.append(el)
        elif rtype == REC_TEXT:
            if count != 0 or len(strings) != 1:
                raise BxmlFormatError("Ungültiger Textdatensatz bei Offset %d" % pos)
            if not stack:
                raise BxmlFormatError("Text außerhalb des Wurzelelements bei Offset %d" % pos)
            stack[-1].children.append(_dec(strings[0]))
        elif rtype == REC_END:
            if count != 0 or strings:
                raise BxmlFormatError("Ungültiges Element-Ende bei Offset %d" % pos)
            if not stack:
                raise BxmlFormatError("Element-Ende ohne offenes Element bei Offset %d" % pos)
            stack.pop()
        elif rtype == REC_END_DOC:
            if count != 0 or strings:
                raise BxmlFormatError("Ungültige Endmarke bei Offset %d" % pos)
            if stack or root is None:
                raise BxmlFormatError("Endmarke vor Abschluss des Dokuments bei Offset %d" % pos)
            end_markers += 1
        else:
            raise BxmlFormatError("Unbekannter Datensatztyp %d bei Offset %d" % (rtype, pos))
    if root is None:
        raise BxmlFormatError("Dokument ohne Wurzelelement")
    if stack:
        raise BxmlFormatError("Dokument endet mit %d offenen Elementen" % len(stack))
    if not end_markers:
        raise BxmlFormatError("Dokument ohne Endmarke")
    return Document(root=root, end_markers=end_markers)


# ----------------------------------------------------------------------- Schreiben
def _record(rtype: int, count: int, strings) -> bytes:
    body = b"".join(s.encode("utf-8") + b"\0" for s in strings)
    length = 4 + len(body)
    if length > 0xFFFF:
        raise BxmlFormatError("Datensatz zu lang (%d Byte, höchstens 65.535)" % length)
    return RECORD.pack(length, rtype, count) + body


_END = RECORD.pack(4, REC_END, 0)
_END_DOC = RECORD.pack(4, REC_END_DOC, 0)


def encode(doc: Union[Document, Element]) -> bytes:
    """Wandelt ein Document (oder ein Wurzelelement) in BXML-Bytes um."""
    if isinstance(doc, Element):
        doc = Document(doc)
    out = [MAGIC]
    stack: List[Union[Element, str, None]] = [doc.root]
    while stack:
        node = stack.pop()
        if node is None:
            out.append(_END)
        elif isinstance(node, str):
            out.append(_record(REC_TEXT, 0, (node,)))
        else:
            strings = [node.name]
            for k, v in node.attrib.items():
                strings.append(k)
                strings.append(v)
            out.append(_record(REC_START, len(node.attrib), strings))
            stack.append(None)
            stack.extend(reversed(node.children))
    out.extend([_END_DOC] * doc.end_markers)
    return b"".join(out)


# ----------------------------------------------------------------------- TSMD
def compute_tsmd(doc: Union[Document, Element]) -> bytes:
    """Berechnet das 120-Byte-Zweitfragment "TSMD" (Zeichenketten-Statistik)."""
    root = doc.root if isinstance(doc, Document) else doc
    attr_names: List[bytes] = []
    attr_values: List[bytes] = []
    elem_names: List[bytes] = []
    texts: List[bytes] = []
    for el in root.iter():
        elem_names.append(el.name.encode("utf-8"))
        for k, v in el.attrib.items():
            attr_names.append(k.encode("utf-8"))
            attr_values.append(v.encode("utf-8"))
        for c in el.children:
            if isinstance(c, str):
                texts.append(c.encode("utf-8"))

    def stat(items):
        return len(items), sum(len(s) for s in items)

    u_an, u_av, u_en, u_tx = set(attr_names), set(attr_values), set(elem_names), set(texts)
    pairs = [
        stat(attr_names), stat(attr_values), stat(elem_names), stat(texts),
        stat(attr_names + elem_names), stat(attr_values + texts),
        stat(attr_names + elem_names + attr_values + texts),
        stat(u_an), stat(u_av), stat(u_en), stat(u_tx),
        stat(u_an | u_en), stat(u_av | u_tx), stat(u_an | u_en | u_av | u_tx),
    ]
    out = TSMD_MAGIC + struct.pack("<I", TSMD_VERSION)
    for count, chars in pairs:
        out += struct.pack("<II", count, chars)
    return out


def parse_tsmd(data: bytes) -> Dict[str, object]:
    """Liest ein TSMD-Fragment in ein Wörterbuch {feld: (anzahl, zeichen)}."""
    if len(data) != TSMD_SIZE or not is_tsmd(data):
        raise BxmlFormatError("Kein TSMD-Fragment (Länge %d, Kopf %r)" % (len(data), data[:4]))
    (version,) = struct.unpack_from("<I", data, 4)
    values = struct.unpack_from("<28I", data, 8)
    out: Dict[str, object] = {"version": version}
    for i, name in enumerate(TSMD_FIELDS):
        out[name] = (values[2 * i], values[2 * i + 1])
    return out


# --------------------------------------------------------------------- XML-Text
def _check_xml_chars(s: str) -> None:
    for ch in s:
        if ch < " " and ch not in "\t\n\r":
            raise BxmlFormatError("Steuerzeichen %r ist in XML nicht darstellbar" % ch)


def _esc_attr(s: str) -> str:
    _check_xml_chars(s)
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
            .replace("\t", "&#9;").replace("\n", "&#10;").replace("\r", "&#13;"))


def _esc_text(s: str) -> str:
    _check_xml_chars(s)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\r", "&#13;")


def _has_text(el: Element) -> bool:
    return any(isinstance(c, str) for c in el.children)


def to_xml(doc: Union[Document, Element], indent: str = "  ") -> str:
    """Erzeugt XML-Text. Elemente mit Text werden ohne zusätzliche Einrückung geschrieben,
    damit from_xml() den Text unverändert zurückliest."""
    if isinstance(doc, Element):
        doc = Document(doc)
    out = ['<?xml version="1.0" encoding="utf-8"?>\n',
           '<?%s endmarken="%d"?>\n' % (XML_PI_TARGET, doc.end_markers)]

    def start_tag(el: Element, close: bool) -> str:
        attrs = "".join(' %s="%s"' % (k, _esc_attr(v)) for k, v in el.attrib.items())
        return "<%s%s%s>" % (el.name, attrs, "/" if close else "")

    def compact(el: Element) -> str:
        if not el.children:
            return start_tag(el, True)
        parts = [start_tag(el, False)]
        for c in el.children:
            parts.append(_esc_text(c) if isinstance(c, str) else compact(c))
        parts.append("</%s>" % el.name)
        return "".join(parts)

    # Iterativ (tiefe Dokumente), Einträge: (Element, Tiefe, schließen?)
    stack = [(doc.root, 0, False)]
    while stack:
        el, depth, closing = stack.pop()
        pad = indent * depth
        if closing:
            out.append("%s</%s>\n" % (pad, el.name))
        elif not el.children:
            out.append(pad + start_tag(el, True) + "\n")
        elif _has_text(el):
            out.append(pad + compact(el) + "\n")
        else:
            out.append(pad + start_tag(el, False) + "\n")
            stack.append((el, depth, True))
            for c in reversed(el.children):
                stack.append((c, depth + 1, False))
    return "".join(out)


def from_xml(text: Union[str, bytes]) -> Document:
    """Liest XML-Text (z. B. aus to_xml()) in ein Document. Text, der nur aus Leerraum
    besteht, wird verworfen; anderer Text bleibt unverändert."""
    parser = xml.parsers.expat.ParserCreate()
    parser.ordered_attributes = True
    parser.buffer_text = True
    stack: List[Element] = []
    pending: List[str] = []
    state = {"root": None, "end_markers": GAME_END_MARKERS}

    def flush():
        if pending:
            t = "".join(pending)
            pending.clear()
            if t.strip(XML_WS):
                if not stack:
                    raise BxmlFormatError("Text außerhalb des Wurzelelements: %r" % t[:40])
                stack[-1].children.append(t)

    def on_start(name, attrs):
        flush()
        el = Element(name)
        for i in range(0, len(attrs), 2):
            el.attrib[attrs[i]] = attrs[i + 1]
        if stack:
            stack[-1].children.append(el)
        elif state["root"] is None:
            state["root"] = el
        else:
            raise BxmlFormatError("Zweites Wurzelelement %r" % name)
        stack.append(el)

    def on_end(name):
        flush()
        stack.pop()

    def on_chars(data):
        pending.append(data)

    def on_pi(target, data):
        if target != XML_PI_TARGET:
            return
        for part in data.split():
            key, _, val = part.partition("=")
            if key == "endmarken":
                state["end_markers"] = int(val.strip("\"'"))

    parser.StartElementHandler = on_start
    parser.EndElementHandler = on_end
    parser.CharacterDataHandler = on_chars
    parser.ProcessingInstructionHandler = on_pi
    try:
        if isinstance(text, str):
            parser.Parse(text.encode("utf-8"), True)
        else:
            parser.Parse(text, True)
    except xml.parsers.expat.ExpatError as e:
        raise BxmlFormatError("XML-Fehler: %s" % e) from e
    flush()
    if state["root"] is None:
        raise BxmlFormatError("XML ohne Wurzelelement")
    return Document(root=state["root"], end_markers=state["end_markers"])


# -------------------------------------------------------------- ERP-Ressourcen
def read_resource(resource) -> Document:
    """Liest das BXML-Dokument einer ERP-Ressource (erstes Fragment)."""
    return decode(resource.fragments[0].read())


def write_resource(resource, doc: Document) -> None:
    """Schreibt ein Dokument in eine ERP-Ressource zurück: Fragment 0 = BXML, ein
    vorhandenes TSMD-Fragment wird neu berechnet. Der MD5 der Ressource wird neu gesetzt."""
    resource.fragments[0].set_data(encode(doc))
    for fr in resource.fragments[1:]:
        if fr.size == TSMD_SIZE and is_tsmd(fr.read()):
            fr.set_data(compute_tsmd(doc))
    resource.update_hash()


def load_xml(path: Union[str, "os.PathLike[str]"]) -> Document:
    with open(path, "rb") as f:
        return from_xml(f.read())


def save_xml(doc: Union[Document, Element], path: Union[str, "os.PathLike[str]"]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(to_xml(doc))
