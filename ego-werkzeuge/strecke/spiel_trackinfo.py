"""AP14: TrackInfo einer neuen Strecke in einem geöffneten ERP ersetzen.

Die Ressourcennamen/ERP-Metadaten kommen vom Träger. Das Höhenfeld stammt
standardmäßig aus dessen bereits ersetzter TRN~-Geometrie. Installiert nichts.
Keine Abhängigkeit von den gleichzeitig bearbeiteten AP13-/AP15-Modulen.
"""

import copy

import numpy as np

from ego import streckenlogik, trackinfo, trn
from . import platzierung


def _logikpunkte(logik, weltkoordinaten):
    if isinstance(logik, streckenlogik.Strecke):
        return np.vstack([s.punkte for s in logik.splines.values()])
    if not isinstance(logik, dict):
        raise trackinfo.TrackInfoFehler("Neutrale Generatorlogik oder gelesene Strecke erwartet")
    sp = logik.get('spielformate', logik)
    p = np.vstack([s['position'] for s in sp['TrackSpace']['splines']])
    platziert = bool(logik.get('platzierung')) if weltkoordinaten is None else weltkoordinaten
    return p if platziert else platzierung.punkte(p)


def _physiknetz(archiv, code):
    kandidaten = [r for r in archiv if r.resource_type == 'TRN~'
                  and '/tracks/%s/terrain/' % code in r.identifier]
    if len(kandidaten) != 1:
        raise trackinfo.TrackInfoFehler("Genau eine Strecken-TRN~ erwartet, gefunden: %d" % len(kandidaten))
    p, t, codes = trn.mesh(trn.open_trn(kandidaten[0].data()).geometry())
    # Mauern tragen keinen Fahrbahnhöhenwert bei; vertikale Flächen würden
    # ohnehin entfallen, horizontale Mauerkappen müssen ebenfalls entfallen.
    return p, t[codes != 'SCON'], kandidaten[0].identifier


def trackinfo_fuer_strecke(archiv, logik, *, code='portimao', netz=None,
                           weltkoordinaten=None, netz_weltkoordinaten=False,
                           rand_m=32.0, grundhoehe=None, sonnenwinkel=None,
                           max_rasterpunkte=16_777_216):
    """Binary und Tex ersetzen, serialisierbaren Prüfbericht zurückgeben.

    ``logik``: Generatorausgang oder ``streckenlogik.Strecke``. Generatorlogik
    ohne ``platzierung`` wird mit platzierung.py verschoben. Bereits platzierte
    Daten ausdrücklich mit weltkoordinaten=True kennzeichnen; eine gelesene
    Strecke ist immer in Weltkoordinaten.

    ``netz``: optional (Positionen, Dreiecke), standardmäßig Generatorraum;
    netz_weltkoordinaten=True für bereits verschobene Daten. Ohne Netz wird
    das TRN~ aus archiv gelesen, also die tatsächlich eingebaute Fahrphysik.
    ``grundhoehe`` gilt für Proben außerhalb von Dreiecken; Standard ist die
    Medianhöhe der Logik. Das Raster umfasst auch verlängerte Boxensplines.

    Die Textur wird konstant, daher unabhängig von Fortschritts-/UV-Offsets.
    Der vorhandene Sonnenwinkel bleibt erhalten, sofern nicht überschrieben.
    Nur zwei Ressourcen werden geändert; beide müssen ein Fragment besitzen.
    """
    ressourcen = {}
    for typ in ('TrackInfoBinary', 'TrackInfoTex'):
        rr = [r for r in archiv if r.resource_type == typ and '/tracks/%s/' % code in r.identifier]
        if len(rr) != 1 or len(rr[0].fragments) != 1:
            raise trackinfo.TrackInfoFehler("Genau eine einfragmentige %s-Ressource für %s erwartet" % (typ, code))
        ressourcen[typ] = rr[0]
    alt = trackinfo.TrackInfoBinary.aus_bytes(ressourcen['TrackInfoBinary'].data())
    tex = trackinfo.TrackInfoTex.aus_bytes(ressourcen['TrackInfoTex'].data())
    lp = np.asarray(_logikpunkte(logik, weltkoordinaten), dtype=float)
    if lp.ndim != 2 or lp.shape[1] != 3 or not len(lp) or not np.isfinite(lp).all():
        raise trackinfo.TrackInfoFehler("Streckenlogik enthält keine endlichen XYZ-Punkte")
    if netz is None:
        p, t, quelle = _physiknetz(archiv, code)
    else:
        p, t = netz
        p = np.asarray(p, dtype=float)
        if not netz_weltkoordinaten:
            p = platzierung.punkte(p)
        quelle = 'übergebenes Dreiecksnetz'
    if grundhoehe is None:
        grundhoehe = float(np.median(lp[:, 1]))
    props = copy.deepcopy(alt.eigenschaften)
    if sonnenwinkel is not None:
        props = [p for p in props if p.pfad != '/sky/sunRotationAngle']
        props.append(trackinfo.Eigenschaft.sonnenwinkel(sonnenwinkel))
    neu = trackinfo.hoehenfeld_aus_netz(p, t, eigenschaften=props, rand_m=rand_m,
                                      grundhoehe=grundhoehe, zusaetzliche_punkte=lp,
                                      max_rasterpunkte=max_rasterpunkte)
    neu_tex = trackinfo.textur_neutral(tex)
    binaer_bytes, tex_bytes = neu.to_bytes(), neu_tex.to_bytes()
    # Parser prüfen vor der ersten ERP-Mutation.
    if trackinfo.TrackInfoBinary.aus_bytes(binaer_bytes).to_bytes() != binaer_bytes:
        raise trackinfo.TrackInfoFehler("Neues Höhenfeld ist nicht bitstabil")
    if trackinfo.TrackInfoTex.aus_bytes(tex_bytes).to_bytes() != tex_bytes:
        raise trackinfo.TrackInfoFehler("Neue Textur ist nicht bitstabil")
    abdeckung = neu.abdeckung(lp)
    if abdeckung['abgedeckt'] != len(lp):
        raise trackinfo.TrackInfoFehler("Neues Höhenfeld deckt die Logik nicht vollständig ab")
    ressourcen['TrackInfoBinary'].set_fragment_data(0, binaer_bytes)
    ressourcen['TrackInfoTex'].set_fragment_data(0, tex_bytes)
    return {'ressourcen': {typ: r.identifier for typ, r in ressourcen.items()},
            'geometriequelle': quelle, 'grundhoehe': grundhoehe,
            'alt': {'minimum': list(alt.minimum), 'maximum': list(alt.maximum),
                    'bloecke': len(alt.raster), 'logik': alt.abdeckung(lp)},
            'neu': {'minimum': list(neu.minimum), 'maximum': list(neu.maximum),
                    'hoehe_min': neu.hoehe_min, 'hoehe_max': neu.hoehe_max,
                    'hoehen_schritt': neu.hoehen_schritt, 'bloecke': len(neu.raster),
                    'binaer_bytes': len(binaer_bytes), 'textur_bytes': len(tex_bytes),
                    'textur_bgra': [0, 239, 0, 105], 'logik': abdeckung}}
