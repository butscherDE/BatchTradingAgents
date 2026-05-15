"""Periodic reconciler that updates trade_actions with fill data from Alpaca."""

import asyncio
import datetime
import logging
from typing import TYPE_CHECKING

from sqlalchemy import or_, select, update

from service import clock

if TYPE_CHECKING:
    from service.config import ServiceConfig

logger = logging.getLogger(__name__)


_TERMINAL_STATUSES = {
    "filled", "canceled", "cancelled", "expired", "rejected",
    "done_for_day", "replaced", "stopped", "suspended",
}

_RESYNC_COOLDOWN_SECONDS = 30


async def reconcile_loop(get_db_session, config: "ServiceConfig", interval_seconds: int = 60):
    """Poll Alpaca every interval_seconds for fill status of non-terminal trade_actions."""
    while True:
        try:
            await _reconcile_once(get_db_session, config)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"trade reconciler error: {e}")
        await asyncio.sleep(interval_seconds)


async def _reconcile_once(get_db_session, config: "ServiceConfig"):
    from service.db.models import TradeAction

    cutoff = clock.now() - datetime.timedelta(seconds=_RESYNC_COOLDOWN_SECONDS)

    async with get_db_session() as session:
        result = await session.execute(
            select(TradeAction).where(
                TradeAction.order_id.is_not(None),
                TradeAction.status.notin_(list(_TERMINAL_STATUSES)),
                or_(TradeAction.last_synced_at.is_(None), TradeAction.last_synced_at < cutoff),
            )
        )
        rows = list(result.scalars().all())

    if not rows:
        return

    by_account: dict[str, list] = {}
    for r in rows:
        by_account.setdefault(r.account_id, []).append(r)

    for account_id, account_rows in by_account.items():
        acct = config.accounts.get(account_id)
        if not acct or not acct.api_key or not acct.api_secret:
            continue

        try:
            updates = await asyncio.to_thread(
                _fetch_account_orders, acct, [r.order_id for r in account_rows]
            )
        except Exception as e:
            logger.warning(f"reconciler: failed fetching orders for {account_id}: {e}")
            continue

        now = clock.now()
        async with get_db_session() as session:
            for row in account_rows:
                upd = updates.get(row.order_id)
                if not upd:
                    await session.execute(
                        update(TradeAction)
                        .where(TradeAction.id == row.id)
                        .values(last_synced_at=now)
                    )
                    continue
                await session.execute(
                    update(TradeAction)
                    .where(TradeAction.id == row.id)
                    .values(**upd, last_synced_at=now)
                )
            await session.commit()


def _fetch_account_orders(acct, order_ids: list[str]) -> dict[str, dict]:
    from alpaca.trading.client import TradingClient

    client = TradingClient(acct.api_key, acct.api_secret, paper=acct.is_paper)
    out: dict[str, dict] = {}
    for oid in order_ids:
        if not oid:
            continue
        try:
            order = client.get_order_by_id(oid)
        except Exception:
            continue
        status = order.status.value if hasattr(order.status, "value") else str(order.status)
        out[oid] = {
            "status": status,
            "filled_qty": float(order.filled_qty) if order.filled_qty else None,
            "filled_avg_price": float(order.filled_avg_price) if order.filled_avg_price else None,
            "filled_at": order.filled_at,
        }
    return out
