from PySide6.QtWidgets import QFileDialog


def get_dbc_file_path(parent=None) -> str:
    """Open a file dialog to select a DBC/KCD/ODX file. Returns path or empty string."""
    path, _ = QFileDialog.getOpenFileName(
        parent,
        "Import Database File",
        "",
        "Database Files (*.dbc *.kcd *.odx *.pdx *.odx-d);;"
        "DBC Files (*.dbc);;KCD Files (*.kcd);;"
        "ODX Files (*.odx *.pdx *.odx-d);;All Files (*)",
    )
    return path
