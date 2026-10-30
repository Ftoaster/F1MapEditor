"""Spielordner und geschützte Ordner, ohne feste Laufwerke oder Benutzernamen.

    kandidaten()      mögliche Spielordner: Umgebungsvariable ``EGO_SPIELORDNER``, dann Steam-Orte auf allen Laufwerken
    finden()          erster vorhandener Spielordner oder None
    geschuetzt(pfad)  True für Spielordner und den Downloads-Ordner des Benutzers: dorthin schreibt der Bau nie
"""

import os
import string
from pathlib import Path
from typing import List, Optional

SPIEL_REL = os.path.join("steamapps", "common", "F1 25")
STEAM_ORTE = ("SteamLibrary", "Steam", os.path.join("Program Files (x86)", "Steam"), os.path.join("Program Files", "Steam"),
              os.path.join("Games", "Steam"), os.path.join("Games", "SteamLibrary"))
#: Anzeige, wenn das Spiel nicht gefunden wird
STANDARD = os.path.join(r"C:\Program Files (x86)\Steam", SPIEL_REL)


def kandidaten() -> List[str]:
    gefunden = [os.environ["EGO_SPIELORDNER"]] if os.environ.get("EGO_SPIELORDNER") else []
    if os.name != "nt":
        return gefunden
    for laufwerk in string.ascii_uppercase[2:]:      # A und B sind Disketten-Reservierungen und bremsen die Suche
        wurzel = laufwerk + ":\\"
        if os.path.isdir(wurzel):
            gefunden += [os.path.join(wurzel, ort, SPIEL_REL) for ort in STEAM_ORTE]
    return gefunden


def finden() -> Optional[str]:
    return next((p for p in kandidaten() if os.path.isdir(p)), None)


def geschuetzt(pfad) -> bool:
    ziel = Path(pfad).expanduser().resolve()
    teile = [p.casefold() for p in ziel.parts]
    if any(teile[i:i + 3] == ["steamapps", "common", "f1 25"] for i in range(len(teile))):
        return True
    downloads = (Path.home() / "Downloads").resolve()
    return ziel == downloads or downloads in ziel.parents
