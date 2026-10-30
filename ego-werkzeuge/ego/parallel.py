"""Unabhängige Teilaufgaben auf mehrere Kerne verteilen (26.10.2026).

Der Werkzeugkasten rechnet in reinem Python; ein Prozess nutzt nur einen Kern. Hier laufen unabhängige
Teilaufgaben (KI-Ressourcen, Verweise je Ressource) in Arbeitsprozessen und zstd-Kompression in Threads
(``zstandard`` gibt dabei die GIL frei). Ergebnisse kommen immer in Auftragsreihenfolge zurück, die
Ausgabe bleibt bitgleich zum Lauf in einem Prozess.

Steuerung: Umgebungsvariable ``EGO_PROZESSE`` (Zahl; ``1`` = alles in einem Prozess). Standard: alle
logischen Kerne, höchstens 61 (Grenze von Windows für Prozess-Pools). ``EGO_PARALLEL_ZEITEN=1`` schreibt je
Aufruf Wandzeit, Summe und längsten Auftrag nach stderr (Messung, siehe docs/bau_laufzeit.md).

Arbeitsprozesse werden unter Windows neu gestartet. Sie führen das Hauptskript nicht erneut aus (beim Start ist
es verborgen, ``_hauptskript_verbergen``); die Arbeitsfunktionen stehen in Modulen. So laufen auch Skripte ohne
``if __name__ == "__main__":`` nicht 24-mal. Eingefrorene Programme (PyInstaller) rufen zu Beginn
``multiprocessing.freeze_support()`` auf.
"""

import contextlib
import functools
import importlib.machinery
import multiprocessing
import os
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool

WINDOWS_GRENZE = 61
_GEMEINSAM = None


def anzahl(auftraege=None) -> int:
    """Zahl der Arbeiter: ``EGO_PROZESSE`` bzw. alle Kerne, nie mehr als Aufträge."""
    try:
        n = int(os.environ.get("EGO_PROZESSE") or 0) or (os.cpu_count() or 1)
    except ValueError:
        n = os.cpu_count() or 1
    n = max(1, min(n, WINDOWS_GRENZE))
    if auftraege is not None:
        n = min(n, max(1, auftraege))
    return n


def gemeinsam():
    """Die an ``abbilden`` übergebenen gemeinsamen Daten (im Arbeitsprozess und beim seriellen Lauf)."""
    return _GEMEINSAM


def _start(daten):
    global _GEMEINSAM
    _GEMEINSAM = daten


def _arbeiter_start(daten):
    """Initialisierung im Arbeitsprozess: gemeinsame Daten setzen und mit dem Elternprozess enden. Wird der
    Elternprozess hart beendet (z. B. Abbruch einer Vorschau im Editor), blieben die Arbeiter sonst verwaist."""
    _start(daten)
    eltern = multiprocessing.parent_process()
    if eltern is not None:
        threading.Thread(target=_mit_eltern_enden, args=(eltern,), daemon=True).start()


def _mit_eltern_enden(eltern):
    eltern.join()
    os._exit(0)


@contextlib.contextmanager
def _hauptskript_verbergen():
    """Während Arbeitsprozesse starten: Hauptmodul als ``….__main__`` ausgeben. ``multiprocessing.spawn`` lädt
    es dann im Arbeitsprozess nicht (sonst liefe ein ungeschütztes Skript dort komplett noch einmal)."""
    haupt = sys.modules.get("__main__")
    if haupt is None or getattr(sys, "frozen", False):
        yield
        return
    alt = haupt.__dict__.get("__spec__")
    haupt.__spec__ = importlib.machinery.ModuleSpec("ego_parallel_hauptskript.__main__", None)
    try:
        yield
    finally:
        haupt.__spec__ = alt


def _gemessen(funktion, auftrag):
    t = time.perf_counter()
    return funktion(auftrag), time.perf_counter() - t


def _seriell(funktion, auftraege, daten):
    alt = _GEMEINSAM
    _start(daten)
    try:
        return [funktion(a) for a in auftraege]
    finally:
        _start(alt)


class Arbeitsgruppe:
    """Arbeitsprozesse für mehrere ``abbilden``-Aufrufe mit denselben gemeinsamen Daten (Start nur einmal).

    ``with Arbeitsgruppe(daten) as g: g.abbilden(funktion, auftraege)``. ``funktion`` steht auf Modulebene
    (der Arbeitsprozess importiert sie); ``daten`` gehen einmal je Arbeitsprozess hinüber und sind dort mit
    ``gemeinsam()`` lesbar, sie dürfen nicht verändert werden. Ausnahmen der Funktion kommen unverändert an.
    Ohne nutzbare Arbeitsprozesse (``EGO_PROZESSE=1``, schon im Arbeitsprozess, Pool startet nicht) läuft
    alles in diesem Prozess."""

    def __init__(self, daten=None, prozesse=None):
        self.daten, self.n, self.pool = daten, min(prozesse or anzahl(), anzahl()), None

    def __enter__(self):
        if self.n >= 2 and multiprocessing.parent_process() is None:
            try:
                self.pool = ProcessPoolExecutor(self.n, initializer=_arbeiter_start, initargs=(self.daten,))
            except OSError as fehler:
                self._melden(fehler)
        return self

    def __exit__(self, *exc):
        if self.pool is not None:
            self.pool.shutdown()
            self.pool = None

    @staticmethod
    def _melden(fehler):
        print("Hinweis: Arbeitsprozesse nicht verfügbar (%s), rechne in einem Prozess." % fehler, file=sys.stderr)

    def abbilden(self, funktion, auftraege, teile_je_prozess=8, aufwand=None):
        """Ergebnisliste in Auftragsreihenfolge. ``aufwand`` (je Auftrag eine Zahl): teure Aufträge zuerst
        verteilen, damit am Ende kein langer Auftrag allein läuft; die Ergebnisreihenfolge bleibt."""
        auftraege = list(auftraege)
        if aufwand is not None and self.pool is not None:
            folge = sorted(range(len(auftraege)), key=lambda i: -aufwand[i])
            sortiert = self.abbilden(funktion, [auftraege[i] for i in folge], teile_je_prozess)
            ergebnisse = [None] * len(auftraege)
            for i, e in zip(folge, sortiert):
                ergebnisse[i] = e
            return ergebnisse
        if os.environ.get("EGO_PARALLEL_ZEITEN") == "1":
            t = time.perf_counter()
            paare = self._abbilden(functools.partial(_gemessen, funktion), auftraege, teile_je_prozess)
            dauer = [d for _, d in paare]
            print("Parallel %s: %d Aufträge, %d Prozesse, Wand %.2f s, Summe %.2f s, längster %.2f s"
                  % (funktion.__name__, len(auftraege), self.n if self.pool else 1, time.perf_counter() - t,
                     sum(dauer), max(dauer, default=0.0)), file=sys.stderr)
            return [e for e, _ in paare]
        return self._abbilden(funktion, auftraege, teile_je_prozess)

    def _abbilden(self, funktion, auftraege, teile_je_prozess):
        if self.pool is not None and len(auftraege) > 1:
            groesse = max(1, len(auftraege) // (self.n * teile_je_prozess))
            try:
                with _hauptskript_verbergen():       # map reicht alle Aufträge sofort ein, Prozesse starten dabei
                    ergebnisse = self.pool.map(funktion, auftraege, chunksize=groesse)
                return list(ergebnisse)
            except (BrokenProcessPool, OSError) as fehler:
                self._melden(fehler)
                self.pool.shutdown()
                self.pool = None
        return _seriell(funktion, auftraege, self.daten)


def abbilden(funktion, auftraege, daten=None, prozesse=None, aufwand=None):
    """``funktion(auftrag)`` für alle Aufträge in Arbeitsprozessen (einmalige ``Arbeitsgruppe``);
    Ergebnisliste in Auftragsreihenfolge."""
    auftraege = list(auftraege)
    with Arbeitsgruppe(daten, min(prozesse or anzahl(), anzahl(len(auftraege)))) as gruppe:
        return gruppe.abbilden(funktion, auftraege, aufwand=aufwand)


def prozess_kennung(_=None) -> int:
    """Prozessnummer des Arbeiters (für Selbsttests, z. B. im Rauchtest der EXE)."""
    return os.getpid()


def threads(funktion, auftraege):
    """``funktion(auftrag)`` in Threads (für Arbeit, die die GIL freigibt, z. B. zstd); Reihenfolge bleibt."""
    auftraege = list(auftraege)
    n = anzahl(len(auftraege))
    if n < 2:
        return [funktion(a) for a in auftraege]
    with ThreadPoolExecutor(n) as pool:
        return list(pool.map(funktion, auftraege))
