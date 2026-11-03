"""Deutsche Qt-Oberfläche entlang der Hauptansicht aus AP17."""

from __future__ import annotations

import bisect
import copy
import hashlib
import json
import math
import re
from pathlib import Path

from PySide6.QtCore import QDateTime, QEvent, QSize, Qt, QTimer, Slot, QItemSelection, QItemSelectionModel
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence, QPalette, QColor
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMenuBar, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QSplitter, QStatusBar, QTabWidget, QTableWidget, QTableWidgetItem, QToolBar,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QLineEdit,
)

from .adapter import (ProcessJob, check_key, env_cache_folder, import_template, suggest_track, toolkit_path,
                      worker_command)
from .appearance import ensure_fonts
from .document import Document
from .i18n import N_, SPRACHEN, meldung, rule_title, setze_sprache, sprache, tr, trn
from .input_geometry import input_geometry
from .project import Project, point_xyz, read_json, set_point
from .views import CrossSection, MapView, ProfileView, SpeedView
from .paths import writable_path
from . import ansicht, neigung, querschnitt, umgebung
from .neigung import BankPanel
from .querschnitt import CrossPanel
from .umgebung_ui import EnvironmentPanel
from .ux import (BOOL_WORDS, CHOICES, ChoiceDelegate, HelpDialog, Section, WelcomePanel, choice_text, choice_value, edit_text,
                 has_icons, icon, zahl)


LABELS = {
    "x": N_("X · m"), "y": N_("Höhe Y · m"), "z": N_("Z · m"), "hoehe": N_("Höhe · m"),
    "typ": N_("Segmentart"), "laenge": N_("Länge · m"), "radius": N_("Radius · m"), "winkel": N_("Winkel · °"),
    "richtung": N_("Kurvenrichtung"), "radius_anfang": N_("Anfangsradius · m"), "radius_ende": N_("Endradius · m"),
    "hoehe_ende": N_("Endhöhe · m"), "steigung_prozent": N_("Steigung · %"), "name": N_("Name"),
    "breite": N_("Gesamtbreite · m"), "breite_links": N_("Halbbreite links · m"), "breite_rechts": N_("Halbbreite rechts · m"),
    "asymmetrisch": N_("Asymmetrische Breite"),
    "querneigung_grad": N_("Querneigung · ° (+ = links höher)"), "uebergang_m": N_("Übergang · m"),
    "randlinie_breite": N_("Randlinie · m"), "abtastung_m": N_("Abtastung · m"), "glaettung_m": N_("Glättung · m"),
    "hoehen_glaettung_m": N_("Höhenglättung · m"), "schliessen": N_("Schließregel (verteilen/fehler)"),
    "max_schliessfehler_m": N_("Max. Schließfehler · m"), "umkehren": N_("Richtung umkehren"),
    "s": N_("Start/Ziel ab Eingabeursprung · m"), "punkt": N_("Start/Ziel an Punkt [X,Z]"), "linienbreite": N_("Ziellinienbreite · m"),
    "aktiv": N_("Aktiv"), "seite": N_("Seite in Fahrtrichtung"), "einfahrt_vor_start_m": N_("Einfahrt vor Ziel · m"),
    "ausfahrt_nach_start_m": N_("Ausfahrt nach Ziel · m"), "abstand_fahrbahn": N_("Abstand Fahrbahn · m"),
    "anlauf_m": N_("Anlauf · m"), "hinter_box_m": N_("Hinter Boxen · m"), "boxen": N_("Boxenzahl"),
    "box_abstand_m": N_("Boxenabstand · m"), "tempolimit_kmh": N_("Tempolimit · km/h"),
    "mauer_hoehe": N_("Boxenmauerhöhe · m"), "mauer_dicke": N_("Boxenmauerdicke · m"), "mauer_abstand_m": N_("Boxenmauerabstand · m"),
    "linien": N_("Linien (Tempolimit, Ein-/Ausfahrt)"), "linie_breite_m": N_("Linienbreite · m"),
    "gebaeude": N_("Garagengebäude"), "gebaeude_tiefe_m": N_("Garagentiefe · m"), "gebaeude_hoehe_m": N_("Garagenhöhe · m"),
    "gebaeude_wand_m": N_("Garagenwand · m"),
    "anzahl": N_("Anzahl"), "abstand_m": N_("Platzabstand · m"), "abstand_pole_m": N_("Pole hinter Ziel · m"),
    "pole_seite": N_("Pole-Seite"), "seitenversatz_m": N_("Seitlicher Versatz · m"), "fahrzeuglaenge_m": N_("Fahrzeuglänge · m"),
    "startboxen": N_("Startboxen auf dem Asphalt"),
    "spur_versatz_m": N_("Boxenlinie zur Strecke versetzt · m"), "spur_halbbreite_m": N_("Boxenspur halbe Breite · m"),
    "kruemmung_schwelle": N_("Krümmungsschwelle · 1/m"), "vorlauf_m": N_("Vorlauf · m"), "nachlauf_m": N_("Nachlauf · m"),
    "aussen_nachlauf_m": N_("Außennachlauf · m"), "rampe_m": N_("Rampe · m"), "abstand": N_("Abstand ab Fahrbahnrand · m"),
    "dicke": N_("Dicke · m"), "mindestabstand": N_("Mindestabstand · m"), "luecke_m": N_("Lücke · m"),
    "von_s": N_("Von Eingabe-s · m"), "bis_s": N_("Bis Eingabe-s · m"), "richtung_grad": N_("Startrichtung · °"),
    "suchbereich_anteil": N_("Sektor-Suchbereich · Anteil"), "kruemmung_gerade": N_("Geradenkrümmung · 1/m"),
    "mindestlaenge_m": N_("Mindestlänge · m"), "erkennung_vor_m": N_("DRS-Erkennung vorher · m"),
    "aktivierung_nach_m": N_("DRS-Aktivierung danach · m"), "ende_vor_m": N_("DRS-Ende vorher · m"),
    "mindestwinkel_grad": N_("Mindestwinkel · °"), "zusammenfassen_m": N_("Zusammenfassen · m"),
    "approximation_m": N_("Glättende Approximation · σ m (0 = aus, 6–8 empfohlen)"),
    "erkennung_m": N_("Erkennungspunkt · m ab Ziel"), "aktivierung_m": N_("DRS offen ab · m ab Ziel"),
    "ende_m": N_("DRS offen bis · m ab Ziel"), "von_m": N_("Straight Mode ab · m ab Ziel"), "bis_m": N_("Straight Mode bis · m ab Ziel"),
    "partial": N_("Partial-Zone"), "partial_versatz_m": N_("Partial beginnt nach · m"),
    "uebergang": N_("Klothoiden-Übergang (ja/nein/Länge m)"),
    "min_abbau_kmh": N_("Tafeln ab Geschwindigkeitsabbau · km/h"), "abstaende_m": N_("Tafeln · m vor der Kurve"),
    "rand_m": N_("Abstand vom Fahrbahnrand · m"),
    "schritt_m": N_("Stützstellenabstand · m"), "sicherheitsabstand_m": N_("Abstand Fahrzeugmitte – Rand · m"),
    "kruemmung_glaettung_m": N_("Krümmungsglättung · m"), "iterationen": N_("Iterationen (1 = einfach)"),
    "iterationen_toleranz_m": N_("Abbruch bei Verschiebung < m"), "feine_rueckprojektion": N_("Feine Rückprojektion"),
    "kruemmung_max": N_("Krümmungsgrenze · 1/m (leer = aus)"), "mindestzeit": N_("Mindestzeit-Feinschliff (Scheitelsuche)"),
    "mindestzeit_fahrzeug": N_("Fahrzeug der Bewertung"),
    "uebergaenge.aktiv": N_("Klothoiden-Übergänge"), "uebergaenge.laenge_m": N_("Übergang feste Länge · m"),
    "uebergaenge.a_faktor": N_("Übergang A = Faktor · R"), "uebergaenge.min_laenge_m": N_("Übergang min. · m"),
    "uebergaenge.max_laenge_m": N_("Übergang max. · m"), "uebergaenge.max_anteil": N_("Übergang max. Anteil Segment"),
}

# Fahrbahnbreite (wie strecke.definition.STANDARD; gilt, solange keine Vorschau geladen ist)
ROAD_WIDTH = 12.0
# Standard der Klothoiden-Übergänge (wie strecke.definition.STANDARD; gilt, solange keine Vorschau geladen ist)
TRANSITION_DEFAULTS = {"aktiv": False, "laenge_m": None, "a_faktor": 0.5, "min_laenge_m": 20.0,
                       "max_laenge_m": 150.0, "max_anteil": 0.4}

# Listen-Eigenschaften: Pfad in der Definition, Felder, Beschriftung je Eintrag, Knöpfe
LISTS = {
    "sections": {"path": ("fahrbahn", "abschnitte"),
                 "keys": ["von_s", "bis_s", "breite", "breite_links", "breite_rechts", "querneigung_grad"],
                 "title": lambda i, z: f"{i + 1} · {z.get('von_s', 0)}–{z.get('bis_s', 0)} m",
                 "add": N_("Abschnitt +"), "remove": N_("Abschnitt −"), "noun": N_("Profilabschnitt")},
    "drs_zonen": {"path": ("drs", "zonen"), "keys": ["erkennung_m", "aktivierung_m", "ende_m"],
                  "title": lambda i, z: f"DRS {i + 1} · {z.get('aktivierung_m', 0):.0f}–{z.get('ende_m', 0):.0f} m",
                  "add": N_("DRS-Zone +"), "remove": N_("DRS-Zone −"), "noun": N_("DRS-Zone")},
    "xmode_zonen": {"path": ("x_mode", "zonen"), "keys": ["von_m", "bis_m", "partial", "partial_versatz_m"],
                    "title": lambda i, z: f"Straight Mode {i + 1} · {z.get('von_m', 0):.0f}–{z.get('bis_m', 0):.0f} m",
                    "add": N_("Straight Mode +"), "remove": N_("Straight Mode −"), "noun": N_("Straight-Mode-Zone")},
}
ZONE_CATEGORY = {"drs": "drs_zonen", "xmode": "xmode_zonen"}

#: Stufen der Meldungen (deutsche Werte aus dem Werkzeugkasten; angezeigt wird tr(stufe))
ISSUE_LEVELS = [N_("Alle"), N_("Fehler"), N_("Warnung"), N_("Hinweis"), N_("Nicht geprüft")]
#: Prüfstufen des Werkzeugkastens (Bericht „stufen“, Meldung „stufe“)
#: Eingabegrenze der Querneigung in Grad (wie das Querneigungsfeld)
BANK_LIMIT = neigung.MAX_GRAD
CHECK_STAGES = [N_("eingabe"), N_("generator"), N_("profil"), N_("logik"), N_("bau"), N_("paket"), N_("Eingabe")]


def issue_texts(issue):
    """(Kürzel, Text) einer Prüfmeldung. Der Werkzeugkasten liefert nur deutsche Texte; auf Englisch steht der
    übersetzte Regeltitel (``i18n/regeln.csv``) vor dem Originaltext."""
    short, text = issue.get("kurz", issue["id"]), meldung(issue.get("text", ""))
    title = rule_title(issue.get("id"))
    if title:
        text = f"{title} – {text}" if text else title
    return short, text


def issue_order(issue):
    """Reihenfolge der Prüfliste: Fehler, Warnungen, Hinweise, nicht geprüft; je Stufe ohne Ort zuerst, dann
    nach Station."""
    location = issue.get("ort") or {}
    station = location.get("station_m", issue.get("s"))
    known = isinstance(station, (int, float))
    return ({"Fehler": 0, "Warnung": 1, "Hinweis": 2}.get(issue.get("level"), 3), known, station if known else 0)


def german_numbers(text):
    """Dezimalpunkt in Zahlen von Werkzeugmeldungen als Komma anzeigen („989.0 m“ → „989,0 m“); englisch unverändert."""
    if sprache() == "en":
        return str(text)
    return re.sub(r"(?<![\w.])(-?\d+)\.(\d+)(?![\w.])", r"\1,\2", str(text))


def lap_text(seconds):
    """Rundenzeit „1:44,7“ (englisch „1:44.7“)."""
    minutes, rest = divmod(float(seconds), 60.0)
    text = f"{int(minutes)}:{rest:04.1f}"
    return text if sprache() == "en" else text.replace(".", ",")


def value_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    simple = edit_text(value)
    return simple if simple is not None else json.dumps(value, ensure_ascii=False)


def parse_bool(text):
    word = text.strip().lower()
    if word not in BOOL_WORDS:
        raise ValueError(tr("Bitte ja oder nein wählen."))
    return BOOL_WORDS[word]


def parse_value(text, template, key):
    text = text.strip()
    if not text:
        return None
    if key in CHOICES:
        return choice_value(key, text)
    if isinstance(template, str) or key in ("typ", "name", "richtung", "seite", "pole_seite", "schliessen"):
        return text
    if isinstance(template, (dict, list)) or key == "punkt":
        return json.loads(text)
    if key == "uebergang":
        # ja / nein / Länge in m / {"anfang": …, "ende": …}
        if text.lower() in BOOL_WORDS:
            return BOOL_WORDS[text.lower()]
        if text.startswith("{"):
            return json.loads(text)
        number = float(text.replace(",", "."))
        if not math.isfinite(number) or number <= 0:
            raise ValueError(tr("Übergang: ja, nein, Länge > 0 oder {\"anfang\": …, \"ende\": …}."))
        return number
    if isinstance(template, bool) or key.endswith(".aktiv") or key in ("aktiv", "umkehren", "partial"):
        return parse_bool(text)
    number = float(text.replace(",", "."))
    if not math.isfinite(number):
        raise ValueError(tr("Bitte einen endlichen Zahlenwert eingeben."))
    if isinstance(template, int) and key in ("anzahl", "boxen"):
        if number != int(number):
            raise ValueError(tr("Bitte eine ganze Zahl eingeben."))
        return int(number)
    return number


def _outside_slope(ordered, k, step, reach=25.0):
    """Steigung (m/m) neben dem Bereich: vom Punkt k aus mindestens ``reach`` m nach außen (step −1/+1)."""
    n = len(ordered)
    j, travelled = k, 0.0
    while travelled < reach and 0 <= j + step < n:
        j += step
        travelled = abs(ordered[j]["s"] - ordered[k]["s"])
    if j == k or travelled < 1e-6:
        return 0.0
    return (ordered[k]["y"] - ordered[j]["y"]) / (ordered[k]["s"] - ordered[j]["s"])


def smooth_transition(ordered, ka, kb):
    """Sanfter Übergang zwischen den nach Station sortierten Punkten ka (A) und kb (B): kubische Hermite-Kurve über
    der Station, Höhen von A und B bleiben, Endsteigungen = Steigung außerhalb des Bereichs (monoton begrenzt nach
    Fritsch–Carlson, daher kein Über-/Unterschwingen). Rückgabe {Objektindex: Höhe} für die Punkte dazwischen."""
    a, b = ordered[ka], ordered[kb]
    run = b["s"] - a["s"]
    if run <= 1e-6 or kb - ka < 2:
        return {}
    secant = (b["y"] - a["y"]) / run
    def limit(m):
        if secant == 0 or m * secant <= 0:
            return 0.0
        return max(-3 * abs(secant), min(3 * abs(secant), m))
    ma, mb = limit(_outside_slope(ordered, ka, -1)), limit(_outside_slope(ordered, kb, 1))
    out = {}
    for k in range(ka + 1, kb):
        t = (ordered[k]["s"] - a["s"]) / run
        h00, h10, h01, h11 = 2 * t**3 - 3 * t**2 + 1, t**3 - 2 * t**2 + t, -2 * t**3 + 3 * t**2, t**3 - t**2
        out[ordered[k]["index"]] = h00 * a["y"] + h10 * run * ma + h01 * b["y"] + h11 * run * mb
    return out


def smooth_range(ordered, ka, kb, passes=None):
    """Glätten zwischen ka und kb (Endpunkte fest): wiederholtes 1-2-1-Mitteln, Stärke nach Punktzahl."""
    ys = [n["y"] for n in ordered[ka:kb + 1]]
    for _ in range(passes or max(3, min(40, len(ys)))):
        ys = [ys[0]] + [(ys[i - 1] + 2 * ys[i] + ys[i + 1]) / 4 for i in range(1, len(ys) - 1)] + [ys[-1]]
    return {ordered[ka + i]["index"]: y for i, y in enumerate(ys) if 0 < i < len(ys) - 1}


class PropertyTable(QTableWidget):
    """Nur tatsächlich geänderte Felder schreiben; leeres Feld entfernt eine Vorgabe."""
    def __init__(self):
        super().__init__(0, 2)
        self.setHorizontalHeaderLabels([tr("Eigenschaft"), tr("Wert")])
        self.setItemDelegate(ChoiceDelegate(self))
        self.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed |
                             QAbstractItemView.AnyKeyPressed | QAbstractItemView.SelectedClicked)
        self.setAlternatingRowColors(True)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.verticalHeader().hide()
        self.setWordWrap(False)
        self.entries = []
        self.setMinimumWidth(300)

    def populate(self, entries, readonly=False):
        self.blockSignals(True)
        self.entries = entries
        self.setRowCount(len(entries))
        for row, (key, actual, fallback, label) in enumerate(entries):
            value = actual if actual is not None else fallback
            label_item = QTableWidgetItem(tr(label or LABELS.get(key, key)))
            label_item.setFlags(label_item.flags() & ~Qt.ItemIsEditable)
            label_item.setToolTip((tr("Eigener Wert") if actual is not None else
                                   tr("Standardwert (kursiv) – wird erst gespeichert, wenn du ihn änderst"))
                                  + tr("\nFeld in der Definition: {key}", key=key))
            shown = choice_text(value) if key in CHOICES else value_text(value)
            item = QTableWidgetItem(shown)
            item.setData(Qt.UserRole, shown)
            item.setToolTip(tr("Doppelklick oder Tippen zum Ändern, Enter übernimmt. Feld leeren = Standard wiederherstellen."))
            if actual is None:
                font = item.font()
                font.setItalic(True)
                item.setFont(font)
            if readonly:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                item.setToolTip(tr("Unbekannte Projektfassung: nur lesend geöffnet."))
            self.setItem(row, 0, label_item)
            self.setItem(row, 1, item)
        self.resizeRowsToContents()
        self.blockSignals(False)

    def pending(self):
        return any(self.item(row, 1).text() != self.item(row, 1).data(Qt.UserRole) for row in range(len(self.entries)))

    def changes(self):
        changes = {}
        for row, (key, actual, fallback, label) in enumerate(self.entries):
            item = self.item(row, 1)
            if item.text() != item.data(Qt.UserRole):
                template = actual if actual is not None else fallback
                changes[key] = parse_value(item.text(), 0. if template == "—" else template, key)
        return changes


class MainWindow(QMainWindow):
    def __init__(self, project=None, auto_preview=True):
        super().__init__()
        ensure_fonts(QApplication.instance())
        self.document = Document(project)
        self.preview = None
        self.preview_revision = -1
        self.job = None
        self.build_dialog = None
        self.check_generator = False
        self.validation_issues = []
        self.build_issues = []
        self.validation_report = {}
        self.preview_pending = False
        self.defaults = {}
        self.loading_list = False
        self.fit_after_preview = True
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(400)
        self.preview_timer.timeout.connect(self.request_preview)
        # Fahrlinienlauf (voller Generator + Prüfung) im Hintergrund; Ergebnis je Definitionsstand zwischengespeichert
        # und von „Prüfen“ und der Vorabprüfung des Baus wiederverwendet.
        self.analysis = None
        self.analysis_key = None
        self.analysis_job = None
        self.analysis_job_key = None
        self.check_cache = {}
        self.auto_analysis = auto_preview      # Tests ohne Automatik starten keinen Generatorlauf
        self.analysis_timer = QTimer(self)
        self.analysis_timer.setSingleShot(True)
        self.analysis_timer.setInterval(1500)
        self.analysis_timer.timeout.connect(self.request_analysis)
        # Ein-/Ausblenden (Menü Ansicht), Autosicherung, zuletzt geöffnete Projekte
        self.view_state = ansicht.load_state()
        if not auto_preview:
            self.view_state["fahrlinie_auto"] = False      # Tests/Skripte ohne Automatik: kein Generatorlauf
        self.feature_actions, self.feature_checks = {}, {}
        self.view_dialog = None
        self.bank_feature = True
        self.welcome_feature = True
        self.interactive = auto_preview
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(60_000)
        self.autosave_timer.timeout.connect(self.autosave)
        # Umgebung: Katalog (einmal je Fenster geladen, bleibt beim Sprachwechsel), Prüfmeldungen UMG_*, Arbeitsprozesse
        self.env_catalog = None
        self.env_issues = []
        self.env_job = self.env_preview_job = self.env_check_job = None
        self.env_preview_wanted = None
        self.env_preview_paths = {}
        self.env_check_timer = QTimer(self)
        self.env_check_timer.setSingleShot(True)
        self.env_check_timer.setInterval(700)
        self.env_check_timer.timeout.connect(self.request_env_check)
        self.resize(1440, 940)
        self.setMinimumSize(1100, 720)
        self._build_ui()
        self.document.changed.connect(self._changed)
        self.document.selection_changed.connect(self._selection_changed)
        self.document.env_selection_changed.connect(self._env_selection_changed)
        self.document.undo.cleanChanged.connect(self._title)
        self._after_build()
        self.map.fit_track()
        self._remember_current()
        if auto_preview:
            self.autosave_timer.start()
            QTimer.singleShot(0, self.offer_recovery)
            QTimer.singleShot(0, self.request_preview)

    def _after_build(self):
        """Frisch aufgebaute Bedienelemente mit dem Dokument und den Ansichtsschaltern füllen."""
        self._refresh_objects()
        self._refresh_properties()
        self.set_theme(self.document.project.manifest.get("thema", "dunkel"))
        self._title()
        self.map.rebuild()
        self._show_input_profiles()
        self.set_mode("auswahl", focus=False)
        for key, value in self.view_state.items():
            self.apply_feature(key, value, save=False)
        self._restore_layout()
        self._apply_env_catalog()
        self._refresh_env_panel()

    def action(self, menu, title, callback, shortcut=None):
        action = QAction(title, self)
        action.triggered.connect(callback)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        menu.addAction(action)
        return action

    def _build_ui(self):
        file_menu = self.menuBar().addMenu(tr("&Projekt"))
        self.action(file_menu, tr("Neu"), self.new_project, "Ctrl+N")
        self.action(file_menu, tr("Neu aus Vorlage …"), self.new_from_template)
        self.action(file_menu, tr("Vorschlag erzeugen …"), self.new_from_suggestion)
        self.action(file_menu, tr("Definition / Manifest öffnen …"), self.open_dialog, "Ctrl+O")
        self.action(file_menu, tr("Projektordner öffnen …"), self.open_folder_dialog)
        self.action(file_menu, tr("Speichern"), self.save_project, "Ctrl+S")
        self.action(file_menu, tr("Speichern unter …"), lambda: self.save_project(True), "Ctrl+Shift+S")
        self.recent_menu = file_menu.addMenu(tr("Zuletzt geöffnet"))
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        file_menu.addSeparator()
        self.action(file_menu, tr("Beenden"), self.close, "Alt+F4")
        edit_menu = self.menuBar().addMenu(tr("&Bearbeiten"))
        undo = self.document.undo.createUndoAction(self, tr("Rückgängig"))
        undo.setShortcut(QKeySequence("Ctrl+Z"))
        redo = self.document.undo.createRedoAction(self, tr("Wiederholen"))
        redo.setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        undo.setToolTip(tr("Noch keine Änderung zum Rückgängigmachen."))
        redo.setToolTip(tr("Noch keine rückgängig gemachte Änderung zum Wiederholen."))
        self.undo_action, self.redo_action = undo, redo
        undo.changed.connect(self._history_status)
        redo.changed.connect(self._history_status)
        edit_menu.addActions([undo, redo])
        edit_menu.addSeparator()
        # Kopieren/Einfügen von Streckenpunkten (Issue #2); Kürzel nur in der Karte, Textfelder kopieren wie gewohnt
        self.clip_actions = [
            self.action(edit_menu, tr("Punkte kopieren"), self.copy_points, "Ctrl+C"),
            self.action(edit_menu, tr("Punkte ausschneiden"), self.cut_points, "Ctrl+X"),
            self.action(edit_menu, tr("Punkte einfügen"), self.paste_points, "Ctrl+V"),
        ]
        self.action(edit_menu, tr("Runde hier öffnen"), self.open_here)
        self.action(edit_menu, tr("Reihenfolge der Auswahl umkehren"), self.reverse_selection)
        self.action(edit_menu, tr("Überstand abschneiden"), self.cut_overhang)
        edit_menu.addSeparator()
        self.action(edit_menu, tr("Querneigung: alle Kurven automatisch"), lambda: self.auto_bank(self.bank_panel.amount.value() or 4.0))
        self.action(edit_menu, tr("Querneigung: ganze Strecke flach"), self.flatten_bank)
        view_menu = self.menuBar().addMenu(tr("&Ansicht"))
        self.action(view_menu, tr("Dunkles Thema"), lambda: self.change_theme("dunkel"))
        self.action(view_menu, tr("Helles Thema"), lambda: self.change_theme("hell"))
        view_menu.addSeparator()
        for group in ansicht.GROUPS:
            submenu = view_menu.addMenu(tr(group))
            for key, g, label, tip, _ in ansicht.FEATURES:
                if g != group:
                    continue
                action = QAction(tr(label), self)
                action.setCheckable(True)
                action.setChecked(self.view_state.get(key, True))
                action.setToolTip(tr(tip) if tip else "")
                action.toggled.connect(lambda on, key=key: self.apply_feature(key, on))
                submenu.addAction(action)
                self.feature_actions[key] = action
        view_menu.addSeparator()
        self.action(view_menu, tr("Ansicht anpassen …"), self.open_view_dialog, "Ctrl+Shift+A")
        self.action(view_menu, tr("Alles einblenden"), self.show_all_features)
        self.action(view_menu, tr("Unteren Bereich maximieren / zurück"), self.toggle_results_maximized, "Ctrl+M")
        view_menu.addSeparator()
        self.language_menu = view_menu.addMenu(tr("Sprache / Language"))
        self.language_actions = {}
        language_group = QActionGroup(self)
        language_group.setExclusive(True)
        for code, title in SPRACHEN.items():
            action = QAction(title, self)
            action.setCheckable(True)
            action.setChecked(code == sprache())
            action.triggered.connect(lambda checked=False, code=code: self.change_language(code))
            language_group.addAction(action)
            self.language_menu.addAction(action)
            self.language_actions[code] = action
        tools = self.menuBar().addMenu(tr("&Werkzeuge"))
        self.action(tools, tr("Werkzeugkasten wählen …"), self.choose_toolkit)
        self.action(tools, tr("Spielweltversatz einstellen …"), self.choose_shift)
        self.action(tools, tr("Generatorausgabe lesend öffnen …"), self.open_output)
        self.action(tools, tr("Aktuellen Entwurf prüfen"), self.request_check, "Ctrl+Return")
        self.action(tools, tr("Bauen / Paket …"), self.open_build)
        self.action(tools, tr("Spieltest-Protokoll …"), self.open_playtests)
        self.action(tools, tr("Alte Bauten aufräumen …"), self.open_cleanup)
        help_menu = self.menuBar().addMenu(tr("&Hilfe"))
        self.action(help_menu, tr("Bedienung"), self.show_help, "F1")
        toolbar = QToolBar(tr("Entwerfen"), self)
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        toolbar.setIconSize(QSize(18, 18))
        self.addToolBar(toolbar)
        self.toolbar = toolbar

        def tool(text, glyph, callback, tip, shortcut_text=""):
            action = QAction(icon(glyph), text, self)
            action.triggered.connect(callback)
            action.setToolTip(f"{tip}  ({shortcut_text})" if shortcut_text else tip)
            toolbar.addAction(action)
            return action
        group = QActionGroup(self)
        group.setExclusive(True)
        self.mode_actions = {}
        for name, mode, glyph, tip, key in ((tr("Auswahl"), "auswahl", "auswahl", tr("Wählen, ziehen, drehen"), "V"),
                                            (tr("Punkte"), "punkte", "punkte", tr("Klick in die Karte setzt einen Kontrollpunkt"), "P"),
                                            (tr("Umgebung"), "umgebung", "umgebung",
                                             tr("Bäume, Gebäude … aus dem Katalog platzieren, wählen und verschieben"), "U"),
                                            (tr("Bepflanzen"), "streuen", "streuen",
                                             tr("Eine Fläche (Rechteck oder Pinsel) mit Bäumen und Büschen bepflanzen"), "B")):
            action = QAction(icon(glyph), name, self)
            action.setCheckable(True)
            action.setChecked(mode == "auswahl")
            action.setToolTip(f"{tip}  ({key})")
            action.triggered.connect(lambda checked=False, mode=mode: self.set_mode(mode))
            group.addAction(action)
            self.mode_actions[mode] = action
            toolbar.addAction(action)
        toolbar.addSeparator()
        self.close_action = tool(tr("Runde schließen"), "runde", self.toggle_closed, "")
        toolbar.widgetForAction(self.close_action).setObjectName("runde")
        toolbar.addSeparator()
        undo.setIcon(icon("undo"))
        redo.setIcon(icon("redo"))
        toolbar.addActions([undo, redo])
        compact = Qt.ToolButtonIconOnly if has_icons() else Qt.ToolButtonTextOnly
        for action in (undo, redo):
            toolbar.widgetForAction(action).setToolButtonStyle(compact)
        toolbar.addSeparator()
        fit = tool(tr("Einpassen"), "einpassen", lambda: self.map.fit_track(), tr("Ganze Strecke zeigen"), tr("Pos1"))
        zoom_in = tool(tr("Vergrößern"), "zoom_in", lambda: self.map.scale(1.2, 1.2), tr("Hineinzoomen"), tr("Mausrad"))
        zoom_out = tool(tr("Verkleinern"), "zoom_out", lambda: self.map.scale(1 / 1.2, 1 / 1.2), tr("Herauszoomen"), tr("Mausrad"))
        # Zoomen geht mit dem Mausrad: im Menü statt in der Werkzeugleiste
        for action in (zoom_in, zoom_out):
            toolbar.removeAction(action)
        view_menu.insertSeparator(self.language_menu.menuAction())
        view_menu.insertActions(self.language_menu.menuAction(), [zoom_in, zoom_out])
        zoom_in.setShortcut(QKeySequence.ZoomIn)
        zoom_out.setShortcut(QKeySequence.ZoomOut)
        edit_menu.insertAction(edit_menu.actions()[2], self.close_action)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)
        self.check_action = tool(tr("Prüfen"), "pruefen", self.request_check,
                                 tr("Strecke mit allen Regeln prüfen, Ergebnis unten unter „Prüfung“"), tr("Strg+Enter"))
        self.build_action = tool(tr("Bauen"), "bauen", self.open_build, tr("Paket für den Track Installer bauen"))
        toolbar.widgetForAction(self.build_action).setObjectName("primary")
        self.playtest_action = tool(tr("Spieltest"), "spieltest", self.open_playtests,
                                    tr("Ergebnisse deiner Spieltests notieren"))
        # Vorschau erneuern / abbrechen: im Menü und in der Statuszeile (läuft automatisch)
        self.preview_action = QAction(icon("runde"), tr("Vorschau neu berechnen"), self)
        self.preview_action.triggered.connect(self.request_preview)
        self.cancel_action = QAction(icon("abbrechen"), tr("Vorschau abbrechen"), self)
        self.cancel_action.triggered.connect(self.cancel_preview)
        self.cancel_action.setEnabled(False)
        self.cancel_action.setToolTip(tr("Keine laufende Berechnung. Die Vorschau startet 400 ms nach einer Änderung."))
        tools.addSeparator()
        tools.addActions([self.preview_action, self.cancel_action])

        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.display_section = Section(tr("ANZEIGE"), self._section_open("anzeige", False))
        self.display_section.toggled.connect(lambda on: self._section_saved("anzeige", on))
        left_layout.addWidget(self.display_section)
        from PySide6.QtWidgets import QCheckBox
        for key in ("raster", "raender", "richtung", "spielbereich", "zonen", "ideallinie", "rennbetrieb",
                    "kurvennummern", "umgebung", "eingabepunkte", "fahrlinie_auto"):
            checkbox = QCheckBox(tr(ansicht.LABELS[key]))
            checkbox.setChecked(self.view_state.get(key, True))
            checkbox.setToolTip(tr(ansicht.TIPS[key]) + tr(" · Alle Schalter: Ansicht → Ansicht anpassen (Strg+Umschalt+A)"))
            checkbox.toggled.connect(lambda checked, key=key: self.apply_feature(key, checked))
            checkbox.toggled.connect(lambda *_: self._display_summary())
            self.display_section.layout_body.addWidget(checkbox)
            self.feature_checks[key] = checkbox
        self.auto_analysis_box = self.feature_checks["fahrlinie_auto"]
        self._display_summary()
        left_layout.addSpacing(6)
        left_layout.addWidget(self._heading(tr("STRECKE")))
        self.source_label = QLabel()
        self.source_label.setWordWrap(True)
        left_layout.addWidget(self.source_label)
        self.find_field = QLineEdit()
        self.find_field.setPlaceholderText(tr("Gehe zu: Station (m), T4 oder Punkt #12 …"))
        self.find_field.setToolTip(tr("Enter springt: Zahl = Meter ab Ziel, „T4“ = Kurve 4 (Spielnummer), "
                                   "„#12“ = Objekt 12. Karte, Profile und Liste folgen."))
        self.find_field.returnPressed.connect(self.find_location)
        left_layout.addWidget(self.find_field)
        self.point_section = Section(tr("PUNKTE"), True)
        self.point_section.toggled.connect(self._point_section_toggled)
        self.point_section_user = self._section_open("punkte", None)     # None: je nach Streckengröße
        left_layout.addWidget(self.point_section, 1)
        self.left_stretch = left_layout.count()
        left_layout.addStretch(0)
        section_layout = self.point_section.layout_body
        self.objects = QListWidget()
        self.objects.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.objects.setWordWrap(True)
        self.objects.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.objects.setAccessibleName(tr("Mittellinienobjekte; Auswahl synchron mit Karte und Profil"))
        self.objects.itemSelectionChanged.connect(self._list_selected)
        self.objects.currentRowChanged.connect(self._list_current_selected)
        section_layout.addWidget(self.objects, 1)
        row = QHBoxLayout()
        self.add_button = add = QPushButton(tr("+ Punkt"))
        add.clicked.connect(self.add_object)
        self.remove_button = remove = QPushButton(tr("Löschen"))
        remove.clicked.connect(self.remove_object)
        row.addWidget(add)
        row.addWidget(remove)
        section_layout.addLayout(row)
        self.segment_buttons = []
        for title, callback in ((tr("+ Bogen"), lambda: self.document.insert_segment("bogen")),
                                (tr("Segment teilen"), self.document.split_segment),
                                (tr("Gerade ↔ Bogen"), self.document.switch_segment),
                                (tr("In Kontrollpunkte umwandeln"), self.convert_to_points)):
            button = QPushButton(title)
            button.clicked.connect(callback)
            section_layout.addWidget(button)
            self.segment_buttons.append(button)
        self.gap_button = QPushButton(tr("Lücken füllen"))              # außerhalb: bleibt bei eingeklappter Liste sichtbar
        self.gap_button.clicked.connect(self.fill_gaps)
        left_layout.addWidget(self.gap_button)
        self.left_layout = left_layout

        self.map = MapView(self.document)
        for action in self.clip_actions:
            action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
            self.map.addAction(action)
        self.map.mode_changed.connect(self.set_mode)
        self.map.draft_changed.connect(self._draft_profiles)
        # Profile während einer Kartengeste höchstens ~15× pro Sekunde neu zeichnen (Leistung beim Ziehen).
        self.profile_timer = QTimer(self)
        self.profile_timer.setSingleShot(True)
        self.profile_timer.setInterval(66)
        self.profile_timer.timeout.connect(self._show_input_profiles)
        self.map.station_changed.connect(self.set_station)
        self.height = ProfileView()
        self.bank = ProfileView("bank")
        self.cross = CrossSection()
        for profile in (self.height, self.bank):
            profile.station_changed.connect(self.set_station)
            profile.object_selected.connect(self.document.select)
        self.bank.object_selected.connect(lambda i: self.category.setCurrentIndex(self.category.findData("object")))
        # Höhenprofil: Eingabehöhen als Griffe (ziehen, Rahmen, Pfeiltasten, Rechtsklick-Werkzeuge)
        self._geometry_cache = (-1, None)
        self.height.node_source = self._height_nodes
        self.height.nodes_selected.connect(lambda indices, primary: self.document.select_many(indices, primary))
        self.height.heights_changed.connect(self.set_heights)
        self.height.height_tool.connect(self.height_tool)
        # Querneigungsprofil: dieselben Griffe und Werkzeuge, Werte in Grad
        self._bank_nodes_cache = (None, [])
        self.bank.node_source = self._bank_nodes
        self.bank.nodes_selected.connect(lambda indices, primary: self.document.select_many(indices, primary))
        self.bank.heights_changed.connect(self.set_banks)
        self.bank.height_tool.connect(self.bank_tool)
        self.results = QTabWidget()
        # Höhenprofil-Reiter: Knopfleiste für die gewählte Strecke A→B über dem Profil
        self.height_page = QWidget()
        page_layout = QVBoxLayout(self.height_page)
        page_layout.setContentsMargins(6, 4, 6, 0)
        page_layout.setSpacing(2)
        bar = QHBoxLayout()
        self.height_info = QLabel(tr("Waagerecht ziehen wählt einen Bereich A→B."))
        bar.addWidget(self.height_info, 1)
        self.height_buttons = {}
        for key, text, tip in (
                ("uebergang", tr("Sanfter Übergang A→B"), tr("Höhen zwischen dem ersten (A) und letzten (B) gewählten Punkt als "
                 "S-Kurve neu verteilen: Höhe von A und B bleibt, die Steigung schließt an beiden Enden ohne Knick an.")),
                ("rampe", tr("Rampe"), tr("Gleichmäßige Steigung von A nach B")),
                ("eben", tr("Gleiche Höhe"), tr("Alle gewählten Punkte auf ihren Mittelwert")),
                ("glaetten", tr("Glätten"), tr("Unebenheiten in der Auswahl ausgleichen (A und B bleiben)"))):
            button = QPushButton(text)
            button.setToolTip(tip + tr(" · Strg+Z macht rückgängig."))
            button.clicked.connect(lambda checked=False, key=key: self.height_tool(key))
            bar.addWidget(button)
            self.height_buttons[key] = button
        page_layout.addLayout(bar)
        page_layout.addWidget(self.height, 1)
        self.results.addTab(self.height_page, tr("Höhenprofil"))
        # Querneigungs-Reiter: gleiche Knopfleiste wie beim Höhenprofil
        self.bank_page = QWidget()
        bank_layout = QVBoxLayout(self.bank_page)
        bank_layout.setContentsMargins(6, 4, 6, 0)
        bank_layout.setSpacing(2)
        bank_bar = QHBoxLayout()
        self.bank_info = QLabel(tr("Waagerecht ziehen wählt einen Bereich A→B · senkrecht ziehen ändert die Querneigung."))
        self.bank_info.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)   # langer Text weitet das Fenster nicht
        bank_bar.addWidget(self.bank_info, 1)
        self.bank_buttons = {}
        for key, text, tip in (
                ("uebergang", tr("Sanfter Übergang A→B"), tr("Querneigung zwischen dem ersten (A) und letzten (B) gewählten "
                 "Punkt als S-Kurve neu verteilen: Werte von A und B bleiben, ohne Knick an beiden Enden.")),
                ("rampe", tr("Rampe"), tr("Gleichmäßiger Übergang der Querneigung von A nach B")),
                ("eben", tr("Gleicher Wert"), tr("Alle gewählten Punkte auf ihren Mittelwert")),
                ("glaetten", tr("Glätten"), tr("Sprünge in der Auswahl ausgleichen (A und B bleiben)"))):
            button = QPushButton(text)
            button.setToolTip(tip + tr(" · Strg+Z macht rückgängig."))
            button.clicked.connect(lambda checked=False, key=key: self.bank_tool(key))
            bank_bar.addWidget(button)
            self.bank_buttons[key] = button
        bank_layout.addLayout(bank_bar)
        bank_layout.addWidget(self.bank, 1)
        self.results.addTab(self.bank_page, tr("Querneigung"))
        # Querschnitt-Reiter: Ansicht + Bearbeiten je Bereich (Fahrbahn, Randstein, Grünstreifen, Auslauf, Barriere)
        self.cross_page = QWidget()
        cross_layout = QHBoxLayout(self.cross_page)
        cross_layout.setContentsMargins(0, 0, 6, 0)
        cross_layout.addWidget(self.cross, 1)
        self.cross_panel = CrossPanel()
        self._cross_loaded = self.cross_panel.fields()      # Stand nach dem letzten Laden: Abweichung = eigene Eingabe
        self.cross_panel.apply_requested.connect(self.apply_cross)
        self.cross_panel.clear_requested.connect(self.clear_cross)
        self.cross_panel.range_requested.connect(self.cross_range)
        self.cross_panel.side.currentIndexChanged.connect(lambda *_: self._update_cross_panel())
        # Randwinkel sofort in der Ansicht zeigen, nicht erst nach „Übernehmen“
        self.cross_panel.edge.currentIndexChanged.connect(lambda *_: self._cross_edge_input())
        self.cross_panel.edge_angle.valueChanged.connect(lambda *_: self._cross_edge_input())
        for box in (self.cross_panel.von, self.cross_panel.bis):
            box.valueChanged.connect(lambda *_: self._cross_edge_input())
        cross_scroll = QScrollArea()             # sonst bestimmt die Höhe des Felds die Mindesthöhe des unteren Bereichs
        cross_scroll.setWidget(self.cross_panel)
        cross_scroll.setWidgetResizable(True)
        cross_scroll.setFrameShape(QFrame.NoFrame)
        cross_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        cross_scroll.setMinimumWidth(570)           # zwei Spalten samt Rollbalken; Stile wirken erst nach dem Messen
        cross_layout.addWidget(cross_scroll)
        self._update_cross_panel()
        self.results.addTab(self.cross_page, tr("Querschnitt"))
        self.speed = SpeedView()
        self.speed.station_changed.connect(self.set_station)
        self.results.addTab(self.speed, tr("Geschwindigkeit"))
        self.issues = QListWidget()
        self.issues.setWordWrap(True)
        self.issues.itemClicked.connect(self.jump_issue)
        self.issues.itemActivated.connect(self.jump_issue)
        self.issue_panel = QWidget()
        issue_layout = QVBoxLayout(self.issue_panel)
        filter_row = QHBoxLayout()
        self.issue_filter = QComboBox()
        for level in ISSUE_LEVELS:
            self.issue_filter.addItem(tr(level), level)
        self.issue_search = QLineEdit()
        self.issue_search.setPlaceholderText(tr("Meldung, Regel-ID oder Bau-ID suchen …"))
        self.issue_filter.currentTextChanged.connect(self.render_issues)
        self.issue_search.textChanged.connect(self.render_issues)
        filter_row.addWidget(self.issue_filter)
        filter_row.addWidget(self.issue_search, 1)
        self.issue_summary = QLabel(tr("Noch nicht geprüft"))
        issue_layout.addLayout(filter_row)
        issue_layout.addWidget(self.issue_summary)
        issue_layout.addWidget(self.issues)
        self.issue_tab = self.results.addTab(self.issue_panel, tr("Prüfung"))
        self.analysis_page = QWidget()
        analysis_layout = QVBoxLayout(self.analysis_page)
        analysis_layout.setContentsMargins(6, 4, 6, 4)
        self.analysis_info = QLabel(tr("Auswertung erscheint nach dem Fahrlinienlauf (automatisch nach einer Änderung "
                                    "oder mit „Prüfen“ / Strg+Enter)."))
        self.analysis_info.setWordWrap(True)
        self.analysis_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        analysis_layout.addWidget(self.analysis_info)
        self.curve_table = QTableWidget(0, 10)
        self.curve_table.setHorizontalHeaderLabels([tr("Kurve"), tr("Richtung"), tr("Winkel"), tr("R min"), tr("Scheitel bei"),
                                                    tr("v vor Bremsen"), "v min", tr("Bremsweg"), tr("Tafeln"), tr("Generator")])
        self.curve_table.verticalHeader().hide()
        self.curve_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.curve_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.curve_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.curve_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.curve_table.horizontalHeader().setStretchLastSection(True)
        self.curve_table.setToolTip(tr("Zeile anklicken: Karte und Profile springen zum Scheitel der Kurve"))
        self.curve_table.cellClicked.connect(self._curve_clicked)
        analysis_layout.addWidget(self.curve_table, 1)
        self.output_tree = QTreeWidget()
        self.output_tree.setHeaderLabels([tr("Generatorausgabe · lesend"), tr("Wert")])
        self.output_tree.hide()
        analysis_layout.addWidget(self.output_tree, 1)
        self.results.addTab(self.analysis_page, tr("Auswertung"))
        self.results.setTabToolTip(self.results.indexOf(self.analysis_page),
                                   tr("Rundenzeit, Geschwindigkeiten und Kurventabelle aus dem Fahrlinienlauf; "
                                   "Rohdaten einer Generatorausgabe über Werkzeuge → Generatorausgabe lesend öffnen"))
        # Umgebung-Reiter: Katalog, Bepflanzen, Eigenschaften der gewählten Objekte (zuletzt, damit die Reiterindizes bleiben)
        self.env_panel = EnvironmentPanel()
        self.env_panel.model_chosen.connect(self._env_model_chosen)
        self.env_panel.preview_requested.connect(self._env_preview_request)
        self.env_panel.refresh_requested.connect(lambda: self.load_env_catalog(neu=True))
        self.env_panel.settings_changed.connect(self._env_settings_changed)
        self.env_panel.properties_requested.connect(self._env_properties)
        self.env_panel.ground_cover_toggled.connect(self._env_ground_cover)
        self.env_panel.select_group_requested.connect(self._env_select_group)
        self.env_panel.delete_group_requested.connect(self._env_delete_group)
        self.env_panel.delete_requested.connect(lambda: self.document.env_delete())
        self.env_panel.terrain_armed.connect(self._terrain_armed)
        self.env_panel.terrain_values_changed.connect(self._terrain_values)
        self.env_panel.terrain_delete_requested.connect(lambda: self.document.terrain_delete())
        self.env_tab = self.results.addTab(self.env_panel, tr("Umgebung"))
        self.results.setTabToolTip(self.env_tab, tr("Bäume, Büsche, Gebäude und Zelte aus dem Spiel platzieren und bepflanzen"))
        self.results.currentChanged.connect(self._results_tab_changed)
        self.map.env_place_requested.connect(self._env_place)
        self.map.env_scatter_requested.connect(self._env_scatter)
        self.map.env_disarm_requested.connect(self.env_panel.disarm)
        self.map.terrain_place_requested.connect(self._terrain_place)
        center = QSplitter(Qt.Vertical)
        center.addWidget(self.map)
        center.addWidget(self.results)
        center.setSizes([540, 300])          # unten genug Höhe für die Profile (E12)
        self.vertical_split = center
        self.results.tabBarDoubleClicked.connect(lambda index: self.toggle_results_maximized())
        self.results.setTabToolTip(0, tr("Doppelklick auf einen Reiter: unteren Bereich maximieren / zurück (Strg+M)"))
        self.tab_features = {"reiter_hoehe": self.height_page, "reiter_neigung": self.bank_page, "reiter_querschnitt": self.cross_page,
                             "reiter_geschwindigkeit": self.speed, "reiter_pruefung": self.issue_panel,
                             "reiter_auswertung": self.analysis_page, "reiter_umgebung": self.env_panel}

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(self.env_panel.selection_box)        # nur in den Umgebungswerkzeugen sichtbar
        right_layout.addWidget(self.env_panel.terrain_box)
        for box in (self.env_panel.selection_box, self.env_panel.terrain_box):   # nicht stauchen; Eigenschaften weichen
            box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.inspector_title = self._heading(tr("EIGENSCHAFTEN"))
        right_layout.addWidget(self.inspector_title)
        self.category = QComboBox()
        self.category.setToolTip(tr("Welche Einstellungen angezeigt werden"))
        self.category.setMaxVisibleItems(24)
        for group in (((tr("Ausgewähltes Objekt"), "object"),),
                      ((tr("Fahrbahn (ganze Strecke)"), "fahrbahn"), (tr("Abschnitte: Breite / Querneigung"), "sections"),
                       (tr("Mittellinie: Glättung, Übergänge"), "mittellinie"), (tr("Segmentstart"), "start"),
                       (tr("Start / Ziel"), "start_ziel")),
                      ((tr("Randstein"), "randstein"), (tr("Grünstreifen"), "gruenstreifen"), (tr("Kiesbett"), "kiesbett"),
                       (tr("Barriere"), "barriere"), (tr("Entfernungstafeln (Bremspunkte)"), "bremsmarker")),
                      ((tr("Boxengasse"), "boxengasse"), (tr("Startaufstellung"), "startaufstellung"),
                       (tr("Sektoren (Automatik)"), "sektoren"), (tr("DRS (Automatik)"), "drs"), (tr("DRS: eigene Zonen"), "drs_zonen"),
                       (tr("Straight Mode: Zonen"), "xmode_zonen"), (tr("Kurvenerkennung"), "kurven"),
                       (tr("Ideallinie"), "ideallinie"))):
            if self.category.count():
                self.category.insertSeparator(self.category.count())
            for title, key in group:
                self.category.addItem(title, key)
        self.category.currentIndexChanged.connect(self._refresh_properties)
        right_layout.addWidget(self.category)
        self.scope_hint = QLabel(tr("Kursiv: Standard/geerbt. Leeres Feld entfernt Vorgabe."))
        self.scope_hint.setWordWrap(True)
        right_layout.addWidget(self.scope_hint)
        self.section_choice = QComboBox()
        self.section_choice.currentIndexChanged.connect(self._refresh_properties)
        right_layout.addWidget(self.section_choice)
        self.bank_panel = BankPanel()
        self.bank_panel.apply.connect(self.apply_bank)
        self.bank_panel.auto_requested.connect(self.auto_bank)
        self.bank_panel.flat_requested.connect(self.flatten_bank)
        right_layout.addWidget(self.bank_panel)
        self.properties = PropertyTable()
        self.properties.cellChanged.connect(lambda row, col: self.apply_properties() if col == 1 else None)
        right_layout.addWidget(self.properties, 1)
        section_actions = QHBoxLayout()
        self.add_section_button = QPushButton(tr("Abschnitt +"))
        self.remove_section_button = QPushButton(tr("Abschnitt −"))
        self.add_section_button.clicked.connect(self.add_section)
        self.remove_section_button.clicked.connect(self.remove_section)
        section_actions.addWidget(self.add_section_button)
        section_actions.addWidget(self.remove_section_button)
        right_layout.addLayout(section_actions)
        self.right_layout, self.right_stretch = right_layout, right_layout.count()
        right_layout.addStretch(0)                  # hält alles oben, solange die Tabelle ausgeblendet ist
        horizontal = QSplitter()
        horizontal.addWidget(left)
        horizontal.addWidget(center)
        horizontal.addWidget(right)
        horizontal.setSizes([240, 840, 360])
        horizontal.setStretchFactor(1, 1)
        self.horizontal_split, self.left_panel, self.right_panel = horizontal, left, right
        for splitter in (horizontal, center):
            splitter.splitterMoved.connect(lambda *args: self._save_layout())
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        self.project_heading = QLabel()
        self.project_heading.setContentsMargins(14, 6, 14, 6)
        self.project_heading.setTextFormat(Qt.RichText)
        content_layout.addWidget(self.project_heading)
        content_layout.addWidget(horizontal, 1)
        # Startbildschirm über der Karte (leeres Projekt)
        self.welcome = WelcomePanel(self.map)
        self.welcome.chosen.connect(self._welcome_choice)
        self.map.installEventFilter(self)
        self.setCentralWidget(content)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.statusBar().addWidget(self.status_label, 1)
        self.busy_label = QLabel(tr("Vorschau wird berechnet …"))
        self.busy_label.hide()
        self.statusBar().addPermanentWidget(self.busy_label)
        self.cancel_button = QPushButton(icon("abbrechen"), tr("Abbrechen"))
        self.cancel_button.setToolTip(tr("Laufende Vorschau abbrechen; deine Eingabe bleibt erhalten."))
        self.cancel_button.clicked.connect(self.cancel_preview)
        self.cancel_button.hide()
        self.statusBar().addPermanentWidget(self.cancel_button)
        self.issue_badge = QPushButton(tr("Noch nicht geprüft"))
        self.issue_badge.setFlat(True)
        self.issue_badge.setToolTip(tr("Prüfergebnis anzeigen (Prüfen: Strg+Enter)"))
        self.issue_badge.clicked.connect(lambda: self.results.setCurrentWidget(self.issue_panel))
        self.statusBar().addPermanentWidget(self.issue_badge)
        self.group_hint = QLabel(tr("Für Gruppenverschiebung erst ‚In Kontrollpunkte umwandeln‘"))
        self.group_hint.setWordWrap(True)
        self.statusBar().addWidget(self.group_hint, 1)
        self.group_convert_button = QPushButton(tr("In Kontrollpunkte umwandeln"))
        self.group_convert_button.clicked.connect(self.convert_to_points)
        self.statusBar().addPermanentWidget(self.group_convert_button)
        self.group_hint.hide()
        self.group_convert_button.hide()
        self.map.group_move_blocked.connect(lambda: self._group_status(force=True))
        self.map.zone_moved.connect(self._zone_moved)
        self.map.zone_clicked.connect(self._zone_clicked)
        self.map.zone_restore.connect(self._update_zone_overlay)
        self.map.turn_clicked.connect(self.show_turn)
        self.curve_table.cellDoubleClicked.connect(lambda row, column: self._curve_clicked(row, column, zoom=True))
        self.mode_hint = QLabel()
        self.mode_hint.setToolTip(tr("Aktives Werkzeug (V: Auswahl, P: Punkte)"))
        self.statusBar().addPermanentWidget(self.mode_hint)
        self.statusBar().setFixedHeight(40)

    #: Ab so vielen Punkten klappt die Liste zunächst ein: Sie würde die Spalte nur füllen, gewählt wird auf der Karte
    POINT_LIST_LIMIT = 300

    @staticmethod
    def _section_open(name, default):
        value = ansicht.settings().value(f"ansicht/bereich_{name}")
        return default if value is None else str(value).lower() in ("true", "1")

    @staticmethod
    def _section_saved(name, on):
        ansicht.settings().setValue(f"ansicht/bereich_{name}", on)

    def _display_summary(self):
        on = sum(box.isChecked() for box in self.feature_checks.values())
        self.display_section.set_summary(tr("{an} von {alle} an", an=on, alle=len(self.feature_checks)))

    def _point_section_toggled(self, on):
        if not getattr(self, "_point_section_auto", False):
            self.point_section_user = on
            self._section_saved("punkte", on)
        self._point_section_layout()

    def _point_section_layout(self):
        on = self.point_section.expanded()
        self.left_layout.setStretchFactor(self.point_section, 1 if on else 0)
        self.left_layout.setStretch(self.left_stretch, 0 if on else 1)

    def _point_section_update(self, count):
        """Punktliste einklappen, wenn sie lang ist; im Werkzeug Punkte und nach eigener Wahl gilt das nicht."""
        on = self.point_section_user if self.point_section_user is not None else count <= self.POINT_LIST_LIMIT
        self._point_section_auto = True
        self.point_section.set_expanded(on or getattr(getattr(self, "map", None), "mode", "") == "punkte")
        self._point_section_auto = False
        self.point_section.set_summary("")
        self._point_section_layout()

    @staticmethod
    def _heading(text):
        label = QLabel(text)
        label.setObjectName("heading")
        return label

    def eventFilter(self, obj, event):
        if obj is getattr(self, "map", None) and event.type() == QEvent.Resize:
            self._place_welcome()
        return super().eventFilter(obj, event)

    def _place_welcome(self):
        panel = self.welcome
        size = panel.sizeHint()
        width = min(max(size.width(), 560), self.map.width() - 40)
        height = min(size.height(), self.map.height() - 40)
        panel.setGeometry(max(20, (self.map.width() - width) // 2), max(20, (self.map.height() - height) // 2),
                          width, height)

    def _update_welcome(self):
        if not hasattr(self, "welcome"):
            return
        empty = self.document.ml.get("art", "kontrollpunkte") == "kontrollpunkte" and not self.document.points
        show = (empty and self.map.mode != "punkte" and not self.document.project.readonly
                and getattr(self, "welcome_feature", True))
        if show:
            self.welcome.set_recent(ansicht.recent_projects())
        if show:
            self._place_welcome()
            self.welcome.raise_()
        self.welcome.setVisible(show)

    def _welcome_choice(self, key):
        if key.startswith("zuletzt:"):
            self.load_project(key.split(":", 1)[1])
        elif key == "zeichnen":
            self.set_mode("punkte")
        elif key == "vorlage":
            self.new_from_template()
        elif key == "vorschlag":
            self.new_from_suggestion()
        elif key == "oeffnen":
            self.open_folder_dialog()
        elif key == "beispiel":
            try:
                root = toolkit_path(self.document.project.manifest.get("werkzeugkasten"))
                self.load_project(root / "strecke" / "beispiele" / "teststrecke.json")
            except (ValueError, OSError) as error:
                self.error(tr("Beispiel nicht gefunden: {error}", error=error))

    def _group_status(self, force=False):
        show = self.document.ml.get("art") == "segmente" and (force or len(self.document.selected_indices) > 1)
        whole = self.document.rotatable() == "strecke" and not force
        self.group_hint.setText(tr("Ganze Strecke gewählt: ziehen verschiebt, runder Griff dreht · Teilgruppen erst ‚In Kontrollpunkte umwandeln‘")
                                if whole else tr("Für Gruppenverschiebung erst ‚In Kontrollpunkte umwandeln‘"))
        self.group_hint.setVisible(show)
        self.group_convert_button.setVisible(show)
        self.group_convert_button.setEnabled(not self.document.project.readonly)
        self.status_label.setVisible(not show)
        self.mode_hint.setVisible(not show)

    @Slot()
    def _history_status(self):
        action = self.sender()
        if action is self.undo_action:
            action.setToolTip(tr("Letzte abgeschlossene Eingabe rückgängig machen.") if action.isEnabled() else tr("Noch keine Änderung zum Rückgängigmachen."))
        elif action is self.redo_action:
            action.setToolTip(tr("Änderung wiederholen.") if action.isEnabled() else tr("Noch keine rückgängig gemachte Änderung zum Wiederholen."))

    def _title(self, *args):
        p = self.document.project
        unsaved = not self.document.undo.isClean() or not p.folder
        name = p.raw.get("name", tr("Strecke"))
        self.setWindowTitle(f"{name}{' *' if unsaved else ''} – Mapeditor" + (tr(" (nur lesend)") if p.readonly else ""))
        state = ("<span style='color:#D6A94A'>● " + tr("ungespeicherte Änderungen") + "</span>" if unsaved and p.folder else
                 "<span style='color:#D6A94A'>● " + tr("noch nicht gespeichert") + "</span>" if unsaved else
                 "<span style='color:#5BD08A'>● " + tr("gespeichert") + "</span>")
        where = f"<span style='color:#8D9BA9'>&nbsp;&nbsp;{p.folder}</span>" if p.folder else ""
        readonly = ("&nbsp;&nbsp;<span style='color:#F07878'>" + tr("nur lesend: unbekannte Projektfassung") + "</span>"
                    if p.readonly else "")
        self.project_heading.setText(f"<b style='font-size:15px'>{name}</b>&nbsp;&nbsp;&nbsp;{state}{where}{readonly}")
        self.project_heading.setToolTip(tr("Entwurfsstand E{revision}", revision=self.document.revision))

    def _changed(self):
        if self.document.env_only:
            self._env_changed()
            return
        theme = self.document.project.manifest.get("thema", "dunkel")
        if self.map.dark != (theme != "hell"):
            self.set_theme(theme)
        self._refresh_objects()
        if self.properties.state() != QAbstractItemView.EditingState:
            self._refresh_properties()
        self.map.stale = self.preview_revision != self.document.revision
        self.map.rebuild(self.preview)
        self._show_input_profiles()
        self.cross.stale = True
        self.cross.update()
        self.analysis_timer.stop()
        self._mark_analysis_stale()
        self.status_label.setText(tr("Geändert · Vorschau veraltet, wird gleich neu berechnet"))
        if self.map.input["error"]:
            self.status_label.setText(tr("Eingabefehler: {error} · Eingabe bleibt bearbeitbar.", error=self.map.input['error']))
        self.set_issues([], {})
        self.issue_summary.setText(tr("Entwurf geändert · Prüfung folgt"))
        self.issue_badge.setText(tr("Prüfung folgt …"))
        self._title()
        if self.job:
            self.job.cancel()
        self.preview_pending = True
        self.preview_timer.start(400)
        self._update_height_bar()
        self._refresh_env_panel()

    def _height_data(self):
        """Höhenprofil bleibt nach einer Änderung auf der Stationsachse der letzten Vorschau (ab Ziel), solange die
        Objektzahl passt: die Griffe zeigen sofort die neuen Höhen, Zoom und Ausschnitt bleiben stehen."""
        preview = self.preview
        if preview and preview.get("pos") and len(preview.get("object_s") or []) == self.document.object_count                 and not self.map.input.get("error"):
            return preview
        return self.map.input

    def _draft_profiles(self):
        # Daten sofort übernehmen (Tests/Abfragen sehen den Entwurf), Neuzeichnen gedrosselt.
        for widget in (self.height, self.bank, self.cross):
            widget.preview = self._height_data() if widget is not self.cross else self.map.input
        if self.map.drag_point is None and self.map.rotate_center is None:
            self._show_input_profiles()
        elif not self.profile_timer.isActive():
            self.profile_timer.start()

    def _show_input_profiles(self):
        for widget in (self.height, self.bank, self.cross):
            widget.preview = self._height_data() if widget is not self.cross else self.map.input
            widget.update()
        index = self.document.selected
        if 0 <= index < len(self.map.input["object_s"]):
            self.set_station(self.map.input["object_s"][index])
        if self.map.input["error"]:
            self.status_label.setText(self.map.input["error"])

    def _refresh_objects(self):
        self.loading_list = True
        self.objects.clear()
        ml = self.document.ml
        art = ml.get("art", "unbekannt")
        if art == "kontrollpunkte":
            for i, point in enumerate(self.document.points):
                x, y, z = point_xyz(point)
                bank = point.get("querneigung_grad") if isinstance(point, dict) else None
                tilt = f"  ·  ∠ {zahl(abs(bank), 1)}°" if isinstance(bank, (int, float)) and abs(bank) >= .05 else ""
                where = self._object_place(i)
                item = QListWidgetItem(f"{i + 1:>4}   {where}X {x:.0f}  Z {z:.0f}  ·  {zahl(y, 1)} m{tilt}")
                item.setToolTip(tr("Kontrollpunkt {i}: X {x} m, Z {z} m, Höhe {y} m", i=i + 1, x=zahl(x, 2), z=zahl(z, 2), y=zahl(y, 2))
                                + (f" · {where.strip(' ·')}" if where else ""))
                self.objects.addItem(item)
        elif art == "segmente":
            kinds = {"gerade": tr("Gerade"), "bogen": tr("Bogen"), "klothoide": tr("Klothoide")}
            for i, segment in enumerate(ml.get("segmente", [])):
                def num(value, unit, decimals=0):
                    if not isinstance(value, (float, int)):
                        return "?"
                    return zahl(value, decimals) + "°" if unit == "°" else zahl(value, decimals, unit)
                kind = segment.get("typ", "?")
                if kind == "bogen":
                    measure = f"R {num(segment.get('radius'), 'm')} · {num(segment.get('winkel'), '°')} {choice_text(segment.get('richtung', ''))}".rstrip()
                elif kind == "klothoide":
                    measure = f"{num(segment.get('laenge'), 'm')} {choice_text(segment.get('richtung', ''))}".rstrip()
                else:
                    measure = num(segment.get("laenge"), "m")
                if isinstance(segment.get("hoehe_ende"), (int, float)):
                    measure += tr(" · Höhe {hoehe_ende}", hoehe_ende=zahl(segment['hoehe_ende'], 1))
                if isinstance(segment.get("querneigung_grad"), (int, float)) and abs(segment["querneigung_grad"]) >= .05:
                    measure += f" · ∠ {zahl(abs(segment['querneigung_grad']), 1)}°"
                title = segment.get("name") or kinds.get(kind, kind)
                where = self._object_place(i).strip(" ·")
                self.objects.addItem(f"{i + 1:02}  {title}{'  ·  ' + where if where else ''}\n      "
                                     f"{kinds.get(kind, kind) + ' · ' if segment.get('name') else ''}{measure}")
        self._sync_list_selection()
        state = tr("Runde geschlossen") if self.document.project.closed else tr("Runde offen")
        what = {"kontrollpunkte": tr("Kontrollpunkte"), "segmente": tr("Segmente")}.get(art, art)
        self.source_label.setText(f"{zahl(self.objects.count(), 0)} {what} · {state}")
        self.point_section.title = what.upper()
        self._point_section_update(self.objects.count())
        self.loading_list = False
        readonly = self.document.project.readonly
        editable = art in ("segmente", "kontrollpunkte") and not readonly
        reason = tr("Unbekannte Projektfassung: nur lesend.") if readonly else tr("CSV-Geometrie ist nur lesend.")
        self.add_button.setText(tr("+ Gerade") if art == "segmente" else tr("+ Punkt"))
        if hasattr(self, "close_action"):
            self._update_close_action(art, editable, reason)
        for button in (self.add_button, self.remove_button):
            button.setEnabled(editable and (button is self.add_button or self.document.selected >= 0))
            button.setToolTip(reason if not editable else tr("Zuerst ein Objekt wählen.") if not button.isEnabled() else tr("Wirkt sofort; Strg+Z macht rückgängig."))
        for i, button in enumerate(self.segment_buttons):
            button.setVisible(art == "segmente")
            button.setEnabled(editable and (i in (0, 3) or self.document.selected >= 0))
            button.setToolTip(reason if readonly else tr("Zuerst ein Segment wählen.") if not button.isEnabled() else tr("Wirkt sofort; Strg+Z macht rückgängig."))
        self._update_gap_button()
        self.mode_actions["punkte"].setVisible(art == "kontrollpunkte")
        self.mode_actions["punkte"].setEnabled(editable and art == "kontrollpunkte")
        self.mode_actions["punkte"].setToolTip(tr("Klick fügt einen Kontrollpunkt ein.") if editable else reason)
        if art != "kontrollpunkte" and hasattr(self, "map") and self.map.mode == "punkte":
            self.set_mode("auswahl", focus=False)
        self._group_status()
        self._update_welcome()

    def _sync_list_selection(self):
        # Eine einzige Auswahloperation statt setSelected je Zeile (Rahmenauswahl über hunderte Punkte).
        self.objects.setCurrentRow(self.document.selected, QItemSelectionModel.NoUpdate)
        model = self.objects.model()
        selection = QItemSelection()
        rows = sorted(i for i in self.document.selected_indices if i < self.objects.count())
        start = None
        for k, row in enumerate(rows):
            if start is None:
                start = row
            if k + 1 == len(rows) or rows[k + 1] != row + 1:
                selection.select(model.index(start, 0), model.index(row, 0))
                start = None
        self.objects.selectionModel().select(selection, QItemSelectionModel.ClearAndSelect)

    def _list_selected(self):
        if not self.loading_list:
            self.loading_list = True
            self.document.select_many({self.objects.row(item) for item in self.objects.selectedItems()}, self.objects.currentRow())
            self.loading_list = False

    def _list_current_selected(self, index):
        if not self.loading_list and index in self.document.selected_indices and index != self.document.selected:
            self.loading_list = True
            self.document.select_many(self.document.selected_indices, index)
            self.loading_list = False

    def _selection_changed(self, index):
        for profile in (self.height, self.bank):
            profile.selected = set(self.document.selected_indices)
            profile.update()
        self._update_height_bar()
        self._update_gap_button()
        if not self.loading_list:
            self.loading_list = True
            self._sync_list_selection()
            self.loading_list = False
        editable = not self.document.project.readonly and self.document.ml.get("art") in ("segmente", "kontrollpunkte")
        self.remove_button.setEnabled(editable and index >= 0)
        self.remove_button.setToolTip(tr("Wirkt sofort; Strg+Z macht rückgängig.") if self.remove_button.isEnabled() else tr("Zuerst ein Objekt wählen."))
        for i in (1, 2):
            button = self.segment_buttons[i]
            button.setEnabled(editable and index >= 0)
            button.setToolTip(tr("Wirkt sofort; Strg+Z macht rückgängig.") if button.isEnabled() else tr("Zuerst ein Segment wählen."))
        self._group_status()
        self._refresh_properties()
        self._cross_follow_selection()

    def _refresh_properties(self, *args):
        if not hasattr(self, "properties"):
            return
        raw = self.document.project.raw
        ml = self.document.ml
        category = self.category.currentData()
        entries = []
        defaults = self.defaults.get(category, {})
        values = raw.get(category, {})
        keys = list(defaults)
        if category == "object":
            index = self.document.selected
            if len(self.document.selected_indices) > 1:
                values, keys = {}, []
                if ml.get("art") == "kontrollpunkte":
                    heights = [point_xyz(self.document.points[i])[1] for i in sorted(self.document.selected_indices)]
                    values = {"y": heights[0] if all(y == heights[0] for y in heights) else "—"}
                    keys = ["y"]
            elif ml.get("art") == "kontrollpunkte" and 0 <= index < len(self.document.points):
                point = self.document.points[index]
                x, y, z = point_xyz(point)
                values = dict(point) if isinstance(point, dict) else {}
                values.update(x=x, y=y, z=z)
                keys = ["x", "z", "y"] + self._width_keys(values)
            elif ml.get("art") == "segmente" and 0 <= index < len(ml.get("segmente", [])):
                values = dict(ml["segmente"][index])
                shape_keys = {"gerade": ["laenge"], "bogen": ["radius", "winkel", "richtung"] if values.get("winkel") is not None else ["radius", "laenge", "richtung"], "klothoide": ["laenge", "richtung", "radius_anfang", "radius_ende"]}
                keys = ["typ", "name"] + shape_keys.get(values.get("typ"), []) + ["hoehe_ende", "steigung_prozent"] + self._width_keys(values)
                if values.get("typ") != "klothoide":
                    keys.append("uebergang")
            else:
                values, keys = {}, []
            if keys and len(self.document.selected_indices) <= 1:
                if not self.bank_feature:      # sonst steht die Querneigung im Querneigungsfeld darüber
                    keys.append("querneigung_grad")
                values["asymmetrisch"] = True if "breite_links" in keys else None
                halves = self._current_halves(index)               # Breite hier (auch aus Profilabschnitten)
                defaults = dict(defaults, asymmetrisch=False, breite=round(sum(halves), 2) if halves else None)
        elif category == "mittellinie":
            keys = ["abtastung_m", "approximation_m", "glaettung_m", "hoehen_glaettung_m", "schliessen", "max_schliessfehler_m"]
            values = dict(ml)
            if ml.get("art") == "segmente":
                # Klothoiden-Übergänge: verschachtelte Felder als „uebergaenge.<name>“ zeigen
                rules = ml.get("uebergaenge") if isinstance(ml.get("uebergaenge"), dict) else {}
                fallback = defaults.get("uebergaenge") if isinstance(defaults.get("uebergaenge"), dict) else TRANSITION_DEFAULTS
                defaults = dict(defaults)
                for name in TRANSITION_DEFAULTS:
                    keys.append("uebergaenge." + name)
                    values["uebergaenge." + name] = rules.get(name)
                    defaults["uebergaenge." + name] = fallback.get(name, TRANSITION_DEFAULTS[name])
        elif category == "start":
            keys = ["x", "z", "hoehe", "richtung_grad"] if ml.get("art") == "segmente" else []
            values = ml.get("start", {})
            defaults = {key: 0.0 for key in keys}
        elif category in LISTS:
            spec = LISTS[category]
            items = self._list_items(raw, category)
            previous = self.section_choice.currentIndex()
            titles = [spec["title"](i, z) for i, z in enumerate(items)]
            if [self.section_choice.itemText(i) for i in range(self.section_choice.count())] != titles:
                self.section_choice.blockSignals(True)
                self.section_choice.clear()
                self.section_choice.addItems(titles)
                self.section_choice.setCurrentIndex(max(0, min(previous, len(items) - 1)))
                self.section_choice.blockSignals(False)
            i = self.section_choice.currentIndex()
            values = items[i] if 0 <= i < len(items) else {}
            keys = list(spec["keys"]) if items else []
            defaults = {"partial": True, "partial_versatz_m": 60.0} if category == "xmode_zonen" else {}
        if not keys and isinstance(values, dict) and category not in ("object", "start") and category not in LISTS:
            keys = list(values)
        for key in keys:
            if key in ("art", "abschnitte", "csv_achsen", "umkehren", "zonen"):
                continue
            if isinstance(defaults.get(key), dict):
                continue
            entries.append((key, values.get(key), defaults.get(key), LABELS.get(key, key)))
        self.properties.populate(entries, self.document.project.readonly)
        listed = category in LISTS
        self.section_choice.setVisible(listed)
        for button in (self.add_section_button, self.remove_section_button):
            button.setVisible(listed)
            button.setEnabled(not self.document.project.readonly)
            button.setToolTip(tr("Unbekannte Projektfassung: nur lesend.") if self.document.project.readonly else tr("Wirkt sofort; Strg+Z macht rückgängig."))
        if listed:
            self.add_section_button.setText(tr(LISTS[category]["add"]))
            self.remove_section_button.setText(tr(LISTS[category]["remove"]))
        self.remove_section_button.setEnabled(not self.document.project.readonly and self.section_choice.count() > 0)
        if self.section_choice.count() == 0:
            self.remove_section_button.setToolTip(tr("Zuerst eine(n) {noun} anlegen.", noun=tr(LISTS.get(category, LISTS['sections'])['noun'])))
        text = tr("Doppelklick zum Ändern, Enter übernimmt. Kursiv = Standardwert.")
        if category == "object":
            if len(self.document.selected_indices) > 1:
                text = tr("{anzahl} Objekte gewählt. ", anzahl=len(self.document.selected_indices)) + (tr("Höhe für alle setzen; — = gemischte Werte. Drehen: runder Griff über dem Auswahlkasten.") if self.document.points
                    else tr("Ganze Strecke: auf der Karte ziehen = verschieben, runder Griff = drehen.") if self.document.rotatable() == "strecke"
                    else tr("Gruppenverschiebung: erst in Kontrollpunkte umwandeln."))
            elif self.document.selected < 0:
                text = tr("Nichts gewählt – Objekt in der Karte oder Liste anklicken.")
        if category == "sections":
            text += tr(" Meter ab Eingabeursprung; bei Überlappung gilt der spätere Abschnitt.")
        if category == "boxengasse":
            text += tr(" Links/rechts gelten in Fahrtrichtung.")
        if category == "ideallinie":
            text = (tr("Ideallinie des Generators (Fahrlinie, Bremslinien, KI-Profil). Standard seit 25.10.2026: iterativ "
                    "mit feiner Rückprojektion und Mindestzeit-Feinschliff. Kursiv = Standard."))
        if category == "bremsmarker":
            text = (tr("Entfernungstafeln (50/100/150/200/250 m) vor Kurven mit starker Bremsung, auf der Seite der "
                    "Ideallinie. 50 und 100 m ab dem Mindestabbau, je weitere 50 m Tafel 50 km/h mehr Abbau. Erscheinen "
                    "beim Bauen im Spiel."))
        if category == "drs_zonen":
            text = (tr("Eigene DRS-Zonen ersetzen die Automatik. Meter ab Ziellinie in Fahrtrichtung. "
                    "Neue Zone entsteht am Stationscursor; auf der Karte die Quadrate ziehen."))
        if category == "xmode_zonen":
            text = (tr("Straight Mode (2026er Aero) – eigene Zonen ersetzen die Träger-Zonen. Meter ab Ziel; über die "
                    "Ziellinie erlaubt. Partial-Zone wie in den Originalen (Standard 60 m nach Beginn). "
                    "Neue Zone am Stationscursor; auf der Karte die Quadrate ziehen."))
        self.scope_hint.setText(text)
        filled = self.properties.rowCount() > 0                         # leere Tabelle zeigt nichts Nützliches
        self.properties.setVisible(filled)
        self.right_layout.setStretch(self.right_stretch, 0 if filled else 1)
        self._refresh_bank_panel()
        self._update_zone_overlay()
        highlight = {"start_ziel": "start", "startaufstellung": "grid", "sektoren": "sektoren"}.get(category)
        if hasattr(self, "map") and self.map.highlight != highlight:
            self.map.highlight = highlight
            self.map.viewport().update()

    # ------------------------------------------------------------------ Höhenprofil bearbeiten
    def _input_geometry(self):
        revision, geometry = self._geometry_cache
        if revision != self.document.revision or geometry is None:
            geometry = input_geometry(self.document)
            self._geometry_cache = (self.document.revision, geometry)
        return geometry

    def _height_nodes(self, data):
        """Griffe für das Höhenprofil: Kontrollpunkte bzw. Segmentenden an ihrer Station in ``data``."""
        ml = self.document.ml
        art = ml.get("art")
        self.height.editable = not self.document.project.readonly and not ml.get("umkehren")
        if art not in ("segmente", "kontrollpunkte"):
            return []
        stations, length = data.get("object_s") or [], float(data["length"])
        if art == "kontrollpunkte":
            points = self.document.points
            if len(stations) != len(points):
                return []
            return [{"index": i, "s": float(stations[i]), "y": point_xyz(p)[1]} for i, p in enumerate(points)]
        geometry = self._input_geometry()
        segments = geometry["segments"]
        if geometry["error"] or len(stations) != len(segments) or not segments:
            return []
        n = len(segments)
        nodes = []
        for i, entry in enumerate(segments):
            s = float(stations[i + 1]) if i + 1 < n else (length if data.get("input") else float(stations[0]))
            nodes.append({"index": i, "s": s, "y": float(entry["end"][1])})
        return nodes

    def _update_height_bar(self):
        self._update_bank_bar()
        if not hasattr(self, "height_info"):
            return
        nodes = {n["index"]: n for n in self.height.nodes()} if self.height.preview else {}
        chosen = sorted((i for i in self.document.selected_indices if i in nodes), key=lambda i: nodes[i]["s"])
        editable = self.height.editable and bool(nodes)
        need = {"uebergang": 3, "rampe": 3, "eben": 2, "glaetten": 3}
        for key, button in self.height_buttons.items():
            button.setEnabled(editable and len(chosen) >= need[key])
        if not nodes:
            self.height_info.setText(tr("Höhen bearbeiten: geschlossene Runde mit Vorschau nötig."))
        elif len(chosen) < 2:
            self.height_info.setText(tr("Waagerecht ziehen wählt einen Bereich A→B · senkrecht ziehen ändert die Höhe."))
        else:
            a, b = nodes[chosen[0]], nodes[chosen[-1]]
            run = b["s"] - a["s"]
            grade = (b["y"] - a["y"]) / run * 100 if run > 1e-6 else 0.0
            self.height_info.setText(tr("A→B: {anzahl} Punkte · {run} · Höhe {y} → {y2} m · Ø {grade} %", anzahl=len(chosen), run=zahl(run, 0, 'm'), y=zahl(a['y'], 1), y2=zahl(b['y'], 1), grade=zahl(grade, 1)))

    def _bank_nodes(self, data):
        """Griffe für das Querneigungsprofil: Kontrollpunkte an ihrer Station, Segmente in ihrer Mitte; Wert = wirksame
        Querneigung der Eingabe (``neigung.current_values``). Zwischengespeichert je Bearbeitungsstand."""
        ml = self.document.ml
        art = ml.get("art")
        self.bank.editable = not self.document.project.readonly and not ml.get("umkehren")
        if art not in ("segmente", "kontrollpunkte"):
            return []
        key = (self.document.revision, self.document.undo.index(), id(data), len(data.get("object_s") or []))
        if self._bank_nodes_cache[0] == key:
            return self._bank_nodes_cache[1]
        stations, length = data.get("object_s") or [], float(data["length"])
        values = neigung.current_values(self.document.project.raw, self.document.project.closed)
        nodes = []
        if values and len(stations) == len(values):
            if art == "kontrollpunkte":
                nodes = [{"index": i, "s": float(s), "y": float(v)} for i, (s, v) in enumerate(zip(stations, values))]
            else:
                closed, n = self.document.project.closed, len(values)
                for i, v in enumerate(values):
                    a = float(stations[i])
                    b = float(stations[(i + 1) % n]) if i + 1 < n or closed else length
                    if b <= a:
                        b += length
                    mid = (a + b) / 2
                    nodes.append({"index": i, "s": mid % length if closed else mid, "y": float(v)})
        self._bank_nodes_cache = (key, nodes)
        return nodes

    def _update_bank_bar(self):
        if not hasattr(self, "bank_info"):
            return
        nodes = {n["index"]: n for n in self.bank.nodes()} if self.bank.preview else {}
        chosen = sorted((i for i in self.document.selected_indices if i in nodes), key=lambda i: nodes[i]["s"])
        editable = self.bank.editable and bool(nodes)
        need = {"uebergang": 3, "rampe": 3, "eben": 2, "glaetten": 3}
        for key, button in self.bank_buttons.items():
            button.setEnabled(editable and len(chosen) >= need[key])
        if not nodes:
            self.bank_info.setText(tr("Querneigung bearbeiten: geschlossene Runde mit Vorschau nötig."))
        elif len(chosen) < 2:
            self.bank_info.setText(tr("Waagerecht ziehen wählt einen Bereich A→B · senkrecht ziehen ändert die Querneigung."))
        else:
            a, b = nodes[chosen[0]], nodes[chosen[-1]]
            self.bank_info.setText(tr("A→B: {anzahl} Punkte · {run} · Querneigung {y}° → {y2}°", anzahl=len(chosen),
                                      run=zahl(b["s"] - a["s"], 0, "m"), y=zahl(a["y"], 1), y2=zahl(b["y"], 1)))

    def set_banks(self, changes):
        """Neue Querneigung {Objektindex: Grad} in einem Undo-Schritt (Begrenzung wie im Querneigungsfeld)."""
        art = self.document.ml.get("art")
        if not changes or self.document.project.readonly or art not in ("segmente", "kontrollpunkte"):
            return
        closed = self.document.project.closed
        target = {i: max(-BANK_LIMIT, min(BANK_LIMIT, round(float(v), 2))) for i, v in changes.items()}
        count = len(target)
        self.document.edit(tr("Querneigung ändern") if count == 1 else tr("Querneigung ändern ({count} Punkte)", count=count),
                           lambda raw, meta: neigung.set_values(raw, target, closed))
        self.status_label.setText(trn(count, "Querneigung von {n} Punkt geändert · Strg+Z macht rückgängig.",
                                      "Querneigung von {n} Punkten geändert · Strg+Z macht rückgängig."))

    def set_heights(self, changes):
        """Neue Eingabehöhen {Objektindex: m} in einem Undo-Schritt. Segmente: alle Endhöhen werden fest
        eingetragen (statt relativer Steigung), damit sich die übrigen Punkte nicht mitbewegen."""
        art = self.document.ml.get("art")
        if not changes or self.document.project.readonly or art not in ("segmente", "kontrollpunkte"):
            return
        if art == "kontrollpunkte":
            def operation(raw, meta):
                points = raw["mittellinie"]["punkte"]
                for i, y in changes.items():
                    points[i] = set_point(points[i], y=round(float(y), 3))
        else:
            ends = [float(entry["end"][1]) for entry in self._input_geometry()["segments"]]

            def operation(raw, meta):
                segments = raw["mittellinie"]["segmente"]
                for j, segment in enumerate(segments[:len(ends)]):
                    if segment.get("hoehe_ende") is None:
                        segment["hoehe_ende"] = round(ends[j], 3)
                    segment.pop("steigung_prozent", None)
                for i, y in changes.items():
                    segments[i]["hoehe_ende"] = round(float(y), 3)
        count = len(changes)
        self.document.edit(tr("Höhe ändern") if count == 1 else tr("Höhe ändern ({count} Punkte)", count=count), operation)
        self.status_label.setText(trn(count, "Höhe von {n} Punkt geändert · Strg+Z macht rückgängig.",
                                      "Höhe von {n} Punkten geändert · Strg+Z macht rückgängig."))

    def height_tool(self, tool):
        self._profile_tool(self.height, self.set_heights, tool)

    def bank_tool(self, tool):
        self._profile_tool(self.bank, self.set_banks, tool)

    def _profile_tool(self, view, apply, tool):
        """Werkzeuge Übergang/Rampe/Gleich/Glätten/Eingeben für Höhen- und Querneigungsprofil (``apply`` schreibt)."""
        nodes = {n["index"]: n for n in view.nodes()}
        chosen = sorted((i for i in self.document.selected_indices if i in nodes), key=lambda i: nodes[i]["s"])
        if not chosen:
            return
        ordered = sorted(nodes.values(), key=lambda n: n["s"])
        rank = {n["index"]: k for k, n in enumerate(ordered)}
        if tool == "uebergang" and len(chosen) >= 3:
            apply(smooth_transition(ordered, rank[chosen[0]], rank[chosen[-1]]))
            return
        if tool == "rampe" and len(chosen) >= 3:
            a, b = nodes[chosen[0]], nodes[chosen[-1]]
            run = max(b["s"] - a["s"], 1e-6)
            apply({i: a["y"] + (b["y"] - a["y"]) * (nodes[i]["s"] - a["s"]) / run for i in chosen[1:-1]})
        elif tool == "eben" and len(chosen) >= 2:
            mean = sum(nodes[i]["y"] for i in chosen) / len(chosen)
            apply({i: mean for i in chosen})
        elif tool == "glaetten":
            if len(chosen) >= 3:
                apply(smooth_range(ordered, rank[chosen[0]], rank[chosen[-1]]))
            else:                                   # einzelner Punkt: mit den Nachbarn mitteln
                closed = self.document.project.closed or self.document.ml.get("art") == "segmente"
                new = {}
                for i in chosen:
                    k = rank[i]
                    if not closed and k in (0, len(ordered) - 1):
                        continue
                    before, after = ordered[(k - 1) % len(ordered)], ordered[(k + 1) % len(ordered)]
                    new[i] = (before["y"] + 2 * nodes[i]["y"] + after["y"]) / 4
                apply(new)
        elif tool == "eingeben":
            from PySide6.QtWidgets import QInputDialog
            first = nodes[chosen[0]]["y"]
            if view.kind == "height":
                value, ok = QInputDialog.getDouble(self, tr("Höhe eingeben"),
                                                   trn(len(chosen), "Höhe in m für {n} Punkt:", "Höhe in m für {n} Punkte:"),
                                                   round(first, 2), -500.0, 1500.0, 2)
            else:
                value, ok = QInputDialog.getDouble(
                    self, tr("Querneigung eingeben"),
                    trn(len(chosen), "Querneigung in ° für {n} Punkt (+ = links höher):",
                        "Querneigung in ° für {n} Punkte (+ = links höher):"),
                    round(first, 1), -BANK_LIMIT, BANK_LIMIT, 1)
            if ok:
                apply({i: value for i in chosen})

    # ------------------------------------------------------------------ Querneigung
    def _bank_context(self):
        raw, closed = self.document.project.raw, self.document.project.closed
        try:
            return neigung.current_values(raw, closed), neigung.turns(raw, closed)
        except (ValueError, TypeError, KeyError, ZeroDivisionError):
            return [], []

    def _refresh_bank_panel(self):
        if not hasattr(self, "bank_panel"):
            return
        art = self.document.ml.get("art")
        shown = self.category.currentData() == "object" and art in ("segmente", "kontrollpunkte") and self.bank_feature
        shown = shown and self.map.mode not in ("umgebung", "streuen")    # dort brauchen Auswahl und Gelände den Platz
        # ohne Auswahl nur im Reiter Querneigung; „Alle Kurven automatisch“ steht auch im Menü Bearbeiten
        shown = shown and bool(self.document.selected_indices or self.results.currentWidget() is self.bank_page)
        self.bank_panel.setVisible(shown)
        if not shown:
            return
        values, turns = self._bank_context()
        chosen = sorted(i for i in self.document.selected_indices if i < len(values))
        self.bank_panel.show_selection([values[i] for i in chosen], [turns[i][0] for i in chosen], len(chosen),
                                       not self.document.project.readonly)

    def apply_bank(self, magnitude, side):
        values, turns = self._bank_context()
        chosen = [i for i in sorted(self.document.selected_indices) if i < len(turns)]
        if not chosen:
            return
        if side in ("innen", "aussen") and magnitude and not any(turns[i][0] for i in chosen):
            self.status_label.setText(tr("Geraden haben keine Kurveninnenseite – Seite „links höher“ oder "
                                      "„rechts höher“ wählen."))
            return
        target = {i: neigung.signed(magnitude, side, turns[i][0]) for i in chosen}
        if all(abs(values[i] - v) < 1e-6 for i, v in target.items()):
            return
        closed = self.document.project.closed
        noun = tr("Querneigung") if len(chosen) == 1 else tr("Querneigung ({anzahl} Objekte)", anzahl=len(chosen))
        what = tr("flach") if not magnitude else f"{zahl(magnitude, 1)}° {tr(neigung.SIDES[side])}"
        self.document.edit(f"{noun}: {what}", lambda raw, meta: neigung.set_values(raw, target, closed))

    def auto_bank(self, maximum):
        if self.document.ml.get("art") not in ("segmente", "kontrollpunkte"):
            return
        closed = self.document.project.closed
        values = neigung.auto_values(self.document.project.raw, closed, maximum)
        self.document.edit(tr("Querneigung: alle Kurven automatisch (bis {maximum}°)", maximum=zahl(maximum, 1)),
                           lambda raw, meta: neigung.set_values(raw, values, closed))
        banked = sum(1 for v in values.values() if abs(v) >= .05)
        self.status_label.setText(trn(banked, "{n} Objekt zur Kurveninnenseite geneigt (bis {maximum}°) · Strg+Z macht rückgängig.",
                                      "{n} Objekte zur Kurveninnenseite geneigt (bis {maximum}°) · Strg+Z macht rückgängig.",
                                      maximum=zahl(maximum, 1)))

    def flatten_bank(self):
        self.document.edit(tr("Querneigung: ganze Strecke flach"), lambda raw, meta: neigung.flatten(raw))

    @staticmethod
    def _width_keys(values):
        """Breite eines Objekts: Gesamtbreite; Halbbreiten nur, wenn links/rechts verschieden sind."""
        links, rechts = values.get("breite_links") is not None, values.get("breite_rechts") is not None
        if links and rechts:                   # beide gesetzt: die Gesamtbreite wirkt nicht mehr
            return ["asymmetrisch", "breite_links", "breite_rechts"]
        if links or rechts:
            return ["breite", "asymmetrisch", "breite_links", "breite_rechts"]
        return ["breite", "asymmetrisch"]

    def _current_halves(self, index):
        """Halbbreiten (links, rechts) am Objekt ``index`` laut Vorschau; None ohne passende Vorschau."""
        p = self.preview or {}
        stations = p.get("object_s") or []
        if not 0 <= index < len(stations) or not p.get("wl") or not p.get("s"):
            return None
        i = min(range(len(p["s"])), key=lambda k: abs(p["s"][k] - stations[index]))
        return round(float(p["wl"][i]), 2), round(float(p["wr"][i]), 2)

    def _set_asymmetric(self, raw, target, on, halves):
        """Schalter „asymmetrische Breite“: Halbbreiten anlegen (Startwert: Breite am Objekt laut Vorschau,
        sonst die halbe Gesamtbreite) bzw. zur Gesamtbreite zusammenführen."""
        if halves is None:
            fahrbahn = raw.get("fahrbahn") if isinstance(raw.get("fahrbahn"), dict) else {}
            half = (target.get("breite") or fahrbahn.get("breite") or self.defaults.get("fahrbahn", {}).get("breite")
                    or ROAD_WIDTH) / 2
            halves = (half, half)
        links, rechts = target.get("breite_links"), target.get("breite_rechts")
        if on:
            target["breite_links"] = links if links is not None else halves[0]
            target["breite_rechts"] = rechts if rechts is not None else halves[1]
        elif links is not None or rechts is not None:
            total = (links if links is not None else halves[0]) + (rechts if rechts is not None else halves[1])
            target.pop("breite_links", None)
            target.pop("breite_rechts", None)
            target["breite"] = round(total, 3)

    @staticmethod
    def update_fields(target, changes):
        for key, value in changes.items():
            if value is None:
                target.pop(key, None)
            else:
                target[key] = value

    def apply_properties(self):
        try:
            changes = self.properties.changes()
            category = self.category.currentData()
            index = self.document.selected
            section = self.section_choice.currentIndex()
            if category == "object" and len(self.document.selected_indices) > 1:
                if "y" in changes:
                    if changes["y"] is None:
                        raise ValueError(tr("Koordinaten dürfen nicht leer sein."))
                    self.document.move_selection(y=changes["y"])
                    QTimer.singleShot(0, self._refresh_properties)
                return
            halves = self._current_halves(index) if "asymmetrisch" in changes else None

            def operation(raw, meta):
                if category == "object":
                    ml = raw["mittellinie"]
                    fields = {key: value for key, value in changes.items() if key != "asymmetrisch"}
                    if ml["art"] == "kontrollpunkte":
                        point = ml["punkte"][index]
                        coords = {key: fields[key] for key in ("x", "y", "z") if key in fields}
                        if any(value is None for value in coords.values()):
                            raise ValueError(tr("Koordinaten dürfen nicht leer sein."))
                        updated = set_point(point, **coords)
                        attrs = {key: value for key, value in fields.items() if key not in coords}
                        if (attrs or "asymmetrisch" in changes) and not isinstance(updated, dict):
                            x, y, z = point_xyz(updated)
                            updated = {"x": x, "y": y, "z": z}
                        if attrs:
                            self.update_fields(updated, attrs)
                        if "asymmetrisch" in changes:
                            self._set_asymmetric(raw, updated, bool(changes["asymmetrisch"]), halves)
                        ml["punkte"][index] = updated
                    else:
                        if "typ" in changes and changes["typ"] != ml["segmente"][index].get("typ"):
                            self.document.change_type(ml["segmente"][index], changes["typ"])
                        self.update_fields(ml["segmente"][index], fields)
                        if "asymmetrisch" in changes:
                            self._set_asymmetric(raw, ml["segmente"][index], bool(changes["asymmetrisch"]), halves)
                elif category in LISTS:
                    self.update_fields(self._list_items(raw, category, create=True)[section], changes)
                elif category == "start":
                    self.update_fields(raw["mittellinie"].setdefault("start", {}), changes)
                elif category == "mittellinie":
                    nested = {key.split(".", 1)[1]: value for key, value in changes.items() if key.startswith("uebergaenge.")}
                    self.update_fields(raw["mittellinie"], {key: value for key, value in changes.items() if not key.startswith("uebergaenge.")})
                    if nested:
                        rules = raw["mittellinie"].get("uebergaenge")
                        rules = dict(rules) if isinstance(rules, dict) else {}
                        self.update_fields(rules, nested)
                        if rules:
                            raw["mittellinie"]["uebergaenge"] = rules
                        else:
                            raw["mittellinie"].pop("uebergaenge", None)
                else:
                    self.update_fields(raw.setdefault(category, {}), changes)
            if changes:
                self.document.edit(tr("Eigenschaft ändern"), operation)
                # Während Qt den Zell-Editor schließt, dessen Widgets nicht ersetzen.
                self.properties.blockSignals(True)
                for row, (key, actual, fallback, label) in enumerate(self.properties.entries):
                    if key in changes:
                        self.properties.item(row, 1).setData(Qt.UserRole, self.properties.item(row, 1).text())
                        self.properties.entries[row] = (key, changes[key], fallback, label)
                self.properties.blockSignals(False)
                QTimer.singleShot(0, self._refresh_after_edit)
        except (ValueError, TypeError, KeyError, IndexError) as error:
            self.status_label.setText(tr("Ungültige Eingabe: {error} · Feld korrigieren; Eingabe bleibt erhalten.", error=error))

    def _refresh_after_edit(self):
        if self.properties.state() != QAbstractItemView.EditingState and not self.properties.pending():
            self._refresh_properties()

    @staticmethod
    def _list_items(raw, category, create=False):
        first, second = LISTS[category]["path"]
        if create:
            return raw.setdefault(first, {}).setdefault(second, [])
        return (raw.get(first) or {}).get(second) or []

    def _track_length(self):
        if self.preview and self.preview.get("length"):
            return float(self.preview["length"])
        return float(self.map.input.get("length") or 1000.0)

    def _new_list_item(self, category):
        if category == "sections":
            return {"von_s": 0, "bis_s": 100}
        L = self._track_length()
        s = float(self.map.station or 0.0) % L
        if category == "drs_zonen":
            return {"erkennung_m": round((s - 150) % L, 1), "aktivierung_m": round(s, 1), "ende_m": round((s + 500) % L, 1)}
        return {"von_m": round(s, 1), "bis_m": round((s + 500) % L, 1)}

    def add_section(self):
        category = self.category.currentData()
        if category not in LISTS:
            return
        item = self._new_list_item(category)
        self.document.edit(tr("{noun} anlegen", noun=tr(LISTS[category]['noun'])), lambda raw, meta: self._list_items(raw, category, create=True).append(item))
        self._refresh_properties()
        self.section_choice.setCurrentIndex(self.section_choice.count() - 1)

    def remove_section(self):
        category = self.category.currentData()
        index = self.section_choice.currentIndex()
        if category in LISTS and index >= 0:
            self.document.edit(tr("{noun} löschen", noun=tr(LISTS[category]['noun'])), lambda raw, meta: self._list_items(raw, category).pop(index))

    # ------------------------------------------------------------------ DRS / Straight Mode auf der Karte
    def _update_zone_overlay(self):
        if not hasattr(self, "map"):
            return
        raw = self.document.project.raw
        zones = []
        own_drs = self._list_items(raw, "drs_zonen")
        own_x = self._list_items(raw, "xmode_zonen")
        preview_zones = (self.preview or {}).get("zones") or {}
        try:
            if own_drs:
                for i, z in enumerate(own_drs):
                    zones.append({"art": "drs", "index": i, "a": float(z["aktivierung_m"]), "b": float(z["ende_m"]),
                                  "det": float(z["erkennung_m"]), "auto": False, "label": f"DRS {i + 1}"})
            else:
                for i, z in enumerate(preview_zones.get("drs") or []):
                    zones.append({"art": "drs", "index": i, "a": z["aktivierung_s_m"], "b": z["ende_s_m"],
                                  "det": z["erkennung_s_m"], "auto": True, "label": f"DRS {i + 1}"})
            if own_x:
                for i, z in enumerate(own_x):
                    zones.append({"art": "xmode", "index": i, "a": float(z["von_m"]), "b": float(z["bis_m"]),
                                  "auto": False, "label": f"Straight Mode {i + 1}"})
            else:
                for i, z in enumerate(preview_zones.get("xmode") or []):
                    zones.append({"art": "xmode", "index": i, "a": z["von_s_m"], "b": z["bis_s_m"],
                                  "auto": z.get("quelle") != "eingabe", "label": f"Straight Mode {i + 1}"})
            box = self._pit_settings()
            if box and box.get("aktiv") and self.preview:
                length = float(self.preview["length"])
                zones.append({"art": "box", "index": 0, "a": (length - float(box["einfahrt_vor_start_m"])) % length,
                              "b": float(box["ausfahrt_nach_start_m"]) % length, "auto": False,
                              "label": tr("Boxengasse"), "seite": box.get("seite", "rechts")})
        except (KeyError, TypeError, ValueError):
            pass
        self.map.zones = zones
        category = self.category.currentData() if hasattr(self, "category") else None
        index = self.section_choice.currentIndex() if hasattr(self, "section_choice") else -1
        art = {"drs_zonen": "drs", "xmode_zonen": "xmode"}.get(category)
        self.map.zone_selected = (art, index) if art and index >= 0 else ("box", 0) if category == "boxengasse" else None
        self.map.viewport().update()

    def _pit_settings(self):
        """Boxengasse aus Definition und Standardwerten."""
        merged = dict((self.defaults or {}).get("boxengasse") or {})
        merged.update(self.document.project.raw.get("boxengasse") or {})
        return merged

    def _zone_moved(self, art, index, field, station):
        if art == "box":
            length = float(self.preview["length"]) if self.preview else 0.0
            if length <= 0:
                return
            value = round((length - station) % length if field == "einfahrt_vor_start_m" else station % length, 1)
            self.document.edit(tr("Boxengasse verschieben"),
                               lambda raw, meta: raw.setdefault("boxengasse", {}).__setitem__(field, value))
            self._refresh_properties()
            return
        category = ZONE_CATEGORY[art]
        items = self._list_items(self.document.project.raw, category)
        if not 0 <= index < len(items):
            return
        self.document.edit(tr("{noun} verschieben", noun=tr(LISTS[category]['noun'])),
                           lambda raw, meta: self._list_items(raw, category)[index].__setitem__(field, station))
        self._refresh_properties()

    def _zone_clicked(self, art, index):
        if art == "box":
            self.category.setCurrentIndex(self.category.findData("boxengasse"))
            self.status_label.setText(tr("Boxengasse gewählt: Quadrate an Ein- und Ausfahrt auf der Karte ziehen oder "
                                      "Werte rechts ändern."))
            self._refresh_properties()
            return
        category = ZONE_CATEGORY[art]
        own = self._list_items(self.document.project.raw, category)
        self.category.setCurrentIndex(self.category.findData(category))
        if own and 0 <= index < len(own):
            self.section_choice.setCurrentIndex(index)
        self.status_label.setText(tr("Automatik-Zone: mit „+“ eine eigene Zone anlegen, um sie zu verändern.")
                                  if not own else tr("Zone gewählt: Quadrate auf der Karte ziehen oder Werte rechts ändern."))
        self._refresh_properties()

    def add_object(self):
        self.document.insert()

    def remove_object(self):
        self.document.delete()

    def _update_gap_button(self):
        """Der Knopf ist aktiv, wenn ein Klick etwas füllen kann: geschlossene Runde (gefüllt wird entlang der berechneten
        Strecke) und eine große Lücke oder zwei gewählte Nachbarpunkte; sonst nennt er den Grund."""
        if not hasattr(self, "gap_button"):
            return
        doc = self.document
        points = doc.ml.get("art", "kontrollpunkte") == "kontrollpunkte"
        targets = doc.gap_targets() if points else {}
        reason = (tr("Unbekannte Projektfassung: nur lesend.") if doc.project.readonly else
                  tr("Erst die Runde schließen: Gefüllt wird entlang der berechneten Strecke.") if not doc.project.closed else
                  tr("Bei umgekehrter Fahrtrichtung nicht möglich.") if doc.project.raw["mittellinie"].get("umkehren") else
                  tr("Keine großen Lücken. Zwei benachbarte Punkte wählen füllt genau diese Lücke.") if not targets else "")
        self.gap_button.setVisible(points)
        self.gap_button.setEnabled(points and not reason)
        self.gap_button.setText(tr("Lücken füllen ({anzahl})", anzahl=len(targets)) if targets else tr("Lücken füllen"))
        self.gap_button.setToolTip(reason or tr("Füllt Lücken entlang der sichtbaren Strecke (Generatorvorschau) mit Punkten im "
                                                "üblichen Abstand. Zwei benachbarte Punkte gewählt: nur diese Lücke. Strg+Z: rückgängig."))

    def fill_gaps(self):
        """Lücken zwischen Kontrollpunkten entlang der sichtbaren (Generator-)Strecke füllen."""
        doc = self.document
        gaps = doc.gap_targets()
        n = len(doc.points)
        if not gaps:
            self.status_label.setText(tr("Keine Lücke gefunden. Zwei benachbarte Punkte wählen oder Lücke > 4× Punktabstand."))
            return
        if self.preview is None or self.map.stale or doc.project.raw["mittellinie"].get("umkehren"):
            self.status_label.setText(tr("Lücken füllen braucht eine aktuelle Vorschau (Strg+Enter) und keine umgekehrte Richtung."))
            self.request_preview()
            return
        stations, pos = self.preview["s"], self.preview["pos"]
        L = float(self.preview["length"])
        source = self.preview["object_s"]
        if len(source) != n:
            self.status_label.setText(tr("Vorschau passt nicht zu den Kontrollpunkten – Vorschau erneuern (Strg+Enter)."))
            return
        pts = [point_xyz(p) for p in doc.points]
        step = doc.gap_step()
        fills = {}
        for i in gaps:
            s0, s1 = source[i], source[(i + 1) % n]
            span = (s1 - s0) % L
            k = max(1, int(round(span / step)))
            neu = []
            for j in range(1, k):
                s = (s0 + span * j / k) % L
                q = min(bisect.bisect_left(stations, s), len(stations) - 1)
                q0 = max(q - 1, 0)
                # zwischen zwei Vorschaustationen linear (Vorschau ist auf ≤ 1.800 Punkte ausgedünnt)
                sa, sb = stations[q0], stations[q]
                w = 0.0 if sb == sa else min(1.0, max(0.0, (s - sa) / (sb - sa)))
                pa, pb = pos[q0], pos[q]
                x, z = pa[0] + w * (pb[0] - pa[0]), pa[2] + w * (pb[2] - pa[2])
                y = pts[i][1] + (pts[(i + 1) % n][1] - pts[i][1]) * j / k   # Höhe zwischen den Eingabepunkten
                neu.append((x, y, z))
            fills[i] = neu
        added = doc.fill_gaps(fills)
        self.status_label.setText(tr("{anzahl} Lücke(n) mit {added} Punkten entlang der sichtbaren Strecke gefüllt · Strg+Z: rückgängig.", anzahl=len(fills), added=added))

    def convert_to_points(self):
        try:
            self.document.convert_to_points()
        except ValueError as error:
            self.status_label.setText(meldung(error))

    def set_mode(self, mode, focus=True):
        if mode == "punkte" and self.document.ml.get("art") != "kontrollpunkte":
            mode = "auswahl"
        self.map.mode = mode
        self.mode_actions[mode].setChecked(True)
        self.map.viewport().update()
        if focus:
            self.map.setFocus()
        self._point_section_update(self.objects.count())
        self.mode_hint.setText({"punkte": tr("Werkzeug: Punkte (EINFÜGEN)"), "umgebung": tr("Werkzeug: Umgebung (U)"),
                                "streuen": tr("Werkzeug: Bepflanzen (B)")}.get(mode, tr("Werkzeug: Auswahl")))
        self.env_panel.selection_box.setVisible(mode in ("umgebung", "streuen"))
        self.env_panel.terrain_box.setVisible(mode in ("umgebung", "streuen"))
        self._refresh_bank_panel()
        if mode in ("umgebung", "streuen") and focus:
            self.results.setCurrentWidget(self.env_panel)      # Katalog und Einstellungen zeigen (lädt den Katalog)
            self._results_tab_changed()
        self._update_welcome()

    def layer_changed(self, attr, value):
        if hasattr(self, "map"):
            setattr(self.map, attr, value)
            self.map.rebuild(self.preview)

    def set_station(self, station):
        self.map.station = station
        self.map.viewport().update()
        for widget in (self.height, self.bank, self.cross, self.speed):
            widget.station = station
            widget.update()
        self._update_cross_panel()

    # ------------------------------------------------------------------ Querschnitt bearbeiten
    def _cross_frame(self):
        """(Länge, s_start) der aktuellen Vorschau oder None (ohne berechnete Vorschau kein Bearbeiten)."""
        p = self.preview
        if not p or p.get("input") or p.get("s_start") is None or not p.get("length"):
            return None
        return float(p["length"]), float(p["s_start"])

    def _update_cross_panel(self):
        if not hasattr(self, "cross_panel"):
            return
        frame = self._cross_frame()
        readonly = self.document.project.readonly
        if frame is None or readonly:
            self.cross_panel.set_enabled(False, tr("Unbekannte Projektfassung: nur lesend.") if readonly else
                                         tr("Querschnitt bearbeiten: geschlossene Runde mit Vorschau nötig."))
            return
        self.cross_panel.set_enabled(True)
        length, s_start = frame
        if self.cross_panel.von.value() == self.cross_panel.bis.value() == 0:
            self.cross_range("cursor")
        side = self.cross_panel.side.currentData()
        sides = ("links", "rechts") if side == "beide" else (side,)
        s_in = querschnitt.to_input_s(self.cross.station, s_start, length)
        texts = [querschnitt.describe(self.document.project.raw, s_in, name, s_start, length) for name in sides]
        self.cross_panel.info.setText("\n".join(dict.fromkeys(texts)))      # beide Seiten gleich: nur einmal
        self._cross_edge_input()

    def _cross_edge_input(self):
        """Randwinkel aus dem Feld in die Querschnittsansicht, solange er von der übernommenen Einstellung abweicht und
        der Cursor im gewählten Bereich liegt."""
        frame = self._cross_frame()
        panel = self.cross_panel
        wanted = None
        if frame is not None:
            length, s_start = frame
            side = panel.side.currentData()
            value = panel.fields()["rand_neigung"]
            inside = querschnitt._inside(self.cross.station % length, panel.von.value(), panel.bis.value())
            s_in = querschnitt.to_input_s(self.cross.station, s_start, length)
            names = ("links", "rechts") if side == "beide" else (side,)
            saved = [querschnitt.effective(self.document.project.raw, s_in, name).get("rand_neigung") for name in names]
            if inside and any(v != value for v in saved):
                wanted = {"wert": value, "seite": side}
            hint = tr("Der Cursor liegt außerhalb des Bereichs: Die Ansicht zeigt den Randwinkel nur innerhalb.")
            shown = panel.info.text().replace("\n" + hint, "")
            panel.info.setText(shown + "\n" + hint if not inside and value is not None else shown)
        if wanted != self.cross.edge_input:
            self.cross.edge_input = wanted
            self.cross.update()

    def _cross_follow_selection(self):
        """Zusammenhängende Auswahl wird zum Querschnittsbereich. Eingaben, die noch nicht übernommen sind, bleiben stehen;
        bei Lücken ändert sich nichts. Liegt der Cursor außerhalb, rückt er in die Mitte, damit die Ansicht passt."""
        panel = getattr(self, "cross_panel", None)
        frame = self._cross_frame() if panel is not None else None
        if frame is None:
            return
        stations = self.preview.get("object_s") or []
        chosen = [i for i in self.document.selected_indices if i < len(stations)]
        if querschnitt.contiguous_span(chosen, len(stations), self.document.project.closed) is None:
            return
        length = frame[0]
        clean = panel.fields() == self._cross_loaded and panel.road_half() is None
        self.cross_range("auswahl", load=clean)
        von, bis = panel.von.value(), panel.bis.value()
        if not querschnitt._inside(self.cross.station % length, von, bis):
            self.set_station((von + ((bis - von) % length) / 2) % length)

    def cross_range(self, kind, load=True):
        """Bereich setzen: ``auswahl`` = erster bis letzter gewählter Punkt/Segment, ``cursor`` = Cursor ±50 m,
        ``runde`` = ganze Strecke (bis 1 m vor das Ziel: gleiche Stationen wären ein leerer Bereich)."""
        frame = self._cross_frame()
        if frame is None:
            return
        length, s_start = frame
        stations = self.preview.get("object_s") or []
        chosen = sorted(i for i in self.document.selected_indices if i < len(stations))
        if kind == "runde":
            von, bis = 0.0, max(1.0, length - 1.0)
        elif kind == "auswahl" and chosen:
            first, last = (querschnitt.contiguous_span(chosen, len(stations), self.document.project.closed)
                           or (chosen[0], chosen[-1]))
            von = stations[first]
            if self.document.ml.get("art") == "segmente":       # Segment i reicht bis zum Anfang von i + 1
                bis = stations[last + 1] if last + 1 < len(stations) else length
            else:
                bis = stations[last]
        else:
            von, bis = (self.cross.station - 50) % length, (self.cross.station + 50) % length
        self.cross_panel.set_range(von, bis)
        side = self.cross_panel.side.currentData()
        s_in = querschnitt.to_input_s((von + ((bis - von) % length) / 2) % length, s_start, length)
        if load:
            self.cross_panel.load(querschnitt.effective(self.document.project.raw, s_in, "links" if side == "beide" else side))
            self._cross_loaded = self.cross_panel.fields()
        self._update_cross_panel()

    def apply_cross(self):
        frame = self._cross_frame()
        if frame is None or self.document.project.readonly:
            return
        length, s_start = frame
        von = querschnitt.to_input_s(self.cross_panel.von.value(), s_start, length)
        bis = querschnitt.to_input_s(self.cross_panel.bis.value(), s_start, length)
        side, fields, half = self.cross_panel.side.currentData(), self.cross_panel.fields(), self.cross_panel.road_half()
        self.document.edit(tr("Querschnitt ändern"),
                           lambda raw, meta: querschnitt.apply_section(raw, von, bis, side, fields, half))
        self._cross_loaded = fields
        self.status_label.setText(tr("Querschnitt {von}–{bis} geändert · Strg+Z macht rückgängig.",
                                     von=zahl(self.cross_panel.von.value(), 0, "m"), bis=zahl(self.cross_panel.bis.value(), 0, "m")))
        self._update_cross_panel()

    def clear_cross(self):
        frame = self._cross_frame()
        if frame is None or self.document.project.readonly:
            return
        length, s_start = frame
        von = querschnitt.to_input_s(self.cross_panel.von.value(), s_start, length)
        bis = querschnitt.to_input_s(self.cross_panel.bis.value(), s_start, length)
        side = self.cross_panel.side.currentData()
        probe = copy.deepcopy(self.document.project.raw)
        if not querschnitt.clear_range(probe, von, bis, side):
            self.status_label.setText(tr("Im Bereich gibt es keine eigenen Querschnitt-Abschnitte."))
            return
        self.document.edit(tr("Querschnitt: Automatik"), lambda raw, meta: querschnitt.clear_range(raw, von, bis, side))
        self.cross_panel.load({})
        self._cross_loaded = self.cross_panel.fields()
        self._update_cross_panel()

    def request_check(self):
        """Prüfen = Fahrlinienlauf (Generator + alle Regeln); gleicher Stand → Ergebnis aus dem Zwischenspeicher."""
        self.results.setCurrentWidget(self.issue_panel)
        if not self.document.project.closed:
            self.request_preview()
            return
        if self.job or self.preview_timer.isActive() or self.preview_revision != self.document.revision:
            self.check_after_preview = True        # erst die Vorschau des aktuellen Stands, dann prüfen
            if not self.job:
                self.request_preview()
            return
        self.request_analysis(force=True)

    # ------------------------------------------------------------------ Fahrlinienlauf
    def current_key(self):
        project = self.document.project
        return check_key(project.raw, project.basis, project.manifest.get("versatz", [0, 0, 0]))

    def _auto_analysis_toggled(self, checked):
        self.auto_analysis = checked
        if checked and self.preview is not None:
            self.analysis_timer.start()

    def request_analysis(self, force=False):
        if not self.document.project.closed or self.preview is None:
            return
        key = self.current_key()
        if key in self.check_cache:
            self.apply_analysis(self.check_cache[key], key)
            return
        if self.analysis_job is not None:
            if self.analysis_job_key == key:
                return
            old, self.analysis_job = self.analysis_job, None
            old.done.disconnect()
            old.cancel()
        try:
            root = toolkit_path(self.document.project.manifest.get("werkzeugkasten"))
        except ValueError as error:
            self.map.analysis_note = tr("Fahrlinie nicht verfügbar: {error}", error=error)
            return
        payload = {"task": "analyse", "raw": copy.deepcopy(self.document.project.raw),
                   "basis": str(self.document.project.basis), "toolkit": str(root),
                   "shift": self.document.project.manifest.get("versatz", [0, 0, 0])}
        self.analysis_job, self.analysis_job_key = ProcessJob(self), key
        self.analysis_job.done.connect(lambda code, text, key=key: self._analysis_done(code, text, key))
        self.analysis_job.start(worker_command(), payload)
        self._set_analysis_note(tr("Fahrlinie wird berechnet …"))

    def _stop_analysis(self):
        self.analysis_timer.stop()
        if self.analysis_job is not None:
            job, self.analysis_job = self.analysis_job, None
            self.analysis_job_key = None
            job.done.disconnect()
            job.cancel()
            job.process.waitForFinished(1500)
            job._kill_if_running()

    def _set_analysis_note(self, text):
        self.map.analysis_note = text
        self.map.viewport().update()

    def _analysis_done(self, code, text, key):
        job, self.analysis_job = self.analysis_job, None
        self.analysis_job_key = None
        if job:
            job.deleteLater()
        if code == -2:
            return
        try:
            result = json.loads(text)
            if code != 0 or "error" in result:
                raise ValueError(result.get("error", text))
        except (ValueError, TypeError) as error:
            self._set_analysis_note(tr("Fahrlinie: Fehler im Generator"))
            self.status_label.setText(tr("Fahrlinienlauf fehlgeschlagen: {error}", error=error))
            return
        self.store_analysis(key, result)

    def store_analysis(self, key, result):
        """Ergebnis eines Fahrlinienlaufs (auch aus dem Baudialog) merken und anzeigen, wenn es zum Stand passt."""
        self.check_cache[key] = result
        while len(self.check_cache) > 8:
            self.check_cache.pop(next(iter(self.check_cache)))
        if key == self.current_key():
            self.apply_analysis(result, key)
        elif self.auto_analysis and self.preview is not None:
            self.analysis_timer.start()

    def apply_analysis(self, result, key):
        self.analysis_key = key
        self.map.focus_curve = None
        self.set_issues(result["issues"], result.get("report", {}))
        self.analysis = result.get("analysis")
        for widget in (self.map, self.speed):
            widget.analysis = self.analysis
        self.map.analysis_stale = self.speed.stale = False
        self.speed.update()
        self._update_summary()
        self._refresh_objects()
        if self.analysis:
            self._set_analysis_note(tr("Fahrlinie aktuell · Runde {zeit}", zeit=lap_text(self.analysis["rundenzeit_s"])))
        else:
            self._set_analysis_note(tr("Fahrlinie: Generator lief nicht (siehe Prüfung)"))

    def _mark_analysis_stale(self):
        if self.analysis is not None:
            self.map.analysis_stale = self.speed.stale = True
            self.speed.update()
            self._set_analysis_note(tr("Fahrlinie veraltet"))

    def _update_summary(self):
        a = self.analysis
        self.curve_table.setRowCount(0)
        if not a:
            self.analysis_info.setText(tr("Keine Auswertung: der Generator lief für diesen Stand nicht (Meldungen unter Prüfung)."))
            return
        def kmh(v):
            return "–" if v is None else zahl(v, 0, "km/h")
        self.analysis_info.setText(
            tr("<b>Runde {rundenzeit_s}</b> (Generator-Fahrzeug) · Länge {laenge_m} · Ideallinie {ideallinie_m} · {v_min_kmh} … {v_max_kmh} · Vollgas {vollgas} · {bremszonen} Bremszonen · {tafeln} Entfernungstafeln · {kurven} Kurven im Spiel",
               rundenzeit_s=lap_text(a['rundenzeit_s']), laenge_m=zahl(a['laenge_m'], 0, 'm'),
               ideallinie_m=zahl(a['ideallinie_m'], 0, 'm'), v_min_kmh=kmh(a['v_min_kmh']), v_max_kmh=kmh(a['v_max_kmh']),
               vollgas=zahl(100 * a['vollgas_anteil'], 0, '%'), bremszonen=len(a['bremszonen']), tafeln=len(a['tafeln']),
               kurven=len({k['spiel'] for k in a['kurven']}))
            + ("" if not a.get("berechnung_s") else tr(" · gerechnet in {berechnung_s}", berechnung_s=zahl(a['berechnung_s'], 0, 's'))))
        rows = a["kurven"]
        self.curve_table.setRowCount(len(rows))
        for r, k in enumerate(rows):
            merged = sum(1 for o in rows if o["spiel"] == k["spiel"]) > 1
            values = ["T%d" % k["spiel"], choice_text(k["richtung"]), zahl(k["winkel"], 0, "°"), zahl(k["r_min"], 0, "m"),
                      zahl(k["s_scheitel"], 0, "m"), kmh(k["v_vor"]), kmh(k["v_min"]),
                      "–" if k["bremsweg_m"] is None else zahl(k["bremsweg_m"], 0, "m"),
                      " / ".join(str(m) for m in k["tafeln"]) or "–",
                      tr("Kurve {nr}", nr=k["nr"]) + (tr(" · zusammengelegt") if merged else "")]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, k["s_scheitel"])
                if c in (2, 3, 4, 5, 6, 7):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.curve_table.setItem(r, c, item)

    def _curve_clicked(self, row, column, zoom=False):
        item = self.curve_table.item(row, 0)
        if item is None or not self.preview:
            return
        station = float(item.data(Qt.UserRole))
        curve = next((k for k in (self.analysis or {}).get("kurven", []) if k["s_scheitel"] == station), None)
        if zoom and curve is not None:
            self.show_curve(curve)
            return
        self.set_station(station)
        i = self.map.station_at(station)
        point = self.preview["pos"][i]
        self.map.centerOn(point[0], point[2])

    def show_turn(self, number):
        """Klick auf eine Kurvennummer der Karte: Kurve groß zeigen und in der Tabelle wählen."""
        curves = [k for k in (self.analysis or {}).get("kurven", []) if k["spiel"] == number]
        if curves:
            self.show_curve(curves[0], curves)

    def show_curve(self, curve, group=None):
        """Kurve im Detail (A6): Karte auf Bremszone bis Kurvenende, Infoschild am Scheitel, Tabellenzeile wählen."""
        if not self.preview:
            return
        group = group or [k for k in self.analysis.get("kurven", []) if k["spiel"] == curve["spiel"]]
        length = float(self.preview["length"])
        start = min(k["s_anfang"] for k in group) - max([k["bremsweg_m"] or 0 for k in group] + [0]) - 60
        end = max(k["s_ende"] for k in group) + 50
        points = [self.preview["pos"][self.map.station_at(s % length)]
                  for s in [start + (end - start) * j / 40 for j in range(41)]]
        xs, zs = [p[0] for p in points], [p[2] for p in points]
        margin = 40
        from PySide6.QtCore import QRectF
        self.map.fitInView(QRectF(min(xs) - margin, min(zs) - margin, max(xs) - min(xs) + 2 * margin,
                                  max(zs) - min(zs) + 2 * margin), Qt.KeepAspectRatio)
        def kmh(v):
            return "–" if v is None else f"{v:.0f}"
        info = (f"T{curve['spiel']} · {choice_text(curve['richtung'])} {sum(k['winkel'] for k in group):.0f}° · "
                f"R {curve['r_min']:.0f} m · {kmh(curve['v_vor'])} → "
                f"{kmh(min((k['v_min'] for k in group if k['v_min'] is not None), default=None))} km/h"
                + (tr(" · Bremsweg {bremsweg_m:.0f} m", bremsweg_m=curve['bremsweg_m']) if curve.get("bremsweg_m") else "")
                + (tr(" · Tafeln {tafeln}", tafeln='/'.join(str(m) for m in curve['tafeln'])) if curve.get("tafeln") else ""))
        self.map.focus_curve = dict(curve, info=info if sprache() == "en" else info.replace(".", ","))
        self.set_station(curve["s_scheitel"])
        for row in range(self.curve_table.rowCount()):
            item = self.curve_table.item(row, 0)
            if item is not None and item.data(Qt.UserRole) == curve["s_scheitel"]:
                self.curve_table.selectRow(row)
                break
        self.map.viewport().update()

    # ------------------------------------------------------------------ Ein-/Ausblenden (Menü Ansicht)
    MAP_FEATURES = {"raster": "grid", "raender": "bands", "richtung": "direction", "spielbereich": "show_frame",
                    "zonen": "show_zones", "ideallinie": "show_line", "rennbetrieb": "show_race",
                    "kurvennummern": "show_turns", "eingabepunkte": "show_points", "beschriftungen": "show_labels",
                    "massstab": "show_scale", "kartenhinweise": "show_hints", "umgebung": "show_env"}

    def apply_feature(self, key, on, save=True):
        on = bool(on)
        self.view_state[key] = on
        if key in self.MAP_FEATURES:
            self.layer_changed(self.MAP_FEATURES[key], on)
        elif key == "seitenleiste":
            self.left_panel.setVisible(on)
        elif key == "eigenschaften":
            self.right_panel.setVisible(on)
        elif key == "unterer_bereich":
            self.results.setVisible(on)
        elif key == "querneigungsfeld":
            self.bank_feature = on
            self._refresh_properties()             # Querneigung dann wieder als Tabellenzeile
        elif key == "werkzeugleiste":
            self.toolbar.setVisible(on)
        elif key == "kopfzeile":
            self.project_heading.setVisible(on)
        elif key == "statuszeile":
            self.statusBar().setVisible(on)
        elif key in getattr(self, "tab_features", {}):
            self.results.setTabVisible(self.results.indexOf(self.tab_features[key]), on)
        elif key == "fahrlinie_auto":
            self._auto_analysis_toggled(on)
        elif key == "startbildschirm":
            self.welcome_feature = on
            self._update_welcome()
        for widget in (self.feature_actions.get(key), self.feature_checks.get(key)):
            if widget is not None and widget.isChecked() != on:
                widget.blockSignals(True)
                widget.setChecked(on)
                widget.blockSignals(False)
        if self.view_dialog is not None:
            self.view_dialog.sync(key, on)
        if save and not (key == "fahrlinie_auto" and not self.interactive):
            ansicht.save_state(key, on)

    def open_view_dialog(self):
        self.view_dialog = ansicht.ViewDialog(self.view_state, self)
        self.view_dialog.changed.connect(self.apply_feature)
        self.view_dialog.finished.connect(lambda *args: setattr(self, "view_dialog", None))
        self.view_dialog.show()

    def show_all_features(self):
        for key in ansicht.DEFAULTS:
            if key != "letztes_projekt":
                self.apply_feature(key, True)

    def toggle_results_maximized(self):
        sizes = self.vertical_split.sizes()
        total = sum(sizes) or 1
        if sizes[0] > 40:
            self._sizes_before_max = sizes
            self.vertical_split.setSizes([0, total])
        else:
            self.vertical_split.setSizes(getattr(self, "_sizes_before_max", None) or [int(total * .7), int(total * .3)])
        self._save_layout()

    def _save_layout(self):
        if not self.interactive:
            return
        store = ansicht.settings()
        store.setValue("layout/horizontal", self.horizontal_split.sizes())
        store.setValue("layout/vertikal", self.vertical_split.sizes())

    def _restore_layout(self):
        if not self.interactive:
            return
        store = ansicht.settings()
        for key, splitter in (("layout/horizontal", self.horizontal_split), ("layout/vertikal", self.vertical_split)):
            sizes = store.value(key)
            try:
                sizes = [int(s) for s in (sizes or [])]
            except (TypeError, ValueError):
                sizes = []
            if len(sizes) == splitter.count() and sum(sizes) > 0:
                splitter.setSizes(sizes)

    # ------------------------------------------------------------------ zuletzt geöffnet, Autosicherung
    def _remember_current(self):
        project = self.document.project
        if self.interactive and project.folder and not project.readonly:
            ansicht.remember_project(project.folder)

    def _fill_recent_menu(self):
        self.recent_menu.clear()
        paths = ansicht.recent_projects()
        for path in paths:
            action = self.recent_menu.addAction(str(path))
            action.triggered.connect(lambda checked=False, path=path: self.load_project(path))
        if not paths:
            action = self.recent_menu.addAction(tr("(noch keine)"))
            action.setEnabled(False)

    def recovery_path(self):
        folder = self.document.project.folder
        return Path(folder) / ".mapeditor_wiederherstellung.json" if folder else None

    def autosave(self):
        """Ungespeicherte Änderungen in eine Wiederherstellungsdatei (das Projekt selbst bleibt unverändert)."""
        path = self.recovery_path()
        if not self.view_state.get("autosicherung", True) or path is None or self.document.undo.isClean() \
                or self.document.project.readonly:
            return False
        try:
            writable_path(path.parent)
            data = {"zeit": QDateTime.currentDateTime().toString(Qt.ISODate),
                    "raw": self.document.project.raw, "manifest": self.document.project.manifest,
                    "umgebung": self.document.project.umgebung}
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(path)
            self.statusBar().showMessage(tr("Automatisch gesichert (Wiederherstellungsdatei)"), 3000)
            return True
        except (OSError, ValueError, TypeError):
            return False

    def _drop_recovery(self):
        path = self.recovery_path()
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def offer_recovery(self, ask=None):
        """Beim Öffnen: neuere Wiederherstellungsdatei anbieten (Übernahme als rückgängig machbarer Schritt)."""
        path = self.recovery_path()
        if path is None or not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if data.get("raw") == self.document.project.raw and data.get("umgebung", self.document.project.umgebung) == self.document.project.umgebung:
            self._drop_recovery()
            return False
        ask = ask or (lambda text: QMessageBox.question(
            self, tr("Wiederherstellen?"), text, QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) == QMessageBox.Yes)
        if not ask(tr("Für dieses Projekt gibt es ungespeicherte Änderungen vom {zeit}. Übernehmen? "
                      "(Rückgängig: Strg+Z; „Nein“ verwirft die Sicherung.)", zeit=data.get('zeit', '?'))):
            self._drop_recovery()
            return False
        raw, env = data["raw"], data.get("umgebung", self.document.project.umgebung)
        def operation(target, meta, current):
            target.clear()
            target.update(copy.deepcopy(raw))
            return copy.deepcopy(env)
        self.document.replace_content(tr("Wiederherstellung übernehmen"), operation)
        return True

    # ------------------------------------------------------------------ Gehe zu
    def _object_place(self, index):
        """„1.234 m · T4 · “ für die Objektliste (nur wenn Vorschau und Fahrlinie zum Stand passen)."""
        preview = self.preview
        if not preview or self.preview_revision != self.document.revision:
            return ""
        stations = preview.get("object_s") or []
        if index >= len(stations):
            return ""
        s = stations[index]
        turn = ""
        if self.analysis and not self.map.analysis_stale:
            length = float(preview["length"])
            for k in self.analysis.get("kurven", []):
                if (s - k["s_anfang"]) % length <= (k["s_ende"] - k["s_anfang"]) % length:
                    turn = tr("T{spiel} · ", spiel=k['spiel'])
                    break
        return f"{zahl(s, 0, 'm')} · {turn}"

    def find_location(self):
        text = self.find_field.text().strip().replace(",", ".")
        if not text or not self.preview:
            return
        match = re.fullmatch(r"[tT]\s*(\d+)", text)
        if match:
            if not self.analysis:
                self.status_label.setText(tr("Kurvennummern gibt es nach dem Fahrlinienlauf (Prüfen: Strg+Enter)."))
                return
            self.show_turn(int(match.group(1)))
            return
        match = re.fullmatch(r"#\s*(\d+)", text)
        if match:
            index = int(match.group(1)) - 1
            if 0 <= index < self.objects.count():
                self.document.select(index)
                self.objects.scrollToItem(self.objects.item(index))
                self.map.focus_selection()
            return
        try:
            station = float(text.rstrip("m ").strip()) % float(self.preview["length"])
        except ValueError:
            self.status_label.setText(tr("Gehe zu: Meter (z. B. 1250), Kurve (T4) oder Objekt (#12)."))
            return
        stations = self.preview.get("object_s") or []
        if stations:                           # nächstes Objekt wählen, Cursor danach genau auf die Station
            self.document.select(min(range(len(stations)), key=lambda i: abs(stations[i] - station)))
        self.set_station(station)
        point = self.preview["pos"][self.map.station_at(station)]
        self.map.centerOn(point[0], point[2])

    def request_preview(self):
        if self.job:
            self.preview_pending = True
            return
        self.preview_timer.stop()
        self.preview_pending = False
        if not self.document.project.closed:
            self.preview = None
            self.map.rebuild()
            self._show_input_profiles()
            self.cross.preview = None
            self.cross.update()
            self.issues.clear()
            self.issues.addItem(tr("Fehler · Runde ist offen; mindestens vier Punkte anlegen und schließen."))
            self.status_label.setText(tr("Offener Entwurf · Eingabepunkte bearbeitbar · keine Generatorvorschau"))
        try:
            root = toolkit_path(self.document.project.manifest.get("werkzeugkasten"))
            revision = self.document.revision
            payload = {"raw": copy.deepcopy(self.document.project.raw), "basis": str(self.document.project.basis),
                       "toolkit": str(root), "closed": self.document.project.closed,
                       "shift": self.document.project.manifest.get("versatz", [0, 0, 0]),
                       "check_generator": self.check_generator}
            self.check_generator = False
            self.job = ProcessJob(self)
            self.job.done.connect(lambda code, text: self._preview_done(code, text, revision))
            self.preview_action.setEnabled(False)
            self.preview_action.setToolTip(tr("Eine Vorschau läuft bereits. Änderungen ersetzen die Berechnung automatisch; mit Abbrechen stoppen."))
            self.cancel_action.setEnabled(True)
            self.cancel_action.setToolTip(tr("Laufende Vorschau abbrechen; Eingabe bleibt erhalten. Die nächste Änderung startet die Automatik wieder."))
            self._busy(True)
            self.status_label.setText(tr("Vorschau wird berechnet …"))
            self.job.start(worker_command(), payload)
        except (ValueError, OSError) as error:
            self.issues.clear()
            self.issues.addItem(tr("Fehler · Vorschau: {error}", error=error))
            self.status_label.setText(tr("Vorschau nicht verfügbar: {error} · Eingabe bleibt bearbeitbar.", error=error))

    def _preview_done(self, code, text, revision):
        job = self.job
        self.job = None
        if job:
            job.deleteLater()
        self.preview_action.setEnabled(True)
        self.preview_action.setToolTip(tr("Generatorvorschau erneut anfordern; Änderungen starten sie auch automatisch."))
        self.cancel_action.setEnabled(False)
        self._busy(False)
        self.cancel_action.setToolTip(tr("Keine laufende Berechnung. Die nächste Änderung startet die Automatik."))
        if self.preview_pending and not self.preview_timer.isActive():
            self.preview_timer.start(0)
        if code == -2:
            self.status_label.setText(tr("Vorschau abgebrochen · Karte zeigt deine Eingabe, die berechnete Fahrbahn ist veraltet")
                                      + (tr(" · neuer Stand folgt automatisch") if self.preview_pending else ""))
            return
        if revision != self.document.revision:
            self.status_label.setText(tr("Berechnung gehört zu einem älteren Entwurf; neuer Stand folgt automatisch."))
            return
        try:
            result = json.loads(text)
            if code != 0 or "error" in result:
                raise ValueError(result.get("error", text))
            if result.get("draft"):
                self.defaults = result["defaults"]
                self._refresh_after_edit()
                self.status_label.setText(tr("Offener Entwurf · Eingabe bearbeitbar · Generatorstandards geladen · keine berechnete Runde"))
                return
            if result.get("validation_only"):
                self.set_issues(result["issues"], result["report"])
                self.status_label.setText(" · ".join(m["text"] for m in result["issues"]) + tr(" · Eingabe bleibt bearbeitbar"))
                return
            self.install_preview(result, revision)
        except (ValueError, TypeError) as error:
            self.issues.clear()
            self.issues.addItem(tr("Fehler · Werkzeugkasten: {error}", error=error))
            self.status_label.setText(tr("Vorschau fehlgeschlagen: {error} · Entwurf kann gespeichert werden", error=error))

    def install_preview(self, preview, revision=None):
        self.preview, self.preview_revision = preview, self.document.revision if revision is None else revision
        self.defaults = preview["defaults"]
        self.map.stale = False
        self.map.rebuild(preview)
        self._update_zone_overlay()
        for widget in (self.height, self.bank, self.cross):
            widget.preview = preview
            widget.stale = False
            widget.update()
        self.set_issues(preview["issues"], preview.get("report", {}))
        self._refresh_after_edit()
        self._update_cross_panel()
        self._update_height_bar()
        self._schedule_env_check()
        key = self.current_key()
        if key in self.check_cache:
            self.apply_analysis(self.check_cache[key], key)
        elif getattr(self, "check_after_preview", False):
            self.check_after_preview = False
            self.request_analysis(force=True)
        elif self.auto_analysis:
            self.analysis_timer.start()
        direction = {"im_uhrzeigersinn": tr("im Uhrzeigersinn"), "gegen_uhrzeigersinn": tr("gegen den Uhrzeigersinn")}.get(
            preview["direction"], preview["direction"].replace("_", " "))
        self.status_label.setText(tr("Vorschau aktuell · Länge {length} · kleinster Radius {min_radius} · {direction}", length=zahl(preview['length'], 0, 'm'), min_radius=zahl(preview['min_radius'], 1, 'm'), direction=direction))
        if self.properties.pending():
            self.status_label.setText(self.status_label.text() + tr(" · Ungültige Feldeingabe korrigieren; Text bleibt erhalten."))
        if self.fit_after_preview:
            self.map.fit_track()
            QTimer.singleShot(0, self.map, self.map.fit_track)      # verfällt mit der Karte (Sprachwechsel)
            self.fit_after_preview = False
        # Neue Stationszuordnung synchronisieren, ohne einen offenen/ungültigen
        # Zell-Editor durch erneutes Auswählen und Befüllen zu überschreiben.
        self.map._selection(self.document.selected)

    def cancel_preview(self):
        self.preview_timer.stop()
        self.preview_pending = False
        if self.job:
            self.job.cancel()

    def jump_issue(self, item):
        issue = item.data(Qt.UserRole)
        if not issue:
            return
        preview = self.preview if self.preview_revision == self.document.revision else self.map.input
        location = issue.get("ort") or {}
        if location.get("objekt") and self.jump_env_object(location["objekt"]):
            return
        station = issue.get("s")
        if station is None and location.get("welt") and preview.get("pos"):
            # Weltkoordinaten enthalten den Spielweltversatz; Boxenstationen liegen
            # auf einer anderen Achse und werden deshalb über die Weltposition zugeordnet.
            shift = self.document.project.manifest.get("versatz", [0, 0, 0])
            world = location["welt"]
            i = min(range(len(preview["pos"])), key=lambda i: sum(
                (preview["pos"][i][k] + shift[k] - world[k]) ** 2 for k in (0, 2)))
            station = preview["s"][i]
        if station is None and location.get("spline") == "maintrack":
            station = location.get("station_m")
        if station is not None and preview.get("s"):
            station %= preview["length"]
            stations = preview["object_s"]
            if stations:
                index = min(range(len(stations)), key=lambda i: min(abs(stations[i] - station), preview["length"] - abs(stations[i] - station)))
                self.document.select(index)
            self.set_station(station)
            i = min(range(len(preview["s"])), key=lambda i: abs(preview["s"][i] - station))
            point = preview["pos"][i]
            self.map.centerOn(point[0], point[2])

    def set_issues(self, issues, report=None):
        self.validation_issues = list(issues)
        self.validation_report = report or {}
        self.render_issues()

    def add_build_issue(self, issue, build_id):
        self.build_issues.append(dict(issue, bau_id=build_id))
        self.render_issues()

    def render_issues(self, *args):
        self.issues.clear()
        issues = self.validation_issues + self.build_issues
        issues += self._env_issue_list(issues)
        for rule in self.validation_report.get("nicht_geprueft", []):
            issues.append({"id": rule, "level": "Nicht geprüft",
                           "text": tr("Diese Regel wurde in dieser Prüfung noch nicht geprüft.")})
        level, search = self.issue_filter.currentData() or "Alle", self.issue_search.text().casefold()
        for issue in sorted(issues, key=issue_order):
            origin = tr(" · Bau {bau_id}", bau_id=issue['bau_id']) if issue.get("bau_id") else ""
            location = issue.get("ort") or {}
            station = location.get("station_m", issue.get("s"))
            spline = {"mittellinie": tr("Mittellinie"), "maintrack": tr("Strecke"), "pitlane": tr("Boxengasse")}.get(
                location.get("spline", "mittellinie"), location.get("spline", "Mittellinie"))
            where = f" · {spline} {zahl(station, 0, 'm')}" if station is not None else ""
            if location.get("objekt"):
                where += tr(" · Objekt {kennung}", kennung=location["objekt"])
            short, message = issue_texts(issue)
            text = f"{tr(issue['level'])} · {short}{where}{origin} · {german_numbers(message)}"
            if level == "Alle" and issue["level"] == "Nicht geprüft":
                continue                     # nur mit Filter „Nicht geprüft“ (sonst überdecken sie die Meldungen)
            if level != "Alle" and issue["level"] != level or search and search not in (text + issue["id"]).casefold():
                continue
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, issue)
            item.setToolTip(issue["id"] + tr("\nStufe: ") + tr(issue.get("stufe", "Eingabe")) +
                            tr("\nIst: ") + str(issue.get("ist", "–")) + tr(" · Soll: ") + str(issue.get("soll", "–")) +
                            "\n" + str(issue.get("quelle", "")))
            item.setForeground(QColor({"Fehler": "#F07878", "Warnung": "#D6A94A", "Hinweis": "#63BFCF"}.get(issue["level"], "#8D9BA9")))
            self.issues.addItem(item)
        errors = sum(issue["level"] == "Fehler" for issue in issues)
        warnings = sum(issue["level"] == "Warnung" for issue in issues)
        stages = ", ".join(tr(s) for s in self.validation_report.get("stufen", [])) or tr("ausstehend")
        hints = sum(issue["level"] == "Hinweis" for issue in issues)
        self.issue_summary.setText(tr("{errors} Fehler · {warnings} Warnungen · {unchecked} nicht geprüft · Stufen: {stages}",
                                      errors=errors, warnings=warnings, stages=stages,
                                      unchecked=len(self.validation_report.get('nicht_geprueft', []))))
        self.issue_summary.setWordWrap(True)
        checked = bool(self.validation_report.get("stufen")) or bool(issues)
        if not checked:
            self.issue_badge.setText(tr("Noch nicht geprüft"))
            self.issue_badge.setStyleSheet("")
            self.results.setTabText(self.issue_tab, tr("Prüfung"))
        else:
            self.issue_badge.setText(" · ".join((trn(errors, "{n} Fehler", "{n} Fehler"),
                                                trn(warnings, "{n} Warnung", "{n} Warnungen"),
                                                trn(hints, "{n} Hinweis", "{n} Hinweise"))) if errors or warnings or hints
                                     else tr("Keine Probleme gefunden"))
            self.issue_badge.setStyleSheet("color: %s; font-weight: 600;" % (
                "#F07878" if errors else "#D6A94A" if warnings else "#5BD08A"))
            self.results.setTabText(self.issue_tab, tr("Prüfung ({anzahl})", anzahl=errors + warnings) if errors + warnings
                                    else tr("Prüfung · ok"))

    def open_build(self):
        from .build import BuildDialog
        if self.build_dialog and self.build_dialog.job:
            self.build_dialog.show()
            self.build_dialog.raise_()
            return
        if self.build_dialog:
            self.build_dialog.deleteLater()
        self.build_dialog = BuildDialog(self)
        self.build_dialog.setWindowModality(Qt.NonModal)     # Weiterarbeiten während des Baus (Schnappschuss)
        self.build_dialog.show()

    def open_playtests(self, select=None):
        from .build import PlaytestDialog
        self.playtest_dialog = PlaytestDialog(self.document.project, self, select=select)
        self.playtest_dialog.exec()

    def open_cleanup(self):
        from .build import CleanupDialog, build_folders
        try:
            output, packages = build_folders(self.document.project)
        except ValueError as error:
            self.error(meldung(error))
            return
        self.cleanup_dialog = CleanupDialog(self.document.project, output, packages, self)
        self.cleanup_dialog.exec()

    def _update_close_action(self, art, editable, reason):
        """Solange die Runde offen ist, ist Schließen der nächste Schritt: Der Knopf fällt dann auf (Regel ``QToolButton#runde``)."""
        closed = self.document.project.closed
        ready = closed or art != "kontrollpunkte" or len(self.document.points) >= 4
        self.close_action.setText(tr("Runde öffnen") if closed else tr("Runde schließen"))
        self.close_action.setEnabled(editable and ready)
        self.close_action.setToolTip(reason if not editable else tr("Zum Schließen sind mindestens vier Kontrollpunkte erforderlich.")
                                     if not ready else tr("Ersten und letzten Punkt verbinden bzw. die Runde wieder öffnen"))
        button = self.toolbar.widgetForAction(self.close_action)
        if button is not None and button.property("offen") != (not closed):
            button.setProperty("offen", not closed)
            button.style().unpolish(button)
            button.style().polish(button)

    def toggle_closed(self):
        try:
            self.document.close_round()
        except ValueError as error:
            self.error(meldung(error))

    def change_theme(self, theme):
        if not self.document.project.readonly:
            self.document.edit(tr("Thema ändern"), lambda raw, meta: meta.update(thema=theme))
        self.set_theme(theme)

    def set_theme(self, theme):
        dark = theme != "hell"
        palette = QPalette()
        for role, color in ((QPalette.Window, "#101820" if dark else "#F3F6F8"),
                            (QPalette.WindowText, "#F1F5F9" if dark else "#162331"),
                            (QPalette.Base, "#18232E" if dark else "#FFFFFF"),
                            (QPalette.AlternateBase, "#22313F" if dark else "#E8EEF3"),
                            (QPalette.Text, "#F1F5F9" if dark else "#162331"),
                            (QPalette.PlaceholderText, "#9BA9B6" if dark else "#657B8E"),
                            (QPalette.Button, "#22313F" if dark else "#E8EEF3"),
                            (QPalette.ButtonText, "#F1F5F9" if dark else "#162331"),
                            (QPalette.Highlight, "#005C73"), (QPalette.HighlightedText, "#FFFFFF"),
                            (QPalette.ToolTipBase, "#22313F" if dark else "#FFFFFF"),
                            (QPalette.ToolTipText, "#F1F5F9" if dark else "#162331")):
            palette.setColor(role, QColor(color))
        palette.setColor(QPalette.Disabled, QPalette.Text, QColor("#9BA9B6" if dark else "#657B8E"))
        palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#9BA9B6" if dark else "#657B8E"))
        QApplication.instance().setPalette(palette)
        line = "#52677A" if dark else "#CFDAE2"
        muted = "#8D9BA9" if dark else "#5B6F80"
        QApplication.instance().setStyleSheet(
            "QWidget { font-family: 'Segoe UI'; font-size: 13px; } "
            "QToolBar { spacing: 4px; padding: 4px 6px; border-bottom: 1px solid %(line)s; } "
            "QToolButton { padding: 4px 8px; border-radius: 4px; } "
            "QToolButton:checked { background: #005C73; color: #FFFFFF; } "
            "QToolButton#primary { background: #0A7D4B; color: #FFFFFF; font-weight: 600; padding: 4px 14px; } "
            "QToolButton#primary:hover { background: #0E9A5D; } "
            "QToolButton#runde[offen=\"true\"]:enabled { background: #D6A94A; color: #101820; font-weight: 600; padding: 4px 14px; } "
            "QToolButton#runde[offen=\"true\"]:enabled:hover { background: #E8BE62; } "
            "QPushButton, QComboBox { min-height: 30px; } "
            "QLabel#heading { color: %(muted)s; font-size: 11px; font-weight: 600; letter-spacing: 1px; padding-top: 4px; } "
            "QFrame#welcome { border: 1px solid %(line)s; border-radius: 8px; } "
            "QTabBar::tab { padding: 8px 12px; } QTableWidget::item { padding: 5px; } QListWidget::item { padding: 4px; } "
            "QSplitter::handle { background: %(line)s; } QStatusBar QLabel { padding: 0 6px; }"
            % {"line": line, "muted": muted})
        self.map.dark = dark
        self.map.viewport().update()
        for widget in (self.height, self.bank, self.cross):
            widget.update()

    def choose_toolkit(self):
        folder = QFileDialog.getExistingDirectory(self, tr("Ordner mit strecke/definition.py wählen"))
        if folder:
            try:
                toolkit_path(folder)
                self.document.edit(tr("Werkzeugkasten zuordnen"), lambda raw, meta: meta.update(werkzeugkasten=folder))
            except ValueError as error:
                self.error(meldung(error))

    def choose_shift(self):
        current = self.document.project.manifest.get("versatz", [0, 0, 0])
        text, ok = QInputDialog.getText(self, tr("Spielweltversatz"), tr("[dx, dy, dz] in Metern · Teststrecke bisher [-591, 0, -473]"), text=json.dumps(current))
        if ok:
            try:
                value = json.loads(text)
                if not isinstance(value, list) or len(value) != 3 or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
                    raise ValueError(tr("Drei endliche Zahlen als JSON-Liste erwartet."))
                self.document.edit(tr("Spielweltversatz ändern"), lambda raw, meta: meta.update(versatz=value))
            except ValueError as error:
                self.error(meldung(error))

    def open_output(self):
        filename, _ = QFileDialog.getOpenFileName(self, tr("Vorhandene strecke_logik.json lesen"), filter=tr("JSON (*.json)"))
        if filename:
            try:
                self.load_output(filename)
            except (ValueError, OSError) as error:
                self.error(meldung(error))

    def load_output(self, filename):
        data = read_json(filename)
        self.output_file = filename
        self.output_tree.clear()
        self.output_tree.addTopLevelItem(QTreeWidgetItem([str(filename), tr("Externe Ausgabe · Zuordnung zum Entwurf ungeprüft · schreibgeschützt")]))
        names = {"sektoren": tr("Sektoren"), "drs": tr("DRS-Zonen"), "startplaetze": tr("Startaufstellung"),
                 "boxenplaetze": tr("Boxenplätze"), "boxengasse": tr("Boxengasse"), "kurven": tr("Erkannte Kurven"),
                 "ideallinie": tr("Geschätzte Ideallinie"), "spielformate": tr("Spielformatdaten"),
                 "AISplineData": tr("KI-Kurven und Bremslinien (AISplineData)"), "TrackSpace": tr("TrackSpace-Linien"),
                 "pruefung": tr("Generatorprüfung"), "annahmen": tr("Annahmen / Grenzen")}
        def append(parent, key, value):
            label = names.get(key, str(key))
            if isinstance(value, (dict, list)):
                node = QTreeWidgetItem([label, tr("{anzahl} Einträge", anzahl=len(value))])
                parent.addChild(node)
                children = value.items() if isinstance(value, dict) else enumerate(value)
                for child_key, child in children:
                    append(node, child_key, child)
            else:
                parent.addChild(QTreeWidgetItem([label, value_text(value)]))
        append(self.output_tree.invisibleRootItem(), tr("Generatorlogik · Boxen / Start / Sektoren / DRS / KI"), data)
        self.output_tree.topLevelItem(1).setExpanded(True)
        self.output_tree.setColumnWidth(0, 460)
        self.output_tree.show()
        self.results.setCurrentWidget(self.analysis_page)

    def confirm_discard(self):
        if self.document.undo.isClean():
            return True
        answer = QMessageBox.question(self, tr("Ungespeicherter Entwurf"), tr("Änderungen vor dem Verlassen speichern?"), QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        if answer == QMessageBox.Save:
            return self.save_project()
        return answer == QMessageBox.Discard

    def open_dialog(self):
        filename, _ = QFileDialog.getOpenFileName(self, tr("Definition oder projekt.json öffnen"), filter=tr("JSON (*.json)"))
        if filename:
            self.load_project(filename)

    def open_folder_dialog(self):
        folder = QFileDialog.getExistingDirectory(self, tr("Projektordner öffnen"))
        if folder:
            self.load_project(folder)

    def load_project(self, path):
        try:
            project = Project.open(path)
            if self.confirm_discard():
                self.replace_project(project)
        except (ValueError, OSError, KeyError, TypeError) as error:
            self.error(meldung(error))

    def replace_project(self, project):
        if self.build_dialog and self.build_dialog.job:
            self.status_label.setText(tr("Vor einem Projektwechsel den Bau beenden oder abbrechen."))
            return
        self.preview_timer.stop()
        self.preview_pending = False
        if self.job:
            self.job.done.disconnect()
            self.job.cancel()
            self.job.process.waitForFinished(1500)
            self.job.deleteLater()
            self.job = None
        self._stop_analysis()
        self.check_cache.clear()
        self.analysis = self.analysis_key = None
        self.map.analysis = self.speed.analysis = None
        self._set_analysis_note("")
        self._update_summary()
        self.document.project = project
        self.document.undo.clear()
        self.document.selected = -1
        self.document.selected_indices.clear()
        self.document.env_selected.clear()
        self.document.env_primary = None
        self.document.env_only = False
        self.document.revision += 1
        self.env_check_timer.stop()
        self._stop_env_check()
        self.env_issues = []
        self.preview, self.defaults = None, {}
        self.output_tree.clear()
        self.build_issues.clear()
        self.set_issues([], {})
        self.fit_after_preview = True
        self.set_theme(project.manifest.get("thema", "dunkel"))
        self._changed()
        self._remember_current()
        self.request_preview()
        if self.interactive and project.umgebung and project.umgebung.get("objekte"):
            self.load_env_catalog()

    def new_project(self):
        if self.confirm_discard():
            self.replace_project(Project())

    def new_from_template(self):
        """TUM-CSV, GPX-Spur oder Spielstrecke als neuer, ungespeicherter Entwurf."""
        from .templates import TemplateDialog
        if not self.confirm_discard():
            return
        dialog = TemplateDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        if not values["path"]:
            self.error(tr("Bitte eine Vorlagendatei wählen."))
            return
        if values["kind"] == "spiel" and not values["code"]:
            self.error(tr("Bitte den Streckencode der Spielstrecke angeben (z. B. monza)."))
            return
        toolkit = self.document.project.manifest.get("werkzeugkasten")
        self._create_from_tool(lambda fit: import_template(values["kind"], values["path"], values["code"], fit,
                                                           values["spacing"], toolkit),
                               tr("Vorlage {name} wird gelesen", name=Path(values['path']).name), values["fit"], tr("Vorlage"))

    def new_from_suggestion(self):
        """Zufälliger, gültiger Streckenvorschlag als neuer, ungespeicherter Entwurf."""
        from .templates import SuggestionDialog
        if not self.confirm_discard():
            return
        dialog = SuggestionDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        v = dialog.values()
        toolkit = self.document.project.manifest.get("werkzeugkasten")
        self._create_from_tool(lambda fit: suggest_track(v["seed"], v["length_m"], v["curves"], v["min_radius_m"], toolkit),
                               tr("Vorschlag (Seed {seed}) wird berechnet", seed=v['seed']), True, tr("Vorschlag"))

    def _create_from_tool(self, make, busy_text, fit, label):
        """Führt ``make(fit)`` mit Wartecursor aus, fragt bei zu großer Vorlage nach dem Verkleinern und öffnet das Ergebnis."""
        def run(fit_now):
            self.status_label.setText(busy_text + " …")
            QApplication.setOverrideCursor(Qt.WaitCursor)
            QApplication.processEvents()
            try:
                return make(fit_now)
            finally:
                QApplication.restoreOverrideCursor()
        try:
            result = run(fit)
            info = result["info"]
            if not info.get("passt_in_ao", True):
                answer = QMessageBox.question(
                    self, tr("Vorlage zu groß"),
                    tr("Die Vorlage ist {breite:.0f} × {tiefe:.0f} m groß und passt nicht in die Spielwelt (X/Z ±1024 m).\n\n"
                       "Gleichmäßig verkleinern? Breiten und Höhen bleiben. Bei „Nein“ bleibt die Originalgröße; die "
                       "Prüfung meldet sie dann als Fehler.", breite=info['ausdehnung_m'][0], tiefe=info['ausdehnung_m'][1]), QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                if answer == QMessageBox.Yes:
                    result = run(True)
                    info = result["info"]
            project = Project(result["raw"], result["manifest"])
        except (ValueError, OSError, RuntimeError, KeyError, TypeError) as error:
            self.status_label.setText(tr("{label} nicht möglich: {error}", label=label, error=error))
            self.error(tr("{label} nicht möglich:\n{error}", label=label, error=error))
            return
        self.replace_project(project)
        if self.document.project is not project:
            return
        # Der Entwurf ist neu und ungespeichert: Schließen/Neu fragt vorher nach dem Speichern.
        self.document.undo.resetClean()
        self._title()
        parts = [tr("{label}: {anzahl} Punkte", label=label, anzahl=len(project.raw['mittellinie']['punkte'])), f"{info.get('laenge_m', 0) / 1000:.2f} km"]
        if info.get("kurven_gezaehlt") is not None:
            parts.append(tr("{kurven} Kurven, kleinster Radius {radius} m, Versuch {versuch}", kurven=info['kurven_gezaehlt'],
                            radius=info.get('radius_min_m'), versuch=info.get('versuch')))
        if info.get("massstab", 1.0) != 1.0:
            parts.append(tr("auf {prozent:.1f} % verkleinert", prozent=info['massstab'] * 100))
        parts += info.get("warnungen", [])
        text = " · ".join(parts) + tr(" · ungespeichert (Projekt → Speichern unter …)")
        self.status_label.setText(text)
        self.statusBar().showMessage(text, 12000)

    def save_project(self, save_as=False):
        if self.document.project.readonly:
            self.error(tr("Unbekannte Projektfassung: nur lesend geöffnet."))
            return False
        folder = self.document.project.folder
        if folder:
            try:
                writable_path(folder)
            except ValueError:
                save_as = True
        if save_as or folder is None:
            chosen = QFileDialog.getExistingDirectory(self, tr("Projektordner wählen (enthält projekt.json und definition.json)"))
            if not chosen:
                return False
            folder = Path(chosen)
            if (folder / "projekt.json").exists() and folder != self.document.project.folder:
                answer = QMessageBox.question(self, tr("Projekt ersetzen"), tr("Der gewählte Ordner enthält bereits ein Projekt. Ersetzen?"), QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if answer != QMessageBox.Yes:
                    return False
        try:
            self.document.project.save(folder)
            self.document.undo.setClean()
            self._title()
            self.statusBar().showMessage(tr("Projekt gespeichert: {folder}", folder=folder), 6000)
            self._drop_recovery()
            self._remember_current()
            return True
        except (ValueError, OSError) as error:
            self.error(tr("Speichern fehlgeschlagen: {error}", error=error))
            return False

    def error(self, text):
        self.status_label.setText(text)
        QMessageBox.warning(self, "Mapeditor", text)

    # ------------------------------------------------------------------ Kopieren und Einfügen (Issue #2)
    def copy_points(self):
        from PySide6.QtCore import QMimeData
        from . import kopieren
        try:
            daten = kopieren.kopie(self.document)
        except ValueError as error:
            self.status_label.setText(meldung(error))
            return False
        text = kopieren.als_text(daten)
        mime = QMimeData()
        mime.setData(kopieren.MIME, text.encode("utf-8"))
        mime.setText(text)
        QApplication.clipboard().setMimeData(mime)
        self.status_label.setText(tr("{anzahl} Punkte kopiert · in einer offenen Strecke mit Strg+V einfügen.",
                                     anzahl=len(daten["punkte"])))
        return True

    def cut_points(self):
        if self.document.project.readonly or not self.copy_points():
            return
        anzahl = len(self.document.selected_indices)
        self.document.delete()
        self.status_label.setText(tr("{anzahl} Punkte ausgeschnitten · Strg+Z: rückgängig.", anzahl=anzahl))

    def paste_points(self):
        from . import kopieren
        mime = QApplication.clipboard().mimeData()
        roh = bytes(mime.data(kopieren.MIME)).decode("utf-8") if mime is not None and mime.hasFormat(kopieren.MIME) else             (mime.text() if mime is not None else "")
        try:
            plan = self.document.paste_points(kopieren.aus_text(roh))
        except ValueError as error:
            self.map.overlap_marks = []
            self.status_label.setText(meldung(error))
            return
        # Das eingefügte Stück ist gewählt: im Auswahlwerkzeug verschiebt Ziehen es, statt einen Punkt zu setzen
        self.set_mode("auswahl")
        a, b = plan["neu"]
        teile = [tr("{anzahl} Punkte eingefügt", anzahl=b - a + 1),
                 tr("hinten angehängt") if plan["hinten"] else tr("vorne angefügt")]
        if plan["umgedreht"]:
            teile.append(tr("umgedreht"))
        if plan["verschoben"] is not None:
            teile.append(tr("an das offene Ende verschoben ({abstand} m entfernt)", abstand=round(plan["abstand_m"])))
        if plan["versatz_m"]:
            teile.append(tr("Streckenmeter um {meter} m verschoben", meter=round(plan["versatz_m"], 1)))
        marken = [point_xyz(self.document.points[i]) for i in plan["ueberschneidungen"]]
        self.map.set_overlap_marks([(x, z) for x, _, z in marken])
        if marken:
            teile.append(tr("überschneidet die Strecke an {anzahl} Punkten (rot) · „Überstand abschneiden“ oder verschieben",
                            anzahl=len(marken)))
        teile.append(tr("Strg+Z: rückgängig"))
        self.status_label.setText(" · ".join(teile))

    def open_here(self):
        try:
            self.document.open_here()
        except ValueError as error:
            self.status_label.setText(meldung(error))
            return
        self.set_mode("auswahl")
        self.status_label.setText(tr("Runde an der Auswahl geöffnet · Strg+V fügt kopierte Punkte an ein offenes Ende."))

    def reverse_selection(self):
        try:
            self.document.reverse_selection()
        except ValueError as error:
            self.status_label.setText(meldung(error))
            return
        self.set_mode("auswahl")
        self.status_label.setText(tr("Reihenfolge umgekehrt · Strg+Z: rückgängig."))

    def cut_overhang(self):
        try:
            anzahl = self.document.cut_overhang()
        except ValueError as error:
            self.status_label.setText(meldung(error))
            return
        self.set_mode("auswahl")
        self.map.set_overlap_marks([])
        self.status_label.setText(tr("{anzahl} überstehende Punkte entfernt · Strg+Z: rückgängig.", anzahl=anzahl))

    def show_help(self):
        self.help_dialog = HelpDialog(self)
        self.help_dialog.show()

    def _busy(self, running):
        self.busy_label.setVisible(running)
        self.cancel_button.setVisible(running)

    # ------------------------------------------------------------------ Umgebung (Bäume, Gebäude …)
    def _apply_env_catalog(self):
        """Katalog in Karte und Reiter eintragen (nach dem Laden und nach jedem Neuaufbau der Oberfläche)."""
        self.map.env_index = umgebung.katalog_index(self.env_catalog)
        self.env_panel.set_catalog(self.env_catalog)
        self.map.viewport().update()

    def set_env_catalog(self, katalog):
        self.env_catalog = katalog
        self._apply_env_catalog()

    def _results_tab_changed(self, *args):
        self._refresh_bank_panel()
        if self.results.currentWidget() is self.env_panel and self.env_catalog is None and self.interactive:
            self.load_env_catalog()

    def load_env_catalog(self, neu=False):
        """Katalog im Arbeitsprozess laden (die Oberfläche bleibt frei); ``neu`` baut ihn aus dem Spielordner neu auf."""
        if self.env_job:
            return
        try:
            root = toolkit_path(self.document.project.manifest.get("werkzeugkasten"))
        except ValueError as error:
            self.env_panel.set_catalog(None, meldung(error))
            return
        self.env_job = ProcessJob(self)
        self.env_job.done.connect(self._env_catalog_done)
        self.env_panel.status.setText(tr("Katalog wird geladen …") if not neu else tr("Katalog wird neu aufgebaut (das dauert) …"))
        self.env_panel.reload_button.setEnabled(False)
        self.env_job.start(worker_command(), {"task": "umgebung_katalog", "neu": neu, "toolkit": str(root)})

    def _env_catalog_done(self, code, text):
        job, self.env_job = self.env_job, None
        if job:
            job.deleteLater()
        self.env_panel.reload_button.setEnabled(True)
        try:
            result = json.loads(text)
            if code != 0 or "error" in result:
                raise ValueError(result.get("error", text))
            self.set_env_catalog(result)
        except (ValueError, TypeError) as error:
            self.env_panel.set_catalog(self.env_catalog, tr("Katalog nicht verfügbar: {error}", error=str(error)[:300]))

    def _env_model_chosen(self, modell):
        """Ein Modell im Katalog gewählt: die Karte platziert es mit dem nächsten Klick."""
        self.map.env_model = modell
        if modell:
            self.map.terrain_armed = None
        if modell and self.map.mode not in ("umgebung", "streuen"):
            self.set_mode("umgebung", focus=False)
        self.map.viewport().update()

    def _terrain_armed(self, art):
        """Knopf „Hügel/Berg setzen“: der nächste Klick auf die Karte setzt die Form."""
        self.map.terrain_armed = art
        if art:
            self.map.env_model = None
            self.map.terrain_radius = self.env_panel.terrain_values()["radius_m"]
            if self.map.mode != "umgebung":
                self.set_mode("umgebung", focus=False)
        self.map.viewport().update()

    def _terrain_place(self, art, x, z):
        if not self.document.env_editable:
            return
        form = self.document.terrain_place(art, x, z, **self.env_panel.terrain_values())
        self.map.terrain_armed = None
        self.env_panel.disarm_terrain()
        if form:
            problem = umgebung.form_problem(art, form, self.document.project.manifest.get("versatz", [0, 0, 0]))
            self.status_label.setText(tr("{name} „{id}“ gesetzt ({hoehe} m, Radius {radius} m) · Strg+Z macht rückgängig.",
                                         name=tr(umgebung.FORM_NAMEN[art]), id=form["id"], hoehe=zahl(form["hoehe_m"], 0),
                                         radius=zahl(form["radius_m"], 0))
                                      + (" ⚠ " + tr(problem) if problem else ""))

    def _terrain_values(self, felder):
        if self.document.terrain_selected:
            self.document.terrain_set(self.document.terrain_selected, **felder)
        if "radius_m" in felder:
            self.map.terrain_radius = float(felder["radius_m"])

    def _env_settings_changed(self):
        settings = self.env_panel.scatter_settings()
        self.map.env_form, self.map.env_radius = settings["form"], float(settings["radius"])
        self.map.viewport().update()

    def _env_preview_request(self, modell):
        """Silhouette des gewählten Modells: aus dem Zwischenspeicher oder im Arbeitsprozess erzeugen."""
        path = env_cache_folder() / (hashlib.sha1(modell.encode("utf-8")).hexdigest()[:20] + ".png")
        if path.is_file():
            self.env_panel.set_preview(modell, path)
            return
        self.env_preview_wanted = modell
        if self.env_preview_job:
            return
        try:
            root = toolkit_path(self.document.project.manifest.get("werkzeugkasten"))
        except ValueError:
            self.env_panel.set_preview(modell, None)
            return
        self.env_preview_job = ProcessJob(self)
        self.env_preview_job.done.connect(lambda code, text, modell=modell, path=path: self._env_preview_done(modell, path, code, text))
        self.env_preview_job.start(worker_command(), {"task": "umgebung_vorschau", "modell": modell, "pfad": str(path),
                                                      "groesse": 160, "toolkit": str(root)})

    def _env_preview_done(self, modell, path, code, text):
        job, self.env_preview_job = self.env_preview_job, None
        if job:
            job.deleteLater()
        ok = code == 0 and Path(path).is_file()
        self.env_panel.set_preview(modell, path if ok else None)
        wanted, self.env_preview_wanted = self.env_preview_wanted, None
        if wanted and wanted != modell and wanted == self.env_panel.armed_model():
            self._env_preview_request(wanted)

    def _env_obstacle(self):
        """Abstand zur Strecke für das Bepflanzen: Vorschaugeometrie (Mauer), sonst die Eingabegeometrie."""
        preview = self.preview if self.preview and self.preview_revision == self.document.revision else None
        try:
            if preview and preview.get("sides"):
                analysis = self.analysis if self.analysis and not self.map.analysis_stale else None
                return umgebung.Hindernis.aus_vorschau(preview, analysis)
            return umgebung.Hindernis.aus_eingabe(self.map.input) if self.map.input.get("pos") else None
        except (KeyError, ValueError, IndexError):
            return None

    def _env_place(self, x, z):
        modell = self.env_panel.armed_model() or self.map.env_model
        if not modell or not self.document.env_editable:
            return
        objekt = self.document.env_place(modell, x, z)
        self.status_label.setText(tr("{modell} platziert bei X {x} · Z {z} · Strg+Z macht rückgängig.",
                                     modell=umgebung.kurzname(modell), x=zahl(objekt["x"], 1), z=zahl(objekt["z"], 1)))

    def _env_scatter(self, area):
        """Fläche bepflanzen: Einstellungen aus dem Reiter, Ergebnis als ein Undo-Schritt und eine Gruppe."""
        settings = self.env_panel.scatter_settings()
        modelle = self.env_panel.chosen_models()
        if not modelle:
            self.status_label.setText(tr("Zum Bepflanzen zuerst im Katalog ein oder mehrere Modelle wählen."))
            return
        shift = self.document.project.manifest.get("versatz", [0, 0, 0])
        try:
            gebiet = (umgebung.Gebiet("pinsel", punkte=area["punkte"], radius=area["radius"]) if area["form"] == "pinsel"
                      else umgebung.Gebiet("rechteck", rechteck=area["rechteck"]))
            gruppe = settings["gruppe"] or umgebung.neue_gruppe(self.document.env_objects, tr("Streuung {n}"))
            objekte, info = umgebung.streuen(
                gebiet, modelle, settings["dichte"], settings["seed"], hindernis=self._env_obstacle(),
                abstand_fahrbahn=settings["abstand_fahrbahn"], abstand_objekte=settings["abstand_objekte"],
                skala=settings["skala"], drehung_zufall=settings["drehung_zufall"], gruppe=gruppe,
                vorhanden=self.document.env_objects, versatz=shift)
        except umgebung.StreuFehler as error:
            self.status_label.setText(
                tr("Zu viele Objekte ({anzahl}, höchstens {maximum}): Dichte oder Fläche verkleinern.", **error.werte)
                if error.code == "zu_viele" else tr("Bepflanzen nicht möglich: keine Fläche oder kein Modell."))
            return
        if not objekte:
            if info["verworfen_welt"] > max(info["verworfen_strecke"], info["verworfen_abstand"]):
                self.status_label.setText(tr("Nichts gesetzt: Die Fläche liegt außerhalb des Streckenbereichs "
                                             "(Welt X −920 … 1128, Z −1008 … 1040 m, Versatz eingerechnet)."))
            else:
                self.status_label.setText(tr("Nichts gesetzt: Die Fläche liegt auf der Strecke, zu nah daran oder die Dichte ist zu gering."))
            return
        self.document.env_add(objekte, tr("Fläche bepflanzen ({anzahl} Objekte)", anzahl=len(objekte)))
        text = tr("{anzahl} Objekte in der Gruppe „{gruppe}“ gesetzt · Strg+Z macht rückgängig.", anzahl=len(objekte), gruppe=gruppe)
        if len(objekte) < info["gewuenscht"]:
            text += " " + tr("({gewuenscht} gewünscht; der Rest lag zu nah an Strecke und Nachbarn)", gewuenscht=info["gewuenscht"])
        self.status_label.setText(text)

    def _env_properties(self, felder):
        if self.document.env_selected:
            self.document.env_set(self.document.env_selected, **felder)

    def _env_ground_cover(self, on):
        self.document.env_set_ground_cover(on)

    def _env_select_group(self):
        primary = self.document.env_object(self.document.env_primary) if self.document.env_primary else None
        if primary and primary.get("gruppe"):
            self.document.env_select(self.document.env_group(primary["gruppe"]), primary["id"])

    def _env_delete_group(self):
        primary = self.document.env_object(self.document.env_primary) if self.document.env_primary else None
        if primary and primary.get("gruppe"):
            self.document.env_delete(self.document.env_group(primary["gruppe"]))

    def jump_env_object(self, kennung):
        """Zu einem Umgebungsobjekt springen (Meldung angeklickt): wählen, Karte mittig, Werkzeug „Umgebung“."""
        objekt = self.document.env_object(kennung)
        if objekt is None:
            objekt = self.document.terrain_form(kennung)
            if objekt is None:
                return False
            self.document.terrain_select(kennung)
            self.map.centerOn(objekt["x"], objekt["z"])
            self.map.viewport().update()
            return True
        self.document.env_select({kennung}, kennung)
        self.map.centerOn(objekt["x"], objekt["z"])
        self.map.viewport().update()
        return True

    def _env_selection_changed(self):
        self._refresh_env_panel()

    def _refresh_env_panel(self):
        if not hasattr(self, "env_panel"):
            return
        document = self.document
        chosen = [o for o in (document.env_object(k) for k in sorted(document.env_selected)) if o]
        chosen.sort(key=lambda o: o["id"] != document.env_primary)       # das erste Objekt zeigt die Werte
        project = document.project
        self.env_panel.show_selection(chosen, self.map.env_index, anzahl=len(document.env_objects),
                                      bodenbewuchs=project.umgebung.get("bodenbewuchs") if project.umgebung is not None else False,
                                      editierbar=document.env_editable)
        form = document.terrain_form(document.terrain_selected) if document.terrain_selected else None
        art = umgebung.form_art(document.env, form["id"]) if form else None
        self.env_panel.show_terrain(art, form, umgebung.form_problem(art, form, project.manifest.get("versatz", [0, 0, 0]))
                                    if form else None, editierbar=document.env_editable)
        self.map.viewport().update()

    def _env_changed(self):
        """Nur die Umgebung wurde geändert: Karte, Reiter, Titel; Vorschau und Fahrlinie bleiben gültig."""
        self._refresh_env_panel()
        self._title()
        self.map.viewport().update()
        count = len(self.document.env_objects)
        self.status_label.setText(tr("Umgebung geändert · {anzahl} Objekte", anzahl=zahl(count, 0)))
        self._schedule_env_check()

    # --- Prüfung (UM-01 … UM-05)
    def _schedule_env_check(self):
        if self.document.env_objects or self.document.terrain_forms or self.env_issues:
            self.env_check_timer.start()

    def _stop_env_check(self):
        job, self.env_check_job = self.env_check_job, None
        if job:
            job.done.disconnect()
            job.cancel()
            job.process.waitForFinished(500)
            job.deleteLater()

    def request_env_check(self):
        """Prüfregeln der Umgebung im Arbeitsprozess; die Meldungen erscheinen in der Prüfliste (Klick springt zum Objekt)."""
        project = self.document.project
        if not self.document.env_objects and not self.document.terrain_forms:
            if self.env_issues:
                self.env_issues = []
                self.render_issues()
            return
        if not project.closed:
            return
        self._stop_env_check()
        try:
            root = toolkit_path(project.manifest.get("werkzeugkasten"))
        except ValueError:
            return
        payload = {"task": "umgebung_pruefen", "umgebung": copy.deepcopy(project.umgebung), "raw": copy.deepcopy(project.raw),
                   "basis": str(project.basis), "shift": project.manifest.get("versatz", [0, 0, 0]), "toolkit": str(root)}
        revision = self.document.env_revision, self.document.revision
        self.env_check_job = ProcessJob(self)
        self.env_check_job.done.connect(lambda code, text: self._env_check_done(code, text, revision))
        self.env_check_job.start(worker_command(), payload)

    def _env_check_done(self, code, text, revision):
        job, self.env_check_job = self.env_check_job, None
        if job:
            job.deleteLater()
        if code == -2 or revision != (self.document.env_revision, self.document.revision):
            return
        try:
            result = json.loads(text)
            if code != 0 or "error" in result:
                raise ValueError(result.get("error", text))
            self.env_issues = list(result["issues"])
        except (ValueError, TypeError, KeyError) as error:
            self.env_issues = [{"id": "UMG_PRUEFUNG", "kurz": "UM", "level": "Hinweis", "schwere": "hinweis", "stufe": "eingabe",
                                "text": tr("Die Umgebung konnte nicht geprüft werden: {error}", error=str(error)[:200])}]
        self.render_issues()

    def _env_issue_list(self, present):
        """Meldungen der Umgebung für die Prüfliste: Dateifehler, Prüfregeln UMG_*; ohne Doppelte."""
        issues = []
        error = self.document.project.umgebung_fehler
        if error:
            issues.append({"id": "UMG_DATEI", "kurz": "UM-01", "level": "Fehler", "schwere": "fehler", "stufe": "eingabe",
                           "text": tr("umgebung.json ist nicht lesbar und bleibt unverändert: {error}", error=error)})
        seen = {(i.get("id"), (i.get("ort") or {}).get("objekt"), i.get("text")) for i in present}
        for issue in self.env_issues:
            key = (issue.get("id"), (issue.get("ort") or {}).get("objekt"), issue.get("text"))
            if key not in seen:
                seen.add(key)
                issues.append(issue)
        return issues

    # ------------------------------------------------------------------ Sprache
    def change_language(self, code, ask=True):
        """Sprache wechseln und speichern. Wirkt sofort ohne Neustart: das Fenster baut seine Bedienelemente in der
        neuen Sprache neu auf; Projekt, Rückgängig-Verlauf, Vorschau, Fahrlinie, Kartenausschnitt und ein laufender Bau
        bleiben erhalten. ``ask`` bleibt für alte Aufrufer (es gibt nichts mehr zu fragen)."""
        if code not in SPRACHEN:
            return None
        ansicht.save_language(code)
        if code != sprache():
            self.retranslate(code)
        return self

    def retranslate(self, code):
        state = self._ui_state()
        setze_sprache(code)
        self._rebuild_ui()
        self._restore_ui_state(state)
        self.statusBar().showMessage(tr("Sprache: {name}", name=SPRACHEN[code]), 5000)

    def _ui_state(self):
        """Was der Nutzer gerade sieht (nur Ansicht; Daten liegen im Dokument und bleiben ohnehin)."""
        self._save_layout()
        view = self.map
        return {"transform": view.transform(), "center": view.mapToScene(view.viewport().rect().center()),
                "station": view.station, "mode": view.mode, "highlight": view.highlight,
                "focus_curve": view.focus_curve, "tab": self.results.currentIndex(), "env_model": view.env_model,
                "category": self.category.currentData(), "section": self.section_choice.currentIndex(),
                "find": self.find_field.text(), "issue_filter": self.issue_filter.currentData(),
                "issue_search": self.issue_search.text(), "horizontal": self.horizontal_split.sizes(),
                "vertical": self.vertical_split.sizes(),
                "view_dialog": self.view_dialog is not None and self.view_dialog.isVisible(),
                "help": getattr(self, "help_dialog", None) is not None and self.help_dialog.isVisible(),
                "output": getattr(self, "output_file", None)}

    def _rebuild_ui(self):
        """Menüs, Werkzeugleiste, Seiten, Karte und Statuszeile neu erzeugen (Texte in der aktiven Sprache)."""
        keep = {self.preview_timer, self.analysis_timer, self.autosave_timer, self.env_check_timer}
        old = [child for child in self.children()
               if isinstance(child, (QAction, QActionGroup, QTimer)) and child not in keep]
        try:
            self.document.selection_changed.disconnect(self.map._selection)
        except (RuntimeError, TypeError):
            pass
        for dialog in (self.view_dialog, getattr(self, "help_dialog", None)):
            if dialog is not None:
                dialog.close()
        self.view_dialog = self.help_dialog = None
        self.removeToolBar(self.toolbar)
        self.toolbar.deleteLater()
        self.setMenuBar(QMenuBar(self))                # Qt löscht die alte Leiste
        self.setStatusBar(QStatusBar(self))
        for child in old:
            if isinstance(child, QTimer):
                child.stop()
            child.deleteLater()
        self.feature_actions, self.feature_checks = {}, {}
        self._build_ui()                               # neues Zentralelement ersetzt (und löscht) das alte
        self._after_build()

    def _restore_ui_state(self, state):
        if self.preview is not None:
            fit, self.fit_after_preview = self.fit_after_preview, False
            self.install_preview(self.preview, self.preview_revision)
            self.fit_after_preview = fit
        else:
            self.render_issues()
        if self.analysis is not None and self.analysis_key != self.current_key():
            for widget in (self.map, self.speed):       # veraltete Fahrlinie bleibt sichtbar, als veraltet markiert
                widget.analysis = self.analysis
            self._update_summary()
            self._mark_analysis_stale()
        if self.analysis_job is not None:
            self._set_analysis_note(tr("Fahrlinie wird berechnet …"))
        self._busy(self.job is not None)
        self.cancel_action.setEnabled(self.job is not None)
        self.horizontal_split.setSizes(state["horizontal"])
        self.vertical_split.setSizes(state["vertical"])
        self.results.setCurrentIndex(state["tab"])
        index = self.category.findData(state["category"])
        if index >= 0:
            self.category.setCurrentIndex(index)
        if 0 <= state["section"] < self.section_choice.count():
            self.section_choice.setCurrentIndex(state["section"])
        self.find_field.setText(state["find"])
        index = self.issue_filter.findData(state["issue_filter"])
        if index >= 0:
            self.issue_filter.setCurrentIndex(index)
        self.issue_search.setText(state["issue_search"])
        self.set_mode(state["mode"], focus=False)
        if state.get("env_model"):
            self.env_panel.select_models([state["env_model"]])
        self.map.highlight = state["highlight"]
        self.map.focus_curve = state["focus_curve"]
        if state["station"] is not None:
            self.set_station(state["station"])
        if state["output"]:
            try:
                self.load_output(state["output"])
            except (ValueError, OSError):
                pass
            self.results.setCurrentIndex(state["tab"])

        def view(transform=state["transform"], center=state["center"]):
            self.map.setTransform(transform)
            self.map.centerOn(center)
        view()
        QTimer.singleShot(0, self.map, view)           # nach dem ersten Layout der neuen Karte noch einmal
        if state["view_dialog"]:
            self.open_view_dialog()
        if state["help"]:
            self.show_help()
        old = self.build_dialog
        if old is not None and old.isVisible() and not old.job and not old.inject_job and not old.status_job:
            fields = (old.name.text(), old.note.text(), old.code.currentData(), old.track_name.text(),
                      old.pvs.currentData(), old.advanced_toggle.isChecked(), old.game_state, old.status_read)
            old.close()
            self.open_build()                          # Baudialog in der neuen Sprache, Eingaben übernommen
            d = self.build_dialog
            d.name.setText(fields[0])
            d.note.setText(fields[1])
            d.track_name.setText(fields[3])
            d.pvs.setCurrentIndex(max(0, d.pvs.findData(fields[4])))
            d.advanced_toggle.setChecked(fields[5])
            if d.status_job is None:
                d.game_state, d.status_read = fields[6], fields[7]
                d._fill_codes()
            d.code.setCurrentIndex(max(0, d.code.findData(fields[2])))
            d._update_code_info()

    def closeEvent(self, event: QCloseEvent):
        if self.build_dialog and self.build_dialog.job:
            self.build_dialog.raise_()
            self.build_dialog.phase.setText(tr("Bau läuft · zuerst Abbrechen wählen und das Bauende abwarten."))
            event.ignore()
            return
        if not getattr(self, "_rebuilding", False) and not self.confirm_discard():
            event.ignore()
            return
        self.preview_timer.stop()
        self.preview_pending = False
        self._stop_analysis()
        self.autosave_timer.stop()
        self.env_check_timer.stop()
        self._stop_env_check()
        for job in (self.env_job, self.env_preview_job):
            if job:
                job.done.disconnect()
                job.process.kill()
                job.process.waitForFinished(500)
        self.env_job = self.env_preview_job = None
        if self.document.undo.isClean():
            self._drop_recovery()
        self._save_layout()
        if self.job:
            self.job.done.disconnect()
            self.job.cancel()
            self.job.process.waitForFinished(1500)
            self.job._kill_if_running()
            self.job.process.waitForFinished(1000)
        event.accept()
