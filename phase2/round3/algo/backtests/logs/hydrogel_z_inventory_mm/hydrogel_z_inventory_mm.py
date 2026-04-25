"""
HYDROGEL_PACK — Z-Score Inventory Market Maker
IMC Prosperity 4 | Round 3

Hypothesis tested by this bot:
    Passive inside market making is the base edge, but slow mean-reversion
    information should improve inventory selection.

Core execution:
    - Always quote relative to the live bot book, not around EMA/fair value.
    - Normal quote prices are:
          bid = best_bid + 1
          ask = best_ask - 1
    - No liquidity-taking / crossing.
    - Z-score is used only to change quote sizes / preferred inventory side.

Mean-reversion idea:
    - Compute z = (current_mid - rolling_mean_mid) / rolling_std_mid
      using a slow rolling window of prior mids.
    - If z is high, price is rich vs slow anchor -> prefer short inventory.
    - If z is low, price is cheap vs slow anchor -> prefer long inventory.

This is intentionally still simple. It is meant to be compared against the
simple inside-MM baseline, not treated as a final optimized strategy.
"""

from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Optional, Tuple
import json
import math


# ── Product / exchange constraints ──────────────────────────────────────────
SYMBOL = "HYDROGEL_PACK"
POSITION_LIMIT = 200

# ── Base market-making parameters ───────────────────────────────────────────
INSIDE_OFFSET = 1          # quote 1 tick inside bot best bid/ask
MAX_ORDER_SIZE = 20        # per-side quote size before z/inventory scaling
MIN_SPREAD = 7             # keep same broad sanity band as baseline
MAX_SPREAD = 17

# ── Z-score anchor parameters ───────────────────────────────────────────────
ROLLING_WINDOW = 500       # slow anchor, aligned with EDA windows 300-500
MIN_HISTORY = 100          # warmup before z is trusted
SOFT_Z = 1.0
STRONG_Z = 2.0

# ── Inventory safety parameters ─────────────────────────────────────────────
# These are practical risk caps, not exchange limits. The exchange allows 200,
# but letting inventory drift near 200 makes mark-to-market swings dominate.
COMFORT_INV_CAP = 60       # slow down further favored inventory beyond this
HARD_INV_CAP = 100         # force inventory-reducing side beyond this

# ── Logging ─────────────────────────────────────────────────────────────────
# Keep True for simulator testing. Set to False before final submission if logs
# become too large/noisy.
VERBOSE = True
LOG_EVERY = 100


# ── State helpers ───────────────────────────────────────────────────────────

def load_state(trader_data: str) -> dict:
    """Restore lightweight state from traderData."""
    default = {"step": 0, "mid_history": []}
    if not trader_data:
        return default
    try:
        s = json.loads(trader_data)
        hist = s.get("mid_history", [])
        if not isinstance(hist, list):
            hist = []
        # keep only floats and cap length defensively
        clean_hist = []
        for x in hist[-ROLLING_WINDOW:]:
            try:
                clean_hist.append(float(x))
            except Exception:
                pass
        return {
            "step": int(s.get("step", 0)),
            "mid_history": clean_hist,
        }
    except Exception:
        return default


def save_state(step: int, mid_history: List[float]) -> str:
    """Persist state through traderData."""
    return json.dumps({
        "step": step,
        "mid_history": mid_history[-ROLLING_WINDOW:],
    })


# ── Book helpers ────────────────────────────────────────────────────────────

def best_bid_ask(od: OrderDepth) -> Tuple[Optional[int], Optional[int]]:
    best_bid = max(od.buy_orders.keys()) if od.buy_orders else None
    best_ask = min(od.sell_orders.keys()) if od.sell_orders else None
    return best_bid, best_ask


def mid_price(od: OrderDepth) -> Optional[float]:
    b, a = best_bid_ask(od)
    if b is None or a is None:
        return None
    return (b + a) / 2.0


# ── Z-score helpers ─────────────────────────────────────────────────────────

def rolling_mean_std(values: List[float]) -> Tuple[Optional[float], Optional[float]]:
    """Sample mean/std of prior mids. Returns (None, None) during warmup."""
    n = len(values)
    if n < MIN_HISTORY:
        return None, None
    mean = sum(values) / n
    var = sum((x - mean) ** 2 for x in values) / max(1, n - 1)
    std = math.sqrt(var)
    if std < 1e-9:
        return mean, None
    return mean, std


def compute_z(current_mid: float, history: List[float]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Compute z using prior history only. This avoids letting the current mid
    dilute the signal at the exact tick where we are deciding orders.
    """
    anchor, std = rolling_mean_std(history)
    if anchor is None or std is None:
        return None, anchor, std
    return (current_mid - anchor) / std, anchor, std


# ── Sizing logic ────────────────────────────────────────────────────────────

def round_size(multiplier: float) -> int:
    """Turn a size multiplier into an integer order size."""
    if multiplier <= 0:
        return 0
    return max(1, int(round(MAX_ORDER_SIZE * multiplier)))


def z_size_multipliers(z: Optional[float], pos: int) -> Tuple[float, float, str]:
    """
    Return (bid_multiplier, ask_multiplier, regime_label).

    Bid fills increase long inventory.
    Ask fills increase short inventory.

    If z is high: price rich -> prefer short -> ask active, bid reduced.
    If z is low : price cheap -> prefer long  -> bid active, ask reduced.
    """
    bid_mult = 1.0
    ask_mult = 1.0
    regime = "warmup/neutral"

    if z is not None:
        if z >= STRONG_Z:
            bid_mult = 0.0
            ask_mult = 1.0
            regime = "strong_high_prefer_short"
        elif z >= SOFT_Z:
            bid_mult = 0.5
            ask_mult = 1.0
            regime = "mild_high_prefer_short"
        elif z <= -STRONG_Z:
            bid_mult = 1.0
            ask_mult = 0.0
            regime = "strong_low_prefer_long"
        elif z <= -SOFT_Z:
            bid_mult = 1.0
            ask_mult = 0.5
            regime = "mild_low_prefer_long"
        else:
            regime = "neutral"

    # Do not let the preferred inventory side grow without bound.
    # Example: if z is high, being short is comfortable, but after -60 units
    # we slow additional sells and allow a small bid to capture spread / reduce risk.
    if z is not None and z >= SOFT_Z and pos <= -COMFORT_INV_CAP:
        ask_mult = min(ask_mult, 0.25)
        bid_mult = max(bid_mult, 0.25)
        regime += "|short_comfort_cap"
    elif z is not None and z <= -SOFT_Z and pos >= COMFORT_INV_CAP:
        bid_mult = min(bid_mult, 0.25)
        ask_mult = max(ask_mult, 0.25)
        regime += "|long_comfort_cap"

    # Hard practical inventory cap. Beyond this, quote only the side that
    # reduces absolute inventory.
    if pos >= HARD_INV_CAP:
        bid_mult = 0.0
        ask_mult = 1.0
        regime += "|hard_long_unwind"
    elif pos <= -HARD_INV_CAP:
        bid_mult = 1.0
        ask_mult = 0.0
        regime += "|hard_short_unwind"

    return bid_mult, ask_mult, regime


# ── Trader ──────────────────────────────────────────────────────────────────

class Trader:

    def bid(self):
        # Ignored in Round 3, harmless if present.
        return 1

    def run(self, state: TradingState):
        s = load_state(state.traderData)
        step = s["step"]
        history: List[float] = s["mid_history"]

        result: Dict[str, List[Order]] = {}

        if SYMBOL not in state.order_depths:
            return result, 0, save_state(step + 1, history)

        od: OrderDepth = state.order_depths[SYMBOL]
        pos = state.position.get(SYMBOL, 0)
        orders: List[Order] = []

        best_bid, best_ask = best_bid_ask(od)
        mid = mid_price(od)

        if best_bid is None or best_ask is None or mid is None:
            return result, 0, save_state(step + 1, history)

        spread = best_ask - best_bid

        # Keep this broad filter identical to the simple baseline so the test is
        # about z-inventory logic, not a new spread-regime hypothesis.
        if spread < MIN_SPREAD or spread > MAX_SPREAD:
            if VERBOSE:
                print(f"[SKIP] ts={state.timestamp} abnormal_spread={spread} pos={pos}")
            result[SYMBOL] = orders
            # Still update history because the observed mid is valid information.
            history.append(float(mid))
            return result, 0, save_state(step + 1, history)

        # Compute z from prior mids.
        z, anchor, std = compute_z(float(mid), history)

        # Base prices: inside the bot's best quotes by one tick.
        our_bid = best_bid + INSIDE_OFFSET
        our_ask = best_ask - INSIDE_OFFSET

        # Guard against accidental crossing in compressed spread states.
        if our_bid >= our_ask:
            if VERBOSE:
                print(f"[SKIP] ts={state.timestamp} crossed_quotes bid={our_bid} ask={our_ask} spread={spread}")
            result[SYMBOL] = orders
            history.append(float(mid))
            return result, 0, save_state(step + 1, history)

        bid_mult, ask_mult, regime = z_size_multipliers(z, pos)

        # Exchange capacity checks.
        buy_capacity = max(0, POSITION_LIMIT - pos)
        sell_capacity = max(0, POSITION_LIMIT + pos)

        bid_qty = min(round_size(bid_mult), buy_capacity)
        ask_qty = min(round_size(ask_mult), sell_capacity)

        if bid_qty > 0:
            orders.append(Order(SYMBOL, int(our_bid), int(bid_qty)))

        if ask_qty > 0:
            orders.append(Order(SYMBOL, int(our_ask), -int(ask_qty)))

        result[SYMBOL] = orders

        if VERBOSE and (step % LOG_EVERY == 0 or (z is not None and abs(z) >= SOFT_Z)):
            z_s = "NA" if z is None else f"{z:.2f}"
            anchor_s = "NA" if anchor is None else f"{anchor:.2f}"
            std_s = "NA" if std is None else f"{std:.2f}"
            print(
                f"[ZMM] step={step} ts={state.timestamp} mid={mid:.1f} spread={spread} "
                f"anchor={anchor_s} std={std_s} z={z_s} pos={pos} "
                f"bid={our_bid}x{bid_qty} ask={our_ask}x{ask_qty} regime={regime}"
            )

        # Update rolling history after decisions are made.
        history.append(float(mid))

        return result, 0, save_state(step + 1, history)
