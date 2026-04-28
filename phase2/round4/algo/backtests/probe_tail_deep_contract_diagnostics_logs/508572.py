from __future__ import annotations

"""
Probe: diagnostic handling of VEV_4000/4500/6000/6500.

Why we are testing this:
- The user explicitly asked not to ignore 4000/4500/6000/6500 because they may
  contain information.
- The data says:
  * VEV_6000/6500 should never be bought at 1 blindly. Round 3 test lost about
    -600 by filling both limits at 1.
  * VEV_6000/6500 trades occur at price 0 historically, mostly Mark01 buying
    from Mark22, so they can be an information signal even if not a direct alpha.
  * VEV_4000/4500 are deep ITM, mostly delta-like, and useful for Mark38 fade
    diagnostics rather than convexity inventory.

How it works:
- VEV_6000/6500: only posts bid 0 and sells at 1 if we somehow own inventory.
  It never pays 1.
- VEV_4000/4500: posts small inside-spread fade quotes, with extra emphasis if
  Mark38 recently appeared.

What the logs should reveal:
- Whether zero-price queue fills are realistically possible.
- Whether tail voucher fills carry useful bot information even without PnL.
- Whether 4000/4500 Mark38 fade remains useful under a tiny, controlled test.
"""

import json
from typing import Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ModuleNotFoundError:
    from prosperity3bt.datamodel import Order, OrderDepth, TradingState


class Trader:
    LIMIT = 300
    DEEP = ["VEV_4000", "VEV_4500"]
    TAIL = ["VEV_6000", "VEV_6500"]

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        timestamp = int(getattr(state, "timestamp", 0))
        notes: List[str] = []

        for product in self.TAIL:
            pos = int(state.position.get(product, 0))
            buy_cap = max(0, self.LIMIT - pos)
            sell_cap = max(0, self.LIMIT + pos)
            if buy_cap > 0:
                q = min(20, buy_cap)
                result[product].append(Order(product, 0, q))
                notes.append(f"{product}:BID_ZERO:q={q}")
            if pos > 0 and sell_cap > 0:
                q = min(20, pos)
                result[product].append(Order(product, 1, -q))
                notes.append(f"{product}:SELL_ONE_FLATTEN:q={q}")

        for product in self.DEEP:
            snap = self._snapshot(state.order_depths.get(product))
            if snap is None:
                continue
            pos = int(state.position.get(product, 0))
            mark38_seen = self._mark38_seen(state, product)
            qty = 2 + (3 if mark38_seen else 0)
            if snap["spread"] >= (18 if product == "VEV_4000" else 14):
                buy_price = int(snap["bid"] + 1)
                sell_price = int(snap["ask"] - 1)
                if self.LIMIT - pos > 0:
                    result[product].append(Order(product, buy_price, min(qty, self.LIMIT - pos)))
                if self.LIMIT + pos > 0:
                    result[product].append(Order(product, sell_price, -min(qty, self.LIMIT + pos)))
                notes.append(f"{product}:DEEP_ITM_INSIDE:spread={snap['spread']:.0f}:mark38={mark38_seen}:q={qty}")

        fills = self._own_fill_notes(state)
        if notes or fills or timestamp % 5000 == 0:
            print(f"TAIL_DEEP_DIAGNOSTICS|t={timestamp}|signals={';'.join(notes[:20])}|fills={';'.join(fills[:20])}")
        return result, 0, json.dumps({"probe": "TAIL_DEEP_DIAGNOSTICS", "t": timestamp, "signals": notes[:20]}, separators=(",", ":"))

    def _snapshot(self, depth: Optional[OrderDepth]) -> Optional[Dict[str, float]]:
        if depth is None or not depth.buy_orders or not depth.sell_orders:
            return None
        bid = max(depth.buy_orders)
        ask = min(depth.sell_orders)
        if bid >= ask:
            return None
        return {"bid": float(bid), "ask": float(ask), "mid": (bid + ask) / 2.0, "spread": float(ask - bid)}

    def _mark38_seen(self, state: TradingState, product: str) -> bool:
        for trade in state.market_trades.get(product, [])[-8:]:
            if getattr(trade, "buyer", "") == "Mark 38" or getattr(trade, "seller", "") == "Mark 38":
                return True
        return False

    def _own_fill_notes(self, state: TradingState) -> List[str]:
        notes: List[str] = []
        for product in self.DEEP + self.TAIL:
            for trade in state.own_trades.get(product, []):
                notes.append(f"{product}:{getattr(trade, 'buyer', '')}>{getattr(trade, 'seller', '')}@{trade.price}x{trade.quantity}")
        return notes