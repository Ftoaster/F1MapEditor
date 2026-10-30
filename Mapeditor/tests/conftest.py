"""Gemeinsame Qt-Umgebung und unveränderte Beispiele aus dem Werkzeugkasten."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

import tempfile

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtWidgets import QApplication

# Einstellungen (Ansicht, zuletzt geöffnet, Teiler) nie im echten Benutzerprofil: eigene INI-Datei je Testlauf
QCoreApplication.setOrganizationName("MapeditorTest")
QCoreApplication.setApplicationName("MapeditorTest")
QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, tempfile.mkdtemp(prefix="mapeditor_einstellungen_"))

from mapeditor.adapter import calculate_preview
from mapeditor.project import Project


ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "ego-werkzeuge" / "strecke" / "beispiele"
#: Portimãos Streckenverlauf stammt aus den Spieldaten und fehlt im öffentlichen Stand
EXAMPLE_NAMES = [n for n in ("teststrecke.json", "portimao_form.json") if (EXAMPLES / n).exists()]
SCREENSHOTS = ROOT / "Mapeditor" / "docs" / "screenshots"


def doc_screenshot(widget, name):
    """Doku-Bild eines Widgets: immer aufnehmen (prüft das Zeichnen), speichern nach docs/screenshots nur mit
    ``MAPEDITOR_SCREENSHOTS=1`` – normale Testläufe ändern keine Doku-Dateien (27.10.2026)."""
    image = widget.grab()
    assert not image.isNull()
    if os.environ.get("MAPEDITOR_SCREENSHOTS") == "1":
        SCREENSHOTS.mkdir(parents=True, exist_ok=True)
        assert image.save(str(SCREENSHOTS / name))


@pytest.fixture(autouse=True)
def fenster_aufraeumen():
    """Nach jedem Test alle Fenster schließen und löschen (27.10.2026). Offen gebliebene Fenster früherer Tests
    machten app-weite Änderungen (Thema, Sprache) in der vollen Reihe bis zu 400× langsamer (66 s statt 0,2 s)."""
    yield
    application = QApplication.instance()
    if application is None:
        return
    for widget in application.topLevelWidgets():
        undo = getattr(getattr(widget, "document", None), "undo", None)
        if undo is not None:
            undo.setClean()                       # keine Rückfrage „Änderungen verwerfen?“
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    application.processEvents()


@pytest.fixture(scope="session")
def app():
    application = QApplication.instance() or QApplication([])
    application.setStyle("Fusion")
    yield application


@pytest.fixture(scope="session")
def example_preview():
    project = Project.open(EXAMPLES / "teststrecke.json")
    return calculate_preview({"raw": project.raw, "basis": str(project.basis), "shift": [-591, 0, -473]})


@pytest.fixture
def point_project():
    return Project({"name": "Prüfrunde", "unbekannt": {"behalten": [1, "ä", None]},
                    "mittellinie": {"art": "kontrollpunkte", "punkte": [
                        {"x": 0, "z": 0, "hoehe": 2, "zusatz": [7]}, [200, 0, 4], [200, 200], [0, 200, 8]]}},
                   {"schema": 1, "definition": "definition.json", "runde_geschlossen": True})


@pytest.fixture(autouse=True)
def frische_einstellungen():
    """Jeder Test beginnt mit leeren Einstellungen (Ansicht, zuletzt geöffnet, Teiler)."""
    QSettings().clear()
    yield
    QSettings().clear()
