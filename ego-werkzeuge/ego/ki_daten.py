"""AP12: zwölf KI-Ressourcentypen verlustfrei lesen, ändern und schreiben.

Zehn Typen benutzen ego.pkg, AIBrakeSettings benutzt ego.bxml samt TSMD.
VPTimes besteht aus hintereinanderliegenden Tabellen, jeweils mit eigenem
12-Byte-Kopf, 32-Byte-Datensatzköpfen und float32-Zeitfolgen. Unbekannte
Parameter bleiben als 32-Bit-Worte erhalten. Siehe docs/formate/ki_daten.md.
"""

import copy
import math
import struct
from dataclasses import dataclass, field

import numpy as np

from . import bxml, pkg


TYPEN = {
    'AIAcceleration': ('acpd', b'AIAC'),
    'AIBraking': ('brpd', b'AIBR'),
    'AIBrakeSettings': ('brakelinesettings', None),
    'AICornering': ('copd', b'AICO'),
    'AICruising': ('crpd', b'AICR'),
    'ERSBoost': ('ers_boost_data', b'ERSB'),
    'ERSUseGran': ('ers_use_granular', b'TEUG'),
    'FuelBurnGran': ('fuel_burn_granular', b'TFBG'),
    'AIThrottle': ('thpd', b'AITH'),
    'TyreWearData': ('tyre_wear', b'TTWD'),
    'AIVPChar': ('vp_chars', b'AIVP'),
    'VPTimes': ('vp_times', None),
}
KENNUNGEN = {kennung: typ for typ, (_, kennung) in TYPEN.items() if kennung}
VP_KOPF = struct.Struct('<III')
VP_SATZ = struct.Struct('<8I')


class KiFormatError(ValueError):
    """Unpassender, abgeschnittener oder widersprüchlicher KI-Datensatz."""


def _f32(wort):
    return struct.unpack('<f', struct.pack('<I', wort))[0]


@dataclass
class VpSatz:
    # Beobachtete Interpretation: 0 Wetterklasse, 1 Reifenfaktor, 2 Schwierigkeit,
    # 3/4 unbekannte diskrete Modi, 5 Masse, 6 Grip. Keine Engine-Symbolnamen.
    parameter_worte: tuple
    zeitbytes: bytes

    @property
    def anzahl(self):
        return len(self.zeitbytes) // 4

    @property
    def zeiten(self):
        return struct.unpack('<%df' % self.anzahl, self.zeitbytes)

    @property
    def parameter(self):
        return {'klasse_0': self.parameter_worte[0],
                'faktor_1': _f32(self.parameter_worte[1]),
                'schwierigkeit': _f32(self.parameter_worte[2]),
                'modus_3': self.parameter_worte[3], 'modus_4': self.parameter_worte[4],
                'masse': _f32(self.parameter_worte[5]),
                'grip': _f32(self.parameter_worte[6])}

    def zeiten_setzen(self, werte):
        # 26.10.2026 mit numpy statt Wert für Wert (vorher ≈ 1 s je VPTimes-Ressource); float32 wie struct.pack
        # (C-Umwandlung, nächstgelegener Wert), Überlauf wie dort als OverflowError
        werte = np.asarray(werte if isinstance(werte, (np.ndarray, list, tuple)) else list(werte), dtype=np.float64)
        if werte.ndim != 1:
            raise TypeError('VP-Zeiten: eindimensionale Zahlenfolge erwartet')
        if not len(werte) or not (np.all(np.isfinite(werte)) and np.all(werte >= 0)):
            raise KiFormatError('VP-Zeiten benötigen endliche, nichtnegative Werte')
        if np.any(werte[1:] < werte[:-1]):
            raise KiFormatError('VP-Zeiten müssen monoton steigen')
        if not np.array_equal(werte, np.frombuffer(self.zeitbytes, '<f4').astype(np.float64)):
            with np.errstate(over='ignore'):
                gepackt = werte.astype('<f4')
            if not np.all(np.isfinite(gepackt)):
                raise OverflowError('float too large to pack with f format')
            self.zeitbytes = gepackt.tobytes()


@dataclass
class VpTabelle:
    version: int
    kennung: int
    saetze: list = field(default_factory=list)


def vp_lesen(daten):
    """Alle verketteten VP-Tabellen lesen, ohne einen Restblob zu übergehen."""
    daten = bytes(daten)
    tabellen, pos = [], 0
    while pos < len(daten):
        if pos + VP_KOPF.size > len(daten):
            raise KiFormatError('VP-Tabellenkopf abgeschnitten bei %d' % pos)
        version, kennung, anzahl = VP_KOPF.unpack_from(daten, pos)
        pos += VP_KOPF.size
        if version != 3:
            raise KiFormatError('Unbekannter VP-Kopf bei %d' % (pos-12))
        if anzahl > (len(daten)-pos)//VP_SATZ.size:
            raise KiFormatError('VP-Datensatzanzahl überschreitet Dateigrenze')
        t = VpTabelle(version, kennung)
        for _ in range(anzahl):
            if pos + VP_SATZ.size > len(daten):
                raise KiFormatError('VP-Datensatzkopf abgeschnitten')
            werte = VP_SATZ.unpack_from(daten, pos)
            pos += VP_SATZ.size
            n = werte[-1]
            if n > (len(daten)-pos)//4:
                raise KiFormatError('VP-Zeitfolge überschreitet Dateigrenze')
            t.saetze.append(VpSatz(werte[:7], daten[pos:pos+4*n]))
            pos += 4*n
        tabellen.append(t)
    if not tabellen:
        raise KiFormatError('Leere VP-Datei')
    return tabellen


def vp_schreiben(tabellen):
    ausgabe = bytearray()
    for t in tabellen:
        if t.version != 3:
            raise KiFormatError('Unbekannte VP-Version/Kennung')
        ausgabe += VP_KOPF.pack(t.version, t.kennung, len(t.saetze))
        for s in t.saetze:
            if len(s.parameter_worte) != 7 or len(s.zeitbytes) % 4:
                raise KiFormatError('Ungültiger VP-Datensatz')
            ausgabe += VP_SATZ.pack(*s.parameter_worte, s.anzahl)
            ausgabe += s.zeitbytes
    if not ausgabe:
        raise KiFormatError('Leere VP-Datei')
    return bytes(ausgabe)


@dataclass
class KiDaten:
    typ: str
    paket: object = None
    dokument: object = None
    vp_tabellen: list = field(default_factory=list)
    _eingang: object = field(default=None, repr=False)

    def inhalt(self):
        """Lesesicht; für bitgenauen Rundlauf das KiDaten-Modell aufbewahren."""
        if self.paket is not None:
            return self.paket.inhalt()
        if self.dokument is not None:
            return bxml.to_xml(self.dokument)
        return [{'version': t.version, 'kennung': t.kennung,
                 'saetze': [{'parameter': s.parameter, 'zeiten': s.zeiten}
                            for s in t.saetze]} for t in self.vp_tabellen]

    def _block(self, pfad):
        """Pfad zum Strukturblock, gemeinsam benutzte Blöcke lokal abtrennen."""
        p = self.paket
        if p is None:
            raise KiFormatError('Pfadänderungen erfordern pkg-KI-Daten')
        if self._eingang is None:
            self._eingang = {id(p.wurzel.ziel): 1}
            for b in p.bloecke:
                for r in b.verweise():
                    if r.typ == 0x80:
                        k = id(r.ziel)
                        self._eingang[k] = self._eingang.get(k, 0)+1
        ref = p.wurzel
        for schritt in [None] + list(pfad):
            if schritt is not None:
                if ref.ziel.kennung == b'!idi':
                    ref = p.feld(ref.ziel, schritt).wert
                elif ref.ziel.kennung == b'!ili':
                    ref = ref.ziel.werte[schritt]
                else:
                    raise KiFormatError('Pfad führt nicht durch Wörterbuch/Liste')
            if ref.typ != 0x80 or ref.ziel is None:
                raise KiFormatError('Pfad bezeichnet keinen Strukturblock')
            eingang = self._eingang.get(id(ref.ziel), 0)
            if eingang > 1:
                alt = ref.ziel
                neu = copy.copy(alt)
                neu.felder = [pkg.Feld(copy.copy(f.name), copy.copy(f.wert), f.hash)
                              for f in alt.felder]
                neu.werte = [copy.copy(r) for r in alt.werte]
                neu.davor = b''
                p.bloecke.append(neu)
                ref.ziel = neu
                self._eingang[id(alt)] -= 1
                self._eingang[id(neu)] = 1
                for r in neu.verweise():
                    if r.typ == 0x80:
                        k = id(r.ziel)
                        self._eingang[k] = self._eingang.get(k, 0)+1
        return ref.ziel

    def _wert(self, ref, wert):
        p = self.paket
        alt = p.wert(ref)
        if alt == wert:
            return ref
        t = p.tabelle(ref)
        if t.kennung == b'!sbi':
            raw = str(wert).encode('utf-8', 'surrogateescape')
            if len(raw) > 255:
                raise KiFormatError('KI-String länger als 255 Byte')
            raw = bytes([len(raw)]) + raw + b'\0'
        elif t.datentyp in pkg.FORMATE:
            werte = wert if isinstance(wert, (list, tuple)) else [wert]
            if any(isinstance(x, float) and not math.isfinite(x) for x in werte):
                raise KiFormatError('Nichtendlicher KI-Wert')
            raw = struct.pack('<'+pkg.FORMATE[t.datentyp], *werte)
        else:
            raise KiFormatError('Änderung unbekannter KI-Wertetypen abgewiesen')
        return p.neuer_wert(ref, raw)

    def setzen(self, pfad, wert):
        """Skalar oder Zahlenliste ändern; gemeinsame Tabellenwerte bleiben intakt."""
        if not pfad:
            raise KiFormatError('Leerer KI-Feldpfad')
        block = self._block(pfad[:-1])
        if block.kennung == b'!idi':
            feld = self.paket.feld(block, pfad[-1])
            ref = feld.wert
        else:
            feld = None
            ref = block.werte[pfad[-1]]
        if isinstance(wert, (list, tuple)):
            ziel = self._block(pfad)
            if ziel.kennung != b'!ili':
                raise KiFormatError('Liste erwartet')
            if wert and not ziel.werte:
                raise KiFormatError('Leere Liste besitzt keinen typisierten Musterwert')
            alt = ziel.werte[:]
            ziel.werte = [self._wert(alt[min(i, len(alt)-1)], v)
                          for i, v in enumerate(wert)]
        else:
            neu = self._wert(ref, wert)
            if feld is not None:
                feld.wert = neu
            else:
                block.werte[pfad[-1]] = neu


def lesen(daten, typ=None):
    daten = bytes(daten)
    if daten.startswith(b'!pkg'):
        p = pkg.lesen(daten)
        erkannt = KENNUNGEN.get(p.kennung)
        if erkannt is None or typ is not None and erkannt != typ:
            raise KiFormatError('pkg-Kennung passt nicht zum KI-Typ')
        return KiDaten(erkannt, paket=p)
    if bxml.is_bxml(daten):
        d = bxml.decode(daten)
        if d.root.name != 'brake_line_settings' or typ not in (None, 'AIBrakeSettings'):
            raise KiFormatError('Kein KI-Bremssettings-Dokument')
        return KiDaten('AIBrakeSettings', dokument=d)
    if typ not in (None, 'VPTimes'):
        raise KiFormatError('Keine Daten des verlangten KI-Typs')
    return KiDaten('VPTimes', vp_tabellen=vp_lesen(daten))


def schreiben(daten):
    if daten.paket is not None:
        if KENNUNGEN.get(daten.paket.kennung) != daten.typ:
            raise KiFormatError('Inkonsistenter KI-pkg-Typ')
        return pkg.schreiben(daten.paket)
    if daten.dokument is not None:
        return bxml.encode(daten.dokument)
    return vp_schreiben(daten.vp_tabellen)


def ressourcen_lesen(ressource):
    """Ein Hauptfragment und optionales TSMD prüfen, unbekannte Zusätze abweisen."""
    if ressource.resource_type not in TYPEN:
        raise KiFormatError('Unbekannter KI-Ressourcentyp')
    return fragmente_lesen(ressource.resource_type, [fr.read() for fr in ressource.fragments])


def fragmente_lesen(typ, fragmente):
    """Wie ressourcen_lesen, aus entpackten Fragmentdaten (z. B. in einem Arbeitsprozess)."""
    if typ not in TYPEN:
        raise KiFormatError('Unbekannter KI-Ressourcentyp')
    if not fragmente:
        raise KiFormatError('KI-Ressource ohne Fragmente')
    d = lesen(fragmente[0], typ)
    for x in fragmente[1:]:
        if d.dokument is None or x != bxml.compute_tsmd(d.dokument):
            raise KiFormatError('Unbekanntes oder inkonsistentes KI-Zusatzfragment')
    return d


def ressourcen_schreiben(ressource, daten):
    """Serialisieren, TSMD neu berechnen; ERP-MD5 bei Änderungen aktualisieren."""
    if ressource.resource_type != daten.typ:
        raise KiFormatError('Ressource und KI-Modell haben verschiedene Typen')
    ausgabe = [schreiben(daten)]
    if len(ressource.fragments) > 1:
        if daten.dokument is None or len(ressource.fragments) != 2:
            raise KiFormatError('Unbekannte KI-Zusatzfragmente')
        ausgabe.append(bxml.compute_tsmd(daten.dokument))
    geaendert = False
    for i, raw in enumerate(ausgabe):
        if raw != ressource.fragments[i].read():
            ressource.set_fragment_data(i, raw)
            geaendert = True
    return geaendert
