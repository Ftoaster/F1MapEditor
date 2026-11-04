"""Reiter „Umgebung“: Katalog (Suche, Kategorie, Vorschau), Bepflanzen-Einstellungen und Eigenschaften der Auswahl.

Das Widget enthält keine Dokumentlogik: es meldet Wünsche über Signale; das Hauptfenster führt sie über
``Document`` (Undo) aus und füllt das Widget mit Katalog, Vorschaubild und Auswahl.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from . import umgebung
from .i18n import tr
from .ux import zahl


def _kreis(farbe, groesse=12):
    pixmap = QPixmap(groesse, groesse)
    pixmap.fill(Qt.transparent)
    from PySide6.QtGui import QPainter
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(farbe))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(1, 1, groesse - 2, groesse - 2)
    painter.end()
    return QIcon(pixmap)


def _spin(minimum, maximum, wert, schritt, nachkomma=1, suffix=""):
    box = QDoubleSpinBox()
    box.setRange(minimum, maximum)
    box.setDecimals(nachkomma)
    box.setSingleStep(schritt)
    box.setValue(wert)
    box.setSuffix(suffix)
    box.setKeyboardTracking(False)
    return box


class EnvironmentPanel(QScrollArea):
    model_chosen = Signal(object)            # Modell-Kennung zum Platzieren oder None
    preview_requested = Signal(str)          # Silhouette dieses Modells anfordern
    refresh_requested = Signal()             # Katalog neu einlesen
    settings_changed = Signal()              # Form/Radius des Bepflanzens geändert
    properties_requested = Signal(dict)      # Felder für die gewählten Objekte {feld: wert}
    ground_cover_toggled = Signal(bool)
    select_group_requested = Signal()
    delete_group_requested = Signal()
    delete_requested = Signal()
    terrain_armed = Signal(object)           # "huegel"/"berge" zum Setzen per Klick oder None
    terrain_values_changed = Signal(dict)    # Höhe/Radius des gewählten Hügels/Bergs {feld: wert}
    terrain_delete_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.index = {}
        self.models = []
        self._loading = False
        self._icons = {}
        # In einer Rollfläche: der Reiter darf das Fenster nicht verbreitern (Reiter-Mindestgröße = größte Seite)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        self.setWidget(inner)
        root = QHBoxLayout(inner)
        root.setContentsMargins(6, 4, 6, 4)
        root.addWidget(self._catalog_box(), 5)
        root.addWidget(self._detail_box(), 2)
        root.addWidget(self._scatter_box(), 4)
        # Die Auswahl (Eigenschaften der gewählten Objekte, Bodenbewuchs) und das Feld „Gelände“ sitzen im Hauptfenster
        # rechts bei den Eigenschaften; das Fenster fügt ``selection_box`` und ``terrain_box`` dort ein (sichtbar in den
        # Umgebungswerkzeugen).
        self.selection_box = self._selection_box()
        self.terrain_box = self._terrain_box()     # ebenfalls rechts im Hauptfenster (Werkzeug „Umgebung“)
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(150)
        self.preview_timer.timeout.connect(self._request_preview)
        self.show_selection([], {})
        self.show_terrain(None, None)

    # ------------------------------------------------------------------ Aufbau
    def _catalog_box(self):
        box = QGroupBox(tr("Catalogue"))
        layout = QVBoxLayout(box)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Search model …"))
        self.search.setClearButtonEnabled(True)
        self.search.setToolTip(tr("Searches the name, family and identifier of the model."))
        self.search.textChanged.connect(self.fill_list)
        self.category = QComboBox()
        self.category.addItem(tr("All categories"), None)
        for key in umgebung.KATEGORIEN:
            self.category.addItem(tr(umgebung.KATEGORIE_NAMEN[key]), key)
        self.category.currentIndexChanged.connect(self.fill_list)
        self.reload_button = QPushButton(tr("Reload"))
        self.reload_button.setToolTip(tr(("Rebuild the catalogue from the game folder (takes a while; the game folder "
                                          "is only read).")))
        self.reload_button.clicked.connect(self.refresh_requested)
        row.addWidget(self.search, 1)
        row.addWidget(self.category)
        row.addWidget(self.reload_button)
        layout.addLayout(row)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setUniformItemSizes(True)
        self.list.setAccessibleName(tr("Catalogue of environment models"))
        self.list.setToolTip(tr("Choose a model and click the map to place it. Choose several (Ctrl/Shift): mix for "
                                "“Plant area”."))
        self.list.itemSelectionChanged.connect(self._list_changed)
        layout.addWidget(self.list, 1)
        self.status = QLabel(tr("Catalogue not loaded yet"))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        return box

    def _detail_box(self):
        box = QGroupBox(tr("Model"))
        layout = QVBoxLayout(box)
        self.preview = QLabel(tr("no preview"))
        self.preview.setFixedSize(150, 150)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet("border: 1px solid #44566A; border-radius: 4px;")
        layout.addWidget(self.preview, 0, Qt.AlignHCenter)
        self.details = QLabel()
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.details)
        layout.addStretch(1)
        return box

    def _scatter_box(self):
        box = QGroupBox(tr("Plant area (tool B)"))
        form = QFormLayout(box)
        form.setVerticalSpacing(3)
        self.form_choice = QComboBox()
        self.form_choice.addItem(tr("Rectangle"), "rechteck")
        self.form_choice.addItem(tr("Brush"), "pinsel")
        self.form_choice.currentIndexChanged.connect(self._form_changed)
        self.radius = _spin(2, 300, 30, 5, 0, " m")
        self.radius.setToolTip(tr("Brush radius: how wide the stroke is"))
        self.radius.valueChanged.connect(lambda *_: self.settings_changed.emit())
        row = QHBoxLayout()
        row.addWidget(self.form_choice, 1)
        row.addWidget(self.radius)
        form.addRow(tr("Area"), row)
        self.density = _spin(1, 2000, 60, 10, 0, tr(" per ha"))
        self.density.setToolTip(tr("Objects per hectare (100 × 100 m); 400 is roughly one tree every 5 m"))
        form.addRow(tr("Density"), self.density)
        self.track_distance = _spin(0, 300, umgebung.STANDARD_ABSTAND_FAHRBAHN, 1, 0, " m")
        self.track_distance.setToolTip(tr("Minimum distance to the wall or outer edge of the track (road, edge bands, "
                                          "wall, pit lane); default 15 m behind the wall"))
        form.addRow(tr("Distance to track"), self.track_distance)
        self.object_distance = _spin(0, 100, 3, 0.5, 1, " m")
        self.object_distance.setToolTip(tr("Minimum distance between objects (also to existing ones)"))
        form.addRow(tr("Spacing between objects"), self.object_distance)
        row = QHBoxLayout()
        self.scale_min = _spin(umgebung.SKALA_MIN, umgebung.SKALA_MAX, 0.8, 0.1, 2)
        self.scale_max = _spin(umgebung.SKALA_MIN, umgebung.SKALA_MAX, 1.2, 0.1, 2)
        row.addWidget(self.scale_min)
        row.addWidget(QLabel("–"))
        row.addWidget(self.scale_max)
        form.addRow(tr("Scale from – to"), row)
        row = QHBoxLayout()
        self.random_rotation = QCheckBox(tr("Random rotation"))
        self.random_rotation.setChecked(True)
        self.seed = QSpinBox()
        self.seed.setRange(0, 999999)
        self.seed.setValue(1)
        self.seed.setToolTip(tr("Seed: the same value and settings give the same scatter"))
        self.dice = QPushButton(tr("Roll dice"))
        self.dice.setToolTip(tr("Choose a new seed (different distribution)"))
        self.dice.clicked.connect(self.roll_seed)
        row.addWidget(self.random_rotation, 1)
        row.addWidget(QLabel(tr("Random seed")))
        row.addWidget(self.seed)
        row.addWidget(self.dice)
        form.addRow(row)
        self.group_name = QLineEdit()
        self.group_name.setPlaceholderText(tr("Scatter 1, Scatter 2 …"))
        self.group_name.setToolTip(tr("Group name: a scatter can be selected and deleted together"))
        form.addRow(tr("Group"), self.group_name)
        self._form_changed()
        return box

    def _terrain_box(self):
        box = QGroupBox(tr("Terrain"))
        layout = QVBoxLayout(box)
        row = QHBoxLayout()
        self.hill_button = QPushButton(tr("Place hill"))
        self.hill_button.setCheckable(True)
        self.hill_button.setToolTip(tr("Hill or hollow in the ground surface of the track area: visible and drivable. "
                                       "The build flattens it towards the track (30 m + 40 m transition)."))
        self.mountain_button = QPushButton(tr("Place mountain"))
        self.mountain_button.setCheckable(True)
        self.mountain_button.setToolTip(tr("Mountain in the outer terrain outside the track area: scenery on the "
                                           "horizon only, no driving physics. Zoom out to place it."))
        self.hill_button.toggled.connect(lambda on: self._arm("huegel", on))
        self.mountain_button.toggled.connect(lambda on: self._arm("berge", on))
        row.addWidget(self.hill_button)
        row.addWidget(self.mountain_button)
        layout.addLayout(row)
        form = QFormLayout()
        form.setVerticalSpacing(3)
        self.terrain_height = _spin(-60, 2000, 40, 10, 0, " m")
        self.terrain_height.setToolTip(tr("Height at the centre; negative = hollow (hills only)"))
        self.terrain_radius = _spin(30, 5000, 200, 10, 0, " m")
        self.terrain_radius.setToolTip(tr("Radius of the shape; the edge fades out smoothly (cosine bell)"))
        form.addRow(tr("Height"), self.terrain_height)
        form.addRow(tr("Radius"), self.terrain_radius)
        layout.addLayout(form)
        self.terrain_height.valueChanged.connect(lambda v: self._terrain_send("hoehe_m", v))
        self.terrain_radius.valueChanged.connect(lambda v: self._terrain_send("radius_m", v))
        self.terrain_text = QLabel()
        self.terrain_text.setWordWrap(True)
        layout.addWidget(self.terrain_text)
        self.terrain_delete = QPushButton(tr("Delete shape"))
        self.terrain_delete.setToolTip(tr("Remove the selected hill/mountain (Del)"))
        self.terrain_delete.clicked.connect(self.terrain_delete_requested)
        layout.addWidget(self.terrain_delete)
        layout.addStretch(1)
        self._terrain_form = None
        return box

    def _set_terrain_range(self, art):
        grenzen = umgebung.FORM_GRENZEN[art]
        for box, feld in ((self.terrain_height, "hoehe_m"), (self.terrain_radius, "radius_m")):
            box.blockSignals(True)
            box.setRange(*grenzen[feld])
            box.blockSignals(False)

    def _arm(self, art, on):
        if self._loading:
            return
        andere = self.mountain_button if art == "huegel" else self.hill_button
        if on:
            self._loading = True
            andere.setChecked(False)
            self._set_terrain_range(art)
            for box, feld in ((self.terrain_height, "hoehe_m"), (self.terrain_radius, "radius_m")):
                box.setValue(umgebung.FORM_STANDARD[art][feld])
            self._loading = False
            self.disarm_models()
            self.terrain_armed.emit(art)
        elif not andere.isChecked():
            self.terrain_armed.emit(None)

    def armed_terrain(self):
        return "huegel" if self.hill_button.isChecked() else "berge" if self.mountain_button.isChecked() else None

    def disarm_terrain(self):
        self._loading = True
        self.hill_button.setChecked(False)
        self.mountain_button.setChecked(False)
        self._loading = False

    def terrain_values(self):
        return {"hoehe_m": self.terrain_height.value(), "radius_m": self.terrain_radius.value()}

    def _terrain_send(self, feld, wert):
        if not self._loading and self._terrain_form is not None:
            self.terrain_values_changed.emit({feld: wert})

    def show_terrain(self, art, form, problem=None, editierbar=True):
        """Gewählten Hügel/Berg anzeigen (``form`` None: Werte gelten für den nächsten gesetzten)."""
        self._loading = True
        try:
            self._terrain_form = form["id"] if form else None
            self.terrain_delete.setEnabled(form is not None and editierbar)
            for widget in (self.hill_button, self.mountain_button):
                widget.setEnabled(editierbar)
            if form is None:
                self.terrain_text.setText(tr("Hills lie inside the track area (visible and drivable), mountains "
                                             "outside (scenery only). Press a button, then click on the map."))
                return
            self._set_terrain_range(art)
            self.terrain_height.setValue(form["hoehe_m"])
            self.terrain_radius.setValue(form["radius_m"])
            text = tr("{name} “{id}” · X {x} · Z {z}", name=tr(umgebung.FORM_NAMEN[art]), id=form["id"],
                      x=zahl(form["x"], 0), z=zahl(form["z"], 0))
            if problem:
                text += "\n⚠ " + tr(problem)
            self.terrain_text.setText(text)
        finally:
            self._loading = False

    def _selection_box(self):
        box = QGroupBox(tr("Environment · selection"))
        layout = QVBoxLayout(box)
        self.selection_text = QLabel()
        self.selection_text.setWordWrap(True)
        layout.addWidget(self.selection_text)
        form = QFormLayout()
        form.setVerticalSpacing(3)
        self.height_box = _spin(umgebung.HOEHE_MIN, umgebung.HOEHE_MAX, 0, 0.5, 2, " m")
        self.height_box.setToolTip(tr(("Distance of the base point above the terrain (the build determines the "
                                       "terrain height itself)")))
        self.rotation_box = _spin(0, 360, 0, 15, 1, "°")
        self.rotation_box.setWrapping(True)
        self.rotation_box.setToolTip(tr("Rotation about the vertical axis, mathematically positive in plan view"))
        self.scale_box = _spin(umgebung.SKALA_MIN, umgebung.SKALA_MAX, 1, 0.1, 2)
        self.group = QLineEdit()
        self.group.setToolTip(tr("Group (scatter); empty = none"))
        form.addRow(tr("Height above ground"), self.height_box)
        form.addRow(tr("Rotation"), self.rotation_box)
        form.addRow(tr("Object scale"), self.scale_box)
        form.addRow(tr("Group"), self.group)
        layout.addLayout(form)
        self.height_box.editingFinished.connect(lambda: self._send("hoehe_m", self.height_box.value()))
        self.rotation_box.editingFinished.connect(lambda: self._send("drehung_grad", self.rotation_box.value()))
        self.scale_box.editingFinished.connect(lambda: self._send("skala", self.scale_box.value()))
        self.group.editingFinished.connect(lambda: self._send("gruppe", self.group.text().strip()))
        buttons = QGridLayout()
        self.group_button = QPushButton(tr("Select group"))
        self.group_button.clicked.connect(self.select_group_requested)
        self.group_delete = QPushButton(tr("Delete group"))
        self.group_delete.clicked.connect(self.delete_group_requested)
        self.delete_button = QPushButton(tr("Delete"))
        self.delete_button.setToolTip(tr("Remove the selected objects (Del)"))
        self.delete_button.clicked.connect(self.delete_requested)
        buttons.addWidget(self.group_button, 0, 0)
        buttons.addWidget(self.group_delete, 0, 1)
        buttons.addWidget(self.delete_button, 1, 0, 1, 2)
        layout.addLayout(buttons)
        self.ground_cover = QCheckBox(tr("Ground cover (grass/weeds)"))
        self.ground_cover.setToolTip(tr("Off: the build clears the ground cover as before. On: grass and weeds of the "
                                        "carrier are kept. New environment files start with “on”."))
        self.ground_cover.toggled.connect(lambda on: None if self._loading else self.ground_cover_toggled.emit(on))
        layout.addWidget(self.ground_cover)
        self.count_label = QLabel()
        layout.addWidget(self.count_label)
        layout.addStretch(1)
        return box

    # ------------------------------------------------------------------ Katalog
    def set_catalog(self, katalog, text=None):
        """Katalog setzen (None = nicht geladen); ``text`` ist die Statuszeile darunter."""
        self.models = list((katalog or {}).get("modelle", []))
        self.index = umgebung.katalog_index(katalog)
        self.fill_list()
        self.status.setText(text if text is not None else
                            tr("{anzahl} models in the catalogue", anzahl=zahl(len(self.models), 0)) if katalog else
                            tr("Catalogue not loaded yet"))

    def fill_list(self, *_args):
        key = self.category.currentData()
        needle = self.search.text().strip().casefold()
        keep = {i.data(Qt.UserRole) for i in self.list.selectedItems()}
        self.list.blockSignals(True)
        self.list.clear()
        shown = 0
        for m in self.models:
            if key and m.get("kategorie") != key:
                continue
            if needle and needle not in f"{m.get('name', '')} {m.get('familie', '')} {m.get('modell', '')}".casefold():
                continue
            size = m.get("groesse_m") or [0, 0, 0]
            item = QListWidgetItem(f"{m.get('name') or umgebung.kurzname(m['modell'])}   ·   "
                                   f"{zahl(size[0], 0)} × {zahl(size[2], 0)} × {zahl(size[1], 0)} m")
            item.setData(Qt.UserRole, m["modell"])
            item.setIcon(self._icon(m.get("kategorie")))
            item.setToolTip(f"{m['modell']}\n{tr(umgebung.KATEGORIE_NAMEN.get(m.get('kategorie'), 'Sonstiges'))}")
            self.list.addItem(item)
            if m["modell"] in keep:
                item.setSelected(True)
            shown += 1
            if shown >= 4000:
                break
        self.list.blockSignals(False)
        self.list.setIconSize(QSize(12, 12))

    def _icon(self, kategorie):
        if kategorie not in self._icons:
            self._icons[kategorie] = _kreis(umgebung.KATEGORIE_FARBEN.get(kategorie, umgebung.UNBEKANNT_FARBE))
        return self._icons[kategorie]

    def chosen_models(self):
        """Gewählte Modelle (Mischung für das Bepflanzen); leer, wenn nichts gewählt ist."""
        return [i.data(Qt.UserRole) for i in self.list.selectedItems()]

    def armed_model(self):
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item is not None and item.isSelected() else None

    def disarm(self):
        """Auswahl im Katalog und Geländeknöpfe aufheben (Esc auf der Karte)."""
        self.disarm_terrain()
        self.disarm_models()

    def disarm_models(self):
        self.list.blockSignals(True)
        self.list.clearSelection()
        self.list.setCurrentItem(None)
        self.list.blockSignals(False)
        self._show_details(None)

    def select_models(self, modelle):
        """Modelle im Katalog wählen (für Tests und Sprünge); meldet wie eine Bedienung."""
        wanted = set(modelle)
        self.list.clearSelection()
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(Qt.UserRole) in wanted:
                item.setSelected(True)
                self.list.setCurrentItem(item)

    def _list_changed(self):
        modell = self.armed_model()
        if modell and self.armed_terrain():
            self.disarm_terrain()
            self.terrain_armed.emit(None)
        self._show_details(modell)
        self.model_chosen.emit(modell)
        if modell:
            self.preview_timer.start()

    def _show_details(self, modell):
        eintrag = self.index.get(modell) if modell else None
        if not eintrag:
            self.details.setText(tr("No model chosen.") if not modell else
                                 tr("{modell}\nNot in the catalogue.", modell=umgebung.kurzname(modell)))
            self.preview.setPixmap(QPixmap())
            self.preview.setText(tr("no preview"))
            return
        size = eintrag.get("groesse_m") or [0, 0, 0]
        lines = [f"<b>{eintrag.get('name') or umgebung.kurzname(modell)}</b>",
                 tr(umgebung.KATEGORIE_NAMEN.get(eintrag.get("kategorie"), "Sonstiges")) + (f" · {eintrag['familie']}" if eintrag.get("familie") else ""),
                 tr("Size {x} × {z} × {y} m (width × depth × height)", x=zahl(size[0], 1), z=zahl(size[2], 1), y=zahl(size[1], 1)),
                 tr("Radius on the map {radius} m", radius=zahl(eintrag.get("radius_m") or 0, 1))]
        if eintrag.get("quelle"):
            lines.append(tr("Source: {quelle}", quelle=eintrag["quelle"]))
        lines.append(tr("already in the carrier package") if eintrag.get("im_traeger") else tr(("copied from the game during "
                                                                                      "the build")))
        self.details.setText("<br>".join(lines))
        self.preview.setPixmap(QPixmap())
        self.preview.setText(tr("Creating preview …"))

    def _request_preview(self):
        modell = self.armed_model()
        if modell:
            self.preview_requested.emit(modell)

    def set_preview(self, modell, pfad):
        """Silhouette anzeigen, wenn sie noch zum gewählten Modell gehört (``pfad`` None = nicht verfügbar)."""
        if modell != self.armed_model():
            return
        pixmap = QPixmap(str(pfad)) if pfad else QPixmap()
        if pixmap.isNull():
            self.preview.setPixmap(QPixmap())
            self.preview.setText(tr("no preview"))
        else:
            self.preview.setPixmap(pixmap.scaled(self.preview.size() - QSize(4, 4), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    # ------------------------------------------------------------------ Bepflanzen
    def _form_changed(self, *_args):
        self.radius.setEnabled(self.form_choice.currentData() == "pinsel")
        self.settings_changed.emit()

    def roll_seed(self):
        import random
        self.seed.setValue(random.randint(0, 999999))

    def scatter_settings(self):
        return {"form": self.form_choice.currentData(), "radius": self.radius.value(), "dichte": self.density.value(),
                "abstand_fahrbahn": self.track_distance.value(), "abstand_objekte": self.object_distance.value(),
                "skala": (self.scale_min.value(), self.scale_max.value()), "drehung_zufall": self.random_rotation.isChecked(),
                "seed": self.seed.value(), "gruppe": self.group_name.text().strip()}

    # ------------------------------------------------------------------ Auswahl
    def show_selection(self, objekte, index, anzahl=0, bodenbewuchs=None, editierbar=True):
        """Eigenschaften der gewählten Objekte anzeigen (``objekte``: Liste; bei mehreren gelten die Werte des ersten)."""
        self._loading = True
        try:
            first = objekte[0] if objekte else None
            for widget in (self.height_box, self.rotation_box, self.scale_box, self.group, self.delete_button):
                widget.setEnabled(first is not None and editierbar)
            if first is None:
                self.selection_text.setText(tr("Nothing selected. Click an object with the “Environment” tool (U)."))
                self.group.setText("")
            else:
                name = umgebung.kurzname(first["modell"])
                self.selection_text.setText(name if len(objekte) == 1 else
                                            tr("{anzahl} objects · values of the first: {name}", anzahl=len(objekte), name=name))
                self.height_box.setValue(first.get("hoehe_m", 0.0))
                self.rotation_box.setValue(first.get("drehung_grad", 0.0))
                self.scale_box.setValue(first.get("skala", 1.0))
                self.group.setText(first.get("gruppe", ""))
            has_group = bool(first and first.get("gruppe"))
            self.group_button.setEnabled(has_group)
            self.group_delete.setEnabled(has_group and editierbar)
            self.ground_cover.setChecked(bool(bodenbewuchs))
            self.ground_cover.setEnabled(editierbar)
            self.count_label.setText(tr("{anzahl} objects in the environment", anzahl=zahl(anzahl, 0)))
        finally:
            self._loading = False

    def _send(self, feld, wert):
        if not self._loading:
            self.properties_requested.emit({feld: wert})
