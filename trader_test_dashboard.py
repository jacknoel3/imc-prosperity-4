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
from typing import Dict, List

from datamodel import Observation, Order, OrderDepth, TradingState


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EMERALDS = "EMERALDS"
TOMATOES = "TOMATOES"

POSITION_LIMIT = {
    EMERALDS: 80,
    TOMATOES: 35,
}

EMERALD_FAIR_VALUE = 10_000
EMERALD_FRONT_BUY_QUOTE = 9994
EMERALD_FRONT_SELL_QUOTE = 10006
EMERALD_BACK_BUY_QUOTE = 9993
EMERALD_BACK_SELL_QUOTE = 10007
EMERALD_SOFT_INVENTORY = 18
EMERALD_HARD_INVENTORY = 55
EMERALD_FRONT_QUOTE_SIZE = 8
EMERALD_BACK_QUOTE_SIZE = 6
EMERALD_FAIR_INTERACT_SIZE = 6

TOMATO_EMA_ALPHA = 0.30
TOMATO_FAIR_INVENTORY_SKEW = 2.0
TOMATO_MAX_FRONT_SIZE = 16
TOMATO_TAKE_EDGE_BASE = 1.0
TOMATO_EDGE_WIDE = 3
TOMATO_EDGE_TIGHT = 2

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

        if EMERALDS in state.order_depths:
            result[EMERALDS] = self._trade_emeralds(
                state.order_depths[EMERALDS],
                state.position.get(EMERALDS, 0),
            )

        if TOMATOES in state.order_depths:
            tomatoes_orders, tomatoes_ema = self._trade_tomatoes(
                state.order_depths[TOMATOES],
                state.position.get(TOMATOES, 0),
                tomatoes_ema,
            )
            result[TOMATOES] = tomatoes_orders

        if state.timestamp % 10_000 == 0:
            pos_em = state.position.get(EMERALDS, 0)
            pos_to = state.position.get(TOMATOES, 0)
            n_em_orders = len(result.get(EMERALDS, []))
            n_to_orders = len(result.get(TOMATOES, []))
            print(
                f"[t={state.timestamp}] pos_em={pos_em} pos_to={pos_to} "
                f"orders_em={n_em_orders} orders_to={n_to_orders}"
            )

        return result, 0, json.dumps({"tomatoes_ema": tomatoes_ema})

    def _trade_emeralds(self, depth: OrderDepth, position: int) -> List[Order]:
        orders: List[Order] = []
        limit = POSITION_LIMIT[EMERALDS]
        fair = EMERALD_FAIR_VALUE

        buy_capacity = limit - position
        sell_capacity = limit + position
        best_bid = max(depth.buy_orders) if depth.buy_orders else None
        best_ask = min(depth.sell_orders) if depth.sell_orders else None

        if depth.sell_orders:
            for ask_price in sorted(depth.sell_orders):
                if ask_price >= fair:
                    break
                if buy_capacity <= 0:
                    break
                available = -depth.sell_orders[ask_price]
                take_qty = min(available, buy_capacity)
                if take_qty > 0:
                    orders.append(Order(EMERALDS, ask_price, take_qty))
                    buy_capacity -= take_qty
                    position += take_qty

        if depth.buy_orders:
            for bid_price in sorted(depth.buy_orders, reverse=True):
                if bid_price <= fair:
                    break
                if sell_capacity <= 0:
                    break
                available = depth.buy_orders[bid_price]
                take_qty = min(available, sell_capacity)
                if take_qty > 0:
                    orders.append(Order(EMERALDS, bid_price, -take_qty))
                    sell_capacity -= take_qty
                    position -= take_qty

        if best_ask == fair and position < 0 and buy_capacity > 0:
            cover_qty = min(
                -position,
                buy_capacity,
                -depth.sell_orders[best_ask],
                EMERALD_FAIR_INTERACT_SIZE,
            )
            if cover_qty > 0:
                orders.append(Order(EMERALDS, fair, cover_qty))
                buy_capacity -= cover_qty
                position += cover_qty

        if best_bid == fair and position > 0 and sell_capacity > 0:
            trim_qty = min(
                position,
                sell_capacity,
                depth.buy_orders[best_bid],
                EMERALD_FAIR_INTERACT_SIZE,
            )
            if trim_qty > 0:
                orders.append(Order(EMERALDS, fair, -trim_qty))
                sell_capacity -= trim_qty
                position -= trim_qty

        if position > EMERALD_SOFT_INVENTORY and sell_capacity > 0:
            flatten_qty = min(
                position - EMERALD_SOFT_INVENTORY + EMERALD_FAIR_INTERACT_SIZE,
                sell_capacity,
            )
            if flatten_qty > 0:
                orders.append(Order(EMERALDS, fair, -flatten_qty))
                sell_capacity -= flatten_qty
        elif position < -EMERALD_SOFT_INVENTORY and buy_capacity > 0:
            flatten_qty = min(
                -position - EMERALD_SOFT_INVENTORY + EMERALD_FAIR_INTERACT_SIZE,
                buy_capacity,
            )
            if flatten_qty > 0:
                orders.append(Order(EMERALDS, fair, flatten_qty))
                buy_capacity -= flatten_qty

        skew = position // 10
        front_buy_desired = max(0, EMERALD_FRONT_QUOTE_SIZE - skew)
        front_sell_desired = max(0, EMERALD_FRONT_QUOTE_SIZE + skew)
        back_buy_desired = max(0, EMERALD_BACK_QUOTE_SIZE - skew)
        back_sell_desired = max(0, EMERALD_BACK_QUOTE_SIZE + skew)

        if position >= EMERALD_HARD_INVENTORY:
            front_buy_desired = 0
            back_buy_desired = 0
        if position <= -EMERALD_HARD_INVENTORY:
            front_sell_desired = 0
            back_sell_desired = 0

        front_buy_qty = min(front_buy_desired, buy_capacity)
        buy_capacity -= front_buy_qty
        back_buy_qty = min(back_buy_desired, buy_capacity)

        front_sell_qty = min(front_sell_desired, sell_capacity)
        sell_capacity -= front_sell_qty
        back_sell_qty = min(back_sell_desired, sell_capacity)

        if front_buy_qty > 0:
            orders.append(Order(EMERALDS, EMERALD_FRONT_BUY_QUOTE, front_buy_qty))
        if back_buy_qty > 0:
            orders.append(Order(EMERALDS, EMERALD_BACK_BUY_QUOTE, back_buy_qty))
        if front_sell_qty > 0:
            orders.append(Order(EMERALDS, EMERALD_FRONT_SELL_QUOTE, -front_sell_qty))
        if back_sell_qty > 0:
            orders.append(Order(EMERALDS, EMERALD_BACK_SELL_QUOTE, -back_sell_qty))

        return orders

    def _trade_tomatoes(
        self,
        depth: OrderDepth,
        position: int,
        ema: float | None,
    ) -> tuple[List[Order], float | None]:
        orders: List[Order] = []

        if not depth.buy_orders or not depth.sell_orders:
            return orders, ema

        limit = POSITION_LIMIT[TOMATOES]
        best_bid = max(depth.buy_orders)
        best_ask = min(depth.sell_orders)
        mid_price = (best_bid + best_ask) / 2.0

        if ema is None:
            ema = mid_price
        else:
            ema = TOMATO_EMA_ALPHA * mid_price + (1 - TOMATO_EMA_ALPHA) * ema

        spread = best_ask - best_bid
        inventory_ratio = position / limit
        fair_value = ema - inventory_ratio * TOMATO_FAIR_INVENTORY_SKEW

        buy_capacity = limit - position
        sell_capacity = limit + position

        take_buy_edge = TOMATO_TAKE_EDGE_BASE + max(0.0, inventory_ratio) * 0.75
        take_sell_edge = TOMATO_TAKE_EDGE_BASE + max(0.0, -inventory_ratio) * 0.75

        for ask_price, ask_volume in sorted(depth.sell_orders.items()):
            if ask_price <= fair_value - take_buy_edge and buy_capacity > 0:
                qty = min(-ask_volume, buy_capacity)
                if qty > 0:
                    orders.append(Order(TOMATOES, ask_price, qty))
                    position += qty
                    buy_capacity -= qty
                    sell_capacity = limit + position

        for bid_price, bid_volume in sorted(depth.buy_orders.items(), reverse=True):
            if bid_price >= fair_value + take_sell_edge and sell_capacity > 0:
                qty = min(bid_volume, sell_capacity)
                if qty > 0:
                    orders.append(Order(TOMATOES, bid_price, -qty))
                    position -= qty
                    sell_capacity -= qty
                    buy_capacity = limit - position

        inventory_ratio = position / limit
        fair_value = ema - inventory_ratio * TOMATO_FAIR_INVENTORY_SKEW

        base_edge = TOMATO_EDGE_TIGHT if spread <= 6 else TOMATO_EDGE_WIDE
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

        front_buy = self._scaled_size(min(buy_capacity, TOMATO_MAX_FRONT_SIZE), inventory_ratio, same_side=True)
        front_sell = self._scaled_size(min(sell_capacity, TOMATO_MAX_FRONT_SIZE), inventory_ratio, same_side=False)

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


def _clone_depth(depth: OrderDepth) -> OrderDepth:
    cloned = OrderDepth()
    cloned.buy_orders = dict(depth.buy_orders)
    cloned.sell_orders = dict(depth.sell_orders)
    return cloned


def _copy_public_trades(public_trades: list[dict]):
    return [{"price": trade["price"], "quantity": trade["quantity"]} for trade in public_trades]


def _consume_book_fills(order: Order, depth: OrderDepth):
    fills = []
    remaining = abs(order.quantity)

    if order.quantity > 0:
        for ask_price in sorted(list(depth.sell_orders)):
            if ask_price > order.price or remaining <= 0:
                break
            available = -depth.sell_orders[ask_price]
            fill_qty = min(remaining, available)
            if fill_qty > 0:
                fills.append((ask_price, fill_qty, "buy"))
                remaining -= fill_qty
                leftover = available - fill_qty
                if leftover > 0:
                    depth.sell_orders[ask_price] = -leftover
                else:
                    depth.sell_orders.pop(ask_price, None)

    elif order.quantity < 0:
        for bid_price in sorted(list(depth.buy_orders), reverse=True):
            if bid_price < order.price or remaining <= 0:
                break
            available = depth.buy_orders[bid_price]
            fill_qty = min(remaining, available)
            if fill_qty > 0:
                fills.append((bid_price, fill_qty, "sell"))
                remaining -= fill_qty
                leftover = available - fill_qty
                if leftover > 0:
                    depth.buy_orders[bid_price] = leftover
                else:
                    depth.buy_orders.pop(bid_price, None)

    remaining_quantity = remaining if order.quantity > 0 else -remaining
    return fills, remaining_quantity


def _consume_public_trade_fills(order: Order, depth: OrderDepth, public_trades: list[dict]):
    fills = []
    remaining = abs(order.quantity)

    if remaining <= 0:
        return fills, 0

    best_bid = max(depth.buy_orders) if depth.buy_orders else None
    best_ask = min(depth.sell_orders) if depth.sell_orders else None

    if order.quantity > 0:
        if best_ask is not None and order.price >= best_ask:
            return fills, order.quantity

        if best_bid is None or order.price > best_bid:
            priority_fraction = 1.0
        elif order.price == best_bid:
            priority_fraction = 0.5
        else:
            priority_fraction = 0.25

        for trade in public_trades:
            if remaining <= 0:
                break
            if trade["quantity"] <= 0 or trade["price"] > order.price:
                continue
            visible_qty = trade["quantity"]
            tradable_qty = visible_qty if priority_fraction >= 1.0 else max(1, int(visible_qty * priority_fraction))
            fill_qty = min(remaining, visible_qty, tradable_qty)
            if fill_qty > 0:
                fills.append((order.price, fill_qty, "buy"))
                remaining -= fill_qty
                trade["quantity"] -= fill_qty

    elif order.quantity < 0:
        if best_bid is not None and order.price <= best_bid:
            return fills, order.quantity

        if best_ask is None or order.price < best_ask:
            priority_fraction = 1.0
        elif order.price == best_ask:
            priority_fraction = 0.5
        else:
            priority_fraction = 0.25

        for trade in public_trades:
            if remaining <= 0:
                break
            if trade["quantity"] <= 0 or trade["price"] < order.price:
                continue
            visible_qty = trade["quantity"]
            tradable_qty = visible_qty if priority_fraction >= 1.0 else max(1, int(visible_qty * priority_fraction))
            fill_qty = min(remaining, visible_qty, tradable_qty)
            if fill_qty > 0:
                fills.append((order.price, fill_qty, "sell"))
                remaining -= fill_qty
                trade["quantity"] -= fill_qty

    remaining_quantity = remaining if order.quantity > 0 else -remaining
    return fills, remaining_quantity


def _simulate_fills_for_order(order: Order, depth: OrderDepth, public_trades: list[dict], allow_passive: bool):
    fills, remaining_quantity = _consume_book_fills(order, depth)
    if not allow_passive or remaining_quantity == 0:
        return fills, remaining_quantity

    residual_order = Order(order.symbol, order.price, remaining_quantity)
    passive_fills, final_remaining = _consume_public_trade_fills(residual_order, depth, public_trades)
    fills.extend(passive_fills)
    return fills, final_remaining


def _apply_fills(
    product: str,
    timestamp: int,
    mid_price: float,
    fills: list[tuple[int, int, str]],
    positions: dict[str, int],
    cash: dict[str, float],
    overlay_rows: list[dict],
):
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
                "event_type": "fill",
                "timestamp": timestamp,
                "product": product,
                "price": fill_price,
                "quantity": fill_qty,
                "side": side,
                "pnl": round(pnl, 2),
                "position": positions[product],
            }
        )


def _append_quote_row(
    product: str,
    timestamp: int,
    price: int,
    quantity: int,
    side: str,
    mid_price: float,
    positions: dict[str, int],
    cash: dict[str, float],
    overlay_rows: list[dict],
):
    pnl = cash[product] + positions[product] * mid_price
    overlay_rows.append(
        {
            "event_type": "quote",
            "timestamp": timestamp,
            "product": product,
            "price": price,
            "quantity": quantity,
            "side": side,
            "pnl": round(pnl, 2),
            "position": positions[product],
        }
    )


def export_dashboard_overlay(prices_path: str, trades_path: str, output_path: str):
    snapshots = _load_price_snapshots(prices_path)
    public_trades = _load_public_trades(trades_path)

    trader = Trader()
    trader_data = ""
    positions = {EMERALDS: 0, TOMATOES: 0}
    cash = {EMERALDS: 0.0, TOMATOES: 0.0}
    overlay_rows = []
    active_orders = {EMERALDS: [], TOMATOES: []}

    for timestamp, products in snapshots.items():
        for product, pending_orders in active_orders.items():
            if product not in products or not pending_orders:
                continue

            depth = _clone_depth(products[product]["depth"])
            market_trades_for_tick = _copy_public_trades(public_trades.get((timestamp, product), []))
            mid_price = products[product]["mid_price"]

            for order in pending_orders:
                fills, _ = _simulate_fills_for_order(order, depth, market_trades_for_tick, allow_passive=True)
                _apply_fills(product, timestamp, mid_price, fills, positions, cash, overlay_rows)

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
        next_active_orders = {EMERALDS: [], TOMATOES: []}

        for product, orders in result.items():
            if product not in products:
                continue

            depth = _clone_depth(products[product]["depth"])
            mid_price = products[product]["mid_price"]

            for order in orders:
                fills, remaining_quantity = _simulate_fills_for_order(order, depth, [], allow_passive=False)
                _apply_fills(product, timestamp, mid_price, fills, positions, cash, overlay_rows)

                if remaining_quantity != 0:
                    _append_quote_row(
                        product=product,
                        timestamp=timestamp,
                        price=order.price,
                        quantity=abs(remaining_quantity),
                        side="buy" if remaining_quantity > 0 else "sell",
                        mid_price=mid_price,
                        positions=positions,
                        cash=cash,
                        overlay_rows=overlay_rows,
                    )
                    next_active_orders[product].append(Order(product, order.price, remaining_quantity))

        active_orders = next_active_orders

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, "w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["event_type", "timestamp", "product", "price", "quantity", "side", "pnl", "position"],
        )
        writer.writeheader()
        writer.writerows(overlay_rows)

    fill_count = sum(1 for row in overlay_rows if row["event_type"] == "fill")
    quote_count = sum(1 for row in overlay_rows if row["event_type"] == "quote")
    return {
        "output_file": str(output_file),
        "rows": len(overlay_rows),
        "fills": fill_count,
        "quotes": quote_count,
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

