from __future__ import annotations

"""
Motivation probe for Mark 38: generally weak trader vs bad Mark14 counterparty.

Why we are testing this:
- Mark 38 is consistently fadeable, especially in HGP and VEV_4000.
- The open question is whether Mark 38 is generally noisy or only loses badly
  around Mark 14-linked flow.

How this probe works:
- Tracks recent Mark 14 activity in HGP/VEV_4000.
- Quotes/fades Mark 38 in two matched regimes:
  * FADE_MARK38_AFTER_MARK14: stronger fade after Mark14 activity.
  * FADE_MARK38_NO_MARK14: same fade with no recent Mark14 trigger.
  * PASSIVE_BASELINE: small symmetric bait.

What the logs can reveal:
- If Mark38 fade works only after Mark14 triggers, Mark38 weakness is relational.
- If it works in both regimes, Mark38 is generally weak/noisy.
"""

import json
from typing import Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ModuleNotFoundError:
    from prosperity3bt.datamodel import Order, OrderDepth, TradingState


class Trader:
    PRODUCTS = ["HYDROGEL_PACK", "VEV_4000", "VEV_4500", "VEV_5000", "VEV_5100", "VEV_5200"]
    LIMITS = {"HYDROGEL_PACK": 200, "VEV_4000": 300, "VEV_4500": 300, "VEV_5000": 300, "VEV_5100": 300, "VEV_5200": 300}
    MAX_POS = {"HYDROGEL_PACK": 45, "VEV_4000": 45, "VEV_4500": 35, "VEV_5000": 30, "VEV_5100": 25, "VEV_5200": 25}
    WINDOW = 7000

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        timestamp = int(getattr(state, "timestamp", 0))
        memory = self._read_memory(getattr(state, "traderData", ""))
        self._update_triggers(state, memory, timestamp)
        has_mark14 = int(memory.get("mark14_expires", -1)) >= timestamp
        phase = (timestamp // 3500) % 3
        mode = "FADE_MARK38_AFTER_MARK14" if has_mark14 and phase != 2 else ("FADE_MARK38_NO_MARK14" if phase == 1 else "PASSIVE_BASELINE")
        notes: List[str] = []

        for product in self.PRODUCTS:
            bid, ask = self._best_bid_ask(state.order_depths.get(product))
            if bid is None or ask is None:
                continue
            pos = int(state.position.get(product, 0))
            orders = self._orders(product, bid, ask, pos, mode, memory, timestamp)
            if orders:
                result[product].extend(orders)
                notes.append(product + ":" + ",".join(str(o) for o in orders))

        fills = self._own_fill_notes(state)
        if fills or timestamp % 3500 == 0:
            print(f"MOTIVE_MARK38_WEAKNESS|mode={mode}|mark14_recent={has_mark14}|t={timestamp}|mem={memory}|fills={';'.join(fills[:10])}|orders={';'.join(notes[:8])}")
        memory["mode"] = mode
        return result, 0, json.dumps(memory, separators=(",", ":"))

    def _orders(self, product: str, bid: int, ask: int, pos: int, mode: str, memory: dict, timestamp: int) -> List[Order]:
        if abs(pos) >= int(0.85 * self.MAX_POS[product]):
            return self._flatten(product, bid, ask, pos, 8)
        trig = memory.get("mark38", {}).get(product, {})
        direction = str(trig.get("direction", ""))
        active = int(trig.get("expires", -1)) >= timestamp
        if mode.startswith("FADE") and active:
            if direction == "BUY":
                return self._sell(product, bid, ask, pos, 2)
            if direction == "SELL":
                return self._buy(product, bid, ask, pos, 2)
        return self._two_sided(product, bid, ask, pos, 1)

    def _update_triggers(self, state: TradingState, memory: dict, timestamp: int) -> None:
        mark38 = memory.setdefault("mark38", {})
        for product in self.PRODUCTS:
            for trade in state.market_trades.get(product, []) + state.own_trades.get(product, []):
                buyer = getattr(trade, "buyer", "")
                seller = getattr(trade, "seller", "")
                if buyer == "Mark 14" or seller == "Mark 14":
                    memory["mark14_expires"] = timestamp + self.WINDOW
                if buyer == "Mark 38":
                    mark38[product] = {"direction": "BUY", "expires": timestamp + self.WINDOW}
                elif seller == "Mark 38":
                    mark38[product] = {"direction": "SELL", "expires": timestamp + self.WINDOW}

    def _buy(self, product: str, bid: int, ask: int, pos: int, qty: int) -> List[Order]:
        q = min(qty, self._buy_capacity(product, pos))
        return [Order(product, ask, q)] if q > 0 else []

    def _sell(self, product: str, bid: int, ask: int, pos: int, qty: int) -> List[Order]:
        q = min(qty, self._sell_capacity(product, pos))
        return [Order(product, bid, -q)] if q > 0 else []

    def _two_sided(self, product: str, bid: int, ask: int, pos: int, qty: int) -> List[Order]:
        orders: List[Order] = []
        bq = min(qty, self._buy_capacity(product, pos))
        sq = min(qty, self._sell_capacity(product, pos))
        if bq > 0 and bid < ask:
            orders.append(Order(product, bid, bq))
        if sq > 0 and ask > bid:
            orders.append(Order(product, ask, -sq))
        return orders

    def _flatten(self, product: str, bid: int, ask: int, pos: int, qty: int) -> List[Order]:
        if pos > 0:
            return [Order(product, bid, -min(pos, qty))]
        if pos < 0:
            return [Order(product, ask, min(-pos, qty))]
        return []

    def _buy_capacity(self, product: str, pos: int) -> int:
        return max(0, min(self.LIMITS[product] - pos, self.MAX_POS[product] - pos))

    def _sell_capacity(self, product: str, pos: int) -> int:
        return max(0, min(self.LIMITS[product] + pos, self.MAX_POS[product] + pos))

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