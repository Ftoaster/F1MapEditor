"""Ideallinie (minimale Krümmung) und Geschwindigkeitsprofil (Punktmasse oder g-g-v).

Ideallinie
    Die Linie wird als seitlicher Versatz alpha_i von der Mittellinie beschrieben:
    p_i = c_i + alpha_i * n_i (n = linke Normale). Minimiert wird die Summe der
    quadrierten linearisierten Krümmungen (kappa + alpha'' + kappa² alpha)².
    Damit liegt die Kreislinie korrekt am Außenrand. Das ist ein quadratisches
    Problem mit Schranken  -(wr_i - a) <= alpha_i <= wl_i - a  (a = Sicherheitsabstand).
    Gelöst wird es mit beschleunigter Projektion und adaptivem Neustart auf einem
    zyklischen System mit fünf Diagonalen. Ein expliziter KKT-Test prüft die Konvergenz.
    Danach wird alpha formerhaltend (PCHIP) auf 1 m interpoliert, die Linie per Spline
    gleichmäßig nach ihrer eigenen Bogenlänge abgetastet und die Krümmung berechnet.

    Optional (Parameter ``ideallinie``): ``iterationen`` > 1 linearisiert nach jeder
    Lösung um die neue Linie neu (Heilmeier et al. 2020), bis sich die Linie um
    weniger als ``iterationen_toleranz_m`` verschiebt; ``kruemmung_max`` (1/m)
    begrenzt die linearisierte Krümmung (ADMM-Löser, dichte Matrix). Standard:
    eine Linearisierung um die Mittellinie, keine Krümmungsgrenze.

Geschwindigkeitsprofil
    Punktmasse mit Abtrieb und Luftwiderstand. Querbeschleunigung
    a_y,max(v) = mu * (g + k_A v^2); Längs: Antrieb min(P / (m v), Traktion) bzw.
    Bremsen mu * (g + k_A v^2), jeweils im Reibungskreis (Ellipse) mit der genutzten
    Querbeschleunigung; Luftwiderstand und Rollwiderstand wirken immer.
    Gekoppelte Vorwärts-/Rückwärtshüllkurven bis zur Konvergenz. Die Grenzen werden
    an beiden Segmentenden geprüft, einschließlich der Rundennaht. Das Profil
    benutzt die ungeglättete geometrische Krümmung, keine geglätteten Kurvenspitzen.

    Optional (``fahrzeug.modell = "ggv"``, Schritt D): quasistationäres g-g-v-Modell
    mit getrennten Reibwerten quer/Bremsen/Antrieb, verallgemeinerter Reibungsellipse,
    Steigung und vertikaler Krümmung; Vorwärts-/Rückwärtslöser (``FahrzeugGGV``,
    ``geschwindigkeitsprofil_ggv``). Kalibrierte Sätze in ``strecke/fahrzeuge/*.json``
    (``fahrzeug.parametersatz``).
"""

import math

import numpy as np

from ego import parallel

from .mittellinie import PeriodischerSpline, periodisch_gauss

G = 9.81


# =========================================================================== Box-QP
def _bandprodukt(band, x):
    """Produkt eines zyklischen Systems mit fünf Diagonalen, Speicher O(n)."""
    return sum(w * np.roll(x, -k) for k, w in zip((-2, -1, 0, 1, 2), band))


def box_qp(band, q, lo, hi, max_iter=60000, toleranz=1e-7, toleranz_min=0.0):
    """Box-QP per beschleunigter Projektion mit adaptivem Neustart.

    Die Gershgorin-Schranke ist eine sichere Lipschitz-Konstante. Bei
    überschrittenem KKT-Rest wird abgebrochen statt still eine Näherung auszugeben.
    """
    if np.any(lo > hi):
        raise ValueError("Road too narrow for the safety margin")
    lip = float(np.max(np.sum(np.abs(band), axis=0)))
    diag = float(np.mean(band[2]))
    x = np.clip(np.zeros(len(q)), lo, hi)
    start_rest = float(np.max(np.abs(x-np.clip(x-q/diag,lo,hi))))
    # Eine fast krümmungsfreie, große Kreislinie darf nicht allein wegen
    # kleiner absoluter Gradienten vor Erreichen des Außenrands als fertig gelten.
    if start_rest > 0:
        toleranz = max(min(toleranz,start_rest*1e-5), toleranz_min)
    # Exakte eindimensionale Minimierung für eine konstante Verschiebung.
    # Diese fast freie Mode ist auf langen Geraden und großen Kreisen sehr
    # schlecht konditioniert; reine Gradienten können dort vorzeitig stocken.
    nenner = float(np.sum(np.asarray(band,dtype=np.longdouble)))
    if nenner > 1e-14:
        schub = np.clip(-np.sum(_bandprodukt(band,x)+q)/nenner,
                        np.max(lo-x),np.min(hi-x))
        x += schub
    y, tk = x.copy(), 1.0
    rest = np.inf
    # Bis zum Vierfachen von max_iter, bevor abgebrochen wird (4.11.2026: Catalunya, Las Vegas, Losail blieben nach
    # 60.000 Schritten knapp über der Toleranz und konvergierten danach). Wer früher konvergiert, bricht wie bisher ab.
    for it in range(1, 4 * max_iter + 1):
        neu = np.clip(y - (_bandprodukt(band, y) + q) / lip, lo, hi)
        tn = 0.5 * (1 + math.sqrt(1 + 4 * tk * tk))
        # Gradient-Neustart verhindert Schwingungen an aktiven Schranken.
        if np.dot(y - neu, neu - x) > 0:
            tn = 1.0
        y = neu + (tk - 1) / tn * (neu - x) if tn > 1 else neu.copy()
        x, tk = neu, tn
        if it % 100 == 0:
            rest = float(np.max(np.abs(x - np.clip(
                x - (_bandprodukt(band, x) + q) / diag, lo, hi))))
            if rest < toleranz:
                break
    rest = float(np.max(np.abs(x - np.clip(
        x - (_bandprodukt(band,x)+q)/diag,lo,hi))))
    if rest >= toleranz:
        raise RuntimeError("Racing line does not converge: KKT residual %.3g" % rest)
    return x, {"verfahren": "zyklische_fuenf_diagonalen_projektion",
               "iterationen": it, "kkt_rest": rest, "kkt_toleranz": toleranz,
               "aktiv_unten": int(np.sum(x <= lo + 1e-6)),
               "aktiv_oben": int(np.sum(x >= hi - 1e-6)), "unbekannte": len(q)}


def kruemmung_qp_admm(mitte, quelle, h, lo, hi, kmax, x0=None, max_iter=20000, toleranz=1e-6):
    """Minimal-Krümmung mit Box- und Krümmungsgrenzen per ADMM (OSQP-Schema, NumPy).

    min |A x + b|²  mit  lo <= x <= hi  und  |A x + b| <= kmax h²,
    A = zyklischer, symmetrischer Tridiagonaloperator (1, mitte, 1), b = quelle.
    Beide Nebenbedingungsblöcke sind in Metern skaliert. Das lineare System
    (P + sigma I + rho (I + A^T A)) wird dicht invertiert (O(n²) Speicher, nur wenn
    eine Krümmungsgrenze aktiv wird); A selbst wird als Bandoperator angewandt.
    Unerfüllbare Grenzen erkennt das Unzulässigkeitszertifikat von OSQP.
    """
    n = len(mitte)
    if np.any(lo > hi):
        raise ValueError("Road too narrow for the safety margin")
    def op(x):
        return np.roll(x, 1) + mitte * x + np.roll(x, -1)
    A = np.zeros((n, n))
    i = np.arange(n)
    A[i, i] = mitte
    A[i, (i-1) % n] += 1.0
    A[i, (i+1) % n] += 1.0
    AtA = A @ A
    del A
    q = 2*op(quelle)
    grenze = kmax*h*h
    l2, u2 = -grenze-quelle, grenze-quelle
    sigma, rho, relax = 1e-6, 1.0, 1.6
    def matrix(r):
        return np.linalg.inv((2.0+r)*AtA + (sigma+r)*np.eye(n))
    Minv = matrix(rho)
    x = np.clip(np.zeros(n) if x0 is None else np.asarray(x0, float), lo, hi)
    z1, z2 = x.copy(), np.clip(op(x), l2, u2)
    y1, y2 = np.zeros(n), np.zeros(n)
    neuberechnet = 0
    prim = dual = np.inf
    for it in range(1, max_iter+1):
        y1a, y2a = y1.copy(), y2.copy()
        xt = Minv @ (sigma*x - q + rho*z1 - y1 + op(rho*z2 - y2))
        zt2 = op(xt)
        x = relax*xt + (1-relax)*x
        w1 = relax*xt + (1-relax)*z1
        w2 = relax*zt2 + (1-relax)*z2
        z1n = np.clip(w1 + y1/rho, lo, hi)
        z2n = np.clip(w2 + y2/rho, l2, u2)
        y1 += rho*(w1 - z1n)
        y2 += rho*(w2 - z2n)
        z1, z2 = z1n, z2n
        if it % 25 == 0:
            Ax = op(x)
            prim = max(np.max(np.abs(x-z1)), np.max(np.abs(Ax-z2)))
            dual = np.max(np.abs(2*op(Ax) + q + y1 + op(y2)))
            if prim < toleranz and dual < toleranz*max(1.0, np.max(np.abs(q))):
                break
            # Primale Unzulässigkeit (OSQP): C^T dy ~ 0 und u^T dy+ + l^T dy- < 0.
            d1, d2 = y1 - y1a, y2 - y2a
            dn = max(np.max(np.abs(d1)), np.max(np.abs(d2)))
            if dn > 1e-12:
                ct = np.max(np.abs(d1 + op(d2)))
                stuetz = (np.sum(hi*np.maximum(d1, 0) + lo*np.minimum(d1, 0))
                          + np.sum(u2*np.maximum(d2, 0) + l2*np.minimum(d2, 0)))
                if ct < 1e-5*dn and stuetz < -1e-5*dn:
                    raise ValueError("Curvature limit %.4g 1/m cannot be met on this road"
                                     % kmax)
            # Adaptives rho (selten, jede Änderung kostet eine Inversion).
            if it % 200 == 0 and neuberechnet < 12:
                pn = prim/max(np.max(np.abs(x)), np.max(np.abs(z1)), 1e-9)
                dn = dual/max(np.max(np.abs(q)), 1e-9)
                faktor = math.sqrt(pn/max(dn, 1e-30))
                if faktor > 5 or faktor < 0.2:
                    rho = float(np.clip(rho*faktor, 1e-6, 1e6))
                    Minv = matrix(rho)
                    neuberechnet += 1
    else:
        raise RuntimeError("Curvature limit %.4g 1/m: ADMM does not converge (residual primal %.3g, dual %.3g)"
                           % (kmax, prim, dual))
    x = np.clip(x, lo, hi)
    kappa_lin = (op(x) + quelle)/(h*h)
    return x, {"verfahren": "admm_box_und_kruemmungsgrenze", "iterationen": it,
               "primal_rest": float(prim), "dual_rest": float(dual), "rho": rho,
               "kruemmung_max": float(kmax),
               "kruemmung_lin_max": float(np.max(np.abs(kappa_lin))),
               "aktiv_kruemmung": int(np.sum(np.abs(kappa_lin) >= kmax*(1-1e-4))),
               "aktiv_unten": int(np.sum(x <= lo + 1e-6)),
               "aktiv_oben": int(np.sum(x >= hi - 1e-6)), "unbekannte": n}


def _qp_schritt(kappa, h, lo, hi, kruemmung_max=None, toleranz_min=0.0):
    """Ein linearisiertes Minimal-Krümmungs-QP um eine Referenzlinie (Krümmung kappa)."""
    n = len(kappa)
    # Linearisierte geometrische Krümmung: k + alpha'' + k² alpha.
    # Reine zweite Ortsdifferenzen würden beim Kreis fälschlich den Innenrand
    # wählen, weil sie den geänderten Weglängenmaßstab nicht berücksichtigen.
    mitte = -2 + (h*kappa)**2
    quelle = h*h*kappa
    band = np.array([np.full(n,2.0), 2*(mitte+np.roll(mitte,1)),
                     2*(2+mitte*mitte), 2*(mitte+np.roll(mitte,-1)),np.full(n,2.0)])
    def operator(x):
        return np.roll(x,1)+mitte*x+np.roll(x,-1)
    q = 2*operator(quelle)
    alpha, info = box_qp(band, q, lo, hi, toleranz_min=toleranz_min)
    if kruemmung_max is not None:
        kl = np.max(np.abs(operator(alpha)+quelle))/(h*h)
        info["kruemmung_lin_max_ohne_grenze"] = float(kl)
        if kl > kruemmung_max:
            alpha, info2 = kruemmung_qp_admm(mitte, quelle, h, lo, hi, kruemmung_max, x0=alpha)
            info2["box_qp"] = info
            info = info2
    info["zielwert_referenz"] = float(np.sum(quelle**2))
    info["zielwert_neu"] = float(np.sum((operator(alpha)+quelle)**2))
    return alpha, info


def _linie_abtasten(pkt, n, s_ml, L_ml):
    """Geschlossene Linie (x, z) -> n Stationen gleichen Bogenabstands.

    Rückgabe pos (n, 2), t, nl (linke Normale wie Mittellinie), kappa, h,
    Länge und die zugeordnete Mittellinienstation s_ml (periodisch fortgeführt).
    """
    spl = PeriodischerSpline(np.column_stack([pkt[:, 0], np.zeros(len(pkt)), pkt[:, 1]]))
    L = spl.laenge
    h = L / n
    tau = spl.param_aus_s(np.arange(n) * h)
    p = spl.auswerten(tau)
    d1 = spl.auswerten(tau, 1)
    d2 = spl.auswerten(tau, 2)
    vx, vz = d1[:, 0], d1[:, 2]
    v2 = np.hypot(vx, vz)
    t = np.column_stack([vx / v2, vz / v2])
    kappa_spline = (d2[:, 0] * vz - d2[:, 2] * vx) / v2 ** 3
    # Diskrete Dreipunkt-Krümmung (Menger) passt zur zweiten Differenz im QP. Mit der
    # Splinekrümmung schaukeln sich Knicke an aktiven Grenzen nach ~6 Durchläufen auf.
    q = p[:, [0, 2]]
    a, c = np.roll(q, 1, axis=0), np.roll(q, -1, axis=0)
    u, v = q - a, c - q
    kreuz = u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]
    kappa = -2 * kreuz / (np.linalg.norm(u, axis=1) * np.linalg.norm(v, axis=1)
                          * np.linalg.norm(c - a, axis=1))
    s_auf = np.unwrap(s_ml, period=L_ml)
    s_neu = np.interp(tau, spl.t, np.concatenate([s_auf, [s_auf[0] + L_ml]])) % L_ml
    return {"pos": p[:, [0, 2]], "t": t, "nl": np.column_stack([t[:, 1], -t[:, 0]]),
            "kappa": kappa, "kappa_spline": kappa_spline, "h": h, "laenge": L, "s_ml": s_neu, "spline": spl}


def strahl_schnitt(p, r, Q, j0, fenster, mit_winkel=False):
    """Abstand d entlang der Strahlen p_i + d r_i bis zur geschlossenen Polylinie Q.

    Gesucht wird nur in den Segmenten j0_i ± fenster; gewählt wird der Schnitt
    mit dem kleinsten Indexabstand. Kein Schnitt -> NaN. Mit ``mit_winkel`` zusätzlich
    |sin| des Winkels zwischen Strahl und getroffenem Segment (1 = senkrecht).
    """
    m = len(Q)
    off = np.arange(-fenster, fenster + 1)
    J = (np.asarray(j0)[:, None] + off[None, :]) % m
    a = Q[J]
    e = Q[(J + 1) % m] - a
    w = a - p[:, None, :]
    def kreuz(u, v):
        return u[..., 0] * v[..., 1] - u[..., 1] * v[..., 0]
    rr = np.broadcast_to(r[:, None, :], e.shape)
    nenner = kreuz(rr, e)
    with np.errstate(divide="ignore", invalid="ignore"):
        d = kreuz(w, e) / nenner
        u = kreuz(w, rr) / nenner
    gueltig = (np.abs(nenner) > 1e-12) & (u >= 0) & (u < 1)
    rang = np.where(gueltig, np.abs(off)[None, :], np.inf)
    k = np.argmin(rang, axis=1)
    i = np.arange(len(p))
    erg = np.where(np.isfinite(rang[i, k]), d[i, k], np.nan)
    if mit_winkel:
        sinus = np.abs(nenner[i, k]) / np.maximum(np.linalg.norm(e[i, k], axis=1), 1e-12)
        return erg, sinus
    return erg


def randreserve(ml, xz, s_ml, fenster_m=40.0):
    """Kleinster euklidischer Abstand von Punkten (x, z) zu den Fahrbahnrändern.

    Gesucht wird je Punkt nur im Randbereich um seine Mittellinienstation s_ml.
    Liegt ein Punkt jenseits eines Rands, wird der Abstand negativ.
    """
    ref = ml.ref
    m = len(ref.s)
    w = int(math.ceil(fenster_m / ml.ds))
    off = np.arange(-w, w + 1)
    j0 = np.round(np.asarray(s_ml) / ml.ds).astype(int) % m
    J = (j0[:, None] + off[None, :]) % m
    erg = np.inf
    for rand, vorz in ((ref.punkt(ml.wl)[:, [0, 2]], 1.0), (ref.punkt(-ml.wr)[:, [0, 2]], -1.0)):
        a = rand[J]
        e = rand[(J + 1) % m] - a
        t = np.clip(np.sum((xz[:, None, :] - a) * e, axis=2) / np.maximum(np.sum(e * e, axis=2), 1e-12), 0, 1)
        d = xz[:, None, :] - (a + t[..., None] * e)
        dist = np.sqrt(np.sum(d * d, axis=2))
        k = np.argmin(dist, axis=1)
        i = np.arange(len(xz))
        # Seite: Kreuzprodukt Randrichtung x (Punkt - Rand); links innen = rechts vom linken Rand.
        kr = e[i, k, 0] * d[i, k, 1] - e[i, k, 1] * d[i, k, 0]
        innen = (kr > 0) if vorz > 0 else (kr < 0)
        erg = min(erg, float(np.min(np.where(innen, dist[i, k], -dist[i, k]))))
    return erg


def kruemmungsbilanz(pkt):
    """Länge, Integral |kappa| ds und kappa² ds einer geschlossenen Linie (x, z)."""
    spl = PeriodischerSpline(np.column_stack([pkt[:, 0], np.zeros(len(pkt)), pkt[:, 1]]))
    n = max(64, int(round(spl.laenge)))
    h = spl.laenge / n
    tau = spl.param_aus_s(np.arange(n) * h)
    d1 = spl.auswerten(tau, 1)
    d2 = spl.auswerten(tau, 2)
    vx, vz = d1[:, 0], d1[:, 2]
    kappa = (d2[:, 0] * vz - d2[:, 2] * vx) / np.hypot(vx, vz) ** 3
    return {"laenge": spl.laenge, "kappa": kappa, "ds": h,
            "int_abs": float(np.sum(np.abs(kappa)) * h), "int_quadrat": float(np.sum(kappa**2) * h)}


def minimale_kruemmung(ml, schritt=3.0, sicherheit=1.5, iterationen=1, kruemmung_max=None,
                       toleranz_m=0.01, feine_rueckprojektion=False):
    """Optimiert seitlichen Versatz; zweite Differenzen ohne dichte Matrix.

    iterationen > 1: nach jeder Lösung wird um die neue Linie neu linearisiert
    (Heilmeier et al. 2020): neue Referenz mit eigenen Normalen und eigener
    Krümmung, Grenzen als Schnitt der neuen Normalen mit den Fahrbahnrändern.
    Abbruch, sobald sich die Linie um weniger als toleranz_m verschiebt.
    Rückgabe immer als Versatz zur Mittellinie an den Stationen s; bei
    iterationen > 1 oder feine_rueckprojektion zusätzlich ``alpha_fein`` auf dem
    1-m-Bezugsgitter der Mittellinie (Schnitt der Normalen mit der Lösungslinie).
    """
    L = ml.laenge
    n = max(32, int(round(L / schritt)))
    s = np.arange(n) * (L / n)
    st = ml.stationen(s)
    lo, hi = -(st.wr - sicherheit), st.wl - sicherheit
    h = L/n
    alpha, info = _qp_schritt(st.kappa, h, lo, hi, kruemmung_max)
    zielwert_ml, zielwert_neu = info.pop("zielwert_referenz"), info.pop("zielwert_neu")
    info.update(schritt_m=float(h), sicherheitsabstand_m=float(sicherheit),
                zielwert_mittellinie=zielwert_ml, zielwert_ideallinie=zielwert_neu,
                zielfunktion="Quadrat der linearisierten Krümmung, skaliert mit h^4")
    if iterationen <= 1 and not feine_rueckprojektion:
        if kruemmung_max is not None:
            info["kruemmung_max"] = float(kruemmung_max)
        return {"s": s, "alpha": alpha, "lo": lo, "hi": hi, "info": info}

    # ---- Iterativ: Referenz = letzte Lösung
    ref = ml.ref
    links = ref.punkt(ml.wl)[:, [0, 2]]
    rechts = ref.punkt(-ml.wr)[:, [0, 2]]
    mitte_pkt = ref.pos[:, [0, 2]]
    m_fein = len(ref.s)
    fenster = int(math.ceil(60.0 / ml.ds))
    pkt = st.pos[:, [0, 2]] + alpha[:, None] * st.nl
    s_ml = s.copy()
    verlauf = [{"iteration": 1, "verschiebung_max_m": float(np.max(np.abs(alpha))),
                "zielwert": zielwert_neu, "kkt_rest": info.get("kkt_rest")}]
    konvergiert = int(iterationen) <= 1
    for it in range(2, int(iterationen) + 1):
        lin = _linie_abtasten(pkt, n, s_ml, L)
        j0 = np.round(lin["s_ml"] / ml.ds).astype(int) % m_fein
        dl, sl = strahl_schnitt(lin["pos"], lin["nl"], links, j0, fenster, mit_winkel=True)
        dr, sr = strahl_schnitt(lin["pos"], -lin["nl"], rechts, j0, fenster, mit_winkel=True)
        if not (np.all(np.isfinite(dl)) and np.all(np.isfinite(dr))):
            verlauf.append({"iteration": it, "abbruch": "Normale schneidet Fahrbahnrand nicht"})
            break
        # Euklidischer Abstand zum (lokal geraden) Rand = Strahlabstand * sin(Winkel):
        # schräg getroffene Ränder brauchen entlang der Normalen mehr Reserve.
        lo_k = -(dr - sicherheit / np.maximum(sr, 0.5))
        hi_k = dl - sicherheit / np.maximum(sl, 0.5)
        # Rundungsreserve: die Vorlinie liegt innerhalb, 0 bleibt zulässig.
        lo_k = np.minimum(lo_k, 0.0)
        hi_k = np.maximum(hi_k, 0.0)
        # Nahe am Fixpunkt ist der Startgradient winzig; absolute Untergrenze 1e-7 m.
        d, info_k = _qp_schritt(lin["kappa"], lin["h"], lo_k, hi_k, kruemmung_max, toleranz_min=1e-7)
        aenderung = float(np.max(np.abs(d)))
        if len(verlauf) >= 3 and aenderung > 3 * min(v["verschiebung_max_m"] for v in verlauf[1:]):
            # Schutz gegen Aufschaukeln: vorige Linie behalten.
            verlauf.append({"iteration": it, "abbruch": "Änderung wächst (%.3f m)" % aenderung})
            break
        pkt = lin["pos"] + d[:, None] * lin["nl"]
        s_ml = lin["s_ml"]
        verlauf.append({"iteration": it, "verschiebung_max_m": aenderung,
                        "zielwert": info_k["zielwert_neu"], "kkt_rest": info_k.get("kkt_rest"),
                        "qp_iterationen": info_k.get("iterationen")})
        if aenderung < toleranz_m:
            konvergiert = True
            break

    # ---- Zurück auf Mittellinienversatz an den Stationen s
    lin = _linie_abtasten(pkt, max(64, int(round(L / 0.5))), s_ml, L)
    fein = lin["pos"]
    s_auf = np.unwrap(lin["s_ml"], period=L)
    s_auf -= np.floor(s_auf[0] / L) * L
    def rueck(stat):
        j0 = np.searchsorted(s_auf, stat.s) % len(fein)
        a = strahl_schnitt(stat.pos[:, [0, 2]], stat.nl, fein, j0, 40)
        if not np.all(np.isfinite(a)):
            raise RuntimeError("Iterated racing line: back-projection onto the centreline failed")
        return a
    a_neu = rueck(st)
    alpha = np.clip(a_neu, lo, hi)
    # Zusätzlich direkt auf dem feinen Bezugsgitter: keine PCHIP-Knicke im 3-m-Raster.
    # Nicht auf die Normalen-Schranken der Mittellinie kappen: In engen Kurven misst
    # die Mittellinien-Normale schräg, die Linie hält den Abstand aber entlang ihrer
    # eigenen Normalen ein. Ein Kappen erzeugt dort Knicke (Zandvoort: +11 s Runde).
    # Harte Grenze nur bei halbem Sicherheitsabstand; geprüft wird euklidisch.
    a_fein = rueck(ref)
    alpha_fein = np.clip(a_fein, -(ref.wr - sicherheit / 2), ref.wl - sicherheit / 2)
    reserve = randreserve(ml, ref.pos[:, [0, 2]] + alpha_fein[:, None] * ref.nl, ref.s)
    info.update(iterationen_aussen=len(verlauf), konvergiert=konvergiert,
                toleranz_m=float(toleranz_m), verlauf=verlauf,
                rueckprojektion_clip_max_m=float(np.max(np.abs(a_neu - alpha))),
                normalen_schranke_ueberschritten_max_m=float(np.max(np.maximum(
                    -(ref.wr - sicherheit) - a_fein, a_fein - (ref.wl - sicherheit)))),
                hart_gekappt_max_m=float(np.max(np.abs(a_fein - alpha_fein))),
                randreserve_euklidisch_min_m=reserve)
    if kruemmung_max is not None:
        info["kruemmung_max"] = float(kruemmung_max)
    return {"s": s, "alpha": alpha, "lo": lo, "hi": hi, "info": info, "alpha_fein": alpha_fein}


# =========================================================================== PCHIP
def periodisch_pchip(sq, s, y, laenge):
    """Formerhaltende kubische Hermite-Interpolation (Fritsch-Carlson), geschlossen."""
    s_ext = np.concatenate([s, [s[0] + laenge]])
    y_ext = np.concatenate([y, [y[0]]])
    h = np.diff(s_ext)
    delta = np.diff(y_ext) / h                     # Sekante k -> k+1
    dm = np.roll(delta, 1)                         # Sekante k-1 -> k
    hm = np.roll(h, 1)
    w1 = 2 * h + hm
    w2 = h + 2 * hm
    with np.errstate(divide="ignore", invalid="ignore"):
        d = (w1 + w2) / (w1 / dm + w2 / delta)
    d = np.where(dm * delta > 0, d, 0.0)
    d_ext = np.concatenate([d, [d[0]]])
    sq = np.mod(np.asarray(sq, dtype=float), laenge)
    k = np.clip(np.searchsorted(s_ext, sq, side="right") - 1, 0, len(s) - 1)
    hk = h[k]
    t = (sq - s_ext[k]) / hk
    h00 = 2 * t ** 3 - 3 * t ** 2 + 1
    h10 = t ** 3 - 2 * t ** 2 + t
    h01 = -2 * t ** 3 + 3 * t ** 2
    h11 = t ** 3 - t ** 2
    return h00 * y_ext[k] + h10 * hk * d_ext[k] + h01 * y_ext[k + 1] + h11 * hk * d_ext[k + 1]


# =========================================================================== Geschwindigkeit
class Fahrzeug:
    """Grob F1-ähnliche Punktmasse (Parameter siehe definition.STANDARD['fahrzeug'])."""

    def __init__(self, p):
        self.m = float(p["masse_kg"])
        self.leistung = float(p["leistung_kw"]) * 1000.0
        self.k_w = 0.5 * float(p["luftdichte"]) * float(p["cw_a_m2"]) / self.m
        self.k_a = 0.5 * float(p["luftdichte"]) * float(p["ca_a_m2"]) / self.m
        self.mu = float(p["reibwert"])
        self.traktion = float(p["traktion_anteil"])
        self.roll = float(p["rollwiderstand"]) * G
        self.v_max = float(p["v_max_kmh"]) / 3.6
        self.param = dict(p)

    def ay_max(self, v):
        return self.mu * (G + self.k_a * v * v)

    def widerstand(self, v):
        return self.k_w * v * v + self.roll

    def antrieb_max(self, v, ay):
        """Maximale Vortriebsbeschleunigung (vor Abzug der Widerstände)."""
        griff = self.traktion * self.ay_max(v)
        rest = max(0.0, 1.0 - (ay / self.ay_max(v)) ** 2)
        return min(self.leistung / (self.m * max(v, 1.0)), griff * math.sqrt(rest))

    def brems_max(self, v, ay):
        rest = max(0.0, 1.0 - (ay / self.ay_max(v)) ** 2)
        return self.ay_max(v) * math.sqrt(rest)

    def v_kurve(self, kappa):
        """Grenzgeschwindigkeit aus der Querbeschleunigung (ohne Längsanteil)."""
        ak = np.abs(np.asarray(kappa, dtype=float))
        nenner = ak - self.mu * self.k_a
        with np.errstate(divide="ignore", invalid="ignore"):
            v = np.where(nenner > 0, np.sqrt(self.mu * G / nenner), np.inf)
        return np.minimum(v, self.v_max)


FAHRZEUG_ORDNER = __import__("pathlib").Path(__file__).resolve().parent / "fahrzeuge"


def parametersatz_laden(name):
    """Gespeicherten Fahrzeug-Parametersatz ``strecke/fahrzeuge/<name>.json`` laden."""
    import json
    pfad = FAHRZEUG_ORDNER / ("%s.json" % name)
    if not pfad.exists():
        verfuegbar = sorted(p.stem for p in FAHRZEUG_ORDNER.glob("*.json"))
        raise ValueError("Vehicle parameter set %r unknown (available: %s)"
                         % (name, ", ".join(verfuegbar) or "keine"))
    with open(pfad, encoding="utf-8") as f:
        satz = json.load(f)
    if not isinstance(satz.get("fahrzeug"), dict):
        raise ValueError("%s is not a vehicle parameter set (key 'fahrzeug' is missing)" % pfad.name)
    return satz


def fahrzeug_param(p):
    """Fahrzeugparameter mit aufgelöstem ``parametersatz`` (Satz überschreibt Werte)."""
    p = dict(p)
    name = p.get("parametersatz")
    if name:
        satz = parametersatz_laden(name)["fahrzeug"]
        for k, w in satz.items():
            if k == "ggv":
                p["ggv"] = dict(p.get("ggv") or {}, **w)
            else:
                p[k] = w
    return p


def fahrzeug_erzeugen(p):
    """Fahrzeugmodell nach ``fahrzeug.modell``: ``punktmasse`` (Standard) oder ``ggv``."""
    p = fahrzeug_param(p)
    modell = p.get("modell", "punktmasse")
    if modell == "punktmasse":
        return Fahrzeug(p)
    if modell == "ggv":
        return FahrzeugGGV(p)
    raise ValueError("fahrzeug.modell must be punktmasse or ggv, not %r" % modell)


class FahrzeugGGV:
    """Quasistationäres g-g-v(-g)-Modell (Schritt D).

    Normalbeschleunigung je Masse  N(v) = g cos(theta) + (k_A + kappa_v) v²
    (Abtrieb ∝ v², vertikale Krümmung kappa_v = y'' der Höhe; Kuppe < 0 entlastet).
    Grenzen: quer mu_q N, Bremsen mu_b N, Antrieb min(P/(m v), mu_a N), gekoppelt
    über eine verallgemeinerte Reibungsellipse |ax/ax_max|^e + |ay/ay_max|^e <= 1.
    Widerstand k_W v² + Rollwiderstand + g sin(theta) (bergauf positiv).
    Optional begrenzt ``verzoegerung_max_ms2`` die Bremsverzögerung (Bremsanlage).
    """

    modell = "ggv"

    def __init__(self, p):
        q = dict(p.get("ggv") or {})
        self.m = float(p["masse_kg"])
        self.leistung = float(p["leistung_kw"]) * 1000.0
        self.k_w = 0.5 * float(p["luftdichte"]) * float(p["cw_a_m2"]) / self.m
        self.k_a = 0.5 * float(p["luftdichte"]) * float(p["ca_a_m2"]) / self.m
        mu = float(p["reibwert"])
        def wert(name, standard):
            w = q.get(name)
            return float(standard if w is None else w)
        self.mu_q = wert("reibwert_quer", mu)
        self.mu_b = wert("reibwert_bremsen", mu)
        self.mu_a = wert("reibwert_antrieb", float(p["traktion_anteil"]) * mu)
        self.exp = wert("ellipse_exponent", 2.0)
        vmax = q.get("verzoegerung_max_ms2")
        self.brems_grenze = math.inf if vmax is None else float(vmax)
        self.hoehe = bool(q.get("hoeheneinfluss", True))
        self.hoehe_glaettung = wert("hoehe_glaettung_m", 10.0)
        self.roll = float(p["rollwiderstand"]) * G
        self.v_max = float(p["v_max_kmh"]) / 3.6
        self.param = dict(p)
        self.mu = self.mu_q            # Kompatibilität (Kurvengrenze ebener Fall)

    # ---- ebene Hilfsfunktionen (Schnittstelle wie Fahrzeug, theta = 0, kappa_v = 0)
    def ay_max(self, v, gn=G, kv=0.0):
        return self.mu_q * max(gn + (self.k_a + kv) * v * v, 0.0)

    def widerstand(self, v, steig=0.0):
        return self.k_w * v * v + self.roll + G * steig

    def _rest(self, v, ay, gn, kv):
        aym = self.ay_max(v, gn, kv)
        if aym <= 0:
            return 0.0
        r = 1.0 - (abs(ay) / aym) ** self.exp
        return r ** (1.0 / self.exp) if r > 0 else 0.0

    def antrieb_max(self, v, ay, gn=G, kv=0.0):
        n = max(gn + (self.k_a + kv) * v * v, 0.0)
        return min(self.leistung / (self.m * max(v, 1.0)), self.mu_a * n * self._rest(v, ay, gn, kv))

    def brems_max(self, v, ay, gn=G, kv=0.0):
        n = max(gn + (self.k_a + kv) * v * v, 0.0)
        return min(self.mu_b * n, self.brems_grenze) * self._rest(v, ay, gn, kv)

    def v_kurve(self, kappa, gn=None, kv=None):
        """Grenzgeschwindigkeit aus der Querbeschleunigung (ohne Längsanteil)."""
        ak = np.abs(np.asarray(kappa, dtype=float))
        gn = np.full(ak.shape, G) if gn is None else np.asarray(gn, float)
        kv = np.zeros(ak.shape) if kv is None else np.asarray(kv, float)
        nenner = ak - self.mu_q * (self.k_a + kv)
        with np.errstate(divide="ignore", invalid="ignore"):
            v = np.where(nenner > 0, np.sqrt(np.maximum(self.mu_q * gn, 0) / nenner), np.inf)
        return np.minimum(v, self.v_max)


def _hoehenverlauf(hoehe, ds, glaettung_m):
    """sin(Steigung), cos(Steigung) und vertikale Krümmung einer geschlossenen Höhenlinie."""
    y = np.asarray(hoehe, dtype=float)
    sigma = glaettung_m / ds
    if sigma > 0:
        # Höhe enthält keinen periodischen Sprung; Glättung wie bei der Krümmung.
        y = periodisch_gauss(y, sigma)
    dy = (np.roll(y, -1) - np.roll(y, 1)) / (2 * ds)
    d2y = (np.roll(y, -1) - 2 * y + np.roll(y, 1)) / (ds * ds)
    steig = dy / np.sqrt(1 + dy * dy)
    cos_t = 1 / np.sqrt(1 + dy * dy)
    kv = d2y * cos_t ** 3             # Krümmung des Höhenprofils (Wanne > 0)
    return steig, cos_t, kv


def geschwindigkeitsprofil_ggv(kappa, ds, fahrzeug, hoehe=None):
    """Quasistationäres Profil (g-g-v) per Vorwärts-/Rückwärtslöser, geschlossen.

    Start am globalen Minimum der Kurvengrenzgeschwindigkeit, je Schritt
    Heun-Verfahren (Mittel der Beschleunigung an beiden Segmentenden). Vorwärts-
    und Rückwärtsdurchlauf wechseln, bis sich nichts mehr ändert. Mit ``hoehe``
    (m je Station) wirken Steigung und vertikale Krümmung (Fahrzeug.hoehe).
    """
    fz = fahrzeug
    k = np.asarray(kappa, dtype=float)
    n = len(k)
    if hoehe is not None and fz.hoehe:
        steig, cos_t, kv = _hoehenverlauf(hoehe, ds, fz.hoehe_glaettung)
    else:
        steig, cos_t, kv = np.zeros(n), np.ones(n), np.zeros(n)
    gn = G * cos_t
    vk = fz.v_kurve(k, gn, kv)
    # Kurvengrenze mit Reserve gegen Fahrwiderstand (vektorisierte Bisektion).
    low, high = np.zeros(n), np.minimum(vk, fz.v_max)
    ak = np.abs(k)
    for _ in range(40):
        mid = (low + high) / 2
        nn = np.maximum(gn + (fz.k_a + kv) * mid * mid, 0.0)
        aym = fz.mu_q * nn
        with np.errstate(divide="ignore", invalid="ignore"):
            r = 1.0 - np.where(aym > 0, (ak * mid * mid / aym) ** fz.exp, np.inf)
        rest = np.where(r > 0, np.abs(r) ** (1.0 / fz.exp), 0.0)
        antr = np.minimum(fz.leistung / (fz.m * np.maximum(mid, 1.0)), fz.mu_a * nn * rest)
        ok = (antr >= fz.k_w * mid * mid + fz.roll + G * steig) & (r >= 0)
        low = np.where(ok, mid, low)
        high = np.where(ok, high, mid)
    # Steigung, die das Auto nicht hochkommt, würde 0 ergeben; Kurvengrenze dann ohne Reserve.
    v_lim = np.where(low > 0.5, low, np.minimum(vk, fz.v_max))
    kl, gl, kvl, sl, vl = k.tolist(), gn.tolist(), kv.tolist(), steig.tolist(), v_lim.tolist()
    # acc/dec = antrieb_max/brems_max ∓ widerstand, eingebettet (gleiche Rechenfolge, ~2× schneller;
    # die Mindestzeitsuche ruft das Profil einige hundert Mal auf).
    k_a, mu_q, mu_a, mu_b, k_w, roll = fz.k_a, fz.mu_q, fz.mu_a, fz.mu_b, fz.k_w, fz.roll
    ex, inv_ex, leist, masse, bg = fz.exp, 1.0 / fz.exp, fz.leistung, fz.m, fz.brems_grenze

    def _n_rest(v, i):
        n = gl[i] + (k_a + kvl[i]) * v * v
        if n < 0.0:
            n = 0.0
        aym = mu_q * n
        if aym <= 0:
            return n, 0.0
        r = 1.0 - (abs(kl[i] * v * v) / aym) ** ex
        return n, (r ** inv_ex if r > 0 else 0.0)

    def acc(v, i):
        n, rest = _n_rest(v, i)
        return min(leist / (masse * max(v, 1.0)), mu_a * n * rest) - (k_w * v * v + roll + G * sl[i])

    def dec(v, i):
        n, rest = _n_rest(v, i)
        return min(mu_b * n, bg) * rest + (k_w * v * v + roll + G * sl[i])

    def schritt(v0, i0, i1, f):
        """Erreichbare Geschwindigkeit an i1 aus v0 an i0 (f = Beschleunigungsfunktion)."""
        a0 = f(v0, i0)
        v2 = v0*v0 + 2*ds*a0
        v1 = math.sqrt(v2) if v2 > 0 else 0.0
        a1 = f(v1, i1)
        a = 0.5*(a0 + max(a1, 0.0)) if a0 > 0 else a0
        v2 = v0*v0 + 2*ds*a
        return math.sqrt(v2) if v2 > 0 else 0.0

    start = int(np.argmin(v_lim))
    v = list(vl)
    for durchlauf in range(50):
        vorher = list(v)
        for t in range(n):
            i = (start + t) % n
            j = (i + 1) % n
            if v[j] > v[i]:
                v[j] = min(v[j], schritt(v[i], i, j, acc))
        for t in range(n):
            j = (start - t) % n
            i = (j - 1) % n
            if v[i] > v[j]:
                v[i] = min(v[i], schritt(v[j], j, i, dec))
        if max(a - b for a, b in zip(vorher, v)) < 1e-6:
            break
    else:
        raise RuntimeError("g-g-v profile does not converge")
    v = np.asarray(v)
    v_next = np.roll(v, -1)
    a_laengs = (v_next ** 2 - v ** 2) / (2.0 * ds)
    a_quer = k * v ** 2
    vollgas = a_laengs > 0.98 * np.array([acc(vi, i) for i, vi in enumerate(v)])
    dt = 2.0 * ds / (v + v_next)
    zeit = np.concatenate([[0.0], np.cumsum(dt)[:-1]])
    return {"v": v, "a_laengs": a_laengs, "a_quer": a_quer, "zeit": zeit,
            "rundenzeit": float(np.sum(dt)), "vollgas": vollgas, "v_kurve": vk,
            "steigung": steig, "kappa_vertikal": kv}


def profil_berechnen(kappa, ds, fahrzeug, hoehe=None):
    """Geschwindigkeitsprofil passend zum Fahrzeugmodell."""
    if getattr(fahrzeug, "modell", "punktmasse") == "ggv":
        return geschwindigkeitsprofil_ggv(kappa, ds, fahrzeug, hoehe)
    return geschwindigkeitsprofil(kappa, ds, fahrzeug)


def geschwindigkeitsprofil(kappa, ds, fahrzeug):
    """Geschwindigkeit entlang einer geschlossenen Linie (Stützstellenabstand ds).

    Rückgabe: Dict v (m/s), a_laengs, a_quer, zeit (ab Index 0), rundenzeit, vollgas (bool).
    """
    fz = fahrzeug
    k = np.asarray(kappa, dtype=float)
    n = len(k)
    vk = fz.v_kurve(k)
    v = vk.copy()
    # Kurvengrenze mit Reserve zum Halten der Geschwindigkeit gegen Luftwiderstand.
    for i in range(n):
        low, high = 0.0, v[i]
        for _ in range(32):
            mid = (low + high) / 2
            if fz.antrieb_max(mid, k[i] * mid * mid) >= fz.widerstand(mid):
                low = mid
            else:
                high = mid
        v[i] = low

    def erreichbar(a, b, ka, kb, bremsen):
        # a: bekannte Geschwindigkeit, b: zu begrenzende höhere Geschwindigkeit.
        if b <= a:
            return b
        if bremsen:
            cap = min(fz.brems_max(a, ka*a*a) + fz.widerstand(a),
                      fz.brems_max(b, kb*b*b) + fz.widerstand(b))
        else:
            cap = min(fz.antrieb_max(a, ka*a*a) - fz.widerstand(a),
                      fz.antrieb_max(b, kb*b*b) - fz.widerstand(b))
        if (b*b-a*a)/(2*ds) <= cap:
            return b
        low, high = a, b
        for _ in range(24):
            mid = (low + high) / 2
            if bremsen:
                cap = min(fz.brems_max(a, ka*a*a) + fz.widerstand(a),
                          fz.brems_max(mid, kb*mid*mid) + fz.widerstand(mid))
            else:
                cap = min(fz.antrieb_max(a, ka*a*a) - fz.widerstand(a),
                          fz.antrieb_max(mid, kb*mid*mid) - fz.widerstand(mid))
            if (mid*mid - a*a) / (2*ds) <= cap:
                low = mid
            else:
                high = mid
        return low

    # Beide Hüllkurven werden gekoppelt, einschließlich der Naht n-1 -> 0.
    for durchlauf in range(100):
        vorher = v.copy()
        for i in range(n):
            j = (i+1) % n
            v[j] = erreichbar(v[i], v[j], k[i], k[j], False)
        for i in range(n-1, -1, -1):
            j = (i+1) % n
            v[i] = erreichbar(v[j], v[i], k[j], k[i], True)
        if np.max(vorher-v) < 1e-6:
            break
    else:
        raise RuntimeError("Speed envelope does not converge")
    vollgas = np.zeros(n, dtype=bool)
    v_next = np.roll(v, -1)
    a_laengs = (v_next ** 2 - v ** 2) / (2.0 * ds)
    a_quer = k * v ** 2
    vollgas = a_laengs > 0.98 * np.array([
        fz.antrieb_max(vi, ayi) - fz.widerstand(vi) for vi, ayi in zip(v, a_quer)])
    dt = 2.0 * ds / (v + v_next)
    zeit = np.concatenate([[0.0], np.cumsum(dt)[:-1]])
    return {"v": v, "a_laengs": a_laengs, "a_quer": a_quer, "zeit": zeit,
            "rundenzeit": float(np.sum(dt)), "vollgas": vollgas, "v_kurve": vk}


# =========================================================================== Mindestzeit-Feinschliff
def _linien_rundenzeit(ml, alpha, fz, glaettung_m=4.0, schritt=1.0):
    """Rundenzeit einer Linie (Versatz ``alpha`` auf dem feinen Bezugsgitter) mit geglätteter Krümmung.
    1-m-Abtastung: bei 2 m schwankt die Zeit derselben Linie je nach Stützstellenlage um ±0,07 s (1 m: ±0,01 s);
    eine Suche würde dieses Rauschen als Gewinn ausnutzen."""
    pkt = ml.ref.punkt(alpha)
    pkt[:, 1] = ml.hoehe_quer(ml.ref, alpha)
    spl = PeriodischerSpline(pkt)
    n = max(64, int(round(spl.laenge / schritt)))
    h = spl.laenge / n
    tau = spl.param_aus_s(np.arange(n) * h)
    p, d1, d2 = spl.auswerten(tau), spl.auswerten(tau, 1), spl.auswerten(tau, 2)
    k = (d2[:, 0] * d1[:, 2] - d2[:, 2] * d1[:, 0]) / np.hypot(d1[:, 0], d1[:, 2]) ** 3
    if glaettung_m > 0:
        k = periodisch_gauss(k, glaettung_m / h)
    return profil_berechnen(k, h, fz, hoehe=p[:, 1])["rundenzeit"]


def _platz(alpha, lo, hi, form, richtung):
    """Größte Amplitude, mit der ``alpha + richtung * amp * form`` (form ≥ 0) die Grenzen nicht berührt."""
    traeger = form > 1e-3
    if not traeger.any():
        return 0.0
    rand = (hi - alpha) if richtung > 0 else (alpha - lo)
    return max(0.0, float(np.min(rand[traeger] / form[traeger])))


#: Scheitelsuche: Lagen relativ zum Zentrum (m, positiv = später), Halbbreiten (m; dazu Anteile der Kurvenlänge),
#: Formen (Exponent der Glocke: 2 = Gauß, 4 = flacher Boden), Tiefen (Anteil des Platzes bis zur Grenze).
SCHEITEL_LAGEN = (-20.0, 0.0, 20.0)
SCHEITEL_BREITEN = (25.0, 45.0, 90.0)
SCHEITEL_BREITEN_ANTEIL = (0.4, 0.6)
SCHEITEL_FORMEN = (2, 4)
SCHEITEL_TIEFEN = (0.5, 1.0)


def _probe_zeit(auftrag):
    """Arbeitsprozess der Scheitelsuche: Rundenzeit einer Probelinie (alpha, Schritt); gemeinsam: (ml, fz)."""
    alpha, schritt = auftrag
    ml, fz = parallel.gemeinsam()
    return _linien_rundenzeit(ml, alpha, fz, schritt=schritt)


def scheitel_suchen(ml, alpha, lo, hi, fz, kurven, min_winkel_grad=45.0, min_gewinn_s=0.03, durchgaenge=2,
                    nachpruefen=3):
    """Je Kurve (Richtungsänderung ≥ ``min_winkel_grad``) ganze Scheitelwege zum Innenrand ausprobieren und den
    schnellsten übernehmen, wenn er die Runde verkürzt (25.10.2026).

    Minimale Krümmung legt enge Kehren und lange Kurven an den Außenrand (Editor-Strecke Turn 4/11/14; TUM-Strecken:
    größte Abweichungen in 157–205°-Kurven), die schnellere Linie nimmt den Scheitel innen. Kleine Schritte
    (``mindestzeit_verfeinern``) finden das nicht, weil der Weg nach innen erst langsamer, dann schneller wird.
    Zentren: Punkt stärkster Krümmung und Kurvenmitte (lange Kurven: der Krümmungsgipfel liegt oft am Ende).
    Tiefe so begrenzt, dass die Linie nirgends eine Grenze berührt (Kappen/Sättigen erzeugt Krümmung).
    Vorsieben mit 2-m-Bewertung, die besten ``nachpruefen`` Kandidaten mit 1 m (Abtastrauschen ±0,07 s bzw. ±0,01 s).
    Seit 26.10.2026 werden die Probelinien einer Kurve parallel bewertet (``ego.parallel``; Kurven weiter
    nacheinander, weil jede auf der Linie der vorigen aufbaut) – Ergebnis wie seriell."""
    s, L = ml.ref.s, ml.laenge
    alpha = np.asarray(alpha, float).copy()
    best = _linien_rundenzeit(ml, alpha, fz)
    start, protokoll = best, []
    kandidaten = [(nr, kv) for nr, kv in enumerate(kurven) if abs(kv["winkel_grad"]) >= min_winkel_grad]
    with parallel.Arbeitsgruppe((ml, fz), prozesse=None if kandidaten else 1) as gruppe:
        for durchgang in range(durchgaenge):
            verbessert = []
            for nr, kv in kandidaten:
                innen = -1.0 if kv["richtung"] == "rechts" else 1.0
                lang = ((kv["i_ende"] - kv["i_anfang"]) * ml.ds) % L
                zentren = {("Krümmungsgipfel", kv["i_scheitel"] * ml.ds),
                           ("Kurvenmitte", (kv["i_anfang"] * ml.ds + lang / 2) % L)}
                breiten = sorted(set(SCHEITEL_BREITEN) | {round(max(20.0, f * lang)) for f in SCHEITEL_BREITEN_ANTEIL})
                proben = []
                for zname, zentrum in zentren:
                    for lage in SCHEITEL_LAGEN:
                        u0 = np.abs((s - (zentrum + lage) + L / 2) % L - L / 2)
                        for halb in breiten:
                            for form in SCHEITEL_FORMEN:
                                delle = np.exp(-0.5 * (u0 / halb) ** form)
                                delle[delle < 1e-4] = 0.0
                                tiefe_max = 0.98 * _platz(alpha, lo, hi, delle, innen)
                                if tiefe_max < 0.5:
                                    continue
                                for tiefe in SCHEITEL_TIEFEN:
                                    proben.append((alpha + innen * tiefe * tiefe_max * delle,
                                                   (zname, lage, halb, form, tiefe * tiefe_max)))
                # erste Bewertung = bisherige Linie (Grundwert des Siebs), dann alle Proben
                grob_basis, *grob = gruppe.abbilden(_probe_zeit, [(alpha, 2.0)] + [(p, 2.0) for p, _ in proben], 2)
                sieb = [(g, probe, para) for g, (probe, para) in zip(grob, proben) if g < grob_basis]
                beste = sorted(sieb, key=lambda x: x[0])[:nachpruefen]
                wahl = None
                for (_, probe, para), t in zip(beste, gruppe.abbilden(_probe_zeit, [(p, 1.0) for _, p, _ in beste], 1)):
                    if t < best - min_gewinn_s and (wahl is None or t < wahl[0]):
                        wahl = (t, probe, para)
                if wahl is not None:
                    best, alpha = wahl[0], wahl[1]
                    verbessert.append((nr, kv))
                    zname, lage, halb, form, tiefe = wahl[2]
                    protokoll.append({"kurve": nr + 1, "durchgang": durchgang + 1, "zentrum": zname, "lage_m": lage,
                                      "halbbreite_m": halb, "form": form, "tiefe_m": round(float(tiefe), 2),
                                      "rundenzeit_s": round(float(best), 3)})
            if not verbessert:
                break
            kandidaten = verbessert          # weitere Durchgänge nur dort, wo es noch weiter nach innen gehen kann
    return alpha, {"rundenzeit_vorher_s": float(start), "rundenzeit_nachher_s": float(best),
                   "gewinn_s": float(start - best), "kurven": protokoll}


def mindestzeit_verfeinern(ml, alpha, lo, hi, fz, kurven, schritte=(3.0, 1.5, 0.75), min_gewinn_s=0.03):
    """Reduzierte Mindestzeit-Optimierung ausgehend von der Minimal-Krümmungs-Linie (25.10.2026).

    Je Kurve drei glatte Verformungen (Gauß-Glocken an Einfahrt, Scheitel, Ausfahrt, Breite ein Drittel der
    Kurvenlänge); Koordinatensuche mit fallender Schrittweite, übernommen nur bei kürzerer Rundenzeit. Die
    Amplitude wird so begrenzt, dass die Linie die Grenzen [lo, hi] nie erreicht (kein Kappen, keine Knicke).
    Minimale Krümmung legt U- und V-Kehren an den Außenrand; die Zeitbewertung zieht sie nach innen (enger, kürzer),
    wo das schneller ist. Verworfen (siehe docs/ideallinie_bremspunkte.md): Krümmungsgewichte, Mischung mit dem
    kürzesten Weg (global und je Kurve langsamer bzw. ohne Gewinn)."""
    s, L = ml.ref.s, ml.laenge
    basen = []
    for kv in kurven:
        a, e, sc = kv["i_anfang"] * ml.ds, kv["i_ende"] * ml.ds, kv["i_scheitel"] * ml.ds
        lang = max(20.0, (e - a) % L)
        breite = max(10.0, lang / 3)
        for lage in (a - 0.25 * lang, sc, e + 0.25 * lang):
            u = (s - lage % L + L / 2) % L - L / 2
            b = np.exp(-0.5 * (u / breite) ** 2)
            b[b < 1e-4] = 0.0
            basen.append(b)
    alpha = np.asarray(alpha, float).copy()
    start = best = _linien_rundenzeit(ml, alpha, fz)
    versuche = angenommen = 0
    for schritt in schritte:
        for b in basen:
            for richtung in (1.0, -1.0):
                amp = min(schritt, 0.98 * _platz(alpha, lo, hi, b, richtung))
                if amp < 0.05:
                    continue
                probe = alpha + richtung * amp * b
                versuche += 1
                t = _linien_rundenzeit(ml, probe, fz)
                if t < best - min_gewinn_s:
                    best, alpha = t, probe
                    angenommen += 1
                    break
    return alpha, {"rundenzeit_vorher_s": float(start), "rundenzeit_nachher_s": float(best),
                   "gewinn_s": float(start - best), "versuche": versuche, "angenommen": angenommen,
                   "verformungen": len(basen), "fahrzeug": getattr(fz, "param", {}).get("parametersatz")}


# =========================================================================== Ideallinie
class Ideallinie:
    """Ideallinie, gleichmäßig nach ihrer eigenen Bogenlänge abgetastet."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def ideallinie_berechnen(ml, param):
    """Ideallinie + Geschwindigkeitsprofil zu einer Mittellinie."""
    pi = param["ideallinie"]
    zusatz = {}
    if int(pi.get("iterationen", 1)) > 1:
        zusatz["iterationen"] = int(pi["iterationen"])
        zusatz["toleranz_m"] = float(pi.get("iterationen_toleranz_m", 0.01))
    if pi.get("feine_rueckprojektion"):
        zusatz["feine_rueckprojektion"] = True
    if pi.get("kruemmung_max") is not None:
        zusatz["kruemmung_max"] = float(pi["kruemmung_max"])
        if not zusatz["kruemmung_max"] > 0:
            raise ValueError("ideallinie.kruemmung_max must be > 0")
    opt = minimale_kruemmung(ml, schritt=pi["schritt_m"], sicherheit=pi["sicherheitsabstand_m"],
                             **zusatz)
    st = ml.ref
    L = ml.laenge
    if "alpha_fein" in opt:      # iterativ: Versatz schon auf dem feinen Gitter
        alpha = opt["alpha_fein"].copy()
    else:
        alpha = periodisch_pchip(st.s, opt["s"], opt["alpha"], L)
    # Schranken auf dem feinen Gitter einhalten (PCHIP bleibt zwischen Stützwerten,
    # die Schranken selbst ändern sich aber mit der Breite)
    lo = -(st.wr - pi["sicherheitsabstand_m"])
    hi = st.wl - pi["sicherheitsabstand_m"]
    if np.any(lo > hi):
        raise ValueError("Road too narrow for the safety margin")
    if "alpha_fein" not in opt:
        alpha = np.clip(alpha, lo, hi)
    if pi.get("mindestzeit"):
        from .mittellinie import kurven_finden
        from .definition import STANDARD
        pk = param["kurven"]
        kurven = kurven_finden(ml, pk["kruemmung_schwelle"], pk["mindestwinkel_grad"], pk["zusammenfassen_m"])
        satz = pi.get("mindestzeit_fahrzeug")
        fz_mz = fahrzeug_erzeugen(dict(STANDARD["fahrzeug"], parametersatz=satz) if satz else param["fahrzeug"])
        # voller Sicherheitsabstand; die Startlinie (iterativ: wenige cm darüber möglich) bleibt zulässig
        lo_mz, hi_mz = np.minimum(lo, alpha), np.maximum(hi, alpha)
        alpha, scheitel = scheitel_suchen(ml, alpha, lo_mz, hi_mz, fz_mz, kurven)
        alpha, opt["info"]["mindestzeit"] = mindestzeit_verfeinern(ml, alpha, lo_mz, hi_mz, fz_mz, kurven)
        opt["info"]["mindestzeit"]["scheitel"] = scheitel
        opt["info"]["mindestzeit"]["rundenzeit_vorher_s"] = scheitel["rundenzeit_vorher_s"]
        opt["info"]["mindestzeit"]["gewinn_s"] = scheitel["rundenzeit_vorher_s"] - opt["info"]["mindestzeit"]["rundenzeit_nachher_s"]
    pkt = st.punkt(alpha)
    pkt[:, 1] = ml.hoehe_quer(st, alpha)
    spl = PeriodischerSpline(pkt)
    Lr = spl.laenge
    n = max(16, int(round(Lr / ml.ds)))
    ds = Lr / n
    s = np.arange(n) * ds
    tau = spl.param_aus_s(s)
    pos = spl.auswerten(tau)
    d1 = spl.auswerten(tau, 1)
    d2 = spl.auswerten(tau, 2)
    vx, vz = d1[:, 0], d1[:, 2]
    v2 = np.hypot(vx, vz)
    kappa_roh = (d2[:, 0] * vz - d2[:, 2] * vx) / v2 ** 3
    sigma = pi.get("kruemmung_glaettung_m", 0.0) / ds
    kappa = periodisch_gauss(kappa_roh, sigma) if sigma > 0 else kappa_roh
    # Zuordnung zur Mittellinie (Knoten k des Splines = Station k der Mittellinie)
    t_ext = spl.t
    s_ml = np.interp(tau, t_ext, np.concatenate([st.s, [L]])) % L
    versatz = np.interp(tau, t_ext, np.concatenate([alpha, [alpha[0]]]))
    fz = fahrzeug_erzeugen(param["fahrzeug"])
    # Beide Modelle rechnen mit der geglätteten Krümmung (``kruemmung_glaettung_m``): kappa_roh enthält
    # Knotenrauschen (Portimão-Form 10× rauer als die KI-Linie des Spiels), das Scheinkurven mit
    # 30–60 km/h Verlust und Scheinbremszonen erzeugt. Seit 25.10.2026 auch für die Punktmasse.
    prof = profil_berechnen(kappa, ds, fz, hoehe=pos[:, 1])
    richtung = np.column_stack([vx / v2, vz / v2])
    return Ideallinie(s=s, ds=ds, laenge=Lr, pos=pos, t=richtung, kappa=kappa, kappa_roh=kappa_roh,
                      s_ml=s_ml, versatz=versatz, v=prof["v"], a_laengs=prof["a_laengs"],
                      a_quer=prof["a_quer"], zeit=prof["zeit"], rundenzeit=prof["rundenzeit"],
                      vollgas=prof["vollgas"], v_kurve=prof["v_kurve"], fahrzeug=fz,
                      optimierung=opt, alpha_ml=alpha)


def rundenzeit_text(sek):
    m = int(sek // 60)
    return "%d:%06.3f" % (m, sek - 60 * m)
