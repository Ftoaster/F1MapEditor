"""Querneigungsprofil mit denselben Werkzeugen wie das Höhenprofil (26.10.2026): Griffe ziehen, Bereich A→B,
Pfeiltasten, Übergang/Rampe/Gleicher Wert/Glätten, Segmente in ihrer Mitte."""

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from mapeditor import neigung
from mapeditor.window import MainWindow
from test_hoehe import hill_project
from test_journeys import journey  # noqa: F401  (Fixture)


def open_window(app, project):
    w = MainWindow(project, auto_preview=False)
    w.resize(1400, 950)
    w.show()
    w.results.setCurrentWidget(w.bank_page)
    QTest.qWait(40)
    w.bank.repaint()
    return w


def banks(w):
    return neigung.current_values(w.document.project.raw, w.document.project.closed)


def node_pos(w, index):
    node = next(n for n in w.bank.nodes() if n["index"] == index)
    p = w.bank._xy(node["s"], node["y"])
    return QPoint(round(p.x()), round(p.y()))


def test_nodes_and_drag_one_undo_step(app):
    w = open_window(app, hill_project())
    try:
        nodes = w.bank.nodes()
        assert [n["index"] for n in nodes] == list(range(8)) and [n["y"] for n in nodes] == banks(w)
        assert w.bank.editable
        before, steps = banks(w), w.document.undo.count()
        start = node_pos(w, 2)
        per_px = w.bank._meters_per_px()                      # hier: Grad je Pixel
        QTest.mousePress(w.bank, Qt.LeftButton, Qt.NoModifier, start)
        QTest.mouseMove(w.bank, start + QPoint(0, -10))
        assert w.document.selected == 2
        QTest.mouseMove(w.bank, start + QPoint(0, -40))
        QTest.mouseRelease(w.bank, Qt.LeftButton, Qt.NoModifier, start + QPoint(0, -40))
        after = banks(w)
        assert w.document.undo.count() == steps + 1
        assert abs((after[2] - before[2]) - 40 * per_px) <= 0.11 and after[2] > 0
        assert all(abs(a - b) < 1e-9 for i, (a, b) in enumerate(zip(after, before)) if i != 2)   # Nachbarn bleiben
        assert w.document.undo.undoText().startswith("Querneigung")
        w.document.undo.undo()
        assert banks(w) == before
    finally:
        w.document.undo.setClean()
        w.close()


def test_band_select_arrow_keys_and_bar(app):
    w = open_window(app, hill_project())
    try:
        a, b = node_pos(w, 1), node_pos(w, 3)
        y = a.y()
        QTest.mousePress(w.bank, Qt.LeftButton, Qt.NoModifier, QPoint(a.x() - 6, y - 20))
        QTest.mouseMove(w.bank, QPoint(b.x() + 6, y - 18))
        QTest.mouseRelease(w.bank, Qt.LeftButton, Qt.NoModifier, QPoint(b.x() + 6, y - 18))
        assert w.document.selected_indices == {1, 2, 3} and w.bank.selected == {1, 2, 3}
        assert w.height.selected == {1, 2, 3}                 # Auswahl gilt in beiden Profilen
        assert "A→B: 3 Punkte" in w.bank_info.text()
        assert w.bank_buttons["rampe"].isEnabled() and w.bank_buttons["eben"].isEnabled()
        before = banks(w)
        w.bank.setFocus()
        QTest.keyClick(w.bank, Qt.Key_Up, Qt.ShiftModifier)
        QTest.keyClick(w.bank, Qt.Key_Down)
        after = banks(w)
        assert [round(after[i] - before[i], 3) for i in range(8)] == [0, 0.9, 0.9, 0.9, 0, 0, 0, 0]
    finally:
        w.document.undo.setClean()
        w.close()


def test_tools_transition_ramp_level_smooth(app):
    w = open_window(app, hill_project())
    try:
        w.set_banks({0: 0.0, 1: 4.0, 2: -2.0, 3: 6.0, 4: 1.0, 5: 5.0})
        nodes = {n["index"]: n for n in w.bank.nodes()}
        w.document.select_many({0, 1, 2, 3}, 0)
        QTest.mouseClick(w.bank_buttons["rampe"], Qt.LeftButton)
        v = banks(w)
        a, b = nodes[0], nodes[3]
        for i in (1, 2):
            want = a["y"] + (b["y"] - a["y"]) * (nodes[i]["s"] - a["s"]) / (b["s"] - a["s"])
            assert abs(v[i] - want) < 0.01
        w.document.select_many({4, 5}, 4)
        QTest.mouseClick(w.bank_buttons["eben"], Qt.LeftButton)
        assert banks(w)[4] == banks(w)[5] == 3.0
        w.set_banks({1: 4.0, 2: -2.0, 3: 6.0})
        w.document.select_many({0, 1, 2, 3, 4}, 0)
        steps = w.document.undo.count()
        QTest.mouseClick(w.bank_buttons["glaetten"], Qt.LeftButton)
        v = banks(w)
        assert w.document.undo.count() == steps + 1 and max(v[1:4]) - min(v[1:4]) < 8.0 - 1.0   # Sprünge kleiner
        w.set_banks({1: 0.0, 2: 0.0, 3: 0.0, 5: 6.0})
        w.document.select_many({1, 2, 3, 4, 5}, 1)
        QTest.mouseClick(w.bank_buttons["uebergang"], Qt.LeftButton)
        v = banks(w)
        assert all(-1e-6 <= v[i] <= 6.0 + 1e-6 for i in (2, 3, 4)) and v[2] <= v[3] <= v[4]
        w.set_banks({0: 99.0})
        assert banks(w)[0] == neigung.MAX_GRAD                # Grenze wie im Querneigungsfeld
    finally:
        w.document.undo.setClean()
        w.close()


def test_segments_nodes_in_segment_middle(journey):  # noqa: F811
    w = journey()
    w.results.setCurrentWidget(w.bank_page)
    QTest.qWait(30)
    w.bank.repaint()
    segments = w.document.ml["segmente"]
    nodes = w.bank.nodes()
    assert len(nodes) == len(segments)
    data = w.bank.preview
    s0, s1 = data["object_s"][0], data["object_s"][1]
    first = next(n for n in nodes if n["index"] == 0)
    assert abs(first["s"] - (s0 + s1) / 2) < 1e-6 or s1 < s0
    w.set_banks({1: 3.5})
    assert w.document.ml["segmente"][1]["querneigung_grad"] == 3.5
    assert next(n for n in w.bank.nodes() if n["index"] == 1)["y"] == 3.5     # Griff sofort aktuell
    w.document.undo.undo()
    assert "querneigung_grad" not in w.document.ml["segmente"][1] or \
        w.document.ml["segmente"][1]["querneigung_grad"] != 3.5


def test_bank_tab_feature_toggles_page(app):
    w = open_window(app, hill_project())
    try:
        w.apply_feature("reiter_neigung", False, save=False)
        assert not w.results.isTabVisible(w.results.indexOf(w.bank_page))
        w.apply_feature("reiter_neigung", True, save=False)
        assert w.results.isTabVisible(w.results.indexOf(w.bank_page))
    finally:
        w.document.undo.setClean()
        w.close()
