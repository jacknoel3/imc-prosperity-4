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
    PRODUCT_L = "PEBBLES_L"
    PRODUCT_XL = "PEBBLES_XL"
    PAIR_NAME = "PEBBLES_L_XL_SUM_ACTIVE_ROLLOVER"

    POSITION_LIMIT = 10

    # Uses more of the L limit, keeps XL smaller because XL was the leg that killed the previous run.
    LEG_TARGETS = {
        PRODUCT_L: 10,
        PRODUCT_XL: 5,
    }

    ROLLING_WINDOW = 300
    ENTRY_Z = 2.5

    # Entry confirmation
    CONFIRMED_ENTRY_Z = 2.3
    MIN_EXTREME_Z = 2.8
    ROLLOVER_Z = 0.6
    MOMENTUM_LOOKBACK = 20
    MIN_TICKS_BETWEEN_TRADES = 50
    MIN_HOLD_TICKS = 25

    # Exit / stop logic
    EXIT_Z = 0.0
    MIN_PROFIT_CASH = 20.0
    PARTIAL_STOP_Z = 0.8
    FULL_STOP_Z = 1.2

    def bid(self) -> int:
        return 0

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        data = self._decode_state(state.traderData)
        pair_data = data.setdefault("pairs", {})
        store = pair_data.setdefault(self.PAIR_NAME, self._new_pair_store())

        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}

        mid_l = self._mid(state, self.PRODUCT_L)
        mid_xl = self._mid(state, self.PRODUCT_XL)

        if mid_l is None or mid_xl is None:
            return result, 0, self._encode_state(data)

        residual = mid_l + mid_xl
        z, mean, std = self._update_rolling(store, residual)
        self._update_mid_histories(store, {self.PRODUCT_L: mid_l, self.PRODUCT_XL: mid_xl})

        if z is None or mean is None or std is None:
            return result, 0, self._encode_state(data)

        side = self._current_side(store)

        if side != 0 and self._strategy_positions_flat(state):
            self._reset_trade_state(store)
            side = 0

        self._update_extreme_state(store, z)

        residual_momentum_5 = self._series_momentum(store.get("hist", []), self.MOMENTUM_LOOKBACK)

        long_confirmed = self._long_setup_confirmed(store, z, residual_momentum_5)
        short_confirmed = self._short_setup_confirmed(store, z, residual_momentum_5)

        if side == 0:
            if abs(z) < self.CONFIRMED_ENTRY_Z or not self._can_open_new_trade(state, store):
                return result, 0, self._encode_state(data)

            if short_confirmed:
                orders = self._enter_trade(
                    state=state,
                    store=store,
                    side=-1,
                    z=z,
                    residual=residual,
                )
                self._append_orders(result, orders)

            elif long_confirmed:
                orders = self._enter_trade(
                    state=state,
                    store=store,
                    side=1,
                    z=z,
                    residual=residual,
                )
                self._append_orders(result, orders)

        else:
            # Avoid opening and closing in the exact same timestamp.
            if int(store.get("entry_ts", -1)) == int(state.timestamp):
                return result, 0, self._encode_state(data)

            opposite_setup = long_confirmed if side == -1 else short_confirmed

            orders = self._manage_open_trade(
                state=state,
                store=store,
                side=side,
                z=z,
                residual=residual,
                opposite_setup=opposite_setup,
            )
            self._append_orders(result, orders)

        return result, 0, self._encode_state(data)

    def _new_pair_store(self) -> Dict:
        return {
            "hist": [],
            "sum": 0.0,
            "sumsq": 0.0,
            "mid_hist": {
                self.PRODUCT_L: [],
                self.PRODUCT_XL: [],
            },
            "side": 0,
            "high_extreme_seen": False,
            "low_extreme_seen": False,
            "recent_max_z": None,
            "recent_min_z": None,
            "stop_reduced": False,
            "last_trade_ts": None,
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

    def _update_rolling(
        self,
        store: Dict,
        residual: float,
    ) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        hist = store.setdefault("hist", [])
        total = float(store.get("sum", 0.0))
        total_sq = float(store.get("sumsq", 0.0))

        hist.append(residual)
        total += residual
        total_sq += residual * residual

        if len(hist) > self.ROLLING_WINDOW:
            old = float(hist.pop(0))
            total -= old
            total_sq -= old * old

        store["sum"] = total
        store["sumsq"] = total_sq

        n = len(hist)
        if n < self.ROLLING_WINDOW:
            return None, None, None

        mean = total / n
        var = max(total_sq / n - mean * mean, 0.0)
        std = math.sqrt(var)

        if std <= 1e-9:
            return None, mean, std

        z = (residual - mean) / std
        return z, mean, std

    def _update_mid_histories(self, store: Dict, mids: Dict[str, float]) -> None:
        mid_hist = store.setdefault("mid_hist", {self.PRODUCT_L: [], self.PRODUCT_XL: []})

        for product, mid in mids.items():
            hist = mid_hist.setdefault(product, [])
            hist.append(mid)

            keep = self.MOMENTUM_LOOKBACK + 1
            if len(hist) > keep:
                del hist[: len(hist) - keep]

    def _update_extreme_state(self, store: Dict, z: float) -> None:
        # Track high-z regime until z crosses back below 0.
        if z > self.ENTRY_Z:
            store["high_extreme_seen"] = True
            store["recent_max_z"] = max(float(store.get("recent_max_z") or z), z)
            store["low_extreme_seen"] = False
            store["recent_min_z"] = None

        elif store.get("high_extreme_seen") and z > 0:
            store["recent_max_z"] = max(float(store.get("recent_max_z") or z), z)

        elif z <= 0:
            store["high_extreme_seen"] = False
            store["recent_max_z"] = None

        # Track low-z regime until z crosses back above 0.
        if z < -self.ENTRY_Z:
            store["low_extreme_seen"] = True
            store["recent_min_z"] = min(float(store.get("recent_min_z") or z), z)
            store["high_extreme_seen"] = False
            store["recent_max_z"] = None

        elif store.get("low_extreme_seen") and z < 0:
            store["recent_min_z"] = min(float(store.get("recent_min_z") or z), z)

        elif z >= 0:
            store["low_extreme_seen"] = False
            store["recent_min_z"] = None

    def _short_setup_confirmed(
        self,
        store: Dict,
        z: float,
        residual_momentum_5: Optional[float],
    ) -> bool:
        if not store.get("high_extreme_seen"):
            return False

        recent_max_z = store.get("recent_max_z")
        if recent_max_z is None or residual_momentum_5 is None:
            return False

        recent_max_z = float(recent_max_z)
        extreme_enough = recent_max_z >= self.MIN_EXTREME_Z
        rolled_over = z < recent_max_z - self.ROLLOVER_Z
        still_extended = z > self.CONFIRMED_ENTRY_Z
        momentum_confirmed = residual_momentum_5 < 0

        return extreme_enough and rolled_over and still_extended and momentum_confirmed

    def _long_setup_confirmed(
        self,
        store: Dict,
        z: float,
        residual_momentum_5: Optional[float],
    ) -> bool:
        if not store.get("low_extreme_seen"):
            return False

        recent_min_z = store.get("recent_min_z")
        if recent_min_z is None or residual_momentum_5 is None:
            return False

        recent_min_z = float(recent_min_z)
        extreme_enough = recent_min_z <= -self.MIN_EXTREME_Z
        bounced = z > recent_min_z + self.ROLLOVER_Z
        still_extended = z < -self.CONFIRMED_ENTRY_Z
        momentum_confirmed = residual_momentum_5 > 0

        return extreme_enough and bounced and still_extended and momentum_confirmed

    def _enter_trade(
        self,
        state: TradingState,
        store: Dict,
        side: int,
        z: float,
        residual: float,
    ) -> List[Order]:
        target_positions = self._entry_targets(state, store, side)

        if not target_positions:
            return []

        orders: List[Order] = []
        for product, target in target_positions.items():
            orders.extend(self._trade_toward_target(state, product, target))

        if not orders:
            return []

        store.update(
            {
                "side": side,
                "entry_ts": int(state.timestamp),
                "entry_z": z,
                "entry_residual": residual,
                "entry_prices": self._approx_entry_prices(state, side, orders),
                "stop_reduced": False,
            }
        )

        residual_momentum = self._series_momentum(store.get("hist", []), self.MOMENTUM_LOOKBACK)
        reason = "entry_long_confirmed" if side > 0 else "entry_short_confirmed"
        self._debug_orders(state, store, orders, side, z, residual_momentum, reason)

        # Once we enter, reset extreme flags so the same regime does not repeatedly fire.
        store["high_extreme_seen"] = False
        store["low_extreme_seen"] = False
        store["recent_max_z"] = None
        store["recent_min_z"] = None

        return orders

    def _entry_targets(self, state: TradingState, store: Dict, side: int) -> Dict[str, int]:
        targets: Dict[str, int] = {}

        for product, base_size in self.LEG_TARGETS.items():
            momentum = self._series_momentum(
                store.get("mid_hist", {}).get(product, []),
                self.MOMENTUM_LOOKBACK,
            )

            if momentum is None:
                continue
            if side > 0 and momentum < 0:
                continue
            if side < 0 and momentum > 0:
                continue

            size = int(base_size)

            target = side * size
            target = self._clip(target, -self.POSITION_LIMIT, self.POSITION_LIMIT)

            current = int(state.position.get(product, 0))
            if target != current:
                targets[product] = target

        return targets

    def _manage_open_trade(
        self,
        state: TradingState,
        store: Dict,
        side: int,
        z: float,
        residual: float,
        opposite_setup: bool,
    ) -> List[Order]:
        if self._strategy_positions_flat(state):
            self._reset_trade_state(store)
            return []

        entry_z = float(store.get("entry_z", z))
        executable_profit = self._executable_exit_profit(state, side, store)

        # Adverse residual stop.
        if side < 0:
            if z > entry_z + self.FULL_STOP_Z:
                return self._record_exit_orders(state, store, side, z, "full_adverse_z_stop", fraction=0.0)
            if z > entry_z + self.PARTIAL_STOP_Z and not store.get("stop_reduced"):
                store["stop_reduced"] = True
                return self._record_exit_orders(state, store, side, z, "partial_adverse_z_stop", fraction=0.5)

        else:
            if z < entry_z - self.FULL_STOP_Z:
                return self._record_exit_orders(state, store, side, z, "full_adverse_z_stop", fraction=0.0)
            if z < entry_z - self.PARTIAL_STOP_Z and not store.get("stop_reduced"):
                store["stop_reduced"] = True
                return self._record_exit_orders(state, store, side, z, "partial_adverse_z_stop", fraction=0.5)

        if self._age_ticks(state, store) < self.MIN_HOLD_TICKS:
            return []

        if abs(z) < self.CONFIRMED_ENTRY_Z:
            return []

        # Opposite confirmed setup: stop being stubborn.
        if opposite_setup:
            return self._record_exit_orders(state, store, side, z, "opposite_setup", fraction=0.0)

        # Profit-aware residual exit.
        if executable_profit >= self.MIN_PROFIT_CASH:
            if side < 0 and z <= self.EXIT_Z:
                return self._record_exit_orders(state, store, side, z, "profitable_reversion", fraction=0.0)
            if side > 0 and z >= -self.EXIT_Z:
                return self._record_exit_orders(state, store, side, z, "profitable_reversion", fraction=0.0)

        return []

    def _record_exit_orders(
        self,
        state: TradingState,
        store: Dict,
        side: int,
        z: float,
        reason: str,
        fraction: float,
    ) -> List[Order]:
        orders = self._exit_to_fraction(state, fraction=fraction)
        if orders:
            store["last_trade_ts"] = int(state.timestamp)
            residual_momentum = self._series_momentum(store.get("hist", []), self.MOMENTUM_LOOKBACK)
            self._debug_orders(state, store, orders, side, z, residual_momentum, reason)
        return orders

    def _exit_to_fraction(self, state: TradingState, fraction: float) -> List[Order]:
        orders: List[Order] = []

        for product in self.LEG_TARGETS:
            current = int(state.position.get(product, 0))

            if current == 0:
                continue

            target_abs = int(math.floor(abs(current) * fraction))
            target = target_abs if current > 0 else -target_abs

            orders.extend(self._trade_toward_target(state, product, target))

        return orders

    def _trade_toward_target(
        self,
        state: TradingState,
        product: str,
        target: int,
    ) -> List[Order]:
        orders: List[Order] = []

        depth = state.order_depths.get(product)
        if depth is None:
            return orders

        current = int(state.position.get(product, 0))
        target = self._clip(int(target), -self.POSITION_LIMIT, self.POSITION_LIMIT)

        if target > current:
            qty_needed = min(target - current, self.POSITION_LIMIT - current)
            if qty_needed <= 0:
                return orders

            # Active buy: cross asks.
            for price in sorted(depth.sell_orders):
                visible_qty = max(0, -int(depth.sell_orders[price]))
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

            # Active sell: cross bids.
            for price in sorted(depth.buy_orders, reverse=True):
                visible_qty = max(0, int(depth.buy_orders[price]))
                qty = min(qty_needed, visible_qty)

                if qty > 0:
                    orders.append(Order(product, int(price), -int(qty)))
                    qty_needed -= qty

                if qty_needed <= 0:
                    break

        return orders

    def _approx_entry_prices(self, state: TradingState, side: int, orders: List[Order]) -> Dict[str, float]:
        notional: Dict[str, float] = {}
        qtys: Dict[str, int] = {}

        for order in orders:
            notional[order.symbol] = notional.get(order.symbol, 0.0) + order.price * abs(order.quantity)
            qtys[order.symbol] = qtys.get(order.symbol, 0) + abs(order.quantity)

        prices: Dict[str, float] = {}

        for product, value in notional.items():
            qty = qtys.get(product, 0)
            if qty > 0:
                prices[product] = value / qty

        # Fallback for any leg not executed but later held somehow.
        for product in self.LEG_TARGETS:
            if product in prices:
                continue

            best_bid, best_ask = self._best_bid_ask(state.order_depths.get(product))
            if best_bid is None or best_ask is None:
                continue

            prices[product] = best_ask if side > 0 else best_bid

        return prices

    def _executable_exit_profit(self, state: TradingState, side: int, store: Dict) -> float:
        entry_prices = store.get("entry_prices", {})
        profit = 0.0

        for product in self.LEG_TARGETS:
            position = int(state.position.get(product, 0))
            entry_price = entry_prices.get(product)
            best_bid, best_ask = self._best_bid_ask(state.order_depths.get(product))

            if entry_price is None or best_bid is None or best_ask is None:
                continue

            if position > 0:
                profit += (best_bid - float(entry_price)) * position
            elif position < 0:
                profit += (float(entry_price) - best_ask) * (-position)

        return profit

    def _series_momentum(self, series: List[float], lookback: int) -> Optional[float]:
        if len(series) <= lookback:
            return None
        return float(series[-1]) - float(series[-1 - lookback])

    def _age_ticks(self, state: TradingState, store: Dict) -> int:
        entry_ts = store.get("entry_ts")
        if entry_ts is None:
            return 0
        return max(0, int(round((int(state.timestamp) - int(entry_ts)) / 100)))

    def _can_open_new_trade(self, state: TradingState, store: Dict) -> bool:
        last_trade_ts = store.get("last_trade_ts")
        if last_trade_ts is None:
            return True
        ticks_since_last_trade = int(round((int(state.timestamp) - int(last_trade_ts)) / 100))
        return ticks_since_last_trade >= self.MIN_TICKS_BETWEEN_TRADES

    def _strategy_positions_flat(self, state: TradingState) -> bool:
        return all(int(state.position.get(product, 0)) == 0 for product in self.LEG_TARGETS)

    def _current_side(self, store: Dict) -> int:
        return int(store.get("side", 0) or 0)

    def _reset_trade_state(self, store: Dict) -> None:
        for key in (
            "entry_ts",
            "entry_z",
            "entry_residual",
            "entry_prices",
        ):
            store.pop(key, None)

        store["side"] = 0
        store["stop_reduced"] = False
        store["high_extreme_seen"] = False
        store["low_extreme_seen"] = False
        store["recent_max_z"] = None
        store["recent_min_z"] = None

    def _debug_orders(
        self,
        state: TradingState,
        store: Dict,
        orders: List[Order],
        side: int,
        z: float,
        residual_momentum: Optional[float],
        reason: str,
    ) -> None:
        pair_side = "long" if side > 0 else "short"
        recent_max_z = store.get("recent_max_z")
        recent_min_z = store.get("recent_min_z")

        for order in orders:
            print(
                "PEBBLES_L_XL_DEBUG",
                f"timestamp={int(state.timestamp)}",
                f"side={pair_side}",
                f"product={order.symbol}",
                f"quantity={int(order.quantity)}",
                f"price={int(order.price)}",
                f"z={z:.4f}",
                f"recent_max_z={self._fmt_debug_value(recent_max_z)}",
                f"recent_min_z={self._fmt_debug_value(recent_min_z)}",
                f"residual_momentum={self._fmt_debug_value(residual_momentum)}",
                f"reason={reason}",
            )

    def _fmt_debug_value(self, value) -> str:
        if value is None:
            return "None"
        try:
            return f"{float(value):.4f}"
        except Exception:
            return str(value)

    def _clip(self, x: int, lo: int, hi: int) -> int:
        return max(lo, min(hi, x))

    def _append_orders(self, result: Dict[str, List[Order]], orders: List[Order]) -> None:
        for order in orders:
            result.setdefault(order.symbol, []).append(order)


if __name__ == "__main__":
    from round5_dashboard_export import main

    main(Trader, __file__)
