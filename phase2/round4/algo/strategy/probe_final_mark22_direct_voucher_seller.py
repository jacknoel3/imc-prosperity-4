from __future__ import annotations

"""
Final direct probe for Mark 22 as voucher seller.

Question to answer from logs:
- Is Mark 22 directly reachable by SUBMISSION as a seller of VEV_5200-6500,
  or was the historical Mark 22 seller signal mostly visible only against
  other bots such as Mark 01/14?

Test design:
- Spend most timestamps posting persistent passive bids in the voucher strip
  where Mark 22 historically sells convexity.
- Add tiny taker pings at the best ask to identify resting sellers without
  letting taker cost dominate the run.
- Keep inventory capped and flatten only when the probe inventory gets too
  large.
- Logs are tagged with MARK22_DIRECT_FINAL_PROBE and mode names so the
  post-log profiler can separate passive fills from taker identification.
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
    MAX_POS = {"VEV_5200": 85, "VEV_5300": 90, "VEV_5400": 95, "VEV_5500": 95, "VEV_6000": 120, "VEV_6500": 120}
    TAKER_CAP_PER_PRODUCT = 18

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        timestamp = int(getattr(state, "timestamp", 0))
        memory = self._read_memory(getattr(state, "traderData", ""))
        mode = self._mode(timestamp)
        notes: List[str] = []

        for product in self.PRODUCTS:
            depth = state.order_depths.get(product)
            bid, ask = self._best_bid_ask(depth)
            if bid is None or ask is None:
                continue
            position = int(state.position.get(product, 0))
            orders = self._orders(product, bid, ask, position, timestamp, mode, memory)
            if orders:
                result[product].extend(orders)
                notes.append(product + ":" + ",".join(str(order) for order in orders))

        fills = self._own_fill_notes(state)
        self._update_memory_from_fills(memory, state, timestamp)
        if fills or timestamp % 3000 == 0:
            print(
                f"MARK22_DIRECT_FINAL_PROBE|mode={mode}|t={timestamp}"
                + f"|fills={';'.join(fills[:12])}|orders={';'.join(notes[:10])}"
            )

        memory["mode"] = mode
        memory["t"] = timestamp
        return result, 0, json.dumps(memory, separators=(",", ":"))

    def _mode(self, timestamp: int) -> str:
        phase = (timestamp // 3000) % 5
        return [
            "PERSISTENT_INSIDE_BID",
            "JOIN_BID_LARGE_SAMPLE",
            "NEAR_ASK_PASSIVE_BID",
            "TINY_TAKER_SELLER_ID",
            "TAIL_ZERO_BID_SWEEP",
        ][phase]

    def _orders(self, product: str, bid: int, ask: int, position: int, timestamp: int, mode: str, memory: dict) -> List[Order]:
        if abs(position) >= int(0.88 * self.MAX_POS[product]):
            return self._flatten(product, bid, ask, position, qty=12)

        orders: List[Order] = []
        spread = ask - bid
        buy_cap = self._buy_capacity(product, position)
        if buy_cap <= 0:
            return orders

        base_qty = self._base_qty(product, timestamp)
        if mode == "TINY_TAKER_SELLER_ID":
            taker_used = int(memory.get(product + "_taker", 0))
            if taker_used < self.TAKER_CAP_PER_PRODUCT:
                qty = min(2, buy_cap, self.TAKER_CAP_PER_PRODUCT - taker_used)
                if qty > 0:
                    orders.append(Order(product, ask, qty))
                    memory[product + "_taker"] = taker_used + qty
            return orders

        if mode == "TAIL_ZERO_BID_SWEEP" and product in {"VEV_6000", "VEV_6500"}:
            px = 0 if bid <= 0 else min(bid, 1)
            qty = min(base_qty * 2, buy_cap)
        elif mode == "NEAR_ASK_PASSIVE_BID" and spread >= 4:
            px = ask - 1
            qty = min(max(1, base_qty // 2), buy_cap)
        elif mode == "PERSISTENT_INSIDE_BID" and spread >= 3:
            px = min(ask - 1, bid + 1)
            qty = min(base_qty, buy_cap)
        else:
            px = bid
            qty = min(base_qty * 2, buy_cap)

        if qty > 0 and px < ask:
            orders.append(Order(product, int(px), int(qty)))
        return orders

    def _base_qty(self, product: str, timestamp: int) -> int:
        bucket = 1 + ((timestamp // 1000) % 3)
        if product in {"VEV_6000", "VEV_6500"}:
            return 5 * bucket
        if product in {"VEV_5400", "VEV_5500"}:
            return 4 * bucket
        return 3 * bucket

    def _flatten(self, product: str, bid: int, ask: int, position: int, qty: int) -> List[Order]:
        if position > 0:
            return [Order(product, bid, -min(position, qty))]
        if position < 0:
            return [Order(product, ask, min(-position, qty))]
        return []

    def _buy_capacity(self, product: str, position: int) -> int:
        return max(0, min(self.LIMIT - position, self.MAX_POS[product] - position))

    def _read_memory(self, trader_data: str) -> dict:
        try:
            parsed = json.loads(trader_data) if trader_data else {}
        except Exception:
            parsed = {}
        return parsed if isinstance(parsed, dict) else {}

    def _update_memory_from_fills(self, memory: dict, state: TradingState, timestamp: int) -> None:
        mark22_hits = int(memory.get("mark22_sold_to_us", 0))
        for product in self.PRODUCTS:
            for trade in state.own_trades.get(product, []):
                if getattr(trade, "buyer", "") == "SUBMISSION" and getattr(trade, "seller", "") == "Mark 22":
                    mark22_hits += int(getattr(trade, "quantity", 0))
                    memory["last_mark22_sell_ts"] = timestamp
        memory["mark22_sold_to_us"] = mark22_hits

    def _best_bid_ask(self, depth: Optional[OrderDepth]) -> Tuple[Optional[int], Optional[int]]:
        if depth is None or not depth.buy_orders or not depth.sell_orders:
            return None, None
        return max(depth.buy_orders), min(depth.sell_orders)

    def _own_fill_notes(self, state: TradingState) -> List[str]:
        notes: List[str] = []
        for product in self.PRODUCTS:
            for trade in state.own_trades.get(product, []):
                notes.append(
                    f"{product}:{getattr(trade, 'buyer', '')}>{getattr(trade, 'seller', '')}"
                    + f"@{getattr(trade, 'price', '')}x{getattr(trade, 'quantity', '')}"
                )
        return notes
