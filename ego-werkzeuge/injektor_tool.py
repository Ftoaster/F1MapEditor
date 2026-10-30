#!/usr/bin/env python3
"""Schnellaustausch: gebautes Paket direkt in F1 25 schreiben (ohne Deinstallieren/Installieren).

Voraussetzung: Der Streckencode des Pakets (z. B. ``sochi``) wurde **einmal** mit dem Track Installer
installiert und einmal aktiviert („Activate mod“). Danach ersetzt dieses Werkzeug für jede neue Version
derselben Strecke Slot-ERP, Mipmaps, ERS-Werte und Minimap-Ecken und schreibt die Statusdatei des
Installers fort (dessen Deinstallation funktioniert weiter). Eine aktivierte Spieldatenbank merkt es sich
und stellt sie wieder her, wenn der Mod im EGO-Editor deaktiviert wurde – der Editor muss dafür nicht
laufen. Details: ``ego/injektion.py``, ``ego/datenbank_abbild.py``, ``docs/injektion.md``.

Befehle:
    plan          PAKET.zip              zeigen, was sich ändern würde (schreibt nichts)
    injizieren    PAKET.zip --ausfuehren Paket ins Spiel schreiben (vorher Sicherung)
    injizieren    PAKET.zip --fragen     Plan zeigen, dann nachfragen (für Paket_ins_Spiel.cmd)
    zuruecksetzen [--sicherung ORDNER]   letzte (oder angegebene) Injektion rückgängig machen
    sicherungen                          vorhandene Sicherungen auflisten
    datenbank     [--code CODE] [--json] Zustand der Spieldatenbank (merkt eine aktivierte Datenbank)

Optionen: --spielordner (Standard Steam-Pfad), --sicherungsordner (Standard testausgabe/injektion_sicherung),
--behalten N (so viele Sicherungen bleiben, Standard 3), --genau (Mipmaps zusätzlich per CRC32 vergleichen),
--datenbank-ablage (gemerkte Aktivierungen, Standard %LOCALAPPDATA%\ego-werkzeuge\datenbank_aktivierung).
Das Spiel muss beendet sein.
"""

import argparse
import os
import sys

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)
from ego import datenbank_abbild, injektion, spielpfade  # noqa: E402

SPIEL = spielpfade.finden() or spielpfade.STANDARD
SICHERUNG = os.path.join(HIER, "testausgabe", "injektion_sicherung")


def cmd_plan(args):
    plan = injektion.planen(args.paket, args.spielordner, genau=args.genau, ablage=args.datenbank_ablage)
    print(plan.text())
    print("\n%s" % ("Nichts zu tun – das Paket ist schon im Spiel." if plan.leer else
                    "Zum Ausführen: injizieren %s --ausfuehren" % args.paket))
    return 0


def cmd_injizieren(args):
    plan = injektion.planen(args.paket, args.spielordner, genau=args.genau, ablage=args.datenbank_ablage)
    print(plan.text())
    print()
    if plan.leer:
        print("Nichts zu tun – das Paket ist schon im Spiel.")
        return 0
    if args.fragen:
        try:
            antwort = input("Jetzt ins Spiel schreiben? (j/n) ").strip().lower()
        except EOFError:
            antwort = ""
        if antwort not in ("j", "ja", "y", "yes"):
            print("Abgebrochen, nichts geändert.")
            return 1
    elif not args.ausfuehren:
        print("Probelauf. Zum Ausführen --ausfuehren angeben.")
        return 0
    injektion.ausfuehren(plan, args.sicherungsordner)
    n = injektion.alte_sicherungen_loeschen(args.sicherungsordner, args.behalten)
    if n:
        print("%d ältere Sicherung(en) gelöscht (behalten: %d)" % (n, args.behalten))
    return 0


def cmd_zuruecksetzen(args):
    ziel = args.sicherung
    if not ziel:
        alle = injektion.sicherungen(args.sicherungsordner)
        if not alle:
            sys.exit("Keine Sicherung vorhanden in %s" % args.sicherungsordner)
        ziel = alle[-1]
    print("Setze zurück auf den Stand vor %s" % ziel)
    injektion.zuruecksetzen(ziel)
    if not args.sicherung:
        import shutil
        shutil.rmtree(ziel, ignore_errors=True)        # verbraucht; die nächstältere bleibt
    return 0


def cmd_sicherungen(args):
    import json
    alle = injektion.sicherungen(args.sicherungsordner)
    for s in alle:
        with open(os.path.join(s, "protokoll.json"), encoding="utf-8") as f:
            p = json.load(f)
        print("%s  %-6s %s%s" % (os.path.basename(s), p["code"], os.path.basename(p["paket"]),
                                 "" if p.get("fertig") else "  (UNVOLLSTÄNDIG)"))
    if not alle:
        print("Keine Sicherungen in %s" % args.sicherungsordner)
    return 0


def cmd_datenbank(args):
    import json
    z = datenbank_abbild.zustand(args.spielordner, args.datenbank_ablage, args.code)
    try:
        installiert = {c: {"slot": i.get("slot_name"), "name": i.get("track_name"),
                           "paket": i.get("archive_file_name")}
                       for c, i in (injektion.status_lesen(args.spielordner).get("installations") or {}).items()}
    except injektion.InjektionsFehler:
        installiert = {}
    if args.json:
        gemerkt = sorted({c for a in datenbank_abbild.abbilder(args.datenbank_ablage) for c in a.codes})
        print(json.dumps({"zustand": z.art, "text": z.text(args.code), "aktiv": z.codes_aktiv,
                          "gemerkt": gemerkt, "installiert": installiert,
                          "spiel_laeuft": injektion.spiel_laeuft()}, ensure_ascii=False))
        return 0
    print("Spieldatenbank: %s" % z.text(args.code))
    for a in datenbank_abbild.abbilder(args.datenbank_ablage):
        print("  gemerkt %s: %s%s" % (a.zeit, ", ".join(a.codes), "" if a.erkannt else " (alle installierten)"))
    for c, i in sorted(installiert.items()):
        print("  installiert %-10s %-24s %s" % (c, i["slot"], i["paket"]))
    return 0


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spielordner", default=SPIEL)
    ap.add_argument("--sicherungsordner", default=SICHERUNG)
    ap.add_argument("--datenbank-ablage", default=datenbank_abbild.standard_ablage())
    sub = ap.add_subparsers(dest="befehl", required=True)
    for name, hilfe in (("plan", "Änderungen anzeigen"), ("injizieren", "Paket ins Spiel schreiben")):
        s = sub.add_parser(name, help=hilfe)
        s.add_argument("paket")
        s.add_argument("--genau", action="store_true", help="Mipmaps zusätzlich per CRC32 vergleichen")
        if name == "injizieren":
            s.add_argument("--ausfuehren", action="store_true")
            s.add_argument("--fragen", action="store_true", help="Plan zeigen und nachfragen")
            s.add_argument("--behalten", type=int, default=3)
    s = sub.add_parser("zuruecksetzen", help="letzte Injektion rückgängig machen")
    s.add_argument("--sicherung", default=None)
    sub.add_parser("sicherungen", help="Sicherungen auflisten")
    s = sub.add_parser("datenbank", help="Zustand der Spieldatenbank; merkt eine aktivierte Datenbank")
    s.add_argument("--code", default=None)
    s.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        return {"plan": cmd_plan, "injizieren": cmd_injizieren, "zuruecksetzen": cmd_zuruecksetzen,
                "sicherungen": cmd_sicherungen, "datenbank": cmd_datenbank}[args.befehl](args)
    except injektion.InjektionsFehler as e:
        print("FEHLER: %s" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
