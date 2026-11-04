"""Querschnitt bearbeiten (27.10.2026): Fahrbahnbreite, Randstein, Grünstreifen, Auslauf, Barriere und Randwinkel je
Bereich.

Der Generator legt die Bänder neben der Fahrbahn automatisch an (Randsteine in Kurven, Kiesbett außen nach engen
Kurven, Barriere im Standardabstand). Abschnitte überschreiben das zwischen zwei Stationen auf einer oder beiden
Seiten: ``seitenraum.abschnitte`` (Werkzeugkasten ``strecke/querprofil.py``) und für die Fahrbahn
``fahrbahn.abschnitte``. Beide zählen in Metern ab Eingabeursprung (``von_s``/``bis_s``); im Editor wird in
Metern ab Ziel gearbeitet und mit ``s_start`` der Vorschau umgerechnet.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from .i18n import N_, tr
from .ux import zahl

SIDES = {"beide": N_("both sides"), "links": N_("left"), "rechts": N_("right")}
RUNOFFS = {None: N_("Automation"), "gras": N_("Grass"), "kies": N_("Gravel"), "asphalt": N_("Asphalt")}
#: Felder eines Seitenraum-Abschnitts mit Zahlenwert (None = Automatik)
NUMBER_FIELDS = ("randstein_breite", "gruenstreifen_breite", "barriere_abstand")
AUTO = -1.0              # Wert der Eingabefelder für „Automatik“ (Sondertext)
#: Randwinkel: waagerecht (kein Eintrag), wie die Fahrbahn an dieser Stelle, eigener Winkel
EDGES = {None: N_("Level"), "strecke": N_("Same as track"), "eigen": N_("Custom angle")}
EDGE_MAX = 30.0          # Grad, wie strecke/definition.py RAND_NEIGUNG_MAX


# ------------------------------------------------------------------ Logik (ohne Qt-Widgets)
def to_input_s(station, s_start, length):
    """Station ab Ziel → Meter ab Eingabeursprung (wie ``von_s``/``bis_s`` der Definition)."""
    return round((float(station) + float(s_start)) % float(length), 1)


def to_station(input_s, s_start, length):
    return (float(input_s) - float(s_start)) % float(length)


def side_sections(raw):
    sections = (raw.get("seitenraum") or {}).get("abschnitte")
    return sections if isinstance(sections, list) else []


def _inside(s, von, bis):
    return von <= s <= bis if von <= bis else s >= von or s <= bis


def sections_at(raw, input_s, side):
    """Seitenraum-Abschnitte, die bei ``input_s`` auf ``side`` wirken (Index, Eintrag); der letzte gewinnt."""
    return [(i, ab) for i, ab in enumerate(side_sections(raw))
            if ab.get("seite", "beide") in ("beide", side) and _inside(input_s, float(ab["von_s"]), float(ab["bis_s"]))]


def effective(raw, input_s, side):
    """Eigene Werte bei ``input_s`` auf ``side`` (fehlende Felder = Automatik)."""
    values = {}
    for _, ab in sections_at(raw, input_s, side):
        values.update({k: v for k, v in ab.items() if k in NUMBER_FIELDS + ("auslauf", "rand_neigung") and v is not None})
    return values


def _same_range(ab, von, bis):
    return abs(float(ab["von_s"]) - von) < 0.05 and abs(float(ab["bis_s"]) - bis) < 0.05


def contiguous_span(indices, count, closed):
    """(erster, letzter) Index eines zusammenhängenden Laufs in Fahrtrichtung; None bei Lücken oder weniger als zwei
    Punkten. In einer geschlossenen Runde darf der Lauf über den letzten Punkt hinweg zum ersten führen."""
    chosen = sorted(set(indices))
    if len(chosen) < 2:
        return None
    gaps = [(a, b) for a, b in zip(chosen, chosen[1:]) if b - a > 1]
    if not gaps:
        return chosen[0], chosen[-1]
    if closed and len(gaps) == 1 and chosen[0] == 0 and chosen[-1] == count - 1:
        return gaps[0][1], gaps[0][0]
    return None


def apply_section(raw, von, bis, side, fields, road_half=None):
    """Abschnitt von ``von`` bis ``bis`` (Meter ab Eingabeursprung) setzen.

    ``fields``: Randstein/Grünstreifen/Barriere in m, ``auslauf`` gras|kies|asphalt, ``rand_neigung`` "strecke" oder
    Grad; None = Automatik.
    Ein bestehender Abschnitt mit gleichem Bereich und gleicher Seite wird ersetzt. ``road_half``: halbe
    Fahrbahnbreite auf der Seite (bei „beide“ auf beiden) als ``fahrbahn.abschnitte``-Eintrag; None = unverändert."""
    entry = {"von_s": von, "bis_s": bis, "seite": side}
    entry.update({k: v for k, v in fields.items() if v is not None})
    sections = [ab for ab in side_sections(raw)
                if not (_same_range(ab, von, bis) and ab.get("seite", "beide") == side)]
    if len(entry) > 3:
        sections.append(entry)
    if sections:
        raw.setdefault("seitenraum", {})["abschnitte"] = sections
    elif isinstance(raw.get("seitenraum"), dict):
        raw["seitenraum"].pop("abschnitte", None)
        if not raw["seitenraum"]:
            raw.pop("seitenraum")
    if road_half is not None:
        road = raw.setdefault("fahrbahn", {})
        keys = ("breite_links", "breite_rechts") if side == "beide" else ("breite_" + side,)
        road_sections = [ab for ab in road.get("abschnitte") or []
                         if not (_same_range(ab, von, bis) and set(ab) - {"von_s", "bis_s"} == set(keys))]
        road_sections.append(dict({"von_s": von, "bis_s": bis}, **{k: float(road_half) for k in keys}))
        road["abschnitte"] = road_sections


def clear_range(raw, von, bis, side):
    """Eigene Abschnitte (Seitenraum und Fahrbahnbreite) entfernen, die den Bereich auf ``side`` berühren."""
    def touches(ab):
        a, b = float(ab["von_s"]), float(ab["bis_s"])
        return _inside(a, von, bis) or _inside(b, von, bis) or _inside(von, a, b)
    before = len(side_sections(raw))
    kept = [ab for ab in side_sections(raw)
            if not (touches(ab) and (side == "beide" or ab.get("seite", "beide") in ("beide", side)))]
    removed = before - len(kept)
    if isinstance(raw.get("seitenraum"), dict):
        if kept:
            raw["seitenraum"]["abschnitte"] = kept
        else:
            raw.pop("seitenraum")
    road = raw.get("fahrbahn") or {}
    if road.get("abschnitte"):
        keys = {"breite_links", "breite_rechts", "breite"} if side == "beide" else {"breite_" + side, "breite"}
        kept_road = [ab for ab in road["abschnitte"] if not (touches(ab) and set(ab) & keys)]
        removed += len(road["abschnitte"]) - len(kept_road)
        road["abschnitte"] = kept_road
    return removed


# ------------------------------------------------------------------ Bedienfeld
def _number_box(suffix, auto_text, maximum, tip):
    box = QDoubleSpinBox()
    box.setRange(AUTO, maximum)
    box.setDecimals(1)
    box.setSingleStep(0.5)
    box.setSuffix(suffix)
    box.setSpecialValueText(auto_text)          # Wert AUTO zeigt „Automatik“
    box.setValue(AUTO)
    box.setToolTip(tip)
    return box


class CrossPanel(QFrame):
    """Bereich (ab Ziel), Seite und Werte; „Übernehmen“ setzt einen Abschnitt, „Automatik“ entfernt eigene."""

    apply_requested = Signal()
    clear_requested = Signal()
    range_requested = Signal(str)       # auswahl / cursor

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 2)
        layout.setSpacing(3)
        form = QFormLayout()
        form.setVerticalSpacing(3)
        span = QHBoxLayout()
        self.von, self.bis = QDoubleSpinBox(), QDoubleSpinBox()
        for box in (self.von, self.bis):
            box.setRange(0.0, 100000.0)
            box.setDecimals(0)
            box.setSingleStep(10)
            box.setSuffix(" m")
            box.setToolTip(tr(("Metres from the finish in driving direction; across the finish line allowed (from > "
                               "to).")))
            span.addWidget(box, 1)
        form.addRow(tr("From / to"), span)
        quick = QHBoxLayout()
        self.selection_button = QPushButton(tr("Selection A→B"))
        self.selection_button.setToolTip(tr("Range between the first and last selected point/segment"))
        self.selection_button.clicked.connect(lambda: self.range_requested.emit("auswahl"))
        self.cursor_button = QPushButton(tr("Cursor ±50 m"))
        self.cursor_button.clicked.connect(lambda: self.range_requested.emit("cursor"))
        self.lap_button = QPushButton(tr("Whole lap"))
        self.lap_button.setToolTip(tr("Range over the whole track"))
        self.lap_button.clicked.connect(lambda: self.range_requested.emit("runde"))
        quick.addWidget(self.selection_button)
        quick.addWidget(self.cursor_button)
        quick.addWidget(self.lap_button)
        form.addRow("", quick)
        self.side = QComboBox()
        for key, text in SIDES.items():
            self.side.addItem(tr(text), key)
        self.side.setToolTip(tr("Left/right in driving direction"))
        # zweispaltig: Alle Felder und „Übernehmen“ passen ohne Scrollen in den unteren Bereich
        grid = QGridLayout()
        grid.setVerticalSpacing(3)
        grid.setHorizontalSpacing(8)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        def field(row, column, text, widget):
            grid.addWidget(QLabel(text), row, column * 2)
            grid.addWidget(widget, row, column * 2 + 1)
        field(0, 0, tr("Side"), self.side)
        self.road = _number_box(" m", tr("unchanged"), 30.0, tr("Half road width on this side (asphalt)"))
        field(0, 1, tr("Road (half)"), self.road)
        self.kerb = _number_box(" m", tr("Automation"), 10.0, tr("Kerb width; 0 = no kerb"))
        field(1, 0, tr("Kerb"), self.kerb)
        self.grass = _number_box(" m", tr("Automation"), 30.0, tr("Grass strip between kerb and run-off"))
        field(1, 1, tr("Grass strip"), self.grass)
        self.runoff = QComboBox()
        for key, text in RUNOFFS.items():
            self.runoff.addItem(tr(text), key)
        self.runoff.setToolTip(tr("Run-off area up to the barrier: grass, gravel or asphalt"))
        field(2, 0, tr("Run-off"), self.runoff)
        self.barrier = _number_box(" m", tr("Automation"), 200.0, tr("Distance road edge → barrier (at most as far as "
                                                                    "there is room to the next part of the track)"))
        field(2, 1, tr("Barrier"), self.barrier)
        edge = QHBoxLayout()
        self.edge = QComboBox()
        for key, text in EDGES.items():
            self.edge.addItem(tr(text), key)
        self.edge.setToolTip(tr("Angle beside the road: “Same as track” continues the banking at every point over the "
                                "first 12 m, then it stays level."))
        self.edge_angle = QDoubleSpinBox()
        self.edge_angle.setRange(-EDGE_MAX, EDGE_MAX)
        self.edge_angle.setDecimals(1)
        self.edge_angle.setSuffix(" °")
        self.edge_angle.setToolTip(tr("Positive: the edge rises outwards, negative: it falls."))
        self.edge.currentIndexChanged.connect(lambda *_: self.edge_angle.setVisible(self.edge.currentData() == "eigen"))
        self.edge_angle.setVisible(False)
        edge.addWidget(self.edge, 1)
        edge.addWidget(self.edge_angle)
        grid.addWidget(QLabel(tr("Edge angle")), 3, 0)
        grid.addLayout(edge, 3, 1, 1, 3)
        layout.addLayout(form)
        layout.addLayout(grid)
        buttons = QHBoxLayout()
        self.apply_button = QPushButton(tr("Apply"))
        self.apply_button.setToolTip(tr("Set a section for the range · Ctrl+Z undoes."))
        self.apply_button.clicked.connect(self.apply_requested.emit)
        self.clear_button = QPushButton(tr("Automation"))
        self.clear_button.setToolTip(tr("Remove your own sections in the range (roadside and road width)"))
        self.clear_button.clicked.connect(self.clear_requested.emit)
        buttons.addWidget(self.apply_button)
        buttons.addWidget(self.clear_button)
        layout.addLayout(buttons)
        self.info = QLabel()
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        layout.addStretch(1)

    def set_range(self, von, bis):
        self.von.setValue(round(von))
        self.bis.setValue(round(bis))

    def load(self, values):
        """Eingabefelder auf eigene Werte (``effective``) setzen; fehlende = Automatik."""
        for box, key in ((self.kerb, "randstein_breite"), (self.grass, "gruenstreifen_breite"),
                         (self.barrier, "barriere_abstand")):
            box.setValue(float(values[key]) if values.get(key) is not None else AUTO)
        self.runoff.setCurrentIndex(max(0, self.runoff.findData(values.get("auslauf"))))
        rand = values.get("rand_neigung")
        self.edge.setCurrentIndex(max(0, self.edge.findData(rand if rand in (None, "strecke") else "eigen")))
        self.edge_angle.setValue(float(rand) if rand not in (None, "strecke") else 0.0)
        self.road.setValue(AUTO)

    def fields(self):
        def number(box):
            return None if box.value() <= AUTO + 1e-9 else round(box.value(), 2)
        rand = self.edge.currentData()
        return {"randstein_breite": number(self.kerb), "gruenstreifen_breite": number(self.grass),
                "auslauf": self.runoff.currentData(), "barriere_abstand": number(self.barrier),
                "rand_neigung": round(self.edge_angle.value(), 1) if rand == "eigen" else rand}

    def road_half(self):
        return None if self.road.value() <= AUTO + 1e-9 else round(self.road.value(), 2)

    def set_enabled(self, enabled, reason=""):
        for widget in (self.von, self.bis, self.selection_button, self.cursor_button, self.lap_button, self.side,
                       self.road, self.kerb, self.grass, self.runoff, self.barrier, self.edge, self.edge_angle,
                       self.apply_button, self.clear_button):
            widget.setEnabled(enabled)
        if reason:
            self.info.setText(reason)


def describe(raw, input_s, side, s_start, length):
    """Kurztext: welche eigenen Abschnitte am Cursor wirken."""
    found = sections_at(raw, input_s, side)
    if not found:
        return tr("At the cursor ({side}): automatic.", side=tr(SIDES.get(side, side)))
    parts = []
    for i, ab in found:
        von, bis = (zahl(to_station(ab[k], s_start, length), 0, "m") for k in ("von_s", "bis_s"))
        parts.append(tr("Section {nr} ({seite}, {von}–{bis})", nr=i + 1, seite=tr(SIDES.get(ab.get("seite", "beide"))),
                        von=von, bis=bis))
    return tr("Active at the cursor: ") + "; ".join(parts)
