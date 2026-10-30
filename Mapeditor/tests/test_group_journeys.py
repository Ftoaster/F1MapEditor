"""AP26: Gruppen ausschließlich mit echten QTest-Maus-/Tastaturereignissen bedienen."""

import copy

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest

from mapeditor.project import Project, point_xyz
from mapeditor.window import MainWindow
from conftest import EXAMPLES, doc_screenshot
from test_journeys import current_preview, edit_field, undo


@pytest.fixture
def group_window(app, point_project):
    w = MainWindow(point_project, auto_preview=False)
    w.show()
    QTest.qWait(25)
    w.map.fit_track()
    yield w
    w.document.undo.setClean()
    w.close()


def pixel(w, x, z):
    return w.map.mapFromScene(QPointF(x, z))


def rubber(w, a, b, modifiers=Qt.NoModifier):
    start, end = pixel(w, *a), pixel(w, *b)
    assert w.map.viewport().rect().contains(start)
    assert w.map.viewport().rect().contains(end)
    QTest.mousePress(w.map.viewport(), Qt.LeftButton, modifiers, start)
    QTest.mouseMove(w.map.viewport(), end, delay=15)
    QTest.mouseRelease(w.map.viewport(), Qt.LeftButton, modifiers, end)


def selected(w, indices):
    assert w.document.selected_indices == set(indices)
    assert {w.objects.row(item) for item in w.objects.selectedItems()} == set(indices)


def click_point(w, i, modifiers=Qt.NoModifier):
    point = w.map.object_positions()[i]
    QTest.mouseClick(w.map.viewport(), Qt.LeftButton, modifiers, pixel(w, point[0], point[2]))


def test_rubber_group_live_drag_undo_and_auto_preview(group_window):
    w = group_window
    before = copy.deepcopy(w.document.project.raw)
    rubber(w, (-20, -20), (220, 20))
    selected(w, {0, 1})
    assert w.document.undo.count() == 0
    start = pixel(w, 0, 0) + QPoint(3, -2)
    end = start + QPoint(21, 14)
    vector = w.map.mapToScene(end) - w.map.mapToScene(start)
    QTest.mousePress(w.map.viewport(), Qt.LeftButton, pos=start)
    selected(w, {0, 1})
    QTest.mouseMove(w.map.viewport(), start + QPoint(10, 6), delay=15)
    QTest.mouseMove(w.map.viewport(), end, delay=15)
    assert w.document.project.raw == before
    assert w.document.undo.count() == 0
    for i, old in enumerate(before["mittellinie"]["punkte"]):
        x, y, z = point_xyz(old)
        assert w.map.input["pos"][i] == pytest.approx((x + (vector.x() if i < 2 else 0), y, z + (vector.y() if i < 2 else 0)))
    QTest.mouseRelease(w.map.viewport(), Qt.LeftButton, pos=end)
    selected(w, {0, 1})
    assert w.document.undo.count() == 1 and w.document.project.closed
    for i, old in enumerate(before["mittellinie"]["punkte"]):
        x, y, z = point_xyz(old)
        if i < 2:
            assert point_xyz(w.document.points[i]) == pytest.approx((x + vector.x(), y, z + vector.y()))
        else:
            assert w.document.points[i] == old
    assert w.document.points[0]["zusatz"] == [7]
    assert w.preview_timer.isActive() and w.map.stale
    current_preview(w)
    assert not w.map.stale
    undo(w)
    assert w.document.project.raw == before and w.document.project.closed
    selected(w, {0, 1})
    current_preview(w)


def test_shift_control_frames_clicks_clear_and_select_all(group_window):
    w = group_window
    rubber(w, (-20, -20), (220, 20))
    rubber(w, (220, 220), (180, 180), Qt.ShiftModifier)
    selected(w, {0, 1, 2})
    rubber(w, (180, -20), (220, 20), Qt.ControlModifier)
    selected(w, {0, 2})
    click_point(w, 1, Qt.ShiftModifier)
    selected(w, {0, 1, 2})
    click_point(w, 0, Qt.ControlModifier)
    selected(w, {1, 2})
    click_point(w, 1)
    selected(w, {1})
    QTest.mouseClick(w.map.viewport(), Qt.LeftButton, Qt.ShiftModifier, pixel(w, 100, 100))
    selected(w, {1})
    QTest.mouseClick(w.map.viewport(), Qt.LeftButton, pos=pixel(w, 100, 100))
    selected(w, set())
    QTest.keyClick(w.map, Qt.Key_A, Qt.ControlModifier)
    selected(w, range(4))
    assert w.document.undo.count() == 0


@pytest.mark.parametrize("key,modifiers,dx,dz", [
    (Qt.Key_Right, Qt.NoModifier, .1, 0),
    (Qt.Key_Left, Qt.ShiftModifier, -1, 0),
    (Qt.Key_Down, Qt.ShiftModifier, 0, 1),
    (Qt.Key_Up, Qt.NoModifier, 0, -.1),
])
def test_arrow_keys_move_whole_group(group_window, key, modifiers, dx, dz):
    w = group_window
    rubber(w, (-20, -20), (220, 20))
    before = copy.deepcopy(w.document.project.raw)
    QTest.keyClick(w.map, key, modifiers)
    selected(w, {0, 1})
    for i, old in enumerate(before["mittellinie"]["punkte"]):
        x, y, z = point_xyz(old)
        assert point_xyz(w.document.points[i]) == pytest.approx((x + (dx if i < 2 else 0), y, z + (dz if i < 2 else 0)))
    assert w.document.undo.count() == 1 and w.document.project.closed
    undo(w)
    assert w.document.project.raw == before


def test_list_multi_selection_stays_synced_after_edit(group_window):
    w = group_window
    def row_click(index, modifiers=Qt.NoModifier):
        QTest.mouseClick(w.objects.viewport(), Qt.LeftButton, modifiers, w.objects.visualItemRect(w.objects.item(index)).center())
    row_click(0)
    row_click(2, Qt.ControlModifier)
    selected(w, {0, 2})
    assert w.document.selected == 2
    row_click(1, Qt.ShiftModifier)
    selected(w, {0, 1, 2})
    QTest.keyClick(w.objects, Qt.Key_A, Qt.ControlModifier)
    selected(w, range(4))
    QTest.keyClick(w.map, Qt.Key_Right)
    selected(w, range(4))
    assert len(w.document.points[2]) == 2  # Verschieben ergänzt keine implizite Höhe.
    undo(w)
    selected(w, range(4))


def test_escape_cancels_group_drag_and_restores_rubber_selection(group_window):
    w = group_window
    rubber(w, (-20, -20), (220, 20))
    before = copy.deepcopy(w.document.project.raw)
    start = pixel(w, 0, 0)
    QTest.mousePress(w.map.viewport(), Qt.LeftButton, pos=start)
    QTest.mouseMove(w.map.viewport(), start + QPoint(20, 15), delay=15)
    QTest.keyClick(w.map, Qt.Key_Escape)
    QTest.mouseRelease(w.map.viewport(), Qt.LeftButton, pos=start + QPoint(20, 15))
    assert w.document.project.raw == before and w.document.undo.count() == 0
    assert w.map.input["pos"] == [point_xyz(p) for p in w.document.points]
    selected(w, {0, 1})
    start, end = pixel(w, 180, 180), pixel(w, 220, 220)
    QTest.mousePress(w.map.viewport(), Qt.LeftButton, pos=start)
    QTest.mouseMove(w.map.viewport(), end, delay=15)
    selected(w, {2})
    QTest.keyClick(w.map, Qt.Key_Escape)
    QTest.mouseRelease(w.map.viewport(), Qt.LeftButton, pos=end)
    selected(w, {0, 1})


def test_group_height_mixed_values_and_undo(group_window):
    w = group_window
    rubber(w, (-20, -20), (220, 20))
    assert [e[0] for e in w.properties.entries] == ["y"]
    assert w.properties.item(0, 1).text() == "—"
    before = copy.deepcopy(w.document.project.raw)
    edit_field(w, "y", "12,5")
    assert [point_xyz(p)[1] for p in w.document.points] == [12.5, 12.5, 0, 8]
    assert w.document.points[0]["zusatz"] == [7] and "y" not in w.document.points[0]
    assert w.properties.item(0, 1).text() == "12,5"
    assert w.document.undo.count() == 1
    undo(w)
    assert w.document.project.raw == before
    assert w.properties.item(0, 1).text() == "—"


def test_segment_frame_hint_blocks_movement_then_conversion_works(app):
    w = MainWindow(Project.open(EXAMPLES / "teststrecke.json"), auto_preview=False)
    w.show()
    QTest.qWait(25)
    w.map.fit_track()
    try:
        before = copy.deepcopy(w.document.project.raw)
        points = w.map.input["pos"]
        rubber(w, (min(p[0] for p in points) - 30, min(p[2] for p in points) - 30),
               (max(p[0] for p in points) + 30, max(p[2] for p in points) + 30))
        selected(w, range(26))
        assert w.group_hint.isVisible() and w.group_convert_button.isVisible()
        # Ganze Segmentstrecke gewählt: Ziehen verschiebt die Strecke (Start), Segmente bleiben gleich.
        assert w.group_hint.text().startswith("Ganze Strecke gewählt")
        p = w.map.object_positions()[0]
        start = pixel(w, p[0], p[2])
        vector = w.map.mapToScene(start + QPoint(10, 10)) - w.map.mapToScene(start)
        QTest.mousePress(w.map.viewport(), Qt.LeftButton, pos=start)
        QTest.mouseMove(w.map.viewport(), start + QPoint(10, 10), delay=15)
        QTest.mouseRelease(w.map.viewport(), Qt.LeftButton, pos=start + QPoint(10, 10))
        ml = w.document.project.raw["mittellinie"]
        assert ml["segmente"] == before["mittellinie"]["segmente"]
        assert (ml["start"]["x"], ml["start"]["z"]) == pytest.approx(
            (before["mittellinie"]["start"]["x"] + vector.x(), before["mittellinie"]["start"]["z"] + vector.y()), abs=1e-3)
        QTest.keyClick(w.map, Qt.Key_Right)
        assert w.document.project.raw["mittellinie"]["start"]["x"] == pytest.approx(ml["start"]["x"] + .1, abs=1e-6)
        assert w.document.undo.count() == 2
        undo(w)
        undo(w)
        assert w.document.project.raw == before
        # Teilgruppe aus Segmenten bleibt gesperrt (geometrisch nicht eindeutig).
        w.document.select_many({0, 1}, 0)
        assert w.group_hint.text() == "Für Gruppenverschiebung erst ‚In Kontrollpunkte umwandeln‘"
        QTest.mousePress(w.map.viewport(), Qt.LeftButton, pos=start)
        QTest.mouseMove(w.map.viewport(), start + QPoint(10, 10), delay=15)
        QTest.mouseRelease(w.map.viewport(), Qt.LeftButton, pos=start + QPoint(10, 10))
        QTest.keyClick(w.map, Qt.Key_Right)
        assert w.document.project.raw == before and w.document.undo.index() == 0
        QTest.keyClick(w.map, Qt.Key_A, Qt.ControlModifier)
        selected(w, range(26))
        QTest.mouseClick(w.group_convert_button, Qt.LeftButton)
        assert w.document.points and w.document.project.closed
        assert not w.group_hint.isVisible() and not w.group_convert_button.isVisible()
        QTest.keyClick(w.map, Qt.Key_A, Qt.ControlModifier)
        converted = copy.deepcopy(w.document.project.raw)
        QTest.keyClick(w.map, Qt.Key_Right, Qt.ShiftModifier)
        for old, new in zip(converted["mittellinie"]["punkte"], w.document.points):
            x, y, z = point_xyz(old)
            assert point_xyz(new) == pytest.approx((x + 1, y, z))
        assert w.document.undo.count() == 2
        undo(w)
        undo(w)
        assert w.document.project.raw == before
        selected(w, set())
    finally:
        w.document.undo.setClean()
        w.close()


def test_readonly_group_cannot_move_or_change_height(group_window):
    w = group_window
    w.document.project.readonly = True
    rubber(w, (-20, -20), (220, 20))
    before = copy.deepcopy(w.document.project.raw)
    start = pixel(w, 0, 0)
    QTest.mousePress(w.map.viewport(), Qt.LeftButton, pos=start)
    QTest.mouseMove(w.map.viewport(), start + QPoint(20, 15), delay=15)
    QTest.mouseRelease(w.map.viewport(), Qt.LeftButton, pos=start + QPoint(20, 15))
    QTest.keyClick(w.map, Qt.Key_Right)
    assert w.document.project.raw == before and w.document.undo.count() == 0
    assert not w.properties.item(0, 1).flags() & Qt.ItemIsEditable


def test_rubber_selection_is_live_during_drag(group_window):
    """Rahmen zeigt die Auswahl schon während des Ziehens (vor dem Loslassen); Doku-Bild gruppenauswahl.png."""
    w = group_window
    w.document.project.raw["name"] = "AP26 · Rahmenauswahl und Punktgruppe"
    w._title()
    start, end = pixel(w, -20, -20), pixel(w, 220, 20)
    QTest.mousePress(w.map.viewport(), Qt.LeftButton, pos=start)
    QTest.mouseMove(w.map.viewport(), end, delay=15)
    selected(w, {0, 1})
    w.map.viewport().repaint()
    doc_screenshot(w, "gruppenauswahl.png")
    QTest.mouseRelease(w.map.viewport(), Qt.LeftButton, pos=end)


def test_delete_selected_group_is_one_undo_step(group_window):
    w = group_window
    before = copy.deepcopy(w.document.project.raw)
    rubber(w, (-20, -20), (220, 20))
    QTest.keyClick(w.map, Qt.Key_Delete)
    assert w.document.points == before["mittellinie"]["punkte"][2:]
    assert not w.document.project.closed and w.document.undo.count() == 1
    selected(w, {0})
    undo(w)
    assert w.document.project.raw == before and w.document.project.closed


def test_drag_back_to_origin_does_not_create_undo(group_window):
    w = group_window
    rubber(w, (-20, -20), (220, 20))
    before = copy.deepcopy(w.document.project.raw)
    start = pixel(w, 0, 0)
    QTest.mousePress(w.map.viewport(), Qt.LeftButton, pos=start)
    QTest.mouseMove(w.map.viewport(), start + QPoint(20, 15), delay=15)
    QTest.mouseMove(w.map.viewport(), start, delay=15)
    QTest.mouseRelease(w.map.viewport(), Qt.LeftButton, pos=start)
    selected(w, {0, 1})
    assert w.document.project.raw == before and w.document.undo.count() == 0
