from __future__ import annotations

"""
Vacuum / one-sided order book probe.

Why we are testing this:
- In Round 1, vacuum books and one-sided books created large PnL jumps because
  SUBMISSION could become the only visible liquidity and quote very favorable
  prices.
- Round 4 public price data and the strategy logs observed so far show no
  missing best bid or best ask. Still, if the final test contains even rare
  vacuum states, we want to detect and quantify them.

How this probe works:
- It normally posts no orders.
- If a book is one-sided, it posts a small quote on the missing side at a very
  favorable price relative to a static fair estimate.
- If a book is fully empty, it posts a small wide two-sided trap.
- It logs every product's book state with the tag VACUUM_BOOK_PROBE.

What the logs can reveal:
- Whether vacuum or one-sided books exist at all.
- Which products exhibit them.
- Which side is missing.
- Whether bots cross our monopoly quote.
- Which quantity is safe before inventory risk dominates.
"""

import json
from typing import Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ModuleNotFoundError:
    from prosperity3bt.datamodel import Order, OrderDepth, TradingState


class Trader:
    PRODUCTS = [
        "HYDROGEL_PACK", "VELVETFRUIT_EXTRACT", "VEV_4000", "VEV_4500",
        "VEV_5000", "VEV_5100", "VEV_5200", "VEV_5300", "VEV_5400",
        "VEV_5500", "VEV_6000", "VEV_6500",
    ]
    LIMITS = {
        "HYDROGEL_PACK": 200, "VELVETFRUIT_EXTRACT": 200,
        "VEV_4000": 300, "VEV_4500": 300, "VEV_5000": 300,
        "VEV_5100": 300, "VEV_5200": 300, "VEV_5300": 300,
        "VEV_5400": 300, "VEV_5500": 300, "VEV_6000": 300, "VEV_6500": 300,
    }
    FAIR = {
        "HYDROGEL_PACK": 10000, "VELVETFRUIT_EXTRACT": 5262,
        "VEV_4000": 1265, "VEV_4500": 764, "VEV_5000": 266,
        "VEV_5100": 175, "VEV_5200": 101, "VEV_5300": 50,
        "VEV_5400": 16, "VEV_5500": 6, "VEV_6000": 0, "VEV_6500": 0,
    }
    HALF = {
        "HYDROGEL_PACK": 30, "VELVETFRUIT_EXTRACT": 10,
        "VEV_4000": 18, "VEV_4500": 14, "VEV_5000": 9,
        "VEV_5100": 7, "VEV_5200": 5, "VEV_5300": 4,
        "VEV_5400": 3, "VEV_5500": 2, "VEV_6000": 1, "VEV_6500": 1,
    }

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        timestamp = int(getattr(state, "timestamp", 0))
        notes: List[str] = []
        orders_notes: List[str] = []

        for product in self.PRODUCTS:
            depth = state.order_depths.get(product)
            if depth is None:
                continue
            has_bid = bool(depth.buy_orders)
            has_ask = bool(depth.sell_orders)
            if has_bid and has_ask:
                continue
            state_label = "EMPTY" if not has_bid and not has_ask else ("NO_BID" if not has_bid else "NO_ASK")
            orders = self._orders(product, has_bid, has_ask, int(state.position.get(product, 0)), timestamp)
            if orders:
                result[product].extend(orders)
                orders_notes.append(product + ":" + ",".join(str(order) for order in orders))
            notes.append(f"{product}:{state_label}")

        fills = self._own_fill_notes(state)
        if notes or fills or timestamp % 5000 == 0:
            print(f"VACUUM_BOOK_PROBE|t={timestamp}|states={';'.join(notes)}|fills={';'.join(fills[:12])}|orders={';'.join(orders_notes[:12])}")

        return result, 0, json.dumps({"probe": "VACUUM_BOOK", "t": timestamp, "states": notes}, separators=(",", ":"))

    def _orders(self, product: str, has_bid: bool, has_ask: bool, pos: int, timestamp: int) -> List[Order]:
        fair = self.FAIR[product]
        half = self.HALF[product]
        qty = 1 + ((timestamp // 5000) % 4)
        limit = self.LIMITS[product]
        orders: List[Order] = []
        if not has_bid and not has_ask:
            buy_qty = min(qty, max(0, limit - pos))
            sell_qty = min(qty, max(0, limit + pos))
            if buy_qty > 0:
                orders.append(Order(product, max(0, fair - 2 * half), buy_qty))
            if sell_qty > 0:
                orders.append(Order(product, max(0, fair + 2 * half), -sell_qty))
        elif not has_ask:
            sell_qty = min(qty, max(0, limit + pos))
            if sell_qty > 0:
                orders.append(Order(product, max(0, fair + half), -sell_qty))
        elif not has_bid:
            buy_qty = min(qty, max(0, limit - pos))
            if buy_qty > 0:
                orders.append(Order(product, max(0, fair - half), buy_qty))
        return orders

    def _own_fill_notes(self, state: TradingState) -> List[str]:
        notes: List[str] = []
        for product in self.PRODUCTS:
            for trade in state.own_trades.get(product, []):
                notes.append(f"{product}:{getattr(trade, 'buyer', '')}>{getattr(trade, 'seller', '')}@{getattr(trade, 'price', '')}x{getattr(trade, 'quantity', '')}")
        return notes