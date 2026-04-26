from __future__ import annotations

"""
Focused VE probe for Mark 67.

Hypothesis:
- Mark 67's player-player buying in VELVETFRUIT_EXTRACT may be an informed
  bullish signal, but direct SUBMISSION evidence is still thin.

Test design:
- Mostly passive ask bait to let a structural/aggressive buyer reveal itself.
- Secondary passive bid bait to identify liquidity sellers around Mark 49/55/22.
- No blind taker mode; flatten only when inventory becomes too large.
- Logs use MARK67_VE_PROBE for easy post-run filtering.
"""

import json
from typing import Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ModuleNotFoundError:
    from prosperity3bt.datamodel import Order, OrderDepth, TradingState


class Trader:
    PRODUCT = "VELVETFRUIT_EXTRACT"
    LIMIT = 200
    MAX_PROBE_POS = 50

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        timestamp = int(getattr(state, "timestamp", 0))
        depth = state.order_depths.get(self.PRODUCT)
        if depth is None:
            return result, 0, self._data(timestamp, "NO_BOOK")

        bid, ask = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return result, 0, self._data(timestamp, "NO_BBO")

        position = int(state.position.get(self.PRODUCT, 0))
        phase = (timestamp // 4000) % 4
        qty = 3 + ((timestamp // 1000) % 3)
        mode = ["ASK_BAIT_MARK67", "ASK_BAIT_DEEPER", "BID_BAIT_SELLERS", "PASSIVE_TWO_SIDED"][phase]

        orders: List[Order] = []
        if abs(position) >= int(0.8 * self.MAX_PROBE_POS):
            mode = "CONTROLLED_FLATTEN"
            orders = self._flatten(bid, ask, position, qty)
        elif phase == 0:
            orders = self._sell_bait(bid, ask, position, qty, offset=1)
        elif phase == 1:
            orders = self._sell_bait(bid, ask, position, qty, offset=2)
        elif phase == 2:
            orders = self._buy_bait(bid, ask, position, qty, offset=1)
        else:
            orders = self._two_sided(bid, ask, position, max(1, qty // 2))

        if orders:
            result[self.PRODUCT].extend(orders)

        fills = self._own_fill_notes(state)
        if fills or timestamp % 4000 == 0:
            print(
                f"MARK67_VE_PROBE|mode={mode}|t={timestamp}|pos={position}|bbo={bid}/{ask}"
                + f"|fills={';'.join(fills[:8])}|orders={','.join(str(o) for o in orders)}"
            )

        return result, 0, self._data(timestamp, mode)

    def _data(self, timestamp: int, mode: str) -> str:
        return json.dumps({"probe": "MARK67_VE", "mode": mode, "t": timestamp}, separators=(",", ":"))

    def _own_fill_notes(self, state: TradingState) -> List[str]:
        notes: List[str] = []
        for trade in state.own_trades.get(self.PRODUCT, []):
            notes.append(
                f"{getattr(trade, 'buyer', '')}>{getattr(trade, 'seller', '')}"
                + f"@{getattr(trade, 'price', '')}x{getattr(trade, 'quantity', '')}"
            )
        return notes

    def _best_bid_ask(self, depth: Optional[OrderDepth]) -> Tuple[Optional[int], Optional[int]]:
        if depth is None or not depth.buy_orders or not depth.sell_orders:
            return None, None
        return max(depth.buy_orders), min(depth.sell_orders)

    def _inside_price(self, bid: int, ask: int, side: str, offset: int) -> int:
        if ask - bid <= 2:
            return ask if side == "SELL" else bid
        return max(bid + 1, ask - offset) if side == "SELL" else min(ask - 1, bid + offset)

    def _buy_capacity(self, position: int) -> int:
        return max(0, min(self.LIMIT - position, self.MAX_PROBE_POS - position))

    def _sell_capacity(self, position: int) -> int:
        return max(0, min(self.LIMIT + position, self.MAX_PROBE_POS + position))

    def _sell_bait(self, bid: int, ask: int, position: int, qty: int, offset: int) -> List[Order]:
        sell_qty = min(qty, self._sell_capacity(position))
        price = self._inside_price(bid, ask, "SELL", offset)
        return [Order(self.PRODUCT, price, -sell_qty)] if sell_qty > 0 and price > bid else []

    def _buy_bait(self, bid: int, ask: int, position: int, qty: int, offset: int) -> List[Order]:
        buy_qty = min(qty, self._buy_capacity(position))
        price = self._inside_price(bid, ask, "BUY", offset)
        return [Order(self.PRODUCT, price, buy_qty)] if buy_qty > 0 and price < ask else []

    def _two_sided(self, bid: int, ask: int, position: int, qty: int) -> List[Order]:
        orders: List[Order] = []
        orders.extend(self._buy_bait(bid, ask, position, qty, offset=1))
        orders.extend(self._sell_bait(bid, ask, position, qty, offset=1))
        return orders

    def _flatten(self, bid: int, ask: int, position: int, qty: int) -> List[Order]:
        if position > 0:
            return [Order(self.PRODUCT, bid, -min(position, qty * 2))]
        if position < 0:
            return [Order(self.PRODUCT, ask, min(-position, qty * 2))]
        return self._two_sided(bid, ask, position, max(1, qty // 2))
