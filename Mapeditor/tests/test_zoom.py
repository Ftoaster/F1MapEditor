"""Mausrad-Zoom der Karte (4.11.2026): stufenlos nach Raddrehung, Punkt unter der Maus bleibt stehen."""

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent

from mapeditor.project import Project
from mapeditor.window import MainWindow

from conftest import EXAMPLES


def rad(app, m, dy, pos):
    e = QWheelEvent(pos, m.viewport().mapToGlobal(pos.toPoint()), QPoint(0, 0), QPoint(0, dy), Qt.NoButton,
                    Qt.NoModifier, Qt.NoScrollPhase, False)
    app.sendEvent(m.viewport(), e)


def test_mausrad_zoom(app, example_preview):
    w = MainWindow(Project.open(EXAMPLES / "teststrecke.json"), auto_preview=False)
    try:
        w.install_preview(example_preview)
        w.resize(1400, 900)
        w.show()
        app.processEvents()
        m, p = w.map, QPointF(300, 200)
        s0 = m.transform().m11()
        for _ in range(8):                      # eine Raste eines feinen Rads / Touchpads
            rad(app, m, 15, p)
        assert abs(m.transform().m11() / s0 - 1.2) < 0.01
        for dy in (120, -120, -120):
            vorher = m.mapToScene(p.toPoint())
            rad(app, m, dy, p)
            nachher = m.mapToScene(p.toPoint())
            assert abs(vorher.x() - nachher.x()) * m.transform().m11() < 2.5
            assert abs(vorher.y() - nachher.y()) * m.transform().m11() < 2.5
        for _ in range(60):                     # Untergrenze: Szene füllt das Fenster
            rad(app, m, -120, p)
        assert m.transform().m11() * m.sceneRect().width() >= m.viewport().width() - 1
    finally:
        w.document.undo.setClean()
        w.close()
