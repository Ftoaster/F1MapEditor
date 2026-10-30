"""Drehen über den Drehgriff des Auswahlkastens – nur mit echten Maus-/Tastaturereignissen."""

import copy
import math

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mapeditor.project import Project, point_xyz
from mapeditor.window import MainWindow
from conftest import EXAMPLES
from test_journeys import undo


@pytest.fixture
def square(app, point_project):
    w = MainWindow(point_project, auto_preview=False)
    w.show()
    QTest.qWait(25)
    w.map.fit_track()
    yield w
    w.document.undo.setClean()
    w.close()


def select_all(w):
    QTest.keyClick(w.map, Qt.Key_A, Qt.ControlModifier)
    QTest.qWait(10)


def handle(w):
    result = w.map.rotation_handle()
    assert result is not None, "Drehgriff fehlt"
    return result[0].toPoint()


def view_point(w, x, z):
    return w.map.mapFromScene(QPointF(x, z))


def shift_move(w, pos):
    event = QMouseEvent(QEvent.MouseMove, QPointF(pos), w.map.viewport().mapToGlobal(QPointF(pos)),
                        Qt.NoButton, Qt.LeftButton, Qt.ShiftModifier)
    QApplication.sendEvent(w.map.viewport(), event)


def test_handle_only_for_rotatable_selection(square):
    w = square
    assert w.map.rotation_handle() is None
    w.document.select(0)
    assert w.map.rotation_handle() is None          # ein Punkt: nichts zu drehen
    select_all(w)
    assert w.map.rotation_handle() is not None


def test_rotate_square_90_degrees_with_handle_and_undo(square):
    w = square
    select_all(w)
    before = copy.deepcopy(w.document.project.raw)
    start = handle(w)
    target = view_point(w, 100 + 141, 100)          # rechts vom Mittelpunkt (100, 100): +90° im Uhrzeigersinn
    QTest.mousePress(w.map.viewport(), Qt.LeftButton, pos=start)
    QTest.mouseMove(w.map.viewport(), target, delay=15)
    assert w.document.project.raw == before          # Entwurf erst beim Loslassen übernommen
    assert w.map.input["pos"][0] == pytest.approx((200, 2, 0), abs=2.5)
    QTest.mouseRelease(w.map.viewport(), Qt.LeftButton, pos=target)
    QTest.qWait(10)
    assert w.document.undo.count() == 1
    expected = [(200, 0), (200, 200), (0, 200), (0, 0)]
    for point, (x, z) in zip(w.document.points, expected):
        px, _, pz = point_xyz(point)
        assert (px, pz) == pytest.approx((x, z), abs=2.5)
    # Höhen, Listenform und Zusatzfelder bleiben erhalten
    assert [point_xyz(p)[1] for p in w.document.points] == [2, 4, 0, 8]
    assert w.document.points[0]["zusatz"] == [7] and isinstance(w.document.points[1], list)
    undo(w)
    assert w.document.project.raw == before


def test_shift_snaps_to_15_degrees(square):
    w = square
    select_all(w)
    start = handle(w)
    angle = math.radians(-90 + 37)                   # Griff liegt bei -90°; 37° weiter → Raster 30°
    target = view_point(w, 100 + 120 * math.cos(angle), 100 + 120 * math.sin(angle))
    QTest.mousePress(w.map.viewport(), Qt.LeftButton, pos=start)
    QTest.mouseMove(w.map.viewport(), target, delay=15)
    shift_move(w, target)
    assert math.degrees(w.map.rotate_angle) == pytest.approx(30)
    QTest.mouseRelease(w.map.viewport(), Qt.LeftButton, Qt.ShiftModifier, target)
    x, _, z = point_xyz(w.document.points[1])        # (200, 0) um 30° um (100, 100)
    dx, dz = 100, -100
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    assert (x, z) == pytest.approx((100 + dx * c - dz * s, 100 + dx * s + dz * c), abs=1e-3)


def test_escape_cancels_rotation(square):
    w = square
    select_all(w)
    before = copy.deepcopy(w.document.project.raw)
    start = handle(w)
    QTest.mousePress(w.map.viewport(), Qt.LeftButton, pos=start)
    QTest.mouseMove(w.map.viewport(), view_point(w, 260, 100), delay=15)
    QTest.keyClick(w.map, Qt.Key_Escape)
    QTest.mouseRelease(w.map.viewport(), Qt.LeftButton, pos=view_point(w, 260, 100))
    assert w.document.project.raw == before and w.document.undo.count() == 0
    assert w.map.input["pos"] == [point_xyz(p) for p in w.document.points]


def test_r_key_rotates_one_degree(square):
    w = square
    select_all(w)
    QTest.keyClick(w.map, Qt.Key_R)
    x, _, z = point_xyz(w.document.points[1])
    c, s = math.cos(math.radians(1)), math.sin(math.radians(1))
    assert (x, z) == pytest.approx((100 + 100 * c + 100 * s, 100 + 100 * s - 100 * c), abs=1e-3)
    QTest.keyClick(w.map, Qt.Key_R, Qt.ShiftModifier)
    assert point_xyz(w.document.points[1])[0] == pytest.approx(200, abs=1e-3)
    assert w.document.undo.count() == 2


def test_rotate_whole_segment_track(app):
    w = MainWindow(Project.open(EXAMPLES / "teststrecke.json"), auto_preview=False)
    w.show()
    QTest.qWait(25)
    w.map.fit_track()
    try:
        w.document.select(0)
        assert w.map.rotation_handle() is None       # einzelnes Segment: Strecke nicht drehbar
        select_all(w)
        before = copy.deepcopy(w.document.project.raw)
        old = copy.deepcopy(w.map.input["pos"])
        x0, z0, x1, z1 = w.map.rotation_frame()
        cx, cz = (x0 + x1) / 2, (z0 + z1) / 2
        start = handle(w)
        target = view_point(w, cx - 400, cz)          # links vom Mittelpunkt: −90°
        QTest.mousePress(w.map.viewport(), Qt.LeftButton, pos=start)
        QTest.mouseMove(w.map.viewport(), target, delay=15)
        QTest.mouseRelease(w.map.viewport(), Qt.LeftButton, pos=target)
        QTest.qWait(10)
        after = w.document.project.raw
        assert after["mittellinie"]["segmente"] == before["mittellinie"]["segmente"]   # Form unverändert
        assert after["mittellinie"]["start"]["richtung_grad"] == pytest.approx(90, abs=1)
        new = w.map.input["pos"]
        # Abstände bleiben (starre Drehung), die Strecke ist um ~90° gedreht
        assert math.dist(new[0], new[200]) == pytest.approx(math.dist(old[0], old[200]), rel=1e-6)
        angle = math.degrees(math.atan2(new[200][2] - new[0][2], new[200][0] - new[0][0])
                             - math.atan2(old[200][2] - old[0][2], old[200][0] - old[0][0]))
        assert (angle + 180) % 360 - 180 == pytest.approx(-90, abs=1)
        assert w.document.undo.count() == 1
        undo(w)
        assert w.document.project.raw == before
    finally:
        w.document.undo.setClean()
        w.close()
