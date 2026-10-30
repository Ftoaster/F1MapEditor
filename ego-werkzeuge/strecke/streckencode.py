"""Streckencode einer fertigen Strecken-ERP bzw. eines Mod-Pakets umbenennen (AP16).

Eine Strecke im Portimão-Träger trägt den Code ``portimao`` in Kennungen, Verweisen und
Dateipfaden. Dieses Modul benennt alles, was zur Strecke gehört, konsistent um, damit sie unter
einem anderen Code installiert werden kann und nicht mehr den Portimão-Platz belegt.

Umbenannt werden (``<alt>`` → ``<neu>``):

    Kennungen   eaid://environment_package/tracks/<alt>/…   Ordner und <alt> als Wort im Rest
                                                             (portimao_common.wep, portimao-ai.layer,
                                                             trackinfo_portimao.trackinfobin …)
                eaid://ui_package/art/textures/track_installer/<alt>/…
                eaid://ui_package/art/osd/textures/*/<alt>_ai_trackspace.tif(.image)
                eaid://ui_package/art/textures/car_monitor/*/<alt>_ai_trackspace.tif(.image)
                eaid://game_package/practice_programs/track_acclimatisation/<alt>.taprogram
    Verweise    GfxMeshRes (Puffer), GfxModelRes (Meshes), GfxMatBindRes (Materialien),
                GfxMaterialRes (Texturen inkl. Heap-Liste), GfxSRVResource (Bild), TerrainRes
                (Puffer, Materialien), pkg-Zeichenketten (Welt, Layer, Zuschauer, KI-Daten)
    Mip-Pfade   GfxSurfaceRes Fragment 2: asset_groups/environment_package/tracks/<alt>/mipmaps/…
                (Dateinamen bleiben, nur der Ordner = Kopierziel des Pakets ändert sich)
    Namen       Weltname in World-Ressourcen (portimao_common → <neu>_common),
                Feld ``track`` der KI-Daten (FuelBurnGran, TyreWearData, ERSBoost, ERSUseGran)

Bewusst **nicht** umbenannt (Begründung in docs/streckencode.md): kopierte core_assets
(``rolex_clock_led_portimao.material``, ``…emb?context=portimao`` u. a. – identisch mit Spiel/Mod),
Instanz-, Knoten-, Slot- und PVS-Objektnamen (``portimao_terrain_sponsors``,
``buildings$portimao_mill_tribune01…`` – reine Namen, gegenseitig verknüpft), Dateinamen der
mipmaps. Verweise über Hashes der Kennungen gibt es nicht (geprüft: FNV-1/1a 32/64, CRC32).
Der MD5 einer Ressource hängt nur von den Daten ab; geänderte Ressourcen erhalten einen neuen MD5.

Voraussetzung im Spiel (siehe docs/streckencode.md): Der Track Installer aktiviert nur Codes, die
in der Spieldatenbank (gamedatabases.2025.bin) als Strecke vorhanden sind.
"""

import copy
import heapq
import json
import os
import re
import struct
import time
from collections import Counter, defaultdict
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ego import erp, grafik, paket, parallel, pkg

STANDARD_ALT = "portimao"
CODE_MUSTER = re.compile(r"^[a-z0-9_]+$")  # Regel des Track Installers (Manifest track_code)

# Strecken des Spiels (Ordner unter 2025_asset_groups/environment_package/tracks): Diese Codes
# laden die Original-Assets und dürfen nicht verwendet werden.
SPIELSTRECKEN = frozenset((
    "abu_dhabi austria austria_reverse bahrain baku brazil catalunya hungaroring imola jeddah "
    "las_vegas losail madrid melbourne mexico miami monaco montreal monza shanghai silverstone "
    "silverstone_reverse singapore spa_francorchamps suzuka texas zandvoort zandvoort_reverse").split())
# Ladevorlage, die der Installer für alle installierten Strecken umbiegt.
VERBOTEN = frozenset(("ai_test_track", "frontend", "common_all_tracks"))
# In der Original-Spieldatenbank vorhandene, im Spiel nicht mehr genutzte Strecken (Auswertung
# gamedatabases.2025.bin vom 16.10.2026, docs/streckencode.md). Zahl = Einträge Typ 2/Typ 4.
DB_ALTSTRECKEN = {
    "portimao": "48/25, belegt durch Portimão-Mod bzw. Teststrecke bis v17",
    "bahrain24": "20/2, belegt durch Sepang-Mod",
    "sochi": "44/26, fast vollständige Streckendaten",
    "hanoi": "32/11",
    "paul_ricard": "27/21, in alten Ereignislisten genannt",
    "styria": "15/15, vermutlich Ordner von austria (riskant)",
    "hockenheim": "16/0",
    "sepang": "18/0",
    "silverstone_int": "15/10, Variante von silverstone (riskant)",
    "suzuka_east": "15/6, Variante von suzuka (riskant)",
    "texas_nat": "15/6, Variante von texas (riskant)",
    "bahrain_pad": "15/6, Variante von bahrain (riskant)",
}
KI_TYPEN_MIT_STRECKE = frozenset(("FuelBurnGran", "TyreWearData", "ERSBoost", "ERSUseGran"))


class StreckencodeFehler(ValueError):
    """Umbenennung nicht möglich oder Prüfung fehlgeschlagen."""


def code_bewerten(code: str) -> Tuple[bool, str]:
    """(zulässig, Hinweis). Unzulässig: Formatfehler, Spielstrecke, Installer-Vorlage."""
    if not CODE_MUSTER.match(code or ""):
        return False, "Code darf nur Kleinbuchstaben, Ziffern und Unterstrich enthalten (Installer-Regel)"
    if len(code) > 40:
        return False, "Code zu lang (höchstens 40 Zeichen)"
    if code in SPIELSTRECKEN:
        return False, "Code einer Strecke des Spiels – würde die Original-Strecke laden"
    if code in VERBOTEN:
        return False, "Code ist reserviert (Ladevorlage bzw. gemeinsame Dateien des Spiels)"
    if code in DB_ALTSTRECKEN:
        return True, "in der Spieldatenbank vorhanden (%s)" % DB_ALTSTRECKEN[code]
    return True, ("WARNUNG: Code ist in der Spieldatenbank nicht vorhanden. Der Track Installer kopiert "
                  "die Dateien, überspringt die Strecke aber bei der Datenbank-Aktivierung "
                  "(„Track not found in database“) – im Spiel nicht auswählbar.")


# =============================================================================== Regeln
class Umbenennung:
    """Abbildung alter → neuer Zeichenketten für einen Codewechsel."""

    def __init__(self, alt: str, neu: str):
        for c in (alt, neu):
            if not CODE_MUSTER.match(c or ""):
                raise StreckencodeFehler("Ungültiger Streckencode: %r" % c)
        if alt == neu:
            raise StreckencodeFehler("Alter und neuer Code sind gleich (%s)" % alt)
        self.alt, self.neu = alt, neu
        self.alt_b = alt.encode()
        self._praefixe = (
            ("eaid://environment_package/tracks/%s/" % alt, "eaid://environment_package/tracks/%s/" % neu, True),
            ("eaid://ui_package/art/textures/track_installer/%s/" % alt,
             "eaid://ui_package/art/textures/track_installer/%s/" % neu, False),
            ("asset_groups/environment_package/tracks/%s/mipmaps/" % alt,
             "asset_groups/environment_package/tracks/%s/mipmaps/" % neu, False),
        )
        self._karte = re.compile(r"^(eaid://ui_package/art/(?:osd/textures|textures/car_monitor)/[a-z0-9_]+/)%s"
                                 r"(_ai_trackspace\.tif(?:\.image)?)$" % re.escape(alt))
        self._ta = "eaid://game_package/practice_programs/track_acclimatisation/%s.taprogram"
        self._welt = re.compile(r"^%s(_[a-z0-9_]+)$" % re.escape(alt))
        # Code im Pfadrest nur als eigenes Wort (Grenzen: Anfang/Ende, / _ - . ?), nie innerhalb
        # anderer Wörter (Code "x" darf "textures" nicht verändern)
        self._wort = re.compile(r"(?<![a-zA-Z0-9])%s(?![a-zA-Z0-9])" % re.escape(alt))
        # verbotene Reste nach der Umbenennung (Bytesuche in allen Daten)
        self.reste = (b"tracks/" + self.alt_b + b"/", b"track_installer/" + self.alt_b + b"/",
                      b"/" + self.alt_b + b"_ai_trackspace", b"track_acclimatisation/" + self.alt_b + b".")

    def kennung(self, k: str) -> str:
        """Kennung bzw. Pfad umbenennen; alles andere bleibt unverändert."""
        for a, n, rest in self._praefixe:
            if k.startswith(a):
                r = k[len(a):]
                return n + (self._wort.sub(self.neu, r) if rest else r)
        m = self._karte.match(k)
        if m:
            return m.group(1) + self.neu + m.group(2)
        if k == self._ta % self.alt:
            return self._ta % self.neu
        return k

    def pkg_text(self, s: str, typ: str) -> str:
        if s.startswith("eaid://") or s.startswith("asset_groups/"):
            return self.kennung(s)
        if typ == "World":
            m = self._welt.match(s)
            if m:
                return self.neu + m.group(1)
        if typ in KI_TYPEN_MIT_STRECKE and s == self.alt:
            return self.neu
        return s

    def json(self, obj):
        """Alle Zeichenketten eines JSON-Objekts (images.json, minimap.json) abbilden."""
        if isinstance(obj, dict):
            return {k: self.json(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.json(v) for v in obj]
        if isinstance(obj, str):
            return self.kennung(obj)
        return obj


# =============================================================================== Formate
def _cstrs(d: bytes, a: int, b: int) -> List[Tuple[int, bytes]]:
    """Nullterminierte Zeichenketten in d[a:b] als (relativer Offset, Bytes)."""
    out, pos = [], a
    while pos < b:
        e = d.index(b"\0", pos)
        out.append((pos - a, d[pos:e]))
        pos = e + 1
    if pos != b:
        raise StreckencodeFehler("Zeichenkettenbereich endet nicht sauber")
    return out


def _pool_neu(liste: List[Tuple[int, bytes]], f: Callable[[str], str]) -> Tuple[Dict[int, int], bytes, Dict[int, int]]:
    """Zeichenketten abbilden und neu aneinanderreihen: (alt→neu Offset, Blob, neuer Offset→Länge)."""
    offs, teile, laengen, p = {}, [], {}, 0
    for o, s in liste:
        s2 = f(s.decode("utf-8", "surrogateescape")).encode("utf-8", "surrogateescape")
        offs[o] = p
        laengen[p] = len(s2)
        teile.append(s2 + b"\0")
        p += len(s2) + 1
    return offs, b"".join(teile), laengen


def _rundlauf(klasse, d: bytes, was: str):
    obj = klasse.aus_bytes(d)
    if obj.to_bytes() != d:
        raise StreckencodeFehler("%s: Rundlauf nicht bitgleich – Umbenennen nicht sicher" % was)
    return obj


def mesh_umbenennen(d: bytes, f) -> Optional[bytes]:
    m = _rundlauf(grafik.GfxMesh, d, "GfxMeshRes")
    vb, ib = [f(k) for k in m.vb_kennungen], [f(k) for k in m.ib_kennungen]
    if vb == m.vb_kennungen and ib == m.ib_kennungen:
        return None
    m.vb_kennungen, m.ib_kennungen = vb, ib
    return m.to_bytes()


def modell_umbenennen(d: bytes, f) -> Optional[bytes]:
    m = grafik.GfxModell.aus_bytes(d)
    if m.rest is not None:
        raise StreckencodeFehler("GfxModelRes mit nicht entschlüsseltem Rest")
    if m.to_bytes() != d:
        raise StreckencodeFehler("GfxModelRes: Rundlauf nicht bitgleich")
    neu = [f(k) for k in m.mesh_kennungen]
    if neu == m.mesh_kennungen:
        return None
    m.mesh_kennungen = neu
    return m.to_bytes()


def matbind_umbenennen(d: bytes, f) -> Optional[bytes]:
    b = _rundlauf(grafik.MaterialBindung, d, "GfxMatBindRes")
    neu = [f(k) for k in b.materialien]
    if neu == b.materialien:
        return None
    b.materialien = neu
    return b.to_bytes()


def sicht_umbenennen(d: bytes, f) -> Optional[bytes]:
    s = _rundlauf(grafik.TexturSicht, d, "GfxSRVResource")
    neu = f(s.bild)
    if neu == s.bild:
        return None
    s.bild = neu
    return s.to_bytes()


def material_umbenennen(d: bytes, f) -> Optional[bytes]:
    """GfxMaterialRes: Textur- und Heap-Texturliste neu schreiben, Offsets/Längen nachführen.
    Aufbau (geprüft an allen 724 Materialien der Teststrecke): Bereiche Shader, Parameternamen,
    Texturen, Heap-Texturen, Puffernamen lückenlos in dieser Reihenfolge."""
    K, P = grafik.MATERIAL_KOPF, grafik.MATERIAL_PARAM
    n, ncb, wg, osh, onam, otex, oheap, ocb = K.unpack_from(d, 0)
    basis = K.size + P.size * n
    if not (wg + 8 * ncb == osh <= onam <= otex <= oheap <= ocb <= len(d) - basis):
        raise StreckencodeFehler("GfxMaterialRes: unerwartete Bereichsfolge")
    tex = _cstrs(d, basis + otex, basis + oheap)
    heap = _cstrs(d, basis + oheap, basis + ocb)
    t_offs, t_blob, t_len = _pool_neu(tex, f)
    h_offs, h_blob, _ = _pool_neu(heap, f)
    if t_blob == d[basis + otex:basis + oheap] and h_blob == d[basis + oheap:basis + ocb]:
        return None
    params = bytearray(d[K.size:basis])
    for i in range(n):
        p = list(P.unpack_from(d, K.size + P.size * i))
        if p[1] == 2:  # Textur: Offset in der Texturliste, Größe = Länge inkl. Null
            if p[7] not in t_offs:
                raise StreckencodeFehler("GfxMaterialRes: Texturoffset %d ungültig" % p[7])
            p[7] = t_offs[p[7]]
            p[4] = t_len[p[7]] + 1
        if p[3] == 3:  # Heap-Index einer Textur
            if p[8] not in h_offs:
                raise StreckencodeFehler("GfxMaterialRes: Heap-Offset %d ungültig" % p[8])
            p[8] = h_offs[p[8]]
        P.pack_into(params, P.size * i, *p)
    neu_heap = otex + len(t_blob)
    neu_cb = neu_heap + len(h_blob)
    return (K.pack(n, ncb, wg, osh, onam, otex, neu_heap, neu_cb) + bytes(params)
            + d[basis:basis + otex] + t_blob + h_blob + d[basis + ocb:])


def mip_umbenennen(d: bytes, f) -> Optional[bytes]:
    """GfxSurfaceRes Fragment 2: Längenbyte, Pfad, Null, Rest (Stufentabelle) unverändert."""
    laenge = d[0]
    if d[1 + laenge] != 0:
        raise StreckencodeFehler("Mip-Verweis ohne Nullbyte")
    alt = d[1:1 + laenge].decode("utf-8", "surrogateescape")
    neu = f(alt)
    if neu == alt:
        return None
    nb = neu.encode("utf-8", "surrogateescape")
    if len(nb) > 255:
        raise StreckencodeFehler("Mip-Pfad länger als 255 Byte: %s" % neu)
    return bytes([len(nb)]) + nb + d[1 + laenge:]


def terrain_umbenennen(d: bytes, f) -> Optional[bytes]:
    """TerrainRes: Zeichenkettenpool abbilden, gemeinsam genutzte Einträge bleiben gemeinsam."""
    t = _rundlauf(grafik.Terrain, d, "TerrainRes")
    texte, blob, offs = t._zeichenketten
    pool = _cstrs(blob, 0, len(blob))
    p_offs, p_blob, _ = _pool_neu(pool, f)
    if p_blob == blob:
        return None
    neu_offs = [p_offs[o] for o in offs]
    neu_texte = [f(x) for x in texte]
    t._zeichenketten = (neu_texte, p_blob, neu_offs)
    nv = len(t.vb_kennungen)
    t.ib_kennung, t.vb_kennungen, t.materialien = neu_texte[0], neu_texte[1:1 + nv], neu_texte[1 + nv:]
    return t.to_bytes()


def pkg_umbenennen(d: bytes, f) -> Optional[bytes]:
    """pkg: alle !sbi-Zeichenketten abbilden, Verweise (Byteoffsets) und Feldhashes nachführen."""
    p = pkg.lesen(d)
    vca = p.tabellen.ziel
    tabellen = {i: v.ziel for i, v in enumerate(vca.werte) if v.ziel is not None and v.ziel.kennung == b"!sbi"}
    abbildung, geaendert = {}, False
    for i, b in tabellen.items():
        D, pos, liste = b.daten, 0, []
        while pos < len(D):
            n = D[pos]
            if pos + n + 2 > len(D) or D[pos + n + 1] != 0:
                raise StreckencodeFehler("pkg: !sbi ist keine reine Zeichenkettenfolge")
            liste.append((pos, D[pos + 1:pos + 1 + n]))
            pos += n + 2
        offs, teile, q = {}, [], 0
        for o, s in liste:
            s2 = f(s.decode("utf-8", "surrogateescape")).encode("utf-8", "surrogateescape")
            if len(s2) > 255:
                raise StreckencodeFehler("pkg-Zeichenkette länger als 255 Byte")
            offs[o] = q
            teile.append(bytes([len(s2)]) + s2 + b"\0")
            q += len(s2) + 2
        neu = b"".join(teile)
        geaendert |= neu != D
        abbildung[i] = (offs, neu)
    if not geaendert:
        return None
    alte_namen = {}
    for b in p.bloecke:
        for feld in b.felder:
            if feld.hash is not None:
                alte_namen[id(feld)] = p.wert(feld.name)
    for b in p.bloecke:
        for r in b.verweise():
            if r.typ != 0x80 and r.typ in abbildung:
                offs = abbildung[r.typ][0]
                if r.index not in offs:
                    raise StreckencodeFehler("pkg: Verweis zeigt nicht auf einen Zeichenkettenanfang")
                r.index = offs[r.index]
    for i, (_, neu) in abbildung.items():
        tabellen[i].daten = neu
    for b in p.bloecke:
        for feld in b.felder:
            if feld.hash is not None:
                name = p.wert(feld.name)
                if name != alte_namen[id(feld)]:
                    feld.hash = pkg.fnv1a(name)
    neu = pkg.schreiben(p)
    pkg.lesen(neu).pruefen()
    return neu


# =============================================================================== ERP
GRAFIK = {"GfxMeshRes": mesh_umbenennen, "GfxModelRes": modell_umbenennen, "GfxMatBindRes": matbind_umbenennen,
          "GfxMaterialRes": material_umbenennen, "GfxSRVResource": sicht_umbenennen, "TerrainRes": terrain_umbenennen}


def _umbenennen_fragment(auftrag):
    """Arbeitsprozess (``ego.parallel``): ein Fragment mit dem alten Code umbenennen.

    Auftrag: (Kennung, Typ, Fragmentindex, entpackte Daten); gemeinsam: die ``Umbenennung``. Ergebnis:
    (neue Daten oder None, gepackt oder None, alter Code bleibt erlaubt übrig, Beispiel dafür oder None)."""
    kennung, typ, i, d = auftrag
    regeln = parallel.gemeinsam()
    neu = None
    if d[:4] == b"!pkg":
        neu = pkg_umbenennen(d, lambda s: regeln.pkg_text(s, typ))
    elif typ == "GfxSurfaceRes":
        neu = mip_umbenennen(d, regeln.kennung)
    elif typ in GRAFIK and i == 0:
        neu = GRAFIK[typ](d, regeln.kennung)
    if neu is not None:
        d = neu
    for muster in regeln.reste:
        if muster in d:
            raise StreckencodeFehler("%s (%s): nicht unterstützter Verweis mit %r bleibt übrig"
                                     % (kennung, typ, muster))
    beispiel, rest = None, regeln.alt_b in d
    if rest:
        m = re.search(rb"[\x21-\x7e]{0,40}" + re.escape(regeln.alt_b) + rb"[\x21-\x7e]{0,30}", d)
        beispiel = m.group().decode("ascii", "replace")[-70:] if m else None
    return neu, (erp.fragment_packen(neu) if neu is not None else None), rest, beispiel


def archiv_umbenennen(a: "erp.ErpFile", regeln: Umbenennung) -> dict:
    """Benennt im geöffneten Archiv um (in place). Gibt einen Bericht zurück.

    26.10.2026: Fragmente mit dem alten Code werden parallel umbenannt und gepackt (pkg-Parser in reinem
    Python); Einsetzen, MD5 und Kennungen danach in Archivreihenfolge, Ergebnis wie vorher."""
    t0 = time.time()
    kennungen, daten, rest_erlaubt = Counter(), Counter(), Counter()
    rest_beispiele = defaultdict(set)
    alt_b = regeln.alt_b
    vorhanden = {r.identifier for r in a}
    neue_kennungen = set()
    orte, auftraege = [], []
    for k, r in enumerate(a):
        for i, fr in enumerate(r.fragments):
            if r.resource_type == "GfxBufferRes" or (r.resource_type == "GfxSurfaceRes" and i != 2):
                continue  # Rohdaten ohne Zeichenketten
            d = fr.read()
            if alt_b in d:
                orte.append((k, i))
                auftraege.append((r.identifier, r.resource_type, i, d))
    ergebnisse = dict(zip(orte, parallel.abbilden(_umbenennen_fragment, auftraege, regeln,
                                                  aufwand=[len(a[3]) for a in auftraege])))
    del auftraege
    for k, r in enumerate(a):
        # --- Daten
        for i, fr in enumerate(r.fragments):
            if (k, i) not in ergebnisse:
                continue
            neu, gepackt, rest, beispiel = ergebnisse[(k, i)]
            if neu is not None:
                fr.set_data(neu, gepackt=gepackt)
                daten[r.resource_type] += 1
            if rest:
                rest_erlaubt[r.resource_type] += 1
                if len(rest_beispiele[r.resource_type]) < 3 and beispiel is not None:
                    rest_beispiele[r.resource_type].add(beispiel)
        if r.modified:
            r.update_hash()
        # --- Kennung
        neu_k = regeln.kennung(r.identifier)
        if neu_k != r.identifier:
            if neu_k in vorhanden or neu_k in neue_kennungen:
                raise StreckencodeFehler("Kennung nach Umbenennung doppelt: %s" % neu_k)
            neue_kennungen.add(neu_k)
            r.identifier = neu_k
            kennungen[r.resource_type] += 1
    rest_kennungen = sorted(r.identifier for r in a if regeln.alt in r.identifier)
    return {"alt": regeln.alt, "neu": regeln.neu, "sekunden": round(time.time() - t0, 1),
            "kennungen_umbenannt": dict(kennungen.most_common()), "kennungen_summe": sum(kennungen.values()),
            "daten_geaendert": dict(daten.most_common()), "daten_summe": sum(daten.values()),
            "rest_in_daten": {k: {"ressourcen": n, "beispiele": sorted(rest_beispiele[k])}
                              for k, n in rest_erlaubt.most_common()},
            "rest_kennungen": rest_kennungen}


def _verweise_ressource(r) -> Tuple[List[str], Optional[str]]:
    """Verweise (eaid-Texte) einer Ressource und ggf. ihr Mip-Pfad."""
    t = r.resource_type
    if t == "GfxBufferRes":
        return [], None
    if t == "GfxSurfaceRes":
        if len(r.fragments) > 2:
            d = r.fragments[2].read()
            return [], d[1:1 + d[0]].decode("utf-8", "surrogateescape")
        return [], None
    texte = []
    for i, fr in enumerate(r.fragments):
        d = fr.read()
        if d[:4] == b"!pkg":
            for b in pkg.zeichenketten_bloecke(d):          # wie pkg.lesen(d).bloecke mit Kennung !sbi
                pos = 0
                while pos < len(b):
                    n = b[pos]
                    s = b[pos + 1:pos + 1 + n].decode("utf-8", "surrogateescape")
                    if s.startswith("eaid://"):
                        texte.append(s)
                    pos += n + 2
        elif i == 0 and t == "GfxMeshRes":
            m = grafik.GfxMesh.aus_bytes(d)
            texte += m.vb_kennungen + m.ib_kennungen
        elif i == 0 and t == "GfxModelRes":
            texte += grafik.GfxModell.aus_bytes(d).mesh_kennungen
        elif i == 0 and t == "GfxMatBindRes":
            texte += grafik.MaterialBindung.aus_bytes(d).materialien
        elif i == 0 and t == "GfxMaterialRes":
            texte += [x for x in grafik.Material.aus_bytes(d).texturen().values() if x]
            texte += [p.textur for p in grafik.Material.aus_bytes(d).parameter if p.klasse == 3 and p.textur]
        elif i == 0 and t == "GfxSRVResource":
            texte.append(grafik.TexturSicht.aus_bytes(d).bild)
        elif i == 0 and t == "TerrainRes":
            tr = grafik.Terrain.aus_bytes(d)
            texte += [tr.ib_kennung] + tr.vb_kennungen + tr.materialien
    return texte, None


def _verweise_teil(auftrag):
    """Arbeitsprozess: (ERP-Pfad, Ressourcenindizes) → [(Index, Texte, Mip-Pfad), …]."""
    pfad, indizes = auftrag
    with erp.open_erp(pfad) as a:
        return [(i, *_verweise_ressource(a.resources[i])) for i in indizes]


def _verweis_aufwand(r) -> int:
    """Grobe Kosten von _verweise_ressource: entpackte Bytes, die gelesen und geparst werden."""
    if r.resource_type == "GfxBufferRes":
        return 0
    if r.resource_type == "GfxSurfaceRes":
        return r.fragments[2].size if len(r.fragments) > 2 else 0
    return 1000 + sum(f.size for f in r.fragments)


def verweise_sammeln_mehrere(pfade: Sequence[str]) -> List[dict]:
    """verweise_sammeln für mehrere ERPs zugleich, verteilt auf alle Kerne (26.10.2026; der pkg-Parser
    ist reines Python, vorher ≈ 40 s je Träger-ERP auf einem Kern). Ressourcen nach Datenmenge auf die
    Aufträge verteilt (größte zuerst in den leichtesten Auftrag). Ergebnis gleich dem seriellen Lauf."""
    n = parallel.anzahl()
    teile = 2 * n if n > 1 else 1
    auftraege, besitzer, ids = [], [], []
    for pfad in pfade:
        with erp.open_erp(pfad) as a:
            ids.append({r.identifier for r in a})
            aufwand = sorted(((_verweis_aufwand(r), i) for i, r in enumerate(a.resources)), reverse=True)
        stapel, last = [[] for _ in range(teile)], [(0, k) for k in range(teile)]
        for kosten, i in aufwand:
            summe, k = heapq.heappop(last)
            stapel[k].append(i)
            heapq.heappush(last, (summe + kosten, k))
        besitzer += [len(ids) - 1] * sum(1 for s in stapel if s)
        auftraege += [(pfad, sorted(s)) for s in stapel if s]
    ergebnisse = parallel.abbilden(_verweise_teil, auftraege)
    aus = []
    for j in range(len(pfade)):
        gesamt, offen, mips = 0, Counter(), Counter()
        eintraege = (x for b, teil in zip(besitzer, ergebnisse) if b == j for x in teil)
        for _, texte, mip in sorted(eintraege, key=lambda x: x[0]):
            if mip is not None:
                mips[mip] += 1
            for s in texte:
                gesamt += 1
                if s not in ids[j]:
                    offen[s] += 1
        aus.append({"verweise": gesamt, "offen": offen, "mips": mips})
    return aus


def verweise_sammeln(pfad: str) -> dict:
    """Alle Verweise der unterstützten Typen und deren Auflösbarkeit in der ERP (nur lesend)."""
    return verweise_sammeln_mehrere([pfad])[0]


def erp_umbenennen(quelle: str, ziel: str, regeln: Umbenennung, pruefen: bool = True,
                   mip_namen: Optional[set] = None, protokoll=print) -> dict:
    """Quelle lesen, umbenennen, nach ziel schreiben und prüfen. Die Quelle bleibt unverändert."""
    if os.path.abspath(quelle) == os.path.abspath(ziel):
        raise StreckencodeFehler("Ziel darf nicht die Quelle sein")
    with erp.open_erp(quelle) as a:
        md5_falsch_vorher = erp.md5_falsch(a)
        protokoll("Umbenennen %s -> %s …" % (regeln.alt, regeln.neu))
        bericht = archiv_umbenennen(a, regeln)
        protokoll("  %d Kennungen, %d Ressourcen mit geänderten Daten (%.0f s)" % (
            bericht["kennungen_summe"], bericht["daten_summe"], bericht["sekunden"]))
        a.write(ziel, overwrite=True)
    bericht["md5_falsch_quelle"] = md5_falsch_vorher
    if pruefen:
        protokoll("Prüfen …")
        bericht["pruefung"] = erp_pruefen(quelle, ziel, regeln, mip_namen)
    return bericht


def erp_pruefen(quelle: str, ziel: str, regeln: Umbenennung, mip_namen: Optional[set] = None) -> List[Tuple[str, bool, str]]:
    """Prüfungen nach dem Umbenennen: Lesbarkeit, Lücken, MD5, Reste, Verweise, Mip-Pfade."""
    erg = []

    def melde(name, ok, text=""):
        erg.append((name, bool(ok), text))

    with erp.open_erp(quelle) as a, erp.open_erp(ziel) as b:
        melde("Ressourcenzahl gleich", len(a) == len(b), "%d / %d" % (len(a), len(b)))
        melde("ERP lückenlos", b.layout_contiguous and b.declared_fragment_count == b.fragment_count)
        falsch = erp.md5_falsch(b)
        melde("MD5 aller Ressourcen", not falsch, "alle korrekt" if not falsch else "%d falsch: %s" % (
            len(falsch), "; ".join(falsch[:3])))
        abb = [(ra.identifier, rb.identifier, ra.resource_type == rb.resource_type) for ra, rb in zip(a, b)]
        melde("Reihenfolge/Typen erhalten, Kennungen = Regel",
              all(t and regeln.kennung(x) == y for x, y, t in abb), "%d geprüft" % len(abb))
        ids = [r.identifier for r in b]
        melde("Kennungen eindeutig", len(ids) == len(set(ids)))
        alt_reste = [k for k in ids if any(m.decode() in k for m in regeln.reste)]
        melde("keine alten Streckenkennungen", not alt_reste, ", ".join(alt_reste[:3]) or "0")
        welt = "eaid://environment_package/tracks/%s/wep/%s_common.wep" % (regeln.neu, regeln.neu)
        melde("Welt %s_common.wep vorhanden" % regeln.neu, any(k == welt for k in ids))
        reste = Counter()
        for r in b:
            if r.resource_type == "GfxBufferRes":
                continue
            for i, fr in enumerate(r.fragments):
                if r.resource_type == "GfxSurfaceRes" and i == 1:
                    continue
                d = fr.read()
                for m in regeln.reste:
                    if m in d:
                        reste[(r.resource_type, m.decode())] += 1
        melde("keine alten Verweise in den Daten", not reste,
              "0" if not reste else "; ".join("%s %s: %d" % (k[0], k[1], n) for k, n in reste.items()))
    va, vb = verweise_sammeln_mehrere([quelle, ziel])
    offen_erwartet = Counter({regeln.kennung(k): n for k, n in va["offen"].items()})
    melde("Verweise auflösbar wie vorher", va["verweise"] == vb["verweise"] and vb["offen"] == offen_erwartet,
          "%d Verweise, %d aufgelöst, %d extern (Spiel/core_assets, wie vorher)" % (
              vb["verweise"], vb["verweise"] - sum(vb["offen"].values()), sum(vb["offen"].values())))
    mip_alt = Counter({regeln.kennung(k): n for k, n in va["mips"].items()})
    ordner = "asset_groups/environment_package/tracks/%s/mipmaps/" % regeln.neu
    eigene = {k: n for k, n in vb["mips"].items() if k.startswith("asset_groups/")}
    falscher_ordner = [k for k in eigene if not k.startswith(ordner)]
    text = "%d Texturen -> %s" % (sum(eigene.values()), ordner)
    ok = vb["mips"] == mip_alt and not falscher_ordner
    if mip_namen is not None:
        fehlen = sorted(k.rsplit("/", 1)[1] for k in eigene if k.rsplit("/", 1)[1] not in mip_namen)
        ok &= not fehlen
        text += ", alle Dateien im Paket" if not fehlen else ", fehlen im Paket: %s" % ", ".join(fehlen[:3])
    melde("Mip-Pfade = Kopierziel", ok, text)
    return erg


# =============================================================================== Paket
def manifest_umbenennen(manifest: dict, regeln: Umbenennung, name: Optional[str] = None,
                        aktivierung_vorlage: Optional[str] = None) -> dict:
    m = copy.deepcopy(manifest)
    if m.get("track_code") != regeln.alt:
        raise StreckencodeFehler("Manifest hat track_code %r statt %r" % (m.get("track_code"), regeln.alt))
    m["track_code"] = regeln.neu
    if name:
        m["track_name"] = name
    m["erp"] = regeln.neu + ".erp"
    for c in m.get("copy_directories", []):
        alt = "asset_groups/environment_package/tracks/%s/mipmaps/" % regeln.alt
        if c.get("destination") != alt:
            raise StreckencodeFehler("Unerwartetes Kopierziel %r" % c.get("destination"))
        c["destination"] = "asset_groups/environment_package/tracks/%s/mipmaps/" % regeln.neu
    if aktivierung_vorlage:
        akt = m.setdefault("activation", {})
        akt["copy_from_track"] = aktivierung_vorlage
        akt.setdefault("overrides", [])
        akt.setdefault("track_overrides", [])
    return m


#: Streckenmetadaten im Manifest (``activation.track_overrides``), Schlüssel wie im Sepang-Manifest – nur
#: diese drei sind im Installer belegt (docs/streckenname.md). Werte schreibt der Installer als Text.
METADATEN_SCHLUESSEL = ("length_metres", "numberOfCorners", "allow_night_start")


def metadaten_aus_logik(logik: dict, kurven_im_spiel: Optional[int] = None) -> dict:
    """Metadaten aus der berechneten Streckenlogik: Länge (m, gerundet), Zahl der Kurven, keine Nachtrennen
    (der Träger hat keine Flutlichter).

    ``kurven_im_spiel``: Hauptkurven der KI-Daten nach dem Zusammenlegen auf die Bremslinien-IDs des Trägers
    (``spiel_welt.kurven_begrenzen``, Portimão höchstens 15). Die Datenbank soll nicht mehr Kurven nennen, als die
    Spieldaten haben (LongBoy 27.10.2026: 25 erkannt, 15 im Spiel); ohne Angabe die erkannten Kurven."""
    n = len(logik.get("kurven") or []) if kurven_im_spiel is None else int(kurven_im_spiel)
    return {"length_metres": str(int(round(float(logik["laenge_m"])))),
            "numberOfCorners": str(n),
            "allow_night_start": "False"}


def metadaten_setzen(manifest: dict, werte: dict) -> dict:
    """``activation.track_overrides`` setzen: vorhandene Einträge gleichen Pfads ersetzen, Reihenfolge der übrigen
    bleibt. Werte werden Text (``True``/``False`` für Wahrheitswerte, wie im Sepang-Manifest)."""
    akt = manifest.setdefault("activation", {"copy_from_track": None, "overrides": []})
    akt.setdefault("overrides", [])
    liste = [e for e in akt.get("track_overrides") or [] if e.get("path") not in werte]
    for pfad, wert in werte.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(pfad)):
            raise StreckencodeFehler("Ungültiger Metadaten-Schlüssel %r" % pfad)
        liste.append({"path": str(pfad), "value": wert if isinstance(wert, str) else
                      ("True" if wert is True else "False" if wert is False else str(wert))})
    akt["track_overrides"] = liste
    return manifest


def _json(roh):
    if roh is None:
        return None
    return json.loads(roh.decode("utf-8")) if isinstance(roh, (bytes, bytearray)) else roh


def paket_bauen_umbenannt(ziel: str, manifest, images, minimap, erp_pfad: str, mipmaps, regeln: Umbenennung,
                          arbeitsordner: str, name: Optional[str] = None, aktivierung_vorlage: Optional[str] = None,
                          ueberschreiben: bool = False, protokoll=print) -> dict:
    """Wie paket.paket_bauen, aber vorher ERP, Manifest und UI-JSONs auf den neuen Code umbenennen.
    mipmaps: Folge (Name im ZIP, Quelle) wie bei paket.paket_bauen; Dateinamen bleiben."""
    os.makedirs(arbeitsordner, exist_ok=True)
    neu_erp = os.path.join(arbeitsordner, regeln.neu + ".erp")
    mip = list(mipmaps)
    mip_namen = {n.rsplit("/", 1)[-1] for n, _ in mip}
    bericht = {"erp": erp_umbenennen(erp_pfad, neu_erp, regeln, mip_namen=mip_namen, protokoll=protokoll)}
    m = manifest_umbenennen(_json(manifest), regeln, name, aktivierung_vorlage)
    img, mm = regeln.json(_json(images)), regeln.json(_json(minimap))
    bericht["manifest"] = m
    protokoll("Paket %s …" % ziel)
    bericht["groesse"] = paket.paket_bauen(ziel, m, img, mm, neu_erp, mip, ueberschreiben=ueberschreiben)
    bericht["neu_erp"] = neu_erp
    return bericht


def paket_umbenennen(quelle: str, ziel: str, code: str, name: Optional[str] = None,
                     aktivierung_vorlage: Optional[str] = None, arbeitsordner: Optional[str] = None,
                     ueberschreiben: bool = False, protokoll=print) -> dict:
    """Mod-Paket (ZIP) mit neuem Streckencode bauen. Die Quelle bleibt unverändert."""
    if os.path.exists(ziel) and not ueberschreiben:
        raise FileExistsError("Zieldatei existiert bereits: %s" % ziel)
    arbeit = arbeitsordner or os.path.join(os.path.dirname(os.path.abspath(ziel)), "_umbenennen")
    paket.ordner_leeren(arbeit)
    t0 = time.time()
    with paket.ModPaket(quelle) as p:
        regeln = Umbenennung(p.track_code, code)
        protokoll("Quelle %s: Code %s, ERP entpacken …" % (os.path.basename(quelle), p.track_code))
        alt_erp = p.erp_entpacken(os.path.join(arbeit, "quelle_" + os.path.basename(p.erp_name)))
        mip = [(i.filename, (p.zip, i)) for i in p.mipmap_eintraege()]
        bericht = paket_bauen_umbenannt(ziel, p.manifest_roh, p.images_roh, p.minimap_roh, alt_erp, mip, regeln,
                                        arbeit, name, aktivierung_vorlage, ueberschreiben, protokoll)
    os.remove(alt_erp)
    os.remove(bericht.pop("neu_erp"))
    with paket.ModPaket(ziel) as q:
        bericht["paket_pruefung"] = q.pruefen(os.path.join(arbeit, "_pruefen"), tief=True)
    paket.ordner_leeren(arbeit)
    os.rmdir(arbeit)
    bericht.update(quelle=os.path.abspath(quelle), ziel=os.path.abspath(ziel), code=code,
                   bewertung=code_bewerten(code)[1], sekunden=round(time.time() - t0))
    return bericht


def bericht_markdown(b: dict) -> str:
    """Markdown-Bericht für ergebnisse/ (Ausgabe von paket_umbenennen)."""
    e = b["erp"]
    z = ["# Streckencode-Umbenennung `%s` → `%s`" % (e["alt"], e["neu"]), "",
         "| | |", "|---|---|",
         "| Quelle | `%s` |" % b.get("quelle", "?"), "| Ziel | `%s` (nicht im Repository) |" % b.get("ziel", "?"),
         "| Code | `%s` – %s |" % (b.get("code", e["neu"]), b.get("bewertung", "")),
         "| Name im Manifest | %s |" % b["manifest"].get("track_name"),
         "| Aktivierung | %s |" % json.dumps(b["manifest"].get("activation"), ensure_ascii=False),
         "| Kopierziel mipmaps | `%s` |" % b["manifest"]["copy_directories"][0]["destination"],
         "| Dauer | %s s |" % b.get("sekunden", "?"), "",
         "## Umbenannte Kennungen (%d)" % e["kennungen_summe"], "", "| Typ | Anzahl |", "|---|---:|"]
    z += ["| %s | %d |" % kv for kv in e["kennungen_umbenannt"].items()]
    z += ["", "## Ressourcen mit geänderten Daten (%d)" % e["daten_summe"], "", "| Typ | Anzahl |", "|---|---:|"]
    z += ["| %s | %d |" % kv for kv in e["daten_geaendert"].items()]
    z += ["", "## Verbliebene Vorkommen von `%s` (begründet)" % e["alt"], "",
          "| Typ | Ressourcen | Beispiele |", "|---|---:|---|"]
    z += ["| %s | %d | %s |" % (k, v["ressourcen"], "; ".join("`%s`" % x for x in v["beispiele"]))
          for k, v in e["rest_in_daten"].items()]
    z += ["", "Kennungen, die `%s` behalten (core_assets, identisch mit Spiel/Mod): %s" % (
        e["alt"], ", ".join("`%s`" % k.split("/")[-1] for k in e["rest_kennungen"]) or "keine"), ""]
    if e.get("md5_falsch_quelle"):
        z += ["Falsche MD5 in der Quelle (vom Träger geerbt): %s. Geänderte Ressourcen erhalten einen neu "
              "berechneten MD5." % ", ".join("`%s`" % k.split("/")[-1] for k in e["md5_falsch_quelle"]), ""]
    z += ["## Prüfung der ERP", "", "| Prüfung | Ergebnis | Details |", "|---|---|---|"]
    z += ["| %s | %s | %s |" % (n, "OK" if ok else "**FEHLER**", t) for n, ok, t in e.get("pruefung", [])]
    if b.get("paket_pruefung"):
        z += ["", "## `paket_tool.py pruefen --tief`", "", "| Prüfung | Ergebnis | Details |", "|---|---|---|"]
        z += ["| %s | %s | %s |" % (n, "OK" if ok else "**FEHLER**", t) for n, ok, t in b["paket_pruefung"]]
    return "\n".join(z) + "\n"
