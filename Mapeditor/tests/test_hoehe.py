"""Höhenprofil als Liniengraph der Eingabepunkte (25.10.2026): ziehen, Rahmen, Pfeiltasten, Rampe/Eben/Glätten."""

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from mapeditor.input_geometry import input_geometry
from mapeditor.project import Project, point_xyz
from mapeditor.window import MainWindow
from test_journeys import journey  # noqa: F401  (Fixture)


def hill_project():
    """Geschlossene Runde aus 8 Punkten mit verschiedenen Höhen."""
    import math
    points = [{"x": round(400 * math.cos(2 * math.pi * i / 8), 3), "y": float(i % 4),
               "z": round(250 * math.sin(2 * math.pi * i / 8), 3)} for i in range(8)]
    return Project({"name": "Hügel", "mittellinie": {"art": "kontrollpunkte", "punkte": points}},
                   {"schema": 1, "definition": "definition.json", "runde_geschlossen": True})


def open_window(app, project):
    w = MainWindow(project, auto_preview=False)
    w.resize(1400, 950)
    w.show()
    w.results.setCurrentWidget(w.height_page)
    QTest.qWait(40)
    w.height.repaint()
    return w


def node_pos(w, index):
    node = next(n for n in w.height.nodes() if n["index"] == index)
    p = w.height._xy(node["s"], node["y"])
    return QPoint(round(p.x()), round(p.y()))


def heights(w):
    return [point_xyz(p)[1] for p in w.document.points]


def test_nodes_follow_points(app):
    w = open_window(app, hill_project())
    try:
        nodes = w.height.nodes()
        assert [n["index"] for n in nodes] == list(range(8))
        assert [n["y"] for n in nodes] == heights(w)
        assert w.height.editable
    finally:
        w.document.undo.setClean()
        w.close()


def test_drag_single_point_one_undo_step(app):
    w = open_window(app, hill_project())
    try:
        before = heights(w)
        steps = w.document.undo.count()
        start = node_pos(w, 2)
        per_px = w.height._meters_per_px()
        QTest.mousePress(w.height, Qt.LeftButton, Qt.NoModifier, start)
        QTest.mouseMove(w.height, start + QPoint(0, -10))
        assert w.document.selected == 2                     # senkrecht gezogen: Punkt gewählt und gezogen
        QTest.mouseMove(w.height, start + QPoint(0, -40))
        QTest.mouseRelease(w.height, Qt.LeftButton, Qt.NoModifier, start + QPoint(0, -40))
        after = heights(w)
        assert w.document.undo.count() == steps + 1
        assert abs((after[2] - before[2]) - 40 * per_px) <= 0.11            # 0,1-m-Raster
        assert [a for i, a in enumerate(after) if i != 2] == [b for i, b in enumerate(before) if i != 2]
        w.document.undo.undo()
        assert heights(w) == before
    finally:
        w.document.undo.setClean()
        w.close()


def test_band_select_and_arrow_keys(app):
    w = open_window(app, hill_project())
    try:
        a, b = node_pos(w, 1), node_pos(w, 3)
        top = min(n.y() for n in (node_pos(w, i) for i in (1, 2, 3))) - 15
        bottom = max(n.y() for n in (node_pos(w, i) for i in (1, 2, 3))) + 15
        QTest.mousePress(w.height, Qt.LeftButton, Qt.NoModifier, QPoint(a.x() - 6, top))
        QTest.mouseMove(w.height, QPoint(b.x() + 6, bottom))
        QTest.mouseRelease(w.height, Qt.LeftButton, Qt.NoModifier, QPoint(b.x() + 6, bottom))
        assert w.document.selected_indices == {1, 2, 3}
        assert w.height.selected == {1, 2, 3}
        before = heights(w)
        w.height.setFocus()
        QTest.keyClick(w.height, Qt.Key_Up, Qt.ShiftModifier)
        after = heights(w)
        assert [round(after[i] - before[i], 3) for i in range(8)] == [0, 1, 1, 1, 0, 0, 0, 0]
        QTest.keyClick(w.height, Qt.Key_Down)
        assert round(heights(w)[2] - before[2], 3) == 0.9
    finally:
        w.document.undo.setClean()
        w.close()


def test_tools_ramp_level_smooth(app):
    w = open_window(app, hill_project())
    try:
        nodes = {n["index"]: n for n in w.height.nodes()}
        w.document.select_many({0, 1, 2, 3}, 0)
        w.height_tool("rampe")
        y = heights(w)
        a, b = nodes[0], nodes[3]
        for i in (1, 2):
            want = a["y"] + (b["y"] - a["y"]) * (nodes[i]["s"] - a["s"]) / (b["s"] - a["s"])
            assert abs(y[i] - want) < 1e-3
        assert y[0] == a["y"] and y[3] == b["y"]
        w.document.select_many({4, 5}, 4)
        w.height_tool("eben")
        y = heights(w)
        assert y[4] == y[5] == (nodes[4]["y"] + nodes[5]["y"]) / 2
        w.document.select_many({7}, 7)
        before = heights(w)
        w.height_tool("glaetten")
        assert abs(heights(w)[7] - (before[6] + 2 * before[7] + before[0]) / 4) < 1e-3
    finally:
        w.document.undo.setClean()
        w.close()


def test_segment_ends_pinned(journey):
    w = journey()
    w.results.setCurrentWidget(w.height_page)
    QTest.qWait(30)
    w.height.repaint()
    segments = w.document.ml["segmente"]
    nodes = w.height.nodes()
    assert len(nodes) == len(segments)                       # Generatorvorschau: ein Griff je Segmentende
    ends_before = [e["end"][1] for e in input_geometry(w.document)["segments"]]
    w.set_heights({2: ends_before[2] + 5})
    ends_after = [e["end"][1] for e in input_geometry(w.document)["segments"]]
    assert abs(ends_after[2] - ends_before[2] - 5) < 1e-3
    assert all(abs(a - b) < 2e-3 for i, (a, b) in enumerate(zip(ends_before, ends_after)) if i != 2)
    assert all("steigung_prozent" not in s for s in w.document.ml["segmente"])
    w.document.undo.undo()
    assert [e["end"][1] for e in input_geometry(w.document)["segments"]] == ends_before


def test_click_on_empty_profile_still_moves_cursor(app):
    w = open_window(app, hill_project())
    try:
        plot = w.height._frame[0]
        QTest.mouseClick(w.height, Qt.LeftButton, Qt.NoModifier, QPoint(int(plot.center().x()), int(plot.top() + 3)))
        assert 0 < w.height.station < w.height.preview["length"]
    finally:
        w.document.undo.setClean()
        w.close()


def test_wheel_zoom_and_dense_points(app):
    """Viele Punkte (Import, ~5 m Abstand): Mausrad zoomt waagerecht, Rahmen + Ziehen bleiben möglich."""
    import math
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QWheelEvent
    n = 600
    points = [{"x": round(900 * math.cos(2 * math.pi * i / n), 3), "y": round(3 * math.sin(6 * math.pi * i / n), 3),
               "z": round(500 * math.sin(2 * math.pi * i / n), 3)} for i in range(n)]
    w = open_window(app, Project({"name": "Dicht", "mittellinie": {"art": "kontrollpunkte", "punkte": points}},
                                 {"schema": 1, "definition": "definition.json", "runde_geschlossen": True}))
    try:
        view = w.height
        plot = view._frame[0]
        center = QPointF(plot.center().x(), plot.center().y())
        for _ in range(6):
            event = QWheelEvent(center, view.mapToGlobal(center), QPoint(0, 0), QPoint(0, 120), Qt.NoButton,
                                Qt.NoModifier, Qt.NoScrollPhase, False)
            view.wheelEvent(event)
        s0, s1 = view.view
        assert s1 - s0 < view.preview["length"] / 3 and s0 < view._station_at(center.x()) < s1
        view.repaint()
        a, b = node_pos(w, 300), node_pos(w, 310)
        QTest.mousePress(view, Qt.LeftButton, Qt.NoModifier, QPoint(min(a.x(), b.x()) - 2, int(plot.top()) + 2))
        QTest.mouseMove(view, QPoint(max(a.x(), b.x()) + 2, int(plot.bottom()) - 2))
        QTest.mouseRelease(view, Qt.LeftButton, Qt.NoModifier, QPoint(max(a.x(), b.x()) + 2, int(plot.bottom()) - 2))
        assert {300, 305, 310} <= w.document.selected_indices
        QTest.mouseDClick(view, Qt.LeftButton, Qt.NoModifier, QPoint(int(plot.left()) + 3, int(plot.top()) + 3))
        assert view.view is None
    finally:
        w.document.undo.setClean()
        w.close()


def test_horizontal_drag_on_point_selects_range(app):
    """Waagerechtes Ziehen – auch wenn es auf einem Punkt beginnt – wählt den Bereich A→B statt zu verschieben."""
    w = open_window(app, hill_project())
    try:
        before = heights(w)
        a, b = node_pos(w, 2), node_pos(w, 5)
        QTest.mousePress(w.height, Qt.LeftButton, Qt.NoModifier, a)
        QTest.mouseMove(w.height, a + QPoint(30, 0))
        QTest.mouseMove(w.height, QPoint(b.x() + 2, a.y() + 3))
        QTest.mouseRelease(w.height, Qt.LeftButton, Qt.NoModifier, QPoint(b.x() + 2, a.y() + 3))
        assert w.document.selected_indices == {2, 3, 4, 5}
        assert heights(w) == before                                    # nichts verschoben
        assert "A→B: 4 Punkte" in w.height_info.text()
        assert w.height_buttons["uebergang"].isEnabled()
        QTest.mouseClick(w.height, Qt.LeftButton, Qt.NoModifier, node_pos(w, 6))
        assert w.document.selected_indices == {6}
        assert not w.height_buttons["uebergang"].isEnabled()
    finally:
        w.document.undo.setClean()
        w.close()


def test_smooth_transition_s_curve():
    """Sprung 0 → 10 m: Übergang monoton, ohne Überschwingen, Endpunkte fest, flach an flachen Nachbarn."""
    from mapeditor.window import smooth_range, smooth_transition
    ordered = [{"index": i, "s": 10.0 * i, "y": 0.0 if i < 10 else 10.0} for i in range(20)]
    new = smooth_transition(ordered, 5, 14)
    ys = [0.0] + [new[i] for i in range(6, 14)] + [10.0]
    assert all(b >= a for a, b in zip(ys, ys[1:])) and 0 <= min(ys) and max(ys) <= 10
    steps = [b - a for a, b in zip(ys, ys[1:])]
    assert steps[0] < steps[len(steps) // 2] and steps[-1] < steps[len(steps) // 2]   # S-Form: Enden flach
    assert max(steps) < 10 / 9 * 1.6                                                  # kein Sprung mehr
    smooth = smooth_range(ordered, 5, 14)
    assert set(smooth) == set(range(6, 14)) and all(0 <= v <= 10 for v in smooth.values())


def test_transition_button_one_undo_step(app):
    w = open_window(app, hill_project())
    try:
        w.document.select_many({1, 2, 3, 4, 5}, 1)
        steps = w.document.undo.count()
        QTest.mouseClick(w.height_buttons["uebergang"], Qt.LeftButton)
        assert w.document.undo.count() == steps + 1
        y = heights(w)
        lo, hi = sorted((y[1], y[5]))
        assert all(lo - 1e-6 <= y[i] <= hi + 1e-6 for i in (2, 3, 4))
    finally:
        w.document.undo.setClean()
        w.close()
