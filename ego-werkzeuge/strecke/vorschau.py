"""Draufsicht als SVG und PNG (Pillow), aus denselben Dreiecken und Markierungen.

+X liegt rechts, +Z unten. Die Farben dienen nur der Vorschau. Geometrie bleibt
unverändert; Markierungen und Geschwindigkeitsfarben liegen darüber.
"""

import html
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .definition import OBERFLAECHEN
from .geometrie import FARBEN
from .mittellinie import periodisch_interp
from .rennlinie import rundenzeit_text


def _farbe(v, lo=50, hi=320):
    t = float(np.clip((v*3.6-lo)/(hi-lo), 0, 1))
    return (int(240*(1-t)+30*t), int(60*(1-t)+210*t), int(50*(1-t)+230*t))


def zeichnen(pfad, ml, netz, rl, logik, breite=1600, hoehe=1100, ausschnitt=None):
    """Schreibt PNG und SVG; pfad ist ein Dateistamm ohne Erweiterung."""
    pfad = Path(pfad)
    pos = netz['vertices'][:, [0, 2]]
    if ausschnitt is None:
        low, high = pos.min(axis=0), pos.max(axis=0)
    else:
        low, high = np.array(ausschnitt[0]), np.array(ausschnitt[1])
    rahmen = 45
    fuss = 100
    mass = min((breite-2*rahmen)/(high[0]-low[0]), (hoehe-fuss-2*rahmen)/(high[1]-low[1]))
    offset = np.array([(breite-mass*(high[0]-low[0]))/2,
                       (hoehe-fuss-mass*(high[1]-low[1]))/2])-low*mass
    def xy(p):
        a = np.asarray(p)
        return a[:, [0,2]]*mass+offset if a.shape[-1]==3 else a*mass+offset
    im = Image.new('RGB', (breite,hoehe), '#16211a')
    draw = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 15)
        klein = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 11)
    except OSError:
        font = klein = ImageFont.load_default()
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">' % (breite,hoehe,breite,hoehe),
           '<rect width="100%" height="100%" fill="#16211a"/>']
    def col(c):
        return '#%02x%02x%02x' % tuple(c)
    def linie(p,c,w=2):
        pp = xy(np.asarray(p))
        draw.line([tuple(a) for a in pp], fill=c, width=max(1,int(w)))
        svg.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="%.2f"/>' %
                   (' '.join('%.2f,%.2f' % tuple(a) for a in pp), col(c),w))
    def polygon(p,c):
        pp = xy(np.asarray(p))
        draw.polygon([tuple(a) for a in pp],fill=c)
        svg.append('<polygon points="%s" fill="%s"/>' %
                   (' '.join('%.2f,%.2f' % tuple(a) for a in pp),col(c)))
    def text(p,t,c=(245,245,245),kleintext=False):
        xx,yy = p
        draw.text((xx,yy),t,font=klein if kleintext else font,fill=c)
        svg.append('<text x="%.2f" y="%.2f" font-family="Arial,sans-serif" font-size="%d" fill="%s">%s</text>' %
                   (xx,yy+(11 if kleintext else 15),11 if kleintext else 15,col(c),html.escape(t)))
    vp = pos*mass+offset
    # Sichtbare Bodenklassen zuerst, Mauern zuletzt. Keine Fantasie-Texturen.
    for name in ('gras','kies','asphalt','boxengasse','auslauf','randstein','linie','startbox','mauer','gebaeude'):
        oi = OBERFLAECHEN.index(name)
        ff = netz['dreiecke'][netz['oberflaeche']==oi]
        c = tuple(int(v*255) for v in FARBEN[name])
        paths = []
        for f in ff:
            pp = vp[f]
            if np.any(pp.max(axis=0)<0) or np.any(pp.min(axis=0)>[breite,hoehe-fuss]):
                continue
            draw.polygon([tuple(a) for a in pp],fill=c)
            paths.append('M%.2f %.2fL%.2f %.2fL%.2f %.2fZ' % tuple(pp.ravel()))
        svg.append('<path fill="%s" d="%s"/>' % (col(c),''.join(paths)))
    # Markierungen DRS (parallel zur Mittellinie) und Bremspunkt-Erkennung.
    for d in logik['drs']:
        a,e = d['aktivierung_s_m'],d['ende_s_m']
        if e<a:
            e += ml.laenge
        ss = np.linspace(a,e,max(2,int((e-a)/4)))
        st = ml.stationen(ss)
        linie(st.punkt(-st.wr-2), (230,205,30),3)
        for typ,c in (('erkennung_s_m',(255,175,30)),('aktivierung_s_m',(255,235,30))):
            st = ml.stationen([d[typ]])
            linie(np.vstack([st.punkt(-st.wr)[0],st.punkt(st.wl)[0]]),c,3)
        text(xy(st.pos)[0]+[5,-20],'DRS %d' % (d['ID']+1), (255,235,30),True)
    # Ideallinie: Geschwindigkeitsfarbe, ca. 2 m pro Segment.
    idx = np.r_[np.arange(0,len(rl.pos),2),len(rl.pos)]
    for i,j in zip(idx[:-1],idx[1:]):
        linie(np.vstack([rl.pos[i],rl.pos[j % len(rl.pos)]]),_farbe(rl.v[i]),2)
    for sec in logik['sektoren'][1:]:
        st = ml.stationen([sec['s_start_m']])
        linie(np.vstack([st.punkt(-st.wr-4)[0],st.punkt(st.wl+4)[0]]),(210,120,255),3)
        text(xy(st.pos)[0]+[8,-20],'S%d' % sec['nummer'],(230,160,255))
    for i,p in enumerate(logik['startplaetze']):
        m = np.asarray(p['matrix']).reshape(4,4)
        lokal = np.array([[-1,0,-2.8,1],[1,0,-2.8,1],[1,0,2.8,1],[-1,0,2.8,1]])
        polygon((lokal@m)[:,:3],(240,240,240))
        if ausschnitt is not None:
            text(xy(np.array([p['position']]))[0]+[4,2],str(i+1),kleintext=True)
    for p in logik['boxenplaetze']:
        m = np.asarray(p['matrix']).reshape(4,4)
        lokal = np.array([[-1,0,-2.8,1],[1,0,-2.8,1],[1,0,2.8,1],[-1,0,2.8,1]])
        polygon((lokal@m)[:,:3],(255,170,60))
    linie(logik['start_ziel']['punkte'],(255,255,255),4)
    st = ml.stationen([30])
    linie(np.vstack([st.pos[0],st.pos[0]+st.t3[0]*35]),(255,255,255),3)
    # Kleine Pfeilspitze macht die Fahrtrichtung sichtbar.
    tip = st.pos[0]+st.t3[0]*35
    n = np.array([st.nl[0,0],0,st.nl[0,1]])
    linie(np.vstack([tip-st.t3[0]*12+n*7,tip,tip-st.t3[0]*12-n*7]),(255,255,255),3)
    text((20,hoehe-93),'%s | %.1f m | Runde %s | +X rechts, +Z unten' %
         (logik['name'],ml.laenge,rundenzeit_text(rl.rundenzeit)))
    for i,name in enumerate(OBERFLAECHEN):
        c = tuple(int(v*255) for v in FARBEN[name])
        x = 20+i*170
        draw.rectangle((x,hoehe-62,x+18,hoehe-44),fill=c)
        svg.append('<rect x="%d" y="%d" width="18" height="18" fill="%s"/>' % (x,hoehe-62,col(c)))
        text((x+25,hoehe-64),name)
    text((20,hoehe-32),'Ideallinie: rot 50 → blau 320 km/h   |   Gelb: DRS   |   Violett: Sektor   |   Orange: Boxenplätze')
    # Maßstab 100 m.
    meter = 100 if 100*mass <= 180 else 10
    x,y=breite-200,30
    draw.line((x,y,x+meter*mass,y),fill='white',width=2)
    svg.append('<path d="M%d %d h%.2f" stroke="white"/>' % (x,y,meter*mass))
    text((x,y+5),'%d m' % meter)
    svg.append('</svg>')
    im.save(pfad.with_suffix('.png'))
    pfad.with_suffix('.svg').write_text('\n'.join(svg),encoding='utf-8')
    return {'png': str(pfad.with_suffix('.png')), 'svg': str(pfad.with_suffix('.svg'))}


def vorschau_schreiben(ordner, ml, netz, rl, logik):
    """Übersicht plus vergrößerter Start-/Boxenbereich."""
    ordner = Path(ordner)
    dateien = zeichnen(ordner/'vorschau',ml,netz,rl,logik)
    p = np.array([s['position'] for s in logik['startplaetze']]+logik['start_ziel']['punkte']+
                 [s['position'] for s in logik['boxenplaetze']])[:,[0,2]]
    dateien['startbereich'] = zeichnen(ordner/'vorschau_start',ml,netz,rl,logik,
                                      ausschnitt=(p.min(axis=0)-[35,35],p.max(axis=0)+[35,35]))
    return dateien
