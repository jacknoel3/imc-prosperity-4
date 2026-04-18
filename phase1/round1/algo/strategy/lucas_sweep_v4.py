import json
import math
from typing import Dict, List, Tuple

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

        best_bid, best_bid_volume, best_ask, best_ask_volume, mid, _, imbalance, microprice = book

        level = self._as_float(prev_state.get("level"), mid)
        trend = self._as_float(prev_state.get("trend"), 0.0)
        prev_mid = self._as_float(prev_state.get("last_mid"), mid)

        if prev_state:
            prev_level = level
            level = 0.08 * mid + 0.92 * (level + trend)
            trend = 0.12 * (level - prev_level) + 0.88 * trend

        fair_value = level + trend
        residual = mid - fair_value
        micro_edge = microprice - mid

        core_target = 80
        buy_signal = residual <= -5.0 + 2.5 * min(0.0, imbalance) + 0.8 * min(0.0, micro_edge)

        if timestamp <= 300 and position < 80:
            for ask_price, ask_volume in sorted(depth.sell_orders.items()):
                available = -ask_volume
                qty = min(80 - position, available)
                if qty <= 0:
                    break
                orders.append(Order(PEPPER, ask_price, qty))
                position += qty
                if position >= 80:
                    break

        if position < core_target and (buy_signal or timestamp <= 300 or best_ask <= int(fair_value)):
            qty = min(core_target - position, best_ask_volume, 12)
            if qty > 0:
                orders.append(Order(PEPPER, best_ask, qty))
                position += qty

        if position < core_target:
            passive_bid = best_bid + 1
            qty = min(12, core_target - position, 80 - position)
            if qty > 0 and passive_bid < best_ask:
                orders.append(Order(PEPPER, passive_bid, qty))

        new_state = {
            "level": level,
            "trend": trend,
            "last_mid": mid,
            "last_move": mid - prev_mid,
        }
        return orders, new_state

    def _book_snapshot(self, depth: OrderDepth):
        if not depth.buy_orders or not depth.sell_orders:
            return None

        best_bid = max(depth.buy_orders)
        best_ask = min(depth.sell_orders)
        best_bid_volume = depth.buy_orders[best_bid]
        best_ask_volume = -depth.sell_orders[best_ask]
        mid = (best_bid + best_ask) / 2.0
        spread = best_ask - best_bid

        total_bid = sum(depth.buy_orders.values())
        total_ask = sum(-volume for volume in depth.sell_orders.values())
        imbalance = 0.0
        if total_bid + total_ask > 0:
            imbalance = (total_bid - total_ask) / (total_bid + total_ask)

        microprice = (
            best_ask * best_bid_volume + best_bid * best_ask_volume
        ) / max(1, best_bid_volume + best_ask_volume)

        return best_bid, best_bid_volume, best_ask, best_ask_volume, mid, spread, imbalance, microprice

    def _sanitize_quotes(self, bid: int, ask: int, best_bid: int, best_ask: int) -> Tuple[int, int]:
        if bid >= best_ask:
            bid = best_ask - 1
        if ask <= best_bid:
            ask = best_bid + 1
        if bid >= ask:
            bid = min(bid, ask - 1)
            ask = max(ask, bid + 1)
        return bid, ask

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

        gap = position - target_position
        pressure = gap / limit
        if side == "buy":
            scale = 0.95 - max(0.0, pressure) * 0.75
        else:
            scale = 0.95 - max(0.0, -pressure) * 0.75

        if took_liquidity:
            scale *= 0.75

        scale = max(0.20, min(1.0, scale))
        return max(1, int(capacity * scale))

    def _take_clip(self, position: int, buy_side: bool, limit: int) -> int:
        if buy_side:
            if position <= -68:
                return 18
            if position <= -48:
                return 14
            if position <= -24:
                return 10
            if position >= 48:
                return 4
            return 8

        if position >= 68:
            return 18
        if position >= 48:
            return 14
        if position >= 24:
            return 10
        if position <= -48:
            return 4
        return 8

    def _load_state(self, trader_data: str):
        try:
            data = json.loads(trader_data) if trader_data else {}
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _as_float(self, value, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
