"""Querneigung einfach einstellen: Stärke + Seite in Klartext statt Vorzeichen-Gradzahl.

Konvention der Definition: ``querneigung_grad`` > 0 = links höher (in Eingaberichtung). Hier wird
daraus „Kurveninnenseite tiefer“ usw. Für Kontrollpunkte interpoliert der Generator nur zwischen
Punkten mit Wert; damit eine Änderung lokal bleibt, werden die Nachbarn der geänderten Punkte auf
ihren bisherigen (interpolierten) Wert festgeschrieben.
"""

from __future__ import annotations

import bisect
import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from .i18n import N_, tr
from .project import point_xyz

KEY = "querneigung_grad"
FIA_MAX = 5.7            # wie strecke.pruefregeln.FIA_MAX_QUERNEIGUNG_GRAD (Prüfregel warnt darüber)
MAX_GRAD = 30.0          # Eingabegrenze (Querneigungsfeld und Querneigungsprofil)
AUTO_FULL_R = 80.0       # Automatik: ab diesem Radius abwärts volle Stärke …
AUTO_ZERO_R = 500.0      # … ab diesem Radius aufwärts flach (dazwischen linear in der Krümmung)

SIDES = {                # Schlüssel: Beschriftung
    "innen": N_("Corner inside lower"),
    "aussen": N_("Corner outside lower"),
    "links": N_("left higher"),
    "rechts": N_("right higher"),
}


# ------------------------------------------------------------------ Logik (ohne Qt-Widgets)
def base_value(raw):
    return float((raw.get("fahrbahn") or {}).get(KEY) or 0.0)


def signed(magnitude, side, turn):
    """Gradzahl der Definition aus Stärke, Seite und Kurvenrichtung (turn: +1 links, −1 rechts, 0 gerade)."""
    if side == "links":
        return magnitude
    if side == "rechts":
        return -magnitude
    if not turn:
        return 0.0                                  # Gerade: innen/außen gibt es nicht
    return -turn * magnitude if side == "innen" else turn * magnitude


def describe(value, turn):
    """Klartext für einen Wert: „flach“, „4,0° · Kurveninnenseite tiefer“, „2,0° · links höher“."""
    if abs(value) < 0.05:
        return tr("flat")
    from .ux import zahl
    amount = zahl(abs(value), 1) + "°"
    if turn:
        return f"{amount} · {tr(SIDES['innen' if value * turn < 0 else 'aussen'])}"
    return f"{amount} · {tr(SIDES['links' if value > 0 else 'rechts'])}"


def auto_magnitude(radius, maximum):
    """Automatik: enge Kurven stärker, weite flach; auf 0,5° gerundet."""
    if radius is None or not math.isfinite(radius) or radius <= 0:
        return 0.0
    k, k_full, k_zero = 1 / radius, 1 / AUTO_FULL_R, 1 / AUTO_ZERO_R
    share = min(1.0, max(0.0, (k - k_zero) / (k_full - k_zero)))
    return round(maximum * share * 2) / 2


def _stations(positions, closed):
    s = [0.0]
    for a, b in zip(positions, positions[1:]):
        s.append(s[-1] + math.dist((a[0], a[2]), (b[0], b[2])))
    length = s[-1] + (math.dist((positions[-1][0], positions[-1][2]), (positions[0][0], positions[0][2])) if closed else 0.0)
    return s, max(length, 1e-6)


def point_banks(points, closed, base=0.0):
    """Wirksame Querneigung je Kontrollpunkt wie im Generator: linear zwischen Punkten mit Wert
    (geschlossen: periodisch), ohne jeden Wert der Fahrbahn-Standard."""
    n = len(points)
    given = [(i, float(p[KEY])) for i, p in enumerate(points) if isinstance(p, dict) and p.get(KEY) is not None]
    if not given:
        return [base] * n
    if len(given) == 1:
        return [given[0][1]] * n
    s, length = _stations([point_xyz(p) for p in points], closed)
    indices = [g[0] for g in given]
    out = []
    for i in range(n):
        k = bisect.bisect_right(indices, i)            # given[k-1] ≤ i < given[k]
        before = given[k - 1] if k > 0 else None
        after = given[k - 1] if k > 0 and indices[k - 1] == i else given[k] if k < len(given) else None
        if before is None or after is None:
            if not closed:
                out.append((before or after)[1])
                continue
            before = before or given[-1]
            after = after or given[0]
        if before[0] == after[0]:
            out.append(before[1])
            continue
        sa, sb, si = s[before[0]], s[after[0]], s[i]
        if sb <= sa:                                  # über den Rundenschluss
            sb += length
            if si < sa:
                si += length
        t = (si - sa) / max(sb - sa, 1e-9)
        out.append(before[1] + (after[1] - before[1]) * t)
    return out


def point_turns(points, closed, reach=8.0):
    """(Richtung, Radius) je Kontrollpunkt aus dem Kreis durch die Nachbarn im Abstand ≥ reach."""
    pos = [point_xyz(p) for p in points]
    n = len(pos)
    s, length = _stations(pos, closed)
    result = []
    for i in range(n):
        def walk(step):
            j, travelled = i, 0.0
            while True:
                k = j + step
                if closed:
                    k %= n
                elif not 0 <= k < n:
                    return j
                if k == i:
                    return j
                travelled += math.dist((pos[j][0], pos[j][2]), (pos[k][0], pos[k][2]))
                j = k
                if travelled >= reach:
                    return j
        a, b = walk(-1), walk(1)
        if a == i or b == i or a == b:
            result.append((0, None))
            continue
        # Draufsicht wie die Segmentgeometrie: vorwärts = (cos φ, −sin φ) → (x, −z) ist rechtshändig, + = links
        (ax, az), (px, pz), (bx, bz) = ((pos[k][0], -pos[k][2]) for k in (a, i, b))
        ux, uz, vx, vz = px - ax, pz - az, bx - px, bz - pz
        cross = ux * vz - uz * vx
        chord = math.dist((ax, az), (bx, bz)) * math.hypot(ux, uz) * math.hypot(vx, vz)
        if abs(cross) < 1e-9 or chord < 1e-9:
            result.append((0, None))
            continue
        radius = chord / (2 * abs(cross))
        result.append((1 if cross > 0 else -1, radius))
    return result


def segment_turns(segments):
    """(Richtung, Radius) je Segment; Klothoide: engerer Endradius, Gerade: (0, None)."""
    result = []
    for seg in segments:
        kind, direction = seg.get("typ"), {"links": 1, "rechts": -1}.get(seg.get("richtung"), 0)
        try:
            if kind == "bogen":
                result.append((direction, float(seg["radius"])))
            elif kind == "klothoide":
                radii = [float(seg[k]) for k in ("radius_anfang", "radius_ende") if seg.get(k)]
                result.append((direction, min(radii)) if radii else (0, None))
            else:
                result.append((0, None))
        except (TypeError, ValueError, KeyError):
            result.append((0, None))
    return result


def turns(raw, closed):
    ml = raw.get("mittellinie", {})
    if ml.get("art") == "segmente":
        return segment_turns(ml.get("segmente", []))
    if ml.get("art") == "kontrollpunkte":
        return point_turns(ml.get("punkte", []), closed)
    return []


def current_values(raw, closed):
    """Wirksame Querneigung je Objekt (Segment bzw. Kontrollpunkt), ohne Profilabschnitte."""
    ml, base = raw.get("mittellinie", {}), base_value(raw)
    if ml.get("art") == "segmente":
        return [float(s[KEY]) if s.get(KEY) is not None else base for s in ml.get("segmente", [])]
    if ml.get("art") == "kontrollpunkte":
        return point_banks(ml.get("punkte", []), closed, base)
    return []


def set_values(raw, values, closed):
    """Schreibt {Objektindex: Grad}. Segmente: Standardwert = Feld entfernen. Kontrollpunkte: Nachbarn
    behalten ihren bisherigen Wert; sind am Ende alle Werte gleich dem Standard, verschwinden sie."""
    ml, base = raw["mittellinie"], base_value(raw)
    if ml.get("art") == "segmente":
        for i, value in values.items():
            seg = ml["segmente"][i]
            if abs(value - base) < 1e-9:
                seg.pop(KEY, None)
            else:
                seg[KEY] = round(value, 2)
        return
    points = ml["punkte"]
    n = len(points)
    before = point_banks(points, closed, base)

    def as_dict(i):
        if not isinstance(points[i], dict):
            x, y, z = point_xyz(points[i])
            points[i] = {"x": x, "y": y, "z": z}
        return points[i]

    for i in values:
        for j in (i - 1, i + 1):
            if closed:
                j %= n
            if 0 <= j < n and j not in values and \
                    not (isinstance(points[j], dict) and points[j].get(KEY) is not None):
                as_dict(j)[KEY] = round(before[j], 2)
    for i, value in values.items():
        as_dict(i)[KEY] = round(value, 2)
    if all(abs(float(p[KEY]) - base) < 1e-9 for p in points if isinstance(p, dict) and p.get(KEY) is not None):
        for p in points:
            if isinstance(p, dict):
                p.pop(KEY, None)


def auto_values(raw, closed, maximum):
    """Alle Kurven: Kurveninnenseite tiefer, Stärke nach Radius (siehe ``auto_magnitude``)."""
    return {i: signed(auto_magnitude(radius, maximum), "innen", turn)
            for i, (turn, radius) in enumerate(turns(raw, closed))}


def flatten(raw):
    """Ganze Strecke flach: Werte an Objekten, Abschnitten und Fahrbahn entfernen."""
    ml = raw.get("mittellinie", {})
    for item in ml.get("segmente", []) + ml.get("punkte", []):
        if isinstance(item, dict):
            item.pop(KEY, None)
    fahrbahn = raw.get("fahrbahn") or {}
    fahrbahn.pop(KEY, None)
    for section in fahrbahn.get("abschnitte", []) or []:
        section.pop(KEY, None)


# ------------------------------------------------------------------ Bedienfeld
class BankPanel(QFrame):
    """Querneigung der Auswahl: Stärke, Seite, Schnellwerte; Automatik und „Alles flach“ für die ganze Strecke."""

    apply = Signal(float, str)          # Stärke in °, Seite (SIDES)
    auto_requested = Signal(float)      # Höchstwert in °
    flat_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("bankpanel")
        self.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        title = QLabel(tr("BANKING"))
        title.setObjectName("heading")
        layout.addWidget(title)
        self.state = QLabel()
        self.state.setWordWrap(True)
        layout.addWidget(self.state)
        row = QHBoxLayout()
        self.amount = QDoubleSpinBox()
        self.amount.setRange(0.0, MAX_GRAD)
        self.amount.setSingleStep(0.5)
        self.amount.setDecimals(1)
        self.amount.setSuffix(" °")
        self.amount.setKeyboardTracking(False)       # erst bei Enter/Pfeil übernehmen
        self.amount.setToolTip(tr("Amount of banking. Arrows/mouse wheel: 0.5° steps, Enter applies."))
        self.side = QComboBox()
        for key, text in SIDES.items():
            self.side.addItem(tr(text), key)
        self.side.setToolTip(tr("Which side is lower. “Corner inside” adapts to every corner (straights stay flat)."))
        row.addWidget(self.amount)
        row.addWidget(self.side, 1)
        # Stärke, Seite, Schnellwerte und Warnung nur bei Auswahl (ohne Auswahl bleibt das Feld kompakt)
        self.detail = QWidget()
        detail = QVBoxLayout(self.detail)
        detail.setContentsMargins(0, 0, 0, 0)
        detail.setSpacing(6)
        detail.addLayout(row)
        layout.addWidget(self.detail)
        presets = QGridLayout()
        presets.setSpacing(4)
        self.preset_buttons = []
        for col, (text, value, tip) in enumerate(((tr("Flat"), 0.0, tr("No banking")),
                                                  ("2°", 2.0, tr("Slight banking")),
                                                  ("4°", 4.0, tr("Strong banking")),
                                                  (tr("FIA max"), FIA_MAX, tr("Maximum of the FIA rules (5.7°)")))):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(lambda checked=False, value=value: self._preset(value))
            presets.addWidget(button, 0, col)
            self.preset_buttons.append(button)
        detail.addLayout(presets)
        self.warning = QLabel()
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: #D6A94A;")
        detail.addWidget(self.warning)
        whole = QHBoxLayout()
        self.auto_button = QPushButton(tr("All corners automatically"))
        self.auto_button.setToolTip(tr("Whole track: bank every corner towards the inside – tight corners (R ≤ 80 m) "
                                       "with the set amount, wider ones less, flat from R 500 m. Ctrl+Z undoes."))
        self.flat_button = QPushButton(tr("All flat"))
        self.flat_button.setToolTip(tr("Whole track without banking (also road default and sections). Ctrl+Z undoes."))
        self.auto_button.clicked.connect(lambda: self.auto_requested.emit(self.amount.value() or 4.0))
        self.flat_button.clicked.connect(self.flat_requested.emit)
        whole.addWidget(self.auto_button)
        whole.addWidget(self.flat_button)
        layout.addLayout(whole)
        hint = QLabel(tr("The generator smooths transitions (road → transition, default 40 m)."))
        hint.setWordWrap(True)
        hint.setEnabled(False)
        detail.addWidget(hint)
        self.amount.valueChanged.connect(self._changed)
        self.side.activated.connect(self._changed)
        self._loading = False

    def _preset(self, value):
        if abs(self.amount.value() - value) < 1e-9:
            self._changed()                           # gleicher Wert: trotzdem auf die Auswahl anwenden
        else:
            self.amount.setValue(value)

    def _changed(self, *args):
        self._update_warning()
        if not self._loading:
            self.apply.emit(self.amount.value(), self.side.currentData())

    def _update_warning(self):
        value = self.amount.value()
        self.warning.setVisible(value > FIA_MAX + 1e-9)
        self.warning.setText(tr("Above the FIA maximum of 5.7° – the check reports this (ovals/steep banking are "
                                "allowed, but not compliant).") if value > FIA_MAX + 1e-9 else "")

    def show_selection(self, values, turns_, count, editable):
        """Zustand der Auswahl anzeigen, ohne etwas zu schreiben."""
        self._loading = True
        try:
            for widget in (self.amount, self.side, *self.preset_buttons):
                widget.setEnabled(editable and count > 0)
            for widget in (self.auto_button, self.flat_button):
                widget.setEnabled(editable)
            self.detail.setVisible(count > 0)
            if not count:
                self.state.setText(tr("Select a corner or points to bank them. Or: “All corners automatically”."))
                return
            texts = {describe(v, t) for v, t in zip(values, turns_)}
            if len(texts) == 1:
                self.state.setText(tr("Selection: {zustand}", zustand=texts.pop()) if count > 1 else
                                   tr("Selected: {zustand}", zustand=texts.pop()))
            else:
                self.state.setText(tr("Selection: mixed ({anzahl} objects)", anzahl=count))
            first = max(values, key=abs) if values else 0.0
            self.amount.setValue(abs(first))
            turn = next((t for v, t in zip(values, turns_) if abs(v) >= 0.05), 0)
            if abs(first) >= 0.05:
                key = ("innen" if first * turn < 0 else "aussen") if turn else ("links" if first > 0 else "rechts")
                if self.side.currentData() in ("links", "rechts") and turn:
                    key = "links" if first > 0 else "rechts"      # gewählte Seitenart beibehalten
                self.side.setCurrentIndex(self.side.findData(key))
            self._update_warning()
        finally:
            self._loading = False
