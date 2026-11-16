"""Fahrbahnkatalog (fahrbahn.json) und ERP-Kopier-Backend (sticktor PR #7)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ego-werkzeuge"))

from strecke import asphalt_vorlagen  # noqa: E402


def test_fahrbahn_katalog_liegt_im_repo():
    daten = asphalt_vorlagen.katalog_laden()
    assert daten.get("version") == 2
    asphalt = daten["asphalt"]
    assert "traeger" in asphalt["vorlagen"]
    assert asphalt["vorlagen"]["traeger"].get("traeger") is True
    assert "abu_dhabi" in asphalt["vorlagen"]
    assert "monza" in asphalt["vorlagen"]["abu_dhabi"]["aliase"]
    skid = daten["skid"]
    assert skid["vorlagen"]["traeger"].get("traeger") is True
    assert skid["reihenfolge"][0] == "traeger"
    assert "abu_dhabi" in skid["vorlagen"] and "imola" in skid["vorlagen"] and "losail" in skid["vorlagen"]
    assert "bahrain" not in skid["vorlagen"] and "silverstone" not in skid["vorlagen"]
    assert "bahrain" in skid["vorlagen"]["imola"]["aliase"]
    assert "silverstone" in skid["vorlagen"]["losail"]["aliase"]
    assert "hungaroring" in asphalt["vorlagen"]["traeger"]["aliase"]
    assert "type1" in skid["vorlagen"]["abu_dhabi"]["aliase"]


def test_alias_aufloesen_und_traeger_noop():
    key, ein = asphalt_vorlagen._aufloesen(asphalt_vorlagen.asphalt_katalog(), "monza")
    assert key == "abu_dhabi" and ein["strecke"] == "abu_dhabi"
    key, ein = asphalt_vorlagen._aufloesen(asphalt_vorlagen.asphalt_katalog(), "type1")
    assert key == "abu_dhabi"
    with pytest.raises(asphalt_vorlagen.VorlagenFehler, match="Unknown"):
        asphalt_vorlagen._aufloesen(asphalt_vorlagen.asphalt_katalog(), "gibt_es_nicht")
    assert asphalt_vorlagen.einsetzen(None, "traeger") == {
        "vorlage": "traeger", "traeger": True, "mipmaps": []}
    assert asphalt_vorlagen.skid_einsetzen(None, "type5")["traeger"] is True
    assert asphalt_vorlagen._aufloesen(asphalt_vorlagen.skid_katalog(), "miami")[0] == "losail"
    assert asphalt_vorlagen._aufloesen(asphalt_vorlagen.skid_katalog(), "bahrain")[0] == "imola"
    assert asphalt_vorlagen._aufloesen(asphalt_vorlagen.asphalt_katalog(), "hungaroring")[0] == "traeger"


@pytest.fixture
def window(app, example_preview):
    from mapeditor.project import Project
    from mapeditor.window import MainWindow
    from conftest import EXAMPLES

    project = Project.open(EXAMPLES / "teststrecke.json")
    w = MainWindow(project, auto_preview=False)
    w.install_preview(example_preview)
    yield w
    w.document.undo.setClean()
    w.close()


def test_build_dialog_fuellt_fahrbahn(window):
    """Combo-Boxen lesen fahrbahn.json und zeigen Streckennamen; Standard = Träger."""
    w = window
    w.open_build()
    d = w.build_dialog
    assert d.asphalt.count() >= 10
    assert d.asphalt.itemData(0) == "traeger"
    assert "Abu Dhabi" in d.asphalt.itemText(d.asphalt.findData("abu_dhabi"))
    assert d.asphalt.currentData() == "traeger"
    assert d.skid.count() >= 2
    assert d.skid.itemData(0) == "traeger"
    assert d.skid.currentData() == "traeger"
    # Alias aus altem Spielstand / skid_vergleich-Namen
    d._fill_skid("type5")
    assert d.skid.currentData() == "traeger"
    d._fill_skid("miami")
    assert d.skid.currentData() == "losail"
    d._fill_skid("bahrain")
    assert d.skid.currentData() == "imola"
    assert d.skid.count() >= 4
    d._fill_asphalt("monza")
    assert d.asphalt.currentData() == "abu_dhabi"
    d._fill_asphalt("hungaroring")
    assert d.asphalt.currentData() == "traeger"


def test_fahrbahn_json_ohne_spieltexturen():
    """Katalog darf keine extrahierten Spieltexturen enthalten (nur eaid + Vorschau-Dateinamen)."""
    roh = (ROOT / "ego-werkzeuge" / "strecke" / "daten" / "fahrbahn.json").read_text(encoding="utf-8")
    assert "asphalt_texturen" not in roh
    daten = json.loads(roh)
    for key, ein in daten["asphalt"]["vorlagen"].items():
        if ein.get("traeger"):
            continue
        assert ein.get("slots"), key
        assert all(v.startswith("eaid://") for v in ein["slots"].values()), key
        assert "/" not in (ein.get("vorschau") or ""), key
    for key, ein in daten["skid"]["vorlagen"].items():
        if ein.get("traeger"):
            continue
        assert (ein.get("quelle") or "").startswith("eaid://"), key


def test_skid_katalog_aus_roh_merged_per_md5():
    """analysieren-Pfad: gleiche Skid-Textur-Hash → eine Gruppe; Portimão → traeger."""
    roh = {
        "portimao": {"quelle": "eaid://skid/portimao", "finger": "aaa"},
        "abu_dhabi": {"quelle": "eaid://skid/a", "finger": "bbb"},
        "monza": {"quelle": "eaid://skid/a", "finger": "bbb"},
        "bahrain": {"quelle": "eaid://skid/b", "finger": "ccc"},
        "imola": {"quelle": "eaid://skid/b", "finger": "ccc"},
        "hungaroring": {"quelle": "eaid://skid/portimao", "finger": "aaa"},
    }
    alt = {"vorlagen": {
        "abu_dhabi": {"quelle": "eaid://skid/a", "aliase": ["type1"], "vorschau": "abu_dhabi.png"},
        "imola": {"quelle": "eaid://skid/b", "aliase": ["type3", "bahrain"], "vorschau": "imola.png"},
        "traeger": {"traeger": True, "aliase": ["type5"], "vorschau": "traeger.png"},
    }}
    skid = asphalt_vorlagen.skid_katalog_aus_roh(roh, alt)
    assert skid["reihenfolge"][0] == "traeger"
    assert "hungaroring" in skid["vorlagen"]["traeger"]["aliase"]
    assert "type5" in skid["vorlagen"]["traeger"]["aliase"]
    assert "monza" in skid["vorlagen"]["abu_dhabi"]["aliase"]
    assert "type1" in skid["vorlagen"]["abu_dhabi"]["aliase"]
    assert skid["vorlagen"]["abu_dhabi"]["vorschau"] == "abu_dhabi.png"
    assert "bahrain" in skid["vorlagen"]["imola"]["aliase"] or "bahrain" in skid["vorlagen"]["bahrain"]["aliase"]
    assert "portimao" not in skid["vorlagen"] or skid["vorlagen"].get("portimao", {}).get("traeger")


def test_fahrbahn_pruefregeln_unbekannt_und_spielordner(monkeypatch):
    from strecke import pruefregeln, umgebung

    m = []
    gp = pruefregeln.fahrbahn_pruefen("gibt_es_nicht", "traeger", m)
    assert "FAHRBAHN_UNBEKANNT" in gp
    assert any(x["id"] == "FAHRBAHN_UNBEKANNT" for x in m)

    m2 = []
    monkeypatch.setattr(umgebung, "spielordner_finden", lambda *a, **k: None)
    gp2 = pruefregeln.fahrbahn_pruefen("abu_dhabi", "traeger", m2)
    assert "FAHRBAHN_SPIELORDNER" in gp2
    assert any(x["id"] == "FAHRBAHN_SPIELORDNER" for x in m2)

    m3 = []
    monkeypatch.setattr(umgebung, "spielordner_finden", lambda *a, **k: None)
    gp3 = pruefregeln.profil_pruefen(asphalt_vorlage="traeger", skid_vorlage="traeger", m=m3)
    assert "FAHRBAHN_UNBEKANNT" in gp3 and "FAHRBAHN_SPIELORDNER" in gp3
    assert not any(x["id"] in ("FAHRBAHN_UNBEKANNT", "FAHRBAHN_SPIELORDNER") for x in m3)
