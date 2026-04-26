from __future__ import annotations

"""
Mark 14 follow/avoid probe.

Hypothesis:
- Mark 14 is informed on HYDROGEL_PACK, VEV_4000, and VELVETFRUIT_EXTRACT.
- The production choice is whether to follow Mark 14 after detection, avoid
  quoting against them, or simply widen around their activity.

Test design:
- Passive detection quotes expose small size.
- If Mark 14 buys from us, follow with small buys for a short window.
- If Mark 14 sells to us, follow with small sells for a short window.
- Otherwise quote passively/wider than the old broad probes.
- Logs use MARK14_FOLLOW_AVOID_PROBE.
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
    MAX_POS = {"HYDROGEL_PACK": 45, "VEV_4000": 55, "VELVETFRUIT_EXTRACT": 50}
    FOLLOW_WINDOW = 6000

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        timestamp = int(getattr(state, "timestamp", 0))
        memory = self._read_memory(getattr(state, "traderData", ""))
        memory = self._update_mark14_signal(state, memory, timestamp)
        order_notes: List[str] = []

        for product in self.PRODUCTS:
            depth = state.order_depths.get(product)
            bid, ask = self._best_bid_ask(depth)
            if bid is None or ask is None:
                continue
            position = int(state.position.get(product, 0))
            qty = self._qty(product, timestamp)
            signal = memory.get(product, {})
            active = int(signal.get("expires", -1)) >= timestamp
            direction = str(signal.get("direction", ""))

            if abs(position) >= int(0.8 * self.MAX_POS[product]):
                mode = "CONTROLLED_FLATTEN"
                orders = self._flatten(product, bid, ask, position, qty)
            elif active and direction == "BUY":
                mode = "FOLLOW_MARK14_BUY"
                orders = self._follow_buy(product, bid, ask, position, qty)
            elif active and direction == "SELL":
                mode = "FOLLOW_MARK14_SELL"
                orders = self._follow_sell(product, bid, ask, position, qty)
            else:
                phase = (timestamp // 5000) % 3
                if phase == 0:
                    mode = "PASSIVE_DETECT_TWO_SIDED"
                    orders = self._two_sided(product, bid, ask, position, max(1, qty // 2), offset=1)
                elif phase == 1:
                    mode = "WIDE_AVOIDANCE_QUOTES"
                    orders = self._two_sided(product, bid, ask, position, max(1, qty // 2), offset=2)
                else:
                    mode = "SMALL_MARK14_BAIT"
                    orders = self._two_sided(product, bid, ask, position, qty, offset=1)

            if orders:
                result[product].extend(orders)
                order_notes.append(product + ":" + mode + ":" + ",".join(str(order) for order in orders))

        fills = self._own_fill_notes(state)
        if fills or timestamp % 5000 == 0:
            print(
                f"MARK14_FOLLOW_AVOID_PROBE|t={timestamp}|memory={memory}"
                + f"|fills={';'.join(fills[:10])}|orders={';'.join(order_notes[:8])}"
            )

        return result, 0, json.dumps(memory, separators=(",", ":"))

    def _read_memory(self, trader_data: str) -> dict:
        try:
            data = json.loads(trader_data) if trader_data else {}
        except Exception:
            data = {}
        return data if isinstance(data, dict) else {}

    def _update_mark14_signal(self, state: TradingState, memory: dict, timestamp: int) -> dict:
        for product in self.PRODUCTS:
            for trade in state.own_trades.get(product, []):
                buyer = getattr(trade, "buyer", "")
                seller = getattr(trade, "seller", "")
                if buyer == "Mark 14" and seller == "SUBMISSION":
                    memory[product] = {"direction": "BUY", "expires": timestamp + self.FOLLOW_WINDOW}
                elif buyer == "SUBMISSION" and seller == "Mark 14":
                    memory[product] = {"direction": "SELL", "expires": timestamp + self.FOLLOW_WINDOW}
        return memory

    def _qty(self, product: str, timestamp: int) -> int:
        bucket = 1 + ((timestamp // 1500) % 2)
        if product == "HYDROGEL_PACK":
            return 3 * bucket
        if product == "VELVETFRUIT_EXTRACT":
            return 2 * bucket
        return 2 * bucket

    def _two_sided(self, product: str, bid: int, ask: int, position: int, qty: int, offset: int) -> List[Order]:
        orders: List[Order] = []
        buy_qty = min(qty, self._buy_capacity(product, position))
        sell_qty = min(qty, self._sell_capacity(product, position))
        if ask - bid >= 2 + offset:
            buy_px = min(ask - 1, bid + offset)
            sell_px = max(bid + 1, ask - offset)
        else:
            buy_px, sell_px = bid, ask
        if buy_qty > 0 and buy_px < ask:
            orders.append(Order(product, buy_px, buy_qty))
        if sell_qty > 0 and sell_px > bid:
            orders.append(Order(product, sell_px, -sell_qty))
        return orders

    def _follow_buy(self, product: str, bid: int, ask: int, position: int, qty: int) -> List[Order]:
        buy_qty = min(max(1, qty // 2), self._buy_capacity(product, position))
        return [Order(product, ask, buy_qty)] if buy_qty > 0 else []

    def _follow_sell(self, product: str, bid: int, ask: int, position: int, qty: int) -> List[Order]:
        sell_qty = min(max(1, qty // 2), self._sell_capacity(product, position))
        return [Order(product, bid, -sell_qty)] if sell_qty > 0 else []

    def _flatten(self, product: str, bid: int, ask: int, position: int, qty: int) -> List[Order]:
        if position > 0:
            return [Order(product, bid, -min(position, qty * 2))]
        if position < 0:
            return [Order(product, ask, min(-position, qty * 2))]
        return []

    def _buy_capacity(self, product: str, position: int) -> int:
        return max(0, min(self.LIMITS[product] - position, self.MAX_POS[product] - position))

    def _sell_capacity(self, product: str, position: int) -> int:
        return max(0, min(self.LIMITS[product] + position, self.MAX_POS[product] + position))

    def _best_bid_ask(self, depth: Optional[OrderDepth]) -> Tuple[Optional[int], Optional[int]]:
        if depth is None or not depth.buy_orders or not depth.sell_orders:
            return None, None
        return max(depth.buy_orders), min(depth.sell_orders)

    def _own_fill_notes(self, state: TradingState) -> List[str]:
        notes: List[str] = []
        for product in self.PRODUCTS:
            for trade in state.own_trades.get(product, []):
                notes.append(f"{product}:{getattr(trade, 'buyer', '')}>{getattr(trade, 'seller', '')}@{getattr(trade, 'price', '')}x{getattr(trade, 'quantity', '')}")
        return notes
