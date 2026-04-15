from datamodel import Order, OrderDepth, TradingState
from typing import Dict, List, Optional, Tuple
import json
import math


TOMATOES = "TOMATOES"
TOMATOES_LIMIT = 80
TOMATOES_WORKING_LIMIT = 74
TOMATOES_UNWIND_START = 70


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

        ema_fast = 0.55 * mid_price + 0.45 * ema_fast_prev
        ema_slow = 0.18 * mid_price + 0.82 * ema_slow_prev
        trend_signal = ema_fast - ema_slow
        last_move = mid_price - prev_mid

        # More aggressive directional interpretation:
        # lean into microprice pressure, imbalance, and continuation.
        measurement = (
            mid_price
            + 0.85 * (microprice - mid_price)
            + 2.80 * imbalance
            + 0.20 * last_move
            + 0.45 * trend_signal
        )
        fair_value = prev_fair + 0.50 * (measurement - prev_fair)

        inventory_ratio = position / TOMATOES_LIMIT
        reservation_price = fair_value - 1.50 * inventory_ratio

        strong_up = measurement > fair_value + 0.8
        strong_down = measurement < fair_value - 0.8

        buy_capacity = min(TOMATOES_LIMIT - position, TOMATOES_WORKING_LIMIT - position)
        sell_capacity = min(TOMATOES_LIMIT + position, TOMATOES_WORKING_LIMIT + position)
        buy_capacity = max(0, buy_capacity)
        sell_capacity = max(0, sell_capacity)

        buy_threshold = 0.75 + max(0.0, inventory_ratio) * 0.9
        sell_threshold = 0.75 + max(0.0, -inventory_ratio) * 0.9

        buy_taken = 0
        for ask_price, ask_volume in sorted(depth.sell_orders.items()):
            available = abs(ask_volume)
            if available <= 0 or buy_capacity <= 0:
                continue

            edge = reservation_price - ask_price
            if edge < buy_threshold and not strong_up:
                break

            clip = min(available, buy_capacity, 20)
            if strong_up:
                clip = min(available, buy_capacity, 24)
            if clip > 0:
                orders.append(Order(TOMATOES, ask_price, clip))
                position += clip
                buy_capacity -= clip
                sell_capacity = TOMATOES_LIMIT + position
                buy_taken += clip

        sell_taken = 0
        for bid_price, bid_volume in sorted(depth.buy_orders.items(), reverse=True):
            available = bid_volume
            if available <= 0 or sell_capacity <= 0:
                continue

            edge = bid_price - reservation_price
            if edge < sell_threshold and not strong_down:
                break

            clip = min(available, sell_capacity, 20)
            if strong_down:
                clip = min(available, sell_capacity, 24)
            if clip > 0:
                orders.append(Order(TOMATOES, bid_price, -clip))
                position -= clip
                sell_capacity -= clip
                buy_capacity = TOMATOES_LIMIT - position
                sell_taken += clip

        inventory_ratio = position / TOMATOES_LIMIT
        reservation_price = fair_value - 1.50 * inventory_ratio

        # Inventory is still respected, but we hedge less and allow larger stance.
        if position >= TOMATOES_UNWIND_START and strong_down:
            unwind_qty = min(sell_capacity, max(6, position - 60))
            orders.append(Order(TOMATOES, best_bid, -unwind_qty))
            position -= unwind_qty
            sell_capacity -= unwind_qty
            buy_capacity = min(TOMATOES_LIMIT - position, TOMATOES_WORKING_LIMIT - position)
            buy_capacity = max(0, buy_capacity)

        if position <= -TOMATOES_UNWIND_START and strong_up:
            unwind_qty = min(buy_capacity, max(6, -position - 60))
            orders.append(Order(TOMATOES, best_ask, unwind_qty))
            position += unwind_qty
            buy_capacity -= unwind_qty
            sell_capacity = min(TOMATOES_LIMIT + position, TOMATOES_WORKING_LIMIT + position)
            sell_capacity = max(0, sell_capacity)

        inventory_ratio = position / TOMATOES_LIMIT
        reservation_price = fair_value - 1.50 * inventory_ratio

        quote_edge = 1.0
        if spread >= 10:
            quote_edge = 1.5
        if strong_up or strong_down:
            quote_edge = 0.5

        target_bid = int(math.floor(reservation_price - quote_edge))
        target_ask = int(math.ceil(reservation_price + quote_edge))

        passive_bid = min(best_bid + 1, target_bid)
        passive_ask = max(best_ask - 1, target_ask)

        if strong_up:
            passive_bid = min(best_ask - 1, max(passive_bid, best_bid + 1))
        if strong_down:
            passive_ask = max(best_bid + 1, min(passive_ask, best_ask - 1))

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
            strong_signal=strong_up,
            took_liquidity=buy_taken > 0,
        )
        front_sell = self._quote_size(
            capacity=sell_capacity,
            inventory_ratio=inventory_ratio,
            same_side=False,
            strong_signal=strong_down,
            took_liquidity=sell_taken > 0,
        )

        if front_buy > 0:
            orders.append(Order(TOMATOES, passive_bid, front_buy))
        if front_sell > 0:
            orders.append(Order(TOMATOES, passive_ask, -front_sell))

        residual_buy = max(0, buy_capacity - front_buy)
        residual_sell = max(0, sell_capacity - front_sell)

        if residual_buy > 0 and passive_bid - 1 > 0 and inventory_ratio < 0.80:
            second_buy = min(residual_buy, max(3, front_buy // 2))
            orders.append(Order(TOMATOES, passive_bid - 1, second_buy))

        if residual_sell > 0 and inventory_ratio > -0.80:
            second_sell = min(residual_sell, max(3, front_sell // 2))
            orders.append(Order(TOMATOES, passive_ask + 1, -second_sell))

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
        strong_signal: bool,
        took_liquidity: bool,
    ) -> int:
        if capacity <= 0:
            return 0

        pressure = inventory_ratio if same_side else -inventory_ratio
        scale = 0.48 if took_liquidity else 0.58
        scale -= max(0.0, pressure) * 0.18
        if strong_signal:
            scale += 0.08
        scale = max(0.20, min(0.72, scale))

        size = int(capacity * scale)
        size = max(3, min(16, size))
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
