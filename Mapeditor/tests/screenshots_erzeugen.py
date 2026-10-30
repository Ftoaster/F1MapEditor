"""Reproduzierbare echte Qt-Screenshots; kein Spiel- oder Paketbau."""

import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

from PySide6.QtCore import QElapsedTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mapeditor.project import Project
from mapeditor.window import MainWindow


def main():
    root = Path(__file__).resolve().parents[1]
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    window = MainWindow(Project.open(root.parent / "ego-werkzeuge/strecke/beispiele/teststrecke.json"), auto_preview=False)
    # Dokumentiertes v17-Profil; allein im Screenshotentwurf, keine Quelldatei ändern.
    window.document.project.manifest["versatz"] = [-591, 0, -473]
    window.resize(1600, 1000)
    window.show()
    window.request_preview()
    timer = QElapsedTimer()
    timer.start()
    while window.job and timer.elapsed() < 15000:
        QTest.qWait(20)
    if not window.preview:
        raise RuntimeError(window.status_label.text())
    window.document.select(6)
    window.map.fit_track()
    QTest.qWait(80)
    target = root / "docs/screenshots"
    target.mkdir(parents=True, exist_ok=True)
    assert window.grab().save(str(target / "hauptansicht_teststrecke.png"))
    assert window.height.grab().save(str(target / "hoehenprofil_teststrecke.png"))
    window.results.setCurrentWidget(window.cross_page)
    QTest.qWait(30)
    assert window.grab().save(str(target / "querprofil_teststrecke.png"))
    window.set_theme("hell")
    QTest.qWait(30)
    assert window.grab().save(str(target / "hauptansicht_hell.png"))
    window.document.undo.setClean()
    window.close()
    print(f"Vier Screenshots unter {target}")


if __name__ == "__main__":
    main()
