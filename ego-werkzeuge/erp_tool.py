#!/usr/bin/env python3
"""Kommandozeile für ERP-Archive (F1 25).

Beispiele:
    python erp_tool.py liste    "...\\bahrain_common.erp"
    python erp_tool.py pruefen  "...\\ferrari.erp"
    python erp_tool.py rundlauf "...\\ferrari.erp"
    python erp_tool.py entpacken "...\\ferrari.erp" entpackt\\ferrari --filter thpd
    python erp_tool.py ersetzen "...\\ferrari.erp" "eaid://.../ferrari.thpd" neu.bin ausgabe\\ferrari.erp

Sicherheit: Das Werkzeug schreibt nie in die Quelldatei und verweigert Ausgaben
im Spielordner (steamapps\\common\\F1 25), außer mit --spielordner-erlauben.
"""

import argparse
import collections
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ego import compression, erp  # noqa: E402

_INVALID = re.compile(r'[<>:"|?*\x00-\x1f]')


def _is_game_folder(path: str) -> bool:
    p = os.path.abspath(path).replace("\\", "/").lower()
    return "/steamapps/common/f1 25" in p or "/mnt/f1 25" in p


def _check_output(path: str, args) -> None:
    if _is_game_folder(path) and not getattr(args, "spielordner_erlauben", False):
        sys.exit("Abbruch: Ausgabe im Spielordner ist gesperrt (%s). "
                 "Nur mit --spielordner-erlauben möglich." % path)


def _safe_relpath(identifier: str) -> str:
    rel = identifier.split("://", 1)[-1]
    rel = rel.replace("?", "@").replace("&", "+")
    parts = [_INVALID.sub("_", p) or "_" for p in rel.split("/")]
    return os.path.join(*parts)


def cmd_liste(args):
    with erp.open_erp(args.datei) as a:
        print("Datei:        %s" % args.datei)
        print("Ressourcen:   %d   Fragmente: %d   Layout lückenlos: %s"
              % (len(a), a.fragment_count, "ja" if a.layout_contiguous else "NEIN"))
        rx = re.compile(args.filter) if args.filter else None
        types = collections.Counter()
        sizes = collections.Counter()
        for r in a:
            if rx and not (rx.search(r.identifier) or rx.search(r.resource_type)):
                continue
            types[r.resource_type] += 1
            sizes[r.resource_type] += sum(f.size for f in r.fragments)
            if args.details:
                frs = ", ".join("%s %d B %s" % (f.name_str, f.size, erp.compression_name(f.compression))
                                for f in r.fragments)
                print("  [%s] %s  (%s)" % (r.resource_type, r.identifier, frs))
        print("\nRessourcentypen:")
        for t, c in types.most_common():
            print("  %-18s %7d   %10.2f MB entpackt" % (t, c, sizes[t] / 1e6))


def cmd_pruefen(args):
    bad = 0
    total = 0
    with erp.open_erp(args.datei) as a:
        for r in a:
            total += 1
            try:
                ok = r.verify()
            except Exception as e:  # Entpackfehler
                ok = False
                print("FEHLER %s: %s" % (r.identifier, e))
            if not ok:
                bad += 1
                print("Hash stimmt nicht: %s" % r.identifier)
    print("%d Ressourcen geprüft, %d fehlerhaft" % (total, bad))
    return 1 if bad else 0


def cmd_rundlauf(args):
    with erp.open_erp(args.datei) as a, open(args.datei, "rb") as orig:
        h_new, h_old = hashlib.sha256(), hashlib.sha256()
        pos = 0
        first_diff = None
        for chunk in a.iter_bytes():
            ref = orig.read(len(chunk))
            if first_diff is None and ref != chunk:
                for i in range(min(len(ref), len(chunk))):
                    if ref[i] != chunk[i]:
                        first_diff = pos + i
                        break
                else:
                    first_diff = pos + min(len(ref), len(chunk))
            h_new.update(chunk)
            h_old.update(ref)
            pos += len(chunk)
        rest = orig.read(1)
        if rest and first_diff is None:
            first_diff = pos
        if args.ausgabe:
            _check_output(args.ausgabe, args)
            a.write(args.ausgabe, overwrite=args.ueberschreiben)
            print("Neu verpackte Datei geschrieben: %s" % args.ausgabe)
    if first_diff is None:
        print("IDENTISCH: %d Byte, SHA-256 %s" % (pos, h_new.hexdigest()))
        return 0
    print("ABWEICHUNG ab Byte %d" % first_diff)
    return 1


def cmd_entpacken(args):
    _check_output(args.ziel, args)
    rx = re.compile(args.filter) if args.filter else None
    listing = []
    n = 0
    with erp.open_erp(args.datei) as a:
        for r in a:
            if rx and not (rx.search(r.identifier) or rx.search(r.resource_type)):
                continue
            rel = _safe_relpath(r.identifier)
            frags = []
            for i, fr in enumerate(r.fragments):
                out = os.path.join(args.ziel, "%s#%d.%s" % (rel, i, _INVALID.sub("_", fr.name_str.strip()) or "frag"))
                os.makedirs(os.path.dirname(out), exist_ok=True)
                with open(out, "wb") as f:
                    f.write(fr.read())
                frags.append({"datei": os.path.relpath(out, args.ziel), "name": fr.name_str, "groesse": fr.size,
                              "flags": fr.flags, "kompression": fr.compression})
                n += 1
            listing.append({"kennung": r.identifier, "typ": r.resource_type, "unbekannt": r.unknown,
                            "md5": r.hash.hex(), "fragmente": frags})
    os.makedirs(args.ziel, exist_ok=True)
    with open(os.path.join(args.ziel, "inhalt.json"), "w", encoding="utf-8") as f:
        json.dump({"quelle": os.path.abspath(args.datei), "ressourcen": listing}, f, ensure_ascii=False, indent=1)
    print("%d Fragmente aus %d Ressourcen nach %s entpackt" % (n, len(listing), args.ziel))


def cmd_ersetzen(args):
    _check_output(args.ausgabe, args)
    with open(args.neue_daten, "rb") as f:
        data = f.read()
    with erp.open_erp(args.datei) as a:
        r = a.find(args.kennung)
        if r is None:
            sys.exit("Ressource nicht gefunden: %s" % args.kennung)
        r.set_fragment_data(args.fragment, data, compress=not args.unkomprimiert)
        size = a.write(args.ausgabe, overwrite=args.ueberschreiben)
    print("Fragment %d von %s ersetzt (%d Byte), neue Datei: %s (%d Byte)"
          % (args.fragment, args.kennung, len(data), args.ausgabe, size))


def main(argv=None):
    p = argparse.ArgumentParser(description="ERP-Archive von F1 25 lesen, prüfen und schreiben.")
    p.add_argument("--spielordner-erlauben", action="store_true",
                   help="Schreiben im Spielordner zulassen (nicht empfohlen)")
    sub = p.add_subparsers(dest="befehl", required=True)

    s = sub.add_parser("liste", help="Inhalt auflisten")
    s.add_argument("datei")
    s.add_argument("--filter", help="regulärer Ausdruck für Kennung oder Typ")
    s.add_argument("--details", action="store_true", help="jede Ressource einzeln zeigen")
    s.set_defaults(func=cmd_liste)

    s = sub.add_parser("pruefen", help="alle Fragmente entpacken und MD5-Hashes prüfen")
    s.add_argument("datei")
    s.set_defaults(func=cmd_pruefen)

    s = sub.add_parser("rundlauf", help="neu verpacken und mit dem Original vergleichen")
    s.add_argument("datei")
    s.add_argument("--ausgabe", help="neu verpackte Datei zusätzlich speichern")
    s.add_argument("--ueberschreiben", action="store_true")
    s.set_defaults(func=cmd_rundlauf)

    s = sub.add_parser("entpacken", help="Ressourcen als Dateien entpacken")
    s.add_argument("datei")
    s.add_argument("ziel")
    s.add_argument("--filter", help="regulärer Ausdruck für Kennung oder Typ")
    s.set_defaults(func=cmd_entpacken)

    s = sub.add_parser("ersetzen", help="ein Fragment ersetzen und als neue Datei speichern")
    s.add_argument("datei")
    s.add_argument("kennung")
    s.add_argument("neue_daten")
    s.add_argument("ausgabe")
    s.add_argument("--fragment", type=int, default=0)
    s.add_argument("--unkomprimiert", action="store_true")
    s.add_argument("--ueberschreiben", action="store_true")
    s.set_defaults(func=cmd_ersetzen)

    args = p.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
