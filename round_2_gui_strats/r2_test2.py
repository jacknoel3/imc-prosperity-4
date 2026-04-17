import argparse
import csv
import json
import math
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from datamodel import Observation, Order, OrderDepth, TradingState


PEPPER = "INTARIAN_PEPPER_ROOT"
POSITION_LIMIT = 80
CORE_POSITION = 70
MM_INVENTORY_BAND = 6

DEFAULT_PRICES_PATH = str(REPO_ROOT / "data/round2/prices_round_2_day_0.csv")
DEFAULT_TRADES_PATH = str(REPO_ROOT / "data/round2/trades_round_2_day_0.csv")
DEFAULT_OUTPUT_PATH = str(REPO_ROOT / "dashboard_round2/examples/backtest_trades_r2_test2_day_0_generated.csv")
DEFAULT_OUTPUT_TEMPLATE = str(
    REPO_ROOT / "dashboard_round2/examples/backtest_trades_r2_test2_day_{day}_generated.csv"
)


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
        prev_pressure = self._as_float(prev_state.get("pressure"), 0.0)

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

        # New: sharp downside shock relative to normal drift
        shock_buy_active = residual_move <= -4 and mid <= trend_fair - 1

        # Persistent pressure from repeated book leaning
        instant_pressure = 0.6 * imbalance + 0.4 * (microprice - mid)
        pressure = 0.85 * prev_pressure + 0.15 * instant_pressure

        signal_strength = (
            0.85 * (microprice - mid)
            + 2.7 * imbalance
            - 0.45 * residual_move
            + 0.18
            + 1.2 * pressure
        )

        fair_measurement = trend_fair + signal_strength
        fair_value = 0.66 * prev_fair + 0.34 * fair_measurement

        overlay_target = 0
        if signal_strength > 1.9:
            overlay_target = 14
        elif signal_strength > 1.0:
            overlay_target = 8
        elif signal_strength > 0.35:
            overlay_target = 4
        target_position = CORE_POSITION + overlay_target

        neutral_mm_active = spread >= 10 and abs(signal_strength) <= 1.1 and pressure > -0.25
        mm_band = MM_INVENTORY_BAND if neutral_mm_active else 0
        sell_floor = max(0, CORE_POSITION - mm_band)
        rebuild_floor = CORE_POSITION - max(2, mm_band // 2)
        buy_capacity = max(0, POSITION_LIMIT - position)
        sell_capacity = max(0, position - sell_floor)

        target_gap = position - target_position
        inventory_ratio = target_gap / POSITION_LIMIT
        reservation_price = fair_value - 1.75 * inventory_ratio

        build_shortfall = max(0, rebuild_floor - position)
        build_mode = build_shortfall > 0
        overlay_inventory = max(0, position - CORE_POSITION)

        buy_take_threshold = 1.7
        sell_take_threshold = 2.8
        if spread <= 8:
            buy_take_threshold -= 0.25
            sell_take_threshold -= 0.20
        elif spread >= 16:
            buy_take_threshold += 0.25
            sell_take_threshold += 0.35

        if imbalance > 0.35:
            buy_take_threshold -= 0.35
        if imbalance < -0.35:
            sell_take_threshold -= 0.25

        if build_mode and pressure > 0.08:
            buy_take_threshold -= 0.9

        buy_taken = 0
        for ask_price, ask_volume in sorted(depth.sell_orders.items()):
            available = abs(ask_volume)
            if available <= 0 or buy_capacity <= 0:
                continue

            edge = reservation_price - ask_price

            shock_buy = (
                shock_buy_active
                and ask_price <= math.floor(trend_fair) - 1
                and position < min(POSITION_LIMIT, target_position + 4)
            )

            should_take = build_mode and pressure > 0.08 and ask_price <= best_ask + 2
            should_take = should_take or edge >= buy_take_threshold
            should_take = should_take or (
                ask_price <= math.floor(trend_fair) and signal_strength > 0.5
            )
            should_take = should_take or (
                position < CORE_POSITION and ask_price <= math.ceil(fair_value) + 1 and pressure > -0.02
            )
            should_take = should_take or shock_buy

            if not should_take:
                break

            clip_cap = 4 if shock_buy else self._take_clip(position, True)
            clip = min(available, buy_capacity, clip_cap)
            if clip <= 0:
                continue

            orders.append(Order(PEPPER, ask_price, clip))
            position += clip
            buy_capacity = max(0, POSITION_LIMIT - position)
            sell_capacity = max(0, position - sell_floor)
            buy_taken += clip
            build_shortfall = max(0, rebuild_floor - position)
            build_mode = build_shortfall > 0

        sell_taken = 0
        for bid_price, bid_volume in sorted(depth.buy_orders.items(), reverse=True):
            available = bid_volume
            if available <= 0 or sell_capacity <= 0:
                continue

            edge = bid_price - reservation_price
            rich_vs_trend = bid_price >= math.ceil(trend_fair) + 2
            should_take = edge >= sell_take_threshold and position > target_position
            should_take = should_take or (
                signal_strength < -1.3 and rich_vs_trend and position > CORE_POSITION + 6
            )
            should_take = should_take or (
                position > target_position + 10 and bid_price >= math.ceil(fair_value)
            )

            if not should_take:
                break

            clip = min(available, sell_capacity, self._take_clip(position, False))
            if clip <= 0:
                continue
            orders.append(Order(PEPPER, bid_price, -clip))
            position -= clip
            buy_capacity = max(0, POSITION_LIMIT - position)
            sell_capacity = max(0, position - sell_floor)
            sell_taken += clip

        target_gap = position - target_position
        inventory_ratio = target_gap / POSITION_LIMIT
        overlay_inventory = max(0, position - CORE_POSITION)
        reservation_price = fair_value - 1.75 * inventory_ratio

        bid_edge = 0.95
        ask_edge = 1.55
        if spread <= 8:
            bid_edge = 0.70
            ask_edge = 1.25
        elif spread >= 16:
            bid_edge = 1.15
            ask_edge = 1.85

        if signal_strength > 0.9:
            bid_edge -= 0.20
            ask_edge += 0.20
        elif signal_strength < -0.9:
            bid_edge += 0.20
            ask_edge -= 0.15

        if neutral_mm_active and sell_capacity > 0:
            bid_edge += 0.10
            ask_edge -= 0.25

        if position < rebuild_floor:
            bid_edge -= 0.35
            ask_edge += 0.50
        elif overlay_inventory <= 6 and not neutral_mm_active:
            ask_edge += 0.35
        elif overlay_inventory >= 18:
            ask_edge -= 0.20

        target_bid = int(math.floor(reservation_price - bid_edge))
        target_ask = int(math.ceil(reservation_price + ask_edge))

        passive_bid = min(best_bid + 1, target_bid)
        passive_ask = max(best_ask - 1, target_ask)
        passive_bid, passive_ask = self._sanitize_quotes(passive_bid, passive_ask, best_bid, best_ask)

        front_buy = self._quote_size(
            capacity=buy_capacity,
            position=position,
            target_position=target_position,
            side="buy",
            took_liquidity=buy_taken > 0,
        )
        front_sell = self._quote_size(
            capacity=sell_capacity,
            position=position,
            target_position=target_position,
            side="sell",
            took_liquidity=sell_taken > 0,
        )

        if position < rebuild_floor:
            front_buy = min(buy_capacity, front_buy + 4)
            front_sell = 0
        elif neutral_mm_active and sell_capacity > 0:
            front_buy = min(buy_capacity, front_buy + (1 if position < CORE_POSITION else 0))
            front_sell = max(2, front_sell)
        elif position <= CORE_POSITION + 4:
            front_sell = 0
        elif position > target_position + 8:
            front_sell = min(sell_capacity, front_sell + 2)

        if front_buy > 0 and passive_bid > 0:
            orders.append(Order(PEPPER, passive_bid, front_buy))
        if front_sell > 0:
            orders.append(Order(PEPPER, passive_ask, -front_sell))

        residual_buy = max(0, buy_capacity - front_buy)
        residual_sell = max(0, sell_capacity - front_sell)

        if residual_buy > 0 and passive_bid - 1 > 0 and position < target_position + 14:
            second_buy = min(residual_buy, max(2, (front_buy + 2) // 2))
            if position < rebuild_floor:
                second_buy = min(residual_buy, second_buy + 2)
            if second_buy > 0:
                orders.append(Order(PEPPER, passive_bid - 1, second_buy))

        if residual_sell > 0 and position > (CORE_POSITION + (6 if neutral_mm_active else 10)):
            second_sell = min(residual_sell, max(2, front_sell // 2 if front_sell > 0 else 2))
            if second_sell > 0:
                orders.append(Order(PEPPER, passive_ask + 1, -second_sell))

        new_state = {
            "fair_value": fair_value,
            "last_mid": mid,
            "anchor": day_anchor,
            "step_index": step_index,
            "last_timestamp": timestamp,
            "pressure": pressure,
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
        for path in trades_dir.glob("trades_round_2_day_*.csv")
    }

    summaries = []
    for price_path in sorted(
        prices_dir.glob("prices_round_2_day_*.csv"),
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
        raise ValueError("No matching round 2 price/trade file pairs were found.")

    return summaries


def _parse_args():
    parser = argparse.ArgumentParser(description="Export round 2 PEPPER core70 shock-buy variant overlays")
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
