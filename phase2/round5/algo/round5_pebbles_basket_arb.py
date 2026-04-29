try:
    from datamodel import Order, OrderDepth, TradingState
except ModuleNotFoundError:
    import sys
    import types
    from pathlib import Path

    sys.modules.setdefault("jsonpickle", types.SimpleNamespace(encode=lambda value: str(value)))
    misc_dir = Path(__file__).resolve().parents[3] / "misc"
    if str(misc_dir) not in sys.path:
        sys.path.insert(0, str(misc_dir))
    from datamodel import Order, OrderDepth, TradingState

from typing import Dict, List, Optional, Tuple
import json


class Trader:
    """Round 5 pebbles basket arbitrage.

    The five pebbles mids are centered near 50,000 in the public sample. This
    strategy trades the full basket at top-of-book when the executable basket
    crosses that anchor:
    - buy one unit of every pebble when sum(best asks) < 50,000
    - sell one unit of every pebble when sum(best bids) > 50,000
    """

    POSITION_LIMIT = 10
    FAIR_BASKET_VALUE = 50000.0
    MIN_EDGE = 0.0
    MIN_EXIT_PROFIT = 0.0

    PEBBLES = [
        "PEBBLES_XS",
        "PEBBLES_S",
        "PEBBLES_M",
        "PEBBLES_L",
        "PEBBLES_XL",
    ]

    def bid(self) -> int:
        return 0

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        data = self._decode_state(state.traderData)
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}

        book = self._pebbles_top_of_book(state)
        if book is None:
            return result, 0, self._encode_state(data)

        self._sync_cost_basis(data, state)

        best_bid_sum = sum(item["bid"] for item in book.values())
        best_ask_sum = sum(item["ask"] for item in book.values())
        buy_edge = self.FAIR_BASKET_VALUE - best_ask_sum
        sell_edge = best_bid_sum - self.FAIR_BASKET_VALUE
        basis = data.setdefault("basis", {})
        long_qty = int(basis.get("long_qty", 0) or 0)
        short_qty = int(basis.get("short_qty", 0) or 0)
        avg_long_cost = self._average_cost(basis, "long_cost_total", long_qty)
        avg_short_credit = self._average_cost(basis, "short_credit_total", short_qty)

        data["last"] = {
            "timestamp": int(state.timestamp),
            "best_bid_sum": best_bid_sum,
            "best_ask_sum": best_ask_sum,
            "buy_edge": buy_edge,
            "sell_edge": sell_edge,
            "long_qty": long_qty,
            "short_qty": short_qty,
            "avg_long_cost": avg_long_cost,
            "avg_short_credit": avg_short_credit,
        }

        if long_qty > 0 and avg_long_cost is not None and best_bid_sum > avg_long_cost + self.MIN_EXIT_PROFIT:
            basket_qty = self._max_sell_existing_long_qty(state, book)
            if basket_qty > 0:
                self._append_basket_orders(result, book, "sell", basket_qty)
                self._set_pending(data, "sell", best_bid_sum, basket_qty)
                data["last"]["signal"] = "close_long_basket"
            else:
                data["last"]["signal"] = "close_long_blocked"

        elif short_qty > 0 and avg_short_credit is not None and best_ask_sum < avg_short_credit - self.MIN_EXIT_PROFIT:
            basket_qty = self._max_buy_existing_short_qty(state, book)
            if basket_qty > 0:
                self._append_basket_orders(result, book, "buy", basket_qty)
                self._set_pending(data, "buy", best_ask_sum, basket_qty)
                data["last"]["signal"] = "close_short_basket"
            else:
                data["last"]["signal"] = "close_short_blocked"

        elif buy_edge > self.MIN_EDGE and buy_edge >= sell_edge:
            basket_qty = self._max_buy_basket_qty(state, book)
            if basket_qty > 0:
                self._append_basket_orders(result, book, "buy", basket_qty)
                self._set_pending(data, "buy", best_ask_sum, basket_qty)
                data["last"]["signal"] = "buy_basket"
            else:
                data["last"]["signal"] = "buy_blocked"

        elif sell_edge > self.MIN_EDGE:
            basket_qty = self._max_sell_basket_qty(state, book)
            if basket_qty > 0:
                self._append_basket_orders(result, book, "sell", basket_qty)
                self._set_pending(data, "sell", best_bid_sum, basket_qty)
                data["last"]["signal"] = "sell_basket"
            else:
                data["last"]["signal"] = "sell_blocked"

        else:
            data["last"]["signal"] = "none"

        return result, 0, self._encode_state(data)

    def _sync_cost_basis(self, data: Dict, state: TradingState) -> None:
        basis = data.setdefault(
            "basis",
            {
                "long_qty": 0,
                "long_cost_total": 0.0,
                "short_qty": 0,
                "short_credit_total": 0.0,
            },
        )
        pending = data.pop("pending", None)

        current_long_qty = self._complete_long_basket_qty(state)
        current_short_qty = self._complete_short_basket_qty(state)
        previous_long_qty = int(basis.get("long_qty", 0) or 0)
        previous_short_qty = int(basis.get("short_qty", 0) or 0)

        long_cost_total = float(basis.get("long_cost_total", 0.0) or 0.0)
        short_credit_total = float(basis.get("short_credit_total", 0.0) or 0.0)

        if current_long_qty > previous_long_qty:
            fill_qty = current_long_qty - previous_long_qty
            basket_price = self._pending_basket_price(pending, "buy")
            long_cost_total += fill_qty * basket_price
        elif current_long_qty < previous_long_qty:
            if current_long_qty <= 0:
                long_cost_total = 0.0
            else:
                avg_cost = long_cost_total / max(previous_long_qty, 1)
                long_cost_total = avg_cost * current_long_qty

        if current_short_qty > previous_short_qty:
            fill_qty = current_short_qty - previous_short_qty
            basket_price = self._pending_basket_price(pending, "sell")
            short_credit_total += fill_qty * basket_price
        elif current_short_qty < previous_short_qty:
            if current_short_qty <= 0:
                short_credit_total = 0.0
            else:
                avg_credit = short_credit_total / max(previous_short_qty, 1)
                short_credit_total = avg_credit * current_short_qty

        basis["long_qty"] = current_long_qty
        basis["long_cost_total"] = long_cost_total
        basis["short_qty"] = current_short_qty
        basis["short_credit_total"] = short_credit_total

    def _pending_basket_price(self, pending, side: str) -> float:
        if isinstance(pending, dict) and pending.get("side") == side:
            price = pending.get("basket_price")
            if isinstance(price, (int, float)):
                return float(price)
        return self.FAIR_BASKET_VALUE

    def _average_cost(self, basis: Dict, key: str, quantity: int) -> Optional[float]:
        if quantity <= 0:
            return None
        return float(basis.get(key, 0.0) or 0.0) / quantity

    def _append_basket_orders(
        self,
        result: Dict[str, List[Order]],
        book: Dict[str, Dict[str, int]],
        side: str,
        quantity: int,
    ) -> None:
        for product in self.PEBBLES:
            if side == "buy":
                result[product].append(Order(product, int(book[product]["ask"]), int(quantity)))
            else:
                result[product].append(Order(product, int(book[product]["bid"]), -int(quantity)))

    def _set_pending(self, data: Dict, side: str, basket_price: float, quantity: int) -> None:
        data["pending"] = {
            "side": side,
            "basket_price": basket_price,
            "quantity": quantity,
        }

    def _pebbles_top_of_book(self, state: TradingState) -> Optional[Dict[str, Dict[str, int]]]:
        book: Dict[str, Dict[str, int]] = {}

        for product in self.PEBBLES:
            depth = state.order_depths.get(product)
            if depth is None:
                return None

            bid, bid_volume, ask, ask_volume = self._best_bid_ask_with_volume(depth)
            if bid is None or bid_volume is None or ask is None or ask_volume is None:
                return None

            book[product] = {
                "bid": bid,
                "bid_volume": bid_volume,
                "ask": ask,
                "ask_volume": ask_volume,
            }

        return book

    def _best_bid_ask_with_volume(
        self,
        depth: OrderDepth,
    ) -> Tuple[Optional[int], Optional[int], Optional[int], Optional[int]]:
        if not depth.buy_orders or not depth.sell_orders:
            return None, None, None, None

        bid = max(depth.buy_orders)
        ask = min(depth.sell_orders)
        bid_volume = max(0, int(depth.buy_orders[bid]))
        ask_volume = max(0, -int(depth.sell_orders[ask]))
        return int(bid), bid_volume, int(ask), ask_volume

    def _max_buy_basket_qty(self, state: TradingState, book: Dict[str, Dict[str, int]]) -> int:
        quantities = []
        for product in self.PEBBLES:
            current = int(state.position.get(product, 0))
            remaining_limit = self.POSITION_LIMIT - current
            quantities.append(min(remaining_limit, int(book[product]["ask_volume"])))
        return max(0, min(quantities))

    def _max_sell_basket_qty(self, state: TradingState, book: Dict[str, Dict[str, int]]) -> int:
        quantities = []
        for product in self.PEBBLES:
            current = int(state.position.get(product, 0))
            remaining_limit = self.POSITION_LIMIT + current
            quantities.append(min(remaining_limit, int(book[product]["bid_volume"])))
        return max(0, min(quantities))

    def _max_sell_existing_long_qty(self, state: TradingState, book: Dict[str, Dict[str, int]]) -> int:
        quantities = []
        for product in self.PEBBLES:
            current = max(0, int(state.position.get(product, 0)))
            quantities.append(min(current, int(book[product]["bid_volume"])))
        return max(0, min(quantities))

    def _max_buy_existing_short_qty(self, state: TradingState, book: Dict[str, Dict[str, int]]) -> int:
        quantities = []
        for product in self.PEBBLES:
            current = max(0, -int(state.position.get(product, 0)))
            quantities.append(min(current, int(book[product]["ask_volume"])))
        return max(0, min(quantities))

    def _complete_long_basket_qty(self, state: TradingState) -> int:
        return max(0, min(max(0, int(state.position.get(product, 0))) for product in self.PEBBLES))

    def _complete_short_basket_qty(self, state: TradingState) -> int:
        return max(0, min(max(0, -int(state.position.get(product, 0))) for product in self.PEBBLES))

    def _decode_state(self, trader_data: str) -> Dict:
        if not trader_data:
            return {}
        try:
            data = json.loads(trader_data)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _encode_state(self, data: Dict) -> str:
        try:
            return json.dumps(data, separators=(",", ":"))
        except Exception:
            return ""


if __name__ == "__main__":
    from round5_dashboard_export import main

    main(Trader, __file__)
