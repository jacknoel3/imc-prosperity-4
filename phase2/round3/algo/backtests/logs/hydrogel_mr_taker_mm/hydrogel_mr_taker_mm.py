"""
HYDROGEL_PACK — MR Taker + Z-Inventory Market Maker
IMC Prosperity 4 | Round 3

Purpose:
    First active/taker experiment.

Hypothesis:
    The passive inside-1 market maker captures spread, but extreme slow
    mean-reversion states may justify crossing the spread. We only take
    liquidity when the best visible price is still meaningfully cheap/rich
    versus a slow rolling anchor AFTER paying the spread.

Core idea:
    1. Keep the proven passive base:
         bid = best_bid + 1
         ask = best_ask - 1

    2. Maintain a rolling 500-mid anchor and z-score.

    3. Active taker overlay:
         If z < -2 and best_ask is below anchor by EDGE_BUFFER:
             buy at best_ask, moving toward a long target.
         If z > +2 and best_bid is above anchor by EDGE_BUFFER:
             sell at best_bid, moving toward a short target.

    4. Passive z-inventory filter:
         If price is cheap, avoid increasing short inventory.
         If price is expensive, avoid increasing long inventory.

Important:
    - This is intentionally still controlled. It does not blindly max out.
    - It is a hypothesis test, not the final strategy.
    - Compare against simple_inside_mm and z_inventory_mm.

Set VERBOSE=False before final submission.
"""

from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Tuple, Optional
import json
import math


# ── PARAMETERS ──────────────────────────────────────────────────────────────

SYMBOL = "HYDROGEL_PACK"
POSITION_LIMIT = 200

# Rolling mean-reversion anchor.
ROLLING_WINDOW = 500
MIN_HISTORY_FOR_SIGNAL = 100

# Z-score thresholds.
Z_SOFT = 1.0
Z_STRONG = 2.0

# Active taking parameters.
EDGE_BUFFER = 4.0          # require anchor - ask > 4, or bid - anchor > 4
TAKER_CHUNK = 10           # max active units per tick
TARGET_PER_Z = 35          # z=-2 -> target +70; z=+2 -> target -70
MAX_ACTIVE_POS = 120       # never intentionally target beyond this

# Passive market-making parameters.
INSIDE_OFFSET = 1
BASE_MM_SIZE = 20

# Practical passive inventory limits.
COMFORT_INV_CAP = 60       # beyond this, restrict inventory-worsening passive quotes
HARD_INV_CAP = 120         # no passive quotes that worsen exposure beyond this

# Allowed spread range for passive MM. We allow compressed states for now because
# z_inventory_mm beat skip_compressed in the website simulator sample.
MIN_SPREAD_MM = 7
MAX_SPREAD_MM = 17

VERBOSE = True


# ── STATE HELPERS ───────────────────────────────────────────────────────────

def load_state(trader_data: str) -> dict:
    default = {"step": 0, "mids": []}
    if not trader_data:
        return default
    try:
        s = json.loads(trader_data)
        if "mids" not in s or not isinstance(s["mids"], list):
            s["mids"] = []
        if "step" not in s:
            s["step"] = 0
        return s
    except Exception:
        return default


def save_state(step: int, mids: List[float]) -> str:
    # Keep state comfortably below Prosperity's traderData limit.
    if len(mids) > ROLLING_WINDOW:
        mids = mids[-ROLLING_WINDOW:]
    return json.dumps({"step": step, "mids": mids})


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


def current_mid(depth: OrderDepth) -> Optional[float]:
    best_bid, best_ask = best_bid_ask(depth)
    if best_bid is None or best_ask is None:
        return None
    return (best_bid + best_ask) / 2.0


def official_capacity(position: int, side: str) -> int:
    if side == "BUY":
        return max(0, POSITION_LIMIT - position)
    return max(0, POSITION_LIMIT + position)


def clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def target_position_from_z(z: float) -> int:
    # Negative z means cheap -> target long.
    # Positive z means expensive -> target short.
    target = -TARGET_PER_Z * z
    return int(round(clip(target, -MAX_ACTIVE_POS, MAX_ACTIVE_POS)))


# ── ACTIVE TAKER LOGIC ──────────────────────────────────────────────────────

def active_taker_orders(
    depth: OrderDepth,
    position: int,
    anchor: Optional[float],
    z: Optional[float],
) -> Tuple[List[Order], int, str]:
    """
    Return active orders, simulated local position after those orders if fully
    filled, and a text reason.

    Important: we only take the visible L1 size to avoid leaving aggressive
    residual orders resting at the crossing price.
    """
    orders: List[Order] = []
    reason = "no_signal"

    if anchor is None or z is None:
        return orders, position, "no_anchor"

    best_bid, best_ask = best_bid_ask(depth)
    if best_bid is None or best_ask is None:
        return orders, position, "empty_book"

    target_pos = target_position_from_z(z)

    # BUY taker: price is cheap and ask is still below anchor after buffer.
    if z <= -Z_STRONG and best_ask < anchor - EDGE_BUFFER and position < target_pos:
        visible_ask_qty = -depth.sell_orders.get(best_ask, 0)
        desired = target_pos - position
        qty = min(
            TAKER_CHUNK,
            visible_ask_qty,
            official_capacity(position, "BUY"),
            max(0, MAX_ACTIVE_POS - position),
            desired,
        )
        if qty > 0:
            orders.append(Order(SYMBOL, int(best_ask), int(qty)))
            position += qty
            reason = "take_buy_low_z"

    # SELL taker: price is rich and bid is still above anchor after buffer.
    elif z >= Z_STRONG and best_bid > anchor + EDGE_BUFFER and position > target_pos:
        visible_bid_qty = depth.buy_orders.get(best_bid, 0)
        desired = position - target_pos
        qty = min(
            TAKER_CHUNK,
            visible_bid_qty,
            official_capacity(position, "SELL"),
            max(0, position + MAX_ACTIVE_POS),
            desired,
        )
        if qty > 0:
            orders.append(Order(SYMBOL, int(best_bid), -int(qty)))
            position -= qty
            reason = "take_sell_high_z"

    return orders, position, reason


# ── PASSIVE MM LOGIC ────────────────────────────────────────────────────────

def passive_side_size(position_after_takers: int, side: str, z: Optional[float]) -> int:
    """
    Size passive quote by inventory and z-score.
    This is simple on purpose:
      - cheap market: reduce/suppress ask because selling worsens short bias
      - rich market: reduce/suppress bid because buying worsens long bias
      - also cap inventory-worsening side around HARD_INV_CAP
    """
    pos = position_after_takers
    size = BASE_MM_SIZE

    # Hard inventory safety first.
    if side == "BUY" and pos >= HARD_INV_CAP:
        return 0
    if side == "SELL" and pos <= -HARD_INV_CAP:
        return 0

    # Passive inventory comfort cap:
    # If already long, buying worsens. If already short, selling worsens.
    if side == "BUY" and pos >= COMFORT_INV_CAP:
        size = min(size, 5)
    if side == "SELL" and pos <= -COMFORT_INV_CAP:
        size = min(size, 5)

    if z is not None:
        # Strong cheap: prefer long, avoid adding shorts through asks.
        if z <= -Z_STRONG:
            if side == "SELL" and pos <= COMFORT_INV_CAP:
                return 0
            if side == "BUY":
                size = BASE_MM_SIZE

        # Mild cheap: reduce asks, keep bids.
        elif z <= -Z_SOFT:
            if side == "SELL" and pos <= 0:
                size = min(size, BASE_MM_SIZE // 4)
            elif side == "SELL":
                size = min(size, BASE_MM_SIZE // 2)

        # Strong rich: prefer short, avoid adding longs through bids.
        elif z >= Z_STRONG:
            if side == "BUY" and pos >= -COMFORT_INV_CAP:
                return 0
            if side == "SELL":
                size = BASE_MM_SIZE

        # Mild rich: reduce bids, keep asks.
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

    # Avoid simultaneous active buy + passive sell, or active sell + passive buy.
    # This prevents self-cross style order sets and keeps the test clean.
    has_active_buy = any(o.quantity > 0 and o.price >= best_ask for o in existing_orders)
    has_active_sell = any(o.quantity < 0 and o.price <= best_bid for o in existing_orders)

    # Aggregate official capacity must consider all orders sent this tick.
    used_buy = sum(o.quantity for o in existing_orders if o.quantity > 0)
    used_sell = -sum(o.quantity for o in existing_orders if o.quantity < 0)

    buy_cap = max(0, official_capacity(original_position, "BUY") - used_buy)
    sell_cap = max(0, official_capacity(original_position, "SELL") - used_sell)

    # Passive bid.
    if not has_active_sell:
        bid_size = min(passive_side_size(position_after_takers, "BUY", z), buy_cap)
        if bid_size > 0:
            orders.append(Order(SYMBOL, int(bid_px), int(bid_size)))

    # Passive ask.
    if not has_active_buy:
        ask_size = min(passive_side_size(position_after_takers, "SELL", z), sell_cap)
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

        if SYMBOL not in state.order_depths:
            return result, 0, save_state(step + 1, mids)

        depth = state.order_depths[SYMBOL]
        position = state.position.get(SYMBOL, 0)

        mid = current_mid(depth)
        if mid is None:
            result[SYMBOL] = []
            return result, 0, save_state(step + 1, mids)

        # Signal uses previous + current history.
        mids.append(float(mid))
        if len(mids) > ROLLING_WINDOW:
            mids = mids[-ROLLING_WINDOW:]

        anchor, sigma = mean_std(mids)
        z = None
        if anchor is not None and sigma is not None and len(mids) >= MIN_HISTORY_FOR_SIGNAL:
            z = (mid - anchor) / sigma

        orders: List[Order] = []

        # 1) Active taker overlay.
        taker_orders, pos_after_taker, taker_reason = active_taker_orders(depth, position, anchor, z)
        orders.extend(taker_orders)

        # 2) Passive market-making layer.
        mm_orders = passive_mm_orders(depth, position, pos_after_taker, z, orders)
        orders.extend(mm_orders)

        result[SYMBOL] = orders

        if VERBOSE:
            best_bid, best_ask = best_bid_ask(depth)
            spread = (best_ask - best_bid) if best_bid is not None and best_ask is not None else None
            z_s = "None" if z is None else f"{z:.2f}"
            anchor_s = "None" if anchor is None else f"{anchor:.2f}"
            print(
                f"[HYDRO_MR_TAKER] ts={state.timestamp} step={step} "
                f"mid={mid:.1f} anchor={anchor_s} z={z_s} spread={spread} "
                f"pos={position} pos_after_taker={pos_after_taker} "
                f"taker={taker_reason} orders={orders}"
            )

        return result, 0, save_state(step + 1, mids)
