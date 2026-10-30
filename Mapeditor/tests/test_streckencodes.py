"""Streckencodes im Baudialog (alle, die die Spieldatenbank kennt) mit Zustand im Spiel; Sprachwechsel mit offenem
Baudialog übernimmt die Eingaben."""

import json
import sys

import pytest

from mapeditor.adapter import BUILD_CODES, TRACK_CODES, build_command, parse_game_status
from mapeditor.build import BuildDialog, code_label, code_status
from mapeditor.i18n import setze_sprache
from mapeditor.window import MainWindow
from conftest import ROOT
from test_adapter import wait_until
from test_bauen_komfort import make_project

ZUSTAND = {"zustand": "ohne_abbild", "aktiv": [], "gemerkt": ["sochi", "portimao"], "spiel_laeuft": False,
           "installiert": {"sochi": {"slot": "invitational_image_10", "name": "Teststrecke", "paket": "bau_7.zip"},
                           "bahrain24": {"slot": "invitational_image_9", "name": "Sepang", "paket": "sepang.zip"}}}


def test_alle_codes_der_spieldatenbank():
    root = ROOT / "ego-werkzeuge"
    sys.path.insert(0, str(root))
    try:
        from strecke import streckencode
    finally:
        sys.path.remove(str(root))
    assert set(BUILD_CODES) == set(streckencode.DB_ALTSTRECKEN)
    assert BUILD_CODES[0] == "sochi" and len(BUILD_CODES) == len(set(BUILD_CODES))
    for code in BUILD_CODES:
        assert streckencode.code_bewerten(code)[0], code
        assert build_command(root, root, root / "b", root / "p", "x", options={"streckencode": code})[-2:] == [
            "--streckencode", code]


def test_zustandstexte():
    assert code_label("sochi", "empfohlen", ZUSTAND) == "sochi  ·  empfohlen  ·  im Spiel"
    assert code_label("hanoi", "gut", ZUSTAND) == "hanoi  ·  gut"
    text = code_status("sochi", ZUSTAND)
    assert "invitational_image_10" in text and "bau_7.zip" in text and "gemerkte Aktivierung" in text
    assert "Activate mod" in code_status("bahrain24", ZUSTAND)                  # installiert, nicht gemerkt
    assert "noch nicht im Spiel" in code_status("hanoi", ZUSTAND)
    aktiv = dict(ZUSTAND, zustand="aktiv", aktiv=["sochi"], spiel_laeuft=True)
    assert "ersetzt die Strecke direkt" in code_status("sochi", aktiv) and "Spiel läuft" in code_status("sochi", aktiv)
    assert "nicht eingeschaltet" in code_status("bahrain24", aktiv)
    assert "unbekannt" in code_status("sochi", None)
    assert parse_game_status("Hinweis\n" + json.dumps(ZUSTAND)) == ZUSTAND and parse_game_status("kaputt") is None


@pytest.fixture
def window(app, tmp_path, example_preview):
    w = MainWindow(make_project(tmp_path), auto_preview=False)
    w.resize(1400, 900)
    w.show()
    w.install_preview(example_preview)
    yield w
    if w.build_dialog:
        w.build_dialog.close()
    w.document.undo.setClean()
    w.close()
    setze_sprache("de")


def fake_status(tmp_path, state=ZUSTAND):
    path = tmp_path / "zustand.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    return lambda root: [sys.executable, "-c", f"print(open({str(path)!r}, encoding='utf-8').read())"]


def test_dialog_zeigt_alle_codes_mit_zustand(window, tmp_path):
    window.open_build()
    d = window.build_dialog
    assert [d.code.itemData(i) for i in range(d.code.count())] == list(BUILD_CODES)
    assert d.code.currentData() == "sochi" and d.status_job is None          # Tests lesen das Spiel nicht
    d.read_game, d.status_command = True, fake_status(tmp_path)
    d.refresh_game_status()
    assert "wird gelesen" in d.code_info.text()
    wait_until(lambda: d.status_job is None)
    assert d.code.itemText(0).endswith("im Spiel") and "im Spiel" not in d.code.itemText(1)
    assert "invitational_image_10" in d.code_info.text()
    d.code.setCurrentIndex(d.code.findData("hanoi"))
    assert "noch nicht im Spiel" in d.code_info.text() and d.options()["streckencode"] == "hanoi"
    assert "hanoi" in d.profile_hint.text()
    tips = {d.code.itemData(i): d.code.itemData(i, 0x03) for i in range(d.code.count())}   # Qt.ToolTipRole
    assert all(tips[c] for c, _, _ in TRACK_CODES)


def test_sprachwechsel_mit_offenem_baudialog(window, tmp_path):
    window.open_build()
    d = window.build_dialog
    d.read_game, d.status_command = True, fake_status(tmp_path)
    d.refresh_game_status()
    wait_until(lambda: d.status_job is None)
    d.name.setText("testedit9_kurven")
    d.note.setText("Turn 4 enger")
    d.code.setCurrentIndex(d.code.findData("paul_ricard"))
    window.change_language("en")
    neu = window.build_dialog
    assert neu is not d and neu.windowTitle() == "Build / package"
    assert neu.name.text() == "testedit9_kurven" and neu.note.text() == "Turn 4 enger"
    assert neu.code.currentData() == "paul_ricard" and neu.code.itemText(0).endswith("in game")
    assert "is not in the game yet" in neu.code_info.text()
    window.change_language("de")
    assert window.build_dialog.code.currentData() == "paul_ricard"
