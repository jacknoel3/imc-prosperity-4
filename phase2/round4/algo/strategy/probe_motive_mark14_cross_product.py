from __future__ import annotations

"""
Motivation probe for Mark 14: directional information vs cross-product hedging.

Why we are testing this:
- Mark 14 is the strongest informed player in the data, especially on
  HYDROGEL_PACK, VEV_4000, and VELVETFRUIT_EXTRACT.
- The open question is whether Mark 14 is mainly a same-product informed trader
  or whether it expresses a cross-product/basket view.

How this probe works:
- It posts tiny passive two-sided bait on HGP, VEV_4000, and VE.
- When Mark 14 buys/sells one product, the probe records a short-lived trigger.
- It alternates between same-product follow and cross-product follow:
  * SAME_PRODUCT_FOLLOW tests whether Mark 14 predicts the same product.
  * CROSS_PRODUCT_FOLLOW tests whether a Mark 14 trade in one product predicts
    related products.
  * AVOIDANCE_CONTROL quotes wider/smaller after Mark 14 activity.

What the logs can reveal:
- If same-product follow has better markout, Mark 14 is mostly directional.
- If cross-product follow has better markout, Mark 14 is likely trading a
  basket/hedge relation.
- If avoidance reduces adverse selection, Mark 14 should become a hard
  execution filter rather than an alpha to follow blindly.
"""

import json
from typing import Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ModuleNotFoundError:
    from prosperity3bt.datamodel import Order, OrderDepth, TradingState


class Trader:
    PRODUCTS = ["HYDROGEL_PACK", "VEV_4000", "VELVETFRUIT_EXTRACT"]
    LIMITS = {"HYDROGEL_PACK": 200, "VEV_4000": 300, "VELVETFRUIT_EXTRACT": 200}
    MAX_POS = {"HYDROGEL_PACK": 35, "VEV_4000": 35, "VELVETFRUIT_EXTRACT": 35}
    WINDOW = 7000

    CROSS_MAP = {
        "HYDROGEL_PACK": ["VEV_4000", "VELVETFRUIT_EXTRACT"],
        "VEV_4000": ["HYDROGEL_PACK", "VELVETFRUIT_EXTRACT"],
        "VELVETFRUIT_EXTRACT": ["HYDROGEL_PACK", "VEV_4000"],
    }

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        timestamp = int(getattr(state, "timestamp", 0))
        memory = self._read_memory(getattr(state, "traderData", ""))
        self._update_mark14_triggers(state, memory, timestamp)
        mode = ["PASSIVE_BAIT", "SAME_PRODUCT_FOLLOW", "CROSS_PRODUCT_FOLLOW", "AVOIDANCE_CONTROL"][(timestamp // 4000) % 4]
        notes: List[str] = []

        for product in self.PRODUCTS:
            bid, ask = self._best_bid_ask(state.order_depths.get(product))
            if bid is None or ask is None:
                continue
            position = int(state.position.get(product, 0))
            orders = self._orders(product, bid, ask, position, mode, memory, timestamp)
            if orders:
                result[product].extend(orders)
                notes.append(product + ":" + ",".join(str(order) for order in orders))

        fills = self._own_fill_notes(state)
        if fills or timestamp % 4000 == 0:
            print(f"MOTIVE_MARK14_CROSS|mode={mode}|t={timestamp}|mem={memory}|fills={';'.join(fills[:10])}|orders={';'.join(notes[:8])}")

        memory["mode"] = mode
        memory["t"] = timestamp
        return result, 0, json.dumps(memory, separators=(",", ":"))

    def _orders(self, product: str, bid: int, ask: int, position: int, mode: str, memory: dict, timestamp: int) -> List[Order]:
        if abs(position) >= int(0.85 * self.MAX_POS[product]):
            return self._flatten(product, bid, ask, position, 6)
        if mode == "SAME_PRODUCT_FOLLOW":
            trig = memory.get("mark14", {}).get(product, {})
            if int(trig.get("expires", -1)) >= timestamp:
                return self._follow(product, bid, ask, position, str(trig.get("direction", "")), 2)
        if mode == "CROSS_PRODUCT_FOLLOW":
            for source, targets in self.CROSS_MAP.items():
                if product not in targets:
                    continue
                trig = memory.get("mark14", {}).get(source, {})
                if int(trig.get("expires", -1)) >= timestamp:
                    return self._follow(product, bid, ask, position, str(trig.get("direction", "")), 1)
        offset = 2 if mode == "AVOIDANCE_CONTROL" else 1
        qty = 1 if mode == "AVOIDANCE_CONTROL" else 2
        return self._two_sided(product, bid, ask, position, qty, offset)

    def _update_mark14_triggers(self, state: TradingState, memory: dict, timestamp: int) -> None:
        triggers = memory.setdefault("mark14", {})
        for book_name in ("own_trades", "market_trades"):
            for product in self.PRODUCTS:
                for trade in getattr(state, book_name, {}).get(product, []):
                    if getattr(trade, "buyer", "") == "Mark 14":
                        triggers[product] = {"direction": "BUY", "expires": timestamp + self.WINDOW}
                    elif getattr(trade, "seller", "") == "Mark 14":
                        triggers[product] = {"direction": "SELL", "expires": timestamp + self.WINDOW}

    def _follow(self, product: str, bid: int, ask: int, position: int, direction: str, qty: int) -> List[Order]:
        if direction == "BUY":
            q = min(qty, self._buy_capacity(product, position))
            return [Order(product, ask, q)] if q > 0 else []
        if direction == "SELL":
            q = min(qty, self._sell_capacity(product, position))
            return [Order(product, bid, -q)] if q > 0 else []
        return []

    def _two_sided(self, product: str, bid: int, ask: int, position: int, qty: int, offset: int) -> List[Order]:
        orders: List[Order] = []
        buy_px = min(ask - 1, bid + offset) if ask - bid >= offset + 2 else bid
        sell_px = max(bid + 1, ask - offset) if ask - bid >= offset + 2 else ask
        bq = min(qty, self._buy_capacity(product, position))
        sq = min(qty, self._sell_capacity(product, position))
        if bq > 0 and buy_px < ask:
            orders.append(Order(product, buy_px, bq))
        if sq > 0 and sell_px > bid:
            orders.append(Order(product, sell_px, -sq))
        return orders

    def _flatten(self, product: str, bid: int, ask: int, position: int, qty: int) -> List[Order]:
        if position > 0:
            return [Order(product, bid, -min(position, qty))]
        if position < 0:
            return [Order(product, ask, min(-position, qty))]
        return []

    def _buy_capacity(self, product: str, position: int) -> int:
        return max(0, min(self.LIMITS[product] - position, self.MAX_POS[product] - position))

    def _sell_capacity(self, product: str, position: int) -> int:
        return max(0, min(self.LIMITS[product] + position, self.MAX_POS[product] + position))

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
