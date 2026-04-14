from datamodel import Order, OrderDepth, TradingState
from typing import Dict, List, Optional, Tuple
import json
import math


TOMATOES = "TOMATOES"
TOMATOES_LIMIT = 35


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
        if total_top_volume > 0:
            imbalance = (bid_volume - ask_volume) / total_top_volume

        microprice = mid_price
        if total_top_volume > 0:
            microprice = (best_ask * bid_volume + best_bid * ask_volume) / total_top_volume

        prev_fair = self._as_float(prev_state.get("fair_value"), mid_price)
        prev_mid = self._as_float(prev_state.get("last_mid"), mid_price)
        ema_fast_prev = self._as_float(prev_state.get("ema_fast"), mid_price)
        ema_slow_prev = self._as_float(prev_state.get("ema_slow"), mid_price)

        ema_fast = 0.45 * mid_price + 0.55 * ema_fast_prev
        ema_slow = 0.12 * mid_price + 0.88 * ema_slow_prev
        trend_signal = ema_fast - ema_slow
        last_move = mid_price - prev_mid

        # Round 0 EDA suggests:
        # - positive predictive power from imbalance/microprice
        # - strong negative lag-1 autocorrelation in dmid
        measurement = (
            mid_price
            + 0.65 * (microprice - mid_price)
            + 2.40 * imbalance
            - 0.55 * last_move
            + 0.20 * trend_signal
        )

        # Random-walk fair value: retain prior belief, but let the latent price
        # drift toward the latest book-informed measurement.
        fair_value = prev_fair + 0.35 * (measurement - prev_fair)

        inventory_ratio = position / TOMATOES_LIMIT
        reservation_price = fair_value - 2.60 * inventory_ratio

        buy_capacity = TOMATOES_LIMIT - position
        sell_capacity = TOMATOES_LIMIT + position

        signal_strength = measurement - fair_value
        take_buy_threshold = max(1.0, 1.8 - 3.0 * max(0.0, signal_strength))
        take_sell_threshold = max(1.0, 1.8 + 3.0 * min(0.0, signal_strength))
        take_buy_threshold += max(0.0, inventory_ratio) * 1.4
        take_sell_threshold += max(0.0, -inventory_ratio) * 1.4

        buy_taken = 0
        for ask_price, ask_volume in sorted(depth.sell_orders.items()):
            available = abs(ask_volume)
            if available <= 0 or buy_capacity <= 0:
                continue

            edge = reservation_price - ask_price
            if edge < take_buy_threshold:
                break

            clip = min(available, buy_capacity)
            clip = min(clip, self._take_clip(clip, position, buy_side=True))
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
            if edge < take_sell_threshold:
                break

            clip = min(available, sell_capacity)
            clip = min(clip, self._take_clip(clip, position, buy_side=False))
            if clip > 0:
                orders.append(Order(TOMATOES, bid_price, -clip))
                position -= clip
                sell_capacity -= clip
                buy_capacity = TOMATOES_LIMIT - position
                sell_taken += clip

        inventory_ratio = position / TOMATOES_LIMIT
        reservation_price = fair_value - 2.60 * inventory_ratio

        # If inventory is stretched, prioritize flattening at the front of book.
        if position >= 26 and best_bid >= math.floor(fair_value) - 1 and sell_capacity > 0:
            unwind_qty = min(sell_capacity, max(4, position - 20))
            orders.append(Order(TOMATOES, best_bid, -unwind_qty))
            position -= unwind_qty
            sell_capacity -= unwind_qty
            buy_capacity = TOMATOES_LIMIT - position

        if position <= -26 and best_ask <= math.ceil(fair_value) + 1 and buy_capacity > 0:
            unwind_qty = min(buy_capacity, max(4, -position - 20))
            orders.append(Order(TOMATOES, best_ask, unwind_qty))
            position += unwind_qty
            buy_capacity -= unwind_qty
            sell_capacity = TOMATOES_LIMIT + position

        inventory_ratio = position / TOMATOES_LIMIT
        reservation_price = fair_value - 2.60 * inventory_ratio

        quote_edge = 2.0
        if spread <= 4:
            quote_edge = 1.0
        elif spread <= 8:
            quote_edge = 1.5
        elif spread >= 14:
            quote_edge = 2.5

        if abs(inventory_ratio) > 0.55:
            quote_edge += 0.5

        target_bid = int(math.floor(reservation_price - quote_edge))
        target_ask = int(math.ceil(reservation_price + quote_edge))

        passive_bid = min(best_bid + 1, target_bid)
        passive_ask = max(best_ask - 1, target_ask)

        if spread <= 2:
            passive_bid = min(best_bid, target_bid)
            passive_ask = max(best_ask, target_ask)

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
            leaning_with_inventory=True,
            took_liquidity=buy_taken > 0,
        )
        front_sell = self._quote_size(
            capacity=sell_capacity,
            inventory_ratio=inventory_ratio,
            leaning_with_inventory=False,
            took_liquidity=sell_taken > 0,
        )

        if front_buy > 0:
            orders.append(Order(TOMATOES, passive_bid, front_buy))
        if front_sell > 0:
            orders.append(Order(TOMATOES, passive_ask, -front_sell))

        # Second layer: keep presence deeper in the book for extra fills,
        # but avoid adding too much size on the inventory-heavy side.
        residual_buy = max(0, buy_capacity - front_buy)
        residual_sell = max(0, sell_capacity - front_sell)

        if residual_buy > 0 and inventory_ratio < 0.30 and passive_bid - 1 > 0:
            second_buy = min(residual_buy, max(2, front_buy // 2))
            if second_buy > 0:
                orders.append(Order(TOMATOES, passive_bid - 1, second_buy))

        if residual_sell > 0 and inventory_ratio > -0.30:
            second_sell = min(residual_sell, max(2, front_sell // 2))
            if second_sell > 0:
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
        leaning_with_inventory: bool,
        took_liquidity: bool,
    ) -> int:
        if capacity <= 0:
            return 0

        pressure = inventory_ratio if leaning_with_inventory else -inventory_ratio
        scale = 0.34 if took_liquidity else 0.44
        scale -= max(0.0, pressure) * 0.30
        scale = max(0.14, min(0.55, scale))

        size = int(capacity * scale)
        size = max(2, min(12, size))
        return min(size, capacity)

    def _take_clip(self, capacity: int, position: int, buy_side: bool) -> int:
        if capacity <= 0:
            return 0

        if buy_side and position > 20:
            return min(capacity, 4)
        if (not buy_side) and position < -20:
            return min(capacity, 4)
        return min(capacity, 10)

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
