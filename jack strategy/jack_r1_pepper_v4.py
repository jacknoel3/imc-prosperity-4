"""
jack_r1_pepper_v4 — INTARIAN_PEPPER_ROOT only
==============================================
Built on jack_r1_pepper_v3 (mean 85,468 over 1000 MC sessions).

One change vs v3: adds a front-of-queue passive bid whenever position is
still below TARGET_LONG after the accumulation sweep.  Posting at
best_bid + 1 gives zero queue-ahead (no bots sit at that price), so any
incoming sell taker fills us first.  This adds marginal alpha in two cases:

  A) No-ask outage + underweight:  v3 posts nothing; we post a passive bid
     to catch any sell taker during the outage.
  B) Normal market + ask depth exhausted before reaching 80:  the passive
     bid acts as a backup fill channel for the remaining gap.

Expected gain vs v3: small (~200–500 XIRECs/session when triggered), zero
downside (an unfilled passive bid has no P&L impact).

Everything else — Holt's FV, outage sell at FV+75, aggressive ask sweep —
is identical to v3.
"""

from datamodel import Order, TradingState
from typing import Dict, List
import json

PRODUCT      = "INTARIAN_PEPPER_ROOT"
POS_LIMIT    = 80
TARGET_LONG  = 80

HOLT_ALPHA   = 0.05
HOLT_BETA    = 0.10
HALF_SPREAD  = 6.5

OUTAGE_EDGE     = 75
OUTAGE_SELL_MAX = 8


class Trader:
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

        # ── 2. Outage sell (unchanged from v3) ───────────────────────────────
        if (not has_asks) and has_bids and position >= TARGET_LONG:
            sell_qty = min(OUTAGE_SELL_MAX, position)
            if sell_qty > 0:
                sell_px = int(fv + OUTAGE_EDGE)
                if sell_px > best_bid:
                    orders.append(Order(PRODUCT, sell_px, -sell_qty))

        # ── 3. Accumulate / refill (unchanged from v3) ───────────────────────
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

        # ── 4. Front-of-queue passive bid (new in v4) ────────────────────────
        # Post at best_bid+1 whenever we still need units after the sweep above.
        # Zero queue-ahead: no bots sit at this price, so any sell taker fills
        # us first. Guards: only when bids exist; don't cross the ask.
        if position < TARGET_LONG and has_bids:
            remaining = TARGET_LONG - position
            # Subtract any aggressive orders already queued for this tick
            aggressive_qty = sum(o.quantity for o in orders if o.quantity > 0)
            still_needed = max(0, remaining - aggressive_qty)
            if still_needed > 0:
                pb = best_bid + 1
                if best_ask is None or pb < best_ask:
                    orders.append(Order(PRODUCT, pb, min(still_needed, 20)))

        result[PRODUCT] = orders
        return result, 0, json.dumps(mem)
