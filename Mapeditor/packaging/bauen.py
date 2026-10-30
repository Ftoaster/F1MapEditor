"""Baut die eigenständige Windows-Distribution: Mapeditor/dist/Mapeditor/ + ZIP.

Aufruf (aus beliebigem Ordner):  Mapeditor/.venv/Scripts/python.exe Mapeditor/packaging/bauen.py [--ohne-zip] [--ohne-rauchtest]

Schritte: PyInstaller (mapeditor.spec) -> Werkzeugkasten kopieren (nur Code und
kleine Beispiele) -> Lizenzdateien -> optional Rauchtest -> ZIP.
Schreibt nur nach Mapeditor/build und Mapeditor/dist. Nie in den Spielordner.
"""
import argparse
import fnmatch
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

HIER = Path(__file__).resolve().parent
EDITOR = HIER.parent
WERKZEUG = EDITOR.parent / "ego-werkzeuge"
DIST = EDITOR / "dist"
ZIEL = DIST / "Mapeditor"

# Whitelist: nur Code und kleine Daten. Keine Tests, Analysen, Ergebnisse, Testausgaben.
KOPIEREN = [("strecke", ["*.py", "beispiele/*.json", "fahrzeuge/*.json"]), ("ego", ["*.py"]),
            (".", ["neue_strecke_tool.py", "paket_tool.py", "erp_tool.py", "strecke_tool.py", "injektor_tool.py"])]
MAX_DATEI = 1_000_000  # Sicherung: nichts über 1 MB (keine ERPs/Pakete)


def version():
    text = (EDITOR / "src" / "mapeditor" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def werkzeugkasten_kopieren(ziel):
    ziel = Path(ziel).resolve()
    if DIST.resolve() not in ziel.parents:
        raise ValueError("Werkzeugkopie muss unter Mapeditor/dist liegen.")
    if ziel.exists():
        shutil.rmtree(ziel)
    anzahl = 0
    for ordner, muster in KOPIEREN:
        quelle = WERKZEUG / ordner
        for datei in sorted(p for p in quelle.rglob("*") if p.is_file()):
            rel = datei.relative_to(quelle).as_posix()
            if "__pycache__" in datei.parts:
                continue
            if ordner == "." and "/" in rel:
                continue
            if not any(fnmatch.fnmatch(rel, m) for m in muster):
                continue
            if datei.stat().st_size > MAX_DATEI:
                raise SystemExit(f"Zu große Datei im Werkzeugkasten: {datei}")
            ausgabe = ziel / ordner / rel if ordner != "." else ziel / rel
            ausgabe.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(datei, ausgabe)
            anzahl += 1
    return anzahl


PRUNE = ["Qt6Pdf.dll", "Qt6Qml*.dll", "Qt6Quick*.dll", "Qt6VirtualKeyboard.dll",
         "plugins/platforminputcontexts/*virtualkeyboard*"]


def beschneiden(ziel):
    """Entfernt von PyInstaller mitgezogene, ungenutzte Qt-Teile (Editor nutzt nur Core/Gui/Widgets)."""
    qt = ziel / "_internal" / "PySide6"
    vorher = groesse(ziel)
    for muster in PRUNE:
        for p in qt.glob(muster):
            p.unlink()
    for p in (qt / "translations").glob("*.qm"):
        if p.name not in ("qt_de.qm", "qt_en.qm", "qtbase_de.qm", "qtbase_en.qm"):
            p.unlink()
    print(f"Beschnitten: {(vorher - groesse(ziel)) / 1e6:.0f} MB")


def groesse(pfad):
    return sum(p.stat().st_size for p in Path(pfad).rglob("*") if p.is_file())


def distribution_frei():
    """Läuft die alte Mapeditor.exe noch, bricht PyInstaller beim Leeren von dist/ mittendrin ab und hinterlässt eine
    halb gelöschte Distribution (27.10.2026). Deshalb vorher prüfen: Eine laufende EXE lässt sich nicht zum Schreiben
    öffnen."""
    exe = ZIEL / "Mapeditor.exe"
    if not exe.exists():
        return
    try:
        with open(exe, "r+b"):
            pass
    except PermissionError:
        sys.exit("Mapeditor.exe läuft noch (%s) – bitte schließen und erneut bauen. Es wurde nichts geändert." % exe)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ohne-zip", action="store_true")
    ap.add_argument("--ohne-rauchtest", action="store_true")
    args = ap.parse_args()
    # PyInstaller-Cache und temporäre Dateien bleiben im Editor-Arbeitsbaum.
    temp = EDITOR / "build" / "tmp"
    temp.mkdir(parents=True, exist_ok=True)
    os.environ.update(PYINSTALLER_CONFIG_DIR=str(EDITOR / "build" / "pyinstaller-cache"),
                      TEMP=str(temp), TMP=str(temp), PYTHONDONTWRITEBYTECODE="1")
    distribution_frei()
    t0 = time.time()
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(DIST), "--workpath", str(EDITOR / "build"),
                    str(HIER / "mapeditor.spec")], check=True, cwd=EDITOR)
    beschneiden(ZIEL)
    n = werkzeugkasten_kopieren(ZIEL / "werkzeugkasten")
    print(f"Werkzeugkasten: {n} Dateien")
    shutil.copy2(HIER / "LIESMICH.txt", ZIEL / "LIESMICH.txt")
    (ZIEL / "lizenzen").mkdir(exist_ok=True)
    for f in (HIER / "lizenzen").glob("*"):
        shutil.copy2(f, ZIEL / "lizenzen" / f.name)
    print(f"Distribution: {ZIEL}  {groesse(ZIEL) / 1e6:.0f} MB")
    if not args.ohne_rauchtest:
        r = subprocess.run([sys.executable, str(HIER / "rauchtest.py")])
        if r.returncode:
            raise SystemExit("Rauchtest fehlgeschlagen")
    if not args.ohne_zip:
        zielzip = DIST / f"Mapeditor-{version()}-win64.zip"
        with zipfile.ZipFile(zielzip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for p in sorted(ZIEL.rglob("*")):
                if p.is_file():
                    z.write(p, Path("Mapeditor") / p.relative_to(ZIEL))
        print(f"ZIP: {zielzip}  {zielzip.stat().st_size / 1e6:.0f} MB")
    print(f"Fertig in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
