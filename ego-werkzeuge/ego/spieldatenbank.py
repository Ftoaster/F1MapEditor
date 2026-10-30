"""Spieldatenbank ``game_package/gamedatabases/gamedatabases.2025.bin`` lesen (nur lesend, AP16).

Aufbau (ermittelt am Original vom 09.10.2026 und an der vom Track Installer geänderten Fassung,
beide vollständig und lückenlos gelesen; Details docs/streckencode.md):

    u32 Anzahl Einträge
    je Eintrag: u32 h, u32 v, u8 typ, u32 Länge, Zeichen[Länge], 0

``v`` ist über die ganze Datei aufsteigend sortiert (Hash des Schlüssels, 50 Kollisionen mit
verschiedenem ``h``), ``h`` ein zweiter Hash. Die Schlüssel (Tabelle/Zeile/Feld) selbst stehen nicht in
der Datei; die Tabellenstruktur ist daraus allein nicht rekonstruierbar. ``typ`` 0…18 ist der
Werttyp (z. B. 5 = bool ``True``/``False``, 13 = Komma-Liste, 2/4 = Text bzw. Verweis auf einen
Datensatznamen). Alle Werte sind Text. Die Zeichen sind additiv verschleiert mit einem laufenden
30-Zeichen-Schlüssel, der über alle Zeichenketten der Datei weiterläuft (Startindex 18), gefunden
durch Klartextvergleich (``False``/``True``).

Einträge schreibt dieses Modul bewusst nicht: Aktivierungen erzeugt der EGO GameDatabases Editor. Das Modul
``datenbank_abbild`` merkt sich eine vom Editor aktivierte Datenbank und kopiert sie als Ganzes zurück, wenn der
Mod deaktiviert wurde (docs/injektion.md).
"""

import struct
from collections import Counter
from typing import List, NamedTuple

SCHLUESSEL = b"qj42Jcq9tG5MYmBZEZZnrH7yg2qTC4"
START = 18


class Eintrag(NamedTuple):
    h: int
    v: int
    typ: int
    wert: bytes


def lesen(pfad: str) -> List[Eintrag]:
    with open(pfad, "rb") as f:
        d = f.read()
    (n,) = struct.unpack_from("<I", d, 0)
    p, k, out = 4, START, []
    for _ in range(n):
        h, v, typ, laenge = struct.unpack_from("<IIBI", d, p)
        roh = d[p + 13:p + 13 + laenge]
        if d[p + 13 + laenge] != 0:
            raise ValueError("Eintrag bei %d ohne Nullbyte" % p)
        wert = bytes((c - SCHLUESSEL[(k + j) % 30]) & 255 for j, c in enumerate(roh))
        out.append(Eintrag(h, v, typ, wert))
        k += laenge
        p += 14 + laenge
    if p != len(d):
        raise ValueError("Datei nicht vollständig gelesen (%d von %d Byte)" % (p, len(d)))
    return out


def vorkommen(eintraege: List[Eintrag], wert: str) -> Counter:
    """Wie oft ein Wert exakt vorkommt, je Werttyp."""
    b = wert.encode("utf-8")
    return Counter(e.typ for e in eintraege if e.wert == b)
