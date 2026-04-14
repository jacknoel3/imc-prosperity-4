"""
Prosperity 4 — Round 0 (Tutorial) Trader.

Strategy: pure market making around a fixed fair value of 10,000 for EMERALDS.
TOMATOES is handled with a minimal placeholder (no trades) — we'll tackle it
in a separate iteration once the emeralds plumbing is proven to work.

Three-layer logic, executed in this order every tick:
    1) TAKE   — hit any bot order already crossing fair value (free edge)
    2) FLATTEN — if inventory is uncomfortably large, post a break-even
                 order at fair value to unwind risk
    3) MAKE   — post passive quotes one tick inside the bot wall
                (9993 buy, 10007 sell), sized to respect position limits

Position accounting is tracked carefully: after every intended order, we
update a running 'projected position' so that the combined buy quantity
cannot push us past +limit, and the combined sell quantity cannot push us
past -limit. This matches the exchange's rejection rule (aggregated orders).
"""

from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List


# ---------------------------------------------------------------------------
# Tunable parameters — kept at the top so we can A/B test easily
# ---------------------------------------------------------------------------

EMERALDS = "EMERALDS"
TOMATOES = "TOMATOES"

POSITION_LIMIT = {
    EMERALDS: 80,
    TOMATOES: 80,
}

# Emeralds: fair value is effectively a constant in Round 0 data
EMERALD_FAIR_VALUE = 10000

# Quote one tick inside the bot wall (wall lives at 9992 / 10008)
EMERALD_BUY_QUOTE = 9993
EMERALD_SELL_QUOTE = 10007

# Inventory thresholds for the flatten step
EMERALD_SOFT_INVENTORY = 40   # start posting break-even unwind orders
EMERALD_HARD_INVENTORY = 65   # stop quoting on the side that worsens inventory

# How many units to quote per side when at neutral inventory.
# Must be small enough that occasional over-fills don't blow the limit.
EMERALD_BASE_QUOTE_SIZE = 30


class Trader:

    def bid(self):
        # Required for Round 2 only; ignored in Round 0. Kept for safety.
        return 15

    # -----------------------------------------------------------------------
    # Main entry point
    # -----------------------------------------------------------------------
    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}

        # Handle emeralds with the full make/take/flatten logic
        if EMERALDS in state.order_depths:
            result[EMERALDS] = self._trade_emeralds(
                state.order_depths[EMERALDS],
                state.position.get(EMERALDS, 0),
            )

        # Placeholder — no orders for tomatoes yet
        if TOMATOES in state.order_depths:
            result[TOMATOES] = []

        # Light heartbeat log every 100 ticks — useful for debugging fills
        if state.timestamp % 10_000 == 0:
            pos_em = state.position.get(EMERALDS, 0)
            n_orders = len(result.get(EMERALDS, []))
            print(f"[t={state.timestamp}] pos_em={pos_em} orders={n_orders}")

        conversions = 0
        # No state persistence needed — fair value is constant, position is
        # provided by the exchange each tick. Keep traderData empty.
        trader_data = ""
        return result, conversions, trader_data

    # -----------------------------------------------------------------------
    # Emeralds strategy
    # -----------------------------------------------------------------------
    def _trade_emeralds(self, order_depth: OrderDepth, position: int) -> List[Order]:
        orders: List[Order] = []
        limit = POSITION_LIMIT[EMERALDS]
        fair = EMERALD_FAIR_VALUE

        # Projected position accounting:
        #   'buy_capacity'  = how many more units we are allowed to BUY  this tick
        #   'sell_capacity' = how many more units we are allowed to SELL this tick
        # These shrink as we add take/flatten/make orders. They are the
        # exchange's rejection rule expressed as a running budget.
        buy_capacity = limit - position       # e.g. pos=+30 → can still buy 50
        sell_capacity = limit + position      # e.g. pos=+30 → can still sell 110 worth... capped at 80
        # Note: 'sell_capacity' represents how far we can move DOWN in position.
        # If pos=+30, going to -80 means selling 110 units. That's correct —
        # the limit is |new_position| <= 80.

        # ---- STEP 1: TAKE any free edge -----------------------------------
        # Scan asks that are strictly below fair value → buy them.
        # sell_orders values are negative, so available volume = -volume.
        # We iterate from the lowest (best) ask upwards.
        if order_depth.sell_orders:
            for ask_price in sorted(order_depth.sell_orders.keys()):
                if ask_price >= fair:
                    break  # no more edge available on this side
                if buy_capacity <= 0:
                    break
                available = -order_depth.sell_orders[ask_price]  # positive
                take_qty = min(available, buy_capacity)
                if take_qty > 0:
                    orders.append(Order(EMERALDS, ask_price, take_qty))
                    buy_capacity -= take_qty
                    position += take_qty  # track for flatten/make decisions

        # Scan bids that are strictly above fair value → sell to them.
        # buy_orders values are positive. Iterate from highest (best) bid downwards.
        if order_depth.buy_orders:
            for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
                if bid_price <= fair:
                    break
                if sell_capacity <= 0:
                    break
                available = order_depth.buy_orders[bid_price]
                take_qty = min(available, sell_capacity)
                if take_qty > 0:
                    # Sell orders use negative quantity
                    orders.append(Order(EMERALDS, bid_price, -take_qty))
                    sell_capacity -= take_qty
                    position -= take_qty

        # ---- STEP 2: FLATTEN at fair value if inventory is heavy ---------
        # If we're uncomfortably long, post a sell at 10000 (break-even).
        # If short, post a buy at 10000. This is in ADDITION to the normal
        # make quotes — it's an extra order, not a replacement.
        if position > EMERALD_SOFT_INVENTORY and sell_capacity > 0:
            flatten_qty = min(position - EMERALD_SOFT_INVENTORY, sell_capacity)
            if flatten_qty > 0:
                orders.append(Order(EMERALDS, fair, -flatten_qty))
                sell_capacity -= flatten_qty
        elif position < -EMERALD_SOFT_INVENTORY and buy_capacity > 0:
            flatten_qty = min(-position - EMERALD_SOFT_INVENTORY, buy_capacity)
            if flatten_qty > 0:
                orders.append(Order(EMERALDS, fair, flatten_qty))
                buy_capacity -= flatten_qty

        # ---- STEP 3: MAKE passive quotes at 9993 / 10007 -----------------
        # Inventory skew: shrink the side that would worsen inventory,
        # grow the side that would improve it. Simple linear formula:
        #   buy_size  = base - position/2   (fewer buys if already long)
        #   sell_size = base + position/2   (more sells if already long)
        # Then clamped to remaining capacity and >= 0.
        skew = position // 2
        desired_buy = max(0, EMERALD_BASE_QUOTE_SIZE - skew)
        desired_sell = max(0, EMERALD_BASE_QUOTE_SIZE + skew)

        # Hard threshold: don't quote the bad side at all if inventory is extreme
        if position >= EMERALD_HARD_INVENTORY:
            desired_buy = 0
        if position <= -EMERALD_HARD_INVENTORY:
            desired_sell = 0

        make_buy_qty = min(desired_buy, buy_capacity)
        make_sell_qty = min(desired_sell, sell_capacity)

        if make_buy_qty > 0:
            orders.append(Order(EMERALDS, EMERALD_BUY_QUOTE, make_buy_qty))
        if make_sell_qty > 0:
            orders.append(Order(EMERALDS, EMERALD_SELL_QUOTE, -make_sell_qty))

        return orders
