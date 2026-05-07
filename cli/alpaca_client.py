import datetime
import os
from typing import Optional

import typer
from cli.portfolio import Portfolio
from cli.tax import holding_period_type


def create_client(key: str, secret: str, paper: bool = True):
    from alpaca.trading.client import TradingClient

    return TradingClient(key, secret, paper=paper)


def _fetch_earliest_fills(client, symbols: list[str]) -> dict[str, str]:
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus, OrderSide

    if not symbols:
        return {}

    try:
        filled_orders = client.get_orders(
            filter=GetOrdersRequest(
                status=QueryOrderStatus.CLOSED,
                side=OrderSide.BUY,
                limit=500,
            )
        )
    except Exception:
        return {}

    earliest: dict[str, str] = {}
    for order in filled_orders:
        if order.symbol not in symbols:
            continue
        if order.filled_at is None:
            continue
        fill_date = order.filled_at.strftime("%Y-%m-%d")
        if order.symbol not in earliest or fill_date < earliest[order.symbol]:
            earliest[order.symbol] = fill_date

    return earliest


def fetch_portfolio(client) -> tuple[Portfolio, list[dict], dict[str, float], dict[str, dict]]:
    """Returns (portfolio, pending_orders, prices_by_symbol, position_details)."""
    account = client.get_account()
    positions = client.get_all_positions()

    holdings: dict[str, float] = {}
    prices: dict[str, float] = {}
    position_details: dict[str, dict] = {}

    for pos in positions:
        sym = pos.symbol
        qty = float(pos.qty)
        holdings[sym] = qty
        if pos.current_price is not None:
            prices[sym] = float(pos.current_price)

        details: dict = {"qty": qty}
        if pos.avg_entry_price is not None:
            details["avg_entry_price"] = float(pos.avg_entry_price)
        if pos.cost_basis is not None:
            details["cost_basis"] = float(pos.cost_basis)
        if pos.unrealized_pl is not None:
            details["unrealized_pl"] = float(pos.unrealized_pl)
        if pos.unrealized_plpc is not None:
            details["unrealized_plpc"] = float(pos.unrealized_plpc)
        position_details[sym] = details

    cash = float(account.cash)

    earliest_fills = _fetch_earliest_fills(client, list(holdings.keys()))
    for sym, fill_date in earliest_fills.items():
        if sym in position_details:
            position_details[sym]["earliest_fill"] = fill_date
            position_details[sym]["holding_period"] = holding_period_type(fill_date)

    for sym in position_details:
        if "holding_period" not in position_details[sym]:
            position_details[sym]["holding_period"] = "unknown"

    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus

    open_orders = client.get_orders(
        filter=GetOrdersRequest(status=QueryOrderStatus.OPEN)
    )
    pending = []
    for order in open_orders:
        entry = {
            "symbol": order.symbol,
            "side": order.side.value,
            "qty": float(order.qty) if order.qty else None,
            "notional": float(order.notional) if order.notional else None,
            "type": order.type.value,
            "status": order.status.value,
        }
        if order.filled_qty and float(order.filled_qty) > 0:
            entry["filled_qty"] = float(order.filled_qty)
        pending.append(entry)

    portfolio = Portfolio(holdings=holdings, cash=cash)
    return portfolio, pending, prices, position_details


def fetch_quotes(client, tickers: list[str]) -> dict[str, float]:
    if not tickers:
        return {}

    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockLatestQuoteRequest

    data_client = StockHistoricalDataClient(
        client._api_key, client._secret_key
    )
    request = StockLatestQuoteRequest(symbol_or_symbols=tickers)
    quotes = data_client.get_stock_latest_quote(request)

    result = {}
    for sym, quote in quotes.items():
        if quote.ask_price and quote.ask_price > 0:
            result[sym] = float(quote.ask_price)
        elif quote.bid_price and quote.bid_price > 0:
            result[sym] = float(quote.bid_price)
    return result


def submit_orders(client, orders: list[dict], quotes: dict[str, float] = None) -> list[dict]:
    from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce

    # Determine if market is open or in extended hours
    clock = client.get_clock()
    market_open = clock.is_open

    # Extended hours: market is closed but we can still trade with limit orders
    # Alpaca extended hours: pre-market 4:00-9:30 ET, after-hours 16:00-20:00 ET
    # If market is closed, use limit orders with extended_hours=True
    use_extended = not market_open

    results = []
    for order in orders:
        try:
            side = OrderSide.BUY if order["side"] == "buy" else OrderSide.SELL

            if use_extended and quotes:
                price = quotes.get(order["symbol"])
                if price is None:
                    results.append({
                        "symbol": order["symbol"],
                        "side": order["side"],
                        "qty": order["qty"],
                        "order_id": None,
                        "status": "error",
                        "error": "No quote available for extended-hours limit order",
                    })
                    continue

                # Use a slight buffer: buy slightly above ask, sell slightly below bid
                if order["side"] == "buy":
                    limit_price = round(price * 1.005, 2)
                else:
                    limit_price = round(price * 0.995, 2)

                req = LimitOrderRequest(
                    symbol=order["symbol"],
                    qty=order["qty"],
                    side=side,
                    time_in_force=TimeInForce.DAY,
                    limit_price=limit_price,
                    extended_hours=True,
                )
            else:
                req = MarketOrderRequest(
                    symbol=order["symbol"],
                    qty=order["qty"],
                    side=side,
                    time_in_force=TimeInForce.DAY,
                )

            response = client.submit_order(req)
            results.append({
                "symbol": order["symbol"],
                "side": order["side"],
                "qty": order["qty"],
                "order_id": str(response.id),
                "status": response.status.value,
                "error": None,
                "extended_hours": use_extended,
            })
        except Exception as e:
            results.append({
                "symbol": order["symbol"],
                "side": order["side"],
                "qty": order["qty"],
                "order_id": None,
                "status": "error",
                "error": str(e),
            })
    return results


def resolve_credentials(
    key: Optional[str], secret: Optional[str],
) -> tuple[str, str]:
    api_key = key or os.environ.get("ALPACA_API_KEY", "")
    api_secret = secret or os.environ.get("ALPACA_API_SECRET", "")
    if not api_key or not api_secret:
        raise typer.BadParameter(
            "Alpaca credentials required. Provide --key/--secret or set "
            "ALPACA_API_KEY/ALPACA_API_SECRET environment variables."
        )
    return api_key, api_secret
