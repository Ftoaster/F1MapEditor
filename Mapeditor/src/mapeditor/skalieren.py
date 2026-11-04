"""Strecke skalieren: um einen Faktor oder auf eine Ziellänge (Menü „Bearbeiten → Strecke skalieren …“).

Festlegungen (Nutzer 3.11.2026): Skaliert wird in der Draufsicht um die Mitte der Strecke (Mitte des umschließenden
Rechtecks der Eingabelinie). Höhen in Metern bleiben, eine längere Strecke wird also flacher. Fahrbahn- und Boxenbreiten
bleiben. Streckenmeter (Abschnitte, Ziellinie, DRS- und Straight-Mode-Zonen, Boxenein- und -ausfahrt) wachsen mit.
Umgebungsobjekte und Hügel/Berge behalten ihre Lage zur Strecke (Positionen skaliert), ihre Größe bleibt.
"""

from __future__ import annotations

import copy

from PySide6.QtWidgets import (QButtonGroup, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel, QRadioButton,
                               QVBoxLayout)

from .i18n import tr
from .project import point_xyz, set_point

FAKTOR_MIN, FAKTOR_MAX = 0.1, 10.0
#: Segmentwerte in Metern (Länge, Radien) – der Winkel eines Bogens bleibt
SEGMENT_METER = ("laenge", "radius", "radius_anfang", "radius_ende")
#: Klothoiden-Regeln in Metern (Standardwerte wie ``input_geometry.TRANSITION_DEFAULTS``)
UEBERGANG_METER = {"laenge_m": None, "min_laenge_m": 20.0, "max_laenge_m": 150.0}
#: Streckenmeter: (Gruppe, Liste oder None, Felder)
STATIONEN = (("fahrbahn", "abschnitte", ("von_s", "bis_s")),
             ("seitenraum", "abschnitte", ("von_s", "bis_s")),
             ("drs", "zonen", ("erkennung_m", "aktivierung_m", "ende_m")),
             ("x_mode", "zonen", ("von_m", "bis_m", "partial_versatz_m")),
             ("start_ziel", None, ("s",)),
             ("boxengasse", None, ("einfahrt_vor_start_m", "ausfahrt_nach_start_m")))


def mitte(positionen):
    """Mitte des umschließenden Rechtecks (x, z) der Eingabelinie."""
    xs, zs = [p[0] for p in positionen], [p[2] for p in positionen]
    return (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2


def faktor_pruefen(faktor):
    faktor = float(faktor)
    if not FAKTOR_MIN <= faktor <= FAKTOR_MAX:
        raise ValueError(tr("Factor must be between {a} and {b}.", a=FAKTOR_MIN, b=FAKTOR_MAX))
    return faktor


def _punkt(x, z, faktor, cx, cz):
    return round(cx + (x - cx) * faktor, 3), round(cz + (z - cz) * faktor, 3)


def _segmente(quelle, faktor, cx, cz):
    """Segmentkette samt Start: Längen und Radien mal Faktor, Steigung in % geteilt (Höhen bleiben)."""
    start = quelle.get("start")
    if isinstance(start, dict) and "x" in start and "z" in start:
        start["x"], start["z"] = _punkt(float(start["x"]), float(start["z"]), faktor, cx, cz)
    for seg in quelle.get("segmente") or []:
        for k in SEGMENT_METER:
            if seg.get(k) is not None and not (k == "laenge" and seg.get("typ") == "bogen" and seg.get("winkel") is not None):
                seg[k] = round(float(seg[k]) * faktor, 3)
        if seg.get("steigung_prozent") is not None:
            seg["steigung_prozent"] = round(float(seg["steigung_prozent"]) / faktor, 3)
        ueb = seg.get("uebergang")
        if isinstance(ueb, dict):
            for k, v in ueb.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    ueb[k] = round(float(v) * faktor, 3)
        elif isinstance(ueb, (int, float)) and not isinstance(ueb, bool):
            seg["uebergang"] = round(float(ueb) * faktor, 3)


def anwenden(raw, umgebung, positionen, faktor):
    """Skaliert Rohdefinition und Umgebung an Ort und Stelle. ``positionen``: Eingabelinie (x, y, z) für die Mitte."""
    faktor = faktor_pruefen(faktor)
    cx, cz = mitte(positionen)
    ml = raw["mittellinie"]
    if ml.get("art") == "segmente":
        _segmente(ml, faktor, cx, cz)
        regeln = ml.get("uebergaenge")
        if isinstance(regeln, dict) and regeln.get("aktiv") is True:
            for k, standard in UEBERGANG_METER.items():
                wert = regeln.get(k, standard)
                if wert is not None:
                    regeln[k] = round(float(wert) * faktor, 3)
    else:
        punkte = ml.get("punkte") or []
        for i, p in enumerate(punkte):
            x, _, z = point_xyz(p)
            nx, nz = _punkt(x, z, faktor, cx, cz)
            punkte[i] = set_point(p, x=nx, z=nz)
        if isinstance(ml.get("editor_segmentquelle"), dict):    # Herkunft einer umgewandelten Segmentstrecke
            _segmente(ml["editor_segmentquelle"], faktor, cx, cz)
    for gruppe, liste, felder in STATIONEN:
        teil = raw.get(gruppe)
        if not isinstance(teil, dict):
            continue
        eintraege = (teil.get(liste) or []) if liste else [teil]
        for e in eintraege:
            for k in felder:
                if isinstance(e.get(k), (int, float)) and not isinstance(e.get(k), bool):
                    e[k] = round(float(e[k]) * faktor, 1)
    if umgebung:
        for o in umgebung.get("objekte") or []:
            if "x" in o and "z" in o:
                o["x"], o["z"] = (round(v, 2) for v in _punkt(float(o["x"]), float(o["z"]), faktor, cx, cz))
        for formen in (umgebung.get("gelaende") or {}).values():
            for f in formen if isinstance(formen, list) else []:
                if "x" in f and "z" in f:
                    f["x"], f["z"] = (round(v, 1) for v in _punkt(float(f["x"]), float(f["z"]), faktor, cx, cz))
    return cx, cz


class SkalierenDialog(QDialog):
    """Faktor oder Ziellänge wählen; zeigt die aktuelle und die neue Länge."""

    def __init__(self, laenge, parent=None):
        super().__init__(parent)
        self.laenge = float(laenge)
        self.setWindowTitle(tr("Scale track"))
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("The whole track grows or shrinks around its centre: points, segment lengths and "
                                   "radii, sections and zones, and the position of the environment. Heights, widths "
                                   "and the size of environment objects stay. Ctrl+Z undoes it.")))
        layout.itemAt(0).widget().setWordWrap(True)
        form = QFormLayout()
        form.addRow(tr("Current length"), QLabel(f"{self.laenge:,.0f} m".replace(",", ".")))
        self.nach_faktor = QRadioButton(tr("By factor"))
        self.nach_laenge = QRadioButton(tr("To length"))
        gruppe = QButtonGroup(self)
        gruppe.addButton(self.nach_faktor)
        gruppe.addButton(self.nach_laenge)
        self.nach_faktor.setChecked(True)
        self.faktor = QDoubleSpinBox()
        self.faktor.setRange(FAKTOR_MIN, FAKTOR_MAX)
        self.faktor.setDecimals(3)
        self.faktor.setSingleStep(0.05)
        self.faktor.setValue(1.1)
        self.faktor.setAccessibleName(tr("Scale factor"))
        self.ziel = QDoubleSpinBox()
        self.ziel.setRange(round(self.laenge * FAKTOR_MIN), round(self.laenge * FAKTOR_MAX))
        self.ziel.setDecimals(0)
        self.ziel.setSingleStep(100)
        self.ziel.setSuffix(" m")
        self.ziel.setValue(round(self.laenge * 1.1))
        self.ziel.setAccessibleName(tr("Target length in metres"))
        form.addRow(self.nach_faktor, self.faktor)
        form.addRow(self.nach_laenge, self.ziel)
        self.ergebnis = QLabel()
        form.addRow(tr("Result"), self.ergebnis)
        layout.addLayout(form)
        knoepfe = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        knoepfe.accepted.connect(self.accept)
        knoepfe.rejected.connect(self.reject)
        layout.addWidget(knoepfe)
        self.faktor.valueChanged.connect(lambda _: (self.nach_faktor.setChecked(True), self._zeigen()))
        self.ziel.valueChanged.connect(lambda _: (self.nach_laenge.setChecked(True), self._zeigen()))
        self.nach_faktor.toggled.connect(lambda _: self._zeigen())
        self._zeigen()

    def wert(self):
        """Gewählter Faktor (aus Faktor oder Ziellänge)."""
        if self.nach_laenge.isChecked():
            return self.ziel.value() / self.laenge
        return self.faktor.value()

    def _zeigen(self):
        f = self.wert()
        self.ergebnis.setText(tr("Factor {faktor} · about {laenge} m", faktor=f"{f:.3f}",
                                 laenge=f"{self.laenge * f:,.0f}".replace(",", ".")))
