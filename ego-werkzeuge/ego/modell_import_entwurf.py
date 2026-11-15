"""ENTWURF: eigene 3D-Modelle (Blender → glTF 2.0 / OBJ) in Spielressourcen umsetzen (15.11.2026).

Eigenständiger Prototyp zur Untersuchung ``docs/eigene_modelle.md``. **Nicht im Bau und nicht im Mapeditor
eingehängt**, verändert keine vorhandenen Module und schreibt nie in den Spielordner. Im Spiel ist nichts davon
getestet; belegt sind Aufbau und Werte nur gegen die Originale (``analyse/modell_formate.py``,
``tests/modell_import_test.py``).

Ablauf:

    szene = glb_lesen("schild.glb")                    # oder gltf_lesen / obj_lesen
    bau = modell_bauen(szene, "eaid://environment_package/tracks/portimao/objects/idf/usr_schild.idf",
                       materialien={"Tafel": "eaid://…/vorhandenes.material"})
    bau.ressourcen += textur_ressourcen("eaid://…/usr_schild_01_d.tif", rgba, FORMAT_DIFFUS)
    bau.ressourcen.append(material_aus_vorlage(vorlage_bytes, "eaid://…/usr_schild_01.material", {...}))
    in_archiv(archiv, bau.ressourcen)
    pruefen(archiv, bau, quelle=spiel_index)           # Liste (Name, ok, Einzelheit)

Koordinaten: Das Spiel rechnet wie glTF **rechtshändig mit Y oben in Metern** (docs/formate/streckenlogik.md);
glTF-Daten werden deshalb unverändert übernommen. Blender (Z oben) exportiert glTF mit „+Y Up“ selbst richtig:
Blender (x, y, z) → Spiel (x, z, −y); die Blender-Draufsicht entspricht der Karte des Mapeditors (x rechts,
z unten). Die übliche Blender-Vorderseite (−Y, Ansicht „Vorn“) ist im Spiel +Z = ``vorne_grad`` 270.

Erzeugt wird das häufigste Layout statischer Objekte (46.749 von 67.790 Teilnetzen): vier Ströme
``POSITION float32x3 | NORMAL half4 | TANGENT half4 | TEXCOORD0 half4 (u, v, Lichtkarten-u, Lichtkarten-v)``,
16-Bit-Indizes mit Basis-Vertex, je LOD ein eigener Modellknoten.
"""

from __future__ import annotations

import base64
import io
import json
import os
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import erp, grafik

# --------------------------------------------------------------------------- Konstanten (aus den Originalen)
PUFFER_CODE_FLOAT = 39      # float32-Ströme (Stride 12/16)
PUFFER_CODE_HALB4 = 35      # float16x4 (Stride 8)
PUFFER_CODE_INDEX = 19      # u16-Indizes (Art 0x110A)
FORMAT_F32X3, FORMAT_HALB4 = 47, 35
FRAGMENT_FLAGS, FRAGMENT_FLAGS_PUFFERDATEN = 16, 4
EINHEIT = tuple(float(i % 5 == 0) for i in range(16))
INDEX_GRENZE = 65535
#: Texturformate (Codes der Engine): Standard je Slot von ``object_opaque`` in den Originalen
FORMAT_DIFFUS = 70          # BC7 sRGB (Originale meist BC1 sRGB = 54; BC7 ist mit dem vorhandenen Kodierer schärfer)
FORMAT_DIFFUS_BC1 = 54
FORMAT_NORMAL = 65          # BC5
FORMAT_OBERFLAECHE = 52     # BC1 linear
TEXTUR_FLAGS = 264
SRV_ART = 5
#: Budget aus dem Bestand (analyse/modell_formate.py): LOD 0 Median 1.100, p90 10.900, p99 41.800, Maximum 254.000
DREIECKE_WARNUNG, DREIECKE_FEHLER = 50000, 250000
UV_HALB_WARNUNG = 64.0
LICHT_UV_STANDARD = (0.5, 0.5)


class ModellImportFehler(ValueError):
    """Die Eingabe lässt sich nicht in ein Spielmodell umsetzen."""


# --------------------------------------------------------------------------- Zwischenformat
@dataclass
class Teil:
    """Ein Dreiecksnetz mit einem Material (glTF-Primitive bzw. OBJ-Materialgruppe), schon im Modellraum."""

    name: str
    material: str
    positionen: np.ndarray                  # (N, 3)
    dreiecke: np.ndarray                    # (M, 3)
    normalen: Optional[np.ndarray] = None   # (N, 3)
    tangenten: Optional[np.ndarray] = None  # (N, 4), w = ±1
    uv0: Optional[np.ndarray] = None        # (N, 2), v nach unten (0 = oberer Bildrand) wie glTF und Spiel
    uv1: Optional[np.ndarray] = None        # (N, 2) Lichtkarten-UV 0…1
    farben: Optional[np.ndarray] = None     # (N, 4) 0…1 (wird im Vier-Strom-Layout nicht geschrieben)


@dataclass
class QuellMaterial:
    name: str
    basisfarbe: Tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
    bild_basisfarbe: Optional[np.ndarray] = None   # RGBA uint8
    bild_normal: Optional[np.ndarray] = None
    bild_orm: Optional[np.ndarray] = None          # glTF: Verdeckung/Rauheit/Metall
    alpha: str = "OPAQUE"
    doppelseitig: bool = False


@dataclass
class Szene:
    teile: List[Teil] = field(default_factory=list)
    materialien: Dict[str, QuellMaterial] = field(default_factory=dict)
    hinweise: List[str] = field(default_factory=list)
    quelle: str = ""

    def grenzen(self) -> Tuple[np.ndarray, np.ndarray]:
        p = np.concatenate([t.positionen for t in self.teile])
        return p.min(0), p.max(0)

    def dreiecke(self) -> int:
        return int(sum(len(t.dreiecke) for t in self.teile))


# --------------------------------------------------------------------------- glTF 2.0 lesen
_KOMPONENTE = {5120: "i1", 5121: "u1", 5122: "<i2", 5123: "<u2", 5125: "<u4", 5126: "<f4"}
_ANZAHL = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}
_NICHT_UNTERSTUETZT = ("KHR_draco_mesh_compression", "EXT_meshopt_compression", "KHR_mesh_quantization")


def _bild_rgba(daten: bytes) -> np.ndarray:
    from PIL import Image
    return np.asarray(Image.open(io.BytesIO(daten)).convert("RGBA"), np.uint8)


class _Gltf:
    def __init__(self, j: dict, binaer: Optional[bytes], ordner: str):
        self.j, self.ordner = j, ordner
        self.puffer: List[bytes] = []
        for i, b in enumerate(j.get("buffers", [])):
            uri = b.get("uri")
            if uri is None:
                if binaer is None or i != 0:
                    raise ModellImportFehler("glTF: Puffer %d ohne Daten" % i)
                self.puffer.append(binaer)
            else:
                self.puffer.append(self._uri(uri))

    def _uri(self, uri: str) -> bytes:
        if uri.startswith("data:"):
            return base64.b64decode(uri.split(",", 1)[1])
        from urllib.parse import unquote
        pfad = os.path.normpath(os.path.join(self.ordner, unquote(uri)))
        if os.path.commonpath([os.path.abspath(pfad), os.path.abspath(self.ordner)]) != os.path.abspath(self.ordner):
            raise ModellImportFehler("glTF: Verweis außerhalb des Ordners: %s" % uri)
        with open(pfad, "rb") as f:
            return f.read()

    def sicht(self, nr: int) -> bytes:
        v = self.j["bufferViews"][nr]
        o = v.get("byteOffset", 0)
        return self.puffer[v["buffer"]][o:o + v["byteLength"]]

    def zugriff(self, nr: int) -> np.ndarray:
        a = self.j["accessors"][nr]
        if "sparse" in a:
            raise ModellImportFehler("glTF: dünn besetzte Accessoren (sparse) werden nicht unterstützt")
        dt = np.dtype(_KOMPONENTE[a["componentType"]])
        k, n = _ANZAHL[a["type"]], a["count"]
        if "bufferView" not in a:
            return np.zeros((n, k), np.float64)
        v = self.j["bufferViews"][a["bufferView"]]
        roh = self.puffer[v["buffer"]]
        start = v.get("byteOffset", 0) + a.get("byteOffset", 0)
        breite = dt.itemsize * k
        schritt = v.get("byteStride") or breite
        if schritt == breite:
            w = np.frombuffer(roh, dt, n * k, start).reshape(n, k)
        else:
            m = np.frombuffer(roh, np.uint8, (n - 1) * schritt + breite, start)
            idx = (np.arange(n) * schritt)[:, None] + np.arange(breite)[None, :]
            w = np.ascontiguousarray(m[idx]).view(dt).reshape(n, k)
        w = w.astype(np.float64)
        if a.get("normalized"):
            w = {"u1": w / 255.0, "<u2": w / 65535.0, "i1": np.maximum(w / 127.0, -1.0),
                 "<i2": np.maximum(w / 32767.0, -1.0)}[_KOMPONENTE[a["componentType"]]]
        return w

    def bild(self, textur_info: Optional[dict]) -> Optional[np.ndarray]:
        if not textur_info:
            return None
        tex = self.j["textures"][textur_info["index"]]
        if "source" not in tex:
            return None
        b = self.j["images"][tex["source"]]
        daten = self.sicht(b["bufferView"]) if "bufferView" in b else self._uri(b["uri"])
        return _bild_rgba(daten)


def _knotenmatrix(k: dict) -> np.ndarray:
    if "matrix" in k:
        return np.array(k["matrix"], float).reshape(4, 4).T        # glTF speichert spaltenweise
    return grafik.trs_matrix(k.get("translation", (0, 0, 0)), k.get("rotation", (0, 0, 0, 1)), k.get("scale", (1, 1, 1)))


def _gltf_szene(g: _Gltf, quelle: str) -> Szene:
    j = g.j
    benutzt = set(j.get("extensionsRequired", []))
    for e in _NICHT_UNTERSTUETZT:
        if e in benutzt:
            raise ModellImportFehler("glTF: Erweiterung %s wird nicht unterstützt (ohne Kompression exportieren)" % e)
    sz = Szene(quelle=quelle)
    if j.get("asset", {}).get("version", "2.0")[:1] != "2":
        raise ModellImportFehler("glTF: nur Version 2.0")
    if j.get("skins"):
        sz.hinweise.append("Skinning wird ignoriert (statische Objekte)")
    if j.get("animations"):
        sz.hinweise.append("Animationen werden ignoriert")
    namen = []
    for i, m in enumerate(j.get("materials", [])):
        name = m.get("name") or "material_%d" % i
        while name in namen:
            name += "_"
        namen.append(name)
        pbr = m.get("pbrMetallicRoughness", {})
        for info in (pbr.get("baseColorTexture"), m.get("normalTexture")):
            if info and info.get("texCoord", 0) != 0:
                sz.hinweise.append("Material %s: Textur nutzt UV-Satz %d, das Spielmaterial nimmt UV0" % (name, info["texCoord"]))
            if info and "KHR_texture_transform" in info.get("extensions", {}):
                sz.hinweise.append("Material %s: KHR_texture_transform wird ignoriert" % name)
        sz.materialien[name] = QuellMaterial(
            name, tuple(float(x) for x in pbr.get("baseColorFactor", (1, 1, 1, 1))), g.bild(pbr.get("baseColorTexture")),
            g.bild(m.get("normalTexture")), g.bild(pbr.get("metallicRoughnessTexture")),
            m.get("alphaMode", "OPAQUE"), bool(m.get("doubleSided", False)))
    wurzeln = j.get("scenes", [{}])[j.get("scene", 0)].get("nodes") if j.get("scenes") else None
    if wurzeln is None:
        kinder = {c for k in j.get("nodes", []) for c in k.get("children", [])}
        wurzeln = [i for i in range(len(j.get("nodes", []))) if i not in kinder]
    stapel = [(n, np.eye(4)) for n in wurzeln]
    while stapel:
        nr, eltern = stapel.pop(0)
        k = j["nodes"][nr]
        welt = eltern @ _knotenmatrix(k)
        stapel += [(c, welt) for c in k.get("children", [])]
        if "mesh" not in k:
            continue
        mesh = j["meshes"][k["mesh"]]
        lin = welt[:3, :3]
        gespiegelt = np.linalg.det(lin) < 0
        nmat = np.linalg.inv(lin).T
        for pi, prim in enumerate(mesh.get("primitives", [])):
            if prim.get("mode", 4) != 4:
                sz.hinweise.append("Primitive ohne Dreiecksliste ausgelassen (mode %s)" % prim.get("mode"))
                continue
            if "extensions" in prim and any(e in prim["extensions"] for e in _NICHT_UNTERSTUETZT):
                raise ModellImportFehler("glTF: komprimierte Geometrie wird nicht unterstützt")
            if prim.get("targets"):
                sz.hinweise.append("Morph-Ziele werden ignoriert")
            at = prim["attributes"]
            if "POSITION" not in at:
                continue
            pos = g.zugriff(at["POSITION"])[:, :3] @ lin.T + welt[:3, 3]
            tri = (g.zugriff(prim["indices"]).astype(np.int64).reshape(-1, 3) if "indices" in prim
                   else np.arange(len(pos) // 3 * 3, dtype=np.int64).reshape(-1, 3))
            nrm = tan = None
            if "NORMAL" in at:
                nrm = g.zugriff(at["NORMAL"])[:, :3] @ nmat.T
                nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
            if "TANGENT" in at and nrm is not None:
                t4 = g.zugriff(at["TANGENT"])
                txyz = t4[:, :3] @ lin.T
                txyz /= np.maximum(np.linalg.norm(txyz, axis=1, keepdims=True), 1e-12)
                tan = np.concatenate([txyz, np.where(t4[:, 3:4] < 0, -1.0, 1.0) * (-1.0 if gespiegelt else 1.0)], axis=1)
            if gespiegelt:
                tri = tri[:, ::-1].copy()
            mat = namen[prim["material"]] if "material" in prim else "ohne_material"
            if mat == "ohne_material" and mat not in sz.materialien:
                sz.materialien[mat] = QuellMaterial(mat)
            farbe = g.zugriff(at["COLOR_0"]) if "COLOR_0" in at else None
            if farbe is not None and farbe.shape[1] == 3:
                farbe = np.concatenate([farbe, np.ones((len(farbe), 1))], axis=1)
            sz.teile.append(Teil("%s_%d" % (k.get("name") or mesh.get("name") or "mesh%d" % k["mesh"], pi), mat, pos, tri,
                                 nrm, tan, g.zugriff(at["TEXCOORD_0"])[:, :2] if "TEXCOORD_0" in at else None,
                                 g.zugriff(at["TEXCOORD_1"])[:, :2] if "TEXCOORD_1" in at else None, farbe))
    if not sz.teile:
        raise ModellImportFehler("glTF: keine Dreiecke gefunden")
    return sz


def glb_lesen(pfad) -> Szene:
    """Binäres glTF 2.0 (.glb) lesen; Knotentransformationen werden in die Vertices gerechnet."""
    with open(pfad, "rb") as f:
        d = f.read()
    if d[:4] != b"glTF" or struct.unpack_from("<I", d, 4)[0] != 2:
        raise ModellImportFehler("Keine GLB-Datei der Version 2: %s" % pfad)
    o, j, binaer = 12, None, None
    while o + 8 <= len(d):
        laenge, art = struct.unpack_from("<I4s", d, o)
        teil = d[o + 8:o + 8 + laenge]
        if art == b"JSON":
            j = json.loads(teil.decode("utf-8"))
        elif art == b"BIN\0" and binaer is None:
            binaer = teil
        o += 8 + laenge
    if j is None:
        raise ModellImportFehler("GLB ohne JSON-Teil")
    return _gltf_szene(_Gltf(j, binaer, os.path.dirname(os.path.abspath(pfad))), str(pfad))


def gltf_lesen(pfad) -> Szene:
    """Text-glTF 2.0 (.gltf) mit eingebetteten (data:) oder benachbarten Dateien."""
    with open(pfad, "r", encoding="utf-8") as f:
        j = json.load(f)
    return _gltf_szene(_Gltf(j, None, os.path.dirname(os.path.abspath(pfad))), str(pfad))


def obj_lesen(pfad, z_oben: bool = False) -> Szene:
    """Wavefront-OBJ (Rückfall): v/vt/vn/f/usemtl, Vielecke als Fächer. OBJ-``vt`` hat v nach oben und wird
    gedreht. ``z_oben``: Datei wurde mit Z oben geschrieben (Blender ohne Achsenumrechnung) → (x, z, −y)."""
    v, vt, vn = [], [], []
    gruppen: Dict[str, List[Tuple[Tuple[int, int, int], ...]]] = {}
    mat = "ohne_material"
    with open(pfad, "r", encoding="utf-8", errors="replace") as f:
        for zeile in f:
            t = zeile.split()
            if not t or t[0].startswith("#"):
                continue
            if t[0] == "v":
                v.append([float(x) for x in t[1:4]])
            elif t[0] == "vt":
                vt.append([float(t[1]), 1.0 - float(t[2]) if len(t) > 2 else 0.0])
            elif t[0] == "vn":
                vn.append([float(x) for x in t[1:4]])
            elif t[0] == "usemtl":
                mat = t[1] if len(t) > 1 else "ohne_material"
            elif t[0] == "f":
                ecken = []
                for e in t[1:]:
                    teile = (e.split("/") + ["", ""])[:3]
                    ecken.append(tuple((int(x) - 1 if int(x) > 0 else int(x)) if x else None for x in teile))
                for i in range(1, len(ecken) - 1):
                    gruppen.setdefault(mat, []).append((ecken[0], ecken[i], ecken[i + 1]))
    if not gruppen:
        raise ModellImportFehler("OBJ ohne Flächen: %s" % pfad)
    va, vta, vna = np.array(v, float).reshape(-1, 3), np.array(vt, float).reshape(-1, 2), np.array(vn, float).reshape(-1, 3)
    if z_oben:
        va = np.stack([va[:, 0], va[:, 2], -va[:, 1]], axis=1)
        vna = np.stack([vna[:, 0], vna[:, 2], -vna[:, 1]], axis=1) if len(vna) else vna
    sz = Szene(quelle=str(pfad))
    for name, flaechen in gruppen.items():
        ecken: Dict[Tuple, int] = {}
        tri = []
        for fl in flaechen:
            tri.append([ecken.setdefault(e, len(ecken)) for e in fl])
        schluessel = list(ecken)
        pos = va[[e[0] for e in schluessel]]
        uv = vta[[e[1] for e in schluessel]] if all(e[1] is not None for e in schluessel) and len(vta) else None
        nrm = vna[[e[2] for e in schluessel]] if all(e[2] is not None for e in schluessel) and len(vna) else None
        sz.teile.append(Teil(name, name, pos, np.array(tri, np.int64), nrm, None, uv))
        sz.materialien[name] = QuellMaterial(name)
    sz.hinweise.append("OBJ: Materialien nur dem Namen nach (MTL wird nicht gelesen)")
    return sz


def laden(pfad, **kw) -> Szene:
    endung = os.path.splitext(str(pfad))[1].lower()
    if endung == ".glb":
        return glb_lesen(pfad)
    if endung == ".gltf":
        return gltf_lesen(pfad)
    if endung == ".obj":
        return obj_lesen(pfad, **kw)
    raise ModellImportFehler("Unbekanntes Format %s (erwartet .glb, .gltf, .obj)" % endung)


# --------------------------------------------------------------------------- glTF schreiben (Tests, Maßvorlagen)
def glb_schreiben(szene: Szene, pfad) -> None:
    """Schreibt eine Szene als .glb (ein Mesh je Teil, Basisfarbe und optional Basisfarb-Textur als PNG).
    Für Tests und um ein Modell als Maßvorlage in Blender zu öffnen."""
    from PIL import Image
    binaer = bytearray()
    sichten, zugriffe, meshes, knoten, bilder, texturen, materialien = [], [], [], [], [], [], []

    def sicht(daten: bytes, ziel: Optional[int] = None) -> int:
        while len(binaer) % 4:
            binaer.append(0)
        v = {"buffer": 0, "byteOffset": len(binaer), "byteLength": len(daten)}
        if ziel:
            v["target"] = ziel
        binaer.extend(daten)
        sichten.append(v)
        return len(sichten) - 1

    def zugriff(a: np.ndarray, typ: str, komponente: int, ziel: int, grenzen: bool = False) -> int:
        z = {"bufferView": sicht(a.tobytes(), ziel), "componentType": komponente, "count": len(a), "type": typ}
        if grenzen:
            z["min"], z["max"] = a.min(0).tolist(), a.max(0).tolist()
        zugriffe.append(z)
        return len(zugriffe) - 1

    mat_index = {}
    for name, m in szene.materialien.items():
        eintrag = {"name": name, "pbrMetallicRoughness": {"baseColorFactor": list(m.basisfarbe), "metallicFactor": 0.0}}
        if m.bild_basisfarbe is not None:
            puffer = io.BytesIO()
            Image.fromarray(m.bild_basisfarbe, "RGBA").save(puffer, "PNG")
            bilder.append({"bufferView": sicht(puffer.getvalue()), "mimeType": "image/png"})
            texturen.append({"source": len(bilder) - 1})
            eintrag["pbrMetallicRoughness"]["baseColorTexture"] = {"index": len(texturen) - 1}
        mat_index[name] = len(materialien)
        materialien.append(eintrag)
    for t in szene.teile:
        at = {"POSITION": zugriff(np.asarray(t.positionen, "<f4"), "VEC3", 5126, 34962, True)}
        if t.normalen is not None:
            at["NORMAL"] = zugriff(np.asarray(t.normalen, "<f4"), "VEC3", 5126, 34962)
        if t.tangenten is not None:
            at["TANGENT"] = zugriff(np.asarray(t.tangenten, "<f4"), "VEC4", 5126, 34962)
        if t.uv0 is not None:
            at["TEXCOORD_0"] = zugriff(np.asarray(t.uv0, "<f4"), "VEC2", 5126, 34962)
        if t.uv1 is not None:
            at["TEXCOORD_1"] = zugriff(np.asarray(t.uv1, "<f4"), "VEC2", 5126, 34962)
        ind = np.asarray(t.dreiecke, "<u4").reshape(-1, 1)
        prim = {"attributes": at, "indices": zugriff(ind, "SCALAR", 5125, 34963), "mode": 4}
        if t.material in mat_index:
            prim["material"] = mat_index[t.material]
        meshes.append({"name": t.name, "primitives": [prim]})
        knoten.append({"name": t.name, "mesh": len(meshes) - 1})
    j = {"asset": {"version": "2.0", "generator": "ego-werkzeuge modell_import_entwurf"}, "scene": 0,
         "scenes": [{"nodes": list(range(len(knoten)))}], "nodes": knoten, "meshes": meshes,
         "accessors": zugriffe, "bufferViews": sichten, "buffers": [{"byteLength": len(binaer)}]}
    if materialien:
        j["materials"] = materialien
    if bilder:
        j["images"], j["textures"] = bilder, texturen
    js = json.dumps(j, separators=(",", ":")).encode("utf-8")
    js += b" " * (-len(js) % 4)
    while len(binaer) % 4:
        binaer.append(0)
    with open(pfad, "wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(js) + 8 + len(binaer)))
        f.write(struct.pack("<I4s", len(js), b"JSON") + js)
        f.write(struct.pack("<I4s", len(binaer), b"BIN\0") + bytes(binaer))


# --------------------------------------------------------------------------- Geometrie aufbereiten
def tangenten_berechnen(pos: np.ndarray, nrm: np.ndarray, uv: np.ndarray, tri: np.ndarray) -> np.ndarray:
    """Tangenten (N, 4) in der Konvention des Spiels (gemessen an 1.489 Originalmodellen): xyz zeigt entlang
    dP/du, senkrecht zur Normale; ``w · cross(N, T)`` zeigt **gegen** dP/dv (v wächst im Bild nach unten).
    Das ist dieselbe Konvention wie glTF/MikkTSpace."""
    pos, nrm, uv = np.asarray(pos, float), np.asarray(nrm, float), np.asarray(uv, float)
    p0, p1, p2 = pos[tri[:, 0]], pos[tri[:, 1]], pos[tri[:, 2]]
    e1, e2 = p1 - p0, p2 - p0
    d1, d2 = uv[tri[:, 1]] - uv[tri[:, 0]], uv[tri[:, 2]] - uv[tri[:, 0]]
    det = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    gut = np.abs(det) > 1e-14
    det = np.where(gut, det, 1.0)
    dpdu = np.where(gut[:, None], (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) / det[:, None], 0.0)
    dpdv = np.where(gut[:, None], (e2 * d1[:, 0:1] - e1 * d2[:, 0:1]) / det[:, None], 0.0)
    # flächengewichtet: |det| hebt sich gegen die Parametrisierung auf, übrig bleibt die Dreiecksfläche
    gew = np.linalg.norm(np.cross(e1, e2), axis=1, keepdims=True)
    lu = np.maximum(np.linalg.norm(dpdu, axis=1, keepdims=True), 1e-20)
    lv = np.maximum(np.linalg.norm(dpdv, axis=1, keepdims=True), 1e-20)
    su, sv = np.zeros_like(pos), np.zeros_like(pos)
    for k in range(3):
        np.add.at(su, tri[:, k], dpdu / lu * gew)
        np.add.at(sv, tri[:, k], dpdv / lv * gew)
    t = su - nrm * (su * nrm).sum(1, keepdims=True)
    laenge = np.linalg.norm(t, axis=1)
    ersatz = np.cross(nrm, np.array([0.0, 1.0, 0.0]))
    ersatz[np.linalg.norm(ersatz, axis=1) < 1e-6] = (1.0, 0.0, 0.0)
    t = np.where(laenge[:, None] > 1e-9, t, ersatz)
    t /= np.linalg.norm(t, axis=1, keepdims=True)
    w = np.where((np.cross(nrm, t) * sv).sum(1) > 0, -1.0, 1.0)
    return np.concatenate([t, w[:, None]], axis=1)


def huellkugel(p: np.ndarray) -> Tuple[float, float, float, float]:
    """Kleine, sichere Hüllkugel: Ritter-Näherung oder AABB-Mittelpunkt, die engere von beiden; Radius nach außen
    gerundet. (Originale: Radius im Median 0,96 × halbe AABB-Diagonale, also ebenfalls enger als die AABB-Kugel.)"""
    p = np.asarray(p, np.float64)
    mn, mx = p.min(0), p.max(0)
    c1 = (mn + mx) / 2
    r1 = float(np.sqrt(((p - c1) ** 2).sum(1).max()))
    a = p[np.argmax(((p - p[0]) ** 2).sum(1))]
    b = p[np.argmax(((p - a) ** 2).sum(1))]
    c2, r2 = (a + b) / 2, float(np.linalg.norm(b - a)) / 2
    for _ in range(8):
        d = np.sqrt(((p - c2) ** 2).sum(1))
        i = int(np.argmax(d))
        if d[i] <= r2 * (1 + 1e-9):
            break
        neu = (r2 + d[i]) / 2
        c2 = c2 + (p[i] - c2) * ((d[i] - r2) / (2 * d[i]))
        r2 = neu
    r2 = float(np.sqrt(((p - c2) ** 2).sum(1).max()))
    c, r = (c2, r2) if r2 < r1 else (c1, r1)
    c = c.astype(np.float32)
    r = float(np.sqrt(((p - c.astype(np.float64)) ** 2).sum(1).max()))
    return (float(c[0]), float(c[1]), float(c[2]), float(np.nextafter(np.float32(r * (1 + 1e-6) + 1e-6), np.float32(np.inf))))


def _teil_aufbereiten(t: Teil, hinweise: List[str]) -> Teil:
    pos = np.asarray(t.positionen, np.float64).reshape(-1, 3)
    tri = np.asarray(t.dreiecke, np.int64).reshape(-1, 3)
    if not len(tri) or not len(pos):
        raise ModellImportFehler("Teil %s ist leer" % t.name)
    if tri.min() < 0 or tri.max() >= len(pos):
        raise ModellImportFehler("Teil %s: Dreiecksindizes außerhalb der Vertexliste" % t.name)
    if not np.isfinite(pos).all():
        raise ModellImportFehler("Teil %s: Positionen enthalten NaN/Inf" % t.name)
    flaeche = np.linalg.norm(np.cross(pos[tri[:, 1]] - pos[tri[:, 0]], pos[tri[:, 2]] - pos[tri[:, 0]]), axis=1)
    entartet = flaeche <= 1e-12
    if entartet.any():
        hinweise.append("Teil %s: %d entartete Dreiecke entfernt" % (t.name, int(entartet.sum())))
        tri = tri[~entartet]
        if not len(tri):
            raise ModellImportFehler("Teil %s: nur entartete Dreiecke" % t.name)
    if t.normalen is None:
        nrm = grafik.normalen_berechnen(pos, tri)
        hinweise.append("Teil %s: Normalen fehlen, aus den Flächen berechnet (weich)" % t.name)
    else:
        nrm = np.asarray(t.normalen, np.float64)
        nrm = nrm / np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    if t.uv0 is None:
        uv = np.zeros((len(pos), 2))
        hinweise.append("Teil %s: UV fehlt, Textur erscheint einfarbig" % t.name)
    else:
        uv = np.asarray(t.uv0, np.float64)
        if np.abs(uv).max() > UV_HALB_WARNUNG:
            hinweise.append("Teil %s: UV bis %.0f, mit 16-Bit-Gleitkomma ungenau (Kachelung verkleinern)" % (
                t.name, np.abs(uv).max()))
    tan = tangenten_berechnen(pos, nrm, uv, tri) if t.tangenten is None else np.asarray(t.tangenten, np.float64)
    uv1 = np.tile(LICHT_UV_STANDARD, (len(pos), 1)) if t.uv1 is None else np.clip(np.asarray(t.uv1, np.float64), 0.0, 1.0)
    # nur benutzte Vertices behalten
    benutzt, lokal = np.unique(tri, return_inverse=True)
    if len(benutzt) != len(pos):
        pos, nrm, uv, tan, uv1 = pos[benutzt], nrm[benutzt], uv[benutzt], tan[benutzt], uv1[benutzt]
        tri = lokal.reshape(-1, 3)
    return Teil(t.name, t.material, pos, tri, nrm, tan, uv, uv1)


def _stuecke(tri: np.ndarray, vertices: int, grenze: int = INDEX_GRENZE) -> List[Tuple[np.ndarray, np.ndarray]]:
    """Teilt ein Netz so, dass jedes Stück höchstens ``grenze`` + 1 Vertices nutzt (16-Bit-Indizes).
    Rückgabe je Stück: (benutzte Vertexnummern, Dreiecke mit lokalen Indizes)."""
    if vertices <= grenze + 1:
        return [(np.arange(vertices), tri)]
    aus, karte, liste, lokal = [], {}, [], []
    for a, b, c in tri.tolist():
        neu = sum(1 for x in {a, b, c} if x not in karte)
        if len(liste) + neu > grenze + 1:
            aus.append((np.array(liste, np.int64), np.array(lokal, np.int64)))
            karte, liste, lokal = {}, [], []
        dreieck = []
        for x in (a, b, c):
            if x not in karte:
                karte[x] = len(liste)
                liste.append(x)
            dreieck.append(karte[x])
        lokal.append(dreieck)
    aus.append((np.array(liste, np.int64), np.array(lokal, np.int64)))
    return aus


# --------------------------------------------------------------------------- Ressourcen
@dataclass
class Ressource:
    kennung: str
    typ: str
    fragmente: List[Tuple[bytes, bytes, int, bool]]   # (Name, Daten, Flags, komprimieren)
    unknown: int

    @property
    def daten(self) -> bytes:
        return self.fragmente[0][1]


def _res(kennung: str, typ: str, daten: bytes) -> Ressource:
    return Ressource(kennung, typ, [(b"temp", daten, FRAGMENT_FLAGS, True)], grafik.TYP_UNBEKANNT[typ])


def _puffer_res(kennung: str, art: int, stride: int, code: int, daten: bytes) -> Ressource:
    kopf = grafik.GfxPuffer(art, stride, len(daten) // stride, code).kopf_bytes()
    return Ressource(kennung, "GfxBufferRes", [(b"temp", kopf, FRAGMENT_FLAGS, False),
                                               (b"temp", daten, FRAGMENT_FLAGS_PUFFERDATEN, True)],
                     grafik.TYP_UNBEKANNT["GfxBufferRes"])


@dataclass
class Bau:
    basis: str
    ressourcen: List[Ressource]
    slots: Dict[str, str]                 # Materialslot ("01") → Quellmaterial
    bindung: Dict[str, str]               # Materialslot → Material-Kennung
    aabb: Tuple[Tuple[float, ...], Tuple[float, ...]]
    kugel: Tuple[float, float, float, float]
    lods: List[List[Teil]]                # aufbereitete Teile je LOD (für die Prüfung)
    bericht: dict
    hinweise: List[str]

    @property
    def modell(self) -> str:
        return self.basis[:-len(".idf")]


def lod_abstaende_vorschlag(lods: int, sichtweite_m: float) -> Tuple[float, ...]:
    """LOD-Block wie in den Originalen: je Wechsel der Abstand doppelt, am Ende die Sichtweite
    (z. B. 80, 80, 200, 200, 800). Wechsel bei 10 % und 25 % der Sichtweite, weitere jeweils × 2,5."""
    if lods == 1:
        return (float(sichtweite_m),)
    wechsel = [sichtweite_m * 0.1 * 2.5 ** i for i in range(lods - 1)]
    wechsel = [min(w, sichtweite_m * (0.5 + 0.4 * i / max(1, lods - 2))) for i, w in enumerate(wechsel)]
    aus = []
    for w in wechsel:
        aus += [round(w), round(w)]
    return tuple(float(x) for x in aus + [sichtweite_m])


def modell_bauen(szenen, basis: str, materialien: Optional[Dict[str, str]] = None, *, sichtweite_m: float = 2500.0,
                 lod_abstaende: Optional[Sequence[float]] = None, opak: Optional[Dict[str, bool]] = None,
                 mit_bindung: bool = True, streng: bool = True, slotnamen: Optional[Dict[str, str]] = None) -> Bau:
    """Erzeugt GfxModelRes, ModelProxyRes, je LOD ein GfxMeshRes, vier Vertexpuffer, einen Indexpuffer und die
    Materialbindung ``<modell>.emb?context=default``.

    ``szenen``: eine Szene oder eine Liste (LOD 0 zuerst). ``basis``: Kennung ``eaid://…/<name>.idf``.
    ``materialien``: Quellmaterial → Kennung eines GfxMaterialRes (vorhanden oder mit ``material_aus_vorlage``
    erzeugt). ``opak``: Quellmaterial → False für Alpha-/Maskenmaterial (Zeichnungs-Flag 0 statt 1).
    ``slotnamen``: Quellmaterial → Name des Materialslots (Standard ``01``, ``02`` … wie bei den meisten
    Originalen), z. B. um die Slots einer vorhandenen Bindung zu treffen."""
    if isinstance(szenen, Szene):
        szenen = [szenen]
    if not basis.startswith("eaid://") or not basis.endswith(".idf"):
        raise ModellImportFehler("Basiskennung muss eaid://…/<name>.idf sein")
    name = basis.rsplit("/", 1)[-1][:-len(".idf")]
    hinweise: List[str] = []
    for s in szenen:
        hinweise += s.hinweise
    lods = [[_teil_aufbereiten(t, hinweise) for t in s.teile] for s in szenen]
    n0 = sum(len(t.dreiecke) for t in lods[0])
    if n0 > DREIECKE_FEHLER and streng:
        raise ModellImportFehler("%d Dreiecke in LOD 0 (größtes Spielmodell: 254.000)" % n0)
    if n0 > DREIECKE_WARNUNG:
        hinweise.append("%d Dreiecke in LOD 0 (99 %% der Spielmodelle haben unter 42.000)" % n0)
    for i in range(1, len(lods)):
        if sum(len(t.dreiecke) for t in lods[i]) >= sum(len(t.dreiecke) for t in lods[i - 1]):
            hinweise.append("LOD %d hat nicht weniger Dreiecke als LOD %d" % (i, i - 1))
    # Materialslots nach erstem Auftreten in LOD 0, dann in den weiteren LODs
    reihenfolge: List[str] = []
    for teile in lods:
        for t in teile:
            if t.material not in reihenfolge:
                reihenfolge.append(t.material)
    if len(reihenfolge) > 99:
        raise ModellImportFehler("Mehr als 99 Materialien")
    slot_von = {m: (slotnamen or {}).get(m, "%02d" % (i + 1)) for i, m in enumerate(reihenfolge)}
    if len(set(slot_von.values())) != len(slot_von):
        raise ModellImportFehler("Materialslots sind nicht eindeutig: %s" % sorted(slot_von.values()))
    materialien = materialien or {}
    fehlend = [m for m in reihenfolge if m not in materialien]
    if fehlend and mit_bindung:
        raise ModellImportFehler("Keine Spielmaterialien für: %s" % ", ".join(fehlend))
    opak = opak or {}
    # Vertexpool und Indizes
    pos_l, nrm_l, tan_l, uv_l, ind_l = [], [], [], [], []
    v_off = i_off = 0
    meshes: List[grafik.GfxMesh] = []
    for li, teile in enumerate(lods):
        mesh = grafik.GfxMesh(lod=li)
        mesh.vb_kennungen = [grafik.vb_kennung(basis, k) for k in range(4)]
        mesh.ib_kennungen = [grafik.ib_kennung(basis, 0)]
        mesh.elemente = [grafik.VertexElement(0, 0, 1, 0, FORMAT_F32X3), grafik.VertexElement(1, 0, 2, 0, FORMAT_HALB4),
                         grafik.VertexElement(2, 0, 3, 0, FORMAT_HALB4), grafik.VertexElement(3, 0, 5, 0, FORMAT_HALB4)]
        mesh.strom_tabelle = [(1, 0), (1, 1), (1, 2), (1, 3)]
        for m in reihenfolge:
            gruppe = [t for t in teile if t.material == m]
            if not gruppe:
                continue
            if slot_von[m] not in mesh.materialnamen:
                mesh.materialnamen.append(slot_von[m])
            mi = mesh.materialnamen.index(slot_von[m])
            # alle Teile eines Materials zu einem Netz, dann in 16-Bit-Stücke
            versatz, tri_alle = 0, []
            for t in gruppe:
                tri_alle.append(t.dreiecke + versatz)
                versatz += len(t.positionen)
            p = np.concatenate([t.positionen for t in gruppe])
            n = np.concatenate([t.normalen for t in gruppe])
            tg = np.concatenate([t.tangenten for t in gruppe])
            uv = np.concatenate([np.concatenate([t.uv0, t.uv1], axis=1) for t in gruppe])
            for benutzt, tri in _stuecke(np.concatenate(tri_alle), len(p)):
                pp = p[benutzt].astype(np.float32)
                pos_l.append(pp)
                nrm_l.append(np.concatenate([n[benutzt], np.zeros((len(benutzt), 1))], axis=1))
                tan_l.append(tg[benutzt])
                uv_l.append(uv[benutzt])
                ind_l.append(tri.astype("<u2").ravel())
                ti = len(mesh.teilnetze)
                mesh.eintraege.append((1 + li, ti))
                mesh.teilnetze.append(grafik.Teilnetz(4, grafik.TOPOLOGIE_DREIECKSLISTE, 2, i_off, int(tri.size), v_off,
                                                      huellkugel(pp), 8 * ti, 1, mi, 0, [0, 1, 2, 3, 0, 0, 0, 0]))
                mesh.zeichnungen.append(grafik.Zeichnung(1 if opak.get(m, True) else 0, ti, i_off, int(tri.size)))
                v_off += len(benutzt)
                i_off += int(tri.size)
        mesh.knoten = [grafik.MeshKnoten(EINHEIT, 0, 1 + li, 0, 0, len(mesh.zeichnungen), 0, 0)]
        fehler = mesh.pruefen()
        if fehler:
            raise ModellImportFehler("Mesh LOD %d fehlerhaft: %s" % (li, fehler))
        meshes.append(mesh)
    pos = np.concatenate(pos_l)
    res: List[Ressource] = [
        _puffer_res(grafik.vb_kennung(basis, 0), grafik.PUFFER_VERTEX, 12, PUFFER_CODE_FLOAT, pos.astype("<f4").tobytes()),
        _puffer_res(grafik.vb_kennung(basis, 1), grafik.PUFFER_VERTEX, 8, PUFFER_CODE_HALB4,
                    np.concatenate(nrm_l).astype("<f2").tobytes()),
        _puffer_res(grafik.vb_kennung(basis, 2), grafik.PUFFER_VERTEX, 8, PUFFER_CODE_HALB4,
                    np.concatenate(tan_l).astype("<f2").tobytes()),
        _puffer_res(grafik.vb_kennung(basis, 3), grafik.PUFFER_VERTEX, 8, PUFFER_CODE_HALB4,
                    np.concatenate(uv_l).astype("<f2").tobytes()),
        _puffer_res(grafik.ib_kennung(basis, 0), grafik.PUFFER_INDEX, 2, PUFFER_CODE_INDEX, np.concatenate(ind_l).tobytes()),
    ]
    for li, mesh in enumerate(meshes):
        res.append(_res(grafik.mesh_kennung(basis, li), "GfxMeshRes", mesh.to_bytes()))
    # Modell: Wurzel + ein Knoten je LOD (Originale: <name>, <name>_x1, <name>_x2 …)
    knoten = [grafik.ModellKnoten("Scene Root", -1, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0), EINHEIT, 0, 0)]
    for li in range(len(lods)):
        knoten.append(grafik.ModellKnoten(name if li == 0 else "%s_x%d" % (name, li), 0, (0.0, 0.0, 0.0),
                                          (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0), EINHEIT, 1, 1 + li))
    p0 = pos[:sum(len(x) for x in pos_l[:len(meshes[0].teilnetze)])]
    alle = pos                                   # AABB und Kugel über alle LODs: nie zu klein
    aabb = (tuple(float(x) for x in alle.min(0)), tuple(float(x) for x in alle.max(0)))
    kugel = huellkugel(alle)
    abst = tuple(float(x) for x in (lod_abstaende if lod_abstaende is not None
                                    else lod_abstaende_vorschlag(len(lods), sichtweite_m)))
    if len(abst) != 2 * len(lods) - 1:
        raise ModellImportFehler("LOD-Abstände: %d Werte statt %d" % (len(abst), 2 * len(lods) - 1))
    modell = grafik.GfxModell(lod_anzahl=len(lods), knoten_anzahl=len(knoten), namen_anzahl=len(knoten),
                              mesh_kennungen=[grafik.mesh_kennung(basis, li) for li in range(len(lods))], kugel=kugel,
                              aabb_min=aabb[0], aabb_max=aabb[1], lod_abstaende=abst, lod_wert=2, knoten=knoten)
    res.append(_res(grafik.modell_kennung(basis), "GfxModelRes", modell.to_bytes()))
    res.append(_res(grafik.proxy_kennung(basis), "ModelProxyRes", grafik.ModellProxy.aus_modell(modell).to_bytes()))
    bindung = {slot_von[m]: materialien[m] for m in reihenfolge if m in materialien}
    if mit_bindung:
        mats = []
        for m in reihenfolge:
            if materialien[m] not in mats:
                mats.append(materialien[m])
        b = grafik.MaterialBindung([slot_von[m] for m in reihenfolge], mats,
                                   [mats.index(materialien[m]) for m in reihenfolge])
        res.append(_res(basis[:-len(".idf")] + ".emb?context=default", "GfxMatBindRes", b.to_bytes()))
    bericht = {"basis": basis, "lods": len(lods), "dreiecke": [int(sum(len(t.dreiecke) for t in x)) for x in lods],
               "vertices": int(len(pos)), "vertices_lod0": int(len(p0)), "teilnetze": [len(m.teilnetze) for m in meshes],
               "materialslots": {slot_von[m]: m for m in reihenfolge}, "aabb_min": list(aabb[0]), "aabb_max": list(aabb[1]),
               "kugel": list(kugel), "lod_abstaende": list(abst), "ressourcen": len(res),
               "bytes": int(sum(len(f[1]) for r in res for f in r.fragmente))}
    return Bau(basis, res, {slot_von[m]: m for m in reihenfolge}, bindung, aabb, kugel, lods, bericht, hinweise)


# --------------------------------------------------------------------------- Texturen und Materialien
def bc4_kodieren(kanal: np.ndarray) -> bytes:
    """Ein Kanal (H, W) uint8 → BC4-Blöcke (Achtwertmodus), wie der Alphateil von BC3."""
    k = np.asarray(kanal, np.uint8)
    h, w = k.shape
    if h % 4 or w % 4:
        k = np.pad(k, ((0, -h % 4), (0, -w % 4)), mode="edge")
        h, w = k.shape
    b = k.reshape(h // 4, 4, w // 4, 4).transpose(0, 2, 1, 3).reshape(-1, 16).astype(np.float32)
    a0, a1 = b.max(1), b.min(1)
    pal = np.stack([a0, a1] + [((7 - j) * a0 + j * a1) / 7 for j in range(1, 7)], axis=1)
    idx = np.argmin((b[:, :, None] - pal[:, None, :]) ** 2, axis=2)
    idx[a0 == a1] = 0
    bits = np.sum(idx.astype(np.uint64) << (3 * np.arange(16, dtype=np.uint64)), axis=1, dtype=np.uint64)
    out = np.empty((len(b), 8), np.uint8)
    out[:, 0], out[:, 1] = a0, a1
    out[:, 2:8] = bits.astype("<u8").view(np.uint8).reshape(-1, 8)[:, :6]
    return out.tobytes()


def bc5_kodieren(rgba: np.ndarray) -> bytes:
    """Rot und Grün als zwei BC4-Blöcke (Normalenkarten ``_n``: x in Rot, y in Grün, z rechnet der Shader)."""
    a = np.asarray(rgba, np.uint8)
    r = np.frombuffer(bc4_kodieren(a[:, :, 0]), np.uint8).reshape(-1, 8)
    g = np.frombuffer(bc4_kodieren(a[:, :, 1]), np.uint8).reshape(-1, 8)
    return np.concatenate([r, g], axis=1).tobytes()


def _zweierpotenz(n: int) -> int:
    p = 4
    while p * 2 <= n * 1.5 and p < 4096:      # zur nächsten Zweierpotenz (ab 1,5-fach aufrunden)
        p *= 2
    return p


def bild_vorbereiten(rgba: np.ndarray, max_kante: int = 2048) -> Tuple[np.ndarray, Optional[str]]:
    """Bild auf Zweierpotenzen (4…max_kante) bringen. Rückgabe (RGBA, Hinweis oder None)."""
    from PIL import Image
    a = np.asarray(rgba, np.uint8)
    if a.ndim != 3 or a.shape[2] != 4:
        raise ModellImportFehler("Bild muss RGBA sein")
    h, w = a.shape[:2]
    zw, zh = min(_zweierpotenz(w), max_kante), min(_zweierpotenz(h), max_kante)
    if (zw, zh) == (w, h):
        return a, None
    neu = np.asarray(Image.fromarray(a, "RGBA").resize((zw, zh), Image.Resampling.LANCZOS), np.uint8)
    return neu, "Bild %d×%d auf %d×%d umgerechnet (Zweierpotenzen, höchstens %d)" % (w, h, zw, zh, max_kante)


def textur_ressourcen(kennung_tif: str, rgba: np.ndarray, format: int = FORMAT_DIFFUS) -> List[Ressource]:
    """Neue Textur vollständig im ERP (ohne externe ``.mipmaps``): GfxSurfaceRes ``<kennung>.image`` (Kopf 40 B +
    Mip-Kette bis 4 px) und GfxSRVResource ``<kennung>``. 3.756 Originaltexturen sind ebenso aufgebaut."""
    from . import grafik_textur_schreiben as gts
    if not kennung_tif.endswith(".tif"):
        raise ModellImportFehler("Texturkennung muss auf .tif enden")
    a = np.asarray(rgba, np.uint8)
    h, w = a.shape[:2]
    if w & (w - 1) or h & (h - 1) or min(w, h) < 4:
        raise ModellImportFehler("Texturgröße %d×%d ist keine Zweierpotenz ≥ 4 (bild_vorbereiten)" % (w, h))
    stufen = gts.mip_kette(a)
    kodieren = bc5_kodieren if format == FORMAT_NORMAL else (lambda s: gts.bild_kodieren(s, format))
    daten = b"".join(kodieren(s) for s in stufen)
    kopf = grafik.TEXTUR_KOPF.pack(2, TEXTUR_FLAGS, format, w, h, 1, len(stufen), 1, 0, 0)
    bild = Ressource(kennung_tif + ".image", "GfxSurfaceRes",
                     [(b"temp", kopf, FRAGMENT_FLAGS, True), (b"temp", daten, FRAGMENT_FLAGS, True)],
                     grafik.TYP_UNBEKANNT["GfxSurfaceRes"])
    sicht = grafik.TexturSicht(SRV_ART, format, 0, len(stufen), 0, 0, kennung_tif + ".image")
    return [bild, _res(kennung_tif, "GfxSRVResource", sicht.to_bytes())]


def material_aus_vorlage(vorlage: bytes, kennung: str, texturen: Optional[Dict[str, str]] = None,
                         konstanten: Optional[Dict[str, Sequence[float]]] = None) -> Ressource:
    """Neues GfxMaterialRes als Kopie eines Spielmaterials (Shader, Parameter und Konstantenpuffer bleiben):
    ``texturen`` = {Slot (z. B. ``me_diffuseMap``): neue ``.tif``-Kennung}, ``konstanten`` wie
    ``grafik.material_aendern`` (z. B. ``me_diffuseTint``). Nicht genannte Slots behalten die Textur der Vorlage;
    diese muss dann im Paket vorhanden sein."""
    if not kennung.endswith(".material"):
        raise ModellImportFehler("Materialkennung muss auf .material enden")
    m = grafik.Material.aus_bytes(vorlage)
    alt = m.texturen()
    tausch: Dict[str, str] = {}
    for slot, neu in (texturen or {}).items():
        if slot not in alt:
            raise ModellImportFehler("Vorlagenmaterial hat keinen Texturslot %s (vorhanden: %s)" % (slot, ", ".join(sorted(alt))))
        andere = [s for s, k in alt.items() if k == alt[slot] and s != slot and (texturen or {}).get(s) != neu]
        if andere:
            raise ModellImportFehler("Vorlage nutzt dieselbe Textur in %s und %s; andere Vorlage wählen" % (slot, andere[0]))
        tausch[alt[slot]] = neu
    return _res(kennung, "GfxMaterialRes", grafik.material_aendern(vorlage, konstanten=konstanten, texturen=tausch))


def vorlage_material_finden(quelle, shader: str = "object_opaque.nefx2", slots=("me_diffuseMap", "me_normalMap", "me_surfaceMap")
                            ) -> Optional[str]:
    """Kennung eines unauffälligen Materials mit dem Shader (weiße Tönung, kein Leuchten) in Archiv oder Index."""
    idx = grafik._als_index(quelle)
    for r in idx.alle("GfxMaterialRes"):
        try:
            m = grafik.Material.aus_bytes(r.data())
        except Exception:  # noqa: BLE001
            continue
        if not m.shader.endswith("/" + shader) or sorted(m.texturen()) != sorted(slots):
            continue
        k = m.konstanten()
        if len(set(m.texturen().values())) != len(slots):
            continue
        if tuple(round(x, 3) for x in k.get("me_diffuseTint", (1, 1, 1))) == (1, 1, 1) and \
                k.get("me_glowLevel", (0.0,))[0] == 0 and k.get("me_selfIlluminationLevel", (0.0,))[0] == 0 and \
                k.get("me_translucency", (0.0,))[0] == 0:
            return r.identifier
    return None


# --------------------------------------------------------------------------- Archiv
def in_archiv(archiv: erp.ErpFile, ressourcen: Sequence[Ressource], ersetzen: bool = False) -> List[str]:
    """Ressourcen in ein ERP-Archiv im Speicher eintragen (Typfeld, Fragment-Flags und Kompression wie die
    Originale, MD5 neu). Vorhandene Kennungen sind ein Fehler, außer ``ersetzen``."""
    doppelt = [r.kennung for r in ressourcen if archiv.find(r.kennung) is not None]
    if doppelt and not ersetzen:
        raise ModellImportFehler("Kennung schon vorhanden: %s" % doppelt[0])
    neu = []
    for r in ressourcen:
        if archiv.find(r.kennung) is not None:
            archiv.remove_resource(r.kennung)
        n = archiv.add_resource(r.kennung, r.typ, [(name, daten) for name, daten, _, _ in r.fragmente], unknown=r.unknown)
        for fr, (_, daten, flags, komprimieren) in zip(n.fragments, r.fragmente):
            if not komprimieren:
                fr.set_data(daten, compress=False)
            fr.flags = flags
        n.update_hash()
        neu.append(r.kennung)
    return neu


def _dreieckssuppe(pos: np.ndarray, tri: np.ndarray) -> np.ndarray:
    """Dreiecke als sortierte (M, 9)-Tabelle der Eckpositionen (float32), unabhängig von der Vertexreihenfolge;
    die Ecken jedes Dreiecks werden zyklisch auf die kleinste Ecke gedreht (Umlaufsinn bleibt)."""
    e = np.asarray(pos, np.float32)[tri]                                  # (M, 3, 3)

    def kleiner(a, b):                                                    # lexikographisch a < b
        return (a[:, 0] < b[:, 0]) | ((a[:, 0] == b[:, 0]) & ((a[:, 1] < b[:, 1]) | ((a[:, 1] == b[:, 1]) & (a[:, 2] < b[:, 2]))))

    start = np.zeros(len(e), np.int64)
    start[kleiner(e[:, 1], e[:, 0])] = 1
    bisher = e[np.arange(len(e)), start]
    start[kleiner(e[:, 2], bisher)] = 2
    idx = (start[:, None] + np.arange(3)[None, :]) % 3
    e = e[np.arange(len(e))[:, None], idx].reshape(-1, 9)
    return e[np.lexsort(e.T[::-1])]


def pruefen(archiv: erp.ErpFile, bau: Bau, quelle=None) -> List[Tuple[str, bool, str]]:
    """Offline-Prüfung eines eingetragenen Modells. ``quelle``: Index mit Spielressourcen, in dem Verweise
    liegen dürfen, die nicht im Archiv sind (vorhandene Materialien und Texturen)."""
    aus: List[Tuple[str, bool, str]] = []

    def ok(name, bedingung, text=""):
        aus.append((name, bool(bedingung), text))

    idx = grafik._als_index(archiv)
    aussen = grafik._als_index(quelle) if quelle is not None else None
    basis = bau.basis
    eigene = [r.kennung for r in bau.ressourcen]
    fehlen = [k for k in eigene if archiv.find(k) is None]
    ok("Ressourcen vorhanden", not fehlen, "%d von %d%s" % (len(eigene) - len(fehlen), len(eigene), (", fehlt " + fehlen[0]) if fehlen else ""))
    if fehlen:
        return aus
    ok("MD5 stimmt", all(archiv.find(k).verify() for k in eigene))
    soll_typ = {r.kennung: (r.typ, r.unknown, [f[2] for f in r.fragmente]) for r in bau.ressourcen}
    falsch = [k for k in eigene if (archiv.find(k).resource_type, archiv.find(k).unknown,
                                    [f.flags for f in archiv.find(k).fragments]) != soll_typ[k]]
    ok("Typ, Typfeld und Fragment-Flags wie die Originale", not falsch, falsch[0] if falsch else "")
    modell = grafik.GfxModell.aus_bytes(idx[grafik.modell_kennung(basis)].data())
    ok("GfxModelRes: Rundlauf bitgleich", modell.to_bytes() == idx[grafik.modell_kennung(basis)].data())
    proxy = grafik.ModellProxy.aus_bytes(idx[grafik.proxy_kennung(basis)].data())
    ok("ModelProxyRes: Rundlauf, Knoten wie im Modell",
       proxy.to_bytes() == idx[grafik.proxy_kennung(basis)].data() and proxy.knoten == modell.knoten)
    ok("Modell: LODs, Knoten, Abstände", modell.lod_anzahl == len(bau.lods) == len(modell.mesh_kennungen)
       and len(modell.knoten) == 1 + len(bau.lods) and len(modell.lod_abstaende) == 2 * len(bau.lods) - 1
       and list(modell.lod_abstaende) == sorted(modell.lod_abstaende), str(modell.lod_abstaende))
    puffer_ok, mesh_ok, geo_ok, kugel_ok, index_ok, einzel = True, True, True, True, True, []
    alle_pos = []
    for li, mk in enumerate(modell.mesh_kennungen):
        roh = idx[mk].data()
        mesh = grafik.GfxMesh.aus_bytes(roh)
        mesh_ok &= mesh.to_bytes() == roh and not mesh.pruefen() and mesh.lod == li
        mesh_ok &= all(e == (1 + li, i) for i, e in enumerate(mesh.eintraege)) and \
            [k.modell_knoten for k in mesh.knoten] == [1 + li]
        vb = [grafik.GfxPuffer.aus_ressource(idx[k]) for k in mesh.vb_kennungen]
        ib = grafik.GfxPuffer.aus_ressource(idx[mesh.ib_kennungen[0]])
        puffer_ok &= not any(p.pruefen() for p in vb + [ib]) and len({p.anzahl for p in vb}) == 1
        puffer_ok &= [(p.stride, p.code) for p in vb] == [(12, 39), (8, 35), (8, 35), (8, 35)] and (ib.stride, ib.code) == (2, 19)
        ind = ib.indizes()
        pos = np.frombuffer(vb[0].daten, "<f4").reshape(-1, 3)
        for t in mesh.teilnetze:
            index_ok &= t.start_index + t.index_anzahl <= len(ind)
            teil = ind[t.start_index:t.start_index + t.index_anzahl].astype(np.int64) + t.basis_vertex
            index_ok &= len(teil) > 0 and teil.max() < vb[0].anzahl
            p = pos[teil].astype(np.float64)
            kugel_ok &= bool((np.sqrt(((p - np.array(t.kugel[:3])) ** 2).sum(1)) <= t.kugel[3] + 1e-6).all())
        geladen = grafik.modell_laden(idx, basis, lod=li)
        ist = np.concatenate([_dreieckssuppe(n.positionen, n.dreiecke) for n in geladen.netze])
        soll = np.concatenate([_dreieckssuppe(t.positionen.astype(np.float32), t.dreiecke) for t in bau.lods[li]])
        ist, soll = ist[np.lexsort(ist.T[::-1])], soll[np.lexsort(soll.T[::-1])]
        gleich = ist.shape == soll.shape and np.array_equal(ist, soll)
        geo_ok &= gleich
        einzel.append("LOD %d: %d Dreiecke%s" % (li, len(ist), "" if gleich else " ≠ Eingabe"))
        # Normalen, Tangenten und UV vertexweise gegen die Eingabe (halbe Genauigkeit)
        n_ist = np.concatenate([n.attribute["NORMAL0"][:, :3] for n in geladen.netze])
        t_ist = np.concatenate([n.attribute["TANGENT0"] for n in geladen.netze])
        laenge = np.linalg.norm(n_ist, axis=1)
        geo_ok &= bool(np.abs(laenge - 1).max() < 2e-3) and set(np.unique(t_ist[:, 3]).tolist()) <= {-1.0, 1.0}
        geo_ok &= bool(np.abs((n_ist * t_ist[:, :3]).sum(1)).max() < 0.02)
        alle_pos.append(np.concatenate([n.positionen for n in geladen.netze]))
    ok("GfxMeshRes: Rundlauf bitgleich, Einträge/Knoten je LOD", mesh_ok)
    ok("Puffer: Köpfe wie die Originale, Größe = Stride × Anzahl", puffer_ok)
    ok("Indizes im gültigen Bereich (Basis-Vertex)", index_ok)
    ok("Geometrie = Eingabe (Dreiecke bitgleich in float32, Normalen/Tangenten gültig)", geo_ok, "; ".join(einzel))
    ok("Hüllkugeln der Teilnetze enthalten ihre Vertices", kugel_ok)
    p = np.concatenate(alle_pos).astype(np.float64)
    ok("AABB = Grenzen der Geometrie", np.allclose(p.min(0), modell.aabb_min, atol=1e-6) and np.allclose(p.max(0), modell.aabb_max, atol=1e-6),
       "%s … %s" % (np.round(modell.aabb_min, 3).tolist(), np.round(modell.aabb_max, 3).tolist()))
    ok("Modellkugel enthält alle Vertices", (np.sqrt(((p - np.array(modell.kugel[:3])) ** 2).sum(1)) <= modell.kugel[3] + 1e-6).all(),
       "Radius %.3f, halbe AABB-Diagonale %.3f" % (modell.kugel[3], float(np.linalg.norm(p.max(0) - p.min(0))) / 2))
    # Material- und Texturverweise
    br = archiv.find(bau.modell + ".emb?context=default")
    if br is None:
        ok("Materialbindung vorhanden", False)
        return aus
    bind = grafik.MaterialBindung.aus_bytes(br.data())
    slots = set()
    for mk in modell.mesh_kennungen:
        slots |= set(grafik.GfxMesh.aus_bytes(idx[mk].data()).materialnamen)
    ok("Bindung: Rundlauf, deckt alle Materialslots", bind.to_bytes() == br.data() and slots <= set(bind.slots),
       ", ".join("%s→%s" % (s, bind.material_fuer(s).rsplit("/", 1)[-1]) for s in bind.slots))
    offen, texturen = [], 0
    for mk in bind.materialien:
        mr = idx.get(mk) or (aussen.get(mk) if aussen else None)
        if mr is None:
            offen.append(mk)
            continue
        mat = grafik.Material.aus_bytes(mr.data())
        for tk in set(list(mat.texturen().values()) + [x.textur for x in mat.parameter if x.klasse == 3 and x.textur]):
            sr = idx.get(tk) or (aussen.get(tk) if aussen else None)
            if sr is None:
                offen.append(tk)
                continue
            sicht = grafik.TexturSicht.aus_bytes(sr.fragments[0].read())
            ir = idx.get(sicht.bild) or (aussen.get(sicht.bild) if aussen else None)
            if ir is None:
                offen.append(sicht.bild)
                continue
            t = grafik.Textur.aus_ressource(ir)
            texturen += 1
            if t.pruefen() or sicht.format != t.format or sicht.mips != t.mips:
                offen.append("fehlerhaft: " + sicht.bild)
    ok("Materialien und Texturen auflösbar und gültig", not offen, "%d Texturen%s" % (texturen, (", offen: " + offen[0]) if offen else ""))
    return aus
