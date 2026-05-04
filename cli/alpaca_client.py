import os
from typing import Optional

import typer
from cli.portfolio import Portfolio


def create_client(key: str, secret: str, paper: bool = True):
    from alpaca.trading.client import TradingClient

    return TradingClient(key, secret, paper=paper)


def fetch_portfolio(client) -> tuple[Portfolio, list[dict]]:
    account = client.get_account()
    positions = client.get_all_positions()

    holdings: dict[str, float] = {}
    for pos in positions:
        holdings[pos.symbol] = float(pos.qty)

    cash = float(account.cash)

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
    return portfolio, pending


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


def submit_orders(client, orders: list[dict]) -> list[dict]:
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce

    results = []
    for order in orders:
        try:
            req = MarketOrderRequest(
                symbol=order["symbol"],
                qty=order["qty"],
                side=OrderSide.BUY if order["side"] == "buy" else OrderSide.SELL,
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
