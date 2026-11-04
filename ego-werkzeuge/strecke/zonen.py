"""DRS- und Straight-Mode-Zonen (Spiel: ``drs_det``/``drs_act`` bzw. ``x_mode``) aus Eingabe oder Automatik.

Alle Stationen in Metern **ab der Ziellinie in Fahrtrichtung** (0 … L; ``ml.s`` der Mittellinie),
Zonen dürfen über die Ziellinie laufen (Start > Ende).

Befund aus allen 28 Spielstrecken mit 2026er-Fahrzeugen (16.10.2026, Abfrage über ``ego.streckenlogik``):
- Straight Mode = TM-Typ ``x_mode`` auf der Hauptlinie. Je Zone eine ``Full``-Zone ``x_mode_N`` (ID N) und
  meist eine ``Partial``-Zone ``x_mode_N_partial`` mit **gleichem Ende**, die 25–300 m (typisch 50–70 m)
  später beginnt; Partial-IDs werden nach den Full-IDs fortlaufend vergeben. 2–5 Zonen je Strecke,
  Monaco keine. Einige Strecken haben zusätzlich Boxengassen-Varianten (``x_mode_1_pit``) – hier nicht.
- DRS (2025er Fahrzeuge) ist davon unabhängig: Erkennungspunkt ``drs_det`` + Aktivierung ``drs_act`` je ID.

Ohne eigene Straight-Mode-Zonen setzt ``spiel_welt.tm_aufbauen`` wie bisher ``x_mode_1/2`` auf die
Meterabstände des Portimão-Trägers (``XMODE_AUTOMATIK_M``); mit eigenen Zonen gelten diese.
"""

from typing import Dict, List

import numpy as np

from .mittellinie import laeufe

#: Straight-Mode-Zonen des Portimão-Trägers (Meter zur Ziellinie; negativ = davor), Automatik ohne Eingabe.
XMODE_AUTOMATIK_M = ((-246.41, 446.34), (1148.39, 1520.33))
#: Standardversatz der Partial-Zone hinter dem Beginn der Full-Zone (Median der Originale ≈ 60 m).
PARTIAL_VERSATZ_M = 60.0
MIN_ZONE_M = 50.0


def _bogen(a: float, b: float, L: float) -> float:
    """Länge von a nach b in Fahrtrichtung (zyklisch)."""
    return (b - a) % L


def drs_zonen(ml, param) -> List[Dict]:
    """DRS-Zonen: eigene (``drs.zonen``) oder automatisch auf den längsten Geraden (bisheriges Verfahren)."""
    L = float(ml.laenge)
    pd = param["drs"]
    eigene = pd.get("zonen") or []
    zonen = []
    if eigene:
        for i, z in enumerate(eigene):
            det, anf, ende = (float(z["erkennung_m"]) % L, float(z["aktivierung_m"]) % L, float(z["ende_m"]) % L)
            zonen.append({"ID": i, "erkennung_s_m": det, "aktivierung_s_m": anf, "ende_s_m": ende,
                          "laenge_m": _bogen(anf, ende, L), "quelle": "eingabe"})
        return zonen
    gerade = np.abs(ml.kappa_glatt) <= pd["kruemmung_gerade"]
    kandidaten = [(a, e) for a, e in laeufe(gerade) if (e - a) * ml.ds >= pd["mindestlaenge_m"]]
    kandidaten.sort(key=lambda x: x[1] - x[0], reverse=True)
    for i, (a, e) in enumerate(kandidaten[:pd["anzahl"]]):
        anf = a * ml.ds + pd["aktivierung_nach_m"]
        ende = (e - 1) * ml.ds - pd["ende_vor_m"]
        if ende <= anf:
            continue
        det = a * ml.ds - pd["erkennung_vor_m"]
        zonen.append({"ID": i, "erkennung_s_m": det % L, "aktivierung_s_m": anf % L, "ende_s_m": ende % L,
                      "laenge_m": ende - anf, "quelle": "automatik"})
    return zonen


def xmode_zonen(ml, param) -> List[Dict]:
    """Eigene Straight-Mode-Zonen (``x_mode.zonen``), sortiert nach Beginn ab Ziel; leer = Automatik im Bau."""
    L = float(ml.laenge)
    zonen = []
    for z in (param.get("x_mode") or {}).get("zonen") or []:
        von, bis = float(z["von_m"]) % L, float(z["bis_m"]) % L
        versatz = z.get("partial_versatz_m", PARTIAL_VERSATZ_M)
        if versatz is None or z.get("partial") is False:    # null oder partial=false: ohne Partial-Zone
            versatz = None
        laenge = _bogen(von, bis, L)
        partial = None
        if versatz is not None and 0 <= float(versatz) < laenge - 1.0:
            partial = (von + float(versatz)) % L
        zonen.append({"von_s_m": von, "bis_s_m": bis, "partial_s_m": partial, "laenge_m": laenge,
                      "quelle": "eingabe"})
    zonen.sort(key=lambda z: z["von_s_m"])
    for i, z in enumerate(zonen):
        z["ID"] = i + 1
    return zonen


def xmode_automatik(L: float) -> List[Dict]:
    """Die Zonen, die der Bau ohne eigene Eingabe setzt (Portimão-Meterabstände), für Anzeige/Prüfung."""
    return [{"ID": i + 1, "von_s_m": a % L, "bis_s_m": b % L, "partial_s_m": None,
             "laenge_m": _bogen(a % L, b % L, L), "quelle": "automatik"} for i, (a, b) in enumerate(XMODE_AUTOMATIK_M)]


def xmode_markup(zonen, fort) -> List[Dict]:
    """TM-Einträge (Full + Partial) wie in den Originalen."""
    out = []
    n = len(zonen)
    teil = 0
    for z in zonen:
        out.append({"Name": "x_mode_%d" % z["ID"], "Start": float(fort.an(z["von_s_m"])),
                    "End": float(fort.an(z["bis_s_m"])), "SplineName": "maintrack", "Type": "x_mode",
                    "Properties": {"ID": z["ID"], "type": "Full"}})
    for z in zonen:
        if z["partial_s_m"] is None:
            continue
        teil += 1
        out.append({"Name": "x_mode_%d_partial" % z["ID"], "Start": float(fort.an(z["partial_s_m"])),
                    "End": float(fort.an(z["bis_s_m"])), "SplineName": "maintrack", "Type": "x_mode",
                    "Properties": {"ID": n + teil, "type": "Partial"}})
    return out


def eingabe_pruefen(param) -> None:
    """Formprüfung der Eingabelisten (wirft ValueError, Teil von ``definition._parameter_pruefen``)."""
    for gruppe, felder in (("drs", ("erkennung_m", "aktivierung_m", "ende_m")), ("x_mode", ("von_m", "bis_m"))):
        zonen = (param.get(gruppe) or {}).get("zonen") or []
        if not isinstance(zonen, list):
            raise ValueError("%s.zonen must be a list" % gruppe)
        for i, z in enumerate(zonen):
            if not isinstance(z, dict):
                raise ValueError("%s.zonen[%d] must be an object" % (gruppe, i))
            for f in felder:
                v = z.get(f)
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not np.isfinite(v):
                    raise ValueError("%s.zonen[%d].%s is missing or not a number" % (gruppe, i, f))
            if gruppe == "x_mode" and not isinstance(z.get("partial", True), bool):
                raise ValueError("x_mode.zonen[%d].partial must be true or false" % i)
            if gruppe == "x_mode" and z.get("partial_versatz_m") is not None:
                v = z["partial_versatz_m"]
                if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                    raise ValueError("x_mode.zonen[%d].partial_versatz_m must be ≥ 0 (empty = no partial)" % i)
