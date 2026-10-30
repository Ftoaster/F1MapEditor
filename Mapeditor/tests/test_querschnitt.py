"""Querschnitt bearbeiten (27.10.2026): Abschnitte für Fahrbahnbreite, Randstein, Grünstreifen, Auslauf, Barriere."""

import copy
import math

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from conftest import EXAMPLES
from mapeditor import querschnitt
from mapeditor.adapter import calculate_preview
from mapeditor.project import Project
from mapeditor.querschnitt import AUTO
from mapeditor.views import CrossSection
from mapeditor.window import MainWindow
from test_group_journeys import rubber


def test_randwinkel_panel_und_abschnitt(app):
    panel = querschnitt.CrossPanel()
    assert panel.fields()["rand_neigung"] is None
    panel.edge.setCurrentIndex(panel.edge.findData("strecke"))
    assert panel.fields()["rand_neigung"] == "strecke"
    panel.edge.setCurrentIndex(panel.edge.findData("eigen"))
    panel.edge_angle.setValue(4.5)
    assert panel.fields()["rand_neigung"] == 4.5
    raw = {}
    querschnitt.apply_section(raw, 10.0, 90.0, "links", panel.fields())
    assert raw["seitenraum"]["abschnitte"][0]["rand_neigung"] == 4.5
    assert querschnitt.effective(raw, 50.0, "links")["rand_neigung"] == 4.5
    panel.load({"rand_neigung": "strecke"})
    assert panel.edge.currentData() == "strecke"
    panel.load({"rand_neigung": -6.0})
    assert panel.edge.currentData() == "eigen" and panel.edge_angle.value() == -6.0
    panel.load({})
    assert panel.edge.currentData() is None


def test_randwinkel_zeigt_die_ansicht_sofort(app, example_preview, tmp_path):
    view = CrossSection()
    view.resize(500, 260)
    view.preview = example_preview
    i = len(example_preview["s"]) // 2
    view.station = example_preview["s"][i]
    angle = math.radians(example_preview["bank"][i])
    assert view._edge_rise(i, angle) == [0.0, 0.0]
    view.edge_input = {"wert": 10.0, "seite": "beide"}
    assert view._edge_rise(i, angle) == pytest.approx([math.tan(math.radians(10))] * 2)
    view.edge_input = {"wert": 10.0, "seite": "links"}
    assert view._edge_rise(i, angle)[1] == 0.0
    view.edge_input = {"wert": "strecke", "seite": "beide"}
    assert view._edge_rise(i, angle) == pytest.approx([math.tan(angle), -math.tan(angle)])
    view.edge_input = {"wert": 30.0, "seite": "beide"}
    assert 1.0 <= view._exaggeration() < 5.0                 # steiler Rand: Überhöhung passt sich an
    assert not view.grab().isNull()


def test_randwinkel_vorschau_folgt_dem_feld(app, tmp_path):
    w = MainWindow(Project.open(EXAMPLES / "teststrecke.json"), auto_preview=False)
    w.show()
    try:
        w.request_preview()
        QTest.qWait(50)
        for _ in range(600):
            if not w.job:
                break
            QTest.qWait(20)
        w.results.setCurrentWidget(w.cross_page)
        w.set_station(100.0)
        w.cross_range("cursor")
        panel = w.cross_panel
        assert w.cross.edge_input is None
        panel.edge.setCurrentIndex(panel.edge.findData("eigen"))
        panel.edge_angle.setValue(8.0)
        assert w.cross.edge_input == {"wert": 8.0, "seite": "beide"}      # ohne „Übernehmen“
        w.set_station(600.0)
        assert w.cross.edge_input is None and "außerhalb" in panel.info.text()
        w.set_station(100.0)
        assert w.cross.edge_input is not None
        panel.edge.setCurrentIndex(panel.edge.findData(None))
        assert w.cross.edge_input is None
    finally:
        w.document.undo.setClean()
        w.close()


def test_zusammenhaengender_lauf():
    span = querschnitt.contiguous_span
    assert span([3, 4, 5], 10, True) == (3, 5)
    assert span([5, 3, 4], 10, False) == (3, 5)
    assert span([3], 10, True) is None and span([], 10, True) is None           # ein Punkt ist kein Bereich
    assert span([3, 5], 10, True) is None                                       # Lücke
    assert span([9, 0, 1], 10, True) == (9, 1)                                  # über die Ziellinie
    assert span([9, 0, 1], 10, False) is None                                   # offene Strecke hat kein Weiterlaufen
    assert span([0, 2, 9], 10, True) is None
    assert span(range(10), 10, True) == (0, 9)


def test_auswahl_setzt_den_bereich(app, example_preview):
    project = Project.open(EXAMPLES / "teststrecke.json")
    project.manifest["versatz"] = [-591, 0, -473]
    w = MainWindow(project, auto_preview=False)
    w.resize(1400, 950)
    w.show()
    try:
        w.install_preview(example_preview)
        w.results.setCurrentWidget(w.cross_page)
        panel, stations = w.cross_panel, example_preview["object_s"]
        n = len(stations)
        w.document.select_many({2, 3, 4})                                       # Segment reicht bis zum nächsten
        assert (panel.von.value(), panel.bis.value()) == (round(stations[2]), round(stations[5]))
        assert panel.von.value() <= w.cross.station <= panel.bis.value()        # Ansicht zeigt den Bereich
        w.document.select_many({6, 9})                                          # Lücke: Bereich bleibt
        assert (panel.von.value(), panel.bis.value()) == (round(stations[2]), round(stations[5]))
        w.document.select_many({n - 2, n - 1, 0, 1})                            # über die Ziellinie
        assert (panel.von.value(), panel.bis.value()) == (round(stations[n - 2]), round(stations[2]))
        panel.edge.setCurrentIndex(panel.edge.findData("strecke"))              # Eingabe bleibt beim Wechsel erhalten
        w.document.select_many({6, 7})
        assert panel.edge.currentData() == "strecke" and panel.von.value() == round(stations[6])
        panel.edge.setCurrentIndex(panel.edge.findData(None))
        w.document.select_many(set())
        w.map.fit_track()                                                       # echter Rahmen mit der Maus
        QTest.qWait(25)
        ends = [w.map.input["segments"][i]["middle"] for i in (2, 3)]
        x, z = [e[0] for e in ends], [e[2] for e in ends]
        rubber(w, (min(x) - 60, min(z) - 60), (max(x) + 60, max(z) + 60))        # Start abseits der Linie
        chosen = sorted(w.document.selected_indices)
        span = querschnitt.contiguous_span(chosen, n, True)
        assert len(chosen) >= 2 and span is not None, chosen
        assert panel.von.value() == round(stations[span[0]])
        assert panel.bis.value() == round(stations[(span[1] + 1) % n])
    finally:
        w.document.undo.setClean()
        w.close()


def test_apply_replace_and_clear_sections():
    raw = {"fahrbahn": {"breite": 12.0}}
    querschnitt.apply_section(raw, 100.0, 300.0, "links", {"auslauf": "kies", "barriere_abstand": 30.0}, road_half=7.5)
    assert raw["seitenraum"]["abschnitte"] == [{"von_s": 100.0, "bis_s": 300.0, "seite": "links", "auslauf": "kies",
                                                "barriere_abstand": 30.0}]
    assert raw["fahrbahn"]["abschnitte"] == [{"von_s": 100.0, "bis_s": 300.0, "breite_links": 7.5}]
    querschnitt.apply_section(raw, 100.0, 300.0, "links", {"auslauf": "asphalt"})     # gleicher Bereich: ersetzt
    assert len(raw["seitenraum"]["abschnitte"]) == 1 and raw["seitenraum"]["abschnitte"][0]["auslauf"] == "asphalt"
    assert querschnitt.effective(raw, 200.0, "links") == {"auslauf": "asphalt"}
    assert querschnitt.effective(raw, 200.0, "rechts") == {}
    querschnitt.apply_section(raw, 4900.0, 50.0, "beide", {"randstein_breite": 0.0})    # über die Ziellinie
    assert querschnitt.sections_at(raw, 10.0, "rechts")[0][1]["randstein_breite"] == 0.0
    assert querschnitt.clear_range(raw, 150.0, 160.0, "links") == 2                    # Seitenraum + Breite
    assert raw["seitenraum"]["abschnitte"][0]["von_s"] == 4900.0 and raw["fahrbahn"]["abschnitte"] == []
    assert querschnitt.clear_range(raw, 0.0, 20.0, "beide") == 1 and "seitenraum" not in raw


def test_stations_and_input_s_round_trip():
    assert querschnitt.to_input_s(100.0, 800.0, 4903.0) == 900.0
    assert querschnitt.to_input_s(4500.0, 800.0, 4903.0) == 397.0
    assert abs(querschnitt.to_station(397.0, 800.0, 4903.0) - 4500.0) < 1e-9


def test_panel_writes_section_with_undo_and_generator_uses_it(app, example_preview):
    project = Project.open(EXAMPLES / "teststrecke.json")
    project.manifest["versatz"] = [-591, 0, -473]
    w = MainWindow(project, auto_preview=False)
    w.resize(1400, 950)
    w.show()
    try:
        assert not w.cross_panel.apply_button.isEnabled()              # ohne Vorschau kein Bearbeiten
        w.install_preview(example_preview)
        assert "s_start" in example_preview and w.cross_panel.apply_button.isEnabled()
        w.results.setCurrentWidget(w.cross_page)
        w.set_station(1000.0)
        w.cross_range("cursor")
        assert (w.cross_panel.von.value(), w.cross_panel.bis.value()) == (950.0, 1050.0)
        panel = w.cross_panel
        panel.side.setCurrentIndex(panel.side.findData("rechts"))
        panel.runoff.setCurrentIndex(panel.runoff.findData("asphalt"))
        panel.barrier.setValue(25.0)
        panel.kerb.setValue(0.0)
        assert panel.grass.value() == AUTO
        steps = w.document.undo.count()
        QTest.mouseClick(panel.apply_button, Qt.LeftButton)
        assert w.document.undo.count() == steps + 1
        sections = w.document.project.raw["seitenraum"]["abschnitte"]
        s0, length = example_preview["s_start"], example_preview["length"]
        assert sections == [{"von_s": querschnitt.to_input_s(950, s0, length), "bis_s": querschnitt.to_input_s(1050, s0, length),
                             "seite": "rechts", "randstein_breite": 0.0, "auslauf": "asphalt", "barriere_abstand": 25.0}]
        assert "Abschnitt 1" in panel.info.text()
        # Generator: Asphalt-Auslauf (Material 7) rechts im Bereich, links weiter Automatik
        raw = copy.deepcopy(w.document.project.raw)
        preview = calculate_preview({"raw": raw, "basis": str(project.basis), "shift": [-591, 0, -473]})
        i = min(range(len(preview["s"])), key=lambda k: abs(preview["s"][k] - 1000.0))
        right, left = preview["sides"]["rechts"], preview["sides"]["links"]
        assert right["bands"][i][4] == 7 and left["bands"][i][4] != 7
        assert right["distances"][i][3] - right["distances"][i][2] < 0.01         # kein Randstein
        QTest.mouseClick(panel.clear_button, Qt.LeftButton)
        assert "seitenraum" not in w.document.project.raw
        w.document.undo.undo()
        assert w.document.project.raw["seitenraum"]["abschnitte"] == sections
    finally:
        w.document.undo.setClean()
        w.close()
