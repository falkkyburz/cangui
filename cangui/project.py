"""Project file I/O: save, load, and portable path handling.

A cangui project is a JSON file that stores all session state: database file
paths, TX messages, watch signals, connection configs, and the workspace
layout.  File paths are stored in a *portable* form (relative where possible)
so projects can be shared across machines and operating systems.

The project file format is versioned via the ``version`` integer field.
:data:`PROJECT_FORMAT_VERSION` holds the current writer version; files without
this field are treated as version 0 for backwards compatibility.

See :doc:`/architecture/persistence` for a complete field inventory and path
portability strategy.
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path

_log = logging.getLogger(__name__)

#: Increment this whenever a backwards-incompatible change is made to the
#: project file format.  Old files without a ``version`` field are treated
#: as version 0 (pre-versioning) and loaded with best-effort compatibility.
PROJECT_FORMAT_VERSION = 1


@dataclass
class ProjectData:
    """Flat data bag for all project-level state written to the JSON file.

    All file paths in list fields (``database_files``, ``trace_files``,
    ``plot_files``) are stored as *absolute* strings on disk after
    :meth:`Project.save` resolves them from the portable form.  At load time
    :meth:`Project.load` resolves stored paths back to absolute form via
    :meth:`Project._resolve_stored_path`.

    Attributes:
        name: Project name, derived from the filename stem.
        database_files: Absolute paths to loaded DBC/KCD/ODX files.
        trace_files: Absolute paths to TRC/BLF trace recordings.
        watch_signals: List of watch signal dicts
            ``{arb_id, signal_name, display_name, unit, direction}``.
        tx_messages: List of TX message config dicts.
        connections: List of CAN connection config dicts.
        watch_dids: List of DID polling entry dicts ``{did, name, cycle_ms}``.
        rx_filters: List of RX filter rule dicts.
        workspace_state: JSON string capturing splitter sizes and tab layout.
        settings: Per-project settings overrides (see :mod:`cangui.options`).
        plot_files: Absolute paths to plot trace recordings.
        database_editor_file: Basename of the sibling ``.db.json`` file, or
            empty string if none.
        script_plugin_file: Absolute path to the active ``.script.py`` plugin.
        seedkey_file: Absolute path to the active ``.seedkey.py`` plugin.
    """

    name: str = "Untitled"
    database_files: list[str] = field(default_factory=list)
    trace_files: list[str] = field(default_factory=list)
    watch_signals: list[dict] = field(default_factory=list)
    tx_messages: list[dict] = field(default_factory=list)
    connections: list[dict] = field(default_factory=list)
    watch_dids: list[dict] = field(default_factory=list)
    rx_filters: list[dict] = field(default_factory=list)
    workspace_state: str = ""  # JSON splitter/tab state
    settings: dict = field(default_factory=dict)
    plot_files: list[str] = field(default_factory=list)
    database_editor_file: str = ""
    script_plugin_file: str = ""
    seedkey_file: str = ""


class Project:
    """Manages a single cangui project: data, file path, and dirty flag.

    The public API is:

    * :meth:`new` — reset to an empty project.
    * :meth:`load` — deserialise from a JSON file.
    * :meth:`save` — serialise to JSON (with portable paths).
    * :meth:`mark_modified` — flag the project as dirty.
    * Convenience add/remove methods for each file-list field.
    """

    def __init__(self):
        """Create a new, empty, unsaved project."""
        self._data = ProjectData()
        self._path: Path | None = None
        self._modified = False

    @property
    def data(self) -> ProjectData:
        """Direct access to the mutable :class:`ProjectData` bag."""
        return self._data

    @property
    def path(self) -> Path | None:
        """Absolute path to the project JSON file, or ``None`` if unsaved."""
        return self._path

    @property
    def name(self) -> str:
        """Project name (filename stem)."""
        return self._data.name

    @name.setter
    def name(self, value: str):
        """Set the project name and mark the project as modified."""
        self._data.name = value
        self._modified = True

    @property
    def is_modified(self) -> bool:
        """``True`` if the project has unsaved changes."""
        return self._modified

    def mark_modified(self):
        """Set the dirty flag without changing any data.

        Call this after externally mutating :attr:`data` fields that are not
        covered by the typed add/remove helpers.
        """
        self._modified = True

    # --- Path helpers ---

    def _resolve_stored_path(self, stored: str) -> str:
        """
        Resolve a path from the project JSON to an absolute path on the current
        platform.  Resolution order:

        1. Relative paths → resolved against the project file's directory.
        2. Absolute paths that exist on this machine → used as-is.
        3. Absolute paths that don't exist (cross-platform / moved project):
           try the bare filename next to the project file as a fallback.
        4. Give up and return the stored string unchanged (will show as
           "File not found" in the UI).
        """
        p = Path(stored)
        if not p.is_absolute():
            # Relative: resolve against project dir
            if self._path is not None:
                return str((self._path.parent / p).resolve())
            return str(p.resolve())

        # Absolute: try it directly first
        if p.exists():
            return str(p.resolve())

        # Absolute but not found on this machine: try just the filename
        # next to the project file (covers cross-platform / moved projects)
        if self._path is not None:
            candidate = self._path.parent / p.name
            if candidate.exists():
                return str(candidate.resolve())

        # No match found – return as-is so the UI can display "File not found"
        return stored

    def _make_portable(self, path: str) -> str:
        """
        Always store paths as forward-slash (POSIX/Unix) strings.

        Tries to produce a path relative to the project file using walk_up so
        that paths outside the project directory are expressed as ../../ style
        relatives.  Falls back to an absolute POSIX path only when the project
        is unsaved or a relative path is impossible (e.g. different Windows
        drives).  Forward slashes are always used regardless of platform.
        """
        resolved = Path(path).resolve()
        if self._path is not None:
            try:
                return resolved.relative_to(
                    self._path.parent.resolve(), walk_up=True
                ).as_posix()
            except ValueError:
                pass  # Different drive on Windows – fall through to absolute
        return resolved.as_posix()

    def _norm(self, path: str) -> Path:
        """Resolved Path used only for duplicate detection."""
        return Path(path).resolve()

    # --- File list management ---

    def _add_to(self, lst: list[str], path: str):
        """Append *path* to *lst* if it is not already present (by resolved form).

        Args:
            lst: One of the file-list fields in :attr:`data`.
            path: Path to add (resolved to absolute before comparison).
        """
        resolved = str(Path(path).resolve())
        norm = self._norm(resolved)
        if not any(self._norm(p) == norm for p in lst):
            lst.append(resolved)
            self._modified = True

    def _remove_from(self, lst: list[str], path: str):
        """Remove the entry matching *path* from *lst* (by resolved form).

        Args:
            lst: One of the file-list fields in :attr:`data`.
            path: Path to remove.  No-op if not found.
        """
        norm = self._norm(path)
        for i, p in enumerate(lst):
            if self._norm(p) == norm:
                lst.pop(i)
                self._modified = True
                return

    def set_script_plugin(self, path: str):
        """Set the active script plugin path.

        Args:
            path: Absolute or relative path to a ``.script.py`` file.
                Pass an empty string to clear.
        """
        self._data.script_plugin_file = str(Path(path).resolve()) if path else ""
        self._modified = True

    def set_seedkey_file(self, path: str):
        """Set the active seed-key plugin path.

        Args:
            path: Absolute or relative path to a ``.seedkey.py`` file.
                Pass an empty string to clear.
        """
        self._data.seedkey_file = str(Path(path).resolve()) if path else ""
        self._modified = True

    def add_database_file(self, path: str):
        """Add a database file (DBC/KCD/ODX) to the project, deduplicated."""
        self._add_to(self._data.database_files, path)

    def remove_database_file(self, path: str):
        """Remove a database file from the project by path."""
        self._remove_from(self._data.database_files, path)

    def add_trace_file(self, path: str):
        """Register a trace recording file with the project, deduplicated."""
        self._add_to(self._data.trace_files, path)

    def remove_trace_file(self, path: str):
        """Remove a trace recording file from the project."""
        self._remove_from(self._data.trace_files, path)

    def add_plot_file(self, path: str):
        """Register a plot trace file with the project, deduplicated."""
        self._add_to(self._data.plot_files, path)

    def remove_plot_file(self, path: str):
        """Remove a plot trace file from the project."""
        self._remove_from(self._data.plot_files, path)

    @property
    def trace_folder(self) -> Path | None:
        """Return the trace folder next to the project file, or None if unsaved."""
        if self._path is None:
            return None
        return self._path.parent / "trace"

    @property
    def plot_folder(self) -> Path | None:
        """Return the plot trace folder next to the project file, or None if unsaved."""
        if self._path is None:
            return None
        return self._path.parent / "plot"

    # --- Persistence ---

    def save(self, path: str | Path | None = None):
        """Serialise the project to a JSON file.

        Updates :attr:`_path` if *path* is given, then writes all
        :class:`ProjectData` fields plus a ``"version"`` key to disk.
        File-list paths are stored in portable (relative where possible) form
        via :meth:`_make_portable`.  Clears :attr:`is_modified` on success.

        Args:
            path: Destination file path.  If omitted, the previously set
                :attr:`path` is used.

        Raises:
            ValueError: If no path has ever been set and *path* is ``None``.
            OSError: If the file cannot be written (permission denied, disk
                full, etc.).
        """
        if path is not None:
            self._path = Path(path)
        if self._path is None:
            raise ValueError("No path specified")
        self._data.name = self._path.stem
        data = asdict(self._data)
        data["version"] = PROJECT_FORMAT_VERSION
        # Store file lists as portable relative paths where possible
        data["database_files"] = [self._make_portable(f)
                                   for f in self._data.database_files]
        data["trace_files"] = [self._make_portable(f)
                                for f in self._data.trace_files]
        data["plot_files"] = [self._make_portable(f)
                               for f in self._data.plot_files]
        data["script_plugin_file"] = (self._make_portable(self._data.script_plugin_file)
                                    if self._data.script_plugin_file else "")
        data["seedkey_file"] = (self._make_portable(self._data.seedkey_file)
                                if self._data.seedkey_file else "")
        with open(self._path, "w") as f:
            json.dump(data, f, indent=2)
        self._modified = False

    def load(self, path: str | Path):
        """Deserialise a project from a JSON file.

        Resolves all stored paths back to absolute form via
        :meth:`_resolve_stored_path` and deduplicates them.  Handles old
        field aliases (``rxtx_plugin_file``, ``e2e_plugin_file``) for
        backwards compatibility.  Clears :attr:`is_modified` on success.

        Args:
            path: Path to the ``.cangui`` JSON project file.

        Raises:
            FileNotFoundError: If *path* does not exist.
            json.JSONDecodeError: If the file is not valid JSON.
        """
        self._path = Path(path).resolve()
        with open(self._path) as f:
            raw = json.load(f)

        file_version = raw.get("version", 0)
        if file_version > PROJECT_FORMAT_VERSION:
            _log.warning(
                "Project file '%s' was saved with format version %d, "
                "but this build only supports up to version %d. "
                "Some fields may be ignored.",
                self._path.name, file_version, PROJECT_FORMAT_VERSION,
            )

        def _load_paths(paths: list) -> list[str]:
            """Resolve and deduplicate a list of stored path strings."""
            result: list[str] = []
            seen: list[Path] = []
            for stored in paths:
                resolved_str = self._resolve_stored_path(stored)
                # Deduplicate by resolved form
                try:
                    resolved = Path(resolved_str).resolve()
                except Exception:
                    resolved = Path(resolved_str)
                if resolved not in seen:
                    seen.append(resolved)
                    result.append(resolved_str)
            return result

        self._data = ProjectData(
            name=self._path.stem,
            database_files=_load_paths(raw.get("database_files", [])),
            trace_files=_load_paths(raw.get("trace_files", [])),
            watch_signals=raw.get("watch_signals", []),
            tx_messages=raw.get("tx_messages", []),
            connections=raw.get("connections", []),
            watch_dids=raw.get("watch_dids", []),
            rx_filters=raw.get("rx_filters", []),
            workspace_state=raw.get("workspace_state", ""),
            settings=raw.get("settings", {}),
            plot_files=_load_paths(raw.get("plot_files", [])),
            database_editor_file=raw.get("database_editor_file", ""),
            script_plugin_file=(self._resolve_stored_path(raw.get("script_plugin_file") or raw.get("rxtx_plugin_file") or raw.get("e2e_plugin_file", "")) if (raw.get("script_plugin_file") or raw.get("rxtx_plugin_file") or raw.get("e2e_plugin_file")) else ""),
            seedkey_file=(self._resolve_stored_path(raw["seedkey_file"])
                          if raw.get("seedkey_file") else ""),
        )
        self._modified = False

    def save_database_editor(self, data: list[dict]):
        """Save database editor content to a sibling ``.db.json`` file.

        Writes *data* as pretty-printed JSON to ``<project_stem>.db.json``
        next to the project file and records the basename in
        :attr:`ProjectData.database_editor_file`.  No-op if the project has
        not been saved yet.

        Args:
            data: List of row dicts as produced by the database editor model.
        """
        if self._path is None:
            return
        db_file = self._path.with_suffix(".db.json")
        self._data.database_editor_file = db_file.name
        with open(db_file, "w") as f:
            json.dump(data, f, indent=2)

    def load_database_editor(self) -> list[dict]:
        """Load database editor content from the sibling ``.db.json`` file.

        Returns:
            List of row dicts, or an empty list if the project has not been
            saved, :attr:`ProjectData.database_editor_file` is empty, or the
            file does not exist on disk.
        """
        if not self._data.database_editor_file or self._path is None:
            return []
        db_path = self._path.parent / self._data.database_editor_file
        if not db_path.exists():
            return []
        with open(db_path) as f:
            return json.load(f)

    def new(self, name: str = "Untitled"):
        """Reset the project to an empty, unsaved state.

        Replaces :attr:`data` with a fresh :class:`ProjectData`, clears
        :attr:`path`, and clears :attr:`is_modified`.

        Args:
            name: Initial project name.  Defaults to ``"Untitled"``.
        """
        self._data = ProjectData(name=name)
        self._path = None
        self._modified = False
