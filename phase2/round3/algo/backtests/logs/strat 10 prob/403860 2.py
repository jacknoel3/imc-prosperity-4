from __future__ import annotations

"""
Strategy 10 - maker-placement bot map.

This is not a production strategy. It is a controlled market-making experiment
that rotates quote placement across four modes: at the current L1, one tick
inside the spread, near the midpoint, and asymmetric one-sided inventory unwind.

The goal is to learn which products and placements receive fills, whether fills
are toxic, how fill size depends on quoted size, and whether bots behave like
different populations across products. After running this strategy, compare
fill counts and markouts by mode using the converted log. The strategy prints a
small MODE tag every 5k timestamps so the log can be segmented by experiment.
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

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        timestamp = int(getattr(state, "timestamp", 0))
        mode = (timestamp // 10000) % 4
        size_bucket = 1 + ((timestamp // 2500) % 4)

        mode_names = {
            0: "L1_SMALL",
            1: "INSIDE_SPREAD",
            2: "MIDPOINT",
            3: "INVENTORY_UNWIND",
        }
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
            limit = self.LIMITS[product]
            max_probe_pos = min(40, limit)
            qty = self._qty_for_product(product, size_bucket)

            if mode == 0:
                buy_px, sell_px = bid, ask
            elif mode == 1:
                buy_px = bid + 1 if spread >= 3 else bid
                sell_px = ask - 1 if spread >= 3 else ask
            elif mode == 2:
                buy_px = (bid + ask) // 2
                sell_px = buy_px + 1 if buy_px <= bid else buy_px
                if sell_px <= bid:
                    sell_px = ask
                if buy_px >= ask:
                    buy_px = bid
            else:
                buy_px = bid + 1 if spread >= 3 else bid
                sell_px = ask - 1 if spread >= 3 else ask
                qty = max(qty, 6)

            if mode == 3 and pos > 8:
                result[product].append(Order(product, sell_px, -min(qty * 2, pos + max_probe_pos)))
                notes.append(f"{product}:UNWIND_SELL")
            elif mode == 3 and pos < -8:
                result[product].append(Order(product, buy_px, min(qty * 2, max_probe_pos - pos)))
                notes.append(f"{product}:UNWIND_BUY")
            else:
                if pos < max_probe_pos and buy_px < ask:
                    result[product].append(Order(product, buy_px, min(qty, max_probe_pos - pos)))
                if pos > -max_probe_pos and sell_px > bid:
                    result[product].append(Order(product, sell_px, -min(qty, pos + max_probe_pos)))
                notes.append(f"{product}:B{buy_px}:S{sell_px}:q{qty}")

        if timestamp % 5000 == 0:
            print(f"MAKER_MAP|mode={mode_names[mode]}|size_bucket={size_bucket}|" + "|".join(notes[:8]))
        return result, 0, json.dumps({"t": timestamp, "mode": mode_names[mode], "size": size_bucket}, separators=(",", ":"))

    def _qty_for_product(self, product: str, bucket: int) -> int:
        if product == "HYDROGEL_PACK":
            return 3 * bucket
        if product == "VELVETFRUIT_EXTRACT":
            return 2 * bucket
        if product in {"VEV_4000", "VEV_4500", "VEV_5000"}:
            return bucket
        return max(1, bucket * 2)

    def _best_bid_ask(self, depth: Optional[OrderDepth]) -> Tuple[Optional[int], Optional[int]]:
        if depth is None:
            return None, None
        bid = max(depth.buy_orders) if depth.buy_orders else None
        ask = min(depth.sell_orders) if depth.sell_orders else None
        return bid, ask