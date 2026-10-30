"""Undo/Redo erhält unbekannte Inhalte und bündelt Gesten."""

import copy

import pytest

from mapeditor.document import Document
from mapeditor.project import Project, point_xyz


def test_move_undo_redo_unknown_fields(app, point_project):
    doc = Document(point_project)
    before = copy.deepcopy(point_project.raw)
    doc.move(0, x=12, y=9)
    assert doc.undo.count() == 1
    point = point_project.raw["mittellinie"]["punkte"][0]
    assert point["hoehe"] == 9 and point["zusatz"] == [7] and "y" not in point
    doc.undo.undo()
    assert point_project.raw == before
    doc.undo.redo()
    assert point_xyz(doc.points[0]) == (12, 9, 0)


def test_insert_delete_round_state_undo(app, point_project):
    doc = Document(point_project)
    doc.select(0)
    doc.insert()
    assert point_xyz(doc.points[1]) == (100, 3, 0)
    doc.delete()
    doc.select(0)
    doc.delete()
    assert not point_project.closed
    assert len(doc.points) == 3
    doc.undo.undo()
    assert point_project.closed and len(doc.points) == 4


def test_empty_round_can_be_drawn_and_closed(app):
    doc = Document()
    with pytest.raises(ValueError, match="mindestens vier"):
        doc.close_round()
    for x, z in [(0, 0), (100, 0), (100, 100), (0, 100)]:
        doc.insert(x, z)
    doc.close_round()
    assert doc.project.closed
    doc.undo.undo()
    assert not doc.project.closed


def test_new_change_discards_redo(app, point_project):
    doc = Document(point_project)
    doc.move(0, x=1)
    doc.move(0, x=2)
    doc.undo.undo()
    doc.move(0, x=3)
    assert not doc.undo.canRedo()


def test_clean_marker_tracks_saved_revision(app, point_project, tmp_path):
    doc = Document(point_project)
    doc.move(0, x=1)
    point_project.save(tmp_path)
    doc.undo.setClean()
    assert doc.undo.isClean()
    doc.move(0, x=2)
    assert not doc.undo.isClean()
    doc.undo.undo()
    assert doc.undo.isClean()


def test_readonly_prevents_edits(app, point_project):
    point_project.readonly = True
    before = copy.deepcopy(point_project.raw)
    doc = Document(point_project)
    doc.move(0, x=999)
    assert point_project.raw == before and doc.undo.count() == 0
