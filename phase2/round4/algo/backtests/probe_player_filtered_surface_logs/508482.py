from __future__ import annotations

"""
Probe: surface residual trading with player-toxicity filters.

Why we are testing this:
- Surface-only signals tell us what looks mispriced, but Round 4 logs show that
  counterparty identity matters a lot:
  * Mark14 and Mark01 are toxic/informed when we provide the wrong liquidity.
  * Mark38 is fadeable, especially on HGP/VEV_4000/VEV_4500 and sometimes core VEV.
  * Mark55 is a useful VE liquidity source.
  * Mark67 buying VE is a do-not-sell signal.
- This probe tests whether adding only player filters improves the core
  VEV_5300/VEV_5400 surface trade.

How it works:
- It uses the same VEV_5300 short / VEV_5400 long surface logic.
- It blocks selling if Mark14/Mark01 recently bought the product.
- It blocks buying if Mark14/Mark01 recently sold the product.
- It increases size if Mark38 is the recent opposite-side participant.

What the logs should reveal:
- Whether player filters reduce adverse selection.
- Whether we lose too much fill capacity by avoiding Mark14/Mark01.
- Whether Mark38 still improves surface execution.
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
    TOXIC = {"Mark 14", "Mark 01"}
    FADEABLE = {"Mark 38"}

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        timestamp = int(getattr(state, "timestamp", 0))
        flow = self._recent_flow(state)
        notes: List[str] = []

        for product in self.PRODUCTS:
            snap = self._snapshot(state.order_depths.get(product))
            if snap is None:
                continue
            pos = int(state.position.get(product, 0))
            if product == "VEV_5400":
                fair = snap["mid"] + 1.80
                edge = fair - snap["ask"]
                blocked = bool(flow[product]["toxic_seller"])
                boost = bool(flow[product]["fadeable_seller"])
                qty = 8 + (8 if boost else 0)
                if edge >= 0.75 and not blocked:
                    q = min(qty, max(0, self.LIMIT - pos))
                    if q > 0:
                        result[product].append(Order(product, int(snap["ask"]), q))
                        notes.append(f"{product}:BUY:edge={edge:.2f}:boost={boost}:q={q}")
                elif blocked:
                    notes.append(f"{product}:BLOCK_BUY_TOXIC_SELLER")
            elif product == "VEV_5300":
                fair = snap["mid"] - 1.54
                edge = snap["bid"] - fair
                blocked = bool(flow[product]["toxic_buyer"])
                boost = bool(flow[product]["fadeable_buyer"])
                qty = 8 + (8 if boost else 0)
                if edge >= 0.75 and not blocked:
                    q = min(qty, max(0, self.LIMIT + pos))
                    if q > 0:
                        result[product].append(Order(product, int(snap["bid"]), -q))
                        notes.append(f"{product}:SELL:edge={edge:.2f}:boost={boost}:q={q}")
                elif blocked:
                    notes.append(f"{product}:BLOCK_SELL_TOXIC_BUYER")

        fills = self._own_fill_notes(state)
        if notes or fills or timestamp % 5000 == 0:
            print(f"PLAYER_FILTERED_SURFACE|t={timestamp}|signals={';'.join(notes)}|fills={';'.join(fills[:20])}")
        return result, 0, json.dumps({"probe": "PLAYER_FILTERED_SURFACE", "t": timestamp, "signals": notes[:20]}, separators=(",", ":"))

    def _recent_flow(self, state: TradingState) -> Dict[str, Dict[str, bool]]:
        out = {product: {"toxic_buyer": False, "toxic_seller": False, "fadeable_buyer": False, "fadeable_seller": False} for product in self.PRODUCTS}
        for product in self.PRODUCTS:
            for trade in state.market_trades.get(product, [])[-8:]:
                buyer = getattr(trade, "buyer", "")
                seller = getattr(trade, "seller", "")
                if buyer in self.TOXIC:
                    out[product]["toxic_buyer"] = True
                if seller in self.TOXIC:
                    out[product]["toxic_seller"] = True
                if buyer in self.FADEABLE:
                    out[product]["fadeable_buyer"] = True
                if seller in self.FADEABLE:
                    out[product]["fadeable_seller"] = True
        return out

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
        for product in self.PRODUCTS:
            for trade in state.own_trades.get(product, []):
                notes.append(f"{product}:{getattr(trade, 'buyer', '')}>{getattr(trade, 'seller', '')}@{trade.price}x{trade.quantity}")
        return notes