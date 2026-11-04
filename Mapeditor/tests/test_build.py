"""AP25-Nutzerreisen: echte Widgets/Prozesse, kleine Bauattrappen, Schreibschutz."""

import copy
import json
from pathlib import Path
import sys
import os
import subprocess
import zipfile

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from mapeditor import build as build_module
from mapeditor.adapter import BuildJob, build_command, calculate_preview
from mapeditor.build import BuildDialog, PlaytestDialog
from mapeditor.paths import writable_path
from mapeditor.project import Project, read_json
from mapeditor.window import MainWindow
from conftest import EXAMPLES, ROOT, doc_screenshot
from test_adapter import wait_until


MOCK = '''import json,sys,time,pathlib,shutil
mode=sys.argv[1]
def opt(n): return pathlib.Path(sys.argv[sys.argv.index(n)+1])
work,abort,packages,project=map(opt,['--ausgabe','--abbruch-datei','--paket-ordner','--projekt'])
work.mkdir(parents=True,exist_ok=True)
(work/'eingabe').mkdir()
shutil.copyfile(project/'definition.json',work/'eingabe'/'definition.json')
(work/'_paket_tmp').mkdir()
(work/'_paket_tmp'/'teil.zip').write_bytes(b'teil')
def emit(typ,**kw):
    print(json.dumps(dict(schema='ego-bau-ereignis/1',typ=typ,**kw),ensure_ascii=False),flush=True)
emit('start',eingabe={'schnappschuss':str(work/'eingabe'/'definition.json'),'sha256':'abc'})
emit('phase',phase='generator',titel='Generator läuft',fortschritt=0.25)
print('Nur Diagnose auf stderr',file=sys.stderr,flush=True)
emit('meldung',meldung={'id':'LAGE_AUSSERHALB_AO','kurz':'KO-01','schwere':'warnung','text':'Attrappe: Stelle prüfen','ort':{'spline':'mittellinie','station_m':123.0}})
time.sleep(0.2)
if mode=='malformed': print('keine JSON-Zeile',flush=True)
if mode in ('cancel','hang'):
    if mode=='hang': time.sleep(30)
    while not abort.exists(): time.sleep(0.01)
    shutil.rmtree(work/'_paket_tmp')
    emit('abbruch',text='Bau abgebrochen',phase='generator')
    sys.exit(3)
if mode in ('error','rules'):
    emit('fehler',text='Attrappenfehler',phase='generator',art='BauFehler')
    sys.exit(2 if mode=='rules' else 1)
if mode=='missing': sys.exit(0)
packages.mkdir(parents=True,exist_ok=True)
package=packages/'attrappe.zip'
package.write_bytes(b'Attrappenpaket')
report=work/'pruefbericht.json'
report.write_text('{}')
event=dict(schema='ego-bau-ereignis/1',typ='ergebnis',status='ok',fortschritt=1,
           pakete=[dict(pfad=str(package),groesse=13,pruefung_ok=True,sha256='123')],
           pruefbericht=str(report),bericht_md=str(work/'bericht.md'))
sys.stdout.write(json.dumps(event))
sys.stdout.flush()
sys.exit(1 if mode=='badexit' else 0)
'''


@pytest.fixture
def builder(app, tmp_path, monkeypatch, example_preview):
    project = Project.open(EXAMPLES / "teststrecke.json")
    project.manifest["versatz"] = [-591, 0, -473]
    project.save(tmp_path / "projekt")
    w = MainWindow(project, auto_preview=False)
    w.install_preview(example_preview)
    w.show()
    d = BuildDialog(w)
    w.build_dialog = d
    d.show()
    d.output.setText(str(tmp_path / "bauten"))
    d.packages.setText(str(tmp_path / "pakete"))
    carrier = tmp_path / "traeger.zip"
    carrier.write_bytes(b"Attrappe")
    d.carrier.setText(str(carrier))
    script = tmp_path / "attrappe.py"
    script.write_text(MOCK, encoding="utf-8")
    mode = ["success"]
    real_command = build_command
    def command(*args, **kwargs):
        original = real_command(*args, **kwargs)
        return [sys.executable, "-B", str(script), mode[0]] + original[original.index("bauen") + 1:]
    monkeypatch.setattr(build_module, "build_command", command)
    response = {"issues": [], "report": {"meldungen": [], "nicht_geprueft": [], "zusammenfassung": {"ok": True}}}
    monkeypatch.setattr(build_module, "worker_command", lambda: [sys.executable, "-B", "-c",
                        "import sys;sys.stdin.read();print(" + repr(json.dumps(response)) + ")"])
    monkeypatch.setattr(build_module, "check_space", lambda *args: None)
    yield w, d, mode
    if d.job:
        d.job._kill_if_running()
        wait_until(lambda: d.job is None)
    d.close()
    w.document.undo.setClean()
    w.close()


def click_build(dialog):
    QTest.mouseClick(dialog.start_button, Qt.LeftButton)
    wait_until(lambda: isinstance(dialog.job, BuildJob) or not dialog.start_button.isEnabled(), limit=8000)


def test_build_journey_progress_result_snapshot_and_playtest(builder, tmp_path):
    w, d, mode = builder
    # Ungespeicherte Änderung muss vor dem Prozess in der gespeicherten Eingabe stehen.
    w.document.edit("Name", lambda raw, meta: raw.update(name="Gespeicherter Bau"))
    w.cancel_preview()
    QTest.mouseClick(d.start_button, Qt.LeftButton)
    wait_until(lambda: isinstance(d.job, BuildJob) and d.progress.value() == 250)
    assert not d.fields.isEnabled() and d.close_button.text() == __import__("mapeditor.i18n", fromlist=["tr"]).tr("Hide") and d.state == "building"
    assert "Generator läuft" in d.phase.text()
    assert "Attrappe" in d.issue_list.item(0).text()
    d.issue_list.setCurrentRow(0)
    QTest.mouseClick(d.issue_list.viewport(), Qt.LeftButton, pos=d.issue_list.visualItemRect(d.issue_list.item(0)).center())
    assert w.height.station == w.map.station == w.cross.station == 123
    screenshot(d, "bauen_fortschritt.png")
    wait_until(lambda: d.job is None)
    assert "Paket geprüft" in d.phase.text() and d.progress.value() == 1000
    assert d.explorer_button.isEnabled() and d.report_button.isEnabled()
    assert "SHA-256: 123" in d.result_text.toPlainText()
    assert "Nur Diagnose auf stderr" in d.log.toPlainText()
    assert (Path(d.record["ausgabe"]) / "eingabe" / "definition.json").is_file()
    assert read_json(Path(d.record["ausgabe"]) / "eingabe" / "definition.json")["name"] == "Gespeicherter Bau"
    assert read_json(w.document.project.folder / "projekt.json")["bauen"]["traeger"] == d.carrier.text()
    screenshot(d, "bauen_ergebnis.png")
    d.close()
    p = PlaytestDialog(w.document.project, w)
    p.show()
    p.status.setCurrentText("Problem")
    QTest.keyClicks(p.text, "Boxenausfahrt testen")
    QTest.mouseClick(p.save_button, Qt.LeftButton)
    assert "gespeichert" in p.message.text()
    saved = Project.open(w.document.project.folder)
    assert saved.playtests[0]["status"] == "Problem"
    assert saved.playtests[0]["ergebnis"] == "Boxenausfahrt testen"
    assert saved.playtests[0]["paket"].endswith("attrappe.zip")
    w.document.undo.undo()
    assert w.document.project.playtests[0]["status"] == "Problem"
    screenshot(p, "bauen_spieltest.png")
    p.close()


@pytest.mark.parametrize("mode", ["error", "rules", "missing", "badexit", "malformed"])
def test_build_journey_error_never_claims_success(builder, mode):
    w, d, selection = builder
    selection[0] = mode
    QTest.mouseClick(d.start_button, Qt.LeftButton)
    wait_until(lambda: d.record is not None and d.job is None)
    assert d.record["bau_status"] == "fehlgeschlagen"
    assert d.record["paket"] == "" and d.result is None
    assert not d.explorer_button.isEnabled() and not d.report_button.isEnabled()
    assert d.start_button.isEnabled()
    if mode == "error":
        assert "Attrappenfehler" in d.phase.text()
        screenshot(d, "bauen_fehler.png")


@pytest.mark.parametrize("hard", [False, True])
def test_build_journey_cancel_file_and_timeout_cleanup(builder, hard, tmp_path):
    w, d, mode = builder
    mode[0] = "hang" if hard else "cancel"
    QTest.mouseClick(d.start_button, Qt.LeftButton)
    wait_until(lambda: isinstance(d.job, BuildJob) and d.progress.value() == 250)
    job = d.job
    sibling = Path(d.output.text()) / "anderer_bau" / "_paket_tmp"
    sibling.mkdir(parents=True)
    (sibling / "behalten").write_text("unberührt", encoding="utf-8")
    if hard:
        job.kill_timer.setInterval(30)
    QTest.mouseClick(d.cancel_button, Qt.LeftButton)
    assert job.abort_file.exists() and "Wird abgebrochen" in d.cancel_button.text()
    assert not d.cancel_button.isEnabled()
    screenshot(d, "bauen_abbruch.png")
    wait_until(lambda: d.job is None)
    assert d.record["exitcode"] == (-2 if hard else 3)
    assert d.record["bau_status"] == "abgebrochen"
    assert not (job.work / "_paket_tmp").exists()
    assert (sibling / "behalten").exists()
    assert not Path(d.packages.text()).exists()
    if hard:
        assert read_json(job.work / "bau_status.json")["hart_beendet"]


def screenshot(widget, name):
    doc_screenshot(widget, name)


def test_real_check_journey_click_filter_and_location(app, example_preview):
    project = Project.open(EXAMPLES / "teststrecke.json")
    project.manifest["versatz"] = [5000, 0, 0]
    w = MainWindow(project, auto_preview=False)
    w.show()
    w.request_check()
    wait_until(lambda: w.job is None and w.analysis_job is None and w.analysis_key is not None, limit=90000)
    assert "generator" in w.validation_report["stufen"]
    assert w.analysis and w.analysis["kurven"] and w.curve_table.rowCount() == len(w.analysis["kurven"])
    # gleicher Stand: erneutes Prüfen kommt aus dem Zwischenspeicher, ohne neuen Prozess
    w.request_check()
    assert w.analysis_job is None and w.analysis_key == w.current_key()
    w.issue_filter.setCurrentText("Warnung")   # LAGE_AUSSERHALB_AO ist seit 27.10.2026 eine Warnung
    w.issue_search.setText("LAGE_AUSSERHALB_AO")
    assert w.issues.count() >= 1
    # je Stufe stehen Meldungen ohne Ort vorn (issue_order); angeklickt wird die erste mit Station
    item = next(w.issues.item(i) for i in range(w.issues.count()) if "s" in w.issues.item(i).data(Qt.UserRole))
    assert item.data(Qt.UserRole)["id"] == "LAGE_AUSSERHALB_AO"
    station = item.data(Qt.UserRole)["s"]
    QTest.mouseClick(w.issues.viewport(), Qt.LeftButton, pos=w.issues.visualItemRect(item).center())
    assert w.map.station == w.height.station == w.bank.station == w.cross.station == station
    assert w.document.selected == w.objects.currentRow() >= 0
    w.issue_filter.setCurrentText("Nicht geprüft")
    w.issue_search.clear()
    assert w.issues.count() == len(w.validation_report["nicht_geprueft"])
    w.issue_filter.setCurrentText("Fehler")
    screenshot(w, "bauen_pruefung.png")
    w.close()


def test_world_location_uses_offset_for_box_spline(app, example_preview):
    project = Project.open(EXAMPLES / "teststrecke.json")
    project.manifest["versatz"] = [-591, 0, -473]
    w = MainWindow(project, auto_preview=False)
    w.install_preview(example_preview)
    i = 100
    world = [a + b for a, b in zip(example_preview["pos"][i], project.manifest["versatz"])]
    w.set_issues([{"id": "BOX_SPLINE_SPRUNG", "level": "Warnung", "text": "Ort",
                   "ort": {"spline": "pitlane", "station_m": 5, "welt": world}}])
    w.jump_issue(w.issues.item(0))
    assert w.map.station == example_preview["s"][i]
    w.close()


@pytest.mark.parametrize("destination", [r"Q:\Andere\steamapps\common\F1 25\mods",
                                       r"Q:\STEAMAPPS\COMMON\f1 25\bauten", str(Path.home() / "Downloads" / "pakete")])
def test_guard_rejects_game_downloads_before_any_write(destination, tmp_path, point_project):
    with pytest.raises(ValueError, match="schreibgeschützt|Schreibgeschützt"):
        point_project.save(destination)
    for output, packages in ((destination, tmp_path), (tmp_path, destination)):
        with pytest.raises(ValueError):
            build_command(ROOT / "ego-werkzeuge", tmp_path, output, packages)


def test_frozen_shim_and_installation_guard(monkeypatch, tmp_path, point_project):
    install = tmp_path / "Installation"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(install / "Mapeditor.exe"))
    command = build_command(ROOT / "ego-werkzeuge", tmp_path / "projekt", tmp_path / "arbeit", tmp_path / "pakete")
    assert command[:3] == [sys.executable, "--werkzeug-cli", "neue_strecke_tool.py"]
    for destination in (install, install / "werkzeugkasten" / "testausgabe"):
        with pytest.raises(ValueError):
            point_project.save(destination)
        with pytest.raises(ValueError):
            build_command(ROOT / "ego-werkzeuge", tmp_path, tmp_path / "ausgabe", destination)
    assert not install.exists()


@pytest.mark.parametrize("name", ["../falsch", "a/b", "..", "", "-falsch"])
def test_build_name_no_path_escape(tmp_path, name):
    with pytest.raises(ValueError, match="Bauname"):
        build_command(ROOT / "ego-werkzeuge", tmp_path, tmp_path, tmp_path, name=name)


def test_existing_package_or_work_rejected(tmp_path):
    (tmp_path / "entwurf.zip").write_bytes(b"behalten")
    with pytest.raises(ValueError, match="bereits vorhanden"):
        build_command(ROOT / "ego-werkzeuge", tmp_path, tmp_path / "arbeit", tmp_path)
    (tmp_path / "entwurf.zip").unlink()
    (tmp_path / "entwurf").mkdir()
    with pytest.raises(ValueError, match="bereits vorhanden"):
        build_command(ROOT / "ego-werkzeuge", tmp_path, tmp_path, tmp_path / "pakete")


def test_protocol_chunks_stderr_and_last_line(app, tmp_path):
    script = tmp_path / "chunks.py"
    script.write_text("""import sys,time,json
event=dict(schema='ego-bau-ereignis/1',typ='phase',titel='Prüfung',fortschritt=0.5)
data=(json.dumps(event,ensure_ascii=False)+'\\n').encode('utf8')
i=data.index(bytes([195]))+1
sys.stdout.buffer.write(data[:i]);sys.stdout.flush();time.sleep(.1)
print('Diagnose ist kein JSON',file=sys.stderr,flush=True)
sys.stdout.buffer.write(data[i:]);sys.stdout.flush();time.sleep(.1)
sys.stdout.write(json.dumps(dict(schema='ego-bau-ereignis/1',typ='abbruch',text='Ende')))
""", encoding="utf-8")
    job = BuildJob(tmp_path / "abbruch", tmp_path / "arbeit")
    events, diagnostics, done = [], [], []
    job.event.connect(events.append)
    job.output.connect(diagnostics.append)
    job.done.connect(lambda code, text: done.append(code))
    job.start([sys.executable, "-B", str(script)])
    wait_until(lambda: bool(done))
    assert [e["typ"] for e in events] == ["phase", "abbruch"]
    assert events[0]["titel"] == "Prüfung" and job.protocol_error is None
    assert "Diagnose ist kein JSON" in "".join(diagnostics)


def test_invalid_definition_still_returns_stable_rule():
    result = calculate_preview({"raw": {"mittellinie": {"art": "kontrollpunkte", "punkte": []}}})
    assert result["validation_only"]
    assert result["issues"][0]["id"] == "DEF_UNGUELTIG"


def test_source_package_cli_utf8_and_isolated_workspace(tmp_path):
    archive = tmp_path / "attrappe.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("manifest.json", json.dumps(dict(track_code="sochi", track_name="Prüfung", author_name="Test",
                       version="1", erp="fehlt.erp", ui={}, copy_directories=[])))
    result = subprocess.run([sys.executable, "-B", "-m", "mapeditor", "--werkzeug-cli",
                             str(ROOT / "ego-werkzeuge" / "paket_tool.py"), "pruefen", "--tief", str(archive)],
                            capture_output=True, cwd=tmp_path,
                            env=dict(os.environ, MAPEDITOR_WERKZEUG_ARBEIT=str(tmp_path)), timeout=20)
    assert result.returncode == 1
    assert "Prüfungen" in result.stdout.decode("utf-8") and "vollständig" in result.stdout.decode("utf-8")
    assert (tmp_path / "werkzeug-pruefung" / "testausgabe" / "pakete" / "_pruefen").is_dir()


def test_source_nested_package_process_uses_same_shim(tmp_path):
    toolkit = tmp_path / "werkzeug"
    (toolkit / "strecke").mkdir(parents=True)
    (toolkit / "strecke" / "definition.py").write_text("# Attrappe", encoding="utf-8")
    (toolkit / "neue_strecke_tool.py").write_text("import subprocess,sys,pathlib\np=pathlib.Path(__file__).parent/'paket_tool.py'\nr=subprocess.run([sys.executable,str(p),'pruefen'],capture_output=True,text=True,encoding='utf8')\nprint(r.stdout,end='')\nraise SystemExit(r.returncode)\n", encoding="utf-8")
    (toolkit / "paket_tool.py").write_text("import pathlib\nHIER=str(pathlib.Path(__file__).parent)\ndef main(argv=None):\n p=pathlib.Path(HIER)/'nachweis'\n p.parent.mkdir(parents=True,exist_ok=True)\n p.write_text('Prüfung',encoding='utf8')\n print('Prüfung vollständig')\n return 0\nif __name__=='__main__': raise SystemExit(main())\n", encoding="utf-8")
    work = tmp_path / "arbeit"
    result = subprocess.run([sys.executable, "-B", "-m", "mapeditor", "--werkzeug-cli", str(toolkit / "neue_strecke_tool.py"),
                             "bauen", "--ausgabe", str(work)], capture_output=True, cwd=tmp_path, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "Prüfung vollständig" in result.stdout.decode("utf-8")
    assert (work / "werkzeug-pruefung" / "nachweis").read_text(encoding="utf-8") == "Prüfung"
    assert not (toolkit / "nachweis").exists()


def test_build_start_failure_records_failure(builder, tmp_path):
    w, d, mode = builder
    d.command = [str(tmp_path / "fehlt.exe"), "--ausgabe", str(tmp_path / "bau"),
                 "--abbruch-datei", str(tmp_path / "abbruch")]
    d.launch()
    wait_until(lambda: d.job is None)
    assert Project.open(w.document.project.folder).playtests[0]["exitcode"] == -1
    assert not d.explorer_button.isEnabled()


def test_output_guard_in_click_journey(builder, tmp_path):
    w, d, mode = builder
    d.output.setText(r"Q:\steamapps\common\F1 25\bau")
    assert not d.valid and d.paths_box.isHidden()                  # Feldprüfung beim Tippen, Ordner noch zu
    assert "write-protected" in d.field_error.text() or "schreibgeschützt" in d.field_error.text()
    QTest.mouseClick(d.start_button, Qt.LeftButton)
    assert d.job is None and not d.paths_box.isHidden()             # Klick öffnet den Ordnerbereich
    assert d.state == "error"
    assert not w.document.project.playtests
    assert not (tmp_path / "bauten").exists()


def test_preflight_error_blocks_process_and_preserves_saved_input(builder, monkeypatch):
    w, d, mode = builder
    issue = {"id": "BOX_FEHLT", "kurz": "BO-00", "schwere": "fehler", "level": "Fehler", "text": "Box fehlt"}
    response = {"issues": [issue], "report": {"meldungen": [issue], "zusammenfassung": {"ok": False}}}
    monkeypatch.setattr(build_module, "worker_command", lambda: [sys.executable, "-B", "-c",
                        "import sys;sys.stdin.read();print(" + repr(json.dumps(response)) + ")"])
    QTest.mouseClick(d.start_button, Qt.LeftButton)
    wait_until(lambda: d.job is None)
    assert "Fehler beheben" in d.phase.text() and not w.document.project.playtests
    assert not Path(d.output.text()).exists()
    assert w.validation_issues[0]["id"] == "BOX_FEHLT"


def test_preflight_cancel_never_starts_build(builder, monkeypatch):
    w, d, mode = builder
    monkeypatch.setattr(build_module, "worker_command", lambda: [sys.executable, "-B", "-c",
                        "import sys,time;sys.stdin.read();time.sleep(30)"])
    QTest.mouseClick(d.start_button, Qt.LeftButton)
    assert d.job is not None and not isinstance(d.job, BuildJob)
    QTest.mouseClick(d.cancel_button, Qt.LeftButton)
    wait_until(lambda: d.job is None)
    assert "Vorabprüfung abgebrochen" in d.phase.text()
    assert not w.document.project.playtests


def test_insufficient_space_blocks_before_save(builder, monkeypatch):
    w, d, mode = builder
    def no_space(*args):
        raise ValueError("Zu wenig freier Speicher")
    monkeypatch.setattr(build_module, "check_space", no_space)
    before = (w.document.project.folder / "projekt.json").read_bytes()
    QTest.mouseClick(d.start_button, Qt.LeftButton)
    assert "Zu wenig freier Speicher" in d.phase.text() and d.job is None
    assert (w.document.project.folder / "projekt.json").read_bytes() == before


def test_unavailable_volume_returns_instead_of_looping(monkeypatch):
    monkeypatch.setattr(Path, "exists", lambda self: False)
    with pytest.raises(ValueError, match="nicht erreichbar"):
        build_module.nearest_existing(Path("Q:/nicht_erreichbar/ausgabe"))


def test_frozen_example_save_forces_save_as(app, tmp_path, point_project, monkeypatch):
    install = tmp_path / "installation"
    point_project.save(install / "beispiele")
    before = point_project.source.read_bytes()
    w = MainWindow(point_project, auto_preview=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(install / "Mapeditor.exe"))
    from mapeditor.window import QFileDialog
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: str(tmp_path / "kopie"))
    assert w.save_project()
    assert point_project.folder == tmp_path / "kopie"
    assert (install / "beispiele" / "definition.json").read_bytes() == before
    w.close()


def test_hard_cancel_stops_child_process_heartbeat(app, tmp_path):
    child = tmp_path / "kind.py"
    heartbeat = tmp_path / "heartbeat"
    child.write_text("import pathlib,sys,time\np=pathlib.Path(sys.argv[1])\nwhile True:\n p.write_text(str(time.time_ns()))\n time.sleep(.02)\n", encoding="utf-8")
    parent = tmp_path / "eltern.py"
    parent.write_text("import subprocess,sys,time,json\nsubprocess.Popen([sys.executable,'-B',sys.argv[1],sys.argv[2]])\nprint(json.dumps(dict(schema='ego-bau-ereignis/1',typ='phase',phase='paket_pruefen')),flush=True)\ntime.sleep(30)\n", encoding="utf-8")
    job = BuildJob(tmp_path / "abbruch", tmp_path / "arbeit", cancel_timeout_ms=30)
    done = []
    job.done.connect(lambda code, text: done.append(code))
    job.start([sys.executable, "-B", str(parent), str(child), str(heartbeat)])
    try:
        wait_until(heartbeat.exists)
        job.cancel()
        wait_until(lambda: bool(done))
        assert done == [-2]
        last = heartbeat.read_bytes()
        QTest.qWait(150)
        assert heartbeat.read_bytes() == last
    finally:
        if not done:
            job._kill_if_running()
            job.process.waitForFinished(3000)


def test_ki_profile_option_in_command(tmp_path):
    """KI-Fahrdaten aus dem eigenen Profil (25.10.2026): nur mit Option, dann ``--ki-daten profil``."""
    ohne = build_command(ROOT / "ego-werkzeuge", tmp_path / "projekt", tmp_path / "arbeit", tmp_path / "pakete")
    mit = build_command(ROOT / "ego-werkzeuge", tmp_path / "projekt", tmp_path / "arbeit2", tmp_path / "pakete",
                        ki_profil=True)
    assert "--ki-daten" not in ohne
    assert mit[-2:] == ["--ki-daten", "profil"]
