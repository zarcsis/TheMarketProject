"""Shrink a large (demo) account to the live account's size, keeping PnL one to one."""

from dataclasses import replace
from decimal import Decimal

from marketbot.exchange.futures import AccountState, Exchange


class VirtualBalance:
    """Delegates to a venue and subtracts a fixed offset from its balances.

    The offset is chosen once (actual equity minus the target size) and must stay the
    same across restarts, so PnL keeps flowing into the virtual equity unchanged.
    """

    def __init__(self, venue: Exchange, offset: Decimal) -> None:
        self._venue = venue
        self.offset = offset

    def __getattr__(self, name: str) -> object:
        return getattr(self._venue, name)

    async def account(self) -> AccountState:
        state = await self._venue.account()
        margin = state.margin_balance - self.offset
        return replace(
            state,
            margin_balance=margin,
            available_balance=min(state.available_balance, margin),
            other_wallets=Decimal(0),
        )
