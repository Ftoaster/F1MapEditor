"""Spielordner von F1 25: einmal gewählt, programmweit gemerkt.

Der Werkzeugkasten sucht das Spiel selbst (Umgebungsvariable ``EGO_SPIELORDNER``, Steam-Registry,
``libraryfolders.vdf``, übliche Ordner). Findet er es nicht (andere Plattform, umgezogene Bibliothek), wählt der Nutzer
den Ordner einmal; der Editor speichert ihn in den Programmeinstellungen (``spiel/ordner``) und gibt ihn über
``EGO_SPIELORDNER`` an alle Arbeitsprozesse weiter.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

ENV = "EGO_SPIELORDNER"
SCHLUESSEL = "spiel/ordner"
ERKENNUNG = Path("2025_asset_groups") / "environment_package" / "tracks"


def ist_spielordner(pfad) -> bool:
    return bool(pfad) and (Path(pfad) / ERKENNUNG).is_dir()


def normalisieren(pfad) -> Optional[str]:
    """Gewählten Ordner auf die Installation zurückführen: der Ordner selbst, ``…/F1 25`` darin oder ``steamapps/common/F1 25``."""
    if not pfad:
        return None
    basis = Path(pfad)
    for kandidat in (basis, basis / "F1 25", basis / "common" / "F1 25", basis / "steamapps" / "common" / "F1 25",
                     basis.parent):
        if ist_spielordner(kandidat):
            return str(kandidat)
    return None


def gemerkt(store=None) -> Optional[str]:
    if store is None:
        from .ansicht import settings
        store = settings()
    wert = store.value(SCHLUESSEL) or None
    return str(wert) if wert and ist_spielordner(wert) else None


def merken(pfad, store=None) -> None:
    if store is None:
        from .ansicht import settings
        store = settings()
    store.setValue(SCHLUESSEL, str(pfad))
    os.environ[ENV] = str(pfad)


def anwenden(store=None) -> Optional[str]:
    """Beim Start: gemerkten Ordner an die Arbeitsprozesse weitergeben. Eine gesetzte Umgebungsvariable bleibt."""
    if os.environ.get(ENV):
        return os.environ[ENV]
    pfad = gemerkt(store)
    if pfad:
        os.environ[ENV] = pfad
    return pfad


def ist_fehlt_meldung(text) -> bool:
    text = str(text).casefold()
    return "game folder not found" in text or "spielordner nicht gefunden" in text


def waehlen(parent=None) -> Optional[str]:
    """Ordnerauswahl mit Prüfung; gibt den gemerkten Spielordner zurück oder None (abgebrochen/ungültig)."""
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    from .i18n import tr
    start = os.environ.get(ENV) or ""
    while True:
        gewaehlt = QFileDialog.getExistingDirectory(parent, tr("Choose the F1 25 game folder"), start)
        if not gewaehlt:
            return None
        pfad = normalisieren(gewaehlt)
        if pfad:
            merken(pfad)
            return pfad
        antwort = QMessageBox.warning(
            parent, tr("Not an F1 25 folder"),
            tr("{ordner} does not contain an F1 25 installation (2025_asset_groups is missing). Choose the folder "
               "that contains F1_25.exe, for example …\\steamapps\\common\\F1 25.", ordner=gewaehlt),
            QMessageBox.StandardButton.Retry | QMessageBox.StandardButton.Cancel)
        if antwort != QMessageBox.StandardButton.Retry:
            return None
        start = gewaehlt


def anbieten(parent=None) -> Optional[str]:
    """Nach „Game folder not found“: fragen, ob der Nutzer den Ordner jetzt wählt."""
    from PySide6.QtWidgets import QMessageBox
    from .i18n import tr
    antwort = QMessageBox.question(
        parent, tr("F1 25 not found"),
        tr("The editor could not find F1 25 in your Steam libraries. Choose the game folder now? The editor "
           "remembers it."),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    return waehlen(parent) if antwort == QMessageBox.StandardButton.Yes else None
