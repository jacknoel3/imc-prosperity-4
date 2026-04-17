import json
from datamodel import Order, OrderDepth, TradingState
from typing import Dict, List

# ── ASH_COATED_OSMIUM ────────────────────────────────────────────────────────
# FV is 10000 exactly, confirmed stationary across all 3 training days.
# Mean-revert around 10000 — do not track mid-price dynamically.
ASH_FV    = 10_000
ASH_LIMIT = 80
ASH_EDGE  = 2        # half-spread for passive quotes around FV
ASH_IMB   = 0.25     # imbalance threshold; corr(imb, fwd_ret_1)≈0.38 per repo data

# ── INTARIAN_PEPPER_ROOT ─────────────────────────────────────────────────────
# Price ramps +1000/day linearly. Holt's double-EMA tracks level + slope so
# the one-step-ahead forecast rides the ramp without lag.
IPR_LIMIT       = 80
HOLT_ALPHA      = 0.05   # level smoothing — slow, noise-robust
HOLT_BETA       = 0.10   # trend smoothing — converges to intraday slope
IPR_HALF_SPREAD = 6.5    # fallback mid estimate when one book side absent
OUTAGE_EDGE     = 75     # sell price = int(fv) + OUTAGE_EDGE during no-ask ticks
OUTAGE_SELL_MAX = 8      # max units per no-ask tick; MC-validated sweet spot


class Trader:
    def run(self, state: TradingState):
        try:
            mem = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            mem = {}

        result: Dict[str, List[Order]] = {}

        for symbol, depth in state.order_depths.items():
            pos = state.position.get(symbol, 0)
            if symbol == "ASH_COATED_OSMIUM":
                result[symbol] = self._trade_ash(depth, pos)
            elif symbol == "INTARIAN_PEPPER_ROOT":
                result[symbol], mem = self._trade_ipr(depth, pos, mem)

        return result, 0, json.dumps(mem)

    # ── ASH: fixed-FV market maker + imbalance tilt ───────────────────────────
    def _trade_ash(self, depth: OrderDepth, pos: int) -> List[Order]:
        orders: List[Order] = []
        fv = ASH_FV

        # Book imbalance signal
        tbv = sum(v for v in depth.buy_orders.values())
        tav = sum(-v for v in depth.sell_orders.values())
        tot = tbv + tav
        imb = (tbv - tav) / tot if tot > 0 else 0.0
        imb_tilt = 1 if imb > ASH_IMB else (-1 if imb < -ASH_IMB else 0)

        # 1) Aggressive takes: any ask < FV → buy; any bid > FV → sell
        for px, vol in sorted(depth.sell_orders.items()):
            if px >= fv:
                break
            qty = min(-vol, ASH_LIMIT - pos)
            if qty > 0:
                orders.append(Order("ASH_COATED_OSMIUM", px, qty))
                pos += qty

        for px, vol in sorted(depth.buy_orders.items(), reverse=True):
            if px <= fv:
                break
            qty = min(vol, ASH_LIMIT + pos)
            if qty > 0:
                orders.append(Order("ASH_COATED_OSMIUM", px, -qty))
                pos -= qty

        # 2) Passive MM: quote at FV ± EDGE, shifted by inventory + imbalance.
        # Inventory skew shifts both quotes together — when long, reservation
        # price rises (we need more premium to sell and are less eager to buy more).
        inv_skew = int(round(pos / ASH_LIMIT * ASH_EDGE))
        bid_px   = fv - ASH_EDGE + inv_skew + imb_tilt
        ask_px   = fv + ASH_EDGE + inv_skew + imb_tilt

        best_bid = max(depth.buy_orders) if depth.buy_orders else bid_px - 1
        best_ask = min(depth.sell_orders) if depth.sell_orders else ask_px + 1
        bid_px   = min(bid_px, best_ask - 1)
        ask_px   = max(ask_px, best_bid + 1)

        buy_cap  = ASH_LIMIT - pos
        sell_cap = ASH_LIMIT + pos

        # Scale passive size down at extreme inventory to limit adverse-selection risk.
        # Formula from repo rule file: max(0.3, 1.0 - |pos/limit| * 0.7)
        scale        = max(0.3, 1.0 - abs(pos / ASH_LIMIT) * 0.7)
        passive_buy  = int(buy_cap  * scale)
        passive_sell = int(sell_cap * scale)

        if passive_buy  > 0: orders.append(Order("ASH_COATED_OSMIUM", bid_px,  passive_buy))
        if passive_sell > 0: orders.append(Order("ASH_COATED_OSMIUM", ask_px, -passive_sell))

        return orders

    # ── IPR: Holt's-FV accumulate-to-limit with outage-sell overlay ───────────
    def _trade_ipr(self, depth: OrderDepth, pos: int, mem: dict):
        orders: List[Order] = []

        has_bids = bool(depth.buy_orders)
        has_asks = bool(depth.sell_orders)
        best_bid = max(depth.buy_orders)  if has_bids else None
        best_ask = min(depth.sell_orders) if has_asks else None

        # Observed mid with one-sided fallback
        if has_bids and has_asks:
            obs_mid = (best_bid + best_ask) / 2.0
        elif has_bids:
            obs_mid = best_bid + IPR_HALF_SPREAD
        elif has_asks:
            obs_mid = best_ask - IPR_HALF_SPREAD
        else:
            return orders, mem

        # Holt's linear double-exponential smoothing
        # level + trend gives the one-step-ahead FV that tracks the intraday ramp.
        level = mem.get("level")
        trend = mem.get("trend", 0.0)
        if level is None:
            level, trend = obs_mid, 0.0
        else:
            new_level = HOLT_ALPHA * obs_mid + (1.0 - HOLT_ALPHA) * (level + trend)
            trend     = HOLT_BETA  * (new_level - level) + (1.0 - HOLT_BETA) * trend
            level     = new_level
        mem["level"] = level
        mem["trend"] = trend
        fv = level + trend

        # Outage sell: when the ask side vanishes and we're fully loaded,
        # post a small premium sell to collect from any response bots.
        # These ticks are the only time we'll trade above the trend ramp.
        # Worst case: unfilled → stays at +80 → earns trend drift as usual.
        if (not has_asks) and has_bids and pos >= IPR_LIMIT:
            sell_qty = min(OUTAGE_SELL_MAX, pos)
            sell_px  = int(fv + OUTAGE_EDGE)
            if sell_qty > 0 and sell_px > best_bid:
                orders.append(Order("INTARIAN_PEPPER_ROOT", sell_px, -sell_qty))

        # Accumulate / refill to limit — sweep all visible asks in price order.
        # Handles initial fill, post-outage refill, and partial-fill gaps uniformly.
        if has_asks and pos < IPR_LIMIT:
            remaining = IPR_LIMIT - pos
            for ask_px in sorted(depth.sell_orders.keys()):
                ask_vol = abs(depth.sell_orders[ask_px])
                to_buy  = min(remaining, ask_vol)
                if to_buy > 0:
                    orders.append(Order("INTARIAN_PEPPER_ROOT", ask_px, to_buy))
                    remaining -= to_buy
                if remaining <= 0:
                    break

        return orders, mem
