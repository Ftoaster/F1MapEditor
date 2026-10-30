"""Pflege der Übersetzungstabelle: verwendete Schlüssel im Quelltext finden und ``texte.csv`` abgleichen.

Aufruf (im Ordner ``Mapeditor``)::

    .venv/Scripts/python -m mapeditor.i18n.pflege            # Bericht: fehlend, leer, unbenutzt, Platzhalter
    .venv/Scripts/python -m mapeditor.i18n.pflege --schreiben  # neue Schlüssel (en leer) anhängen, Orte erneuern

Gefunden werden Textliterale in ``tr("…")``, ``trn(n, "…", "…")`` (als ``Einzahl|Mehrzahl``) und ``N_("…")``.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

from . import MEHRZAHL_TRENNER, TABELLE, lade_tabelle, platzhalter, schreibe_tabelle

QUELLEN = Path(__file__).resolve().parents[1]


def _text(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def schluessel_in_datei(pfad):
    """[(Schlüssel, Zeile)] aller markierten Texte einer Python-Datei."""
    baum = ast.parse(Path(pfad).read_text(encoding="utf-8"))
    gefunden = []
    for node in ast.walk(baum):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
        if name in ("tr", "N_") and node.args:
            text = _text(node.args[0])
            if text is not None:
                gefunden.append((text, node.lineno))
        elif name == "trn" and len(node.args) >= 3:
            eins, viele = _text(node.args[1]), _text(node.args[2])
            if eins is not None and viele is not None:
                gefunden.append((eins + MEHRZAHL_TRENNER + viele, node.lineno))
    return gefunden


def verwendete_schluessel(wurzel=QUELLEN):
    """{Schlüssel: [„datei.py:zeile“, …]} über alle Module des Editors."""
    alle = {}
    for pfad in sorted(Path(wurzel).rglob("*.py")):
        if "__pycache__" in pfad.parts:
            continue
        for text, zeile in schluessel_in_datei(pfad):
            alle.setdefault(text, []).append(f"{pfad.relative_to(wurzel).as_posix()}:{zeile}")
    return alle


def pruefen(zeilen=None, verwendet=None):
    """Befunde als Dict: fehlend (im Code, nicht in der Tabelle), leer (ohne Englisch), unbenutzt, platzhalter,
    mehrzahl (Plural-Zeile ohne ``|`` im Englischen), doppelt."""
    zeilen = lade_tabelle() if zeilen is None else zeilen
    verwendet = verwendete_schluessel() if verwendet is None else verwendet
    tabelle, doppelt = {}, []
    for zeile in zeilen:
        if zeile["de"] in tabelle:
            doppelt.append(zeile["de"])
        tabelle[zeile["de"]] = zeile
    befund = {"fehlend": sorted(k for k in verwendet if k not in tabelle),
              "leer": sorted(k for k, z in tabelle.items() if not (z.get("en") or "").strip()),
              "unbenutzt": sorted(k for k in tabelle if k not in verwendet),
              "platzhalter": [], "mehrzahl": [], "doppelt": doppelt}
    for k, z in tabelle.items():
        en = z.get("en") or ""
        if not en:
            continue
        teile_de, teile_en = k.split(MEHRZAHL_TRENNER), en.split(MEHRZAHL_TRENNER)
        if len(teile_de) != len(teile_en):
            befund["mehrzahl"].append(k)
        elif any(platzhalter(a) != platzhalter(b) for a, b in zip(teile_de, teile_en)):
            befund["platzhalter"].append(k)
    return befund


def abgleichen(zeilen=None, verwendet=None):
    """Neue Schlüssel anhängen (Englisch leer), Spalte ``ort`` erneuern; unbenutzte Zeilen bleiben stehen."""
    if zeilen is None:
        zeilen = lade_tabelle() if TABELLE.exists() else []
    verwendet = verwendete_schluessel() if verwendet is None else verwendet
    vorhanden = {z["de"] for z in zeilen}
    for zeile in zeilen:
        orte = verwendet.get(zeile["de"])
        zeile["ort"] = orte[0].split(":")[0] if orte else "unbenutzt"
    for text, orte in verwendet.items():
        if text not in vorhanden:
            zeilen.append({"de": text, "en": "", "ort": orte[0].split(":")[0]})
    zeilen.sort(key=lambda z: (z["ort"], z["de"].casefold()))
    return zeilen


def main(argv=None):
    parser = argparse.ArgumentParser(description="Übersetzungstabelle prüfen und abgleichen")
    parser.add_argument("--schreiben", action="store_true", help="neue Schlüssel in texte.csv eintragen")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.schreiben:
        schreibe_tabelle(abgleichen())
        print(f"{TABELLE} abgeglichen.")
    befund = pruefen()
    fehler = 0
    for art, liste in befund.items():
        if liste:
            fehler += len(liste) if art != "unbenutzt" else 0
            print(f"{art}: {len(liste)}")
            for text in liste[:40]:
                print("   ", text.replace("\n", "⏎")[:120])
    print("OK" if not fehler else f"{fehler} Befund(e)")
    return 1 if fehler else 0


if __name__ == "__main__":
    raise SystemExit(main())
