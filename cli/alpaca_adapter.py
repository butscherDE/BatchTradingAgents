"""Alpaca adapter — wraps alpaca-py into the BrokerClient interface."""

from __future__ import annotations

from typing import Optional

from cli.broker import BrokerClient, Clock, OrderResult, Position


class AlpacaAdapter:
    def __init__(self, api_key: str, api_secret: str, is_paper: bool):
        from alpaca.trading.client import TradingClient

        self._client = TradingClient(api_key, api_secret, paper=is_paper)
        self._api_key = api_key
        self._api_secret = api_secret

    def get_clock(self) -> Clock:
        clock = self._client.get_clock()
        return Clock(is_open=clock.is_open)

    def get_account_cash(self) -> float:
        account = self._client.get_account()
        return float(account.cash)

    def get_positions(self) -> list[Position]:
        positions = self._client.get_all_positions()
        result = []
        for pos in positions:
            result.append(Position(
                symbol=pos.symbol,
                qty=float(pos.qty),
                avg_entry_price=float(pos.avg_entry_price) if pos.avg_entry_price else None,
                cost_basis=float(pos.cost_basis) if pos.cost_basis else None,
                current_price=float(pos.current_price) if pos.current_price else None,
                unrealized_pl=float(pos.unrealized_pl) if pos.unrealized_pl else None,
                unrealized_plpc=float(pos.unrealized_plpc) if pos.unrealized_plpc else None,
            ))
        return result

    def get_position(self, symbol: str) -> Optional[Position]:
        try:
            pos = self._client.get_open_position(symbol)
        except Exception:
            return None
        return Position(
            symbol=pos.symbol,
            qty=float(pos.qty),
            avg_entry_price=float(pos.avg_entry_price) if pos.avg_entry_price else None,
            cost_basis=float(pos.cost_basis) if pos.cost_basis else None,
            current_price=float(pos.current_price) if pos.current_price else None,
            unrealized_pl=float(pos.unrealized_pl) if pos.unrealized_pl else None,
            unrealized_plpc=float(pos.unrealized_plpc) if pos.unrealized_plpc else None,
        )

    def get_quotes(self, symbols: list[str]) -> dict[str, float]:
        if not symbols:
            return {}
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestQuoteRequest

        data_client = StockHistoricalDataClient(self._api_key, self._api_secret)
        request = StockLatestQuoteRequest(symbol_or_symbols=symbols)
        quotes = data_client.get_stock_latest_quote(request)

        result = {}
        for sym, quote in quotes.items():
            if quote.ask_price and quote.ask_price > 0:
                result[sym] = float(quote.ask_price)
            elif quote.bid_price and quote.bid_price > 0:
                result[sym] = float(quote.bid_price)
        return result

    def submit_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        *,
        limit_price: Optional[float] = None,
        extended_hours: bool = False,
        notional: Optional[float] = None,
    ) -> OrderResult:
        from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL

        try:
            if limit_price is not None or extended_hours:
                price = limit_price
                if price is None:
                    return OrderResult(
                        symbol=symbol, side=side, qty=qty,
                        error="limit_price required for extended hours",
                    )
                req = LimitOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=order_side,
                    time_in_force=TimeInForce.DAY,
                    limit_price=price,
                    extended_hours=True,
                )
            elif notional is not None:
                req = MarketOrderRequest(
                    symbol=symbol,
                    notional=notional,
                    side=order_side,
                    time_in_force=TimeInForce.DAY,
                )
            else:
                req = MarketOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=order_side,
                    time_in_force=TimeInForce.DAY,
                )

            response = self._client.submit_order(req)
            return OrderResult(
                symbol=symbol,
                side=side,
                qty=qty,
                order_id=str(response.id),
                status=response.status.value,
                extended_hours=extended_hours,
            )
        except Exception as e:
            return OrderResult(
                symbol=symbol, side=side, qty=qty,
                error=str(e),
            )
