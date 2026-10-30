"""Alles ein- und ausblendbar (Menü Ansicht, Dialog, Speicherung) und die restlichen Verbesserungen der Durchsicht:
Kurvendetail, Gehe zu, Punktliste mit Station/Kurve, Meldungen deutsch und ohne „Nicht geprüft“, kompaktes
Querneigungsfeld, Ideallinie-Bereich, zuletzt geöffnet, Autosicherung, Bauoptionen, nicht-modaler Bau."""

import json

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from mapeditor import ansicht
from mapeditor.adapter import build_command
from mapeditor.build import BuildDialog, PlaytestDialog
from mapeditor.project import Project
from mapeditor.window import MainWindow, german_numbers
from conftest import EXAMPLES, ROOT
from test_fahrlinie import example_analysis  # noqa: F401  (Sitzungsfixture)


@pytest.fixture
def window(app, example_preview):
    project = Project.open(EXAMPLES / "teststrecke.json")
    project.manifest["versatz"] = [-591, 0, -473]
    w = MainWindow(project, auto_preview=False)
    w.resize(1400, 900)
    w.show()
    w.install_preview(example_preview)
    yield w
    for key in ansicht.DEFAULTS:            # Standard für die nächsten Tests
        w.apply_feature(key, ansicht.DEFAULTS[key])
    w.document.undo.setClean()
    w.close()


def test_every_feature_toggles_menu_sidebar_and_is_saved(window):
    w = window
    assert set(w.feature_actions) == set(ansicht.DEFAULTS)
    for key in ansicht.DEFAULTS:
        w.feature_actions[key].setChecked(False)
        assert w.view_state[key] is False
        assert ansicht.load_state()[key] is False or key == "fahrlinie_auto"   # ohne Automatik nie gespeichert
        if key in w.feature_checks:
            assert not w.feature_checks[key].isChecked()
    assert not w.left_panel.isVisible() and not w.right_panel.isVisible() and not w.results.isVisible()
    assert not w.toolbar.isVisible() and not w.statusBar().isVisible() and not w.project_heading.isVisible()
    for key, page in w.tab_features.items():
        assert not w.results.isTabVisible(w.results.indexOf(page))
    for attr in w.MAP_FEATURES.values():
        assert getattr(w.map, attr) is False
    assert not w.bank_feature and not w.welcome_feature
    w.show_all_features()
    assert all(w.view_state[k] for k in ansicht.DEFAULTS if k != "letztes_projekt")
    assert w.left_panel.isVisible() and w.results.isVisible() and w.map.show_points


def test_view_dialog_and_sidebar_stay_in_sync(window):
    w = window
    w.open_view_dialog()
    d = w.view_dialog
    d.boxes["ideallinie"].setChecked(False)
    assert not w.map.show_line and not w.feature_actions["ideallinie"].isChecked()
    assert not w.feature_checks["ideallinie"].isChecked()
    w.feature_checks["massstab" if "massstab" in w.feature_checks else "raster"].setChecked(False)
    w.feature_checks["raster"].setChecked(False)
    assert not d.boxes["raster"].isChecked() and not w.map.grid
    d.show_all()
    assert w.map.grid and w.map.show_line
    d.close()


def test_results_maximize_and_tab_visibility(window):
    w = window
    before = w.vertical_split.sizes()
    w.toggle_results_maximized()
    assert w.vertical_split.sizes()[0] == 0
    w.toggle_results_maximized()
    assert w.vertical_split.sizes()[0] > 0
    w.apply_feature("reiter_querschnitt", False)
    assert not w.results.isTabVisible(w.results.indexOf(w.cross_page))
    assert before


def test_curve_detail_turn_click_and_find(window, example_analysis):  # noqa: F811
    w = window
    w.store_analysis(w.current_key(), example_analysis)
    a = w.analysis
    curve = a["kurven"][3]
    w.show_turn(curve["spiel"])
    assert w.map.focus_curve and w.map.focus_curve["spiel"] == curve["spiel"]
    assert "T%d" % curve["spiel"] in w.map.focus_curve["info"]
    assert w.map.station == curve["s_scheitel"] or any(k["spiel"] == curve["spiel"] and k["s_scheitel"] == w.map.station
                                                       for k in a["kurven"])
    QTest.qWait(50)
    w.map.viewport().repaint()
    assert w.map._turn_hits, "Kurvenschilder werden als Klickflächen gemerkt"
    boxes = [b for b in w.map._placed]
    assert all(not a1.intersects(a2) for i, a1 in enumerate(boxes) for a2 in boxes[i + 1:]), "keine Überlappung"
    box, number = next((b, n) for b, n in w.map._turn_hits if b is not None)
    w.map.focus_curve = None
    QTest.mouseClick(w.map.viewport(), Qt.LeftButton, pos=box.center().toPoint())
    assert w.map.focus_curve and w.map.focus_curve["spiel"] == number
    # Gehe zu
    w.find_field.setText("T2")
    w.find_location()
    assert w.map.focus_curve["spiel"] == 2
    w.find_field.setText("1250")
    w.find_location()
    assert w.map.station == 1250 and w.document.selected >= 0
    w.find_field.setText("#3")
    w.find_location()
    assert w.document.selected == 2
    # Objektliste zeigt Station und Kurve
    w._refresh_objects()
    assert " m · " in w.objects.item(0).text()
    assert any("T" in w.objects.item(i).text() for i in range(w.objects.count()))


def test_issue_texts_german_and_not_checked_hidden(window):
    w = window
    assert german_numbers("bei 989.0 m (FIA ≤ 5.7°) Regel XM-03 v1.2.3") == "bei 989,0 m (FIA ≤ 5,7°) Regel XM-03 v1.2.3"
    w.set_issues([{"id": "XM-03", "level": "Warnung", "text": "reicht bei 989.0 m in eine Kurve",
                   "ort": {"spline": "mittellinie", "station_m": 989.0}}],
                 {"nicht_geprueft": ["BOX_FEHLT", "BOX_IN_HAUPT_TS"], "stufen": ["eingabe"]})
    assert w.issues.count() == 1 and "989,0 m" in w.issues.item(0).text() and "Mittellinie 989 m" in w.issues.item(0).text()
    w.issue_filter.setCurrentText("Nicht geprüft")
    assert w.issues.count() == 2
    w.issue_filter.setCurrentText("Alle")


def test_bank_panel_compact_and_ideal_line_category(window):
    w = window
    w.document.select(-1)
    w.category.setCurrentIndex(w.category.findData("object"))
    assert not w.bank_panel.isVisible() and not w.properties.isVisible()    # nichts gewählt: nichts Leeres anzeigen
    w.results.setCurrentWidget(w.bank_page)
    assert w.bank_panel.isVisible() and not w.bank_panel.detail.isVisible()
    w.results.setCurrentWidget(w.height_page)
    w.document.select(1)
    assert w.bank_panel.isVisible() and w.bank_panel.detail.isVisible() and w.properties.isVisible()
    w.category.setCurrentIndex(w.category.findData("ideallinie"))
    labels = [w.properties.item(r, 0).text() for r in range(w.properties.rowCount())]
    assert "Mindestzeit-Feinschliff (Scheitelsuche)" in labels and "Abstand Fahrzeugmitte – Rand · m" in labels
    w.category.setCurrentIndex(w.category.findData("startaufstellung"))
    assert w.map.highlight == "grid"
    w.category.setCurrentIndex(w.category.findData("object"))
    assert w.map.highlight is None


def test_recent_projects_and_autosave_recovery(app, tmp_path, example_preview):
    project = Project.open(EXAMPLES / "teststrecke.json")
    project.save(tmp_path / "Projekt")
    w = MainWindow(project, auto_preview=False)
    w.interactive = True                     # wie im echten Start: zuletzt geöffnet merken
    w._remember_current()
    assert ansicht.recent_projects()[0] == str(project.folder)
    w._fill_recent_menu()
    assert str(project.folder) in [a.text() for a in w.recent_menu.actions()]
    w.document.edit("Name", lambda raw, meta: raw.update(name="Ungesichert"))
    assert w.autosave() and w.recovery_path().exists()
    w.hide()                                 # „Absturz“: Fenster nicht regulär geschlossen
    again = MainWindow(Project.open(tmp_path / "Projekt"), auto_preview=False)
    asked = []
    assert again.offer_recovery(ask=lambda text: asked.append(text) or True)
    assert again.document.project.raw["name"] == "Ungesichert" and asked
    again.document.undo.undo()
    assert again.document.project.raw["name"] != "Ungesichert"
    assert again.save_project() is True and not again.recovery_path().exists()
    again.close()
    w.document.undo.setClean()
    w.close()


def test_left_column_sections(window):
    w = window
    assert not w.display_section.expanded() and "von" in w.display_section.head.text()
    w.feature_checks["raster"].setChecked(not w.feature_checks["raster"].isChecked())    # Kurzinfo folgt
    before = w.display_section.head.text()
    w.feature_checks["raster"].setChecked(not w.feature_checks["raster"].isChecked())
    assert before != w.display_section.head.text()
    w.display_section.head.click()
    assert w.feature_checks["raster"].isVisible() and w.display_section.expanded()
    limit, w.POINT_LIST_LIMIT = w.POINT_LIST_LIMIT, 1
    try:
        w.point_section_user = None
        w._point_section_update(w.objects.count())
        assert not w.objects.isVisible() and w.find_field.isVisible()        # lange Liste: eingeklappt
        w.point_section.head.click()                                         # eigene Wahl gilt
        assert w.objects.isVisible() and w.point_section_user is True
        w._point_section_update(w.objects.count())
        assert w.objects.isVisible()
    finally:
        w.POINT_LIST_LIMIT = limit


def test_build_options_weltkarten(tmp_path):
    basis = (ROOT / "ego-werkzeuge", tmp_path, tmp_path / "b", tmp_path / "p")
    an = build_command(*basis, "x1", options={"weltkarten": True, "biom": "wueste", "farbton": "herbst"})
    assert an[-5:] == ["--weltkarten", "--biom", "wueste", "--farbton", "herbst"]
    aus = build_command(*basis, "x2", options={"weltkarten": False, "biom": "wueste", "farbton": "herbst"})
    assert aus[-1] == "--no-weltkarten" and "--biom" not in aus
    assert "--weltkarten" not in build_command(*basis, "x3", options={})      # Vorgabe kommt aus dem Bauprofil
    with pytest.raises(ValueError):
        build_command(*basis, "x4", options={"weltkarten": True, "biom": "mond"})
    with pytest.raises(ValueError):
        build_command(*basis, "x5", options={"weltkarten": True, "farbton": "neon"})


def test_build_options_note_and_nonmodal(window, tmp_path):
    w = window
    command = build_command(ROOT / "ego-werkzeuge", tmp_path, tmp_path / "b", tmp_path / "p", "x1",
                            options={"streckencode": "hanoi", "streckenname": "Meine Strecke", "pvs_variante": "alles"})
    assert command[-6:] == ["--streckencode", "hanoi", "--streckenname", "Meine Strecke", "--pvs-variante", "alles"]
    with pytest.raises(ValueError):
        build_command(ROOT / "ego-werkzeuge", tmp_path, tmp_path / "b", tmp_path / "p", "x2",
                      options={"streckencode": "ausgedacht"})
    w.open_build()
    d = w.build_dialog
    assert d.windowModality() == Qt.NonModal
    assert not d.advanced.isVisible()
    d.advanced_toggle.setChecked(True)
    assert d.advanced.isVisible()
    d.code.setCurrentIndex(d.code.findData("hanoi"))
    assert "hanoi" in d.profile_hint.text() and d.options()["streckencode"] == "hanoi"
    d.close()
    p = PlaytestDialog.__new__(PlaytestDialog)
    title = PlaytestDialog._title({"datum": "d", "name": "testedit11", "beschreibung": "Turn 11", "bau_status": "ok",
                                   "injiziert": "x", "status": "ok"})
    assert "„Turn 11“" in title and "im Spiel" in title
    assert p is not None


def test_view_settings_isolated_in_tests():
    # conftest legt die Einstellungen in eine eigene INI-Datei (nie in das echte Benutzerprofil)
    store = ansicht.settings()
    assert "MapeditorTest" in store.fileName() and store.fileName().endswith(".ini")
    json.dumps(ansicht.load_state())
