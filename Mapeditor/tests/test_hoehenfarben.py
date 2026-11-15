"""Höhe als Farbe und Mauskopplung von Karte und Profil (die geteilte Ansicht ist seit 15.11.2026 entfernt)."""

import math

from PySide6.QtGui import QColor
from PySide6.QtTest import QTest

from mapeditor import hoehenfarbe
from mapeditor.adapter import calculate_preview
from mapeditor.project import Project
from mapeditor.window import MainWindow


def huegel_projekt():
    punkte = []
    for i in range(48):
        t = 2 * math.pi * i / 48
        punkte.append({"x": round(500 * math.cos(t), 2), "z": round(300 * math.sin(t), 2),
                       "y": round(12 * math.sin(2 * t), 2)})
    return Project({"name": "Hügel", "mittellinie": {"art": "kontrollpunkte", "punkte": punkte}},
                   {"schema": 1, "definition": "definition.json", "runde_geschlossen": True})


def fenster(app):
    p = huegel_projekt()
    w = MainWindow(p, auto_preview=False)
    w.resize(1400, 950)
    w.show()
    w.install_preview(calculate_preview({"raw": p.raw, "basis": str(p.basis), "shift": [0, 0, 0]}))
    QTest.qWait(30)
    return w


def schliessen(w):
    w.document.undo.setClean()
    w.close()


def test_farbskala():
    assert hoehenfarbe.stufe(-10, -10, 10) == 0 and hoehenfarbe.stufe(10, -10, 10) == hoehenfarbe.STUFEN - 1
    tief, hoch = hoehenfarbe.farbe(-10, -10, 10), hoehenfarbe.farbe(10, -10, 10)
    assert tief.blue() > tief.red() and hoch.red() > hoch.blue()           # blau tief, rot hoch
    assert hoehenfarbe.bereich({"pos": [[0, 5, 0], [1, 5, 0]]}) == (4.5, 5.5)  # flach: kein Teilen durch 0


def test_hoehenfarben_ohne_geteilte_ansicht(app):
    w = fenster(app)
    try:
        w.apply_feature("hoehenfarben", True, save=False)
        assert w.map.height_colors and w.height.height_colors
        farben = {item[2].name() for item in w.map._preview_items if isinstance(item[2], QColor)}
        assert len(farben) > 5                                              # Fahrbahn in vielen Farbstufen
        w.apply_feature("hoehenfarben", False, save=False)
        assert not w.map.height_colors
    finally:
        schliessen(w)


def test_maus_markiert_dieselbe_stelle(app):
    w = fenster(app)
    try:
        w.results.setCurrentWidget(w.height_page)
        QTest.qWait(30)
        w.height.hover_changed.emit(300.0)
        assert w.map.hover_station == 300.0
        w.map.hover_changed.emit(450.0)
        assert w.height.hover_station == 450.0
        w.map.hover_changed.emit(None)
        assert w.height.hover_station is None
        w.map.fit_track()
        QTest.qWait(30)
        p = w.preview["pos"][w.map.station_at(450.0)]
        w.map._track_hover(w.map.mapFromScene(p[0], p[2]).toPointF())     # Maus direkt über der Strecke
        assert abs(w.height.hover_station - 450.0) < 15
    finally:
        schliessen(w)


def test_farbe_nach_steigung(app):
    w = fenster(app)
    try:
        w.apply_feature("hoehenfarben", True, save=False)
        QTest.qWait(30)
        assert not w.color_mode_box.isHidden()
        hoehe = {item[2].name() for item in w.map._preview_items if isinstance(item[2], QColor)}
        w.set_color_mode("steigung")
        assert w.map.color_mode == "steigung" and w.height.color_mode == "steigung"
        steigung = {item[2].name() for item in w.map._preview_items if isinstance(item[2], QColor)}
        assert steigung and steigung != hoehe
        werte = hoehenfarbe.werte(w.preview, "steigung")
        assert min(werte) >= 0 and len(werte) == len(w.preview["pos"])
        assert hoehenfarbe.bereich(w.preview, "steigung") == (0.0, hoehenfarbe.STEIGUNG_MAX)
        w.apply_feature("hoehenfarben", False, save=False)
        assert w.color_mode_box.isHidden() and not w.map.height_colors
    finally:
        schliessen(w)


def test_steigung_ohne_vorschauwerte():
    data = {"pos": [[0, 0, 0], [10, 1, 0], [20, 2, 0], [30, 2, 0]], "s": [0, 10, 20, 30]}
    assert hoehenfarbe.steigungen(data) == [10.0, 10.0, 5.0, 0.0]


def test_mauskopplung_mit_normalen_reitern(app):
    w = fenster(app)
    try:
        w.results.setCurrentWidget(w.height_page)
        w.vertical_split.setSizes([500, 300])
        w._update_track_hover()
        assert w.map.track_hover                                            # V9: auch ohne geteilte Ansicht
        w.map.hover_changed.emit(200.0)
        assert w.height.hover_station == 200.0 and w.bank.hover_station == 200.0
        w.bank.hover_changed.emit(350.0)
        assert w.map.hover_station == 350.0
        w.results.setCurrentWidget(w.speed)
        assert not w.map.track_hover and w.height.hover_station is None
    finally:
        schliessen(w)
