import json
from pathlib import Path

import cantools

from PySide6.QtCore import Qt, Signal, QModelIndex, QTimer
from PySide6.QtWidgets import (
    QWidget, QToolBar, QHeaderView,
    QFileDialog, QMessageBox, QStyledItemDelegate, QComboBox,
)
from PySide6.QtGui import QAction

from cangui.model_database import DatabaseModel, COL_BYTE_ORDER
from cangui.ui_tab_navigation import TabTreeView
from cangui.icons import icon as _icon
from cangui.ui_base_dock_window import BaseDockWindow

# Widths for columns 1-15 of the database tree (col 0 = Name, sized dynamically;
# col 16 = Comment, stretches as last section).
_DB_COL_WIDTHS = {
    1:  50,   # Bus
    2:  80,   # ID (hex)
    3:  45,   # DLC
    4:  72,   # Cycle (ms)
    5:  65,   # Start Bit
    6:  72,   # Bit Length
    7: 105,   # Byte Order  ("little_endian" is wide)
    8:  58,   # Signed
    9:  65,   # Factor
    10: 65,   # Offset
    11: 65,   # Min
    12: 65,   # Max
    13: 60,   # Unit
    14: 50,   # Mux
    15: 90,   # Values
}


class ByteOrderDelegate(QStyledItemDelegate):
    """Dropdown delegate for the Byte Order column (little_endian / big_endian)."""

    def createEditor(self, parent, option, index):
        """Return a QComboBox pre-populated with the two byte-order choices.

        Args:
            parent: Parent widget for the combo box.
            option: Style option (unused).
            index: Model index of the cell being edited (unused).

        Returns:
            A QComboBox with ``"little_endian"`` and ``"big_endian"`` items.
        """
        combo = QComboBox(parent)
        combo.addItems(["little_endian", "big_endian"])
        return combo

    def setEditorData(self, editor, index):
        """Select the current byte-order value in the combo-box editor.

        Args:
            editor: The QComboBox created by :meth:`createEditor`.
            index: Model index whose edit-role data should be reflected.
        """
        value = index.data(Qt.ItemDataRole.EditRole)
        idx = editor.findText(value)
        if idx >= 0:
            editor.setCurrentIndex(idx)

    def setModelData(self, editor, model, index):
        """Write the selected byte-order string back to the model.

        Args:
            editor: The QComboBox whose current text should be committed.
            model: The item model owning the cell.
            index: Model index of the cell to update.
        """
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)


class DatabaseWindow(BaseDockWindow):
    """Database editor tab — view/edit CAN message and signal definitions."""

    TITLE = "Database"

    database_changed = Signal()
    dbc_imported = Signal(str)  # file path
    add_to_watch_requested = Signal(int, str, str, str)  # arb_id, signal_name, unit, direction
    add_to_plot_requested = Signal(int, str, str)  # arb_id, signal_name, unit
    add_to_tx_requested = Signal(int, int, bool, str, int, int)  # can_id, dlc, is_extended, symbol, cycle_ms, bus

    def __init__(self, parent=None):
        """Build the DatabaseWindow with its toolbar and tree view.

        Constructs all toolbar actions (add database/message/signal, remove,
        move up/down, import/export DBC/JSON, add to Watch/Plot/TX), wires
        model change signals to ``database_changed``, and schedules an initial
        column resize.

        Args:
            parent: Optional parent QWidget.
        """
        super().__init__(parent)

        # Toolbar
        toolbar = QToolBar()
        toolbar.setMovable(False)

        add_db = QAction(_icon("database"), "Add Database", self)
        add_db.triggered.connect(self._on_add_database)
        toolbar.addAction(add_db)

        add_msg = QAction(_icon("message"), "Add Message", self)
        add_msg.triggered.connect(self._on_add_message)
        toolbar.addAction(add_msg)

        add_sig = QAction(_icon("signal"), "Add Signal", self)
        add_sig.triggered.connect(self._on_add_signal)
        toolbar.addAction(add_sig)

        remove = QAction(_icon("trash"), "Remove Selected", self)
        remove.triggered.connect(self._on_remove_selected)
        toolbar.addAction(remove)

        toolbar.addSeparator()

        up_action = QAction(_icon("up"), "Move Up", self)
        up_action.triggered.connect(self._on_move_up)
        toolbar.addAction(up_action)

        down_action = QAction(_icon("down"), "Move Down", self)
        down_action.triggered.connect(self._on_move_down)
        toolbar.addAction(down_action)

        toolbar.addSeparator()

        import_dbc = QAction(_icon("import"), "Import DBC", self)
        import_dbc.triggered.connect(self._on_import_dbc)
        toolbar.addAction(import_dbc)

        export_dbc = QAction(_icon("export"), "Export DBC", self)
        export_dbc.triggered.connect(self._on_export_dbc)
        toolbar.addAction(export_dbc)

        toolbar.addSeparator()

        import_json = QAction(_icon("import"), "Import JSON", self)
        import_json.triggered.connect(self._on_import_json)
        toolbar.addAction(import_json)

        export_json = QAction(_icon("export"), "Export JSON", self)
        export_json.triggered.connect(self._on_export_json)
        toolbar.addAction(export_json)

        toolbar.addSeparator()

        add_watch = QAction(_icon("watch"), "Add to Watch", self)
        add_watch.triggered.connect(self._on_add_to_watch)
        toolbar.addAction(add_watch)

        add_plot = QAction(_icon("plot"), "Add to Plot", self)
        add_plot.triggered.connect(self._on_add_to_plot)
        toolbar.addAction(add_plot)

        add_tx = QAction(_icon("send"), "Add to TX", self)
        add_tx.setToolTip("Add selected message to the TX list")
        add_tx.triggered.connect(self._on_add_to_tx)
        toolbar.addAction(add_tx)

        self._layout.addWidget(toolbar)

        # Model + TreeView
        self._model = DatabaseModel()
        self._model.dataChanged.connect(lambda *_: self.database_changed.emit())
        self._model.rowsInserted.connect(lambda *_: self.database_changed.emit())
        self._model.rowsRemoved.connect(lambda *_: self.database_changed.emit())
        self._model.modelReset.connect(lambda *_: self.database_changed.emit())

        self._tree = TabTreeView()
        self._tree.setModel(self._model)
        self._tree.setRootIsDecorated(True)
        self._tree.setAlternatingRowColors(True)
        self._tree.setSelectionBehavior(TabTreeView.SelectionBehavior.SelectRows)
        self._tree.setSelectionMode(TabTreeView.SelectionMode.SingleSelection)
        self._tree.setItemDelegateForColumn(COL_BYTE_ORDER, ByteOrderDelegate(self._tree))

        header = self._tree.header()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)

        self._layout.addWidget(self._tree)
        QTimer.singleShot(0, self._resize_columns)

    def _resize_columns(self):
        """Resize columns to sensible widths after data is loaded."""
        header = self._tree.header()
        # Name column: fit to visible content, minimum 150 px
        self._tree.resizeColumnToContents(0)
        if header.sectionSize(0) < 150:
            header.resizeSection(0, 150)
        # Fixed widths for all data columns
        for col, width in _DB_COL_WIDTHS.items():
            header.resizeSection(col, width)

    @property
    def primary_view(self):
        """The database tree view that receives keyboard focus."""
        return self._tree

    # -- Toolbar actions --

    def _on_add_database(self):
        """Add a new empty database file entry to the model."""
        self._model.add_file()

    def _on_add_message(self):
        """Add a new blank message to the file containing the current selection.

        If nothing is selected, the message is appended to the last file (or a
        new one is created).  If a file node is selected, the tree is expanded
        to show the new child.
        """
        index = self._tree.currentIndex()
        if not index.isValid():
            # Add to last file, or create one
            self._model.add_message()
            return
        level = self._model._get_level(index)
        if level == 0:
            # File selected — add message to this file
            self._model.add_message(index.row())
            self._tree.expand(index)
        elif level == 1:
            # Message selected — add message to same file
            file_row, _ = self._model.get_message_row(index)
            self._model.add_message(file_row)
        else:
            # Signal selected — add message to parent file
            file_row, _ = self._model.get_message_row(index)
            self._model.add_message(file_row)

    def _on_add_signal(self):
        """Add a new blank signal to the message containing the current selection.

        Shows an information dialog if no message context can be determined.
        Expands the file and message nodes so the new signal is immediately
        visible.
        """
        index = self._tree.currentIndex()
        if not index.isValid():
            QMessageBox.information(self, "Add Signal", "Select a message first.")
            return
        file_row, msg_row = self._model.get_message_row(index)
        if file_row < 0 or msg_row < 0:
            QMessageBox.information(self, "Add Signal", "Select a message first.")
            return
        self._model.add_signal(file_row, msg_row)
        # Expand the parent so the new signal is visible
        file_idx = self._model.index(file_row, 0)
        msg_idx = self._model.index(msg_row, 0, file_idx)
        self._tree.expand(file_idx)
        self._tree.expand(msg_idx)

    def _on_remove_selected(self):
        """Remove the currently selected file, message, or signal node."""
        index = self._tree.currentIndex()
        if not index.isValid():
            return
        self._model.remove_row(index)

    def _on_move_up(self):
        """Move the selected node one row up and follow the selection."""
        index = self._tree.currentIndex()
        if not index.isValid():
            return
        row = index.row()
        self._model.move_up(index)
        parent = index.parent()
        new_index = self._model.index(row - 1, 0, parent)
        self._tree.setCurrentIndex(new_index)

    def _on_move_down(self):
        """Move the selected node one row down and follow the selection."""
        index = self._tree.currentIndex()
        if not index.isValid():
            return
        row = index.row()
        self._model.move_down(index)
        parent = index.parent()
        new_index = self._model.index(row + 1, 0, parent)
        self._tree.setCurrentIndex(new_index)

    def _on_import_dbc(self):
        """Open a file dialog and import a DBC file, showing an error on failure."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Import DBC", "", "DBC Files (*.dbc);;All Files (*)")
        if not path:
            return
        try:
            self.import_dbc(path)
        except Exception as e:
            QMessageBox.warning(self, "Import Error", f"Failed to import DBC:\n{e}")

    def _on_export_dbc(self):
        """Open a save dialog and export the current database as a DBC file."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Export DBC", "", "DBC Files (*.dbc);;All Files (*)")
        if not path:
            return
        if not path.endswith(".dbc"):
            path += ".dbc"
        try:
            dbc_string = self.export_dbc()
            with open(path, "w") as f:
                f.write(dbc_string)
        except Exception as e:
            QMessageBox.warning(self, "Export Error", f"Failed to export DBC:\n{e}")

    def _on_import_json(self):
        """Open a file dialog and import a .db.json database file."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Import JSON Database", "",
            "JSON Files (*.json *.db.json);;All Files (*)")
        if path:
            self.import_from_json(path)

    def import_from_json(self, path: str):
        """Import a .db.json file into the database model."""
        try:
            with open(path) as f:
                data = json.load(f)
            self._model.from_dict(data)
        except Exception as e:
            QMessageBox.warning(self, "Import Error", f"Failed to import JSON:\n{e}")

    def _on_export_json(self):
        """Open a save dialog and export the database model as a .json file."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Export JSON Database", "",
            "JSON Files (*.json);;All Files (*)")
        if not path:
            return
        if not path.endswith(".json"):
            path += ".json"
        try:
            with open(path, "w") as f:
                json.dump(self._model.to_dict(), f, indent=2)
        except Exception as e:
            QMessageBox.warning(self, "Export Error", f"Failed to export JSON:\n{e}")

    def _on_add_to_watch(self):
        """Add the selected signal (or all signals of the selected message) to Watch.

        If a signal row is selected only that signal is added.  If a message
        row is selected all of its signals are added.  An information dialog
        is shown if neither could be resolved.

        Emits:
            add_to_watch_requested: Once per signal with
                ``(can_id, name, unit, "Db")``.
        """
        index = self._tree.currentIndex()
        # Signal selected → add that one signal
        result = self._model.get_signal(index)
        if result is not None:
            msg, sig = result
            self.add_to_watch_requested.emit(msg.can_id, sig.name, sig.unit, "Db")
            return
        # Message selected → add all signals in the message
        msg = self._model.get_message(index)
        if msg is not None:
            for sig in msg.signals:
                self.add_to_watch_requested.emit(msg.can_id, sig.name, sig.unit, "Db")
            return
        QMessageBox.information(self, "Add to Watch",
                                "Select a message or signal.")

    def _on_add_to_plot(self):
        """Add the selected signal (or all signals of the selected message) to Plot.

        If a signal row is selected only that signal is added.  If a message
        row is selected all of its signals are added.  An information dialog
        is shown if neither could be resolved.

        Emits:
            add_to_plot_requested: Once per signal with ``(can_id, name, unit)``.
        """
        index = self._tree.currentIndex()
        # Signal selected → add that one signal
        result = self._model.get_signal(index)
        if result is not None:
            msg, sig = result
            self.add_to_plot_requested.emit(msg.can_id, sig.name, sig.unit)
            return
        # Message selected → add all signals in the message
        msg = self._model.get_message(index)
        if msg is not None:
            for sig in msg.signals:
                self.add_to_plot_requested.emit(msg.can_id, sig.name, sig.unit)
            return
        QMessageBox.information(self, "Add to Plot",
                                "Select a message or signal.")

    def _on_add_to_tx(self):
        """Add the message containing the current selection to the TX list.

        Resolves both message and signal selections to the parent message.
        Uses the source file's bus number for the new TX entry.

        Emits:
            add_to_tx_requested: With ``(can_id, dlc, is_extended, name,
                cycle_ms, bus)`` when a message is successfully resolved.
        """
        index = self._tree.currentIndex()
        # Resolve to the message regardless of whether a message or signal is selected
        msg = self._model.get_message(index)
        if msg is None:
            result = self._model.get_signal(index)
            if result is not None:
                msg, _ = result
        if msg is None:
            QMessageBox.information(self, "Add to TX", "Select a message first.")
            return
        file_row, _ = self._model.get_message_row(index)
        bus = (self._model._items[file_row].bus
               if 0 <= file_row < len(self._model._items) else 1)
        is_extended = msg.can_id > 0x7FF
        self.add_to_tx_requested.emit(
            msg.can_id, msg.dlc, is_extended, msg.name, msg.cycle_time_ms, bus)

    # -- DBC import/export --

    def import_dbc(self, path: str):
        """Import a DBC file, append its contents to the model, and emit ``dbc_imported``.

        Args:
            path: Absolute path to the ``.dbc`` file to import.

        Emits:
            dbc_imported: With the imported file path on success.
        """
        db = cantools.database.Database()
        db.add_dbc_file(path)
        filename = Path(path).name
        self._model.import_from_cantools(db, filename=filename,
                                         source_path=path, append=True)
        self._resize_columns()
        self.dbc_imported.emit(path)

    def import_dbc_silent(self, path: str):
        """Import a DBC without emitting dbc_imported (used on project load)."""
        db = cantools.database.Database()
        db.add_dbc_file(path)
        filename = Path(path).name
        self._model.import_from_cantools(db, filename=filename,
                                         source_path=path, append=True)
        self._resize_columns()

    def remove_dbc(self, path: str):
        """Remove the file-level database entry matching path."""
        self._model.remove_by_source_path(path)

    def export_dbc(self) -> str:
        """Export the current database as a DBC-format string.

        Returns:
            DBC content as a text string suitable for writing to a ``.dbc`` file.
        """
        db = self._model.export_to_cantools()
        return db.as_dbc_string()

    # -- Serialization --

    def to_dict(self) -> list[dict]:
        """Serialize the entire database model to a list of file-entry dicts.

        Returns:
            List of dicts representing all file entries, for project persistence.
        """
        return self._model.to_dict()

    def manual_databases_to_dict(self) -> list[dict]:
        """Return only manually created databases (not imported from DBC)."""
        return self._model.to_dict(manual_only=True)

    def from_dict(self, data: list[dict]):
        """Load a complete database from a list of file-entry dicts.

        Replaces existing model contents and resizes columns afterward.

        Args:
            data: List of file-entry dicts as returned by :meth:`to_dict`.
        """
        self._model.from_dict(data)
        self._resize_columns()

    def append_from_dict(self, data: list[dict]):
        """Append file entries from dict without clearing existing data."""
        self._model.append_from_dict(data)
        self._resize_columns()
