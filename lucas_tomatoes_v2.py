from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List
import json

# ── Constants ────────────────────────────────────────────────────────────────
TOMATOES_LIMIT = 35
TOMATOES_ALPHA = 0.05


class Trader:
    # ── Entry point ──────────────────────────────────────────────────────────
    def run(self, state: TradingState):
        try:
            trader_state = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            trader_state = {}

        tomatoes_ema: float = trader_state.get("tomatoes_ema", 0.0)

        result: Dict[str, List[Order]] = {}

        for symbol, depth in state.order_depths.items():
            if symbol == "TOMATOES":
                pos = state.position.get(symbol, 0)
                orders, tomatoes_ema = self._trade_tomatoes(depth, pos, tomatoes_ema)
                result[symbol] = orders

        new_state = json.dumps({"tomatoes_ema": tomatoes_ema})
        return result, 0, new_state

    # ── TOMATOES — dynamic fair value via slow EMA, two-level passive MM ─────
    def _trade_tomatoes(
        self, depth: OrderDepth, pos: int, ema: float
    ) -> tuple[List[Order], float]:
        orders: List[Order] = []
        limit = TOMATOES_LIMIT

        best_bid = max(depth.buy_orders)  if depth.buy_orders  else None
        best_ask = min(depth.sell_orders) if depth.sell_orders else None

        if best_bid is None or best_ask is None:
            return orders, ema

        mid = (best_bid + best_ask) / 2.0

        alpha = TOMATOES_ALPHA
        prev_ema = ema
        ema = mid if ema == 0.0 else alpha * mid + (1 - alpha) * ema
        fv = ema

        # 1) Aggressive take — snipe mispricings
        for ask_px, ask_vol in sorted(depth.sell_orders.items()):
            if ask_px < fv - 1:
                buy_qty = min(-ask_vol, limit - pos)
                if buy_qty > 0:
                    orders.append(Order("TOMATOES", ask_px, buy_qty))
                    pos += buy_qty

        for bid_px, bid_vol in sorted(depth.buy_orders.items(), reverse=True):
            if bid_px > fv:
                sell_qty = min(bid_vol, limit + pos)
                if sell_qty > 0:
                    orders.append(Order("TOMATOES", bid_px, -sell_qty))
                    pos -= sell_qty

        # 2) Two-level passive quotes — inner (tight) + outer (wide)
        inventory_ratio = pos / limit
        abs_ratio = abs(inventory_ratio)

        # Inventory skew shifts both levels
        skew = int(round(inventory_ratio * 2))

        # Inner level at ±4, outer at ±6
        inner_bid = int(fv) - 4 + skew
        inner_ask = int(fv) + 4 + skew
        outer_bid = int(fv) - 6 + skew
        outer_ask = int(fv) + 6 + skew

        # Size: 20% inner, 80% outer, scaled by inventory
        size_scale = max(0.3, 1.0 - abs_ratio * 0.7)
        total_buy  = max(0, int((limit - pos) * size_scale))
        total_sell = max(0, int((limit + pos) * size_scale))

        # Lag signal adjusts total capacity — computed from prev tick EMA
        lag = prev_ema - mid
        if lag > 1:
            total_buy  = min(limit - pos, int(total_buy  * 1.5))
            total_sell = max(0, int(total_sell * 0.6))
        elif lag < -1:
            total_sell = min(limit + pos, int(total_sell * 1.5))
            total_buy  = max(0, int(total_buy  * 0.6))

        inner_buy  = int(total_buy  * 0.2)
        outer_buy  = total_buy  - inner_buy
        inner_sell = int(total_sell * 0.2)
        outer_sell = total_sell - inner_sell

        # Guard inner quotes against crossing book
        if inner_bid >= best_ask:
            inner_bid = best_ask - 1
        if inner_ask <= best_bid:
            inner_ask = best_bid + 1

        # Guard outer quotes
        if outer_bid >= best_ask:
            outer_bid = best_ask - 1
        if outer_ask <= best_bid:
            outer_ask = best_bid + 1

        # Avoid quoting same price on inner/outer
        if outer_bid >= inner_bid:
            outer_bid = inner_bid - 1
        if outer_ask <= inner_ask:
            outer_ask = inner_ask + 1

        if inner_buy > 0:
            orders.append(Order("TOMATOES", inner_bid, inner_buy))
        if outer_buy > 0:
            orders.append(Order("TOMATOES", outer_bid, outer_buy))
        if inner_sell > 0:
            orders.append(Order("TOMATOES", inner_ask, -inner_sell))
        if outer_sell > 0:
            orders.append(Order("TOMATOES", outer_ask, -outer_sell))

        return orders, ema
