from __future__ import annotations

"""
Strategy 12 - displayed-size sensitivity probe.

This diagnostic strategy tests whether bot fill probability and fill quantity
depend on our displayed order size. Strat10 showed that maker placement matters,
while strat9 showed off-market quotes do not get filled. This probe holds
placement mostly constant (L1 or one tick inside the spread) and rotates size
through [1, 3, 8, 20, 50]. The converted log should be grouped by timestamp
bucket to estimate fill rate, partial-fill behavior, and adverse markout as a
function of displayed size.

This is not intended to maximize PnL. It caps inventory tightly and posts only
non-crossing orders.
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
    ]
    LIMITS = {"HYDROGEL_PACK": 200, "VELVETFRUIT_EXTRACT": 200}
    LIMITS.update({p: 300 for p in PRODUCTS if p.startswith("VEV_")})
    SIZES = [1, 3, 8, 20, 50]

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        timestamp = int(getattr(state, "timestamp", 0))
        size_idx = (timestamp // 4000) % len(self.SIZES)
        raw_size = self.SIZES[size_idx]
        inside = ((timestamp // 20000) % 2) == 1
        side_mode = (timestamp // 10000) % 3
        notes = []

        for product in self.PRODUCTS:
            depth = state.order_depths.get(product)
            if depth is None:
                continue
            bid, ask = self._best_bid_ask(depth)
            if bid is None or ask is None:
                continue
            spread = ask - bid
            pos = int(state.position.get(product, 0))
            max_pos = 60 if product in {"HYDROGEL_PACK", "VELVETFRUIT_EXTRACT"} else 35
            qty = min(raw_size, max_pos)
            buy_px = bid + 1 if inside and spread >= 3 else bid
            sell_px = ask - 1 if inside and spread >= 3 else ask

            if side_mode in (0, 2) and pos < max_pos and buy_px < ask:
                result[product].append(Order(product, buy_px, min(qty, max_pos - pos)))
            if side_mode in (1, 2) and pos > -max_pos and sell_px > bid:
                result[product].append(Order(product, sell_px, -min(qty, pos + max_pos)))
            notes.append(f"{product}:q{qty}:{'inside' if inside else 'l1'}")

        if timestamp % 4000 == 0:
            print(f"SIZE_SWEEP|size={raw_size}|inside={inside}|side_mode={side_mode}|" + "|".join(notes[:8]))
        return result, 0, json.dumps({"t": timestamp, "size": raw_size, "inside": inside, "side": side_mode}, separators=(",", ":"))

    def _best_bid_ask(self, depth: Optional[OrderDepth]) -> Tuple[Optional[int], Optional[int]]:
        if depth is None:
            return None, None
        bid = max(depth.buy_orders) if depth.buy_orders else None
        ask = min(depth.sell_orders) if depth.sell_orders else None
        return bid, ask