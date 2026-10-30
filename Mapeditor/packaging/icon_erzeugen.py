"""Erzeugt das Programmsymbol src/mapeditor/mapeditor.ico (+ PNG-Vorschau) aus Code.

Aufruf:  Mapeditor/.venv/Scripts/python.exe Mapeditor/packaging/icon_erzeugen.py

Motiv: stilisierte, geschlossene Rennstrecke (Farbe der Editor-Eingabe) auf dunklem
abgerundetem Quadrat, Start-Ziel-Karo und ein Kontrollpunkt als Zeichen für „Editor“.
Jede Größe wird einzeln gezeichnet (vierfach überabgetastet); 16/24/32 px vereinfacht.
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HIER = Path(__file__).resolve().parent
ZIEL = HIER.parent / "src" / "mapeditor" / "mapeditor.ico"
VORSCHAU = HIER.parent / "docs" / "screenshots" / "icon.png"
GROESSEN = (16, 20, 24, 32, 40, 48, 64, 128, 256)

HINTERGRUND_OBEN = np.array((0x24, 0x34, 0x44))
HINTERGRUND_UNTEN = np.array((0x12, 0x1A, 0x24))
RAND = (0x3A, 0x4E, 0x62, 255)
ASPHALT = (0x0B, 0x11, 0x18, 255)
LINIE = (0x67, 0xD9, 0xF5, 255)
PUNKT = (0xF1, 0xF5, 0xF9, 255)

# Kontrollpunkte der Strecke im Einheitsquadrat (Uhrzeigersinn), angelehnt an einen GP-Kurs
KONTROLLE = np.array([
    (0.30, 0.79), (0.55, 0.79), (0.76, 0.78), (0.83, 0.70), (0.78, 0.60),
    (0.64, 0.55), (0.60, 0.46), (0.70, 0.38), (0.80, 0.27), (0.70, 0.18),
    (0.52, 0.21), (0.40, 0.33), (0.27, 0.36), (0.18, 0.46), (0.17, 0.66),
])
START = 0          # Abschnitt mit der Start-Ziel-Linie
HAARNADEL = 8      # Kontrollpunkt mit dem Editor-Griff


def catmull_rom(punkte, je=40):
    n = len(punkte)
    aus = []
    for i in range(n):
        p0, p1, p2, p3 = (punkte[(i + k) % n] for k in (-1, 0, 1, 2))
        for t in np.linspace(0, 1, je, endpoint=False):
            t2, t3 = t * t, t * t * t
            aus.append(0.5 * (2 * p1 + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2
                              + (-p0 + 3 * p1 - 3 * p2 + p3) * t3))
    return np.array(aus)


def linie(draw, pts, farbe, breite):
    """Geschlossener Linienzug mit runden Gelenken."""
    folge = [tuple(p) for p in pts] + [tuple(pts[0])]
    draw.line(folge, fill=farbe, width=int(round(breite)), joint="curve")
    r = breite / 2
    for x, y in folge[:: max(1, len(folge) // 200)]:
        draw.ellipse((x - r, y - r, x + r, y + r), fill=farbe)


def zeichnen(groesse):
    s = 4 * groesse                       # Überabtastung
    klein = groesse <= 32
    bild = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    # Hintergrund: abgerundetes Quadrat mit senkrechtem Verlauf
    rand = s * (0.02 if klein else 0.04)
    radius = s * 0.22
    maske = Image.new("L", (s, s), 0)
    ImageDraw.Draw(maske).rounded_rectangle((rand, rand, s - rand, s - rand), radius, fill=255)
    t = np.linspace(0, 1, s)[:, None, None]
    verlauf = (HINTERGRUND_OBEN * (1 - t) + HINTERGRUND_UNTEN * t).astype(np.uint8)
    verlauf = np.broadcast_to(verlauf, (s, s, 3))
    flaeche = Image.fromarray(np.dstack([verlauf, np.full((s, s), 255, np.uint8)]), "RGBA")
    bild.paste(flaeche, (0, 0), maske)
    draw = ImageDraw.Draw(bild)
    if not klein:
        draw.rounded_rectangle((rand, rand, s - rand, s - rand), radius, outline=RAND, width=max(2, s // 128))

    # Strecke: dunkle Fahrbahn, darauf leuchtende Mittellinie
    pts = catmull_rom(KONTROLLE) * s
    if klein:
        linie(draw, pts, LINIE, s * 0.10)
    else:
        linie(draw, pts, ASPHALT, s * 0.115)
        linie(draw, pts, LINIE, s * 0.045)

    # Start-Ziel: Karomuster quer zur Gerade unten links (zwischen Punkt 0 und 1)
    if groesse >= 40:
        i = START * 40 + 20                      # Mitte des Start-Ziel-Abschnitts
        mitte, weiter = pts[i], pts[i + 1]
        richtung = (weiter - mitte) / np.linalg.norm(weiter - mitte)
        quer = np.array((-richtung[1], richtung[0]))
        breite, tiefe = s * 0.115, s * 0.05
        spalten, zeilen = 4, 2
        for a in range(spalten):
            for b in range(zeilen):
                u0, u1 = -breite / 2 + a * breite / spalten, -breite / 2 + (a + 1) * breite / spalten
                v0, v1 = -tiefe / 2 + b * tiefe / zeilen, -tiefe / 2 + (b + 1) * tiefe / zeilen
                ecken = [mitte + quer * u + richtung * v for u, v in ((u0, v0), (u1, v0), (u1, v1), (u0, v1))]
                farbe = PUNKT if (a + b) % 2 == 0 else ASPHALT
                draw.polygon([tuple(e) for e in ecken], fill=farbe)

    # Kontrollpunkt (Editor): weißer Kreis mit farbigem Ring auf der Haarnadel oben rechts
    if groesse >= 24:
        x, y = KONTROLLE[HAARNADEL] * s
        r = s * (0.085 if klein else 0.07)
        ring = s * (0.03 if klein else 0.022)
        draw.ellipse((x - r - ring, y - r - ring, x + r + ring, y + r + ring), fill=ASPHALT)
        draw.ellipse((x - r, y - r, x + r, y + r), fill=PUNKT)
        draw.ellipse((x - r * 0.45, y - r * 0.45, x + r * 0.45, y + r * 0.45), fill=LINIE)

    return bild.resize((groesse, groesse), Image.LANCZOS)


def main():
    bilder = {g: zeichnen(g) for g in GROESSEN}
    ZIEL.parent.mkdir(parents=True, exist_ok=True)
    gross = bilder[256]
    gross.save(ZIEL, format="ICO", sizes=[(g, g) for g in GROESSEN],
               append_images=[bilder[g] for g in GROESSEN if g != 256])
    VORSCHAU.parent.mkdir(parents=True, exist_ok=True)
    vorschau = Image.new("RGBA", (256 + 16 + 128 + 16 + 64 + 16 + 48 + 16 + 32 + 16 + 16, 256), (0, 0, 0, 0))
    x = 0
    for g in (256, 128, 64, 48, 32, 16):
        vorschau.paste(bilder[g], (x, 256 - g))
        x += g + 16
    vorschau.save(VORSCHAU)
    print(f"{ZIEL}  ({ZIEL.stat().st_size} B, {len(GROESSEN)} Größen)\n{VORSCHAU}")


if __name__ == "__main__":
    main()
