"""Flagge einer Strecke im Menü ersetzen (docs/flagge.md).

Die Flagge ist kein Datenbankwert: Sie besteht aus drei Texturen im Paket, auf die ``ui/images.json`` verweist
(``flag_circle`` 28×28, ``flag_rectangle`` 100×64, ``flag_rectangle_small`` 40×28, BC7). Der Bau übernimmt sie vom
Träger (Portugal). Hier wird ein beliebiges Bild eingepasst; Größe, Format und Deckkraft der Vorlage bleiben erhalten
(der Kreis behält so seinen runden Ausschnitt).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from typing import Optional

import numpy as np
from PIL import Image, ImageOps

from ego import erp, grafik, grafik_textur
from ego.grafik_textur_schreiben import textur_ersetzen

ARTEN = ("flag_circle", "flag_rectangle", "flag_rectangle_small")


def flagge_einpassen(bild: Image.Image, vorlage: Image.Image) -> np.ndarray:
    """Bild formatfüllend auf die Größe der Vorlage bringen (Mitte bleibt), Deckkraft der Vorlage übernehmen."""
    neu = ImageOps.fit(bild.convert("RGB"), vorlage.size, Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    a = np.dstack([np.array(neu), np.array(vorlage.convert("RGBA"))[:, :, 3]])
    return a.astype(np.uint8)


def flagge_einsetzen(archiv: erp.ErpFile, bild_pfad: str, kennungen: Optional[dict] = None) -> list:
    """Die drei Flaggentexturen des Archivs durch ``bild_pfad`` ersetzen; ``kennungen`` = ``resources`` aus images.json."""
    if kennungen is None:
        kennungen = {}
        for r in archiv:
            name = r.identifier.rsplit("/", 1)[-1]
            if r.resource_type == "GfxSRVResource" and "/track_installer/" in r.identifier and name[:-4] in ARTEN:
                kennungen[name[:-4]] = r.identifier
    fehlend = [a for a in ARTEN if a not in kennungen or archiv.find(kennungen[a]) is None]
    if fehlend:
        raise ValueError("Flag textures are missing in the package: %s" % ", ".join(fehlend))
    with Image.open(bild_pfad) as b:
        bild = b.convert("RGB")
    bericht = []
    for art in ARTEN:   # erst alle Bilder rechnen, dann schreiben
        srv = archiv.find(kennungen[art])
        res = archiv.find(grafik.TexturSicht.aus_bytes(srv.fragments[0].read()).bild)
        vorlage = grafik_textur.textur_bild(grafik.Textur.aus_ressource(res))
        bericht.append((art, flagge_einpassen(bild, vorlage)))
    return [dict(textur_ersetzen(archiv, kennungen[art], a), art=art) for art, a in bericht]


def paket_flagge(quelle: str, ziel: str, bild_pfad: str, protokoll=print) -> dict:
    """Paket (Track Installer) oder Swofty-Addon mit neuer Flagge nach ``ziel`` schreiben; die Quelle bleibt unverändert."""
    with zipfile.ZipFile(quelle) as z:
        namen = z.namelist()
        vorsatz = "" if "manifest.json" in namen else "track/"
        m = json.loads(z.read(vorsatz + "manifest.json"))
        bilder = json.loads(z.read(vorsatz + (m.get("ui") or {}).get("images_file", "ui/images.json")))["resources"]
        erp_name = vorsatz + m["erp"]
        with tempfile.TemporaryDirectory(dir=os.path.dirname(os.path.abspath(ziel))) as tmp:
            roh, neu = os.path.join(tmp, "roh.erp"), os.path.join(tmp, "neu.erp")
            with z.open(erp_name) as e, open(roh, "wb") as d:
                shutil.copyfileobj(e, d, 1 << 24)
            with erp.open_erp(roh) as a:
                bericht = flagge_einsetzen(a, bild_pfad, bilder)
                a.write(neu, overwrite=True)
            protokoll("Flag replaced: %s" % ", ".join("%s %dx%d" % (b["art"], b["breite"], b["hoehe"]) for b in bericht))
            with zipfile.ZipFile(ziel, "w", zipfile.ZIP_STORED, allowZip64=True) as aus:
                for i in z.infolist():
                    if i.is_dir():
                        continue
                    if i.filename == erp_name:
                        aus.write(neu, i.filename)
                        continue
                    with z.open(i) as e, aus.open(i.filename, "w", force_zip64=True) as d:
                        shutil.copyfileobj(e, d, 1 << 24)
    return {"ziel": ziel, "code": m["track_code"], "texturen": bericht, "groesse": os.path.getsize(ziel)}
