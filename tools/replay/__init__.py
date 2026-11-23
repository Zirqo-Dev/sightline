"""Replay entry rules over historical scan and pool snapshots.

Read-only and offline. The default exit is the desk's check. An entry does not
depend on a later bar existing.
"""

from .engine import RuleError, format_report, replay
from .load import ReplayRefused, load_bars

__all__ = ["RuleError", "ReplayRefused", "format_report", "load_bars", "replay"]
