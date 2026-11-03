"""Bearbeitungsbefehle: eine Übernahme oder Geste entspricht einem Undo-Schritt."""

import copy
import math

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QUndoCommand, QUndoStack

from . import umgebung
from .i18n import tr
from .project import Project, point_xyz, set_point, shifted_point
from .input_geometry import input_geometry, segment_shape


class Change(QUndoCommand):
    """Ein Undo-Schritt. Zustand = (Rohdefinition, Manifest, Umgebung); die Umgebung (``umgebung.json``) ist Teil
    desselben Rückgängig-Verlaufs."""

    def __init__(self, document, text, before, after):
        super().__init__(text)
        self.document, self.before, self.after = document, before, after

    def install(self, state):
        document, project = self.document, self.document.project
        previous_kind = document.ml.get("art")
        raw, manifest, env = copy.deepcopy(state)
        # Änderte sich nur die Umgebung, bleiben Vorschau und Fahrlinie gültig (Revision zählt nicht weiter).
        document.env_only = raw == project.raw and manifest == project.manifest
        # Ebenso nur die Bildvorlage (Stützpunkte, Maßstab, Deckkraft im Manifest unter ``bildvorlage``).
        ohne_bild = lambda m: {k: v for k, v in m.items() if k != "bildvorlage"}
        document.bild_only = (not document.env_only and raw == project.raw and env == project.umgebung
                              and ohne_bild(manifest) == ohne_bild(project.manifest))
        project.raw, project.manifest, project.umgebung = raw, manifest, env
        if previous_kind != document.ml.get("art"):
            document.selected_indices.clear()
        document.normalize_selection()
        document.normalize_env_selection()
        if document.env_only:
            document.env_revision += 1
        elif document.bild_only:
            document.bild_revision += 1
        else:
            document.revision += 1
        document.changed.emit()

    def redo(self):
        self.install(self.after)

    def undo(self):
        self.install(self.before)


class Document(QObject):
    changed = Signal()
    selection_changed = Signal(int)
    env_selection_changed = Signal()

    def __init__(self, project=None):
        super().__init__()
        self.project = project or Project()
        self.undo = QUndoStack(self)
        self.selected = -1
        self.selected_indices = set()
        self.revision = 0
        # Umgebung: Zählung und Auswahl (Kennungen) der Umgebungsobjekte; ``env_only``: letzte Änderung betraf nur sie
        self.env_revision = 0
        self.env_only = False
        # Bildvorlage: Zählung der Änderungen, die nur ``manifest["bildvorlage"]`` betrafen
        self.bild_revision = 0
        self.bild_only = False
        self.env_selected = set()
        self.env_primary = None
        self.terrain_selected = None   # Kennung des gewählten Hügels/Bergs (Einzelauswahl)

    @property
    def ml(self):
        return self.project.raw.get("mittellinie", {})

    @property
    def points(self):
        return self.ml.get("punkte", []) if self.ml.get("art") == "kontrollpunkte" else []

    def select(self, index):
        self.select_many({index}, index)

    @property
    def object_count(self):
        return len(self.ml.get("segmente", [])) if self.ml.get("art") == "segmente" else len(self.points)

    def normalize_selection(self):
        self.selected_indices.intersection_update(range(self.object_count))
        if self.selected not in self.selected_indices:
            self.selected = min(self.selected_indices, default=-1)

    def select_many(self, indices, primary=None):
        self.selected_indices = {i for i in indices if 0 <= i < self.object_count}
        if primary in self.selected_indices:
            self.selected = primary
        self.normalize_selection()
        self.selection_changed.emit(self.selected)

    def move_selection(self, dx=0., dz=0., y=None):
        indices = sorted(self.selected_indices)
        if not self.points or not indices:
            return
        def operation(raw, meta):
            points = raw["mittellinie"]["punkte"]
            for i in indices:
                x, _, z = point_xyz(points[i])
                points[i] = set_point(points[i], x=x + dx, y=y, z=z + dz)
        self.edit(tr("Punktgruppe ändern") if len(indices) > 1 else tr("Punkt verschieben"), operation)

    def edit(self, text, operation):
        """Eine Änderung an Rohdefinition/Manifest als ein Undo-Schritt: ``operation(raw, manifest)``."""
        self._edit(text, lambda raw, meta, env: operation(raw, meta))

    def _edit(self, text, operation):
        """Undo-Schritt über den ganzen Zustand: ``operation(raw, manifest, umgebung)`` (Umgebung kann None sein)."""
        if self.project.readonly:
            return False
        before = copy.deepcopy((self.project.raw, self.project.manifest, self.project.umgebung))
        after = copy.deepcopy(before)
        operation(*after)
        if before != after:
            self.undo.push(Change(self, text, before, after))
            return True
        return False

    def edit_bild(self, text, operation):
        """Eine Änderung der Bildvorlage als ein Undo-Schritt: ``operation(vorlage)`` ändert das Wörterbuch
        ``manifest["bildvorlage"]`` (muss bestehen)."""
        def change(raw, meta):
            if isinstance(meta.get("bildvorlage"), dict):
                operation(meta["bildvorlage"])
        return self.edit(text, change)

    def replace_content(self, text, operation):
        """Rohdefinition und Umgebung ersetzen (Wiederherstellung): ``operation(raw, manifest, umgebung)`` ändert
        raw/manifest und gibt die neue Umgebung zurück."""
        if self.project.readonly:
            return False
        before = copy.deepcopy((self.project.raw, self.project.manifest, self.project.umgebung))
        after = copy.deepcopy(before)
        env = operation(*after)
        after = (after[0], after[1], env)
        if before != after:
            self.undo.push(Change(self, text, before, after))
            return True
        return False

    # ------------------------------------------------------------------ Umgebung
    @property
    def env(self):
        """Umgebungsdaten des Projekts (leere Fassung ohne Bodenbewuchs, solange es keine Datei gibt; sie entsteht
        erst beim ersten Ändern)."""
        return self.project.umgebung if self.project.umgebung is not None else umgebung.neu(False)

    @property
    def env_objects(self):
        return self.env["objekte"]

    @property
    def env_editable(self):
        return not self.project.readonly and not self.project.umgebung_fehler

    def env_object(self, kennung):
        return next((o for o in self.env_objects if o["id"] == kennung), None)

    def edit_env(self, text, operation):
        """Eine Änderung der Umgebung als ein Undo-Schritt: ``operation(umgebung_dict)``. Gibt es noch keine Datei,
        entsteht sie dabei (Bodenbewuchs dann an)."""
        if not self.env_editable:
            return False
        before = copy.deepcopy((self.project.raw, self.project.manifest, self.project.umgebung))
        env = copy.deepcopy(before[2]) if before[2] is not None else umgebung.neu(True)
        operation(env)
        after = (copy.deepcopy(before[0]), copy.deepcopy(before[1]), env)
        if before != after:
            self.undo.push(Change(self, text, before, after))
            return True
        return False

    def normalize_env_selection(self):
        kennungen = {o["id"] for o in self.env_objects}
        self.env_selected &= kennungen
        if self.env_primary not in self.env_selected:
            self.env_primary = min(self.env_selected, default=None)
        if self.terrain_selected is not None and self.terrain_form(self.terrain_selected) is None:
            self.terrain_selected = None

    def env_select(self, kennungen, primary=None):
        """Umgebungsobjekte wählen (nur vorhandene Kennungen); Signal nur bei Änderung."""
        alt = (set(self.env_selected), self.env_primary)
        vorhanden = {o["id"] for o in self.env_objects}
        self.env_selected = set(kennungen) & vorhanden
        self.env_primary = primary if primary in self.env_selected else None
        if self.env_selected:
            self.terrain_selected = None
        self.normalize_env_selection()
        if (self.env_selected, self.env_primary) != alt:
            self.env_selection_changed.emit()

    # ------------------------------------------------------------------ Gelände (Hügel, Berge)
    @property
    def terrain_forms(self):
        """Liste ``(art, form)`` aller Hügel und Berge."""
        return umgebung.formen(self.env)

    def terrain_form(self, kennung):
        return next((f for _, f in self.terrain_forms if f["id"] == kennung), None)

    def terrain_select(self, kennung):
        """Einen Hügel/Berg wählen (None hebt auf); hebt die Objektauswahl auf. Signal ``env_selection_changed``."""
        kennung = kennung if kennung is not None and self.terrain_form(kennung) is not None else None
        alt = (self.terrain_selected, set(self.env_selected))
        self.terrain_selected = kennung
        if kennung is not None:
            self.env_selected, self.env_primary = set(), None
        if (self.terrain_selected, self.env_selected) != alt:
            self.env_selection_changed.emit()

    def terrain_place(self, art, x, z, **werte):
        """Neuen Hügel (``art="huegel"``) oder Berg (``"berge"``) setzen und wählen."""
        if not self.env_editable:
            return None
        form = umgebung.neue_form(umgebung.alle_ids(self.env), art, x, z, **werte)

        def operation(env):
            env.setdefault("gelaende", {}).setdefault(art, []).append(copy.deepcopy(form))
        if self.edit_env(tr("Hügel setzen") if art == "huegel" else tr("Berg setzen"), operation):
            self.terrain_select(form["id"])
            return form
        return None

    def _terrain_edit(self, kennung, text, change):
        def operation(env):
            for art, f in umgebung.formen(env):
                if f["id"] == kennung:
                    change(art, f)
        return self.edit_env(text, operation)

    def terrain_move(self, kennung, dx, dz):
        if abs(dx) < 1e-9 and abs(dz) < 1e-9:
            return
        self._terrain_edit(kennung, tr("Gelände verschieben"), lambda art, f: f.update(
            x=round(f["x"] + dx, 1), z=round(f["z"] + dz, 1)))

    def terrain_set(self, kennung, **felder):
        """``hoehe_m``/``radius_m`` (auf die Grenzen der Art begrenzt), ``x``/``z``."""
        def change(art, f):
            for feld, wert in felder.items():
                f[feld] = umgebung.form_wert(art, feld, wert) if feld in ("hoehe_m", "radius_m") else round(float(wert), 1)
        self._terrain_edit(kennung, tr("Gelände ändern"), change)

    def terrain_delete(self, kennung=None):
        kennung = self.terrain_selected if kennung is None else kennung
        if kennung is None:
            return

        def operation(env):
            for art in umgebung.FORM_ARTEN:
                liste = (env.get("gelaende") or {}).get(art)
                if liste is not None:
                    liste[:] = [f for f in liste if f["id"] != kennung]
        self.edit_env(tr("Gelände löschen"), operation)

    def env_add(self, objekte, text=None, select=True):
        """Neue Objekte (fertige Einträge) in einem Undo-Schritt anlegen und wählen."""
        objekte = [umgebung.normalisiere_objekt(o) for o in objekte]
        if not objekte or not self.env_editable:
            return []
        done = self.edit_env(text or (tr("Umgebungsobjekt platzieren") if len(objekte) == 1 else tr("Umgebung bepflanzen")),
                             lambda env: env["objekte"].extend(copy.deepcopy(objekte)))
        if done and select:
            self.env_select({o["id"] for o in objekte}, objekte[-1]["id"])
        return objekte if done else []

    def env_place(self, modell, x, z, **werte):
        """Ein Objekt am Punkt (x, z) platzieren; Kennung und Rundung vergibt das Dokument."""
        objekt = umgebung.neues_objekt(umgebung.alle_ids(self.env), modell, x, z, **werte)
        self.env_add([objekt], tr("Umgebungsobjekt platzieren"))
        return objekt

    def env_move(self, kennungen, dx, dz):
        kennungen = set(kennungen)
        if not kennungen or (abs(dx) < 1e-9 and abs(dz) < 1e-9):
            return

        def operation(env):
            for o in env["objekte"]:
                if o["id"] in kennungen:
                    o["x"], o["z"] = round(o["x"] + dx, 2), round(o["z"] + dz, 2)
        self.edit_env(tr("Umgebungsobjekte verschieben") if len(kennungen) > 1 else tr("Umgebungsobjekt verschieben"), operation)

    def env_delete(self, kennungen=None):
        """Objekte löschen (Standard: die Auswahl)."""
        kennungen = set(self.env_selected if kennungen is None else kennungen)
        if not kennungen:
            return
        self.edit_env(tr("Umgebungsobjekte löschen") if len(kennungen) > 1 else tr("Umgebungsobjekt löschen"),
                      lambda env: env.__setitem__("objekte", [o for o in env["objekte"] if o["id"] not in kennungen]))

    def env_set(self, kennungen, **felder):
        """Felder der Objekte setzen (``modell``, ``hoehe_m``, ``drehung_grad``, ``skala``, ``gruppe``); Werte werden begrenzt."""
        kennungen = set(kennungen)
        felder = dict(felder)
        if "skala" in felder:
            felder["skala"] = round(min(max(float(felder["skala"]), umgebung.SKALA_MIN), umgebung.SKALA_MAX), 3)
        if "hoehe_m" in felder:
            felder["hoehe_m"] = round(min(max(float(felder["hoehe_m"]), umgebung.HOEHE_MIN), umgebung.HOEHE_MAX), 3)
        if "drehung_grad" in felder:
            felder["drehung_grad"] = round(float(felder["drehung_grad"]) % 360.0, 3)

        def operation(env):
            for o in env["objekte"]:
                if o["id"] in kennungen:
                    o.update(felder)
        self.edit_env(tr("Umgebungsobjekt ändern") if len(kennungen) == 1 else tr("Umgebungsobjekte ändern"), operation)

    def env_set_ground_cover(self, an):
        self.edit_env(tr("Bodenbewuchs ändern"), lambda env: env.__setitem__("bodenbewuchs", bool(an)))

    def env_group(self, gruppe):
        """Kennungen aller Objekte einer Gruppe."""
        return {o["id"] for o in self.env_objects if gruppe and o.get("gruppe") == gruppe}

    def move(self, index, x=None, y=None, z=None):
        if not 0 <= index < len(self.points):
            return
        self.edit(tr("Punkt verschieben"), lambda raw, meta: raw["mittellinie"]["punkte"].__setitem__(
            index, set_point(raw["mittellinie"]["punkte"][index], x, y, z)))

    def insert(self, x=None, z=None):
        if self.ml.get("art") == "segmente":
            self.insert_segment()
            return
        if self.ml.get("art") != "kontrollpunkte":
            return
        index = min(self.selected + 1, len(self.points)) if self.selected >= 0 else len(self.points)
        if x is None:
            if self.points:
                a = point_xyz(self.points[max(0, index - 1)])
                b = point_xyz(self.points[index % len(self.points)])
                x, y, z = ((a[k] + b[k]) / 2 for k in range(3))
                if not self.project.closed and index == len(self.points):
                    x, y, z = a[0] + 20, a[1], a[2]
            else:
                x, y, z = 0, 0, 0
        else:
            y = point_xyz(self.points[max(0, index - 1)])[1] if self.points else 0
        self.edit(tr("Punkt einfügen"), lambda raw, meta: raw["mittellinie"]["punkte"].insert(index, [x, z, y]))
        self.select(index)

    def delete(self):
        index = self.selected
        if len(self.selected_indices) > 1:
            indices = sorted(self.selected_indices, reverse=True)
            def operation(raw, meta):
                key = "segmente" if self.ml.get("art") == "segmente" else "punkte"
                for i in indices:
                    raw["mittellinie"][key].pop(i)
                if key == "punkte" and len(raw["mittellinie"][key]) < 4:
                    meta["runde_geschlossen"] = False
            self.edit(tr("Auswahl löschen"), operation)
            self.select(min(indices[-1], self.object_count - 1))
            return
        if self.ml.get("art") == "segmente":
            if 0 <= index < len(self.ml.get("segmente", [])):
                self.edit(tr("Segment löschen"), lambda raw, meta: raw["mittellinie"]["segmente"].pop(index))
                self.select(min(index, len(self.ml["segmente"]) - 1))
            return
        if 0 <= index < len(self.points):
            def operation(raw, meta):
                raw["mittellinie"]["punkte"].pop(index)
                if len(raw["mittellinie"]["punkte"]) < 4:
                    meta["runde_geschlossen"] = False
            self.edit(tr("Punkt löschen"), operation)
            self.select(min(index, len(self.points) - 1))

    def insert_segment(self, kind="gerade"):
        index = self.selected + 1 if self.selected >= 0 else len(self.ml.get("segmente", []))
        segment = {"typ": "gerade", "laenge": 100.} if kind == "gerade" else {"typ": "bogen", "radius": 60., "winkel": 45., "richtung": "rechts"}
        self.edit(tr("Segment einfügen"), lambda raw, meta: raw["mittellinie"]["segmente"].insert(index, segment))
        self.select(index)

    @staticmethod
    def change_type(segment, kind):
        try:
            length, _, _ = segment_shape(segment)
        except (ValueError, TypeError, KeyError, ZeroDivisionError):
            length = 100.
        length = max(.1, length)
        segment["typ"] = kind
        if kind == "gerade":
            segment["laenge"] = length
        elif kind == "bogen":
            segment.update(radius=60., winkel=math.degrees(length / 60.), richtung=segment.get("richtung", "rechts"))
        else:
            raise ValueError(tr("Bitte Gerade oder Bogen wählen."))

    def switch_segment(self):
        i = self.selected
        if self.ml.get("art") == "segmente" and 0 <= i < len(self.ml["segmente"]):
            kind = "bogen" if self.ml["segmente"][i]["typ"] == "gerade" else "gerade"
            self.edit(tr("Segmentart wechseln"), lambda raw, meta: self.change_type(raw["mittellinie"]["segmente"][i], kind))

    def split_segment(self):
        i = self.selected
        geometry = input_geometry(self)
        if not 0 <= i < len(geometry["segments"]):
            return
        def operation(raw, meta):
            segments = raw["mittellinie"]["segmente"]
            a, b = copy.deepcopy(segments[i]), copy.deepcopy(segments[i])
            length, _, _ = segment_shape(a)
            if a["typ"] == "bogen" and a.get("winkel") is not None:
                a["winkel"] = b["winkel"] = float(a["winkel"]) / 2
            else:
                a["laenge"] = b["laenge"] = length / 2
            if a["typ"] == "klothoide":
                _, k0, k1 = segment_shape(segments[i])
                radius = None if abs(k0 + k1) < 1e-12 else abs(2 / (k0 + k1))
                a["radius_ende"], b["radius_anfang"] = radius, radius
            if "hoehe_ende" in a:
                a["hoehe_ende"] = geometry["segments"][i]["middle"][1]
            segments[i:i + 1] = [a, b]
        self.edit(tr("Segment teilen"), operation)

    def segment_drag_changes(self, index, point, handle="end", geometry=None):
        # Beim Ziehen reicht die Geometrie vom Gestenbeginn (das Dokument ändert sich erst beim Loslassen).
        geometry = geometry or input_geometry(self)
        if not 0 <= index < len(geometry["segments"]):
            return {}
        entry, segment = geometry["segments"][index], self.ml["segmente"][index]
        x, _, z = entry["start"]
        phi = entry["phi"]
        dx, dz = point.x() - x, point.y() - z
        along, left = dx * math.cos(phi) - dz * math.sin(phi), -dx * math.sin(phi) - dz * math.cos(phi)
        changes = {}
        if segment["typ"] != "bogen":
            changes["laenge"] = max(.1, along)
        elif handle == "middle":
            old = entry["middle"]
            factor = math.hypot(dx, dz) / max(.001, math.hypot(old[0] - x, old[2] - z))
            changes["radius"] = max(.1, float(segment["radius"]) * factor)
        else:
            radius = (along * along + left * left) / max(.001, 2 * abs(left))
            angle = math.degrees(2 * math.atan2(abs(left), along))
            changes.update(radius=max(.1, radius), winkel=max(.1, min(359.9, angle)), richtung="links" if left > 0 else "rechts")
        return {key: round(value, 3) if isinstance(value, float) else value for key, value in changes.items()}

    def drag_segment(self, index, point, handle="end", geometry=None):
        changes = self.segment_drag_changes(index, point, handle, geometry)
        if not changes:
            return
        self.edit(tr("Segmentgriff ziehen"), lambda raw, meta: raw["mittellinie"]["segmente"][index].update(changes))

    def gaps(self, factor=4.0, minimum=20.0):
        """Lücken zwischen aufeinanderfolgenden Kontrollpunkten: [(i, Abstand)] mit Abstand > max(minimum,
        factor × Median). i = Index des ersten Punkts; bei geschlossener Runde auch letzter → erster."""
        pts = [point_xyz(p) for p in self.points]
        n = len(pts)
        if n < 4:
            return []
        count = n if self.project.closed else n - 1
        dist = [math.dist((pts[i][0], pts[i][2]), (pts[(i + 1) % n][0], pts[(i + 1) % n][2])) for i in range(count)]
        median = sorted(dist)[len(dist) // 2]
        limit = max(minimum, factor * median)
        return [(i, d) for i, d in enumerate(dist) if d > limit]

    def gap_step(self):
        """Punktabstand beim Füllen: der übliche Abstand der Strecke, begrenzt auf 2–20 m."""
        pts = [point_xyz(p) for p in self.points]
        n = len(pts)
        dist = sorted(math.dist((pts[i][0], pts[i][2]), (pts[(i + 1) % n][0], pts[(i + 1) % n][2])) for i in range(n))
        return max(2.0, min(20.0, dist[n // 2]))

    def gap_targets(self):
        """Was „Lücken füllen“ bearbeitet: {i: Abstand}. Zwei gewählte Nachbarpunkte mit Platz für mindestens einen neuen
        Punkt: nur diese Lücke, sonst alle großen Lücken (``gaps``)."""
        chosen = sorted(self.selected_indices)
        n = len(self.points)
        if len(chosen) == 2:
            a, b = chosen
            i = a if b == a + 1 else b if a == 0 and b == n - 1 and self.project.closed else None
            if i is not None:
                p, q = point_xyz(self.points[i]), point_xyz(self.points[(i + 1) % n])
                distance = math.dist((p[0], p[2]), (q[0], q[2]))
                if distance >= 1.5 * self.gap_step():
                    return {i: distance}
        return dict(self.gaps())

    def fill_gaps(self, fills):
        """Füllt Lücken in einem Undo-Schritt. ``fills``: {i: [(x, y, z), …]} – Punkte zwischen Punkt i und i+1."""
        fills = {i: pts for i, pts in fills.items() if pts}
        if not fills or self.ml.get("art") != "kontrollpunkte":
            return 0
        def operation(raw, meta):
            punkte = raw["mittellinie"]["punkte"]
            for i in sorted(fills, reverse=True):          # von hinten, damit Indizes gültig bleiben
                vorlage = punkte[i] if isinstance(punkte[i], dict) else {}
                neu = []
                for x, y, z in fills[i]:
                    p = {"x": round(x, 3), "y": round(y, 3), "z": round(z, 3)}
                    for key in ("breite", "breite_links", "breite_rechts", "querneigung_grad"):
                        if key in vorlage:
                            p[key] = vorlage[key]
                    neu.append(p)
                punkte[i + 1:i + 1] = neu
        self.edit(tr("Lücken füllen"), operation)
        return sum(len(v) for v in fills.values())

    def rotatable(self):
        """Was die Drehgeste dreht: gewählte Kontrollpunkte (ab 2) oder die ganze Segmentkette
        (nur wenn alle Segmente gewählt sind, da Segmente relativ zum Start liegen)."""
        if self.project.readonly:
            return None
        if self.ml.get("art") == "kontrollpunkte" and len(self.selected_indices) >= 2:
            return "punkte"
        if self.ml.get("art") == "segmente" and self.object_count and len(self.selected_indices) == self.object_count:
            return "strecke"
        return None

    def rotated_ml(self, angle, cx, cz):
        """Mittellinie (flache Kopie) um ``angle`` (Bogenmaß, in der Draufsicht im Uhrzeigersinn) um (cx, cz) gedreht."""
        c, s = math.cos(angle), math.sin(angle)
        ml = dict(self.ml)
        if self.rotatable() == "punkte":
            points = list(ml["punkte"])
            for i in self.selected_indices:
                x, _, z = point_xyz(points[i])
                dx, dz = x - cx, z - cz
                points[i] = shifted_point(points[i], cx + dx * c - dz * s, cz + dx * s + dz * c)
            ml["punkte"] = points
        elif self.rotatable() == "strecke":
            start = dict(ml.get("start", {}))
            dx, dz = float(start.get("x", 0)) - cx, float(start.get("z", 0)) - cz
            start["x"], start["z"] = cx + dx * c - dz * s, cz + dx * s + dz * c
            # Richtung phi: Vorwärtsvektor (cos phi, -sin phi) in x/z → Drehung um +angle heißt phi - angle.
            start["richtung_grad"] = (float(start.get("richtung_grad", 0)) - math.degrees(angle) + 180) % 360 - 180
            ml["start"] = start
        return ml

    def moved_track_ml(self, dx, dz):
        """Mittellinie (flache Kopie) einer ganz gewählten Segmentstrecke um (dx, dz) verschoben."""
        ml = dict(self.ml)
        start = dict(ml.get("start", {}))
        start["x"], start["z"] = float(start.get("x", 0)) + dx, float(start.get("z", 0)) + dz
        ml["start"] = start
        return ml

    def move_track(self, dx, dz):
        if self.rotatable() != "strecke" or (abs(dx) < 1e-12 and abs(dz) < 1e-12):
            return
        def operation(raw, meta):
            start = raw["mittellinie"].setdefault("start", {})
            start["x"] = round(float(start.get("x", 0)) + dx, 4)
            start["z"] = round(float(start.get("z", 0)) + dz, 4)
        self.edit(tr("Strecke verschieben"), operation)

    def rotate_selection(self, angle, cx, cz):
        kind = self.rotatable()
        if kind is None or abs(angle) < 1e-9:
            return
        ml = self.rotated_ml(angle, cx, cz)
        def operation(raw, meta):
            target = raw["mittellinie"]
            if kind == "punkte":
                for i in self.selected_indices:
                    x, _, z = point_xyz(ml["punkte"][i])
                    target["punkte"][i] = set_point(target["punkte"][i], x=round(x, 4), z=round(z, 4))
            else:
                start = target.setdefault("start", {})
                for key in ("x", "z", "richtung_grad"):
                    start[key] = round(ml["start"][key], 4)
        self.edit(tr("Strecke drehen") if kind == "strecke" else tr("Punktgruppe drehen"), operation)

    def convert_to_points(self):
        geometry = input_geometry(self)
        if geometry["error"] or not geometry["segments"]:
            raise ValueError(geometry["error"] or tr("Keine Segmentkette vorhanden."))
        def operation(raw, meta):
            ml = raw["mittellinie"]
            ml["editor_segmentquelle"] = {"start": copy.deepcopy(ml.get("start", {})), "segmente": ml.pop("segmente")}
            ml["art"] = "kontrollpunkte"
            ml["punkte"] = []
            for segment, entry in zip(ml["editor_segmentquelle"]["segmente"], geometry["segments"]):
                for x, y, z in geometry["pos"][entry["first"]:entry["last"]]:
                    point = {"x": x, "y": y, "z": z}
                    for key in ("breite", "breite_links", "breite_rechts", "querneigung_grad"):
                        if key in segment:
                            point[key] = segment[key]
                    ml["punkte"].append(point)
            last = geometry["pos"][-1]
            # Eine fast geschlossene Kette braucht keinen zusätzlichen Millimeter-
            # Abstand am Rundenschluss des anschließenden Kontrollpunktsplines.
            close_tolerance = min(1., math.dist(last, geometry["pos"][-2]) / 4) if self.project.closed else 0.
            if math.dist(last, geometry["pos"][0]) > max(.01, close_tolerance) or not self.project.closed:
                ml["punkte"].append({"x": last[0], "y": last[1], "z": last[2]})
        self.edit(tr("In Kontrollpunkte umwandeln"), operation)
        self.select(0)

    def close_round(self):
        if not self.project.closed and self.ml.get("art") == "kontrollpunkte" and len(self.points) < 4:
            raise ValueError(tr("Zum Schließen sind mindestens vier Kontrollpunkte erforderlich."))
        self.edit(tr("Rundenschluss ändern"), lambda raw, meta: meta.update(runde_geschlossen=not self.project.closed))

    # ------------------------------------------------------------------ Kopieren und Einfügen (Issue #2)
    def _nur_punkte(self, text):
        if self.project.readonly:
            raise ValueError(tr("Unbekannte Projektfassung: nur lesend."))
        if self.ml.get("art") != "kontrollpunkte":
            raise ValueError(text)

    def paste_points(self, punkte):
        """Kopierte Punkte an das passende offene Ende binden (ein Undo-Schritt); danach sind sie gewählt.
        Rückgabe: Plan (``kopieren.einfuegen_plan``) samt ``ueberschneidungen`` (Indizes der neuen Punkte)."""
        from . import kopieren
        self._nur_punkte(tr("Einfügen geht nur in Kontrollpunkt-Strecken: Segmente erst in Kontrollpunkte umwandeln."))
        plan = kopieren.einfuegen_plan(self.points, punkte, self.project.closed)
        def operation(raw, meta):
            raw["mittellinie"]["punkte"] = copy.deepcopy(plan["punkte"])
            kopieren.stationen_verschieben(raw, plan["versatz_m"])
        self.edit(tr("Punkte einfügen"), operation)
        a, b = plan["neu"]
        self.select_many(range(a, b + 1), a if plan["hinten"] else b)
        plan["ueberschneidungen"] = kopieren.ueberschneidungen(
            self.points, plan["neu"], kopieren.mindestabstand(self.project.raw)) if len(self.points) > b - a + 1 else []
        return plan

    def open_here(self):
        """Geschlossene Runde an der Auswahl öffnen: hinter einem Punkt bzw. gewählte Punkte entfernen und dort öffnen.
        Streckenmeter ab dem ersten Punkt wandern mit (fester Versatz modulo Rundenlänge)."""
        from . import kopieren
        self._nur_punkte(tr("„Runde hier öffnen“ geht nur bei Kontrollpunkten."))
        if not self.project.closed:
            raise ValueError(tr("Die Runde ist schon offen."))
        punkte, versatz, laenge = kopieren.oeffnen_plan(self.points, self.selected_indices)
        def operation(raw, meta):
            raw["mittellinie"]["punkte"] = copy.deepcopy(punkte)
            kopieren.stationen_verschieben(raw, -versatz, laenge)
            meta["runde_geschlossen"] = False
        self.edit(tr("Runde hier öffnen"), operation)
        self.select(len(punkte) - 1)

    def reverse_selection(self):
        """Reihenfolge eines zusammenhängenden Stücks umkehren (z. B. ein falsch herum angebundenes Stück)."""
        from . import kopieren
        self._nur_punkte(tr("Umkehren geht nur bei Kontrollpunkten."))
        folge = kopieren.kette(self.selected_indices, len(self.points), self.project.closed)
        if folge is None or len(folge) < 2:
            raise ValueError(tr("Zum Umkehren mindestens zwei zusammenhängende Punkte wählen."))
        def operation(raw, meta):
            punkte = raw["mittellinie"]["punkte"]
            alt = [punkte[i] for i in folge]
            for i, p in zip(folge, reversed(alt)):
                punkte[i] = p
        self.edit(tr("Reihenfolge umkehren"), operation)

    def cut_overhang(self):
        """Gewähltes Stück an einem offenen Ende ab der ersten Überschneidung mit der Strecke kürzen.
        Rückgabe: Anzahl entfernter Punkte."""
        from . import kopieren
        self._nur_punkte(tr("„Überstand abschneiden“ geht nur bei Kontrollpunkten."))
        punkte, weg, versatz = kopieren.ueberstand_plan(self.points, self.selected_indices,
                                                         kopieren.mindestabstand(self.project.raw), self.project.closed)
        rest = sorted(set(self.selected_indices) - set(weg))
        vorne = 0 in weg
        def operation(raw, meta):
            raw["mittellinie"]["punkte"] = copy.deepcopy(punkte)
            kopieren.stationen_verschieben(raw, versatz)
        self.edit(tr("Überstand abschneiden"), operation)
        verschiebung = len(weg) if vorne else 0
        self.select_many([i - verschiebung for i in rest], None)
        return len(weg)

    # ------------------------------------------------------------------ Skalieren
    def scale_track(self, faktor):
        """Ganze Strecke um ``faktor`` um ihre Mitte skalieren (ein Undo-Schritt, Umgebung inklusive)."""
        from . import skalieren
        if self.project.readonly:
            raise ValueError(tr("Unbekannte Projektfassung: nur lesend."))
        geometrie = input_geometry(self)
        if geometrie["error"] or len(geometrie["pos"]) < 2:
            raise ValueError(geometrie["error"] or tr("Keine Strecke zum Skalieren."))
        faktor = skalieren.faktor_pruefen(faktor)
        def operation(raw, meta, env):
            skalieren.anwenden(raw, env, geometrie["pos"], faktor)
        self._edit(tr("Strecke skalieren"), operation)
        return faktor
