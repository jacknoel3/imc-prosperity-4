"""
trader_pepper_resting_ladder.py
================================
EXPERIMENT: Do seller bots cross into resting bids that are inside the spread?

HYPOTHESIS
----------
Normal best_bid is ~13 ticks below best_ask. If we post a BUY order at
ask-2, ask-3, ask-4, ask-5, or ask-6, our order becomes the new best bid
(still below ask, no immediate fill). A seller bot that wants to sell might
cross into our resting order rather than waiting for the normal best_bid.

If fills happen -> seller bots have a reservation price ABOVE normal best_bid.
The tightest offset that fills tells us how much edge we can extract.

STRUCTURE
---------
We post ONE small order (qty=1) at each of 5 offsets simultaneously:
  ask-2, ask-3, ask-4, ask-5, ask-6

Total BUY qty = 5 units, well within position limits.
We track fills per level in traderData to measure which levels get hit.

WHAT TO LOOK FOR IN THE LOG
----------------------------
[FILL] lines show which price level filled -> that offset gets seller traffic.
If ask-2 fills: sellers accept 2 ticks worse than ask = huge edge opportunity.
If only ask-6 fills: sellers need ~6 ticks improvement to cross.
If no fills: seller bots only hit the normal best_bid, no inside-spread edge.

POSITION LIMIT NOTE
-------------------
We keep qty=1 per level (5 total) so this never interferes with a
combined hold strategy. Pure probe, minimal position impact.
"""

from datamodel import Order, TradingState
from typing import Dict, List
import json

PRODUCT   = "INTARIAN_PEPPER_ROOT"
POS_LIMIT = 80

# Offsets below ask to test: ask-2 through ask-6
# ask-1 skipped: almost identical to ask, negligible saving
OFFSETS = [2, 3, 4, 5, 6]
QTY_PER_LEVEL = 1   # small probe qty per level


class Trader:

    def bid(self):
        return 15

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}

        try:
            mem = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            mem = {}

        # Fill counters per offset
        for off in OFFSETS:
            mem.setdefault(f"fills_ask_minus_{off}", 0)
            mem.setdefault(f"qty_ask_minus_{off}", 0)
        mem.setdefault("events", 0)

        ts       = state.timestamp
        position = state.position.get(PRODUCT, 0)
        orders: List[Order] = []

        if PRODUCT not in state.order_depths:
            result[PRODUCT] = orders
            return result, 0, json.dumps(mem)

        od       = state.order_depths[PRODUCT]
        has_bids = len(od.buy_orders)  > 0
        has_asks = len(od.sell_orders) > 0

        # Log fills from previous tick
        for trade in state.own_trades.get(PRODUCT, []):
            if trade.quantity > 0:  # we bought
                # Identify which offset level this fill corresponds to
                # We need the ask from the PREVIOUS tick - use mem
                prev_ask = mem.get("prev_ask")
                if prev_ask:
                    offset = prev_ask - trade.price
                    key_fills = f"fills_ask_minus_{int(offset)}"
                    key_qty   = f"qty_ask_minus_{int(offset)}"
                    if key_fills in mem:
                        mem[key_fills] += 1
                        mem[key_qty]   += trade.quantity
                print(
                    f"[FILL] ts={ts} | BUY {trade.quantity}x @ {trade.price} "
                    f"| prev_ask={prev_ask} | offset={prev_ask - trade.price if prev_ask else '?'} "
                    f"| pos={position}"
                )

        # Only act on clean ticks (both sides present)
        if not has_bids or not has_asks:
            result[PRODUCT] = orders
            return result, 0, json.dumps(mem)

        best_ask = min(od.sell_orders.keys())
        best_bid = max(od.buy_orders.keys())
        mem["prev_ask"] = best_ask

        buy_cap = POS_LIMIT - position

        # Post ladder of resting bids inside the spread
        if buy_cap >= len(OFFSETS):
            mem["events"] += 1
            for off in OFFSETS:
                bid_px = best_ask - off
                # Only post if strictly above best_bid (true queue jump)
                # and strictly below best_ask (no immediate fill)
                if bid_px > best_bid and bid_px < best_ask:
                    orders.append(Order(PRODUCT, bid_px, QTY_PER_LEVEL))

            if ts % 10_000 == 0:
                print(
                    f"[TICK] ts={ts} | ask={best_ask} bid={best_bid} "
                    f"spread={best_ask-best_bid} pos={position}"
                )
                for off in OFFSETS:
                    fills = mem[f'fills_ask_minus_{off}']
                    qty   = mem[f'qty_ask_minus_{off}']
                    print(f"  ask-{off} @ {best_ask-off}: fills={fills} qty={qty}")

        # End of run summary
        if ts == 99_900:
            print(f"\n[SUMMARY] ts={ts} | pos={position} | events={mem['events']}")
            for off in OFFSETS:
                fills = mem[f'fills_ask_minus_{off}']
                qty   = mem[f'qty_ask_minus_{off}']
                print(f"  ask-{off}: fills={fills}  qty_filled={qty}")

        result[PRODUCT] = orders
        return result, 0, json.dumps(mem)