from __future__ import annotations

"""
Motivation probe for Mark 67: informed VE buyer vs bullish-regime passenger.

Why we are testing this:
- Mark 67 is historically buyer-only in VE and has positive player-player
  markout, but direct SUBMISSION samples are sparse.
- We need a matched-trigger test: follow after Mark67 buys versus follow during
  similar timestamps without Mark67.

How this probe works:
- Posts passive ask bait to allow Mark67 to buy directly from SUBMISSION.
- Tracks direct and market Mark67 VE buys.
- Alternates between Mark67-triggered follow buys, random/control follow buys,
  ask bait, and bid bait for liquidity sellers.

What the logs can reveal:
- If Mark67-triggered follow beats control follow, Mark67 is a usable informed
  trigger.
- If both are similar, Mark67 may simply appear during bullish regimes.
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
    MAX_POS = 60
    WINDOW = 8000

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        timestamp = int(getattr(state, "timestamp", 0))
        memory = self._read_memory(getattr(state, "traderData", ""))
        self._update_triggers(state, memory, timestamp)
        bid, ask = self._best_bid_ask(state.order_depths.get(self.PRODUCT))
        if bid is None or ask is None:
            return result, 0, json.dumps(memory, separators=(",", ":"))
        pos = int(state.position.get(self.PRODUCT, 0))
        phase = (timestamp // 3500) % 4
        mode = ["ASK_BAIT_MARK67", "FOLLOW_MARK67_BUY", "CONTROL_FOLLOW_BUY", "BID_BAIT_SELLERS"][phase]
        orders = self._orders(bid, ask, pos, mode, memory, timestamp)
        if orders:
            result[self.PRODUCT].extend(orders)
        fills = self._own_fill_notes(state)
        if fills or timestamp % 3500 == 0:
            print(f"MOTIVE_MARK67_INFORMED|mode={mode}|t={timestamp}|mem={memory}|fills={';'.join(fills[:10])}|orders={','.join(str(o) for o in orders)}")
        memory["mode"] = mode
        return result, 0, json.dumps(memory, separators=(",", ":"))

    def _orders(self, bid: int, ask: int, pos: int, mode: str, memory: dict, timestamp: int) -> List[Order]:
        if abs(pos) >= int(0.85 * self.MAX_POS):
            return self._flatten(bid, ask, pos, 10)
        active = int(memory.get("mark67_buy_expires", -1)) >= timestamp
        if mode == "ASK_BAIT_MARK67":
            return self._sell_bait(bid, ask, pos, 5)
        if mode == "FOLLOW_MARK67_BUY" and active:
            return self._buy(ask, pos, 3)
        if mode == "CONTROL_FOLLOW_BUY" and not active and (timestamp // 7000) % 2 == 0:
            return self._buy(ask, pos, 2)
        if mode == "BID_BAIT_SELLERS":
            return self._buy_bait(bid, ask, pos, 3)
        return []

    def _update_triggers(self, state: TradingState, memory: dict, timestamp: int) -> None:
        for trade in state.market_trades.get(self.PRODUCT, []) + state.own_trades.get(self.PRODUCT, []):
            if getattr(trade, "buyer", "") == "Mark 67":
                memory["mark67_buy_expires"] = timestamp + self.WINDOW
                memory["last_mark67_price"] = getattr(trade, "price", 0)

    def _sell_bait(self, bid: int, ask: int, pos: int, qty: int) -> List[Order]:
        q = min(qty, self._sell_capacity(pos))
        px = max(bid + 1, ask - 1) if ask - bid >= 3 else ask
        return [Order(self.PRODUCT, px, -q)] if q > 0 and px > bid else []

    def _buy_bait(self, bid: int, ask: int, pos: int, qty: int) -> List[Order]:
        q = min(qty, self._buy_capacity(pos))
        px = min(ask - 1, bid + 1) if ask - bid >= 3 else bid
        return [Order(self.PRODUCT, px, q)] if q > 0 and px < ask else []

    def _buy(self, ask: int, pos: int, qty: int) -> List[Order]:
        q = min(qty, self._buy_capacity(pos))
        return [Order(self.PRODUCT, ask, q)] if q > 0 else []

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