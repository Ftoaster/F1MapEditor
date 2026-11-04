"""EventGraph der Welt (``<code>_common.wep?event_graph``) lesen und schreiben (4.11.2026).

Aufbau (entschlüsselt an 28 Spielstrecken und beiden Portimão-Trägern, bitgenauer Rundlauf):

* Fragment ``!pkgEVGT`` (pkg, ``ego.pkg``): ``factories`` = Knotentypen mit ``nodes``; jeder Knoten hat
  ``inputnames``/``inputids``, ``outputnames``/``outputids``, ``datanames``/``dataids`` und ggf. ``values``
  (z. B. ``worldobjectid`` = ``uniqueid`` einer Instanz). ``varids``/``varvalues``: Datenwerte (Kameranamen, Zahlen)
  über ihre Daten-ID. ``inputcount`` = Zahl der Eingänge (IDs 0…inputcount−1).
* Fragment ohne pkg-Kopf = **Kantentabelle** aus u32-Wörtern: Eine Ausgangs-ID ist eine Wortposition; dort stehen
  ``n`` und danach ``n`` Eingangs-IDs, die der Ausgang auslöst. Die Einträge liegen lückenlos in der Reihenfolge der
  Ausgangs-IDs. Leere Graphen (Weltvarianten, 260 Byte) haben nur das pkg-Fragment.

Beispiel Monza: Ausgang ``entered`` der Zone ``s07a_trigger`` → Knoten ``BroadcastCamera_TriggerZoneEntered`` mit
dem Datenwert ``Camera name`` = ``s07a_cam``. Zonen schalten oft schon die Kamera des nächsten Abschnitts
(``s01a_trigger`` → ``s02a_cam``).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from . import pkg

#: Knotentypen, die eine Kamera per Name schalten
KAMERA_KNOTEN = ("BroadcastCamera_TriggerZoneEntered", "Camera_ChangeCamera", "ReplayHighlights_CameraShot",
                 "BroadcastCamera_StoreCameraForAllVehicles")


class EventGraphFehler(ValueError):
    pass


@dataclass
class Knoten:
    typ: str
    daten: dict
    index: int

    @property
    def ausgaenge(self) -> Dict[str, int]:
        return dict(zip(self.daten.get("outputnames", []), self.daten.get("outputids", [])))

    @property
    def eingaenge(self) -> Dict[str, int]:
        return dict(zip(self.daten.get("inputnames", []), self.daten.get("inputids", [])))

    @property
    def weltobjekt(self) -> Optional[str]:
        return (self.daten.get("values") or {}).get("worldobjectid")


@dataclass
class EventGraph:
    inhalt: dict
    kanten: Dict[int, List[int]]              # Ausgangs-ID → Eingangs-IDs
    knoten: List[Knoten] = field(default_factory=list)

    @classmethod
    def lesen(cls, fragmente: List[bytes]) -> "EventGraph":
        p = next((f for f in fragmente if f[:4] == b"!pkg"), None)
        if p is None:
            raise EventGraphFehler("EventGraph without pkg fragment")
        inhalt = pkg.lesen(p).inhalt()
        roh = next((f for f in fragmente if f[:4] != b"!pkg"), b"")
        kanten = kanten_lesen(roh, inhalt)
        g = cls(inhalt, kanten)
        for fa in inhalt.get("factories", []):
            for n in fa.get("nodes", []):
                g.knoten.append(Knoten(fa["name"], n, len(g.knoten)))
        return g

    def datenwerte(self) -> Dict[int, object]:
        return dict(zip(self.inhalt.get("varids", []), self.inhalt.get("varvalues", [])))

    def knoten_je_eingang(self) -> Dict[int, Knoten]:
        return {i: k for k in self.knoten for i in k.daten.get("inputids", [])}

    def nachfolger(self, k: Knoten) -> List[Knoten]:
        je = self.knoten_je_eingang()
        return [je[i] for o in k.daten.get("outputids", []) for i in self.kanten.get(o, []) if i in je]

    def kamera_name(self, k: Knoten, werte=None) -> Optional[str]:
        werte = self.datenwerte() if werte is None else werte
        for name, did in zip(k.daten.get("datanames", []), k.daten.get("dataids", [])):
            if name == "Camera name":
                v = werte.get(did)
                return v if isinstance(v, str) else None
        return None

    def kameras_je_weltobjekt(self, tiefe: int = 12) -> Dict[str, List[str]]:
        """Weltobjekt-ID (z. B. Auslösezone) → Kameranamen, die seine Ausgänge über bis zu ``tiefe`` Knoten schalten.
        Andere Weltobjekte unterbrechen den Weg (sie sind eigene Auslöser)."""
        je = self.knoten_je_eingang()
        werte = self.datenwerte()
        out: Dict[str, List[str]] = {}
        for k in self.knoten:
            woid = k.weltobjekt
            if not woid or not k.daten.get("outputids"):
                continue
            gefunden: List[str] = []
            gesehen: Set[int] = {k.index}
            stapel = [(o, 0) for o in k.daten["outputids"]]
            while stapel:
                o, d = stapel.pop()
                for i in self.kanten.get(o, []):
                    n = je.get(i)
                    if n is None or n.index in gesehen or d >= tiefe:
                        continue
                    gesehen.add(n.index)
                    if n.typ in KAMERA_KNOTEN:
                        name = self.kamera_name(n, werte)
                        if name and name not in gefunden:
                            gefunden.append(name)
                    if n.typ == "WorldObjectNode":
                        continue
                    stapel += [(oo, d + 1) for oo in n.daten.get("outputids", [])]
            if gefunden:
                out.setdefault(woid, [])
                out[woid] += [g for g in gefunden if g not in out[woid]]
        return out


def kanten_lesen(roh: bytes, inhalt: dict) -> Dict[int, List[int]]:
    """Kantentabelle prüfen und lesen (lückenlos, Eingänge im Bereich)."""
    ausgaenge = sorted(o for fa in inhalt.get("factories", []) for n in fa.get("nodes", []) for o in n.get("outputids", []))
    if not roh:
        if ausgaenge:
            raise EventGraphFehler("Edge table missing")
        return {}
    if len(roh) % 4:
        raise EventGraphFehler("Edge table is not a multiple of 4 bytes")
    w = struct.unpack("<%dI" % (len(roh) // 4), roh)
    kanten, pos = {}, 0
    for o in ausgaenge:
        if o != pos or o >= len(w):
            raise EventGraphFehler("Edge table has a gap at output %d (expected %d)" % (o, pos))
        n = w[o]
        ziel = list(w[o + 1:o + 1 + n])
        if len(ziel) != n or any(i >= inhalt.get("inputcount", 0) for i in ziel):
            raise EventGraphFehler("Edge table entry %d is invalid" % o)
        kanten[o] = ziel
        pos = o + 1 + n
    if pos != len(w):
        raise EventGraphFehler("Edge table has %d words left over" % (len(w) - pos))
    return kanten


def kanten_schreiben(kanten: Dict[int, List[int]]) -> bytes:
    """Kantentabelle in der Reihenfolge der Ausgangs-IDs (die IDs müssen zu den Positionen passen)."""
    w: List[int] = []
    for o in sorted(kanten):
        if o != len(w):
            raise EventGraphFehler("Output %d does not match position %d" % (o, len(w)))
        w.append(len(kanten[o]))
        w.extend(kanten[o])
    return struct.pack("<%dI" % len(w), *w)


def aus_archiv(archiv, code: str) -> Optional[EventGraph]:
    """EventGraph der gemeinsamen Welt (``<code>_common.wep?event_graph``) oder None."""
    for r in archiv.resources:
        if r.identifier.endswith("/%s_common.wep?event_graph" % code):
            return EventGraph.lesen([fr.read() for fr in r.fragments])
    return None
