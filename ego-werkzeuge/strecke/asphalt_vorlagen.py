"""Fahrbahnoberflächen der Spielstrecken als Vorlage: aus den Strecken-ERPs extrahieren (PNG + katalog.json) und
beim Bau in die Asphalttexturen des Trägers (por_track_a) einsetzen.

Neue Strecken (Shader ts_ribbon) haben dieselben Slots wie por_track_a. Ältere (lt_ribbon: Bahrain, Imola, Madrid,
Melbourne, Suzuka) mischen Basis- und Breitkarten über eine Blendkarte; davon werden nur die Basiskarten übernommen
(experimentell, Kanalbelegung nicht geprüft)."""
import glob
import json
import os

import numpy as np

from ego import erp, grafik, grafik_textur, grafik_textur_schreiben

ORDNER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'asphalt_texturen')
KATALOG = 'katalog.json'
ZIEL_MATERIAL = 'por_track_a.material'
#: Zielslot (por_track_a) ← Quellslot je Shader
SLOTS = {
    'ts_ribbon': {s: s for s in ('me_albedoMap', 'me_normalMap', 'me_surfaceMap', 'me_detailAlbedoMap',
                                 'me_detailSurfaceMap')},
    'lt_ribbon': {'me_albedoMap': 'me_baseAlbedoMap', 'me_normalMap': 'me_baseNormalMap',
                  'me_surfaceMap': 'me_baseSurfaceMap'},
}
#: Formate ohne eigenen Kodierer → BC7 mit gleichem Farbraum
ERSATZFORMAT = {63: 69, 65: 69, 80: 69, 83: 69, 67: 70}
KODIERBAR = (15, 52, 54, 55, 57, 62, 69, 70)
NEBENFLAECHEN = ('off', 'pit', 'outfield', 'rumble', 'kerb', 'drain', 'runoff', 'astro', 'sausage', 'line', 'ground')


class VorlagenFehler(Exception):
    pass


def _shader_art(shader):
    for art in SLOTS:
        if shader.endswith('/' + art + '.nefx2'):
            return art
    return None


def _hauptmaterial(kandidaten):
    """Fahrbahnmaterial der Strecke: Name mit 'track'/'surface', ohne Neben-/Randflächen; 'main' bzw. '_a' bevorzugt."""
    def rang(k):
        n = k.rsplit('/', 1)[-1][:-len('.material')]
        return (0 if 'main' in n else 1, 0 if '_a' in n or n.endswith('surface') else 1, len(n), n)
    gut = [k for k in kandidaten
           if any(w in k.rsplit('/', 1)[-1] for w in ('track', 'surface'))
           and not any(w in k.rsplit('/', 1)[-1] for w in NEBENFLAECHEN)]
    return sorted(gut, key=rang)[0] if gut else None


def strecken_erps(spiel):
    """(Strecke, Pfad) der Streckendaten (2025 vor 2021, je Strecke eine Datei)."""
    gesehen = {}
    for gruppe in ('2025_asset_groups', '2021_asset_groups'):
        for p in sorted(glob.glob(os.path.join(spiel, gruppe, 'environment_package', 'tracks', '*', 'wep',
                                               '*_common.erp'))):
            gesehen.setdefault(p.split(os.sep)[-3], p)
    return sorted(gesehen.items())


def extrahieren(spiel, ordner=ORDNER, log=print):
    """Alle Strecken: Fahrbahnmaterial finden, Texturen als PNG (je Kennung einmal) und katalog.json schreiben."""
    os.makedirs(ordner, exist_ok=True)
    such = [spiel, os.path.join(spiel, '2025_asset_groups'), os.path.join(spiel, '2021_asset_groups')]
    vorlagen, fehler = {}, {}
    for strecke, pfad in strecken_erps(spiel):
        try:
            with erp.open_erp(pfad) as a:
                res = {r.identifier: r for r in a.resources}
                mats = {}
                for k, r in res.items():
                    if '/tracks/%s/terrain/materials/' % strecke not in k or not k.endswith('.material'):
                        continue
                    m = grafik.Material.aus_bytes(r.fragments[0].read())
                    if _shader_art(m.shader):
                        mats[k] = m
                haupt = _hauptmaterial(mats)
                if haupt is None:
                    fehler[strecke] = 'kein Fahrbahnmaterial'
                    continue
                m = mats[haupt]
                art = _shader_art(m.shader)
                tx = m.texturen()
                texturen = {}
                for ziel, quelle in SLOTS[art].items():
                    kennung = tx.get(quelle)
                    if not kennung:
                        continue
                    basis = kennung.split('?')[0]
                    r = res.get(basis + '.image') or res.get(basis)
                    if r is None:
                        fehler.setdefault(strecke, []).append('%s fehlt' % quelle)
                        continue
                    t = grafik.Textur.aus_ressource(r)
                    datei = basis.rsplit('/', 1)[-1].rsplit('.', 1)[0] + '.png'
                    ziel_png = os.path.join(ordner, datei)
                    if not os.path.isfile(ziel_png):
                        extern = None
                        if t.extern_pfad:
                            p = grafik_textur.extern_datei_finden(t, such)
                            if p is None:
                                fehler.setdefault(strecke, []).append('%s: Mipmaps fehlen' % quelle)
                                continue
                            extern = grafik_textur.extern_stufen(t, p)
                        grafik_textur.textur_bild(t, extern).save(ziel_png)
                    texturen[ziel] = {'datei': datei, 'quelle': basis, 'format': t.format}
                vorlagen[strecke] = {'material': haupt, 'shader': art, 'experimentell': art != 'ts_ribbon',
                                     'texturen': texturen}
                log('%-20s %-34s %s %d Texturen' % (strecke, haupt.rsplit('/', 1)[-1], art, len(texturen)))
        except Exception as e:      # eine defekte/fremde Datei soll die übrigen Strecken nicht verhindern
            fehler[strecke] = '%s: %s' % (type(e).__name__, e)
    katalog = {'version': 1, 'spiel': spiel, 'vorlagen': vorlagen, 'fehler': fehler}
    with open(os.path.join(ordner, KATALOG), 'w', encoding='utf-8') as f:
        json.dump(katalog, f, indent=1, ensure_ascii=False)
    return katalog


def katalog_laden(ordner=ORDNER):
    p = os.path.join(ordner, KATALOG)
    if not os.path.isfile(p):
        return {'vorlagen': {}}
    with open(p, encoding='utf-8') as f:
        return json.load(f)


SKID_ORDNER = os.path.join(ORDNER, 'skid')
SKID_SLOT = 'me_racingSkidsMap'
#: Angezeigte Typen (katalog.json). 1 Abu Dhabi, 2 Hungaroring, 3 Imola, 4 Losail, 5 Portimão
SKID_REIHENFOLGE = ('type1', 'type2', 'type3', 'type4', 'type5')


def skid_katalog_laden(ordner=SKID_ORDNER):
    p = os.path.join(ordner, KATALOG)
    if not os.path.isfile(p):
        return {'vorlagen': {}}
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def skid_einsetzen(archiv: erp.ErpFile, vorlage: str, ordner=SKID_ORDNER) -> dict:
    """me_racingSkidsMap von por_track_a durch eine der sechs Skidmaps ersetzen."""
    from PIL import Image
    eintrag = skid_katalog_laden(ordner)['vorlagen'].get(vorlage)
    if eintrag is None:
        raise VorlagenFehler('Skidvorlage %s nicht im Katalog (%s)' % (vorlage, os.path.join(ordner, KATALOG)))
    material = next((r for r in archiv.resources if r.identifier.endswith('/terrain/materials/' + ZIEL_MATERIAL)),
                    None)
    if material is None:
        raise VorlagenFehler('%s nicht im Träger' % ZIEL_MATERIAL)
    kennung = grafik.Material.aus_bytes(material.fragments[0].read()).texturen().get(SKID_SLOT)
    if not kennung:
        raise VorlagenFehler('%s hat keinen Slot %s' % (ZIEL_MATERIAL, SKID_SLOT))
    rgba = np.asarray(Image.open(os.path.join(ordner, eintrag['datei'])).convert('RGBA'))
    code = eintrag['format'] if eintrag['format'] in KODIERBAR else ERSATZFORMAT.get(eintrag['format'], 55)
    erg = grafik_textur_schreiben.textur_ersetzen(archiv, kennung.split('?')[0], rgba, format=code, mips='voll')
    return {'vorlage': vorlage, 'quelle': eintrag.get('quelle'), 'format': code,
            'groesse': [erg['breite'], erg['hoehe']], 'bytes': erg['bytes'],
            'extern_entfernt': erg['extern_entfernt']}


def einsetzen(archiv: erp.ErpFile, vorlage: str, ordner=ORDNER) -> dict:
    """Asphalttexturen von por_track_a durch die der Vorlage ersetzen (Format der Quelle, volle interne Mip-Kette)."""
    from PIL import Image
    eintrag = katalog_laden(ordner)['vorlagen'].get(vorlage)
    if eintrag is None:
        raise VorlagenFehler('Asphaltvorlage %s nicht im Katalog (%s)' % (vorlage, os.path.join(ordner, KATALOG)))
    material = next((r for r in archiv.resources if r.identifier.endswith('/terrain/materials/' + ZIEL_MATERIAL)),
                    None)
    if material is None:
        raise VorlagenFehler('%s nicht im Träger' % ZIEL_MATERIAL)
    tx = grafik.Material.aus_bytes(material.fragments[0].read()).texturen()
    ersetzt = {}
    for slot, info in eintrag['texturen'].items():
        kennung = tx.get(slot)
        if not kennung:
            continue
        rgba = np.asarray(Image.open(os.path.join(ordner, info['datei'])).convert('RGBA'))
        code = info['format'] if info['format'] in KODIERBAR else ERSATZFORMAT.get(info['format'], 69)
        erg = grafik_textur_schreiben.textur_ersetzen(archiv, kennung.split('?')[0], rgba, format=code, mips='voll')
        ersetzt[slot] = {'quelle': info['quelle'], 'format': code, 'groesse': [erg['breite'], erg['hoehe']],
                         'bytes': erg['bytes'], 'extern_entfernt': erg['extern_entfernt']}
    return {'vorlage': vorlage, 'experimentell': eintrag.get('experimentell', False), 'ersetzt': ersetzt}


if __name__ == '__main__':
    import argparse
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(description='Fahrbahntexturen der Spielstrecken extrahieren')
    ap.add_argument('spiel', help='F1-25-Spielordner')
    ap.add_argument('--ordner', default=ORDNER)
    k = extrahieren(ap.parse_args().spiel, ap.parse_args().ordner)
    print('%d Vorlagen, Fehler: %s' % (len(k['vorlagen']), k['fehler'] or '-'))
