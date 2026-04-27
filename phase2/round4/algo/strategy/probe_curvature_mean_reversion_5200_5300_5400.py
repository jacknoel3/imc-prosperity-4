from __future__ import annotations

"""
Probe: cross-strike curvature / mean-reversion around VEV_5200/5300/5400.

Why we are testing this:
- The EDA showed IV-gap mean reversion with half-life around 80-138 ticks.
- The Round 4 surface table says VEV_5200 is unstable, VEV_5300 is rich, and
  VEV_5400 is cheap. This can express itself as a curvature/butterfly anomaly.
- This probe does not assume a fixed direction forever. It measures a live
  curvature residual and trades only when the residual moves far from its own
  EWMA baseline.

How it works:
- Curvature residual = 2 * mid(5300) - mid(5200) - mid(5400).
- If residual is high, VEV_5300 is rich versus neighbors:
  sell 5300, buy 5200 and 5400.
- If residual is low, VEV_5300 is cheap versus neighbors:
  buy 5300, sell 5200 and 5400.
- This is deliberately a small diagnostic basket.

What the logs should reveal:
- Whether the curvature signal is stable enough to trade.
- Whether legging risk kills the theoretical edge.
- Whether player fills cluster on one leg, especially VEV_5300 or VEV_5400.
"""

import json
import math
from typing import Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ModuleNotFoundError:
    from prosperity3bt.datamodel import Order, OrderDepth, TradingState


class Trader:
    LIMIT = 300
    PRODUCTS = ["VEV_5200", "VEV_5300", "VEV_5400"]
    ALPHA = 0.03

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        timestamp = int(getattr(state, "timestamp", 0))
        memory = self._load(state.traderData)
        notes: List[str] = []

        snaps = {product: self._snapshot(state.order_depths.get(product)) for product in self.PRODUCTS}
        if all(snaps.values()):
            residual = 2.0 * snaps["VEV_5300"]["mid"] - snaps["VEV_5200"]["mid"] - snaps["VEV_5400"]["mid"]
            mean = memory.get("mean")
            var = memory.get("var", 1.0)
            if mean is None:
                mean = residual
            diff = residual - mean
            mean = (1.0 - self.ALPHA) * mean + self.ALPHA * residual
            var = (1.0 - self.ALPHA) * var + self.ALPHA * diff * diff
            z = diff / max(1e-6, math.sqrt(var))
            memory["mean"] = mean
            memory["var"] = var

            if timestamp > 3000 and abs(z) >= 1.5:
                basket_qty = 3 if abs(z) < 2.5 else 6
                if z > 1.5:
                    self._sell(result, state, "VEV_5300", int(snaps["VEV_5300"]["bid"]), 2 * basket_qty)
                    self._buy(result, state, "VEV_5200", int(snaps["VEV_5200"]["ask"]), basket_qty)
                    self._buy(result, state, "VEV_5400", int(snaps["VEV_5400"]["ask"]), basket_qty)
                    notes.append(f"RICH_5300:z={z:.2f}:res={residual:.2f}:qty={basket_qty}")
                elif z < -1.5:
                    self._buy(result, state, "VEV_5300", int(snaps["VEV_5300"]["ask"]), 2 * basket_qty)
                    self._sell(result, state, "VEV_5200", int(snaps["VEV_5200"]["bid"]), basket_qty)
                    self._sell(result, state, "VEV_5400", int(snaps["VEV_5400"]["bid"]), basket_qty)
                    notes.append(f"CHEAP_5300:z={z:.2f}:res={residual:.2f}:qty={basket_qty}")

        fills = self._own_fill_notes(state)
        if notes or fills or timestamp % 5000 == 0:
            print(f"CURVATURE_MR_5200_5300_5400|t={timestamp}|signals={';'.join(notes)}|fills={';'.join(fills[:20])}")
        return result, 0, json.dumps(memory, separators=(",", ":"))

    def _load(self, raw: str) -> Dict[str, float]:
        try:
            return json.loads(raw) if raw else {}
        except Exception:
            return {}

    def _snapshot(self, depth: Optional[OrderDepth]) -> Optional[Dict[str, float]]:
        if depth is None or not depth.buy_orders or not depth.sell_orders:
            return None
        bid = max(depth.buy_orders)
        ask = min(depth.sell_orders)
        if bid >= ask:
            return None
        return {"bid": float(bid), "ask": float(ask), "mid": (bid + ask) / 2.0}

    def _buy(self, result: Dict[str, List[Order]], state: TradingState, product: str, price: int, qty: int) -> None:
        cap = self.LIMIT - int(state.position.get(product, 0))
        q = min(qty, max(0, cap))
        if q > 0:
            result[product].append(Order(product, price, q))

    def _sell(self, result: Dict[str, List[Order]], state: TradingState, product: str, price: int, qty: int) -> None:
        cap = self.LIMIT + int(state.position.get(product, 0))
        q = min(qty, max(0, cap))
        if q > 0:
            result[product].append(Order(product, price, -q))

    def _own_fill_notes(self, state: TradingState) -> List[str]:
        notes: List[str] = []
        for product in self.PRODUCTS:
            for trade in state.own_trades.get(product, []):
                notes.append(f"{product}:{getattr(trade, 'buyer', '')}>{getattr(trade, 'seller', '')}@{trade.price}x{trade.quantity}")
        return notes
