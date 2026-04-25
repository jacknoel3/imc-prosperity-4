from __future__ import annotations

"""
Strategy 18 - LUCAS REFINED.

Diagnosis from strat 15/16/17 backtest postmortem:
    - HGP lost -3249 in all three because we posted passive at best_bid+1 /
      best_ask-1 (chasing market) instead of FIXED at FV +/- edge.
      Lucas's passive bid 9998 CROSSES the book 62% of the time (de-facto take
      below FV); his ask 10002 crosses 21% (de-facto take above FV). That's
      the source of his +8585 PnL on HGP.
    - VEV_6000/6500 floor short never filled: PnL=0 across all my strats.
      Bots post bid=0/ask=1 but do not lift our offer at 1. Skip.
    - VEV_5300/5400 long-gamma at 280 units lost ~1500: too much directional
      delta exposure for a 1000-tick replay where realized vol is small.
    - VELVETFRUIT_EXTRACT FV is NOT 5250 - day 2 actual mean is 5262 with
      upward drift. Fixed FV=5250 made Lucas dump to -200 and lose -111.

Design priorities:
    1. Replicate Lucas's HGP MM EXACTLY (the +8585 engine):
       fixed FV=10000, edge=2, take all asks below FV / bids above FV,
       passive at FV +/- edge with size scaled by remaining capacity.
       NO OBI tilt - it suppressed orders during one-way moves.
    2. Fix VEV with ROLLING mid as fair value:
       EMA of (bid+ask)/2 with alpha 0.05; edge=2 to avoid immediate crosses.
       Modest size (40-50). Track in traderData.
    3. Tiny VEV_5400 passive bid at int(bs_fair - 1.0), max 25 units.
       Cap stop-loss: never go above 25 long, never short.
    4. Tiny VEV_5300 same pattern, max 15 units.
    5. SKIP VEV_6000/6500/5000/5100/5500/4000/4500 entirely.
       Empirical PnL = 0 or negative on every prior backtest.
    6. NO delta hedge from voucher to VE - VE position stays close to flat
       so MM PnL on VE is not contaminated by directional hedge orders.
"""

import json
import math
from typing import Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ModuleNotFoundError:
    from prosperity3bt.datamodel import Order, OrderDepth, TradingState


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


class Trader:
    HP = "HYDROGEL_PACK"
    VE = "VELVETFRUIT_EXTRACT"

    HGP_LIMIT = 200
    HGP_FV = 10000
    HGP_EDGE = 2

    VE_LIMIT = 200
    VE_EDGE = 2
    VE_EMA_ALPHA = 0.05
    VE_FV_FALLBACK = 5260.0

    OPT_LIMIT = 300
    SIGMA = 0.342
    TTE_START_DAYS = 5

    V5400_MAX = 25
    V5300_MAX = 15
    V5400_BUFFER = 1.0
    V5300_BUFFER = 1.0

    def bid(self) -> int:
        return 1

    # ============================================================ pricing
    def _bs_call(self, S: float, K: float, T: float) -> float:
        if T <= 0 or S <= 0:
            return max(0.0, S - K)
        sig = self.SIGMA
        sqrt_t = math.sqrt(T)
        d1 = (math.log(max(S, 0.001) / max(K, 0.001)) + 0.5 * sig * sig * T) / (sig * sqrt_t)
        d2 = d1 - sig * sqrt_t
        return S * _norm_cdf(d1) - K * _norm_cdf(d2)

    def _tte(self, timestamp: int, day_idx: int) -> float:
        frac = timestamp / 1_000_000.0
        days_used = day_idx + frac
        return max(0.001, (self.TTE_START_DAYS - days_used) / 365.0)

    def _mid(self, depth: Optional[OrderDepth]) -> Optional[float]:
        if depth is None:
            return None
        b = max(depth.buy_orders) if depth.buy_orders else None
        a = min(depth.sell_orders) if depth.sell_orders else None
        if b is None or a is None:
            return None
        return (b + a) / 2.0

    # ============================================================ HGP MM
    def _trade_hgp(self, depth: OrderDepth, pos: int) -> List[Order]:
        orders: List[Order] = []
        if depth is None:
            return orders
        L = self.HGP_LIMIT
        fv = self.HGP_FV
        edge = self.HGP_EDGE

        buy_cap = L - pos
        sell_cap = L + pos

        asks_sorted = sorted(depth.sell_orders.items())
        bids_sorted = sorted(depth.buy_orders.items(), reverse=True)

        for ask_px, ask_vol in asks_sorted:
            if ask_px < fv and buy_cap > 0:
                qty = min(-ask_vol, buy_cap)
                if qty > 0:
                    orders.append(Order(self.HP, int(ask_px), int(qty)))
                    pos += qty
                    buy_cap -= qty
                    sell_cap += qty

        for bid_px, bid_vol in bids_sorted:
            if bid_px > fv and sell_cap > 0:
                qty = min(bid_vol, sell_cap)
                if qty > 0:
                    orders.append(Order(self.HP, int(bid_px), -int(qty)))
                    pos -= qty
                    sell_cap -= qty
                    buy_cap += qty

        best_bid = bids_sorted[0][0] if bids_sorted else (fv - edge - 1)
        best_ask = asks_sorted[0][0] if asks_sorted else (fv + edge + 1)

        scale = max(40, int(max(0.3, 1.0 - abs(pos / L) * 0.7) * L))

        pass_bid = fv - edge
        pass_ask = fv + edge

        if buy_cap > 0 and pass_bid < best_ask:
            orders.append(Order(self.HP, pass_bid, min(scale, buy_cap)))
        if sell_cap > 0 and pass_ask > best_bid:
            orders.append(Order(self.HP, pass_ask, -min(scale, sell_cap)))

        return orders

    # ============================================================ VE MM
    def _trade_ve(self, depth: OrderDepth, pos: int, ve_fv: float) -> List[Order]:
        orders: List[Order] = []
        if depth is None:
            return orders
        L = self.VE_LIMIT
        edge = self.VE_EDGE
        fv = int(round(ve_fv))

        buy_cap = L - pos
        sell_cap = L + pos

        asks_sorted = sorted(depth.sell_orders.items())
        bids_sorted = sorted(depth.buy_orders.items(), reverse=True)

        for ask_px, ask_vol in asks_sorted:
            if ask_px < fv - 1 and buy_cap > 0:
                qty = min(-ask_vol, buy_cap, 30)
                if qty > 0:
                    orders.append(Order(self.VE, int(ask_px), int(qty)))
                    pos += qty
                    buy_cap -= qty
                    sell_cap += qty

        for bid_px, bid_vol in bids_sorted:
            if bid_px > fv + 1 and sell_cap > 0:
                qty = min(bid_vol, sell_cap, 30)
                if qty > 0:
                    orders.append(Order(self.VE, int(bid_px), -int(qty)))
                    pos -= qty
                    sell_cap -= qty
                    buy_cap += qty

        best_bid = bids_sorted[0][0] if bids_sorted else (fv - edge - 1)
        best_ask = asks_sorted[0][0] if asks_sorted else (fv + edge + 1)

        scale = min(50, max(20, int(max(0.3, 1.0 - abs(pos / L) * 0.7) * L)))

        pass_bid = fv - edge
        pass_ask = fv + edge

        if buy_cap > 0 and pass_bid < best_ask:
            orders.append(Order(self.VE, pass_bid, min(scale, buy_cap)))
        if sell_cap > 0 and pass_ask > best_bid:
            orders.append(Order(self.VE, pass_ask, -min(scale, sell_cap)))

        return orders

    # ============================================================ vouchers
    def _trade_5400(self, depth: OrderDepth, pos: int, ve_mid: float, tte: float) -> List[Order]:
        orders: List[Order] = []
        if depth is None or ve_mid is None or tte <= 0:
            return orders
        cap = self.V5400_MAX
        if pos >= cap:
            return orders
        bs = self._bs_call(ve_mid, 5400.0, tte)
        bid_px = int(bs - self.V5400_BUFFER)
        asks_sorted = sorted(depth.sell_orders.items())
        best_ask = asks_sorted[0][0] if asks_sorted else None
        if best_ask is not None and bid_px >= best_ask:
            return orders
        size = min(8, cap - pos)
        if size > 0:
            orders.append(Order("VEV_5400", bid_px, int(size)))
        return orders

    def _trade_5300(self, depth: OrderDepth, pos: int, ve_mid: float, tte: float) -> List[Order]:
        orders: List[Order] = []
        if depth is None or ve_mid is None or tte <= 0:
            return orders
        cap = self.V5300_MAX
        if pos >= cap:
            return orders
        bs = self._bs_call(ve_mid, 5300.0, tte)
        bid_px = int(bs - self.V5300_BUFFER)
        asks_sorted = sorted(depth.sell_orders.items())
        best_ask = asks_sorted[0][0] if asks_sorted else None
        if best_ask is not None and bid_px >= best_ask:
            return orders
        size = min(5, cap - pos)
        if size > 0:
            orders.append(Order("VEV_5300", bid_px, int(size)))
        return orders

    # ============================================================ run
    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        try:
            cache: Dict = json.loads(state.traderData) if state.traderData else {}
        except (TypeError, json.JSONDecodeError):
            cache = {}

        ts = int(getattr(state, "timestamp", 0))
        last_ts = cache.get("last_ts")
        day_idx = int(cache.get("day_idx", 0))
        if isinstance(last_ts, int) and ts < last_ts:
            day_idx += 1
        cache["day_idx"] = day_idx
        cache["last_ts"] = ts

        result: Dict[str, List[Order]] = {}
        positions = state.position

        ve_depth = state.order_depths.get(self.VE)
        ve_mid = self._mid(ve_depth)
        ve_fv = float(cache.get("ve_fv", self.VE_FV_FALLBACK))
        if ve_mid is not None:
            ve_fv = self.VE_EMA_ALPHA * ve_mid + (1 - self.VE_EMA_ALPHA) * ve_fv
        cache["ve_fv"] = ve_fv

        tte = self._tte(ts, day_idx)

        if self.HP in state.order_depths:
            result[self.HP] = self._trade_hgp(state.order_depths[self.HP],
                                              positions.get(self.HP, 0))

        if ve_depth is not None:
            result[self.VE] = self._trade_ve(ve_depth, positions.get(self.VE, 0), ve_fv)

        if "VEV_5400" in state.order_depths and ve_mid is not None:
            orders_5400 = self._trade_5400(
                state.order_depths["VEV_5400"], positions.get("VEV_5400", 0), ve_mid, tte
            )
            if orders_5400:
                result["VEV_5400"] = orders_5400

        if "VEV_5300" in state.order_depths and ve_mid is not None:
            orders_5300 = self._trade_5300(
                state.order_depths["VEV_5300"], positions.get("VEV_5300", 0), ve_mid, tte
            )
            if orders_5300:
                result["VEV_5300"] = orders_5300

        return result, 0, json.dumps(cache, separators=(",", ":"))