"""Pflege der Übersetzungstabelle: verwendete Schlüssel im Quelltext finden und ``texte.csv`` abgleichen.

Aufruf (im Ordner ``Mapeditor``)::

    .venv/Scripts/python -m mapeditor.i18n.pflege            # Bericht: fehlend, leer, unbenutzt, Platzhalter
    .venv/Scripts/python -m mapeditor.i18n.pflege --schreiben  # neue Schlüssel (en leer) anhängen, Orte erneuern

Gefunden werden Textliterale in ``tr("…")``, ``trn(n, "…", "…")`` (als ``Einzahl|Mehrzahl``) und ``N_("…")``.
``meldungen.csv`` wird mit den Meldungsvorlagen des Werkzeugkastens (``ego-werkzeuge/strecke``) abgeglichen: Texte in
``meldung(…)``, in Fehlern (``raise …Fehler/…Error``), in ``warnungen.append(…)`` und in den Variablen ``text`` und ``grund``,
dazu die Phasentitel aus ``PHASEN`` in ``neue_strecke_tool.py``.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

from . import MEHRZAHL_TRENNER, MELDUNGEN, TABELLE, lade_tabelle, platzhalter, schreibe_tabelle

QUELLEN = Path(__file__).resolve().parents[1]
WERKZEUG = QUELLEN.parents[2] / "ego-werkzeuge" / "strecke"


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


def _vorlagen(node):
    """Vorlagen (Texte ohne Formatierung) eines Ausdrucks: Literal, f-String, ``"…" % x``, ``"…".format``, ``a if b else c``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return ["".join(w.value if isinstance(w, ast.Constant) else "{}" for w in node.values)]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return _vorlagen(node.left)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        links, rechts = _vorlagen(node.left) or ["{}"], _vorlagen(node.right) or ["{}"]
        return [a + b for a in links for b in rechts]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        return _vorlagen(node.func.value)
    if isinstance(node, ast.IfExp):
        return _vorlagen(node.body) + _vorlagen(node.orelse)
    return []


def vorlage_normal(text):
    """``%d``, ``%.1f``, ``{x:.1f}`` … werden zu ``{}``, ``%%`` zu ``%``."""
    text = text.replace("%%", "\0")
    text = re.sub(r"%[-+ #0]*\d*(?:\.\d+)?[dsfgeErx]", "{}", text)
    return re.sub(r"\{[^{}]*\}", "{}", text).replace("\0", "%")


def vorlagen_in_datei(pfad):
    gefunden = []
    for node in ast.walk(ast.parse(Path(pfad).read_text(encoding="utf-8"))):
        ausdruck = None
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "meldung" and len(node.args) > 1:
            ausdruck = node.args[1]
        elif isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
            if re.search(r"(Fehler|Error|SystemExit)$", getattr(node.exc.func, "id", "")):
                ausdruck = node.exc.args[0]
        elif isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "append" and node.args:
            if getattr(node.func.value, "id", "") == "warnungen":
                ausdruck = node.args[0]
        elif isinstance(node, (ast.Assign, ast.AugAssign)):
            ziele = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(getattr(z, "id", "") in ("text", "grund") for z in ziele):
                ausdruck = node.value
        if ausdruck is not None:
            gefunden += [vorlage_normal(t) for t in _vorlagen(ausdruck)]
    return [t for t in gefunden if re.search(r"[^\W\d_]{3}", t.replace("{}", ""))]


def phasentitel(pfad):
    """Titel der Bauphasen: ``PHASEN = ((name, titel, anteil), …)``."""
    for node in ast.parse(Path(pfad).read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "PHASEN":
            return [zeile.elts[1].value for zeile in node.value.elts]
    return []


def meldungsvorlagen(ordner=WERKZEUG):
    """{Vorlage: Datei} aller Meldungen des Werkzeugkastens."""
    alle = {}
    for pfad in sorted(Path(ordner).glob("*.py")):
        for text in vorlagen_in_datei(pfad):
            alle.setdefault(text, pfad.name)
    werkzeug = Path(ordner).parent / "neue_strecke_tool.py"
    if werkzeug.is_file():
        for text in phasentitel(werkzeug):
            alle.setdefault(text, werkzeug.name)
    return alle


def meldungen_pruefen(zeilen=None, vorlagen=None):
    """Befunde für ``meldungen.csv``: fehlend, leer, platzhalter (andere Anzahl ``{}`` im Englischen)."""
    zeilen = lade_tabelle(MELDUNGEN) if zeilen is None else zeilen
    vorlagen = meldungsvorlagen() if vorlagen is None else vorlagen
    tabelle = {z["de"]: z.get("en") or "" for z in zeilen}
    zaehlen = lambda t: len(re.findall(r"\{\d*\}", t))
    return {"meldung_fehlend": sorted(t for t in vorlagen if t not in tabelle),
            "meldung_leer": sorted(t for t, en in tabelle.items() if not en.strip()),
            "meldung_platzhalter": sorted(t for t, en in tabelle.items() if en and zaehlen(t) != zaehlen(en))}


def meldungen_abgleichen():
    zeilen = lade_tabelle(MELDUNGEN) if MELDUNGEN.exists() else []
    vorlagen = meldungsvorlagen()
    vorhanden = {z["de"] for z in zeilen}
    for zeile in zeilen:
        zeile["ort"] = vorlagen.get(zeile["de"], zeile.get("ort") if zeile.get("ort") == "wort" else "unbenutzt")
    zeilen += [{"de": t, "en": "", "ort": ort} for t, ort in vorlagen.items() if t not in vorhanden]
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
        if WERKZEUG.is_dir():
            schreibe_tabelle(meldungen_abgleichen(), MELDUNGEN)
    befund = pruefen()
    if WERKZEUG.is_dir():
        befund.update(meldungen_pruefen())
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
