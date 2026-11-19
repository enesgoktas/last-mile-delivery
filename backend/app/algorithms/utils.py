"""Shared utilities for the algorithm layer."""
from __future__ import annotations


def fmt_time(seconds: int) -> str:
    """Format seconds-from-midnight as HH:MM:SS."""
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def fmt_time_short(seconds: int) -> str:
    """Format seconds-from-midnight as HH:MM."""
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h:02d}:{m:02d}"
