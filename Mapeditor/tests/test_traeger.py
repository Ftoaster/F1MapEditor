"""Trägerpaket einrichten: prüfen, ablegen, im Downloads-Ordner finden, Dialog und Aufruf beim Bauen."""

import json
from pathlib import Path
import zipfile

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog

from mapeditor import traeger, traeger_ui
from test_build import builder, wait_until   # noqa: F401  (Fixture builder: Fenster mit Bauattrappe)


def make_zip(path, version="1.0.2", code="portimao", erp="portimao.erp", with_erp=True, author="Pajko"):
    manifest = {"author_name": author, "erp": erp, "track_code": code, "version": version}
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        if with_erp:
            archive.writestr(erp, b"x" * 2000)
    return Path(path)


@pytest.fixture(autouse=True)
def small_carrier(monkeypatch, tmp_path):
    monkeypatch.setattr(traeger, "MIN_SIZE", 0)       # Attrappen statt 2,9 GB
    monkeypatch.setenv("MAPEDITOR_TRAEGER", str(tmp_path / "ablage"))


def test_inspect_accepts_supported_versions_and_names_every_problem(tmp_path):
    good = traeger.inspect_carrier(make_zip(tmp_path / "a.zip"))
    assert good["ok"] and good["version"] == "1.0.2" and good["autor"] == "Pajko"
    assert traeger.inspect_carrier(make_zip(tmp_path / "b.zip", "1.0.0"))["ok"]
    old = traeger.inspect_carrier(make_zip(tmp_path / "c.zip", "0.9"))
    assert not old["ok"] and "0.9" in old["problem"] and "1.0.2" in old["problem"]
    other = traeger.inspect_carrier(make_zip(tmp_path / "d.zip", code="monza"))
    assert not other["ok"] and "monza" in other["problem"]
    assert not traeger.inspect_carrier(make_zip(tmp_path / "e.zip", with_erp=False))["ok"]
    (tmp_path / "f.zip").write_bytes(b"kein zip")
    assert not traeger.inspect_carrier(tmp_path / "f.zip")["ok"]
    with zipfile.ZipFile(tmp_path / "g.zip", "w") as archive:
        archive.writestr("x.txt", "x")
    assert not traeger.inspect_carrier(tmp_path / "g.zip")["ok"]


def test_too_small_download_is_not_taken_for_the_mod(tmp_path, monkeypatch):
    monkeypatch.setattr(traeger, "MIN_SIZE", 10_000_000)
    info = traeger.inspect_carrier(make_zip(tmp_path / "klein.zip"))
    assert not info["ok"] and info["problem"]


def test_candidates_skip_unfinished_downloads(tmp_path):
    folder = tmp_path / "downloads"
    folder.mkdir()
    make_zip(folder / "portimao.zip")
    make_zip(folder / "portimao_neu.zip")
    (folder / "portimao_neu.zip.crdownload").write_bytes(b"")
    make_zip(folder / "fremd.zip", code="monza")
    found = traeger.find_candidates(folder)
    assert [p.name for p, _ in found] == ["portimao.zip"]


def test_copy_keeps_source_and_leaves_no_part_file(tmp_path):
    source = make_zip(tmp_path / "quelle.zip")
    target = traeger.carrier_target("1.0.2")
    seen = []
    assert traeger.copy_carrier(source, target, seen.append, chunk=512)
    assert target.read_bytes() == source.read_bytes() and source.is_file() and seen[-1] == source.stat().st_size
    assert target.name == "portimao_1.0.2.zip" and not list(target.parent.glob("*.part"))
    other = tmp_path / "ablage" / "zwei.zip"
    assert not traeger.copy_carrier(source, other, cancelled=lambda: True, chunk=512)
    assert not other.exists() and not list(other.parent.glob("*.part"))


def test_space_problem_reported_before_copy(tmp_path, monkeypatch):
    class Usage:
        free = 1000
    monkeypatch.setattr(traeger.shutil, "disk_usage", lambda p: Usage)
    assert "free" in traeger.space_problem(tmp_path, 3_000_000_000).lower() or traeger.space_problem(tmp_path, 3_000_000_000)


def test_default_carrier_prefers_the_one_set_up_by_the_user(tmp_path):
    from mapeditor.adapter import default_carrier
    settings = QSettings()
    settings.remove(traeger.SETTING)
    assert default_carrier(tmp_path) == traeger.carrier_target()     # nichts eingerichtet: Soll-Ort der Nutzerablage
    mine = make_zip(tmp_path / "mein.zip")
    traeger.remember_carrier(settings, mine)
    assert default_carrier(tmp_path) == mine
    settings.remove(traeger.SETTING)


def test_dialog_copies_to_the_carrier_folder_and_remembers_it(app, tmp_path):
    settings = QSettings()
    settings.remove(traeger.SETTING)
    source = make_zip(tmp_path / "heruntergeladen.zip")
    dialog = traeger_ui.CarrierDialog(None, missing=tmp_path / "fehlt.zip", settings=settings)
    assert "fehlt.zip" in dialog.question.text()
    dialog.offer(source)
    assert not dialog.offer_card.isHidden() and "1.0.2" in dialog.offer_label.text() and "Pajko" in dialog.offer_label.text()
    dialog.copy_and_use()
    wait_until(lambda: dialog.result_path is not None, limit=8000)
    assert dialog.result_path == traeger.carrier_target("1.0.2") and dialog.result_path.is_file()
    assert traeger.saved_carrier(settings) == dialog.result_path and source.is_file()
    settings.remove(traeger.SETTING)


def test_dialog_use_in_place_and_rejects_wrong_mod(app, tmp_path):
    dialog = traeger_ui.CarrierDialog(None)
    dialog.offer(make_zip(tmp_path / "monza.zip", code="monza"))
    assert dialog.offer_card.isHidden() and "monza" in dialog.message.text() and dialog.offer_path is None
    good = make_zip(tmp_path / "ok.zip", "1.0.0")
    dialog.offer(good)
    assert "1.0.2" in dialog.offer_note.text()                       # ältere Version: Hinweis auf die empfohlene
    dialog.use_in_place()
    assert dialog.result_path == good


def test_download_button_opens_page_and_offers_a_stable_download(app, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(traeger_ui.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    folder = tmp_path / "downloads"
    folder.mkdir()
    monkeypatch.setattr(traeger, "downloads_folder", lambda: folder)
    dialog = traeger_ui.CarrierDialog(None)
    dialog.start_download()
    assert opened == [traeger.CARRIER_URL] and not dialog.wait_label.isHidden()
    assert dialog.offer_path is None
    make_zip(folder / "portimao.zip")
    dialog.poll()                       # erste Sichtung: Größe merken
    assert dialog.offer_path is None
    dialog.poll()                       # Größe unverändert: fertig, anbieten
    assert dialog.offer_path == folder / "portimao.zip" and not dialog.timer.isActive()


def test_build_without_carrier_asks_and_uses_the_answer(builder, tmp_path, monkeypatch):
    w, d, mode = builder
    d.carrier.setText(str(tmp_path / "fehlt.zip"))
    assert not d.carrier_banner.isHidden()
    # Skripte und Tests fragen nie: der Bau meldet den fehlenden Träger und öffnet den Ordnerbereich
    w.interactive = False
    QTest.mouseClick(d.start_button, Qt.LeftButton)
    assert d.job is None and "fehlt.zip" in d.phase.text() and not d.paths_box.isHidden()
    # im echten Editor kommt die Abfrage; Antwort „Abbrechen“ bricht ab, ein Pfad setzt das Feld
    w.interactive = True
    asked = []
    monkeypatch.setattr(traeger_ui.CarrierDialog, "ask", staticmethod(lambda parent, missing, settings: asked.append(missing) or None))
    QTest.mouseClick(d.start_button, Qt.LeftButton)
    assert asked and "fehlt.zip" in str(asked[0]) and d.job is None
    good = make_zip(tmp_path / "traeger_neu.zip")
    monkeypatch.setattr(traeger_ui.CarrierDialog, "ask", staticmethod(lambda parent, missing, settings: good))
    QTest.mouseClick(d.start_button, Qt.LeftButton)
    assert d.carrier.text() == str(good) and d.carrier_banner.isHidden()
