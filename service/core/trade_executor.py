"""Emergency trade execution — sells on "sell" signals immediately."""

import logging
from typing import Optional

from cli.broker import create_broker_client

logger = logging.getLogger(__name__)


def execute_emergency_sell(
    api_key: str,
    api_secret: str,
    is_paper: bool,
    ticker: str,
    sell_fraction: float,
    reason: str,
    brokerage: str = "alpaca",
    oauth_token: str = "",
    oauth_token_secret: str = "",
    etrade_account_id_key: str = "",
) -> Optional[dict]:
    """Immediately sell a fraction of a position.

    Returns order details dict or None if no position exists.
    """
    client = create_broker_client(
        brokerage=brokerage,
        api_key=api_key,
        api_secret=api_secret,
        is_paper=is_paper,
        oauth_token=oauth_token,
        oauth_token_secret=oauth_token_secret,
        etrade_account_id_key=etrade_account_id_key,
    )

    position = client.get_position(ticker)
    if position is None or position.qty <= 0:
        logger.info(f"No open position for {ticker}, skipping emergency sell")
        return None

    qty = position.qty
    sell_qty = max(1, int(qty * sell_fraction))

    logger.warning(
        f"EMERGENCY SELL: {ticker} — selling {sell_qty}/{int(qty)} shares. Reason: {reason}"
    )

    result = client.submit_order(symbol=ticker, qty=sell_qty, side="sell")

    if result.error:
        logger.error(f"Failed to submit emergency sell for {ticker}: {result.error}")
        return {
            "ticker": ticker,
            "qty_sold": 0,
            "error": result.error,
            "reason": reason,
        }

    order_result = {
        "ticker": ticker,
        "qty_sold": sell_qty,
        "qty_remaining": int(qty) - sell_qty,
        "order_id": result.order_id,
        "status": result.status,
        "reason": reason,
    }
    logger.info(f"Emergency sell order submitted: {order_result}")
    return order_result
