"""Lokalisierung: Übersetzungstabellen vollständig, englische Oberfläche, Sprachwahl, Zahlenformat."""

import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QLocale, QSettings, QTimer
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMenuBar, QStatusBar, QToolBar

from mapeditor import ansicht, i18n
from mapeditor.i18n import lade_regeln, lade_tabelle, meldung, platzhalter, pflege, setze_sprache, sprache, tr, trn
from mapeditor.project import Project
from mapeditor.ux import choice_value, edit_text, help_html, zahl
from mapeditor.window import MainWindow, parse_value


@pytest.fixture
def englisch():
    setze_sprache("en")
    yield
    setze_sprache("de")


def test_tabelle_vollstaendig_und_ohne_luecken():
    befund = pflege.pruefen()
    assert befund["fehlend"] == [], "Schlüssel im Code ohne Tabellenzeile (pflege --schreiben)"
    assert befund["leer"] == [], "Zeilen ohne Englisch"
    assert befund["platzhalter"] == [] and befund["mehrzahl"] == [] and befund["doppelt"] == []
    zeilen = lade_tabelle()
    assert len(zeilen) > 700
    assert all(z["de"].strip() and z["en"].strip() for z in zeilen)
    # Mehrzahl in beiden Sprachen mit zwei Formen
    assert any(i18n.MEHRZAHL_TRENNER in z["de"] for z in zeilen)


def test_platzhalter_gleich():
    for zeile in lade_tabelle():
        for de, en in zip(zeile["de"].split("|"), zeile["en"].split("|")):
            assert platzhalter(de) == platzhalter(en), zeile["de"]
            # Leerzeichen am Rand gehören zum Satzbau (Text wird angehängt): in beiden Sprachen gleich
            assert de.startswith(" ") == en.startswith(" ") and de.endswith(" ") == en.endswith(" "), zeile["de"]


def test_regelkatalog_uebersetzt():
    root = Path(__file__).resolve().parents[2] / "ego-werkzeuge"
    sys.path.insert(0, str(root))
    try:
        from strecke import pruefregeln
    finally:
        sys.path.remove(str(root))
    regeln = {z["id"]: z for z in lade_regeln()}
    for regel in pruefregeln.katalog():
        assert regel["id"] in regeln and regeln[regel["id"]]["en"].strip(), regel["id"]


def test_meldungen_des_werkzeugkastens_vollstaendig():
    befund = pflege.meldungen_pruefen()
    assert not any(befund.values()), befund


@pytest.mark.parametrize("deutsch, englisch_erwartet", [
    ("keine Kurvenlinien für Bremslinien (Träger Portimão: 15 Haupt-Bremslinien)",
     "no corner lines for braking lines (Portimão carrier: 15 main braking lines)"),
    ("Ideallinie konvergiert nicht: KKT-Rest 0.0012", "Racing line does not converge: KKT residual 0.0012"),
    ("Geometrie und Fahrphysik", "Geometry and driving physics"),
    ("Fahrbahnrand an 12 Stationen außerhalb X/Z ±1024 m (inkl. Versatz (0, 0, 0))",
     "Track edge outside X/Z ±1024 m at 12 stations (including offset (0, 0, 0))"),
    ("rechts: an 3 Stationen weniger als 2.5 m Platz bis zum nächsten Streckenteil (Barriere auf Mindestabstand gesetzt)",
     "right: less than 2.5 m of space to the next part of the track at 3 stations (barrier set to minimum distance)"),
    ("Objekt b1 steht auf asphalt/randstein", "Object b1 stands on asphalt/kerb"),
    ("Strecke ragt über das Loch des Außenterrains hinaus: Der Bau bricht ab. Unter Werkzeuge den Spielweltversatz auf "
     "[69, 0, -18] stellen",
     "Track extends beyond the hole of the outer terrain: the build will stop. Under Tools, set the game world offset to "
     "[69, 0, -18]"),
])
def test_meldung_englisch(englisch, deutsch, englisch_erwartet):
    assert meldung(deutsch) == englisch_erwartet


def test_unbekannte_meldung_bleibt_und_deutsch_unveraendert(englisch):
    assert meldung("Etwas ganz Neues 12") == "Etwas ganz Neues 12"
    setze_sprache("de")
    text = "keine Kurvenlinien für Bremslinien (Träger Portimão: 15 Haupt-Bremslinien)"
    assert meldung(text) == text


def test_fehlerwert_in_tr_wird_uebersetzt(englisch):
    assert "Racing line does not converge" in tr("Fehler · Vorschau: {error}", error="Ideallinie konvergiert nicht: KKT-Rest 1")


def test_deutsch_ist_standard_und_unveraendert():
    assert sprache() == "de"
    assert tr("Speichern") == "Speichern"
    assert trn(1, "{n} Warnung", "{n} Warnungen") == "1 Warnung"
    assert trn(2, "{n} Warnung", "{n} Warnungen") == "2 Warnungen"
    assert zahl(4903.14, 1, "m") == "4.903,1 m"
    assert edit_text(1.5) == "1,5" and edit_text(True) == "ja"
    assert "Bedienung" in help_html()


def test_englisch_text_mehrzahl_zahlen(englisch):
    assert tr("Speichern") == "Save"
    assert tr("Projekt gespeichert: {folder}", folder="X") == "Project saved: X"
    assert trn(1, "{n} Warnung", "{n} Warnungen") == "1 warning"
    assert trn(3, "{n} Warnung", "{n} Warnungen") == "3 warnings"
    assert zahl(4903.14, 1, "m") == "4,903.1 m"
    assert QLocale().decimalPoint() == "."
    assert edit_text(1.5) == "1.5" and edit_text(False) == "no"
    # Eingaben: Punkt und Komma, yes/no, übersetzte Auswahlwerte zurück in Definitionswerte
    assert parse_value("1,5", 0.0, "breite") == 1.5 and parse_value("1.5", 0.0, "breite") == 1.5
    assert parse_value("yes", True, "aktiv") is True
    assert choice_value("richtung", "left") == "links"
    assert parse_value("right", "links", "richtung") == "rechts"
    # Tempolimit der Boxengasse: nur 60 oder 80 km/h (Werte der Spielstrecken)
    assert parse_value("60", 80.0, "tempolimit_kmh") == 60.0 and parse_value("80", 80.0, "tempolimit_kmh") == 80.0
    with pytest.raises(ValueError, match="60, 80"):
        parse_value("70", 80.0, "tempolimit_kmh")
    assert "How to use" in help_html() and "Bedienung" not in help_html()


def test_englisches_fenster(app, point_project, englisch):
    w = MainWindow(point_project, auto_preview=False)
    try:
        menus = [a.text() for a in w.menuBar().actions()]
        assert menus == ["&Project", "&Edit", "&View", "&Tools", "&Help"]
        tools = {a.text() for a in w.toolbar.actions()}
        assert {"Select", "Points", "Check", "Build", "Playtest"} <= tools
        tabs = [w.results.tabText(i) for i in range(w.results.count())]
        assert tabs[:4] == ["Height profile", "Banking", "Cross-section", "Speed"]
        assert "Analysis" in tabs
        assert w.category.itemText(0) == "Selected object"
        assert w.properties.horizontalHeaderItem(0).text() == "Property"
        labels = {w.properties.item(r, 0).text() for r in range(w.properties.rowCount())}
        w.category.setCurrentIndex(w.category.findData("fahrbahn"))
        w.document.select(0)
        w.category.setCurrentIndex(w.category.findData("object"))
        labels = {w.properties.item(r, 0).text() for r in range(w.properties.rowCount())}
        assert "Height Y · m" in labels and "Höhe Y · m" not in labels
        assert w.issue_filter.itemText(1) == "Error" and w.issue_filter.itemData(1) == "Fehler"
        w.set_issues([{"id": "XMODE_IN_KURVE", "kurz": "XM-03", "level": "Warnung", "text": "reicht bei 989.0 m"}])
        text = w.issues.item(0).text()
        assert text.startswith("Warning · XM-03") and "Straight Mode zone reaches into a tight corner" in text
        assert "989.0 m" in text
        assert w.issue_badge.text() == "0 errors · 1 warning · 0 hints"
        assert w.language_actions["en"].isChecked()
    finally:
        w.document.undo.setClean()
        w.close()


def _anzahl_eigene_objekte(w):
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    return sum(isinstance(c, (QAction, QActionGroup, QTimer, QToolBar, QMenuBar, QStatusBar)) for c in w.children())


def test_sprachwechsel_sofort_ohne_neustart(app, point_project, example_preview):
    assert ansicht.load_language() == "de"
    w = MainWindow(point_project, auto_preview=False)
    w.resize(1400, 900)
    w.show()
    w.install_preview(example_preview)
    try:
        w.document.edit("Test", lambda raw, meta: raw.update(name="Umbenannt"))
        schritte = w.document.undo.count()
        w.map.scale(2.0, 2.0)
        zoom = w.map.transform().m11()
        w.results.setCurrentIndex(w.results.indexOf(w.speed))
        w.category.setCurrentIndex(w.category.findData("boxengasse"))
        w.find_field.setText("T3")
        karte, objekte = w.map, _anzahl_eigene_objekte(w)
        assert w.change_language("en", ask=False) is w                  # dasselbe Fenster, keine Rückfrage
        assert QSettings().value("sprache") == "en" and ansicht.load_language() == "en"
        assert [a.text() for a in w.menuBar().actions()] == ["&Project", "&Edit", "&View", "&Tools", "&Help"]
        assert w.category.itemText(0) == "Selected object" and w.results.tabText(0) == "Height profile"
        assert w.undo_action.text().startswith("Undo") and w.language_actions["en"].isChecked()
        assert w.map is not karte and w.map.transform().m11() == pytest.approx(zoom)    # Ausschnitt bleibt
        assert w.results.currentWidget() is w.speed and w.category.currentData() == "boxengasse"
        assert w.find_field.text() == "T3" and w.preview is example_preview
        assert "Preview up to date" in w.status_label.text()
        assert w.document.undo.count() == schritte and not w.document.undo.isClean()   # Verlauf bleibt
        assert _anzahl_eigene_objekte(w) == objekte                    # alte Menüs/Aktionen/Zeitgeber entsorgt
        w.document.undo.undo()
        assert w.document.project.raw["name"] != "Umbenannt"
        w.document.undo.redo()
        assert w.change_language("de") is w
        assert [a.text() for a in w.menuBar().actions()][0] == "&Projekt" and ansicht.load_language() == "de"
        assert w.document.project.raw["name"] == "Umbenannt" and w.category.itemText(0) == "Ausgewähltes Objekt"
    finally:
        w.document.undo.setClean()
        w.close()
        setze_sprache("de")


def test_startoption_sprache():
    import mapeditor.__main__ as start
    import inspect
    assert "--sprache" in inspect.getsource(start.main)
