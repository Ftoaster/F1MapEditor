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
    ("raster", N_("Kartenebenen"), N_("Raster"), N_("Hilfslinien; Abstand siehe Maßstab unten rechts"), True),
    ("raender", N_("Kartenebenen"), N_("Fahrbahnränder"), N_("Berechnete Randsteine, Grün- und Kiesstreifen"), True),
    ("richtung", N_("Kartenebenen"), N_("Fahrtrichtung"), N_("Pfeile in Fahrtrichtung"), True),
    ("spielbereich", N_("Kartenebenen"), N_("Spielbereich ±1024 m"), N_("Die Strecke muss vollständig in diesem Rahmen liegen"), True),
    ("zonen", N_("Kartenebenen"), N_("DRS / Straight Mode"), N_("Zonen auf der Strecke"), True),
    ("ideallinie", N_("Kartenebenen"), N_("Ideallinie · Bremspunkte · Tafeln"),
     N_("Ideallinie aus dem Fahrlinienlauf: rot Bremsen, gelb Rollen, grün Vollgas; rote Dreiecke = Bremsbeginn, "
     "weiße Quadrate = Entfernungstafeln"), True),
    ("rennbetrieb", N_("Kartenebenen"), N_("Box · Startplätze · Sektoren"),
     N_("Boxengasse (orange), Boxen, Startaufstellung und Sektorgrenzen aus dem Fahrlinienlauf"), True),
    ("kurvennummern", N_("Kartenebenen"), N_("Kurvennummern"), N_("T1 … wie im Spiel; Klick auf eine Nummer zeigt die Kurve"), True),
    ("umgebung", N_("Kartenebenen"), N_("Umgebung"),
     N_("Bäume, Büsche, Gebäude … als Kreise in Katalogröße (Farbe je Kategorie); bearbeiten mit Werkzeug „Umgebung“ (U)"), True),
    ("eingabepunkte", N_("Kartenebenen"), N_("Eingabepunkte"), N_("Kontrollpunkte bzw. Segmentenden als Griffe"), True),
    ("beschriftungen", N_("Kartenebenen"), N_("Zonenbeschriftungen"), N_("Namen der DRS-/Straight-Mode-Zonen und der Boxengasse"), True),
    ("massstab", N_("Kartenebenen"), N_("Maßstab"), N_("Maßstab unten rechts"), True),
    ("kartenhinweise", N_("Kartenebenen"), N_("Hinweisschilder"), N_("Zustand oben und Bedienhinweis unten in der Karte"), True),
    ("seitenleiste", N_("Bereiche"), N_("Seitenleiste (Anzeige, Strecke)"), N_("Linke Leiste mit Ebenen und Objektliste"), True),
    ("eigenschaften", N_("Bereiche"), N_("Eigenschaften"), N_("Rechte Leiste mit Einstellungen"), True),
    ("unterer_bereich", N_("Bereiche"), N_("Profile und Auswertung (unten)"), N_("Reiter unter der Karte"), True),
    ("querneigungsfeld", N_("Bereiche"), N_("Querneigungsfeld"), N_("Feld „Querneigung“ in den Eigenschaften"), True),
    ("werkzeugleiste", N_("Bereiche"), N_("Werkzeugleiste"), N_("Knöpfe oben"), True),
    ("kopfzeile", N_("Bereiche"), N_("Kopfzeile (Projekt)"), N_("Projektname, Speicherzustand und Ordner"), True),
    ("statuszeile", N_("Bereiche"), N_("Statuszeile"), N_("Zustand, Prüfsumme und Werkzeug unten"), True),
    ("reiter_hoehe", N_("Reiter"), N_("Höhenprofil"), "", True),
    ("reiter_neigung", N_("Reiter"), N_("Querneigung"), "", True),
    ("reiter_querschnitt", N_("Reiter"), N_("Querschnitt"), "", True),
    ("reiter_geschwindigkeit", N_("Reiter"), N_("Geschwindigkeit"), "", True),
    ("reiter_pruefung", N_("Reiter"), N_("Prüfung"), "", True),
    ("reiter_auswertung", N_("Reiter"), N_("Auswertung"), "", True),
    ("reiter_umgebung", N_("Reiter"), N_("Umgebung"), N_("Katalog, Bepflanzen und Eigenschaften der Umgebungsobjekte"), True),
    ("fahrlinie_auto", N_("Automatik"), N_("Fahrlinie automatisch berechnen"),
     N_("Nach jeder Änderung rechnet der Generator im Hintergrund Ideallinie, Kurven, Bremspunkte, Tafeln und die "
     "Prüfung (je nach Strecke 10–50 s). Aus: nur mit „Prüfen“ (Strg+Enter)."), True),
    ("autosicherung", N_("Automatik"), N_("Autosicherung"),
     N_("Ungespeicherte Änderungen jede Minute in eine Wiederherstellungsdatei im Projektordner sichern"), True),
    ("letztes_projekt", N_("Automatik"), N_("Letztes Projekt beim Start öffnen"), N_("Ohne Angabe startet der Editor mit dem "
     "zuletzt geöffneten Projekt"), False),
    ("startbildschirm", N_("Automatik"), N_("Startbildschirm bei leerem Projekt"), N_("Auswahl Zeichnen/Vorlage/Vorschlag"), True),
]
GROUPS = [N_("Kartenebenen"), N_("Bereiche"), N_("Reiter"), N_("Automatik")]
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
    """Gespeicherte Oberflächensprache (``de``/``en``), Standard Deutsch."""
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
        self.setWindowTitle(tr("Ansicht anpassen"))
        layout = QVBoxLayout(self)
        intro = QLabel(tr("Was der Editor zeigt und automatisch tut. Änderungen wirken sofort und bleiben gespeichert."))
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
        buttons.button(QDialogButtonBox.Close).setText(tr("Schließen"))
        reset = QPushButton(tr("Alles einblenden"))
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
