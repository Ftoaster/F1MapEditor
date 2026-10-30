"""Abnahmetor und Dateisicherheit: Quellformat und Zusatzfelder erhalten."""

import copy
import json

import pytest

from mapeditor import project as project_module
from mapeditor.project import Project, encoded, point_xyz, read_json, set_point
from conftest import EXAMPLE_NAMES, EXAMPLES


@pytest.mark.parametrize("name", EXAMPLE_NAMES)
def test_example_roundtrip_unchanged(name, tmp_path):
    source = EXAMPLES / name
    original_bytes = source.read_bytes()
    original = read_json(source)
    project = Project.open(source)
    project.save(tmp_path / "projekt")
    reopened = Project.open(tmp_path / "projekt")
    assert reopened.raw == original
    assert read_json(tmp_path / "projekt" / "definition.json") == original
    assert source.read_bytes() == original_bytes
    if name == "portimao_form.json":
        assert len(reopened.raw["mittellinie"]["punkte"]) == 669


def test_nested_unknown_fields_and_manifest(point_project, tmp_path):
    point_project.manifest["fremde_erweiterung"] = {"wert": [1.23456789012345, False]}
    point_project.raw["mittellinie"]["zukunft"] = {"namen": ["ö", "ß"]}
    before = copy.deepcopy((point_project.raw, point_project.manifest))
    point_project.save(tmp_path)
    result = Project.open(tmp_path / "projekt.json")
    assert (result.raw, result.manifest) == before


def test_future_schema_readonly(point_project, tmp_path):
    point_project.manifest["schema"] = 99
    (tmp_path / "projekt.json").write_bytes(encoded(point_project.manifest))
    (tmp_path / "definition.json").write_bytes(encoded(point_project.raw))
    loaded = Project.open(tmp_path)
    assert loaded.readonly
    with pytest.raises(ValueError, match="nur lesend"):
        loaded.save()


@pytest.mark.parametrize("path", ["../a.json", "projekt.json", "C:/a.json"])
def test_manifest_path_must_be_local(point_project, tmp_path, path):
    point_project.manifest["definition"] = path
    with pytest.raises(ValueError, match="innerhalb"):
        point_project.save(tmp_path)


def test_save_as_independent(point_project, tmp_path):
    point_project.save(tmp_path / "eins")
    point_project.raw["name"] = "Andere Runde"
    point_project.save(tmp_path / "zwei")
    assert Project.open(tmp_path / "eins").raw["name"] == "Prüfrunde"
    assert Project.open(tmp_path / "zwei").raw["name"] == "Andere Runde"


def test_failed_save_restores_pair(point_project, tmp_path, monkeypatch):
    point_project.save(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    point_project.raw["name"] = "Nicht gespeichert"
    original = project_module.atomic_write
    calls = 0
    def fail_once(path, data):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("Datenträgerfehler")
        original(path, data)
    monkeypatch.setattr(project_module, "atomic_write", fail_once)
    with pytest.raises(OSError):
        point_project.save()
    assert {p: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_csv_relative_attachment_copied(tmp_path):
    source, target = tmp_path / "quelle", tmp_path / "kopie"
    source.mkdir()
    (source / "linie.csv").write_text("x,y,z\n0,0,0", encoding="utf-8")
    raw = {"mittellinie": {"art": "csv", "datei": "linie.csv"}}
    (source / "quelle.json").write_bytes(encoded(raw))
    project = Project.open(source / "quelle.json")
    project.save(target)
    assert (target / "linie.csv").read_bytes() == (source / "linie.csv").read_bytes()
    assert Project.open(target).raw == raw


@pytest.mark.parametrize("point,kwargs,expected", [
    ([1, 2], {"y": 7}, [1, 2, 7]),
    ([1, 2, 3], {"x": 9}, [9, 2, 3]),
    ({"x": 1, "z": 2, "hoehe": 3, "foo": 4}, {"y": 5}, {"x": 1, "z": 2, "hoehe": 5, "foo": 4}),
])
def test_point_representation(point, kwargs, expected):
    assert set_point(point, **kwargs) == expected


def test_reject_nonfinite_json(point_project, tmp_path):
    point_project.raw["fehler"] = float("nan")
    with pytest.raises(ValueError):
        point_project.save(tmp_path)
    assert not (tmp_path / "projekt.json").exists()
