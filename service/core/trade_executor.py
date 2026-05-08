"""Emergency trade execution — sells on "sell" signals immediately."""

import logging
from typing import Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

logger = logging.getLogger(__name__)


def execute_emergency_sell(
    api_key: str,
    api_secret: str,
    is_paper: bool,
    ticker: str,
    sell_fraction: float,
    reason: str,
) -> Optional[dict]:
    """Immediately sell a fraction of a position.

    Returns order details dict or None if no position exists.
    """
    client = TradingClient(api_key, api_secret, paper=is_paper)

    try:
        position = client.get_open_position(ticker)
    except Exception:
        logger.info(f"No open position for {ticker}, skipping emergency sell")
        return None

    qty = float(position.qty)
    if qty <= 0:
        return None

    sell_qty = max(1, int(qty * sell_fraction))

    logger.warning(
        f"EMERGENCY SELL: {ticker} — selling {sell_qty}/{int(qty)} shares. Reason: {reason}"
    )

    try:
        order = client.submit_order(
            MarketOrderRequest(
                symbol=ticker,
                qty=sell_qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
            )
        )
        result = {
            "ticker": ticker,
            "qty_sold": sell_qty,
            "qty_remaining": int(qty) - sell_qty,
            "order_id": str(order.id),
            "status": order.status.value,
            "reason": reason,
        }
        logger.info(f"Emergency sell order submitted: {result}")
        return result

    except Exception as e:
        logger.error(f"Failed to submit emergency sell for {ticker}: {e}")
        return {
            "ticker": ticker,
            "qty_sold": 0,
            "error": str(e),
            "reason": reason,
        }
