"""AP8: AP6-Logik mit explizitem Boxenpfad und ortsfesten Entwurfsankern.

Die Hauptlinie und ihre Ideallinie werden von AP6 neu berechnet. Für den
Portimão-Vergleich wird die Streckenanlage als Definition rekonstruiert:
offene Boxenpolylinie, Breitenprofile, TS-Außenabstände, Zonenanker und lokale
Placement-Frames. Diese Entwurfsdaten sind keine automatisch erkannten Regeln.
Alle Gates, Normalen, TS-Tore, Fortschritte und Matrizen werden neu aufgebaut.
Der originale AISpline-Gateblob wird nicht weitergereicht. Standard-AP6 bleibt
unverändert; diese Erweiterung erzeugt ausschließlich Logik, kein Boxennetz.
"""

import copy
import numpy as np

from ego import pkg, streckenlogik
from . import definition, erzeugung
from .logik import _volumes
from .mittellinie import periodisch_interp


def _frame(t):
    t = np.asarray(t, float)
    t /= np.linalg.norm(t)
    n = np.cross(t, [0, 1, 0])
    n /= np.linalg.norm(n)
    return np.array([-n, np.cross(n, t), t])


def _torframe(spline, f):
    """Über 24 m gemittelte Richtung, unabhängig von einem Knotensegment.

    Lange TS-Außentore verstärken sonst den Tangentensprung eines groben
    Polygonknotens. Dies ist eine Entwurfsregel, keine Engine-Splineformel.
    """
    delta = 12/spline.laenge
    a = f-delta if spline.geschlossen else max(0, f-delta)
    b = f+delta if spline.geschlossen else min(1, f+delta)
    return _frame(np.asarray(spline.punkt(b)[0])-spline.punkt(a)[0])


def _profil(st, spline):
    daten = []
    for g in st.gates:
        if g.boxengasse != (not spline.geschlossen):
            continue
        f, _ = spline.fortschritt_von(g.position)
        c, t = spline.punkt(f)
        n = np.asarray(streckenlogik.rechts(t))
        c = np.asarray(c)
        links, rechts = g.seitenpunkt('left_track_limit'), g.seitenpunkt('right_track_limit')
        wl = -np.dot(np.asarray(links)-c, n)
        wr = np.dot(np.asarray(rechts)-c, n)
        # Originalmittellinie kann an Boxenanschlüssen außerhalb des KI-Tors
        # liegen. Mindesthalbbreite dort ist ausdrücklich eine Näherung.
        daten.append([f, max(.6, float(wl)), max(.6, float(wr))])
    daten.sort()
    return np.asarray(daten)


def _profil_an(profil, f, geschlossen):
    profil = np.asarray(profil)
    if geschlossen:
        return periodisch_interp(f, profil[:, 0], profil[:, 1:], 1)
    return np.array([np.interp(f, profil[:, 0], profil[:, i]) for i in range(1, profil.shape[1])]).T


def definition_aus_vorlage(schreiber):
    """Vorlagenanlage als überprüfbare JSON-fähige Generator-Eingabe ausdrücken."""
    st = schreiber.original
    main = st.hauptlinie
    pit = next(s for s in st.splines.values() if not s.geschlossen)
    profile = {s.name: _profil(st, s) for s in (main, pit)}
    punkte = []
    for p, f, w in zip(main.punkte, main.fortschritt_knoten(),
                       _profil_an(profile[main.name], main.fortschritt_knoten(), True)):
        punkte.append(dict(x=p[0], y=p[1], z=p[2], breite_links=float(w[0]), breite_rechts=float(w[1])))
    zonen = []
    for z in st.zonen:
        spline = st.splines[z.spline]
        zonen.append({'Name': z.name, 'Type': z.typ, 'SplineName': z.spline,
                      'Properties': z.eigenschaften,
                      'anfang_punkt': spline.punkt(z.start)[0], 'ende_punkt': spline.punkt(z.ende)[0],
                      'anfang_ist_null': z.start == 0, 'ende_ist_eins': z.ende == 1,
                      'ende_ist_null': z.ende == 0})
    tor_entwurf = []
    for vl in st.ts_listen:
        spline = main if vl['geschlossen'] else pit
        toranker = []
        quelltore = vl['tore'][:-1] if vl['geschlossen'] else vl['tore']
        for tor in quelltore:
            p = np.asarray(tor['punkte'])
            f, _ = spline.fortschritt_von((p[1]+p[2])/2)
            c, t = spline.punkt(f)
            basis = _torframe(spline, f)
            toranker.append({'anker': c, 'offset_aussen_lokal': ((p[[0, 3]]-c)@basis.T).tolist()})
        tor_entwurf.append({'name': vl['name'], 'type': vl['typ'], 'closed': vl['geschlossen'],
                            'SplineName': spline.name, 'tore': toranker})
    placements = []
    paket = pkg.lesen(schreiber.roh['vehicles_layer'][0])
    for inst in paket.instanzen():
        m = streckenlogik.PLATZ_RE.search(inst.name or '')
        if not m or inst.matrix is None:
            continue
        spline = main if m.group(1).lower() == 'gridslot' else pit
        f, _ = spline.fortschritt_von(inst.position)
        c, t = spline.punkt(f)
        basis = _frame(t)
        matrix = np.asarray(inst.matrix).reshape(4, 4)
        placements.append({'name': inst.name, 'SplineName': spline.name,
                           'anker': c, 'offset_lokal': ((matrix[3, :3]-c)@basis.T).tolist(),
                           'basis_lokal': (matrix[:3, :3]@basis.T).tolist()})
    nmain = sum(not g.boxengasse for g in st.gates)
    return {'name': 'Portimão – AP8 Neuerzeugung',
            'mittellinie': {'art': 'kontrollpunkte', 'punkte': punkte, 'hoehen_glaettung_m': 0},
            'fahrbahn': {'uebergang_m': 0},
            'boxengasse': {'aktiv': False},
            'logik': {'trackspace_schritt_m': 9, 'gate_schritt_m': main.laenge/nmain,
                      'volume_schritt_m': main.laenge/st.ts_listen[0]['volumes']},
            'spiel_entwurf': {
                'hauptname': main.name,
                'boxenpfad': {'name': pit.name, 'punkte': pit.punkte,
                             'breitenprofil': profile[pit.name].tolist(),
                             'gate_start': min(pit.fortschritt_von(g.position)[0] for g in st.gates if g.boxengasse),
                             'gate_ende': max(pit.fortschritt_von(g.position)[0] for g in st.gates if g.boxengasse),
                             'gate_anzahl': sum(g.boxengasse for g in st.gates),
                             'volume_anzahl': st.ts_listen[1]['volumes']},
                'tor_entwurf': tor_entwurf,
                'zonenanker': zonen, 'platzierungen': placements,
                'hinweise': ['Zonen und Plätze sind ortsfeste Entwurfsanker aus der Vorlage, keine automatische Erkennung',
                             'Boxenpfad offen/linear abgetastet; KI-Route umfasst den vorgegebenen Teilbereich',
                             'TS-Außentore mit lokalen Quer-/Längs-/Höhenoffsets und adaptiven Stationsankern',
                             'Lokale Garagenbasis und seitliche/höhenmäßige Abstände aus der Vorlage']}}


def berechnen(daten):
    """AP6 neu berechnen und die expliziten Anlagenparameter in neutrale Logik umsetzen."""
    entwurf = daten['spiel_entwurf']
    erg = erzeugung.berechnen(definition.aus_dict(daten))
    logik = erg['logik']
    sp = logik['spielformate']
    main = sp['TrackSpace']['splines'][0]
    main['name'] = entwurf['hauptname']
    ml = erg['mittellinie']
    mainsp = streckenlogik.Spline(main['name'], main['position'], True)
    pitdaten = entwurf['boxenpfad']
    quellpit = streckenlogik.Spline(pitdaten['name'], pitdaten['punkte'], False)
    n = max(2, int(round(quellpit.laenge/9))+1)
    pf = np.linspace(0, 1, n)
    pitpunkte = [quellpit.punkt(float(f))[0] for f in pf]
    pitsp = streckenlogik.Spline(pitdaten['name'], pitpunkte, False)
    sp['TrackSpace']['splines'].append({'name': pitsp.name, 'closed': False, 'position': pitpunkte,
                                      'progress': pitsp.fortschritt_knoten(), 'laenge_m': pitsp.laenge})
    splines = {mainsp.name: mainsp, pitsp.name: pitsp}
    ai = sp['AISplineData']
    ng = len(ai['gates'])
    # Boxenideallinie folgt zunächst der expliziten Mitte. Kein Anspruch auf
    # optimalen Boxenfahrweg oder originale Kurvenverdichtung.
    pfg = np.linspace(pitdaten['gate_start'], pitdaten['gate_ende'], pitdaten['gate_anzahl'])
    breit = _profil_an(pitdaten['breitenprofil'], pfg, False)
    typen = [w['type'] for w in ai['gates'][0]['waypoints'][:7]]
    for i, (f, (wl, wr)) in enumerate(zip(pfg, breit)):
        p, t = quellpit.punkt(float(f))
        normal = streckenlogik.rechts(t)
        weg = []
        for j, typ in enumerate(typen):
            w = wl if typ.startswith('left') else wr
            inset = min(1.5, .4*w) if 'racing_limit' in typ else (.125 if 'white_line' in typ else 0)
            val = 0 if typ == 'racing_line' else (-1 if typ.startswith('left') else 1)*(w-inset)
            weg.append({'id': j, 'type': typ, 'length': float(val)})
        ai['gates'].append({'id': ng+i, 'name': 'ai_gate_track_pit_%03d' % i,
                            'position': p, 'normal': normal,
                            'progress': pitsp.fortschritt_von(p)[0], 'waypoints': weg})
    def anschluss(p):
        f, _ = mainsp.fortschritt_von(p)
        return min(ai['gates'][:ng], key=lambda g: min(abs(g['progress']-f), 1-abs(g['progress']-f)))['id']
    kanten = [(anschluss(ai['gates'][ng]['position']), ng)]
    kanten += [(ng+i, ng+i+1) for i in range(len(pfg)-1)]
    kanten += [(ng+len(pfg)-1, anschluss(ai['gates'][-1]['position']))]
    ai['links'] += [{'id': ng+i, 'from_gate': a, 'to_gate': b} for i, (a, b) in enumerate(kanten)]
    ai['routes'].append({'id': 1, 'type': 'pit', 'from_link': ng, 'to_link': len(ai['links'])-1,
                         'from_route': 'track', 'to_route': 'track'})
    # Originale Außentore sind nicht überall reine Normalenquerschnitte.
    # Insbesondere in Kurven enthalten sie große Längsoffsets. Die explizite
    # Anlage bewahrt diese Offsets als Entwurfsparameter und berechnet ihre
    # Weltlage aus dem neuen TrackSpace neu. Innere Grenzen stammen aus Breiten.
    adaptive_volumes = []
    for vl in entwurf['tor_entwurf']:
        spline = splines[vl['SplineName']]
        tore = []
        for i, tor in enumerate(vl['tore']):
            f, _ = spline.fortschritt_von(tor['anker'])
            if i == 0 and vl['closed']:
                f = 0.0
            c, t = spline.punkt(f)
            basis = _torframe(spline, f)
            exterior = np.asarray(tor['offset_aussen_lokal'])@basis+c
            if vl['closed']:
                st = ml.stationen([f*ml.laenge])
                wl, wr = st.wl[0], st.wr[0]
            else:
                wl, wr = _profil_an(pitdaten['breitenprofil'], [f], False)[0]
            nrm = -basis[0]
            tore.append({'point1': exterior[0].tolist(), 'point2': (np.asarray(c)+nrm*wr).tolist(),
                         'point3': (np.asarray(c)-nrm*wl).tolist(), 'point4': exterior[1].tolist()})
        adaptive_volumes.append(_volumes(tore, vl['closed'], vl['name'], vl['type']))
    sp['TS']['volume_lists'] = adaptive_volumes
    zonen = []
    for z in entwurf['zonenanker']:
        spline = splines[z['SplineName']]
        start = 0.0 if z['anfang_ist_null'] else spline.fortschritt_von(z['anfang_punkt'])[0]
        end = 1.0 if z['ende_ist_eins'] else (0.0 if z['ende_ist_null'] else spline.fortschritt_von(z['ende_punkt'])[0])
        zonen.append({k: copy.deepcopy(z[k]) for k in ('Name', 'Type', 'SplineName', 'Properties')})
        zonen[-1].update(Start=start, End=end)
    sp['TM']['markup'] = zonen
    placements = []
    for pl in entwurf['platzierungen']:
        spline = splines[pl['SplineName']]
        f, _ = spline.fortschritt_von(pl['anker'])
        c, t = spline.punkt(f)
        basis = _frame(t)
        m = np.eye(4)
        m[:3, :3] = np.asarray(pl['basis_lokal'])@basis
        m[3, :3] = c+np.asarray(pl['offset_lokal'])@basis
        placements.append({'name': pl['name'], 'position': m[3, :3].tolist(),
                           'progress': f, 'matrix': m.ravel().tolist()})
    sp['vehicles_layer']['placements'] = placements
    logik['startplaetze'] = [p for p in placements if 'gridslot' in p['name'].lower()]
    logik['boxenplaetze'] = [p for p in placements if 'pitbox' in p['name'].lower()]
    logik['annahmen'] += entwurf['hinweise']
    logik['spiel_entwurf'] = entwurf
    # Übergeordnete Darstellungsmetadaten auf dieselben exportierten Zonen setzen.
    logik['sektoren'] = [{'nummer': int(z['Type'][-1]), 'Start': z['Start'], 'End': z['End'],
                         's_start_m': z['Start']*ml.laenge, 's_ende_m': z['End']*ml.laenge}
                        for z in zonen if z['Type'].startswith('sector_')]
    logik['drs'] = []
    for z in zonen:
        if z['Type'] == 'drs_act':
            ident = z['Properties']['ID']
            det = next(d for d in zonen if d['Type']=='drs_det' and d['Properties']['ID']==ident)
            logik['drs'].append({'ID': int(ident), 'erkennung': det['Start'], 'aktivierung': z['Start'], 'ende': z['End']})
    logik['boxengasse'] = {'laenge_m': pitsp.laenge, 'position': pitsp.punkte,
                           'progress': pitsp.fortschritt_knoten()}
    erg['bericht']['anzahl_boxen'] = len(logik['boxenplaetze'])
    erg['bericht']['anzahl_startplaetze'] = len(logik['startplaetze'])
    erg['bericht']['anzahl_drs'] = len(logik['drs'])
    return erg
