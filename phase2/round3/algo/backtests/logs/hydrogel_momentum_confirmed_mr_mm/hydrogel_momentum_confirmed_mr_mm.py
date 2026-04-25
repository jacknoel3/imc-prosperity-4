"""
HYDROGEL_PACK — Momentum-Confirmed Mean-Reversion MM
IMC Prosperity 4 | Round 3

Purpose:
    Test the hypothesis that Hydrogel is mean-reverting at medium horizons,
    but active entries should wait for short-term momentum to turn.

Why this exists:
    The first MR taker bot bought too early into a continuing selloff.
    This bot keeps the passive MM base, uses z-score for inventory preference,
    and only takes liquidity when:
        1) price is at an extreme vs a slow anchor,
        2) short-term momentum has reversed in the expected direction,
        3) the visible L1 price is still attractive vs anchor,
        4) the trade reduces wrong-way inventory or only opens a small test position.

Core strategy:
    Normal passive MM:
        bid = best_bid + 1
        ask = best_ask - 1

    Cheap + rebound confirmation:
        if z <= -2.0 and fast momentum > 0:
            prefer long / avoid selling
            optionally buy at ask only if strict edge + risk checks pass

    Rich + rollover confirmation:
        if z >= +2.0 and fast momentum < 0:
            prefer short / avoid buying
            optionally sell at bid only if strict edge + risk checks pass

Important:
    This is a test bot, not final.
    It intentionally uses strict taker limits and cooldown to avoid machine-gunning.
    Set VERBOSE=False before final submission.
"""

from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Optional, Tuple
import json
import math


# ── PARAMETERS ──────────────────────────────────────────────────────────────

SYMBOL = "HYDROGEL_PACK"
POSITION_LIMIT = 200

# Signal history.
ROLLING_WINDOW = 500
MIN_HISTORY_FOR_SIGNAL = 500

# Mean-reversion thresholds.
Z_SOFT = 1.0
Z_STRONG = 2.0
Z_TAKE = 2.2

# Momentum confirmation.
# We require price to have turned in the expected direction over this recent window.
MOM_CONFIRM_WINDOW = 5

# Optional larger context move; used only for logs/classification.
MOM_CONTEXT_WINDOW = 20

# Active taker settings.
# Much stricter than the failed first taker bot.
EDGE_BUFFER = 12.0          # buy only if ask < anchor - 12; sell only if bid > anchor + 12
TAKER_CHUNK = 4             # small active chunks only
TAKER_COOLDOWN_STEPS = 5    # 5 observations = 500 timestamp units in website simulator

# Active trades can rescue wrong-way inventory freely up to flat.
# Fresh directional inventory is capped very tightly for this test.
MIN_BAD_INV_TO_RESCUE = 8
MAX_FRESH_ACTIVE_POS = 20

# Passive MM.
INSIDE_OFFSET = 1
BASE_MM_SIZE = 20
MIN_SPREAD_MM = 7
MAX_SPREAD_MM = 17

# Practical inventory limits for passive quoting.
COMFORT_INV_CAP = 60
HARD_INV_CAP = 120

VERBOSE = True


# ── STATE HELPERS ───────────────────────────────────────────────────────────

def load_state(trader_data: str) -> dict:
    default = {"step": 0, "mids": [], "last_take_step": -10_000}
    if not trader_data:
        return default
    try:
        s = json.loads(trader_data)
        if "mids" not in s or not isinstance(s["mids"], list):
            s["mids"] = []
        if "step" not in s:
            s["step"] = 0
        if "last_take_step" not in s:
            s["last_take_step"] = -10_000
        return s
    except Exception:
        return default


def save_state(step: int, mids: List[float], last_take_step: int) -> str:
    if len(mids) > ROLLING_WINDOW:
        mids = mids[-ROLLING_WINDOW:]
    return json.dumps({
        "step": int(step),
        "mids": mids,
        "last_take_step": int(last_take_step),
    })


def mean_std(xs: List[float]) -> Tuple[Optional[float], Optional[float]]:
    n = len(xs)
    if n < 2:
        return None, None
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return m, math.sqrt(max(var, 1e-12))


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


# ── ACTIVE TAKER LOGIC ──────────────────────────────────────────────────────

def active_taker_orders(
    depth: OrderDepth,
    position: int,
    anchor: Optional[float],
    z: Optional[float],
    mom_confirm: Optional[float],
    step: int,
    last_take_step: int,
) -> Tuple[List[Order], int, int, str]:
    """
    Momentum-confirmed active taking.

    This bot is intentionally conservative:
      - no active taking before full rolling history is mature
      - cooldown after any active take
      - small chunks
      - active trades mostly rescue wrong-way inventory
      - fresh active inventory capped to ±MAX_FRESH_ACTIVE_POS
    """
    orders: List[Order] = []
    reason = "no_take"

    if anchor is None or z is None or mom_confirm is None:
        return orders, position, last_take_step, "no_signal"

    if step - last_take_step < TAKER_COOLDOWN_STEPS:
        return orders, position, last_take_step, "cooldown"

    best_bid, best_ask = best_bid_ask(depth)
    if best_bid is None or best_ask is None:
        return orders, position, last_take_step, "empty_book"

    # Cheap + rebound confirmation: buy only if recent momentum has turned positive.
    if z <= -Z_TAKE and mom_confirm > 0 and best_ask < anchor - EDGE_BUFFER:
        visible_ask_qty = max(0, -depth.sell_orders.get(best_ask, 0))

        # Rescue mode: if short, buy only toward flat.
        if position <= -MIN_BAD_INV_TO_RESCUE:
            desired = -position
            qty = min(
                TAKER_CHUNK,
                visible_ask_qty,
                desired,
                official_capacity(position, "BUY"),
            )
            if qty > 0:
                orders.append(Order(SYMBOL, int(best_ask), int(qty)))
                position += qty
                last_take_step = step
                reason = "take_buy_rescue_short_after_rebound"

        # Small fresh test mode: allow limited long if not already long.
        elif position < MAX_FRESH_ACTIVE_POS:
            desired = MAX_FRESH_ACTIVE_POS - position
            qty = min(
                TAKER_CHUNK,
                visible_ask_qty,
                desired,
                official_capacity(position, "BUY"),
            )
            if qty > 0:
                orders.append(Order(SYMBOL, int(best_ask), int(qty)))
                position += qty
                last_take_step = step
                reason = "take_buy_small_fresh_after_rebound"

    # Rich + rollover confirmation: sell only if recent momentum has turned negative.
    elif z >= Z_TAKE and mom_confirm < 0 and best_bid > anchor + EDGE_BUFFER:
        visible_bid_qty = max(0, depth.buy_orders.get(best_bid, 0))

        # Rescue mode: if long, sell only toward flat.
        if position >= MIN_BAD_INV_TO_RESCUE:
            desired = position
            qty = min(
                TAKER_CHUNK,
                visible_bid_qty,
                desired,
                official_capacity(position, "SELL"),
            )
            if qty > 0:
                orders.append(Order(SYMBOL, int(best_bid), -int(qty)))
                position -= qty
                last_take_step = step
                reason = "take_sell_rescue_long_after_rollover"

        # Small fresh test mode: allow limited short if not already short.
        elif position > -MAX_FRESH_ACTIVE_POS:
            desired = position + MAX_FRESH_ACTIVE_POS
            qty = min(
                TAKER_CHUNK,
                visible_bid_qty,
                desired,
                official_capacity(position, "SELL"),
            )
            if qty > 0:
                orders.append(Order(SYMBOL, int(best_bid), -int(qty)))
                position -= qty
                last_take_step = step
                reason = "take_sell_small_fresh_after_rollover"

    return orders, position, last_take_step, reason


# ── PASSIVE MARKET MAKING ───────────────────────────────────────────────────

def passive_side_size(position_after_takers: int, side: str, z: Optional[float], mom_confirm: Optional[float]) -> int:
    """
    Passive quote sizing using z-score + momentum.

    The logic intentionally stays simple:
      - If cheap, avoid adding shorts through asks.
      - If rich, avoid adding longs through bids.
      - If cheap but still falling, reduce bids too: do not catch falling knife.
      - If rich but still rising, reduce asks too: do not stand in front of squeeze.
    """
    pos = position_after_takers
    size = BASE_MM_SIZE

    # Hard inventory protection.
    if side == "BUY" and pos >= HARD_INV_CAP:
        return 0
    if side == "SELL" and pos <= -HARD_INV_CAP:
        return 0

    # Passive cap/taper.
    if side == "BUY" and pos >= COMFORT_INV_CAP:
        size = min(size, 5)
    if side == "SELL" and pos <= -COMFORT_INV_CAP:
        size = min(size, 5)

    if z is not None:
        # Cheap: prefer long, but do not bid full size if still falling hard.
        if z <= -Z_STRONG:
            if side == "SELL" and pos <= COMFORT_INV_CAP:
                return 0
            if side == "BUY":
                if mom_confirm is not None and mom_confirm < 0:
                    size = min(size, BASE_MM_SIZE // 4)  # wait for rebound
                else:
                    size = BASE_MM_SIZE

        elif z <= -Z_SOFT:
            if side == "SELL" and pos <= 0:
                size = min(size, BASE_MM_SIZE // 4)
            elif side == "SELL":
                size = min(size, BASE_MM_SIZE // 2)

        # Rich: prefer short, but do not ask full size if still rising hard.
        elif z >= Z_STRONG:
            if side == "BUY" and pos >= -COMFORT_INV_CAP:
                return 0
            if side == "SELL":
                if mom_confirm is not None and mom_confirm > 0:
                    size = min(size, BASE_MM_SIZE // 4)  # wait for rollover
                else:
                    size = BASE_MM_SIZE

        elif z >= Z_SOFT:
            if side == "BUY" and pos >= 0:
                size = min(size, BASE_MM_SIZE // 4)
            elif side == "BUY":
                size = min(size, BASE_MM_SIZE // 2)

    return max(0, int(size))


def passive_mm_orders(
    depth: OrderDepth,
    original_position: int,
    position_after_takers: int,
    z: Optional[float],
    mom_confirm: Optional[float],
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
        bid_size = min(passive_side_size(position_after_takers, "BUY", z, mom_confirm), buy_cap)
        if bid_size > 0:
            orders.append(Order(SYMBOL, int(bid_px), int(bid_size)))

    if not has_active_buy:
        ask_size = min(passive_side_size(position_after_takers, "SELL", z, mom_confirm), sell_cap)
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
        last_take_step = int(s.get("last_take_step", -10_000))

        if SYMBOL not in state.order_depths:
            return result, 0, save_state(step + 1, mids, last_take_step)

        depth = state.order_depths[SYMBOL]
        position = state.position.get(SYMBOL, 0)

        mid = mid_price(depth)
        if mid is None:
            result[SYMBOL] = []
            return result, 0, save_state(step + 1, mids, last_take_step)

        mids.append(float(mid))
        if len(mids) > ROLLING_WINDOW:
            mids = mids[-ROLLING_WINDOW:]

        anchor, sigma = mean_std(mids)
        z = None
        if anchor is not None and sigma is not None and len(mids) >= MIN_HISTORY_FOR_SIGNAL:
            z = (mid - anchor) / sigma

        mom_confirm = recent_momentum(mids, MOM_CONFIRM_WINDOW)
        mom_context = recent_momentum(mids, MOM_CONTEXT_WINDOW)

        orders: List[Order] = []

        # 1) Active momentum-confirmed mean-reversion taker.
        taker_orders, pos_after_taker, last_take_step, take_reason = active_taker_orders(
            depth, position, anchor, z, mom_confirm, step, last_take_step
        )
        orders.extend(taker_orders)

        # 2) Passive z + momentum-aware market making.
        mm_orders = passive_mm_orders(depth, position, pos_after_taker, z, mom_confirm, orders)
        orders.extend(mm_orders)

        result[SYMBOL] = orders

        if VERBOSE:
            best_bid, best_ask = best_bid_ask(depth)
            spread = (best_ask - best_bid) if best_bid is not None and best_ask is not None else None
            z_s = "None" if z is None else f"{z:.2f}"
            anchor_s = "None" if anchor is None else f"{anchor:.2f}"
            mc_s = "None" if mom_confirm is None else f"{mom_confirm:.1f}"
            mx_s = "None" if mom_context is None else f"{mom_context:.1f}"
            print(
                f"[HYDRO_MOM_MR] ts={state.timestamp} step={step} "
                f"mid={mid:.1f} anchor={anchor_s} z={z_s} "
                f"mom{MOM_CONFIRM_WINDOW}={mc_s} mom{MOM_CONTEXT_WINDOW}={mx_s} "
                f"spread={spread} pos={position} pos_after_taker={pos_after_taker} "
                f"take={take_reason} orders={orders}"
            )

        return result, 0, save_state(step + 1, mids, last_take_step)
