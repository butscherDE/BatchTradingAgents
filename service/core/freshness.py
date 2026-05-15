"""Freshness policy helpers: duration parsing and portfolio-tier classification."""

import re
from typing import Optional


_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhd]?)\s*$", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(value: str) -> Optional[int]:
    """Parse '30m', '2h', '3d', '90s', or bare integer (seconds).

    Empty/whitespace string returns None (meaning: tier disabled).
    Raises ValueError on bad input.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    m = _DURATION_RE.match(s)
    if not m:
        raise ValueError(f"invalid duration: {value!r}")
    n = int(m.group(1))
    if n <= 0:
        raise ValueError(f"duration must be positive: {value!r}")
    unit = m.group(2).lower() or "s"
    return n * _UNIT_SECONDS[unit]


def tier_for_pct(pct: Optional[float]) -> str:
    """Map a holding's portfolio % to a freshness tier.

    None / not held -> "watchlist".
    """
    if pct is None:
        return "watchlist"
    if pct < 25:
        return "owned_lt_25"
    if pct < 50:
        return "owned_lt_50"
    return "owned_gte_50"
