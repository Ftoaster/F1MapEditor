"""Spielordner und geschützte Ordner, ohne feste Laufwerke oder Benutzernamen.

    kandidaten()      mögliche Spielordner: Umgebungsvariable ``EGO_SPIELORDNER``, dann die Steam-Bibliotheken
                      (Registry + ``libraryfolders.vdf``), zuletzt übliche Steam-Orte auf allen Laufwerken
    steam_bibliotheken()  alle Steam-Bibliotheksordner des Benutzers
    ist_spielordner(p) True, wenn ``p`` eine F1-25-Installation ist
    finden()          erster vorhandener Spielordner oder None
    geschuetzt(pfad)  True für Spielordner und den Downloads-Ordner des Benutzers: dorthin schreibt der Bau nie
"""

import os
import re
import string
from pathlib import Path
from typing import List, Optional

SPIEL_REL = os.path.join("steamapps", "common", "F1 25")
STEAM_ORTE = ("SteamLibrary", "Steam", os.path.join("Program Files (x86)", "Steam"), os.path.join("Program Files", "Steam"),
              os.path.join("Games", "Steam"), os.path.join("Games", "SteamLibrary"))
#: Anzeige, wenn das Spiel nicht gefunden wird
STANDARD = os.path.join(r"C:\Program Files (x86)\Steam", SPIEL_REL)


#: Unterordner, an dem eine F1-25-Installation erkannt wird
ERKENNUNG = os.path.join("2025_asset_groups", "environment_package", "tracks")


def ist_spielordner(pfad) -> bool:
    return bool(pfad) and os.path.isdir(os.path.join(pfad, ERKENNUNG))


def _steam_wurzeln() -> List[str]:
    """Steam-Installationsordner aus der Registry (Benutzer- und Maschinenschlüssel)."""
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []
    wurzeln = []
    for haupt, schluessel, wert in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                                    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                                    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")):
        try:
            with winreg.OpenKey(haupt, schluessel) as k:
                pfad = winreg.QueryValueEx(k, wert)[0]
        except OSError:
            continue
        if isinstance(pfad, str) and pfad:
            wurzeln.append(os.path.normpath(pfad))
    return wurzeln


def bibliotheken_aus_vdf(text: str) -> List[str]:
    """``"path"``-Einträge aus ``libraryfolders.vdf`` (Valve-KeyValues, Backslashes doppelt maskiert)."""
    return [os.path.normpath(m.replace("\\\\", "\\")) for m in re.findall(r'"path"\s+"([^"]+)"', text)]


def steam_bibliotheken() -> List[str]:
    """Alle Steam-Bibliotheken: Steam-Ordner aus der Registry und die Einträge seiner ``libraryfolders.vdf``."""
    gesehen, aus = set(), []
    for wurzel in _steam_wurzeln():
        orte = [wurzel]
        for vdf in (os.path.join(wurzel, "steamapps", "libraryfolders.vdf"), os.path.join(wurzel, "config", "libraryfolders.vdf")):
            try:
                with open(vdf, encoding="utf-8", errors="replace") as f:
                    orte += bibliotheken_aus_vdf(f.read())
            except OSError:
                continue
        for ort in orte:
            schluessel = os.path.normcase(ort)
            if schluessel not in gesehen:
                gesehen.add(schluessel)
                aus.append(ort)
    return aus


def kandidaten() -> List[str]:
    gefunden = [os.environ["EGO_SPIELORDNER"]] if os.environ.get("EGO_SPIELORDNER") else []
    if os.name != "nt":
        return gefunden
    gefunden += [os.path.join(b, SPIEL_REL) for b in steam_bibliotheken()]
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
