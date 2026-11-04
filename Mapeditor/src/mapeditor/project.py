"""Verlustfreie Rohdefinition und versionierter Projektordner."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import tempfile
from .i18n import tr
from .paths import writable_path


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def atomic_write(path: Path, data: bytes):
    """Ersetzt eine Datei erst nach vollständigem Schreiben auf demselben Datenträger."""
    path = writable_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".mapeditor-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def encoded(data):
    return (json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")


class Project:
    def __init__(self, raw=None, manifest=None, folder=None, source=None):
        self.raw = copy.deepcopy(raw if raw is not None else {
            "name": tr("New track"), "mittellinie": {"art": "kontrollpunkte", "punkte": []}})
        self.manifest = copy.deepcopy(manifest if manifest is not None else {
            "schema": 1, "definition": "definition.json", "runde_geschlossen": False,
            "thema": "dunkel", "versatz": [0, 0, 0]})
        self.folder = Path(folder) if folder else None
        self.source = Path(source) if source else None
        self.readonly = self.manifest.get("schema", 1) != 1
        self.playtests = []
        if self.folder and (self.folder / "spieltests.json").exists():
            data = read_json(self.folder / "spieltests.json")
            if not isinstance(data, list) or any(not isinstance(entry, dict) for entry in data):
                raise ValueError(tr("Invalid playtest log."))
            self.playtests = data
        # Umgebung (Bäume, Gebäude …): ``umgebung.json`` neben der Definition; None = keine Datei. Eine nicht lesbare
        # Datei bleibt unberührt (``umgebung_fehler``): der Editor öffnet das Projekt, bearbeitet sie aber nicht.
        self.umgebung = None
        self.umgebung_fehler = None
        self._umgebung_geschrieben = set()
        self._load_umgebung()
        # Bildvorlage (Nachzeichnen): Bilddaten (PNG-Bytes) neben der Definition; Lage, Maßstab und Stützpunkte stehen
        # im Manifest unter ``bildvorlage`` (Teil des Rückgängig-Verlaufs), die Bytes nicht.
        self.bildvorlage_daten = None
        self._load_bildvorlage()

    def bildvorlage_path(self, folder=None):
        """Pfad des Bilds der Bildvorlage im Projektordner oder None (keine Vorlage oder ungültiger Name)."""
        vorlage = self.manifest.get("bildvorlage")
        if not isinstance(vorlage, dict):
            return None
        folder = Path(folder or self.folder or "")
        relative = Path(str(vorlage.get("datei") or "bildvorlage.png"))
        target = (folder / relative).resolve()
        if relative.is_absolute() or folder.resolve() not in target.parents or target.suffix.lower() != ".png":
            return None
        return target

    def _load_bildvorlage(self):
        path = self.bildvorlage_path() if self.folder else None
        if path is not None and path.is_file():
            self.bildvorlage_daten = path.read_bytes()

    def umgebung_path(self, folder=None):
        """Pfad der Umgebungsdatei (``projekt.json`` → ``umgebung``, sonst ``umgebung.json``) im Projektordner."""
        folder = Path(folder or self.folder or "")
        name = self.manifest.get("umgebung")
        relative = Path(name if isinstance(name, str) and name else "umgebung.json")
        target = (folder / relative).resolve()
        if relative.is_absolute() or folder.resolve() not in target.parents or target.name in (
                "projekt.json", "spieltests.json", self.manifest.get("definition", "definition.json")):
            return folder / "umgebung.json"
        return target

    def _load_umgebung(self):
        if not self.folder:
            return
        path = self.umgebung_path()
        if not path.is_file():
            return
        from .adapter import umgebung_laden
        try:
            self.umgebung = umgebung_laden(path)
        except ValueError as error:
            self.umgebung_fehler = str(error)

    @property
    def basis(self):
        return self.folder or (self.source.parent if self.source else Path.cwd())

    @property
    def closed(self):
        return self.manifest.get("runde_geschlossen", True)

    @classmethod
    def open(cls, path):
        path = Path(path).resolve()
        if path.is_dir():
            path = path / "projekt.json"
        data = read_json(path)
        if path.name == "projekt.json" or ("schema" in data and "definition" in data):
            definition = cls.definition_path(path.parent, data)
            return cls(read_json(definition), data, path.parent, definition)
        if not isinstance(data, dict) or not isinstance(data.get("mittellinie"), dict):
            raise ValueError(tr("No track definition: mittellinie is missing."))
        return cls(data, {"schema": 1, "definition": "definition.json",
                          "runde_geschlossen": True, "thema": "dunkel", "versatz": [0, 0, 0]}, source=path)

    @staticmethod
    def definition_path(folder, manifest):
        relative = Path(manifest.get("definition", "definition.json"))
        target = (folder / relative).resolve()
        if relative.is_absolute() or folder.resolve() not in target.parents or target.name == "projekt.json":
            raise ValueError(tr("Definition path must be inside the project folder."))
        return target

    def save(self, folder=None):
        if self.readonly:
            raise ValueError(tr("Unknown project version: opened read-only."))
        target = writable_path(folder or self.folder or "")
        if folder is None and self.folder is None:
            raise ValueError(tr("Please choose a project folder."))
        definition = self.definition_path(target, self.manifest)
        writable_path(definition)
        writable_path(target / "projekt.json")
        writable_path(target / "spieltests.json")
        if definition.name == "spieltests.json":
            raise ValueError(tr("Definition path collides with the playtest log."))
        new_definition, new_manifest = encoded(self.raw), encoded(self.manifest)
        # Externe CSV-Quellen bleiben unverändert; relative Beilagen werden mitgenommen.
        ml = self.raw.get("mittellinie", {})
        if ml.get("art") == "csv":
            rel = Path(ml["datei"])
            if rel.is_absolute() or ".." in rel.parts:
                raise ValueError(tr("First place the CSV as a relative attachment in the source folder."))
            original = (self.basis / rel).resolve()
            destination = (target / rel).resolve()
            if target not in destination.parents:
                raise ValueError(tr("CSV target must be inside the project folder."))
            if destination in (definition, target / "projekt.json", target / "spieltests.json"):
                raise ValueError(tr("CSV attachment collides with a project file."))
            if original != destination:
                atomic_write(destination, original.read_bytes())
        manifest_path = target / "projekt.json"
        umgebung_datei = self.umgebung_path(target)
        write_umgebung = self.umgebung is not None and not self.umgebung_fehler
        # Eine selbst geschriebene Umgebungsdatei, die inzwischen rückgängig gemacht wurde, nicht stehen lassen.
        remove_umgebung = self.umgebung is None and umgebung_datei in self._umgebung_geschrieben
        bild_datei = self.bildvorlage_path(target) if self.bildvorlage_daten else None
        if bild_datei in (definition, manifest_path, target / "spieltests.json", umgebung_datei):
            bild_datei = None
        old = {p: p.read_bytes() if p.exists() else None
               for p in (definition, manifest_path, target / "spieltests.json", umgebung_datei, bild_datei) if p}
        try:
            if bild_datei is not None and old.get(bild_datei) != self.bildvorlage_daten:
                atomic_write(bild_datei, self.bildvorlage_daten)
            atomic_write(definition, new_definition)
            atomic_write(manifest_path, new_manifest)
            if self.playtests or (target / "spieltests.json").exists():
                atomic_write(target / "spieltests.json", encoded(self.playtests))
            if write_umgebung:
                from .adapter import umgebung_speichern
                umgebung_speichern(self.umgebung, umgebung_datei)
                self._umgebung_geschrieben.add(umgebung_datei)
            elif remove_umgebung:
                umgebung_datei.unlink(missing_ok=True)
                self._umgebung_geschrieben.discard(umgebung_datei)
        except OSError:
            for path, content in old.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path, content)
            raise
        self.folder, self.source = target, definition

    def save_playtests(self):
        if self.readonly or not self.folder:
            raise ValueError(tr("Playtests need a saved, writable project."))
        atomic_write(self.folder / "spieltests.json", encoded(self.playtests))


def point_xyz(point):
    if isinstance(point, dict):
        return float(point["x"]), float(point.get("y", point.get("hoehe", 0))), float(point["z"])
    return float(point[0]), float(point[2]) if len(point) > 2 else 0.0, float(point[1])


def set_point(point, x=None, y=None, z=None):
    """Behält Listenform, Höhenalias und unbekannte Punktattribute bei."""
    result = copy.deepcopy(point)
    if isinstance(result, dict):
        for key, value in (("x", x), ("z", z), ("y" if "y" in result or "hoehe" not in result else "hoehe", y)):
            if value is not None:
                result[key] = value
    else:
        if x is not None:
            result[0] = x
        if z is not None:
            result[1] = z
        if y is not None:
            if len(result) == 2:
                result.append(y)
            else:
                result[2] = y
    return result


def shifted_point(point, x, z):
    """Schnelle Entwurfskopie mit neuem x/z (flach; nur für Gesten-Vorschau, nicht zum Speichern)."""
    if isinstance(point, dict):
        result = dict(point)
        result["x"], result["z"] = x, z
        return result
    result = list(point)
    result[0], result[1] = x, z
    return result
