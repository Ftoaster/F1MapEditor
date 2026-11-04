#!/usr/bin/env python3
"""Neue Strecke im Portimão-Träger zusammenbauen (AP11b).

Befehle:
    bauen    Generator (falls nötig) → Träger-ERP → Geometrie (AP11a) → Minimap (AP11c) → Umgebung
             (AP-U1, --umgebung) → Welt aufräumen → Streckenlogik → Licht → ERP (zwei PVS-Varianten) → Pakete → Prüfungen
    pruefen  Prüfregeln ohne Bau (Definition, Generator, Profil) und für gebaute Logik → JSON (AP24)
    regeln   Regelkatalog mit Meldungs-IDs (JSON, --md)            profile   Bauprofile (JSON)

Editor-Schnittstelle (AP24, docs/editor_schnittstelle.md):
    python neue_strecke_tool.py bauen --ereignisse-json --profil empfohlen --projekt <Projektordner>         --name <name> --ausgabe <Arbeitsordner> --paket-ordner <Ziel> --abbruch-datei <Datei>

Beispiel (im Ordner ego-werkzeuge):
    python neue_strecke_tool.py bauen
    python neue_strecke_tool.py bauen --ohne-geometrie --ohne-pakete     # schneller Probelauf
    python neue_strecke_tool.py bauen --definition strecke/beispiele/portimao_form.json \
        --generator-ausgabe testausgabe/strecke_portimao_form --neu-erzeugen --versatz 0,0,0 \
        --name portimao_form_ap11 --ausgabe testausgabe/neue_strecke/portimao_form \
        --bericht-md ergebnisse/portimao_form_ap11.md                   # AP13: Portimãos Form, ohne Versatz
    python neue_strecke_tool.py bauen --name teststrecke_v18 --nur-original-pvs --streckencode sochi \
        --streckenname Teststrecke --aktivierung-vorlage portimao        # AP16: eigener Code (docs/streckencode.md)

Ausgaben:
    testausgabe/neue_strecke/zusammenbau/      Arbeits-ERPs, Draufsichten, bericht.json
    testausgabe/pakete/<name>.zip              Variante PVS „alles sichtbar“
    testausgabe/pakete/<name>_original_pvs.zip Variante mit unverändertem Portimão-PVS
                                               (Spieltest 15.10.2026: beide laden, Absturz bei fliegender Runde)
    ergebnisse/neue_strecke.md                 Bericht

Sicherheit: Spielordner und Downloads werden nur gelesen; Ausgaben dort werden verweigert.
"""

import argparse
import zipfile
import contextlib
import datetime
import subprocess
import hashlib
import importlib
import json
import math
import os
import shutil
import signal
import sys
import time
import traceback
from collections import Counter

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from ego import bxml, erp, grafik, licht, paket, pkg, pvs, spielpfade, streckenlogik, trn, tsquadtree  # noqa: E402
from strecke import platzierung, pruefregeln, renndaten, spiel_welt, streckencode  # noqa: E402
from strecke.spielformat import Schreiber  # noqa: E402

AUSGABE = os.path.join(HIER, "testausgabe", "neue_strecke", "zusammenbau")
PAKETE = os.path.join(HIER, "testausgabe", "pakete")
OBERFLAECHEN_FARBEN = {"asphalt": (70, 70, 74), "randstein": (200, 40, 40), "gras": (70, 130, 60),
                       "kies": (190, 170, 120), "mauer": (200, 200, 200), "boxengasse": (110, 110, 118),
                       "linie": (240, 240, 240)}


def _pfad_pruefen(pfad):
    if spielpfade.geschuetzt(pfad):
        raise BauFehler("Ausgabe in geschütztem Ordner (%s)" % pfad)


def _relativ(pfad):
    """Pfad für Berichte relativ zum Werkzeug; auf einem anderen Laufwerk bleibt er absolut."""
    try:
        return os.path.relpath(pfad, HIER)
    except ValueError:
        return os.path.abspath(pfad)


def _log(text):
    if EREIGNISSE is not None:   # AP24: im JSON-Modus als Ereignis
        EREIGNISSE.senden("log", phase=EREIGNISSE.phase, text=str(text))
    else:
        print("[%s] %s" % (time.strftime("%H:%M:%S"), text), flush=True)


def _sha256(pfad):
    h = hashlib.sha256()
    with open(pfad, "rb") as f:
        for b in iter(lambda: f.read(1 << 24), b""):
            h.update(b)
    return h.hexdigest()


# =============================================================================== Schritte
def generator(args):
    npz = os.path.join(args.generator_ausgabe, "strecke.npz")
    logik = os.path.join(args.generator_ausgabe, "strecke_logik.json")
    if args.neu_erzeugen or not (os.path.exists(npz) and os.path.exists(logik)):
        _log("Generator: %s → %s" % (args.definition, args.generator_ausgabe))
        import strecke
        strecke.erzeugen(args.definition, args.generator_ausgabe)
    else:
        _log("Generator-Ausgabe vorhanden: %s" % args.generator_ausgabe)
    return npz, logik


def traeger_entpacken(args):
    os.makedirs(args.traeger_cache, exist_ok=True)
    ziel = os.path.join(args.traeger_cache, "traeger_portimao.erp")
    with paket.ModPaket(args.traeger) as p:
        info = p.zip.getinfo(p.erp_name)
        if os.path.exists(ziel) and os.path.getsize(ziel) == info.file_size and not args.neu_entpacken:
            _log("Träger-ERP vorhanden (%.1f MB)" % (info.file_size / 1e6))
        else:
            _log("Träger-ERP entpacken …")
            p.erp_entpacken(ziel)
        return ziel, p.manifest, p.images_roh, p.minimap_roh


def modul_laden(name):
    try:
        return importlib.import_module(name), None
    except ModuleNotFoundError as e:
        if e.name == name:
            return None, "Modul %s nicht vorhanden" % name
        raise


# =============================================================================== Prüfungen
def _netz_platziert(npz):
    d = np.load(npz)
    v = platzierung.punkte(d["vertices"])
    return v, d["dreiecke"].astype(np.int64), d["oberflaeche"], [str(n) for n in d["oberflaechen_namen"]]


def punkt_im_netz(p, v, tri, surf, namen):
    """Oberflächenname und Höhe des Netzes unter Punkt p (XZ), None außerhalb."""
    a, b, c = v[tri[:, 0]], v[tri[:, 1]], v[tri[:, 2]]
    x, z = p[0], p[2]
    d = (b[:, 2] - c[:, 2]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 2] - c[:, 2])
    with np.errstate(divide="ignore", invalid="ignore"):
        l1 = ((b[:, 2] - c[:, 2]) * (x - c[:, 0]) + (c[:, 0] - b[:, 0]) * (z - c[:, 2])) / d
        l2 = ((c[:, 2] - a[:, 2]) * (x - c[:, 0]) + (a[:, 0] - c[:, 0]) * (z - c[:, 2])) / d
        l3 = 1 - l1 - l2
    innen = np.where((l1 >= -1e-6) & (l2 >= -1e-6) & (l3 >= -1e-6) & (np.abs(d) > 1e-12))[0]
    if not len(innen):
        return None, None
    hoehen = l1[innen] * a[innen, 1] + l2[innen] * b[innen, 1] + l3[innen] * c[innen, 1]
    # oberste Fläche unter dem Punkt (Garagenplätze stehen unter dem Dach des Boxengebäudes)
    darunter = hoehen <= p[1] + 0.5
    if darunter.any():
        innen, hoehen = innen[darunter], hoehen[darunter]
    k = int(np.argmax(hoehen))
    return namen[int(surf[innen[k]])], float(hoehen[k])


def _abstand_polylinie(p, punkte, geschlossen=True):
    P = np.asarray(punkte, float)[:, [0, 2]]
    A = P
    B = np.roll(P, -1, axis=0) if geschlossen else P[1:]
    A = A if geschlossen else P[:-1]
    q = np.asarray(p, float)[[0, 2]]
    ab = B - A
    t = np.clip(((q - A) * ab).sum(1) / np.maximum((ab * ab).sum(1), 1e-12), 0, 1)
    return float(np.min(np.linalg.norm(A + t[:, None] * ab - q, axis=1)))


def raster_netz(v, tri, surf, namen, aufl=1.0, halb=1024):
    """Höhen- und Oberflächenraster (1 m) des Generatornetzes in Weltkoordinaten."""
    n = int(2 * halb / aufl)
    hoehe = Image.new("F", (n, n), -9999.0)
    art = Image.new("L", (n, n), 0)
    dh, da = ImageDraw.Draw(hoehe), ImageDraw.Draw(art)
    # Mauer zuletzt (oben), sonst Reihenfolge der Klassen
    reihen = sorted(range(len(namen)), key=lambda i: namen[i] == "mauer")
    for si in reihen:
        for t in tri[surf == si]:
            pts = [((v[j, 0] + halb) / aufl, (v[j, 2] + halb) / aufl) for j in t]
            dh.polygon(pts, fill=float(v[t, 1].mean()))
            da.polygon(pts, fill=si + 1)
    return np.asarray(hoehe), np.asarray(art)


def trn_pruefen(archiv, original_hash, hoehe, art, namen, aufl=1.0, halb=1024):
    r = next((x for x in archiv if x.resource_type == "TRN~"), None)
    if r is None:
        return {"fehler": "keine TRN~"}
    geaendert = r.hash != original_hash
    jp = trn.open_trn(r.fragments[0].read())
    kacheln = jp.geometry()
    for k in kacheln:
        k.validate()
    vert, dre, codes = trn.mesh(kacheln)
    zent = vert[dre].mean(1)
    ix = ((zent[:, 0] + halb) / aufl).astype(int)
    iz = ((zent[:, 2] + halb) / aufl).astype(int)
    ok = (ix >= 0) & (iz >= 0) & (ix < art.shape[1]) & (iz < art.shape[0])
    innen = np.zeros(len(zent), bool)
    innen[ok] = art[iz[ok], ix[ok]] > 0
    fahrbahn = np.zeros(len(zent), bool)
    fb_ids = [i + 1 for i, n in enumerate(namen) if n in ("asphalt", "boxengasse", "linie", "randstein")]
    fahrbahn[ok] = np.isin(art[iz[ok], ix[ok]], fb_ids)
    dy = np.full(len(zent), np.nan)
    dy[ok] = zent[ok, 1] - hoehe[iz[ok], ix[ok]]
    fremd = fahrbahn & (np.abs(dy) > 1.0)
    code_str = [c.decode("ascii", "replace") if isinstance(c, bytes) else str(c) for c in codes]
    return {"geaendert": bool(geaendert), "kacheln": len(kacheln), "dreiecke": int(len(dre)),
            "im_neuen_streckenbereich": int(innen.sum()), "auf_neuer_fahrbahn": int(fahrbahn.sum()),
            "auf_fahrbahn_hoehe_abweichend_gt_1m": int(fremd.sum()),
            "codes_auf_fahrbahn": dict(Counter(code_str[i] for i in np.where(fahrbahn)[0]).most_common(12)),
            "codes_abweichend": dict(Counter(code_str[i] for i in np.where(fremd)[0]).most_common(12))}


def ressourcen_neu_parsen(pfad, kennungen):
    """Alle geänderten Ressourcen mit dem passenden Leser neu einlesen."""
    ergebnis = Counter()
    fehler = []
    with erp.open_erp(pfad) as a:
        index = {r.identifier: r for r in a}
        for k in kennungen:
            r = index.get(k)
            if r is None:
                continue
            t = r.resource_type
            try:
                if t in ("World", "WOInstances", "CrowdPlacement", "EventGraph"):
                    for fr in r.fragments:
                        d = fr.read()
                        if d[:4] == b"!pkg":
                            pkg.lesen(d).pruefen()
                    art = "pkg"
                elif t in ("TrackSpace", "AISplineData", "TM", "TS", "AIBrakeSettings"):
                    bxml.decode(r.fragments[0].read())
                    for fr in r.fragments[1:]:
                        d = fr.read()
                        if bxml.is_tsmd(d):
                            bxml.parse_tsmd(d)
                    art = "bxml"
                elif t == "TSQuadTree":
                    tsquadtree.lesen(r.fragments[0].read()).pruefen()
                    art = "tsquadtree"
                elif t == "TRN~":
                    for k2 in trn.open_trn(r.fragments[0].read()).geometry():
                        k2.validate()
                    art = "trn"
                elif t == "PVSVisRes":
                    pvs.vis_lesen(r.fragments[0].read())
                    art = "pvs"
                elif t == "PVSObjIDRes":
                    pvs.obj_lesen(r.fragments[0].read())
                    art = "pvs"
                elif t == "LightMapLocRes":
                    licht.Lichtkarte.aus_bytes(r.fragments[0].read())
                    art = "licht"
                elif t == "GfxSurfaceRes":
                    f = grafik.Textur.aus_ressource(r).pruefen()
                    if f:
                        raise ValueError("; ".join(f[:3]))
                    art = "grafik_textur"
                elif t == "TerrainRes":
                    grafik.Terrain.aus_bytes(r.fragments[0].read())
                    art = "grafik_terrain"
                elif t == "GfxSRVResource":
                    grafik.TexturSicht.aus_bytes(r.fragments[0].read())
                    art = "grafik_sicht"
                else:
                    art = "nur_md5:" + t
                ergebnis[art] += 1
            except Exception as e:  # noqa: BLE001
                fehler.append("%s (%s): %s" % (k, t, e))
    return dict(ergebnis), fehler


def logik_pruefen(pfad, lg, plaetze, netz):
    """Neu einlesen über streckenlogik.lade/Schreiber; Lage der Plätze auf dem Netz."""
    v, tri, surf, namen = netz
    out = {}
    with erp.open_erp(pfad) as a:
        s = Schreiber(a, "portimao")   # prüft Brems-IDs gegen alle 38 .brakelinesettings
        st = s.original
        out["brems_ids"] = sorted(int(l["id"]) for l in st.linien["brake_lines"])
        out["bremsressourcen"] = len(s.bremsressourcen)
        out["reifengruppen_geprueft"] = len(s.bremspruefung)
        out["splines"] = {n: {"knoten": len(sp.punkte), "laenge_m": round(sp.laenge, 2), "ring": sp.geschlossen}
                          for n, sp in st.splines.items()}
        out["gates"] = len(st.gates)
        out["gates_box"] = sum(g.boxengasse for g in st.gates)
        zonen = [(z.name, z.typ, z.start, z.ende) for z in st.zonen]
        out["zonen"] = len(zonen)
        out["zonen_ausserhalb_0_1"] = [z for z in zonen if not (0 <= z[2] <= 1 and 0 <= z[3] <= 1)]
        out["ts_listen"] = [(vl["name"], vl["volumes"]) for vl in st.ts_listen]
        qt = next(r for r in a if r.resource_type == "TSQuadTree")
        baum = tsquadtree.lesen(qt.fragments[0].read())
        baum.pruefen()
        out["quadtree_punkte"] = len(baum.punkte)
        haupt = st.hauptlinie
        # Plätze aus allen Platz-Layern neu lesen
        plaetze_neu = []
        for kurz, r in spiel_welt.layer_ressourcen(a).items():
            if kurz.startswith("vehicles") or kurz == "safety_car":
                for i in pkg.lesen(r.fragments[0].read()).instanzen():
                    plaetze_neu.append((kurz, i.name, i._wert("template"), i.position, i.matrix))
    pruef = []
    for kurz, name, tpl, pos, m in plaetze_neu:
        art, n = spiel_welt._nummer(name)
        if art is None:
            art = "safetycar"
        flaeche, h = punkt_im_netz(pos, v, tri, surf, namen)
        eintrag = {"layer": kurz, "name": name, "art": art, "position": [round(x, 2) for x in pos],
                   "flaeche": flaeche, "dy_m": None if h is None else round(pos[1] - h, 3),
                   "abstand_mittellinie_m": round(_abstand_polylinie(pos, haupt.punkte), 2)}
        if art == "gridslot":
            eintrag["fortschritt"] = round(haupt.fortschritt_von(pos)[0], 5)
            eintrag["ok"] = flaeche in ("asphalt", "linie", "startbox") and eintrag["abstand_mittellinie_m"] < 7.5
        elif art == "f1garage":
            eintrag["ok"] = flaeche == "boxengasse"
        else:
            eintrag["ok"] = flaeche == "boxengasse"
        pruef.append(eintrag)
    out["plaetze"] = pruef
    out["plaetze_ok"] = sum(p["ok"] for p in pruef)
    out["plaetze_gesamt"] = len(pruef)
    return out


# =============================================================================== Draufsicht
def draufsicht(pfad_png, netz, lg, zustand, welt_pos, plaetze, ausschnitt=None, mass=0.8, titel=""):
    v, tri, surf, namen = netz
    if ausschnitt is None:
        ausschnitt = (-1100.0, 1100.0, -1100.0, 1100.0)
    x0, x1, z0, z1 = ausschnitt
    W, H = int((x1 - x0) * mass), int((z1 - z0) * mass)
    im = Image.new("RGB", (W, H + 70), (22, 26, 24))
    d = ImageDraw.Draw(im)
    try:
        schrift = ImageFont.truetype(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arial.ttf"), 13)
    except OSError:
        schrift = ImageFont.load_default()

    def P(x, z):
        return ((x - x0) * mass, (z - z0) * mass)

    # Rahmen: AO-Terrainbereich ±1024 m, Loch des Außenterrains (AP11a), PVS-Zellen Portimão
    d.rectangle([P(-1024, -1024), P(1024, 1024)], outline=(80, 80, 140))
    for i, (sp_name, sp) in enumerate(zustand.schreiber.original.splines.items()):
        pts = [P(p[0], p[2]) for p in sp.punkte]
        d.line(pts + ([pts[0]] if sp.geschlossen else []), fill=(95, 95, 95), width=1)
    for si in sorted(range(len(namen)), key=lambda i: namen[i] == "mauer"):
        farbe = OBERFLAECHEN_FARBEN.get(namen[si], (150, 150, 150))
        for t in tri[surf == si]:
            d.polygon([P(v[j, 0], v[j, 2]) for j in t], fill=farbe)
    sp = lg["spielformate"]
    for vl in sp["TS"]["volume_lists"]:
        for k, farbe in (("point1", (0, 160, 200)), ("point4", (0, 160, 200))):
            pts = [P(vo["gates"][0][k][0], vo["gates"][0][k][2]) for vo in vl["volumes"]]
            d.line(pts, fill=farbe, width=1)
    ai = sp["AISplineData"]
    haupt = [g for g in ai["gates"] if "pit" not in g["name"]]
    d.line([P(g["position"][0], g["position"][2]) for g in haupt] + [P(haupt[0]["position"][0], haupt[0]["position"][2])],
           fill=(230, 60, 160), width=1)
    gates = {int(g["id"]): g for g in ai["gates"]}
    for l in ai["brake_lines"]:
        g = gates[int(l["gate_id"])]
        x, z = P(g["position"][0], g["position"][2])
        d.ellipse([x - 3, z - 3, x + 3, z + 3], outline=(255, 0, 0))
    for (kurz, name, tpl, pos, modell, uid) in welt_pos:
        if pos is None or kurz in ("vehicles", "vehicles_myteam", "vehicles_current_myteam", "safety_car"):
            continue
        x, z = P(pos[0], pos[2])
        if 0 <= x < W and 0 <= z < H:
            d.point((x, z), fill=(220, 0, 220))
    farben = {"gridslot": (255, 255, 255), "pitbox": (255, 160, 40), "pitgarage": (80, 140, 255),
              "f1garage": (40, 220, 255)}
    for kurz, eintraege in plaetze.items():
        for name, m in eintraege.items():
            art, n = spiel_welt._nummer(name)
            x, z = P(m[12], m[14])
            r = 2.5 if mass < 2 else 4
            d.ellipse([x - r, z - r, x + r, z + r], fill=farben.get(art, (255, 255, 0)))
    d.text((10, H + 5), titel, fill=(230, 230, 230), font=schrift)
    d.text((10, H + 22), "Grau: alte Portimão-Linien | Fläche: neues Netz | Cyan: TS-Außengrenzen | Magenta: "
           "KI-Linie, Punkte = verbliebene Weltobjekte | rote Kreise: Bremslinien", fill=(200, 200, 200), font=schrift)
    d.text((10, H + 39), "Weiß: Startplätze | Orange: Boxen | Blau: PitGarage | Hellblau: F1Garage | Gelb: "
           "Safety-Car | Rahmen: AO-Terrain ±1024 m", fill=(200, 200, 200), font=schrift)
    im.save(pfad_png)
    return pfad_png


# =============================================================================== Ereignisse / Abbruch (AP24)
class BauFehler(Exception):
    """Fachlicher Abbruch des Baus (Eingabe, Schutz, Prüfregel); CLI: Exitcode 1 bzw. 2."""

    def __init__(self, text, code=1):
        super().__init__(text)
        self.code = code


class BauAbbruch(Exception):
    """Abbruch auf Wunsch (Signal oder Abbruchdatei); Exitcode 3."""


#: Phasen des Baus mit Anteil am Gesamtfortschritt (gemessen am Bau `--profil empfohlen`, 16.10.2026: 149 s;
#: Fortschritt = Anteil beim Phasenbeginn, keine Zeitprognose).
PHASEN = (("vorbereiten", "Input and options", 0.005), ("pruefen_vorab", "Check without build", 0.005),
          ("generator", "Generator", 0.04), ("traeger", "Unpacking and reading the carrier", 0.03),
          ("geometrie", "Geometry and driving physics", 0.08), ("minimap", "Minimap", 0.04),
          ("umgebung", "Environment (trees, objects)", 0.01), ("welt", "Cleaning up the world", 0.01),
          ("logik", "Track logic", 0.01), ("pruefen_logik", "Checking the logic", 0.005), ("licht", "Lighting", 0.005),
          ("erp_schreiben", "Writing the ERP", 0.04), ("pvs", "PVS", 0.25), ("pruefen_bau", ("Re-reading and checking the "
                                                                                           "ERP"), 0.06),
          ("draufsichten", "Top views", 0.01), ("paket", "Writing the package", 0.36),
          ("paket_pruefen", "Deep package check", 0.015), ("swofty", "Swofty addon", 0.005),
          ("abschluss", "Finishing", 0.02))
_PHASE_START = {}
_s = 0.0
for _n, _t, _w in PHASEN:
    _PHASE_START[_n] = _s
    _s += _w
del _s, _n, _t, _w


class Ereignisse:
    """Eine JSON-Zeile je Ereignis (Schema ``ego-bau-ereignis/1``, docs/editor_schnittstelle.md)."""

    def __init__(self, strom):
        self.strom = strom
        self.nr = 0
        self.t0 = time.time()
        self.phase = None

    def senden(self, typ, **felder):
        self.nr += 1
        e = {"schema": "ego-bau-ereignis/1", "nr": self.nr, "typ": typ,
             "zeit": datetime.datetime.now().isoformat(timespec="seconds"), "t_s": round(time.time() - self.t0, 2)}
        e.update(felder)
        self.strom.write(json.dumps(e, ensure_ascii=False, default=_json) + "\n")
        self.strom.flush()


EREIGNISSE = None
ABBRUCH = {"datei": None, "signal": False}


def _abbruch_pruefen():
    if ABBRUCH["signal"] or (ABBRUCH["datei"] and os.path.exists(ABBRUCH["datei"])):
        raise BauAbbruch("Abbruch angefordert (%s)" % ("Signal" if ABBRUCH["signal"] else ABBRUCH["datei"]))


def _phase(name):
    """Sicherer Phasenwechsel: Abbruch prüfen, Ereignis senden."""
    _abbruch_pruefen()
    if EREIGNISSE is not None:
        EREIGNISSE.phase = name
        titel = next(t for n, t, _ in PHASEN if n == name)
        EREIGNISSE.senden("phase", phase=name, titel=titel, fortschritt=round(_PHASE_START[name], 3))


def _meldungen_senden(bericht_regeln, stufe, alle, gesehen):
    neu = []
    for m in bericht_regeln["meldungen"]:
        k = (m["id"], m["text"])
        if k in gesehen:
            continue
        gesehen.add(k)
        neu.append(m)
        alle.append(m)
        if EREIGNISSE is not None:
            EREIGNISSE.senden("meldung", meldung=m)
    z = pruefregeln.zusammenfassen(neu)
    if EREIGNISSE is not None:
        EREIGNISSE.senden("pruefung", stufe=stufe, zusammenfassung=z)
    _log("Prüfregeln %s: %d Fehler, %d Warnungen, %d Hinweise" % (stufe, z["fehler"], z["warnung"], z["hinweis"]))
    return z


def _streng_pruefen(args, z, stufe):
    if args.pruefregeln == "streng" and not z["ok"]:
        raise BauFehler("Prüfregeln (%s): %d Fehler – Bau gestoppt (--pruefregeln melden zum Fortsetzen)"
                        % (stufe, z["fehler"]), code=2)


def _paketpruefung(ziel):
    """`paket_tool.py pruefen --tief` als Kindprozess; Abbruch beendet ihn."""
    p = subprocess.Popen([sys.executable, os.path.join(HIER, "paket_tool.py"), "pruefen", "--tief", ziel],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))   # Ausgabe läuft über die Pipe
    while True:
        try:
            out, _ = p.communicate(timeout=1.0)
            return p.returncode, out
        except subprocess.TimeoutExpired:
            try:
                _abbruch_pruefen()
            except BauAbbruch:
                p.kill()
                p.communicate()
                raise


# =============================================================================== Bauen
def cmd_bauen(args):
    t0 = time.time()
    _phase("vorbereiten")
    aus = args.ausgabe or AUSGABE
    paket_ordner = args.paket_ordner or PAKETE
    for p in (aus, paket_ordner, args.generator_ausgabe, args.bericht_md, args.traeger_cache):
        _pfad_pruefen(p)
    os.makedirs(aus, exist_ok=True)
    if args.nur_original_pvs and args.nur_hauptvariante:
        raise BauFehler("--nur-original-pvs und --nur-hauptvariante schließen sich aus")
    if args.streckencode != streckencode.STANDARD_ALT:  # AP16: Code erst beim Verpacken umbenennen
        ok, hinweis = streckencode.code_bewerten(args.streckencode, args.ziel)
        _log("Streckencode %s: %s" % (args.streckencode, hinweis))
        if not ok:
            raise BauFehler("unzulässiger Streckencode %s" % args.streckencode)
        if args.ohne_pakete:
            _log("Hinweis: --streckencode wirkt nur beim Verpacken (--ohne-pakete gesetzt)")
    if args.versatz is not None:
        platzierung.setzen(args.versatz)
    _log("Platzierung: Versatz %s" % (platzierung.VERSATZ,))
    paketnamen = [] if args.ohne_pakete else (
        ([] if args.nur_original_pvs else [args.name]) + ([] if args.nur_hauptvariante else [args.name + "_original_pvs"]))
    if args.atomar:
        vorhanden = [n for n in paketnamen if os.path.exists(os.path.join(paket_ordner, n + ".zip"))]
        if vorhanden and not args.ueberschreiben:
            raise BauFehler("Paket existiert bereits: %s (anderen --name/--paket-ordner wählen oder --ueberschreiben)"
                            % ", ".join(os.path.join(paket_ordner, n + ".zip") for n in vorhanden))
    bericht = {"zeitpunkt": datetime.datetime.now().isoformat(timespec="seconds"), "name": args.name,
               "warnungen": [], "schritte": {}, "versatz": list(platzierung.VERSATZ),
               "definition": _relativ(args.definition), "ausgabe": _relativ(aus),
               "profil": args.profil, "eingabe": getattr(args, "eingabe_info", None)}
    regel_meldungen, gesehen, geprueft = [], set(), set()
    args.weltkarten = bool(args.weltkarten) and not args.ohne_geometrie
    if args.weltkarten:
        args.welt_uv = "echt"
    optionen = dict(streckencode=args.streckencode, pvs_variante=args.pvs_variante, ziel=args.ziel,
                    nur_original_pvs=args.nur_original_pvs, ohne_geometrie=args.ohne_geometrie,
                    ohne_minimap=args.ohne_minimap)
    _phase("pruefen_vorab")
    if args.pruefregeln != "aus":
        b = pruefregeln.vorab_pruefen(args.definition, versatz=platzierung.VERSATZ, bauoptionen=optionen)
        geprueft |= set(b["geprueft"])
        _streng_pruefen(args, _meldungen_senden(b, "eingabe", regel_meldungen, gesehen), "eingabe")
    _phase("generator")
    npz, logik_pfad = generator(args)
    if args.pruefregeln != "aus":
        b = pruefregeln.vorab_pruefen(args.definition, versatz=platzierung.VERSATZ, generator=args.generator_ausgabe,
                                      **({"umgebung": args.umgebung} if args.umgebung else {}))
        geprueft |= set(b["geprueft"])
        _streng_pruefen(args, _meldungen_senden(b, "generator", regel_meldungen, gesehen), "generator")
    with open(logik_pfad, encoding="utf-8") as f:
        logik = json.load(f)
    netz = _netz_platziert(npz)
    _phase("traeger")
    traeger, manifest, images_roh, minimap_roh = traeger_entpacken(args)
    bericht["traeger"] = {"pfad": _relativ(traeger), "groesse": os.path.getsize(traeger)}

    a = erp.open_erp(traeger)
    if not args.ohne_geometrie:           # Träger gleich prüfen, nicht erst nach Minuten (Mod-Version 1.0.2 passt nicht)
        mod_geo, _fehler = modul_laden("strecke.spiel_geometrie")
        if mod_geo is not None and hasattr(mod_geo, "traeger_pruefen"):
            try:
                mod_geo.traeger_pruefen(a, manifest)
            except mod_geo.GeometrieFehler:
                nah = sorted({r.identifier for r in a.find_all("terrain") if r.identifier.startswith("eaid://environment_package/tracks/")})
                _log("Träger: Mod-Version %s, Code %s, %d Ressourcen; Terrain-Kennungen: %s"
                     % (manifest.get("version", "?"), manifest.get("track_code", "?"), len(a), ", ".join(nah[:6]) or "keine"))
                raise
    _log("Träger erfassen …")
    zustand = spiel_welt.TraegerZustand.erfassen(a)
    zustand.raster = args.raster
    trn_hash = next(r.hash for r in a if r.resource_type == "TRN~")
    bericht["traeger"].update(ressourcen=len(a), layer=len(zustand.layer_uids), welten=len(zustand.welten),
                              evgt_verweise=len(zustand.evgt_uids),
                              lichtkarte=len(zustand.lichtkarte.eintraege) if zustand.lichtkarte else 0)

    # --- Geometrie (AP11a)
    _phase("geometrie")
    geo = None
    if args.ohne_geometrie:
        bericht["warnungen"].append("Geometrie ausdrücklich abgeschaltet (--ohne-geometrie)")
    else:
        mod, fehler = modul_laden("strecke.spiel_geometrie")
        if mod is None or not hasattr(mod, "geometrie_einsetzen"):
            text = ("WARNUNG: %s – Paket wird OHNE neue Geometrie gebaut (Portimão-Fahrbahn/TRN~ bleiben). "
                    "Nach Fertigstellung von AP11a erneut ausführen." % (fehler or "geometrie_einsetzen fehlt"))
            _log(text)
            bericht["warnungen"].append(text)
        else:
            _log("Geometrie einsetzen (AP11a) …")
            huegel = list(getattr(args, "gelaende_huegel", None) or [])
            if args.umgebung:       # 29.10.2026: Hügel/Senken aus umgebung.json (gelaende.huegel)
                from strecke import umgebung as umg
                try:
                    huegel += umg.huegel_welt(umg.laden(args.umgebung), platzierung.VERSATZ)
                except umg.UmgebungFehler as e:
                    raise BauFehler("Umgebung: %s" % e)
            geo = mod.geometrie_einsetzen(a, npz, mauer_physik=args.mauer_physik, trn_flags=args.trn_flags,
                                          hoehe_versatz=args.geometrie_dy, physik_umfang=args.physik_umfang,
                                          physik_kante_max=args.physik_kante_max,
                                          grundflaeche=getattr(args, "grundflaeche", "raster"),
                                          huegel=huegel or None, welt_uv=getattr(args, "welt_uv", "konstant"))
            bericht["schritte"]["geometrie"] = _kurz(geo)
    # --- Minimap (AP11c)
    _phase("minimap")
    minimap_json = None
    if not args.ohne_minimap:
        mod, fehler = modul_laden("strecke.spiel_minimap")
        if mod is None or not hasattr(mod, "minimap_einsetzen"):
            text = "Hinweis: %s – Minimap des Trägers bleibt" % (fehler or "minimap_einsetzen fehlt")
            _log(text)
            bericht["warnungen"].append(text)
        else:
            _log("Minimap einsetzen (AP11c) …")
            erg = mod.minimap_einsetzen(a, logik_pfad, ausgabe=os.path.join(aus, "minimap"))
            minimap_json = erg["minimap_json"]
            bericht["schritte"]["minimap"] = {"bilder": erg["bericht"].get("anzahl_bilder"),
                                              "ecken": minimap_json["textures"]["corners"]}
    # --- Umgebung (AP-U1): Klone der Träger-Instanzen vor dem Aufräumen setzen (das Aufräumen entfernt nur Originale)
    _phase("umgebung")
    welt_regeln = None
    umg_daten, umg_bericht, umg_katalog = None, None, None
    if args.umgebung:
        from strecke import umgebung as umg
        try:
            umg_daten = umg.laden(args.umgebung)
            try:
                umg_katalog = umg.katalog(protokoll=_log)
            except umg.UmgebungFehler as e:   # nur nötig, wenn Modelle kopiert werden müssen
                _log("Hinweis: Katalog nicht verfügbar (%s)" % e)
            _log("Umgebung: %d Objekte aus %s …" % (len(umg_daten["objekte"]), args.umgebung))
            umg_bericht = umg.einsetzen(a, umg_daten, code=zustand.code, versatz=platzierung.VERSATZ,
                                        katalog_daten=umg_katalog, protokoll=_log)
            berge = umg.berge_einsetzen(a, umg_daten, versatz=platzierung.VERSATZ, protokoll=_log)
        except umg.UmgebungFehler as e:
            raise BauFehler("Umgebung: %s" % e)
        if umg_daten["bodenbewuchs"]:
            welt_regeln = dict(spiel_welt.LAYER_REGELN)
            welt_regeln["groundcover"] = (spiel_welt.BEHALTEN, "Bodenbewuchs des Trägers (umgebung.json: bodenbewuchs)")
        b = {k: v for k, v in umg_bericht.items() if k not in ("mipmaps", "kategorien_je_id")}
        b.update(datei=args.umgebung, mipmaps=len(umg_bericht["mipmaps"]), berge=berge,
                 huegel=len((umg_daten.get("gelaende") or {}).get("huegel", [])))
        bericht["schritte"]["umgebung"] = b
        _log("Umgebung: %d gesetzt (%s), %d Ressourcen und %d Mip-Dateien (%.1f MB) kopiert, %d übersprungen" % (
            b["gesetzt"], ", ".join("%s %d" % kv for kv in b["je_layer"].items()), b.get("ressourcen_neu_summe", 0),
            b["mipmaps"], b["mipmaps_mb"], len(b["uebersprungen"])))
    # --- Welt
    _phase("welt")
    if logik.get("bremsmarker"):
        # 25.10.2026: Entfernungstafeln vor starken Bremspunkten – vor dem Aufräumen (Vorlagen werden dort entfernt)
        _log("Entfernungstafeln setzen …")
        bericht["schritte"]["bremsmarker"] = spiel_welt.bremsmarker_setzen(a, zustand, logik["bremsmarker"])
    garagen = spiel_welt.garagen_modell(logik)
    if garagen == "traeger":
        # 29.10.2026: Portimãos Garagenmodule bleiben und werden nach der Platzberechnung an die neuen Boxen versetzt
        welt_regeln = dict(welt_regeln or spiel_welt.LAYER_REGELN)
        for kurz in spiel_welt.GARAGEN_LAYER:
            welt_regeln[kurz] = (spiel_welt.ANPASSEN, "Garagenmodule des Trägers, an die neuen Boxen versetzt")
    _log("Welt aufräumen …")
    welt = spiel_welt.welt_aufraeumen(a, zustand, welt_regeln)
    for e in welt:      # Ebenen, die die Regeltabelle nicht kennt (neue Mod-Version): bleiben, aber nicht unbemerkt
        if e["grund"] == spiel_welt.UNBEKANNTER_LAYER and e["vorher"] > 0:
            text = "Ebene %s (%d Instanzen) hat keine Regel und bleibt unverändert" % (e["layer"], e["vorher"])
            _log("WARNUNG: " + text)
            bericht["warnungen"].append(text)
    bericht["schritte"]["welt"] = welt
    physik = spiel_welt.verbliebene_physik(a)
    bericht["schritte"]["physik_verblieben"] = physik
    # --- Logik
    _phase("logik")
    _log("Streckenlogik …")
    lg, plaetze, protokoll = spiel_welt.logik_vorbereiten(logik, zustand, args.logik_dy, args.plaetze_dy,
                                                          boxen_korrektur_an=args.boxen_korrektur)
    _phase("pruefen_logik")
    if args.pruefregeln != "aus":
        orig = zustand.schreiber.original
        b = pruefregeln.logik_pruefen(lg, vorlage_zeitfahr_m=pruefregeln.zeitfahr_vorlage(orig),
                                      bremslinien_ids={int(l["id"]) for l in orig.linien["brake_lines"]})
        if physik:
            b["meldungen"].append(pruefregeln.meldung(
                "PHYSIK_VERBLIEBEN", "%d Träger-Instanzen mit Kollision verblieben" % len(physik), ist=len(physik), soll=0))
        geprueft |= set(b["geprueft"]) | {"PHYSIK_VERBLIEBEN"}
        _streng_pruefen(args, _meldungen_senden(b, "logik", regel_meldungen, gesehen), "logik")
    geschrieben = spiel_welt.logik_schreiben(a, zustand, lg)
    n_platz = spiel_welt.platz_layer_setzen(a, zustand, plaetze)
    if garagen == "traeger":
        g = spiel_welt.garagen_versetzen(a, zustand, lg, protokoll)
        _log("Garagen des Trägers versetzt: %s (gespiegelt: %s)" % (
            ", ".join("%s %d" % (k, v.get("versetzt", 0)) for k, v in g["layer"].items()), g["spiegeln"]))
    protokoll["schreiber"] = {k: v for k, v in zustand.schreiber.protokoll.items() if k != "gate_abbildung"}
    protokoll["ressourcen"] = geschrieben
    protokoll["weitere_plaetze_gesetzt"] = n_platz
    bericht["schritte"]["logik"] = protokoll
    with open(os.path.join(aus, "logik_platziert.json"), "w", encoding="utf-8") as f:
        json.dump(lg, f, ensure_ascii=False)
    if getattr(args, "ki_daten", "vorlage") != "vorlage":
        # Experimentell (AP12): Portimãos KI-Daten (Zeiten/Geschwindigkeiten über die Strecke) an die neue Länge anpassen
        _log("KI-Daten anpassen (%s) …" % args.ki_daten)
        from strecke import spiel_ki
        # profil: VPTimes aus dem eigenen Geschwindigkeitsprofil, gerechnet mit dem an der Spiel-KI kalibrierten
        # Fahrzeug (25.10.2026; vorher Portimãos Zeitkurve über dem Rundenanteil → Bremszonen ohne Bezug zu den Kurven)
        extra = {"profil_fahrzeug": "f1_25_ki_portimao"} if args.ki_daten == "profil" else {}
        if args.ki_vp_abtastung:
            extra["vp_abtastung"] = args.ki_vp_abtastung
        if args.ki_nur_typen:
            extra["nur_typen"] = tuple(p.strip() for p in args.ki_nur_typen.split(",") if p.strip())
        if args.ki_ohne_pakete:
            extra["ohne_pakete"] = tuple(p.strip() for p in args.ki_ohne_pakete.split(",") if p.strip())
        bericht["schritte"]["ki_daten"] = spiel_ki.ki_daten_anpassen(
            a, lg, vorlage=traeger, modus=args.ki_daten, **extra)
    # --- Licht
    _phase("licht")
    _log("Licht-Platzhalter …")
    welt_pos = spiel_welt.welt_positionen(a)
    behalten = {uid for (_k, _n, _t, _p, _m, uid) in welt_pos}
    neu_modelle = [uid for (k, _n, _t, _p, m, uid) in welt_pos if m and uid not in zustand.layer_uids.get(k, set())]
    bericht["schritte"]["licht"] = spiel_welt.licht_platzhalter(a, zustand, behalten, neu_modelle)
    # --- Weltkarten: Boden, Ideallinie und Außenterrain der neuen Strecke (docs/welt_texturen.md)
    welt_mips = []
    if args.weltkarten:
        from strecke import spiel_weltkarten
        _log("Weltkarten (Biom %s, Farbton %s) …" % (args.biom, args.farbton))
        ideal = dict(lg["ideallinie"], position=platzierung.punkte(np.asarray(lg["ideallinie"]["position"], float)))
        bericht["schritte"]["weltkarten"], welt_mips = spiel_weltkarten.weltkarten_einsetzen(
            a, zustand.code, npz, ideal, biom=args.biom, farbton=args.farbton)
    # --- ERP schreiben: zuerst Original-PVS-Variante, dann PVS „alles sichtbar“
    _phase("erp_schreiben")
    erp_b = os.path.join(aus, args.name + "_original_pvs.erp")
    erp_a = os.path.join(aus, args.name + ".erp")
    if not args.nur_hauptvariante:
        _log("ERP schreiben (Original-PVS) …")
        a.write(erp_b, overwrite=True)
    if args.nur_original_pvs:  # AP15: Speicherplatz sparen, nur die getestete Variante bauen
        erp_a = erp_b
        bericht["schritte"]["pvs"] = {"zellen": 0, "vorher_byte": 0, "nachher_byte": 0, "uebersprungen": True}
    else:
        _phase("pvs")
        if not a.find_all(r"\.volumedata\.pvsbin$"):
            # Portimão-Mod 1.0.2 liefert keine PVS-Daten (ohne PVSObjIDRes/PVSVisRes gibt es nichts zu ersetzen)
            _log("Träger ohne PVS (Portimão-Mod 1.0.2): PVS-Schritt entfällt")
            bericht["schritte"]["pvs"] = {"zellen": 0, "vorher_byte": 0, "nachher_byte": 0, "uebersprungen": True,
                                          "grund": "Träger ohne PVS"}
        elif args.pvs_variante == "strecke":
            # 16.10.2026: Original-PVS lässt die neue Fahrbahn neben der Box (alter Portimão-Bereich) unsichtbar
            _log("PVS-Zellen entlang der neuen Strecke …")
            from strecke import spiel_pvs
            erg = spiel_pvs.pvs_fuer_strecke(a, logik=lg, korridor_m=args.pvs_korridor)
            erg.pop("linien", None)
            erg.update(zellen=erg["zellen_a"], vorher_byte=erg["vis_bytes_alt"], nachher_byte=erg["vis_bytes_neu"])
            bericht["schritte"]["pvs"] = erg
        else:
            _log("PVS „alles sichtbar“ …")
            bericht["schritte"]["pvs"] = spiel_welt.pvs_alles_sichtbar(a)
        _log("ERP schreiben (Hauptvariante) …")
        a.write(erp_a, overwrite=True)
    a.close()

    # --- Prüfungen
    _phase("pruefen_bau")
    _log("Prüfungen …")
    pr = {}
    varianten = (("haupt", erp_a),) + ((("original_pvs", erp_b),) if not args.nur_hauptvariante else ())
    for name, pfad in varianten:
        with erp.open_erp(pfad) as b:
            falsch = erp.md5_falsch(b)
            pr[name] = {"ressourcen": len(b), "md5_falsch": falsch, "lueckenlos": b.layout_contiguous,
                        "groesse": os.path.getsize(pfad)}
        diff = paket.inhalte_vergleichen(traeger, pfad)
        pr[name]["vergleich"] = {k: len(v) for k, v in diff.items()}
        pr[name]["geaendert_typen"] = dict(Counter(_typ(pfad, diff["daten"])).most_common())
        pr[name]["neu_geparst"], pr[name]["parse_fehler"] = ressourcen_neu_parsen(pfad, diff["daten"] + diff["nur_b"])
        _abbruch_pruefen()
    pr["logik"] = logik_pruefen(erp_a, lg, plaetze, netz)
    _log("TRN-Prüfung …")
    hoehe, art = raster_netz(*netz)
    with erp.open_erp(erp_a) as b:
        pr["trn"] = trn_pruefen(b, trn_hash, hoehe, art, netz[3])
    bericht["pruefungen"] = pr
    if args.pruefregeln != "aus":
        m = []
        geprueft |= pruefregeln.bau_pruefungen_bewerten(pr, m)
        if minimap_json is not None:
            geprueft |= pruefregeln.minimap_pruefen(minimap_json, m)
        b = pruefregeln.bericht(m, geprueft, ["bau"])
        _streng_pruefen(args, _meldungen_senden(b, "bau", regel_meldungen, gesehen), "bau")
    # --- Draufsichten
    _phase("draufsichten")
    _log("Draufsichten …")
    welt_pos_end = []
    with erp.open_erp(erp_a) as b:
        welt_pos_end = spiel_welt.welt_positionen(b)
    bericht["verbliebene_instanzen"] = dict(Counter(k for (k, *_r) in welt_pos_end).most_common())
    titel = "%s – Gesamtergebnis (Spielkoordinaten, x rechts, z unten)" % args.name
    bilder = [draufsicht(os.path.join(aus, "draufsicht_gesamt.png"), netz, lg, zustand, welt_pos_end, plaetze,
                         titel=titel)]
    pit = next(s for s in lg["spielformate"]["TrackSpace"]["splines"] if not s["closed"])
    px = [p[0] for p in pit["position"]]
    pz = [p[2] for p in pit["position"]]
    mx, mz = (min(px) + max(px)) / 2, (min(pz) + max(pz)) / 2
    breite = max(px) - min(px) + 120
    bilder.append(draufsicht(os.path.join(aus, "draufsicht_start_boxen.png"), netz, lg, zustand, welt_pos_end,
                             plaetze, ausschnitt=(mx - breite / 2, mx + breite / 2, mz - 90, mz + 90), mass=1600 / breite,
                             titel="%s – Start/Boxengasse" % args.name))
    if umg_daten is not None:
        from strecke import umgebung as umg
        bilder.append(umg.karte(os.path.join(aus, "umgebung_karte.png"), umg_daten, netz=npz, versatz=platzierung.VERSATZ,
                                katalog_daten=umg_katalog, logik=lg,
                                titel="%s – Umgebung: %d Objekte (Welt, x rechts, z unten)" % (
                                    args.name, len(umg_daten["objekte"]))))
    bericht["bilder"] = [_relativ(b) for b in bilder]

    # --- Pakete (mit --atomar zuerst in <ausgabe>/_paket_tmp, erst nach der Prüfung in den Paketordner)
    fertige = []
    if not args.ohne_pakete:
        _phase("paket")
        neu_manifest = json.loads(json.dumps(manifest))
        tm = {z["Type"]: z for z in lg["spielformate"]["TM"]["markup"]}
        ers = neu_manifest.get("ers", {})
        if "OverrideDetectionLapProgress" in ers:
            ers["OverrideDetectionLapProgress"] = round(float(tm["ers_over_det"]["Start"]), 4)
            ers["OverrideActivationLapProgress"] = round(float(tm["ers_over_act"]["Start"]), 4)
        bericht["manifest_ers"] = ers
        # Streckenmetadaten (Länge, Kurven, Nachtstart) für die Datenbank-Aktivierung (docs/streckenname.md)
        kb = (bericht["schritte"].get("logik") or {}).get("kurven_begrenzt") or {}
        meta = {} if args.ohne_metadaten else streckencode.metadaten_aus_logik(lg, kb.get("nachher"))
        meta.update(args.metadaten or {})
        if meta:
            streckencode.metadaten_setzen(neu_manifest, meta)
        bericht["manifest_metadaten"] = meta
        zusatz = None
        if args.renndaten:   # Runden, Kraftstoff, Reifen, KI-Zeiten (strecke/renndaten.py)
            zusatz = renndaten.aus_logik(lg, lg["laenge_m"])
            bericht["renndaten"] = zusatz["bericht"]
            _log("Renndaten: %s" % (zusatz["bericht"],))
            if args.streckencode == streckencode.STANDARD_ALT:
                streckencode.zusatz_anhaengen(neu_manifest, zusatz)
        mm = minimap_json if minimap_json is not None else minimap_roh
        if args.pruefregeln != "aus":
            m = []
            geprueft |= pruefregeln.manifest_ers_pruefen(lg["spielformate"]["TM"]["markup"], neu_manifest, m)
            if minimap_json is None and minimap_roh is not None:
                roh = minimap_roh if isinstance(minimap_roh, dict) else json.loads(bytes(minimap_roh).decode("utf-8"))
                geprueft |= pruefregeln.minimap_pruefen(roh, m)
            _streng_pruefen(args, _meldungen_senden(pruefregeln.bericht(m, geprueft, ["paket"]), "paket",
                                                    regel_meldungen, gesehen), "paket")
        mip = []
        quelle = paket.ModPaket(args.traeger)
        mip = [(i.filename, (quelle.zip, i)) for i in quelle.mipmap_eintraege()]
        if isinstance(geo, dict) and geo.get("mipmaps"):
            mip += [(n, q) for n, q in geo["mipmaps"]]
        if umg_bericht and umg_bericht["mipmaps"]:   # AP-U1: Mip-Stufen kopierter Modelle (mipmaps/umg_<Datei>)
            vorhanden = {n for n, _ in mip}
            mip += [(n, q) for n, q in umg_bericht["mipmaps"] if n not in vorhanden]
        if welt_mips:                                # Biom: Mip-Stufen kopierter Texturen (mipmaps/wt_<Datei>)
            vorhanden = {n for n, _ in mip}
            mip += [(n, q) for n, q in welt_mips if n not in vorhanden]
        bericht["pakete"] = {}
        tmp = os.path.join(aus, "_paket_tmp")
        if args.atomar:
            os.makedirs(tmp, exist_ok=True)
        try:
            for k, (name, pfad) in enumerate((((args.name, erp_a),) if not args.nur_original_pvs else ()) + (
                    (((args.name + "_original_pvs", erp_b),) if not args.nur_hauptvariante else ()))):
                if k:
                    _phase("paket")
                endziel = os.path.join(paket_ordner, name + ".zip")
                ziel = os.path.join(tmp, name + ".zip") if args.atomar else endziel
                _log("Paket %s …" % ziel)
                if args.streckencode == streckencode.STANDARD_ALT:
                    groesse = paket.paket_bauen(ziel, neu_manifest, images_roh, mm, pfad, mip, ueberschreiben=True)
                else:  # AP16: ERP, Manifest und UI-JSONs auf den neuen Code umbenennen (docs/streckencode.md)
                    erg = streckencode.paket_bauen_umbenannt(
                        ziel, neu_manifest, images_roh, mm, pfad, mip,
                        streckencode.Umbenennung(streckencode.STANDARD_ALT, args.streckencode),
                        os.path.join(aus, "%s_%s" % (name, args.streckencode)), name=args.streckenname,
                        aktivierung_vorlage=args.aktivierung_vorlage, ueberschreiben=True, protokoll=_log, zusatz=zusatz)
                    groesse = erg["groesse"]
                    os.remove(erg["neu_erp"])
                    bericht.setdefault("streckencode", {})[name] = {
                        k: v for k, v in erg["erp"].items() if k not in ("rest_kennungen",)}
                    bericht["streckencode"][name]["manifest"] = erg["manifest"]
                _phase("paket_pruefen")
                _log("paket_tool.py pruefen --tief %s …" % os.path.basename(ziel))
                rueckgabe, ausgabe_text = _paketpruefung(ziel)
                print(ausgabe_text, end="", file=sys.stderr if EREIGNISSE is not None else sys.stdout)
                pruefung = []
                for zeile in ausgabe_text.splitlines():
                    teile = zeile.strip().split(None, 1)
                    if teile and teile[0] in ("OK", "FEHLER") and len(teile) == 2:
                        name_text = teile[1]
                        pruefung.append((name_text[:40].strip(), teile[0] == "OK", name_text[40:].strip()))
                bericht["pakete"][name] = {"pfad": _relativ(endziel), "groesse": groesse,
                                           "pruefung": pruefung, "rueckgabe": rueckgabe,
                                           "fehler": sum(1 for _, ok, _ in pruefung if not ok)}
                z = {"ok": True}
                if args.pruefregeln != "aus":
                    m = []
                    geprueft |= pruefregeln.paket_pruefung_bewerten(pruefung, m)
                    z = _meldungen_senden(pruefregeln.bericht(m, geprueft, ["paket"]), "paket", regel_meldungen, gesehen)
                if args.ziel == streckencode.ZIEL_SWOFTY and (z["ok"] or args.fehlerhafte_behalten):
                    _phase("swofty")
                    traeger_zip = None
                    if args.traeger_teilen:   # Texturen einmal im Träger-Addon neben den Paketen (docs/boxen_links_und_traeger.md)
                        with zipfile.ZipFile(ziel) as zz:
                            kennung = streckencode.traeger_kennung(zz)
                        traeger_zip = os.path.join(paket_ordner, kennung + ".zip")
                        if not os.path.exists(traeger_zip):
                            os.makedirs(paket_ordner, exist_ok=True)
                            streckencode.swofty_traeger_addon(ziel, traeger_zip + ".tmp", protokoll=_log)
                            os.replace(traeger_zip + ".tmp", traeger_zip)
                        else:
                            _log("Träger-Addon vorhanden: %s" % traeger_zip)
                    addon = streckencode.swofty_addon_bauen(ziel, ziel + ".addon", args.streckenname, protokoll=_log,
                                                            geteilt=args.traeger_teilen)
                    os.replace(ziel + ".addon", ziel)
                    groesse = addon["groesse"]
                    bericht["pakete"][name].update(groesse=groesse, swofty=addon, traeger=traeger_zip)
                if args.atomar:
                    if z["ok"] or args.fehlerhafte_behalten:
                        _abbruch_pruefen()
                        os.makedirs(paket_ordner, exist_ok=True)
                        if os.path.exists(endziel):
                            os.remove(endziel)  # nur mit --ueberschreiben erreichbar (oben geprüft)
                        shutil.move(ziel, endziel)
                    else:
                        os.remove(ziel)
                        bericht["pakete"][name]["verworfen"] = True
                        _log("Paket %s verworfen (Prüffehler; --fehlerhafte-behalten zum Behalten)" % name)
                        continue
                fertige.append({"name": name, "pfad": os.path.abspath(endziel), "groesse": groesse,
                                "pruefung_ok": z["ok"], "ziel": args.ziel,
                                "traeger": (bericht["pakete"][name].get("traeger") or None)})
        finally:
            quelle.close()
            if args.atomar and os.path.isdir(tmp):
                shutil.rmtree(tmp, ignore_errors=True)
    _phase("abschluss")
    bericht["sekunden"] = round(time.time() - t0)
    pb = pruefregeln.bericht(regel_meldungen, geprueft, ["eingabe", "generator", "profil", "logik", "bau", "paket"])
    bericht["pruefregeln"] = pb["zusammenfassung"]
    with open(os.path.join(aus, "pruefbericht.json"), "w", encoding="utf-8") as f:
        json.dump(pb, f, ensure_ascii=False, indent=1, default=_json)
    with open(os.path.join(aus, "bericht.json"), "w", encoding="utf-8") as f:
        json.dump(bericht, f, ensure_ascii=False, indent=1, default=_json)
    bericht_md(bericht, zustand, args.bericht_md)
    if args.arbeit_aufraeumen:
        for pfad in {erp_a, erp_b}:
            if os.path.exists(pfad):
                os.remove(pfad)
    if args.pruefsummen:
        for p in fertige:
            _log("SHA-256 %s …" % os.path.basename(p["pfad"]))
            p["sha256"] = _sha256(p["pfad"])
    _log("Fertig in %d s" % bericht["sekunden"])
    z = pb["zusammenfassung"]
    # Trägerabweichung zählt für das Paket (beim Umbenennen auf einen eigenen Code werden die MD5 neu berechnet)
    altlast = any(m["id"] == "ERP_MD5_TRAEGER_ALTLAST" and (m["stufe"] == "paket" or args.ohne_pakete)
                  for m in regel_meldungen)
    status = "fehlgeschlagen" if not z["ok"] else ("mit_traegerabweichung" if altlast else "ok")
    if not args.ohne_pakete and not fertige:
        status = "fehlgeschlagen"
    args.ergebnis = {"status": status, "pakete": fertige, "pruefbericht": os.path.abspath(os.path.join(aus, "pruefbericht.json")),
                     "bericht_json": os.path.abspath(os.path.join(aus, "bericht.json")),
                     "bericht_md": os.path.abspath(args.bericht_md), "ausgabe": os.path.abspath(aus),
                     "zusammenfassung": z, "sekunden": bericht["sekunden"]}
    return 0 if status != "fehlgeschlagen" else 2


def _typ(pfad, kennungen):
    with erp.open_erp(pfad) as a:
        t = {r.identifier: r.resource_type for r in a}
    return [t.get(k, "?") for k in kennungen]


def _kurz(obj, tiefe=0):
    if isinstance(obj, dict):
        return {k: _kurz(v, tiefe + 1) for k, v in list(obj.items())[:40]}
    if isinstance(obj, (list, tuple)):
        return [_kurz(v, tiefe + 1) for v in list(obj)[:20]] + (["…%d" % len(obj)] if len(obj) > 20 else [])
    if isinstance(obj, (np.ndarray,)):
        return "array%s" % (obj.shape,)
    if isinstance(obj, (bytes, bytearray)):
        return "%d Byte" % len(obj)
    return obj


def _json(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (bytes, bytearray)):
        return o.hex()
    return str(o)


# =============================================================================== Bericht
def bericht_md(b, zustand, pfad=None):
    pr = b["pruefungen"]
    lg = pr["logik"]
    z = ["# Neue Strecke im Portimão-Träger – Welt, Logik, Zusammenbau (AP11b)", "",
         "Erzeugt von `neue_strecke_tool.py bauen` am %s (%d s). Rohdaten: "
         "`%s/bericht.json` (nicht im Repository). Definition `%s`, Versatz %s." % (
             b["zeitpunkt"], b["sekunden"], b.get("ausgabe", "testausgabe/neue_strecke/zusammenbau").replace("\\", "/"),
             b.get("definition", "?").replace("\\", "/"), tuple(b.get("versatz", ()))),
         ""]
    if b["warnungen"]:
        z += ["## Warnungen", ""] + ["- %s" % w for w in b["warnungen"]] + [""]
    z += ["## Ergebnis", "", "| Prüfung | Ergebnis |", "|---|---|"]
    for name in ("haupt", "original_pvs"):
        if name not in pr:   # AP24: --nur-hauptvariante
            continue
        p = pr[name]
        z.append("| ERP %s: Ressourcen / MD5 falsch / lückenlos | %d / %d (%s) / %s |" % (
            name, p["ressourcen"], len(p["md5_falsch"]), ", ".join("`%s`" % x.rsplit("/", 1)[-1] for x in p["md5_falsch"]),
            "ja" if p["lueckenlos"] else "nein"))
        z.append("| ERP %s: geänderte Ressourcen (Typen) | %d (%s) |" % (
            name, p["vergleich"]["daten"], ", ".join("%s %d" % kv for kv in p["geaendert_typen"].items())))
        z.append("| ERP %s: neu geparst / Fehler | %s / %d |" % (
            name, ", ".join("%s %d" % kv for kv in p["neu_geparst"].items()), len(p["parse_fehler"])))
    z.append("| Brems-IDs = ID-Satz aller %d `.brakelinesettings` (%d Reifengruppen) | %s |" % (
        lg["bremsressourcen"], lg["reifengruppen_geprueft"], ", ".join(map(str, lg["brems_ids"]))))
    z.append("| TrackSpace | %s |" % "; ".join("%s: %d Knoten, %.1f m" % (n, s["knoten"], s["laenge_m"])
                                             for n, s in lg["splines"].items()))
    z.append("| KI-Gates (davon Box) / TS-Volumes / QT02-Punkte | %d (%d) / %s / %d |" % (
        lg["gates"], lg["gates_box"], ", ".join("%s %d" % tuple(t) for t in lg["ts_listen"]), lg["quadtree_punkte"]))
    z.append("| TM-Zonen / außerhalb 0…1 | %d / %d |" % (lg["zonen"], len(lg["zonen_ausserhalb_0_1"])))
    z.append("| Fahrzeugplätze auf passender Fläche des neuen Netzes | %d / %d |" % (lg["plaetze_ok"], lg["plaetze_gesamt"]))
    phys = b["schritte"]["physik_verblieben"]
    z.append("| Verbliebene Instanzen mit Kollision (PhysicsEntity/StaticBody) | %d |" % len(phys))
    t = pr["trn"]
    z.append("| TRN~ geändert (AP11a) | %s |" % ("ja" if t.get("geaendert") else "**nein – Portimão-Fahrphysik unverändert**"))
    z.append("| TRN~-Dreiecke auf neuer Fahrbahnfläche / davon Höhe ±1 m abweichend | %d / %d |" % (
        t.get("auf_neuer_fahrbahn", 0), t.get("auf_fahrbahn_hoehe_abweichend_gt_1m", 0)))
    li = b["schritte"]["licht"]
    z.append("| Lichtkarte: Einträge vorher → nachher (Terrain Schicht 0, neue Objekte) | %d → %d (%s, %d) |" % (
        li["vorher"], li["eintraege"], li["terrain"], li["neue_objekte"]))
    pv = b["schritte"]["pvs"]
    z.append("| PVS „alles sichtbar“ (Hauptvariante) | %d Zellen, %.1f → %.1f MB |" % (
        pv["zellen"], pv["vorher_byte"] / 1e6, pv["nachher_byte"] / 1e6))
    for name, p in b.get("pakete", {}).items():
        fehlt = "; ".join("%s: %s" % (n, t[:160]) for n, ok, t in p["pruefung"] if not ok)
        z.append("| Paket `%s` (%.2f GB): `paket_tool.py pruefen --tief` | %d Prüfungen, %d Fehler%s |" % (
            p["pfad"], p["groesse"] / 1e9, len(p["pruefung"]), p["fehler"],
            (" (%s – bekannte Fehler der Original-Mod, unverändert übernommen)" % fehlt) if fehlt else ""))
    for name, sc in b.get("streckencode", {}).items():
        fehl = [n for n, ok, _ in sc.get("pruefung", []) if not ok]
        z.append("| Streckencode `%s` → `%s` (`%s`): Kennungen / Daten / Prüfungen | %d / %d / %d, %d Fehler%s |" % (
            sc["alt"], sc["neu"], name, sc["kennungen_summe"], sc["daten_summe"], len(sc.get("pruefung", [])),
            len(fehl), (" (%s)" % ", ".join(fehl)) if fehl else ""))
    if "manifest_ers" in b:
        z.append("| Manifest ERS-Override (Erkennung / Aktivierung) | %s / %s |" % (
            b["manifest_ers"].get("OverrideDetectionLapProgress"), b["manifest_ers"].get("OverrideActivationLapProgress")))
    z += [""]
    for name in ("haupt", "original_pvs"):
        if name in pr and pr[name]["parse_fehler"]:
            z += ["**Parse-Fehler %s:**" % name, ""] + ["- %s" % f for f in pr[name]["parse_fehler"][:20]] + [""]
    # Welt
    welten = zustand.welten_je_layer()
    z += ["## Welt: Entscheidung je Layer", "",
          "69 Layer in 9 Welten (`portimao_common` + 8 Varianten). Entfernt werden nur Instanzen des "
          "Original-Trägers; der EventGraph (275 `worldobjectid`-Verweise) wird vorher gegen die zu "
          "entfernenden Instanzen geprüft (0 Konflikte). Geleerte Layer haben exakt die Form der im "
          "Original vorhandenen leeren Layer (`instances: []`, 100–130 Byte); die Weltlisten bleiben unverändert.", "",
          "| Layer | Welten | Aktion | vorher | entfernt | davon Kollision | Begründung |", "|---|---:|---|---:|---:|---:|---|"]
    for e in b["schritte"]["welt"]:
        z.append("| %s | %d | %s | %d | %d | %d | %s |" % (e["layer"], e["welten"], e["aktion"], e["vorher"],
                                                           e["entfernt"], e["physik_entfernt"], e["grund"]))
    summe = sum(e["entfernt"] for e in b["schritte"]["welt"])
    z += ["", "Summe entfernt: **%d Instanzen**, davon %d mit Kollision. Verbliebene Instanzen je Layer: %s." % (
        summe, sum(e["physik_entfernt"] for e in b["schritte"]["welt"]),
        ", ".join("%s %d" % kv for kv in b["verbliebene_instanzen"].items())), ""]
    # Logik
    lo = b["schritte"]["logik"]
    z += ["## Streckenlogik", "",
          "- Schreiber (AP8) im Bremsmodus `neutral`, TM vollständig vorgegeben (`tm_rest_behalten=False`); "
          "QT02: %s." % lo["schreiber"].get("quadtree_sampling"),
          "- TM: %d Zonen (%d aus dem Generator, Rest Vorlage/ergänzt); entfernt: %s; Boxen-Ankerabbildung "
          "(Vorlage 0,362/0,445/0,7227/0,8614 →) %s." % (lo["tm"]["zonen"], lo["tm"]["aus_generator"],
                                                        lo["tm"]["entfernt"], lo["tm"]["boxenabbildung"]),
          "- Fahrzeugplätze: %s; zusätzlich gesetzt in MyTeam-/Safety-Car-Layern: %d." % (
              lo["plaetze"], lo["weitere_plaetze_gesetzt"]), "",
          "| Brems-ID | Gate | Fortschritt | Route |", "|---:|---:|---:|---|"]
    for l in lo["bremslinien"]:
        z.append("| %d | %d | %.4f | %s |" % (l["id"], l["gate"], l["fortschritt"], "Box" if l["box"] else "Haupt"))
    z += ["", "### Fahrzeugplätze (neu eingelesen)", "",
          "| Layer | Platz | Fläche | Abstand Mittellinie m | dy zum Netz m | ok |", "|---|---|---|---:|---:|---|"]
    for p in lg["plaetze"]:
        z.append("| %s | %s | %s | %.2f | %s | %s |" % (p["layer"], p["name"].split("$")[-1], p["flaeche"],
                                                       p["abstand_mittellinie_m"], p["dy_m"], "ja" if p["ok"] else "**nein**"))
    z += ["", "## TRN~ im neuen Streckenbereich", "", "```", json.dumps(pr["trn"], ensure_ascii=False, indent=1), "```", ""]
    z += ["## Draufsichten", ""] + ["- `%s`" % x for x in b["bilder"]] + [""]
    if b.get("pakete"):
        z += ["## Pakete", ""]
        for name, p in b["pakete"].items():
            z += ["### `%s`" % p["pfad"], "", "| Prüfung | Ergebnis | Details |", "|---|---|---|"]
            z += ["| %s | %s | %s |" % (n, "OK" if ok else "**FEHLER**", t) for n, ok, t in p["pruefung"]]
            z.append("")
    pfad = pfad or os.path.join(HIER, "ergebnisse", "neue_strecke.md")
    with open(pfad, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(z) + "\n")
    _log("Bericht: %s" % pfad)


# =============================================================================== Profile, Projekt (AP24)
#: Benannte Bauprofile: Werte für die Optionen von `bauen`; ausdrücklich angegebene Optionen haben Vorrang.
BAUPROFILE = {
    "empfohlen": {
        "beschreibung": "Empfohlener Bau (16.10.2026, v20 im Spiel fahrbar): PVS entlang der Strecke, "
                        "Boxengassen-Korrektur (AP21), Code sochi, nur Hauptvariante",
        "optionen": {"pvs_variante": "strecke", "boxen_korrektur": True, "streckencode": "sochi",
                     "aktivierung_vorlage": "portimao", "nur_hauptvariante": True, "weltkarten": True, "renndaten": True},
        "streckenname": "aus der Definition (Feld name), sonst Teststrecke",
        "beleg": "CLAUDE.md „Empfohlener Bau“, docs/schritt2_ergebnisse.md (v20_pvs_strecke ✔)",
    },
}


def projekt_laden(ordner):
    """Editor-Projektordner (`projekt.json` → `definition.json`, Mapeditor/src/mapeditor/project.py)."""
    ordner = os.path.abspath(ordner)
    pfad = os.path.join(ordner, "projekt.json") if os.path.isdir(ordner) else ordner
    with open(pfad, encoding="utf-8-sig") as f:
        manifest = json.load(f)
    if manifest.get("schema", 1) != 1:
        raise BauFehler("Projektfassung %r wird nicht unterstützt" % manifest.get("schema"))
    basis = os.path.dirname(pfad)
    rel = manifest.get("definition", "definition.json")
    definition = os.path.abspath(os.path.join(basis, rel))
    if os.path.isabs(rel) or not definition.startswith(basis + os.sep) or os.path.basename(definition) == "projekt.json":
        raise BauFehler("Definitionspfad muss innerhalb des Projektordners liegen: %s" % rel)
    if manifest.get("runde_geschlossen", True) is False:
        raise BauFehler("Runde im Projekt ist noch offen – vor dem Bau schließen")
    return definition, manifest


def schnappschuss(definition, ziel_ordner):
    """Unveränderliche Kopie der Definition (+ relative CSV-Beilage) für diesen Bau; SHA-256."""
    os.makedirs(ziel_ordner, exist_ok=True)
    with open(definition, "rb") as f:
        roh = f.read()
    daten = json.loads(roh.decode("utf-8-sig"))
    ziel = os.path.join(ziel_ordner, "definition.json")
    with open(ziel, "wb") as f:
        f.write(roh)
    beilagen = []
    ml = daten.get("mittellinie", {})
    if ml.get("art") == "csv" and ml.get("datei") and not os.path.isabs(ml["datei"]):
        q = os.path.join(os.path.dirname(os.path.abspath(definition)), ml["datei"])
        z = os.path.join(ziel_ordner, ml["datei"])
        os.makedirs(os.path.dirname(z), exist_ok=True)
        shutil.copyfile(q, z)
        beilagen.append(ml["datei"])
    h = hashlib.sha256(roh)
    for b in beilagen:
        with open(os.path.join(ziel_ordner, b), "rb") as f:
            h.update(f.read())
    return ziel, {"quelle": os.path.abspath(definition), "schnappschuss": os.path.abspath(ziel), "beilagen": beilagen,
                  "sha256": h.hexdigest(), "name": daten.get("name")}


def _vorbereiten(args):
    """Optionen nach dem Parsen auflösen (Projekt, Profil, Standardpfade im JSON-Modus)."""
    if not hasattr(args, "definition"):
        return
    if getattr(args, "projekt", None):
        args.definition, man = projekt_laden(args.projekt)
        if args.versatz is None and man.get("versatz") is not None:
            args.versatz = ",".join(str(float(x)) for x in man["versatz"])
        if getattr(args, "umgebung", None) is None and not getattr(args, "ohne_umgebung", False):
            from strecke import umgebung as umg   # AP-U1: projekt.json „umgebung“ oder umgebung.json
            try:
                args.umgebung = umg.aus_projekt(args.projekt)
            except umg.UmgebungFehler as e:
                raise BauFehler(str(e))
    if getattr(args, "ohne_umgebung", False):
        args.umgebung = None
    if getattr(args, "umgebung", None):
        args.umgebung = os.path.abspath(args.umgebung)
        if not os.path.exists(args.umgebung):
            raise BauFehler("Umgebungsdatei fehlt: %s" % args.umgebung)
    if args.definition is None:
        args.definition = os.path.join(HIER, "strecke", "beispiele", "teststrecke.json")
    if getattr(args, "befehl", None) != "bauen":
        return
    json_modus = args.ereignisse_json
    if args.atomar is None:
        args.atomar = json_modus
    if args.pruefregeln is None:
        args.pruefregeln = "streng" if json_modus else "melden"
    if json_modus and args.ausgabe is None:
        args.ausgabe = os.path.join(HIER, "testausgabe", "neue_strecke", args.name)
    args.ausgabe = args.ausgabe or AUSGABE
    if args.generator_ausgabe is None:
        if json_modus:
            args.generator_ausgabe = os.path.join(args.ausgabe, "generator")
            args.neu_erzeugen = True   # nie stillen Altstand verwenden (UX §10.4)
        else:
            args.generator_ausgabe = os.path.join(HIER, "testausgabe", "strecke")
    if args.bericht_md is None:
        args.bericht_md = (os.path.join(args.ausgabe, "bericht.md") if json_modus
                           else os.path.join(HIER, "ergebnisse", "neue_strecke.md"))
    for k in ("ausgabe", "generator_ausgabe", "bericht_md", "paket_ordner", "traeger_cache", "definition",
              "abbruch_datei", "traeger"):
        if getattr(args, k, None):
            setattr(args, k, os.path.abspath(getattr(args, k)))
    for p in (args.ausgabe, args.generator_ausgabe, args.bericht_md, args.paket_ordner or PAKETE, args.traeger_cache):
        _pfad_pruefen(p)
    args.eingabe_info = None
    if json_modus or args.schnappschuss:
        args.definition, args.eingabe_info = schnappschuss(args.definition, os.path.join(args.ausgabe, "eingabe"))
        args.neu_erzeugen = True
        if args.umgebung:   # AP-U1: Umgebungsdatei ebenfalls einfrieren
            with open(args.umgebung, "rb") as f:
                roh = f.read()
            ziel = os.path.join(args.ausgabe, "eingabe", "umgebung.json")
            with open(ziel, "wb") as f:
                f.write(roh)
            args.eingabe_info["umgebung"] = {"quelle": args.umgebung, "schnappschuss": os.path.abspath(ziel),
                                             "sha256": hashlib.sha256(roh).hexdigest()}
            args.umgebung = os.path.abspath(ziel)
    if args.raster:   # Plätze im Generator: Startaufstellung und Boxen der Eingabekopie (docs/swofty_launcher.md)
        with open(args.definition, encoding="utf-8-sig") as f:
            daten = json.load(f)
        daten.setdefault("startaufstellung", {})["anzahl"] = args.raster
        daten.setdefault("boxengasse", {})["boxen"] = (args.raster + 1) // 2
        args.definition = os.path.join(args.ausgabe, "eingabe", "definition_raster.json")
        os.makedirs(os.path.dirname(args.definition), exist_ok=True)
        with open(args.definition, "w", encoding="utf-8") as f:
            json.dump(daten, f, ensure_ascii=False)
    swofty = args.ziel == streckencode.ZIEL_SWOFTY
    if (swofty or args.profil and args.streckencode != streckencode.STANDARD_ALT) and args.streckenname is None:
        name = None
        try:
            with open(args.definition, encoding="utf-8-sig") as f:
                name = json.load(f).get("name")
        except (OSError, ValueError):
            pass
        args.streckenname = name or "Teststrecke"
    if swofty:  # eigener Code statt eines Altplatzes, Basis aus den Streckenlisten (docs/swofty_launcher.md)
        if args.streckencode == streckencode.STANDARD_ALT or args.streckencode in streckencode.DB_ALTSTRECKEN:
            args.streckencode = streckencode.swofty_code(args.streckenname)
        args.aktivierung_vorlage = streckencode.SWOFTY_VORLAGE


def _huegel_liste(text):
    """``--gelaende-huegel``: Einträge „H“ oder „H@X:Z:R“, getrennt durch Komma oder Semikolon."""
    aus = []
    for teil in text.replace(";", ",").split(","):
        if not teil.strip():
            continue
        if "@" in teil:
            h, ort = teil.split("@", 1)
            x, z, r = (float(v) for v in ort.split(":"))
            aus.append((float(h), x, z, r))
        else:
            aus.append(float(teil))
    return aus


class _SchluesselWert(argparse.Action):
    """``--metadaten a=b`` mehrfach → {a: b}."""

    def __call__(self, parser, namespace, wert, option_string=None):
        if "=" not in wert:
            parser.error("%s erwartet SCHLUESSEL=WERT, nicht %r" % (option_string, wert))
        k, v = wert.split("=", 1)
        d = dict(getattr(namespace, self.dest) or {})
        d[k.strip()] = v.strip()
        setattr(namespace, self.dest, d)


def _profil_vorab(argv):
    """--profil vor dem eigentlichen Parsen lesen (Profilwerte werden Standardwerte)."""
    vor = argparse.ArgumentParser(add_help=False)
    vor.add_argument("--profil", choices=sorted(BAUPROFILE))
    a, _ = vor.parse_known_args(argv)
    return a.profil


# =============================================================================== Befehle pruefen/regeln/profile
def cmd_pruefen(args):
    """Stufe a (ohne Bau) und optional Stufe b (platzierte Logik/ERP) als ein JSON-Prüfbericht auf stdout."""
    if args.versatz is not None:
        platzierung.setzen(args.versatz)
    optionen = None
    if args.streckencode is not None or args.pvs_variante is not None:
        optionen = dict(streckencode=args.streckencode, pvs_variante=args.pvs_variante)
    gen = args.generator_ausgabe if args.generator_ausgabe else None
    b = pruefregeln.vorab_pruefen(args.definition, versatz=platzierung.VERSATZ, generator=gen,
                                  mit_generator=args.mit_generator, bauoptionen=optionen,
                                  **({"umgebung": args.umgebung} if args.umgebung else {}))
    if args.logik or args.erp:
        if args.logik:
            with open(args.logik, encoding="utf-8") as f:
                lg = json.load(f)
        else:
            lg = pruefregeln.logik_aus_erp(args.erp)
        b2 = pruefregeln.logik_pruefen(lg, bremslinien_ids=pruefregeln.PORTIMAO_BREMSLINIEN_IDS)
        b = pruefregeln.bericht(b["meldungen"] + b2["meldungen"], set(b["geprueft"]) | set(b2["geprueft"]),
                                b["stufen"] + b2["stufen"])
    b["definition"] = os.path.abspath(args.definition)
    b["versatz"] = list(platzierung.VERSATZ)
    sys.stdout.write(json.dumps(b, ensure_ascii=False, indent=None if args.kompakt else 1, default=_json) + "\n")
    return 0 if b["zusammenfassung"]["ok"] else 2


def cmd_regeln(args):
    if args.md:
        print(pruefregeln.katalog_markdown())
    else:
        print(json.dumps({"schema": "ego-regelkatalog/1", "regeln": pruefregeln.katalog()}, ensure_ascii=False, indent=1))
    return 0


def cmd_profile(args):
    print(json.dumps({"schema": "ego-bauprofile/1", "profile": BAUPROFILE}, ensure_ascii=False, indent=1))
    return 0


def _bauen_json(args):
    """`bauen --ereignisse-json`: Ereignisse auf stdout, alles andere (Log, Kindausgaben) auf stderr."""
    global EREIGNISSE
    echt = sys.stdout
    EREIGNISSE = Ereignisse(echt)
    code = 1
    try:
        with contextlib.redirect_stdout(sys.stderr):
            EREIGNISSE.senden("start", befehl="bauen", name=args.name, profil=args.profil,
                              eingabe=args.eingabe_info, definition=os.path.abspath(args.definition),
                              optionen={k: v for k, v in vars(args).items() if k not in ("func", "eingabe_info")},
                              phasen=[{"phase": n, "titel": t, "anteil": w} for n, t, w in PHASEN])
            code = cmd_bauen(args)
        EREIGNISSE.senden("ergebnis", fortschritt=1.0, **args.ergebnis)
    except BauAbbruch as e:
        code = 3
        _unvollstaendig(args, "abgebrochen", str(e))
        EREIGNISSE.senden("abbruch", phase=EREIGNISSE.phase, text=str(e))
    except BauFehler as e:
        code = e.code
        _unvollstaendig(args, "fehlgeschlagen", str(e))
        EREIGNISSE.senden("fehler", phase=EREIGNISSE.phase, art="BauFehler", text=str(e))
    except Exception as e:  # noqa: BLE001
        code = 1
        _unvollstaendig(args, "fehlgeschlagen", str(e))
        EREIGNISSE.senden("fehler", phase=EREIGNISSE.phase, art=type(e).__name__, text=str(e),
                          traceback=traceback.format_exc()[-4000:])
    finally:
        EREIGNISSE = None
    return code


def _unvollstaendig(args, status, text):
    """Arbeitsordner als unvollständig markieren; temporäre Pakete entfernen (nie im Paketordner)."""
    try:
        tmp = os.path.join(args.ausgabe, "_paket_tmp")
        if os.path.isdir(tmp):
            shutil.rmtree(tmp, ignore_errors=True)
        if os.path.isdir(args.ausgabe):
            with open(os.path.join(args.ausgabe, "bau_status.json"), "w", encoding="utf-8") as f:
                json.dump({"status": status, "text": text, "zeit": datetime.datetime.now().isoformat(timespec="seconds"),
                           "phase": EREIGNISSE.phase if EREIGNISSE else None}, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def _signal(signum, _frame):
    ABBRUCH["signal"] = True


# =============================================================================== CLI
def argumente(argv):
    """Kommandozeile parsen, Profil anwenden, Optionen auflösen (_vorbereiten); wirft BauFehler."""
    argv = list(argv)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="befehl", required=True)
    s = sub.add_parser("bauen", help="neue Strecke zusammenbauen")
    s.add_argument("--definition", default=None, help="Definitions-JSON (Standard strecke/beispiele/teststrecke.json)")
    s.add_argument("--projekt", default=None, help="Editor-Projektordner (projekt.json → definition.json, Versatz)")
    s.add_argument("--generator-ausgabe", default=None,
                   help="Generatorordner (Standard testausgabe/strecke; mit --ereignisse-json <ausgabe>/generator)")
    s.add_argument("--neu-erzeugen", action="store_true", help="Generator auch bei vorhandener Ausgabe ausführen")
    s.add_argument("--traeger", default=os.path.join(PAKETE, "portimao_original_wiederhergestellt.zip"))
    s.add_argument("--traeger-cache", default=AUSGABE, help="Ordner für die entpackte Träger-ERP (≈ 420 MB, wiederverwendet)")
    s.add_argument("--neu-entpacken", action="store_true")
    s.add_argument("--name", default="teststrecke_v1")
    s.add_argument("--versatz", default=None,
                   help="Verschiebung Generator → Spielwelt \"dx,dy,dz\" in m (Standard: strecke/platzierung.py, "
                        "Teststrecke -591,0,-473; Strecken in Portimão-Lage: 0,0,0)")
    s.add_argument("--ausgabe", default=None, help="Arbeitsordner für ERPs, bericht.json, Draufsichten "
                   "(Standard testausgabe/neue_strecke/zusammenbau; mit --ereignisse-json testausgabe/neue_strecke/<name>)")
    s.add_argument("--bericht-md", default=None,
                   help="Markdown-Bericht (Standard ergebnisse/neue_strecke.md; mit --ereignisse-json <ausgabe>/bericht.md)")
    s.add_argument("--ohne-geometrie", action="store_true")
    s.add_argument("--mauer-physik", choices=("senkrecht", "alle", "keine"), default="senkrecht",
                   help="Mauern in der Fahrphysik (TRN~): senkrecht (Standard), alle oder keine")
    s.add_argument("--trn-flags", choices=("kanten", "alle7"), default="kanten",
                   help="Dreiecksflags der Fahrphysik: kanten (Standard, Bit gelöscht an freien Rändern "
                        "wie im Original) oder alle7 (bis v13)")
    # AP15: Diagnose-Varianten der Fahrphysik (Standard = bisheriges Verhalten)
    s.add_argument("--geometrie-dy", type=float, default=0.0,
                   help="Geometrie (sichtbar + Physik) in y verschieben, Logik bleibt; -0.2 = Portimãos Abstand "
                        "TrackSpace/KI-Gates/TS 0,20 m über der Physik (AP15)")
    s.add_argument("--logik-dy", type=float, default=0.0,
                   help="TrackSpace, KI-Gates und TS-Tore in y anheben; 0.2 = Portimãos Abstand über der Physik (AP15)")
    s.add_argument("--plaetze-dy", type=float, default=0.0,
                   help="Fahrzeugplätze in y anheben; Portimão ≈ 0.03 (AP15)")
    s.add_argument("--physik-umfang", choices=("voll", "ohne_grund", "fahrbahn"), default="voll",
                   help="TRN~-Inhalt: voll (Standard), ohne_grund (Grundfläche nur sichtbar), fahrbahn (nur Asphalt, "
                        "Boxengasse, Linien, Randsteine) (AP15)")
    s.add_argument("--physik-kante-max", type=float, default=None,
                   help="Streckendreiecke der Physik konform unterteilen bis Kantenlänge <= Wert in m (AP15)")
    s.add_argument("--grundflaeche", choices=("raster", "adaptiv"), default="raster",
                   help="Grundfläche: raster (Standard, 8-m-Raster unter der Strecke) oder adaptiv (Schritt E: "
                        "Delaunay-Netz, schließt an den Streckenrand an, 4 m an der Strecke bis 64 m in der Ferne)")
    s.add_argument("--gelaende-huegel", type=_huegel_liste, default=None, metavar="H[@X:Z:R],…",
                   help="experimentell (Spieltest T4): Hügel (+) bzw. Senken (−) dieser Höhen in Metern in die "
                        "Grundfläche, mit Abstand zur Strecke und zum Lochrand (spiel_geometrie.huegel_anwenden); "
                        "mit @X:Z:R an festem Ort (Spielkoordinaten) und Radius, sonst automatisch")
    s.add_argument("--welt-uv", choices=("konstant", "echt"), default="konstant",
                   help="Welt-UV der Terrain-Vertices: konstant (Standard, ein Texel der Portimão-Karten je Material) oder "
                        "echt (Weltprojektion über den Streckenbereich, für eigene Weltkarten; docs/welt_texturen.md)")
    s.add_argument("--weltkarten", action=argparse.BooleanOptionalAction, default=None,
                   help="eigene Boden-, TrackInfo- und Außenterrain-Karten (setzt --welt-uv echt; docs/welt_texturen.md); "
                        "im Profil empfohlen an, --no-weltkarten schaltet sie ab")
    s.add_argument("--biom", choices=("standard", "wueste"), default="standard",
                   help="mit --weltkarten: standard = Gras, wueste = Bahrains Sand und Fels, kein Bodenbewuchs")
    s.add_argument("--farbton", choices=("standard", "trocken", "saftig", "herbst"), default="standard",
                   help="mit --weltkarten: Farbton der Grasschichten und des Außenterrains")
    s.add_argument("--umgebung", default=None, metavar="PFAD",
                   help="umgebung.json (Bäume, Büsche, Palmen, Kulissenobjekte, Bodenbewuchs; strecke/umgebung.py, "
                        "docs/zwischenstand/umgebung_vertrag.md); mit --projekt automatisch aus dem Projektordner")
    s.add_argument("--ohne-umgebung", action="store_true", help="Umgebungsdatei des Projekts nicht verwenden")
    s.add_argument("--pvs-variante", choices=("alles", "strecke"), default="alles",
                   help="Hauptvariante: alles = Original-Zellen sehen alles (Standard); strecke = neue Zellen "
                        "entlang der Strecke (strecke/spiel_pvs.py)")
    s.add_argument("--ki-daten", choices=("vorlage", "skalieren", "profil"), default="vorlage",
                   help="KI-Daten je Fahrzeug: vorlage = Portimão unverändert (Standard), skalieren/profil = an die neue "
                        "Strecke angepasst (AP12, experimentell)")
    s.add_argument("--ki-vp-abtastung", choices=("auto", "skaliert", "original"), default="",
                   help="Stützpunkte der Zeittabellen (VPTimes, Hauptroute): auto (Standard) = skaliert mit der Streckenlänge, "
                        "solange die Gesamtmenge unter 300 MB bleibt, sonst wie in der Vorlage (Mod 1.0.2 stürzt darüber ab)")
    s.add_argument("--ki-nur-typen", default="",
                   help="nur diese KI-Ressourcentypen anpassen (Komma), z. B. VPTimes; Rest unverändert")
    s.add_argument("--ki-ohne-pakete", default="",
                   help="Fahrzeugpakete (Komma), deren KI-Daten unverändert bleiben, z. B. f1_2024_story_vehicle_package")
    s.add_argument("--pvs-korridor", type=float, default=60.0, help="Korridorbreite je Seite für --pvs-variante strecke")
    s.add_argument("--nur-original-pvs", action="store_true",
                   help="nur die Variante mit Original-PVS schreiben und verpacken (spart ~3 GB)")
    s.add_argument("--nur-hauptvariante", action="store_true",
                   help="nur die Hauptvariante (--pvs-variante) schreiben und verpacken, keine _original_pvs (AP24)")
    # AP16: eigener Streckencode (Standard = bisheriges Verhalten, Träger-Code portimao)
    s.add_argument("--streckencode", default=streckencode.STANDARD_ALT,
                   help="Code der Strecke im Paket; muss in der Spieldatenbank vorhanden sein (z. B. sochi), "
                        "siehe docs/streckencode.md. Standard portimao = keine Umbenennung")
    s.add_argument("--ziel", choices=streckencode.ZIELE, default=streckencode.ZIEL_EGO,
                   help="ego = Paket für den Track Installer, swofty = Addon für den Swofty Launcher mit eigenem Code und "
                        "Namen im Menü (docs/swofty_launcher.md)")
    s.add_argument("--renndaten", action=argparse.BooleanOptionalAction, default=False,
                   help="Runden, Kraftstoff, Reifen und KI-Zeiten aus der Strecke berechnen (strecke/renndaten.py)")
    s.add_argument("--traeger-teilen", action="store_true",
                   help="nur mit --ziel swofty: Strecke ohne Träger-Texturen (0,45 statt 3 GB), Träger-Addon einmal im Paketordner")
    s.add_argument("--raster", type=int, choices=(26,), default=None,
                   help="Plätze, Garagen und Boxen für so viele Autos in der Welt anlegen (Standard: 24 wie der Träger)")
    s.add_argument("--streckenname", default=None, help="track_name im Manifest (nur mit --streckencode)")
    s.add_argument("--aktivierung-vorlage", default=None,
                   help="activation.copy_from_track im Manifest (nur mit --streckencode), z. B. portimao")
    s.add_argument("--metadaten", action=_SchluesselWert, metavar="SCHLUESSEL=WERT", default=None,
                   help="Streckenmetadaten im Manifest (activation.track_overrides) setzen oder überschreiben, mehrfach "
                        "möglich, z. B. allow_night_start=True (docs/streckenname.md)")
    s.add_argument("--ohne-metadaten", action="store_true",
                   help="Länge/Kurvenzahl/Nachtstart nicht automatisch ins Manifest schreiben")
    # AP21: Boxengasse nach den Regeln der 30 Originale (docs/zwischenstand/ap21.md); Standard aus = v17/v18
    s.add_argument("--boxen-korrektur", action="store_true",
                   help="stetige Boxen-Spline, TS-Box-Volumes über die ganze Spline, Haupt-TS ohne Boxengasse, "
                        "Einfahrtszonen erst neben der Fahrbahn, Kurven/Bremslinien/pair_id der KI-Boxenroute (AP21)")
    s.add_argument("--ohne-minimap", action="store_true")
    s.add_argument("--ohne-pakete", action="store_true")
    # AP24: Editor-Schnittstelle (docs/editor_schnittstelle.md)
    s.add_argument("--profil", choices=sorted(BAUPROFILE),
                   help="benanntes Bauprofil; empfohlen = %s" % BAUPROFILE["empfohlen"]["optionen"])
    s.add_argument("--ereignisse-json", action="store_true",
                   help="eine JSON-Zeile je Ereignis auf stdout (Log auf stderr); setzt --atomar, --pruefregeln streng, "
                        "Schnappschuss der Definition und frischen Generatorlauf")
    s.add_argument("--paket-ordner", default=None, help="Zielordner der Pakete (Standard testausgabe/pakete)")
    s.add_argument("--atomar", action="store_true", default=None,
                   help="Pakete in <ausgabe>/_paket_tmp bauen und prüfen, erst dann in den Paketordner verschieben")
    s.add_argument("--ueberschreiben", action="store_true", help="mit --atomar: vorhandene Pakete ersetzen")
    s.add_argument("--fehlerhafte-behalten", action="store_true",
                   help="mit --atomar: Pakete mit Prüffehlern trotzdem in den Paketordner verschieben")
    s.add_argument("--abbruch-datei", default=None,
                   help="sobald diese Datei existiert, bricht der Bau an der nächsten Phasengrenze ab (Exitcode 3)")
    s.add_argument("--pruefregeln", choices=("streng", "melden", "aus"), default=None,
                   help="streng = Fehler einer Prüfregel stoppt den Bau (Standard mit --ereignisse-json); "
                        "melden = nur berichten (Standard); aus")
    s.add_argument("--schnappschuss", action="store_true",
                   help="Definition nach <ausgabe>/eingabe kopieren und von dort bauen (Standard mit --ereignisse-json)")
    s.add_argument("--arbeit-aufraeumen", action="store_true", help="Arbeits-ERPs (≈ 400 MB je Variante) am Ende löschen")
    s.add_argument("--pruefsummen", action="store_true", help="SHA-256 der fertigen Pakete ins Ergebnis")
    s.set_defaults(func=cmd_bauen)

    p = sub.add_parser("pruefen", help="Prüfregeln ohne Bau (Stufe a) bzw. für gebaute Logik (Stufe b), JSON auf stdout")
    p.add_argument("--definition", default=None)
    p.add_argument("--projekt", default=None)
    p.add_argument("--versatz", default=None)
    p.add_argument("--mit-generator", action="store_true", help="Generator rechnen (≈ 2–5 s, ohne Dateien)")
    p.add_argument("--generator-ausgabe", default=None, help="vorhandene Generatorausgabe mitprüfen")
    p.add_argument("--umgebung", default=None, help="umgebung.json mitprüfen (Regeln UMG_*); mit --projekt automatisch")
    p.add_argument("--ohne-umgebung", action="store_true")
    p.add_argument("--logik", default=None, help="logik_platziert.json eines Baus (Stufe b)")
    p.add_argument("--erp", default=None, help="gebaute ERP (Logik neu gelesen, Stufe b)")
    p.add_argument("--streckencode", default=None)
    p.add_argument("--pvs-variante", choices=("alles", "strecke"), default=None)
    p.add_argument("--kompakt", action="store_true", help="JSON in einer Zeile")
    p.set_defaults(func=cmd_pruefen)
    r = sub.add_parser("regeln", help="Regelkatalog (JSON, mit --md als Markdown-Tabelle)")
    r.add_argument("--md", action="store_true")
    r.set_defaults(func=cmd_regeln)
    q = sub.add_parser("profile", help="Bauprofile als JSON")
    q.set_defaults(func=cmd_profile)

    profil = _profil_vorab(argv) if argv and argv[0] == "bauen" else None
    if profil:
        s.set_defaults(**BAUPROFILE[profil]["optionen"])
    args = ap.parse_args(argv)
    _vorbereiten(args)
    return args


def main(argv=None):
    # Windows-Konsole (cp1252): Hilfetext enthält „→“ u. a.; ohne UTF-8 bricht `bauen --help` ab.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        args = argumente(argv)
    except BauFehler as e:
        if argv and argv[0] == "bauen" and "--ereignisse-json" in argv:
            Ereignisse(sys.stdout).senden("fehler", phase="vorbereiten", art="BauFehler", text=str(e))
            return e.code
        sys.exit("Abbruch: %s" % e)
    if args.befehl == "bauen":
        ABBRUCH["datei"] = args.abbruch_datei
        for name in (("SIGINT", "SIGTERM", "SIGBREAK") if args.ereignisse_json else ()):  # klassisch: Strg+C wie bisher
            if hasattr(signal, name):
                try:
                    signal.signal(getattr(signal, name), _signal)
                except (ValueError, OSError):
                    pass
        if args.ereignisse_json:
            return _bauen_json(args)
        try:
            code = args.func(args)
        except BauFehler as e:
            sys.exit("Abbruch: %s" % e)
        except BauAbbruch as e:
            _unvollstaendig(args, "abgebrochen", str(e))
            sys.exit("Abbruch: %s" % e)
        return 0 if code == 0 or args.pruefregeln != "streng" else code
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
