import json
from dataclasses import dataclass, field, asdict
from pathlib import Path


def _norm(path: str) -> Path:
    """Return a resolved, absolute Path for comparison (handles separator differences)."""
    return Path(path).resolve()


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

    def add_database_file(self, path: str):
        norm = _norm(path)
        if any(_norm(p) == norm for p in self._data.database_files):
            return
        self._data.database_files.append(str(norm))
        self._modified = True

    def remove_database_file(self, path: str):
        norm = _norm(path)
        for i, p in enumerate(self._data.database_files):
            if _norm(p) == norm:
                self._data.database_files.pop(i)
                self._modified = True
                return

    def add_trace_file(self, path: str):
        norm = _norm(path)
        if any(_norm(p) == norm for p in self._data.trace_files):
            return
        self._data.trace_files.append(str(norm))
        self._modified = True

    def remove_trace_file(self, path: str):
        norm = _norm(path)
        for i, p in enumerate(self._data.trace_files):
            if _norm(p) == norm:
                self._data.trace_files.pop(i)
                self._modified = True
                return

    def add_plot_file(self, path: str):
        norm = _norm(path)
        if any(_norm(p) == norm for p in self._data.plot_files):
            return
        self._data.plot_files.append(str(norm))
        self._modified = True

    def remove_plot_file(self, path: str):
        norm = _norm(path)
        for i, p in enumerate(self._data.plot_files):
            if _norm(p) == norm:
                self._data.plot_files.pop(i)
                self._modified = True
                return

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

    def save(self, path: str | Path | None = None):
        if path is not None:
            self._path = Path(path)
        if self._path is None:
            raise ValueError("No path specified")
        self._data.name = self._path.stem
        with open(self._path, "w") as f:
            json.dump(asdict(self._data), f, indent=2)
        self._modified = False

    def load(self, path: str | Path):
        self._path = Path(path)
        with open(self._path) as f:
            raw = json.load(f)
        # Normalize stored paths to absolute form (resolves separator differences)
        def _norm_list(paths: list) -> list[str]:
            result = []
            seen: list[Path] = []
            for p in paths:
                try:
                    resolved = _norm(p)
                except Exception:
                    result.append(p)
                    continue
                # Deduplicate by resolved path
                if resolved not in seen:
                    seen.append(resolved)
                    result.append(str(resolved))
            return result

        self._data = ProjectData(
            name=self._path.stem,
            database_files=_norm_list(raw.get("database_files", [])),
            trace_files=_norm_list(raw.get("trace_files", [])),
            watch_signals=raw.get("watch_signals", []),
            tx_messages=raw.get("tx_messages", []),
            connections=raw.get("connections", []),
            watch_dids=raw.get("watch_dids", []),
            uds_config=raw.get("uds_config", {}),
            rx_filters=raw.get("rx_filters", []),
            workspace_state=raw.get("workspace_state", ""),
            settings=raw.get("settings", {}),
            plot_files=_norm_list(raw.get("plot_files", [])),
            database_editor_file=raw.get("database_editor_file", ""),
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
