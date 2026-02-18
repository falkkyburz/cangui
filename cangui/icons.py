"""Central icon registry using qtawesome (Font Awesome 5)."""
import qtawesome as qta
from PySide6.QtGui import QIcon

_C  = "#3C3C3E"   # neutral / default
_RD = "#C62828"   # red — destructive or record
_GN = "#2E7D32"   # green — start / connect

_MAP: dict[str, tuple[str, str]] = {
    # CRUD
    "add":          ("fa5s.plus",           _C),
    "remove":       ("fa5s.minus",          _C),
    "trash":        ("fa5s.trash-alt",      _C),
    "copy":         ("fa5s.copy",           _C),
    # File
    "new":          ("fa5s.file",           _C),
    "open":         ("fa5s.folder-open",    _C),
    "save":         ("fa5s.save",           _C),
    "save-as":      ("fa5s.file-export",    _C),
    "import":       ("fa5s.file-import",    _C),
    "export":       ("fa5s.file-export",    _C),
    # Transport
    "send":         ("fa5s.paper-plane",    _C),
    "up":           ("fa5s.arrow-up",       _C),
    "down":         ("fa5s.arrow-down",     _C),
    "refresh":      ("fa5s.sync-alt",       _C),
    # Playback
    "play":         ("fa5s.play",           _GN),
    "pause":        ("fa5s.pause",          _C),
    "stop":         ("fa5s.stop",           _C),
    "record":       ("fa5s.circle",         _RD),
    # Views
    "watch":        ("fa5s.eye",            _C),
    "plot":         ("fa5s.chart-line",     _C),
    "search":       ("fa5s.search",         _C),
    "clear":        ("fa5s.eraser",         _C),
    "broom":        ("fa5s.broom",          _C),
    # Network
    "connect":      ("fa5s.plug",           _GN),
    "disconnect":   ("fa5s.unlink",         _RD),
    "heartbeat":    ("fa5s.heartbeat",      _C),
    # Database / CAN
    "database":     ("fa5s.database",       _C),
    "message":      ("fa5s.comment-alt",    _C),
    "signal":       ("fa5s.wave-square",    _C),
    # Filters
    "pass":         ("fa5s.check-circle",   _GN),
    "drop":         ("fa5s.times-circle",   _RD),
}


def icon(name: str) -> QIcon:
    """Return a QIcon for the given logical name, or an empty QIcon if unknown."""
    entry = _MAP.get(name)
    if entry is None:
        return QIcon()
    icon_name, color = entry
    try:
        return qta.icon(icon_name, color=color)
    except Exception:
        return QIcon()
