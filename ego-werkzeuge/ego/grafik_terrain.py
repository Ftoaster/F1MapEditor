"""TerrainRes aus materialweise indizierten Dreieckslisten erzeugen (F1 25).

Das Render-Terrain enthält eine flache, nach z/x sortierte Rastertabelle, keine
gespeicherten Eltern-/Kindverweise. Dreiecksschwerpunkte bestimmen die Zelle;
deren AABB/Kugel umfasst die vollständigen Dreiecke. Indizes sind u16, größere
Vertexsätze benutzen Basis-Vertices. Pufferformate und Materialien kommen aus
einem Träger-Terrain. Ströme: Position, Normale (+ Zusatzwert), Tangente (+ Vorzeichen),
Masken/Farbe und UV-Paare. Ältere Terrain-Layouts haben stattdessen half4-UVs.

Gesichert sind binärer Aufbau, Rasterzuordnung und geometrische Basisvektoren.
Die Shader-Bedeutung der Masken, des Normalen-w und der Zusatz-UVs ist offen.
Ohne explizite Attribute werden Material-Vorlagenwerte verwendet; Zusatz-UVs
werden mit (UV0, Welt-UV) vorbelegt. Das ist eine dokumentierte Annahme und
ersetzt keinen Spieltest. Siehe docs/formate/grafik.md, Abschnitt Terrain erzeugen.
"""

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

from . import erp, grafik as g


@dataclass
class TerrainNetz:
    """Ein Materialnetz; UVs und optionale Attribute gelten pro Vertex.

    material: Index im Träger-Terrain oder vollständige Materialkennung.
    farben: RGBA-Bytes (N,4), keine automatisch interpretierten Blendgewichte.
    uv_welt: zweites Paar des Haupt-UV-Stroms (N,2).
    uv_zusatz: kompletter zusätzlicher float4-Strom (N,4), sofern vorhanden.
    normale_w: Zusatzwert des half4-Normalenstroms (N,).
    tangenten: (N,4), xyz und Bitangenten-Vorzeichen; ohne Angabe neu berechnet.
    Harte Kanten und UV-Nähte benötigen getrennte Vertices in der Eingabe.
    """

    material: Union[int, str]
    positionen: np.ndarray
    dreiecke: np.ndarray
    uvs: np.ndarray
    normalen: Optional[np.ndarray] = None
    tangenten: Optional[np.ndarray] = None
    farben: Optional[np.ndarray] = None
    uv_welt: Optional[np.ndarray] = None
    uv_zusatz: Optional[np.ndarray] = None
    normale_w: Optional[np.ndarray] = None


@dataclass(frozen=True)
class TerrainRaster:
    """Rastergrenzen (x,z) und Zahl der Zellen; keine Baumtiefe.

    Standard: floor(Ausdehnung / kachelgroesse), mindestens eine Zelle je Achse.
    Die tatsächlichen Zellbreiten ergeben sich aus Ausdehnung / Anzahl.
    """

    minimum: Tuple[float, float]
    maximum: Tuple[float, float]
    anzahl: Tuple[int, int]

    @classmethod
    def aus_positionen(cls, positionen: Sequence[np.ndarray], kachelgroesse=100.0):
        if not math.isfinite(kachelgroesse) or kachelgroesse <= 0:
            raise ValueError("Kachelgröße muss endlich und positiv sein")
        mn = np.min([p[:, (0, 2)].min(0) for p in positionen], axis=0)
        mx = np.max([p[:, (0, 2)].max(0) for p in positionen], axis=0)
        n = np.maximum(1, np.floor((mx.astype(float) - mn) / kachelgroesse).astype(np.int64))
        return cls(tuple(float(v) for v in mn), tuple(float(v) for v in mx), tuple(int(v) for v in n))

    def zellen(self, pos: np.ndarray, tri: np.ndarray) -> np.ndarray:
        mn, mx = np.asarray(self.minimum, '<f4'), np.asarray(self.maximum, '<f4')
        anzahl = np.asarray(self.anzahl)
        if (mn.shape != (2,) or mx.shape != (2,) or anzahl.shape != (2,)
                or not np.isfinite([mn, mx]).all() or np.any(mx < mn)
                or anzahl.dtype.kind not in 'iu' or np.any(anzahl < 1) or np.any(anzahl > 65536)):
            raise ValueError("Ungültige Rastergrenzen oder Zellzahl (1..65536)")
        verwendet = pos[np.unique(tri)][:, (0, 2)]
        if np.any(verwendet < mn) or np.any(verwendet > mx):
            raise ValueError("Geometrie liegt außerhalb der Rastergrenzen")
        # Die Reihenfolge dieser float32-Operationen reproduziert Grenzfälle der Originale.
        cc = (pos[tri].sum(1) * np.float32(1.0 / 3.0))[:, (0, 2)]
        ausdehnung = np.where(mx > mn, mx - mn, np.float32(1))
        xy = np.floor((cc - mn) / ausdehnung * anzahl.astype('<f4')).astype(np.int64)
        xy = np.clip(xy, 0, anzahl - 1)
        return xy[:, 0] | (xy[:, 1] << 16)


def _layout(strides):
    """Liefert (UV-Slot, Farb-Slot, Zusatz-Slot) für belegte Terrain-Layouts."""
    s = tuple(strides)
    layouts = {(12, 8, 8, 4, 16): (4, 3, None),
               (12, 8, 8, 4, 16, 16): (4, 3, 5),
               (12, 8, 8, 8): (3, None, None),
               (12, 8, 8, 8, 4): (3, 4, None)}
    if s not in layouts:
        raise g.GrafikFormatFehler("Nicht unterstütztes Terrain-Layout: %r" % (s,))
    return layouts[s]


def _matrix(puffer):
    dt = 'u1' if puffer.stride == 4 else '<f2' if puffer.stride == 8 else '<f4'
    return np.frombuffer(puffer.daten, dt).reshape(puffer.anzahl, -1)


def terrain_lesen(quelle, kennung: str) -> List[TerrainNetz]:
    """Liest Dreiecke je Material inklusive aller Attribute, ohne Knoten/Zeichnungen.

    Damit kann der Erzeuger dieselbe Geometrie neu kompilieren. Materialslots mit
    derselben Kennung bleiben durch numerische Materialindizes unterscheidbar.
    """
    idx = g._als_index(quelle)
    t = g.Terrain.aus_bytes(idx[kennung].data())
    ib = g.GfxPuffer.aus_ressource(idx[t.ib_kennung]).indizes()
    cache = {}
    out = []
    for m, gruppe in enumerate(t.material_puffer):
        zeichnungen = [z for z in t.zeichnungen if z.material == m]
        if not zeichnungen:
            continue
        vbs = [v for v in gruppe if v >= 0]
        uv, farbe, zusatz = _layout([t.strides[v] for v in vbs])
        tri = np.concatenate([(ib[z.start_index:z.start_index + z.index_anzahl].astype(np.int64)
                               + z.basis_vertex).reshape(-1, 3) for z in zeichnungen])
        benutzt, lokal = np.unique(tri, return_inverse=True)
        werte = []
        for v in vbs:
            if v not in cache:
                cache[v] = _matrix(g.GfxPuffer.aus_ressource(idx[t.vb_kennungen[v]]))
            werte.append(cache[v][benutzt].copy())
        out.append(TerrainNetz(m, werte[0], lokal.reshape(-1, 3), werte[uv][:, :2],
                               werte[1][:, :3], werte[2],
                               werte[farbe] if farbe is not None else None,
                               werte[uv][:, 2:], werte[zusatz] if zusatz is not None else None,
                               werte[1][:, 3]))
    return out


def _attribut(wert, form, name):
    a = np.asarray(wert, dtype=np.float64)
    if a.shape != form or not np.isfinite(a).all():
        raise ValueError("%s: erwartet %r mit endlichen Werten" % (name, form))
    return a


def _tangenten(pos, tri, uv, nrm):
    """UV-Tangente und Vorzeichen der Bitangente, auch bei gespiegelten UVs."""
    e1, e2 = pos[tri[:, 1]] - pos[tri[:, 0]], pos[tri[:, 2]] - pos[tri[:, 0]]
    d1, d2 = uv[tri[:, 1]] - uv[tri[:, 0]], uv[tri[:, 2]] - uv[tri[:, 0]]
    det = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    inv = np.zeros_like(det)
    ok = np.abs(det) > 1e-15
    inv[ok] = 1.0 / det[ok]
    tt = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) * inv[:, None]
    bb = (e2 * d1[:, 0:1] - e1 * d2[:, 0:1]) * inv[:, None]
    tan, bit = np.zeros_like(pos), np.zeros_like(pos)
    for j in range(3):
        np.add.at(tan, tri[:, j], tt)
        np.add.at(bit, tri[:, j], bb)
    tan -= nrm * (tan * nrm).sum(1, keepdims=True)
    laenge = np.linalg.norm(tan, axis=1)
    # Für zusammengefallene UVs eine deterministische, orthogonale Ersatzachse.
    achse = np.eye(3)[np.argmin(np.abs(nrm), axis=1)]
    ersatz = np.cross(achse, nrm)
    tan[laenge < 1e-12] = ersatz[laenge < 1e-12]
    tan /= np.maximum(np.linalg.norm(tan, axis=1, keepdims=True), 1e-15)
    # EGO-Konvention, durch alle Terrain-Sätze geprüft: B = w * cross(T, N).
    w = np.where((np.cross(tan, nrm) * bit).sum(1) < 0, -1.0, 1.0)
    return np.c_[tan, w]


def _profil(werte, uv_slot, farb_slot, ids):
    """Statistische Vorbelegung; kein behauptetes Verständnis der Shader-Masken."""
    pos = werte[0][ids].astype(float)
    uv = werte[uv_slot][ids].astype(float)
    mat = np.c_[pos[:, (0, 2)], np.ones(len(pos))]
    maske = np.ones(len(pos), dtype=bool)
    for _ in range(4):
        aff = np.linalg.lstsq(mat[maske], uv[maske, 2:], rcond=None)[0]
        fehler = np.abs(mat @ aff - uv[:, 2:]).max(1)
        maske = fehler <= np.quantile(fehler, 0.8)
    farbe = None
    if farb_slot is not None:
        # Häufigstes vollständiges RGBA-Tupel, keine künstliche Mischung von Masken.
        ww, counts = np.unique(werte[farb_slot][ids], axis=0, return_counts=True)
        farbe = ww[counts.argmax()]
    nw, counts = np.unique(werte[1][ids, 3], return_counts=True)
    return aff, float(np.quantile(fehler, 0.95)), farbe, float(nw[counts.argmax()])


def _huelle(pos):
    mn, mx = pos.min(0), pos.max(0)
    c = (mn + mx) * np.float32(0.5)
    # Nach außen runden: die gespeicherte Kugel soll die Punkte sicher enthalten.
    r = np.nextafter(np.float32(np.linalg.norm((mx - mn) * np.float32(0.5))), np.float32(np.inf))
    return tuple(float(v) for v in (*c, r, *mn, 0, *mx, 0))


def terrain_erzeugen(archiv: erp.ErpFile, kennung: str, netze: Sequence[TerrainNetz], *,
                      quelle=None, neue_kennung: Optional[str] = None,
                      raster: Optional[TerrainRaster] = None, kachelgroesse: float = 100.0) -> Dict[str, object]:
    """Kompiliert neue Geometrie und schreibt TerrainRes + Puffer ins Zielarchiv.

    kennung bezeichnet das Träger-Terrain (Materialslots, Pufferformate). Ohne
    neue_kennung wird dessen TerrainRes ersetzt. Alle Puffer werden unter eigenen
    Kennungen angelegt; die Originalpuffer bleiben für andere Ressourcen erhalten.
    quelle kann ein RessourcenIndex sein, wenn das Träger-Terrain verteilt liegt.
    Positionen werden vor Raster/Hüllkörperberechnung auf float32 quantisiert.

    Die Eingabe wird vollständig geprüft und kompiliert, bevor das Archiv geändert
    wird. Leere/ungültige Netze, unbekannte Materialien und nicht endliche Werte
    werden abgewiesen. Normalen/Tangenten werden berechnet, außer sie sind explizit
    angegeben (Erhaltung der modellierten Glättung beim Neuerzeugen).
    Rückgabe: Zahlen, Raster, verwendete Vorbelegungen, geänderte Ressourcen.
    """
    idx = g._als_index(quelle if quelle is not None else archiv)
    vorlage = idx[kennung]
    if vorlage.resource_type != 'TerrainRes':
        raise ValueError("Vorlage ist kein TerrainRes")
    t = g.Terrain.aus_bytes(vorlage.data())
    zielkennung = neue_kennung or kennung
    vorhanden = archiv.find(zielkennung)
    if vorhanden is not None and vorhanden.resource_type != 'TerrainRes':
        raise ValueError("Zielkennung gehört zu einem anderen Ressourcentyp")
    if vorhanden is not None and len(vorhanden.fragments) != 1:
        raise ValueError("TerrainRes muss ein Fragment besitzen")
    if not netze:
        raise ValueError("Mindestens ein nicht leeres Materialnetz ist erforderlich")
    puffer = [g.GfxPuffer.aus_ressource(idx[v]) for v in t.vb_kennungen]
    ib_vorlage = g.GfxPuffer.aus_ressource(idx[t.ib_kennung])
    if ib_vorlage.art != g.PUFFER_INDEX_TERRAIN or ib_vorlage.stride != 2:
        raise g.GrafikFormatFehler("Vorlage hat keinen u16-Terrain-Indexpuffer")
    ib_alt = ib_vorlage.indizes()
    cache, profile, vorbelegungen, vorbereitet = {}, {}, [], []
    for netz in netze:
        if isinstance(netz.material, str):
            treffer = [i for i, v in enumerate(t.materialien) if v == netz.material]
            if len(treffer) != 1:
                raise ValueError("Materialkennung fehlt oder ist mehrdeutig: %s" % netz.material)
            m = treffer[0]
        elif isinstance(netz.material, (int, np.integer)):
            m = int(netz.material)
        else:
            raise ValueError("Material muss Index oder Kennung sein")
        if not 0 <= m < len(t.materialien) or t.material_puffer[m][0] < 0:
            raise ValueError("Material ist kein Render-Material: %r" % netz.material)
        gruppe = tuple(v for v in t.material_puffer[m] if v >= 0)
        us, fs, zs = _layout([puffer[v].stride for v in gruppe])
        n = len(netz.positionen)
        pos = _attribut(netz.positionen, (n, 3), 'Positionen').astype('<f4')
        if not np.isfinite(pos).all():
            raise ValueError("Positionen überschreiten float32")
        tri = np.asarray(netz.dreiecke)
        if tri.ndim != 2 or tri.shape[1] != 3 or not len(tri) or tri.dtype.kind not in 'iu':
            raise ValueError("Dreiecke müssen eine nicht leere ganzzahlige (M,3)-Liste sein")
        tri = tri.astype(np.int64)
        if tri.min() < 0 or tri.max() >= n:
            raise ValueError("Dreiecksindex außerhalb der Vertexliste")
        benutzt = np.unique(tri)
        uv = _attribut(netz.uvs, (n, 2), 'UVs')
        if netz.normalen is None:
            nrm = g.normalen_berechnen(pos.astype(float), tri)
            nrm[np.linalg.norm(nrm, axis=1) < 1e-12] = (0, 1, 0)
        else:
            nrm = _attribut(netz.normalen, (n, 3), 'Normalen')
            if np.any(np.linalg.norm(nrm[benutzt], axis=1) < 1e-12):
                raise ValueError("Verwendete Normalen dürfen nicht null sein")
        tan = (_tangenten(pos.astype(float), tri, uv, nrm)
               if netz.tangenten is None else _attribut(netz.tangenten, (n, 4), 'Tangenten'))
        if np.any(~np.isin(tan[benutzt, 3], (-1, 1))):
            raise ValueError("Tangenten-w muss -1 oder +1 sein")
        # Für neue Werte wird das Profil nur bei fehlenden Attributen benötigt.
        bedarf = (netz.uv_welt is None or netz.normale_w is None
                  or (fs is not None and netz.farben is None))
        if bedarf and m not in profile:
            alt = [ib_alt[z.start_index:z.start_index + z.index_anzahl].astype(np.int64) + z.basis_vertex
                   for z in t.zeichnungen if z.material == m]
            if not alt:
                raise ValueError("Material ohne Vorlagenvertices benötigt explizite Attribute")
            ids = np.unique(np.concatenate(alt))
            werte = []
            for v in gruppe:
                if v not in cache:
                    cache[v] = _matrix(puffer[v])
                werte.append(cache[v])
            profile[m] = _profil(werte, us, fs, ids)
        aff, fit, farbe, nw = profile.get(m, (None, 0.0, None, 0.0))
        if netz.uv_welt is None:
            if fit > 0.001:
                raise ValueError("Welt-UV des Materials nicht affin; uv_welt ausdrücklich angeben")
            uw = np.c_[pos[:, (0, 2)], np.ones(n)] @ aff
            vorbelegungen.append((m, 'Welt-UV aus affinem Vorlagenprofil', fit))
        else:
            uw = _attribut(netz.uv_welt, (n, 2), 'Welt-UV')
        if netz.normale_w is None:
            normale_w = np.full(n, nw)
            vorbelegungen.append((m, 'Normalen-w aus häufigstem Vorlagenwert', nw))
        else:
            normale_w = _attribut(netz.normale_w, (n,), 'Normalen-w')
        werte = [None] * len(gruppe)
        werte[0], werte[1], werte[2], werte[us] = pos, np.c_[nrm, normale_w], tan, np.c_[uv, uw]
        if fs is not None:
            if netz.farben is None:
                fc = np.tile(farbe, (n, 1))
                vorbelegungen.append((m, 'RGBA aus häufigstem Vorlagenwert', farbe.tolist()))
            else:
                fc = _attribut(netz.farben, (n, 4), 'RGBA')
                if np.any(fc < 0) or np.any(fc > 255) or np.any(fc != np.floor(fc)):
                    raise ValueError("RGBA muss ganzzahlige Bytes enthalten")
            werte[fs] = fc
        if zs is not None:
            if netz.uv_zusatz is None:
                werte[zs] = np.c_[uv, uw]
                vorbelegungen.append((m, 'Zusatz-UV vorbelegt mit UV0 und Welt-UV (Annahme)', None))
            else:
                werte[zs] = _attribut(netz.uv_zusatz, (n, 4), 'Zusatz-UV')
        roh = []
        for v, w in zip(gruppe, werte):
            dt = 'u1' if puffer[v].stride == 4 else '<f2' if puffer[v].stride == 8 else '<f4'
            if dt != 'u1' and np.any(np.abs(w) > np.finfo(dt).max):
                raise ValueError("Attribut überschreitet %s" % dt)
            roh.append(np.ascontiguousarray(w, dtype=dt).view('u1').reshape(n, puffer[v].stride))
        vorbereitet.append((m, gruppe, pos, tri, roh))
    if raster is None:
        raster = TerrainRaster.aus_positionen([p[np.unique(tri)] for _, _, p, tri, _ in vorbereitet], kachelgroesse)
    zellen = {}
    for m, gruppe, pos, tri, roh in vorbereitet:
        codes = raster.zellen(pos, tri)
        reihenfolge = np.argsort(codes, kind='stable')
        cuts = np.r_[0, np.flatnonzero(np.diff(codes[reihenfolge])) + 1, len(tri)]
        for aa, bb in zip(cuts[:-1], cuts[1:]):
            code = int(codes[reihenfolge[aa]])
            zellen.setdefault(code, {}).setdefault(m, []).append((gruppe, pos, tri[reihenfolge[aa:bb]], roh))
    basis = g.basis_kennung(zielkennung)
    aktiv = sorted({v for _, gruppe, _, _, _ in vorbereitet for v in gruppe})
    umnummerierung = {v: i for i, v in enumerate(aktiv)}
    neu = g.Terrain(materialien=t.materialien[:],
                    material_puffer=[[umnummerierung.get(v, -1) for v in gruppe] for gruppe in t.material_puffer],
                    strides=[t.strides[v] for v in aktiv], ib_kennung=basis + '?ap3b=terrain&ib=0',
                    vb_kennungen=[basis + '?ap3b=terrain&vb=%d' % i for i in range(len(aktiv))])
    teile = [[] for _ in puffer]
    zaehler = [0] * len(puffer)
    index_teile, index_anzahl = [], 0
    for code, materialien in sorted(zellen.items()):
        erste = len(neu.zeichnungen)
        huellen = []
        for m, eintraege in sorted(materialien.items()):
            # Eigene Eingaben bleiben eigene Zeichnungen (z. B. eine Zusatzplatte).
            for gruppe, pos, tri, roh in eintraege:
                # Ein Teil mit maximal 65536 verschiedenen Vertices passt in u16.
                if len(np.unique(tri)) <= 65536:
                    stapel = [tri]
                else:
                    stapel = [tri[i:i + 21845] for i in range(0, len(tri), 21845)]
                for teil in stapel:
                    ids, lokal = np.unique(teil, return_inverse=True)
                    base = zaehler[gruppe[0]]
                    for v, rr in zip(gruppe, roh):
                        teile[v].append(rr[ids].tobytes())
                        zaehler[v] += len(ids)
                    indices = lokal.astype('<u2').tobytes()
                    index_teile.append(indices)
                    neu.zeichnungen.append(g.TerrainZeichnung(index_anzahl, lokal.size, base, 0, m))
                    index_anzahl += lokal.size
                    huellen.append(pos[ids])
        neu.knoten.append(_huelle(np.concatenate(huellen)))
        neu.knoten_zuordnung.append((len(neu.zeichnungen) - erste, erste, erste, code))
    neu.knoten_extra = b'\0' * (8 * len(neu.knoten))
    fertig = {neu.ib_kennung: g.GfxPuffer(ib_vorlage.art, 2, index_anzahl, ib_vorlage.code, b''.join(index_teile))}
    for v in aktiv:
        alt = puffer[v]
        fertig[neu.vb_kennungen[umnummerierung[v]]] = g.GfxPuffer(alt.art, alt.stride, zaehler[v], alt.code, b''.join(teile[v]))
    # Keine fremden Ressourcen überschreiben, auch bei einer zufälligen Kennungskollision.
    erlaubt = set()
    if vorhanden is not None:
        alt_ziel = g.Terrain.aus_bytes(vorhanden.data())
        erlaubt = set(alt_ziel.vb_kennungen + [alt_ziel.ib_kennung])
    for k in fertig:
        res = archiv.find(k)
        if res is not None and (k not in erlaubt or res.resource_type != 'GfxBufferRes'):
            raise ValueError("Pufferkennung bereits anderweitig vergeben: %s" % k)
    fehler = terrain_pruefen(neu, fertig)
    if fehler:
        raise g.GrafikFormatFehler('; '.join(fehler))
    for k, buf in fertig.items():
        res = archiv.find(k)
        if res is None:
            res = archiv.add_resource(k, 'GfxBufferRes', [(b'temp', buf.kopf_bytes()), (b'temp', buf.daten)],
                                       unknown=idx[t.ib_kennung].unknown)
            res.fragments[0].set_data(buf.kopf_bytes(), compress=False)
            res.fragments[0].flags, res.fragments[1].flags = 16, 4
            res.update_hash()
        else:
            g._puffer_setzen(res, buf)
    if vorhanden is None:
        vorhanden = archiv.add_resource(zielkennung, 'TerrainRes', [(vorlage.fragments[0].name, neu.to_bytes())],
                                          unknown=vorlage.unknown, flags=vorlage.fragments[0].flags)
    else:
        vorhanden.set_fragment_data(0, neu.to_bytes())
    return dict(kennung=zielkennung, knoten=len(neu.knoten), zeichnungen=len(neu.zeichnungen),
                dreiecke=index_anzahl // 3, vertices=sum(zaehler[v] for v in g.terrain_positions_puffer(t) if v in umnummerierung),
                raster=raster, vorbelegungen=vorbelegungen, ressourcen=list(fertig) + [zielkennung])


def terrain_pruefen(t: g.Terrain, puffer: Dict[str, g.GfxPuffer]) -> List[str]:
    """Prüft Referenzen, Indexbereiche, Rasterreihenfolge und Hüllkörper geometrisch."""
    fehler = []
    if not (len(t.knoten) == len(t.knoten_zuordnung) and len(t.knoten_extra) == 8 * len(t.knoten)
            and len(t.materialien) == len(t.material_puffer) and len(t.strides) == len(t.vb_kennungen)):
        return ['Terrain-Tabellen haben unterschiedliche Längen']
    if any(k not in puffer for k in [t.ib_kennung] + t.vb_kennungen):
        return ['Terrain-Pufferreferenz fehlt']
    for v, k in enumerate(t.vb_kennungen):
        p = puffer[k]
        fehler.extend(p.pruefen())
        if p.art != g.PUFFER_VERTEX or p.stride != t.strides[v]:
            fehler.append('Vertexpuffer-Art oder Stride falsch')
    ib = puffer[t.ib_kennung]
    fehler.extend(ib.pruefen())
    if ib.art != g.PUFFER_INDEX_TERRAIN or ib.stride != 2:
        return fehler + ['Indexpuffer-Art oder Stride falsch']
    if fehler:
        return fehler
    indices = ib.indizes()
    pos = {}
    erwartet, letzter_code, indexende = 0, -1, 0
    for node, (anz, start, doppelt, code) in zip(t.knoten, t.knoten_zuordnung):
        if anz < 1 or start != erwartet or doppelt != start or code <= letzter_code:
            fehler.append('Knotenzuordnung nicht lückenlos oder Raster nicht sortiert')
        erwartet += anz
        letzter_code = code
        punkte = []
        for z in t.zeichnungen[start:start + anz]:
            if not 0 <= z.material < len(t.material_puffer):
                fehler.append('Materialindex außerhalb'); continue
            gruppe = [v for v in t.material_puffer[z.material] if v >= 0]
            if not gruppe or max(gruppe) >= len(t.vb_kennungen):
                fehler.append('Material ohne gültigen Vertexsatz'); continue
            if z.index_anzahl <= 0 or z.index_anzahl % 3 or z.start_index != indexende:
                fehler.append('Indexbereiche nicht lückenlos oder keine Dreiecksliste')
            indexende = z.start_index + z.index_anzahl
            if indexende > len(indices):
                fehler.append('Indexbereich außerhalb'); continue
            ids = indices[z.start_index:indexende].astype(np.int64) + z.basis_vertex
            if len(set(puffer[t.vb_kennungen[v]].anzahl for v in gruppe)) != 1:
                fehler.append('Vertexsatz hat unterschiedliche Anzahlen')
            if not len(ids) or ids.min() < 0 or ids.max() >= min(puffer[t.vb_kennungen[v]].anzahl for v in gruppe):
                fehler.append('Vertexindex außerhalb'); continue
            v = gruppe[0]
            if v not in pos:
                pos[v] = np.frombuffer(puffer[t.vb_kennungen[v]].daten, '<f4').reshape(-1, 3)
            punkte.append(pos[v][ids])
        if punkte:
            pp = np.concatenate(punkte).astype(float)
            n = np.array(node)
            if not np.isfinite(n).all() or not np.isfinite(pp).all():
                fehler.append('Nicht endliche Hülle oder Positionen')
            elif (np.any(pp < n[4:7] - 1e-4) or np.any(pp > n[8:11] + 1e-4)
                  or np.any(np.linalg.norm(pp - n[:3], axis=1) > n[3] + 1e-4)):
                fehler.append('Hüllkörper enthält Zeichnung nicht')
    if erwartet != len(t.zeichnungen) or indexende != len(indices):
        fehler.append('Zeichnungen oder Indizes nicht vollständig zugeordnet')
    return fehler
