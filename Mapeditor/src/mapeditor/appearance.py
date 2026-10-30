"""Schriften für normale Windows- und Offscreen-Sitzungen bereitstellen."""

import os
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase


def ensure_fonts(application):
    # Das Qt-Offscreen-Plugin entdeckt unter Windows nicht immer Systemschriften.
    # Nur lesen und registrieren; keine Schriftdateien ins Projekt kopieren.
    if not QFontDatabase.families():
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf"):
            path = fonts / name
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))
    application.setFont(QFont("Segoe UI", 10))
