"""
HYDROGEL_PACK — Noah Semi-Anchor v3 + Mark 14 Guard

Base: noah_hydrogel/525513.py — passive anchor MM, unchanged.
Guard: when Mark 14 sells HGP → suppress bids for 5000 ts units (~50 ticks)
       when Mark 14 buys HGP  → suppress asks for 5000 ts units (~50 ticks)

Rationale: Mark 14 is an informed directional trader on HGP (MO10 = -3.99 buy /
-4.37 sell). The base passive strategy collects spread but blindly buys into
Mark 14's sells (and sells into his buys). The guard prevents trading against
informed flow without changing the passive architecture.

Guard window calibrated from ale57 (reduced from ale54's 14000 which blocked
14% of the day; 5000 is ~50 ticks = a few minutes of protection).
"""

from datamodel import OrderDepth, TradingState, Order
from typing import List, Optional
import json


PRODUCT = "HYDROGEL_PACK"
POS_LIMIT = 200

# ── Base MM (unchanged from noah v3) ─────────────────────────────────────────
OBI_ALPHA = 11.0
HALF_SPREAD = 7
POSITION_SKEW_FACTOR = 0.10
QUOTE_SIZE = 12
OBI_SUPPRESS_THRESHOLD = 0.15
EMERGENCY_THRESHOLD = 150

# ── Semi-fixed anchor (unchanged from noah v3) ────────────────────────────────
FIXED_ANCHOR = 10000.0
ANCHOR_WINDOW = 500
MIN_HISTORY_FOR_ANCHOR = 300
FIXED_WEIGHT = 0.85
ROLLING_WEIGHT = 0.15
ANCHOR_EDGE = 18.0
DEEP_ANCHOR_EDGE = 32.0
EXTREME_ANCHOR_EDGE = 42.0
MAX_ANCHOR_LONG = 85
MAX_ANCHOR_SHORT = -65
ANCHOR_QTY = 6
DEEP_ANCHOR_QTY = 10
ENABLE_DEEP_LADDER = True
LADDER_QTY_1 = 6
LADDER_QTY_2 = 4
LADDER_QTY_3 = 3
MOM_FAST = 5
MOM_MED = 20
FALLING_KNIFE_FAST = -10.0
FALLING_KNIFE_MED = -22.0
RISING_KNIFE_FAST = 10.0
RISING_KNIFE_MED = 22.0
SUPPRESS_OPPOSITE_ON_DEEP = True
MIN_LONG_TO_KEEP_ASK_ON_DEEP_CHEAP = 45
MAX_SHORT_TO_KEEP_BID_ON_DEEP_RICH = -45

# ── Active reversion (unchanged from noah v3) ─────────────────────────────────
ENABLE_ACTIVE_BOOST = True
ACTIVE_COOLDOWN_STEPS = 6
ACTIVE_ENTRY_QTY = 5
ACTIVE_EXTREME_QTY = 8
ACTIVE_MAX_LONG = 105
ACTIVE_MAX_SHORT = -85
ACTIVE_REDUCE_QTY = 8
ACTIVE_STRONG_REDUCE_QTY = 12
REDUCE_EDGE = 30.0
STRONG_REDUCE_EDGE = 42.0
MAX_SPREAD_ACTIVE = 17

# ── Mark 14 guard (from ale57 calibration) ────────────────────────────────────
MARK14_GUARD_WINDOW = 5000  # timestamp units; ale54 had 14000 (too long)

VERBOSE = False


# ── State helpers ─────────────────────────────────────────────────────────────

def load_state(trader_data: str) -> dict:
    default = {
        "mids": [],
        "step": 0,
        "last_active_step": -10_000,
        "buy_guard_until": -1,   # suppress bids while timestamp <= this
        "sell_guard_until": -1,  # suppress asks while timestamp <= this
    }
    if not trader_data:
        return default
    try:
        data = json.loads(trader_data)
        if not isinstance(data, dict):
            return default
        for k, v in default.items():
            data.setdefault(k, v)
        if not isinstance(data["mids"], list):
            data["mids"] = []
        return data
    except Exception:
        return default


def save_state(data: dict) -> str:
    mids = data.get("mids", [])
    if len(mids) > ANCHOR_WINDOW + 5:
        data["mids"] = mids[-(ANCHOR_WINDOW + 5):]
    return json.dumps(data)


def rolling_mean(values: List[float], window: int) -> Optional[float]:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def momentum(values: List[float], k: int) -> Optional[float]:
    if len(values) <= k:
        return None
    return values[-1] - values[-1 - k]


def compute_semi_anchor(mids: List[float]) -> float:
    roll = rolling_mean(mids, ANCHOR_WINDOW)
    if roll is None:
        return FIXED_ANCHOR
    return FIXED_WEIGHT * FIXED_ANCHOR + ROLLING_WEIGHT * roll


def add_or_merge_order(orders: List[Order], product: str, price: int, qty: int):
    if qty == 0:
        return
    for i, o in enumerate(orders):
        if o.symbol == product and o.price == int(price):
            if (o.quantity > 0 and qty > 0) or (o.quantity < 0 and qty < 0):
                orders[i] = Order(product, int(price), int(o.quantity + qty))
                return
    orders.append(Order(product, int(price), int(qty)))


def used_buy_qty(orders: List[Order]) -> int:
    return sum(o.quantity for o in orders if o.quantity > 0)


def used_sell_qty(orders: List[Order]) -> int:
    return sum(-o.quantity for o in orders if o.quantity < 0)


def visible_best_ask_qty(sell_orders: dict, best_ask: int) -> int:
    return max(0, abs(int(sell_orders.get(best_ask, 0))))


def visible_best_bid_qty(buy_orders: dict, best_bid: int) -> int:
    return max(0, int(buy_orders.get(best_bid, 0)))


class Trader:

    def run(self, state: TradingState):
        result = {}
        conversions = 0

        data = load_state(state.traderData)
        mids = [float(x) for x in data.get("mids", [])]
        step = int(data.get("step", 0))
        timestamp = int(getattr(state, "timestamp", 0))

        # ── Update Mark 14 guard ─────────────────────────────────────────────
        # Read both market_trades (bot-vs-bot) and own_trades (our fills).
        # Skip trades where buyer or seller is missing — same filter as ale57.
        for book_name in ("own_trades", "market_trades"):
            trade_book = getattr(state, book_name, {}) or {}
            for trade in trade_book.get(PRODUCT, []):
                buyer = getattr(trade, "buyer", "") or ""
                seller = getattr(trade, "seller", "") or ""
                if not buyer or not seller:
                    continue
                if buyer == "Mark 14":
                    # Mark 14 buying → informed upward push → don't sell into it
                    data["sell_guard_until"] = max(
                        int(data.get("sell_guard_until", -1)),
                        timestamp + MARK14_GUARD_WINDOW,
                    )
                if seller == "Mark 14":
                    # Mark 14 selling → informed downward push → don't buy into it
                    data["buy_guard_until"] = max(
                        int(data.get("buy_guard_until", -1)),
                        timestamp + MARK14_GUARD_WINDOW,
                    )

        m14_no_bid = int(data.get("buy_guard_until", -1)) >= timestamp
        m14_no_ask = int(data.get("sell_guard_until", -1)) >= timestamp

        # ── Main product loop ─────────────────────────────────────────────────
        for product in state.order_depths:
            if product != PRODUCT:
                continue

            order_depth: OrderDepth = state.order_depths[product]
            orders: List[Order] = []

            buy_orders = order_depth.buy_orders
            sell_orders = order_depth.sell_orders

            if not buy_orders or not sell_orders:
                result[product] = orders
                continue

            best_bid = max(buy_orders.keys())
            best_ask = min(sell_orders.keys())

            bid_vol_1 = buy_orders[best_bid]
            ask_vol_1 = abs(sell_orders[best_ask])

            mid = (best_bid + best_ask) / 2.0
            spread = best_ask - best_bid

            mids.append(float(mid))
            if len(mids) > ANCHOR_WINDOW + 5:
                mids = mids[-(ANCHOR_WINDOW + 5):]

            semi_anchor = compute_semi_anchor(mids)
            mom5 = momentum(mids, MOM_FAST)
            mom20 = momentum(mids, MOM_MED)

            position = state.position.get(product, 0)
            max_buy = POS_LIMIT - position
            max_sell = POS_LIMIT + position

            if max_buy <= 0 and max_sell <= 0:
                result[product] = orders
                continue

            # ── 1. Base OBI-adjusted MM ───────────────────────────────────────
            total_vol_1 = bid_vol_1 + ask_vol_1
            obi = (bid_vol_1 - ask_vol_1) / total_vol_1 if total_vol_1 > 0 else 0.0
            fv = mid + OBI_ALPHA * obi
            pos_skew = round(position * POSITION_SKEW_FACTOR)

            our_bid_price = round(fv - HALF_SPREAD - pos_skew)
            our_ask_price = round(fv + HALF_SPREAD - pos_skew)
            our_bid_price = min(our_bid_price, best_ask - 1)
            our_ask_price = max(our_ask_price, best_bid + 1)

            bid_qty = min(QUOTE_SIZE, max_buy)
            ask_qty = min(QUOTE_SIZE, max_sell)

            # Mark 14 guard is the first suppression gate; OBI/emergency can add to it
            suppress_bid = m14_no_bid
            suppress_ask = m14_no_ask

            if obi < -OBI_SUPPRESS_THRESHOLD:
                suppress_bid = True
                our_ask_price = round(fv + 4 - pos_skew)
                our_ask_price = max(our_ask_price, best_bid + 1)
            elif obi > OBI_SUPPRESS_THRESHOLD:
                suppress_ask = True
                our_bid_price = round(fv - 4 - pos_skew)
                our_bid_price = min(our_bid_price, best_ask - 1)

            if position >= EMERGENCY_THRESHOLD:
                suppress_bid = True
                our_ask_price = max(round(fv - 5), best_bid + 1)
                ask_qty = min(20, max_sell)
            elif position <= -EMERGENCY_THRESHOLD:
                suppress_ask = True
                our_bid_price = min(round(fv + 5), best_ask - 1)
                bid_qty = min(20, max_buy)

            # ── 2. Semi-fixed anchor state ────────────────────────────────────
            cheap_edge = semi_anchor - best_ask
            rich_edge = best_bid - semi_anchor

            falling_knife = rising_knife = rebound_confirmed = rollover_confirmed = False
            if mom5 is not None and mom20 is not None:
                falling_knife = mom5 <= FALLING_KNIFE_FAST and mom20 <= FALLING_KNIFE_MED
                rising_knife = mom5 >= RISING_KNIFE_FAST and mom20 >= RISING_KNIFE_MED
            if mom5 is not None:
                rebound_confirmed = mom5 > 0
                rollover_confirmed = mom5 < 0

            enough_history = len(mids) >= MIN_HISTORY_FOR_ANCHOR

            # ── 3. Active reversion boost (guard applies here too) ────────────
            active_reason = "none"
            if (
                ENABLE_ACTIVE_BOOST
                and enough_history
                and spread <= MAX_SPREAD_ACTIVE
                and step - int(data.get("last_active_step", -10_000)) >= ACTIVE_COOLDOWN_STEPS
            ):
                if (
                    position > 35 and rich_edge >= REDUCE_EDGE
                    and rollover_confirmed and max_sell > 0
                    and not m14_no_ask
                ):
                    qty_cap = ACTIVE_STRONG_REDUCE_QTY if rich_edge >= STRONG_REDUCE_EDGE else ACTIVE_REDUCE_QTY
                    qty = min(qty_cap, visible_best_bid_qty(buy_orders, best_bid), max_sell, position)
                    if qty > 0:
                        add_or_merge_order(orders, product, int(best_bid), -int(qty))
                        data["last_active_step"] = step
                        active_reason = "reduce_long_rich_rollover"

                elif (
                    position < -25 and cheap_edge >= REDUCE_EDGE
                    and rebound_confirmed and max_buy > 0
                    and not m14_no_bid
                ):
                    qty_cap = ACTIVE_STRONG_REDUCE_QTY if cheap_edge >= STRONG_REDUCE_EDGE else ACTIVE_REDUCE_QTY
                    qty = min(qty_cap, visible_best_ask_qty(sell_orders, best_ask), max_buy, -position)
                    if qty > 0:
                        add_or_merge_order(orders, product, int(best_ask), int(qty))
                        data["last_active_step"] = step
                        active_reason = "cover_short_cheap_rebound"

                elif (
                    cheap_edge >= EXTREME_ANCHOR_EDGE and rebound_confirmed
                    and not falling_knife and position < ACTIVE_MAX_LONG
                    and max_buy > 0 and best_ask <= semi_anchor + 8
                    and obi > -OBI_SUPPRESS_THRESHOLD
                    and not m14_no_bid
                ):
                    qty_cap = ACTIVE_EXTREME_QTY if cheap_edge >= EXTREME_ANCHOR_EDGE + 15 else ACTIVE_ENTRY_QTY
                    qty = min(qty_cap, visible_best_ask_qty(sell_orders, best_ask), max_buy, ACTIVE_MAX_LONG - position)
                    if qty > 0:
                        add_or_merge_order(orders, product, int(best_ask), int(qty))
                        data["last_active_step"] = step
                        active_reason = "active_buy_extreme_cheap_rebound"

                elif (
                    rich_edge >= EXTREME_ANCHOR_EDGE and rollover_confirmed
                    and not rising_knife and position > ACTIVE_MAX_SHORT
                    and max_sell > 0 and best_bid >= semi_anchor - 12
                    and obi < OBI_SUPPRESS_THRESHOLD
                    and not m14_no_ask
                ):
                    qty_cap = ACTIVE_EXTREME_QTY if rich_edge >= EXTREME_ANCHOR_EDGE + 15 else ACTIVE_ENTRY_QTY
                    qty = min(qty_cap, visible_best_bid_qty(buy_orders, best_bid), max_sell, position - ACTIVE_MAX_SHORT)
                    if qty > 0:
                        add_or_merge_order(orders, product, int(best_bid), -int(qty))
                        data["last_active_step"] = step
                        active_reason = "active_sell_extreme_rich_rollover"

            # ── 4. Semi-fixed passive overlay ─────────────────────────────────
            cheap_overlay = False
            rich_overlay = False
            cheap_bid_prices: List[int] = []
            rich_ask_prices: List[int] = []
            cheap_ladder_qtys: List[int] = []
            rich_ladder_qtys: List[int] = []

            if (
                enough_history and cheap_edge >= ANCHOR_EDGE
                and not falling_knife and position < MAX_ANCHOR_LONG
                and spread <= 17 and max_buy > used_buy_qty(orders)
                and not suppress_bid
            ):
                cheap_overlay = True
                deep = cheap_edge >= DEEP_ANCHOR_EDGE
                top_bid = min(best_bid + 1, best_ask - 1)
                if deep and ENABLE_DEEP_LADDER:
                    cheap_bid_prices = [top_bid, best_bid - 1, best_bid - 3]
                    cheap_ladder_qtys = [LADDER_QTY_1, LADDER_QTY_2, LADDER_QTY_3]
                else:
                    cheap_bid_prices = [top_bid]
                    cheap_ladder_qtys = [DEEP_ANCHOR_QTY if deep else ANCHOR_QTY]
                if not suppress_bid and our_bid_price >= top_bid and not deep:
                    cheap_bid_prices = [best_bid - 1]
                if SUPPRESS_OPPOSITE_ON_DEEP and deep and position < MIN_LONG_TO_KEEP_ASK_ON_DEEP_CHEAP:
                    suppress_ask = True

            if (
                enough_history and rich_edge >= ANCHOR_EDGE
                and not rising_knife and position > MAX_ANCHOR_SHORT
                and spread <= 17 and max_sell > used_sell_qty(orders)
                and not suppress_ask
            ):
                rich_overlay = True
                deep = rich_edge >= DEEP_ANCHOR_EDGE
                top_ask = max(best_ask - 1, best_bid + 1)
                if deep and ENABLE_DEEP_LADDER:
                    rich_ask_prices = [top_ask, best_ask + 1, best_ask + 3]
                    rich_ladder_qtys = [LADDER_QTY_1, LADDER_QTY_2, LADDER_QTY_3]
                else:
                    rich_ask_prices = [top_ask]
                    rich_ladder_qtys = [DEEP_ANCHOR_QTY if deep else ANCHOR_QTY]
                if not suppress_ask and our_ask_price <= top_ask and not deep:
                    rich_ask_prices = [best_ask + 1]
                if SUPPRESS_OPPOSITE_ON_DEEP and deep and position > MAX_SHORT_TO_KEEP_BID_ON_DEEP_RICH:
                    suppress_bid = True

            # ── 5. Send base orders ───────────────────────────────────────────
            if not suppress_bid and bid_qty > 0 and our_bid_price < best_ask:
                remaining = max_buy - used_buy_qty(orders)
                qty = min(bid_qty, remaining)
                if qty > 0:
                    add_or_merge_order(orders, product, int(our_bid_price), int(qty))

            if not suppress_ask and ask_qty > 0 and our_ask_price > best_bid:
                remaining = max_sell - used_sell_qty(orders)
                qty = min(ask_qty, remaining)
                if qty > 0:
                    add_or_merge_order(orders, product, int(our_ask_price), -int(qty))

            # ── 6. Send passive overlay ladders ───────────────────────────────
            if cheap_overlay and cheap_bid_prices:
                remaining_buy = max_buy - used_buy_qty(orders)
                overlay_room = max(0, MAX_ANCHOR_LONG - position)
                for px, q in zip(cheap_bid_prices, cheap_ladder_qtys):
                    if remaining_buy <= 0 or overlay_room <= 0:
                        break
                    px = min(int(px), best_ask - 1)
                    if px <= 0 or px >= best_ask:
                        continue
                    qty = min(int(q), remaining_buy, overlay_room)
                    if qty > 0:
                        add_or_merge_order(orders, product, px, qty)
                        remaining_buy -= qty
                        overlay_room -= qty

            if rich_overlay and rich_ask_prices:
                remaining_sell = max_sell - used_sell_qty(orders)
                overlay_room = max(0, position - MAX_ANCHOR_SHORT)
                for px, q in zip(rich_ask_prices, rich_ladder_qtys):
                    if remaining_sell <= 0 or overlay_room <= 0:
                        break
                    px = max(int(px), best_bid + 1)
                    if px <= best_bid:
                        continue
                    qty = min(int(q), remaining_sell, overlay_room)
                    if qty > 0:
                        add_or_merge_order(orders, product, px, -qty)
                        remaining_sell -= qty
                        overlay_room -= qty

            result[product] = orders

        data["mids"] = mids
        data["step"] = step + 1
        return result, conversions, save_state(data)
