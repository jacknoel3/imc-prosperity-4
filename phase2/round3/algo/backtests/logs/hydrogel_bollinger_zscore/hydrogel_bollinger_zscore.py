"""
HYDROGEL_PACK — Bollinger Re-entry / State Machine MR Bot
IMC Prosperity 4 | Round 3

Purpose:
    Cleaner, staged mean-reversion bot using Bollinger/z-score logic.

Why this version exists:
    Previous bots either:
      - bought too early on first oversold bounce,
      - sold/shorted too early at the rebound top,
      - or flipped back long too quickly during a broader down move.

Core design:
    1. z-score/Bollinger bands define valuation:
         z <= -2.2  => oversold watch zone
         z >= +2.2  => overbought watch zone

    2. We do NOT instantly go full size on first band touch.
       First touch = watch.
       Re-entry / rollover confirmation = active trade.

    3. Active long signals:
         - z was oversold recently and now re-enters above lower band,
           with mom5 > 0 and no fresh local low.
         - OR local selloff + fast-z oversold + rebound confirmation.
         - Deep oversold probe is allowed, but smaller.

    4. Active short signals:
         - z was overbought recently and now re-enters below upper band,
           with mom5 < 0 and no fresh local high.
         - OR local rebound + fast-z overbought + rollover confirmation.
         - Deep overbought probe is allowed, but smaller.

    5. Active targets are staged and sticky for a short build window.
       Target decay/passive bias does NOT trigger spread crossing.

    6. Inventory does not need to end flat. Remaining position is marked to mid.

Set VERBOSE=False before final submission.
"""

from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Optional, Tuple
import json
import math


# ── PARAMETERS ──────────────────────────────────────────────────────────────

SYMBOL = "HYDROGEL_PACK"
POSITION_LIMIT = 200

# Main rolling z-score windows.
FULL_WINDOW = 500
WARMUP_WINDOW = 300
FAST_WINDOW = 100
LOCAL_WINDOW = 200

# Warm-up:
# Before 300 observations, active taker is disabled.
# 300-499 uses z300 with reduced target size.
# 500+ uses z500 with full target size.
MIN_ACTIVE_HISTORY = 300

# Bollinger / z-score thresholds.
LOW_TOUCH_Z = -2.2
HIGH_TOUCH_Z = 2.2
LOW_REENTRY_Z = -2.0
HIGH_REENTRY_Z = 2.0
DEEP_LOW_Z = -2.8
DEEP_HIGH_Z = 2.8
MILD_Z = 1.0

# Signal memory.
TOUCH_MEMORY_STEPS = 60          # remember band touch for 60 observations
ACTIVE_HOLD_STEPS = 38           # keep confirmed active target for build window
OPPOSITE_LOCAL_COOLDOWN = 28     # block weak opposite local flips after signal

# Momentum / stability.
MOM_FAST_WINDOW = 5
MOM_CONTEXT_WINDOW = 20
STABILITY_WINDOW = 8

# Local extension rules.
FAST_Z_TRIGGER = 1.20
LOCAL_MOVE_TRIGGER = 45.0

# Context guards:
# Local long requires broader momentum not still clearly falling.
# Local short requires broader momentum not still clearly rising.
LOCAL_LONG_MIN_MOM20 = -2.0
LOCAL_SHORT_MAX_MOM20 = 2.0

# Target sizing.
LONG_REENTRY_TARGET = 125
SHORT_REENTRY_TARGET = 115

LONG_PROBE_TARGET = 70
SHORT_PROBE_TARGET = 65

LOCAL_LONG_TARGET = 105
LOCAL_SHORT_TARGET = 115

MAX_LONG_TARGET = 175
MAX_SHORT_TARGET = 165

TARGET_PER_EXTRA_Z_LONG = 35
TARGET_PER_EXTRA_Z_SHORT = 30
TARGET_PER_EXTRA_LOCAL_TICK = 1.15

# Active trading.
TAKER_CHUNK = 16
ACTIVE_COOLDOWN_STEPS = 1
MIN_ACTIVE_GAP = 8

# Edge guards for adding fresh risk.
# Covering shorts / reducing longs is allowed more freely.
BUY_MAX_ABOVE_ANCHOR = 8.0
SELL_MAX_BELOW_ANCHOR = 16.0

# Passive MM.
INSIDE_OFFSET = 1
BASE_MM_SIZE = 20
MIN_SPREAD_MM = 7
MAX_SPREAD_MM = 17
HARD_PASSIVE_CAP = 185

VERBOSE = True


# ── STATE HELPERS ───────────────────────────────────────────────────────────

def load_state(trader_data: str) -> dict:
    default = {
        "step": 0,
        "mids": [],
        "last_active_step": -10_000,

        # Active confirmed target.
        "active_side": "none",        # "long", "short", "none"
        "active_target": 0,
        "active_until": -1,
        "active_reason": "none",

        # Watch memory.
        "last_low_touch": -10_000,
        "last_high_touch": -10_000,

        # Used for opposite local flip cooldown.
        "last_signal_side": "none",
        "last_signal_step": -10_000,
    }

    if not trader_data:
        return default

    try:
        s = json.loads(trader_data)
        for k, v in default.items():
            if k not in s:
                s[k] = v
        if not isinstance(s.get("mids"), list):
            s["mids"] = []
        return s
    except Exception:
        return default


def save_state(
    step: int,
    mids: List[float],
    last_active_step: int,
    active_side: str,
    active_target: int,
    active_until: int,
    active_reason: str,
    last_low_touch: int,
    last_high_touch: int,
    last_signal_side: str,
    last_signal_step: int,
) -> str:
    if len(mids) > FULL_WINDOW:
        mids = mids[-FULL_WINDOW:]

    return json.dumps({
        "step": int(step),
        "mids": mids,
        "last_active_step": int(last_active_step),
        "active_side": str(active_side),
        "active_target": int(active_target),
        "active_until": int(active_until),
        "active_reason": str(active_reason),
        "last_low_touch": int(last_low_touch),
        "last_high_touch": int(last_high_touch),
        "last_signal_side": str(last_signal_side),
        "last_signal_step": int(last_signal_step),
    })


def mean_std(xs: List[float]) -> Tuple[Optional[float], Optional[float]]:
    n = len(xs)
    if n < 2:
        return None, None
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return m, math.sqrt(max(var, 1e-12))


def clip_int(x: float, lo: int, hi: int) -> int:
    return int(max(lo, min(hi, round(x))))


# ── BOOK / PRICE HELPERS ────────────────────────────────────────────────────

def best_bid_ask(depth: OrderDepth):
    if not depth.buy_orders or not depth.sell_orders:
        return None, None
    return max(depth.buy_orders.keys()), min(depth.sell_orders.keys())


def mid_price(depth: OrderDepth) -> Optional[float]:
    best_bid, best_ask = best_bid_ask(depth)
    if best_bid is None or best_ask is None:
        return None
    return (best_bid + best_ask) / 2.0


def official_capacity(position: int, side: str) -> int:
    if side == "BUY":
        return max(0, POSITION_LIMIT - position)
    return max(0, POSITION_LIMIT + position)


def rolling_stats(xs: List[float], window: int) -> Tuple[Optional[float], Optional[float]]:
    if len(xs) < window:
        return None, None
    return mean_std(xs[-window:])


def recent_momentum(xs: List[float], window: int) -> Optional[float]:
    if len(xs) <= window:
        return None
    return xs[-1] - xs[-1 - window]


def rolling_low_high(xs: List[float], window: int) -> Tuple[Optional[float], Optional[float]]:
    if len(xs) < window:
        return None, None
    sub = xs[-window:]
    return min(sub), max(sub)


def has_stabilized_after_low(xs: List[float], window: int = STABILITY_WINDOW) -> bool:
    """
    True if current mid is not making a fresh local low.
    This avoids buying while price is still printing lower lows.
    """
    if len(xs) <= window:
        return False
    prev_lows = xs[-window - 1:-1]
    return xs[-1] > min(prev_lows)


def has_stabilized_after_high(xs: List[float], window: int = STABILITY_WINDOW) -> bool:
    """
    True if current mid is not making a fresh local high.
    This avoids shorting while price is still squeezing upward.
    """
    if len(xs) <= window:
        return False
    prev_highs = xs[-window - 1:-1]
    return xs[-1] < max(prev_highs)


# ── SIGNAL CALCULATION ──────────────────────────────────────────────────────

def compute_main_z(mids: List[float]) -> Tuple[Optional[float], Optional[float], Optional[float], int, float]:
    """
    Returns anchor, std, z, window_used, confidence.
    500-window is preferred; 300-window is warm-up with reduced target.
    """
    if len(mids) >= FULL_WINDOW:
        anchor, std = rolling_stats(mids, FULL_WINDOW)
        if anchor is None or std is None:
            return None, None, None, 0, 0.0
        return anchor, std, (mids[-1] - anchor) / max(std, 1e-12), FULL_WINDOW, 1.0

    if len(mids) >= WARMUP_WINDOW:
        anchor, std = rolling_stats(mids, WARMUP_WINDOW)
        if anchor is None or std is None:
            return None, None, None, 0, 0.0
        return anchor, std, (mids[-1] - anchor) / max(std, 1e-12), WARMUP_WINDOW, 0.65

    return None, None, None, 0, 0.0


def scale_target(raw: int, confidence: float) -> int:
    return max(0, int(round(raw * max(0.0, min(1.0, confidence)))))


def target_long_reentry(z: float, confidence: float) -> int:
    raw = LONG_REENTRY_TARGET + TARGET_PER_EXTRA_Z_LONG * max(0.0, abs(z) - abs(LOW_TOUCH_Z))
    return clip_int(scale_target(raw, confidence), 0, MAX_LONG_TARGET)


def target_short_reentry(z: float, confidence: float) -> int:
    raw = SHORT_REENTRY_TARGET + TARGET_PER_EXTRA_Z_SHORT * max(0.0, abs(z) - HIGH_TOUCH_Z)
    return clip_int(scale_target(raw, confidence), 0, MAX_SHORT_TARGET)


def target_local(extension: float, base: int, max_target: int, confidence: float) -> int:
    raw = base + TARGET_PER_EXTRA_LOCAL_TICK * max(0.0, extension - LOCAL_MOVE_TRIGGER)
    return clip_int(scale_target(raw, confidence), 0, max_target)


def block_opposite_local(
    side: str,
    reason: str,
    step: int,
    last_signal_side: str,
    last_signal_step: int,
) -> bool:
    """
    Avoid immediately flipping on weak local opposite bounces.
    Core z/Bollinger re-entry signals are allowed to override cooldown.
    """
    if last_signal_side not in ("long", "short"):
        return False
    if side == last_signal_side:
        return False
    if step - last_signal_step > OPPOSITE_LOCAL_COOLDOWN:
        return False

    # Allow core z re-entry to override. Block local-only opposite flips.
    if reason.startswith("z_reentry") or reason.startswith("deep_z"):
        return False
    return True


def detect_confirmed_signal(
    step: int,
    mids: List[float],
    mid: float,
    best_bid: int,
    best_ask: int,
    anchor: Optional[float],
    z: Optional[float],
    confidence: float,
    fast_mean: Optional[float],
    fast_std: Optional[float],
    local_low: Optional[float],
    local_high: Optional[float],
    mom5: Optional[float],
    mom20: Optional[float],
    last_low_touch: int,
    last_high_touch: int,
    last_signal_side: str,
    last_signal_step: int,
) -> Tuple[str, int, str]:
    """
    Returns side, signed_target, reason.
    side: "long", "short", or "none".
    """
    if anchor is None or z is None or mom5 is None:
        return "none", 0, "no_signal"

    stable_low = has_stabilized_after_low(mids)
    stable_high = has_stabilized_after_high(mids)

    z_fast = None
    if fast_mean is not None and fast_std is not None:
        z_fast = (mid - fast_mean) / max(fast_std, 1e-12)

    candidates = []

    # --- Long logic ---

    # 1) Proper Bollinger re-entry long:
    # price touched oversold recently, then re-enters above lower band with positive momentum.
    low_touch_recent = step - last_low_touch <= TOUCH_MEMORY_STEPS
    if low_touch_recent and z > LOW_REENTRY_Z and mom5 > 0 and stable_low:
        raw_tgt = target_long_reentry(z, confidence)
        reason = "z_reentry_long"
        score = 6.0 + abs(z)
        candidates.append(("long", raw_tgt, score, reason))

    # 2) Deep oversold probe: smaller target. Useful if z stays deeply oversold but stabilizes.
    if z <= DEEP_LOW_Z and mom5 > 0 and stable_low and (mom20 is None or mom20 > -30):
        raw_tgt = clip_int(scale_target(LONG_PROBE_TARGET, confidence), 0, MAX_LONG_TARGET)
        reason = "deep_z_probe_long"
        score = 4.0 + abs(z)
        candidates.append(("long", raw_tgt, score, reason))

    # 3) Local selloff + rebound long. Stricter context to avoid 78k-style false bounce.
    if local_high is not None and z_fast is not None and mom5 > 0 and stable_low:
        drawdown = local_high - mid
        mom20_ok = mom20 is not None and mom20 >= LOCAL_LONG_MIN_MOM20
        context_ok = z < 0.75 and best_ask < anchor + BUY_MAX_ABOVE_ANCHOR

        if drawdown >= LOCAL_MOVE_TRIGGER and z_fast <= -FAST_Z_TRIGGER and mom20_ok and context_ok:
            raw_tgt = target_local(drawdown, LOCAL_LONG_TARGET, MAX_LONG_TARGET, confidence)
            if z < -1.0:
                raw_tgt = min(MAX_LONG_TARGET, int(raw_tgt * 1.10))
            reason = "local_rebound_long"
            score = 3.5 + 0.035 * drawdown + 0.50 * abs(z_fast)
            candidates.append(("long", raw_tgt, score, reason))

    # --- Short logic ---

    # 4) Proper Bollinger re-entry short:
    # price touched overbought recently, then re-enters below upper band with negative momentum.
    high_touch_recent = step - last_high_touch <= TOUCH_MEMORY_STEPS
    if high_touch_recent and z < HIGH_REENTRY_Z and mom5 < 0 and stable_high:
        raw_tgt = target_short_reentry(z, confidence)
        reason = "z_reentry_short"
        score = 6.0 + abs(z)
        candidates.append(("short", -raw_tgt, score, reason))

    # 5) Deep overbought probe: smaller target.
    if z >= DEEP_HIGH_Z and mom5 < 0 and stable_high and (mom20 is None or mom20 < 30):
        raw_tgt = clip_int(scale_target(SHORT_PROBE_TARGET, confidence), 0, MAX_SHORT_TARGET)
        reason = "deep_z_probe_short"
        score = 4.0 + abs(z)
        candidates.append(("short", -raw_tgt, score, reason))

    # 6) Local rebound + rollover short. Designed for tops where z500 is not >2.
    if local_low is not None and z_fast is not None and mom5 < 0 and stable_high:
        rebound = mid - local_low
        mom20_ok = mom20 is not None and mom20 <= LOCAL_SHORT_MAX_MOM20
        context_ok = z > -0.75 and best_bid > anchor - SELL_MAX_BELOW_ANCHOR

        if rebound >= LOCAL_MOVE_TRIGGER and z_fast >= FAST_Z_TRIGGER and mom20_ok and context_ok:
            raw_tgt = target_local(rebound, LOCAL_SHORT_TARGET, MAX_SHORT_TARGET, confidence)
            if z > 1.0:
                raw_tgt = min(MAX_SHORT_TARGET, int(raw_tgt * 1.10))
            reason = "local_rollover_short"
            score = 3.8 + 0.035 * rebound + 0.50 * z_fast
            candidates.append(("short", -raw_tgt, score, reason))

    if not candidates:
        return "none", 0, "no_confirmed_signal"

    # Choose strongest candidate.
    side, signed_target, score, reason = max(candidates, key=lambda x: x[2])

    if block_opposite_local(side, reason, step, last_signal_side, last_signal_step):
        return "none", 0, "opposite_local_cooldown_block"

    return side, signed_target, reason


def update_active_target(
    step: int,
    prev_side: str,
    prev_target: int,
    prev_until: int,
    prev_reason: str,
    new_side: str,
    new_target: int,
    new_reason: str,
) -> Tuple[str, int, int, str, bool]:
    """
    New confirmed signal creates/refreshes active target.
    Existing target remains active until expiry.
    After expiry, active taking stops, but inventory is not forcibly flattened.
    """
    if new_side in ("long", "short") and new_target != 0:
        return new_side, new_target, step + ACTIVE_HOLD_STEPS, new_reason, True

    if prev_side in ("long", "short") and step <= prev_until:
        return prev_side, prev_target, prev_until, prev_reason, True

    return "none", 0, -1, "no_active_target", False


def passive_target_from_z(z: Optional[float], active_side: str, active_target: int) -> Tuple[int, str]:
    """
    Passive target can use valuation even when active signal is inactive.
    This biases maker quotes without crossing the spread.
    """
    if active_side in ("long", "short") and active_target != 0:
        return active_target, "active_target"

    if z is None:
        return 0, "no_z"

    if z <= LOW_TOUCH_Z:
        raw = 85 + 25 * max(0.0, abs(z) - abs(LOW_TOUCH_Z))
        return clip_int(raw, 0, 120), "passive_oversold_long"

    if z >= HIGH_TOUCH_Z:
        raw = 75 + 20 * max(0.0, abs(z) - HIGH_TOUCH_Z)
        return clip_int(-raw, -110, 0), "passive_overbought_short"

    if z <= -MILD_Z:
        return clip_int(-35 * z, 0, 70), "passive_mild_long"

    if z >= MILD_Z:
        return clip_int(-30 * z, -60, 0), "passive_mild_short"

    return 0, "neutral"


# ── ORDER LOGIC ─────────────────────────────────────────────────────────────

def active_orders_to_target(
    depth: OrderDepth,
    position: int,
    active_target: int,
    active_side: str,
    anchor: Optional[float],
    step: int,
    last_active_step: int,
) -> Tuple[List[Order], int, int, str]:
    orders: List[Order] = []

    if active_side not in ("long", "short") or active_target == 0:
        return orders, position, last_active_step, "no_active_signal"

    if anchor is None:
        return orders, position, last_active_step, "no_anchor"

    if step - last_active_step < ACTIVE_COOLDOWN_STEPS:
        return orders, position, last_active_step, "cooldown"

    gap = active_target - position
    if abs(gap) < MIN_ACTIVE_GAP:
        return orders, position, last_active_step, "target_close"

    best_bid, best_ask = best_bid_ask(depth)
    if best_bid is None or best_ask is None:
        return orders, position, last_active_step, "empty_book"

    # Need to buy toward long target or to cover short.
    if gap > 0:
        visible_ask = max(0, -depth.sell_orders.get(best_ask, 0))
        qty = min(TAKER_CHUNK, gap, visible_ask, official_capacity(position, "BUY"))

        reducing_short = position < 0
        adding_long_ok = best_ask < anchor + BUY_MAX_ABOVE_ANCHOR

        if qty > 0 and (reducing_short or adding_long_ok):
            orders.append(Order(SYMBOL, int(best_ask), int(qty)))
            position += qty
            last_active_step = step
            return orders, position, last_active_step, "active_buy_to_target"

    # Need to sell toward short target or reduce long.
    elif gap < 0:
        visible_bid = max(0, depth.buy_orders.get(best_bid, 0))
        qty = min(TAKER_CHUNK, -gap, visible_bid, official_capacity(position, "SELL"))

        reducing_long = position > 0
        adding_short_ok = best_bid > anchor - SELL_MAX_BELOW_ANCHOR

        if qty > 0 and (reducing_long or adding_short_ok):
            orders.append(Order(SYMBOL, int(best_bid), -int(qty)))
            position -= qty
            last_active_step = step
            return orders, position, last_active_step, "active_sell_to_target"

    return orders, position, last_active_step, "edge_guard_blocked"


def passive_side_size(position_after_active: int, desired_target: int, side: str) -> int:
    pos = position_after_active
    gap = desired_target - pos

    # Hard cap near exchange limits.
    if side == "BUY" and pos >= HARD_PASSIVE_CAP:
        return 0
    if side == "SELL" and pos <= -HARD_PASSIVE_CAP:
        return 0

    # If target wants long, bias passive toward bids.
    if gap > MIN_ACTIVE_GAP:
        if side == "BUY":
            return BASE_MM_SIZE
        # Don't fight desired long unless already above target.
        if pos <= desired_target:
            return 0
        return max(1, BASE_MM_SIZE // 4)

    # If target wants short, bias passive toward asks.
    if gap < -MIN_ACTIVE_GAP:
        if side == "SELL":
            return BASE_MM_SIZE
        # Don't fight desired short unless already below target.
        if pos >= desired_target:
            return 0
        return max(1, BASE_MM_SIZE // 4)

    # Near target / neutral.
    return BASE_MM_SIZE


def passive_mm_orders(
    depth: OrderDepth,
    original_position: int,
    position_after_active: int,
    desired_target: int,
    existing_orders: List[Order],
) -> List[Order]:
    orders: List[Order] = []

    best_bid, best_ask = best_bid_ask(depth)
    if best_bid is None or best_ask is None:
        return orders

    spread = best_ask - best_bid
    if spread < MIN_SPREAD_MM or spread > MAX_SPREAD_MM:
        return orders

    bid_px = best_bid + INSIDE_OFFSET
    ask_px = best_ask - INSIDE_OFFSET
    if bid_px >= ask_px:
        return orders

    has_active_buy = any(o.quantity > 0 and o.price >= best_ask for o in existing_orders)
    has_active_sell = any(o.quantity < 0 and o.price <= best_bid for o in existing_orders)

    used_buy = sum(o.quantity for o in existing_orders if o.quantity > 0)
    used_sell = sum(-o.quantity for o in existing_orders if o.quantity < 0)

    buy_cap = max(0, official_capacity(original_position, "BUY") - used_buy)
    sell_cap = max(0, official_capacity(original_position, "SELL") - used_sell)

    if not has_active_sell:
        qty = min(passive_side_size(position_after_active, desired_target, "BUY"), buy_cap)
        if qty > 0:
            orders.append(Order(SYMBOL, int(bid_px), int(qty)))

    if not has_active_buy:
        qty = min(passive_side_size(position_after_active, desired_target, "SELL"), sell_cap)
        if qty > 0:
            orders.append(Order(SYMBOL, int(ask_px), -int(qty)))

    return orders


# ── TRADER ──────────────────────────────────────────────────────────────────

class Trader:
    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}

        s = load_state(state.traderData)
        step = int(s.get("step", 0))
        mids = [float(x) for x in s.get("mids", [])][-FULL_WINDOW:]

        last_active_step = int(s.get("last_active_step", -10_000))

        prev_active_side = str(s.get("active_side", "none"))
        prev_active_target = int(s.get("active_target", 0))
        prev_active_until = int(s.get("active_until", -1))
        prev_active_reason = str(s.get("active_reason", "none"))

        last_low_touch = int(s.get("last_low_touch", -10_000))
        last_high_touch = int(s.get("last_high_touch", -10_000))

        last_signal_side = str(s.get("last_signal_side", "none"))
        last_signal_step = int(s.get("last_signal_step", -10_000))

        if SYMBOL not in state.order_depths:
            return result, 0, save_state(
                step + 1, mids, last_active_step,
                prev_active_side, prev_active_target, prev_active_until, prev_active_reason,
                last_low_touch, last_high_touch,
                last_signal_side, last_signal_step,
            )

        depth = state.order_depths[SYMBOL]
        position = state.position.get(SYMBOL, 0)

        best_bid, best_ask = best_bid_ask(depth)
        mid = mid_price(depth)
        if mid is None or best_bid is None or best_ask is None:
            result[SYMBOL] = []
            return result, 0, save_state(
                step + 1, mids, last_active_step,
                prev_active_side, prev_active_target, prev_active_until, prev_active_reason,
                last_low_touch, last_high_touch,
                last_signal_side, last_signal_step,
            )

        mids.append(float(mid))
        if len(mids) > FULL_WINDOW:
            mids = mids[-FULL_WINDOW:]

        anchor, main_std, z, z_window, confidence = compute_main_z(mids)

        # Update touch memory.
        if z is not None:
            if z <= LOW_TOUCH_Z:
                last_low_touch = step
            if z >= HIGH_TOUCH_Z:
                last_high_touch = step

        fast_mean, fast_std = rolling_stats(mids, FAST_WINDOW)
        local_low, local_high = rolling_low_high(mids, LOCAL_WINDOW)
        mom5 = recent_momentum(mids, MOM_FAST_WINDOW)
        mom20 = recent_momentum(mids, MOM_CONTEXT_WINDOW)

        new_side, new_target, new_reason = detect_confirmed_signal(
            step=step,
            mids=mids,
            mid=mid,
            best_bid=best_bid,
            best_ask=best_ask,
            anchor=anchor,
            z=z,
            confidence=confidence,
            fast_mean=fast_mean,
            fast_std=fast_std,
            local_low=local_low,
            local_high=local_high,
            mom5=mom5,
            mom20=mom20,
            last_low_touch=last_low_touch,
            last_high_touch=last_high_touch,
            last_signal_side=last_signal_side,
            last_signal_step=last_signal_step,
        )

        active_side, active_target, active_until, active_reason, fresh_signal = update_active_target(
            step=step,
            prev_side=prev_active_side,
            prev_target=prev_active_target,
            prev_until=prev_active_until,
            prev_reason=prev_active_reason,
            new_side=new_side,
            new_target=new_target,
            new_reason=new_reason,
        )

        if fresh_signal and new_side in ("long", "short"):
            last_signal_side = new_side
            last_signal_step = step

        desired_target, desired_reason = passive_target_from_z(z, active_side, active_target)

        orders: List[Order] = []

        active_orders, pos_after_active, last_active_step, active_order_reason = active_orders_to_target(
            depth=depth,
            position=position,
            active_target=active_target,
            active_side=active_side,
            anchor=anchor,
            step=step,
            last_active_step=last_active_step,
        )
        orders.extend(active_orders)

        mm_orders = passive_mm_orders(
            depth=depth,
            original_position=position,
            position_after_active=pos_after_active,
            desired_target=desired_target,
            existing_orders=orders,
        )
        orders.extend(mm_orders)

        result[SYMBOL] = orders

        if VERBOSE:
            spread = best_ask - best_bid
            z_s = "None" if z is None else f"{z:.2f}"
            fast_z_s = "None"
            if fast_mean is not None and fast_std is not None:
                fast_z_s = f"{((mid - fast_mean) / max(fast_std, 1e-12)):.2f}"
            anchor_s = "None" if anchor is None else f"{anchor:.2f}"
            m5_s = "None" if mom5 is None else f"{mom5:.1f}"
            m20_s = "None" if mom20 is None else f"{mom20:.1f}"
            rebound_s = "None" if local_low is None else f"{(mid - local_low):.1f}"
            drawdown_s = "None" if local_high is None else f"{(local_high - mid):.1f}"

            print(
                f"[HYDRO_BB_STATE_MR] ts={state.timestamp} step={step} "
                f"mid={mid:.1f} anchor={anchor_s} z={z_s} zwin={z_window} conf={confidence:.2f} "
                f"fast_z={fast_z_s} mom5={m5_s} mom20={m20_s} "
                f"rebound200={rebound_s} drawdown200={drawdown_s} spread={spread} "
                f"pos={position} active_side={active_side} active_target={active_target} "
                f"active_until={active_until} active_reason={active_reason} new_signal={new_reason} "
                f"desired_target={desired_target} desired_reason={desired_reason} "
                f"active_order={active_order_reason} pos_after={pos_after_active} orders={orders}"
            )

        return result, 0, save_state(
            step + 1,
            mids,
            last_active_step,
            active_side,
            active_target,
            active_until,
            active_reason,
            last_low_touch,
            last_high_touch,
            last_signal_side,
            last_signal_step,
        )
