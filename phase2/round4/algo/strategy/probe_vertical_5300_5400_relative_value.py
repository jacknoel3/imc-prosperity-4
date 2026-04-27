from __future__ import annotations

"""
Probe: VEV_5300 / VEV_5400 vertical relative-value trade.

Why we are testing this:
- The Round 4 surface calculation gives the cleanest pair signal:
  * VEV_5300 is rich/overpriced: sell-edge survives the gate on 22.39% of rows.
  * VEV_5400 is cheap/underpriced: buy-edge survives the gate on 58.26% of rows.
- This is the simplest relative-value expression of that result:
  short VEV_5300, long VEV_5400.

How it works:
- It executes a small vertical package when both legs are available:
  sell VEV_5300 at bid and buy VEV_5400 at ask.
- The default ratio is 2 short VEV_5300 vs 3 long VEV_5400. This is not perfectly
  delta-neutral, but it reduces vega/gamma mismatch compared with 1:1.
- It avoids adding when either leg is already near the position limit.

What the logs should reveal:
- Whether the paired edge survives real execution.
- Which leg gets filled first and creates legging risk.
- Whether Mark38/Mark22/Mark01/Mark14 appear on the fills.
"""

import json
from typing import Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ModuleNotFoundError:
    from prosperity3bt.datamodel import Order, OrderDepth, TradingState


class Trader:
    LIMIT = 300
    SHORT_PRODUCT = "VEV_5300"
    LONG_PRODUCT = "VEV_5400"
    SHORT_PER_PACKAGE = 2
    LONG_PER_PACKAGE = 3

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        timestamp = int(getattr(state, "timestamp", 0))
        notes: List[str] = []

        d_short = state.order_depths.get(self.SHORT_PRODUCT)
        d_long = state.order_depths.get(self.LONG_PRODUCT)
        s_short = self._snapshot(d_short)
        s_long = self._snapshot(d_long)
        if s_short and s_long:
            pos_short = int(state.position.get(self.SHORT_PRODUCT, 0))
            pos_long = int(state.position.get(self.LONG_PRODUCT, 0))

            # Historical surface residuals: VEV_5300 fair ~= mid - 1.54;
            # VEV_5400 fair ~= mid + 1.80.
            sell_edge_5300 = s_short["bid"] - (s_short["mid"] - 1.54)
            buy_edge_5400 = (s_long["mid"] + 1.80) - s_long["ask"]
            package_edge = self.SHORT_PER_PACKAGE * sell_edge_5300 + self.LONG_PER_PACKAGE * buy_edge_5400
            package_gate = 3.0

            max_packages_by_short = max(0, (self.LIMIT + pos_short) // self.SHORT_PER_PACKAGE)
            max_packages_by_long = max(0, (self.LIMIT - pos_long) // self.LONG_PER_PACKAGE)
            packages = min(4, max_packages_by_short, max_packages_by_long)
            if package_edge >= package_gate and packages > 0:
                short_qty = packages * self.SHORT_PER_PACKAGE
                long_qty = packages * self.LONG_PER_PACKAGE
                result[self.SHORT_PRODUCT].append(Order(self.SHORT_PRODUCT, int(s_short["bid"]), -short_qty))
                result[self.LONG_PRODUCT].append(Order(self.LONG_PRODUCT, int(s_long["ask"]), long_qty))
                notes.append(
                    f"PACKAGE:edge={package_edge:.2f}:pkgs={packages}:sell5300@{s_short['bid']:.0f}x{short_qty}:buy5400@{s_long['ask']:.0f}x{long_qty}"
                )

        fills = self._own_fill_notes(state)
        if notes or fills or timestamp % 5000 == 0:
            print(f"VERTICAL_5300_5400_RV|t={timestamp}|signals={';'.join(notes)}|fills={';'.join(fills[:20])}")
        return result, 0, json.dumps({"probe": "VERTICAL_5300_5400_RV", "t": timestamp, "signals": notes}, separators=(",", ":"))

    def _snapshot(self, depth: Optional[OrderDepth]) -> Optional[Dict[str, float]]:
        if depth is None or not depth.buy_orders or not depth.sell_orders:
            return None
        bid = max(depth.buy_orders)
        ask = min(depth.sell_orders)
        if bid >= ask:
            return None
        return {"bid": float(bid), "ask": float(ask), "mid": (bid + ask) / 2.0}

    def _own_fill_notes(self, state: TradingState) -> List[str]:
        notes: List[str] = []
        for product in [self.SHORT_PRODUCT, self.LONG_PRODUCT]:
            for trade in state.own_trades.get(product, []):
                notes.append(f"{product}:{getattr(trade, 'buyer', '')}>{getattr(trade, 'seller', '')}@{trade.price}x{trade.quantity}")
        return notes
