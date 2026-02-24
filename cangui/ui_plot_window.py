"""Plot window panel for real-time time-series visualisation of CAN signals.

Uses pyqtgraph for hardware-accelerated rendering.  A QTimer fires every 50 ms
to pull fresh (time, value) arrays from PlotDataService and push them to the
corresponding PlotDataItem curves.  Toolbar controls manage recording state,
time-window span, and auto-range behaviour.  Keyboard shortcuts (+/-/Home)
allow quick zoom and reset without touching the mouse.
"""

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QToolBar, QHBoxLayout,
    QLabel, QDoubleSpinBox, QCheckBox,
)
from PySide6.QtGui import QAction, QKeySequence, QShortcut

import pyqtgraph as pg

from cangui.service_plot_data import PlotDataService
from cangui.icons import icon as _icon
from cangui.theme import SECONDARY_TEXT_STYLE
from cangui.ui_base_dock_window import BaseDockWindow


class PlotWindow(BaseDockWindow):
    """Signal plotting panel that renders live CAN signal curves via pyqtgraph.

    Each signal is represented by a named PlotDataItem curve registered in an
    internal dict keyed by ``(arb_id, signal_name)``.  A 50 ms QTimer calls
    PlotDataService to consume buffered data points and update every active
    curve.  The toolbar provides clear, time-span, auto-range, start, and stop
    controls.  Keyboard shortcuts (+, -, Home) control zoom and range reset.

    Signals:
        record_toggled: Emitted with ``True`` when the user presses Start or
            ``False`` when the user presses Stop.
    """

    TITLE = "Plot"

    record_toggled = Signal(bool)  # True = start recording, False = stop

    def __init__(self, plot_service: PlotDataService, parent=None):
        """Initialize the PlotWindow and build the toolbar and plot widget.

        Creates the pyqtgraph PlotWidget with a white background, grid, legend,
        and time axis label.  Registers keyboard shortcuts for zoom/reset and
        starts the 50 ms refresh timer.

        Args:
            plot_service: Shared PlotDataService that buffers signal samples and
                provides display-ready (time, values) arrays on demand.
            parent: Optional parent QWidget.
        """
        super().__init__(parent)
        self._plot_service = plot_service
        self._curves: dict[tuple[int, str], pg.PlotDataItem] = {}
        self._view_boxes: dict[tuple[int, str], pg.ViewBox] = {}
        self._axis_items: dict[tuple[int, str], pg.AxisItem] = {}
        self._axis_order: list[tuple[int, str]] = []
        self._axes_in_layout: set[tuple[int, str]] = set()
        self._auto_range = True

        # Toolbar
        toolbar = QToolBar()
        toolbar.setMovable(False)

        clear_action = QAction(_icon("trash"), "Clear Data", self)
        clear_action.triggered.connect(self._on_clear_data)
        toolbar.addAction(clear_action)

        toolbar.addSeparator()

        # Time span control
        toolbar.addWidget(QLabel(" Time span (s): "))
        self._time_span_spin = QDoubleSpinBox()
        self._time_span_spin.setRange(1.0, 3600.0)
        self._time_span_spin.setValue(plot_service.time_window)
        self._time_span_spin.setDecimals(1)
        self._time_span_spin.setSingleStep(1.0)
        self._time_span_spin.valueChanged.connect(self._on_time_span_changed)
        toolbar.addWidget(self._time_span_spin)

        toolbar.addSeparator()

        # Auto-range checkbox
        self._auto_range_cb = QCheckBox("Auto Range")
        self._auto_range_cb.setChecked(True)
        self._auto_range_cb.toggled.connect(self._on_auto_range_toggled)
        toolbar.addWidget(self._auto_range_cb)

        toolbar.addSeparator()

        self._start_action = QAction(_icon("record"), "Start", self)
        self._start_action.triggered.connect(self._on_start)
        toolbar.addAction(self._start_action)

        self._stop_action = QAction(_icon("stop"), "Stop", self)
        self._stop_action.setEnabled(False)
        self._stop_action.triggered.connect(self._on_stop)
        toolbar.addAction(self._stop_action)

        toolbar.addSeparator()

        self._file_label = QLabel("")
        self._file_label.setStyleSheet(SECONDARY_TEXT_STYLE)
        toolbar.addWidget(self._file_label)

        self._layout.addWidget(toolbar)

        # Plot widget — fills full area
        self._plot_widget = pg.PlotWidget()
        self._plot_widget.setBackground("w")
        self._plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self._plot_widget.setLabel("bottom", "Time", "s")
        self._legend = self._plot_widget.addLegend()
        self._plot_widget.setMouseEnabled(x=True, y=True)
        self._layout.addWidget(self._plot_widget)
        self._plot_item = self._plot_widget.getPlotItem()
        self._main_vb = self._plot_item.vb
        self._plot_item.showAxis("right", False)
        self._plot_item.showAxis("left", False)
        self._main_vb.sigResized.connect(self._update_viewboxes)

        # Keyboard shortcuts for pan/zoom
        self._setup_shortcuts()

        # Update timer
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(50)
        self._update_timer.timeout.connect(self._update_plot)
        self._update_timer.start()

    def _setup_shortcuts(self):
        """Register +, -, and Home keyboard shortcuts for zoom and range reset."""
        def _shortcut(key, slot):
            """Create a widget-scoped shortcut binding *key* to *slot*."""
            s = QShortcut(QKeySequence(key), self)
            s.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            s.activated.connect(slot)

        _shortcut(Qt.Key.Key_Plus, lambda: self._zoom(0.8))
        _shortcut(Qt.Key.Key_Minus, lambda: self._zoom(1.25))
        _shortcut(Qt.Key.Key_Home, self._reset_range)

    def _zoom(self, factor: float):
        """Scale both axes by *factor*, disabling auto-range first.

        Args:
            factor: Scale multiplier (< 1 zooms in, > 1 zooms out).
        """
        self._auto_range_cb.setChecked(False)
        self._main_vb.scaleBy((factor, 1.0))
        for vb in self._view_boxes.values():
            vb.scaleBy((1.0, factor))

    def _reset_range(self):
        """Re-enable auto-range and check the auto-range checkbox."""
        self._auto_range_cb.setChecked(True)
        self._set_auto_range(True)

    @property
    def primary_view(self):
        """The pyqtgraph PlotWidget that receives keyboard focus."""
        return self._plot_widget

    def set_update_interval(self, ms: int):
        """Set the curve-refresh timer interval, capped at 50 ms minimum.

        Args:
            ms: Desired interval in milliseconds.
        """
        self._update_timer.setInterval(max(50, ms))  # cap at 20 fps

    def add_signal_curve(self, arb_id: int, signal_name: str, unit: str,
                         color: str, width: int):
        """Add a curve for a signal. Called from MainWindow when PlotListWindow adds a signal."""
        key = (arb_id, signal_name)
        if key in self._curves:
            return
        self._plot_service.add_signal(arb_id, signal_name, unit)
        label = f"{signal_name}"
        if unit:
            label += f" ({unit})"
        curve = pg.PlotDataItem(pen=pg.mkPen(color, width=width), name=label)
        self._add_axis_for_signal(key, signal_name, unit, color)
        self._view_boxes[key].addItem(curve)
        if self._legend is not None:
            self._legend.addItem(curve, label)
        self._curves[key] = curve

    def remove_signal_curve(self, arb_id: int, signal_name: str):
        """Remove a signal curve."""
        key = (arb_id, signal_name)
        curve = self._curves.pop(key, None)
        if curve is not None:
            if self._legend is not None:
                self._legend.removeItem(curve)
            self._remove_axis_for_signal(key)
            self._plot_service.remove_signal(arb_id, signal_name)

    def update_curve_style(self, arb_id: int, signal_name: str, settings: dict):
        """Update curve color, width, visibility from PlotListWindow."""
        key = (arb_id, signal_name)
        curve = self._curves.get(key)
        if curve is None:
            return
        color = settings.get("color", "#1f77b4")
        width = settings.get("width", 2)
        visible = settings.get("visible", True)
        curve.setPen(pg.mkPen(color, width=width))
        curve.setVisible(visible)
        axis = self._axis_items.get(key)
        if axis is not None:
            pen = pg.mkPen(color)
            axis.setPen(pen)
            axis.setTextPen(pen)

    def clear_all_curves(self):
        """Remove all curves."""
        self._plot_service.clear()
        for key, curve in list(self._curves.items()):
            if self._legend is not None:
                self._legend.removeItem(curve)
            self._remove_axis_for_signal(key)
        self._curves.clear()
        self._axes_in_layout.clear()

    def _on_clear_data(self):
        """Clear plot data but keep curves."""
        self._plot_service.clear()

    def _on_time_span_changed(self, value: float):
        """Forward a new time-span value from the spinbox to PlotDataService.

        Args:
            value: New rolling window width in seconds.
        """
        self._plot_service.time_window = value

    def _on_auto_range_toggled(self, checked: bool):
        """Enable or disable pyqtgraph auto-range when the checkbox changes.

        Args:
            checked: ``True`` to enable auto-range, ``False`` to disable.
        """
        self._auto_range = checked
        self._set_auto_range(checked)

    def _on_start(self):
        """Emit :attr:`record_toggled` with ``True`` when the Start button is clicked."""
        self.record_toggled.emit(True)

    def _on_stop(self):
        """Emit :attr:`record_toggled` with ``False`` when the Stop button is clicked."""
        self.record_toggled.emit(False)

    def set_recording_state(self, recording: bool):
        """Update the Start/Stop toolbar actions to reflect the current recording state.

        Args:
            recording: ``True`` when recording is active (disables Start, enables Stop).
        """
        self._start_action.setEnabled(not recording)
        self._stop_action.setEnabled(recording)

    def set_active_file(self, path: str):
        """Update the file label to display the name of the current plot trace file.

        Args:
            path: Absolute path to the active file, or an empty string to clear the label.
        """
        if path:
            from pathlib import Path
            self._file_label.setText(Path(path).name)
            self._file_label.setToolTip(path)
        else:
            self._file_label.setText("")
            self._file_label.setToolTip("")

    def _update_plot(self):
        """Poll PlotDataService for new data and update all active curves.

        Called every 50 ms by the update timer.  Skips curves that have not
        received new data since the last call to avoid redundant setData calls.
        Updates the X-axis range to implement rolling window scrolling when
        auto-range is enabled.
        """
        any_updated = False
        latest_time = None
        for key, curve in self._curves.items():
            data = self._plot_service.consume_display_data(key)
            if data is None:
                continue
            curve.setData(data[0], data[1])
            any_updated = True
            # Track the latest timestamp for X-axis scrolling
            if len(data[0]) > 0:
                latest_time = max(latest_time or data[0][-1], data[0][-1])

        if any_updated and self._auto_range and latest_time is not None:
            # Set X-axis range to show the rolling time window
            time_window = self._plot_service.time_window
            x_min = latest_time - time_window
            x_max = latest_time
            self._main_vb.setXRange(x_min, x_max, padding=0)

    def _add_axis_for_signal(self, key: tuple[int, str], label: str,
                             unit: str, color: str):
        """Create and register a dedicated Y axis + ViewBox for a signal."""
        axis = pg.AxisItem("right")
        axis.setLabel(label, units=unit)
        pen = pg.mkPen(color)
        axis.setPen(pen)
        axis.setTextPen(pen)
        vb = pg.ViewBox()
        vb.setXLink(self._main_vb)
        self._plot_item.scene().addItem(vb)
        axis.linkToView(vb)
        self._view_boxes[key] = vb
        self._axis_items[key] = axis
        self._axis_order.append(key)
        self._reflow_axes()
        self._update_viewboxes()

    def _remove_axis_for_signal(self, key: tuple[int, str]):
        """Remove the Y axis + ViewBox for a signal."""
        axis = self._axis_items.pop(key, None)
        vb = self._view_boxes.pop(key, None)
        if axis is not None:
            self._plot_item.layout.removeItem(axis)
        if vb is not None:
            scene = self._plot_item.scene()
            if scene is not None:
                scene.removeItem(vb)
        if key in self._axis_order:
            self._axis_order.remove(key)
        self._axes_in_layout.discard(key)
        self._reflow_axes()

    def _reflow_axes(self):
        """Pack all signal axes into consecutive layout columns."""
        layout = self._plot_item.layout
        # Only remove axes that are currently in the layout
        for key in list(self._axes_in_layout):
            axis = self._axis_items.get(key)
            if axis is not None:
                layout.removeItem(axis)
        self._axes_in_layout.clear()
        # Add axes in the correct order
        base_col = 3  # 0=left axis, 1=plot, 2=right axis (hidden)
        for idx, key in enumerate(self._axis_order):
            axis = self._axis_items[key]
            layout.addItem(axis, 2, base_col + idx)
            self._axes_in_layout.add(key)

    def _update_viewboxes(self):
        """Keep all signal ViewBoxes aligned with the main plot geometry."""
        rect = self._main_vb.sceneBoundingRect()
        for vb in self._view_boxes.values():
            vb.setGeometry(rect)
            vb.linkedViewChanged(self._main_vb, vb.XAxis)

    def _set_auto_range(self, enabled: bool):
        """Enable/disable rolling window scrolling (X) and Y-axis auto-scaling per signal.

        When enabled, the X-axis shows a fixed-width rolling time window (updated by
        _update_plot) and each signal's Y-axis auto-scales to fit visible data.
        When disabled, both axes freeze at their current positions.
        """
        if enabled:
            # Don't use auto-range on X-axis; we manually set it in _update_plot for rolling window
            self._main_vb.disableAutoRange(axis=pg.ViewBox.XAxis)
            for vb in self._view_boxes.values():
                vb.enableAutoRange(axis=pg.ViewBox.YAxis)
        else:
            self._main_vb.disableAutoRange(axis=pg.ViewBox.XAxis)
            for vb in self._view_boxes.values():
                vb.disableAutoRange(axis=pg.ViewBox.YAxis)
