"""Nutzerreisen zur überarbeiteten Oberfläche (25.10.2026): Startbildschirm, Klapplisten,
Zahlenformat, Prüfsumme in der Statuszeile, Hilfe, Werkzeugleiste."""


import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox

from mapeditor.project import Project
from mapeditor.ux import edit_text, zahl
from mapeditor.window import MainWindow, parse_value
from conftest import EXAMPLES, doc_screenshot
from test_adapter import wait_until
from test_journeys import click_object, edit_field, journey  # noqa: F401  (Fixture)


def open_editor(w, key):
    table = w.properties
    row = next(i for i, e in enumerate(table.entries) if e[0] == key)
    item = table.item(row, 1)
    table.scrollToItem(item)
    center = table.visualItemRect(item).center()
    QTest.mouseClick(table.viewport(), Qt.LeftButton, pos=center)
    QTest.mouseDClick(table.viewport(), Qt.LeftButton, pos=center)
    return row, QApplication.focusWidget()


def test_number_format_and_parsing():
    assert zahl(4903.14, 0, "m") == "4.903 m"
    assert zahl(14.25, 1, "m") == "14,3 m"
    assert edit_text(486.8845638138274) == "486,885"
    assert edit_text(1600.0) == "1600" and edit_text(6) == "6" and edit_text(-0.0001) == "0"
    assert edit_text(True) == "ja" and edit_text(False) == "nein"
    assert parse_value("12,5", 0.0, "laenge") == 12.5
    assert parse_value("ja", False, "aktiv") is True and parse_value("nein", True, "partial") is False
    assert parse_value("true", False, "uebergaenge.aktiv") is True
    assert parse_value("nein", None, "uebergang") is False and parse_value("40", None, "uebergang") == 40.0
    assert parse_value("links", "rechts", "richtung") == "links" and parse_value("", None, "breite") is None
    assert parse_value("false", True, "aktiv") is False
    with pytest.raises(ValueError, match="ganze"):          # (vorher test_ui.test_integer_rejects_fraction)
        parse_value("2,5", 2, "anzahl")


def test_welcome_panel_for_empty_project(app):
    w = MainWindow(Project(), auto_preview=False)
    w.resize(1400, 900)
    w.show()
    QTest.qWait(30)
    try:
        assert w.welcome.isVisible()
        assert set(w.welcome.buttons) == {"zeichnen", "vorlage", "vorschlag", "oeffnen", "beispiel"}
        doc_screenshot(w, "startbildschirm.png")
        QTest.mouseClick(w.welcome.buttons["zeichnen"], Qt.LeftButton)
        assert w.map.mode == "punkte" and w.mode_actions["punkte"].isChecked()
        assert not w.welcome.isVisible() and "EINFÜGEN" in w.mode_hint.text()
        QTest.keyClick(w.map, Qt.Key_Escape)
        assert w.welcome.isVisible()                      # noch leer → Einstieg wieder sichtbar
        w.document.insert()
        assert w.document.points and not w.welcome.isVisible()
    finally:
        w.document.undo.setClean()
        w.close()


def test_welcome_example_opens_test_track(app):
    w = MainWindow(Project(), auto_preview=False)
    w.show()
    try:
        QTest.mouseClick(w.welcome.buttons["beispiel"], Qt.LeftButton)
        assert w.document.ml.get("art") == "segmente" and not w.welcome.isVisible()
        assert w.document.project.raw.get("name")
    finally:
        w.document.undo.setClean()
        w.close()


def test_choice_fields_use_dropdown(journey):
    w = journey()
    click_object(w, 1)                                    # Bogen T1
    row, editor = open_editor(w, "richtung")
    assert isinstance(editor, QComboBox) and [editor.itemText(i) for i in range(editor.count())] == ["links", "rechts"]
    old = w.document.ml["segmente"][1]["richtung"]
    new = "links" if old == "rechts" else "rechts"
    QTest.keyClicks(editor, new)
    QTest.keyClick(editor, Qt.Key_Return)
    QTest.qWait(25)
    assert w.document.ml["segmente"][1]["richtung"] == new
    w.category.setCurrentIndex(w.category.findData("mittellinie"))
    row, editor = open_editor(w, "uebergaenge.aktiv")
    assert isinstance(editor, QComboBox) and editor.currentText() == "nein"
    QTest.keyClicks(editor, "ja")
    QTest.keyClick(editor, Qt.Key_Return)
    QTest.qWait(25)
    assert w.document.ml["uebergaenge"]["aktiv"] is True
    assert w.properties.item(row, 1).text() == "ja"


def test_status_bar_summary_badge_and_tab(journey):
    w = journey()
    text = w.status_label.text()
    assert "Vorschau aktuell" in text and "Länge 4.903 m" in text and "Uhrzeigersinn" in text
    assert "Fehler" in w.issue_badge.text() or w.issue_badge.text() == "Keine Probleme gefunden"
    w.results.setCurrentWidget(w.height_page)
    QTest.mouseClick(w.issue_badge, Qt.LeftButton)
    assert w.results.currentWidget() is w.issue_panel
    assert w.results.tabText(w.issue_tab).startswith("Prüfung")
    assert not w.busy_label.isVisible() and not w.cancel_button.isVisible()


def test_object_list_and_heading_are_readable(journey):
    w = journey()
    first = w.objects.item(0).text()
    assert "Start-Ziel-Gerade" in first and "Gerade · 1.328 m" in first
    bend = w.objects.item(1).text()
    assert "Bogen · R 40 m · 79° rechts" in bend
    assert "Teststrecke" in w.project_heading.text() and "E0" not in w.project_heading.text()
    assert "26 Segmente · Runde geschlossen" == w.source_label.text()


def test_toolbar_grouping_and_help(journey):
    w = journey()
    assert w.toolbar.widgetForAction(w.build_action).objectName() == "primary"
    assert "Strg+Enter" in w.check_action.toolTip() and "(V)" in w.mode_actions["auswahl"].toolTip()
    assert w.close_action.text() == "Runde öffnen"        # Teststrecke ist geschlossen
    QTest.keyClick(w, Qt.Key_F1)
    QTest.qWait(30)
    assert w.help_dialog.isVisible() and "Schnellstart" in w.help_dialog.browser.toPlainText()
    w.help_dialog.close()


def test_close_lap_button_shows_the_next_step(app):
    w = MainWindow(Project(), auto_preview=False)
    w.show()
    QApplication.processEvents()
    button = w.toolbar.widgetForAction(w.close_action)
    assert button.isVisible() and button.objectName() == "runde"
    assert not w.close_action.isEnabled() and "mindestens vier" in w.close_action.toolTip()
    for x, z in [(0, 0), (100, 0), (100, 100), (0, 100)]:
        w.document.insert(x, z)
    QApplication.processEvents()
    assert w.close_action.isEnabled() and w.close_action.text() == "Runde schließen" and button.property("offen") is True
    w.close_action.trigger()
    QApplication.processEvents()
    assert w.document.project.closed and w.close_action.text() == "Runde öffnen" and button.property("offen") is False
    w.close_action.trigger()
    QApplication.processEvents()
    assert not w.document.project.closed and w.close_action.isEnabled()
    w.document.undo.setClean()
    w.close()


def test_point_list_and_property_numbers(app):
    if not (EXAMPLES / "portimao_form.json").exists():
        pytest.skip("portimao_form.json gehört nicht zum öffentlichen Stand")
    w = MainWindow(Project.open(EXAMPLES / "portimao_form.json"))
    w.show()
    try:
        wait_until(lambda: w.preview is not None and w.job is None, 15000)
        item = w.objects.item(0).text()
        assert item.strip().startswith("1") and " m" in item and "X " in item
        click_object(w, 0)
        values = {e[0]: w.properties.item(i, 1).text() for i, e in enumerate(w.properties.entries)}
        assert all(len(v.split(",")[-1]) <= 3 for k, v in values.items() if k in ("x", "z") and "," in v)
    finally:
        w.document.undo.setClean()
        w.close()
