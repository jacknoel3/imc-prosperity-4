from typing import Dict, List

from datamodel import Order, TradingState


PEPPER = "INTARIAN_PEPPER_ROOT"
POSITION_LIMIT = 80
BLOCK_SIZE = 50000


class Trader:
    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}

        depth = state.order_depths.get(PEPPER)
        if depth is None:
            return result, 0, ""

        block_index = int(state.timestamp) // BLOCK_SIZE
        target_position = POSITION_LIMIT if block_index % 2 == 0 else -POSITION_LIMIT
        position = state.position.get(PEPPER, 0)
        delta = target_position - position
        orders: List[Order] = []

        if delta > 0 and depth.sell_orders:
            remaining = delta
            for ask_price, ask_volume in sorted(depth.sell_orders.items()):
                available = max(0, -ask_volume)
                if available <= 0 or remaining <= 0:
                    continue

                clip = min(available, remaining)
                orders.append(Order(PEPPER, ask_price, clip))
                remaining -= clip

                if remaining <= 0:
                    break

        elif delta < 0 and depth.buy_orders:
            remaining = -delta
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
