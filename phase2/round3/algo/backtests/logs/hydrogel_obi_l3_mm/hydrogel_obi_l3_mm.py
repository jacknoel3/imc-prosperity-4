"""
HYDROGEL_PACK — OBI L3/XOR Market Maker Test Bot
IMC Prosperity 4 | Round 3

Purpose
-------
This is a clean hypothesis-test bot.

Baseline:
    In the normal HYDROGEL_PACK regime, quote passively one tick inside
    the visible bot market:
        bid = best_bid + 1
        ask = best_ask - 1

Hypothesis being tested:
    The L3/XOR / compressed-spread regime contains a short-horizon
    directional OBI signal.

    In L3/XOR:
        OBI > 0  -> expected future mid up   -> quote bid only
        OBI < 0  -> expected future mid down -> quote ask only

Important:
    This bot deliberately does NOT use z-score mean reversion.
    It is meant to isolate the microstructure/OBI hypothesis.
"""

from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List
import json


# ── PARAMETERS ──────────────────────────────────────────────────────────────

SYMBOL = "HYDROGEL_PACK"
POSITION_LIMIT = 200

# One tick inside the bot quote.
INSIDE_OFFSET = 1

# Keep size modest and comparable to the simple inside-MM baseline.
MAX_ORDER_SIZE = 20

# In L3/XOR, only trust OBI if the signal has a meaningful sign.
# Historical L3 OBI is usually far from zero, but this avoids noise around 0.
OBI_EPS = 1e-9

# Safety: avoid trading broken/crossed/weird books.
MIN_SPREAD = 7
MAX_SPREAD = 17

# Logging. Set False before final submission if desired.
VERBOSE = True


# ── HELPERS ─────────────────────────────────────────────────────────────────

def best_bid_ask(depth: OrderDepth):
    """Return best bid and best ask, or (None, None) if book incomplete."""
    if not depth.buy_orders or not depth.sell_orders:
        return None, None
    return max(depth.buy_orders.keys()), min(depth.sell_orders.keys())


def level_count(depth: OrderDepth, side: str) -> int:
    if side == "bid":
        return len(depth.buy_orders)
    return len(depth.sell_orders)


def is_l3_xor(depth: OrderDepth) -> bool:
    """
    L3/XOR regime:
      exactly one side has a third visible level.

    This matches the EDA idea that the special second bot appears
    on only one side of the book.
    """
    bid_l3 = level_count(depth, "bid") >= 3
    ask_l3 = level_count(depth, "ask") >= 3
    return bid_l3 != ask_l3


def l3_side(depth: OrderDepth) -> str:
    """
    Returns 'bid', 'ask', or 'none' based on which side has L3.
    """
    bid_l3 = level_count(depth, "bid") >= 3
    ask_l3 = level_count(depth, "ask") >= 3
    if bid_l3 and not ask_l3:
        return "bid"
    if ask_l3 and not bid_l3:
        return "ask"
    return "none"


def obi_l1(depth: OrderDepth) -> float:
    """
    L1 order book imbalance:
        OBI = (bid_vol_1 - ask_vol_1) / (bid_vol_1 + ask_vol_1)

    buy_orders quantities are positive.
    sell_orders quantities are negative in Prosperity, so use abs().
    """
    best_bid, best_ask = best_bid_ask(depth)
    if best_bid is None or best_ask is None:
        return 0.0

    bid_vol = depth.buy_orders.get(best_bid, 0)
    ask_vol = abs(depth.sell_orders.get(best_ask, 0))
    denom = bid_vol + ask_vol
    if denom <= 0:
        return 0.0

    return (bid_vol - ask_vol) / denom


def capacity(position: int, side: str) -> int:
    """
    Remaining position capacity.
    side='BUY' increases position.
    side='SELL' decreases position.
    """
    if side == "BUY":
        return max(0, POSITION_LIMIT - position)
    return max(0, POSITION_LIMIT + position)


def add_bid(orders: List[Order], price: int, position: int):
    qty = min(MAX_ORDER_SIZE, capacity(position, "BUY"))
    if qty > 0:
        orders.append(Order(SYMBOL, int(price), int(qty)))


def add_ask(orders: List[Order], price: int, position: int):
    qty = min(MAX_ORDER_SIZE, capacity(position, "SELL"))
    if qty > 0:
        orders.append(Order(SYMBOL, int(price), -int(qty)))


# ── TRADER ───────────────────────────────────────────────────────────────────

class Trader:

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}

        # Minimal state only for readable logs.
        try:
            old = json.loads(state.traderData) if state.traderData else {}
            step = int(old.get("step", 0))
        except Exception:
            step = 0

        if SYMBOL not in state.order_depths:
            return result, 0, json.dumps({"step": step + 1})

        depth = state.order_depths[SYMBOL]
        position = state.position.get(SYMBOL, 0)
        orders: List[Order] = []

        best_bid, best_ask = best_bid_ask(depth)
        if best_bid is None or best_ask is None:
            result[SYMBOL] = orders
            return result, 0, json.dumps({"step": step + 1})

        spread = best_ask - best_bid

        # Safety guard.
        if spread < MIN_SPREAD or spread > MAX_SPREAD:
            if VERBOSE:
                print(f"[SKIP] ts={state.timestamp} abnormal_spread={spread} pos={position}")
            result[SYMBOL] = orders
            return result, 0, json.dumps({"step": step + 1})

        our_bid = best_bid + INSIDE_OFFSET
        our_ask = best_ask - INSIDE_OFFSET

        # Never cross our own quotes.
        if our_bid >= our_ask:
            if VERBOSE:
                print(f"[SKIP] ts={state.timestamp} crossed_quotes bid={our_bid} ask={our_ask} pos={position}")
            result[SYMBOL] = orders
            return result, 0, json.dumps({"step": step + 1})

        l3 = is_l3_xor(depth)
        side = l3_side(depth)
        obi = obi_l1(depth)

        if l3:
            # Directional special-regime logic.
            # OBI > 0 => expected mid up => prefer long/bid only.
            # OBI < 0 => expected mid down => prefer short/ask only.
            if obi > OBI_EPS:
                add_bid(orders, our_bid, position)
                regime = "l3_obi_pos_bid_only"
            elif obi < -OBI_EPS:
                add_ask(orders, our_ask, position)
                regime = "l3_obi_neg_ask_only"
            else:
                # Rare fallback: if OBI is numerically zero, use L3 side.
                if side == "bid":
                    add_bid(orders, our_bid, position)
                    regime = "l3_bid_side_fallback"
                elif side == "ask":
                    add_ask(orders, our_ask, position)
                    regime = "l3_ask_side_fallback"
                else:
                    regime = "l3_no_trade"
        else:
            # Normal regime: pure simple inside market making.
            add_bid(orders, our_bid, position)
            add_ask(orders, our_ask, position)
            regime = "normal_simple_mm"

        if VERBOSE:
            mid = (best_bid + best_ask) / 2
            print(
                f"[HGP_OBI] step={step} ts={state.timestamp} "
                f"regime={regime} l3_side={side} obi={obi:.4f} "
                f"bb={best_bid} ba={best_ask} spread={spread} mid={mid:.1f} "
                f"bid={our_bid if any(o.quantity > 0 for o in orders) else 'None'} "
                f"ask={our_ask if any(o.quantity < 0 for o in orders) else 'None'} "
                f"pos={position}"
            )

        result[SYMBOL] = orders
        return result, 0, json.dumps({"step": step + 1})
