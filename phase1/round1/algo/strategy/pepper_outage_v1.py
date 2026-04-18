import json
from typing import Any, Dict, List, Tuple

from datamodel import Order, OrderDepth, TradingState


PRODUCT = "INTARIAN_PEPPER_ROOT"
POSITION_LIMIT = 80

# Outage capture parameters
EDGE = 100
HALF_SPREAD = 6.5

# Inventory protection trigger
IMBALANCE_THRESHOLD = 0.80


class Trader:
    def bid(self) -> int:
        return 15

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        memory = self._load_memory(state.traderData)
        orders: List[Order] = []
        result: Dict[str, List[Order]] = {PRODUCT: orders}

        depth = state.order_depths.get(PRODUCT)
        if depth is None:
            return result, 0, json.dumps(memory, separators=(",", ":"))

        position = state.position.get(PRODUCT, 0)
        best_bid = max(depth.buy_orders) if depth.buy_orders else None
        best_ask = min(depth.sell_orders) if depth.sell_orders else None
        has_bids = best_bid is not None
        has_asks = best_ask is not None

        fair_value, imbalance = self._fair_value_and_imbalance(
            depth=depth,
            memory=memory,
            best_bid=best_bid,
            best_ask=best_ask,
        )
        if fair_value is None:
            return result, 0, json.dumps(memory, separators=(",", ":"))

        buy_cap = max(0, POSITION_LIMIT - position)
        sell_cap = max(0, POSITION_LIMIT + position)

        # Phase 1: attack one-sided books at a wide edge from synthetic fair value.
        if not has_bids or not has_asks:
            if not has_asks and sell_cap > 0 and best_bid is not None:
                price = int(fair_value + EDGE)
                orders.append(Order(PRODUCT, price, -sell_cap))

            if not has_bids and buy_cap > 0 and best_ask is not None:
                price = int(fair_value - EDGE)
                orders.append(Order(PRODUCT, price, buy_cap))

            return result, 0, json.dumps(memory, separators=(",", ":"))

        # Phase 2: flatten vulnerable inventory before the book fully breaks.
        if imbalance > IMBALANCE_THRESHOLD and position < 0:
            flatten_qty = min(abs(position), abs(depth.sell_orders[best_ask]))
            if flatten_qty > 0:
                orders.append(Order(PRODUCT, best_ask, flatten_qty))
            return result, 0, json.dumps(memory, separators=(",", ":"))

        if imbalance < -IMBALANCE_THRESHOLD and position > 0:
            flatten_qty = min(position, depth.buy_orders[best_bid])
            if flatten_qty > 0:
                orders.append(Order(PRODUCT, best_bid, -flatten_qty))
            return result, 0, json.dumps(memory, separators=(",", ":"))

        # Phase 3: rest inside the spread when flat; otherwise only work back to flat.
        spread = best_ask - best_bid
        if position == 0 and spread >= 4:
            if buy_cap >= 2:
                orders.append(Order(PRODUCT, best_ask - 2, 1))
                orders.append(Order(PRODUCT, best_ask - 3, 1))

            if sell_cap >= 2:
                orders.append(Order(PRODUCT, best_bid + 2, -1))
                orders.append(Order(PRODUCT, best_bid + 3, -1))

        elif position > 0:
            exit_price = best_bid + 1 if spread >= 2 else best_bid
            orders.append(Order(PRODUCT, exit_price, -min(position, sell_cap)))

        elif position < 0:
            exit_price = best_ask - 1 if spread >= 2 else best_ask
            orders.append(Order(PRODUCT, exit_price, min(abs(position), buy_cap)))

        return result, 0, json.dumps(memory, separators=(",", ":"))

    def _load_memory(self, trader_data: str) -> Dict[str, Any]:
        try:
            parsed = json.loads(trader_data) if trader_data else {}
            if isinstance(parsed, dict):
                return {"prev_mid": parsed.get("prev_mid")}
        except Exception:
            pass
        return {"prev_mid": None}

    def _fair_value_and_imbalance(
        self,
        depth: OrderDepth,
        memory: Dict[str, Any],
        best_bid: int | None,
        best_ask: int | None,
    ) -> Tuple[float | None, float]:
        if best_bid is not None and best_ask is not None:
            mid = (best_bid + best_ask) / 2.0
            memory["prev_mid"] = mid

            bid_volume = sum(depth.buy_orders.values())
            ask_volume = sum(-volume for volume in depth.sell_orders.values())
            total_volume = bid_volume + ask_volume
            imbalance = (bid_volume - ask_volume) / total_volume if total_volume > 0 else 0.0
            return mid, imbalance

        if best_bid is not None:
            return best_bid + HALF_SPREAD, 1.0

        if best_ask is not None:
            return best_ask - HALF_SPREAD, -1.0

        return memory.get("prev_mid"), 0.0
