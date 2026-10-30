"""Einstiegspunkt der gebauten Anwendung (PyInstaller). Ã„ndert keine Quellen in src/.

Normalstart: Editor. ``--vorschau-arbeiter``: Generatorvorschau-Prozess (Adapter).
Umgebungsvariable ``MAPEDITOR_RAUCHTEST=1``: Selbsttest ohne Bedienung (siehe rauchtest.py).
``--multiprocessing-fork``: Arbeitsprozess des Werkzeugkastens (``ego/parallel.py``), über ``freeze_support``.
"""
import os
import sys


def _rauchtest():
    import json
    import time
    from pathlib import Path
    from PySide6.QtWidgets import QApplication
    from mapeditor.project import Project
    from mapeditor.window import MainWindow
    from mapeditor import adapter

    t0 = time.perf_counter()
    app = QApplication(sys.argv[:1])
    projekt = Path(adapter.toolkit_path()) / "strecke" / "beispiele" / "teststrecke.json"
    window = MainWindow(Project.open(projekt))
    window.show()
    t_fenster = time.perf_counter() - t0
    frist = time.perf_counter() + float(os.environ.get("MAPEDITOR_RAUCHTEST_FRIST", "120"))
    while window.preview is None and time.perf_counter() < frist:
        app.processEvents()
        time.sleep(0.02)
    ok = window.preview is not None
    bericht = {"ok": ok, "fenster_s": round(t_fenster, 2),
               "vorschau_s": round(time.perf_counter() - t0, 2),
               "werkzeugkasten": str(adapter.toolkit_path()),
               "stationen": len(window.preview["curvature"]) if ok else 0,
               "status": window.status_label.text()}
    print(json.dumps(bericht, ensure_ascii=False))
    window.close()
    return 0 if ok else 1


def _importtest():
    """Lädt alle Werkzeugkasten-Module aus der Distribution (Abhängigkeitsprüfung für spätere Bau-CLI)."""
    import importlib
    import json
    from pathlib import Path
    from mapeditor import adapter
    root = adapter.toolkit_path()
    sys.path.insert(0, str(root))
    fehler = {}
    namen = [f"{o}.{p.stem}" for o in ("ego", "strecke") for p in sorted((root / o).glob("*.py")) if p.stem != "__init__"]
    namen += ["neue_strecke_tool", "paket_tool", "erp_tool", "strecke_tool"]
    for n in namen:
        try:
            importlib.import_module(n)
        except Exception as e:  # noqa
            fehler[n] = f"{type(e).__name__}: {e}"
    print(json.dumps({"module": len(namen), "fehler": fehler}, ensure_ascii=False))
    return 1 if fehler else 0


def _paralleltest():
    """Arbeitsprozesse des Werkzeugkastens aus der EXE starten (ego/parallel.py; Packen wie beim Bau)."""
    import json
    from mapeditor import adapter
    sys.path.insert(0, str(adapter.toolkit_path()))
    from ego import erp, parallel
    daten = [bytes([i % 7]) * 4000 + bytes(range(256)) for i in range(16)]
    gepackt = parallel.abbilden(erp.fragment_packen, daten, prozesse=4)
    kennungen = set(parallel.abbilden(parallel.prozess_kennung, range(16), prozesse=4))
    ok = gepackt == [erp.fragment_packen(d) for d in daten] and os.getpid() not in kennungen
    print(json.dumps({"ok": ok, "arbeitsprozesse": len(kennungen), "kerne": parallel.anzahl()}))
    return 0 if ok else 1


if __name__ == "__main__":
    import multiprocessing
    try:
        multiprocessing.freeze_support()     # Arbeitsprozesse des Werkzeugkastens starten als Mapeditor.exe
    except OSError:
        # Arbeitsprozess, dessen Elternprozess schon beendet ist (Vorschau/Bau abgebrochen, während er startete):
        # OpenProcess auf die alte PID scheitert mit WinError 87. Still beenden statt Fehlerfenster (27.10.2026).
        if "--multiprocessing-fork" in sys.argv:
            os._exit(0)
        raise
    if os.environ.get("MAPEDITOR_PARALLELTEST") == "1":
        raise SystemExit(_paralleltest())
    if os.environ.get("MAPEDITOR_IMPORTTEST") == "1" and "--vorschau-arbeiter" not in sys.argv:
        raise SystemExit(_importtest())
    if os.environ.get("MAPEDITOR_RAUCHTEST") == "1" and "--vorschau-arbeiter" not in sys.argv:
        raise SystemExit(_rauchtest())
    from mapeditor.__main__ import main
    raise SystemExit(main())
