from __future__ import annotations

import argparse
import csv
import importlib.util
import sys
from pathlib import Path
from typing import Dict, List, Tuple


ROOT = Path(__file__).resolve().parents[4]
DATA_DIR = ROOT / "phase2" / "round4" / "algo" / "data"
DEFAULT_TRADER = ROOT / "phase2" / "round4" / "algo" / "strategy" / "hydrogel_gui_v1.py"
DEFAULT_OUTPUT = (
    ROOT
    / "phase2"
    / "round4"
    / "algo"
    / "dashboard"
    / "examples"
    / "hydrogel_gui_v1_dashboard_trades.csv"
)
PRODUCT = "HYDROGEL_PACK"
LIMIT = 200

sys.path.insert(0, str(ROOT / "misc"))
from datamodel import Listing, Observation, OrderDepth, Trade, TradingState  # noqa: E402


def load_trader(path: Path):
    spec = importlib.util.spec_from_file_location("_hydrogel_dashboard_export_trader", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load trader from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Trader()


def build_depth(row: Dict[str, str]) -> OrderDepth:
    depth = OrderDepth()
    for level in (1, 2, 3):
        bid_price = row.get(f"bid_price_{level}", "")
        bid_volume = row.get(f"bid_volume_{level}", "")
        ask_price = row.get(f"ask_price_{level}", "")
        ask_volume = row.get(f"ask_volume_{level}", "")
        if bid_price not in ("", None) and bid_volume not in ("", None):
            depth.buy_orders[int(float(bid_price))] = int(float(bid_volume))
        if ask_price not in ("", None) and ask_volume not in ("", None):
            depth.sell_orders[int(float(ask_price))] = -int(float(ask_volume))
    return depth


def load_price_rows(day: int) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    with open(DATA_DIR / f"prices_round_4_day_{day}.csv", newline="") as handle:
        for row in csv.DictReader(handle, delimiter=";"):
            if row.get("product") != PRODUCT:
                continue
            depth = build_depth(row)
            if not depth.buy_orders or not depth.sell_orders:
                continue
            best_bid = max(depth.buy_orders)
            best_ask = min(depth.sell_orders)
            rows.append(
                {
                    "day": day,
                    "timestamp": int(row["timestamp"]),
                    "depth": depth,
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                    "mid": (best_bid + best_ask) / 2.0,
                }
            )
    return rows


def load_market_trades(day: int) -> Dict[int, List[Trade]]:
    trades_by_timestamp: Dict[int, List[Trade]] = {}
    with open(DATA_DIR / f"trades_round_4_day_{day}.csv", newline="") as handle:
        for row in csv.DictReader(handle, delimiter=";"):
            if row.get("symbol") != PRODUCT:
                continue
            timestamp = int(row["timestamp"])
            trade = Trade(
                PRODUCT,
                int(float(row["price"])),
                int(float(row["quantity"])),
                row.get("buyer", ""),
                row.get("seller", ""),
                timestamp,
            )
            trades_by_timestamp.setdefault(timestamp, []).append(trade)
    return trades_by_timestamp


def fill_active_orders(orders, row, position: int) -> Tuple[List[Dict[str, object]], int]:
    fills: List[Dict[str, object]] = []
    depth: OrderDepth = row["depth"]  # type: ignore[assignment]
    best_bid = int(row["best_bid"])
    best_ask = int(row["best_ask"])
    for order in orders:
        quantity = int(order.quantity)
        if quantity > 0 and int(order.price) >= best_ask and position < LIMIT:
            visible = max(0, -int(depth.sell_orders.get(best_ask, 0)))
            filled = min(quantity, visible, LIMIT - position)
            if filled > 0:
                position += filled
                fills.append(
                    {
                        "side": "buy",
                        "price": best_ask,
                        "quantity": filled,
                        "fill_type": "active_cross",
                        "buyer": "SUBMISSION",
                        "seller": "BOOK",
                    }
                )
        elif quantity < 0 and int(order.price) <= best_bid and position > -LIMIT:
            visible = max(0, int(depth.buy_orders.get(best_bid, 0)))
            filled = min(-quantity, visible, LIMIT + position)
            if filled > 0:
                position -= filled
                fills.append(
                    {
                        "side": "sell",
                        "price": best_bid,
                        "quantity": filled,
                        "fill_type": "active_cross",
                        "buyer": "BOOK",
                        "seller": "SUBMISSION",
                    }
                )
    return fills, position


def fill_passive_orders(orders, row, market_trades: List[Trade], position: int) -> Tuple[List[Dict[str, object]], int]:
    fills: List[Dict[str, object]] = []
    mid = float(row["mid"])
    passive_buys = [
        {"price": int(order.price), "remaining": int(order.quantity)}
        for order in orders
        if int(order.quantity) > 0 and int(order.price) < int(row["best_ask"])
    ]
    passive_sells = [
        {"price": int(order.price), "remaining": -int(order.quantity)}
        for order in orders
        if int(order.quantity) < 0 and int(order.price) > int(row["best_bid"])
    ]
    passive_buys.sort(key=lambda item: item["price"], reverse=True)
    passive_sells.sort(key=lambda item: item["price"])

    for trade in market_trades:
        trade_price = int(trade.price)
        remaining_trade_qty = int(trade.quantity)
        if trade_price <= mid:
            for order in passive_buys:
                if remaining_trade_qty <= 0 or position >= LIMIT:
                    break
                if order["remaining"] <= 0 or trade_price > order["price"]:
                    continue
                filled = min(order["remaining"], remaining_trade_qty, LIMIT - position)
                if filled <= 0:
                    continue
                order["remaining"] -= filled
                remaining_trade_qty -= filled
                position += filled
                fills.append(
                    {
                        "side": "buy",
                        "price": order["price"],
                        "quantity": filled,
                        "fill_type": "passive_market_trade",
                        "buyer": "SUBMISSION",
                        "seller": getattr(trade, "seller", ""),
                    }
                )
        else:
            for order in passive_sells:
                if remaining_trade_qty <= 0 or position <= -LIMIT:
                    break
                if order["remaining"] <= 0 or trade_price < order["price"]:
                    continue
                filled = min(order["remaining"], remaining_trade_qty, LIMIT + position)
                if filled <= 0:
                    continue
                order["remaining"] -= filled
                remaining_trade_qty -= filled
                position -= filled
                fills.append(
                    {
                        "side": "sell",
                        "price": order["price"],
                        "quantity": filled,
                        "fill_type": "passive_market_trade",
                        "buyer": getattr(trade, "buyer", ""),
                        "seller": "SUBMISSION",
                    }
                )
    return fills, position


def export_dashboard_csv(trader_path: Path, output_path: Path, days: List[int]) -> None:
    trader = load_trader(trader_path)
    trader_data = ""
    position = 0
    cash = 0.0
    previous_market_trades: List[Trade] = []
    previous_own_trades: List[Trade] = []
    output_rows: List[Dict[str, object]] = []

    for day in days:
        market_trades_by_ts = load_market_trades(day)
        for row in load_price_rows(day):
            timestamp = int(row["timestamp"])
            state = TradingState(
                traderData=trader_data,
                timestamp=timestamp,
                listings={PRODUCT: Listing(PRODUCT, PRODUCT, "XIRECS")},
                order_depths={PRODUCT: row["depth"]},
                own_trades={PRODUCT: previous_own_trades},
                market_trades={PRODUCT: previous_market_trades},
                position={PRODUCT: position},
                observations=Observation({}, {}),
            )
            result, _, trader_data = trader.run(state)
            orders = result.get(PRODUCT, [])
            current_market_trades = market_trades_by_ts.get(timestamp, [])

            fills, position = fill_active_orders(orders, row, position)
            passive_fills, position = fill_passive_orders(orders, row, current_market_trades, position)
            fills.extend(passive_fills)

            own_trades_for_next: List[Trade] = []
            for fill in fills:
                signed = int(fill["quantity"]) if fill["side"] == "buy" else -int(fill["quantity"])
                cash -= signed * int(fill["price"])
                pnl = cash + position * float(row["mid"])
                output_rows.append(
                    {
                        "timestamp": timestamp,
                        "product": PRODUCT,
                        "price": int(fill["price"]),
                        "quantity": int(fill["quantity"]),
                        "side": fill["side"],
                        "pnl": round(pnl, 3),
                        "position": position,
                        "fill_type": fill["fill_type"],
                    }
                )
                own_trades_for_next.append(
                    Trade(
                        PRODUCT,
                        int(fill["price"]),
                        int(fill["quantity"]),
                        str(fill["buyer"]),
                        str(fill["seller"]),
                        timestamp,
                    )
                )

            previous_own_trades = own_trades_for_next
            previous_market_trades = current_market_trades

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="") as handle:
        fieldnames = ["timestamp", "product", "price", "quantity", "side", "pnl", "position", "fill_type"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)
    print(f"Wrote {len(output_rows)} rows to {output_path}")


def parse_days(raw: str) -> List[int]:
    days = [int(item.strip()) for item in raw.split(",") if item.strip()]
    invalid = [day for day in days if day not in (1, 2, 3)]
    if invalid:
        raise argparse.ArgumentTypeError(f"Unsupported day(s): {invalid}; use 1,2,3")
    return days


def main() -> None:
    parser = argparse.ArgumentParser(description="Export HYDROGEL_PACK strategy fills for the Round 4 dashboard.")
    parser.add_argument("--trader", type=Path, default=DEFAULT_TRADER)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--days", type=parse_days, default=[1, 2, 3], help="Comma-separated day list, e.g. 1,2,3")
    args = parser.parse_args()
    export_dashboard_csv(args.trader, args.output, args.days)


if __name__ == "__main__":
    main()
