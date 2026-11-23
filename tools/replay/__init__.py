"""Replay fixed entry and exit rules over historical scan and pool snapshots.

Read-only and offline. A position is opened only when its exit rule, including
a finite max hold, is already bound.
"""

from .engine import RuleError, format_report, replay
from .load import ReplayRefused, load_bars

__all__ = ["RuleError", "ReplayRefused", "format_report", "load_bars", "replay"]
