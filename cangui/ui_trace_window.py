"""Trace window panel for recording, replaying, and browsing CAN bus traces.

Provides a toolbar-driven QTableView backed by TraceModel, with controls for
starting/pausing/stopping live recording, loading and saving trace files in
TRC or BLF format, configuring replay speed, and live-filtering displayed
messages by CAN ID or text.
"""

from PySide6.QtCore import Qt, Signal, QSortFilterProxyModel
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QToolBar, QTableView,
    QFileDialog, QDoubleSpinBox, QLabel, QLineEdit, QHeaderView, QToolButton, QMenu,
    QApplication,
)
from PySide6.QtGui import QAction

from cangui.model_trace import TraceModel
from cangui.icons import icon as _icon
from cangui.theme import SECONDARY_TEXT_STYLE
from cangui.ui_base_dock_window import BaseDockWindow


class _TraceSortProxy(QSortFilterProxyModel):
    """Sort/filter proxy for the trace table view.

    Applies numeric sorting for the frame number (#), timestamp, CAN-ID, and
    DLC columns.  Text filtering via ``setFilterFixedString`` compares the
    filter text against every visible column.
    """

    def lessThan(self, left, right) -> bool:
        """Compare two trace rows for sort ordering.

        Args:
            left: Left-hand source model index.
            right: Right-hand source model index.

        Returns:
            ``True`` if *left* should appear before *right* in ascending order.
        """
        col = left.column()
        ld = left.data() or ""
        rd = right.data() or ""

        if col == 0:  # # — integer
            try:
                return int(ld) < int(rd)
            except (ValueError, TypeError):
                pass
        elif col == 1:  # Time — float
            try:
                return float(ld) < float(rd)
            except (ValueError, TypeError):
                pass
        elif col == 3:  # CAN-ID — hex integer
            try:
                return int(ld, 16) < int(rd, 16)
            except (ValueError, TypeError):
                pass
        elif col == 6:  # DLC — integer
            try:
                return int(ld) < int(rd)
            except (ValueError, TypeError):
                pass

        return super().lessThan(left, right)

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:
        """Accept a row when the filter text appears in any column.

        Args:
            source_row: Row index in the source model.
            source_parent: Parent index (always invalid for a flat table).

        Returns:
            ``True`` when the row should be visible.
        """
        pattern = self.filterRegularExpression().pattern()
        if not pattern:
            return True
        model = self.sourceModel()
        for col in range(model.columnCount()):
            idx = model.index(source_row, col, source_parent)
            text = str(model.data(idx) or "")
            if pattern.lower() in text.lower():
                return True
        return False


class TraceWindow(BaseDockWindow):
    """Trace recording and replay panel backed by TraceModel.

    Displays all captured CAN frames in a QTableView and exposes toolbar
    actions for recording control (start/pause/stop), file I/O (save/load),
    replay-speed selection, and a live text filter.  A status bar shows the
    running message count, the current message rate, and the recording state.

    .. attribute:: _STATE_STYLE

        Mapping from state name to QSS style string used by :meth:`_set_state`.

    Signals:
        load_trace_requested: Emitted with the chosen file path when the user
            selects a trace file to load.
        save_trace_requested: Emitted with the chosen file path when the user
            saves the current trace.
        start_recording_requested: Emitted when the Start toolbar button is
            activated.
    """

    _STATE_STYLE: dict[str, str] = {
        "Stopped":   "color: gray;",
        "Recording": "color: #CC0000; font-weight: bold;",
        "Paused":    "color: #CC7700;",
        "Replaying": "color: #006699;",
    }

    TITLE = "Trace"

    load_trace_requested = Signal(str)  # file path
    save_trace_requested = Signal(str)  # file path
    start_recording_requested = Signal()

    def __init__(self, model: TraceModel, parent=None):
        """Initialize the TraceWindow with its model and build the UI.

        Constructs the recording toolbar, speed selector, filter bar,
        QTableView, and status bar, then connects the model's signals for
        auto-scroll, row-count updates, file-name display, and rate display.

        Args:
            model: The TraceModel that provides CAN frame data to the view.
            parent: Optional parent QWidget.
        """
        super().__init__(parent)
        self._model = model
        self._auto_scroll = True

        # Sort/filter proxy — wraps the raw TraceModel
        self._proxy = _TraceSortProxy(self)
        self._proxy.setSourceModel(self._model)

        # Toolbar
        toolbar = QToolBar()
        toolbar.setMovable(False)

        self._start_action = QAction(_icon("record"), "Start [F9]", self)
        self._start_action.triggered.connect(self._on_start)
        toolbar.addAction(self._start_action)

        self._pause_action = QAction(_icon("pause"), "Pause", self)
        self._pause_action.setEnabled(False)
        self._pause_action.triggered.connect(self._on_pause)
        toolbar.addAction(self._pause_action)

        self._stop_action = QAction(_icon("stop"), "Stop [F6]", self)
        self._stop_action.setEnabled(False)
        self._stop_action.triggered.connect(self._on_stop)
        toolbar.addAction(self._stop_action)

        toolbar.addSeparator()

        self._clear_action = QAction(_icon("trash"), "Clear", self)
        self._clear_action.triggered.connect(self._on_clear)
        toolbar.addAction(self._clear_action)

        toolbar.addSeparator()

        save_action = QAction(_icon("save"), "Save...", self)
        save_action.triggered.connect(self._on_save)
        toolbar.addAction(save_action)

        load_action = QAction(_icon("open"), "Load...", self)
        load_action.triggered.connect(self._on_load)
        toolbar.addAction(load_action)

        toolbar.addSeparator()

        # Speed selector for replay
        toolbar.addWidget(QLabel(" Speed: "))
        self._speed_spin = QDoubleSpinBox()
        self._speed_spin.setRange(0.1, 100.0)
        self._speed_spin.setSingleStep(0.5)
        self._speed_spin.setDecimals(1)
        self._speed_spin.setValue(1.0)
        self._speed_spin.setSuffix("x")
        self._speed_spin.setFixedWidth(72)
        toolbar.addWidget(self._speed_spin)

        self._max_speed_btn = QToolButton()
        self._max_speed_btn.setText("Max")
        self._max_speed_btn.setCheckable(True)
        self._max_speed_btn.toggled.connect(
            lambda on: self._speed_spin.setEnabled(not on)
        )
        toolbar.addWidget(self._max_speed_btn)

        toolbar.addSeparator()

        self._file_label = QLabel("")
        self._file_label.setStyleSheet(SECONDARY_TEXT_STYLE)
        toolbar.addWidget(self._file_label)

        self._layout.addWidget(toolbar)

        # Filter bar
        filter_layout = QHBoxLayout()
        filter_layout.setContentsMargins(4, 2, 4, 2)
        filter_layout.addWidget(QLabel("Filter:"))
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("CAN ID or text...")
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.textChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self._filter_edit)
        self._layout.addLayout(filter_layout)

        # Table view
        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self._table.setSortingEnabled(True)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        # Use fixed widths instead of ResizeToContents (which measures ALL rows)
        for col, width in enumerate([60, 80, 35, 70, 35, 45, 35, 200]):
            header.resizeSection(col, width)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._layout.addWidget(self._table)

        # Auto-scroll on new rows
        self._model.entries_committed.connect(self._on_entries_committed)
        self._model.modelReset.connect(self._on_model_reset)
        self._model.file_changed.connect(self._on_file_changed)

        # Status bar
        self._status_layout = QHBoxLayout()
        self._status_layout.setContentsMargins(4, 2, 4, 2)
        self._count_label = QLabel("Messages: 0")
        self._status_layout.addWidget(self._count_label)
        self._status_layout.addStretch()
        self._rate_label = QLabel("")
        self._rate_label.setStyleSheet("color: gray;")
        self._status_layout.addWidget(self._rate_label)
        self._status_layout.addStretch()
        self._state_label = QLabel()
        self._status_layout.addWidget(self._state_label)
        self._set_state("Stopped")
        self._layout.addLayout(self._status_layout)

        self._model.rate_updated.connect(self._on_rate_updated)

    def _set_state(self, text: str) -> None:
        """Update the state label text and apply the corresponding color style.

        Args:
            text: State name; one of ``"Stopped"``, ``"Recording"``,
                ``"Paused"``, or ``"Replaying"``.
        """
        self._state_label.setText(text)
        self._state_label.setStyleSheet(self._STATE_STYLE.get(text, ""))

    @property
    def primary_view(self):
        """Return the QTableView that displays trace entries.

        Returns:
            The QTableView used to render CAN trace rows.
        """
        return self._table

    @property
    def speed_factor(self) -> float:
        """Return the numeric replay speed multiplier from the toolbar.

        Returns ``1000.0`` when the Max button is checked (unlimited speed),
        otherwise returns the spinbox value.

        Returns:
            Floating-point speed multiplier (e.g. ``0.5``, ``1.0``, ``1000.0``).
        """
        if self._max_speed_btn.isChecked():
            return 1000.0
        return self._speed_spin.value()

    def _update_button_state(self, recording: bool):
        """Enable or disable the Start/Pause/Stop toolbar actions.

        Args:
            recording: ``True`` while a recording is active; ``False`` when
                stopped or paused.
        """
        self._start_action.setEnabled(not recording)
        self._pause_action.setEnabled(recording)
        self._stop_action.setEnabled(recording)

    def _on_start(self):
        """Handle Start toolbar action by emitting start_recording_requested.

        Emits:
            start_recording_requested: Always, when the Start button is clicked.
        """
        self.start_recording_requested.emit()

    def _on_pause(self):
        """Pause live recording and update toolbar and status label."""
        self._model.pause()
        self._update_button_state(False)
        self._start_action.setEnabled(True)
        self._set_state("Paused")

    def set_recording_state(self, recording: bool):
        """Update button states and status label without touching the model.

        Called by MainWindow after the CAN service confirms the recording
        state has changed, so the UI stays in sync with the backend.

        Args:
            recording: ``True`` if recording is now active, ``False`` if stopped.
        """
        self._update_button_state(recording)
        self._set_state("Recording" if recording else "Stopped")

    def _on_stop(self):
        """Stop live recording and reset toolbar and status label."""
        self._model.stop()
        self._update_button_state(False)
        self._set_state("Stopped")

    def _on_clear(self):
        """Clear all trace entries from the model and reset the count label."""
        self._model.clear()
        self._count_label.setText("Messages: 0")

    def _on_save(self):
        """Open a file-save dialog and emit save_trace_requested with the path.

        Emits:
            save_trace_requested: With the chosen file path, if the user
                confirmed the dialog.
        """
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Trace", "",
            "Trace Files (*.trc *.blf);;TRC Files (*.trc);;BLF Files (*.blf);;All Files (*)"
        )
        if path:
            self.save_trace_requested.emit(path)

    def _on_load(self):
        """Open a file-open dialog and emit load_trace_requested with the path.

        Emits:
            load_trace_requested: With the chosen file path, if the user
                confirmed the dialog.
        """
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Trace", "",
            "Trace Files (*.trc *.blf);;TRC Files (*.trc);;BLF Files (*.blf);;All Files (*)"
        )
        if path:
            self.load_trace_requested.emit(path)

    def _on_entries_committed(self):
        """Refresh the message-count label and auto-scroll to the newest row."""
        self._count_label.setText(f"Messages: {self._model.message_count}")
        if self._auto_scroll and not self._filter_edit.text():
            self._table.scrollToBottom()

    def _on_model_reset(self):
        """Refresh the message-count label after the model is fully reset."""
        self._count_label.setText(f"Messages: {self._model.message_count}")

    def _on_file_changed(self, path: str):
        """Update the toolbar file-name label when the active trace file changes.

        Args:
            path: Absolute path to the newly active trace file, or an empty
                string when no file is active.
        """
        if path:
            from pathlib import Path
            self._file_label.setText(Path(path).name)
            self._file_label.setToolTip(path)
        else:
            self._file_label.setText("")
            self._file_label.setToolTip("")

    def _on_filter_changed(self, text: str):
        """Apply the filter text to the proxy model.

        Args:
            text: Free-form filter string; an empty string shows all rows.
        """
        self._proxy.setFilterFixedString(text)
        if self._auto_scroll and not text:
            self._table.scrollToBottom()

    def _on_rate_updated(self, rate: int):
        """Refresh the rate label in the status bar.

        Args:
            rate: Current message throughput in messages per second.  A value
                of ``0`` clears the label.
        """
        if rate > 0:
            self._rate_label.setText(f"{rate} msg/s")
        else:
            self._rate_label.setText("")

    def _on_context_menu(self, pos):
        """Show a context menu at pos with a Copy Row action."""
        index = self._table.indexAt(pos)
        if not index.isValid():
            return
        menu = QMenu(self)
        copy_action = menu.addAction("Copy Row")
        action = menu.exec(self._table.viewport().mapToGlobal(pos))
        if action is copy_action:
            model = self._table.model()
            row = index.row()
            parts = []
            for col in range(model.columnCount()):
                cell = model.index(row, col)
                text = str(model.data(cell) or "")
                parts.append(text)
            QApplication.clipboard().setText("  ".join(parts))

    def set_replay_state(self, playing: bool):
        """Update the status label to reflect the current replay state.

        Args:
            playing: ``True`` while a trace file is being replayed, ``False``
                when playback has stopped.
        """
        if playing:
            self._set_state("Replaying")
        else:
            self._set_state("Stopped")
