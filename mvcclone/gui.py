from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QGridLayout, QSpinBox, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget,
)

from . import csa, msd, stqr
from .arc import read_arc
from .clone import (
    ROSTER, CloneSpec, assist_slot_names, bgm_stream_index, bgm_stream_owners,
    bgm_stream_plan,
    character_list, detect_base_name, detect_sound_id, find_characters_ini,
    find_sources, install, max_name_length, run,
)


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
        self.name_limit = 0
        self.characters_ini = ""
        self.report = None
        self.spec: CloneSpec | None = None

        self.game_dir = QLineEdit()
        self.game_dir.setPlaceholderText(r"E:\ULTIMATE MARVEL VS. CAPCOM 3")
        pick_game = QPushButton("Browse")
        pick_game.clicked.connect(lambda: self._pick_dir(self.game_dir, "Game install"))
        self.game_dir.editingFinished.connect(self.detect_name)
        self.game_dir.editingFinished.connect(self.refresh_assist_names)

        self.out_dir = QLineEdit(str(Path(tempfile.gettempdir()) / "mvcclone_out"))
        pick_out = QPushButton("Browse")
        pick_out.clicked.connect(lambda: self._pick_dir(self.out_dir, "Staging folder"))

        self.char_id = QComboBox()
        self.char_id.setEditable(True)
        self.char_id.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for cid, label in ROSTER:
            self.char_id.addItem(f"{cid} - {label}", cid)
        self.char_id.setCurrentIndex(-1)
        self.char_id.lineEdit().setPlaceholderText("Type a character ID or pick one from the list")
        self.char_id.currentIndexChanged.connect(self.detect_name)
        self.char_id.lineEdit().editingFinished.connect(self.detect_name)
        detect = QPushButton("Read name")
        detect.clicked.connect(self.detect_name)

        self.base_name = QLineEdit()
        self.base_name.setPlaceholderText("IronMan, case sensitive")
        self.new_name = QLineEdit()
        self.new_name.setPlaceholderText("PwrSuit")
        self.new_name.textChanged.connect(self.update_length_note)
        self.base_name.textChanged.connect(self.update_length_note)
        self.new_name.textChanged.connect(self.refresh_buttons)
        self.base_name.textChanged.connect(self.refresh_buttons)

        self.sound_id = QLineEdit()
        self.sound_id.setMaxLength(3)
        self.sound_id.textChanged.connect(self.refresh_buttons)
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
        self.costumes.valueChanged.connect(self.refresh_buttons)
        self.colour_count = QLabel()
        self.want_sound = QCheckBox("Clone the voice bank")
        self.want_sound.setChecked(True)
        self.want_ui = QCheckBox("Pull select screen art from mnchs_en.arc")
        self.want_ui.setChecked(True)
        self.underscores = QCheckBox("Rename underscore-delimited names")
        self.underscores.setChecked(True)
        self.ui_255 = QCheckBox("Also write a 255 copy of every 99 UI texture")
        self.ui_255.setToolTip(
            "Writes b_Name255 alongside b_Name99.")
        self.fan_out = QCheckBox("Duplicate numbered UI art across costumes")
        self.fan_out.setToolTip(
            "Off by default. The costume arcs already carry per-costume UI. "
            "Only turn this on if the game asks for a numbered texture that "
            "does not exist.")

        form = QFormLayout()
        form.addRow("Game install", self._row(self.game_dir, pick_game))
        form.addRow("Cloning folder", self._row(self.out_dir, pick_out))
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
        opts.addWidget(self.ui_255)
        opts_box = QGroupBox("Extras")
        opts_box.setLayout(opts)

        self.stage_btn = QPushButton("Create clone")
        self.stage_btn.clicked.connect(self.stage)
        self.stage_btn.setToolTip("Builds everything into the cloning folder. "
                                  "Nothing touches the game yet.")
        self.install_btn = QPushButton("Install into the game")
        self.install_btn.setEnabled(False)
        self.install_btn.clicked.connect(self.install_clone)
        self.install_btn.setToolTip("Copies the staged files over and appends "
                                    "characters.ini, backing it up first.")

        self.open_btn = QPushButton("Open cloning folder")
        self.open_btn.clicked.connect(self.open_out_dir)

        buttons = QHBoxLayout()
        buttons.addWidget(self.stage_btn)
        buttons.addWidget(self.open_btn)
        buttons.addWidget(self.install_btn)
        buttons.addStretch(1)

        self.note = QLabel(
            "Point at your install and type a character ID."
        )
        self.note.setWordWrap(True)

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(2000)

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
        body.addWidget(self.console, 1)

        holder = QWidget()
        holder.setLayout(body)

        tabs = QTabWidget()
        tabs.addTab(holder, "Clone")
        tabs.addTab(self.build_stream_tab(), "BGM and assists")
        self.setCentralWidget(tabs)

        self.update_colour_count()
        self.refresh_buttons()

    def build_stream_tab(self) -> QWidget:
        body = QHBoxLayout()
        body.addWidget(self.build_bgm_panel(), 1)
        body.addWidget(self.build_assist_panel(), 1)
        page = QWidget()
        page.setLayout(body)
        return page

    def build_bgm_panel(self) -> QWidget:
        self.bgm_src = QLineEdit()
        self.bgm_src.setPlaceholderText("BGM.stqr")
        browse = QPushButton("Browse")
        browse.clicked.connect(self.pick_bgm)

        self.bgm_list = QTreeWidget()
        self.bgm_list.setAlternatingRowColors(True)
        self.bgm_list.setRootIsDecorated(False)
        self.bgm_list.setUniformRowHeights(True)
        self.bgm_list.setHeaderLabels(["#", "Character", "Path"])
        self.bgm_list.header().setStretchLastSection(True)
        self.bgm_list.setColumnWidth(0, 52)
        self.bgm_list.setColumnWidth(1, 130)

        self.bgm_filter = QLineEdit()
        self.bgm_filter.setPlaceholderText("Filter")
        self.bgm_filter.textChanged.connect(self.filter_bgm)

        # A clone gets its music from a stream named after its CharacterID, so
        # offer the characters directly rather than making you type the path.
        self.bgm_char = QComboBox()
        self.bgm_char.currentIndexChanged.connect(self.select_bgm_character)

        self.bgm_track = QLineEdit()
        self.bgm_track.setPlaceholderText("sound\\bgm\\source\\MyTrack")

        set_char = QPushButton("Set track")
        set_char.clicked.connect(self.set_bgm_for_character)

        self.bgm_note = QLabel("Pick BGM.stqr.")
        self.bgm_note.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Table", self._row(self.bgm_src, browse))

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self.bgm_filter)
        layout.addWidget(self.bgm_list, 1)
        layout.addWidget(QLabel("Set a character's track"))
        layout.addWidget(self.bgm_char)
        layout.addWidget(self._row(self.bgm_track, set_char))
        layout.addWidget(self.bgm_note)

        box = QGroupBox("BGM")
        box.setLayout(layout)
        return box

    def select_bgm_character(self):
        """Jump the list to that character's stream and show its current track."""
        cid = self.bgm_char.currentData()
        if not cid:
            return
        index = bgm_stream_index(self.bgm_hints(), cid)
        if 0 <= index < self.bgm_list.topLevelItemCount():
            item = self.bgm_list.topLevelItem(index)
            self.bgm_list.setCurrentItem(item)
            self.bgm_list.scrollToItem(item)
            self.bgm_track.setText(item.text(2))

    def bgm_hints(self):
        hints = [self.characters_ini, self.bgm_src.text().strip(),
                 self.csa_src.text().strip(), self.game_dir.text().strip()]
        hints = [h for h in hints if h]
        found = find_characters_ini(*hints) if hints else None
        if found is not None and found.is_file():
            self.characters_ini = str(found)
        return hints

    def load_bgm_characters(self, table):
        # Every playable character, including the ones whose stream does not
        # exist yet. Those are the whole point of the picker.
        keep = self.bgm_char.currentData()
        self.bgm_char.clear()
        for slot, cid in bgm_stream_plan(self.bgm_hints()):
            if slot < len(table.paths):
                leaf = table.paths[slot].split("\\")[-1]
                self.bgm_char.addItem(f"{cid}  (stream {slot}, {leaf})", cid)
            else:
                self.bgm_char.addItem(f"{cid}  (stream {slot}, no track yet)", cid)
        if keep is not None:
            self.bgm_char.setCurrentIndex(max(0, self.bgm_char.findData(keep)))

    def set_bgm_for_character(self):
        """
        Put a track on the character's own stream.

        The engine goes by position, not by the path, so appending gives the
        track to whichever character owns the next free index. It has to be
        written at 109 + the character's place in Characters.ini.
        """
        cid = self.bgm_char.currentData()
        if not cid:
            return
        src = Path(self.bgm_src.text().strip())
        if not src.is_file():
            self.bgm_note.setText("Pick a table first.")
            return
        track = self.bgm_track.text().strip()
        if not track:
            self.bgm_note.setText("Give the track a path.")
            return

        table = stqr.parse(src.read_bytes())
        index = bgm_stream_index(self.bgm_hints(), cid)
        if table is None or index < 0:
            self.bgm_note.setText(f"Could not work out a stream for {cid}.")
            return

        filled = table.set_path(index, track)
        dest = beside(src)
        dest.write_bytes(table.build())

        self.bgm_src.setText(str(dest))
        self.show_bgm_entries(table)
        self.bgm_list.setCurrentItem(self.bgm_list.topLevelItem(index))
        note = f"{cid} now plays {track} at stream {index}."
        if filled:
            owners = dict(bgm_stream_plan(self.bgm_hints()))
            waiting = [owners.get(i, str(i)) for i in filled]
            note += (f" {', '.join(waiting)} sit before them and had no stream, "
                     f"so they were given a placeholder track.")
        self.bgm_note.setText(note + f" Wrote {dest.name} beside the original.")

    def show_bgm_entries(self, table):
        owners = bgm_stream_owners(self.bgm_hints(), len(table.paths))
        self.bgm_list.clear()
        for i, path in enumerate(table.paths):
            row = QTreeWidgetItem([str(i), owners.get(i, ""), path])
            row.setTextAlignment(0, Qt.AlignmentFlag.AlignRight
                                 | Qt.AlignmentFlag.AlignVCenter)
            self.bgm_list.addTopLevelItem(row)
        self.filter_bgm()
        self.load_bgm_characters(table)

    def filter_bgm(self):
        needle = self.bgm_filter.text().strip().lower()
        for row in range(self.bgm_list.topLevelItemCount()):
            item = self.bgm_list.topLevelItem(row)
            hay = " ".join(item.text(c) for c in range(item.columnCount())).lower()
            item.setHidden(bool(needle) and needle not in hay)

    def build_assist_panel(self) -> QWidget:
        self.amsg_src = QLineEdit()
        self.amsg_src.setPlaceholderText("AssistMsg.msd")
        msg_browse = QPushButton("Browse")
        msg_browse.clicked.connect(lambda: self._pick_file(self.amsg_src, "*.msd"))

        self.csa_src = QLineEdit()
        self.csa_src.setPlaceholderText("AssistDef.csa")
        csa_browse = QPushButton("Browse")
        csa_browse.clicked.connect(lambda: self._pick_file(self.csa_src, "*.csa"))

        self.assist_list = QTreeWidget()
        self.assist_list.setAlternatingRowColors(True)
        self.assist_list.setRootIsDecorated(False)
        self.assist_list.setUniformRowHeights(True)
        self.assist_list.setHeaderLabels(["#", "Character", "Assists"])
        self.assist_list.header().setStretchLastSection(True)
        self.assist_list.setColumnWidth(0, 52)
        self.assist_list.setColumnWidth(1, 130)
        self.assist_list.currentItemChanged.connect(self.load_assist_slot)

        self.assist_filter = QLineEdit()
        self.assist_filter.setPlaceholderText("Filter by character or assist")
        self.assist_filter.textChanged.connect(self.filter_assists)

        grid = QGridLayout()
        for col, title in enumerate(("", "Top line", "Bottom line", "Type", "Direction")):
            grid.addWidget(QLabel(title), 0, col)

        self.assist_rows = []
        for row in range(3):
            name1, name2 = QLineEdit(), QLineEdit()
            kind, direction = QComboBox(), QComboBox()
            for label in csa.TYPES:
                kind.addItem(label.capitalize(), csa.TYPES[label])
            for label in csa.DIRECTIONS:
                direction.addItem(
                    {"tiltup": "TiltUp", "tiltdw": "TiltDw"}.get(label, label.capitalize()),
                    csa.DIRECTIONS[label])
            grid.addWidget(QLabel(f"{row + 1}"), row + 1, 0)
            grid.addWidget(name1, row + 1, 1)
            grid.addWidget(name2, row + 1, 2)
            grid.addWidget(kind, row + 1, 3)
            grid.addWidget(direction, row + 1, 4)
            self.assist_rows.append((name1, name2, kind, direction))

        self.assist_note = QLabel("Pick the two tables.")
        self.assist_note.setWordWrap(True)

        self.assist_btn = QPushButton("Write tables")
        self.assist_btn.clicked.connect(self.write_assists)
        self.assist_btn.setEnabled(False)

        form = QFormLayout()
        form.addRow("Names", self._row(self.amsg_src, msg_browse))
        form.addRow("Definitions", self._row(self.csa_src, csa_browse))


        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self.assist_filter)
        layout.addWidget(self.assist_list, 1)
        layout.addLayout(grid)
        layout.addWidget(self.assist_note)
        layout.addWidget(self.assist_btn)

        box = QGroupBox("Assists")
        box.setLayout(layout)
        return box

    def show_assist_slots(self, names, defs):
        # The slot index is the character ID. 0 to 51 is the base roster,
        # 52 to 59 the game's own children, and Characters.ini [CharacterN]
        # is slot 59 + N.
        # The Clone tab's install path may be empty, so fall back to walking up
        # from the tables themselves. They sit in nativePCx64/CloneEngine.
        # Remember where it was found. After a write the table paths point at
        # the output folder, which has no Characters.ini above it.
        hints = [self.characters_ini, self.csa_src.text().strip(),
                 self.amsg_src.text().strip(), self.game_dir.text().strip()]
        found = find_characters_ini(*[h for h in hints if h])
        if found.is_file():
            self.characters_ini = str(found)
        # Uncapped: characters past the end of the table have to appear too,
        # and the cap is what was hiding Sagt, Heisn and the rest.
        roster = assist_slot_names([found], 10 ** 6)

        self.assist_list.blockSignals(True)
        self.assist_list.clear()
        for i, slot in enumerate(defs.slots):
            labels = [names.messages[a.name1 - 1] for a in slot
                      if 0 < a.name1 <= len(names.messages)]
            row = QTreeWidgetItem(
                [str(i), roster.get(i, ""), " / ".join(labels) or "empty"])
            row.setTextAlignment(0, Qt.AlignmentFlag.AlignRight
                                 | Qt.AlignmentFlag.AlignVCenter)
            self.assist_list.addTopLevelItem(row)
        # Characters whose slot is past the end of the table have no row yet.
        # Without one they are simply missing from the list.
        for slot, cid in sorted(roster.items()):
            if slot >= len(defs.slots):
                row = QTreeWidgetItem([str(slot), cid, "no slot yet"])
                row.setTextAlignment(0, Qt.AlignmentFlag.AlignRight
                                     | Qt.AlignmentFlag.AlignVCenter)
                self.assist_list.addTopLevelItem(row)

        self.assist_list.blockSignals(False)
        self.filter_assists()

    def filter_assists(self):
        needle = self.assist_filter.text().strip().lower()
        for row in range(self.assist_list.topLevelItemCount()):
            item = self.assist_list.topLevelItem(row)
            hay = " ".join(item.text(c) for c in range(item.columnCount())).lower()
            item.setHidden(bool(needle) and needle not in hay)

    def assist_tables(self):
        msg_path = Path(self.amsg_src.text().strip())
        csa_path = Path(self.csa_src.text().strip())
        if not (msg_path.is_file() and csa_path.is_file()):
            return None, None, None, None
        try:
            names = msd.parse(msg_path.read_bytes())
            defs = csa.parse(csa_path.read_bytes())
        except Exception as exc:
            self.assist_note.setText(str(exc))
            return None, None, None, None
        return names, defs, msg_path, csa_path

    def refresh_assist_names(self):
        names, defs, _m, _c = self.assist_tables()
        if names is not None and defs is not None:
            keep = self.current_assist_slot()
            self.show_assist_slots(names, defs)
            self.select_assist_slot(keep)

    def select_assist_slot(self, slot: int):
        for row in range(self.assist_list.topLevelItemCount()):
            item = self.assist_list.topLevelItem(row)
            if item.text(0) == str(slot):
                self.assist_list.setCurrentItem(item)
                return

    def current_assist_slot(self) -> int:
        """The slot number from the row, which may be past the table's end."""
        item = self.assist_list.currentItem()
        if item is not None and item.text(0).isdigit():
            return int(item.text(0))
        return 0

    def load_assist_slot(self):
        names, defs, _m, _c = self.assist_tables()
        if names is None or defs is None:
            return
        # The list is longer than the table now, because characters past its
        # end get a row too, so only build it when it is actually empty.
        if self.assist_list.topLevelItemCount() == 0:
            self.show_assist_slots(names, defs)
        self.assist_btn.setEnabled(True)

        index = self.current_assist_slot()
        slot = defs.slots[index] if index < len(defs.slots) else []
        for row, (name1, name2, kind, direction) in enumerate(self.assist_rows):
            assist = slot[row] if row < len(slot) else csa.Assist()
            name1.setText(names.messages[assist.name1 - 1] if assist.name1 else "")
            name2.setText(names.messages[assist.name2 - 1] if assist.name2 else "")
            kind.setCurrentIndex(max(0, kind.findData(assist.type)))
            direction.setCurrentIndex(max(0, direction.findData(assist.direction)))

        if index < len(defs.slots):
            self.assist_note.setText(f"Slot {index} of {len(defs.slots)}.")
        else:
            self.assist_note.setText(
                f"Slot {index}, past the table's {len(defs.slots)}. "
                f"Writing extends it.")

    def _pick_file(self, target: QLineEdit, pattern: str):
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Choose a file", target.text() or "",
            f"{pattern};;All files (*)")
        if chosen:
            target.setText(chosen)
            self.load_assist_slot()

    def pick_bgm(self):
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Choose BGM.stqr", self.bgm_src.text() or "",
            "Stream tables (*.stqr);;All files (*)")
        if not chosen:
            return
        self.bgm_src.setText(chosen)
        table = stqr.parse(Path(chosen).read_bytes())
        if table is None:
            self.bgm_note.setText("Not a stream table this tool reads.")
            return
        self.show_bgm_entries(table)
        self.bgm_note.setText(f"{len(table.paths)} entries.")

    def write_assists(self):
        names, defs, msg_path, csa_path = self.assist_tables()
        if names is None or defs is None:
            self.assist_note.setText("Pick both tables first.")
            return

        assists = []
        for name1, name2, kind, direction in self.assist_rows:
            top, bottom = name1.text().strip(), name2.text().strip()
            if not top and not bottom:
                assists.append(csa.Assist())
                continue
            assists.append(csa.Assist(
                names.add(top) if top else 0,
                names.add(bottom) if bottom else 0,
                kind.currentData(), direction.currentData()))

        index = self.current_assist_slot()
        defs.set_slot(index, assists)

        csa_out, msg_out = beside(csa_path), beside(msg_path)
        csa_out.write_bytes(defs.build())
        msg_out.write_bytes(names.build())

        # Carry on from what was just written, otherwise the next edit reloads
        # the untouched source and the change appears to vanish.
        self.csa_src.setText(str(csa_out))
        self.amsg_src.setText(str(msg_out))

        filled = sum(1 for a in assists if a.name1 or a.name2)
        self.show_assist_slots(names, defs)
        self.select_assist_slot(index)
        self.assist_note.setText(
            f"Slot {index}, {filled} assists. Wrote {csa_out.name} and "
            f"{msg_out.name} beside the originals.")
        self.console.appendPlainText(f"assists: slot {index}, {filled} entries")

    def _row(self, widget, button):
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(widget, 1)
        lay.addWidget(button)
        return w

    def open_out_dir(self):
        target = Path(self.out_dir.text().strip())
        target.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _pick_dir(self, target: QLineEdit, caption: str):
        chosen = QFileDialog.getExistingDirectory(self, caption, target.text() or "")
        if chosen:
            target.setText(chosen)
            self.load_assist_slot()

    def _busy(self, on: bool):
        for b in (self.stage_btn,):
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
            char_id=self.char_id_value(),
            base_name=self.base_name.text().strip(),
            new_name=self.new_name.text().strip(),
            costumes=self.costume_slots(),
            sound_id=self.sound_id.text().strip(),
            include_sound=self.want_sound.isChecked(),
            include_ui=self.want_ui.isChecked(),
            underscore_names=self.underscores.isChecked(),
            fan_out_ui=self.fan_out.isChecked(),
            ui_255=self.ui_255.isChecked(),
        )

    def costume_slots(self) -> list[str]:
        return [f"{i:02d}" for i in range(self.costumes.value())]

    def update_colour_count(self):
        slots = self.costume_slots()
        self.colour_count.setText(f"NumColors={len(slots)}, {slots[0]} to {slots[-1]}")

    def normalise_char_id(self):
        """Show the full '0033 - Iron Man' label whatever was typed."""
        cid = self.char_id_value()
        for index in range(self.char_id.count()):
            if self.char_id.itemData(index) == cid:
                if self.char_id.currentText() != self.char_id.itemText(index):
                    self.char_id.setCurrentText(self.char_id.itemText(index))
                return
        # Not a base character. Keep the padded number so it still matches the
        # archive filenames, since clones and mods have IDs of their own.
        if cid and self.char_id.currentText() != cid:
            self.char_id.setCurrentText(cid)

    def char_id_value(self) -> str:
        """
        The numeric ID, however it was entered.

        The field accepts a pick from the list, a bare "26", or a full
        "0026 - Spider Man" typed by hand, so take the leading token and pad it
        out to the four digits the archive filenames use.
        """
        token = self.char_id.currentText().strip().split(" ")[0]
        if token.isdigit():
            return token.zfill(4)
        return token

    def blocker(self) -> str:
        """Why the clone cannot be staged yet, or empty if it can."""
        if not Path(self.game_dir.text().strip() or ".").is_dir():
            return "Point at your game install."
        if not self.char_id_value():
            return "Type a character ID or pick one from the drop-down."
        if not self.base_name.text().strip():
            return "No codename yet. Check the ID matches archives in chr/archive."
        if not self.new_name.text().strip():
            return "Give the clone a name."
        sid = self.sound_id.text().strip()
        if sid and len(sid) != 3:
            return f"Sound ID is {len(sid)} characters, it has to be 3."
        if self.name_limit and len(self.new_name.text().strip()) > self.name_limit:
            return f"Clone name is longer than {self.name_limit} characters."
        return ""

    def refresh_buttons(self):
        # An edit makes a previous run stale.
        if self.report is not None and self.spec is not None:
            if (self.spec.new_name != self.new_name.text().strip()
                    or self.spec.sound_id != self.sound_id.text().strip()
                    or self.spec.base_name != self.base_name.text().strip()):
                self.report = None
                self.install_btn.setEnabled(False)

        why = self.blocker()
        self.stage_btn.setEnabled(not why)
        if why:
            self.note.setText(why)
        else:
            self.update_length_note()

    def update_length_note(self):
        base, new = self.base_name.text().strip(), self.new_name.text().strip()
        if not base or not new:
            return
        if self.name_limit:
            over = len(new) - self.name_limit
            if over > 0:
                self.note.setText(
                    f"{len(new)} characters, {over} too many. {base} allows "
                    f"{self.name_limit}."
                )
            else:
                self.note.setText(
                    f"{len(new)} of {self.name_limit} characters."
                )
        else:
            self.note.setText(
                f"{len(new)} characters against {base}'s {len(base)}. "
                f"Read the name to get the limit for this character."
            )

    # actions

    def detect_name(self):
        self.normalise_char_id()
        if not self.char_id_value():
            self.refresh_buttons()
            return

        # Anything on screen belongs to the previous character.
        self.base_name.clear()
        self.name_limit = 0
        self.characters_ini = ""
        self.new_name.setMaxLength(32767)
        self.install_btn.setEnabled(False)
        self.report = None

        spec = self.build_spec()
        try:
            sources = find_sources(spec.game_dir, spec.char_id, spec.sound_lang)
        except Exception as exc:
            self.console.appendPlainText(f"could not read the install: {exc}")
            self.refresh_buttons()
            return
        pick = sources.get("cmn") or next(iter(sources.values()), None)
        if pick is None:
            self.console.appendPlainText(
                f"no archives for {spec.char_id} under that install")
            self.refresh_buttons()
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
            self.console.appendPlainText(f"could not read a codename out of {pick.name}")
            self.refresh_buttons()
            return

        # Needs the codename: an empty base name measures nothing and returns 0,
        # and a zero max length locks the field.
        try:
            self.name_limit, bound = max_name_length(
                self.build_spec(), log=lambda _m: None)
        except Exception as exc:
            self.name_limit = 0
            self.console.appendPlainText(f"could not work out the name limit: {exc}")
            return

        if self.name_limit > 0:
            self.new_name.setMaxLength(self.name_limit)
            self.console.appendPlainText(
                f"name limit is {self.name_limit} characters ({bound})")
        else:
            self.console.appendPlainText(
                "name limit came out as zero, leaving the field open")
        self.refresh_buttons()

    def stage(self):
        spec = self.build_spec()
        why = self.blocker()
        if why:
            self.note.setText(why)
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


def beside(src: Path) -> Path:
    """<name>_New next to the source, without stacking _New_New on a rewrite."""
    stem = src.stem
    while stem.endswith("_New"):
        stem = stem[:-4]
    return src.with_name(f"{stem}_New{src.suffix}")


def main():
    app = QApplication(sys.argv)
    win = Window()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()