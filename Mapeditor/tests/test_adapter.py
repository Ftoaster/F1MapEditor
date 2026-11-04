"""Echter Generator für Vorschau; ausschließlich Attrappen für Bau/Laufzeit."""

import json
import math
from pathlib import Path
import sys

import pytest
from PySide6.QtCore import QElapsedTimer
from PySide6.QtTest import QTest

from mapeditor.adapter import ProcessJob, build_command, calculate_analysis, calculate_preview, toolkit_path, worker_command
from mapeditor.project import Project
from conftest import EXAMPLES, ROOT


def wait_until(predicate, limit=8000):
    timer = QElapsedTimer()
    timer.start()
    while not predicate() and timer.elapsed() < limit:
        QTest.qWait(10)
    assert predicate(), "Zeitlimit für Qt-Prozess überschritten"


def test_real_generator_preview(example_preview):
    assert 4900 < example_preview["length"] < 4910
    assert 0 <= min(example_preview["s"]) < 1
    assert len(example_preview["objects"]) == 26
    assert max(example_preview["bank"]) > 2
    assert max(p[1] for p in example_preview["pos"]) > 7
    assert len(example_preview["sides"]["links"]["edges"]) == 6
    json.dumps(example_preview, allow_nan=False)


def test_portimao_preview_source_unchanged():
    if not (EXAMPLES / "portimao_form.json").exists():
        pytest.skip("portimao_form.json gehört nicht zum öffentlichen Stand")
    project = Project.open(EXAMPLES / "portimao_form.json")
    raw = json.dumps(project.raw)
    preview = calculate_preview({"raw": project.raw, "basis": str(project.basis)})
    assert len(preview["objects"]) == 669
    assert 4500 < preview["length"] < 4800
    assert json.dumps(project.raw) == raw


def test_frame_includes_project_offset(example_preview):
    assert not any(issue["id"] == "LAGE_AUSSERHALB_AO" for issue in example_preview["issues"])
    project = Project.open(EXAMPLES / "teststrecke.json")
    result = calculate_preview({"raw": project.raw, "shift": [5000, 0, 0]})
    issue = next(issue for issue in result["issues"] if issue["id"] == "LAGE_AUSSERHALB_AO")
    assert "s" in issue and issue["kurz"] == "KO-01" and issue["level"] == "Warnung"


def test_invalid_toolkit_does_not_fallback(tmp_path):
    with pytest.raises(ValueError, match="nicht gefunden"):
        toolkit_path(tmp_path)


def test_build_contract_only_describes_command(tmp_path):
    command = build_command(ROOT / "ego-werkzeuge", tmp_path / "projekt", tmp_path / "bau", tmp_path / "pakete")
    assert "bauen" in command and command[3:5] == ["mapeditor", "--werkzeug-cli"]
    assert "--ereignisse-json" in command and "--atomar" in command
    assert command[command.index("--projekt") + 1] == str((tmp_path / "projekt").resolve())
    assert command[command.index("--profil") + 1] == "empfohlen"
    assert command[command.index("--ausgabe") + 1] == str((tmp_path / "bau" / "entwurf").resolve())
    assert "--pruefsummen" in command and "--arbeit-aufraeumen" in command
    assert not (tmp_path / "bau").exists()


def test_process_progress_with_build_mock(app, tmp_path):
    script = tmp_path / "attrappe.py"
    script.write_text("import sys,time\nprint('Generator startet',flush=True)\ntime.sleep(0.08)\nprint('Paket: Attrappe',flush=True)\n", encoding="utf-8")
    job = ProcessJob()
    done, output = [], []
    job.done.connect(lambda code, text: done.append((code, text)))
    job.output.connect(output.append)
    job.start([sys.executable, str(script), "bauen"])
    wait_until(lambda: bool(done))
    assert done[0][0] == 0 and "Attrappe" in done[0][1]
    assert "Generator startet" in "".join(output)


def test_process_cancellation_with_mock(app):
    job = ProcessJob()
    done, output = [], []
    job.done.connect(lambda code, text: done.append(code))
    job.output.connect(output.append)
    job.start([sys.executable, "-u", "-c", "import time; print('Bereit',flush=True); time.sleep(30)"])
    wait_until(lambda: bool(output))
    job.cancel()
    wait_until(lambda: bool(done))
    assert done == [-2]


def test_progress_utf8_across_chunks(app):
    job, done, output = ProcessJob(), [], []
    job.done.connect(lambda code, text: done.append(code))
    job.output.connect(output.append)
    job.start([sys.executable, "-u", "-c", "import sys,time; sys.stdout.buffer.write(bytes([195])); sys.stdout.flush(); time.sleep(0.1); sys.stdout.buffer.write(bytes([188])); sys.stdout.flush()"])
    wait_until(lambda: bool(done))
    assert "".join(output) == "ü"


def test_process_start_failure(app, tmp_path):
    job, done = ProcessJob(), []
    job.done.connect(lambda code, text: done.append((code, text)))
    job.start([str(tmp_path / "fehlt.exe")])
    wait_until(lambda: bool(done))
    assert done[0][0] == -1 and done[0][1]


def test_worker_json_protocol(app):
    project = Project.open(EXAMPLES / "teststrecke.json")
    job, done = ProcessJob(), []
    job.done.connect(lambda code, text: done.append((code, text)))
    job.start(worker_command(), {"raw": project.raw, "basis": str(project.basis)})
    wait_until(lambda: bool(done))
    assert done[0][0] == 0
    assert json.loads(done[0][1])["length"] > 4900


def ring(radius, anzahl=150):
    punkte = [{"x": round(radius * math.cos(k / anzahl * math.tau), 3), "z": round(radius * math.sin(k / anzahl * math.tau), 3), "y": 0.0}
              for k in range(anzahl)]
    return {"name": "Ring", "mittellinie": {"art": "kontrollpunkte", "punkte": punkte}, "ideallinie": {"schritt_m": 15.0}}


def test_grosser_ring_hat_kurven_fuer_alle_bremslinien(tmp_path):
    """Radius über der Kurvenschwelle (500 m): Die Prüfung fand keine Kurve, der Bau scheiterte an den Bremslinien."""
    ergebnis = calculate_analysis({"raw": ring(700.0), "basis": str(tmp_path), "shift": [0, 0, 0]})
    assert ergebnis["analysis"]["kurven"]
    assert not [i["kurz"] for i in ergebnis["issues"] if i["kurz"] == "KI-04"]


def test_strecke_ausserhalb_des_aussenterrains_nennt_den_versatz(tmp_path):
    ergebnis = calculate_analysis({"raw": ring(950.0, 300), "basis": str(tmp_path), "shift": [0, 0, 0]})
    meldungen = [i for i in ergebnis["issues"] if i["kurz"] == "KO-02"]
    assert len(meldungen) == 1 and meldungen[0]["level"] == "Fehler" and "game world offset to [" in meldungen[0]["text"]
