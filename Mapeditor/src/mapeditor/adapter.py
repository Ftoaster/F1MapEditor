"""Einzige Werkzeuggrenze: Vorschau/Prüfung im Arbeiter, atomarer Bau als JSON-CLI."""

from __future__ import annotations

import json
import codecs
import hashlib
import os
from pathlib import Path
import sys
import re
import shutil
import subprocess
import runpy

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal
from .i18n import N_, tr
from .paths import writable_path
from .process_group import ProcessGroup


def toolkit_path(configured=None):
    candidates = [configured, os.environ.get("MAPEDITOR_WERKZEUGKASTEN")]
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / "werkzeugkasten")
    candidates.append(Path(__file__).resolve().parents[3] / "ego-werkzeuge")
    for value in candidates:
        if value:
            path = Path(value).resolve()
            if (path / "strecke" / "definition.py").is_file():
                return path
            # Ein ausdrücklich konfigurierter Pfad darf nicht unbemerkt ersetzt werden.
            if value == configured or value == os.environ.get("MAPEDITOR_WERKZEUGKASTEN"):
                raise ValueError(tr("Werkzeugkasten nicht gefunden: {path}", path=path))
    raise ValueError(tr("Werkzeugkasten fehlt. Pfad über Werkzeuge → Werkzeugkasten wählen setzen."))


def worker_command():
    if getattr(sys, "frozen", False):
        return [sys.executable, "--vorschau-arbeiter"]
    return [sys.executable, "-B", "-m", "mapeditor.adapter", "--arbeiter"]


#: Streckencodes, die die Spieldatenbank kennt und das Spiel selbst nicht lädt (Werkzeugkasten
#: ``strecke/streckencode.py`` ``DB_ALTSTRECKEN``, docs/streckencode.md): (Code, Einstufung, Hinweis), empfohlene zuerst.
TRACK_CODES = (
    ("sochi", N_("empfohlen"), N_("fast vollständige Streckendaten; im Spiel bestätigt")),
    ("hanoi", N_("gut"), N_("Streckendaten vorhanden")),
    ("paul_ricard", N_("gut"), N_("Streckendaten vorhanden; steht in alten Ereignislisten")),
    ("hockenheim", N_("unklar"), N_("nur wenige Datenbankeinträge; ob die Strecke auswählbar wird, ist offen")),
    ("sepang", N_("unklar"), N_("nur wenige Datenbankeinträge; ob die Strecke auswählbar wird, ist offen")),
    ("portimao", N_("belegt"), N_("Code des Portimão-Mods (Träger); eine Übertragung ersetzt Portimão")),
    ("bahrain24", N_("belegt"), N_("Code des Sepang-Mods; eine Übertragung ersetzt Sepang")),
    ("styria", N_("riskant"), N_("gleiche Anlage wie Österreich; lädt vermutlich deren Ordner")),
    ("silverstone_int", N_("riskant"), N_("Layout-Variante von Silverstone")),
    ("suzuka_east", N_("riskant"), N_("Layout-Variante von Suzuka")),
    ("texas_nat", N_("riskant"), N_("Layout-Variante von Texas")),
    ("bahrain_pad", N_("riskant"), N_("Layout-Variante von Bahrain")),
)
#: Erweiterte Bauoptionen (überschreiben das Profil „empfohlen“): Streckencode, Streckenname, PVS-Variante
BUILD_CODES = tuple(code for code, _, _ in TRACK_CODES)
BUILD_PVS = ("strecke", "alles")
BUILD_BIOME = ("standard", "wueste")
BUILD_FARBTOENE = ("standard", "trocken", "saftig", "herbst")


def build_command(toolkit, project_folder, output, packages, name="entwurf", python=None,
                  profile="empfohlen", carrier=None, ki_profil=False, options=None, umgebung=None):
    """AP24-Vertrag; alle Schreibziele liegen außerhalb Spiel und Installation.

    ``umgebung``: Pfad der ``umgebung.json`` (Umgebungsobjekte, Vertrag ``umgebung_vertrag.md``); ohne Angabe
    gibt es keinen ``--umgebung``-Schalter und der Bau wie bisher (Bodenbewuchs geleert)."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", name):
        raise ValueError(tr("Bauname: 1–80 Buchstaben/Ziffern, Bindestrich oder Unterstrich."))
    if profile != "empfohlen":
        raise ValueError(tr("Unbekanntes Bauprofil."))
    root, target = writable_path(output), writable_path(packages)
    work = writable_path(root / name)
    if (target / (name + ".zip")).exists() or work.exists():
        raise ValueError(tr("Bauname bereits vorhanden. Vorschlag: {name}_neu", name=name))
    if getattr(sys, "frozen", False) and python is None:
        prefix = [sys.executable, "--werkzeug-cli", "neue_strecke_tool.py"]
    else:
        prefix = [str(python or sys.executable), "-B", "-m", "mapeditor", "--werkzeug-cli",
                  str(Path(toolkit).resolve() / "neue_strecke_tool.py")]
    return prefix + ["bauen", "--ereignisse-json", "--profil", profile,
                     "--projekt", str(Path(project_folder).resolve()), "--atomar", "--name", name,
                     "--ausgabe", str(work), "--paket-ordner", str(target),
                     "--abbruch-datei", str(writable_path(root / (name + ".abbruch"))),
                     "--traeger", str(Path(carrier or default_carrier(toolkit)).resolve()),
                     "--traeger-cache", str(writable_path(work / "traeger-cache")),
                     "--pruefsummen", "--arbeit-aufraeumen"] + (["--ki-daten", "profil"] if ki_profil else []) \
        + _option_args(options) + (["--umgebung", str(Path(umgebung).resolve())] if umgebung else [])


def _option_args(options):
    """Erweiterte Bauoptionen als CLI-Argumente (nur gesetzte, geprüfte Werte)."""
    options = options or {}
    args = []
    code = options.get("streckencode")
    if code:
        if code not in BUILD_CODES:
            raise ValueError(tr("Streckencode {code!r} nicht erlaubt (nur {wert}).", code=code, wert=', '.join(BUILD_CODES)))
        args += ["--streckencode", code]
    name = (options.get("streckenname") or "").strip()
    if name:
        if len(name) > 60 or any(c in name for c in '"\\<>|'):
            raise ValueError(tr("Streckenname: höchstens 60 Zeichen, keine Anführungszeichen oder Sonderzeichen."))
        args += ["--streckenname", name]
    pvs = options.get("pvs_variante")
    if pvs:
        if pvs not in BUILD_PVS:
            raise ValueError(tr("PVS-Variante {pvs!r} unbekannt.", pvs=pvs))
        args += ["--pvs-variante", pvs]
    if "weltkarten" in options:
        args.append("--weltkarten" if options["weltkarten"] else "--no-weltkarten")
    if options.get("weltkarten"):
        biom, farbton = options.get("biom") or "standard", options.get("farbton") or "standard"
        if biom not in BUILD_BIOME:
            raise ValueError(tr("Landschaft {wert!r} unbekannt.", wert=biom))
        if farbton not in BUILD_FARBTOENE:
            raise ValueError(tr("Farbton {wert!r} unbekannt.", wert=farbton))
        args += ["--biom", biom, "--farbton", farbton]
    return args


def tool_command(toolkit, script, arguments, python=None):
    """Aufruf einer mitgelieferten Werkzeug-CLI über den CLI-Shim (Quellstart und EXE)."""
    if getattr(sys, "frozen", False) and python is None:
        return [sys.executable, "--werkzeug-cli", script] + list(arguments)
    return [str(python or sys.executable), "-B", "-m", "mapeditor", "--werkzeug-cli",
            str(Path(toolkit).resolve() / script)] + list(arguments)


def injector_command(toolkit, action, package, backups, python=None):
    """Schnellaustausch (``injektor_tool.py``): ``plan`` zeigt nur, ``injizieren`` schreibt ins Spiel.

    Sicherungen gehen in ``backups`` (beschreibbar, außerhalb von Spiel und Installation)."""
    if action not in ("plan", "injizieren"):
        raise ValueError(tr("Unbekannte Schnellaustausch-Aktion."))
    arguments = ["--sicherungsordner", str(writable_path(backups)), action, str(Path(package).resolve())]
    if action == "injizieren":
        arguments.append("--ausfuehren")
    return tool_command(toolkit, "injektor_tool.py", arguments, python)


def game_status_command(toolkit, python=None):
    """Zustand im Spiel (nur lesen): installierte Codes mit Slot, Datenbank aktiv/gemerkt, Spiel läuft.

    Ausgabe: eine JSON-Zeile (``injektor_tool.py datenbank --json``). Merkt eine aktivierte Datenbank in der
    Ablage des Werkzeugkastens (außerhalb des Spiels)."""
    return tool_command(toolkit, "injektor_tool.py", ["datenbank", "--json"], python)


def parse_game_status(text):
    """Letzte JSON-Zeile der Zustandsabfrage; None, wenn keine lesbar ist."""
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                return None
    return None


def default_carrier(toolkit):
    return Path(toolkit) / "testausgabe" / "pakete" / "portimao_original_wiederhergestellt.zip"


def run_tool_cli(root, script, arguments):
    """CLI-Shim für Quell- und EXE-Start, einschließlich verschachtelter Prüfung.

    paket_tool hat noch einen festen Prüf-Arbeitsordner relativ zu HIER. Nur diese
    Laufzeitvariable wird umgebogen; die mitgelieferten Werkzeugquellen bleiben gleich.
    """
    root = Path(root).resolve()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    os.environ["MAPEDITOR_WERKZEUGKASTEN"] = str(root)
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    if script == "neue_strecke_tool.py" and arguments[:1] == ["bauen"] and "--ausgabe" in arguments:
        work = writable_path(arguments[arguments.index("--ausgabe") + 1])
        os.environ["MAPEDITOR_WERKZEUG_ARBEIT"] = str(work)
    sys.argv = [str(root / script)] + list(arguments)
    if script == "paket_tool.py":
        namespace = runpy.run_path(str(root / script), run_name="__mapeditor_werkzeug__")
        work = writable_path(os.environ.get("MAPEDITOR_WERKZEUG_ARBEIT", Path.cwd()))
        namespace["main"].__globals__["HIER"] = str(work / "werkzeug-pruefung")
        return namespace["main"](arguments)
    original_popen = subprocess.Popen

    def routed_popen(command, *args, **kwargs):
        if (not getattr(sys, "frozen", False) and isinstance(command, (list, tuple)) and len(command) > 1
                and Path(command[0]).resolve() == Path(sys.executable).resolve()
                and Path(command[1]).resolve() == root / "paket_tool.py"):
            command = [sys.executable, "-B", "-m", "mapeditor", "--werkzeug-cli", str(root / "paket_tool.py")] + list(command[2:])
        return original_popen(command, *args, **kwargs)

    subprocess.Popen = routed_popen
    try:
        runpy.run_path(str(root / script), run_name="__main__")
        return 0
    finally:
        subprocess.Popen = original_popen


def issue_from_message(message):
    issue = dict(message)
    issue["level"] = {"fehler": "Fehler", "warnung": "Warnung", "hinweis": "Hinweis"}.get(message.get("schwere"), "Hinweis")
    location = message.get("ort") or {}
    if location.get("spline") == "mittellinie" and location.get("station_m") is not None:
        issue["s"] = location["station_m"]
    return issue


class ProcessJob(QObject):
    """Abbrechbarer Einzelprozess mit unverfälschter Textausgabe, ohne Prozentfiktion."""
    output = Signal(str)
    done = Signal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONDONTWRITEBYTECODE", "1")
        # Quellstart und installierter Start benutzen denselben Arbeitervertrag.
        if not getattr(sys, "frozen", False):
            env.insert("PYTHONPATH", str(Path(__file__).resolve().parents[1]) + os.pathsep + env.value("PYTHONPATH"))
        self.process.setProcessEnvironment(env)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self.buffer = bytearray()
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.cancelled = False
        self.completed = False

    def start(self, command, payload=None):
        self.buffer.clear()
        self.decoder.reset()
        self.cancelled = self.completed = False
        self.process.start(str(command[0]), list(map(str, command[1:])))
        if payload is not None:
            self.process.write(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        self.process.closeWriteChannel()

    def _read(self):
        data = bytes(self.process.readAllStandardOutput())
        self.buffer.extend(data)
        decoded = self.decoder.decode(data)
        if decoded:
            self.output.emit(decoded)

    def _finished(self, code, status):
        self._read()
        if not self.completed:
            self.completed = True
            tail = self.decoder.decode(b"", final=True)
            if tail:
                self.output.emit(tail)
            self.done.emit(-2 if self.cancelled else code, self.buffer.decode("utf-8", errors="replace"))

    def _error(self, error):
        if error == QProcess.FailedToStart and not self.completed:
            self.completed = True
            self.done.emit(-1, self.process.errorString())

    def cancel(self):
        self.cancelled = True
        self.process.terminate()
        QTimer.singleShot(800, self._kill_if_running)

    def _kill_if_running(self):
        if self.process.state() != QProcess.NotRunning:
            self.process.kill()


class BuildJob(ProcessJob):
    """JSON-Lines auf stdout, Diagnose auf stderr; kooperativer Abbruch mit Frist."""
    event = Signal(dict)

    def __init__(self, abort_file, work, parent=None, cancel_timeout_ms=120000):
        super().__init__(parent)
        self.abort_file, self.work = writable_path(abort_file), writable_path(work)
        self.process.setProcessChannelMode(QProcess.SeparateChannels)
        self.process.readyReadStandardError.connect(self._stderr)
        self.kill_timer = QTimer(self)
        self.kill_timer.setSingleShot(True)
        self.kill_timer.setInterval(cancel_timeout_ms)
        self.kill_timer.timeout.connect(self._kill_if_running)
        self.lines = ""
        self.terminal = None
        self.protocol_error = None
        self.killed = False
        self.stderr_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.group = None
        self.process.started.connect(self._attach_group)

    def _attach_group(self):
        try:
            self.group = ProcessGroup()
            self.group.attach(self.process.processId())
        except OSError as error:
            self.output.emit(f"Windows-Prozessgruppe nicht verfügbar: {error}\n")

    def start(self, command, payload=None):
        self.abort_file.unlink(missing_ok=True)
        super().start(command, payload)

    def _line(self, line):
        if not line.strip():
            return
        try:
            event = json.loads(line)
            if event.get("schema") != "ego-bau-ereignis/1" or not isinstance(event.get("typ"), str):
                raise ValueError(tr("Unbekanntes Bauereignis"))
            if self.terminal is not None:
                raise ValueError(tr("Ereignis nach dem Bauende"))
            if event["typ"] in ("ergebnis", "fehler", "abbruch"):
                self.terminal = event
            self.event.emit(event)
        except (ValueError, TypeError, AttributeError) as error:
            self.protocol_error = tr("Ungültiger Ereignisstrom: {error}", error=error)
            self.output.emit(self.protocol_error + "\n" + line + "\n")

    def _read(self):
        data = bytes(self.process.readAllStandardOutput())
        self.buffer.extend(data)
        self.lines += self.decoder.decode(data)
        while "\n" in self.lines:
            line, self.lines = self.lines.split("\n", 1)
            self._line(line)

    def _stderr(self):
        text = self.stderr_decoder.decode(bytes(self.process.readAllStandardError()))
        if text:
            self.output.emit(text)

    def _finished(self, code, status):
        self.kill_timer.stop()
        if self.group:
            self.group.close()
        self._read()
        self._stderr()
        self.lines += self.decoder.decode(b"", final=True)
        self._line(self.lines)
        self.lines = ""
        tail = self.stderr_decoder.decode(b"", final=True)
        if tail:
            self.output.emit(tail)
        if self.killed:
            temp = self.work / "_paket_tmp"
            # Nur den bekannten temporären Paketordner dieses Baus entfernen.
            try:
                if not temp.is_symlink() and writable_path(temp).parent == self.work:
                    shutil.rmtree(temp, ignore_errors=True)
            except ValueError:
                pass
            if self.work.is_dir():
                try:
                    from .project import atomic_write, encoded
                    atomic_write(self.work / "bau_status.json", encoded({"status": "abgebrochen",
                                 "text": tr("Abbruch-Zeitlimit: Prozessbaum hart beendet"), "hart_beendet": True}))
                except (ValueError, OSError) as error:
                    self.output.emit(f"Abbruchstatus nicht gespeichert: {error}\n")
        if not self.completed:
            self.completed = True
            self.done.emit(-2 if self.killed else code, self.buffer.decode("utf-8", errors="replace"))

    def cancel(self):
        if self.completed or self.cancelled:
            return
        # Kein terminate(): WM_CLOSE erreicht Konsolenprozesse unter Windows nicht.
        self.abort_file.parent.mkdir(parents=True, exist_ok=True)
        self.abort_file.write_text("Abbruch durch Mapeditor\n", encoding="utf-8")
        self.cancelled = True
        self.kill_timer.start()

    def _kill_if_running(self):
        if self.process.state() == QProcess.NotRunning:
            return
        self.killed = True
        try:
            if self.group and self.group.kill():
                self.process.kill()
                return
        except OSError as error:
            self.output.emit(f"Windows-Prozessgruppe: {error}\n")
        if os.name == "nt":
            # Tiefe Paketprüfung ist ein Kindprozess; beim harten Abbruch mit beenden.
            try:
                subprocess.run(["taskkill", "/PID", str(self.process.processId()), "/T", "/F"],
                               capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
            except (OSError, subprocess.TimeoutExpired) as error:
                self.output.emit(f"Prozessbaum-Abbruch: {error}\n")
        self.process.kill()


def calculate_preview(request):
    """Delegiert sämtliche Streckenberechnungen an die vorhandenen Module."""
    import numpy as np

    root = toolkit_path(request.get("toolkit"))
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    from strecke import definition, mittellinie, querprofil, pruefregeln, zonen

    if not request.get("closed", True):
        return {"draft": True, "defaults": definition.STANDARD}
    raw = request["raw"]
    report = pruefregeln.vorab_pruefen(raw, basisordner=request.get("basis", "."),
                                     versatz=request.get("shift", [0, 0, 0]),
                                     mit_generator=request.get("check_generator", False),
                                     bauoptionen={"streckencode": "sochi", "pvs_variante": "strecke"})
    issues = [issue_from_message(m) for m in report["meldungen"]]
    if request.get("check_only"):
        return {"issues": issues, "report": report}
    if any(m["id"] == "DEF_UNGUELTIG" for m in issues):
        return {"validation_only": True, "issues": issues, "report": report}
    defn = definition.aus_dict(raw, basisordner=request.get("basis", "."))
    ml = mittellinie.Mittellinie(defn)
    pk = defn.param["kurven"]
    curves = mittellinie.kurven_finden(ml, pk["kruemmung_schwelle"], pk["mindestwinkel_grad"], pk["zusammenfassen_m"])
    qp = querprofil.querprofil_berechnen(ml, defn.param, curves)
    check = mittellinie.raender_pruefen(ml)
    # Zeichenauflösung begrenzen; die Prüfung oben bleibt auf dem Originalgitter.
    indices = np.arange(0, ml.anzahl, max(1, int(np.ceil(ml.anzahl / 1800))))
    st = ml.ref
    pos, normal = st.pos[:, [0, 2]], st.nl
    def offset(distance):
        return (pos + normal * np.asarray(distance)[:, None])[indices].tolist()
    sides = {}
    for side, sign, width in (("links", 1, st.wl), ("rechts", -1, st.wr)):
        profile = qp.seiten[side]
        sides[side] = {"edges": [offset(sign * (width + profile["e"][:, k])) for k in range(6)],
                       "bands": profile["band"][indices].tolist(),
                       "distances": profile["e"][indices].tolist()}
        if getattr(ml, "rand", None) is not None:      # Randwinkel: Höhe je Meter nach außen
            sides[side]["edge_slope"] = ml.rand[0 if side == "links" else 1][indices].tolist()
    source_positions = defn.punkte.tolist()
    # Stationen der Eingabeobjekte aus dem Werkzeug-Spline, keine zweite Geometrie.
    # Stationen der Eingabeobjekte aus dem Werkzeug (bei Approximation liegen die Spline-Knoten nicht mehr auf den Punkten)
    s_eingabe = getattr(ml, "s_eingabe", None)
    if s_eingabe is None:
        s_eingabe = ml.spline.bogenlaenge(ml.spline.t[:-1])
    source_stations = np.mod(np.asarray(s_eingabe) - ml.s_start, ml.laenge)
    if defn.param["mittellinie"].get("umkehren"):
        source_stations = source_stations[::-1]
        source_positions = source_positions[::-1]
    object_positions, object_stations = source_positions, source_stations.tolist()
    if raw["mittellinie"]["art"] == "segmente":
        # Segmentanfänge vom Werkzeug (bei Klothoiden-Übergängen die Übergangsmitte); ältere
        # Werkzeugstände ohne dieses Feld: aus den Segmentlängen wie bisher.
        endpoints = getattr(defn, "segment_anfaenge", None)
        if endpoints is None:
            endpoints = [0]
            for segment in raw["mittellinie"]["segmente"]:
                length, _, _ = definition._segment_kruemmung(segment)
                endpoints.append(endpoints[-1] + max(1, int(np.ceil(length))))
            endpoints = endpoints[:-1]
        endpoints = np.minimum(endpoints, len(source_positions) - 1)
        object_positions = [source_positions[i] for i in endpoints]
        object_stations = [float(source_stations[i]) for i in endpoints]
    # DRS-/Straight-Mode-Zonen (Meter ab Ziel) wie der Generator sie setzt: eigene oder Automatik.
    try:
        zones = {"drs": zonen.drs_zonen(ml, defn.param),
                 "xmode": zonen.xmode_zonen(ml, defn.param) or zonen.xmode_automatik(ml.laenge)}
    except (ValueError, KeyError, TypeError):
        zones = {"drs": [], "xmode": []}
    tau = ml.spline.param_aus_s(np.mod(st.s + ml.s_start, ml.laenge))
    raw_heights = ml.spline.auswerten(tau)[:, 1]
    return {"length": ml.laenge, "s": st.s[indices].tolist(), "pos": st.pos[indices].tolist(),
            "raw_y": raw_heights[indices].tolist(), "bank": np.degrees(st.bank[indices]).tolist(),
            "wl": st.wl[indices].tolist(), "wr": st.wr[indices].tolist(), "normal": normal[indices].tolist(),
            "gradient": (st.steigung[indices] * 100).tolist(), "curvature": st.kappa[indices].tolist(),
            "left": offset(st.wl), "right": offset(-st.wr), "sides": sides,
            "objects": object_positions, "object_s": object_stations, "defaults": definition.STANDARD,
            "curves": curves, "issues": issues, "report": report, "min_radius": check["min_radius_m"],
            "direction": ml.laufrichtung(), "source_count": len(defn.punkte),
            "edge_reach": float((defn.param.get("seitenraum") or {}).get("rand_breite_m", 12.0)),
            "line_width": defn.param["fahrbahn"]["randlinie_breite"], "barrier": defn.param["barriere"],
            "zones": zones, "s_start": float(getattr(ml, "s_start", 0.0))}


def check_key(raw, basis, shift):
    """Schlüssel eines Prüf-/Fahrlinienstands: gleiche Definition, Basis und Versatz → gleiches Ergebnis."""
    text = json.dumps({"raw": raw, "basis": str(basis), "shift": list(shift or [0, 0, 0])},
                      ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


#: Haupt-Bremslinien des Portimão-Trägers: mehr Kurven legt der Bau zusammen (spiel_welt.kurven_begrenzen)
TRAEGER_KURVEN = 15


def game_numbers(kurven, n, grenze=TRAEGER_KURVEN):
    """Kurvennummer im Spiel je Generator-Kurve. Wie beim Bau werden, solange es mehr Kurven als Bremslinien des
    Trägers gibt, die beiden Kurven mit der kürzesten Geraden dazwischen zusammengelegt (n = Stationen der Runde)."""
    groups = [[i] for i in range(len(kurven))]
    while len(groups) > grenze and len(groups) > 1:
        gaps = [((kurven[groups[(j + 1) % len(groups)][0]]["i_anfang"] - kurven[g[-1]]["i_ende"]) % n, j)
                for j, g in enumerate(groups)]
        _, j = min(gaps)
        k = (j + 1) % len(groups)
        if k == 0:                   # Zusammenlegen über die Ziellinie: letzte Kurve gehört zur ersten
            groups[0] = groups[j] + groups[0]
        else:
            groups[j] = groups[j] + groups[k]
        del groups[k if k else j]
    numbers = [0] * len(kurven)
    for nummer, g in enumerate(groups, 1):
        for i in g:
            numbers[i] = nummer
    return numbers


def analysis_summary(erg, schritt_m=2.0):
    """Kompakte, JSON-fähige Auswertung eines Generatorlaufs (Generatorkoordinaten X/Z wie die Vorschau)."""
    import numpy as np

    ml, rl, lg = erg["mittellinie"], erg["ideallinie"], erg["logik"]
    L = float(ml.laenge)
    v = np.asarray(rl.v, float) * 3.6
    a = np.asarray(rl.a_laengs, float)
    s_ml = np.mod(np.asarray(rl.s_ml, float), L)
    vollgas = np.asarray(getattr(rl, "vollgas", a > 0.3), bool)
    zustand = np.where(a < -1.0, "b", np.where(vollgas, "g", "r"))
    step = max(1, int(round(schritt_m / max(float(rl.ds), 0.1))))
    idx = np.arange(0, len(v), step)
    pos = np.asarray(rl.pos, float)
    linie = {"x": np.round(pos[idx, 0], 2).tolist(), "z": np.round(pos[idx, 2], 2).tolist(),
             "s": np.round(s_ml[idx], 1).tolist(), "v": np.round(v[idx], 1).tolist(), "zustand": "".join(zustand[idx])}
    # Bremszonen: zusammenhängend a < -2 m/s², mindestens 10 km/h Abbau
    brems = a < -2.0
    zonen = []
    if brems.any() and not brems.all():
        start = int(np.argmin(brems))             # an einer Stelle ohne Bremsen beginnen
        order = np.roll(np.arange(len(a)), -start)
        run = []
        for i in list(order) + [order[0]]:
            if brems[i]:
                run.append(int(i))
            elif run:
                i0, i1 = run[0], run[-1]
                if v[i0] - v[i1] >= 10.0:
                    zonen.append({"s_start": round(float(s_ml[i0]), 1), "s_ende": round(float(s_ml[i1]), 1),
                                  "v_start": round(float(v[i0]), 0), "v_ende": round(float(v[i1]), 0),
                                  "laenge_m": round(float(len(run) * rl.ds), 0),
                                  "x": round(float(pos[i0, 0]), 2), "z": round(float(pos[i0, 2]), 2)})
                run = []
    kurven = lg.get("kurven") or []
    ds = float(ml.ds)
    nummern = game_numbers(kurven, len(ml.s))
    tafeln = lg.get("bremsmarker") or []
    table = []
    for i, kv in enumerate(kurven):
        s0, s1, sc = kv["i_anfang"] * ds, kv["i_ende"] * ds, kv["i_scheitel"] * ds
        inside = ((s_ml - s0) % L) <= max(10.0, (s1 - s0) % L)
        zone = next((z for z in zonen if ((s0 - z["s_ende"]) % L) <= 80.0 or ((z["s_ende"] - s0) % L) <= (s1 - s0) % L), None)
        table.append({"nr": i + 1, "spiel": nummern[i], "richtung": kv["richtung"],
                      "winkel": round(abs(float(kv["winkel_grad"])), 0), "r_min": round(float(kv["min_radius_m"]), 1),
                      "s_anfang": round(s0, 1), "s_scheitel": round(sc, 1), "s_ende": round(s1, 1),
                      "v_min": round(float(v[inside].min()), 0) if inside.any() else None,
                      "v_vor": zone["v_start"] if zone else None,
                      "bremsweg_m": zone["laenge_m"] if zone else None,
                      "tafeln": sorted(int(t["meter"]) for t in tafeln if int(t["kurve"]) == i)})
    out = {"rundenzeit_s": float(rl.rundenzeit), "laenge_m": L, "ideallinie_m": float(rl.laenge),
           "v_min_kmh": round(float(v.min()), 0), "v_max_kmh": round(float(v.max()), 0),
           "vollgas_anteil": round(float(np.mean(vollgas)), 3),
           "berechnung_s": round(float((erg.get("bericht") or {}).get("berechnung_s", 0.0)), 1),
           "linie": linie, "bremszonen": zonen, "kurven": table,
           "tafeln": [{"x": round(t["position"][0], 2), "z": round(t["position"][2], 2), "meter": int(t["meter"]),
                       "seite": t["seite"], "kurve": int(t["kurve"])} for t in tafeln],
           "startplaetze": [[round(p["position"][0], 2), round(p["position"][2], 2)] for p in lg.get("startplaetze") or []],
           "boxenplaetze": [[round(p["position"][0], 2), round(p["position"][2], 2)] for p in lg.get("boxenplaetze") or []],
           "sektoren": [{"nummer": s["nummer"], "s_start": round(float(s["s_start_m"]), 1),
                         "s_ende": round(float(s["s_ende_m"]), 1)} for s in lg.get("sektoren") or []],
           "boxengasse": None}
    box = lg.get("boxengasse")
    if box:
        bp = np.asarray(box["position"], float)
        k = np.arange(0, len(bp), max(1, len(bp) // 400))
        out["boxengasse"] = {"x": np.round(bp[k, 0], 2).tolist(), "z": np.round(bp[k, 2], 2).tolist(),
                             "laenge_m": round(float(box["laenge_m"]), 0), "tempolimit_kmh": box.get("tempolimit_kmh")}
    return out


def calculate_analysis(request):
    """Voller Generatorlauf (Ideallinie mit Mindestzeit, Logik) + Prüfung mit Generator; dazu die Auswertung.

    Gleiche Prüfung wie „Prüfen“ und die Vorabprüfung des Baus; das Ergebnis gilt für ``check_key``."""
    root = toolkit_path(request.get("toolkit"))
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    from strecke import definition, erzeugung, pruefregeln

    raw, basis = request["raw"], request.get("basis", ".")
    options = {"streckencode": "sochi", "pvs_variante": "strecke"}
    erg = None
    try:
        erg = erzeugung.berechnen(definition.aus_dict(raw, basisordner=basis))
    except (ValueError, KeyError, TypeError):
        erg = None              # Meldung liefert die Prüfung unten (DEF_UNGUELTIG / Generator)
    report = pruefregeln.vorab_pruefen(raw, basisordner=basis, versatz=request.get("shift", [0, 0, 0]),
                                       generator=erg, mit_generator=erg is None, bauoptionen=options)
    return {"issues": [issue_from_message(m) for m in report["meldungen"]], "report": report,
            "analysis": analysis_summary(erg) if erg is not None else None}


NEW_MANIFEST = {"schema": 1, "definition": "definition.json", "runde_geschlossen": True,
                "thema": "dunkel", "versatz": [0, 0, 0]}


def calculate_template(request):
    """Vorlagen-Import und Zufallsvorschlag (Schritt F): ausschließlich Werkzeugkasten-Module.

    ``task``: ``import`` (``kind`` tum|gpx|spiel, ``path``, ``code`` für spiel, ``fit``, ``spacing``) oder
    ``vorschlag`` (``seed``, ``length_m``, ``curves``, ``min_radius_m``). Antwort: ``raw`` (Rohdefinition),
    ``manifest`` (versatz 0, die Strecke ist auf ±1024 m zentriert) und ``info`` (= ``raw["editor_import"]``)."""
    root = toolkit_path(request.get("toolkit"))
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    if request["task"] == "vorschlag":
        from strecke import vorschlag
        raw = vorschlag.vorschlag(seed=int(request["seed"]), laenge_m=float(request["length_m"]),
                                  kurven=int(request["curves"]), min_radius_m=float(request["min_radius_m"]))
    else:
        from strecke import importe
        kind, path = request["kind"], request["path"]
        fit, spacing = bool(request.get("fit")), request.get("spacing") or None
        if kind == "tum":
            raw = importe.tum_csv(path, punktabstand_m=spacing, einpassen=fit)
        elif kind == "gpx":
            raw = importe.gpx(path, einpassen=fit)
        elif kind == "spiel":
            raw = importe.spielstrecke(path, request.get("code") or "", punktabstand_m=spacing, einpassen=fit)
        else:
            raise ValueError(tr("Unbekannte Vorlagenart: {kind}", kind=kind))
    return {"raw": raw, "manifest": dict(NEW_MANIFEST), "info": raw.get("editor_import", {})}


def run_task(request, timeout=300):
    """Startet den Arbeiterprozess (wie die Vorschau) für eine Anfrage und wartet auf die Antwort.

    Wirft ``ValueError`` mit der Meldung des Werkzeugkastens; blockiert (Sekunden)."""
    root = toolkit_path(request.get("toolkit"))
    request = dict(request, toolkit=str(root))
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    if not getattr(sys, "frozen", False):
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1]) + os.pathsep + env.get("PYTHONPATH", "")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        done = subprocess.run(worker_command(), input=json.dumps(request, ensure_ascii=False).encode("utf-8"),
                              capture_output=True, timeout=timeout, env=env, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(tr("Arbeiterprozess nicht verfügbar: {error}", error=error)) from error
    try:
        answer = json.loads(done.stdout.decode("utf-8"))
    except ValueError:
        raise ValueError(tr("Arbeiterprozess ohne gültige Antwort: ") + done.stderr.decode("utf-8", "replace")[-400:]) from None
    if "error" in answer:
        raise ValueError(answer["error"])
    return answer


def import_template(kind, path, code=None, fit=False, spacing=None, toolkit=None):
    """Neue Rohdefinition aus TUM-CSV (``tum``), GPX-Spur (``gpx``) oder Spielstrecke (``spiel`` + ``code``)."""
    return run_task({"task": "import", "kind": kind, "path": str(path), "code": code, "fit": fit,
                     "spacing": spacing, "toolkit": toolkit})


def suggest_track(seed, length_m=5000.0, curves=14, min_radius_m=30.0, toolkit=None):
    """Zufälliger, gültiger Streckenvorschlag (gleicher Seed und Parameter → gleiche Strecke)."""
    return run_task({"task": "vorschlag", "seed": seed, "length_m": length_m, "curves": curves,
                     "min_radius_m": min_radius_m, "toolkit": toolkit})


# ------------------------------------------------------------------ Umgebung (Vertrag umgebung_vertrag.md)
#: Zustand der Werkzeugkasten-Fassung von ``strecke.umgebung`` im Editorprozess: None = noch nicht versucht,
#: False = nicht vorhanden/nicht ladbar (dann gilt die lokale Fassung ``mapeditor.umgebung``).
_umgebung_werkzeug = None


def _umgebung_modul(toolkit=None, im_prozess=False):
    """Modul ``strecke.umgebung`` des Werkzeugkastens oder None, wenn es (noch) fehlt.

    ``im_prozess``: im Editorprozess nur einmal versuchen (Lesen/Schreiben der Datei); die Arbeitsprozesse
    (Katalog, Vorschau, Prüfung) importieren es selbst."""
    global _umgebung_werkzeug
    if im_prozess and _umgebung_werkzeug is False:
        return None
    try:
        root = toolkit_path(toolkit)
        sys.dont_write_bytecode = True
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from strecke import umgebung
    except (ValueError, ImportError, OSError):
        if im_prozess:
            _umgebung_werkzeug = False
        return None
    except Exception:                                  # defektes Modul darf den Editor nicht stören
        if im_prozess:
            _umgebung_werkzeug = False
        return None
    return umgebung


def umgebung_laden(pfad, toolkit=None):
    """``umgebung.json`` lesen und normalisieren (Werkzeugkasten, sonst lokale Fassung). ``ValueError`` bei Fehlern."""
    from . import umgebung as lokal
    modul = _umgebung_modul(toolkit, im_prozess=True)
    if modul is not None:
        try:
            return lokal.normalisieren(modul.laden(str(pfad)))
        except Exception as fehler:
            raise ValueError(str(fehler)) from fehler
    try:
        return lokal.lade_text(Path(pfad).read_text(encoding="utf-8"))
    except OSError as fehler:
        raise ValueError(f"umgebung.json: {fehler}") from fehler


def umgebung_speichern(daten, pfad, toolkit=None):
    """``umgebung.json`` atomar schreiben (UTF-8, Einrückung 2, feste Schlüsselreihenfolge)."""
    from . import umgebung as lokal
    from .project import atomic_write
    pfad = writable_path(pfad)
    modul = _umgebung_modul(toolkit, im_prozess=True)
    if modul is not None:
        import tempfile
        pfad.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".mapeditor-", suffix=".json", dir=pfad.parent)
        os.close(fd)
        try:
            modul.speichern(lokal.geordnet(daten), name)
            atomic_write(pfad, Path(name).read_bytes())
            return
        except Exception:                              # Werkzeug-Schreiber unbrauchbar: lokale Fassung
            pass
        finally:
            Path(name).unlink(missing_ok=True)
    atomic_write(pfad, lokal.als_text(daten).encode("utf-8"))


def calculate_env_catalog(request):
    """Arbeitsprozess: Katalog der Umgebungsmodelle (Zwischenspeicher des Werkzeugkastens, ``neu`` liest neu ein)."""
    modul = _umgebung_modul(request.get("toolkit"))
    if modul is None:
        raise ValueError(tr("Der Werkzeugkasten enthält noch keine Umgebungsmodelle (strecke/umgebung.py fehlt)."))
    return modul.katalog(spielordner=request.get("spielordner"), neu=bool(request.get("neu")))


def calculate_env_preview(request):
    """Arbeitsprozess: Silhouette eines Modells als PNG (vom Werkzeugkasten zwischengespeichert)."""
    modul = _umgebung_modul(request.get("toolkit"))
    if modul is None:
        raise ValueError(tr("Der Werkzeugkasten enthält noch keine Umgebungsmodelle (strecke/umgebung.py fehlt)."))
    ziel = writable_path(request["pfad"])
    ziel.parent.mkdir(parents=True, exist_ok=True)
    return {"pfad": str(modul.vorschau(request["modell"], str(ziel), int(request.get("groesse", 160))))}


def calculate_env_check(request):
    """Arbeitsprozess: Prüfregeln UMG_* (UM-01 … UM-05) für die Umgebung dieser Definition.

    Zuerst mit der Definition allein (schnell); verlangt die Prüfung eine Generatorausgabe, wird sie berechnet."""
    root = toolkit_path(request.get("toolkit"))
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    modul = _umgebung_modul(request.get("toolkit"))
    if modul is None:
        raise ValueError(tr("Der Werkzeugkasten enthält noch keine Umgebungsmodelle (strecke/umgebung.py fehlt)."))
    from strecke import definition, erzeugung
    shift = request.get("shift", [0, 0, 0])
    defn = definition.aus_dict(request["raw"], basisordner=request.get("basis", "."))
    try:
        meldungen = modul.pruefen(request["umgebung"], defn, shift)
    except (AttributeError, TypeError, KeyError):
        meldungen = modul.pruefen(request["umgebung"], erzeugung.berechnen(defn), shift)
    return {"issues": [issue_from_message(m) for m in meldungen]}


def umgebung_katalog(neu=False, spielordner=None, toolkit=None):
    """Katalog der Umgebungsmodelle (blockierend; die Oberfläche startet dieselbe Anfrage als ``ProcessJob``)."""
    return run_task({"task": "umgebung_katalog", "neu": neu, "spielordner": spielordner, "toolkit": toolkit})


def umgebung_vorschau(modell, pfad_png, groesse=160, toolkit=None):
    """Silhouette eines Modells als PNG (blockierend); Pfad der Datei."""
    return run_task({"task": "umgebung_vorschau", "modell": modell, "pfad": str(pfad_png), "groesse": groesse,
                     "toolkit": toolkit})["pfad"]


def umgebung_pruefen(daten, raw, basis=".", shift=(0, 0, 0), toolkit=None):
    """Prüfregeln UMG_* (blockierend): Meldungen wie die Prüfung der Strecke (``level``, ``ort.objekt`` …)."""
    return run_task({"task": "umgebung_pruefen", "umgebung": daten, "raw": raw, "basis": str(basis),
                     "shift": list(shift), "toolkit": toolkit}, timeout=900)["issues"]


def env_cache_folder():
    """Ordner für Silhouetten (außerhalb von Spiel und Installation)."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "ego-werkzeuge" / "mapeditor_umgebung"


def worker_main():
    try:
        request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        task = request.get("task")
        result = (calculate_analysis(request) if task == "analyse" else
                  calculate_env_catalog(request) if task == "umgebung_katalog" else
                  calculate_env_preview(request) if task == "umgebung_vorschau" else
                  calculate_env_check(request) if task == "umgebung_pruefen" else
                  calculate_template(request) if task else calculate_preview(request))
        sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        return 0
    except Exception as error:
        sys.stdout.buffer.write(json.dumps({"error": str(error)}, ensure_ascii=False).encode("utf-8"))
        return 1


if __name__ == "__main__":
    raise SystemExit(worker_main())
