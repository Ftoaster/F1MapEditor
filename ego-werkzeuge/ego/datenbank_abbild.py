"""Datenbank-Aktivierung merken und ohne EGO-Editor wiederherstellen.

Der EGO GameDatabases Editor aktiviert installierte Strecken mit „Activate mod“: Er legt die
Original-Datenbank als ``gamedatabases.2025.bak`` ab und schreibt eine geänderte
``gamedatabases.2025.bin``. „Deactivate mod“ kopiert die ``.bak`` zurück – danach sind die Strecken
zwar noch installiert (Slot-ERPs, ERS, Minimap), im Spiel aber nicht mehr auswählbar.

Welche Datenbankeinträge eine Aktivierung ändert, lässt sich nicht selbst berechnen: Die Schlüssel
der Datenbank sind Hashes, ihre Namen stehen nur verschlüsselt im Editor (dessen Lizenz verbietet
Reverse Engineering). Dieses Modul nimmt deshalb die fertige Datenbank als **Abbild**:

* ``erfassen``: Ist die Datenbank aktiviert (``.bak`` vorhanden und verschieden), wird sie samt
  Prüfsumme des Originals außerhalb des Spiels abgelegt (``<ablage>/<sha-original>/<sha-aktiviert>/``).
* ``zustand``: Vergleicht die Datenbank im Spiel mit den Abbildern (aktiv / mit Abbild aktivierbar /
  ohne Abbild / Original unbekannt, z. B. nach einem Spiel-Update).
* ``aktivieren`` (über ``injektion.ausfuehren``): legt wie der Editor die ``.bak`` an und kopiert das
  Abbild als ``.bin``. Nur, wenn die Datenbank im Spiel bitgleich dem Original des Abbilds ist.

Welche Streckencodes ein Abbild aktiviert, wird aus der Statusdatei des Installers (Code → Slot) und
den neu hinzugekommenen Verweisen ``invitational_image_N`` in der Datenbank bestimmt.
Details: ``docs/injektion.md``.
"""

import datetime
import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from typing import List, Optional

from . import spieldatenbank

DB_ORDNER = os.path.join("game_package", "gamedatabases")
DB_DATEI = "gamedatabases.2025.bin"
DB_SICHERUNG = "gamedatabases.2025.bak"          # Name, den der EGO-Editor verwendet
INFO = "info.json"
ABBILD = "aktiviert.bin"


def standard_ablage() -> str:
    """Abbilder je Benutzer (gemeinsam für Mapeditor-Projekte und Kommandozeile)."""
    basis = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(basis, "ego-werkzeuge", "datenbank_aktivierung")


def db_pfad(spielordner: str) -> str:
    return os.path.join(spielordner, DB_ORDNER, DB_DATEI)


def bak_pfad(spielordner: str) -> str:
    return os.path.join(spielordner, DB_ORDNER, DB_SICHERUNG)


def sha256(pfad: str) -> str:
    h = hashlib.sha256()
    with open(pfad, "rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


@dataclass
class Abbild:
    ordner: str
    original_sha: str
    aktiviert_sha: str
    codes: List[str]
    slots: dict
    zeit: str
    erkannt: bool = True                 # Codes aus Datenbankverweisen erkannt (sonst: alle installierten)

    @property
    def datei(self) -> str:
        return os.path.join(self.ordner, ABBILD)

    @classmethod
    def laden(cls, ordner: str) -> Optional["Abbild"]:
        try:
            with open(os.path.join(ordner, INFO), encoding="utf-8") as f:
                i = json.load(f)
        except (OSError, ValueError):
            return None
        if not os.path.exists(os.path.join(ordner, ABBILD)):
            return None
        return cls(ordner, i["original_sha256"], i["aktiviert_sha256"], list(i.get("codes") or []),
                   dict(i.get("slots") or {}), i.get("zeit", ""), bool(i.get("codes_erkannt", True)))


@dataclass
class Zustand:
    """Datenbank im Spiel gegenüber den Abbildern."""
    art: str                              # aktiv | aktivierbar | ohne_code | ohne_abbild | unbekannt | fehlt
    db_sha: str = ""
    bak_vorhanden: bool = False
    abbild: Optional[Abbild] = None       # aktives bzw. anzuwendendes Abbild
    codes_aktiv: List[str] = field(default_factory=list)
    neu_erfasst: bool = False

    def code_aktiv(self, code: str) -> Optional[bool]:
        """True/False, wenn bekannt; None, wenn die Datenbank fremd aktiviert ist und die Codes unklar sind."""
        if self.art != "aktiv":
            return False
        if self.abbild is not None and not self.abbild.erkannt:
            return None
        return code in self.codes_aktiv

    def text(self, code: Optional[str] = None) -> str:
        if self.art == "fehlt":
            return "Spieldatenbank nicht gefunden"
        if self.art == "aktiv":
            liste = ", ".join(self.codes_aktiv) or "unbekannt"
            t = "aktiviert (%s)%s" % (liste, " – Aktivierung neu gemerkt" if self.neu_erfasst else "")
            if code and self.code_aktiv(code) is False:
                t += "; %s ist darin NICHT aktiviert" % code
            return t
        if self.art == "aktivierbar":
            return "nicht aktiviert (Mod deaktiviert) – wird aus der gemerkten Aktivierung vom %s wiederhergestellt " \
                   "(%s)" % (self.abbild.zeit, ", ".join(self.abbild.codes))
        if self.art == "ohne_code":
            return "nicht aktiviert; die gemerkte Aktivierung enthält %s nicht (nur %s)" % (
                code or "diese Strecke", ", ".join(self.codes_aktiv) or "–")
        if self.art == "unbekannt":
            return "unbekannter Stand (weder Original noch gemerkte Aktivierung, z. B. nach einem Spiel-Update)"
        return "nicht aktiviert und keine gemerkte Aktivierung"


# ----------------------------------------------------------------------------- Abbilder
def abbilder(ablage: str) -> List[Abbild]:
    """Alle Abbilder, neueste zuerst."""
    out = []
    if not os.path.isdir(ablage):
        return out
    for o in os.listdir(ablage):
        po = os.path.join(ablage, o)
        if not os.path.isdir(po):
            continue
        for a in os.listdir(po):
            ab = Abbild.laden(os.path.join(po, a))
            if ab is not None:
                out.append(ab)
    return sorted(out, key=lambda a: a.zeit, reverse=True)


def _status_slots(spielordner: str) -> dict:
    """Code → Slotname aus der Statusdatei des Installers (leer, wenn keine da ist)."""
    from .injektion import SLOT_ORDNER, STATUS
    pfad = os.path.join(spielordner, SLOT_ORDNER, STATUS)
    try:
        with open(pfad, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        return {}
    return {c: i.get("slot_name") or os.path.splitext(i.get("slot_file", ""))[0]
            for c, i in (st.get("installations") or {}).items()}


def codes_ermitteln(original: str, aktiviert: str, slots: dict):
    """(Codes, erkannt): Codes, deren Slot ``invitational_image_N`` als neuer Datenbankwert auftaucht."""
    try:
        alt = {(e.h, e.v, e.wert) for e in spieldatenbank.lesen(original)}
        neu = [e.wert for e in spieldatenbank.lesen(aktiviert) if (e.h, e.v, e.wert) not in alt]
    except (OSError, ValueError):
        return sorted(slots), False
    codes = []
    for code, slot in slots.items():
        name = (slot or "").encode("ascii", "ignore")
        if name and any(name in w and not w[w.find(name) + len(name):][:1].isdigit() for w in neu):
            codes.append(code)
    if codes:
        return sorted(codes), True
    return sorted(slots), False


def erfassen(spielordner: str, ablage: str, behalten: int = 3) -> Optional[Abbild]:
    """Aktivierte Datenbank (``.bak`` vorhanden, ``.bin`` verschieden) als Abbild ablegen.

    Schreibt nur in ``ablage``. Gibt das (neue oder schon vorhandene) Abbild zurück, sonst None."""
    db, bak = db_pfad(spielordner), bak_pfad(spielordner)
    if not (os.path.exists(db) and os.path.exists(bak)):
        return None
    s_db, s_bak = sha256(db), sha256(bak)
    if s_db == s_bak:
        return None
    ziel = os.path.join(ablage, s_bak[:16], s_db[:16])
    vorhanden = Abbild.laden(ziel)
    if vorhanden is not None and vorhanden.aktiviert_sha == s_db:
        return vorhanden
    slots = _status_slots(spielordner)
    codes, erkannt = codes_ermitteln(bak, db, slots)
    tmp = ziel + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    shutil.copy2(db, os.path.join(tmp, ABBILD))
    if sha256(os.path.join(tmp, ABBILD)) != s_db:
        shutil.rmtree(tmp, ignore_errors=True)
        raise OSError("Datenbank beim Merken fehlerhaft kopiert")
    zeit = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(os.path.join(tmp, INFO), "w", encoding="utf-8") as f:
        json.dump({"original_sha256": s_bak, "aktiviert_sha256": s_db, "codes": codes, "codes_erkannt": erkannt,
                   "slots": {c: slots[c] for c in codes if c in slots}, "zeit": zeit,
                   "groesse": os.path.getsize(db)}, f, ensure_ascii=False, indent=1)
    shutil.rmtree(ziel, ignore_errors=True)
    os.replace(tmp, ziel)
    _aufraeumen(os.path.join(ablage, s_bak[:16]), behalten)
    return Abbild.laden(ziel)


def _aufraeumen(ordner: str, behalten: int) -> None:
    alle = sorted((a for a in (Abbild.laden(os.path.join(ordner, d)) for d in os.listdir(ordner)) if a),
                  key=lambda a: a.zeit, reverse=True)
    for a in alle[max(1, behalten):]:
        shutil.rmtree(a.ordner, ignore_errors=True)


def zustand(spielordner: str, ablage: str, code: Optional[str] = None, merken: bool = True) -> Zustand:
    """Zustand der Datenbank; eine neue Aktivierung des Editors wird dabei (``merken``) gleich abgelegt."""
    db, bak = db_pfad(spielordner), bak_pfad(spielordner)
    if not os.path.exists(db):
        return Zustand("fehlt")
    s_db = sha256(db)
    hat_bak = os.path.exists(bak)
    alle = abbilder(ablage)
    for a in alle:
        if a.aktiviert_sha == s_db:
            return Zustand("aktiv", s_db, hat_bak, a, list(a.codes))
    if hat_bak and sha256(bak) != s_db:
        neu = erfassen(spielordner, ablage) if merken else None
        if neu is not None:
            return Zustand("aktiv", s_db, True, neu, list(neu.codes), neu_erfasst=True)
        z = Zustand("aktiv", s_db, True)
        z.abbild = Abbild("", "", s_db, [], {}, "", erkannt=False)
        return z
    passend = [a for a in alle if a.original_sha == s_db]
    if not passend:
        return Zustand("ohne_abbild" if not alle else "unbekannt", s_db, hat_bak)
    if code:
        mit_code = [a for a in passend if code in a.codes]
        if not mit_code:
            return Zustand("ohne_code", s_db, hat_bak, passend[0], list(passend[0].codes))
        return Zustand("aktivierbar", s_db, hat_bak, mit_code[0], list(mit_code[0].codes))
    return Zustand("aktivierbar", s_db, hat_bak, passend[0], list(passend[0].codes))


def aktivieren(spielordner: str, abbild: Abbild, melde=print) -> dict:
    """Abbild ins Spiel schreiben (vorher ``.bak`` wie der Editor). Rückgabe: angelegte Dateien."""
    db, bak = db_pfad(spielordner), bak_pfad(spielordner)
    s_db = sha256(db)
    if s_db != abbild.original_sha:
        raise OSError("Spieldatenbank entspricht nicht dem Original des Abbilds – nichts geändert")
    neu = []
    if not os.path.exists(bak):
        shutil.copy2(db, bak + ".injektion_tmp")
        os.replace(bak + ".injektion_tmp", bak)
        neu.append(bak)
    elif sha256(bak) != abbild.original_sha:
        raise OSError("%s ist nicht das Original des Abbilds – nichts geändert" % DB_SICHERUNG)
    melde("Datenbank aktivieren (gemerkte Aktivierung vom %s: %s) …" % (abbild.zeit, ", ".join(abbild.codes)))
    shutil.copy2(abbild.datei, db + ".injektion_tmp")
    if sha256(db + ".injektion_tmp") != abbild.aktiviert_sha:
        os.remove(db + ".injektion_tmp")
        raise OSError("Abbild fehlerhaft kopiert – nichts geändert")
    os.replace(db + ".injektion_tmp", db)
    return {"neu_angelegt": neu}
