from datamodel import Order, TradingState
from typing import Dict, List
import json

# ── Product ──────────────────────────────────────────────────────────────────
PRODUCT     = "INTARIAN_PEPPER_ROOT"
POS_LIMIT   = 80
TARGET_LONG = 80

# ── Fair value: Holt's double exponential smoothing ─────────────────────────
# Pepper trends +~1000/day (~+0.10/tick). Plain EMA lags a linear ramp;
# Holt's one-step-ahead forecast (level + trend) rides the ramp without lag.
HOLT_ALPHA  = 0.05   # level smoothing — slow, noise-robust
HOLT_BETA   = 0.10   # trend smoothing — converges to intraday slope

# Half-spread fallback for FV when one side of the book is missing.
HALF_SPREAD = 6.5    # historical mean spread 13.0 ticks / 2

# ── Outage sell parameters ───────────────────────────────────────────────────
# During no-ask outages, aggressive buyers have no visible liquidity.
# We post a small sell above FV to collect premium from any response bot.
# EDGE=75 is the MC-validated sweet spot between fv+50 and fv+100
# (300-session sweep: 50→82k, 75→84.5k, 100→83.5k mean PnL).
# Worst case if no fill: we stay at +80 and collect the trend drift as usual.
OUTAGE_EDGE     = 75   # sell price = int(fv) + OUTAGE_EDGE
OUTAGE_SELL_MAX = 8    # max units per no-ask tick; partial fills ~5 per MC model

# ── traderData: {"level": float, "trend": float} ─────────────────────────────
# Two scalars only — no unbounded growth.


class Trader:
    """
    INTARIAN_PEPPER_ROOT strategy for Round 1.

    Core thesis
    -----------
    Pepper rises ~+1000/day in a near-linear ramp. Holding +80 units through
    a full 10k-tick day is worth ~80k XIRECs. The outage sell overlay adds
    ~5–6k in expectation when response bots exist, and costs nothing when
    they don't (unfilled orders have no P&L impact).

    Logic per tick
    --------------
    1. Holt's FV update — track level and trend slope from mid-price.
       Falls back to bid+HALF_SPREAD or ask-HALF_SPREAD when one side missing.

    2. Outage sell — if no asks are visible and position == TARGET_LONG:
       post sell(min(8, position)) at int(fv + 75).
       Guards: position must be at target (don't sell during accumulation);
               sell price must exceed best_bid (stale-FV sanity check).

    3. Accumulate / refill — if asks are present and position < TARGET_LONG:
       sweep all visible ask levels in price order until position reaches +80.
       This handles initial accumulation, post-outage refill, and everything
       in between with one unified block.

    MC results (1000 sessions, seed 20260401, local round1 data)
    ------------------------------------------------------------
    pepper_hold_v1 (pure hold):  mean 79,341  std   138  p5 79,128  p95 79,563
    170899 original (fv+100x80): mean 79,407  std   146  p5 79,194  p95 79,676
    204756 (lift-ask hold):      mean 79,431  std   162  p5 79,188  p95 79,717
    jack_r1_pepper_v3 (this):    mean 85,468  std 9,076  p5 82,106  p95 86,723
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

        # ── 1. Holt's FV update ──────────────────────────────────────────────
        if has_bids and has_asks:
            obs_mid = (best_bid + best_ask) / 2.0
        elif has_bids:
            obs_mid = best_bid + HALF_SPREAD
        elif has_asks:
            obs_mid = best_ask - HALF_SPREAD
        else:
            obs_mid = None

        level = mem.get("level")
        trend = mem.get("trend", 0.0)

        if obs_mid is not None:
            if level is None:
                level, trend = obs_mid, 0.0
            else:
                new_level = HOLT_ALPHA * obs_mid + (1.0 - HOLT_ALPHA) * (level + trend)
                trend     = HOLT_BETA  * (new_level - level) + (1.0 - HOLT_BETA) * trend
                level     = new_level
            mem["level"] = level
            mem["trend"] = trend
        elif level is None:
            result[PRODUCT] = orders
            return result, 0, json.dumps(mem)

        fv = level + trend

        # ── 2. Outage sell ───────────────────────────────────────────────────
        if (not has_asks) and has_bids and position >= TARGET_LONG:
            sell_qty = min(OUTAGE_SELL_MAX, position)
            if sell_qty > 0:
                sell_px = int(fv + OUTAGE_EDGE)
                if sell_px > best_bid:
                    orders.append(Order(PRODUCT, sell_px, -sell_qty))

        # ── 3. Accumulate / refill ───────────────────────────────────────────
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
