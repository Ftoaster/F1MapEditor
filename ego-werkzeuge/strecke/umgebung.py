"""Umgebung einer neuen Strecke: Bäume, Büsche, Palmen, Kulissenobjekte, Bodenbewuchs (AP-U1, 28.10.2026).

Vertrag mit dem Mapeditor: ``docs/zwischenstand/umgebung_vertrag.md`` (Projektdatei ``umgebung.json``,
Funktionen unten, Prüfregeln ``UMG_*``). Grundlage sind die Spieltests T1–T3/T6
(``docs/umgebung_terraforming.md``, Abschnitt 5a): neue Instanznamen sind sichtbar, der Bodenbewuchs-Layer
des Trägers funktioniert, Modelle anderer Spielstrecken lassen sich samt Texturen ins Paket kopieren.

Einstieg:

    laden(pfad) / speichern(daten, pfad)   Projektdatei lesen (normalisiert) bzw. schreiben (feste Reihenfolge)
    katalog(spielordner=None, neu=False)  Kulissenmodelle aller Spielstrecken (nur lesend, zwischengespeichert)
    vorschau(modell, pfad_png, groesse)   Silhouette (Seitenansicht LOD 0) als PNG
    pruefen(daten, definition_oder_generatorausgabe, versatz)   Meldungen UMG_* (strecke/pruefregeln.py)
    einsetzen(archiv, daten, ...)         im Bau (neue_strecke_tool.py bauen --umgebung): Instanzen klonen,
                                          fehlende Modelle samt Abhängigkeiten und Mip-Dateien kopieren
    karte(pfad_png, daten, ...)           Draufsicht Strecke + Objekte (Farbe je Kategorie)

Koordinaten: ``x``/``z`` der Objekte sind Definitionskoordinaten wie die Kontrollpunkte des Editors;
Welt = Definition + Versatz (``strecke/platzierung.py``). Die Höhe bestimmt der Bau: oberste Fläche der neu
erzeugten Fahrphysik (TRN~) am Punkt plus ``hoehe_m``.

Der Spielordner wird nur gelesen. Der Zwischenspeicher liegt unter ``%LOCALAPPDATA%\\ego-werkzeuge``.
"""

from __future__ import annotations

import datetime
import glob
import json
import math
import os
import re
import struct
import time
import uuid
import zipfile
from collections import Counter
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

SCHEMA = 1
KATALOG_SCHEMA = 1
#: Kategorien des Katalogs (Vertrag Abschnitt 2)
KATEGORIEN = ("baum", "busch", "palme", "gebaeude", "zelt", "moebel", "flagge", "licht", "sonstiges")
#: Bäume, Büsche, Palmen → Layer ``speedtrees``, alles andere → ``dressing``
LAYER_PFLANZEN = "speedtrees"
#: Gleiche Ebene unter anderem Namen: Portimão-Mod 1.0.2 nennt ``speedtrees`` jetzt ``trees`` (docs/portimao_mod_102.md)
LAYER_ALIAS = {"speedtrees": ("trees",)}
LAYER_KULISSE = "dressing"
PFLANZEN = ("baum", "busch", "palme")
#: Loch des Außenterrains im Portimão-Träger (Welt), docs/neue_strecke.md „Grundfläche und Außenterrain“
LOCH = (-920.13, -1008.23, 2048.0)
SKALA_MIN, SKALA_MAX = 0.2, 5.0
VIELE_OBJEKTE = 3000
MAUER_ABSTAND_M = 2.0
#: Oberflächenklassen des Generatornetzes, auf denen kein Objekt stehen soll (UMG_AUF_FAHRBAHN)
FAHRBAHN_KLASSEN = ("asphalt", "randstein", "linie", "kies", "boxengasse", "startbox", "markierung")
#: Fahrphysik-Code der Mauern (senkrechte Flächen), wird bei der Höhensuche übergangen
MAUER_CODES = ("SCON",)
TRACKS_REL = os.path.join("2025_asset_groups", "environment_package", "tracks")
CORE = "eaid://environment_package/core_assets/"
#: Farben der Kategorien in Karte und Vorschau (RGB)
FARBEN = {"baum": (60, 170, 70), "busch": (150, 200, 80), "palme": (230, 200, 40), "gebaeude": (200, 120, 70),
          "zelt": (240, 240, 240), "moebel": (90, 160, 230), "flagge": (230, 70, 160), "licht": (255, 240, 120),
          "sonstiges": (170, 170, 170)}

_OBJEKT_SCHLUESSEL = ("id", "modell", "x", "z", "hoehe_m", "drehung_grad", "skala", "gruppe")
_DATEI_SCHLUESSEL = ("schema", "bodenbewuchs", "gelaende", "objekte")
_FORM_SCHLUESSEL = ("id", "x", "z", "hoehe_m", "radius_m")
#: Geländeformen (29.10.2026, Spieltests T4/T5 zweite Fassung ✔): Grenzen je Art (Höhe, Radius in m).
#: ``huegel``: Grundfläche im Streckenbereich, sichtbar und Fahrphysik (spiel_geometrie.huegel_anwenden), negativ = Senke.
#: ``berge``: Außenterrain außerhalb des Streckenbereichs, nur sichtbar (berge_einsetzen).
HUEGEL_HOEHE = (-60.0, 200.0)
HUEGEL_RADIUS = (30.0, 800.0)
BERG_HOEHE = (10.0, 2000.0)
BERG_RADIUS = (100.0, 5000.0)
#: Berge: Mitte höchstens so weit von der Lochmitte (Außenterrain reicht bis ±24 km, außen grob aufgelöst)
BERG_ENTFERNUNG_MAX = 12000.0
#: Berge: am Lochrand bleibt das Außenterrain bis RAND_FEST m unverändert, Übergang danach über RAND_UEBERGANG m
RAND_FEST, RAND_UEBERGANG = 10.0, 150.0


class UmgebungFehler(ValueError):
    """Projektdatei ungültig, Modell fehlt, Spielordner fehlt …"""


# =============================================================================== Projektdatei
def _zahl(o, feld, standard=None, idx=None):
    w = o.get(feld, standard)
    if isinstance(w, bool) or not isinstance(w, (int, float)) or not math.isfinite(float(w)):
        raise UmgebungFehler("Object %s: field %s must be a finite number (%r)" % (idx, feld, w))
    return float(w)


def normalisieren(roh) -> dict:
    """Prüft und ergänzt Standardwerte; unbekannte Schlüssel bleiben erhalten. Wirft UmgebungFehler."""
    if not isinstance(roh, dict):
        raise UmgebungFehler("umgebung.json: object ({…}) expected, not %s" % type(roh).__name__)
    d = dict(roh)
    schema = d.get("schema", SCHEMA)
    if schema != SCHEMA:
        raise UmgebungFehler("umgebung.json: schema %r is not supported (only %d)" % (schema, SCHEMA))
    d["schema"] = SCHEMA
    bb = d.get("bodenbewuchs", False)
    if not isinstance(bb, bool):
        raise UmgebungFehler("umgebung.json: bodenbewuchs must be true/false (%r)" % (bb,))
    d["bodenbewuchs"] = bb
    objekte = d.get("objekte", [])
    if not isinstance(objekte, list):
        raise UmgebungFehler("umgebung.json: objekte must be a list")
    neu, ids = [], set()
    for k, o in enumerate(objekte):
        if not isinstance(o, dict):
            raise UmgebungFehler("Object %d: object ({…}) expected" % (k + 1))
        o = dict(o)
        oid = o.get("id")
        if not isinstance(oid, str) or not oid.strip():
            raise UmgebungFehler("Object %d: id is missing or empty" % (k + 1))
        if oid in ids:
            raise UmgebungFehler("Duplicate object ID %r" % oid)
        if len(("%s$umg_%s" % (LAYER_PFLANZEN, oid)).encode("utf-8")) > 255 or "\0" in oid:
            raise UmgebungFehler("Object ID %r too long or contains a null character" % oid)
        ids.add(oid)
        m = o.get("modell")
        if not isinstance(m, str) or not m.startswith("eaid://") or "?" in m or m.endswith((".idf", ".emb")):
            raise UmgebungFehler("Object %s: model must be a base ID eaid://… without extension (%r)" % (oid, m))
        o["x"] = _zahl(o, "x", None, oid)
        o["z"] = _zahl(o, "z", None, oid)
        o["hoehe_m"] = _zahl(o, "hoehe_m", 0.0, oid)
        o["drehung_grad"] = _zahl(o, "drehung_grad", 0.0, oid)
        o["skala"] = _zahl(o, "skala", 1.0, oid)
        if not SKALA_MIN <= o["skala"] <= SKALA_MAX:
            raise UmgebungFehler("Object %s: scale %.3g outside %.1f–%.0f" % (oid, o["skala"], SKALA_MIN, SKALA_MAX))
        g = o.get("gruppe", "")
        o["gruppe"] = "" if g is None else str(g)
        neu.append(o)
    d["objekte"] = neu
    if "gelaende" in d:
        d["gelaende"] = _gelaende_normalisieren(d["gelaende"], ids)
    return d


def _gelaende_normalisieren(g, ids) -> dict:
    if not isinstance(g, dict):
        raise UmgebungFehler("umgebung.json: gelaende must be an object ({…})")
    g = dict(g)
    for art, (hmin, hmax), (rmin, rmax) in (("huegel", HUEGEL_HOEHE, HUEGEL_RADIUS), ("berge", BERG_HOEHE, BERG_RADIUS)):
        liste = g.get(art, [])
        if not isinstance(liste, list):
            raise UmgebungFehler("umgebung.json: gelaende.%s must be a list" % art)
        neu = []
        for k, f in enumerate(liste):
            if not isinstance(f, dict):
                raise UmgebungFehler("gelaende.%s %d: object ({…}) expected" % (art, k + 1))
            f = dict(f)
            fid = f.get("id")
            if not isinstance(fid, str) or not fid.strip():
                raise UmgebungFehler("gelaende.%s %d: id is missing or empty" % (art, k + 1))
            if fid in ids:
                raise UmgebungFehler("Duplicate ID %r" % fid)
            ids.add(fid)
            for feld in ("x", "z", "hoehe_m", "radius_m"):
                f[feld] = _zahl(f, feld, None, fid)
            if not hmin <= f["hoehe_m"] <= hmax:
                raise UmgebungFehler("%s %s: hoehe_m %.0f outside %.0f…%.0f" % (art, fid, f["hoehe_m"], hmin, hmax))
            if not rmin <= f["radius_m"] <= rmax:
                raise UmgebungFehler("%s %s: radius_m %.0f outside %.0f…%.0f" % (art, fid, f["radius_m"], rmin, rmax))
            neu.append(f)
        g[art] = neu
    return g


def huegel_welt(daten, versatz=None) -> List[Tuple[float, float, float, float]]:
    """Hügel/Senken aus ``gelaende.huegel`` als ``(h, x, z, r)`` in Weltkoordinaten für ``--gelaende-huegel``
    (``spiel_geometrie.huegel_anwenden``)."""
    from . import platzierung
    v = platzierung.VERSATZ if versatz is None else versatz
    return [(f["hoehe_m"], f["x"] + v[0], f["z"] + v[2], f["radius_m"])
            for f in (daten.get("gelaende") or {}).get("huegel", [])]


def _lochrand_abstand(x, z):
    """Abstand (m) vom Rand des Streckenbereichs nach außen; 0 innen."""
    x0, z0, kante = LOCH
    dx = np.maximum(np.maximum(x0 - x, x - x0 - kante), 0.0)
    dz = np.maximum(np.maximum(z0 - z, z - z0 - kante), 0.0)
    return np.hypot(dx, dz)


def laden(pfad) -> dict:
    """Liest ``umgebung.json`` (UTF-8, auch mit BOM) und normalisiert sie (Standardwerte). Wirft UmgebungFehler."""
    try:
        with open(pfad, encoding="utf-8-sig") as f:
            roh = json.load(f)
    except OSError as e:
        raise UmgebungFehler("umgebung.json cannot be read: %s" % e) from e
    except ValueError as e:
        raise UmgebungFehler("umgebung.json is not valid JSON: %s" % e) from e
    return normalisieren(roh)


def _geordnet(d: dict, reihenfolge: Sequence[str]) -> dict:
    aus = {k: d[k] for k in reihenfolge if k in d}
    aus.update({k: v for k, v in d.items() if k not in aus})
    return aus


def speichern(daten: dict, pfad) -> None:
    """Schreibt UTF-8 (ohne BOM, LF), Einrückung 2, Schlüssel in fester Reihenfolge (unbekannte dahinter)."""
    d = normalisieren(daten)
    d["objekte"] = [_geordnet(o, _OBJEKT_SCHLUESSEL) for o in d["objekte"]]
    if "gelaende" in d:
        d["gelaende"] = _geordnet({art: [_geordnet(f, _FORM_SCHLUESSEL) for f in liste] if art in ("huegel", "berge")
                                   else liste for art, liste in d["gelaende"].items()}, ("huegel", "berge"))
    d = _geordnet(d, _DATEI_SCHLUESSEL)
    text = json.dumps(d, ensure_ascii=False, indent=2) + "\n"
    pfad = os.fspath(pfad)
    os.makedirs(os.path.dirname(os.path.abspath(pfad)) or ".", exist_ok=True)
    tmp = pfad + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, pfad)


def aus_projekt(ordner) -> Optional[str]:
    """Pfad der Umgebungsdatei eines Editor-Projekts: ``projekt.json`` Schlüssel ``umgebung`` oder
    ``umgebung.json`` im Projektordner; None, wenn es keine gibt."""
    ordner = os.path.abspath(ordner)
    basis = ordner if os.path.isdir(ordner) else os.path.dirname(ordner)
    man = os.path.join(basis, "projekt.json")
    rel = None
    if os.path.exists(man):
        with open(man, encoding="utf-8-sig") as f:
            rel = (json.load(f) or {}).get("umgebung")
    if rel:
        pfad = os.path.abspath(os.path.join(basis, rel))
        if os.path.isabs(rel) or not pfad.startswith(basis + os.sep):
            raise UmgebungFehler("Environment file must be in the project folder: %s" % rel)
        if not os.path.exists(pfad):
            raise UmgebungFehler("projekt.json names %s, the file is missing" % rel)
        return pfad
    pfad = os.path.join(basis, "umgebung.json")
    return pfad if os.path.exists(pfad) else None


# =============================================================================== Katalog
def spielordner_finden(spielordner=None) -> Optional[str]:
    """Spielordner: Angabe, Umgebungsvariable ``EGO_SPIELORDNER`` oder Steam-Orte auf allen Laufwerken; None ohne Fund."""
    from ego import spielpfade
    for p in ([spielordner] if spielordner else []) + spielpfade.kandidaten():
        if p and os.path.isdir(os.path.join(p, TRACKS_REL)):
            return os.path.abspath(p)
    if spielordner:
        raise UmgebungFehler("Game folder without %s: %s" % (TRACKS_REL, spielordner))
    return None


def zwischenspeicher_ordner() -> str:
    basis = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(basis, "ego-werkzeuge")


def katalog_pfad() -> str:
    return os.path.join(zwischenspeicher_ordner(), "umgebung_katalog.json")


def _name_familie(basis: str) -> Tuple[str, str]:
    name = basis.rsplit("/", 1)[-1]
    teile = basis[len(CORE):].split("/") if basis.startswith(CORE) else basis.split("/")
    if teile[0] in ("speedtree", "spt_trees") and len(teile) >= 4:
        return name, teile[2]
    fam = re.sub(r"(_[a-z]\d*|_\d+)+$", "", name) or name
    return name, fam


#: Objektordner unter core_assets/objects/idf/ → Kategorie (None = ausgeschlossen)
_OBJEKT_ORDNER = {"buildings": "gebaeude", "dressing": "sonstiges", "street_furniture": "moebel", "kit": "sonstiges",
                  "flags": "flagge", "lights": "licht", "light_fittings": "licht", "sponsor": "sonstiges",
                  "events": "sonstiges"}
_AUSSCHLUSS_WORT = re.compile(r"(^|_)(pit|pits|pitlane|garage|barrier|armco|tecpro|tyre|tyres|fence|car|cars|"
                              r"vehicle|marshal|crowd|character|people|podium|gameplay|safety)(_|$)")


def kategorie(basis: str, hoehe_m: Optional[float] = None) -> Optional[str]:
    """Kategorie aus dem Pfad der Basis-Kennung (None = keine Kulisse: Barrieren, Fahrzeuge, Boxen …)."""
    if not basis.startswith(CORE) or ".idf-" in basis:
        return None
    teile = basis[len(CORE):].split("/")
    name = teile[-1]
    if teile[0] in ("speedtree", "spt_trees") and len(teile) >= 4 and teile[1] == "idf":
        fam = teile[2]
        if re.search(r"palmetto|bush|hedge|undergrowth|topiary|dwarf", fam):
            return "busch"
        if "palm" in fam:
            return "palme"
        if hoehe_m is not None and hoehe_m < 2.5:
            return "busch"
        return "baum"
    if teile[0] != "objects" or len(teile) < 4 or teile[1] != "idf":
        return None                    # groundcover, speedtree_groundcover, sky, characters …
    ordner = teile[2]
    kat = _OBJEKT_ORDNER.get(ordner)
    if kat is None or _AUSSCHLUSS_WORT.search(name):
        return None
    if re.search(r"tent|marquee|gazebo|pavilion|canopy|awning", name):
        return "zelt"
    if ordner in ("kit", "dressing", "street_furniture"):
        if "light" in name or "lamp" in name:
            return "licht"
        if re.search(r"flag|banner", name):
            return "flagge"
        if re.search(r"furniture|bench|table|chair|bin|seat", name):
            return "moebel"
    return kat


def _erp_toc_im_zip(pfad: str):
    """ERP-Inhaltsverzeichnis eines Mod-Pakets (ZIP, unkomprimiert gespeichert) mit Lesezugriff auf die Daten."""
    from ego import erp as _erp
    z = zipfile.ZipFile(pfad)
    try:
        name = json.loads(z.read("manifest.json").decode("utf-8"))["erp"]
        info = z.getinfo(name)
    finally:
        z.close()
    if info.compress_type != zipfile.ZIP_STORED:
        raise UmgebungFehler("ERP in the package is stored compressed: %s" % pfad)
    fh = open(pfad, "rb")
    fh.seek(info.header_offset)
    kopf = fh.read(30)
    start = info.header_offset + 30 + struct.unpack_from("<H", kopf, 26)[0] + struct.unpack_from("<H", kopf, 28)[0]
    fh.seek(start)
    a = _erp.ErpFile()
    a.path = pfad
    a._read_info(fh)
    for r in a.resources:
        for fr in r.fragments:
            fr.source_offset += start
    a._fh = fh
    return a


def traeger_oeffnen(pfad: str):
    """Träger als ErpFile: ``.erp`` direkt, Mod-Paket (``.zip``) ohne Entpacken."""
    from ego import erp as _erp
    return _erp_toc_im_zip(pfad) if pfad.lower().endswith(".zip") else _erp.open_erp(pfad)


def _toc_lesen(pfad: str):
    """Arbeitsprozess: Kulissenmodelle, Materialbindungen und Kollisionen einer Spiel-ERP (nur Inhaltsverzeichnis)."""
    from ego import erp as _erp
    modelle, bindungen, kollision = [], [], []
    with _erp.open_erp(pfad) as a:
        for r in a:
            k = r.identifier
            if not k.startswith(CORE):
                continue
            if r.resource_type == "GfxModelRes" and k.endswith(".idf?model"):
                modelle.append(k[:-len(".idf?model")])
            elif r.resource_type == "GfxMatBindRes" and k.endswith(".emb?context=default"):
                bindungen.append(k[:-len(".emb?context=default")])
            elif r.resource_type == "PYRB":
                kollision.append(k.split(".idf", 1)[0])
    return pfad, modelle, bindungen, kollision


def _aabb_lesen(auftrag):
    """Arbeitsprozess: (ERP-Pfad, [Basis]) → [(Basis, aabb_min, aabb_max, kugel)]."""
    from ego import erp as _erp, grafik
    pfad, basen = auftrag
    aus = []
    with (traeger_oeffnen(pfad) if pfad.lower().endswith(".zip") else _erp.open_erp(pfad)) as a:
        for b in basen:
            r = a.find(b + ".idf?model")
            try:
                m = grafik.GfxModell.aus_bytes(r.data())
                aus.append((b, list(m.aabb_min), list(m.aabb_max), list(m.kugel)))
            except Exception:  # noqa: BLE001  (Modell nicht lesbar → ohne Größe)
                aus.append((b, None, None, None))
    return aus


def _stempel(dateien: Sequence[str]) -> dict:
    groesse, zeit = 0, 0.0
    for f in dateien:
        s = os.stat(f)
        groesse += s.st_size
        zeit = max(zeit, s.st_mtime)
    return {"dateien": len(dateien), "bytes": groesse, "mtime": round(zeit, 3)}


def _traeger_stempel(traeger: Optional[str]) -> Optional[dict]:
    if not traeger:
        return None
    s = os.stat(traeger)
    return {"pfad": os.path.abspath(traeger), "bytes": s.st_size, "mtime": round(s.st_mtime, 3)}


def standard_traeger() -> Optional[str]:
    """Portimão-Träger des Werkzeugkastens (für ``im_traeger``), falls vorhanden."""
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "testausgabe", "pakete",
                     "portimao_original_wiederhergestellt.zip")
    return p if os.path.exists(p) else None


def katalog(spielordner=None, neu=False, traeger="standard", cache=None, protokoll=None) -> dict:
    """Kulissenmodelle aller Spielstrecken (Vertrag Abschnitt 2).

    Liest die Inhaltsverzeichnisse aller Strecken-ERPs unter ``<Spielordner>/2025_asset_groups/environment_package/
    tracks`` (nur lesend, parallel über ``ego/parallel.py``) und von jedem Modell einmal die AABB. ``traeger``:
    Träger-ERP oder -Paket für ``im_traeger`` und Modelle, die nur der Träger hat (Quelle ``traeger``);
    ``"standard"`` = Portimão-Träger des Werkzeugkastens, falls vorhanden; None = ohne.
    Zwischenspeicher: ``cache`` bzw. ``%LOCALAPPDATA%\\ego-werkzeuge\\umgebung_katalog.json``; er gilt, solange
    Spielordner (Zahl, Größe, Zeit der ERPs) und Träger unverändert sind (``neu=True`` erzwingt den Neuaufbau)."""
    from ego import parallel
    t0 = time.time()
    so = spielordner_finden(spielordner)
    if so is None:
        raise UmgebungFehler("Game folder not found (specify it or set the environment variable EGO_SPIELORDNER)")
    if traeger == "standard":
        traeger = standard_traeger()
    tracks = os.path.join(so, TRACKS_REL)
    dateien = sorted(glob.glob(os.path.join(tracks, "**", "*.erp"), recursive=True))
    stempel = {"spiel": _stempel(dateien), "traeger": _traeger_stempel(traeger)}
    cache = cache or katalog_pfad()
    if not neu and os.path.exists(cache):
        try:
            with open(cache, encoding="utf-8") as f:
                alt = json.load(f)
            if (alt.get("schema") == KATALOG_SCHEMA and alt.get("spielordner") == so
                    and alt.get("stempel") == json.loads(json.dumps(stempel))):
                alt["laufzeit_s"] = round(time.time() - t0, 3)
                alt["aus_zwischenspeicher"] = True
                return alt
        except (OSError, ValueError):
            pass
    log = protokoll or (lambda *_: None)
    log("Katalog: %d Strecken-ERPs lesen …" % len(dateien))
    toc = parallel.abbilden(_toc_lesen, dateien, aufwand=[os.path.getsize(f) for f in dateien])
    quelle, bindung, kollision, strecken = {}, set(), set(), {}
    for pfad, modelle, bind, koll in toc:
        rel = os.path.relpath(pfad, os.path.join(so, "2025_asset_groups", "environment_package")).replace("\\", "/")
        bindung |= set(bind)
        kollision |= set(koll)
        for b in modelle:
            strecken.setdefault(b, set()).add(rel.split("/")[1])
            if b not in quelle or (b in bind and quelle[b][1] is False):
                quelle[b] = (pfad, b in bind)
    im_traeger, nur_traeger = set(), []
    if traeger:
        a = traeger_oeffnen(traeger)
        try:
            tb = {r.identifier[:-len(".emb?context=default")] for r in a
                  if r.resource_type == "GfxMatBindRes" and r.identifier.endswith(".emb?context=default")}
            for r in a:
                k = r.identifier
                if r.resource_type == "GfxModelRes" and k.startswith(CORE) and k.endswith(".idf?model"):
                    b = k[:-len(".idf?model")]
                    im_traeger.add(b)
                    if b not in quelle and b in tb:
                        nur_traeger.append(b)
        finally:
            a.close()
    kandidaten = [b for b, (p, mit_bindung) in quelle.items() if mit_bindung and kategorie(b) is not None
                  and b not in kollision]
    kandidaten += [b for b in nur_traeger if kategorie(b) is not None and b not in kollision]
    auftraege = {}
    for b in kandidaten:
        auftraege.setdefault(quelle[b][0] if b in quelle else traeger, []).append(b)
    log("Katalog: Größen von %d Modellen lesen …" % len(kandidaten))
    groessen = {}
    for liste in parallel.abbilden(_aabb_lesen, sorted(auftraege.items())):
        for b, lo, hi, kugel in liste:
            groessen[b] = (lo, hi, kugel)
    modelle = []
    for b in sorted(kandidaten):
        lo, hi, _k = groessen.get(b, (None, None, None))
        if lo is None:
            continue
        g = [round(float(hi[i] - lo[i]), 3) for i in range(3)]
        kat = kategorie(b, g[1])
        if kat is None:
            continue
        rad = max(math.hypot(x, z) for x in (lo[0], hi[0]) for z in (lo[2], hi[2]))
        name, fam = _name_familie(b)
        q = quelle.get(b)
        modelle.append({"modell": b, "name": name, "familie": fam, "kategorie": kat, "groesse_m": g,
                        "radius_m": round(float(rad), 3), "aabb_min": [round(float(v), 3) for v in lo],
                        "aabb_max": [round(float(v), 3) for v in hi],
                        "quelle": ("tracks/" + os.path.relpath(q[0], tracks).replace("\\", "/")) if q else "traeger",
                        "strecken": sorted(strecken.get(b, ())), "im_traeger": b in im_traeger})
    daten = {"schema": KATALOG_SCHEMA, "spielordner": so, "traeger": stempel["traeger"]["pfad"] if traeger else None,
             "erzeugt": datetime.datetime.now().isoformat(timespec="seconds"), "stempel": stempel,
             "erps": len(dateien), "modelle_core_assets": len(quelle), "modelle": modelle,
             "je_kategorie": dict(Counter(m["kategorie"] for m in modelle).most_common()),
             "laufzeit_s": round(time.time() - t0, 2), "aus_zwischenspeicher": False}
    try:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        tmp = cache + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(daten, f, ensure_ascii=False, indent=1)
        os.replace(tmp, cache)
    except OSError:
        pass
    return daten


def katalog_index(katalog_daten: dict) -> Dict[str, dict]:
    return {m["modell"]: m for m in katalog_daten.get("modelle", [])}


def quelle_pfad(eintrag: dict, katalog_daten: dict) -> str:
    """Absoluter Pfad der Quell-ERP eines Katalogeintrags (``traeger`` = Träger aus dem Katalog)."""
    if eintrag["quelle"] == "traeger":
        if not katalog_daten.get("traeger"):
            raise UmgebungFehler("Catalogue has no carrier, model %s only exists in the carrier" % eintrag["modell"])
        return katalog_daten["traeger"]
    return os.path.join(katalog_daten["spielordner"], "2025_asset_groups", "environment_package",
                        *eintrag["quelle"].split("/"))


# =============================================================================== Vorschau
def _ressourcen_index(pfad: str):
    """RessourcenIndex: Quell-ERP zuerst, dann die übrigen ERPs derselben Strecke (Material/Textur können dort liegen)."""
    from ego import erp as _erp, grafik
    idx = grafik.RessourcenIndex()
    idx.hinzufuegen(traeger_oeffnen(pfad))
    teile = os.path.normpath(pfad).split(os.sep)
    if "tracks" in teile and not pfad.lower().endswith(".zip"):
        wurzel = os.sep.join(teile[:teile.index("tracks") + 2])
        for f in sorted(glob.glob(os.path.join(wurzel, "**", "*.erp"), recursive=True)):
            if os.path.normcase(os.path.abspath(f)) != os.path.normcase(os.path.abspath(pfad)):
                idx.hinzufuegen(_erp.open_erp(f))
    return idx


def vorschau_pfad(modell: str, groesse: int = 160) -> str:
    return os.path.join(zwischenspeicher_ordner(), "umgebung_vorschau", "%s_%d.png" % (modell.rsplit("/", 1)[-1], groesse))


def vorschau(modell: str, pfad_png=None, groesse: int = 160, katalog_daten=None, neu=False) -> str:
    """Silhouette (Seitenansicht der LOD-0-Geometrie über die breitere waagrechte Achse) als PNG mit transparentem Hintergrund; gibt den Pfad
    zurück. Ohne lesbare Geometrie (z. B. reine Billboards) ein Umriss aus der AABB. Zwischengespeichert: eine
    vorhandene Datei wird nicht neu gezeichnet (``neu=True`` erzwingt es)."""
    from PIL import Image, ImageDraw
    from ego import grafik
    pfad_png = os.fspath(pfad_png) if pfad_png else vorschau_pfad(modell, groesse)
    if os.path.exists(pfad_png) and not neu:
        return pfad_png
    kd = katalog_daten or katalog()
    e = katalog_index(kd).get(modell)
    if e is None:
        raise UmgebungFehler("Model not in the catalogue: %s" % modell)
    farbe = FARBEN.get(e["kategorie"], (170, 170, 170)) + (255,)
    lo, hi = np.asarray(e["aabb_min"], float), np.asarray(e["aabb_max"], float)
    achse = 0 if hi[0] - lo[0] >= hi[2] - lo[2] else 2       # Seitenansicht über die breitere Achse
    tris = []
    try:
        idx = _ressourcen_index(quelle_pfad(e, kd))
        try:
            gm = grafik.modell_laden(idx, modell + ".idf?model", lod=0, nur_positionen=True)
            for n in gm.netze:
                tris.append(n.positionen[n.dreiecke][:, :, [achse, 1]].astype(np.float64))
        finally:
            idx.schliessen()
    except Exception:  # noqa: BLE001  (Vorschau ist Hilfsmittel; Umriss aus der AABB)
        tris = []
    rand = 6
    x0, x1, y0, y1 = lo[achse], hi[achse], lo[1], hi[1]
    if tris:
        alle = np.concatenate(tris).reshape(-1, 2)
        x0, y0 = alle.min(0)
        x1, y1 = alle.max(0)
    mass = (groesse - 2 * rand) / max(x1 - x0, y1 - y0, 1e-3)
    ox = (groesse - (x1 - x0) * mass) / 2
    oy = (groesse - (y1 - y0) * mass) / 2
    im = Image.new("RGBA", (groesse, groesse), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    def P(x, y):
        return (ox + (x - x0) * mass, groesse - (oy + (y - y0) * mass))
    if tris:
        for t in np.concatenate(tris):
            d.polygon([P(*t[0]), P(*t[1]), P(*t[2])], fill=farbe)
    else:
        if e["kategorie"] in PFLANZEN:
            mx = (x0 + x1) / 2
            d.rectangle([P(mx - (x1 - x0) * 0.05, y0), P(mx + (x1 - x0) * 0.05, y0 + (y1 - y0) * 0.35)], fill=farbe)
            d.ellipse([P(x0, y1), P(x1, y0 + (y1 - y0) * 0.3)], fill=farbe)
        else:
            d.rectangle([P(x0, y1), P(x1, y0)], outline=farbe, width=2)
    os.makedirs(os.path.dirname(os.path.abspath(pfad_png)), exist_ok=True)
    im.save(pfad_png)
    return pfad_png


# =============================================================================== Flächensuche (Raster)
class Flaechen:
    """Dreiecke in einem XZ-Raster für schnelle Punktabfragen (Höhe, Oberfläche, Mauerabstand)."""

    def __init__(self, v, tri, klasse=None, zelle=8.0):
        self.v = np.asarray(v, np.float64)
        self.tri = np.asarray(tri, np.int64)
        self.klasse = None if klasse is None else np.asarray(klasse)
        self.zelle = float(zelle)
        p = self.v[self.tri]
        lo = np.floor(p[:, :, [0, 2]].min(1) / self.zelle).astype(np.int64)
        hi = np.floor(p[:, :, [0, 2]].max(1) / self.zelle).astype(np.int64)
        self.raster: Dict[Tuple[int, int], List[int]] = {}
        for i in range(len(self.tri)):
            for gx in range(lo[i, 0], hi[i, 0] + 1):
                for gz in range(lo[i, 1], hi[i, 1] + 1):
                    self.raster.setdefault((gx, gz), []).append(i)

    def treffer(self, x, z):
        """[(Dreieck, Höhe)] aller Dreiecke unter (x, z)."""
        k = self.raster.get((int(math.floor(x / self.zelle)), int(math.floor(z / self.zelle))))
        if not k:
            return []
        k = np.asarray(k)
        a, b, c = (self.v[self.tri[k, i]] for i in range(3))
        d = (b[:, 2] - c[:, 2]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 2] - c[:, 2])
        with np.errstate(divide="ignore", invalid="ignore"):
            l1 = ((b[:, 2] - c[:, 2]) * (x - c[:, 0]) + (c[:, 0] - b[:, 0]) * (z - c[:, 2])) / d
            l2 = ((c[:, 2] - a[:, 2]) * (x - c[:, 0]) + (a[:, 0] - c[:, 0]) * (z - c[:, 2])) / d
            l3 = 1 - l1 - l2
            ok = (np.abs(d) > 1e-12) & (l1 >= -1e-6) & (l2 >= -1e-6) & (l3 >= -1e-6)
            y = l1 * a[:, 1] + l2 * b[:, 1] + l3 * c[:, 1]
        return [(int(i), float(h)) for i, h in zip(k[ok], y[ok])]

    def hoehe(self, x, z) -> Optional[float]:
        """Oberste Fläche unter (x, z), None außerhalb."""
        t = self.treffer(x, z)
        return max(h for _, h in t) if t else None

    def hoehe_nahe(self, x, z, radius=8.0) -> Optional[float]:
        """Wie ``hoehe``; über einer Lücke der Fahrphysik zählt das Mittel der Flächen im nächsten Ring bis ``radius``."""
        y = self.hoehe(x, z)
        r = 1.0
        while y is None and r <= radius:
            ring = [self.hoehe(x + r * math.cos(k * math.pi / 4), z + r * math.sin(k * math.pi / 4)) for k in range(8)]
            ring = [h for h in ring if h is not None]
            y = sum(ring) / len(ring) if ring else None
            r *= 2
        return y


class Kanten:
    """Strecken (XZ) in einem Raster für Abstandsabfragen (Mauern)."""

    def __init__(self, a, b, zelle=8.0):
        self.a, self.b = np.asarray(a, np.float64), np.asarray(b, np.float64)
        self.zelle = float(zelle)
        lo = np.floor(np.minimum(self.a, self.b) / self.zelle).astype(np.int64)
        hi = np.floor(np.maximum(self.a, self.b) / self.zelle).astype(np.int64)
        self.raster: Dict[Tuple[int, int], List[int]] = {}
        for i in range(len(self.a)):
            for gx in range(lo[i, 0], hi[i, 0] + 1):
                for gz in range(lo[i, 1], hi[i, 1] + 1):
                    self.raster.setdefault((gx, gz), []).append(i)

    def abstand(self, x, z, bis) -> float:
        """Kleinster Abstand zu einer Strecke, höchstens bis zur Grenze ``bis`` gesucht (sonst inf)."""
        r = int(math.ceil(bis / self.zelle))
        gx, gz = int(math.floor(x / self.zelle)), int(math.floor(z / self.zelle))
        k = set()
        for dx in range(-r, r + 1):
            for dz in range(-r, r + 1):
                k.update(self.raster.get((gx + dx, gz + dz), ()))
        if not k:
            return math.inf
        k = np.fromiter(k, np.int64)
        a, ab = self.a[k], self.b[k] - self.a[k]
        q = np.array([x, z])
        t = np.clip(((q - a) * ab).sum(1) / np.maximum((ab * ab).sum(1), 1e-12), 0, 1)
        return float(np.min(np.linalg.norm(a + ab * t[:, None] - q, axis=1)))


def _netz_aus(quelle):
    """Generatornetz (vertices, dreiecke, oberflaeche, namen) aus Ordner/npz/erzeugung-Ergebnis; sonst None."""
    if quelle is None:
        return None
    if isinstance(quelle, dict) and "netz" in quelle:
        n = quelle["netz"]
        namen = list(n.get("oberflaechen_namen") or n.get("namen") or [])
        if not namen:                    # Ergebnis von erzeugung.berechnen: Klassen nach definition.OBERFLAECHEN (4.11.2026)
            from .definition import OBERFLAECHEN
            namen = list(OBERFLAECHEN)
        return (np.asarray(n["vertices"], float), np.asarray(n["dreiecke"], np.int64),
                np.asarray(n["oberflaeche"], np.int64), [str(x) for x in namen])
    if isinstance(quelle, (str, os.PathLike)):
        p = os.fspath(quelle)
        npz = os.path.join(p, "strecke.npz") if os.path.isdir(p) else p
        if npz.endswith(".npz") and os.path.exists(npz):
            z = np.load(npz)
            return (z["vertices"].astype(float), z["dreiecke"].astype(np.int64), z["oberflaeche"].astype(np.int64),
                    [str(x) for x in z["oberflaechen_namen"]])
    return None


def _mittellinie_aus(quelle):
    """Mittellinie (Stationen) aus Definition (Pfad, dict, Streckendefinition); sonst None."""
    from . import definition as dfn, mittellinie
    try:
        if isinstance(quelle, dfn.Streckendefinition):
            d = quelle
        elif isinstance(quelle, dict) and "netz" not in quelle:
            d = dfn.aus_dict(quelle)
        elif isinstance(quelle, (str, os.PathLike)) and str(quelle).lower().endswith(".json"):
            d = dfn.laden(os.fspath(quelle))
        else:
            return None
        return mittellinie.Mittellinie(d).ref
    except (ValueError, KeyError, TypeError, OSError):
        return None


def flaechen_aus_netz(netz):
    v, tri, surf, namen = netz
    fl = Flaechen(v, tri, surf)
    kanten = None
    if "mauer" in namen:
        km = namen.index("mauer")
        t = tri[surf == km]
        if len(t):
            a = np.concatenate([v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]])[:, [0, 2]]
            b = np.concatenate([v[t[:, 1]], v[t[:, 2]], v[t[:, 0]]])[:, [0, 2]]
            lang = np.linalg.norm(b - a, axis=1) > 1e-3
            kanten = Kanten(a[lang], b[lang])
    return fl, kanten


# =============================================================================== Prüfen
def pruefen_mit_geprueft(daten, definition_oder_generatorausgabe=None, versatz=None, katalog_daten=None):
    """Wie ``pruefen``, zusätzlich die Menge der geprüften Regel-IDs (für ``pruefregeln.bericht``)."""
    from .pruefregeln import meldung, ort
    from . import platzierung
    m: List[dict] = []
    gp = {"UMG_DATEI"}
    try:
        d = laden(daten) if isinstance(daten, (str, os.PathLike)) else normalisieren(daten)
    except UmgebungFehler as e:
        return [meldung("UMG_DATEI", str(e))], gp
    v = np.asarray(platzierung.VERSATZ if versatz is None else versatz, float)
    obj = d["objekte"]

    def welt(o):
        return [o["x"] + v[0], v[1], o["z"] + v[2]]
    gp |= {"UMG_VIELE", "UMG_AUSSERHALB"}
    if len(obj) > VIELE_OBJEKTE:
        m.append(meldung("UMG_VIELE", "%d environment objects (guide value ≤ %d; Portimão has 1,274 trees)"
                         % (len(obj), VIELE_OBJEKTE), ist=len(obj), soll=VIELE_OBJEKTE))
    kd = katalog_daten
    if kd is None:
        try:
            kd = katalog()
        except UmgebungFehler:
            kd = None
    if kd is not None:
        gp.add("UMG_MODELL_UNBEKANNT")
        bekannt = katalog_index(kd)
        for o in obj:
            if o["modell"] not in bekannt:
                m.append(meldung("UMG_MODELL_UNBEKANNT", "Object %s: model %s not in the catalogue"
                                 % (o["id"], o["modell"].rsplit("/", 1)[-1]), ort(None, None, welt(o), o["id"])))
    x0, z0, kante = LOCH
    for o in obj:
        w = welt(o)
        if not (x0 < w[0] < x0 + kante and z0 < w[2] < z0 + kante):
            m.append(meldung("UMG_AUSSERHALB", "Object %s at world x %.0f, z %.0f is outside the track area (x "
                                               "%.0f…%.0f, z %.0f…%.0f)" % (o["id"], w[0], w[2], x0, x0 + kante, z0, z0 + kante),
                             ort(None, None, w, o["id"])))
    gp |= {"UMG_HUEGEL_LAGE", "UMG_BERG_LAGE", "UMG_BERG_RAND"}
    gel = d.get("gelaende") or {}
    for f in gel.get("huegel", []):
        w = welt(f)
        if not (x0 < w[0] < x0 + kante and z0 < w[2] < z0 + kante):
            m.append(meldung("UMG_HUEGEL_LAGE", "Hill %s: centre at world x %.0f, z %.0f is outside the track area "
                                                "(mountains outside belong under “berge”)" % (f["id"], w[0], w[2]),
                             ort(None, None, w, f["id"])))
    mitte = (x0 + kante / 2.0, z0 + kante / 2.0)
    for f in gel.get("berge", []):
        w = welt(f)
        aussen = float(_lochrand_abstand(np.float64(w[0]), np.float64(w[2])))
        weit = math.hypot(w[0] - mitte[0], w[2] - mitte[1])
        if aussen <= 0.0 or weit > BERG_ENTFERNUNG_MAX:
            grund = ("lies in the track area (only hills work there)" if aussen <= 0.0
                     else "is %.1f km from the centre (at most %.0f km)" % (weit / 1000, BERG_ENTFERNUNG_MAX / 1000))
            m.append(meldung("UMG_BERG_LAGE", "Mountain %s at world x %.0f, z %.0f %s" % (f["id"], w[0], w[2], grund),
                             ort(None, None, w, f["id"])))
        elif aussen < f["radius_m"] * 0.5 + RAND_FEST + RAND_UEBERGANG:
            m.append(meldung("UMG_BERG_RAND", "Mountain %s reaches the track area; the terrain stays flat at the edge "
                                              "and the mountain is cut off there (centre %.0f m from the edge, radius "
                                              "%.0f m)"
                             % (f["id"], aussen, f["radius_m"]), ort(None, None, w, f["id"]),
                             ist=round(aussen), soll="≥ %.0f m" % (f["radius_m"] * 0.5 + RAND_FEST + RAND_UEBERGANG)))
    netz = _netz_aus(definition_oder_generatorausgabe)
    if netz is not None:
        gp.add("UMG_AUF_FAHRBAHN")
        fl, kanten = flaechen_aus_netz(netz)
        namen = netz[3]
        for o in obj:
            klassen = sorted({namen[int(fl.klasse[i])] for i, _ in fl.treffer(o["x"], o["z"])} & set(FAHRBAHN_KLASSEN))
            ab = kanten.abstand(o["x"], o["z"], MAUER_ABSTAND_M) if kanten is not None else math.inf
            if klassen or ab < MAUER_ABSTAND_M:
                grund = ("on %s" % "/".join(klassen)) if klassen else "%.1f m beside a wall" % ab
                m.append(meldung("UMG_AUF_FAHRBAHN", "Object %s stands %s" % (o["id"], grund),
                                 ort(None, None, welt(o), o["id"]), ist=klassen or round(ab, 2),
                                 soll="Gras/Gelände, ≥ %.0f m von Mauern" % MAUER_ABSTAND_M))
    else:
        st = _mittellinie_aus(definition_oder_generatorausgabe)
        if st is not None:
            gp.add("UMG_AUF_FAHRBAHN")
            P = np.asarray(st.pos, float)[:, [0, 2]]
            halb = np.maximum(np.asarray(st.wl, float), np.asarray(st.wr, float)) + 1.0
            kanten = Kanten(P, np.roll(P, -1, axis=0))
            for o in obj:
                ab = kanten.abstand(o["x"], o["z"], float(halb.max()))
                if ab < float(halb.max()):
                    i = int(np.argmin(np.linalg.norm(P - [o["x"], o["z"]], axis=1)))
                    if ab < halb[i]:
                        m.append(meldung("UMG_AUF_FAHRBAHN", "Object %s stands on the road (%.1f m from the "
                                                             "centreline; approximation without generator mesh)" % (o["id"], ab),
                                         ort(None, None, welt(o), o["id"]), ist=round(ab, 2),
                                         soll="> %.1f m" % halb[i]))
    return m, gp


def pruefen(daten, definition_oder_generatorausgabe=None, versatz=None, katalog_daten=None) -> List[dict]:
    """Prüfregeln UMG_* (``strecke/pruefregeln.py``): Meldungen mit ``ort.welt`` und ``ort.objekt`` (Objekt-ID).

    ``daten``: Pfad oder dict. ``definition_oder_generatorausgabe``: Generatorordner/``strecke.npz``/Ergebnis von
    ``erzeugung.berechnen`` (genaue Oberflächen und Mauern) oder Definition (Pfad, dict; Näherung über die
    Mittellinie). ``versatz``: Definition → Welt (Standard ``platzierung.VERSATZ``). ``katalog_daten``: Ergebnis von
    ``katalog()``; ohne Angabe wird der zwischengespeicherte Katalog benutzt (ohne Spielordner: Regel nicht geprüft)."""
    return pruefen_mit_geprueft(daten, definition_oder_generatorausgabe, versatz, katalog_daten)[0]


# =============================================================================== Einsetzen (Bau)
def _layer(archiv, kurz, code):
    """WOInstances-Ressource der Ebene ``kurz`` und ihr Name im Träger (bei Mod 1.0.2 ``trees`` statt ``speedtrees``)."""
    from . import spiel_welt
    vorhanden = spiel_welt.layer_ressourcen(archiv, code)
    for name in (kurz,) + LAYER_ALIAS.get(kurz, ()):
        if name in vorhanden:
            return vorhanden[name], name
    raise UmgebungFehler("Carrier has no layer %s" % kurz)


def _text_setzen(p, block, pfad, text):
    from ego import pkg
    f = pkg._pfad(p, block, *pfad)
    b = text.encode("utf-8")
    f.wert = p.neuer_wert(f.wert, bytes([len(b)]) + b + b"\0")


def _vorlage(p, layer_kurz):
    """Index einer Träger-Instanz als Klonvorlage: RenderModel mit Modell und Materialbindung, ohne Kollision;
    nie eine eigene (``$umg_``) oder eine Entfernungstafel."""
    from . import spiel_welt
    beste = None
    for i in p.instanzen():
        if i._wert("template") != "RenderModel" or not i.modell or "$umg_" in i.name or "bremsmarker" in i.name:
            continue
        if spiel_welt.hat_physik(p, i) or not i._wert("overrides", "Model", "mat binding"):
            continue
        komp = spiel_welt.komponenten(p, i)
        if "Render" in komp:
            return i.index
        if beste is None:
            beste = i.index
    if beste is None:
        raise UmgebungFehler("Layer %s has no template (RenderModel without collision)" % layer_kurz)
    return beste


def lagematrix(x, y, z, drehung_grad=0.0, skala=1.0) -> List[float]:
    """4×4-Matrix (Zeilenvektoren, Translation 12–14): Drehung um die Hochachse, mathematisch positiv in der
    Draufsicht (x rechts, z unten, die lokale x-Achse dreht von +x nach −z), gleichmäßiger Maßstab."""
    w = math.radians(drehung_grad)
    c, s = math.cos(w) * skala, math.sin(w) * skala
    return [c, 0.0, -s, 0.0, 0.0, skala, 0.0, 0.0, s, 0.0, c, 0.0, float(x), float(y), float(z), 1.0]


def ressourcen_kopieren(ziel, quellen, start, code, spielordner, praefix="umg_"):
    """Abhängigkeiten ab ``start`` aus ``quellen`` (RessourcenIndex) übernehmen, die in ``ziel`` fehlen; Typ-Konstante
    (``unknown``) und Fragment-Flags wie in der Quelle, MD5 neu. Externe Mip-Pfade (GfxSurfaceRes Fragment 2) auf
    ``asset_groups/environment_package/tracks/<code>/mipmaps/<praefix><Datei>`` umschreiben.
    Rückgabe: ([(Typ, Kennung)], [(Name im ZIP, Spieldatei)], [offene Kennungen])."""
    from . import streckencode
    offen, gesehen, neu, mips, fehlt = list(start), set(), [], [], []
    ordner = "asset_groups/environment_package/tracks/%s/mipmaps/" % code
    while offen:
        k = offen.pop(0)
        if k in gesehen:
            continue
        gesehen.add(k)
        if ziel.find(k) is not None:
            continue
        r = quellen.get(k)
        if r is None:
            fehlt.append(k)
            continue
        texte, _ = streckencode._verweise_ressource(r)
        offen += texte
        frag = []
        for i, fr in enumerate(r.fragments):
            d = fr.read()
            if r.resource_type == "GfxSurfaceRes" and i == 2 and d:
                pfad = d[1:1 + d[0]].decode("utf-8", "surrogateescape")
                if not pfad.startswith("asset_groups/"):
                    datei = praefix + pfad.rsplit("/", 1)[-1]
                    mips.append(("mipmaps/" + datei, os.path.join(spielordner, *pfad.split("/"))))
                    d = streckencode.mip_umbenennen(d, lambda _s, z=ordner + datei: z) or d
            frag.append((fr.name, d, fr.flags))
        n = ziel.add_resource(k, r.resource_type, [(nm, d) for nm, d, _ in frag], unknown=r.unknown)
        for fr, (_, _, fl) in zip(n.fragments, frag):
            fr.flags = fl
        neu.append((r.resource_type, k))
    for _, datei in mips:
        if not os.path.exists(datei):
            raise UmgebungFehler("Mip file is missing in the game folder: %s" % datei)
    return neu, mips, fehlt


def hoehenflaechen(archiv) -> Flaechen:
    """Fahrphysik (TRN~) des Archivs ohne Mauerflächen als Höhenquelle."""
    from ego import trn
    r = [x for x in archiv if x.resource_type == "TRN~"]
    if len(r) != 1:
        raise UmgebungFehler("Archive has no unique TRN~")
    p, t, c = trn.mesh(trn.open_trn(r[0].fragments[0].read()).geometry())
    c = np.asarray([x.decode("ascii", "replace") if isinstance(x, bytes) else str(x) for x in c])
    flach = ~np.isin(c, MAUER_CODES)
    return Flaechen(np.asarray(p, np.float64), np.asarray(t, np.int64)[flach])


def einsetzen(archiv, daten, code="portimao", versatz=None, katalog_daten=None, spielordner=None,
              hoehen=None, protokoll=None) -> dict:
    """Umgebungsobjekte in das (Träger-)Archiv setzen. Läuft im Bau **vor** ``spiel_welt.welt_aufraeumen``:
    Klonvorlagen sind Träger-Instanzen; das Aufräumen entfernt nur Original-Instanzen, die Klone bleiben.

    Je Objekt: Klon einer Träger-Instanz in ``speedtrees`` (Bäume, Büsche, Palmen) bzw. ``dressing`` (Rest) mit Name
    ``<layer>$umg_<id>``, uuid5-ID, Modell ``<modell>.idf?context=render``, Bindung ``<modell>.emb?context=default``
    und Lagematrix (Drehung, Maßstab). Höhe = oberste Fläche der Fahrphysik (``hoehen``, Standard: TRN~ des Archivs)
    plus ``hoehe_m``. Fehlende Modelle kommen samt Abhängigkeiten aus der Quell-ERP des Katalogs.
    Rückgabe: Bericht mit ``mipmaps`` = [(Name im ZIP, Spieldatei)] fürs Paket."""
    from ego import pkg
    from . import platzierung
    log = protokoll or (lambda *_: None)
    d = laden(daten) if isinstance(daten, (str, os.PathLike)) else normalisieren(daten)
    v = np.asarray(platzierung.VERSATZ if versatz is None else versatz, float)
    bericht = {"objekte": len(d["objekte"]), "bodenbewuchs": d["bodenbewuchs"], "gesetzt": 0, "uebersprungen": [],
               "je_layer": {}, "je_kategorie": {}, "modelle": {}, "ressourcen_neu": {}, "mipmaps": [],
               "mipmaps_mb": 0.0, "offen": []}
    if not d["objekte"]:
        return bericht
    kd = katalog_daten
    index = {}
    modelle = sorted({o["modell"] for o in d["objekte"]})
    fehlen = [m for m in modelle if archiv.find(m + ".idf?model") is None or archiv.find(m + ".emb?context=default") is None]
    if fehlen or kd is not None:
        kd = kd or katalog(spielordner)
        index = katalog_index(kd)
    unbekannt = [m for m in fehlen if m not in index]
    if unbekannt:
        raise UmgebungFehler("Models neither in the carrier nor in the catalogue: %s" % ", ".join(unbekannt[:5]))
    # --- fehlende Modelle samt Abhängigkeiten kopieren (je Quell-ERP ein Index)
    je_quelle: Dict[str, List[str]] = {}
    for m in fehlen:
        je_quelle.setdefault(quelle_pfad(index[m], kd), []).append(m)
    alle_neu, alle_mips = [], {}
    for qpfad, liste in sorted(je_quelle.items()):
        log("Umgebung: %d Modell(e) aus %s kopieren …" % (len(liste), os.path.relpath(qpfad, kd["spielordner"])
                                                           if qpfad.lower().endswith(".erp") else qpfad))
        idx = _ressourcen_index(qpfad)
        try:
            start = []
            for m in liste:
                start += [m + ".idf?model", m + ".idf?render", m + ".emb?context=default"]
            neu, mips, offen = ressourcen_kopieren(archiv, idx, start, code, kd["spielordner"])
        finally:
            idx.schliessen()
        alle_neu += neu
        bericht["offen"] += offen
        for name, datei in mips:
            alle_mips.setdefault(name, datei)
        for m in liste:
            for k in (m + ".idf?model", m + ".idf?render", m + ".emb?context=default"):
                if archiv.find(k) is None:
                    raise UmgebungFehler("Model %s copied incompletely: %s is missing" % (m.rsplit("/", 1)[-1], k))
    bericht["ressourcen_neu"] = dict(Counter(t for t, _ in alle_neu).most_common())
    bericht["ressourcen_neu_summe"] = len(alle_neu)
    bericht["mipmaps"] = sorted(alle_mips.items())
    bericht["mipmaps_mb"] = round(sum(os.path.getsize(f) for f in alle_mips.values()) / 1e6, 1)
    # --- Instanzen
    hoehen = hoehen or hoehenflaechen(archiv)
    pakete = {}
    namen_kat = {}
    for o in d["objekte"]:
        e = index.get(o["modell"])
        kat = e["kategorie"] if e else (kategorie(o["modell"]) or "sonstiges")
        namen_kat[o["id"]] = kat
        layer = LAYER_PFLANZEN if kat in PFLANZEN else LAYER_KULISSE
        if layer not in pakete:
            r, im_traeger = _layer(archiv, layer, code)
            p = pkg.lesen(r.fragments[0].read())
            pakete[layer] = (r, p, _vorlage(p, im_traeger), im_traeger)
        r, p, vi, im_traeger = pakete[layer]
        wx, wz = o["x"] + v[0], o["z"] + v[2]
        y = hoehen.hoehe_nahe(wx, wz)
        if y is None:
            bericht["uebersprungen"].append({"id": o["id"], "grund": "keine Fläche der Fahrphysik unter dem Punkt",
                                             "welt": [round(wx, 2), round(wz, 2)]})
            continue
        y += o["hoehe_m"]
        name = "%s$umg_%s" % (im_traeger, o["id"])
        uid = uuid.uuid5(uuid.NAMESPACE_URL, "ego-strecke:umgebung:" + name).bytes
        inst = pkg.instanz_hinzufuegen(p, vi, [wx, y, wz], name, uid)
        _text_setzen(p, inst.block, ("overrides", "Model", "model"), o["modell"] + ".idf?context=render")
        _text_setzen(p, inst.block, ("overrides", "Model", "mat binding"), o["modell"] + ".emb?context=default")
        pkg.instanz_matrix_setzen(p, inst, lagematrix(wx, y, wz, o["drehung_grad"], o["skala"]))
        bericht["gesetzt"] += 1
        bericht["je_layer"][layer] = bericht["je_layer"].get(layer, 0) + 1
        bericht["je_kategorie"][kat] = bericht["je_kategorie"].get(kat, 0) + 1
        bericht["modelle"][o["modell"].rsplit("/", 1)[-1]] = bericht["modelle"].get(o["modell"].rsplit("/", 1)[-1], 0) + 1
        hb = bericht.setdefault("hoehen_m", [y, y])
        hb[0], hb[1] = min(hb[0], y), max(hb[1], y)
    for layer, (r, p, _vi, _name) in pakete.items():
        p.pruefen()
        r.set_fragment_data(0, pkg.schreiben(p))
    if "hoehen_m" in bericht:
        bericht["hoehen_m"] = [round(x, 2) for x in bericht["hoehen_m"]]
    bericht["kategorien_je_id"] = namen_kat
    return bericht


# =============================================================================== Draufsicht
def _vertexnormalen(p, tri):
    fn = np.cross(p[tri[:, 1]] - p[tri[:, 0]], p[tri[:, 2]] - p[tri[:, 0]])
    if fn[:, 1].sum() < 0:
        fn = -fn
    n = np.zeros_like(p)
    for k in range(3):
        np.add.at(n, tri[:, k], fn)
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)


def berge_einsetzen(archiv, daten, versatz=None, protokoll=None) -> dict:
    """Berge aus ``gelaende.berge`` ins Außenterrain (``*outer_terrain*``, nur sichtbar, keine Fahrphysik).

    Verfahren wie Spieltest T5 (zweite Fassung ✔ 29.10.2026): Kosinusglocke je Berg direkt in den Positionspuffern
    (``grafik.terrain_verformen``, Hüllkörper der Quadtree-Knoten erweitert), danach Vertexnormalen der betroffenen
    Vertices neu. Der Lochrand bleibt bis ``RAND_FEST`` m fest (Übergang ``RAND_UEBERGANG`` m), damit keine Lücke zur
    Grundfläche entsteht."""
    from ego import grafik
    from . import platzierung
    from .spiel_geometrie import _glatt
    berge = (daten.get("gelaende") or {}).get("berge", [])
    if not berge:
        return {"berge": 0}
    v = platzierung.VERSATZ if versatz is None else versatz
    liste = [(f["id"], f["x"] + v[0], f["z"] + v[2], f["radius_m"], f["hoehe_m"]) for f in berge]
    kandidaten = [x for x in archiv if x.resource_type == "TerrainRes" and "outer_terrain" in x.identifier]
    if not kandidaten:
        raise UmgebungFehler("Outer terrain (TerrainRes *outer_terrain*) is missing in the carrier")
    r = kandidaten[0]

    def gewicht(p):
        return _glatt((_lochrand_abstand(p[:, 0], p[:, 2]) - RAND_FEST) / RAND_UEBERGANG)
    knoten, ohne = 0, []
    for fid, cx, cz, rad, h in liste:
        try:
            knoten += grafik.terrain_verformen(archiv, r.identifier, cx, cz, rad, h, gewicht=gewicht)["knoten"]
        except ValueError:      # kein Vertex im Kreis
            ohne.append(fid)
    t = grafik.Terrain.aus_bytes(r.data())
    ib = grafik.GfxPuffer.aus_ressource(archiv.find(t.ib_kennung)).indizes()
    gruppen = {}
    for mi, puffer in enumerate(t.material_puffer):
        vbs = [x for x in puffer if x >= 0]
        if len(vbs) < 2:
            continue
        g = gruppen.setdefault(vbs[0], {"normalen": vbs[1], "tri": []})
        for z in t.zeichnungen:
            if z.material == mi:
                g["tri"].append(ib[z.start_index:z.start_index + z.index_anzahl].astype(np.int64).reshape(-1, 3)
                                + z.basis_vertex)
    geaendert, hoch = 0, -1e9
    for vb_pos, g in gruppen.items():
        if not g["tri"]:
            continue
        pp = grafik.GfxPuffer.aus_ressource(archiv.find(t.vb_kennungen[vb_pos]))
        p = np.frombuffer(pp.daten, "<f4").reshape(-1, 3).astype(np.float64)
        neu = np.zeros(len(p), bool)
        for _, cx, cz, rad, _h in liste:
            neu |= np.hypot(p[:, 0] - cx, p[:, 2] - cz) < rad + 60.0
        if not neu.any():
            continue
        hoch = max(hoch, float(p[neu, 1].max()))
        nres = archiv.find(t.vb_kennungen[g["normalen"]])
        npuf = grafik.GfxPuffer.aus_ressource(nres)
        if npuf.stride != 8:
            raise UmgebungFehler("Normal buffer of the outer terrain has stride %d (expected 8)" % npuf.stride)
        nh = np.frombuffer(npuf.daten, "<f2").reshape(-1, 4).copy()
        nn = _vertexnormalen(p, np.concatenate(g["tri"]))
        nh[neu, :3] = nn[neu].astype(np.float16)
        nres.set_fragment_data(1, nh.astype("<f2").tobytes())
        geaendert += int(neu.sum())
    if protokoll:
        protokoll("Außenterrain: %d Berge, %d Vertices mit neuen Normalen, höchster Punkt %.0f m"
                  % (len(liste) - len(ohne), geaendert, hoch))
    return {"berge": len(liste) - len(ohne), "ohne_vertices": ohne, "terrain": r.identifier, "knoten_erweitert": knoten,
            "vertices_normalen_neu": geaendert, "hoechster_punkt_m": round(hoch, 1)}


def karte(pfad_png, daten, netz=None, versatz=None, katalog_daten=None, logik=None, mass=0.8, titel=""):
    """Draufsicht (Welt, x rechts, z unten): Streckennetz (Generator, verschoben), KI-Linie, Lochrahmen und
    alle Objekte als Kreise (Radius aus dem Katalog × Maßstab, mindestens 2 px) in der Farbe ihrer Kategorie."""
    from PIL import Image, ImageDraw, ImageFont
    from . import platzierung
    d = laden(daten) if isinstance(daten, (str, os.PathLike)) else normalisieren(daten)
    v = np.asarray(platzierung.VERSATZ if versatz is None else versatz, float)
    index = katalog_index(katalog_daten) if katalog_daten else {}
    x0, z0, kante = LOCH
    W = H = int(kante * mass)
    im = Image.new("RGB", (W, H + 46), (24, 30, 26))
    dr = ImageDraw.Draw(im)
    try:
        schrift = ImageFont.truetype(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arial.ttf"), 13)
    except OSError:
        schrift = ImageFont.load_default()

    def P(x, z):
        return ((x - x0) * mass, (z - z0) * mass)
    dr.rectangle([P(x0, z0), P(x0 + kante - 1, z0 + kante - 1)], outline=(80, 80, 140))
    n = _netz_aus(netz) if not isinstance(netz, tuple) else netz
    if n is not None:
        vv, tri, surf, namen = n
        farben = {"asphalt": (75, 75, 80), "randstein": (200, 40, 40), "gras": (55, 95, 50), "kies": (190, 170, 120),
                  "mauer": (210, 210, 210), "boxengasse": (110, 110, 118), "linie": (235, 235, 235)}
        for si in sorted(range(len(namen)), key=lambda i: namen[i] == "mauer"):
            f = farben.get(namen[si], (120, 120, 120))
            for t in tri[surf == si]:
                dr.polygon([P(vv[j, 0] + v[0], vv[j, 2] + v[2]) for j in t], fill=f)
    if logik is not None:
        for s in logik["spielformate"]["TrackSpace"]["splines"]:
            pts = [P(p[0], p[2]) for p in s["position"]]
            dr.line(pts + ([pts[0]] if s.get("closed") else []), fill=(230, 60, 160), width=1)
    zahl = Counter()
    for o in d["objekte"]:
        e = index.get(o["modell"])
        kat = e["kategorie"] if e else (kategorie(o["modell"]) or "sonstiges")
        zahl[kat] += 1
        r = max(2.0, (e["radius_m"] if e else 2.0) * o["skala"] * mass)
        x, z = P(o["x"] + v[0], o["z"] + v[2])
        dr.ellipse([x - r, z - r, x + r, z + r], outline=FARBEN[kat], width=2)
        dr.point((x, z), fill=FARBEN[kat])
    dr.text((8, H + 4), titel or "Umgebung: %d Objekte (Welt, x rechts, z unten)" % len(d["objekte"]),
            fill=(230, 230, 230), font=schrift)
    x = 8
    for kat in KATEGORIEN:
        if zahl[kat]:
            dr.ellipse([x, H + 26, x + 10, H + 36], fill=FARBEN[kat])
            t = "%s %d" % (kat, zahl[kat])
            dr.text((x + 14, H + 24), t, fill=(210, 210, 210), font=schrift)
            x += 24 + int(dr.textlength(t, font=schrift))
    os.makedirs(os.path.dirname(os.path.abspath(pfad_png)), exist_ok=True)
    im.save(pfad_png)
    return pfad_png
