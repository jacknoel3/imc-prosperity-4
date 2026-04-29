from __future__ import annotations

"""
HYDROGEL_PACK — scaled high-conviction mean-reversion strategy.

Design goal:
- Replace weak trend-following probes with cleaner high-conviction reversal signals.
- Use rolling z500 / fast-z100 / local high-low structure, inspired by the older
  high-PnL Hydrogel strategy, but scaled down for Round 4 robustness.
- Trade both sides: real long signals after overextended drops and real short
  signals after overextended rebounds/tops.
- Keep passive market making around the desired target position.

Core signals:
1. z500_low_mom_confirm_long
2. z500_high_mom_confirm_short
3. local_bottom_rebound_long
4. local_top_rollover_short

No tiny trend-following probes.
No option/VELVETFRUIT trading.
"""

import json
import math
from typing import Any, Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ModuleNotFoundError:
    from prosperity3bt.datamodel import Order, OrderDepth, TradingState


SYMBOL = "HYDROGEL_PACK"
POSITION_LIMIT = 200

# ── Windows ──────────────────────────────────────────────────────────────────
Z_WINDOW = 500
FAST_WINDOW = 100
LOCAL_WINDOW = 200
MIN_ACTIVE_HISTORY = 300

MOM_FAST = 5
MOM_MED = 10
MOM_SLOW = 20

# ── Signal thresholds ────────────────────────────────────────────────────────
# Main slow-anchor z-score signals.
Z500_LONG_TH = -2.00
Z500_SHORT_TH = 2.00

# Local top/bottom structure.
LOCAL_BOTTOM_DRAWDOWN_TH = 58.0
LOCAL_TOP_REBOUND_TH = 52.0
FAST_Z_TH = 1.00

# Active target persistence.
ACTIVE_HOLD_STEPS = 42
ACTIVE_COOLDOWN_STEPS = 1
MIN_ACTIVE_GAP = 8
TAKER_CHUNK = 12

# Target sizing: deliberately below the old aggressive strategy, but large
# enough to matter.
Z_LONG_BASE_TARGET = 85
Z_LONG_MAX_TARGET = 135
Z_LONG_SLOPE = 28

Z_SHORT_BASE_TARGET = 80
Z_SHORT_MAX_TARGET = 125
Z_SHORT_SLOPE = 25

LOCAL_LONG_BASE_TARGET = 75
LOCAL_LONG_MAX_TARGET = 120
LOCAL_LONG_SLOPE = 0.75

LOCAL_SHORT_BASE_TARGET = 80
LOCAL_SHORT_MAX_TARGET = 125
LOCAL_SHORT_SLOPE = 0.80

# Edge guards for active crossing. These prevent paying the spread too far away
# from the slow anchor unless we are reducing existing wrong-way inventory.
BUY_MAX_ABOVE_ANCHOR = 8.0
SELL_MAX_BELOW_ANCHOR = 12.0

# Passive market making.
INSIDE_OFFSET = 1
BASE_MM_SIZE = 14
MIN_SPREAD_MM = 7
MAX_SPREAD_MM = 18
HARD_PASSIVE_CAP = 185
PASSIVE_SMALL_EXIT_SIZE = 4

# Passive mild target from z500 when no active target exists.
MILD_Z = 1.00
MILD_LONG_SCALE = 26
MILD_SHORT_SCALE = 24
MILD_TARGET_CAP = 55

# Simple profit/risk target decay. These avoid getting stuck with a stale target
# after the move has already reverted.
NEUTRAL_Z_EXIT = 0.30
STALE_TARGET_MOM_AGAINST = 6.0

# Debug. Keep false for submission.
VERBOSE = False


# ── State ────────────────────────────────────────────────────────────────────

def load_state(trader_data: str) -> Dict[str, Any]:
    default = {
        "step": 0,
        "mids": [],
        "last_active_step": -10_000,
        "active_side": "none",
        "active_target": 0,
        "active_until": -1,
        "active_reason": "none",
        "active_z_entry": 0.0,
    }
    if not trader_data:
        return default
    try:
        parsed = json.loads(trader_data)
        if not isinstance(parsed, dict):
            return default
        for key, value in default.items():
            parsed.setdefault(key, value)
        if not isinstance(parsed.get("mids"), list):
            parsed["mids"] = []
        return parsed
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
    active_z_entry: float,
) -> str:
    if len(mids) > Z_WINDOW:
        mids = mids[-Z_WINDOW:]
    compact_mids = [round(float(x), 3) for x in mids]
    return json.dumps(
        {
            "step": int(step),
            "mids": compact_mids,
            "last_active_step": int(last_active_step),
            "active_side": str(active_side),
            "active_target": int(active_target),
            "active_until": int(active_until),
            "active_reason": str(active_reason),
            "active_z_entry": float(active_z_entry),
        },
        separators=(",", ":"),
    )


# ── Helpers ──────────────────────────────────────────────────────────────────

def mean_std(xs: List[float]) -> Tuple[Optional[float], Optional[float]]:
    if len(xs) < 2:
        return None, None
    mean = sum(xs) / len(xs)
    variance = sum((x - mean) ** 2 for x in xs) / (len(xs) - 1)
    return mean, math.sqrt(max(variance, 1e-12))


def clip_int(value: float, lo: int, hi: int) -> int:
    return int(max(lo, min(hi, round(value))))


def best_bid_ask(depth: OrderDepth) -> Tuple[Optional[int], Optional[int]]:
    if not depth.buy_orders or not depth.sell_orders:
        return None, None
    return max(depth.buy_orders), min(depth.sell_orders)


def visible_best_volumes(depth: OrderDepth) -> Tuple[int, int]:
    best_bid, best_ask = best_bid_ask(depth)
    if best_bid is None or best_ask is None:
        return 0, 0
    bid_vol = max(0, int(depth.buy_orders.get(best_bid, 0)))
    ask_vol = max(0, -int(depth.sell_orders.get(best_ask, 0)))
    return bid_vol, ask_vol


def mid_price(depth: OrderDepth) -> Optional[float]:
    best_bid, best_ask = best_bid_ask(depth)
    if best_bid is None or best_ask is None:
        return None
    return (best_bid + best_ask) / 2.0


def capacity(position: int, side: str) -> int:
    if side == "BUY":
        return max(0, POSITION_LIMIT - position)
    return max(0, POSITION_LIMIT + position)


def rolling_stats(xs: List[float], window: int) -> Tuple[Optional[float], Optional[float]]:
    if len(xs) < window:
        return None, None
    return mean_std(xs[-window:])


def rolling_low_high(xs: List[float], window: int) -> Tuple[Optional[float], Optional[float]]:
    if len(xs) < window:
        return None, None
    recent = xs[-window:]
    return min(recent), max(recent)


def momentum(xs: List[float], window: int) -> Optional[float]:
    if len(xs) <= window:
        return None
    return xs[-1] - xs[-1 - window]


# ── Target sizing ────────────────────────────────────────────────────────────

def z_long_target(z500: float) -> int:
    raw = Z_LONG_BASE_TARGET + Z_LONG_SLOPE * max(0.0, abs(z500) - abs(Z500_LONG_TH))
    return clip_int(raw, 0, Z_LONG_MAX_TARGET)


def z_short_target(z500: float) -> int:
    raw = Z_SHORT_BASE_TARGET + Z_SHORT_SLOPE * max(0.0, z500 - Z500_SHORT_TH)
    return clip_int(raw, 0, Z_SHORT_MAX_TARGET)


def local_long_target(drawdown: float) -> int:
    raw = LOCAL_LONG_BASE_TARGET + LOCAL_LONG_SLOPE * max(0.0, drawdown - LOCAL_BOTTOM_DRAWDOWN_TH)
    return clip_int(raw, 0, LOCAL_LONG_MAX_TARGET)


def local_short_target(rebound: float) -> int:
    raw = LOCAL_SHORT_BASE_TARGET + LOCAL_SHORT_SLOPE * max(0.0, rebound - LOCAL_TOP_REBOUND_TH)
    return clip_int(raw, 0, LOCAL_SHORT_MAX_TARGET)


# ── Signal detection ─────────────────────────────────────────────────────────

def detect_signal(
    mid: float,
    z500: Optional[float],
    fast_z: Optional[float],
    local_low: Optional[float],
    local_high: Optional[float],
    mom5: Optional[float],
    mom10: Optional[float],
    mom20: Optional[float],
) -> Tuple[str, int, str, float]:
    """Return side, signed target, reason, score."""
    candidates: List[Tuple[str, int, str, float]] = []

    # LONG A: slow z-score extreme with medium confirmation.
    if (
        z500 is not None
        and z500 <= Z500_LONG_TH
        and ((mom10 is not None and mom10 > 0) or (mom20 is not None and mom20 > 0))
    ):
        target = z_long_target(z500)
        score = 10.0 + abs(z500)
        if mom20 is not None and mom20 > 0:
            score += 0.5
        candidates.append(("long", target, "z500_low_mom_confirm_long", score))

    # SHORT A: symmetric slow z-score extreme with medium confirmation.
    if (
        z500 is not None
        and z500 >= Z500_SHORT_TH
        and ((mom10 is not None and mom10 < 0) or (mom20 is not None and mom20 < 0))
    ):
        target = -z_short_target(z500)
        score = 9.5 + z500
        if mom20 is not None and mom20 < 0:
            score += 0.5
        candidates.append(("short", target, "z500_high_mom_confirm_short", score))

    # LONG B: local bottom after a large drawdown, but only once bounce starts.
    if local_high is not None and fast_z is not None and mom5 is not None:
        drawdown = local_high - mid
        if (
            drawdown >= LOCAL_BOTTOM_DRAWDOWN_TH
            and fast_z <= -FAST_Z_TH
            and mom5 > 0
            and (z500 is None or z500 < 1.0)
        ):
            target = local_long_target(drawdown)
            if z500 is not None and z500 <= -1.0:
                target = min(LOCAL_LONG_MAX_TARGET, int(target * 1.08))
            score = 7.0 + 0.04 * drawdown + 0.50 * abs(fast_z)
            candidates.append(("long", target, "local_bottom_rebound_long", score))

    # SHORT B: local top after a large rebound, once rollover starts.
    if local_low is not None and fast_z is not None and mom5 is not None:
        rebound = mid - local_low
        if (
            rebound >= LOCAL_TOP_REBOUND_TH
            and fast_z >= FAST_Z_TH
            and mom5 < 0
            and (z500 is None or z500 > -1.0)
        ):
            target = -local_short_target(rebound)
            if z500 is not None and z500 >= 0.5:
                target = max(-LOCAL_SHORT_MAX_TARGET, int(target * 1.08))
            score = 8.0 + 0.04 * rebound + 0.50 * fast_z
            candidates.append(("short", target, "local_top_rollover_short", score))

    if not candidates:
        return "none", 0, "no_confirmed_signal", 0.0

    side, target, reason, score = max(candidates, key=lambda x: x[3])
    return side, target, reason, score


# ── Active target management ─────────────────────────────────────────────────

def update_active_target(
    step: int,
    previous_side: str,
    previous_target: int,
    previous_until: int,
    previous_reason: str,
    new_side: str,
    new_target: int,
    new_reason: str,
    position: int,
    z500: Optional[float],
    mom10: Optional[float],
    mom20: Optional[float],
) -> Tuple[str, int, int, str]:
    if new_side in {"long", "short"} and new_target != 0:
        return new_side, new_target, step + ACTIVE_HOLD_STEPS, new_reason

    if previous_side in {"long", "short"} and step <= previous_until:
        # Kill stale active targets when the signal has normalized or momentum is
        # clearly against the position. This prevents the old target from being
        # blindly pursued too late.
        if z500 is not None and abs(z500) <= NEUTRAL_Z_EXIT:
            return "none", 0, -1, "active_target_expired_neutral_z"

        if previous_side == "long":
            against = (mom10 is not None and mom10 <= -STALE_TARGET_MOM_AGAINST) or (
                mom20 is not None and mom20 <= -STALE_TARGET_MOM_AGAINST
            )
            if position > 0 and against:
                return "none", 0, -1, "active_target_expired_long_mom_against"
        elif previous_side == "short":
            against = (mom10 is not None and mom10 >= STALE_TARGET_MOM_AGAINST) or (
                mom20 is not None and mom20 >= STALE_TARGET_MOM_AGAINST
            )
            if position < 0 and against:
                return "none", 0, -1, "active_target_expired_short_mom_against"

        return previous_side, previous_target, previous_until, "sticky_" + previous_reason

    return "none", 0, -1, "no_active_target"


def passive_desired_target(z500: Optional[float], active_side: str, active_target: int) -> Tuple[int, str]:
    if active_side in {"long", "short"} and active_target != 0:
        return active_target, "active_target"

    if z500 is None:
        return 0, "no_z"

    if z500 <= Z500_LONG_TH:
        return z_long_target(z500), "passive_z500_long"
    if z500 >= Z500_SHORT_TH:
        return -z_short_target(z500), "passive_z500_short"
    if z500 <= -MILD_Z:
        return clip_int(-MILD_LONG_SCALE * z500, 0, MILD_TARGET_CAP), "passive_mild_long"
    if z500 >= MILD_Z:
        return -clip_int(MILD_SHORT_SCALE * z500, 0, MILD_TARGET_CAP), "passive_mild_short"

    return 0, "neutral"


def dynamic_chunk(active_side: str, current_z: Optional[float], entry_z: float) -> int:
    if current_z is None:
        return max(4, TAKER_CHUNK // 2)

    if active_side == "long":
        strength_delta = entry_z - current_z  # positive: cheaper than entry signal
    else:
        strength_delta = current_z - entry_z  # positive: more expensive than short signal entry

    if strength_delta > 0.35:
        return TAKER_CHUNK
    if strength_delta > -0.10:
        return max(4, TAKER_CHUNK * 2 // 3)
    if strength_delta > -0.45:
        return max(2, TAKER_CHUNK // 3)
    return max(1, TAKER_CHUNK // 6)


# ── Orders ───────────────────────────────────────────────────────────────────

def active_orders_to_target(
    depth: OrderDepth,
    position: int,
    active_side: str,
    active_target: int,
    anchor: Optional[float],
    step: int,
    last_active_step: int,
    active_z_entry: float,
    current_z: Optional[float],
) -> Tuple[List[Order], int, int, str]:
    orders: List[Order] = []

    if active_side not in {"long", "short"} or active_target == 0:
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

    bid_volume, ask_volume = visible_best_volumes(depth)
    chunk = dynamic_chunk(active_side, current_z, active_z_entry)

    if gap > 0:
        qty = min(chunk, gap, ask_volume, capacity(position, "BUY"))
        reducing_short = position < 0
        adding_long_ok = best_ask <= anchor + BUY_MAX_ABOVE_ANCHOR
        if qty > 0 and (reducing_short or adding_long_ok):
            orders.append(Order(SYMBOL, int(best_ask), int(qty)))
            return orders, position + qty, step, f"active_buy(chunk={chunk})"
        return orders, position, last_active_step, "buy_edge_guard_blocked"

    if gap < 0:
        qty = min(chunk, -gap, bid_volume, capacity(position, "SELL"))
        reducing_long = position > 0
        adding_short_ok = best_bid >= anchor - SELL_MAX_BELOW_ANCHOR
        if qty > 0 and (reducing_long or adding_short_ok):
            orders.append(Order(SYMBOL, int(best_bid), -int(qty)))
            return orders, position - qty, step, f"active_sell(chunk={chunk})"
        return orders, position, last_active_step, "sell_edge_guard_blocked"

    return orders, position, last_active_step, "no_gap"


def passive_side_size(position_after_active: int, desired_target: int, side: str) -> int:
    pos = position_after_active
    gap = desired_target - pos

    if side == "BUY" and pos >= HARD_PASSIVE_CAP:
        return 0
    if side == "SELL" and pos <= -HARD_PASSIVE_CAP:
        return 0

    if gap > MIN_ACTIVE_GAP:
        if side == "BUY":
            return BASE_MM_SIZE
        # Still offer a small exit/market-making ask if we are already above target.
        if pos <= desired_target:
            return 0
        return PASSIVE_SMALL_EXIT_SIZE

    if gap < -MIN_ACTIVE_GAP:
        if side == "SELL":
            return BASE_MM_SIZE
        if pos >= desired_target:
            return 0
        return PASSIVE_SMALL_EXIT_SIZE

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

    bid_price = best_bid + INSIDE_OFFSET
    ask_price = best_ask - INSIDE_OFFSET
    if bid_price >= ask_price:
        return orders

    has_active_buy = any(order.quantity > 0 and order.price >= best_ask for order in existing_orders)
    has_active_sell = any(order.quantity < 0 and order.price <= best_bid for order in existing_orders)

    used_buy = sum(order.quantity for order in existing_orders if order.quantity > 0)
    used_sell = sum(-order.quantity for order in existing_orders if order.quantity < 0)

    buy_cap = max(0, capacity(original_position, "BUY") - used_buy)
    sell_cap = max(0, capacity(original_position, "SELL") - used_sell)

    if not has_active_sell:
        qty = min(passive_side_size(position_after_active, desired_target, "BUY"), buy_cap)
        if qty > 0:
            orders.append(Order(SYMBOL, int(bid_price), int(qty)))

    if not has_active_buy:
        qty = min(passive_side_size(position_after_active, desired_target, "SELL"), sell_cap)
        if qty > 0:
            orders.append(Order(SYMBOL, int(ask_price), -int(qty)))

    return orders


# ── Trader ───────────────────────────────────────────────────────────────────

class Trader:
    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {}

        saved = load_state(getattr(state, "traderData", ""))
        step = int(saved.get("step", 0))
        mids = [float(x) for x in saved.get("mids", [])][-Z_WINDOW:]

        last_active_step = int(saved.get("last_active_step", -10_000))
        prev_active_side = str(saved.get("active_side", "none"))
        prev_active_target = int(saved.get("active_target", 0))
        prev_active_until = int(saved.get("active_until", -1))
        prev_active_reason = str(saved.get("active_reason", "none"))
        active_z_entry = float(saved.get("active_z_entry", 0.0))

        depth = state.order_depths.get(SYMBOL)
        if depth is None:
            return result, 0, save_state(
                step + 1,
                mids,
                last_active_step,
                prev_active_side,
                prev_active_target,
                prev_active_until,
                prev_active_reason,
                active_z_entry,
            )

        mid = mid_price(depth)
        if mid is None:
            result[SYMBOL] = []
            return result, 0, save_state(
                step + 1,
                mids,
                last_active_step,
                prev_active_side,
                prev_active_target,
                prev_active_until,
                prev_active_reason,
                active_z_entry,
            )

        position = int(state.position.get(SYMBOL, 0))
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
        mom5 = momentum(mids, MOM_FAST)
        mom10 = momentum(mids, MOM_MED)
        mom20 = momentum(mids, MOM_SLOW)

        new_side, new_target, new_reason, _score = detect_signal(
            mid=mid,
            z500=z500,
            fast_z=fast_z,
            local_low=local_low,
            local_high=local_high,
            mom5=mom5,
            mom10=mom10,
            mom20=mom20,
        )

        active_side, active_target, active_until, active_reason = update_active_target(
            step=step,
            previous_side=prev_active_side,
            previous_target=prev_active_target,
            previous_until=prev_active_until,
            previous_reason=prev_active_reason,
            new_side=new_side,
            new_target=new_target,
            new_reason=new_reason,
            position=position,
            z500=z500,
            mom10=mom10,
            mom20=mom20,
        )

        if new_side in {"long", "short"} and new_target != 0:
            active_z_entry = float(z500 if z500 is not None else 0.0)

        desired_target, desired_reason = passive_desired_target(z500, active_side, active_target)

        orders: List[Order] = []
        active_orders, pos_after_active, last_active_step, active_order_reason = active_orders_to_target(
            depth=depth,
            position=position,
            active_side=active_side,
            active_target=active_target,
            anchor=anchor,
            step=step,
            last_active_step=last_active_step,
            active_z_entry=active_z_entry,
            current_z=z500,
        )
        orders.extend(active_orders)

        orders.extend(
            passive_mm_orders(
                depth=depth,
                original_position=position,
                position_after_active=pos_after_active,
                desired_target=desired_target,
                existing_orders=orders,
            )
        )

        result[SYMBOL] = orders

        if VERBOSE:
            z_s = "None" if z500 is None else f"{z500:.2f}"
            fz_s = "None" if fast_z is None else f"{fast_z:.2f}"
            m5_s = "None" if mom5 is None else f"{mom5:.1f}"
            m10_s = "None" if mom10 is None else f"{mom10:.1f}"
            m20_s = "None" if mom20 is None else f"{mom20:.1f}"
            rebound = None if local_low is None else mid - local_low
            drawdown = None if local_high is None else local_high - mid
            reb_s = "None" if rebound is None else f"{rebound:.1f}"
            dd_s = "None" if drawdown is None else f"{drawdown:.1f}"
            print(
                f"[HYDRO_V4_SCALED] ts={state.timestamp} step={step} mid={mid:.1f} "
                f"z500={z_s} fast_z={fz_s} mom5={m5_s} mom10={m10_s} mom20={m20_s} "
                f"rebound200={reb_s} drawdown200={dd_s} pos={position} "
                f"active={active_side}:{active_target} reason={active_reason} "
                f"new={new_reason} desired={desired_target}({desired_reason}) "
                f"active_order={active_order_reason} orders={orders}"
            )

        return result, 0, save_state(
            step=step + 1,
            mids=mids,
            last_active_step=last_active_step,
            active_side=active_side,
            active_target=active_target,
            active_until=active_until,
            active_reason=active_reason,
            active_z_entry=active_z_entry,
        )
