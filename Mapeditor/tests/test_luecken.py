"""Lücken zwischen Kontrollpunkten entlang der sichtbaren Strecke füllen (Knopf „Lücken füllen“)."""

import copy
import math

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton

from mapeditor.project import point_xyz
from test_journeys import current_preview, journey, undo  # noqa: F401  (Fixture)


def gap_button(w):
    return next(b for b in w.findChildren(QPushButton) if b.text().startswith("Lücken füllen"))


def make_gap(w, start=100, count=60):
    """Punkte wie nach einem Rahmen-Löschen entfernen (Vorbereitung, kein Prüfgegenstand)."""
    w.document.select_many(set(range(start, start + count)), start)
    QTest.keyClick(w.map, Qt.Key_Delete)
    current_preview(w)


def nearest(preview, x, z):
    return min(math.hypot(p[0] - x, p[2] - z) for p in preview["pos"])


def test_fill_all_gaps_along_visible_track_and_undo(journey):
    w = journey("portimao_form.json")
    make_gap(w)
    gaps = w.document.gaps()
    assert len(gaps) == 1 and gaps[0][0] == 99 and gaps[0][1] > 100
    before = copy.deepcopy(w.document.project.raw)
    count = len(w.document.points)
    preview = w.preview
    button = gap_button(w)
    assert button.isVisible() and button.isEnabled() and button.text() == "Lücken füllen (1)"
    QTest.mouseClick(button, Qt.LeftButton)
    QTest.qWait(20)
    added = len(w.document.points) - count
    assert added > 10
    assert not w.document.gaps()
    # neue Punkte liegen auf der vorher sichtbaren Strecke (Vorschau ist auf ≤ 1.800 Punkte ausgedünnt)
    for p in w.document.points[100:100 + added]:
        x, y, z = point_xyz(p)
        assert nearest(preview, x, z) < 3.0
    # gleichmäßig wie die übrigen Punkte
    pts = [point_xyz(p) for p in w.document.points[98:100 + added + 2]]
    steps = [math.dist((a[0], a[2]), (b[0], b[2])) for a, b in zip(pts, pts[1:])]
    assert max(steps) < 3 * min(steps) + 1
    assert w.document.undo.index() == 2      # Löschen + Füllen
    undo(w)
    assert w.document.project.raw == before


def test_fill_only_selected_pair(journey):
    w = journey("portimao_form.json")
    make_gap(w, 100, 60)
    make_gap(w, 300, 60)
    assert len(w.document.gaps()) == 2
    w.document.select_many({99, 100}, 99)
    QTest.mouseClick(gap_button(w), Qt.LeftButton)
    QTest.qWait(20)
    remaining = w.document.gaps()
    assert len(remaining) == 1 and remaining[0][0] > 200


def test_button_hidden_for_segments_and_disabled_without_gaps(journey):
    w = journey()
    assert not gap_button(w).isVisible()
    w2 = journey("portimao_form.json")
    assert gap_button(w2).isVisible() and not gap_button(w2).isEnabled()
