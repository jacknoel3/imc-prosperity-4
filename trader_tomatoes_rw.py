from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List
import json

# ── Constants ────────────────────────────────────────────────────────────────
EMERALDS_FV       = 10_000
EMERALDS_LIMIT    = 20
TOMATOES_LIMIT    = 35

# How aggressively to quote inside the spread
EMERALDS_EDGE     = 2   # post bids at FV-2..FV-1, asks at FV+1..FV+2
TOMATOES_EDGE     = 3   # half-spread around dynamic mid


class Trader:
    # ── Entry point ──────────────────────────────────────────────────────────
    def run(self, state: TradingState):
        # No cross-tick state needed for random walk FV (pure instantaneous mid)
        try:
            trader_state = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            trader_state = {}

        result: Dict[str, List[Order]] = {}

        for symbol, depth in state.order_depths.items():
            pos = state.position.get(symbol, 0)

            if symbol == "EMERALDS":
                result[symbol] = self._trade_emeralds(depth, pos)
            elif symbol == "TOMATOES":
                result[symbol] = self._trade_tomatoes(depth, pos)

        # No state to persist for random walk model
        new_state = json.dumps({})
        return result, 0, new_state

    # ── EMERALDS — fixed fair value market maker ──────────────────────────────
    def _trade_emeralds(self, depth: OrderDepth, pos: int) -> List[Order]:
        orders: List[Order] = []
        fv = EMERALDS_FV
        limit = EMERALDS_LIMIT

        best_ask = min(depth.sell_orders) if depth.sell_orders else None
        best_bid = max(depth.buy_orders)  if depth.buy_orders  else None

        # Passive market-making inside the spread, skewed by inventory
        skew = int(round(pos / limit * EMERALDS_EDGE))
        bid_px = fv - EMERALDS_EDGE + skew
        ask_px = fv + EMERALDS_EDGE + skew

        # Never bid at or above FV (breakeven at best, adverse selection at worst)
        bid_px = min(bid_px, fv - 1)

        # Don't cross the book
        if best_ask is not None and bid_px >= best_ask:
            bid_px = best_ask - 1
        if best_bid is not None and ask_px <= best_bid:
            ask_px = best_bid + 1

        buy_capacity  = limit - pos
        sell_capacity = limit + pos

        if buy_capacity > 0:
            orders.append(Order("EMERALDS", bid_px, buy_capacity))
        if sell_capacity > 0:
            orders.append(Order("EMERALDS", ask_px, -sell_capacity))

        return orders

    # ── TOMATOES — random walk FV: instantaneous mid-price, no smoothing ──────
    def _trade_tomatoes(self, depth: OrderDepth, pos: int) -> List[Order]:
        orders: List[Order] = []
        limit = TOMATOES_LIMIT

        best_bid = max(depth.buy_orders)  if depth.buy_orders  else None
        best_ask = min(depth.sell_orders) if depth.sell_orders else None

        if best_bid is None or best_ask is None:
            return orders

        # Random walk assumption: best estimate of FV is the current mid-price
        # (alpha=1.0 equivalent — no smoothing, no memory of prior ticks)
        mid = (best_bid + best_ask) / 2.0
        fv = mid

        # 1) Take obvious mispricings
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

        # 2) Passive quotes — skew by inventory
        inventory_ratio = pos / limit
        skew = int(round(inventory_ratio * TOMATOES_EDGE))

        passive_bid = int(fv) - TOMATOES_EDGE + skew
        passive_ask = int(fv) + TOMATOES_EDGE + skew

        # Reduce size when inventory is heavy
        size_scale = max(0.3, 1.0 - abs(inventory_ratio) * 0.7)

        buy_capacity  = max(0, int((limit - pos) * size_scale))
        sell_capacity = max(0, int((limit + pos) * size_scale))

        # Don't cross the book
        if passive_bid >= best_ask:
            passive_bid = best_ask - 1
        if passive_ask <= best_bid:
            passive_ask = best_bid + 1

        if buy_capacity > 0:
            orders.append(Order("TOMATOES", passive_bid, buy_capacity))
        if sell_capacity > 0:
            orders.append(Order("TOMATOES", passive_ask, -sell_capacity))

        return orders
