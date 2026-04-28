from __future__ import annotations

"""
Strat51: first combined surface + player + inventory strategy.

Why we are testing this strategy:
- The data-supported direction is not a single trick. The strongest pieces are:
  * Surface residual: buy VEV_5400, sell VEV_5300.
  * Player intelligence: avoid Mark14/Mark01 toxicity, fade Mark38, use Mark55
    as VE liquidity, avoid selling to Mark67 on VE.
  * Inventory discipline: do not let one low-capacity edge block better trades.
- Strat51 combines these ideas conservatively. It is not intended as the final
  answer; it is the first integrated candidate whose logs should tell us which
  modules deserve more size.

How it works:
- Trades the core surface residual:
  * buy VEV_5400 when cheap;
  * sell VEV_5300 when rich.
- Adds a small VE module:
  * buy VE from noisy/liquidity sellers if price is favorable;
  * avoid selling VE when Mark67 is a recent buyer.
- Applies player filters:
  * do not sell a product when Mark14/Mark01 are recent buyers;
  * do not buy a product when Mark14/Mark01 are recent sellers;
  * increase size when Mark38 is the recent counterflow.
- Applies a soft delta band using VE as hedge only when aggregate delta is too
  large.

What the logs should reveal:
- Whether combining modules improves over pure surface probes.
- Whether player filters help or over-filter.
- Whether the delta band reduces terminal inventory without killing edge.
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
    TOXIC = {"Mark 14", "Mark 01"}
    DELTA = {"VEV_5300": 0.37, "VEV_5400": 0.17, "VELVETFRUIT_EXTRACT": 1.0}
    DELTA_BAND = 80.0

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        timestamp = int(getattr(state, "timestamp", 0))
        flow = self._recent_flow(state)
        notes: List[str] = []

        self._trade_surface_leg(result, state, flow, notes, "VEV_5400", "BUY", residual=1.80, threshold=0.75)
        self._trade_surface_leg(result, state, flow, notes, "VEV_5300", "SELL", residual=-1.54, threshold=0.75)
        self._trade_ve_liquidity(result, state, flow, notes)
        self._hedge_delta_band(result, state, notes)

        fills = self._own_fill_notes(state)
        if notes or fills or timestamp % 5000 == 0:
            print(f"STRAT51_SURFACE_PLAYER_INVENTORY|t={timestamp}|signals={';'.join(notes[:24])}|fills={';'.join(fills[:24])}")
        return result, 0, json.dumps({"strategy": "STRAT51_SURFACE_PLAYER_INVENTORY", "t": timestamp, "signals": notes[:24]}, separators=(",", ":"))

    def _trade_surface_leg(
        self,
        result: Dict[str, List[Order]],
        state: TradingState,
        flow: Dict[str, Dict[str, bool]],
        notes: List[str],
        product: str,
        side: str,
        residual: float,
        threshold: float,
    ) -> None:
        snap = self._snapshot(state.order_depths.get(product))
        if snap is None:
            return
        pos = int(state.position.get(product, 0))
        fair = snap["mid"] + residual
        if side == "BUY":
            edge = fair - snap["ask"]
            if edge < threshold or flow[product]["toxic_seller"]:
                return
            qty = 8 + (8 if flow[product]["fadeable_seller"] else 0)
            if abs(pos) > 180:
                qty = max(2, qty // 2)
            q = min(qty, max(0, self.OPTION_LIMIT - pos))
            if q > 0:
                result[product].append(Order(product, int(snap["ask"]), q))
                notes.append(f"{product}:BUY:edge={edge:.2f}:q={q}")
        else:
            edge = snap["bid"] - fair
            if edge < threshold or flow[product]["toxic_buyer"]:
                return
            qty = 8 + (8 if flow[product]["fadeable_buyer"] else 0)
            if abs(pos) > 180:
                qty = max(2, qty // 2)
            q = min(qty, max(0, self.OPTION_LIMIT + pos))
            if q > 0:
                result[product].append(Order(product, int(snap["bid"]), -q))
                notes.append(f"{product}:SELL:edge={edge:.2f}:q={q}")

    def _trade_ve_liquidity(self, result: Dict[str, List[Order]], state: TradingState, flow: Dict[str, Dict[str, bool]], notes: List[str]) -> None:
        product = "VELVETFRUIT_EXTRACT"
        snap = self._snapshot(state.order_depths.get(product))
        if snap is None:
            return
        pos = int(state.position.get(product, 0))
        # VE is not the main alpha here. This small module keeps the known Mark55
        # liquidity edge and blocks selling when Mark67 is buying.
        recent = state.market_trades.get(product, [])[-8:]
        mark55_seller = any(getattr(t, "seller", "") == "Mark 55" for t in recent)
        mark67_buyer = any(getattr(t, "buyer", "") == "Mark 67" for t in recent)
        if mark55_seller and self.VE_LIMIT - pos > 0:
            q = min(6, self.VE_LIMIT - pos)
            result[product].append(Order(product, int(snap["bid"] + 1), q))
            notes.append(f"VE:BUY_MARK55_SOURCE:q={q}")
        if not mark67_buyer and pos > 80 and self.VE_LIMIT + pos > 0:
            q = min(6, self.VE_LIMIT + pos)
            result[product].append(Order(product, int(snap["ask"] - 1), -q))
            notes.append(f"VE:SOFT_UNLOAD:q={q}")

    def _hedge_delta_band(self, result: Dict[str, List[Order]], state: TradingState, notes: List[str]) -> None:
        snap = self._snapshot(state.order_depths.get("VELVETFRUIT_EXTRACT"))
        if snap is None:
            return
        net_delta = sum(float(state.position.get(product, 0)) * delta for product, delta in self.DELTA.items())
        ve_pos = int(state.position.get("VELVETFRUIT_EXTRACT", 0))
        if net_delta > self.DELTA_BAND:
            q = min(12, max(0, self.VE_LIMIT + ve_pos), int(net_delta - self.DELTA_BAND))
            if q > 0:
                result["VELVETFRUIT_EXTRACT"].append(Order("VELVETFRUIT_EXTRACT", int(snap["bid"]), -q))
                notes.append(f"VE:HEDGE_SELL:delta={net_delta:.1f}:q={q}")
        elif net_delta < -self.DELTA_BAND:
            q = min(12, max(0, self.VE_LIMIT - ve_pos), int(-self.DELTA_BAND - net_delta))
            if q > 0:
                result["VELVETFRUIT_EXTRACT"].append(Order("VELVETFRUIT_EXTRACT", int(snap["ask"]), q))
                notes.append(f"VE:HEDGE_BUY:delta={net_delta:.1f}:q={q}")

    def _recent_flow(self, state: TradingState) -> Dict[str, Dict[str, bool]]:
        products = ["VEV_5300", "VEV_5400"]
        out = {product: {"toxic_buyer": False, "toxic_seller": False, "fadeable_buyer": False, "fadeable_seller": False} for product in products}
        for product in products:
            for trade in state.market_trades.get(product, [])[-8:]:
                buyer = getattr(trade, "buyer", "")
                seller = getattr(trade, "seller", "")
                if buyer in self.TOXIC:
                    out[product]["toxic_buyer"] = True
                if seller in self.TOXIC:
                    out[product]["toxic_seller"] = True
                if buyer == "Mark 38":
                    out[product]["fadeable_buyer"] = True
                if seller == "Mark 38":
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
        for product in ["VEV_5300", "VEV_5400", "VELVETFRUIT_EXTRACT"]:
            for trade in state.own_trades.get(product, []):
                notes.append(f"{product}:{getattr(trade, 'buyer', '')}>{getattr(trade, 'seller', '')}@{trade.price}x{trade.quantity}")
        return notes