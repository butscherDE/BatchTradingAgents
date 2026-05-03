import csv
from pathlib import Path
from typing import Callable, Optional

import typer
from pydantic import BaseModel, Field


class Portfolio(BaseModel):
    holdings: dict[str, float] = Field(default_factory=dict)
    cash: float = 0.0

    def to_dict(self) -> dict:
        return {"holdings": self.holdings, "cash": self.cash}

    def ticker_symbols(self) -> list[str]:
        return sorted(self.holdings.keys())


def _clean_number(val: str) -> float:
    return float(val.replace(",", "").replace("$", "").strip())


def parse_etrade_csv(path: Path) -> Portfolio:
    lines = path.read_text(encoding="utf-8").splitlines()

    header_idx = None
    for i, line in enumerate(lines):
        if line.startswith("Symbol,Last Price"):
            header_idx = i
            break
    if header_idx is None:
        raise typer.BadParameter(
            f"Could not find positions table in E*Trade CSV: {path}"
        )

    reader = csv.reader(lines[header_idx + 1:])
    holdings: dict[str, float] = {}
    cash = 0.0

    for row in reader:
        if not row or not row[0].strip():
            continue
        symbol = row[0].strip().upper()
        if symbol == "TOTAL":
            continue
        if symbol == "CASH":
            try:
                cash = _clean_number(row[9])
            except (IndexError, ValueError):
                pass
            continue
        try:
            qty = _clean_number(row[4])
        except (IndexError, ValueError):
            continue
        if qty > 0:
            holdings[symbol] = qty

    return Portfolio(holdings=holdings, cash=cash)


def parse_generic_csv(path: Path) -> Portfolio:
    text = path.read_text(encoding="utf-8")
    reader = csv.DictReader(text.splitlines())

    if not reader.fieldnames or len(reader.fieldnames) < 2:
        raise typer.BadParameter(
            f"Generic CSV must have at least two columns (ticker, quantity): {path}"
        )

    ticker_col = reader.fieldnames[0]
    qty_col = reader.fieldnames[1]

    holdings: dict[str, float] = {}
    cash = 0.0

    for row in reader:
        ticker = row[ticker_col].strip().upper()
        if not ticker:
            continue
        try:
            qty = _clean_number(row[qty_col])
        except (ValueError, TypeError):
            continue
        if ticker == "CASH":
            cash = qty
        elif qty > 0:
            holdings[ticker] = qty

    return Portfolio(holdings=holdings, cash=cash)


def parse_inline(positions: list[str], cash: float) -> Portfolio:
    holdings: dict[str, float] = {}
    for pos in positions:
        if ":" not in pos:
            raise typer.BadParameter(
                f"Invalid position format '{pos}'. Expected TICKER:QUANTITY (e.g. AAPL:100)"
            )
        ticker, qty_str = pos.split(":", 1)
        ticker = ticker.strip().upper()
        if not ticker:
            raise typer.BadParameter(f"Empty ticker in position '{pos}'")
        try:
            qty = float(qty_str.strip())
        except ValueError:
            raise typer.BadParameter(f"Invalid quantity in position '{pos}'")
        holdings[ticker] = qty
    return Portfolio(holdings=holdings, cash=cash)


def detect_format(path: Path) -> str:
    lines = []
    with open(path, encoding="utf-8") as f:
        for _ in range(20):
            line = f.readline()
            if not line:
                break
            lines.append(line)

    text = "".join(lines)
    if "Account Summary" in text or "Last Price $" in text:
        return "etrade"

    for line in lines:
        stripped = line.strip().lower()
        if stripped:
            if stripped.startswith("ticker,"):
                return "generic"
            break

    raise typer.BadParameter(
        f"Could not detect portfolio format for '{path}'. "
        "Use --portfolio-format (etrade, generic) to specify."
    )


_PARSERS: dict[str, Callable[[Path], Portfolio]] = {
    "etrade": parse_etrade_csv,
    "generic": parse_generic_csv,
}


def load_portfolio(
    path: Optional[Path] = None,
    positions: Optional[list[str]] = None,
    cash: float = 0.0,
    format_override: Optional[str] = None,
) -> Optional[Portfolio]:
    portfolio = None

    if path is not None:
        if not path.is_file():
            raise typer.BadParameter(f"Portfolio file not found: {path}")
        fmt = format_override or detect_format(path)
        parser = _PARSERS.get(fmt)
        if parser is None:
            raise typer.BadParameter(
                f"Unknown portfolio format '{fmt}'. Supported: {', '.join(_PARSERS)}"
            )
        portfolio = parser(path)

    if positions:
        inline = parse_inline(positions, 0.0)
        if portfolio is None:
            portfolio = inline
        else:
            portfolio.holdings.update(inline.holdings)

    if portfolio is None:
        if cash > 0:
            return Portfolio(cash=cash)
        return None

    if cash > 0:
        portfolio.cash = cash

    return portfolio
