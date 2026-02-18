import json
from pathlib import Path

import cantools

from PySide6.QtCore import Qt, Signal, QModelIndex
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QToolBar, QHeaderView,
    QFileDialog, QMessageBox, QStyledItemDelegate, QComboBox,
)
from PySide6.QtGui import QAction

from cangui.model_database import DatabaseModel, COL_BYTE_ORDER
from cangui.ui_tab_navigation import TabTreeView
from cangui.icons import icon as _icon

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
    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.addItems(["little_endian", "big_endian"])
        return combo

    def setEditorData(self, editor, index):
        value = index.data(Qt.ItemDataRole.EditRole)
        idx = editor.findText(value)
        if idx >= 0:
            editor.setCurrentIndex(idx)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)


class DatabaseWindow(QWidget):
    """Database editor tab — view/edit CAN message and signal definitions."""

    TITLE = "Database"

    database_changed = Signal()
    dbc_imported = Signal(str)  # file path
    add_to_watch_requested = Signal(int, str, str, str)  # arb_id, signal_name, unit, direction
    add_to_plot_requested = Signal(int, str, str)  # arb_id, signal_name, unit

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

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

        layout.addWidget(toolbar)

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

        layout.addWidget(self._tree)

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
        return self._tree

    # -- Toolbar actions --

    def _on_add_database(self):
        self._model.add_file()

    def _on_add_message(self):
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
        index = self._tree.currentIndex()
        if not index.isValid():
            return
        self._model.remove_row(index)

    def _on_import_dbc(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import DBC", "", "DBC Files (*.dbc);;All Files (*)")
        if not path:
            return
        try:
            self.import_dbc(path)
        except Exception as e:
            QMessageBox.warning(self, "Import Error", f"Failed to import DBC:\n{e}")

    def _on_export_dbc(self):
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
        path, _ = QFileDialog.getOpenFileName(
            self, "Import JSON Database", "",
            "JSON Files (*.json *.db.json);;All Files (*)")
        if not path:
            return
        try:
            with open(path) as f:
                data = json.load(f)
            self._model.from_dict(data)
        except Exception as e:
            QMessageBox.warning(self, "Import Error", f"Failed to import JSON:\n{e}")

    def _on_export_json(self):
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

    # -- DBC import/export --

    def import_dbc(self, path: str):
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
        db = self._model.export_to_cantools()
        return db.as_dbc_string()

    # -- Serialization --

    def to_dict(self) -> list[dict]:
        return self._model.to_dict()

    def manual_databases_to_dict(self) -> list[dict]:
        """Return only manually created databases (not imported from DBC)."""
        return self._model.to_dict(manual_only=True)

    def from_dict(self, data: list[dict]):
        self._model.from_dict(data)
        self._resize_columns()

    def append_from_dict(self, data: list[dict]):
        """Append file entries from dict without clearing existing data."""
        self._model.append_from_dict(data)
        self._resize_columns()
