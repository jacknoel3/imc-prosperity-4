import json
import math
from typing import Dict, List, Optional, Tuple

from datamodel import Order, OrderDepth, TradingState


ASH = "ASH_COATED_OSMIUM"
LIMIT = 80


class Trader:
    def run(self, state: TradingState):
        trader_state = self._load_state(state.traderData)
        ash_state = trader_state.get(ASH, {})
        result: Dict[str, List[Order]] = {}

        for product, depth in state.order_depths.items():
            if product == ASH:
                orders, ash_state = self._trade_ash(
                    depth=depth,
                    position=state.position.get(ASH, 0),
                    prev_state=ash_state,
                    timestamp=int(state.timestamp),
                )
                result[product] = orders
            else:
                result[product] = []

        new_state = json.dumps({ASH: ash_state}, separators=(",", ":"))
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

        working_limit = 64
        unwind_start = 50
        danger_start = 68

        prev_mid = self._as_float(prev_state.get("last_mid"), mid)
        prev_fair = self._as_float(prev_state.get("fair_value"), 10000.0)
        ema_fast_prev = self._as_float(prev_state.get("ema_fast"), mid)
        ema_slow_prev = self._as_float(prev_state.get("ema_slow"), mid)

        last_move = mid - prev_mid
        ema_fast = 0.40 * mid + 0.60 * ema_fast_prev
        ema_slow = 0.10 * mid + 0.90 * ema_slow_prev
        trend_signal = ema_fast - ema_slow

        # ASH behaves more like a noisy wandering fair than a clean mean-reverter.
        measurement = (
            10000.0
            + 0.52 * (mid - 10000.0)
            + 0.85 * (microprice - mid)
            + 2.10 * imbalance
            - 0.55 * last_move
            + 0.08 * trend_signal
        )
        fair_value = 0.82 * prev_fair + 0.18 * measurement

        inventory_ratio = position / LIMIT
        reservation_price = fair_value - 3.30 * inventory_ratio

        buy_capacity = max(0, min(LIMIT - position, working_limit - position))
        sell_capacity = max(0, min(LIMIT + position, working_limit + position))

        signal_strength = measurement - fair_value
        buy_take_threshold = 2.9 + max(0.0, inventory_ratio) * 2.3
        sell_take_threshold = 2.9 + max(0.0, -inventory_ratio) * 2.3

        if imbalance > 0.45:
            buy_take_threshold -= 0.8
        if imbalance < -0.45:
            sell_take_threshold -= 0.8
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
                imbalance > 0.72 and ask_price <= math.floor(fair_value) and signal_strength > 0.7
            )
            should_take = should_take or (
                position <= -danger_start and ask_price <= best_ask + 1
            )
            if not should_take:
                break

            clip = min(available, buy_capacity, self._take_clip(position, True))
            if clip <= 0:
                continue
            orders.append(Order(ASH, ask_price, clip))
            position += clip
            buy_capacity -= clip
            sell_capacity = max(0, min(LIMIT + position, working_limit + position))
            buy_taken += clip

        sell_taken = 0
        for bid_price, bid_volume in sorted(depth.buy_orders.items(), reverse=True):
            available = bid_volume
            if available <= 0 or sell_capacity <= 0:
                continue

            edge = bid_price - reservation_price
            should_take = edge >= sell_take_threshold
            should_take = should_take or (
                imbalance < -0.72 and bid_price >= math.ceil(fair_value) and signal_strength < -0.7
            )
            should_take = should_take or (
                position >= danger_start and bid_price >= best_bid - 1
            )
            if not should_take:
                break

            clip = min(available, sell_capacity, self._take_clip(position, False))
            if clip <= 0:
                continue
            orders.append(Order(ASH, bid_price, -clip))
            position -= clip
            sell_capacity -= clip
            buy_capacity = max(0, min(LIMIT - position, working_limit - position))
            sell_taken += clip

        inventory_ratio = position / LIMIT
        reservation_price = fair_value - 3.30 * inventory_ratio

        quote_half_spread = 2.1
        if spread <= 10:
            quote_half_spread = 1.6
        elif spread >= 18:
            quote_half_spread = 2.7

        if abs(inventory_ratio) > 0.45:
            quote_half_spread += 0.4
        if abs(signal_strength) > 1.7:
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
            side="buy",
            took_liquidity=buy_taken > 0,
        )
        front_sell = self._quote_size(
            capacity=sell_capacity,
            position=position,
            side="sell",
            took_liquidity=sell_taken > 0,
        )

        if front_buy > 0:
            orders.append(Order(ASH, passive_bid, front_buy))
        if front_sell > 0:
            orders.append(Order(ASH, passive_ask, -front_sell))

        residual_buy = max(0, buy_capacity - front_buy)
        residual_sell = max(0, sell_capacity - front_sell)

        if residual_buy > 0 and position < 18 and passive_bid - 1 > 0:
            second_buy = min(residual_buy, max(2, front_buy // 2))
            if second_buy > 0:
                orders.append(Order(ASH, passive_bid - 1, second_buy))

        if residual_sell > 0 and position > -18:
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

    def _book_snapshot(
        self, depth: OrderDepth
    ) -> Optional[Tuple[int, int, int, int, float, int, float, float]]:
        if not depth.buy_orders or not depth.sell_orders:
            return None

        best_bid = max(depth.buy_orders)
        best_ask = min(depth.sell_orders)
        best_bid_volume = depth.buy_orders[best_bid]
        best_ask_volume = abs(depth.sell_orders[best_ask])
        mid = (best_bid + best_ask) / 2.0
        spread = best_ask - best_bid

        total_bid = sum(max(0, volume) for volume in depth.buy_orders.values())
        total_ask = sum(abs(volume) for volume in depth.sell_orders.values())
        denom = total_bid + total_ask
        imbalance = (total_bid - total_ask) / denom if denom > 0 else 0.0

        top_denom = best_bid_volume + best_ask_volume
        microprice = (
            (best_ask * best_bid_volume + best_bid * best_ask_volume) / top_denom
            if top_denom > 0
            else mid
        )
        return (
            best_bid,
            best_bid_volume,
            best_ask,
            best_ask_volume,
            mid,
            spread,
            imbalance,
            microprice,
        )

    def _take_clip(self, position: int, is_buy: bool) -> int:
        if is_buy:
            if position <= -55:
                return 22
            if position < 0:
                return 14
            if position < 35:
                return 9
            return 6
        if position >= 55:
            return 22
        if position > 0:
            return 14
        if position > -35:
            return 9
        return 6

    def _quote_size(self, capacity: int, position: int, side: str, took_liquidity: bool) -> int:
        if capacity <= 0:
            return 0

        base = 11
        if took_liquidity:
            base -= 2

        if side == "buy":
            if position < -45:
                base += 6
            elif position > 35:
                base -= 4
        else:
            if position > 45:
                base += 6
            elif position < -35:
                base -= 4

        return max(0, min(capacity, base))

    def _sanitize_quotes(self, bid: int, ask: int, best_bid: int, best_ask: int) -> Tuple[int, int]:
        bid = max(1, bid)
        ask = max(bid + 1, ask)
        if bid >= best_ask:
            bid = best_ask - 1
        if ask <= best_bid:
            ask = best_bid + 1
        if ask <= bid:
            ask = bid + 1
        return bid, ask

    def _load_state(self, trader_data: str) -> Dict[str, Dict[str, float]]:
        if not trader_data:
            return {}
        try:
            parsed = json.loads(trader_data)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        return {}

    def _as_float(self, value: object, default: float) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        return default
