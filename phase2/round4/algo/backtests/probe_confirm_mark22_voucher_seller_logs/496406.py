from __future__ import annotations

"""
Focused passive voucher probe for Mark 22 as structural seller.

Hypothesis:
- Mark 22 is a recurring seller/source of voucher convexity.
- We want to test how much convexity can be bought passively without paying
  the large taker cost seen in earlier broad probes.

Test design:
- Passive bids only most of the time, across VEV_5200-6500.
- Lower strikes get inside bids; far OTM strikes are bid only at zero/near zero.
- Occasional tiny ask quotes test whether Mark 01/14 immediately take us out.
- Logs use MARK22_VOUCHER_PROBE.
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
    MAX_PROBE_POS = {"VEV_5200": 55, "VEV_5300": 55, "VEV_5400": 45, "VEV_5500": 45, "VEV_6000": 70, "VEV_6500": 70}

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        timestamp = int(getattr(state, "timestamp", 0))
        phase = (timestamp // 5000) % 4
        mode = ["PASSIVE_BID_INSIDE", "PASSIVE_BID_ZERO_TAIL", "STAGGERED_BIDS", "TINY_ASK_TO_TEST_BUYERS"][phase]
        order_notes: List[str] = []

        for product in self.PRODUCTS:
            depth = state.order_depths.get(product)
            bid, ask = self._best_bid_ask(depth)
            if bid is None or ask is None:
                continue
            position = int(state.position.get(product, 0))
            qty = self._qty(product, timestamp)
            orders = self._orders_for_mode(product, bid, ask, position, qty, phase)
            if orders:
                result[product].extend(orders)
                order_notes.append(product + ":" + ",".join(str(order) for order in orders))

        fills = self._own_fill_notes(state)
        if fills or timestamp % 5000 == 0:
            print(f"MARK22_VOUCHER_PROBE|mode={mode}|t={timestamp}|fills={';'.join(fills[:10])}|orders={';'.join(order_notes)}")

        return result, 0, json.dumps({"probe": "MARK22_VOUCHER", "mode": mode, "t": timestamp}, separators=(",", ":"))

    def _orders_for_mode(self, product: str, bid: int, ask: int, position: int, qty: int, phase: int) -> List[Order]:
        if abs(position) >= int(0.85 * self.MAX_PROBE_POS[product]):
            return self._flatten(product, bid, ask, position, qty)
        if phase == 3:
            return self._tiny_ask(product, bid, ask, position, max(1, qty // 2))

        buy_qty = min(qty, self._buy_capacity(product, position))
        if buy_qty <= 0:
            return []

        if product in {"VEV_6000", "VEV_6500"}:
            price = 0 if bid <= 0 else min(bid, 1)
        elif phase == 0:
            price = min(ask - 1, bid + 1) if ask - bid >= 3 else bid
        elif phase == 1:
            price = max(0, bid)
        else:
            price = min(ask - 1, bid + (1 if product in {"VEV_5200", "VEV_5300"} else 0))
        return [Order(product, price, buy_qty)] if price < ask else []

    def _tiny_ask(self, product: str, bid: int, ask: int, position: int, qty: int) -> List[Order]:
        sell_qty = min(qty, self._sell_capacity(product, position))
        price = max(bid + 1, ask - 1) if ask - bid >= 3 else ask
        return [Order(product, price, -sell_qty)] if sell_qty > 0 and price > bid else []

    def _flatten(self, product: str, bid: int, ask: int, position: int, qty: int) -> List[Order]:
        if position > 0:
            return [Order(product, bid, -min(position, qty * 2))]
        if position < 0:
            return [Order(product, ask, min(-position, qty * 2))]
        return []

    def _qty(self, product: str, timestamp: int) -> int:
        bucket = 1 + ((timestamp // 1000) % 2)
        return (5 if product in {"VEV_6000", "VEV_6500"} else 2) * bucket

    def _buy_capacity(self, product: str, position: int) -> int:
        return max(0, min(self.LIMIT - position, self.MAX_PROBE_POS[product] - position))

    def _sell_capacity(self, product: str, position: int) -> int:
        return max(0, min(self.LIMIT + position, self.MAX_PROBE_POS[product] + position))

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