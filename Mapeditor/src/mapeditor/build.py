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

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .adapter import (BUILD_PVS, TRACK_CODES, BuildJob, ProcessJob, build_command, swofty_auswerten,
                      swofty_installieren, swofty_installierte, swofty_programm, swofty_starten, check_key, default_carrier,
                      game_status_command, injector_command, parse_game_status, toolkit_path, worker_command)
from .i18n import N_, meldung, tr
from .paths import writable_path
from .ux import zahl

#: Werte im Spieltest-Protokoll (gespeichert deutsch, angezeigt übersetzt)
PLAYTEST_STATES = [N_("offen"), N_("ok"), N_("Absturz"), N_("Problem")]
BUILD_STATES = [N_("läuft"), N_("abgebrochen"), N_("fehlgeschlagen"), N_("mit_traegerabweichung")]

#: Bau erfolgreich (Paket geprüft)
GOOD_BUILDS = {"ok", "mit_traegerabweichung"}


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
        self.setWindowModality(Qt.WindowModal)
        self.resize(900, 790)
        layout = QVBoxLayout(self)
        title = QLabel(tr("BUILD / PACKAGE   ·   recommended profile"))
        layout.addWidget(title)
        self.profile_hint = hint = QLabel()
        hint.setWordWrap(True)
        layout.addWidget(hint)
        settings = self.project.manifest.get("bauen", {})
        base = self.project.folder or (Path.home() / "Documents" / "Mapeditor")
        self.fields = QWidget()
        form = self.main_form = QFormLayout(self.fields)
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
        for label, field, file in ((tr("Working folder"), self.output, False), (tr("Package folder"), self.packages, False),
                                   (tr("Carrier package (ZIP)"), self.carrier, True)):
            row = QHBoxLayout()
            row.addWidget(field)
            button = QPushButton(tr("Choose …"))
            button.clicked.connect(lambda checked=False, field=field, file=file: self.choose(field, file))
            row.addWidget(button)
            form.addRow(label, row)
        from PySide6.QtWidgets import QCheckBox
        self.ki_profil = QCheckBox(tr(("Adapt AI driving data to the track (braking points of the driving assist and "
                                       "AI pace)")))
        self.ki_profil.setChecked(bool(settings.get("ki_profil", True)))
        self.ki_profil.setToolTip(tr("Generates the AI time/speed curve (VPTimes) from the track's own profile instead "
                                     "of taking over Portimão's data. Without this option braking zones have no "
                                     "relation to the corners (25.10.2026). New, not yet confirmed in game."))
        form.addRow("", self.ki_profil)
        self.race_data = QCheckBox(tr("Work out race data from the track (laps, fuel, tyres, AI, pit lane)"))
        self.race_data.setChecked(bool(settings.get("renndaten", True)))
        self.race_data.setToolTip(tr("Race distance and fuel follow the rules of Portimão mod 1.0.2, AI times come "
                                     "from the estimated lap time, plus the pit lane speed limit and the time a pit "
                                     "stop costs, and the minimum lap time for leaderboards. The tyre choice is an "
                                     "estimate."))
        form.addRow("", self.race_data)
        self.target = QComboBox()
        self.target.addItem(tr("Track Installer (EGO Editor)"), "ego")
        self.target.addItem(tr("Swofty Launcher"), "swofty")
        self.target.setCurrentIndex(max(0, self.target.findData(settings.get("ziel", "ego"))))
        self.target.setToolTip(tr("Swofty Launcher: an addon with its own track code and its own name in the menus, "
                                  "for as many tracks as you like. Starts only from the launcher, offline and without "
                                  "EA AntiCheat."))
        form.addRow(tr("Build for"), self.target)
        self.shared_carrier = QCheckBox(tr("Use a shared carrier (0.45 instead of 3 GB per track)"))
        self.shared_carrier.setChecked(bool(settings.get("traeger_teilen", True)))
        self.shared_carrier.setToolTip(tr("The Portimão mod's textures sit once in a carrier addon next to the "
                                          "packages, and every track only points to it. The launcher needs the carrier "
                                          "addon as well."))
        form.addRow("", self.shared_carrier)
        self.grid_size = QComboBox()
        self.grid_size.addItem(tr("24 cars (default)"), 0)
        self.grid_size.addItem(tr("26 cars"), 26)
        self.grid_size.setCurrentIndex(max(0, self.grid_size.findData(settings.get("raster", 0))))
        self.grid_size.setToolTip(tr("Adds grid slots, garages and pit boxes for 26 cars. For 26 cars to actually "
                                     "race, the game also needs an addon such as Custom Grid Size."))
        form.addRow(tr("Grid"), self.grid_size)
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
        form.addRow(self.code_label, self.code)
        self.code_info = QLabel()
        self.code_info.setWordWrap(True)
        self.code_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow("", self.code_info)
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
        form.addRow("", self.advanced_toggle)
        form.addRow("", self.advanced)
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
        self.phase = QLabel(tr("Ready · progress shows phase boundaries."))
        self.phase.setWordWrap(True)
        layout.addWidget(self.phase)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setFormat(tr("%p % · phase progress"))
        layout.addWidget(self.progress)
        self.issue_list = QListWidget()
        self.issue_list.setWordWrap(True)
        self.issue_list.itemClicked.connect(self.jump_message)
        layout.addWidget(self.issue_list, 1)
        self.result_text = QPlainTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setMaximumHeight(130)
        self.result_text.setPlaceholderText(tr("After the build: package path, size, SHA-256 and check report."))
        layout.addWidget(self.result_text)
        self.log_toggle = QPushButton(tr("Show technical log"))
        self.log_toggle.setCheckable(True)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(3000)
        self.log.setMaximumHeight(150)
        self.log.hide()
        self.log_toggle.toggled.connect(self.log.setVisible)
        layout.addWidget(self.log_toggle)
        layout.addWidget(self.log)
        install = QLabel(tr("First installation of a track code: install the ZIP once with the Track Installer and "
                            "activate it in the EGO editor. After that, every new version with “Transfer to game” – "
                            "without the EGO editor, even with the mod deactivated (backs up the affected game files, "
                            "writes to the game after your confirmation; quit the game first)."))
        install.setWordWrap(True)
        layout.addWidget(install)
        buttons = QHBoxLayout()
        self.start_button = QPushButton(tr("Check and build"))
        self.start_button.clicked.connect(self.start_build)
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
        self.close_button.clicked.connect(self.close)
        for button in (self.start_button, self.cancel_button, self.inject_button, self.install_button, self.play_button,
                       self.diagnosis_button, self.explorer_button, self.report_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
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
        self._update_code_info()
        self.refresh_game_status()

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
        row = target.getWidgetPosition(self.target)[0] + 1 if self.swofty() else 0
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
        self.code_info.setText((hint + "\n" + state).strip())
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
        if not hasattr(self, "pvs"):
            return
        o = self.options()
        self.profile_hint.setText(
            tr("Track code {streckencode} · PVS {pvs} · name “{name}” · pit correction · one main variant.\nBefore the "
               "build the project is saved and checked (same state: result of the last check). The build creates a "
               "snapshot; you can keep working during the build.",
               streckencode=tr("from the name") if self.swofty() else o['streckencode'],
               pvs=tr("along the track") if o['pvs_variante'] == 'strecke' else tr("Original"),
               name=o['streckenname'] or self.project.raw.get('name') or tr("Test track")))

    def update_space(self):
        try:
            output, packages = build_folders(self.project, self.output.text(), self.packages.text())
            self.space.setText(space_text(output, packages))
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
        self.start_button.setEnabled(not active)
        self.cancel_button.setEnabled(active and getattr(self, "inject_job", None) is None)
        self.close_button.setEnabled(not active)
        self.inject_button.setEnabled(not active and self.inject_record is not None)
        for button in (self.install_button, self.play_button):
            button.setEnabled(not active and self.launcher_job is None and self.launcher_record is not None)
        self.cancel_button.setText(tr("Cancel"))
        self._update_buttons()

    def _update_buttons(self):
        """Die Leiste zeigt nur, was gerade geht: Abbrechen beim Bau, das Weitergeben zum Bauziel, Ergebnisse nach dem Bau."""
        if not hasattr(self, "launcher_job"):
            return
        swofty = self.swofty()
        self.cancel_button.setVisible(self.cancel_button.isEnabled() or self.job is not None)
        self.inject_button.setVisible(self.inject_record is not None and not swofty)
        self.install_button.setVisible(self.launcher_record is not None and swofty)
        self.play_button.setVisible(self.launcher_record is not None and swofty)
        self.diagnosis_button.setVisible(swofty)
        self.explorer_button.setVisible(self.explorer_button.isEnabled())
        self.report_button.setVisible(self.report_button.isEnabled())

    def start_build(self):
        if self.job:
            return
        try:
            if self.project.readonly or not self.project.closed:
                raise ValueError(tr("A build needs a closed lap and a known project version."))
            if self.main.properties.pending():
                raise ValueError(tr("Correct the invalid field input first."))
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
            self.log.clear()
            self.result_text.clear()
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
                self.busy(False)
                return
            response = json.loads(text)
            if code or "error" in response:
                raise ValueError(response.get("error", text))
            if hasattr(self.main, "store_analysis") and "analysis" in response:
                self.main.store_analysis(self.check_key, response)
        except (ValueError, KeyError, OSError) as error:
            self.phase.setText(meldung(error))
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
        self.phase.setText(tr("Build starting · the saved project is taken over as a snapshot"))
        self.job.start(self.command)

    def add_message(self, message):
        from .adapter import issue_from_message
        issue = issue_from_message(message)
        self.messages.append(issue)
        self.issue_list.addItem(f"{tr(issue['level'])} · {issue.get('kurz', issue['id'])} · {meldung(issue['text'])}")
        self.issue_list.item(self.issue_list.count() - 1).setData(Qt.UserRole, issue)
        self.main.add_build_issue(issue, self.record["id"] if self.record else tr("Pre-check"))

    def jump_message(self, item):
        self.main.jump_issue(item)

    def on_event(self, event):
        typ = event["typ"]
        if typ == "start":
            entry = event.get("eingabe", {})
            self.record["eingabe"] = entry
            self.log.appendPlainText(tr("Snapshot: ") + json.dumps(entry, ensure_ascii=False))
        elif typ == "phase":
            self.phase.setText(meldung(event.get("titel", event.get("phase", tr("Build run")))))
            self.progress.setValue(round(float(event.get("fortschritt", 0)) * 1000))
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
            packages = terminal["pakete"]
            self.record.update(pakete=packages, paket=packages[0]["pfad"], bau_status=terminal["status"],
                               pruefbericht=terminal.get("pruefbericht"), bericht_md=terminal.get("bericht_md"))
            lines = [label]
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
            self.result_text.setPlainText(tr("{status} · exit code {code}\n{reason}", status=tr(status).capitalize(), code=code, reason=reason))
            self.record.update(bau_status=status, bau_fehler=reason)
        self.record["exitcode"] = code
        try:
            self.project.save_playtests()
        except (ValueError, OSError) as error:
            self.phase.setText(self.phase.text() + tr(" · log not saved: {error}", error=error))
        self.busy(False)
        job.deleteLater()
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
        settings = self.project.manifest.get("bauen", {})
        program = swofty_programm(settings.get("swofty_programm"))
        if program is None:
            value, _ = QFileDialog.getOpenFileName(self, tr("Choose swofty.exe (the launcher's bin folder)"), "", "swofty.exe")
            if not value:
                return None
            program = Path(value)
            self.main.document.edit(tr("Change build settings"),
                                    lambda raw, meta: meta.setdefault("bauen", {}).update(swofty_programm=str(program)))
            self.main.save_project()
        return program

    def launcher_start(self, play):
        program = self.launcher_program()
        if program is None or self.launcher_record is None:
            return
        try:
            carrier = next((p.get("traeger") for p in self.launcher_record.get("pakete") or [] if p.get("traeger")), None)
            if carrier and not Path(carrier).is_file():
                raise ValueError(tr("Carrier addon missing: {pfad}", pfad=carrier))
            steps = swofty_installieren(program, self.launcher_record["paket"], carrier,
                                        swofty_installierte(program) if carrier else ())
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

    def reject(self):
        if not self.job and not self.inject_job and not self.launcher_job:
            super().reject()

    def closeEvent(self, event):
        if self.job or self.inject_job or self.launcher_job:
            event.ignore()
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
