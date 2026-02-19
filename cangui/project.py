import json
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class ProjectData:
    name: str = "Untitled"
    database_files: list[str] = field(default_factory=list)
    trace_files: list[str] = field(default_factory=list)
    watch_signals: list[dict] = field(default_factory=list)
    tx_messages: list[dict] = field(default_factory=list)
    connections: list[dict] = field(default_factory=list)
    watch_dids: list[dict] = field(default_factory=list)
    uds_config: dict = field(default_factory=dict)
    rx_filters: list[dict] = field(default_factory=list)
    workspace_state: str = ""  # JSON splitter/tab state
    settings: dict = field(default_factory=dict)
    plot_files: list[str] = field(default_factory=list)
    database_editor_file: str = ""
    script_plugin_file: str = ""
    seedkey_file: str = ""


class Project:
    def __init__(self):
        self._data = ProjectData()
        self._path: Path | None = None
        self._modified = False

    @property
    def data(self) -> ProjectData:
        return self._data

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def name(self) -> str:
        return self._data.name

    @name.setter
    def name(self, value: str):
        self._data.name = value
        self._modified = True

    @property
    def is_modified(self) -> bool:
        return self._modified

    def mark_modified(self):
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
        resolved = str(Path(path).resolve())
        norm = self._norm(resolved)
        if not any(self._norm(p) == norm for p in lst):
            lst.append(resolved)
            self._modified = True

    def _remove_from(self, lst: list[str], path: str):
        norm = self._norm(path)
        for i, p in enumerate(lst):
            if self._norm(p) == norm:
                lst.pop(i)
                self._modified = True
                return

    def set_script_plugin(self, path: str):
        self._data.script_plugin_file = str(Path(path).resolve()) if path else ""
        self._modified = True

    def set_seedkey_file(self, path: str):
        self._data.seedkey_file = str(Path(path).resolve()) if path else ""
        self._modified = True

    def add_database_file(self, path: str):
        self._add_to(self._data.database_files, path)

    def remove_database_file(self, path: str):
        self._remove_from(self._data.database_files, path)

    def add_trace_file(self, path: str):
        self._add_to(self._data.trace_files, path)

    def remove_trace_file(self, path: str):
        self._remove_from(self._data.trace_files, path)

    def add_plot_file(self, path: str):
        self._add_to(self._data.plot_files, path)

    def remove_plot_file(self, path: str):
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
        if path is not None:
            self._path = Path(path)
        if self._path is None:
            raise ValueError("No path specified")
        self._data.name = self._path.stem
        data = asdict(self._data)
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
        self._path = Path(path).resolve()
        with open(self._path) as f:
            raw = json.load(f)

        def _load_paths(paths: list) -> list[str]:
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
            uds_config=raw.get("uds_config", {}),
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
        """Save database editor content to a separate JSON file next to the project."""
        if self._path is None:
            return
        db_file = self._path.with_suffix(".db.json")
        self._data.database_editor_file = db_file.name
        with open(db_file, "w") as f:
            json.dump(data, f, indent=2)

    def load_database_editor(self) -> list[dict]:
        """Load database editor content from the separate file."""
        if not self._data.database_editor_file or self._path is None:
            return []
        db_path = self._path.parent / self._data.database_editor_file
        if not db_path.exists():
            return []
        with open(db_path) as f:
            return json.load(f)

    def new(self, name: str = "Untitled"):
        self._data = ProjectData(name=name)
        self._path = None
        self._modified = False
