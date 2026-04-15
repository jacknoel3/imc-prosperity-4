from datamodel import Order, OrderDepth, TradingState
from typing import Dict, List, Optional, Tuple
import json
import math


TOMATOES = "TOMATOES"
TOMATOES_LIMIT = 80
TOMATOES_SAFE_LIMIT = 54
TOMATOES_BALANCED_LIMIT = 66
TOMATOES_TREND_LIMIT = 74


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
        move_var_prev = self._as_float(prev_state.get("move_var"), 1.0)
        regime_score_prev = self._as_float(prev_state.get("regime_score"), 0.0)

        ema_fast = 0.42 * mid_price + 0.58 * ema_fast_prev
        ema_slow = 0.10 * mid_price + 0.90 * ema_slow_prev
        trend_signal = ema_fast - ema_slow
        last_move = mid_price - prev_mid
        move_var = 0.18 * (last_move * last_move) + 0.82 * move_var_prev
        move_sigma = math.sqrt(max(move_var, 0.25))

        continuation_score = abs(trend_signal) / move_sigma
        imbalance_score = abs(imbalance) * 4.0
        momentum_score = abs(last_move) / move_sigma
        regime_score = 0.55 * regime_score_prev + 0.45 * max(continuation_score, imbalance_score, momentum_score)

        directional_up = trend_signal > 0 and imbalance > 0
        directional_down = trend_signal < 0 and imbalance < 0

        if regime_score > 1.6 and (directional_up or directional_down):
            regime = "trend"
        elif regime_score < 0.95 and spread >= 8:
            regime = "mean_revert"
        else:
            regime = "balanced"

        if regime == "trend":
            measurement = (
                mid_price
                + 0.80 * (microprice - mid_price)
                + 2.60 * imbalance
                + 0.20 * last_move
                + 0.40 * trend_signal
            )
            fair_alpha = 0.46
            working_limit = TOMATOES_TREND_LIMIT
            inv_skew = 1.9
            quote_edge = 1.0 if spread <= 8 else 1.5
            base_take = 0.9
            max_clip = 18
        elif regime == "mean_revert":
            measurement = (
                mid_price
                + 0.45 * (microprice - mid_price)
                + 1.70 * imbalance
                - 0.85 * last_move
                + 0.08 * trend_signal
            )
            fair_alpha = 0.24
            working_limit = TOMATOES_SAFE_LIMIT
            inv_skew = 3.5
            quote_edge = 2.5 if spread >= 8 else 2.0
            base_take = 2.0
            max_clip = 8
        else:
            measurement = (
                mid_price
                + 0.62 * (microprice - mid_price)
                + 2.20 * imbalance
                - 0.35 * last_move
                + 0.18 * trend_signal
            )
            fair_alpha = 0.34
            working_limit = TOMATOES_BALANCED_LIMIT
            inv_skew = 2.6
            quote_edge = 1.5 if spread <= 8 else 2.0
            base_take = 1.4
            max_clip = 12

        fair_value = prev_fair + fair_alpha * (measurement - prev_fair)
        inventory_ratio = position / TOMATOES_LIMIT
        reservation_price = fair_value - inv_skew * inventory_ratio

        buy_capacity = min(TOMATOES_LIMIT - position, working_limit - position)
        sell_capacity = min(TOMATOES_LIMIT + position, working_limit + position)
        buy_capacity = max(0, buy_capacity)
        sell_capacity = max(0, sell_capacity)

        take_buy_threshold = base_take + max(0.0, inventory_ratio) * 1.4
        take_sell_threshold = base_take + max(0.0, -inventory_ratio) * 1.4

        if regime == "trend" and directional_up:
            take_buy_threshold -= 0.45
            take_sell_threshold += 0.55
        if regime == "trend" and directional_down:
            take_sell_threshold -= 0.45
            take_buy_threshold += 0.55

        buy_taken = 0
        for ask_price, ask_volume in sorted(depth.sell_orders.items()):
            available = abs(ask_volume)
            if available <= 0 or buy_capacity <= 0:
                continue

            edge = reservation_price - ask_price
            if edge < take_buy_threshold and not (regime == "trend" and directional_up):
                break

            clip = min(available, buy_capacity, max_clip)
            if regime == "mean_revert" and position > 20:
                clip = min(clip, 3)
            if clip > 0:
                orders.append(Order(TOMATOES, ask_price, clip))
                position += clip
                buy_capacity -= clip
                sell_capacity = min(TOMATOES_LIMIT + position, working_limit + position)
                sell_capacity = max(0, sell_capacity)
                buy_taken += clip

        sell_taken = 0
        for bid_price, bid_volume in sorted(depth.buy_orders.items(), reverse=True):
            available = bid_volume
            if available <= 0 or sell_capacity <= 0:
                continue

            edge = bid_price - reservation_price
            if edge < take_sell_threshold and not (regime == "trend" and directional_down):
                break

            clip = min(available, sell_capacity, max_clip)
            if regime == "mean_revert" and position < -20:
                clip = min(clip, 3)
            if clip > 0:
                orders.append(Order(TOMATOES, bid_price, -clip))
                position -= clip
                sell_capacity -= clip
                buy_capacity = min(TOMATOES_LIMIT - position, working_limit - position)
                buy_capacity = max(0, buy_capacity)
                sell_taken += clip

        inventory_ratio = position / TOMATOES_LIMIT
        reservation_price = fair_value - inv_skew * inventory_ratio

        if regime == "mean_revert":
            unwind_start = 28
        elif regime == "balanced":
            unwind_start = 46
        else:
            unwind_start = 62

        if position >= unwind_start and (regime != "trend" or directional_down):
            unwind_qty = min(sell_capacity, max(5, min(14, position - (unwind_start - 8))))
            if unwind_qty > 0:
                orders.append(Order(TOMATOES, best_bid, -unwind_qty))
                position -= unwind_qty

        if position <= -unwind_start and (regime != "trend" or directional_up):
            unwind_qty = min(buy_capacity, max(5, min(14, -position - (unwind_start - 8))))
            if unwind_qty > 0:
                orders.append(Order(TOMATOES, best_ask, unwind_qty))
                position += unwind_qty

        inventory_ratio = position / TOMATOES_LIMIT
        reservation_price = fair_value - inv_skew * inventory_ratio
        buy_capacity = min(TOMATOES_LIMIT - position, working_limit - position)
        sell_capacity = min(TOMATOES_LIMIT + position, working_limit + position)
        buy_capacity = max(0, buy_capacity)
        sell_capacity = max(0, sell_capacity)

        target_bid = int(math.floor(reservation_price - quote_edge))
        target_ask = int(math.ceil(reservation_price + quote_edge))

        passive_bid = min(best_bid + 1, target_bid)
        passive_ask = max(best_ask - 1, target_ask)

        if regime == "trend" and directional_up:
            passive_bid = min(best_ask - 1, max(passive_bid, best_bid + 1))
        if regime == "trend" and directional_down:
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
            regime=regime,
            same_side=True,
            took_liquidity=buy_taken > 0,
        )
        front_sell = self._quote_size(
            capacity=sell_capacity,
            inventory_ratio=inventory_ratio,
            regime=regime,
            same_side=False,
            took_liquidity=sell_taken > 0,
        )

        if front_buy > 0:
            orders.append(Order(TOMATOES, passive_bid, front_buy))
        if front_sell > 0:
            orders.append(Order(TOMATOES, passive_ask, -front_sell))

        residual_buy = max(0, buy_capacity - front_buy)
        residual_sell = max(0, sell_capacity - front_sell)

        if residual_buy > 0 and passive_bid - 1 > 0 and inventory_ratio < 0.55:
            second_buy = min(residual_buy, max(2, front_buy // 2))
            if second_buy > 0:
                orders.append(Order(TOMATOES, passive_bid - 1, second_buy))
        if residual_sell > 0 and inventory_ratio > -0.55:
            second_sell = min(residual_sell, max(2, front_sell // 2))
            if second_sell > 0:
                orders.append(Order(TOMATOES, passive_ask + 1, -second_sell))

        new_state = {
            "fair_value": fair_value,
            "last_mid": mid_price,
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "move_var": move_var,
            "regime_score": regime_score,
        }
        return orders, new_state

    def _quote_size(
        self,
        capacity: int,
        inventory_ratio: float,
        regime: str,
        same_side: bool,
        took_liquidity: bool,
    ) -> int:
        if capacity <= 0:
            return 0

        pressure = inventory_ratio if same_side else -inventory_ratio
        if regime == "trend":
            scale = 0.50 if took_liquidity else 0.58
            scale -= max(0.0, pressure) * 0.20
            low, high, cap = 3, 16, 0.72
        elif regime == "mean_revert":
            scale = 0.24 if took_liquidity else 0.30
            scale -= max(0.0, pressure) * 0.36
            low, high, cap = 1, 8, 0.38
        else:
            scale = 0.34 if took_liquidity else 0.42
            scale -= max(0.0, pressure) * 0.28
            low, high, cap = 2, 12, 0.52

        scale = max(0.08, min(cap, scale))
        size = int(capacity * scale)
        size = max(low, min(high, size))
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
