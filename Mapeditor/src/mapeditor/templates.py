"""Dialoge „Neu aus Vorlage“ und „Vorschlag erzeugen“ (Schritt F). Die Berechnung liegt im Werkzeugkasten (adapter)."""

from __future__ import annotations

import random
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout,
)

from .i18n import N_, tr

KINDS = (
    ("tum", N_("TUM-Rennstrecke (CSV: x_m, y_m, w_tr_right_m, w_tr_left_m)"), N_("TUM-CSV (*.csv);;Alle Dateien (*)")),
    ("gpx", N_("GPS-Spur (GPX, Rundkurs)"), N_("GPX (*.gpx);;Alle Dateien (*)")),
    ("spiel", N_("Spielstrecke (ERP aus dem Spielordner, nur lesend)"), N_("ERP (*.erp);;Alle Dateien (*)")),
)


def guess_code(path):
    """``…/monza_common.erp`` → ``monza``; Mod-ERP/ZIP: Dateiname ohne Endung."""
    name = Path(path).stem
    return name[: -len("_common")] if name.endswith("_common") else name


class TemplateDialog(QDialog):
    """Quelle, Datei, ggf. Streckencode und Einpassen wählen. ``values()`` liefert die Eingaben für den Adapter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Neu aus Vorlage"))
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("Die Form einer vorhandenen Strecke wird als neuer, ungespeicherter Entwurf geöffnet.\n"
                                "Dein aktuelles Projekt bleibt bis zum Speichern unberührt; Quelldateien werden nur gelesen.")))
        form = QFormLayout()
        self.kind = QComboBox()
        self.kind.setAccessibleName(tr("Quelle der Vorlage"))
        for key, title, _ in KINDS:
            title = tr(title)
            self.kind.addItem(title, key)
        form.addRow(tr("Quelle"), self.kind)
        row = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setAccessibleName(tr("Pfad der Vorlagendatei"))
        self.browse_button = QPushButton(tr("Durchsuchen …"))
        row.addWidget(self.path, 1)
        row.addWidget(self.browse_button)
        form.addRow(tr("Datei"), row)
        self.code = QLineEdit()
        self.code.setAccessibleName(tr("Streckencode der Spielstrecke"))
        self.code.setPlaceholderText(tr("z. B. monza, silverstone, spa_francorchamps"))
        self.code_label = QLabel(tr("Streckencode"))
        form.addRow(self.code_label, self.code)
        self.spacing = QDoubleSpinBox()
        self.spacing.setRange(0, 200)
        self.spacing.setDecimals(0)
        self.spacing.setSuffix(" m")
        self.spacing.setSpecialValueText(tr("alle Punkte"))
        self.spacing.setToolTip(tr("Punktabstand der Kontrollpunkte. Weniger Punkte lassen sich leichter bearbeiten; "
                                "die glättende Approximation (6 m) gleicht Lücken aus."))
        self.spacing_label = QLabel(tr("Punktabstand"))
        form.addRow(self.spacing_label, self.spacing)
        self.fit = QCheckBox(tr("Verkleinern, falls die Strecke nicht in X/Z ±1024 m passt"))
        self.fit.setChecked(False)
        form.addRow("", self.fit)
        layout.addLayout(form)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText(tr("Importieren"))
        self.buttons.button(QDialogButtonBox.Cancel).setText(tr("Abbrechen"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.browse_button.clicked.connect(self.browse)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.path.textChanged.connect(self._path_changed)
        self._kind_changed()

    def _kind_changed(self):
        spiel = self.kind.currentData() == "spiel"
        for widget in (self.code, self.code_label):
            widget.setVisible(spiel)
        self.spacing.setVisible(self.kind.currentData() != "gpx")
        self.spacing_label.setVisible(self.kind.currentData() != "gpx")
        self.hint.setText({
            "tum": tr("TUM-Datenbank (LGPL-3.0): Mittellinie und Breiten, ohne Höhen. Achsen: z = −y, Breiten rechts/links bleiben."),
            "gpx": tr("Rundkurs, Spur wird auf 5 m neu abgetastet; Breite pauschal 14 m; Höhen aus <ele>."),
            "spiel": tr("Spielordner …/2025_asset_groups/environment_package/tracks/<code>/wep/<code>_common.erp. "
                     "Übernommen werden Mittellinie, Höhen und Breiten – keine Boxengasse, kein Gelände."),
        }[self.kind.currentData()])

    def _path_changed(self, text):
        if self.kind.currentData() == "spiel" and text and not self.code.text().strip():
            self.code.setText(guess_code(text))

    def browse(self):
        filter_ = tr(next(f for key, _, f in KINDS if key == self.kind.currentData()))
        filename, _ = QFileDialog.getOpenFileName(self, tr("Vorlage wählen"), filter=filter_)
        if filename:
            if self.kind.currentData() == "spiel":
                self.code.clear()
            self.path.setText(filename)

    def values(self):
        return {"kind": self.kind.currentData(), "path": self.path.text().strip(),
                "code": self.code.text().strip() or None, "fit": self.fit.isChecked(),
                "spacing": self.spacing.value() or None}


class SuggestionDialog(QDialog):
    """Seed, Länge, Kurvenzahl und Mindestradius für den Zufallsvorschlag."""

    def __init__(self, parent=None, seed=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Vorschlag erzeugen"))
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("Eine zufällige, gültige Strecke als Ausgangspunkt. Gleicher Seed und gleiche Werte ergeben "
                                "dieselbe Strecke; ein neuer Seed eine andere.")))
        form = QFormLayout()
        row = QHBoxLayout()
        self.seed = QSpinBox()
        self.seed.setRange(0, 999999)
        self.seed.setValue(random.randint(1, 999999) if seed is None else seed)
        self.seed.setAccessibleName(tr("Seed"))
        self.dice = QPushButton(tr("Würfeln"))
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
        self.length.setAccessibleName(tr("Streckenlänge"))
        form.addRow(tr("Länge"), self.length)
        self.curves = QSpinBox()
        self.curves.setRange(6, 30)
        self.curves.setValue(14)
        self.curves.setAccessibleName(tr("Kurvenzahl"))
        self.curves.setToolTip(tr("Richtwert; die tatsächlich gezählte Kurvenzahl steht nach dem Erzeugen in der Statuszeile."))
        form.addRow(tr("Kurvenzahl (Richtwert)"), self.curves)
        self.radius = QDoubleSpinBox()
        self.radius.setRange(10, 100)
        self.radius.setDecimals(0)
        self.radius.setValue(30)
        self.radius.setSuffix(" m")
        self.radius.setAccessibleName(tr("Mindestradius"))
        self.radius.setToolTip(tr("Kleinster Kurvenradius der Mittellinie; Originalstrecken ab 7,5 m (Bahrain)."))
        form.addRow(tr("Mindestradius"), self.radius)
        layout.addLayout(form)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText(tr("Erzeugen"))
        self.buttons.button(QDialogButtonBox.Cancel).setText(tr("Abbrechen"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def values(self):
        return {"seed": self.seed.value(), "length_m": self.length.value(), "curves": self.curves.value(),
                "min_radius_m": self.radius.value()}
