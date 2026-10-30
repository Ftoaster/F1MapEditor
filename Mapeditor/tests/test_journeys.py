"""Nutzerreisen: ausschließlich Maus/Tastatur auf Widgets zum Bearbeiten.

Definition, Eingabepfade, Pixel und echte asynchrone Generatorantworten prüfen.
"""

import copy

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QPushButton

from mapeditor.project import Project, point_xyz
from mapeditor.window import MainWindow
from conftest import EXAMPLES, doc_screenshot
from test_adapter import wait_until


@pytest.fixture
def journey(app):
    windows = []
    def open_track(name="teststrecke.json"):
        if not (EXAMPLES / name).exists():
            pytest.skip(f"{name} gehört nicht zum öffentlichen Stand")
        w = MainWindow(Project.open(EXAMPLES / name))
        windows.append(w)
        w.show()
        wait_until(lambda: w.preview is not None and w.job is None)
        QTest.qWait(30)
        return w
    yield open_track
    for w in windows:
        w.document.undo.setClean()
        w.close()


def click_object(w, index):
    w.objects.scrollToItem(w.objects.item(index))
    QTest.mouseClick(w.objects.viewport(), Qt.LeftButton, pos=w.objects.visualItemRect(w.objects.item(index)).center())
    assert w.document.selected == index


def edit_field(w, key, text, enter=True):
    table = w.properties
    row = next(i for i, e in enumerate(table.entries) if e[0] == key)
    item = table.item(row, 1)
    table.scrollToItem(item)
    center = table.visualItemRect(item).center()
    QTest.mouseClick(table.viewport(), Qt.LeftButton, pos=center)
    QTest.mouseDClick(table.viewport(), Qt.LeftButton, pos=center)
    editor = QApplication.focusWidget()
    if isinstance(editor, QComboBox):          # Auswahlfelder (Segmentart, ja/nein …): Eintrag eintippen
        QTest.keyClicks(editor, text)
        assert editor.currentText() == text, f"Auswahl {text!r} nicht gefunden"
        QTest.keyClick(editor, Qt.Key_Return)
        QTest.qWait(25)
        return
    assert isinstance(editor, QLineEdit), f"Kein echter Zell-Editor: {editor}"
    QTest.keyClick(editor, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(editor, text)
    if enter:
        QTest.keyClick(editor, Qt.Key_Return)
    else:
        QTest.keyClick(editor, Qt.Key_Tab)
    QTest.qWait(25)


def drag(w, position, delta):
    start = w.map.mapFromScene(QPointF(position[0], position[2]))
    end = start + delta
    assert w.map.viewport().rect().contains(start)
    QTest.mousePress(w.map.viewport(), Qt.LeftButton, pos=start)
    QTest.mouseMove(w.map.viewport(), end, delay=20)
    QTest.mouseRelease(w.map.viewport(), Qt.LeftButton, pos=end)
    QTest.qWait(25)


def undo(w):
    QTest.keyClick(w, Qt.Key_Z, Qt.ControlModifier)
    QTest.qWait(25)


def button(w, title):
    result = next(b for b in w.findChildren(QPushButton) if b.text() == title)
    assert result.isVisible() and result.isEnabled()
    QTest.mouseClick(result, Qt.LeftButton)
    QTest.qWait(25)


def current_preview(w):
    wait_until(lambda: w.job is None and w.preview_revision == w.document.revision and not w.preview_timer.isActive())


def test_segment_map_drag_automatic_preview_and_undo(journey):
    w = journey()
    before = copy.deepcopy(w.document.project.raw)
    original_positions = copy.deepcopy(w.preview["pos"])
    line = w.map.input["segments"][0]["middle"]
    QTest.mouseClick(w.map.viewport(), Qt.LeftButton, pos=w.map.mapFromScene(QPointF(line[0], line[2])))
    assert w.document.selected == w.objects.currentRow() == 0
    image = w.map.grab().toImage()
    input_before = copy.deepcopy(w.map.input["pos"])
    doc_screenshot(w, "bedienung_vorher.png")
    drag(w, w.map.input["segments"][0]["end"], QPoint(5, 0))
    assert w.document.ml["segmente"][0]["laenge"] != before["mittellinie"]["segmente"][0]["laenge"]
    assert w.map.input["pos"] != input_before
    assert w.map.grab().toImage() != image
    assert w.document.undo.count() == 1
    assert w.map.stale and w.preview_timer.isActive()
    doc_screenshot(w, "bedienung_nachher_eingabe.png")
    current_preview(w)
    assert w.preview["pos"] != original_positions
    assert not w.map.stale
    doc_screenshot(w, "bedienung_nachher.png")
    undo(w)
    assert w.document.project.raw == before
    assert w.map.input["pos"] == input_before
    current_preview(w)
    assert w.preview["pos"] == original_positions


@pytest.mark.parametrize("enter", [True, False], ids=["Enter", "Feld-verlassen"])
def test_length_field_changes_map_without_another_action(journey, enter):
    w = journey()
    click_object(w, 0)
    before = copy.deepcopy(w.map.input["pos"])
    image = w.map.grab().toImage()
    edit_field(w, "laenge", "1338,481", enter=enter)
    assert w.document.ml["segmente"][0]["laenge"] == 1338.481
    assert w.map.input["pos"] != before
    assert w.map.grab().toImage() != image
    assert w.document.undo.count() == 1
    current_preview(w)


def test_bend_radius_handle_changes_definition_and_drawing(journey):
    w = journey()
    click_object(w, 3)
    before = copy.deepcopy(w.map.input["pos"])
    radius = w.document.ml["segmente"][3]["radius"]
    image = w.map.grab().toImage()
    drag(w, w.map.input["segments"][3]["middle"], QPoint(4, 2))
    assert w.document.ml["segmente"][3]["radius"] != radius
    assert w.map.input["pos"] != before
    assert w.map.grab().toImage() != image
    undo(w)
    assert w.document.ml["segmente"][3]["radius"] == radius


def test_portimao_drag_double_click_insert_delete(journey):
    w = journey("portimao_form.json")
    before = copy.deepcopy(w.document.project.raw)
    points = copy.deepcopy(w.map.input["pos"])
    image = w.map.grab().toImage()
    drag(w, point_xyz(w.document.points[0]), QPoint(22, 13))
    moved = w.document.selected
    assert w.document.points[moved] != before["mittellinie"]["punkte"][moved]
    assert w.map.input["pos"] != points
    assert w.map.grab().toImage() != image
    count = len(w.document.points)
    a, b = point_xyz(w.document.points[moved]), point_xyz(w.document.points[(moved + 1) % count])
    middle = QPointF((a[0] + b[0]) / 2, (a[2] + b[2]) / 2)
    image = w.map.grab().toImage()
    QTest.mouseDClick(w.map.viewport(), Qt.LeftButton, pos=w.map.mapFromScene(middle))
    QTest.qWait(25)
    assert len(w.document.points) == count + 1
    assert len(w.map.input["pos"]) == count + 1
    assert w.map.grab().toImage() != image
    inserted = w.document.selected
    QTest.keyClick(w.map, Qt.Key_Delete)
    QTest.qWait(25)
    assert len(w.document.points) == count
    assert len(w.map.input["pos"]) == count
    assert w.document.selected <= inserted
    undo(w)
    undo(w)
    undo(w)
    assert w.document.project.raw == before


def test_segment_height_updates_drawn_profile_immediately(journey):
    w = journey()
    click_object(w, 6)
    image = w.height.grab().toImage()
    old = [p[1] for p in w.height.preview["pos"]]
    edit_field(w, "hoehe_ende", "16,5")
    assert w.document.ml["segmente"][6]["hoehe_ende"] == 16.5
    # Seit 25.10.2026 bleibt das Höhenprofil auf der Vorschauachse; der Griff zeigt die neue Höhe sofort
    assert next(n["y"] for n in w.height.nodes() if n["index"] == 6) == 16.5
    assert w.height.grab().toImage() != image
    current_preview(w)
    assert max(p[1] for p in w.height.preview["pos"]) > 15


def test_segment_actions_and_explicit_conversion_are_undoable(journey):
    w = journey()
    click_object(w, 0)
    before = copy.deepcopy(w.document.project.raw)
    button(w, "Segment teilen")
    assert len(w.document.ml["segmente"]) == 27
    assert w.document.ml["segmente"][0]["laenge"] == before["mittellinie"]["segmente"][0]["laenge"] / 2
    undo(w)
    button(w, "+ Bogen")
    assert w.document.ml["segmente"][1]["typ"] == "bogen"
    button(w, "Gerade ↔ Bogen")
    assert w.document.ml["segmente"][1]["typ"] == "gerade"
    QTest.keyClick(w.map, Qt.Key_Delete)
    assert len(w.document.ml["segmente"]) == 26
    undo(w)
    undo(w)
    undo(w)
    assert w.document.project.raw == before
    button(w, "+ Gerade")
    assert len(w.document.ml["segmente"]) == 27
    undo(w)
    button(w, "In Kontrollpunkte umwandeln")
    assert w.document.ml["art"] == "kontrollpunkte"
    assert w.document.points and not w.segment_buttons[0].isVisible()
    current_preview(w)
    image = w.map.grab().toImage()
    drag(w, point_xyz(w.document.points[0]), QPoint(5, 5))
    assert w.map.grab().toImage() != image
    undo(w)
    undo(w)
    assert w.document.project.raw == before


def test_invalid_text_and_closure_error_keep_editor_usable(journey):
    w = journey()
    click_object(w, 0)
    old = w.document.ml["segmente"][0]["laenge"]
    edit_field(w, "laenge", "abc")
    assert w.document.ml["segmente"][0]["laenge"] == old
    assert "Ungültige Eingabe" in w.status_label.text()
    row = next(i for i, e in enumerate(w.properties.entries) if e[0] == "laenge")
    assert w.properties.item(row, 1).text() == "abc"
    edit_field(w, "laenge", "1600")
    assert w.document.ml["segmente"][0]["laenge"] == 1600
    wait_until(lambda: w.job is None and not w.preview_timer.isActive())
    assert "schließen nicht" in w.status_label.text()
    assert w.map.stale
    assert w.properties.item(row, 1).text() == "1600"
    edit_field(w, "laenge", str(old))
    current_preview(w)
    assert not w.map.stale


def test_mode_keyboard_and_toolbar_agree(journey):
    w = journey("portimao_form.json")
    QTest.keyClick(w.map, Qt.Key_P)
    assert w.map.mode == "punkte" and w.mode_actions["punkte"].isChecked()
    assert "EINFÜGEN" in w.mode_hint.text()
    QTest.keyClick(w.map, Qt.Key_Escape)
    assert w.map.mode == "auswahl" and w.mode_actions["auswahl"].isChecked()


def test_rapid_edits_are_debounced_and_completed_inputs_have_own_undo(journey):
    w = journey()
    click_object(w, 0)
    starts = QSignalSpy(w.preview_timer.timeout)
    for value in ("1330", "1331", "1332"):
        edit_field(w, "laenge", value)
    assert starts.count() == 0
    assert w.document.undo.count() == 3
    current_preview(w)
    assert starts.count() == 1
    assert w.document.ml["segmente"][0]["laenge"] == 1332
    undo(w)
    assert w.document.ml["segmente"][0]["laenge"] == 1331


def test_edit_during_preview_replaces_old_job_and_cancel_is_respected(journey):
    w = journey()
    click_object(w, 0)
    edit_field(w, "laenge", "1330")
    wait_until(lambda: w.job is not None)
    old_job = w.job
    edit_field(w, "laenge", "1332")
    assert old_job.cancelled
    current_preview(w)
    assert w.document.ml["segmente"][0]["laenge"] == 1332
    edit_field(w, "laenge", "1334")
    wait_until(lambda: w.job is not None)
    assert w.cancel_button.isVisible() and w.cancel_action.isEnabled()
    QTest.mouseClick(w.cancel_button, Qt.LeftButton)
    wait_until(lambda: w.job is None)
    QTest.qWait(450)
    assert w.job is None and not w.preview_timer.isActive()
    assert w.map.stale and "abgebrochen" in w.status_label.text()
    edit_field(w, "laenge", "1336")
    current_preview(w)
    assert not w.map.stale


def test_bend_endpoint_changes_radius_and_angle(journey):
    w = journey()
    click_object(w, 3)
    original = copy.deepcopy(w.document.ml["segmente"][3])
    path = copy.deepcopy(w.map.input["pos"])
    drag(w, w.map.input["segments"][3]["end"], QPoint(5, 2))
    segment = w.document.ml["segmente"][3]
    assert segment["radius"] != original["radius"] and segment["winkel"] != original["winkel"]
    assert w.map.input["pos"] != path
    undo(w)
    assert w.document.ml["segmente"][3] == original


def test_integer_radius_accepts_decimal_and_type_field_creates_valid_defaults(journey):
    w = journey()
    click_object(w, 3)
    edit_field(w, "radius", "110,5")
    assert w.document.ml["segmente"][3]["radius"] == 110.5
    click_object(w, 0)
    edit_field(w, "typ", "bogen")
    assert w.document.ml["segmente"][0]["typ"] == "bogen"
    assert w.document.ml["segmente"][0]["radius"] > 0
    assert not w.map.input["error"]


def test_drag_draws_live_and_escape_cancels_without_undo_entry(journey):
    w = journey()
    click_object(w, 0)
    before = copy.deepcopy(w.document.project.raw)
    positions = copy.deepcopy(w.map.input["pos"])
    end = w.map.input["segments"][0]["end"]
    start = w.map.mapFromScene(QPointF(end[0], end[2]))
    QTest.mousePress(w.map.viewport(), Qt.LeftButton, pos=start)
    QTest.mouseMove(w.map.viewport(), start + QPoint(10, 0), delay=20)
    assert w.map.input["pos"] != positions
    assert w.document.project.raw == before and w.document.undo.count() == 0
    QTest.keyClick(w.map, Qt.Key_Escape)
    QTest.mouseRelease(w.map.viewport(), Qt.LeftButton, pos=start + QPoint(10, 0))
    assert w.map.input["pos"] == positions
    assert w.document.project.raw == before and w.document.undo.count() == 0


def test_local_width_and_bank_change_input_edges_and_profiles(journey):
    w = journey()
    click_object(w, 0)
    image = w.map.grab().toImage()
    edges = copy.deepcopy(w.map.input["left"])
    edit_field(w, "breite", "22,5")
    assert w.document.ml["segmente"][0]["breite"] == 22.5
    assert w.map.input["left"] != edges
    assert w.map.grab().toImage() != image
    bank_before = w.bank.grab().toImage()
    assert "querneigung_grad" not in [e[0] for e in w.properties.entries]     # steht im Querneigungsfeld
    w.apply_feature("querneigungsfeld", False, save=False)                     # ohne Feld wieder als Tabellenzeile
    edit_field(w, "querneigung_grad", "5")
    # Querneigungsprofil: der Griff zeigt den neuen Wert sofort, die berechnete Kurve folgt mit der Vorschau
    assert next(n for n in w.bank.nodes() if n["index"] == 0)["y"] == 5
    assert w.bank.grab().toImage() != bank_before
    current_preview(w)
    assert max(w.bank.preview["bank"]) > 4.9


def test_async_result_preserves_invalid_field_text(journey):
    w = journey()
    click_object(w, 0)
    edit_field(w, "laenge", "1332")
    wait_until(lambda: w.job is not None)
    edit_field(w, "laenge", "abc")
    current_preview(w)
    row = next(i for i, e in enumerate(w.properties.entries) if e[0] == "laenge")
    assert w.properties.item(row, 1).text() == "abc"
    assert w.document.ml["segmente"][0]["laenge"] == 1332
    assert "Ungültige Feldeingabe" in w.status_label.text()
