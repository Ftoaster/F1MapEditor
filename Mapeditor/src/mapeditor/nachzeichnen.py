"""Strecke aus einem Bild nachzeichnen (Bildvorlage): Fahrbahnmaske, billigste Wege, Glätten, Regelprüfung.

Ohne Qt, nur NumPy (Pillow nur zum Laden). Herkunft: Prototyp ``ego-werkzeuge/analyse/bild_strecke_prototyp.py``,
Untersuchung und Messwerte ``docs/mapeditor/bild_import.md``. Gegenüber dem Prototyp sucht die Wegsuche mit A*
(Luftlinie als Schätzung, Mindestkosten 1 je Pixel), damit Teilwege beim Klicken in Sekundenbruchteilen stehen.

Koordinaten: Alle Funktionen rechnen in Pixeln des (bei Bedarf verkleinerten) Arbeitsbilds, (x, y) mit y nach unten.
Die Umrechnung in Originalpixel (``/ faktor``) und Meter übernimmt der Aufrufer bzw. ``kontrollpunkte``.

Regeln einer gültigen Runde (Nutzer 3.11.2026): vollständig, keine Überschneidung, kein Stück doppelt befahren.
"""

from __future__ import annotations

import heapq
import io
import math

import numpy as np

#: Größte Fläche des Arbeitsbilds (Pixel); größere Bilder werden verkleinert (Laufzeit der Wegsuche).
MAX_PIXEL = 1_500_000
#: Kosten außerhalb der Fahrbahnmaske: Grundwert plus Zuschlag je Pixel Abstand zur Maske. Kurze Lücken (Schrift,
#: Schatten) bleiben billig, weite Sprünge zwischen getrennten Streckenteilen werden teuer.
KOSTEN_AUSSEN = 10.0
KOSTEN_AUSSEN_JE_PX = 15.0
#: Ein-Klick-Verfahren im Linienmodus: Gegenpunkt nur bis Halbbreite + so viele px neben dem Bandkern gesucht.
LUECKE_MAX_PX = 3
#: Kontrast (Grauwerte) im Linienmodus: dunkler als das hellste Pixel im 5×5-Umfeld.
LINIE_KONTRAST = 6.0
#: Regelgrenzen: Anteil der Linie neben einem entfernten Teil derselben Linie (%), Längenabweichung (Anteil).
SELBSTNAH_MAX_PCT = 1.0
LAENGE_TOLERANZ = 0.10
#: Zweiter Weg: höchstens so viel teurer als der erste, und mindestens eine Fahrbahnbreite entfernt.
ALTERNATIVE_KOSTEN_FAKTOR = 1.5
MODI = ("linie", "farbe")

_NACHBARN = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


# =============================================================================== Bild
def bild_laden(daten):
    """PNG/JPEG-Bytes oder Pfad → (RGB-Feld float32 des Arbeitsbilds, Faktor Arbeitsbild/Original, Originalgröße)."""
    from PIL import Image
    bild = Image.open(io.BytesIO(daten) if isinstance(daten, (bytes, bytearray)) else daten).convert("RGB")
    groesse = bild.size
    faktor = min(1.0, math.sqrt(MAX_PIXEL / float(bild.width * bild.height)))
    if faktor < 1.0:
        bild = bild.resize((max(1, int(bild.width * faktor)), max(1, int(bild.height * faktor))), Image.LANCZOS)
        faktor = bild.width / float(groesse[0])
    return np.asarray(bild, np.float32), faktor, groesse


def _verschiebe(a, dy, dx, fuell):
    out = np.full_like(a, fuell)
    h, w = a.shape[:2]
    ys, yd = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else (slice(-dy, h), slice(0, h + dy))
    xs, xd = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else (slice(-dx, w), slice(0, w + dx))
    out[yd, xd] = a[ys, xs]
    return out


def _erodieren(m):
    r = m.copy()
    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        r &= _verschiebe(m, dy, dx, False)
    return r


def _dilatieren(m):
    r = m.copy()
    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        r |= _verschiebe(m, dy, dx, False)
    return r


def randabstand(maske, grenze=60):
    """Abstand (px, Raute) jedes Maskenpixels zum Rand, durch wiederholtes Erodieren."""
    d = np.zeros(maske.shape, np.float32)
    m = maske.copy()
    for _ in range(grenze):
        if not m.any():
            break
        d += m
        m = _erodieren(m)
    return d


def farbabstand(rgb, farbe):
    return np.sqrt(((rgb - np.asarray(farbe, np.float32)) ** 2).sum(axis=2))


def _otsu(werte, stufen=64):
    """Schwelle, die die Werte in zwei Gruppen mit größter Zwischenklassenvarianz teilt."""
    hist, kanten = np.histogram(werte, bins=stufen)
    mitte = (kanten[:-1] + kanten[1:]) / 2
    w0 = np.cumsum(hist)
    w1 = w0[-1] - w0
    m0 = np.cumsum(hist * mitte) / np.maximum(w0, 1)
    m1 = (np.sum(hist * mitte) - np.cumsum(hist * mitte)) / np.maximum(w1, 1)
    return float(kanten[1:][int(np.argmax(w0 * w1 * (m0 - m1) ** 2))])


class Kostenbild:
    """Fahrbahnmaske, Randabstand und Kosten je Pixel (Mitte 1, Rand 5, außerhalb ab 10)."""

    def __init__(self, maske, halbbreite, modus, farbe=None, schwelle=None):
        self.maske = maske
        self.abstand = randabstand(maske)
        self.halbbreite = float(halbbreite)
        self.modus, self.farbe, self.schwelle = modus, farbe, schwelle
        aussen = KOSTEN_AUSSEN + KOSTEN_AUSSEN_JE_PX * randabstand(~maske, grenze=40)
        innen = 1.0 + 4.0 * (1.0 - np.minimum(self.abstand / self.halbbreite, 1.0))
        self.kosten = np.where(maske, innen, aussen).astype(np.float32)

    @property
    def form(self):
        return self.maske.shape


def kostenbild(rgb, klick, modus="linie"):
    """Kostenbild zum ersten Stützpunkt ``klick`` (x, y).

    ``farbe`` (Luftbild, einfarbige Karten): Farbe am Klick (3×3-Median nach 3×3-Weichzeichnen), Schwelle nach Otsu aus
    dem Umfeld (121×121), halbiert, 15–35. ``linie`` (Kartenansicht): Pixel, die um mehr als ``LINIE_KONTRAST`` dunkler
    sind als das hellste im 5×5-Umfeld (nach 3×3-Weichzeichnen) – Google zeichnet Rennstrecken als Doppellinie mit fast weißer Füllung, die
    weißen Flächen gleicht. In beiden Fällen schließt die Maske danach 2 px (Rand, Füllung, Rand werden ein Band)."""
    if modus not in MODI:
        raise ValueError(modus)
    h, w = rgb.shape[:2]
    x, y = int(round(min(max(klick[0], 0), w - 1))), int(round(min(max(klick[1], 0), h - 1)))
    farbe = schwelle = None
    glatt = sum(_verschiebe(rgb, dy, dx, 0.0) for dy in (-1, 0, 1) for dx in (-1, 0, 1)) / 9.0   # Rauschen dämpfen
    if modus == "linie":
        grau = glatt.mean(axis=2)
        hell = grau.copy()
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                hell = np.maximum(hell, _verschiebe(grau, dy, dx, 0.0))
        maske = (hell - grau) > LINIE_KONTRAST
    else:
        probe = glatt[max(0, y - 1):y + 2, max(0, x - 1):x + 2].reshape(-1, 3)
        farbe = np.median(probe, axis=0)
        d = farbabstand(glatt, farbe)
        umfeld = d[max(0, y - 60):y + 61, max(0, x - 60):x + 61]
        schwelle = min(35.0, max(15.0, 0.5 * _otsu(np.minimum(umfeld, 150.0))))
        maske = d < schwelle
        farbe = [round(float(c)) for c in farbe]
    maske = _erodieren(_erodieren(_dilatieren(_dilatieren(maske))))
    abstand = randabstand(maske)
    halb = max(2.0, float(abstand[max(0, y - 15):y + 16, max(0, x - 15):x + 16].max()))
    return Kostenbild(maske, halb, modus, farbe, schwelle)


def einrasten(kb, punkt, radius=8):
    """Punkt auf die Fahrbahnmitte (größter Randabstand in der Nähe) ziehen; (x, y) als ganze Pixel."""
    h, w = kb.form
    x = int(round(min(max(punkt[0], 0), w - 1)))
    y = int(round(min(max(punkt[1], 0), h - 1)))
    y0, y1, x0, x1 = max(0, y - radius), min(h, y + radius + 1), max(0, x - radius), min(w, x + radius + 1)
    fenster = kb.abstand[y0:y1, x0:x1]
    if not fenster.any():
        return x, y
    iy, ix = np.unravel_index(int(np.argmax(fenster)), fenster.shape)
    return x0 + int(ix), y0 + int(iy)


# =============================================================================== Wegsuche
class Abbruch(Exception):
    """Eine Wegsuche wurde von außen abgebrochen (``abbrechen()`` liefert True)."""


def wegsuche(kosten, start, ziel=None, gesperrt=None, abbrechen=None):
    """Billigster 8er-Pfad von ``start`` nach ``ziel`` (je (x, y)), Kantengewicht = mittlere Kosten × Schrittlänge.

    Mit Ziel A* (Luftlinie, zulässig, da jedes Pixel mindestens 1 kostet). ``ziel=None``: Wegkosten zu allen Pixeln
    (Bildform). Rückgabe mit Ziel: (Pfad N×2 float, Kosten); ohne Weg ``ValueError``."""
    h, w = kosten.shape
    k = kosten.ravel()
    sperre = gesperrt.ravel() if gesperrt is not None else None
    s = int(start[1]) * w + int(start[0])
    z = -1 if ziel is None else int(ziel[1]) * w + int(ziel[0])
    zx, zy = (-1, -1) if ziel is None else (int(ziel[0]), int(ziel[1]))
    dist = np.full(h * w, np.inf)
    vor = np.full(h * w, -1, np.int64)
    dist[s] = 0.0
    schaetzung = (lambda x, y: math.hypot(x - zx, y - zy)) if ziel is not None else (lambda x, y: 0.0)
    heap = [(schaetzung(start[0], start[1]), 0.0, s)]
    schritte = [(dy * w + dx, dy, dx, math.hypot(dy, dx)) for dy, dx in _NACHBARN]
    zaehler = 0
    while heap:
        _, dv, v = heapq.heappop(heap)
        if v == z:
            break
        if dv > dist[v]:
            continue
        zaehler += 1
        if abbrechen is not None and zaehler % 4096 == 0 and abbrechen():
            raise Abbruch()
        vy, vx = divmod(v, w)
        kv = k[v]
        for off, dy, dx, laenge in schritte:
            ny, nx = vy + dy, vx + dx
            if ny < 0 or ny >= h or nx < 0 or nx >= w:
                continue
            u = v + off
            if sperre is not None and sperre[u]:
                continue
            nd = dv + 0.5 * (kv + k[u]) * laenge
            if nd < dist[u]:
                dist[u] = nd
                vor[u] = v
                heapq.heappush(heap, (nd + schaetzung(nx, ny), nd, u))
    if ziel is None:
        return dist.reshape(h, w)
    if not np.isfinite(dist[z]):
        raise ValueError("kein Weg")
    pfad = [z]
    while pfad[-1] != s:
        pfad.append(int(vor[pfad[-1]]))
    pfad.reverse()
    return np.array([(p % w, p // w) for p in pfad], float), float(dist[z])


def teilweg(kb, a, b, abbrechen=None):
    """Billigster Weg zwischen zwei (eingerasteten) Punkten: (Pfad, Kosten)."""
    if tuple(a) == tuple(b):
        return np.array([a], float), 0.0
    return wegsuche(kb.kosten, a, b, abbrechen=abbrechen)


def _stempel(form, punkte, radius):
    """Bool-Feld: alle Pixel höchstens ``radius`` (Raute) von den Punkten entfernt."""
    m = np.zeros(form, bool)
    p = np.asarray(punkte, int)
    p = p[(p[:, 0] >= 0) & (p[:, 0] < form[1]) & (p[:, 1] >= 0) & (p[:, 1] < form[0])]
    m[p[:, 1], p[:, 0]] = True
    for _ in range(int(radius)):
        m = _dilatieren(m)
    return m


def alternative(kb, a, b, pfad, kosten, abbrechen=None):
    """Zweiter, deutlich anderer Weg zwischen a und b (Boxengasse statt Gerade, Oval statt Innenfeld) oder None.

    Der erste Weg wird eine Fahrbahnbreite breit gesperrt (außer nahe a und b); der neue Weg darf höchstens
    ``ALTERNATIVE_KOSTEN_FAKTOR``-mal so teuer sein und muss im Mittel mindestens eine Fahrbahnbreite entfernt liegen."""
    if len(pfad) < 8:
        return None
    breite = max(3.0, 2.0 * kb.halbbreite)
    sperre = _stempel(kb.form, pfad, breite)
    frei = _stempel(kb.form, [a, b], 1.5 * breite)
    sperre &= ~frei
    try:
        neu, neu_kosten = wegsuche(kb.kosten, a, b, sperre, abbrechen)
    except ValueError:
        return None
    if neu_kosten > ALTERNATIVE_KOSTEN_FAKTOR * kosten:
        return None
    alt = _neu_abtasten(neu, 2.0)
    d = np.array([np.min(np.hypot(*(pfad - q).T)) for q in alt[:: max(1, len(alt) // 200)]])
    if d.mean() < breite:
        return None
    return neu


def runde_ein_klick(kb, klick, abbrechen=None):
    """Geschlossene Runde aus einem Klick (Pfad N×2, geschlossen gedacht).

    Gegenpunkt = Fahrbahnpunkt mit den höchsten Wegkosten vom Klick, nur auf Stellen ähnlicher Breite wie am Klick
    (60–160 % der Halbbreite). Im Linienmodus nur im Kern fahrbahnbreiter Bänder gesucht (sonst gewinnt ein Fremdband
    wie eine Autobahn, gerade weil es nur über dünne, teure Wege erreichbar ist); bei Farbbildern verschlechterte das die
    Testreihe (2 statt 14 von 36) und gilt dort nicht. Danach zwei billigste Wege Klick → Gegenpunkt → Klick, mit kurzer
    Quersperre am Klick, damit kein Teilweg rückwärts losfährt."""
    maske, abstand, halb = kb.maske, kb.abstand, kb.halbbreite
    p = einrasten(kb, klick)
    h, w = maske.shape
    x, y = p
    r = int(3 * halb + 6)
    ys, xs = np.nonzero(maske[max(0, y - r):y + r + 1, max(0, x - r):x + r + 1])
    pts = np.column_stack([xs + max(0, x - r) - x, ys + max(0, y - r) - y]).astype(float)
    pts = pts[np.hypot(pts[:, 0], pts[:, 1]) <= r]
    if len(pts) < 5:
        raise ValueError("keine Fahrbahn")
    _, eigv = np.linalg.eigh(np.cov(pts.T))
    t = eigv[:, -1] / np.linalg.norm(eigv[:, -1])
    n = np.array([-t[1], t[0]])
    sperre = np.zeros((h, w), bool)
    for seite in (1, -1):
        for s in np.arange(0, halb + 3, 0.5):
            q = np.array(p, float) + seite * s * n
            for dt in (-1.0, 0.0, 1.0):
                ax, ay = int(round(q[0] + dt * t[0])), int(round(q[1] + dt * t[1]))
                if 0 <= ax < w and 0 <= ay < h:
                    sperre[ay, ax] = True
    vorn = tuple(int(round(v)) for v in np.clip(np.array(p) + 3 * t, 0, [w - 1, h - 1]))
    hinten = tuple(int(round(v)) for v in np.clip(np.array(p) - 3 * t, 0, [w - 1, h - 1]))
    suche = None
    if kb.modus == "linie":
        # erlaubt: Kern und alles bis Halbbreite + Lücke daneben (überbrückt schwache Stellen im Band, z. B. schräg
        # gezeichnete Abschnitte mit nicht ganz geschlossener Füllung); lange dünne Wege zu Fremdbändern bleiben gesperrt
        kern = abstand >= max(1.0, 0.6 * halb)
        grenze = LUECKE_MAX_PX + int(halb)
        suche = randabstand(~kern, grenze=grenze + 1) > grenze
    wege = wegsuche(kb.kosten, p, None, suche, abbrechen)
    kandidat = maske & (abstand >= max(1.0, 0.6 * halb)) & (abstand <= 1.6 * halb + 1) & np.isfinite(wege)
    if not kandidat.any():
        raise ValueError("keine Fahrbahn")
    gy, gx = np.unravel_index(int(np.argmax(np.where(kandidat, wege, -1.0))), wege.shape)
    hin, _ = wegsuche(kb.kosten, vorn, (int(gx), int(gy)), sperre, abbrechen)
    zurueck, _ = wegsuche(kb.kosten, (int(gx), int(gy)), hinten, sperre, abbrechen)
    return np.vstack([hin[:-1], zurueck])


# =============================================================================== Linie
def _neu_abtasten(xy, abstand, geschlossen=False):
    xy = np.asarray(xy, float)
    ring = np.vstack([xy, xy[:1]]) if geschlossen else xy
    s = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(ring, axis=0), axis=1))]
    if s[-1] <= 0:
        return xy[:1].copy()
    n = max(2, int(round(s[-1] / abstand)))
    st = np.linspace(0.0, s[-1], n, endpoint=not geschlossen)
    return np.column_stack([np.interp(st, s, ring[:, 0]), np.interp(st, s, ring[:, 1])])


def ring_laenge(xy):
    return float(np.linalg.norm(np.roll(xy, -1, axis=0) - xy, axis=1).sum())


def _glaetten_ring(xy, sigma):
    r = int(3 * sigma) + 1
    kern = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    kern /= kern.sum()
    idx = (np.arange(len(xy))[:, None] + np.arange(-r, r + 1)[None]) % len(xy)
    return np.einsum("nkd,k->nd", xy[idx], kern)


def _douglas_peucker(xy, toleranz):
    behalten = np.zeros(len(xy), bool)
    behalten[[0, -1]] = True
    stapel = [(0, len(xy) - 1)]
    while stapel:
        a, e = stapel.pop()
        if e - a < 2:
            continue
        sehne = xy[e] - xy[a]
        laenge = np.linalg.norm(sehne)
        rel = xy[a + 1:e] - xy[a]
        d = (np.abs(rel[:, 0] * sehne[1] - rel[:, 1] * sehne[0]) / laenge if laenge > 1e-9
             else np.linalg.norm(rel, axis=1))
        i = int(np.argmax(d))
        if d[i] > toleranz:
            behalten[a + 1 + i] = True
            stapel += [(a, a + 1 + i), (a + 1 + i, e)]
    return xy[behalten]


def mittellinie(pfad, halbbreite, toleranz_px=0.6):
    """Treppen-Pixelpfad einer geschlossenen Runde → glatte Linie (1 px Abstand).

    Nach der Gauß-Glättung (σ = Halbbreite) bleibt auf Geraden eine Restwelligkeit, die über der Geradenschwelle des
    Generators (1/700 m⁻¹) liegen kann; Douglas-Peucker mit ``toleranz_px`` macht Geraden gerade (sonst keine DRS)."""
    xy = _glaetten_ring(_neu_abtasten(pfad, 1.0, True), max(2.0, halbbreite))
    fern = int(np.argmax(np.linalg.norm(xy - xy[0], axis=1)))
    teil1 = _douglas_peucker(xy[:fern + 1], toleranz_px)
    teil2 = _douglas_peucker(np.vstack([xy[fern:], xy[:1]]), toleranz_px)
    return _neu_abtasten(np.vstack([teil1[:-1], teil2[:-1]]), 1.0, True)


def _schnitte(xy):
    """Indexpaare (i, j), i < j, nicht benachbarter Abschnitte eines geschlossenen Linienzugs, die sich schneiden."""
    d = np.roll(xy, -1, axis=0) - xy
    n = len(xy)
    paare = []
    for i0 in range(0, n, 128):
        i = np.arange(i0, min(n, i0 + 128))
        p, r = xy[i][:, None], d[i][:, None]
        qp = xy[None] - p
        kreuz = r[..., 0] * d[None][..., 1] - r[..., 1] * d[None][..., 0]
        with np.errstate(divide="ignore", invalid="ignore"):
            u = (qp[..., 0] * d[None][..., 1] - qp[..., 1] * d[None][..., 0]) / kreuz
            v = (qp[..., 0] * r[..., 1] - qp[..., 1] * r[..., 0]) / kreuz
        treffer = (np.abs(kreuz) > 1e-12) & (u >= 0) & (u < 1) & (v >= 0) & (v < 1)
        abstand = np.abs(i[:, None] - np.arange(n)[None])
        treffer &= (np.minimum(abstand, n - abstand) > 1) & (i[:, None] < np.arange(n)[None])
        zi, zj = np.nonzero(treffer)
        paare += [(int(i[x]), int(y)) for x, y in zip(zi, zj)]
    return paare


def _orte(xy, maske, hoechstens=12):
    """Je zusammenhängendem Lauf markierter Linienpunkte ein Ort (Laufmitte), höchstens ``hoechstens``."""
    idx = np.nonzero(maske)[0]
    if not len(idx):
        return []
    laeufe, start = [], idx[0]
    for a, b in zip(idx, idx[1:]):
        if b != a + 1:
            laeufe.append((start, a))
            start = b
    laeufe.append((start, idx[-1]))
    return [[float(v) for v in xy[(a + e) // 2]] for a, e in laeufe[:hoechstens]]


def pruefen(linie, halbbreite, m_pro_px=None, laenge_soll_m=None):
    """Harte Prüfung einer geschlossenen Linie (Arbeitsbild-Pixel). Rückgabe: Wörterbuch mit

    - ``selbstnah_pct``: Anteil, der näher als eine Fahrbahnbreite an einem entfernten Teil derselben Linie liegt
      (doppelt befahren, Abkürzung, unvollständig); Fehler über ``SELBSTNAH_MAX_PCT``;
    - ``schnitte``: Selbstkreuzungen der Mittellinie (der Generator kennt keine Brücken);
    - ``laenge_m`` / ``laenge_abweichung``: nur mit Maßstab; Fehler über ``LAENGE_TOLERANZ`` (fehlendes Stück);
    - ``fehler``: Liste der verletzten Regeln (``doppelt``, ``ueberschneidung``, ``laenge``) mit ``orte`` (px)."""
    xy = _neu_abtasten(linie, 2.0, True)
    n = len(xy)
    nah = np.zeros(n, bool)
    sperr = int(6 * halbbreite / 2.0) + 5
    for i in range(0, n, 256):
        d = np.linalg.norm(xy[i:i + 256, None] - xy[None], axis=2)
        j = np.arange(i, min(n, i + 256))[:, None]
        bogen = np.abs(j - np.arange(n)[None])
        bogen = np.minimum(bogen, n - bogen)
        nah[i:i + 256] = ((d < 2 * halbbreite) & (bogen > sperr)).any(axis=1)
    ergebnis = {"selbstnah_pct": round(100.0 * float(nah.mean()), 1), "schnitte": 0, "fehler": []}
    if ergebnis["selbstnah_pct"] > SELBSTNAH_MAX_PCT:
        ergebnis["fehler"].append({"regel": "doppelt", "orte": _orte(xy, nah)})
    schnitte = _schnitte(xy)
    ergebnis["schnitte"] = len(schnitte)
    if schnitte:
        ergebnis["fehler"].append({"regel": "ueberschneidung", "orte": [[float(v) for v in xy[i]] for i, _ in schnitte[:12]]})
    if m_pro_px:
        laenge = ring_laenge(linie) * m_pro_px
        ergebnis["laenge_m"] = laenge
        if laenge_soll_m:
            ergebnis["laenge_abweichung"] = laenge / float(laenge_soll_m) - 1.0
            if abs(ergebnis["laenge_abweichung"]) > LAENGE_TOLERANZ:
                ergebnis["fehler"].append({"regel": "laenge", "orte": []})
    return ergebnis


def gleichmaessig(pfad, anzahl):
    """``anzahl`` Punkte gleichmäßig entlang eines geschlossenen Pfads (für Stützpunkte aus der Ein-Klick-Runde)."""
    return [tuple(float(v) for v in p) for p in _neu_abtasten(pfad, ring_laenge(pfad) / anzahl, True)[:anzahl]]


def kontrollpunkte(linie, faktor, m_pro_px, x0, z0, abstand_m=5.0):
    """Glatte Linie (Arbeitsbild-Pixel) → Kontrollpunkte {x, y, z} in Metern; Bild-Ursprung (x0, z0) oben links."""
    meter = np.asarray(linie, float) / faktor * m_pro_px + [x0, z0]
    meter = _neu_abtasten(meter, abstand_m, True)
    return [{"x": round(float(x), 3), "y": 0.0, "z": round(float(z), 3)} for x, z in meter]
