"""
HYDROGEL_PACK — Sticky Signal Target MR
IMC Prosperity 4 | Round 3

Purpose:
    Simpler, more aggressive signal-state bot.

Why this version exists:
    The previous target-position bot detected the top, but the target became
    unconfirmed/decayed too quickly, so the bot only reduced long inventory
    and did not actually go short. This version uses sticky confirmed signals:
    once a strong long/short signal fires, the target persists for a fixed
    window and the bot keeps moving toward it.

Core idea:
    - Do not constantly recompute/decay the active target every tick.
    - Identify clean BUY / SELL regimes.
    - When BUY signal fires: target large long.
    - When SELL signal fires: target large short.
    - Active orders aggressively move toward that target in chunks.
    - Passive MM is aligned with the target.

Signals:
    LONG:
        1) slow z500 very low + mom5 > 0
        OR
        2) local 200-step selloff + fast z100 oversold + mom5 > 0

    SHORT:
        1) slow z500 very high + mom5 < 0
        OR
        2) local 200-step rebound + fast z100 overbought + mom5 < 0

This is symmetric and can sell through zero into short, or buy through zero
into long.

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

# Slow z-score signal
Z_SLOW_TRIGGER = 2.2

# Local extension signal
FAST_Z_TRIGGER = 1.15
REBOUND_FROM_LOW_TRIGGER = 45.0      # top/rollover short
DRAWDOWN_FROM_HIGH_TRIGGER = 45.0    # bottom/rebound long

# Momentum confirmation
MOM_FAST_WINDOW = 5
MOM_CONTEXT_WINDOW = 20

# Sticky signal memory
SIGNAL_HOLD_STEPS = 55       # observations; 55 = 5,500 timestamp units
SIGNAL_REFRESH = True        # refresh hold window if same signal appears again

# Target sizing
BASE_TARGET = 125
MAX_TARGET = 170
SLOW_TARGET_SLOPE = 35       # extra target per z above threshold
LOCAL_TARGET_SLOPE = 1.4     # extra target per tick extension above threshold

# Active trading
TAKER_CHUNK = 20             # L1 visible size usually caps this anyway
ACTIVE_COOLDOWN_STEPS = 1
MIN_ACTIVE_GAP = 6

# Edge/risk guards. These are deliberately looser than previous versions.
# We want to actually reach short/long targets, but avoid chasing too far.
LONG_BUILD_MAX_ABOVE_ANCHOR = 12.0    # buy allowed if ask < anchor + 12
SHORT_BUILD_MAX_BELOW_ANCHOR = 25.0   # sell allowed if bid > anchor - 25

# Passive MM
INSIDE_OFFSET = 1
BASE_MM_SIZE = 20
MIN_SPREAD_MM = 7
MAX_SPREAD_MM = 17

# Passive inventory safety
HARD_PASSIVE_CAP = 185

VERBOSE = True


# ── STATE HELPERS ───────────────────────────────────────────────────────────

def load_state(trader_data: str) -> dict:
    default = {
        "step": 0,
        "mids": [],
        "last_active_step": -10_000,
        "target_pos": 0,
        "signal_side": "none",   # "long", "short", "none"
        "signal_until": -1,
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


def save_state(step: int, mids: List[float], last_active_step: int,
               target_pos: int, signal_side: str, signal_until: int) -> str:
    if len(mids) > SLOW_WINDOW:
        mids = mids[-SLOW_WINDOW:]
    return json.dumps({
        "step": int(step),
        "mids": mids,
        "last_active_step": int(last_active_step),
        "target_pos": int(target_pos),
        "signal_side": str(signal_side),
        "signal_until": int(signal_until),
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


# ── SIGNAL LOGIC ────────────────────────────────────────────────────────────

def target_from_slow_z(z: float) -> int:
    extra = SLOW_TARGET_SLOPE * max(0.0, abs(z) - Z_SLOW_TRIGGER)
    return clip_int(BASE_TARGET + extra, 0, MAX_TARGET)


def target_from_extension(extension: float, trigger: float) -> int:
    extra = LOCAL_TARGET_SLOPE * max(0.0, extension - trigger)
    return clip_int(BASE_TARGET + extra, 0, MAX_TARGET)


def detect_signal(
    mid: float,
    best_bid: int,
    best_ask: int,
    slow_anchor: Optional[float],
    z_slow: Optional[float],
    fast_mean: Optional[float],
    fast_std: Optional[float],
    local_low: Optional[float],
    local_high: Optional[float],
    mom5: Optional[float],
    mom20: Optional[float],
) -> Tuple[str, int, str]:
    """
    Return side, target_abs, reason.
    side in {"long", "short", "none"}.
    """

    if slow_anchor is None or z_slow is None or mom5 is None:
        return "none", 0, "no_signal"

    z_fast = None
    if fast_mean is not None and fast_std is not None:
        z_fast = (mid - fast_mean) / max(fast_std, 1e-12)

    candidates = []

    # Slow extreme: buy low after rebound.
    if z_slow <= -Z_SLOW_TRIGGER and mom5 > 0:
        target = target_from_slow_z(z_slow)
        candidates.append(("long", target, abs(z_slow), "slow_low_rebound_long"))

    # Slow extreme: sell high after rollover.
    if z_slow >= Z_SLOW_TRIGGER and mom5 < 0:
        target = target_from_slow_z(z_slow)
        candidates.append(("short", target, abs(z_slow), "slow_high_rollover_short"))

    # Local top / rebound rollover. This catches tops where z500 is too slow.
    if local_low is not None and z_fast is not None and mom5 < 0:
        rebound = mid - local_low
        if (
            rebound >= REBOUND_FROM_LOW_TRIGGER
            and z_fast >= FAST_Z_TRIGGER
            and z_slow > -0.90
            and best_bid > slow_anchor - SHORT_BUILD_MAX_BELOW_ANCHOR
        ):
            target = target_from_extension(rebound, REBOUND_FROM_LOW_TRIGGER)
            # Score prioritizes larger rebound and faster overboughtness.
            score = 2.0 + 0.03 * rebound + 0.35 * z_fast
            candidates.append(("short", target, score, "local_rebound_rollover_short"))

    # Local bottom / selloff rebound. Symmetric mirror.
    if local_high is not None and z_fast is not None and mom5 > 0:
        drawdown = local_high - mid
        if (
            drawdown >= DRAWDOWN_FROM_HIGH_TRIGGER
            and z_fast <= -FAST_Z_TRIGGER
            and z_slow < 0.90
            and best_ask < slow_anchor + LONG_BUILD_MAX_ABOVE_ANCHOR
        ):
            target = target_from_extension(drawdown, DRAWDOWN_FROM_HIGH_TRIGGER)
            score = 2.0 + 0.03 * drawdown + 0.35 * abs(z_fast)
            candidates.append(("long", target, score, "local_selloff_rebound_long"))

    if not candidates:
        return "none", 0, "no_new_signal"

    # If both long/short somehow fire, choose highest score.
    side, target, score, reason = max(candidates, key=lambda x: x[2])
    return side, target, reason


def update_sticky_target(
    step: int,
    previous_side: str,
    previous_until: int,
    previous_target: int,
    new_side: str,
    new_abs_target: int,
    new_reason: str,
) -> Tuple[str, int, int, str, bool]:
    """
    Update sticky target state.

    If a new confirmed signal appears, set/refresh target.
    If no new signal, keep old target until expiry.
    After expiry, go neutral target 0.
    """
    if new_side in ("long", "short") and new_abs_target > 0:
        target = new_abs_target if new_side == "long" else -new_abs_target

        if new_side != previous_side or SIGNAL_REFRESH:
            return new_side, step + SIGNAL_HOLD_STEPS, target, new_reason, True

    # No new signal: keep target if still within hold window.
    if previous_side in ("long", "short") and step <= previous_until:
        return previous_side, previous_until, previous_target, "sticky_" + previous_side, True

    # Expired.
    return "none", -1, 0, "expired_neutral", False


# ── ACTIVE TRADING ──────────────────────────────────────────────────────────

def active_orders_to_target(
    depth: OrderDepth,
    position: int,
    target_pos: int,
    signal_side: str,
    slow_anchor: Optional[float],
    step: int,
    last_active_step: int,
) -> Tuple[List[Order], int, int, str]:
    orders: List[Order] = []

    if signal_side not in ("long", "short"):
        return orders, position, last_active_step, "no_active_signal"

    if slow_anchor is None:
        return orders, position, last_active_step, "no_anchor"

    if step - last_active_step < ACTIVE_COOLDOWN_STEPS:
        return orders, position, last_active_step, "cooldown"

    gap = target_pos - position
    if abs(gap) < MIN_ACTIVE_GAP:
        return orders, position, last_active_step, "target_close"

    best_bid, best_ask = best_bid_ask(depth)
    if best_bid is None or best_ask is None:
        return orders, position, last_active_step, "empty_book"

    # Need to buy.
    if gap > 0:
        visible_ask = max(0, -depth.sell_orders.get(best_ask, 0))
        qty = min(TAKER_CHUNK, gap, visible_ask, official_capacity(position, "BUY"))

        # If covering short, always okay. If adding long, avoid buying too far
        # above slow anchor.
        reducing_short = position < 0
        edge_ok = best_ask < slow_anchor + LONG_BUILD_MAX_ABOVE_ANCHOR

        if qty > 0 and (reducing_short or edge_ok):
            orders.append(Order(SYMBOL, int(best_ask), int(qty)))
            position += qty
            last_active_step = step
            return orders, position, last_active_step, "active_buy_to_sticky_target"

    # Need to sell.
    elif gap < 0:
        visible_bid = max(0, depth.buy_orders.get(best_bid, 0))
        qty = min(TAKER_CHUNK, -gap, visible_bid, official_capacity(position, "SELL"))

        # If reducing long, always okay. If adding short, avoid selling too far
        # below slow anchor.
        reducing_long = position > 0
        edge_ok = best_bid > slow_anchor - SHORT_BUILD_MAX_BELOW_ANCHOR

        if qty > 0 and (reducing_long or edge_ok):
            orders.append(Order(SYMBOL, int(best_bid), -int(qty)))
            position -= qty
            last_active_step = step
            return orders, position, last_active_step, "active_sell_to_sticky_target"

    return orders, position, last_active_step, "edge_guard_blocked"


# ── PASSIVE MARKET MAKING ───────────────────────────────────────────────────

def passive_size(position_after_active: int, target_pos: int, side: str) -> int:
    pos = position_after_active
    size = BASE_MM_SIZE

    # Safety near official limit.
    if side == "BUY" and pos >= HARD_PASSIVE_CAP:
        return 0
    if side == "SELL" and pos <= -HARD_PASSIVE_CAP:
        return 0

    gap = target_pos - pos

    # If target wants long, do not fight with asks unless above target.
    if gap > MIN_ACTIVE_GAP:
        if side == "BUY":
            return size
        if pos <= target_pos:
            return 0
        return max(1, size // 4)

    # If target wants short, do not fight with bids unless below target.
    if gap < -MIN_ACTIVE_GAP:
        if side == "SELL":
            return size
        if pos >= target_pos:
            return 0
        return max(1, size // 4)

    # Neutral / near target: quote both sides.
    return size


def passive_mm_orders(
    depth: OrderDepth,
    original_position: int,
    position_after_active: int,
    target_pos: int,
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
    used_sell = -sum(o.quantity for o in existing_orders if o.quantity < 0)

    buy_cap = max(0, official_capacity(original_position, "BUY") - used_buy)
    sell_cap = max(0, official_capacity(original_position, "SELL") - used_sell)

    if not has_active_sell:
        qty = min(passive_size(position_after_active, target_pos, "BUY"), buy_cap)
        if qty > 0:
            orders.append(Order(SYMBOL, int(bid_px), int(qty)))

    if not has_active_buy:
        qty = min(passive_size(position_after_active, target_pos, "SELL"), sell_cap)
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
        prev_target = int(s.get("target_pos", 0))
        prev_side = str(s.get("signal_side", "none"))
        prev_until = int(s.get("signal_until", -1))

        if SYMBOL not in state.order_depths:
            return result, 0, save_state(step + 1, mids, last_active_step, prev_target, prev_side, prev_until)

        depth = state.order_depths[SYMBOL]
        position = state.position.get(SYMBOL, 0)

        best_bid, best_ask = best_bid_ask(depth)
        mid = mid_price(depth)
        if mid is None or best_bid is None or best_ask is None:
            result[SYMBOL] = []
            return result, 0, save_state(step + 1, mids, last_active_step, prev_target, prev_side, prev_until)

        mids.append(float(mid))
        if len(mids) > SLOW_WINDOW:
            mids = mids[-SLOW_WINDOW:]

        slow_anchor, slow_std = mean_std(mids)
        z_slow = None
        if slow_anchor is not None and slow_std is not None and len(mids) >= MIN_HISTORY_FOR_SIGNAL:
            z_slow = (mid - slow_anchor) / max(slow_std, 1e-12)

        fast_mean, fast_std = rolling_stats(mids, FAST_WINDOW)
        local_low, local_high = rolling_low_high(mids, LOCAL_WINDOW)

        mom5 = recent_momentum(mids, MOM_FAST_WINDOW)
        mom20 = recent_momentum(mids, MOM_CONTEXT_WINDOW)

        new_side, new_abs_target, new_reason = detect_signal(
            mid=mid,
            best_bid=best_bid,
            best_ask=best_ask,
            slow_anchor=slow_anchor,
            z_slow=z_slow,
            fast_mean=fast_mean,
            fast_std=fast_std,
            local_low=local_low,
            local_high=local_high,
            mom5=mom5,
            mom20=mom20,
        )

        signal_side, signal_until, target_pos, signal_reason, signal_active = update_sticky_target(
            step=step,
            previous_side=prev_side,
            previous_until=prev_until,
            previous_target=prev_target,
            new_side=new_side,
            new_abs_target=new_abs_target,
            new_reason=new_reason,
        )

        orders: List[Order] = []

        # 1) Active movement toward sticky signal target.
        active_orders, pos_after_active, last_active_step, active_reason = active_orders_to_target(
            depth=depth,
            position=position,
            target_pos=target_pos,
            signal_side=signal_side,
            slow_anchor=slow_anchor,
            step=step,
            last_active_step=last_active_step,
        )
        orders.extend(active_orders)

        # 2) Passive MM aligned with target.
        orders.extend(passive_mm_orders(
            depth=depth,
            original_position=position,
            position_after_active=pos_after_active,
            target_pos=target_pos,
            existing_orders=orders,
        ))

        result[SYMBOL] = orders

        if VERBOSE:
            spread = best_ask - best_bid
            z_s = "None" if z_slow is None else f"{z_slow:.2f}"
            fz_s = "None"
            if fast_mean is not None and fast_std is not None:
                fz_s = f"{((mid - fast_mean) / max(fast_std, 1e-12)):.2f}"
            anchor_s = "None" if slow_anchor is None else f"{slow_anchor:.2f}"
            m5_s = "None" if mom5 is None else f"{mom5:.1f}"
            m20_s = "None" if mom20 is None else f"{mom20:.1f}"
            rebound_s = "None" if local_low is None else f"{(mid - local_low):.1f}"
            drawdown_s = "None" if local_high is None else f"{(local_high - mid):.1f}"
            print(
                f"[HYDRO_STICKY_TARGET] ts={state.timestamp} step={step} "
                f"mid={mid:.1f} anchor={anchor_s} z={z_s} fast_z={fz_s} "
                f"mom5={m5_s} mom20={m20_s} rebound200={rebound_s} drawdown200={drawdown_s} "
                f"spread={spread} pos={position} target={target_pos} side={signal_side} "
                f"until={signal_until} signal_reason={signal_reason} "
                f"new_signal={new_reason} active={active_reason} pos_after={pos_after_active} "
                f"orders={orders}"
            )

        return result, 0, save_state(
            step + 1,
            mids,
            last_active_step,
            target_pos,
            signal_side,
            signal_until,
        )
