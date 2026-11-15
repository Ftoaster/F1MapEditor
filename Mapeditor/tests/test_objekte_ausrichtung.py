"""Ausrichtung der Umgebungsobjekte (15.11.2026, docs/mapeditor/objekte_ausrichtung.md).

Grundriss und Vorderpfeil in der Karte nach derselben Winkelkonvention wie der Bau (``strecke.umgebung.lagematrix``),
Drehen mit Griff, Tasten und Strg+Rad, „Zur Strecke ausrichten“, Vorderseite je Modell korrigieren, Bodenbezug.
"""

import math
import sys

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mapeditor import umgebung
from mapeditor.window import MainWindow
from conftest import ROOT

HAUS = "eaid://environment_package/core_assets/buildings/tribune_a"
SCHIRM = "eaid://environment_package/core_assets/dressing/jumbotron_c"
BAUM = "eaid://environment_package/core_assets/speedtree/idf/st_oak/st_oak_a_05"

KATALOG = {"schema": 1, "spielordner": "G:/Spiel", "erzeugt": "2026-11-15T10:00:00", "modelle": [
    {"modell": HAUS, "name": "tribune_a", "familie": "tribune", "kategorie": "gebaeude", "groesse_m": [40, 12, 14],
     "radius_m": 21.2, "aabb_min": [-20, 0, -7], "aabb_max": [20, 12, 7], "quelle": "tracks/spa/wep/spa_common.erp",
     "im_traeger": False},
    {"modell": SCHIRM, "name": "jumbotron_c", "familie": "jumbotron", "kategorie": "gebaeude", "groesse_m": [8, 6, 1],
     "radius_m": 4.1, "aabb_min": [-4, 0.9, -0.5], "aabb_max": [4, 6.9, 0.5], "vorne_grad": 270.0, "vorne_quelle": "spiel",
     "quelle": "tracks/spa/wep/spa_common.erp", "im_traeger": False},
    {"modell": BAUM, "name": "st_oak_a_05", "familie": "st_oak", "kategorie": "baum", "groesse_m": [9, 14, 9],
     "radius_m": 4.5, "aabb_min": [-4.5, 0, -4.5], "aabb_max": [4.5, 14, 4.5], "quelle": "tracks/bahrain/wep/x.erp",
     "im_traeger": True}]}
INDEX = umgebung.katalog_index(KATALOG)


def _werkzeug():
    sys.path.insert(0, str(ROOT / "ego-werkzeuge"))
    try:
        from strecke import umgebung as werkzeug
    except ImportError:
        pytest.skip("Werkzeugkasten fehlt")
    return werkzeug


# ------------------------------------------------------------------ Konvention (gleich wie der Bau)
def test_grundriss_und_vorderseite_wie_im_bau():
    werkzeug = _werkzeug()
    for drehung in (0.0, 30.0, 90.0, 211.5):
        objekt = {"id": "o1", "modell": HAUS, "x": 12.0, "z": -40.0, "drehung_grad": drehung, "skala": 1.3}
        ecken = umgebung.grundriss(objekt, INDEX)
        soll = werkzeug.grundriss(objekt, INDEX[HAUS])
        assert [v for e in ecken for v in e] == pytest.approx([float(v) for e in soll for v in e], abs=1e-9)
        # Vorderpfeil = lokale Vorderseite durch die Lagematrix des Baus (Zeilenvektor) gedreht
        m = werkzeug.lagematrix(0, 0, 0, drehung, 1.0)
        lokal = umgebung.richtung(umgebung.vorderseite(SCHIRM, INDEX)[0])
        welt = (lokal[0] * m[0] + lokal[1] * m[8], lokal[0] * m[2] + lokal[1] * m[10])
        assert umgebung.richtung(umgebung.vorne_welt(dict(objekt, modell=SCHIRM), INDEX)) == pytest.approx(welt, abs=1e-9)
    assert umgebung.richtung(90) == pytest.approx((0.0, -1.0), abs=1e-12)          # 90° = oben in der Karte (−z)
    assert umgebung.vorderseite("eaid://x/neu", {"eaid://x/neu": {"aabb_min": [-1, 0, -5], "aabb_max": [1, 1, 5]}}) == (0.0, "annahme")
    assert umgebung.vorderseite(HAUS, INDEX) == werkzeug.vorderseite(HAUS, INDEX[HAUS])[:1] + ("annahme",)


def test_drehung_zur_strecke_und_naechster_punkt():
    linie = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
    assert umgebung.naechster_punkt(linie, 50.0, -20.0) == pytest.approx((50.0, 0.0))
    objekt = {"id": "o1", "modell": SCHIRM, "x": 50.0, "z": -20.0, "drehung_grad": 0.0, "skala": 1.0}
    drehung = umgebung.drehung_zur_strecke(objekt, INDEX, (50.0, 0.0))
    vorne = umgebung.richtung(umgebung.vorne_welt(dict(objekt, drehung_grad=drehung), INDEX))
    assert vorne == pytest.approx((0.0, 1.0), abs=1e-3)                              # zeigt nach +z zur Strecke
    korrektur = {SCHIRM: 0.0}
    drehung = umgebung.drehung_zur_strecke(objekt, INDEX, (50.0, 0.0), korrektur)
    assert umgebung.richtung(umgebung.vorne_welt(dict(objekt, drehung_grad=drehung), INDEX, korrektur)) == pytest.approx(
        (0.0, 1.0), abs=1e-3)


def test_datei_boden_und_vorderseiten():
    daten = umgebung.normalisieren({"schema": 1, "objekte": [
        {"id": "o1", "modell": HAUS, "x": 1, "z": 2, "boden": "ursprung"}], "vorderseiten": {HAUS: -90}})
    assert daten["vorderseiten"] == {HAUS: 270.0} and daten["objekte"][0]["boden"] == "ursprung"
    text = umgebung.als_text(daten)
    assert text.index('"vorderseiten"') < text.index('"objekte"')
    with pytest.raises(ValueError):
        umgebung.normalisieren({"schema": 1, "objekte": [{"id": "o1", "modell": HAUS, "x": 1, "z": 2, "boden": "x"}]})
    werkzeug = _werkzeug()
    assert werkzeug.normalisieren(umgebung.geordnet(daten))["vorderseiten"] == {HAUS: 270.0}


# ------------------------------------------------------------------ Bedienung
@pytest.fixture
def fenster(app, point_project):
    w = MainWindow(point_project, auto_preview=False)
    w.show()
    w.set_env_catalog(KATALOG)
    w.map.fit_track()
    QTest.qWait(25)
    yield w
    w.document.undo.setClean()
    w.close()


def _platzieren(w, modell=SCHIRM):
    pos = w.map.input["pos"]
    x, z = pos[0][0] + 60.0, pos[0][2] + 60.0
    objekt = w.document.env_place(modell, x, z)
    w.set_mode("umgebung")
    w.document.env_select({objekt["id"]}, objekt["id"])
    w.map.centerOn(x, z)
    QTest.qWait(10)
    return objekt["id"]


def _rad(w, schritte, modifiers):
    punkt = QPointF(w.map.viewport().rect().center())
    ereignis = QWheelEvent(punkt, w.map.viewport().mapToGlobal(punkt), QPoint(0, 0), QPoint(0, 120 * schritte),
                           Qt.NoButton, modifiers, Qt.NoScrollPhase, False)
    QApplication.sendEvent(w.map.viewport(), ereignis)


def test_drehen_mit_tasten_rad_und_griff(fenster):
    w = fenster
    kennung = _platzieren(w)
    vorher = w.map.grab().toImage()
    QTest.keyClick(w.map, Qt.Key_R)
    assert w.document.env_object(kennung)["drehung_grad"] == pytest.approx(15.0)
    assert w.map.grab().toImage() != vorher                                          # Grundriss/Pfeil gedreht
    QTest.keyClick(w.map, Qt.Key_R, Qt.ShiftModifier)
    assert w.document.env_object(kennung)["drehung_grad"] == pytest.approx(0.0)
    _rad(w, 2, Qt.ControlModifier)
    assert w.document.env_object(kennung)["drehung_grad"] == pytest.approx(10.0)
    _rad(w, -1, Qt.ControlModifier | Qt.ShiftModifier)
    assert w.document.env_object(kennung)["drehung_grad"] == pytest.approx(9.0)
    w.document.env_rotate({kennung: 0.0})
    # Griff an der Pfeilspitze um 90° gegen den Uhrzeigersinn um die Mitte ziehen (Umschalt rastet auf 15°)
    griff, mitte = w.map.env_turn_handle()
    r = math.hypot(griff.x() - mitte.x(), griff.y() - mitte.y())
    winkel0 = math.atan2(griff.y() - mitte.y(), griff.x() - mitte.x())
    vp = w.map.viewport()
    QTest.mousePress(vp, Qt.LeftButton, Qt.ShiftModifier, griff.toPoint())
    for k in range(1, 7):
        a = winkel0 - math.radians(90) * k / 6                                       # Bildschirm-y nach unten
        QTest.mouseMove(vp, QPoint(round(mitte.x() + r * math.cos(a)), round(mitte.y() + r * math.sin(a))), delay=5)
    assert w.document.env_object(kennung)["drehung_grad"] == 0.0                    # Entwurf bis zum Loslassen
    a = winkel0 - math.radians(90)
    QTest.mouseRelease(vp, Qt.LeftButton, Qt.ShiftModifier, QPoint(round(mitte.x() + r * math.cos(a)),
                                                                    round(mitte.y() + r * math.sin(a))))
    assert w.document.env_object(kennung)["drehung_grad"] == pytest.approx(90.0)
    w.document.undo.undo()
    assert w.document.env_object(kennung)["drehung_grad"] == pytest.approx(0.0)


def test_zur_strecke_ausrichten_und_vorderseite_korrigieren(fenster):
    w = fenster
    kennung = _platzieren(w)
    QTest.keyClick(w.map, Qt.Key_T)
    objekt = w.document.env_object(kennung)
    linie = [(p[0], p[2]) for p in w.map.input["pos"]]
    ziel = umgebung.naechster_punkt(linie, objekt["x"], objekt["z"])
    soll = umgebung.winkel(ziel[0] - objekt["x"], ziel[1] - objekt["z"])
    ist = umgebung.vorne_welt(objekt, w.map.env_index)
    assert abs((ist - soll + 180) % 360 - 180) < 0.2
    assert w.document.undo.undoText() == "Zur Strecke ausrichten"
    # Vorderseite des Modells +90°: Korrektur in umgebung.json, Anzeige folgt, ein Undo-Schritt
    panel = w.env_panel
    assert "aus den Spielstrecken" in panel.front_text.text()
    panel.front_turn.click()
    assert w.document.env["vorderseiten"] == {SCHIRM: 0.0}
    assert "in diesem Projekt festgelegt" in panel.front_text.text() and panel.front_reset.isEnabled()
    panel.front_reset.click()
    assert "vorderseiten" not in w.document.env
    w.document.undo.undo()
    assert w.document.env["vorderseiten"] == {SCHIRM: 0.0}
    # Bodenbezug je Objekt
    i = panel.ground_box.findData("unterkante")
    panel.ground_box.setCurrentIndex(i)
    panel.ground_box.activated.emit(i)
    assert w.document.env_object(kennung)["boden"] == "unterkante"


def test_pflanzen_ohne_pfeil_gebaeude_mit_grundriss(fenster):
    w = fenster
    assert w.map.env_shape({"id": "b", "modell": BAUM, "x": 0, "z": 0, "drehung_grad": 0, "skala": 1}) is None
    form = w.map.env_shape({"id": "h", "modell": HAUS, "x": 0, "z": 0, "drehung_grad": 90, "skala": 1})
    assert len(form["ecken"]) == 4 and form["reichweite"] == pytest.approx(7.0)    # Vorderseite +z (Annahme) über 7 m
