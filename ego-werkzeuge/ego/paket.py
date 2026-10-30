"""Mod-Pakete für den Track Installer (EGO GameDatabases Editor) lesen, prüfen und bauen.

Aufbau, ermittelt an den Paketen sepang.zip und portimao.zip:

    ZIP-Archiv, alle Einträge unkomprimiert (stored), Zeitstempel 1980-01-01 00:00,
    Reihenfolge der Einträge:
        manifest.json         Metadaten (siehe PFLICHTFELDER), Kopierregeln, ERS-Werte, Aktivierung
        ui/images.json        Kennungen der Menübilder ({"resources": {name: "eaid://..."}})
        ui/minimap.json       Minimap: Eckpunkte in Streckenkoordinaten, Drehung, Zoom, Kennungen
        <code>.erp            komplette Strecke in einer ERP-Datei (Name aus manifest["erp"])
        mipmaps/*.mipmaps     externe Texturstufen, alphabetisch sortiert

Der Installer kopiert die ERP unverändert in einen Invitational-Bild-Slot und die mipmaps nach
manifest["copy_directories"][i]["destination"] (siehe docs/streckenaufbau.md).
"""

import hashlib
import json
import os
import shutil
import zipfile
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Iterable, List, Optional, Tuple

from . import compression, erp, parallel

ZIP_ZEIT = (1980, 1, 1, 0, 0, 0)
KOPIER_BLOCK = 8 * 1024 * 1024
PFLICHTFELDER = ("track_code", "track_name", "author_name", "version", "erp", "ui", "copy_directories")
# Logik-Ressourcen, die jede Strecke hat (siehe docs/streckenaufbau.md)
STRECKEN_TYPEN = ("World", "WOInstances", "TRN~", "AISplineData", "TrackSpace", "TSQuadTree", "TM", "TS",
                  "TrackInfoBinary", "TrackInfoTex", "PVSObjIDRes", "PVSVisRes", "CrowdPlacement", "Spline",
                  "AIBraking", "VPTimes", "IBLSH", "DDGIRes", "LightMapLocRes")


class PaketFehler(ValueError):
    """Das Paket entspricht nicht dem erwarteten Aufbau."""


def _kopieren(quelle, ziel, laenge: Optional[int] = None) -> int:
    n = 0
    while laenge is None or n < laenge:
        block = quelle.read(KOPIER_BLOCK if laenge is None else min(KOPIER_BLOCK, laenge - n))
        if not block:
            break
        ziel.write(block)
        n += len(block)
    return n


class ModPaket:
    """Ein Mod-Paket (ZIP). Liest Manifest und UI-Dateien sofort, die ERP nur bei Bedarf."""

    def __init__(self, pfad: str):
        self.pfad = os.fspath(pfad)
        self.zip = zipfile.ZipFile(self.pfad)
        namen = set(self.zip.namelist())
        if "manifest.json" not in namen:
            raise PaketFehler("manifest.json fehlt")
        self.manifest_roh = self.zip.read("manifest.json")
        self.manifest = json.loads(self.manifest_roh.decode("utf-8"))
        ui = self.manifest.get("ui") or {}
        self.images_roh = self._roh(ui.get("images_file"))
        self.minimap_roh = self._roh(ui.get("minimap_file"))
        self.images = json.loads(self.images_roh.decode("utf-8")) if self.images_roh else None
        self.minimap = json.loads(self.minimap_roh.decode("utf-8")) if self.minimap_roh else None

    def _roh(self, name):
        if name and name in self.zip.namelist():
            return self.zip.read(name)
        return None

    def close(self) -> None:
        self.zip.close()

    def __enter__(self) -> "ModPaket":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------ Abfragen
    @property
    def erp_name(self) -> str:
        return self.manifest.get("erp", "")

    @property
    def track_code(self) -> str:
        return self.manifest.get("track_code", "")

    def mipmap_eintraege(self) -> List[zipfile.ZipInfo]:
        quellen = [c.get("source", "") for c in self.manifest.get("copy_directories", [])]
        return [i for i in self.zip.infolist()
                if not i.is_dir() and any(q and i.filename.startswith(q) for q in quellen)]

    def ui_kennungen(self) -> Dict[str, str]:
        """Alle Kennungen, auf die images.json und minimap.json verweisen."""
        k = {}
        for name, wert in ((self.images or {}).get("resources") or {}).items():
            k["images." + name] = wert
        for gruppe in ("textures", "textures_om_xm"):
            for name, wert in (((self.minimap or {}).get(gruppe) or {}).get("resources") or {}).items():
                k["minimap.%s.%s" % (gruppe, name)] = wert
        return k

    def erp_entpacken(self, ziel: str) -> str:
        """Kopiert die Strecken-ERP aus dem ZIP in eine Datei (ErpFile braucht einen Pfad)."""
        os.makedirs(os.path.dirname(os.path.abspath(ziel)), exist_ok=True)
        info = self.zip.getinfo(self.erp_name)
        with self.zip.open(info) as q, open(ziel, "wb") as z:
            _kopieren(q, z)
        if os.path.getsize(ziel) != info.file_size:
            raise PaketFehler("ERP unvollständig entpackt")
        return ziel

    # -------------------------------------------------------------- Prüfen
    def pruefen(self, arbeitsordner: str, tief: bool = False) -> List[Tuple[str, bool, str]]:
        """Prüft Aufbau, Manifest, Verweise und die ERP. Gibt [(prüfung, ok, text), ...] zurück."""
        ergebnis = []

        def melde(name, ok, text=""):
            ergebnis.append((name, bool(ok), text))

        fehlend = [f for f in PFLICHTFELDER if f not in self.manifest]
        melde("Manifest-Pflichtfelder", not fehlend, "fehlt: %s" % fehlend if fehlend else "vollständig")
        namen = self.zip.namelist()
        for datei in (self.erp_name, (self.manifest.get("ui") or {}).get("images_file"),
                      (self.manifest.get("ui") or {}).get("minimap_file")):
            melde("Datei %s" % datei, datei in namen)
        nicht_stored = [i.filename for i in self.zip.infolist() if i.compress_type != zipfile.ZIP_STORED]
        melde("ZIP unkomprimiert (wie Originalpakete)", not nicht_stored,
              "%d komprimierte Einträge" % len(nicht_stored) if nicht_stored else "ja")
        mip = self.mipmap_eintraege()
        melde("mipmaps vorhanden", mip, "%d Dateien, %.1f MB" % (len(mip), sum(i.file_size for i in mip) / 1e6))
        if self.erp_name not in namen:
            return ergebnis

        erp_pfad = self.erp_entpacken(os.path.join(arbeitsordner, os.path.basename(self.erp_name)))
        try:
            with erp.open_erp(erp_pfad) as a:
                melde("ERP lesbar", True, "%d Ressourcen, %d Fragmente, %.1f MB"
                      % (len(a), a.fragment_count, os.path.getsize(erp_pfad) / 1e6))
                melde("ERP-Layout lückenlos", a.layout_contiguous and a.declared_fragment_count == a.fragment_count)
                kennungen = {r.identifier for r in a}
                fehlt = {k: v for k, v in self.ui_kennungen().items() if v not in kennungen}
                melde("UI-Kennungen in der ERP", not fehlt,
                      "alle %d gefunden" % len(self.ui_kennungen()) if not fehlt
                      else "fehlen: %s" % ", ".join(sorted(fehlt)[:5]))
                praefix = "eaid://environment_package/tracks/%s/" % self.track_code
                eigene = sum(1 for k in kennungen if k.startswith(praefix))
                melde("Ressourcen unter tracks/%s/" % self.track_code, eigene, "%d" % eigene)
                typen = {r.resource_type for r in a}
                ohne = [t for t in STRECKEN_TYPEN if t not in typen]
                melde("Strecken-Ressourcentypen", not ohne,
                      "alle %d vorhanden" % len(STRECKEN_TYPEN) if not ohne else "fehlen: %s" % ", ".join(ohne))
                # AP16: Einstiegswelt und externe Mip-Pfade müssen zum Code bzw. Kopierziel passen
                welt = "%swep/%s_common.wep" % (praefix, self.track_code)
                melde("Welt %s_common.wep" % self.track_code, welt in kennungen)
                ziele = [c.get("destination", "") for c in self.manifest.get("copy_directories", [])]
                namen = {i.filename.rsplit("/", 1)[-1] for i in self.mipmap_eintraege()}
                n, falsch, fehlt = 0, [], []
                for r in a:
                    if r.resource_type != "GfxSurfaceRes" or len(r.fragments) < 3:
                        continue
                    d = r.fragments[2].read()
                    pfad = d[1:1 + d[0]].decode("utf-8", "surrogateescape")
                    if not pfad.startswith("asset_groups/"):
                        continue  # Mip-Dateien des Spiels selbst
                    n += 1
                    if not any(z and pfad.startswith(z) for z in ziele):
                        falsch.append(pfad)
                    elif pfad.rsplit("/", 1)[-1] not in namen:
                        fehlt.append(pfad.rsplit("/", 1)[-1])
                melde("Mip-Pfade der Texturen = Kopierziel", not falsch and not fehlt,
                      "%d Texturen, alle Dateien im Paket" % n if not falsch and not fehlt else
                      "%d außerhalb des Kopierziels (%s), %d Dateien fehlen (%s)" % (
                          len(falsch), "; ".join(falsch[:2]), len(fehlt), "; ".join(fehlt[:2])))
                if tief:
                    falsch = []
                    for r in a:
                        try:
                            if not r.verify():
                                falsch.append(r.identifier)
                        except Exception as e:  # Entpackfehler
                            falsch.append("%s (%s)" % (r.identifier, e))
                    melde("MD5 aller Ressourcen", not falsch,
                          "alle korrekt" if not falsch else "%d falsch: %s" % (len(falsch), "; ".join(falsch[:3])))
        finally:
            os.remove(erp_pfad)
        return ergebnis


# ------------------------------------------------------------------ Bauen
def paket_bauen(ziel: str, manifest: dict, images: dict, minimap: dict, erp_pfad: str,
                mipmaps: Iterable[Tuple[str, object]], ueberschreiben: bool = False) -> int:
    """Schreibt ein Paket im Aufbau der Originalpakete.

    manifest, images, minimap: dict (wird als JSON geschrieben, Schlüssel sortiert, Einrückung 2 wie in
    den Originalen) oder bytes (wird unverändert übernommen).
    mipmaps: Folge von (Name im ZIP, Quelle). Quelle ist ein Dateipfad oder ein Paar
    (zipfile.ZipFile, ZipInfo), aus dem gestreamt kopiert wird."""
    if os.path.exists(ziel) and not ueberschreiben:
        raise FileExistsError("Zieldatei existiert bereits: %s" % ziel)
    manifest_dict = json.loads(manifest.decode("utf-8")) if isinstance(manifest, (bytes, bytearray)) else manifest
    ui = manifest_dict.get("ui") or {}
    erp_name = manifest_dict["erp"]

    def eintrag(name, groesse):
        zi = zipfile.ZipInfo(name, date_time=ZIP_ZEIT)
        zi.compress_type = zipfile.ZIP_STORED
        zi.file_size = groesse
        return zi

    def text(obj):
        if isinstance(obj, (bytes, bytearray)):
            return bytes(obj)
        return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True).encode("utf-8")

    os.makedirs(os.path.dirname(os.path.abspath(ziel)), exist_ok=True)
    with zipfile.ZipFile(ziel, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
        zf.writestr(eintrag("manifest.json", 0), text(manifest))
        zf.writestr(eintrag(ui.get("images_file", "ui/images.json"), 0), text(images))
        zf.writestr(eintrag(ui.get("minimap_file", "ui/minimap.json"), 0), text(minimap))
        groesse = os.path.getsize(erp_pfad)
        with open(erp_pfad, "rb") as q, zf.open(eintrag(erp_name, groesse), "w", force_zip64=True) as z:
            _kopieren(q, z)
        for name, quelle in sorted(mipmaps, key=lambda x: x[0]):
            if isinstance(quelle, tuple):
                qzip, qinfo = quelle
                with qzip.open(qinfo) as q, zf.open(eintrag(name, qinfo.file_size), "w") as z:
                    _kopieren(q, z)
            else:
                with open(quelle, "rb") as q, zf.open(eintrag(name, os.path.getsize(quelle)), "w") as z:
                    _kopieren(q, z)
    return os.path.getsize(ziel)


def paket_aus_ordner(ordner: str, ziel: str, ueberschreiben: bool = False) -> int:
    """Baut ein Paket aus einem Ordner mit manifest.json, ui/, <erp> und mipmaps/."""
    with open(os.path.join(ordner, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    ui = manifest.get("ui") or {}
    with open(os.path.join(ordner, ui.get("images_file", "ui/images.json")), encoding="utf-8") as f:
        images = json.load(f)
    with open(os.path.join(ordner, ui.get("minimap_file", "ui/minimap.json")), encoding="utf-8") as f:
        minimap = json.load(f)
    mip = []
    for c in manifest.get("copy_directories", []):
        quelle = os.path.join(ordner, c["source"])
        for root, _dirs, files in os.walk(quelle):
            for n in files:
                p = os.path.join(root, n)
                mip.append((c["source"] + os.path.relpath(p, quelle).replace("\\", "/"), p))
    return paket_bauen(ziel, manifest, images, minimap, os.path.join(ordner, manifest["erp"]), mip, ueberschreiben)


def paket_umbauen(quelle: ModPaket, ziel: str, erp_pfad: str, manifest: Optional[dict] = None,
                  ueberschreiben: bool = False) -> int:
    """Neues Paket mit anderer ERP (und optional anderem Manifest); UI und mipmaps aus der Quelle."""
    mip = [(i.filename, (quelle.zip, i)) for i in quelle.mipmap_eintraege()]
    return paket_bauen(ziel, manifest if manifest is not None else quelle.manifest_roh, quelle.images_roh,
                       quelle.minimap_roh, erp_pfad, mip, ueberschreiben)


# ------------------------------------------------- ERP-Umbauten für Tests
def alles_neu_komprimieren(archiv: "erp.ErpFile", stufe: int = compression.DEFAULT_LEVEL,
                           threads: Optional[int] = None, laengenfeld_exakt: bool = True,
                           fortschritt=None) -> None:
    """Komprimiert jedes Fragment neu (Code 0x11, unkomprimiert nur wenn kleiner) und schreibt die
    Längenfelder im Stil des Spiels. Die gespeicherten MD5-Werte bleiben unverändert (Inhalt gleich).
    Gelesen wird nur im aufrufenden Thread (gemeinsamer Dateizeiger), komprimiert parallel."""
    fragmente = [fr for r in archiv for fr in r.fragments]
    with ThreadPoolExecutor(threads or os.cpu_count()) as pool:
        for start in range(0, len(fragmente), 512):
            block = fragmente[start:start + 512]
            daten = [fr.read() for fr in block]
            list(pool.map(lambda fd: fd[0].set_data(fd[1], level=stufe), zip(block, daten)))
            if fortschritt:
                fortschritt(min(start + 512, len(fragmente)), len(fragmente))
    if laengenfeld_exakt:
        for r in archiv:
            r.entry_length_delta = 0


def md5_verfaelschen(archiv: "erp.ErpFile", kennung: str) -> Tuple[str, str]:
    """Setzt den gespeicherten MD5 einer Ressource absichtlich falsch (Daten bleiben gleich)."""
    r = archiv.find(kennung)
    if r is None:
        raise KeyError(kennung)
    alt = r.hash.hex()
    r.hash = hashlib.md5(b"EGO-Werkzeuge: absichtlich falscher Hash " + kennung.encode("utf-8")).digest()
    return alt, r.hash.hex()


def md5_reparieren(archiv: "erp.ErpFile") -> List[str]:
    """Berechnet falsche MD5-Werte neu. Gibt die Kennungen der reparierten Ressourcen zurück."""
    repariert = []
    for r in archiv:
        if not r.verify():
            r.update_hash()
            repariert.append(r.identifier)
    return repariert


def inhalte_vergleichen(pfad_a: str, pfad_b: str) -> Dict[str, list]:
    """Vergleicht zwei ERPs inhaltlich (entpackte Daten je Ressource, Typ, Feld, Fragmentnamen)."""
    unterschiede = {"nur_a": [], "nur_b": [], "daten": [], "kopf": [], "hash": []}

    def gleich(paar):
        r, o = paar
        return ([hashlib.md5(f.read()).digest() for f in r.fragments] ==
                [hashlib.md5(f.read()).digest() for f in o.fragments])

    with erp.open_erp(pfad_a) as a, erp.open_erp(pfad_b) as b:
        rb = {r.identifier: r for r in b}
        paare = []
        for r in a:
            o = rb.pop(r.identifier, None)
            if o is None:
                unterschiede["nur_a"].append(r.identifier)
                continue
            if (r.resource_type, r.unknown, [f.name for f in r.fragments], [f.flags for f in r.fragments]) != \
                    (o.resource_type, o.unknown, [f.name for f in o.fragments], [f.flags for f in o.fragments]):
                unterschiede["kopf"].append(r.identifier)
            if r.hash != o.hash:
                unterschiede["hash"].append(r.identifier)
            paare.append((r, o))
        # Entpacken und MD5 in Threads (26.10.2026); Reihenfolge der Liste wie vorher
        unterschiede["daten"] = [r.identifier for (r, _), ok in zip(paare, parallel.threads(gleich, paare)) if not ok]
        unterschiede["nur_b"] = list(rb)
    return unterschiede


def ordner_leeren(pfad: str) -> None:
    if os.path.isdir(pfad):
        shutil.rmtree(pfad)
    os.makedirs(pfad)
