from __future__ import annotations

"""
Strategy 15C: LAYERED MM ACROSS THE FULL VOUCHER BOOK.

Core thesis:
    The two single-asset bets (15A long-gamma, 15B IV regime) leave money
    on the table on the OTHER 5 strikes (4000, 4500, 5000, 5100, 5500 partial,
    plus the underlyings). EDA shows:

      - VEV_4000 has 464 trades over 3 days -> liquid, but it's near pure
        intrinsic (delta~1, extrinsic ~ 0.13). Trade-vs-FV bias = +1.45 ticks
        (market PAYS premium).  -> SELL it when price > intrinsic + 1.5.
      - VEV_5200/5300/5400/5500 trade BELOW BS by 0.5 to 1 tick -> BUY them.
      - VEV_6000/6500 trade at 0/1 with BS=0.5 -> SELL the ask=1.
      - HGP and VE behave like classic MM products with deep books;
        EDA shows L1 depth ~38 in VE, ~12 in HGP, both stationary.

Design - layered MM with per-strike playbook:
    * Layer 1: Underlying MM at fixed FV, contrarian-OBI tilt.
        - HGP @ 10000, edge=2 ticks, full 200 limit.
        - VE  @ 5250, edge=1 tick, full 200 limit (also serves as hedging buffer).
    * Layer 2: Per-voucher BS-anchored MM with strike-specific edge tables.
        - Deep ITM (4000, 4500): pure intrinsic + tiny premium. Sell when market
          pays > intrinsic + 1.5 (toxic flow protection: cap small).
        - Light ATM (5000, 5100): wide spread (4-6 ticks), low trade rate.
          Quote both sides at fair +/- 1 tick, small size.
        - Heavy ATM (5200, 5300, 5400, 5500): aggressive long-bias MM, large
          size, take when ask < fair-1.
        - Floor (6000, 6500): permanent short at ask=1.
    * Layer 3: Continuous net delta hedge in VE (rehedge gap > 12).

Why this is structurally different:
    15A maxes out gamma on 4 strikes only. 15C trades ALL 10 vouchers + both
    underlyings. Lower per-strike size but broader breadth - more independent
    PnL streams smooth drawdowns and let us push aggregate delta exposure
    to the LIMIT without paying double for it on any single strike.
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

    DEEP_ITM = ["VEV_4000", "VEV_4500"]
    LIGHT_ATM = ["VEV_5000", "VEV_5100"]
    HEAVY_ATM = ["VEV_5200", "VEV_5300", "VEV_5400", "VEV_5500"]
    FLOOR = ["VEV_6000", "VEV_6500"]
    ALL_VOUCHERS = DEEP_ITM + LIGHT_ATM + HEAVY_ATM + FLOOR

    LIMITS = {
        HP: 200, VE: 200,
        "VEV_4000": 300, "VEV_4500": 300, "VEV_5000": 300, "VEV_5100": 300,
        "VEV_5200": 300, "VEV_5300": 300, "VEV_5400": 300, "VEV_5500": 300,
        "VEV_6000": 300, "VEV_6500": 300,
    }

    # Per-bucket caps (long, short side capacity).
    DEEP_ITM_CAP = 60     # small cap - they trade often but volatile vs VE moves
    LIGHT_ATM_CAP = 120
    HEAVY_ATM_CAP = 250
    FLOOR_CAP = 290

    HGP_FV = 10000
    VE_FV = 5250
    SIGMA = 0.342
    INITIAL_TTE_DAYS = 5.0

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

        # ---------- Layer 1: underlyings ----------
        self._mm_hgp(state.order_depths.get(self.HP), om)

        # ---------- Layer 2: voucher per-bucket ----------
        if ve_mid is not None and t_years > 0:
            for p in self.DEEP_ITM:
                self._mm_deep_itm(p, state.order_depths.get(p), ve_mid, t_years, om)
            for p in self.LIGHT_ATM:
                self._mm_light_atm(p, state.order_depths.get(p), ve_mid, t_years, om)
            for p in self.HEAVY_ATM:
                self._mm_heavy_atm(p, state.order_depths.get(p), ve_mid, t_years, om)
            for p in self.FLOOR:
                self._sell_floor(p, state.order_depths.get(p), om)

        # ---------- Layer 3: continuous net delta hedge in VE ----------
        if ve_mid is not None and t_years > 0:
            self._hedge_delta(state.order_depths.get(self.VE), ve_mid, t_years, om)
        else:
            # Pure VE MM if we have no good hedge target.
            self._mm_ve_baseline(state.order_depths.get(self.VE), om)

        cache["last_ts"] = ts
        return result, 0, json.dumps(cache, separators=(",", ":"))

    # =========================================================== voucher buckets
    def _mm_deep_itm(self, product, depth, ve_mid, t_years, om):
        # 4000/4500 = nearly pure intrinsic. Edge is small but consistent;
        # market historically pays +1.45 over BS on 4000 -> SELL when price
        # rises above intrinsic + 1.5. Stay small (delta-1 risk is large).
        if depth is None:
            return
        bid, ask, _, _ = self._top(depth)
        if bid is None or ask is None:
            return
        K = float(product.split("_")[1])
        intrinsic = max(0.0, ve_mid - K)
        fair = self._bs(ve_mid, K, t_years)  # fair = intrinsic + tiny extrinsic
        pos = om.pos(product)
        cap = self.DEEP_ITM_CAP
        spread = ask - bid

        post_bid = bid + 1 if spread >= 2 else bid
        post_ask = ask - 1 if spread >= 2 else ask

        # Sell when bid is well above fair (market overpays).
        if bid >= fair + 1.5 and pos > -cap:
            om.add(product, bid, -min(6, pos + cap))
            pos = om.pos(product)
        if pos > -cap and post_ask >= fair + 1.0:
            om.add(product, post_ask, -min(5, pos + cap))
        # Buy only if ask is meaningfully below fair (rare).
        if pos < cap and ask <= fair - 1.5:
            om.add(product, ask, min(5, cap - pos))
        if pos < cap and post_bid <= fair - 1.0:
            om.add(product, post_bid, min(4, cap - pos))

    def _mm_light_atm(self, product, depth, ve_mid, t_years, om):
        # 5000/5100: wide spread (4-6 ticks), low trade count (~1 over 3 days).
        # Post wide passive both sides at small size; almost entirely opportunistic.
        if depth is None:
            return
        bid, ask, _, _ = self._top(depth)
        if bid is None or ask is None:
            return
        K = float(product.split("_")[1])
        fair = self._bs(ve_mid, K, t_years)
        pos = om.pos(product)
        cap = self.LIGHT_ATM_CAP
        spread = ask - bid

        post_bid = bid + 1 if spread >= 3 else bid
        post_ask = ask - 1 if spread >= 3 else ask

        # Aggressive take only when very mispriced.
        if pos < cap and ask <= fair - 2.0:
            om.add(product, ask, min(8, cap - pos))
            pos = om.pos(product)
        if pos > -cap and bid >= fair + 2.0:
            om.add(product, bid, -min(8, pos + cap))
            pos = om.pos(product)

        if pos < cap and post_bid <= fair - 0.8:
            om.add(product, post_bid, min(6, cap - pos))
        if pos > -cap and post_ask >= fair + 0.8:
            om.add(product, post_ask, -min(6, pos + cap))

    def _mm_heavy_atm(self, product, depth, ve_mid, t_years, om):
        # 5200/5300/5400/5500: most liquid, biggest gamma scalp PnL,
        # market trades BELOW BS -> long-bias MM with large size.
        if depth is None:
            return
        bid, ask, bv, av = self._top(depth)
        if bid is None or ask is None:
            return
        K = float(product.split("_")[1])
        fair = self._bs(ve_mid, K, t_years)
        pos = om.pos(product)
        cap = self.HEAVY_ATM_CAP
        spread = ask - bid

        # Inventory penalty so we never accumulate forever in one direction.
        fair -= 0.01 * pos

        post_bid = bid + 1 if spread >= 2 else bid
        post_ask = ask - 1 if spread >= 2 else ask

        # Take ladder.
        if pos < cap and ask <= fair - 1.0:
            om.add(product, ask, min(15, cap - pos))
            pos = om.pos(product)
        if pos > -cap and bid >= fair + 1.0:
            om.add(product, bid, -min(15, pos + cap))
            pos = om.pos(product)

        # Strike-specific size on passive side.
        size_long = {"VEV_5200": 18, "VEV_5300": 18, "VEV_5400": 16, "VEV_5500": 12}.get(product, 14)
        size_short = max(6, size_long // 2)  # short side smaller (long-bias)

        if pos < cap and post_bid <= fair - 0.4:
            om.add(product, post_bid, min(size_long, cap - pos))
        if pos > -cap and post_ask >= fair + 0.4:
            om.add(product, post_ask, -min(size_short, pos + cap))

    def _sell_floor(self, product, depth, om):
        if depth is None:
            return
        bid, ask, _, _ = self._top(depth)
        if ask is None:
            return
        pos = om.pos(product)
        if ask <= 1 and pos > -self.FLOOR_CAP:
            om.add(product, 1, -min(25, pos + self.FLOOR_CAP))
        if bid is not None and bid <= 0 and pos < -self.FLOOR_CAP + 30:
            om.add(product, 0, min(15, -pos))

    # =========================================================== underlyings
    def _mm_hgp(self, depth, om):
        if depth is None:
            return
        bid, ask, bv, av = self._top(depth)
        if bid is None or ask is None:
            return
        pos = om.pos(self.HP)
        L = 180
        spread = ask - bid

        if ask < self.HGP_FV - 2 and pos < L:
            om.add(self.HP, ask, min(30, L - pos))
            pos = om.pos(self.HP)
        if bid > self.HGP_FV + 2 and pos > -L:
            om.add(self.HP, bid, -min(30, L + pos))
            pos = om.pos(self.HP)

        post_bid = bid + 1 if spread >= 3 else bid
        post_ask = ask - 1 if spread >= 3 else ask
        total = bv + av
        obi = (bv - av) / total if total > 0 else 0.0
        allow_bid = obi <= 0.15
        allow_ask = obi >= -0.15
        size = 22 if abs(pos) < L * 0.6 else 12
        fair = self.HGP_FV - 0.05 * pos
        if allow_bid and pos < L and post_bid <= fair - 1:
            om.add(self.HP, post_bid, min(size, L - pos))
        if allow_ask and pos > -L and post_ask >= fair + 1:
            om.add(self.HP, post_ask, -min(size, L + pos))

    def _mm_ve_baseline(self, depth, om):
        if depth is None:
            return
        bid, ask, _, _ = self._top(depth)
        if bid is None or ask is None:
            return
        pos = om.pos(self.VE)
        L = 100
        spread = ask - bid
        post_bid = bid + 1 if spread >= 2 else bid
        post_ask = ask - 1 if spread >= 2 else ask
        fair = self.VE_FV
        if pos < L and post_bid <= fair - 1:
            om.add(self.VE, post_bid, min(15, L - pos))
        if pos > -L and post_ask >= fair + 1:
            om.add(self.VE, post_ask, -min(15, L + pos))

    def _hedge_delta(self, depth, ve_mid, t_years, om):
        if depth is None:
            return
        bid, ask, bv, av = self._top(depth)
        if bid is None or ask is None:
            return

        d_total = 0.0
        for p in self.DEEP_ITM + self.LIGHT_ATM + self.HEAVY_ATM:
            K = float(p.split("_")[1])
            d_total += self._delta(ve_mid, K, t_years) * om.pos(p)
        target = -int(round(d_total))
        target = max(-180, min(180, target))
        pos = om.pos(self.VE)
        gap = target - pos
        spread = ask - bid

        # Contrarian OBI guard on the hedge order (do not chase imbalance).
        total = bv + av
        obi = (bv - av) / total if total > 0 else 0.0

        if gap >= 12:
            if obi <= 0.20:
                px = bid + 1 if spread >= 2 else bid
                om.add(self.VE, px, min(25, gap, 200 - pos))
            else:
                # Heavy bid book -> price about to fall, do not lift hedge here.
                pass
        elif gap <= -12:
            if obi >= -0.20:
                px = ask - 1 if spread >= 2 else ask
                om.add(self.VE, px, -min(25, -gap, 200 + pos))
        else:
            # Add small VE MM around fair when no big hedge needed.
            fair = self.VE_FV
            post_bid = bid + 1 if spread >= 2 else bid
            post_ask = ask - 1 if spread >= 2 else ask
            if pos < 60 and post_bid <= fair - 1:
                om.add(self.VE, post_bid, min(10, 60 - pos))
            if pos > -60 and post_ask >= fair + 1:
                om.add(self.VE, post_ask, -min(10, 60 + pos))

    # =========================================================== utilities
    def _tte_years(self, cache: Dict, ts: int) -> float:
        d_used = float(cache.get("day_idx", 0)) + ts / 1_000_000.0
        return max(0.5, self.INITIAL_TTE_DAYS - d_used) / 365.0

    def _track_day(self, cache: Dict, ts: int) -> None:
        last = cache.get("last_ts")
        idx = int(cache.get("day_idx", 0))
        if isinstance(last, int) and ts < last:
            idx += 1
        cache["day_idx"] = idx

    def _bs(self, s, k, t):
        if t <= 0:
            return max(0.0, s - k)
        sig = self.SIGMA
        sqrt_t = math.sqrt(t)
        d1 = (math.log(max(1e-9, s / k)) + 0.5 * sig * sig * t) / (sig * sqrt_t)
        d2 = d1 - sig * sqrt_t
        return s * _norm_cdf(d1) - k * _norm_cdf(d2)

    def _delta(self, s, k, t):
        if t <= 0:
            return 1.0 if s > k else 0.0
        sig = self.SIGMA
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