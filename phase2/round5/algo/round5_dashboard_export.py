import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Type

ALGO_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = ALGO_DIR / "dashboard" / "examples"
DATA_DIR = ALGO_DIR / "data"

try:
    from datamodel import Listing, Observation, Order, OrderDepth, TradingState
except ModuleNotFoundError:
    import types

    sys.modules.setdefault("jsonpickle", types.SimpleNamespace(encode=lambda value: str(value)))
    misc_dir = ALGO_DIR.parents[2] / "misc"
    if str(misc_dir) not in sys.path:
        sys.path.insert(0, str(misc_dir))
    from datamodel import Listing, Observation, Order, OrderDepth, TradingState


def export_dashboard_overlay(
    trader_cls: Type,
    prices_path: str,
    trades_path: str,
    output_path: str,
    day: Optional[int] = None,
) -> Dict[str, object]:
    snapshots, day_by_timestamp = _load_price_snapshots(prices_path)
    public_trades = _load_public_trades(trades_path)

    trader = trader_cls()
    trader_data = ""
    positions: Dict[str, int] = {}
    cash: Dict[str, float] = {}
    overlay_rows: List[dict] = []
    inferred_day = day if day is not None else _extract_day_from_name(prices_path)

    for timestamp, products in snapshots.items():
        snapshot_day = day_by_timestamp.get(timestamp, inferred_day)
        if day is not None and snapshot_day != day:
            continue

        order_depths = {
            product: product_data["depth"]
            for product, product_data in products.items()
        }
        listings = {
            product: Listing(product, product, "XIRECS")
            for product in order_depths
        }
        state = TradingState(
            trader_data,
            timestamp,
            listings,
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
            positions.setdefault(product, 0)
            cash.setdefault(product, 0.0)

            for order in orders:
                fills = _simulate_fills_for_order(order, depth, market_trades_for_tick)
                for fill_price, fill_qty, side, fill_type in fills:
                    if side == "buy":
                        positions[product] += fill_qty
                        cash[product] -= fill_price * fill_qty
                    else:
                        positions[product] -= fill_qty
                        cash[product] += fill_price * fill_qty

                    pnl = cash[product] + positions[product] * mid_price
                    overlay_rows.append(
                        {
                            "day": snapshot_day,
                            "timestamp": timestamp,
                            "product": product,
                            "price": fill_price,
                            "quantity": fill_qty,
                            "side": side,
                            "pnl": round(pnl, 2),
                            "position": positions[product],
                            "fill_type": fill_type,
                        }
                    )

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "day",
                "timestamp",
                "product",
                "price",
                "quantity",
                "side",
                "pnl",
                "position",
                "fill_type",
            ],
        )
        writer.writeheader()
        writer.writerows(overlay_rows)

    return {
        "output_file": str(output_file),
        "rows": len(overlay_rows),
        "first_timestamp": overlay_rows[0]["timestamp"] if overlay_rows else None,
        "last_timestamp": overlay_rows[-1]["timestamp"] if overlay_rows else None,
        "day": day if day is not None else inferred_day,
    }


def export_all_days(trader_cls: Type, prices_path: str, trades_path: str, output_template: str) -> List[dict]:
    snapshots, day_by_timestamp = _load_price_snapshots(prices_path)
    _ = snapshots
    days = sorted({day for day in day_by_timestamp.values() if day is not None})
    if not days:
        raise ValueError("No day values were found in the prices CSV.")

    summaries = []
    for day in days:
        summaries.append(
            export_dashboard_overlay(
                trader_cls=trader_cls,
                prices_path=prices_path,
                trades_path=trades_path,
                output_path=output_template.format(day=day),
                day=day,
            )
        )
    return summaries


def export_all_round_files(trader_cls: Type, prices_path: str, trades_path: str, output_template: str) -> List[dict]:
    prices_dir = Path(prices_path).parent
    trades_dir = Path(trades_path).parent

    trade_files = {
        _extract_day_from_name(path.name): path
        for path in trades_dir.glob("trades_round_5_day_*.csv")
    }

    summaries = []
    for price_path in sorted(
        prices_dir.glob("prices_round_5_day_*.csv"),
        key=lambda path: _extract_day_from_name(path.name)
        if _extract_day_from_name(path.name) is not None
        else 999,
    ):
        day = _extract_day_from_name(price_path.name)
        if day is None:
            continue
        trade_path = trade_files.get(day)
        if trade_path is None:
            continue

        summary = export_dashboard_overlay(
            trader_cls=trader_cls,
            prices_path=str(price_path),
            trades_path=str(trade_path),
            output_path=output_template.format(day=day),
            day=day,
        )
        summary["prices_file"] = str(price_path)
        summary["trades_file"] = str(trade_path)
        summaries.append(summary)

    if not summaries:
        raise ValueError("No matching round 5 price/trade file pairs were found.")

    return summaries


def main(trader_cls: Type, script_path_or_stem: str) -> None:
    script_stem = Path(script_path_or_stem).stem
    default_prices_path = str(DATA_DIR / "prices_round_5_day_2.csv")
    default_trades_path = str(DATA_DIR / "trades_round_5_day_2.csv")
    default_output_path = str(OUTPUT_DIR / f"{script_stem}_day_2_generated.csv")
    default_output_template = str(OUTPUT_DIR / f"{script_stem}_day_{{day}}_generated.csv")

    parser = argparse.ArgumentParser(description=f"Export {script_stem} Round 5 dashboard overlay CSVs")
    parser.add_argument("--prices", default=default_prices_path, help="Path to a Prosperity prices CSV")
    parser.add_argument("--trades", default=default_trades_path, help="Path to a Prosperity trades CSV")
    parser.add_argument("--out", default=default_output_path, help="Output CSV for the dashboard overlay")
    parser.add_argument(
        "--all-days",
        action="store_true",
        help="Export one backtest CSV per matching round 5 day file",
    )
    parser.add_argument(
        "--out-template",
        default=default_output_template,
        help="Output template for --all-days. Use {day} in the filename.",
    )
    args = parser.parse_args()

    if args.all_days:
        summaries = export_all_round_files(trader_cls, args.prices, args.trades, args.out_template)
        print(json.dumps({"outputs": summaries}, indent=2))
    else:
        summary = export_dashboard_overlay(trader_cls, args.prices, args.trades, args.out)
        print(json.dumps(summary, indent=2))


def _load_price_snapshots(prices_path: str) -> Tuple[Dict[int, Dict[str, dict]], Dict[int, int]]:
    snapshots: Dict[int, Dict[str, dict]] = {}
    day_by_timestamp: Dict[int, int] = {}

    with open(prices_path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter=_detect_delimiter(handle))
        for row in reader:
            timestamp = int(float(row["timestamp"]))
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
                    depth.buy_orders[int(float(bid_price))] = abs(int(float(bid_volume)))
                if ask_price and ask_volume:
                    depth.sell_orders[int(float(ask_price))] = -abs(int(float(ask_volume)))

            mid_price = float(row["mid_price"]) if row.get("mid_price") else _compute_mid(depth)
            snapshots.setdefault(timestamp, {})[product] = {
                "depth": depth,
                "mid_price": mid_price,
            }

    return dict(sorted(snapshots.items())), day_by_timestamp


def _load_public_trades(trades_path: str) -> Dict[Tuple[int, str], List[dict]]:
    trades: Dict[Tuple[int, str], List[dict]] = {}
    trade_file = Path(trades_path)
    if not trade_file.exists():
        return trades

    with open(trade_file, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter=_detect_delimiter(handle))
        for row in reader:
            key = (int(float(row["timestamp"])), row["symbol"])
            trades.setdefault(key, []).append(
                {
                    "price": int(float(row["price"])),
                    "quantity": int(float(row["quantity"])),
                }
            )

    return trades


def _simulate_fills_for_order(order: Order, depth: OrderDepth, public_trades: List[dict]):
    fills = []
    remaining = abs(int(order.quantity))

    if order.quantity > 0:
        for ask_price in sorted(depth.sell_orders):
            if ask_price > order.price or remaining <= 0:
                break
            available = -depth.sell_orders[ask_price]
            fill_qty = min(remaining, available)
            if fill_qty > 0:
                fills.append((ask_price, fill_qty, "buy", "active"))
                remaining -= fill_qty

        best_ask = min(depth.sell_orders) if depth.sell_orders else None
        if remaining > 0 and best_ask is not None and order.price < best_ask:
            candidate_qty = sum(trade["quantity"] for trade in public_trades if trade["price"] <= order.price)
            passive_fill = min(remaining, max(1, candidate_qty // 2)) if candidate_qty > 0 else 0
            if passive_fill > 0:
                fills.append((int(order.price), passive_fill, "buy", "passive"))

    elif order.quantity < 0:
        for bid_price in sorted(depth.buy_orders, reverse=True):
            if bid_price < order.price or remaining <= 0:
                break
            available = depth.buy_orders[bid_price]
            fill_qty = min(remaining, available)
            if fill_qty > 0:
                fills.append((bid_price, fill_qty, "sell", "active"))
                remaining -= fill_qty

        best_bid = max(depth.buy_orders) if depth.buy_orders else None
        if remaining > 0 and best_bid is not None and order.price > best_bid:
            candidate_qty = sum(trade["quantity"] for trade in public_trades if trade["price"] >= order.price)
            passive_fill = min(remaining, max(1, candidate_qty // 2)) if candidate_qty > 0 else 0
            if passive_fill > 0:
                fills.append((int(order.price), passive_fill, "sell", "passive"))

    return fills


def _compute_mid(depth: OrderDepth) -> float:
    if not depth.buy_orders or not depth.sell_orders:
        return 0.0
    return (max(depth.buy_orders) + min(depth.sell_orders)) / 2.0


def _extract_day_from_name(path: str) -> Optional[int]:
    match = re.search(r"day[_ -]?(-?\d+)", Path(path).name)
    if not match:
        return None
    return int(match.group(1))


def _detect_delimiter(handle) -> str:
    sample = handle.readline()
    handle.seek(0)
    return ";" if ";" in sample else ","
