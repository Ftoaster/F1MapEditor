"""Schreibziele vor jeder Editor-Mutation prüfen (auch aufgelöste Verknüpfungen)."""

from pathlib import Path
import sys

from .i18n import tr


def writable_path(value):
    path = Path(value).expanduser().resolve()
    parts = [p.casefold() for p in path.parts]
    if any(parts[i:i + 3] == ["steamapps", "common", "f1 25"] for i in range(len(parts))):
        raise ValueError(tr("Der Spielordner ist schreibgeschützt. Einen eigenen Ausgabeordner wählen."))
    protected = [(Path.home() / "Downloads").resolve(),
                 Path(__file__).resolve().parents[2] / "dist" / "Mapeditor"]
    if getattr(sys, "frozen", False):
        protected.append(Path(sys.executable).resolve().parent)
    for root in protected:
        if path == root or root in path.parents:
            raise ValueError(tr("Schreibgeschützter Ordner: {root}. Bitte außerhalb speichern.", root=root))
    return path
