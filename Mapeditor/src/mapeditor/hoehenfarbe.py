"""Höhe bzw. Steigung als Farbe (geteilte Ansicht): gleiche Farbskala für Karte und Höhenprofil.

Zwei Modi:
- ``hoehe``: tiefster bis höchster Punkt der Runde. Die Skala gilt immer für die ganze Runde, damit dieselbe Stelle in
  Karte und Profil dieselbe Farbe hat, auch wenn das Profil nur einen Ausschnitt zeigt.
- ``steigung``: Betrag der Steigung von 0 % (eben) bis ``STEIGUNG_MAX`` % und mehr.

Farbskala ohne Grün (blau → violett → magenta → orange → gelb): hebt sich vom grünen Untergrund der Karte ab und bleibt
bei Rot-Grün-Sehschwäche unterscheidbar (Helligkeit steigt von blau nach gelb).
"""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient

#: Farbstufen (benachbarte Stücke gleicher Stufe werden zusammen gezeichnet)
STUFEN = 32
MODI = ("hoehe", "steigung")
#: Ende der Steigungsskala in Prozent (steiler = gleiche Farbe)
STEIGUNG_MAX = 12.0
#: Stützpunkte der Farbskala (Anteil, RGB)
STUETZEN = ((0.0, (60, 120, 255)), (0.25, (140, 80, 235)), (0.5, (220, 70, 170)), (0.75, (255, 135, 45)),
            (1.0, (255, 225, 60)))


def steigungen(data):
    """Steigung in Prozent je Station: aus der Vorschau oder aus Höhe und Station berechnet (Eingabezeichnung)."""
    if data.get("gradient"):
        return list(data["gradient"])
    pos, s = data.get("pos") or [], data.get("s") or []
    n = len(pos)
    if n < 3 or len(s) != n:
        return [0.0] * n
    out = []
    for i in range(n):
        a, b = max(0, i - 1), min(n - 1, i + 1)
        ds = s[b] - s[a]
        out.append((pos[b][1] - pos[a][1]) / ds * 100 if ds > 1e-9 else 0.0)
    return out


def werte(data, modus="hoehe"):
    """Farbwert je Station: Höhe in m oder Betrag der Steigung in %."""
    if not data or not data.get("pos"):
        return []
    if modus == "steigung":
        return [abs(g) for g in steigungen(data)]
    return [p[1] for p in data["pos"]]


def bereich(data, modus="hoehe"):
    """(unten, oben) der Farbskala; None ohne Daten."""
    if not data or not data.get("pos"):
        return None
    if modus == "steigung":
        return 0.0, STEIGUNG_MAX
    ys = [p[1] for p in data["pos"]]
    lo, hi = min(ys), max(ys)
    return (lo, hi) if hi - lo >= 0.05 else (lo - 0.5, hi + 0.5)


def anteil(y, lo, hi):
    return max(0.0, min(1.0, (y - lo) / max(hi - lo, 1e-9)))


def stufe(y, lo, hi):
    return min(STUFEN - 1, int(anteil(y, lo, hi) * STUFEN))


def farbe_anteil(f):
    f = max(0.0, min(1.0, f))
    for (a, ca), (b, cb) in zip(STUETZEN, STUETZEN[1:]):
        if f <= b:
            t = (f - a) / (b - a)
            return QColor(*(round(x + (y - x) * t) for x, y in zip(ca, cb)))
    return QColor(*STUETZEN[-1][1])


def stufenfarbe(k):
    return farbe_anteil((k + 0.5) / STUFEN)


def farbe(y, lo, hi):
    return stufenfarbe(stufe(y, lo, hi))


def beschriftung(modus, lo, hi):
    """(oben, unten) für die Legende."""
    if modus == "steigung":
        return "≥ %.0f %%" % hi, "0 %"
    return "%+.1f m" % hi, "%+.1f m" % lo


def legende(painter, x, y, lo, hi, titel, hell=False, hoehe=150, modus="hoehe"):
    """Farbleiste mit oberem und unterem Wert, oben links an (x, y)."""
    oben, unten = beschriftung(modus, lo, hi)
    painter.save()
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(255, 255, 255, 225) if hell else QColor(16, 24, 32, 215))
    painter.drawRoundedRect(QRectF(x - 10, y - 26, 104, hoehe + 46), 5, 5)
    verlauf = QLinearGradient(x, y + hoehe, x, y)
    for k in range(11):
        verlauf.setColorAt(k / 10, farbe_anteil(k / 10))
    painter.setBrush(verlauf)
    painter.drawRect(QRectF(x, y, 14, hoehe))
    painter.setPen(QColor("#162331" if hell else "#F1F5F9"))
    painter.drawText(QPointF(x, y - 9), titel)
    painter.drawText(QPointF(x + 22, y + 10), oben)
    painter.drawText(QPointF(x + 22, y + hoehe), unten)
    painter.restore()
