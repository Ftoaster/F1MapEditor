"""Live map: true metres, undo, and painting without a network."""

import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap, QTransform

from mapeditor.document import Document
from mapeditor.livemap import LiveMap, paint_map, pin, scene_to_ll, tile_span, zoom_for, _pix
from mapeditor.window import MainWindow

pytestmark = pytest.mark.usefixtures("app")


@pytest.fixture(autouse=True)
def offline_tiles():
    previous = LiveMap.network
    LiveMap.network = False
    yield
    LiveMap.network = previous


def test_anchor_stays_under_the_cursor_when_rotated():
    state = {"lat": 37.383, "lon": 126.644, "x": 0.0, "z": 0.0, "rotation": 0.0,
             "kind": "satellite", "visible": True}
    assert scene_to_ll(state, 0.0, 0.0) == pytest.approx((37.383, 126.644))
    state["rotation"] = 25.0
    pin(state, 37.386, 126.650, 120.0, -40.0)
    assert scene_to_ll(state, 120.0, -40.0) == pytest.approx((37.386, 126.650))


def test_place_move_and_undo_do_not_change_the_track():
    LiveMap.network = False
    document = Document()
    layer = LiveMap(document)
    layer.place(37.38, 126.65, 12.0, "satellite", 10.0, -4.0)
    saved = document.project.manifest["livemap"]
    assert saved["lat"] == 37.38 and saved["rotation"] == 12.0 and saved["visible"] is True
    assert document.revision == 0
    layer.commit_position(35.0, -14.0)
    assert document.project.manifest["livemap"]["x"] == 35.0
    document.undo.undo()
    assert document.project.manifest["livemap"]["x"] == 10.0
    document.undo.undo()
    assert "livemap" not in document.project.manifest


def test_tiles_paint_in_scene_metres_without_network():
    state = {"lat": 37.383, "lon": 126.644, "x": 0.0, "z": 0.0, "rotation": 0.0,
             "kind": "satellite", "visible": True}
    rect = QRectF(-180, -140, 360, 280)
    z = zoom_for(2.0, state["lat"])
    x0, x1, y0, y1 = tile_span(state, rect, z)
    pixmap = QPixmap(8, 8)
    pixmap.fill(QColor(220, 30, 30))
    for tx in range(x0, x1 + 1):
        for ty in range(y0, y1 + 1):
            _pix[("satellite", z, tx, ty)] = pixmap
    image = QImage(400, 300, QImage.Format_RGB32)
    image.fill(QColor("#101820"))
    painter = QPainter(image)
    painter.translate(200, 150)
    painter.scale(2, 2)
    drawn = paint_map(painter, rect, state, 2.0, False)
    painter.end()
    assert drawn >= 1
    center = image.pixelColor(200, 150)
    assert (center.red(), center.green(), center.blue()) != (16, 24, 32)


def test_window_menu_places_a_live_map():
    LiveMap.network = False
    window = MainWindow(auto_preview=False)
    assert window.map.livemap is window.livemap
    window.livemap.place(37.5, 127.0, 0.0, "street", 0.0, 0.0)
    assert window.document.project.manifest["livemap"]["kind"] == "street"
    assert window.document.revision == 0
