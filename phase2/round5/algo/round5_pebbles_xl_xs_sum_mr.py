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
import math

# Round 5 residual mean-reversion strategy.
# IMPORTANT:
# - Round 5 products all have a position limit of 10.
# - This version uses only active execution against visible book levels.
# - It stores compact rolling residual histories in traderData.

class Trader:
    POSITION_LIMIT = 10
    PAIR_SIZE = 10

    # (name, product_a, product_b, residual_type, rolling_window, entry_z)
    # residual_type:
    #   "sum":  residual = mid_a + mid_b
    #           high z => sell both, low z => buy both
    #   "diff": residual = mid_a - mid_b
    #           high z => sell A / buy B, low z => buy A / sell B
    PAIRS = [('PEBBLES_XL_XS_SUM', 'PEBBLES_XL', 'PEBBLES_XS', 'sum', 1000, 2.0)]

    EXIT_Z = 0.0

    def bid(self) -> int:
        # Ignored outside Round 2.
        return 0

    def _decode_state(self, trader_data: str) -> Dict:
        if not trader_data:
            return {"pairs": {}}
        try:
            data = json.loads(trader_data)
            if not isinstance(data, dict):
                return {"pairs": {}}
            if "pairs" not in data:
                data["pairs"] = {}
            return data
        except Exception:
            return {"pairs": {}}

    def _encode_state(self, data: Dict) -> str:
        try:
            return json.dumps(data, separators=(",", ":"))
        except Exception:
            return ""

    def _best_bid_ask(self, order_depth: OrderDepth) -> Tuple[Optional[int], Optional[int]]:
        if not order_depth.buy_orders or not order_depth.sell_orders:
            return None, None
        best_bid = max(order_depth.buy_orders.keys())
        best_ask = min(order_depth.sell_orders.keys())
        return best_bid, best_ask

    def _mid(self, state: TradingState, product: str) -> Optional[float]:
        order_depth = state.order_depths.get(product)
        if order_depth is None:
            return None
        best_bid, best_ask = self._best_bid_ask(order_depth)
        if best_bid is None or best_ask is None:
            return None
        return (best_bid + best_ask) / 2.0

    def _update_rolling(self, pair_store: Dict, value: float, window: int) -> Optional[float]:
        hist = pair_store.setdefault("hist", [])
        total = float(pair_store.get("sum", 0.0))
        total_sq = float(pair_store.get("sumsq", 0.0))

        hist.append(value)
        total += value
        total_sq += value * value

        if len(hist) > window:
            old = float(hist.pop(0))
            total -= old
            total_sq -= old * old

        pair_store["sum"] = total
        pair_store["sumsq"] = total_sq

        n = len(hist)
        if n < window:
            return None

        mean = total / n
        var = max(total_sq / n - mean * mean, 0.0)
        std = math.sqrt(var)
        if std <= 1e-9:
            return None

        return (value - mean) / std

    def _clip(self, x: int, lo: int, hi: int) -> int:
        if x < lo:
            return lo
        if x > hi:
            return hi
        return x

    def _trade_toward_target(self, product: str, target: int, state: TradingState) -> List[Order]:
        orders: List[Order] = []
        order_depth = state.order_depths.get(product)
        if order_depth is None:
            return orders

        current = int(state.position.get(product, 0))
        target = self._clip(int(target), -self.POSITION_LIMIT, self.POSITION_LIMIT)

        if target > current:
            qty_needed = min(target - current, self.POSITION_LIMIT - current)
            if qty_needed <= 0:
                return orders

            # Buy from cheapest asks first. Sell volumes are negative in Prosperity.
            for price in sorted(order_depth.sell_orders.keys()):
                visible_qty = -int(order_depth.sell_orders[price])
                if visible_qty <= 0:
                    continue
                qty = min(qty_needed, visible_qty)
                if qty > 0:
                    orders.append(Order(product, int(price), int(qty)))
                    qty_needed -= qty
                if qty_needed <= 0:
                    break

        elif target < current:
            qty_needed = min(current - target, current + self.POSITION_LIMIT)
            if qty_needed <= 0:
                return orders

            # Sell to highest bids first. Buy volumes are positive in Prosperity.
            for price in sorted(order_depth.buy_orders.keys(), reverse=True):
                visible_qty = int(order_depth.buy_orders[price])
                if visible_qty <= 0:
                    continue
                qty = min(qty_needed, visible_qty)
                if qty > 0:
                    orders.append(Order(product, int(price), -int(qty)))
                    qty_needed -= qty
                if qty_needed <= 0:
                    break

        return orders

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        data = self._decode_state(state.traderData)
        pair_data = data.setdefault("pairs", {})

        targets: Dict[str, int] = {}

        for name, product_a, product_b, residual_type, window, entry_z in self.PAIRS:
            mid_a = self._mid(state, product_a)
            mid_b = self._mid(state, product_b)
            if mid_a is None or mid_b is None:
                continue

            if residual_type == "sum":
                residual = mid_a + mid_b
            else:
                residual = mid_a - mid_b

            store = pair_data.setdefault(name, {"hist": [], "sum": 0.0, "sumsq": 0.0, "signal": 0})
            z = self._update_rolling(store, residual, int(window))
            if z is None:
                continue

            signal = int(store.get("signal", 0))

            # Signal convention:
            # +1 = long residual
            # -1 = short residual
            if signal == 0:
                if z > float(entry_z):
                    signal = -1
                elif z < -float(entry_z):
                    signal = 1
            elif signal == 1:
                if z >= self.EXIT_Z:
                    signal = 0
            elif signal == -1:
                if z <= -self.EXIT_Z:
                    signal = 0

            store["signal"] = signal

            if signal == 0:
                continue

            size = int(self.PAIR_SIZE)

            if residual_type == "sum":
                # Low residual: buy both. High residual: sell both.
                targets[product_a] = targets.get(product_a, 0) + signal * size
                targets[product_b] = targets.get(product_b, 0) + signal * size
            else:
                # Low A-B: buy A, sell B. High A-B: sell A, buy B.
                targets[product_a] = targets.get(product_a, 0) + signal * size
                targets[product_b] = targets.get(product_b, 0) - signal * size

        # Flatten products whose pair signal has disappeared by giving them target 0.
        products_to_trade = set(targets.keys()) | {
            product for product, position in state.position.items() if position != 0
        }

        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        for product in products_to_trade:
            if product not in state.order_depths:
                continue
            target = targets.get(product, 0)
            target = self._clip(target, -self.POSITION_LIMIT, self.POSITION_LIMIT)
            orders = self._trade_toward_target(product, target, state)
            if orders:
                result[product].extend(orders)

        traderData = self._encode_state(data)
        conversions = 0
        return result, conversions, traderData


if __name__ == "__main__":
    from round5_dashboard_export import main

    main(Trader, __file__)
