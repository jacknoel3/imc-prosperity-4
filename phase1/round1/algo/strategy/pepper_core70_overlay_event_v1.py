import argparse
import csv
import json
import math
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from datamodel import Observation, Order, OrderDepth, TradingState


PEPPER = "INTARIAN_PEPPER_ROOT"
POSITION_LIMIT = 80
CORE_POSITION = 70
OVERLAY_LIMIT = POSITION_LIMIT - CORE_POSITION
EVENT_EXPERIMENT_MAX_SIZE = 2
EVENT_COOLDOWN_STEPS = 3

DEFAULT_PRICES_PATH = "data/round1/prices_round_1_day_0.csv"
DEFAULT_TRADES_PATH = "data/round1/trades_round_1_day_0.csv"
DEFAULT_OUTPUT_PATH = "phase1/round1/algo/dashboard/examples/backtest_trades_pepper_core70_overlay_event_v1_day_0_generated.csv"
DEFAULT_OUTPUT_TEMPLATE = "phase1/round1/algo/dashboard/examples/backtest_trades_pepper_core70_overlay_event_v1_day_{day}_generated.csv"


class Trader:
    def run(self, state: TradingState):
        trader_state = self._load_state(state.traderData)
        pepper_state = trader_state.get(PEPPER, {})
        result: Dict[str, List[Order]] = {}

        for product, depth in state.order_depths.items():
            if product == PEPPER:
                orders, pepper_state = self._trade_pepper(
                    depth=depth,
                    position=state.position.get(PEPPER, 0),
                    prev_state=pepper_state,
                    timestamp=int(state.timestamp),
                )
                result[product] = orders
            else:
                result[product] = []

        new_state = json.dumps({PEPPER: pepper_state}, separators=(",", ":"))
        return result, 0, new_state

    def _trade_pepper(
        self,
        depth: OrderDepth,
        position: int,
        prev_state: Dict[str, float],
        timestamp: int,
    ) -> Tuple[List[Order], Dict[str, float]]:
        orders: List[Order] = []

        has_bids = bool(depth.buy_orders)
        has_asks = bool(depth.sell_orders)
        regime = self._book_regime(depth)

        prev_timestamp = int(prev_state.get("last_timestamp", timestamp))
        prev_step = int(prev_state.get("step_index", 0))
        prev_anchor = self._as_float(prev_state.get("anchor"), 0.0)
        prev_mid = self._as_float(prev_state.get("last_mid"), 0.0)
        prev_fair = self._as_float(prev_state.get("fair_value"), prev_mid if prev_mid > 0 else 0.0)
        prev_pressure = self._as_float(prev_state.get("pressure"), 0.0)
        prev_signal = self._as_float(prev_state.get("signal_strength"), 0.0)
        prev_full_bid = self._as_float(prev_state.get("last_full_bid"), 0.0)
        prev_full_ask = self._as_float(prev_state.get("last_full_ask"), 0.0)
        prev_full_mid = self._as_float(prev_state.get("last_full_mid"), prev_mid)
        prev_full_spread = self._as_float(prev_state.get("last_full_spread"), 0.0)
        prev_regime = str(prev_state.get("last_regime", "none"))
        last_event_step = int(prev_state.get("last_event_step", -999999))

        if timestamp < prev_timestamp:
            step_index = 0
            anchor = self._infer_reference_mid(depth, prev_state)
            prev_step = 0
            prev_anchor = anchor
            last_event_step = -999999
        else:
            step_index = max(prev_step, max(0, int(round(timestamp / 100))))
            anchor = prev_anchor if prev_anchor > 0 else self._infer_reference_mid(depth, prev_state)

        reference_mid = self._infer_reference_mid(depth, prev_state)
        if reference_mid <= 0:
            new_state = {
                "last_timestamp": timestamp,
                "last_regime": regime,
                "step_index": step_index,
                "anchor": anchor,
                "fair_value": prev_fair,
                "last_mid": prev_mid,
                "pressure": prev_pressure,
                "signal_strength": prev_signal,
                "last_event_step": last_event_step,
                "last_full_bid": prev_full_bid,
                "last_full_ask": prev_full_ask,
                "last_full_mid": prev_full_mid,
                "last_full_spread": prev_full_spread,
            }
            return orders, new_state

        trend_per_step = 0.1002
        anchor_measurement = reference_mid - trend_per_step * step_index
        if anchor <= 0:
            anchor = anchor_measurement
        else:
            anchor = 0.90 * anchor + 0.10 * anchor_measurement
        trend_fair = anchor + trend_per_step * step_index

        steps_elapsed = max(1, step_index - prev_step) if timestamp >= prev_timestamp else 1
        expected_step_move = trend_per_step * steps_elapsed
        residual_move = (reference_mid - prev_mid) - expected_step_move if prev_mid > 0 else 0.0

        buy_capacity = max(0, POSITION_LIMIT - position)
        overlay_inventory = max(0, position - CORE_POSITION)
        sell_overlay_capacity = overlay_inventory

        if has_bids and has_asks:
            book = self._book_snapshot(depth)
            if book is None:
                new_state = {
                    "last_timestamp": timestamp,
                    "last_regime": regime,
                    "step_index": step_index,
                    "anchor": anchor,
                    "fair_value": prev_fair,
                    "last_mid": reference_mid,
                    "pressure": prev_pressure,
                    "signal_strength": prev_signal,
                    "last_event_step": last_event_step,
                    "last_full_bid": prev_full_bid,
                    "last_full_ask": prev_full_ask,
                    "last_full_mid": prev_full_mid,
                    "last_full_spread": prev_full_spread,
                }
                return orders, new_state

            best_bid, bid_volume, best_ask, ask_volume, mid, spread, imbalance, microprice = book

            instant_pressure = 0.65 * imbalance + 0.35 * (microprice - mid)
            pressure = 0.82 * prev_pressure + 0.18 * instant_pressure

            value_gap = trend_fair - mid
            micro_gap = microprice - mid

            signal_strength = (
                1.10 * value_gap
                + 0.95 * micro_gap
                + 2.20 * imbalance
                - 0.55 * residual_move
                + 1.10 * pressure
            )

            fair_measurement = trend_fair + 0.65 * micro_gap + 1.10 * imbalance + 0.55 * pressure
            fair_value = 0.68 * prev_fair + 0.32 * fair_measurement if prev_fair > 0 else fair_measurement

            overlay_target = self._overlay_target(signal_strength, mid, trend_fair, residual_move)
            buy_target_limit = min(POSITION_LIMIT, CORE_POSITION + overlay_target)

            inventory_ratio = (position - buy_target_limit) / POSITION_LIMIT
            reservation_price = fair_value - 1.35 * inventory_ratio

            # -------------------------
            # Aggressive buys for spare overlay only
            # -------------------------
            for ask_price, ask_volume_signed in sorted(depth.sell_orders.items()):
                available = abs(ask_volume_signed)
                if available <= 0 or position >= buy_target_limit or buy_capacity <= 0:
                    break

                edge = reservation_price - ask_price
                strong_dip_buy = (
                    signal_strength >= 1.45
                    and residual_move <= -0.8
                    and mid <= trend_fair - 0.7
                    and ask_price <= math.ceil(trend_fair) + 1
                )
                should_take = edge >= 1.15 or strong_dip_buy
                if not should_take:
                    break

                clip_cap = 6 if strong_dip_buy else 4
                clip = min(available, buy_capacity, buy_target_limit - position, clip_cap)
                if clip <= 0:
                    continue

                orders.append(Order(PEPPER, ask_price, clip))
                position += clip
                buy_capacity = max(0, POSITION_LIMIT - position)
                overlay_inventory = max(0, position - CORE_POSITION)
                sell_overlay_capacity = overlay_inventory

            # Refresh inventory-aware values after buys
            inventory_ratio = (position - buy_target_limit) / POSITION_LIMIT
            reservation_price = fair_value - 1.35 * inventory_ratio
            rich_bid_threshold = max(math.ceil(trend_fair) + 2, math.ceil(fair_value) + 1)

            # -------------------------
            # Aggressive sells only out of overlay inventory
            # -------------------------
            for bid_price, bid_volume_available in sorted(depth.buy_orders.items(), reverse=True):
                if bid_volume_available <= 0 or sell_overlay_capacity <= 0:
                    break

                edge = bid_price - reservation_price
                rich_bid = bid_price >= rich_bid_threshold
                signal_flip = signal_strength <= -0.8 and bid_price >= math.ceil(trend_fair) + 1

                should_take = rich_bid or edge >= 1.75 or signal_flip
                if not should_take:
                    break

                clip_cap = 4 if not rich_bid else 6
                clip = min(bid_volume_available, sell_overlay_capacity, clip_cap)
                if clip <= 0:
                    continue

                orders.append(Order(PEPPER, bid_price, -clip))
                position -= clip
                buy_capacity = max(0, POSITION_LIMIT - position)
                overlay_inventory = max(0, position - CORE_POSITION)
                sell_overlay_capacity = overlay_inventory

            # Final passive overlay logic
            overlay_inventory = max(0, position - CORE_POSITION)
            buy_capacity = max(0, POSITION_LIMIT - position)
            sell_overlay_capacity = overlay_inventory

            inventory_ratio = (position - buy_target_limit) / POSITION_LIMIT
            reservation_price = fair_value - 1.35 * inventory_ratio

            bid_edge = 0.95
            ask_edge = 1.30
            if spread <= 8:
                bid_edge -= 0.20
                ask_edge -= 0.10
            elif spread >= 16:
                bid_edge += 0.20
                ask_edge += 0.30

            if signal_strength >= 1.3:
                bid_edge -= 0.20
                ask_edge += 0.15
            elif signal_strength <= -1.0:
                bid_edge += 0.20
                ask_edge -= 0.15

            passive_bid = min(best_bid + 1, int(math.floor(reservation_price - bid_edge)))
            passive_ask = max(best_ask - 1, int(math.ceil(reservation_price + ask_edge)))
            passive_bid, passive_ask = self._sanitize_quotes(passive_bid, passive_ask, best_bid, best_ask)

            headroom_to_target = max(0, buy_target_limit - position)
            if headroom_to_target > 0 and passive_bid > 0:
                front_buy = min(headroom_to_target, max(2, min(5, 2 + headroom_to_target // 3)))
                orders.append(Order(PEPPER, passive_bid, front_buy))

                residual_buy = max(0, headroom_to_target - front_buy)
                if residual_buy > 0 and passive_bid - 1 > 0 and signal_strength >= 0.9:
                    second_buy = min(residual_buy, 2)
                    if second_buy > 0:
                        orders.append(Order(PEPPER, passive_bid - 1, second_buy))

            if sell_overlay_capacity > 0:
                want_passive_sell = best_bid >= rich_bid_threshold - 1 or signal_strength <= -0.4
                if want_passive_sell:
                    front_sell = min(sell_overlay_capacity, 3 if best_bid >= rich_bid_threshold else 2)
                    if front_sell > 0:
                        orders.append(Order(PEPPER, passive_ask, -front_sell))

            new_state = {
                "fair_value": fair_value,
                "last_mid": mid,
                "anchor": anchor,
                "step_index": step_index,
                "last_timestamp": timestamp,
                "pressure": pressure,
                "signal_strength": signal_strength,
                "last_regime": regime,
                "last_event_step": last_event_step,
                "last_full_bid": best_bid,
                "last_full_ask": best_ask,
                "last_full_mid": mid,
                "last_full_spread": spread,
            }
            return orders, new_state

        # -------------------------
        # One-sided event-driven micro experiment
        # Only 1-2 lots, only first snapshot of a burst, and never by selling core.
        # -------------------------
        fair_value = prev_fair if prev_fair > 0 else trend_fair
        first_in_burst = regime != prev_regime
        cooled_down = (step_index - last_event_step) >= EVENT_COOLDOWN_STEPS

        if first_in_burst and cooled_down:
            if has_asks and not has_bids:
                event_buy_signal = (
                    buy_capacity > 0
                    and prev_full_spread > 0
                    and prev_full_spread <= 14
                    and prev_signal >= 0.55
                    and prev_pressure >= -0.05
                )
                if event_buy_signal:
                    best_ask = min(depth.sell_orders)
                    event_bid = min(
                        best_ask - 1,
                        int(math.floor(fair_value - 0.8)),
                    )
                    if prev_full_bid > 0:
                        event_bid = min(best_ask - 1, max(event_bid, int(prev_full_bid)))
                    if event_bid > 0 and event_bid < best_ask:
                        size = min(EVENT_EXPERIMENT_MAX_SIZE, buy_capacity)
                        if size > 0:
                            orders.append(Order(PEPPER, event_bid, size))
                            last_event_step = step_index

            elif has_bids and not has_asks:
                event_sell_signal = (
                    sell_overlay_capacity > 0
                    and prev_full_spread > 0
                    and prev_full_spread <= 14
                    and prev_pressure >= 0.0
                )
                if event_sell_signal:
                    best_bid = max(depth.buy_orders)
                    event_ask = max(
                        best_bid + 1,
                        int(math.ceil(fair_value + 0.8)),
                    )
                    if prev_full_ask > 0:
                        event_ask = max(best_bid + 1, min(event_ask, int(prev_full_ask)))
                    size = min(EVENT_EXPERIMENT_MAX_SIZE, sell_overlay_capacity)
                    if size > 0:
                        orders.append(Order(PEPPER, event_ask, -size))
                        last_event_step = step_index

        new_state = {
            "fair_value": fair_value,
            "last_mid": reference_mid,
            "anchor": anchor,
            "step_index": step_index,
            "last_timestamp": timestamp,
            "pressure": prev_pressure,
            "signal_strength": prev_signal,
            "last_regime": regime,
            "last_event_step": last_event_step,
            "last_full_bid": prev_full_bid,
            "last_full_ask": prev_full_ask,
            "last_full_mid": prev_full_mid,
            "last_full_spread": prev_full_spread,
        }
        return orders, new_state

    def _overlay_target(
        self,
        signal_strength: float,
        mid: float,
        trend_fair: float,
        residual_move: float,
    ) -> int:
        if signal_strength >= 2.20 and mid <= trend_fair - 1.0 and residual_move <= -0.6:
            return 10
        if signal_strength >= 1.50 and mid <= trend_fair - 0.7:
            return 7
        if signal_strength >= 0.85 and mid <= trend_fair - 0.3:
            return 4
        return 0

    def _book_regime(self, depth: OrderDepth) -> str:
        has_bids = bool(depth.buy_orders)
        has_asks = bool(depth.sell_orders)
        if has_bids and has_asks:
            return "two_sided"
        if has_bids:
            return "no_ask"
        if has_asks:
            return "no_bid"
        return "empty"

    def _infer_reference_mid(self, depth: OrderDepth, prev_state: Dict[str, float]) -> float:
        if depth.buy_orders and depth.sell_orders:
            return (max(depth.buy_orders) + min(depth.sell_orders)) / 2.0
        prev_mid = self._as_float(prev_state.get("last_full_mid"), 0.0)
        if prev_mid > 0:
            return prev_mid
        prev_bid = self._as_float(prev_state.get("last_full_bid"), 0.0)
        prev_ask = self._as_float(prev_state.get("last_full_ask"), 0.0)
        if prev_bid > 0 and prev_ask > 0:
            return (prev_bid + prev_ask) / 2.0
        if depth.buy_orders:
            return float(max(depth.buy_orders))
        if depth.sell_orders:
            return float(min(depth.sell_orders))
        return 0.0

    def _book_snapshot(
        self, depth: OrderDepth
    ) -> Optional[Tuple[int, int, int, int, float, int, float, float]]:
        if not depth.buy_orders or not depth.sell_orders:
            return None

        best_bid = max(depth.buy_orders)
        best_ask = min(depth.sell_orders)
        bid_volume = depth.buy_orders[best_bid]
        ask_volume = abs(depth.sell_orders[best_ask])
        spread = best_ask - best_bid
        mid = (best_bid + best_ask) / 2.0

        total_volume = bid_volume + ask_volume
        imbalance = 0.0
        microprice = mid
        if total_volume > 0:
            imbalance = (bid_volume - ask_volume) / total_volume
            microprice = (best_ask * bid_volume + best_bid * ask_volume) / total_volume

        return best_bid, bid_volume, best_ask, ask_volume, mid, spread, imbalance, microprice

    def _sanitize_quotes(
        self,
        bid_price: int,
        ask_price: int,
        best_bid: int,
        best_ask: int,
    ) -> Tuple[int, int]:
        if bid_price >= best_ask:
            bid_price = best_ask - 1
        if ask_price <= best_bid:
            ask_price = best_bid + 1
        if bid_price >= ask_price:
            bid_price = ask_price - 1
        return bid_price, ask_price

    def _load_state(self, trader_data: str) -> Dict[str, Dict[str, float]]:
        try:
            parsed = json.loads(trader_data) if trader_data else {}
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def _as_float(self, value: Optional[float], fallback: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return fallback


# -------------------------
# Local overlay export helpers
# -------------------------
def _load_price_snapshots(prices_path: str) -> Tuple[Dict[int, Dict[str, dict]], Dict[int, int]]:
    snapshots: Dict[int, Dict[str, dict]] = {}
    day_by_timestamp: Dict[int, int] = {}

    with open(prices_path, newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        for row in reader:
            timestamp = int(row["timestamp"])
            day_value = row.get("day", "")
            if day_value != "":
                day_by_timestamp[timestamp] = int(float(day_value))

            product = row["product"]
            depth = OrderDepth()
            for level in (1, 2, 3):
                bid_price = row.get(f"bid_price_{level}", "")
                bid_volume = row.get(f"bid_volume_{level}", "")
                ask_price = row.get(f"ask_price_{level}", "")
                ask_volume = row.get(f"ask_volume_{level}", "")

                if bid_price and bid_volume:
                    depth.buy_orders[int(float(bid_price))] = int(float(bid_volume))
                if ask_price and ask_volume:
                    depth.sell_orders[int(float(ask_price))] = -int(float(ask_volume))

            mid_price = float(row["mid_price"]) if row["mid_price"] else _compute_mid(depth)
            snapshots.setdefault(timestamp, {})[product] = {
                "depth": depth,
                "mid_price": mid_price,
            }

    return dict(sorted(snapshots.items())), day_by_timestamp


def _load_public_trades(trades_path: str) -> Dict[Tuple[int, str], List[dict]]:
    trades: Dict[Tuple[int, str], List[dict]] = {}

    with open(trades_path, newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        for row in reader:
            key = (int(row["timestamp"]), row["symbol"])
            trades.setdefault(key, []).append(
                {
                    "price": int(float(row["price"])),
                    "quantity": int(float(row["quantity"])),
                }
            )

    return trades


def _compute_mid(depth: OrderDepth) -> float:
    if not depth.buy_orders or not depth.sell_orders:
        return 0.0
    return (max(depth.buy_orders) + min(depth.sell_orders)) / 2.0


def _simulate_fills_for_order(order: Order, depth: OrderDepth, public_trades: List[dict]):
    fills = []
    remaining = abs(order.quantity)

    if order.quantity > 0:
        for ask_price in sorted(depth.sell_orders):
            if ask_price > order.price or remaining <= 0:
                break
            available = -depth.sell_orders[ask_price]
            fill_qty = min(remaining, available)
            if fill_qty > 0:
                fills.append((ask_price, fill_qty, "buy"))
                remaining -= fill_qty

        best_ask = min(depth.sell_orders) if depth.sell_orders else None
        if remaining > 0 and best_ask is not None and order.price < best_ask:
            candidate_qty = sum(trade["quantity"] for trade in public_trades if trade["price"] <= order.price)
            passive_fill = min(remaining, max(1, candidate_qty // 2)) if candidate_qty > 0 else 0
            if passive_fill > 0:
                fills.append((order.price, passive_fill, "buy"))

    elif order.quantity < 0:
        for bid_price in sorted(depth.buy_orders, reverse=True):
            if bid_price < order.price or remaining <= 0:
                break
            available = depth.buy_orders[bid_price]
            fill_qty = min(remaining, available)
            if fill_qty > 0:
                fills.append((bid_price, fill_qty, "sell"))
                remaining -= fill_qty

        best_bid = max(depth.buy_orders) if depth.buy_orders else None
        if remaining > 0 and best_bid is not None and order.price > best_bid:
            candidate_qty = sum(trade["quantity"] for trade in public_trades if trade["price"] >= order.price)
            passive_fill = min(remaining, max(1, candidate_qty // 2)) if candidate_qty > 0 else 0
            if passive_fill > 0:
                fills.append((order.price, passive_fill, "sell"))

    return fills


def export_dashboard_overlay(
    prices_path: str,
    trades_path: str,
    output_path: str,
    day: Optional[int] = None,
):
    snapshots, day_by_timestamp = _load_price_snapshots(prices_path)
    public_trades = _load_public_trades(trades_path)

    trader = Trader()
    trader_data = ""
    positions = {PEPPER: 0}
    cash = {PEPPER: 0.0}
    overlay_rows = []

    for timestamp, products in snapshots.items():
        if day is not None:
            snapshot_day = day_by_timestamp.get(timestamp)
            if snapshot_day is None or snapshot_day != day:
                continue

        order_depths = {
            product: product_data["depth"]
            for product, product_data in products.items()
        }
        state = TradingState(
            trader_data,
            timestamp,
            {},
            order_depths,
            {},
            {},
            positions.copy(),
            Observation({}, {}),
        )

        result, _, trader_data = trader.run(state)
        for product, orders in result.items():
            if product != PEPPER or product not in products:
                continue

            depth = products[product]["depth"]
            mid_price = products[product]["mid_price"]
            market_trades_for_tick = public_trades.get((timestamp, product), [])

            for order in orders:
                fills = _simulate_fills_for_order(order, depth, market_trades_for_tick)
                for fill_price, fill_qty, side in fills:
                    if side == "buy":
                        positions[product] += fill_qty
                        cash[product] -= fill_price * fill_qty
                    else:
                        positions[product] -= fill_qty
                        cash[product] += fill_price * fill_qty

                    pnl = cash[product] + positions[product] * mid_price
                    overlay_rows.append(
                        {
                            "timestamp": timestamp,
                            "product": product,
                            "price": fill_price,
                            "quantity": fill_qty,
                            "side": side,
                            "pnl": round(pnl, 2),
                            "position": positions[product],
                        }
                    )

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["timestamp", "product", "price", "quantity", "side", "pnl", "position"],
        )
        writer.writeheader()
        writer.writerows(overlay_rows)

    return {
        "output_file": str(output_file),
        "rows": len(overlay_rows),
        "first_timestamp": overlay_rows[0]["timestamp"] if overlay_rows else None,
        "last_timestamp": overlay_rows[-1]["timestamp"] if overlay_rows else None,
        "day": day,
    }


def export_all_days(prices_path: str, trades_path: str, output_template: str):
    snapshots, day_by_timestamp = _load_price_snapshots(prices_path)
    days = sorted({day for day in day_by_timestamp.values() if day is not None})
    if not days:
        raise ValueError("No day values were found in the prices CSV.")

    summaries = []
    for day in days:
        output_path = output_template.format(day=day)
        summary = export_dashboard_overlay(
            prices_path=prices_path,
            trades_path=trades_path,
            output_path=output_path,
            day=day,
        )
        summaries.append(summary)

    return summaries


def _extract_day_from_name(path: str) -> Optional[int]:
    match = re.search(r"day_(-?\d+)", Path(path).name)
    if not match:
        return None
    return int(match.group(1))


def export_all_round_files(prices_path: str, trades_path: str, output_template: str):
    prices_dir = Path(prices_path).parent
    trades_dir = Path(trades_path).parent

    trade_files = {
        _extract_day_from_name(path.name): path
        for path in trades_dir.glob("trades_round_1_day_*.csv")
    }

    summaries = []
    for price_path in sorted(
        prices_dir.glob("prices_round_1_day_*.csv"),
        key=lambda path: _extract_day_from_name(path.name) if _extract_day_from_name(path.name) is not None else 999,
    ):
        day = _extract_day_from_name(price_path.name)
        if day is None:
            continue
        trade_path = trade_files.get(day)
        if trade_path is None:
            continue

        output_path = output_template.format(day=day)
        summary = export_dashboard_overlay(
            prices_path=str(price_path),
            trades_path=str(trade_path),
            output_path=output_path,
            day=None,
        )
        summary["day"] = day
        summary["prices_file"] = str(price_path)
        summary["trades_file"] = str(trade_path)
        summaries.append(summary)

    if not summaries:
        raise ValueError("No matching round 1 price/trade file pairs were found.")

    return summaries


def _parse_args():
    parser = argparse.ArgumentParser(description="Export PEPPER core70 overlay + one-sided event experiment overlays")
    parser.add_argument("--prices", default=DEFAULT_PRICES_PATH, help="Path to a Prosperity prices CSV")
    parser.add_argument("--trades", default=DEFAULT_TRADES_PATH, help="Path to a Prosperity trades CSV")
    parser.add_argument("--out", default=DEFAULT_OUTPUT_PATH, help="Output CSV for the dashboard overlay")
    parser.add_argument(
        "--all-days",
        action="store_true",
        help="Export one backtest CSV per day found in the prices file",
    )
    parser.add_argument(
        "--out-template",
        default=DEFAULT_OUTPUT_TEMPLATE,
        help="Output template for --all-days (use {day} in the filename)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.all_days:
        summaries = export_all_days(args.prices, args.trades, args.out_template)
        if len(summaries) <= 1:
            try:
                summaries = export_all_round_files(args.prices, args.trades, args.out_template)
            except Exception:
                pass
        print(json.dumps({"outputs": summaries}, indent=2))
    else:
        summary = export_dashboard_overlay(args.prices, args.trades, args.out)
        print(json.dumps(summary, indent=2))
