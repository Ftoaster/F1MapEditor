"""Kameras für Wiederholungen (4.11.2026): Kartenebene und Eigenschaften „Kameras (Wiederholungen)“.

Die Lage der Trägerkameras kommt hier aus einer kleinen erfundenen Lage (zwei Gruppen), damit der Test ohne Träger
läuft; die Rechnung selbst ist ``strecke.spiel_kameras.plan`` wie im Bau (ego-werkzeuge/tests/kameras_test.py).
"""

from mapeditor.project import Project
from mapeditor.window import MainWindow

from conftest import EXAMPLES


def lage():
    def inst(name, template, f, lokal):
        return {"layer": "cameras_replay_main", "name": "cameras_replay_main$" + name, "gruppe": name.split("_")[0],
                "template": template, "bezug": "haupt", "f": f, "lokal": lokal,
                "achsen": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "punkte": {}, "schwellen": {}}
    return {"schema": 1, "code": "portimao", "laenge_haupt": 4649.0, "laenge_box": None, "splines": {},
            "instanzen": [inst("s01a_trigger", "TriggerZoneBox", 0.1, [0.0, 0.0, 0.0]),
                          inst("s01a_cam", "ReplayCam", 0.12, [30.0, 6.0, 0.0]),
                          inst("s02a_trigger", "TriggerZoneBox", 0.5, [0.0, 0.0, 0.0]),
                          inst("s02a_cam", "ReplayCam", 0.52, [-25.0, 8.0, 0.0])]}


def fenster(app, example_preview):
    w = MainWindow(Project.open(EXAMPLES / "teststrecke.json"), auto_preview=False)
    w.install_preview(example_preview)
    w.set_camera_layout(lage(), "erfunden")
    w.category.setCurrentIndex(w.category.findData("kameras"))
    w._refresh_properties()
    return w


def test_gruppen_und_karte(app, example_preview):
    w = fenster(app, example_preview)
    try:
        assert [g["gruppe"] for g in w.camera_groups] == ["s01a", "s02a"]
        assert w.section_choice.itemText(0) == "Alle Kameras" and w.section_choice.count() == 3
        assert len(w.map.cameras) == 4
        L = example_preview["length"]
        assert abs(w.camera_groups[0]["s"] - 0.1 * L) < 1.0
        w.section_choice.setCurrentIndex(1)
        w._refresh_properties()
        assert [e[0] for e in w.properties.entries] == ["aktiv", "verschiebung_m", "seitlich_m", "hoehe_m"]
        assert w.map.camera_group == "s01a"
    finally:
        w.document.undo.setClean()
        w.close()


def test_gruppe_abschalten_verschieben_und_rueckgaengig(app, example_preview):
    w = fenster(app, example_preview)
    try:
        w.section_choice.setCurrentIndex(2)
        w._refresh_properties()
        w.document.edit("Kamera", lambda raw, meta: w._apply_camera_changes(raw, 2, {"aktiv": False, "verschiebung_m": 40.0}))
        assert w.document.project.raw["kameras"] == {"gruppen": {"s02a": {"aus": True, "verschiebung_m": 40.0}}}
        w._update_camera_overlay()
        zone = next(e for e in w.map.cameras if e["gruppe"] == "s02a" and e["template"] == "TriggerZoneBox")
        assert zone["aus"] and zone["position"][1] < -900
        assert w.camera_groups[1]["aus"]
        w.document.edit("Kamera", lambda raw, meta: w._apply_camera_changes(raw, 2, {"aktiv": True, "verschiebung_m": None}))
        assert "kameras" not in w.document.project.raw
        w.document.undo.undo()
        assert w.document.project.raw["kameras"]["gruppen"]["s02a"]["aus"] is True
        w.document.edit("Alle", lambda raw, meta: w._apply_camera_changes(raw, 0, {"mitfuehren": False}))
        w._update_camera_overlay()
        assert w.map.cameras == []
    finally:
        w.document.undo.setClean()
        w.close()


def test_klick_auf_kamera_waehlt_gruppe(app, example_preview):
    w = fenster(app, example_preview)
    try:
        w.category.setCurrentIndex(w.category.findData("fahrbahn"))
        w._camera_clicked("s02a")
        assert w.category.currentData() == "kameras" and w.section_choice.currentIndex() == 2
        assert "s02a" in w.status_label.text()
    finally:
        w.document.undo.setClean()
        w.close()


def test_werkzeug_automatik_und_ziehen(app, example_preview):
    """Werkzeug „Kameras“: Kategorie folgt, Automatik schreibt Positionen, Ziehen speichert Station/Querabstand."""
    w = fenster(app, example_preview)
    try:
        l = lage()
        l["verknuepfung"] = {"cameras_replay_main$s01a_trigger": ["cameras_replay_main$s02a_cam"],
                             "cameras_replay_main$s02a_trigger": ["cameras_replay_main$s01a_cam"]}
        w.set_camera_layout(l, "erfunden")
        w.category.setCurrentIndex(w.category.findData("fahrbahn"))
        w.set_mode("kameras")
        assert w.category.currentData() == "kameras" and w.mode_actions["kameras"].isChecked()
        w._refresh_properties()
        assert w.camera_auto_button.isVisible() or not w.isVisible()
        w.camera_auto()
        objekte = w.document.project.raw["kameras"]["objekte"]
        assert set(objekte) == {"cameras_replay_main$s01a_trigger", "cameras_replay_main$s02a_trigger",
                                "cameras_replay_main$s01a_cam", "cameras_replay_main$s02a_cam"}
        kam = objekte["cameras_replay_main$s02a_cam"]
        zone = objekte["cameras_replay_main$s01a_trigger"]
        assert abs(kam["quer"]) == 28.0 and abs(((kam["s"] - zone["s"]) % example_preview["length"]) - 110.0) < 1.0
        z = next(e for e in w.map.cameras if e["name"] == "cameras_replay_main$s01a_trigger")
        w._camera_moved(z["name"], z["template"], z["position"][0], z["position"][2])
        assert set(w.document.project.raw["kameras"]["objekte"][z["name"]]) == {"s"}
        k = next(e for e in w.map.cameras if e["name"] == "cameras_replay_main$s01a_cam")
        w._camera_moved(k["name"], k["template"], k["position"][0], k["position"][2])
        neu = w.document.project.raw["kameras"]["objekte"][k["name"]]
        assert set(neu) == {"s", "quer"}
        k2 = next(e for e in w.map.cameras if e["name"] == k["name"])
        assert abs(k2["position"][0] - k["position"][0]) < 0.5 and abs(k2["position"][2] - k["position"][2]) < 0.5
        w.camera_reset()
        assert "kameras" not in w.document.project.raw
        w.document.undo.undo()
        assert w.document.project.raw["kameras"]["objekte"]
    finally:
        w.document.undo.setClean()
        w.close()
