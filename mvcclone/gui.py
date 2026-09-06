from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .arc import read_arc, unpack, verify_roundtrip
from .clone import (
    CloneSpec, detect_base_name, detect_sound_id, embedded_name_report,
    find_sources, install, max_name_length, run,
)
from .scan import Room, max_safe_length, scan_arc_paths, scan_tree, summarise


class Job(QThread):
    log = pyqtSignal(str)
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn(self.log.emit))
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Clone Engine character porter")
        self.resize(1150, 780)
        self.job: Job | None = None
        self.report = None
        self.spec: CloneSpec | None = None

        self.game_dir = QLineEdit()
        self.game_dir.setPlaceholderText(r"E:\ULTIMATE MARVEL VS. CAPCOM 3")
        pick_game = QPushButton("Browse")
        pick_game.clicked.connect(lambda: self._pick_dir(self.game_dir, "Game install"))

        self.out_dir = QLineEdit(str(Path(tempfile.gettempdir()) / "mvcclone_out"))
        pick_out = QPushButton("Browse")
        pick_out.clicked.connect(lambda: self._pick_dir(self.out_dir, "Staging folder"))

        self.char_id = QLineEdit()
        self.char_id.setPlaceholderText("0033")
        detect = QPushButton("Read name")
        detect.clicked.connect(self.detect_name)

        self.base_name = QLineEdit()
        self.base_name.setPlaceholderText("IronMan, case sensitive")
        self.new_name = QLineEdit()
        self.new_name.setPlaceholderText("PwrSuit")
        self.new_name.textChanged.connect(self.update_length_note)
        self.base_name.textChanged.connect(self.update_length_note)

        self.sound_id = QLineEdit()
        self.sound_id.setPlaceholderText("read from the voice bank")
        self.sound_id.setToolTip(
            "Leave as detected to share the base character's voice. Type a "
            "different three letter ID to give the clone its own sound bank.")
        # Costume arcs run contiguously from 00, so a count says everything a
        # list would. Read name sets this to however many the character has.
        self.costumes = QSpinBox()
        self.costumes.setRange(1, 16)
        self.costumes.setValue(8)
        self.costumes.valueChanged.connect(self.update_colour_count)
        self.colour_count = QLabel()
        self.want_sound = QCheckBox("Clone the voice bank")
        self.want_sound.setChecked(True)
        self.want_ui = QCheckBox("Pull select screen art from mnchs_en.arc")
        self.want_ui.setChecked(True)
        self.underscores = QCheckBox("Rename underscore-delimited names")
        self.underscores.setChecked(True)
        self.fan_out = QCheckBox("Duplicate numbered UI art across costumes")
        self.fan_out.setToolTip(
            "Off by default. The costume arcs already carry per-costume UI. "
            "Only turn this on if the game asks for a numbered texture that "
            "does not exist.")

        form = QFormLayout()
        form.addRow("Game install", self._row(self.game_dir, pick_game))
        form.addRow("Staging folder", self._row(self.out_dir, pick_out))
        form.addRow("Character ID", self._row(self.char_id, detect))
        form.addRow("Base codename", self.base_name)
        form.addRow("Clone name", self.new_name)
        form.addRow("Sound ID", self.sound_id)
        form.addRow("Costumes", self.costumes)
        form.addRow("Colours", self.colour_count)

        opts = QVBoxLayout()
        opts.addWidget(self.want_sound)
        opts.addWidget(self.want_ui)
        opts.addWidget(self.underscores)
        opts.addWidget(self.fan_out)
        opts_box = QGroupBox("Extras")
        opts_box.setLayout(opts)

        self.survey_btn = QPushButton("Survey the name")
        self.survey_btn.clicked.connect(self.survey)
        self.stage_btn = QPushButton("Stage the clone")
        self.stage_btn.clicked.connect(self.stage)
        self.install_btn = QPushButton("Install into the game")
        self.install_btn.setEnabled(False)
        self.install_btn.clicked.connect(self.install_clone)

        buttons = QHBoxLayout()
        buttons.addWidget(self.survey_btn)
        buttons.addWidget(self.stage_btn)
        buttons.addWidget(self.install_btn)
        buttons.addStretch(1)

        self.note = QLabel(
            "Point at your install, give a character ID, read the name off the archive."
        )
        self.note.setWordWrap(True)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["File", "Offset", "Encoding", "Room", "Slack", "Context"]
        )
        self.table.setSortingEnabled(True)

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(2000)

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.console)
        split.setSizes([380, 240])

        left = QVBoxLayout()
        left.addLayout(form)
        left.addWidget(opts_box)
        left.addLayout(buttons)
        left.addWidget(self.note)
        left.addStretch(1)
        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setMaximumWidth(430)

        body = QHBoxLayout()
        body.addWidget(left_widget)
        body.addWidget(split, 1)

        holder = QWidget()
        holder.setLayout(body)
        self.setCentralWidget(holder)
        self.update_colour_count()

    # helpers

    def _row(self, widget, button):
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(widget, 1)
        lay.addWidget(button)
        return w

    def _pick_dir(self, target: QLineEdit, caption: str):
        chosen = QFileDialog.getExistingDirectory(self, caption, target.text() or "")
        if chosen:
            target.setText(chosen)

    def _busy(self, on: bool):
        for b in (self.survey_btn, self.stage_btn):
            b.setEnabled(not on)

    def _start(self, fn, on_done):
        self._busy(True)
        self.job = Job(fn)
        self.job.log.connect(self.console.appendPlainText)
        self.job.done.connect(on_done)
        self.job.failed.connect(self.show_error)
        self.job.finished.connect(lambda: self._busy(False))
        self.job.start()

    def show_error(self, message: str):
        self.note.setText(message)
        self.console.appendPlainText(message)

    def build_spec(self) -> CloneSpec:
        return CloneSpec(
            game_dir=Path(self.game_dir.text().strip()),
            out_dir=Path(self.out_dir.text().strip()),
            char_id=self.char_id.text().strip(),
            base_name=self.base_name.text().strip(),
            new_name=self.new_name.text().strip(),
            costumes=self.costume_slots(),
            sound_id=self.sound_id.text().strip(),
            include_sound=self.want_sound.isChecked(),
            include_ui=self.want_ui.isChecked(),
            underscore_names=self.underscores.isChecked(),
            fan_out_ui=self.fan_out.isChecked(),
        )

    def costume_slots(self) -> list[str]:
        return [f"{i:02d}" for i in range(self.costumes.value())]

    def update_colour_count(self):
        slots = self.costume_slots()
        self.colour_count.setText(f"NumColors={len(slots)}, {slots[0]} to {slots[-1]}")

    def update_length_note(self):
        base, new = self.base_name.text().strip(), self.new_name.text().strip()
        if not base or not new:
            return
        if len(base) == len(new):
            self.note.setText(f"Same length as {base}.")
        else:
            self.note.setText(
                f"{len(new)} characters against {base}'s {len(base)}. "
            )

    # actions

    def detect_name(self):
        spec = self.build_spec()
        sources = find_sources(spec.game_dir, spec.char_id, spec.sound_lang)
        pick = sources.get("cmn") or next(iter(sources.values()), None)
        if pick is None:
            QMessageBox.warning(
                self, "Nothing found",
                f"No archives for {spec.char_id} under that install."
            )
            return
        numbered = sorted(k for k in sources if k.isdigit())
        if numbered:
            self.costumes.setValue(min(len(numbered), self.costumes.maximum()))
            self.console.appendPlainText(
                f"found {len(numbered)} costume arcs, {numbered[0]} to {numbered[-1]}")

        snd = sources.get("sound")
        if snd is not None:
            sid = detect_sound_id(read_arc(snd))
            if sid:
                self.sound_id.setText(sid)
                self.console.appendPlainText(f"sound ID is {sid}")

        name = detect_base_name(read_arc(pick))
        if name:
            self.base_name.setText(name)
            self.console.appendPlainText(f"{pick.name} says the character ID is {name}")
        else:
            self.console.appendPlainText(f"Could not read a character ID out of {pick.name}")

    def survey(self):
        spec = self.build_spec()
        if not spec.base_name:
            QMessageBox.warning(self, "No character ID", "Read the character ID off the archive first.")
            return

        def work(log):
            sources = find_sources(spec.game_dir, spec.char_id, spec.sound_lang)
            log(f"{len(sources)} archives: {', '.join(sorted(sources))}")
            max_name_length(spec, log)
            report = embedded_name_report(spec)
            if report:
                log(f"\n{len(report)} names embedded rather than standalone:")
                for line in report:
                    log(f"   {line}")
                log("")
            all_hits = []
            for suffix, src in sorted(sources.items()):
                ok, detail = verify_roundtrip(src)
                log(f"{src.name} round trip: {detail}")
                if not ok:
                    raise RuntimeError(f"{src.name} does not survive a repack. {detail}")
                arc = read_arc(src)
                out = Path(tempfile.mkdtemp(prefix=f"mvc_{suffix}_"))
                unpack(arc, out)
                hits = scan_arc_paths(arc, spec.base_name) + scan_tree(out, spec.base_name)
                log(f"{src.name}: {len(hits)} references")
                all_hits.extend(hits)
            return all_hits

        self.console.clear()
        self.table.setRowCount(0)
        self._start(work, self.show_survey)

    def show_survey(self, hits):
        base = self.base_name.text().strip()
        cap = max_safe_length(hits, base)
        tight = sum(1 for h in hits if h.room is Room.TIGHT)

        if tight:
            self.note.setText(
                f"{len(hits)} references, {tight} with no padding behind them. "
                f"Those pin you to {len(base)} characters unless a format handler "
                f"rebuilds them."
            )
        else:
            self.note.setText(
                f"{len(hits)} references, all padded. Names up to {cap} characters fit."
            )

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(hits))
        for row, h in enumerate(hits):
            cells = [h.file, "" if h.offset < 0 else f"0x{h.offset:X}", h.encoding,
                     h.room.value, str(h.slack), h.context]
            for col, text in enumerate(cells):
                self.table.setItem(row, col, QTableWidgetItem(text))
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()

        self.console.appendPlainText("")
        for ext, info in sorted(summarise(hits).items()):
            self.console.appendPlainText(
                f"{ext or '(none)':12} {info['count']:5} hits  {info['tight']:5} tight  "
                f"{len(info['files'])} files"
            )

    def stage(self):
        spec = self.build_spec()
        if not (spec.base_name and spec.new_name):
            QMessageBox.warning(self, "Missing names", "Both names are needed.")
            return
        self.spec = spec
        self.console.clear()
        self._start(lambda log: run(spec, log), self.show_stage)

    def show_stage(self, report):
        self.report = report
        for w in report.warnings:
            self.console.appendPlainText(f"warning: {w}")

        if report.ok:
            total = sum(a.content_hits for a in report.arcs)
            self.note.setText(
                f"Staged. {total} references rewritten across {len(report.arcs)} archives. "
            )
            self.install_btn.setEnabled(True)
        else:
            self.note.setText(
                f"{report.total_refusals} references would not fit. "
            )
            self.install_btn.setEnabled(False)
            for a in report.arcs:
                for r in a.refused:
                    self.console.appendPlainText(
                        f"refused {a.label} 0x{r.offset:X}: {r.reason}"
                    )

        self.console.appendPlainText("\ncharacters.ini block:\n" + report.ini_block)

    def install_clone(self):
        if not (self.spec and self.report):
            return
        answer = QMessageBox.question(
            self, "Install",
            "Copy the staged files into the game and append the characters.ini block?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start(
            lambda log: install(self.spec, self.report, log),
            lambda copied: self.note.setText(f"Installed {len(copied)} files."),
        )


def main():
    app = QApplication(sys.argv)
    win = Window()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()