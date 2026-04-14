import argparse
import csv
import importlib.util
import json
import re
import uuid
from collections import defaultdict
from pathlib import Path
from types import ModuleType
from typing import Dict, List, Optional, Tuple

from datamodel import Observation, Order, OrderDepth, Trade, TradingState


ACTIVITY_FIELDS = [
    "day",
    "timestamp",
    "product",
    "bid_price_1",
    "bid_volume_1",
    "bid_price_2",
    "bid_volume_2",
    "bid_price_3",
    "bid_volume_3",
    "ask_price_1",
    "ask_volume_1",
    "ask_price_2",
    "ask_volume_2",
    "ask_price_3",
    "ask_volume_3",
    "mid_price",
    "profit_and_loss",
]
DEFAULT_CURRENCY = "XIRECS"
DEFAULT_OUTPUT_PATH = "dashboard_round1/examples/prosperity_like_generated.log"
DEFAULT_OUTPUT_TEMPLATE = "dashboard_round1/examples/prosperity_like_day_{day}_generated.log"
DEFAULT_ROUND_OUTPUT_PATH = "dashboard_round1/examples/prosperity_like_round_generated.log"


def _compute_mid(depth: OrderDepth) -> float:
    if not depth.buy_orders or not depth.sell_orders:
        return 0.0
    return (max(depth.buy_orders) + min(depth.sell_orders)) / 2.0


def _load_strategy_module(strategy_path: str) -> ModuleType:
    strategy_file = Path(strategy_path).resolve()
    module_name = f"strategy_{strategy_file.stem}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, strategy_file)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load strategy module from {strategy_file}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _extract_day_from_name(path: str) -> Optional[int]:
    match = re.search(r"day_(-?\d+)", Path(path).name)
    if not match:
        return None
    return int(match.group(1))


def _parse_int(value: str) -> Optional[int]:
    if value is None or value == "":
        return None
    return int(float(value))


def _parse_float(value: str) -> Optional[float]:
    if value is None or value == "":
        return None
    return float(value)


def _parse_price_row(row: dict) -> Tuple[int, Optional[int], str, OrderDepth, float]:
    timestamp = int(float(row["timestamp"]))
    day_value = row.get("day", "")
    day = int(float(day_value)) if day_value != "" else None
    product = row["product"]
    depth = OrderDepth()

    for level in (1, 2, 3):
        bid_price = _parse_int(row.get(f"bid_price_{level}", ""))
        bid_volume = _parse_int(row.get(f"bid_volume_{level}", ""))
        ask_price = _parse_int(row.get(f"ask_price_{level}", ""))
        ask_volume = _parse_int(row.get(f"ask_volume_{level}", ""))

        if bid_price is not None and bid_volume is not None:
            depth.buy_orders[bid_price] = bid_volume
        if ask_price is not None and ask_volume is not None:
            depth.sell_orders[ask_price] = -ask_volume

    mid_price = _parse_float(row.get("mid_price", "")) or _compute_mid(depth)
    return timestamp, day, product, depth, mid_price


def _load_price_data(prices_path: str, day: Optional[int] = None):
    raw_rows: List[dict] = []
    rows_by_timestamp: Dict[int, List[dict]] = defaultdict(list)
    snapshots: Dict[int, Dict[str, dict]] = defaultdict(dict)
    days_seen = set()

    with open(prices_path, newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        for row in reader:
            timestamp, row_day, product, depth, mid_price = _parse_price_row(row)
            if day is not None and row_day != day:
                continue

            if row_day is not None:
                days_seen.add(row_day)

            normalized = {}
            for field in ACTIVITY_FIELDS:
                if field == "profit_and_loss":
                    normalized[field] = row.get(field, "") or "0.0"
                else:
                    normalized[field] = row.get(field, "")

            raw_rows.append(normalized)
            rows_by_timestamp[timestamp].append(normalized)
            snapshots[timestamp][product] = {
                "depth": depth,
                "mid_price": mid_price,
                "day": row_day,
            }

    return raw_rows, dict(sorted(rows_by_timestamp.items())), dict(sorted(snapshots.items())), sorted(days_seen)


def _load_public_trades(trades_path: str) -> Tuple[List[dict], Dict[Tuple[int, str], List[dict]]]:
    trade_history: List[dict] = []
    grouped: Dict[Tuple[int, str], List[dict]] = defaultdict(list)

    with open(trades_path, newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        for row in reader:
            timestamp = int(float(row["timestamp"]))
            quantity_field = row.get("quantity", row.get("quantity,day", ""))
            quantity = int(float(quantity_field.split(",")[0]))
            trade = {
                "timestamp": timestamp,
                "buyer": row.get("buyer", "") or "",
                "seller": row.get("seller", "") or "",
                "symbol": row["symbol"],
                "currency": row.get("currency", DEFAULT_CURRENCY) or DEFAULT_CURRENCY,
                "price": int(float(row["price"])),
                "quantity": quantity,
            }
            trade_history.append(trade)
            grouped[(timestamp, trade["symbol"])].append(trade)

    trade_history.sort(key=lambda trade: (trade["timestamp"], trade["symbol"], trade["price"], trade["quantity"]))
    return trade_history, grouped


def _default_fill_simulator(order: Order, depth: OrderDepth, public_trades: List[dict]):
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


def _normalize_run_result(run_result):
    if isinstance(run_result, tuple):
        if len(run_result) == 3:
            return run_result[0], run_result[2]
        if len(run_result) == 2:
            return run_result[0], run_result[1]
    return run_result, ""


def _to_trade_objects(trades: List[dict]) -> List[Trade]:
    return [
        Trade(
            symbol=trade["symbol"],
            price=trade["price"],
            quantity=trade["quantity"],
            buyer=trade.get("buyer", ""),
            seller=trade.get("seller", ""),
            timestamp=trade["timestamp"],
        )
        for trade in trades
    ]


def _validate_order_limits(module: ModuleType, positions: Dict[str, int], orders_by_product: Dict[str, List[Order]]) -> str:
    position_limit = getattr(module, "POSITION_LIMIT", None)
    if position_limit is None:
        return ""

    violations = []
    for product, orders in orders_by_product.items():
        projected = positions.get(product, 0) + sum(order.quantity for order in orders)
        if abs(projected) > position_limit:
            violations.append(f"Orders for product {product} exceeded limit of {position_limit} set")

    return "\n".join(violations)


def export_prosperity_like_log(
    strategy_path: str,
    prices_path: str,
    trades_path: str,
    output_path: str,
    day: Optional[int] = None,
):
    module = _load_strategy_module(strategy_path)
    trader = module.Trader()

    raw_rows, rows_by_timestamp, snapshots, days_seen = _load_price_data(prices_path, day=day)
    public_trade_history, public_trades_by_tick = _load_public_trades(trades_path)

    fill_simulator = getattr(module, "_simulate_fills_for_order", _default_fill_simulator)

    positions: Dict[str, int] = defaultdict(int)
    cash: Dict[str, float] = defaultdict(float)
    trader_data = ""
    pending_own_trades: Dict[str, List[Trade]] = defaultdict(list)
    own_trade_history: List[dict] = []
    log_entries: List[dict] = []

    for timestamp, product_rows in rows_by_timestamp.items():
        product_snapshots = snapshots[timestamp]
        order_depths = {
            product: product_data["depth"]
            for product, product_data in product_snapshots.items()
        }

        market_trades = {
            product: _to_trade_objects(public_trades_by_tick.get((timestamp, product), []))
            for product in order_depths
        }
        own_trades = {
            product: pending_own_trades.get(product, [])
            for product in order_depths
        }

        state = TradingState(
            traderData=trader_data,
            timestamp=timestamp,
            listings={},
            order_depths=order_depths,
            own_trades=own_trades,
            market_trades=market_trades,
            position=dict(positions),
            observations=Observation({}, {}),
        )

        raw_result = trader.run(state)
        orders_by_product, trader_data = _normalize_run_result(raw_result)
        if orders_by_product is None:
            orders_by_product = {}

        sandbox_log = _validate_order_limits(module, positions, orders_by_product)
        current_tick_own_trades: Dict[str, List[Trade]] = defaultdict(list)

        for product, orders in orders_by_product.items():
            if product not in product_snapshots:
                continue

            depth = product_snapshots[product]["depth"]
            public_trades = public_trades_by_tick.get((timestamp, product), [])
            for order in orders:
                fills = fill_simulator(order, depth, public_trades)
                for fill_price, fill_qty, side in fills:
                    if side == "buy":
                        positions[product] += fill_qty
                        cash[product] -= fill_price * fill_qty
                        buyer = "SUBMISSION"
                        seller = ""
                    else:
                        positions[product] -= fill_qty
                        cash[product] += fill_price * fill_qty
                        buyer = ""
                        seller = "SUBMISSION"

                    trade_dict = {
                        "timestamp": timestamp,
                        "buyer": buyer,
                        "seller": seller,
                        "symbol": product,
                        "currency": DEFAULT_CURRENCY,
                        "price": int(fill_price),
                        "quantity": int(fill_qty),
                    }
                    own_trade_history.append(trade_dict)
                    current_tick_own_trades[product].append(
                        Trade(
                            symbol=product,
                            price=int(fill_price),
                            quantity=int(fill_qty),
                            buyer=buyer,
                            seller=seller,
                            timestamp=timestamp,
                        )
                    )

        pending_own_trades = current_tick_own_trades

        for row in product_rows:
            product = row["product"]
            mid_price = product_snapshots[product]["mid_price"]
            pnl = cash[product] + positions[product] * mid_price
            row["profit_and_loss"] = f"{round(pnl, 4)}"

        log_entries.append(
            {
                "sandboxLog": sandbox_log,
                "lambdaLog": "",
                "timestamp": timestamp,
            }
        )

    activity_lines = [";".join(ACTIVITY_FIELDS)]
    for row in raw_rows:
        activity_lines.append(";".join(str(row.get(field, "")) for field in ACTIVITY_FIELDS))

    output_payload = {
        "submissionId": str(uuid.uuid4()),
        "activitiesLog": "\n".join(activity_lines),
        "logs": log_entries,
        "tradeHistory": sorted(
            public_trade_history + own_trade_history,
            key=lambda trade: (trade["timestamp"], trade["symbol"], trade["price"], trade["quantity"], trade["buyer"], trade["seller"]),
        ),
    }

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(output_payload, separators=(",", ":")))

    return {
        "output_file": str(output_file),
        "rows": len(raw_rows),
        "timestamps": len(rows_by_timestamp),
        "days": days_seen,
        "own_trades": len(own_trade_history),
        "public_trades": len(public_trade_history),
    }


def export_combined_round_log(
    strategy_path: str,
    prices_path: str,
    trades_path: str,
    output_path: str,
):
    prices_dir = Path(prices_path).parent
    trades_dir = Path(trades_path).parent

    day_files: List[Tuple[int, Path, Path]] = []
    for price_path in prices_dir.glob("prices_round_1_day_*.csv"):
        day = _extract_day_from_name(price_path.name)
        if day is None:
            continue

        trade_path = trades_dir / f"trades_round_1_day_{day}.csv"
        if not trade_path.exists():
            continue
        day_files.append((day, price_path, trade_path))

    day_files.sort(key=lambda item: item[0])
    if not day_files:
        raise ValueError("No matching round 1 day files were found for a combined export.")

    combined_activity_lines = [";".join(ACTIVITY_FIELDS)]
    combined_logs: List[dict] = []
    combined_trade_history: List[dict] = []
    exported_days: List[int] = []

    temp_output = Path(output_path).with_suffix(".tmp.log")

    for day, price_path, trade_path in day_files:
        export_prosperity_like_log(
            strategy_path=strategy_path,
            prices_path=str(price_path),
            trades_path=str(trade_path),
            output_path=str(temp_output),
            day=None,
        )

        payload = json.loads(temp_output.read_text())
        activity_lines = payload["activitiesLog"].splitlines()
        combined_activity_lines.extend(activity_lines[1:])
        combined_logs.extend(payload["logs"])
        combined_trade_history.extend(payload["tradeHistory"])
        exported_days.append(day)

    if temp_output.exists():
        temp_output.unlink()

    combined_payload = {
        "submissionId": str(uuid.uuid4()),
        "activitiesLog": "\n".join(combined_activity_lines),
        "logs": combined_logs,
        "tradeHistory": combined_trade_history,
    }

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(combined_payload, separators=(",", ":")))

    unique_timestamps = set()
    for log_entry in combined_logs:
        unique_timestamps.add(log_entry["timestamp"])

    return {
        "output_file": str(output_file),
        "days": exported_days,
        "rows": len(combined_activity_lines) - 1,
        "log_entries": len(combined_logs),
        "unique_timestamps": len(unique_timestamps),
        "trade_history": len(combined_trade_history),
    }


def export_all_round_files(
    strategy_path: str,
    prices_path: str,
    trades_path: str,
    output_template: str,
):
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

        summary = export_prosperity_like_log(
            strategy_path=strategy_path,
            prices_path=str(price_path),
            trades_path=str(trade_path),
            output_path=output_template.format(day=day),
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
    parser = argparse.ArgumentParser(description="Export a Prosperity-like log from a local strategy file")
    parser.add_argument("--strategy", required=True, help="Path to the strategy Python file")
    parser.add_argument("--prices", help="Path to a Prosperity prices CSV")
    parser.add_argument("--trades", help="Path to a Prosperity trades CSV")
    parser.add_argument("--out", default=DEFAULT_OUTPUT_PATH, help="Output .log/.json path")
    parser.add_argument(
        "--all-round-files",
        action="store_true",
        help="Export one log per matching prices/trades day file in the same folder",
    )
    parser.add_argument(
        "--out-template",
        default=DEFAULT_OUTPUT_TEMPLATE,
        help="Output template for --all-round-files (use {day} in the filename)",
    )
    parser.add_argument(
        "--combined-round-log",
        action="store_true",
        help="Export one combined round-wide log using all matching day files in the folder",
    )
    parser.add_argument(
        "--round-out",
        default=DEFAULT_ROUND_OUTPUT_PATH,
        help="Output path for --combined-round-log",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    module = _load_strategy_module(args.strategy)
    prices_path = args.prices or getattr(module, "DEFAULT_PRICES_PATH", None)
    trades_path = args.trades or getattr(module, "DEFAULT_TRADES_PATH", None)

    if not prices_path or not trades_path:
        raise ValueError("Could not infer prices/trades paths; pass --prices and --trades explicitly.")

    if args.all_round_files:
        summaries = export_all_round_files(
            strategy_path=args.strategy,
            prices_path=prices_path,
            trades_path=trades_path,
            output_template=args.out_template,
        )
        print(json.dumps({"outputs": summaries}, indent=2))
    elif args.combined_round_log:
        summary = export_combined_round_log(
            strategy_path=args.strategy,
            prices_path=prices_path,
            trades_path=trades_path,
            output_path=args.round_out,
        )
        print(json.dumps(summary, indent=2))
    else:
        summary = export_prosperity_like_log(
            strategy_path=args.strategy,
            prices_path=prices_path,
            trades_path=trades_path,
            output_path=args.out,
        )
        print(json.dumps(summary, indent=2))
