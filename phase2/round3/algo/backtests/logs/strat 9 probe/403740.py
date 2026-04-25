from __future__ import annotations

"""
Strategy 9 - price-ladder bot probe.

This is not a profit-maximizing strategy. It is an experimental diagnostic
submission designed to answer one question: will any bot trade against clearly
off-market resting quotes, and at what distance from the visible top of book?

The strategy posts tiny one-lot non-crossing buy and sell quotes at rotating
offsets from the current best bid/ask. If a quote is filled, the converted log
will reveal the product, timestamp, side, and execution price. Use this to map
whether bots are purely L1 mechanical, whether they accept stale/foolish prices,
and whether acceptance differs by product, offset, or time of day.
"""

import json
from typing import Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ModuleNotFoundError:
    from prosperity3bt.datamodel import Order, OrderDepth, TradingState


class Trader:
    PRODUCTS = [
        "HYDROGEL_PACK",
        "VELVETFRUIT_EXTRACT",
        "VEV_4000",
        "VEV_4500",
        "VEV_5000",
        "VEV_5100",
        "VEV_5200",
        "VEV_5300",
        "VEV_5400",
        "VEV_5500",
        "VEV_6000",
        "VEV_6500",
    ]
    LIMITS = {
        "HYDROGEL_PACK": 200,
        "VELVETFRUIT_EXTRACT": 200,
        "VEV_4000": 300,
        "VEV_4500": 300,
        "VEV_5000": 300,
        "VEV_5100": 300,
        "VEV_5200": 300,
        "VEV_5300": 300,
        "VEV_5400": 300,
        "VEV_5500": 300,
        "VEV_6000": 300,
        "VEV_6500": 300,
    }
    OFFSETS = [1, 2, 3, 5, 8, 13, 21, 34]

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        timestamp = int(getattr(state, "timestamp", 0))
        bucket = (timestamp // 1000) % len(self.OFFSETS)
        offset = self.OFFSETS[bucket]
        phase = (timestamp // 8000) % 3

        probe_notes = []
        for idx, product in enumerate(self.PRODUCTS):
            depth = state.order_depths.get(product)
            if depth is None:
                continue
            bid, ask = self._best_bid_ask(depth)
            if bid is None or ask is None:
                continue
            pos = int(state.position.get(product, 0))
            limit = self.LIMITS[product]

            product_offset = offset
            if product.startswith("VEV_6") or product in {"VEV_5400", "VEV_5500"}:
                product_offset = max(1, min(offset, 5))

            # Rotate which side is probed so position risk stays tiny and the
            # log can separate buy-acceptance from sell-acceptance.
            if (idx + phase) % 2 == 0 and pos < min(20, limit):
                price = max(0, bid - product_offset)
                result[product].append(Order(product, price, 1))
                probe_notes.append(f"{product}:B@bid-{product_offset}")
            elif pos > -min(20, limit):
                price = ask + product_offset
                result[product].append(Order(product, price, -1))
                probe_notes.append(f"{product}:S@ask+{product_offset}")

        if timestamp % 5000 == 0:
            print("PRICE_LADDER_PROBE|" + "|".join(probe_notes[:12]))
        return result, 0, json.dumps({"t": timestamp, "offset": offset, "phase": phase}, separators=(",", ":"))

    def _best_bid_ask(self, depth: Optional[OrderDepth]) -> Tuple[Optional[int], Optional[int]]:
        if depth is None:
            return None, None
        bid = max(depth.buy_orders) if depth.buy_orders else None
        ask = min(depth.sell_orders) if depth.sell_orders else None
        return bid, ask