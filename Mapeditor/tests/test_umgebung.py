"""Umgebung (Bäume, Gebäude …): Datei, Rückgängig, Karte, Katalog, Bepflanzen, Meldungen, Bau.

Gearbeitet wird gegen den Vertrag ``docs/zwischenstand/umgebung_vertrag.md`` mit Attrappen für Katalog, Vorschaubild
und Prüfung (Arbeitsprozess-Attrappe wie in ``test_build``); steht das echte Modul ``strecke/umgebung.py`` des
Werkzeugkastens bereit, prüfen die letzten Tests zusätzlich gegen dieses.
"""

import copy
import json
import math
import os
import sys

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest

from mapeditor import umgebung, window as window_module
from mapeditor.adapter import (build_command, calculate_env_check, calculate_preview, umgebung_laden, umgebung_speichern)
from mapeditor.project import Project, read_json
from mapeditor.window import MainWindow
from conftest import EXAMPLES, ROOT
from test_adapter import wait_until
from test_build import builder, click_build  # noqa: F401  (Fixture)
from test_journeys import current_preview, journey  # noqa: F401  (Fixture)

BAUM = "eaid://environment_package/core_assets/speedtree/idf/st_oak/st_oak_a_05"
BAUM_B = "eaid://environment_package/core_assets/speedtree/idf/st_oak/st_oak_b_01"
BUSCH = "eaid://environment_package/core_assets/speedtree/idf/st_bush/st_bush_a_02"
HAUS = "eaid://environment_package/core_assets/buildings/tribune_a"

KATALOG = {"schema": 1, "spielordner": "G:/Spiel", "erzeugt": "2026-10-28T10:00:00", "modelle": [
    {"modell": BAUM, "name": "st_oak_a_05", "familie": "st_oak", "kategorie": "baum", "groesse_m": [9, 14, 9], "radius_m": 4.5,
     "quelle": "tracks/bahrain/wep/bahrain_common.erp", "im_traeger": True},
    {"modell": BAUM_B, "name": "st_oak_b_01", "familie": "st_oak", "kategorie": "baum", "groesse_m": [7, 11, 7], "radius_m": 3.5,
     "quelle": "tracks/bahrain/wep/bahrain_common.erp", "im_traeger": False},
    {"modell": BUSCH, "name": "st_bush_a_02", "familie": "st_bush", "kategorie": "busch", "groesse_m": [3, 2, 3], "radius_m": 1.5,
     "quelle": "tracks/bahrain/wep/bahrain_common.erp", "im_traeger": False},
    {"modell": HAUS, "name": "tribune_a", "familie": "tribune", "kategorie": "gebaeude", "groesse_m": [40, 12, 14], "radius_m": 20,
     "quelle": "tracks/spa/wep/spa_common.erp", "im_traeger": False}]}


def pixel(w, x, z):
    return w.map.mapFromScene(QPointF(x, z))


def click_map(w, x, z, modifiers=Qt.NoModifier):
    QTest.mouseClick(w.map.viewport(), Qt.LeftButton, modifiers, pixel(w, x, z))
    QTest.qWait(10)


def drag_map(w, a, b, modifiers=Qt.NoModifier, steps=3):
    start, end = pixel(w, *a), pixel(w, *b)
    QTest.mousePress(w.map.viewport(), Qt.LeftButton, modifiers, start)
    for i in range(1, steps + 1):
        QTest.mouseMove(w.map.viewport(), start + (end - start) * i / steps, delay=10)
    QTest.mouseRelease(w.map.viewport(), Qt.LeftButton, modifiers, end)
    QTest.qWait(10)


def choose_model(w, *modelle):
    """Modelle im Katalog wie ein Nutzer wählen (Klick, mit Strg weitere)."""
    panel = w.env_panel
    w.results.setCurrentWidget(panel)
    first = True
    for modell in modelle:
        item = next(panel.list.item(r) for r in range(panel.list.count()) if panel.list.item(r).data(Qt.UserRole) == modell)
        QTest.mouseClick(panel.list.viewport(), Qt.LeftButton, Qt.NoModifier if first else Qt.ControlModifier,
                         panel.list.visualItemRect(item).center())
        first = False
    QTest.qWait(10)


@pytest.fixture
def env_window(app, point_project):
    """Fenster ohne Automatik (kein Vorschau-/Fahrlinienlauf), Katalog als Attrappe geladen, Karte eingepasst."""
    w = MainWindow(point_project, auto_preview=False)
    w.show()
    w.set_env_catalog(KATALOG)
    w.map.fit_track()
    QTest.qWait(25)
    yield w
    w.document.undo.setClean()
    w.close()


# ------------------------------------------------------------------ Datei (Vertrag Abschnitt 1)
def test_datei_standardwerte_reihenfolge_und_unbekannte_schluessel():
    text = json.dumps({"schema": 1, "zukunft": {"a": [1]}, "objekte": [
        {"id": "o7", "modell": BAUM, "x": 1, "z": 2, "extra": "bleibt"}]})
    daten = umgebung.lade_text(text)
    assert daten["bodenbewuchs"] is True and daten["zukunft"] == {"a": [1]}
    objekt = daten["objekte"][0]
    assert (objekt["hoehe_m"], objekt["drehung_grad"], objekt["skala"], objekt["gruppe"]) == (0.0, 0.0, 1.0, "")
    assert objekt["extra"] == "bleibt"
    geschrieben = json.loads(umgebung.als_text(daten))
    assert list(geschrieben)[:3] == ["schema", "bodenbewuchs", "objekte"] and geschrieben["zukunft"] == {"a": [1]}
    assert list(geschrieben["objekte"][0])[:8] == list(umgebung.OBJEKT_SCHLUESSEL) and geschrieben["objekte"][0]["extra"] == "bleibt"
    assert umgebung.als_text(daten).endswith("}\n") and "  \"schema\": 1" in umgebung.als_text(daten)
    for kaputt in ({"schema": 2}, {"objekte": "x"}, {"objekte": [{"id": "a", "modell": BAUM, "x": "1", "z": 0}]},
                   {"objekte": [{"id": "a", "modell": BAUM, "x": 1, "z": 0}] * 2}):
        with pytest.raises(ValueError):
            umgebung.normalisieren(kaputt)


def test_projekt_laden_speichern_rundlauf_und_kaputte_datei(tmp_path, point_project):
    project = Project(point_project.raw, point_project.manifest)
    project.umgebung = umgebung.neu()
    project.umgebung["objekte"].append(umgebung.neues_objekt(set(), BAUM, 10, 20, gruppe="Wald"))
    project.umgebung["zukunft"] = {"x": 1}
    project.save(tmp_path / "p")
    datei = tmp_path / "p" / "umgebung.json"
    assert datei.is_file() and read_json(datei)["objekte"][0]["gruppe"] == "Wald"
    geladen = Project.open(tmp_path / "p")
    assert geladen.umgebung == project.umgebung and geladen.umgebung_fehler is None
    assert umgebung_laden(datei) == project.umgebung                       # Adapter = Vertrags-API
    umgebung_speichern(project.umgebung, tmp_path / "p2" / "umgebung.json")
    assert read_json(tmp_path / "p2" / "umgebung.json")["zukunft"] == {"x": 1}
    # Eine nicht lesbare Datei bleibt unberührt, das Projekt öffnet trotzdem
    datei.write_text("{kaputt", encoding="utf-8")
    kaputt = Project.open(tmp_path / "p")
    assert kaputt.umgebung is None and "JSON" in kaputt.umgebung_fehler
    kaputt.save()
    assert datei.read_text(encoding="utf-8") == "{kaputt"
    # Ohne Umgebung entsteht keine Datei
    Project(point_project.raw, point_project.manifest).save(tmp_path / "leer")
    assert not (tmp_path / "leer" / "umgebung.json").exists()


def test_manifest_schluessel_umgebung_und_pfadschutz(tmp_path, point_project):
    manifest = dict(point_project.manifest, umgebung="szene.json")
    project = Project(point_project.raw, manifest)
    project.umgebung = umgebung.neu()
    project.save(tmp_path / "p")
    assert (tmp_path / "p" / "szene.json").is_file() and not (tmp_path / "p" / "umgebung.json").exists()
    for boese in ("../aussen.json", "projekt.json", "spieltests.json"):
        assert Project(point_project.raw, dict(point_project.manifest, umgebung=boese)).umgebung_path(tmp_path / "p") == \
            tmp_path / "p" / "umgebung.json"


# ------------------------------------------------------------------ Platzieren, Rückgängig, ungespeichert
def test_platzieren_per_klick_rueckgaengig_wiederholen_und_speichern(env_window, tmp_path):
    w = env_window
    revision = w.document.revision
    assert w.document.undo.isClean() and w.document.project.umgebung is None
    assert [w.results.tabText(i) for i in range(w.results.count())][-1] == "Umgebung"
    choose_model(w, BAUM)
    assert w.map.mode == "umgebung" and w.map.env_model == BAUM                  # Wählen schaltet auf das Werkzeug
    click_map(w, 100, 100)
    env = w.document.project.umgebung
    assert env["bodenbewuchs"] is True and len(env["objekte"]) == 1                # neue Datei: Bodenbewuchs an
    objekt = env["objekte"][0]
    assert objekt["modell"] == BAUM and objekt["id"] == "o1" and objekt["x"] == pytest.approx(100, abs=.6)
    assert (objekt["hoehe_m"], objekt["skala"], objekt["drehung_grad"]) == (0.0, 1.0, 0.0)
    assert w.document.undo.count() == 1 and not w.document.undo.isClean() and w.windowTitle().startswith("Prüfrunde *")
    assert w.document.revision == revision and w.document.env_revision == 1        # Vorschau/Fahrlinie bleiben gültig
    assert w.document.env_selected == {"o1"} and "1 Objekte" in w.env_panel.count_label.text()
    click_map(w, 150, 140)
    assert len(w.document.env_objects) == 2 and w.document.undo.count() == 2
    w.document.undo.undo()
    assert [o["id"] for o in w.document.env_objects] == ["o1"] and w.document.env_selected <= {"o1"}
    w.document.undo.undo()
    assert w.document.project.umgebung is None and w.document.undo.isClean()
    w.document.undo.redo()
    w.document.undo.redo()
    assert len(w.document.env_objects) == 2
    w.document.project.save(tmp_path / "proj")                                  # Speichern wie der Menüpunkt
    w.document.undo.setClean()
    assert read_json(tmp_path / "proj" / "umgebung.json")["objekte"][1]["id"] == "o2"
    w.document.undo.undo()                                                       # nach dem Speichern wieder „ungespeichert“
    assert not w.document.undo.isClean()


def test_ebene_ein_ausblenden_und_zeichnen(env_window):
    w = env_window
    for i in range(5):
        w.document.env_place(BAUM if i % 2 else HAUS, 40 * i, 60)
    w.map.fit_track()
    w.map.viewport().update()
    bild = w.map.grab().toImage()
    w.apply_feature("umgebung", False)
    assert w.map.show_env is False and w.map.grab().toImage() != bild
    assert w.map.env_nearest(pixel(w, 0, 60)) is None                              # ausgeblendet: nicht treffbar
    w.apply_feature("umgebung", True)
    assert w.map.show_env is True and w.map.grab().toImage() == bild
    # Radius in Katalogeinheiten (Haus 20 m, Baum 4,5 m, unbekanntes Modell 3 m) mal Skala
    assert w.map.env_radius_m({"modell": HAUS, "skala": 1.5}) == pytest.approx(30)
    assert w.map.env_radius_m({"modell": "eaid://unbekannt", "skala": 1}) == umgebung.UNBEKANNT_RADIUS_M


# ------------------------------------------------------------------ Auswahl, Verschieben, Entfernen
def test_auswahl_verschieben_rahmen_und_entfernen_mit_der_maus(env_window):
    w = env_window
    for x, z in ((100, 100), (130, 100), (160, 100), (100, 160)):
        w.document.env_place(BAUM, x, z)
    w.document.env_select(set())
    w.document.undo.clear()
    w.set_mode("umgebung")
    w.map.fit_track()
    click_map(w, 100, 100)
    assert w.document.env_selected == {"o1"}
    click_map(w, 130, 100, Qt.ControlModifier)
    assert w.document.env_selected == {"o1", "o2"}
    click_map(w, 130, 100, Qt.ControlModifier)
    assert w.document.env_selected == {"o1"}
    click_map(w, 160, 100, Qt.ShiftModifier)
    assert w.document.env_selected == {"o1", "o3"}
    # Ziehen eines gewählten Objekts verschiebt die ganze Gruppe als ein Undo-Schritt
    before = {o["id"]: (o["x"], o["z"]) for o in w.document.env_objects}
    start = w.map.mapToScene(pixel(w, 100, 100)), w.map.mapToScene(pixel(w, 100, 100) + QPoint(40, 30))
    drag_map(w, (100, 100), (100, 100))                                              # Klick ohne Zug ändert nichts
    w.document.env_select({"o1", "o3"}, "o1")
    start_px = pixel(w, 100, 100)
    QTest.mousePress(w.map.viewport(), Qt.LeftButton, Qt.NoModifier, start_px)
    QTest.mouseMove(w.map.viewport(), start_px + QPoint(20, 15), delay=10)
    QTest.mouseMove(w.map.viewport(), start_px + QPoint(40, 30), delay=10)
    assert w.document.undo.count() == 0                                              # Entwurf, noch kein Schritt
    QTest.mouseRelease(w.map.viewport(), Qt.LeftButton, Qt.NoModifier, start_px + QPoint(40, 30))
    dx, dz = (start[1] - start[0]).x(), (start[1] - start[0]).y()
    assert w.document.undo.count() == 1
    after = {o["id"]: (o["x"], o["z"]) for o in w.document.env_objects}
    for kennung in ("o1", "o3"):
        assert after[kennung][0] == pytest.approx(before[kennung][0] + dx, abs=.02)
        assert after[kennung][1] == pytest.approx(before[kennung][1] + dz, abs=.02)
    assert after["o2"] == before["o2"] and after["o4"] == before["o4"]
    w.document.undo.undo()
    assert {o["id"]: (o["x"], o["z"]) for o in w.document.env_objects} == before
    # Rahmen (Strg+A auf der Karte: alle), Entf entfernt die Auswahl in einem Schritt
    drag_map(w, (80, 80), (180, 120))
    assert w.document.env_selected == {"o1", "o2", "o3"}
    QTest.keyClick(w.map, Qt.Key_Delete)
    assert [o["id"] for o in w.document.env_objects] == ["o4"] and w.document.undo.count() == 1   # (Verschiebung zurückgenommen)
    w.document.undo.undo()
    assert len(w.document.env_objects) == 4
    w.document.env_select({"o1", "o2", "o3"}, "o1")
    # Pfeiltasten: 0,1 m / 1 m
    QTest.keyClick(w.map, Qt.Key_Right)
    assert w.document.env_object("o1")["x"] == pytest.approx(before["o1"][0] + .1, abs=1e-6)
    QTest.keyClick(w.map, Qt.Key_Down, Qt.ShiftModifier)
    assert w.document.env_object("o1")["z"] == pytest.approx(before["o1"][1] + 1, abs=1e-6)


def test_esc_waehlt_modell_ab_und_zurueck_zur_auswahl(env_window):
    w = env_window
    choose_model(w, BAUM)
    assert w.map.env_model == BAUM
    QTest.keyClick(w.map, Qt.Key_Escape)
    assert w.map.env_model is None and w.env_panel.armed_model() is None and w.map.mode == "umgebung"
    click_map(w, 100, 100)                                                           # nichts gewählt: kein Objekt entsteht
    assert w.document.env_objects == []
    QTest.keyClick(w.map, Qt.Key_Escape)
    assert w.map.mode == "auswahl"
    QTest.keyClick(w.map, Qt.Key_U)
    assert w.map.mode == "umgebung"
    QTest.keyClick(w.map, Qt.Key_B)
    assert w.map.mode == "streuen"


# ------------------------------------------------------------------ Katalog
def test_katalogliste_suche_kategorie_und_modelleigenschaften(env_window, monkeypatch, tmp_path):
    w = env_window
    stille_arbeiter(monkeypatch)
    panel = w.env_panel
    names = lambda: [panel.list.item(r).data(Qt.UserRole) for r in range(panel.list.count())]
    assert set(names()) == {BAUM, BAUM_B, BUSCH, HAUS} and "4 Modelle" in panel.status.text()
    QTest.keyClicks(panel.search, "bush")
    assert names() == [BUSCH]
    panel.search.clear()
    panel.category.setCurrentIndex(panel.category.findData("baum"))
    assert set(names()) == {BAUM, BAUM_B}
    choose_model(w, BAUM)
    text = panel.details.text()
    assert "st_oak_a_05" in text and "9,0 × 9,0 × 14,0 m" in text and "schon im Trägerpaket" in text
    # Vorschaubild: gleiche Anfrage wird beim Wählen gestellt
    anfragen = []
    panel.preview_requested.connect(anfragen.append)
    choose_model(w, BAUM_B)
    wait_until(lambda: anfragen == [BAUM_B], limit=2000)
    from PySide6.QtGui import QImage
    bild = QImage(60, 60, QImage.Format_ARGB32)
    bild.fill(0xFF336699)
    datei = tmp_path / "silhouette.png"
    bild.save(str(datei))
    panel.set_preview(BAUM_B, datei)
    assert not panel.preview.pixmap().isNull()
    panel.set_preview(BAUM, datei)                                                   # veraltete Antwort wird verworfen
    # Eigenschaften der Auswahl
    w.document.env_place(BAUM, 100, 100)
    for widget, wert in ((panel.height_box, 1.5), (panel.rotation_box, 45), (panel.scale_box, 2)):
        widget.setValue(wert)
        widget.editingFinished.emit()
    panel.group.setText("Reihe A")
    panel.group.editingFinished.emit()
    objekt = w.document.env_object("o1")
    assert (objekt["hoehe_m"], objekt["drehung_grad"], objekt["skala"], objekt["gruppe"]) == (1.5, 45.0, 2.0, "Reihe A")
    assert w.document.undo.count() == 5                                              # Platzieren + vier Änderungen, je ein Schritt
    panel.scale_box.setValue(99)
    panel.scale_box.editingFinished.emit()
    assert w.document.env_object("o1")["skala"] == umgebung.SKALA_MAX                # begrenzt (0,2 … 5)
    panel.ground_cover.setChecked(False)
    assert w.document.project.umgebung["bodenbewuchs"] is False
    w.document.undo.undo()
    assert w.document.project.umgebung["bodenbewuchs"] is True and panel.ground_cover.isChecked()


def test_katalog_wird_im_arbeitsprozess_geladen(app, point_project, monkeypatch):
    """Der Katalog kommt über einen Arbeitsprozess (hier eine Attrappe), die Oberfläche bleibt frei."""
    monkeypatch.setattr(window_module, "worker_command", lambda: [
        sys.executable, "-B", "-c", "import sys,json;r=json.loads(sys.stdin.read());"
        "print(json.dumps(dict(" + repr(KATALOG) + ", gefragt=r['task'], neu=r['neu'])))"])
    w = MainWindow(point_project, auto_preview=False)
    w.interactive = True
    w.show()
    assert w.env_catalog is None
    w.results.setCurrentWidget(w.env_panel)                                          # erster Blick auf den Reiter lädt ihn
    assert w.env_job is not None and "geladen" in w.env_panel.status.text()
    assert not w.env_panel.reload_button.isEnabled()
    wait_until(lambda: w.env_job is None)
    assert w.env_catalog["gefragt"] == "umgebung_katalog" and w.env_catalog["neu"] is False
    assert w.env_panel.list.count() == 4 and w.map.env_index[BAUM]["radius_m"] == 4.5
    w.env_panel.reload_button.click()
    wait_until(lambda: w.env_job is None and w.env_catalog["neu"] is True)
    w.document.undo.setClean()
    w.close()


def test_sprachwechsel_behaelt_katalog_und_objekte(env_window):
    w = env_window
    w.document.env_place(BAUM, 100, 100)
    w.map.env_model = None
    try:
        w.change_language("en")
        assert w.results.tabText(w.results.indexOf(w.env_panel)) == "Environment"
        assert w.env_panel.list.count() == 4 and len(w.document.env_objects) == 1 and w.map.env_index
        assert w.env_panel.ground_cover.text().startswith("Ground cover")
    finally:
        w.change_language("de")


# ------------------------------------------------------------------ Streuen (reine Logik)
def gerade_strecke():
    """Gerade Strecke entlang x (z = 0): Fahrbahn ±7 m, Mauer bei ±22 m."""
    x = np.linspace(-500, 500, 400)
    pos = np.column_stack([x, np.zeros_like(x)])
    return umgebung.Hindernis(pos, np.column_stack([np.zeros_like(x), -np.ones_like(x)]), np.full_like(x, 22), np.full_like(x, 22))


def test_hindernis_abstand_negativ_auf_der_strecke():
    h = gerade_strecke()
    d = h.abstand([[0, 0], [0, 21], [0, 22], [0, 40], [0, -37], [100, 60]])
    assert d[0] < 0 and d[1] < 0 and d[2] == pytest.approx(0) and d[3] == pytest.approx(18) and d[4] == pytest.approx(15)
    assert d[5] == pytest.approx(38)


def test_streuen_dichte_abstaende_und_wiederholbarkeit():
    gebiet = umgebung.Gebiet("rechteck", rechteck=(-200, 100, -100, 200))          # 1 ha, weit weg von der Strecke
    kw = dict(hindernis=gerade_strecke(), abstand_objekte=4.0)
    a, info = umgebung.streuen(gebiet, [BAUM, BUSCH], 40, 7, **kw)
    assert len(a) == info["gewuenscht"] == 40 and info["flaeche_ha"] == pytest.approx(1.0)
    assert {o["modell"] for o in a} == {BAUM, BUSCH}                                  # Mischung
    xy = np.array([(o["x"], o["z"]) for o in a])
    assert xy[:, 0].min() >= -200 and xy[:, 0].max() <= -100 and xy[:, 1].min() >= 100 and xy[:, 1].max() <= 200
    abstaende = np.sqrt(((xy[:, None] - xy[None]) ** 2).sum(-1)) + np.eye(len(xy)) * 1e9
    assert abstaende.min() >= 4.0 - 1e-6
    assert a == umgebung.streuen(gebiet, [BAUM, BUSCH], 40, 7, **kw)[0]             # gleicher Saatwert: gleiche Streuung
    assert a != umgebung.streuen(gebiet, [BAUM, BUSCH], 40, 8, **kw)[0]
    assert all(0.8 <= o["skala"] <= 1.2 and 0 <= o["drehung_grad"] < 360 for o in a)
    assert len({o["drehung_grad"] for o in a}) > 10
    gleich, _ = umgebung.streuen(gebiet, [BAUM], 40, 7, drehung_zufall=False, skala=(1, 1), **kw)
    assert {o["drehung_grad"] for o in gleich} == {0.0} and {o["skala"] for o in gleich} == {1.0}
    # Kennungen laufen hinter den vorhandenen weiter, Mindestabstand gilt auch zu vorhandenen Objekten
    vorhanden = [umgebung.neues_objekt(set(), BAUM, -150, 150)] + [dict(a[0], id="o9")]
    neu, _ = umgebung.streuen(gebiet, [BAUM], 40, 7, vorhanden=vorhanden, gruppe="G", **kw)
    assert neu[0]["id"] == "o10" and all(o["gruppe"] == "G" for o in neu)
    assert min(math.dist((o["x"], o["z"]), (-150, 150)) for o in neu) >= 4.0 - 1e-6


def test_streuen_haelt_abstand_zur_fahrbahn_und_zum_weltbereich():
    h = gerade_strecke()
    gebiet = umgebung.Gebiet("rechteck", rechteck=(-300, -100, 300, 100))           # überdeckt die Strecke
    objekte, info = umgebung.streuen(gebiet, [BAUM], 100, 3, hindernis=h, abstand_fahrbahn=15.0, abstand_objekte=2.0)
    assert objekte and info["verworfen_strecke"] > 0
    assert all(abs(o["z"]) >= 22 + 15 - .01 for o in objekte)                         # 15 m hinter der Mauer (22 m)
    eng, _ = umgebung.streuen(gebiet, [BAUM], 100, 3, hindernis=h, abstand_fahrbahn=0.0, abstand_objekte=2.0)
    assert min(abs(o["z"]) for o in eng) >= 22 - .01 and min(abs(o["z"]) for o in eng) < 37
    # Weltbereich: mit Versatz −591 liegen Welt-x −920 … 1128 bei Definition-x −329 … 1719
    rand = umgebung.Gebiet("rechteck", rechteck=(-400, 300, -300, 400))
    objekte, info = umgebung.streuen(rand, [BAUM], 100, 1, versatz=(-591, 0, -473))
    assert objekte and all(o["x"] + (-591) >= -920 for o in objekte) and info["verworfen_welt"] > 0


def test_streuen_pinsel_fehler_und_grenzen():
    pinsel = umgebung.Gebiet("pinsel", punkte=[(0, 100), (40, 100), (80, 100)], radius=15)    # grober Strich: wird aufgefüllt
    assert pinsel.flaeche_m2() == pytest.approx(math.pi * 15 ** 2 + 80 * 30, rel=.08)
    objekte, _ = umgebung.streuen(pinsel, [BAUM], 200, 5)
    assert objekte and all(min(math.dist((o["x"], o["z"]), p) for p in pinsel.punkte) <= 15 + 1e-6 for o in objekte)
    with pytest.raises(umgebung.StreuFehler) as fehler:
        umgebung.streuen(umgebung.Gebiet("rechteck", rechteck=(0, 0, 3000, 3000)), [BAUM], 400, 1)
    assert fehler.value.code == "zu_viele"
    with pytest.raises(umgebung.StreuFehler) as fehler:
        umgebung.streuen(pinsel, [], 10, 1)
    assert fehler.value.code == "keine_modelle"
    assert umgebung.streuen(pinsel, [BAUM], 0, 1)[0] == []


# ------------------------------------------------------------------ Streuen mit der Maus (echte Vorschau), Gruppe
def stille_arbeiter(monkeypatch, issues=()):
    """Alle Umgebungs-Arbeitsprozesse antworten mit einer festen Prüfung (kein echter Werkzeugkasten nötig)."""
    antwort = json.dumps({"issues": list(issues)})
    monkeypatch.setattr(window_module, "worker_command", lambda: [
        sys.executable, "-B", "-c", "import sys;sys.stdin.read();print(" + repr(antwort) + ")"])


def test_bepflanzen_mit_maus_gruppe_abstand_und_loeschen(journey, monkeypatch):
    w = journey()
    stille_arbeiter(monkeypatch)
    w.document.project.manifest["versatz"] = [-591, 0, -473]                           # wie die Beispielstrecke im Spiel
    w.set_env_catalog(KATALOG)
    panel = w.env_panel
    choose_model(w, BAUM, BUSCH)
    panel.density.setValue(150)
    panel.seed.setValue(11)
    w.set_mode("streuen")
    assert w.map.mode == "streuen" and w.results.currentWidget() is panel
    before = copy.deepcopy(w.document.project.raw)
    pos = w.preview["pos"]
    # Rechteck quer über die Strecke ziehen (Mitte = ein Punkt der Mittellinie)
    p, q = pos[len(pos) // 4], pos[3 * len(pos) // 4]
    a, b = (p[0] - 90, p[2] - 90), (p[0] + 90, p[2] + 90)
    count = w.document.undo.count()
    drag_map(w, a, b, steps=5)
    objekte = w.document.env_objects
    assert objekte and w.document.undo.count() == count + 1                          # eine Streuung = ein Undo-Schritt
    assert {o["gruppe"] for o in objekte} == {"Streuung 1"} and w.document.env_selected == {o["id"] for o in objekte}
    assert {o["modell"] for o in objekte} <= {BAUM, BUSCH} and len({o["modell"] for o in objekte}) == 2
    assert w.document.project.raw == before                                          # Streckendefinition unberührt
    # Mindestabstand 15 m hinter der Mauer, aus der Vorschaugeometrie
    hindernis = umgebung.Hindernis.aus_vorschau(w.preview)
    abstaende = hindernis.abstand([(o["x"], o["z"]) for o in objekte])
    assert abstaende.min() >= 15.0 - 1e-6
    # zweite Streuung = eigene Gruppe; Gruppe über Doppelklick wählen, gemeinsam löschen
    panel.seed.setValue(12)
    c, d = (q[0] - 90, q[2] - 90), (q[0] + 90, q[2] + 90)
    drag_map(w, c, d, steps=5)
    gruppen = umgebung.gruppen(w.document.env_objects)
    assert gruppen == ["Streuung 1", "Streuung 2"]
    zweite = {o["id"] for o in w.document.env_objects if o["gruppe"] == "Streuung 2"}
    assert zweite and w.document.env_selected == zweite
    w.document.env_select(set())
    w.set_mode("umgebung")
    ziel = next(o for o in w.document.env_objects if o["gruppe"] == "Streuung 1")
    QTest.mouseDClick(w.map.viewport(), Qt.LeftButton, Qt.NoModifier, pixel(w, ziel["x"], ziel["z"]))
    erste = {o["id"] for o in w.document.env_objects if o["gruppe"] == "Streuung 1"}
    assert w.document.env_selected == erste
    anzahl = len(w.document.env_objects)
    QTest.mouseClick(panel.group_delete, Qt.LeftButton)
    assert {o["id"] for o in w.document.env_objects} == zweite
    w.document.undo.undo()
    assert len(w.document.env_objects) == anzahl
    # „Gruppe wählen“ im Reiter
    w.document.env_select({ziel["id"]}, ziel["id"])
    QTest.mouseClick(panel.group_button, Qt.LeftButton)
    assert w.document.env_selected == erste


def test_bepflanzen_pinsel_und_fehlermeldungen(env_window):
    w = env_window
    w.map.fit_track()
    w.set_mode("streuen")
    drag_map(w, (100, 100), (150, 130))
    assert w.document.env_objects == [] and "Modelle wählen" in w.status_label.text()
    choose_model(w, BAUM)
    w.set_mode("streuen")
    w.env_panel.form_choice.setCurrentIndex(w.env_panel.form_choice.findData("pinsel"))
    w.env_panel.radius.setValue(25)
    assert w.map.env_form == "pinsel" and w.map.env_radius == 25
    w.env_panel.density.setValue(300)
    drag_map(w, (80, 100), (140, 100), steps=6)
    objekte = w.document.env_objects
    assert len(objekte) > 5 and all(o["z"] == pytest.approx(100, abs=26) for o in objekte)
    w.env_panel.density.setValue(2000)
    w.env_panel.form_choice.setCurrentIndex(w.env_panel.form_choice.findData("rechteck"))
    w.env_panel.density.setValue(2000)
    drag_map(w, (-1000, -1000), (1000, 1000))
    assert "Zu viele Objekte" in w.status_label.text() and len(w.document.env_objects) == len(objekte)


# ------------------------------------------------------------------ Meldungen und Sprung
def test_umgebungsmeldungen_in_der_pruefliste_und_sprung_zum_objekt(env_window, monkeypatch):
    w = env_window
    w.document.env_place(BAUM, 100, 100)
    w.document.env_place(BUSCH, 150, 150)
    meldung = {"id": "UMG_AUF_FAHRBAHN", "kurz": "UM-04", "schwere": "warnung", "stufe": "eingabe",
               "text": "Objekt o2 steht auf der Fahrbahn", "ort": {"welt": [-441, 0, -323], "objekt": "o2"}}
    stille = json.dumps({"issues": [dict(meldung, level="Warnung")]})
    monkeypatch.setattr(window_module, "worker_command", lambda: [
        sys.executable, "-B", "-c", "import sys,json;r=json.loads(sys.stdin.read());assert r['task']=='umgebung_pruefen';"
        "assert len(r['umgebung']['objekte'])==2;print(" + repr(stille) + ")"])
    w.env_check_timer.stop()
    w.request_env_check()
    wait_until(lambda: w.env_check_job is None and w.env_issues)
    texte = [w.issues.item(i).text() for i in range(w.issues.count())]
    zeile = next(i for i, t in enumerate(texte) if "UM-04" in t)
    assert "Objekt o2" in texte[zeile] and texte[zeile].startswith("Warnung")
    w.document.env_select({"o1"})
    w.map.centerOn(0, 0)
    item = w.issues.item(zeile)
    QTest.mouseClick(w.issues.viewport(), Qt.LeftButton, pos=w.issues.visualItemRect(item).center())
    assert w.document.env_selected == {"o2"}
    mitte = w.map.mapToScene(w.map.viewport().rect().center())
    assert math.dist((mitte.x(), mitte.y()), (150, 150)) < 3                         # Karte springt zum Objekt
    # Prüfung kommt auch ohne Objekte aus der Liste: Meldungen verschwinden
    w.document.undo.undo()
    w.document.undo.undo()
    w.env_check_timer.stop()
    w.request_env_check()
    assert w.env_issues == []
    # Eine Änderung stößt die Prüfung selbst an (700 ms Ruhe)
    w.document.env_place(BAUM, 100, 100)
    assert w.env_check_timer.isActive()


def test_kaputte_umgebungsdatei_ist_ein_fehler_und_nur_lesend(tmp_path, app, point_project):
    ordner = tmp_path / "p"
    Project(point_project.raw, point_project.manifest).save(ordner)
    (ordner / "umgebung.json").write_text('{"schema": 7}', encoding="utf-8")
    w = MainWindow(Project.open(ordner), auto_preview=False)
    w.show()
    w.set_env_catalog(KATALOG)
    w.render_issues()
    assert any("UM-01" in w.issues.item(i).text() and w.issues.item(i).text().startswith("Fehler") for i in range(w.issues.count()))
    assert not w.document.env_editable
    w.document.env_place(BAUM, 1, 1)
    assert w.document.env_objects == []                                              # nichts geschrieben
    assert w.document.undo.count() == 0
    w.close()


# ------------------------------------------------------------------ Wiederherstellung und Bau
def test_autosicherung_enthaelt_die_umgebung(env_window, tmp_path):
    w = env_window
    w.document.project.save(tmp_path / "p")
    w.document.undo.setClean()
    w.document.env_place(BAUM, 100, 100)
    assert w.autosave() is True
    gesichert = json.loads(w.recovery_path().read_text(encoding="utf-8"))
    assert gesichert["umgebung"]["objekte"][0]["modell"] == BAUM
    w.document.undo.undo()
    assert w.offer_recovery(ask=lambda text: True) is True                           # Wiederherstellung bringt sie zurück
    assert [o["modell"] for o in w.document.env_objects] == [BAUM]


def test_bauaufruf_mit_umgebung(builder, tmp_path):
    w, d, mode = builder
    w.document.env_place(BAUM, 10, 20)
    w.cancel_preview()
    click_build(d)
    wait_until(lambda: d.job is None)
    assert "--umgebung" in d.command
    datei = d.command[d.command.index("--umgebung") + 1]
    assert datei.endswith("umgebung.json") and read_json(datei)["objekte"][0]["modell"] == BAUM   # gespeichert vor dem Bau
    # ohne Umgebung kein Schalter
    kein = build_command(ROOT / "ego-werkzeuge", tmp_path / "x", tmp_path / "a", tmp_path / "b")
    assert "--umgebung" not in kein
    mit = build_command(ROOT / "ego-werkzeuge", tmp_path / "x", tmp_path / "a2", tmp_path / "b", umgebung=tmp_path / "u.json")
    assert mit[-2:] == ["--umgebung", str((tmp_path / "u.json").resolve())]


# ------------------------------------------------------------------ gegen das echte Modul des Werkzeugkastens
ECHT = (ROOT / "ego-werkzeuge" / "strecke" / "umgebung.py").is_file()


@pytest.mark.skipif(not ECHT, reason="strecke/umgebung.py (AP-U1) fehlt noch")
def test_echtes_werkzeug_liest_unsere_datei_und_prueft(tmp_path, point_project):
    daten = umgebung.neu()
    daten["objekte"] += [umgebung.neues_objekt(set(), BAUM, 10, 20, gruppe="G"),
                         umgebung.neues_objekt({"o1"}, "eaid://gibt/es/nicht", 5000, 5000)]
    datei = tmp_path / "umgebung.json"
    umgebung_speichern(daten, datei)
    assert umgebung_laden(datei) == daten
    sys.path.insert(0, str(ROOT / "ego-werkzeuge"))
    from strecke import umgebung as werkzeug
    assert werkzeug.laden(str(datei))["objekte"][0]["id"] == "o1"
    ergebnis = calculate_env_check({"umgebung": daten, "raw": Project.open(EXAMPLES / "teststrecke.json").raw,
                                    "basis": str(EXAMPLES), "shift": [-591, 0, -473]})
    kennungen = {i["id"] for i in ergebnis["issues"]}
    assert "UMG_MODELL_UNBEKANNT" in kennungen or "UMG_AUSSERHALB" in kennungen
    assert all(i["level"] in ("Fehler", "Warnung", "Hinweis") for i in ergebnis["issues"])


# ------------------------------------------------------------------ Bild für die Doku
def test_bildschirmfoto_umgebungsebene(journey, monkeypatch):
    """Karte mit Umgebungsebene und Reiter „Umgebung“ (echte Vorschau). Mit ``MAPEDITOR_SCREENSHOTS=1`` landet das
    Bild in ``docs/bilder/umgebung_ebene.png``; mit ``MAPEDITOR_ECHTER_KATALOG=1`` kommt der Katalog aus dem Spiel
    (lesend), sonst die Attrappe."""
    from PySide6.QtGui import QTransform
    w = journey()
    w.document.project.manifest["versatz"] = [-591, 0, -473]
    katalog = KATALOG
    echt = os.environ.get("MAPEDITOR_ECHTER_KATALOG") == "1"
    if echt:
        from mapeditor.adapter import umgebung_katalog
        katalog = umgebung_katalog()
    else:
        stille_arbeiter(monkeypatch)
    w.set_env_catalog(katalog)
    modelle = [m["modell"] for m in katalog["modelle"]]
    baeume = [m["modell"] for m in katalog["modelle"] if m["kategorie"] == "baum" and (m.get("radius_m") or 0) < 9][:3]
    buesche = [m["modell"] for m in katalog["modelle"] if m["kategorie"] == "busch" and (m.get("radius_m") or 0) < 6][:2]
    haeuser = [m["modell"] for m in katalog["modelle"] if m["kategorie"] in ("gebaeude", "zelt") and (m.get("radius_m") or 0) < 25][:2]
    assert baeume and buesche and haeuser and set(baeume + buesche + haeuser) <= set(modelle)
    pos = w.preview["pos"]
    p = pos[len(pos) // 4]
    w.resize(1500, 920)
    QTest.qWait(30)
    w.map.setTransform(QTransform.fromScale(1.3, 1.3))
    w.map.centerOn(p[0], p[2])
    QTest.qWait(30)
    choose_model(w, *baeume)
    w.env_panel.density.setValue(55)
    w.env_panel.seed.setValue(5)
    w.set_mode("streuen")
    drag_map(w, (p[0] - 150, p[2] - 110), (p[0] + 150, p[2] + 110), steps=6)
    choose_model(w, *buesche)
    w.env_panel.density.setValue(90)
    w.env_panel.track_distance.setValue(8)
    w.env_panel.seed.setValue(9)
    w.set_mode("streuen")
    drag_map(w, (p[0] - 150, p[2] - 20), (p[0] + 150, p[2] + 110), steps=6)
    anzahl = len(w.document.env_objects)
    w.env_panel.search.clear()
    choose_model(w, haeuser[0])
    w.set_mode("umgebung")
    haus = [w.document.env_place(haeuser[0], p[0] + dx, p[2] + dz)["id"] for dx, dz in ((-60, -95), (-5, -100), (50, -92))]
    w.document.env_select(set(haus[:2]), haus[0])
    assert len(w.document.env_objects) > anzahl > 20
    w.results.setCurrentWidget(w.env_panel)
    w.set_mode("umgebung")
    w.env_panel.list.scrollToTop()
    if echt:                                                                        # echte Silhouette abwarten
        wait_until(lambda: w.env_preview_job is None and w.env_check_job is None and w.env_panel.preview.pixmap() is not None
                   and not w.env_panel.preview.pixmap().isNull(), limit=20000)
    QTest.qWait(60)
    bild = w.grab()
    assert not bild.isNull()
    if os.environ.get("MAPEDITOR_SCREENSHOTS") == "1":
        ziel = ROOT / "Mapeditor" / "docs" / "bilder"
        ziel.mkdir(parents=True, exist_ok=True)
        assert bild.save(str(ziel / "umgebung_ebene.png"))


# ------------------------------------------------------------------ Gelände: Hügel und Berge (29.10.2026)
def welt_zu_definition(w, wx, wz):
    shift = w.document.project.manifest.get("versatz", [0, 0, 0])
    return wx - shift[0], wz - shift[2]


def test_gelaende_datei_reihenfolge_kennungen_und_lage():
    daten = umgebung.lade_text(json.dumps({"schema": 1, "objekte": [], "gelaende": {
        "berge": [{"radius_m": 450, "hoehe_m": 400, "x": 2000, "z": 0, "id": "b1"}],
        "huegel": [{"id": "h1", "x": 0, "z": 0, "hoehe_m": -20, "radius_m": 100, "notiz": "bleibt"}]}}))
    text = umgebung.als_text(daten)
    js = json.loads(text)
    assert list(js) == ["schema", "bodenbewuchs", "gelaende", "objekte"] and list(js["gelaende"]) == ["huegel", "berge"]
    assert list(js["gelaende"]["berge"][0]) == ["id", "x", "z", "hoehe_m", "radius_m"]
    assert js["gelaende"]["huegel"][0]["notiz"] == "bleibt"
    assert umgebung.alle_ids(daten) == {"h1", "b1"}
    assert umgebung.neue_form({"h1", "b1"}, "huegel", 1, 2)["id"] == "h2"
    assert umgebung.neue_form(set(), "berge", 1, 2, hoehe_m=99999)["hoehe_m"] == 2000.0
    for kaputt in ({"gelaende": {"huegel": [{"id": "h", "x": 0, "z": 0, "hoehe_m": 500, "radius_m": 100}]}},
                   {"objekte": [{"id": "h", "modell": BAUM, "x": 0, "z": 0}],
                    "gelaende": {"huegel": [{"id": "h", "x": 0, "z": 0, "hoehe_m": 5, "radius_m": 100}]}}):
        with pytest.raises(ValueError):
            umgebung.normalisieren(kaputt)
    assert umgebung.form_problem("huegel", {"x": 0, "z": 0, "radius_m": 100}) is None
    assert umgebung.form_problem("huegel", {"x": 2000, "z": 0, "radius_m": 100})
    assert umgebung.form_problem("berge", {"x": 0, "z": 0, "radius_m": 400})                    # im Streckenbereich
    assert umgebung.form_problem("berge", {"x": 1700, "z": 0, "radius_m": 450}) is None
    assert umgebung.form_problem("berge", {"x": 1200, "z": 0, "radius_m": 600})                 # wird abgeschnitten


def test_gelaende_setzen_ziehen_aendern_loeschen(env_window):
    w = env_window
    panel = w.env_panel
    w.results.setCurrentWidget(panel)
    hx, hz = welt_zu_definition(w, 100, 0)
    w.map.centerOn(hx, hz)
    QTest.mouseClick(panel.hill_button, Qt.LeftButton)
    assert w.map.mode == "umgebung" and w.map.terrain_armed == "huegel"
    panel.terrain_height.setValue(80)
    panel.terrain_radius.setValue(150)
    click_map(w, hx, hz)
    formen = w.document.terrain_forms
    assert len(formen) == 1 and formen[0][0] == "huegel"
    form = formen[0][1]
    assert form["id"] == "h1" and (form["hoehe_m"], form["radius_m"]) == (80.0, 150.0)
    assert form["x"] == pytest.approx(hx, abs=1) and w.document.terrain_selected == "h1"
    assert w.map.terrain_armed is None and not panel.hill_button.isChecked()       # ein Klick, eine Form
    assert "h1" in panel.terrain_text.text() and panel.terrain_delete.isEnabled()
    drag_map(w, (hx, hz), (hx + 40, hz - 20))                                        # Mitte ziehen
    form = w.document.terrain_form("h1")
    assert form["x"] == pytest.approx(hx + 40, abs=1.5) and form["z"] == pytest.approx(hz - 20, abs=1.5)
    panel.terrain_height.setValue(-30)                                               # Senke
    assert w.document.terrain_form("h1")["hoehe_m"] == -30.0
    click_map(w, hx + 300, hz + 300)                                                 # leer: Auswahl weg
    assert w.document.terrain_selected is None
    click_map(w, hx + 40, hz - 20)
    assert w.document.terrain_selected == "h1"
    w.map.setFocus()
    QTest.keyClick(w.map.viewport(), Qt.Key_Delete)
    assert w.document.terrain_forms == []
    w.document.undo.undo()
    assert w.document.terrain_form("h1")["hoehe_m"] == -30.0
    # Berg außerhalb: herauszoomen, setzen, Lage ohne Meldung; im Streckenbereich mit Warnzeichen
    bx, bz = welt_zu_definition(w, 1128 + 700, 16)
    w.map.resetTransform()
    w.map.scale(0.08, 0.08)
    w.map.centerOn(bx, bz)
    QTest.mouseClick(panel.mountain_button, Qt.LeftButton)
    assert panel.terrain_height.value() == 400 and panel.terrain_radius.value() == 450
    click_map(w, bx, bz)
    art, berg = w.document.terrain_forms[-1]
    assert art == "berge" and berg["id"] == "b1" and "⚠" not in panel.terrain_text.text()
    shift = w.document.project.manifest.get("versatz", [0, 0, 0])
    assert umgebung.form_problem("berge", berg, shift) is None
    w.document.terrain_set("b1", x=hx, z=hz)
    assert "⚠" in panel.terrain_text.text()
    assert json.loads(umgebung.als_text(w.document.env))["gelaende"]["berge"][0]["id"] == "b1"


def test_gelaende_escape_und_modellwahl_heben_knopf_auf(env_window):
    w = env_window
    panel = w.env_panel
    w.results.setCurrentWidget(panel)
    QTest.mouseClick(panel.mountain_button, Qt.LeftButton)
    assert w.map.terrain_armed == "berge"
    w.map.setFocus()
    QTest.keyClick(w.map.viewport(), Qt.Key_Escape)
    assert w.map.terrain_armed is None and not panel.mountain_button.isChecked()
    QTest.mouseClick(panel.hill_button, Qt.LeftButton)
    choose_model(w, BAUM)
    assert w.map.terrain_armed is None and not panel.hill_button.isChecked() and w.map.env_model == BAUM


@pytest.mark.skipif(not ECHT, reason="strecke/umgebung.py (AP-U1) fehlt noch")
def test_echtes_werkzeug_prueft_gelaende(tmp_path):
    daten = umgebung.neu()
    daten["gelaende"] = {"huegel": [umgebung.neue_form(set(), "huegel", 5000, 0)],
                         "berge": [umgebung.neue_form({"h1"}, "berge", 0, 0)]}
    datei = tmp_path / "umgebung.json"
    umgebung_speichern(daten, datei)
    sys.path.insert(0, str(ROOT / "ego-werkzeuge"))
    from strecke import umgebung as werkzeug
    gelesen = werkzeug.laden(str(datei))
    assert werkzeug.huegel_welt(gelesen, (0, 0, 0)) == [(40.0, 5000.0, 0.0, 200.0)]
    ergebnis = calculate_env_check({"umgebung": daten, "raw": Project.open(EXAMPLES / "teststrecke.json").raw,
                                    "basis": str(EXAMPLES), "shift": [0, 0, 0]})
    kennungen = {(i["id"], (i.get("ort") or {}).get("objekt")) for i in ergebnis["issues"]}
    assert {("UMG_HUEGEL_LAGE", "h1"), ("UMG_BERG_LAGE", "b1")} <= kennungen, kennungen
