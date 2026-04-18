from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List
import json
from math import sqrt

# ── ASH_COATED_OSMIUM ─────────────────────────────────────────────────────────
ASH_FV    = 10_000
ASH_LIMIT = 80
ASH_EDGE  = 2       # half-spread around FV for passive quotes
ASH_IMB   = 0.25    # imbalance threshold for 1-tick quote tilt

# ── INTARIAN_PEPPER_ROOT ──────────────────────────────────────────────────────
IPR_LIMIT      = 80
IPR_EDGE       = 3
IPR_ALPHA      = 0.20
IPR_BETA       = 0.10
IPR_OBI_THRESH = 0.15
IPR_Z_THRESH   = 1.0


class Trader:
    def run(self, state: TradingState) -> tuple[Dict[str, List[Order]], int, str]:
        try:
            ts = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            ts = {}

        result: Dict[str, List[Order]] = {}

        for symbol, depth in state.order_depths.items():
            pos = state.position.get(symbol, 0)
            if symbol == "ASH_COATED_OSMIUM":
                result[symbol] = self._trade_ash(depth, pos)
            elif symbol == "INTARIAN_PEPPER_ROOT":
                result[symbol] = self._trade_ipr(depth, pos, ts)

        return result, 0, json.dumps(ts)

    def bid(self):
        return 0

    # ── ASH: fixed-FV market maker + imbalance tilt ───────────────────────────
    def _trade_ash(self, depth: OrderDepth, pos: int) -> List[Order]:
        orders: List[Order] = []
        fv = ASH_FV
        start_pos = pos
        buy_used = 0
        sell_used = 0

        # Book imbalance signal (corr ≈ 0.38 with fwd_ret_1)
        tbv = sum(v for v in depth.buy_orders.values())
        tav = sum(-v for v in depth.sell_orders.values())
        tot = tbv + tav
        imb = (tbv - tav) / tot if tot > 0 else 0.0
        imb_tilt = 1 if imb > ASH_IMB else (-1 if imb < -ASH_IMB else 0)

        # 1) Aggressive takes: any ask < FV → buy; any bid > FV → sell
        for px, vol in sorted(depth.sell_orders.items()):
            if px >= fv:
                break
            qty = min(-vol, ASH_LIMIT - start_pos - buy_used)
            if qty > 0:
                orders.append(Order("ASH_COATED_OSMIUM", px, qty))
                pos += qty
                buy_used += qty

        for px, vol in sorted(depth.buy_orders.items(), reverse=True):
            if px <= fv:
                break
            qty = min(vol, ASH_LIMIT + start_pos - sell_used)
            if qty > 0:
                orders.append(Order("ASH_COATED_OSMIUM", px, -qty))
                pos -= qty
                sell_used += qty

        # 2) Passive MM: FV ± EDGE, shifted by inventory + imbalance
        # Inventory skew: long pos → shift both quotes up (hold long, sell at premium)
        # Imbalance tilt: follow expected 1-tick direction
        inv_skew = int(round(pos / ASH_LIMIT * ASH_EDGE))
        bid_px   = fv - ASH_EDGE + inv_skew + imb_tilt
        ask_px   = fv + ASH_EDGE + inv_skew + imb_tilt

        # Guard: never cross the remaining book
        best_bid = max(depth.buy_orders) if depth.buy_orders else bid_px - 1
        best_ask = min(depth.sell_orders) if depth.sell_orders else ask_px + 1
        bid_px   = min(bid_px, best_ask - 1)
        ask_px   = max(ask_px, best_bid + 1)

        buy_cap  = ASH_LIMIT - start_pos - buy_used
        sell_cap = ASH_LIMIT + start_pos - sell_used
        if buy_cap  > 0: orders.append(Order("ASH_COATED_OSMIUM", bid_px,  buy_cap))
        if sell_cap > 0: orders.append(Order("ASH_COATED_OSMIUM", ask_px, -sell_cap))

        return orders

    # ── IPR: Holt's EMA FV + OBI/Z-score signals + passive MM ───────────────
    def _trade_ipr(self, depth: OrderDepth, pos: int, ts: dict) -> List[Order]:
        orders: List[Order] = []

        best_bid = max(depth.buy_orders) if depth.buy_orders else None
        best_ask = min(depth.sell_orders) if depth.sell_orders else None
        if best_bid is None or best_ask is None:
            return orders

        mid = (best_bid + best_ask) / 2.0

        # ── Holt's double exponential smoothing ──────────────────────────────
        level = ts.get("ipr_level", None)
        trend = ts.get("ipr_trend", 0.0)
        if level is None:
            level = mid
            trend = 0.0
        prev_level = level
        level = IPR_ALPHA * mid + (1 - IPR_ALPHA) * (level + trend)
        trend = IPR_BETA * (level - prev_level) + (1 - IPR_BETA) * trend
        fv = level + trend
        ts["ipr_level"] = level
        ts["ipr_trend"] = trend

        # ── Running residual stats for Z-score ───────────────────────────────
        top_bids = sorted(depth.buy_orders.items(), reverse=True)[:3]
        top_asks = sorted(depth.sell_orders.items())[:3]
        bid_vol = sum(vol for _, vol in top_bids)
        ask_vol = sum(-vol for _, vol in top_asks)
        denom = bid_vol + ask_vol
        if denom > 0 and bid_vol > 0 and ask_vol > 0:
            bid_vwap = sum(px * vol for px, vol in top_bids) / bid_vol
            ask_vwap = sum(px * (-vol) for px, vol in top_asks) / ask_vol
            micro = (bid_vwap * ask_vol + ask_vwap * bid_vol) / denom
        else:
            micro = mid
        residual = micro - mid

        n   = ts.get("ipr_resid_n", 0)
        rsum = ts.get("ipr_resid_sum", 0.0)
        rsq  = ts.get("ipr_resid_sq", 0.0)
        n   += 1
        rsum += residual
        rsq  += residual ** 2
        ts["ipr_resid_n"]   = n
        ts["ipr_resid_sum"] = rsum
        ts["ipr_resid_sq"]  = rsq

        if n >= 30:
            mean_r = rsum / n
            var_r  = rsq / n - mean_r ** 2
            std_r  = sqrt(var_r) if var_r > 0 else 0.0
            Z = (residual - mean_r) / std_r if std_r > 0 else 0.0
        else:
            Z = 0.0

        # ── OBI: 3-level contrarian signal ───────────────────────────────────
        tot_vol = bid_vol + ask_vol
        obi = (bid_vol - ask_vol) / tot_vol if tot_vol > 0 else 0.0

        post_bid = True
        post_ask = True
        if obi > IPR_OBI_THRESH:
            post_bid = False
        elif obi < -IPR_OBI_THRESH:
            post_ask = False

        if Z > IPR_Z_THRESH:
            post_ask = False
        elif Z < -IPR_Z_THRESH:
            post_bid = False

        # ── Quote placement ───────────────────────────────────────────────────
        base_bid = int(fv) - IPR_EDGE
        base_ask = int(fv) + IPR_EDGE

        inv_skew      = int(round(pos / IPR_LIMIT * IPR_EDGE))
        short_penalty = 1 if pos < 0 else 0

        final_bid = base_bid + inv_skew + short_penalty
        final_ask = base_ask + inv_skew + short_penalty

        ba = min(depth.sell_orders) if depth.sell_orders else final_ask + 1
        bb = max(depth.buy_orders)  if depth.buy_orders  else final_bid - 1
        final_bid = min(final_bid, ba - 1)
        final_ask = max(final_ask, bb + 1)

        # ── Sizing (lean long) ────────────────────────────────────────────────
        buy_cap  = IPR_LIMIT - pos
        sell_cap = IPR_LIMIT + pos

        buy_scale  = max(0.3, 1.0 - pos / IPR_LIMIT * 0.5)
        sell_scale = max(0.3, 1.0 - abs(pos) / IPR_LIMIT * 0.7)

        buy_qty  = max(1, int(buy_cap  * buy_scale))
        # Cap sell qty to current pos — never go net short
        sell_qty = min(pos, max(1, int(sell_cap * sell_scale))) if pos > 0 else 0

        # ── Post orders ───────────────────────────────────────────────────────
        if post_bid and buy_cap > 0:
            orders.append(Order("INTARIAN_PEPPER_ROOT", int(final_bid), buy_qty))
        # Only reduce existing longs (never initiate new shorts)
        if post_ask and sell_qty > 0 and pos > 0:
            orders.append(Order("INTARIAN_PEPPER_ROOT", int(final_ask), -sell_qty))

        return orders
