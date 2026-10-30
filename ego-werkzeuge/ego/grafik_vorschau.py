"""Einfache PNG-Vorschau für Geometrie (nur numpy + Pillow).

Zweck: visuell prüfen, ob dekodierte oder selbst erzeugte Geometrie stimmt. Es gibt zwei
Verfahren:

    maler    Dreiecke werden mit Pillow von hinten nach vorn gezeichnet (Maleralgorithmus),
             flach schattiert, optional mit Kanten. Scharf, für bis ca. 200.000 Dreiecke.
    punkte   Jedes Dreieck wird mit Punkten proportional zu seiner Bildfläche abgetastet und
             mit einem Tiefenpuffer eingetragen. Für sehr große Netze (Terrain, Millionen
             Dreiecke), bei denen die meisten Dreiecke kleiner als ein Pixel sind.

Koordinaten: Engine-Konvention, y ist oben. Ansichten: oben (Blick nach -y, x nach rechts,
z nach unten im Bild), vorn (Blick nach -z), seite (Blick nach +x), iso (45°/30°).
"""

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ANSICHTEN = ("oben", "vorn", "seite", "iso")

# Farben für Terrain-Materialien nach Namensteilen (erste Übereinstimmung gilt)
MATERIAL_FARBEN = [
    ("kerb", (205, 60, 50)),
    ("rumble", (205, 60, 50)),
    ("line_white", (240, 240, 240)),
    ("white", (235, 235, 235)),
    ("yellow", (230, 200, 40)),
    ("gridline", (240, 240, 240)),
    ("parkline", (240, 240, 240)),
    ("track", (70, 70, 75)),
    ("tarmac", (90, 90, 95)),
    ("asphalt", (70, 70, 75)),
    ("skid", (50, 50, 50)),
    ("astroturf", (60, 150, 80)),
    ("green", (60, 150, 80)),
    ("grass", (95, 150, 70)),
    ("turf", (95, 150, 70)),
    ("gravel", (190, 175, 140)),
    ("sand", (215, 195, 150)),
    ("beige", (210, 190, 150)),
    ("offtrack", (150, 150, 150)),
    ("terrain", (185, 160, 120)),
    ("water", (60, 110, 190)),
    ("decal", (200, 200, 200)),
]


def material_farbe(name: str, nummer: int = 0) -> Tuple[int, int, int]:
    n = (name or "").lower().rsplit("/", 1)[-1]
    for teil, farbe in MATERIAL_FARBEN:
        if teil in n:
            return farbe
    h = (nummer * 0.61803398875 + 0.13) % 1.0
    r, g, b = [abs(((h * 6 + k) % 6) - 3) - 1 for k in (0, 4, 2)]
    return tuple(int(255 * (0.35 + 0.5 * min(1.0, max(0.0, x)))) for x in (r, g, b))


def ansicht_matrix(ansicht: str) -> np.ndarray:
    """3x3-Matrix: Weltpunkt -> (Bild-x rechts, Bild-y oben, Tiefe zur Kamera hin)."""
    if ansicht == "oben":
        return np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], float)
    if ansicht == "vorn":
        return np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1]], float)
    if ansicht == "seite":
        return np.array([[0, 0, -1], [0, 1, 0], [1, 0, 0]], float)
    if ansicht == "iso":
        a, b = math.radians(45), math.radians(30)
        ry = np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])
        rx = np.array([[1, 0, 0], [0, math.cos(b), -math.sin(b)], [0, math.sin(b), math.cos(b)]])
        return rx @ ry
    raise ValueError("Unbekannte Ansicht %s" % ansicht)


def _projizieren(punkte: np.ndarray, m: np.ndarray, groesse: int, rand: int,
                 grenzen: Optional[Tuple[np.ndarray, np.ndarray]] = None):
    v = punkte @ m.T
    mn, mx = (v[:, :2].min(0), v[:, :2].max(0)) if grenzen is None else grenzen
    ausdehnung = max(float((mx - mn).max()), 1e-6)
    massstab = (groesse - 2 * rand) / ausdehnung
    mitte = (mn + mx) / 2
    xy = np.empty((len(v), 2))
    xy[:, 0] = (v[:, 0] - mitte[0]) * massstab + groesse / 2
    xy[:, 1] = groesse / 2 - (v[:, 1] - mitte[1]) * massstab
    return xy, v[:, 2], massstab, ausdehnung


def ansicht_rendern(netze: Sequence, ansicht: str, groesse: int = 600, verfahren: str = "auto",
                    kanten: Optional[bool] = None, farben: Optional[Sequence[Tuple[int, int, int]]] = None,
                    hintergrund=(255, 255, 255)) -> Tuple[Image.Image, Dict[str, float]]:
    """Rendert Netze (Objekte mit .positionen (N,3) und .dreiecke (M,3)) in eine Ansicht."""
    m = ansicht_matrix(ansicht)
    pos_alle, tri_alle, farbe_alle = [], [], []
    off = 0
    for i, n in enumerate(netze):
        if len(n.dreiecke) == 0:
            continue
        pos_alle.append(np.asarray(n.positionen, float))
        tri_alle.append(np.asarray(n.dreiecke, np.int64) + off)
        f = farben[i] if farben else material_farbe(getattr(n, "material", ""), i)
        farbe_alle.append(np.tile(np.array(f, float), (len(n.dreiecke), 1)))
        off += len(n.positionen)
    bild = Image.new("RGB", (groesse, groesse), hintergrund)
    info = {"dreiecke": 0, "massstab": 0.0, "ausdehnung": 0.0}
    if not pos_alle:
        return bild, info
    pos = np.concatenate(pos_alle)
    tri = np.concatenate(tri_alle)
    basis = np.concatenate(farbe_alle)
    rand = max(10, groesse // 25)
    xy, tiefe, massstab, ausdehnung = _projizieren(pos, m, groesse, rand)
    info.update(dreiecke=len(tri), massstab=massstab, ausdehnung=ausdehnung)
    # flache Schattierung: Normale in Ansichtskoordinaten, Licht von vorn oben links
    vp = pos @ m.T
    fn = np.cross(vp[tri[:, 1]] - vp[tri[:, 0]], vp[tri[:, 2]] - vp[tri[:, 0]])
    fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
    licht = np.array([-0.35, 0.55, 0.76])
    licht /= np.linalg.norm(licht)
    hell = 0.35 + 0.65 * np.abs(fn @ licht)
    farbe = np.clip(basis * hell[:, None], 0, 255).astype(np.uint8)
    z = tiefe[tri].mean(1)
    if verfahren == "auto":
        verfahren = "maler" if len(tri) <= 200000 else "punkte"
    if verfahren == "maler":
        if kanten is None:
            kanten = len(tri) <= 4000
        zeichner = ImageDraw.Draw(bild)
        ecken = xy[tri]
        for i in np.argsort(z):
            p = [tuple(e) for e in ecken[i]]
            f = tuple(int(c) for c in farbe[i])
            zeichner.polygon(p, fill=f, outline=(40, 40, 40) if kanten else f)
    else:
        bild = _punkte_rendern(bild, xy, tiefe, tri, farbe)
    return bild, info


def _punkte_rendern(bild: Image.Image, xy: np.ndarray, tiefe: np.ndarray, tri: np.ndarray,
                    farbe: np.ndarray, dichte: float = 3.0, max_punkte: int = 30_000_000) -> Image.Image:
    w, h = bild.size
    a, b, c = xy[tri[:, 0]], xy[tri[:, 1]], xy[tri[:, 2]]
    flaeche = 0.5 * np.abs((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1]))
    anzahl = np.ceil(flaeche * dichte).astype(np.int64) + 1
    if anzahl.sum() > max_punkte:
        anzahl = np.maximum(1, (anzahl * (max_punkte / anzahl.sum())).astype(np.int64))
    idx = np.repeat(np.arange(len(tri)), anzahl)
    rng = np.random.default_rng(1)
    r1 = np.sqrt(rng.random(len(idx)))
    r2 = rng.random(len(idx))
    erst = np.r_[0, np.cumsum(anzahl)[:-1]]
    einzeln = anzahl[idx] == 1  # Einzelpunkt = Schwerpunkt
    u = np.where(einzeln, 1 / 3, 1 - r1)
    v = np.where(einzeln, 1 / 3, r1 * (1 - r2))
    wgt = 1 - u - v
    p = a[idx] * u[:, None] + b[idx] * v[:, None] + c[idx] * wgt[:, None]
    zt = tiefe[tri[idx, 0]] * u + tiefe[tri[idx, 1]] * v + tiefe[tri[idx, 2]] * wgt
    del erst
    px = np.floor(p[:, 0]).astype(np.int64)
    py = np.floor(p[:, 1]).astype(np.int64)
    ok = (px >= 0) & (px < w) & (py >= 0) & (py < h)
    px, py, zt, idx = px[ok], py[ok], zt[ok], idx[ok]
    lin = py * w + px
    reihenfolge = np.lexsort((-zt, lin))  # je Pixel der Kamera am nächsten zuerst
    lin_s = lin[reihenfolge]
    erste = np.r_[True, lin_s[1:] != lin_s[:-1]]
    gewaehlt = reihenfolge[erste]
    arr = np.array(bild)
    flach = arr.reshape(-1, 3)
    flach[lin[gewaehlt]] = farbe[idx[gewaehlt]]
    return Image.fromarray(arr)


def vorschau(netze: Sequence, pfad: str, titel: str = "", groesse: int = 600,
             ansichten: Sequence[str] = ANSICHTEN, verfahren: str = "auto",
             farben: Optional[Sequence[Tuple[int, int, int]]] = None) -> Dict[str, Dict[str, float]]:
    """Schreibt ein PNG mit mehreren Ansichten nebeneinander (2 Spalten)."""
    schrift = ImageFont.load_default()
    bilder, infos = [], {}
    for a in ansichten:
        b, info = ansicht_rendern(netze, a, groesse, verfahren, farben=farben)
        d = ImageDraw.Draw(b)
        d.text((6, 4), "%s  (Breite %.1f m)" % (a, info["ausdehnung"]), fill=(0, 0, 0), font=schrift)
        d.rectangle([0, 0, groesse - 1, groesse - 1], outline=(160, 160, 160))
        bilder.append(b)
        infos[a] = info
    spalten = 2 if len(bilder) > 1 else 1
    zeilen = (len(bilder) + spalten - 1) // spalten
    kopf = 22 if titel else 0
    gesamt = Image.new("RGB", (spalten * groesse, zeilen * groesse + kopf), (255, 255, 255))
    if titel:
        ImageDraw.Draw(gesamt).text((6, 5), titel, fill=(0, 0, 0), font=schrift)
    for i, b in enumerate(bilder):
        gesamt.paste(b, ((i % spalten) * groesse, kopf + (i // spalten) * groesse))
    gesamt.save(pfad)
    return infos
