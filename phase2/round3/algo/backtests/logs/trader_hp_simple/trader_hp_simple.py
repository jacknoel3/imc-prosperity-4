"""
HYDROGEL_PACK Trader v2 — IMC Prosperity 4, Round 3
=====================================================

Strategy: Pure passive market making, 1 tick inside the bot's spread.

Key findings from EDA + backtest analysis:
  - Bot market maker always quotes exactly bid1 = mid-8, ask1 = mid+8
  - Noise traders ALWAYS hit bid1 or ask1 exactly — never inside, never level 2
  - ~337 trades/day, qty 2-6 per trade, ~50/50 buy/sell
  - Posting at bid1+1 and ask1-1 makes us best bid/ask by price priority
  - Expected ~1,359 fillable units/day at ~7 ticks edge → ~9,500/day → ~95k/10days
  - OBI signal is a pure mechanical artifact — dropped entirely
  - EMA caused catastrophic quote lag in v1 — dropped entirely

Execution rules:
  - our_bid = best_bid + 1  (best_bid = bot's bid1)
  - our_ask = best_ask - 1  (best_ask = bot's ask1)
  - Size: fill remaining capacity up to MAX_ORDER_SIZE
  - Inventory skew: reduce size and eventually skip heavy side
  - Hard stop: no new quotes on heavy side above HARD_LIMIT
"""

from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict
import json

# ── PARAMETERS ──────────────────────────────────────────────────────────────

SYMBOL        = "HYDROGEL_PACK"
POSITION_LIMIT = 200

# How far inside the bot's spread we quote (1 = right inside, 2 = 2 ticks in)
INSIDE_OFFSET = 1

# Max order size per side per step
MAX_ORDER_SIZE = 20

# Inventory management
# Above these thresholds (absolute position), start reducing size on heavy side
SKEW_THRESHOLD_1 = 50    # reduce heavy-side size by 25%
SKEW_THRESHOLD_2 = 100   # reduce heavy-side size by 50%
SKEW_THRESHOLD_3 = 150   # reduce heavy-side size by 75%
HARD_LIMIT       = 185   # stop quoting heavy side entirely

# Logging — set False before submission
VERBOSE = True

# ── HELPERS ─────────────────────────────────────────────────────────────────

def skewed_size(base_size: int, pos: int, side: str) -> int:
    """
    Reduce order size on the side that would increase our inventory exposure.
    side = "BUY" (we're quoting a bid) or "SELL" (we're quoting an ask).
    """
    # How much does this side increase our exposure?
    # BUY increases long exposure when pos > 0, reduces when pos < 0
    # SELL increases short exposure when pos < 0, reduces when pos > 0
    if side == "BUY":
        exposure = pos   # positive = already long, this makes it worse
    else:
        exposure = -pos  # negative pos = already short, selling makes it worse

    if exposure >= HARD_LIMIT:
        return 0
    elif exposure >= SKEW_THRESHOLD_3:
        return max(1, base_size // 4)
    elif exposure >= SKEW_THRESHOLD_2:
        return max(1, base_size // 2)
    elif exposure >= SKEW_THRESHOLD_1:
        return max(1, (base_size * 3) // 4)
    else:
        return base_size


def capacity(pos: int, side: str) -> int:
    """Remaining position capacity on this side."""
    if side == "BUY":
        return max(0, POSITION_LIMIT - pos)
    else:
        return max(0, POSITION_LIMIT + pos)


# ── TRADER ───────────────────────────────────────────────────────────────────

class Trader:

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}

        # ── restore lightweight state ──────────────────────────────────────
        step = 0
        if state.traderData:
            try:
                step = json.loads(state.traderData).get("step", 0)
            except Exception:
                step = 0

        if SYMBOL not in state.order_depths:
            return result, 0, json.dumps({"step": step + 1})

        od: OrderDepth = state.order_depths[SYMBOL]
        pos = state.position.get(SYMBOL, 0)
        orders: List[Order] = []

        # ── parse book ────────────────────────────────────────────────────
        if not od.buy_orders or not od.sell_orders:
            return result, 0, json.dumps({"step": step + 1})

        best_bid = max(od.buy_orders.keys())   # bot's bid1
        best_ask = min(od.sell_orders.keys())  # bot's ask1
        spread   = best_ask - best_bid
        mid      = (best_bid + best_ask) / 2

        # sanity check — only trade when book looks normal
        if spread < 7 or spread > 17:
            if VERBOSE:
                print(f"[SKIP] ts={state.timestamp} abnormal spread={spread}")
            result[SYMBOL] = orders
            return result, 0, json.dumps({"step": step + 1})

        # ── our quote prices ──────────────────────────────────────────────
        our_bid = best_bid + INSIDE_OFFSET   # 1 tick better → best bid
        our_ask = best_ask - INSIDE_OFFSET   # 1 tick better → best ask

        # guard: never cross the spread
        if our_bid >= our_ask:
            if VERBOSE:
                print(f"[SKIP] ts={state.timestamp} quotes crossed: "
                      f"our_bid={our_bid} our_ask={our_ask}")
            result[SYMBOL] = orders
            return result, 0, json.dumps({"step": step + 1})

        # ── bid order (we buy) ────────────────────────────────────────────
        bid_cap  = capacity(pos, "BUY")
        bid_size = skewed_size(MAX_ORDER_SIZE, pos, "BUY")
        bid_qty  = min(bid_size, bid_cap)

        if bid_qty > 0:
            orders.append(Order(SYMBOL, our_bid, bid_qty))
            if VERBOSE:
                print(f"[BID] ts={state.timestamp} px={our_bid} qty={bid_qty} "
                      f"pos={pos} cap={bid_cap}")

        # ── ask order (we sell) ───────────────────────────────────────────
        ask_cap  = capacity(pos, "SELL")
        ask_size = skewed_size(MAX_ORDER_SIZE, pos, "SELL")
        ask_qty  = min(ask_size, ask_cap)

        if ask_qty > 0:
            orders.append(Order(SYMBOL, our_ask, -ask_qty))
            if VERBOSE:
                print(f"[ASK] ts={state.timestamp} px={our_ask} qty={ask_qty} "
                      f"pos={pos} cap={ask_cap}")

        # ── summary log every 1000 steps ─────────────────────────────────
        if VERBOSE and step % 1000 == 0:
            print(f"[SUMMARY] ts={state.timestamp} mid={mid} spread={spread} "
                  f"pos={pos} our_bid={our_bid} our_ask={our_ask}")

        result[SYMBOL] = orders
        return result, 0, json.dumps({"step": step + 1})
