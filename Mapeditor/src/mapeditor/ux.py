"""Bausteine der Bedienoberfläche: Zahlenformat, Symbole, Auswahl-Editoren, Startbildschirm, Hilfe."""

from __future__ import annotations

import math

from PySide6.QtCore import QLocale, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFrame, QGridLayout, QLabel, QPushButton, QSizePolicy, QStyledItemDelegate,
    QTextBrowser, QToolButton, QVBoxLayout, QWidget,
)

from .i18n import N_, html, qlocale, sprache, tr

GERMAN = QLocale(QLocale.German, QLocale.Germany)


# ------------------------------------------------------------------ Zahlen
def zahl(value, decimals=1, unit=""):
    """Anzeigezahl im Format der Oberflächensprache: deutsch ``4.903,1 m`` (Tausenderpunkt, Dezimalkomma),
    englisch ``4,903.1 m`` (Tausenderkomma, Dezimalpunkt)."""
    try:
        text = qlocale().toString(float(value), "f", decimals)
    except (TypeError, ValueError):
        return str(value)
    return f"{text} {unit}".strip()


def edit_text(value):
    """Wert für ein Eingabefeld: Dezimalzahl der Oberflächensprache ohne Tausendertrennung, höchstens
    3 Nachkommastellen, ja/nein (englisch yes/no)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return tr("ja") if value else tr("nein")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return str(value)
        text = f"{value:.3f}".rstrip("0").rstrip(".")
        if text in ("-0",):
            return "0"
        return text if sprache() == "en" else text.replace(".", ",")
    return None


BOOL_WORDS = {"ja": True, "nein": False, "true": True, "false": False, "an": True, "aus": False,
              "yes": True, "no": False, "on": True, "off": False}

#: Feste Auswahlwerte für Eigenschaften (Editor als Klappliste statt Freitext)
CHOICES = {
    "typ": ["gerade", "bogen", "klothoide"],     # Werte der Definition; angezeigt wird tr(wert)
    "richtung": ["links", "rechts"],
    "seite": ["links", "rechts"],
    "pole_seite": ["links", "rechts"],
    "schliessen": ["verteilen", "fehler"],
    # Boxengasse: nur die Tempolimits der Spielstrecken (TrackPitData, docs/hooks_untersuchung.md)
    "tempolimit_kmh": [60.0, 80.0],
}


#: Anzeigetexte der Auswahlwerte (Übersetzung; gespeichert wird immer der deutsche Wert der Definition)
CHOICE_WORDS = [N_("gerade"), N_("bogen"), N_("klothoide"), N_("links"), N_("rechts"), N_("verteilen"), N_("fehler")]


def choice_text(value):
    """Auswahlwert der Definition in der Oberflächensprache (``links`` → ``left``, Zahlen als Text)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return edit_text(float(value))
    return tr(value) if isinstance(value, str) and value in CHOICE_WORDS else value


def choice_value(key, text):
    """Eingetippten oder gewählten Anzeigetext zurück in den Wert der Definition (``left`` → ``links``)."""
    for value in CHOICES.get(key, []):
        if text.strip().lower() in (value, choice_text(value).lower()):
            return value
    if any(isinstance(v, float) for v in CHOICES.get(key, [])):
        raise ValueError(tr("Bitte einen der Werte {werte} wählen.", werte=", ".join(choice_text(v) for v in CHOICES[key])))
    return text


def help_html():
    """Bedienungshilfe (F1) in der Oberflächensprache: ``i18n/hilfe_<sprache>.html``."""
    return html("hilfe")


# ------------------------------------------------------------------ Symbole
_ICON_FONT = None


def _icon_font():
    global _ICON_FONT
    if _ICON_FONT is None:
        families = set(QFontDatabase.families())
        _ICON_FONT = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in families), "")
    return _ICON_FONT


#: Glyphen aus Segoe MDL2 Assets / Segoe Fluent Icons (Windows 10/11)
GLYPHS = {
    "auswahl": "", "punkte": "", "undo": "", "redo": "", "einpassen": "",
    "zoom_in": "", "zoom_out": "", "runde": "", "pruefen": "", "bauen": "",
    "spieltest": "", "abbrechen": "", "neu": "", "oeffnen": "", "vorlage": "",
    "vorschlag": "", "hilfe": "", "speichern": "",
    "umgebung": "", "streuen": "", "bild": "",
}


def has_icons():
    return bool(_icon_font())


def icon(name, color="#F1F5F9", size=20):
    """Symbol aus der Windows-Symbolschrift; ohne Schrift ein leeres Symbol (Text bleibt sichtbar)."""
    family = _icon_font()
    if not family or name not in GLYPHS:
        return QIcon()
    result = QIcon()
    for mode, tone in ((QIcon.Normal, color), (QIcon.Disabled, "#6B7C8C")):
        pixmap = QPixmap(QSize(size, size) * 2)
        pixmap.setDevicePixelRatio(2)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.TextAntialiasing)
        font = QFont(family)
        font.setPixelSize(int(size * 0.8))
        painter.setFont(font)
        painter.setPen(QColor(tone))
        painter.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, GLYPHS[name])
        painter.end()
        result.addPixmap(pixmap, mode)
    return result


# ------------------------------------------------------------------ Eigenschaften: Klapplisten
class Section(QWidget):
    """Einklappbarer Bereich: Kopfzeile mit Titel und Kurzinfo, darunter ``body``. Zustand merkt sich der Aufrufer."""

    toggled = Signal(bool)

    def __init__(self, title, expanded=True, parent=None):
        super().__init__(parent)
        self.title = title
        self.head = QToolButton()
        self.head.setObjectName("heading")
        self.head.setCheckable(True)
        self.head.setAutoRaise(True)
        self.head.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.head.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.body = QWidget()
        self.layout_body = QVBoxLayout(self.body)
        self.layout_body.setContentsMargins(0, 0, 0, 0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.head)
        layout.addWidget(self.body, 1)
        self.summary = ""
        self.head.setChecked(expanded)
        self.head.toggled.connect(self._toggled)
        self._show(expanded)

    def _show(self, on):
        self.body.setVisible(on)
        self.head.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
        self._label()

    def _label(self):
        self.head.setText(f"{self.title}  ·  {self.summary}" if self.summary else self.title)

    def _toggled(self, on):
        self._show(on)
        self.toggled.emit(on)

    def expanded(self):
        return self.head.isChecked()

    def set_expanded(self, on):
        self.head.setChecked(on)

    def set_summary(self, text):
        self.summary = text
        self._label()


class ChoiceDelegate(QStyledItemDelegate):
    """Klappliste für ja/nein und feste Werte; alle anderen Felder bleiben Textfelder."""

    def __init__(self, table):
        super().__init__(table)
        self.table = table

    def options(self, row):
        if not 0 <= row < len(self.table.entries):
            return None
        key, actual, fallback, _ = self.table.entries[row]
        template = actual if actual is not None else fallback
        if key in CHOICES:
            return [choice_text(v) for v in CHOICES[key]], False
        if isinstance(template, bool) or key.endswith(".aktiv") or key in ("aktiv", "umkehren", "partial"):
            return [tr("ja"), tr("nein")], False
        if key == "uebergang":
            return [tr("ja"), tr("nein")], True          # oder Länge in m eintippen
        return None

    def createEditor(self, parent, option, index):
        choice = self.options(index.row()) if index.column() == 1 else None
        if choice is None:
            return super().createEditor(parent, option, index)
        values, editable = choice
        box = QComboBox(parent)
        box.setEditable(editable)
        box.addItems(values)
        box.activated.connect(lambda *_: self.commitData.emit(box))
        return box

    def setEditorData(self, editor, index):
        if isinstance(editor, QComboBox):
            text = index.data() or ""
            i = editor.findText(text)
            if i >= 0:
                editor.setCurrentIndex(i)
            elif editor.isEditable():
                editor.setEditText(text)
            else:
                editor.setCurrentIndex(-1)
            return
        super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):
        if isinstance(editor, QComboBox):
            if editor.currentText() != (index.data() or ""):
                model.setData(index, editor.currentText())
            return
        super().setModelData(editor, model, index)


# ------------------------------------------------------------------ Startbildschirm
class WelcomePanel(QFrame):
    """Einstieg bei leerem Projekt: Zeichnen, Vorlage, Vorschlag, Bild, Öffnen, Beispiel."""

    chosen = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("welcome")
        self.setFrameShape(QFrame.StyledPanel)
        self.setAutoFillBackground(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(10)
        title = QLabel(tr("Neue Strecke"))
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(title)
        intro = QLabel(tr("Wie möchtest du beginnen?"))
        layout.addWidget(intro)
        grid = QGridLayout()
        grid.setSpacing(8)
        self.buttons = {}
        for row, (key, glyph, text, info) in enumerate((
                ("zeichnen", "punkte", tr("Strecke zeichnen"), tr("Punkte in die Karte klicken, dann „Runde schließen“")),
                ("vorlage", "vorlage", tr("Aus Vorlage …"), tr("Echte Strecke (TUM-CSV), GPX-Spur oder Spielstrecke")),
                ("vorschlag", "vorschlag", tr("Vorschlag erzeugen …"), tr("Zufällige, gültige Strecke mit Länge und Kurvenzahl")),
                ("bild", "bild", tr("Aus Bild nachzeichnen …"),
                 tr("Kartenausschnitt (Bildschirmfoto) als Vorlage, Strecke mit Klicks nachziehen")),
                ("oeffnen", "oeffnen", tr("Projekt öffnen …"), tr("Vorhandenen Projektordner laden")),
                ("beispiel", "auswahl", tr("Beispiel ansehen"), tr("Teststrecke aus dem Werkzeugkasten (fahrbar im Spiel)")))):
            button = QPushButton(icon(glyph), "  " + text)
            button.setMinimumWidth(210)
            button.setStyleSheet("text-align: left; padding-left: 10px;")
            button.clicked.connect(lambda checked=False, key=key: self.chosen.emit(key))
            label = QLabel(info)
            label.setWordWrap(True)
            grid.addWidget(button, row, 0)
            grid.addWidget(label, row, 1)
            self.buttons[key] = button
        layout.addLayout(grid)
        self.recent_title = QLabel(tr("Zuletzt geöffnet"))
        self.recent_title.setObjectName("heading")
        layout.addWidget(self.recent_title)
        self.recent_box = QVBoxLayout()
        self.recent_box.setSpacing(4)
        layout.addLayout(self.recent_box)
        self.recent_buttons = []
        self.set_recent([])
        foot = QLabel(tr("Tipp: F1 zeigt jederzeit die Bedienung."))
        foot.setEnabled(False)
        layout.addWidget(foot)
        self.setMaximumWidth(640)

    def set_recent(self, paths):
        """Bis zu drei zuletzt geöffnete Projekte als Knöpfe (Signal ``chosen("zuletzt:<Pfad>")``)."""
        from pathlib import Path
        for button in self.recent_buttons:
            button.deleteLater()
        self.recent_buttons = []
        for path in paths[:3]:
            p = Path(path)
            name = p.parent.name if p.name in ("projekt.json", "definition.json") else p.stem if p.suffix else p.name
            button = QPushButton(icon("oeffnen"), f"  {name}")
            button.setToolTip(str(path))
            button.setStyleSheet("text-align: left; padding-left: 10px;")
            button.clicked.connect(lambda checked=False, path=path: self.chosen.emit("zuletzt:" + str(path)))
            self.recent_box.addWidget(button)
            self.recent_buttons.append(button)
        self.recent_title.setVisible(bool(self.recent_buttons))


# ------------------------------------------------------------------ Hilfe
class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Bedienung"))
        self.resize(720, 640)
        layout = QVBoxLayout(self)
        self.browser = QTextBrowser()
        self.browser.setHtml(help_html())
        layout.addWidget(self.browser)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Close).setText(tr("Schließen"))
        layout.addWidget(buttons)
