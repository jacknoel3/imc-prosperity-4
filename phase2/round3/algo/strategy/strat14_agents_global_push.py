from __future__ import annotations

"""
Round 3 production candidate from multi-agent synthesis.

Design goals:
- Passive-first execution (avoid taker toxicity seen in probe strategies).
- Stable carry from HYDROGEL + selective voucher edge on 5200/5300/5400.
- Conservative deep OTM carry (6000/6500) with strict caps.
- Delta-aware VELVET hedge bias, mostly passive.
"""

import json
import math
from typing import Any, Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ModuleNotFoundError:
    from prosperity3bt.datamodel import Order, OrderDepth, TradingState


class OrderManager:
    def __init__(self, state: TradingState, limits: Dict[str, int], result: Dict[str, List[Order]]) -> None:
        self.limits = limits
        self.result = result
        self.start_pos = {p: int(state.position.get(p, 0)) for p in limits}
        self.expected_pos = dict(self.start_pos)
        self.buy_sent = {p: 0 for p in limits}
        self.sell_sent = {p: 0 for p in limits}

    def add(self, product: str, price: int, qty: int) -> int:
        if qty == 0 or product not in self.limits:
            return 0
        start = self.start_pos.get(product, 0)
        limit = self.limits[product]
        if qty > 0:
            allowed = max(0, limit - start - self.buy_sent[product])
            placed = min(int(qty), allowed)
            if placed <= 0:
                return 0
            self.buy_sent[product] += placed
        else:
            allowed = max(0, limit + start - self.sell_sent[product])
            placed = -min(int(-qty), allowed)
            if placed >= 0:
                return 0
            self.sell_sent[product] += -placed
        self.expected_pos[product] = self.expected_pos.get(product, start) + placed
        self.result.setdefault(product, []).append(Order(product, int(price), int(placed)))
        return abs(placed)

    def pos(self, product: str) -> int:
        return self.expected_pos.get(product, self.start_pos.get(product, 0))


class Trader:
    HP = "HYDROGEL_PACK"
    VE = "VELVETFRUIT_EXTRACT"
    FOCUS = ["VEV_5200", "VEV_5300", "VEV_5400"]
    FLOOR = ["VEV_6000", "VEV_6500"]
    OTHERS = ["VEV_4000", "VEV_4500", "VEV_5000", "VEV_5100", "VEV_5500"]
    PRODUCTS = [HP, VE] + OTHERS + FOCUS + FLOOR

    LIMITS = {
        HP: 200,
        VE: 200,
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

    SOFT = {
        HP: 120,
        VE: 120,
        "VEV_4000": 30,
        "VEV_4500": 30,
        "VEV_5000": 50,
        "VEV_5100": 70,
        "VEV_5200": 120,
        "VEV_5300": 130,
        "VEV_5400": 150,
        "VEV_5500": 80,
        "VEV_6000": 110,
        "VEV_6500": 110,
    }

    # Product anchors from EDA findings.
    HGP_FV = 10000.0
    VE_FV = 5250.0
    VE_SIGMA = 0.342

    # Initial R3 TTE is 5 days; we detect day roll by timestamp reset.
    INITIAL_TTE_DAYS = 5.0

    # Deep OTM floor logic is intentionally conservative.
    FLOOR_ASK_PRICE = 1
    FLOOR_MAX_SHORT = 90

    def bid(self) -> int:
        # Safe to keep for all rounds; ignored outside Round 2.
        return 0

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {p: [] for p in state.order_depths}
        om = OrderManager(state, self.LIMITS, result)
        cache = self._load_cache(state.traderData)

        timestamp = int(getattr(state, "timestamp", 0))
        self._update_day_counter(cache, timestamp)

        self._trade_hydrogel(state.order_depths.get(self.HP), om)

        ve_depth = state.order_depths.get(self.VE)
        ve_mid = self._mid(ve_depth)

        if ve_mid is not None:
            for product in self.FOCUS:
                depth = state.order_depths.get(product)
                if depth is not None:
                    self._trade_focus_voucher(product, depth, ve_mid, cache, timestamp, om)

        for product in self.FLOOR:
            depth = state.order_depths.get(product)
            if depth is not None:
                self._trade_floor(product, depth, om)

        self._trade_velvet_hedge(ve_depth, ve_mid, om)

        cache["last_ts"] = timestamp
        return result, 0, json.dumps(cache, separators=(",", ":"))

    def _trade_hydrogel(self, depth: Optional[OrderDepth], om: OrderManager) -> None:
        bid, ask, bid_vol, ask_vol = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return

        pos = om.pos(self.HP)
        soft = self.SOFT[self.HP]
        spread = ask - bid

        total = bid_vol + ask_vol
        obi = ((bid_vol - ask_vol) / total) if total > 0 else 0.0

        base_bid = bid + 1 if spread >= 3 else bid
        base_ask = ask - 1 if spread >= 3 else ask

        # Contrarian OBI for HGP: positive OBI suppresses bid, negative suppresses ask.
        allow_bid = not (obi > 0.15)
        allow_ask = not (obi < -0.15)

        size = 24
        if abs(pos) > soft * 0.5:
            size = 14
        if abs(pos) > soft * 0.8:
            size = 8

        fair = self.HGP_FV - 0.02 * pos

        # Small passive take only for very favorable prices.
        if ask < fair - 2.0 and pos < soft:
            om.add(self.HP, ask, min(10, soft - pos))
            pos = om.pos(self.HP)
        if bid > fair + 2.0 and pos > -soft:
            om.add(self.HP, bid, -min(10, pos + soft))
            pos = om.pos(self.HP)

        if allow_bid and pos < soft and base_bid <= fair - 1.0:
            om.add(self.HP, base_bid, min(size, soft - pos))
        if allow_ask and pos > -soft and base_ask >= fair + 1.0:
            om.add(self.HP, base_ask, -min(size, pos + soft))

    def _trade_focus_voucher(
        self,
        product: str,
        depth: OrderDepth,
        ve_mid: float,
        cache: Dict[str, Any],
        timestamp: int,
        om: OrderManager,
    ) -> None:
        bid, ask, _, _ = self._best_bid_ask(depth)
        mid = self._mid(depth)
        if bid is None or ask is None or mid is None:
            return

        strike = float(product.split("_")[1])
        t_days_left = max(0.7, self.INITIAL_TTE_DAYS - float(cache.get("day_idx", 0)) - timestamp / 1_000_000.0)
        t_years = t_days_left / 365.0

        fair = self._bs_call(ve_mid, strike, t_years, self.VE_SIGMA)
        pos = om.pos(product)
        soft = self.SOFT[product]

        # Inventory penalty keeps us from accumulating stale long gamma blindly.
        fair -= 0.015 * pos

        edge = fair - mid
        spread = ask - bid
        min_edge_to_quote = 0.45 + 0.20 * max(0, spread - 1)

        buy_px = bid + 1 if spread >= 3 else bid
        sell_px = ask - 1 if spread >= 3 else ask

        base_size = 10 if product == "VEV_5200" else 12 if product == "VEV_5300" else 14
        if abs(pos) > soft * 0.55:
            base_size = max(4, base_size // 2)

        # Regime guard from prior failures: reduce new longs in mid-session repricing window.
        allow_new_longs = not (47000 <= timestamp <= 62000 and product in ("VEV_5200", "VEV_5300"))

        if edge > min_edge_to_quote and allow_new_longs and pos < soft:
            om.add(product, buy_px, min(base_size, soft - pos))
        if edge < -min_edge_to_quote and pos > -soft:
            om.add(product, sell_px, -min(base_size, pos + soft))

    def _trade_floor(self, product: str, depth: OrderDepth, om: OrderManager) -> None:
        bid, ask, _, _ = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return

        pos = om.pos(product)
        if ask <= self.FLOOR_ASK_PRICE and pos > -self.FLOOR_MAX_SHORT:
            om.add(product, self.FLOOR_ASK_PRICE, -min(12, pos + self.FLOOR_MAX_SHORT))

        # If over-short and bid=0, reduce risk for free.
        if pos < -self.FLOOR_MAX_SHORT + 20 and bid <= 0:
            om.add(product, 0, min(10, -pos))

    def _trade_velvet_hedge(self, depth: Optional[OrderDepth], ve_mid: Optional[float], om: OrderManager) -> None:
        bid, ask, _, _ = self._best_bid_ask(depth)
        if bid is None or ask is None or ve_mid is None:
            return

        pos = om.pos(self.VE)
        soft = self.SOFT[self.VE]

        # Approximate delta from focused voucher sleeve.
        d5200 = self._bs_delta(ve_mid, 5200.0, 4.0 / 365.0, self.VE_SIGMA)
        d5300 = self._bs_delta(ve_mid, 5300.0, 4.0 / 365.0, self.VE_SIGMA)
        d5400 = self._bs_delta(ve_mid, 5400.0, 4.0 / 365.0, self.VE_SIGMA)

        opt_delta = (
            d5200 * om.pos("VEV_5200")
            + d5300 * om.pos("VEV_5300")
            + d5400 * om.pos("VEV_5400")
        )
        target_ve = int(round(-opt_delta))
        gap = target_ve - pos

        spread = ask - bid
        buy_px = bid + 1 if spread >= 3 else bid
        sell_px = ask - 1 if spread >= 3 else ask

        # Contrarian OBI for VE as in research notes.
        bid_vol = int(depth.buy_orders[bid]) if bid in depth.buy_orders else 0
        ask_vol = -int(depth.sell_orders[ask]) if ask in depth.sell_orders else 0
        total = bid_vol + ask_vol
        obi = ((bid_vol - ask_vol) / total) if total > 0 else 0.0

        allow_bid = not (obi > 0.15)
        allow_ask = not (obi < -0.15)

        if gap > 12 and pos < soft and allow_bid:
            om.add(self.VE, buy_px, min(16, soft - pos, gap))
        elif gap < -12 and pos > -soft and allow_ask:
            om.add(self.VE, sell_px, -min(16, pos + soft, -gap))
        else:
            fair = self.VE_FV - 0.03 * pos
            if allow_bid and pos < soft and buy_px <= fair - 1.0:
                om.add(self.VE, buy_px, min(10, soft - pos))
            if allow_ask and pos > -soft and sell_px >= fair + 1.0:
                om.add(self.VE, sell_px, -min(10, pos + soft))

    def _update_day_counter(self, cache: Dict[str, Any], timestamp: int) -> None:
        last = cache.get("last_ts")
        day_idx = int(cache.get("day_idx", 0))
        if isinstance(last, int) and timestamp < last:
            day_idx += 1
        cache["day_idx"] = day_idx

    def _best_bid_ask(self, depth: Optional[OrderDepth]) -> Tuple[Optional[int], Optional[int], int, int]:
        if depth is None:
            return None, None, 0, 0
        bid = max(depth.buy_orders) if depth.buy_orders else None
        ask = min(depth.sell_orders) if depth.sell_orders else None
        bid_vol = int(depth.buy_orders[bid]) if bid is not None else 0
        ask_vol = -int(depth.sell_orders[ask]) if ask is not None else 0
        return bid, ask, bid_vol, ask_vol

    def _mid(self, depth: Optional[OrderDepth]) -> Optional[float]:
        bid, ask, _, _ = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2.0

    def _load_cache(self, trader_data: str) -> Dict[str, Any]:
        if not trader_data:
            return {}
        try:
            parsed = json.loads(trader_data)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, json.JSONDecodeError):
            return {}

    def _bs_call(self, s: float, k: float, t: float, sigma: float) -> float:
        if t <= 0:
            return max(0.0, s - k)
        sqrt_t = math.sqrt(t)
        d1 = (math.log(max(1e-9, s / k)) + 0.5 * sigma * sigma * t) / (sigma * sqrt_t)
        d2 = d1 - sigma * sqrt_t
        return s * self._norm_cdf(d1) - k * self._norm_cdf(d2)

    def _bs_delta(self, s: float, k: float, t: float, sigma: float) -> float:
        if t <= 0:
            return 1.0 if s > k else 0.0
        d1 = (math.log(max(1e-9, s / k)) + 0.5 * sigma * sigma * t) / (sigma * math.sqrt(t))
        return self._norm_cdf(d1)

    def _norm_cdf(self, x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
