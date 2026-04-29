from __future__ import annotations

"""
HYDROGEL_PACK-only trend-aware mean-reversion market maker.

This version is based on the best-performing conservative v1 shape:
- passive market making is the primary edge;
- small inventory is allowed to breathe;
- active crossing is restricted so small-position target changes do not pay the spread unnecessarily;
- passive quote sizes include mild trailing/de-risk behaviour;
- own-trade flow can be used, but with lower weight to avoid self-feedback loops.
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
Z_MEDIUM = 1.28
Z_ENTRY = 1.62
Z_STRONG = 2.15
ACTIVE_REVERSAL_Z = 2.05
Z_EXIT = 0.35
FAST_MOM_CONFIRM = 0.85
FAST_MOM_STRONG = 2.4
MED_MOM_TOL = 9.0
TREND_TOL = 4.0
ROLL_OVER_MOM = 3.5

# Light continuation mode. This is deliberately mostly passive; crossing in
# the direction of trend is tiny and slow because HYDROGEL_PACK spread is wide.
TREND_MOM_MIN = 4.0
TREND_EXTREME_Z = 1.85
TREND_TARGET = 34
TREND_STRONG_TARGET = 52
FLOW_TARGET = 28
MEDIUM_REVERSAL_TARGET = 62
STRONG_REVERSAL_TARGET = 112

# Order sizing and risk.
MAX_ACTIVE_SIZE = 16
MAX_MEDIUM_ACTIVE_SIZE = 6
MAX_TREND_ACTIVE_SIZE = 3
MAX_EXIT_SIZE = 18
PASSIVE_SIZE = 12
MIN_PASSIVE_SIZE = 2
INVENTORY_SOFT_LIMIT = 95
INVENTORY_HARD_LIMIT = 160
COOLDOWN_TICKS = 3
TREND_ACTIVE_COOLDOWN_TICKS = 12
SIGNAL_PERSIST_TICKS = 2
ACTIVE_TARGET_GAP = 18
TARGET_DEADBAND = 8

# Lifecycle and inventory controls.
PARTIAL_EXIT_Z = 0.90
WRONG_WAY_MED_MOM = 11.0
MIN_RISK_EXIT_POSITION = 45
PASSIVE_TARGET_SCALE = 58.0

# Small-position active crossing guard.
# The best v1 run made money mostly from passive fills and carried small
# inventory. Do not pay the spread to flip/reduce tiny positions unless the
# reversal is genuinely extreme.
SMALL_POSITION_ACTIVE_BLOCK = 25
SMALL_POSITION_EXTREME_Z = Z_STRONG + 0.35
SMALL_POSITION_EXTREME_MOM = FAST_MOM_STRONG + 0.4

# Mild passive trailing protection. This is intentionally not the aggressive
# v2 de-risking patch; it only changes passive quote skew unless inventory is
# already meaningful.
PASSIVE_TRAIL_MOM = 3.2
PASSIVE_TRAIL_POSITION = 18
PASSIVE_TRAIL_STRONG_POSITION = 30

# Recent public/own flow is a regime hint, not a standalone reason to cross.
FLOW_WINDOW_TICKS = 70
FLOW_MARK14_BIAS = 0.60
FLOW_MARK38_BIAS = 0.45
USE_OWN_TRADES_FOR_FLOW = True
OWN_FLOW_WEIGHT = 0.25

# Keep false for submissions. Flip true locally when you want compact logs.
ENABLE_DEBUG_LOGS = False
DEBUG_EVERY_TICKS = 100
DEBUG_ONLY_WHEN_ORDER = True


class Trader:
    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        memory = self._load_memory(getattr(state, "traderData", ""))

        depth = state.order_depths.get(PRODUCT)
        if depth is None:
            return result, 0, self._dump_memory(memory)

        self._update_flow_signals(state, memory)
        book = self._book_snapshot(depth)
        if book is None:
            return result, 0, self._dump_memory(memory)

        position = int(state.position.get(PRODUCT, 0))
        indicators = self._update_indicators(memory, book)
        orders, diagnostics = self._build_orders(depth, book, indicators, memory, position)
        result[PRODUCT] = orders

        self._debug_log(state, book, indicators, diagnostics, orders)
        memory["last_pos"] = position
        return result, 0, self._dump_memory(memory)

    def _build_orders(
        self,
        depth: OrderDepth,
        book: Dict[str, float],
        indicators: Dict[str, Optional[float]],
        memory: Dict[str, Any],
        position: int,
    ) -> Tuple[List[Order], Dict[str, Any]]:
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
        plan = self._signal_plan(z, trend, mom_fast, mom_med, position, memory)
        long_setup = plan["active_side"] == "BUY" and plan["active_quality"] in {"medium_reversal", "strong_reversal"}
        short_setup = plan["active_side"] == "SELL" and plan["active_quality"] in {"medium_reversal", "strong_reversal"}

        memory["long_streak"] = int(memory.get("long_streak", 0)) + 1 if long_setup else 0
        memory["short_streak"] = int(memory.get("short_streak", 0)) + 1 if short_setup else 0
        memory["uptrend_streak"] = int(memory.get("uptrend_streak", 0)) + 1 if plan["active_quality"] == "trend_up" else 0
        memory["downtrend_streak"] = int(memory.get("downtrend_streak", 0)) + 1 if plan["active_quality"] == "trend_down" else 0

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
        active_cooldown = TREND_ACTIVE_COOLDOWN_TICKS if plan["active_quality"].startswith("trend_") else COOLDOWN_TICKS
        active_ready = step - last_active_step >= active_cooldown
        active_side = "NONE"
        active_reason = "none"

        # Conservative active risk exit from the best v1 strategy.
        # This is deliberately not the more aggressive v2 trailing-stop logic.
        exit_side, exit_qty, exit_reason = self._risk_exit(position, z, mom_fast, mom_med)
        if active_ready and exit_qty > 0:
            if exit_side == "SELL":
                filled_intent = add_sell(best_bid, exit_qty, bid_volume)
                if filled_intent > 0:
                    memory["last_active_step"] = step
                    active_side = "RISK_SELL"
                    active_reason = exit_reason
            elif exit_side == "BUY":
                filled_intent = add_buy(best_ask, exit_qty, ask_volume)
                if filled_intent > 0:
                    memory["last_active_step"] = step
                    active_side = "RISK_BUY"
                    active_reason = exit_reason

        if active_side == "NONE" and active_ready:
            target = int(plan["target"])
            gap = target - position
            wants_buy = gap >= ACTIVE_TARGET_GAP and position < INVENTORY_HARD_LIMIT
            wants_sell = gap <= -ACTIVE_TARGET_GAP and position > -INVENTORY_HARD_LIMIT
            can_buy = plan["active_side"] == "BUY" and wants_buy
            can_sell = plan["active_side"] == "SELL" and wants_sell

            if plan["active_quality"] == "medium_reversal":
                can_buy = can_buy and int(memory.get("long_streak", 0)) >= SIGNAL_PERSIST_TICKS
                can_sell = can_sell and int(memory.get("short_streak", 0)) >= SIGNAL_PERSIST_TICKS
            elif plan["active_quality"] == "strong_reversal":
                can_buy = can_buy and int(memory.get("long_streak", 0)) >= 1
                can_sell = can_sell and int(memory.get("short_streak", 0)) >= 1
            elif plan["active_quality"] == "trend_up":
                can_buy = can_buy and int(memory.get("uptrend_streak", 0)) >= 3
            elif plan["active_quality"] == "trend_down":
                can_sell = can_sell and int(memory.get("downtrend_streak", 0)) >= 3

            # Patch: avoid paying spread for small-position target changes.
            if can_buy and not self._active_cross_allowed("BUY", position, target, plan, z, mom_fast):
                can_buy = False
            if can_sell and not self._active_cross_allowed("SELL", position, target, plan, z, mom_fast):
                can_sell = False

            if can_buy:
                qty = min(self._planned_active_size(plan, z, mom_fast), gap)
                filled_intent = add_buy(best_ask, qty, ask_volume)
                if filled_intent > 0:
                    memory["last_active_step"] = step
                    active_side = "BUY"
                    active_reason = str(plan["reason"])
            elif can_sell:
                qty = min(self._planned_active_size(plan, z, mom_fast), -gap)
                filled_intent = add_sell(best_bid, qty, bid_volume)
                if filled_intent > 0:
                    memory["last_active_step"] = step
                    active_side = "SELL"
                    active_reason = str(plan["reason"])

        passive_bid, passive_ask = self._passive_prices(best_bid, best_ask, spread, z, mom_fast, position, plan)
        bid_size, ask_size = self._passive_sizes(z, mom_fast, mom_med, position, plan)

        if passive_bid is not None and passive_bid < best_ask and bid_size > 0:
            add_buy(passive_bid, bid_size)
        if passive_ask is not None and passive_ask > best_bid and ask_size > 0:
            add_sell(passive_ask, ask_size)

        no_order_reason = "orders_sent"
        if not orders:
            if spread <= 0:
                no_order_reason = "bad_spread"
            elif buy_capacity() <= 0 and sell_capacity() <= 0:
                no_order_reason = "both_limits"
            elif bid_size <= 0 and ask_size <= 0:
                no_order_reason = "passive_sizes_zero"
            elif not active_ready and plan["active_side"] != "NONE":
                no_order_reason = "cooldown"
            else:
                no_order_reason = "no_signal_or_filtered"

        diagnostics = {
            "mode": plan["mode"],
            "target": int(plan["target"]),
            "active_side": active_side,
            "active_reason": active_reason,
            "plan_reason": plan["reason"],
            "passive_bid": passive_bid,
            "passive_ask": passive_ask,
            "bid_size": bid_size,
            "ask_size": ask_size,
            "no_order_reason": no_order_reason,
            "flow_bias": self._flow_bias(memory),
        }
        return orders, diagnostics

    def _active_cross_allowed(
        self,
        side: str,
        position: int,
        target: int,
        plan: Dict[str, Any],
        z: Optional[float],
        mom_fast: Optional[float],
    ) -> bool:
        """Restrict active crossing for small positions.

        This targets the observed bad active sell around 53.1k in the best run:
        a small-ish position was crossed at the bid and paid large spread. The
        edge in this strategy is mostly passive, so small inventory changes should
        normally be handled by passive quote skew.
        """
        quality = str(plan.get("active_quality", "none"))
        z_abs = abs(z or 0.0)
        mom_abs = abs(mom_fast or 0.0)

        # Risk reduction for genuinely meaningful inventory is allowed.
        if side == "BUY" and position <= -SMALL_POSITION_ACTIVE_BLOCK:
            return True
        if side == "SELL" and position >= SMALL_POSITION_ACTIVE_BLOCK:
            return True

        # Small position: only cross on a truly extreme strong reversal.
        if abs(position) < SMALL_POSITION_ACTIVE_BLOCK:
            return (
                quality == "strong_reversal"
                and z_abs >= SMALL_POSITION_EXTREME_Z
                and mom_abs >= SMALL_POSITION_EXTREME_MOM
            )

        # Medium inventory but not risk-reducing: require strong reversal quality.
        return quality == "strong_reversal" and z_abs >= ACTIVE_REVERSAL_Z

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

    def _signal_plan(
        self,
        z: Optional[float],
        trend: Optional[float],
        mom_fast: Optional[float],
        mom_med: Optional[float],
        position: int,
        memory: Dict[str, Any],
    ) -> Dict[str, Any]:
        flow_bias = self._flow_bias(memory)
        target = int(round(FLOW_TARGET * flow_bias))
        plan = {
            "mode": "warmup",
            "target": target,
            "active_side": "NONE",
            "active_quality": "none",
            "passive_bias": max(-1.0, min(1.0, 0.40 * flow_bias)),
            "reason": "warming_indicators",
        }
        if z is None or trend is None or mom_fast is None or mom_med is None:
            return plan

        uptrend = trend >= TREND_TOL and mom_med >= TREND_MOM_MIN and mom_fast >= -FAST_MOM_CONFIRM
        downtrend = trend <= -TREND_TOL and mom_med <= -TREND_MOM_MIN and mom_fast <= FAST_MOM_CONFIRM
        strong_uptrend = trend >= TREND_TOL * 1.65 and mom_med >= TREND_MOM_MIN * 1.6
        strong_downtrend = trend <= -TREND_TOL * 1.65 and mom_med <= -TREND_MOM_MIN * 1.6

        cheap = z <= -Z_MEDIUM
        expensive = z >= Z_MEDIUM
        bouncing = mom_fast >= FAST_MOM_CONFIRM
        rolling_over = mom_fast <= -FAST_MOM_CONFIRM
        weak_bounce = mom_fast >= FAST_MOM_CONFIRM * 0.55
        weak_rollover = mom_fast <= -FAST_MOM_CONFIRM * 0.55
        not_waterfall = mom_med >= -MED_MOM_TOL
        not_rocket = mom_med <= MED_MOM_TOL

        trend_allows_long = trend >= -TREND_TOL or (mom_med > 0 and mom_fast >= FAST_MOM_STRONG)
        trend_allows_short = trend <= TREND_TOL or (mom_med < 0 and mom_fast <= -FAST_MOM_STRONG)

        if z <= -ACTIVE_REVERSAL_Z and bouncing and not_waterfall and trend_allows_long:
            target = STRONG_REVERSAL_TARGET
            if abs(z) >= Z_STRONG or abs(mom_fast) >= FAST_MOM_STRONG:
                target = min(INVENTORY_HARD_LIMIT, target + 20)
            plan.update(
                mode="reversal_long",
                target=target,
                active_side="BUY",
                active_quality="strong_reversal",
                passive_bias=0.95,
                reason="strong_confirmed_long_reversal",
            )
        elif z >= ACTIVE_REVERSAL_Z and rolling_over and not_rocket and trend_allows_short:
            target = -STRONG_REVERSAL_TARGET
            if abs(z) >= Z_STRONG or abs(mom_fast) >= FAST_MOM_STRONG:
                target = max(-INVENTORY_HARD_LIMIT, target - 20)
            plan.update(
                mode="reversal_short",
                target=target,
                active_side="SELL",
                active_quality="strong_reversal",
                passive_bias=-0.95,
                reason="strong_confirmed_short_reversal",
            )
        elif cheap and weak_bounce and mom_med >= -MED_MOM_TOL * 1.45:
            plan.update(
                mode="reversal_long_medium",
                target=MEDIUM_REVERSAL_TARGET,
                active_side="NONE",
                active_quality="medium_reversal",
                passive_bias=0.70,
                reason="medium_long_reversal_passive_only",
            )
        elif expensive and weak_rollover and mom_med <= MED_MOM_TOL * 1.45:
            plan.update(
                mode="reversal_short_medium",
                target=-MEDIUM_REVERSAL_TARGET,
                active_side="NONE",
                active_quality="medium_reversal",
                passive_bias=-0.70,
                reason="medium_short_reversal_passive_only",
            )
        elif uptrend and z < TREND_EXTREME_Z:
            target = TREND_STRONG_TARGET if strong_uptrend else TREND_TARGET
            target = max(target, int(round(FLOW_TARGET * max(0.0, flow_bias))))
            plan.update(
                mode="trend_up",
                target=target,
                active_side="NONE",
                active_quality="trend_up",
                passive_bias=0.45 + max(0.0, 0.25 * flow_bias),
                reason="light_uptrend_continuation_passive_only",
            )
        elif downtrend and z > -TREND_EXTREME_Z:
            target = -(TREND_STRONG_TARGET if strong_downtrend else TREND_TARGET)
            target = min(target, int(round(FLOW_TARGET * min(0.0, flow_bias))))
            plan.update(
                mode="trend_down",
                target=target,
                active_side="NONE",
                active_quality="trend_down",
                passive_bias=-0.45 + min(0.0, 0.25 * flow_bias),
                reason="light_downtrend_continuation_passive_only",
            )
        elif abs(flow_bias) >= 0.35:
            plan.update(
                mode="flow_bias",
                target=int(round(FLOW_TARGET * flow_bias)),
                active_side="NONE",
                active_quality="flow",
                passive_bias=max(-0.65, min(0.65, flow_bias)),
                reason="recent_mark14_mark38_flow",
            )
        else:
            plan.update(
                mode="choppy",
                target=0,
                active_side="NONE",
                active_quality="none",
                passive_bias=self._signal_bias(z, mom_fast),
                reason="normal_passive_mm",
            )

        plan["target"] = self._lifecycle_target(int(plan["target"]), position, z, mom_fast, mom_med, plan)
        plan["target"] = int(max(-INVENTORY_HARD_LIMIT, min(INVENTORY_HARD_LIMIT, plan["target"])))
        return plan

    def _lifecycle_target(
        self,
        target: int,
        position: int,
        z: float,
        mom_fast: float,
        mom_med: float,
        plan: Dict[str, Any],
    ) -> int:
        if position == 0:
            return target

        same_side_long = target > position and plan["active_side"] == "BUY"
        same_side_short = target < position and plan["active_side"] == "SELL"

        if position > 0 and not same_side_long:
            if z >= -PARTIAL_EXIT_Z:
                target = min(target, max(0, int(round(position * 0.65))))
                plan["reason"] = f"{plan['reason']}+partial_long_take_profit"
            if abs(z) <= Z_EXIT:
                target = min(target, 0)
                plan["reason"] = f"{plan['reason']}+neutral_long_exit"
            if mom_fast <= -ROLL_OVER_MOM or (position >= INVENTORY_SOFT_LIMIT and mom_med <= -WRONG_WAY_MED_MOM):
                target = min(target, max(0, int(round(position * 0.35))))
                plan["reason"] = f"{plan['reason']}+wrong_way_long_risk"
        elif position < 0 and not same_side_short:
            if z <= PARTIAL_EXIT_Z:
                target = max(target, min(0, int(round(position * 0.65))))
                plan["reason"] = f"{plan['reason']}+partial_short_take_profit"
            if abs(z) <= Z_EXIT:
                target = max(target, 0)
                plan["reason"] = f"{plan['reason']}+neutral_short_exit"
            if mom_fast >= ROLL_OVER_MOM or (position <= -INVENTORY_SOFT_LIMIT and mom_med >= WRONG_WAY_MED_MOM):
                target = max(target, min(0, int(round(position * 0.35))))
                plan["reason"] = f"{plan['reason']}+wrong_way_short_risk"

        if abs(target - position) < TARGET_DEADBAND:
            target = position
        return target

    def _planned_active_size(self, plan: Dict[str, Any], z: Optional[float], mom_fast: Optional[float]) -> int:
        quality = str(plan.get("active_quality", "none"))
        if quality.startswith("trend_"):
            return MAX_TREND_ACTIVE_SIZE
        if quality == "medium_reversal":
            return min(MAX_MEDIUM_ACTIVE_SIZE, max(2, self._active_size(z, mom_fast) // 2))
        if quality == "strong_reversal":
            return self._active_size(z, mom_fast)
        return 0

    def _risk_exit(
        self,
        position: int,
        z: Optional[float],
        mom_fast: Optional[float],
        mom_med: Optional[float],
    ) -> Tuple[str, int, str]:
        if position == 0 or z is None or mom_fast is None or mom_med is None:
            return "NONE", 0, "none"

        hard_inventory = abs(position) >= INVENTORY_HARD_LIMIT
        soft_wrong_way_long = position > 0 and abs(position) >= INVENTORY_SOFT_LIMIT and mom_med <= -WRONG_WAY_MED_MOM
        soft_wrong_way_short = position < 0 and abs(position) >= INVENTORY_SOFT_LIMIT and mom_med >= WRONG_WAY_MED_MOM
        fast_against_long = position > 0 and abs(position) >= MIN_RISK_EXIT_POSITION and mom_fast <= -ROLL_OVER_MOM
        fast_against_short = position < 0 and abs(position) >= MIN_RISK_EXIT_POSITION and mom_fast >= ROLL_OVER_MOM
        neutral_large = abs(z) <= Z_EXIT and abs(position) >= INVENTORY_SOFT_LIMIT

        if not (hard_inventory or soft_wrong_way_long or soft_wrong_way_short or fast_against_long or fast_against_short or neutral_large):
            return "NONE", 0, "none"

        base = max(2, abs(position) // 6)
        reason = "staged_exit"
        if fast_against_long or fast_against_short:
            base = max(base, abs(position) // 4)
            reason = "fast_momentum_against_inventory"
        if soft_wrong_way_long or soft_wrong_way_short:
            base = max(base, abs(position) // 3)
            reason = "wrong_way_medium_momentum"
        if hard_inventory:
            base = max(base, abs(position) // 2)
            reason = "hard_inventory_exit"

        side = "SELL" if position > 0 else "BUY"
        return side, min(MAX_EXIT_SIZE, base), reason

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
        plan: Dict[str, Any],
    ) -> Tuple[Optional[int], Optional[int]]:
        if spread <= 0:
            return None, None

        if spread >= 3:
            bid = best_bid + 1
            ask = best_ask - 1
        else:
            bid = best_bid
            ask = best_ask

        bias = float(plan.get("passive_bias", self._signal_bias(z, mom_fast)))
        inv_ratio = position / HYDROGEL_LIMIT
        target_gap = int(plan.get("target", 0)) - position

        if (bias > 0.35 or target_gap > 20) and spread >= 5:
            bid = min(best_ask - 1, bid + 1)
        elif (bias < -0.35 or target_gap < -20) and spread >= 5:
            ask = max(best_bid + 1, ask - 1)

        # Passive trailing protection: when small/medium inventory is suddenly
        # wrong-way, adjust quotes rather than crossing the spread.
        if mom_fast is not None and abs(position) >= PASSIVE_TRAIL_POSITION:
            if position > 0 and mom_fast <= -PASSIVE_TRAIL_MOM and spread >= 4:
                ask = max(best_bid + 1, ask - 1)
            elif position < 0 and mom_fast >= PASSIVE_TRAIL_MOM and spread >= 4:
                bid = min(best_ask - 1, bid + 1)

        if inv_ratio > 0.35:
            ask = max(best_bid + 1, ask - 1)
        elif inv_ratio < -0.35:
            bid = min(best_ask - 1, bid + 1)

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
        mom_med: Optional[float],
        position: int,
        plan: Dict[str, Any],
    ) -> Tuple[int, int]:
        bias = float(plan.get("passive_bias", self._signal_bias(z, mom_fast)))
        inv_ratio = position / HYDROGEL_LIMIT
        target_gap = int(plan.get("target", 0)) - position
        target_bias = max(-1.0, min(1.0, target_gap / PASSIVE_TARGET_SCALE))

        bid_size = PASSIVE_SIZE * (1.0 + 0.65 * bias + 0.85 * target_bias - 1.15 * inv_ratio)
        ask_size = PASSIVE_SIZE * (1.0 - 0.65 * bias - 0.85 * target_bias + 1.15 * inv_ratio)

        # Cheap-still-falling and expensive-still-rising are exactly where we
        # avoid active reversal, but do not go fully silent: keep the trend or
        # risk-reducing side alive.
        if z is not None and mom_fast is not None:
            if z <= -Z_ENTRY and mom_fast < 0:
                bid_size *= 0.35
                ask_size *= 1.15
            if z >= Z_ENTRY and mom_fast > 0:
                ask_size *= 0.35
                bid_size *= 1.15

        # Mild passive-only trailing guard.
        # Do not aggressively cross for small inventory; just stop adding and
        # make the exit side larger/more available.
        if mom_fast is not None and mom_med is not None:
            if position > 0 and mom_fast <= -PASSIVE_TRAIL_MOM:
                bid_size *= 0.30
                ask_size *= 1.35
                if position >= PASSIVE_TRAIL_STRONG_POSITION and mom_med < 0:
                    bid_size *= 0.35
                    ask_size *= 1.20
            elif position < 0 and mom_fast >= PASSIVE_TRAIL_MOM:
                ask_size *= 0.30
                bid_size *= 1.35
                if abs(position) >= PASSIVE_TRAIL_STRONG_POSITION and mom_med > 0:
                    ask_size *= 0.35
                    bid_size *= 1.20

        if position >= INVENTORY_SOFT_LIMIT:
            bid_size *= 0.20
            ask_size *= 1.35
        elif position <= -INVENTORY_SOFT_LIMIT:
            ask_size *= 0.20
            bid_size *= 1.35

        if position >= INVENTORY_HARD_LIMIT:
            bid_size = 0
        elif position <= -INVENTORY_HARD_LIMIT:
            ask_size = 0

        # If both sides were reduced by interacting filters, keep the side that
        # moves inventory toward target unless the hard limit blocks it.
        if bid_size < MIN_PASSIVE_SIZE and ask_size < MIN_PASSIVE_SIZE:
            if target_gap > 0 and position < INVENTORY_HARD_LIMIT:
                bid_size = MIN_PASSIVE_SIZE
            elif target_gap < 0 and position > -INVENTORY_HARD_LIMIT:
                ask_size = MIN_PASSIVE_SIZE
            elif position > 0:
                ask_size = MIN_PASSIVE_SIZE
            elif position < 0:
                bid_size = MIN_PASSIVE_SIZE
            else:
                bid_size = MIN_PASSIVE_SIZE
                ask_size = MIN_PASSIVE_SIZE

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

    def _update_flow_signals(self, state: TradingState, memory: Dict[str, Any]) -> None:
        step = int(memory.get("step", 0))
        current_bias = self._flow_bias(memory)
        bias_delta = 0.0
        reason = ""

        books = [("market_trades", 1.0)]
        if USE_OWN_TRADES_FOR_FLOW:
            books.append(("own_trades", OWN_FLOW_WEIGHT))

        for trade_book_name, weight in books:
            trade_book = getattr(state, trade_book_name, {}) or {}
            for trade in trade_book.get(PRODUCT, []):
                buyer = str(getattr(trade, "buyer", "") or "")
                seller = str(getattr(trade, "seller", "") or "")
                if buyer == "Mark 14":
                    bias_delta += FLOW_MARK14_BIAS * weight
                    reason = f"{trade_book_name}:mark14_buy"
                if seller == "Mark 14":
                    bias_delta -= FLOW_MARK14_BIAS * weight
                    reason = f"{trade_book_name}:mark14_sell"
                if buyer == "Mark 38":
                    bias_delta -= FLOW_MARK38_BIAS * weight
                    reason = f"{trade_book_name}:fade_mark38_buy"
                if seller == "Mark 38":
                    bias_delta += FLOW_MARK38_BIAS * weight
                    reason = f"{trade_book_name}:fade_mark38_sell"

        if abs(bias_delta) > 1e-9:
            memory["flow_bias"] = round(max(-1.0, min(1.0, current_bias * 0.45 + bias_delta)), 4)
            memory["flow_until"] = step + FLOW_WINDOW_TICKS
            memory["flow_reason"] = reason

    def _flow_bias(self, memory: Dict[str, Any]) -> float:
        step = int(memory.get("step", 0))
        until = int(memory.get("flow_until", -1))
        if until < step:
            memory["flow_bias"] = 0.0
            return 0.0
        try:
            return float(memory.get("flow_bias", 0.0))
        except (TypeError, ValueError):
            return 0.0

    def _debug_log(
        self,
        state: TradingState,
        book: Dict[str, float],
        indicators: Dict[str, Optional[float]],
        diagnostics: Dict[str, Any],
        orders: List[Order],
    ) -> None:
        if not ENABLE_DEBUG_LOGS:
            return
        if DEBUG_ONLY_WHEN_ORDER and not orders:
            return

        try:
            step = int(json.loads(getattr(state, "traderData", "") or "{}").get("step", 0))
        except Exception:
            step = 0
        if not DEBUG_ONLY_WHEN_ORDER and step % DEBUG_EVERY_TICKS != 0:
            return

        order_str = ",".join(f"{order.price}:{order.quantity}" for order in orders)
        fields = {
            "timestamp": int(getattr(state, "timestamp", 0)),
            "mid": round(float(book.get("mid", 0.0)), 3),
            "wall_mid": round(float(book.get("wall_mid", 0.0)), 3),
            "position": int(getattr(state, "position", {}).get(PRODUCT, 0)),
            "z": self._fmt_diag(indicators.get("z")),
            "mom_fast": self._fmt_diag(indicators.get("mom_fast")),
            "mom_med": self._fmt_diag(indicators.get("mom_med")),
            "trend": self._fmt_diag(indicators.get("trend")),
            "mode": diagnostics.get("mode"),
            "target": diagnostics.get("target"),
            "active": diagnostics.get("active_side"),
            "active_reason": diagnostics.get("active_reason"),
            "plan_reason": diagnostics.get("plan_reason"),
            "passive_bid": diagnostics.get("passive_bid"),
            "passive_ask": diagnostics.get("passive_ask"),
            "bid_size": diagnostics.get("bid_size"),
            "ask_size": diagnostics.get("ask_size"),
            "flow_bias": diagnostics.get("flow_bias"),
            "orders": order_str,
            "no_order_reason": diagnostics.get("no_order_reason"),
        }
        print("HGP_DIAG|" + "|".join(f"{key}={value}" for key, value in fields.items()))

    def _fmt_diag(self, value: Optional[float]) -> str:
        if value is None:
            return "na"
        return f"{float(value):.4f}"

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
