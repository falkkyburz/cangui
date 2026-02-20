# cangui

A CAN Network GUI Application built with PySide6. Inspired by PCAN Explorer's workflow: table-based RX/TX views, a 3-pane tabbed layout, hex data display, and multi-channel connection management.

## Features

- **RX/TX**: Live message reception in a tree-table with per-row decoded signal values; manual and cyclic message transmission with inline signal editing
- **Trace**: Record and play back CAN traffic in PEAK TRC or BLF format; background disk writer keeps the UI responsive
- **Plot**: Time-series signal visualization using pyqtgraph with LTTB downsampling; configurable rolling time window
- **Watch**: Real-time decoded signal monitor — pin any signal from any loaded database
- **Diagnostics (UDS)**: ISO 14229 session management, DID read/write, ECU reset, security access with seed-key plugin support, DTC readout
- **Database**: Load DBC, KCD, or ODX files for signal decoding; built-in database editor for lightweight signal definitions
- **Project persistence**: All session state (connections, watch list, TX messages, workspace layout) saved to a portable JSON project file

## Requirements

- Python 3.13+
- [uv](https://github.com/astral-sh/uv) (package manager)
- A supported CAN interface (or `vcan` for testing)

## Installation

```bash
git clone <repo-url>
cd cangui
uv sync
```

## Running

```bash
uv run python main.py
```

## Testing with a virtual CAN bus

```bash
sudo modprobe vcan
sudo ip link add vcan0 type vcan
sudo ip link set up vcan0

# Send a test frame
cansend vcan0 123#DEADBEEF
```

Add a **socketcan** connection to `vcan0` in the Connections tab to see traffic.

## Documentation

Build the Sphinx docs locally:

```bash
uv sync --group docs
uv run sphinx-build -b html docs docs/_build/html
open docs/_build/html/index.html
```

The docs cover:

- **Architecture overview** — layered module structure and dependency rules
- **Threading model** — batching strategy, timer intervals, snapshot and queue patterns
- **Data flow** — end-to-end path from `CanBus.recv()` to the UI
- **Persistence** — project file format, portable path strategy, workspace state

## Architecture

```
┌─────────────────────────────────────────────────┐
│  UI Layer       (ui_*.py, widgets, dialogs)     │
├─────────────────────────────────────────────────┤
│  Model Layer    (model_*.py — Qt item models)   │
├─────────────────────────────────────────────────┤
│  Service Layer  (service_*.py — business logic) │
├─────────────────────────────────────────────────┤
│  Worker Layer   (worker_*.py — QThread workers) │
├─────────────────────────────────────────────────┤
│  Core Layer     (can_bus, can_message, options) │
└─────────────────────────────────────────────────┘
```

Workers communicate with the UI exclusively through Qt signals — no shared mutable state. Models buffer incoming messages in a `_pending` list and drain them on a QTimer to decouple 1000+ msg/s CAN traffic from 60 fps UI rendering.

## Dependencies

| Package | Purpose |
|---------|---------|
| PySide6 | Qt bindings |
| pyqtgraph | Real-time signal plots |
| python-can | CAN bus abstraction (socketcan, PEAK, Vector, …) |
| cantools | DBC/KCD parsing and signal decoding |
| odxtools | ODX/PDX diagnostic database parsing |
| udsoncan | ISO 14229 UDS client |
| can-isotp | ISO 15765-2 transport layer |
| numpy | Signal buffer arithmetic and LTTB downsampling |
| qtawesome | Icon font (Font Awesome) |
