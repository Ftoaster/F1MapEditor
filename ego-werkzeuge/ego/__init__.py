"""EGO-Werkzeuge: Lesen und Schreiben von Dateien der EGO-Engine (F1 25).

Enthalten:
    erp           ERP-Archive (Containerformat aller Spieldaten) lesen und schreiben
    compression   zstd-Kompression, wie sie in ERP-Archiven verwendet wird
    paket         Mod-Pakete des Track Installers lesen, prüfen und bauen
    bxml          Binär-XML (Streckenlogik, Kameras, KI-Bremslinien, VTF …) lesen und schreiben
    streckenlogik Auswertung und Änderung der Streckenlogik (KI-Gates, KI-Linie)
    pkg           pkg-Objektgraphen (Welt, Layer, Zuschauer, KI-Daten) lesen und schreiben
    trn           Fahrphysik-Oberfläche TRN~ (JPAK mit VCQTC-Kacheln) lesen und schreiben
    grafik        Grafikressourcen (Modelle, Meshes, Puffer, Materialien, Texturen, Terrain)

Formatbeschreibungen: docs/erp_format.md und docs/formate/*.md
"""

__version__ = "0.2.0"
