from __future__ import annotations

"""
Probe: adaptive market making around surface fair values.

Why we are testing this:
- Surface residual taker trades may be too capacity-limited. A market-making
  version could monetize the same information with better execution.
- The probe checks whether quoting around adjusted fair, with inventory skew,
  produces better fills than crossing the spread.

How it works:
- It quotes VEV_5300 and VEV_5400 around fair ~= mid + surface_residual.
- The fair quote is skewed away from current inventory.
- It only quotes when spread is wide enough to leave at least one tick of edge.
- It does not chase Mark14/Mark01; this is purely an execution experiment.

What the logs should reveal:
- Fill rate and markout of passive surface-aware quotes.
- Whether adverse selection is worse than spread capture.
- Whether inventory skew is enough to prevent terminal position problems.
"""

import json
from typing import Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ModuleNotFoundError:
    from prosperity3bt.datamodel import Order, OrderDepth, TradingState


class Trader:
    LIMIT = 300
    PRODUCTS = ["VEV_5300", "VEV_5400"]
    FAIR_MINUS_MID = {"VEV_5300": -1.54, "VEV_5400": 1.80}

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        timestamp = int(getattr(state, "timestamp", 0))
        notes: List[str] = []

        for product in self.PRODUCTS:
            snap = self._snapshot(state.order_depths.get(product))
            if snap is None:
                continue
            pos = int(state.position.get(product, 0))
            fair = snap["mid"] + self.FAIR_MINUS_MID[product]
            skew = 0.015 * pos
            reservation = fair - skew
            spread = snap["spread"]
            if spread < 2:
                continue

            bid_px = min(int(snap["bid"] + 1), int(reservation - 0.25))
            ask_px = max(int(snap["ask"] - 1), int(reservation + 0.25))
            size = 6 if abs(pos) < 150 else 3
            if bid_px < snap["ask"] and self.LIMIT - pos > 0:
                q = min(size, self.LIMIT - pos)
                result[product].append(Order(product, bid_px, q))
                notes.append(f"{product}:BID:{bid_px}:fair={fair:.2f}:pos={pos}:q={q}")
            if ask_px > snap["bid"] and self.LIMIT + pos > 0:
                q = min(size, self.LIMIT + pos)
                result[product].append(Order(product, ask_px, -q))
                notes.append(f"{product}:ASK:{ask_px}:fair={fair:.2f}:pos={pos}:q={q}")

        fills = self._own_fill_notes(state)
        if notes or fills or timestamp % 5000 == 0:
            print(f"ADAPTIVE_SURFACE_MM|t={timestamp}|signals={';'.join(notes[:20])}|fills={';'.join(fills[:20])}")
        return result, 0, json.dumps({"probe": "ADAPTIVE_SURFACE_MM", "t": timestamp, "signals": notes[:20]}, separators=(",", ":"))

    def _snapshot(self, depth: Optional[OrderDepth]) -> Optional[Dict[str, float]]:
        if depth is None or not depth.buy_orders or not depth.sell_orders:
            return None
        bid = max(depth.buy_orders)
        ask = min(depth.sell_orders)
        if bid >= ask:
            return None
        return {"bid": float(bid), "ask": float(ask), "mid": (bid + ask) / 2.0, "spread": float(ask - bid)}

    def _own_fill_notes(self, state: TradingState) -> List[str]:
        notes: List[str] = []
        for product in self.PRODUCTS:
            for trade in state.own_trades.get(product, []):
                notes.append(f"{product}:{getattr(trade, 'buyer', '')}>{getattr(trade, 'seller', '')}@{trade.price}x{trade.quantity}")
        return notes