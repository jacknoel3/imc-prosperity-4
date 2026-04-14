import argparse
import csv
import json
import math
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from datamodel import Observation, Order, OrderDepth, TradingState


ASH = "ASH_COATED_OSMIUM"
PEPPER = "INTARIAN_PEPPER_ROOT"

LIMITS = {
    ASH: 80,
    PEPPER: 80,
}

DEFAULT_PRICES_PATH = "data/round1/prices_round_1_day_0.csv"
DEFAULT_TRADES_PATH = "data/round1/trades_round_1_day_0.csv"
DEFAULT_OUTPUT_PATH = "dashboard_round1/examples/backtest_trades_gui_round1_v1_day_0_generated.csv"
DEFAULT_OUTPUT_TEMPLATE = "dashboard_round1/examples/backtest_trades_gui_round1_v1_day_{day}_generated.csv"


class Trader:
    def run(self, state: TradingState):
        trader_state = self._load_state(state.traderData)
        result: Dict[str, List[Order]] = {}

        ash_state = trader_state.get(ASH, {})
        pepper_state = trader_state.get(PEPPER, {})

        for product, depth in state.order_depths.items():
            if product == ASH:
                orders, ash_state = self._trade_ash(
                    depth=depth,
                    position=state.position.get(ASH, 0),
                    prev_state=ash_state,
                    timestamp=int(state.timestamp),
                )
                result[product] = orders
            elif product == PEPPER:
                orders, pepper_state = self._trade_pepper(
                    depth=depth,
                    position=state.position.get(PEPPER, 0),
                    prev_state=pepper_state,
                    timestamp=int(state.timestamp),
                )
                result[product] = orders
            else:
                result[product] = []

        new_state = json.dumps(
            {
                ASH: ash_state,
                PEPPER: pepper_state,
            },
            separators=(",", ":"),
        )
        return result, 0, new_state

    def _trade_ash(
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

        best_bid, best_bid_volume, best_ask, best_ask_volume, mid, spread, imbalance, microprice = book

        fixed_fair = 10000.0
        limit = LIMITS[ASH]
        edge = 2
        take_clip = 18

        prev_mid = self._as_float(prev_state.get("last_mid"), mid)
        last_move = mid - prev_mid
        fair_value = fixed_fair

        buy_capacity = max(0, limit - position)
        sell_capacity = max(0, limit + position)

        # Emerald-like taker logic: cross only when the book is clearly through fixed fair value.
        for ask_price, ask_volume in sorted(depth.sell_orders.items()):
            available = abs(ask_volume)
            if available <= 0 or buy_capacity <= 0 or ask_price >= fixed_fair:
                break
            clip = min(available, buy_capacity, take_clip)
            if clip <= 0:
                continue
            orders.append(Order(ASH, ask_price, clip))
            position += clip
            buy_capacity = max(0, limit - position)
            sell_capacity = max(0, limit + position)

        for bid_price, bid_volume in sorted(depth.buy_orders.items(), reverse=True):
            available = bid_volume
            if available <= 0 or sell_capacity <= 0 or bid_price <= fixed_fair:
                break
            clip = min(available, sell_capacity, take_clip)
            if clip <= 0:
                continue
            orders.append(Order(ASH, bid_price, -clip))
            position -= clip
            buy_capacity = max(0, limit - position)
            sell_capacity = max(0, limit + position)

        # When inventory is stretched, allow cheap flattening at fair value itself.
        if best_ask == fixed_fair and position < -18 and buy_capacity > 0:
            clip = min(abs(depth.sell_orders[best_ask]), buy_capacity, min(12, -position))
            if clip > 0:
                orders.append(Order(ASH, best_ask, clip))
                position += clip
                buy_capacity = max(0, limit - position)
                sell_capacity = max(0, limit + position)

        if best_bid == fixed_fair and position > 18 and sell_capacity > 0:
            clip = min(depth.buy_orders[best_bid], sell_capacity, min(12, position))
            if clip > 0:
                orders.append(Order(ASH, best_bid, -clip))
                position -= clip
                buy_capacity = max(0, limit - position)
                sell_capacity = max(0, limit + position)

        inventory_ratio = position / limit
        imbalance_tilt = 0
        if imbalance > 0.25:
            imbalance_tilt = 1
        elif imbalance < -0.25:
            imbalance_tilt = -1

        mean_revert_tilt = 0
        if last_move > 1.2:
            mean_revert_tilt = -1
        elif last_move < -1.2:
            mean_revert_tilt = 1

        inventory_skew = int(round(inventory_ratio * 3.0))
        quote_shift = imbalance_tilt + mean_revert_tilt - inventory_skew

        target_bid = int(fixed_fair - edge + quote_shift)
        target_ask = int(fixed_fair + edge + quote_shift)

        passive_bid = min(best_bid + 1, target_bid)
        passive_ask = max(best_ask - 1, target_ask)

        if spread <= 2:
            passive_bid = min(best_bid, target_bid)
            passive_ask = max(best_ask, target_ask)

        passive_bid, passive_ask = self._sanitize_quotes(passive_bid, passive_ask, best_bid, best_ask)

        buy_scale = max(0.3, 0.95 - max(0.0, inventory_ratio) * 0.75)
        sell_scale = max(0.3, 0.95 - max(0.0, -inventory_ratio) * 0.75)
        front_buy = min(buy_capacity, max(1, int(min(buy_capacity, 16) * buy_scale))) if buy_capacity > 0 else 0
        front_sell = min(sell_capacity, max(1, int(min(sell_capacity, 16) * sell_scale))) if sell_capacity > 0 else 0

        if front_buy > 0:
            orders.append(Order(ASH, passive_bid, front_buy))
        if front_sell > 0:
            orders.append(Order(ASH, passive_ask, -front_sell))

        residual_buy = max(0, buy_capacity - front_buy)
        residual_sell = max(0, sell_capacity - front_sell)

        if residual_buy > 0 and position < 20 and passive_bid - 1 > 0:
            second_buy = min(residual_buy, max(2, max(1, front_buy // 2)))
            if second_buy > 0:
                orders.append(Order(ASH, passive_bid - 1, second_buy))

        if residual_sell > 0 and position > -20:
            second_sell = min(residual_sell, max(2, max(1, front_sell // 2)))
            if second_sell > 0:
                orders.append(Order(ASH, passive_ask + 1, -second_sell))

        new_state = {
            "fair_value": fair_value,
            "last_mid": mid,
            "last_timestamp": timestamp,
        }
        return orders, new_state

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

        best_bid, best_bid_volume, best_ask, best_ask_volume, mid, spread, imbalance, microprice = book

        limit = LIMITS[PEPPER]
        working_limit = 64
        unwind_start = 46
        danger_start = 68
        trend_per_step = 0.1002
        base_inventory_target = 12

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
        day_anchor = 0.84 * day_anchor + 0.16 * anchor_measurement
        trend_fair = day_anchor + trend_per_step * step_index

        expected_step_move = (
            trend_per_step * max(1, step_index - prev_step)
            if step_index >= prev_step
            else trend_per_step
        )
        residual_move = (mid - prev_mid) - expected_step_move
        signal_strength = (
            0.82 * (microprice - mid)
            + 2.65 * imbalance
            - 0.50 * residual_move
            + 0.22
        )

        measurement = trend_fair + signal_strength
        fair_value = 0.62 * prev_fair + 0.38 * measurement

        inventory_target = base_inventory_target
        if signal_strength > 1.5:
            inventory_target += 8
        elif signal_strength > 0.8:
            inventory_target += 4
        elif signal_strength < -1.4:
            inventory_target -= 8
        elif signal_strength < -0.8:
            inventory_target -= 4
        inventory_target = max(2, min(24, inventory_target))

        target_gap = position - inventory_target
        inventory_ratio = target_gap / limit
        inventory_pressure = target_gap / working_limit
        reservation_price = fair_value - 2.15 * inventory_ratio

        buy_capacity = max(0, min(limit - position, working_limit - target_gap))
        sell_capacity = max(0, min(limit + position, working_limit + target_gap))

        buy_take_threshold = 2.4 + max(0.0, inventory_pressure) * 1.8
        sell_take_threshold = 4.3 + max(0.0, -inventory_pressure) * 2.0

        if imbalance > 0.40:
            buy_take_threshold -= 0.7
        if imbalance < -0.40:
            sell_take_threshold -= 0.5

        if position <= -unwind_start:
            buy_take_threshold -= 1.2
        if position >= unwind_start:
            sell_take_threshold -= 1.4

        buy_taken = 0
        for ask_price, ask_volume in sorted(depth.sell_orders.items()):
            available = abs(ask_volume)
            if available <= 0 or buy_capacity <= 0:
                continue

            edge = reservation_price - ask_price
            strong_up_signal = imbalance > 0.72 and signal_strength > 1.5
            should_take = edge >= buy_take_threshold
            should_take = should_take or (
                ask_price <= math.floor(trend_fair) and signal_strength > 0.5
            )
            should_take = should_take or (strong_up_signal and ask_price <= math.floor(fair_value))
            should_take = should_take or (position <= -danger_start and ask_price <= best_ask + 1)

            if not should_take:
                break

            clip = min(available, buy_capacity, self._take_clip(position, True, limit))
            if clip <= 0:
                continue
            orders.append(Order(PEPPER, ask_price, clip))
            position += clip
            buy_capacity -= clip
            sell_capacity = max(0, min(limit + position, working_limit + inventory_target - position))
            buy_taken += clip

        sell_taken = 0
        for bid_price, bid_volume in sorted(depth.buy_orders.items(), reverse=True):
            available = bid_volume
            if available <= 0 or sell_capacity <= 0:
                continue

            edge = bid_price - reservation_price
            strong_down_signal = imbalance < -0.80 and signal_strength < -2.2
            should_take = edge >= sell_take_threshold
            should_take = should_take or (strong_down_signal and bid_price >= math.ceil(fair_value) + 1)
            should_take = should_take or (
                position >= danger_start and bid_price >= best_bid - 1
            )
            should_take = should_take or (
                position > inventory_target + 18 and bid_price >= math.ceil(trend_fair)
            )

            if not should_take:
                break

            clip = min(available, sell_capacity, self._take_clip(position, False, limit))
            if clip <= 0:
                continue
            orders.append(Order(PEPPER, bid_price, -clip))
            position -= clip
            sell_capacity -= clip
            buy_capacity = max(0, min(limit - position, working_limit - (position - inventory_target)))
            sell_taken += clip

        target_gap = position - inventory_target
        inventory_ratio = target_gap / limit
        reservation_price = fair_value - 2.15 * inventory_ratio

        bid_edge = 1.25
        ask_edge = 2.45
        if spread <= 8:
            bid_edge = 0.9
            ask_edge = 1.7
        elif spread >= 16:
            bid_edge = 1.55
            ask_edge = 2.8

        if signal_strength > 0.9:
            bid_edge -= 0.35
            ask_edge += 0.25
        elif signal_strength < -0.9:
            bid_edge += 0.35
            ask_edge -= 0.45

        if position < inventory_target:
            bid_edge -= 0.25
            ask_edge += 0.15
        elif position > inventory_target:
            bid_edge += 0.2
            ask_edge -= 0.3

        if position >= unwind_start:
            bid_edge += 0.55
            ask_edge -= 0.65
        elif position <= -unwind_start:
            bid_edge -= 0.55
            ask_edge += 0.55

        target_bid = int(math.floor(reservation_price - bid_edge))
        target_ask = int(math.ceil(reservation_price + ask_edge))

        passive_bid = min(best_bid + 1, target_bid)
        passive_ask = max(best_ask - 1, target_ask)
        passive_bid, passive_ask = self._sanitize_quotes(passive_bid, passive_ask, best_bid, best_ask)

        front_buy = self._quote_size(
            capacity=buy_capacity,
            position=position,
            limit=limit,
            side="buy",
            target_position=inventory_target,
            took_liquidity=buy_taken > 0,
        )
        front_sell = self._quote_size(
            capacity=sell_capacity,
            position=position,
            limit=limit,
            side="sell",
            target_position=inventory_target,
            took_liquidity=sell_taken > 0,
        )

        if position < inventory_target:
            front_buy = min(buy_capacity, front_buy + 2)
            front_sell = max(0, front_sell - 1)
        elif position > inventory_target + 8:
            front_buy = max(0, front_buy - 1)
            front_sell = min(sell_capacity, front_sell + 2)

        if front_buy > 0:
            orders.append(Order(PEPPER, passive_bid, front_buy))
        if front_sell > 0:
            orders.append(Order(PEPPER, passive_ask, -front_sell))

        residual_buy = max(0, buy_capacity - front_buy)
        residual_sell = max(0, sell_capacity - front_sell)

        if residual_buy > 0 and position < inventory_target + 22 and passive_bid - 1 > 0:
            second_buy = min(residual_buy, max(2, (front_buy + 1) // 2))
            if second_buy > 0:
                orders.append(Order(PEPPER, passive_bid - 1, second_buy))

        if residual_sell > 0 and position > inventory_target - 14:
            second_sell = min(residual_sell, max(2, front_sell // 2))
            if second_sell > 0:
                orders.append(Order(PEPPER, passive_ask + 1, -second_sell))

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
        limit: int,
        side: str,
        target_position: int,
        took_liquidity: bool,
    ) -> int:
        if capacity <= 0:
            return 0

        target_gap = position - target_position
        if side == "buy":
            pressure = max(0.0, target_gap / limit)
        else:
            pressure = max(0.0, -target_gap / limit)

        scale = 0.42 if not took_liquidity else 0.31
        scale -= 0.24 * pressure
        scale = max(0.12, min(0.55, scale))

        size = int(capacity * scale)
        size = max(2, min(14, size))
        return min(size, capacity)

    def _take_clip(self, position: int, buy_side: bool, limit: int) -> int:
        heavy = 0.60 * limit
        if buy_side and position > heavy:
            return 5
        if (not buy_side) and position < -heavy:
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
                    # Prosperity order books store ask volume as negative.
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
    positions = {product: 0 for product in LIMITS}
    cash = {product: 0.0 for product in LIMITS}
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
            if product not in products:
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
    prices_file = Path(prices_path)
    trades_file = Path(trades_path)
    prices_dir = prices_file.parent
    trades_dir = trades_file.parent

    price_files = sorted(prices_dir.glob("prices_round_1_day_*.csv"))
    trade_files = { _extract_day_from_name(path.name): path for path in trades_dir.glob("trades_round_1_day_*.csv") }

    summaries = []
    for price_path in price_files:
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
    parser = argparse.ArgumentParser(description="Export a dashboard overlay from jack_round1_v1.py")
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
