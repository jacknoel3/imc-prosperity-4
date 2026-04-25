from __future__ import annotations

"""
Strategy 15B: IV REGIME SWITCH (EMA bands on ATM IV).

Core thesis from EDA (REPORT.md sec 9, 10, Q4, Q5):
    - Mean IV-RV gap = -17.77% but the gap MEAN-REVERTS:
        Q4: mean_gap_phi = 0.993, mean_half_life ~109 ticks.
    - Q5: ATM IV EMA crossover signals had hit rate up to 98.7% on
      n=3589 signals in EDA.
    - The most reliable ATM IV proxy strike is VEV_5200 (top_atm_proxy_fraction=0.518).

Design:
    - Maintain an EMA of ATM implied vol (using VEV_5200 mid as IV proxy).
    - When current IV < EMA - K_LOW * std  -> regime = "VOL CHEAP".
        * Long-gamma posture: aggressive passive bids on 5200/5300/5400/5500
          up to large but not full size, delta-hedge in VE.
        * Sell floor 6000/6500 at ask=1 always (free carry).
    - When current IV > EMA + K_HI * std  -> regime = "VOL RICH".
        * Short-premium posture: lift the offer of any voucher trading above
          BS by > 1 tick (mostly 5400/5500/6000/6500). Reduce / unwind long
          gamma positions passively.
    - When neutral -> hold positions, run passive 1-tick MM on each voucher
      around BS fair value.
    - HGP MM at fixed FV=10000 with contrarian OBI tilt (independent product).
    - Persist EMA state across iterations (kept tiny - far below 50k chars).

Why this can beat the 15A "always long-gamma" approach:
    Pure long-gamma loses when IV mean-reverts back toward RV (premium decay).
    Switching with IV bands captures both directions: long when cheap,
    short when rich. EDA hit rate of 98.7% is unusually high - even after
    halving for live conditions, this is a strong directional filter.
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
    def __init__(self, state, limits, result):
        self.limits = limits
        self.result = result
        self.start = {p: int(state.position.get(p, 0)) for p in limits}
        self.exp = dict(self.start)
        self.bsent = {p: 0 for p in limits}
        self.ssent = {p: 0 for p in limits}

    def add(self, p, px, qty):
        if qty == 0 or p not in self.limits:
            return 0
        L, s = self.limits[p], self.start[p]
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

    def pos(self, p):
        return self.exp.get(p, self.start.get(p, 0))


class Trader:
    HP = "HYDROGEL_PACK"
    VE = "VELVETFRUIT_EXTRACT"

    ATM = ["VEV_5200", "VEV_5300", "VEV_5400", "VEV_5500"]
    FLOOR = ["VEV_6000", "VEV_6500"]

    LIMITS = {
        HP: 200, VE: 200,
        "VEV_4000": 300, "VEV_4500": 300, "VEV_5000": 300, "VEV_5100": 300,
        "VEV_5200": 300, "VEV_5300": 300, "VEV_5400": 300, "VEV_5500": 300,
        "VEV_6000": 300, "VEV_6500": 300,
    }

    HGP_FV = 10000
    VE_FV = 5250
    SIGMA_PRIOR = 0.342
    INITIAL_TTE_DAYS = 5.0

    EMA_ALPHA = 0.02       # ~50-tick effective half-life on per-tick IV samples
    STD_ALPHA = 0.01       # slower std EMA
    K_LOW = 1.0            # IV < EMA - K_LOW * std -> CHEAP regime
    K_HI = 1.0             # IV > EMA + K_HI * std  -> RICH regime
    MIN_SAMPLES = 80       # warm-up before signals fire

    LONG_CAP = 250
    SHORT_CAP = 250
    FLOOR_SHORT_CAP = 290

    def bid(self) -> int:
        return 0

    # ------------------------------------------------------------------ run
    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {p: [] for p in state.order_depths}
        om = _OM(state, self.LIMITS, result)
        cache = self._load(state.traderData)

        ts = int(getattr(state, "timestamp", 0))
        self._track_day(cache, ts)
        t_years = self._tte_years(cache, ts)

        ve_mid = self._mid(state.order_depths.get(self.VE))

        # 1. Update IV EMA from VEV_5200 mid.
        regime = "NEUTRAL"
        if ve_mid is not None and t_years > 0:
            atm_depth = state.order_depths.get("VEV_5200")
            atm_mid = self._mid(atm_depth) if atm_depth is not None else None
            if atm_mid is not None and atm_mid > 0.5:
                iv = self._implied_vol(atm_mid, ve_mid, 5200.0, t_years)
                if iv is not None:
                    regime = self._update_iv_ema(cache, iv)

        # 2. Floor short - always.
        for p in self.FLOOR:
            self._sell_floor(p, state.order_depths.get(p), om)

        # 3. Voucher trading by regime.
        if ve_mid is not None and t_years > 0:
            for p in self.ATM:
                self._trade_atm(p, state.order_depths.get(p), ve_mid, t_years, regime, om)

        # 4. Delta hedge.
        if ve_mid is not None and t_years > 0:
            self._hedge(state.order_depths.get(self.VE), ve_mid, t_years, om)

        # 5. HGP MM.
        self._mm_hgp(state.order_depths.get(self.HP), om)

        cache["last_ts"] = ts
        return result, 0, json.dumps(cache, separators=(",", ":"))

    # ----------------------------------------------------- IV regime detection
    def _update_iv_ema(self, cache: Dict, iv: float) -> str:
        ema = float(cache.get("iv_ema", iv))
        var = float(cache.get("iv_var", 0.0001))
        n = int(cache.get("iv_n", 0)) + 1

        a = self.EMA_ALPHA
        new_ema = a * iv + (1 - a) * ema
        # variance EMA tracks (iv-ema)^2.
        diff = iv - ema
        new_var = self.STD_ALPHA * diff * diff + (1 - self.STD_ALPHA) * var

        cache["iv_ema"] = new_ema
        cache["iv_var"] = new_var
        cache["iv_n"] = n
        cache["iv_last"] = iv

        if n < self.MIN_SAMPLES:
            return "NEUTRAL"
        std = math.sqrt(max(1e-12, new_var))
        if iv < new_ema - self.K_LOW * std:
            return "CHEAP"
        if iv > new_ema + self.K_HI * std:
            return "RICH"
        return "NEUTRAL"

    def _implied_vol(self, mkt: float, s: float, k: float, t: float) -> Optional[float]:
        # Bisection on sigma in [0.01, 3.0] using BS call.
        intrinsic = max(0.0, s - k)
        if mkt <= intrinsic + 1e-6:
            return 0.01
        lo, hi = 0.01, 3.0
        for _ in range(40):
            mid = 0.5 * (lo + hi)
            px = self._bs(s, k, t, mid)
            if px > mkt:
                hi = mid
            else:
                lo = mid
            if hi - lo < 1e-4:
                break
        return 0.5 * (lo + hi)

    # ----------------------------------------------------- voucher logic
    def _trade_atm(self, product: str, depth: Optional[OrderDepth], ve_mid: float,
                   t_years: float, regime: str, om: _OM) -> None:
        if depth is None:
            return
        bid, ask, _, _ = self._top(depth)
        if bid is None or ask is None:
            return
        K = float(product.split("_")[1])
        # Use SIGMA_PRIOR for fair value because the EMA-derived IV is the
        # SIGNAL, not the pricing input. Otherwise the regime never fires.
        fair = self._bs(ve_mid, K, t_years, self.SIGMA_PRIOR)
        pos = om.pos(product)
        spread = ask - bid

        post_bid = bid + 1 if spread >= 2 else bid
        post_ask = ask - 1 if spread >= 2 else ask

        if regime == "CHEAP":
            # Aggressive long: take if ask < fair-0.5, post bid up to LONG_CAP.
            if pos < self.LONG_CAP and ask <= fair - 0.5:
                om.add(product, ask, min(20, self.LONG_CAP - pos))
                pos = om.pos(product)
            if pos < self.LONG_CAP and post_bid <= fair - 0.2:
                size = 20 if pos < self.LONG_CAP * 0.5 else 10
                om.add(product, post_bid, min(size, self.LONG_CAP - pos))
            # Trim only if we are way over.
            if pos > self.LONG_CAP * 0.95 and post_ask >= fair + 0.5:
                om.add(product, post_ask, -min(8, pos))

        elif regime == "RICH":
            # Aggressive short: take bid if bid > fair+0.5, post ask down to -SHORT_CAP.
            if pos > -self.SHORT_CAP and bid >= fair + 0.5:
                om.add(product, bid, -min(20, pos + self.SHORT_CAP))
                pos = om.pos(product)
            if pos > -self.SHORT_CAP and post_ask >= fair + 0.2:
                size = 20 if pos > -self.SHORT_CAP * 0.5 else 10
                om.add(product, post_ask, -min(size, pos + self.SHORT_CAP))
            # Trim long inventory passively.
            if pos > 0 and post_ask >= fair:
                om.add(product, post_ask, -min(15, pos))

        else:  # NEUTRAL: tight passive 1-tick MM around fair, modest size.
            if pos < 100 and post_bid <= fair - 0.6:
                om.add(product, post_bid, min(8, 100 - pos))
            if pos > -100 and post_ask >= fair + 0.6:
                om.add(product, post_ask, -min(8, 100 + pos))

    def _sell_floor(self, product: str, depth: Optional[OrderDepth], om: _OM) -> None:
        if depth is None:
            return
        bid, ask, _, _ = self._top(depth)
        if ask is None:
            return
        pos = om.pos(product)
        if ask <= 1 and pos > -self.FLOOR_SHORT_CAP:
            om.add(product, 1, -min(25, pos + self.FLOOR_SHORT_CAP))
        if bid is not None and bid <= 0 and pos < -self.FLOOR_SHORT_CAP + 30:
            om.add(product, 0, min(15, -pos))

    # ----------------------------------------------------- hedge
    def _hedge(self, depth: Optional[OrderDepth], ve_mid: float, t_years: float,
               om: _OM) -> None:
        if depth is None:
            return
        bid, ask, _, _ = self._top(depth)
        if bid is None or ask is None:
            return
        d_total = 0.0
        for p in self.ATM:
            K = float(p.split("_")[1])
            d_total += self._delta(ve_mid, K, t_years, self.SIGMA_PRIOR) * om.pos(p)
        target = -int(round(d_total))
        target = max(-180, min(180, target))
        pos = om.pos(self.VE)
        gap = target - pos
        spread = ask - bid

        if gap >= 8:
            px = bid + 1 if spread >= 2 else bid
            om.add(self.VE, px, min(25, gap, 200 - pos))
        elif gap <= -8:
            px = ask - 1 if spread >= 2 else ask
            om.add(self.VE, px, -min(25, -gap, 200 + pos))
        else:
            fair = self.VE_FV
            if pos < 50 and (bid + 1) <= fair - 1:
                om.add(self.VE, bid + 1, min(8, 50 - pos))
            if pos > -50 and (ask - 1) >= fair + 1:
                om.add(self.VE, ask - 1, -min(8, 50 + pos))

    # ----------------------------------------------------- HGP MM
    def _mm_hgp(self, depth: Optional[OrderDepth], om: _OM) -> None:
        if depth is None:
            return
        bid, ask, bv, av = self._top(depth)
        if bid is None or ask is None:
            return
        pos = om.pos(self.HP)
        L = 180
        spread = ask - bid

        if ask < self.HGP_FV - 2 and pos < L:
            om.add(self.HP, ask, min(25, L - pos))
            pos = om.pos(self.HP)
        if bid > self.HGP_FV + 2 and pos > -L:
            om.add(self.HP, bid, -min(25, L + pos))
            pos = om.pos(self.HP)

        post_bid = bid + 1 if spread >= 3 else bid
        post_ask = ask - 1 if spread >= 3 else ask
        total = bv + av
        obi = (bv - av) / total if total > 0 else 0.0
        allow_bid = obi <= 0.15
        allow_ask = obi >= -0.15
        size = 20 if abs(pos) < L * 0.6 else 12
        fair = self.HGP_FV - 0.05 * pos
        if allow_bid and pos < L and post_bid <= fair - 1:
            om.add(self.HP, post_bid, min(size, L - pos))
        if allow_ask and pos > -L and post_ask >= fair + 1:
            om.add(self.HP, post_ask, -min(size, L + pos))

    # ----------------------------------------------------- utilities
    def _tte_years(self, cache: Dict, ts: int) -> float:
        d_used = float(cache.get("day_idx", 0)) + ts / 1_000_000.0
        return max(0.5, self.INITIAL_TTE_DAYS - d_used) / 365.0

    def _track_day(self, cache: Dict, ts: int) -> None:
        last = cache.get("last_ts")
        idx = int(cache.get("day_idx", 0))
        if isinstance(last, int) and ts < last:
            idx += 1
        cache["day_idx"] = idx

    def _bs(self, s: float, k: float, t: float, sig: float) -> float:
        if t <= 0:
            return max(0.0, s - k)
        sqrt_t = math.sqrt(t)
        d1 = (math.log(max(1e-9, s / k)) + 0.5 * sig * sig * t) / (sig * sqrt_t)
        d2 = d1 - sig * sqrt_t
        return s * _norm_cdf(d1) - k * _norm_cdf(d2)

    def _delta(self, s: float, k: float, t: float, sig: float) -> float:
        if t <= 0:
            return 1.0 if s > k else 0.0
        d1 = (math.log(max(1e-9, s / k)) + 0.5 * sig * sig * t) / (sig * math.sqrt(t))
        return _norm_cdf(d1)

    def _top(self, depth):
        if depth is None:
            return None, None, 0, 0
        b = max(depth.buy_orders) if depth.buy_orders else None
        a = min(depth.sell_orders) if depth.sell_orders else None
        bv = int(depth.buy_orders[b]) if b is not None else 0
        av = -int(depth.sell_orders[a]) if a is not None else 0
        return b, a, bv, av

    def _mid(self, depth):
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