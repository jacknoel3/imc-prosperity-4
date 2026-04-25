"""
HYDROGEL_PACK — skip-compressed inside market maker
IMC Prosperity 4 | Round 3

Hypothesis tested:
    The base inside-1 market maker earns spread, but compressed/L3 states
    have lower edge and worse execution/inventory behavior. Skip those states
    and only quote in the normal wide-spread regime.

Core rules:
    - Trade HYDROGEL_PACK only.
    - If spread < MIN_SPREAD_TO_QUOTE, send no Hydrogel orders.
    - Otherwise quote 1 tick inside the bot book:
        bid = best_bid + 1
        ask = best_ask - 1
    - Use a practical inventory cap/taper around ±50.
    - No EMA, no z-score, no OBI, no taker logic.

Why this is intentionally simple:
    This bot isolates the "skip compressed spread" hypothesis from the EDA.
    Compare it against:
        1. simple inside-MM
        2. z_inventory_mm
        3. later: skip_compressed + z_inventory combined
"""

from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List
import json


# ── PARAMETERS ──────────────────────────────────────────────────────────────

SYMBOL = "HYDROGEL_PACK"

# Exchange limit from Round 3 wiki.
POSITION_LIMIT = 200

# Practical risk limits used by the strategy.
# We still respect POSITION_LIMIT, but we try not to run inventory near it.
PRACTICAL_LIMIT = 50

# Start tapering inventory-increasing side after this absolute exposure.
TAPER_START = 10

# Quote one tick inside the visible bot book.
INSIDE_OFFSET = 1

# Only quote in the normal/wide spread regime.
# Historical compressed/L3 states are typically spread 7-9.
# Normal book is mostly 15-17, especially 16.
MIN_SPREAD_TO_QUOTE = 15
MAX_SPREAD_TO_QUOTE = 17

# Base size per side. Bot trade sizes are usually 2-6, so 20 is enough to
# absorb multiple possible fills but still controlled by taper/caps.
BASE_SIZE = 20

# Set True for simulator diagnostics; set False before final submission.
VERBOSE = True


# ── HELPERS ─────────────────────────────────────────────────────────────────

def load_step(trader_data: str) -> int:
    if not trader_data:
        return 0
    try:
        return int(json.loads(trader_data).get("step", 0))
    except Exception:
        return 0


def save_step(step: int) -> str:
    return json.dumps({"step": step})


def best_bid_ask(depth: OrderDepth):
    if not depth.buy_orders or not depth.sell_orders:
        return None, None
    return max(depth.buy_orders.keys()), min(depth.sell_orders.keys())


def exchange_capacity(position: int, side: str) -> int:
    """Maximum legal quantity before hitting the official exchange limit."""
    if side == "BUY":
        return max(0, POSITION_LIMIT - position)
    return max(0, POSITION_LIMIT + position)


def practical_capacity(position: int, side: str) -> int:
    """
    Maximum preferred quantity before hitting the practical risk cap.
    Allows inventory-reducing trades even when outside practical cap.
    """
    if side == "BUY":
        # Buying increases long exposure, but reduces short exposure.
        if position < 0:
            return exchange_capacity(position, side)
        return max(0, PRACTICAL_LIMIT - position)

    # Selling increases short exposure, but reduces long exposure.
    if position > 0:
        return exchange_capacity(position, side)
    return max(0, PRACTICAL_LIMIT + position)


def taper_size(position: int, side: str, base_size: int = BASE_SIZE) -> int:
    """
    Reduce size only on the side that increases existing exposure.

    Examples:
      - If long, BUY is the heavy side and gets tapered.
      - If short, SELL is the heavy side and gets tapered.
      - Inventory-reducing side stays full-size, subject to capacity.
    """
    if side == "BUY":
        exposure = max(0, position)       # long exposure worsened by buying
    else:
        exposure = max(0, -position)      # short exposure worsened by selling

    if exposure >= PRACTICAL_LIMIT:
        return 0

    if exposure <= TAPER_START:
        return base_size

    # Linear taper from BASE_SIZE at TAPER_START to 1 near PRACTICAL_LIMIT.
    remaining_frac = (PRACTICAL_LIMIT - exposure) / max(1, PRACTICAL_LIMIT - TAPER_START)
    tapered = int(round(base_size * remaining_frac))
    return max(1, min(base_size, tapered))


def make_orders(depth: OrderDepth, position: int) -> List[Order]:
    orders: List[Order] = []

    best_bid, best_ask = best_bid_ask(depth)
    if best_bid is None or best_ask is None:
        return orders

    spread = best_ask - best_bid

    # Skip compressed / abnormal regimes.
    if spread < MIN_SPREAD_TO_QUOTE or spread > MAX_SPREAD_TO_QUOTE:
        return orders

    bid_price = best_bid + INSIDE_OFFSET
    ask_price = best_ask - INSIDE_OFFSET

    # Never cross our own quotes.
    if bid_price >= ask_price:
        return orders

    # BUY quote.
    buy_cap = min(exchange_capacity(position, "BUY"), practical_capacity(position, "BUY"))
    buy_size = min(taper_size(position, "BUY"), buy_cap)
    if buy_size > 0:
        orders.append(Order(SYMBOL, int(bid_price), int(buy_size)))

    # SELL quote.
    sell_cap = min(exchange_capacity(position, "SELL"), practical_capacity(position, "SELL"))
    sell_size = min(taper_size(position, "SELL"), sell_cap)
    if sell_size > 0:
        orders.append(Order(SYMBOL, int(ask_price), -int(sell_size)))

    return orders


# ── TRADER ──────────────────────────────────────────────────────────────────

class Trader:
    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        step = load_step(state.traderData)

        if SYMBOL not in state.order_depths:
            return result, 0, save_step(step + 1)

        depth = state.order_depths[SYMBOL]
        position = state.position.get(SYMBOL, 0)

        best_bid, best_ask = best_bid_ask(depth)
        if best_bid is None or best_ask is None:
            result[SYMBOL] = []
            return result, 0, save_step(step + 1)

        spread = best_ask - best_bid
        mid = (best_bid + best_ask) / 2.0

        orders = make_orders(depth, position)
        result[SYMBOL] = orders

        if VERBOSE:
            bid_order = next((o for o in orders if o.quantity > 0), None)
            ask_order = next((o for o in orders if o.quantity < 0), None)

            if not orders:
                print(
                    f"[SKIP] ts={state.timestamp} step={step} "
                    f"spread={spread} mid={mid:.1f} pos={position}"
                )
            else:
                print(
                    f"[MM] ts={state.timestamp} step={step} "
                    f"spread={spread} mid={mid:.1f} pos={position} "
                    f"bid={bid_order.price if bid_order else None} "
                    f"bid_qty={bid_order.quantity if bid_order else 0} "
                    f"ask={ask_order.price if ask_order else None} "
                    f"ask_qty={ask_order.quantity if ask_order else 0}"
                )

        return result, 0, save_step(step + 1)
