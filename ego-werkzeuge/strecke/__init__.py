"""Streckengenerator (AP6): aus einer Mittellinie Geometrie und Streckenlogik erzeugen.

Das Paket ist bewusst unabhängig von den Spielformaten. Es erzeugt neutrale Ausgaben
(OBJ, NPZ, JSON, SVG, PNG), die später in die Formate der EGO-Engine übertragen werden.

Aufbau:
    definition.py   Streckendefinition (JSON), Segmente, Kontrollpunkte, CSV-Leser
    mittellinie.py  periodischer Spline, Abtastung, Krümmung, Prüfungen, Kurvenerkennung
    querprofil.py   Randsteine, Grünstreifen, Kiesbetten, Barrieren, Boxengasse (Querschnitt)
    rennlinie.py    Ideallinie (minimale Krümmung) und Geschwindigkeitsprofil (Punktmasse)
    logik.py        Streckenlogik (Sektoren, Startaufstellung, DRS, Boxengasse, Grenzen)
    geometrie.py    Dreiecksnetz mit Oberflächenklassen, Export OBJ/NPZ/JSON
    vorschau.py     Draufsicht als SVG und PNG
    erzeugung.py    Ablauf: Definition -> alle Ausgaben

Koordinaten intern: Meter, y oben, Fahrbahn in der x/z-Ebene, rechtshändig
(Draufsicht: x nach rechts, z nach unten). Die Ausgabe ist umstellbar (siehe definition.py).
"""

VERSION = "1.0"

from .definition import Streckendefinition, aus_dict, laden  # noqa: E402,F401
from .erzeugung import berechnen, erzeugen  # noqa: E402,F401
