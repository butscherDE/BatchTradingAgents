"""Watchlist loader — reads named ticker lists from a TOML file.

Supports:
- Per-section ticker lists with comments (commented-out tickers are ignored by TOML)
- `extends` key to inherit from another section
- `exclude` key to remove tickers inherited from the parent
- Chained inheritance (yolo → aggressive → conservative)
"""

import sys
from pathlib import Path
from typing import Optional

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib


DEFAULT_PATHS = [
    Path("./watchlists.toml"),
    Path.home() / ".tradingagents" / "watchlists.toml",
]


def _find_watchlist_file(path: Optional[Path] = None) -> Optional[Path]:
    if path is not None:
        return path if path.is_file() else None
    for candidate in DEFAULT_PATHS:
        if candidate.is_file():
            return candidate
    return None


def _resolve_section(
    name: str,
    data: dict,
    resolved: dict[str, list[str]],
    stack: list[str],
) -> list[str]:
    if name in resolved:
        return resolved[name]

    if name in stack:
        cycle = " → ".join(stack + [name])
        raise ValueError(f"Circular extends in watchlist: {cycle}")

    section = data.get(name)
    if section is None:
        raise ValueError(f"Watchlist section '{name}' not found. Available: {', '.join(data.keys())}")

    stack.append(name)

    parent_tickers: list[str] = []
    extends = section.get("extends")
    if extends:
        parent_tickers = list(_resolve_section(extends, data, resolved, stack))

    own_tickers = section.get("tickers", [])
    exclude = set(t.upper() for t in section.get("exclude", []))

    combined = [t.upper() for t in parent_tickers if t.upper() not in exclude]
    for t in own_tickers:
        upper = t.upper()
        if upper not in combined and upper not in exclude:
            combined.append(upper)

    stack.pop()
    resolved[name] = combined
    return combined


def load_watchlist(name: str, path: Optional[Path] = None) -> list[str]:
    filepath = _find_watchlist_file(path)
    if filepath is None:
        searched = path or ", ".join(str(p) for p in DEFAULT_PATHS)
        raise FileNotFoundError(f"No watchlist file found. Searched: {searched}")

    with open(filepath, "rb") as f:
        data = tomllib.load(f)

    resolved: dict[str, list[str]] = {}
    return _resolve_section(name, data, resolved, [])


def list_watchlists(path: Optional[Path] = None) -> list[str]:
    filepath = _find_watchlist_file(path)
    if filepath is None:
        return []

    with open(filepath, "rb") as f:
        data = tomllib.load(f)

    return [k for k in data.keys() if isinstance(data[k], dict)]
