"""Service clock — single source of truth for 'what time is it?'

In production: returns real wall-clock time (datetime.utcnow()).
In simulation: returns the simulated time, advanced by the replay driver.

This module has NO simulation logic — it's just an indirection point.
A simulation layer sets the time; this module serves it.
"""

import datetime
from typing import Optional

_sim_time: Optional[datetime.datetime] = None


def now() -> datetime.datetime:
    """Current service time (UTC). Wall clock in production, virtual in simulation."""
    if _sim_time is not None:
        return _sim_time
    return datetime.datetime.utcnow()


def today() -> str:
    """Current service date as YYYY-MM-DD string (for analysis_date)."""
    return now().strftime("%Y-%m-%d")


def is_sim() -> bool:
    """True when running in simulation mode."""
    return _sim_time is not None


def set_time(dt: datetime.datetime) -> None:
    """Advance the clock. Only called by the simulation replay driver.

    Enforces monotonicity — cannot go backward.
    """
    global _sim_time
    if _sim_time is not None and dt < _sim_time:
        return
    _sim_time = dt


def reset() -> None:
    """Return to real-time mode. Used by tests and shutdown."""
    global _sim_time
    _sim_time = None
