"""Querprofil neben der Fahrbahn: Randsteine, Grünstreifen, Kiesbett, Barriere, Boxengasse.

Je Seite und Station gibt es sechs Stützabstände e0..e5 (Meter ab Fahrbahnrand, nach
außen) und fünf Bänder dazwischen:

    Band 0  [e0, e1]  Grünkeil zwischen Fahrbahn und Boxengasse (nur Boxenseite)
    Band 1  [e1, e2]  Boxengasse
    Band 2  [e2, e3]  Randstein
    Band 3  [e3, e4]  Grünstreifen
    Band 4  [e4, e5]  Auslauf: Kiesbett, Gras oder Asphalt; bei e5 steht die Barriere

Seitenraum-Abschnitte (``seitenraum.abschnitte``, 27.10.2026) überschreiben je Bereich und Seite Randsteinbreite,
Grünstreifenbreite, Auslauf-Material und Barrierenabstand; ohne Abschnitte rechnet alles wie vorher.
Bänder ohne Breite fallen weg. Weil alle Abstände stetig in s verlaufen, ergibt sich ein
lückenloses Netz (siehe geometrie.py). Die Barriere wird durch den freien Platz neben
der Strecke begrenzt (andere Streckenteile, Innenseite enger Kurven).
"""

import math

import numpy as np

from .definition import OBERFLAECHE
from .mittellinie import freiraum, periodisch_mittel, periodisch_minimum

SEITEN = (("links", 1.0), ("rechts", -1.0))


def _glatt01(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


def _maske_bereich(n, a, e):
    """Maske für Indizes a..e (einschließlich, periodisch, a/e beliebig ganzzahlig)."""
    m = np.zeros(n, dtype=bool)
    if e - a + 1 >= n:
        m[:] = True
    else:
        m[np.arange(a, e + 1) % n] = True
    return m


class Querprofil:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def an(self, sq, laenge):
        """Profil an beliebigen Bogenlängen: Abstände linear, Bänder nach nächster Station."""
        sq = np.mod(np.asarray(sq, dtype=float), laenge)
        s_ext = np.concatenate([self.s, [laenge]])
        erg = {}
        for name, _ in SEITEN:
            e = self.seiten[name]["e"]
            e_ext = np.vstack([e, e[:1]])
            ei = np.column_stack([np.interp(sq, s_ext, e_ext[:, k]) for k in range(e.shape[1])])
            idx = np.clip(np.floor(sq / self.ds).astype(int), 0, len(self.s) - 1)
            erg[name] = {"e": ei, "band": self.seiten[name]["band"][idx]}
        mauer = np.interp(sq, s_ext, np.concatenate([self.boxenmauer, self.boxenmauer[:1]]))
        return erg, mauer


def _abschnitte(ml, param):
    """Seitenraum-Abschnitte je Seite: [(Maske über die Stationen, Eintrag), …]."""
    st, L = ml.ref, ml.laenge
    s_e = np.mod(st.s + getattr(ml, "s_start", 0.0), L)
    erg = {name: [] for name, _ in SEITEN}
    for ab in (param.get("seitenraum") or {}).get("abschnitte") or []:
        von, bis = float(ab["von_s"]) % L, float(ab["bis_s"]) % L
        maske = (s_e >= von) & (s_e <= bis) if von <= bis else (s_e >= von) | (s_e <= bis)
        seite = ab.get("seite", "beide")
        for name, _ in SEITEN:
            if seite in ("beide", name):
                erg[name].append((maske, ab))
    return erg


def _ueberschreiben(werte, abschnitte, feld):
    """Kopie von ``werte`` mit den Feldwerten der Abschnitte (späterer Abschnitt gewinnt); None = unverändert."""
    neu, gesetzt = np.array(werte, dtype=float), False
    for maske, ab in abschnitte:
        if ab.get(feld) is not None:
            neu[maske] = float(ab[feld])
            gesetzt = True
    return neu if gesetzt else None


def boxen_spurversatz(pbox):
    """Versatz der Boxenlinie von der Gassenmitte Richtung Strecke (m), begrenzt, damit die Spur in der Gasse bleibt."""
    breite = float(pbox.get("breite", 12.0) or 12.0)
    return max(0.0, min(float(pbox.get("spur_versatz_m", 0.0)), breite / 2 - float(pbox.get("spur_halbbreite_m", 2.5))))


def boxen_u(pbox, U):
    """Lage der Boxen längs der Boxengasse (m ab Einfahrtsbeginn): mittig im vollbreiten Abschnitt."""
    a = pbox["anlauf_m"] + pbox["uebergang_m"]
    b = U - a
    span = (pbox["boxen"] - 1) * pbox["box_abstand_m"]
    if span > b - a:
        raise ValueError("Boxenplätze passen nicht in den vollbreiten Abschnitt")
    return (a + b) / 2 + (np.arange(pbox["boxen"]) - (pbox["boxen"] - 1) / 2) * pbox["box_abstand_m"]


def garagen_u(pbox, U):
    """(Anfang, Ende) des Garagengebäudes längs der Boxengasse oder None (kein Gebäude)."""
    if not pbox.get("gebaeude", True):
        return None
    us = boxen_u(pbox, U)
    # eine Garage mehr am Ende: der Träger braucht Box 12 (MyTeam), spiel_welt setzt sie hinter die letzte Box
    ende = min(us[-1] + 1.5 * pbox["box_abstand_m"], U - pbox["anlauf_m"] - pbox["uebergang_m"])
    return float(us[0] - pbox["box_abstand_m"] / 2), float(ende)


def querprofil_berechnen(ml, param, kurven):
    st = ml.ref
    n = st.anzahl
    ds = ml.ds
    L = ml.laenge
    warnungen = []
    pr, pg, pk, pb, pbox = (param["randstein"], param["gruenstreifen"], param["kiesbett"],
                           param["barriere"], param["boxengasse"])

    # ---- freier Platz je Seite
    fl, fr, gl, gr = freiraum(ml, max_abstand=2 * (pb["abstand"] + 40.0))
    frei = {}
    for (name, vz), D, wg in ((SEITEN[0], fl, gl), (SEITEN[1], fr, gr)):
        w = st.wl if vz > 0 else st.wr
        e = np.where(np.isfinite(D), (D - w - wg) / 2.0 - pb["luecke_m"] / 2.0, np.inf)
        innen = vz * st.kappa > 0
        with np.errstate(divide="ignore"):
            grenze = np.where(innen, 0.85 / np.abs(st.kappa) - w, np.inf)
        e = np.minimum(e, grenze)
        frei[name] = e

    # ---- Randsteine und Kiesbetten aus den Kurven
    kerb = {name: np.zeros(n) for name, _ in SEITEN}
    kies = {name: np.zeros(n, dtype=bool) for name, _ in SEITEN}
    vor, nach, anach = (int(round(pr[k] / ds)) for k in ("vorlauf_m", "nachlauf_m", "aussen_nachlauf_m"))
    for kv in kurven:
        a, e, sch = kv["i_anfang"], kv["i_ende"], kv["i_scheitel"]
        if e < a:
            e += n
        if sch < a:
            sch += n
        kmax = 1.0 / kv["min_radius_m"]
        innen = "links" if kv["richtung"] == "links" else "rechts"
        aussen = "rechts" if innen == "links" else "links"
        if kmax > pr["kruemmung_schwelle"]:
            kerb[innen] = np.maximum(kerb[innen], _maske_bereich(n, a - vor, e + nach))
            kerb[aussen] = np.maximum(kerb[aussen], _maske_bereich(n, sch, e + anach))
        if pk.get("aktiv", True) and kmax > pk["kruemmung_schwelle"]:
            kies[aussen] |= _maske_bereich(n, sch - int(round(pk["vorlauf_m"] / ds)),
                                           e + int(round(pk["nachlauf_m"] / ds)))
    rampe = max(1, int(round(pr["rampe_m"] / ds)))
    abschnitte = _abschnitte(ml, param)
    for name in kerb:
        eigene = _ueberschreiben(kerb[name] * pr["breite"], abschnitte[name], "randstein_breite")
        if eigene is None:
            kerb[name] = pr["breite"] * np.clip(periodisch_mittel(kerb[name], rampe), 0, 1)
        else:                                   # eigene Randsteinbreite (0 = keiner), Übergang wie die Automatik
            kerb[name] = np.maximum(periodisch_mittel(eigene, rampe), 0)
        for maske, ab in abschnitte[name]:
            if ab.get("auslauf") is not None:
                kies[name][maske] = ab["auslauf"] == "kies"
    asphalt = {name: np.zeros(n, dtype=bool) for name, _ in SEITEN}
    for name in asphalt:
        for maske, ab in abschnitte[name]:
            if ab.get("auslauf") is not None:
                asphalt[name][maske] = ab["auslauf"] == "asphalt"

    # ---- Boxengasse (Lage relativ zur Start-Ziel-Linie s = 0)
    keil = {name: np.zeros(n) for name, _ in SEITEN}
    boxb = {name: np.zeros(n) for name, _ in SEITEN}
    box_bereich = np.zeros(n, dtype=bool)
    garage = np.zeros(n, dtype=bool)          # Stationen neben dem Garagengebäude
    garage_gewicht = np.zeros(n)              # 1 am Gebäude, über 20 m auf 0 (Barriere dahinter)
    box_info = None
    if pbox.get("aktiv"):
        seite = pbox["seite"]
        s_ein = (L - pbox["einfahrt_vor_start_m"]) % L
        U = pbox["einfahrt_vor_start_m"] + pbox["ausfahrt_nach_start_m"]
        if not 0 < U < L:
            raise ValueError("Boxengasse muss kürzer als eine Runde sein")
        an, ue = pbox["anlauf_m"], pbox["uebergang_m"]
        if U < 2 * (an + ue) + 20:
            raise ValueError("Boxengasse zu kurz: Ein- und Ausfahrt (%.0f m) brauchen mindestens "
                             "%.0f m" % (U, 2 * (an + ue) + 20))
        u = np.mod(st.s - s_ein, L)
        box_bereich = u < U
        v = U - u                                      # Restweg bis zum Ende
        breite = pbox["breite"] * np.where(box_bereich, np.minimum(_glatt01(u / an), _glatt01(v / an)), 0)
        k_ein = _glatt01((u - an) / ue)
        k_aus = _glatt01((v - an) / ue)
        keil[seite] = np.where(box_bereich, pbox["abstand_fahrbahn"] * np.minimum(k_ein, k_aus), 0)
        boxb[seite] = breite
        kerb[seite] = np.where(box_bereich, 0.0, kerb[seite])
        kies[seite] &= ~box_bereich
        asphalt[seite] &= ~box_bereich
        gu = garagen_u(pbox, U)
        if gu is not None:
            garage = box_bereich & (u >= gu[0]) & (u <= gu[1])
            ausserhalb = np.maximum(gu[0] - u, u - gu[1])
            garage_gewicht = np.where(box_bereich, np.clip(1 - ausserhalb / 20.0, 0, 1), 0)
        box_info = {"seite": seite, "s_einfahrt": float(s_ein), "laenge_entlang_strecke": float(U),
                    "u": u, "bereich": box_bereich, "garagen_u": gu}

    # ---- Randwinkel: Höhenänderung je Meter nach außen, je Seite; ohne Eintrag bleibt der Rand waagerecht
    rand = {}
    for name, vz in SEITEN:
        werte = np.zeros(n)
        for maske, ab in abschnitte[name]:
            w = ab.get("rand_neigung")
            if w == "strecke":
                werte[maske] = (vz * np.tan(ml.bank))[maske]     # gleiche Ebene wie die Fahrbahn
            elif w is not None:
                werte[maske] = math.tan(math.radians(float(w)))
        if np.any(werte):
            rand[name] = periodisch_mittel(werte, rampe)
    if rand:
        ml.rand_setzen(rand.get("links", np.zeros(n)), rand.get("rechts", np.zeros(n)),
                       float((param.get("seitenraum") or {}).get("rand_breite_m", 12.0)))

    # ---- Abstände und Bänder
    seiten = {}
    for name, vz in SEITEN:
        e_bar = np.full(n, float(pb["abstand"]))
        eigene = _ueberschreiben(e_bar, abschnitte[name], "barriere_abstand")
        if eigene is not None:
            e_bar = eigene
        verfuegbar = frei[name]
        eng = verfuegbar < pb["mindestabstand"]
        if np.any(eng):
            warnungen.append("%s: an %d Stationen weniger als %.1f m Platz bis zum nächsten "
                             "Streckenteil (Barriere auf Mindestabstand gesetzt)"
                             % (name, int(eng.sum()), pb["mindestabstand"]))
        e_bar = np.minimum(e_bar, np.maximum(verfuegbar, pb["mindestabstand"]))
        h = int(round(15.0 / ds))
        e_bar = periodisch_mittel(periodisch_minimum(e_bar, h), h)
        e1 = keil[name]
        e2 = e1 + boxb[name]
        if pbox.get("aktiv") and name == pbox["seite"]:
            noetig = np.where(box_bereich, e2 + pbox["hinter_box_m"]
                              + garage_gewicht * pbox.get("gebaeude_tiefe_m", 0.0), 0)
            if np.any(noetig > verfuegbar + 1e-6):
                warnungen.append("Boxengasse: an %d Stationen reicht der Platz neben der Strecke "
                                 "nicht (Überlappung mit anderem Streckenteil möglich)"
                                 % int(np.sum(noetig > verfuegbar + 1e-6)))
            e_bar = np.maximum(e_bar, noetig)
        k = np.minimum(kerb[name], np.maximum(e_bar - e2, 0))
        e3 = e2 + k
        gruen = _ueberschreiben(np.full(n, float(pg["breite"])), abschnitte[name], "gruenstreifen_breite")
        if gruen is not None:
            gruen = periodisch_mittel(gruen, rampe)
        g = np.minimum(pg["breite"] if gruen is None else gruen, np.maximum(e_bar - e3, 0))
        boden = garage if (pbox.get("aktiv") and name == pbox["seite"]) else np.zeros(n, dtype=bool)
        if boden.any():                         # Garagenboden hinter der Boxengasse (Boxenasphalt)
            g = np.where(boden, pbox["gebaeude_tiefe_m"], g)
        e4 = e3 + g
        e5 = np.maximum(e_bar, e4)
        e = np.column_stack([np.zeros(n), e1, e2, e3, e4, e5])
        band = np.zeros((n, 5), dtype=np.int16)
        band[:, 0] = OBERFLAECHE["gras"]
        band[:, 1] = OBERFLAECHE["boxengasse"]
        band[:, 2] = OBERFLAECHE["randstein"]
        band[:, 3] = np.where(boden, OBERFLAECHE["boxengasse"], OBERFLAECHE["gras"])
        band[:, 4] = np.where(asphalt[name], OBERFLAECHE["auslauf"],
                              np.where(kies[name], OBERFLAECHE["kies"], OBERFLAECHE["gras"]))
        seiten[name] = {"e": e, "band": band, "frei": verfuegbar, "kies": kies[name],
                        "randstein": k}

    mauer = np.full(n, np.nan)
    if pbox.get("aktiv"):
        e1 = seiten[pbox["seite"]]["e"][:, 1]
        abst = e1 - pbox["mauer_abstand_m"]
        mauer = np.where(box_bereich & (abst > pg["breite"] * 0.5 + 1.0), abst, np.nan)
    return Querprofil(s=st.s, ds=ds, seiten=seiten, boxenmauer=mauer, box=box_info,
                      warnungen=warnungen)
