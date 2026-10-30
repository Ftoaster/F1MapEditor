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

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .adapter import (BUILD_PVS, TRACK_CODES, BuildJob, ProcessJob, build_command, check_key, default_carrier,
                      game_status_command, injector_command, parse_game_status, toolkit_path, worker_command)
from .i18n import N_, tr
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
        text += "  ·  " + tr("im Spiel")
    return text


def code_status(code, state):
    """Was „Ins Spiel übertragen“ für diesen Code tun kann (aus ``injektor_tool.py datenbank --json``)."""
    if state is None:
        return tr("Zustand im Spiel unbekannt (Werkzeugkasten oder Spielordner nicht lesbar).")
    inst = (state.get("installiert") or {}).get(code)
    if not inst:
        return tr("{code} ist noch nicht im Spiel: das Paket einmalig im Track Installer installieren und im EGO-Editor "
                  "aktivieren („Activate mod“). Danach überträgt der Mapeditor jede neue Version selbst – auch bei "
                  "deaktiviertem Mod.", code=code)
    where = tr("{code} ist installiert ({slot}, zuletzt {paket}).", code=code, slot=inst.get("slot") or "?",
               paket=inst.get("paket") or "?")
    if code in (state.get("aktiv") or []):
        db = tr("Datenbank aktiv · „Ins Spiel übertragen“ ersetzt die Strecke direkt.")
    elif state.get("zustand") == "aktiv" and not state.get("aktiv"):
        db = tr("Datenbank aktiviert · „Ins Spiel übertragen“ ersetzt die Strecke direkt.")
    elif state.get("zustand") == "aktiv":
        db = tr("In der aktiven Datenbank ist {code} nicht eingeschaltet (EGO-Editor: Database activation).", code=code)
    elif code in (state.get("gemerkt") or []):
        db = tr("Mod deaktiviert · die Übertragung aktiviert die Datenbank selbst wieder (gemerkte Aktivierung).")
    else:
        db = tr("Datenbank nicht aktiviert und noch nicht gemerkt: einmal im EGO-Editor „Activate mod“ klicken – danach "
                "geht es ohne den EGO-Editor.")
    note = tr(" Spiel läuft – vor dem Übertragen beenden.") if state.get("spiel_laeuft") else ""
    return f"{where} {db}{note}"


def nearest_existing(path):
    path = Path(path)
    while not path.exists():
        if path.parent == path:
            raise ValueError(tr("Datenträger oder Ordner nicht erreichbar: {path}", path=path))
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
            raise ValueError(tr("Zu wenig freier Speicher auf {anchor}: {free:.1f} GB frei, {required:.1f} GB benötigt.", anchor=path.anchor, free=free / 1000000000.0, required=required / 1000000000.0))


def space_text(work, packages):
    """„Bedarf etwa 4,0 GB · frei 130 GB auf G:“ (je Datenträger)."""
    try:
        parts = []
        for path, required in space_needs(work, packages):
            free = shutil.disk_usage(path).free
            drive = path.anchor.rstrip("\\/") or str(path)
            parts.append(tr("Bedarf etwa {required} · frei {free} auf {drive}", required=zahl(required / 1000000000.0, 1, 'GB'), free=zahl(free / 1000000000.0, 0 if free >= 10000000000.0 else 1, 'GB'), drive=drive) + (tr(" – zu wenig!") if free < required else ""))
        return " · ".join(parts)
    except (OSError, ValueError) as error:
        return tr("Speicher nicht prüfbar: {error}", error=error)


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
        self.setWindowTitle(tr("Bauen / Paket"))
        self.setWindowModality(Qt.WindowModal)
        self.resize(900, 790)
        layout = QVBoxLayout(self)
        title = QLabel(tr("BAUEN / PAKET   ·   Profil empfohlen"))
        layout.addWidget(title)
        self.profile_hint = hint = QLabel()
        hint.setWordWrap(True)
        layout.addWidget(hint)
        settings = self.project.manifest.get("bauen", {})
        base = self.project.folder or (Path.home() / "Documents" / "Mapeditor")
        self.fields = QWidget()
        form = QFormLayout(self.fields)
        self.output = QLineEdit(settings.get("ausgabe", str(base / "bauten")))
        self.packages = QLineEdit(settings.get("pakete", str(base / "pakete")))
        try:
            suggestion = next_build_name(self.project, *build_folders(self.project, self.output.text(), self.packages.text()))
        except (OSError, ValueError):
            suggestion = "bau_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        self.name = QLineEdit(suggestion)
        self.name.setToolTip(tr("Fortlaufend vorgeschlagen (letzter Bau + 1); eine Beschreibung mit Unterstrich anhängen, "
                             "z. B. „_tafeln50“."))
        form.addRow(tr("Bauname"), self.name)
        self.note = QLineEdit()
        self.note.setPlaceholderText(tr("Was ist neu? (z. B. „Turn 11 Scheitel, 50-m-Tafeln“) – steht im Spieltest-Protokoll"))
        form.addRow(tr("Was ist neu?"), self.note)
        try:
            root = toolkit_path(self.project.manifest.get("werkzeugkasten"))
            carrier = str(default_carrier(root))
        except ValueError:
            carrier = ""
        self.carrier = QLineEdit(settings.get("traeger", carrier))
        for label, field, file in ((tr("Arbeitsordner"), self.output, False), (tr("Paketordner"), self.packages, False),
                                   (tr("Trägerpaket (ZIP)"), self.carrier, True)):
            row = QHBoxLayout()
            row.addWidget(field)
            button = QPushButton(tr("Wählen …"))
            button.clicked.connect(lambda checked=False, field=field, file=file: self.choose(field, file))
            row.addWidget(button)
            form.addRow(label, row)
        from PySide6.QtWidgets import QCheckBox
        self.ki_profil = QCheckBox(tr("KI-Fahrdaten an die Strecke anpassen (Bremspunkte der Fahrhilfe und KI-Tempo)"))
        self.ki_profil.setChecked(bool(settings.get("ki_profil", True)))
        self.ki_profil.setToolTip(tr("Erzeugt die Zeit-/Geschwindigkeitskurve der KI (VPTimes) aus dem eigenen Profil statt "
                                  "Portimãos Daten zu übernehmen. Ohne diese Option liegen Bremszonen ohne Bezug zu "
                                  "den Kurven (25.10.2026). Neu, noch nicht im Spiel bestätigt."))
        form.addRow("", self.ki_profil)
        # Streckencode: alle Codes, die die Spieldatenbank kennt; Zustand im Spiel darunter
        self.code = QComboBox()
        self.code.setMaxVisibleItems(len(TRACK_CODES))
        self.code.setToolTip(tr("Spielcode, unter dem die Strecke installiert wird. Jeder Code ist ein eigener Platz im "
                             "Spiel: mehrere eigene Strecken nebeneinander brauchen verschiedene Codes "
                             "(docs/streckencode.md)."))
        self.game_state = None
        self.status_read = False
        self.status_job = None
        self._fill_codes()
        self.code.setCurrentIndex(max(0, self.code.findData(settings.get("streckencode", "sochi"))))
        form.addRow(tr("Streckencode"), self.code)
        self.code_info = QLabel()
        self.code_info.setWordWrap(True)
        self.code_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow("", self.code_info)
        self.code.currentIndexChanged.connect(self._update_code_info)
        # Erweiterte Bauoptionen (überschreiben das Profil); eingeklappt
        self.advanced_toggle = QPushButton(tr("Erweiterte Bauoptionen ▸"))
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setFlat(True)
        self.advanced_toggle.setStyleSheet("text-align: left;")
        self.advanced = QWidget()
        advanced = QFormLayout(self.advanced)
        advanced.setContentsMargins(0, 0, 0, 0)
        self.track_name = QLineEdit(settings.get("streckenname", ""))
        self.track_name.setPlaceholderText(tr("aus der Definition: {name}", name=self.project.raw.get('name') or tr("Teststrecke")))
        self.pvs = QComboBox()
        self.pvs.addItem(tr("entlang der Strecke (empfohlen)"), "strecke")
        self.pvs.addItem(tr("Original-Zellen sehen alles"), "alles")
        self.pvs.setCurrentIndex(max(0, self.pvs.findData(settings.get("pvs_variante", "strecke"))))
        self.world_maps = QCheckBox(tr("Eigene Boden- und Umlandkarten"))
        self.world_maps.setToolTip(tr("Ersetzt Portimãos Luftbilder: Boden aus den Flächen der Strecke, Ideallinie auf dem "
                                      "Asphalt, Umland passend zu Hügeln und Bergen."))
        self.world_maps.setChecked(bool(settings.get("weltkarten", True)))
        self.biome = QComboBox()
        self.biome.addItem(tr("Gras (Standard)"), "standard")
        self.biome.addItem(tr("Wüste"), "wueste")
        self.biome.setCurrentIndex(max(0, self.biome.findData(settings.get("biom", "standard"))))
        self.tint = QComboBox()
        self.tint.addItem(tr("Standard"), "standard")
        self.tint.addItem(tr("Trocken"), "trocken")
        self.tint.addItem(tr("Saftig"), "saftig")
        self.tint.addItem(tr("Herbst"), "herbst")
        self.tint.setCurrentIndex(max(0, self.tint.findData(settings.get("farbton", "standard"))))
        self.world_maps.toggled.connect(self.biome.setEnabled)
        self.world_maps.toggled.connect(self.tint.setEnabled)
        self.biome.setEnabled(self.world_maps.isChecked())
        self.tint.setEnabled(self.world_maps.isChecked())
        advanced.addRow(tr("Streckenname"), self.track_name)
        advanced.addRow(tr("Sichtbarkeit (PVS)"), self.pvs)
        advanced.addRow("", self.world_maps)
        advanced.addRow(tr("Landschaft"), self.biome)
        advanced.addRow(tr("Farbton"), self.tint)
        fixed = QLabel(tr("Fest im Profil: Boxengassen-Korrektur, Aktivierungsvorlage portimao, nur Hauptvariante."))
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
        self.phase = QLabel(tr("Bereit · Fortschritt zeigt Phasengrenzen."))
        self.phase.setWordWrap(True)
        layout.addWidget(self.phase)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setFormat(tr("%p % · Phasenfortschritt"))
        layout.addWidget(self.progress)
        self.issue_list = QListWidget()
        self.issue_list.setWordWrap(True)
        self.issue_list.itemClicked.connect(self.jump_message)
        layout.addWidget(self.issue_list, 1)
        self.result_text = QPlainTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setMaximumHeight(130)
        self.result_text.setPlaceholderText(tr("Nach dem Bau: Paketpfad, Größe, SHA-256 und Prüfbericht."))
        layout.addWidget(self.result_text)
        self.log_toggle = QPushButton(tr("Technisches Protokoll anzeigen"))
        self.log_toggle.setCheckable(True)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(3000)
        self.log.setMaximumHeight(150)
        self.log.hide()
        self.log_toggle.toggled.connect(self.log.setVisible)
        layout.addWidget(self.log_toggle)
        layout.addWidget(self.log)
        install = QLabel(tr("Erstinstallation eines Streckencodes: ZIP einmalig im Track Installer installieren und im "
                         "EGO-Editor aktivieren. Danach jede neue Version mit „Ins Spiel übertragen“ – ohne EGO-Editor, "
                         "auch bei deaktiviertem Mod (sichert die betroffenen Spieldateien, schreibt nach deiner "
                         "Bestätigung ins Spiel; Spiel vorher beenden)."))
        install.setWordWrap(True)
        layout.addWidget(install)
        buttons = QHBoxLayout()
        self.start_button = QPushButton(tr("Prüfen und bauen"))
        self.start_button.clicked.connect(self.start_build)
        self.cancel_button = QPushButton(tr("Abbrechen"))
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_build)
        self.explorer_button = QPushButton(tr("Im Explorer zeigen"))
        self.explorer_button.setEnabled(False)
        self.explorer_button.clicked.connect(self.show_package)
        self.report_button = QPushButton(tr("Prüfbericht öffnen"))
        self.report_button.setEnabled(False)
        self.report_button.clicked.connect(self.show_report)
        self.inject_button = QPushButton(tr("Ins Spiel übertragen …"))
        self.inject_button.setEnabled(False)
        self.inject_button.clicked.connect(self.inject_start)
        self.cleanup_button = QPushButton(tr("Aufräumen …"))
        self.cleanup_button.setToolTip(tr("Alte Bauten und Pakete mit Größe und Spieltest-Ergebnis anzeigen und löschen"))
        self.cleanup_button.clicked.connect(self.open_cleanup)
        self.close_button = QPushButton(tr("Schließen"))
        self.close_button.clicked.connect(self.close)
        for button in (self.start_button, self.cancel_button, self.inject_button, self.explorer_button, self.report_button,
                       self.cleanup_button, self.close_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        # Schnellaustausch: zuletzt erfolgreich gebautes Paket des Projekts (auch nach erneutem Öffnen des Dialogs)
        self.inject_record = None
        self.inject_job = None
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

    def _update_code_info(self, *args):
        code = self.code.currentData()
        hint = next((tr(h) for c, _, h in TRACK_CODES if c == code), "")
        if self.status_job is not None:
            state = tr("Zustand im Spiel wird gelesen …")
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
        self.advanced_toggle.setText(tr("Erweiterte Bauoptionen ▾") if on else tr("Erweiterte Bauoptionen ▸"))

    def options(self):
        return {"streckencode": self.code.currentData(), "streckenname": self.track_name.text().strip(),
                "pvs_variante": self.pvs.currentData(), "weltkarten": self.world_maps.isChecked(),
                "biom": self.biome.currentData(), "farbton": self.tint.currentData()}

    def _update_profile_hint(self, *args):
        if not hasattr(self, "pvs"):
            return
        o = self.options()
        self.profile_hint.setText(
            tr("Streckencode {streckencode} · PVS {pvs} · Name „{name}“ · Boxenkorrektur · eine Hauptvariante.\n"
               "Vor dem Bau wird das Projekt gespeichert und geprüft (gleicher Stand: Ergebnis der letzten Prüfung). "
               "Der Bau erzeugt einen Schnappschuss; während des Baus kann weitergearbeitet werden.",
               streckencode=o['streckencode'],
               pvs=tr("entlang der Strecke") if o['pvs_variante'] == 'strecke' else tr("Original"),
               name=o['streckenname'] or self.project.raw.get('name') or tr("Teststrecke")))

    def update_space(self):
        try:
            output, packages = build_folders(self.project, self.output.text(), self.packages.text())
            self.space.setText(space_text(output, packages))
        except ValueError as error:
            self.space.setText(str(error))

    def choose(self, field, file):
        if file:
            value, _ = QFileDialog.getOpenFileName(self, tr("Trägerpaket wählen"), field.text(), tr("ZIP (*.zip)"))
        else:
            value = QFileDialog.getExistingDirectory(self, tr("Eigenen Ausgabeordner wählen"), field.text())
        if value:
            field.setText(value)

    def busy(self, active):
        self.fields.setEnabled(not active)
        self.start_button.setEnabled(not active)
        self.cancel_button.setEnabled(active and getattr(self, "inject_job", None) is None)
        self.close_button.setEnabled(not active)
        self.cleanup_button.setEnabled(not active)
        self.inject_button.setEnabled(not active and self.inject_record is not None)
        self.cancel_button.setText(tr("Abbrechen"))

    def start_build(self):
        if self.job:
            return
        try:
            if self.project.readonly or not self.project.closed:
                raise ValueError(tr("Bau braucht eine geschlossene Runde und eine bekannte Projektfassung."))
            if self.main.properties.pending():
                raise ValueError(tr("Ungültige Feldeingabe zuerst korrigieren."))
            if not all(f.text().strip() for f in (self.name, self.output, self.packages, self.carrier)):
                raise ValueError(tr("Bitte Bauname, beide Ausgabeordner und Trägerpaket angeben."))
            root = toolkit_path(self.project.manifest.get("werkzeugkasten"))
            output, packages = writable_path(self.output.text()), writable_path(self.packages.text())
            carrier = Path(self.carrier.text()).expanduser().resolve()
            if not carrier.is_file():
                raise ValueError(tr("Trägerpaket fehlt: {carrier}", carrier=carrier))
            # Zielschutz und Namen vor dem Speichern prüfen; Befehl danach neu bilden.
            build_command(root, self.project.basis, output, packages, self.name.text(), carrier=carrier,
                          options=self.options())
            check_space(output, packages)
            settings = {"ausgabe": str(output), "pakete": str(packages), "traeger": str(carrier),
                        "ki_profil": self.ki_profil.isChecked(), **self.options()}
            self.main.document.edit(tr("Baueinstellungen ändern"), lambda raw, meta: meta.update(bauen=settings))
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
            self.progress.setValue(0)
            shift = self.project.manifest.get("versatz", [0, 0, 0])
            self.check_key = check_key(self.project.raw, self.project.folder, shift)
            cached = getattr(self.main, "check_cache", {}).get(self.check_key)
            if cached is not None:
                self.phase.setText(tr("Vorabprüfung: Ergebnis der letzten Prüfung dieses Stands wird verwendet"))
                self.log.appendPlainText(tr("Vorabprüfung aus dem Zwischenspeicher (gleiche Definition, Basis und Versatz)."))
                self.use_check(cached)
                return
            self.phase.setText(tr("Vorabprüfung mit Generator …"))
            self.job = ProcessJob(self)
            self.job.done.connect(self.checked)
            self.job.start(worker_command(), {"task": "analyse", "raw": copy.deepcopy(self.project.raw),
                                              "basis": str(self.project.folder), "toolkit": str(root), "shift": shift})
        except (ValueError, OSError) as error:
            self.phase.setText(str(error))

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
                self.phase.setText(tr("Vorabprüfung abgebrochen · kein Bau gestartet"))
                self.busy(False)
                return
            response = json.loads(text)
            if code or "error" in response:
                raise ValueError(response.get("error", text))
            if hasattr(self.main, "store_analysis") and "analysis" in response:
                self.main.store_analysis(self.check_key, response)
        except (ValueError, KeyError, OSError) as error:
            self.phase.setText(str(error))
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
                raise ValueError(tr("Vorabprüfung: Fehler beheben, bevor ein Fahrpaket gebaut werden kann."))
            self.launch()
        except (ValueError, KeyError, OSError) as error:
            self.phase.setText(str(error))
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
        self.phase.setText(tr("Bau startet · gespeichertes Projekt wird als Schnappschuss übernommen"))
        self.job.start(self.command)

    def add_message(self, message):
        from .adapter import issue_from_message
        issue = issue_from_message(message)
        self.messages.append(issue)
        self.issue_list.addItem(f"{issue['level']} · {issue.get('kurz', issue['id'])} · {issue['text']}")
        self.issue_list.item(self.issue_list.count() - 1).setData(Qt.UserRole, issue)
        self.main.add_build_issue(issue, self.record["id"] if self.record else tr("Vorabprüfung"))

    def jump_message(self, item):
        self.main.jump_issue(item)

    def on_event(self, event):
        typ = event["typ"]
        if typ == "start":
            entry = event.get("eingabe", {})
            self.record["eingabe"] = entry
            self.log.appendPlainText(tr("Schnappschuss: ") + json.dumps(entry, ensure_ascii=False))
        elif typ == "phase":
            self.phase.setText(event.get("titel", event.get("phase", "Bau")))
            self.progress.setValue(round(float(event.get("fortschritt", 0)) * 1000))
        elif typ == "meldung":
            self.add_message(event["meldung"])
        elif typ == "log":
            self.log.appendPlainText(event.get("text", ""))
        elif typ == "pruefung":
            self.log.appendPlainText(tr("Prüfung {stufe}: {zusammenfassung}", stufe=tr(str(event.get('stufe'))), zusammenfassung=event.get('zusammenfassung')))
        elif typ == "ergebnis":
            self.result = event
        elif typ in ("fehler", "abbruch"):
            self.phase.setText(event.get("text", typ))
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
            label = tr("Paket geprüft") if terminal["status"] == "ok" else tr("Mit Trägerabweichung geprüft")
            self.phase.setText(label + tr(" · Spieltest offen"))
            self.progress.setValue(1000)
            packages = terminal["pakete"]
            self.record.update(pakete=packages, paket=packages[0]["pfad"], bau_status=terminal["status"],
                               pruefbericht=terminal.get("pruefbericht"), bericht_md=terminal.get("bericht_md"))
            lines = [label]
            for package in packages:
                lines += [package["pfad"], tr("{groesse:.3f} GB · SHA-256: {sha256}", groesse=package.get('groesse', 0) / 1e9,
                                                    sha256=package.get('sha256', tr("nicht vorhanden")))]
            lines += [tr("Prüfbericht: ") + str(terminal.get("pruefbericht", "")), tr("Bericht: ") + str(terminal.get("bericht_md", ""))]
            self.result_text.setPlainText("\n".join(lines))
            self.explorer_button.setEnabled(True)
            self.report_button.setEnabled(bool(terminal.get("pruefbericht")))
            self.set_inject_target(self.record)
        else:
            self.result = None
            status = "abgebrochen" if code in (3, -2) else "fehlgeschlagen"
            reason = job.protocol_error or (terminal or {}).get("text") or {
                2: tr("Prüffehler · Paket verworfen"), -2: tr("Zeitlimit beim Abbruch · Prozessbaum beendet; temporäre Pakete entfernt"),
                3: tr("Bau abgebrochen"), -1: text,
            }.get(code, "Bau fehlgeschlagen oder vollständiges Ergebnis fehlt")
            self.phase.setText(reason)
            self.result_text.setPlainText(tr("{status} · Exitcode {code}\n{reason}", status=tr(status).capitalize(), code=code, reason=reason))
            self.record.update(bau_status=status, bau_fehler=reason)
        self.record["exitcode"] = code
        try:
            self.project.save_playtests()
        except (ValueError, OSError) as error:
            self.phase.setText(self.phase.text() + tr(" · Protokoll nicht gespeichert: {error}", error=error))
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
        self.inject_record = record
        name = Path(record["paket"]).name
        self.inject_button.setEnabled(self.job is None)
        self.inject_button.setToolTip(tr("{name} per Schnellaustausch in F1 25 schreiben: erst Plan anzeigen, dann nach deiner Bestätigung schreiben (vorher Sicherung der betroffenen Spieldateien).", name=name))

    def _run_injector(self, action, finished):
        try:
            root = toolkit_path(self.project.manifest.get("werkzeugkasten"))
            output, _ = build_folders(self.project, self.output.text(), self.packages.text())
            command = self.injector(root, action, self.inject_record["paket"], backup_folder(output))
        except (ValueError, OSError) as error:
            self.phase.setText(tr("Schnellaustausch nicht möglich: {error}", error=error))
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
        self.phase.setText(tr("Schnellaustausch: Plan für {name} wird erstellt …", name=Path(self.inject_record['paket']).name))
        self._run_injector("plan", self.plan_done)

    def _finish_injector(self):
        job, self.inject_job = self.inject_job, None
        if job:
            job.deleteLater()
        self.busy(False)

    def plan_done(self, code, text):
        self._finish_injector()
        if code != 0:
            self.phase.setText(tr("Schnellaustausch nicht möglich: ") + _tool_error(text, code))
            return
        if "Nichts zu tun" in text:
            self.phase.setText(tr("Dieses Paket ist bereits im Spiel · nichts übertragen."))
            return
        if not self.confirm_injection(text):
            self.phase.setText(tr("Schnellaustausch abgebrochen · nichts geändert."))
            return
        self.phase.setText(tr("Schreibe ins Spiel … (Sicherung der betroffenen Dateien, dann Austausch)"))
        self._run_injector("injizieren", self.inject_done)

    def confirm_injection(self, plan_text):
        """Eigene Bestätigung je Übertragung (schreibt in den Spielordner)."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(tr("Ins Spiel übertragen?"))
        box.setText(tr("<b>{name}</b> jetzt in F1 25 schreiben?", name=Path(self.inject_record['paket']).name))
        box.setInformativeText(tr("Der Schnellaustausch ersetzt Strecken-ERP, Mipmaps, ERS- und Minimap-Werte des schon "
                               "installierten Streckencodes und aktiviert bei deaktiviertem Mod die Spieldatenbank aus "
                               "der gemerkten Aktivierung. Vorher werden die betroffenen Spieldateien gesichert; "
                               "das Spiel muss beendet sein. Details: „Details anzeigen“."))
        box.setDetailedText(plan_text)
        write = box.addButton(tr("Ins Spiel schreiben"), QMessageBox.AcceptRole)
        box.addButton(tr("Abbrechen"), QMessageBox.RejectRole)
        box.setDefaultButton(QMessageBox.Cancel)
        box.exec()
        return box.clickedButton() is write

    def inject_done(self, code, text):
        self._finish_injector()
        if code != 0:
            self.phase.setText(tr("Schnellaustausch fehlgeschlagen: ") + _tool_error(text, code))
            return
        record = self.inject_record
        record["injiziert"] = datetime.now().astimezone().isoformat(timespec="seconds")
        try:
            self.project.save_playtests()
        except (ValueError, OSError) as error:
            self.log.appendPlainText(tr("Protokoll nicht gespeichert: {error}", error=error))
        self.phase.setText(tr("{name} ist im Spiel · Spieltest eintragen", name=Path(record['paket']).name))
        self.refresh_game_status()
        self.open_playtest(record)

    def open_playtest(self, record):
        if hasattr(self.main, "open_playtests"):
            self.main.open_playtests(select=record.get("id"))

    def open_cleanup(self):
        try:
            output, packages = build_folders(self.project, self.output.text(), self.packages.text())
        except ValueError as error:
            self.phase.setText(str(error))
            return
        CleanupDialog(self.project, output, packages, self).exec()
        self.update_space()

    def cancel_build(self):
        if self.job:
            try:
                self.job.cancel()
                self.cancel_button.setEnabled(False)
                self.cancel_button.setText(tr("Wird abgebrochen …"))
                self.phase.setText(tr("Wird abgebrochen … nächste Phasengrenze, spätestens nach 120 s beenden."))
            except OSError as error:
                self.phase.setText(tr("Abbruchdatei konnte nicht angelegt werden: {error}", error=error))

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
        if not self.job and not self.inject_job:
            super().reject()

    def closeEvent(self, event):
        if self.job or self.inject_job:
            event.ignore()
        else:
            event.accept()


def _tool_error(text, code):
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    error = next((line for line in reversed(lines) if line.startswith("FEHLER")), None)
    return error or (lines[-1] if lines else tr("Exitcode {code}", code=code))


class CleanupDialog(QDialog):
    """Alte Bauten (Arbeitsordner) und Pakete mit Größe und Spieltest-Ergebnis; Löschen nach Bestätigung.

    Vorausgewählt ist alles außer dem jüngsten Bau, dem jüngsten Bau mit Spieltest „ok“ und dem zuletzt ins Spiel
    übertragenen Paket. Gelöscht wird nur direkt in den beiden Ordnern (Name des Baus, ``.zip``, ``.abbruch``)."""

    def __init__(self, project, output, packages, parent=None):
        super().__init__(parent)
        self.project = project
        self.output, self.packages = Path(output).resolve(), Path(packages).resolve()
        self.setWindowTitle(tr("Alte Bauten aufräumen"))
        self.resize(860, 520)
        layout = QVBoxLayout(self)
        info = QLabel(tr("Arbeitsordner: {output}\nPaketordner: {packages}\nVorausgewählt: alles außer dem jüngsten Bau, dem jüngsten Bau mit Spieltest „ok“ und dem zuletzt ins Spiel übertragenen Paket.", output=self.output, packages=self.packages))
        info.setWordWrap(True)
        layout.addWidget(info)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([tr("Löschen"), tr("Bau"), tr("Datum"), tr("Größe"), tr("Bau-Status"), tr("Spieltest")])
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
        self.delete_button = QPushButton(tr("Ausgewählte löschen …"))
        self.delete_button.clicked.connect(self.delete_selected)
        close = QPushButton(tr("Schließen"))
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
            status = tr(record.get("status", "–")) + (tr(" · im Spiel") if record.get("injiziert") else "")
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
        self.total.setText(tr("{anzahl} von {gesamt} ausgewählt · {groesse} von {total}", anzahl=len(chosen), gesamt=len(self.items),
                              groesse=zahl(sum(e['groesse'] for e in chosen) / 1e9, 1, 'GB'), total=zahl(total / 1e9, 1, 'GB')))
        self.delete_button.setEnabled(bool(chosen))

    def confirm(self, chosen):
        size = sum(e["groesse"] for e in chosen)
        answer = QMessageBox.warning(
            self, tr("Bauten löschen?"), tr("{anzahl} Bauten mit zusammen {size} endgültig löschen?\nDas lässt sich nicht rückgängig machen. Das Spieltest-Protokoll behält die Einträge (als gelöscht markiert).", anzahl=len(chosen), size=zahl(size / 1000000000.0, 1, 'GB')),
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
                    errors.append(tr("{target}: liegt nicht direkt im Arbeits- oder Paketordner", target=target))
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
            errors.append(tr("Spieltest-Protokoll: {error}", error=error))
        self.reload()
        if errors:
            QMessageBox.warning(self, tr("Nicht alles gelöscht"), "\n".join(errors[:12]))


class PlaytestDialog(QDialog):
    def __init__(self, project, parent=None, select=None):
        super().__init__(parent)
        self.project = project
        self.setWindowTitle(tr("Spieltest-Protokoll"))
        self.resize(850, 550)
        layout = QVBoxLayout(self)
        self.entries = QListWidget()
        for entry in project.playtests:
            self.entries.addItem(self._title(entry))
        layout.addWidget(self.entries)
        self.package = QLabel(tr("Noch kein Bau im Projekt."))
        self.package.setWordWrap(True)
        self.package.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.package)
        self.status = QComboBox()
        for state in PLAYTEST_STATES:
            self.status.addItem(tr(state), state)
        layout.addWidget(self.status)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText(tr("Ergebnis des Spieltests: Strecke, Slot, Beobachtung …"))
        layout.addWidget(self.text)
        self.message = QLabel()
        layout.addWidget(self.message)
        self.save_button = QPushButton(tr("Spieltest speichern"))
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
        game = tr(" · im Spiel") if entry.get("injiziert") else ""
        return tr("{datum} · {name}{note} · Bau: {bau}{game} · Spieltest: {spieltest}", datum=entry['datum'],
                  name=entry.get('name', ''), note=note, bau=tr(str(entry.get('bau_status'))), game=game,
                  spieltest=tr(entry.get('status', 'offen')))

    def select(self, index):
        if index >= 0:
            entry = self.project.playtests[index]
            self.package.setText((entry.get("paket") or tr("Kein fertiges Paket in diesem Bau."))
                                 + (tr("\nWas ist neu: {beschreibung}", beschreibung=entry['beschreibung']) if entry.get("beschreibung") else ""))
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
            self.message.setText(tr("Spieltest gespeichert."))
            self.entries.item(index).setText(self._title(entry))
        except (OSError, ValueError) as error:
            entry.clear()
            entry.update(before)
            self.message.setText(str(error))
