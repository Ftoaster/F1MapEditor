"""Werkzeug „Nachzeichnen“ mit Bildvorlage: echte Bedienung im Hauptfenster (Klicks, Tasten, Rückgängig, Übernehmen,
Speichern), Ein-Klick-Runde, zweiter Weg „B“, Bild aus der Zwischenablage und Sprachwechsel."""

import time

import numpy as np
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from mapeditor import nachzeichnen as nz
from mapeditor.bildvorlage import EIN_KLICK_PUNKTE, szene
from mapeditor.project import Project, point_xyz
from mapeditor.window import MainWindow
from test_nachzeichnen import kartenbild, oval

M_PRO_PX = 2.0
pytestmark = pytest.mark.usefixtures("app")


def warten(bedingung, sekunden=40):
    ende = time.time() + sekunden
    while time.time() < ende:
        QApplication.processEvents()
        if bedingung():
            return True
        time.sleep(0.01)
    return False


def fenster(**bild):
    w = MainWindow(auto_preview=False)
    w.show()
    w._bildvorlage_setzen(kartenbild(**bild), "Test")
    QApplication.processEvents()
    return w


def klick(w, u, v, taste=Qt.LeftButton):
    """Mausklick auf Bildpixel (u, v) der Vorlage in der Karte."""
    x, z = szene(w.werkzeug_bild.vorlage, u, v)
    punkt = w.map.mapFromScene(QPointF(x, z))
    QTest.mouseClick(w.map.viewport(), taste, Qt.NoModifier, punkt)
    QApplication.processEvents()


def fertig(w):
    return warten(lambda: not w.werkzeug_bild.rechnet and w.werkzeug_bild.befund is not None)


def test_nachzeichnen_klicken_pruefen_uebernehmen_speichern(tmp_path, monkeypatch):
    w = fenster()
    werkzeug = w.werkzeug_bild
    revision = w.document.revision
    assert w.map.mode == "nachzeichnen" and w.bild_dock.isVisible()
    assert werkzeug.bereit and not w.welcome.isVisible()
    assert w.document.revision == revision                         # nur Bildvorlage: Vorschau bleibt gültig
    werkzeug.massstab(M_PRO_PX, True)
    soll = nz.ring_laenge(oval(n=4000)) * M_PRO_PX
    werkzeug.einstellen("Soll", laenge_soll_m=round(soll))
    for u, v in ((520, 220), (300, 80), (80, 220), (300, 360)):
        klick(w, u, v)
    assert len(werkzeug.punkte) == 4 and not werkzeug.geschlossen
    QTest.keyClick(w.map, Qt.Key_Return)                           # Enter schließt die Runde
    assert werkzeug.geschlossen
    assert fertig(w)
    assert werkzeug.gueltig(), werkzeug.befund
    assert w.bild_panel.uebernehmen_button.isEnabled()
    assert w.bild_panel.pruefliste.text().count("✓") == 4          # geschlossen, Überschneidung, doppelt, vollständig
    assert abs(werkzeug.befund["laenge_m"] / soll - 1) < 0.03
    w.document.undo.undo()                                         # Rückgängig: Runde wieder offen
    assert not werkzeug.geschlossen and not w.bild_panel.uebernehmen_button.isEnabled()
    w.document.undo.redo()
    assert fertig(w) and werkzeug.gueltig()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    w.bild_panel.uebernehmen_button.click()
    ml = w.document.project.raw["mittellinie"]
    assert ml["art"] == "kontrollpunkte" and w.document.project.closed
    xs = [point_xyz(p)[0] for p in ml["punkte"]]
    assert max(xs) - min(xs) == pytest.approx(440 * M_PRO_PX, rel=0.03)   # Oval 440 px breit, 2 m je Pixel
    assert w.map.mode == "auswahl" and w.document.revision > revision
    w.document.project.save(tmp_path)
    assert (tmp_path / "bildvorlage.png").read_bytes() == w.document.project.bildvorlage_daten
    wieder = Project.open(tmp_path)
    assert wieder.bildvorlage_daten == w.document.project.bildvorlage_daten
    assert wieder.manifest["bildvorlage"]["punkte"] == w.document.project.manifest["bildvorlage"]["punkte"]


def test_ein_klick_setzt_stuetzpunkte_entlang_der_runde():
    w = fenster()
    klick(w, 520, 220)
    assert warten(lambda: w.bild_panel.automatisch.isEnabled())
    w.bild_panel.automatisch.click()
    assert warten(lambda: w.werkzeug_bild.geschlossen)
    assert len(w.werkzeug_bild.punkte) == EIN_KLICK_PUNKTE
    assert fertig(w) and w.werkzeug_bild.gueltig()
    w.document.undo.undo()                                         # ein Schritt zurück: wieder nur der Startpunkt
    assert len(w.werkzeug_bild.punkte) == 1


def test_zweiter_weg_b_wird_angeboten_und_per_klick_gewaehlt():
    w = fenster(gasse_breit=True)
    werkzeug = w.werkzeug_bild
    for u, v in ((80, 220), (180, 345), (420, 345)):
        klick(w, u, v)
    assert warten(lambda: not werkzeug.rechnet and any(werkzeug.alternativen.get(werkzeug._paar_key(i, j)) is not None
                                                        for i, j in werkzeug.paare()))
    w.map.viewport().repaint()                                     # Zeichnen legt die Trefferflächen von „B“ an
    index, punkte, pfad = werkzeug._b_treffer[0]
    mitte = punkte[len(punkte) // 2]
    QTest.mouseClick(w.map.viewport(), Qt.LeftButton, Qt.NoModifier, QPointF(*mitte).toPoint())
    QApplication.processEvents()
    assert len(werkzeug.punkte) == 4                               # Punkt auf dem zweiten Weg eingefügt
    neu = np.array(werkzeug.punkte[index + 1]) * werkzeug.faktor
    assert np.min(np.hypot(*(pfad - neu).T)) < 3


def test_bild_aus_zwischenablage_mit_strg_v():
    w = MainWindow(auto_preview=False)
    w.show()
    QApplication.clipboard().setImage(QImage.fromData(kartenbild()))
    QApplication.processEvents()
    w.map.setFocus()
    w.paste_points()                                               # Strg+V in der Karte
    assert w.werkzeug_bild.vorlage is not None and w.map.mode == "nachzeichnen"
    assert w.werkzeug_bild.vorlage["breite_px"] == 600


def test_sprachwechsel_behaelt_bildvorlage_und_werkzeug():
    w = fenster()
    klick(w, 520, 220)
    werkzeug = w.werkzeug_bild
    try:
        w.retranslate("en")
        assert w.werkzeug_bild is werkzeug and w.map.trace is werkzeug
        assert w.map.mode == "nachzeichnen" and len(werkzeug.punkte) == 1
        assert w.bild_dock.windowTitle().startswith("Image")
    finally:
        w.retranslate("de")


def test_nachzeichnen_ohne_vorlage_bleibt_bei_auswahl():
    w = MainWindow(auto_preview=False)
    w.set_mode("nachzeichnen")
    assert w.map.mode == "auswahl" and "Bildvorlage" in w.status_label.text()


def _gespeichert(tmp_path):
    """Projekt mit Bildvorlage und drei Stützpunkten speichern (wie beim Nutzer, dann neu starten)."""
    w = fenster()
    for u, v in ((520, 220), (300, 80), (80, 220)):
        klick(w, u, v)
    w.document.project.save(tmp_path)
    w.document.undo.setClean()
    return tmp_path


def test_start_mit_projekt_laedt_die_bildvorlage(tmp_path):
    """Fehler 3.11.2026: Start mit Projekt auf der Befehlszeile lud das Bild nicht (faktor None) und das Hinweisschild
    brach bei jedem Neuzeichnen mit TypeError ab."""
    w = MainWindow(Project.open(_gespeichert(tmp_path)), auto_preview=False)
    w.show()
    assert w.werkzeug_bild.bereit and len(w.werkzeug_bild.punkte) == 3
    w.set_mode("nachzeichnen")
    assert w.werkzeug_bild.hinweis()
    w.map.viewport().repaint()
    assert warten(lambda: not w.werkzeug_bild.rechnet and len(w.werkzeug_bild.wege) == 2)


def test_fehlendes_bild_gibt_hinweis_statt_fehler(tmp_path):
    ordner = _gespeichert(tmp_path)
    (ordner / "bildvorlage.png").unlink()
    w = MainWindow(Project.open(ordner), auto_preview=False)
    w.show()
    w.set_mode("nachzeichnen")
    assert not w.werkzeug_bild.bereit
    assert "bildvorlage.png" in w.werkzeug_bild.hinweis()
    w.map.viewport().repaint()                                     # Zeichnen ohne Bild bricht nicht ab
    w.bild_panel.aktualisieren()
