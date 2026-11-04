"""AP12: Fahrzeug-KI auf bereits geschriebene Streckenlogik übertragen.

``skalieren`` erhält normierte Profile und die fahrzeugspezifische Kalibrierung.
``profil`` erzeugt die Hauptstrecken-Zeitkurve aus dem Generatorprofil und nutzt
offen ausgewiesene Ersatzmodelle für Verbrauch und Verschleiß. Beides ersetzt
keine Kalibrierung im Spiel. Die fünf Dynamikkennfelder und AIVPChar bleiben
unverändert. Ausschließlich KI-Ressourcen werden im übergebenen ERP geändert.
"""

import copy
from contextlib import contextmanager
import json
import math
from pathlib import Path

import numpy as np

from ego import bxml, erp, ki_daten, paket, parallel, streckenlogik


ROOT = Path(__file__).resolve().parents[1]
FAHRZEUGFELDER = {'AIAcceleration', 'AIBraking', 'AICornering', 'AICruising',
                  'AIThrottle', 'AIVPChar'}


@contextmanager
def _vorlage(vorlage):
    if isinstance(vorlage, erp.ErpFile):
        yield vorlage
        return
    if str(vorlage) == 'portimao':
        ziel = ROOT/'testausgabe/ki/portimao_original.erp'
        if not ziel.exists():
            ziel.parent.mkdir(parents=True, exist_ok=True)
            with paket.ModPaket(ROOT/'testausgabe/pakete/portimao_original_wiederhergestellt.zip') as p:
                p.erp_entpacken(str(ziel))
        vorlage = ziel
    with erp.open_erp(vorlage) as quelle:
        yield quelle


def _splines(logik):
    daten = logik.get('spielformate', logik)['TrackSpace']['splines']
    out = []
    for s in daten:
        punkte = np.asarray(s['position'], dtype=float)
        if punkte.ndim != 2 or punkte.shape[1] != 3 or not np.all(np.isfinite(punkte)):
            raise ValueError('TrackSpace needs finite XYZ points')
        spline = streckenlogik.Spline(s['name'], s['position'], bool(s['closed']))
        if spline.laenge <= 0 or len(punkte) < 2:
            raise ValueError('TrackSpace length must be positive')
        out.append(spline)
    haupt = [s for s in out if s.geschlossen]
    box = [s for s in out if not s.geschlossen]
    if len(haupt) != 1 or len(box) != 1:
        raise ValueError('AP12 supports exactly one main and one pit spline')
    return [haupt[0], box[0]]


def _original_splines(archiv):
    ress = [r for r in archiv if r.resource_type == 'TrackSpace']
    if len(ress) != 1:
        raise ValueError('Template must contain exactly one TrackSpace')
    st = streckenlogik.Strecke('vorlage')
    streckenlogik.lade_trackspace(st, bxml.read_resource(ress[0]))
    return sorted(st.splines.values(), key=lambda s: not s.geschlossen)


def _ai(archiv):
    ress = [r for r in archiv if r.resource_type == 'AISplineData']
    if len(ress) != 1:
        raise ValueError('Exactly one AISplineData resource required')
    doc = bxml.read_resource(ress[0])
    gates = {g['id']: g for g in streckenlogik.ki_gates(doc)}
    linien = [dict(e.attrib) for e in doc.iter('brake_line')]
    ids = [int(e['id']) for e in linien]
    if len(ids) != len(set(ids)) or not all(int(e['gate_id']) in gates for e in linien):
        raise ValueError('Duplicate brake ID or invalid brake gate reference')
    return gates, linien


def _fortschritt(spline, position):
    # Projektion in 3D auf die Polylinie, ohne eine Koordinatendrehung.
    p = np.asarray(spline.punkte, dtype=float)
    if spline.geschlossen:
        p = np.vstack([p, p[:1]])
    delta = np.diff(p, axis=0)
    l2 = np.sum(delta*delta, axis=1)
    u = np.clip(np.sum((np.asarray(position)-p[:-1])*delta, axis=1)/np.maximum(l2, 1e-30), 0, 1)
    k = int(np.argmin(np.sum((p[:-1]+u[:, None]*delta-position)**2, axis=1)))
    laengen = np.sqrt(l2)
    return float((laengen[:k].sum()+u[k]*laengen[k])/laengen.sum())


def _profil(logik):
    ideal = logik.get('ideallinie', {})
    p = np.asarray(ideal.get('progress', []), float)
    v = np.asarray(ideal.get('geschwindigkeit_m_s', []), float)
    xyz = np.asarray(ideal.get('position', []), float)
    if len(p) < 3 or v.shape != p.shape or xyz.shape != (len(p), 3):
        raise ValueError('Profile mode needs a racing line with progress, position, geschwindigkeit_m_s')
    if (not np.all(np.isfinite(p)) or not np.all(np.isfinite(v)) or
            not np.all(np.isfinite(xyz)) or np.any(v <= 0) or
            abs(p[0]) > 1e-9 or np.any(np.diff(p) <= 0) or p[-1] >= 1):
        raise ValueError('Invalid speed profile (ring without duplicate end point expected)')
    ds = np.linalg.norm(np.diff(np.vstack([xyz, xyz[:1]]), axis=0), axis=1)
    if np.any(ds <= 0):
        raise ValueError('Duplicate points in the speed profile')
    vn = np.roll(v, -1)
    dt = 2*ds/(v+vn)
    zeit = np.r_[0, np.cumsum(dt)]
    a = (vn*vn-v*v)/(2*ds)
    # Ersatzmodelle: mechanischer Arbeitsbedarf je Masse und Strecke; keine
    # Behauptung über das Verbrennungs-/ERS-/Reifenmodell der Engine.
    fahrzeug = logik.get('fahrzeug', {})
    masse = float(fahrzeug.get('masse_kg', 800))
    if not math.isfinite(masse) or masse <= 0:
        raise ValueError('Positive vehicle mass required')
    drag = (0.5*float(fahrzeug.get('luftdichte', 1.2))*
            float(fahrzeug.get('cw_a_m2', 1.5))/masse*((v+vn)/2)**2)
    arbeit = np.maximum(0, a+drag+9.81*float(fahrzeug.get('rollwiderstand', 0.015)))*ds
    ernte = np.maximum(0, -a)*ds
    k = np.asarray(ideal.get('kruemmung_1_m', np.zeros_like(v)), float)
    if k.shape != p.shape or not np.all(np.isfinite(k)):
        raise ValueError('Invalid curvature sequence')
    reifen = ds*(1+np.abs(k)*v*v/9.81)
    def normiert(werte):
        total = float(werte.sum())
        return np.r_[0, np.cumsum(werte)/total] if total > 0 else np.r_[p, 1.0]
    return {'p': np.r_[p, 1.0], 'v': np.r_[v, v[0]], 'zeit': zeit,
            'kraftstoff': normiert(arbeit), 'einsatz': normiert(arbeit),
            'ernte': normiert(ernte), 'reifen': normiert(reifen)}


def geschwindigkeit_neu(logik, parametersatz='f1_25_ki_portimao', glaettung_m=4.0):
    """Geschwindigkeitsprofil der Ideallinie mit einem Fahrzeug-Parametersatz neu rechnen (Kopie der Logik).

    Standard ``f1_25_ki_portimao``: an der Spiel-KI kalibriert (Portimão-KI-Linie, RMS 11 km/h), damit die
    erzeugten ``VPTimes`` Bremswege und Kurventempo der Spiel-KI treffen. Krümmung der Linie per Spline,
    gleiche Glättung wie im Generator; Höhe aus der Linie (Steigung, Kuppen)."""
    from . import definition, rennlinie
    from .mittellinie import PeriodischerSpline, periodisch_gauss
    logik = copy.deepcopy(logik)
    ideal = logik['ideallinie']
    xyz = np.asarray(ideal['position'], float)
    spl = PeriodischerSpline(xyz)
    tau = spl.t[:-1]
    d1, d2 = spl.auswerten(tau, 1), spl.auswerten(tau, 2)
    vx, vz = d1[:, 0], d1[:, 2]
    kappa = (d2[:, 0]*vz-d2[:, 2]*vx)/np.hypot(vx, vz)**3
    ds = spl.laenge/len(xyz)
    if glaettung_m > 0:
        kappa = periodisch_gauss(kappa, glaettung_m/ds)
    p = dict(definition.STANDARD['fahrzeug'], parametersatz=parametersatz)
    fz = rennlinie.fahrzeug_erzeugen(p)
    prof = rennlinie.profil_berechnen(kappa, ds, fz, hoehe=xyz[:, 1])
    ideal['geschwindigkeit_m_s'] = np.asarray(prof['v'], float).tolist()
    dt = ds/np.maximum(np.asarray(prof['v'], float), 1.0)
    logik['ki_profil'] = {'parametersatz': parametersatz, 'rundenzeit_s': float(prof['rundenzeit']),
                          'a_quer_mittel': float(np.sum(np.abs(prof['a_quer'])*dt)/np.sum(dt)),
                          'v_min_kmh': float(np.min(prof['v'])*3.6), 'v_max_kmh': float(np.max(prof['v'])*3.6)}
    return logik


#: Stützpunkte je Zeittabelle der Portimão-Vorlagen (Hauptroute, Boxenroute); in allen 83.976 Sätzen beider Mod-Versionen gleich
VP_SAMPLES = (931, 305)
#: Obergrenze der umgerechneten VPTimes (roh, MB·10⁶). Verifiziert: LongBoy auf Mod 1.0.0 mit 294 MB lädt im Spiel; auf Mod 1.0.2
#: stürzt das Spiel mit 449 MB ab, mit 251 MB (Stützpunkte der Vorlage) lädt es. Wo dazwischen die Grenze liegt, ist unbekannt.
VP_GRENZE_BYTES = 300e6


def vp_abtastung_waehlen(vorlage_bytes, faktoren, modus='auto', grenze=VP_GRENZE_BYTES):
    """(Abtastung, geschätzte Bytes) der umgerechneten VPTimes. ``auto``: mit der Streckenlänge skaliert, solange die Schätzung
    unter ``grenze`` bleibt, sonst Stützpunktzahl der Vorlage (Hauptroute). Schätzung: Bytes der Vorlage × Stützpunktgewicht."""
    gewicht = (VP_SAMPLES[0]*faktoren[0]+VP_SAMPLES[1]*faktoren[1])/sum(VP_SAMPLES)
    geschaetzt = vorlage_bytes*gewicht
    if modus == 'auto':
        modus = 'original' if geschaetzt > grenze else 'skaliert'
    return modus, geschaetzt


def _interpolieren(werte, n, faktor=1):
    if n == len(werte) and faktor == 1:
        return list(werte)
    return (np.interp(np.linspace(0, 1, n), np.linspace(0, 1, len(werte)), werte)*faktor).tolist()


def _bremsen(daten, gates, linien, splines, profil, modus, pit_limit):
    ids = [int(x['id']) for x in linien]
    bericht = []
    for gruppe in daten.dokument.root.elements:
        alt = gruppe.findall('brake_line')
        if not alt:
            continue
        lookup = {int(e.get('id')): e for e in alt}
        if len(lookup) != len(alt):
            raise ValueError('Duplicate brake ID in tyre group')
        # Vorlagenmodus erfindet keine Kalibrierung für neue IDs.
        if modus == 'vorlage' and set(ids) != set(lookup):
            raise ValueError('Brake ID sets differ; profile mode or consistent logic needed')
        neu = []
        for linie in linien:
            nummer = int(linie['id'])
            g = gates[int(linie['gate_id'])]
            pit = 'pit' in g['name']
            if nummer in lookup:
                e = copy.deepcopy(lookup[nummer])
            else:
                # Neue Entwurfs-ID: nur Parameterstruktur übernehmen; fachliche
                # Modifikatoren neutralisieren, weil ihre Kalibrierung fehlt.
                e = copy.deepcopy(alt[0])
                e.set('id', nummer)
                for eintrag in e.iter('data'):
                    if 'modifier' in eintrag.attrib:
                        eintrag.set('modifier', '1.000000')
            if modus == 'profil':
                if pit:
                    if e.get('curvature_gate') == 'false' and pit_limit is not None:
                        e.set('max_speed', format(pit_limit, '.12g'))
                else:
                    p = _fortschritt(splines[0], g['position'])
                    v = float(np.interp(p, profil['p'], profil['v']))
                    e.set('curvature_gate', 'false')
                    e.set('max_speed', format(v, '.12g'))
            neu.append(e)
        # Unbekannte Kinder bleiben an ihrer Position; nur bekannte Liste ersetzen.
        kinder, eingesetzt = [], False
        for e in gruppe.children:
            if isinstance(e, bxml.Element) and e.name == 'brake_line':
                if not eingesetzt:
                    kinder.extend(neu)
                    eingesetzt = True
            else:
                kinder.append(e)
        gruppe.children = kinder
        bericht.append({'gruppe': gruppe.name, 'ids': ids})
    if not bericht:
        raise ValueError('Brake settings have no tyre groups')
    return bericht


def _verbrauch(d, obj, typ, faktor, profil, samplezahl):
    liste, samples = (('fuel_mix_list', 'fuel_burn_samples') if typ == 'FuelBurnGran'
                       else ('ers_mode_list', 'ers_use_samples'))
    grenzen = (min(min(s[samples]) for s in obj[liste]), max(max(s[samples]) for s in obj[liste]))
    for i, s in enumerate(obj[liste]):
        alt = np.asarray(s[samples], float)
        if len(alt) < 2 or not np.all(np.isfinite(alt)):
            raise ValueError('Granular table needs at least two finite samples')
        n = samplezahl or len(alt)
        if profil is None:
            neu = _interpolieren(alt, n, faktor)
        else:
            p = np.linspace(0, 1, n)
            if typ == 'FuelBurnGran':
                form = np.interp(p, profil['p'], profil['kraftstoff'])
                neu = alt[0]+(alt[-1]-alt[0])*faktor*form
            else:
                diff = np.diff(alt)
                positiv, negativ = np.maximum(diff, 0).sum(), np.minimum(diff, 0).sum()
                neu = alt[0]+faktor*(positiv*np.interp(p, profil['p'], profil['ernte'])+
                                          negativ*np.interp(p, profil['p'], profil['einsatz']))
        if typ == 'ERSUseGran' and (faktor != 1 or profil is not None):
            # Beobachtete Vorlagengrenzen, keine erfundene Batteriekapazität.
            neu = np.clip(neu, *grenzen).tolist()
        d.setzen([liste, i, samples], list(neu))
        d.setzen([liste, i, 'number_of_samples'], n)


def _reifen(d, obj, faktor, profil):
    for i, t in enumerate(obj['tyre_list']):
        for j, m in enumerate(t['mass_list']):
            for k, diff in enumerate(m['difficulty_list_']):
                for l, s in enumerate(diff['startingWears_list']):
                    pfad = ['tyre_list', i, 'mass_list', j, 'difficulty_list_', k, 'startingWears_list', l]
                    p, ende = s['samples_progress'], s['samples_endingWear']
                    if len(p) != len(ende) or not p:
                        raise ValueError('Mismatched tyre sample sequences')
                    start = float(s['wear'])
                    if profil is None:
                        neu = [min(1, start+(v-start)*faktor) for v in ende]
                    else:
                        form = np.interp(p, profil['p'], profil['reifen'])
                        neu = np.minimum(1, start+(ende[-1]-start)*faktor*form).tolist()
                    d.setzen(pfad+['samples_endingWear'], neu)
                    d.setzen(pfad+['startingProgress'], s['startingProgress']/faktor)


def _ers_boost(d, obj, faktor, zeitfaktor, neutral):
    for feld, f in [('baseLapTime', zeitfaktor), ('baseFuelUsage', faktor), ('baseERS', faktor)]:
        if f != 1:
            d.setzen([feld], format(float(obj[feld])*f, '.6f'))
    if neutral:
        if not obj['ers_boost_list']:
            return
        block = d._block(['ers_boost_list'])
        # Eine im Schema bereits vorhandene Strategie wird auf den bekannten
        # Leerlistenfall reduziert; unbekannte Felder werden nicht erfunden.
        index = min(range(len(obj['ers_boost_list'])), key=lambda i:
                    (len(obj['ers_boost_list'][i].get('progress_start', [])),
                     len(obj['ers_boost_list'][i].get('corner_id', []))))
        block.werte = [block.werte[index]]
        s = obj['ers_boost_list'][index]
        for feld in s:
            if isinstance(s[feld], list):
                d.setzen(['ers_boost_list', 0, feld], [])
        for feld in ('coastDist', 'lap_time_gain', 'ers_delta_recorded', 'ers_burn_aim'):
            if feld in s:
                d.setzen(['ers_boost_list', 0, feld], 0.0)
        d.setzen(['ers_boost_list', 0, 'fuelUsage'], float(obj['baseFuelUsage'])*faktor)
    else:
        for i, s in enumerate(obj['ers_boost_list']):
            for feld, f in [('fuelUsage', faktor), ('coastDist', faktor), ('coast_dist', faktor),
                            ('lap_time_gain', zeitfaktor), ('ers_amount', faktor),
                            ('ers_delta_recorded', faktor), ('ers_burn_aim', faktor)]:
                if feld in s and f != 1:
                    v = s[feld]
                    d.setzen(['ers_boost_list', i, feld], [x*f for x in v] if isinstance(v, list) else v*f)


def _ki_ressource(auftrag):
    """Eine KI-Ressource anpassen (läuft im Arbeitsprozess, siehe ``ego.parallel``).

    Auftrag: (Typ, Vorlagenfragmente, bisherige Zielfragmente), jeweils (gepackt, Größe, Kompression).
    Ergebnis: (Typ, None) ohne Änderung, sonst (Typ, [(Daten, gepackt), …]) mit ``erp.fragment_packen`` schon
    komprimiert."""
    typ, quelle, ziel = auftrag
    quelle = [erp.fragment_entpacken(*f) for f in quelle]
    ziel = [erp.fragment_entpacken(*f) for f in ziel]
    g = parallel.gemeinsam()
    modus, profil, faktoren = g['modus'], g['profil'], g['faktoren']
    d = ki_daten.fragmente_lesen(typ, quelle)
    if typ == 'VPTimes':
        if len(d.vp_tabellen) != 2:
            raise ValueError('VP template needs two route tables')
        for route, t in enumerate(d.vp_tabellen):
            for s in t.saetze:
                n = max(2, int(round((s.anzahl-1)*faktoren[route]))+1)
                if route == 0 and g['vp_abtastung'] == 'original':
                    n = s.anzahl          # Stützpunktzahl der Mod behalten (Eingrenzung des Absturzes mit Mod 1.0.2)
                if n > 100000:
                    raise ValueError('VP target sampling too large')
                if modus == 'profil' and route == 0:
                    form = np.interp(np.linspace(0, 1, n), profil['p'], profil['zeit'])
                    zeiten = form*s.zeiten[-1]/float(g['referenz'])
                else:
                    zeiten = _interpolieren(s.zeiten, n, faktoren[route])
                s.zeiten_setzen(zeiten)
    elif typ == 'AIBrakeSettings':
        _bremsen(d, g['gates'], g['linien'], g['splines'], profil, g['bremsmodus'], g['pit_limit'])
    elif typ not in FAHRZEUGFELDER:
        obj = d.inhalt()
        if typ in ('FuelBurnGran', 'ERSUseGran'):
            _verbrauch(d, obj, typ, faktoren[0], profil if modus == 'profil' else None, g['samples'])
        elif typ == 'TyreWearData':
            _reifen(d, obj, faktoren[0], profil if modus == 'profil' else None)
        elif typ == 'ERSBoost':
            _ers_boost(d, obj, faktoren[0], g['zeitfaktor'],
                       g['strategie'] == 'neutral' and not g['gleiche_geometrie'])
    raw = [ki_daten.schreiben(d)]
    if len(ziel) > 1:
        if d.dokument is None or len(ziel) != 2:
            raise ValueError('Unknown additional AI fragments')
        raw.append(bxml.compute_tsmd(d.dokument))
    # Erneut parsen, bevor die erste Zielressource geändert wird.
    kontrolle = ki_daten.lesen(raw[0], typ)
    if ki_daten.schreiben(kontrolle) != raw[0]:
        raise ValueError('AI output fails the repeated round trip')
    if not any(alt != x for alt, x in zip(ziel, raw)):
        return typ, None
    return typ, [(x, erp.fragment_packen(x)) for x in raw]


def ki_daten_anpassen(archiv, logik_json, vorlage='portimao', **optionen):
    """KI in einem offenen ``erp.ErpFile`` anpassen; Bericht als dict liefern.

    Vorher die Ziel-TrackSpace/aispline schreiben (AP8/AP11). Akzeptiert einen
    JSON-Pfad oder ein neutrales Logik-dict. ``vorlage``: Portimão-Originalpaket,
    ERP-Pfad oder offenes Originalarchiv. Die Ziel-KI wird stets aus dieser
    Quelle abgeleitet; erneute Aufrufe skalieren nicht ein zweites Mal.

    Optionen: modus='skalieren'|'profil', bremsmodus='vorlage'|'profil', profil_fahrzeug=None|Parametersatz
    (Profil vorher mit diesem Fahrzeugsatz neu rechnen, z. B. 'f1_25_ki_portimao'),
    ers_strategie='neutral'|'behalten', granular_samples=None|int,
    referenz_zeit_s=None|float, ohne_pakete=Fahrzeugpakete (z. B. ``f1_2024_story_vehicle_package``), deren KI-Daten
    unverändert bleiben (Portimão-Mod 1.0.2: Absturz beim Laden mit angepassten Daten, Eingrenzung läuft).
    Profil und explizite Bremsgeschwindigkeiten sind
    Versuchsmodelle. Ohne Profil bleiben Verbrauchsformen normiert erhalten.
    """
    erlaubt = {'modus', 'bremsmodus', 'ers_strategie', 'granular_samples', 'referenz_zeit_s', 'profil_fahrzeug', 'ohne_pakete', 'nur_typen', 'vp_abtastung'}
    if set(optionen)-erlaubt:
        raise TypeError('Unknown AI options: '+', '.join(sorted(set(optionen)-erlaubt)))
    modus = optionen.get('modus', 'skalieren')
    bremsmodus = optionen.get('bremsmodus', 'vorlage')
    strategie = optionen.get('ers_strategie', 'neutral')
    samples = optionen.get('granular_samples')
    ohne = tuple(optionen.get('ohne_pakete') or ())
    nur_typen = frozenset(optionen.get('nur_typen') or ())
    vp_abtastung = optionen.get('vp_abtastung') or 'auto'
    if vp_abtastung not in ('auto', 'skaliert', 'original'):
        raise ValueError('vp_abtastung: auto, skaliert or original')
    if modus not in ('skalieren', 'profil') or bremsmodus not in ('vorlage', 'profil'):
        raise ValueError('Unknown AI/brake mode')
    if strategie not in ('neutral', 'behalten'):
        raise ValueError('Unknown ERS strategy')
    if samples is not None and (isinstance(samples, bool) or not isinstance(samples, int) or not 2 <= samples <= 100000):
        raise ValueError('granular_samples must be a whole number between 2 and 100000')
    logik = (json.loads(Path(logik_json).read_text(encoding='utf-8'))
             if isinstance(logik_json, (str, Path)) else copy.deepcopy(logik_json))
    if optionen.get('profil_fahrzeug') and modus == 'profil':
        # 25.10.2026: Profil mit an der Spiel-KI kalibriertem Fahrzeug statt der Generator-Punktmasse
        logik = geschwindigkeit_neu(logik, optionen['profil_fahrzeug'])
    koordinaten = logik.get('koordinatensystem', {})
    if (koordinaten.get('achsen', 'y_oben_rechtshaendig') != 'y_oben_rechtshaendig' or
            koordinaten.get('einheit', 'm') != 'm'):
        raise ValueError('AI adapter needs metres, Y up and right-handed coordinates')
    neu = _splines(logik)
    im_archiv = _original_splines(archiv)
    if len(im_archiv) != len(neu) or any(
            a.name != n.name or len(a.punkte) != len(n.punkte) or
            not np.allclose(np.asarray(a.punkte)-a.punkte[0], np.asarray(n.punkte)-n.punkte[0],
                            atol=0.02, rtol=0)
            for a, n in zip(im_archiv, neu)):
        raise ValueError('Write the target TrackSpace first: spline points differ')
    gates, linien = _ai(archiv)
    eingabe_gates = logik.get('spielformate', logik)['AISplineData']['gates']
    ziel_gates = {int(g['id']): g for g in eingabe_gates}
    if set(ziel_gates) != set(gates) or len(ziel_gates) != len(eingabe_gates):
        raise ValueError('Write the target aispline first: gate IDs or count differ')
    # AP11 verschiebt alle Punkte um denselben verbindlichen Versatz. Die
    # Invarianz unter Translation wird geprüft, keine Koordinaten werden geändert.
    delta = np.asarray(next(iter(gates.values()))['position'])-np.asarray(ziel_gates[next(iter(gates))]['position'])
    if any(not np.allclose(np.asarray(a.punkte)-np.asarray(n.punkte), delta, atol=0.02, rtol=0)
           for a, n in zip(im_archiv, neu)):
        raise ValueError('TrackSpace and aispline must use the same placement offset')
    if any(np.linalg.norm(np.asarray(g['position'])-ziel_gates[i]['position']-delta) > 0.02
           for i, g in gates.items()):
        raise ValueError('Target aispline does not match the logic spatially (write the logic first)')
    # Für die Projektion beide Darstellungen in dasselbe Koordinatensystem bringen.
    projektionsgates = copy.deepcopy(gates)
    for g in projektionsgates.values():
        g['position'] = (np.asarray(g['position'])-delta).tolist()
    profil = _profil(logik) if modus == 'profil' or bremsmodus == 'profil' else None
    pit = logik.get('boxengasse', {})
    pit_limit = float(pit.get('tempolimit_kmh', 80))/3.6
    geaendert, typzahlen, plan = [], {}, []
    with _vorlage(vorlage) as quelle:
        alt = _original_splines(quelle)
        if len(alt) != 2 or not alt[0].geschlossen or alt[1].geschlossen:
            raise ValueError('Mismatched template routes')
        faktoren = [n.laenge/a.laenge for n, a in zip(neu, alt)]
        gleiche_geometrie = all(len(a.punkte) == len(n.punkte) and
            np.allclose(np.asarray(a.punkte)-a.punkte[0], np.asarray(n.punkte)-n.punkte[0], rtol=0, atol=1e-7)
            for a, n in zip(alt, neu))
        quellen = {r.identifier.split('/ai/', 1)[1]: r for r in quelle
                   if r.resource_type in ki_daten.TYPEN and '/ai/' in r.identifier}
        ziele = [r for r in archiv if r.resource_type in ki_daten.TYPEN]
        if not ziele:
            raise ValueError('No vehicle AI in the target archive')
        referenz = optionen.get('referenz_zeit_s')
        if modus == 'profil' and referenz is None:
            kandidaten = [r for key, r in sorted(quellen.items()) if r.resource_type == 'VPTimes']
            bevorzugt = [r for r in kandidaten if '/f1_2025_vehicle_package/ferrari/' in r.identifier]
            if not kandidaten:
                raise ValueError('VP reference time is missing')
            vp = ki_daten.ressourcen_lesen((bevorzugt or kandidaten)[0])
            mass = float(logik.get('fahrzeug', {}).get('masse_kg', 800))
            s = min(vp.vp_tabellen[0].saetze, key=lambda s:
                    (s.parameter['klasse_0'], abs(s.parameter['faktor_1']-1),
                     abs(s.parameter['schwierigkeit']-1), s.parameter['modus_3'], s.parameter['modus_4'],
                     abs(s.parameter['grip']-1), abs(s.parameter['masse']-mass)))
            referenz = s.zeiten[-1]
        if referenz is not None and (not math.isfinite(float(referenz)) or float(referenz) <= 0):
            raise ValueError('Positive finite reference time required')
        zeitfaktor = float(profil['zeit'][-1]/referenz) if modus == 'profil' else faktoren[0]
        auftraege, ergebnisse, uebersprungen = [], [], 0
        for res in ziele:
            suffix = res.identifier.split('/ai/', 1)[-1]
            if suffix not in quellen or quellen[suffix].resource_type != res.resource_type:
                raise ValueError('AI template resource is missing: '+res.identifier)
            if len(res.fragments) != len(quellen[suffix].fragments):
                raise ValueError('Source and target fragments differ')
            if nur_typen and res.resource_type not in nur_typen:
                ergebnisse.append((res.resource_type, None))      # Typ ausgenommen (Eingrenzung des Absturzes)
                uebersprungen += 1
            elif any('/ai/%s/' % paket in res.identifier for paket in ohne):
                ergebnisse.append((res.resource_type, None))      # Paket ausgenommen: Daten der Mod bleiben
                uebersprungen += 1
            elif (res.resource_type in FAHRZEUGFELDER and len(res.fragments) == 1 and
                    quellen[suffix].fragments[0].read() == res.fragments[0].read()):
                # Kennfelder bleiben unverändert; Vorlage = Ziel → nichts zu tun (Rundlauf ist bitgenau,
                # vorher ≈ 50 s CPU für Parsen und Zurückschreiben ohne Änderung)
                ergebnisse.append((res.resource_type, None))
            else:
                ergebnisse.append(len(auftraege))
                auftraege.append((res.resource_type,
                                  [(f.read_packed(), f.size, f.compression) for f in quellen[suffix].fragments],
                                  [(f.read_packed(), f.size, f.compression) for f in res.fragments]))
        vp_bytes = 0 if nur_typen and 'VPTimes' not in nur_typen else sum(
            f.size for res in ziele if res.resource_type == 'VPTimes'
            and not any('/ai/%s/' % paket in res.identifier for paket in ohne) for f in res.fragments)
        vp_abtastung, vp_geschaetzt = vp_abtastung_waehlen(vp_bytes, faktoren, vp_abtastung)
        # 26.10.2026: Ressourcen sind unabhängig → auf alle Kerne verteilt (vorher ≈ 140 s auf einem Kern)
        gemeinsam = {'modus': modus, 'profil': profil, 'faktoren': faktoren, 'referenz': referenz,
                     'gates': projektionsgates, 'linien': linien, 'splines': neu, 'bremsmodus': bremsmodus,
                     'pit_limit': pit_limit, 'samples': samples, 'zeitfaktor': zeitfaktor,
                     'strategie': strategie, 'gleiche_geometrie': gleiche_geometrie, 'vp_abtastung': vp_abtastung}
        # größte Ressourcen zuerst (zstd Stufe 19 der VPTimes ist der teuerste Teil)
        berechnet = parallel.abbilden(_ki_ressource, auftraege, gemeinsam,
                                      aufwand=[sum(f[1] for f in a[1]) for a in auftraege])
        ergebnisse = [berechnet[e] if isinstance(e, int) else e for e in ergebnisse]
        for res, (typ, neu_daten) in zip(ziele, ergebnisse):
            typzahlen[typ] = typzahlen.get(typ, 0)+1
            if neu_daten is not None:
                plan.append((res, neu_daten))
        for res, neu_daten in plan:
            for i, (x, gepackt) in enumerate(neu_daten):
                res.set_fragment_data(i, x, gepackt=gepackt)
            geaendert.append(res.identifier)
    return {'modus': modus, 'bremsmodus': bremsmodus, 'ers_strategie': strategie,
            'ressourcen': len(ziele), 'typen': typzahlen, 'geaendert': len(geaendert),
            'ohne_pakete': list(ohne), 'nur_typen': sorted(nur_typen), 'uebersprungen': uebersprungen,
            'vp_abtastung': vp_abtastung, 'vp_geschaetzt_mb': round(vp_geschaetzt/1e6, 1),
            'vp_grenze_mb': round(VP_GRENZE_BYTES/1e6, 1),
            'geaenderte_kennungen': geaendert, 'laengen_alt_m': [s.laenge for s in alt],
            'laengen_neu_m': [s.laenge for s in neu], 'laengenfaktoren': faktoren,
            'brems_ids': sorted(int(l['id']) for l in linien),
            'gates': len(gates), 'referenz_zeit_s': referenz,
            'profil_rundenzeit_s': float(profil['zeit'][-1]) if profil else None,
            'profil_fahrzeug': logik.get('ki_profil'),
            'grenzen': ['Kein Spieltest; Fahrzeugkennfelder bleiben kalibriert aus der Vorlage.',
                        'VP-Abtastung wird proportional zur Routenlänge umgerechnet; Engine-Regel offen.',
                        'Profilmodus: Verbrauch, ERS und Reifen sind unkalibrierte Ersatzmodelle.',
                        'Boxen-VP erhält die alte Zeitform, skaliert mit der offenen TrackSpace-Länge.',
                        'Bremsmodifikatoren vorhandener IDs bleiben fahrzeugspezifisch erhalten.']}
