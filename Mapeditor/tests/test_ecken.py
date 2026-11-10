"""Eckpunkte mit Radius und „Align to line“ (Feedback U3: gerade Linien, 90°-Ecken, Rundenschluss ohne Bogen)."""

import math

import pytest

from mapeditor.document import Document
from mapeditor.project import Project, point_xyz
from mapeditor.window import MainWindow, parse_value


def projekt(punkte, geschlossen=True):
    return Project({"name": "Test", "mittellinie": {"art": "kontrollpunkte", "punkte": punkte}},
                   {"schema": 1, "definition": "definition.json", "runde_geschlossen": geschlossen})


QUADRAT = [{"x": 0.0, "y": 0.0, "z": 0.0}, [300.0, 0.0], {"x": 300.0, "y": 0.0, "z": 300.0}, [0.0, 300.0, 2.0]]


def test_ecken_setzen_und_entfernen(app):
    d = Document(projekt([dict(p) if isinstance(p, dict) else list(p) for p in QUADRAT]))
    d.select_many({1, 3})
    assert d.set_corners(15.0) == 2
    assert d.points[1] == {"x": 300.0, "y": 0.0, "z": 0.0, "ecke_radius_m": 15.0}      # Listenpunkt wird Objekt
    assert d.points[3]["y"] == 2.0 and d.points[3]["ecke_radius_m"] == 15.0
    assert "ecke_radius_m" not in d.points[0]
    d.set_corners(None)
    assert "ecke_radius_m" not in d.points[1]
    d.undo.undo()
    assert d.points[1]["ecke_radius_m"] == 15.0
    d.undo.undo()
    assert d.points[1] == [300.0, 0.0]


def test_ecken_fehler(app):
    d = Document(projekt([dict(p) if isinstance(p, dict) else list(p) for p in QUADRAT]))
    with pytest.raises(ValueError):
        d.set_corners(15.0)                             # nichts gewählt
    d.select_many({0})
    with pytest.raises(ValueError):
        d.set_corners(0)
    with pytest.raises(ValueError):
        parse_value("-3", None, "ecke_radius_m")
    assert parse_value("", None, "ecke_radius_m") is None                 # leer = rund
    assert parse_value("12,5", None, "ecke_radius_m") == 12.5


def test_auf_linie_ausrichten_behaelt_reihenfolge_und_abstaende(app):
    punkte = [{"x": 0.0, "y": 1.0, "z": 0.0}, {"x": 100.0, "y": 2.0, "z": 7.0}, {"x": 200.0, "y": 3.0, "z": -4.0},
              {"x": 300.0, "y": 4.0, "z": 0.0}, {"x": 300.0, "y": 0.0, "z": 300.0}, {"x": 0.0, "y": 0.0, "z": 300.0}]
    d = Document(projekt(punkte))
    d.select_many({0, 1, 2, 3})
    assert d.align_to_line() == 2
    xs = [point_xyz(p) for p in d.points[:4]]
    assert all(z == 0 for _, _, z in xs)
    assert [y for _, y, _ in xs] == [1.0, 2.0, 3.0, 4.0]                  # Höhen bleiben
    assert xs[0][0] < xs[1][0] < xs[2][0] < xs[3][0]
    assert xs[1][0] == pytest.approx(300 * math.hypot(100, 7) / (math.hypot(100, 7) + math.hypot(100, 11)
                                                                 + math.hypot(100, 4)), abs=1e-3)
    d.undo.undo()
    assert d.points[1]["z"] == 7.0


def test_auf_linie_ausrichten_ueber_rundenschluss_und_fehler(app):
    punkte = [{"x": 0.0, "z": 3.0}, {"x": 300.0, "z": 0.0}, {"x": 300.0, "z": 300.0}, {"x": 0.0, "z": 300.0},
              {"x": -5.0, "z": 150.0}]
    d = Document(projekt(punkte))
    d.select_many({3, 4, 0})                                              # Schlussstück 4 → 1 über den Rundenschluss
    assert d.align_to_line() == 1
    assert (d.points[4]["x"], d.points[4]["z"]) == (pytest.approx(0.0, abs=1e-3), pytest.approx(150.0, abs=0.1))
    d.select_many({0, 2})
    with pytest.raises(ValueError):
        d.align_to_line()                                                  # Lücke
    d.select_many(set(range(5)))
    with pytest.raises(ValueError):
        d.align_to_line()                                                  # ganze Runde


def test_eckpunkte_eckig_gezeichnet_und_im_eigenschaftsfeld(app):
    w = MainWindow(projekt([{"x": 0.0, "z": 0.0, "ecke_radius_m": 20.0}, {"x": 300.0, "z": 0.0},
                            {"x": 300.0, "z": 300.0}, {"x": 0.0, "z": 300.0}]), auto_preview=False)
    try:
        assert w.map.corner_indices() == {0}
        w.document.select(0)
        w._refresh_properties()
        keys = [entry[0] for entry in w.properties.entries]
        assert "ecke_radius_m" in keys
        w.document.select_many({1, 2})
        w.make_corner()                                    # ohne gewählte Ecke: Standardradius
        assert w.document.points[1]["ecke_radius_m"] == Document.ECKE_RADIUS_M
        w.document.select_many({0, 3})
        w.make_corner()                                    # Radius der gewählten Ecke übernehmen
        assert w.document.points[3]["ecke_radius_m"] == 20.0
        assert w.map.corner_indices() == {0, 1, 2, 3}
    finally:
        w.document.undo.setClean()
        w.close()
