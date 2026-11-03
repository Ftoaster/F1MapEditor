"""Streckenpunkte kopieren und einfügen (Issue #2): Logik und echte Bedienung über zwei Fenster."""

import copy
import json
import math

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mapeditor import kopieren
from mapeditor.document import Document
from mapeditor.project import Project, point_xyz
from mapeditor.window import MainWindow
from test_journeys import undo


def gerade(x0, z0, dx, dz, n, schritt=10.0):
    return [{"x": x0 + dx * schritt * i, "y": 0.0, "z": z0 + dz * schritt * i} for i in range(n)]


def projekt(punkte, geschlossen=False, **raw):
    daten = {"name": "Test", "mittellinie": {"art": "kontrollpunkte", "punkte": punkte}}
    daten.update(raw)
    return Project(daten, {"schema": 1, "definition": "definition.json", "runde_geschlossen": geschlossen})


def stueck(punkte):
    return json.loads(kopieren.als_text({"format": kopieren.FORMAT, "version": 1, "punkte": punkte}))["punkte"]


# ------------------------------------------------------------------ Logik
def test_kette_ueber_rundenschluss_und_luecken():
    assert kopieren.kette({8, 9, 0, 1}, 10, True) == [8, 9, 0, 1]
    assert kopieren.kette({2, 3, 4}, 10, False) == [2, 3, 4]
    assert kopieren.kette({2, 4}, 10, True) is None
    assert kopieren.kette({8, 9, 0, 1}, 10, False) is None


def test_anhaengen_hinten_in_fahrtrichtung():
    strecke = gerade(0, 0, 1, 0, 6)                          # endet bei x = 50
    teil = gerade(60, 0, 1, 0, 4)                            # 60 … 90, gleiche Richtung
    plan = kopieren.einfuegen_plan(strecke, teil, False)
    assert plan["hinten"] and not plan["umgedreht"] and plan["verschoben"] is None and plan["versatz_m"] == 0
    assert [p["x"] for p in plan["punkte"][6:]] == [60, 70, 80, 90]


def test_umgedrehtes_stueck_wird_gedreht():
    strecke = gerade(0, 0, 1, 0, 6)
    teil = gerade(60, 0, 1, 0, 4)[::-1]                       # 90 … 60, falsch herum
    plan = kopieren.einfuegen_plan(strecke, teil, False)
    assert plan["hinten"] and plan["umgedreht"]
    assert [p["x"] for p in plan["punkte"][6:]] == [60, 70, 80, 90]


def test_vorne_anfuegen_verschiebt_streckenmeter():
    strecke = gerade(100, 0, 1, 0, 6)                        # 100 … 150
    teil = gerade(50, 0, 1, 0, 4)                            # 50 … 80, endet 20 m vor dem Anfang
    raw = {"fahrbahn": {"abschnitte": [{"von_s": 10, "bis_s": 30, "breite": 14}]},
           "seitenraum": {"abschnitte": [{"von_s": 0, "bis_s": 20, "seite": "links"}]},
           "start_ziel": {"s": 25}, "drs": {"zonen": [{"erkennung_m": 5, "aktivierung_m": 10, "ende_m": 40}]}}
    plan = kopieren.einfuegen_plan(strecke, teil, False)
    assert not plan["hinten"] and not plan["umgedreht"] and plan["neu"] == (0, 3)
    assert plan["versatz_m"] == pytest.approx(50.0)            # 30 m Stück + 20 m Verbindung
    kopieren.stationen_verschieben(raw, plan["versatz_m"])
    assert raw["fahrbahn"]["abschnitte"][0]["von_s"] == 60 and raw["fahrbahn"]["abschnitte"][0]["bis_s"] == 80
    assert raw["seitenraum"]["abschnitte"][0]["von_s"] == 50 and raw["start_ziel"]["s"] == 75
    assert raw["drs"]["zonen"][0]["erkennung_m"] == 5          # ab Ziel: bleibt


def test_knick_entscheidet_bei_aehnlichem_abstand():
    # Strecke läuft nach +x und endet bei (50, 0). Das Stück liegt quer davor; das Ende, das in Fahrtrichtung
    # weiterführt, gewinnt, obwohl das andere Ende etwas näher liegt.
    strecke = gerade(0, 0, 1, 0, 6)
    teil = [{"x": 58.0, "y": 0, "z": 5.0}, {"x": 70.0, "y": 0, "z": 5.0}, {"x": 80.0, "y": 0, "z": 5.0},
            {"x": 56.0, "y": 0, "z": -3.0}]
    teil = [teil[3], teil[0], teil[1], teil[2]]               # (56,-3) liegt am nächsten, läuft aber zurück
    hinten, umgedreht, abstand, knick = kopieren.anschluss(strecke, teil)
    assert hinten and not umgedreht


def test_weit_entferntes_stueck_rueckt_an():
    strecke = gerade(0, 0, 1, 0, 6)
    teil = gerade(1000, 500, 1, 0, 3)
    plan = kopieren.einfuegen_plan(strecke, teil, False)
    assert plan["verschoben"] is not None
    erster = plan["punkte"][6]
    assert math.dist((erster["x"], erster["z"]), (60, 0)) < 1e-6    # ein Punktabstand hinter dem Ende
    assert plan["punkte"][7]["x"] - erster["x"] == pytest.approx(10)  # Form bleibt


def test_geschlossene_runde_lehnt_ab():
    with pytest.raises(ValueError, match="offener Strecke"):
        kopieren.einfuegen_plan(gerade(0, 0, 1, 0, 6), gerade(0, 10, 1, 0, 3), True)


def test_fremder_text_wird_abgewiesen():
    for text in ("", "Hallo", json.dumps({"format": "anderes"}), json.dumps({"format": kopieren.FORMAT, "punkte": []})):
        with pytest.raises(ValueError, match="keine kopierten Streckenpunkte"):
            kopieren.aus_text(text)


def test_ueberschneidung_und_ueberstand():
    # U-Form, offen; das angehängte Stück läuft quer über den Anfang zurück
    strecke = gerade(0, 0, 1, 0, 11) + gerade(100, 10, 0, 1, 10) + gerade(90, 100, -1, 0, 10)   # endet bei (0, 100)
    n = len(strecke)
    teil = gerade(0, 90, 0, -1, 8)                             # (0,90) … (0,20): fährt auf den Anfang zu
    plan = kopieren.einfuegen_plan(strecke, teil, False)
    assert plan["hinten"]
    treffer = kopieren.ueberschneidungen(plan["punkte"], plan["neu"], 14.0)
    assert not treffer                                         # schließt nur an den Anfang an: gewollt
    teil2 = gerade(0, 90, 0, -1, 10) + gerade(10, 0, 1, 0, 5)   # … und läuft auf der Startgeraden weiter
    plan2 = kopieren.einfuegen_plan(strecke, teil2, False)
    treffer2 = kopieren.ueberschneidungen(plan2["punkte"], plan2["neu"], 14.0)
    assert treffer2 and min(treffer2) > n
    neu, weg, versatz = kopieren.ueberstand_plan(plan2["punkte"], set(range(*plan2["neu"])) | {plan2["neu"][1]}, 14.0, False)
    assert weg[0] == min(treffer2) and versatz == 0 and len(neu) == min(treffer2)


def test_oeffnen_hinter_punkt_und_streckenmeter():
    quadrat = gerade(0, 0, 1, 0, 10) + gerade(100, 0, 0, 1, 10) + gerade(100, 100, -1, 0, 10) + gerade(0, 100, 0, -1, 10)
    punkte, versatz, laenge = kopieren.oeffnen_plan(quadrat, {9})
    assert laenge == pytest.approx(400) and versatz == pytest.approx(100)
    assert (punkte[0]["x"], punkte[0]["z"]) == (100, 0) and len(punkte) == 40
    raw = {"start_ziel": {"s": 50}, "fahrbahn": {"abschnitte": [{"von_s": 120, "bis_s": 380}]}}
    kopieren.stationen_verschieben(raw, -versatz, laenge)
    assert raw["start_ziel"]["s"] == 350 and raw["fahrbahn"]["abschnitte"][0] == {"von_s": 20, "bis_s": 280}
    punkte2, versatz2, _ = kopieren.oeffnen_plan(quadrat, {38, 39, 0, 1})   # Stück über den Rundenschluss entfernen
    assert len(punkte2) == 36 and (punkte2[0]["x"], punkte2[0]["z"]) == (20, 0) and versatz2 == pytest.approx(20)


def test_dokument_einfuegen_ist_ein_undo_schritt(app):
    d = Document(projekt(gerade(100, 0, 1, 0, 6), start_ziel={"s": 10}))
    plan = d.paste_points(gerade(50, 0, 1, 0, 4))
    assert len(d.points) == 10 and d.selected_indices == {0, 1, 2, 3}
    assert d.project.raw["start_ziel"]["s"] == pytest.approx(60)
    d.undo.undo()
    assert len(d.points) == 6 and d.project.raw["start_ziel"]["s"] == 10


def test_dokument_umkehren_und_segmentziel(app):
    d = Document(projekt(gerade(0, 0, 1, 0, 6)))
    d.select_many({3, 4, 5})
    d.reverse_selection()
    assert [point_xyz(p)[0] for p in d.points] == [0, 10, 20, 50, 40, 30]
    seg = Document(Project({"name": "S", "mittellinie": {"art": "segmente", "start": {"x": 0, "z": 0},
                                                       "segmente": [{"typ": "gerade", "laenge": 100}]}},
                           {"schema": 1, "definition": "definition.json", "runde_geschlossen": False}))
    with pytest.raises(ValueError, match="Kontrollpunkte umwandeln"):
        seg.paste_points(gerade(0, 0, 1, 0, 3))
    seg.select_many({0})
    daten = kopieren.kopie(seg)                                # Segmentquelle: Punkte entlang der Geraden
    assert daten["punkte"][0]["x"] == 0 and daten["punkte"][-1]["x"] == pytest.approx(100)


# ------------------------------------------------------------------ Bedienung: zwei Fenster, echte Tasten
@pytest.fixture
def zwei_fenster(app):
    quelle = MainWindow(projekt(gerade(0, 300, 1, 0, 20), breite_test=True), auto_preview=False)
    ziel = MainWindow(projekt(gerade(0, 0, 1, 0, 10)), auto_preview=False)
    for w in (quelle, ziel):
        w.show()
        QTest.qWait(20)
        w.map.fit_track()
    yield quelle, ziel
    for w in (quelle, ziel):
        w.document.undo.setClean()
        w.close()


def karte_fokussieren(w):
    """Wie ein Klick in die Karte: Fenster aktiv, Tastaturfokus in der Karte (Kürzel gelten nur dort)."""
    w.activateWindow()
    QTest.qWait(20)
    w.map.setFocus()
    QTest.qWait(10)
    assert QApplication.focusWidget() is w.map


def test_strg_c_in_fenster_a_strg_v_in_fenster_b(zwei_fenster):
    quelle, ziel = zwei_fenster
    quelle.document.select_many(range(5, 9), 5)
    karte_fokussieren(quelle)
    QTest.keyClick(quelle.map, Qt.Key_C, Qt.ControlModifier)
    assert QApplication.clipboard().mimeData().hasFormat(kopieren.MIME)
    assert "4 Punkte kopiert" in quelle.status_label.text()
    vorher = copy.deepcopy(ziel.document.project.raw)
    karte_fokussieren(ziel)
    QTest.keyClick(ziel.map, Qt.Key_P)                         # Punkte-Werkzeug aktiv (Klick setzt Punkte)
    assert ziel.map.mode == "punkte"
    QTest.keyClick(ziel.map, Qt.Key_V, Qt.ControlModifier)
    assert len(ziel.document.points) == 14
    assert ziel.map.mode == "auswahl"                          # Ziehen verschiebt das Stück, statt Punkte zu setzen
    assert "an das offene Ende verschoben" in ziel.status_label.text()   # 300 m entfernt
    assert ziel.document.selected_indices == {10, 11, 12, 13}
    undo(ziel)
    assert ziel.document.project.raw == vorher


def test_strg_v_bei_geschlossener_runde_nennt_den_weg(zwei_fenster):
    quelle, ziel = zwei_fenster
    quelle.document.select_many(range(0, 3), 0)
    quelle.copy_points()
    ziel.document.close_round()
    karte_fokussieren(ziel)
    QTest.keyClick(ziel.map, Qt.Key_V, Qt.ControlModifier)
    assert len(ziel.document.points) == 10
    assert "Runde hier öffnen" in ziel.status_label.text()


def test_strg_c_in_textfeld_bleibt_textkopie(zwei_fenster):
    quelle, _ = zwei_fenster
    QApplication.clipboard().setText("vorher")
    quelle.document.select_many(range(0, 3), 0)
    from PySide6.QtWidgets import QLineEdit
    feld = QLineEdit("Text im Feld", quelle)
    feld.show()
    feld.setFocus()
    feld.selectAll()
    QTest.keyClick(feld, Qt.Key_C, Qt.ControlModifier)
    assert QApplication.clipboard().text() == "Text im Feld"
