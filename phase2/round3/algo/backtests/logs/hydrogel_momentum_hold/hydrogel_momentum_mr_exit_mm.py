"""
HYDROGEL_PACK — Momentum-Confirmed MR MM with Active Profit-Taking
IMC Prosperity 4 | Round 3

Version purpose:
    Updated version of hydrogel_momentum_confirmed_mr_mm.

What we learned from the previous test:
    - Momentum-confirmed active buys worked: they bought near the dip/rebound.
    - The bot did not actively sell the rebound/top, leaving long inventory (+16).
    - Entry should stay conservative; exit should become more responsive.

Core structure:
    1. Passive market making base:
         bid = best_bid + 1
         ask = best_ask - 1

    2. Active entry:
         cheap + z extreme + momentum rebound -> buy at ask
         rich + z extreme + momentum rollover -> sell at bid

    3. Active profit-taking exit:
         if we actively bought and best_bid >= active_avg_buy + PROFIT_TARGET:
             sell at best_bid
         if we actively sold and best_ask <= active_avg_sell - PROFIT_TARGET:
             buy at best_ask

    4. Reversion exit:
         if long and z has normalized/rebounded and short-term momentum rolls over:
             sell partial position at bid, if at least minimally profitable
         if short and z has normalized/rebounded downward and short-term momentum turns up:
             buy partial position at ask, if at least minimally profitable

Important:
    - This is still a test bot.
    - It keeps active entry strict and adds faster active exits.
    - Set VERBOSE=False before final submission.
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
MOM_CONFIRM_WINDOW = 5
MOM_CONTEXT_WINDOW = 20

# Active entry settings.
EDGE_BUFFER_ENTRY = 12.0
TAKER_CHUNK = 4
ENTRY_COOLDOWN_STEPS = 5

# Active profit-taking / exit settings.
PROFIT_TARGET = 10.0          # active lot exit target, in ticks
MIN_EXIT_PROFIT = 5.0         # weaker reversion exit must still be profitable
EXIT_CHUNK = 4
EXIT_COOLDOWN_STEPS = 3

# Fresh active inventory cap. Active entries may rescue inventory, but fresh
# directional active inventory remains small.
MIN_BAD_INV_TO_RESCUE = 8
MAX_FRESH_ACTIVE_POS = 20

# Passive MM.
INSIDE_OFFSET = 1
BASE_MM_SIZE = 20
MIN_SPREAD_MM = 7
MAX_SPREAD_MM = 17

# Practical passive inventory limits.
COMFORT_INV_CAP = 60
HARD_INV_CAP = 120

VERBOSE = True


# ── STATE HELPERS ───────────────────────────────────────────────────────────

def load_state(trader_data: str) -> dict:
    default = {
        "step": 0,
        "mids": [],
        "last_entry_step": -10_000,
        "last_exit_step": -10_000,
        "active_long_qty": 0,
        "active_long_avg": 0.0,
        "active_short_qty": 0,
        "active_short_avg": 0.0,
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
    last_entry_step: int,
    last_exit_step: int,
    active_long_qty: int,
    active_long_avg: float,
    active_short_qty: int,
    active_short_avg: float,
) -> str:
    if len(mids) > ROLLING_WINDOW:
        mids = mids[-ROLLING_WINDOW:]
    return json.dumps({
        "step": int(step),
        "mids": mids,
        "last_entry_step": int(last_entry_step),
        "last_exit_step": int(last_exit_step),
        "active_long_qty": int(max(0, active_long_qty)),
        "active_long_avg": float(active_long_avg if active_long_qty > 0 else 0.0),
        "active_short_qty": int(max(0, active_short_qty)),
        "active_short_avg": float(active_short_avg if active_short_qty > 0 else 0.0),
    })


def mean_std(xs: List[float]) -> Tuple[Optional[float], Optional[float]]:
    n = len(xs)
    if n < 2:
        return None, None
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return m, math.sqrt(max(var, 1e-12))


def add_active_long(qty_old: int, avg_old: float, qty_add: int, price: float) -> Tuple[int, float]:
    if qty_add <= 0:
        return qty_old, avg_old
    new_qty = qty_old + qty_add
    new_avg = (qty_old * avg_old + qty_add * price) / max(1, new_qty)
    return new_qty, new_avg


def reduce_active_long(qty_old: int, avg_old: float, qty_sell: int) -> Tuple[int, float]:
    new_qty = max(0, qty_old - max(0, qty_sell))
    return new_qty, (avg_old if new_qty > 0 else 0.0)


def add_active_short(qty_old: int, avg_old: float, qty_add: int, price: float) -> Tuple[int, float]:
    if qty_add <= 0:
        return qty_old, avg_old
    new_qty = qty_old + qty_add
    new_avg = (qty_old * avg_old + qty_add * price) / max(1, new_qty)
    return new_qty, new_avg


def reduce_active_short(qty_old: int, avg_old: float, qty_buy: int) -> Tuple[int, float]:
    new_qty = max(0, qty_old - max(0, qty_buy))
    return new_qty, (avg_old if new_qty > 0 else 0.0)


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


# ── ACTIVE EXIT LOGIC ───────────────────────────────────────────────────────

def active_exit_orders(
    depth: OrderDepth,
    position: int,
    z: Optional[float],
    mom_confirm: Optional[float],
    step: int,
    last_exit_step: int,
    active_long_qty: int,
    active_long_avg: float,
    active_short_qty: int,
    active_short_avg: float,
) -> Tuple[List[Order], int, int, int, float, int, float, str]:
    """
    Active exits are checked before new entries.

    For active long lots:
      - Primary exit: sell if best_bid >= active_long_avg + PROFIT_TARGET.
      - Secondary exit: if z normalized and short momentum rolls over, sell
        if at least MIN_EXIT_PROFIT above active_long_avg.

    For active short lots:
      - Mirror logic.
    """
    orders: List[Order] = []
    reason = "no_exit"

    if step - last_exit_step < EXIT_COOLDOWN_STEPS:
        return orders, position, last_exit_step, active_long_qty, active_long_avg, active_short_qty, active_short_avg, "exit_cooldown"

    best_bid, best_ask = best_bid_ask(depth)
    if best_bid is None or best_ask is None:
        return orders, position, last_exit_step, active_long_qty, active_long_avg, active_short_qty, active_short_avg, "empty_book"

    # Exit active longs by selling at bid.
    if position > 0 and active_long_qty > 0:
        profit_ticks = best_bid - active_long_avg
        hard_profit = profit_ticks >= PROFIT_TARGET
        normalized_rollover = (
            z is not None and z >= -0.5 and
            mom_confirm is not None and mom_confirm < 0 and
            profit_ticks >= MIN_EXIT_PROFIT
        )

        if hard_profit or normalized_rollover:
            visible_bid_qty = max(0, depth.buy_orders.get(best_bid, 0))
            qty = min(
                EXIT_CHUNK,
                active_long_qty,
                position,
                visible_bid_qty,
                official_capacity(position, "SELL"),
            )
            if qty > 0:
                orders.append(Order(SYMBOL, int(best_bid), -int(qty)))
                position -= qty
                active_long_qty, active_long_avg = reduce_active_long(active_long_qty, active_long_avg, qty)
                last_exit_step = step
                reason = "exit_long_profit" if hard_profit else "exit_long_rollover"

    # Exit active shorts by buying at ask.
    if not orders and position < 0 and active_short_qty > 0:
        profit_ticks = active_short_avg - best_ask
        hard_profit = profit_ticks >= PROFIT_TARGET
        normalized_rebound = (
            z is not None and z <= 0.5 and
            mom_confirm is not None and mom_confirm > 0 and
            profit_ticks >= MIN_EXIT_PROFIT
        )

        if hard_profit or normalized_rebound:
            visible_ask_qty = max(0, -depth.sell_orders.get(best_ask, 0))
            qty = min(
                EXIT_CHUNK,
                active_short_qty,
                -position,
                visible_ask_qty,
                official_capacity(position, "BUY"),
            )
            if qty > 0:
                orders.append(Order(SYMBOL, int(best_ask), int(qty)))
                position += qty
                active_short_qty, active_short_avg = reduce_active_short(active_short_qty, active_short_avg, qty)
                last_exit_step = step
                reason = "exit_short_profit" if hard_profit else "exit_short_rebound"

    return orders, position, last_exit_step, active_long_qty, active_long_avg, active_short_qty, active_short_avg, reason


# ── ACTIVE ENTRY LOGIC ──────────────────────────────────────────────────────

def active_entry_orders(
    depth: OrderDepth,
    position: int,
    anchor: Optional[float],
    z: Optional[float],
    mom_confirm: Optional[float],
    step: int,
    last_entry_step: int,
    active_long_qty: int,
    active_long_avg: float,
    active_short_qty: int,
    active_short_avg: float,
) -> Tuple[List[Order], int, int, int, float, int, float, str]:
    """
    Momentum-confirmed active entries.

    Conservative by design:
      - mature rolling history only
      - cooldown
      - small chunks
      - strict anchor edge
      - mostly rescues wrong-way inventory; small fresh inventory allowed
    """
    orders: List[Order] = []
    reason = "no_entry"

    if anchor is None or z is None or mom_confirm is None:
        return orders, position, last_entry_step, active_long_qty, active_long_avg, active_short_qty, active_short_avg, "no_signal"

    if step - last_entry_step < ENTRY_COOLDOWN_STEPS:
        return orders, position, last_entry_step, active_long_qty, active_long_avg, active_short_qty, active_short_avg, "entry_cooldown"

    best_bid, best_ask = best_bid_ask(depth)
    if best_bid is None or best_ask is None:
        return orders, position, last_entry_step, active_long_qty, active_long_avg, active_short_qty, active_short_avg, "empty_book"

    # Cheap + rebound: buy.
    if z <= -Z_TAKE and mom_confirm > 0 and best_ask < anchor - EDGE_BUFFER_ENTRY:
        visible_ask_qty = max(0, -depth.sell_orders.get(best_ask, 0))

        if position <= -MIN_BAD_INV_TO_RESCUE:
            # Rescue short only toward flat.
            desired = -position
            qty = min(
                TAKER_CHUNK,
                visible_ask_qty,
                desired,
                official_capacity(position, "BUY"),
            )
            reason_if_fill = "entry_buy_rescue_short"
        elif position < MAX_FRESH_ACTIVE_POS:
            # Small fresh active long.
            desired = MAX_FRESH_ACTIVE_POS - position
            qty = min(
                TAKER_CHUNK,
                visible_ask_qty,
                desired,
                official_capacity(position, "BUY"),
            )
            reason_if_fill = "entry_buy_fresh_small"
        else:
            qty = 0
            reason_if_fill = "entry_buy_blocked_pos"

        if qty > 0:
            orders.append(Order(SYMBOL, int(best_ask), int(qty)))
            position += qty
            active_long_qty, active_long_avg = add_active_long(active_long_qty, active_long_avg, qty, float(best_ask))
            last_entry_step = step
            reason = reason_if_fill

    # Rich + rollover: sell.
    elif z >= Z_TAKE and mom_confirm < 0 and best_bid > anchor + EDGE_BUFFER_ENTRY:
        visible_bid_qty = max(0, depth.buy_orders.get(best_bid, 0))

        if position >= MIN_BAD_INV_TO_RESCUE:
            # Rescue long only toward flat.
            desired = position
            qty = min(
                TAKER_CHUNK,
                visible_bid_qty,
                desired,
                official_capacity(position, "SELL"),
            )
            reason_if_fill = "entry_sell_rescue_long"
        elif position > -MAX_FRESH_ACTIVE_POS:
            # Small fresh active short.
            desired = position + MAX_FRESH_ACTIVE_POS
            qty = min(
                TAKER_CHUNK,
                visible_bid_qty,
                desired,
                official_capacity(position, "SELL"),
            )
            reason_if_fill = "entry_sell_fresh_small"
        else:
            qty = 0
            reason_if_fill = "entry_sell_blocked_pos"

        if qty > 0:
            orders.append(Order(SYMBOL, int(best_bid), -int(qty)))
            position -= qty
            active_short_qty, active_short_avg = add_active_short(active_short_qty, active_short_avg, qty, float(best_bid))
            last_entry_step = step
            reason = reason_if_fill

    return orders, position, last_entry_step, active_long_qty, active_long_avg, active_short_qty, active_short_avg, reason


# ── PASSIVE MARKET MAKING ───────────────────────────────────────────────────

def passive_side_size(position_after_active: int, side: str, z: Optional[float], mom_confirm: Optional[float]) -> int:
    """
    Passive quote sizing:
      - Cheap: prefer long, suppress/reduce asks.
      - Rich: prefer short, suppress/reduce bids.
      - Momentum filter: if cheap but still falling, reduce bid; if rich but
        still rising, reduce ask.
    """
    pos = position_after_active
    size = BASE_MM_SIZE

    if side == "BUY" and pos >= HARD_INV_CAP:
        return 0
    if side == "SELL" and pos <= -HARD_INV_CAP:
        return 0

    if side == "BUY" and pos >= COMFORT_INV_CAP:
        size = min(size, 5)
    if side == "SELL" and pos <= -COMFORT_INV_CAP:
        size = min(size, 5)

    if z is not None:
        if z <= -Z_STRONG:
            # Cheap: don't worsen short inventory.
            if side == "SELL" and pos <= COMFORT_INV_CAP:
                return 0
            if side == "BUY":
                if mom_confirm is not None and mom_confirm < 0:
                    size = min(size, BASE_MM_SIZE // 4)
                else:
                    size = BASE_MM_SIZE

        elif z <= -Z_SOFT:
            if side == "SELL" and pos <= 0:
                size = min(size, BASE_MM_SIZE // 4)
            elif side == "SELL":
                size = min(size, BASE_MM_SIZE // 2)

        elif z >= Z_STRONG:
            # Rich: don't worsen long inventory.
            if side == "BUY" and pos >= -COMFORT_INV_CAP:
                return 0
            if side == "SELL":
                if mom_confirm is not None and mom_confirm > 0:
                    size = min(size, BASE_MM_SIZE // 4)
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
    position_after_active: int,
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
        bid_size = min(passive_side_size(position_after_active, "BUY", z, mom_confirm), buy_cap)
        if bid_size > 0:
            orders.append(Order(SYMBOL, int(bid_px), int(bid_size)))

    if not has_active_buy:
        ask_size = min(passive_side_size(position_after_active, "SELL", z, mom_confirm), sell_cap)
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

        last_entry_step = int(s.get("last_entry_step", -10_000))
        last_exit_step = int(s.get("last_exit_step", -10_000))

        active_long_qty = int(s.get("active_long_qty", 0))
        active_long_avg = float(s.get("active_long_avg", 0.0))
        active_short_qty = int(s.get("active_short_qty", 0))
        active_short_avg = float(s.get("active_short_avg", 0.0))

        if SYMBOL not in state.order_depths:
            return result, 0, save_state(
                step + 1, mids, last_entry_step, last_exit_step,
                active_long_qty, active_long_avg, active_short_qty, active_short_avg
            )

        depth = state.order_depths[SYMBOL]
        position = state.position.get(SYMBOL, 0)

        mid = mid_price(depth)
        if mid is None:
            result[SYMBOL] = []
            return result, 0, save_state(
                step + 1, mids, last_entry_step, last_exit_step,
                active_long_qty, active_long_avg, active_short_qty, active_short_avg
            )

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

        # 1) Active profit-taking / exit first.
        (
            exit_orders,
            pos_after_exit,
            last_exit_step,
            active_long_qty,
            active_long_avg,
            active_short_qty,
            active_short_avg,
            exit_reason,
        ) = active_exit_orders(
            depth, position, z, mom_confirm, step, last_exit_step,
            active_long_qty, active_long_avg, active_short_qty, active_short_avg
        )
        orders.extend(exit_orders)

        # 2) Active entry only if no active exit was sent this tick.
        if not exit_orders:
            (
                entry_orders,
                pos_after_active,
                last_entry_step,
                active_long_qty,
                active_long_avg,
                active_short_qty,
                active_short_avg,
                entry_reason,
            ) = active_entry_orders(
                depth, pos_after_exit, anchor, z, mom_confirm, step, last_entry_step,
                active_long_qty, active_long_avg, active_short_qty, active_short_avg
            )
            orders.extend(entry_orders)
        else:
            pos_after_active = pos_after_exit
            entry_reason = "blocked_by_exit"

        # 3) Passive market making layer.
        mm_orders = passive_mm_orders(depth, position, pos_after_active, z, mom_confirm, orders)
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
                f"[HYDRO_MOM_MR_EXIT] ts={state.timestamp} step={step} "
                f"mid={mid:.1f} anchor={anchor_s} z={z_s} "
                f"mom{MOM_CONFIRM_WINDOW}={mc_s} mom{MOM_CONTEXT_WINDOW}={mx_s} "
                f"spread={spread} pos={position} pos_after_active={pos_after_active} "
                f"active_long={active_long_qty}@{active_long_avg:.1f} "
                f"active_short={active_short_qty}@{active_short_avg:.1f} "
                f"exit={exit_reason} entry={entry_reason} orders={orders}"
            )

        return result, 0, save_state(
            step + 1, mids, last_entry_step, last_exit_step,
            active_long_qty, active_long_avg, active_short_qty, active_short_avg
        )
