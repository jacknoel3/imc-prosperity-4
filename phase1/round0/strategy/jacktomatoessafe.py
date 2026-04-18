from datamodel import Order, OrderDepth, TradingState
from typing import Dict, List, Optional, Tuple
import json
import math


TOMATOES = "TOMATOES"
TOMATOES_LIMIT = 80
TOMATOES_WORKING_LIMIT = 56
TOMATOES_TREND_LIMIT = 40
TOMATOES_UNWIND_START = 32


class Trader:
    def run(self, state: TradingState):
        trader_state = self._load_state(state.traderData)
        result: Dict[str, List[Order]] = {}

        tomatoes_state = trader_state.get(TOMATOES, {})

        for product, depth in state.order_depths.items():
            if product == TOMATOES:
                orders, tomatoes_state = self._trade_tomatoes(
                    depth=depth,
                    position=state.position.get(TOMATOES, 0),
                    prev_state=tomatoes_state,
                )
                result[product] = orders
            else:
                result[product] = []

        new_state = json.dumps({TOMATOES: tomatoes_state}, separators=(",", ":"))
        return result, 0, new_state

    def _trade_tomatoes(
        self,
        depth: OrderDepth,
        position: int,
        prev_state: Dict[str, float],
    ) -> Tuple[List[Order], Dict[str, float]]:
        orders: List[Order] = []

        if not depth.buy_orders or not depth.sell_orders:
            return orders, prev_state

        best_bid = max(depth.buy_orders)
        best_ask = min(depth.sell_orders)
        spread = best_ask - best_bid
        mid_price = (best_bid + best_ask) / 2.0

        bid_volume = depth.buy_orders[best_bid]
        ask_volume = abs(depth.sell_orders[best_ask])
        total_top_volume = bid_volume + ask_volume

        imbalance = 0.0
        microprice = mid_price
        if total_top_volume > 0:
            imbalance = (bid_volume - ask_volume) / total_top_volume
            microprice = (best_ask * bid_volume + best_bid * ask_volume) / total_top_volume

        prev_fair = self._as_float(prev_state.get("fair_value"), mid_price)
        prev_mid = self._as_float(prev_state.get("last_mid"), mid_price)
        ema_fast_prev = self._as_float(prev_state.get("ema_fast"), mid_price)
        ema_slow_prev = self._as_float(prev_state.get("ema_slow"), mid_price)

        ema_fast = 0.35 * mid_price + 0.65 * ema_fast_prev
        ema_slow = 0.10 * mid_price + 0.90 * ema_slow_prev
        trend_signal = ema_fast - ema_slow
        last_move = mid_price - prev_mid

        measurement = (
            mid_price
            + 0.55 * (microprice - mid_price)
            + 2.00 * imbalance
            - 0.75 * last_move
            + 0.10 * trend_signal
        )
        fair_value = prev_fair + 0.25 * (measurement - prev_fair)

        inventory_ratio = position / TOMATOES_LIMIT
        reservation_price = fair_value - 3.40 * inventory_ratio

        trend_down = last_move < 0 and trend_signal < 0
        trend_up = last_move > 0 and trend_signal > 0

        soft_buy_cap = TOMATOES_WORKING_LIMIT
        soft_sell_cap = TOMATOES_WORKING_LIMIT
        if trend_down:
            soft_buy_cap = TOMATOES_TREND_LIMIT
        if trend_up:
            soft_sell_cap = TOMATOES_TREND_LIMIT

        buy_capacity = max(0, min(TOMATOES_LIMIT - position, soft_buy_cap - max(position, 0)))
        sell_capacity = max(0, min(TOMATOES_LIMIT + position, soft_sell_cap - max(-position, 0)))

        buy_threshold = 1.8 + max(0.0, inventory_ratio) * 2.0
        sell_threshold = 1.8 + max(0.0, -inventory_ratio) * 2.0
        if trend_down:
            buy_threshold += 1.2
        if trend_up:
            sell_threshold += 1.2

        buy_taken = 0
        for ask_price, ask_volume in sorted(depth.sell_orders.items()):
            available = abs(ask_volume)
            if available <= 0 or buy_capacity <= 0:
                continue

            edge = reservation_price - ask_price
            if edge < buy_threshold:
                break

            clip = min(available, buy_capacity, 8)
            if trend_down and position >= 20:
                clip = min(clip, 3)
            if clip > 0:
                orders.append(Order(TOMATOES, ask_price, clip))
                position += clip
                buy_capacity = max(0, min(TOMATOES_LIMIT - position, soft_buy_cap - max(position, 0)))
                sell_capacity = max(0, min(TOMATOES_LIMIT + position, soft_sell_cap - max(-position, 0)))
                buy_taken += clip

        sell_taken = 0
        for bid_price, bid_volume in sorted(depth.buy_orders.items(), reverse=True):
            available = bid_volume
            if available <= 0 or sell_capacity <= 0:
                continue

            edge = bid_price - reservation_price
            if edge < sell_threshold:
                break

            clip = min(available, sell_capacity, 8)
            if trend_up and position <= -20:
                clip = min(clip, 3)
            if clip > 0:
                orders.append(Order(TOMATOES, bid_price, -clip))
                position -= clip
                buy_capacity = max(0, min(TOMATOES_LIMIT - position, soft_buy_cap - max(position, 0)))
                sell_capacity = max(0, min(TOMATOES_LIMIT + position, soft_sell_cap - max(-position, 0)))
                sell_taken += clip

        inventory_ratio = position / TOMATOES_LIMIT
        reservation_price = fair_value - 3.40 * inventory_ratio

        # The log drawdowns were mostly long inventory during local selloffs.
        # Force quicker inventory release in that exact generic situation.
        if position >= TOMATOES_UNWIND_START and trend_down:
            unwind_qty = min(TOMATOES_LIMIT + position, max(4, min(10, position - 24)))
            if unwind_qty > 0:
                orders.append(Order(TOMATOES, best_bid, -unwind_qty))
                position -= unwind_qty

        if position <= -TOMATOES_UNWIND_START and trend_up:
            unwind_qty = min(TOMATOES_LIMIT - position, max(4, min(10, -position - 24)))
            if unwind_qty > 0:
                orders.append(Order(TOMATOES, best_ask, unwind_qty))
                position += unwind_qty

        inventory_ratio = position / TOMATOES_LIMIT
        reservation_price = fair_value - 3.40 * inventory_ratio
        buy_capacity = max(0, min(TOMATOES_LIMIT - position, TOMATOES_WORKING_LIMIT - position))
        sell_capacity = max(0, min(TOMATOES_LIMIT + position, TOMATOES_WORKING_LIMIT + position))

        quote_edge = 2.0 if spread <= 8 else 3.0
        if trend_down and position > 0:
            quote_edge += 1.0
        if trend_up and position < 0:
            quote_edge += 1.0
        if abs(inventory_ratio) > 0.45:
            quote_edge += 0.5

        target_bid = int(math.floor(reservation_price - quote_edge))
        target_ask = int(math.ceil(reservation_price + quote_edge))

        passive_bid = min(best_bid + 1, target_bid)
        passive_ask = max(best_ask - 1, target_ask)

        if trend_down and position > 0:
            passive_bid = min(passive_bid, best_bid - 1)
        if trend_up and position < 0:
            passive_ask = max(passive_ask, best_ask + 1)

        if passive_bid >= best_ask:
            passive_bid = best_ask - 1
        if passive_ask <= best_bid:
            passive_ask = best_bid + 1

        if passive_bid >= passive_ask:
            passive_bid = min(passive_bid, passive_ask - 1)
            passive_ask = max(passive_ask, passive_bid + 1)

        front_buy = self._quote_size(
            capacity=buy_capacity,
            inventory_ratio=inventory_ratio,
            same_side=True,
            adverse_trend=trend_down,
            took_liquidity=buy_taken > 0,
        )
        front_sell = self._quote_size(
            capacity=sell_capacity,
            inventory_ratio=inventory_ratio,
            same_side=False,
            adverse_trend=trend_up,
            took_liquidity=sell_taken > 0,
        )

        if front_buy > 0 and not (trend_down and position > 20):
            orders.append(Order(TOMATOES, passive_bid, front_buy))
        if front_sell > 0 and not (trend_up and position < -20):
            orders.append(Order(TOMATOES, passive_ask, -front_sell))

        new_state = {
            "fair_value": fair_value,
            "last_mid": mid_price,
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
        }
        return orders, new_state

    def _quote_size(
        self,
        capacity: int,
        inventory_ratio: float,
        same_side: bool,
        adverse_trend: bool,
        took_liquidity: bool,
    ) -> int:
        if capacity <= 0:
            return 0

        pressure = inventory_ratio if same_side else -inventory_ratio
        scale = 0.28 if took_liquidity else 0.34
        scale -= max(0.0, pressure) * 0.35
        if adverse_trend:
            scale -= 0.10
        scale = max(0.08, min(0.40, scale))

        size = int(capacity * scale)
        size = max(1, min(8, size))
        return min(size, capacity)

    def _load_state(self, trader_data: str) -> Dict[str, Dict[str, float]]:
        try:
            parsed = json.loads(trader_data) if trader_data else {}
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def _as_float(self, value: Optional[float], fallback: float) -> float:
        try:
            return float(value)
        except Exception:
            return fallback
