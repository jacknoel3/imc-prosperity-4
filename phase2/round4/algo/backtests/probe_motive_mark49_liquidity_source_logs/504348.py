from __future__ import annotations

"""
Motivation probe for Mark 49: VE liquidity source vs weak seller to Mark 67.

Why we are testing this:
- Mark 49 appears mostly as a VE seller in the network, especially against
  Mark 67.
- Direct SUBMISSION evidence is limited.
- We need to know whether Mark 49 is a generic liquidity source we can buy from
  or whether the signal only matters when Mark 67 is the buyer.

How this probe works:
- Tracks Mark67 buying from Mark49 as a context trigger.
- Alternates between passive bids with no context, passive bids after
  Mark67->Mark49 context, tiny ask controls, and symmetric VE liquidity quotes.

What the logs can reveal:
- If buying from Mark49 is profitable only after Mark67 buys from Mark49, Mark49
  is a contextual source.
- If standalone Mark49 sells to us are profitable, Mark49 is directly exploitable.
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
    MAX_POS = 50
    WINDOW = 8000

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        timestamp = int(getattr(state, "timestamp", 0))
        memory = self._read_memory(getattr(state, "traderData", ""))
        self._update_context(state, memory, timestamp)
        bid, ask = self._best_bid_ask(state.order_depths.get(self.PRODUCT))
        if bid is None or ask is None:
            return result, 0, json.dumps(memory, separators=(",", ":"))
        pos = int(state.position.get(self.PRODUCT, 0))
        context = int(memory.get("mark67_from_49_expires", -1)) >= timestamp
        mode = ["STANDALONE_BID_MARK49", "CONTEXT_BID_MARK49", "ASK_CONTROL", "SYMMETRIC_BASELINE"][(timestamp // 3500) % 4]
        orders = self._orders(bid, ask, pos, mode, context)
        if orders:
            result[self.PRODUCT].extend(orders)
        fills = self._own_fill_notes(state)
        if fills or timestamp % 3500 == 0:
            print(f"MOTIVE_MARK49_SOURCE|mode={mode}|context={context}|t={timestamp}|mem={memory}|fills={';'.join(fills[:10])}|orders={','.join(str(o) for o in orders)}")
        memory["mode"] = mode
        return result, 0, json.dumps(memory, separators=(",", ":"))

    def _orders(self, bid: int, ask: int, pos: int, mode: str, context: bool) -> List[Order]:
        if abs(pos) >= int(0.85 * self.MAX_POS):
            return self._flatten(bid, ask, pos, 8)
        if mode == "STANDALONE_BID_MARK49" and not context:
            return self._buy_bait(bid, ask, pos, 3, offset=1)
        if mode == "CONTEXT_BID_MARK49" and context:
            return self._buy_bait(bid, ask, pos, 5, offset=1)
        if mode == "ASK_CONTROL":
            return self._sell_bait(bid, ask, pos, 2)
        return self._two_sided(bid, ask, pos, 2)

    def _update_context(self, state: TradingState, memory: dict, timestamp: int) -> None:
        for trade in state.market_trades.get(self.PRODUCT, []) + state.own_trades.get(self.PRODUCT, []):
            if getattr(trade, "buyer", "") == "Mark 67" and getattr(trade, "seller", "") == "Mark 49":
                memory["mark67_from_49_expires"] = timestamp + self.WINDOW

    def _buy_bait(self, bid: int, ask: int, pos: int, qty: int, offset: int) -> List[Order]:
        q = min(qty, self._buy_capacity(pos))
        px = min(ask - 1, bid + offset) if ask - bid >= offset + 2 else bid
        return [Order(self.PRODUCT, px, q)] if q > 0 and px < ask else []

    def _sell_bait(self, bid: int, ask: int, pos: int, qty: int) -> List[Order]:
        q = min(qty, self._sell_capacity(pos))
        px = max(bid + 1, ask - 1) if ask - bid >= 3 else ask
        return [Order(self.PRODUCT, px, -q)] if q > 0 and px > bid else []

    def _two_sided(self, bid: int, ask: int, pos: int, qty: int) -> List[Order]:
        return self._buy_bait(bid, ask, pos, qty, 1) + self._sell_bait(bid, ask, pos, qty)

    def _flatten(self, bid: int, ask: int, pos: int, qty: int) -> List[Order]:
        if pos > 0:
            return [Order(self.PRODUCT, bid, -min(pos, qty))]
        if pos < 0:
            return [Order(self.PRODUCT, ask, min(-pos, qty))]
        return []

    def _buy_capacity(self, pos: int) -> int:
        return max(0, min(self.LIMIT - pos, self.MAX_POS - pos))

    def _sell_capacity(self, pos: int) -> int:
        return max(0, min(self.LIMIT + pos, self.MAX_POS + pos))

    def _best_bid_ask(self, depth: Optional[OrderDepth]) -> Tuple[Optional[int], Optional[int]]:
        if depth is None or not depth.buy_orders or not depth.sell_orders:
            return None, None
        return max(depth.buy_orders), min(depth.sell_orders)

    def _read_memory(self, trader_data: str) -> dict:
        try:
            parsed = json.loads(trader_data) if trader_data else {}
        except Exception:
            parsed = {}
        return parsed if isinstance(parsed, dict) else {}

    def _own_fill_notes(self, state: TradingState) -> List[str]:
        return [f"{getattr(t,'buyer','')}>{getattr(t,'seller','')}@{getattr(t,'price','')}x{getattr(t,'quantity','')}" for t in state.own_trades.get(self.PRODUCT, [])]