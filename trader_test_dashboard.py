"""
Prosperity 4 - Round 0 dashboard test trader.

This file is designed to do two things:

1. Stay a valid Prosperity `Trader` module with `class Trader` and `run(...)`
2. When executed locally, export a dashboard-ready CSV overlay from round-0 data

Run locally:

    python3 trader_test_dashboard.py

Or with custom paths:

    python3 trader_test_dashboard.py --prices data/round0/prices_round_0_day_-2.csv \
        --trades data/round0/trades_round_0_day_-2.csv \
        --out dashboard/examples/backtest_trades_trader_test_dashboard_day_-2_generated.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Dict, List, Tuple

from datamodel import Observation, Order, OrderDepth, TradingState


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EMERALDS = "EMERALDS"
TOMATOES = "TOMATOES"

EMERALDS_FV = 10_000
EMERALDS_LIMIT = 20
TOMATOES_LIMIT = 35

DEFAULT_PRICES_PATH = "data/round0/prices_round_0_day_-1.csv"
DEFAULT_TRADES_PATH = "data/round0/trades_round_0_day_-1.csv"
DEFAULT_OUTPUT_PATH = "dashboard/examples/backtest_trades_trader_test_dashboard_day_-1_generated.csv"


class Trader:
    def bid(self):
        return 15

    def run(self, state: TradingState):
        try:
            trader_state = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            trader_state = {}

        tomatoes_ema = trader_state.get("tomatoes_ema")
        result: Dict[str, List[Order]] = {}

        for product, depth in state.order_depths.items():
            position = state.position.get(product, 0)

            if product == EMERALDS:
                result[product] = self._trade_emeralds(depth, position)
            elif product == TOMATOES:
                orders, tomatoes_ema = self._trade_tomatoes(depth, position, tomatoes_ema)
                result[product] = orders
            else:
                result[product] = []

        trader_data = json.dumps({"tomatoes_ema": tomatoes_ema})
        return result, 0, trader_data

    def _trade_emeralds(self, depth: OrderDepth, position: int) -> List[Order]:
        orders: List[Order] = []
        buy_capacity = EMERALDS_LIMIT - position
        sell_capacity = EMERALDS_LIMIT + position

        best_bid = max(depth.buy_orders) if depth.buy_orders else None
        best_ask = min(depth.sell_orders) if depth.sell_orders else None

        for ask_price, ask_volume in sorted(depth.sell_orders.items()):
            if ask_price < EMERALDS_FV and buy_capacity > 0:
                qty = min(-ask_volume, buy_capacity)
                if qty > 0:
                    orders.append(Order(EMERALDS, ask_price, qty))
                    position += qty
                    buy_capacity -= qty
                    sell_capacity = EMERALDS_LIMIT + position

        for bid_price, bid_volume in sorted(depth.buy_orders.items(), reverse=True):
            if bid_price > EMERALDS_FV and sell_capacity > 0:
                qty = min(bid_volume, sell_capacity)
                if qty > 0:
                    orders.append(Order(EMERALDS, bid_price, -qty))
                    position -= qty
                    sell_capacity -= qty
                    buy_capacity = EMERALDS_LIMIT - position

        if best_ask == EMERALDS_FV and position < 0 and buy_capacity > 0:
            qty = min(-depth.sell_orders[best_ask], min(4, buy_capacity, -position))
            if qty > 0:
                orders.append(Order(EMERALDS, best_ask, qty))
                position += qty
                buy_capacity -= qty
                sell_capacity = EMERALDS_LIMIT + position

        if best_bid == EMERALDS_FV and position > 0 and sell_capacity > 0:
            qty = min(depth.buy_orders[best_bid], min(4, sell_capacity, position))
            if qty > 0:
                orders.append(Order(EMERALDS, best_bid, -qty))
                position -= qty
                sell_capacity -= qty
                buy_capacity = EMERALDS_LIMIT - position

        if best_bid is None or best_ask is None:
            return orders

        inventory_ratio = position / EMERALDS_LIMIT
        skew = int(round(inventory_ratio * 2))

        target_bid = EMERALDS_FV - 2 - skew
        target_ask = EMERALDS_FV + 2 - skew

        passive_bid = min(best_bid + 1, target_bid)
        passive_ask = max(best_ask - 1, target_ask)

        if passive_bid >= passive_ask:
            passive_bid = min(passive_bid, passive_ask - 1)
            passive_ask = max(passive_ask, passive_bid + 1)

        buy_size = self._scaled_size(buy_capacity, inventory_ratio, same_side=True)
        sell_size = self._scaled_size(sell_capacity, inventory_ratio, same_side=False)

        if buy_size > 0:
            orders.append(Order(EMERALDS, passive_bid, buy_size))
        if sell_size > 0:
            orders.append(Order(EMERALDS, passive_ask, -sell_size))

        return orders

    def _trade_tomatoes(
        self, depth: OrderDepth, position: int, ema: float | None
    ) -> Tuple[List[Order], float | None]:
        orders: List[Order] = []

        if not depth.buy_orders or not depth.sell_orders:
            return orders, ema

        best_bid = max(depth.buy_orders)
        best_ask = min(depth.sell_orders)
        mid_price = (best_bid + best_ask) / 2.0

        if ema is None:
            ema = mid_price
        else:
            ema = 0.30 * mid_price + 0.70 * ema

        spread = best_ask - best_bid
        inventory_ratio = position / TOMATOES_LIMIT
        fair_value = ema - (inventory_ratio * 2.0)

        buy_capacity = TOMATOES_LIMIT - position
        sell_capacity = TOMATOES_LIMIT + position

        take_buy_edge = 1.0 + max(0.0, inventory_ratio) * 0.75
        take_sell_edge = 1.0 + max(0.0, -inventory_ratio) * 0.75

        for ask_price, ask_volume in sorted(depth.sell_orders.items()):
            if ask_price <= fair_value - take_buy_edge and buy_capacity > 0:
                qty = min(-ask_volume, buy_capacity)
                if qty > 0:
                    orders.append(Order(TOMATOES, ask_price, qty))
                    position += qty
                    buy_capacity -= qty
                    sell_capacity = TOMATOES_LIMIT + position

        for bid_price, bid_volume in sorted(depth.buy_orders.items(), reverse=True):
            if bid_price >= fair_value + take_sell_edge and sell_capacity > 0:
                qty = min(bid_volume, sell_capacity)
                if qty > 0:
                    orders.append(Order(TOMATOES, bid_price, -qty))
                    position -= qty
                    sell_capacity -= qty
                    buy_capacity = TOMATOES_LIMIT - position

        inventory_ratio = position / TOMATOES_LIMIT
        fair_value = ema - (inventory_ratio * 2.0)

        base_edge = 2 if spread <= 6 else 3
        if abs(inventory_ratio) > 0.65:
            base_edge += 1

        target_bid = math.floor(fair_value) - base_edge
        target_ask = math.ceil(fair_value) + base_edge

        passive_bid = min(best_bid + 1, target_bid)
        passive_ask = max(best_ask - 1, target_ask)

        if passive_bid >= best_ask:
            passive_bid = best_ask - 1
        if passive_ask <= best_bid:
            passive_ask = best_bid + 1

        if passive_bid >= passive_ask:
            passive_bid = min(passive_bid, passive_ask - 1)
            passive_ask = max(passive_ask, passive_bid + 1)

        front_buy = self._scaled_size(min(buy_capacity, 16), inventory_ratio, same_side=True)
        front_sell = self._scaled_size(min(sell_capacity, 16), inventory_ratio, same_side=False)

        reserve_buy = max(0, buy_capacity - front_buy)
        reserve_sell = max(0, sell_capacity - front_sell)

        if front_buy > 0:
            orders.append(Order(TOMATOES, passive_bid, front_buy))
        if front_sell > 0:
            orders.append(Order(TOMATOES, passive_ask, -front_sell))

        if reserve_buy > 0 and passive_bid - 1 > 0 and inventory_ratio < 0.35:
            second_buy = min(max(2, reserve_buy // 2), reserve_buy)
            if second_buy > 0:
                orders.append(Order(TOMATOES, passive_bid - 1, second_buy))

        if reserve_sell > 0 and inventory_ratio > -0.35:
            second_sell = min(max(2, reserve_sell // 2), reserve_sell)
            if second_sell > 0:
                orders.append(Order(TOMATOES, passive_ask + 1, -second_sell))

        return orders, ema

    def _scaled_size(self, capacity: int, inventory_ratio: float, same_side: bool) -> int:
        if capacity <= 0:
            return 0

        pressure = inventory_ratio if same_side else -inventory_ratio
        scale = 0.95 - max(0.0, pressure) * 0.75
        scale = max(0.20, min(1.0, scale))
        return max(1, int(capacity * scale))


# ---------------------------------------------------------------------------
# Local dashboard export helpers
# ---------------------------------------------------------------------------


def _load_price_snapshots(prices_path: str):
    snapshots: Dict[int, Dict[str, dict]] = {}

    with open(prices_path, newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        for row in reader:
            timestamp = int(row["timestamp"])
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

    return dict(sorted(snapshots.items()))


def _load_public_trades(trades_path: str):
    trades: Dict[tuple[int, str], list[dict]] = {}

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


def _simulate_fills_for_order(order: Order, depth: OrderDepth, public_trades: list[dict]):
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


def export_dashboard_overlay(prices_path: str, trades_path: str, output_path: str):
    snapshots = _load_price_snapshots(prices_path)
    public_trades = _load_public_trades(trades_path)

    trader = Trader()
    trader_data = ""
    positions = {EMERALDS: 0, TOMATOES: 0}
    cash = {EMERALDS: 0.0, TOMATOES: 0.0}
    overlay_rows = []

    for timestamp, products in snapshots.items():
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
    }


def _parse_args():
    parser = argparse.ArgumentParser(description="Export a dashboard overlay from trader_test_dashboard.py")
    parser.add_argument("--prices", default=DEFAULT_PRICES_PATH, help="Path to a Prosperity prices CSV")
    parser.add_argument("--trades", default=DEFAULT_TRADES_PATH, help="Path to a Prosperity trades CSV")
    parser.add_argument("--out", default=DEFAULT_OUTPUT_PATH, help="Output CSV for the dashboard overlay")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    summary = export_dashboard_overlay(args.prices, args.trades, args.out)
    print(json.dumps(summary, indent=2))
