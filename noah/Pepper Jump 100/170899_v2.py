from datamodel import Order, TradingState
from typing import Dict, List
import json

# ── Product constants ────────────────────────────────────────────────────────
PRODUCT     = "INTARIAN_PEPPER_ROOT"
POS_LIMIT   = 80
TARGET_LONG = 80

# ── Fair value: Holt's double exponential smoothing ─────────────────────────
# Pepper trends +~1000/day (+0.10/tick). Plain EMA lags a linear ramp by ~1–2
# ticks. Holt's tracks both level and slope, so the one-step-ahead forecast
# (level + trend) stays on the ramp rather than chasing from behind.
HOLT_ALPHA  = 0.05   # level smoothing  – slow, noise-robust
HOLT_BETA   = 0.10   # trend smoothing  – medium, captures intraday ramp

# Historical mean spread 13.0 ticks, half used when one side is missing.
HALF_SPREAD = 6.5

# ── Outage exploitation ──────────────────────────────────────────────────────
# During no-ask states aggressive takers have no visible liquidity. We post a
# small sell above our FV to collect premium if a response-bot takes it.
# Key differences from 170899.py:
#   - sell size capped at OUTAGE_SELL_MAX (5) instead of full 80/160
#   - only fires when position == TARGET_LONG (never sells while accumulating)
#   - never takes position below 0 (no short into an uptrend)
OUTAGE_SELL_MAX = 8   # units to offer per outage tick
OUTAGE_EDGE     = 75  # offer price = int(fv) + OUTAGE_EDGE

# ── traderData schema ────────────────────────────────────────────────────────
# {"level": float, "trend": float}
# level – Holt's running level estimate
# trend – Holt's running slope estimate (~+0.10/tick once converged)
# Total: ~2 floats — no unbounded growth risk.


class Trader:
    """
    INTARIAN_PEPPER_ROOT-only strategy.

    Design pillars
    --------------
    1. Stay structurally long at +80. The +1000/day drift is the dominant PnL
       source. Being at +80 vs +0 is worth ~80,000 XIRECs over a full 10k-tick
       day; outage alpha is an order of magnitude smaller.

    2. Accumulate aggressively. Cross all visible asks each tick until +80.
       Never post a resting buy below ask when aggressively building.

    3. Outage sell: up to 8 units at FV+75 during no-ask states.
       EDGE=75 is the MC sweet spot between fv+50 and fv+100 (300-session sweep).
       If fills: we earn ~68.5 ticks of edge after refill cost (~6.5 half-spread).
       If no fill: no position change, no harm — we hold +80 through the drift.
       Guard: only fires if already at TARGET_LONG (no selling while building).
       Guard: sell price must be above best_bid (sanity check on stale FV).

    4. Immediate refill. After an outage sell (or any shortfall), cross all
       visible ask layers on the very next normal tick to restore +80 quickly.
       In an uptrend, every tick below +80 is a missed +0.1 × 80 = 8 XIRECs.

    5. Lightweight: two floats in state, O(ask_levels) per tick.
    """

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}

        try:
            mem = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            mem = {}

        position = state.position.get(PRODUCT, 0)
        orders: List[Order] = []

        if PRODUCT not in state.order_depths:
            result[PRODUCT] = orders
            return result, 0, json.dumps(mem)

        od       = state.order_depths[PRODUCT]
        has_bids = bool(od.buy_orders)
        has_asks = bool(od.sell_orders)

        best_bid = max(od.buy_orders.keys())  if has_bids else None
        best_ask = min(od.sell_orders.keys()) if has_asks else None

        # ── Step 1: Update Holt's FV ─────────────────────────────────────────
        # Compute an observed mid from whatever sides are present.
        if has_bids and has_asks:
            obs_mid = (best_bid + best_ask) / 2.0
        elif has_bids:
            obs_mid = best_bid + HALF_SPREAD   # no-ask: FV is slightly above bid
        elif has_asks:
            obs_mid = best_ask - HALF_SPREAD   # no-bid: FV is slightly below ask
        else:
            obs_mid = None

        level = mem.get("level")
        trend = mem.get("trend", 0.0)

        if obs_mid is not None:
            if level is None:
                level = obs_mid   # cold start: seed from first observation
                trend = 0.0
            else:
                new_level = HOLT_ALPHA * obs_mid + (1.0 - HOLT_ALPHA) * (level + trend)
                trend     = HOLT_BETA  * (new_level - level) + (1.0 - HOLT_BETA) * trend
                level     = new_level
            mem["level"] = level
            mem["trend"] = trend
        elif level is None:
            # No FV estimate yet (both sides missing at start) — skip tick
            result[PRODUCT] = orders
            return result, 0, json.dumps(mem)
        # else: both sides missing but we have a prior estimate — reuse it

        fv = level + trend   # one-step-ahead Holt forecast

        # ── Step 2: Outage sell ──────────────────────────────────────────────
        # Condition: no visible asks, at least one visible bid, at target long.
        # We never sell while below target (don't interrupt accumulation).
        # We never go short (min position after sell is 0).
        if (not has_asks) and has_bids and position >= TARGET_LONG:
            sell_qty = min(OUTAGE_SELL_MAX, position)   # clamp: stay >= 0
            if sell_qty > 0:
                sell_px = int(fv + OUTAGE_EDGE)
                if sell_px > best_bid:   # sanity: must be above visible market
                    orders.append(Order(PRODUCT, sell_px, -sell_qty))

        # ── Step 3: Accumulate / refill toward TARGET_LONG ──────────────────
        # Cross every visible ask layer until position reaches +80. This fires
        # on normal ticks, post-outage recovery ticks, and opening ticks alike.
        if has_asks and position < TARGET_LONG:
            remaining = TARGET_LONG - position
            for ask_px in sorted(od.sell_orders.keys()):
                ask_vol = abs(od.sell_orders[ask_px])
                to_buy  = min(remaining, ask_vol)
                if to_buy > 0:
                    orders.append(Order(PRODUCT, ask_px, to_buy))
                    remaining -= to_buy
                if remaining <= 0:
                    break

        result[PRODUCT] = orders
        return result, 0, json.dumps(mem)
