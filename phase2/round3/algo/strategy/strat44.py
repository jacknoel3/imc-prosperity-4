from __future__ import annotations

"""
Strat44: Ale v3 HGP engine plus the independent voucher/VE boosters that worked.

What the logs taught us:
- Ale v3 is the best HGP engine so far: more aggressive thresholds, target carry,
  and OBI-tilted passive size added about +999 PnL versus Ale v2, entirely from
  HYDROGEL_PACK.
- Strat39 added about +237 PnL versus Ale v2 without touching HGP, mostly from
  VEV_5000 and slightly more active VE passive quoting.
- HGP stop/cap variants reduced PnL badly. Strat41 proved that waiting for a
  confirmed pullback before shorting misses too much edge; strat42/43 proved
  adverse stops and short caps are too expensive.

This version therefore keeps Ale v3's HGP untouched, keeps Ale's VEV_4000/4500
passive MR, never trades VEV_6000/6500, and layers the proven VE/VEV_5000 boost.
"""

import json
import math
from typing import Any, Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ModuleNotFoundError:
    from prosperity3bt.datamodel import Order, OrderDepth, TradingState


# === HGP block (verbatim Noah hydrogel_simple_mr_v5_ramp logic, namespaced) ===

HGP_SYMBOL = "HYDROGEL_PACK"
HGP_POSITION_LIMIT = 200

Z_WINDOW = 500
FAST_WINDOW = 100
LOCAL_WINDOW = 200
MIN_ACTIVE_HISTORY = 300  # noah107: was 500, recovers ~200 ticks of valid signal

Z500_LONG_TH = -1.9
LOCAL_TOP_REBOUND_TH = 48.0
LOCAL_BOTTOM_DRAWDOWN_TH = 56.0
FAST_Z_TH = 0.9

MOM_FAST = 5
MOM_MED = 10
MOM_SLOW = 20

ACTIVE_HOLD_STEPS = 55
ACTIVE_COOLDOWN_STEPS = 1
TAKER_CHUNK = 20
MIN_ACTIVE_GAP = 6

Z500_LONG_BASE_TARGET = 135
Z500_LONG_MAX_TARGET = 188
Z500_LONG_SLOPE = 42

LOCAL_LONG_BASE_TARGET = 122
LOCAL_LONG_MAX_TARGET = 178
LOCAL_LONG_SLOPE = 1.15

LOCAL_SHORT_BASE_TARGET = 125
LOCAL_SHORT_MAX_TARGET = 178
LOCAL_SHORT_SLOPE = 1.15

BUY_MAX_ABOVE_ANCHOR = 11.0
SELL_MAX_BELOW_ANCHOR = 20.0

INSIDE_OFFSET = 1
BASE_MM_SIZE = 24
MIN_SPREAD_MM = 7
MAX_SPREAD_MM = 18
HARD_PASSIVE_CAP = 192

MILD_Z = 0.85
MILD_LONG_SCALE = 45
MILD_SHORT_SCALE = 32
OBI_TILT_TH = 0.15
OBI_BOOST_MULT = 1.35
OBI_SUPPRESS_MULT = 0.35


def _hgp_mean_std(xs: List[float]) -> Tuple[Optional[float], Optional[float]]:
    n = len(xs)
    if n < 2:
        return None, None
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return m, math.sqrt(max(var, 1e-12))


def _hgp_clip_int(x: float, lo: int, hi: int) -> int:
    return int(max(lo, min(hi, round(x))))


def _hgp_best_bid_ask(depth: OrderDepth):
    if not depth.buy_orders or not depth.sell_orders:
        return None, None
    return max(depth.buy_orders.keys()), min(depth.sell_orders.keys())


def _hgp_mid(depth: OrderDepth) -> Optional[float]:
    bb, ba = _hgp_best_bid_ask(depth)
    if bb is None or ba is None:
        return None
    return (bb + ba) / 2.0


def _hgp_obi(depth: OrderDepth) -> float:
    bid_depth = sum(max(0, int(v)) for v in depth.buy_orders.values())
    ask_depth = sum(max(0, -int(v)) for v in depth.sell_orders.values())
    total = bid_depth + ask_depth
    if total <= 0:
        return 0.0
    return (bid_depth - ask_depth) / total


def _hgp_capacity(position: int, side: str) -> int:
    if side == "BUY":
        return max(0, HGP_POSITION_LIMIT - position)
    return max(0, HGP_POSITION_LIMIT + position)


def _hgp_rolling_stats(xs: List[float], window: int) -> Tuple[Optional[float], Optional[float]]:
    if len(xs) < window:
        return None, None
    return _hgp_mean_std(xs[-window:])


def _hgp_recent_mom(xs: List[float], w: int) -> Optional[float]:
    if len(xs) <= w:
        return None
    return xs[-1] - xs[-1 - w]


def _hgp_low_high(xs: List[float], w: int) -> Tuple[Optional[float], Optional[float]]:
    if len(xs) < w:
        return None, None
    sub = xs[-w:]
    return min(sub), max(sub)


def _hgp_z500_long_target(z: float) -> int:
    raw = Z500_LONG_BASE_TARGET + Z500_LONG_SLOPE * max(0.0, abs(z) - 2.0)
    return _hgp_clip_int(raw, 0, Z500_LONG_MAX_TARGET)


def _hgp_local_long_target(dd: float) -> int:
    raw = LOCAL_LONG_BASE_TARGET + LOCAL_LONG_SLOPE * max(0.0, dd - LOCAL_BOTTOM_DRAWDOWN_TH)
    return _hgp_clip_int(raw, 0, LOCAL_LONG_MAX_TARGET)


def _hgp_local_short_target(rb: float) -> int:
    raw = LOCAL_SHORT_BASE_TARGET + LOCAL_SHORT_SLOPE * max(0.0, rb - LOCAL_TOP_REBOUND_TH)
    return _hgp_clip_int(raw, 0, LOCAL_SHORT_MAX_TARGET)


def _hgp_dynamic_long_threshold(fast_z, m5, m10, m20) -> float:
    threshold = Z500_LONG_TH
    if fast_z is not None and fast_z > -0.4:
        threshold += 0.05
    if m5 is not None and m5 > 0:
        threshold += 0.04
    if m20 is not None and m20 > 0:
        threshold += 0.06
    return threshold


def _hgp_detect_signal(mid, anchor, z500, fast_z, local_low, local_high, m5, m10, m20):
    if anchor is None or z500 is None:
        return "none", 0, "no_z500"
    candidates = []
    z_long_th = _hgp_dynamic_long_threshold(fast_z, m5, m10, m20)
    medium_reversal = m10 is not None and m10 > 0
    slow_reversal = m20 is not None and m20 > 0
    fast_snapback = fast_z is not None and fast_z <= -1.4 and m5 is not None and m5 >= 0
    if z500 <= z_long_th and (medium_reversal or slow_reversal or fast_snapback):
        tgt = _hgp_z500_long_target(z500)
        if fast_z is not None and fast_z <= -1.6 and (m10 is not None and m10 > 0):
            tgt = min(Z500_LONG_MAX_TARGET, int(tgt * 1.08))
        score = 10.0 + abs(z500)
        if m20 is not None and m20 > 0:
            score += 0.5
        candidates.append(("long", tgt, score, "v3_z500_vol_long"))
    if local_high is not None and fast_z is not None and m5 is not None and m5 > 0:
        dd = local_high - mid
        if dd >= LOCAL_BOTTOM_DRAWDOWN_TH and fast_z <= -FAST_Z_TH and z500 < 1.0:
            tgt = _hgp_local_long_target(dd)
            if z500 <= -0.8:
                tgt = min(LOCAL_LONG_MAX_TARGET, int(tgt * 1.10))
            score = 7.0 + 0.04 * dd + 0.50 * abs(fast_z)
            candidates.append(("long", tgt, score, "v3_local_bottom_vol_long"))
    if local_low is not None and fast_z is not None and m5 is not None and m5 < 0:
        rb = mid - local_low
        if rb >= LOCAL_TOP_REBOUND_TH and fast_z >= FAST_Z_TH and z500 > -1.0:
            tgt = _hgp_local_short_target(rb)
            if z500 >= 0.25:
                tgt = min(LOCAL_SHORT_MAX_TARGET, int(tgt * 1.10))
            score = 8.0 + 0.04 * rb + 0.50 * fast_z
            candidates.append(("short", -tgt, score, "v3_local_top_vol_short"))
    if not candidates:
        return "none", 0, "none"
    side, target, _, reason = max(candidates, key=lambda x: x[2])
    return side, target, reason


def _hgp_update_active(step, prev_side, prev_target, prev_until, prev_reason,
                       new_side, new_target, new_reason):
    """v3: carry peak target forward while same-side volatility persists."""
    if new_side in ("long", "short") and new_target != 0:
        if prev_side == new_side and step <= prev_until:
            if new_side == "long":
                new_target = max(prev_target, new_target)
            else:
                new_target = min(prev_target, new_target)
        return new_side, new_target, step + ACTIVE_HOLD_STEPS, new_reason
    if prev_side in ("long", "short") and step <= prev_until:
        return prev_side, prev_target, prev_until, "sticky_" + prev_reason
    return "none", 0, -1, "none"


def _hgp_dynamic_chunk(side: str, current_z: Optional[float], z_entry: float) -> int:
    """noah107: scale chunk by signal strength_delta (z_entry vs current).
    Stronger signal -> full chunk. Weakening -> small chunk, let passive work."""
    if current_z is None:
        return max(4, TAKER_CHUNK // 2)
    if side == "long":
        sd = z_entry - current_z
    else:
        sd = current_z - z_entry
    if sd > 0.3:
        return TAKER_CHUNK
    elif sd > -0.1:
        return max(6, TAKER_CHUNK * 3 // 4)
    elif sd > -0.5:
        return max(4, TAKER_CHUNK // 2)
    else:
        return max(2, TAKER_CHUNK // 3)


def _hgp_passive_target(z500, side, allowed):
    if side in ("long", "short") and allowed != 0:
        return allowed
    if z500 is None:
        return 0
    if z500 <= Z500_LONG_TH:
        return _hgp_z500_long_target(z500)
    if z500 >= MILD_Z:
        return -_hgp_clip_int(MILD_SHORT_SCALE * z500, 0, 60)
    if z500 <= -MILD_Z:
        return _hgp_clip_int(-MILD_LONG_SCALE * z500, 0, 75)
    return 0


def _hgp_active_orders(depth, position, side, target, anchor, step, last_active_step,
                       z_entry, current_z):
    """noah107: chunk size scales with strength_delta (dynamic_chunk)."""
    orders: List[Order] = []
    if side not in ("long", "short") or target == 0:
        return orders, position, last_active_step
    if anchor is None:
        return orders, position, last_active_step
    if step - last_active_step < ACTIVE_COOLDOWN_STEPS:
        return orders, position, last_active_step
    gap = target - position
    if abs(gap) < MIN_ACTIVE_GAP:
        return orders, position, last_active_step
    bb, ba = _hgp_best_bid_ask(depth)
    if bb is None or ba is None:
        return orders, position, last_active_step
    chunk = _hgp_dynamic_chunk(side, current_z, z_entry)
    if gap > 0:
        visible_ask = max(0, -depth.sell_orders.get(ba, 0))
        qty = min(chunk, gap, visible_ask, _hgp_capacity(position, "BUY"))
        reducing_short = position < 0
        adding_long_ok = ba < anchor + BUY_MAX_ABOVE_ANCHOR
        if qty > 0 and (reducing_short or adding_long_ok):
            orders.append(Order(HGP_SYMBOL, int(ba), int(qty)))
            position += qty
            last_active_step = step
    elif gap < 0:
        visible_bid = max(0, depth.buy_orders.get(bb, 0))
        qty = min(chunk, -gap, visible_bid, _hgp_capacity(position, "SELL"))
        reducing_long = position > 0
        adding_short_ok = bb > anchor - SELL_MAX_BELOW_ANCHOR
        if qty > 0 and (reducing_long or adding_short_ok):
            orders.append(Order(HGP_SYMBOL, int(bb), -int(qty)))
            position -= qty
            last_active_step = step
    return orders, position, last_active_step


def _hgp_passive_size(pos_after, desired, side):
    pos = pos_after
    gap = desired - pos
    if side == "BUY" and pos >= HARD_PASSIVE_CAP:
        return 0
    if side == "SELL" and pos <= -HARD_PASSIVE_CAP:
        return 0
    if gap > MIN_ACTIVE_GAP:
        if side == "BUY":
            return BASE_MM_SIZE
        if pos <= desired:
            return 0
        return max(1, BASE_MM_SIZE // 4)
    if gap < -MIN_ACTIVE_GAP:
        if side == "SELL":
            return BASE_MM_SIZE
        if pos >= desired:
            return 0
        return max(1, BASE_MM_SIZE // 4)
    return BASE_MM_SIZE


def _hgp_passive_orders(depth, original_pos, pos_after, desired, existing):
    orders: List[Order] = []
    bb, ba = _hgp_best_bid_ask(depth)
    if bb is None or ba is None:
        return orders
    spread = ba - bb
    if spread < MIN_SPREAD_MM or spread > MAX_SPREAD_MM:
        return orders
    bid_px = bb + INSIDE_OFFSET
    ask_px = ba - INSIDE_OFFSET
    if bid_px >= ask_px:
        return orders
    has_active_buy = any(o.quantity > 0 and o.price >= ba for o in existing)
    has_active_sell = any(o.quantity < 0 and o.price <= bb for o in existing)
    used_buy = sum(o.quantity for o in existing if o.quantity > 0)
    used_sell = sum(-o.quantity for o in existing if o.quantity < 0)
    buy_cap = max(0, _hgp_capacity(original_pos, "BUY") - used_buy)
    sell_cap = max(0, _hgp_capacity(original_pos, "SELL") - used_sell)
    obi = _hgp_obi(depth)
    if not has_active_sell:
        qty = min(_hgp_passive_size(pos_after, desired, "BUY"), buy_cap)
        if obi < -OBI_TILT_TH:
            qty = min(buy_cap, int(round(qty * OBI_BOOST_MULT)))
        elif obi > OBI_TILT_TH:
            qty = int(qty * OBI_SUPPRESS_MULT)
        if qty > 0:
            orders.append(Order(HGP_SYMBOL, int(bid_px), int(qty)))
    if not has_active_buy:
        qty = min(_hgp_passive_size(pos_after, desired, "SELL"), sell_cap)
        if obi > OBI_TILT_TH:
            qty = min(sell_cap, int(round(qty * OBI_BOOST_MULT)))
        elif obi < -OBI_TILT_TH:
            qty = int(qty * OBI_SUPPRESS_MULT)
        if qty > 0:
            orders.append(Order(HGP_SYMBOL, int(ask_px), -int(qty)))
    return orders


# === Voucher engine (strat30 verbatim, with HGP block removed) ===


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
    VE = "VELVETFRUIT_EXTRACT"
    VOUCHERS = [
        "VEV_4000", "VEV_4500", "VEV_5000", "VEV_5100", "VEV_5200",
        "VEV_5300", "VEV_5400", "VEV_5500", "VEV_6000", "VEV_6500",
    ]
    LIMITS = {VE: 200, **{v: 300 for v in VOUCHERS}}

    ANCHOR = {
        VE: 5262.0,
        "VEV_4000": 1265.0, "VEV_4500": 764.0, "VEV_5000": 266.0,
        "VEV_5100": 175.0, "VEV_5200": 101.0, "VEV_5300": 50.0,
        "VEV_5400": 16.0, "VEV_5500": 6.0,
        "VEV_6000": 0.5, "VEV_6500": 0.5,
    }
    # SOFT caps tuned from EDA model_lq_inventory_capacity (delta-equivalent capacity)
    SOFT = {
        VE: 130,
        "VEV_4000": 24, "VEV_4500": 24, "VEV_5000": 55,
        "VEV_5100": 55, "VEV_5200": 70, "VEV_5300": 60,
        "VEV_5400": 50, "VEV_5500": 12,  # 5500 capacity = 14, was 30 (above limit)
        "VEV_6000": 0, "VEV_6500": 0,
    }
    REG = {
        "VEV_4000": (-3998.27, 1.000, 0.83), "VEV_4500": (-4497.00, 0.999, 0.76),
        "VEV_5000": (-4550.49, 0.915, 1.40), "VEV_5100": (-3950.96, 0.784, 3.48),
        "VEV_5200": (-2871.36, 0.565, 3.92), "VEV_5300": (-1704.69, 0.334, 3.41),
        "VEV_5400": (-644.24, 0.126, 2.81), "VEV_5500": (-281.46, 0.055, 1.51),
    }
    STRIKE = {
        "VEV_4000": 4000, "VEV_4500": 4500, "VEV_5000": 5000, "VEV_5100": 5100,
        "VEV_5200": 5200, "VEV_5300": 5300, "VEV_5400": 5400, "VEV_5500": 5500,
        "VEV_6000": 6000, "VEV_6500": 6500,
    }
    DELTA_APPROX = {
        "VEV_4000": 1.00, "VEV_4500": 1.00, "VEV_5000": 0.93, "VEV_5100": 0.78,
        "VEV_5200": 0.57, "VEV_5300": 0.33, "VEV_5400": 0.13, "VEV_5500": 0.05,
        "VEV_6000": 0.00, "VEV_6500": 0.00,
    }
    CORE_BASKET = ("VEV_5000", "VEV_5100", "VEV_5200", "VEV_5300", "VEV_5400")

    def bid(self) -> int:
        return 1

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {}
        cache = self._load_cache(state.traderData)

        # --- HGP (Noah block) ---
        hgp_state = cache.get("hgp", {})
        hgp_orders = self._run_hgp(state, hgp_state)
        cache["hgp"] = hgp_state
        if hgp_orders is not None:
            result[HGP_SYMBOL] = hgp_orders

        # --- Vouchers + VE (strat30 engine) ---
        for p in self.LIMITS:
            result.setdefault(p, [])
        om = OrderManager(state, self.LIMITS, result)
        self._update_state(state, cache)

        depth_ve = state.order_depths.get(self.VE)
        if depth_ve is not None:
            self._trade_ve_passive(depth_ve, cache, om)
        for product in self.VOUCHERS:
            depth = state.order_depths.get(product)
            if depth is None:
                continue
            self._trade_voucher(product, depth, cache, om)
        self._trade_noarb_lower_bound(state, cache, om)
        self._delta_hedge_passive(state, cache, om)

        cache["t"] = int(getattr(state, "timestamp", 0))
        return result, 0, json.dumps(cache, separators=(",", ":"))

    # ----------- HGP -----------

    def _run_hgp(self, state: TradingState, s: Dict[str, Any]) -> Optional[List[Order]]:
        step = int(s.get("step", 0))
        mids = [float(x) for x in s.get("mids", [])][-Z_WINDOW:]
        last_active_step = int(s.get("last_active_step", -10_000))
        prev_side = str(s.get("active_side", "none"))
        prev_target = int(s.get("active_target", 0))
        prev_until = int(s.get("active_until", -1))
        prev_reason = str(s.get("active_reason", "none"))
        active_z_entry = float(s.get("active_z_entry", 0.0))

        if HGP_SYMBOL not in state.order_depths:
            self._save_hgp(s, step + 1, mids, last_active_step, prev_side, prev_target, prev_until, prev_reason, active_z_entry)
            return None

        depth = state.order_depths[HGP_SYMBOL]
        position = state.position.get(HGP_SYMBOL, 0)
        bb, ba = _hgp_best_bid_ask(depth)
        mid = _hgp_mid(depth)
        if bb is None or ba is None or mid is None:
            self._save_hgp(s, step + 1, mids, last_active_step, prev_side, prev_target, prev_until, prev_reason, active_z_entry)
            return []

        mids.append(float(mid))
        if len(mids) > Z_WINDOW:
            mids = mids[-Z_WINDOW:]

        anchor, sigma = _hgp_rolling_stats(mids, Z_WINDOW)
        z500 = None
        if anchor is not None and sigma is not None and len(mids) >= MIN_ACTIVE_HISTORY:
            z500 = (mid - anchor) / max(sigma, 1e-12)
        fast_mean, fast_sigma = _hgp_rolling_stats(mids, FAST_WINDOW)
        fast_z = None
        if fast_mean is not None and fast_sigma is not None:
            fast_z = (mid - fast_mean) / max(fast_sigma, 1e-12)
        local_low, local_high = _hgp_low_high(mids, LOCAL_WINDOW)
        m5 = _hgp_recent_mom(mids, MOM_FAST)
        m10 = _hgp_recent_mom(mids, MOM_MED)
        m20 = _hgp_recent_mom(mids, MOM_SLOW)

        new_side, new_target, new_reason = _hgp_detect_signal(mid, anchor, z500, fast_z, local_low, local_high, m5, m10, m20)
        side, target, until, reason = _hgp_update_active(
            step, prev_side, prev_target, prev_until, prev_reason,
            new_side, new_target, new_reason,
        )
        # Record z500 at moment of fresh fire (sticky continuations keep prior entry)
        if new_side in ("long", "short") and new_target != 0:
            active_z_entry = z500 if z500 is not None else 0.0

        desired_raw = _hgp_passive_target(z500, side, target)

        orders: List[Order] = []
        active_orders, pos_after, last_active_step = _hgp_active_orders(
            depth, position, side, target, anchor, step, last_active_step,
            active_z_entry, z500,
        )
        orders.extend(active_orders)
        orders.extend(_hgp_passive_orders(depth, position, pos_after, desired_raw, orders))

        self._save_hgp(s, step + 1, mids, last_active_step, side, target, until, reason, active_z_entry)
        return orders

    def _save_hgp(self, s, step, mids, last_active_step, side, target, until, reason, z_entry):
        s["step"] = int(step)
        s["mids"] = mids[-Z_WINDOW:]
        s["last_active_step"] = int(last_active_step)
        s["active_side"] = str(side)
        s["active_target"] = int(target)
        s["active_until"] = int(until)
        s["active_reason"] = str(reason)
        s["active_z_entry"] = float(z_entry)

    # ----------- Voucher engine (verbatim from strat30) -----------

    def _update_state(self, state: TradingState, cache: Dict[str, Any]) -> None:
        ve_depth = state.order_depths.get(self.VE)
        ve_mid = self._mid(ve_depth)
        if ve_mid is None:
            return
        cache["ve_mid"] = round(float(ve_mid), 4)
        self._ema(cache, "ve_slow", ve_mid, 0.025)
        vals = []
        for product, (a, beta, std) in self.REG.items():
            mid = self._mid(state.order_depths.get(product))
            if mid is None:
                continue
            key = product.replace("_", "").lower()
            resid = mid - (a + beta * ve_mid)
            center = self._ema(cache, f"{key}_rv_center", resid, 0.018)
            z = (resid - center) / max(std, 0.75)
            cache[f"{key}_rv_z"] = round(z, 6)
            if product in self.CORE_BASKET:
                vals.append(z)
        if vals:
            vals.sort()
            trimmed = vals[1:-1] if len(vals) >= 3 else vals
            cache["rv_basket_z"] = round(sum(trimmed) / len(trimmed), 6)

    def _trade_ve_passive(self, depth: OrderDepth, cache: Dict[str, Any], om: OrderManager) -> None:
        bid, ask, bvol, avol = self._best_bid_ask(depth)
        mid = self._mid(depth)
        if bid is None or ask is None or mid is None:
            return
        spread = ask - bid
        if spread <= 0:
            return
        ema = self._ema(cache, "ve_ema", mid, 0.045)
        anchor = self.ANCHOR[self.VE]
        fair = 0.6 * anchor + 0.4 * ema
        obi = self._obi(bvol, avol)
        pos = om.pos(self.VE)
        soft = self.SOFT[self.VE]
        fair -= 0.030 * pos
        edge = 0.55
        buy_px = bid + 1 if spread >= 3 else bid
        sell_px = ask - 1 if spread >= 3 else ask
        base = 22
        buy_size = base
        sell_size = base
        if obi > 0.12:
            buy_size = max(0, buy_size // 3)
        elif obi < -0.12:
            sell_size = max(0, sell_size // 3)
        if pos > soft * 0.55:
            buy_size = 0
            sell_size = int(sell_size * 1.4)
        elif pos < -soft * 0.55:
            sell_size = 0
            buy_size = int(buy_size * 1.4)
        if buy_size > 0 and pos < soft and buy_px <= fair - edge:
            om.add(self.VE, buy_px, min(buy_size, soft - pos))
        if sell_size > 0 and pos > -soft and sell_px >= fair + edge:
            om.add(self.VE, sell_px, -min(sell_size, pos + soft))

    def _trade_voucher(self, product: str, depth: OrderDepth, cache: Dict[str, Any], om: OrderManager) -> None:
        if product in ("VEV_6000", "VEV_6500"):
            return
        if product in ("VEV_4000", "VEV_4500"):
            self._trade_mr(product, depth, cache, om, alpha=0.025, inv=0.080, edge=4.0, size=6, taker=False)
            return
        if product == "VEV_5000":
            self._trade_mr(product, depth, cache, om, alpha=0.030, inv=0.085, edge=2.1, size=10, taker=True)
        elif product in ("VEV_5100", "VEV_5200", "VEV_5300"):
            self._trade_mr(product, depth, cache, om, alpha=0.035, inv=0.075, edge=1.4, size=12, taker=True)
        elif product == "VEV_5400":
            self._trade_mr(product, depth, cache, om, alpha=0.045, inv=0.040, edge=0.8, size=14, taker=True)
        elif product == "VEV_5500":
            self._trade_mr(product, depth, cache, om, alpha=0.050, inv=0.060, edge=1.2, size=8, taker=False)

    def _trade_intrinsic_mm(self, product: str, depth: OrderDepth, cache: Dict[str, Any], om: OrderManager) -> None:
        """Deep-ITM voucher MM around intrinsic (S - K).
        EDA insight: model_noarb_survival shows 939-3045 lower-bound violations/day with avg
        lifetime 1.1-1.5 ticks - take fast or miss. But research_voucher_passive_favorable_fills
        shows only 8-12% favorable rate at ANY mispricing - so use take_edge >= 2 for AS protection."""
        ve_mid = float(cache.get("ve_mid", 0.0))
        if ve_mid <= 0:
            return
        K = self.STRIKE[product]
        fair = max(ve_mid - K, 0.0)
        bid, ask, _, _ = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return
        half_spread = 3
        size = 12
        take_edge = 2  # AS protection: only take >=2 ticks below intrinsic
        soft = self.SOFT[product]
        pos = om.pos(product)
        skew = int(round(pos / max(self.LIMITS[product], 1) * half_spread))
        bid_px = int(round(fair - half_spread - skew))
        ask_px = int(round(fair + half_spread - skew))
        bid_px = min(bid_px, ask - 1)
        ask_px = max(ask_px, bid + 1)
        # Aggressive take with AS guard
        for ap in sorted(depth.sell_orders):
            if ap >= fair - take_edge:
                break
            if pos >= soft:
                break
            vol = -int(depth.sell_orders[ap])
            qty = min(vol, soft - pos, size)
            if qty <= 0:
                break
            placed = om.add(product, int(ap), int(qty))
            pos += placed
            if placed <= 0:
                break
        for bp in sorted(depth.buy_orders, reverse=True):
            if bp <= fair + take_edge:
                break
            if pos <= -soft:
                break
            vol = int(depth.buy_orders[bp])
            qty = min(vol, pos + soft, size)
            if qty <= 0:
                break
            placed = -om.add(product, int(bp), -int(qty))
            pos += placed
            if placed >= 0:
                break
        # Passive quotes
        if pos < soft and bid_px > 0:
            om.add(product, bid_px, min(size, soft - pos))
        if pos > -soft:
            om.add(product, ask_px, -min(size, pos + soft))

    def _trade_mr(self, product, depth, cache, om, alpha, inv, edge, size, taker):
        bid, ask, bvol, avol = self._best_bid_ask(depth)
        mid = self._mid(depth)
        if bid is None or ask is None or mid is None:
            return
        spread = ask - bid
        if spread <= 0:
            return
        key = product.replace("_", "").lower()
        anchor = self.ANCHOR[product]
        ema = self._ema(cache, f"{key}_ema", mid, alpha)
        last = float(cache.get(f"{key}_last", mid))
        abs_move = self._ema(cache, f"{key}_abs", abs(mid - last), 0.08)
        cache[f"{key}_last"] = round(float(mid), 6)
        micro = self._microprice(depth)
        micro_edge = (micro - mid) if micro is not None else 0.0
        anchor_w = 0.72
        fair = anchor_w * anchor + (1 - anchor_w) * ema + 0.08 * micro_edge
        pos = om.pos(product)
        soft = self.SOFT[product]
        if soft <= 0:
            return
        fair -= inv * pos
        dyn_edge = edge + min(1.5 * edge, 0.25 * abs_move)
        if spread >= 3:
            buy_px, sell_px = bid + 1, ask - 1
        else:
            buy_px, sell_px = bid, ask
        richness = mid - fair
        buy_size = sell_size = size
        if richness > dyn_edge:
            buy_size = max(1, size // 4); sell_size = int(size * 1.6)
        elif richness < -dyn_edge:
            buy_size = int(size * 1.6); sell_size = max(1, size // 4)
        if pos > soft * 0.55:
            buy_size = 0; sell_size = int(size * 2.0)
        elif pos < -soft * 0.55:
            buy_size = int(size * 2.0); sell_size = 0
        basket_z = float(cache.get("rv_basket_z", 0.0))
        if product in self.CORE_BASKET:
            if basket_z < -0.85:
                buy_size = int(buy_size * 1.15)
                sell_size = max(1, int(sell_size * 0.80)) if sell_size > 0 else 0
            elif basket_z > 0.85:
                sell_size = int(sell_size * 1.20)
                buy_size = max(1, int(buy_size * 0.70)) if buy_size > 0 else 0
        # EDA voucher_maker_taker_fills: VEV_5400/5500 = 0% fills_at_ask. Bots only sell.
        # Posting passive ask is wasted capacity. Keep only as inventory exit when long.
        if product in ("VEV_5400", "VEV_5500") and pos <= 0:
            sell_size = 0
        if taker:
            if pos > -soft and bid >= fair + dyn_edge + spread:
                self._sweep_sell(product, depth, bid, min(sell_size, pos + soft), om)
                pos = om.pos(product)
            if pos < soft and ask <= fair - dyn_edge - spread:
                self._sweep_buy(product, depth, ask, min(buy_size, soft - pos), om)
                pos = om.pos(product)
        if pos < soft and buy_size > 0 and buy_px <= fair - dyn_edge:
            om.add(product, buy_px, min(buy_size, soft - pos))
        if pos > -soft and sell_size > 0 and sell_px >= fair + dyn_edge:
            om.add(product, sell_px, -min(sell_size, pos + soft))

    def _trade_noarb_lower_bound(self, state, cache, om):
        ve_depth = state.order_depths.get(self.VE)
        ve_bid, _, _, _ = self._best_bid_ask(ve_depth)
        if ve_bid is None:
            return
        for product, threshold, qty in (("VEV_4000", 1.0, 3), ("VEV_4500", 0.8, 3)):
            depth = state.order_depths.get(product)
            bid, ask, _, ask_vol = self._best_bid_ask(depth)
            if bid is None or ask is None or ask_vol <= 0:
                continue
            lb = ve_bid - self.STRIKE[product]
            edge = lb - ask
            if edge < threshold:
                continue
            pos = om.pos(product)
            soft = min(self.SOFT[product], 24)
            if pos >= soft:
                continue
            buy_qty = min(qty, ask_vol, soft - pos)
            filled = om.add(product, ask, buy_qty)
            if filled > 0 and om.pos(self.VE) > -150:
                om.add(self.VE, ve_bid, -min(filled, 150 + om.pos(self.VE)))

    def _delta_hedge_passive(self, state, cache, om):
        """EDA research_hedge_threshold_grid: optimum at delta_threshold ~0.10 (cost 4136 vs
        4233 at threshold 0). Dead-zone 50 was effectively never triggering. Use 12."""
        ve_depth = state.order_depths.get(self.VE)
        ve_bid, ve_ask, _, _ = self._best_bid_ask(ve_depth)
        if ve_bid is None or ve_ask is None:
            return
        net_delta = 0.0
        for v in self.VOUCHERS:
            net_delta += om.pos(v) * self.DELTA_APPROX.get(v, 0.0)
        ve_pos = om.pos(self.VE)
        target_ve = -net_delta * 0.5
        gap = target_ve - ve_pos
        if abs(gap) < 12:
            return
        soft = 150
        if gap > 0:
            qty = min(int(gap), max(0, soft - ve_pos), 6)
            if qty > 0:
                om.add(self.VE, int(ve_bid), qty)
        else:
            qty = min(int(-gap), max(0, ve_pos + soft), 6)
            if qty > 0:
                om.add(self.VE, int(ve_ask), -qty)

    def _sweep_buy(self, product, depth, max_price, qty, om):
        rem = max(0, int(qty))
        for price, vol in sorted(depth.sell_orders.items()):
            if rem <= 0 or price > max_price:
                break
            rem -= om.add(product, int(price), min(rem, -int(vol)))

    def _sweep_sell(self, product, depth, min_price, qty, om):
        rem = max(0, int(qty))
        for price, vol in sorted(depth.buy_orders.items(), reverse=True):
            if rem <= 0 or price < min_price:
                break
            rem -= om.add(product, int(price), -min(rem, int(vol)))

    def _best_bid_ask(self, depth):
        if depth is None:
            return None, None, 0, 0
        bid = max(depth.buy_orders) if depth.buy_orders else None
        ask = min(depth.sell_orders) if depth.sell_orders else None
        bvol = int(depth.buy_orders[bid]) if bid is not None else 0
        avol = -int(depth.sell_orders[ask]) if ask is not None else 0
        return bid, ask, bvol, avol

    def _mid(self, depth):
        bid, ask, _, _ = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2.0

    def _microprice(self, depth):
        bid, ask, bvol, avol = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return None
        total = bvol + avol
        if total <= 0:
            return (bid + ask) / 2.0
        return (ask * bvol + bid * avol) / total

    def _obi(self, bvol, avol):
        total = bvol + avol
        if total <= 0:
            return 0.0
        return (bvol - avol) / total

    def _ema(self, cache, key, value, alpha):
        old = cache.get(key)
        new = (1 - alpha) * float(old) + alpha * value if isinstance(old, (int, float)) else value
        cache[key] = round(float(new), 6)
        return float(new)

    def _load_cache(self, trader_data):
        if not trader_data:
            return {}
        try:
            parsed = json.loads(trader_data)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, json.JSONDecodeError):
            return {}
