from datamodel import Order, OrderDepth, TradingState
from typing import Dict, List, Optional, Tuple
import json
import math


TOMATOES = "TOMATOES"
TOMATOES_LIMIT = 80
TOMATOES_WORKING_LIMIT = 72
TOMATOES_UNWIND_START = 60


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
        prev_jump_score = self._as_float(prev_state.get("jump_score"), 0.0)
        ema_fast_prev = self._as_float(prev_state.get("ema_fast"), mid_price)
        ema_slow_prev = self._as_float(prev_state.get("ema_slow"), mid_price)
        move_var_prev = self._as_float(prev_state.get("move_var"), 1.0)

        ema_fast = 0.50 * mid_price + 0.50 * ema_fast_prev
        ema_slow = 0.12 * mid_price + 0.88 * ema_slow_prev
        trend_signal = ema_fast - ema_slow
        last_move = mid_price - prev_mid
        move_var = 0.20 * (last_move * last_move) + 0.80 * move_var_prev
        move_sigma = math.sqrt(max(move_var, 0.25))

        jump_raw = abs(last_move) / move_sigma
        book_shift = abs(microprice - prev_mid) / max(1.0, move_sigma)
        jump_score = 0.55 * prev_jump_score + 0.45 * max(jump_raw, book_shift)

        jump_up = last_move > 0 and jump_score > 1.8
        jump_down = last_move < 0 and jump_score > 1.8
        jump_active = jump_up or jump_down

        baseline_measurement = (
            mid_price
            + 0.60 * (microprice - mid_price)
            + 2.20 * imbalance
            - 0.35 * last_move
            + 0.18 * trend_signal
        )

        jump_bias = 0.0
        if jump_up:
            jump_bias = 0.85 * last_move + 0.35 * trend_signal
        elif jump_down:
            jump_bias = 0.85 * last_move + 0.35 * trend_signal

        measurement = baseline_measurement + jump_bias
        fair_alpha = 0.32 if not jump_active else 0.60
        fair_value = prev_fair + fair_alpha * (measurement - prev_fair)

        inventory_ratio = position / TOMATOES_LIMIT
        reservation_price = fair_value - 2.10 * inventory_ratio

        buy_capacity = min(TOMATOES_LIMIT - position, TOMATOES_WORKING_LIMIT - position)
        sell_capacity = min(TOMATOES_LIMIT + position, TOMATOES_WORKING_LIMIT + position)
        buy_capacity = max(0, buy_capacity)
        sell_capacity = max(0, sell_capacity)

        buy_threshold = 1.4 + max(0.0, inventory_ratio) * 1.2
        sell_threshold = 1.4 + max(0.0, -inventory_ratio) * 1.2

        if jump_up:
            buy_threshold -= 0.65
            sell_threshold += 0.80
        if jump_down:
            sell_threshold -= 0.65
            buy_threshold += 0.80

        buy_taken = 0
        for ask_price, ask_volume in sorted(depth.sell_orders.items()):
            available = abs(ask_volume)
            if available <= 0 or buy_capacity <= 0:
                continue

            edge = reservation_price - ask_price
            if edge < buy_threshold and not jump_up:
                break

            clip = min(available, buy_capacity, 14)
            if jump_up:
                clip = min(available, buy_capacity, 18)
            if jump_down and position > 30:
                clip = min(clip, 4)
            if clip > 0:
                orders.append(Order(TOMATOES, ask_price, clip))
                position += clip
                buy_capacity -= clip
                sell_capacity = min(TOMATOES_LIMIT + position, TOMATOES_WORKING_LIMIT + position)
                sell_capacity = max(0, sell_capacity)
                buy_taken += clip

        sell_taken = 0
        for bid_price, bid_volume in sorted(depth.buy_orders.items(), reverse=True):
            available = bid_volume
            if available <= 0 or sell_capacity <= 0:
                continue

            edge = bid_price - reservation_price
            if edge < sell_threshold and not jump_down:
                break

            clip = min(available, sell_capacity, 14)
            if jump_down:
                clip = min(available, sell_capacity, 18)
            if jump_up and position < -30:
                clip = min(clip, 4)
            if clip > 0:
                orders.append(Order(TOMATOES, bid_price, -clip))
                position -= clip
                sell_capacity -= clip
                buy_capacity = min(TOMATOES_LIMIT - position, TOMATOES_WORKING_LIMIT - position)
                buy_capacity = max(0, buy_capacity)
                sell_taken += clip

        inventory_ratio = position / TOMATOES_LIMIT
        reservation_price = fair_value - 2.10 * inventory_ratio

        if position >= TOMATOES_UNWIND_START and jump_down:
            unwind_qty = min(sell_capacity, max(6, min(16, position - 48)))
            if unwind_qty > 0:
                orders.append(Order(TOMATOES, best_bid, -unwind_qty))
                position -= unwind_qty
        if position <= -TOMATOES_UNWIND_START and jump_up:
            unwind_qty = min(buy_capacity, max(6, min(16, -position - 48)))
            if unwind_qty > 0:
                orders.append(Order(TOMATOES, best_ask, unwind_qty))
                position += unwind_qty

        inventory_ratio = position / TOMATOES_LIMIT
        reservation_price = fair_value - 2.10 * inventory_ratio
        buy_capacity = min(TOMATOES_LIMIT - position, TOMATOES_WORKING_LIMIT - position)
        sell_capacity = min(TOMATOES_LIMIT + position, TOMATOES_WORKING_LIMIT + position)
        buy_capacity = max(0, buy_capacity)
        sell_capacity = max(0, sell_capacity)

        quote_edge = 1.5
        if spread <= 6:
            quote_edge = 1.0
        elif spread >= 14:
            quote_edge = 2.0
        if jump_active:
            quote_edge = 0.5

        target_bid = int(math.floor(reservation_price - quote_edge))
        target_ask = int(math.ceil(reservation_price + quote_edge))

        passive_bid = min(best_bid + 1, target_bid)
        passive_ask = max(best_ask - 1, target_ask)

        if jump_up:
            passive_bid = min(best_ask - 1, max(passive_bid, best_bid + 1))
            passive_ask = max(passive_ask, best_ask + 1)
        if jump_down:
            passive_ask = max(best_bid + 1, min(passive_ask, best_ask - 1))
            passive_bid = min(passive_bid, best_bid - 1)

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
            jump_active=jump_active,
            took_liquidity=buy_taken > 0,
        )
        front_sell = self._quote_size(
            capacity=sell_capacity,
            inventory_ratio=inventory_ratio,
            same_side=False,
            jump_active=jump_active,
            took_liquidity=sell_taken > 0,
        )

        if front_buy > 0 and not (jump_down and position > 28):
            orders.append(Order(TOMATOES, passive_bid, front_buy))
        if front_sell > 0 and not (jump_up and position < -28):
            orders.append(Order(TOMATOES, passive_ask, -front_sell))

        residual_buy = max(0, buy_capacity - front_buy)
        residual_sell = max(0, sell_capacity - front_sell)

        if residual_buy > 0 and inventory_ratio < 0.45 and passive_bid - 1 > 0:
            second_buy = min(residual_buy, max(2, front_buy // 2))
            if second_buy > 0:
                orders.append(Order(TOMATOES, passive_bid - 1, second_buy))
        if residual_sell > 0 and inventory_ratio > -0.45:
            second_sell = min(residual_sell, max(2, front_sell // 2))
            if second_sell > 0:
                orders.append(Order(TOMATOES, passive_ask + 1, -second_sell))

        new_state = {
            "fair_value": fair_value,
            "last_mid": mid_price,
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "move_var": move_var,
            "jump_score": jump_score,
        }
        return orders, new_state

    def _quote_size(
        self,
        capacity: int,
        inventory_ratio: float,
        same_side: bool,
        jump_active: bool,
        took_liquidity: bool,
    ) -> int:
        if capacity <= 0:
            return 0

        pressure = inventory_ratio if same_side else -inventory_ratio
        scale = 0.38 if took_liquidity else 0.46
        scale -= max(0.0, pressure) * 0.24
        if jump_active:
            scale += 0.05
        scale = max(0.12, min(0.58, scale))

        size = int(capacity * scale)
        size = max(2, min(14, size))
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
