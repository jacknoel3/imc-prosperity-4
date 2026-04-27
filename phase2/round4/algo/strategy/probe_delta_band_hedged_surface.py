from __future__ import annotations

"""
Probe: surface residual trades with a simple delta band hedge in VELVETFRUIT.

Why we are testing this:
- Surface-only trades can show theoretical alpha but lose through directional
  inventory. The EDA also showed that continuous hedging is too expensive.
- This probe tests a middle ground: trade the strongest surface residuals, but
  only hedge the aggregate delta when it leaves a wide no-hedge band.

How it works:
- It buys VEV_5400 when surface edge is positive and sells VEV_5300 when rich.
- It estimates option delta from the Round 4 surface table.
- If net portfolio delta exceeds +/-60, it trades VELVETFRUIT_EXTRACT to pull
  delta back toward zero. The hedge is partial and event-driven, not continuous.

What the logs should reveal:
- Whether delta-band hedging improves markout and terminal PnL.
- Whether hedge costs eat the option edge.
- Whether we should leave intentional residual delta instead of forcing flat.
"""

import json
from typing import Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ModuleNotFoundError:
    from prosperity3bt.datamodel import Order, OrderDepth, TradingState


class Trader:
    OPTION_LIMIT = 300
    VE_LIMIT = 200
    DELTA_BAND = 60.0
    HEDGE_CHUNK = 20
    PRODUCTS = ["VEV_5300", "VEV_5400", "VELVETFRUIT_EXTRACT"]
    DELTA = {"VEV_5300": 0.37, "VEV_5400": 0.17, "VELVETFRUIT_EXTRACT": 1.0}

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        timestamp = int(getattr(state, "timestamp", 0))
        notes: List[str] = []

        s5300 = self._snapshot(state.order_depths.get("VEV_5300"))
        s5400 = self._snapshot(state.order_depths.get("VEV_5400"))
        if s5300:
            fair5300 = s5300["mid"] - 1.54
            sell_edge = s5300["bid"] - fair5300
            if sell_edge >= 0.75:
                self._sell(result, state, "VEV_5300", int(s5300["bid"]), 10)
                notes.append(f"SELL_5300:edge={sell_edge:.2f}")
        if s5400:
            fair5400 = s5400["mid"] + 1.80
            buy_edge = fair5400 - s5400["ask"]
            if buy_edge >= 0.75:
                self._buy(result, state, "VEV_5400", int(s5400["ask"]), 12)
                notes.append(f"BUY_5400:edge={buy_edge:.2f}")

        net_delta = self._net_delta(state)
        ve = self._snapshot(state.order_depths.get("VELVETFRUIT_EXTRACT"))
        if ve and net_delta > self.DELTA_BAND:
            self._sell_ve(result, state, int(ve["bid"]), min(self.HEDGE_CHUNK, int(net_delta - self.DELTA_BAND)))
            notes.append(f"HEDGE_SELL_VE:delta={net_delta:.1f}")
        elif ve and net_delta < -self.DELTA_BAND:
            self._buy_ve(result, state, int(ve["ask"]), min(self.HEDGE_CHUNK, int(-self.DELTA_BAND - net_delta)))
            notes.append(f"HEDGE_BUY_VE:delta={net_delta:.1f}")

        fills = self._own_fill_notes(state)
        if notes or fills or timestamp % 5000 == 0:
            print(f"DELTA_BAND_HEDGED_SURFACE|t={timestamp}|delta={net_delta:.1f}|signals={';'.join(notes)}|fills={';'.join(fills[:20])}")
        return result, 0, json.dumps({"probe": "DELTA_BAND_HEDGED_SURFACE", "t": timestamp, "delta": net_delta}, separators=(",", ":"))

    def _net_delta(self, state: TradingState) -> float:
        return sum(float(state.position.get(product, 0)) * delta for product, delta in self.DELTA.items())

    def _snapshot(self, depth: Optional[OrderDepth]) -> Optional[Dict[str, float]]:
        if depth is None or not depth.buy_orders or not depth.sell_orders:
            return None
        bid = max(depth.buy_orders)
        ask = min(depth.sell_orders)
        if bid >= ask:
            return None
        return {"bid": float(bid), "ask": float(ask), "mid": (bid + ask) / 2.0}

    def _buy(self, result: Dict[str, List[Order]], state: TradingState, product: str, price: int, qty: int) -> None:
        q = min(qty, max(0, self.OPTION_LIMIT - int(state.position.get(product, 0))))
        if q > 0:
            result[product].append(Order(product, price, q))

    def _sell(self, result: Dict[str, List[Order]], state: TradingState, product: str, price: int, qty: int) -> None:
        q = min(qty, max(0, self.OPTION_LIMIT + int(state.position.get(product, 0))))
        if q > 0:
            result[product].append(Order(product, price, -q))

    def _buy_ve(self, result: Dict[str, List[Order]], state: TradingState, price: int, qty: int) -> None:
        q = min(qty, max(0, self.VE_LIMIT - int(state.position.get("VELVETFRUIT_EXTRACT", 0))))
        if q > 0:
            result["VELVETFRUIT_EXTRACT"].append(Order("VELVETFRUIT_EXTRACT", price, q))

    def _sell_ve(self, result: Dict[str, List[Order]], state: TradingState, price: int, qty: int) -> None:
        q = min(qty, max(0, self.VE_LIMIT + int(state.position.get("VELVETFRUIT_EXTRACT", 0))))
        if q > 0:
            result["VELVETFRUIT_EXTRACT"].append(Order("VELVETFRUIT_EXTRACT", price, -q))

    def _own_fill_notes(self, state: TradingState) -> List[str]:
        notes: List[str] = []
        for product in self.PRODUCTS:
            for trade in state.own_trades.get(product, []):
                notes.append(f"{product}:{getattr(trade, 'buyer', '')}>{getattr(trade, 'seller', '')}@{trade.price}x{trade.quantity}")
        return notes
