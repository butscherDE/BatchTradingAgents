"""Broker abstraction layer — unified interface for Alpaca and E*TRADE."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class Position:
    symbol: str
    qty: float
    avg_entry_price: Optional[float] = None
    cost_basis: Optional[float] = None
    current_price: Optional[float] = None
    unrealized_pl: Optional[float] = None
    unrealized_plpc: Optional[float] = None


@dataclass
class OrderResult:
    symbol: str
    side: str
    qty: Optional[float] = None
    notional: Optional[float] = None
    order_id: Optional[str] = None
    status: str = "error"
    error: Optional[str] = None
    extended_hours: bool = False


@dataclass
class Clock:
    is_open: bool


class BrokerClient(Protocol):
    def get_clock(self) -> Clock: ...
    def get_account_cash(self) -> float: ...
    def get_positions(self) -> list[Position]: ...
    def get_position(self, symbol: str) -> Optional[Position]: ...
    def get_quotes(self, symbols: list[str]) -> dict[str, float]: ...
    def submit_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        *,
        limit_price: Optional[float] = None,
        extended_hours: bool = False,
        notional: Optional[float] = None,
    ) -> OrderResult: ...


def create_broker_client(
    brokerage: str,
    api_key: str,
    api_secret: str,
    is_paper: bool,
    *,
    oauth_token: str = "",
    oauth_token_secret: str = "",
    etrade_account_id_key: str = "",
) -> BrokerClient:
    if brokerage == "etrade":
        from cli.etrade_client import ETradeClient

        return ETradeClient(
            consumer_key=api_key,
            consumer_secret=api_secret,
            oauth_token=oauth_token,
            oauth_token_secret=oauth_token_secret,
            account_id_key=etrade_account_id_key,
            is_paper=is_paper,
        )
    else:
        from cli.alpaca_adapter import AlpacaAdapter

        return AlpacaAdapter(api_key=api_key, api_secret=api_secret, is_paper=is_paper)


def create_broker_from_config(acct) -> BrokerClient:
    return create_broker_client(
        brokerage=acct.brokerage,
        api_key=acct.api_key,
        api_secret=acct.api_secret,
        is_paper=acct.is_paper,
        oauth_token=acct.oauth_token,
        oauth_token_secret=acct.oauth_token_secret,
        etrade_account_id_key=acct.etrade_account_id_key,
    )
