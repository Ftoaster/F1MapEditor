"""Schritt F: „Neu aus Vorlage“ und „Vorschlag erzeugen“ – Adapter und Nutzerreisen.

Die Reisen bedienen die echten Menüeinträge, Dialoge (Auswahl, Durchsuchen, Schaltflächen) und prüfen danach
Projekt, Karte, Titel und die echte asynchrone Generatorvorschau. Nur der Dateiauswahldialog des Betriebssystems
und Rückfragen (QMessageBox) werden ersetzt.
"""

import json
import math
import os
from pathlib import Path
import sys

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QFileDialog, QMessageBox

from mapeditor import adapter
from mapeditor.project import Project
from mapeditor.templates import SuggestionDialog, TemplateDialog, guess_code
from mapeditor.window import MainWindow
from test_adapter import wait_until
from test_journeys import click_object, undo

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ego-werkzeuge"))
from ego import spielpfade  # noqa: E402

SPIEL = (Path(spielpfade.finden() or spielpfade.STANDARD) / "2025_asset_groups" / "environment_package" / "tracks" / "monza"
         / "wep" / "monza_common.erp")


# ----------------------------------------------------------------------------- Hilfen
def oval_csv(path, a=700.0, b=380.0, rechts=6.0, links=7.5):
    """TUM-artige CSV: Oval, rund 5 m Abstand, y nach oben, Spalten rechts vor links."""
    umfang = math.pi * (3 * (a + b) - math.sqrt((3 * a + b) * (a + 3 * b)))
    n = int(umfang / 5)
    lines = ["# x_m,y_m,w_tr_right_m,w_tr_left_m"]
    for i in range(n):
        t = 2 * math.pi * i / n
        lines.append(f"{a * math.cos(t):.6f},{b * math.sin(t):.6f},{rechts},{links}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def oval_gpx(path, a=800.0, b=350.0, n=300):
    R, lat0, lon0 = 6378137.0, 48.0, 11.0
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        lat = lat0 + math.degrees(b * math.sin(t) / R)
        lon = lon0 + math.degrees(a * math.cos(t) / (R * math.cos(math.radians(lat0))))
        pts.append(f'<trkpt lat="{lat:.8f}" lon="{lon:.8f}"><ele>{300 + 8 * math.sin(t):.1f}</ele></trkpt>')
    Path(path).write_text('<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>' + "".join(pts)
                          + "</trkseg></trk></gpx>", encoding="utf-8")
    return path


def menu_action(w, text):
    for menu_action_ in w.menuBar().actions():
        for action in menu_action_.menu().actions():
            if action.text() == text:
                return action
    raise AssertionError(f"Menüeintrag fehlt: {text}")


def drive(handler, delay=40):
    """Bedient den nächsten modalen Dialog (Fehler im Handler schließen ihn und werden zurückgegeben)."""
    errors = []

    def run():
        dialog = QApplication.activeModalWidget()
        if dialog is None:
            QTimer.singleShot(30, run)
            return
        try:
            handler(dialog)
        except BaseException as error:  # noqa: BLE001 - in Qt-Callback nicht sichtbar, sonst hängt der Test
            errors.append(error)
            dialog.reject()
    QTimer.singleShot(delay, run)
    return errors


def ok(dialog):
    QTest.mouseClick(dialog.buttons.button(QDialogButtonBox.Ok), Qt.LeftButton)


@pytest.fixture
def editor(app):
    windows = []

    def make(project=None):
        w = MainWindow(project or Project(), auto_preview=False)
        windows.append(w)
        w.show()
        return w
    yield make
    for w in windows:
        w.document.undo.setClean()
        w.close()


def settle(w):
    wait_until(lambda: w.preview is not None and w.job is None and w.preview_revision == w.document.revision)
    QTest.qWait(30)


@pytest.fixture(autouse=True)
def no_real_dialogs(monkeypatch):
    """Ein unerwarteter echter Dialog würde den Test hängen lassen."""
    def unexpected(*args, **kwargs):
        raise AssertionError("Unerwarteter Dialog: " + " ".join(str(a) for a in args[1:3]))
    monkeypatch.setattr(QMessageBox, "warning", unexpected)
    monkeypatch.setattr(QMessageBox, "question", unexpected)


# ----------------------------------------------------------------------------- Adapter
def test_adapter_tum_import_runs_in_worker(tmp_path):
    result = adapter.import_template("tum", oval_csv(tmp_path / "Oval.csv"))
    raw, info = result["raw"], result["info"]
    assert result["manifest"]["versatz"] == [0, 0, 0] and result["manifest"]["runde_geschlossen"] is True
    first = raw["mittellinie"]["punkte"][0]
    assert first["breite_rechts"] == 6.0 and first["breite_links"] == 7.5       # kein Tausch
    assert raw["mittellinie"]["approximation_m"] == 6.0 and info["passt_in_ao"]
    assert abs(first["x"] - 700 + info["verschiebung_m"][0]) < 0.01


def test_adapter_gpx_and_deterministic_suggestion(tmp_path):
    gpx = adapter.import_template("gpx", oval_gpx(tmp_path / "o.gpx"))
    assert 3700 < gpx["info"]["laenge_m"] < 3800
    a, b = adapter.suggest_track(11), adapter.suggest_track(11)
    assert a["raw"] == b["raw"] and a["info"]["seed"] == 11
    assert 4900 < a["info"]["laenge_m"] < 5100 and a["info"]["radius_min_m"] >= 27


def test_adapter_errors_are_messages_not_crashes(tmp_path):
    with pytest.raises(ValueError, match="GPX"):
        adapter.import_template("gpx", tmp_path / "gibt_es_nicht.gpx")
    with pytest.raises(ValueError, match="Vorlagenart"):
        adapter.import_template("kml", tmp_path / "x.kml")
    with pytest.raises(ValueError, match="Länge"):
        adapter.suggest_track(1, length_m=100)


def test_guess_code():
    assert guess_code("a/b/monza_common.erp") == "monza"
    assert guess_code("spa_francorchamps_common.erp") == "spa_francorchamps"
    assert guess_code("portimao.erp") == "portimao"


# ----------------------------------------------------------------------------- Nutzerreisen
def test_journey_new_from_tum_template(editor, tmp_path, monkeypatch):
    csv = oval_csv(tmp_path / "Oval.csv")
    w = editor()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(csv), ""))
    seen = {}

    def handler(dialog):
        assert isinstance(dialog, TemplateDialog) and dialog.kind.currentData() == "tum"
        assert not dialog.code.isVisible() and dialog.spacing.isVisible()
        QTest.mouseClick(dialog.browse_button, Qt.LeftButton)       # echter Klick auf „Durchsuchen …“
        assert dialog.path.text() == str(csv)
        dialog.spacing.setValue(25)
        seen["values"] = dialog.values()
        ok(dialog)
    errors = drive(handler)
    menu_action(w, "Neu aus Vorlage …").trigger()
    assert not errors, errors
    assert seen["values"]["spacing"] == 25 and seen["values"]["kind"] == "tum"
    project = w.document.project
    assert project.raw["name"] == "Vorlage Oval (TUM)" and project.folder is None
    assert 120 < len(project.raw["mittellinie"]["punkte"]) < 200            # ausgedünnt auf ~25 m
    assert " *" in w.windowTitle() and not w.document.undo.isClean()
    assert w.document.revision >= 1
    settle(w)
    assert 3400 < w.preview["length"] < 3550 and not [i for i in w.preview["issues"] if i.get("schwere") == "fehler"]
    assert "Vorlage: " in w.status_label.text() or "Vorlage:" in w.statusBar().currentMessage()


def test_journey_template_edit_undo_and_save_roundtrip(editor, tmp_path, monkeypatch):
    csv = oval_csv(tmp_path / "Oval.csv")
    w = editor()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(csv), ""))

    def handler(dialog):
        QTest.mouseClick(dialog.browse_button, Qt.LeftButton)
        dialog.spacing.setValue(40)
        ok(dialog)
    errors = drive(handler)
    menu_action(w, "Neu aus Vorlage …").trigger()
    assert not errors, errors
    settle(w)
    count = len(w.document.project.raw["mittellinie"]["punkte"])
    w.activateWindow()                                   # nach dem modalen Dialog wieder das aktive Fenster
    QTest.qWait(50)
    click_object(w, 3)
    QTest.keyClick(w.map, Qt.Key_Delete)
    QTest.qWait(30)
    assert len(w.document.project.raw["mittellinie"]["punkte"]) == count - 1
    undo(w)
    assert len(w.document.project.raw["mittellinie"]["punkte"]) == count
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path / "projekt"))
    assert w.save_project(True)
    reopened = Project.open(tmp_path / "projekt")
    assert reopened.raw == w.document.project.raw and "editor_import" in reopened.raw     # verlustfrei, Metadaten bleiben
    assert reopened.manifest["versatz"] == [0, 0, 0]


def test_journey_gpx_template(editor, tmp_path, monkeypatch):
    gpx = oval_gpx(tmp_path / "Runde.gpx")
    w = editor()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(gpx), ""))

    def handler(dialog):
        dialog.kind.setCurrentIndex(1)
        assert not dialog.spacing.isVisible()
        QTest.mouseClick(dialog.browse_button, Qt.LeftButton)
        ok(dialog)
    errors = drive(handler)
    menu_action(w, "Neu aus Vorlage …").trigger()
    assert not errors, errors
    pts = w.document.project.raw["mittellinie"]["punkte"]
    assert w.document.project.raw["name"] == "Vorlage Runde (GPX)" and len(pts) > 600
    assert w.document.project.raw["fahrbahn"]["breite"] == 14.0
    settle(w)
    assert 3700 < w.preview["length"] < 3800


def test_journey_too_large_template_asks_and_shrinks(editor, tmp_path, monkeypatch):
    csv = oval_csv(tmp_path / "Gross.csv", a=1500, b=800)
    w = editor()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(csv), ""))
    asked = []

    def yes(parent, title, text, *rest):
        asked.append((title, text))
        return QMessageBox.Yes
    monkeypatch.setattr(QMessageBox, "question", yes)

    def handler(dialog):
        QTest.mouseClick(dialog.browse_button, Qt.LeftButton)
        dialog.spacing.setValue(30)
        ok(dialog)
    errors = drive(handler)
    menu_action(w, "Neu aus Vorlage …").trigger()
    assert not errors, errors
    assert asked and asked[0][0] == "Vorlage zu groß" and "3000" in asked[0][1]
    info = w.document.project.raw["editor_import"]
    assert info["passt_in_ao"] and info["massstab"] < 0.7
    settle(w)
    assert not [i for i in w.preview["issues"] if i.get("id") == "LAGE_AUSSERHALB_AO"]


def test_journey_too_large_template_declined_keeps_size_and_checker_reports(editor, tmp_path, monkeypatch):
    csv = oval_csv(tmp_path / "Gross.csv", a=1500, b=800)
    w = editor()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(csv), ""))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)

    def handler(dialog):
        QTest.mouseClick(dialog.browse_button, Qt.LeftButton)
        dialog.spacing.setValue(30)
        ok(dialog)
    errors = drive(handler)
    menu_action(w, "Neu aus Vorlage …").trigger()
    assert not errors, errors
    assert w.document.project.raw["editor_import"]["massstab"] == 1.0
    settle(w)
    assert [i for i in w.preview["issues"] if i.get("id") == "LAGE_AUSSERHALB_AO"]


def test_journey_invalid_file_shows_message_and_keeps_project(editor, tmp_path, monkeypatch):
    bad = tmp_path / "kaputt.gpx"
    bad.write_text("das ist kein xml", encoding="utf-8")
    project = Project({"name": "Mein Entwurf", "mittellinie": {"art": "kontrollpunkte", "punkte": []}})
    w = editor(project)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(bad), ""))
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *rest: messages.append(text))

    def handler(dialog):
        dialog.kind.setCurrentIndex(1)
        QTest.mouseClick(dialog.browse_button, Qt.LeftButton)
        ok(dialog)
    errors = drive(handler)
    menu_action(w, "Neu aus Vorlage …").trigger()
    assert not errors, errors
    assert messages and "GPX nicht lesbar" in messages[0]
    assert w.document.project is project and project.raw["name"] == "Mein Entwurf"


def test_journey_missing_file_and_missing_code_are_refused(editor, monkeypatch):
    w = editor()
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *rest: messages.append(text))
    project = w.document.project
    errors = drive(ok)                                       # nichts eingetragen
    menu_action(w, "Neu aus Vorlage …").trigger()
    assert not errors and messages == ["Bitte eine Vorlagendatei wählen."] and w.document.project is project

    def handler(dialog):
        dialog.kind.setCurrentIndex(2)
        dialog.path.setText("C:/irgendwo/strecke.erp")      # ergibt vorgeschlagenen Code „strecke“
        dialog.code.clear()
        ok(dialog)
    errors = drive(handler)
    menu_action(w, "Neu aus Vorlage …").trigger()
    assert not errors and messages[-1].startswith("Bitte den Streckencode")


@pytest.mark.skipif(not SPIEL.exists(), reason="Spielordner nicht vorhanden")
def test_journey_game_track_template_guesses_code_and_reads_only(editor, monkeypatch):
    w = editor()
    before = (SPIEL.stat().st_size, SPIEL.stat().st_mtime)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(SPIEL), ""))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)   # Monza ist größer als ±1024 m

    def handler(dialog):
        dialog.kind.setCurrentIndex(2)
        assert dialog.code.isVisible()
        QTest.mouseClick(dialog.browse_button, Qt.LeftButton)
        assert dialog.code.text() == "monza"
        dialog.spacing.setValue(30)
        ok(dialog)
    errors = drive(handler)
    menu_action(w, "Neu aus Vorlage …").trigger()
    assert not errors, errors
    raw = w.document.project.raw
    assert raw["name"] == "Vorlage monza (Spiel)" and raw["editor_import"]["code"] == "monza"
    assert raw["editor_import"]["massstab"] < 1.0 and len(raw["mittellinie"]["punkte"]) > 100
    settle(w)
    assert 5000 < w.preview["length"] < 5800
    assert before == (SPIEL.stat().st_size, SPIEL.stat().st_mtime)


def test_journey_suggestion_is_reproducible_and_valid(editor, tmp_path):
    w = editor()
    seen = {}

    def handler(dialog):
        assert isinstance(dialog, SuggestionDialog)
        QTest.mouseClick(dialog.dice, Qt.LeftButton)
        würfel = dialog.seed.value()
        assert 1 <= würfel <= 999999
        dialog.seed.setValue(0)
        dialog.seed.clear()
        QTest.keyClicks(dialog.seed, "42")
        QTest.keyClick(dialog.seed, Qt.Key_Tab)
        dialog.length.setValue(4500)
        dialog.curves.setValue(10)
        dialog.radius.setValue(35)
        seen["values"] = dialog.values()
        ok(dialog)
    errors = drive(handler)
    menu_action(w, "Vorschlag erzeugen …").trigger()
    assert not errors, errors
    assert seen["values"] == {"seed": 42, "length_m": 4500.0, "curves": 10, "min_radius_m": 35.0}
    first = json.loads(json.dumps(w.document.project.raw))
    info = first["editor_import"]
    assert first["name"] == "Vorschlag 42" and info["seed"] == 42 and abs(info["laenge_m"] - 4500) < 100
    assert info["radius_min_m"] >= 31
    settle(w)
    assert not [i for i in w.preview["issues"] if i.get("schwere") in ("fehler", "warnung")]
    assert " *" in w.windowTitle()
    # zweiter Aufruf mit denselben Werten: dieselbe Strecke
    w.document.undo.setClean()
    errors = drive(lambda dialog: (dialog.seed.setValue(42), dialog.length.setValue(4500), dialog.curves.setValue(10),
                                   dialog.radius.setValue(35), ok(dialog)))
    menu_action(w, "Vorschlag erzeugen …").trigger()
    assert not errors, errors
    assert w.document.project.raw == first


def test_journey_menu_asks_before_discarding_dirty_project(editor, monkeypatch):
    w = editor(Project({"name": "Ungespeichert", "mittellinie": {"art": "kontrollpunkte", "punkte": [[0, 0], [100, 0], [100, 100], [0, 100]]}}))
    w.document.edit("Name ändern", lambda raw, meta: raw.update(name="Geändert"))
    answers = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: answers.append("Abbrechen") or QMessageBox.Cancel)
    shown = []
    monkeypatch.setattr(TemplateDialog, "exec", lambda self: shown.append(1) or QDialog.Rejected)
    monkeypatch.setattr(SuggestionDialog, "exec", lambda self: shown.append(2) or QDialog.Rejected)
    menu_action(w, "Neu aus Vorlage …").trigger()
    menu_action(w, "Vorschlag erzeugen …").trigger()
    assert answers == ["Abbrechen", "Abbrechen"] and not shown and w.document.project.raw["name"] == "Geändert"


def test_journey_cancel_in_dialog_changes_nothing(editor):
    w = editor()
    project = w.document.project
    errors = drive(lambda dialog: QTest.mouseClick(dialog.buttons.button(QDialogButtonBox.Cancel), Qt.LeftButton))
    menu_action(w, "Vorschlag erzeugen …").trigger()
    assert not errors and w.document.project is project
