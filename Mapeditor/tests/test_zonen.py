"""Eigene DRS- und Straight-Mode-Zonen: anlegen, ändern, auf der Karte ziehen – mit echter Maus/Tastatur."""

import copy

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton

from test_journeys import current_preview, edit_field, journey, undo  # noqa: F401  (Fixture)


def choose_category(w, key):
    combo = w.category
    index = combo.findData(key)
    assert index >= 0
    combo.setFocus()
    combo.setCurrentIndex(index)       # Auswahl im Kombifeld wie per Tastatur
    QTest.qWait(10)


def press_button(w, text):
    button = next(b for b in w.findChildren(QPushButton) if b.text() == text)
    assert button.isVisible() and button.isEnabled(), text
    QTest.mouseClick(button, Qt.LeftButton)
    QTest.qWait(25)


def set_station(w, s):
    """Stationscursor über einen Klick ins Höhenprofil setzen (wie ein Nutzer)."""
    w.results.setCurrentWidget(w.height_page)
    QTest.qWait(10)
    length = w.preview["length"]
    rect = w.height.rect()
    # Zeichenbereich des Profils wie in ProfileView: ab x = 65, Breite − 90 px
    x = 65 + (rect.width() - 90) * s / length
    QTest.mouseClick(w.height, Qt.LeftButton, pos=QPointF(x, rect.height() / 2).toPoint())
    QTest.qWait(10)


def test_auto_zones_are_drawn_and_custom_drs_zone_replaces_them(journey):
    w = journey()
    auto = [z for z in w.map.zones if z["art"] == "drs"]
    assert auto and all(z["auto"] for z in auto)                       # Automatik sichtbar (gestrichelt)
    assert any(z["art"] == "xmode" and z["auto"] for z in w.map.zones)  # Träger-Straight-Mode sichtbar
    before = copy.deepcopy(w.document.project.raw)
    choose_category(w, "drs_zonen")
    press_button(w, "DRS-Zone +")
    zones = w.document.project.raw["drs"]["zonen"]
    assert len(zones) == 1
    z = zones[0]
    L = w.preview["length"]
    assert (z["aktivierung_m"] - z["erkennung_m"]) % L == pytest.approx(150, abs=0.2)
    assert (z["ende_m"] - z["aktivierung_m"]) % L == pytest.approx(500, abs=0.2)
    own = [z for z in w.map.zones if z["art"] == "drs"]
    assert len(own) == 1 and not own[0]["auto"]
    assert w.map.zone_selected == ("drs", 0) and len(w.map.zone_handles()) == 3
    edit_field(w, "ende_m", "1234,5")
    assert w.document.project.raw["drs"]["zonen"][0]["ende_m"] == 1234.5
    current_preview(w)
    assert [round(z["ende_s_m"], 1) for z in w.preview["zones"]["drs"]] == [1234.5]
    undo(w)
    undo(w)
    assert w.document.project.raw == before


def test_drag_zone_handle_along_track_and_undo(journey):
    w = journey()
    choose_category(w, "xmode_zonen")
    set_station(w, 1500)
    press_button(w, "Straight Mode +")
    zone = w.document.project.raw["x_mode"]["zonen"][0]
    assert zone["von_m"] == pytest.approx(1500, abs=5)          # am gesetzten Stationscursor
    handles = dict(w.map.zone_handles())
    assert set(handles) == {"von_m", "bis_m"}
    start = handles["bis_m"].toPoint()
    # entlang der Strecke 60 Stationen weiter ziehen
    i = w.map.station_at(zone["bis_m"])
    target_s = w.preview["s"][min(i + 60, len(w.preview["s"]) - 1)]
    p = w.preview["pos"][w.map.station_at(target_s)]
    end = w.map.mapFromScene(QPointF(p[0], p[2]))
    before = copy.deepcopy(w.document.project.raw)
    QTest.mousePress(w.map.viewport(), Qt.LeftButton, pos=start)
    QTest.mouseMove(w.map.viewport(), end, delay=15)
    assert w.document.project.raw == before                         # erst beim Loslassen übernommen
    QTest.mouseRelease(w.map.viewport(), Qt.LeftButton, pos=end)
    QTest.qWait(10)
    moved = w.document.project.raw["x_mode"]["zonen"][0]["bis_m"]
    assert moved == pytest.approx(target_s, abs=w.preview["s"][1] - w.preview["s"][0] + 0.2)
    assert moved != zone["bis_m"]
    undo(w)
    assert w.document.project.raw == before


def test_straight_mode_partial_and_generator_markup(journey):
    w = journey()
    choose_category(w, "xmode_zonen")
    set_station(w, 900)
    press_button(w, "Straight Mode +")
    assert [e[0] for e in w.properties.entries] == ["von_m", "bis_m", "partial", "partial_versatz_m"]
    edit_field(w, "partial", "nein")
    assert w.document.project.raw["x_mode"]["zonen"][0]["partial"] is False
    current_preview(w)
    zones = w.preview["zones"]["xmode"]
    assert len(zones) == 1 and zones[0]["quelle"] == "eingabe" and zones[0]["partial_s_m"] is None
    own = [z for z in w.map.zones if z["art"] == "xmode"]
    assert len(own) == 1 and not own[0]["auto"]
    press_button(w, "Straight Mode −")
    assert not w.document.project.raw["x_mode"]["zonen"]


def test_zone_rules_reported_in_check(journey):
    w = journey()
    choose_category(w, "xmode_zonen")
    set_station(w, 100)
    press_button(w, "Straight Mode +")
    edit_field(w, "bis_m", "120")                                     # 20 m: zu kurz
    current_preview(w)
    ids = {issue["id"] for issue in w.preview["issues"]}
    assert "XMODE_ZONE_UNGUELTIG" in ids and "XMODE_EIGENE_UNGETESTET" in ids


def test_click_on_zone_label_selects_zone(journey):
    w = journey()
    choose_category(w, "drs_zonen")
    press_button(w, "DRS-Zone +")
    choose_category(w, "object")
    w.document.select_many(set())
    w.map.viewport().repaint()
    zone = next(z for z in w.map.zones if z["art"] == "drs")
    assert zone["_anchor"] is not None
    QTest.mouseClick(w.map.viewport(), Qt.LeftButton, pos=(zone["_anchor"] + QPointF(30, 20)).toPoint())
    assert w.category.currentData() == "drs_zonen" and w.map.zone_selected == ("drs", 0)
