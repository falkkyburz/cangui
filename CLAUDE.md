# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

cangui is a CAN Network GUI Application built with PySide6.
It follows PCAN Explorer's UI patterns: table-based RX/TX views, 3-pane tabbed layout, hex view, connections management.

## Development

- **Python version**: 3.13+ (see `.python-version`)
- **Package manager**: uv (always use `uv run` to execute, `uv sync` to install)
- **Run**: `uv run python main.py`
- **Dependencies**: See `pyproject.toml` — PySide6, pyqtgraph, python-can, cantools, odxtools, udsoncan, can-isotp, numpy

## Architecture

- `cangui/core/` — CAN bus wrapper, message data structures, options
- `cangui/models/` — Qt item models (QAbstractItemModel/QAbstractTableModel)
- `cangui/services/` — Business logic (CAN service, message dispatcher)
- `cangui/workers/` — QThread workers (CAN receiver, transmitter)
- `cangui/ui/` — UI layer (main window, dock windows, widgets, dialogs)

## Testing with vcan

```bash
sudo modprobe vcan && sudo ip link add vcan0 type vcan && sudo ip link set up vcan0
cansend vcan0 123#DEADBEEF
```

## Notes

- RX/TX uses QTreeView + QAbstractItemModel (tree-table pattern), NOT ParameterTree
- ParameterTree is reserved for the Properties window (context-sensitive editing)
- PCAN Explorer reference screenshots are in `pcan/`
