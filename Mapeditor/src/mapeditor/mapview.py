"""Draufsicht (Karte): Ebenen-Zeichnung, Gesten Ziehen/Rahmen/Drehen.

Leistung: Die Generator-Ebene wird nur bei neuer Vorschau aufgebaut; beim Ziehen werden nur die
Pfade der Eingabe-Ebene ersetzt. Rahmenauswahl rechnet in Szenenkoordinaten und meldet die Auswahl
nur bei Änderung. Entwürfe während einer Geste sind flache Kopien der Mittellinie.
"""

import bisect
import math
from types import SimpleNamespace

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QRadialGradient
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView

from . import umgebung
from .i18n import N_, tr
from .project import point_xyz, shifted_point
from .input_geometry import input_geometry


MATERIALS = {0: "#596675", 1: "#D65B65", 2: "#436A50", 3: "#B49768", 4: "#A8B2BB", 5: "#48596C", 6: "#F5F5F0",
             7: "#6B7684"}
MATERIAL_NAMES = {0: N_("Asphalt"), 1: N_("Kerb"), 2: N_("Grass"), 3: N_("Gravel"), 4: N_("Wall"), 5: N_("Pit lane"),
                  6: N_("Edge line"), 7: N_("Asphalt run-off")}

ROTATE_HANDLE_PX = 30     # Abstand des Drehgriffs über dem Auswahlkasten
SNAP_DEG = 15.0           # Raster beim Drehen mit Umschalt
ENV_MODES = ("umgebung", "streuen")     # Werkzeuge für die Umgebungsebene (Taste U bzw. B)
#: Farben der Geländeformen auf der Karte
TERRAIN_FARBEN = {"huegel": "#C08A4A", "senke": "#4F8FD0", "berge": "#9AA3AE"}
ENV_HIT_PX = 7            # Trefferfläche eines Umgebungsobjekts mindestens (Pixel)


def path_for(points, close=False):
    path = QPainterPath()
    if len(points):
        # Ein Polygon statt tausender lineTo-Aufrufe aus Python.
        path.addPolygon(QPolygonF([QPointF(float(p[0]), float(p[1])) for p in points]))
        if close:
            path.closeSubpath()
    return path


def cosmetic(color, width=1, style=Qt.SolidLine):
    pen = QPen(QColor(color), width, style)
    pen.setCosmetic(True)
    return pen


class MapView(QGraphicsView):
    station_changed = Signal(float)
    mode_changed = Signal(str)
    draft_changed = Signal()
    group_move_blocked = Signal()
    zone_moved = Signal(str, int, str, float)     # Art (drs/xmode), Index, Feld, Station ab Ziel
    zone_clicked = Signal(str, int)
    zone_restore = Signal()                       # Esc während eines Zonenzugs: Overlay aus der Definition
    turn_clicked = Signal(int)                    # Kurvennummer (Spiel) angeklickt
    camera_clicked = Signal(str)                  # Kameragruppe (z. B. „s07a“) angeklickt
    camera_moved = Signal(str, str, float, float)  # Instanzname, Vorlage, Kartenpunkt x, z (Werkzeug Kameras)
    env_place_requested = Signal(float, float)    # Klick mit gewähltem Modell: Definitionskoordinaten x, z
    env_scatter_requested = Signal(dict)          # Fläche bepflanzen: {"form": "rechteck"|"pinsel", …}
    env_disarm_requested = Signal()               # Esc: Modell der Platzierung abwählen
    terrain_place_requested = Signal(str, float, float)   # Klick mit gewählter Geländeform: Art, x, z (Definition)

    def __init__(self, document):
        super().__init__()
        self.document = document
        self.setScene(QGraphicsScene(self))
        self.scene().setItemIndexMethod(QGraphicsScene.NoIndex)   # Pfade ändern sich oft: kein BSP-Baum
        self.setSceneRect(-14000, -14000, 28000, 28000)   # Berge im Außenterrain bis 12 km von der Mitte
        self.setRenderHint(QPainter.Antialiasing)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setOptimizationFlags(QGraphicsView.DontSavePainterState | QGraphicsView.DontAdjustForAntialiasing)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(tr("Track map: X right, Z down, metres"))
        # Verschieben mit mittlerer Taste/Leertaste, Mausrad zoomt: Rollbalken sind überflüssig
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.preview = None
        self.dark = True
        self.grid = self.bands = self.direction = self.show_frame = self.show_zones = True
        # Kameras des Trägers (spiel_kameras.plan): Zonen quer über der Strecke, Kameras daneben
        self.show_cameras = False
        self.cameras = []
        self.camera_group = None                  # gewählte Gruppe (hervorgehoben)
        self._camera_hits = []
        self.camera_drag = None                   # {"name", "template", "punkt": QPointF (Ansicht), "start"}
        self.mode = "auswahl"
        self.pan_origin = None
        self.space = False
        self.drag_index = -1
        self.drag_point = None
        self.drag_handle = "end"
        self.drag_geometry = None
        self.stale = False
        self.input = input_geometry(document)
        self.press_position = None
        self.rubber_origin = None
        self.rubber_end = None
        self.rubber_base = set()
        self.rubber_modifiers = Qt.NoModifier
        self.rubber_last = None
        self.click_single = None
        self.drag_delta = QPointF()
        self.rotate_center = None
        self.rotate_start = 0.0
        self.rotate_angle = 0.0
        self.station = 0.0
        self._preview_items = []
        self._preview_cache = None
        self._preview_obj = None
        self._preview_key = None
        self._input_items = None
        # Zonen-Overlay (vom Fenster gesetzt): [{"art", "index", "a", "b", "det", "auto", "label"}]
        self.zones = []
        self.zone_selected = None      # (art, index) der Zone mit Griffen
        self.overlap_marks = []        # eingefügtes Stück überschneidet die Strecke: [(x, z)], gilt bis zur nächsten Änderung
        self.overlap_revision = -1
        self.zone_drag = None          # (art, index, feld) während eines Griffzugs
        # Fahrlinienlauf (adapter.analysis_summary): Ideallinie, Bremszonen, Tafeln, Startplätze, Boxen, Sektoren
        self.analysis = None
        self.analysis_stale = False
        self.analysis_note = ""
        self.show_line = self.show_race = self.show_turns = True
        # weitere Ebenen (Menü Ansicht): Eingabepunkte, Zonenbeschriftungen, Maßstab, Hinweisschilder
        self.show_points = self.show_labels = self.show_scale = self.show_hints = True
        self.highlight = None          # „start“ / „grid“ / „sektoren“: gewählter Eigenschaftsbereich
        self.focus_curve = None        # Kurve aus der Auswertung (Infoschild am Scheitel)
        self._line_paths = (None, None)
        self._placed = []              # Beschriftungen dieses Bildes (keine Überlappung)
        self._turn_hits = []           # (Rechteck, Kurvennummer) der Kurvenschilder
        # Umgebungsebene (Bäume, Gebäude …): Katalogindex für Größe/Farbe, gewähltes Modell, Bepflanzen-Einstellungen
        self.show_env = True
        self.env_index = {}
        self.env_model = None
        self.env_form = "rechteck"
        self.env_radius = 30.0
        self.env_drag = None           # {"press": Ansichtspunkt, "origin": Szenenpunkt, "active": bool}
        self.env_delta = QPointF()     # Verschiebung der gewählten Objekte während des Ziehens (Meter)
        self.env_click_single = None
        self.env_rubber = None         # {"a": Ansichtspunkt, "b": Ansichtspunkt, "basis": set, "mods": Modifier}
        self.env_area = None           # Bepflanzen-Geste: {"form", "a", "b", "punkte"} in Szenenkoordinaten
        self.env_hover = None
        # Gelände: gewählte Art zum Setzen ("huegel"/"berge"), Radius der Vorschau, Ziehen eines Hügels/Bergs
        self.terrain_armed = None
        self.terrain_radius = 200.0
        self.terrain_drag = None       # {"id", "press": Ansichtspunkt, "origin": Szenenpunkt, "active": bool}
        # Bildvorlage (Werkzeug Nachzeichnen, ``bildvorlage.Nachzeichner``; vom Fenster gesetzt) und ihre Sichtbarkeit
        self.trace = None
        self.show_bild = True
        self.livemap = None          # Live map in true metres (``livemap.LiveMap``; set by the window)
        self.setMouseTracking(True)
        self.document.selection_changed.connect(self._selection)
        self.scale(0.5, 0.5)

    # ------------------------------------------------------------------ Zeichnen (Ebenen)
    def rebuild(self, preview=None, draft=None):
        """Generator-Ebene nur bei neuer Vorschau/Darstellung neu aufbauen, Eingabe-Ebene immer."""
        self.preview = preview
        self.input = input_geometry(self.document) if draft is None else draft
        key = (self.stale, self.bands)
        if preview is not self._preview_obj or key != self._preview_key:
            self._build_preview_layer(preview)
            self._preview_key = key
        self._update_input_layer()
        self.viewport().update()

    def _build_preview_layer(self, preview):
        """Generatorpfade sammeln; gezeichnet werden sie als zwischengespeichertes Bild (drawBackground)."""
        self._preview_items = []
        self._preview_obj = preview
        self._preview_cache = None
        if not preview:
            return
        add = self._preview_items.append
        no_pen = QPen(Qt.NoPen)
        if self.bands:
            # Gleichfarbige Teilstücke gruppieren; Übergänge bleiben Generatorwerte.
            for side in preview["sides"].values():
                for band in range(5):
                    start = 0
                    n = len(side["bands"])
                    while start < n:
                        material = side["bands"][start][band]
                        end = start + 1
                        while end < n and side["bands"][end][band] == material:
                            end += 1
                        ids = list(range(start, end)) + [end % n]
                        polygon = [side["edges"][band][i] for i in ids] + [side["edges"][band + 1][i] for i in reversed(ids)]
                        add((path_for(polygon, True), no_pen, QColor(MATERIALS[material])))
                        start = end
        road = preview["left"] + list(reversed(preview["right"]))
        add((path_for(road, True), no_pen, QColor("#596675")))
        for edge in (preview["left"], preview["right"]):
            add((path_for(edge, True), cosmetic("#F5F5F0", 1), Qt.NoBrush))
        add((path_for([(p[0], p[2]) for p in preview["pos"]], True), cosmetic("#67D9F5", 1.5), Qt.NoBrush))

    def _preview_pixmap(self):
        """Generator-Ebene als Bild; neu gerendert nur bei Zoom/Pan/Größe/Vorschau/Darstellung."""
        if not self._preview_items:
            return None
        t = self.viewportTransform()
        dpr = self.viewport().devicePixelRatioF()
        key = (t.m11(), t.m22(), t.dx(), t.dy(), self.viewport().width(), self.viewport().height(), dpr)
        cache = getattr(self, "_preview_cache", None)
        if cache is not None and cache[0] == key:
            return cache[1]
        pixmap = QPixmap(max(1, int(self.viewport().width() * dpr)), max(1, int(self.viewport().height() * dpr)))
        pixmap.setDevicePixelRatio(dpr)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setTransform(t)
        painter.setOpacity(.25 if self.stale else .65)
        for path, pen, brush in self._preview_items:
            painter.setPen(pen)
            painter.setBrush(brush)
            painter.drawPath(path)
        painter.end()
        self._preview_cache = (key, pixmap)
        return pixmap

    def _update_input_layer(self):
        scene = self.scene()
        if self._input_items is None:
            dotted = cosmetic("#67D9F5", 1, Qt.DotLine)
            self._input_items = {
                "left": scene.addPath(QPainterPath(), dotted),
                "right": scene.addPath(QPainterPath(), dotted),
                "center": scene.addPath(QPainterPath(), cosmetic("#67D9F5", 3)),
                # Klothoiden-Übergänge (mittellinie.uebergaenge.aktiv): Linie mit Übergängen über der Eingabe
                "transitions": scene.addPath(QPainterPath(), cosmetic("#FF9F43", 1.5, Qt.DashLine)),
                "selection": scene.addPath(QPainterPath(), cosmetic("#FFD184", 5)),
            }
            for z, item in enumerate(self._input_items.values()):
                item.setZValue(10 + z)
        items = self._input_items
        pts = [(p[0], p[2]) for p in self.input["pos"]]
        closed = self.input["closed"]
        if pts:
            items["left"].setPath(path_for(self.input["left"], closed))
            items["right"].setPath(path_for(self.input["right"], closed))
            items["center"].setPath(path_for(pts, closed))
            items["transitions"].setPath(path_for(self.input.get("transitions") or [], False))
        else:
            for name in ("left", "right", "center", "transitions"):
                items[name].setPath(QPainterPath())
        self._update_selection_layer(pts)

    def _update_selection_layer(self, pts=None):
        if self._input_items is None:
            return
        if pts is None:
            pts = [(p[0], p[2]) for p in self.input["pos"]]
        path = QPainterPath()
        for i in sorted(self.document.selected_indices):
            if 0 <= i < len(self.input["segments"]):
                entry = self.input["segments"][i]
                path.addPath(path_for(pts[entry["first"]:entry["last"] + 1]))
        self._input_items["selection"].setPath(path)

    def object_positions(self):
        if self.document.ml.get("art") in ("segmente", "kontrollpunkte"):
            if self.input["segments"]:
                return [entry["end"] for entry in self.input["segments"]]
            return self.input["objects"]
        return self.preview["objects"] if self.preview else []

    def fit_track(self):
        points = self.input["pos"] or self.object_positions()
        if self.preview and not points:
            points = self.preview["pos"]
        if not points:
            self.fitInView(QRectF(-250, -200, 500, 400), Qt.KeepAspectRatio)
            return
        xs, zs = [p[0] for p in points], [p[2] for p in points]
        box = QRectF(min(xs), min(zs), max(1, max(xs) - min(xs)), max(1, max(zs) - min(zs)))
        metres_per_pixel = max(box.width() / max(1, self.viewport().width() - 140), box.height() / max(1, self.viewport().height() - 140))
        margin = max(10, metres_per_pixel * 70)
        self.fitInView(box.adjusted(-margin, -margin, margin, margin), Qt.KeepAspectRatio)

    def _selection(self, index):
        data = self.input if self.stale or not self.preview else self.preview
        if 0 <= index < len(data["object_s"]):
            self.station = data["object_s"][index]
            self.station_changed.emit(self.station)
        # Eine Auswahländerung ändert nur die Hervorhebung, nicht die Szene.
        self._update_selection_layer()
        self.viewport().update()

    def focus_selection(self):
        positions = self.object_positions()
        if 0 <= self.document.selected < len(positions):
            point = positions[self.document.selected]
            self.centerOn(point[0], point[2])

    def _to_view(self):
        """Schnelle Abbildung Szene → Ansicht (die Karte wird nie gedreht oder geschert)."""
        t = self.viewportTransform()
        sx, sy, ox, oy = t.m11(), t.m22(), t.dx(), t.dy()
        return lambda x, z: (x * sx + ox, z * sy + oy)

    # ------------------------------------------------------------------ Drehen
    def rotation_frame(self):
        """Szenen-Rechteck (x0, z0, x1, z1) der drehbaren Auswahl oder None."""
        kind = self.document.rotatable()
        if kind is None:
            return None
        pos = self.input["pos"]
        pts = [pos[i] for i in self.document.selected_indices if i < len(pos)] if kind == "punkte" else pos
        if len(pts) < 2:
            return None
        xs, zs = [p[0] for p in pts], [p[2] for p in pts]
        return min(xs), min(zs), max(xs), max(zs)

    def rotation_handle(self):
        """(Griffpunkt, Kasten) in Ansichtskoordinaten oder None."""
        frame = self.rotation_frame()
        if frame is None:
            return None
        to_view = self._to_view()
        box = QRectF(QPointF(*to_view(frame[0], frame[1])), QPointF(*to_view(frame[2], frame[3]))).normalized().adjusted(-8, -8, 8, 8)
        return QPointF(box.center().x(), box.top() - ROTATE_HANDLE_PX), box

    # ------------------------------------------------------------------ Malen
    def drawBackground(self, painter, rect):
        painter.fillRect(rect, QColor("#101820" if self.dark else "#F3F6F8"))
        if self.livemap is not None:
            self.livemap.paint(painter, rect, self.transform().m11())
        if self.grid:
            scale = max(self.transform().m11(), 0.0001)
            step = 10 ** math.ceil(math.log10(45 / scale))
            painter.setPen(cosmetic("#293846" if self.dark else "#CFDAE2"))
            for x in range(math.floor(rect.left() / step), math.ceil(rect.right() / step) + 1):
                painter.drawLine(QPointF(x * step, rect.top()), QPointF(x * step, rect.bottom()))
            for z in range(math.floor(rect.top() / step), math.ceil(rect.bottom() / step) + 1):
                painter.drawLine(QPointF(rect.left(), z * step), QPointF(rect.right(), z * step))
        if self.show_frame:
            shift = self.document.project.manifest.get("versatz", [0, 0, 0])
            painter.setPen(cosmetic("#D4AA61", 1.5, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(QRectF(-1024 - shift[0], -1024 - shift[2], 2048, 2048))
        if self.trace is not None and self.show_bild:
            self.trace.zeichne_bild(painter)
        pixmap = self._preview_pixmap()
        if pixmap is not None:
            painter.save()
            painter.resetTransform()
            painter.drawPixmap(0, 0, pixmap)
            painter.restore()

    def drawForeground(self, painter, rect):
        if self.analysis and self.preview:
            self._draw_analysis_scene(painter)
        self._placed = []
        self._turn_hits = []
        painter.save()
        painter.resetTransform()
        text_color = QColor("#F1F5F9" if self.dark else "#162331")
        to_view = self._to_view()
        width, height = self.viewport().width(), self.viewport().height()
        self._draw_env(painter, to_view, width, height)
        if self.trace is not None and self.mode == "nachzeichnen":
            self.trace.zeichne(painter, to_view)
        selected_set = self.document.selected_indices
        chosen, plain = [], []
        for i, point in enumerate(self.object_positions()):
            x, y = to_view(point[0], point[2])
            if -10 <= x <= width + 10 and -10 <= y <= height + 10:
                if i in selected_set:
                    chosen.append((i, QPointF(x, y)))
                else:
                    plain.append(QPointF(x, y))
        if not self.show_points:
            plain = []                   # Eingabepunkte ausgeblendet; gewählte bleiben sichtbar
        # Punkte gesammelt zeichnen (ein Aufruf je Farbe statt einer Ellipse je Punkt):
        # dicker runder Stift = Ring, dünnerer dunkler Stift = Füllung.
        many = len(chosen) > 8           # bei größeren Gruppen keine Einzelbeschriftung
        for points, outer, inner, colors in ((plain, 8, 5, ("#67D9F5", "#101820")),
                                             ([xy for _, xy in chosen], 10 if many else 14, 6 if many else 10, ("#F1F5F9", "#005C73"))):
            if points:
                polygon = QPolygonF(points)
                for size, color in zip((outer, inner), colors):
                    pen = QPen(QColor(color), size)
                    pen.setCapStyle(Qt.RoundCap)
                    painter.setPen(pen)
                    painter.drawPoints(polygon)
        for i, xy in chosen:
            if many:
                break
            painter.setPen(text_color)
            painter.drawText(xy + QPointF(12, -10).toPoint(), f"{i + 1}" + (tr(" · drag end") if self.input["segments"] else ""))
            if i < len(self.input["segments"]) and self.document.ml["segmente"][i]["typ"] == "bogen":
                middle = self.input["segments"][i]["middle"]
                mid = QPointF(*to_view(middle[0], middle[2]))
                painter.setBrush(QColor("#FFD184"))
                painter.drawRect(QRectF(mid.x() - 5, mid.y() - 5, 10, 10))
                painter.drawText(mid + QPointF(10, 18).toPoint(), tr("Drag radius"))
        if self.preview:
            self._draw_preview_marks(painter, to_view, text_color)
            if self.analysis:
                self._draw_analysis_marks(painter, to_view, text_color)
            self._draw_zones(painter, to_view)
        self._draw_cameras(painter, to_view)
        if self.drag_point and self.rotate_center is None:
            painter.setPen(QPen(QColor("#FFD184"), 2, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(self.mapFromScene(self.drag_point), 8, 8)
        self._draw_rotation(painter, to_view, text_color)
        if self.rubber_origin is not None and self.rubber_end is not None:
            painter.setPen(QPen(QColor("#67D9F5"), 1, Qt.DashLine))
            painter.setBrush(QColor(103, 217, 245, 40))
            painter.drawRect(QRectF(self.rubber_origin, self.rubber_end).normalized())
        gesture = self.drag_point is not None or self.rotate_center is not None
        gesture = gesture or self.env_drag is not None and self.env_drag["active"] or self.env_area is not None
        gesture = gesture or self.terrain_drag is not None and self.terrain_drag["active"]
        state = (tr("Release applies · Esc cancels") if gesture else
                 tr("no track yet") if not self.document.points and not self.input["segments"] else
                 tr("Preview outdated – being recalculated") if self.stale else
                 tr("Preview up to date") if self.preview else
                 tr("Close the lap for the preview") if not self.document.project.closed and self.document.points else
                 tr("Preview pending"))
        note = f"  ·  {self.analysis_note}" if self.analysis_note and not gesture else ""
        # Oben links eine Zeile für das Wichtige: Werkzeug und aktuelle Vorschau stehen schon in Leiste und Statuszeile
        top = 12
        if (self.show_hints or gesture) and (note or state != tr("Preview up to date") or self.mode != "auswahl"):
            names = {"punkte": tr("Set points"), "umgebung": tr("Environment"), "streuen": tr("Plant area"),
                     "nachzeichnen": tr("Trace"), "kameras": tr("Cameras")}
            self._chip(painter, 12, top, f"{names.get(self.mode, tr('Select'))}  ·  {state}{note}",
                       "#D6A94A" if self.stale and not gesture else None)
            top += 30
        count = len(selected_set)
        if self.rotate_center is not None:
            hint = tr("Rotate {winkel:+.1f}° · Shift: {raster:.0f}° steps · Esc: cancel", winkel=math.degrees(self.rotate_angle),
                      raster=SNAP_DEG)
        elif self.mode in ENV_MODES:
            hint = self._env_hint()
        elif self.mode == "nachzeichnen" and self.trace is not None:
            hint = self.trace.hinweis()
        elif self.mode == "kameras":
            hint = tr("Drag a diamond: move the camera · drag a bar: move the trigger zone along the track · "
                      "click: select · V or Esc: back to selection")
        elif self.mode == "punkte":
            hint = (tr("Click sets a point · from 4 points “Close lap” · V or Esc: back to selection")
                    if not self.document.project.closed else
                    tr("Click inserts a point at the nearest place on the track · V or Esc: back to selection"))
        elif self.document.rotatable():
            what = tr("whole track") if self.document.rotatable() == "strecke" else tr("{count} points", count=count)
            hint = tr("{what} selected · drag or arrow keys: move · round handle: rotate · Del: delete", what=what)
        elif count == 1:
            hint = (tr("Drag end: length/angle · yellow square: radius · right: change values") if self.input["segments"]
                    else tr("Drag point: move · Del: delete · right: height and width"))
        elif not self.document.points and not self.input["segments"]:
            hint = ""
        else:
            hint = (tr(("Click selects · drag a box to select several · double-click on the line inserts a point · "
                        "F1: help"))
                    if not self.input["segments"] else
                    tr("Click selects a segment · Ctrl+A: move/rotate the whole track · F1: help"))
        if hint and (self.show_hints or gesture):
            self._chip(painter, 12, top, hint)                # oben: unten lag der Hinweis über der Strecke
        if self.show_frame:
            shift = self.document.project.manifest.get("versatz", [0, 0, 0])
            corner = QPointF(*to_view(-1024 - shift[0], -1024 - shift[2]))
            if -200 < corner.x() < width - 60 and -20 < corner.y() < height - 20:
                painter.setPen(QColor("#D4AA61"))
                painter.drawText(corner + QPointF(6, 16), tr("Play area ±1024 m"))
        if self.show_scale:
            self._scale_bar(painter, width, height, text_color)
        if self.livemap is not None:
            self.livemap.chrome(painter, width, height)
        if self.overlap_marks and self.overlap_revision == self.document.revision:
            painter.setPen(QPen(QColor("#E5484D"), 3))
            painter.setBrush(Qt.NoBrush)
            for x, z in self.overlap_marks:
                painter.drawEllipse(QPointF(*to_view(x, z)), 9, 9)
        painter.restore()

    def set_overlap_marks(self, marks):
        """Rote Ringe an Punkten eines eingefügten Stücks, die die Strecke überschneiden (bis zur nächsten Änderung)."""
        self.overlap_marks, self.overlap_revision = list(marks), self.document.revision
        self.viewport().update()

    # ------------------------------------------------------------------ Umgebung (Zeichnen)
    def env_radius_m(self, objekt):
        """Radius eines Umgebungsobjekts auf der Karte (Katalogradius mal Skala)."""
        return umgebung.objekt_radius(objekt, self.env_index)

    def _env_hint(self):
        if not self.document.env_editable:
            return tr("Environment read-only: umgebung.json is unreadable or the project has an unknown version")
        if self.mode == "streuen":
            if self.env_form == "pinsel":
                return tr(("Paint with the mouse button held: planting happens there · radius and density in the "
                           "“Environment” tab · Esc: back"))
            return tr("Drag a rectangle: the area is planted · density in the “Environment” tab · Esc: back")
        if self.terrain_armed:
            return (tr("Click places a hill (inside the track area, negative height = hollow) · Esc: back")
                    if self.terrain_armed == "huegel" else
                    tr("Click places a mountain (outside the track area; zoom out) · Esc: back"))
        form = self.document.terrain_form(self.document.terrain_selected) if self.document.terrain_selected else None
        if form is not None:
            return tr(("“{name}” selected · drag the centre: move · height and radius in the “Environment” tab · Del: "
                       "delete"),
                      name=form["id"])
        if self.env_model:
            return tr("Click places “{modell}” · click an object to select it · Esc: deselect model",
                      modell=umgebung.kurzname(self.env_model))
        if self.document.env_selected:
            return tr("{anzahl} objects selected · drag: move · Del: delete · double-click: whole group",
                      anzahl=len(self.document.env_selected))
        return tr("Choose a model in the catalogue and click to place · drag a frame to select several · Esc: back")

    def _draw_env(self, painter, to_view, width, height):
        """Umgebungsebene: Kreise in Katalogröße, Farbe je Kategorie; Auswahl hell, Ziehen/Bepflanzen als Entwurf."""
        document = self.document
        active = self.mode in ENV_MODES
        scale = abs(self.transform().m11())
        if self.show_env and document.terrain_forms:
            self._draw_terrain(painter, to_view, width, height, scale, active)
        if self.show_env and document.env_objects:
            painter.save()
            painter.setBrush(Qt.NoBrush)
            selected = document.env_selected
            offset = (self.env_delta.x() * scale, self.env_delta.y() * scale) if self.env_drag and self.env_drag["active"] else (0, 0)
            colors, points, chosen = {}, {}, []
            for o in document.env_objects:
                x, y = to_view(o["x"], o["z"])
                marked = o["id"] in selected
                if marked:
                    x, y = x + offset[0], y + offset[1]
                radius = self.env_radius_m(o) * scale
                if not (-radius - 4 <= x <= width + radius + 4 and -radius - 4 <= y <= height + radius + 4):
                    continue
                if marked:
                    chosen.append((x, y, radius))
                    continue
                color = umgebung.objekt_farbe(o, self.env_index)
                if radius < 2.0:
                    points.setdefault(color, []).append(QPointF(x, y))
                    continue
                if color not in colors:
                    fill, line = QColor(color), QColor(color)
                    fill.setAlpha(80 if active else 45)
                    line.setAlpha(220 if active else 120)
                    colors[color] = (QBrush(fill), QPen(line, 1))
                brush, pen = colors[color]
                painter.setBrush(brush)
                painter.setPen(pen)
                painter.drawEllipse(QPointF(x, y), radius, radius)
            for color, pts in points.items():
                pen = QPen(QColor(color), 3)
                pen.setCapStyle(Qt.RoundCap)
                painter.setPen(pen)
                painter.drawPoints(QPolygonF(pts))
            for x, y, radius in chosen:
                painter.setBrush(QColor(255, 255, 255, 45))
                painter.setPen(QPen(QColor("#FFFFFF"), 2.5))
                painter.drawEllipse(QPointF(x, y), max(radius, 4.0), max(radius, 4.0))
                painter.setPen(QPen(QColor("#FFD184"), 4))
                painter.drawPoint(QPointF(x, y))
            painter.restore()
        if not active:
            return
        painter.save()
        painter.setBrush(Qt.NoBrush)
        if self.env_rubber is not None:
            painter.setPen(QPen(QColor("#9CCC65"), 1, Qt.DashLine))
            painter.setBrush(QColor(156, 204, 101, 40))
            painter.drawRect(QRectF(self.env_rubber["a"], self.env_rubber["b"]).normalized())
        area = self.env_area
        if area is not None:
            painter.setPen(QPen(QColor("#9CCC65"), 1.5, Qt.DashLine))
            painter.setBrush(QColor(156, 204, 101, 45))
            if area["form"] == "rechteck":
                a, b = QPointF(*to_view(area["a"].x(), area["a"].y())), QPointF(*to_view(area["b"].x(), area["b"].y()))
                painter.drawRect(QRectF(a, b).normalized())
            else:
                for p in area["punkte"]:
                    c = QPointF(*to_view(p[0], p[1]))
                    painter.drawEllipse(c, self.env_radius * scale, self.env_radius * scale)
        elif self.env_hover is not None and self.viewport().rect().contains(self.env_hover.toPoint()):
            hover = self.env_hover
            if self.mode == "streuen" and self.env_form == "pinsel":
                painter.setPen(QPen(QColor("#9CCC65"), 1.5, Qt.DashLine))
                painter.drawEllipse(hover, self.env_radius * scale, self.env_radius * scale)
            elif self.mode == "umgebung" and self.terrain_armed:
                painter.setPen(QPen(QColor(TERRAIN_FARBEN[self.terrain_armed]), 1.5, Qt.DashLine))
                painter.drawEllipse(hover, max(self.terrain_radius * scale, 4.0), max(self.terrain_radius * scale, 4.0))
                painter.drawLine(hover + QPointF(-6, 0), hover + QPointF(6, 0))
                painter.drawLine(hover + QPointF(0, -6), hover + QPointF(0, 6))
            elif self.mode == "umgebung" and self.env_model:
                radius = (self.env_index.get(self.env_model) or {}).get("radius_m") or umgebung.UNBEKANNT_RADIUS_M
                painter.setPen(QPen(QColor("#FFFFFF"), 1.5, Qt.DashLine))
                painter.drawEllipse(hover, max(radius * scale, 4.0), max(radius * scale, 4.0))
        painter.restore()

    def _draw_terrain(self, painter, to_view, width, height, scale, active):
        """Hügel, Senken und Berge: Kreis mit weichem Verlauf zur Mitte, Höhe als Beschriftung; Fehllage rot umrandet."""
        document = self.document
        shift = document.project.manifest.get("versatz", [0, 0, 0])
        drag = self.terrain_drag if self.terrain_drag and self.terrain_drag["active"] else None
        painter.save()
        for art, f in document.terrain_forms:
            x, y = to_view(f["x"], f["z"])
            chosen = f["id"] == document.terrain_selected
            if drag is not None and drag["id"] == f["id"]:
                x, y = x + self.env_delta.x() * scale, y + self.env_delta.y() * scale
            radius = f["radius_m"] * scale
            if not (-radius - 40 <= x <= width + radius + 40 and -radius - 40 <= y <= height + radius + 40):
                continue
            color = QColor(TERRAIN_FARBEN["senke" if art == "huegel" and f["hoehe_m"] < 0 else art])
            gradient = QRadialGradient(QPointF(x, y), max(radius, 1.0))
            inner, outer = QColor(color), QColor(color)
            inner.setAlpha(110 if active else 60)
            outer.setAlpha(0)
            gradient.setColorAt(0.0, inner)
            gradient.setColorAt(1.0, outer)
            painter.setBrush(QBrush(gradient))
            problem = umgebung.form_problem(art, f, shift)
            line = QColor("#FF6B6B") if problem else QColor("#FFFFFF") if chosen else QColor(color)
            painter.setPen(QPen(line, 2.5 if chosen else 1.2, Qt.SolidLine if chosen else Qt.DashLine))
            painter.drawEllipse(QPointF(x, y), max(radius, 5.0), max(radius, 5.0))
            painter.setPen(QPen(line, 2))
            painter.drawLine(QPointF(x - 5, y), QPointF(x + 5, y))
            painter.drawLine(QPointF(x, y - 5), QPointF(x, y + 5))
            if radius > 18 or chosen:
                painter.setPen(QColor("#F1F5F9" if self.dark else "#162331"))
                text = f"{'▲' if art == 'berge' else ''}{f['hoehe_m']:+.0f} m"
                if chosen and problem:
                    text += "  ·  " + tr(problem)
                painter.drawText(QPointF(x + 8, y - 8), text)
        painter.restore()

    def terrain_nearest(self, position):
        """Kennung des Hügels/Bergs, dessen Mitte (Kreuz, 12 px) oder innerer Bereich (ein Drittel des Radius) unter
        der Ansichtsposition liegt, sonst None. Kleinere Formen gewinnen."""
        to_view = self._to_view()
        scale = abs(self.transform().m11())
        best, best_key = None, None
        for _, f in self.document.terrain_forms:
            x, y = to_view(f["x"], f["z"])
            d = math.hypot(position.x() - x, position.y() - y)
            if d <= max(12.0, f["radius_m"] * scale / 3.0):
                key = (d > 12.0, f["radius_m"], d)
                if best_key is None or key < best_key:
                    best, best_key = f["id"], key
        return best

    def _chip(self, painter, x, y, text, accent=None):
        """Halbtransparentes Schild mit Text (oben Zustand, unten Kontext-Hinweis)."""
        metrics = painter.fontMetrics()
        rect = QRectF(x, y, metrics.horizontalAdvance(text) + 20, metrics.height() + 10)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(16, 24, 32, 200) if self.dark else QColor(255, 255, 255, 215))
        painter.drawRoundedRect(rect, 5, 5)
        painter.setPen(QColor(accent) if accent else QColor("#F1F5F9" if self.dark else "#162331"))
        painter.drawText(rect.adjusted(10, 0, -10, 0), Qt.AlignVCenter | Qt.AlignLeft, text)

    def _scale_bar(self, painter, width, height, text_color):
        """Maßstab unten rechts (runde Länge, 60–160 Pixel)."""
        scale = abs(self.transform().m11())
        if scale <= 0:
            return
        meters = next((m for m in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000) if m * scale >= 60), 5000)
        pixels = meters * scale
        x1, y = width - 24, height - 22
        x0 = x1 - pixels
        painter.setPen(QPen(text_color, 2))
        painter.drawLine(QPointF(x0, y), QPointF(x1, y))
        painter.drawLine(QPointF(x0, y - 5), QPointF(x0, y + 1))
        painter.drawLine(QPointF(x1, y - 5), QPointF(x1, y + 1))
        label = f"{meters // 1000} km" if meters >= 1000 else f"{meters} m"
        painter.drawText(QRectF(x0, y - 22, pixels, 16), Qt.AlignCenter, label)

    def _draw_preview_marks(self, painter, to_view, text_color):
        painter.save()
        if self.stale:
            painter.setOpacity(.3)
        values = self.preview
        a, b = self.mapFromScene(QPointF(*values["left"][0])), self.mapFromScene(QPointF(*values["right"][0]))
        painter.setPen(QPen(QColor("#101820"), 7))
        painter.drawLine(a, b)
        painter.setPen(QPen(QColor("#FFFFFF"), 3, Qt.DashLine))
        painter.drawLine(a, b)
        if self.highlight == "start":
            painter.setPen(QPen(QColor(255, 209, 132, 150), 13))
            painter.drawLine(a, b)
            painter.setPen(QPen(QColor("#FFFFFF"), 3, Qt.DashLine))
            painter.drawLine(a, b)
        painter.setPen(text_color)
        anchor, label = QPointF(a) + QPointF(8, -10), tr("FINISH · 0 m")
        metrics = painter.fontMetrics()           # Platz belegen: spätere Schilder (DRS, Straight Mode) weichen aus
        self._placed.append(QRectF(anchor.x() - 4, anchor.y() - metrics.ascent() - 2,
                                   metrics.horizontalAdvance(label) + 8, metrics.height() + 3))
        painter.drawText(anchor, label)
        if self.direction:
            stride = max(1, len(values["pos"]) // 12)
            painter.setPen(QPen(QColor("#F1F5F9"), 2))
            for i in range(0, len(values["pos"]) - 1, stride):
                p, q = values["pos"][i], values["pos"][i + 1]
                origin = QPointF(*to_view(p[0], p[2]))
                angle = math.atan2(q[2] - p[2], q[0] - p[0])
                for delta in (-0.55, 0.55):
                    painter.drawLine(origin, QPointF(origin.x() - 10 * math.cos(angle + delta), origin.y() - 10 * math.sin(angle + delta)))
        stations = values["s"]
        if stations:
            index = min(bisect.bisect_left(stations, self.station), len(stations) - 1)
            if index > 0 and abs(stations[index - 1] - self.station) <= abs(stations[index] - self.station):
                index -= 1
            pos = values["pos"][index]
            painter.setPen(QPen(QColor("#FFD184"), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(QPointF(*to_view(pos[0], pos[2])), 9, 9)
        painter.restore()

    # ------------------------------------------------------------------ Fahrlinie (Hintergrundlauf)
    LINE_COLORS = {"b": "#F07878", "r": "#D6A94A", "g": "#5BD08A"}     # Bremsen, Rollen/Teillast, Vollgas

    def _line_path_cache(self):
        if self._line_paths[0] is self.analysis:
            return self._line_paths[1]
        line = self.analysis["linie"]
        paths = {key: QPainterPath() for key in self.LINE_COLORS}
        xs, zs, states = line["x"], line["z"], line["zustand"]
        n = len(xs)
        for i in range(n):
            j = (i + 1) % n
            path = paths.get(states[i])
            if path is None:
                continue
            if i == 0 or states[i - 1] != states[i]:
                path.moveTo(xs[i], zs[i])
            path.lineTo(xs[j], zs[j])
        box = self.analysis.get("boxengasse")
        pit = path_for(list(zip(box["x"], box["z"]))) if box else None
        self._line_paths = (self.analysis, (paths, pit))
        return self._line_paths[1]

    def _draw_analysis_scene(self, painter):
        """Ideallinie (nach Bremsen/Rollen/Gas gefärbt) und Boxengassenlinie in Szenenkoordinaten."""
        paths, pit = self._line_path_cache()
        painter.save()
        if self.analysis_stale:
            painter.setOpacity(.35)
        painter.setBrush(Qt.NoBrush)
        if self.show_race and pit is not None:
            painter.setPen(cosmetic("#F0A040", 2, Qt.DashLine))
            painter.drawPath(pit)
        if self.show_line:
            for key, path in paths.items():
                pen = cosmetic(self.LINE_COLORS[key], 2.5)
                pen.setCapStyle(Qt.RoundCap)
                painter.setPen(pen)
                painter.drawPath(path)
        painter.restore()

    def _free_box(self, point, width, height, metrics):
        """Platz für eine Beschriftung ohne Überlappung (bis zu fünf Ausweichlagen), sonst None."""
        for dx, dy in ((0, 0), (0, height + 2), (0, -height - 2), (14, 2 * height + 4), (14, -2 * height - 4)):
            box = QRectF(point.x() + dx, point.y() + dy - metrics.ascent() - 2, width, height)
            if not any(box.intersects(other) for other in self._placed):
                self._placed.append(box)
                return box
        return None

    def _label(self, painter, point, text, color, force=False):
        metrics = painter.fontMetrics()
        width, height = metrics.horizontalAdvance(text) + 8, metrics.height() + 3
        box = self._free_box(point, width, height, metrics)
        if box is None:
            if not force:
                return None
            box = QRectF(point.x(), point.y() - metrics.ascent() - 2, width, height)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(16, 24, 32, 205) if self.dark else QColor(255, 255, 255, 225))
        painter.drawRoundedRect(box, 3, 3)
        painter.setPen(QColor(color))
        painter.drawText(QPointF(box.left() + 4, box.top() + metrics.ascent() + 2), text)
        return box

    def _draw_analysis_marks(self, painter, to_view, text_color):
        """Kurvennummern, Bremspunkte, Entfernungstafeln, Startplätze, Boxen und Sektorgrenzen."""
        a = self.analysis
        scale = abs(self.transform().m11())            # Pixel je Meter
        painter.save()
        if self.analysis_stale:
            painter.setOpacity(.4)
        if self.show_race:
            painter.setPen(Qt.NoPen)
            grid_size = 5.5 if self.highlight == "grid" else 3.0
            for points, color, size in ((a.get("startplaetze") or [], "#F1F5F9", grid_size),
                                        (a.get("boxenplaetze") or [], "#F0A040", 3.5)):
                painter.setBrush(QColor(color))
                for x, z in points:
                    painter.drawEllipse(QPointF(*to_view(x, z)), size, size)
            if self.preview.get("left") and self.preview.get("right"):
                for sector in a.get("sektoren") or []:
                    if sector["nummer"] == 1:
                        continue                         # Sektor 1 beginnt an der Ziellinie
                    i = self.station_at(sector["s_start"])
                    p = QPointF(*to_view(*self.preview["left"][i]))
                    q = QPointF(*to_view(*self.preview["right"][i]))
                    painter.setPen(QPen(QColor("#E3C341"), 6 if self.highlight == "sektoren" else 3))
                    painter.drawLine(p, q)
                    self._label(painter, p + QPointF(6, -6), "S%d" % sector["nummer"], "#E3C341", force=True)
        if self.show_line:
            for zone in a.get("bremszonen") or []:
                p = QPointF(*to_view(zone["x"], zone["z"]))
                painter.setPen(QPen(QColor("#101820"), 1))
                painter.setBrush(QColor("#F07878"))
                painter.drawPolygon(QPolygonF([p + QPointF(-6, -9), p + QPointF(6, -9), p]))
                if scale >= 1.2:                         # km/h erst beim Hineinzoomen (sonst überladen)
                    self._label(painter, p + QPointF(8, -8), tr("{v_start:.0f}→{v_ende:.0f} km/h", v_start=zone['v_start'], v_ende=zone['v_ende']), "#F07878")
            for board in a.get("tafeln") or []:
                p = QPointF(*to_view(board["x"], board["z"]))
                painter.setPen(QPen(QColor("#101820"), 1))
                painter.setBrush(QColor("#F1F5F9"))
                painter.drawRect(QRectF(p.x() - 3, p.y() - 3, 6, 6))
                if scale >= 1.5:
                    self._label(painter, p + QPointF(6, 4), str(board["meter"]), "#F1F5F9")
        if self.show_turns:
            shown = set()
            for curve in a.get("kurven") or []:
                if curve["spiel"] in shown:
                    continue
                shown.add(curve["spiel"])
                pos = self.preview["pos"][self.station_at(curve["s_scheitel"])]
                box = self._label(painter, QPointF(*to_view(pos[0], pos[2])) + QPointF(10, -10), "T%d" % curve["spiel"],
                                  "#FFD184", force=True)
                self._turn_hits.append((box, curve["spiel"]))
        if self.focus_curve is not None:
            curve = self.focus_curve
            pos = self.preview["pos"][self.station_at(curve["s_scheitel"])]
            p = QPointF(*to_view(pos[0], pos[2]))
            painter.setPen(QPen(QColor("#FFD184"), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(p, 14, 14)
            self._label(painter, p + QPointF(18, 26), curve.get("info", ""), "#FFD184", force=True)
        painter.restore()

    # ------------------------------------------------------------------ DRS / Straight Mode
    ZONE_FARBEN = {"drs": "#3DDC84", "xmode": "#B67CFF", "box": "#F0A040"}
    ZONE_FELDER = {"drs": (("erkennung_m", "det"), ("aktivierung_m", "a"), ("ende_m", "b")),
                   "xmode": (("von_m", "a"), ("bis_m", "b")),
                   "box": (("einfahrt_vor_start_m", "a"), ("ausfahrt_nach_start_m", "b"))}

    def _length(self):
        stations = self.preview["s"]
        return float(self.preview.get("length") or (stations[-1] if stations else 1.0))

    def station_at(self, s):
        """Index der Vorschau-Station (Meter ab Ziel) am nächsten zu s."""
        stations = self.preview["s"]
        s %= self._length()
        i = min(bisect.bisect_left(stations, s), len(stations) - 1)
        if i > 0 and abs(stations[i - 1] - s) <= abs(stations[i] - s):
            i -= 1
        return i

    def station_from_view(self, point):
        """Station ab Ziel des Mittellinienpunkts, der einer Ansichtsposition am nächsten liegt."""
        to_view = self._to_view()
        px, py = point.x(), point.y()
        pos, stations = self.preview["pos"], self.preview["s"]
        best, best_d = 0, float("inf")
        for i, p in enumerate(pos):
            x, y = to_view(p[0], p[2])
            d = (x - px) ** 2 + (y - py) ** 2
            if d < best_d:
                best, best_d = i, d
        return float(stations[best])

    @staticmethod
    def _offset(piece, px):
        """Polylinie in der Ansicht um px Pixel nach links (Fahrtrichtung) versetzen."""
        if not px or len(piece) < 2:
            return piece
        out = []
        for i, p in enumerate(piece):
            a, b = piece[max(0, i - 1)], piece[min(len(piece) - 1, i + 1)]
            dx, dy = b.x() - a.x(), b.y() - a.y()
            n = max(1e-6, (dx * dx + dy * dy) ** .5)
            out.append(QPointF(p.x() + dy / n * px, p.y() - dx / n * px))
        return out

    def _zone_polyline(self, a, b, to_view):
        stations, pos = self.preview["s"], self.preview["pos"]
        length = self._length()
        span = (b - a) % length
        pieces, current = [], []
        for i, s in enumerate(stations):
            if (s - a) % length <= span:
                current.append(QPointF(*to_view(pos[i][0], pos[i][2])))
            elif current:
                pieces.append(current)
                current = []
        if current:
            if pieces and (stations[0] - a) % length <= span:   # über die Ziellinie: Enden verbinden
                pieces[0] = current + pieces[0]
            else:
                pieces.append(current)
        return pieces

    def zone_handles(self):
        """[(feld, Ansichtspunkt)] der gewählten eigenen Zone."""
        if not self.preview or not self.preview.get("s") or self.zone_selected is None:
            return []
        zone = next((z for z in self.zones if (z["art"], z["index"]) == self.zone_selected and not z["auto"]), None)
        if zone is None:
            return []
        to_view = self._to_view()
        out = []
        for feld, key in self.ZONE_FELDER[zone["art"]]:
            if zone.get(key) is None:
                continue
            p = self.preview["pos"][self.station_at(zone[key])]
            out.append((feld, QPointF(*to_view(p[0], p[2]))))
        return out

    def _draw_zones(self, painter, to_view):
        if not self.zones or not self.show_zones or not self.preview.get("s"):
            return
        painter.save()
        for zone in self.zones:
            color = QColor(self.ZONE_FARBEN[zone["art"]])
            selected = (zone["art"], zone["index"]) == self.zone_selected
            color.setAlpha(110 if zone["auto"] else 230 if selected else 170)
            pen = QPen(color, {"xmode": 14, "box": 6}.get(zone["art"], 8), Qt.DashLine if zone["auto"] else Qt.SolidLine)
            pen.setCapStyle(Qt.FlatCap)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            # DRS seitlich versetzt, damit es neben einer Straight-Mode-Zone sichtbar bleibt; Boxengasse auf ihrer Seite
            shift = {"drs": 13, "box": 20 if zone.get("seite") == "links" else -20}.get(zone["art"], 0)
            pieces = [self._offset(piece, shift) for piece in self._zone_polyline(zone["a"], zone["b"], to_view)]
            for piece in pieces:
                if len(piece) > 1:
                    painter.drawPolyline(QPolygonF(piece))
            if zone.get("det") is not None:
                p = self.preview["pos"][self.station_at(zone["det"])]
                painter.setPen(QPen(color, 3))
                painter.drawEllipse(QPointF(*to_view(p[0], p[2])), 5, 5)
            zone["_anchor"] = pieces[0][len(pieces[0]) // 2] if pieces and pieces[0] else None
        # Beschriftungen nach allen Bändern, mit dunklem Hintergrund (lesbar über Bändern und Linien)
        for zone in self.zones:
            anchor = zone.get("_anchor")
            if anchor is None or not self.show_labels:
                continue
            text = zone["label"] + (tr(" (automatic)") if zone["auto"] else "")
            pos = anchor + (QPointF(10, -14) if zone["art"] == "xmode" else QPointF(10, 24))
            selected = (zone["art"], zone["index"]) == self.zone_selected
            self._label(painter, pos, text, QColor(self.ZONE_FARBEN[zone["art"]]).lighter(130).name(), force=selected)
        if self.zone_selected is not None:
            for feld, point in self.zone_handles():
                painter.setPen(QPen(QColor("#F1F5F9"), 2))
                painter.setBrush(QColor(self.ZONE_FARBEN[self.zone_selected[0]]))
                painter.drawRect(QRectF(point.x() - 6, point.y() - 6, 12, 12))
        painter.restore()

    CAMERA_FARBE = "#C4B5FD"

    def _draw_cameras(self, painter, to_view):
        """Auslösezonen als Balken quer über der Strecke, Kameras als Rauten; gepunktete Linien zeigen, welche Kameras
        eine Zone laut EventGraph schaltet (oft zufällig eine von mehreren)."""
        self._camera_hits = []
        if not self.cameras or not (self.show_cameras or self.camera_group is not None or self.mode == "kameras"):
            return
        painter.save()
        drag = self.camera_drag if self.camera_drag and self.camera_drag.get("aktiv") else None

        def ansicht(e):
            if drag is not None and e["name"] == drag["name"]:
                if e["template"].startswith("TriggerZone"):
                    return drag.get("ansicht_zone") or to_view(e["position"][0], e["position"][2])
                return drag["punkt"].x(), drag["punkt"].y()
            return to_view(e["position"][0], e["position"][2])
        je_name = {e["name"]: e for e in self.cameras}
        zonen = [e for e in self.cameras if e["template"].startswith("TriggerZone")]
        gewaehlt_namen = set()
        for z in zonen:
            if z["gruppe"] == self.camera_group:
                gewaehlt_namen.add(z["name"])
                gewaehlt_namen.update(z.get("kameras") or [])
        for z in zonen:                                   # Verknüpfungen zuerst (unter den Symbolen)
            for name in z.get("kameras") or []:
                k = je_name.get(name)
                if k is None:
                    continue
                aktiv = z["name"] in gewaehlt_namen
                farbe = QColor("#7C8796" if z["aus"] else ("#F1F5F9" if aktiv else self.CAMERA_FARBE))
                farbe.setAlpha(230 if aktiv else 120)
                painter.setPen(QPen(farbe, 1.5 if aktiv else 1, Qt.DotLine))
                painter.drawLine(QPointF(*ansicht(z)), QPointF(*ansicht(k)))
        for e in self.cameras:
            gewaehlt = e["name"] in gewaehlt_namen
            farbe = QColor("#7C8796" if e["aus"] else ("#F1F5F9" if gewaehlt else self.CAMERA_FARBE))
            x, y = ansicht(e)
            if e["template"].startswith("TriggerZone"):
                q = e.get("quer") or [1.0, 0.0, 0.0]
                skala = to_view(1.0, 0.0)[0] - to_view(0.0, 0.0)[0]
                a = (x - 12 * q[0] * skala, y - 12 * q[2] * skala)
                b = (x + 12 * q[0] * skala, y + 12 * q[2] * skala)
                pen = QPen(farbe, 5 if gewaehlt else 3, Qt.DashLine if e["aus"] else Qt.SolidLine)
                pen.setCapStyle(Qt.FlatCap)
                painter.setPen(pen)
                painter.drawLine(QPointF(*a), QPointF(*b))
            elif e["template"] != "Spline":
                r = 6 if gewaehlt else 4
                painter.setPen(QPen(QColor("#101820"), 1))
                painter.setBrush(farbe)
                painter.drawPolygon(QPolygonF([QPointF(x, y - r), QPointF(x + r, y), QPointF(x, y + r), QPointF(x - r, y)]))
            self._camera_hits.append((QPointF(x, y), e["gruppe"], e["name"], e["template"]))
        if self.camera_group is not None and self.show_labels:
            z = next((z for z in zonen if z["gruppe"] == self.camera_group), None)
            if z is not None:
                self._label(painter, QPointF(*to_view(z["position"][0], z["position"][2])) + QPointF(10, -14),
                            self.camera_group, self.CAMERA_FARBE, force=True)
        painter.restore()

    def camera_at(self, position, radius=9.0):
        """Kameragruppe unter dem Mauszeiger (Zone oder Kamera) oder None."""
        treffer = self.camera_hit(position, radius)
        return treffer[0] if treffer else None

    def camera_hit(self, position, radius=9.0):
        """(Gruppe, Instanzname, Vorlage) unter dem Mauszeiger oder None; Kameras vor Zonen."""
        best, best_d = None, radius
        for point, gruppe, name, template in self._camera_hits:
            d = math.hypot(point.x() - position.x(), point.y() - position.y()) - (2.0 if "Zone" not in template else 0.0)
            if d < best_d:
                best, best_d = (gruppe, name, template), d
        return best

    def _zone_auf_strecke(self, position):
        """Nächster Punkt der Vorschau-Mittellinie zu einem Ansichtspunkt (Ansichtskoordinaten) für das Ziehen einer Zone."""
        if not self.preview or not self.preview.get("pos"):
            return None
        to_view = self._to_view()
        best, best_d = None, float("inf")
        for p in self.preview["pos"]:
            x, y = to_view(p[0], p[2])
            d = (x - position.x()) ** 2 + (y - position.y()) ** 2
            if d < best_d:
                best, best_d = (x, y), d
        return best

    def _draw_rotation(self, painter, to_view, text_color):
        if self.rotate_center is not None:
            center = QPointF(*to_view(self.rotate_center.x(), self.rotate_center.y()))
            painter.setPen(QPen(QColor("#FFD184"), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawLine(center + QPointF(-8, 0), center + QPointF(8, 0))
            painter.drawLine(center + QPointF(0, -8), center + QPointF(0, 8))
            if self.drag_point is not None:
                painter.setPen(QPen(QColor("#FFD184"), 1, Qt.DashLine))
                painter.drawLine(center, self.mapFromScene(self.drag_point))
            painter.setPen(text_color)
            painter.drawText(center + QPointF(12, -12), f"{math.degrees(self.rotate_angle):+.1f}°")
            return
        if self.drag_point is not None or self.rubber_origin is not None:
            return
        handle = self.rotation_handle()
        if handle is None:
            return
        point, box = handle
        painter.setPen(QPen(QColor("#FFD184"), 1, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(box)
        painter.drawLine(QPointF(box.center().x(), box.top()), point)
        painter.setPen(QPen(QColor("#FFD184"), 2))
        painter.setBrush(QColor("#101820"))
        painter.drawEllipse(point, 7, 7)
        painter.setPen(text_color)
        painter.drawText(point + QPointF(12, 5), tr("rotate"))

    # ------------------------------------------------------------------ Eingaben
    def wheelEvent(self, event):
        if (event.modifiers() & Qt.AltModifier and event.modifiers() & Qt.ShiftModifier
                and self.livemap is not None):
            delta = event.angleDelta().y() or event.pixelDelta().y()
            scene = self.mapToScene(event.position().toPoint())
            if self.livemap.wheel(delta, scene.x(), scene.y()):
                event.accept()
                return
        # Stufenlos nach Raddrehung (120 = eine Raste = Faktor 1,2): Touchpads und feine Räder melden viele kleine
        # Schritte, die früher je 1,2 zoomten (eine Raste ≈ Faktor 4). Untergrenze: Szene füllt das Fenster, sonst
        # zentriert Qt sie und der Punkt unter der Maus springt weg.
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if delta:
            r = self.sceneRect()
            vp = self.viewport()
            minimum = max(0.02, vp.width() / r.width(), vp.height() / r.height())
            scale = self.transform().m11()
            target = min(50.0, max(minimum, scale * 1.2 ** (delta / 120.0)))
            if abs(target - scale) > 1e-9:
                # Anker selbst setzen: AnchorUnderMouse nimmt die letzte Mausbewegung, nicht die Radposition
                pos = event.position().toPoint()
                vorher = self.mapToScene(pos)
                anker = self.transformationAnchor()
                self.setTransformationAnchor(QGraphicsView.NoAnchor)
                self.scale(target / scale, target / scale)
                self.setTransformationAnchor(anker)
                nachher = self.mapToScene(pos)
                mitte = self.mapToScene(self.viewport().rect().center())
                self.centerOn(mitte + (vorher - nachher))
        event.accept()

    def nearest_object(self, point):
        positions = self.object_positions()
        if not positions:
            return -1, float("inf")
        to_view = self._to_view()
        px, py = point.x(), point.y()
        best, best_d = -1, float("inf")
        for i, p in enumerate(positions):
            x, y = to_view(p[0], p[2])
            d = abs(x - px) + abs(y - py)
            if d < best_d:
                best, best_d = i, d
        return best, best_d

    def mousePressEvent(self, event):
        self.setFocus()
        if (self.livemap is not None and event.button() == Qt.LeftButton
                and event.modifiers() & Qt.AltModifier):
            scene = self.mapToScene(event.position().toPoint())
            if self.livemap.begin_drag(scene.x(), scene.y()):
                event.accept()
                return
        if event.button() == Qt.MiddleButton or (event.button() == Qt.LeftButton and self.space):
            self.pan_origin = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            return
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        scene_point = self.mapToScene(event.position().toPoint())
        if self.mode in ENV_MODES:
            self._env_press(event, scene_point)
            return
        if self.mode == "kameras":
            treffer = self.camera_hit(event.position())
            if treffer is not None:
                gruppe, name, template = treffer
                self.camera_clicked.emit(gruppe)
                if not self.document.project.readonly:
                    self.camera_drag = {"name": name, "template": template, "punkt": event.position(),
                                        "start": event.position(), "aktiv": False}
            return
        if self.mode == "nachzeichnen":
            if self.trace is not None and not self.document.project.readonly:
                self.trace.druecken(scene_point.x(), scene_point.y(), event.position(), self._to_view())
            return
        if self.mode == "punkte" and self.document.ml.get("art") == "kontrollpunkte":
            self.document.insert(scene_point.x(), scene_point.y())
            return
        for box, number in self._turn_hits:
            if box is not None and box.adjusted(-3, -3, 3, 3).contains(event.position()):
                self.turn_clicked.emit(number)
                return
        gruppe = self.camera_at(event.position()) if (self.show_cameras or self.camera_group is not None) else None
        if gruppe is not None:
            self.camera_clicked.emit(gruppe)
            return
        for feld, point in self.zone_handles():
            if (point - event.position()).manhattanLength() < 14:
                self.zone_drag = (self.zone_selected[0], self.zone_selected[1], feld)
                self.press_position = event.position()
                self.setCursor(Qt.SizeAllCursor)
                return
        for zone in self.zones:
            anchor = zone.get("_anchor")
            label_y = anchor.y() + (-18 if zone["art"] == "xmode" else 20) if anchor is not None else 0
            if anchor is not None and abs(anchor.x() + 40 - event.position().x()) < 45 and abs(label_y - event.position().y()) < 12:
                self.zone_clicked.emit(zone["art"], zone["index"])
                return
        handle = self.rotation_handle()
        if handle is not None and (handle[0] - event.position()).manhattanLength() < 14:
            frame = self.rotation_frame()
            self.rotate_center = QPointF((frame[0] + frame[2]) / 2, (frame[1] + frame[3]) / 2)
            self.rotate_start = math.atan2(scene_point.y() - self.rotate_center.y(), scene_point.x() - self.rotate_center.x())
            self.rotate_angle = 0.0
            self.press_position = event.position()
            self.drag_point = scene_point
            self.setCursor(Qt.CrossCursor)
            self.viewport().update()
            return
        index, distance = self.nearest_object(event.position())
        self.drag_handle = "end"
        selected = self.document.selected
        if 0 <= selected < len(self.input["segments"]) and self.document.ml["segmente"][selected]["typ"] == "bogen":
            middle = self.input["segments"][selected]["middle"]
            mid = self.mapFromScene(QPointF(middle[0], middle[2]))
            if (mid - event.position().toPoint()).manhattanLength() < 14:
                index, distance, self.drag_handle = selected, 0, "middle"
        if distance < 16:
            self._click_select(index, event.modifiers())
            if index in self.document.selected_indices and self.document.ml.get("art") in ("segmente", "kontrollpunkte") and not self.document.project.readonly:
                self.drag_index = index
                self.press_position = event.position()
                self.drag_geometry = self.input if self.document.ml.get("art") == "segmente" else None
        elif self.input["segments"]:
            click = event.position()
            to_view = self._to_view()
            best, best_distance = -1, 12.
            for i, entry in enumerate(self.input["segments"]):
                pts = [to_view(p[0], p[2]) for p in self.input["pos"][entry["first"]:entry["last"] + 1]]
                for a, b in zip(pts, pts[1:]):
                    dx, dy = b[0] - a[0], b[1] - a[1]
                    t = max(0, min(1, ((click.x() - a[0]) * dx + (click.y() - a[1]) * dy) / max(1, dx * dx + dy * dy)))
                    d = math.hypot(click.x() - a[0] - t * dx, click.y() - a[1] - t * dy)
                    if d < best_distance:
                        best, best_distance = i, d
            if best >= 0:
                self._click_select(best, event.modifiers())
            else:
                self._start_rubber(event)
        else:
            self._start_rubber(event)

    def _click_select(self, index, modifiers):
        self.click_single = None
        selected = self.document.selected_indices
        if modifiers & Qt.ControlModifier:
            self.document.select_many(selected ^ {index}, index)
        elif modifiers & Qt.ShiftModifier:
            self.document.select_many(selected | {index}, index)
        elif index in selected:
            # Erst beim Loslassen auf Einzelauswahl reduzieren, damit ein Zug
            # an einem bereits ausgewählten Punkt die ganze Gruppe erhält.
            self.click_single = index
        else:
            self.document.select(index)

    def _start_rubber(self, event):
        self.rubber_origin = QPointF(event.position())
        self.rubber_end = QPointF(event.position())
        self.rubber_base = set(self.document.selected_indices)
        self.rubber_modifiers = event.modifiers()
        self.rubber_last = None
        if not event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier):
            self.document.select_many(set())

    def _update_rubber(self, position):
        self.rubber_end = QPointF(position)
        indices = set()
        if (self.rubber_end - self.rubber_origin).manhattanLength() > 3:
            # Kasten einmal in Szenenkoordinaten umrechnen, danach reine Zahlenvergleiche.
            a = self.mapToScene(self.rubber_origin.toPoint())
            b = self.mapToScene(self.rubber_end.toPoint())
            x0, x1 = sorted((a.x(), b.x()))
            z0, z1 = sorted((a.y(), b.y()))

            def inside(p):
                return x0 <= p[0] <= x1 and z0 <= p[2] <= z1
            segments, pos = self.input["segments"], self.input["pos"]
            for i, point in enumerate(self.object_positions()):
                if inside(point):
                    indices.add(i)
                elif i < len(segments):
                    entry = segments[i]
                    if inside(entry["middle"]) or any(inside(p) for p in pos[entry["first"]:entry["last"] + 1]):
                        indices.add(i)
        if self.rubber_modifiers & Qt.ControlModifier:
            indices ^= self.rubber_base
        elif self.rubber_modifiers & Qt.ShiftModifier:
            indices |= self.rubber_base
        if indices != self.rubber_last:     # Liste/Eigenschaften nur bei geänderter Auswahl aktualisieren
            self.rubber_last = set(indices)
            self.document.select_many(indices)
        self.viewport().update()

    def _draft(self, ml):
        points = ml.get("punkte", []) if ml.get("art") == "kontrollpunkte" else []
        return input_geometry(SimpleNamespace(ml=ml, points=points, project=self.document.project))

    # ------------------------------------------------------------------ Umgebung (Eingaben)
    def env_nearest(self, position):
        """Kennung des Umgebungsobjekts unter einer Ansichtsposition (am nächsten zur Mitte) oder None."""
        if not self.show_env:
            return None
        to_view = self._to_view()
        scale = abs(self.transform().m11())
        px, py = position.x(), position.y()
        best, best_d = None, float("inf")
        for o in self.document.env_objects:
            x, y = to_view(o["x"], o["z"])
            d = math.hypot(x - px, y - py)
            if d <= max(self.env_radius_m(o) * scale, ENV_HIT_PX) and d < best_d:
                best, best_d = o["id"], d
        return best

    def _env_press(self, event, scene_point):
        document, position = self.document, event.position()
        if self.mode == "streuen":
            if document.env_editable:
                self.env_area = {"form": self.env_form, "a": scene_point, "b": scene_point,
                                 "punkte": [(scene_point.x(), scene_point.y())]}
                self.viewport().update()
            return
        if self.terrain_armed and document.env_editable:
            self.terrain_place_requested.emit(self.terrain_armed, scene_point.x(), scene_point.y())
            return
        hit = self.env_nearest(position)
        selected, modifiers = document.env_selected, event.modifiers()
        self.env_click_single = None
        if hit is not None:
            if modifiers & Qt.ControlModifier:
                document.env_select(selected ^ {hit}, hit)
            elif modifiers & Qt.ShiftModifier:
                document.env_select(selected | {hit}, hit)
            elif hit in selected:
                self.env_click_single = hit        # erst beim Loslassen auf Einzelauswahl: ein Zug erhält die Gruppe
            else:
                document.env_select({hit}, hit)
            if hit in document.env_selected and document.env_editable:
                self.env_drag = {"press": QPointF(position), "origin": scene_point, "active": False}
                self.env_delta = QPointF()
            return
        if self.env_model and document.env_editable:
            self.env_place_requested.emit(scene_point.x(), scene_point.y())
            return
        terrain = self.terrain_nearest(position)
        if terrain is not None:
            document.terrain_select(terrain)
            if document.env_editable:
                self.terrain_drag = {"id": terrain, "press": QPointF(position), "origin": scene_point, "active": False}
                self.env_delta = QPointF()
            return
        if not modifiers & (Qt.ShiftModifier | Qt.ControlModifier):
            document.terrain_select(None)
        self.env_rubber = {"a": QPointF(position), "b": QPointF(position), "basis": set(selected), "mods": modifiers}
        if not modifiers & (Qt.ShiftModifier | Qt.ControlModifier):
            document.env_select(set())

    def _env_move(self, event):
        position = event.position()
        self.env_hover = QPointF(position)
        if self.env_area is not None:
            point = self.mapToScene(position.toPoint())
            self.env_area["b"] = point
            if self.env_area["form"] == "pinsel":
                last = self.env_area["punkte"][-1]
                if math.dist(last, (point.x(), point.y())) >= max(self.env_radius / 4, 0.5):
                    self.env_area["punkte"].append((point.x(), point.y()))
            self.viewport().update()
        elif self.terrain_drag is not None:
            if not self.terrain_drag["active"] and (position - self.terrain_drag["press"]).manhattanLength() > 3:
                self.terrain_drag["active"] = True
            if self.terrain_drag["active"]:
                self.env_delta = self.mapToScene(position.toPoint()) - self.terrain_drag["origin"]
            self.viewport().update()
        elif self.env_drag is not None:
            if not self.env_drag["active"] and (position - self.env_drag["press"]).manhattanLength() > 3:
                self.env_drag["active"] = True
                self.env_click_single = None
            if self.env_drag["active"]:
                self.env_delta = self.mapToScene(position.toPoint()) - self.env_drag["origin"]
            self.viewport().update()
        elif self.env_rubber is not None:
            self.env_rubber["b"] = QPointF(position)
            a, b = self.mapToScene(self.env_rubber["a"].toPoint()), self.mapToScene(position.toPoint())
            x0, x1 = sorted((a.x(), b.x()))
            z0, z1 = sorted((a.y(), b.y()))
            inside = ({o["id"] for o in self.document.env_objects if x0 <= o["x"] <= x1 and z0 <= o["z"] <= z1}
                      if (self.env_rubber["b"] - self.env_rubber["a"]).manhattanLength() > 3 else set())
            if self.env_rubber["mods"] & Qt.ControlModifier:
                inside ^= self.env_rubber["basis"]
            elif self.env_rubber["mods"] & Qt.ShiftModifier:
                inside |= self.env_rubber["basis"]
            self.document.env_select(inside)
            self.viewport().update()
        else:
            self.viewport().update()

    def _env_release(self, event):
        document = self.document
        if self.env_area is not None:
            area, self.env_area = self.env_area, None
            a, b = area["a"], area["b"]
            if area["form"] == "rechteck":
                if abs(a.x() - b.x()) >= 1.0 and abs(a.y() - b.y()) >= 1.0:
                    self.env_scatter_requested.emit({"form": "rechteck", "rechteck": [a.x(), a.y(), b.x(), b.y()]})
            else:
                self.env_scatter_requested.emit({"form": "pinsel", "punkte": [list(p) for p in area["punkte"]],
                                                 "radius": self.env_radius})
            self.viewport().update()
        elif self.terrain_drag is not None:
            drag, self.terrain_drag = self.terrain_drag, None
            delta, self.env_delta = self.env_delta, QPointF()
            if drag["active"]:
                document.terrain_move(drag["id"], delta.x(), delta.y())
            self.viewport().update()
        elif self.env_drag is not None:
            drag, self.env_drag = self.env_drag, None
            delta, self.env_delta = self.env_delta, QPointF()
            if drag["active"]:
                document.env_move(document.env_selected, delta.x(), delta.y())
            elif self.env_click_single is not None:
                document.env_select({self.env_click_single}, self.env_click_single)
            self.viewport().update()
        elif self.env_rubber is not None:
            self.env_rubber = None
            self.viewport().update()
        self.env_click_single = None

    def _env_cancel(self):
        """Laufende Umgebungsgeste verwerfen; True, wenn eine lief."""
        running = (self.env_area is not None or self.env_drag is not None or self.env_rubber is not None
                   or self.terrain_drag is not None)
        self.terrain_drag = None
        if self.env_rubber is not None:
            document_selection = self.env_rubber["basis"]
            self.document.env_select(document_selection)
        self.env_area = self.env_drag = self.env_rubber = self.env_click_single = None
        self.env_delta = QPointF()
        self.viewport().update()
        return running

    def _env_key(self, event):
        """Tasten der Umgebungswerkzeuge; True, wenn die Taste verbraucht wurde."""
        key, document = event.key(), self.document
        if key == Qt.Key_Escape:
            if self._env_cancel():
                return True
            if self.env_model or self.terrain_armed:
                self.env_model = self.terrain_armed = None
                self.env_disarm_requested.emit()
                self.viewport().update()
                return True
            return False
        if key == Qt.Key_Delete:
            if document.env_selected:
                document.env_delete()
            elif document.terrain_selected:
                document.terrain_delete()
            return True
        if key == Qt.Key_A and event.modifiers() & Qt.ControlModifier:
            document.env_select({o["id"] for o in document.env_objects})
            return True
        if key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down) and document.env_selected:
            step = 1 if event.modifiers() & Qt.ShiftModifier else 0.1
            document.env_move(document.env_selected, step * ((key == Qt.Key_Right) - (key == Qt.Key_Left)),
                              step * ((key == Qt.Key_Down) - (key == Qt.Key_Up)))
            return True
        return False

    def mouseMoveEvent(self, event):
        if self.livemap is not None and self.livemap.drag is not None:
            scene = self.mapToScene(event.position().toPoint())
            self.livemap.update_drag(scene.x(), scene.y())
            self.viewport().update()
            return
        if self.mode == "kameras" and self.camera_drag is not None and self.pan_origin is None:
            d = self.camera_drag
            if not d["aktiv"] and (event.position() - d["start"]).manhattanLength() > 4:
                d["aktiv"] = True
                self.setCursor(Qt.SizeAllCursor)
            if d["aktiv"]:
                d["punkt"] = event.position()
                if d["template"].startswith("TriggerZone"):
                    d["ansicht_zone"] = self._zone_auf_strecke(event.position())
                self.viewport().update()
            return
        if self.mode in ENV_MODES and self.pan_origin is None:
            self._env_move(event)
            return
        if self.mode == "nachzeichnen" and self.pan_origin is None:
            if self.trace is not None:
                point = self.mapToScene(event.position().toPoint())
                self.trace.bewegen(point.x(), point.y(), event.position())
            return
        if self.pan_origin is not None:
            delta = event.position() - self.pan_origin
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(delta.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(delta.y()))
            self.pan_origin = event.position()
        elif self.zone_drag is not None:
            art, index, feld = self.zone_drag
            s = self.station_from_view(event.position())
            key = dict(self.ZONE_FELDER[art])[feld]
            for zone in self.zones:
                if (zone["art"], zone["index"]) == (art, index):
                    zone[key] = s
            self.station = s
            self.viewport().update()
        elif self.rotate_center is not None:
            self.drag_point = self.mapToScene(event.position().toPoint())
            angle = math.atan2(self.drag_point.y() - self.rotate_center.y(), self.drag_point.x() - self.rotate_center.x()) - self.rotate_start
            angle = (angle + math.pi) % (2 * math.pi) - math.pi
            if event.modifiers() & Qt.ShiftModifier:
                angle = math.radians(round(math.degrees(angle) / SNAP_DEG) * SNAP_DEG)
            self.rotate_angle = angle
            self.rebuild(self.preview, self._draft(self.document.rotated_ml(angle, self.rotate_center.x(), self.rotate_center.y())))
            self.draft_changed.emit()
        elif self.rubber_origin is not None:
            self._update_rubber(event.position())
        elif self.drag_index >= 0 and (self.drag_point is not None or (event.position() - self.press_position).manhattanLength() > 3):
            self.click_single = None
            whole_track = self.document.rotatable() == "strecke"
            if self.document.ml.get("art") == "segmente" and len(self.document.selected_indices) > 1 and not whole_track:
                self.group_move_blocked.emit()
                return
            self.drag_point = self.mapToScene(event.position().toPoint())
            self.drag_delta = self.drag_point - self.mapToScene(self.press_position.toPoint())
            ml = dict(self.document.ml)          # flache Kopie: nur geänderte Einträge werden ersetzt
            if whole_track and len(self.document.selected_indices) > 1:
                ml = self.document.moved_track_ml(self.drag_delta.x(), self.drag_delta.y())
            elif ml.get("art") == "segmente":
                segments = list(ml["segmente"])
                segment = dict(segments[self.drag_index])
                segment.update(self.document.segment_drag_changes(self.drag_index, self.drag_point, self.drag_handle, self.drag_geometry))
                segments[self.drag_index] = segment
                ml["segmente"] = segments
            else:
                points = list(ml["punkte"])
                dx, dz = self.drag_delta.x(), self.drag_delta.y()
                for i in self.document.selected_indices:
                    x, _, z = point_xyz(points[i])
                    points[i] = shifted_point(points[i], x + dx, z + dz)
                ml["punkte"] = points
            self.rebuild(self.preview, self._draft(ml))
            self.draft_changed.emit()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.livemap is not None and self.livemap.finish_drag():
            self.viewport().update()
            return
        if self.pan_origin is not None:
            self.pan_origin = None
            self.unsetCursor()
        elif self.mode == "kameras" and self.camera_drag is not None:
            d, self.camera_drag = self.camera_drag, None
            self.unsetCursor()
            if d["aktiv"]:
                punkt = self.mapToScene(event.position().toPoint())
                if d["template"].startswith("TriggerZone") and d.get("ansicht_zone"):
                    punkt = self.mapToScene(QPointF(*d["ansicht_zone"]).toPoint())
                self.camera_moved.emit(d["name"], d["template"], punkt.x(), punkt.y())
            self.viewport().update()
        elif self.mode in ENV_MODES:
            self._env_release(event)
        elif self.mode == "nachzeichnen":
            if self.trace is not None:
                point = self.mapToScene(event.position().toPoint())
                self.trace.loslassen(point.x(), point.y(), event.position())
        elif self.zone_drag is not None:
            art, index, feld = self.zone_drag
            self.zone_drag = None
            self.unsetCursor()
            if (event.position() - self.press_position).manhattanLength() > 2:
                self.zone_moved.emit(art, index, feld, round(self.station_from_view(event.position()), 1))
            self.viewport().update()
        elif self.rotate_center is not None:
            if (event.position() - self.press_position).manhattanLength() > 3:
                self.mouseMoveEvent(event)
            angle, center = self.rotate_angle, self.rotate_center
            self.rotate_center, self.drag_point, self.rotate_angle = None, None, 0.0
            self.unsetCursor()
            if abs(angle) > 1e-9:
                self.document.rotate_selection(angle, center.x(), center.y())
            else:
                self.rebuild(self.preview)
            self.viewport().update()
        elif self.rubber_origin is not None:
            self._update_rubber(event.position())
            self.rubber_origin = self.rubber_end = self.rubber_last = None
            self.viewport().update()
        elif self.drag_index >= 0:
            if self.drag_point is not None or (event.position() - self.press_position).manhattanLength() > 3:
                self.mouseMoveEvent(event)
            index, point = self.drag_index, self.drag_point
            self.drag_index, self.drag_point = -1, None
            geometry, self.drag_geometry = self.drag_geometry, None
            if point is not None:
                if self.document.rotatable() == "strecke" and len(self.document.selected_indices) > 1:
                    self.document.move_track(self.drag_delta.x(), self.drag_delta.y())
                elif self.document.ml.get("art") == "segmente":
                    self.document.drag_segment(index, point, self.drag_handle, geometry)
                else:
                    self.document.move_selection(self.drag_delta.x(), self.drag_delta.y())
            elif self.click_single is not None:
                self.document.select(self.click_single)
            self.click_single = None
            self.viewport().update()
        else:
            if self.click_single is not None:
                self.document.select(self.click_single)
                self.click_single = None
            super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        if self.trace is not None and self.mode == "nachzeichnen":
            self.trace.verlassen()
        super().leaveEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.mode == "nachzeichnen":
            return
        if self.mode in ENV_MODES:
            hit = self.env_nearest(event.position()) if self.mode == "umgebung" else None
            gruppe = (self.document.env_object(hit) or {}).get("gruppe") if hit else None
            if gruppe:
                self.document.env_select(self.document.env_group(gruppe), hit)     # ganze Gruppe wählen
            return
        if event.button() == Qt.LeftButton and self.document.ml.get("art") == "kontrollpunkte" and not self.document.project.readonly:
            point = self.mapToScene(event.position().toPoint())
            # Einfügen nach der nächstgelegenen Eingabekante, auch am Rundenschluss.
            pts = [point_xyz(p) for p in self.document.points]
            best, distance = -1, float("inf")
            count = len(pts) if self.document.project.closed else max(0, len(pts) - 1)
            for i in range(count):
                a, b = pts[i], pts[(i + 1) % len(pts)]
                dx, dz = b[0] - a[0], b[2] - a[2]
                t = max(0, min(1, ((point.x() - a[0]) * dx + (point.y() - a[2]) * dz) / max(dx * dx + dz * dz, 1e-12)))
                d = (point.x() - a[0] - t * dx) ** 2 + (point.y() - a[2] - t * dz) ** 2
                if d < distance:
                    best, distance = i, d
            self.document.select(best)
            self.document.insert(point.x(), point.y())

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Escape and self.livemap is not None and self.livemap.cancel_drag():
            return
        if self.mode in ENV_MODES and key != Qt.Key_Space and self._env_key(event):
            return
        if self.mode == "nachzeichnen" and key != Qt.Key_Space and self.trace is not None and self.trace.taste(key):
            return
        if key == Qt.Key_Space:
            self.space = True
        elif key == Qt.Key_Escape:
            if self.zone_drag is not None:
                self.zone_drag = None
                self.zone_restore.emit()
            self.drag_index, self.drag_point, self.pan_origin = -1, None, None
            self.drag_geometry = None
            self.rotate_center, self.rotate_angle = None, 0.0
            self.click_single = None
            if self.rubber_origin is not None:
                self.document.select_many(self.rubber_base)
            self.rubber_origin = self.rubber_end = self.rubber_last = None
            self.unsetCursor()
            self.rebuild(self.preview)
            self.draft_changed.emit()
            self.mode_changed.emit("auswahl")
            self.viewport().update()
        elif key == Qt.Key_Delete:
            self.document.delete()
        elif key == Qt.Key_Insert:
            self.document.insert()
        elif key == Qt.Key_Home:
            self.fit_track()
        elif key == Qt.Key_F:
            self.focus_selection()
        elif key == Qt.Key_R and self.document.rotatable() and not event.modifiers() & Qt.ControlModifier:
            frame = self.rotation_frame()
            if frame is not None:
                step = -1.0 if event.modifiers() & Qt.ShiftModifier else 1.0
                self.document.rotate_selection(math.radians(step), (frame[0] + frame[2]) / 2, (frame[1] + frame[3]) / 2)
        elif key in (Qt.Key_V, Qt.Key_P, Qt.Key_U, Qt.Key_B, Qt.Key_N, Qt.Key_K):
            self.mode_changed.emit({Qt.Key_P: "punkte", Qt.Key_U: "umgebung", Qt.Key_B: "streuen",
                                    Qt.Key_N: "nachzeichnen", Qt.Key_K: "kameras"}.get(key, "auswahl"))
        elif key == Qt.Key_A and event.modifiers() & Qt.ControlModifier:
            self.document.select_many(range(self.document.object_count))
        elif key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down) and self.document.selected_indices:
            step = 1 if event.modifiers() & Qt.ShiftModifier else 0.1
            if self.document.ml.get("art") == "segmente":
                if self.document.rotatable() == "strecke" and len(self.document.selected_indices) > 1:
                    self.document.move_track(step * ((key == Qt.Key_Right) - (key == Qt.Key_Left)), step * ((key == Qt.Key_Down) - (key == Qt.Key_Up)))
                else:
                    self.group_move_blocked.emit()
                return
            self.document.move_selection(dx=step * ((key == Qt.Key_Right) - (key == Qt.Key_Left)), dz=step * ((key == Qt.Key_Down) - (key == Qt.Key_Up)))
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key_Space:
            self.space = False
        else:
            super().keyReleaseEvent(event)
