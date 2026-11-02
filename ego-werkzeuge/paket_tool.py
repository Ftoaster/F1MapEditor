#!/usr/bin/env python3
"""Kommandozeile für Mod-Pakete des Track Installers (EGO GameDatabases Editor).

Befehle:
    info     PAKET.zip                       Manifest und Inhalt anzeigen
    pruefen  PAKET.zip [--tief]              Aufbau, Verweise und ERP prüfen (--tief: alle MD5)
    umbauen  QUELLE.zip ZIEL.zip --variante V   Testpaket für den Spieltest erzeugen
    bauen    ORDNER ZIEL.zip                 Paket aus Ordner (manifest.json, ui/, <erp>, mipmaps/)
    umbenennen QUELLE.zip ZIEL.zip --code CODE [--name NAME] [--aktivierung-vorlage CODE]
                                             Paket mit anderem Streckencode bauen (AP16,
                                             docs/streckencode.md); Bericht ergebnisse/paket_<ziel>.md
    installiert PAKET.zip [...] [--slot N]   prüfen, welches Paket tatsächlich im Spiel-Slot liegt
                                             (Größe und CRC32; liest den Spielordner nur). Ohne --slot
                                             wird der Slot aus der Statusdatei des Installers zum
                                             track_code des ersten Pakets gesucht (sonst Slot 4).

Varianten für "umbauen" (siehe docs/schritt2_spieltest.md):
    unveraendert   ERP mit dem Werkzeug neu geschrieben (bitgleich zum Original)
    spielstil      alle Fragmente neu komprimiert (zstd 0x11), Längenfelder exakt; Inhalt und MD5 gleich
    md5_test       nur der MD5 einer Ressource absichtlich falsch (--kennung, Standard: <code>_f1.wep)
    md5_repariert  falsche MD5-Werte neu berechnet, sonst unverändert
    erp            fertige Test-ERP einsetzen (--erp PFAD, optional --beschreibung TEXT)

Beispiel:
    python paket_tool.py umbauen "C:\\Users\\...\\portimao.zip" testausgabe\\pakete\\portimao_2_2_spielstil.zip --variante spielstil

Sicherheit: Ausgaben im Spielordner werden verweigert.
"""

import argparse
import datetime
import hashlib
import json
import os
import shutil
import sys
import time
import zipfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ego import compression, erp, paket, spielpfade  # noqa: E402
from erp_tool import _is_game_folder  # noqa: E402

HIER = os.path.dirname(os.path.abspath(__file__))
VARIANTEN = ("unveraendert", "spielstil", "md5_test", "md5_repariert", "erp")


def _ziel_pruefen(pfad):
    if _is_game_folder(pfad):
        sys.exit("Abbruch: Ausgabe im Spielordner ist gesperrt (%s)." % pfad)


def _sha256(pfad):
    h = hashlib.sha256()
    with open(pfad, "rb") as f:
        for block in iter(lambda: f.read(1 << 24), b""):
            h.update(block)
    return h.hexdigest()


def cmd_info(args):
    with paket.ModPaket(args.paket) as p:
        m = p.manifest
        print("Paket:      %s (%.2f GB)" % (args.paket, os.path.getsize(args.paket) / 1e9))
        print("Strecke:    %s (%s), Version %s, Autor %s" % (m.get("track_name"), m.get("track_code"),
                                                          m.get("version"), m.get("author_name")))
        info = p.zip.getinfo(p.erp_name)
        print("ERP:        %s (%.1f MB)" % (p.erp_name, info.file_size / 1e6))
        mip = p.mipmap_eintraege()
        print("mipmaps:    %d Dateien, %.1f MB" % (len(mip), sum(i.file_size for i in mip) / 1e6))
        for c in m.get("copy_directories", []):
            print("Kopierregel: %s -> %s" % (c.get("source"), c.get("destination")))
        if "ers" in m:
            print("ERS:        %s" % ", ".join("%s=%s" % kv for kv in m["ers"].items()))
        akt = m.get("activation")
        if akt:
            print("Aktivierung: Vorlage '%s', %d Overrides, %d Strecken-Overrides" % (
                akt.get("copy_from_track"), len(akt.get("overrides", [])), len(akt.get("track_overrides", []))))
        print("UI-Kennungen: %d" % len(p.ui_kennungen()))


def cmd_pruefen(args):
    arbeit = os.path.join(HIER, "testausgabe", "pakete", "_pruefen")
    os.makedirs(arbeit, exist_ok=True)
    with paket.ModPaket(args.paket) as p:
        ergebnis = p.pruefen(arbeit, tief=args.tief)
    for name, ok, text in ergebnis:
        print("  %-6s %-40s %s" % ("OK" if ok else "FEHLER", name, text))
    fehler = sum(1 for _, ok, _ in ergebnis if not ok)
    print("%d Prüfungen, %d Fehler" % (len(ergebnis), fehler))
    return 1 if fehler else 0


def cmd_umbauen(args):
    _ziel_pruefen(args.ziel)
    if os.path.exists(args.ziel) and not args.ueberschreiben:
        sys.exit("Zieldatei existiert bereits: %s (--ueberschreiben)" % args.ziel)
    arbeit = os.path.join(HIER, "testausgabe", "pakete", "_arbeit")
    paket.ordner_leeren(arbeit)
    t0 = time.time()
    bericht = {"quelle": os.path.abspath(args.quelle), "ziel": os.path.abspath(args.ziel), "variante": args.variante,
               "zeitpunkt": datetime.datetime.now().isoformat(timespec="seconds"), "aenderungen": []}
    with paket.ModPaket(args.quelle) as p:
        original = p.erp_entpacken(os.path.join(arbeit, "original.erp"))
        neu = os.path.join(arbeit, os.path.basename(p.erp_name))
        print("Original-ERP entpackt: %.1f MB" % (os.path.getsize(original) / 1e6))
        with erp.open_erp(original) as a:
            bericht["original_md5_falsch"] = [r.identifier for r in a if not r.verify()]
            if bericht["original_md5_falsch"]:
                print("Hinweis: Original hat %d falsche MD5-Werte" % len(bericht["original_md5_falsch"]))
            if args.variante == "spielstil":
                def fortschritt(n, gesamt):
                    print("  neu komprimiert: %d/%d Fragmente (%.0f s)" % (n, gesamt, time.time() - t0), flush=True)
                paket.alles_neu_komprimieren(a, stufe=args.stufe, fortschritt=fortschritt)
                bericht["aenderungen"].append("alle %d Fragmente neu komprimiert (zstd-Stufe %d, Code 0x11), "
                                              "Längenfelder exakt" % (a.fragment_count, args.stufe))
            elif args.variante == "md5_test":
                kennung = args.kennung or "eaid://environment_package/tracks/%s/wep/%s_f1.wep" % (
                    p.track_code, p.track_code)
                alt, neu_hash = paket.md5_verfaelschen(a, kennung)
                bericht["aenderungen"].append("MD5 von `%s`: %s -> %s (Daten unverändert)" % (kennung, alt, neu_hash))
            elif args.variante == "md5_repariert":
                rep = paket.md5_reparieren(a)
                bericht["aenderungen"].append("%d MD5-Werte neu berechnet: %s" % (len(rep), ", ".join(rep) or "-"))
            if args.variante == "erp":
                if not args.erp:
                    sys.exit("Variante 'erp' braucht --erp PFAD")
                shutil.copyfile(args.erp, neu)
                bericht["aenderungen"].append("ERP ersetzt durch `%s`%s" % (
                    os.path.abspath(args.erp), (": " + args.beschreibung) if args.beschreibung else ""))
            else:
                a.write(neu)
        print("Neue ERP geschrieben: %.1f MB (%.0f s)" % (os.path.getsize(neu) / 1e6, time.time() - t0))

        # Gegenprüfung: inhaltlicher Vergleich mit dem Original
        diff = paket.inhalte_vergleichen(original, neu)
        bericht["vergleich"] = {k: len(v) for k, v in diff.items()}
        bericht["vergleich_beispiele"] = {k: v[:5] for k, v in diff.items() if v}
        bericht["erp_bitgleich"] = _sha256(original) == _sha256(neu)
        print("Vergleich mit Original: %s, bitgleich: %s" % (bericht["vergleich"], bericht["erp_bitgleich"]))

        groesse = paket.paket_umbauen(p, args.ziel, neu, ueberschreiben=args.ueberschreiben)
        print("Paket geschrieben: %s (%.2f GB, %.0f s)" % (args.ziel, groesse / 1e9, time.time() - t0))
    with paket.ModPaket(args.ziel) as q:
        bericht["pruefung"] = q.pruefen(os.path.join(arbeit, "_pruefen"), tief=True)
    for name, ok, text in bericht["pruefung"]:
        print("  %-6s %-40s %s" % ("OK" if ok else "FEHLER", name, text))
    os.remove(original)
    os.remove(neu)
    bericht["sekunden"] = round(time.time() - t0)
    _bericht_schreiben(bericht)


def _bericht_schreiben(b):
    name = os.path.splitext(os.path.basename(b["ziel"]))[0]
    pfad = os.path.join(HIER, "ergebnisse", "paket_%s.md" % name)
    zeilen = ["# Testpaket `%s`" % os.path.basename(b["ziel"]), "",
              "| | |", "|---|---|",
              "| Zeitpunkt | %s |" % b["zeitpunkt"],
              "| Quelle | `%s` |" % b["quelle"],
              "| Ziel | `%s` (nicht im Repository) |" % b["ziel"],
              "| Variante | %s |" % b["variante"],
              "| Dauer | %d s |" % b["sekunden"],
              "| zstd-Backend | %s |" % compression.backend_name(),
              "| ERP bitgleich zum Original | %s |" % ("ja" if b["erp_bitgleich"] else "nein"), "",
              "## Änderungen", ""]
    zeilen += ["- %s" % a for a in b["aenderungen"]] or ["- keine"]
    zeilen += ["", "## Inhaltlicher Vergleich mit der Original-ERP", "",
               "| Merkmal | Anzahl Ressourcen |", "|---|---:|"]
    namen = {"nur_a": "nur im Original", "nur_b": "nur im Testpaket", "daten": "entpackte Daten verschieden",
             "kopf": "Typ/Feld/Fragmentnamen/Flags verschieden", "hash": "gespeicherter MD5 verschieden"}
    for k, n in b["vergleich"].items():
        zeilen.append("| %s | %d |" % (namen[k], n))
    for k, v in b.get("vergleich_beispiele", {}).items():
        zeilen.append("")
        zeilen.append("%s: %s" % (namen[k], ", ".join("`%s`" % x for x in v)))
    if b.get("original_md5_falsch"):
        zeilen += ["", "**Hinweis:** Schon das Original enthält %d Ressource(n) mit falschem MD5 (%s). "
                   "Diese werden absichtlich unverändert übernommen (außer bei `md5_repariert`) und erscheinen "
                   "deshalb auch unten in der MD5-Prüfung." % (len(b["original_md5_falsch"]),
                                                             ", ".join("`%s`" % x for x in b["original_md5_falsch"]))]
    zeilen += ["", "## Prüfung des fertigen Pakets", "", "| Prüfung | Ergebnis | Details |", "|---|---|---|"]
    for name, ok, text in b["pruefung"]:
        zeilen.append("| %s | %s | %s |" % (name, "OK" if ok else "**FEHLER**", text))
    with open(pfad, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(zeilen) + "\n")
    print("Bericht: %s" % pfad)


SLOT_ORDNER = os.path.join(spielpfade.finden() or spielpfade.STANDARD, "2025_asset_groups", "ui_package",
                           "texture_arrays", "invitational_events", "event_images")


def _zeit(pfad):
    return time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(os.stat(pfad).st_mtime))


def _slot_aus_status(slotordner, pfad):
    """Slotnummer zum track_code eines Pakets aus f1_db_editor_invitational_slots.json (nur lesend)."""
    status = os.path.join(slotordner, "f1_db_editor_invitational_slots.json")
    try:
        with zipfile.ZipFile(pfad) as z:
            code = json.loads(z.read("manifest.json").decode("utf-8")).get("track_code")
        with open(status, encoding="utf-8") as f:
            eintrag = json.load(f).get("installations", {}).get(code)
    except (OSError, ValueError, KeyError):
        return None, None
    if not eintrag:
        return None, code
    nummer = eintrag.get("slot_name", "").rsplit("_", 1)[-1]
    return (int(nummer) if nummer.isdigit() else None), code


def cmd_installiert(args):
    """Vergleicht die ERP im Invitational-Slot mit der ERP der angegebenen Pakete. Nur lesend."""
    if args.slot is None:
        args.slot, code = _slot_aus_status(args.slotordner, args.pakete[0])
        if args.slot is None:
            print("Code %s ist laut Statusdatei des Installers nicht installiert; prüfe Slot 4." % code)
            args.slot = 4
        else:
            print("Slot %d laut Statusdatei des Installers (Code %s)" % (args.slot, code))
    slot = os.path.join(args.slotordner, "invitational_image_%d.erp" % args.slot)
    if not os.path.exists(slot):
        sys.exit("Slot-Datei nicht gefunden: %s" % slot)
    groesse = os.path.getsize(slot)
    crc = 0
    with open(slot, "rb") as f:
        for block in iter(lambda: f.read(1 << 24), b""):
            crc = zlib.crc32(block, crc)
    print("Slot %d: %.1f MB, zuletzt geändert %s, CRC32 %08x" % (args.slot, groesse / 1e6, _zeit(slot), crc))
    status = os.path.join(args.slotordner, "f1_db_editor_invitational_slots.json")
    if os.path.exists(status):
        print("Statusdatei des Installers zuletzt geändert: %s" % _zeit(status))
    gefunden = []
    for pfad in args.pakete:
        with zipfile.ZipFile(pfad) as z:
            manifest = json.loads(z.read("manifest.json").decode("utf-8"))
            info = z.getinfo(manifest["erp"])
        if info.file_size == groesse and info.CRC == crc:
            gefunden.append(pfad)
            print("  %-48s <<< INSTALLIERT" % os.path.basename(pfad))
        else:
            print("  %-48s nicht installiert" % os.path.basename(pfad))
    if not gefunden:
        print("Keines der angegebenen Pakete liegt in Slot %d." % args.slot)
    return 0 if gefunden else 1


def cmd_bauen(args):
    _ziel_pruefen(args.ziel)
    groesse = paket.paket_aus_ordner(args.ordner, args.ziel, ueberschreiben=args.ueberschreiben)
    print("Paket geschrieben: %s (%.2f GB)" % (args.ziel, groesse / 1e9))


def cmd_swofty_addon(args):
    """Paket aus dem eigenen Bau als Swofty-Addon mit neuem Code; die Renndaten bleiben erhalten."""
    from strecke import streckencode
    _ziel_pruefen(args.ziel)
    ok, hinweis = streckencode.code_bewerten(args.code, streckencode.ZIEL_SWOFTY)
    print("Code %s: %s" % (args.code, hinweis))
    if not ok:
        return 1
    zwischen = args.ziel + ".paket.zip"
    streckencode.paket_umbenennen(args.quelle, zwischen, args.code, name=args.name, aktivierung_vorlage=streckencode.SWOFTY_VORLAGE,
                                  aktivierung_behalten=True, ueberschreiben=True)
    try:
        print(streckencode.swofty_addon_bauen(zwischen, args.ziel, args.name))
    finally:
        os.remove(zwischen)
    return 0


def cmd_swofty_raster(args):
    """Test-Addon für 26 Autos schreiben (nur mit Strecken, die mit --raster 26 gebaut sind)."""
    from strecke import streckencode
    _ziel_pruefen(args.ziel)
    print(streckencode.swofty_raster_addon(args.ziel))
    return 0


def cmd_umbenennen(args):
    """Paket mit neuem Streckencode (AP16). Quelle bleibt unverändert."""
    from strecke import streckencode
    _ziel_pruefen(args.ziel)
    ok, hinweis = streckencode.code_bewerten(args.code)
    print("Code %s: %s" % (args.code, hinweis))
    if not ok:
        return 1
    if args.aktivierung_vorlage and args.aktivierung_vorlage not in streckencode.DB_ALTSTRECKEN and \
            args.aktivierung_vorlage not in streckencode.SPIELSTRECKEN:
        sys.exit("Aktivierungsvorlage %s ist keine bekannte Strecke" % args.aktivierung_vorlage)
    b = streckencode.paket_umbenennen(args.quelle, args.ziel, args.code, name=args.name,
                                      aktivierung_vorlage=args.aktivierung_vorlage,
                                      ueberschreiben=args.ueberschreiben)
    for teil in ("erp", None):
        liste = b["erp"]["pruefung"] if teil else b["paket_pruefung"]
        for name, ok, text in liste:
            print("  %-6s %-44s %s" % ("OK" if ok else "FEHLER", name, text))
    name = os.path.splitext(os.path.basename(args.ziel))[0]
    pfad = os.path.join(HIER, "ergebnisse", "paket_%s.md" % name)
    with open(pfad, "w", encoding="utf-8", newline="\n") as f:
        f.write(streckencode.bericht_markdown(b))
    print("Paket: %s (%.2f GB, %d s), Bericht: %s" % (args.ziel, b["groesse"] / 1e9, b["sekunden"], pfad))
    fehler = [n for n, ok, _ in b["erp"]["pruefung"] + b["paket_pruefung"] if not ok]
    return 1 if fehler else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="befehl", required=True)
    s = sub.add_parser("info", help="Manifest und Inhalt anzeigen")
    s.add_argument("paket")
    s.set_defaults(func=cmd_info)
    s = sub.add_parser("pruefen", help="Paket prüfen")
    s.add_argument("paket")
    s.add_argument("--tief", action="store_true", help="alle MD5-Werte der ERP prüfen")
    s.set_defaults(func=cmd_pruefen)
    s = sub.add_parser("umbauen", help="Testpaket erzeugen")
    s.add_argument("quelle")
    s.add_argument("ziel")
    s.add_argument("--variante", choices=VARIANTEN, required=True)
    s.add_argument("--kennung", help="Ressource für md5_test")
    s.add_argument("--erp", help="fertige ERP für Variante 'erp'")
    s.add_argument("--beschreibung", help="Beschreibung der Änderung für den Bericht")
    s.add_argument("--stufe", type=int, default=compression.DEFAULT_LEVEL, help="zstd-Stufe für spielstil")
    s.add_argument("--ueberschreiben", action="store_true")
    s.set_defaults(func=cmd_umbauen)
    s = sub.add_parser("bauen", help="Paket aus Ordner bauen")
    s.add_argument("ordner")
    s.add_argument("ziel")
    s.add_argument("--ueberschreiben", action="store_true")
    s.set_defaults(func=cmd_bauen)
    s = sub.add_parser("umbenennen", help="Paket mit anderem Streckencode bauen (AP16)")
    s.add_argument("quelle")
    s.add_argument("ziel")
    s.add_argument("--code", required=True, help="neuer Streckencode (in der Spieldatenbank vorhanden, z. B. sochi)")
    s.add_argument("--name", help="track_name im Manifest (Standard: unverändert)")
    s.add_argument("--aktivierung-vorlage", help="activation.copy_from_track: Strecke, deren Datenbankeinträge "
                                                   "der Installer für fehlende Datensätze kopiert (z. B. portimao)")
    s.add_argument("--ueberschreiben", action="store_true")
    s.set_defaults(func=cmd_umbenennen)
    s = sub.add_parser("swofty-addon", help="Paket als Swofty-Addon mit eigenem Code schreiben")
    s.add_argument("quelle")
    s.add_argument("ziel")
    s.add_argument("--code", required=True)
    s.add_argument("--name", required=True)
    s.set_defaults(func=cmd_swofty_addon)
    s = sub.add_parser("swofty-raster", help="Test-Addon für 26 Autos schreiben")
    s.add_argument("ziel")
    s.set_defaults(func=cmd_swofty_raster)
    s = sub.add_parser("installiert", help="prüfen, welches Paket im Spiel-Slot liegt (nur lesend)")
    s.add_argument("pakete", nargs="+")
    s.add_argument("--slot", type=int, default=None,
                   help="Invitational-Slot (Standard: aus der Statusdatei des Installers; Portimão 4, Sepang 9)")
    s.add_argument("--slotordner", default=SLOT_ORDNER)
    s.set_defaults(func=cmd_installiert)
    args = ap.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
