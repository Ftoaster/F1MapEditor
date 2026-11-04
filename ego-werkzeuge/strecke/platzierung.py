"""Platzierung der generierten Teststrecke im Portimão-Träger (verbindlich für AP11).

Die Teststrecke aus dem Generator (`strecke/beispiele/teststrecke.json`, Meter, y oben, Draufsicht
x nach rechts, z nach unten) wird **nur verschoben, nicht gedreht**, so dass die Mitte ihrer
Ausdehnung im Weltursprung liegt:

    Generator:  x -302 … 1484, z -36 … 982   (Netz inkl. Auslaufzonen)
    Spielwelt:  x -893 …  893, z -509 … 509

Damit liegt sie vollständig im Bereich ±1024 m, den der AO-Atlas des Trägers für das Terrain
abdeckt (u = x/2048 + 0,5, v = z/2048 + 0,5; siehe docs/formate/licht.md), und überdeckt die
Fläche der Portimão-Strecke (x -236 … 445, z -582 … 574).

Alle Weltkoordinaten der neuen Strecke (Geometrie, TRN~, Streckenlogik, Fahrzeugplätze, Kameras)
müssen mit denselben Funktionen umgerechnet werden.

Für Strecken, die schon in Spielkoordinaten vorliegen (AP13: Portimãos eigene Mittellinie), setzt
``setzen((0, 0, 0))`` den Versatz für den laufenden Prozess (``neue_strecke_tool.py bauen --versatz``).
Alle Module lesen ``VERSATZ`` erst beim Aufruf von ``punkte``/``matrix``.
"""

from typing import Sequence

import numpy as np

STANDARD_VERSATZ = (-591.0, 0.0, -473.0)  # Teststrecke (AP11), (dx, dy, dz) in Metern
VERSATZ = STANDARD_VERSATZ


def setzen(versatz) -> tuple:
    """Setzt den Versatz für diesen Prozess (Folge aus drei Zahlen oder Text "dx,dy,dz")."""
    global VERSATZ
    if isinstance(versatz, str):
        versatz = [t for t in versatz.replace(";", ",").split(",") if t.strip()]
    v = tuple(float(x) for x in versatz)
    if len(v) != 3 or not all(np.isfinite(v)):
        raise ValueError("Offset needs three finite numbers (dx, dy, dz): %r" % (versatz,))
    VERSATZ = v
    return VERSATZ


def punkte(p) -> np.ndarray:
    """Verschiebt Punkte (N×3 oder 3) von Generator- in Spielweltkoordinaten."""
    return np.asarray(p, dtype=np.float64) + np.asarray(VERSATZ)


def matrix(m: Sequence[float]) -> list:
    """Verschiebt eine 4×4-Transformationsmatrix im Layout der pkg-Layer (16 Werte, Translation in
    den Elementen 12–14, siehe docs/formate/pkg.md)."""
    m = list(m)
    if len(m) != 16:
        raise ValueError("4×4 matrix with 16 values expected")
    for i in range(3):
        m[12 + i] += VERSATZ[i]
    return m
