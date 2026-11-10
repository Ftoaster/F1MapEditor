"""Live map under the track: satellite or street tiles in true ground metres.

The reference image is one picture fitted to a rectangle, so a window with a different shape stretches it.
Tiles stay in metres at the anchor latitude. Zoom changes which tiles are shown, not the scale of the ground.

Stored in the project manifest under ``livemap`` (part of undo). Tile files are a cache under
``%LOCALAPPDATA%\\Mapeditor\\tiles`` and are not saved with the project.

``lat`` / ``lon`` is the anchor. Scene ``x`` / ``z`` is where that anchor sits (X east, Z south).
``rotation`` is degrees clockwise. ``kind`` is ``satellite`` or ``street``. ``visible`` hides the layer.
"""

from __future__ import annotations

import json
import math
import os
import queue
import threading
import urllib.parse
import urllib.request
import weakref
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)

from .i18n import tr

_UA = "Mapeditor livemap (https://github.com/sticktor/F1MapEditor)"
_pix = {}
_pending = set()
_failed = set()
_jobs = queue.Queue(maxsize=240)
_workers = False
_hub = None
_layers = weakref.WeakSet()
_net_noted = False


class _Hub(QObject):
    arrived = Signal(object, object)
    found = Signal(object)


def _cache_dir():
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "Mapeditor" / "tiles"


def _tile_url(kind, z, x, y):
    if kind == "street":
        return f"https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    return ("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/"
            f"{z}/{y}/{x}")


def _cache_path(kind, z, x, y):
    return _cache_dir() / kind / str(z) / str(x) / str(y)


def fetch_tile(kind, z, x, y):
    """Tile bytes from the disk cache or the network. Raises on failure."""
    path = _cache_path(kind, z, x, y)
    if path.is_file() and path.stat().st_size > 32:
        data = path.read_bytes()
        if data[:2] in (b"\xff\xd8", b"\x89P"):
            return data
    request = urllib.request.Request(
        _tile_url(kind, z, x, y),
        headers={"User-Agent": _UA, "Accept": "image/png,image/jpeg,*/*"},
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        data = response.read()
    if data[:2] not in (b"\xff\xd8", b"\x89P"):
        raise RuntimeError("not an image")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def _worker():
    while True:
        key = _jobs.get()
        data = None
        try:
            data = fetch_tile(*key)
        except Exception:
            data = None
        hub = _hub
        if hub is not None:
            hub.arrived.emit(key, data)
        _jobs.task_done()


def _ensure_hub():
    global _hub, _workers
    if _hub is None:
        _hub = _Hub()
        _hub.arrived.connect(_on_tile, Qt.QueuedConnection)
    if not _workers:
        _workers = True
        for _ in range(4):
            threading.Thread(target=_worker, daemon=True).start()
    return _hub


def _note_failure(layer):
    global _net_noted
    if _net_noted:
        return
    _net_noted = True
    window = layer.parent()
    bar = getattr(window, "statusBar", None)
    if callable(bar):
        bar().showMessage(tr("Could not load the map. Check the internet connection."), 20000)


def _on_tile(key, data):
    _pending.discard(key)
    if not data:
        _failed.add(key)
        for layer in list(_layers):
            _note_failure(layer)
            break
    else:
        image = QImage()
        if image.loadFromData(data):
            _pix[key] = QPixmap.fromImage(image)
            if len(_pix) > 180:
                for old in list(_pix)[:40]:
                    _pix.pop(old, None)
        else:
            _failed.add(key)
    for layer in list(_layers):
        layer.changed.emit()


def _request_tile(key):
    if key in _pix or key in _pending or key in _failed:
        return
    _ensure_hub()
    _pending.add(key)
    try:
        _jobs.put_nowait(key)
    except queue.Full:
        _pending.discard(key)


def norm_deg(value):
    return (float(value) + 180.0) % 360.0 - 180.0


def metres_per_degree(lat):
    r = math.radians(lat)
    m_lat = 111132.92 - 559.82 * math.cos(2 * r) + 1.175 * math.cos(4 * r)
    m_lon = 111412.84 * math.cos(r) - 93.5 * math.cos(3 * r)
    return m_lat, max(m_lon, 1.0)


def ll_to_local(state, lat, lon):
    """Metres east and scene-south of the anchor. Scene +Z points south."""
    m_lat, m_lon = metres_per_degree(state["lat"])
    east = (lon - state["lon"]) * m_lon
    north = (lat - state["lat"]) * m_lat
    return east, -north


def local_to_ll(state, lx, lz):
    m_lat, m_lon = metres_per_degree(state["lat"])
    return state["lat"] + (-lz) / m_lat, state["lon"] + lx / m_lon


def scene_to_local(state, x, z):
    dx = x - state["x"]
    dz = z - state["z"]
    theta = math.radians(state["rotation"])
    c, s = math.cos(theta), math.sin(theta)
    return dx * c + dz * s, -dx * s + dz * c


def scene_to_ll(state, x, z):
    return local_to_ll(state, *scene_to_local(state, x, z))


def pin(state, lat, lon, sx, sz):
    """Move the anchor so ``lat``/``lon`` stays on the scene point (``sx``, ``sz``)."""
    lx, lz = ll_to_local(state, lat, lon)
    theta = math.radians(state["rotation"])
    c, s = math.cos(theta), math.sin(theta)
    state["x"] = sx - (lx * c - lz * s)
    state["z"] = sz - (lx * s + lz * c)


def tile_bounds(z, x, y):
    n = 2 ** z

    def lon_of(ix):
        return ix / n * 360.0 - 180.0

    def lat_of(iy):
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * iy / n))))

    return lon_of(x), lat_of(y), lon_of(x + 1), lat_of(y + 1)


def ll_to_tile(lon, lat, z):
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    lat = max(-85.0, min(85.0, lat))
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def zoom_for(scale, lat):
    scale = max(abs(scale), 1e-6)
    cosl = max(0.2, math.cos(math.radians(lat)))
    z = int(round(math.log2(156543.03392 * cosl * scale)))
    return max(2, min(19, z))


def tile_span(state, rect, z):
    points = []
    for x in (rect.left(), rect.center().x(), rect.right()):
        for zc in (rect.top(), rect.center().y(), rect.bottom()):
            points.append(local_to_ll(state, *scene_to_local(state, x, zc)))
    xs, ys = [], []
    n = 2 ** z
    for lat, lon in points:
        tx, ty = ll_to_tile(lon, lat, z)
        xs.append(tx)
        ys.append(ty)
    return (max(0, min(xs) - 1), min(n - 1, max(xs) + 1),
            max(0, min(ys) - 1), min(n - 1, max(ys) + 1))


def read_state(manifest):
    data = manifest.get("livemap") if isinstance(manifest, dict) else None
    if not isinstance(data, dict):
        return None
    try:
        lat, lon = float(data["lat"]), float(data["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (-85.0 <= lat <= 85.0 and -180.0 <= lon <= 180.0):
        return None
    kind = "street" if data.get("kind") == "street" else "satellite"
    try:
        return {"lat": lat, "lon": lon, "x": float(data.get("x", 0)), "z": float(data.get("z", 0)),
                "rotation": norm_deg(data.get("rotation", 0)), "kind": kind,
                "visible": bool(data.get("visible", True))}
    except (TypeError, ValueError):
        return None


def write_state(state):
    return {"lat": round(float(state["lat"]), 7), "lon": round(float(state["lon"]), 7),
            "x": round(float(state["x"]), 3), "z": round(float(state["z"]), 3),
            "rotation": round(norm_deg(state["rotation"]), 3),
            "kind": "street" if state.get("kind") == "street" else "satellite",
            "visible": bool(state.get("visible", True))}


def paint_map(painter, rect, state, scale, network):
    """Draw tiles for ``state`` into a scene-coordinate painter. Returns how many tiles were drawn."""
    if not state or not state["visible"] or rect.isEmpty():
        return 0
    z = zoom_for(scale, state["lat"])
    x0, x1, y0, y1 = tile_span(state, rect, z)
    while z > 2 and (x1 - x0 + 1) * (y1 - y0 + 1) > 80:
        z -= 1
        x0, x1, y0, y1 = tile_span(state, rect, z)
    drawn = 0
    painter.save()
    painter.translate(state["x"], state["z"])
    painter.rotate(state["rotation"])
    for tx in range(x0, x1 + 1):
        for ty in range(y0, y1 + 1):
            key = (state["kind"], z, tx, ty)
            west, north, east, south = tile_bounds(z, tx, ty)
            ax, az = ll_to_local(state, north, west)
            bx, bz = ll_to_local(state, south, east)
            target = QRectF(ax, az, bx - ax, bz - az)
            pixmap = _pix.get(key)
            if pixmap is not None and not pixmap.isNull():
                painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
                drawn += 1
            elif network:
                _request_tile(key)
    painter.restore()
    return drawn


class LiveMap(QObject):
    """Project layer. ``network`` is off in tests so opening a map never fetches tiles."""

    changed = Signal()
    network = True

    def __init__(self, document, parent=None):
        super().__init__(parent)
        self.document = document
        self.drag = None
        self.preview_xy = None
        _layers.add(self)

    def current(self):
        state = read_state(self.document.project.manifest)
        if state and self.preview_xy is not None:
            state = dict(state)
            state["x"], state["z"] = self.preview_xy
        return state

    def place(self, lat, lon, rotation, kind, x, z):
        stored = write_state({"lat": lat, "lon": lon, "x": x, "z": z, "rotation": rotation,
                              "kind": kind, "visible": True})

        def operation(raw, meta):
            meta["livemap"] = stored

        self.preview_xy = None
        return self.document.edit(tr("Place live map"), operation)

    def hide(self):
        def operation(raw, meta):
            layer = meta.get("livemap")
            if isinstance(layer, dict):
                layer["visible"] = False

        self.preview_xy = None
        return self.document.edit(tr("Hide live map"), operation)

    def commit_position(self, x, z):
        def operation(raw, meta):
            layer = meta.get("livemap")
            if isinstance(layer, dict):
                layer["x"] = round(float(x), 3)
                layer["z"] = round(float(z), 3)

        self.preview_xy = None
        return self.document.edit(tr("Move live map"), operation)

    def rotate_at(self, degrees, sx, sz):
        state = self.current()
        if not state or not state["visible"]:
            return False
        lat, lon = scene_to_ll(state, sx, sz)
        state = dict(state)
        state["rotation"] = norm_deg(state["rotation"] + degrees)
        pin(state, lat, lon, sx, sz)
        stored = write_state(state)

        def operation(raw, meta):
            meta["livemap"] = stored

        self.preview_xy = None
        return self.document.edit(tr("Rotate live map"), operation)

    def begin_drag(self, x, z):
        if self.document.project.readonly:
            return False
        state = read_state(self.document.project.manifest)
        if not state or not state["visible"]:
            return False
        self.drag = (x, z, state["x"], state["z"])
        self.preview_xy = (state["x"], state["z"])
        return True

    def update_drag(self, x, z):
        if not self.drag:
            return False
        x0, z0, ox, oz = self.drag
        self.preview_xy = (ox + (x - x0), oz + (z - z0))
        return True

    def finish_drag(self):
        if not self.drag:
            return False
        xy, self.drag = self.preview_xy, None
        if xy is None:
            return True
        self.commit_position(*xy)
        return True

    def cancel_drag(self):
        if not self.drag:
            return False
        self.drag = None
        self.preview_xy = None
        self.changed.emit()
        return True

    def wheel(self, delta, sx, sz):
        state = read_state(self.document.project.manifest)
        if not state or not state["visible"] or self.document.project.readonly:
            return False
        if not delta:
            return True
        notches = delta / 120.0
        if abs(notches) < 0.01:
            notches = 1.0 if delta > 0 else -1.0
        self.rotate_at(notches * 0.5, sx, sz)
        return True

    def paint(self, painter, rect, scale):
        return paint_map(painter, rect, self.current(), scale, self.network and self.preview_xy is None)

    def chrome(self, painter, width, height):
        state = self.current()
        if not state or not state["visible"]:
            return
        painter.save()
        painter.translate(width - 36, 46)
        painter.rotate(state["rotation"])
        painter.setPen(QColor("#F4F7FA"))
        painter.drawLine(QPointF(0, 12), QPointF(0, -16))
        painter.drawLine(QPointF(0, -16), QPointF(-5, -8))
        painter.drawLine(QPointF(0, -16), QPointF(5, -8))
        painter.drawText(QPointF(-4, -20), "N")
        painter.restore()
        credit = tr("Streets: OpenStreetMap") if state["kind"] == "street" else tr("Satellite: Esri")
        if _pending and not any(key[0] == state["kind"] for key in _pix):
            credit = tr("Loading the map") + "  ·  " + credit
        painter.setPen(QColor("#F4F7FA"))
        painter.drawText(QPointF(12, height - 10), credit)

    def open_dialog(self, parent, view):
        current = read_state(self.document.project.manifest) or {}
        dialog = QDialog(parent)
        dialog.setWindowTitle(tr("Live map"))
        dialog.setMinimumWidth(480)
        root = QVBoxLayout(dialog)
        root.addWidget(QLabel(tr(
            "Satellite or street map in true metres. It does not stretch when you zoom. "
            "Alt+drag moves it. Shift+Alt+wheel rotates it.")))
        root.addWidget(QLabel(tr("A reference image, if loaded, is drawn on top of this map.")))
        search = QLineEdit()
        search.setPlaceholderText(tr("Place name, for example Songdo"))
        find = QPushButton(tr("Find place"))
        row = QHBoxLayout()
        row.addWidget(search, 1)
        row.addWidget(find)
        root.addLayout(row)
        results = QListWidget()
        results.setMaximumHeight(110)
        root.addWidget(results)
        lat = QLineEdit(f"{current['lat']:.6f}" if current else "")
        lon = QLineEdit(f"{current['lon']:.6f}" if current else "")
        lat.setPlaceholderText("37.380000")
        lon.setPlaceholderText("126.650000")
        form = QFormLayout()
        form.addRow(tr("Latitude"), lat)
        form.addRow(tr("Longitude"), lon)
        turn = QDoubleSpinBox()
        turn.setRange(-180.0, 180.0)
        turn.setDecimals(2)
        turn.setSingleStep(0.5)
        turn.setValue(float(current.get("rotation", 0.0)) if current else 0.0)
        form.addRow(tr("Rotation (°)"), turn)
        kind = QComboBox()
        kind.addItem(tr("Satellite"), "satellite")
        kind.addItem(tr("Street map"), "street")
        if current.get("kind") == "street":
            kind.setCurrentIndex(1)
        form.addRow(tr("Map type"), kind)
        root.addLayout(form)
        buttons = QHBoxLayout()
        apply = QPushButton(tr("Place at centre of view"))
        hide = QPushButton(tr("Hide map"))
        close = QPushButton(tr("Close"))
        buttons.addWidget(apply)
        buttons.addWidget(hide)
        buttons.addWidget(close)
        root.addLayout(buttons)
        bus = _Hub(dialog)

        def on_found(payload):
            results.clear()
            if isinstance(payload, dict):
                results.addItem(tr("Search failed. Enter latitude and longitude."))
                return
            if not payload:
                results.addItem(tr("No place found."))
                return
            for place in payload:
                item = QListWidgetItem(place.get("display_name", ""))
                item.setData(Qt.UserRole, (float(place["lat"]), float(place["lon"])))
                results.addItem(item)

        def on_pick(item):
            data = item.data(Qt.UserRole)
            if not data:
                return
            lat.setText(f"{data[0]:.6f}")
            lon.setText(f"{data[1]:.6f}")

        def on_find():
            text = search.text().strip()
            if not text:
                return
            results.clear()
            results.addItem(tr("Searching…"))

            def work():
                try:
                    query = urllib.parse.urlencode({"q": text, "format": "jsonv2", "limit": 6})
                    request = urllib.request.Request(
                        "https://nominatim.openstreetmap.org/search?" + query,
                        headers={"User-Agent": _UA},
                    )
                    with urllib.request.urlopen(request, timeout=12) as response:
                        rows = json.loads(response.read().decode("utf-8"))
                except Exception:
                    rows = {"error": True}
                bus.found.emit(rows)

            threading.Thread(target=work, daemon=True).start()

        bus.found.connect(on_found, Qt.QueuedConnection)
        results.itemClicked.connect(on_pick)
        find.clicked.connect(on_find)
        search.returnPressed.connect(on_find)

        def on_apply():
            try:
                lat_v = float(lat.text().replace(",", ".").strip())
                lon_v = float(lon.text().replace(",", ".").strip())
            except ValueError:
                QMessageBox.warning(dialog, tr("Live map"), tr("Enter latitude and longitude as numbers."))
                return
            if not (-85.0 <= lat_v <= 85.0 and -180.0 <= lon_v <= 180.0):
                QMessageBox.warning(
                    dialog, tr("Live map"),
                    tr("Latitude must be between -85 and 85, longitude between -180 and 180."))
                return
            center = view.mapToScene(view.viewport().rect().center())
            self.place(lat_v, lon_v, float(turn.value()), kind.currentData(), center.x(), center.y())
            QMessageBox.information(dialog, tr("Live map"), tr(
                "Live map placed at the centre of the view. Distances stay in true metres. "
                "Alt+drag moves it, Shift+Alt+wheel rotates it."))
            dialog.accept()

        def on_hide():
            self.hide()
            dialog.accept()

        apply.clicked.connect(on_apply)
        hide.clicked.connect(on_hide)
        close.clicked.connect(dialog.reject)
        dialog.exec()
