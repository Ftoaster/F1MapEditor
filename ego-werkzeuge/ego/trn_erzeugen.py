"""TRN~-Fahrphysik aus Dreiecken: vollständiger JPAK/VCQTC-Erzeuger.

Der Erzeuger schreibt die im Sepang-Original belegte Version 0x20002:
144-Byte-Kopf, 16-Byte-Vertices, lokale Acht-Code-Palette, big-endian
Quadtree-Knoten, komprimierte 4-Byte-Dreiecke und Differenzlisten.
Es werden keine Originalblöcke oder unbekannten Version-4-Blattwörter
übernommen. Version 4 bleibt bis zur vollständigen Klärung dieser Wörter
ausdrücklich gesperrt. Formatgültigkeit ist durch unabhängige Referenz-
und Geometrieprüfungen belegt; ein Fahrtest steht aus.

Äußere Kacheln erhalten Namen qt[_00|_01|_10|_11...].vcqtc. Normalerweise
entscheidet das XZ-Minimum der Dreiecks-AABB an festen Halbierungsebenen.
Bei deckungsgleichen Dreiecken oder erschöpfter float32-Auflösung erfolgt
eine deterministische Mengenaufteilung. Das ist eine eigene Erzeugungs-
regel, keine behauptete Reproduktion des Originalexporters. Optional
vorgegebene Kachelgruppen isolieren diese offene Regel beim Spieltest.

AP4c (15.10.2026): Jede Kachel erhält eine geteilte Vertexliste ohne
Duplikate (wie alle Originale); Dreiecke werden dafür bei Bedarf
umnummeriert (Cuthill-McKee), zyklisch gedreht und nach Basisindex
sortiert. ``kantenflags`` berechnet die drei Dreiecksflagbits aus der
Kantennachbarschaft (Bit e = Kante (v_e, v_e+1) hat eine Nachbarfläche).
"""

import struct
from collections import OrderedDict

import numpy as np

from .trn import JpakEntry, JpakFile, _ausrichten, _code


class _KachelVoll(ValueError):
    """Interne Aufforderung, eine Kachel weiter aufzuteilen."""


def _ganzzahlen(werte, form, name, maximum=None):
    array = np.asarray(werte)
    if array.shape != form or not np.issubdtype(array.dtype, np.integer):
        raise ValueError(name + ': Form oder ganzzahliger Datentyp ungültig')
    if np.any(array < 0) or (maximum is not None and np.any(array > maximum)):
        raise ValueError(name + ': Wert außerhalb des erlaubten Bereichs')
    return array


def _differenzliste(indices):
    """Originalbelegte Version-2-Kodierung einschließlich 254-Fortsetzungen."""
    indices = np.sort(np.asarray(indices, dtype=np.int64))
    raw = bytearray(struct.pack('>H', int(indices[0])))
    for delta in np.diff(indices):
        delta = int(delta)
        while delta >= 254:
            raw.append(254)
            delta -= 254
        raw.append(delta)
    raw.append(255)
    return bytes(raw)


def _quadtree(positionen, dreiecke, bounds, blattgroesse, max_tiefe):
    """Verwendet AABB-Schnitt mit 10-cm-Zellrand, keine Dreiecksabschneidung.

Blattlisten werden dedupliziert. Gleiche Referenzmengen dürfen mehrere
Zellen bedienen. Unterteilungen ohne räumlichen Gewinn werden beendet.
    Referenzvervielfachung und Zellen kleiner als die Toleranz beenden die
    Unterteilung ebenfalls. Bei Überschreitung des 15-Bit-Adressraums ist ein Wurzelblatt ein gültiger,
langsamerer Ersatz; es referenziert weiterhin sämtliche Dreiecke.
"""
    p = positionen[dreiecke][:, :, [0, 2]].astype(np.float64)
    tri_lo, tri_hi = p.min(1), p.max(1)
    nodes, workspace, offsets = [0], bytearray(), {}

    def blatt(node, indices):
        if not len(indices):
            nodes[node] = 0xffff
            return
        key = tuple(map(int, indices))
        if key not in offsets:
            off = len(workspace)
            if off >= 0x7fff:
                raise _KachelVoll('Blattoffset überschreitet 15 Bit')
            offsets[key] = off
            workspace.extend(_differenzliste(indices))
        nodes[node] = 0x8000 | offsets[key]

    def bauen(node, indices, lo, hi, tiefe):
        if (len(indices) <= blattgroesse or tiefe >= max_tiefe or
                np.max(hi - lo) <= .2):
            blatt(node, indices)
            return
        mid = (lo + hi) * .5
        kinder = []
        for k in range(4):
            up = np.array([k & 1, 1 - (k >> 1)], dtype=bool)
            a, b = np.where(up, mid, lo), np.where(up, hi, mid)
            mask = np.all(tri_hi[indices] >= a - .1, axis=1) & np.all(tri_lo[indices] <= b + .1, axis=1)
            kinder.append((indices[mask], a, b))
        # Bei Zellen nahe der 10-cm-Toleranz würde fortgesetztes Teilen
        # dieselben Referenzen immer weiter vervielfachen. Eine vollständige
        # Elternliste ist dann günstiger und geometrisch gleichwertig.
        if (all(len(ix) in (0, len(indices)) for ix, _, _ in kinder) or
                sum(len(ix) for ix, _, _ in kinder) >= 3 * len(indices)):
            blatt(node, indices)
            return
        first = len(nodes)
        if first + 3 >= 0x8000:
            raise _KachelVoll('Knotenindex überschreitet 15 Bit')
        nodes[node] = first
        nodes.extend([0] * 4)
        for k, (ix, a, b) in enumerate(kinder):
            bauen(first + k, ix, a, b, tiefe + 1)

    indices = np.arange(len(dreiecke), dtype=np.int64)
    try:
        bauen(0, indices, bounds[0, [0, 2]].astype(float), bounds[1, [0, 2]].astype(float), 0)
    except _KachelVoll:
        nodes, workspace, offsets = [0], bytearray(), {}
        blatt(0, indices)
    return np.asarray(nodes, dtype='>u2').tobytes(), bytes(workspace)


def _flags_rotieren(flags, r):
    """Kantenbits zur zyklisch gedrehten Eckenfolge (neue Ecke 0 = alte Ecke r).

    Bit e gehört zur Kante (v_e, v_e+1) (AP4c, aus Portimão/Bahrain belegt).
    Neues Bit e = altes Bit (e + r) mod 3.
    """
    flags = np.asarray(flags, dtype=np.uint8)
    r = np.asarray(r, dtype=np.uint8) % 3
    neu = np.zeros_like(flags)
    for e in range(3):
        neu |= ((flags >> ((e + r) % 3)) & 1) << e
    return neu


def _lokale_vertices(vertices, attribute, quellen):
    """Fasst Vertices mit gleicher float32-Position und gleichem Attribut zusammen.

    quellen: aufsteigende globale Indices. Rückgabe: Vertreter (globale Indices,
    Reihenfolge des ersten Vorkommens in ``quellen``) und die lokale Nummer
    jeder Quelle. -0.0 und 0.0 gelten als gleich.
    """
    rec = np.empty(len(quellen), dtype=[('p', '<f4', (3,)), ('a', '<u4')])
    rec['p'] = vertices[quellen] + np.float32(0)
    rec['a'] = attribute[quellen]
    _, erst, inv = np.unique(rec.view('V16').ravel(), return_index=True, return_inverse=True)
    ordnung = np.argsort(erst, kind='stable')
    rang = np.empty_like(ordnung)
    rang[ordnung] = np.arange(len(ordnung))
    return quellen[erst[ordnung]], rang[inv.reshape(-1)]


def _ungueltig(lokal):
    """Dreiecke, deren Indexspanne die 8-Bit-Differenzen sprengt (nach Drehung)."""
    return (lokal.max(1) - lokal.min(1)) > 255


def _bandbreiten_ordnung(n, lokal):
    """Cuthill-McKee-Nummerierung je Zusammenhangskomponente des Kantengraphen.

    Kleine Bandbreite = kleine Indexspanne je Dreieck. Start an einem
    pseudo-peripheren Knoten, Nachbarn nach Grad (dann alter Index). Komponenten
    in Reihenfolge ihres kleinsten alten Index; deterministisch.
    Rückgabe: neue Nummer je alter lokaler Nummer.
    """
    nachbarn = [set() for _ in range(n)]
    for a, b, c in lokal.tolist():
        nachbarn[a].update((b, c))
        nachbarn[b].update((a, c))
        nachbarn[c].update((a, b))
    for i, s in enumerate(nachbarn):
        s.discard(i)
    grad = [len(s) for s in nachbarn]
    nachbarn = [sorted(s, key=lambda j: (grad[j], j)) for s in nachbarn]

    def ebenen(start):
        tiefe = {start: 0}
        letzte = [start]
        while True:
            neu = []
            for v in letzte:
                for w in nachbarn[v]:
                    if w not in tiefe:
                        tiefe[w] = tiefe[v] + 1
                        neu.append(w)
            if not neu:
                return tiefe, letzte
            letzte = neu

    neu_nr = np.full(n, -1, dtype=np.int64)
    zaehler = 0
    for s0 in range(n):
        if neu_nr[s0] >= 0:
            continue
        tiefe, letzte = ebenen(s0)
        start, exz = s0, max(tiefe.values())
        for _ in range(6):
            kandidat = min(letzte, key=lambda j: (grad[j], j))
            t2, l2 = ebenen(kandidat)
            e2 = max(t2.values())
            if e2 <= exz:
                break
            start, exz, letzte = kandidat, e2, l2
        neu_nr[start] = zaehler
        zaehler += 1
        schlange, kopf = [start], 0
        while kopf < len(schlange):
            v = schlange[kopf]
            kopf += 1
            for w in nachbarn[v]:
                if neu_nr[w] < 0:
                    neu_nr[w] = zaehler
                    zaehler += 1
                    schlange.append(w)
    return neu_nr


def _kachel(vertices, dreiecke, codes, attribute, flags, indices, blattgroesse, max_tiefe,
            statistik=None):
    """Baut Palette, geteilte lokale Vertexliste und kodierbare Dreiecke (AP4c).

1. Vertices der Kachel werden zusammengefasst (gleiche float32-Position und
   gleiches Attribut = ein Vertex), Reihenfolge = aufsteigender Eingangsindex.
2. Erfüllt diese Reihenfolge die Kodierung (Indexspanne je Dreieck <= 255)
   nicht, wird nach Cuthill-McKee neu nummeriert (kleinere Spannen).
3. Jedes Dreieck wird zyklisch so gedreht, dass der kleinste Index vorn steht
   (Winding bleibt; Kantenflagbits werden mitgedreht).
4. Nur wenn auch dann eine Spanne > 255 bleibt, werden die drei Vertices des
   Dreiecks lokal dupliziert (Anzahl in ``statistik['duplikate']``).
5. Dreiecke werden stabil nach Basisindex sortiert (wie in allen Originalen).
Bereits geordnete Eingaben (z. B. Originalkacheln) bleiben unverändert.
"""
    palette = list(OrderedDict.fromkeys(codes[indices].tolist()))
    if len(palette) > 8:
        raise _KachelVoll('Mehr als acht Oberflächencodes')
    slots = {code: i for i, code in enumerate(palette)}
    quellen = np.unique(dreiecke[indices])
    vertreter, lokal_von_quelle = _lokale_vertices(vertices, attribute, quellen)
    if len(vertreter) > 1024:
        raise _KachelVoll('Mehr als 1024 lokale Vertices')
    lokal = lokal_von_quelle[np.searchsorted(quellen, dreiecke[indices])]
    umsortiert = False
    schlecht = _ungueltig(lokal)
    if schlecht.any():
        neu_nr = _bandbreiten_ordnung(len(vertreter), lokal)
        lokal_neu = neu_nr[lokal]
        schlecht_neu = _ungueltig(lokal_neu)
        if schlecht_neu.sum() < schlecht.sum():
            umsortiert = True
            ordnung = np.argsort(neu_nr)
            vertreter, lokal, schlecht = vertreter[ordnung], lokal_neu, schlecht_neu
    lokale_quellen = vertreter.tolist()
    duplikate = 0
    if schlecht.any():
        # Rückfall wie bisher: jüngste Kopie wiederverwenden, sonst drei neue Kopien.
        zuletzt = {}
        lokal = lokal.copy()
        for ti in np.flatnonzero(schlecht).tolist():
            kandidat = [zuletzt.get(int(v), int(v)) for v in lokal[ti]]
            if max(kandidat) - min(kandidat) > 255:
                kandidat = []
                for v in lokal[ti].tolist():
                    zuletzt[v] = len(lokale_quellen)
                    kandidat.append(len(lokale_quellen))
                    lokale_quellen.append(lokale_quellen[v])
                    duplikate += 1
            lokal[ti] = kandidat
        if len(lokale_quellen) > 1024:
            raise _KachelVoll('Vertex-/Basisindex überschreitet 10 Bit')
    drehung = np.argmin(lokal, axis=1)
    zeilen = np.arange(len(lokal))[:, None]
    lokal = lokal[zeilen, (drehung[:, None] + np.arange(3)) % 3]
    tri_flags = _flags_rotieren(flags[indices], drehung)
    reihenfolge = np.argsort(lokal[:, 0], kind='stable')
    lokal, tri_flags = lokal[reihenfolge], tri_flags[reihenfolge]
    tri_slots = np.asarray([slots[c] for c in codes[indices]], dtype=np.uint8)[reihenfolge]
    if int(lokal[:, 0].max()) > 1023:
        raise _KachelVoll('Basisindex überschreitet 10 Bit')
    if statistik is not None:
        statistik['kacheln'] = statistik.get('kacheln', 0) + 1
        statistik['vertices'] = statistik.get('vertices', 0) + len(lokale_quellen)
        statistik['zusammengelegt'] = statistik.get('zusammengelegt', 0) + len(quellen) - len(vertreter)
        statistik['duplikate'] = statistik.get('duplikate', 0) + duplikate
        statistik['umsortierte_kacheln'] = statistik.get('umsortierte_kacheln', 0) + int(umsortiert)
        statistik['gedrehte_dreiecke'] = statistik.get('gedrehte_dreiecke', 0) + int(np.count_nonzero(drehung))
    basis = lokal[:, 0]
    records = np.empty((len(lokal), 4), dtype=np.uint8)
    records[:, 0] = ((basis >> 8) & 3) | (tri_slots.astype(np.int64) << 2) | (tri_flags.astype(np.int64) << 5)
    records[:, 1] = basis & 255
    records[:, 2] = lokal[:, 1] - basis
    records[:, 3] = lokal[:, 2] - basis
    records = [records.tobytes()]
    lokale_quellen = np.asarray(lokale_quellen, dtype=np.int64)
    local_v = vertices[lokale_quellen]
    local_t = np.asarray(lokal, dtype=np.int64)
    bounds = np.stack((local_v.min(0) - np.float32(.1), local_v.max(0) + np.float32(.1)))
    node_data, leaf_data = _quadtree(local_v, local_t, bounds, blattgroesse, max_tiefe)
    header = bytearray(144)
    struct.pack_into('<I6f5I', header, 0, 0x20002, *bounds.ravel(), len(indices), len(local_v), 8, 144, 0)
    no = 144 + len(local_v) * 16
    to = no + len(node_data)
    struct.pack_into('<5I', header, 48, no, 0, to, 0, to + 4 * len(indices))
    # Unbenutzte Plätze wiederholen den letzten Code wie viele Originalkacheln.
    header[80:112] = b''.join(palette + [palette[-1]] * (8 - len(palette)))
    dtype = np.dtype([('position', '<f4', (3,)), ('attribut', '<u4')])
    vertex_data = np.empty(len(local_v), dtype=dtype)
    vertex_data['position'] = local_v
    vertex_data['attribut'] = attribute[lokale_quellen]
    return bytes(header) + vertex_data.tobytes() + node_data + b''.join(records) + leaf_data


def _punkt_dreieck_abstand(p, a, b, c):
    """Abstand Punkt–Dreieck (vektorisiert, nächster Punkt nach Ericson)."""
    ab, ac, ap = b - a, c - a, p - a
    d1, d2 = (ab * ap).sum(1), (ac * ap).sum(1)
    bp = p - b
    d3, d4 = (ab * bp).sum(1), (ac * bp).sum(1)
    cp = p - c
    d5, d6 = (ab * cp).sum(1), (ac * cp).sum(1)
    va, vb, vc = d3 * d6 - d5 * d4, d5 * d2 - d1 * d6, d1 * d4 - d3 * d2
    with np.errstate(divide='ignore', invalid='ignore'):
        nenner = va + vb + vc
        q = a + ab * (vb / nenner)[:, None] + ac * (vc / nenner)[:, None]
        faelle = [
            ((d1 <= 0) & (d2 <= 0), a),
            ((d3 >= 0) & (d4 <= d3), b),
            ((d6 >= 0) & (d5 <= d6), c),
            ((vc <= 0) & (d1 >= 0) & (d3 <= 0), a + ab * (d1 / (d1 - d3))[:, None]),
            ((vb <= 0) & (d2 >= 0) & (d6 <= 0), a + ac * (d2 / (d2 - d6))[:, None]),
            ((va <= 0) & (d4 - d3 >= 0) & (d5 - d6 >= 0),
             b + (c - b) * ((d4 - d3) / ((d4 - d3) + (d5 - d6)))[:, None]),
        ]
    erledigt = np.zeros(len(p), dtype=bool)
    for maske, punkt in faelle:
        neu = maske & ~erledigt
        q[neu] = punkt[neu]
        erledigt |= maske
    abstand = np.linalg.norm(q - p, axis=1)
    # Entartete Dreiecke: Abstand zu den Ecken als sichere Obergrenze
    kaputt = ~np.isfinite(abstand)
    if kaputt.any():
        abstand[kaputt] = np.min([np.linalg.norm(x[kaputt] - p[kaputt], axis=1) for x in (a, b, c)], axis=0)
    return abstand


def kantenflags(vertices, dreiecke, *, raster=0.001, beruehrung=0.05, knick_cos=None, zelle=4.0):
    """Dreiecksflags (drei obere Bits) aus der Kantennachbarschaft (AP4c).

    Befund an Portimão und Bahrain (docs/formate/trn.md, Abschnitt 10): Bit e
    gehört zur Kante (v_e, v_e+1). Es ist an Kanten mit Nachbarfläche gesetzt
    und an offenen Rändern gelöscht; an gemeinsamen Kanten wird es nur an
    konvexen, meist scharfen Knicken gelöscht, nie an konkaven. Diese Funktion
    setzt Bit e, wenn

    - eine andere Dreieckskante dieselben Endpunkte hat (Positionen auf
      ``raster`` Meter gerundet), oder
    - ``beruehrung`` > 0 und der Kantenmittelpunkt höchstens ``beruehrung``
      Meter von einem anderen Dreieck entfernt liegt (T-Stoß, wie in Bahrain).

    ``knick_cos`` (optional, z. B. 0.0): zusätzlich löschen an gemeinsamen
    konvexen Kanten mit cos(Winkel der Flächennormalen) < knick_cos. Die
    Originalschwelle ist nicht eindeutig; Standard: nicht angewandt.
    Rückgabe: uint8-Array (M) mit Werten 0..7.
    """
    p = np.asarray(vertices, dtype=np.float64)
    t = np.asarray(dreiecke, dtype=np.int64)
    if p.ndim != 2 or p.shape[1:] != (3,) or t.ndim != 2 or t.shape[1:] != (3,):
        raise ValueError('vertices N×3 und dreiecke M×3 erforderlich')
    schluessel = np.round(p / raster).astype(np.int64)
    _, inv = np.unique(schluessel, axis=0, return_inverse=True)
    g = inv.reshape(-1)[t]
    a, b = g, np.roll(g, -1, axis=1)          # Kante e = (v_e, v_e+1)
    lo, hi = np.minimum(a, b), np.maximum(a, b)
    kante = lo * (int(g.max()) + 1) + hi
    _, kinv, anzahl = np.unique(kante.ravel(), return_inverse=True, return_counts=True)
    anzahl = anzahl[kinv.reshape(-1)].reshape(-1, 3)
    gesetzt = anzahl >= 2
    if beruehrung and beruehrung > 0:
        offen = np.argwhere(~gesetzt)
        if len(offen):
            pa = p[t[offen[:, 0], offen[:, 1]]]
            pb = p[t[offen[:, 0], (offen[:, 1] + 1) % 3]]
            mitte = (pa + pb) * .5
            # Dreiecke in XZ-Zellen eintragen (AABB um beruehrung erweitert)
            pt = p[t]
            tlo = np.floor((pt[:, :, [0, 2]].min(1) - beruehrung) / zelle).astype(np.int64)
            thi = np.floor((pt[:, :, [0, 2]].max(1) + beruehrung) / zelle).astype(np.int64)
            nx, nz = thi[:, 0] - tlo[:, 0] + 1, thi[:, 1] - tlo[:, 1] + 1
            n = nx * nz
            tri_id = np.repeat(np.arange(len(t)), n)
            lauf = np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n)
            cx = tlo[tri_id, 0] + lauf % np.repeat(nx, n)
            cz = tlo[tri_id, 1] + lauf // np.repeat(nx, n)
            ver = 1 << 31
            zkey = (cx + ver) * (1 << 32) + (cz + ver)
            ordnung = np.argsort(zkey, kind='stable')
            zkey, tri_id = zkey[ordnung], tri_id[ordnung]
            mz = np.floor(mitte[:, [0, 2]] / zelle).astype(np.int64)
            qkey = (mz[:, 0] + ver) * (1 << 32) + (mz[:, 1] + ver)
            von, bis = np.searchsorted(zkey, qkey, 'left'), np.searchsorted(zkey, qkey, 'right')
            treffer = np.zeros(len(offen), dtype=bool)
            # Paare (Abfrage, Kandidat) blockweise prüfen
            start = 0
            while start < len(offen):
                ende = start
                summe = 0
                while ende < len(offen) and (summe < 400000 or ende == start):
                    summe += int(bis[ende] - von[ende])
                    ende += 1
                laenge = bis[start:ende] - von[start:ende]
                q = np.repeat(np.arange(start, ende), laenge)
                k = tri_id[np.concatenate([np.arange(v0, v1) for v0, v1 in zip(von[start:ende], bis[start:ende])])
                           if len(q) else np.zeros(0, np.int64)]
                eigen = k == offen[q, 0]
                q, k = q[~eigen], k[~eigen]
                if len(q):
                    d = _punkt_dreieck_abstand(mitte[q], pt[k, 0], pt[k, 1], pt[k, 2])
                    treffer[np.unique(q[d <= beruehrung])] = True
                start = ende
            gesetzt[offen[treffer, 0], offen[treffer, 1]] = True
    if knick_cos is not None:
        n = np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]])
        n /= np.maximum(np.linalg.norm(n, axis=1), 1e-12)[:, None]
        flach = kante.ravel()
        ordnung = np.argsort(flach, kind='stable')
        sortiert = flach[ordnung]
        paar = np.flatnonzero((sortiert[1:] == sortiert[:-1]) &
                              np.r_[True, sortiert[1:-1] != sortiert[:-2]] &
                              np.r_[sortiert[2:] != sortiert[1:-1], True])
        for i, j in zip(ordnung[paar], ordnung[paar + 1]):
            ti, ei, tj, ej = i // 3, i % 3, j // 3, j % 3
            gegen = t[tj, (ej + 2) % 3]
            konvex = np.dot(n[ti], p[gegen] - p[t[ti, ei]]) < -1e-4
            if konvex and np.dot(n[ti], n[tj]) < knick_cos:
                gesetzt[ti, ei] = gesetzt[tj, ej] = False
    return (gesetzt[:, 0] | (gesetzt[:, 1] << 1) | (gesetzt[:, 2] << 2)).astype(np.uint8)


def trn_erzeugen(vertices, dreiecke, codes, *, vertex_attribute=None, flags=None,
                 max_dreiecke=512, blattgroesse=24, max_tiefe=12,
                 kachelgruppen=None, version=0x20002, statistik=None):
    """Erzeugt vollständige entpackte TRN~-Bytes aus einer Dreiecksliste.

    vertices: N×3, Meter, XYZ mit Y oben; auf float32 gerundet.
    dreiecke: M×3 ganzzahlige Eingangsindices; Winding bleibt erhalten.
    codes: M FourCCs als str/bytes, Nullcode auch als '0x00000000'.
    vertex_attribute: optional N uint32; Standard 0xffffffff (weiße Farbe
        als Vermutung). flags: optional M Werte 0..7; Standard 7, dessen
        Bedeutung nicht abschließend geklärt ist.
    kachelgruppen: optional [(name, dreiecksindices), ...], disjunkte,
        vollständige Partition. Erzwingt diese äußere Zuordnung, niemals
        Originalblöcke oder Blattattribute. Zu große Gruppen werden mit
        klarer Fehlermeldung abgelehnt; ohne Vorgabe automatisch geteilt.

    statistik: optional dict; wird um Zählwerte ergänzt (kacheln, vertices,
        zusammengelegt = innerhalb einer Kachel verschmolzene gleiche Vertices,
        duplikate = wegen der Indexkodierung lokal kopierte Vertices,
        umsortierte_kacheln, gedrehte_dreiecke). Nur erfolgreich geschriebene
        Kacheln zählen.

    Je Kachel entsteht eine geteilte Vertexliste ohne Duplikate (gleiche
    float32-Position und gleiches Attribut = ein Vertex); Dreiecke werden bei
    Bedarf zyklisch gedreht (Winding bleibt, Flagbits werden mitgedreht).

    Grenzen: 10-Bit-Vertexbasis, 8-Bit-Differenzen und 15-Bit-Baumindices.
    Zusätzlich konservativ höchstens 1024 lokale Vertices pro Kachel.
    Jede gültige endliche Eingabe wird verlustfrei in der float32-Geometrie
    auf mehrere Kacheln verteilt. Die Original-Teilungsheuristik und die
    Laufzeitakzeptanz einer Version-2-Ressource im Portimão-Slot sind offen.
    """
    if version != 0x20002:
        raise ValueError('Freie Version-4-Erzeugung ist wegen offener Blattattribute nicht freigegeben')
    raw_v = np.asarray(vertices)
    if raw_v.ndim != 2 or raw_v.shape[1:] != (3,) or not len(raw_v):
        raise ValueError('Vertices müssen eine nicht leere N×3-Liste sein')
    if not np.issubdtype(raw_v.dtype, np.number) or np.iscomplexobj(raw_v):
        raise ValueError('Vertexkoordinaten müssen reelle Zahlen sein')
    with np.errstate(over='ignore', invalid='ignore'):
        vertices = np.asarray(raw_v, dtype=np.float32)
    if not np.isfinite(vertices).all():
        raise ValueError('Vertexkoordinaten sind nicht als endliche float32 darstellbar')
    raw_t = np.asarray(dreiecke)
    if raw_t.ndim != 2 or raw_t.shape[1:] != (3,) or not len(raw_t):
        raise ValueError('Dreiecke müssen eine nicht leere M×3-Liste sein')
    dreiecke = _ganzzahlen(raw_t, raw_t.shape, 'Dreiecke', len(vertices) - 1).astype(np.int64)
    codes = list(codes)
    if len(codes) != len(dreiecke):
        raise ValueError('Genau ein Oberflächencode je Dreieck erforderlich')
    codes = np.asarray([_code(b'\0' * 4 if c == '0x00000000' else c) for c in codes], dtype=object)
    attribute = (np.full(len(vertices), 0xffffffff, dtype=np.uint32) if vertex_attribute is None else
                 _ganzzahlen(vertex_attribute, (len(vertices),), 'Vertexattribute', 0xffffffff).astype(np.uint32))
    flags = (np.full(len(dreiecke), 7, dtype=np.uint8) if flags is None else
             _ganzzahlen(flags, (len(dreiecke),), 'Dreiecksflags', 7).astype(np.uint8))
    for value, name, lo, hi in ((max_dreiecke, 'max_dreiecke', 1, 65535),
                               (blattgroesse, 'blattgroesse', 1, 65535),
                               (max_tiefe, 'max_tiefe', 0, 24)):
        if not isinstance(value, (int, np.integer)) or not lo <= value <= hi:
            raise ValueError(name + ': Ganzzahl außerhalb des erlaubten Bereichs')
    entries = []

    def ausgeben(name, ix):
        probe = {}
        data = _kachel(vertices, dreiecke, codes, attribute, flags, ix, blattgroesse, max_tiefe, probe)
        entries.append(JpakEntry(name, data))
        if statistik is not None:
            for schluessel, wert in probe.items():
                statistik[schluessel] = statistik.get(schluessel, 0) + wert

    if kachelgruppen is not None:
        belegung = np.zeros(len(dreiecke), dtype=np.int64)
        namen = set()
        for name, indices in kachelgruppen:
            ix_raw = np.asarray(indices)
            if ix_raw.ndim != 1 or not len(ix_raw):
                raise ValueError('Kachelgruppe muss eine nicht leere Indexliste sein')
            ix = _ganzzahlen(ix_raw, ix_raw.shape, 'Kachelgruppe', len(dreiecke) - 1).astype(np.int64)
            if (not isinstance(name, str) or not name.endswith('.vcqtc') or
                    '/' in name or '\\' in name or '\0' in name or name in namen):
                raise ValueError('Kachelname ungültig oder doppelt')
            name.encode('ascii')
            if len(ix) > 65535:
                raise ValueError('Kachelgruppe überschreitet 16-Bit-Dreiecksindices')
            np.add.at(belegung, ix, 1)
            namen.add(name)
            try:
                ausgeben(name, ix)
            except _KachelVoll as exc:
                raise ValueError('Vorgegebene Kachelgruppe nicht kodierbar: ' + name) from exc
        if not len(entries) or np.any(belegung != 1):
            raise ValueError('Kachelgruppen müssen jedes Dreieck genau einmal enthalten')
    else:
        points = vertices[dreiecke][:, :, [0, 2]].astype(np.float64)
        minima = points.min(1)
        lo, hi = points.reshape(-1, 2).min(0) - .1, points.reshape(-1, 2).max(0) + .1

        def teilen(ix, a, b, name, depth):
            if len(ix) <= max_dreiecke:
                try:
                    ausgeben(name + '.vcqtc', ix)
                    return
                except _KachelVoll:
                    pass
            mid = (a + b) * .5
            quadrant = ((minima[ix, 0] > mid[0]).astype(np.int8) * 2 +
                        (minima[ix, 1] > mid[1]).astype(np.int8))
            groups = [ix[quadrant == k] for k in range(4)]
            # Mehrere coincidente Flächen sind eine erlaubte Eingabe. Sie
            # können durch rein räumliche Halbierung nicht getrennt werden.
            if depth >= 24 or max(map(len, groups)) == len(ix):
                groups = np.array_split(ix, min(4, len(ix)))
            for k, group in enumerate(groups):
                if not len(group):
                    continue
                up = np.array([k >> 1, k & 1], dtype=bool)
                teilen(group, np.where(up, mid, a), np.where(up, b, mid),
                       name + '_' + format(k, '02b'), depth + 1)

        teilen(np.arange(len(dreiecke), dtype=np.int64), lo, hi, 'qt', 0)
    return JpakFile(entries).to_bytes()
