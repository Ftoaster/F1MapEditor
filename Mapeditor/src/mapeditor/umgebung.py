"""Umgebung (Bäume, Büsche, Gebäude …): Projektdatei ``umgebung.json``, Streuen und Abstand zur Strecke.

Reine Logik ohne Qt und ohne Werkzeugkasten. Das Dateiformat steht im Vertrag
``docs/zwischenstand/umgebung_vertrag.md`` (Abschnitt 1); Koordinaten sind Definitionskoordinaten wie die
Kontrollpunkte. Der Bau selbst (Instanzen, Layer, Ressourcen) gehört dem Werkzeugkasten; hier entsteht nur die
Planung im Editor.
"""

from __future__ import annotations

import copy
import json
import math
import random
import re

import numpy as np

from .i18n import N_

SCHEMA = 1
DATEI = "umgebung.json"
#: Reihenfolge der Schlüssel beim Schreiben (unbekannte Schlüssel folgen danach)
KOPF_SCHLUESSEL = ("schema", "bodenbewuchs", "gelaende", "objekte")
OBJEKT_SCHLUESSEL = ("id", "modell", "x", "z", "hoehe_m", "drehung_grad", "skala", "gruppe")
SKALA_MIN, SKALA_MAX = 0.2, 5.0
HOEHE_MIN, HOEHE_MAX = -50.0, 500.0
#: Grenze der Hinweisregel UMG_VIELE (UM-05)
VIELE_OBJEKTE = 3000
#: Streuen: so viele Objekte höchstens je Vorgang (Schutz vor versehentlich riesigen Flächen)
MAX_STREUUNG = 10000
#: Standard-Mindestabstand zur Fahrbahn: hinter der Mauer (Vorgabe des Auftrags)
STANDARD_ABSTAND_FAHRBAHN = 15.0
#: Loch des Außenterrains in Weltkoordinaten (Vertrag Regel UMG_AUSSERHALB): x −920…1128, z −1008…1040
WELT_X = (-920.0, 1128.0)
WELT_Z = (-1008.0, 1040.0)
#: Rand, den das Streuen zum Außenterrain-Loch einhält
WELT_RAND = 5.0
#: Garagengebäude hinter der Boxengasse (Schätzung, der Bau legt sie nach Boxenparametern an)
BOXEN_ZUSATZ_M = 15.0

#: Geländeformen (Vertrag Abschnitt 1a): ``huegel`` in der Grundfläche (sichtbar und Fahrphysik, negativ = Senke),
#: ``berge`` im Außenterrain außerhalb des Streckenbereichs (nur sichtbar). Grenzen wie im Werkzeugkasten.
FORM_ARTEN = ("huegel", "berge")
FORM_SCHLUESSEL = ("id", "x", "z", "hoehe_m", "radius_m")
FORM_GRENZEN = {"huegel": {"hoehe_m": (-60.0, 200.0), "radius_m": (30.0, 800.0)},
                "berge": {"hoehe_m": (10.0, 2000.0), "radius_m": (100.0, 5000.0)}}
FORM_STANDARD = {"huegel": {"hoehe_m": 40.0, "radius_m": 200.0}, "berge": {"hoehe_m": 400.0, "radius_m": 450.0}}
FORM_PRAEFIX = {"huegel": "h", "berge": "b"}
FORM_NAMEN = {"huegel": N_("Hill"), "berge": N_("Mountain")}
#: Berge: Mitte höchstens so weit von der Mitte des Streckenbereichs (Regel UMG_BERG_LAGE)
BERG_ENTFERNUNG_MAX = 12000.0
#: Berge: Lochrand bleibt fest (10 m), Übergang 150 m (Regel UMG_BERG_RAND)
BERG_RAND_M = 160.0

KATEGORIEN = ("baum", "busch", "palme", "gebaeude", "zelt", "moebel", "flagge", "licht", "sonstiges")
KATEGORIE_NAMEN = {"baum": N_("Trees"), "busch": N_("Bushes"), "palme": N_("Palms"), "gebaeude": N_("Buildings"),
                   "zelt": N_("Tents"), "moebel": N_("Furniture"), "flagge": N_("Flags"), "licht": N_("Lights"),
                   "sonstiges": N_("Other")}
KATEGORIE_FARBEN = {"baum": "#4CAF50", "busch": "#9CCC65", "palme": "#26C6A0", "gebaeude": "#C8B08A", "zelt": "#EF6F6C",
                    "moebel": "#BA68C8", "flagge": "#FFB74D", "licht": "#FFE45C", "sonstiges": "#90A4AE"}
UNBEKANNT_FARBE = "#8D9BA9"
UNBEKANNT_RADIUS_M = 3.0


# ------------------------------------------------------------------ Datei
def neu(bodenbewuchs=True):
    """Leere Umgebungsdatei (Bodenbewuchs ist bei neuen Dateien an)."""
    return {"schema": SCHEMA, "bodenbewuchs": bool(bodenbewuchs), "objekte": []}


def _zahl(wert, name, standard=None):
    if wert is None and standard is not None:
        return float(standard)
    if isinstance(wert, bool) or not isinstance(wert, (int, float)) or not math.isfinite(wert):
        raise ValueError(f"{name}: Zahl erwartet")
    return float(wert)


def normalisiere_objekt(objekt):
    """Ein Objekt mit Standardwerten; unbekannte Schlüssel bleiben erhalten (``ValueError`` bei ungültigen Werten)."""
    if not isinstance(objekt, dict):
        raise ValueError("Objekt: Eintrag erwartet")
    ergebnis = dict(objekt)
    if not isinstance(objekt.get("id"), str) or not objekt["id"]:
        raise ValueError("Objekt: id (Text) fehlt")
    if not isinstance(objekt.get("modell"), str) or not objekt["modell"]:
        raise ValueError(f"Objekt {objekt['id']}: modell fehlt")
    ergebnis["x"] = _zahl(objekt.get("x"), "x")
    ergebnis["z"] = _zahl(objekt.get("z"), "z")
    ergebnis["hoehe_m"] = _zahl(objekt.get("hoehe_m"), "hoehe_m", 0.0)
    ergebnis["drehung_grad"] = _zahl(objekt.get("drehung_grad"), "drehung_grad", 0.0)
    ergebnis["skala"] = _zahl(objekt.get("skala"), "skala", 1.0)
    gruppe = objekt.get("gruppe", "")
    ergebnis["gruppe"] = "" if gruppe is None else str(gruppe)
    return ergebnis


def normalisieren(daten):
    """Standardwerte setzen, Typen prüfen; unbekannte Schlüssel bleiben (``ValueError`` bei ungültigen Daten)."""
    if not isinstance(daten, dict):
        raise ValueError("umgebung.json: Objekt erwartet")
    if daten.get("schema", SCHEMA) != SCHEMA:
        raise ValueError(f"umgebung.json: unbekannte Fassung {daten.get('schema')!r}")
    ergebnis = dict(daten)
    ergebnis["schema"] = SCHEMA
    ergebnis["bodenbewuchs"] = bool(daten.get("bodenbewuchs", True))
    objekte = daten.get("objekte", [])
    if not isinstance(objekte, list):
        raise ValueError("objekte: Liste erwartet")
    ergebnis["objekte"] = [normalisiere_objekt(o) for o in objekte]
    ids = [o["id"] for o in ergebnis["objekte"]]
    if "gelaende" in daten:
        gelaende = daten["gelaende"]
        if not isinstance(gelaende, dict):
            raise ValueError("gelaende: Eintrag erwartet")
        gelaende = dict(gelaende)
        for art in FORM_ARTEN:
            liste = gelaende.get(art, [])
            if not isinstance(liste, list):
                raise ValueError(f"gelaende.{art}: Liste erwartet")
            gelaende[art] = [normalisiere_form(art, f) for f in liste]
            ids += [f["id"] for f in gelaende[art]]
        ergebnis["gelaende"] = gelaende
    if len(set(ids)) != len(ids):
        raise ValueError("objekte: ids müssen eindeutig sein")
    return ergebnis


def normalisiere_form(art, form):
    """Hügel oder Berg mit geprüften Werten (``ValueError`` bei Fehlern); unbekannte Schlüssel bleiben."""
    if not isinstance(form, dict) or not isinstance(form.get("id"), str) or not form["id"]:
        raise ValueError(f"gelaende.{art}: Eintrag mit id erwartet")
    ergebnis = dict(form)
    for feld in ("x", "z", "hoehe_m", "radius_m"):
        ergebnis[feld] = _zahl(form.get(feld), feld)
    for feld, (unten, oben) in FORM_GRENZEN[art].items():
        if not unten <= ergebnis[feld] <= oben:
            raise ValueError(f"{form['id']}: {feld} {ergebnis[feld]:g} außerhalb {unten:g}…{oben:g}")
    return ergebnis


def geordnet(daten):
    """Kopie mit fester Schlüsselreihenfolge (Kopf, Objekte; unbekannte Schlüssel zuletzt)."""
    ergebnis = {k: daten[k] for k in KOPF_SCHLUESSEL if k in daten}
    ergebnis.update({k: v for k, v in daten.items() if k not in ergebnis})
    ergebnis["objekte"] = [
        {**{k: o[k] for k in OBJEKT_SCHLUESSEL if k in o}, **{k: v for k, v in o.items() if k not in OBJEKT_SCHLUESSEL}}
        for o in daten.get("objekte", [])]
    if "gelaende" in daten:
        gelaende = {art: daten["gelaende"][art] for art in FORM_ARTEN if art in daten["gelaende"]}
        gelaende.update({k: v for k, v in daten["gelaende"].items() if k not in gelaende})
        for art in FORM_ARTEN:
            if art in gelaende:
                gelaende[art] = [{**{k: f[k] for k in FORM_SCHLUESSEL if k in f},
                                  **{k: v for k, v in f.items() if k not in FORM_SCHLUESSEL}} for f in gelaende[art]]
        ergebnis["gelaende"] = gelaende
    return ergebnis


def als_text(daten):
    """UTF-8-Text der Datei: Einrückung 2, feste Schlüsselreihenfolge, Zeilenende ``\\n``."""
    return json.dumps(geordnet(daten), ensure_ascii=False, allow_nan=False, indent=2) + "\n"


def lade_text(text):
    """Text einer ``umgebung.json`` lesen und normalisieren (``ValueError`` bei Fehlern)."""
    try:
        return normalisieren(json.loads(text.lstrip("﻿")))
    except json.JSONDecodeError as fehler:
        raise ValueError(f"umgebung.json: kein gültiges JSON ({fehler})") from fehler


# ------------------------------------------------------------------ Objekte
def naechste_ids(vorhanden, anzahl=1):
    """``anzahl`` freie Kennungen ``o<n>`` (fortlaufend nach der höchsten vorhandenen Nummer)."""
    belegt = set(vorhanden)
    hoechste = max((int(m.group(1)) for k in belegt if (m := re.fullmatch(r"o(\d+)", k))), default=0)
    ergebnis = []
    while len(ergebnis) < anzahl:
        hoechste += 1
        if f"o{hoechste}" not in belegt:
            ergebnis.append(f"o{hoechste}")
    return ergebnis


def neues_objekt(vorhanden_ids, modell, x, z, hoehe_m=0.0, drehung_grad=0.0, skala=1.0, gruppe=""):
    kennung = naechste_ids(vorhanden_ids)[0]
    return {"id": kennung, "modell": modell, "x": round(float(x), 2), "z": round(float(z), 2),
            "hoehe_m": round(float(hoehe_m), 2), "drehung_grad": round(float(drehung_grad) % 360.0, 1),
            "skala": round(min(max(float(skala), SKALA_MIN), SKALA_MAX), 2), "gruppe": gruppe}


def formen(daten):
    """Alle Geländeformen als Liste ``(art, form)`` (Hügel zuerst)."""
    gelaende = (daten or {}).get("gelaende") or {}
    return [(art, f) for art in FORM_ARTEN for f in gelaende.get(art, [])]


def alle_ids(daten):
    """Kennungen aller Objekte und Geländeformen (gemeinsamer Namensraum)."""
    return {o["id"] for o in (daten or {}).get("objekte", [])} | {f["id"] for _, f in formen(daten)}


def neue_form(vorhanden_ids, art, x, z, hoehe_m=None, radius_m=None):
    """Neuer Hügel (Kennung ``h<n>``) bzw. Berg (``b<n>``); Werte auf die Grenzen begrenzt."""
    praefix = FORM_PRAEFIX[art]
    belegt = set(vorhanden_ids)
    n = max((int(m.group(1)) for k in belegt if (m := re.fullmatch(praefix + r"(\d+)", k))), default=0) + 1
    while f"{praefix}{n}" in belegt:
        n += 1
    return {"id": f"{praefix}{n}", "x": round(float(x), 1), "z": round(float(z), 1),
            "hoehe_m": form_wert(art, "hoehe_m", FORM_STANDARD[art]["hoehe_m"] if hoehe_m is None else hoehe_m),
            "radius_m": form_wert(art, "radius_m", FORM_STANDARD[art]["radius_m"] if radius_m is None else radius_m)}


def form_wert(art, feld, wert):
    unten, oben = FORM_GRENZEN[art][feld]
    return round(min(max(float(wert), unten), oben), 1)


def form_art(daten, kennung):
    return next((art for art, f in formen(daten) if f["id"] == kennung), None)


def rand_abstand(x, z, versatz=(0.0, 0.0, 0.0)):
    """Abstand (m) des Definitionspunkts vom Streckenbereich nach außen; 0 innen."""
    wx, wz = x + versatz[0], z + versatz[2]
    dx = max(WELT_X[0] - wx, wx - WELT_X[1], 0.0)
    dz = max(WELT_Z[0] - wz, wz - WELT_Z[1], 0.0)
    return math.hypot(dx, dz)


def form_problem(art, form, versatz=(0.0, 0.0, 0.0)):
    """Kurzer Text, wenn die Form nach den Regeln UMG_HUEGEL_LAGE/UMG_BERG_LAGE/UMG_BERG_RAND falsch liegt, sonst None."""
    aussen = rand_abstand(form["x"], form["z"], versatz)
    if art == "huegel":
        return N_("Centre outside the track area") if aussen > 0 else None
    mitte = ((WELT_X[0] + WELT_X[1]) / 2, (WELT_Z[0] + WELT_Z[1]) / 2)
    if aussen <= 0:
        return N_("Centre inside the track area: only hills work here")
    if math.hypot(form["x"] + versatz[0] - mitte[0], form["z"] + versatz[2] - mitte[1]) > BERG_ENTFERNUNG_MAX:
        return N_("more than 12 km from the centre")
    if aussen < form["radius_m"] * 0.5 + BERG_RAND_M:
        return N_("reaches the track area and is cut off there")
    return None


def katalog_index(katalog):
    """{Modell-Kennung: Eintrag} aus einem Katalog (leer, wenn keiner geladen ist)."""
    return {m["modell"]: m for m in (katalog or {}).get("modelle", []) if m.get("modell")}


def kurzname(modell):
    """Anzeigename eines Modells ohne Pfad (letztes Stück der Kennung)."""
    return str(modell).rsplit("/", 1)[-1]


def objekt_radius(objekt, index):
    """Radius des Kreises auf der Karte in Metern (Katalogradius mal Skala)."""
    eintrag = index.get(objekt.get("modell")) or {}
    return float(eintrag.get("radius_m") or UNBEKANNT_RADIUS_M) * float(objekt.get("skala", 1.0))


def objekt_farbe(objekt, index):
    eintrag = index.get(objekt.get("modell"))
    return KATEGORIE_FARBEN.get((eintrag or {}).get("kategorie"), UNBEKANNT_FARBE) if eintrag else UNBEKANNT_FARBE


def gruppen(objekte):
    """Namen der Gruppen (ohne leere), nach erstem Auftreten."""
    return list(dict.fromkeys(o.get("gruppe") for o in objekte if o.get("gruppe")))


def neue_gruppe(objekte, vorlage="Streuung {n}"):
    """Freier Gruppenname ``Streuung 1``, ``Streuung 2`` …"""
    belegt = set(gruppen(objekte))
    n = 1
    while vorlage.format(n=n) in belegt:
        n += 1
    return vorlage.format(n=n)


def im_weltbereich(x, z, versatz=(0.0, 0.0, 0.0), rand=0.0):
    """Liegt der Definitionspunkt (x, z) im Loch des Außenterrains (Welt = Definition + Versatz)?"""
    wx, wz = x + versatz[0], z + versatz[2]
    return WELT_X[0] + rand <= wx <= WELT_X[1] - rand and WELT_Z[0] + rand <= wz <= WELT_Z[1] - rand


# ------------------------------------------------------------------ Abstand zur Strecke
class Hindernis:
    """Abstand eines Punkts zur Strecke (Fahrbahn, Randbänder, Mauer) aus der Vorschaugeometrie.

    Je Mittellinienstation: Position, linke Normale und Abstand der äußersten Kante (Mauer) nach links/rechts.
    Der Abstand ist negativ auf der Strecke und sonst der Seitenabstand zur Mauer; er ist nie größer als der
    wahre Abstand (Streuen bleibt auf der sicheren Seite). Zusätzlich lassen sich Linien (Boxengasse) mit einem
    Zusatzradius sperren.
    """

    def __init__(self, pos, normale, links, rechts, linien=(), linien_zusatz=BOXEN_ZUSATZ_M):
        self.pos = np.asarray(pos, float).reshape(-1, 2)
        self.normale = np.asarray(normale, float).reshape(-1, 2)
        self.links = np.asarray(links, float).reshape(-1)
        self.rechts = np.asarray(rechts, float).reshape(-1)
        self.linien = [np.asarray(l, float).reshape(-1, 2) for l in linien if len(l)]
        self.linien_zusatz = float(linien_zusatz)

    @classmethod
    def aus_vorschau(cls, vorschau, analyse=None):
        """Aus der Generatorvorschau (``sides[...].distances`` = Abstände der Bänder, letzte Kante = Mauer)."""
        pos = np.asarray(vorschau["pos"], float)[:, [0, 2]]
        außen = []
        for seite, breite in (("links", vorschau["wl"]), ("rechts", vorschau["wr"])):
            abstaende = np.asarray(vorschau["sides"][seite]["distances"], float)
            außen.append(np.asarray(breite, float) + abstaende[:, -1])
        linien = []
        box = (analyse or {}).get("boxengasse")
        if box:
            linien.append(np.column_stack([box["x"], box["z"]]))
        return cls(pos, vorschau["normal"], außen[0], außen[1], linien)

    @classmethod
    def aus_eingabe(cls, eingabe, mauer_m=22.0):
        """Aus der Eingabegeometrie (ohne Vorschau): Fahrbahnbreite plus ein Standardabstand der Mauer."""
        pos = np.asarray(eingabe["pos"], float).reshape(-1, 3)[:, [0, 2]]
        if len(pos) < 2:
            return None
        tangente = np.gradient(pos, axis=0)
        norm = np.maximum(np.hypot(tangente[:, 0], tangente[:, 1]), 1e-3)
        normale = np.column_stack([tangente[:, 1] / norm, -tangente[:, 0] / norm])
        return cls(pos, normale, np.asarray(eingabe["wl"], float) + mauer_m, np.asarray(eingabe["wr"], float) + mauer_m)

    def abstand(self, punkte):
        """Abstand je Punkt (m): < 0 auf der Strecke, sonst Abstand zur Mauer bzw. zu den gesperrten Linien."""
        punkte = np.asarray(punkte, float).reshape(-1, 2)
        ergebnis = np.full(len(punkte), np.inf)
        if len(self.pos):
            for a in range(0, len(punkte), 1024):
                teil = punkte[a:a + 1024]
                d2 = ((teil[:, None, :] - self.pos[None, :, :]) ** 2).sum(-1)
                i = d2.argmin(1)
                seitlich = ((teil - self.pos[i]) * self.normale[i]).sum(1)
                außen = np.where(seitlich >= 0, self.links[i], self.rechts[i])
                ergebnis[a:a + 1024] = np.abs(seitlich) - außen
        for linie in self.linien:
            for a in range(0, len(punkte), 1024):
                teil = punkte[a:a + 1024]
                d = np.sqrt(((teil[:, None, :] - linie[None, :, :]) ** 2).sum(-1)).min(1) - self.linien_zusatz
                ergebnis[a:a + 1024] = np.minimum(ergebnis[a:a + 1024], d)
        return ergebnis


# ------------------------------------------------------------------ Streuen
class StreuFehler(ValueError):
    """Streuen nicht möglich; ``code`` benennt den Grund (``keine_modelle``, ``keine_flaeche``, ``zu_viele``)."""

    def __init__(self, code, text, **werte):
        super().__init__(text)
        self.code, self.werte = code, werte


class Gebiet:
    """Fläche zum Streuen: Rechteck (x0, z0, x1, z1) oder Pinselstrich (Mittelpunkte und Radius)."""

    def __init__(self, form, rechteck=None, punkte=None, radius=0.0):
        self.form = form
        if form == "rechteck":
            x0, z0, x1, z1 = (float(v) for v in rechteck)
            self.box = (min(x0, x1), min(z0, z1), max(x0, x1), max(z0, z1))
            self.punkte = None
        elif form == "pinsel":
            radius = float(radius)
            if radius <= 0 or not punkte:
                raise StreuFehler("keine_flaeche", "Pinsel ohne Fläche")
            self.punkte = self._ausduennen(np.asarray(punkte, float).reshape(-1, 2), radius)
            self.box = (self.punkte[:, 0].min() - radius, self.punkte[:, 1].min() - radius,
                        self.punkte[:, 0].max() + radius, self.punkte[:, 1].max() + radius)
        else:
            raise ValueError(form)
        self.radius = float(radius)

    @staticmethod
    def _ausduennen(punkte, radius, maximum=500):
        """Pinselstrich als dichte Punktfolge: zwischen weit auseinanderliegenden Mausstellen wird eingefügt (Abstand
        höchstens Radius/2), dicht liegende Punkte entfallen; höchstens ``maximum`` Punkte."""
        schritt = radius / 2
        folge = [tuple(punkte[0])]
        for p in punkte[1:]:
            q = folge[-1]
            laenge = math.dist(p, q)
            if laenge < radius / 3:
                continue
            teile = max(1, int(math.ceil(laenge / schritt)))
            folge += [(q[0] + (p[0] - q[0]) * k / teile, q[1] + (p[1] - q[1]) * k / teile) for k in range(1, teile + 1)]
        feld = np.asarray(folge, float)
        if len(feld) > maximum:
            feld = feld[np.linspace(0, len(feld) - 1, maximum).astype(int)]
        return feld

    def enthaelt(self, xz):
        xz = np.asarray(xz, float).reshape(-1, 2)
        innen = ((xz[:, 0] >= self.box[0]) & (xz[:, 0] <= self.box[2]) & (xz[:, 1] >= self.box[1]) & (xz[:, 1] <= self.box[3]))
        if self.form == "pinsel":
            nah = np.zeros(len(xz), bool)
            for a in range(0, len(xz), 2048):
                teil = xz[a:a + 2048]
                nah[a:a + 2048] = (((teil[:, None, :] - self.punkte[None]) ** 2).sum(-1).min(1) <= self.radius ** 2)
            innen &= nah
        return innen

    def flaeche_m2(self):
        breite, tiefe = self.box[2] - self.box[0], self.box[3] - self.box[1]
        if self.form == "rechteck":
            return breite * tiefe
        # Vereinigung von Kreisen: Anteil eines festen Rasters (deterministisch)
        n = 80
        gx = np.linspace(self.box[0], self.box[2], n)
        gz = np.linspace(self.box[1], self.box[3], n)
        gitter = np.array([(x, z) for z in gz for x in gx])
        return float(self.enthaelt(gitter).mean()) * breite * tiefe


def streuen(gebiet, modelle, dichte_ha, seed, *, hindernis=None, abstand_fahrbahn=STANDARD_ABSTAND_FAHRBAHN,
            abstand_objekte=3.0, skala=(0.8, 1.2), drehung_zufall=True, hoehe_m=0.0, gruppe="", vorhanden=(),
            versatz=(0.0, 0.0, 0.0), maximum=MAX_STREUUNG):
    """Objekte zufällig, aber reproduzierbar über ``gebiet`` verteilen.

    ``dichte_ha``: Objekte je Hektar (ohne die Flächen, die wegen des Abstands zur Strecke entfallen: die Zahl
    gilt für die gewählte Fläche, danach werden Punkte auf der Strecke und nahe an ihr verworfen). Gleicher Saatwert
    und gleiche Eingaben liefern dieselben Objekte. ``vorhanden``: bereits vorhandene Objekte (Mindestabstand
    auch zu ihnen, Kennungen laufen danach weiter). Rückgabe: (Objektliste, Auskunft).
    """
    modelle = list(modelle)
    if not modelle:
        raise StreuFehler("keine_modelle", "Kein Modell gewählt")
    flaeche = gebiet.flaeche_m2()
    if flaeche <= 0:
        raise StreuFehler("keine_flaeche", "Keine Fläche")
    wunsch = int(round(float(dichte_ha) * flaeche / 10000.0))
    if wunsch > maximum:
        raise StreuFehler("zu_viele", "Zu viele Objekte", anzahl=wunsch, maximum=maximum)
    auskunft = {"gewuenscht": wunsch, "flaeche_ha": flaeche / 10000.0, "verworfen_strecke": 0,
                "verworfen_abstand": 0, "verworfen_welt": 0}
    if wunsch <= 0:
        return [], auskunft
    zufall = random.Random(int(seed))
    skala_min, skala_max = sorted((float(skala[0]), float(skala[1])))
    skala_min, skala_max = max(skala_min, SKALA_MIN), min(skala_max, SKALA_MAX)
    abstand_objekte = max(float(abstand_objekte), 0.0)
    zelle = max(abstand_objekte, 1.0)
    raster = {}

    def zelle_von(x, z):
        return int(math.floor(x / zelle)), int(math.floor(z / zelle))

    def frei(x, z):
        if abstand_objekte <= 0:
            return True
        cx, cz = zelle_von(x, z)
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for px, pz in raster.get((cx + dx, cz + dz), ()):
                    if (px - x) ** 2 + (pz - z) ** 2 < abstand_objekte ** 2:
                        return False
        return True

    for o in vorhanden:
        raster.setdefault(zelle_von(o["x"], o["z"]), []).append((o["x"], o["z"]))
    gesetzt = []
    for _runde in range(10):
        if len(gesetzt) >= wunsch:
            break
        anzahl = max(256, int((wunsch - len(gesetzt)) * 2.5))
        kandidaten = np.array([(zufall.uniform(gebiet.box[0], gebiet.box[2]), zufall.uniform(gebiet.box[1], gebiet.box[3]))
                               for _ in range(anzahl)])
        ok = gebiet.enthaelt(kandidaten)
        welt = np.array([im_weltbereich(x, z, versatz, WELT_RAND) for x, z in kandidaten])
        auskunft["verworfen_welt"] += int((ok & ~welt).sum())
        ok &= welt
        if hindernis is not None and ok.any():
            weit = hindernis.abstand(kandidaten[ok]) >= float(abstand_fahrbahn)
            auskunft["verworfen_strecke"] += int((~weit).sum())
            ok[np.flatnonzero(ok)[~weit]] = False
        for x, z in kandidaten[ok]:
            if len(gesetzt) >= wunsch:
                break
            if not frei(x, z):
                auskunft["verworfen_abstand"] += 1
                continue
            raster.setdefault(zelle_von(x, z), []).append((x, z))
            gesetzt.append((float(x), float(z), zufall.choice(modelle), zufall.uniform(skala_min, skala_max),
                            zufall.uniform(0.0, 360.0) if drehung_zufall else 0.0))
    ids = naechste_ids({o["id"] for o in vorhanden}, len(gesetzt))
    objekte = [{"id": kennung, "modell": modell, "x": round(x, 2), "z": round(z, 2), "hoehe_m": round(float(hoehe_m), 2),
                "drehung_grad": round(drehung, 1), "skala": round(s, 2), "gruppe": gruppe}
               for kennung, (x, z, modell, s, drehung) in zip(ids, gesetzt)]
    auskunft["gesetzt"] = len(objekte)
    return objekte, auskunft


def kopie(daten):
    return copy.deepcopy(daten)
