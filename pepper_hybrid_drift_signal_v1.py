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

DEFAULT_PRICES_PATH = "data/round1/prices_round_1_day_0.csv"
DEFAULT_TRADES_PATH = "data/round1/trades_round_1_day_0.csv"
DEFAULT_OUTPUT_PATH = "dashboard_round1/examples/backtest_trades_pepper_hybrid_drift_signal_v1_day_0_generated.csv"
DEFAULT_OUTPUT_TEMPLATE = "dashboard_round1/examples/backtest_trades_pepper_hybrid_drift_signal_v1_day_{day}_generated.csv"


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
        book = self._book_snapshot(depth)
        if book is None:
            return orders, prev_state

        best_bid, _, best_ask, _, mid, spread, imbalance, microprice = book

        trend_per_step = 0.1002
        prev_mid = self._as_float(prev_state.get("last_mid"), mid)
        prev_fair = self._as_float(prev_state.get("fair_value"), mid)
        prev_step = int(prev_state.get("step_index", 0))
        prev_anchor = self._as_float(prev_state.get("anchor"), mid)
        prev_timestamp = int(prev_state.get("last_timestamp", timestamp))

        if timestamp < prev_timestamp:
            step_index = 0
            day_anchor = mid
        else:
            inferred_step = max(0, int(round(timestamp / 100)))
            step_index = max(prev_step, inferred_step)
            day_anchor = prev_anchor

        anchor_measurement = mid - trend_per_step * step_index
        day_anchor = 0.87 * day_anchor + 0.13 * anchor_measurement
        trend_fair = day_anchor + trend_per_step * step_index

        expected_step_move = (
            trend_per_step * max(1, step_index - prev_step)
            if step_index >= prev_step
            else trend_per_step
        )
        residual_move = (mid - prev_mid) - expected_step_move
        signal_strength = (
            0.85 * (microprice - mid)
            + 2.7 * imbalance
            - 0.45 * residual_move
            + 0.18
        )

        fair_measurement = trend_fair + signal_strength
        fair_value = 0.66 * prev_fair + 0.34 * fair_measurement

        # Drift-first philosophy:
        # target 80 unless signal is strongly negative, then fall back toward 70.
        if signal_strength < -2.2:
            target_position = 70
        elif signal_strength < -1.2:
            target_position = 76
        else:
            target_position = 80

        # --- Aggressive build to target ---
        ceiling = int(mid) + 20
        buy_capacity = max(0, target_position - position)

        if buy_capacity > 0:
            for ask_price, ask_volume in sorted(depth.sell_orders.items()):
                if ask_price > ceiling:
                    break
                available = abs(ask_volume)
                if available <= 0 or buy_capacity <= 0:
                    continue
                clip = min(available, buy_capacity)
                if clip > 0:
                    orders.append(Order(PEPPER, ask_price, clip))
                    position += clip
                    buy_capacity -= clip

        # Passive fallback while still below target
        if position < target_position:
            passive_bid = best_bid + 2
            clip = min(20, target_position - position)
            if clip > 0 and passive_bid < best_ask:
                orders.append(Order(PEPPER, passive_bid, clip))

        # --- Very rare defensive sells only ---
        # Keep a hard floor at 70. No routine trimming above core.
        sell_floor = 70
        sell_capacity = max(0, position - sell_floor)

        if sell_capacity > 0 and signal_strength < -2.2:
            rich_vs_trend = best_bid >= math.ceil(trend_fair) + 2
            rich_vs_fair = best_bid >= math.ceil(fair_value) + 1

            if rich_vs_trend or rich_vs_fair:
                clip = min(12, sell_capacity)
                if clip > 0:
                    orders.append(Order(PEPPER, best_bid, -clip))
                    position -= clip

        new_state = {
            "fair_value": fair_value,
            "last_mid": mid,
            "anchor": day_anchor,
            "step_index": step_index,
            "last_timestamp": timestamp,
        }
        return orders, new_state


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

    def _quote_size(
        self,
        capacity: int,
        position: int,
        target_position: int,
        side: str,
        took_liquidity: bool,
    ) -> int:
        if capacity <= 0:
            return 0

        target_gap = position - target_position
        if side == "buy":
            pressure = max(0.0, target_gap / POSITION_LIMIT)
        else:
            pressure = max(0.0, -target_gap / POSITION_LIMIT)

        scale = 0.38 if not took_liquidity else 0.28
        scale -= 0.22 * pressure
        scale = max(0.10, min(0.52, scale))

        size = int(capacity * scale)
        size = max(2, min(12, size))
        return min(size, capacity)

    def _take_clip(self, position: int, buy_side: bool) -> int:
        if buy_side and position >= CORE_POSITION + 24:
            return 6
        if (not buy_side) and position <= CORE_POSITION + 8:
            return 5
        return 12

    def _load_state(self, trader_data: str) -> Dict[str, Dict[str, float]]:
        try:
            parsed = json.loads(trader_data) if trader_data else {}
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def _as_float(self, value: Optional[float], fallback: float) -> float:
        try:
            return float(value)
        except Exception:
            return fallback


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
    parser = argparse.ArgumentParser(description="Export PEPPER core=70 variant overlays")
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
