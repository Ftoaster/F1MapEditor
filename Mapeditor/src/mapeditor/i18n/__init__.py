"""Übersetzung der Oberfläche, **English First** (4.11.2026): Englisch ist Quelltext und Standard, Deutsch kommt aus
``texte.csv``.

Aufbau (Begründung in ``docs/uebersetzung.md``):

* Im Code steht der englische Text selbst als Schlüssel: ``tr("Save")``. Fehlt eine Übersetzung, erscheint der
  englische Text – die Oberfläche bleibt immer bedienbar.
* Werte über benannte Platzhalter statt zusammengesetzter Sätze: ``tr("Height {wert} m", wert=zahl(y))``.
* Mehrzahl: ``trn(n, "{n} warning", "{n} warnings")``; in der Tabelle als eine Zeile ``Einzahl|Mehrzahl``.
* Konstanten auf Modulebene markiert ``N_("…")`` (gibt den Text unverändert zurück); übersetzt wird erst bei der
  Anzeige mit ``tr(konstante)``. So wirkt ein Sprachwechsel ohne Neuimport der Module.
* Deutsche Datenwerte (Stufen und Status aus dem Werkzeugkasten oder aus Projektdateien, z. B. „Fehler“) laufen ebenfalls
  durch ``tr``: Steht der Wert in der Spalte ``de``, erscheint auf Englisch der Schlüssel dieser Zeile.
* Lange Texte (Hilfe) liegen als eigene Dateien je Sprache daneben: ``hilfe_en.html``, ``hilfe_de.html``.
* Meldungen des Werkzeugkastens sind englisch und fertig formatiert. ``meldung(text)`` bildet sie auf Deutsch über
  ``meldungen.csv`` (Vorlagen mit ``{}``) ab; ``pflege`` prüft, dass jede Vorlage des Werkzeugkastens dort steht.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

HIER = Path(__file__).resolve().parent
TABELLE = HIER / "texte.csv"
SPALTEN = ("en", "de", "ort")
SPRACHEN = {"en": "English", "de": "Deutsch"}
#: Quell- und Standardsprache (English First): Schlüssel im Code, Rückfall für fehlende Übersetzungen
STANDARD = "en"
MEHRZAHL_TRENNER = "|"

_sprache = STANDARD
_cache = {}


def N_(text):
    """Text für die Übersetzung markieren, ohne ihn jetzt zu übersetzen (Konstanten auf Modulebene)."""
    return text


def lade_tabelle(pfad=TABELLE):
    """Zeilen der Übersetzungstabelle als Liste von Dicts (Spalten ``en``, ``de``, ``ort``)."""
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
    """Schlüssel (Englisch) → Text der Sprache; für ``STANDARD`` deutsche Datenwerte → Englisch."""
    if sprache not in _cache:
        try:
            zeilen = lade_tabelle()
        except OSError:
            zeilen = []
        if sprache == STANDARD:
            _cache[sprache] = {z["de"]: z["en"] for z in zeilen if z.get("de") and z.get("en")}
        else:
            _cache[sprache] = {z["en"]: z.get(sprache) or "" for z in zeilen if z.get("en")}
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
        return _woerterbuch(STANDARD).get(text) or text          # deutsche Datenwerte („Fehler“) → Englisch
    return _woerterbuch(_sprache).get(text) or text


def tr(text, **werte):
    """Oberflächentext in der aktiven Sprache; ``werte`` füllen benannte Platzhalter ``{name}``, ``error`` kommt meist
    aus dem Werkzeugkasten und läuft durch ``meldung``."""
    uebersetzt = _nachschlagen(text)
    if "error" in werte:
        werte["error"] = meldung(werte["error"])
    return uebersetzt.format(**werte) if werte else uebersetzt


def trn(n, einzahl, mehrzahl, **werte):
    """Mehrzahl: ``trn(3, "{n} warning", "{n} warnings")`` → „3 warnings“ bzw. „3 Warnungen“.

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
    """Übersetzte Titel der Prüfregeln des Werkzeugkastens (Spalten ``id``, ``kurz``, ``en``, ``de``)."""
    with open(pfad, encoding="utf-8-sig", newline="") as datei:
        return [dict(zeile) for zeile in csv.DictReader(datei, delimiter=";")]


def rule_title(regel_id):
    """Regeltitel auf Englisch vor der Meldung; auf Deutsch keiner (wie bisher: die Meldung allein)."""
    if _sprache == "de" or not regel_id:
        return None
    if "regeln" not in _cache:
        try:
            _cache["regeln"] = {z["id"]: z for z in lade_regeln()}
        except OSError:
            _cache["regeln"] = {}
    return (_cache["regeln"].get(regel_id) or {}).get(_sprache) or None


MELDUNGEN = HIER / "meldungen.csv"
#: Tiefe verschachtelter Meldungen („… (Träger Portimão: 15 …)“ um eine Fehlermeldung herum)
MELDUNG_TIEFE = 3


def _meldungsmuster():
    """(exakte Texte, [(Regex, Vorlage)]): längster fester Text zuerst, damit allgemeine Vorlagen zuletzt greifen."""
    schluessel = "meldungen:" + _sprache
    if schluessel not in _cache:
        try:
            zeilen = lade_tabelle(MELDUNGEN)
        except OSError:
            zeilen = []
        exakt, muster = {}, []
        for zeile in zeilen:
            quelle, ziel = zeile.get(STANDARD), zeile.get(_sprache)
            if not quelle or not ziel:
                continue
            if zeile.get("ort") == "wort" and zeile.get("de"):
                # Datenwörter des Werkzeugkastens (Seiten, Oberflächen …) sind deutsch und stecken in englischen Meldungen
                exakt[zeile["de"]] = ziel
            if "{}" not in quelle:
                exakt[quelle] = ziel
                continue
            teile = quelle.split("{}")
            muster.append((sum(map(len, teile)), re.compile("(.+?)".join(map(re.escape, teile)), re.DOTALL), ziel))
        _cache[schluessel] = (exakt, [(regex, ziel) for _laenge, regex, ziel in sorted(muster, key=lambda m: -m[0])])
    return _cache[schluessel]


def _einsetzen(vorlage, werte):
    """``{}`` der Reihe nach füllen, ``{2}`` gezielt, wenn die deutsche Satzstellung abweicht."""
    folge = iter(werte)
    return re.sub(r"\{(\d*)\}", lambda t: werte[int(t.group(1)) - 1] if t.group(1) else next(folge), vorlage)


def _uebersetzen(text, tiefe=0):
    exakt, muster = _meldungsmuster()
    if text in exakt:
        return exakt[text]
    if tiefe > MELDUNG_TIEFE or not re.search("[^\\W\\d_]", text):
        return text
    for regex, vorlage in muster:
        treffer = regex.fullmatch(text)
        if treffer:
            return _einsetzen(vorlage, [_uebersetzen(w, tiefe + 1) for w in treffer.groups()])
    return "/".join(_uebersetzen(w, tiefe + 1) for w in text.split("/")) if tiefe and "/" in text else text


def meldung(text):
    """Meldung des Werkzeugkastens (englisch) in der aktiven Sprache; Unbekanntes bleibt, wie es ist. Auch auf Englisch
    laufen Meldungen durch die Vorlagen: eingesetzte deutsche Datenwörter („rechts“, „randstein“) werden übersetzt."""
    text = str(text)
    return _uebersetzen(text) if text else text


def html(name):
    """Langer Text als Datei je Sprache (``<name>_<sprache>.html``), sonst die englische Fassung."""
    for code in (_sprache, STANDARD):
        pfad = HIER / f"{name}_{code}.html"
        if pfad.is_file():
            return pfad.read_text(encoding="utf-8")
    return ""


PLATZHALTER = re.compile(r"(?<!\{)\{([A-Za-z_]\w*)[^{}]*\}(?!\})")


def platzhalter(text):
    """Menge der benannten Platzhalter in einem Text (für die Vollständigkeitsprüfung)."""
    return set(PLATZHALTER.findall(text or ""))
