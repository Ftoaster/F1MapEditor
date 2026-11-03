"""Nachzeichnen aus einer Bildvorlage: Kern ohne Qt (Maske, Wegsuche, zweiter Weg, Regeln, Umrechnung)."""

import io
import math

import numpy as np
import pytest
from PIL import Image, ImageDraw

from mapeditor import nachzeichnen as nz


def oval(cx=300, cy=220, rx=220, ry=140, n=400):
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return np.column_stack([cx + rx * np.cos(t), cy + ry * np.sin(t)])


def kartenbild(boxengasse=True, wege=True, gasse_breit=False):
    """Kartenansicht wie Google: beiger Grund, Strecke als graue Doppellinie mit heller Füllung (8 px, Füllung 3 px), dünne Wege,
    Boxengasse parallel zur unteren Geraden (an beiden Enden angebunden)."""
    bild = Image.new("RGB", (600, 440), (246, 239, 230))
    dr = ImageDraw.Draw(bild)
    if wege:
        dr.line([(0, 30), (600, 60)], fill=(205, 210, 220), width=2)
        dr.line([(560, 0), (520, 440)], fill=(205, 210, 220), width=2)
    ring = [tuple(p) for p in np.vstack([oval(), oval()[:1]])]
    dr.line(ring, fill=(210, 216, 226), width=8, joint="curve")        # wie Hockenheim: 2–3 px Füllung
    dr.line(ring, fill=(245, 245, 255), width=3, joint="curve")
    if boxengasse:
        gasse = [(180, 337), (215, 376), (385, 376), (420, 337)]        # Enden auf dem Oval
        dr.line(gasse, fill=(210, 216, 226), width=8 if gasse_breit else 6, joint="curve")
        dr.line(gasse, fill=(245, 245, 255), width=3 if gasse_breit else 2, joint="curve")
    puffer = io.BytesIO()
    bild.save(puffer, "PNG")
    return puffer.getvalue()


def abweichung(linie, wahr):
    return float(np.mean([np.min(np.hypot(*(wahr - p).T)) for p in linie[::5]]))


def test_bild_laden_verkleinert_grosse_bilder():
    bild = Image.new("RGB", (3000, 1000), (255, 255, 255))
    puffer = io.BytesIO()
    bild.save(puffer, "PNG")
    rgb, faktor, groesse = nz.bild_laden(puffer.getvalue())
    assert groesse == (3000, 1000)
    assert rgb.shape[0] * rgb.shape[1] <= nz.MAX_PIXEL
    assert faktor == pytest.approx(rgb.shape[1] / 3000)


def test_astern_gleich_dijkstra():
    rgb, _, _ = nz.bild_laden(kartenbild())
    kb = nz.kostenbild(rgb, (520, 220), "linie")
    a, b = nz.einrasten(kb, (520, 220)), nz.einrasten(kb, (300, 80))
    _, kosten = nz.wegsuche(kb.kosten, a, b)
    alle = nz.wegsuche(kb.kosten, a, None)
    assert kosten == pytest.approx(float(alle[b[1], b[0]]), rel=1e-9)


def test_ein_klick_findet_die_runde_im_linienmodus():
    rgb, _, _ = nz.bild_laden(kartenbild())
    kb = nz.kostenbild(rgb, (520, 220), "linie")
    assert kb.halbbreite >= 3                                  # Doppellinie + Füllung = ein Band
    pfad = nz.runde_ein_klick(kb, (520, 220))
    linie = nz.mittellinie(pfad, kb.halbbreite)
    assert abweichung(linie, oval()) < 2.0
    assert nz.pruefen(linie, kb.halbbreite)["fehler"] == []


def test_stuetzpunkte_runde_und_laengenpruefung():
    rgb, _, _ = nz.bild_laden(kartenbild())
    kb = nz.kostenbild(rgb, (520, 220), "linie")
    punkte = [nz.einrasten(kb, p) for p in ((520, 220), (300, 80), (80, 220), (300, 360))]
    pfad = np.vstack([nz.teilweg(kb, punkte[i], punkte[(i + 1) % 4])[0][:-1] for i in range(4)])
    linie = nz.mittellinie(pfad, kb.halbbreite)
    wahr = nz.ring_laenge(oval(n=4000))
    ok = nz.pruefen(linie, kb.halbbreite, m_pro_px=2.0, laenge_soll_m=wahr * 2.0)
    assert ok["fehler"] == [] and abs(ok["laenge_abweichung"]) < 0.03
    falsch = nz.pruefen(linie, kb.halbbreite, m_pro_px=2.0, laenge_soll_m=wahr * 2.0 * 1.5)
    assert [f["regel"] for f in falsch["fehler"]] == ["laenge"]


def test_zweiter_weg_nur_bei_echter_mehrdeutigkeit():
    """Gleich breiter Parallelweg (wie Oval/Innenfeld): B wird angeboten. Schmale Boxengasse ist deutlich teurer
    und damit nicht mehrdeutig: kein B. Ohne Parallelweg ebenfalls keins."""
    def zweiter(**art):
        rgb, _, _ = nz.bild_laden(kartenbild(**art))
        kb = nz.kostenbild(rgb, (520, 220), "linie")
        a, b = nz.einrasten(kb, (180, 345)), nz.einrasten(kb, (420, 345))
        pfad, kosten = nz.teilweg(kb, a, b)
        return pfad, nz.alternative(kb, a, b, pfad, kosten)
    pfad, alt = zweiter(gasse_breit=True)
    assert alt is not None
    assert abs(np.max(alt[:, 1]) - np.max(pfad[:, 1])) > 8         # die beiden Wege liegen deutlich auseinander
    assert zweiter()[1] is None
    assert zweiter(boxengasse=False)[1] is None


def test_farbmodus_auf_einfarbiger_strecke():
    bild = Image.new("RGB", (600, 440), (90, 120, 60))
    dr = ImageDraw.Draw(bild)
    dr.line([tuple(p) for p in np.vstack([oval(), oval()[:1]])], fill=(80, 80, 86), width=10, joint="curve")
    rgb = np.asarray(bild, np.float32)
    kb = nz.kostenbild(rgb, (520, 220), "farbe")
    linie = nz.mittellinie(nz.runde_ein_klick(kb, (520, 220)), kb.halbbreite)
    assert abweichung(linie, oval()) < 2.0


def test_regeln_doppelt_und_ueberschneidung():
    t = np.linspace(0, 2 * np.pi, 800, endpoint=False)
    kreis = np.column_stack([300 + 200 * np.cos(t), 300 + 200 * np.sin(t)])
    acht = np.column_stack([300 + 200 * np.sin(t), 300 + 100 * np.sin(2 * t)])
    hin = np.column_stack([np.linspace(0, 400, 400), np.full(400, 50.0)])
    zurueck = np.vstack([hin, hin[::-1] + [0, 2]])
    regeln = lambda linie: [f["regel"] for f in nz.pruefen(linie, 4.0)["fehler"]]
    assert regeln(kreis) == []
    assert "ueberschneidung" in regeln(acht)
    assert regeln(zurueck)[0] == "doppelt"
    ort = nz.pruefen(acht, 4.0)["fehler"][-1]["orte"][0]
    assert math.hypot(ort[0] - 300, ort[1] - 300) < 5             # Kreuzung der Acht in der Mitte


def test_kontrollpunkte_in_metern():
    linie = np.array([[0, 0], [100, 0], [100, 50], [0, 50]], float)
    punkte = nz.kontrollpunkte(linie, faktor=0.5, m_pro_px=2.0, x0=-100.0, z0=10.0, abstand_m=20.0)
    xs, zs = [p["x"] for p in punkte], [p["z"] for p in punkte]
    assert min(xs) == pytest.approx(-100) and max(xs) == pytest.approx(300)     # 100 px Arbeitsbild = 200 px Original = 400 m
    assert min(zs) == pytest.approx(10) and max(zs) == pytest.approx(210)
    assert len(punkte) == 60                                                   # 1.200 m Umfang / 20 m


def test_abbruch_der_wegsuche():
    rgb, _, _ = nz.bild_laden(kartenbild())
    kb = nz.kostenbild(rgb, (520, 220), "linie")
    with pytest.raises(nz.Abbruch):
        nz.wegsuche(kb.kosten, (10, 10), None, abbrechen=lambda: True)
