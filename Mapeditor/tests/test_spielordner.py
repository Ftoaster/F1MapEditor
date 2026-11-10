"""Spielordner von F1 25: Ordnerwahl erkennen, merken und an die Arbeitsprozesse weitergeben."""

import os

from mapeditor import spielordner


class Store(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):
        self[key] = value


def _ohne_env(monkeypatch):
    # erst setzen, dann löschen: so stellt monkeypatch den Ausgangszustand sicher wieder her
    monkeypatch.setenv(spielordner.ENV, "x")
    monkeypatch.delenv(spielordner.ENV)


def _spiel(tmp_path):
    spiel = tmp_path / "Bib" / "steamapps" / "common" / "F1 25"
    (spiel / spielordner.ERKENNUNG).mkdir(parents=True)
    return spiel


def test_normalisieren_findet_installation(tmp_path):
    spiel = _spiel(tmp_path)
    assert spielordner.normalisieren(spiel) == str(spiel)
    assert spielordner.normalisieren(tmp_path / "Bib") == str(spiel)                       # Bibliothek gewählt
    assert spielordner.normalisieren(tmp_path / "Bib" / "steamapps" / "common") == str(spiel)
    assert spielordner.normalisieren(spiel / "2025_asset_groups") == str(spiel)            # eine Ebene zu tief
    assert spielordner.normalisieren(tmp_path) is None
    assert spielordner.normalisieren("") is None


def test_merken_und_anwenden(tmp_path, monkeypatch):
    spiel = _spiel(tmp_path)
    _ohne_env(monkeypatch)
    store = Store()
    assert spielordner.anwenden(store) is None
    spielordner.merken(spiel, store)
    assert os.environ[spielordner.ENV] == str(spiel)
    monkeypatch.delenv(spielordner.ENV)
    assert spielordner.anwenden(store) == str(spiel)                                      # nächster Start
    assert os.environ[spielordner.ENV] == str(spiel)


def test_gesetzte_umgebungsvariable_bleibt(tmp_path, monkeypatch):
    spiel = _spiel(tmp_path)
    monkeypatch.setenv(spielordner.ENV, "X:\\eigen")
    assert spielordner.anwenden(Store({spielordner.SCHLUESSEL: str(spiel)})) == "X:\\eigen"


def test_ungueltig_gemerkter_ordner_wird_ignoriert(tmp_path, monkeypatch):
    _ohne_env(monkeypatch)
    assert spielordner.anwenden(Store({spielordner.SCHLUESSEL: str(tmp_path / "weg")})) is None
    assert spielordner.ENV not in os.environ


def test_fehlermeldung_erkannt():
    assert spielordner.ist_fehlt_meldung("Game folder not found (choose it under Tools …)")
    assert spielordner.ist_fehlt_meldung("Spielordner nicht gefunden (unter Werkzeuge …)")
    assert not spielordner.ist_fehlt_meldung("Catalogue broken")
