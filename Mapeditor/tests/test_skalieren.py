"""Strecke skalieren: um einen Faktor oder auf eine Ziellänge."""

import copy
import math

import pytest
from PySide6.QtWidgets import QDialog

from mapeditor import skalieren
from mapeditor.document import Document
from mapeditor.input_geometry import input_geometry
from mapeditor.project import Project, point_xyz
from mapeditor.window import MainWindow


def quadrat(geschlossen=True):
    """100 m × 100 m, Mitte (50, 50), Höhen 0…3 m, ein Punkt in Listenform."""
    punkte = [{"x": 0, "y": 0, "z": 0, "breite": 12}, {"x": 100, "y": 1, "z": 0}, [100, 100, 2], {"x": 0, "y": 3, "z": 100}]
    raw = {"name": "Q", "mittellinie": {"art": "kontrollpunkte", "punkte": punkte},
           "fahrbahn": {"breite": 12, "abschnitte": [{"von_s": 10, "bis_s": 50, "breite": 14}]},
           "seitenraum": {"abschnitte": [{"von_s": 100, "bis_s": 150, "seite": "links"}]},
           "start_ziel": {"s": 20}, "drs": {"zonen": [{"erkennung_m": 5, "aktivierung_m": 30, "ende_m": 90}]},
           "x_mode": {"zonen": [{"von_m": 200, "bis_m": 300, "partial_versatz_m": None}]},
           "boxengasse": {"einfahrt_vor_start_m": 40, "ausfahrt_nach_start_m": 60, "breite": 12}}
    return Project(raw, {"schema": 1, "definition": "definition.json", "runde_geschlossen": geschlossen})


def test_punkte_um_die_mitte_hoehen_und_breiten_bleiben():
    p = quadrat()
    raw = copy.deepcopy(p.raw)
    env = {"objekte": [{"id": "o1", "modell": "m", "x": 150.0, "z": 50.0, "drehung_grad": 30, "skala": 1.0}],
           "gelaende": {"huegel": [{"id": "h1", "x": 50.0, "z": 250.0, "hoehe_m": 20, "radius_m": 80}]}}
    pos = [point_xyz(q) for q in raw["mittellinie"]["punkte"]]
    assert skalieren.anwenden(raw, env, pos, 2.0) == (50, 50)
    punkte = [point_xyz(q) for q in raw["mittellinie"]["punkte"]]
    assert [(x, z) for x, _, z in punkte] == [(-50, -50), (150, -50), (150, 150), (-50, 150)]
    assert [y for _, y, _ in punkte] == [0, 1, 2, 3]                       # Höhen bleiben
    assert isinstance(raw["mittellinie"]["punkte"][2], list)                # Listenform bleibt
    assert raw["mittellinie"]["punkte"][0]["breite"] == 12 and raw["fahrbahn"]["breite"] == 12
    assert raw["fahrbahn"]["abschnitte"][0] == {"von_s": 20, "bis_s": 100, "breite": 14}
    assert raw["seitenraum"]["abschnitte"][0]["von_s"] == 200 and raw["start_ziel"]["s"] == 40
    assert raw["drs"]["zonen"][0] == {"erkennung_m": 10, "aktivierung_m": 60, "ende_m": 180}
    assert raw["x_mode"]["zonen"][0] == {"von_m": 400, "bis_m": 600, "partial_versatz_m": None}
    assert raw["boxengasse"] == {"einfahrt_vor_start_m": 80, "ausfahrt_nach_start_m": 120, "breite": 12}
    o, h = env["objekte"][0], env["gelaende"]["huegel"][0]
    assert (o["x"], o["z"], o["drehung_grad"], o["skala"]) == (250, 50, 30, 1.0)  # Lage mit, Größe nicht
    assert (h["x"], h["z"], h["hoehe_m"], h["radius_m"]) == (50, 450, 20, 80)


def test_segmente_laengen_radien_steigung():
    raw = {"mittellinie": {"art": "segmente", "start": {"x": 0, "z": 0, "richtung_grad": 0, "hoehe": 5},
                           "segmente": [{"typ": "gerade", "laenge": 200, "steigung_prozent": 2, "uebergang": {"ende": 30}},
                                        {"typ": "bogen", "radius": 50, "winkel": 90, "richtung": "links", "hoehe_ende": 7},
                                        {"typ": "bogen", "radius": 40, "laenge": 60, "richtung": "rechts"},
                                        {"typ": "klothoide", "laenge": 40, "radius_anfang": 80, "radius_ende": None,
                                         "richtung": "links"}],
                           "uebergaenge": {"aktiv": True, "a_faktor": .5}}}
    skalieren.anwenden(raw, None, [(0, 0, 0), (200, 0, 0)], 0.5)            # Mitte (100, 0)
    ml = raw["mittellinie"]
    assert (ml["start"]["x"], ml["start"]["z"], ml["start"]["hoehe"], ml["start"]["richtung_grad"]) == (50, 0, 5, 0)
    g, b, b2, k = ml["segmente"]
    assert g == {"typ": "gerade", "laenge": 100, "steigung_prozent": 4, "uebergang": {"ende": 15}}
    assert (b["radius"], b["winkel"], b["hoehe_ende"]) == (25, 90, 7)
    assert (b2["radius"], b2["laenge"]) == (20, 30)
    assert (k["laenge"], k["radius_anfang"], k["radius_ende"]) == (20, 40, None)
    assert ml["uebergaenge"] == {"aktiv": True, "a_faktor": .5, "min_laenge_m": 10, "max_laenge_m": 75}


def test_faktor_grenzen():
    with pytest.raises(ValueError, match="Faktor"):
        skalieren.faktor_pruefen(0)
    with pytest.raises(ValueError, match="Faktor"):
        skalieren.faktor_pruefen(20)


def test_segmentstrecke_laenge_genau(app):
    d = Document(Project({"name": "S", "mittellinie": {"art": "segmente", "start": {"x": 0, "z": 0, "richtung_grad": 0},
                                                       "segmente": [{"typ": "gerade", "laenge": 300},
                                                                    {"typ": "bogen", "radius": 100, "winkel": 180, "richtung": "links"},
                                                                    {"typ": "gerade", "laenge": 300},
                                                                    {"typ": "bogen", "radius": 100, "winkel": 180, "richtung": "links"}]}},
                         {"schema": 1, "definition": "definition.json", "runde_geschlossen": True}))
    vorher = input_geometry(d)["length"]
    d.scale_track(1500 / vorher)
    assert input_geometry(d)["length"] == pytest.approx(1500, abs=0.5)


def test_dokument_ein_undo_schritt_mit_umgebung(app):
    p = quadrat()
    p.umgebung = {"schema": 1, "objekte": [{"id": "o1", "modell": "m", "x": 150.0, "z": 50.0}]}
    d = Document(p)
    vorher = copy.deepcopy((d.project.raw, d.project.umgebung))
    d.scale_track(0.5)
    assert point_xyz(d.points[1])[0] == 75 and d.project.umgebung["objekte"][0]["x"] == 100
    assert d.undo.count() == 1
    d.undo.undo()
    assert (d.project.raw, d.project.umgebung) == vorher


def test_dialog_ziellaenge_und_faktor(app):
    dialog = skalieren.SkalierenDialog(4000)
    assert dialog.wert() == pytest.approx(1.1)
    dialog.ziel.setValue(5000)
    assert dialog.nach_laenge.isChecked() and dialog.wert() == pytest.approx(1.25)
    assert "5.000" in dialog.ergebnis.text()
    dialog.faktor.setValue(0.5)
    assert dialog.nach_faktor.isChecked() and dialog.wert() == pytest.approx(0.5)


def test_menue_skaliert_auf_ziellaenge(app, monkeypatch):
    w = MainWindow(quadrat(), auto_preview=False)
    w.show()
    try:
        laenge, aus_vorschau = w.track_length()
        assert laenge == pytest.approx(400, abs=0.1) and not aus_vorschau   # 3D: Höhen 0…3 m

        def ausfuehren(self):
            self.ziel.setValue(600)
            return QDialog.Accepted
        monkeypatch.setattr(skalieren.SkalierenDialog, "exec", ausfuehren)
        aktion = next(a for a in w.menuBar().actions()[1].menu().actions() if a.text() == "Strecke skalieren …")
        aktion.trigger()
        assert input_geometry(w.document)["length"] == pytest.approx(600, abs=0.1)
        assert "Faktor 1.500" in w.status_label.text() and w.map.mode == "auswahl"
        w.document.undo.undo()
        assert input_geometry(w.document)["length"] == pytest.approx(400, abs=0.1)
    finally:
        w.document.undo.setClean()
        w.close()


def test_knopf_unter_eigenschaften_fahrbahn(app, monkeypatch):
    w = MainWindow(quadrat(), auto_preview=False)
    w.show()
    try:
        w.category.setCurrentIndex(w.category.findData("fahrbahn"))
        assert w.scale_button.isVisible() and w.scale_button.isEnabled()
        w.category.setCurrentIndex(w.category.findData("randstein"))
        assert not w.scale_button.isVisible()
        w.category.setCurrentIndex(w.category.findData("fahrbahn"))

        def ausfuehren(self):
            self.faktor.setValue(0.5)
            return QDialog.Accepted
        monkeypatch.setattr(skalieren.SkalierenDialog, "exec", ausfuehren)
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        QTest.mouseClick(w.scale_button, Qt.LeftButton)
        assert input_geometry(w.document)["length"] == pytest.approx(200, abs=0.2)   # Höhen bleiben (3D)
    finally:
        w.document.undo.setClean()
        w.close()
