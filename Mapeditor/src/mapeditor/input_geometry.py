"""Sofortige Eingabezeichnung, ohne Spline, Schließkorrektur oder Exportgeometrie.

Die absichtlich ungeschlossene Segmentkette bleibt auch bei Generatorfehlern sichtbar.
Das lokale Abtasten dient ausschließlich Griffen, Eingabeprofilen und Konvertierung.
"""

import math

from .i18n import tr
from .project import point_xyz


def segment_shape(segment):
    kind = segment.get("typ")
    if kind == "gerade":
        return float(segment["laenge"]), 0., 0.
    sign = {"links": 1., "rechts": -1.}[segment["richtung"]]
    if kind == "bogen":
        radius = float(segment["radius"])
        if radius <= 0:
            raise ValueError(tr("Radius must be positive."))
        length = radius * math.radians(float(segment["winkel"])) if segment.get("winkel") is not None else float(segment["laenge"])
        return length, sign / radius, sign / radius
    if kind == "klothoide":
        return float(segment["laenge"]), sign / float(segment["radius_anfang"]) if segment.get("radius_anfang") else 0., sign / float(segment["radius_ende"]) if segment.get("radius_ende") else 0.
    raise ValueError(tr("Unknown segment type: {kind}", kind=kind))


# Klothoiden-Übergänge: gleiche Regeln wie strecke.definition.segmente_mit_uebergaengen
# (Werkzeugkasten bleibt über den Adapter getrennt; tests/test_klothoiden.py vergleicht beide).
TRANSITION_DEFAULTS = {"aktiv": False, "laenge_m": None, "a_faktor": .5, "min_laenge_m": 20.,
                       "max_laenge_m": 150., "max_anteil": .4}
TRANSITION_MIN_M = 2.


def _transition_setting(segment, at_end):
    value = segment.get("uebergang")
    if isinstance(value, dict):
        value = value.get("ende" if at_end else "anfang")
    if value is None or value is True:
        return None
    if value is False:
        return False
    return float(value)


def transition_pieces(segments, rules):
    """Stücke (Länge, k0, k1, Segmentindex) mit mittig eingesetzten Übergängen, dazu die Übergänge."""
    rules = dict(TRANSITION_DEFAULTS, **(rules or {}))
    shapes = [segment_shape(s) for s in segments]
    n = len(shapes)
    lengths, transitions = [0.] * n, []
    for i in range(n if n > 1 else 0):
        j = (i + 1) % n
        ka, kb = shapes[i][2], shapes[j][1]
        if abs(kb - ka) < 1e-9 or "klothoide" in (segments[i].get("typ"), segments[j].get("typ")):
            continue
        settings = [_transition_setting(segments[i], True), _transition_setting(segments[j], False)]
        if False in settings:
            continue
        explicit = [v for v in settings if v is not None]
        if explicit:
            length = max(explicit)
        elif rules.get("laenge_m"):
            length = float(rules["laenge_m"])
        else:
            a = float(rules["a_faktor"]) / max(abs(ka), abs(kb))
            length = min(max(a * a * abs(kb - ka), float(rules["min_laenge_m"])), float(rules["max_laenge_m"]))
        length = min(length, float(rules["max_anteil"]) * min(shapes[i][0], shapes[j][0]))
        if length < TRANSITION_MIN_M:
            continue
        lengths[i] = length
        transitions.append((i, length, kb - ka))
    pieces = []
    for i, (length, k0, k1) in enumerate(shapes):
        front = lengths[i - 1] / 2 if n > 1 else 0.
        back = lengths[i] / 2
        if front > 0:
            pieces.append((front, (shapes[i - 1][2] + k0) / 2, k0, i))
        pieces.append((length - front - back, k0, k1, i))
        if back > 0:
            pieces.append((back, k1, (k1 + shapes[(i + 1) % n][1]) / 2, i))
    return pieces, transitions


def transition_line(ml):
    """Mittellinie mit Klothoiden-Übergängen (ungeschlossen, ohne Schließfehler-Verteilung) oder []."""
    rules = ml.get("uebergaenge")
    if ml.get("art") != "segmente" or not isinstance(rules, dict) or rules.get("aktiv") is not True:
        return []
    try:
        pieces, transitions = transition_pieces(ml.get("segmente", []), rules)
        start = ml.get("start", {})
        x, z = float(start.get("x", 0)), float(start.get("z", 0))
        phi = math.radians(float(start.get("richtung_grad", 0)))
        if transitions and transitions[-1][0] == len(ml["segmente"]) - 1:
            _, length, dk = transitions[-1]
            shift = -dk * length * length / 48
            x, z, phi = x - shift * math.sin(phi), z - shift * math.cos(phi), phi + dk * length / 8
        line = [(x, z)]
        for length, k0, k1, _ in pieces:
            if not math.isfinite(length) or length <= 0:
                return []
            count = max(1, min(512, math.ceil(length / 2)))
            step = length / count
            for j in range(count):
                k = k0 + (k1 - k0) * (j + .5) / count
                turn = k * step
                chord = step if abs(turn) < 1e-12 else 2 * math.sin(turn / 2) / k
                x += chord * math.cos(phi + turn / 2)
                z -= chord * math.sin(phi + turn / 2)
                phi += turn
                line.append((x, z))
        return line
    except (ValueError, TypeError, KeyError, OverflowError, ZeroDivisionError):
        return []


def input_geometry(document):
    ml = document.ml
    positions, stations, objects, object_s, segments = [], [], [], [], []
    sources = []
    error = ""
    if ml.get("art") == "segmente":
        start = ml.get("start", {})
        x, y, z = float(start.get("x", 0)), float(start.get("hoehe", start.get("y", 0))), float(start.get("z", 0))
        phi, station = math.radians(float(start.get("richtung_grad", 0))), 0.
        positions, stations = [(x, y, z)], [0.]
        sources = [{}]
        for index, segment in enumerate(ml.get("segmente", [])):
            try:
                length, k0, k1 = segment_shape(segment)
                if not math.isfinite(length) or length <= 0:
                    raise ValueError(tr("Length must be positive and finite."))
                objects.append((x, y, z))
                object_s.append(station)
                first = len(positions) - 1
                sources[first] = segment
                entry = {"start": (x, y, z), "phi": phi, "first": first, "length": length}
                end_y = float(segment.get("hoehe_ende", y + length * float(segment.get("steigung_prozent", 0)) / 100))
                initial_y = y
                count = max(2, min(512, math.ceil(length / 5)))
                count += count % 2
                step = length / count
                for j in range(count):
                    k = k0 + (k1 - k0) * (j + .5) / count
                    turn = k * step
                    chord = step if abs(turn) < 1e-12 else 2 * math.sin(turn / 2) / k
                    x += chord * math.cos(phi + turn / 2)
                    z -= chord * math.sin(phi + turn / 2)
                    phi += turn
                    y = initial_y + (end_y - initial_y) * (j + 1) / count
                    station += step
                    positions.append((x, y, z))
                    sources.append(segment)
                    stations.append(station)
                entry.update(end=(x, y, z), middle=positions[first + count // 2], last=len(positions) - 1)
                segments.append(entry)
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                error = tr("Segment {index}: {exc}", index=index + 1, exc=exc)
                break
    else:
        positions = [point_xyz(p) for p in document.points]
        for i, p in enumerate(positions):
            stations.append(0. if i == 0 else stations[-1] + math.dist(positions[i - 1], p))
        objects, object_s = positions[:], stations[:]
        sources = [p if isinstance(p, dict) else {} for p in document.points]
    length = stations[-1] if stations else 0.
    if document.project.closed and len(positions) > 1 and ml.get("art") == "kontrollpunkte":
        length += math.dist(positions[-1], positions[0])
    # Unglättete Eingabewerte; der Generator bleibt für Übergänge/Interpolation zuständig.
    road = document.project.raw.get("fahrbahn", {})
    wl, wr, banks = [], [], []
    for station, source in zip(stations, sources):
        left = float(road["breite_links"]) if road.get("breite_links") is not None else float(road.get("breite", 12)) / 2
        right = float(road["breite_rechts"]) if road.get("breite_rechts") is not None else float(road.get("breite", 12)) / 2
        bank = float(road.get("querneigung_grad", 0))
        overrides = [source]
        for section in road.get("abschnitte", []):
            start, end = float(section.get("von_s", 0)), float(section.get("bis_s", length))
            inside = start <= station <= end if end >= start else station >= start or station <= end
            if inside:
                overrides.append(section)
        for value in overrides:
            if value.get("breite") is not None:
                left = right = float(value["breite"]) / 2
            if value.get("breite_links") is not None:
                left = float(value["breite_links"])
            if value.get("breite_rechts") is not None:
                right = float(value["breite_rechts"])
            if value.get("querneigung_grad") is not None:
                bank = float(value["querneigung_grad"])
        wl.append(left)
        wr.append(right)
        banks.append(bank)
    if ml.get("art") == "kontrollpunkte" and banks:
        # Generator: Querneigung linear zwischen Punkten mit Wert (nicht stufenweise); Abschnitte gehen vor.
        from .neigung import point_banks
        interpolated = point_banks(document.points, document.project.closed, float(road.get("querneigung_grad", 0)))
        for i, station in enumerate(stations):
            def inside(section):
                start, end = float(section.get("von_s", 0)), float(section.get("bis_s", length))
                return start <= station <= end if end >= start else station >= start or station <= end
            if not any(section.get("querneigung_grad") is not None and inside(section)
                       for section in road.get("abschnitte", [])):
                banks[i] = interpolated[i]
    left_edge, right_edge, gradient = [], [], []
    for i, p in enumerate(positions):
        a, b = positions[max(0, i - 1)], positions[min(len(positions) - 1, i + 1)]
        dx, dz = b[0] - a[0], b[2] - a[2]
        norm = max(.001, math.hypot(dx, dz))
        gradient.append((b[1] - a[1]) / norm * 100)
        left_edge.append((p[0] + dz / norm * wl[i], p[2] - dx / norm * wl[i]))
        right_edge.append((p[0] - dz / norm * wr[i], p[2] + dx / norm * wr[i]))
    return {"pos": positions, "s": stations, "length": max(length, .001), "bank": banks,
            "raw_y": [p[1] for p in positions], "gradient": gradient,
            "wl": wl, "wr": wr, "left": left_edge, "right": right_edge,
            "objects": objects, "object_s": object_s, "segments": segments, "error": error,
            "transitions": [] if error else transition_line(ml),
            "input": True, "closed": document.project.closed and ml.get("art") == "kontrollpunkte"}
