"""Dialoge „Neu aus Vorlage“ und „Vorschlag erzeugen“ (Schritt F). Die Berechnung liegt im Werkzeugkasten (adapter)."""

from __future__ import annotations

import random
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout,
)

from .i18n import N_, tr

KINDS = (
    ("tum", N_("TUM race track (CSV: x_m, y_m, w_tr_right_m, w_tr_left_m)"), N_("TUM CSV (*.csv);;All files (*)")),
    ("gpx", N_("GPS track (GPX, circuit)"), N_("GPX (*.gpx);;All files (*)")),
    ("spiel", N_("Game track from F1 25 (read-only)"), N_("ERP or mod package (*.erp *.zip);;All files (*)")),
)


def guess_code(path):
    """``…/monza_common.erp`` → ``monza``; Mod-ERP/ZIP: Dateiname ohne Endung."""
    name = Path(path).stem
    return name[: -len("_common")] if name.endswith("_common") else name


class TemplateDialog(QDialog):
    """Quelle, Datei bzw. Spielstrecke, ggf. Streckencode, Übernahme-Optionen und Einpassen wählen.

    ``game_tracks``: Funktion ohne Argumente, die die Spielstrecken liefert (``[{code, name, datei, bruecke}]``, siehe
    ``adapter.spielstrecken``); sie läuft erst, wenn die Quelle „Spielstrecke“ gewählt wird. ``values()`` liefert die
    Eingaben für den Adapter."""

    def __init__(self, parent=None, game_tracks=None):
        super().__init__(parent)
        self.setWindowTitle(tr("New from template"))
        self.setMinimumWidth(600)
        self._game_tracks_source = game_tracks
        self._game_tracks = None
        self.game_tracks_error = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("The shape of an existing track opens as a new, unsaved draft.\nYour current "
                                   "project stays untouched until you save; source files are only read.")))
        form = QFormLayout()
        self.kind = QComboBox()
        self.kind.setAccessibleName(tr("Template source"))
        for key, title, _ in KINDS:
            title = tr(title)
            self.kind.addItem(title, key)
        form.addRow(tr("Source"), self.kind)
        self.track = QComboBox()
        self.track.setAccessibleName(tr("Game track"))
        self.track_label = QLabel(tr("Track"))
        form.addRow(self.track_label, self.track)
        row = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setAccessibleName(tr("Path of the template file"))
        self.browse_button = QPushButton(tr("Browse …"))
        row.addWidget(self.path, 1)
        row.addWidget(self.browse_button)
        self.path_label = QLabel(tr("File"))
        form.addRow(self.path_label, row)
        self.code = QLineEdit()
        self.code.setAccessibleName(tr("Track code of the game track"))
        self.code.setPlaceholderText(tr("e.g. monza, silverstone, spa_francorchamps"))
        self.code_label = QLabel(tr("Track code"))
        form.addRow(self.code_label, self.code)
        self.spacing = QDoubleSpinBox()
        self.spacing.setRange(0, 200)
        self.spacing.setDecimals(0)
        self.spacing.setSuffix(" m")
        self.spacing.setSpecialValueText(tr("all points"))
        self.spacing.setToolTip(tr("Spacing of the control points. Fewer points are easier to edit; the smoothing "
                                   "approximation (6 m) evens out gaps."))
        self.spacing_label = QLabel(tr("Point spacing"))
        form.addRow(self.spacing_label, self.spacing)
        # Übernahme aus der Spielstrecke (Ausbaustufen 4–6, docs/mapeditor/spielstrecken_laden.md)
        self.take_logic = QCheckBox(tr("Import pit lane, DRS and Straight Mode zones and starting grid"))
        self.take_logic.setChecked(True)
        self.take_sides = QCheckBox(tr("Import side areas from the driving physics (kerbs, gravel, asphalt, barriers)"))
        self.take_sides.setChecked(True)
        self.take_scenery = QCheckBox(tr("Import scenery (trees, buildings, tents … as environment objects)"))
        self.take_scenery.setChecked(True)
        self.take_scenery.setToolTip(tr("Only models from the environment catalogue, without flags and without objects "
                                        "on the new track. Terrain, textures and lighting of the original are not "
                                        "imported."))
        for box in (self.take_logic, self.take_sides, self.take_scenery):
            form.addRow("", box)
        self.fit = QCheckBox(tr("Scale down if the track does not fit into the game world even when rotated"))
        self.fit.setChecked(False)
        form.addRow("", self.fit)
        layout.addLayout(form)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText(tr("Import"))
        self.buttons.button(QDialogButtonBox.Cancel).setText(tr("Cancel"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.browse_button.clicked.connect(self.browse)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.track.currentIndexChanged.connect(self._kind_changed)
        self.path.textChanged.connect(self._path_changed)
        self._kind_changed()

    # ------------------------------------------------------------------ Spielstrecken
    def _load_game_tracks(self):
        """Liste einmal laden (Arbeiterprozess, etwa eine Sekunde); ohne Spielordner bleibt nur „Andere Datei“."""
        if self._game_tracks is not None:
            return
        self._game_tracks = []
        if self._game_tracks_source is not None:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                self._game_tracks = list(self._game_tracks_source())
            except (ValueError, OSError, RuntimeError) as error:
                self.game_tracks_error = str(error)
            finally:
                QApplication.restoreOverrideCursor()
        self.track.blockSignals(True)
        self.track.clear()
        for entry in self._game_tracks:
            name = entry["name"] + (" · " + tr("bridge, cannot be built") if entry.get("bruecke") else "")
            self.track.addItem(name, entry)
        self.track.addItem(tr("Other file (mod ERP or mod package) …"), None)
        self.track.blockSignals(False)

    def selected_game_track(self):
        """Gewählter Eintrag der Spielstreckenliste oder None („Andere Datei“ bzw. andere Quelle)."""
        if self.kind.currentData() != "spiel":
            return None
        return self.track.currentData()

    def _kind_changed(self):
        kind = self.kind.currentData()
        spiel = kind == "spiel"
        if spiel:
            self._load_game_tracks()
        entry = self.selected_game_track()
        own_file = not spiel or entry is None
        for widget in (self.track, self.track_label, self.take_logic, self.take_sides, self.take_scenery):
            widget.setVisible(spiel)
        for widget in (self.path, self.path_label, self.browse_button):
            widget.setVisible(own_file)
        for widget in (self.code, self.code_label):
            widget.setVisible(spiel and entry is None)
        self.spacing.setVisible(kind != "gpx")
        self.spacing_label.setVisible(kind != "gpx")
        if kind == "spiel":
            texte = [tr("Imports centre line, heights and widths, optionally also pit lane, zones, starting grid, side "
                        "areas and scenery. If the track does not fit into the game world, it is rotated first.")]
            if entry is not None and entry.get("bruecke"):
                texte.append(tr("This track crosses itself on a bridge; the generator has no bridges, the check "
                                "reports the crossing as an error."))
            if entry is None:
                texte.append(tr("Game folder "
                                "…/2025_asset_groups/environment_package/tracks/<code>/wep/<code>_common.erp or the "
                                "ERP or package of a mod. Scenery only works with an ERP."))
                if self.game_tracks_error:
                    texte.append(tr("Game tracks not found: {fehler}", fehler=self.game_tracks_error))
            texte.append(tr("For your own use only: packages with the layout of an original track contain parts "
                            "derived from game data and should not be shared."))
            self.hint.setText("\n".join(texte))
        else:
            self.hint.setText({
                "tum": tr(("TUM database (LGPL-3.0): centreline and widths, without heights. Axes: z = −y, right/left "
                           "widths are kept.")),
                "gpx": tr("Circuit; the track is resampled at 5 m; width 14 m throughout; heights from <ele>."),
            }[kind])

    def _path_changed(self, text):
        if self.kind.currentData() == "spiel" and text and not self.code.text().strip():
            self.code.setText(guess_code(text))

    def browse(self):
        filter_ = tr(next(f for key, _, f in KINDS if key == self.kind.currentData()))
        filename, _ = QFileDialog.getOpenFileName(self, tr("Choose template"), filter=filter_)
        if filename:
            if self.kind.currentData() == "spiel":
                self.code.clear()
            self.path.setText(filename)

    def values(self):
        entry = self.selected_game_track()
        spiel = self.kind.currentData() == "spiel"
        path = entry["datei"] if entry else self.path.text().strip()
        code = entry["code"] if entry else (self.code.text().strip() or None)
        return {"kind": self.kind.currentData(), "path": path, "code": code, "fit": self.fit.isChecked(),
                "spacing": self.spacing.value() or None,
                "rennlogik": spiel and self.take_logic.isChecked(), "seitenraum": spiel and self.take_sides.isChecked(),
                "kulisse": spiel and self.take_scenery.isChecked()}


class SuggestionDialog(QDialog):
    """Seed, Länge, Kurvenzahl und Mindestradius für den Zufallsvorschlag."""

    def __init__(self, parent=None, seed=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Generate suggestion"))
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("A random, valid track as a starting point. The same seed and values give the same "
                                   "track; a new seed gives a different one.")))
        form = QFormLayout()
        row = QHBoxLayout()
        self.seed = QSpinBox()
        self.seed.setRange(0, 999999)
        self.seed.setValue(random.randint(1, 999999) if seed is None else seed)
        self.seed.setAccessibleName(tr("Seed"))
        self.dice = QPushButton(tr("Roll dice"))
        self.dice.clicked.connect(lambda: self.seed.setValue(random.randint(1, 999999)))
        row.addWidget(self.seed, 1)
        row.addWidget(self.dice)
        form.addRow(tr("Seed"), row)
        self.length = QDoubleSpinBox()
        self.length.setRange(4000, 6000)
        self.length.setSingleStep(100)
        self.length.setDecimals(0)
        self.length.setValue(5000)
        self.length.setSuffix(" m")
        self.length.setAccessibleName(tr("Track length"))
        form.addRow(tr("Length"), self.length)
        self.curves = QSpinBox()
        self.curves.setRange(6, 30)
        self.curves.setValue(14)
        self.curves.setAccessibleName(tr("Number of corners"))
        self.curves.setToolTip(tr(("Guide value; the actually counted number of corners appears in the status bar "
                                   "after generating.")))
        form.addRow(tr("Number of corners (guide value)"), self.curves)
        self.radius = QDoubleSpinBox()
        self.radius.setRange(10, 100)
        self.radius.setDecimals(0)
        self.radius.setValue(30)
        self.radius.setSuffix(" m")
        self.radius.setAccessibleName(tr("Minimum radius"))
        self.radius.setToolTip(tr("Smallest corner radius of the centreline; original tracks from 7.5 m (Bahrain)."))
        form.addRow(tr("Minimum radius"), self.radius)
        layout.addLayout(form)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText(tr("Generate"))
        self.buttons.button(QDialogButtonBox.Cancel).setText(tr("Cancel"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def values(self):
        return {"seed": self.seed.value(), "length_m": self.length.value(), "curves": self.curves.value(),
                "min_radius_m": self.radius.value()}
