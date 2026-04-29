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


class Trader:
    """Pebbles pair mean-reversion with the 50k basket anchor as a regime guard.

    The uploaded 558717 run confirmed that crossing the whole five-pebble
    basket on executable top-of-book prices is real, but too thin on its own.
    The uploaded 550929 run showed the stronger signal: same-direction pair
    mean reversion in XS/S and L/XL, with a late L/XL trend short causing the
    drawdown.

    This strategy keeps the pair trades, adds the dominant-trend block that
    would have avoided that late short, and uses the full five-pebble sum near
    50,000 as a sanity check before opening new pair exposure.
    """

    POSITION_LIMIT = 10
    FAIR_PEBBLES_SUM = 50000.0

    PEBBLES_XS = "PEBBLES_XS"
    PEBBLES_S = "PEBBLES_S"
    PEBBLES_M = "PEBBLES_M"
    PEBBLES_L = "PEBBLES_L"
    PEBBLES_XL = "PEBBLES_XL"

    PEBBLES = [
        PEBBLES_XS,
        PEBBLES_S,
        PEBBLES_M,
        PEBBLES_L,
        PEBBLES_XL,
    ]

    # (name, product_a, product_b, residual_type, rolling_window, entry_z, size)
    # For "sum": high z => sell both, low z => buy both.
    PAIRS = [
        ("PEBBLES_XS_S_SUM", PEBBLES_XS, PEBBLES_S, "sum", 200, 3.0, 10),
        ("PEBBLES_L_XL_SUM", PEBBLES_L, PEBBLES_XL, "sum", 300, 2.5, 10),
    ]

    EXIT_Z = 0.0

    # The late 550929 loss was a same-direction short into a one-leg-dominated
    # rally. This guard blocks new entries when the pair move is mostly one
    # trending leg rather than a clean pair residual stretch.
    DOMINANT_TREND_LOOKBACK = 100
    DOMINANT_TREND_TICKS = 250.0
    DOMINANT_TREND_SHARE = 0.55

    # Keep the 50k discovery as a guard, not as the whole alpha. The public and
    # uploaded data both keep Pebbles centered near 50k, but executable spreads
    # mean strict full-basket arbitrage is usually only a small edge.
    BASKET_GUARD_TICKS = 20.0

    DEBUG = False

    def bid(self) -> int:
        return 0

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        data = self._decode_state(state.traderData)
        pair_data = data.setdefault("pairs", {})
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}

        basket_metrics = self._pebbles_basket_metrics(state)
        if basket_metrics is not None:
            data["basket"] = {
                "timestamp": int(state.timestamp),
                "bid_sum": basket_metrics["bid_sum"],
                "ask_sum": basket_metrics["ask_sum"],
                "mid_sum": basket_metrics["mid_sum"],
                "bid_edge": basket_metrics["bid_sum"] - self.FAIR_PEBBLES_SUM,
                "ask_edge": self.FAIR_PEBBLES_SUM - basket_metrics["ask_sum"],
            }

        event_targets: Dict[str, int] = {}
        debug_events = []

        for name, product_a, product_b, residual_type, window, entry_z, size in self.PAIRS:
            mid_a = self._mid(state, product_a)
            mid_b = self._mid(state, product_b)
            if mid_a is None or mid_b is None:
                continue

            residual = mid_a + mid_b if residual_type == "sum" else mid_a - mid_b
            store = pair_data.setdefault(name, self._new_pair_store())
            z = self._update_rolling(store, residual, mid_a, mid_b, int(window))
            if z is None:
                continue

            old_signal = int(store.get("signal", 0) or 0)
            new_signal = self._next_signal(
                old_signal,
                z,
                float(entry_z),
                store,
                basket_metrics,
            )
            store["signal"] = new_signal
            store["last_z"] = z

            if new_signal == old_signal:
                continue

            targets = self._pair_targets(
                product_a=product_a,
                product_b=product_b,
                residual_type=residual_type,
                signal=new_signal,
                size=int(size),
            )
            event_targets.update(targets)
            debug_events.append((name, old_signal, new_signal, z, residual))

        for product, target in event_targets.items():
            if product not in state.order_depths:
                continue
            orders = self._trade_toward_target(product, target, state)
            result.setdefault(product, []).extend(orders)

        if self.DEBUG and debug_events:
            self._debug(state, debug_events, result, data.get("basket"))

        return result, 0, self._encode_state(data)

    def _new_pair_store(self) -> Dict:
        return {
            "hist": [],
            "mid_a_hist": [],
            "mid_b_hist": [],
            "sum": 0.0,
            "sumsq": 0.0,
            "signal": 0,
        }

    def _decode_state(self, trader_data: str) -> Dict:
        if not trader_data:
            return {"pairs": {}}
        try:
            data = json.loads(trader_data)
            if not isinstance(data, dict):
                return {"pairs": {}}
            data.setdefault("pairs", {})
            return data
        except Exception:
            return {"pairs": {}}

    def _encode_state(self, data: Dict) -> str:
        try:
            return json.dumps(data, separators=(",", ":"))
        except Exception:
            return ""

    def _best_bid_ask(self, depth: Optional[OrderDepth]) -> Tuple[Optional[int], Optional[int]]:
        if depth is None or not depth.buy_orders or not depth.sell_orders:
            return None, None
        return max(depth.buy_orders), min(depth.sell_orders)

    def _mid(self, state: TradingState, product: str) -> Optional[float]:
        best_bid, best_ask = self._best_bid_ask(state.order_depths.get(product))
        if best_bid is None or best_ask is None:
            return None
        return (best_bid + best_ask) / 2.0

    def _pebbles_basket_metrics(self, state: TradingState) -> Optional[Dict[str, float]]:
        bid_sum = 0.0
        ask_sum = 0.0
        mid_sum = 0.0

        for product in self.PEBBLES:
            best_bid, best_ask = self._best_bid_ask(state.order_depths.get(product))
            if best_bid is None or best_ask is None:
                return None
            bid_sum += best_bid
            ask_sum += best_ask
            mid_sum += (best_bid + best_ask) / 2.0

        return {"bid_sum": bid_sum, "ask_sum": ask_sum, "mid_sum": mid_sum}

    def _update_rolling(
        self,
        store: Dict,
        value: float,
        mid_a: float,
        mid_b: float,
        window: int,
    ) -> Optional[float]:
        hist = store.setdefault("hist", [])
        mid_a_hist = store.setdefault("mid_a_hist", [])
        mid_b_hist = store.setdefault("mid_b_hist", [])
        total = float(store.get("sum", 0.0))
        total_sq = float(store.get("sumsq", 0.0))

        hist.append(value)
        mid_a_hist.append(mid_a)
        mid_b_hist.append(mid_b)
        total += value
        total_sq += value * value

        if len(hist) > window:
            old = float(hist.pop(0))
            total -= old
            total_sq -= old * old

        max_mid_history = self.DOMINANT_TREND_LOOKBACK + 1
        if len(mid_a_hist) > max_mid_history:
            mid_a_hist.pop(0)
        if len(mid_b_hist) > max_mid_history:
            mid_b_hist.pop(0)

        store["sum"] = total
        store["sumsq"] = total_sq

        n = len(hist)
        if n < window:
            return None

        mean = total / n
        var = max(total_sq / n - mean * mean, 0.0)
        std = math.sqrt(var)
        if std <= 1e-9:
            return None

        return (value - mean) / std

    def _next_signal(
        self,
        signal: int,
        z: float,
        entry_z: float,
        store: Dict,
        basket_metrics: Optional[Dict[str, float]],
    ) -> int:
        if signal == 0:
            if z > entry_z:
                candidate = -1
            elif z < -entry_z:
                candidate = 1
            else:
                return 0

            if self._dominant_trend_against_entry(store, signal=candidate):
                return 0
            if not self._basket_allows_entry(candidate, basket_metrics):
                return 0
            return candidate

        if signal == 1 and z >= self.EXIT_Z:
            return 0
        if signal == -1 and z <= -self.EXIT_Z:
            return 0
        return signal

    def _basket_allows_entry(self, signal: int, basket_metrics: Optional[Dict[str, float]]) -> bool:
        if basket_metrics is None:
            return True

        mid_sum = float(basket_metrics["mid_sum"])
        if signal > 0:
            return mid_sum <= self.FAIR_PEBBLES_SUM + self.BASKET_GUARD_TICKS
        if signal < 0:
            return mid_sum >= self.FAIR_PEBBLES_SUM - self.BASKET_GUARD_TICKS
        return True

    def _dominant_trend_against_entry(self, store: Dict, signal: int) -> bool:
        mid_a_hist = store.get("mid_a_hist", [])
        mid_b_hist = store.get("mid_b_hist", [])
        lookback = self.DOMINANT_TREND_LOOKBACK
        if len(mid_a_hist) <= lookback or len(mid_b_hist) <= lookback:
            return False

        move_a = float(mid_a_hist[-1]) - float(mid_a_hist[-1 - lookback])
        move_b = float(mid_b_hist[-1]) - float(mid_b_hist[-1 - lookback])
        total_abs_move = abs(move_a) + abs(move_b)
        if total_abs_move <= 1e-9:
            return False

        if signal < 0:
            dominant_up_move = max(move_a, move_b)
            return (
                move_a + move_b > self.DOMINANT_TREND_TICKS
                and dominant_up_move > self.DOMINANT_TREND_TICKS
                and dominant_up_move / total_abs_move >= self.DOMINANT_TREND_SHARE
            )

        dominant_down_move = max(-move_a, -move_b)
        return (
            move_a + move_b < -self.DOMINANT_TREND_TICKS
            and dominant_down_move > self.DOMINANT_TREND_TICKS
            and dominant_down_move / total_abs_move >= self.DOMINANT_TREND_SHARE
        )

    def _pair_targets(
        self,
        product_a: str,
        product_b: str,
        residual_type: str,
        signal: int,
        size: int,
    ) -> Dict[str, int]:
        if signal == 0:
            return {product_a: 0, product_b: 0}

        clipped_size = self._clip(size, 0, self.POSITION_LIMIT)
        if residual_type == "sum":
            return {
                product_a: signal * clipped_size,
                product_b: signal * clipped_size,
            }

        return {
            product_a: signal * clipped_size,
            product_b: -signal * clipped_size,
        }

    def _trade_toward_target(self, product: str, target: int, state: TradingState) -> List[Order]:
        orders: List[Order] = []
        depth = state.order_depths.get(product)
        if depth is None:
            return orders

        current = int(state.position.get(product, 0))
        target = self._clip(int(target), -self.POSITION_LIMIT, self.POSITION_LIMIT)

        if target > current:
            qty_needed = min(target - current, self.POSITION_LIMIT - current)
            for price in sorted(depth.sell_orders):
                if qty_needed <= 0:
                    break
                visible_qty = -int(depth.sell_orders[price])
                qty = min(qty_needed, visible_qty)
                if qty > 0:
                    orders.append(Order(product, int(price), int(qty)))
                    qty_needed -= qty

        elif target < current:
            qty_needed = min(current - target, current + self.POSITION_LIMIT)
            for price in sorted(depth.buy_orders, reverse=True):
                if qty_needed <= 0:
                    break
                visible_qty = int(depth.buy_orders[price])
                qty = min(qty_needed, visible_qty)
                if qty > 0:
                    orders.append(Order(product, int(price), -int(qty)))
                    qty_needed -= qty

        return orders

    def _debug(self, state: TradingState, debug_events, result: Dict[str, List[Order]], basket) -> None:
        for name, old_signal, new_signal, z, residual in debug_events:
            basket_text = ""
            if isinstance(basket, dict):
                basket_text = f" basket_mid_sum={float(basket.get('mid_sum', 0.0)):.2f}"
            print(
                "PEBBLES_ANCHOR_GUARD_MR",
                f"timestamp={int(state.timestamp)}",
                f"pair={name}",
                f"old_signal={old_signal}",
                f"new_signal={new_signal}",
                f"z={z:.4f}",
                f"residual={residual:.2f}",
                basket_text,
            )
        for product, orders in result.items():
            for order in orders:
                print(
                    "PEBBLES_ANCHOR_GUARD_MR_ORDER",
                    f"timestamp={int(state.timestamp)}",
                    f"product={product}",
                    f"price={int(order.price)}",
                    f"quantity={int(order.quantity)}",
                )

    def _clip(self, value: int, lo: int, hi: int) -> int:
        return max(lo, min(hi, value))


if __name__ == "__main__":
    from round5_dashboard_export import main

    main(Trader, __file__)