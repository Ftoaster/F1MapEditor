"""Kommandozeile für den formatunabhängigen Streckengenerator (AP6)."""
import argparse
import sys
from strecke.erzeugung import erzeugen


def main(argv=None):
    parser = argparse.ArgumentParser(description='Mittellinie und Breiten in neutrale Streckendaten umwandeln.')
    sub = parser.add_subparsers(dest='befehl',required=True)
    for name, beschr in (('erzeugen','Geometrie, Logik und Vorschau erzeugen'),
                         ('vorschau','Nur SVG/PNG und Berechnungsbericht erzeugen')):
        p = sub.add_parser(name,help=beschr)
        p.add_argument('definition',metavar='DEFINITION.json')
        p.add_argument('ausgabe',metavar='AUSGABEORDNER')
    args = parser.parse_args(argv)
    try:
        erg = erzeugen(args.definition,args.ausgabe,nur_vorschau=args.befehl=='vorschau')
    except (OSError,ValueError,RuntimeError,KeyError) as exc:
        print('Fehler: %s' % exc,file=sys.stderr)
        return 1
    b = erg['bericht']
    print('%s: %.1f m, %d Kurven, Runde %s' % (b['name'],b['laenge_m'],b['kurven'],b['rundenzeit']))
    print('%d Dreiecke; Berechnung %.2f s; Ausgabe: %s' % (b['dreiecke'],b['berechnung_s'],args.ausgabe))
    for warnung in b['warnungen']:
        print('Warnung: %s' % warnung)
    return 0


if __name__=='__main__':
    sys.exit(main())
