"""
HYDROGEL_PACK — Z500 Target MR v3
IMC Prosperity 4 | Round 3

Purpose:
    Robust mean-reversion target bot with active taking only on confirmed signals.

What this version tests:
    - Use rolling z500 as the main fair-value / desired-position signal.
    - Use momentum/fast-z/local-extension only for timing active crossing.
    - Allow real long and short positioning.
    - Do NOT actively flatten just because target decays.
    - Do NOT force final inventory to zero. Remaining inventory is marked to fair/mid.

Core logic:
    1. Passive inside market making:
        bid = best_bid + 1
        ask = best_ask - 1

    2. Main directional signal:
        z500 < -2  -> product is cheap -> prefer long
        z500 > +2  -> product is rich  -> prefer short

    3. Active crossing:
        only after confirmed timing:
            cheap + mom5 > 0 -> active buy toward long target
            rich + mom5 < 0  -> active sell toward short target
            local selloff + rebound -> active long
            local rebound + rollover -> active short

    4. Position sizing:
        long target:  roughly +115 to +170
        short target: roughly -95 to -150
        local targets: roughly +/-100 to +/-150

    5. Holding:
        active targets are sticky for a limited build window.
        after the signal expires, the bot does not cross the spread just to
        flatten. It may keep inventory if still reasonable.

Set VERBOSE=False before final submission.
"""

from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Optional, Tuple
import json
import math


# ── PARAMETERS ──────────────────────────────────────────────────────────────

SYMBOL = "HYDROGEL_PACK"
POSITION_LIMIT = 200

# Signal windows
SLOW_WINDOW = 500
FAST_WINDOW = 100
LOCAL_WINDOW = 200
MIN_HISTORY_FOR_SIGNAL = 500

# Core z500 thresholds
Z_STRONG = 2.0
Z_VERY_STRONG = 2.6
Z_MILD = 1.0

# Fast/local overextension thresholds
FAST_Z_TRIGGER = 1.15
LOCAL_MOVE_TRIGGER = 42.0

# Momentum timing
MOM_FAST_WINDOW = 5
MOM_CONTEXT_WINDOW = 20

# Active target persistence
ACTIVE_SIGNAL_HOLD_STEPS = 35       # 35 observations = 3,500 timestamp units
REFRESH_ACTIVE_SIGNAL = True

# Position targets
LONG_BASE_TARGET = 115
LONG_MAX_TARGET = 170
LONG_SLOPE_PER_Z = 42

SHORT_BASE_TARGET = 95
SHORT_MAX_TARGET = 150
SHORT_SLOPE_PER_Z = 34

LOCAL_BASE_TARGET = 105
LOCAL_MAX_TARGET = 155
LOCAL_SLOPE_PER_TICK = 1.25

# Active trading
TAKER_CHUNK = 16
ACTIVE_COOLDOWN_STEPS = 1
MIN_ACTIVE_GAP = 8

# Edge guards. These are guardrails, not profit targets.
# They stop terrible chasing, while still allowing real position switching.
BUY_MAX_ABOVE_ANCHOR = 10.0       # adding long allowed if ask < anchor + 10
SELL_MAX_BELOW_ANCHOR = 18.0      # adding short allowed if bid > anchor - 18

# Passive MM
INSIDE_OFFSET = 1
BASE_MM_SIZE = 20
MIN_SPREAD_MM = 7
MAX_SPREAD_MM = 17

# Passive safety near limits
HARD_PASSIVE_CAP = 185

VERBOSE = True


# ── STATE HELPERS ───────────────────────────────────────────────────────────

def load_state(trader_data: str) -> dict:
    default = {
        "step": 0,
        "mids": [],
        "last_active_step": -10_000,
        "active_target": 0,
        "active_side": "none",      # "long", "short", "none"
        "active_until": -1,
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
    active_target: int,
    active_side: str,
    active_until: int,
) -> str:
    if len(mids) > SLOW_WINDOW:
        mids = mids[-SLOW_WINDOW:]

    return json.dumps({
        "step": int(step),
        "mids": mids,
        "last_active_step": int(last_active_step),
        "active_target": int(active_target),
        "active_side": str(active_side),
        "active_until": int(active_until),
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


def recent_momentum(mids: List[float], window: int) -> Optional[float]:
    if len(mids) <= window:
        return None
    return mids[-1] - mids[-1 - window]


def rolling_stats(mids: List[float], window: int) -> Tuple[Optional[float], Optional[float]]:
    if len(mids) < window:
        return None, None
    return mean_std(mids[-window:])


def rolling_low_high(mids: List[float], window: int) -> Tuple[Optional[float], Optional[float]]:
    if len(mids) < window:
        return None, None
    xs = mids[-window:]
    return min(xs), max(xs)


# ── TARGET SIZING ───────────────────────────────────────────────────────────

def z_long_target(z: float) -> int:
    # z is negative. More negative => larger long target.
    az = abs(z)
    raw = LONG_BASE_TARGET + LONG_SLOPE_PER_Z * max(0.0, az - Z_STRONG)
    return clip_int(raw, 0, LONG_MAX_TARGET)


def z_short_target(z: float) -> int:
    # z is positive. More positive => larger short target.
    raw = SHORT_BASE_TARGET + SHORT_SLOPE_PER_Z * max(0.0, z - Z_STRONG)
    return clip_int(raw, 0, SHORT_MAX_TARGET)


def local_target(extension: float) -> int:
    raw = LOCAL_BASE_TARGET + LOCAL_SLOPE_PER_TICK * max(0.0, extension - LOCAL_MOVE_TRIGGER)
    return clip_int(raw, 0, LOCAL_MAX_TARGET)


# ── SIGNAL DETECTION ────────────────────────────────────────────────────────

def detect_active_signal(
    mid: float,
    best_bid: int,
    best_ask: int,
    slow_anchor: Optional[float],
    z500: Optional[float],
    fast_mean: Optional[float],
    fast_std: Optional[float],
    local_low: Optional[float],
    local_high: Optional[float],
    mom5: Optional[float],
    mom20: Optional[float],
) -> Tuple[str, int, str]:
    """
    Detect confirmed active signal.
    Returns side, signed_target, reason.
    """
    if slow_anchor is None or z500 is None or mom5 is None:
        return "none", 0, "no_signal"

    z_fast = None
    if fast_mean is not None and fast_std is not None:
        z_fast = (mid - fast_mean) / max(fast_std, 1e-12)

    candidates = []

    # 1) Core z500 long: cheap and bounce has started.
    if z500 <= -Z_STRONG and mom5 > 0:
        tgt = z_long_target(z500)

        # If recent rebound is already very large, avoid chasing full size.
        if mom20 is not None and mom20 > 45:
            tgt = int(tgt * 0.75)

        score = 5.0 + abs(z500)
        candidates.append(("long", tgt, score, "z500_low_rebound_long"))

    # 2) Core z500 short: rich and rollover has started.
    if z500 >= Z_STRONG and mom5 < 0:
        tgt = z_short_target(z500)

        # If recent drop is already very large, avoid chasing full size.
        if mom20 is not None and mom20 < -45:
            tgt = int(tgt * 0.75)

        score = 5.0 + abs(z500)
        candidates.append(("short", -tgt, score, "z500_high_rollover_short"))

    # 3) Local selloff/bottom rebound long.
    # This helps when z500 is not extreme enough yet but local oversold rebound is clear.
    if local_high is not None and z_fast is not None and mom5 > 0:
        drawdown = local_high - mid

        if (
            drawdown >= LOCAL_MOVE_TRIGGER
            and z_fast <= -FAST_Z_TRIGGER
            and z500 < 0.90
            and best_ask < slow_anchor + BUY_MAX_ABOVE_ANCHOR
        ):
            tgt = local_target(drawdown)

            # If z500 is also cheap, permit more conviction.
            if z500 < -1.0:
                tgt = min(LONG_MAX_TARGET, int(tgt * 1.10))

            score = 3.5 + 0.03 * drawdown + 0.50 * abs(z_fast)
            candidates.append(("long", tgt, score, "local_selloff_rebound_long"))

    # 4) Local rebound/top rollover short.
    # This is explicitly designed for the "top after rebound" problem.
    if local_low is not None and z_fast is not None and mom5 < 0:
        rebound = mid - local_low

        if (
            rebound >= LOCAL_MOVE_TRIGGER
            and z_fast >= FAST_Z_TRIGGER
            and z500 > -0.90
            and best_bid > slow_anchor - SELL_MAX_BELOW_ANCHOR
        ):
            tgt = local_target(rebound)

            # If z500 is also rich, permit more conviction.
            if z500 > 1.0:
                tgt = min(SHORT_MAX_TARGET, int(tgt * 1.10))

            score = 3.5 + 0.03 * rebound + 0.50 * z_fast
            candidates.append(("short", -tgt, score, "local_rebound_rollover_short"))

    if not candidates:
        return "none", 0, "no_new_active_signal"

    # Pick highest-confidence candidate.
    side, signed_target, score, reason = max(candidates, key=lambda x: x[2])
    return side, signed_target, reason


def update_active_target(
    step: int,
    prev_side: str,
    prev_until: int,
    prev_target: int,
    new_side: str,
    new_target: int,
    new_reason: str,
) -> Tuple[str, int, int, str]:
    """
    Confirmed signal sets a sticky active target.
    If no new signal, keep previous active target only until expiry.
    """
    if new_side in ("long", "short") and new_target != 0:
        if REFRESH_ACTIVE_SIGNAL or new_side != prev_side:
            return new_side, step + ACTIVE_SIGNAL_HOLD_STEPS, new_target, new_reason

    if prev_side in ("long", "short") and step <= prev_until:
        return prev_side, prev_until, prev_target, "sticky_" + prev_side

    return "none", -1, 0, "no_active_target"


def passive_target_from_z(z500: Optional[float], active_target: int, active_side: str) -> Tuple[int, str]:
    """
    Passive desired inventory.
    This can use z500 even when active taking is not confirmed.
    It is used only to bias maker quotes, not to cross the spread.
    """
    if active_side in ("long", "short") and active_target != 0:
        return active_target, "active_target"

    if z500 is None:
        return 0, "no_z"

    if z500 <= -Z_STRONG:
        return z_long_target(z500), "passive_z500_long"

    if z500 >= Z_STRONG:
        return -z_short_target(z500), "passive_z500_short"

    if z500 <= -Z_MILD:
        return clip_int(-35 * z500, 0, 70), "passive_mild_long"

    if z500 >= Z_MILD:
        return clip_int(-30 * z500, -60, 0), "passive_mild_short"

    return 0, "passive_neutral"


# ── ACTIVE ORDER LOGIC ──────────────────────────────────────────────────────

def active_orders_to_target(
    depth: OrderDepth,
    position: int,
    active_target: int,
    active_side: str,
    slow_anchor: Optional[float],
    step: int,
    last_active_step: int,
) -> Tuple[List[Order], int, int, str]:
    """
    Active crossing only moves toward active confirmed target.
    No active flattening on passive/mild/neutral targets.
    """
    orders: List[Order] = []

    if active_side not in ("long", "short") or active_target == 0:
        return orders, position, last_active_step, "no_active_signal"

    if slow_anchor is None:
        return orders, position, last_active_step, "no_anchor"

    if step - last_active_step < ACTIVE_COOLDOWN_STEPS:
        return orders, position, last_active_step, "cooldown"

    gap = active_target - position
    if abs(gap) < MIN_ACTIVE_GAP:
        return orders, position, last_active_step, "target_close"

    best_bid, best_ask = best_bid_ask(depth)
    if best_bid is None or best_ask is None:
        return orders, position, last_active_step, "empty_book"

    if gap > 0:
        visible_ask = max(0, -depth.sell_orders.get(best_ask, 0))
        qty = min(TAKER_CHUNK, gap, visible_ask, official_capacity(position, "BUY"))

        reducing_short = position < 0
        adding_long_ok = best_ask < slow_anchor + BUY_MAX_ABOVE_ANCHOR

        if qty > 0 and (reducing_short or adding_long_ok):
            orders.append(Order(SYMBOL, int(best_ask), int(qty)))
            position += qty
            last_active_step = step
            return orders, position, last_active_step, "active_buy_to_target"

    elif gap < 0:
        visible_bid = max(0, depth.buy_orders.get(best_bid, 0))
        qty = min(TAKER_CHUNK, -gap, visible_bid, official_capacity(position, "SELL"))

        reducing_long = position > 0
        adding_short_ok = best_bid > slow_anchor - SELL_MAX_BELOW_ANCHOR

        if qty > 0 and (reducing_long or adding_short_ok):
            orders.append(Order(SYMBOL, int(best_bid), -int(qty)))
            position -= qty
            last_active_step = step
            return orders, position, last_active_step, "active_sell_to_target"

    return orders, position, last_active_step, "edge_guard_blocked"


# ── PASSIVE MARKET MAKING ───────────────────────────────────────────────────

def passive_side_size(position_after_active: int, desired_target: int, side: str) -> int:
    """
    Passive quotes are aligned with desired target.
    If desired target is long, do not fight it with large asks.
    If desired target is short, do not fight it with large bids.
    """
    pos = position_after_active
    size = BASE_MM_SIZE

    if side == "BUY" and pos >= HARD_PASSIVE_CAP:
        return 0
    if side == "SELL" and pos <= -HARD_PASSIVE_CAP:
        return 0

    gap = desired_target - pos

    if gap > MIN_ACTIVE_GAP:
        if side == "BUY":
            return size
        # If we need more long, suppress asks unless already above target.
        if pos <= desired_target:
            return 0
        return max(1, size // 4)

    if gap < -MIN_ACTIVE_GAP:
        if side == "SELL":
            return size
        # If we need more short, suppress bids unless already below target.
        if pos >= desired_target:
            return 0
        return max(1, size // 4)

    return size


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
    used_sell = -sum(-o.quantity for o in existing_orders if o.quantity < 0)

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
        mids = [float(x) for x in s.get("mids", [])][-SLOW_WINDOW:]

        last_active_step = int(s.get("last_active_step", -10_000))
        prev_active_target = int(s.get("active_target", 0))
        prev_active_side = str(s.get("active_side", "none"))
        prev_active_until = int(s.get("active_until", -1))

        if SYMBOL not in state.order_depths:
            return result, 0, save_state(
                step + 1, mids, last_active_step,
                prev_active_target, prev_active_side, prev_active_until
            )

        depth = state.order_depths[SYMBOL]
        position = state.position.get(SYMBOL, 0)

        best_bid, best_ask = best_bid_ask(depth)
        mid = mid_price(depth)
        if mid is None or best_bid is None or best_ask is None:
            result[SYMBOL] = []
            return result, 0, save_state(
                step + 1, mids, last_active_step,
                prev_active_target, prev_active_side, prev_active_until
            )

        mids.append(float(mid))
        if len(mids) > SLOW_WINDOW:
            mids = mids[-SLOW_WINDOW:]

        slow_anchor, slow_std = mean_std(mids)
        z500 = None
        if slow_anchor is not None and slow_std is not None and len(mids) >= MIN_HISTORY_FOR_SIGNAL:
            z500 = (mid - slow_anchor) / max(slow_std, 1e-12)

        fast_mean, fast_std = rolling_stats(mids, FAST_WINDOW)
        local_low, local_high = rolling_low_high(mids, LOCAL_WINDOW)

        mom5 = recent_momentum(mids, MOM_FAST_WINDOW)
        mom20 = recent_momentum(mids, MOM_CONTEXT_WINDOW)

        new_side, new_target, new_reason = detect_active_signal(
            mid=mid,
            best_bid=best_bid,
            best_ask=best_ask,
            slow_anchor=slow_anchor,
            z500=z500,
            fast_mean=fast_mean,
            fast_std=fast_std,
            local_low=local_low,
            local_high=local_high,
            mom5=mom5,
            mom20=mom20,
        )

        active_side, active_until, active_target, active_reason = update_active_target(
            step=step,
            prev_side=prev_active_side,
            prev_until=prev_active_until,
            prev_target=prev_active_target,
            new_side=new_side,
            new_target=new_target,
            new_reason=new_reason,
        )

        desired_target, desired_reason = passive_target_from_z(z500, active_target, active_side)

        orders: List[Order] = []

        active_orders, pos_after_active, last_active_step, active_order_reason = active_orders_to_target(
            depth=depth,
            position=position,
            active_target=active_target,
            active_side=active_side,
            slow_anchor=slow_anchor,
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
            spread = best_ask - best_bid
            z_s = "None" if z500 is None else f"{z500:.2f}"
            fast_z_s = "None"
            if fast_mean is not None and fast_std is not None:
                fast_z_s = f"{((mid - fast_mean) / max(fast_std, 1e-12)):.2f}"
            anchor_s = "None" if slow_anchor is None else f"{slow_anchor:.2f}"
            m5_s = "None" if mom5 is None else f"{mom5:.1f}"
            m20_s = "None" if mom20 is None else f"{mom20:.1f}"
            rebound_s = "None" if local_low is None else f"{(mid - local_low):.1f}"
            drawdown_s = "None" if local_high is None else f"{(local_high - mid):.1f}"

            print(
                f"[HYDRO_Z500_TARGET_V3] ts={state.timestamp} step={step} "
                f"mid={mid:.1f} anchor={anchor_s} z500={z_s} fast_z={fast_z_s} "
                f"mom5={m5_s} mom20={m20_s} rebound200={rebound_s} drawdown200={drawdown_s} "
                f"spread={spread} pos={position} active_target={active_target} "
                f"active_side={active_side} active_until={active_until} active_reason={active_reason} "
                f"new_signal={new_reason} desired_target={desired_target} desired_reason={desired_reason} "
                f"active_order={active_order_reason} pos_after={pos_after_active} orders={orders}"
            )

        return result, 0, save_state(
            step + 1,
            mids,
            last_active_step,
            active_target,
            active_side,
            active_until,
        )
