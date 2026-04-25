"""
HYDROGEL_PACK — Simple Mean-Reversion v5 Ramped Execution
IMC Prosperity 4 | Round 3

Based on hydrogel_simple_mr_v4.

What changed vs v4:
    1. Same-side target churn fix:
        - Long signal can only actively BUY.
        - Short signal can only actively SELL.
        - If a long target gets smaller while we are already long, we do not
          cross the spread to sell. We simply stop buying and let passive quotes
          manage inventory.
        - Same for shorts: a smaller short target does not trigger active buys.

    2. Ramped execution:
        - A fresh signal does not immediately allow the full target.
        - The allowed active target ramps up over several ticks.
        - This scatters clustered entries/exits and reduces "machine-gunning."

    3. Adverse-extreme throttle:
        - For a long signal, if price is still making a fresh short-term low,
          the ramp is slowed.
        - For a short signal, if price is still making a fresh short-term high,
          the ramp is slowed.
        - This does not reverse the position. It only prevents adding too fast.

Core signals are unchanged:
    LONG A — z500 mean-reversion:
        z500 <= -2.0 and mom10 > 0 or mom20 > 0
        target +125 to +175

    LONG B — local bottom rebound:
        drawdown from local high 200 >= 60
        fast_z100 <= -1.0
        mom5 > 0
        target +110 to +155

    SHORT — local top rollover:
        rebound from local low 200 >= 50
        fast_z100 >= +1.0
        mom5 < 0
        target -115 to -155

Set VERBOSE=False before final submission.
"""

from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Optional, Tuple
import json
import math


# ── PARAMETERS ──────────────────────────────────────────────────────────────

SYMBOL = "HYDROGEL_PACK"
POSITION_LIMIT = 200

# Windows
Z_WINDOW = 500
FAST_WINDOW = 100
LOCAL_WINDOW = 200
MIN_ACTIVE_HISTORY = 500

# Signal thresholds
Z500_LONG_TH = -2.0

LOCAL_TOP_REBOUND_TH = 50.0
LOCAL_BOTTOM_DRAWDOWN_TH = 60.0
FAST_Z_TH = 1.0

# Momentum confirmation
MOM_FAST = 5
MOM_MED = 10
MOM_SLOW = 20

# Active signal persistence
ACTIVE_HOLD_STEPS = 45
ACTIVE_COOLDOWN_STEPS = 1
TAKER_CHUNK = 16
MIN_ACTIVE_GAP = 8

# Ramped execution
# allowed fraction = RAMP_START_FRAC + RAMP_PER_STEP * signal_age, capped at 1.
RAMP_START_FRAC = 0.35
RAMP_PER_STEP = 0.08
ADVERSE_RAMP_MULT = 0.55
ADVERSE_LOOKBACK = 8

# Target sizing
Z500_LONG_BASE_TARGET = 125
Z500_LONG_MAX_TARGET = 175
Z500_LONG_SLOPE = 35       # extra target per |z|-2

LOCAL_LONG_BASE_TARGET = 110
LOCAL_LONG_MAX_TARGET = 155
LOCAL_LONG_SLOPE = 1.0     # extra target per tick beyond 60

LOCAL_SHORT_BASE_TARGET = 115
LOCAL_SHORT_MAX_TARGET = 155
LOCAL_SHORT_SLOPE = 1.0    # extra target per tick beyond 50

# Edge guards for active crossing.
# These are intentionally simple. Reducing opposite inventory is allowed.
BUY_MAX_ABOVE_ANCHOR = 8.0
SELL_MAX_BELOW_ANCHOR = 18.0

# Passive MM
INSIDE_OFFSET = 1
BASE_MM_SIZE = 20
MIN_SPREAD_MM = 7
MAX_SPREAD_MM = 17
HARD_PASSIVE_CAP = 185

# Passive mild target from z500
MILD_Z = 1.0
MILD_LONG_SCALE = 35
MILD_SHORT_SCALE = 25

VERBOSE = True


# ── STATE ───────────────────────────────────────────────────────────────────

def load_state(trader_data: str) -> dict:
    default = {
        "step": 0,
        "mids": [],
        "last_active_step": -10_000,
        "active_side": "none",       # long / short / none
        "active_peak_target": 0,     # signed full target
        "active_until": -1,
        "active_reason": "none",
        "signal_start_step": -10_000,
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
    active_peak_target: int,
    active_until: int,
    active_reason: str,
    signal_start_step: int,
) -> str:
    if len(mids) > Z_WINDOW:
        mids = mids[-Z_WINDOW:]

    return json.dumps({
        "step": int(step),
        "mids": mids,
        "last_active_step": int(last_active_step),
        "active_side": str(active_side),
        "active_peak_target": int(active_peak_target),
        "active_until": int(active_until),
        "active_reason": str(active_reason),
        "signal_start_step": int(signal_start_step),
    })


# ── HELPERS ─────────────────────────────────────────────────────────────────

def mean_std(xs: List[float]) -> Tuple[Optional[float], Optional[float]]:
    n = len(xs)
    if n < 2:
        return None, None
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return m, math.sqrt(max(var, 1e-12))


def clip_int(x: float, lo: int, hi: int) -> int:
    return int(max(lo, min(hi, round(x))))


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


def making_fresh_low(xs: List[float], lookback: int = ADVERSE_LOOKBACK) -> bool:
    if len(xs) <= lookback:
        return False
    prev = xs[-lookback - 1:-1]
    return xs[-1] <= min(prev)


def making_fresh_high(xs: List[float], lookback: int = ADVERSE_LOOKBACK) -> bool:
    if len(xs) <= lookback:
        return False
    prev = xs[-lookback - 1:-1]
    return xs[-1] >= max(prev)


# ── SIGNALS ─────────────────────────────────────────────────────────────────

def z500_long_target(z500: float) -> int:
    raw = Z500_LONG_BASE_TARGET + Z500_LONG_SLOPE * max(0.0, abs(z500) - 2.0)
    return clip_int(raw, 0, Z500_LONG_MAX_TARGET)


def local_long_target(drawdown: float) -> int:
    raw = LOCAL_LONG_BASE_TARGET + LOCAL_LONG_SLOPE * max(0.0, drawdown - LOCAL_BOTTOM_DRAWDOWN_TH)
    return clip_int(raw, 0, LOCAL_LONG_MAX_TARGET)


def local_short_target(rebound: float) -> int:
    raw = LOCAL_SHORT_BASE_TARGET + LOCAL_SHORT_SLOPE * max(0.0, rebound - LOCAL_TOP_REBOUND_TH)
    return clip_int(raw, 0, LOCAL_SHORT_MAX_TARGET)


def detect_confirmed_signal(
    mids: List[float],
    mid: float,
    anchor: Optional[float],
    z500: Optional[float],
    fast_z: Optional[float],
    local_low: Optional[float],
    local_high: Optional[float],
    mom5: Optional[float],
    mom10: Optional[float],
    mom20: Optional[float],
) -> Tuple[str, int, str]:
    """
    Returns active_side, signed_peak_target, reason.
    """
    if anchor is None or z500 is None:
        return "none", 0, "no_z500"

    candidates = []

    # LONG A: z500 low mean reversion.
    if z500 <= Z500_LONG_TH and ((mom10 is not None and mom10 > 0) or (mom20 is not None and mom20 > 0)):
        tgt = z500_long_target(z500)
        score = 10.0 + abs(z500)

        if mom20 is not None and mom20 > 0:
            score += 0.5

        candidates.append(("long", tgt, score, "z500_low_mom_confirm_long"))

    # LONG B: local bottom after large drawdown.
    if (
        local_high is not None
        and fast_z is not None
        and mom5 is not None
        and mom5 > 0
    ):
        drawdown = local_high - mid

        if (
            drawdown >= LOCAL_BOTTOM_DRAWDOWN_TH
            and fast_z <= -FAST_Z_TH
            and z500 < 1.0
        ):
            tgt = local_long_target(drawdown)

            if z500 <= -1.0:
                tgt = min(LOCAL_LONG_MAX_TARGET, int(tgt * 1.10))

            score = 7.0 + 0.04 * drawdown + 0.50 * abs(fast_z)
            candidates.append(("long", tgt, score, "local_bottom_rebound_long"))

    # SHORT: local top / rebound rollover.
    if (
        local_low is not None
        and fast_z is not None
        and mom5 is not None
        and mom5 < 0
    ):
        rebound = mid - local_low

        if (
            rebound >= LOCAL_TOP_REBOUND_TH
            and fast_z >= FAST_Z_TH
            and z500 > -1.0
        ):
            tgt = local_short_target(rebound)

            if z500 >= 0.5:
                tgt = min(LOCAL_SHORT_MAX_TARGET, int(tgt * 1.10))

            score = 8.0 + 0.04 * rebound + 0.50 * fast_z
            candidates.append(("short", -tgt, score, "local_top_rollover_short"))

    if not candidates:
        return "none", 0, "no_confirmed_signal"

    side, target, score, reason = max(candidates, key=lambda x: x[2])
    return side, target, reason


def update_active_signal(
    step: int,
    prev_side: str,
    prev_peak_target: int,
    prev_until: int,
    prev_reason: str,
    prev_start: int,
    new_side: str,
    new_peak_target: int,
    new_reason: str,
) -> Tuple[str, int, int, str, int]:
    """
    New confirmed signal creates or refreshes an active signal.

    Key anti-churn rule:
        Same-side lower target does not downgrade the active peak target.
        This prevents long->less-long causing active sells, and short->less-short
        causing active buys.

    Same-side stronger target upgrades the peak target but keeps the old signal
    start step, so the ramp does not restart every tick.
    Opposite-side signal resets the ramp.
    """
    if new_side in ("long", "short") and new_peak_target != 0:
        if prev_side == new_side:
            # Keep the more aggressive target on same side.
            if new_side == "long":
                peak = max(prev_peak_target, new_peak_target)
            else:
                peak = min(prev_peak_target, new_peak_target)

            start = prev_start if prev_start > -9_000 else step
            return new_side, peak, step + ACTIVE_HOLD_STEPS, new_reason, start

        # Opposite or fresh signal: reset ramp.
        return new_side, new_peak_target, step + ACTIVE_HOLD_STEPS, new_reason, step

    if prev_side in ("long", "short") and step <= prev_until:
        return prev_side, prev_peak_target, prev_until, "sticky_" + prev_reason, prev_start

    return "none", 0, -1, "no_active_target", -10_000


def allowed_target_from_ramp(
    step: int,
    active_side: str,
    peak_target: int,
    signal_start_step: int,
    mids: List[float],
) -> Tuple[int, float, bool]:
    """
    Convert full target into currently allowed target.

    If the price is still making adverse extremes, slow the ramp.
    This scatters trades and avoids adding too fast into a still-moving trend.
    """
    if active_side not in ("long", "short") or peak_target == 0:
        return 0, 0.0, False

    age = max(0, step - signal_start_step)
    frac = min(1.0, RAMP_START_FRAC + RAMP_PER_STEP * age)

    adverse = False
    if active_side == "long" and making_fresh_low(mids):
        frac *= ADVERSE_RAMP_MULT
        adverse = True
    elif active_side == "short" and making_fresh_high(mids):
        frac *= ADVERSE_RAMP_MULT
        adverse = True

    if active_side == "long":
        target = clip_int(peak_target * frac, 0, abs(peak_target))
    else:
        target = -clip_int(abs(peak_target) * frac, 0, abs(peak_target))

    return target, frac, adverse


def passive_desired_target(
    z500: Optional[float],
    active_side: str,
    allowed_active_target: int,
) -> Tuple[int, str]:
    """
    Passive target can use z500 even when active target is inactive.
    This biases maker quotes only. It never causes active crossing.
    """
    if active_side in ("long", "short") and allowed_active_target != 0:
        return allowed_active_target, "allowed_active_target"

    if z500 is None:
        return 0, "no_z"

    if z500 <= Z500_LONG_TH:
        return z500_long_target(z500), "passive_z500_long"

    # Keep short passive bias smaller; shorts mainly come from local_top_rollover.
    if z500 >= MILD_Z:
        return -clip_int(MILD_SHORT_SCALE * z500, 0, 60), "passive_mild_short"

    if z500 <= -MILD_Z:
        return clip_int(-MILD_LONG_SCALE * z500, 0, 75), "passive_mild_long"

    return 0, "neutral"


# ── ORDERS ──────────────────────────────────────────────────────────────────

def active_orders_to_target(
    depth: OrderDepth,
    position: int,
    active_side: str,
    allowed_target: int,
    anchor: Optional[float],
    step: int,
    last_active_step: int,
) -> Tuple[List[Order], int, int, str]:
    """
    Same-side churn fix:
        - long signal: only active BUY toward allowed target.
        - short signal: only active SELL toward allowed target.

    If already beyond allowed target on the same side, do nothing actively.
    Passive MM can manage reductions, but we do not cross the spread.
    """
    orders: List[Order] = []

    if active_side not in ("long", "short") or allowed_target == 0:
        return orders, position, last_active_step, "no_active_signal"

    if anchor is None:
        return orders, position, last_active_step, "no_anchor"

    if step - last_active_step < ACTIVE_COOLDOWN_STEPS:
        return orders, position, last_active_step, "cooldown"

    best_bid, best_ask = best_bid_ask(depth)
    if best_bid is None or best_ask is None:
        return orders, position, last_active_step, "empty_book"

    if active_side == "long":
        # Only buy. Never actively sell because a long target got smaller.
        gap = allowed_target - position
        if gap < MIN_ACTIVE_GAP:
            return orders, position, last_active_step, "long_no_buy_needed"

        visible_ask = max(0, -depth.sell_orders.get(best_ask, 0))
        qty = min(TAKER_CHUNK, gap, visible_ask, official_capacity(position, "BUY"))

        reducing_short = position < 0
        adding_long_ok = best_ask < anchor + BUY_MAX_ABOVE_ANCHOR

        if qty > 0 and (reducing_short or adding_long_ok):
            orders.append(Order(SYMBOL, int(best_ask), int(qty)))
            position += qty
            last_active_step = step
            return orders, position, last_active_step, "active_buy_to_ramped_long"

    elif active_side == "short":
        # Only sell. Never actively buy because a short target got smaller.
        gap = position - allowed_target
        if gap < MIN_ACTIVE_GAP:
            return orders, position, last_active_step, "short_no_sell_needed"

        visible_bid = max(0, depth.buy_orders.get(best_bid, 0))
        qty = min(TAKER_CHUNK, gap, visible_bid, official_capacity(position, "SELL"))

        reducing_long = position > 0
        adding_short_ok = best_bid > anchor - SELL_MAX_BELOW_ANCHOR

        if qty > 0 and (reducing_long or adding_short_ok):
            orders.append(Order(SYMBOL, int(best_bid), -int(qty)))
            position -= qty
            last_active_step = step
            return orders, position, last_active_step, "active_sell_to_ramped_short"

    return orders, position, last_active_step, "edge_guard_blocked"


def passive_side_size(position_after_active: int, desired_target: int, side: str) -> int:
    pos = position_after_active
    gap = desired_target - pos

    if side == "BUY" and pos >= HARD_PASSIVE_CAP:
        return 0
    if side == "SELL" and pos <= -HARD_PASSIVE_CAP:
        return 0

    # Bias passive orders toward desired target.
    if gap > MIN_ACTIVE_GAP:
        if side == "BUY":
            return BASE_MM_SIZE
        # If we are above target, allow passive asks to reduce; otherwise suppress.
        if pos <= desired_target:
            return 0
        return max(1, BASE_MM_SIZE // 4)

    if gap < -MIN_ACTIVE_GAP:
        if side == "SELL":
            return BASE_MM_SIZE
        # If we are below target, allow passive bids to reduce; otherwise suppress.
        if pos >= desired_target:
            return 0
        return max(1, BASE_MM_SIZE // 4)

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
        mids = [float(x) for x in s.get("mids", [])][-Z_WINDOW:]

        last_active_step = int(s.get("last_active_step", -10_000))
        prev_active_side = str(s.get("active_side", "none"))
        prev_peak_target = int(s.get("active_peak_target", 0))
        prev_active_until = int(s.get("active_until", -1))
        prev_active_reason = str(s.get("active_reason", "none"))
        prev_signal_start = int(s.get("signal_start_step", -10_000))

        if SYMBOL not in state.order_depths:
            return result, 0, save_state(
                step + 1,
                mids,
                last_active_step,
                prev_active_side,
                prev_peak_target,
                prev_active_until,
                prev_active_reason,
                prev_signal_start,
            )

        depth = state.order_depths[SYMBOL]
        position = state.position.get(SYMBOL, 0)

        best_bid, best_ask = best_bid_ask(depth)
        mid = mid_price(depth)

        if best_bid is None or best_ask is None or mid is None:
            result[SYMBOL] = []
            return result, 0, save_state(
                step + 1,
                mids,
                last_active_step,
                prev_active_side,
                prev_peak_target,
                prev_active_until,
                prev_active_reason,
                prev_signal_start,
            )

        mids.append(float(mid))
        if len(mids) > Z_WINDOW:
            mids = mids[-Z_WINDOW:]

        anchor, sigma = rolling_stats(mids, Z_WINDOW)
        z500 = None
        if anchor is not None and sigma is not None and len(mids) >= MIN_ACTIVE_HISTORY:
            z500 = (mid - anchor) / max(sigma, 1e-12)

        fast_mean, fast_sigma = rolling_stats(mids, FAST_WINDOW)
        fast_z = None
        if fast_mean is not None and fast_sigma is not None:
            fast_z = (mid - fast_mean) / max(fast_sigma, 1e-12)

        local_low, local_high = rolling_low_high(mids, LOCAL_WINDOW)

        mom5 = recent_momentum(mids, MOM_FAST)
        mom10 = recent_momentum(mids, MOM_MED)
        mom20 = recent_momentum(mids, MOM_SLOW)

        new_side, new_peak_target, new_reason = detect_confirmed_signal(
            mids=mids,
            mid=mid,
            anchor=anchor,
            z500=z500,
            fast_z=fast_z,
            local_low=local_low,
            local_high=local_high,
            mom5=mom5,
            mom10=mom10,
            mom20=mom20,
        )

        active_side, peak_target, active_until, active_reason, signal_start_step = update_active_signal(
            step=step,
            prev_side=prev_active_side,
            prev_peak_target=prev_peak_target,
            prev_until=prev_active_until,
            prev_reason=prev_active_reason,
            prev_start=prev_signal_start,
            new_side=new_side,
            new_peak_target=new_peak_target,
            new_reason=new_reason,
        )

        allowed_target, ramp_frac, adverse_throttle = allowed_target_from_ramp(
            step=step,
            active_side=active_side,
            peak_target=peak_target,
            signal_start_step=signal_start_step,
            mids=mids,
        )

        desired_target, desired_reason = passive_desired_target(z500, active_side, allowed_target)

        orders: List[Order] = []

        active_orders, pos_after_active, last_active_step, active_order_reason = active_orders_to_target(
            depth=depth,
            position=position,
            active_side=active_side,
            allowed_target=allowed_target,
            anchor=anchor,
            step=step,
            last_active_step=last_active_step,
        )
        orders.extend(active_orders)

        orders.extend(passive_mm_orders(
            depth=depth,
            original_position=position,
            position_after_active=pos_after_active,
            desired_target=desired_target,
            existing_orders=orders,
        ))

        result[SYMBOL] = orders

        if VERBOSE:
            z_s = "None" if z500 is None else f"{z500:.2f}"
            fz_s = "None" if fast_z is None else f"{fast_z:.2f}"
            anchor_s = "None" if anchor is None else f"{anchor:.2f}"
            m5_s = "None" if mom5 is None else f"{mom5:.1f}"
            m10_s = "None" if mom10 is None else f"{mom10:.1f}"
            m20_s = "None" if mom20 is None else f"{mom20:.1f}"
            rebound_s = "None" if local_low is None else f"{mid - local_low:.1f}"
            drawdown_s = "None" if local_high is None else f"{local_high - mid:.1f}"
            spread = best_ask - best_bid

            print(
                f"[HYDRO_SIMPLE_MR_V5_RAMP] ts={state.timestamp} step={step} "
                f"mid={mid:.1f} anchor={anchor_s} z500={z_s} fast_z={fz_s} "
                f"mom5={m5_s} mom10={m10_s} mom20={m20_s} "
                f"rebound200={rebound_s} drawdown200={drawdown_s} spread={spread} "
                f"pos={position} active_side={active_side} peak_target={peak_target} "
                f"allowed_target={allowed_target} ramp_frac={ramp_frac:.2f} "
                f"adverse_throttle={adverse_throttle} active_until={active_until} "
                f"active_reason={active_reason} new_signal={new_reason} "
                f"desired_target={desired_target} desired_reason={desired_reason} "
                f"active_order={active_order_reason} pos_after={pos_after_active} orders={orders}"
            )

        return result, 0, save_state(
            step + 1,
            mids,
            last_active_step,
            active_side,
            peak_target,
            active_until,
            active_reason,
            signal_start_step,
        )