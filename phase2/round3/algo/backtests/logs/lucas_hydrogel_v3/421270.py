"""
Hydrogel mean‑reversion and momentum strategy (no market making).

This strategy for Prosperity's Hydrogel market abandons simultaneous
market‑making and instead takes directional trades based on two
statistical signals:

1. **Mean reversion:** The HYDROGEL mid‑price tends to oscillate
   around a long‑term mean.  When the mid deviates far from this
   mean, the algorithm trades against the move, buying low or selling
   high, expecting reversion.  An extreme deviation triggers an
   immediate trade at the best available price.

2. **Short‑term momentum:** Between extremes, the algorithm looks at
   the recent change in the mid‑price (momentum).  If the price is
   ticking up and not too far above the mean, it buys at the best ask.
   If the price is ticking down and not too far below the mean, it
   sells at the best bid.  Trades are sized modestly to manage risk
   and scaled down as the position approaches the limit.

The strategy retains state across iterations via ``traderData`` to
store the previous midpoint.  It never posts passive quotes; all
orders are sent at the best bid or best ask to capture immediate
execution based on directional conviction.

Parameters:

* ``MEAN_PRICE`` – estimated long‑run fair value of HYDROGEL.
* ``MOMENTUM_THRESH`` – minimum change in mid required to act on
  momentum.
* ``MEAN_THRESH`` – deviation from the mean beyond which momentum
  signals are suppressed (mean reversion dominates).
* ``EXTREME_THRESH`` – deviation triggering an aggressive mean
  reversion trade.
* ``BASE_QTY`` – base size for trades.
* ``POSITION_LIMIT`` – maximum absolute inventory.

Uploaded file can be used as a trader in the Prosperity simulator.
"""

from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Tuple, Optional
import json
import jsonpickle


class Trader:
    MEAN_PRICE: int = 9991
    MOMENTUM_THRESH: int = 2
    MEAN_THRESH: int = 20
    EXTREME_THRESH: int = 40
    BASE_QTY: int = 10
    POSITION_LIMIT: int = 200

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        product = "HYDROGEL_PACK"
        orders: Dict[str, List[Order]] = {}
        conversions = 0
        position = state.position.get(product, 0)

        # parse previous midpoint from traderData
        prev_mid: Optional[int] = None
        if state.traderData:
            try:
                data = json.loads(state.traderData)
                prev_mid = data.get("prev_mid")
            except Exception:
                prev_mid = None

        depth: Optional[OrderDepth] = state.order_depths.get(product)
        if not depth:
            # no order book information; no trades
            return {product: []}, conversions, jsonpickle.encode({})

        best_bid = max(depth.buy_orders.keys()) if depth.buy_orders else None
        best_ask = min(depth.sell_orders.keys()) if depth.sell_orders else None

        # compute current midpoint; fallback to mean price when only one side present
        if best_bid is not None and best_ask is not None:
            mid = (best_bid + best_ask) // 2
        elif best_bid is not None:
            mid = best_bid
        elif best_ask is not None:
            mid = best_ask
        else:
            mid = self.MEAN_PRICE

        # default new traderData
        new_data = {"prev_mid": mid}

        orders_list: List[Order] = []

        # Cannot act on momentum without previous midpoint
        if prev_mid is None or best_bid is None or best_ask is None:
            orders[product] = []
            return orders, conversions, json.dumps(new_data)

        # compute signals
        delta = mid - prev_mid  # momentum
        deviation = mid - self.MEAN_PRICE

        # inventory scaling
        inv_frac = abs(position) / self.POSITION_LIMIT
        inv_scale = max(0.2, 1 - inv_frac)

        # extreme mean‑reversion trade
        if deviation > self.EXTREME_THRESH:
            # price extremely high: sell at best bid
            bid_vol = depth.buy_orders[best_bid]
            sell_cap = self.POSITION_LIMIT + position
            qty = min(bid_vol, sell_cap)
            if qty > 0:
                orders_list.append(Order(product, best_bid, -qty))
                position -= qty
        elif deviation < -self.EXTREME_THRESH:
            # price extremely low: buy at best ask
            ask_vol = -depth.sell_orders[best_ask]
            buy_cap = self.POSITION_LIMIT - position
            qty = min(ask_vol, buy_cap)
            if qty > 0:
                orders_list.append(Order(product, best_ask, qty))
                position += qty
        else:
            # moderate deviation: consider momentum
            # only trade momentum if price not too far from mean
            if deviation < self.MEAN_THRESH and deviation > -self.MEAN_THRESH:
                if delta > self.MOMENTUM_THRESH:
                    # upward momentum; buy at best ask
                    ask_vol = -depth.sell_orders[best_ask]
                    buy_cap = self.POSITION_LIMIT - position
                    qty = min(int(self.BASE_QTY * inv_scale), ask_vol, buy_cap)
                    if qty > 0:
                        orders_list.append(Order(product, best_ask, qty))
                        position += qty
                elif delta < -self.MOMENTUM_THRESH:
                    # downward momentum; sell at best bid
                    bid_vol = depth.buy_orders[best_bid]
                    sell_cap = self.POSITION_LIMIT + position
                    qty = min(int(self.BASE_QTY * inv_scale), bid_vol, sell_cap)
                    if qty > 0:
                        orders_list.append(Order(product, best_bid, -qty))
                        position -= qty
            # if deviation moderately high/low, avoid momentum trades and let mean reversion act slowly

        orders[product] = orders_list
        return orders, conversions, json.dumps(new_data)