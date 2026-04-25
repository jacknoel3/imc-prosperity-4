from __future__ import annotations

"""
Strategy 15A: MAX GAMMA HARVEST.

Core thesis from EDA (REPORT.md sec 4, 9, Q1):
    - Mean ATM IV - RV gap = -17.77%   -> implied vol is structurally CHEAP.
    - Per-unit 3-day gamma scalp PnL: VEV_5200=29.18, VEV_5300=28.92,
      VEV_5400=22.48, VEV_5500=11.07.
    - Voucher trade prices sit -0.55 to -0.96 ticks BELOW BS fair value
      on the active ATM strikes -> we are buying option cheaper than fair.
    - VEV_6000 / VEV_6500 trade at mid=0 (ask=1, bid=0), BS~=0.5
      -> selling the ask is a free 0.5-tick edge per unit, P(ITM)~0.3% at TTE=5.

Design:
    - Build the maximum long-gamma book on VEV_{5200..5500} - up to FULL 300 limit
      each, entering only on passive bids placed UNDER current BS fair value.
    - Permanently short the floor strikes 6000/6500 at ask=1 up to FULL 300.
    - Hedge the resulting net delta in VELVETFRUIT_EXTRACT continuously
      (rehedge gap > 8 units), passively only - VE spread is 5 ticks so taker
      hedging would cost 2.5 ticks and destroy the edge.
    - Keep a tight HYDROGEL_PACK fixed-FV market maker at 10000 to add stable carry.

Why prior in-house strategies under-perform:
    Lucas best = +8417 PnL. Top leaderboard ~ +154k.
    The historical 4-strike gamma scalp alone, at 300 units each over 2 days,
    sums to 4 * (29+29+22+11)/3 * 2 * 300 ~ 73k (rough), and that ignores
    OTM carry, MM PnL, and sub-tick passive edge from buying below BS.
    The gap is SIZE: prior strategies cap voucher inventory at 30-150,
    not 300. We push to the exchange limit.
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


class _OM:
    """Position-aware order batcher. Cumulative buy/sell sizes are tracked so we
    never violate exchange-side aggregated-quantity limits (would reject the whole
    side). Pattern from strat14."""
    def __init__(self, state: TradingState, limits: Dict[str, int],
                 result: Dict[str, List[Order]]) -> None:
        self.limits = limits
        self.result = result
        self.start = {p: int(state.position.get(p, 0)) for p in limits}
        self.exp = dict(self.start)
        self.bsent = {p: 0 for p in limits}
        self.ssent = {p: 0 for p in limits}

    def add(self, p: str, px: int, qty: int) -> int:
        if qty == 0 or p not in self.limits:
            return 0
        L = self.limits[p]
        s = self.start[p]
        if qty > 0:
            cap = max(0, L - s - self.bsent[p])
            placed = min(int(qty), cap)
            if placed <= 0:
                return 0
            self.bsent[p] += placed
        else:
            cap = max(0, L + s - self.ssent[p])
            placed = -min(int(-qty), cap)
            if placed >= 0:
                return 0
            self.ssent[p] += -placed
        self.exp[p] = self.exp.get(p, s) + placed
        self.result.setdefault(p, []).append(Order(p, int(px), int(placed)))
        return abs(placed)

    def pos(self, p: str) -> int:
        return self.exp.get(p, self.start.get(p, 0))


class Trader:
    HP = "HYDROGEL_PACK"
    VE = "VELVETFRUIT_EXTRACT"

    LONG_GAMMA = ["VEV_5200", "VEV_5300", "VEV_5400", "VEV_5500"]
    SHORT_FLOOR = ["VEV_6000", "VEV_6500"]
    SKIP = ["VEV_4000", "VEV_4500", "VEV_5000", "VEV_5100"]

    LIMITS = {
        HP: 200, VE: 200,
        "VEV_4000": 300, "VEV_4500": 300, "VEV_5000": 300, "VEV_5100": 300,
        "VEV_5200": 300, "VEV_5300": 300, "VEV_5400": 300, "VEV_5500": 300,
        "VEV_6000": 300, "VEV_6500": 300,
    }

    HGP_FV = 10000
    VE_FV = 5250
    SIGMA = 0.342
    INITIAL_TTE_DAYS = 5.0

    # Strike -> hard cap (long for gamma, short for floor).
    GAMMA_CAP = 280     # leave 20 slack so a take fill cannot reject the side
    FLOOR_SHORT_CAP = 280

    # Edge thresholds (in voucher ticks) for entering passive long gamma.
    # We only POST a bid below market when our bid sits at or below bs_fair - margin.
    GAMMA_EDGE_PASSIVE = 0.40  # we want to buy at fair - 0.4 minimum
    GAMMA_EDGE_TAKE = 1.50     # cross the spread only when ask is mispriced by > 1.5

    # Delta hedge gap (units of VE) before we add a passive hedge order.
    HEDGE_GAP = 8

    def bid(self) -> int:
        return 0

    # ------------------------------------------------------------------ run
    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {p: [] for p in state.order_depths}
        om = _OM(state, self.LIMITS, result)
        cache = self._load(state.traderData)

        ts = int(getattr(state, "timestamp", 0))
        self._track_day(cache, ts)
        t_years = self._tte_years(cache, ts)

        # 1. Floor short - free carry.
        for p in self.SHORT_FLOOR:
            self._sell_floor(p, state.order_depths.get(p), om)

        # 2. Long gamma harvest on liquid ATM strikes.
        ve_mid = self._mid(state.order_depths.get(self.VE))
        if ve_mid is not None and t_years > 0:
            for p in self.LONG_GAMMA:
                self._buy_gamma(p, state.order_depths.get(p), ve_mid, t_years, om)

        # 3. Delta hedge in underlying (passive only).
        if ve_mid is not None and t_years > 0:
            self._hedge_delta(state.order_depths.get(self.VE), ve_mid, t_years, om)

        # 4. HGP fixed-FV market maker.
        self._mm_hgp(state.order_depths.get(self.HP), om)

        cache["last_ts"] = ts
        return result, 0, json.dumps(cache, separators=(",", ":"))

    # ------------------------------------------------------------ floor
    def _sell_floor(self, product: str, depth: Optional[OrderDepth], om: _OM) -> None:
        if depth is None:
            return
        bid, ask, _, _ = self._top(depth)
        if ask is None:
            return
        pos = om.pos(product)
        # Sell at ask=1 when ask <= 1 (always true historically). Edge per unit ~= 0.5
        # because BS fair ~ 0 with 5d TTE / sigma 0.34.
        if ask <= 1 and pos > -self.FLOOR_SHORT_CAP:
            qty = min(25, pos + self.FLOOR_SHORT_CAP)
            if qty > 0:
                om.add(product, 1, -qty)
        # Reduce risk for free if bid drops to 0 and we are very short.
        if bid is not None and bid <= 0 and pos < -self.FLOOR_SHORT_CAP + 30:
            om.add(product, 0, min(15, -pos))

    # ------------------------------------------------------------ gamma
    def _buy_gamma(self, product: str, depth: Optional[OrderDepth], ve_mid: float,
                   t_years: float, om: _OM) -> None:
        if depth is None:
            return
        bid, ask, _, _ = self._top(depth)
        if bid is None or ask is None:
            return
        K = float(product.split("_")[1])
        fair = self._bs(ve_mid, K, t_years)
        pos = om.pos(product)
        cap = self.GAMMA_CAP
        spread = ask - bid

        # Aggressive take: ask substantially below BS - chase it.
        if pos < cap and ask <= fair - self.GAMMA_EDGE_TAKE:
            take = min(20, cap - pos)
            om.add(product, ask, take)
            pos = om.pos(product)

        # Passive: post a bid 1 tick inside the spread (bid+1) only if that bid
        # is still <= fair - GAMMA_EDGE_PASSIVE. This is the main accumulation path.
        post_bid = bid + 1 if spread >= 2 else bid
        if pos < cap and post_bid <= fair - self.GAMMA_EDGE_PASSIVE:
            size = self._gamma_size(product, pos, cap)
            om.add(product, post_bid, min(size, cap - pos))

        # Light passive sell only if we accumulated way past target (defensive trim).
        if pos > cap * 0.92:
            post_ask = ask - 1 if spread >= 2 else ask
            if post_ask >= fair + 0.3:
                om.add(product, post_ask, -min(8, pos))

    def _gamma_size(self, product: str, pos: int, cap: int) -> int:
        # Larger size on the highest-PnL strikes (5200/5300 from EDA),
        # smaller on 5500 because per-unit scalp PnL is ~11 vs ~29.
        base = {"VEV_5200": 22, "VEV_5300": 22, "VEV_5400": 18, "VEV_5500": 12}.get(product, 16)
        # Taper as we approach the cap so a single fill cannot blow through.
        if pos > cap * 0.7:
            base = max(4, base // 2)
        return base

    # ------------------------------------------------------------ hedge
    def _hedge_delta(self, depth: Optional[OrderDepth], ve_mid: float,
                     t_years: float, om: _OM) -> None:
        if depth is None:
            return
        bid, ask, _, _ = self._top(depth)
        if bid is None or ask is None:
            return
        # Delta from voucher inventory. Floor strikes have negligible delta.
        d52 = self._delta(ve_mid, 5200.0, t_years) * om.pos("VEV_5200")
        d53 = self._delta(ve_mid, 5300.0, t_years) * om.pos("VEV_5300")
        d54 = self._delta(ve_mid, 5400.0, t_years) * om.pos("VEV_5400")
        d55 = self._delta(ve_mid, 5500.0, t_years) * om.pos("VEV_5500")
        target = -int(round(d52 + d53 + d54 + d55))

        # Cap target inside VE limit (200) so we keep some VE capacity for any
        # taker hedge needed if delta swings.
        target = max(-180, min(180, target))
        pos = om.pos(self.VE)
        gap = target - pos
        spread = ask - bid

        # Passive hedge: only post when the gap exceeds HEDGE_GAP units.
        if gap >= self.HEDGE_GAP:
            px = bid + 1 if spread >= 2 else bid
            om.add(self.VE, px, min(25, gap, 200 - pos))
        elif gap <= -self.HEDGE_GAP:
            px = ask - 1 if spread >= 2 else ask
            om.add(self.VE, px, -min(25, -gap, 200 + pos))
        else:
            # If gap is tiny, run a very small fixed-FV MM in VE for added carry.
            fair = self.VE_FV
            if pos < 50 and (bid + 1) <= fair - 1:
                om.add(self.VE, bid + 1, min(8, 50 - pos))
            if pos > -50 and (ask - 1) >= fair + 1:
                om.add(self.VE, ask - 1, -min(8, 50 + pos))

    # ------------------------------------------------------------ HGP
    def _mm_hgp(self, depth: Optional[OrderDepth], om: _OM) -> None:
        if depth is None:
            return
        bid, ask, bv, av = self._top(depth)
        if bid is None or ask is None:
            return
        pos = om.pos(self.HP)
        L = 180
        spread = ask - bid

        # Aggressive take when book is mispriced through FV.
        if ask < self.HGP_FV - 2 and pos < L:
            om.add(self.HP, ask, min(30, L - pos))
            pos = om.pos(self.HP)
        if bid > self.HGP_FV + 2 and pos > -L:
            om.add(self.HP, bid, -min(30, L + pos))
            pos = om.pos(self.HP)

        # Passive MM 1 tick inside the spread.
        post_bid = bid + 1 if spread >= 3 else bid
        post_ask = ask - 1 if spread >= 3 else ask

        # Contrarian OBI tilt: heavy bid book -> price about to FALL -> drop our bid.
        total = bv + av
        obi = (bv - av) / total if total > 0 else 0.0
        allow_bid = obi <= 0.15
        allow_ask = obi >= -0.15

        size = 22
        if abs(pos) > L * 0.6:
            size = 12

        fair = self.HGP_FV - 0.05 * pos
        if allow_bid and pos < L and post_bid <= fair - 1:
            om.add(self.HP, post_bid, min(size, L - pos))
        if allow_ask and pos > -L and post_ask >= fair + 1:
            om.add(self.HP, post_ask, -min(size, L + pos))

    # ------------------------------------------------------------ utils
    def _tte_years(self, cache: Dict, ts: int) -> float:
        # 1 historical day = 1_000_000 ticks. Day index increments when ts wraps.
        d_used = float(cache.get("day_idx", 0)) + ts / 1_000_000.0
        days_left = max(0.5, self.INITIAL_TTE_DAYS - d_used)
        return days_left / 365.0

    def _track_day(self, cache: Dict, ts: int) -> None:
        last = cache.get("last_ts")
        idx = int(cache.get("day_idx", 0))
        if isinstance(last, int) and ts < last:
            idx += 1
        cache["day_idx"] = idx

    def _bs(self, s: float, k: float, t: float) -> float:
        if t <= 0:
            return max(0.0, s - k)
        sig = self.SIGMA
        sqrt_t = math.sqrt(t)
        d1 = (math.log(max(1e-9, s / k)) + 0.5 * sig * sig * t) / (sig * sqrt_t)
        d2 = d1 - sig * sqrt_t
        return s * _norm_cdf(d1) - k * _norm_cdf(d2)

    def _delta(self, s: float, k: float, t: float) -> float:
        if t <= 0:
            return 1.0 if s > k else 0.0
        sig = self.SIGMA
        d1 = (math.log(max(1e-9, s / k)) + 0.5 * sig * sig * t) / (sig * math.sqrt(t))
        return _norm_cdf(d1)

    def _top(self, depth: OrderDepth) -> Tuple[Optional[int], Optional[int], int, int]:
        if depth is None:
            return None, None, 0, 0
        b = max(depth.buy_orders) if depth.buy_orders else None
        a = min(depth.sell_orders) if depth.sell_orders else None
        bv = int(depth.buy_orders[b]) if b is not None else 0
        av = -int(depth.sell_orders[a]) if a is not None else 0
        return b, a, bv, av

    def _mid(self, depth: Optional[OrderDepth]) -> Optional[float]:
        b, a, _, _ = self._top(depth)
        if b is None or a is None:
            return None
        return (b + a) / 2.0

    def _load(self, td: str) -> Dict:
        if not td:
            return {}
        try:
            d = json.loads(td)
            return d if isinstance(d, dict) else {}
        except (TypeError, json.JSONDecodeError):
            return {}
