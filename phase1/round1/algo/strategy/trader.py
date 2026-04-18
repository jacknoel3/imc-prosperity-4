from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List
import json

# ── ASH_COATED_OSMIUM ─────────────────────────────────────────────────────────
ASH_FV    = 10_000
ASH_LIMIT = 80
ASH_EDGE  = 2       # half-spread around FV for passive quotes
ASH_IMB   = 0.25    # imbalance threshold for 1-tick quote tilt

# ── INTARIAN_PEPPER_ROOT ──────────────────────────────────────────────────────
IPR_LIMIT   = 80
IPR_CEILING = 20    # max ticks above current mid we'll pay (protects vs outlier asks)


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
                result[symbol] = self._trade_ipr(depth, pos)

        return result, 0, json.dumps(ts)

    # ── ASH: fixed-FV market maker + imbalance tilt ───────────────────────────
    def _trade_ash(self, depth: OrderDepth, pos: int) -> List[Order]:
        orders: List[Order] = []
        fv = ASH_FV

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

        buy_cap  = ASH_LIMIT - pos
        sell_cap = ASH_LIMIT + pos
        if buy_cap  > 0: orders.append(Order("ASH_COATED_OSMIUM", bid_px,  buy_cap))
        if sell_cap > 0: orders.append(Order("ASH_COATED_OSMIUM", ask_px, -sell_cap))

        return orders

    # ── IPR: accumulate-and-hold ──────────────────────────────────────────────
    # Price trends +1000/day. Every tick not at +80 is pure loss. Sweep all
    # available asks immediately; once at limit, hold unconditionally.
    def _trade_ipr(self, depth: OrderDepth, pos: int) -> List[Order]:
        orders: List[Order] = []

        best_bid = max(depth.buy_orders) if depth.buy_orders else None
        best_ask = min(depth.sell_orders) if depth.sell_orders else None
        if best_bid is None or best_ask is None:
            return orders

        mid     = (best_bid + best_ask) / 2.0
        ceiling = int(mid) + IPR_CEILING  # reject anomalous high asks

        if pos < IPR_LIMIT:
            # Sweep all ask levels within ceiling — fills to 80 in ~3 ticks
            for ask_px, ask_vol in sorted(depth.sell_orders.items()):
                if ask_px > ceiling:
                    break
                room = IPR_LIMIT - pos
                if room <= 0:
                    break
                qty = min(-ask_vol, room)
                if qty > 0:
                    orders.append(Order("INTARIAN_PEPPER_ROOT", ask_px, qty))
                    pos += qty

            # Passive fallback at bid+2 (0.81% fill rate) for any remaining gap
            if pos < IPR_LIMIT:
                pb  = int(best_bid) + 2
                qty = min(20, IPR_LIMIT - pos)
                if qty > 0 and pb < best_ask:
                    orders.append(Order("INTARIAN_PEPPER_ROOT", pb, qty))

        # At pos == IPR_LIMIT: hold. No sells. Trend earns +1000/day passively.
        return orders
