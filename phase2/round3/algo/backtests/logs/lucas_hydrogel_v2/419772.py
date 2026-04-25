"""
Hydrogel fill‑test strategy.

This Trader class is designed to probe the behaviour of the bots by
consistently placing very small orders at the top of the book and
logging whether and when these orders are executed.  It can be used to
study how quickly and at what prices the bots trade against player
quotes.

The algorithm operates as follows:

* At each iteration it retrieves the best bid and best ask from the
  order depth for HYDROGEL_PACK.
* It places a buy order for a single unit at `best_bid + 1` (if there
  is a best bid) and a sell order for a single unit at `best_ask - 1`
  (if there is a best ask).  These are typically one tick inside
  the bots’ quotes and thus should be attractive to them.
* It logs the timestamp, current position, and the chosen buy and
  sell prices.  It also inspects `state.own_trades` to determine
  whether its previous orders were filled, printing the details of
  executed trades.
* Position limits are respected (±200), and the quantity is kept at
  1 to minimise risk.

Use this strategy to collect information about how the bots react to
your quotes.  The resulting logs can reveal fill latencies and
execution probabilities at different times.
"""

from datamodel import OrderDepth, TradingState, Order, Trade
from typing import Dict, List, Tuple, Optional
import jsonpickle


class Trader:
    POSITION_LIMIT: int = 200

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        product = "HYDROGEL_PACK"
        orders: Dict[str, List[Order]] = {product: []}
        conversions = 0

        position = state.position.get(product, 0)
        depth: Optional[OrderDepth] = state.order_depths.get(product)

        # Check our own trades from last iteration to log fills
        own_trades: List[Trade] = state.own_trades.get(product, [])
        if own_trades:
            for trade in own_trades:
                # trade.price, trade.quantity, trade.buyer/seller etc.
                # If trade.quantity > 0 we bought; negative we sold
                side = "BUY" if trade.quantity > 0 else "SELL"
                print(f"TS={state.timestamp} FILLED {side} {abs(trade.quantity)} @ {trade.price}")

        # If order depth available, place tiny orders
        if depth:
            best_bid = max(depth.buy_orders.keys()) if depth.buy_orders else None
            best_ask = min(depth.sell_orders.keys()) if depth.sell_orders else None

            # Determine buy and sell prices one tick inside the book
            buy_price = best_bid + 1 if best_bid is not None else None
            sell_price = best_ask - 1 if best_ask is not None else None

            # Decide to place buy order
            if buy_price is not None and (position < self.POSITION_LIMIT):
                # Always place size 1 order
                orders[product].append(Order(product, buy_price, 1))
                position += 1
            # Decide to place sell order
            if sell_price is not None and (position > -self.POSITION_LIMIT):
                orders[product].append(Order(product, sell_price, -1))
                position -= 1

            # Log the current action
            print(f"TS={state.timestamp} POS={position} BUY_P={buy_price} SELL_P={sell_price}")

        return orders, conversions, jsonpickle.encode({})