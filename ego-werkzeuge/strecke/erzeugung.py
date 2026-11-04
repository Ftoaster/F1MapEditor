"""Gesamtablauf Definition -> geprüfte Mittellinie, Netz, Logik und Vorschau.

Schreibt ausschließlich neutrale Daten (JSON, NPZ, OBJ, CSV, SVG, PNG), keine
Spielformate. Vor jedem Schreiben werden geschützte Pfade und Quellen geprüft.
"""

import json
import os
from pathlib import Path
import time

import numpy as np

from ego import spielpfade
from . import definition, mittellinie, querprofil, rennlinie, geometrie, logik


def ausgabe_pruefen(ordner, quellen=()):
    """Verweigert Spielordner, Downloads und das Überschreiben der Definitionsquellen."""
    ziel = Path(ordner).resolve()
    if spielpfade.geschuetzt(ziel):
        raise ValueError('Protected output folder: %s' % ziel)
    namen = {'strecke_logik.json','strecke_netz.json','strecke.npz','strecke.obj','strecke.mtl',
             'mittellinie.csv','definition_aufgeloest.json','bericht.json',
             'vorschau.svg','vorschau.png','vorschau_start.svg','vorschau_start.png'}
    for quelle in quellen:
        if quelle and not str(quelle).startswith('<'):
            q = Path(quelle).resolve()
            if q.parent == ziel and q.name in namen:
                raise ValueError('Output would overwrite the input: %s' % q)
    return ziel


def berechnen(defn):
    """Berechnet ohne Dateiausgaben; ungültige Fahrbahnränder werden abgelehnt."""
    beginn = time.perf_counter()
    ml = mittellinie.Mittellinie(defn)
    pruefung = mittellinie.raender_pruefen(ml)
    if not pruefung['ok']:
        raise ValueError('Track edges folded or intersecting: %s' % pruefung)
    pk = defn.param['kurven']
    kurven = mittellinie.kurven_finden(ml, pk['kruemmung_schwelle'],pk['mindestwinkel_grad'],pk['zusammenfassen_m'])
    qp = querprofil.querprofil_berechnen(ml,defn.param,kurven)
    rl = rennlinie.ideallinie_berechnen(ml,defn.param)
    daten = logik.logik_berechnen(ml,qp,rl,defn.param,kurven)
    netz = geometrie.netz_erzeugen(ml,qp,defn.param)
    bericht = {'name': defn.name, 'laenge_m': ml.laenge, 'kurven': len(kurven),
               'rundenzeit_s': rl.rundenzeit, 'rundenzeit': rennlinie.rundenzeit_text(rl.rundenzeit),
               'geschwindigkeit_kmh': {'min': float(rl.v.min()*3.6),'max': float(rl.v.max()*3.6)},
               'berechnung_s': time.perf_counter()-beginn, 'optimierung': rl.optimierung['info'],
               'vertices': len(netz['vertices']), 'dreiecke': len(netz['dreiecke']),
               'entfernt_entartet': netz['entfernt_entartet'],
               'oberflaechen': geometrie.statistik(netz,defn.param['oberflaechen_codes']),
               'fahrbahnpruefung': pruefung, 'warnungen': ml.warnungen+qp.warnungen,
               'anzahl_drs': len(daten['drs']), 'anzahl_startplaetze': len(daten['startplaetze']),
               'anzahl_boxen': len(daten['boxenplaetze'])}
    if not defn.param['drs'].get('zonen') and len(daten['drs'])<defn.param['drs']['anzahl']:
        bericht['warnungen'].append('Nur %d geeignete DRS-Gerade(n) statt %d gefunden' %
                                    (len(daten['drs']),defn.param['drs']['anzahl']))
    daten['pruefung'] = bericht
    return {'definition': defn, 'mittellinie': ml, 'querprofil': qp, 'ideallinie': rl,
            'logik': daten, 'netz': netz, 'bericht': bericht}


def _json_schreiben(pfad,daten):
    with open(pfad,'w',encoding='utf-8',newline='\n') as f:
        json.dump(daten,f,ensure_ascii=False,allow_nan=False,indent=2)
        f.write('\n')


def _logik_achsen(daten, achsen):
    """Transformiert alle Positions-/Normalenfelder und pkg-Zeilenmatrizen."""
    mx = definition.achsen_matrix(achsen)
    koordinaten = {'position','normal','links','rechts','punkte','point1','point2','point3','point4'}
    def rek(obj, key=None):
        if key=='matrix':
            mat = np.asarray(obj).reshape(4,4).copy()
            mat[:,:3] = mat[:,:3] @ mx.T
            return mat.ravel().tolist()
        if key in koordinaten and isinstance(obj,list) and obj:
            try:
                arr = np.asarray(obj,dtype=float)
                if arr.shape[-1]==3 and arr.ndim<=2:
                    return (arr@mx.T).tolist()
            except (TypeError,ValueError):
                pass
        if isinstance(obj,dict):
            return {k: rek(v,k) for k,v in obj.items()}
        if isinstance(obj,list):
            return [rek(v) for v in obj]
        return obj
    erg = rek(daten)
    erg['koordinatensystem']['achsen'] = achsen
    return erg


def erzeugen(eingabe, ordner, nur_vorschau=False):
    """Erzeugt alle Ausgaben bzw. nur eine Vorschau aus einer JSON-Definition."""
    d = definition.laden(eingabe) if not isinstance(eingabe,definition.Streckendefinition) else eingabe
    quellen = [d.quelle]
    if d.param['mittellinie']['art']=='csv':
        quellen.append(os.path.join(d.basisordner,d.param['mittellinie']['datei']))
    ziel = ausgabe_pruefen(ordner,quellen)
    erg = berechnen(d)
    ziel.mkdir(parents=True,exist_ok=True)
    ax = d.param['ausgabe']['achsen']
    if not nur_vorschau:
        codes = d.param['oberflaechen_codes']
        geometrie.obj_schreiben(ziel/'strecke.obj',erg['netz'],ax)
        geometrie.npz_schreiben(ziel/'strecke.npz',erg['netz'],ax,codes)
        geometrie.json_schreiben(ziel/'strecke_netz.json',erg['netz'],ax,codes,
                                voll=d.param['ausgabe']['netz_json_voll'],
                                dateien={'npz':'strecke.npz','obj':'strecke.obj'})
        _json_schreiben(ziel/'strecke_logik.json',_logik_achsen(erg['logik'],ax))
        _json_schreiben(ziel/'definition_aufgeloest.json',d.param)
        ml = erg['mittellinie']
        definition.csv_schreiben(ziel/'mittellinie.csv',ml.ref.pos[:,0],ml.ref.pos[:,1],ml.ref.pos[:,2],
                                 ml.wl,ml.wr,achsen=ax,querneigung_grad=np.degrees(ml.bank))
    from .vorschau import vorschau_schreiben
    erg['vorschau'] = vorschau_schreiben(ziel,erg['mittellinie'],erg['netz'],erg['ideallinie'],erg['logik'])
    _json_schreiben(ziel/'bericht.json',erg['bericht'])
    return erg
