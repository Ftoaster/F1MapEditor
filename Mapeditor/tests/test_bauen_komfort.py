"""Baudialog-Komfort: fortlaufender Bauname, Speicheranzeige, wiederverwendete Prüfung, Schnellaustausch mit
Bestätigung und Spieltest, Aufräumen alter Bauten (nur in Testordnern)."""

import json
import os
from pathlib import Path
import sys
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from mapeditor import build as build_module
from mapeditor.adapter import injector_command
from mapeditor.build import BuildDialog, CleanupDialog, next_build_name, space_text
from mapeditor.project import Project
from mapeditor.window import MainWindow
from conftest import EXAMPLES, ROOT
from test_adapter import wait_until


def make_project(tmp_path, playtests=()):
    project = Project.open(EXAMPLES / "teststrecke.json")
    project.save(tmp_path / "TestEdit")
    project.playtests = list(playtests)
    if playtests:
        project.save_playtests()
    return project


def test_next_build_name_continues_numbering(tmp_path):
    project = make_project(tmp_path)
    output, packages = tmp_path / "bauten", tmp_path / "pakete"
    assert next_build_name(project, output, packages) == "testedit1"
    output.mkdir()
    packages.mkdir()
    for name in ("bau_20261025_221706", "testedit7_scheitel", "testedit9_bremsmarker"):
        (output / name).mkdir()
        time.sleep(0.01)
    (packages / "testedit10_tafeln50.zip").write_bytes(b"x")
    assert next_build_name(project, output, packages) == "testedit11"
    (output / "testedit11").mkdir()                      # schon vergeben → nächste freie Nummer
    assert next_build_name(project, output, packages) == "testedit12"


def test_space_text_names_need_and_free(tmp_path):
    text = space_text(tmp_path / "bauten", tmp_path / "pakete")
    assert text.startswith("Bedarf etwa 4,0 GB · frei ") and " auf " in text


@pytest.fixture
def dialog(app, tmp_path, monkeypatch, example_preview):
    package = tmp_path / "TestEdit" / "pakete" / "testedit3_neu.zip"
    record = {"id": "r3", "datum": "2026-10-25T20:00:00+02:00", "name": "testedit3_neu", "bau_status": "ok",
              "status": "offen", "paket": str(package), "pakete": []}
    project = make_project(tmp_path, [record])
    package.parent.mkdir(parents=True)
    package.write_bytes(b"Paket")
    w = MainWindow(project, auto_preview=False)
    w.install_preview(example_preview)
    opened = []
    monkeypatch.setattr(w, "open_playtests", lambda select=None: opened.append(select))
    d = BuildDialog(w)
    d.show()
    yield w, d, opened
    d.close()
    w.document.undo.setClean()
    w.close()


def fake_injector(tmp_path, plan_text, code=0, write_marker=True):
    """Attrappe für injektor_tool: plan druckt den Plan, injizieren legt eine Markierung an."""
    script = tmp_path / "injektor_attrappe.py"
    script.write_text(
        "import sys,pathlib\n"
        "action=sys.argv[1]\n"
        f"print({plan_text!r})\n"
        "if action=='injizieren':\n"
        f"    pathlib.Path({str(tmp_path / 'injiziert.txt')!r}).write_text(sys.argv[2])\n"
        f"sys.exit({code} if action=='injizieren' else 0)\n", encoding="utf-8")
    calls = []

    def command(root, action, package, backups):
        calls.append((action, str(backups)))
        return [sys.executable, "-B", str(script), action, str(package)]
    return command, calls


def test_dialog_suggests_name_shows_space_and_offers_last_package(dialog):
    w, d, _ = dialog
    assert d.name.text() == "testedit4"
    assert d.space.text().startswith("Bedarf etwa")
    assert d.inject_button.isEnabled() and "testedit3_neu.zip" in d.inject_button.toolTip()


def test_injection_asks_every_time_then_opens_playtest(dialog, tmp_path, monkeypatch):
    w, d, opened = dialog
    d.injector, calls = fake_injector(tmp_path, "Plan: Slot-ERP ersetzen, 12 Mipmaps neu")
    asked = []
    monkeypatch.setattr(d, "confirm_injection", lambda text: asked.append(text) or False)
    QTest.mouseClick(d.inject_button, Qt.LeftButton)
    wait_until(lambda: d.inject_job is None and asked)
    assert "Slot-ERP" in asked[0] and "abgebrochen" in d.phase.text()
    assert not (tmp_path / "injiziert.txt").exists() and [c[0] for c in calls] == ["plan"]
    monkeypatch.setattr(d, "confirm_injection", lambda text: asked.append(text) or True)
    QTest.mouseClick(d.inject_button, Qt.LeftButton)
    wait_until(lambda: d.inject_job is None and len(asked) == 2 and (tmp_path / "injiziert.txt").exists())
    wait_until(lambda: d.inject_job is None and opened)
    assert len(asked) == 2                               # jede Übertragung fragt erneut
    assert [c[0] for c in calls] == ["plan", "plan", "injizieren"]
    assert calls[-1][1].endswith("injektion_sicherung")
    assert "ist im Spiel" in d.phase.text() and opened == ["r3"]
    saved = Project.open(w.document.project.folder)
    assert saved.playtests[0]["injiziert"]


def test_injection_error_and_nothing_to_do(dialog, tmp_path, monkeypatch):
    w, d, opened = dialog
    d.injector, _ = fake_injector(tmp_path, "Nichts zu tun – das Paket ist schon im Spiel.")
    monkeypatch.setattr(d, "confirm_injection", lambda text: pytest.fail("darf nicht fragen"))
    QTest.mouseClick(d.inject_button, Qt.LeftButton)
    wait_until(lambda: d.inject_job is None and "bereits im Spiel" in d.phase.text())
    d.injector, _ = fake_injector(tmp_path, "FEHLER: F1 25 läuft noch – bitte das Spiel beenden.", code=2)
    monkeypatch.setattr(d, "confirm_injection", lambda text: True)
    QTest.mouseClick(d.inject_button, Qt.LeftButton)
    wait_until(lambda: d.inject_job is None and "fehlgeschlagen" in d.phase.text())
    assert "läuft noch" in d.phase.text() and not opened


def test_injector_command_uses_shim_and_backup_folder(tmp_path):
    command = injector_command(ROOT / "ego-werkzeuge", "injizieren", tmp_path / "p.zip", tmp_path / "sich")
    assert command[command.index("--werkzeug-cli") + 1].endswith("injektor_tool.py")
    assert command[-3:] == ["injizieren", str((tmp_path / "p.zip").resolve()), "--ausfuehren"]
    assert "--sicherungsordner" in command
    with pytest.raises(ValueError):
        injector_command(ROOT / "ego-werkzeuge", "zuruecksetzen", tmp_path / "p.zip", tmp_path)


def test_build_check_reuses_cached_analysis(dialog, monkeypatch):
    w, d, _ = dialog
    report = {"meldungen": [], "nicht_geprueft": [], "stufen": ["eingabe", "profil", "generator"],
              "zusammenfassung": {"ok": True}}
    launched = []
    monkeypatch.setattr(d, "launch", lambda: launched.append(True))
    monkeypatch.setattr(build_module, "worker_command", lambda: pytest.fail("Prüfung hätte nicht laufen dürfen"))
    monkeypatch.setattr(build_module, "check_space", lambda *args: None)
    carrier = Path(d.output.text()).parent / "traeger.zip"
    carrier.write_bytes(b"x")
    d.carrier.setText(str(carrier))
    project = w.document.project
    key = build_module.check_key(project.raw, project.folder, project.manifest.get("versatz", [0, 0, 0]))
    w.check_cache[key] = {"issues": [], "report": report, "analysis": None}
    d.start_build()
    assert launched and "letzten Prüfung" in d.phase.text()


def test_cleanup_preselects_old_builds_and_deletes_only_inside_folders(app, tmp_path, monkeypatch):
    output, packages = tmp_path / "bauten", tmp_path / "pakete"
    output.mkdir()
    packages.mkdir()
    names = ["bau_alt", "testedit6_ki", "testedit7_ok", "testedit8_im_spiel", "testedit9_neu"]
    for i, name in enumerate(names):
        (output / name).mkdir()
        (output / name / "erp.bin").write_bytes(b"x" * 1000 * (i + 1))
        (packages / (name + ".zip")).write_bytes(b"z" * 500)
        stamp = time.time() - 1000 + i * 10
        for p in (output / name, packages / (name + ".zip")):
            os.utime(p, (stamp, stamp))
    (packages / "testedit6_ki_ki_bericht.json").write_text("{}")
    (output / "bau_alt.abbruch").write_text("x")
    outside = tmp_path / "anderes.zip"
    outside.write_bytes(b"bleibt")
    project = make_project(tmp_path, [
        {"id": "a", "name": "testedit7_ok", "status": "ok", "bau_status": "ok", "datum": "x"},
        {"id": "b", "name": "testedit8_im_spiel", "status": "offen", "bau_status": "ok", "datum": "x",
         "injiziert": "2026-10-25T21:00:00+02:00"},
        {"id": "c", "name": "testedit6_ki", "status": "Problem", "bau_status": "ok", "datum": "x"}])
    c = CleanupDialog(project, output, packages)
    c.show()
    chosen = {e["name"] for e in c.selected()}
    assert chosen == {"bau_alt", "testedit6_ki"}         # jüngster, letzter „ok“ und der im Spiel bleiben
    assert "2 von 5 ausgewählt" in c.total.text()
    monkeypatch.setattr(c, "confirm", lambda chosen: False)
    c.delete_selected()
    assert (output / "bau_alt").exists()
    monkeypatch.setattr(c, "confirm", lambda chosen: True)
    c.delete_selected()
    for name in ("bau_alt", "testedit6_ki"):
        assert not (output / name).exists() and not (packages / (name + ".zip")).exists()
    assert not (packages / "testedit6_ki_ki_bericht.json").exists() and not (output / "bau_alt.abbruch").exists()
    for name in ("testedit7_ok", "testedit8_im_spiel", "testedit9_neu"):
        assert (output / name).exists() and (packages / (name + ".zip")).exists()
    assert outside.exists()
    assert Project.open(project.folder).playtests[2]["geloescht"]
    assert c.table.rowCount() == 3
    c.close()
