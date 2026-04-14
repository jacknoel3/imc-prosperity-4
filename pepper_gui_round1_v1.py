import json
import math
from typing import Dict, List, Optional, Tuple

from datamodel import Order, OrderDepth, TradingState


ASH = "ASH_COATED_OSMIUM"
PEPPER = "INTARIAN_PEPPER_ROOT"

LIMITS = {
    ASH: 80,
    PEPPER: 80,
}


class Trader:
    def run(self, state: TradingState):
        trader_state = self._load_state(state.traderData)
        pepper_state = trader_state.get(PEPPER, {})
        result: Dict[str, List[Order]] = {}

        for product, depth in state.order_depths.items():
            if product == PEPPER:
                orders, pepper_state = self._trade_pepper(
                    depth=depth,
                    position=state.position.get(PEPPER, 0),
                    prev_state=pepper_state,
                    timestamp=int(state.timestamp),
                )
                result[product] = orders
            else:
                result[product] = []

        new_state = json.dumps({PEPPER: pepper_state}, separators=(",", ":"))
        return result, 0, new_state

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

        best_bid, _, best_ask, _, mid, spread, imbalance, microprice = book

        limit = LIMITS[PEPPER]
        working_limit = 64
        unwind_start = 46
        danger_start = 68
        trend_per_step = 0.1002
        base_inventory_target = 12

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
        day_anchor = 0.84 * day_anchor + 0.16 * anchor_measurement
        trend_fair = day_anchor + trend_per_step * step_index

        expected_step_move = (
            trend_per_step * max(1, step_index - prev_step)
            if step_index >= prev_step
            else trend_per_step
        )
        residual_move = (mid - prev_mid) - expected_step_move
        signal_strength = (
            0.82 * (microprice - mid)
            + 2.65 * imbalance
            - 0.50 * residual_move
            + 0.22
        )

        measurement = trend_fair + signal_strength
        fair_value = 0.62 * prev_fair + 0.38 * measurement

        inventory_target = base_inventory_target
        if signal_strength > 1.5:
            inventory_target += 8
        elif signal_strength > 0.8:
            inventory_target += 4
        elif signal_strength < -1.4:
            inventory_target -= 8
        elif signal_strength < -0.8:
            inventory_target -= 4
        inventory_target = max(2, min(24, inventory_target))

        target_gap = position - inventory_target
        inventory_ratio = target_gap / limit
        inventory_pressure = target_gap / working_limit
        reservation_price = fair_value - 2.15 * inventory_ratio

        buy_capacity = max(0, min(limit - position, working_limit - target_gap))
        sell_capacity = max(0, min(limit + position, working_limit + target_gap))

        buy_take_threshold = 2.4 + max(0.0, inventory_pressure) * 1.8
        sell_take_threshold = 4.3 + max(0.0, -inventory_pressure) * 2.0

        if imbalance > 0.40:
            buy_take_threshold -= 0.7
        if imbalance < -0.40:
            sell_take_threshold -= 0.5

        if position <= -unwind_start:
            buy_take_threshold -= 1.2
        if position >= unwind_start:
            sell_take_threshold -= 1.4

        buy_taken = 0
        for ask_price, ask_volume in sorted(depth.sell_orders.items()):
            available = abs(ask_volume)
            if available <= 0 or buy_capacity <= 0:
                continue

            edge = reservation_price - ask_price
            strong_up_signal = imbalance > 0.72 and signal_strength > 1.5
            should_take = edge >= buy_take_threshold
            should_take = should_take or (
                ask_price <= math.floor(trend_fair) and signal_strength > 0.5
            )
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
            should_take = should_take or (
                position >= danger_start and bid_price >= best_bid - 1
            )
            should_take = should_take or (
                position > inventory_target + 18 and bid_price >= math.ceil(trend_fair)
            )

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
        reservation_price = fair_value - 2.15 * inventory_ratio

        bid_edge = 1.25
        ask_edge = 2.45
        if spread <= 8:
            bid_edge = 0.9
            ask_edge = 1.7
        elif spread >= 16:
            bid_edge = 1.55
            ask_edge = 2.8

        if signal_strength > 0.9:
            bid_edge -= 0.35
            ask_edge += 0.25
        elif signal_strength < -0.9:
            bid_edge += 0.35
            ask_edge -= 0.45

        if position < inventory_target:
            bid_edge -= 0.25
            ask_edge += 0.15
        elif position > inventory_target:
            bid_edge += 0.2
            ask_edge -= 0.3

        if position >= unwind_start:
            bid_edge += 0.55
            ask_edge -= 0.65
        elif position <= -unwind_start:
            bid_edge -= 0.55
            ask_edge += 0.55

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

        if position < inventory_target:
            front_buy = min(buy_capacity, front_buy + 2)
            front_sell = max(0, front_sell - 1)
        elif position > inventory_target + 8:
            front_buy = max(0, front_buy - 1)
            front_sell = min(sell_capacity, front_sell + 2)

        if front_buy > 0:
            orders.append(Order(PEPPER, passive_bid, front_buy))
        if front_sell > 0:
            orders.append(Order(PEPPER, passive_ask, -front_sell))

        residual_buy = max(0, buy_capacity - front_buy)
        residual_sell = max(0, sell_capacity - front_sell)

        if residual_buy > 0 and position < inventory_target + 22 and passive_bid - 1 > 0:
            second_buy = min(residual_buy, max(2, (front_buy + 1) // 2))
            if second_buy > 0:
                orders.append(Order(PEPPER, passive_bid - 1, second_buy))

        if residual_sell > 0 and position > inventory_target - 14:
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
