"""Schnellaustausch: neue Version einer schon installierten Strecke direkt ins Spiel schreiben.

Der Track Installer (EGO GameDatabases Editor) aktiviert einen Streckencode einmalig in der
Spieldatenbank und legt den Zustand in ``f1_db_editor_invitational_slots.json`` ab. Für jede
weitere Version derselben Strecke (gleicher ``track_code``) ändert er nur vier Dinge – genau die
schreibt dieses Modul selbst:

1. Slot-ERP ``invitational_image_N.erp`` = Strecken-ERP des Pakets (1:1 kopiert),
2. ``mipmaps/`` → Kopierziel des Manifests (nur neue/geänderte Dateien),
3. ERS-Werte je Code in den ``VTF`` der 2026er-Teams (``ers_backup_paths`` der Statusdatei),
4. Minimap-Eckpunkte/Drehung in ``minimap.uidb`` (``ui_backup_paths``).

Danach wird die Statusdatei des Installers fortgeschrieben (Paketname, ERS, Minimap, kopierte
Dateien), damit dessen Deinstallation weiterhin alles zurücksetzt. Vorher werden alle Dateien,
die sich ändern, in einen Sicherungsordner außerhalb des Spiels kopiert (``zuruecksetzen``).

Datenbank-Aktivierung: Ist der Mod im EGO-Editor deaktiviert (Original-Datenbank im Spiel), stellt
das Werkzeug eine früher gemerkte Aktivierung selbst wieder her (``datenbank_abbild``); eine aktivierte
Datenbank wird bei jedem Plan automatisch gemerkt.

Nicht abgedeckt (dafür einmalig den Track Installer nutzen): Erstinstallation eines Codes und seine
erste Datenbank-Aktivierung, ``track_overrides``, Flaggen- und Vorschaubilder (gemeinsamer UI-Slot).
"""

import datetime
import json
import os
import shutil
import struct
import subprocess
import sys
import zipfile
import zlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import bxml, datenbank_abbild, erp, pkg
from .paket import ModPaket

ASSET = "2025_asset_groups"
SLOT_ORDNER = os.path.join(ASSET, "ui_package", "texture_arrays", "invitational_events", "event_images")
STATUS = "f1_db_editor_invitational_slots.json"
MINIMAP_UIDB = "eaid://ui_package/databases/minimap.uidb"
ECKEN = ("bottom_left", "bottom_right", "top_left", "top_right")
MINIMAP_SKALARE = ("rotation", "dimensions", "padding", "zoom_factor")
SPIEL_EXE = "F1_25.exe"


class InjektionsFehler(RuntimeError):
    pass


@dataclass
class Plan:
    paket: str
    spielordner: str
    code: str
    slot_datei: str
    slot_neu: bool = False
    mipmaps: List[tuple] = field(default_factory=list)       # (zip-Name, Zielpfad, existierte)
    mipmaps_gleich: int = 0
    ers: Dict[str, object] = field(default_factory=dict)      # nur geänderte Schlüssel
    ers_dateien: List[str] = field(default_factory=list)
    minimap: Dict[str, object] = field(default_factory=dict)  # geänderte Werte (Pfad -> Wert)
    minimap_dateien: List[str] = field(default_factory=list)
    hinweise: List[str] = field(default_factory=list)
    datenbank: Optional[object] = None                         # anzuwendendes datenbank_abbild.Abbild
    datenbank_text: str = ""

    @property
    def leer(self) -> bool:
        return not (self.slot_neu or self.mipmaps or self.ers or self.minimap or self.datenbank)

    def text(self) -> str:
        z = ["Paket:   %s" % self.paket, "Strecke: %s → %s" % (self.code, self.slot_datei)]
        z.append("  Slot-ERP:  %s" % ("ersetzen" if self.slot_neu else "unverändert (gleiche Größe und CRC32)"))
        z.append("  Mipmaps:   %d kopieren, %d unverändert" % (len(self.mipmaps), self.mipmaps_gleich))
        z.append("  ERS:       %s" % (", ".join("%s=%s" % kv for kv in self.ers.items()) or "unverändert"))
        if self.ers:
            z.append("             in %d Dateien" % len(self.ers_dateien))
        ecken = sum(1 for k in self.minimap if "." in k)
        teile = sorted(k for k in self.minimap if "." not in k) + (["%d Eckkoordinaten" % ecken] if ecken else [])
        z.append("  Minimap:   %s" % (", ".join(teile) or "unverändert"))
        if self.minimap:
            z.append("             in %d Dateien" % len(self.minimap_dateien))
        if self.datenbank_text:
            z.append("  Datenbank: %s" % self.datenbank_text)
        z += ["  Hinweis:   " + h for h in self.hinweise]
        return "\n".join(z)


# ----------------------------------------------------------------------------- Hilfen
def spiel_laeuft() -> bool:
    if sys.platform != "win32":
        return False
    try:
        # ohne Konsolenfenster: aus der Fenster-EXE des Mapeditors blitzte sonst bei jeder Abfrage eine CMD auf
        aus = subprocess.run(["tasklist", "/FI", "IMAGENAME eq %s" % SPIEL_EXE, "/NH"], capture_output=True,
                             timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout or b""
    except (OSError, subprocess.SubprocessError):
        return False
    return SPIEL_EXE.lower().encode("ascii") in aus.lower()


def _crc32(pfad: str) -> int:
    crc = 0
    with open(pfad, "rb") as f:
        for block in iter(lambda: f.read(1 << 24), b""):
            crc = zlib.crc32(block, crc)
    return crc


def _spielpfad(spielordner: str, rel: str) -> str:
    return os.path.join(spielordner, *rel.replace("\\", "/").split("/"))


def status_lesen(spielordner: str) -> dict:
    pfad = os.path.join(spielordner, SLOT_ORDNER, STATUS)
    if not os.path.exists(pfad):
        raise InjektionsFehler("Statusdatei des Track Installers fehlt: %s" % pfad)
    with open(pfad, encoding="utf-8") as f:
        return json.load(f)


def status_text(status: dict) -> str:
    """Wie der Installer (serde_json pretty: 2 Leerzeichen, ohne Zeilenende am Schluss)."""
    return json.dumps(status, indent=2, ensure_ascii=False)


def _ers_text(wert) -> str:
    if isinstance(wert, bool):
        return str(wert).lower()
    if isinstance(wert, int):
        return str(wert)
    return repr(float(wert))


def _f32(x) -> float:
    return struct.unpack("<f", struct.pack("<f", float(x)))[0]


def _minimap_werte(mm: dict) -> Dict[str, float]:
    """Flache Sicht: rotation, …, textures.bottom_left.x, textures_om_xm.top_right.y, …"""
    w = {k: float(mm[k]) for k in MINIMAP_SKALARE if k in mm}
    for gruppe in ("textures", "textures_om_xm"):
        ecken = ((mm.get(gruppe) or {}).get("corners") or {})
        for e in ECKEN:
            if e in ecken:
                w["%s.%s.x" % (gruppe, e)] = float(ecken[e][0])
                w["%s.%s.y" % (gruppe, e)] = float(ecken[e][1])
    return w


# ----------------------------------------------------------------------------- Plan
def planen(paket_zip: str, spielordner: str, genau: bool = False, ablage: Optional[str] = None) -> Plan:
    """Vergleicht Paket und Spielzustand; schreibt nichts ins Spiel.

    Mit ``ablage`` wird außerdem die Spieldatenbank geprüft: Eine aktivierte Datenbank wird dort gemerkt,
    eine deaktivierte aus dem passenden Abbild zum Aktivieren vorgemerkt."""
    status = status_lesen(spielordner)
    with ModPaket(paket_zip) as p:
        code = p.track_code
        inst = (status.get("installations") or {}).get(code)
        if inst is None:
            raise InjektionsFehler(
                "Streckencode %r ist laut Statusdatei nicht installiert (installiert: %s). Die erste Installation "
                "eines Codes macht der Track Installer (Datenbank-Aktivierung); danach geht der Schnellaustausch."
                % (code, ", ".join(sorted(status.get("installations") or {})) or "keine"))
        slot = os.path.join(spielordner, SLOT_ORDNER, inst["slot_file"])
        plan = Plan(os.path.abspath(paket_zip), spielordner, code, slot)
        info = p.zip.getinfo(p.erp_name)
        plan.slot_neu = not (os.path.exists(slot) and os.path.getsize(slot) == info.file_size
                             and _crc32(slot) == info.CRC)
        # Mipmaps
        for kopie in p.manifest.get("copy_directories", []):
            quelle, ziel = kopie.get("source", ""), kopie.get("destination", "")
            for i in p.zip.infolist():
                if i.is_dir() or not i.filename.startswith(quelle):
                    continue
                pfad = _spielpfad(spielordner, ziel + i.filename[len(quelle):])
                da = os.path.exists(pfad)
                gleich = da and os.path.getsize(pfad) == i.file_size and (not genau or _crc32(pfad) == i.CRC)
                if gleich:
                    plan.mipmaps_gleich += 1
                else:
                    plan.mipmaps.append((i.filename, pfad, da))
        # ERS
        alt = {e["key"]: e["value"] for e in inst.get("ers") or []}
        for k, v in (p.manifest.get("ers") or {}).items():
            if k not in alt or _ers_text(v) != alt[k]:
                try:
                    if k in alt and float(alt[k]) == float(v):
                        continue
                except ValueError:
                    pass
                plan.ers[k] = v
        if plan.ers:
            plan.ers_dateien = [_spielpfad(spielordner, r) for r in status.get("ers_backup_paths") or []]
        # Minimap
        if p.minimap:
            neu = _minimap_werte(p.minimap)
            vorher = _minimap_werte(((inst.get("ui") or {}).get("minimap")) or {})
            plan.minimap = {k: v for k, v in neu.items() if k not in vorher or _f32(vorher[k]) != _f32(v)}
            if plan.minimap:
                plan.minimap_dateien = [_spielpfad(spielordner, r) for r in status.get("ui_backup_paths") or []]
        if p.images and p.images.get("resources") != ((inst.get("ui") or {}).get("images") or {}).get("resources"):
            plan.hinweise.append("Bildverweise (images.json) geändert – Flaggen/Vorschaubilder ändert nur der Track Installer")
        if (p.manifest.get("activation") or {}) != (inst.get("activation") or {}):
            plan.hinweise.append("activation im Manifest geändert – Datenbank-Aktivierung ändert nur der Track Installer")
        if p.manifest.get("track_name") != inst.get("track_name"):
            plan.hinweise.append("track_name geändert (%r → %r) – Anzeige im Spiel ändert nur der Track Installer"
                                 % (inst.get("track_name"), p.manifest.get("track_name")))
    if ablage is not None:
        _datenbank_planen(plan, ablage)
    return plan


def _datenbank_planen(plan: Plan, ablage: str) -> None:
    try:
        z = datenbank_abbild.zustand(plan.spielordner, ablage, plan.code)
    except OSError as e:
        plan.datenbank_text = "nicht lesbar (%s)" % e
        return
    plan.datenbank_text = z.text(plan.code)
    if z.art == "aktivierbar":
        plan.datenbank = z.abbild
    elif z.art in ("ohne_abbild", "ohne_code"):
        plan.hinweise.append("Die Strecke ist im Spiel erst nach der Datenbank-Aktivierung auswählbar. Einmalig im "
                             "EGO-Editor „Activate mod“ klicken; danach merkt sich das Werkzeug die Aktivierung "
                             "und stellt sie auch bei deaktiviertem Mod selbst wieder her.")
    elif z.art == "unbekannt":
        plan.hinweise.append("Spieldatenbank weder Original noch gemerkte Aktivierung (Spiel-Update?) – einmal im "
                             "EGO-Editor „Activate mod“ klicken, dann wird die neue Aktivierung gemerkt.")
    elif z.art == "aktiv" and z.code_aktiv(plan.code) is False:
        plan.hinweise.append("%s ist in der aktiven Datenbank nicht aktiviert – im EGO-Editor unter „Database "
                             "activation“ einschalten und „Activate mod“ klicken." % plan.code)


# ----------------------------------------------------------------------------- Ändern
def _vtf_patchen(archiv, code: str, ers: dict) -> int:
    n = 0
    for r in archiv:
        if r.resource_type != "VTF":
            continue
        doc = bxml.read_resource(r)
        geaendert = False
        for ts in doc.iter("TrackSettings"):
            t = next((e for e in ts.elements if e.name == "Track"), None)
            if t is None or t.get("value") != code:
                continue
            for k, v in ers.items():
                e = next((e for e in ts.elements if e.name == k), None)
                if e is None:
                    raise InjektionsFehler("ERS-Schlüssel %s fehlt in %s (Track %s)" % (k, r.identifier, code))
                if e.get("value") != _ers_text(v):
                    e.set("value", _ers_text(v))
                    geaendert = True
        if geaendert:
            bxml.write_resource(r, doc)
            n += 1
    return n


def _pkg_eintrag(p: pkg.Paket, block, name: str):
    """Kind ``name`` eines UIDB-Wörterbuchs (Contents) als Block."""
    contents = p.feld(block, "Contents").wert.ziel
    if contents.kennung == b"!idi":
        return p.feld(contents, name).wert.ziel
    for v in contents.werte:                                   # Array: Liste von Einträgen
        if p.wert(p.feld(v.ziel, "Name").wert) == name:
            return v.ziel
    raise InjektionsFehler("UIDB-Eintrag %s fehlt" % name)


def _pkg_float(p: pkg.Paket, variable, wert: float) -> bool:
    f = p.feld(variable, "Value")
    if _f32(p.wert(f.wert)) == _f32(wert):
        return False
    t = p.tabelle(f.wert)
    if t.datentyp != b"fp32":
        raise InjektionsFehler("Minimap-Wert ist kein float32")
    f.wert = p.neuer_wert(f.wert, struct.pack("<f", float(wert)))
    return True


def _strecke_finden(p: pkg.Paket, code: str):
    for b in p.bloecke:
        if b.kennung != b"!idi":
            continue
        try:
            if p.wert(p.feld(b, "Name").wert) == code and p.wert(p.feld(b, "DBType").wert) == "Dictionary":
                inhalt = p.feld(b, "Contents").wert.ziel
                if inhalt is not None and inhalt.kennung == b"!idi" and any(
                        p.wert(x.name) == "textures" for x in inhalt.felder):
                    return b
        except pkg.PkgFormatError:
            continue
    return None


def _minimap_patchen(archiv, code: str, werte: Dict[str, float]) -> int:
    r = archiv.find(MINIMAP_UIDB)
    if r is None:
        return 0
    p = pkg.lesen(r.fragments[0].read())
    strecke = _strecke_finden(p, code)
    if strecke is None:
        raise InjektionsFehler("Strecke %s fehlt in %s" % (code, MINIMAP_UIDB))
    geaendert = False
    for schluessel, wert in werte.items():
        teile = schluessel.split(".")
        if len(teile) == 1:
            geaendert |= _pkg_float(p, _pkg_eintrag(p, strecke, teile[0]), wert)
        else:
            gruppe, ecke, achse = teile
            arr = _pkg_eintrag(p, _pkg_eintrag(p, strecke, gruppe), ecke)
            geaendert |= _pkg_float(p, _pkg_eintrag(p, arr, achse + "_coordinate"), wert)
    if not geaendert:
        return 0
    r.set_fragment_data(0, pkg.schreiben(p))
    r.update_hash()
    return 1


def _erp_umschreiben(pfad: str, aendern) -> int:
    """ERP öffnen, ``aendern(archiv)`` anwenden, bei Änderungen über eine Temp-Datei ersetzen."""
    tmp = pfad + ".injektion_tmp"
    with erp.open_erp(pfad) as a:
        n = aendern(a)
        if n:
            a.write(tmp, overwrite=True)
    if n:
        os.replace(tmp, pfad)
    return n


def _sichern(quelle: str, sicherung: str, spielordner: str, liste: list) -> None:
    rel = os.path.relpath(quelle, spielordner)
    ziel = os.path.join(sicherung, "dateien", rel)
    os.makedirs(os.path.dirname(ziel), exist_ok=True)
    shutil.copy2(quelle, ziel)
    liste.append(rel)


def ausfuehren(plan: Plan, sicherung_basis: str, melde=print, spielpruefung=True) -> dict:
    """Plan anwenden. Sichert vorher jede zu ändernde Datei nach ``sicherung_basis/<zeit>/``."""
    if plan.leer:
        melde("Nichts zu tun – das Paket ist schon vollständig im Spiel.")
        return {"geaendert": False}
    if spielpruefung and spiel_laeuft():
        raise InjektionsFehler("F1 25 läuft – bitte erst das Spiel beenden.")
    sp = plan.spielordner
    db = datenbank_abbild.db_pfad(sp)
    if plan.datenbank is not None and datenbank_abbild.sha256(db) != plan.datenbank.original_sha:
        raise InjektionsFehler("Spieldatenbank hat sich seit dem Plan geändert – bitte neu planen.")
    zeit = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    sicherung = os.path.join(sicherung_basis, zeit)
    os.makedirs(sicherung, exist_ok=True)
    protokoll = {"zeit": zeit, "paket": plan.paket, "code": plan.code, "spielordner": sp,
                 "gesichert": [], "neu_angelegt": [], "fertig": False}
    status_pfad = os.path.join(sp, SLOT_ORDNER, STATUS)

    def protokoll_schreiben():
        with open(os.path.join(sicherung, "protokoll.json"), "w", encoding="utf-8") as f:
            json.dump(protokoll, f, ensure_ascii=False, indent=1)

    dateien = bool(plan.slot_neu or plan.mipmaps or plan.ers or plan.minimap)
    # 1) Sicherungen
    melde("Sichere Dateien nach %s …" % sicherung)
    if dateien:
        _sichern(status_pfad, sicherung, sp, protokoll["gesichert"])
    if plan.slot_neu and os.path.exists(plan.slot_datei):
        _sichern(plan.slot_datei, sicherung, sp, protokoll["gesichert"])
    ers_ziel = [d for d in plan.ers_dateien if os.path.exists(d)]
    mm_ziel = [d for d in plan.minimap_dateien if os.path.exists(d)]
    for d in sorted(set(ers_ziel + mm_ziel)):
        _sichern(d, sicherung, sp, protokoll["gesichert"])
    for _, ziel, da in plan.mipmaps:
        if da:
            _sichern(ziel, sicherung, sp, protokoll["gesichert"])
        else:
            protokoll["neu_angelegt"].append(os.path.relpath(ziel, sp))
    if plan.datenbank is not None:
        _sichern(db, sicherung, sp, protokoll["gesichert"])
        if not os.path.exists(datenbank_abbild.bak_pfad(sp)):
            protokoll["neu_angelegt"].append(os.path.relpath(datenbank_abbild.bak_pfad(sp), sp))
    protokoll_schreiben()
    if dateien:
        _dateien_schreiben(plan, protokoll, ers_ziel, mm_ziel, status_pfad, melde)
    # 7) Datenbank-Aktivierung aus dem gemerkten Abbild (Mod im EGO-Editor deaktiviert)
    if plan.datenbank is not None:
        try:
            datenbank_abbild.aktivieren(sp, plan.datenbank, melde)
        except OSError as e:
            raise InjektionsFehler("Datenbank-Aktivierung fehlgeschlagen: %s" % e)
        protokoll["datenbank"] = plan.datenbank.ordner
    protokoll["fertig"] = True
    protokoll_schreiben()
    melde("Fertig. Sicherung: %s" % sicherung)
    return {"geaendert": True, "sicherung": sicherung, "protokoll": protokoll}


def _dateien_schreiben(plan: Plan, protokoll: dict, ers_ziel, mm_ziel, status_pfad: str, melde) -> None:
    """Schritte 2–6: Slot-ERP, Mipmaps, ERS, Minimap, Statusdatei (Sicherungen liegen schon vor)."""
    sp = plan.spielordner
    status = status_lesen(sp)
    inst = status["installations"][plan.code]
    with zipfile.ZipFile(plan.paket) as z:
        manifest = json.loads(z.read("manifest.json").decode("utf-8"))
        # 2) Slot-ERP
        if plan.slot_neu:
            melde("Slot-ERP ersetzen (%s) …" % os.path.basename(plan.slot_datei))
            info = z.getinfo(manifest["erp"])
            tmp = plan.slot_datei + ".injektion_tmp"
            with z.open(info) as q, open(tmp, "wb") as f:
                shutil.copyfileobj(q, f, 1 << 24)
            if os.path.getsize(tmp) != info.file_size or _crc32(tmp) != info.CRC:
                os.remove(tmp)
                raise InjektionsFehler("Slot-ERP fehlerhaft kopiert (Größe/CRC32)")
            os.replace(tmp, plan.slot_datei)
        # 3) Mipmaps
        if plan.mipmaps:
            melde("Mipmaps kopieren (%d) …" % len(plan.mipmaps))
        bekannt = {c["path"].replace("\\", "/") for c in inst.get("copied_files") or []}
        for name, ziel, da in plan.mipmaps:
            os.makedirs(os.path.dirname(ziel), exist_ok=True)
            with z.open(name) as q, open(ziel + ".injektion_tmp", "wb") as f:
                shutil.copyfileobj(q, f, 1 << 22)
            os.replace(ziel + ".injektion_tmp", ziel)          # nie in eine bestehende Datei hineinschreiben
            rel = os.path.relpath(ziel, sp).replace("\\", "/")
            if rel not in bekannt:
                kopf, datei = rel.rsplit("/", 1)
                inst.setdefault("copied_files", []).append({"path": kopf + "\\" + datei, "had_original": da})
    # 4) ERS
    if plan.ers:
        melde("ERS-Werte eintragen (%d Dateien) …" % len(ers_ziel))
        n = sum(_erp_umschreiben(d, lambda a: _vtf_patchen(a, plan.code, plan.ers)) for d in ers_ziel)
        protokoll["ers_dateien_geaendert"] = n
        werte = {e["key"]: e for e in inst.get("ers") or []}
        for k, v in plan.ers.items():
            if k in werte:
                werte[k]["value"] = _ers_text(v)
            else:
                inst.setdefault("ers", []).append({"key": k, "value": _ers_text(v)})
    # 5) Minimap
    if plan.minimap:
        melde("Minimap-Werte eintragen (%d Dateien prüfen) …" % len(mm_ziel))
        n = sum(_erp_umschreiben(d, lambda a: _minimap_patchen(a, plan.code, plan.minimap)) for d in mm_ziel)
        protokoll["minimap_dateien_geaendert"] = n
        if n == 0:
            raise InjektionsFehler("minimap.uidb mit Strecke %s in keiner UI-Datei gefunden" % plan.code)
        with zipfile.ZipFile(plan.paket) as z:
            mm = json.loads(z.read((manifest.get("ui") or {})["minimap_file"]).decode("utf-8"))
        inst.setdefault("ui", {})["minimap"] = mm
    # 6) Statusdatei
    inst["archive_file_name"] = os.path.basename(plan.paket)
    tmp = status_pfad + ".injektion_tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(status_text(status))
    os.replace(tmp, status_pfad)


def zuruecksetzen(sicherung: str, melde=print, spielpruefung=True) -> dict:
    """Stand vor einer Injektion wiederherstellen (gesicherte Dateien zurück, neue Mipmaps löschen)."""
    with open(os.path.join(sicherung, "protokoll.json"), encoding="utf-8") as f:
        prot = json.load(f)
    if spielpruefung and spiel_laeuft():
        raise InjektionsFehler("F1 25 läuft – bitte erst das Spiel beenden.")
    sp = prot["spielordner"]
    for rel in prot["gesichert"]:
        q = os.path.join(sicherung, "dateien", rel)
        ziel = os.path.join(sp, rel)
        tmp = ziel + ".injektion_tmp"
        shutil.copy2(q, tmp)
        os.replace(tmp, ziel)
    weg = 0
    for rel in prot["neu_angelegt"]:
        pfad = os.path.join(sp, rel)
        if os.path.exists(pfad):
            os.remove(pfad)
            weg += 1
    melde("Zurückgesetzt: %d Dateien wiederhergestellt, %d neue Mipmaps entfernt" % (len(prot["gesichert"]), weg))
    return {"wiederhergestellt": len(prot["gesichert"]), "entfernt": weg}


def sicherungen(basis: str) -> List[str]:
    if not os.path.isdir(basis):
        return []
    return sorted(os.path.join(basis, d) for d in os.listdir(basis)
                  if os.path.exists(os.path.join(basis, d, "protokoll.json")))


def alte_sicherungen_loeschen(basis: str, behalten: int = 3) -> int:
    alt = sicherungen(basis)[:-behalten] if behalten > 0 else sicherungen(basis)
    for d in alt:
        shutil.rmtree(d, ignore_errors=True)
    return len(alt)
