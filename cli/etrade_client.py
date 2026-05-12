"""E*TRADE broker client — implements BrokerClient interface using pyetrade."""

from __future__ import annotations

import logging
from typing import Optional

from cli.broker import BrokerClient, Clock, OrderResult, Position

logger = logging.getLogger(__name__)

SANDBOX_BASE_URL = "https://apisb.etrade.com"
PRODUCTION_BASE_URL = "https://api.etrade.com"


class ETradeClient:
    def __init__(
        self,
        consumer_key: str,
        consumer_secret: str,
        oauth_token: str,
        oauth_token_secret: str,
        account_id_key: str,
        is_paper: bool = True,
    ):
        self._consumer_key = consumer_key
        self._consumer_secret = consumer_secret
        self._oauth_token = oauth_token
        self._oauth_token_secret = oauth_token_secret
        self._account_id_key = account_id_key
        self._is_paper = is_paper
        self._base_url = SANDBOX_BASE_URL if is_paper else PRODUCTION_BASE_URL
        self._session = None

    def _get_session(self):
        if self._session is not None:
            return self._session
        import pyetrade

        self._session = pyetrade.ETradeAccessManager(
            self._consumer_key,
            self._consumer_secret,
            self._oauth_token,
            self._oauth_token_secret,
            dev=self._is_paper,
        )
        return self._session

    def _accounts_api(self):
        import pyetrade

        return pyetrade.ETradeAccounts(
            self._consumer_key,
            self._consumer_secret,
            self._oauth_token,
            self._oauth_token_secret,
            dev=self._is_paper,
        )

    def _orders_api(self):
        import pyetrade

        return pyetrade.ETradeOrder(
            self._consumer_key,
            self._consumer_secret,
            self._oauth_token,
            self._oauth_token_secret,
            dev=self._is_paper,
        )

    def _market_api(self):
        import pyetrade

        return pyetrade.ETradeMarket(
            self._consumer_key,
            self._consumer_secret,
            self._oauth_token,
            self._oauth_token_secret,
            dev=self._is_paper,
        )

    def get_clock(self) -> Clock:
        import datetime
        import pytz

        eastern = pytz.timezone("US/Eastern")
        now = datetime.datetime.now(eastern)

        if now.weekday() >= 5:
            return Clock(is_open=False)

        market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
        is_open = market_open <= now <= market_close

        return Clock(is_open=is_open)

    def get_account_cash(self) -> float:
        accounts = self._accounts_api()
        balance = accounts.get_account_balance(self._account_id_key, resp_format="json")

        balance_data = balance.get("BalanceResponse", {})
        computed = balance_data.get("Computed", {})
        cash = computed.get("cashAvailableForInvestment", 0.0)
        if not cash:
            cash = computed.get("cashBalance", 0.0)
        return float(cash)

    def get_positions(self) -> list[Position]:
        accounts = self._accounts_api()
        try:
            portfolio_resp = accounts.get_account_portfolio(
                self._account_id_key, resp_format="json"
            )
        except Exception as e:
            if "empty" in str(e).lower() or "no position" in str(e).lower():
                return []
            raise

        positions_data = (
            portfolio_resp.get("PortfolioResponse", {})
            .get("AccountPortfolio", [{}])[0]
            .get("Position", [])
        )

        if isinstance(positions_data, dict):
            positions_data = [positions_data]

        result = []
        for pos in positions_data:
            product = pos.get("Product", {})
            symbol = product.get("symbol", "")
            qty = float(pos.get("quantity", 0))
            cost_basis = float(pos.get("totalCost", 0))
            current_price = float(pos.get("Quick", {}).get("lastTrade", 0))
            market_value = float(pos.get("marketValue", 0))
            avg_entry = cost_basis / qty if qty > 0 else None
            unrealized_pl = market_value - cost_basis if cost_basis else None
            unrealized_plpc = (unrealized_pl / cost_basis) if cost_basis and unrealized_pl else None

            result.append(Position(
                symbol=symbol,
                qty=qty,
                avg_entry_price=avg_entry,
                cost_basis=cost_basis,
                current_price=current_price,
                unrealized_pl=unrealized_pl,
                unrealized_plpc=unrealized_plpc,
            ))
        return result

    def get_position(self, symbol: str) -> Optional[Position]:
        positions = self.get_positions()
        for pos in positions:
            if pos.symbol.upper() == symbol.upper():
                return pos
        return None

    def get_quotes(self, symbols: list[str]) -> dict[str, float]:
        if not symbols:
            return {}

        market = self._market_api()
        result = {}

        # E*TRADE allows up to 25 symbols per quote request
        for i in range(0, len(symbols), 25):
            batch = symbols[i : i + 25]
            try:
                quote_resp = market.get_quote(batch, resp_format="json")
                quote_data = quote_resp.get("QuoteResponse", {}).get("QuoteData", [])
                if isinstance(quote_data, dict):
                    quote_data = [quote_data]

                for q in quote_data:
                    product = q.get("Product", {})
                    sym = product.get("symbol", "")
                    all_data = q.get("All", {})
                    ask = float(all_data.get("ask", 0))
                    bid = float(all_data.get("bid", 0))
                    last = float(all_data.get("lastTrade", 0))

                    if ask > 0:
                        result[sym] = ask
                    elif bid > 0:
                        result[sym] = bid
                    elif last > 0:
                        result[sym] = last
            except Exception as e:
                logger.warning(f"Failed to get quotes for {batch}: {e}")

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
        orders = self._orders_api()

        order_action = "BUY" if side == "buy" else "SELL"
        price_type = "MARKET"
        order_kwargs = {}

        if limit_price is not None:
            price_type = "LIMIT"
            order_kwargs["limit_price"] = limit_price

        market_session = "EXTENDED" if extended_hours else "REGULAR"

        try:
            # Preview the order first
            preview = orders.place_equity_order(
                resp_format="json",
                accountIdKey=self._account_id_key,
                symbol=symbol,
                orderAction=order_action,
                clientOrderId=_generate_client_order_id(),
                priceType=price_type,
                quantity=qty,
                marketSession=market_session,
                orderTerm="GOOD_FOR_DAY",
                preview=True,
                **order_kwargs,
            )

            preview_ids = _extract_preview_ids(preview)

            # Place the order
            response = orders.place_equity_order(
                resp_format="json",
                accountIdKey=self._account_id_key,
                symbol=symbol,
                orderAction=order_action,
                clientOrderId=_generate_client_order_id(),
                priceType=price_type,
                quantity=qty,
                marketSession=market_session,
                orderTerm="GOOD_FOR_DAY",
                preview=False,
                previewId=preview_ids,
                **order_kwargs,
            )

            order_data = response.get("PlaceOrderResponse", {})
            order_ids = order_data.get("OrderIds", [{}])
            order_id = str(order_ids[0].get("orderId", "")) if order_ids else None

            return OrderResult(
                symbol=symbol,
                side=side,
                qty=qty,
                order_id=order_id,
                status="placed",
                extended_hours=extended_hours,
            )

        except Exception as e:
            logger.error(f"E*TRADE order failed for {symbol}: {e}")
            return OrderResult(
                symbol=symbol,
                side=side,
                qty=qty,
                error=str(e),
            )


def _generate_client_order_id() -> str:
    import uuid

    return str(uuid.uuid4())[:20].replace("-", "")


def _extract_preview_ids(preview_response: dict) -> str:
    preview_data = preview_response.get("PreviewOrderResponse", {})
    preview_ids = preview_data.get("PreviewIds", [{}])
    if preview_ids:
        return str(preview_ids[0].get("previewId", ""))
    return ""


def authorize_etrade(
    consumer_key: str, consumer_secret: str, is_paper: bool = True
) -> tuple[str, str]:
    """Interactive OAuth flow — returns (oauth_token, oauth_token_secret).

    Run this once to get tokens, then store them in config.
    Tokens must be refreshed daily (E*TRADE tokens expire at midnight ET).
    """
    import pyetrade

    oauth = pyetrade.ETradeOAuth(consumer_key, consumer_secret)
    request_token, request_token_secret = oauth.get_request_token()

    authorize_url = oauth.get_authorize_url(request_token)
    print(f"\nOpen this URL in your browser to authorize:\n\n  {authorize_url}\n")
    verifier = input("Enter the verification code: ").strip()

    oauth_token, oauth_token_secret = oauth.get_access_token(
        request_token, request_token_secret, verifier
    )

    print(f"\nAuthorization successful!")
    print(f"  oauth_token = \"{oauth_token}\"")
    print(f"  oauth_token_secret = \"{oauth_token_secret}\"")
    print(f"\nAdd these to your account config in application.conf")
    print(f"Note: E*TRADE tokens expire at midnight ET and must be refreshed daily.\n")

    return oauth_token, oauth_token_secret


def list_etrade_accounts(
    consumer_key: str,
    consumer_secret: str,
    oauth_token: str,
    oauth_token_secret: str,
    is_paper: bool = True,
) -> list[dict]:
    """List all accounts to find the account_id_key needed for config."""
    import pyetrade

    accounts = pyetrade.ETradeAccounts(
        consumer_key, consumer_secret, oauth_token, oauth_token_secret, dev=is_paper
    )
    resp = accounts.list_accounts(resp_format="json")
    account_list = resp.get("AccountListResponse", {}).get("Accounts", {}).get("Account", [])

    if isinstance(account_list, dict):
        account_list = [account_list]

    results = []
    for acct in account_list:
        results.append({
            "account_id_key": acct.get("accountIdKey", ""),
            "account_id": acct.get("accountId", ""),
            "account_name": acct.get("accountName", ""),
            "account_desc": acct.get("accountDesc", ""),
            "account_type": acct.get("accountType", ""),
        })
    return results
