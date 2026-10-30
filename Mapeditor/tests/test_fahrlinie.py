"""Fahrlinienlauf: Auswertung (Ideallinie, Kurven, Bremszonen, Tafeln, Box, Sektoren), Karte, Geschwindigkeit,
Auswertungsreiter, Zwischenspeicher und ziehbare Boxengasse."""

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from mapeditor.adapter import calculate_analysis, game_numbers
from mapeditor.project import Project
from mapeditor.window import MainWindow, lap_text
from conftest import EXAMPLES, doc_screenshot


@pytest.fixture(scope="session")
def example_analysis():
    project = Project.open(EXAMPLES / "teststrecke.json")
    return calculate_analysis({"raw": project.raw, "basis": str(project.basis), "shift": [-591, 0, -473]})


def test_game_numbers_merge_shortest_straight_like_the_build():
    # 17 Kurven, Gerade zwischen 3/4 am kürzesten, danach 9/10 → 15 Spielkurven
    kurven, i = [], 0
    for k in range(17):
        gap = 5 if k in (3, 9) else 100
        kurven.append({"i_anfang": i, "i_ende": i + 20})
        i += 20 + gap
    numbers = game_numbers(kurven, i + 50)
    assert max(numbers) == 15
    assert numbers[3] == numbers[4] and numbers[9] == numbers[10]
    assert numbers == sorted(numbers)
    assert game_numbers(kurven[:15], 5000) == list(range(1, 16))


def test_analysis_summary_contents(example_analysis):
    a = example_analysis["analysis"]
    assert "generator" in example_analysis["report"]["stufen"]
    assert 40 < a["rundenzeit_s"] < 200 and a["v_max_kmh"] > a["v_min_kmh"] > 0
    line = a["linie"]
    assert len(line["x"]) == len(line["z"]) == len(line["s"]) == len(line["v"]) == len(line["zustand"]) > 100
    assert set(line["zustand"]) <= {"b", "r", "g"} and "b" in line["zustand"]
    assert a["kurven"] and all(k["spiel"] >= 1 for k in a["kurven"])
    assert a["bremszonen"] and all(z["v_start"] > z["v_ende"] for z in a["bremszonen"])
    assert all(t["meter"] in (50, 100, 150, 200, 250) for t in a["tafeln"])
    assert [s["nummer"] for s in a["sektoren"]] == [1, 2, 3]
    assert len(a["startplaetze"]) == 22
    assert lap_text(104.74) == "1:44,7"


def test_window_shows_analysis_layers_speed_and_table(app, example_preview, example_analysis):
    project = Project.open(EXAMPLES / "teststrecke.json")
    project.manifest["versatz"] = [-591, 0, -473]
    w = MainWindow(project, auto_preview=False)
    w.resize(1500, 950)
    w.show()
    w.install_preview(example_preview)
    assert w.analysis is None and w.analysis_job is None          # ohne Automatik kein Generatorlauf
    w.store_analysis(w.current_key(), example_analysis)
    assert w.analysis is example_analysis["analysis"] and w.map.analysis is w.analysis
    assert "Fahrlinie aktuell" in w.map.analysis_note and "Runde" in w.map.analysis_note
    assert w.curve_table.rowCount() == len(w.analysis["kurven"])
    assert w.curve_table.item(0, 0).text().startswith("T")
    assert "Runde" in w.analysis_info.text()
    # Kurve anklicken → Cursor am Scheitel in Karte, Profilen und Geschwindigkeit
    w.results.setCurrentWidget(w.analysis_page)
    w._curve_clicked(1, 0)
    s = w.analysis["kurven"][1]["s_scheitel"]
    assert w.map.station == w.speed.station == w.height.station == s
    w.results.setCurrentWidget(w.speed)
    QTest.qWait(50)
    assert w.speed.speed_at(s) is not None
    QTest.mouseClick(w.speed, Qt.LeftButton, pos=w.speed.rect().center())
    assert abs(w.map.station - w.analysis["laenge_m"] / 2) < w.analysis["laenge_m"] * 0.05
    doc_screenshot(w.speed, "geschwindigkeit.png")
    w.map.fit_track()
    QTest.qWait(50)
    doc_screenshot(w, "fahrlinie_karte.png")
    # Änderung: Fahrlinie veraltet (gedimmt), Rückgängig: Zwischenspeicher passt wieder
    w.document.edit("Name", lambda raw, meta: raw.update(name="anders"))
    assert w.map.analysis_stale and w.speed.stale and "veraltet" in w.map.analysis_note
    w.document.undo.undo()
    w.cancel_preview()
    w.install_preview(example_preview)
    assert not w.map.analysis_stale and w.analysis_key == w.current_key()
    w.document.undo.setClean()
    w.close()


def test_pit_lane_zone_drag_sets_entry_and_exit(app, example_preview):
    project = Project.open(EXAMPLES / "teststrecke.json")
    project.raw.setdefault("boxengasse", {})["aktiv"] = True
    w = MainWindow(project, auto_preview=False)
    w.resize(1400, 900)
    w.show()
    w.install_preview(example_preview)
    zone = next(z for z in w.map.zones if z["art"] == "box")
    length = example_preview["length"]
    settings = w._pit_settings()
    assert abs(zone["a"] - (length - settings["einfahrt_vor_start_m"])) < 1e-6
    assert abs(zone["b"] - settings["ausfahrt_nach_start_m"]) < 1e-6
    before = project.raw["boxengasse"].get("ausfahrt_nach_start_m")
    w.category.setCurrentIndex(w.category.findData("boxengasse"))
    assert w.map.zone_selected == ("box", 0)
    w.map.fit_track()
    QTest.qWait(50)
    handles = dict(w.map.zone_handles())
    assert set(handles) == {"einfahrt_vor_start_m", "ausfahrt_nach_start_m"}
    # Ausfahrt-Griff auf eine Stelle weiter hinten ziehen
    target_s = 700.0
    p = example_preview["pos"][w.map.station_at(target_s)]
    target = w.map.mapFromScene(QPointF(p[0], p[2]))
    start = handles["ausfahrt_nach_start_m"].toPoint()
    QTest.mousePress(w.map.viewport(), Qt.LeftButton, pos=start)
    QTest.mouseMove(w.map.viewport(), target)
    QTest.mouseRelease(w.map.viewport(), Qt.LeftButton, pos=target)
    value = w.document.project.raw["boxengasse"]["ausfahrt_nach_start_m"]
    assert abs(value - target_s) < 40
    w.document.undo.undo()
    assert w.document.project.raw["boxengasse"].get("ausfahrt_nach_start_m") == before
    w.document.undo.setClean()
    w.close()
