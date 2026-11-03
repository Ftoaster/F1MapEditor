"""Bildvorlage und Werkzeug „Nachzeichnen“: Kartenausschnitt als Unterlage, Stützpunkte, Wegsuche im Hintergrund.

Bedienkonzept: ``docs/mapeditor/bild_import.md`` (Abschnitt „Stützpunkte abfragen“). Der Editor fragt nicht mit
Dialogen, sondern auf der Karte: Klicks setzen Stützpunkte in Fahrtrichtung, zwischen ihnen sucht ``nachzeichnen`` den
billigsten Weg entlang der Fahrbahn, Problemstellen erscheinen als rote Ringe, ein mehrdeutiger Abschnitt zeigt einen
zweiten Weg „B“ zum Anklicken. Übernommen wird nur eine gültige Runde (vollständig, ohne Überschneidung, nichts doppelt).

Daten: ``manifest["bildvorlage"]`` hält Lage (``x``/``z`` = linke obere Ecke in Metern), Maßstab (``m_pro_px`` je
Originalpixel), Deckkraft, Erkennungsart, Soll-Länge und die Stützpunkte (Originalpixel) – Teil des Rückgängig-Verlaufs.
Die Bilddaten liegen als ``bildvorlage.png`` im Projektordner (``Project.bildvorlage_daten``).

Rechnen: genau ein Hintergrundauftrag zur Zeit (eigener Thread, NumPy gibt die Sperre oft frei); das Ergebnis kommt als
Qt-Signal zurück in den Oberflächenthread. Veraltete Aufträge (neues Bild, neue Erkennung) werden verworfen.
"""

from __future__ import annotations

import io
import math
import threading

import numpy as np
from PySide6.QtCore import QBuffer, QIODevice, QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSlider,
                               QVBoxLayout, QWidget)

from . import nachzeichnen as nz
from .i18n import N_, tr
from .ux import zahl

SCHLUESSEL = "bildvorlage"
DATEI = "bildvorlage.png"
#: Trefferradius (Ansichtspixel) für Stützpunkte und den zweiten Weg
TREFFER_PX = 10
#: Stützpunkte, die aus einer Ein-Klick-Runde entstehen
EIN_KLICK_PUNKTE = 10
MODI = {"linie": N_("Kartenansicht (Linien)"), "farbe": N_("Luftbild (Farbe)")}
FARBE_WEG, FARBE_GUELTIG, FARBE_B, FARBE_FEHLER, FARBE_MESSEN = "#FF4FD8", "#5BD08A", "#FFB347", "#E5484D", "#FFD184"


# =============================================================================== Daten
def bild_png(daten):
    """Beliebige Bilddatei (Bytes) → PNG-Bytes ohne Alphakanal; ``ValueError`` bei unlesbaren Daten."""
    from PIL import Image, UnidentifiedImageError
    try:
        bild = Image.open(io.BytesIO(daten))
        bild.load()
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError(tr("Bild nicht lesbar: {error}", error=error)) from None
    if bild.width < 50 or bild.height < 50:
        raise ValueError(tr("Bild zu klein ({breite} × {hoehe} Pixel).", breite=bild.width, hoehe=bild.height))
    puffer = io.BytesIO()
    bild.convert("RGB").save(puffer, "PNG")
    return puffer.getvalue(), bild.size


def qimage_png(image):
    """QImage (Zwischenablage) → PNG-Bytes."""
    puffer = QBuffer()
    puffer.open(QIODevice.WriteOnly)
    image.save(puffer, "PNG")
    return bytes(puffer.data())


def neue_vorlage(breite, hoehe, m_pro_px=1.0):
    """Manifesteintrag einer neuen Vorlage: Bildmitte im Ursprung, Maßstab ungeprüft."""
    return {"datei": DATEI, "breite_px": int(breite), "hoehe_px": int(hoehe), "m_pro_px": float(m_pro_px),
            "x": -breite * m_pro_px / 2.0, "z": -hoehe * m_pro_px / 2.0, "deckkraft": 0.6, "modus": "linie",
            "massstab_geprueft": False, "laenge_soll_m": None, "punkte": [], "geschlossen": False}


def szene(vorlage, u, w):
    m = vorlage["m_pro_px"]
    return vorlage["x"] + u * m, vorlage["z"] + w * m


def bildpunkt(vorlage, x, z):
    m = vorlage["m_pro_px"]
    return (x - vorlage["x"]) / m, (z - vorlage["z"]) / m


def massstab_setzen(vorlage, m_pro_px, geprueft):
    """Neuer Maßstab um die Bildmitte (die Strecke bleibt im Spielbereich zentriert)."""
    mitte = szene(vorlage, vorlage["breite_px"] / 2.0, vorlage["hoehe_px"] / 2.0)
    vorlage["m_pro_px"] = float(m_pro_px)
    vorlage["x"] = mitte[0] - vorlage["breite_px"] * m_pro_px / 2.0
    vorlage["z"] = mitte[1] - vorlage["hoehe_px"] * m_pro_px / 2.0
    vorlage["massstab_geprueft"] = bool(geprueft)


# =============================================================================== Werkzeug
class Nachzeichner(QObject):
    """Zustand und Rechnen des Werkzeugs; die Karte ruft ``druecken``/``bewegen``/``loslassen``/``taste``/``zeichnen``."""

    geaendert = Signal()
    massstab_gemessen = Signal(float)          # Abstand der beiden Klicks in Originalpixeln
    _fertig = Signal(object)

    def __init__(self, document, parent=None):
        super().__init__(parent)
        self.document = document
        self._daten = None
        self.rgb = self.faktor = self.pixmap = None
        self.kb = None
        self._kb_key = None
        self.wege, self.alternativen = {}, {}
        self.pruefung = None                    # (Schlüssel, Linie, Ergebnis)
        self.vorschau = None                    # (Schlüssel, Pfad)
        self._vorschau_soll = None
        self.fehler = ""
        self._ein_klick = False
        self._job = None
        self._gen = 0
        self._geschlossen = False
        self.messen = None                      # None oder Liste der Messpunkte (Originalpixel)
        self.drag = None                        # {"index", "pos": (x, z), "start": Ansichtspunkt}
        self.hover = None                       # Szenenpunkt unter der Maus (Werkzeug aktiv)
        self.aktiv = False                      # Werkzeug in der Karte gewählt
        self._b_treffer = []                    # [(Paarindex, [Ansichtspunkte], Pfad)] des letzten Bildes
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(120)
        self._hover_timer.timeout.connect(self._vorschau_anfordern)
        self._fertig.connect(self._ergebnis)

    def beenden(self):
        """Laufende Rechnung verwerfen (Fenster schließt); der Thread endet beim nächsten Abbruchtest."""
        self._geschlossen = True
        self._gen += 1

    # ------------------------------------------------------------------ Zustand
    @property
    def vorlage(self):
        v = self.document.project.manifest.get(SCHLUESSEL)
        return v if isinstance(v, dict) else None

    @property
    def bereit(self):
        return self.vorlage is not None and self.rgb is not None

    @property
    def punkte(self):
        v = self.vorlage
        return [tuple(p) for p in v.get("punkte", [])] if v else []

    @property
    def geschlossen(self):
        v = self.vorlage
        return bool(v and v.get("geschlossen")) and len(self.punkte) >= 3

    @property
    def rechnet(self):
        return self._job is not None

    def _arbeits(self, p):
        return p[0] * self.faktor, p[1] * self.faktor

    def _szene_aus_arbeits(self, q):
        return szene(self.vorlage, q[0] / self.faktor, q[1] / self.faktor)

    def eingerastet(self, i):
        q = self._arbeits(self.punkte[i])
        return nz.einrasten(self.kb, q) if self.kb is not None else (int(round(q[0])), int(round(q[1])))

    def paare(self):
        n = len(self.punkte)
        paare = [(i, i + 1) for i in range(n - 1)]
        if self.geschlossen:
            paare.append((n - 1, 0))
        return paare

    def _paar_key(self, i, j):
        return self.eingerastet(i), self.eingerastet(j)

    def _soll_kb(self):
        v = self.vorlage
        if not v or not self.punkte:
            return None
        q = self._arbeits(self.punkte[0])
        return v.get("modus", "linie"), int(round(q[0])), int(round(q[1]))

    def _soll_laenge(self):
        """Soll-Länge für die Vollständigkeitsprüfung: nur mit gemessenem Maßstab (sonst wäre die Länge geraten)."""
        v = self.vorlage
        return v.get("laenge_soll_m") if v.get("massstab_geprueft") else None

    def _pruef_key(self):
        v = self.vorlage
        return (tuple(self._paar_key(i, j) for i, j in self.paare()), v.get("m_pro_px"), self._soll_laenge())

    def sync(self):
        """Nach jeder Dokumentänderung: Bild neu laden, wenn es wechselte, dann fehlende Rechnungen planen."""
        daten = self.document.project.bildvorlage_daten if self.vorlage else None
        self.fehler = ""
        if daten is not self._daten:
            self._daten = daten
            self._gen += 1
            self.kb = self._kb_key = self.pruefung = self.vorschau = None
            self.wege, self.alternativen = {}, {}
            self.rgb = self.faktor = self.pixmap = None
            if daten:
                try:
                    self.rgb, self.faktor, _ = nz.bild_laden(daten)
                    self.pixmap = QPixmap.fromImage(QImage.fromData(daten))
                except (OSError, ValueError) as error:
                    self.fehler = tr("Bild nicht lesbar: {error}", error=error)
        if self._kb_key is not None and self._kb_key != self._soll_kb():
            self._gen += 1                       # Erkennung oder Start geändert: alles neu
            self.kb = self._kb_key = self.pruefung = self.vorschau = None
            self.wege, self.alternativen = {}, {}
        if self.drag is not None and self.drag["index"] >= len(self.punkte):
            self.drag = None
        self._planen()
        self.geaendert.emit()

    # ------------------------------------------------------------------ Hintergrundrechnung
    def _planen(self):
        """Nächsten fehlenden Auftrag starten (Reihenfolge: Kostenbild, Ein-Klick, Teilwege, Prüfung, Vorschau, B)."""
        if self._job is not None or not self.bereit or not self.punkte:
            return
        rgb, gen = self.rgb, self._gen
        soll = self._soll_kb()
        if self._kb_key != soll:
            start = self._arbeits(self.punkte[0])
            self._starten("kb", soll, lambda abbrechen: nz.kostenbild(rgb, start, soll[0]))
            return
        kb = self.kb
        if kb is None:
            return
        if self._ein_klick:
            self._ein_klick = False
            start = self._arbeits(self.punkte[0])
            self._starten("ein_klick", gen, lambda abbrechen: nz.runde_ein_klick(kb, start, abbrechen))
            return
        for i, j in self.paare():
            key = self._paar_key(i, j)
            if key not in self.wege:
                self._starten("weg", key, lambda abbrechen, key=key: nz.teilweg(kb, key[0], key[1], abbrechen))
                return
        if self.geschlossen and all(self.wege.get(self._paar_key(i, j)) for i, j in self.paare()):
            key = self._pruef_key()
            if self.pruefung is None or self.pruefung[0] != key:
                pfade = [self.wege[self._paar_key(i, j)][0] for i, j in self.paare()]
                m = self.vorlage["m_pro_px"] / self.faktor
                soll = self._soll_laenge()

                def pruefen(abbrechen):
                    linie = nz.mittellinie(np.vstack([p[:-1] if len(p) > 1 else p for p in pfade]), kb.halbbreite)
                    return linie, nz.pruefen(linie, kb.halbbreite, m, soll)
                self._starten("pruefen", key, pruefen)
                return
        if self._vorschau_soll is not None and (self.vorschau is None or self.vorschau[0] != self._vorschau_soll):
            key = self._vorschau_soll
            self._starten("vorschau", key, lambda abbrechen: nz.teilweg(kb, key[0], key[1], abbrechen))
            return
        for i, j in self.paare():
            key = self._paar_key(i, j)
            weg = self.wege.get(key)
            if weg and key not in self.alternativen:
                self._starten("alt", key, lambda abbrechen, key=key, weg=weg:
                              nz.alternative(kb, key[0], key[1], weg[0], weg[1], abbrechen))
                return

    def _starten(self, art, key, rechnung):
        gen = self._gen
        self._job = (art, key)

        def abbrechen():
            return (self._geschlossen or gen != self._gen
                    or (art == "vorschau" and key != self._vorschau_soll))

        def lauf():
            try:
                ergebnis = rechnung(abbrechen)
            except nz.Abbruch:
                ergebnis = None
            except Exception as error:          # Fehler gehören in den Oberflächenthread
                ergebnis = error
            if not self._geschlossen:
                try:
                    self._fertig.emit((gen, art, key, ergebnis))
                except RuntimeError:            # Fenster samt Werkzeug schon gelöscht
                    pass
        threading.Thread(target=lauf, daemon=True, name="nachzeichnen-" + art).start()
        self.geaendert.emit()

    def _ergebnis(self, paket):
        """Ergebnis eines Auftrags übernehmen. Fehlschläge werden ebenfalls gespeichert (``None``), damit derselbe
        Auftrag nicht endlos neu startet; abgebrochene (veraltete) Aufträge werden verworfen."""
        gen, art, key, ergebnis = paket
        self._job = None
        abgebrochen = ergebnis is None and art != "alt"
        if gen == self._gen and not abgebrochen:
            fehler = isinstance(ergebnis, Exception)
            if art == "kb":
                self.kb, self._kb_key = (None if fehler else ergebnis), key
                self.wege, self.alternativen, self.pruefung, self.vorschau = {}, {}, None, None
                if fehler:
                    self.fehler = tr("Bild nicht auswertbar: {error}", error=ergebnis)
            elif art == "weg":
                self.wege[key] = None if fehler else ergebnis
            elif art == "alt":
                self.alternativen[key] = None if fehler else ergebnis
            elif art == "pruefen":
                self.pruefung = (key, None, None) if fehler else (key, ergebnis[0], ergebnis[1])
                if fehler:
                    self.fehler = tr("Prüfung fehlgeschlagen: {error}", error=ergebnis)
            elif art == "vorschau":
                self.vorschau = (key, None if fehler else ergebnis[0])
            elif art == "ein_klick":
                if fehler:
                    self.fehler = tr("Keine Runde gefunden – bitte weitere Stützpunkte in Fahrtrichtung setzen")
                else:
                    self._ein_klick_uebernehmen(ergebnis)
        self.geaendert.emit()
        self._planen()

    def _ein_klick_uebernehmen(self, pfad):
        punkte = nz.gleichmaessig(pfad, EIN_KLICK_PUNKTE)
        start = int(np.argmin([math.dist(p, self._arbeits(self.punkte[0])) for p in punkte]))
        punkte = punkte[start:] + punkte[:start]

        def setzen(v):
            v["punkte"] = [[round(p[0] / self.faktor, 2), round(p[1] / self.faktor, 2)] for p in punkte]
            v["geschlossen"] = True
        self.document.edit_bild(tr("Runde automatisch suchen"), setzen)

    # ------------------------------------------------------------------ Befehle (je ein Undo-Schritt)
    def anhaengen(self, u, w):
        self.document.edit_bild(tr("Stützpunkt setzen"), lambda v: v.setdefault("punkte", []).append([round(u, 2), round(w, 2)]))

    def einfuegen(self, index, u, w):
        self.document.edit_bild(tr("Stützpunkt einfügen"), lambda v: v["punkte"].insert(index, [round(u, 2), round(w, 2)]))

    def verschieben(self, index, u, w):
        def change(v):
            v["punkte"][index] = [round(u, 2), round(w, 2)]
        self.document.edit_bild(tr("Stützpunkt verschieben"), change)

    def loeschen(self, index=None):
        if not self.punkte:
            return

        def change(v):
            v["punkte"].pop(len(v["punkte"]) - 1 if index is None else index)
            if len(v["punkte"]) < 3:
                v["geschlossen"] = False
        self.document.edit_bild(tr("Stützpunkt löschen"), change)

    def alle_loeschen(self):
        self.document.edit_bild(tr("Alle Stützpunkte löschen"), lambda v: v.update(punkte=[], geschlossen=False))

    def schliessen(self):
        if len(self.punkte) >= 3 and not self.geschlossen:
            self.document.edit_bild(tr("Runde schließen"), lambda v: v.update(geschlossen=True))

    def ein_klick(self):
        """Ganze Runde vom ersten Stützpunkt aus suchen (ersetzt die Punkte durch Punkte entlang der Runde)."""
        if len(self.punkte) == 1:
            self._ein_klick = True
            self._planen()
            self.geaendert.emit()

    def einstellen(self, text, **werte):
        def change(v):
            v.update(werte)
        self.document.edit_bild(text, change)

    def massstab(self, m_pro_px, geprueft):
        self.document.edit_bild(tr("Maßstab festlegen"), lambda v: massstab_setzen(v, m_pro_px, geprueft))

    def messen_starten(self):
        self.messen = []
        self.geaendert.emit()

    # ------------------------------------------------------------------ Ergebnis
    @property
    def linie(self):
        """Geglättete Runde (Arbeitsbild-Pixel), wenn die Prüfung zum aktuellen Stand gehört, sonst None."""
        if self.pruefung is not None and self.geschlossen and self.pruefung[0] == self._pruef_key():
            return self.pruefung[1]
        return None

    @property
    def befund(self):
        return self.pruefung[2] if self.linie is not None else None

    def ohne_weg(self):
        """Anzahl der Abschnitte, zwischen deren Stützpunkten kein Weg gefunden wurde."""
        return sum(1 for i, j in self.paare() if self._paar_key(i, j) in self.wege and self.wege[self._paar_key(i, j)] is None)

    def gueltig(self):
        befund = self.befund
        return befund is not None and not befund["fehler"]

    def kontrollpunkte(self):
        v = self.vorlage
        return nz.kontrollpunkte(self.linie, self.faktor, v["m_pro_px"], v["x"], v["z"])

    def laenge_aus_soll(self):
        """m/px so, dass die erkannte Runde die Soll-Länge hat (Vollständigkeit dann nicht prüfbar)."""
        v = self.vorlage
        if self.linie is None or not v.get("laenge_soll_m"):
            return None
        return float(v["laenge_soll_m"]) / (nz.ring_laenge(self.linie) / self.faktor)

    # ------------------------------------------------------------------ Karte: Eingaben
    def _treffer_punkt(self, ansicht, to_view):
        best, best_d = None, TREFFER_PX
        for i in range(len(self.punkte)):
            x, y = to_view(*self._szene_aus_arbeits(self.eingerastet(i)))
            d = math.hypot(x - ansicht.x(), y - ansicht.y())
            if d <= best_d:
                best, best_d = i, d
        return best

    def _treffer_b(self, ansicht):
        for index, punkte, pfad in self._b_treffer:
            for (ax, ay), (bx, by) in zip(punkte, punkte[1:]):
                dx, dy = bx - ax, by - ay
                t = max(0.0, min(1.0, ((ansicht.x() - ax) * dx + (ansicht.y() - ay) * dy) / max(dx * dx + dy * dy, 1e-9)))
                if math.hypot(ansicht.x() - ax - t * dx, ansicht.y() - ay - t * dy) <= TREFFER_PX * 0.7:
                    return index, pfad
        return None

    def druecken(self, x, z, ansicht, to_view):
        v = self.vorlage
        if v is None:
            return
        u, w = bildpunkt(v, x, z)
        if self.messen is not None:
            self.messen.append((u, w))
            if len(self.messen) == 2:
                (a, b), self.messen = self.messen, None
                abstand = math.dist(a, b)
                if abstand > 2:
                    self.massstab_gemessen.emit(abstand)
            self.geaendert.emit()
            return
        if not self.bereit:
            return
        i = self._treffer_punkt(ansicht, to_view)
        if i is not None:
            if i == 0 and not self.geschlossen and len(self.punkte) >= 3:
                self.schliessen()
                return
            self.drag = {"index": i, "pos": (x, z), "start": QPointF(ansicht)}
            return
        treffer = self._treffer_b(ansicht)
        if treffer is not None:
            index, pfad = treffer
            mitte = pfad[len(pfad) // 2]                  # Punkt auf dem zweiten Weg: der Weg führt dann darüber
            self.einfuegen(index + 1, mitte[0] / self.faktor, mitte[1] / self.faktor)
            return
        if self.geschlossen:
            self.einfuegen(self._naechstes_paar(u, w) + 1, u, w)
        else:
            self.anhaengen(u, w)

    def _naechstes_paar(self, u, w):
        """Index des Stützpunkts, hinter dem ein Punkt (Originalpixel) einzufügen ist: Paar mit dem nächsten Weg."""
        q = np.array(self._arbeits((u, w)))
        best, best_d = len(self.punkte) - 1, float("inf")
        for i, j in self.paare():
            weg = self.wege.get(self._paar_key(i, j))
            pfad = weg[0] if weg else np.array([self._arbeits(self.punkte[i]), self._arbeits(self.punkte[j])])
            d = float(np.min(np.hypot(*(pfad - q).T)))
            if d < best_d:
                best, best_d = i, d
        return best

    def bewegen(self, x, z, ansicht):
        if self.drag is not None:
            self.drag["pos"] = (x, z)
        else:
            self.hover = (x, z)
            if self.bereit and self.punkte and not self.geschlossen and self.messen is None:
                self._hover_timer.start()
        self.geaendert.emit()

    def _vorschau_anfordern(self):
        if self.hover is None or not self.punkte or self.geschlossen or self.kb is None:
            self._vorschau_soll = None
            return
        q = self._arbeits(bildpunkt(self.vorlage, *self.hover))
        h, w = self.kb.form
        if not (0 <= q[0] < w and 0 <= q[1] < h):
            self._vorschau_soll = None
            return
        self._vorschau_soll = (self.eingerastet(len(self.punkte) - 1), nz.einrasten(self.kb, q))
        self._planen()

    def loslassen(self, x, z, ansicht):
        drag, self.drag = self.drag, None
        if drag is not None and (ansicht - drag["start"]).manhattanLength() > 3:
            self.verschieben(drag["index"], *bildpunkt(self.vorlage, x, z))
        self.geaendert.emit()

    def verlassen(self):
        self.hover = self._vorschau_soll = self.vorschau = None
        self.geaendert.emit()

    def taste(self, key):
        """Tasten im Werkzeug; True, wenn verbraucht (Esc ohne laufende Geste verlässt das Werkzeug)."""
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self.schliessen()
            return True
        if key == Qt.Key_Backspace:
            self.loeschen()
            return True
        if key == Qt.Key_Delete:
            self.loeschen()
            return True
        if key == Qt.Key_Escape and (self.messen is not None or self.drag is not None):
            self.messen = self.drag = None
            self.geaendert.emit()
            return True
        return False

    def hinweis(self):
        """Eine Zeile für das Hinweisschild der Karte."""
        if self.fehler:
            return self.fehler
        if self.vorlage is not None and not self.bereit:
            return tr("Das Bild der Vorlage fehlt im Projektordner ({datei}) · Projekt → „Bildvorlage laden …“",
                      datei=self.vorlage.get("datei", "bildvorlage.png"))
        if self.messen is not None:
            return (tr("Maßstab: Anfang der Maßstabsleiste anklicken (1/2) · Esc: abbrechen") if not self.messen else
                    tr("Maßstab: Ende der Maßstabsleiste anklicken (2/2) · Esc: abbrechen"))
        n = len(self.punkte)
        rechnet = tr(" · rechnet …") if self.rechnet else ""
        if self.ohne_weg():
            return tr("Zwischen zwei Stützpunkten wurde kein Weg gefunden – Punkt näher an die Fahrbahn ziehen") + rechnet
        if n == 0:
            text = tr("Klicke auf die Start-Ziel-Linie der Strecke (erster Stützpunkt)")
        elif n == 1:
            text = tr("Nächsten Punkt in Fahrtrichtung anklicken · rechts: „Runde automatisch suchen“")
        elif not self.geschlossen:
            text = tr("Nächsten Punkt in Fahrtrichtung anklicken · Klick auf den Start oder Enter schließt die Runde · "
                      "Rücktaste: letzten Punkt löschen")
        elif self.befund is not None and self.befund["fehler"]:
            text = tr("Rote Ringe: hier stimmt die Runde nicht – klicke auf die Fahrbahn, wo sie weitergeht")
        elif self.befund is not None:
            text = tr("Runde gültig · rechts „Als Strecke übernehmen“ · Punkte ziehen passt sie an")
        else:
            text = tr("Runde wird geprüft")
        if any(self.alternativen.get(self._paar_key(i, j)) is not None for i, j in self.paare()):
            text += tr(" · orange „B“: zweiter möglicher Weg, Klick darauf wählt ihn")
        return text + rechnet

    # ------------------------------------------------------------------ Karte: Zeichnen
    def zeichne_bild(self, painter):
        """Unterlage in Szenenkoordinaten (vom Hintergrund der Karte aufgerufen)."""
        v = self.vorlage
        if v is None or self.pixmap is None:
            return
        painter.save()
        painter.setOpacity(float(v.get("deckkraft", 0.6)))
        m = v["m_pro_px"]
        painter.drawPixmap(QRectF(v["x"], v["z"], v["breite_px"] * m, v["hoehe_px"] * m), self.pixmap,
                           QRectF(0, 0, self.pixmap.width(), self.pixmap.height()))
        painter.restore()

    def _ansicht(self, pfad, to_view, schritt=2):
        return [to_view(*self._szene_aus_arbeits(q)) for q in pfad[::schritt]] + [to_view(*self._szene_aus_arbeits(pfad[-1]))]

    def zeichne(self, painter, to_view):
        """Wege, zweiter Weg, Vorschau, Stützpunkte, Problemstellen und Messlinie (Ansichtskoordinaten)."""
        v = self.vorlage
        self._b_treffer = []
        if v is None or not self.bereit:
            return

        def linie(punkte, farbe, breite, stil=Qt.SolidLine):
            pen = QPen(QColor(farbe), breite, stil)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPolyline(QPolygonF([QPointF(*p) for p in punkte]))

        gueltig = self.gueltig()
        if self.linie is not None:
            linie(self._ansicht(np.vstack([self.linie, self.linie[:1]]), to_view), FARBE_GUELTIG if gueltig else FARBE_WEG, 2.5)
        else:
            for i, j in self.paare():
                weg = self.wege.get(self._paar_key(i, j))
                if weg:
                    linie(self._ansicht(weg[0], to_view), FARBE_WEG, 2.5)
        for index, (i, j) in enumerate(self.paare()):
            alt = self.alternativen.get(self._paar_key(i, j))
            if alt is not None:
                punkte = self._ansicht(alt, to_view)
                linie(punkte, FARBE_B, 2, Qt.DashLine)
                mitte = QPointF(*punkte[len(punkte) // 2])
                painter.setBrush(QColor(FARBE_B))
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(mitte, 9, 9)
                painter.setPen(QColor("#101820"))
                painter.drawText(QRectF(mitte.x() - 9, mitte.y() - 9, 18, 18), Qt.AlignCenter, "B")
                self._b_treffer.append((i, punkte, alt))
        if self.vorschau is not None and self.vorschau[1] is not None and self.vorschau[0] == self._vorschau_soll                 and not self.geschlossen:
            linie(self._ansicht(self.vorschau[1], to_view), FARBE_WEG, 1.5, Qt.DashLine)
        befund = self.befund
        if befund is not None:
            painter.setPen(QPen(QColor(FARBE_FEHLER), 3))
            painter.setBrush(Qt.NoBrush)
            for fehler in befund["fehler"]:
                for ort in fehler.get("orte", []):
                    painter.drawEllipse(QPointF(*to_view(*self._szene_aus_arbeits(ort))), 14, 14)
        for i in range(len(self.punkte)):
            if self.drag is not None and self.drag["index"] == i:
                x, y = to_view(*self.drag["pos"])
            else:
                x, y = to_view(*self._szene_aus_arbeits(self.eingerastet(i)))
            erster = i == 0
            painter.setPen(QPen(QColor("#F1F5F9"), 2))
            painter.setBrush(QColor(FARBE_GUELTIG if erster else "#005C73"))
            painter.drawEllipse(QPointF(x, y), 7 if erster else 5, 7 if erster else 5)
            if erster:
                painter.setPen(QColor("#F1F5F9"))
                painter.drawText(QPointF(x + 10, y - 8), tr("Start") if self.geschlossen or len(self.punkte) < 3
                                 else tr("Start · Klick schließt"))
        if self.messen:
            painter.setPen(QPen(QColor(FARBE_MESSEN), 2))
            a = to_view(*szene(v, *self.messen[0]))
            b = to_view(*self.hover) if self.hover else a
            painter.drawLine(QPointF(*a), QPointF(*b))
            painter.drawEllipse(QPointF(*a), 4, 4)


# =============================================================================== Seitenleiste
class NachzeichnenPanel(QWidget):
    """Einstellungen und Prüfliste des Werkzeugs (rechts); Befehle gehen an den ``Nachzeichner``."""

    uebernehmen = Signal()
    entfernen = Signal()

    def __init__(self, werkzeug, parent=None):
        super().__init__(parent)
        self.werkzeug = werkzeug
        self._laden = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        self.info = QLabel()
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        grid = QGridLayout()
        grid.addWidget(QLabel(tr("Deckkraft")), 0, 0)
        self.deckkraft = QSlider(Qt.Horizontal)
        self.deckkraft.setRange(10, 100)
        self.deckkraft.setToolTip(tr("Wie stark das Bild unter der Strecke zu sehen ist"))
        self.deckkraft.sliderReleased.connect(self._deckkraft)
        grid.addWidget(self.deckkraft, 0, 1)
        grid.addWidget(QLabel(tr("Bildart")), 1, 0)
        self.modus = QComboBox()
        for key, text in MODI.items():
            self.modus.addItem(tr(text), key)
        self.modus.setToolTip(tr("Kartenansicht: Strecke als gezeichnete Linie (empfohlen). Luftbild: Fahrbahn nach "
                                 "ihrer Farbe am ersten Stützpunkt."))
        self.modus.activated.connect(self._modus)
        grid.addWidget(self.modus, 1, 1)
        grid.addWidget(QLabel(tr("Meter je Pixel")), 2, 0)
        self.m_pro_px = QDoubleSpinBox()
        self.m_pro_px.setDecimals(4)
        self.m_pro_px.setRange(0.01, 100.0)
        self.m_pro_px.setToolTip(tr("Maßstab des Bilds. Am sichersten über „Maßstab messen“ an der Maßstabsleiste."))
        self.m_pro_px.editingFinished.connect(self._m_pro_px)
        grid.addWidget(self.m_pro_px, 2, 1)
        grid.addWidget(QLabel(tr("Soll-Länge")), 3, 0)
        self.laenge = QDoubleSpinBox()
        self.laenge.setDecimals(0)
        self.laenge.setRange(0, 30000)
        self.laenge.setSuffix(" m")
        self.laenge.setSpecialValueText(tr("unbekannt"))
        self.laenge.setToolTip(tr("Offizielle Streckenlänge. Mit gemessenem Maßstab prüft der Editor damit, ob die Runde "
                                  "vollständig ist."))
        self.laenge.editingFinished.connect(self._laenge)
        grid.addWidget(self.laenge, 3, 1)
        layout.addLayout(grid)
        row = QHBoxLayout()
        self.messen = QPushButton(tr("Maßstab messen"))
        self.messen.setToolTip(tr("Zwei Klicks auf Anfang und Ende der Maßstabsleiste im Bild, dann ihre Länge eingeben"))
        self.messen.clicked.connect(werkzeug.messen_starten)
        self.aus_soll = QPushButton(tr("Maßstab aus Soll-Länge"))
        self.aus_soll.setToolTip(tr("Maßstab so, dass die erkannte Runde die Soll-Länge hat. Ob die Runde vollständig ist, "
                                    "lässt sich dann nicht mehr prüfen."))
        self.aus_soll.clicked.connect(self._aus_soll)
        row.addWidget(self.messen)
        row.addWidget(self.aus_soll)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.automatisch = QPushButton(tr("Runde automatisch suchen"))
        self.automatisch.setToolTip(tr("Vom ersten Stützpunkt aus die ganze Runde suchen (klappt bei klaren Kartenbildern)"))
        self.automatisch.clicked.connect(werkzeug.ein_klick)
        self.schliessen = QPushButton(tr("Runde schließen"))
        self.schliessen.clicked.connect(werkzeug.schliessen)
        row.addWidget(self.automatisch)
        row.addWidget(self.schliessen)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.letzter = QPushButton(tr("Letzten Punkt löschen"))
        self.letzter.clicked.connect(lambda: werkzeug.loeschen())
        self.alle = QPushButton(tr("Alle Punkte löschen"))
        self.alle.clicked.connect(werkzeug.alle_loeschen)
        row.addWidget(self.letzter)
        row.addWidget(self.alle)
        layout.addLayout(row)
        self.pruefliste = QLabel()
        self.pruefliste.setTextFormat(Qt.RichText)
        self.pruefliste.setWordWrap(True)
        layout.addWidget(self.pruefliste)
        self.uebernehmen_button = QPushButton(tr("Als Strecke übernehmen"))
        self.uebernehmen_button.setObjectName("primary")
        self.uebernehmen_button.clicked.connect(self.uebernehmen)
        layout.addWidget(self.uebernehmen_button)
        self.entfernen_button = QPushButton(tr("Bildvorlage entfernen"))
        self.entfernen_button.clicked.connect(self.entfernen)
        layout.addWidget(self.entfernen_button)
        layout.addStretch(1)
        werkzeug.geaendert.connect(self.aktualisieren)
        self.aktualisieren()

    def _deckkraft(self):
        if not self._laden:
            self.werkzeug.einstellen(tr("Deckkraft der Bildvorlage"), deckkraft=self.deckkraft.value() / 100.0)

    def _modus(self):
        if not self._laden:
            self.werkzeug.einstellen(tr("Bildart der Vorlage"), modus=self.modus.currentData())

    def _m_pro_px(self):
        v = self.werkzeug.vorlage
        if not self._laden and v and abs(self.m_pro_px.value() - v["m_pro_px"]) > 1e-6:
            self.werkzeug.massstab(self.m_pro_px.value(), False)

    def _laenge(self):
        v = self.werkzeug.vorlage
        wert = self.laenge.value() or None
        if not self._laden and v and wert != v.get("laenge_soll_m"):
            self.werkzeug.einstellen(tr("Soll-Länge der Bildvorlage"), laenge_soll_m=wert)

    def _aus_soll(self):
        m = self.werkzeug.laenge_aus_soll()
        if m:
            self.werkzeug.massstab(m, False)

    def aktualisieren(self):
        w = self.werkzeug
        v = w.vorlage
        self.setEnabled(v is not None)
        if v is None:
            self.info.setText(tr("Keine Bildvorlage. Projekt → „Bildvorlage laden …“ oder ein Bildschirmfoto mit Strg+V "
                                 "einfügen."))
            self.pruefliste.setText("")
            return
        self._laden = True
        try:
            if not self.deckkraft.isSliderDown():
                self.deckkraft.setValue(int(round(float(v.get("deckkraft", 0.6)) * 100)))
            self.modus.setCurrentIndex(max(0, self.modus.findData(v.get("modus", "linie"))))
            if not self.m_pro_px.hasFocus():
                self.m_pro_px.setValue(float(v["m_pro_px"]))
            if not self.laenge.hasFocus():
                self.laenge.setValue(float(v.get("laenge_soll_m") or 0))
        finally:
            self._laden = False
        breite, hoehe, m = v["breite_px"], v["hoehe_px"], v["m_pro_px"]
        massstab = (tr("Maßstab gemessen") if v.get("massstab_geprueft") else tr("Maßstab ungeprüft"))
        self.info.setText(tr("Bild {breite} × {hoehe} Pixel = {mb} × {mh} · {massstab}", breite=breite, hoehe=hoehe,
                             mb=zahl(breite * m, 0, "m"), mh=zahl(hoehe * m, 0, "m"), massstab=massstab))
        n = len(w.punkte)
        self.automatisch.setEnabled(n == 1 and w.bereit)
        self.schliessen.setEnabled(n >= 3 and not w.geschlossen)
        self.letzter.setEnabled(n > 0)
        self.alle.setEnabled(n > 0)
        self.aus_soll.setEnabled(w.linie is not None and bool(v.get("laenge_soll_m")))
        self.pruefliste.setText(self._pruefliste())
        self.uebernehmen_button.setEnabled(w.gueltig())

    def _pruefliste(self):
        w = self.werkzeug
        v = w.vorlage
        befund = w.befund

        def zeile(ok, text):
            zeichen, farbe = {True: ("✓", FARBE_GUELTIG), False: ("✗", FARBE_FEHLER), None: ("–", "#8D9BA9")}[ok]
            return f"<span style='color:{farbe}'><b>{zeichen}</b> {text}</span>"
        regeln = {f["regel"] for f in befund["fehler"]} if befund else set()
        geprueft = befund is not None
        zeilen = [zeile(True if w.geschlossen else None, tr("Runde geschlossen")),
                  zeile(("ueberschneidung" not in regeln) if geprueft else None, tr("Keine Überschneidung")),
                  zeile(("doppelt" not in regeln) if geprueft else None, tr("Kein Stück doppelt befahren"))]
        soll = v.get("laenge_soll_m")
        if geprueft:
            laenge = zahl(befund["laenge_m"], 0, "m")
            if soll and v.get("massstab_geprueft"):
                zeilen.append(zeile("laenge" not in regeln, tr("Vollständig: Länge {laenge} (Soll {soll})", laenge=laenge,
                                                               soll=zahl(soll, 0, "m"))))
            else:
                zeilen.append(zeile(None, tr("Länge {laenge} · Vollständigkeit ungeprüft (Maßstab messen und Soll-Länge "
                                             "eintragen)", laenge=laenge)))
        else:
            zeilen.append(zeile(None, tr("Länge und Vollständigkeit")))
        if w.rechnet:
            zeilen.append("<span style='color:#8D9BA9'>" + tr("rechnet …") + "</span>")
        return "<br>".join(zeilen)
