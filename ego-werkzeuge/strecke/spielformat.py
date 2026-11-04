"""AP8: neutrale Logik mit einer ERP-Vorlage in EGO-Spielressourcen schreiben.

BXML wird aus editierbaren Vorlagenbäumen neu serialisiert. Bekannte numerische
Felder behalten bei gleichem Wert ihre ursprüngliche Schreibweise; unbekannte
Attribute und Komponenten kommen aus der Vorlage. AISpline-TSMD wird berechnet,
TrackSpace/TM/TS besitzen in Portimão kein TSMD. QT02 benutzt ego.tsquadtree.
pkg-Matrizen sind Zeilenvektormatrizen mit Position in m[12:15]. Neue Werte
werden angehängt, damit geteilte Tabellenwerte keine Nebenwirkungen verursachen.
Keine Bibliothek in ego/ wird verändert. Nachweise: docs/integration_streckenlogik.md.
"""

import copy
import math
from pathlib import Path
import struct
import uuid

from ego import bxml, erp, pkg, spielpfade, streckenlogik, tsquadtree


def _zahl(wert):
    wert = float(wert)
    if not math.isfinite(wert):
        raise ValueError('Non-finite logic value')
    return format(wert, '.12g')


def _setzen(el, name, wert, numerisch=False):
    alt = el.get(name)
    if numerisch:
        if alt is not None and float(alt) == float(wert):
            return
        wert = _zahl(wert)
    el.set(name, wert)


def _vektor(el, name, wert):
    wert = [float(v) for v in wert]
    if len(wert) != 3 or not all(math.isfinite(v) for v in wert):
        raise ValueError('Vector needs three finite values')
    alt = el.get(name)
    if alt is None or streckenlogik.parse_vec(alt) != wert:
        el.set(name, ', '.join(_zahl(v) for v in wert))


def _kinder(el, name, kinder):
    """Eine bekannte Elementfolge ersetzen, andere Kinder und Text erhalten."""
    rest, eingefuegt = [], False
    for c in el.children:
        if isinstance(c, bxml.Element) and c.name == name:
            if not eingefuegt:
                rest.extend(kinder)
                eingefuegt = True
        else:
            rest.append(c)
    if not eingefuegt:
        rest.extend(kinder)
    el.children = rest


def _vorlagen_element(liste, name, index=0):
    elemente = liste.findall(name)
    return copy.deepcopy(elemente[min(index, len(elemente)-1)]) if elemente else bxml.Element(name)


def neutral_aus_strecke(st):
    """Das öffentliche lade()-Modell ins AP6-Schema überführen (ohne Rohblob)."""
    splines = [{'name': s.name, 'closed': s.geschlossen, 'position': s.punkte,
                'progress': s.fortschritt_knoten(), 'laenge_m': s.laenge}
               for s in st.splines.values()]
    gates = [{'id': g.id, 'name': g.name, 'position': g.position, 'normal': g.normal,
              'waypoints': [{'type': t, 'length': l} for t, l in g.wegpunkte.items()]}
             for g in st.gates]
    listen = []
    for vl in st.ts_listen:
        gruppen = {}
        for tor in vl['tore']:
            gruppen.setdefault(tor['volume'], []).append(
                dict(zip(('point1', 'point2', 'point3', 'point4'), tor['punkte'])))
        volumes, letztes = [], None
        for i in range(vl['volumes']):
            tore = gruppen.get(i, [])
            if len(tore) == 1 and letztes is not None:
                tore = [letztes, tore[0]]
            if len(tore) != 2:
                raise ValueError('lade() TS gates cannot be assigned at volume %d' % i)
            volumes.append({'id': i, 'gates': tore})
            letztes = tore[-1]
        listen.append({'name': vl['name'], 'type': vl['typ'], 'closed': vl['geschlossen'],
                       'numVolumes': len(volumes), 'volumes': volumes})
    return {'koordinatensystem': {'achsen': 'y_oben_rechtshaendig', 'einheit': 'm'},
            'spielformate': {
                'TrackSpace': {'splines': splines},
                'AISplineData': {'gates': gates,
                    'links': [{'id': i, 'from_gate': a, 'to_gate': b} for i, (a, b) in enumerate(st.links)],
                    'routes': st.routen, **st.linien},
                'TS': {'volume_lists': listen},
                'TM': {'markup': [{'Name': z.name, 'Start': z.start, 'End': z.ende,
                                  'SplineName': z.spline, 'Type': z.typ, 'Properties': z.eigenschaften}
                                 for z in st.zonen]},
                'vehicles_layer': {'placements': st.plaetze}}}


def _pfad(paket, block, *namen):
    for name in namen:
        feld = paket.feld(block, name)
        block = feld.wert.ziel
    return feld


def _tabellenwert(paket, feld, raw):
    tab = paket.tabelle(feld.wert)
    start = feld.wert.index * tab.elementgroesse
    if tab.daten[start:start+tab.elementgroesse] != raw:
        feld.wert = paket.neuer_wert(feld.wert, raw)


def _struktur_eindeutig(paket, inst):
    eingang = {}
    for b in paket.bloecke:
        for r in b.verweise():
            if r.typ == 0x80:
                eingang[id(r.ziel)] = eingang.get(id(r.ziel), 0)+1
    def pruefen(b):
        if eingang.get(id(b), 0) > 1:
            raise pkg.PkgFormatError('Platzierung enthält geteilte Strukturblöcke')
        for r in b.verweise():
            if r.typ == 0x80:
                pruefen(r.ziel)
    pruefen(inst.block)


def _platzschluessel(name):
    m = streckenlogik.PLATZ_RE.search(name or '')
    if not m:
        raise ValueError('Unknown placement name: %s' % name)
    return m.group(1).lower(), int(m.group(2))


def _platz_klonen(paket, inst, name, uniqueid):
    """Template-Placement über öffentliche pkg-Blocktypen klonen.

    pkg.instanz_hinzufuegen() verlangt einen Modellverweis; VehiclePlacement
    hat keinen. Tabellen bleiben gemeinsam, Strukturblöcke werden vollständig
    kopiert. Die WOID ist in gespeicherter Byteordnung, ohne UUID-Uminterpretation.
    """
    encoded = name.encode('utf-8')
    uid = bytes.fromhex(uniqueid)
    if not encoded or len(encoded)>255 or b'\0' in encoded or len(uid)!=16:
        raise ValueError('Invalid placement name or WOID')
    if any(i.name==name or i.uniqueid==uid.hex() for i in paket.instanzen()):
        raise ValueError('Placement name or WOID already in use')
    neue_bloecke, gesehen = [], {}
    def klonen(block):
        if id(block) in gesehen:
            return gesehen[id(block)]
        if block.kennung not in (b'!idi', b'!ili', b'!iar'):
            raise pkg.PkgFormatError('Unbekannter Placement-Strukturblock')
        neu = copy.copy(block)
        neu.offset, neu.davor = 0, b''
        gesehen[id(block)] = neu
        neue_bloecke.append(neu)
        def ref(r):
            return pkg.Verweis(r.typ, r.index, klonen(r.ziel) if r.typ==0x80 else None)
        neu.felder = [pkg.Feld(ref(f.name), ref(f.wert), f.hash) for f in block.felder]
        neu.werte = [ref(r) for r in block.werte]
        return neu
    neu = pkg.Instanz(paket, klonen(inst.block), len(paket.instanzen()))
    nf = paket.feld(neu.block, 'name')
    uf = paket.feld(neu.block, 'uniqueid')
    verlangen_woid = paket.tabelle(uf.wert)
    if verlangen_woid.datentyp != b'woid' or verlangen_woid.elementgroesse != 16:
        raise pkg.PkgFormatError('Placement-WOID hat unerwarteten Typ')
    nf.wert = paket.neuer_wert(nf.wert, bytes([len(encoded)])+encoded+b'\0')
    uf.wert = paket.neuer_wert(uf.wert, uid)
    stelle = paket.bloecke.index(paket.tabellen.ziel)
    paket.bloecke[stelle:stelle] = neue_bloecke
    liste = paket.feld(paket.wurzel.ziel, 'instances').wert.ziel
    liste.werte.append(pkg.Verweis(0x80, 0, neu.block))
    return neu


class Schreiber:
    """Vorlage aus offenem ERP lesen; schreiben() liefert Kennung -> Fragmentbytes.

    bremslinien='vorlage': alle Vorlagen-IDs und Settings erhalten; Gate-Bezüge
    über den Fortschritt derselben Route abbilden. 'neutral' übernimmt explizite
    IDs nur bei exakt gleichem ID-Satz und Deckung durch sämtliche Auto-Settings.
    Der Rundlauf mit einem Strecke-Objekt benutzt automatisch 'neutral'.
    """

    def __init__(self, archiv, code='portimao'):
        self.code = code
        self.original = streckenlogik.lade(archiv, code)
        q = self.original.quellen
        self.ressourcen = {typ: archiv.find(q[key]) for typ, key in (
            ('TrackSpace', 'trackspace'), ('AISplineData', 'aispline'),
            ('TM', 'trackmarkup'), ('TS', 'trackspacedata'), ('vehicles_layer', 'fahrzeugplaetze'))}
        self.ressourcen['TSQuadTree'] = archiv.find(q['trackspace']+'?context=quadtree')
        if any(r is None for r in self.ressourcen.values()):
            raise ValueError('Template does not contain all six logic resources')
        self.roh = {t: tuple(f.read() for f in r.fragments) for t, r in self.ressourcen.items()}
        self.dokumente = {t: bxml.decode(self.roh[t][0]) for t in ('TrackSpace', 'AISplineData', 'TM', 'TS')}
        self.bremsressourcen = [r for r in archiv if r.resource_type == 'AIBrakeSettings'
                               and '/tracks/%s/' % code in r.identifier]
        self.bremspruefung = []
        ids = {int(l['id']) for l in self.original.linien['brake_lines']}
        for r in self.bremsressourcen:
            doc = bxml.read_resource(r)
            # Slicks/Regenreifen separat prüfen, kein bloßes Vereinigungs-Ergebnis.
            for gruppe in doc.root.elements:
                linien = list(gruppe.iter('brake_line'))
                if linien:
                    vorhanden = {int(l.get('id')) for l in linien}
                    if not ids <= vorhanden:
                        raise ValueError('Brake IDs are missing in %s/%s' % (r.identifier, gruppe.name))
                    self.bremspruefung.append((r.identifier, gruppe.name, len(vorhanden)))
        if not self.bremsressourcen:
            raise ValueError('No AIBrakeSettings in the template')

    def _gate_abbildung(self, ai, splines):
        neu = {s['name']: streckenlogik.Spline(s['name'], s['position'], s['closed']) for s in splines}
        kandidaten = {}
        for g in ai['gates']:
            pit = 'pit' in g['name']
            spline = next(s for s in neu.values() if s.geschlossen != pit)
            f = float(g.get('progress', spline.fortschritt_von(g['position'])[0]))
            kandidaten.setdefault(pit, []).append((f, int(g['id'])))
        out = {}
        for g in self.original.gates:
            spline = next(s for s in self.original.splines.values() if s.geschlossen != g.boxengasse)
            f = spline.fortschritt_von(g.position)[0]
            if g.boxengasse not in kandidaten:
                raise ValueError('Template braking line needs a missing pit route')
            def abstand(v):
                d = abs(v[0]-f)
                return d if g.boxengasse else min(d, 1-d)
            out[g.id] = min(kandidaten[g.boxengasse], key=abstand)[1]
        return out

    def _trackspace(self, daten):
        doc = copy.deepcopy(self.dokumente['TrackSpace'])
        alt = {s.get('name'): s for s in doc.iter('TrackSpaceSpline')}
        neu = []
        for sp in daten['splines']:
            el = copy.deepcopy(alt.get(sp['name'], next(iter(alt.values()))))
            el.set('name', sp['name'])
            knoten = []
            for i, p in enumerate(sp['position']):
                k = _vorlagen_element(el, 'Knot', i)
                _vektor(k, 'position', p)
                knoten.append(k)
            _kinder(el, 'Knot', knoten)
            neu.append(el)
        _kinder(doc.root, 'TrackSpaceSpline', neu)
        return doc

    def _aispline(self, daten, bremslinien, abbildung):
        doc = copy.deepcopy(self.dokumente['AISplineData'])
        gatesel = doc.root.find('gates')
        alt = {int(g.get('id')): g for g in gatesel.elements}
        neue_gates = []
        for i, g in enumerate(daten['gates']):
            el = copy.deepcopy(alt.get(int(g['id']), gatesel.elements[min(i, len(gatesel.elements)-1)]))
            for k in ('id', 'name'):
                _setzen(el, k, g[k])
            for k in ('position', 'normal'):
                for achse, v in zip('xyz', g[k]):
                    _setzen(el.find(k), achse, v, True)
            wp = el.find('waypoints')
            wpalt = {w.get('type'): w for w in wp.elements}
            weg = []
            for j, w in enumerate(g['waypoints']):
                e = copy.deepcopy(wpalt.get(w['type'], bxml.Element('waypoint')))
                e.set('id', w.get('id', e.get('id', j)))
                e.set('type', w['type'])
                _setzen(e, 'length', w['length'], True)
                weg.append(e)
            _kinder(wp, 'waypoint', weg)
            wp.set('num_waypoints', len(weg))
            neue_gates.append(el)
        _kinder(gatesel, 'gate', neue_gates)
        gatesel.set('num_gates', len(neue_gates))
        for liste, singular in (('links', 'link'), ('routes', 'route'),
                                ('corner_start_lines', 'corner_start_line'),
                                ('corner_end_lines', 'corner_end_line'),
                                ('brake_lines', 'brake_line'), ('retire_lines', 'retire_line')):
            parent = doc.root.find(liste)
            if parent is None:
                parent = doc.root.append(bxml.Element(liste))
            elemente = []
            quelle = self.original.linien['brake_lines'] if liste == 'brake_lines' and bremslinien == 'vorlage' else daten.get(liste, [])
            for i, eintrag in enumerate(quelle):
                el = _vorlagen_element(parent, singular, i)
                # pair_id ist ein Gate-Bezug und darf nicht vom geklonten Index erben.
                if liste == 'corner_start_lines' and 'pair_id' not in eintrag:
                    el.attrib.pop('pair_id', None)
                    if abbildung:
                        # Unbekannte Paarbedeutung nicht neu erfinden: die
                        # nächstliegende Vorlagen-Kurvenmarke derselben Route
                        # wählen, deren Gate-Bezug und Gegenstück abbilden.
                        gate = next(g for g in daten['gates'] if int(g['id']) == int(eintrag['gate_id']))
                        pit = 'pit' in gate['name']
                        altlinien = [l for l in self.original.linien[liste]
                                     if self.original.gate(int(l['gate_id'])).boxengasse == pit]
                        if altlinien:
                            neu_by_id = {int(g['id']): g for g in daten['gates']}
                            quelle = min(altlinien, key=lambda l: math.dist(
                                gate['position'], neu_by_id[abbildung[int(l['gate_id'])]]['position']))
                            if 'pair_id' in quelle:
                                el.set('pair_id', abbildung[int(quelle['pair_id'])])
                for k, v in eintrag.items():
                    if k in ('max_speed_m_s', 'progress'):
                        continue  # Metadaten, keine aispline-Attribute.
                    if k == 'position':
                        for a, x in zip('xyz', v):
                            _setzen(el, a, x, True)
                    elif k == 'gate_id' and liste == 'brake_lines' and bremslinien == 'vorlage':
                        el.set(k, abbildung[int(v)])
                    else:
                        _setzen(el, k, v, k in ('x', 'y', 'z'))
                elemente.append(el)
            _kinder(parent, singular, elemente)
            parent.set('num_'+liste, len(elemente))
        return doc

    def _ts(self, daten):
        doc = copy.deepcopy(self.dokumente['TS'])
        alt = {v.get('name'): v for v in doc.root.findall('VolumeList')}
        neu = []
        for vl in daten['volume_lists']:
            el = copy.deepcopy(alt.get(vl['name'], next(iter(alt.values()))))
            for k in ('name', 'type'):
                el.set(k, vl[k])
            el.set('closed', 'True' if vl['closed'] else 'False')
            el.set('numVolumes', len(vl['volumes']))
            vols = []
            for i, v in enumerate(vl['volumes']):
                vol = _vorlagen_element(el, 'Volume', i)
                vol.set('id', v['id'])
                parent = vol.find('Gates')
                tore = []
                for j, tor in enumerate(v['gates']):
                    gt = _vorlagen_element(parent, 'Gate', j)
                    for k in ('point1', 'point2', 'point3', 'point4'):
                        _vektor(gt, k, tor[k])
                    tore.append(gt)
                _kinder(parent, 'Gate', tore)
                vols.append(vol)
            _kinder(el, 'Volume', vols)
            neu.append(el)
        _kinder(doc.root, 'VolumeList', neu)
        return doc

    def _tm(self, daten, rest_behalten):
        doc = copy.deepcopy(self.dokumente['TM'])
        parent = doc.root.find('MarkupList')
        alt = {m.get('Name'): m for m in parent.elements}
        gruppen = {}
        for z in daten['markup']:
            gruppen.setdefault(z['Name'], []).append(z)
        neu = []
        for name, zonen in gruppen.items():
            el = copy.deepcopy(alt.get(name, bxml.Element('MarkupElement', children=[bxml.Element('Attributes')])))
            z = zonen[0]
            for k in ('Name', 'SplineName', 'Start', 'End'):
                _setzen(el, k, z[k], k in ('Start', 'End'))
            attrs = el.find('Attributes')
            attralt = {a.get('Type'): a for a in attrs.elements}
            attneu = []
            for z in zonen:
                if any(z[k] != zonen[0][k] for k in ('SplineName', 'Start', 'End')):
                    raise ValueError('TM zone name %s has contradictory ranges' % name)
                att = copy.deepcopy(attralt.get(z['Type'], bxml.Element('Attribute')))
                att.set('Type', z['Type'])
                props = att.find('Properties')
                if props is None:
                    props = att.append(bxml.Element('Properties'))
                propalt = {p.get('Type'): p for p in props.elements}
                # lade() reduziert wiederholte Property-Typen auf den letzten
                # Wert (Portimão: Hunderte crowd_zone-Verweise). Bei gleicher
                # reduzierter Sicht die gesamte Vorlagenfolge erhalten.
                reduziert = {p.get('Type'): p.get('Value') for p in props.elements}
                geliefert = {k: str(v) for k, v in z.get('Properties', {}).items()}
                if geliefert == reduziert:
                    attneu.append(att)
                    continue
                if len(propalt) != len(props.elements):
                    raise ValueError('Repeated TM properties cannot be changed unambiguously in the lade() model')
                propneu = []
                for k, v in z.get('Properties', {}).items():
                    p = copy.deepcopy(propalt.get(k, bxml.Element('Property')))
                    p.set('Type', k)
                    p.set('Value', v)
                    propneu.append(p)
                _kinder(props, 'Property', propneu)
                attneu.append(att)
            _kinder(attrs, 'Attribute', attneu)
            neu.append(el)
        if rest_behalten:
            # Nur Typen, die der Generator geliefert hat, ersetzen. Sonst würden
            # andere Namen (z.B. drs_detection_1) widersprüchliche Doppelzonen erzeugen.
            typen = {z['Type'] for z in daten['markup']}
            for name, el in alt.items():
                if name in gruppen:
                    continue
                e = copy.deepcopy(el)
                attrs = e.find('Attributes')
                _kinder(attrs, 'Attribute', [a for a in attrs.elements if a.get('Type') not in typen])
                if attrs.elements:
                    neu.append(e)
        _kinder(parent, 'MarkupElement', neu)
        return doc

    def _layer(self, daten):
        paket = pkg.lesen(self.roh['vehicles_layer'][0])
        instanzen = paket.instanzen()
        lookup = {_platzschluessel(i.name): i for i in instanzen if streckenlogik.PLATZ_RE.search(i.name or '')}
        gesehen = set()
        for platz in daten['placements']:
            key = _platzschluessel(platz['name'])
            if key in gesehen:
                raise ValueError('Duplicate vehicle slot')
            gesehen.add(key)
            if key not in lookup:
                if key[0] not in ('gridslot', 'pitbox'):
                    raise ValueError('New garages need consistent external placement references')
                kandidaten = [i for k, i in lookup.items() if k[0]==key[0]]
                if not kandidaten or 'matrix' not in platz or 'VehPlacement' not in platz:
                    raise ValueError('New vehicle slot needs a template, matrix and VehPlacement')
                # Platz >1 besitzt einen expliziten Id-Override, anders als Pole.
                vorlage = max(kandidaten, key=lambda i: _platzschluessel(i.name)[1])
                uid = platz.get('uniqueid', uuid.uuid5(uuid.NAMESPACE_URL, self.code+':'+platz['name']).hex)
                lookup[key] = _platz_klonen(paket, vorlage, platz['name'], uid)
                platz['VehPlacement']['Id'] = key[1]
            inst = lookup[key]
            _struktur_eindeutig(paket, inst)
            m = list(platz.get('matrix', inst.matrix))
            if 'matrix' not in platz:
                m[12:15] = platz['position']
            if len(m) != 16 or not all(math.isfinite(v) for v in m):
                raise ValueError('Invalid placement matrix')
            if any(abs(m[i]-v) > 1e-6 for i, v in zip((3, 7, 11, 15), (0, 0, 0, 1))):
                raise ValueError('Placement matrix is not affine')
            feld = _pfad(paket, inst.block, 'overrides', 'WorldObject', 'transform')
            _tabellenwert(paket, feld, struct.pack('<16f', *m))
            # Vorlagenname/woid beibehalten: F1Garage enthält wo id-Verweise auf
            # PitGarage. Die neutralen UUIDs ersetzen diese nicht einseitig.
            if 'VehPlacement' in platz:
                comp = _pfad(paket, inst.block, 'overrides', 'VehPlacement').wert.ziel
                for k, v in platz['VehPlacement'].items():
                    try:
                        f = paket.feld(comp, k)
                    except pkg.PkgFormatError:
                        # Id=1 ist bei Pole/erster Box im Template geerbt.
                        if k == 'Id' and int(v) == 1:
                            continue
                        raise ValueError('Placement override %s is missing in the template' % k)
                    tab = paket.tabelle(f.wert)
                    if tab.datentyp == b'shnm':
                        raw = str(v).encode('ascii')
                        if len(raw) >= 16:
                            raise ValueError('Placement group is too long')
                        raw = raw.ljust(16, b'\0')
                    else:
                        raw = struct.pack('<'+pkg.FORMATE[tab.datentyp], v)
                    _tabellenwert(paket, f, raw)
        paket.pruefen()
        return pkg.schreiben(paket)

    def schreiben(self, logik, bremslinien=None, tm_rest_behalten=True):
        """Alle sechs Ressourcen serialisieren, ohne das Vorlagenarchiv zu ändern."""
        rundlauf = isinstance(logik, streckenlogik.Strecke)
        if rundlauf:
            logik = neutral_aus_strecke(logik)
        achsen = logik.get('koordinatensystem', {}).get('achsen', 'y_oben_rechtshaendig')
        if achsen != 'y_oben_rechtshaendig':
            raise ValueError('Game adapter needs Y up, right-handed')
        sp = copy.deepcopy(logik.get('spielformate', logik))
        pruefe_neutral(sp)
        bremslinien = bremslinien or ('neutral' if rundlauf else 'vorlage')
        if bremslinien not in ('neutral', 'vorlage'):
            raise ValueError('Unknown braking line mode')
        ids = {int(l['id']) for l in sp['AISplineData'].get('brake_lines', [])}
        original_ids = {int(l['id']) for l in self.original.linien['brake_lines']}
        if bremslinien == 'neutral' and ids != original_ids:
            raise ValueError('New brake IDs require consistently new car settings')
        abbildung = {} if bremslinien == 'neutral' else self._gate_abbildung(sp['AISplineData'], sp['TrackSpace']['splines'])
        docs = {'TrackSpace': self._trackspace(sp['TrackSpace']),
                'AISplineData': self._aispline(sp['AISplineData'], bremslinien, abbildung),
                'TM': self._tm(sp['TM'], tm_rest_behalten), 'TS': self._ts(sp['TS'])}
        result = {}
        for typ, doc in docs.items():
            fragmente = [bxml.encode(doc)]
            for raw in self.roh[typ][1:]:
                fragmente.append(bxml.compute_tsmd(doc) if bxml.is_tsmd(raw) else raw)
            result[self.ressourcen[typ].identifier] = tuple(fragmente)
        main = next(s for s in sp['TrackSpace']['splines'] if s['closed'])
        # Beim identischen TrackSpace stammen die Stützpunkte aus der Vorlage;
        # die QT02-Knoten werden dennoch vollständig neu gebaut.
        if main['position'] == self.original.hauptlinie.punkte:
            baum = tsquadtree.aus_stuetzpunkten(tsquadtree.lesen(self.roh['TSQuadTree'][0]).punkte)
            qt = tsquadtree.schreiben(baum)
        else:
            qt = tsquadtree.tsquadtree_bauen(main['position'], interpolation='linear')
        result[self.ressourcen['TSQuadTree'].identifier] = (qt,)
        result[self.ressourcen['vehicles_layer'].identifier] = (self._layer(sp['vehicles_layer']),)
        self.protokoll = {'bremslinienmodus': bremslinien, 'brems_ids': sorted(original_ids),
                         'auto_settings': len(self.bremsressourcen),
                         'reifengruppen_geprueft': len(self.bremspruefung),
                         'gate_abbildung': abbildung,
                         'quadtree_sampling': 'Vorlagenstützpunkte' if main['position'] == self.original.hauptlinie.punkte else 'lineare 3D-Näherung'}
        return result


def pruefe_neutral(sp):
    """Wesentliche Referenzen und Geometriezähler vor dem Schreiben prüfen."""
    def endlich(v):
        if isinstance(v, dict):
            for x in v.values():
                endlich(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                endlich(x)
        elif isinstance(v, (int, float)) and not math.isfinite(v):
            raise ValueError('Non-finite logic value')
    endlich(sp)
    splines = sp['TrackSpace']['splines']
    namen = {s['name'] for s in splines}
    if len(namen) != len(splines) or sum(bool(s['closed']) for s in splines) != 1:
        raise ValueError('TrackSpace needs unique names and a main ring')
    for s in splines:
        if len(s['position']) < (3 if s['closed'] else 2):
            raise ValueError('Too few spline nodes')
        for p in s['position']:
            if len(p) != 3:
                raise ValueError('Spline point needs XYZ')
    ai = sp['AISplineData']
    gids = [int(g['id']) for g in ai['gates']]
    if len(set(gids)) != len(gids):
        raise ValueError('Duplicate gate ID')
    for g in ai['gates']:
        if len(g['position']) != 3 or len(g['normal']) != 3 or abs(math.sqrt(sum(v*v for v in g['normal']))-1) > .002:
            raise ValueError('Invalid gate position or unit normal')
        wp = g['waypoints']
        if len({w['type'] for w in wp}) != len(wp) or len(wp) != (7 if 'pit' in g['name'] else 9):
            raise ValueError('AI gate needs nine or seven unique waypoints')
    links = ai['links']
    if [int(l['id']) for l in links] != list(range(len(links))):
        raise ValueError('Link IDs must be consecutive')
    for l in links:
        if int(l['from_gate']) not in gids or int(l['to_gate']) not in gids:
            raise ValueError('Link refers to a missing gate')
    for r in ai['routes']:
        a, b = int(r['from_link']), int(r['to_link'])
        if not 0 <= a <= b < len(links):
            raise ValueError('Route refers to a missing link')
        if r['type'] == 'track':
            if links[b]['to_gate'] != links[a]['from_gate']:
                raise ValueError('Main route is not closed')
        for i in range(a, b):
            if links[i]['to_gate'] != links[i+1]['from_gate']:
                raise ValueError('Route is not contiguous')
    for liste in ('brake_lines', 'corner_start_lines', 'corner_end_lines', 'retire_lines'):
        for l in ai.get(liste, []):
            for k in ('gate_id', 'pair_id'):
                if k in l and int(l[k]) not in gids:
                    raise ValueError('%s refers to a missing gate' % liste)
    for vl in sp['TS']['volume_lists']:
        if vl.get('numVolumes', len(vl['volumes'])) != len(vl['volumes']):
            raise ValueError('TS volume counter is wrong')
        if [int(v['id']) for v in vl['volumes']] != list(range(len(vl['volumes']))):
            raise ValueError('TS volume IDs must be consecutive')
        for i, v in enumerate(vl['volumes']):
            if len(v['gates']) != 2:
                raise ValueError('TS volume needs two gates')
            for g in v['gates']:
                for k in ('point1', 'point2', 'point3', 'point4'):
                    if len(g[k]) != 3:
                        raise ValueError('TS gate needs four XYZ points')
            if i and vl['volumes'][i-1]['gates'][1] != v['gates'][0]:
                raise ValueError('Neighbouring TS volumes do not share the gate')
        if vl['closed'] and vl['volumes'][-1]['gates'][1] != vl['volumes'][0]['gates'][0]:
            raise ValueError('TS main route is not closed')
    for z in sp['TM']['markup']:
        if z['SplineName'] not in namen or not (0 <= float(z['Start']) <= 1 and 0 <= float(z['End']) <= 1):
            raise ValueError('Invalid TM spline or progress')


def erp_schreiben(quelle, ziel, ressourcen, ueberschreiben=False):
    """Nur abweichende Fragmente setzen, andere Ressourcen gepackt erhalten."""
    ziel = Path(ziel).resolve()
    if spielpfade.geschuetzt(ziel):
        raise ValueError('Protected output path: %s' % ziel)
    with erp.open_erp(quelle) as a:
        for kennung, fragmente in ressourcen.items():
            r = a.find(kennung)
            if r is None or len(r.fragments) != len(fragmente):
                raise ValueError('Resource or fragment structure is missing: %s' % kennung)
            for i, raw in enumerate(fragmente):
                if r.fragments[i].read() != raw:
                    r.set_fragment_data(i, raw)
        ziel.parent.mkdir(parents=True, exist_ok=True)
        a.write(ziel, overwrite=ueberschreiben)
