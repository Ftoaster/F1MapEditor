"""Renndaten aus der Strecke: Runden, Kraftstoff, Reifen und KI-Rundenzeiten für ``activation`` im Manifest.

Die Regeln stammen aus der Aktivierung der Portimão-Mod 1.0.2 (4.653 m) und treffen deren Werte: Rennlängen 305/100 km (F1)
und 170/120 km (F2), Kraftstoff je Kilometer, Ladung = Verbrauch je Runde × (Runden + 2,5). Die KI-Zeiten skalieren die
Portimão-Staffel mit der geschätzten Rundenzeit: Der Fahrzeugsatz ``f1_25_ki_portimao`` trifft dort 75,2 s (``ggv_test.md``),
genau den Stufenwert der Mod. Die Reifenwahl ist eine Schätzung ohne Spielbeleg.

Dazu Werte, die sonst von der Vorlage stammen (docs/hooks_untersuchung.md): ``lb_min_time`` (Mindestzeit für Bestenlisten,
bei den Spielstrecken 0,93–0,96 × Polezeit, Zandvoort 66,1 s) und ``TrackPitData`` (Tempolimit und Zeitverlust eines
Boxenstopps; die Spielstrecken haben 60 oder 80 km/h und 19–36 s, nass meist 5 s mehr).
"""

import math

from . import spiel_ki

RENNLAENGE_KM = {"f1": 305.0, "f1_sprint": 100.0, "f2": 170.0, "f2_sprint": 120.0}
#: Verbrauch in kg/km (Mod: 1,55 / 1,06 / 2,03 kg je Runde auf 4,653 km; Strecke gesamt 1,59)
VERBRAUCH_KG_KM = {"f1": 1.55 / 4.653, "f1_2026": 1.06 / 4.653, "f2": 2.03 / 4.653}
VERBRAUCH_STRECKE_KG_KM = 1.59 / 4.653
RESERVE_RUNDEN = 2.5
LADUNG_MAX_KG = 110.0                 # Reglement F1 2025, die anderen Klassen im Verhältnis ihres Verbrauchs
RUNDEN_STUFEN = (1, 2, 3, 5, 9, 10, 15, 20)
PROZENT_STUFEN = (25, 35, 50, 100)

#: Datensätze je Klasse: (Datensätze, Verbrauch, Rennen, Sprint oder None)
KLASSEN = (
    (("f1", "f1_2023", "f1_2024", "f1_2025", "classic", "multiclass"), "f1", "f1", None),
    (("f1_2026",), "f1_2026", "f1", None),
    (("sprint_f1_2023", "sprint_f1_2024", "sprint_f1_2025", "sprint_multiclass"), "f1", "f1", "f1_sprint"),
    (("sprint_f1_2026",), "f1_2026", "f1", "f1_sprint"),
    (("f2", "f2_2023", "f2_2024", "f2_2025", "f2_2026"), "f2", "f2", None),
    (("f2_2025_sprint", "f2_2026_sprint"), "f2", "f2", "f2_sprint"),
)
REIFEN_DATENSAETZE = ["f1_2025", "sprint_f1_2025", "f1_2026", "sprint_f1_2026"]
#: Mittlere Querbeschleunigung über die Zeit (m/s²) → (härteste, mittlere, weichste Mischung), größte Belastung zuerst
REIFEN_STUFEN = ((16.0, ("C1", "C2", "C3")), (13.0, ("C2", "C3", "C4")), (0.0, ("C3", "C4", "C5")))
#: Mindestzeit für Bestenlisten relativ zur Modellrunde; unter den 0,93 der Spielstrecken, weil das Fahrzeugmodell abweicht
MINDESTZEIT_ANTEIL = 0.85
#: TrackPitData nur in diesen Datensätzen (die übrigen erben sie, geprüft an Zandvoort)
BOXEN_DATENSAETZE = ["f1", "f2"]
STANDZEIT_S = 2.5                     # Reifenwechsel
BREMS_ANFAHR_S = 3.0                  # Verzögern vor und Beschleunigen nach dem Tempolimit
BOXENZEIT_GRENZEN_S = (15.0, 40.0)
BOXENZEIT_NASS_PLUS_S = 5.0

#: KI-Zeit je Schwierigkeitsstufe relativ zur Stufe 1,0 (Portimão-Staffel der Mod, 75,2076 s; F2 85,9804 s)
STAFFEL_F1 = (85.7753, 83.704, 81.2043, 79.1973, 77.088, 76.2436, 75.2076, 74.7959, 74.3843, 73.9727)
STAFFEL_F2 = (97.9586, 95.4692, 92.9319, 90.6285, 88.4942, 87.2334, 85.9804, 85.4703, 84.9602, 84.4501)
ZEIT_BEZUG_S = 75.2076
PAKETE_EINZELWERT = ("f1_2023_pro_vehicle_package", "f1_2024_beta_vehicle_package", "f1_classic_vehicle_package",
                     "f1_safetycar_package")
PAKETE_F1 = ("f1_2023_story_vehicle_package", "f1_2023_vehicle_package", "f1_2024_equal_performance_vehicle_package",
             "f1_2024_story_vehicle_package", "f1_2024_vehicle_package", "f1_2024_world_vehicle_package",
             "f1_2025_equal_performance_vehicle_package", "f1_2025_story_vehicle_package", "f1_2025_vehicle_package",
             "f1_2025_world_vehicle_package", "f1_2026_equal_performance_vehicle_package", "f1_2026_vehicle_package")
PAKETE_F2 = ("f2_2022_story_vehicle_package", "f2_2022_vehicle_package", "f2_2023_vehicle_package",
             "f2_2024_vehicle_package", "f2_2025_vehicle_package", "f2_2026_vehicle_package")


def runden(laenge_m, klasse):
    """Kleinste Rundenzahl, die die Renndistanz der Klasse erreicht."""
    return max(1, math.ceil(RENNLAENGE_KM[klasse] * 1000.0 / laenge_m - 1e-9))


def ladung(verbrauch, runden_anzahl, kg_km):
    grenze = LADUNG_MAX_KG * kg_km / VERBRAUCH_KG_KM["f1"]
    return round(min(verbrauch * (runden_anzahl + RESERVE_RUNDEN), grenze), 1)


def reifen_waehlen(a_quer_mittel):
    return next(m for grenze, m in REIFEN_STUFEN if a_quer_mittel >= grenze)


def _zeiten(staffel, faktor):
    return ",".join("%.4f" % (t * faktor) for t in staffel)


def boxenzeit(laenge_m, rundenzeit_s, box):
    """Zeitverlust eines Boxenstopps (s): Weg durch die Gasse mit Tempolimit statt mit mittlerem Rundentempo auf der
    Strecke zwischen Ein- und Ausfahrt, dazu Standzeit sowie Bremsen und Anfahren; begrenzt auf die Spanne der Spielstrecken."""
    v_box = float(box["tempolimit_kmh"]) / 3.6
    strecke_m = ((float(box["ausfahrt_main_progress"]) - float(box["einfahrt_main_progress"])) % 1.0) * laenge_m
    verlust = float(box["laenge_m"]) / v_box - strecke_m / (laenge_m / rundenzeit_s) + STANDZEIT_S + BREMS_ANFAHR_S
    return round(min(max(verlust, BOXENZEIT_GRENZEN_S[0]), BOXENZEIT_GRENZEN_S[1]), 1)


def berechnen(laenge_m, rundenzeit_s, a_quer_mittel, box=None):
    """``{"overrides": […], "track_overrides": […], "bericht": {…}}`` im Format von ``activation``. ``box``: Boxengasse der
    Logik (``laenge_m``, ``tempolimit_kmh``, ``einfahrt_main_progress``, ``ausfahrt_main_progress``) oder None."""
    laenge_km = laenge_m / 1000.0
    overrides = []

    def setzen(datensaetze, pfad, wert):
        overrides.append({"datasets": list(datensaetze), "path": pfad, "value": str(wert)})

    for datensaetze, art, rennen, sprint in KLASSEN:
        n = runden(laenge_m, rennen)
        kg_km = VERBRAUCH_KG_KM[art]
        je_runde = round(kg_km * laenge_km, 2)
        setzen(datensaetze, "TrackGeneralData_0.fuel_burn_per_lap", je_runde)
        setzen(datensaetze, "TrackGeneralData_0.number_of_laps", "%d,%d,%d" % (runden(laenge_m, sprint) if sprint else n, n, n))
        kurz = ladung(je_runde, 1, kg_km)
        for feld in ("full", "one_shot", "short"):
            setzen(datensaetze, "TrackFuelSettings_0.Qualifying." + feld, kurz)
        for k in RUNDEN_STUFEN:
            setzen(datensaetze, "TrackFuelSettings_0.RaceFuelSettings_0.laps_%d" % k, ladung(je_runde, k, kg_km))
        for p in PROZENT_STUFEN:
            setzen(datensaetze, "TrackFuelSettings_0.RaceFuelSettings_0.percent_%d" % p,
                   ladung(je_runde, math.floor(n * p / 100.0 + 0.5), kg_km))
    reifen = reifen_waehlen(a_quer_mittel)
    for feld, mischung in zip(("compound_harder", "compound_medium", "compound_softer"), reifen):
        setzen(REIFEN_DATENSAETZE, "TyreData_0." + feld, mischung)
    bericht = {}
    if box:
        verlust = boxenzeit(laenge_m, rundenzeit_s, box)
        setzen(BOXEN_DATENSAETZE, "TrackPitData_0.pitSpeedLimit", "%g" % float(box["tempolimit_kmh"]))
        setzen(BOXEN_DATENSAETZE, "TrackPitData_0.pitStopTime_Dry", "%g" % verlust)
        setzen(BOXEN_DATENSAETZE, "TrackPitData_0.pitStopTime_Wet", "%g" % (verlust + BOXENZEIT_NASS_PLUS_S))
        bericht.update(tempolimit_kmh=float(box["tempolimit_kmh"]), boxenzeit_s=verlust)
    faktor = rundenzeit_s / ZEIT_BEZUG_S
    mindestzeit = round(MINDESTZEIT_ANTEIL * rundenzeit_s, 3)
    streckenwerte = [{"path": "fuel_burn_per_lap", "value": "%.2f" % (VERBRAUCH_STRECKE_KG_KM * laenge_km)},
                     {"path": "lb_min_time", "value": "%g" % mindestzeit}]
    for pakete, wert in ((PAKETE_EINZELWERT, "%.4f" % rundenzeit_s), (PAKETE_F1, _zeiten(STAFFEL_F1, faktor)),
                         (PAKETE_F2, _zeiten(STAFFEL_F2, faktor))):
        streckenwerte += [{"path": "ai_track_difficulties.%s.time" % p, "value": wert} for p in pakete]
    return {"overrides": overrides, "track_overrides": streckenwerte,
            "bericht": {"laenge_m": laenge_m, "rundenzeit_s": round(rundenzeit_s, 3), "a_quer_mittel": round(a_quer_mittel, 2),
                        "runden_f1": runden(laenge_m, "f1"), "runden_f2": runden(laenge_m, "f2"), "reifen": list(reifen),
                        "mindestzeit_s": mindestzeit, **bericht}}


def aus_logik(lg, laenge_m):
    """Renndaten einer gebauten Logik mit dem an der Spiel-KI kalibrierten Fahrzeugsatz."""
    profil = spiel_ki.geschwindigkeit_neu(lg)["ki_profil"]
    return berechnen(float(laenge_m), profil["rundenzeit_s"], profil["a_quer_mittel"], lg.get("boxengasse"))
