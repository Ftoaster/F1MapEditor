"""Synchrones Höhenprofil (mit bearbeitbaren Höhengriffen) und Querschnitt (Karte: ``mapview.py``)."""

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from .i18n import dezimal, tr, trn
from .mapview import MATERIALS, MATERIAL_NAMES, MapView, cosmetic, path_for  # noqa: F401  (MapView: Rückwärtskompatibilität)
from .ux import zahl


class ProfileView(QWidget):
    """Höhenprofil bzw. Querneigung über der Station. In beiden sind die Eingabewerte (Höhe bzw. Querneigung der
    Kontrollpunkte/Segmente) als Griffe bearbeitbar: ziehen, Bereich A→B, Pfeiltasten, Rechtsklick-Werkzeuge."""

    station_changed = Signal(float)
    object_selected = Signal(int)
    nodes_selected = Signal(object, int)        # Objektindizes, Hauptindex
    heights_changed = Signal(object)            # {Objektindex: neuer Wert} (Höhe in m bzw. Querneigung in °)
    height_tool = Signal(str)                   # uebergang / rampe / eben / glaetten / eingeben

    GRIFF_PX = 8

    def __init__(self, kind="height"):
        super().__init__()
        self.kind = kind
        self.preview = None
        self.station = 0
        self.node_source = None                 # Funktion(data) -> [{"index", "s", "y"}] oder None
        self.selected = set()
        self.editable = True
        self._frame = None                      # (plot, lo, hi, length, s0, s1) der letzten Zeichnung
        self.view = None                        # sichtbarer Stationsbereich (s0, s1); None = ganze Runde
        self._pan = None
        self._drag = None                       # {"start": QPointF, "frame": …, "dy": float, "nodes": […]}
        self._band = None                       # Bereichsauswahl (QPointF, QPointF)
        self._pending = None                    # Maus auf dem Profil gedrückt, Richtung noch offen
        self.setMinimumHeight(170)
        self.setAccessibleName(tr("Height profile in metres from the finish") if kind == "height" else tr(("Banking in degrees from the "
                                                                                                "finish")))
        self.unit = "m" if kind == "height" else "°"
        self.setFocusPolicy(Qt.ClickFocus)
        self.setMouseTracking(True)
        self.setToolTip(tr("Mouse wheel: zoom horizontally · middle mouse button: pan · double-click: whole lap"))

    # ------------------------------------------------------------------ Griffe
    def nodes(self):
        if not self.node_source or not self.preview or not self.preview.get("pos"):
            return []
        try:
            return self.node_source(self.preview) or []
        except (ValueError, TypeError, KeyError, IndexError, ZeroDivisionError):
            return []

    def _frame_for(self, data, values, raw_values, nodes):
        if self._drag:                           # Achsen während des Ziehens einfrieren
            return self._drag["frame"]
        length = data["length"]
        s0, s1 = self.view if self.view else (0.0, length)
        # Höhenbereich nur aus dem sichtbaren Ausschnitt (bessere Auflösung beim Zoomen)
        inside = [v for s, v in zip(data["s"], values) if s0 <= s <= s1] or list(values)
        inside_raw = [v for s, v in zip(data["s"], raw_values) if s0 <= s <= s1] or list(raw_values)
        ys = inside + inside_raw + [n["y"] for n in nodes if s0 <= n["s"] <= s1]
        lo, hi = min(ys), max(ys)
        if hi - lo < 1:
            hi, lo = hi + 0.5, lo - 0.5
        pad = (hi - lo) * (0.08 if nodes else 0)
        plot = QRectF(65, 30, max(1, self.width() - 90), max(1, self.height() - 60))   # eine Kopfzeile (E12)
        return plot, lo - pad, hi + pad, length, s0, s1

    def _xy(self, s, value, frame=None):
        plot, lo, hi, length, s0, s1 = frame or self._frame
        s = s if 0 <= s <= length else s % length
        return QPointF(plot.left() + (s - s0) / (s1 - s0) * plot.width(),
                       plot.bottom() - (value - lo) / (hi - lo) * plot.height())

    def _station_at(self, x):
        plot, _, _, length, s0, s1 = self._frame
        return max(0.0, min(length, s0 + (x - plot.left()) / max(1.0, plot.width()) * (s1 - s0)))

    def _meters_per_px(self):
        plot, lo, hi = self._frame[:3]
        return (hi - lo) / max(1.0, plot.height())

    # ------------------------------------------------------------------ Zoom
    def wheelEvent(self, event):
        if not self.preview or not self._frame:
            return
        length = self.preview["length"]
        s0, s1 = self.view or (0.0, length)
        anchor = self._station_at(event.position().x())
        factor = 0.8 if event.angleDelta().y() > 0 else 1.25
        width = max(50.0, min(length, (s1 - s0) * factor))
        share = (anchor - s0) / max(s1 - s0, 1e-9)
        s0 = max(0.0, min(length - width, anchor - share * width))
        self.view = None if width >= length - 1e-6 else (s0, s0 + width)
        self.update()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and self._hit(event.position()) is None:
            self.view = None
            self.update()

    def _hit(self, pos):
        if not self._frame:
            return None
        best, dist = None, self.GRIFF_PX + 1
        for node in self.nodes():
            p = self._xy(node["s"], node["y"])
            d = math.hypot(p.x() - pos.x(), p.y() - pos.y())
            if d < dist:
                best, dist = node, d
        return best

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        text = self.palette().text().color()
        painter.setPen(text)
        if not self.preview or not self.preview["pos"]:
            painter.drawText(24, 40, tr("Profile: a closed lap with at least four points is required."))
            return
        data = self.preview
        bank = self.kind == "bank"
        values = data["bank"] if bank else data["pos"] and [p[1] for p in data["pos"]]
        raw_values = values if bank else data["raw_y"]
        nodes = self.nodes()
        self._frame = self._frame_for(data, values, raw_values, nodes)
        plot, lo, hi, _, s0, s1 = self._frame
        def xy(s, value):
            p = self._xy(s, value) if s < data["length"] else self._xy(data["length"], value)
            return [p.x(), p.y()]
        painter.setPen(QPen(QColor("#52677A"), 1))
        painter.drawRect(plot)
        painter.setPen(text)
        if bank and nodes and self.editable:
            title = tr("Banking in ° (+ = left higher) · ↑↓ 0.1° · mouse wheel: zoom · right-click: tools")
        elif bank:
            title = tr("Banking in ° (+ = left higher)")
        elif nodes and self.editable:
            title = tr("Height profile in m · ↑↓ 0.1 m · mouse wheel: zoom · right-click: tools")
        else:
            title = tr("Height profile in m")
        title += (tr(" · your input (preview follows)") if data.get("input") else
                  "" if bank or nodes else tr(" · solid: computed, dashed: your input"))
        # Titel links, Cursorwert rechts in derselben Zeile; der Titel wird bei Platzmangel gekürzt
        i = min(range(len(data["s"])), key=lambda i: abs(data["s"][i] - self.station))
        if bank:
            cursor = dezimal(tr("Cursor {station:.1f} m · {wert:.2f}°", station=self.station, wert=values[i]))
        else:
            cursor = dezimal(tr("Cursor {station:.1f} m · {wert:.2f} {unit} · gradient {gradient:+.1f} %",
                                station=self.station, wert=values[i], unit="m", gradient=data['gradient'][i]))
        metrics = painter.fontMetrics()
        room = plot.right() - 16 - metrics.horizontalAdvance(cursor) - 16
        painter.drawText(16, 19, metrics.elidedText(title, Qt.ElideRight, max(0, int(room))))
        painter.drawText(QRectF(16, 5, plot.right() - 16, 18), Qt.AlignRight | Qt.AlignVCenter, cursor)
        painter.drawText(8, int(plot.top() + 5), dezimal(f"{hi:.1f}"))
        painter.drawText(8, int(plot.bottom()), dezimal(f"{lo:.1f}"))
        is_input = data.get("input", False)
        origin = tr("input origin") if is_input else tr("finish")
        if self.view:
            painter.drawText(int(plot.left()), self.height() - 10, f"{s0:.0f} m")
            painter.drawText(int(plot.right() - 330), self.height() - 10,
                             tr("{s1:.0f} m · station from {origin} · zoom (double-click: whole lap)", s1=s1, origin=origin))
        else:
            painter.drawText(int(plot.left()), self.height() - 10, "0 · " + origin)
            painter.drawText(int(plot.right() - (255 if is_input else 190)), self.height() - 10,
                             tr("{length:.0f} m · station from ", length=data['length']) + origin)
        painter.save()
        painter.setClipRect(QRectF(plot.left() - 8, 0, plot.width() + 16, self.height()))
        closed = data.get("closed", True)
        ss = data["s"] + ([data["length"]] if closed else [])
        def complete(values):
            return values + values[:1] if closed else values
        if not bank:
            painter.setPen(QPen(QColor("#AABBCD"), 1.5, Qt.DashLine))
            painter.drawPath(path_for([xy(s, v) for s, v in zip(ss, complete(raw_values))]))
        painter.setPen(QPen(QColor("#67D9F5" if self.palette().window().color().lightness() < 128 else "#005C73"), 2))
        painter.drawPath(path_for([xy(s, v) for s, v in zip(ss, complete(values))]))
        x = xy(self.station, lo)[0]
        painter.setPen(QPen(QColor("#D4AA61"), 1.5, Qt.DashLine))
        painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
        painter.setPen(text)
        if nodes:
            self._paint_nodes(painter, nodes)
        painter.restore()

    def _paint_nodes(self, painter, nodes):
        dark = self.palette().window().color().lightness() < 128
        line = QColor("#F1F5F9" if dark else "#1F2A36")
        accent = QColor("#FFB13B")
        dy = self._drag["dy"] if self._drag and self._drag.get("moved") else 0.0
        moving = set(self._drag["nodes"]) if self._drag else set()
        ordered = sorted(nodes, key=lambda n: n["s"])
        pts = [(n, self._xy(n["s"], n["y"] + (dy if n["index"] in moving else 0.0))) for n in ordered]
        plot = self._frame[0]
        visible = [p for _, p in pts if plot.left() - 1 <= p.x() <= plot.right() + 1]
        gaps = sorted(b.x() - a.x() for a, b in zip(visible, visible[1:]))
        dense = bool(gaps) and gaps[len(gaps) // 2] < 7          # Griffe enger als ~7 px: keine Einzelkreise
        if dense:
            # gewählte Abschnitte als kräftige Linie, einzelne Punkte nur als kleine Marken
            painter.setPen(QPen(accent, 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            for (a, pa), (b, pb) in zip(pts, pts[1:]):
                if a["index"] in self.selected and b["index"] in self.selected:
                    painter.drawLine(pa, pb)
            painter.setPen(Qt.NoPen)
            painter.setBrush(accent)
            for node, p in pts:
                if node["index"] in self.selected:
                    painter.drawEllipse(p, 2.5, 2.5)
            painter.setBrush(Qt.NoBrush)
            self._paint_drag_label(painter, pts, moving, dy, accent)
            self._paint_band(painter, accent)
            return
        # Eingabelinie der Griffe mit Steigung je Abschnitt
        painter.setPen(QPen(line, 1.2, Qt.DotLine))
        for (a, pa), (b, pb) in zip(pts, pts[1:]):
            painter.drawLine(pa, pb)
            run = b["s"] - a["s"]
            if self.kind == "height" and run > 1 and pb.x() - pa.x() > 70:
                grade = ((b["y"] + (dy if b["index"] in moving else 0)) - (a["y"] + (dy if a["index"] in moving else 0))) / run * 100
                painter.setPen(QColor("#E46A5A") if abs(grade) > 8 else line)
                painter.drawText(QPointF((pa.x() + pb.x()) / 2 - 18, min(pa.y(), pb.y()) - 6),
                                 dezimal(f"{grade:+.1f} %"))
                painter.setPen(QPen(line, 1.2, Qt.DotLine))
        for node, p in pts:
            chosen = node["index"] in self.selected
            painter.setPen(QPen(accent if chosen else line, 2))
            painter.setBrush(accent if chosen else QColor("#1B2733" if dark else "#FFFFFF"))
            r = 6 if chosen else 4.5
            painter.drawEllipse(p, r, r)
        painter.setBrush(Qt.NoBrush)
        self._paint_drag_label(painter, pts, moving, dy, accent)
        self._paint_band(painter, accent)

    def _paint_drag_label(self, painter, pts, moving, dy, accent):
        if self._drag and self._drag.get("moved"):
            p = next((q for node, q in pts if node["index"] in moving), pts[0][1])
            painter.setPen(accent)
            if self.kind == "height":
                label = trn(len(moving), "{dy:+.1f} m · {n} point", "{dy:+.1f} m · {n} points", dy=dy)
            else:
                label = trn(len(moving), "{dy:+.1f}° · {n} point", "{dy:+.1f}° · {n} points", dy=dy)
            painter.drawText(QPointF(p.x() + 10, p.y() - 10), dezimal(label))

    def _paint_band(self, painter, accent):
        if self._band:
            a, b = self._band
            plot = self._frame[0]
            fill = QColor(accent)
            fill.setAlpha(45)
            band = QRectF(QPointF(min(a.x(), b.x()), plot.top()), QPointF(max(a.x(), b.x()), plot.bottom()))
            painter.fillRect(band, fill)
            painter.setPen(QPen(accent, 1, Qt.DashLine))
            painter.drawRect(band)
            s0, s1 = sorted((self._station_at(a.x()), self._station_at(b.x())))
            painter.drawText(QPointF(band.left() + 4, plot.top() + 14), tr("A {s0:.0f} m → B {s1:.0f} m", s0=s0, s1=s1))

    # ------------------------------------------------------------------ Maus und Tastatur
    def _pick_station(self, x):
        self.station = self._station_at(x) if self._frame else 0.0
        clicked_station = self.station
        stations = self.preview["object_s"]
        if stations:
            i = min(range(len(stations)), key=lambda i: min(abs(stations[i] - self.station), self.preview["length"] - abs(stations[i] - self.station)))
            self.object_selected.emit(i)
        self.station = clicked_station
        self.station_changed.emit(self.station)
        self.update()

    def mousePressEvent(self, event):
        if not self.preview or not self._frame:
            return
        pos = event.position()
        if event.button() == Qt.MiddleButton:
            self._pan = (pos.x(), self.view or (0.0, self.preview["length"]))
            return
        node = self._hit(pos) if self.editable else None
        if event.button() == Qt.RightButton:
            if node is not None and node["index"] not in self.selected:
                self._select({node["index"]}, node["index"])
            self._context_menu(event.globalPosition().toPoint())
            return
        if event.button() != Qt.LeftButton:
            return
        mods = event.modifiers()
        if node is not None and mods & Qt.ControlModifier:
            index = node["index"]
            chosen = self.selected ^ {index}
            self._select(chosen, index if index in chosen else min(chosen, default=-1))
            return
        if self.nodes() and self.editable:
            # Erst die Bewegungsrichtung entscheidet: waagerecht = Bereich A→B wählen, senkrecht = Höhe ziehen
            self._pending = {"start": pos, "node": node, "mods": mods}
            return
        self._pick_station(pos.x())

    def _start_drag(self, node, mods, start):
        index = node["index"]
        if mods & Qt.ShiftModifier:
            self._select(self.selected | {index}, index)
        elif index not in self.selected:
            self._select({index}, index)
        self._drag = {"start": start, "frame": self._frame, "dy": 0.0, "moved": False,
                      "nodes": sorted(self.selected | {index})}

    def mouseMoveEvent(self, event):
        pos = event.position()
        if self._pan and self.view:
            x0, (s0, s1) = self._pan
            plot, length = self._frame[0], self.preview["length"]
            shift = -(pos.x() - x0) / max(1.0, plot.width()) * (s1 - s0)
            shift = max(-s0, min(length - s1, shift))
            self.view = (s0 + shift, s1 + shift)
            self.update()
            return
        pending = getattr(self, "_pending", None)
        if pending:
            dx, dy = pos.x() - pending["start"].x(), pos.y() - pending["start"].y()
            if max(abs(dx), abs(dy)) >= 4:
                self._pending = None
                if pending["node"] is not None and abs(dy) > abs(dx):
                    self._start_drag(pending["node"], pending["mods"], pending["start"])
                else:
                    self._band = (pending["start"], pos)
                    self._band_mods = pending["mods"]
        if self._drag:
            dy_px = pos.y() - self._drag["start"].y()
            if abs(dy_px) >= 3:
                self._drag["moved"] = True
            if self._drag["moved"]:
                plot, lo, hi = self._drag["frame"][:3]
                self._drag["dy"] = round(-dy_px * (hi - lo) / max(1.0, plot.height()), 1)
                self.update()
            return
        if self._band:
            self._band = (self._band[0], pos)
            self.update()
            return
        self.setCursor(Qt.SizeVerCursor if self.editable and self._hit(pos) is not None else Qt.ArrowCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self._pan = None
            return
        pending = getattr(self, "_pending", None)
        if pending:
            self._pending = None
            node = pending["node"]
            if node is None:
                self._pick_station(pending["start"].x())
                return
            index = node["index"]                     # Klick auf einen Punkt: wählen (Umschalt ergänzt)
            self._select((self.selected | {index}) if pending["mods"] & Qt.ShiftModifier else {index}, index)
            self.station = node["s"]
            self.station_changed.emit(self.station)
            return
        if self._drag:
            drag, self._drag = self._drag, None
            if drag["moved"] and abs(drag["dy"]) >= 0.05:
                heights = {n["index"]: n["y"] for n in self.nodes()}
                self.heights_changed.emit({i: round(heights[i] + drag["dy"], 2) for i in drag["nodes"] if i in heights})
            self.update()
            return
        if self._band:
            a, b = self._band
            self._band = None
            if abs(a.x() - b.x()) < 4 and abs(a.y() - b.y()) < 4:
                self._pick_station(a.x())
                return
            # Bereich A→B: alle Punkte zwischen den beiden Stationen, unabhängig von der Höhe
            x0, x1 = sorted((a.x(), b.x()))
            inside = {n["index"] for n in self.nodes() if x0 <= self._xy(n["s"], n["y"]).x() <= x1}
            if self._band_mods & (Qt.ControlModifier | Qt.ShiftModifier):
                inside |= self.selected
            self._select(inside, min(inside, default=-1))
            self.update()

    def keyPressEvent(self, event):
        if self.selected and self.editable and event.key() in (Qt.Key_Up, Qt.Key_Down):
            step = (1.0 if event.modifiers() & Qt.ShiftModifier else 0.1) * (1 if event.key() == Qt.Key_Up else -1)
            heights = {n["index"]: n["y"] for n in self.nodes()}
            self.heights_changed.emit({i: round(heights[i] + step, 2) for i in self.selected if i in heights})
            return
        super().keyPressEvent(event)

    def _select(self, indices, primary):
        self.selected = set(indices)
        self.nodes_selected.emit(set(indices), primary)
        self.update()

    def _context_menu(self, global_pos):
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        count = len(self.selected)
        if self.kind == "height":
            entries = (("uebergang", tr("Smooth transition A→B (S-curve)"), 3),
                       ("rampe", tr("Even gradient (ramp between first and last)"), 3),
                       ("eben", tr("Same height (mean value)"), 2),
                       ("glaetten", tr("Smooth (even out bumps)"), 1),
                       ("eingeben", tr("Enter height …"), 1))
        else:
            entries = (("uebergang", tr("Smooth transition A→B (S-curve)"), 3),
                       ("rampe", tr("Even progression (ramp between first and last)"), 3),
                       ("eben", tr("Same banking (mean)"), 2),
                       ("glaetten", tr("Smooth (even out jumps)"), 1),
                       ("eingeben", tr("Enter banking …"), 1))
        for key, text, need in entries:
            action = menu.addAction(text)
            action.setEnabled(self.editable and count >= need)
            action.triggered.connect(lambda checked=False, key=key: self.height_tool.emit(key))
        if not count:
            menu.addSeparator()
            menu.addAction(tr("Select points first (click or box)")).setEnabled(False)
        menu.exec(global_pos)


#: Überhöhung der Querschnittsansicht; wird kleiner, wenn der Rand sonst aus dem Bild liefe
CROSS_EXAGGERATION = 5.0


class CrossSection(QWidget):
    def __init__(self):
        super().__init__()
        self.preview = None
        self.stale = False
        self.station = 0
        self.edge_input = None      # Eingabe vor „Übernehmen“: {"wert": None|"strecke"|Grad, "seite": beide|links|rechts}
        self.setMinimumHeight(190)
        self.setAccessibleName(tr("Cross-section: looking in driving direction, left higher with positive banking"))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(self.palette().text().color())
        exaggeration = self._exaggeration()
        painter.drawText(18, 24, tr("Looking in driving direction · height ×{faktor}",
                                    faktor=zahl(exaggeration, 1 if exaggeration < 2 else 0))
                         + (tr(" · your input") if self.preview and self.preview.get("input") else tr(" · outdated") if self.stale else ""))
        if not self.preview or not self.preview["pos"]:
            return
        v = self.preview
        i = min(range(len(v["s"])), key=lambda j: abs(v["s"][j] - self.station))
        wl, wr, angle = v["wl"][i], v["wr"][i], math.radians(v["bank"][i])
        if v.get("input"):
            extent = max(1, wl, wr)
            scale = max(1, (self.width() - 100) / (2 * extent))
            center = QPointF(self.width() / 2, self.height() / 2 + 20)
            painter.setPen(QPen(QColor("#67D9F5"), 4))
            painter.drawLine(center + QPointF(-wl * scale, -wl * math.tan(angle) * scale * 5), center + QPointF(wr * scale, wr * math.tan(angle) * scale * 5))
            painter.setPen(self.palette().text().color())
            painter.drawText(18, 49, tr("Left {wl} · right {wr} · banking {vorzeichen}{bank}°", wl=zahl(wl, 2, 'm'), wr=zahl(wr, 2, 'm'),
                                vorzeichen='+' if v['bank'][i] >= 0 else '', bank=zahl(v['bank'][i], 2)))
            return
        left, right = v["sides"]["links"], v["sides"]["rechts"]
        extent = max(wl + left["distances"][i][-1], wr + right["distances"][i][-1], 1)
        scale = (self.width() - 60) / (2 * extent)
        middle, level = self.width() / 2, self.height() * 0.58
        rise_l, rise_r = self._edge_rise(i, angle)
        reach = v.get("edge_reach", 12.0)
        def height(d):
            return (max(-wr, min(wl, d)) * math.tan(angle) + min(max(d - wl, 0), reach) * rise_l
                    + min(max(-wr - d, 0), reach) * rise_r)
        def xy(d):
            return QPointF(middle - d * scale, level - height(d) * scale * exaggeration)
        def strip(a, b, material):
            # am Ende der Reichweite knickt der Rand: dort teilen
            cuts = [x for x in (wl + reach, -wr - reach) if min(a, b) < x < max(a, b)]
            steps = sorted([a, b] + cuts, reverse=a > b)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(MATERIALS[material]))
            for p, q in zip(steps, steps[1:]):
                pa, pb = xy(p), xy(q)
                painter.drawPolygon(QPolygonF([pa, pb, pb + QPointF(0, 14), pa + QPointF(0, 14)]))
        strip(-wr, wl, 0)
        line = min(v.get("line_width", 0.25), wl, wr)
        if line > 0:
            strip(wl - line, wl, 6)
            strip(-wr, -wr + line, 6)
        names = {tr("Asphalt")}
        for side, sign, width in ((left, 1, wl), (right, -1, wr)):
            distances = side["distances"][i]
            for band in range(5):
                if distances[band + 1] - distances[band] > 0.02:
                    material = side["bands"][i][band]
                    strip(sign * (width + distances[band]), sign * (width + distances[band + 1]), material)
                    names.add(tr(MATERIAL_NAMES[material]))
            barrier_position = xy(sign * (width + distances[-1]))
            painter.setPen(QPen(QColor(MATERIALS[4]), 3))
            painter.drawLine(barrier_position, barrier_position - QPointF(0, min(45, v.get("barrier", {}).get("hoehe", 1.2) * scale * exaggeration)))
        names.add(tr("Barrier"))
        painter.setPen(self.palette().text().color())
        painter.drawText(18, 49, tr("Left {wl}  /  right {wr}  ·  {vorzeichen}{bank}°  ·  station {station}", wl=zahl(wl, 2, 'm'), wr=zahl(wr, 2, 'm'),
                                    vorzeichen='+' if v['bank'][i] >= 0 else '', bank=zahl(v['bank'][i], 2), station=zahl(self.station, 1, 'm')))
        painter.drawText(18, self.height() - 15, tr("Bands: ") + " · ".join(sorted(names)))

    def _edge_rise(self, i, angle):
        """Höhenänderung je Meter nach außen (links, rechts): aus der Vorschau, die Eingabe vor „Übernehmen“ geht vor."""
        left, right = self.preview["sides"]["links"], self.preview["sides"]["rechts"]
        rise = [left["edge_slope"][i] if left.get("edge_slope") else 0.0,
                right["edge_slope"][i] if right.get("edge_slope") else 0.0]
        if self.edge_input is not None:
            value = self.edge_input["wert"]
            for k, (name, sign) in enumerate((("links", 1), ("rechts", -1))):
                if self.edge_input["seite"] in ("beide", name):
                    rise[k] = (0.0 if value is None else sign * math.tan(angle) if value == "strecke"
                               else math.tan(math.radians(float(value))))
        return rise

    def _exaggeration(self):
        """Überhöhung, bei der der Querschnitt noch ins Bild passt; ohne Randwinkel wie bisher."""
        v = self.preview
        if not v or not v.get("pos") or v.get("input"):
            return CROSS_EXAGGERATION
        i = min(range(len(v["s"])), key=lambda j: abs(v["s"][j] - self.station))
        wl, wr, angle = v["wl"][i], v["wr"][i], math.radians(v["bank"][i])
        rise_l, rise_r = self._edge_rise(i, angle)
        if not rise_l and not rise_r:
            return CROSS_EXAGGERATION
        left, right = v["sides"]["links"], v["sides"]["rechts"]
        reach = v.get("edge_reach", 12.0)
        extent = max(wl + left["distances"][i][-1], wr + right["distances"][i][-1], 1)
        scale = (self.width() - 60) / (2 * extent)
        tops = [wl * math.tan(angle) + min(left["distances"][i][-1], reach) * rise_l, -wr * math.tan(angle)
                + min(right["distances"][i][-1], reach) * rise_r, wl * math.tan(angle), -wr * math.tan(angle)]
        room = self.height() * 0.28                 # Platz über und unter der Mittellinie (Texte oben und unten)
        largest = max(abs(h) for h in tops) * scale
        return max(1.0, min(CROSS_EXAGGERATION, room / largest)) if largest > 0 else CROSS_EXAGGERATION


def label_rows(spans, rows=2):
    """Zeile je Beschriftung (links, rechts) von links nach rechts: die erste freie, sonst die am wenigsten volle."""
    ends, out = [float("-inf")] * rows, []
    for left, right in spans:
        row = next((r for r in range(rows) if left >= ends[r]), min(range(rows), key=lambda r: ends[r]))
        ends[row] = right
        out.append(row)
    return out


class SpeedView(QWidget):
    """Geschwindigkeit der Ideallinie über die Runde (Fahrlinienlauf), Bremszonen rot, Kurvennummern T1 …;
    Klick setzt den Stationscursor (gekoppelt mit Karte und Profilen)."""
    station_changed = Signal(float)

    def __init__(self):
        super().__init__()
        self.analysis = None
        self.stale = False
        self.station = 0.0
        self.setMinimumHeight(150)
        self.setMouseTracking(True)
        self.setAccessibleName(tr("Speed over the lap from the racing-line run"))

    def _plot(self):
        return QRectF(52, 36, max(10, self.width() - 70), max(10, self.height() - 70))   # unten 2 Zeilen Kurvennummern

    def _points(self):
        line = self.analysis["linie"]
        pts = sorted(zip(line["s"], line["v"]))
        return pts

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        text = self.palette().text().color()
        painter.setPen(text)
        if not self.analysis:
            painter.drawText(24, 40, tr("Speed: appears after the racing-line run (starts automatically after a "
                                        "change, or “Check” / Ctrl+Enter)."))
            return
        a = self.analysis
        plot = self._plot()
        length = max(1.0, float(a["laenge_m"]))
        top = max(50.0, math.ceil(float(a["v_max_kmh"]) / 50.0) * 50.0)

        def xy(s, v):
            return QPointF(plot.left() + s / length * plot.width(), plot.bottom() - v / top * plot.height())
        painter.drawText(16, 22, tr(("Speed of the racing line in km/h over the lap ({length:.0f} m from the finish) "
                                     "· red: braking zones · click: cursor"), length=length) + (tr((" · outdated, being "
                                                                                                                                                                              "recalculated")) if self.stale else ""))
        if self.stale:
            painter.setOpacity(.45)
        painter.setPen(QPen(QColor("#52677A"), 1))
        painter.drawRect(plot)
        for v in range(50, int(top), 50):
            y = xy(0, v).y()
            painter.setPen(QPen(QColor("#52677A"), 1, Qt.DotLine))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(text)
            painter.drawText(QRectF(4, y - 9, 44, 18), Qt.AlignRight | Qt.AlignVCenter, str(v))
        for zone in a.get("bremszonen", []):
            x0, x1 = xy(zone["s_start"], 0).x(), xy(zone["s_ende"], 0).x()
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(240, 120, 120, 55))
            if x1 >= x0:
                painter.drawRect(QRectF(x0, plot.top(), x1 - x0, plot.height()))
            else:                                       # über die Ziellinie
                painter.drawRect(QRectF(x0, plot.top(), plot.right() - x0, plot.height()))
                painter.drawRect(QRectF(plot.left(), plot.top(), x1 - plot.left(), plot.height()))
        pts = self._points()
        path = QPainterPath()
        for k, (s, v) in enumerate(pts):
            p = xy(s, v)
            if k and s - pts[k - 1][0] > 30:            # Sprung an der Ziellinie nicht verbinden
                path.moveTo(p)
            elif k:
                path.lineTo(p)
            else:
                path.moveTo(p)
        painter.setPen(QPen(QColor("#67D9F5"), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        painter.setPen(text)
        shown, marks = set(), []
        for curve in sorted(a.get("kurven", []), key=lambda c: c["s_scheitel"]):
            if curve["spiel"] not in shown:
                shown.add(curve["spiel"])
                marks.append((xy(curve["s_scheitel"], 0).x(), "T%d" % curve["spiel"]))
        metrics = painter.fontMetrics()
        half = {label: metrics.horizontalAdvance(label) / 2 + 3 for _, label in marks}
        # Zu dicht an der vorigen Nummer: zweite Zeile (sonst „T9T10“)
        for (x, label), row in zip(marks, label_rows([(x - half[label], x + half[label]) for x, label in marks])):
            painter.setPen(QPen(QColor("#8D9BA9"), 1, Qt.DotLine))
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            painter.setPen(text)
            painter.drawText(QRectF(x - 20, plot.bottom() + 1 + 14 * row, 40, 15), Qt.AlignCenter, label)
        painter.setOpacity(1)
        x = xy(self.station % length, 0).x()
        painter.setPen(QPen(QColor("#FFD184"), 1.5))
        painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
        v_here = self.speed_at(self.station)
        if v_here is not None:
            label = dezimal(tr("{station:.0f} m · {v_here:.0f} km/h", station=self.station % length, v_here=v_here))
            painter.drawText(QRectF(min(x + 6, plot.right() - 150), plot.top() + 2, 150, 16), Qt.AlignLeft, label)

    def speed_at(self, s):
        if not self.analysis:
            return None
        pts = self._points()
        if not pts:
            return None
        s %= float(self.analysis["laenge_m"])
        return min(pts, key=lambda p: abs(p[0] - s))[1]

    def mousePressEvent(self, event):
        if not self.analysis or event.button() != Qt.LeftButton:
            return
        plot = self._plot()
        x = min(max(event.position().x(), plot.left()), plot.right())
        self.station_changed.emit((x - plot.left()) / plot.width() * float(self.analysis["laenge_m"]))
