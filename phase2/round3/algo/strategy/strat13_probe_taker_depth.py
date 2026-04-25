from __future__ import annotations

"""
Strategy 13 - controlled taker-depth toxicity probe.

This diagnostic strategy tests whether crossing the visible book ever has
positive information value. Existing logs suggest passive/maker trades dominate,
but we still need to map taker markouts by product and by depth level. The
strategy sends tiny scheduled taker orders against L1, L2, or L3 when available,
rotating side, product group, and level. Inventory is capped tightly.

After conversion, group fills by timestamp bucket: level=(timestamp//6000)%3 and
side=(timestamp//3000)%2. If taker markouts are consistently negative, production
strategies should avoid crossing except for emergency unwind. If any product or
level has positive markout, that becomes a new exploit candidate.
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
        "VEV_5000",
        "VEV_5100",
        "VEV_5200",
        "VEV_5300",
        "VEV_5400",
    ]
    LIMITS = {"HYDROGEL_PACK": 200, "VELVETFRUIT_EXTRACT": 200}
    LIMITS.update({p: 300 for p in PRODUCTS if p.startswith("VEV_")})

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        timestamp = int(getattr(state, "timestamp", 0))
        level = int((timestamp // 6000) % 3) + 1
        buy_side = ((timestamp // 3000) % 2) == 0
        product_slot = (timestamp // 12000) % len(self.PRODUCTS)
        notes = []

        # Only probe a rotating subset each tick to keep position risk readable.
        for shift in range(3):
            product = self.PRODUCTS[(product_slot + shift) % len(self.PRODUCTS)]
            depth = state.order_depths.get(product)
            if depth is None:
                continue
            pos = int(state.position.get(product, 0))
            cap = 18 if product in {"HYDROGEL_PACK", "VELVETFRUIT_EXTRACT"} else 10
            if buy_side and pos < cap:
                px, qty_avail = self._ask_level(depth, level)
                if px is not None and qty_avail > 0:
                    result.setdefault(product, []).append(Order(product, px, min(2, qty_avail, cap - pos)))
                    notes.append(f"{product}:BUY_L{level}@{px}")
            elif (not buy_side) and pos > -cap:
                px, qty_avail = self._bid_level(depth, level)
                if px is not None and qty_avail > 0:
                    result.setdefault(product, []).append(Order(product, px, -min(2, qty_avail, pos + cap)))
                    notes.append(f"{product}:SELL_L{level}@{px}")

        if timestamp % 3000 == 0:
            print(f"TAKER_DEPTH|level={level}|side={'BUY' if buy_side else 'SELL'}|" + "|".join(notes))
        return result, 0, json.dumps({"t": timestamp, "level": level, "side": "BUY" if buy_side else "SELL"}, separators=(",", ":"))

    def _ask_level(self, depth: Optional[OrderDepth], level: int) -> Tuple[Optional[int], int]:
        if depth is None or not depth.sell_orders:
            return None, 0
        asks = sorted(depth.sell_orders.items())
        if len(asks) < level:
            return None, 0
        px, vol = asks[level - 1]
        return int(px), max(0, -int(vol))

    def _bid_level(self, depth: Optional[OrderDepth], level: int) -> Tuple[Optional[int], int]:
        if depth is None or not depth.buy_orders:
            return None, 0
        bids = sorted(depth.buy_orders.items(), reverse=True)
        if len(bids) < level:
            return None, 0
        px, vol = bids[level - 1]
        return int(px), max(0, int(vol))
