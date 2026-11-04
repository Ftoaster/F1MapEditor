"""Dialog „Trägerpaket einrichten“: Datei suchen, Download öffnen und im Downloads-Ordner erkennen, prüfen, ablegen."""

from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton,
                               QVBoxLayout)

from . import traeger
from .i18n import tr
from .ux import zahl


class CopyJob(QThread):
    """Kopie der großen ZIP im Hintergrund (``traeger.copy_carrier``)."""
    progress = Signal(object)
    failed = Signal(str)
    copied = Signal(bool)

    def __init__(self, source, target, parent=None):
        super().__init__(parent)
        self.source, self.target, self.stop = Path(source), Path(target), False

    def run(self):
        try:
            self.copied.emit(traeger.copy_carrier(self.source, self.target, self.progress.emit, lambda: self.stop))
        except OSError as error:
            self.failed.emit(str(error))


class CarrierDialog(QDialog):
    """Ergebnis: ``result_path`` (eingerichteter Träger) nach ``accept``; sonst bleibt es None."""

    def __init__(self, parent, missing=None, settings=None):
        super().__init__(parent)
        self.settings = settings
        self.result_path = None
        self.offer_path, self.offer_info = None, None
        self.watching = False
        self.sizes = {}
        self.job = None
        self.setWindowTitle(tr("Carrier package (Portimão mod)"))
        self.setAcceptDrops(True)
        self.resize(640, 10)
        layout = QVBoxLayout(self)
        intro = QLabel(tr("Every track is built on top of the Portimão mod for F1 25 (about 2.9 GB, version {recommended} "
                          "recommended). It is free, but the download needs an Overtake account. The editor never asks "
                          "for your login and never downloads the mod itself.", recommended=traeger.RECOMMENDED))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.question = QLabel()
        self.question.setWordWrap(True)
        self.question.setStyleSheet("font-weight: 600;")
        self.question.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.question)
        if missing:
            self.question.setText(tr("The carrier package was not found at {path}.", path=missing) + "\n" +
                                  tr("Did you save it somewhere else, or have you not downloaded it yet?"))
        else:
            current = settings and traeger.saved_carrier(settings)
            self.question.setText(tr("Current carrier package: {path}", path=current) if current else
                                  tr("No carrier package set up yet."))
        row = QHBoxLayout()
        self.choose_button = QPushButton(tr("I have it – choose file …"))
        self.choose_button.clicked.connect(self.choose_file)
        self.download_button = QPushButton(tr("Download from Overtake …"))
        self.download_button.setToolTip(tr("Opens the download page in your browser. If you are logged in there, one "
                                           "click is enough."))
        self.download_button.clicked.connect(self.start_download)
        row.addWidget(self.choose_button)
        row.addWidget(self.download_button)
        row.addStretch(1)
        layout.addLayout(row)
        self.wait_label = QLabel()
        self.wait_label.setWordWrap(True)
        self.wait_label.hide()
        layout.addWidget(self.wait_label)
        self.offer_card = QFrame()
        self.offer_card.setFrameShape(QFrame.StyledPanel)
        card = QVBoxLayout(self.offer_card)
        self.offer_label = QLabel()
        self.offer_label.setWordWrap(True)
        self.offer_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        card.addWidget(self.offer_label)
        self.offer_note = QLabel()
        self.offer_note.setWordWrap(True)
        card.addWidget(self.offer_note)
        buttons = QHBoxLayout()
        self.copy_button = QPushButton(tr("Copy to the carrier folder and use"))
        self.copy_button.setStyleSheet("QPushButton {background: #2F7DD1; color: white; font-weight: 600; padding: 5px 16px;"
                                       " border-radius: 4px;}")
        self.copy_button.clicked.connect(self.copy_and_use)
        self.place_button = QPushButton(tr("Use where it is"))
        self.place_button.clicked.connect(self.use_in_place)
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.place_button)
        buttons.addStretch(1)
        card.addLayout(buttons)
        self.progress = QProgressBar()
        self.progress.hide()
        card.addWidget(self.progress)
        self.offer_card.hide()
        layout.addWidget(self.offer_card)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.hide()
        layout.addWidget(self.message)
        footer = QHBoxLayout()
        footer.addStretch(1)
        self.cancel_button = QPushButton(tr("Cancel"))
        self.cancel_button.clicked.connect(self.reject)
        footer.addWidget(self.cancel_button)
        layout.addLayout(footer)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.poll)

    # ------------------------------------------------------------------ Aufruf
    @staticmethod
    def ask(parent, missing=None, settings=None):
        """Modal ausführen; Pfad des eingerichteten Trägers oder None (abgebrochen)."""
        dialog = CarrierDialog(parent, missing, settings)
        return dialog.result_path if dialog.exec() == QDialog.Accepted else None

    # ------------------------------------------------------------------ Quellen
    def choose_file(self):
        start = str(traeger.downloads_folder())
        value, _ = QFileDialog.getOpenFileName(self, tr("Choose carrier package"), start, tr("ZIP (*.zip)"))
        if value:
            self.offer(Path(value))

    def start_download(self):
        QDesktopServices.openUrl(QUrl(traeger.CARRIER_URL))
        folder = traeger.downloads_folder()
        self.wait_label.setText(tr("Waiting for the download … the editor watches your Downloads folder ({folder}) "
                                   "read-only and offers the file as soon as it is complete. You can also drag the ZIP "
                                   "onto this window.", folder=folder))
        self.wait_label.show()
        self.watching = True
        self.poll()
        self.timer.start()

    def poll(self):
        """Fertige Träger im Downloads-Ordner: erst anbieten, wenn die Größe zwischen zwei Prüfungen gleich blieb."""
        if self.job is not None or self.offer_path is not None:
            return
        try:
            candidates = traeger.find_candidates(traeger.downloads_folder())
        except OSError:
            return
        for path, info in candidates:
            if not info["ok"]:
                continue
            size = info["groesse"]
            if self.sizes.get(path) == size:
                self.offer(path)
                return
            self.sizes[path] = size

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(u.toLocalFile().lower().endswith(".zip") for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.toLocalFile().lower().endswith(".zip"):
                self.offer(Path(url.toLocalFile()))
                return

    # ------------------------------------------------------------------ Angebot prüfen und übernehmen
    def offer(self, path):
        info = traeger.inspect_carrier(path)
        self.message.hide()
        if not info["ok"]:
            self.offer_path = None
            self.offer_card.hide()
            self.show_message(info["problem"] or tr("This file cannot be used as carrier package."), error=True)
            return
        self.offer_path, self.offer_info = Path(path), info
        self.timer.stop()
        self.wait_label.hide()
        author = tr("Mod by {author}", author=info["autor"]) if info["autor"] else ""
        self.offer_label.setText(tr("Found: {name} · version {version} · {groesse}", name=Path(path).name,
                                    version=info["version"], groesse=zahl(info["groesse"] / 1e9, 1, "GB")) +
                                 (" · " + author if author else ""))
        target = traeger.carrier_target(info["version"])
        self.offer_note.setText(tr("Copying keeps the carrier for all projects at {target}; the original stays "
                                   "untouched. “Use where it is” needs no extra space.", target=target))
        if info["version"] != traeger.RECOMMENDED:
            self.offer_note.setText(self.offer_note.text() + "\n" +
                                    tr("Version {recommended} is recommended (it fixes the AMD crash).",
                                       recommended=traeger.RECOMMENDED))
        self.offer_card.show()
        self.progress.hide()
        self.set_busy(False)

    def set_busy(self, busy):
        for button in (self.copy_button, self.place_button, self.choose_button, self.download_button):
            button.setEnabled(not busy)

    def finish(self, path):
        if self.settings is not None:
            traeger.remember_carrier(self.settings, path)
        self.result_path = Path(path)
        self.accept()

    def use_in_place(self):
        if self.offer_path:
            self.finish(self.offer_path)

    def copy_and_use(self):
        if not self.offer_path:
            return
        target = traeger.carrier_target(self.offer_info["version"])
        try:
            if target.is_file() and target.stat().st_size == self.offer_info["groesse"] and traeger.inspect_carrier(target)["ok"]:
                self.finish(target)                    # schon eingerichtet: nichts kopieren
                return
        except OSError:
            pass
        problem = traeger.space_problem(target.parent, self.offer_info["groesse"])
        if problem:
            self.show_message(problem, error=True)
            return
        self.message.hide()
        self.set_busy(True)
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setFormat(tr("Copying … %p %"))
        self.progress.show()
        self.cancel_button.setText(tr("Cancel copy"))
        self.job = CopyJob(self.offer_path, target, self)
        self.job.progress.connect(self.on_progress)
        self.job.copied.connect(self.on_copied)
        self.job.failed.connect(self.on_failed)
        self.job.start()

    def on_progress(self, done):
        total = max(1, self.offer_info["groesse"])
        self.progress.setValue(min(1000, int(done * 1000 / total)))

    def _end_job(self):
        job, self.job = self.job, None
        if job:
            job.wait(3000)
            job.deleteLater()
        self.cancel_button.setText(tr("Cancel"))
        self.set_busy(False)

    def on_copied(self, ok):
        target = self.job.target if self.job else None
        self._end_job()
        if ok and target:
            self.finish(target)
        else:
            self.progress.hide()
            self.show_message(tr("Copy cancelled · nothing changed."))

    def on_failed(self, text):
        self._end_job()
        self.progress.hide()
        self.show_message(tr("Copy failed: {error}", error=text), error=True)

    def show_message(self, text, error=False):
        self.message.setText(text)
        self.message.setStyleSheet("color: #F07878;" if error else "")
        self.message.show()

    def reject(self):
        if self.job is not None:                      # läuft eine Kopie, bricht „Abbrechen“ nur diese ab
            self.job.stop = True
            return
        self.timer.stop()
        super().reject()
