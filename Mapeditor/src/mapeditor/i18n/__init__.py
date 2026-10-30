"""Übersetzung der Oberfläche: Deutsch ist Quelltext und Standard, Englisch kommt aus ``texte.csv``.

Aufbau (Begründung in ``docs/uebersetzung.md``):

* Im Code steht der deutsche Text selbst als Schlüssel: ``tr("Speichern")``. Fehlt eine Übersetzung, erscheint
  der deutsche Text – die Oberfläche bleibt immer bedienbar.
* Werte über benannte Platzhalter statt zusammengesetzter Sätze: ``tr("Höhe {wert} m", wert=zahl(y))``.
* Mehrzahl: ``trn(n, "{n} Warnung", "{n} Warnungen")``; in der Tabelle als eine Zeile ``Einzahl|Mehrzahl``.
* Konstanten auf Modulebene markiert ``N_("…")`` (gibt den Text unverändert zurück); übersetzt wird erst bei der
  Anzeige mit ``tr(konstante)``. So wirkt ein Sprachwechsel ohne Neuimport der Module.
* Lange Texte (Hilfe) liegen als eigene Dateien je Sprache daneben: ``hilfe_de.html``, ``hilfe_en.html``.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

HIER = Path(__file__).resolve().parent
TABELLE = HIER / "texte.csv"
SPALTEN = ("de", "en", "ort")
SPRACHEN = {"de": "Deutsch", "en": "English"}
STANDARD = "de"
MEHRZAHL_TRENNER = "|"

_sprache = STANDARD
_cache = {}


def N_(text):
    """Text für die Übersetzung markieren, ohne ihn jetzt zu übersetzen (Konstanten auf Modulebene)."""
    return text


def lade_tabelle(pfad=TABELLE):
    """Zeilen der Übersetzungstabelle als Liste von Dicts (Spalten ``de``, ``en``, ``ort``)."""
    with open(pfad, encoding="utf-8-sig", newline="") as datei:
        return [dict(zeile) for zeile in csv.DictReader(datei, delimiter=";")]


def schreibe_tabelle(zeilen, pfad=TABELLE):
    """Tabelle schreiben: UTF-8 mit BOM und Semikolon, damit ein deutsches Excel sie direkt öffnet."""
    with open(pfad, "w", encoding="utf-8-sig", newline="") as datei:
        writer = csv.DictWriter(datei, fieldnames=SPALTEN, delimiter=";", quoting=csv.QUOTE_MINIMAL,
                                lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for zeile in zeilen:
            writer.writerow({spalte: zeile.get(spalte, "") for spalte in SPALTEN})


def _woerterbuch(sprache):
    if sprache not in _cache:
        try:
            zeilen = lade_tabelle()
        except OSError:
            zeilen = []
        _cache[sprache] = {z["de"]: z.get(sprache) or "" for z in zeilen if z.get("de")}
    return _cache[sprache]


def sprache():
    """Aktive Oberflächensprache (``de`` oder ``en``)."""
    return _sprache


def setze_sprache(code):
    """Sprache für alle danach erzeugten Texte setzen (Fenster neu aufbauen, damit alles umschaltet)."""
    global _sprache
    code = (code or STANDARD).strip().lower()[:2]
    if code not in SPRACHEN:
        raise ValueError(f"Unbekannte Sprache: {code} (erlaubt: {', '.join(SPRACHEN)})")
    _sprache = code
    try:
        from PySide6.QtCore import QLocale
        QLocale.setDefault(qlocale())
    except ImportError:        # pragma: no cover - nur ohne Qt
        pass
    return _sprache


def qlocale():
    """Qt-Gebietsschema der aktiven Sprache (Zahlen, Eingabefelder)."""
    from PySide6.QtCore import QLocale
    if _sprache == "en":
        return QLocale(QLocale.English, QLocale.UnitedStates)
    return QLocale(QLocale.German, QLocale.Germany)


def dezimal(text):
    """Mit ``f"{x:.1f}"`` formatierten Text in die Sprache bringen: deutsch Dezimalkomma, englisch unverändert."""
    return text if _sprache == "en" else text.replace(".", ",")


def _nachschlagen(text):
    if _sprache == STANDARD:
        return text
    return _woerterbuch(_sprache).get(text) or text


def tr(text, **werte):
    """Oberflächentext in der aktiven Sprache; ``werte`` füllen benannte Platzhalter ``{name}``."""
    uebersetzt = _nachschlagen(text)
    return uebersetzt.format(**werte) if werte else uebersetzt


def trn(n, einzahl, mehrzahl, **werte):
    """Mehrzahl: ``trn(3, "{n} Warnung", "{n} Warnungen")`` → „3 Warnungen“ bzw. „3 warnings“.

    ``n`` steht als Platzhalter ``{n}`` zur Verfügung (ohne Formatierung); für Zahlenformat ``n=zahl(…)`` übergeben."""
    werte.setdefault("n", n)
    eins = n == 1
    text = einzahl if eins else mehrzahl
    if _sprache != STANDARD:
        eintrag = _woerterbuch(_sprache).get(einzahl + MEHRZAHL_TRENNER + mehrzahl)
        if eintrag and MEHRZAHL_TRENNER in eintrag:
            e1, e2 = eintrag.split(MEHRZAHL_TRENNER, 1)
            text = e1 if eins else e2
    return text.format(**werte)


REGELN = HIER / "regeln.csv"


def lade_regeln(pfad=REGELN):
    """Übersetzte Titel der Prüfregeln des Werkzeugkastens (Spalten ``id``, ``kurz``, ``de``, ``en``)."""
    with open(pfad, encoding="utf-8-sig", newline="") as datei:
        return [dict(zeile) for zeile in csv.DictReader(datei, delimiter=";")]


def rule_title(regel_id):
    """Regeltitel in der aktiven Sprache, nur wenn nicht Deutsch (deutsche Meldungen bleiben unverändert)."""
    if _sprache == STANDARD or not regel_id:
        return None
    if "regeln" not in _cache:
        try:
            _cache["regeln"] = {z["id"]: z for z in lade_regeln()}
        except OSError:
            _cache["regeln"] = {}
    return (_cache["regeln"].get(regel_id) or {}).get(_sprache) or None


def html(name):
    """Langer Text als Datei je Sprache (``<name>_<sprache>.html``), sonst die deutsche Fassung."""
    for code in (_sprache, STANDARD):
        pfad = HIER / f"{name}_{code}.html"
        if pfad.is_file():
            return pfad.read_text(encoding="utf-8")
    return ""


PLATZHALTER = re.compile(r"(?<!\{)\{([A-Za-z_]\w*)[^{}]*\}(?!\})")


def platzhalter(text):
    """Menge der benannten Platzhalter in einem Text (für die Vollständigkeitsprüfung)."""
    return set(PLATZHALTER.findall(text or ""))
