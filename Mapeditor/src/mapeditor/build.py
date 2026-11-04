"""Bauen/Paket und dauerhaftes Spieltest-Protokoll; keine Generatorlogik."""

import copy
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid
import zipfile

from PySide6.QtCore import QElapsedTimer, QEvent, Qt, QTimer, QUrl
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QFrame, QGridLayout, QScrollArea, QSizePolicy, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .adapter import (BUILD_PVS, TRACK_CODES, BuildJob, ProcessJob, build_command, swofty_auswerten,
                      swofty_installieren, swofty_installierte, swofty_programm, swofty_traeger_ids, swofty_traeger_plan, swofty_starten, check_key, default_carrier,
                      game_status_command, injector_command, parse_game_status, toolkit_path, worker_command)
from .i18n import N_, meldung, tr, trn
from .paths import writable_path
from .ux import zahl

#: Werte im Spieltest-Protokoll (gespeichert deutsch, angezeigt übersetzt)
PLAYTEST_STATES = [N_("offen"), N_("ok"), N_("Absturz"), N_("Problem")]
BUILD_STATES = [N_("läuft"), N_("abgebrochen"), N_("fehlgeschlagen"), N_("mit_traegerabweichung")]

#: Bau erfolgreich (Paket geprüft)
GOOD_BUILDS = {"ok", "mit_traegerabweichung"}

#: Zustände der Statuskarte: Kennung → (Text, Farbe, Symbol)
BUILD_STATES_UI = {"ready": (N_("Ready"), "#5BD08A", "●"), "checking": (N_("Checking"), "#D6A94A", "◐"),
                   "building": (N_("Building"), "#5AA9E6", "◐"), "working": (N_("Working"), "#5AA9E6", "◐"),
                   "done": (N_("Done"), "#5BD08A", "✔"), "error": (N_("Failed"), "#F07878", "✖"),
                   "cancelled": (N_("Cancelled"), "#9AA3AD", "○")}
#: Meldungsstufen: Rang (Fehler zuerst), Farbe, Symbol
ISSUE_LEVELS = {"Fehler": (0, "#F07878", "✖"), "Warnung": (1, "#D6A94A", "▲"), "Hinweis": (2, "#9AA3AD", "ⓘ")}


def code_label(code, rating, state=None):
    """Eintrag der Codeauswahl: „sochi · empfohlen · im Spiel“."""
    text = f"{code}  ·  {tr(rating)}"
    if state and code in (state.get("installiert") or {}):
        text += "  ·  " + tr("in game")
    return text


def code_status(code, state):
    """Was „Ins Spiel übertragen“ für diesen Code tun kann (aus ``injektor_tool.py datenbank --json``)."""
    if state is None:
        return tr("State in the game unknown (toolkit or game folder not readable).")
    inst = (state.get("installiert") or {}).get(code)
    if not inst:
        return tr("{code} is not in the game yet: install the package once with the Track Installer and activate it in "
                  "the EGO editor (“Activate mod”). After that the map editor transfers every new version itself – "
                  "even with the mod deactivated.", code=code)
    where = tr("{code} is installed ({slot}, last {paket}).", code=code, slot=inst.get("slot") or "?",
               paket=inst.get("paket") or "?")
    if code in (state.get("aktiv") or []):
        db = tr("Database active · “Transfer to game” replaces the track directly.")
    elif state.get("zustand") == "aktiv" and not state.get("aktiv"):
        db = tr("Database activated · “Transfer to game” replaces the track directly.")
    elif state.get("zustand") == "aktiv":
        db = tr("{code} is not enabled in the active database (EGO editor: Database activation).", code=code)
    elif code in (state.get("gemerkt") or []):
        db = tr("Mod deactivated · the transfer re-activates the database itself (remembered activation).")
    else:
        db = tr("Database not activated and no activation remembered yet: click “Activate mod” in the EGO editor once "
                "– after that it works without the EGO editor.")
    note = tr(" The game is running – quit it before transferring.") if state.get("spiel_laeuft") else ""
    return f"{where} {db}{note}"


def nearest_existing(path):
    path = Path(path)
    while not path.exists():
        if path.parent == path:
            raise ValueError(tr("Drive or folder not reachable: {path}", path=path))
        path = path.parent
    return path


def space_needs(work, packages):
    """[(Ordner, benötigte Bytes)]: Arbeitsordner ≈ 1,1 GB, Paket ≈ 2,9 GB (gleicher Datenträger: zusammen)."""
    a, b = nearest_existing(work), nearest_existing(packages)
    same = a.stat().st_dev == b.stat().st_dev
    return [(a, 4_000_000_000)] if same else [(a, 1_100_000_000), (b, 2_900_000_000)]


def check_space(work, packages):
    for path, required in space_needs(work, packages):
        free = shutil.disk_usage(path).free
        if free < required:
            raise ValueError(tr("Not enough free space on {anchor}: {free:.1f} GB free, {required:.1f} GB needed.", anchor=path.anchor, free=free / 1000000000.0, required=required / 1000000000.0))


def space_text(work, packages):
    """„Bedarf etwa 4,0 GB · frei 130 GB auf G:“ (je Datenträger)."""
    try:
        parts = []
        for path, required in space_needs(work, packages):
            free = shutil.disk_usage(path).free
            drive = path.anchor.rstrip("\\/") or str(path)
            parts.append(tr("Needs about {required} · free {free} on {drive}", required=zahl(required / 1000000000.0, 1, 'GB'), free=zahl(free / 1000000000.0, 0 if free >= 10000000000.0 else 1, 'GB'), drive=drive) + (tr(" – not enough!") if free < required else ""))
        return " · ".join(parts)
    except (OSError, ValueError) as error:
        return tr("Storage cannot be checked: {error}", error=error)


def build_folders(project, output=None, packages=None):
    """Arbeits- und Paketordner: Dialogfelder, sonst Projekteinstellung, sonst <Projekt>/bauten und /pakete."""
    settings = project.manifest.get("bauen", {})
    base = project.folder or (Path.home() / "Documents" / "Mapeditor")
    return (writable_path(output or settings.get("ausgabe", str(base / "bauten"))),
            writable_path(packages or settings.get("pakete", str(base / "pakete"))))


NAME_NUMBER = re.compile(r"^([A-Za-z][A-Za-z_-]*?)(\d{1,4})(?:_.*)?$")


def _taken(name, output, packages):
    return (Path(output) / name).exists() or (Path(packages) / (name + ".zip")).exists()


def next_build_name(project, output, packages):
    """Fortlaufender Bauname: letzter nummerierter Bau des Projekts + 1 (``testedit10_tafeln50`` → ``testedit11``),
    sonst Projektordnername + 1. Zeitstempel-Namen (``bau_20261025_…``) zählen nicht."""
    known = [entry.get("name", "") for entry in project.playtests]
    for folder, pattern in ((Path(output), "*"), (Path(packages), "*.zip")):
        try:
            entries = sorted(folder.glob(pattern), key=lambda p: p.stat().st_mtime)
        except OSError:
            entries = []
        known += [p.stem if p.suffix == ".zip" else p.name for p in entries if p.is_dir() or p.suffix == ".zip"]
    prefix, number = None, 0
    for name in known:                                  # jüngster nummerierter Name bestimmt den Präfix
        match = NAME_NUMBER.match(name)
        if match:
            prefix = match.group(1)
    if prefix is None:
        stem = re.sub(r"[^A-Za-z0-9_-]", "", (project.folder.name if project.folder else "strecke").lower()) or "strecke"
        prefix = stem if stem[0].isalpha() else "s" + stem
    for name in known:
        match = NAME_NUMBER.match(name)
        if match and match.group(1) == prefix:
            number = max(number, int(match.group(2)))
    number += 1
    while _taken(f"{prefix}{number}", output, packages):
        number += 1
    return f"{prefix}{number}"


def backup_folder(output):
    """Sicherungen des Schnellaustauschs neben dem Arbeitsordner (z. B. TestEdit/injektion_sicherung)."""
    return writable_path(Path(output).parent / "injektion_sicherung")


def dir_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


class ElidedLabel(QLabel):
    """Einzeiler, der in der Mitte kürzt, statt das Layout zu verbreitern (voller Text als Tooltip)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.full = ""

    def set_full(self, text):
        self.full = text
        self.setToolTip(text)
        self.update()

    def minimumSizeHint(self):
        return super().minimumSizeHint().boundedTo(self.fontMetrics().boundingRect("x").size() * 4)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().placeholderText().color())
        painter.drawText(self.rect(), Qt.AlignVCenter | Qt.AlignLeft,
                         self.fontMetrics().elidedText(self.full, Qt.ElideMiddle, self.width()))


class BuildDialog(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.project = main.document.project
        self.job = None
        self.record = None
        self.result = None
        self.command = None
        self.messages = []
        self.setWindowTitle(tr("Build / package"))
        self.setWindowModality(Qt.NonModal)     # Weiterarbeiten während des Baus (der Bau arbeitet auf einem Schnappschuss)
        self.background_hidden = False           # während eines Baus ausgeblendet: das Fenster bleibt erhalten
        self.state = "ready"
        self.valid = True
        self.path_problem = False
        self.steps_log = []
        self.clock = QElapsedTimer()
        self.tick = QTimer(self)
        self.tick.setInterval(1000)
        self.tick.timeout.connect(self._show_elapsed)
        self.setStyleSheet('QPushButton[primary="true"] {background: #2F7DD1; color: white; font-weight: 600; '
                           'padding: 5px 16px; border-radius: 4px;} '
                           'QPushButton[primary="true"]:disabled {background: #55657a; color: #cfd6de;}')
        self.resize(900, 520)
        outer = QVBoxLayout(self)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.content = QWidget()
        self.scroll.setWidget(self.content)
        outer.addWidget(self.scroll, 1)
        layout = QVBoxLayout(self.content)
        layout.setContentsMargins(0, 0, 6, 0)
        self._fit_pending = False
        self.content.installEventFilter(self)          # Höhe folgt dem Inhalt, begrenzt auf den Bildschirm
        title = QLabel(tr("BUILD / PACKAGE   ·   recommended profile"))
        layout.addWidget(title)
        self.profile_hint = hint = QLabel()
        hint.setWordWrap(True)
        layout.addWidget(hint)
        settings = self.project.manifest.get("bauen", {})
        base = self.project.folder or (Path.home() / "Documents" / "Mapeditor")
        self.fields = QWidget()
        fields_layout = QVBoxLayout(self.fields)
        fields_layout.setContentsMargins(0, 0, 0, 0)
        form = self.main_form = QFormLayout()
        fields_layout.addLayout(form)
        self.output = QLineEdit(settings.get("ausgabe", str(base / "bauten")))
        self.packages = QLineEdit(settings.get("pakete", str(base / "pakete")))
        try:
            suggestion = next_build_name(self.project, *build_folders(self.project, self.output.text(), self.packages.text()))
        except (OSError, ValueError):
            suggestion = "bau_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        self.name = QLineEdit(suggestion)
        self.name.setToolTip(tr("Suggested consecutively (last build + 1); append a description with an underscore, "
                                "e.g. “_tafeln50”."))
        form.addRow(tr("Build name"), self.name)
        self.note = QLineEdit()
        self.note.setPlaceholderText(tr("What's new? (e.g. “Turn 11 apex, 50 m boards”) – appears in the playtest log"))
        form.addRow(tr("What's new?"), self.note)
        try:
            root = toolkit_path(self.project.manifest.get("werkzeugkasten"))
            carrier = str(default_carrier(root))
        except ValueError:
            carrier = ""
        self.carrier = QLineEdit(settings.get("traeger", carrier))
        # Ordner und Träger ändert man selten: eingeklappt mit Einzeiler, klappen bei einem Fehler von selbst auf
        self.paths_box = QWidget()
        paths_form = QFormLayout(self.paths_box)
        paths_form.setContentsMargins(0, 0, 0, 0)
        for label, field, file in ((tr("Working folder"), self.output, False), (tr("Package folder"), self.packages, False),
                                   (tr("Carrier package (ZIP)"), self.carrier, True)):
            row = QHBoxLayout()
            row.addWidget(field)
            button = QPushButton(tr("Choose …"))
            button.clicked.connect(lambda checked=False, field=field, file=file: self.choose(field, file))
            row.addWidget(button)
            paths_form.addRow(label, row)
        self.paths_toggle = QPushButton(tr("Folders ▸"))
        self.paths_toggle.setCheckable(True)
        self.paths_toggle.setFlat(True)
        self.paths_toggle.setStyleSheet("text-align: left;")
        self.paths_summary = ElidedLabel()
        form.addRow(self.paths_toggle, self.paths_summary)
        form.addRow(self.paths_box)
        self.paths_box.hide()
        self.paths_toggle.toggled.connect(self._toggle_paths)
        options_grid = QGridLayout()                   # zwei Spalten: Ziel/Raster, Code/Zustand, Optionen
        options_grid.setColumnStretch(1, 1)
        options_grid.setColumnStretch(3, 1)
        fields_layout.addLayout(options_grid)
        from PySide6.QtWidgets import QCheckBox
        self.ki_profil = QCheckBox(tr("Adapt AI driving data to the track"))
        self.ki_profil.setChecked(bool(settings.get("ki_profil", True)))
        self.ki_profil.setToolTip(tr("Braking points of the driving assist and AI pace.") + " " + tr("Generates the AI time/speed curve (VPTimes) from the track's own profile instead "
                                     "of taking over Portimão's data. Without this option braking zones have no "
                                     "relation to the corners (25.10.2026). New, not yet confirmed in game."))
        self.race_data = QCheckBox(tr("Race data from the track"))
        self.race_data.setChecked(bool(settings.get("renndaten", True)))
        self.race_data.setToolTip(tr("Laps, fuel, tyres, AI, pit lane.") + " " + tr("Race distance and fuel follow the rules of Portimão mod 1.0.2, AI times come "
                                     "from the estimated lap time, plus the pit lane speed limit and the time a pit "
                                     "stop costs, and the minimum lap time for leaderboards. The tyre choice is an "
                                     "estimate."))
        self.target = QComboBox()
        self.target.addItem(tr("Track Installer (EGO Editor)"), "ego")
        self.target.addItem(tr("Swofty Launcher"), "swofty")
        self.target.setCurrentIndex(max(0, self.target.findData(settings.get("ziel", "ego"))))
        self.target.setToolTip(tr("Swofty Launcher: an addon with its own track code and its own name in the menus, "
                                  "for as many tracks as you like. Starts only from the launcher, offline and without "
                                  "EA AntiCheat."))
        options_grid.addWidget(QLabel(tr("Build for")), 0, 0)
        options_grid.addWidget(self.target, 0, 1)
        self.shared_carrier = QCheckBox(tr("Use a shared carrier (0.45 instead of 3 GB per track)"))
        self.shared_carrier.setChecked(bool(settings.get("traeger_teilen", True)))
        self.shared_carrier.setToolTip(tr("The Portimão mod's textures sit once in a carrier addon next to the "
                                          "packages, and every track only points to it. The launcher needs the carrier "
                                          "addon as well."))
        options_grid.addWidget(self.shared_carrier, 2, 0, 1, 4)
        self.grid_size = QComboBox()
        self.grid_size.addItem(tr("24 cars (default)"), 0)
        self.grid_size.addItem(tr("26 cars"), 26)
        self.grid_size.setCurrentIndex(max(0, self.grid_size.findData(settings.get("raster", 0))))
        self.grid_size.setToolTip(tr("Adds grid slots, garages and pit boxes for 26 cars. For 26 cars to actually "
                                     "race, the game also needs an addon such as Custom Grid Size."))
        options_grid.addWidget(QLabel(tr("Grid")), 0, 2)
        options_grid.addWidget(self.grid_size, 0, 3)
        # Streckencode: alle Codes, die die Spieldatenbank kennt; Zustand im Spiel darunter
        self.code = QComboBox()
        self.code.setMaxVisibleItems(len(TRACK_CODES))
        self.code.setToolTip(tr("Game code the track is installed under. Each code is a separate slot in the game: "
                                "several custom tracks side by side need different codes (docs/streckencode.md)."))
        self.game_state = None
        self.status_read = False
        self.status_job = None
        self._fill_codes()
        self.code.setCurrentIndex(max(0, self.code.findData(settings.get("streckencode", "sochi"))))
        self.code_label = QLabel(tr("Track code"))
        options_grid.addWidget(self.code_label, 1, 0)
        options_grid.addWidget(self.code, 1, 1)
        self.code_info = QLabel()
        self.code_info.setWordWrap(True)
        self.code_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        options_grid.addWidget(self.code_info, 1, 2, 1, 2)
        options_grid.addWidget(self.ki_profil, 3, 0, 1, 2)
        options_grid.addWidget(self.race_data, 3, 2, 1, 2)
        self.code.currentIndexChanged.connect(self._update_code_info)
        self.target.currentIndexChanged.connect(self._update_code_info)
        # Erweiterte Bauoptionen (überschreiben das Profil); eingeklappt
        self.advanced_toggle = QPushButton(tr("Advanced build options ▸"))
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setFlat(True)
        self.advanced_toggle.setStyleSheet("text-align: left;")
        self.advanced = QWidget()
        advanced = self.advanced_form = QFormLayout(self.advanced)
        advanced.setContentsMargins(0, 0, 0, 0)
        self.track_name = QLineEdit(settings.get("streckenname", ""))
        self.track_name.setPlaceholderText(tr("from the definition: {name}", name=self.project.raw.get('name') or tr("Test track")))
        self.pvs = QComboBox()
        self.pvs.addItem(tr("along the track (recommended)"), "strecke")
        self.pvs.addItem(tr("Original cells see everything"), "alles")
        self.pvs.setCurrentIndex(max(0, self.pvs.findData(settings.get("pvs_variante", "strecke"))))
        self.world_maps = QCheckBox(tr("Own ground and surroundings maps"))
        self.world_maps.setToolTip(tr("Replaces Portimão's aerial photos: ground from the track surfaces, racing line "
                                      "on the asphalt, surroundings matching hills and mountains."))
        self.world_maps.setChecked(bool(settings.get("weltkarten", True)))
        self.biome = QComboBox()
        self.biome.addItem(tr("Grass (default)"), "standard")
        self.biome.addItem(tr("Desert"), "wueste")
        self.biome.setCurrentIndex(max(0, self.biome.findData(settings.get("biom", "standard"))))
        self.tint = QComboBox()
        self.tint.addItem(tr("Default"), "standard")
        self.tint.addItem(tr("Dry"), "trocken")
        self.tint.addItem(tr("Lush"), "saftig")
        self.tint.addItem(tr("Autumn"), "herbst")
        self.tint.setCurrentIndex(max(0, self.tint.findData(settings.get("farbton", "standard"))))
        self.world_maps.toggled.connect(self.biome.setEnabled)
        self.world_maps.toggled.connect(self.tint.setEnabled)
        self.biome.setEnabled(self.world_maps.isChecked())
        self.tint.setEnabled(self.world_maps.isChecked())
        self.track_name_label = QLabel(tr("Track name"))
        advanced.addRow(self.track_name_label, self.track_name)
        advanced.addRow(tr("Visibility (PVS)"), self.pvs)
        advanced.addRow("", self.world_maps)
        advanced.addRow(tr("Landscape"), self.biome)
        advanced.addRow(tr("Colour tone"), self.tint)
        fixed = QLabel(tr(("Fixed in the profile: pit lane correction, activation template portimao, main variant "
                           "only.")))
        fixed.setWordWrap(True)
        fixed.setEnabled(False)
        advanced.addRow("", fixed)
        self.advanced.setVisible(False)
        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        fields_layout.addWidget(self.advanced_toggle)
        fields_layout.addWidget(self.advanced)
        self.pvs.currentIndexChanged.connect(self._update_profile_hint)
        self.track_name.textChanged.connect(self._update_profile_hint)
        self._update_profile_hint()
        layout.addWidget(self.fields)
        self.space = QLabel()
        self.space.setWordWrap(True)
        layout.addWidget(self.space)
        for field in (self.output, self.packages):
            field.editingFinished.connect(self.update_space)
        self.update_space()
        # Statuskarte: Zustand, Phase, Fortschritt, Bauphasen und Ergebnis
        self.status_card = QFrame()
        self.status_card.setObjectName("statuskarte")
        card = QVBoxLayout(self.status_card)
        top = QHBoxLayout()
        self.state_badge = QLabel()
        top.addWidget(self.state_badge)
        self.phase = QLabel(tr("Ready to build."))
        self.phase.setWordWrap(True)
        top.addWidget(self.phase, 1)
        self.elapsed_label = QLabel()
        top.addWidget(self.elapsed_label)
        card.addLayout(top)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setFormat(tr("%p % · phase progress"))
        card.addWidget(self.progress)
        self.steps = QLabel()
        self.steps.setTextFormat(Qt.RichText)
        self.steps.setWordWrap(True)
        self.steps.hide()
        card.addWidget(self.steps)
        self.result_title = QLabel()
        self.result_title.setStyleSheet("font-size: 15px; font-weight: 700;")
        self.result_title.setWordWrap(True)
        self.result_title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.result_title.hide()
        card.addWidget(self.result_title)
        self.result_text = QPlainTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setMaximumHeight(110)
        self.result_text.setPlaceholderText(tr("After the build: package path, size, SHA-256 and check report."))
        self.result_text.hide()                          # erst sichtbar, wenn es ein Ergebnis gibt
        self.result_text.textChanged.connect(lambda: self.result_text.setVisible(bool(self.result_text.toPlainText())))
        card.addWidget(self.result_text)
        self.copy_button = QPushButton(tr("Copy SHA-256"))
        self.copy_button.hide()
        self.copy_button.clicked.connect(self.copy_hash)
        card.addWidget(self.copy_button, 0, Qt.AlignLeft)
        layout.addWidget(self.status_card)
        # Meldungen: erst sichtbar, wenn es welche gibt; Fehler zuerst, Zähler und Filter
        self.issue_box = QWidget()
        issue_layout = QVBoxLayout(self.issue_box)
        issue_layout.setContentsMargins(0, 0, 0, 0)
        issue_head = QHBoxLayout()
        self.issue_summary = QLabel()
        issue_head.addWidget(self.issue_summary, 1)
        self.issue_show = QComboBox()
        self.issue_show.addItem(tr("All"), "alle")
        self.issue_show.addItem(tr("Errors and warnings"), "probleme")
        self.issue_show.addItem(tr("Errors only"), "fehler")
        self.issue_show.currentIndexChanged.connect(self._apply_issue_filter)
        issue_head.addWidget(self.issue_show)
        issue_layout.addLayout(issue_head)
        self.issue_list = QListWidget()
        self.issue_list.setWordWrap(True)
        self.issue_list.setMinimumHeight(70)
        self.issue_list.setMaximumHeight(170)
        self.issue_list.itemClicked.connect(self.jump_message)
        issue_layout.addWidget(self.issue_list, 1)
        self.issue_box.hide()
        layout.addWidget(self.issue_box)
        self.field_error = QLabel()
        self.field_error.setWordWrap(True)
        self.field_error.setStyleSheet("color: #F07878;")
        self.field_error.hide()
        layout.addWidget(self.field_error)
        self.log_toggle = QPushButton(tr("Show technical log"))
        self.log_toggle.setCheckable(True)
        self.log_toggle.setFlat(True)
        top.addWidget(self.log_toggle)                   # neben dem Zustand statt in einer eigenen Zeile
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(3000)
        self.log.setMaximumHeight(150)
        self.log.hide()
        self.log_toggle.toggled.connect(self.log.setVisible)
        layout.addWidget(self.log)
        install = QLabel(tr("First installation of a track code: install the ZIP once with the Track Installer and "
                            "activate it in the EGO editor. After that, every new version with “Transfer to game” – "
                            "without the EGO editor, even with the mod deactivated (backs up the affected game files, "
                            "writes to the game after your confirmation; quit the game first)."))
        install.setWordWrap(True)
        self.install_hint = install
        layout.addWidget(install)
        buttons = QHBoxLayout()
        self.start_button = QPushButton(tr("Check and build"))
        self.start_button.clicked.connect(self.start_build)
        self.start_button.setShortcut("Ctrl+Return")
        self.cancel_button = QPushButton(tr("Cancel"))
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_build)
        self.explorer_button = QPushButton(tr("Show in Explorer"))
        self.explorer_button.setEnabled(False)
        self.explorer_button.clicked.connect(self.show_package)
        self.report_button = QPushButton(tr("Open check report"))
        self.report_button.setEnabled(False)
        self.report_button.clicked.connect(self.show_report)
        self.inject_button = QPushButton(tr("Transfer to game …"))
        self.inject_button.setEnabled(False)
        self.inject_button.clicked.connect(self.inject_start)
        self.install_button = QPushButton(tr("Install in launcher"))
        self.install_button.setEnabled(False)
        self.install_button.clicked.connect(lambda: self.launcher_start(False))
        self.play_button = QPushButton(tr("Install in launcher and start"))
        self.play_button.setEnabled(False)
        self.play_button.clicked.connect(lambda: self.launcher_start(True))
        self.diagnosis_button = QPushButton(tr("Read last session"))
        self.diagnosis_button.setToolTip(tr(("Shows the summary of the last session in Swofty Launcher, with crash "
                                             "data.")))
        self.diagnosis_button.clicked.connect(self.launcher_diagnosis)
        self.close_button = QPushButton(tr("Close"))
        self.close_button.setToolTip(tr("During a build the window hides and the build continues."))
        self.close_button.clicked.connect(self.close)
        for button in (self.explorer_button, self.report_button, self.diagnosis_button, self.install_button):
            buttons.addWidget(button)                  # Nebenaktionen links, die nächste Hauptaktion rechts
        buttons.addStretch(1)
        for button in (self.cancel_button, self.start_button, self.inject_button, self.play_button, self.close_button):
            buttons.addWidget(button)
        outer.addLayout(buttons)
        # Schnellaustausch: zuletzt erfolgreich gebautes Paket des Projekts (auch nach erneutem Öffnen des Dialogs)
        self.inject_record = None
        self.inject_job = None
        self.launcher_record = None
        self.launcher_job = None
        self.launcher_steps = []
        self.injector = injector_command
        for entry in reversed(self.project.playtests):
            if entry.get("bau_status") in GOOD_BUILDS and entry.get("paket") and Path(entry["paket"]).is_file():
                self.set_inject_target(entry)
                break
        self.status_command = game_status_command
        self.read_game = getattr(main, "interactive", False)     # Tests lesen den Spielordner nicht
        for field in (self.name, self.output, self.packages, self.carrier):
            field.textChanged.connect(self.validate)
        self.set_state("ready")
        self.validate()
        self._update_code_info()
        self.refresh_game_status()

    # ------------------------------------------------------------------ Größe
    def eventFilter(self, obj, event):
        if obj is self.content and event.type() == QEvent.LayoutRequest and not self._fit_pending:
            self._fit_pending = True
            QTimer.singleShot(0, self._fit_height)
        return super().eventFilter(obj, event)

    def showEvent(self, event):
        self.background_hidden = False
        super().showEvent(event)
        QTimer.singleShot(0, self._fit_height)

    def _fit_height(self):
        """Dialog so hoch wie der Inhalt, höchstens Bildschirmhöhe ohne Taskleiste; darüber scrollt der Inhalt."""
        self._fit_pending = False
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None or not self.isVisible():
            return
        available = screen.availableGeometry()
        layout = self.content.layout()
        width = self.scroll.viewport().width()
        needed = layout.heightForWidth(width) if layout.hasHeightForWidth() else layout.sizeHint().height()
        needed = max(needed, layout.minimumSize().height())
        if self.content.minimumHeight() != needed:
            self.content.setMinimumHeight(needed)
        frame = max(0, self.frameGeometry().height() - self.height())
        chrome = self.height() - self.scroll.height()
        height = min(needed + chrome + 2, available.height() - frame)
        if abs(height - self.height()) > 1:
            self.resize(self.width(), height)
            top = min(max(self.frameGeometry().top(), available.top()), available.bottom() - self.frameGeometry().height() + 1)
            if top != self.frameGeometry().top():
                self.move(self.frameGeometry().left(), top)

    # ------------------------------------------------------------------ Zustand, Phasen, Ergebnis
    def set_state(self, state):
        self.state = state
        self.progress.setVisible(state != "ready")          # im Leerlauf kein leerer Balken
        text, color, symbol = BUILD_STATES_UI[state]
        self.state_badge.setText(f"{symbol} {tr(text)}")
        self.state_badge.setStyleSheet(f"color: {color}; font-weight: 700; font-size: 14px;")
        self.status_card.setStyleSheet(f"QFrame#statuskarte {{border: 1px solid {color}; border-radius: 6px;}}")

    def _show_elapsed(self):
        if self.clock.isValid():
            seconds = self.clock.elapsed() // 1000
            self.elapsed_label.setText(f"{seconds // 60}:{seconds % 60:02d}")

    def _begin_run(self):
        self.steps_log = []
        self.clock.start()
        self.tick.start()
        self.steps.show()
        self.result_title.hide()
        self.copy_button.hide()
        self._step("vorab", tr("Pre-check"))
        self._show_elapsed()

    def _end_run(self):
        if not self.tick.isActive():
            return
        self.tick.stop()
        if self.steps_log and self.steps_log[-1][3] is None:
            self.steps_log[-1][3] = self.clock.elapsed()
        self._render_steps()
        self._show_elapsed()

    def _step(self, key, title):
        if not self.clock.isValid() or (self.steps_log and self.steps_log[-1][0] == key):
            return
        now = self.clock.elapsed()
        if self.steps_log and self.steps_log[-1][3] is None:
            self.steps_log[-1][3] = now
        self.steps_log.append([key, title, now, None])
        self._render_steps()

    def _render_steps(self):
        """Letzte abgeschlossene Phasen mit Dauer, dazu die laufende Phase."""
        from html import escape
        done = [s for s in self.steps_log if s[3] is not None]
        parts = []
        if len(done) > 5:
            parts.append(f"<span style='color:#9AA3AD'>… +{len(done) - 5}</span>")
        for _, title, start, end in done[-5:]:
            parts.append(f"<span style='color:#9AA3AD'>✔ {escape(title)} {(end - start) / 1000:.0f} s</span>")
        for _, title, _, end in self.steps_log:
            if end is None:
                parts.append(f"<b style='color:#5AA9E6'>▶ {escape(title)}</b>")
        self.steps.setText("  ›  ".join(parts))

    def copy_hash(self):
        packages = (self.result or {}).get("pakete") or []
        if packages and packages[0].get("sha256"):
            QApplication.clipboard().setText(packages[0]["sha256"])
            self.copy_button.setText(tr("Copied"))
            QTimer.singleShot(1500, lambda: self.copy_button.setText(tr("Copy SHA-256")))

    def _style_primary(self, button, on):
        if bool(button.property("primary")) != on:
            button.setProperty("primary", on)
            button.style().unpolish(button)
            button.style().polish(button)
        button.setDefault(on)

    def validate(self, *args):
        """Eingaben schon beim Tippen prüfen: rote Felder mit Grund, Bau-Knopf gesperrt."""
        self._update_paths_summary()
        problems = {}
        name = self.name.text().strip()
        if not name:
            problems[self.name] = tr("Please enter a value.")
        elif not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", name):
            problems[self.name] = tr("Build name: 1–80 letters/digits, hyphen or underscore.")
        paths = {}
        for field in (self.output, self.packages):
            try:
                if not field.text().strip():
                    problems[field] = tr("Please enter a value.")
                else:
                    paths[field] = writable_path(field.text())
            except (ValueError, OSError) as error:
                problems[field] = meldung(error)
        if self.name not in problems and len(paths) == 2:
            if (paths[self.packages] / (name + ".zip")).exists() or (paths[self.output] / name).exists():
                problems[self.name] = tr("Build name already exists. Suggestion: {name}_neu", name=name)
        carrier = self.carrier.text().strip()
        if not carrier:
            problems[self.carrier] = tr("Please enter a value.")
        elif not Path(carrier).expanduser().is_file():
            problems[self.carrier] = tr("Carrier package missing: {carrier}", carrier=carrier)
        for field in (self.name, self.output, self.packages, self.carrier):
            field.setStyleSheet("border: 1px solid #F07878;" if field in problems else "")
            if field in problems:
                field.setToolTip(problems[field])
        self.name.setToolTip(problems.get(self.name) or tr("Suggested consecutively (last build + 1); append a "
                                                           "description with an underscore, e.g. “_tafeln50”."))
        self.valid = not problems
        self.path_problem = any(f in problems for f in (self.output, self.packages, self.carrier))
        self.field_error.setText("\n".join(problems.values()))
        self.field_error.setVisible(bool(problems))
        self.start_button.setToolTip(next(iter(problems.values()), ""))
        self._update_buttons()

    # ------------------------------------------------------------------ Streckencode und Zustand im Spiel
    def _fill_codes(self):
        current = self.code.currentData()
        self.code.blockSignals(True)
        self.code.clear()
        for code, rating, hint in TRACK_CODES:
            self.code.addItem(code_label(code, rating, self.game_state), code)
            self.code.setItemData(self.code.count() - 1, tr(hint), Qt.ToolTipRole)
        if current:
            self.code.setCurrentIndex(max(0, self.code.findData(current)))
        self.code.blockSignals(False)

    def swofty(self):
        return self.target.currentData() == "swofty"

    def _place_track_name(self):
        """Für Swofty bestimmt der Name auch den Streckencode: dann in den Hauptteil, sonst unter Erweitert."""
        if not hasattr(self, "track_name_label"):
            return
        target, source = (self.main_form, self.advanced_form) if self.swofty() else (self.advanced_form, self.main_form)
        if source.getWidgetPosition(self.track_name)[0] < 0:
            return
        source.takeRow(self.track_name)
        row = 2 if self.swofty() else 0                  # unter „Was ist neu?“ bzw. oben in den Erweiterten Optionen
        target.insertRow(row, self.track_name_label, self.track_name)
        self.track_name_label.show()
        self.track_name.show()

    def _update_code_info(self, *args):
        self._place_track_name()
        if hasattr(self, "shared_carrier"):
            self.shared_carrier.setVisible(self.swofty())
        self._update_buttons()
        self.code.setVisible(not self.swofty())
        self.code_label.setVisible(not self.swofty())
        installed = (self.status_read and self.game_state is not None and
                     self.code.currentData() in (self.game_state.get("installiert") or {}))
        self.install_hint.setVisible(not self.swofty() and not installed)    # nur der Track Installer, nur beim ersten Mal
        if self.swofty():
            self.code_info.setText(tr("Creates an addon: drag it onto the launcher's Addons page, then start the game "
                                      "from the launcher. The code comes from the track name, and the menus show that "
                                      "name."))
            self._update_profile_hint()
            return
        code = self.code.currentData()
        hint = next((tr(h) for c, _, h in TRACK_CODES if c == code), "")
        if self.status_job is not None:
            state = tr("Reading state in the game …")
        elif self.status_read:
            state = code_status(code, self.game_state)
        else:
            state = ""
        self.code_info.setText(state.strip() or hint)
        self.code.setToolTip(hint)
        self._update_profile_hint()

    def refresh_game_status(self):
        """Installierte Codes und Datenbankzustand lesen (nur lesend; merkt eine aktivierte Datenbank)."""
        if self.status_job is not None or not self.read_game:
            return
        try:
            root = toolkit_path(self.project.manifest.get("werkzeugkasten"))
            command = self.status_command(root)
        except (ValueError, OSError):
            self.status_read = True
            self._update_code_info()
            return
        self.status_job = ProcessJob(self)
        self.status_job.done.connect(self._game_status_done)
        self.status_job.start(command)
        self._update_code_info()

    def _game_status_done(self, code, text):
        job, self.status_job = self.status_job, None
        if job:
            job.deleteLater()
        self.status_read = True
        self.game_state = parse_game_status(text) if code == 0 else None
        self._fill_codes()
        self._update_code_info()

    def _toggle_paths(self, on):
        self.paths_box.setVisible(on)
        self.paths_toggle.setText(tr("Folders ▾") if on else tr("Folders ▸"))

    def _update_paths_summary(self):
        self.paths_summary.set_full(" · ".join((self.output.text(), self.packages.text(),
                                                  Path(self.carrier.text()).name or "–")))

    def _toggle_advanced(self, on):
        self.advanced.setVisible(on)
        self.advanced_toggle.setText(tr("Advanced build options ▾") if on else tr("Advanced build options ▸"))

    def options(self):
        return {"ziel": self.target.currentData(), "traeger_teilen": self.shared_carrier.isChecked(),
                "streckencode": self.code.currentData(),
                "renndaten": self.race_data.isChecked(), "raster": self.grid_size.currentData(),
                "streckenname": self.track_name.text().strip(),
                "pvs_variante": self.pvs.currentData(), "weltkarten": self.world_maps.isChecked(),
                "biom": self.biome.currentData(), "farbton": self.tint.currentData()}

    def _update_profile_hint(self, *args):
        """Eine Zeile Zusammenfassung vor dem Bau: Ziel, Code, Sichtbarkeit, Raster, Name, Größe."""
        if not hasattr(self, "pvs"):
            return
        o = self.options()
        shared = self.swofty() and o["traeger_teilen"]
        self.profile_hint.setText(
            tr("{ziel} · code {streckencode} · PVS {pvs} · {raster} · name “{name}” · {groesse}",
               ziel=self.target.currentText(),
               streckencode=tr("from the name") if self.swofty() else o['streckencode'],
               pvs=tr("along the track") if o['pvs_variante'] == 'strecke' else tr("Original"),
               raster=self.grid_size.currentText(),
               name=o['streckenname'] or self.project.raw.get('name') or tr("Test track"),
               groesse=tr("about 0.45 GB") if shared else tr("about 3 GB")))
        self.profile_hint.setToolTip(tr("Before the build the project is saved and checked (same state: result of the "
                                        "last check). The build works on a snapshot; you can keep working during the "
                                        "build."))

    def update_space(self):
        try:
            output, packages = build_folders(self.project, self.output.text(), self.packages.text())
            text = space_text(output, packages)
            self.space.setText(text)
            self.space.setStyleSheet("color: #F07878;" if tr(" – not enough!") in text else "")
        except ValueError as error:
            self.space.setText(meldung(error))

    def choose(self, field, file):
        if file:
            value, _ = QFileDialog.getOpenFileName(self, tr("Choose carrier package"), field.text(), tr("ZIP (*.zip)"))
        else:
            value = QFileDialog.getExistingDirectory(self, tr("Choose your own output folder"), field.text())
        if value:
            field.setText(value)

    def busy(self, active):
        self.fields.setEnabled(not active)
        self.cancel_button.setEnabled(active and getattr(self, "inject_job", None) is None)
        self.close_button.setText(tr("Hide") if active else tr("Close"))
        self.inject_button.setEnabled(not active and self.inject_record is not None)
        for button in (self.install_button, self.play_button):
            button.setEnabled(not active and self.launcher_job is None and self.launcher_record is not None)
        self.cancel_button.setText(tr("Cancel"))
        if active and self.job is None and self.state not in ("checking", "building"):
            self._state_before = self.state
            self.set_state("working")
        elif not active:
            self._end_run()
            if self.state == "working":
                self.set_state(getattr(self, "_state_before", "ready"))
        self.busy_now = active
        self._update_buttons()

    def _update_buttons(self):
        """Die Leiste zeigt nur, was gerade geht: Abbrechen beim Bau, das Weitergeben zum Bauziel, Ergebnisse nach dem Bau.
        Hauptaktion (blau) ist immer der nächste sinnvolle Schritt."""
        if not hasattr(self, "launcher_job"):
            return
        busy = getattr(self, "busy_now", False)
        swofty = self.swofty()
        self.start_button.setEnabled(not busy)           # ungültige Eingaben meldet der Klick (Ordner springen auf)
        self.cancel_button.setVisible(self.cancel_button.isEnabled() or self.job is not None)
        self.inject_button.setVisible(self.inject_record is not None and not swofty)
        self.install_button.setVisible(self.launcher_record is not None and swofty)
        self.play_button.setVisible(self.launcher_record is not None and swofty)
        self.diagnosis_button.setVisible(swofty)
        self.explorer_button.setVisible(self.explorer_button.isEnabled())
        self.report_button.setVisible(self.report_button.isEnabled())
        built = self.result is not None and self.job is None and self.state in ("done", "working")
        primary = None
        if built:
            primary = self.play_button if swofty and self.launcher_record else \
                self.inject_button if not swofty and self.inject_record else None
        for button in (self.inject_button, self.play_button):
            self._style_primary(button, button is primary)
        self._style_primary(self.start_button, primary is None)
        self.start_button.setText(tr("Rebuild") if built else tr("Check and build"))

    def start_build(self):
        if self.job:
            return
        try:
            if self.project.readonly or not self.project.closed:
                raise ValueError(tr("A build needs a closed lap and a known project version."))
            if self.main.properties.pending():
                raise ValueError(tr("Correct the invalid field input first."))
            self.validate()
            if not self.valid:
                if self.path_problem:
                    self.paths_toggle.setChecked(True)   # fehlerhafte Ordner/Träger sichtbar machen
                    self.paths_box.setFocus()
                raise ValueError(self.field_error.text().splitlines()[0])
            if not all(f.text().strip() for f in (self.name, self.output, self.packages, self.carrier)):
                raise ValueError(tr("Please enter build name, both output folders and carrier package."))
            root = toolkit_path(self.project.manifest.get("werkzeugkasten"))
            output, packages = writable_path(self.output.text()), writable_path(self.packages.text())
            carrier = Path(self.carrier.text()).expanduser().resolve()
            if not carrier.is_file():
                raise ValueError(tr("Carrier package missing: {carrier}", carrier=carrier))
            # Zielschutz und Namen vor dem Speichern prüfen; Befehl danach neu bilden.
            build_command(root, self.project.basis, output, packages, self.name.text(), carrier=carrier,
                          options=self.options())
            check_space(output, packages)
            settings = {"ausgabe": str(output), "pakete": str(packages), "traeger": str(carrier),
                        "ki_profil": self.ki_profil.isChecked(), **self.options()}
            self.main.document.edit(tr("Change build settings"), lambda raw, meta: meta.update(bauen=settings))
            if not self.main.save_project():
                return
            self.command = build_command(root, self.project.folder, output, packages, self.name.text(), carrier=carrier,
                                         ki_profil=self.ki_profil.isChecked(), options=self.options(),
                                         umgebung=self.umgebung_datei())
            self.busy(True)
            self.result = None
            self.record = None
            self.messages.clear()
            self.issue_list.clear()
            self._update_issue_summary()
            self.log.clear()
            self.result_text.clear()
            self._begin_run()
            self.set_state("checking")
            self.explorer_button.setEnabled(False)
            self.report_button.setEnabled(False)
            self._update_buttons()
            self.progress.setValue(0)
            shift = self.project.manifest.get("versatz", [0, 0, 0])
            self.check_key = check_key(self.project.raw, self.project.folder, shift)
            cached = getattr(self.main, "check_cache", {}).get(self.check_key)
            if cached is not None:
                self.phase.setText(tr("Pre-check: using the result of the last check of this state"))
                self.log.appendPlainText(tr("Pre-check from the cache (same definition, base and offset)."))
                self.use_check(cached)
                return
            self.phase.setText(tr("Pre-check with generator …"))
            self.job = ProcessJob(self)
            self.job.done.connect(self.checked)
            self.job.start(worker_command(), {"task": "analyse", "raw": copy.deepcopy(self.project.raw),
                                              "basis": str(self.project.folder), "toolkit": str(root), "shift": shift})
        except (ValueError, OSError) as error:
            self.phase.setText(meldung(error))
            self.set_state("error")

    def umgebung_datei(self):
        """Gespeicherte ``umgebung.json`` des Projekts für den Bau (Schalter ``--umgebung``); None ohne Umgebung."""
        project = self.project
        if project.umgebung is None or project.umgebung_fehler or not project.folder:
            return None
        path = project.umgebung_path()
        return path if path.is_file() else None

    def checked(self, code, text):
        old, self.job = self.job, None
        old.deleteLater()
        try:
            if code == -2:
                self.phase.setText(tr("Pre-check cancelled · no build started"))
                self.set_state("cancelled")
                self.busy(False)
                return
            response = json.loads(text)
            if code or "error" in response:
                raise ValueError(response.get("error", text))
            if hasattr(self.main, "store_analysis") and "analysis" in response:
                self.main.store_analysis(self.check_key, response)
        except (ValueError, KeyError, OSError) as error:
            self.phase.setText(meldung(error))
            self.set_state("error")
            self.busy(False)
            return
        self.use_check(response)

    def use_check(self, response):
        try:
            report = response["report"]
            self.main.set_issues(response["issues"], report)
            for message in report["meldungen"]:
                self.add_message(message)
            if not report["zusammenfassung"]["ok"]:
                raise ValueError(tr("Pre-check: fix the errors before a driving package can be built."))
            self.launch()
        except (ValueError, KeyError, OSError) as error:
            self.phase.setText(meldung(error))
            self.set_state("error")
            self.busy(False)

    def launch(self):
        """Startgrenze nach erfolgreicher Prüfung; auch für Prozessattrappen nutzbar."""
        def option(name):
            return self.command[self.command.index(name) + 1]
        work, abort = option("--ausgabe"), option("--abbruch-datei")
        self.record = {"id": uuid.uuid4().hex, "datum": datetime.now().astimezone().isoformat(timespec="seconds"),
                       "name": self.name.text(), "pakete": [], "paket": "", "bau_status": "läuft",
                       "status": "offen", "ergebnis": "", "ausgabe": work, "beschreibung": self.note.text().strip(),
                       "optionen": self.options()}
        self.project.playtests.append(self.record)
        try:
            self.project.save_playtests()
        except (OSError, ValueError):
            self.project.playtests.remove(self.record)
            self.record = None
            raise
        self.job = BuildJob(abort, work, self)
        env = self.job.process.processEnvironment()
        env.insert("MAPEDITOR_WERKZEUGKASTEN", str(toolkit_path(self.project.manifest.get("werkzeugkasten"))))
        self.job.process.setProcessEnvironment(env)
        self.job.event.connect(self.on_event)
        self.job.output.connect(self.log.insertPlainText)
        self.job.done.connect(self.build_finished)
        self.busy(True)
        self.set_state("building")
        self.phase.setText(tr("Build starting · the saved project is taken over as a snapshot"))
        self.job.start(self.command)

    def add_message(self, message):
        from .adapter import issue_from_message
        issue = issue_from_message(message)
        self.messages.append(issue)
        rank, color, symbol = ISSUE_LEVELS.get(issue["level"], ISSUE_LEVELS["Hinweis"])
        item = QListWidgetItem(f"{symbol} {tr(issue['level'])} · {issue.get('kurz', issue['id'])} · "
                               f"{meldung(issue['text'])}")
        item.setData(Qt.UserRole, issue)
        item.setData(Qt.UserRole + 1, rank)
        if rank < 2:
            item.setForeground(QBrush(QColor(color)))
        row = self.issue_list.count()                  # Fehler vor Warnungen vor Hinweisen, sonst in Eingangsreihenfolge
        while row > 0 and self.issue_list.item(row - 1).data(Qt.UserRole + 1) > rank:
            row -= 1
        self.issue_list.insertItem(row, item)
        self._update_issue_summary()
        self.main.add_build_issue(issue, self.record["id"] if self.record else tr("Pre-check"))

    def _update_issue_summary(self):
        counts = [0, 0, 0]
        for issue in self.messages:
            counts[ISSUE_LEVELS.get(issue["level"], ISSUE_LEVELS["Hinweis"])[0]] += 1
        self.issue_summary.setText(" · ".join((
            trn(counts[0], "{n} error", "{n} errors"), trn(counts[1], "{n} warning", "{n} warnings"),
            trn(counts[2], "{n} hint", "{n} hints"))))
        self.issue_summary.setStyleSheet("color: #F07878; font-weight: 600;" if counts[0] else
                                         "color: #D6A94A; font-weight: 600;" if counts[1] else "")
        self.issue_box.setVisible(bool(self.messages))
        self._apply_issue_filter()

    def _apply_issue_filter(self, *args):
        limit = {"alle": 2, "probleme": 1, "fehler": 0}[self.issue_show.currentData()]
        for row in range(self.issue_list.count()):
            item = self.issue_list.item(row)
            item.setHidden(item.data(Qt.UserRole + 1) > limit)

    def jump_message(self, item):
        self.main.jump_issue(item)

    def on_event(self, event):
        typ = event["typ"]
        if typ == "start":
            entry = event.get("eingabe", {})
            self.record["eingabe"] = entry
            self.log.appendPlainText(tr("Snapshot: ") + json.dumps(entry, ensure_ascii=False))
        elif typ == "phase":
            title = meldung(event.get("titel", event.get("phase", tr("Build run"))))
            self.phase.setText(title)
            self.progress.setValue(round(float(event.get("fortschritt", 0)) * 1000))
            self._step(event.get("phase") or title, title)
            if not self.isVisible():                   # ausgeblendet: Fortschritt in der Statusleiste des Hauptfensters
                label = getattr(self.main, "status_label", None)
                if label is not None:
                    label.setText(tr("Build: {phase} ({prozent} %)", phase=title, prozent=self.progress.value() // 10))
        elif typ == "meldung":
            self.add_message(event["meldung"])
        elif typ == "log":
            self.log.appendPlainText(event.get("text", ""))
        elif typ == "pruefung":
            self.log.appendPlainText(tr("Check {stufe}: {zusammenfassung}", stufe=tr(str(event.get('stufe'))), zusammenfassung=event.get('zusammenfassung')))
        elif typ == "ergebnis":
            self.result = event
        elif typ in ("fehler", "abbruch"):
            self.phase.setText(meldung(event.get("text", typ)))
            self.log.appendPlainText(json.dumps(event, ensure_ascii=False))

    def build_finished(self, code, text):
        job, self.job = self.job, None
        terminal = job.terminal
        expected = {"ok", "mit_traegerabweichung"}
        success = (code == 0 and terminal and terminal.get("typ") == "ergebnis" and
                   terminal.get("status") in expected and not job.protocol_error and
                   bool(terminal.get("pakete")) and all(p.get("pruefung_ok") for p in terminal["pakete"]))
        if success:
            self.result = terminal
            label = tr("Package checked") if terminal["status"] == "ok" else tr("Checked with carrier deviation")
            self.phase.setText(label + tr(" · playtest open"))
            self.progress.setValue(1000)
            self.set_state("done")
            packages = terminal["pakete"]
            self.record.update(pakete=packages, paket=packages[0]["pfad"], bau_status=terminal["status"],
                               pruefbericht=terminal.get("pruefbericht"), bericht_md=terminal.get("bericht_md"))
            lines = [label]
            self._end_run()
            self.result_title.setText(f"{Path(packages[0]['pfad']).name} · "
                                      f"{zahl(packages[0].get('groesse', 0) / 1e9, 2, 'GB')} · "
                                      f"{self.elapsed_label.text()}")
            self.result_title.show()
            self.copy_button.setVisible(bool(packages[0].get("sha256")))
            for package in packages:
                lines += [package["pfad"], tr("{groesse:.3f} GB · SHA-256: {sha256}", groesse=package.get('groesse', 0) / 1e9,
                                                    sha256=package.get('sha256', tr("not available")))]
            lines += [tr("Check report: ") + str(terminal.get("pruefbericht", "")), tr("Report: ") + str(terminal.get("bericht_md", ""))]
            self.result_text.setPlainText("\n".join(lines))
            self.explorer_button.setEnabled(True)
            self.report_button.setEnabled(bool(terminal.get("pruefbericht")))
            self._update_buttons()
            self.set_inject_target(self.record)
            if self.record["optionen"].get("ziel") == "swofty":
                self.result_text.appendPlainText(tr(("Drag it onto the Addons page of Swofty Launcher and start the "
                                                     "game from the launcher.")))
        else:
            self.result = None
            status = "abgebrochen" if code in (3, -2) else "fehlgeschlagen"
            reason = job.protocol_error or (terminal or {}).get("text") or {
                2: tr("Check error · package discarded"), -2: tr(("Timeout while cancelling · process tree ended; "
                                                               "temporary packages removed")),
                3: tr("Build cancelled"), -1: text,
            }.get(code, "Bau fehlgeschlagen oder vollständiges Ergebnis fehlt")
            self.phase.setText(reason)
            self.set_state("cancelled" if code in (3, -2) else "error")
            self.result_text.setPlainText(tr("{status} · exit code {code}\n{reason}", status=tr(status).capitalize(), code=code, reason=reason))
            self.record.update(bau_status=status, bau_fehler=reason)
        self.record["exitcode"] = code
        try:
            self.project.save_playtests()
        except (ValueError, OSError) as error:
            self.phase.setText(self.phase.text() + tr(" · log not saved: {error}", error=error))
        self.busy(False)
        job.deleteLater()
        if not self.isVisible():                       # im Hintergrund fertig: Hauptfenster melden
            label = getattr(self.main, "status_label", None)
            if label is not None:
                label.setText(tr("Build finished: {name} · open Build / package to see the result.", name=self.name.text())
                              if success else tr("Build failed or cancelled · open Build / package for details."))
            QApplication.alert(self.main)
        if success:
            try:
                output, packages = build_folders(self.project, self.output.text(), self.packages.text())
                self.name.setText(next_build_name(self.project, output, packages))
            except (OSError, ValueError):
                pass
            self.update_space()

    # ------------------------------------------------------------------ Schnellaustausch
    def set_inject_target(self, record):
        if (record.get("optionen") or {}).get("ziel") == "swofty":  # Addon, kein Paket für den Schnellaustausch
            self.inject_record = None
            self.inject_button.setEnabled(False)
            self.inject_button.setToolTip(tr("Swofty addons reach the game through the launcher."))
            self.launcher_record = record
            self.install_button.setEnabled(self.job is None)
            self.play_button.setEnabled(self.job is None)
            self._update_buttons()
            return
        self.launcher_record = None
        self.install_button.setEnabled(False)
        self.play_button.setEnabled(False)
        self.inject_record = record
        name = Path(record["paket"]).name
        self.inject_button.setEnabled(self.job is None)
        self._update_buttons()
        self.inject_button.setToolTip(tr(("Write {name} to F1 25 via quick swap: show the plan first, then write "
                                          "after your confirmation (affected game files are backed up first)."), name=name))

    # ------------------------------------------------------------------ Swofty Launcher
    def launcher_program(self):
        """``swofty.exe`` finden; beim ersten Mal wählen und in den Baueinstellungen merken."""
        # Programmweit gemerkt (QSettings), damit nicht jedes Projekt neu fragt; der Projektwert bleibt als Rückfall
        from .ansicht import settings as app_settings
        store = app_settings()
        settings = self.project.manifest.get("bauen", {})
        program = swofty_programm(store.value("swofty/programm") or None) or swofty_programm(settings.get("swofty_programm"))
        if program is None:
            value, _ = QFileDialog.getOpenFileName(self, tr("Choose swofty.exe (the launcher's bin folder)"), "", "swofty.exe")
            if not value:
                return None
            program = Path(value)
        store.setValue("swofty/programm", str(program))
        return program

    def launcher_start(self, play):
        program = self.launcher_program()
        if program is None or self.launcher_record is None:
            return
        try:
            # Träger aus dem Baubericht oder aus „requires“ des Addons; fehlt er im Launcher, wird er mitinstalliert
            package = self.launcher_record["paket"]
            carrier = next((p.get("traeger") for p in self.launcher_record.get("pakete") or [] if p.get("traeger")), None)
            installed = swofty_installierte(program) if (carrier or swofty_traeger_ids(package)) else set()
            carrier, enable_only = swofty_traeger_plan(package, carrier, installed)
            steps = [[str(program), "addons", "enable", k] for k in enable_only] + \
                swofty_installieren(program, package, carrier, installed)
        except (OSError, KeyError, ValueError, zipfile.BadZipFile) as error:
            self.phase.setText(meldung(error))
            return
        self.launcher_steps = steps + (swofty_starten(program) if play else [])
        self.launcher_play = play
        self.launcher_diagnose = [swofty_auswerten(program)] if play else []
        self.install_button.setEnabled(False)
        self.play_button.setEnabled(False)
        self._launcher_next()

    def launcher_diagnosis(self):
        program = self.launcher_program()
        if program is None or self.launcher_job is not None:
            return
        self.launcher_steps, self.launcher_play, self.launcher_diagnose = [], False, [swofty_auswerten(program)]
        self._launcher_next()

    def _launcher_next(self):
        steps = self.launcher_steps or self.launcher_diagnose
        step = steps.pop(0)
        self.launcher_output = []
        self.phase.setText(tr("Swofty Launcher: {schritt}", schritt=" ".join(step[1:3])))
        self.log.appendPlainText("$ swofty " + " ".join(step[1:]))
        self.launcher_job = ProcessJob(self)
        self.launcher_job.output.connect(self.log.insertPlainText)
        self.launcher_job.output.connect(self.launcher_output.append)
        self.launcher_job.done.connect(lambda code, text, step=step: self._launcher_done(step, code))
        self.launcher_job.start(step)

    def _launcher_done(self, step, code):
        job, self.launcher_job = self.launcher_job, None
        job.deleteLater()
        text = "".join(self.launcher_output)
        if step[1:2] == ["logs"]:
            self._launcher_report(text)
        elif code and step[1:2] != ["run"]:      # Installation gescheitert; ein Absturz endet in der Auswertung
            self.launcher_steps, self.launcher_diagnose = [], []
            self.phase.setText(tr("Swofty Launcher: error · {text}", text=text.strip()[-300:]))
        if self.launcher_steps or self.launcher_diagnose:
            self._launcher_next()
        elif step[1:2] != ["logs"] and not code:
            self.phase.setText(tr("Installed in the launcher. Start it there or from here."))
        self.busy(self.job is not None)

    def _launcher_report(self, text):
        """Sitzungsauswertung zeigen und beim Spieltest ablegen."""
        # „swofty logs“ druckt erst das ganze Protokoll, ab „[info] game exited“ folgt die Zusammenfassung
        start = re.search(r"^\[info\] game exited.*$", text, re.M)
        crash = bool(re.search(r"^\[crash\] reason\s+unhandled", text, re.M)) or bool(start and "(crash)" in start.group())
        self.phase.setText(tr("Crash in the last session · details under Result") if crash
                           else tr("Last session read · no crash reported"))
        self.result_text.setPlainText(text[start.start() if start else 0:].strip())
        record = self.launcher_record
        if record and record.get("ausgabe"):
            try:
                path = Path(record["ausgabe"]) / datetime.now().strftime("spielsitzung_%Y%m%d_%H%M%S.txt")
                path.write_text(text, encoding="utf-8")
                record.setdefault("spielsitzungen", []).append({"datei": str(path), "absturz": crash})
                self.project.save_playtests()
            except (OSError, ValueError) as error:
                self.phase.setText(self.phase.text() + tr(" · log not saved: {error}", error=error))

    def _run_injector(self, action, finished):
        try:
            root = toolkit_path(self.project.manifest.get("werkzeugkasten"))
            output, _ = build_folders(self.project, self.output.text(), self.packages.text())
            command = self.injector(root, action, self.inject_record["paket"], backup_folder(output))
        except (ValueError, OSError) as error:
            self.phase.setText(tr("Quick swap not possible: {error}", error=error))
            return
        self.log.appendPlainText("$ " + " ".join(map(str, command)))
        self.inject_job = ProcessJob(self)
        self.inject_job.output.connect(self.log.insertPlainText)
        self.inject_job.done.connect(finished)
        self.busy(True)
        self.inject_job.start(command)

    def inject_start(self):
        if self.job or self.inject_job or not self.inject_record:
            return
        self.phase.setText(tr("Quick swap: creating plan for {name} …", name=Path(self.inject_record['paket']).name))
        self._run_injector("plan", self.plan_done)

    def _finish_injector(self):
        job, self.inject_job = self.inject_job, None
        if job:
            job.deleteLater()
        self.busy(False)

    def plan_done(self, code, text):
        self._finish_injector()
        if code != 0:
            self.phase.setText(tr("Quick swap not possible: ") + _tool_error(text, code))
            return
        if "Nichts zu tun" in text:
            self.phase.setText(tr("This package is already in the game · nothing transferred."))
            return
        if not self.confirm_injection(text):
            self.phase.setText(tr("Quick swap cancelled · nothing changed."))
            return
        self.phase.setText(tr("Writing to the game … (backing up the affected files, then swapping)"))
        self._run_injector("injizieren", self.inject_done)

    def confirm_injection(self, plan_text):
        """Eigene Bestätigung je Übertragung (schreibt in den Spielordner)."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(tr("Transfer to game?"))
        box.setText(tr("Write <b>{name}</b> to F1 25 now?", name=Path(self.inject_record['paket']).name))
        box.setInformativeText(tr("The quick swap replaces track ERP, mipmaps, ERS and minimap values of the already "
                                  "installed track code and, if the mod is deactivated, activates the game database "
                                  "from the remembered activation. The affected game files are backed up first; the "
                                  "game must be closed. Details: “Show Details”."))
        box.setDetailedText(plan_text)
        write = box.addButton(tr("Write to game"), QMessageBox.AcceptRole)
        box.addButton(tr("Cancel"), QMessageBox.RejectRole)
        box.setDefaultButton(QMessageBox.Cancel)
        box.exec()
        return box.clickedButton() is write

    def inject_done(self, code, text):
        self._finish_injector()
        if code != 0:
            self.phase.setText(tr("Quick swap failed: ") + _tool_error(text, code))
            return
        record = self.inject_record
        record["injiziert"] = datetime.now().astimezone().isoformat(timespec="seconds")
        try:
            self.project.save_playtests()
        except (ValueError, OSError) as error:
            self.log.appendPlainText(tr("Log not saved: {error}", error=error))
        self.phase.setText(tr("{name} is in the game · record the playtest", name=Path(record['paket']).name))
        self.refresh_game_status()
        self.open_playtest(record)

    def open_playtest(self, record):
        if hasattr(self.main, "open_playtests"):
            self.main.open_playtests(select=record.get("id"))

    def cancel_build(self):
        if self.job:
            try:
                self.job.cancel()
                self.cancel_button.setEnabled(False)
                self.cancel_button.setText(tr("Cancelling …"))
                self.phase.setText(tr("Cancelling … next phase boundary, ends after 120 s at the latest."))
            except OSError as error:
                self.phase.setText(tr("Could not create cancel file: {error}", error=error))

    def show_package(self):
        if self.result:
            path = self.result["pakete"][0]["pfad"]
            if os.name == "nt":
                subprocess.Popen(["explorer.exe", "/select,", str(Path(path))])
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))

    def show_report(self):
        if self.result:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.result["pruefbericht"]))

    def _running(self):
        return bool(self.job or self.inject_job or self.launcher_job)

    def reject(self):
        if self._running():
            self.close()                               # Esc blendet nur aus
        else:
            super().reject()

    def closeEvent(self, event):
        if self._running():
            event.ignore()                             # der Bau läuft im Hintergrund weiter
            self.background_hidden = True
            self.hide()
        else:
            event.accept()


def _tool_error(text, code):
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    error = next((line for line in reversed(lines) if line.startswith("FEHLER")), None)
    return error or (lines[-1] if lines else tr("Exit code {code}", code=code))


class CleanupDialog(QDialog):
    """Alte Bauten (Arbeitsordner) und Pakete mit Größe und Spieltest-Ergebnis; Löschen nach Bestätigung.

    Vorausgewählt ist alles außer dem jüngsten Bau, dem jüngsten Bau mit Spieltest „ok“ und dem zuletzt ins Spiel
    übertragenen Paket. Gelöscht wird nur direkt in den beiden Ordnern (Name des Baus, ``.zip``, ``.abbruch``)."""

    def __init__(self, project, output, packages, parent=None):
        super().__init__(parent)
        self.project = project
        self.output, self.packages = Path(output).resolve(), Path(packages).resolve()
        self.setWindowTitle(tr("Clean up old builds"))
        self.resize(860, 520)
        layout = QVBoxLayout(self)
        info = QLabel(tr(("Working folder: {output}\nPackage folder: {packages}\nPreselected: everything except the "
                          "newest build, the newest build with playtest “ok” and the package last transferred to the "
                          "game."), output=self.output, packages=self.packages))
        info.setWordWrap(True)
        layout.addWidget(info)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([tr("Delete"), tr("Build run"), tr("Date"), tr("Size"), tr("Build status"), tr("Playtest")])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemChanged.connect(self._update_total)
        layout.addWidget(self.table, 1)
        self.total = QLabel()
        layout.addWidget(self.total)
        row = QHBoxLayout()
        self.delete_button = QPushButton(tr("Delete selected …"))
        self.delete_button.clicked.connect(self.delete_selected)
        close = QPushButton(tr("Close"))
        close.clicked.connect(self.accept)
        row.addStretch(1)
        row.addWidget(self.delete_button)
        row.addWidget(close)
        layout.addLayout(row)
        self.reload()

    def entries(self):
        """[{name, pfade, groesse, datum, eintrag}] aller Bauten/Pakete in den beiden Ordnern, älteste zuerst."""
        names = {}
        if self.output.is_dir():
            for p in self.output.iterdir():
                if p.is_dir():
                    names.setdefault(p.name, []).append(p)
                elif p.suffix == ".abbruch":
                    names.setdefault(p.stem, []).append(p)
        if self.packages.is_dir():
            for p in self.packages.glob("*.zip"):
                names.setdefault(p.stem, []).append(p)
            for p in self.packages.glob("*.json"):            # Nebenberichte „<bau>_….json“: längster passender Bau
                owner = max((n for n in names if p.name.startswith(n + "_")), key=len, default=None)
                if owner:
                    names[owner].append(p)
        records = {}
        for entry in self.project.playtests:
            records[entry.get("name")] = entry
        out = []
        for name, paths in names.items():
            if not any(p.is_dir() or p.suffix == ".zip" for p in paths):
                continue
            size = sum(dir_size(p) if p.is_dir() else p.stat().st_size for p in paths)
            date = max(p.stat().st_mtime for p in paths if p.is_dir() or p.suffix == ".zip")   # ohne Nebenberichte
            out.append({"name": name, "pfade": paths, "groesse": size, "datum": date, "eintrag": records.get(name)})
        return sorted(out, key=lambda e: e["datum"])

    def reload(self):
        self.items = self.entries()
        keep = set()
        if self.items:
            keep.add(self.items[-1]["name"])
        good = [e for e in self.items if (e["eintrag"] or {}).get("status") == "ok"]
        if good:
            keep.add(good[-1]["name"])
        injected = [e for e in self.items if (e["eintrag"] or {}).get("injiziert")]
        if injected:
            keep.add(max(injected, key=lambda e: e["eintrag"]["injiziert"])["name"])
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.items))
        for r, e in enumerate(self.items):
            record = e["eintrag"] or {}
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            check.setCheckState(Qt.Unchecked if e["name"] in keep else Qt.Checked)
            self.table.setItem(r, 0, check)
            status = tr(record.get("status", "–")) + (tr(" · in game") if record.get("injiziert") else "")
            for c, value in enumerate((e["name"], datetime.fromtimestamp(e["datum"]).strftime("%d.%m.%Y %H:%M"),
                                       zahl(e["groesse"] / 1e9, 2, "GB"), tr(record.get("bau_status", "–")), status), 1):
                self.table.setItem(r, c, QTableWidgetItem(value))
        self.table.blockSignals(False)
        self._update_total()

    def selected(self):
        return [e for r, e in enumerate(self.items) if self.table.item(r, 0).checkState() == Qt.Checked]

    def _update_total(self, *args):
        chosen = self.selected()
        total = sum(e["groesse"] for e in self.items)
        self.total.setText(tr("{anzahl} of {gesamt} selected · {groesse} of {total}", anzahl=len(chosen), gesamt=len(self.items),
                              groesse=zahl(sum(e['groesse'] for e in chosen) / 1e9, 1, 'GB'), total=zahl(total / 1e9, 1, 'GB')))
        self.delete_button.setEnabled(bool(chosen))

    def confirm(self, chosen):
        size = sum(e["groesse"] for e in chosen)
        answer = QMessageBox.warning(
            self, tr("Delete builds?"), tr(("Permanently delete {anzahl} builds totalling {size}?\nThis cannot be "
                                             "undone. The playtest log keeps the entries (marked as deleted)."), anzahl=len(chosen), size=zahl(size / 1000000000.0, 1, 'GB')),
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        return answer == QMessageBox.Yes

    def delete_selected(self):
        chosen = self.selected()
        if not chosen or not self.confirm(chosen):
            return
        errors, stamp = [], datetime.now().astimezone().isoformat(timespec="seconds")
        for e in chosen:
            for path in e["pfade"]:
                target = Path(path).resolve()
                if target.parent not in (self.output, self.packages):
                    errors.append(tr("{target}: not directly inside the working or package folder", target=target))
                    continue
                try:
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                except OSError as error:
                    errors.append(f"{target}: {error}")
            for record in self.project.playtests:
                if record.get("name") == e["name"]:
                    record["geloescht"] = stamp
        try:
            if any(r.get("geloescht") == stamp for r in self.project.playtests):
                self.project.save_playtests()
        except (ValueError, OSError) as error:
            errors.append(tr("Playtest log: {error}", error=error))
        self.reload()
        if errors:
            QMessageBox.warning(self, tr("Not everything deleted"), "\n".join(errors[:12]))


class PlaytestDialog(QDialog):
    def __init__(self, project, parent=None, select=None):
        super().__init__(parent)
        self.project = project
        self.setWindowTitle(tr("Playtest log"))
        self.resize(850, 550)
        layout = QVBoxLayout(self)
        self.entries = QListWidget()
        for entry in project.playtests:
            self.entries.addItem(self._title(entry))
        layout.addWidget(self.entries)
        self.package = QLabel(tr("No build in the project yet."))
        self.package.setWordWrap(True)
        self.package.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.package)
        self.status = QComboBox()
        for state in PLAYTEST_STATES:
            self.status.addItem(tr(state), state)
        layout.addWidget(self.status)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText(tr("Playtest result: track, slot, observation …"))
        layout.addWidget(self.text)
        self.message = QLabel()
        layout.addWidget(self.message)
        self.save_button = QPushButton(tr("Save playtest"))
        self.save_button.setEnabled(bool(project.playtests) and not project.readonly)
        self.save_button.clicked.connect(self.save)
        layout.addWidget(self.save_button)
        self.entries.currentRowChanged.connect(self.select)
        if project.playtests:
            index = next((i for i, e in enumerate(project.playtests) if select and e.get("id") == select),
                         len(project.playtests) - 1)
            self.entries.setCurrentRow(index)

    @staticmethod
    def _title(entry):
        note = f" · „{entry['beschreibung']}“" if entry.get("beschreibung") else ""
        game = tr(" · in game") if entry.get("injiziert") else ""
        return tr("{datum} · {name}{note} · build: {bau}{game} · playtest: {spieltest}", datum=entry['datum'],
                  name=entry.get('name', ''), note=note, bau=tr(str(entry.get('bau_status'))), game=game,
                  spieltest=tr(entry.get('status', 'offen')))

    def select(self, index):
        if index >= 0:
            entry = self.project.playtests[index]
            self.package.setText((entry.get("paket") or tr("No finished package in this build."))
                                 + (tr("\nWhat's new: {beschreibung}", beschreibung=entry['beschreibung']) if entry.get("beschreibung") else ""))
            found = self.status.findData(entry.get("status", "offen"))
            if found >= 0:
                self.status.setCurrentIndex(found)
            else:
                self.status.setCurrentText(entry.get("status", "offen"))
            self.text.setPlainText(entry.get("ergebnis", ""))

    def save(self):
        index = self.entries.currentRow()
        if index < 0:
            return
        entry = self.project.playtests[index]
        before = copy.deepcopy(entry)
        entry.update(status=self.status.currentData() or self.status.currentText(), ergebnis=self.text.toPlainText(),
                     spieltest_datum=datetime.now().astimezone().isoformat(timespec="seconds"))
        try:
            self.project.save_playtests()
            self.message.setText(tr("Playtest saved."))
            self.entries.item(index).setText(self._title(entry))
        except (OSError, ValueError) as error:
            entry.clear()
            entry.update(before)
            self.message.setText(str(error))
