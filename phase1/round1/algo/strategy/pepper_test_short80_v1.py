from typing import Dict, List

from datamodel import Order, TradingState


PEPPER = "INTARIAN_PEPPER_ROOT"
POSITION_LIMIT = 80


class Trader:
    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}

        depth = state.order_depths.get(PEPPER)
        if depth is None or not depth.buy_orders:
            return result, 0, ""

        position = state.position.get(PEPPER, 0)
        remaining = max(0, POSITION_LIMIT + position)
        if remaining <= 0:
            return result, 0, ""

        orders: List[Order] = []
        for bid_price, bid_volume in sorted(depth.buy_orders.items(), reverse=True):
            available = max(0, bid_volume)
            if available <= 0 or remaining <= 0:
                continue

            clip = min(available, remaining)
            orders.append(Order(PEPPER, bid_price, -clip))
            remaining -= clip

            if remaining <= 0:
                break

        result[PEPPER] = orders
        return result, 0, ""
