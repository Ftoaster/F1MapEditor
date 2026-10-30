"""Offscreen-Bedienung einschließlich echter Qt-Maus- und Tastaturereignisse."""

import copy

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest

from mapeditor.adapter import calculate_preview
from mapeditor.project import Project, encoded, point_xyz
from mapeditor.window import MainWindow
from conftest import EXAMPLE_NAMES, EXAMPLES
from test_adapter import wait_until


@pytest.fixture
def window(app, example_preview):
    view = MainWindow(Project.open(EXAMPLES / "teststrecke.json"), auto_preview=False)
    view.install_preview(example_preview)
    view.show()
    QTest.qWait(20)
    yield view
    view.document.undo.setClean()
    view.close()


def test_window_has_synchronous_profile_and_selection(window):
    window.document.select(6)
    assert window.objects.currentRow() == 6
    assert window.height.station == window.map.station == window.cross.station
    assert window.height.preview is window.preview


def test_profile_click_preserves_clicked_station(window):
    QTest.mouseClick(window.height, Qt.LeftButton, pos=QPoint(window.height.width() // 2, 90))
    assert window.document.selected >= 0
    assert window.map.station == window.height.station == window.bank.station


def test_edit_segment_height_preserves_unknown_and_undo(window):
    window.document.select(6)
    before = copy.deepcopy(window.document.project.raw)
    row = next(i for i, e in enumerate(window.properties.entries) if e[0] == "hoehe_ende")
    window.properties.item(row, 1).setText("12,5")
    window.apply_properties()
    assert window.document.ml["segmente"][6]["hoehe_ende"] == 12.5
    assert window.document.undo.count() == 1
    assert "veraltet" in window.status_label.text()
    window.document.undo.undo()
    assert window.document.project.raw == before


def test_apply_unchanged_does_not_inject_defaults(window):
    window.category.setCurrentIndex(window.category.findData("fahrbahn"))
    before = copy.deepcopy(window.document.project.raw)
    window.apply_properties()
    assert window.document.project.raw == before and window.document.undo.count() == 0


def test_global_width_and_sections_undo(window):
    window.category.setCurrentIndex(window.category.findData("fahrbahn"))
    row = next(i for i, e in enumerate(window.properties.entries) if e[0] == "breite_links")
    window.properties.item(row, 1).setText("8,25")
    window.apply_properties()
    assert window.document.project.raw["fahrbahn"]["breite_links"] == 8.25
    window.category.setCurrentIndex(window.category.findData("sections"))
    window.add_section()
    assert len(window.document.project.raw["fahrbahn"]["abschnitte"]) == 1
    window.document.undo.undo()
    assert "abschnitte" not in window.document.project.raw["fahrbahn"]


def test_undo_theme_updates_canvas(window):
    window.change_theme("hell")
    assert not window.map.dark
    window.document.undo.undo()
    assert window.map.dark


def test_map_drag_and_keyboard_single_undo(app, point_project):
    preview = calculate_preview({"raw": point_project.raw})
    w = MainWindow(point_project, auto_preview=False)
    w.install_preview(preview)
    w.show()
    QTest.qWait(20)
    pos = w.map.mapFromScene(QPointF(0, 0))
    QTest.mousePress(w.map.viewport(), Qt.LeftButton, pos=pos)
    QTest.mouseMove(w.map.viewport(), pos + QPoint(30, 15))
    QTest.mouseRelease(w.map.viewport(), Qt.LeftButton, pos=pos + QPoint(30, 15))
    assert w.document.undo.count() == 1
    assert point_xyz(w.document.points[0]) != (0, 2, 0)
    x = point_xyz(w.document.points[0])[0]
    QTest.keyClick(w.map, Qt.Key_Right, Qt.ShiftModifier)
    assert point_xyz(w.document.points[0])[0] == pytest.approx(x + 1)
    w.document.undo.undo()
    w.document.undo.undo()
    assert point_xyz(w.document.points[0]) == (0, 2, 0)
    w.document.undo.setClean()
    w.close()


def test_point_height_alias_retained_via_inspector(app, point_project):
    w = MainWindow(point_project, auto_preview=False)
    w.document.select(0)
    row = next(i for i, e in enumerate(w.properties.entries) if e[0] == "y")
    w.properties.item(row, 1).setText("4,5")
    w.apply_properties()
    assert w.document.points[0]["hoehe"] == 4.5
    assert w.document.points[0]["zusatz"] == [7] and "y" not in w.document.points[0]
    w.document.undo.setClean()
    w.close()


def test_validation_jump_has_station(window):
    outside = calculate_preview({"raw": window.document.project.raw, "shift": [5000, 0, 0]})
    window.install_preview(outside)
    item = next(window.issues.item(i) for i in range(window.issues.count()) if "s" in (window.issues.item(i).data(Qt.UserRole) or {}))
    station = item.data(Qt.UserRole)["s"]
    window.jump_issue(item)
    assert window.map.station == window.height.station == station


def test_actual_window_async_preview(window):
    window.preview = None
    window.request_preview()
    wait_until(lambda: window.job is None)
    assert window.preview is not None and "aktuell" in window.status_label.text()


def test_stale_worker_result_rejected(window):
    old = window.preview
    window.request_preview()
    window.document.edit("Änderung während Berechnung", lambda raw, meta: raw.update(name="Neuer Stand"))
    wait_until(lambda: window.preview_revision == window.document.revision and window.job is None)
    assert window.preview is not old
    assert "aktuell" in window.status_label.text()


def test_read_output_never_changes_source(window, tmp_path):
    filename = tmp_path / "strecke_logik.json"
    data = {"sektoren": [0, 100], "drs": [], "boxenplaetze": [], "AISplineData": {"brake_lines": [{"s": 20}]}}
    filename.write_bytes(encoded(data))
    before = filename.read_bytes()
    window.load_output(filename)
    assert filename.read_bytes() == before
    assert "ungeprüft" in window.output_tree.topLevelItem(0).text(1)


def test_open_draft_loads_defaults_without_fake_round(app):
    w = MainWindow(auto_preview=False)
    w.request_preview()
    wait_until(lambda: w.job is None)
    assert w.defaults["fahrbahn"]["breite"] == 12
    assert w.preview is None and not w.document.project.closed
    w.category.setCurrentIndex(w.category.findData("fahrbahn"))
    assert w.properties.rowCount() > 0
    w.close()


@pytest.mark.parametrize("name", EXAMPLE_NAMES)
def test_examples_through_window_preserve_saved_definition(app, tmp_path, name):
    project = Project.open(EXAMPLES / name)
    before = copy.deepcopy(project.raw)
    w = MainWindow(project, auto_preview=False)
    w.show()
    w.request_preview()
    wait_until(lambda: w.job is None)
    assert w.preview is not None
    w.document.select(w.objects.count() // 2)
    if name == "portimao_form.json":
        assert w.objects.count() == 669
        x = point_xyz(w.document.points[w.document.selected])[0]
        w.document.move(w.document.selected, x=x + 1)
        w.document.undo.undo()
    project.save(tmp_path)
    assert Project.open(tmp_path).raw == before
    w.document.undo.setClean()
    w.close()

