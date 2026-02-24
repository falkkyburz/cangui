"""Trace window panel for recording, replaying, and browsing CAN bus traces.

Provides a toolbar-driven QTableView backed by TraceModel, with controls for
starting/stopping live recording, loading and saving trace files in
TRC or BLF format, configuring replay speed, and live-filtering displayed
messages by CAN ID or text.
"""

from PySide6.QtCore import Qt, Signal, QSortFilterProxyModel
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QToolBar, QTableView,
    QFileDialog, QLabel, QLineEdit, QHeaderView, QMenu,
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
    DLC/Length columns.  Text filtering via ``setFilterFixedString`` compares the
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
        elif col == 7:  # Length — integer
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
    actions for recording control (start/stop), file I/O (save/load),
    replay-speed selection, and a live text filter.  A status bar shows the
    running message count, the current message rate, and the recording state.

    .. attribute:: _STATE_STYLE

        Mapping from state name to QSS style string used by :meth:`_set_state`.

    Signals:
        save_trace_requested: Emitted with the chosen file path when the user
            saves the current trace.
        load_trace_requested: Emitted with the chosen file path when the user
            loads a trace file.
        start_recording_requested: Emitted when the Start toolbar button is
            activated to begin live recording.
    """

    _STATE_STYLE: dict[str, str] = {
        "Stopped":   "color: gray;",
        "Recording": "color: #CC0000; font-weight: bold;",
    }
    TITLE = "Trace"

    save_trace_requested = Signal(str)  # file path
    load_trace_requested = Signal(str)  # file path
    start_recording_requested = Signal()

    def __init__(self, model: TraceModel, parent=None):
        """Initialize the TraceWindow with its model and build the UI.

        Constructs the recording toolbar, speed selector, filter bar,
        QTableView, and status bar, then connects the model's signals for
        row-count updates, file-name display, and rate display.

        Args:
            model: The TraceModel that provides CAN frame data to the view.
            parent: Optional parent QWidget.
        """
        super().__init__(parent)
        self._model = model
        self._bottom_threshold_rows = 200
        self._suppress_scroll_events = False

        # Sort/filter proxy — wraps the raw TraceModel
        self._proxy = _TraceSortProxy(self)
        self._proxy.setSourceModel(self._model)
        self._proxy.setDynamicSortFilter(False)

        # Toolbar
        toolbar = QToolBar()
        toolbar.setMovable(False)

        self._start_action = QAction(_icon("record"), "Start [F9]", self)
        self._start_action.triggered.connect(self._on_start)
        toolbar.addAction(self._start_action)

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
        self._table.setSortingEnabled(False)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionsClickable(False)
        header.setSortIndicatorShown(False)
        # Use fixed widths instead of ResizeToContents (which measures ALL rows)
        for col, width in enumerate([60, 80, 35, 70, 35, 45, 35, 45, 200]):
            header.resizeSection(col, width)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)
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
        self._mode_label = QLabel("Live")
        self._mode_label.setStyleSheet("color: #1f7a1f;")
        self._status_layout.addWidget(self._mode_label)
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
                or ``"Replaying"``.
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

    def _update_button_state(self, recording: bool):
        """Enable or disable the Start/Stop toolbar actions.

        Args:
            recording: ``True`` while a recording is active; ``False`` when
                stopped.
        """
        self._start_action.setEnabled(not recording)
        self._stop_action.setEnabled(recording)

    def _on_start(self):
        """Handle Start toolbar action by emitting start_recording_requested.

        Emits:
            start_recording_requested: When the Start button is clicked.
        """
        self.start_recording_requested.emit()

    def set_recording_state(self, recording: bool):
        """Update button states and status label without touching the model.

        Called by MainWindow after the CAN service confirms the recording
        state has changed, so the UI stays in sync with the backend.

        Args:
            recording: ``True`` if recording is now active, ``False`` if stopped.
        """
        self._update_button_state(recording)
        self._set_state("Recording" if recording else "Stopped")
        if recording:
            self._switch_to_live_mode()

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

        Supports loading .ctb, .trc, and .blf files. For .trc and .blf files,
        automatically converts to .ctb format before loading.

        Emits:
            load_trace_requested: With the chosen file path, if the user
                confirmed the dialog.
        """
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Trace", "",
            "All Trace Files (*.ctb *.trc *.blf);;CTB Files (*.ctb);;TRC Files (*.trc);;BLF Files (*.blf);;All Files (*)"
        )
        if path:
            self.load_trace_requested.emit(path)

    def _on_entries_committed(self):
        """Refresh the message-count label."""
        self._count_label.setText(f"Messages: {self._model.message_count}")
        if self._model.live_mode:
            self._scroll_to_bottom()

    def _on_model_reset(self):
        """Refresh the message-count label after the model is fully reset."""
        self._count_label.setText(f"Messages: {self._model.message_count}")
        self._switch_to_live_mode()

    def _on_file_changed(self, path: str):
        """Handle trace file change (kept for compatibility but does nothing now).

        Args:
            path: Absolute path to the newly active trace file, or an empty
                string when no file is active.
        """
        # File info display moved to ReplayListWindow
        pass

    def _on_filter_changed(self, text: str):
        """Apply the filter text to the proxy model.

        Args:
            text: Free-form filter string; an empty string shows all rows.
        """
        self._proxy.setFilterFixedString(text)

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

    def _scroll_to_bottom(self):
        self._suppress_scroll_events = True
        try:
            self._table.scrollToBottom()
        finally:
            self._suppress_scroll_events = False

    def _remaining_rows_to_bottom(self) -> int:
        model = self._table.model()
        if model is None:
            return 0
        total = model.rowCount()
        if total <= 0:
            return 0
        bottom_row = self._table.rowAt(self._table.viewport().height() - 1)
        if bottom_row < 0:
            bottom_row = total - 1
        return max(0, (total - 1) - bottom_row)

    def _switch_to_live_mode(self):
        self._model.set_live_mode(True)
        self._mode_label.setText("Live")
        self._mode_label.setStyleSheet("color: #1f7a1f;")
        self._scroll_to_bottom()

    def _switch_to_scroll_mode(self):
        self._model.set_live_mode(False)
        self._mode_label.setText("Scroll")
        self._mode_label.setStyleSheet("color: #a06a00;")

    def _on_scroll_changed(self, _value: int):
        if self._suppress_scroll_events:
            return
        remaining = self._remaining_rows_to_bottom()
        if remaining <= self._bottom_threshold_rows:
            if not self._model.live_mode:
                self._switch_to_live_mode()
            return
        if self._model.live_mode:
            self._switch_to_scroll_mode()

