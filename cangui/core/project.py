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
        if path not in self._data.database_files:
            self._data.database_files.append(path)
            self._modified = True

    def remove_database_file(self, path: str):
        if path in self._data.database_files:
            self._data.database_files.remove(path)
            self._modified = True

    def add_trace_file(self, path: str):
        if path not in self._data.trace_files:
            self._data.trace_files.append(path)
            self._modified = True

    @property
    def trace_folder(self) -> Path | None:
        """Return the trace folder next to the project file, or None if unsaved."""
        if self._path is None:
            return None
        return self._path.parent / "trace"

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
        self._data = ProjectData(
            name=self._path.stem,
            database_files=raw.get("database_files", []),
            trace_files=raw.get("trace_files", []),
            watch_signals=raw.get("watch_signals", []),
            tx_messages=raw.get("tx_messages", []),
            connections=raw.get("connections", []),
            watch_dids=raw.get("watch_dids", []),
            uds_config=raw.get("uds_config", {}),
            rx_filters=raw.get("rx_filters", []),
            workspace_state=raw.get("workspace_state", ""),
        )
        self._modified = False

    def new(self, name: str = "Untitled"):
        self._data = ProjectData(name=name)
        self._path = None
        self._modified = False
