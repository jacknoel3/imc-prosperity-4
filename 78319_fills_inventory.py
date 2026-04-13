from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Tuple
import json
import math


EMERALDS_FV = 10_000
EMERALDS_LIMIT = 20
TOMATOES_LIMIT = 35


class Trader:
    def run(self, state: TradingState):
        try:
            trader_state = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            trader_state = {}

        tomatoes_ema = trader_state.get("tomatoes_ema")
        result: Dict[str, List[Order]] = {}

        for product, depth in state.order_depths.items():
            position = state.position.get(product, 0)

            if product == "EMERALDS":
                result[product] = self._trade_emeralds(depth, position)
            elif product == "TOMATOES":
                orders, tomatoes_ema = self._trade_tomatoes(depth, position, tomatoes_ema)
                result[product] = orders
            else:
                result[product] = []

        trader_data = json.dumps({"tomatoes_ema": tomatoes_ema})
        return result, 0, trader_data

    def _trade_emeralds(self, depth: OrderDepth, position: int) -> List[Order]:
        orders: List[Order] = []
        buy_capacity = EMERALDS_LIMIT - position
        sell_capacity = EMERALDS_LIMIT + position

        best_bid = max(depth.buy_orders) if depth.buy_orders else None
        best_ask = min(depth.sell_orders) if depth.sell_orders else None

        for ask_price, ask_volume in sorted(depth.sell_orders.items()):
            if ask_price < EMERALDS_FV and buy_capacity > 0:
                qty = min(-ask_volume, buy_capacity)
                if qty > 0:
                    orders.append(Order("EMERALDS", ask_price, qty))
                    position += qty
                    buy_capacity -= qty
                    sell_capacity = EMERALDS_LIMIT + position

        for bid_price, bid_volume in sorted(depth.buy_orders.items(), reverse=True):
            if bid_price > EMERALDS_FV and sell_capacity > 0:
                qty = min(bid_volume, sell_capacity)
                if qty > 0:
                    orders.append(Order("EMERALDS", bid_price, -qty))
                    position -= qty
                    sell_capacity -= qty
                    buy_capacity = EMERALDS_LIMIT - position

        if best_ask == EMERALDS_FV and position < 0 and buy_capacity > 0:
            qty = min(-depth.sell_orders[best_ask], min(4, buy_capacity, -position))
            if qty > 0:
                orders.append(Order("EMERALDS", best_ask, qty))
                position += qty
                buy_capacity -= qty
                sell_capacity = EMERALDS_LIMIT + position

        if best_bid == EMERALDS_FV and position > 0 and sell_capacity > 0:
            qty = min(depth.buy_orders[best_bid], min(4, sell_capacity, position))
            if qty > 0:
                orders.append(Order("EMERALDS", best_bid, -qty))
                position -= qty
                sell_capacity -= qty
                buy_capacity = EMERALDS_LIMIT - position

        if best_bid is None or best_ask is None:
            return orders

        inventory_ratio = position / EMERALDS_LIMIT
        skew = int(round(inventory_ratio * 2))

        target_bid = EMERALDS_FV - 2 - skew
        target_ask = EMERALDS_FV + 2 - skew

        passive_bid = min(best_bid + 1, target_bid)
        passive_ask = max(best_ask - 1, target_ask)

        if passive_bid >= passive_ask:
            passive_bid = min(passive_bid, passive_ask - 1)
            passive_ask = max(passive_ask, passive_bid + 1)

        buy_size = self._scaled_size(buy_capacity, inventory_ratio, same_side=True)
        sell_size = self._scaled_size(sell_capacity, inventory_ratio, same_side=False)

        if buy_size > 0:
            orders.append(Order("EMERALDS", passive_bid, buy_size))
        if sell_size > 0:
            orders.append(Order("EMERALDS", passive_ask, -sell_size))

        return orders

    def _trade_tomatoes(
        self, depth: OrderDepth, position: int, ema: float | None
    ) -> Tuple[List[Order], float | None]:
        orders: List[Order] = []

        if not depth.buy_orders or not depth.sell_orders:
            return orders, ema

        best_bid = max(depth.buy_orders)
        best_ask = min(depth.sell_orders)
        mid_price = (best_bid + best_ask) / 2.0

        if ema is None:
            ema = mid_price
        else:
            ema = 0.30 * mid_price + 0.70 * ema

        spread = best_ask - best_bid
        inventory_ratio = position / TOMATOES_LIMIT
        fair_value = ema - (inventory_ratio * 2.0)

        buy_capacity = TOMATOES_LIMIT - position
        sell_capacity = TOMATOES_LIMIT + position

        take_buy_edge = 1.0 + max(0.0, inventory_ratio) * 0.75
        take_sell_edge = 1.0 + max(0.0, -inventory_ratio) * 0.75

        for ask_price, ask_volume in sorted(depth.sell_orders.items()):
            if ask_price <= fair_value - take_buy_edge and buy_capacity > 0:
                qty = min(-ask_volume, buy_capacity)
                if qty > 0:
                    orders.append(Order("TOMATOES", ask_price, qty))
                    position += qty
                    buy_capacity -= qty
                    sell_capacity = TOMATOES_LIMIT + position

        for bid_price, bid_volume in sorted(depth.buy_orders.items(), reverse=True):
            if bid_price >= fair_value + take_sell_edge and sell_capacity > 0:
                qty = min(bid_volume, sell_capacity)
                if qty > 0:
                    orders.append(Order("TOMATOES", bid_price, -qty))
                    position -= qty
                    sell_capacity -= qty
                    buy_capacity = TOMATOES_LIMIT - position

        inventory_ratio = position / TOMATOES_LIMIT
        fair_value = ema - (inventory_ratio * 2.0)

        base_edge = 2 if spread <= 6 else 3
        if abs(inventory_ratio) > 0.65:
            base_edge += 1

        target_bid = math.floor(fair_value) - base_edge
        target_ask = math.ceil(fair_value) + base_edge

        passive_bid = min(best_bid + 1, target_bid)
        passive_ask = max(best_ask - 1, target_ask)

        if passive_bid >= best_ask:
            passive_bid = best_ask - 1
        if passive_ask <= best_bid:
            passive_ask = best_bid + 1

        if passive_bid >= passive_ask:
            passive_bid = min(passive_bid, passive_ask - 1)
            passive_ask = max(passive_ask, passive_bid + 1)

        front_buy = self._scaled_size(min(buy_capacity, 16), inventory_ratio, same_side=True)
        front_sell = self._scaled_size(min(sell_capacity, 16), inventory_ratio, same_side=False)

        reserve_buy = max(0, buy_capacity - front_buy)
        reserve_sell = max(0, sell_capacity - front_sell)

        if front_buy > 0:
            orders.append(Order("TOMATOES", passive_bid, front_buy))
        if front_sell > 0:
            orders.append(Order("TOMATOES", passive_ask, -front_sell))

        if reserve_buy > 0 and passive_bid - 1 > 0 and inventory_ratio < 0.35:
            second_buy = min(max(2, reserve_buy // 2), reserve_buy)
            if second_buy > 0:
                orders.append(Order("TOMATOES", passive_bid - 1, second_buy))

        if reserve_sell > 0 and inventory_ratio > -0.35:
            second_sell = min(max(2, reserve_sell // 2), reserve_sell)
            if second_sell > 0:
                orders.append(Order("TOMATOES", passive_ask + 1, -second_sell))

        return orders, ema

    def _scaled_size(self, capacity: int, inventory_ratio: float, same_side: bool) -> int:
        if capacity <= 0:
            return 0

        pressure = inventory_ratio if same_side else -inventory_ratio
        scale = 0.95 - max(0.0, pressure) * 0.75
        scale = max(0.20, min(1.0, scale))
        return max(1, int(capacity * scale))
