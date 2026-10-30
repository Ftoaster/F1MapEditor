"""Darstellung (Durchsicht 3, 26.10.2026): Eigenschaften ohne Doppelungen (E7), Prüfliste nach Schwere (E10),
Kurvennummern ohne Überlappung (E11), eine Kopfzeile im Profil (E12)."""

from PySide6.QtTest import QTest

from conftest import EXAMPLES
from mapeditor.project import Project
from mapeditor.views import label_rows
from mapeditor.window import ROAD_WIDTH, MainWindow, issue_order
from test_hoehe import hill_project


def open_window(app, project):
    w = MainWindow(project, auto_preview=False)
    w.resize(1400, 950)
    w.show()
    w.category.setCurrentIndex(w.category.findData("object"))
    QTest.qWait(20)
    return w


def keys(w):
    return [e[0] for e in w.properties.entries]


def set_property(w, key, text):
    row = keys(w).index(key)
    w.properties.item(row, 1).setText(text)
    w.apply_properties()
    QTest.qWait(10)


def test_object_width_and_bank_shown_once(app):
    w = open_window(app, hill_project())
    try:
        w.document.select(2)
        QTest.qWait(10)
        assert keys(w) == ["x", "z", "y", "breite", "asymmetrisch"]      # Querneigung nur im Querneigungsfeld
        assert w.bank_panel.isVisible()
        base = ROAD_WIDTH                                                 # ohne Vorschau: Standardbreite
        set_property(w, "asymmetrisch", "ja")
        point = w.document.project.raw["mittellinie"]["punkte"][2]
        assert point["breite_links"] == point["breite_rechts"] == base / 2
        assert keys(w) == ["x", "z", "y", "asymmetrisch", "breite_links", "breite_rechts"]
        set_property(w, "breite_links", "8")
        set_property(w, "asymmetrisch", "nein")
        point = w.document.project.raw["mittellinie"]["punkte"][2]
        assert "breite_links" not in point and "breite_rechts" not in point
        assert point["breite"] == 8 + base / 2                            # Gesamtbreite bleibt erhalten
        assert keys(w) == ["x", "z", "y", "breite", "asymmetrisch"]
        w.apply_feature("querneigungsfeld", False, save=False)
        assert keys(w)[-1] == "querneigung_grad"                          # ohne Feld wieder als Zeile
        w.apply_feature("querneigungsfeld", True, save=False)
        assert "querneigung_grad" not in keys(w)
    finally:
        w.document.undo.setClean()
        w.close()


def test_width_from_preview_where_the_object_is(app, example_preview):
    w = open_window(app, Project.open(EXAMPLES / "teststrecke.json"))
    try:
        w.install_preview(example_preview)
        w.document.select(1)
        QTest.qWait(10)
        links, rechts = w._current_halves(1)
        row = keys(w).index("breite")
        item = w.properties.item(row, 1)
        assert item.font().italic() and float(item.text().replace(",", ".")) == round(links + rechts, 2)
        set_property(w, "asymmetrisch", "ja")
        segment = w.document.ml["segmente"][1]
        assert (segment["breite_links"], segment["breite_rechts"]) == (links, rechts)    # Breite bleibt gleich
    finally:
        w.document.undo.setClean()
        w.close()


def test_issue_list_sorted_by_severity_then_station(app):
    w = open_window(app, hill_project())
    try:
        w.set_issues([
            {"id": "A", "kurz": "H-1", "level": "Hinweis", "text": "h", "ort": {"station_m": 10.0}},
            {"id": "B", "kurz": "W-2", "level": "Warnung", "text": "w2", "ort": {"station_m": 500.0}},
            {"id": "C", "kurz": "F-1", "level": "Fehler", "text": "f"},
            {"id": "D", "kurz": "W-1", "level": "Warnung", "text": "w1", "ort": {"station_m": 20.0}},
            {"id": "E", "kurz": "W-0", "level": "Warnung", "text": "w0"},
        ])
        order = [w.issues.item(i).text().split(" · ")[1] for i in range(w.issues.count())]
        assert order == ["F-1", "W-0", "W-1", "W-2", "H-1"]
        assert issue_order({"level": "Nicht geprüft"})[0] == 3
    finally:
        w.close()


def test_curve_labels_use_second_row_when_close():
    # T9 bei x=100, T10 knapp daneben, T11 wieder frei, T12 dicht an T11
    assert label_rows([(90, 110), (100, 122), (300, 320), (310, 332)]) == [0, 1, 0, 1]
    assert label_rows([(0, 10), (5, 15), (8, 18)]) == [0, 1, 0]          # alles voll: die freiere Zeile


def test_profile_has_single_header_line(app):
    w = open_window(app, hill_project())
    try:
        w.results.setCurrentWidget(w.height_page)
        QTest.qWait(30)
        w.height.repaint()
        plot = w.height._frame[0]
        assert plot.top() == 30 and plot.height() == w.height.height() - 60
    finally:
        w.close()
