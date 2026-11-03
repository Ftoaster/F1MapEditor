"""Neutrale Streckenlogik im Zuschnitt von TrackSpace, AISplineData, TS, TM und pkg.

Alle Fortschrittswerte beziehen sich auf die 3D-Polygonbogenlänge der ausgegebenen
TrackSpace-Spline ab Knoten 0. Die Hauptlinie ist geschlossen, pit_1 offen.
Koordinaten: Meter, XYZ, Y oben, rechtshändig; KI-Normalen zeigen nach rechts.
Die Regeln für erzeugte Zonen sind Entwurfsentscheidungen, keine Spieltestbelege.
"""

import uuid

import numpy as np

from . import zonen
from .mittellinie import laeufe, periodisch_interp
from .geometrie import STARTPLATZ_QUER_M
from .querprofil import boxen_spurversatz, boxen_u


def _einheit(v):
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-12)


def _punkt(ml, s, d):
    st = ml.stationen(np.atleast_1d(s))
    d = np.broadcast_to(d, st.s.shape)
    p = st.punkt(d)
    p[:, 1] = ml.hoehe_quer(st, d)
    return p


def _polygon_s(pos, geschlossen):
    p = np.vstack([pos, pos[:1]]) if geschlossen else pos
    return np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(p, axis=0), axis=1))]


class Fortschritt:
    """Zuordnung Entwurfsbogenlänge -> Bogenlänge der tatsächlich exportierten Knoten."""

    def __init__(self, s, pos, geschlossen=False, laenge=None):
        self.s = np.r_[s, laenge] if geschlossen else np.asarray(s)
        self.bogen = _polygon_s(pos, geschlossen)
        self.laenge = float(self.bogen[-1])
        self.periode = laenge if geschlossen else None

    def an(self, s):
        s = np.asarray(s, dtype=float)
        if self.periode is not None:
            s = np.mod(s, self.periode)
        return np.interp(s, self.s, self.bogen) / self.laenge


def _matrix(pos, vorwaerts, oben=(0.0, 1.0, 0.0)):
    """pkg-Zeilenvektormatrix; lokale +Z vorwärts, -X rechts, +Y oben.

    Beleg: Bahrain-VehiclePlacement, alle 22 GridSlot-Matrizen mit +Z entlang
    der Startgeraden zur Pole (Skalarprodukt 0,99909…0,99948). Kein Spieltest.
    """
    t = _einheit(np.asarray(vorwaerts, float))
    rechts = _einheit(np.cross(t, oben))
    up = _einheit(np.cross(rechts, t))
    m = np.eye(4)
    m[:3, :3] = [-rechts, up, t]
    m[3, :3] = pos
    return m.ravel().tolist()


def _tor(ml, qp, s, param):
    st = ml.stationen(np.atleast_1d(s))
    p, _ = qp.an(st.s, ml.laenge)
    aussen = param['barriere']['dicke']
    ds = (-(st.wr + p['rechts']['e'][:, 5] + aussen), -st.wr,
          st.wl, st.wl + p['links']['e'][:, 5] + aussen)
    pkt = [_punkt(ml, st.s, d) for d in ds]
    return [dict(zip(('point1', 'point2', 'point3', 'point4'),
                     [v[i].tolist() for v in pkt])) for i in range(len(st.s))]


def _volumes(tore, geschlossen, name, typ):
    n = len(tore) if geschlossen else len(tore)-1
    return {'name': name, 'type': typ, 'closed': geschlossen, 'numVolumes': n,
            'volumes': [{'id': i, 'gates': [tore[i], tore[(i+1) % len(tore)]]}
                        for i in range(n)]}


def _querabstand(pos, normale, rand, index, erwartet):
    """Schnitt mit benachbarten Randsegmenten statt nur Projektion auf die Normale."""
    n = len(rand)
    idx = (index + np.arange(-70, 71)) % n
    a, b = rand[idx], rand[(idx+1) % n]
    e = b[:, [0, 2]] - a[:, [0, 2]]
    ao = a[:, [0, 2]] - pos[[0, 2]]
    d = normale[[0, 2]]
    den = d[0]*e[:, 1] - d[1]*e[:, 0]
    with np.errstate(divide='ignore', invalid='ignore'):
        ell = (ao[:, 0]*e[:, 1]-ao[:, 1]*e[:, 0]) / den
        u = (ao[:, 0]*d[1]-ao[:, 1]*d[0]) / den
    ok = (np.abs(den)>1e-12) & (u>=-1e-8) & (u<=1+1e-8)
    if not np.any(ok):
        raise ValueError('KI-Gate schneidet eine Streckengrenze nicht')
    kandidaten = ell[ok]
    return float(kandidaten[np.argmin(np.abs(kandidaten-erwartet))])


def _boxpfad(ml, qp, rl, param):
    if qp.box is None:
        return None
    pb = param['boxengasse']
    L, U = ml.laenge, qp.box['laenge_entlang_strecke']
    start = qp.box['s_einfahrt']
    # 30 m Fortsetzung in Hauptfahrbahn vor Ein- und nach Ausfahrt.
    u = np.linspace(-30, U+30, int(np.ceil(U+60))+1)
    s = start + u
    st = ml.stationen(s)
    prof, _ = qp.an(s, L)
    e = prof[pb['seite']]['e']
    vz = 1 if pb['seite']=='links' else -1
    w = st.wl if vz>0 else st.wr
    d = vz*(w + (e[:, 1]+e[:, 2])/2)
    alpha = periodisch_interp(st.s, ml.s, rl.alpha_ml, L)
    blend = np.minimum(np.clip((u+30)/30, 0, 1), np.clip((U+30-u)/30, 0, 1))
    # Boxenlinie auf der schnellen Spur: im vollbreiten Abschnitt zur Strecke hin versetzt, mit schmaler Fahrspur. Die Box
    # liegt damit wie bei allen Originalen außerhalb der Spur (spur_versatz_m). Mitte der Gasse bleibt Bezug für die Boxen.
    r = np.clip(np.minimum(u-pb['anlauf_m'], U-pb['anlauf_m']-u)/pb['uebergang_m'], 0, 1)
    r = r*r*(3-2*r)
    v_spur = boxen_spurversatz(pb)*r
    mitte = _punkt(ml, s, blend*d + (1-blend)*alpha)
    d = d-vz*v_spur
    d = blend*d + (1-blend)*alpha
    pos = _punkt(ml, s, d)
    # Querschnitt der offenen Spline bleibt an den Anschlussknoten nicht entartet.
    breite = e[:, 2]-e[:, 1]
    ueber = np.minimum(np.clip(u/pb['anlauf_m'], 0, 1),
                       np.clip((U-u)/pb['anlauf_m'], 0, 1))
    breite = np.maximum(breite, (1-ueber)*(st.wl+st.wr))
    spur_halb = breite/2
    if v_spur.max() > 0:
        spur_halb = (1-r)*spur_halb+r*np.minimum(spur_halb, float(pb['spur_halbbreite_m']))
    sb = _polygon_s(pos, False)
    t = _einheit(np.gradient(pos, axis=0))
    normal = _einheit(np.cross(t, [0, 1, 0]))
    return {'position': pos, 'mitte': mitte, 's': sb, 'main_s': s, 'u': u, 'breite': breite, 'spur_halb': spur_halb,
            'spur_v': v_spur,
            't': t, 'normal': normal, 'laenge': float(sb[-1]),
            'einfahrt_s': float(np.interp(0, u, sb)),
            'ausfahrt_s': float(np.interp(U, u, sb)), 'offset': d}


def _offen_an(box, s, feld):
    v = np.asarray(box[feld])
    if v.ndim == 1:
        return np.interp(s, box['s'], v)
    return np.array([np.interp(s, box['s'], v[:, i]) for i in range(v.shape[1])]).T


#: Ausdehnung der Träger-Tafel quer zur Fläche (lokal −Z, Modell por_dressing_brake_marker_a: Z −0,88 … 0,06 m)
BREMSMARKER_TIEFE_M = 0.9


def bremsmarker_berechnen(ml, qp, rl, kurven, param):
    """Entfernungstafeln vor Kurven mit starker Bremsung (25.10.2026).

    Je Kurve: Geschwindigkeitsabbau = höchste Geschwindigkeit auf der Geraden davor minus kleinste in der Kurve
    (Generatorprofil der Ideallinie). Tafeln in ``abstaende_m`` vor dem Kurvenanfang (Mittellinie): 100 m ab
    ``min_abbau_kmh`` Abbau, je 50 m mehr Tafel 50 km/h mehr Abbau (250 m ab 200 km/h); die 50-m-Tafel steht wie
    die 100-m-Tafel an jeder Kurve ab ``min_abbau_kmh``; jeweils soweit die Gerade (−20 m) reicht. Seite der Ideallinie (außen),
    ``rand_m`` neben dem Fahrbahnrand, höchstens bis 1,2 m vor die Barriere; Boxenseite im Boxenbereich wird
    gemieden; Tafeln näher als 4 m an einem anderen Streckenteil entfallen. Matrix im pkg-Layout (Zeilenvektor,
    Translation 12–14): lokales X gegen die Fahrtrichtung (Tafelfläche zu den Fahrern wie in Portimão), Y oben."""
    pb = param.get('bremsmarker') or {}
    if not pb.get('aktiv', True) or not kurven:
        return []
    L, ds = ml.laenge, ml.ds
    s_rl = np.asarray(rl.s_ml, float)
    v, a_l = np.asarray(rl.v, float), np.asarray(rl.a_laengs, float)
    alpha = rl.alpha_ml
    box = getattr(qp, 'box', None) or {}
    mitte = ml.ref.pos[:, [0, 2]]
    ordnung = sorted(kurven, key=lambda k: k['i_anfang'])
    out = []
    for n, kv in enumerate(ordnung):
        s_k = kv['i_anfang'] * ds
        vorher = ordnung[n - 1]
        gerade = ((s_k - vorher['i_ende'] * ds) % L) if len(ordnung) > 1 else L
        d = np.mod(s_k - s_rl, L)                                   # Meter vor dem Kurvenanfang
        auf_gerade = d <= gerade
        in_kurve = np.mod(s_rl - s_k, L) <= max(10.0, (kv['i_ende'] - kv['i_anfang']) * ds)
        if not auf_gerade.any() or not in_kurve.any():
            continue
        abbau = (float(v[auf_gerade].max()) - float(v[in_kurve].min())) * 3.6
        if abbau < float(pb.get('min_abbau_kmh', 50.0)):
            continue
        bremst = auf_gerade & (a_l < -1.5)
        bremsweg = float(d[bremst].max()) if bremst.any() else 0.0
        for meter in sorted(int(m) for m in pb.get('abstaende_m', [50, 100, 150, 200, 250])):
            # Countdown wie auf echten Strecken: je 50 m mehr Tafel 50 km/h mehr Abbau (50/100 m ab min_abbau_kmh)
            if abbau < float(pb.get('min_abbau_kmh', 50.0)) + max(0, meter - 100) or meter > gerade - 20.0:
                continue
            s_b = (s_k - meter) % L
            st = ml.stationen([s_b])
            seite = 1 if float(periodisch_interp([s_b], ml.s, alpha, L)[0]) >= 0 else -1
            name = 'links' if seite > 0 else 'rechts'
            if box and box.get('seite') == name:
                a0 = float(box['s_einfahrt']) - 50.0
                if (s_b - a0) % L <= float(box['laenge_entlang_strecke']) + 100.0:
                    seite, name = -seite, ('rechts' if seite > 0 else 'links')
            prof, _ = qp.an([s_b], L)
            breite = float(st.wl[0] if seite > 0 else st.wr[0])
            barriere = float(prof[name]['e'][0][5])
            rand = max(1.5, min(float(pb.get('rand_m', 6.0)), barriere - 1.2))
            # Tafel ragt 0,88 m nach lokal −Z (= rechts); links daher um diese Breite weiter nach außen
            pos = _punkt(ml, s_b, seite * (breite + rand + (BREMSMARKER_TIEFE_M if seite > 0 else 0.0)))[0]
            # Abstand zu anderen Streckenteilen (Kehren, Parallelabschnitte)
            fern = np.abs((ml.ref.s - s_b + L / 2) % L - L / 2) > meter + 150.0
            if fern.any() and float(np.min(np.linalg.norm(mitte[fern] - pos[[0, 2]], axis=1))) <                     max(float(np.max(ml.wl)), float(np.max(ml.wr))) + 4.0:
                continue
            t = np.array([st.t3[0][0], 0.0, st.t3[0][2]])
            x = -t / np.linalg.norm(t)
            y = np.array([0.0, 1.0, 0.0])
            z = np.cross(x, y)
            out.append({'kurve': int(kurven.index(kv)), 'meter': meter, 'seite': name,
                        's_m': float(s_b), 'abbau_kmh': round(abbau, 1), 'bremsweg_m': round(bremsweg, 1),
                        'position': pos.tolist(),
                        'matrix': x.tolist() + [0.0] + y.tolist() + [0.0] + z.tolist() + [0.0] + pos.tolist() + [1.0]})
    return out


def logik_berechnen(ml, qp, rl, param, kurven):
    """Liefert vollständig JSON-fähige Felder für spätere Binärformat-Adapter."""
    L = ml.laenge
    pl = param['logik']
    schritt = float(pl['trackspace_schritt_m'])
    if not 7 <= schritt <= 11:
        raise ValueError('TrackSpace-Schritt muss 7–11 m betragen')
    ns = max(3, int(round(L/schritt)))
    s_ts = np.arange(ns)*L/ns
    pos_ts = ml.stationen(s_ts).pos
    fort = Fortschritt(s_ts, pos_ts, True, L)
    splines = [{'name': 'maintrack', 'closed': True, 'position': pos_ts.tolist(),
                'progress': fort.an(s_ts).tolist(), 'laenge_m': fort.laenge}]
    box = _boxpfad(ml, qp, rl, param)
    pitfort = None
    if box is not None:
        nb = max(1, int(round(box['laenge']/schritt)))
        s_pit = np.linspace(0, box['laenge'], nb+1)
        pitpos = _offen_an(box, s_pit, 'position')
        pitfort = Fortschritt(s_pit, pitpos)
        splines.append({'name': 'pit_1', 'closed': False, 'position': pitpos.tolist(),
                        'progress': pitfort.an(s_pit).tolist(), 'laenge_m': pitfort.laenge})

    # Sektorgrenzen nahe Dritteln auf möglichst geraden Abschnitten, nie überlappend.
    if param['sektoren']['anzahl'] != 3:
        raise ValueError('Die Spielformate erwarten drei Sektoren')
    grenzen = [0.0]
    feste = param['sektoren'].get('grenzen_m')
    for frac in (1/3, 2/3) if not feste else ():
        radius = min(float(param['sektoren']['suchbereich_anteil']), 0.12)*L
        kand = np.flatnonzero(np.abs(ml.s-frac*L)<=radius)
        wert = np.abs(ml.kappa_glatt[kand]) + 1e-7*np.abs(ml.s[kand]-frac*L)
        grenzen.append(float(ml.s[kand[np.argmin(wert)]]))
    if feste:
        if len(feste) != 2 or not 0 < float(feste[0]) < float(feste[1]) < L:
            raise ValueError('sektoren.grenzen_m braucht zwei Werte 0 < a < b < Länge (Meter ab Ziel)')
        grenzen += [float(feste[0]), float(feste[1])]
    grenzen.append(L)
    sektoren = [{'nummer': i+1, 's_start_m': grenzen[i], 's_ende_m': grenzen[i+1],
                 'Start': float(fort.an(grenzen[i])),
                 'End': 1.0 if i==2 else float(fort.an(grenzen[i+1]))} for i in range(3)]

    # KI-Randlinien werden auf dem dichten Bezugsgitter mit echter Querschnittshöhe gebaut.
    st = ml.ref
    sicher = param['ideallinie']['sicherheitsabstand_m']
    weiss = param['fahrbahn']['randlinie_breite']/2
    # Racing-Grenzen schließen die Ideallinie immer ein: die iterative Linie hält den Abstand euklidisch,
    # entlang der Mittellinien-Normalen kann sie wenige cm über wl-sicher hinausragen.
    grenzen_d = {'left_racing_limit': np.maximum(st.wl-sicher, rl.alpha_ml),
                 'right_racing_limit': np.minimum(-st.wr+sicher, rl.alpha_ml),
                 'left_track_limit': st.wl, 'right_track_limit': -st.wr,
                 'left_white_line': st.wl-weiss, 'right_white_line': -st.wr+weiss,
                 'left_run_off': st.wl+np.maximum(qp.seiten['links']['e'][:, 5]-1, 0),
                 'right_run_off': -st.wr-np.maximum(qp.seiten['rechts']['e'][:, 5]-1, 0)}
    raender = {name: _punkt(ml, ml.s, d) for name, d in grenzen_d.items()}
    ng = max(16, int(round(L/pl['gate_schritt_m'])))
    sg = np.arange(ng)*L/ng
    gst = ml.stationen(sg)
    alpha = periodisch_interp(sg, ml.s, rl.alpha_ml, L)
    gpos = _punkt(ml, sg, alpha)
    gt = periodisch_interp(sg, rl.s_ml, rl.t, L)
    normal = _einheit(np.column_stack([-gt[:, 1], np.zeros(ng), gt[:, 0]]))
    typen = ('left_racing_limit', 'right_racing_limit', 'left_track_limit', 'right_track_limit',
             'racing_line', 'left_white_line', 'right_white_line', 'left_run_off', 'right_run_off')
    gates = []
    for i, (p, norm, ss, aa) in enumerate(zip(gpos, normal, sg, alpha)):
        idx = int(round(ss/ml.ds)) % ml.anzahl
        weg = []
        for j, typ in enumerate(typen):
            erwartet = 0 if typ=='racing_line' else aa-grenzen_d[typ][idx]
            ell = 0.0 if typ=='racing_line' else _querabstand(p, norm, raender[typ], idx, erwartet)
            # Am aktiven Optimierungsrand kann die 1-m-Randsehne wenige mm
            # innerhalb des exakten Splines liegen. Gate selbst bleibt zulässig.
            if typ=='left_racing_limit' and 0 < ell < 0.01:
                ell = 0.0
            if typ=='right_racing_limit' and -0.01 < ell < 0:
                ell = 0.0
            weg.append({'id': j, 'type': typ, 'length': ell})
        gates.append({'id': i, 'name': 'ai_gate_track_%03d' % i, 'position': p.tolist(),
                      'normal': norm.tolist(), 'progress': float(fort.an(ss)), 'waypoints': weg})
    links = [{'id': 0, 'from_gate': ng-1, 'to_gate': 0}]
    links += [{'id': i, 'from_gate': i-1, 'to_gate': i} for i in range(1, ng)]
    routes = [{'id': 0, 'type': 'track', 'from_link': 0, 'to_link': ng-1,
               'from_route': 'track', 'to_route': 'track'}]
    ausfahrt_spur_ist = 0.0
    if box is not None:
        nbg = max(2, int(round(box['laenge']/pl['gate_schritt_m']))+1)
        sbg = np.linspace(0, box['laenge'], nbg)
        pb = param['boxengasse']
        spur = float(pb.get('ausfahrt_spur_m', 0.0))
        seite = 1.0 if pb['seite'] == 'links' else -1.0
        s_aus = float(box['main_s'][-1])
        if spur > 0:
            # Boxenroute endet, wo die Gasse die Spur am Fahrbahnrand erreicht; ab dort übernimmt die Ausfahrtsspur
            ms = _offen_an(box, sbg, 'main_s')
            mitte = _punkt(ml, ms % L, np.zeros(nbg))
            links1 = _punkt(ml, ms % L, np.ones(nbg)) - mitte
            quer = np.einsum('ij,ij->i', _offen_an(box, sbg, 'position') - mitte, links1)
            rand = np.where(seite > 0, ml.stationen(ms % L).wl, ml.stationen(ms % L).wr) - 3.0
            drin = np.flatnonzero((np.arange(nbg) > nbg // 2) & (seite*quer < rand))
            if len(drin):
                nbg = max(2, int(drin[0]))
                sbg = sbg[:nbg]
                s_aus = float(ms[nbg-1])
        bpos = _offen_an(box, sbg, 'position')
        bnorm = _einheit(_offen_an(box, sbg, 'normal'))
        bw = _offen_an(box, sbg, 'spur_halb')
        for i in range(nbg):
            weg = []
            for j, typ in enumerate(typen[:7]):
                if typ=='racing_line':
                    val = 0.0
                else:
                    inset = min(sicher, bw[i]*0.4) if 'racing_limit' in typ else (weiss if 'white' in typ else 0)
                    val = (-1 if typ.startswith('left') else 1)*(bw[i]-inset)
                weg.append({'id': j, 'type': typ, 'length': float(val)})
            gates.append({'id': ng+i, 'name': 'ai_gate_track_pit_%03d' % i,
                          'position': bpos[i].tolist(), 'normal': bnorm[i].tolist(),
                          'progress': float(pitfort.an(sbg[i])), 'waypoints': weg})
        # Ausfahrtsspur auf der Strecke: erst am Rand der Boxenseite, dann weich auf die Ideallinie (definition.ausfahrt_spur_m)
        s0 = s_aus
        if spur > 0 and kurven:
            # Die Spur endet 30 m vor dem Bremspunkt der nächsten Kurve: Auf ihr gelten die Fahrdaten der Boxenroute, die keine
            # Kurve kennen (Spieltest 3.11.2026, „Kurz Links“: KI mit Ausfahrtstempo durch T1 in den Kies)
            bremsen = []
            for kv in kurven:
                a = kv['i_anfang']*ml.ds
                dist = np.mod(a-rl.s_ml, L)
                kand = np.flatnonzero((dist < 250) & (rl.a_laengs < -0.5))
                b = float(rl.s_ml[kand[np.argmax(dist[kand])]]) if len(kand) else a
                # Das Fahrzeugmodell bremst später als die KI im Spiel (T1 „Kurz Links“: 38 m), daher auch 120 m vor dem Einlenken
                bremsen.append((min(b - 30.0, a - 120.0) - s0) % L)
            frei = min(0.0 if d > L - 300.0 else d for d in bremsen)   # Grenze knapp hinter s0: keine Spur
            spur = max(0.0, min(spur, frei))
        n_ext = int(round(spur/pl['gate_schritt_m'])) if spur >= 2*pl['gate_schritt_m'] else 0
        spur = spur if n_ext else 0.0
        ausfahrt_spur_ist = spur
        for k in range(1, n_ext+1):
            ss = (s0 + k*spur/n_ext) % L
            stp = ml.stationen(np.array([ss]))
            wl, wr = float(stp.wl[0]), float(stp.wr[0])
            a_rl = float(periodisch_interp(np.array([ss]), ml.s, rl.alpha_ml, L)[0])
            d_pit = seite*((wl if seite > 0 else wr) - 3.0)
            t = k/n_ext
            w = 0.0 if t < 0.4 else 0.5-0.5*np.cos(np.pi*(t-0.4)/0.6)
            d = (1-w)*d_pit + w*a_rl
            p = _punkt(ml, np.array([ss]), np.array([d]))[0]
            tt = periodisch_interp(np.array([ss]), rl.s_ml, rl.t, L)[0]
            nrm = _einheit(np.array([[-tt[1], 0.0, tt[0]]]))[0]
            lt, rt = -(wl-d), wr+d
            werte = {'left_racing_limit': min(lt+sicher, 0.0), 'right_racing_limit': max(rt-sicher, 0.0),
                     'left_track_limit': lt, 'right_track_limit': rt, 'racing_line': 0.0,
                     'left_white_line': lt+weiss, 'right_white_line': rt-weiss}
            weg = [{'id': j, 'type': typ, 'length': float(werte[typ])} for j, typ in enumerate(typen[:7])]
            gates.append({'id': ng+nbg+k-1, 'name': 'ai_gate_track_pit_%03d' % (nbg+k-1), 'position': p.tolist(),
                          'normal': nrm.tolist(), 'progress': float(pitfort.an(sbg[-1])) + k*spur/n_ext/box['laenge'],
                          'waypoints': weg})
        ein = int(round((box['main_s'][0] % L)/L*ng)) % ng
        if n_ext:   # nicht auf ein Haupttor am selben Ort: eine fast leere Verbindung ließ die KI dort bremsen (Spieltest 3.11.2026)
            aus = int(np.ceil(((s0 + spur) % L + 0.5*pl['gate_schritt_m'])/L*ng)) % ng
        else:
            aus = int(round(((s0 + spur) % L)/L*ng)) % ng
        nb = nbg + n_ext
        kanten = [(ein, ng)] + [(ng+i, ng+i+1) for i in range(nb-1)] + [(ng+nb-1, aus)]
        links += [{'id': ng+i, 'from_gate': a, 'to_gate': b} for i, (a,b) in enumerate(kanten)]
        routes.append({'id': 1, 'type': 'pit', 'from_link': ng, 'to_link': len(links)-1,
                       'from_route': 'track', 'to_route': 'track'})
    def gate_id(ss):
        return int(round((ss % L)/L*ng)) % ng
    kurvenlinien = []
    bremslinien = []
    for i, kv in enumerate(kurven):
        a = kv['i_anfang']*ml.ds
        e = kv['i_ende']*ml.ds
        kurvenlinien.append({'id': i, 'start_gate': gate_id(a), 'end_gate': gate_id(e),
                            'apex_gate': gate_id(kv['i_scheitel']*ml.ds)})
        # Bremspunkt aus dem berechneten Profil vor dem Kurvenanfang.
        dist = np.mod(a-rl.s_ml, L)
        kand = np.flatnonzero((dist<250) & (rl.a_laengs < -0.5))
        ss = rl.s_ml[kand[np.argmax(dist[kand])]] if len(kand) else a
        bremslinien.append({'id': i*10, 'gate_id': gate_id(ss),
                            'max_speed_m_s': float(periodisch_interp([a], rl.s_ml, rl.v, L)[0])})
    retired = []
    for i, kv in enumerate(kurven):
        ss = kv['i_ende']*ml.ds
        seite = 'right' if kv['richtung']=='links' else 'left'
        d = grenzen_d['right_run_off' if seite=='right' else 'left_run_off'][kv['i_ende']]
        retired.append({'id': i, 'gate_id': gate_id(ss), 'side': seite,
                        'position': _punkt(ml, ss, d)[0].tolist()})

    vs = np.arange(max(16, int(round(L/pl['volume_schritt_m']))))
    vs = vs*L/len(vs)
    volumes = [_volumes(_tor(ml, qp, vs, param), True, 'trackspace_0', 'main')]
    if box is not None:
        bs = np.linspace(0, box['laenge'], max(2, int(round(box['laenge']/pl['volume_schritt_m']))+1))
        pos = _offen_an(box, bs, 'position')
        norm = _einheit(_offen_an(box, bs, 'normal'))
        hw = _offen_an(box, bs, 'breite')/2
        spur = _offen_an(box, bs, 'spur_halb')
        versatz = _offen_an(box, bs, 'spur_v')
        # Garagen liegen im Boxen-Volume (Portimão: Volume reicht 65 m über die Garagen hinaus); normal = rechts
        pbg = param['boxengasse']
        extra = pbg.get('gebaeude_tiefe_m', 0.0)+2 if (qp.box.get('garagen_u') is not None) else 0.0
        ext_r, ext_l = (extra, 0.0) if pbg['seite'] == 'rechts' else (0.0, extra)
        # Fahrspur point2…point3, außen die ganze Gasse samt Garagen; die Linie liegt um ``versatz`` neben der Gassenmitte
        vz_g = 1.0 if pbg['seite'] == 'rechts' else -1.0
        tore = []
        for p,n,w,sp,v in zip(pos,norm,hw,spur,versatz):
            tore.append(dict(zip(('point1','point2','point3','point4'),
                                  [(p+n*d).tolist() for d in (w+4+ext_r+vz_g*v, sp, -sp, -w-4-ext_l+vz_g*v)])))
        volumes.append(_volumes(tore, False, 'trackspace_pit_0', 'pit'))

    pg = param['startaufstellung']
    if pg['abstand_pole_m']+(pg['anzahl']-1)*pg['abstand_m']+pg['fahrzeuglaenge_m']/2 >= L:
        raise ValueError('Startaufstellung ist länger als die Strecke')
    plaetze = []
    for i in range(pg['anzahl']):
        ss = -pg['abstand_pole_m']-i*pg['abstand_m']
        stp = ml.stationen([ss])
        vz = (1 if pg['pole_seite']=='links' else -1)*(-1 if i%2 else 1)
        off = pg['seitenversatz_m']
        off = float(off) if off is not None else float((stp.wl[0]+stp.wr[0])/4)
        d = vz*off-STARTPLATZ_QUER_M
        if not -stp.wr[0]+1 <= d <= stp.wl[0]-1:
            raise ValueError('Startplatz liegt außerhalb der Fahrbahn')
        pos = _punkt(ml, ss, d)[0]
        links3 = np.array([stp.nl[0,0], np.tan(stp.bank[0]), stp.nl[0,1]])
        oben = _einheit(np.cross(stp.t3[0],links3))
        plaetze.append({'name': 'VehiclePlacement$GridSlot%d' % (i+1),
                        'template': 'VehiclePlacement', 'VehPlacement': {'Group': 'GridWheelMarker', 'Id': i+1},
                        's_hinter_linie_m': ss, 'progress': float(fort.an(ss)),
                        'position': pos.tolist(), 'matrix': _matrix(pos, stp.t3[0],oben)})
    boxen = []
    if box is not None:
        pb = param['boxengasse']
        # Vollbreiter Abschnitt zwischen Übergängen; Arbeitsspur außen.
        us = boxen_u(pb, qp.box['laenge_entlang_strecke'])
        for i,u in enumerate(us):
            ss = qp.box['s_einfahrt']+u
            stp = ml.stationen([ss])
            prof,_ = qp.an([ss], L)
            e = prof[pb['seite']]['e'][0]
            vz = 1 if pb['seite']=='links' else -1
            w = stp.wl[0] if vz>0 else stp.wr[0]
            d = vz*(w+e[1]+0.75*(e[2]-e[1]))
            pos = _punkt(ml, ss, d)[0]
            bs = float(np.interp(u, box['u'], box['s']))
            t = _offen_an(box, np.array([bs]), 't')[0]
            boxen.append({'name': 'VehiclePlacement$PitBox%d' % (i+1),
                          'template': 'VehiclePlacement', 'VehPlacement': {'Group': 'Pit'},
                          'progress': float(pitfort.an(bs)), 'main_progress': float(fort.an(ss)),
                          'position': pos.tolist(), 'matrix': _matrix(pos, t)})

    # DRS: eigene Zonen (drs.zonen) oder Automatik auf den längsten Geraden; Straight Mode (x_mode) nur
    # aus eigener Eingabe – ohne Eingabe setzt der Bau die Träger-Zonen (spiel_welt.tm_aufbauen).
    drs = []
    for z in zonen.drs_zonen(ml, param):
        drs.append(dict(z, erkennung=float(fort.an(z['erkennung_s_m'])),
                        aktivierung=float(fort.an(z['aktivierung_s_m'])), ende=float(fort.an(z['ende_s_m']))))
    xmode = zonen.xmode_zonen(ml, param)
    markup = []
    def zone(name, typ, start, end, spline='maintrack', props=None):
        markup.append({'Name': name, 'Start': float(start), 'End': float(end),
                       'SplineName': spline, 'Type': typ, 'Properties': props or {}})
    for sec in sektoren:
        zone('sector%d' % sec['nummer'], 'sector_%d' % sec['nummer'], sec['Start'], sec['End'])
    for d in drs:
        zone('drs_det_%d' % d['ID'], 'drs_det', d['erkennung'], (d['erkennung']+.003)%1, props={'ID': d['ID']})
        zone('drs_act_%d' % d['ID'], 'drs_act', d['aktivierung'], d['ende'], props={'ID': d['ID']})
    markup.extend(zonen.xmode_markup(xmode, fort))
    zone('grid_park_osd', 'grid_park_osd', fort.an(-pg['abstand_pole_m']-(pg['anzahl']-1)*pg['abstand_m']-10), fort.an(-pg['abstand_pole_m']))
    zone('rolling_start_zone', 'rolling_start_zone', fort.an(-300), fort.an(-20), props={'ID': 0})
    zone('tt_invalidate_lap', 'tt_invalidate_lap', 0, 1)
    zone('safety_car_main', 'safety_car_main', 0, 1)
    zone('safety_car_player_control_countdown', 'safety_car_player_control_countdown', fort.an(-300), 0)
    pitinfo = None
    if box is not None:
        ein,aus = pitfort.an(box['einfahrt_s']), pitfort.an(box['ausfahrt_s'])
        lim_a = pitfort.an(float(np.interp(param['boxengasse']['anlauf_m'], box['u'], box['s'])))
        lim_b = pitfort.an(float(np.interp(qp.box['laenge_entlang_strecke']-param['boxengasse']['anlauf_m'], box['u'], box['s'])))
        for name, a,b in [('pit_lane_entry_zone', 0,lim_a), ('pit_ent_osd',0,lim_a),
                          ('pit_lim_on',lim_a,lim_b), ('pit_sc_exempt',0,1),
                          ('pitlane_exit_zone',lim_b,1), ('show_pitstop_zone',lim_a,lim_b),
                          ('plr_pit_trgr',lim_a,lim_b), ('ai_pit_trigger',lim_a,lim_b),
                          ('drl',0,1), ('safety_car_pit',0,1)]:
            zone('drl_pit' if name=='drl' else name, name,a,b,'pit_1')
        zone('pit_ai_yield','pit_ai_yield',fort.an(qp.box['s_einfahrt']-100),fort.an(qp.box['s_einfahrt']))
        pitinfo = {'einfahrt_main_progress': float(fort.an(qp.box['s_einfahrt'])),
                   'ausfahrt_main_progress': float(fort.an(qp.box['s_einfahrt']+qp.box['laenge_entlang_strecke'])),
                   'einfahrt_pit_progress': float(ein), 'ausfahrt_pit_progress': float(aus),
                   'tempolimit_kmh': param['boxengasse']['tempolimit_kmh'],
                   'laenge_m': box['laenge'], 'position': box['position'].tolist(),
                   'mitte_position': box['mitte'].tolist(),
                   # Garagen (spiel_welt.plaetze_berechnen): Seite, Tor = Außenrand der Gasse ab Boxenplatz
                   'seite': param['boxengasse']['seite'],
                   'ausfahrt_spur_m': float(ausfahrt_spur_ist),
                   'gebaeude': qp.box.get('garagen_u') is not None,
                   'tor_abstand_m': 0.25*param['boxengasse']['breite'],
                   'gebaeude_tiefe_m': float(param['boxengasse'].get('gebaeude_tiefe_m', 0.0)),
                   'gebaeude_modell': param['boxengasse'].get('gebaeude_modell', 'generiert'),
                   'breite': float(param['boxengasse']['breite']),
                   'mauer_abstand_m': float(param['boxengasse'].get('mauer_abstand_m', 0.6)),
                   'mauer_dicke': float(param['boxengasse'].get('mauer_dicke', 0.4)),
                   'progress': pitfort.an(box['s']).tolist(),
                   'main_progress': fort.an(box['main_s']).tolist()}
    for platz in plaetze+boxen:
        platz['uniqueid'] = uuid.uuid5(uuid.NAMESPACE_URL,
                                      'ego-strecke:'+param['name']+':'+platz['name']).hex
    return {'format': 'ego-strecke-logik', 'version': 1, 'name': param['name'],
            'koordinatensystem': {'achsen': 'y_oben_rechtshaendig', 'einheit': 'm'},
            'fortschritt': '3D-Polygonbogenlänge der genannten TrackSpace-Spline ab Knoten 0, normiert 0…1',
            'laenge_m': L, 'ideallinie_laenge_m': rl.laenge,
            'rundenzeit_s': rl.rundenzeit, 'fahrzeug': param['fahrzeug'],
            'optimierung': rl.optimierung['info'], 'sektoren': sektoren, 'drs': drs, 'x_mode': xmode,
            'start_ziel': {'progress': 0.0, 'punkte': [_punkt(ml,0,-ml.ref.wr[0])[0].tolist(),
                                                    _punkt(ml,0,ml.ref.wl[0])[0].tolist()]},
            'startplaetze': plaetze, 'boxenplaetze': boxen, 'boxengasse': pitinfo,
            'bremsmarker': bremsmarker_berechnen(ml, qp, rl, kurven, param),
            'kurven': kurven,
            'streckengrenzen': {'progress': fort.an(ml.s).tolist(),
                               'links': raender['left_track_limit'].tolist(),
                               'rechts': raender['right_track_limit'].tolist()},
            'ideallinie': {'position': rl.pos.tolist(), 'progress': fort.an(rl.s_ml).tolist(),
                          'geschwindigkeit_m_s': rl.v.tolist(), 'kruemmung_1_m': rl.kappa_roh.tolist()},
            'spielformate': {
                'TrackSpace': {'splines': splines},
                'AISplineData': {'name': 'default', 'gates': gates, 'links': links, 'routes': routes,
                                 'brake_lines': bremslinien,
                                 'corner_start_lines': [{'id': k['id'], 'gate_id': k['start_gate']} for k in kurvenlinien],
                                 'corner_end_lines': [{'id': k['id'], 'gate_id': k['end_gate']} for k in kurvenlinien],
                                 'retire_lines': retired},
                'TS': {'Version': 'ts_data_v3.002', 'volume_lists': volumes},
                'TM': {'Version': 'ts_data_v3.002', 'markup': markup},
                'vehicles_layer': {'matrixkonvention': 'Zeilenvektor; Position m[12:15]; lokale +Z vorwärts',
                                   'placements': plaetze+boxen}},
            'annahmen': ['TM-Zonenbereiche sind Generatorregeln; Pflichtumfang und Wirkung nicht im Spiel geprüft',
                         'VehiclePlacement-Template und VehPlacement-Gruppen aus passendem Träger übernehmen',
                         'Garagen und corner_start_lines/pair_id brauchen Template-/Adapterregeln',
                         'Tempolimit ist Metadatum; kein unbekanntes TM-Property dafür erfunden']}
