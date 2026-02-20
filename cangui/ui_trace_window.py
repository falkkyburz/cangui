"""Trace window panel for recording, replaying, and browsing CAN bus traces.

Provides a toolbar-driven QTableView backed by TraceModel, with controls for
starting/pausing/stopping live recording, loading and saving trace files in
TRC or BLF format, configuring replay speed, and live-filtering displayed
messages by CAN ID or text.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QToolBar, QTableView,
    QFileDialog, QComboBox, QLabel, QLineEdit,
)
from PySide6.QtGui import QAction

from cangui.model_trace import TraceModel
from cangui.icons import icon as _icon
from cangui.theme import SECONDARY_TEXT_STYLE
from cangui.ui_base_dock_window import BaseDockWindow


class TraceWindow(BaseDockWindow):
    """Trace recording and replay panel backed by TraceModel.

    Displays all captured CAN frames in a QTableView and exposes toolbar
    actions for recording control (start/pause/stop), file I/O (save/load),
    replay-speed selection, and a live text filter.  A status bar shows the
    running message count, the current message rate, and the recording state.

    Signals:
        load_trace_requested: Emitted with the chosen file path when the user
            selects a trace file to load.
        save_trace_requested: Emitted with the chosen file path when the user
            saves the current trace.
        start_recording_requested: Emitted when the Start toolbar button is
            activated.
    """

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
        self._speed_combo = QComboBox()
        self._speed_combo.addItems(["0.5x", "1x", "2x", "10x", "Max"])
        self._speed_combo.setCurrentIndex(1)
        toolbar.addWidget(self._speed_combo)

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
        filter_layout.addWidget(self._filter_edit)
        self._layout.addLayout(filter_layout)

        # Table view
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        header.setStretchLastSection(True)
        # Use fixed widths instead of ResizeToContents (which measures ALL rows)
        for col, width in enumerate([60, 80, 35, 70, 35, 45, 35, 200]):
            header.resizeSection(col, width)
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
        self._state_label = QLabel("Stopped")
        self._status_layout.addWidget(self._state_label)
        self._layout.addLayout(self._status_layout)

        self._model.rate_updated.connect(self._on_rate_updated)

    @property
    def primary_view(self):
        """Return the QTableView that displays trace entries.

        Returns:
            The QTableView used to render CAN trace rows.
        """
        return self._table

    @property
    def speed_factor(self) -> float:
        """Return the numeric replay speed multiplier selected in the toolbar.

        Maps the human-readable combo-box text (e.g. ``"2x"``, ``"Max"``) to a
        floating-point multiplier.  ``"Max"`` returns ``1000.0`` as a
        practically unlimited speed sentinel.

        Returns:
            Floating-point speed multiplier (e.g. ``0.5``, ``1.0``, ``1000.0``).
        """
        text = self._speed_combo.currentText()
        if text == "Max":
            return 1000.0
        return float(text.rstrip("x"))

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
        self._state_label.setText("Paused")

    def set_recording_state(self, recording: bool):
        """Update button states and status label without touching the model.

        Called by MainWindow after the CAN service confirms the recording
        state has changed, so the UI stays in sync with the backend.

        Args:
            recording: ``True`` if recording is now active, ``False`` if stopped.
        """
        self._update_button_state(recording)
        self._state_label.setText("Recording" if recording else "Stopped")

    def _on_stop(self):
        """Stop live recording and reset toolbar and status label."""
        self._model.stop()
        self._update_button_state(False)
        self._state_label.setText("Stopped")

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
        if self._auto_scroll:
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

    def set_replay_state(self, playing: bool):
        """Update the status label to reflect the current replay state.

        Args:
            playing: ``True`` while a trace file is being replayed, ``False``
                when playback has stopped.
        """
        if playing:
            self._state_label.setText("Replaying")
        else:
            self._state_label.setText("Stopped")
