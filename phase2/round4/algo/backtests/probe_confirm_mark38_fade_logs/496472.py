from __future__ import annotations

"""
Reduced-taker fade probe for Mark 38.

Hypothesis:
- Mark 38 is weak/fadeable on HYDROGEL_PACK and low-strike vouchers,
  especially against Mark 14-linked flow.

Test design:
- Passive two-sided quotes around HGP and VEV_4000, plus small VEV_4500-5200.
- No blind taker probing. The strategy only crosses to flatten inventory.
- Tracks whether Mark 38 fills us and whether those fills remain profitable.
- Logs use MARK38_FADE_PROBE.
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
    MAX_POS = {"HYDROGEL_PACK": 45, "VEV_4000": 55, "VEV_4500": 45, "VEV_5000": 40, "VEV_5100": 35, "VEV_5200": 35}

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        timestamp = int(getattr(state, "timestamp", 0))
        phase = (timestamp // 4500) % 3
        mode = ["PASSIVE_TWO_SIDED", "BUY_FROM_WEAK_SELLER", "SELL_TO_WEAK_BUYER"][phase]
        order_notes: List[str] = []

        for product in self.PRODUCTS:
            depth = state.order_depths.get(product)
            bid, ask = self._best_bid_ask(depth)
            if bid is None or ask is None:
                continue
            position = int(state.position.get(product, 0))
            qty = self._qty(product, timestamp)
            if abs(position) >= int(0.8 * self.MAX_POS[product]):
                orders = self._flatten(product, bid, ask, position, qty)
                local_mode = "CONTROLLED_FLATTEN"
            elif phase == 1:
                orders = self._buy_bait(product, bid, ask, position, qty)
                local_mode = mode
            elif phase == 2:
                orders = self._sell_bait(product, bid, ask, position, qty)
                local_mode = mode
            else:
                orders = self._two_sided(product, bid, ask, position, max(1, qty // 2))
                local_mode = mode
            if orders:
                result[product].extend(orders)
                order_notes.append(product + ":" + local_mode + ":" + ",".join(str(order) for order in orders))

        fills = self._own_fill_notes(state)
        if fills or timestamp % 4500 == 0:
            print(f"MARK38_FADE_PROBE|mode={mode}|t={timestamp}|fills={';'.join(fills[:10])}|orders={';'.join(order_notes[:8])}")

        return result, 0, json.dumps({"probe": "MARK38_FADE", "mode": mode, "t": timestamp}, separators=(",", ":"))

    def _qty(self, product: str, timestamp: int) -> int:
        bucket = 1 + ((timestamp // 1500) % 2)
        return (3 if product == "HYDROGEL_PACK" else 2) * bucket

    def _buy_bait(self, product: str, bid: int, ask: int, position: int, qty: int) -> List[Order]:
        buy_qty = min(qty, self._buy_capacity(product, position))
        price = min(ask - 1, bid + 1) if ask - bid >= 3 else bid
        return [Order(product, price, buy_qty)] if buy_qty > 0 and price < ask else []

    def _sell_bait(self, product: str, bid: int, ask: int, position: int, qty: int) -> List[Order]:
        sell_qty = min(qty, self._sell_capacity(product, position))
        price = max(bid + 1, ask - 1) if ask - bid >= 3 else ask
        return [Order(product, price, -sell_qty)] if sell_qty > 0 and price > bid else []

    def _two_sided(self, product: str, bid: int, ask: int, position: int, qty: int) -> List[Order]:
        return self._buy_bait(product, bid, ask, position, qty) + self._sell_bait(product, bid, ask, position, qty)

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