from __future__ import annotations

"""
Probe: pure option-surface residual trading on the core VEV contracts.

Why we are testing this:
- The Round 4 surface table says the clearest executable edge is:
  * VEV_5400 looks underpriced: robust buy gate rate = 58.26%.
  * VEV_5300 looks overpriced: robust sell gate rate = 22.39%.
  * VEV_5200 is unstable: robust and all-contracts surfaces disagree.
- This probe isolates that signal without player filters, hedging, or market
  making logic. If this fails, the surface edge is probably not executable on
  its own. If it works, later strategies can add player intelligence and
  inventory control.

How it works:
- It uses static residual estimates computed from Round 4 prices:
  fair ~= current_mid + historical_surface_residual.
- It only trades when executable edge beats a simple inventory gate.
- It is intentionally small and diagnostic; it logs every decision with
  SURFACE_RESIDUAL_CORE.

What the logs should reveal:
- Whether VEV_5400 long and VEV_5300 short are actually fillable.
- Whether the fill counterparties are toxic or fadeable.
- Whether markouts are positive before adding any bot filters.
"""

import json
from typing import Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ModuleNotFoundError:
    from prosperity3bt.datamodel import Order, OrderDepth, TradingState


class Trader:
    LIMIT = 300
    PRODUCTS = ["VEV_5200", "VEV_5300", "VEV_5400", "VEV_5500"]

    # Robust-core fair-minus-mid estimates from round4_option_surface_edge_summary.csv.
    FAIR_MINUS_MID = {
        "VEV_5200": -0.71,
        "VEV_5300": -1.54,
        "VEV_5400": 1.80,
        "VEV_5500": -0.38,
    }
    DELTA = {
        "VEV_5200": 0.63,
        "VEV_5300": 0.37,
        "VEV_5400": 0.17,
        "VEV_5500": 0.06,
    }

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        timestamp = int(getattr(state, "timestamp", 0))
        notes: List[str] = []

        for product in self.PRODUCTS:
            depth = state.order_depths.get(product)
            snap = self._snapshot(depth)
            if snap is None:
                continue

            pos = int(state.position.get(product, 0))
            fair = snap["mid"] + self.FAIR_MINUS_MID[product]
            gate = self._inventory_gate(product, snap["spread"])
            buy_edge = fair - snap["ask"]
            sell_edge = snap["bid"] - fair
            qty = self._size(product, buy_edge, sell_edge, pos)

            if buy_edge >= gate and qty > 0:
                buy_qty = min(qty, self.LIMIT - pos)
                if buy_qty > 0:
                    result[product].append(Order(product, int(snap["ask"]), buy_qty))
                    notes.append(self._note(product, "BUY", fair, buy_edge, gate, buy_qty, snap))
            if sell_edge >= gate and qty > 0:
                sell_qty = min(qty, self.LIMIT + pos)
                if sell_qty > 0:
                    result[product].append(Order(product, int(snap["bid"]), -sell_qty))
                    notes.append(self._note(product, "SELL", fair, sell_edge, gate, sell_qty, snap))

        fills = self._own_fill_notes(state)
        if notes or fills or timestamp % 5000 == 0:
            print(f"SURFACE_RESIDUAL_CORE|t={timestamp}|signals={';'.join(notes)}|fills={';'.join(fills[:20])}")

        payload = {"probe": "SURFACE_RESIDUAL_CORE", "t": timestamp, "signals": notes[:20]}
        return result, 0, json.dumps(payload, separators=(",", ":"))

    def _snapshot(self, depth: Optional[OrderDepth]) -> Optional[Dict[str, float]]:
        if depth is None or not depth.buy_orders or not depth.sell_orders:
            return None
        bid = max(depth.buy_orders)
        ask = min(depth.sell_orders)
        if bid >= ask:
            return None
        return {"bid": float(bid), "ask": float(ask), "mid": (bid + ask) / 2.0, "spread": float(ask - bid)}

    def _inventory_gate(self, product: str, spread: float) -> float:
        # Lower than the production gate because this is a diagnostic probe.
        hedge_cost = 2.5 * abs(self.DELTA[product])
        return max(0.50, 0.25 * spread, 0.50 * hedge_cost)

    def _size(self, product: str, buy_edge: float, sell_edge: float, pos: int) -> int:
        edge = max(buy_edge, sell_edge)
        base = 8 if product in ("VEV_5300", "VEV_5400") else 4
        if edge >= 2.0:
            base *= 2
        if abs(pos) > 160:
            base = max(1, base // 2)
        return base

    def _note(self, product: str, side: str, fair: float, edge: float, gate: float, qty: int, s: Dict[str, float]) -> str:
        return f"{product}:{side}:fair={fair:.2f}:edge={edge:.2f}:gate={gate:.2f}:qty={qty}:bid={s['bid']:.0f}:ask={s['ask']:.0f}"

    def _own_fill_notes(self, state: TradingState) -> List[str]:
        notes: List[str] = []
        for product in self.PRODUCTS:
            for trade in state.own_trades.get(product, []):
                notes.append(f"{product}:{getattr(trade, 'buyer', '')}>{getattr(trade, 'seller', '')}@{trade.price}x{trade.quantity}")
        return notes
