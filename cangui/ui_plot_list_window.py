"""Plot list dock window for managing signals displayed in the plot panel.

Provides a QTableView backed by PlotListModel where each row represents one
CAN signal selected for plotting.  The toolbar allows removing signals and
reordering them.  Per-signal style properties (color swatch, line width,
visibility) are edited in-place via custom delegates.  Changes to style
properties are broadcast via ``signal_settings_changed`` so that the companion
PlotWindow can update its pyqtgraph curves without a full rebuild.
"""

from PySide6.QtCore import Qt, Signal, QModelIndex, QTimer
from PySide6.QtWidgets import (
    QWidget, QToolBar, QTableView, QHeaderView,
    QColorDialog, QSpinBox, QStyledItemDelegate,
)
from PySide6.QtGui import QAction, QColor

from cangui.model_plot_list import PlotListModel, COL_COLOR, COL_WIDTH
from cangui.icons import icon as _icon
from cangui.ui_base_dock_window import BaseDockWindow


# Predefined colors for plot curves
COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
    "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
    "#bcbd22", "#17becf",
]


class _ColorDelegate(QStyledItemDelegate):
    """Paints a filled color swatch; editing is handled by the view's doubleClicked signal.

    The editor creation is intentionally suppressed here because color picking
    is done via a QColorDialog connected to the view's ``doubleClicked`` signal
    in PlotListWindow, which gives a richer picking experience than an inline
    editor widget.
    """

    def paint(self, painter, option, index):
        """Paint the cell as a bordered, filled color rectangle.

        Delegates the base paint call first, then overlays a filled rect
        inset by 4 px with a light grey border.

        Args:
            painter: Active QPainter for the viewport.
            option: Style option describing the cell geometry and state.
            index: Model index of the cell being rendered.
        """
        super().paint(painter, option, index)
        color_str = index.data(Qt.ItemDataRole.DisplayRole)
        if color_str:
            color = QColor(color_str)
            if color.isValid():
                rect = option.rect.adjusted(4, 4, -4, -4)
                painter.fillRect(rect, color)
                painter.setPen(QColor("#888888"))
                painter.drawRect(rect.adjusted(0, 0, -1, -1))

    def createEditor(self, parent, option, index):
        """Suppress inline editor creation; color picking uses QColorDialog.

        Args:
            parent: Parent widget (unused).
            option: Style option (unused).
            index: Model index (unused).

        Returns:
            None, preventing Qt from opening an inline editor.
        """
        return None  # handled via doubleClicked signal in PlotListWindow


class _WidthDelegate(QStyledItemDelegate):
    """Spin-box delegate that constrains the curve width column to 1–5 px."""

    def createEditor(self, parent, option, index):
        """Create a QSpinBox limited to the valid line-width range 1–5.

        Args:
            parent: Parent widget for the spin box.
            option: Style option (unused).
            index: Model index of the cell being edited (unused).

        Returns:
            A QSpinBox configured for integer line-width values 1–5.
        """
        sb = QSpinBox(parent)
        sb.setRange(1, 5)
        return sb

    def setEditorData(self, editor, index):
        """Populate the spin box from the model's current edit-role value.

        Falls back to 2 if the stored value is not an integer (e.g. on a
        freshly added row before the model supplies a value).

        Args:
            editor: The QSpinBox created by :meth:`createEditor`.
            index: Model index whose data should be reflected in the editor.
        """
        val = index.data(Qt.ItemDataRole.EditRole)
        editor.setValue(val if isinstance(val, int) else 2)

    def setModelData(self, editor, model, index):
        """Write the spin-box value back to the model.

        Args:
            editor: The QSpinBox whose current value should be committed.
            model: The item model owning the cell.
            index: Model index of the cell to update.
        """
        model.setData(index, editor.value(), Qt.ItemDataRole.EditRole)


class PlotListWindow(BaseDockWindow):
    """Plot list tab — shows plotted signals with editable style properties.

    Displays a QTableView backed by PlotListModel.  Rows are added via
    :meth:`add_signal` (typically triggered from a SignalSelectorDialog in the
    main window) and removed via the toolbar or :meth:`remove_signal`.
    Per-signal style edits (color, width, visibility) propagate to
    PlotWindow through the ``signal_settings_changed`` signal.

    Signals:
        signal_added: Emitted when a new signal row is successfully inserted.
            Arguments are ``(arb_id, signal_name, unit, color, width)``.
        signal_removed: Emitted when a signal row is deleted, carrying
            ``(arb_id, signal_name)``.
        signal_settings_changed: Emitted when the color, width, or visibility
            of an existing signal changes.  Carries
            ``(arb_id, signal_name, settings_dict)`` where *settings_dict*
            has keys ``color``, ``width``, and ``visible``.
        all_cleared: Emitted after the entire signal list has been erased and
            the color-rotation index has been reset.
    """

    TITLE = "Plot List"

    signal_added = Signal(int, str, str, str, int)    # arb_id, signal_name, unit, color, width
    signal_removed = Signal(int, str)                 # arb_id, signal_name
    signal_settings_changed = Signal(int, str, dict)  # arb_id, signal_name, {color,width,visible}
    all_cleared = Signal()

    def __init__(self, parent=None):
        """Initialize the PlotListWindow with toolbar and styled table view.

        Constructs the toolbar actions (remove, clear all, move up/down),
        configures the QTableView with color and width delegates, and wires
        model signals to column-resize and settings-change handlers.

        Args:
            parent: Optional parent QWidget.
        """
        super().__init__(parent)
        self._color_index = 0
        self._model = PlotListModel()

        # Toolbar
        toolbar = QToolBar()
        toolbar.setMovable(False)

        remove_action = QAction(_icon("remove"), "Remove Selected", self)
        remove_action.triggered.connect(self._on_remove)
        toolbar.addAction(remove_action)

        clear_action = QAction(_icon("trash"), "Clear All", self)
        clear_action.triggered.connect(self._on_clear_all)
        toolbar.addAction(clear_action)

        toolbar.addSeparator()

        up_action = QAction(_icon("up"), "Move Up", self)
        up_action.triggered.connect(self._on_move_up)
        toolbar.addAction(up_action)

        down_action = QAction(_icon("down"), "Move Down", self)
        down_action.triggered.connect(self._on_move_down)
        toolbar.addAction(down_action)

        self._layout.addWidget(toolbar)

        # Table
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.setItemDelegateForColumn(COL_COLOR, _ColorDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_WIDTH, _WidthDelegate(self._table))
        self._table.doubleClicked.connect(self._on_double_click)
        self._layout.addWidget(self._table)

        self._model.rowsInserted.connect(lambda *_: self._resize_columns())
        self._model.modelReset.connect(lambda *_: self._resize_columns())
        self._model.dataChanged.connect(self._on_model_data_changed)
        QTimer.singleShot(0, self._resize_columns)

    def _resize_columns(self):
        """Resize all columns except the last to fit their current contents."""
        for i in range(self._model.columnCount() - 1):
            self._table.resizeColumnToContents(i)

    @property
    def primary_view(self):
        """The signal table that receives keyboard focus."""
        return self._table

    def set_decoder(self, decoder):
        """Forward a new signal decoder to the underlying PlotListModel.

        Args:
            decoder: :class:`~cangui.signal_decoder.SignalDecoder` instance
                used to decode incoming CAN messages into signal values.
        """
        self._model.set_decoder(decoder)

    def on_message(self, msg):
        """Queue a single CAN message for the next model flush cycle.

        Args:
            msg: :class:`~cangui.can_message.CanMessage` to be buffered.
        """
        self._model.on_message(msg)

    def on_messages(self, messages):
        """Queue a batch of CAN messages for the next model flush cycle.

        Args:
            messages: List of :class:`~cangui.can_message.CanMessage` to buffer.
        """
        self._model.on_messages(messages)

    # -- Color picking via double-click --

    def _on_double_click(self, index: QModelIndex):
        """Open a QColorDialog when the user double-clicks the Color column.

        If the dialog is accepted, the chosen hex color is written back to the
        model via ``setData``, which then triggers ``_on_model_data_changed``
        and ultimately ``signal_settings_changed``.

        Args:
            index: Model index of the double-clicked cell.
        """
        if index.column() == COL_COLOR:
            current = QColor(index.data(Qt.ItemDataRole.EditRole) or "#ffffff")
            color = QColorDialog.getColor(current, self, "Select Curve Color")
            if color.isValid():
                self._model.setData(index, color.name(), Qt.ItemDataRole.EditRole)

    # -- Model change → signal_settings_changed --

    def _on_model_data_changed(self, top_left: QModelIndex, bottom_right: QModelIndex):
        """Re-emit ``signal_settings_changed`` when a style column changes.

        Filters the ``dataChanged`` range to only Color, Width, and Visible
        columns.  For each affected row that touches one of those columns the
        current style dict is packed and broadcast so PlotWindow can update
        the corresponding pyqtgraph curve.

        Args:
            top_left: Upper-left bound of the changed model region.
            bottom_right: Lower-right bound of the changed model region.

        Emits:
            signal_settings_changed: For each row in the changed region that
                has a style column modification.
        """
        settings_cols = {COL_COLOR, COL_WIDTH, 4}  # 4 = COL_VISIBLE
        changed_cols = set(range(top_left.column(), bottom_right.column() + 1))
        if not (settings_cols & changed_cols):
            return
        for row in range(top_left.row(), bottom_right.row() + 1):
            if row >= len(self._model.entries):
                continue
            entry = self._model.entries[row]
            self.signal_settings_changed.emit(entry.arb_id, entry.signal_name, {
                "color": entry.color,
                "width": entry.width,
                "visible": entry.visible,
            })

    # -- Toolbar handlers --

    def _on_remove(self):
        """Remove the currently selected signal row and emit ``signal_removed``.

        Does nothing if no row is selected or the selection is out of range.

        Emits:
            signal_removed: With ``(arb_id, signal_name)`` of the deleted entry.
        """
        index = self._table.currentIndex()
        if not index.isValid():
            return
        row = index.row()
        if row < len(self._model.entries):
            entry = self._model.entries[row]
            self._model.remove_entry(row)
            self.signal_removed.emit(entry.arb_id, entry.signal_name)

    def _on_clear_all(self):
        """Remove every signal row, reset the color rotation, and emit ``all_cleared``.

        Emits ``signal_removed`` for each entry before clearing the model so
        that PlotWindow can remove the corresponding pyqtgraph curves.

        Emits:
            signal_removed: Once per existing entry before the model is cleared.
            all_cleared: After the model and color index have been reset.
        """
        for entry in list(self._model.entries):
            self.signal_removed.emit(entry.arb_id, entry.signal_name)
        self._model.clear()
        self._color_index = 0
        self.all_cleared.emit()

    def _on_move_up(self):
        """Move the selected signal row one position up and follow the selection.

        Does nothing if no row is selected or the row is already at the top.
        """
        index = self._table.currentIndex()
        if not index.isValid():
            return
        row = index.row()
        self._model.move_up(row)
        self._table.setCurrentIndex(self._model.index(row - 1, 0))

    def _on_move_down(self):
        """Move the selected signal row one position down and follow the selection.

        Does nothing if no row is selected or the row is already at the bottom.
        """
        index = self._table.currentIndex()
        if not index.isValid():
            return
        row = index.row()
        self._model.move_down(row)
        self._table.setCurrentIndex(self._model.index(row + 1, 0))

    # -- Public API (matches original PlotListWindow interface) --

    def _next_color(self) -> str:
        """Return the next color from the rotation palette and advance the index.

        Returns:
            Hex color string (e.g. ``"#1f77b4"``) from :data:`COLORS`.
        """
        color = COLORS[self._color_index % len(COLORS)]
        self._color_index += 1
        return color

    def add_signal(self, arb_id: int, signal_name: str, unit: str = ""):
        """Add a signal to the plot list if it is not already present.

        Picks the next color from the rotation palette and uses a default line
        width of 2.  Silently ignores duplicate ``(arb_id, signal_name)`` pairs.

        Args:
            arb_id: CAN arbitration ID of the message carrying the signal.
            signal_name: DBC signal name within that message.
            unit: Physical unit string shown in the Unit column (default ``""``).

        Emits:
            signal_added: With ``(arb_id, signal_name, unit, color, width)`` if
                the entry was successfully added.
        """
        color = self._next_color()
        width = 2
        added = self._model.add_entry(arb_id, signal_name, unit, color, width)
        if added:
            self.signal_added.emit(arb_id, signal_name, unit, color, width)

    def remove_signal(self, arb_id: int, signal_name: str):
        """Remove a signal by identity and emit ``signal_removed``.

        Searches by ``(arb_id, signal_name)`` and removes the first match.
        Does nothing if the signal is not in the list.

        Args:
            arb_id: CAN arbitration ID of the message carrying the signal.
            signal_name: DBC signal name to remove.

        Emits:
            signal_removed: With ``(arb_id, signal_name)`` if the entry existed.
        """
        for i, entry in enumerate(self._model.entries):
            if entry.arb_id == arb_id and entry.signal_name == signal_name:
                self._model.remove_entry(i)
                self.signal_removed.emit(arb_id, signal_name)
                return

    def get_signal_settings(self, arb_id: int, signal_name: str) -> dict | None:
        """Return the current style settings for a tracked signal.

        Args:
            arb_id: CAN arbitration ID of the message carrying the signal.
            signal_name: DBC signal name.

        Returns:
            Dict with keys ``"color"`` (hex string), ``"width"`` (int 1–5), and
            ``"visible"`` (bool), or ``None`` if the signal is not tracked.
        """
        entry = self._model.get_entry(arb_id, signal_name)
        if entry is None:
            return None
        return {
            "color": entry.color,
            "width": entry.width,
            "visible": entry.visible,
        }
