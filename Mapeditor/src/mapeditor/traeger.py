"""Trägerpaket (Portimão-Mod) einrichten: prüfen, ablegen, im Downloads-Ordner finden. Keine Oberfläche.

Der Mod liegt hinter einem Login (Overtake); der Editor lädt ihn nie selbst und fragt nie nach Zugangsdaten. Er hilft
nur beim Weg danach: die heruntergeladene ZIP erkennen, prüfen und an einen festen Ort pro Nutzer legen.
"""

import json
import os
from pathlib import Path
import shutil
import zipfile

from .i18n import tr

#: Download-Seite des Mods (öffnet im Standardbrowser des Nutzers)
CARRIER_URL = "https://www.overtake.gg/downloads/algarve-international-circuit-portimao-for-f1-25.87333/"
CARRIER_CODE = "portimao"
#: Mod-Versionen, mit denen der Bau getestet ist (1.0.2 empfohlen: behebt den AMD-Absturz)
SUPPORTED = ("1.0.0", "1.0.2")
RECOMMENDED = "1.0.2"
#: Der Mod ist rund 2,9 GB groß; kleinere ZIPs kommen als Träger nicht in Frage
MIN_SIZE = 300_000_000
SETTING = "traeger/pfad"


def carrier_folder():
    """Feste Ablage pro Nutzer (``%LOCALAPPDATA%\\Mapeditor\\traeger``); ``MAPEDITOR_TRAEGER`` überschreibt sie."""
    override = os.environ.get("MAPEDITOR_TRAEGER")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) if base else Path.home() / ".local" / "share") / "Mapeditor" / "traeger"


def carrier_target(version=RECOMMENDED):
    """Dateiname in der Ablage: ``portimao_<Version>.zip``."""
    return carrier_folder() / f"{CARRIER_CODE}_{version}.zip"


def downloads_folder():
    from PySide6.QtCore import QStandardPaths
    path = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation)
    return Path(path) if path else Path.home() / "Downloads"


def inspect_carrier(path):
    """ZIP prüfen, ohne sie zu entpacken (nur Inhaltsverzeichnis und ``manifest.json``).

    Rückgabe: ``{"ok", "problem", "version", "autor", "groesse", "unterstuetzt"}``; ``problem`` nennt den Grund."""
    path = Path(path)
    result = {"ok": False, "problem": "", "version": "", "autor": "", "groesse": 0, "unterstuetzt": False}
    try:
        result["groesse"] = path.stat().st_size
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if "manifest.json" not in names:
                result["problem"] = tr("This ZIP has no manifest.json, so it is not a track mod.")
                return result
            manifest = json.loads(archive.read("manifest.json").decode("utf-8-sig"))
    except (OSError, zipfile.BadZipFile, ValueError, KeyError):
        result["problem"] = tr("This file is not a readable ZIP (still downloading, or damaged).")
        return result
    code = str(manifest.get("track_code", ""))
    result["version"] = str(manifest.get("version", ""))
    result["autor"] = str(manifest.get("author_name", ""))
    erp = manifest.get("erp")
    if code != CARRIER_CODE:
        result["problem"] = tr("This mod contains the track “{code}”, but the Portimão mod is needed.", code=code or "?")
    elif not erp or erp not in names:
        result["problem"] = tr("The mod is incomplete: the track file {erp} is missing.", erp=erp or "?")
    elif result["groesse"] < MIN_SIZE:
        result["problem"] = tr("The file is much smaller than the Portimão mod (about 2.9 GB): the download is probably "
                               "not complete.")
    else:
        result["unterstuetzt"] = result["version"] in SUPPORTED
        if not result["unterstuetzt"]:
            result["problem"] = tr("Mod version {version} is not supported. Supported: {liste}.",
                                   version=result["version"] or "?", liste=", ".join(SUPPORTED))
        else:
            result["ok"] = True
    return result


def saved_carrier(settings):
    """Zuletzt eingerichteter Träger (App-Einstellung) oder None, wenn die Datei fehlt."""
    value = str(settings.value(SETTING, "") or "")
    return Path(value) if value and Path(value).is_file() else None


def remember_carrier(settings, path):
    settings.setValue(SETTING, str(path))


def find_candidates(folder, limit=8):
    """Fertig heruntergeladene Träger-ZIPs im Ordner, neueste zuerst: ``[(Pfad, Prüfergebnis)]``.

    Nur große ZIP-Dateien, nicht die mit begleitender ``.crdownload``/``.part``; nur ``inspect_carrier`` liest hinein."""
    folder = Path(folder)
    found = []
    try:
        entries = [p for p in folder.glob("*.zip") if p.is_file() and p.stat().st_size >= MIN_SIZE]
    except OSError:
        return []
    for path in sorted(entries, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        if any((path.parent / (path.name + suffix)).exists() for suffix in (".crdownload", ".part", ".tmp")):
            continue
        info = inspect_carrier(path)
        if info["version"] or info["ok"]:
            if str(path.name).lower().startswith("portimao") or info["ok"]:
                found.append((path, info))
    return found


def space_problem(target_folder, size):
    """Meldung, wenn im Zielordner nicht genug Platz für die Kopie ist, sonst None."""
    probe = Path(target_folder)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        free = shutil.disk_usage(probe).free
    except OSError:
        return None
    required = int(size * 1.02) + 200_000_000
    if free < required:
        return tr("Not enough free space on {anchor}: {free:.1f} GB free, {required:.1f} GB needed.",
                  anchor=probe.anchor, free=free / 1e9, required=required / 1e9)
    return None


def copy_carrier(source, target, progress=None, cancelled=None, chunk=8 * 1024 * 1024):
    """Kopie über eine ``.part``-Datei; fertig erst nach vollständigem Schreiben. Die Quelle bleibt unberührt.

    ``progress(bytes)`` meldet den Stand, ``cancelled()`` bricht ab (dann bleibt nichts liegen). Rückgabe: True, wenn kopiert."""
    source, target = Path(source), Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    done = 0
    try:
        with open(source, "rb") as reader, open(part, "wb") as writer:
            while True:
                block = reader.read(chunk)
                if not block:
                    break
                if cancelled and cancelled():
                    raise InterruptedError
                writer.write(block)
                done += len(block)
                if progress:
                    progress(done)
        os.replace(part, target)
        return True
    except InterruptedError:
        part.unlink(missing_ok=True)
        return False
    except OSError:
        part.unlink(missing_ok=True)
        raise
