"""Ein- und Ausblenden aller Funktionen: Kartenebenen, Bereiche, Reiter, Automatiken (Menü Ansicht, Dialog).

Jede Funktion hat einen Schlüssel, eine Gruppe, eine Beschriftung, einen Tooltip und einen Standard. Der Zustand wird
in den Benutzereinstellungen gespeichert (``QSettings``: ``ansicht/<schlüssel>``) und beim Start wiederhergestellt.
"""

from PySide6.QtCore import QSettings, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QGridLayout, QGroupBox, QLabel, QPushButton,
                               QVBoxLayout)

from .i18n import N_, SPRACHEN, STANDARD, tr

#: (Schlüssel, Gruppe, Beschriftung, Tooltip, Standard)
FEATURES = [
    ("raster", N_("Map layers"), N_("Grid"), N_("Guide lines; spacing see scale bottom right"), True),
    ("raender", N_("Map layers"), N_("Track edges"), N_("Computed kerbs, grass and gravel strips"), True),
    ("richtung", N_("Map layers"), N_("Driving direction"), N_("Arrows in driving direction"), True),
    ("spielbereich", N_("Map layers"), N_("Play area ±1024 m"), N_(("The track must lie completely inside this "
                                                                         "frame")), True),
    ("zonen", N_("Map layers"), N_("DRS / Straight Mode"), N_("Zones on the track"), True),
    ("kameras", N_("Map layers"), N_("Cameras (replays)"),
     N_("Trigger zones across the track and the carrier's replay cameras, as the build moves them to this track; "
        "settings under Properties → Cameras"), False),
    ("ideallinie", N_("Map layers"), N_("Racing line · braking points · boards"),
     N_("Racing line from the racing-line run: red braking, yellow coasting, green full throttle; red triangles = "
        "braking point, white squares = distance boards"), True),
    ("rennbetrieb", N_("Map layers"), N_("Pit · grid slots · sectors"),
     N_("Pit lane (orange), pit boxes, starting grid and sector boundaries from the racing-line run"), True),
    ("kurvennummern", N_("Map layers"), N_("Corner numbers"), N_(("T1 … as in the game; click a number to show the "
                                                                   "corner")), True),
    ("umgebung", N_("Map layers"), N_("Environment"),
     N_(("Trees, bushes, buildings … as circles at catalogue size (colour per category); edit with the “Environment” "
         "tool (U)")), True),
    ("eingabepunkte", N_("Map layers"), N_("Input points"), N_("Control points or segment ends as handles"), True),
    ("beschriftungen", N_("Map layers"), N_("Zone labels"), N_(("Names of the DRS/Straight Mode zones and "
                                                                          "of the pit lane")), True),
    ("massstab", N_("Map layers"), N_("Scale"), N_("Scale bottom right"), True),
    ("kartenhinweise", N_("Map layers"), N_("Hint labels"), N_(("State at the top and usage hint at the bottom "
                                                                      "of the map")), True),
    ("seitenleiste", N_("Areas"), N_("Side panel (display, track)"), N_(("Left panel with layers and object "
                                                                                "list")), True),
    ("eigenschaften", N_("Areas"), N_("Properties"), N_("Right panel with settings"), True),
    ("unterer_bereich", N_("Areas"), N_("Profiles and analysis (bottom)"), N_("Tabs below the map"), True),
    ("querneigungsfeld", N_("Areas"), N_("Banking field"), N_("“Banking” field in the properties"), True),
    ("werkzeugleiste", N_("Areas"), N_("Toolbar"), N_("Buttons at the top"), True),
    ("kopfzeile", N_("Areas"), N_("Header (project)"), N_("Project name, save state and folder"), True),
    ("statuszeile", N_("Areas"), N_("Status bar"), N_("State, checksum and tool at the bottom"), True),
    ("reiter_hoehe", N_("Tabs"), N_("Height profile"), "", True),
    ("reiter_neigung", N_("Tabs"), N_("Banking"), "", True),
    ("reiter_querschnitt", N_("Tabs"), N_("Cross-section"), "", True),
    ("reiter_geschwindigkeit", N_("Tabs"), N_("Speed"), "", True),
    ("reiter_pruefung", N_("Tabs"), N_("Checks"), "", True),
    ("reiter_auswertung", N_("Tabs"), N_("Analysis"), "", True),
    ("reiter_umgebung", N_("Tabs"), N_("Environment"), N_(("Catalogue, planting and properties of the environment "
                                                          "objects")), True),
    ("fahrlinie_auto", N_("Automation"), N_("Calculate racing line automatically"),
     N_("After every change the generator calculates racing line, corners, braking points, boards and the check in the "
        "background (10–50 s depending on the track). Off: only with “Check” (Ctrl+Enter)."), True),
    ("autosicherung", N_("Automation"), N_("Autosave"),
     N_("Save unsaved changes every minute to a recovery file in the project folder"), True),
    ("letztes_projekt", N_("Automation"), N_("Open last project on start"), N_("Without an argument the editor "
                                                                                     "starts with the last opened "
                                                                                     "project"), False),
    ("startbildschirm", N_("Automation"), N_("Start screen for an empty project"), N_(("Choice of "
                                                                                       "Draw/Template/Suggestion")), True),
]
GROUPS = [N_("Map layers"), N_("Areas"), N_("Tabs"), N_("Automation")]
DEFAULTS = {key: default for key, _, _, _, default in FEATURES}
LABELS = {key: label for key, _, label, _, _ in FEATURES}
TIPS = {key: tip for key, _, _, tip, _ in FEATURES}


def settings():
    return QSettings()


def load_state():
    """Gespeicherter Zustand je Funktion (Standard, wenn nichts gespeichert ist)."""
    store = settings()
    state = {}
    for key, default in DEFAULTS.items():
        value = store.value(f"ansicht/{key}", default)
        state[key] = value if isinstance(value, bool) else str(value).lower() in ("true", "1", "ja")
    return state


def save_state(key, value):
    settings().setValue(f"ansicht/{key}", bool(value))


def load_language():
    """Gespeicherte Oberflächensprache (``en``/``de``), Standard Englisch (English First, 4.11.2026)."""
    value = str(settings().value("sprache", STANDARD) or STANDARD).lower()
    return value if value in SPRACHEN else STANDARD


def save_language(code):
    if code not in SPRACHEN:
        raise ValueError(code)
    settings().setValue("sprache", code)


def recent_projects():
    value = settings().value("zuletzt", [])
    if isinstance(value, str):
        value = [value]
    return [str(v) for v in (value or []) if v]


def remember_project(path, limit=8):
    if not path:
        return
    path = str(path)
    items = [p for p in recent_projects() if p != path]
    settings().setValue("zuletzt", [path] + items[:limit - 1])


class ViewDialog(QDialog):
    """Alle Funktionen mit Häkchen, nach Gruppen; Änderungen wirken sofort."""
    changed = Signal(str, bool)

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Customise view"))
        layout = QVBoxLayout(self)
        intro = QLabel(tr(("What the editor shows and does automatically. Changes take effect immediately and are "
                           "remembered.")))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.boxes = {}
        grid = QGridLayout()
        for column, group in enumerate(GROUPS):
            box = QGroupBox(tr(group))
            inner = QVBoxLayout(box)
            for key, g, label, tip, _ in FEATURES:
                if g != group:
                    continue
                check = QCheckBox(tr(label))
                check.setToolTip(tr(tip) if tip else "")
                check.setChecked(state.get(key, True))
                check.toggled.connect(lambda on, key=key: self.changed.emit(key, on))
                inner.addWidget(check)
                self.boxes[key] = check
            inner.addStretch(1)
            grid.addWidget(box, 0, column)
        layout.addLayout(grid)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Close).setText(tr("Close"))
        reset = QPushButton(tr("Show everything"))
        reset.clicked.connect(self.show_all)
        buttons.addButton(reset, QDialogButtonBox.ResetRole)
        layout.addWidget(buttons)

    def show_all(self):
        for key, check in self.boxes.items():
            check.setChecked(DEFAULTS[key] or key not in ("letztes_projekt",))

    def sync(self, key, on):
        check = self.boxes.get(key)
        if check is not None and check.isChecked() != on:
            check.blockSignals(True)
            check.setChecked(on)
            check.blockSignals(False)
