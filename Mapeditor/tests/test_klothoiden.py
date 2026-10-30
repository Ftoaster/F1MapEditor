"""Klothoiden-Übergänge (Schritt B): Eigenschaften, Eingabezeichnung und Generatorvorschau."""

import copy
import math
import sys

import pytest
from PySide6.QtTest import QTest

from mapeditor.adapter import calculate_preview, toolkit_path
from mapeditor.input_geometry import input_geometry, transition_pieces
from mapeditor.project import Project
from mapeditor.window import MainWindow, parse_value
from conftest import EXAMPLES
from test_journeys import journey  # noqa: F401  (Fixture)


@pytest.fixture
def window(app, example_preview):
    view = MainWindow(Project.open(EXAMPLES / "teststrecke.json"), auto_preview=False)
    view.install_preview(example_preview)
    view.show()
    QTest.qWait(20)
    yield view
    view.document.undo.setClean()
    view.close()


def toolkit_definition():
    sys.path.insert(0, str(toolkit_path()))
    from strecke import definition
    return definition


def test_editor_pieces_match_toolkit():
    definition = toolkit_definition()
    segments = Project.open(EXAMPLES / "teststrecke.json").raw["mittellinie"]["segmente"]
    variants = [copy.deepcopy(segments) for _ in range(2)]
    variants[1][3]["uebergang"] = False
    variants[1][5]["uebergang"] = {"anfang": 30, "ende": False}
    for segs, rules in ((variants[0], {"aktiv": True}), (variants[1], {"aktiv": True, "a_faktor": .8}),
                        (variants[0], {"aktiv": True, "laenge_m": 35})):
        mine, _ = transition_pieces(segs, rules)
        theirs, _, _ = definition.segmente_mit_uebergaengen(segs, rules)
        assert len(mine) == len(theirs)
        for (length, k0, k1, source), piece in zip(mine, theirs):
            assert math.isclose(length, piece["laenge"], abs_tol=1e-9) and source == piece["quelle"]
            assert math.isclose(k0, piece["k0"], abs_tol=1e-12) and math.isclose(k1, piece["k1"], abs_tol=1e-12)


def test_input_drawing_shows_transitions_only_when_active():
    project = Project.open(EXAMPLES / "teststrecke.json")
    from mapeditor.document import Document
    document = Document(project)
    assert input_geometry(document)["transitions"] == []
    project.raw["mittellinie"]["uebergaenge"] = {"aktiv": True}
    geometry = input_geometry(document)
    line = geometry["transitions"]
    assert len(line) > 1000
    # Ohne Schließfehler-Verteilung endet die Linie 1–3 m neben dem Start (Abrückungen, Generator: 1,84 m)
    assert 1.0 < math.dist(line[0], line[-1]) < 3.0
    # Die Eingabe-Segmentkette (Griffe) bleibt unverändert
    assert len(geometry["segments"]) == 26


def test_preview_uses_transitions_and_segment_starts():
    project = Project.open(EXAMPLES / "teststrecke.json")
    project.raw["mittellinie"]["uebergaenge"] = {"aktiv": True}
    preview = calculate_preview({"raw": project.raw, "basis": str(project.basis)})
    assert len(preview["objects"]) == 26
    off = calculate_preview({"raw": Project.open(EXAMPLES / "teststrecke.json").raw, "basis": str(project.basis)})

    def largest_change_per_metre(result):
        k, s = result["curvature"], result["s"]
        return max(abs(k[i + 1] - k[i]) / max(s[i + 1] - s[i], 1e-6) for i in range(len(k) - 1))
    # Vorschau ist ausgedünnt (≈ 3 m): ohne Übergänge ≈ 0,013 1/m², mit ≈ 0,0044 1/m² (Haarnadel, 14,3 m)
    assert largest_change_per_metre(preview) < 0.006 < largest_change_per_metre(off)


def test_rules_properties_and_undo(window):
    window.category.setCurrentIndex(window.category.findData("mittellinie"))
    keys = [entry[0] for entry in window.properties.entries]
    assert "uebergaenge.aktiv" in keys and "uebergaenge.max_anteil" in keys
    row = keys.index("uebergaenge.aktiv")
    assert window.properties.item(row, 1).text() == "nein"
    before = copy.deepcopy(window.document.project.raw)
    window.properties.item(row, 1).setText("true")
    window.apply_properties()
    assert window.document.ml["uebergaenge"] == {"aktiv": True}
    assert window.map.input["transitions"]
    window.document.undo.undo()
    assert window.document.project.raw == before


def test_segment_transition_property(window):
    window.document.select(1)
    row = next(i for i, e in enumerate(window.properties.entries) if e[0] == "uebergang")
    window.properties.item(row, 1).setText("false")
    window.apply_properties()
    assert window.document.ml["segmente"][1]["uebergang"] is False
    assert parse_value("35,5", None, "uebergang") == 35.5
    assert parse_value('{"anfang": 20, "ende": false}', None, "uebergang") == {"anfang": 20, "ende": False}
    with pytest.raises(ValueError):
        parse_value("-3", None, "uebergang")


def test_approximation_field_in_centerline_rules(journey):
    """Schritt A: Feld „Glättende Approximation“ unter „Mittellinie · Regeln“ setzt mittellinie.approximation_m."""
    from test_journeys import edit_field
    w = journey("portimao_form.json")
    w.category.setCurrentIndex(w.category.findData("mittellinie"))
    QTest.qWait(10)
    assert "approximation_m" in [e[0] for e in w.properties.entries]
    edit_field(w, "approximation_m", "8")
    assert w.document.project.raw["mittellinie"]["approximation_m"] == 8.0
