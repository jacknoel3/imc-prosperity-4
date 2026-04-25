"""
HYDROGEL_PACK — Target MR v2: confirmed active targets, no target-churn
IMC Prosperity 4 | Round 3

Why this version exists:
    The first target-position bot failed because target decay/mild-z targets
    caused active buy/sell churn. It crossed the spread to reduce a long while
    price was still cheap. That burned spread.

Main fixes:
    1. Active taker orders are allowed ONLY for confirmed strong targets:
        - strong low + rebound -> long target
        - strong high + rollover -> short target
        - local rebound/top rollover -> short target
        - local selloff/bottom rebound -> long target

    2. Mild/neutral target decay affects passive quote bias only.
       It does NOT trigger active crossing.

    3. The bot can be genuinely long or short.
       It can sell through flat into a short target, but only when a confirmed
       short signal exists.

    4. Inventory does not need to end flat.
       If the signal still supports inventory, the bot is allowed to hold it.

Core:
    - Passive inside market making:
        bid = best_bid + 1
        ask = best_ask - 1

    - Slow z-score:
        rolling 500 mid-price anchor

    - Momentum:
        momentum_5 for reversal timing
        momentum_20 for context / anti-chase guard

    - Active position target:
        low z + positive mom5 -> target long, usually +80 to +150
        high z + negative mom5 -> target short, usually -80 to -150
        local rebound + rollover -> target short, even if slow z is not > 2
        local selloff + rebound -> target long, even if slow z is not < -2

Set VERBOSE=False before final submission.
"""

from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Optional, Tuple
import json
import math


# ── PARAMETERS ──────────────────────────────────────────────────────────────

SYMBOL = "HYDROGEL_PACK"
POSITION_LIMIT = 200

# Signal history
ROLLING_WINDOW = 500
MIN_HISTORY_FOR_SIGNAL = 500

# Slow z-score thresholds
Z_STRONG = 2.2
Z_MILD = 1.0

# Fast/local z-score for rebound/top and selloff/bottom detection
FAST_WINDOW = 100
LOCAL_WINDOW = 200
FAST_Z_EXTREME = 1.15

# Strong target sizing
MIN_STRONG_TARGET = 85
MAX_STRONG_TARGET = 150
TARGET_SLOPE_PER_Z = 55

# Local rebound/top target sizing
LOCAL_TARGET_BASE = 70
LOCAL_TARGET_SLOPE = 2.0
LOCAL_TARGET_MAX = 130

# Local event thresholds in ticks
REBOUND_FROM_LOW_MIN = 28      # for local top/rollover short
DRAWDOWN_FROM_HIGH_MIN = 28    # for local bottom/rebound long

# Neutral/mild target is passive-only. It should not trigger active taking.
MILD_TARGET_PER_Z = 25
TARGET_DECAY = 0.88
TARGET_ZERO_EPS = 4

# Momentum
MOM_CONFIRM_WINDOW = 5
MOM_CONTEXT_WINDOW = 20

# Active taking
TAKER_CHUNK = 12
ACTIVE_COOLDOWN_STEPS = 2
MIN_ACTIVE_GAP = 8

# Edge guards for opening/increasing directional active inventory.
# Rescue-to-flat is allowed more freely; opening new risk needs price support.
BUY_EDGE_BUFFER_SLOW = 8.0
SELL_EDGE_BUFFER_SLOW = 8.0

# For local rebound/top shorts, don't require z500 > +2; require price not
# materially below slow anchor and a local overextension signal.
LOCAL_SHORT_MIN_SLOW_EDGE = -2.0   # best_bid must be > slow_anchor - 2
LOCAL_LONG_MAX_SLOW_EDGE = 2.0     # best_ask must be < slow_anchor + 2

# Passive MM
INSIDE_OFFSET = 1
BASE_MM_SIZE = 20
MIN_SPREAD_MM = 7
MAX_SPREAD_MM = 17

# Passive inventory safety
COMFORT_INV_CAP = 140
HARD_INV_CAP = 180

VERBOSE = True


# ── STATE HELPERS ───────────────────────────────────────────────────────────

def load_state(trader_data: str) -> dict:
    default = {
        "step": 0,
        "mids": [],
        "last_active_step": -10_000,
        "target_pos": 0,
        "target_confirmed": False,
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
               target_pos: int, target_confirmed: bool) -> str:
    if len(mids) > ROLLING_WINDOW:
        mids = mids[-ROLLING_WINDOW:]
    return json.dumps({
        "step": int(step),
        "mids": mids,
        "last_active_step": int(last_active_step),
        "target_pos": int(target_pos),
        "target_confirmed": bool(target_confirmed),
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


# ── BOOK HELPERS ────────────────────────────────────────────────────────────

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


# ── TARGET POSITION LOGIC ───────────────────────────────────────────────────

def strong_target_abs(z: float) -> int:
    """
    Strong z-score target:
      |z| = 2.2 -> ~85
      |z| = 2.6 -> ~107
      |z| = 3.0 -> ~129
      capped at 150
    """
    az = abs(z)
    raw = MIN_STRONG_TARGET + TARGET_SLOPE_PER_Z * max(0.0, az - Z_STRONG)
    return clip_int(raw, 0, MAX_STRONG_TARGET)


def local_target_abs(extension_ticks: float) -> int:
    """
    Local rebound/drawdown target:
      extension 28 -> 70
      extension 40 -> 94
      extension 55 -> 124
      capped at 130
    """
    raw = LOCAL_TARGET_BASE + LOCAL_TARGET_SLOPE * max(0.0, extension_ticks - REBOUND_FROM_LOW_MIN)
    return clip_int(raw, 0, LOCAL_TARGET_MAX)


def decay_target(previous_target: int) -> int:
    x = int(round(previous_target * TARGET_DECAY))
    if abs(x) <= TARGET_ZERO_EPS:
        return 0
    return x


def compute_target_position(
    mid: float,
    best_bid: int,
    best_ask: int,
    slow_anchor: Optional[float],
    z_slow: Optional[float],
    fast_mean: Optional[float],
    fast_std: Optional[float],
    mom5: Optional[float],
    mom20: Optional[float],
    local_low: Optional[float],
    local_high: Optional[float],
    previous_target: int,
) -> Tuple[int, str, bool]:
    """
    Returns:
      target_pos, reason, confirmed_for_active

    Only confirmed targets can trigger active taker trades.
    Mild/neutral targets are passive-only.
    """
    if slow_anchor is None or z_slow is None or mom5 is None:
        return decay_target(previous_target), "no_signal_decay", False

    z_fast = None
    if fast_mean is not None and fast_std is not None:
        z_fast = (mid - fast_mean) / max(fast_std, 1e-12)

    # 1) Strong slow-z mean-reversion signals.
    if z_slow <= -Z_STRONG and mom5 > 0:
        target = strong_target_abs(z_slow)

        # Anti-chase: if the 20-step rebound is already huge, reduce target.
        if mom20 is not None and mom20 > 40:
            target = int(target * 0.75)

        return clip_int(target, -MAX_STRONG_TARGET, MAX_STRONG_TARGET), "strong_low_rebound_long", True

    if z_slow >= Z_STRONG and mom5 < 0:
        target = -strong_target_abs(z_slow)

        # Anti-chase: if the 20-step drop is already huge, reduce target.
        if mom20 is not None and mom20 < -40:
            target = int(target * 0.75)

        return clip_int(target, -MAX_STRONG_TARGET, MAX_STRONG_TARGET), "strong_high_rollover_short", True

    # 2) Local rebound/top rollover short.
    # This is meant to catch cases where z500 is too slow and only around +0.5/+1,
    # but the market has rebounded sharply from a local low and now momentum rolls over.
    if local_low is not None and z_fast is not None and mom5 < 0:
        rebound = mid - local_low
        not_still_cheap = z_slow > -0.75
        local_overextended = z_fast >= FAST_Z_EXTREME
        slow_price_ok = best_bid > slow_anchor + LOCAL_SHORT_MIN_SLOW_EDGE

        if rebound >= REBOUND_FROM_LOW_MIN and not_still_cheap and local_overextended and slow_price_ok:
            target = -local_target_abs(rebound)
            return clip_int(target, -LOCAL_TARGET_MAX, LOCAL_TARGET_MAX), "local_rebound_rollover_short", True

    # 3) Local selloff/bottom rebound long, symmetric mirror.
    if local_high is not None and z_fast is not None and mom5 > 0:
        drawdown = local_high - mid
        not_still_rich = z_slow < 0.75
        local_oversold = z_fast <= -FAST_Z_EXTREME
        slow_price_ok = best_ask < slow_anchor + LOCAL_LONG_MAX_SLOW_EDGE

        if drawdown >= DRAWDOWN_FROM_HIGH_MIN and not_still_rich and local_oversold and slow_price_ok:
            target = local_target_abs(drawdown)
            return clip_int(target, -LOCAL_TARGET_MAX, LOCAL_TARGET_MAX), "local_selloff_rebound_long", True

    # 4) Mild z-score bias: passive-only, not active.
    if abs(z_slow) >= Z_MILD:
        mild_target = -MILD_TARGET_PER_Z * z_slow
        blended = 0.70 * decay_target(previous_target) + 0.30 * mild_target
        target = clip_int(blended, -50, 50)
        if abs(target) <= TARGET_ZERO_EPS:
            target = 0
        return target, "mild_z_bias_passive_only", False

    # 5) Neutral: decay target toward zero; passive only.
    return decay_target(previous_target), "neutral_decay_passive_only", False


# ── ACTIVE TARGET-CHASING LOGIC ─────────────────────────────────────────────

def buy_edge_ok(reason: str, best_ask: int, slow_anchor: float, position: int) -> bool:
    # Covering a short is allowed because it reduces dangerous inventory.
    if position < 0:
        return True

    if reason == "strong_low_rebound_long":
        return best_ask < slow_anchor - BUY_EDGE_BUFFER_SLOW

    if reason == "local_selloff_rebound_long":
        return best_ask < slow_anchor + LOCAL_LONG_MAX_SLOW_EDGE

    return False


def sell_edge_ok(reason: str, best_bid: int, slow_anchor: float, position: int) -> bool:
    # Reducing a long is allowed because it lowers risk. Building short still
    # requires a confirmed short target, handled by reason.
    if position > 0:
        return True

    if reason == "strong_high_rollover_short":
        return best_bid > slow_anchor + SELL_EDGE_BUFFER_SLOW

    if reason == "local_rebound_rollover_short":
        return best_bid > slow_anchor + LOCAL_SHORT_MIN_SLOW_EDGE

    return False


def active_target_orders(
    depth: OrderDepth,
    position: int,
    target_pos: int,
    target_reason: str,
    confirmed_for_active: bool,
    slow_anchor: Optional[float],
    step: int,
    last_active_step: int,
) -> Tuple[List[Order], int, int, str]:
    """
    Move position toward target only if the target is confirmed.

    Crucial fix:
      If target decays or is only mild/neutral, do NOT cross the spread.
      That avoids the previous target-churn failure.
    """
    orders: List[Order] = []

    if not confirmed_for_active:
        return orders, position, last_active_step, "no_active_unconfirmed_target"

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

    # Move upward: buy at ask.
    if gap > 0:
        visible_ask_qty = max(0, -depth.sell_orders.get(best_ask, 0))
        qty = min(
            TAKER_CHUNK,
            gap,
            visible_ask_qty,
            official_capacity(position, "BUY"),
        )

        if qty > 0 and buy_edge_ok(target_reason, best_ask, slow_anchor, position):
            orders.append(Order(SYMBOL, int(best_ask), int(qty)))
            position += qty
            last_active_step = step
            return orders, position, last_active_step, "active_buy_to_confirmed_target"

    # Move downward: sell at bid.
    elif gap < 0:
        visible_bid_qty = max(0, depth.buy_orders.get(best_bid, 0))

        # If we are long and target is short, first chunk may only reduce long.
        # Once flat/short, subsequent chunks must still pass sell_edge_ok.
        qty = min(
            TAKER_CHUNK,
            -gap,
            visible_bid_qty,
            official_capacity(position, "SELL"),
        )

        if qty > 0 and sell_edge_ok(target_reason, best_bid, slow_anchor, position):
            orders.append(Order(SYMBOL, int(best_bid), -int(qty)))
            position -= qty
            last_active_step = step
            return orders, position, last_active_step, "active_sell_to_confirmed_target"

    return orders, position, last_active_step, "edge_guard_blocked"


# ── PASSIVE MARKET MAKING ───────────────────────────────────────────────────

def passive_side_size(
    position_after_active: int,
    target_pos: int,
    target_reason: str,
    confirmed_for_active: bool,
    side: str,
    z_slow: Optional[float],
) -> int:
    """
    Passive quote sizing aligned with target.

    Passive can use mild/neutral target information. Active cannot.
    """
    pos = position_after_active
    size = BASE_MM_SIZE

    # Hard inventory safety.
    if side == "BUY" and pos >= HARD_INV_CAP:
        return 0
    if side == "SELL" and pos <= -HARD_INV_CAP:
        return 0

    # Comfort cap.
    if side == "BUY" and pos >= COMFORT_INV_CAP:
        size = min(size, 5)
    if side == "SELL" and pos <= -COMFORT_INV_CAP:
        size = min(size, 5)

    gap = target_pos - pos

    # If target wants more long, let passive bids work and avoid fighting
    # with asks unless we are already above target.
    if gap > MIN_ACTIVE_GAP:
        if side == "BUY":
            size = BASE_MM_SIZE
        else:
            if pos <= target_pos:
                return 0
            size = min(size, BASE_MM_SIZE // 4)

    # If target wants more short, let passive asks work and avoid fighting
    # with bids unless we are already below target.
    elif gap < -MIN_ACTIVE_GAP:
        if side == "SELL":
            size = BASE_MM_SIZE
        else:
            if pos >= target_pos:
                return 0
            size = min(size, BASE_MM_SIZE // 4)

    # Extra z-score safety if target is near current position.
    elif z_slow is not None:
        if z_slow <= -Z_STRONG and side == "SELL" and pos <= 0:
            return 0
        if z_slow >= Z_STRONG and side == "BUY" and pos >= 0:
            return 0

    return max(0, int(size))


def passive_mm_orders(
    depth: OrderDepth,
    original_position: int,
    position_after_active: int,
    target_pos: int,
    target_reason: str,
    confirmed_for_active: bool,
    z_slow: Optional[float],
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
        bid_size = min(
            passive_side_size(position_after_active, target_pos, target_reason, confirmed_for_active,
                              "BUY", z_slow),
            buy_cap,
        )
        if bid_size > 0:
            orders.append(Order(SYMBOL, int(bid_px), int(bid_size)))

    if not has_active_buy:
        ask_size = min(
            passive_side_size(position_after_active, target_pos, target_reason, confirmed_for_active,
                              "SELL", z_slow),
            sell_cap,
        )
        if ask_size > 0:
            orders.append(Order(SYMBOL, int(ask_px), -int(ask_size)))

    return orders


# ── TRADER ──────────────────────────────────────────────────────────────────

class Trader:
    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}

        s = load_state(state.traderData)
        step = int(s.get("step", 0))
        mids = [float(x) for x in s.get("mids", [])][-ROLLING_WINDOW:]
        last_active_step = int(s.get("last_active_step", -10_000))
        previous_target = int(s.get("target_pos", 0))

        if SYMBOL not in state.order_depths:
            return result, 0, save_state(step + 1, mids, last_active_step, previous_target, False)

        depth = state.order_depths[SYMBOL]
        position = state.position.get(SYMBOL, 0)

        best_bid, best_ask = best_bid_ask(depth)
        mid = mid_price(depth)
        if mid is None or best_bid is None or best_ask is None:
            result[SYMBOL] = []
            return result, 0, save_state(step + 1, mids, last_active_step, previous_target, False)

        mids.append(float(mid))
        if len(mids) > ROLLING_WINDOW:
            mids = mids[-ROLLING_WINDOW:]

        slow_anchor, slow_sigma = mean_std(mids)
        z_slow = None
        if slow_anchor is not None and slow_sigma is not None and len(mids) >= MIN_HISTORY_FOR_SIGNAL:
            z_slow = (mid - slow_anchor) / slow_sigma

        fast_mean, fast_std = rolling_stats(mids, FAST_WINDOW)
        local_low, local_high = rolling_low_high(mids, LOCAL_WINDOW)

        mom5 = recent_momentum(mids, MOM_CONFIRM_WINDOW)
        mom20 = recent_momentum(mids, MOM_CONTEXT_WINDOW)

        target_pos, target_reason, confirmed_for_active = compute_target_position(
            mid=mid,
            best_bid=best_bid,
            best_ask=best_ask,
            slow_anchor=slow_anchor,
            z_slow=z_slow,
            fast_mean=fast_mean,
            fast_std=fast_std,
            mom5=mom5,
            mom20=mom20,
            local_low=local_low,
            local_high=local_high,
            previous_target=previous_target,
        )

        orders: List[Order] = []

        # 1) Active movement toward confirmed target only.
        active_orders, pos_after_active, last_active_step, active_reason = active_target_orders(
            depth=depth,
            position=position,
            target_pos=target_pos,
            target_reason=target_reason,
            confirmed_for_active=confirmed_for_active,
            slow_anchor=slow_anchor,
            step=step,
            last_active_step=last_active_step,
        )
        orders.extend(active_orders)

        # 2) Passive market making aligned with target.
        mm_orders = passive_mm_orders(
            depth=depth,
            original_position=position,
            position_after_active=pos_after_active,
            target_pos=target_pos,
            target_reason=target_reason,
            confirmed_for_active=confirmed_for_active,
            z_slow=z_slow,
            existing_orders=orders,
        )
        orders.extend(mm_orders)

        result[SYMBOL] = orders

        if VERBOSE:
            spread = best_ask - best_bid
            z_s = "None" if z_slow is None else f"{z_slow:.2f}"
            anchor_s = "None" if slow_anchor is None else f"{slow_anchor:.2f}"
            fast_z_s = "None"
            if fast_mean is not None and fast_std is not None:
                fast_z_s = f"{((mid - fast_mean) / max(fast_std, 1e-12)):.2f}"
            m5_s = "None" if mom5 is None else f"{mom5:.1f}"
            m20_s = "None" if mom20 is None else f"{mom20:.1f}"
            rebound_s = "None" if local_low is None else f"{(mid - local_low):.1f}"
            drawdown_s = "None" if local_high is None else f"{(local_high - mid):.1f}"

            print(
                f"[HYDRO_TARGET_MR_V2] ts={state.timestamp} step={step} "
                f"mid={mid:.1f} anchor={anchor_s} z={z_s} fast_z={fast_z_s} "
                f"mom5={m5_s} mom20={m20_s} rebound200={rebound_s} drawdown200={drawdown_s} "
                f"spread={spread} pos={position} target={target_pos} "
                f"reason={target_reason} confirmed={confirmed_for_active} "
                f"active={active_reason} pos_after={pos_after_active} orders={orders}"
            )

        return result, 0, save_state(
            step + 1,
            mids,
            last_active_step,
            target_pos,
            confirmed_for_active,
        )
