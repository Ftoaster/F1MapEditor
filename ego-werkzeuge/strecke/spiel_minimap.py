"""Minimap und Menü-Streckenkarten aus der platzierten Generator-Mittellinie.

Portimão-Stil: dunkelgraue Fahrbahn mit weißen Rändern, transparente Flächen,
separate Sektormasken, weiße Monitor-Silhouette. Alle Weltpunkte benutzen die
verbindliche Verschiebung aus platzierung.py. +Z zeigt in der Textur nach unten.
"""

import copy
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from ego import erp, grafik, grafik_textur
from ego.grafik_textur_schreiben import textur_ersetzen
from . import platzierung
from .pruefregeln import MINIMAP_NACHKOMMA  # Prüfung MINIMAP_ECKEN_UNGERUNDET (AP24)


SEKTOR_FARBEN = ((128, 9, 26, 179), (128, 15, 177, 210), (128, 116, 7, 182))
_BASIS = "eaid://ui_package/art/"
_ORDNER = {"full_track_map": "osd/textures/full_track_maps",
           "full_track_map_sector_overlay": "osd/textures/full_track_maps_sector_overlay",
           "minimap": "osd/textures/minimap",
           "minimap_sector_overlay": "osd/textures/minimap_sector_overlay",
           "monitor": "textures/car_monitor/max_minimap"}


def _vorlage(code):
    out = {"dimensions": 1024.0, "padding": 20.0, "rotation": 0.0,
           "schema_version": 1, "zoom_factor": 1.0}
    for gruppe, suffix in (("textures", ""), ("textures_om_xm", "_xmode")):
        out[gruppe] = {"resources": {name: _BASIS + ordner + suffix + "/" + code + "_ai_trackspace.tif"
                                    for name, ordner in _ORDNER.items()}}
    return out


def _linie(logik, name):
    linie = next(s for s in logik["spielformate"]["TrackSpace"]["splines"] if s["name"] == name)
    p = np.asarray(linie["position"], dtype=np.float64)
    fort = np.asarray(linie["progress"], dtype=np.float64)
    if (p.ndim != 2 or p.shape[1] != 3 or len(p) < 3 or len(fort) != len(p)
            or not np.isfinite(p).all() or not np.isfinite(fort).all()
            or np.any(np.diff(fort) <= 0) or abs(fort[0]) > 1e-8):
        raise ValueError("Invalid TrackSpace line: %s" % name)
    if linie["closed"]:
        if fort[-1] >= 1:
            raise ValueError("Closed TrackSpace must have no duplicate end point")
        p, fort = np.vstack((p, p[0])), np.append(fort, 1)
    elif abs(fort[-1] - 1) > 1e-8:
        raise ValueError("Open TrackSpace must end at progress 1")
    return platzierung.punkte(p)[:, [0, 2]], fort


def _punkt(p, f, progress):
    return np.array([np.interp(progress % 1, f, p[:, i]) for i in range(2)])


def _bogen(p, f, start, ende):
    """Polylinienabschnitt mit interpolierten Grenzen; Wrap über 1 möglich."""
    if ende <= start:
        ende += 1
    ff = np.concatenate((f, f[1:] + 1))
    pp = np.vstack((p, p[1:]))
    innen = (ff > start) & (ff < ende)
    a = np.array([np.interp(start, ff, pp[:, i]) for i in range(2)])
    b = np.array([np.interp(ende, ff, pp[:, i]) for i in range(2)])
    return np.vstack((a, pp[innen], b))


def minimap_metadaten(logik, *, code="portimao", vorlage=None, randfaktor=1.104):
    """Quadrat mit derselben relativen Randzugabe wie Portimão bestimmen.

    Die historisch benannten top-Ecken enthalten z_max, die Bildzeile 0 dagegen
    z_min. padding ist Installer-Metadatum und wird nicht nochmals eingerechnet.
    """
    if not np.isfinite(randfaktor) or randfaktor < 1.05:
        raise ValueError("Margin factor must be at least 1.05")
    p, _ = _linie(logik, "maintrack")
    lo, hi = p.min(0), p.max(0)
    mitte = (lo + hi) / 2
    seite = float(np.max(hi - lo) * randfaktor)
    if seite <= 0:
        raise ValueError("Centreline has no spatial extent")
    lo, hi = mitte - seite / 2, mitte + seite / 2
    # Auf Millimeter runden wie in den Originalpaketen: Der Track Installer bricht mit
    # "Track UI preflight failed … Ordered PKG roundtrip failed" ab, wenn die Eckpunkte volle
    # double-Genauigkeit haben (Spieltest 15.10.2026).
    lo, hi = np.round(lo, MINIMAP_NACHKOMMA), np.round(hi, MINIMAP_NACHKOMMA)
    ecken = {"bottom_left": lo.tolist(), "bottom_right": [float(hi[0]), float(lo[1])],
             "top_left": [float(lo[0]), float(hi[1])], "top_right": hi.tolist()}
    out = copy.deepcopy(vorlage) if vorlage is not None else _vorlage(code)
    if out.get("rotation", 0) != 0 or out.get("zoom_factor", 1) != 1:
        raise ValueError("Only an unrotated minimap with zoom factor 1 is supported")
    for gruppe in ("textures", "textures_om_xm"):
        out[gruppe]["corners"] = copy.deepcopy(ecken)
    return out


def _sektoren(logik):
    zonen = logik["spielformate"]["TM"]["markup"]
    out = []
    for i in range(1, 4):
        z = [z for z in zonen if z["Type"] == "sector_%d" % i and z["SplineName"] == "maintrack"]
        if len(z) != 1:
            raise ValueError("Exactly one main sector zone %d expected" % i)
        out.append((float(z[0]["Start"]), float(z[0]["End"])))
    if (abs(out[0][0]) > 1e-8 or abs(out[-1][1] - 1) > 1e-8
            or any(not 0 <= a < b <= 1 for a, b in out)
            or any(abs(out[i][1] - out[i + 1][0]) > 1e-8 for i in range(2))):
        raise ValueError("Sectors must cover the lap from 0 to 1 without gaps")
    return out


def _malen(logik, meta, breite, hoehe, art, xmode=False):
    """Vierfach abgetastete UI-Linien; Menübilder haben eigene, rechteckige Einpassung."""
    faktor = 3
    p, f = _linie(logik, "maintrack")
    sektoren = _sektoren(logik)
    menu = art in ("spline", "spline_large")
    if menu:
        lo, hi = p.min(0), p.max(0)
        mass = min((breite - 40) / max(hi[0] - lo[0], 1), (hoehe - 42) / max(hi[1] - lo[1], 1))
        mitte = (lo + hi) / 2

        def proj(q):
            return ((np.asarray(q) - mitte) * mass + [breite / 2, hoehe / 2 - 3]) * faktor
    else:
        lo = np.asarray(meta["textures"]["corners"]["bottom_left"])
        hi = np.asarray(meta["textures"]["corners"]["top_right"])

        def proj(q):
            return (np.asarray(q) - lo) / (hi - lo) * [breite, hoehe] * faktor

    # Monitor-RGB ist auch im transparenten Hintergrund weiß, wie in der Vorlage.
    im = Image.new("RGBA", (breite * faktor, hoehe * faktor), (255, 255, 255, 0) if art == "monitor" else (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    skal = breite / 1024

    def linie(q, farbe, dicke):
        pix = proj(q)
        staerke = max(1, round(dicke * faktor))
        d.line([tuple(x) for x in pix], fill=farbe, width=staerke, joint="curve")
        # Pillow lässt bei fast geraden, dicken Teilsegmenten gelegentlich Ritzen.
        # Runde innere Verbindungen schließen sie; Enden bleiben flach (Sektorlücken).
        r = (staerke - 1) / 2
        if staerke > 3:
            for x, y in pix[1:-1]:
                d.ellipse((x - r, y - r, x + r, y + r), fill=farbe)

    def quer(progress, farbe, dicke=2, halbe=12, versatz=0):
        z = _punkt(p, f, progress)
        t = _punkt(p, f, progress + .0003) - _punkt(p, f, progress - .0003)
        t /= max(np.linalg.norm(t), 1e-12)
        n = np.array([-t[1], t[0]])
        a = proj(z) + n * (versatz - halbe) * faktor
        b = proj(z) + n * (versatz + halbe) * faktor
        d.line([tuple(a), tuple(b)], fill=farbe, width=max(1, round(dicke * faktor)))

    def text(position, s, groesse):
        try:
            font = ImageFont.truetype("arial.ttf", round(groesse * faktor))
        except OSError:
            font = ImageFont.load_default()
        d.text(tuple(position), s, fill=(247, 247, 247, 255), font=font, anchor="mm")

    def sektor_text(i, a, b, groesse, abstand):
        z = proj(_punkt(p, f, (a + b) / 2))
        innen = proj(p[:-1].mean(0)) - z
        innen /= max(np.linalg.norm(innen), 1e-12)
        z += innen * abstand * faktor
        rand = (groesse / 2 + 2) * faktor
        z = np.clip(z, [rand, rand], [breite * faktor - rand, hoehe * faktor - rand])
        text(z, str(i + 1), groesse)

    if "overlay" in art:
        # Originalwerte sind Shader-Maskenbytes, keine frei gewählte Farbpalette.
        laenge_px = np.linalg.norm(np.diff(proj(p), axis=0), axis=1).sum() / faktor
        luecke = 2 / laenge_px
        for i, (a, b) in enumerate(sektoren):
            linie(_bogen(p, f, a + luecke, b - luecke), SEKTOR_FARBEN[i], 15 * skal)
    elif art == "monitor":
        linie(p, (255, 255, 255, 255), 26 * skal)
    elif menu:
        schatten = Image.new("RGBA", im.size)
        sd = ImageDraw.Draw(schatten)
        sd.line([tuple(x + [0, 4 * faktor]) for x in proj(p)], fill=(95, 95, 95, 125), width=8 * faktor, joint="curve")
        im.alpha_composite(schatten.filter(ImageFilter.GaussianBlur(faktor / 2)))
        linie(p, (255, 255, 255, 255), 7)
        linie(p, (67, 67, 67, 255), 2.5)
        # Pfeil folgt der echten Fahrtrichtung am Start, keine feste Portimão-Drehung.
        start = proj(p[0])
        tangent = proj(p[1]) - start
        tangent /= max(np.linalg.norm(tangent), 1e-12)
        n = np.array([-tangent[1], tangent[0]])
        z = start + n * 13 * faktor
        d.polygon([tuple(z + tangent * 5 * faktor), tuple(z - tangent * 4 * faktor + n * 3 * faktor),
                   tuple(z - tangent * 4 * faktor - n * 3 * faktor)], fill="white")
        if art == "spline_large":
            for i, (a, b) in enumerate(sektoren):
                sektor_text(i, a, b, 13, 17)
    else:
        # Boxenspur dünner, Hauptlinie außen 26 / innen 18 px bei 1024.
        for s in logik["spielformate"]["TrackSpace"]["splines"]:
            if s["name"] != "maintrack":
                pit, _ = _linie(logik, s["name"])
                linie(pit, (247, 247, 247, 255), 6 * skal)
                linie(pit, (67, 67, 67, 255), 3 * skal)
        linie(p, (245, 245, 245, 255), 26 * skal)
        linie(p, (67, 67, 67, 255), 18 * skal)
        for a, _ in sektoren[1:]:
            quer(a, (247, 247, 247, 255), 2 * skal, 12 * skal)
        for zone in logik.get("drs", []):
            farbe = (201, 29, 153, 255) if xmode else (55, 205, 57, 255)
            if xmode:
                # Sichtbare X-Mode-Klammer neben der tatsächlich definierten DRS-Zone.
                q = _bogen(p, f, zone["aktivierung"], zone["ende"])
                pix = proj(q)
                t = np.gradient(pix, axis=0)
                n = np.stack((-t[:, 1], t[:, 0]), axis=1)
                n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
                pix += n * 24 * skal * faktor
                d.line([tuple(v) for v in pix], fill=farbe, width=max(1, round(3 * skal * faktor)), joint="curve")
            else:
                # Kurze Aktivierungs-/Endmarken wie im Original, kein grünes Vollband.
                for grenze in (zone["aktivierung"], zone["ende"]):
                    linie(_bogen(p, f, grenze, grenze + .008), (79, 133, 80, 255), 14 * skal)
            for fort, farbe2 in ((zone["erkennung"], (249, 210, 0, 255)),
                                 (zone["aktivierung"], farbe)):
                z = proj(_punkt(p, f, fort))
                r = 12 * skal * faktor
                for anteil in (1, .6):
                    rr = r * anteil
                    d.ellipse((z[0] - rr, z[1] - rr, z[0] + rr, z[1] + rr), outline=farbe2,
                              width=max(1, round(2 * skal * faktor)))
        # Zielflagge bleibt auch innerhalb einer über Start/Ziel laufenden DRS-Zone sichtbar.
        for j in range(8):
            quer(j * .00025, (255, 255, 255, 255) if j % 2 == 0 else (15, 15, 15, 255),
                 max(.8, 2 * skal), 12 * skal)
        if art == "full_track_map":
            for i, (a, b) in enumerate(sektoren):
                sektor_text(i, a, b, 30 * skal, 27 * skal)
    if "overlay" in art:
        # Datenmasken kanalweise filtern: Pillow-RGBA würde vormultiplizieren und
        # selbst im konstanten Kern z. B. (128,9,26,179) auf (128,8,25,179) runden.
        im = Image.merge("RGBA", [kanal.resize((breite, hoehe), Image.Resampling.LANCZOS)
                                  for kanal in im.split()])
    else:
        im = im.resize((breite, hoehe), Image.Resampling.LANCZOS)
    return np.array(im)


def minimap_einsetzen(archiv: erp.ErpFile, logik_json: str, **optionen) -> dict:
    """Ersetzt Minimap-/Streckenkarten im Träger und liefert minimap_json + bericht.

    Optionen: code='portimao', ausgabe=<PNG/JSON-Ordner>, minimap_vorlage=<dict>,
    randfaktor=1.104, mips=None (Vorlage erhalten; 'voll' für interne Mip-Kette).
    logik_json enthält Generator-Koordinaten; die Platzierung erfolgt genau hier.
    """
    erlaubt = {"code", "ausgabe", "minimap_vorlage", "randfaktor", "mips"}
    unbekannt = set(optionen) - erlaubt
    if unbekannt:
        raise TypeError("Unknown minimap options: %s" % ", ".join(sorted(unbekannt)))
    code = optionen.get("code", "portimao")
    logik = json.loads(Path(logik_json).read_text(encoding="utf-8"))
    koord = logik.get("koordinatensystem")
    if isinstance(koord, dict):
        koord = koord.get("achsen")
    if koord != "y_oben_rechtshaendig":
        raise ValueError("Generator logic with coordinate system y_oben_rechtshaendig expected")
    _sektoren(logik)
    meta = minimap_metadaten(logik, code=code, vorlage=optionen.get("minimap_vorlage"),
                            randfaktor=optionen.get("randfaktor", 1.104))
    ausgabe = Path(optionen["ausgabe"]) if optionen.get("ausgabe") else None
    if ausgabe:
        ausgabe.mkdir(parents=True, exist_ok=True)
    auftraege = []
    for gruppe, xmode in (("textures", False), ("textures_om_xm", True)):
        for art, kennung in meta[gruppe]["resources"].items():
            auftraege.append((art, kennung, xmode))
    for art in ("spline", "spline_large"):
        auftraege.append((art, _BASIS + "textures/track_installer/" + code + "/" + art + ".tif", False))
    # Fehlende/falsche Zielressourcen vor der ersten Mutation erkennen.
    for _, kennung, _ in auftraege:
        srv = archiv.find(kennung)
        if srv is None or srv.resource_type != "GfxSRVResource":
            raise ValueError("Minimap view is missing: %s" % kennung)
        t = grafik.Textur.aus_ressource(archiv.find(grafik.TexturSicht.aus_bytes(srv.fragments[0].read()).bild))
        if t.dimension != 2 or t.schichten != 1 or t.tiefe != 1 or t.format not in (15, 52, 54, 55, 57, 62, 69, 70):
            raise ValueError("Unsuitable minimap texture: %s" % kennung)
    bilder = []
    for art, kennung, xmode in auftraege:
        srv = archiv.find(kennung)
        res = archiv.find(grafik.TexturSicht.aus_bytes(srv.fragments[0].read()).bild)
        t = grafik.Textur.aus_ressource(res)
        w, h = t.breite << len(t.extern_mips), t.hoehe << len(t.extern_mips)
        a = _malen(logik, meta, w, h, art, xmode)
        name = art + ("_xmode" if xmode else "")
        if ausgabe:
            if not t.extern_mips:
                grafik_textur.textur_bild(t).save(ausgabe / (name + "_vorher.png"))
            Image.fromarray(a).save(ausgabe / (name + "_soll.png"))
        eintrag = textur_ersetzen(archiv, kennung, a, mips=optionen.get("mips"))
        if ausgabe:
            grafik_textur.textur_bild(grafik.Textur.aus_ressource(res)).save(ausgabe / (name + "_nachher.png"))
        eintrag.update(art=art, xmode=xmode)
        bilder.append(eintrag)
    bericht = {"bilder": bilder, "anzahl_bilder": len(bilder), "versatz": list(platzierung.VERSATZ),
               "bildachsen": "+X rechts, +Z unten", "sektoren": _sektoren(logik),
               "sektor_rgba": SEKTOR_FARBEN, "externe_dateien": [],
               "hinweise": ["Installer patcht minimap.uidb aus ui/minimap.json. Erneutes Patchen bei Installation desselben Codes ist offen.",
                            "Menükarten separat rechteckig eingepasst; Bilder und Shader-Masken sind im Spiel noch zu prüfen."]}
    if ausgabe:
        (ausgabe / "minimap.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (ausgabe / "bericht.json").write_text(json.dumps(bericht, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"minimap_json": meta, "bericht": bericht}
