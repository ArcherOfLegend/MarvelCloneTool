"""Clone UMvC3 characters into Clone Engine slots."""

__version__ = "0.1.0"

from .arc import Arc, ArcEntry, read_arc, unpack, verify_roundtrip, write_arc
from .clone import CloneSpec, detect_base_name, find_sources, install, run
from .rename import replace
from .scan import max_safe_length, scan_arc_paths, scan_tree

__all__ = [
    "Arc", "ArcEntry", "read_arc", "write_arc", "unpack", "verify_roundtrip",
    "CloneSpec", "run", "install", "find_sources", "detect_base_name",
    "replace", "scan_tree", "scan_arc_paths", "max_safe_length",
]
