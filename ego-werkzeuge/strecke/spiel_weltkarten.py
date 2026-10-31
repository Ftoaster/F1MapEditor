#!/usr/bin/env python3
"""Weltkarten: Boden, Ideallinie und Außenterrain der neuen Strecke statt Portimãos Luftbildern (docs/welt_texturen.md).

Erprobt in den Spieltests W1–W9 (29./30.10.2026, ``ego-werkzeuge/ergebnisse/welt_texturen_testpakete.md``).

    innenkarten          Blend-, Makro- und Grashöhenkarte aus den Oberflächenklassen des Generatornetzes
    trackinfo_bild       TrackInfo-Karte aus der Ideallinie (sonst liegt Portimãos Linie auf der Fahrbahn)
    aussenkarten         beide Karten des Außenterrains aus Höhe, Neigung und Rauschen
    biom_wueste          Schichttexturen der Grasmaterialien gegen Bahrains Wüste (Bodenbewuchs aus)
    farbton_setzen       Farbton der Grasschichten
    weltkarten_einsetzen alles zusammen für ``neue_strecke_tool.py bauen --weltkarten`` (braucht ``--welt-uv echt``)
"""

import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from ego import erp, grafik, grafik_terrain, grafik_textur_schreiben as gts
from . import platzierung, spiel_geometrie, umgebung

X0, Z0, KANTE = umgebung.LOCH
N = 1024
GRAS_SHADER = "terrain_4way_blend_grass.nefx2"
BIOMES = "eaid://environment_package/core_assets/shared_textures/terrain/textures/biomes/"
#: Schicht → Bahrain-Textur (Basisname ohne _dh/_n/_s), Spieltest W5
BIOM_WUESTE = {1: "core_desert_rock_a_01", 2: "core_desert_sand_a_02", 3: "core_desert_sand_a_02",
               4: "core_desert_conrete_a_02"}
BIOME = ("standard", "wueste")
#: Faktoren auf ``me_layerN_tint`` und die Außenfarben (Mechanik Spieltest W4)
FARBTOENE = {"standard": (1.0, 1.0, 1.0), "trocken": (1.15, 1.05, 0.75), "saftig": (0.85, 1.1, 0.85),
             "herbst": (1.25, 0.85, 0.55)}

# ------------------------------------------------------------------ Außenterrain
#: Außenterrain-Netze: Material → (Karte, Alpha wie im Original). Beide Karten liegen senkrecht von oben auf dem Netz
#: (uv = Lage im Netzquadrat), Zeile = v · Höhe wie die Innenkarten.
AUSSEN_NETZE = {"por_terrain_b.material": ("por_terrain_a_02_m", 23), "por_terrain_c.material": ("por_terrain_a_03_m", 255)}
AUSSEN_N = 2048
#: Farben der Karte. W8 lag beim Mittel der Originalkarten (≈ 82/88/62) und wirkte im Spiel viel dunkler als das Gras
#: der Strecke, weil Luftbilder ihre Helligkeit aus dem Kontrast ziehen; deshalb heller und grüner.
AUSSEN_GRAS = np.array([126.0, 142.0, 70.0])
AUSSEN_TROCKEN = np.array([142.0, 142.0, 84.0])
AUSSEN_WALD = np.array([92.0, 114.0, 58.0])
AUSSEN_FELS = np.array([136.0, 126.0, 104.0])
#: (Gras, Trocken, Wald, Fels) je Biom
AUSSEN_FARBEN = {"standard": (AUSSEN_GRAS, AUSSEN_TROCKEN, AUSSEN_WALD, AUSSEN_FELS),
                 "wueste": (np.array([176.0, 150.0, 106.0]), np.array([188.0, 162.0, 118.0]),
                            np.array([150.0, 124.0, 88.0]), np.array([138.0, 112.0, 88.0]))}
NEUTRALE_NORMALE = "eaid://environment_package/core_assets/shared_textures/default/textures/blank_normal_a_01_n.tif"


def textur_kennung(a, name):
    """Kennung der ``….tif.image``-Ressource, deren Dateiname ``name`` ist (Pfad nach Umbenennung egal)."""
    treffer = [r.identifier for r in a if r.resource_type == "GfxSurfaceRes" and r.identifier.endswith("/" + name + ".tif.image")]
    if len(treffer) > 1:
        # Mod 1.0.2 legt für die Sponsorenfläche Kopien unter stageN/<hash>/ an; die Bodenkarte liegt in terrain/textures/
        treffer = [k for k in treffer if "/terrain/textures/" in k] or treffer
    if len(treffer) != 1:
        raise KeyError("Textur %s: %d Treffer" % (name, len(treffer)))
    return treffer[0]


def material_textur_kennung(a, material, slot="me_macroMap"):
    """Kennung der ``….tif.image``-Ressource, die das Material ``material`` (Dateiname) im ``slot`` nutzt. Mod 1.0.2 nennt die
    Außenterrain-Karten ``stage24_<hash>.tif`` statt ``por_terrain_a_02_m.tif``; das Material kennt den echten Namen."""
    treffer = [x for x in a if x.resource_type == "GfxMaterialRes" and x.identifier.endswith("/" + material)]
    if len(treffer) != 1:
        raise KeyError("Material %s: %d Treffer" % (material, len(treffer)))
    textur = grafik.Material.aus_bytes(treffer[0].data()).texturen().get(slot)
    if not textur or a.find(textur + ".image") is None:
        raise KeyError("Material %s: Textur im Slot %s fehlt (%s)" % (material, slot, textur))
    return textur + ".image"


def textur_kopien(a, kennung):
    """Weitere ``….tif.image``-Ressourcen mit denselben Pixeldaten wie ``kennung``. Mod 1.0.2 (neues Konvertierungsskript) legt
    Kopien mit Hash-Namen an (``stage24_<hash>.tif``), die 28 Materialien (Asphalt, Beton, Linien, Gitter …) statt der
    Standardkarte benutzen; die Beschreibungsblöcke unterscheiden sich, die Pixel nicht."""
    res = a.find(kennung)
    if res is None or len(res.fragments) < 2:
        return []
    groesse, pixel = res.fragments[1].size, None
    kopien = []
    for x in a:
        if (x.resource_type != "GfxSurfaceRes" or x.identifier == kennung or len(x.fragments) < 2
                or x.fragments[1].size != groesse):
            continue
        if pixel is None:
            pixel = res.fragments[1].read()
        if x.fragments[1].read() == pixel:
            kopien.append(x.identifier)
    return kopien


def karte_schreiben(a, name, rgba, arbeit, tag, kennung=None):
    if arbeit:
        Image.fromarray(rgba, "RGBA").save(os.path.join(arbeit, "%s_%s.png" % (tag, name)))
    k = kennung or textur_kennung(a, name)
    kopien = textur_kopien(a, k)               # vor dem Ersetzen: danach gleichen die Pixel nicht mehr
    erg = {"textur": k, **{kk: v for kk, v in gts.textur_ersetzen(a, k, rgba, format=69, mips="voll").items()
                           if isinstance(v, (int, float, str))}}
    for kopie in kopien:
        gts.textur_ersetzen(a, kopie, rgba, format=69, mips="voll")
    if kopien:
        erg["kopien"] = len(kopien)
    return erg


def grasmaterialien(a):
    erg = []
    for r in a:
        if r.resource_type == "GfxMaterialRes" and "/terrain/" in r.identifier:
            m = grafik.Material.aus_bytes(r.data())
            if m.shader.endswith(GRAS_SHADER):
                erg.append((r, m))
    return erg


def _rauschen(saat, zellen, staerke=1.0):
    """Glattes Rauschen 0…1 in N×N (Wertrauschen, bikubisch hochskaliert)."""
    rng = np.random.default_rng(saat)
    klein = (rng.random((zellen, zellen)) * 255).astype(np.uint8)
    gross = np.asarray(Image.fromarray(klein, "L").resize((N, N), Image.BICUBIC), np.float64) / 255.0
    return np.clip(0.5 + (gross - 0.5) * staerke, 0, 1)


def _abstand(maske, schritte):
    """Abstand in Texeln zur Maske (Schachbrett-Näherung), begrenzt auf ``schritte``."""
    d = np.full(maske.shape, float(schritte), np.float64)
    aktuell = maske.copy()
    d[aktuell] = 0
    for k in range(1, schritte + 1):
        n = aktuell.copy()
        n[1:] |= aktuell[:-1]; n[:-1] |= aktuell[1:]; n[:, 1:] |= aktuell[:, :-1]; n[:, :-1] |= aktuell[:, 1:]
        d[n & ~aktuell] = k
        aktuell = n
    return d


def welt_bereich(npz):
    """Quadrat der echten Welt-UV wie im Bau (``spiel_geometrie.welt_uv_bereich``: Loch ∪ Strecke + 50 m)."""
    pos = platzierung.punkte(np.load(npz)["vertices"])
    return spiel_geometrie.welt_uv_bereich({"x0": X0, "z0": Z0, "kante": KANTE}, pos)


def klassenraster(npz, bereich):
    """Texelraster der Generatorklassen (Namen → bool-Maske) im Quadrat ``bereich`` (x0, z0, Kante)."""
    d = np.load(npz)
    namen = [str(x) for x in d["oberflaechen_namen"]]
    pos = platzierung.punkte(d["vertices"])
    bx, bz, bk = bereich
    u = (pos[:, 0] - bx) / bk * N
    v = (pos[:, 2] - bz) / bk * N
    tri, o = d["dreiecke"].astype(np.int64), d["oberflaeche"].astype(np.int64)
    masken = {}
    for k, name in enumerate(namen):
        bild = Image.new("1", (N, N), 0)
        zeichner = ImageDraw.Draw(bild)
        for t in tri[o == k]:
            zeichner.polygon([(u[i], v[i]) for i in t], fill=1, outline=1)
        masken[name] = np.asarray(bild, bool)
    return masken


def innenkarten(a, arbeit, npz, tag="bau", flecken_bis_m=None, bewuchs=True):
    """``flecken_bis_m``: Erdflecken nur bis zu diesem Abstand von harten Flächen (Spielbild W6: auf Hügeln hart begrenzt
    und mit sichtbar kachelnder Erdtextur); None = überall. ``bewuchs=False``: Grashöhe 0, kein Bodenbewuchs (Wüste).
    ``arbeit``: Ordner für PNG-Kopien der Karten oder None."""
    bereich = welt_bereich(npz)
    m_je_texel = bereich[2] / N
    masken = klassenraster(npz, bereich)
    fahrbahn = np.zeros((N, N), bool)
    for k in ("asphalt", "randstein", "boxengasse", "linie", "auslauf", "startbox", "gebaeude"):
        fahrbahn |= masken.get(k, False)
    kies = masken.get("kies", np.zeros((N, N), bool))
    mauer = masken.get("mauer", np.zeros((N, N), bool))
    hart = fahrbahn | kies | mauer
    d_hart = _abstand(hart, int(330 / m_je_texel) + 1) * m_je_texel     # Meter, bis ≈ 330 m
    # Blendkarte: R Erde (Streifen 2–8 m neben harten Flächen, ausgefranst, dazu Flecken), G/B/A Grasarten
    n1, n2, n3, n4 = _rauschen(1, 48, 1.6), _rauschen(2, 24, 1.8), _rauschen(3, 12, 1.8), _rauschen(4, 96, 1.4)
    erde = np.clip(1.0 - (d_hart - 2.0) / (6.0 + 6.0 * n4), 0, 1) * (d_hart > 0)
    flecken = np.clip((n1 - 0.72) * 5, 0, 1)
    if flecken_bis_m is not None:
        flecken = flecken * np.clip((flecken_bis_m - d_hart) / (flecken_bis_m / 2.0), 0, 1)
    erde = np.maximum(erde, flecken)
    g2, g3, g4 = n2, 1.0 - n2, n3
    blend = np.stack([erde, g2 * (1 - erde), g3 * (1 - erde), g4 * (1 - erde)], axis=2)
    blend = (np.clip(blend, 0, 1) * 255).astype(np.uint8)
    # Makrokarte: Overlay um Grau 128 (W1: färbt auch den Asphalt). Werte wie der bisherige Bau mit konstanter Welt-UV
    # (bericht.json → geometrie.vorlagen.uv_welt_makrofarbe): Fahrbahn 128/128/127, Kies 141, Mauer 132, Gras 128/128/98;
    # nur das Gras wird großflächig variiert (±12 %), Erdstreifen leicht bräunlich.
    gras_rgb = np.array([128.0, 128.0, 98.0])
    makro = gras_rgb[None, None, :] * (0.88 + 0.24 * _rauschen(5, 16, 1.5))[..., None]
    makro = makro * (1.0 - 0.2 * erde[..., None]) + np.array([140.0, 124.0, 100.0]) * 0.2 * erde[..., None]
    makro[fahrbahn] = (128, 128, 127)
    makro[kies] = (141, 141, 141)
    makro[mauer] = (132, 131, 131)
    # Alpha = Maske „kahler/trockener Boden“ (Portimão: Median 1 unter Asphalt, 4 im Gras; hell nur auf kahlen Flächen).
    # 255 überall machte im Spieltest W6 die ganze Fahrbahn staubig-braun. Hier: 0 auf harten Flächen, im Gras nur in
    # den Erdstreifen leicht erhöht.
    alpha = np.clip(erde * 90.0, 0, 255)
    alpha[hart] = 0
    makro_rgba = np.dstack([np.clip(makro, 0, 255).astype(np.uint8), alpha.astype(np.uint8)])
    # Grashöhe: 0 auf harten Flächen, weicher Übergang 3 m, im Gras 120–230; bis 320 m um die Strecke, außen 0
    hoehe = np.clip((d_hart - 1.0) / 3.0, 0, 1) * (120 + 110 * _rauschen(6, 64, 1.5))
    hoehe *= np.clip((320.0 - d_hart) / 60.0, 0, 1)
    gras = np.zeros((N, N, 4), np.uint8)
    if not bewuchs:
        hoehe = np.zeros_like(hoehe)
    gras[..., 0] = gras[..., 1] = np.clip(hoehe, 0, 255).astype(np.uint8)
    gras[..., 3] = 255
    return {"blend": karte_schreiben(a, "por_terrain_a_01_bm", blend, arbeit, tag),
            "makro": karte_schreiben(a, "por_terrain_a_01_m", makro_rgba, arbeit, tag),
            "grashoehe": karte_schreiben(a, "por_terrain_a_01_g", gras, arbeit, tag),
            "texel_fahrbahn": int(fahrbahn.sum()), "texel_kies": int(kies.sum()),
            "welt_uv_bereich": [round(v, 3) for v in bereich], "m_je_texel": round(m_je_texel, 3)}


def ideallinie_stationen(a, ideal):
    """Je Querschnitt der Fahrbahn: Zusatz-UV (Rundenfortschritt z, Querlage w) der Ideallinie und Fahrbahnbreite.

    Liest die Asphaltnetze aus dem gebauten Archiv, damit Fortschritt und Querlage genau die Werte sind, die der Shader
    bekommt. Rückgabe: Arrays (z, w, breite_m), nach z sortiert."""
    pos, zus = [], []
    for r in a:
        if r.resource_type != "TerrainRes":
            continue
        t = grafik.Terrain.aus_bytes(r.data())
        for n in grafik_terrain.terrain_lesen(a, r.identifier):
            if t.materialien[n.material].endswith("/por_track_a.material"):
                pos.append(n.positionen)
                zus.append(n.uv_zusatz)
    if not pos:
        raise KeyError("kein Asphaltnetz por_track_a")
    pos, zus = np.concatenate(pos), np.concatenate(zus)
    pz = np.asarray(ideal["position"], float)[:, (0, 2)]
    pr = np.asarray(ideal["progress"], float)
    z_all = np.round(zus[:, 2].astype(np.float64), 6)
    erg = []
    for z in np.unique(z_all):
        g = np.flatnonzero(z_all == z)
        if len(g) < 2:
            continue
        i0, i1 = g[np.argmin(zus[g, 3])], g[np.argmax(zus[g, 3])]
        p0, p1 = pos[i0][[0, 2]], pos[i1][[0, 2]]
        w0, w1 = float(zus[i0, 3]), float(zus[i1, 3])
        e = p1 - p0
        l2 = float(e @ e)
        if l2 < 1.0 or w1 - w0 < 0.5:
            continue
        nah = np.abs((pr - z + 0.5) % 1.0 - 0.5) < 0.03
        q = pz[nah] - p0
        f = np.clip(q @ e / l2, 0, 1)
        k = int(np.argmin(np.linalg.norm(q - f[:, None] * e, axis=1)))
        erg.append((z, w0 + float(f[k]) * (w1 - w0), np.sqrt(l2) / (w1 - w0)))
    return tuple(np.array(erg).T)


def trackinfo_bild(z, w, breite_m, hoehe=64, spalten=16384):
    """TrackInfo-Karte: Spalte = Rundenfortschritt, Zeile = Querlage (v · Höhe, am Portimão-Original nachgemessen).

    R/A = Ideallinie wie im Original: Trapez, 2,2 m voll, 6 m gesamt (Portimão bei ≈ 13,5 m Fahrbahn: 10 bzw. 28 von
    64 Zeilen). G fast konstant, B null. Portimãos zwei dunkle Querstriche in G entfallen."""
    c = (np.arange(spalten) + 0.5) / spalten
    wi = np.interp(c, z, w, period=1.0)
    bi = np.interp(c, z, breite_m, period=1.0)
    zeilen = ((np.arange(hoehe) + 0.5) / hoehe)[:, None]
    abstand = np.abs(zeilen - wi[None, :]) * bi[None, :]
    linie = np.clip((3.0 - abstand) / (3.0 - 1.1), 0, 1)
    bild = np.zeros((hoehe, spalten, 4), np.uint8)
    bild[..., 0] = np.round(linie * 255).astype(np.uint8)
    bild[..., 1] = 241
    bild[0, :, 1], bild[-1, :, 1] = 230, 233
    bild[..., 3] = np.round(105 + 150 * linie).astype(np.uint8)
    return bild


def hoehenraster(pos, tri, x0, z0, kante, n):
    """Höhe je Texel (n×n) aus einem Dreiecksnetz, Texelmitte, ohne Netz NaN."""
    h = np.full((n, n), np.nan)
    px = (pos[:, 0] - x0) / kante * n - 0.5
    pz = (pos[:, 2] - z0) / kante * n - 0.5
    for t in tri:
        xs, zs, ys = px[t], pz[t], pos[t, 1]
        i0, i1 = max(int(np.floor(xs.min())), 0), min(int(np.ceil(xs.max())), n - 1)
        j0, j1 = max(int(np.floor(zs.min())), 0), min(int(np.ceil(zs.max())), n - 1)
        d = (zs[1] - zs[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (zs[0] - zs[2])
        if i1 < i0 or j1 < j0 or abs(d) < 1e-12:
            continue
        zz, xx = np.mgrid[j0:j1 + 1, i0:i1 + 1]
        l0 = ((zs[1] - zs[2]) * (xx - xs[2]) + (xs[2] - xs[1]) * (zz - zs[2])) / d
        l1 = ((zs[2] - zs[0]) * (xx - xs[2]) + (xs[0] - xs[2]) * (zz - zs[2])) / d
        l2 = 1.0 - l0 - l1
        m = (l0 >= -1e-9) & (l1 >= -1e-9) & (l2 >= -1e-9)
        h[zz[m], xx[m]] = (l0 * ys[0] + l1 * ys[1] + l2 * ys[2])[m]
    return h


def _glatt01(x):
    x = np.clip(x, 0, 1)
    return x * x * (3 - 2 * x)


def _welt_fraktal(saat, ausschnitt, n, zellen=(48, 96, 192, 384, 768, 1536, 3072)):
    """Fraktales Rauschen ≈ 0…1 aus einem festen Feld: ``ausschnitt`` (fx, fz, fk) in Anteilen des 24,6-km-Quadrats.
    Beide Karten lesen dasselbe Feld, am Übergang der Netze passen die Muster deshalb zusammen."""
    fx, fz, fk = ausschnitt
    rng = np.random.default_rng(saat)
    summe, norm = np.zeros((n, n)), 0.0
    for k, z in enumerate(zellen):
        klein = rng.random((z, z), np.float32)
        box = (fx * z, fz * z, (fx + fk) * z, (fz + fk) * z)
        o = np.asarray(Image.fromarray(klein, "F").resize((n, n), Image.BICUBIC, box=box), np.float64)
        w = 0.6 ** k
        summe += w * (o - 0.5) / 0.2
        norm += w * w
    return np.clip(0.5 + 0.25 * summe / np.sqrt(norm), 0, 1)


def _mischen(farbe, ziel, anteil):
    return farbe + (ziel - farbe) * anteil[..., None]


def aussen_farben(h, m_je_texel, ausschnitt, farben=None):
    """RGB-Karte des Außenterrains aus Höhe (ohne Netz NaN) und Rauschen: Gras, trockene Flächen und Wald nach Rauschen,
    Fels an steilen Hängen und in großer Höhe, am Lochrand reines Gras. Weiche, breite Übergänge: Schmale Schwellen
    ergaben im Vorschaubild ein Tarnmuster. Die Muster sind richtungslos und werden an Hängen deshalb nicht sichtbar
    gezogen."""
    gras_f, trocken_f, wald_f, fels_f = AUSSEN_FARBEN["standard"] if farben is None else farben
    n = h.shape[0]
    rand = np.isnan(h)
    roh = np.where(rand, np.nanmean(h), h)
    gz, gx = np.gradient(roh, m_je_texel)
    neigung = np.hypot(gx, gz)
    neigung[rand] = 0
    f1 = _welt_fraktal(101, ausschnitt, n)
    f2 = _welt_fraktal(102, ausschnitt, n)
    f3 = _welt_fraktal(103, ausschnitt, n)
    f4 = _welt_fraktal(104, ausschnitt, n)
    fein = _welt_fraktal(105, ausschnitt, n, zellen=(768, 1536, 3072))
    hoch = _glatt01((roh - 150.0) / 550.0)
    fels = 0.85 * _glatt01((neigung - 0.75) / 0.7) * _glatt01((f3 - 0.15) / 0.6)
    fels = np.maximum(fels, 0.6 * _glatt01((roh - 600.0) / 500.0) * _glatt01((f3 - 0.2) / 0.5))
    trocken = np.clip(0.6 * _glatt01((f2 - 0.38) / 0.42) + 0.3 * hoch, 0, 0.8)
    wald = 0.6 * _glatt01((f1 - 0.36) / 0.4) * (1.0 - 0.75 * hoch)
    farbe = np.ones((n, n, 3)) * gras_f
    farbe = _mischen(farbe, trocken_f, trocken)
    farbe = _mischen(farbe, wald_f, wald)
    farbe = _mischen(farbe, fels_f, fels)
    farbe = farbe * (0.9 + 0.2 * f4)[..., None] * (0.92 + 0.16 * fein)[..., None]
    if rand.any():
        schleier = Image.fromarray((rand * 255).astype(np.uint8), "L").filter(
            ImageFilter.GaussianBlur(max(3.0, 150.0 / m_je_texel)))
        am_rand = np.clip(np.asarray(schleier, np.float64) / 255.0 * 2.0, 0, 1)
        farbe = _mischen(farbe, gras_f * (0.94 + 0.12 * f4)[..., None], am_rand)
    return np.clip(farbe, 0, 255).astype(np.uint8)


def aussenkarten(a, arbeit, tag, farben=None):
    """Ersetzt beide Karten des Außenterrains, tauscht die Normalenkarte von ``por_terrain_c`` gegen die neutrale."""
    r = next(x for x in a if x.resource_type == "TerrainRes" and "outer_terrain" in x.identifier)
    t = grafik.Terrain.aus_bytes(r.data())
    netze = {}
    for n in grafik_terrain.terrain_lesen(a, r.identifier):
        netze[t.materialien[n.material].rsplit("/", 1)[-1]] = n
    voll = netze["por_terrain_c.material"].positionen
    vx0, vz0 = float(voll[:, 0].min()), float(voll[:, 2].min())
    vk = float(voll[:, 0].max()) - vx0
    erg = {}
    for material, (karte, alpha) in AUSSEN_NETZE.items():
        n = netze[material]
        x0, z0 = float(n.positionen[:, 0].min()), float(n.positionen[:, 2].min())
        k = float(n.positionen[:, 0].max()) - x0
        h = hoehenraster(n.positionen, n.dreiecke, x0, z0, k, 1024)
        fein = np.asarray(Image.fromarray(np.nan_to_num(h, nan=float(np.nanmean(h))).astype(np.float32), "F")
                          .resize((AUSSEN_N, AUSSEN_N), Image.BICUBIC), np.float64).copy()
        loch = np.asarray(Image.fromarray(np.isnan(h).astype(np.uint8) * 255, "L")
                          .resize((AUSSEN_N, AUSSEN_N), Image.NEAREST)) > 0
        fein[loch] = np.nan
        rgb = aussen_farben(fein, k / AUSSEN_N, ((x0 - vx0) / vk, (z0 - vz0) / vk, k / vk), farben)
        bild = np.dstack([rgb, np.full((AUSSEN_N, AUSSEN_N), alpha, np.uint8)])
        e = karte_schreiben(a, karte, bild, arbeit, tag, kennung=material_textur_kennung(a, material))
        e.update(netz_quadrat_m=round(k, 1), mittel_rgb=[round(float(v), 1) for v in rgb[~loch].mean(0)],
                 hoehe_m=[round(float(np.nanmin(fein)), 1), round(float(np.nanmax(fein)), 1)])
        erg[karte] = e
    m_res = next(x for x in a if x.resource_type == "GfxMaterialRes" and x.identifier.endswith("/por_terrain_c.material"))
    d = m_res.data()
    alt = grafik.Material.aus_bytes(d).texturen()["me_normalMap"]
    if a.find(NEUTRALE_NORMALE) is None:
        raise KeyError(NEUTRALE_NORMALE)
    m_res.set_fragment_data(0, grafik.material_aendern(d, texturen={alt: NEUTRALE_NORMALE}))
    erg["normale"] = {"alt": alt, "neu": NEUTRALE_NORMALE}
    return erg


def biom_wueste(a, code, spielordner=None):
    """Schichttexturen der Grasmaterialien gegen Bahrains Wüste (Spieltest W5). Rückgabe: (Bericht, Mip-Dateien)."""
    spiel = umgebung.spielordner_finden(spielordner)
    if spiel is None:
        raise umgebung.UmgebungFehler("Biom Wüste braucht den Spielordner (nur lesend)")
    quelle = os.path.join(spiel, "2025_asset_groups", "environment_package", "tracks", "bahrain", "wep", "bahrain_common.erp")
    start, tausch, geaendert = [], {}, []
    for i, basis in BIOM_WUESTE.items():
        for endung in ("dh", "n", "s"):
            start.append(BIOMES + "%s_%s.tif" % (basis, endung))
    with erp.open_erp(quelle) as q:
        idx = grafik.RessourcenIndex()
        idx.hinzufuegen(q)
        neu, mips, fehlt = umgebung.ressourcen_kopieren(a, idx, start, code, spiel, praefix="wt_")
    for r, m in grasmaterialien(a):
        t = m.texturen()
        paare = {}
        for i, basis in BIOM_WUESTE.items():
            for art, endung in (("albedoMap", "dh"), ("normalMap", "n"), ("surfaceMap", "s")):
                alt = t.get("me_layer%d_%s" % (i, art))
                if alt:
                    paare[alt] = BIOMES + "%s_%s.tif" % (basis, endung)
        r.set_fragment_data(0, grafik.material_aendern(r.data(), texturen=paare))
        tausch.update(paare)
        geaendert.append(r.identifier.rsplit("/", 1)[-1])
    fehlend = [k for k in set(tausch.values()) if a.find(k) is None]
    if fehlend:
        raise RuntimeError("Biom Wüste: Texturen fehlen nach dem Kopieren: %s" % fehlend)
    return {"grasmaterialien": len(geaendert), "ressourcen_neu": len(neu), "mipmaps": len(mips), "offen": fehlt}, list(mips)


def farbton_setzen(a, farbton):
    """Multipliziert ``me_layerN_tint`` aller Grasmaterialien mit dem Faktor des Farbtons."""
    f = np.array(FARBTOENE[farbton])
    n = 0
    for r, m in grasmaterialien(a):
        k = {}
        for name, wert in m.konstanten().items():
            if name.startswith("me_layer") and name.endswith("_tint"):
                k[name] = tuple(float(v) for v in np.array(wert) * f)
        r.set_fragment_data(0, grafik.material_aendern(r.data(), konstanten=k))
        n += 1
    return {"grasmaterialien": n, "faktor": [float(v) for v in f]}


def weltkarten_einsetzen(a, code, npz, ideallinie, biom="standard", farbton="standard", spielordner=None, arbeit=None):
    """Alle Weltkarten in das Archiv ``a``: Boden, TrackInfo, Außenterrain, optional Biom und Farbton.

    Muss nach Geometrie und Bergen laufen; die Strecke braucht echte Welt-UV. Rückgabe: (Bericht, Mip-Dateien fürs Paket)."""
    if biom not in BIOME:
        raise ValueError("Biom %r unbekannt" % biom)
    if farbton not in FARBTOENE:
        raise ValueError("Farbton %r unbekannt" % farbton)
    bericht = {"biom": biom, "farbton": farbton}
    bericht["innen"] = innenkarten(a, arbeit, npz, flecken_bis_m=40.0, bewuchs=biom != "wueste")
    z, w, breite = ideallinie_stationen(a, ideallinie)
    bericht["trackinfo"] = karte_schreiben(a, "por_trackinfo_a_01_inf", trackinfo_bild(z, w, breite), arbeit, "bau")
    faktor = np.array(FARBTOENE[farbton])
    farben = tuple(np.clip(c * faktor, 0, 255) for c in AUSSEN_FARBEN[biom])
    bericht["aussen"] = aussenkarten(a, arbeit, "bau", farben)
    mips = []
    if biom == "wueste":
        bericht["biom_daten"], mips = biom_wueste(a, code, spielordner)
    if farbton != "standard":
        bericht["farbton_daten"] = farbton_setzen(a, farbton)
    return bericht, mips
