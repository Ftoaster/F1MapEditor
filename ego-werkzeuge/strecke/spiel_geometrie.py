"""Geometrie der generierten Strecke in den Portimão-Träger einsetzen (AP11a).

Ersetzt im geöffneten Träger-Archiv (Portimão-Slot-ERP)

* die sichtbare Fahrbahn ``…/portimao_terrain.idf?context=render`` (TerrainRes) durch das
  Generatornetz plus eine Rasen-Grundfläche, die das Loch des Außenterrains vollständig füllt,
* die Fahrphysik ``…/portimao_terrain.idf?context=physics_terrain`` (TRN~, in Mod 1.0.2 ``portimao_terrain_retained_<hash>.idf``,
  gefunden mit ``physik_kennung``) durch dieselben Dreiecke
  mit Oberflächencodes (Version 2, ``ego.trn_erzeugen``). Von Portimão bleibt keine Physik übrig.

Das Außenterrain ``portimao_outer_terrain`` bleibt unverändert: Es hat ein exakt 2048 × 2048 m
großes Loch (x −920,13…1127,87, z −1008,23…1039,77), in dem die platzierte Strecke vollständig
liegt. Die Grundfläche ist ein 8-m-Raster genau über diesem Loch; ihre Randvertices übernehmen
Lage und Höhe der Innenrand-Vertices des Außenterrains (16-m-Abstand), dazwischen wird linear
interpoliert. Innen ist sie eine harmonische Fläche (Laplace) zwischen Randhöhen und Strecke.

Koordinaten: Generator → Spielwelt nur über ``strecke.platzierung`` (Verschiebung, keine Drehung).

Gesichert (Formatebene): TerrainRes- und TRN~-Schreiber sind geprüft (AP3b/AP4b), Spieltests
2.12b (sichtbares Terrain) und 2.8a (TRN Version 2, Originalkachelung) bestanden.
Annahmen (Spieltest offen): freie TRN-Kachelung (wie 2.8b), Shaderbedeutung von Vertexfarben,
Welt-UV und Zusatz-UV (aus den Vorlagenmaterialien abgeleitet, siehe docs/neue_strecke.md).
"""

import json
import math
import os
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from ego import erp, grafik as g, grafik_terrain as gt, trn
from ego.trn_erzeugen import kantenflags, trn_erzeugen

from . import gelaende, platzierung
from .pruefregeln import mauer_auf_boxenlinie  # Regel BOX_MAUER_AUF_BOXENLINIE (AP24)

BASIS = 'eaid://environment_package/tracks/portimao/terrain/idf/'
TERRAIN = BASIS + 'portimao_terrain.idf?context=render'
AUSSEN = BASIS + 'portimao_outer_terrain.idf?context=render'
PHYSIK = BASIS + 'portimao_terrain.idf?context=physics_terrain'


def physik_kennung(archiv: erp.ErpFile) -> str:
    """Kennung der Fahrphysik (TRN~) des Portimão-Terrains. Mod 1.0.0: ``PHYSIK``; Mod 1.0.2 (neues Konvertierungsskript)
    nennt sie ``portimao_terrain_retained_<hash>.idf?context=physics_terrain``. Bei genau einer passenden TRN~ unter
    ``BASIS`` gilt diese, sonst ``PHYSIK``."""
    if archiv.find(PHYSIK) is not None:
        return PHYSIK
    kandidaten = [r.identifier for r in archiv.resources if r.resource_type == 'TRN~' and r.identifier.startswith(BASIS)
                  and r.identifier.endswith('?context=physics_terrain')]
    return kandidaten[0] if len(kandidaten) == 1 else PHYSIK

#: Abbildung Generatorklasse -> vorhandenes Portimão-Terrainmaterial (Dateiname) und TRN-Code.
#: Begründung und UV-Maßstäbe: docs/neue_strecke.md, Abschnitt „Geometrie“.
KLASSEN = {
    'asphalt': ('por_track_a.material', 'SDT+'),
    'boxengasse': ('por_track_pit_01.material', 'SDT+'),
    'randstein': ('por_rumble_triangle_b_02.material', 'RMBL'),
    'linie': ('por_line_a.material', 'MKNG'),
    'gras': ('por_grass_infield_a_01.material', 'GRS+'),
    'kies': ('por_gravel_a.material', 'HDG+'),
    'mauer': ('por_concrete_a_01.material', 'SCON'),
    'auslauf': ('por_track_pit_01.material', 'SDT+'),     # Asphalt-Auslauf: Boxenasphalt, UV wie Boxengasse
    'startbox': ('por_line_a.material', 'MKNG'),          # Bodenmarkierungen: Linienmaterial, nur sichtbar (keine Physik)
    'gebaeude': ('por_concrete_a_01.material', 'SCON'),   # Garagengebäude der Boxengasse: Beton wie die Mauern
}
#: Klassen, die nur gezeichnet werden (knapp über dem Asphalt liegende Markierungen)
NUR_SICHTBAR = ('startbox',)
#: Klassen, die ältere Generatorausgaben noch nicht kennen
OPTIONAL = ('auslauf', 'startbox', 'gebaeude')
#: Senkrechte/überhängende Bauteile ohne Bodenrolle (Mauern, Gebäude): keine verschweißten Bodennormalen, nicht im
#: Gelände-Rand
BAUTEILE = ('mauer', 'gebaeude')
GRUND = ('por_grass_infield_a_01.material', 'GRS+')

#: Texturperioden der Vorlagen (Meter je UV-Einheit), aus dem Portimão-Original vermessen.
PERIODE_FLAECHE = 32.0      # Asphalt, Boxengasse, Gras, Kies (u und v ≈ 32 m)
PERIODE_RANDSTEIN = 3.4     # Rot/Weiß-Muster längs (u)
RANDSTEIN_V = (0.535, 1.0)  # belegter V-Bereich des Randstein-Atlas (quer)
PERIODE_LINIE = 1.84        # Linie längs (v); u 0…1 über die Linienbreite
PERIODE_MAUER = 4.0

#: Ersatzwerte, falls das Träger-Original nicht mehr lesbar ist (gemessen am Original).
TRN_ATTRIBUT_ERSATZ = {'SDT+': 0xff808080, 'RMBL': 0xff989898, 'MKNG': 0xff808080,
                       'GRS+': 0xff637289, 'HDG+': 0xff6a9ab5, 'SCON': 0xffffffff}
LOCH_ERSATZ = (-920.129638671875, -1008.2256469726562, 2048.0)


class GeometrieFehler(ValueError):
    pass


# ----------------------------------------------------------------------------- Hilfen
def _mat_index(t: g.Terrain, name: str) -> int:
    treffer = [i for i, m in enumerate(t.materialien) if m.endswith('/' + name)]
    if len(treffer) != 1:
        raise GeometrieFehler('Terrain material is missing or ambiguous: %s' % name)
    return treffer[0]


def _periode(laenge: float, soll: float) -> float:
    """Periode nahe ``soll``, die ganzzahlig in die Rundenlänge passt (keine Naht an Start/Ziel)."""
    return laenge / max(1, round(laenge / soll))


def _flaechennormalen(p, tri):
    return np.cross(p[tri[:, 1]] - p[tri[:, 0]], p[tri[:, 2]] - p[tri[:, 0]])


def _verschweisste_normalen(p, tri, raster=0.001):
    """Flächengewichtete Vertexnormalen über alle Dreiecke mit gleicher Position (Bandgrenzen)."""
    key = np.round(p / raster).astype(np.int64)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.ravel()
    fn = _flaechennormalen(p, tri)
    acc = np.zeros((inv.max() + 1, 3))
    for k in range(3):
        np.add.at(acc, inv[tri[:, k]], fn)
    n = acc[inv]
    laenge = np.linalg.norm(n, axis=1, keepdims=True)
    n = np.where(laenge > 1e-12, n / np.maximum(laenge, 1e-12), [0.0, 1.0, 0.0])
    return n


def _teilnetz(tri_global):
    ids, lokal = np.unique(tri_global, return_inverse=True)
    return ids, lokal.reshape(-1, 3)


def _json(x):
    """numpy-Typen für JSON umwandeln."""
    if isinstance(x, dict):
        return {str(k): _json(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_json(v) for v in x]
    if isinstance(x, np.ndarray):
        return _json(x.tolist())
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        return float(x)
    return x


# ----------------------------------------------------------------------------- Generatornetz
def _mauer_auf_boxenlinie_entfernen(netz_npz, vertices, dreiecke, oberflaeche, namen, definition):
    """Entfernt Mauerdreiecke, die auf der Fahrlinie der Boxengasse liegen.

    Spieltest 15.10.2026: Das Ende der Boxenmauer ragte an der Ausfahrt quer über die Boxengasse
    (Generator zieht die Mauer parallel zur Strecke, die Boxenlinie schwenkt dort auf die Strecke ein);
    das Auto kam aus der Box nicht auf die Strecke. Entfernt wird jedes Mauerdreieck, dessen
    Mittelpunkt (Draufsicht) näher als halbe Boxenbreite − 0,5 m an der Boxen-Spline liegt; die
    reguläre Trennmauer (≈ halbe Breite + Mauerabstand) bleibt erhalten."""
    if 'mauer' not in namen:
        return dreiecke, oberflaeche
    pfad = os.path.join(os.path.dirname(os.path.abspath(netz_npz)), 'strecke_logik.json')
    if not os.path.exists(pfad):
        return dreiecke, oberflaeche
    with open(pfad, encoding='utf-8') as fh:
        logik = json.load(fh)
    pit = [s for s in logik['spielformate']['TrackSpace']['splines'] if not s['closed']]
    if not pit:
        return dreiecke, oberflaeche
    # Gassenmitte, nicht die Fahrlinie: Diese liegt im vollbreiten Abschnitt näher an der Mauer (spur_versatz_m), an der
    # Ausfahrt aber auf der Mitte. Kürzte man den Abstand, bliebe dort das Mauerende stehen (Spieltest W12).
    P = np.asarray((logik.get('boxengasse') or {}).get('mitte_position') or pit[0]['position'], np.float64)[:, [0, 2]]
    breite = float((definition.get('boxengasse') or {}).get('breite', 12.0) or 12.0)
    weg = mauer_auf_boxenlinie(vertices, dreiecke, oberflaeche, namen, P, breite)
    if not len(weg):
        return dreiecke, oberflaeche
    behalten = np.ones(len(dreiecke), bool)
    behalten[weg] = False
    return dreiecke[behalten], oberflaeche[behalten]


def netz_laden(netz_npz: str, definition: Optional[dict] = None) -> dict:
    """Lädt das Generatornetz (Generatorkoordinaten) und die nötigen Parameter."""
    d = np.load(netz_npz, allow_pickle=False)
    namen = [str(x) for x in d['oberflaechen_namen']]
    if definition is None:
        pfad = os.path.join(os.path.dirname(os.path.abspath(netz_npz)), 'definition_aufgeloest.json')
        definition = {}
        if os.path.exists(pfad):
            with open(pfad, encoding='utf-8') as fh:
                definition = json.load(fh)
    geo = definition.get('geometrie', {})
    dreiecke = d['dreiecke'].astype(np.int64)
    oberflaeche = d['oberflaeche'].astype(np.int64)
    dreiecke, oberflaeche = _mauer_auf_boxenlinie_entfernen(
        netz_npz, d['vertices'].astype(np.float64), dreiecke, oberflaeche, namen, definition)
    return dict(
        vertices=d['vertices'].astype(np.float64), normalen=d['normalen'].astype(np.float64),
        uv=d['uv'].astype(np.float64), uv32=d['uv'].astype(np.float32),
        dreiecke=dreiecke, oberflaeche=oberflaeche,
        namen=namen, codes=[str(x) for x in d['oberflaechen_codes']],
        tl=float(geo.get('textur_laenge_m', 10.0)), tk=float(geo.get('randstein_textur_m', 4.0)),
        linienbreite=float((definition.get('fahrbahn') or {}).get('randlinie_breite', 0.25) or 0.25),
        randsteinbreite=float((definition.get('randstein') or {}).get('breite', 1.5) or 1.5),
        barrierenhoehe=float((definition.get('barriere') or {}).get('hoehe', 1.2) or 1.2))


# ----------------------------------------------------------------------------- Vorlagen
def vorlagen_lesen(archiv: erp.ErpFile, materialien: Sequence[str], codes: Sequence[str]) -> dict:
    """Leitet Vorbelegungen aus dem **originalen** Träger ab (vor dem Ersetzen aufrufen).

    * Vertexfarbe: Kanal-Median des Vorlagenmaterials (Annahme; Shaderbedeutung offen).
    * Welt-UV (Strom 4.zw): konstanter Wert eines repräsentativen Originalvertex des Materials:
      dessen Makrokartenfarbe (5×5-Texelmittel) liegt am nächsten am Median aller Vertices des
      Materials, bei möglichst gleichmäßiger Umgebung. Begründung: Makro-, Blend- und
      Grashöhenkarte sind Portimão-Luftbilder; echte Weltprojektion zeigte die alte Strecke als
      „Geister“ auf der neuen Fläche.
    * TRN-Vertexattribut: häufigster Wert je Oberflächencode in der Original-TRN.
    """
    t = g.Terrain.aus_bytes(archiv.find(TERRAIN).data())
    netze = gt.terrain_lesen(archiv, TERRAIN)
    je_mat = {n.material: n for n in netze}
    erg = {'farben': {}, 'uv_welt': {}, 'uv_welt_punkt': {}, 'uv_welt_makrofarbe': {},
           'trn_attribut': {}, 'ersatz': []}
    for name in materialien:
        m = _mat_index(t, name)
        n = je_mat.get(m)
        if n is None or n.farben is None:
            erg['farben'][name] = [0, 0, 0, 255]
            erg['uv_welt'][name] = [0.5, 0.5]
            erg['ersatz'].append('Material %s ohne Originalzeichnungen: Ersatzwerte' % name)
            continue
        erg['farben'][name] = [int(v) for v in np.round(np.median(n.farben.astype(float), axis=0))]
        makro = _makrokarte(archiv, t.materialien[m])
        uvw = n.uv_welt.astype(np.float64)
        if makro is None:
            k = int(np.argmin(np.abs(uvw - np.median(uvw, axis=0)).sum(1)))
            erg['ersatz'].append('Makrokarte von %s nicht lesbar: Median-Welt-UV' % name)
        else:
            bild, mittel, streuung = makro
            hh, ww = bild.shape[:2]
            px = np.clip((uvw[:, 0] * ww).astype(np.int64), 0, ww - 1)
            py = np.clip((uvw[:, 1] * hh).astype(np.int64), 0, hh - 1)
            farbe = bild[py, px]
            typisch = np.median(farbe, axis=0)
            # typische Farbe des Materials, in gleichmäßiger Umgebung (5 × 5 Texel)
            score = np.linalg.norm(mittel[py, px] - typisch, axis=1) + 2.0 * streuung[py, px]
            k = int(np.argmin(score))
            erg['uv_welt_makrofarbe'][name] = [float(v) for v in mittel[py[k], px[k]]]
        erg['uv_welt'][name] = [float(v) for v in n.uv_welt[k]]
        erg['uv_welt_punkt'][name] = [float(v) for v in n.positionen[k]]
    try:
        kacheln = trn.open_trn(archiv.find(physik_kennung(archiv)).data()).geometry()
        v, tri, cs, attr, _, _ = trn.mesh_mit_attributen(kacheln)
        cs = np.array([c if isinstance(c, str) else c.decode('latin1') for c in cs])
        for code in codes:
            sel = cs == code
            if sel.any():
                werte, anzahl = np.unique(attr[tri[sel]].ravel(), return_counts=True)
                erg['trn_attribut'][code] = int(werte[np.argmax(anzahl)])
    except Exception as exc:  # Original-TRN nicht lesbar: dokumentierte Ersatzwerte
        erg['ersatz'].append('Original-TRN nicht lesbar (%s)' % exc)
    for code in codes:
        if code not in erg['trn_attribut']:
            erg['trn_attribut'][code] = TRN_ATTRIBUT_ERSATZ.get(code, 0xffffffff)
            erg['ersatz'].append('TRN-Attribut %s: Ersatzwert' % code)
    return erg


def _makrokarte(archiv: erp.ErpFile, material_kennung: str):
    """Makrokarte (me_macroMap) des Materials: Bild, 5×5-Mittel und 5×5-Streuung (float, RGB)."""
    try:
        from ego import grafik_textur as gx
        mat = g.Material.aus_bytes(archiv.find(material_kennung).data())
        sicht_id = mat.texturen().get('me_macroMap')
        sicht = g.TexturSicht.aus_bytes(archiv.find(sicht_id).data())
        bild = np.asarray(gx.textur_bild(g.Textur.aus_ressource(archiv.find(sicht.bild))).convert('RGB'),
                          dtype=np.float64)
    except Exception:
        return None
    r = 2
    pad = np.pad(bild, ((r, r), (r, r), (0, 0)), mode='edge')
    summe = np.zeros_like(bild); quad = np.zeros_like(bild)
    for dy in range(2 * r + 1):
        for dx in range(2 * r + 1):
            f = pad[dy:dy + bild.shape[0], dx:dx + bild.shape[1]]
            summe += f; quad += f * f
    anzahl = (2 * r + 1) ** 2
    mittel = summe / anzahl
    streuung = np.sqrt(np.maximum(quad / anzahl - mittel ** 2, 0).sum(2))
    return bild, mittel, streuung


def loch_lesen(archiv: erp.ErpFile) -> dict:
    """Innenrand des Außenterrains: Box (x0, z0, Kante) und Randvertices (x, y, z)."""
    res = archiv.find(AUSSEN)
    if res is None:
        x0, z0, kante = LOCH_ERSATZ
        return dict(x0=x0, z0=z0, kante=kante, rand=np.zeros((0, 3)), ersatz=True)
    rand = []
    for n in gt.terrain_lesen(archiv, AUSSEN):
        p = n.positionen.astype(np.float64)
        key = np.round(p[:, (0, 2)] * 1000).astype(np.int64)
        _, erst, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
        tt = inv.ravel()[n.dreiecke]
        e = np.sort(np.concatenate([tt[:, [0, 1]], tt[:, [1, 2]], tt[:, [2, 0]]]), axis=1)
        ee, cnt = np.unique(e, axis=0, return_counts=True)
        q = p[erst[np.unique(ee[cnt == 1])]]
        rand.append(q)
    rand = np.concatenate(rand)
    # Innenrand = Randvertices im Umkreis von 2,5 km (die übrigen Ränder liegen bei ±3 km und weiter)
    innen = rand[np.max(np.abs(rand[:, (0, 2)]), axis=1) < 2500]
    if len(innen) < 16:
        x0, z0, kante = LOCH_ERSATZ
        return dict(x0=x0, z0=z0, kante=kante, rand=np.zeros((0, 3)), ersatz=True)
    x0, x1 = innen[:, 0].min(), innen[:, 0].max()
    z0, z1 = innen[:, 2].min(), innen[:, 2].max()
    kante = float(max(x1 - x0, z1 - z0))
    return dict(x0=float(x0), z0=float(z0), kante=kante, rand=innen, ersatz=False)


# ----------------------------------------------------------------------------- Rendernetze
def _strecken_uv(netz: dict, pos: np.ndarray) -> Dict[str, object]:
    """UV, Zusatz-UV und Normalen je Klasse aus den Generator-UVs (s, Querabstand) ableiten."""
    tri, o, uvg = netz['dreiecke'], netz['oberflaeche'], netz['uv']
    namen = netz['namen']
    k = {n: i for i, n in enumerate(namen)}
    tl, tk = netz['tl'], netz['tk']
    s = uvg[:, 1] * tl
    rs = np.zeros(len(pos), dtype=bool)
    rs[np.unique(tri[o == k['randstein']])] = True
    s[rs] = uvg[rs, 1] * tk
    quer = uvg[:, 0] * tl  # Abstand vom Fahrbahnrand (außer Fahrbahnbänder)
    # Fahrbahnbänder (Asphalt + Linien): je Station Breite W aus den Randvertices u=0/u=1
    fb = np.zeros(len(pos), dtype=bool)
    for name in ('asphalt', 'linie'):
        fb[np.unique(tri[o == k[name]])] = True
    idx = np.flatnonzero(fb)
    vkey = netz['uv32'][idx, 1]
    reihen = np.argsort(vkey, kind='stable')
    grenzen = np.r_[0, np.flatnonzero(np.diff(vkey[reihen])) + 1, len(idx)]
    breite = np.full(len(pos), np.nan)
    stationen_ohne_rand = 0
    for a, b in zip(grenzen[:-1], grenzen[1:]):
        gruppe = idx[reihen[a:b]]
        u = uvg[gruppe, 0]
        i0, i1 = gruppe[np.argmin(u)], gruppe[np.argmax(u)]
        if uvg[i0, 0] > 1e-4 or uvg[i1, 0] < 1 - 1e-4:
            stationen_ohne_rand += 1
            continue
        breite[gruppe] = np.linalg.norm(pos[i1, (0, 2)] - pos[i0, (0, 2)])
    luecke = fb & np.isnan(breite)
    breite[luecke] = np.nanmedian(breite[fb])
    laenge = float(s[fb].max())
    return dict(s=s, quer=quer, breite=breite, laenge=laenge, stationen_ohne_rand=stationen_ohne_rand)


def welt_uv_bereich(loch: dict, pos: np.ndarray, rand: float = 50.0) -> Tuple[float, float, float]:
    """Quadrat (x0, z0, Kante) der echten Welt-UV: Loch des Außenterrains vereinigt mit der Strecke (+ ``rand``).
    Ragt eine Strecke über das Loch hinaus (Prüfregel LAGE_AUSSERHALB_AO ist nur eine Warnung), bliebe die Welt-UV
    sonst nicht in 0…1 und die Karten würden von der Gegenseite wiederholt. Die Weltkarten sind mit demselben Quadrat
    zu erzeugen."""
    x0 = min(loch['x0'], float(pos[:, 0].min()) - rand)
    z0 = min(loch['z0'], float(pos[:, 2].min()) - rand)
    x1 = max(loch['x0'] + loch['kante'], float(pos[:, 0].max()) + rand)
    z1 = max(loch['z0'] + loch['kante'], float(pos[:, 2].max()) + rand)
    return x0, z0, max(x1 - x0, z1 - z0)


def _welt_uv(vorlagen: dict, material: str, p: np.ndarray) -> np.ndarray:
    """Welt-UV (Strom 4.zw) je Vertex: konstant (Vorlagentexel, Standard) oder bei ``vorlagen['_welt_uv_loch']``
    echt projiziert ``((x − x0) / Kante, (z − z0) / Kante)`` über ``welt_uv_bereich`` (Loch ∪ Strecke), wie Portimãos Original
    (docs/welt_texturen.md, Abschnitt 2.1). Echt nur zusammen mit eigenen Weltkarten sinnvoll (sonst Geister)."""
    loch = vorlagen.get('_welt_uv_loch')
    if loch is None:
        return np.tile(vorlagen['uv_welt'][material], (len(p), 1))
    x0, z0, kante = loch
    return np.c_[(p[:, 0] - x0) / kante, (p[:, 2] - z0) / kante]


def render_netze(netz: dict, pos: np.ndarray, t: g.Terrain, vorlagen: dict) -> Tuple[List[gt.TerrainNetz], dict]:
    """TerrainNetz je Generatorklasse (Spielkoordinaten), Material und Attribute nach Vorlage."""
    tri, o, uvg = netz['dreiecke'], netz['oberflaeche'], netz['uv']
    k = {n: i for i, n in enumerate(netz['namen'])}
    su = _strecken_uv(netz, pos)
    s, quer, W, L = su['s'], su['quer'], su['breite'], su['laenge']
    boden = ~np.isin(o, [k[n] for n in BAUTEILE if n in k])
    nrm_boden = _verschweisste_normalen(pos, tri[boden])
    p_flaeche = _periode(L, PERIODE_FLAECHE)
    p_rand = _periode(L, PERIODE_RANDSTEIN)
    p_linie = _periode(L, PERIODE_LINIE)
    netze, info = [], {}

    def anfuegen(klasse, sel_tri, uv0, zus, normalen):
        if not len(sel_tri):                  # z. B. Ring ohne Randsteine: leere Klassen entfallen
            return
        material = KLASSEN[klasse][0]
        ids, lokal = _teilnetz(sel_tri)
        n = len(ids)
        welt = _welt_uv(vorlagen, material, pos[ids])
        zusatz = zus(ids, welt) if zus is not None else np.c_[uv0[ids], welt]
        netze.append(gt.TerrainNetz(
            _mat_index(t, material), pos[ids], lokal, uv0[ids], normalen=normalen[ids],
            farben=np.tile(vorlagen['farben'][material], (n, 1)).astype(np.float64),
            uv_welt=welt, uv_zusatz=zusatz, normale_w=np.zeros(n)))
        eintrag = info.setdefault(klasse, dict(material=material, dreiecke=0, vertices=0))
        eintrag['dreiecke'] += int(len(lokal))
        eintrag['vertices'] += int(n)

    # Asphalt: u quer (32 m), v längs (−s/32 m), Zusatz = (UV0, Rundenfortschritt, 1 − u_quer). Auch unter den Linien:
    # por_line_a (ts_painted_line) wird überblendet und ist außerhalb der Farbe durchsichtig; im Original liegen die
    # Linien deckungsgleich auf durchgehendem Asphalt (Trailer 3.11.2026: Linien ohne Asphalt waren durchsichtig).
    dr = uvg[:, 0] * np.nan_to_num(W)
    uv0_asph = np.c_[dr / PERIODE_FLAECHE, -s / p_flaeche]
    anfuegen('asphalt', tri[(o == k['asphalt']) | (o == k['linie'])], uv0_asph,
             lambda ids, welt: np.c_[uv0_asph[ids], s[ids] / L, 1.0 - uvg[ids, 0]], nrm_boden)
    # Boxengasse: wie Asphalt, Zusatz.zw = Welt-UV (wie por_track_pit_01 im Original)
    uv0_box = np.c_[quer / PERIODE_FLAECHE, -s / p_flaeche]
    anfuegen('boxengasse', tri[o == k['boxengasse']], uv0_box, None, nrm_boden)
    if 'auslauf' in k and np.any(o == k['auslauf']):
        anfuegen('auslauf', tri[o == k['auslauf']], uv0_box, None, nrm_boden)
    if 'startbox' in k and np.any(o == k['startbox']):
        # Generator-UV: u 0…1 quer über den Strich, v Meter längs → Linientextur wie die Randlinien
        uv0_box_linie = np.c_[uvg[:, 0], uvg[:, 1] / PERIODE_LINIE]
        anfuegen('startbox', tri[o == k['startbox']], uv0_box_linie,
                 lambda ids, welt: np.c_[uv0_box_linie[ids], np.zeros(len(ids)), np.ones(len(ids))], nrm_boden)
    # Randstein: u längs (Muster 3,4 m), v quer im Atlasbereich 0,535…1
    t_quer = np.clip(quer / netz['randsteinbreite'], 0, 1)
    uv0_rs = np.c_[s / p_rand, RANDSTEIN_V[0] + (RANDSTEIN_V[1] - RANDSTEIN_V[0]) * t_quer]
    anfuegen('randstein', tri[o == k['randstein']], uv0_rs,
             lambda ids, welt: np.c_[quer[ids] / PERIODE_FLAECHE, -s[ids] / p_flaeche,
                                     np.full(len(ids), 0.25), uv0_rs[ids, 0]], nrm_boden)
    # Linien: Startlinie (zwischen Station 0 und 1) und Randlinien getrennt (eigene Vertices)
    lt = tri[o == k['linie']]
    if len(lt):
        s_werte = np.unique(np.round(s[np.unique(lt)], 4))
        s_b = s_werte[1] if len(s_werte) > 1 else s_werte[0]
        start = s[lt].max(1) <= s_b + 1e-3
        lb = max(float(s_b), 1e-3)
        uv0_start = np.c_[np.clip(s / lb, 0, 1), dr / PERIODE_LINIE]
        lw = netz['linienbreite']
        rechts = uvg[:, 0] < 0.5
        u_rand = np.where(rechts, dr, np.nan_to_num(W) - dr) / lw
        uv0_rand = np.c_[np.clip(u_rand, 0, 1), s / p_linie]
        def zus_linie(ids, welt):
            return np.c_[dr[ids] / PERIODE_FLAECHE, -s[ids] / p_flaeche, np.zeros(len(ids)), np.ones(len(ids))]
        if start.any():
            anfuegen('linie', lt[start], uv0_start, zus_linie, nrm_boden)
        if (~start).any():
            anfuegen('linie', lt[~start], uv0_rand, zus_linie, nrm_boden)
        info['linie']['startlinie_dreiecke'] = int(start.sum())
    # Gras: u längs, v quer (je 32 m); Kies: u quer, v längs
    anfuegen('gras', tri[o == k['gras']], np.c_[s / p_flaeche, quer / PERIODE_FLAECHE], None, nrm_boden)
    anfuegen('kies', tri[o == k['kies']], np.c_[quer / PERIODE_FLAECHE, s / p_flaeche], None, nrm_boden)
    # Mauern: Beton, 4-m-Kachel; Normalen des Generators (harte Kanten zwischen den Flächen)
    anfuegen('mauer', tri[o == k['mauer']], uvg * netz['tl'] / PERIODE_MAUER, None, netz['normalen'])
    if 'gebaeude' in k and np.any(o == k['gebaeude']):
        anfuegen('gebaeude', tri[o == k['gebaeude']], uvg * netz['tl'] / PERIODE_MAUER, None, netz['normalen'])
    info['_uv'] = dict(rundenlaenge_m=L, periode_flaeche_m=p_flaeche, periode_randstein_m=p_rand,
                       periode_linie_m=p_linie, stationen_ohne_fahrbahnrand=su['stationen_ohne_rand'],
                       fahrbahnbreite_m=[float(np.nanmin(W)), float(np.nanmax(W))])
    return netze, info


# ----------------------------------------------------------------------------- Grundfläche
def _abdeckung(T: np.ndarray, x0: float, z0: float, schritt: float, n: int) -> np.ndarray:
    """Höchste Streckenhöhe über jedem Gitterpunkt (n+1)², −inf = nicht überdeckt (Draufsicht)."""
    h = np.full((n + 1) * (n + 1), -np.inf)
    a, b, c = T[:, 0], T[:, 1], T[:, 2]
    det = (b[:, 0] - a[:, 0]) * (c[:, 2] - a[:, 2]) - (c[:, 0] - a[:, 0]) * (b[:, 2] - a[:, 2])
    ok = np.abs(det) > 1e-9
    a, b, c, det = a[ok], b[ok], c[ok], det[ok]
    xs = np.stack([a[:, 0], b[:, 0], c[:, 0]], 1)
    zs = np.stack([a[:, 2], b[:, 2], c[:, 2]], 1)
    i0 = np.clip(np.ceil((xs.min(1) - x0) / schritt - 1e-9), 0, n).astype(np.int64)
    i1 = np.clip(np.floor((xs.max(1) - x0) / schritt + 1e-9), -1, n).astype(np.int64)
    j0 = np.clip(np.ceil((zs.min(1) - z0) / schritt - 1e-9), 0, n).astype(np.int64)
    j1 = np.clip(np.floor((zs.max(1) - z0) / schritt + 1e-9), -1, n).astype(np.int64)
    ni, nj = i1 - i0 + 1, j1 - j0 + 1
    for di in range(int(max(ni.max(initial=0), 0))):
        for dj in range(int(max(nj.max(initial=0), 0))):
            m = np.flatnonzero((di < ni) & (dj < nj))
            if not len(m):
                continue
            ii, jj = i0[m] + di, j0[m] + dj
            px, pz = x0 + ii * schritt, z0 + jj * schritt
            l1 = ((px - a[m, 0]) * (c[m, 2] - a[m, 2]) - (c[m, 0] - a[m, 0]) * (pz - a[m, 2])) / det[m]
            l2 = ((b[m, 0] - a[m, 0]) * (pz - a[m, 2]) - (px - a[m, 0]) * (b[m, 2] - a[m, 2])) / det[m]
            l0 = 1 - l1 - l2
            innen = (l0 >= -1e-7) & (l1 >= -1e-7) & (l2 >= -1e-7)
            y = l0 * a[m, 1] + l1 * b[m, 1] + l2 * c[m, 1]
            np.maximum.at(h, (jj * (n + 1) + ii)[innen], y[innen])
    return h.reshape(n + 1, n + 1)  # [j (z), i (x)]


def _proben(T: np.ndarray, abstand: float = 2.0) -> np.ndarray:
    """Prüfpunkte auf den Streckendreiecken: Ecken, Kanten (≤ abstand) und Schwerpunkte."""
    punkte = [T.reshape(-1, 3), T.mean(1)]
    for a, b in ((0, 1), (1, 2), (2, 0)):
        p, q = T[:, a], T[:, b]
        anzahl = np.ceil(np.linalg.norm((q - p)[:, (0, 2)], axis=1) / abstand).astype(np.int64)
        for k in range(1, int(anzahl.max(initial=1))):
            m = anzahl > k
            f = (k / anzahl[m])[:, None]
            punkte.append(p[m] + f * (q[m] - p[m]))
    return np.concatenate(punkte)


def _laplace(H: np.ndarray, fest: np.ndarray, toleranz=1e-3, max_iter=20000) -> int:
    """Rot-Schwarz-SOR für harmonische Höhen an freien Gitterpunkten (in place)."""
    n = H.shape[0]
    omega = 2.0 / (1.0 + math.sin(math.pi / n))
    jj, ii = np.indices(H.shape)
    farben = [((ii + jj) % 2 == f) & ~fest for f in (0, 1)]
    for f in farben:  # Rand ist immer fest
        f[0, :] = f[-1, :] = f[:, 0] = f[:, -1] = False
    for it in range(max_iter):
        aenderung = 0.0
        for f in farben:
            mittel = np.zeros_like(H)
            mittel[1:-1, 1:-1] = 0.25 * (H[:-2, 1:-1] + H[2:, 1:-1] + H[1:-1, :-2] + H[1:-1, 2:])
            d = omega * (mittel[f] - H[f])
            H[f] += d
            if len(d):
                aenderung = max(aenderung, float(np.abs(d).max()))
        if aenderung < toleranz:
            return it + 1
    return max_iter


def grundflaeche(T_boden: np.ndarray, T_mauer_oben: np.ndarray, loch: dict, *, zelle: float = 8.0,
                 abstand_unter_strecke: float = 0.3, mindestabstand: float = 0.15,
                 barrierenhoehe: float = 1.2) -> dict:
    """Rasen-Grundfläche über dem Loch des Außenterrains (Spielkoordinaten).

    Rückgabe: Positionen (Gitter), Dreiecke Render (ohne vollständig überdeckte), Dreiecke Physik
    (nur ohne Berührung der Strecke, damit die TRN wie das Original keine gestapelten Flächen hat).
    """
    x0, z0, kante = loch['x0'], loch['z0'], loch['kante']
    n = int(round(kante / zelle))
    zelle = kante / n
    fein = _abdeckung(np.concatenate([T_boden, T_mauer_oben - [0, barrierenhoehe, 0]]),
                      x0, z0, zelle / 2, 2 * n)
    bedeckt_fein = np.isfinite(fein)
    hv = fein[::2, ::2]
    bedeckt = np.isfinite(hv)
    # Randhöhen aus den Innenrand-Vertices des Außenterrains (exakte Lage übernehmen)
    H = np.zeros((n + 1, n + 1))
    X = x0 + np.arange(n + 1)[None, :] * zelle + np.zeros((n + 1, 1))
    Z = z0 + np.arange(n + 1)[:, None] * zelle + np.zeros((1, n + 1))
    fest = np.zeros_like(bedeckt)
    fest[0, :] = fest[-1, :] = fest[:, 0] = fest[:, -1] = True
    rand = loch['rand']
    rand_treffer = 0
    if len(rand):
        for seite in range(4):
            if seite in (0, 1):  # z = z0 bzw. z1, entlang x
                zz = z0 if seite == 0 else z0 + kante
                q = rand[np.abs(rand[:, 2] - zz) < 0.01]
                q = q[np.argsort(q[:, 0])]
                if len(q) < 2:
                    continue
                zeile = 0 if seite == 0 else n
                H[zeile, :] = np.interp(X[zeile, :], q[:, 0], q[:, 1])
                for i in range(n + 1):
                    d = np.abs(q[:, 0] - X[zeile, i])
                    if d.min() < 0.01:
                        X[zeile, i] = q[d.argmin(), 0]; Z[zeile, i] = q[d.argmin(), 2]
                        H[zeile, i] = q[d.argmin(), 1]; rand_treffer += 1
            else:  # x = x0 bzw. x1, entlang z
                xx = x0 if seite == 2 else x0 + kante
                q = rand[np.abs(rand[:, 0] - xx) < 0.01]
                q = q[np.argsort(q[:, 2])]
                if len(q) < 2:
                    continue
                spalte = 0 if seite == 2 else n
                H[:, spalte] = np.interp(Z[:, spalte], q[:, 2], q[:, 1])
                for j in range(n + 1):
                    d = np.abs(q[:, 2] - Z[j, spalte])
                    if d.min() < 0.01:
                        X[j, spalte] = q[d.argmin(), 0]; Z[j, spalte] = q[d.argmin(), 2]
                        H[j, spalte] = q[d.argmin(), 1]; rand_treffer += 1
    if np.any(bedeckt[0, :] | bedeckt[-1, :] | bedeckt[:, 0] | bedeckt[:, -1]):
        raise GeometrieFehler('Track reaches the edge of the outer terrain hole')
    H[bedeckt] = hv[bedeckt] - abstand_unter_strecke
    fest |= bedeckt
    frei = ~fest
    H[frei] = np.mean(H[fest])
    iterationen = _laplace(H, fest)
    # Absenken, bis die Grundfläche überall mindestens ``mindestabstand`` unter der Strecke liegt
    proben = np.concatenate([_proben(T_boden), _proben(T_mauer_oben) - [0, barrierenhoehe, 0]])
    fx = (proben[:, 0] - x0) / zelle
    fz = (proben[:, 2] - z0) / zelle
    ci = np.clip(np.floor(fx).astype(np.int64), 0, n - 1)
    cj = np.clip(np.floor(fz).astype(np.int64), 0, n - 1)
    ux, uz = fx - ci, fz - cj
    b_dreieck = (ux + uz) > 1
    vid = lambda j, i: j * (n + 1) + i
    ecken = np.where(b_dreieck[:, None],
                     np.stack([vid(cj, ci + 1), vid(cj + 1, ci), vid(cj + 1, ci + 1)], 1),
                     np.stack([vid(cj, ci), vid(cj + 1, ci), vid(cj, ci + 1)], 1))
    gewichte = np.where(b_dreieck[:, None],
                        np.stack([1 - uz, 1 - ux, ux + uz - 1], 1),
                        np.stack([1 - ux - uz, uz, ux], 1))
    Hf = H.ravel()
    randmaske = np.zeros_like(fest); randmaske[0, :] = randmaske[-1, :] = randmaske[:, 0] = randmaske[:, -1] = True
    randf = randmaske.ravel()
    absenk_runden = 0
    for absenk_runden in range(1, 101):
        verletzung = (Hf[ecken] * gewichte).sum(1) - (proben[:, 1] - mindestabstand)
        schlecht = verletzung > 1e-4
        if not schlecht.any():
            break
        senken = np.zeros_like(Hf)
        for k in range(3):
            np.maximum.at(senken, ecken[schlecht, k], verletzung[schlecht] + 1e-4)
        senken[randf] = 0
        Hf -= senken
    verletzung = (Hf[ecken] * gewichte).sum(1) - (proben[:, 1] - mindestabstand)
    # Dreiecke des Gitters (Wicklung: Normale +Y wie Original und Generator)
    cj_, ci_ = np.indices((n, n))
    v00, v10, v01, v11 = vid(cj_, ci_), vid(cj_, ci_ + 1), vid(cj_ + 1, ci_), vid(cj_ + 1, ci_ + 1)
    A = np.stack([v00, v01, v10], -1).reshape(-1, 3)
    B = np.stack([v10, v01, v11], -1).reshape(-1, 3)
    # Abdeckung der Ecken und Kantenmitten (feines Gitter, Index 2j, 2i)
    bf = bedeckt_fein
    jA, iA = 2 * cj_, 2 * ci_
    voll_A = (bf[jA, iA] & bf[jA + 2, iA] & bf[jA, iA + 2] & bf[jA + 1, iA] & bf[jA + 1, iA + 1] & bf[jA, iA + 1])
    voll_B = (bf[jA, iA + 2] & bf[jA + 2, iA] & bf[jA + 2, iA + 2] & bf[jA + 1, iA + 1]
              & bf[jA + 2, iA + 1] & bf[jA + 1, iA + 2])
    ber_A = (bf[jA, iA] | bf[jA + 2, iA] | bf[jA, iA + 2] | bf[jA + 1, iA] | bf[jA + 1, iA + 1] | bf[jA, iA + 1])
    ber_B = (bf[jA, iA + 2] | bf[jA + 2, iA] | bf[jA + 2, iA + 2] | bf[jA + 1, iA + 1]
             | bf[jA + 2, iA + 1] | bf[jA + 1, iA + 2])
    tri_id = np.where(b_dreieck, 2 * (cj * n + ci) + 1, 2 * (cj * n + ci))
    getroffen = np.zeros(2 * n * n, dtype=bool)
    getroffen[tri_id] = True
    alle = np.stack([A, B], 1).reshape(-1, 3)
    voll = np.stack([voll_A, voll_B], -1).ravel()
    beruehrt = np.stack([ber_A, ber_B], -1).ravel() | getroffen
    pos = np.stack([X.ravel(), Hf, Z.ravel()], 1)
    neigung = _neigung_grad(pos, alle[~voll])
    return dict(positionen=pos, render=alle[~voll], physik=alle[~beruehrt], zelle=zelle, anzahl=n,
                laplace_iterationen=iterationen, absenk_runden=absenk_runden,
                rand_vertices_uebernommen=rand_treffer, proben=len(proben),
                min_abstand_unter_strecke=float(-verletzung.max() + mindestabstand),
                dreiecke_gesamt=len(alle), dreiecke_voll_ueberdeckt=int(voll.sum()),
                dreiecke_strecke_beruehrt=int(beruehrt.sum()), neigung_grad_max=neigung[0],
                neigung_grad_p99=neigung[1], hoehe=(float(Hf.min()), float(Hf.max())))


def _neigung_grad(pos, tri):
    fn = _flaechennormalen(pos, tri)
    ny = np.abs(fn[:, 1]) / np.maximum(np.linalg.norm(fn, axis=1), 1e-12)
    w = np.degrees(np.arccos(np.clip(ny, 0, 1)))
    return float(w.max()), float(np.quantile(w, 0.99))


# ----------------------------------------------------------------------------- Physik-Varianten (AP15)
#: Klassen der befahrbaren Fahrbahn (Physikumfang 'fahrbahn')
FAHRBAHN_KLASSEN = ('asphalt', 'boxengasse', 'linie', 'randstein', 'auslauf')


def physik_unterteilen(v, t, attr, kante_max, auswahl=None, max_runden=16):
    """Unterteilt Dreiecke, bis keine Kante der Auswahl länger als ``kante_max`` ist (AP15).

    Konform (ohne T-Stöße): Jede zu lange Kante wird global markiert und in allen anliegenden
    Dreiecken an derselben Mitte geteilt (1 Kante → 2, 2 Kanten → 3 mit kürzerer Diagonale,
    3 Kanten → 4 Dreiecke). Umlaufsinn bleibt; neue Vertices liegen auf den Kanten (Fläche und
    Form unverändert). Erwartet verschweißte Vertices (gemeinsame Kanten = gleiche Indices).
    Rückgabe: vertices, dreiecke, attribute, eltern (Quelldreieck je neuem Dreieck), runden.
    """
    v = np.asarray(v, np.float64)
    t = np.asarray(t, np.int64)
    attr = np.asarray(attr)
    eltern = np.arange(len(t))
    erlaubt = np.ones(len(t), bool) if auswahl is None else np.asarray(auswahl, bool)
    runden = 0
    for runden in range(1, max_runden + 1):
        a, b = t, np.roll(t, -1, axis=1)                      # Kante e = (v_e, v_e+1)
        laenge = np.linalg.norm(v[b] - v[a], axis=2)
        lang = (laenge > kante_max) & erlaubt[eltern][:, None]
        if not lang.any():
            runden -= 1
            break
        n = len(v)
        lo, hi = np.minimum(a, b), np.maximum(a, b)
        schluessel = lo * n + hi
        markiert = np.unique(schluessel[lang])
        geteilt = np.isin(schluessel, markiert)
        kl, kh = markiert // n, markiert % n
        v = np.concatenate([v, (v[kl] + v[kh]) * 0.5])
        attr = np.concatenate([attr, attr[kl]])
        mitte = np.full(schluessel.shape, -1, np.int64)
        mitte[geteilt] = n + np.searchsorted(markiert, schluessel[geteilt])
        anzahl = geteilt.sum(1)
        neu_t, neu_e = [t[anzahl == 0]], [eltern[anzahl == 0]]
        zeilen = np.flatnonzero(anzahl == 1)
        if len(zeilen):                                   # geteilte Kante nach vorn drehen
            k = np.argmax(geteilt[zeilen], axis=1)
            c0, c1, c2 = (t[zeilen, (k + i) % 3] for i in range(3))
            m = mitte[zeilen, k]
            neu_t += [np.c_[c0, m, c2], np.c_[m, c1, c2]]
            neu_e += [eltern[zeilen]] * 2
        zeilen = np.flatnonzero(anzahl == 2)
        if len(zeilen):                                   # ungeteilte Kante nach hinten (Kante 2)
            u = np.argmin(geteilt[zeilen], axis=1)
            c0, c1, c2 = (t[zeilen, (u + 1 + i) % 3] for i in range(3))
            m0, m1 = mitte[zeilen, (u + 1) % 3], mitte[zeilen, (u + 2) % 3]
            neu_t.append(np.c_[m0, c1, m1])
            d_a = np.linalg.norm(v[c0] - v[m1], axis=1)   # Diagonale c0–m1 oder m0–c2
            d_b = np.linalg.norm(v[m0] - v[c2], axis=1)
            q = d_a <= d_b
            neu_t.append(np.where(q[:, None], np.c_[c0, m0, m1], np.c_[c0, m0, c2]))
            neu_t.append(np.where(q[:, None], np.c_[c0, m1, c2], np.c_[m0, m1, c2]))
            neu_e += [eltern[zeilen]] * 3
        zeilen = np.flatnonzero(anzahl == 3)
        if len(zeilen):
            c0, c1, c2 = t[zeilen, 0], t[zeilen, 1], t[zeilen, 2]
            m0, m1, m2 = mitte[zeilen, 0], mitte[zeilen, 1], mitte[zeilen, 2]
            neu_t += [np.c_[c0, m0, m2], np.c_[m0, c1, m1], np.c_[m2, m1, c2], np.c_[m0, m1, m2]]
            neu_e += [eltern[zeilen]] * 4
        t, eltern = np.concatenate(neu_t), np.concatenate(neu_e)
    return v, t, attr, eltern, runden


# ----------------------------------------------------------------------------- Hügel (Spieltest T4)
def _abstand_xz(punkte: np.ndarray, ziele: np.ndarray, block: int = 256) -> np.ndarray:
    """Kleinster Abstand (x/z) jedes Punkts zu den Zielpunkten, blockweise (ohne SciPy)."""
    p = punkte[:, (0, 2)].astype(np.float32)
    q = ziele[:, (0, 2)].astype(np.float32)
    aus = np.empty(len(p), np.float32)
    for a in range(0, len(p), block):
        d = p[a:a + block, None, :] - q[None, :, :]
        aus[a:a + block] = np.sqrt(np.min(np.einsum('ijk,ijk->ij', d, d), axis=1))
    return aus.astype(np.float64)


def _glatt(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def huegel_anwenden(gp: np.ndarray, strecke: np.ndarray, loch: dict, hoehen, *, abstand_strecke: float = 30.0,
                    abstand_rand: float = 40.0, radius_max: float = 200.0, radius_min: float = 60.0) -> dict:
    """Experimentell (Spieltest T4, docs/umgebung_terraforming.md): Hügel (+) und Senken (−) in die Grundfläche.

    Eintrag ``(h, x, z, r)`` setzt Ort und Radius fest (Spielkoordinaten). Für eine bloße Höhe wird ein Mittelpunkt gewählt, der möglichst weit von der Strecke und vom Lochrand entfernt ist
    (Raster 16 m, gierig, Hügel überlappen nicht); Form Kosinusglocke mit Radius ≤ ``radius_max``. Die Wirkung wird
    bis ``abstand_strecke`` (+40 m Übergang) an der Strecke und ``abstand_rand`` (+60 m) am Lochrand auf null
    geblendet, damit Strecke, Mindestabstand darunter und Anschluss an das Außenterrain unverändert bleiben.
    ``gp`` (Grundflächen-Vertices, Spielkoordinaten) wird verändert. Rückgabe: Bericht."""
    x0, z0, kante = loch['x0'], loch['z0'], loch['kante']
    ziele = np.unique(np.round(strecke[:, (0, 2)] / 8.0), axis=0) * 8.0
    ziele = np.c_[ziele[:, 0], np.zeros(len(ziele)), ziele[:, 1]]

    def randabstand(p):
        return np.minimum.reduce([p[:, 0] - x0, x0 + kante - p[:, 0], p[:, 2] - z0, z0 + kante - p[:, 2]])

    gx = np.arange(x0 + 8.0, x0 + kante, 16.0)
    gz = np.arange(z0 + 8.0, z0 + kante, 16.0)
    kand = np.c_[np.repeat(gx, len(gz)), np.zeros(len(gx) * len(gz)), np.tile(gz, len(gx))]
    frei = np.minimum(_abstand_xz(kand, ziele) - abstand_strecke - 40.0, randabstand(kand) - abstand_rand - 60.0)
    d_s = _abstand_xz(gp, ziele)
    maske = _glatt((d_s - abstand_strecke) / 40.0) * _glatt((randabstand(gp) - abstand_rand) / 60.0)
    liste = []
    for eintrag in hoehen:
        if isinstance(eintrag, (tuple, list)):          # (h, x, z, r): fester Ort in Spielkoordinaten
            h, cx, cz, r = (float(v) for v in eintrag)
            c = np.array([cx, 0.0, cz])
        else:
            h = float(eintrag)
            i = int(np.argmax(frei))
            r = float(min(radius_max, frei[i]))
            if r < radius_min:
                break
            c = kand[i]
        d = np.hypot(gp[:, 0] - c[0], gp[:, 2] - c[2])
        innen = d < r
        gp[innen, 1] += float(h) * 0.5 * (1.0 + np.cos(np.pi * d[innen] / r)) * maske[innen]
        frei = np.minimum(frei, np.hypot(kand[:, 0] - c[0], kand[:, 2] - c[2]) - r - 40.0)
        liste.append({'x': round(float(c[0]), 1), 'z': round(float(c[2]), 1), 'radius_m': round(r, 1),
                      'hoehe_m': float(h), 'vertices': int(innen.sum())})
    return {'huegel': liste, 'nicht_platziert': len(hoehen) - len(liste)}


# ----------------------------------------------------------------------------- Hauptfunktion
#: Fassung der Portimão-Mod, mit der alles gebaut und im Spiel getestet ist (overtake.gg, Reiter „History“)
GETESTETE_MOD_VERSION = '1.0.0 und 1.0.2'


def traeger_pruefen(archiv: erp.ErpFile, manifest: Optional[dict] = None) -> None:
    """Wirft ``GeometrieFehler``, wenn die Fahrbahn der Portimão-Mod fehlt. Die Version 1.0.2 der Mod (23.10.2026,
    „new conversion script“) benennt ihre Ressourcen anders (``physik_kennung``); getestet sind ``GETESTETE_MOD_VERSION``."""
    fehlt = [kennung for kennung in (TERRAIN, physik_kennung(archiv)) if archiv.find(kennung) is None]
    if fehlt:
        version = str((manifest or {}).get('version') or '?')
        raise GeometrieFehler('Carrier does not fit: the Portimão mod (version %s) has no %s. Tested versions: %s (mod '
                              'download page, History tab).'
                              % (version, fehlt[0], GETESTETE_MOD_VERSION))


def geometrie_einsetzen(archiv: erp.ErpFile, netz_npz: str, **optionen) -> dict:
    """Ersetzt im geöffneten Träger-Archiv (Portimão) die sichtbare Fahrbahn und die Fahrphysik durch
    die generierte Strecke (Koordinaten über strecke/platzierung.py). Gibt einen Bericht (dict) zurück.

    Optionen:
        definition (dict): aufgelöste Generatordefinition; Standard: ``definition_aufgeloest.json``
            neben ``netz_npz`` (Texturlängen, Linien-/Randsteinbreite, Barrierenhöhe).
        grundflaeche (str|bool, 'raster'): Rasen-Grundfläche über dem Loch des Außenterrains.
            'raster' (= True, bisheriges 8-m-Raster unter der Strecke, bitgleich zu früheren Bauten),
            'adaptiv' (Schritt E, ``strecke/gelaende.py``: eingeschränkte Delaunay-Triangulierung, schließt
            genau an den äußersten Streckenrändern an, fein an der Strecke, grob in der Ferne),
            'keine' (= False).
        gelaende (dict): Parameter für 'adaptiv' (h_min, h_max, rand_h, abstufung, neigung_warnung).
        huegel (list[float], None): experimentell, Hügel/Senken mit diesen Höhen in die Grundfläche
            (``huegel_anwenden``; sichtbar und Fahrphysik).
        welt_uv (str, 'konstant'): Strom 4.zw der Terrain-Vertices: 'konstant' (ein Vorlagentexel je Material,
            keine Portimão-Geister) oder 'echt' (Weltprojektion über das Loch, für eigene Weltkarten;
            docs/welt_texturen.md).
        zelle (float, 8.0): Rasterweite der Grundfläche (teilt 2048 m; Randvertices alle 16 m).
        abstand_unter_strecke (float, 0.3), mindestabstand (float, 0.15): Grundfläche unter der Strecke.
        mauer_physik (str, 'senkrecht'): 'senkrecht' (wie Original nur Wandflächen), 'alle', 'keine'.
        max_dreiecke_kachel (int, 512): TRN-Kachelgröße (ego.trn_erzeugen).
        trn_flags (str, 'kanten'): Dreiecksflags der Fahrphysik; 'kanten' = ego.trn_erzeugen.kantenflags
            (Bit gelöscht an freien Rändern wie im Original), 'alle7' = alle Bits gesetzt (bis v13).
        vorlagen (dict): vorberechnete ``vorlagen_lesen``-Ergebnisse (sonst aus dem Archiv).
    Diagnose-Optionen der Fahrphysik (AP15, Standard = bisheriges Verhalten):
        hoehe_versatz (float, 0.0): Geometrie (sichtbar, Physik, Grundfläche) um diesen Wert in y
            verschieben, Streckenlogik bleibt. −0,2 ergibt Portimãos Abstand Logik ↔ Physik
            (TrackSpace/KI-Gates/TS 0,20 m über der Physikfläche).
        physik_umfang (str, 'voll'): 'voll' (Strecke + Grundfläche), 'ohne_grund' (Grundfläche nur
            sichtbar), 'fahrbahn' (nur Asphalt, Boxengasse, Linien, Randsteine; ohne Gras, Kies,
            Mauern, Grundfläche).
        physik_kante_max (float, None): Streckendreiecke der Physik (ohne Grundfläche) konform
            unterteilen, bis jede Kante ≤ diesem Wert ist (Portimão SDT+: Median 2,1 m, 99 % 5,9 m).
    Das Außenterrain bleibt unverändert (Begründung im Modulkopf).
    """
    zelle = float(optionen.get('zelle', 8.0))
    grund_art = optionen.get('grundflaeche', True)
    if grund_art is True or grund_art is None or grund_art is False:
        grund_art = 'raster' if grund_art is True else 'keine'
    if grund_art not in ('raster', 'adaptiv', 'keine'):
        raise ValueError('grundflaeche: raster, adaptiv or keine')
    mit_grund = grund_art != 'keine'
    mauer_physik = optionen.get('mauer_physik', 'senkrecht')
    if mauer_physik not in ('senkrecht', 'alle', 'keine'):
        raise ValueError('mauer_physik: senkrecht, alle or keine')
    hoehe_versatz = float(optionen.get('hoehe_versatz') or 0.0)
    physik_umfang = optionen.get('physik_umfang') or 'voll'
    if physik_umfang not in ('voll', 'ohne_grund', 'fahrbahn'):
        raise ValueError('physik_umfang: voll, ohne_grund or fahrbahn')
    physik_kante_max = optionen.get('physik_kante_max')
    physik_kante_max = float(physik_kante_max) if physik_kante_max else None
    traeger_pruefen(archiv)
    terrain_res = archiv.find(TERRAIN)
    physik_res = archiv.find(physik_kennung(archiv))
    if len(physik_res.fragments) != 1:
        raise GeometrieFehler('TRN~ must have exactly one fragment')
    netz = netz_laden(netz_npz, optionen.get('definition'))
    namen = netz['namen']
    fehlend = [k for k in KLASSEN if k not in namen and k not in OPTIONAL]
    if fehlend:
        raise GeometrieFehler('Generator mesh without classes: %s' % fehlend)
    pos = np.array(platzierung.punkte(netz['vertices']), dtype=np.float64)
    if hoehe_versatz:
        pos[:, 1] += hoehe_versatz
    t = g.Terrain.aus_bytes(terrain_res.data())
    materialien = sorted({m for m, _ in KLASSEN.values()} | {GRUND[0]})
    codes = sorted({c for _, c in KLASSEN.values()} | {GRUND[1]})
    vorlagen = optionen.get('vorlagen') or vorlagen_lesen(archiv, materialien, codes)
    loch = loch_lesen(archiv)
    bericht = dict(platzierung=dict(versatz=list(platzierung.VERSATZ)),
                   vorlagen={k: v for k, v in vorlagen.items()},
                   loch=dict(x0=loch['x0'], z0=loch['z0'], kante=loch['kante'],
                             randvertices=int(len(loch['rand'])), ersatz=loch['ersatz']))
    if (pos[:, 0].min() <= loch['x0'] or pos[:, 0].max() >= loch['x0'] + loch['kante']
            or pos[:, 2].min() <= loch['z0'] or pos[:, 2].max() >= loch['z0'] + loch['kante']):
        raise GeometrieFehler('Placed track does not lie completely inside the hole of the outer terrain')
    # --- Render
    welt_uv = optionen.get('welt_uv') or 'konstant'
    if welt_uv not in ('konstant', 'echt'):
        raise ValueError('welt_uv: konstant or echt')
    if welt_uv == 'echt':
        bereich = welt_uv_bereich(loch, pos)
        vorlagen = dict(vorlagen, _welt_uv_loch=bereich)
        bericht['welt_uv'] = 'echt'
        bericht['welt_uv_bereich'] = [round(v, 3) for v in bereich]
    netze, info = render_netze(netz, pos, t, vorlagen)
    tri, o = netz['dreiecke'], netz['oberflaeche']
    k = {n: i for i, n in enumerate(namen)}
    T = pos[tri]
    fn = _flaechennormalen(pos, tri)
    ny = fn[:, 1] / np.maximum(np.linalg.norm(fn, axis=1), 1e-12)
    ist_mauer = o == k['mauer']
    markierung = np.isin(o, [k[n] for n in NUR_SICHTBAR if n in k])   # Startboxen liegen über dem Asphalt
    # Gebäude stehen innerhalb der Strecke; Dach und Wände gehören nicht zum Rand der Grundfläche
    markierung |= (o == k['gebaeude']) if 'gebaeude' in k else False
    T_boden = T[~ist_mauer & ~markierung]
    T_mauer_oben = T[ist_mauer & (ny > 0.5)]
    grund = None
    if grund_art == 'adaptiv':
        try:
            grund = gelaende.adaptive_grundflaeche(pos, tri[~markierung], loch, **(optionen.get('gelaende') or {}))
        except gelaende.GelaendeFehler as exc:
            raise GeometrieFehler('Adaptive ground surface: %s (way out: grundflaeche=raster)' % exc) from exc
    elif mit_grund:
        grund = grundflaeche(T_boden, T_mauer_oben, loch, zelle=zelle,
                             abstand_unter_strecke=float(optionen.get('abstand_unter_strecke', 0.3)),
                             mindestabstand=float(optionen.get('mindestabstand', 0.15)),
                             barrierenhoehe=netz['barrierenhoehe'])
    if grund is not None and optionen.get('huegel'):
        nutz = np.unique(tri[~markierung])
        info_huegel = huegel_anwenden(grund['positionen'], pos[nutz], loch, list(optionen['huegel']))
        bericht['huegel'] = info_huegel
    if grund is not None:
        gp = grund['positionen']
        ids, lokal = _teilnetz(grund['render'])
        welt = _welt_uv(vorlagen, GRUND[0], gp[ids])
        uv0 = gp[ids][:, (0, 2)] / PERIODE_FLAECHE
        netze.append(gt.TerrainNetz(
            _mat_index(t, GRUND[0]), gp[ids], lokal, uv0,
            farben=np.tile(vorlagen['farben'][GRUND[0]], (len(ids), 1)).astype(np.float64),
            uv_welt=welt, uv_zusatz=np.c_[uv0, welt], normale_w=np.zeros(len(ids))))
        info['grundflaeche'] = dict(material=GRUND[0], dreiecke=int(len(lokal)), vertices=int(len(ids)))
    kontrolle = gt.terrain_erzeugen(archiv, TERRAIN, netze)
    # --- Physik
    p_teile, t_teile, c_teile, a_teile, g_teile = [], [], [], [], []
    basis = 0

    def physik_anfuegen(sel_tri, p, code, ist_grund=False):
        nonlocal basis
        if not len(sel_tri):
            return
        ids, lokal = _teilnetz(sel_tri)
        p_teile.append(p[ids]); t_teile.append(lokal + basis)
        c_teile.extend([code] * len(lokal))
        g_teile.extend([ist_grund] * len(lokal))
        a_teile.append(np.full(len(ids), vorlagen['trn_attribut'][code], dtype=np.uint32))
        basis += len(ids)

    trn_zahlen = {}
    for klasse, (_, code) in KLASSEN.items():
        if physik_umfang == 'fahrbahn' and klasse not in FAHRBAHN_KLASSEN:
            continue
        if klasse not in k or klasse in NUR_SICHTBAR:   # ältere Generatorausgabe bzw. nur Markierung
            continue
        sel = o == k[klasse]
        if klasse == 'mauer':
            if mauer_physik == 'keine':
                continue
            if mauer_physik == 'senkrecht':
                sel &= np.abs(ny) <= 0.5
        physik_anfuegen(tri[sel], pos, code)
        trn_zahlen[klasse] = int(sel.sum())
    if grund is not None and physik_umfang == 'voll':
        physik_anfuegen(grund['physik'], grund['positionen'], GRUND[1], ist_grund=True)
        trn_zahlen['grundflaeche'] = int(len(grund['physik']))
    pv = np.concatenate(p_teile)
    pt = np.concatenate(t_teile)
    pa = np.concatenate(a_teile)
    # Verschweißen: gleiche Positionen (1 mm) werden zu einem Vertex. Spieltest 15.10.2026: Mit losen
    # Dreiecken (108.300 doppelte Vertices in Kacheln; Portimão: 0) fiel das Auto durch den Boden bzw.
    # das Spiel stürzte kurz nach Fahrtbeginn ab.
    schluessel = np.round(pv / 0.001).astype(np.int64)
    _, erst, inv = np.unique(schluessel, axis=0, return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    # Reihenfolge des ersten Vorkommens erhalten (np.unique sortiert nach Position; das zerstört die
    # Index-Nähe, die das VCQTC-Dreiecksformat braucht, und erzwingt neue Duplikate im Erzeuger)
    reihenfolge = np.argsort(erst, kind="stable")
    rang = np.empty_like(reihenfolge)
    rang[reihenfolge] = np.arange(len(reihenfolge))
    pv, pa, pt = pv[erst[reihenfolge]], pa[erst[reihenfolge]], rang[inv][pt]
    gut = (pt[:, 0] != pt[:, 1]) & (pt[:, 1] != pt[:, 2]) & (pt[:, 0] != pt[:, 2])
    pt = pt[gut]
    c_teile = [c for c, g in zip(c_teile, gut) if g]
    ist_grund = np.asarray(g_teile, bool)[gut]
    trn_zahlen['verschweisst'] = {'vertices': int(len(pv)), 'entartet_entfernt': int((~gut).sum())}
    if physik_kante_max:
        # AP15: feine Triangulierung der Streckenflächen (Grundfläche bleibt im 8-m-Raster)
        vorher = len(pt)
        pv, pt, pa, eltern, runden = physik_unterteilen(pv, pt, pa, physik_kante_max, auswahl=~ist_grund)
        c_teile = [c_teile[i] for i in eltern.tolist()]
        ist_grund = ist_grund[eltern]
        trn_zahlen['unterteilt'] = {'kante_max': physik_kante_max, 'runden': int(runden),
                                    'dreiecke_vorher': int(vorher), 'dreiecke_nachher': int(len(pt)),
                                    'vertices_nachher': int(len(pv))}
    # Kantenflags (AP4c): Bit e = Kante (v_e, v_e+1) hat eine Nachbarfläche; an freien Rändern gelöscht
    # wie in den Originalstrecken. 'alle7' = bisheriges Verhalten (Diagnose).
    trn_flags = optionen.get('trn_flags', 'kanten')
    if trn_flags == 'kanten':
        flags = kantenflags(pv, pt)
    elif trn_flags == 'alle7':
        flags = np.full(len(pt), 7, dtype=np.uint8)
    else:
        raise ValueError('trn_flags: kanten or alle7')
    trn_statistik = {}
    roh = trn_erzeugen(pv, pt, c_teile, vertex_attribute=pa, flags=flags,
                       max_dreiecke=int(optionen.get('max_dreiecke_kachel', 512)), statistik=trn_statistik)
    physik_res.set_fragment_data(0, roh)
    kacheln = trn.open_trn(roh).geometry()
    codes_neu, anzahl = np.unique(np.array(c_teile), return_counts=True)
    # --- Bericht
    boden_y = T_boden[:, :, 1]
    klassen_info = {kk: vv for kk, vv in info.items() if not kk.startswith('_')}
    bericht['render'] = dict(klassen=klassen_info, uv=info.get('_uv'),
                             terrain=dict(knoten=kontrolle['knoten'], zeichnungen=kontrolle['zeichnungen'],
                                          dreiecke=kontrolle['dreiecke'], vertices=kontrolle['vertices'],
                                          raster=dict(minimum=kontrolle['raster'].minimum,
                                                      maximum=kontrolle['raster'].maximum,
                                                      anzahl=kontrolle['raster'].anzahl),
                                          vorbelegungen=len(kontrolle['vorbelegungen']),
                                          ressourcen=kontrolle['ressourcen']))
    bericht['physik'] = dict(kennung=physik_res.identifier, bytes=len(roh), kacheln=len(kacheln), dreiecke=int(len(pt)),
                             vertices=int(len(pv)), dreiecke_je_klasse=trn_zahlen,
                             dreiecke_je_code=dict(zip(codes_neu.tolist(), anzahl.tolist())),
                             mauer_physik=mauer_physik, version='0x20002', erzeuger=trn_statistik,
                             umfang=physik_umfang, kante_max=physik_kante_max, hoehe_versatz=hoehe_versatz,
                             flags=dict(art=trn_flags, werte=dict(zip(*[x.tolist() for x in
                                                                        np.unique(flags, return_counts=True)]))))
    bericht['hoehen'] = dict(strecke_boden=(float(boden_y.min()), float(boden_y.max())),
                             strecke_gesamt=(float(pos[:, 1].min()), float(pos[:, 1].max())),
                             physik=(float(pv[:, 1].min()), float(pv[:, 1].max())))
    bericht['lage'] = dict(strecke_x=(float(pos[:, 0].min()), float(pos[:, 0].max())),
                           strecke_z=(float(pos[:, 2].min()), float(pos[:, 2].max())))
    if grund is not None:
        bericht['grundflaeche'] = {kk: vv for kk, vv in grund.items()
                                   if kk not in ('positionen', 'render', 'physik')}
        bericht['grundflaeche'].update(render_dreiecke=int(len(grund['render'])),
                                       physik_dreiecke=int(len(grund['physik'])),
                                       verfahren=grund_art)
    bericht['aussen'] = dict(kennung=AUSSEN, geaendert=False,
                             grund='Strecke liegt vollständig im 2048-m-Loch; Grundfläche schließt an den Innenrand an')
    bericht['geaenderte_ressourcen'] = sorted(set(kontrolle['ressourcen']) | {physik_res.identifier})
    return _json(bericht)
