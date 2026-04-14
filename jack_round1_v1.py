from datamodel import Order, OrderDepth, TradingState
from typing import Dict, List, Optional, Tuple
import json
import math


ASH = "ASH_COATED_OSMIUM"
PEPPER = "INTARIAN_PEPPER_ROOT"

LIMITS = {
    ASH: 80,
    PEPPER: 80,
}


class Trader:
    def run(self, state: TradingState):
        trader_state = self._load_state(state.traderData)
        result: Dict[str, List[Order]] = {}

        ash_state = trader_state.get(ASH, {})
        pepper_state = trader_state.get(PEPPER, {})

        for product, depth in state.order_depths.items():
            if product == ASH:
                orders, ash_state = self._trade_ash(
                    depth=depth,
                    position=state.position.get(ASH, 0),
                    prev_state=ash_state,
                    timestamp=int(state.timestamp),
                )
                result[product] = orders
            elif product == PEPPER:
                orders, pepper_state = self._trade_pepper(
                    depth=depth,
                    position=state.position.get(PEPPER, 0),
                    prev_state=pepper_state,
                    timestamp=int(state.timestamp),
                )
                result[product] = orders
            else:
                result[product] = []

        new_state = json.dumps(
            {
                ASH: ash_state,
                PEPPER: pepper_state,
            },
            separators=(",", ":"),
        )
        return result, 0, new_state

    def _trade_ash(
        self,
        depth: OrderDepth,
        position: int,
        prev_state: Dict[str, float],
        timestamp: int,
    ) -> Tuple[List[Order], Dict[str, float]]:
        orders: List[Order] = []
        book = self._book_snapshot(depth)
        if book is None:
            return orders, prev_state

        best_bid, best_bid_volume, best_ask, best_ask_volume, mid, spread, imbalance, microprice = book

        limit = LIMITS[ASH]
        working_limit = 68
        unwind_start = 54
        danger_start = 68

        prev_mid = self._as_float(prev_state.get("last_mid"), mid)
        prev_fair = self._as_float(prev_state.get("fair_value"), 10000.0)
        ema_fast_prev = self._as_float(prev_state.get("ema_fast"), mid)
        ema_slow_prev = self._as_float(prev_state.get("ema_slow"), mid)

        last_move = mid - prev_mid
        ema_fast = 0.42 * mid + 0.58 * ema_fast_prev
        ema_slow = 0.12 * mid + 0.88 * ema_slow_prev
        trend_signal = ema_fast - ema_slow

        measurement = (
            10000.0
            + 0.55 * (mid - 10000.0)
            + 0.80 * (microprice - mid)
            + 2.40 * imbalance
            - 0.70 * last_move
            + 0.15 * trend_signal
        )
        fair_value = 0.78 * prev_fair + 0.22 * measurement

        inventory_ratio = position / limit
        inventory_pressure = position / working_limit
        reservation_price = fair_value - 3.10 * inventory_ratio

        buy_capacity = max(0, min(limit - position, working_limit - position))
        sell_capacity = max(0, min(limit + position, working_limit + position))

        signal_strength = measurement - fair_value
        buy_take_threshold = 2.8 + max(0.0, inventory_pressure) * 2.2
        sell_take_threshold = 2.8 + max(0.0, -inventory_pressure) * 2.2

        if imbalance > 0.45:
            buy_take_threshold -= 0.9
        if imbalance < -0.45:
            sell_take_threshold -= 0.9

        if position <= -unwind_start:
            buy_take_threshold -= 1.0
        if position >= unwind_start:
            sell_take_threshold -= 1.0

        buy_taken = 0
        for ask_price, ask_volume in sorted(depth.sell_orders.items()):
            available = abs(ask_volume)
            if available <= 0 or buy_capacity <= 0:
                continue

            edge = reservation_price - ask_price
            should_take = edge >= buy_take_threshold
            should_take = should_take or (
                imbalance > 0.72 and ask_price <= math.floor(fair_value) and signal_strength > 0.8
            )
            should_take = should_take or (
                position <= -danger_start and ask_price <= best_ask + 1
            )

            if not should_take:
                break

            clip = min(available, buy_capacity, self._take_clip(position, True, limit))
            if clip <= 0:
                continue
            orders.append(Order(ASH, ask_price, clip))
            position += clip
            buy_capacity -= clip
            sell_capacity = max(0, min(limit + position, working_limit + position))
            buy_taken += clip

        sell_taken = 0
        for bid_price, bid_volume in sorted(depth.buy_orders.items(), reverse=True):
            available = bid_volume
            if available <= 0 or sell_capacity <= 0:
                continue

            edge = bid_price - reservation_price
            should_take = edge >= sell_take_threshold
            should_take = should_take or (
                imbalance < -0.72 and bid_price >= math.ceil(fair_value) and signal_strength < -0.8
            )
            should_take = should_take or (
                position >= danger_start and bid_price >= best_bid - 1
            )

            if not should_take:
                break

            clip = min(available, sell_capacity, self._take_clip(position, False, limit))
            if clip <= 0:
                continue
            orders.append(Order(ASH, bid_price, -clip))
            position -= clip
            sell_capacity -= clip
            buy_capacity = max(0, min(limit - position, working_limit - position))
            sell_taken += clip

        inventory_ratio = position / limit
        reservation_price = fair_value - 3.10 * inventory_ratio

        quote_half_spread = 2.2
        if spread <= 10:
            quote_half_spread = 1.6
        elif spread >= 18:
            quote_half_spread = 2.8

        if abs(inventory_ratio) > 0.45:
            quote_half_spread += 0.4
        if abs(signal_strength) > 1.6:
            quote_half_spread -= 0.2

        target_bid = int(math.floor(reservation_price - quote_half_spread))
        target_ask = int(math.ceil(reservation_price + quote_half_spread))

        passive_bid = min(best_bid + 1, target_bid)
        passive_ask = max(best_ask - 1, target_ask)

        if spread <= 2:
            passive_bid = min(best_bid, target_bid)
            passive_ask = max(best_ask, target_ask)

        passive_bid, passive_ask = self._sanitize_quotes(passive_bid, passive_ask, best_bid, best_ask)

        front_buy = self._quote_size(
            capacity=buy_capacity,
            position=position,
            limit=limit,
            side="buy",
            target_position=0,
            took_liquidity=buy_taken > 0,
        )
        front_sell = self._quote_size(
            capacity=sell_capacity,
            position=position,
            limit=limit,
            side="sell",
            target_position=0,
            took_liquidity=sell_taken > 0,
        )

        if front_buy > 0:
            orders.append(Order(ASH, passive_bid, front_buy))
        if front_sell > 0:
            orders.append(Order(ASH, passive_ask, -front_sell))

        residual_buy = max(0, buy_capacity - front_buy)
        residual_sell = max(0, sell_capacity - front_sell)

        if residual_buy > 0 and position < 16 and passive_bid - 1 > 0:
            second_buy = min(residual_buy, max(2, front_buy // 2))
            if second_buy > 0:
                orders.append(Order(ASH, passive_bid - 1, second_buy))

        if residual_sell > 0 and position > -16:
            second_sell = min(residual_sell, max(2, front_sell // 2))
            if second_sell > 0:
                orders.append(Order(ASH, passive_ask + 1, -second_sell))

        new_state = {
            "fair_value": fair_value,
            "last_mid": mid,
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "last_timestamp": timestamp,
        }
        return orders, new_state

    def _trade_pepper(
        self,
        depth: OrderDepth,
        position: int,
        prev_state: Dict[str, float],
        timestamp: int,
    ) -> Tuple[List[Order], Dict[str, float]]:
        orders: List[Order] = []
        book = self._book_snapshot(depth)
        if book is None:
            return orders, prev_state

        best_bid, best_bid_volume, best_ask, best_ask_volume, mid, spread, imbalance, microprice = book

        limit = LIMITS[PEPPER]
        working_limit = 64
        unwind_start = 50
        danger_start = 66
        trend_per_step = 0.1002
        inventory_target = 10

        prev_mid = self._as_float(prev_state.get("last_mid"), mid)
        prev_fair = self._as_float(prev_state.get("fair_value"), mid)
        prev_step = int(prev_state.get("step_index", 0))
        prev_anchor = self._as_float(prev_state.get("anchor"), mid)
        prev_timestamp = int(prev_state.get("last_timestamp", timestamp))

        if timestamp < prev_timestamp:
            step_index = 0
            day_anchor = mid
        else:
            inferred_step = max(0, int(round(timestamp / 100)))
            step_index = max(prev_step, inferred_step)
            day_anchor = prev_anchor

        anchor_measurement = mid - trend_per_step * step_index
        day_anchor = 0.92 * day_anchor + 0.08 * anchor_measurement
        trend_fair = day_anchor + trend_per_step * step_index

        expected_step_move = trend_per_step * max(1, step_index - prev_step) if step_index >= prev_step else trend_per_step
        residual_move = (mid - prev_mid) - expected_step_move
        signal_strength = (
            0.65 * (microprice - mid)
            + 2.55 * imbalance
            - 0.55 * residual_move
            + 0.18
        )

        measurement = trend_fair + signal_strength
        fair_value = 0.72 * prev_fair + 0.28 * measurement

        target_gap = position - inventory_target
        inventory_ratio = target_gap / limit
        inventory_pressure = target_gap / working_limit
        reservation_price = fair_value - 2.70 * inventory_ratio

        buy_capacity = max(0, min(limit - position, working_limit - target_gap))
        sell_capacity = max(0, min(limit + position, working_limit + target_gap))

        buy_take_threshold = 3.4 + max(0.0, inventory_pressure) * 2.0
        sell_take_threshold = 3.8 + max(0.0, -inventory_pressure) * 2.3

        if imbalance > 0.45:
            buy_take_threshold -= 0.8
        if imbalance < -0.45:
            sell_take_threshold -= 0.6

        if position <= -unwind_start:
            buy_take_threshold -= 1.2
        if position >= unwind_start:
            sell_take_threshold -= 1.2

        buy_taken = 0
        for ask_price, ask_volume in sorted(depth.sell_orders.items()):
            available = abs(ask_volume)
            if available <= 0 or buy_capacity <= 0:
                continue

            edge = reservation_price - ask_price
            strong_up_signal = imbalance > 0.72 and signal_strength > 1.5
            should_take = edge >= buy_take_threshold
            should_take = should_take or (strong_up_signal and ask_price <= math.floor(fair_value))
            should_take = should_take or (position <= -danger_start and ask_price <= best_ask + 1)

            if not should_take:
                break

            clip = min(available, buy_capacity, self._take_clip(position, True, limit))
            if clip <= 0:
                continue
            orders.append(Order(PEPPER, ask_price, clip))
            position += clip
            buy_capacity -= clip
            sell_capacity = max(0, min(limit + position, working_limit + inventory_target - position))
            buy_taken += clip

        sell_taken = 0
        for bid_price, bid_volume in sorted(depth.buy_orders.items(), reverse=True):
            available = bid_volume
            if available <= 0 or sell_capacity <= 0:
                continue

            edge = bid_price - reservation_price
            strong_down_signal = imbalance < -0.80 and signal_strength < -2.2
            should_take = edge >= sell_take_threshold
            should_take = should_take or (strong_down_signal and bid_price >= math.ceil(fair_value) + 1)
            should_take = should_take or (position >= danger_start and bid_price >= best_bid - 1)

            if not should_take:
                break

            clip = min(available, sell_capacity, self._take_clip(position, False, limit))
            if clip <= 0:
                continue
            orders.append(Order(PEPPER, bid_price, -clip))
            position -= clip
            sell_capacity -= clip
            buy_capacity = max(0, min(limit - position, working_limit - (position - inventory_target)))
            sell_taken += clip

        target_gap = position - inventory_target
        inventory_ratio = target_gap / limit
        reservation_price = fair_value - 2.70 * inventory_ratio

        bid_edge = 1.7
        ask_edge = 2.4
        if spread <= 8:
            bid_edge = 1.2
            ask_edge = 1.8
        elif spread >= 16:
            bid_edge = 2.0
            ask_edge = 2.7

        if signal_strength > 0.9:
            bid_edge -= 0.3
            ask_edge += 0.2
        elif signal_strength < -0.9:
            bid_edge += 0.2
            ask_edge -= 0.3

        if position >= unwind_start:
            bid_edge += 0.6
            ask_edge -= 0.5
        elif position <= -unwind_start:
            bid_edge -= 0.5
            ask_edge += 0.6

        target_bid = int(math.floor(reservation_price - bid_edge))
        target_ask = int(math.ceil(reservation_price + ask_edge))

        passive_bid = min(best_bid + 1, target_bid)
        passive_ask = max(best_ask - 1, target_ask)
        passive_bid, passive_ask = self._sanitize_quotes(passive_bid, passive_ask, best_bid, best_ask)

        front_buy = self._quote_size(
            capacity=buy_capacity,
            position=position,
            limit=limit,
            side="buy",
            target_position=inventory_target,
            took_liquidity=buy_taken > 0,
        )
        front_sell = self._quote_size(
            capacity=sell_capacity,
            position=position,
            limit=limit,
            side="sell",
            target_position=inventory_target,
            took_liquidity=sell_taken > 0,
        )

        if front_buy > 0:
            orders.append(Order(PEPPER, passive_bid, front_buy))
        if front_sell > 0:
            orders.append(Order(PEPPER, passive_ask, -front_sell))

        residual_buy = max(0, buy_capacity - front_buy)
        residual_sell = max(0, sell_capacity - front_sell)

        if residual_buy > 0 and position < inventory_target + 18 and passive_bid - 1 > 0:
            second_buy = min(residual_buy, max(2, front_buy // 2))
            if second_buy > 0:
                orders.append(Order(PEPPER, passive_bid - 1, second_buy))

        if residual_sell > 0 and position > inventory_target - 24:
            second_sell = min(residual_sell, max(2, front_sell // 2))
            if second_sell > 0:
                orders.append(Order(PEPPER, passive_ask + 1, -second_sell))

        new_state = {
            "fair_value": fair_value,
            "last_mid": mid,
            "anchor": day_anchor,
            "step_index": step_index,
            "last_timestamp": timestamp,
        }
        return orders, new_state

    def _book_snapshot(
        self, depth: OrderDepth
    ) -> Optional[Tuple[int, int, int, int, float, int, float, float]]:
        if not depth.buy_orders or not depth.sell_orders:
            return None

        best_bid = max(depth.buy_orders)
        best_ask = min(depth.sell_orders)
        bid_volume = depth.buy_orders[best_bid]
        ask_volume = abs(depth.sell_orders[best_ask])
        spread = best_ask - best_bid
        mid = (best_bid + best_ask) / 2.0

        total_volume = bid_volume + ask_volume
        imbalance = 0.0
        microprice = mid
        if total_volume > 0:
            imbalance = (bid_volume - ask_volume) / total_volume
            microprice = (best_ask * bid_volume + best_bid * ask_volume) / total_volume

        return best_bid, bid_volume, best_ask, ask_volume, mid, spread, imbalance, microprice

    def _sanitize_quotes(
        self,
        bid_price: int,
        ask_price: int,
        best_bid: int,
        best_ask: int,
    ) -> Tuple[int, int]:
        if bid_price >= best_ask:
            bid_price = best_ask - 1
        if ask_price <= best_bid:
            ask_price = best_bid + 1
        if bid_price >= ask_price:
            bid_price = ask_price - 1
        return bid_price, ask_price

    def _quote_size(
        self,
        capacity: int,
        position: int,
        limit: int,
        side: str,
        target_position: int,
        took_liquidity: bool,
    ) -> int:
        if capacity <= 0:
            return 0

        target_gap = position - target_position
        if side == "buy":
            pressure = max(0.0, target_gap / limit)
        else:
            pressure = max(0.0, -target_gap / limit)

        scale = 0.42 if not took_liquidity else 0.31
        scale -= 0.24 * pressure
        scale = max(0.12, min(0.55, scale))

        size = int(capacity * scale)
        size = max(2, min(14, size))
        return min(size, capacity)

    def _take_clip(self, position: int, buy_side: bool, limit: int) -> int:
        heavy = 0.60 * limit
        if buy_side and position > heavy:
            return 5
        if (not buy_side) and position < -heavy:
            return 5
        return 12

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
