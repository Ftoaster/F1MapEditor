"""Rauchtest der gebauten Distribution (offscreen, ohne Bedienung).

1. Worker: Mapeditor.exe --vorschau-arbeiter mit teststrecke.json aus dem gebÃ¼ndelten Werkzeugkasten.
2. Editor: Mapeditor.exe mit MAPEDITOR_RAUCHTEST=1 Ã¶ffnet die Teststrecke, wartet auf die
   Generatorvorschau (lÃ¤uft Ã¼ber QProcess -> Worker-Prozess der EXE) und beendet sich selbst.
3. PrÃ¼ft, dass kein Zugriff auÃŸerhalb der Distribution auf ego-werkzeuge nÃ¶tig war
   (Umgebung ohne PYTHONPATH, MAPEDITOR_WERKZEUGKASTEN gelÃ¶scht).
"""
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ZIEL = Path(__file__).resolve().parents[1] / "dist" / "Mapeditor"
EXE = ZIEL / "Mapeditor.exe"


def umgebung():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for k in ("PYTHONPATH", "MAPEDITOR_WERKZEUGKASTEN", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(k, None)
    return env


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    assert EXE.is_file(), f"Nicht gebaut: {EXE}"
    probe = ZIEL / "werkzeugkasten" / "strecke" / "beispiele" / "teststrecke.json"
    raw = json.loads(probe.read_text(encoding="utf-8"))
    anfrage = {"raw": raw, "basis": str(probe.parent), "toolkit": str(ZIEL / "werkzeugkasten"),
               "closed": True, "shift": [0, 0, 0]}
    t = time.perf_counter()
    r = subprocess.run([str(EXE), "--vorschau-arbeiter"], input=json.dumps(anfrage).encode("utf-8"),
                       capture_output=True, env=umgebung(), timeout=300)
    dt = time.perf_counter() - t
    antwort = json.loads(r.stdout.decode("utf-8"))
    assert r.returncode == 0 and "error" not in antwort, antwort
    print(f"[1] Arbeiter ok: {dt:.1f} s, SchlÃ¼ssel: {sorted(antwort)[:6]}...")
    t = time.perf_counter()
    r = subprocess.run([str(EXE)], capture_output=True, timeout=300,
                       env=dict(umgebung(), MAPEDITOR_RAUCHTEST="1"))
    dt = time.perf_counter() - t
    out = r.stdout.decode("utf-8", "replace").strip().splitlines()
    print(f"[2] Editor Exitcode {r.returncode}, Gesamt {dt:.1f} s")
    print("    " + (out[-1] if out else "(keine Ausgabe)"), r.stderr.decode("utf-8", "replace")[:500])
    assert r.returncode == 0, "Editor-Rauchtest fehlgeschlagen"
    assert str(ZIEL).lower() in json.loads(out[-1])["werkzeugkasten"].lower(), "Werkzeugkasten nicht aus Distribution"
    r = subprocess.run([str(EXE)], capture_output=True, timeout=300,
                       env=dict(umgebung(), MAPEDITOR_IMPORTTEST="1"))
    zeile = r.stdout.decode("utf-8", "replace").strip().splitlines()
    print("[3] Werkzeugkasten-Importe:", zeile[-1] if zeile else r.stderr.decode("utf-8", "replace")[:500])
    assert r.returncode == 0, "Importtest fehlgeschlagen (fehlende Abhaengigkeit?)"
    for command, schema in (("regeln", "ego-regelkatalog/1"), ("profile", "ego-bauprofile/1")):
        r = subprocess.run([str(EXE), "--werkzeug-cli", "neue_strecke_tool.py", command],
                           capture_output=True, env=umgebung(), timeout=30)
        data = json.loads(r.stdout.decode("utf-8"))
        assert r.returncode == 0 and data["schema"] == schema, (r.returncode, r.stderr)
        print(f"[4] CLI-Shim {command}: ok")
    r = subprocess.run([str(EXE), "--werkzeug-cli", "neue_strecke_tool.py", "pruefen", "--definition", str(probe),
                        "--mit-generator", "--versatz=-591,0,-473", "--streckencode", "sochi", "--pvs-variante", "strecke"],
                       capture_output=True, env=umgebung(), timeout=60)
    data = json.loads(r.stdout.decode("utf-8"))
    assert r.returncode == 0 and data["zusammenfassung"]["ok"], (r.returncode, data, r.stderr)
    print("[5] CLI-Shim pruefen mit Generator: ok")
    # Derselbe Aufruf, den der Werkzeugkasten für die tiefe Paketprüfung verwendet.
    r = subprocess.run([str(EXE), str(ZIEL / "werkzeugkasten" / "paket_tool.py"), "--help"],
                       capture_output=True, env=umgebung(), timeout=30)
    assert r.returncode == 0 and b"installiert" in r.stdout, (r.returncode, r.stderr)
    print("[6] Paket-Kindprozess ohne python.exe: ok")
    work = ZIEL.parents[1] / "build" / "rauchtest_cli"
    work.mkdir(parents=True, exist_ok=True)
    probe_zip = work / "attrappe.zip"
    manifest = dict(track_code="sochi", track_name="Prüfung", author_name="Rauchtest", version="1",
                    erp="fehlt.erp", ui={}, copy_directories=[])
    with zipfile.ZipFile(probe_zip, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
    r = subprocess.run([str(EXE), str(ZIEL / "werkzeugkasten" / "paket_tool.py"), "pruefen", "--tief", str(probe_zip)],
                       capture_output=True, env=dict(umgebung(), MAPEDITOR_WERKZEUG_ARBEIT=str(work)), timeout=30)
    out = r.stdout.decode("utf-8")  # Absichtlich strikt: Regression der Frozen-CP1252-Ausgabe.
    assert r.returncode == 1 and "Prüfungen" in out and "vollständig" in out, (r.returncode, out, r.stderr)
    assert (work / "werkzeug-pruefung" / "testausgabe" / "pakete" / "_pruefen").is_dir()
    assert not (ZIEL / "werkzeugkasten" / "testausgabe").exists(), "Prüfer schreibt in die Installation"
    print("[7] Echte Paket-CLI mit kleiner Fehlerattrappe: UTF-8 und isolierter Prüfordner ok")
    r = subprocess.run([str(EXE)], capture_output=True, timeout=120, env=dict(umgebung(), MAPEDITOR_PARALLELTEST="1"))
    zeile = r.stdout.decode("utf-8", "replace").strip().splitlines()
    assert r.returncode == 0 and zeile and json.loads(zeile[-1])["ok"], (r.returncode, zeile, r.stderr[-800:])
    print("[8] Arbeitsprozesse des Werkzeugkastens aus der EXE:", zeile[-1])
    print("Rauchtest bestanden")


if __name__ == "__main__":
    sys.exit(main())
