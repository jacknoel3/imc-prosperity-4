from __future__ import annotations

"""
Motivation probe for Mark 22: standalone cheap seller vs contextual source.

Why we are testing this:
- Mark 22 is a structural voucher seller in the historical network.
- Direct SUBMISSION fills against Mark 22 have not proven a strong standalone
  cheap-convexity edge.
- We need to test whether Mark 22 is useful only when Mark 01/14 are also
  buying from Mark 22.

How this probe works:
- Posts passive bids on VEV_5200-6500.
- Splits behavior into matched modes:
  * MARK22_ALONE_BID: bid when no recent Mark01/14->Mark22 context exists.
  * MARK22_CONTEXT_BID: bid after Mark01 or Mark14 buys from Mark22.
  * ZERO_TAIL_CONTEXT: only bid far OTM at zero/near-zero after context.
  * CONTROL_SMALL_ASK: tiny ask control to detect informed buyers.

What the logs can reveal:
- If context bids beat standalone bids, Mark 22 is a source/context signal.
- If standalone bids are profitable too, Mark 22 is directly exploitable.
- If ask controls get hit by Mark01/14 with bad markout, avoid selling vouchers
  after Mark22 source activity.
"""

import json
from typing import Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ModuleNotFoundError:
    from prosperity3bt.datamodel import Order, OrderDepth, TradingState


class Trader:
    PRODUCTS = ["VEV_5200", "VEV_5300", "VEV_5400", "VEV_5500", "VEV_6000", "VEV_6500"]
    LIMIT = 300
    MAX_POS = {"VEV_5200": 55, "VEV_5300": 60, "VEV_5400": 70, "VEV_5500": 70, "VEV_6000": 100, "VEV_6500": 100}
    WINDOW = 9000

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        timestamp = int(getattr(state, "timestamp", 0))
        memory = self._read_memory(getattr(state, "traderData", ""))
        self._update_context(state, memory, timestamp)
        phase = (timestamp // 3500) % 4
        context = self._has_any_context(memory, timestamp)
        mode = ["MARK22_ALONE_BID", "MARK22_CONTEXT_BID", "ZERO_TAIL_CONTEXT", "CONTROL_SMALL_ASK"][phase]
        notes: List[str] = []

        for product in self.PRODUCTS:
            bid, ask = self._best_bid_ask(state.order_depths.get(product))
            if bid is None or ask is None:
                continue
            pos = int(state.position.get(product, 0))
            orders = self._orders(product, bid, ask, pos, mode, context)
            if orders:
                result[product].extend(orders)
                notes.append(product + ":" + ",".join(str(o) for o in orders))

        fills = self._own_fill_notes(state)
        if fills or timestamp % 3500 == 0:
            print(f"MOTIVE_MARK22_SOURCE|mode={mode}|context={context}|t={timestamp}|mem={memory}|fills={';'.join(fills[:10])}|orders={';'.join(notes[:8])}")
        memory["mode"] = mode
        return result, 0, json.dumps(memory, separators=(",", ":"))

    def _orders(self, product: str, bid: int, ask: int, pos: int, mode: str, context: bool) -> List[Order]:
        if abs(pos) >= int(0.85 * self.MAX_POS[product]):
            return self._flatten(product, bid, ask, pos, 10)
        if mode == "MARK22_ALONE_BID" and not context:
            return self._bid(product, bid, ask, pos, qty=3, offset=0)
        if mode == "MARK22_CONTEXT_BID" and context:
            return self._bid(product, bid, ask, pos, qty=5, offset=1)
        if mode == "ZERO_TAIL_CONTEXT" and context and product in {"VEV_6000", "VEV_6500"}:
            px = 0 if bid <= 0 else min(bid, 1)
            q = min(8, self._buy_capacity(product, pos))
            return [Order(product, px, q)] if q > 0 and px < ask else []
        if mode == "CONTROL_SMALL_ASK":
            q = min(1, self._sell_capacity(product, pos))
            px = max(bid + 1, ask - 1) if ask - bid >= 3 else ask
            return [Order(product, px, -q)] if q > 0 and px > bid else []
        return []

    def _update_context(self, state: TradingState, memory: dict, timestamp: int) -> None:
        ctx = memory.setdefault("mark22_context", {})
        for product in self.PRODUCTS:
            for trade in state.market_trades.get(product, []) + state.own_trades.get(product, []):
                buyer = getattr(trade, "buyer", "")
                seller = getattr(trade, "seller", "")
                if seller == "Mark 22" and buyer in {"Mark 01", "Mark 14"}:
                    ctx[product] = timestamp + self.WINDOW

    def _has_any_context(self, memory: dict, timestamp: int) -> bool:
        return any(int(v) >= timestamp for v in memory.get("mark22_context", {}).values())

    def _bid(self, product: str, bid: int, ask: int, pos: int, qty: int, offset: int) -> List[Order]:
        q = min(qty, self._buy_capacity(product, pos))
        px = min(ask - 1, bid + offset) if ask - bid >= offset + 2 else bid
        return [Order(product, px, q)] if q > 0 and px < ask else []

    def _flatten(self, product: str, bid: int, ask: int, pos: int, qty: int) -> List[Order]:
        if pos > 0:
            return [Order(product, bid, -min(pos, qty))]
        if pos < 0:
            return [Order(product, ask, min(-pos, qty))]
        return []

    def _buy_capacity(self, product: str, pos: int) -> int:
        return max(0, min(self.LIMIT - pos, self.MAX_POS[product] - pos))

    def _sell_capacity(self, product: str, pos: int) -> int:
        return max(0, min(self.LIMIT + pos, self.MAX_POS[product] + pos))

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
        notes: List[str] = []
        for product in self.PRODUCTS:
            for trade in state.own_trades.get(product, []):
                notes.append(f"{product}:{getattr(trade, 'buyer', '')}>{getattr(trade, 'seller', '')}@{getattr(trade, 'price', '')}x{getattr(trade, 'quantity', '')}")
        return notes
