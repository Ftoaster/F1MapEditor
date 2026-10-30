"""Querneigung einfach einstellen (25.10.2026): Stärke + Seite statt Vorzeichen, lokale Wirkung, Automatik."""

import copy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from mapeditor import neigung
from mapeditor.adapter import calculate_preview
from mapeditor.document import Document
from mapeditor.input_geometry import input_geometry
from mapeditor.project import Project
from conftest import EXAMPLES
from test_journeys import click_object, journey  # noqa: F401  (Fixture)


def ring_points(n=24, radius=300.0):
    """Kreis im Uhrzeigersinn in der Draufsicht (x, z) → lauter Rechtskurven in Eingaberichtung."""
    import math
    return [{"x": round(radius * math.cos(2 * math.pi * i / n), 3), "y": 0.0,
             "z": round(radius * math.sin(2 * math.pi * i / n), 3)} for i in range(n)]


def test_sign_and_text():
    # + = links höher. Rechtskurve (turn −1), Innenseite tiefer → rechts tiefer → links höher → +
    assert neigung.signed(4, "innen", -1) == 4 and neigung.signed(4, "innen", 1) == -4
    assert neigung.signed(4, "aussen", -1) == -4
    assert neigung.signed(3, "links", 0) == 3 and neigung.signed(3, "rechts", 1) == -3
    assert neigung.signed(4, "innen", 0) == 0                     # Gerade hat keine Innenseite
    assert neigung.describe(4, -1) == "4,0° · Kurveninnenseite tiefer"
    assert neigung.describe(-2, 0) == "2,0° · rechts höher" and neigung.describe(0.01, 1) == "flach"


def test_auto_magnitude_by_radius():
    assert neigung.auto_magnitude(60, 4) == 4 and neigung.auto_magnitude(80, 4) == 4
    assert neigung.auto_magnitude(600, 4) == 0 and neigung.auto_magnitude(None, 4) == 0
    assert 0 < neigung.auto_magnitude(160, 4) < 4


def test_point_turn_direction_matches_segments():
    """Kontrollpunkte aus einer Segmentstrecke: Kurvenrichtung wie das Segment (rechts = −1)."""
    project = Project.open(EXAMPLES / "teststrecke.json")
    document = Document(project)
    segments = copy.deepcopy(document.ml["segmente"])
    geometry = input_geometry(document)
    document.convert_to_points()
    turns = neigung.point_turns(document.points, project.closed)
    checked = 0
    for segment, entry in zip(segments, geometry["segments"]):
        if segment["typ"] == "bogen" and float(segment["radius"]) < 200:
            middle = (entry["first"] + entry["last"]) // 2
            want = 1 if segment["richtung"] == "links" else -1
            assert turns[middle][0] == want, (segment, turns[middle])
            assert turns[middle][1] == pytest.approx(float(segment["radius"]), rel=0.25)
            checked += 1
    assert checked >= 5


def test_single_point_stays_local():
    """Früher neigte ein einzelner Punktwert die ganze Runde; jetzt bleiben die Nachbarn bei ihrem Wert."""
    raw = {"mittellinie": {"art": "kontrollpunkte", "punkte": ring_points()}}
    neigung.set_values(raw, {5: 4.0}, True)
    banks = neigung.point_banks(raw["mittellinie"]["punkte"], True)
    assert banks[5] == 4.0 and banks[4] == 0 and banks[6] == 0 and banks[15] == 0
    # zweite Änderung weiter hinten lässt die erste unberührt
    neigung.set_values(raw, {12: -2.0, 13: -2.0}, True)
    banks = neigung.point_banks(raw["mittellinie"]["punkte"], True)
    assert banks[5] == 4.0 and banks[12] == banks[13] == -2.0 and banks[11] == 0 and banks[14] == 0 and banks[20] == 0
    # alles wieder flach → keine Felder mehr
    neigung.set_values(raw, {5: 0.0, 12: 0.0, 13: 0.0}, True)
    assert not any("querneigung_grad" in p for p in raw["mittellinie"]["punkte"])


def test_neighbours_keep_interpolated_ramp():
    raw = {"mittellinie": {"art": "kontrollpunkte", "punkte": ring_points()}}
    pts = raw["mittellinie"]["punkte"]
    pts[0]["querneigung_grad"], pts[10]["querneigung_grad"] = 0.0, 5.0      # gewollte Rampe 0 → 5°
    before = neigung.point_banks(pts, True)
    neigung.set_values(raw, {20: 3.0}, True)
    after = neigung.point_banks(pts, True)
    assert after[20] == 3.0
    assert all(after[i] == pytest.approx(before[i], abs=0.01) for i in range(0, 19)), "Rampe 0…10 verändert"


def test_points_match_generator():
    """Eingabewerte = Generatorwerte an den Punkten (Übergangsglättung aus)."""
    raw = {"name": "Neigung", "fahrbahn": {"uebergang_m": 0},
           "mittellinie": {"art": "kontrollpunkte", "punkte": ring_points(32, 280)}}
    neigung.set_values(raw, {8: 4.0, 9: 4.0, 10: 4.0}, True)
    project = Project(raw, {"schema": 1, "definition": "definition.json", "runde_geschlossen": True})
    preview = calculate_preview({"raw": project.raw, "basis": str(EXAMPLES), "shift": [0, 0, 0]})
    s, bank = preview["s"], preview["bank"]
    mine = neigung.point_banks(raw["mittellinie"]["punkte"], True)
    for i, station in enumerate(preview["object_s"]):
        k = min(range(len(s)), key=lambda j: min(abs(s[j] - station), preview["length"] - abs(s[j] - station)))
        assert bank[k] == pytest.approx(mine[i], abs=0.35), (i, bank[k], mine[i])


def test_panel_banks_selected_bend(journey):
    w = journey()
    assert not w.bank_panel.isVisible()                  # ohne Auswahl nur im Reiter Querneigung
    w.results.setCurrentWidget(w.bank_page)
    assert w.bank_panel.isVisible()
    bends = [i for i, s in enumerate(w.document.ml["segmente"]) if s["typ"] == "bogen"]
    i = bends[0]
    segment = w.document.ml["segmente"][i]
    click_object(w, i)
    assert w.bank_panel.state.text().startswith("Gewählt: flach")
    w.bank_panel.side.setCurrentIndex(w.bank_panel.side.findData("innen"))
    QTest.mouseClick(w.bank_panel.preset_buttons[2], Qt.LeftButton)
    want = 4.0 if segment["richtung"] == "rechts" else -4.0
    assert w.document.ml["segmente"][i]["querneigung_grad"] == want
    assert "Kurveninnenseite tiefer" in w.bank_panel.state.text() and "4,0°" in w.bank_panel.state.text()
    assert "∠ 4,0°" in w.objects.item(i).text()
    w.document.undo.undo()
    assert "querneigung_grad" not in w.document.ml["segmente"][i]


def test_panel_straight_needs_side(journey):
    w = journey()
    straight = next(i for i, s in enumerate(w.document.ml["segmente"]) if s["typ"] == "gerade")
    click_object(w, straight)
    w.bank_panel.side.setCurrentIndex(w.bank_panel.side.findData("innen"))
    w.bank_panel.amount.setValue(3)
    assert "querneigung_grad" not in w.document.ml["segmente"][straight]
    assert "keine Kurveninnenseite" in w.status_label.text()
    w.bank_panel.side.setCurrentIndex(w.bank_panel.side.findData("links"))
    w.bank_panel.side.activated.emit(w.bank_panel.side.currentIndex())
    assert w.document.ml["segmente"][straight]["querneigung_grad"] == 3


def test_auto_and_flat(journey):
    w = journey()
    steps = w.document.undo.count()
    w.bank_panel.amount.blockSignals(True)
    w.bank_panel.amount.setValue(5)
    w.bank_panel.amount.blockSignals(False)
    w.bank_panel.auto_button.click()
    assert w.document.undo.count() == steps + 1                       # ein Undo-Schritt
    segments = w.document.ml["segmente"]
    for s in segments:
        if s["typ"] == "gerade":
            assert "querneigung_grad" not in s
        elif s["typ"] == "bogen" and float(s["radius"]) <= 80:
            assert abs(s["querneigung_grad"]) == 5
            assert (s["querneigung_grad"] > 0) == (s["richtung"] == "rechts")
    assert any("querneigung_grad" in s for s in segments)
    assert "zur Kurveninnenseite geneigt" in w.status_label.text()
    w.bank_panel.flat_button.click()
    assert not any("querneigung_grad" in s for s in w.document.ml["segmente"])


def test_input_profile_interpolates_points(app):
    project = Project({"name": "Ring", "mittellinie": {"art": "kontrollpunkte", "punkte": ring_points()}},
                      {"schema": 1, "definition": "definition.json", "runde_geschlossen": True})
    document = Document(project)
    neigung.set_values(project.raw, {6: 4.0}, True)
    banks = input_geometry(document)["bank"]
    assert banks[6] == 4.0 and banks[5] == 0 and banks[18] == 0
