"""Quellpaket offline in der bereitgestellten lokalen Umgebung verfügbar machen."""

from pathlib import Path
import sys
import sysconfig


def main():
    root = Path(__file__).resolve().parent
    prefix = Path(sys.prefix).resolve()
    if prefix != root / ".venv":
        raise SystemExit("Bitte mit Mapeditor/.venv/Scripts/python einrichten.py ausführen.")
    target = Path(sysconfig.get_path("purelib")) / "mapeditor_quellen.pth"
    target.write_text(str(root / "src") + "\n", encoding="utf-8")
    print("Mapeditor eingerichtet. Start: .venv\\Scripts\\python -m mapeditor")


if __name__ == "__main__":
    main()
