"""Start: python -m mapeditor [Projektordner oder Definitions-JSON]."""

import argparse
import sys


def main(argv=None):
    # Der Werkzeugkasten startet paket_tool selbst mit sys.executable + Skriptpfad.
    # Auch dieser Aufruf muss im eingefrorenen Interpreter zum CLI werden.
    from pathlib import Path
    cli_args = list(sys.argv[1:] if argv is None else argv)
    allowed = {"neue_strecke_tool.py", "paket_tool.py", "erp_tool.py", "strecke_tool.py", "injektor_tool.py"}
    if cli_args and (cli_args[0] == "--werkzeug-cli" or
                     (getattr(sys, "frozen", False) and Path(cli_args[0]).name in allowed)):
        from .adapter import toolkit_path, run_tool_cli
        if cli_args[0] == "--werkzeug-cli":
            cli_args.pop(0)
        root = toolkit_path(Path(cli_args[0]).parent if cli_args and Path(cli_args[0]).is_absolute() else None)
        if not cli_args or cli_args[0] not in allowed and Path(cli_args[0]).resolve().parent != root:
            raise ValueError("Nur mitgelieferte Werkzeug-CLIs sind erlaubt.")
        script = root / Path(cli_args[0]).name
        if script.name not in allowed:
            raise ValueError("Unbekannte Werkzeug-CLI.")
        return run_tool_cli(root, script.name, cli_args[1:])
    if "--vorschau-arbeiter" in sys.argv:
        from .adapter import worker_main
        return worker_main()
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox
    from .project import Project
    from .window import MainWindow

    parser = argparse.ArgumentParser(description="Mapeditor · Strecken entwerfen und prüfen", add_help=False)
    parser.add_argument("-h", "--help", action="help", help="Hilfe anzeigen und beenden")
    parser._positionals.title = "Positionsargumente"
    parser._optionals.title = "Optionen"
    parser.add_argument("projekt", nargs="?", help="Projektordner, Manifest oder Definitions-JSON")
    parser.add_argument("--sprache", choices=["de", "en"],
                        help="Oberflächensprache für diesen Start (de/en); sonst die gespeicherte Wahl (Menü Ansicht, Sprache)")
    args = parser.parse_args(argv)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Mapeditor")
    app.setOrganizationName("Mapeditor")
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon(str(Path(__file__).with_name("mapeditor.ico"))))
    if sys.platform == "win32":
        try:   # eigene Taskleistengruppe mit eigenem Symbol (sonst Python-Symbol beim Start aus dem Quelltext)
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Mapeditor.Mapeditor")
        except (AttributeError, OSError):
            pass
    from . import ansicht
    from .i18n import setze_sprache, tr
    setze_sprache(args.sprache or ansicht.load_language())     # setzt auch QLocale (Zahlen, Eingabefelder)
    start = args.projekt
    if not start and ansicht.load_state().get("letztes_projekt"):
        start = next((p for p in ansicht.recent_projects() if Path(p).exists()), None)
    try:
        project = Project.open(start) if start else Project()
        window = MainWindow(project)
    except (ValueError, OSError, TypeError, KeyError) as error:
        QMessageBox.critical(None, tr("Projekt konnte nicht geöffnet werden"), str(error))
        return 1
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
