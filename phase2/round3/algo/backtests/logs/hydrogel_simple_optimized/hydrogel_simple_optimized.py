"""
HYDROGEL_PACK — Simple Mean-Reversion v4
IMC Prosperity 4 | Round 3

Clean reset based on log-sample EDA.

Core conclusions used:
    1. z500 low is the cleanest long signal.
    2. z100 is too noisy as a main signal.
    3. z500/z300 high did not produce useful short signals in the sample.
    4. Shorts should come from local top / rebound-rollover.
    5. Local bottom longs should require a large drawdown, not every small bounce.
    6. Active crossing is only allowed for strong, confirmed signals.
    7. Target decay / passive bias never triggers active crossing.

Signals:
    LONG A — z500 mean-reversion:
        z500 <= -2.0 and mom10 > 0 or mom20 > 0
        target +120 to +170

    LONG B — local bottom rebound:
        drawdown from local high 200 >= 60
        fast_z100 <= -1.0
        mom5 > 0
        target +100 to +150

    SHORT — local top rollover:
        rebound from local low 200 >= 50
        fast_z100 >= +1.0
        mom5 < 0
        target -100 to -150

Execution:
    - Passive inside MM always, aligned with desired target.
    - Active taking only for confirmed signals above.
    - Active target is sticky for a short build window.
    - Does not force final inventory to zero.

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

# Active target persistence
ACTIVE_HOLD_STEPS = 45
ACTIVE_COOLDOWN_STEPS = 1
TAKER_CHUNK = 16
MIN_ACTIVE_GAP = 8

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
        "active_target": 0,
        "active_until": -1,
        "active_reason": "none",
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
) -> str:
    if len(mids) > Z_WINDOW:
        mids = mids[-Z_WINDOW:]

    return json.dumps({
        "step": int(step),
        "mids": mids,
        "last_active_step": int(last_active_step),
        "active_side": str(active_side),
        "active_target": int(active_target),
        "active_until": int(active_until),
        "active_reason": str(active_reason),
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
    Returns active_side, signed_target, reason.
    """
    if anchor is None or z500 is None:
        return "none", 0, "no_z500"

    candidates = []

    # LONG A: z500 low mean reversion.
    # Direction comes from z500. Momentum confirms active entry.
    if z500 <= Z500_LONG_TH and ((mom10 is not None and mom10 > 0) or (mom20 is not None and mom20 > 0)):
        tgt = z500_long_target(z500)
        score = 10.0 + abs(z500)

        # If mom20 is strongly positive, this is cleaner.
        if mom20 is not None and mom20 > 0:
            score += 0.5

        candidates.append(("long", tgt, score, "z500_low_mom_confirm_long"))

    # LONG B: local bottom after large drawdown.
    # Requires large drawdown, fast oversold, and immediate rebound.
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
            # Avoid local long if broader z500 is already rich.
            and z500 < 1.0
        ):
            tgt = local_long_target(drawdown)

            # If z500 is also cheap, slightly more confidence.
            if z500 <= -1.0:
                tgt = min(LOCAL_LONG_MAX_TARGET, int(tgt * 1.10))

            score = 7.0 + 0.04 * drawdown + 0.50 * abs(fast_z)
            candidates.append(("long", tgt, score, "local_bottom_rebound_long"))

    # SHORT: local top / rebound rollover.
    # Main short signal; do not rely on z500 high.
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
            # Do not short if z500 says we are deeply cheap.
            and z500 > -1.0
        ):
            tgt = local_short_target(rebound)

            # If z500 is mildly positive, allow slightly more conviction.
            if z500 >= 0.5:
                tgt = min(LOCAL_SHORT_MAX_TARGET, int(tgt * 1.10))

            score = 8.0 + 0.04 * rebound + 0.50 * fast_z
            candidates.append(("short", -tgt, score, "local_top_rollover_short"))

    if not candidates:
        return "none", 0, "no_confirmed_signal"

    # Pick highest score if long and short both fire.
    side, target, score, reason = max(candidates, key=lambda x: x[2])
    return side, target, reason


def update_active_target(
    step: int,
    prev_side: str,
    prev_target: int,
    prev_until: int,
    prev_reason: str,
    new_side: str,
    new_target: int,
    new_reason: str,
) -> Tuple[str, int, int, str]:
    """
    New confirmed signal creates/refreshes active target.
    Existing target persists for build window.
    After expiry, active taking stops. Inventory is not forcibly flattened.
    """
    if new_side in ("long", "short") and new_target != 0:
        return new_side, new_target, step + ACTIVE_HOLD_STEPS, new_reason

    if prev_side in ("long", "short") and step <= prev_until:
        return prev_side, prev_target, prev_until, "sticky_" + prev_reason

    return "none", 0, -1, "no_active_target"


def passive_desired_target(
    z500: Optional[float],
    active_side: str,
    active_target: int,
) -> Tuple[int, str]:
    """
    Passive target can use z500 even when active target is inactive.
    This biases maker quotes only. It never causes active crossing.
    """
    if active_side in ("long", "short") and active_target != 0:
        return active_target, "active_target"

    if z500 is None:
        return 0, "no_z"

    if z500 <= Z500_LONG_TH:
        return z500_long_target(z500), "passive_z500_long"

    # Keep short passive bias smaller and only mild; high z500 was not observed
    # in the log sample, so shorts should mainly come from local_top_rollover.
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
    active_target: int,
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

    # Move long / cover short.
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

    # Move short / reduce long.
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

    if side == "BUY" and pos >= HARD_PASSIVE_CAP:
        return 0
    if side == "SELL" and pos <= -HARD_PASSIVE_CAP:
        return 0

    # Bias passive orders toward desired target.
    if gap > MIN_ACTIVE_GAP:
        if side == "BUY":
            return BASE_MM_SIZE
        # Do not fight long target unless already above it.
        if pos <= desired_target:
            return 0
        return max(1, BASE_MM_SIZE // 4)

    if gap < -MIN_ACTIVE_GAP:
        if side == "SELL":
            return BASE_MM_SIZE
        # Do not fight short target unless already below it.
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
        prev_active_target = int(s.get("active_target", 0))
        prev_active_until = int(s.get("active_until", -1))
        prev_active_reason = str(s.get("active_reason", "none"))

        if SYMBOL not in state.order_depths:
            return result, 0, save_state(
                step + 1,
                mids,
                last_active_step,
                prev_active_side,
                prev_active_target,
                prev_active_until,
                prev_active_reason,
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
                prev_active_target,
                prev_active_until,
                prev_active_reason,
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

        new_side, new_target, new_reason = detect_confirmed_signal(
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

        active_side, active_target, active_until, active_reason = update_active_target(
            step=step,
            prev_side=prev_active_side,
            prev_target=prev_active_target,
            prev_until=prev_active_until,
            prev_reason=prev_active_reason,
            new_side=new_side,
            new_target=new_target,
            new_reason=new_reason,
        )

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
                f"[HYDRO_SIMPLE_MR_V4] ts={state.timestamp} step={step} "
                f"mid={mid:.1f} anchor={anchor_s} z500={z_s} fast_z={fz_s} "
                f"mom5={m5_s} mom10={m10_s} mom20={m20_s} "
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
        )
