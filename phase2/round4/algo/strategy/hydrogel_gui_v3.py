from __future__ import annotations

"""
HYDROGEL_PACK-only trend-aware mean-reversion market maker, v3.

Core idea:
- Use a slow anchor to detect cheap/expensive regimes.
- Wait for short-term momentum confirmation before crossing the spread.
- Run passive quotes most ticks, skewed by signal and inventory.
- Never trade VELVETFRUIT_EXTRACT or VEV vouchers in this file.

v3 backs away from v2's too-tight passive quoting. It keeps v1-style
confirmation, adds only moderate inside-spread participation, and uses a
Mark 14 guard to avoid leaning into the most adverse observed HGP flow.
"""

import json
import math
from typing import Any, Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ModuleNotFoundError:
    from prosperity3bt.datamodel import Order, OrderDepth, TradingState


PRODUCT = "HYDROGEL_PACK"
HYDROGEL_LIMIT = 200

# Indicator windows.
SLOW_WINDOW = 420
MED_WINDOW = 160
FAST_EMA_WINDOW = 18
ROLLING_STD_WINDOW = 300
HISTORY_LIMIT = 520
MIN_ACTIVE_HISTORY = 120

FAST_MOM_WINDOW = 5
MED_MOM_WINDOW = 20

# Reversal thresholds.
Z_ENTRY = 1.58
Z_STRONG = 2.10
Z_EXIT = 0.35
FAST_MOM_CONFIRM = 0.90
FAST_MOM_STRONG = 2.20
MED_MOM_TOL = 10.0
TREND_TOL = 5.5
ROLL_OVER_MOM = 3.5

# Order sizing and risk.
MAX_ACTIVE_SIZE = 14
MAX_EXIT_SIZE = 18
PASSIVE_SIZE = 10
MIN_PASSIVE_SIZE = 2
INVENTORY_SOFT_LIMIT = 75
INVENTORY_HARD_LIMIT = 130
COOLDOWN_TICKS = 3
SIGNAL_PERSIST_TICKS = 2

# Passive placement. These are offsets from current best bid/ask, not from
# the mid. That keeps us more fillable than v1 without becoming v2's top-tick
# inventory sponge.
DEFAULT_INSIDE_OFFSET = 1
CONFIRMED_INSIDE_OFFSET = 3
WEAK_INSIDE_OFFSET = 2
ADVERSE_INSIDE_OFFSET = 0

# Existing round 4 probes found Mark 14 to be the stronger HGP side. Use only
# flow we directly observe in our fills, and treat it as a temporary side block
# plus a small signal bias rather than as a standalone trade trigger.
MARK_GUARD_TICKS = 55
MARK_BIAS_SIZE = 0.45


class Trader:
    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        memory = self._load_memory(getattr(state, "traderData", ""))

        depth = state.order_depths.get(PRODUCT)
        if depth is None:
            return result, 0, self._dump_memory(memory)

        self._update_counterparty_guard(state, memory)
        book = self._book_snapshot(depth)
        if book is None:
            return result, 0, self._dump_memory(memory)

        position = int(state.position.get(PRODUCT, 0))
        indicators = self._update_indicators(memory, book)
        orders = self._build_orders(depth, book, indicators, memory, position)
        result[PRODUCT] = orders

        memory["last_pos"] = position
        return result, 0, self._dump_memory(memory)

    def _build_orders(
        self,
        depth: OrderDepth,
        book: Dict[str, float],
        indicators: Dict[str, Optional[float]],
        memory: Dict[str, Any],
        position: int,
    ) -> List[Order]:
        orders: List[Order] = []
        buy_sent = 0
        sell_sent = 0

        best_bid = int(book["best_bid"])
        best_ask = int(book["best_ask"])
        spread = best_ask - best_bid
        bid_volume = int(book["bid_volume"])
        ask_volume = int(book["ask_volume"])
        step = int(memory.get("step", 0))

        z = indicators["z"]
        trend = indicators["trend"]
        mom_fast = indicators["mom_fast"]
        mom_med = indicators["mom_med"]
        guard_bias = self._guard_bias(memory)
        long_setup, short_setup = self._reversal_setups(z, trend, mom_fast, mom_med)

        memory["long_streak"] = int(memory.get("long_streak", 0)) + 1 if long_setup else 0
        memory["short_streak"] = int(memory.get("short_streak", 0)) + 1 if short_setup else 0

        def buy_capacity() -> int:
            return max(0, HYDROGEL_LIMIT - position - buy_sent)

        def sell_capacity() -> int:
            return max(0, HYDROGEL_LIMIT + position - sell_sent)

        def add_buy(price: int, quantity: int, visible_cap: Optional[int] = None) -> int:
            nonlocal buy_sent
            cap = buy_capacity()
            if visible_cap is not None:
                cap = min(cap, max(0, visible_cap))
            qty = min(max(0, int(quantity)), cap)
            if qty > 0:
                orders.append(Order(PRODUCT, int(price), int(qty)))
                buy_sent += qty
            return qty

        def add_sell(price: int, quantity: int, visible_cap: Optional[int] = None) -> int:
            nonlocal sell_sent
            cap = sell_capacity()
            if visible_cap is not None:
                cap = min(cap, max(0, visible_cap))
            qty = min(max(0, int(quantity)), cap)
            if qty > 0:
                orders.append(Order(PRODUCT, int(price), -int(qty)))
                sell_sent += qty
            return qty

        last_active_step = int(memory.get("last_active_step", -10_000))
        active_ready = step - last_active_step >= COOLDOWN_TICKS
        active_side = "NONE"

        if active_ready:
            if int(memory.get("long_streak", 0)) >= SIGNAL_PERSIST_TICKS and position < INVENTORY_HARD_LIMIT:
                if not self._guard_blocks(memory, "BUY", position):
                    target = self._target_position("LONG", z, mom_fast)
                    gap = max(0, target - position)
                    qty = min(self._active_size(z, mom_fast), gap)
                    filled_intent = add_buy(best_ask, qty, ask_volume)
                    if filled_intent > 0:
                        memory["last_active_step"] = step
                        active_side = "LONG"
            elif int(memory.get("short_streak", 0)) >= SIGNAL_PERSIST_TICKS and position > -INVENTORY_HARD_LIMIT:
                if not self._guard_blocks(memory, "SELL", position):
                    target = -self._target_position("SHORT", z, mom_fast)
                    gap = max(0, position - target)
                    qty = min(self._active_size(z, mom_fast), gap)
                    filled_intent = add_sell(best_bid, qty, bid_volume)
                    if filled_intent > 0:
                        memory["last_active_step"] = step
                        active_side = "SHORT"

        if active_side == "NONE" and active_ready:
            exit_qty = self._exit_size(position, z, mom_fast)
            if exit_qty > 0 and position > 0:
                filled_intent = add_sell(best_bid, exit_qty, bid_volume)
                if filled_intent > 0:
                    memory["last_active_step"] = step
            elif exit_qty > 0 and position < 0:
                filled_intent = add_buy(best_ask, exit_qty, ask_volume)
                if filled_intent > 0:
                    memory["last_active_step"] = step

        passive_bid, passive_ask = self._passive_prices(best_bid, best_ask, spread, z, mom_fast, position, guard_bias)
        bid_size, ask_size = self._passive_sizes(z, mom_fast, position, guard_bias, memory)

        if passive_bid is not None and passive_bid < best_ask and bid_size > 0:
            add_buy(passive_bid, bid_size)
        if passive_ask is not None and passive_ask > best_bid and ask_size > 0:
            add_sell(passive_ask, ask_size)

        return orders

    def _reversal_setups(
        self,
        z: Optional[float],
        trend: Optional[float],
        mom_fast: Optional[float],
        mom_med: Optional[float],
    ) -> Tuple[bool, bool]:
        if z is None or trend is None or mom_fast is None or mom_med is None:
            return False, False

        cheap = z <= -Z_ENTRY
        expensive = z >= Z_ENTRY
        bouncing = mom_fast >= FAST_MOM_CONFIRM
        rolling_over = mom_fast <= -FAST_MOM_CONFIRM
        not_waterfall = mom_med >= -MED_MOM_TOL
        not_rocket = mom_med <= MED_MOM_TOL

        downtrend_but_reversing = trend < -TREND_TOL and mom_med > 0 and mom_fast >= FAST_MOM_STRONG
        uptrend_but_reversing = trend > TREND_TOL and mom_med < 0 and mom_fast <= -FAST_MOM_STRONG
        trend_allows_long = trend >= -TREND_TOL or downtrend_but_reversing
        trend_allows_short = trend <= TREND_TOL or uptrend_but_reversing

        long_setup = cheap and bouncing and not_waterfall and trend_allows_long
        short_setup = expensive and rolling_over and not_rocket and trend_allows_short
        return long_setup, short_setup

    def _active_size(self, z: Optional[float], mom_fast: Optional[float]) -> int:
        z_abs = abs(z or 0.0)
        mom_abs = abs(mom_fast or 0.0)
        size = 6
        if z_abs >= Z_STRONG:
            size += 5
        if z_abs >= Z_STRONG + 0.45:
            size += 4
        if mom_abs >= FAST_MOM_STRONG:
            size += 3
        return min(MAX_ACTIVE_SIZE, max(1, size))

    def _target_position(self, side: str, z: Optional[float], mom_fast: Optional[float]) -> int:
        z_abs = abs(z or 0.0)
        mom_abs = abs(mom_fast or 0.0)
        target = 45 + int(22 * max(0.0, z_abs - Z_ENTRY)) + int(2.0 * mom_abs)
        if z_abs >= Z_STRONG:
            target += 25
        if side == "LONG":
            return min(INVENTORY_HARD_LIMIT, target)
        return min(INVENTORY_HARD_LIMIT, target)

    def _exit_size(self, position: int, z: Optional[float], mom_fast: Optional[float]) -> int:
        if position == 0 or z is None or mom_fast is None:
            return 0
        neutral = abs(z) <= Z_EXIT
        adverse_roll = position > 0 and mom_fast <= -ROLL_OVER_MOM
        adverse_bounce = position < 0 and mom_fast >= ROLL_OVER_MOM
        hard_inventory = abs(position) >= INVENTORY_HARD_LIMIT

        if not (neutral or adverse_roll or adverse_bounce or hard_inventory):
            return 0

        base = max(1, abs(position) // 5)
        if adverse_roll or adverse_bounce:
            base = max(base, abs(position) // 3)
        if hard_inventory:
            base = max(base, abs(position) // 2)
        return min(MAX_EXIT_SIZE, base)

    def _passive_prices(
        self,
        best_bid: int,
        best_ask: int,
        spread: int,
        z: Optional[float],
        mom_fast: Optional[float],
        position: int,
        guard_bias: float,
    ) -> Tuple[Optional[int], Optional[int]]:
        if spread <= 0:
            return None, None

        if spread >= 3:
            bid_offset = DEFAULT_INSIDE_OFFSET
            ask_offset = DEFAULT_INSIDE_OFFSET

            bias = self._signal_bias(z, mom_fast)
            combined_bias = max(-1.0, min(1.0, bias + 0.60 * guard_bias))
            if combined_bias > 0.45:
                bid_offset = CONFIRMED_INSIDE_OFFSET
                ask_offset = max(ADVERSE_INSIDE_OFFSET, ask_offset - 1)
            elif combined_bias < -0.45:
                ask_offset = CONFIRMED_INSIDE_OFFSET
                bid_offset = max(ADVERSE_INSIDE_OFFSET, bid_offset - 1)
            elif combined_bias > 0.20:
                bid_offset = WEAK_INSIDE_OFFSET
            elif combined_bias < -0.20:
                ask_offset = WEAK_INSIDE_OFFSET

            if z is not None and mom_fast is not None:
                if z <= -Z_ENTRY and mom_fast < 0:
                    bid_offset = ADVERSE_INSIDE_OFFSET
                if z >= Z_ENTRY and mom_fast > 0:
                    ask_offset = ADVERSE_INSIDE_OFFSET

            inv_ratio = position / HYDROGEL_LIMIT
            if inv_ratio > 0.35:
                bid_offset = max(ADVERSE_INSIDE_OFFSET, bid_offset - 1)
                ask_offset = max(ask_offset, WEAK_INSIDE_OFFSET)
            elif inv_ratio < -0.35:
                ask_offset = max(ADVERSE_INSIDE_OFFSET, ask_offset - 1)
                bid_offset = max(bid_offset, WEAK_INSIDE_OFFSET)

            bid = min(best_ask - 1, best_bid + bid_offset)
            ask = max(best_bid + 1, best_ask - ask_offset)
        else:
            bid = best_bid
            ask = best_ask

        if abs(position) >= INVENTORY_HARD_LIMIT:
            if position > 0:
                bid = None
                ask = max(best_bid + 1, best_ask - 2 if spread >= 4 else best_ask)
            else:
                ask = None
                bid = min(best_ask - 1, best_bid + 2 if spread >= 4 else best_bid)

        return bid, ask

    def _passive_sizes(
        self,
        z: Optional[float],
        mom_fast: Optional[float],
        position: int,
        guard_bias: float,
        memory: Dict[str, Any],
    ) -> Tuple[int, int]:
        bias = self._signal_bias(z, mom_fast)
        combined_bias = max(-1.0, min(1.0, bias + 0.60 * guard_bias))
        inv_ratio = position / HYDROGEL_LIMIT

        bid_size = PASSIVE_SIZE * (1.0 + 0.75 * combined_bias - 1.45 * inv_ratio)
        ask_size = PASSIVE_SIZE * (1.0 - 0.75 * combined_bias + 1.45 * inv_ratio)

        # Cheap-still-falling and expensive-still-rising are exactly where we
        # want to stop feeding the wrong side passively.
        if z is not None and mom_fast is not None:
            if z <= -Z_ENTRY and mom_fast < 0:
                bid_size *= 0.15
            if z >= Z_ENTRY and mom_fast > 0:
                ask_size *= 0.15

        if position >= INVENTORY_SOFT_LIMIT:
            bid_size *= 0.15
            ask_size *= 1.35
        elif position <= -INVENTORY_SOFT_LIMIT:
            ask_size *= 0.15
            bid_size *= 1.35

        if self._guard_blocks(memory, "BUY", position):
            bid_size = 0
        if self._guard_blocks(memory, "SELL", position):
            ask_size = 0

        if position >= INVENTORY_HARD_LIMIT:
            bid_size = 0
        elif position <= -INVENTORY_HARD_LIMIT:
            ask_size = 0

        return self._clip_size(bid_size), self._clip_size(ask_size)

    def _signal_bias(self, z: Optional[float], mom_fast: Optional[float]) -> float:
        if z is None or mom_fast is None:
            return 0.0
        if z < -0.45 and mom_fast > 0:
            return min(1.0, (-z / Z_ENTRY) * 0.65 + min(mom_fast, 6.0) / 12.0)
        if z > 0.45 and mom_fast < 0:
            return -min(1.0, (z / Z_ENTRY) * 0.65 + min(abs(mom_fast), 6.0) / 12.0)
        if z < -Z_ENTRY and mom_fast < 0:
            return -0.20
        if z > Z_ENTRY and mom_fast > 0:
            return 0.20
        return max(-0.35, min(0.35, -z / (Z_ENTRY * 4.0)))

    def _update_counterparty_guard(self, state: TradingState, memory: Dict[str, Any]) -> None:
        step = int(memory.get("step", 0))
        for trade in state.own_trades.get(PRODUCT, []):
            buyer = str(getattr(trade, "buyer", "") or "")
            seller = str(getattr(trade, "seller", "") or "")
            if buyer == "Mark 14" and seller == "SUBMISSION":
                memory["guard_bias"] = MARK_BIAS_SIZE
                memory["guard_side"] = "NO_SELL"
                memory["guard_until"] = step + MARK_GUARD_TICKS
            elif buyer == "SUBMISSION" and seller == "Mark 14":
                memory["guard_bias"] = -MARK_BIAS_SIZE
                memory["guard_side"] = "NO_BUY"
                memory["guard_until"] = step + MARK_GUARD_TICKS

    def _guard_bias(self, memory: Dict[str, Any]) -> float:
        step = int(memory.get("step", 0))
        until = int(memory.get("guard_until", -1))
        if until < step:
            memory["guard_bias"] = 0.0
            memory["guard_side"] = ""
            return 0.0
        try:
            return float(memory.get("guard_bias", 0.0))
        except (TypeError, ValueError):
            return 0.0

    def _guard_blocks(self, memory: Dict[str, Any], side: str, position: int) -> bool:
        self._guard_bias(memory)
        guard_side = str(memory.get("guard_side", "") or "")
        if side == "BUY" and guard_side == "NO_BUY" and position >= 0:
            return True
        if side == "SELL" and guard_side == "NO_SELL" and position <= 0:
            return True
        return False

    def _clip_size(self, value: float) -> int:
        if value < MIN_PASSIVE_SIZE:
            return 0
        return int(max(0, min(PASSIVE_SIZE * 2, round(value))))

    def _update_indicators(self, memory: Dict[str, Any], book: Dict[str, float]) -> Dict[str, Optional[float]]:
        mids = [float(x) for x in memory.get("mids", []) if isinstance(x, (int, float))]
        mid = float(book["robust_mid"])
        mids.append(mid)
        if len(mids) > HISTORY_LIMIT:
            mids = mids[-HISTORY_LIMIT:]
        memory["mids"] = [round(x, 3) for x in mids]
        memory["step"] = int(memory.get("step", 0)) + 1

        slow = self._ema(memory.get("slow_ema"), mid, self._alpha(SLOW_WINDOW))
        med = self._ema(memory.get("med_ema"), mid, self._alpha(MED_WINDOW))
        fast = self._ema(memory.get("fast_ema"), mid, self._alpha(FAST_EMA_WINDOW))
        memory["slow_ema"] = round(slow, 5)
        memory["med_ema"] = round(med, 5)
        memory["fast_ema"] = round(fast, 5)

        std = self._rolling_std(mids, ROLLING_STD_WINDOW)
        z = (mid - slow) / std if std is not None and std > 1e-9 and len(mids) >= MIN_ACTIVE_HISTORY else None
        mom_fast = self._momentum(mids, FAST_MOM_WINDOW)
        mom_med = self._momentum(mids, MED_MOM_WINDOW)
        trend = med - slow

        return {
            "z": z,
            "trend": trend,
            "mom_fast": mom_fast,
            "mom_med": mom_med,
            "slow": slow,
            "med": med,
            "fast": fast,
        }

    def _book_snapshot(self, depth: OrderDepth) -> Optional[Dict[str, float]]:
        if not depth.buy_orders or not depth.sell_orders:
            return None

        best_bid = max(depth.buy_orders)
        best_ask = min(depth.sell_orders)
        bid_volume = max(0, int(depth.buy_orders[best_bid]))
        ask_volume = max(0, -int(depth.sell_orders[best_ask]))
        mid = (best_bid + best_ask) / 2.0

        wall_bid_price, _ = max(
            ((int(price), max(0, int(volume))) for price, volume in depth.buy_orders.items()),
            key=lambda item: (item[1], item[0]),
        )
        wall_ask_price, _ = max(
            ((int(price), max(0, -int(volume))) for price, volume in depth.sell_orders.items()),
            key=lambda item: (item[1], -item[0]),
        )
        wall_mid = (wall_bid_price + wall_ask_price) / 2.0
        robust_mid = 0.70 * mid + 0.30 * wall_mid

        return {
            "best_bid": float(best_bid),
            "best_ask": float(best_ask),
            "bid_volume": float(bid_volume),
            "ask_volume": float(ask_volume),
            "mid": mid,
            "wall_mid": wall_mid,
            "robust_mid": robust_mid,
        }

    def _load_memory(self, trader_data: str) -> Dict[str, Any]:
        try:
            parsed = json.loads(trader_data) if trader_data else {}
        except Exception:
            parsed = {}
        if not isinstance(parsed, dict):
            return {}
        return parsed

    def _dump_memory(self, memory: Dict[str, Any]) -> str:
        if len(memory.get("mids", [])) > HISTORY_LIMIT:
            memory["mids"] = memory["mids"][-HISTORY_LIMIT:]
        return json.dumps(memory, separators=(",", ":"))

    def _ema(self, previous: Any, value: float, alpha: float) -> float:
        try:
            prev = float(previous)
        except (TypeError, ValueError):
            return value
        return alpha * value + (1.0 - alpha) * prev

    def _alpha(self, window: int) -> float:
        return 2.0 / (window + 1.0)

    def _momentum(self, values: List[float], window: int) -> Optional[float]:
        if len(values) <= window:
            return None
        return values[-1] - values[-1 - window]

    def _rolling_std(self, values: List[float], window: int) -> Optional[float]:
        if len(values) < max(2, window):
            return None
        sample = values[-window:]
        mean = sum(sample) / len(sample)
        variance = sum((x - mean) ** 2 for x in sample) / (len(sample) - 1)
        return math.sqrt(max(variance, 1e-12))
