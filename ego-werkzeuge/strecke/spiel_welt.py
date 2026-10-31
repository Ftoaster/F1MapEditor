"""AP11b: Welt des Portimão-Trägers für eine neue Strecke aufräumen, Streckenlogik der neuen Strecke
an den Träger anpassen, Platzhalter für PVS und Licht.

Ablauf (siehe ``neue_strecke_tool.py bauen``):

1. ``TraegerZustand.erfassen(archiv)`` merkt sich den unveränderten Träger (Instanzen je Layer,
   EventGraph-Verweise, Lichtkarte, Streckenlogik als ``Schreiber``-Vorlage).
2. ``welt_aufraeumen`` leert die Layer mit Portimão-Objekten (Gebäude, Barrieren, Bäume,
   Zuschauer …). Entfernt werden **nur Instanzen, die schon im Original-Träger standen**; was ein
   anderes Modul (z. B. AP11a) neu hinzugefügt hat, bleibt erhalten. Vollständig geleerte Layer
   erhalten exakt die Form der im Original vorhandenen leeren Layer (``instances: []``).
3. ``logik_vorbereiten`` setzt die neutrale Generatorlogik in Spielkoordinaten (``platzierung``),
   ordnet die Bremslinien-IDs den ``.brakelinesettings`` zu, baut die vollständige TM-Zonenliste und
   berechnet Garagen-/MyTeam-/Safety-Car-Plätze in der neuen Boxengasse.
4. ``pvs_alles_sichtbar`` und ``licht_platzhalter`` (AO weiß, neue Lichtkarte).

Entscheidungen je Layer: ``LAYER_REGELN`` (mit Begründung, auch im Bericht
``ergebnisse/neue_strecke.md``). Gesichert = aus den Daten gelesen; „Vermutung“ ist gekennzeichnet.
"""

import copy
import math
import struct
from collections import Counter, defaultdict

import numpy as np

from ego import bxml, licht, pkg, pvs, streckenlogik
from . import platzierung
from .pruefregeln import (BOXEN_VERLAENGERUNG_NACH_M, BOXEN_VERLAENGERUNG_VOR_M,  # AP24: Regeln zentral
                          BREMSLINIEN_MIN_ABSTAND_GATES, ZEITFAHR_ZONEN)
from .spielformat import Schreiber, _pfad, _struktur_eindeutig, _tabellenwert

BEHALTEN, LEEREN, ANPASSEN = "behalten", "leeren", "anpassen"

# Entscheidung je Layer (Kurzname ohne "<code>-" und ".layer").
LAYER_REGELN = {
    # --- Logik / Fahrzeugplätze (Inhalt wird angepasst) ---------------------------------------
    "ai": (BEHALTEN, "SplineData → KI-Linie (aispline, wird neu geschrieben)"),
    "trackspace": (BEHALTEN, "TrackSpace → trackspacespline (wird neu geschrieben)"),
    "vehicles": (ANPASSEN, "Start-, Box- und Garagenplätze; neu in die neue Boxengasse/Startaufstellung gesetzt"),
    "vehicles_myteam": (ANPASSEN, "11. Box/Garage (2026er-Feld, MyTeam); an Box 11 der neuen Boxengasse gesetzt"),
    "vehicles_current_myteam": (ANPASSEN, "12. Box/Garage und Startplätze 23/24 (MyTeam); neu gesetzt"),
    "safety_car": (ANPASSEN, "Parkposition des Safety-Cars; ans Ende der neuen Boxengasse gesetzt"),
    "dynamic_racing_line": (BEHALTEN, "DRL-Komponente ohne Lage; Zonen kommen aus der TM"),
    "track_data": (BEHALTEN, "GeneralTrackData (Verweis auf Trainingsprogramm), ortsunabhängig"),
    # --- Terrain, Umgebung, Licht -------------------------------------------------------------
    "terrain": (BEHALTEN, "Terrain$PhysicsTerrain/TerrainRender: verweist auf TerrainRes/TRN~ (Geometrie AP11a)"),
    "sky": (BEHALTEN, "Himmelskuppel (core_assets), ortsunabhängig"),
    "timeofday": (BEHALTEN, "Tageszeit/Wetter-Komponenten"),
    "lights": (BEHALTEN, "Skylight"),
    "fog": (BEHALTEN, "Nebel"),
    "postprocess": (BEHALTEN, "Nachbearbeitung"),
    "lightprobes": (BEHALTEN, "IBL-Sonden; IBLConfig verweist per Name darauf (Pflicht)"),
    "compass": (BEHALTEN, "Kompass-Komponente ohne Lage"),
    # --- Kameras, Ereignisse, Audio (EventGraph-Verweise, keine Geometrie/Kollision) -------------
    "cameras": (BEHALTEN, "WritableCamera ohne Lage"),
    "cameras_hero": (BEHALTEN, "TV-/Heldenkameras; EventGraph verweist auf 13 Auslösezonen"),
    "cameras_intro": (BEHALTEN, "schon im Original leer"),
    "cameras_introplacement": (BEHALTEN, "Intro-Kameras; EventGraph nennt Kameranamen"),
    "cameras_pitstop": (BEHALTEN, "Boxenstopp-Kameras; EventGraph verweist auf 22 Auslösezonen"),
    "cameras_replay_main": (BEHALTEN, "Wiederholungskameras; EventGraph verweist auf 158 Auslösezonen"),
    "cameras_replay_pitbox": (BEHALTEN, "Wiederholungskameras Box; EventGraph-Verweise"),
    "cameras_replay_safety": (BEHALTEN, "Wiederholungskameras Safety-Car; EventGraph-Verweise"),
    "cameras_spectator": (BEHALTEN, "Zuschauerkameras; EventGraph nennt Kameranamen"),
    "art_cameras": (BEHALTEN, "statische Kameras (Menü/Präsentation), keine Geometrie"),
    "cutsceneplacement": (BEHALTEN, "Zwischensequenz-Positionen (Ziel, Parc fermé); keine Geometrie"),
    "cutsceneplacementcareer": (BEHALTEN, "Karriere-Zwischensequenzen; EventGraph nennt Namen"),
    "podium": (BEHALTEN, "Siegerehrung (Partikel, Sonne); EventGraph verweist auf 6 Partikeleffekte"),
    "animations-objects": (BEHALTEN, "Intro-Flagge weit außerhalb (y≈185 m); EventGraph-Verweis"),
    "vfx": (BEHALTEN, "Partikel/Auslösezonen (EventGraph: 148 Verweise); Modelle nur Regenblocker/Dummies"),
    "audio_env": (BEHALTEN, "Audio-Emitter; EventGraph verweist auf 6 Instanzen"),
    "audio_crowd": (BEHALTEN, "Audio-Zuschauerkulisse, nur Klang"),
    "audio_occlusion": (BEHALTEN, "Audio-Verdeckung, nur Klang"),
    "pvs": (BEHALTEN, "schon im Original leer"),
    "route_overrides": (BEHALTEN, "schon im Original leer"),
    "true_passby": (BEHALTEN, "schon im Original leer"),
    "trackmarkup_ai": (BEHALTEN, "schon im Original leer"),
    "trackmarkup_audio": (BEHALTEN, "schon im Original leer"),
    "trackmarkup_cannedevent": (BEHALTEN, "schon im Original leer"),
    "trackmarkup_design": (BEHALTEN, "schon im Original leer"),
    "trackmarkup_drl": (BEHALTEN, "schon im Original leer"),
    "trackmarkup_forbidden_zone_audio": (BEHALTEN, "schon im Original leer"),
    "trackmarkup_game": (BEHALTEN, "schon im Original leer"),
    # --- Portimão-Objekte: stehen auf/neben der neuen Strecke --------------------------------
    "buildings": (LEEREN, "Gebäude (328) stehen im Bereich der neuen Strecke; Höhen passen nicht zum neuen Gelände"),
    "dressing": (LEEREN, "Kleinobjekte (847) der alten Anlage"),
    "barriers_static": (LEEREN, "Leitplanken/Mauern (5.277) der alten Streckenführung; kreuzen die neue Fahrbahn"),
    "barriers_dynamic": (LEEREN, "Reifenstapel/Tecpro (2.284, davon 465 PhysicsEntity mit PYRB-Kollision): blockieren Autos"),
    "floodlights": (LEEREN, "Flutlichtmasten (495) der alten Anlage"),
    "offtrack_vehicles": (LEEREN, "abgestellte Fahrzeuge (2.025)"),
    "speedtrees": (LEEREN, "Bäume (1.274) stehen teils auf der neuen Fahrbahn"),
    "speedtrees_distant": (LEEREN, "Billboard-Baumgruppen der alten Umgebung (Lage nur im Modell, nicht prüfbar)"),
    # --- Ebenen der Portimão-Mod 1.0.2 (neues Konvertierungsskript, andere Namen und zusätzliche Ebenen) ------------
    "trees": (LEEREN, "Bäume (1.274) der Mod 1.0.2 (vorher speedtrees); würden in der Luft über der neuen Strecke hängen"),
    "trees_distant": (LEEREN, "Billboard-Baumgruppen der Mod 1.0.2 (vorher speedtrees_distant)"),
    "braking_boards": (LEEREN, "Entfernungstafeln (36) der alten Streckenführung; neue Tafeln kommen aus der Ebene dressing"),
    "terrain_sponsors": (LEEREN, "Werbeflächen auf dem alten Gelände (vorher terrain_sponsors_f1)"),
    "zone_signs_xmode": (LEEREN, "Schilder der alten Straight-Mode-Zonen"),
    "zone_signs_drs_*": (LEEREN, "Schilder der alten DRS-Zonen (Ebenenname trägt einen Hash)"),
    "audio_occlusion_next_gen": (BEHALTEN, "Audio-Verdeckung, nur Klang"),
    "trackmarkup_audio_corner_assist": (BEHALTEN, "schon im Original leer"),
    "trackmarkup_audio_whoosh_left": (BEHALTEN, "schon im Original leer"),
    "trackmarkup_audio_whoosh_right": (BEHALTEN, "schon im Original leer"),
    "trackmarkup_drs": (BEHALTEN, "schon im Original leer"),
    "trackmarkup_xmode": (BEHALTEN, "schon im Original leer"),
    "objects_garages_f1_26": (LEEREN, "Boxengebäude der alten Boxengasse (F1 2026; im Bau an die neuen Boxen versetzt)"),
    "objects_garages_f1_26_myteam": (LEEREN, "Boxengebäude der alten Boxengasse (F1 2026 MyTeam; versetzt)"),
    "objects_garages_f1_previous_myteam": (LEEREN, "Boxengebäude der alten Boxengasse (MyTeam Vorgänger; versetzt)"),
    "objects_garages_f1_story": (LEEREN, "Boxengebäude der alten Boxengasse (Story; versetzt)"),
    "objects_garages_f2_26": (LEEREN, "Boxengebäude der alten Boxengasse (F2 2026; versetzt)"),
    "groundcover": (LEEREN, "Bodenbewuchs nach Portimão-Maske; würde auf neuer Fahrbahn wachsen"),
    "decals": (LEEREN, "Decals (810) projizieren auf die alte Fahrbahnlage"),
    "drs": (LEEREN, "DRS-Tafeln der alten Zonen (unsichtbar geschaltet, Lage im Modell)"),
    "objects_sponsors": (LEEREN, "Werbung, Startampel, Marshal-Lichter, Podium; 25 PhysicsEntity + 23 statische Kollisionen"),
    "objects_sponsors_pit_limits_f1": (LEEREN, "Boxenlimit-Tafel mit Kollision (PhysicsEntity) an alter Boxeneinfahrt"),
    "objects_sponsors_pit_limits_f2": (LEEREN, "Boxenlimit-Tafel mit Kollision (PhysicsEntity) an alter Boxeneinfahrt"),
    "terrain_sponsors_f1": (LEEREN, "Werbeflächen auf dem alten Gelände (lightmap_bake_source = Terrain)"),
    "objects_garages_classic": (LEEREN, "Boxengebäude der alten Boxengasse (im Innenfeld der neuen Strecke)"),
    "objects_garages_f1": (LEEREN, "Boxengebäude der alten Boxengasse"),
    "objects_garages_f1_myteam": (LEEREN, "Boxengebäude der alten Boxengasse"),
    "objects_garages_f2": (LEEREN, "Boxengebäude der alten Boxengasse"),
    "animatedcharacters": (LEEREN, "Personen an der alten Boxenmauer"),
    "animatedcharacters_pitboard": (LEEREN, "Boxentafel-Halter an der alten Boxenmauer"),
    "grandstand-crowd": (LEEREN, "Zuschauer Tribünen (655 Zonen + CrowdPlacement-Instanz)"),
    "standing-crowd": (LEEREN, "Zuschauer stehend (1.050 Zonen + CrowdPlacement-Instanz)"),
    "marshal-crowd": (LEEREN, "Streckenposten (187 Zonen + CrowdPlacement-Instanz)"),
    "podium-crowd": (LEEREN, "Zuschauer an der Siegerehrung (28 Zonen + CrowdPlacement-Instanz)"),
}

# Leere Layer im Format des Originals (portimao-pvs.layer, 100 Byte, Rundlauf geprüft)
LEERER_LAYER_VORLAGE = bytes.fromhex(
    "21706b67494e535410000080380000802169646902000001000000003000008033fd48060b00000012000000"
    "f640a60721696c6900000000217663610100000044000080217362691700000009696e7374616e6365730005"
    "6c6179657200037076730000")

PHYSIK_TEMPLATES = ("PhysicsEntity", "DynamicLightStaticCollision")
PHYSIK_KOMPONENTEN = ("PhysicsSystem", "StaticBody")


class WeltFehler(ValueError):
    pass


# =============================================================================== Hilfen pkg
def layer_kurzname(kennung, code="portimao"):
    praefix = "eaid://environment_package/tracks/%s/wep/%s-" % (code, code)
    if not kennung.startswith(praefix) or not kennung.endswith(".layer"):
        return None
    return kennung[len(praefix):-len(".layer")]


def layer_ressourcen(archiv, code="portimao"):
    out = {}
    for r in archiv:
        if r.resource_type == "WOInstances":
            k = layer_kurzname(r.identifier, code)
            if k is not None:
                out[k] = r
    return out


def komponenten(paket, inst):
    try:
        blk = paket.feld(inst.block, "overrides").wert.ziel
    except pkg.PkgFormatError:
        return []
    if blk is None or blk.kennung != b"!idi":
        return []
    return [paket.wert(f.name) for f in blk.felder]


def hat_physik(paket, inst):
    if inst._wert("template") in PHYSIK_TEMPLATES:
        return True
    return any(k in PHYSIK_KOMPONENTEN for k in komponenten(paket, inst))


def leerer_layer(layer_name, vorlage=LEERER_LAYER_VORLAGE):
    """INST-pkg ohne Instanzen im Aufbau der im Original vorhandenen leeren Layer."""
    p = pkg.lesen(vorlage)
    wurzel = p.wurzel.ziel
    f_inst, f_layer = p.feld(wurzel, "instances"), p.feld(wurzel, "layer")
    sbi = p.tabelle(f_layer.wert)
    name = layer_name.encode("utf-8")
    if not name or len(name) > 255 or b"\0" in name:
        raise WeltFehler("ungültiger Layername %r" % layer_name)
    a, b = b"\x09instances\x00", b"\x05layer\x00"
    sbi.daten = a + b + bytes([len(name)]) + name + b"\x00"
    f_inst.name.index, f_layer.name.index, f_layer.wert.index = 0, len(a), len(a) + len(b)
    # Originale enden auf eine durch 4 teilbare Länge (Nullbytes hinter !sbi)
    p.nachlauf = b""
    p.nachlauf = b"\0" * (-len(pkg.schreiben(p)) % 4)
    daten = pkg.schreiben(p)
    pruef = pkg.lesen(daten)
    if pruef.inhalt() != {"instances": [], "layer": layer_name}:
        raise WeltFehler("leerer Layer fehlerhaft: %r" % pruef.inhalt())
    pruef.pruefen()
    return daten


def _erreichbare_bloecke(paket):
    gesehen = {}
    stapel = [paket.wurzel.ziel, paket.tabellen.ziel]
    while stapel:
        b = stapel.pop()
        if b is None or id(b) in gesehen:
            continue
        gesehen[id(b)] = b
        stapel.extend(r.ziel for r in b.verweise() if r.typ == 0x80 and r.ziel is not None)
    return gesehen


def instanzen_filtern(paket, behalten):
    """Entfernt Instanzen (behalten(inst) → False) und alle nicht mehr erreichbaren Strukturblöcke.
    Wertetabellen bleiben vollständig (unbenutzte Einträge sind im Format zulässig)."""
    liste = paket.feld(paket.wurzel.ziel, "instances").wert.ziel
    instanzen = paket.instanzen()
    liste.werte = [v for v, i in zip(liste.werte, instanzen) if behalten(i)]
    erreichbar = _erreichbare_bloecke(paket)
    paket.bloecke = [b for b in paket.bloecke if id(b) in erreichbar]
    return len(instanzen) - len(liste.werte)


# =============================================================================== Trägerzustand
class TraegerZustand:
    """Unveränderter Träger: Instanzen je Layer, EventGraph-Verweise, Lichtkarte, Logikvorlage."""

    def __init__(self):
        self.code = "portimao"
        self.layer_uids = {}       # Kurzname -> set(uid)
        self.layer_info = {}       # Kurzname -> Statistik
        self.welten = {}           # Weltname -> [Layer-Kurznamen]
        self.evgt_uids = set()
        self.evgt_namen = set()
        self.lichtkarte = None
        self.schreiber = None
        self.plaetze = {}          # Kurzname -> {name: (matrix, uid, template)}
        self.pvs_obj = None

    @classmethod
    def erfassen(cls, archiv, code="portimao"):
        z = cls()
        z.code = code
        for kurz, r in layer_ressourcen(archiv, code).items():
            p = pkg.lesen(r.fragments[0].read())
            inst = p.instanzen()
            z.layer_uids[kurz] = {i.uniqueid for i in inst}
            z.layer_info[kurz] = {
                "instanzen": len(inst),
                "templates": dict(Counter(i._wert("template") for i in inst).most_common()),
                "physik": sum(hat_physik(p, i) for i in inst),
                "modelle": sum(1 for i in inst if i.modell),
                "positionen": [i.position for i in inst if i.position is not None],
            }
            if kurz.startswith("vehicles") or kurz == "safety_car":
                z.plaetze[kurz] = {i.name: (i.matrix, i.uniqueid, i._wert("template")) for i in inst}
        for r in archiv:
            if r.resource_type == "World" and "/tracks/%s/" % code in r.identifier:
                d = pkg.lesen(r.fragments[0].read()).inhalt()
                z.welten[d.get("name") or r.identifier.rsplit("/", 1)[-1]] = [
                    layer_kurzname(l, code) for l in d.get("layers", [])]
            if r.resource_type == "EventGraph" and "/tracks/%s/" % code in r.identifier:
                for fr in r.fragments:
                    d = fr.read()
                    if d[:4] != b"!pkg":
                        continue
                    inhalt = pkg.lesen(d).inhalt()
                    z._evgt_sammeln(inhalt.get("factories", []))
                    z.evgt_namen |= {v for v in inhalt.get("varvalues", []) if isinstance(v, str)}
        lml = archiv.find(licht.LML_KENNUNG)
        if lml is not None:
            z.lichtkarte = licht.Lichtkarte.aus_bytes(lml.fragments[0].read())
        z.schreiber = Schreiber(archiv, code)
        obj = archiv.find_all(r"header\.pvsbin$")
        if obj:
            z.pvs_obj = pvs.obj_lesen(obj[0].fragments[0].read())
        return z

    def _evgt_sammeln(self, o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "worldobjectid" and isinstance(v, str):
                    self.evgt_uids.add(v)
                else:
                    self._evgt_sammeln(v)
        elif isinstance(o, list):
            for v in o:
                self._evgt_sammeln(v)

    def welten_je_layer(self):
        out = defaultdict(list)
        for w, ls in self.welten.items():
            for l in ls:
                out[l].append(w)
        return out


# =============================================================================== Welt aufräumen
UNBEKANNTER_LAYER = "unbekannter Layer: unverändert gelassen"


def regel_fuer(regeln, kurz):
    """Regel eines Layers: genauer Name, sonst ein Eintrag mit ``*`` am Ende als Vorsatz (Mod 1.0.2 hängt Hashes an Namen)."""
    if kurz in regeln:
        return regeln[kurz]
    for muster, regel in regeln.items():
        if muster.endswith("*") and kurz.startswith(muster[:-1]):
            return regel
    return (BEHALTEN, UNBEKANNTER_LAYER)


def welt_aufraeumen(archiv, zustand, regeln=None):
    """Leert die Layer mit Regel LEEREN. Entfernt nur Instanzen des Original-Trägers, prüft vorher,
    dass keine davon vom EventGraph angesprochen wird. Gibt einen Bericht je Layer zurück."""
    regeln = regeln or LAYER_REGELN
    bericht = []
    for kurz, r in sorted(layer_ressourcen(archiv, zustand.code).items()):
        aktion, grund = regel_fuer(regeln, kurz)
        p = pkg.lesen(r.fragments[0].read())
        inst = p.instanzen()
        eintrag = {"layer": kurz, "aktion": aktion, "grund": grund, "vorher": len(inst), "entfernt": 0,
                   "physik_entfernt": 0, "neu_erhalten": 0, "welten": len(zustand.welten_je_layer().get(kurz, []))}
        if aktion == LEEREN:
            original = zustand.layer_uids.get(kurz, set())
            weg = [i for i in inst if i.uniqueid in original]
            konflikt = [i.name for i in weg if i.uniqueid in zustand.evgt_uids]
            if konflikt:
                raise WeltFehler("Layer %s: EventGraph verweist auf %s" % (kurz, konflikt[:3]))
            eintrag["physik_entfernt"] = sum(hat_physik(p, i) for i in weg)
            eintrag["templates_entfernt"] = dict(Counter(i._wert("template") for i in weg).most_common())
            if len(weg) == len(inst):
                name = p.wert(p.feld(p.wurzel.ziel, "layer").wert)
                daten = leerer_layer(name)
            else:
                uids = {i.uniqueid for i in weg}
                instanzen_filtern(p, lambda i: i.uniqueid not in uids)
                p.pruefen()
                daten = pkg.schreiben(p)
            r.set_fragment_data(0, daten)
            nach = pkg.lesen(daten).instanzen()
            eintrag.update(entfernt=len(weg), neu_erhalten=len(nach))
        eintrag["nachher"] = eintrag["vorher"] - eintrag["entfernt"]
        bericht.append(eintrag)
    return bericht


#: Vorlage der Entfernungstafeln im Portimão-Träger (Ebene dressing, RenderModel ohne Physik; Varianten über die
#: Materialbindung ``…por_dressing_brake_marker_a.emb?context=100|150|200|250|default``). Die Zahlen liegen als
#: Flipbook 3×2 in ``por_dressing_brake_marker_a_02_d``: Feld 0 = „50“ (Material ohne Zusatz, Bindung ``default``),
#: Felder 1–4 = 100–250.
BREMSMARKER_LAYER = "dressing"
BREMSMARKER_MODELL = "por_dressing_brake_marker_a"
BREMSMARKER_KONTEXT = {50: "default"}


def bremsmarker_setzen(archiv, zustand, marker, versatz_matrix=None):
    """Entfernungstafeln (Generator ``logik['bremsmarker']``) als Klone der Träger-Tafeln in die Ebene ``dressing``.

    Muss VOR ``welt_aufraeumen`` laufen: die Vorlagen-Instanzen werden dort entfernt, die Klone (neue IDs) bleiben.
    Je Tafel die Vorlage mit passender Zahl (Materialbindung ``context=<meter>``), neue Lagematrix (Generator →
    Weltkoordinaten über ``platzierung.matrix``). Rückgabe: Bericht."""
    if not marker:
        return {"tafeln": 0}
    r = layer_ressourcen(archiv, zustand.code).get(BREMSMARKER_LAYER)
    if r is None:
        raise WeltFehler("Träger ohne Ebene %s – keine Vorlage für Entfernungstafeln" % BREMSMARKER_LAYER)
    p = pkg.lesen(r.fragments[0].read())
    vorlagen = {}
    for i in p.instanzen():
        bindung = i._wert("overrides", "Model", "mat binding") or ""
        if BREMSMARKER_MODELL in str(i.modell or "") and "context=" in str(bindung):
            vorlagen.setdefault(str(bindung).rsplit("context=", 1)[1], i.index)
    umrechnen = versatz_matrix or platzierung.matrix
    gesetzt, fehlend = [], []
    import uuid as _uuid
    for n, m in enumerate(marker):
        kontext = BREMSMARKER_KONTEXT.get(int(m["meter"]), str(int(m["meter"])))
        if kontext not in vorlagen:
            fehlend.append(kontext)
            continue
        matrix = umrechnen(m["matrix"])
        name = "%s$bremsmarker_k%02d_%03d" % (BREMSMARKER_LAYER, int(m["kurve"]) + 1, int(m["meter"]))
        uid = _uuid.uuid5(_uuid.NAMESPACE_URL, "ego-strecke:bremsmarker:%d:%s" % (n, name)).bytes
        neu = pkg.instanz_hinzufuegen(p, vorlagen[kontext], matrix[12:15], name, uid)
        pkg.instanz_matrix_setzen(p, neu, matrix)
        gesetzt.append({"name": name, "meter": int(m["meter"]), "seite": m.get("seite"),
                        "position": [round(float(x), 2) for x in matrix[12:15]]})
    p.pruefen()
    r.set_fragment_data(0, pkg.schreiben(p))
    return {"tafeln": len(gesetzt), "fehlende_varianten": sorted(set(fehlend)), "vorlagen": sorted(vorlagen),
            "liste": gesetzt}


def verbliebene_physik(archiv, code="portimao"):
    """Instanzen mit Kollisions-/Physikkomponente in allen Layern (nach dem Aufräumen)."""
    out = []
    for kurz, r in layer_ressourcen(archiv, code).items():
        p = pkg.lesen(r.fragments[0].read())
        for i in p.instanzen():
            if hat_physik(p, i):
                out.append((kurz, i.name, i.position))
    return out


def welt_positionen(archiv, code="portimao"):
    """(Layer, Name, Template, Position, hat_modell) aller Instanzen mit Matrix."""
    out = []
    for kurz, r in layer_ressourcen(archiv, code).items():
        p = pkg.lesen(r.fragments[0].read())
        for i in p.instanzen():
            out.append((kurz, i.name, i._wert("template"), i.position, bool(i.modell), i.uniqueid))
    return out


# =============================================================================== Logik platzieren
def _vec(p, dy=0.0):
    v = [float(x) for x in platzierung.punkte(p)]
    if dy:
        v[1] += dy
    return v


def logik_platzieren(logik, logik_dy=0.0, plaetze_dy=0.0):
    """Kopie der neutralen Generatorlogik mit allen Spielformat-Koordinaten in Weltkoordinaten.

    ``logik_dy`` hebt TrackSpace-Splines, KI-Gates/Abstelllinien und TS-Tore zusätzlich an,
    ``plaetze_dy`` die Fahrzeugplätze (Portimão: Logik +0,20 m, Plätze +0,03 m über der Physik, AP15)."""
    lg = copy.deepcopy(logik)
    sp = lg["spielformate"]
    for s in sp["TrackSpace"]["splines"]:
        s["position"] = [_vec(p, logik_dy) for p in s["position"]]
    ai = sp["AISplineData"]
    for g in ai["gates"]:
        g["position"] = _vec(g["position"], logik_dy)
    for l in ai.get("retire_lines", []):
        if "position" in l:
            l["position"] = _vec(l["position"], logik_dy)
    for vl in sp["TS"]["volume_lists"]:
        for v in vl["volumes"]:
            for t in v["gates"]:
                for k in ("point1", "point2", "point3", "point4"):
                    t[k] = _vec(t[k], logik_dy)
    # Nachbarvolumes teilen Tore als gleiche Werte (pruefe_neutral vergleicht auf Gleichheit)
    for pl in sp["vehicles_layer"]["placements"]:
        pl["position"] = _vec(pl["position"], plaetze_dy)
        if "matrix" in pl:
            pl["matrix"] = platzierung.matrix(pl["matrix"])
            if plaetze_dy:
                pl["matrix"][13] += plaetze_dy
    lg["platzierung"] = {"versatz": list(platzierung.VERSATZ), "modul": "strecke/platzierung.py",
                         "logik_dy": logik_dy, "plaetze_dy": plaetze_dy}
    return lg


# =============================================================================== TM-Zonen
def _pit_abbildung(alt_zonen, neu_markup):
    """Stückweise lineare Abbildung Boxen-Fortschritt Vorlage → neu über gemeinsame Anker."""
    def anker(zonen, typ, feld):
        for z in zonen:
            if z["Type"] == typ:
                return float(z[feld])
        raise WeltFehler("Ankerzone %s fehlt" % typ)
    paare = [("pit_lane_entry_zone", "Start"), ("pit_lim_on", "Start"), ("pit_lim_on", "End"),
             ("pitlane_exit_zone", "End")]
    xa = [0.0] + [anker(alt_zonen, t, f) for t, f in paare] + [1.0]
    xn = [0.0] + [anker(neu_markup, t, f) for t, f in paare] + [1.0]
    if any(b < a for a, b in zip(xa, xa[1:])) or any(b < a for a, b in zip(xn, xn[1:])):
        raise WeltFehler("Boxenanker nicht monoton: %s / %s" % (xa, xn))
    return lambda f: float(np.interp(f, xa, xn))


def _zone(name, typ, spline, start, ende, props=None):
    return {"Name": name, "Type": typ, "SplineName": spline, "Start": float(start) % 1.0 if start != 1.0 else 1.0,
            "End": float(ende) % 1.0 if ende != 1.0 else 1.0, "Properties": dict(props or {})}


# ZEITFAHR_ZONEN: Regel und Begründung in strecke/pruefregeln.py (Prüfung ZONE_ZEITFAHR_ABSTAND, AP24)


def _vorlagenabstaende(out, alt, l_vorlage, l_neu, hauptlinie, namen=ZEITFAHR_ZONEN):
    """Setzt die Zonen `namen` auf dieselben Meterabstände zur Ziellinie wie in der Vorlage.
    Grenzen > 0,5 gelten als „vor der Linie“, sonst „nach der Linie“; exakt 0 und 1 bleiben."""
    def umrechnen(b):
        if b in (0.0, 1.0):
            return b
        meter = -(1.0 - b) * l_vorlage if b > 0.5 else b * l_vorlage
        return (meter / l_neu) % 1.0
    geaendert = {}
    vorlage = {a["Name"]: a for a in alt if a["SplineName"] == hauptlinie}
    for z in out:
        a = vorlage.get(z["Name"])
        if z["Name"] in namen and a is not None and z["SplineName"] == hauptlinie:
            z["Start"], z["End"] = umrechnen(float(a["Start"])), umrechnen(float(a["End"]))
            geaendert[z["Name"]] = (round(z["Start"], 4), round(z["End"], 4))
    return geaendert


def tm_aufbauen(lg, original, protokoll):
    """Vollständige TM-Zonenliste (für ``tm_rest_behalten=False``).

    - Generatorzonen (Sektoren, DRS, Boxen, Start, Safety-Car …) bleiben, werden aber auf die
      Portimão-Namen/IDs umbenannt (DRS-ID 1 wie im Träger), damit die Vorlagenelemente greifen.
    - Ergänzt: Sprach-Trigger an den Sektoren, Rolling-Start-IDs 1/2, ERS-Override-Zonen an der
      DRS-Zone, erste Kurve, X-Mode-Zonen, Speedtrap, DRL-Zonen je Kurve.
    - Übrige Portimão-Typen (Audio, KI-Overrides, Pannen, Marshal-Lichter …) mit gleichem
      normierten Fortschritt; Boxenzonen über die Boxen-Ankerabbildung.
    - Entfernt: ``crowd_excitement_zone`` (verweist auf entfernte CrowdZone-Instanzen)."""
    sp = lg["spielformate"]
    gen = copy.deepcopy(sp["TM"]["markup"])
    alt = [{"Name": z.name, "Type": z.typ, "SplineName": z.spline, "Start": z.start, "End": z.ende,
            "Properties": dict(z.eigenschaften)} for z in original.zonen]
    hl = next(s for s in sp["TrackSpace"]["splines"] if s["closed"])["name"]
    pit = next(s for s in sp["TrackSpace"]["splines"] if not s["closed"])["name"]
    f_pit = _pit_abbildung(alt, gen)
    gates = {int(g["id"]): g for g in sp["AISplineData"]["gates"]}
    ai = sp["AISplineData"]
    L = next(s for s in sp["TrackSpace"]["splines"] if s["closed"])["laenge_m"]

    def gp(gid):
        return float(gates[int(gid)]["progress"])

    namen_alt = defaultdict(list)
    for z in alt:
        namen_alt[z["Type"]].append(z["Name"])
    out = []
    drs_zaehler = {"drs_det": 0, "drs_act": 0}
    # DRS-IDs in Fortschrittsreihenfolge der Erkennungszonen wie in der Vorlage (Portimão: ID 1 Gegengerade
    # 0,205, ID 2 Start-Ziel-Gerade 0,879); Paare über die Generator-ID. Eine Zone: ID 1 wie bisher.
    det_sortiert = sorted((z for z in gen if z["Type"] == "drs_det"), key=lambda z: float(z["Start"]))
    drs_id = {str(z["Properties"].get("ID")): i + 1 for i, z in enumerate(det_sortiert)}
    if len(drs_id) != len(det_sortiert):
        drs_id = {}
    for z in gen:
        typ = z["Type"]
        if typ in ("drs_det", "drs_act"):
            # mehrere DRS-Zonen fortlaufend nummerieren (Vorlage: drs_detection_1/2, ID 1/2)
            drs_zaehler[typ] += 1
            n = drs_id.get(str(z["Properties"].get("ID")), drs_zaehler[typ])
            z["Properties"] = {"ID": str(n)}
            z["Name"] = ("drs_detection_%d" if typ == "drs_det" else "drs_activation_%d") % n
        elif typ == "rolling_start_zone":
            # Generatorzone → rollingstart_tt (ID 0); schon nach der Vorlage benannte Zonen bleiben
            if z["Name"] not in namen_alt.get(typ, []):
                z["Name"], z["Properties"] = "rollingstart_tt", {"ID": "0"}
        elif typ == "drl" and z["SplineName"] == pit:
            z["Name"] = "drl_pit"
            # nicht genau 0…1 wie vom Generator, sondern innerhalb der Boxengasse (Original 0,127…0,915)
            z["Start"], z["End"] = max(float(z["Start"]), 0.02), min(float(z["End"]), 0.98)
        elif typ == "safety_car_pit" and z["SplineName"] != hl:
            # Vorlage: safety_car_pit liegt auf der Hauptlinie vor der Boxeneinfahrt
            # (Portimão 0,68…0,86). Generator: auf pit_1. Vorlage gilt (2.11 im Spiel belegt).
            ein = lg["boxengasse"]["einfahrt_main_progress"]
            alt_sc = next(a for a in alt if a["Type"] == "safety_car_pit")
            alt_ein = protokoll["vorlage_boxeneinfahrt_main"]
            z.update(SplineName=hl, Start=(alt_sc["Start"] - alt_ein + ein) % 1.0,
                     End=(alt_sc["End"] - alt_ein + ein) % 1.0)
        elif len(namen_alt.get(typ, [])) == 1:
            z["Name"] = namen_alt[typ][0]
        z["Properties"] = {k: str(v) for k, v in z["Properties"].items()}
        out.append(z)
    gen_typen = {z["Type"] for z in out}
    # Ergänzungen nur, wenn der Generator den Typ nicht schon liefert (AP13: eine Generatorlogik mit allen
    # Vorlagenzonen, z. B. AP8 `spielgenerator`, enthält schon first_corner, Sprach-Trigger, X-Mode …;
    # vorher entstanden dann doppelte Namen mit verschiedenen Bereichen).
    gen_namen = {z["Name"] for z in out}
    gen_hl_typen = {z["Type"] for z in out if z["SplineName"] == hl}
    sek = {z["Type"]: z for z in out if z["Type"].startswith("sector_")}
    # Sprach-Trigger an den Sektoren (in der Vorlage am selben Element wie sector_N)
    if "spch_vo_trig" not in gen_typen:
        for a in alt:
            if a["Type"] == "spch_vo_trig":
                n = a["Name"][-1]
                s = sek.get("sector_%s" % n)
                if s is not None:
                    out.append(_zone(s["Name"], "spch_vo_trig", hl, s["Start"], s["End"], a["Properties"]))
    rs = [z for z in out if z["Type"] == "rolling_start_zone"]
    tt = next((z for z in rs if str(z["Properties"].get("ID", "0")) == "0"), rs[0])
    if "rollingstart_fly" not in gen_namen:
        out.append(_zone("rollingstart_fly", "rolling_start_zone", hl, tt["Start"], tt["End"], {"ID": "1"}))
    if "rollingstart_osq" not in gen_namen:
        out.append(_zone("rollingstart_osq", "rolling_start_zone", hl, tt["Start"], 0.0, {"ID": "2"}))
    # Ohne DRS-Zone: Platzhalter am Start; ERS-Override und X-Mode erhalten unten ohnehin die
    # Meterabstände der Vorlage (_vorlagenabstaende).
    det = next((z for z in out if z["Type"] == "drs_det"), {"Start": 0.0, "End": 0.003})
    act = next((z for z in out if z["Type"] == "drs_act"), {"Start": 0.0, "End": 0.05})
    if "ers_over_det" not in gen_typen:
        out.append(_zone("ers_override_detection_1", "ers_over_det", hl, det["Start"], det["End"], {"ID": "1"}))
    if "ers_over_act" not in gen_typen:
        out.append(_zone("ers_override_activation_1", "ers_over_act", hl, act["Start"],
                         (act["Start"] + 0.003) % 1.0, {"ID": "1"}))
    # erste Kurve: vom letzten Startplatz bis hinter das Ende der ersten Kurve
    grid = [p for p in sp["vehicles_layer"]["placements"] if "gridslot" in p["name"].lower()]
    hinten = min(float(p["progress"]) for p in grid)
    ecken_ende = sorted(gp(l["gate_id"]) for l in ai["corner_end_lines"])
    if "first_corner_zone" not in gen_typen:
        out.append(_zone("first_corner", "first_corner_zone", hl, hinten - 0.01, ecken_ende[0] + 0.01))
    # Geraden (zwischen Kurvenende und nächstem Kurvenanfang): X-Mode auf der DRS-Geraden + nächstlängster
    starts = sorted(gp(l["gate_id"]) for l in ai["corner_start_lines"])
    geraden = []
    for e in ecken_ende:
        s = min((x for x in starts if x > e), default=starts[0] + 1.0)
        geraden.append(((s - e) % 1.0, e, s % 1.0))
    geraden.sort(reverse=True)
    if "x_mode" not in gen_typen:
        out.append(_zone("x_mode_1", "x_mode", hl, act["Start"], act["End"], {"ID": "1", "type": "Full"}))
        zweite = next((g for g in geraden if not (g[1] <= act["Start"] <= g[1] + g[0])), None)
        if zweite is not None:
            out.append(_zone("x_mode_2", "x_mode", hl, zweite[1] + 0.005, zweite[2] - 0.01,
                             {"ID": "2", "type": "Full"}))
    # Speedtrap kurz vor dem ersten Bremspunkt hinter der Ziellinie
    if "spd_trp" not in gen_typen:
        brems = sorted(gp(l["gate_id"]) for l in ai["brake_lines"] if "pit" not in gates[int(l["gate_id"])]["name"])
        st = (brems[0] - 0.012) % 1.0
        out.append(_zone("speed_trap", "spd_trp", hl, st, st + 0.003))
    # Dynamische Ideallinie je Kurve (Anfahrt ~150 m bis Kurvenende). Überlappende Bereiche werden
    # zusammengefasst: Im Original überlappen sich drl-Zonen nie; überlappende Zonen zusammen mit der
    # neuen aispline ließen das Spiel beim Start der fliegenden Runde abstürzen (Spieltest 15.10.2026).
    if "drl" not in gen_hl_typen:
        bereiche = sorted(((s - 150.0 / L) % 1.0, e % 1.0) for s, e in zip(starts, ecken_ende))
        zusammen = []
        for s, e in bereiche:
            if zusammen and s <= zusammen[-1][1] + 0.002:
                zusammen[-1][1] = max(zusammen[-1][1], e)
            else:
                zusammen.append([s, e])
        for i, (s, e) in enumerate(zusammen):
            out.append(_zone("drl_%d" % (i * 10), "drl", hl, s, e))
    gen_typen |= {"spch_vo_trig", "ers_over_det", "ers_over_act", "first_corner_zone", "x_mode", "spd_trp", "drl"}
    # übrige Vorlagentypen
    entfernt = Counter()
    for a in alt:
        if a["Type"] in gen_typen:
            continue
        if a["Type"] == "crowd_excitement_zone":
            entfernt[a["Type"]] += 1
            continue
        if a["SplineName"] == pit:
            a = dict(a, Start=f_pit(a["Start"]), End=f_pit(a["End"]))
        out.append(_zone(a["Name"], a["Type"], a["SplineName"], a["Start"], a["End"], a["Properties"]))
    # Zeitfahr-Anlauf und Startzonen wie in der Vorlage, in Metern relativ zur Ziellinie.
    # Spieltest 15.10.2026: Mit Generatorwerten (rollingstart_tt 300…20 m vor der Linie) lag der aus
    # der Vorlage übernommene ai_flying_lap_trigger außerhalb von rollingstart_tt → Absturz beim
    # Start der fliegenden Runde. In der Vorlage liegt der Trigger innerhalb von rollingstart_tt.
    # Eigene Straight-Mode-Zonen (Definition x_mode.zonen, strecke/zonen.py) behalten ihre Lage.
    eigene_xmode = any(z.get("quelle") == "eingabe" for z in lg.get("x_mode") or [])
    namen = tuple(n for n in ZEITFAHR_ZONEN if not (eigene_xmode and n.startswith("x_mode")))
    protokoll["x_mode_eigene"] = eigene_xmode
    protokoll["zeitfahren_angepasst"] = _vorlagenabstaende(out, alt, original.hauptlinie.laenge, L, hl, namen)
    # gleiche Namen müssen gleiche Bereiche haben (Schreiber prüft)
    bereiche = {}
    for z in out:
        key = (z["SplineName"], round(z["Start"], 9), round(z["End"], 9))
        if bereiche.setdefault(z["Name"], key) != key:
            raise WeltFehler("TM-Name %s mit verschiedenen Bereichen" % z["Name"])
    protokoll["tm"] = {"zonen": len(out), "aus_generator": len(gen), "entfernt": dict(entfernt),
                       "boxenabbildung": [round(f_pit(x), 4) for x in (0.362, 0.445, 0.7227, 0.8614)]}
    return out


# =============================================================================== Bremslinien
def _gate_kruemmung(haupt):
    """|Krümmung| (1/m) der Ideallinie an jedem Hauptgate (Dreipunkt-Kreis durch die Nachbargates, zyklisch).
    Das Spiel liest an Bremslinien mit ``curvature_gate="true"`` die Krümmung der Gate-Linie ab."""
    P = np.array([[g["position"][0], g["position"][2]] for g in haupt], dtype=float)
    a, c = np.roll(P, 1, axis=0), np.roll(P, -1, axis=0)
    u, v = P - a, c - P
    kreuz = u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]
    nenner = np.linalg.norm(u, axis=1) * np.linalg.norm(v, axis=1) * np.linalg.norm(c - a, axis=1)
    return np.abs(2 * kreuz / np.maximum(nenner, 1e-12))


#: Bremslinie am frühesten Kurven-Gate mit mindestens diesem Anteil der Scheitelkrümmung (Portimão-Original:
#: Bremslinie 0–20 m vor dem Scheitel, Radius dort im Mittel 4 % über dem Scheitelradius).
BREMSLINIE_SCHEITEL_ANTEIL = 0.92


def _bremslinien_in_kurven(ai, anzahl, min_abstand, protokoll):
    """``anzahl`` Bremslinien-Gates der Hauptroute, jedes innerhalb einer Kurve [Anfang, Ende], kurz vor dem Scheitel.

    Kurven = Generator-``corner_start_lines`` mit dem jeweils nächsten ``corner_end_line`` (zyklisch). Je Kurve
    das früheste Gate nach dem Anfang, dessen Gate-Krümmung ≥ ``BREMSLINIE_SCHEITEL_ANTEIL`` × Scheitelkrümmung
    ist (Original-Regel, 25.10.2026; vorher fest Anfang + 5 Gates → teils 100 m vor oder 30 m hinter dem
    Scheitel, das Spiel las dort eine falsche Kurvenkrümmung). Reihenfolge: Kurven mit Generator-Bremslinie
    davor, dann übrige, dann zweite Linien in langen Kurven (nächster Krümmungsgipfel)."""
    haupt = sorted((g for g in ai["gates"] if "pit" not in g["name"]), key=lambda g: float(g["progress"]))
    idx = {int(g["id"]): i for i, g in enumerate(haupt)}
    n = len(haupt)
    starts = sorted(idx[int(l["gate_id"])] for l in ai["corner_start_lines"] if int(l["gate_id"]) in idx)
    ends = sorted(idx[int(l["gate_id"])] for l in ai["corner_end_lines"] if int(l["gate_id"]) in idx)
    if not starts or not ends:
        raise WeltFehler("keine Kurvenlinien für Bremslinien")
    kurven = []
    for s in starts:
        e = next((x for x in ends if x > s), ends[0] + n)   # zyklisch über die Ziellinie
        kurven.append((s, e))
    gen = sorted(idx[int(l["gate_id"])] for l in ai["brake_lines"] if int(l["gate_id"]) in idx)
    k = _gate_kruemmung(haupt)

    def mit_bremse(kv):
        s, _ = kv
        return any(0 <= (s - b) % n <= 15 for b in gen)

    def abstand_ok(i, belegt):
        return all(min((i - b) % n, (b - i) % n) >= min_abstand for b in belegt)

    def kandidaten(s, e):
        """Gates der Kurve (ohne Anfang) in Wunschreihenfolge: früheste mit ≥ Anteil der Scheitelkrümmung,
        dann nach Nähe zum Scheitel."""
        innen = list(range(s + 1, e + 1)) or [s]
        scheitel = max(innen, key=lambda i: k[i % n])
        schwelle = BREMSLINIE_SCHEITEL_ANTEIL * k[scheitel % n]
        erstes = next(i for i in innen if k[i % n] >= schwelle)
        return [erstes] + sorted((i for i in innen if i != erstes), key=lambda i: (abs(i - scheitel), i > scheitel))

    belegt, lage = [], []
    reihen = [kv for kv in kurven if mit_bremse(kv)] + [kv for kv in kurven if not mit_bremse(kv)]
    for s, e in reihen:
        if len(belegt) >= anzahl:
            break
        if e - s < 1:
            continue
        for i in kandidaten(s, e):
            if abstand_ok(i % n, belegt):
                belegt.append(i % n)
                scheitel = max(range(s + 1, e + 1), key=lambda j: k[j % n])
                lage.append({"gate": int(haupt[i % n]["id"]), "gates_vor_scheitel": scheitel - i,
                             "radius_m": round(1 / max(k[i % n], 1e-9), 1),
                             "scheitel_radius_m": round(1 / max(k[scheitel % n], 1e-9), 1)})
                break
    # weitere Linien in den längsten Kurven, stärkste noch freie Krümmung, reihum bis alle IDs vergeben sind
    # (ein Ring hat nur wenige Kurven, der Träger verlangt trotzdem alle Bremslinien)
    vorher = -1
    while vorher != len(belegt) < anzahl:
        vorher = len(belegt)
        for s, e in sorted(kurven, key=lambda kv: -(kv[1] - kv[0])):
            if len(belegt) >= anzahl:
                break
            frei = [i for i in range(s + 1, e + 1) if abstand_ok(i % n, belegt)]
            if frei:
                belegt.append(max(frei, key=lambda i: k[i % n]) % n)
    if len(belegt) < anzahl:
        raise WeltFehler("zu wenige Kurven-Gates für %d Bremslinien" % anzahl)
    protokoll["bremslinien_kurven"] = {"kurven": len(kurven), "mit_generator_bremse": sum(map(mit_bremse, kurven)),
                                       "regel": "frühestes Gate mit ≥ %.0f %% der Scheitelkrümmung"
                                                % (100 * BREMSLINIE_SCHEITEL_ANTEIL), "lage": lage}
    return [int(haupt[i]["id"]) for i in belegt]


def kurven_begrenzen(lg, original, protokoll):
    """Höchstens so viele Hauptkurven wie der Träger Haupt-Bremslinien-IDs hat (Portimão 15).

    In allen 28 Originalstrecken gilt Haupt-Bremslinien ≥ Hauptkurven (jede Kurve hat eine Bremslinie;
    Monaco fasst 19 Kurven zu 15 Kurvenlinien zusammen). Die Editor-Strecke 16.10.2026 hatte 16 Kurven bei
    15 IDs → Kurve ohne Bremslinie, ortsgebundener Absturz. Zusammengelegt werden jeweils die beiden
    Kurven mit der kürzesten Geraden dazwischen (Ende der einen und Anfang der nächsten Linie entfallen).
    Kurven-IDs danach fortlaufend in Fortschrittsreihenfolge, Box-Kurven anschließend."""
    ai = lg["spielformate"]["AISplineData"]
    gates = {int(g["id"]): g for g in ai["gates"]}
    grenze = sum(1 for l in original.linien["brake_lines"] if not original.gate(int(l["gate_id"])).boxengasse)

    def haupt(l):
        return "pit" not in gates[int(l["gate_id"])]["name"]

    def prog(l):
        return float(gates[int(l["gate_id"])]["progress"])
    cs = sorted((l for l in ai["corner_start_lines"] if haupt(l)), key=prog)
    ce = sorted((l for l in ai["corner_end_lines"] if haupt(l)), key=prog)
    vorher = len(cs)
    zusammen = []
    while len(cs) > grenze and len(cs) > 1:
        # Kurve k = [cs[k], erstes Ende danach]; Gerade bis zum nächsten Anfang
        paare = []
        for k, s in enumerate(cs):
            e = min(ce, key=lambda x: (prog(x) - prog(s)) % 1.0)
            n = cs[(k + 1) % len(cs)]
            paare.append(((prog(n) - prog(e)) % 1.0, k, e, n))
        gerade, k, e, n = min(paare, key=lambda p: p[0])
        ce.remove(e)
        cs.remove(n)
        zusammen.append({"gerade_fortschritt": round(gerade, 5), "ende_gate": int(e["gate_id"]),
                         "anfang_gate": int(n["gate_id"])})
    if zusammen:
        box_s = [l for l in ai["corner_start_lines"] if not haupt(l)]
        box_e = [l for l in ai["corner_end_lines"] if not haupt(l)]
        for i, (s, e) in enumerate(zip(cs, sorted(ce, key=lambda x: (prog(x) - prog(cs[0])) % 1.0))):
            s["id"] = e["id"] = i
        for j, l in enumerate(sorted(box_s, key=lambda l: int(l["id"]))):
            l["id"] = len(cs) + j
        for j, l in enumerate(sorted(box_e, key=lambda l: int(l["id"]))):
            l["id"] = len(cs) + j
        ai["corner_start_lines"] = sorted(cs + box_s, key=lambda l: int(l["id"]))
        ai["corner_end_lines"] = sorted(ce + box_e, key=lambda l: int(l["id"]))
    protokoll["kurven_begrenzt"] = {"vorher": vorher, "nachher": len(cs), "grenze_bremslinien": grenze,
                                    "zusammengelegt": zusammen}
    return protokoll["kurven_begrenzt"]


def bremslinien_zuordnen(lg, original, tm_neu, protokoll):
    """Bremslinien mit genau dem ID-Satz der ``.brakelinesettings`` (Portimão: 15 Haupt + 3 Box).

    Haupt-IDs in Fortschrittsreihenfolge auf Gates innerhalb der Generator-Kurven
    (``_bremslinien_in_kurven``, Regel aus allen 30 Originalstrecken). Box-IDs über die Boxen-Ankerabbildung (910 = Limiterbremsung,
    max_speed 22,222 m/s, liegt vor dem Limiter)."""
    ai = lg["spielformate"]["AISplineData"]
    gates = {int(g["id"]): g for g in ai["gates"]}
    alt_ids = []
    for l in original.linien["brake_lines"]:
        g = original.gate(int(l["gate_id"]))
        alt_ids.append((g.boxengasse, int(l["id"])))
    haupt_ids = sorted(i for b, i in alt_ids if not b)
    box_ids = sorted(i for b, i in alt_ids if b)
    # Spieltest 15.10.2026: Zwei Bremslinien auf demselben Gate (Doppelkurve im Generator) bzw. im
    # Abstand von 2 Gates ließen die KI im Zeitfahr-Anlauf nicht bremsen und das Spiel abstürzen.
    # Deshalb: Bremslinien-Gates paarweise mindestens MIN_ABSTAND Gates auseinander, IDs aufsteigend
    # in Fortschrittsreihenfolge wie im Original.
    MIN_ABSTAND = BREMSLINIEN_MIN_ABSTAND_GATES  # Prüfung KI_BREMSLINIEN_ABSTAND (pruefregeln)

    def prog(gid):
        return float(gates[int(gid)]["progress"])

    # Spieltest 15.10.2026 (Tauschpaket aispline_wie_211 läuft): In allen 30 Originalstrecken liegt
    # jede Bremslinie der Hauptroute INNERHALB einer Kurve (corner_start ≤ Gate ≤ nächstes corner_end,
    # typisch 3–7 Gates nach dem Kurvenanfang). Die Generator-Bremslinien liegen VOR den Kurven →
    # Absturz, sobald ein Auto die erste solche Linie erreicht. Daher: Bremslinien in die Kurven legen.
    belegt = _bremslinien_in_kurven(ai, len(haupt_ids), MIN_ABSTAND, protokoll)
    linien = [{"id": i, "gate_id": g} for i, g in zip(haupt_ids, sorted(belegt, key=prog))]
    # Boxen-IDs
    alt = [{"Name": z.name, "Type": z.typ, "Start": z.start, "End": z.ende} for z in original.zonen]
    f_pit = _pit_abbildung(alt, lg["spielformate"]["TM"]["markup"])
    pitgates = [g for g in ai["gates"] if "pit" in g["name"]]
    pitspline = next(s for s in original.splines.values() if not s.geschlossen)
    for l in original.linien["brake_lines"]:
        g = original.gate(int(l["gate_id"]))
        if not g.boxengasse:
            continue
        f_alt = pitspline.fortschritt_von(g.position)[0]
        f_neu = f_pit(f_alt)
        ziel = min(pitgates, key=lambda x: abs(float(x["progress"]) - f_neu))
        linien.append({"id": int(l["id"]), "gate_id": int(ziel["id"])})
    ai["brake_lines"] = sorted(linien, key=lambda l: l["id"])
    protokoll["bremslinien"] = [{"id": l["id"], "gate": l["gate_id"],
                                 "fortschritt": round(float(gates[l["gate_id"]]["progress"]), 4),
                                 "box": "pit" in gates[l["gate_id"]]["name"]} for l in ai["brake_lines"]]
    if {l["id"] for l in linien} != set(haupt_ids) | set(box_ids):
        raise WeltFehler("Bremslinien-ID-Satz unvollständig")
    return ai["brake_lines"]


# =============================================================================== Fahrzeugplätze
def _rahmen(m):
    M = np.asarray(m, float).reshape(4, 4)
    return M[3, :3].copy(), M[:3, :3].copy()


def _matrix(pos, R):
    M = np.eye(4)
    M[:3, :3] = R
    M[3, :3] = pos
    return [float(v) for v in M.ravel()]


def _nummer(name):
    m = streckenlogik.PLATZ_RE.search(name or "")
    return (m.group(1).lower(), int(m.group(2))) if m else (None, None)


#: Anker der Garagenszene quer zur Box Richtung Garage (Originale: 1,7–10,8 m)
F1GARAGE_QUER_M = 6.5


def plaetze_berechnen(lg, zustand, protokoll):
    """Neue Matrizen für alle Fahrzeugplätze der vier Platz-Layer.

    Start-/Boxenplätze: Generator. Garagen (PitGarage/F1Garage):

    * mit Garagengebäude (Standard seit 27.10.2026): PitGarage wie die Originale 4,3 m hinter dem Tor im Gebäude,
      quer zur Gasse mit der Front zur Gasse, längs wie im Original (±3,8 m). F1Garage steht mitten im Modul
      (``F1GARAGE_QUER_M``) und wird bei Garagen auf der anderen Seite als in der Vorlage (Portimão: rechts) um 180°
      gedreht. Auf die Lage des Autos in der Cockpitansicht hat F1Garage keinen Einfluss: Sie blieb zwischen W8
      (3 m, ungedreht) und W9 (6,5 m, gedreht) gleich. Das Spiel bezieht Personen und Tische daraus (Spieltest
      27.10.2026). Offen: In W7–W9 stand das Auto für die Cockpitansicht auf der Mauerseite der Box.
    * ohne Gebäude: Platzhalter **in der Boxengasse** an der Box (ohne Querversatz), wie bis 27.10.2026.

    Box 12 und Startplätze 23/24 werden extrapoliert."""
    sp = lg["spielformate"]
    gen = {}
    for p in sp["vehicles_layer"]["placements"]:
        art, n = _nummer(p["name"])
        gen[(art, n)] = p
    boxen = {n: _rahmen(p["matrix"]) for (a, n), p in gen.items() if a == "pitbox"}
    grid = {n: _rahmen(p["matrix"]) for (a, n), p in gen.items() if a == "gridslot"}
    # benötigte Box-/Startplatznummern der Vorlage (Portimão: Boxen bis 12, Startplätze bis 24); fehlende
    # werden extrapoliert, mindestens eine Box und zwei Startplätze (wie bisher)
    box_noetig, grid_noetig = set(), set()
    for d in zustand.plaetze.values():
        for name in d:
            art, n = _nummer(name)
            if art in ("pitbox", "f1garage"):
                box_noetig.add(n)
            elif art == "pitgarage":
                box_noetig.add((n + 1) // 2)
            elif art == "gridslot":
                grid_noetig.add(n)
    nb = max(boxen)
    if nb >= 2:
        d = boxen[nb][0] - boxen[nb - 1][0]
        for k in range(nb + 1, max(box_noetig | {nb + 1}) + 1):
            boxen[k] = (boxen[nb][0] + (k - nb) * d, boxen[nb][1])
    ng = max(grid)
    for k in range(ng + 1, max(grid_noetig | {ng + 2}) + 1):
        p, R = grid[k - 2]
        grid[k] = (p - 16.0 * R[2], R)  # lokale +Z = vorwärts → 16 m zurück (2 Reihen à 8 m)
    # Vorlagenrahmen der Boxen (alle Platz-Layer)
    alt = {}
    for kurz, d in zustand.plaetze.items():
        for name, (m, uid, tpl) in d.items():
            art, n = _nummer(name)
            if art and m is not None:
                alt[(art, n)] = (kurz, name, _rahmen(m), uid)
    ergebnis = defaultdict(dict)   # Kurzname -> {name: matrix}
    abw = []
    gi = lg.get("boxengasse") or {}
    mit_gebaeude = bool(gi.get("gebaeude"))
    # Garagenseite im Boxenrahmen: lokale +X = links (Matrixzeile 0 = -rechts)
    gseite = 1.0 if gi.get("seite", "links") == "links" else -1.0
    tor = float(gi.get("tor_abstand_m", 3.0))
    # Seite der Vorlage: Querversatz ihrer PitGarages zur Box (Portimão: rechts → negativ)
    vq = [((alt[k][2][0] - alt[("pitbox", (k[1] + 1) // 2)][2][0]) @ alt[("pitbox", (k[1] + 1) // 2)][2][1].T)[0]
          for k in alt if k[0] == "pitgarage" and ("pitbox", (k[1] + 1) // 2) in alt]
    spiegeln = bool(vq) and np.sign(np.median(vq)) != gseite
    Y180 = np.diag([-1.0, 1.0, -1.0])            # 180° um die Hochachse (Zeilen = Achsen im Boxenrahmen)
    for (art, n), (kurz, name, (pos, R), uid) in sorted(alt.items()):
        if art == "gridslot":
            if n not in grid:
                raise WeltFehler("kein neuer Startplatz %d" % n)
            ergebnis[kurz][name] = _matrix(*grid[n])
        elif art == "pitbox":
            ergebnis[kurz][name] = _matrix(*boxen[n])
        else:
            box = n if art == "f1garage" else (n + 1) // 2
            if ("pitbox", box) not in alt or box not in boxen:
                raise WeltFehler("keine Box %d für %s" % (box, name))
            bpos, bR = alt[("pitbox", box)][2]
            off = (pos - bpos) @ bR.T
            abw.append(float(np.hypot(off[0], off[2])))
            npos, nR = boxen[box]
            rel = R @ bR.T                       # Drehung relativ zur Box (Vorlage)
            if mit_gebaeude:
                if art == "pitgarage":
                    lokal = np.array([gseite * (tor + 4.3), off[1], off[2]])
                    # Front zur Gasse: lokale +Z = -Garagenseite, +X (links) = oben × vorne
                    vorne = np.array([-gseite, 0.0, 0.0]) @ nR
                    oben_w = nR[1]
                    links_w = np.cross(oben_w, vorne)
                    ergebnis[kurz][name] = _matrix(npos + lokal @ nR, np.array([links_w, oben_w, vorne]))
                else:
                    lokal = np.array([gseite * F1GARAGE_QUER_M, 0.0, 0.0])
                    ergebnis[kurz][name] = _matrix(npos + lokal @ nR, ((rel @ Y180) if spiegeln else rel) @ nR)
            elif art == "pitgarage":
                lokal = np.array([0.0, off[1], off[2]])
                ergebnis[kurz][name] = _matrix(npos + lokal @ nR, nR)
            else:
                lokal = np.array([0.0, off[1], 0.0])
                ergebnis[kurz][name] = _matrix(npos + lokal @ nR, rel @ nR)
    # Safety-Car: hinter der letzten Box in Fahrtrichtung, Ausrichtung wie die Boxen
    letzte = max(boxen)
    d = boxen[letzte][0] - boxen[letzte - 1][0]
    for name in zustand.plaetze.get("safety_car", {}):
        ergebnis["safety_car"][name] = _matrix(boxen[letzte][0] + 1.5 * d, boxen[letzte][1])
    protokoll["plaetze"] = {k: len(v) for k, v in ergebnis.items()}
    protokoll["garagen_vorlage_abstand_zur_box_m"] = [round(min(abw), 2), round(max(abw), 2)] if abw else None
    protokoll["box_extrapoliert"] = [int(letzte)]
    protokoll["garagen"] = {"gebaeude": mit_gebaeude, "seite": gi.get("seite"), "f1garage_gedreht_180": spiegeln}
    return ergebnis


#: Garagen-Layer des Trägers (je Spielmodus einer; Portimão: je Box ein Satz garage_main_a, garage_shadow,
#: Tafel, Teamfarben, Bodenmarkierung, Kommandostand). Keine Physik, keine EventGraph-Verweise (geprüft 29.10.2026).
GARAGEN_LAYER = ("objects_garages_f1", "objects_garages_f1_myteam", "objects_garages_f2", "objects_garages_classic",
                 # Portimão-Mod 1.0.2: eine Ebene je Spielmodus mehr (2026, Story, MyTeam-Vorgänger)
                 "objects_garages_f1_26", "objects_garages_f1_26_myteam", "objects_garages_f1_previous_myteam",
                 "objects_garages_f1_story", "objects_garages_f2_26")
#: Kommandostände an der Boxenmauer (Modellname beginnt so); werden an die neue Mauer gerückt
KOMMANDOSTAND = "pit_perch"
#: Abstand Modellursprung – Rückseite des Stands in Meter (Modellhülle quer −0,71…+0,59). In Portimão steht er auf der
#: Boxengassenseite, die Rückseite zur Mauer (Gasse bis 11,7 m, Stand bei 12,6 m, Mauerzone ab ≈ 13,3 m ab Box)
KOMMANDOSTAND_RUECKSEITE_M = 0.6


def garagen_modell(lg):
    """``"traeger"`` (Portimãos Garagenmodule versetzen) oder ``"generiert"`` aus der Logik (Standard: generiert)."""
    gi = lg.get("boxengasse") or {}
    return gi.get("gebaeude_modell", "generiert") if gi.get("gebaeude") else "keine"


def garagen_versetzen(archiv, zustand, lg, protokoll=None):
    """Versetzt Portimãos Garagenmodule Box für Box an die neue Boxengasse (29.10.2026).

    Jede Instanz der ``GARAGEN_LAYER`` gehört zur nächstgelegenen Portimão-Box (``PitBox<n>``; die Module stehen dort
    im Box-Ursprung). Ihre Lage relativ zu dieser Box wird auf die neue Box ``n`` übertragen. Liegen die Garagen auf
    der anderen Seite als in Portimão (rechts), wird der Boxrahmen um 180° um die Hochachse gedreht (wie F1Garage in
    ``plaetze_berechnen``). Kommandostände (``pit_perch_*``) rücken quer an die neue Boxenmauer (Innenseite, Boxengassenseite).
    Boxen über die neue Anzahl hinaus werden mit dem letzten Abstand fortgesetzt. Muss nach ``logik_vorbereiten``
    laufen; die Layer dürfen vorher nicht geleert werden (Regel BEHALTEN)."""
    sp = lg["spielformate"]
    gi = lg.get("boxengasse") or {}
    neu = {}
    for p in sp["vehicles_layer"]["placements"]:
        art, n = _nummer(p["name"])
        if art == "pitbox":
            neu[n] = _rahmen(p["matrix"])
    alt, garagen_alt = {}, {}
    for d in zustand.plaetze.values():
        for name, (m, uid, tpl) in d.items():
            art, n = _nummer(name)
            if m is None:
                continue
            if art == "pitbox":
                alt[n] = _rahmen(m)
            elif art == "pitgarage":
                garagen_alt[n] = _rahmen(m)
    if not alt or not neu:
        raise WeltFehler("Garagen versetzen: Boxenplätze fehlen (alt %d, neu %d)" % (len(alt), len(neu)))
    nb = max(neu)
    if nb >= 2:
        d = neu[nb][0] - neu[nb - 1][0]
        for k in range(nb + 1, max(alt) + 1):
            neu[k] = (neu[nb][0] + (k - nb) * d, neu[nb][1])
    gseite = 1.0 if gi.get("seite", "links") == "links" else -1.0
    vq = [((garagen_alt[k][0] - alt[(k + 1) // 2][0]) @ alt[(k + 1) // 2][1].T)[0]
          for k in garagen_alt if (k + 1) // 2 in alt]
    spiegeln = bool(vq) and np.sign(np.median(vq)) != gseite
    Y180 = np.diag([-1.0, 1.0, -1.0])
    # Boxenmauer im Boxrahmen (Seite gegenüber den Garagen): Boxenplatz tor_abstand_m vom Tor, innerer Rand der
    # Gasse bei breite − tor, Mauermitte mauer_abstand_m davor (querprofil.py), Innenseite − halbe Dicke
    tor = float(gi.get("tor_abstand_m", 3.0))
    mauer_innen = float(gi.get("breite", 12.0)) - tor + float(gi.get("mauer_abstand_m", 0.6)) \
        - float(gi.get("mauer_dicke", 0.4)) / 2.0
    stand_ziel = mauer_innen - KOMMANDOSTAND_RUECKSEITE_M
    alt_pos = np.array([alt[k][0] for k in sorted(alt)])
    alt_nr = sorted(alt)
    res = layer_ressourcen(archiv, zustand.code)
    bericht = {"spiegeln": spiegeln, "seite": gi.get("seite"), "kommandostand_quer_m": round(stand_ziel, 2),
               "layer": {}}
    for kurz in GARAGEN_LAYER:
        r = res.get(kurz)
        if r is None:
            continue
        p = pkg.lesen(r.fragments[0].read())
        inst = [i for i in p.instanzen() if i.matrix is not None]
        if not inst:
            continue
        mats = [np.asarray(i.matrix, float).reshape(4, 4) for i in inst]
        nrn = [alt_nr[int(np.argmin(np.linalg.norm(alt_pos[:, (0, 2)] - M[3, (0, 2)], axis=1)))] for M in mats]
        stand_alt = [abs(((M[3, :3] - alt[n][0]) @ alt[n][1].T)[0])
                     for i, M, n in zip(inst, mats, nrn) if KOMMANDOSTAND in (i.modell or "")]
        stand_quer_alt = float(np.median(stand_alt)) if stand_alt else 0.0
        zaehler = Counter()
        for i, M, n in zip(inst, mats, nrn):
            bpos, bR = alt[n]
            if n not in neu:
                raise WeltFehler("Garagen versetzen: keine neue Box %d" % n)
            npos, nR = neu[n]
            lokal = (M[3, :3] - bpos) @ bR.T
            rel = M[:3, :3] @ bR.T                          # Zeilen = Achsen der Instanz im Boxrahmen (mit Maßstab)
            if KOMMANDOSTAND in (i.modell or ""):
                lokal[0] += np.sign(lokal[0]) * (stand_ziel - stand_quer_alt)
                zaehler["kommandostand"] += 1
            if spiegeln:
                lokal = lokal * np.array([-1.0, 1.0, -1.0])
                rel = rel @ Y180
            neu_m = np.eye(4)
            neu_m[:3, :3] = rel @ nR
            neu_m[3, :3] = npos + lokal @ nR
            neu_m[:, 3] = M[:, 3]
            pkg.instanz_matrix_setzen(p, i, [float(v) for v in neu_m.ravel()])
            zaehler["versetzt"] += 1
        p.pruefen()
        r.set_fragment_data(0, pkg.schreiben(p))
        bericht["layer"][kurz] = dict(zaehler, kommandostand_quer_alt_m=round(stand_quer_alt, 2))
    if protokoll is not None:
        protokoll["garagen_versetzt"] = bericht
    return bericht


def schreiber_plaetze(lg, plaetze):
    """Platzliste für ``Schreiber`` (nur vehicles.layer, PitBox11 gehört zum MyTeam-Layer)."""
    gen = {_nummer(p["name"]): p for p in lg["spielformate"]["vehicles_layer"]["placements"]}
    out = []
    for name, m in plaetze.get("vehicles", {}).items():
        art, n = _nummer(name)
        eintrag = {"name": name, "matrix": m, "position": m[12:15]}
        if art in ("gridslot", "pitbox"):
            eintrag["VehPlacement"] = dict(gen[(art, n)].get("VehPlacement", {}))
        elif art == "pitgarage":
            eintrag["VehPlacement"] = {"Group": "Garage", "Id": n}
        out.append(eintrag)
    return out


def platz_layer_setzen(archiv, zustand, plaetze, kurznamen=("vehicles_myteam", "vehicles_current_myteam",
                                                            "safety_car")):
    """Matrizen in den übrigen Platz-Layern setzen (Rotation/Skalierung vollständig neu)."""
    res = layer_ressourcen(archiv, zustand.code)
    anzahl = 0
    for kurz in kurznamen:
        neu = plaetze.get(kurz, {})
        if not neu:
            continue
        r = res[kurz]
        p = pkg.lesen(r.fragments[0].read())
        for inst in p.instanzen():
            if inst.name in neu:
                _struktur_eindeutig(p, inst)
                feld = _pfad(p, inst.block, "overrides", "WorldObject", "transform")
                _tabellenwert(p, feld, struct.pack("<16f", *neu[inst.name]))
                anzahl += 1
        p.pruefen()
        r.set_fragment_data(0, pkg.schreiben(p))
    return anzahl


# =============================================================================== Logik gesamt
def boxenlinie_verlaengern(lg, vor_m=BOXEN_VERLAENGERUNG_VOR_M, nach_m=BOXEN_VERLAENGERUNG_NACH_M, schritt_m=9.0,
                           stetig=False):
    """Verlängert die TrackSpace-Boxenlinie entlang der Hauptlinie vor die Einfahrt und hinter die Ausfahrt.

    Spieltest 15.10.2026: Absturz am Ende der Boxengasse, als das Spiel die Kontrolle übergeben wollte.
    Beim Generator begann die Boxen-Spline genau an der Einfahrtszone (0,0) und endete genau am Ende der
    Ausfahrtszone (1,0). In der Vorlage reicht sie weit darüber hinaus (Portimão: Einfahrtszone ab 0,362,
    Ausfahrtszone bis 0,861). Alle Fortschrittswerte auf der Boxenlinie (TM-Zonen, Boxendaten,
    Gate-Metadaten) werden auf die verlängerte Linie umgerechnet. Gibt (vor_m, nach_m, neue_länge) zurück."""
    sp = lg["spielformate"]
    ts = sp["TrackSpace"]["splines"]
    main = next(s for s in ts if s["closed"])
    pit = next(s for s in ts if not s["closed"])
    M = np.asarray(main["position"], float)
    P = np.asarray(pit["position"], float)
    Mc = np.vstack([M, M[:1]])
    cum = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(Mc, axis=0), axis=1))])
    Lm = cum[-1]

    def s_von(q):
        return cum[int(np.argmin(np.linalg.norm(M - q, axis=1)))]

    def punkt(s):
        s = s % Lm
        i = min(int(np.searchsorted(cum, s, side="right")) - 1, len(cum) - 2)
        t = (s - cum[i]) / max(cum[i + 1] - cum[i], 1e-9)
        return Mc[i] + t * (Mc[i + 1] - Mc[i])

    s0, s1 = s_von(P[0]), s_von(P[-1])
    n_vor, n_nach = int(vor_m // schritt_m), int(nach_m // schritt_m)
    vor = [punkt(s0 - vor_m + k * schritt_m) for k in range(n_vor)]
    nach = [punkt(s1 + (k + 1) * schritt_m) for k in range(n_nach)]
    if stetig:
        # AP21 (Regel aller 30 Originale): Die Boxen-Spline beginnt/endet auf der Hauptmittellinie und
        # schwenkt stetig zur Boxengasse aus. Bisher sprang sie am Übergang Verlängerung → Generator-
        # Spline um ≈ 5 m quer. Querversatz des ersten/letzten Generatorknotens per Kosinusrampe.
        def normale(s):
            t = punkt(s + 1.0) - punkt(s - 1.0)
            t[1] = 0.0
            t /= max(np.linalg.norm(t), 1e-9)
            return np.array([-t[2], 0.0, t[0]])

        q0 = float(np.dot(P[0] - punkt(s0), normale(s0)))
        q1 = float(np.dot(P[-1] - punkt(s1), normale(s1)))
        h0, h1 = P[0][1] - punkt(s0)[1], P[-1][1] - punkt(s1)[1]
        for k in range(n_vor):
            s = s0 - vor_m + k * schritt_m
            w = 0.5 - 0.5 * math.cos(math.pi * (k * schritt_m) / vor_m)
            vor[k] = punkt(s) + w * q0 * normale(s) + np.array([0.0, w * h0, 0.0])
        for k in range(n_nach):
            s = s1 + (k + 1) * schritt_m
            w = 0.5 + 0.5 * math.cos(math.pi * ((k + 1) * schritt_m) / nach_m)
            nach[k] = punkt(s) + w * q1 * normale(s) + np.array([0.0, w * h1, 0.0])
    neu = np.vstack(vor + [P] + nach)
    cn = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(neu, axis=0), axis=1))])
    L = cn[-1]
    a, b = cn[len(vor)], cn[len(vor) + len(P) - 1]

    def um(f):
        return float((a + float(f) * (b - a)) / L)

    pit["position"] = neu.tolist()
    pit["progress"] = (cn / L).tolist()
    pit["laenge_m"] = float(L)
    for z in sp["TM"]["markup"]:
        if z["SplineName"] == pit["name"]:
            z["Start"], z["End"] = um(z["Start"]), um(z["End"])
    box = lg.get("boxengasse") or {}
    for k in ("einfahrt_pit_progress", "ausfahrt_pit_progress"):
        if k in box:
            box[k] = um(box[k])
    if "progress" in box:
        box["progress"] = [um(f) for f in box["progress"]]
    for g in sp["AISplineData"]["gates"]:
        if "pit" in g["name"] and "progress" in g:
            g["progress"] = um(g["progress"])
    return float(a), float(L - b), float(L)


# =============================================================================== AP21 Boxengasse
BOXEN_EINFAHRTSZONEN = ("pit_lane_entry_zone", "pit_ent_osd", "pit_ent", "pit_sc_exempt", "canned_event_wheel_lockup")


def _ts_quer(gate):
    """Mitte, rechte Normale und Abstände (point1…point4) eines TS-Tors (point1 rechts außen … point4 links außen)."""
    p = [np.asarray(gate["point%d" % i], float) for i in range(1, 5)]
    mitte = (p[1] + p[2]) / 2
    n = p[1] - p[2]
    n[1] = 0.0
    n /= max(np.linalg.norm(n), 1e-9)
    return mitte, n, [float(np.dot(q - mitte, n)) for q in p]


def _im_viereck(q, poly):
    inn = False
    for i in range(4):
        x1, z1 = poly[i]
        x2, z2 = poly[(i + 1) % 4]
        if (z1 > q[1]) != (z2 > q[1]) and q[0] < x1 + (q[1] - z1) * (x2 - x1) / (z2 - z1):
            inn = not inn
    return inn


def boxen_korrektur(lg, protokoll, rand_min=2.0, ts_schritt=50.0, aussen_rand=4.0):
    """Boxengasse nach den Regeln aller 30 Originalstrecken (AP21, ``docs/zwischenstand/ap21.md``).

    Voraussetzung: ``boxenlinie_verlaengern(..., stetig=True)``, ``bremslinien_zuordnen`` und ``tm_aufbauen``
    sind gelaufen. Ändert TS, TM (Einfahrtszonen) und AISplineData (Kurven/Bremslinien der Boxenroute):

    - R1: TS-Box-Volumes über die ganze Boxen-Spline (auch die Verlängerungen auf der Hauptfahrbahn);
      Tore quer zur Boxen-Spline mit den Track-Limit-Breiten der Haupt-TS.
    - R2: Haupt-TS außen (point1/point4) auf der Boxenseite höchstens bis zur Mitte zwischen Track Limit und
      Innenrand der Boxengasse (Box-TS innen) → die Boxengasse liegt nicht mehr in den Haupt-Volumes.
    - Einfahrtszonen (``BOXEN_EINFAHRTSZONEN``) beginnen erst, wo die Boxen-Spline ≥ ``rand_min`` m
      außerhalb der Track Limits liegt (Portimão: +33/+77 m nach dem Abzweig).
    - R4/R5: drei Kurven auf der Boxenroute (Abzweig mit 909, Limiterstrecke mit 910 am Kurvenanfang,
      Ausfahrt mit 911) und ``pair_id`` Ausfahrtskurve ↔ erste Streckenkurve nach der Einmündung."""
    sp = lg["spielformate"]
    ts = sp["TrackSpace"]["splines"]
    main = next(s for s in ts if s["closed"])
    pit = next(s for s in ts if not s["closed"])
    P = np.asarray(pit["position"], float)
    cn = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))])
    L = cn[-1]
    listen = sp["TS"]["volume_lists"]
    haupt_vl = next(v for v in listen if v["type"] == "main")
    box_vl = next(v for v in listen if v["type"] == "pit")
    haupt_tore = [v["gates"][0] for v in haupt_vl["volumes"]]
    hq = [_ts_quer(g) for g in haupt_tore]
    hm = np.array([m for m, _, _ in hq])

    def haupt_quer(q):
        i = int(np.argmin(np.linalg.norm((hm - q)[:, [0, 2]], axis=1)))
        return hq[i]

    # --- Abstand der Boxen-Spline zu den Track Limits (> 0 außerhalb)
    M = np.asarray(main["position"], float)

    def rand(q):
        i = int(np.argmin(np.linalg.norm((M - q)[:, [0, 2]], axis=1)))
        a, b = M[i], M[(i + 1) % len(M)]
        t = b - a
        t[1] = 0.0
        t /= max(np.linalg.norm(t), 1e-9)
        n = np.array([-t[2], 0.0, t[0]])
        quer = float(np.dot(q - a, n))
        _, _, d = haupt_quer(q)
        return (quer - d[1]) if quer > 0 else (-quer + d[2])

    r = np.array([rand(q) for q in P])
    aussen = np.where(r >= rand_min)[0]
    f_aus = float(cn[aussen[0]] / L) if len(aussen) else None
    erg = {"rand_min_m": rand_min, "einfahrt_ab_m": None if f_aus is None else round(f_aus * L, 1)}

    # --- R1: TS-Box-Volumes auf den Verlängerungen
    alte = [list(v["gates"]) for v in box_vl["volumes"]]
    erstes, letztes = alte[0][0], alte[-1][1]
    m0, _, _ = _ts_quer(erstes)
    m1, _, _ = _ts_quer(letztes)
    s_erst = float(cn[int(np.argmin(np.linalg.norm((P - m0)[:, [0, 2]], axis=1)))])
    s_letzt = float(cn[int(np.argmin(np.linalg.norm((P - m1)[:, [0, 2]], axis=1)))])

    def tor(s):
        k = min(int(np.searchsorted(cn, s, side="right")) - 1, len(P) - 2)
        t = (s - cn[k]) / max(cn[k + 1] - cn[k], 1e-9)
        c = P[k] + t * (P[k + 1] - P[k])
        tg = P[k + 1] - P[k]
        tg[1] = 0.0
        tg /= max(np.linalg.norm(tg), 1e-9)
        n = np.array([-tg[2], 0.0, tg[0]])
        _, _, d = haupt_quer(c)
        breite_r, breite_l = abs(d[1] - 0.0), abs(d[2])
        # Breiten auf die Track Limits der Hauptstrecke (Haupt-TS) bezogen, symmetrisch um die Boxen-Spline
        h = max(breite_r, breite_l)
        return {"point1": (c + n * (h + aussen_rand)).tolist(), "point2": (c + n * h).tolist(),
                "point3": (c - n * h).tolist(), "point4": (c - n * (h + aussen_rand)).tolist()}

    def stuetzen(a, b):
        n = max(1, int(round((b - a) / ts_schritt)))
        return [a + (b - a) * k / n for k in range(n)]
    vor = [tor(s) for s in stuetzen(0.0, s_erst)] if s_erst > 1.0 else []
    nach = [tor(s) for s in stuetzen(s_letzt, L)][1:] + [tor(L)] if L - s_letzt > 1.0 else []
    kette = vor + [erstes]
    neu = [[a, b] for a, b in zip(kette, kette[1:])] + alte
    kette = [letztes] + nach
    neu += [[a, b] for a, b in zip(kette, kette[1:])]
    box_vl["volumes"] = [{"id": i, "gates": [copy.deepcopy(a), copy.deepcopy(b)]} for i, (a, b) in enumerate(neu)]
    if "numVolumes" in box_vl:
        box_vl["numVolumes"] = len(neu)
    erg["ts_box_volumes"] = [len(alte), len(neu)]

    # --- R2: Haupt-TS außen auf der Boxenseite begrenzen (deterministisch je Tor → geteilte Tore bleiben gleich)
    box_innen = [np.array([np.asarray(a["point2"])[[0, 2]], np.asarray(a["point3"])[[0, 2]],
                           np.asarray(b["point3"])[[0, 2]], np.asarray(b["point2"])[[0, 2]]]) for a, b in alte]
    box_mitten = np.array([q.mean(0) for q in box_innen])
    geaendert = 0
    for v in haupt_vl["volumes"]:
        for g in v["gates"]:
            mitte, n, d = _ts_quer(g)
            for aussen_k, limit_k, vorz in (("point1", 1, 1.0), ("point4", 2, -1.0)):
                lim = abs(d[limit_k])
                aus = abs(d[0 if aussen_k == "point1" else 3])
                treffer = None
                x = lim + 0.5
                while x <= aus:
                    q = (mitte + vorz * n * x)[[0, 2]]
                    nah = np.where(np.linalg.norm(box_mitten - q, axis=1) < 150)[0]
                    if any(_im_viereck(q, box_innen[j]) for j in nah):
                        treffer = x
                        break
                    x += 0.5
                if treffer is None:
                    continue
                neu_d = max(lim + 1.0, (lim + treffer) / 2)
                if neu_d < aus:
                    alt_p = np.asarray(g[aussen_k], float)
                    p = mitte + vorz * n * neu_d
                    p[1] = alt_p[1]
                    g[aussen_k] = p.tolist()
                    geaendert += 1
    erg["haupt_tore_aussen_begrenzt"] = geaendert // 2  # jedes Tor steht zweimal (Ende/Anfang)

    # --- Einfahrtszonen erst außerhalb der Fahrbahn
    zonen = sp["TM"]["markup"]
    lim_on = next(z for z in zonen if z["Type"] == "pit_lim_on" and z["SplineName"] == pit["name"])
    verschoben = {}
    if f_aus is not None and f_aus < float(lim_on["Start"]):
        for z in zonen:
            if z["SplineName"] != pit["name"] or z["Type"] not in BOXEN_EINFAHRTSZONEN:
                continue
            s0, s1 = float(z["Start"]), float(z["End"])
            if s0 >= f_aus:
                continue
            if s1 - f_aus > 0.005:
                z["Start"] = f_aus
            else:
                z["Start"], z["End"] = f_aus, f_aus + (s1 - s0)
            verschoben[z["Name"]] = (round(z["Start"] * L, 1), round(z["End"] * L, 1))
    erg["einfahrtszonen_verschoben_m"] = verschoben

    # --- R4/R5: Kurven und Bremslinien der Boxenroute
    ai = sp["AISplineData"]
    gates = {int(g["id"]): g for g in ai["gates"]}
    box = sorted((g for g in ai["gates"] if "pit" in g["name"]), key=lambda g: float(g["progress"]))
    bp = [float(g["progress"]) for g in box]
    lim_off = next(z for z in zonen if z["Type"] == "pit_lim_on" and z["SplineName"] == pit["name"])

    def idx_bei(f):
        return int(np.argmin([abs(x - f) for x in bp]))
    i_lim0, i_lim1 = idx_bei(float(lim_on["Start"])), idx_bei(float(lim_off["End"]))
    n_box = len(box)
    kurven = [(0, max(2, i_lim0 - 1)), (i_lim0, i_lim1), (min(i_lim1 + 1, n_box - 3), n_box - 1)]
    if not (kurven[0][1] < kurven[1][0] and kurven[1][1] < kurven[2][0]):
        raise WeltFehler("Boxenroute zu kurz für drei Kurven: %s" % (kurven,))
    cs = [l for l in ai["corner_start_lines"] if "pit" not in gates[int(l["gate_id"])]["name"]]
    ce = [l for l in ai["corner_end_lines"] if "pit" not in gates[int(l["gate_id"])]["name"]]
    nid = max(int(l["id"]) for l in cs + ce) + 1
    for k, (a, b) in enumerate(kurven):
        cs.append({"id": nid + k, "gate_id": int(box[a]["id"])})
        ce.append({"id": nid + k, "gate_id": int(box[b]["id"])})
    # pair_id: Ausfahrtskurve ↔ erste Streckenkurve nach der Einmündung (gegenseitig, wie 25/30 Originale)
    aus_main = float(lg.get("boxengasse", {}).get("ausfahrt_main_progress", 0.0))
    haupt_cs = [l for l in cs if "pit" not in gates[int(l["gate_id"])]["name"]]
    ziel = min(haupt_cs, key=lambda l: (float(gates[int(l["gate_id"])]["progress"]) - aus_main) % 1.0)
    ausfahrt = cs[-1]
    ausfahrt["pair_id"] = int(ziel["gate_id"])
    ziel["pair_id"] = int(ausfahrt["gate_id"])
    ai["corner_start_lines"], ai["corner_end_lines"] = cs, ce
    # Box-Bremslinien in die Kurven: 909 Abzweig (Anfang + 2), 910 am Anfang der Limiterkurve, 911 Ausfahrt (+2)
    soll = {909: box[min(kurven[0][0] + 2, kurven[0][1])], 910: box[kurven[1][0]],
            911: box[min(kurven[2][0] + 2, kurven[2][1])]}
    for l in ai["brake_lines"]:
        if int(l["id"]) in soll:
            l["gate_id"] = int(soll[int(l["id"])]["id"])
    erg["box_kurven_gates"] = [(int(box[a]["id"]), int(box[b]["id"])) for a, b in kurven]
    erg["box_kurven_m"] = [(round(bp[a] * L, 1), round(bp[b] * L, 1)) for a, b in kurven]
    erg["box_bremslinien"] = {k: (int(g["id"]), round(float(g["progress"]) * L, 1)) for k, g in soll.items()}
    erg["pair"] = (int(ausfahrt["gate_id"]), int(ziel["gate_id"]))
    protokoll["boxen_korrektur"] = erg
    return erg


def logik_vorbereiten(logik, zustand, logik_dy=0.0, plaetze_dy=0.0, boxen_korrektur_an=False):
    """Neutrale Generatorlogik → platzierte, an den Träger angepasste Logik + Platzmatrizen.

    ``boxen_korrektur_an`` (AP21): stetige Boxen-Spline und ``boxen_korrektur`` (Standard aus = v17/v18)."""
    protokoll = {}
    orig = zustand.schreiber.original
    pit_alt = next(s for s in orig.splines.values() if not s.geschlossen)
    einfahrt = next(z.start for z in orig.zonen if z.typ == "pit_lane_entry_zone")
    protokoll["vorlage_boxeneinfahrt_main"] = orig.hauptlinie.fortschritt_von(pit_alt.punkt(einfahrt)[0])[0]
    lg = logik_platzieren(logik, logik_dy, plaetze_dy)
    protokoll["boxenlinie_verlaengert"] = boxenlinie_verlaengern(lg, stetig=boxen_korrektur_an)
    sp = lg["spielformate"]
    namen_alt = {s.name for s in orig.splines.values()}
    if {s["name"] for s in sp["TrackSpace"]["splines"]} != namen_alt:
        raise WeltFehler("Splinenamen weichen von der Vorlage ab: %s" % namen_alt)
    kurven_begrenzen(lg, orig, protokoll)
    bremslinien_zuordnen(lg, orig, None, protokoll)
    sp["TM"]["markup"] = tm_aufbauen(lg, orig, protokoll)
    if boxen_korrektur_an:
        boxen_korrektur(lg, protokoll)
    plaetze = plaetze_berechnen(lg, zustand, protokoll)
    sp["vehicles_layer"]["placements"] = schreiber_plaetze(lg, plaetze)
    return lg, plaetze, protokoll


def logik_schreiben(archiv, zustand, lg):
    """Sechs Logikressourcen mit dem AP8-Schreiber (Bremsmodus neutral, vollständige TM)."""
    res = zustand.schreiber.schreiben(lg, bremslinien="neutral", tm_rest_behalten=False)
    for kennung, fragmente in res.items():
        r = archiv.find(kennung)
        if r is None or len(r.fragments) != len(fragmente):
            raise WeltFehler("Logikressource fehlt: %s" % kennung)
        for i, raw in enumerate(fragmente):
            if r.fragments[i].read() != raw:
                r.set_fragment_data(i, raw)
    return list(res)


# =============================================================================== PVS / Licht
def pvs_alles_sichtbar(archiv):
    """PVS-Hauptvariante: jede Zelle sieht alle Objekte (``pvs.alles_sichtbar``)."""
    r = archiv.find_all(r"volumedata\.pvsbin$")
    if len(r) != 1:
        raise WeltFehler("erwartet genau eine PVSVisRes, gefunden %d" % len(r))
    r = r[0]
    vis = pvs.vis_lesen(r.fragments[0].read())
    vorher = r.fragments[0].size
    pvs.alles_sichtbar(vis)
    neu = pvs.vis_schreiben(vis)
    r.set_fragment_data(0, neu)
    return {"kennung": r.identifier, "vorher_byte": vorher, "nachher_byte": len(neu), "zellen": len(vis.zellen)}


def licht_platzhalter(archiv, zustand, behaltene_uids, neue_modell_uids):
    """AO-Atlas weiß; neue Lichtkarte: Terrain$PhysicsTerrain auf Schicht 0, Einträge behaltener
    Original-Instanzen unverändert, neue Modell-Instanzen auf ein gemeinsames 32-px-Rechteck."""
    licht.ao_ersetzen(archiv, 255)
    terrain = None
    res = layer_ressourcen(archiv, zustand.code).get("terrain")
    if res is not None:
        for i in pkg.lesen(res.fragments[0].read()).instanzen():
            if i._wert("template") == "PhysicsTerrain":
                terrain = bytes.fromhex(i.uniqueid)
    if terrain is None:
        raise WeltFehler("Terrain$PhysicsTerrain nicht gefunden")
    karte = licht.Lichtkarte()
    karte.hinzufuegen(terrain, 0, 1.0, 0.0, 0.0)
    behalten = 0
    for e in (zustand.lichtkarte.eintraege if zustand.lichtkarte else []):
        if e.uid != terrain and e.uid.hex() in behaltene_uids and e.uid not in karte.nach_uid():
            karte.eintraege.append(licht.LichtkartenEintrag(e.uid, e.schicht, e.groesse, e.u, e.v))
            behalten += 1
    neu = 0
    for uid in neue_modell_uids:
        b = bytes.fromhex(uid)
        if b not in karte.nach_uid():
            karte.hinzufuegen(b, 1, 32 / 4096.0, 0.0, 0.0)
            neu += 1
    r = archiv.find(licht.LML_KENNUNG)
    r.set_fragment_data(0, karte.to_bytes())
    return {"eintraege": len(karte.eintraege), "terrain": terrain.hex(), "original_behalten": behalten,
            "neue_objekte": neu, "vorher": len(zustand.lichtkarte.eintraege) if zustand.lichtkarte else 0,
            "statistik": karte.statistik()}
