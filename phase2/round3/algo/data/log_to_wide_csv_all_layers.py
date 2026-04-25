#!/usr/bin/env python3
"""
Convert an IMC Prosperity JSON .log file into the same wide CSV design used before.

This version does NOT cap the order book at two levels. It detects every bid/ask
layer available in activitiesLog headers, for example bid_price_1 ... bid_price_N
and ask_price_1 ... ask_price_N, and writes all of them to the output CSV.

Output design:
timestamp,
<product_prefix>_bid1, <product_prefix>_bid1_vol, ... <product_prefix>_bidN, <product_prefix>_bidN_vol,
<product_prefix>_ask1, <product_prefix>_ask1_vol, ... <product_prefix>_askN, <product_prefix>_askN_vol,
<product_prefix>_mid, <product_prefix>_spread,
<product_prefix>_position, <product_prefix>_pnl,
<product_prefix>_trade_price, <product_prefix>_trade_qty, <product_prefix>_trade_side,
algo_log

Example:
python log_to_wide_csv_all_layers.py 369580.log 369580_converted.csv
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

TAIL_COLUMNS = [
    "mid", "spread",
    "position", "pnl",
    "trade_price", "trade_qty", "trade_side",
]


def clean_number(value: Any) -> Any:
    """Convert numeric-looking strings to int/float and blanks to empty string."""
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    if text == "":
        return ""
    try:
        number = float(text)
        if number.is_integer():
            return int(number)
        return number
    except ValueError:
        return text


def make_unique_prefixes(products: Iterable[str], style: str = "abbr") -> Dict[str, str]:
    """
    Create deterministic, unique product prefixes.

    style='abbr':
      ASH_COATED_OSMIUM -> aco
      INTARIAN_PEPPER_ROOT -> ipr
      HYDROGEL_PACK -> hp
      VEV_5200 -> vev_5200

    style='full':
      HYDROGEL_PACK -> hydrogel_pack
    """
    products_sorted = sorted(set(products))
    raw: Dict[str, str] = {}

    for product in products_sorted:
        parts = [p for p in re.split(r"[^A-Za-z0-9]+", product) if p]
        if style == "full":
            prefix = re.sub(r"[^A-Za-z0-9]+", "_", product).strip("_").lower()
        elif len(parts) == 2 and parts[0].isalpha() and parts[1].isdigit():
            prefix = f"{parts[0].lower()}_{parts[1]}"
        else:
            prefix = "".join(p[0].lower() for p in parts if p)
            if not prefix:
                prefix = re.sub(r"[^A-Za-z0-9]+", "_", product).strip("_").lower()
        raw[product] = prefix

    used: Dict[str, int] = defaultdict(int)
    unique: Dict[str, str] = {}
    raw_counts: Dict[str, int] = defaultdict(int)
    for prefix in raw.values():
        raw_counts[prefix] += 1

    for product in products_sorted:
        prefix = raw[product]
        used[prefix] += 1
        if used[prefix] == 1 and raw_counts[prefix] == 1:
            unique[product] = prefix
        else:
            suffix = re.sub(r"[^A-Za-z0-9]+", "_", product).strip("_").lower()
            candidate = f"{prefix}_{suffix}"
            n = 2
            while candidate in unique.values():
                candidate = f"{prefix}_{suffix}_{n}"
                n += 1
            unique[product] = candidate

    return unique


def read_log_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def parse_activities_log(activities_log: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    rows: List[Dict[str, Any]] = []
    product_order: List[str] = []

    reader = csv.DictReader(io.StringIO(activities_log), delimiter=";")
    for row in reader:
        product = row.get("product", "")
        if product and product not in product_order:
            product_order.append(product)
        rows.append(row)

    return rows, product_order


def detect_max_book_layers(activity_rows: List[Dict[str, Any]]) -> Tuple[int, int]:
    """Detect max available bid/ask levels from activitiesLog column names."""
    max_bid = 0
    max_ask = 0

    if not activity_rows:
        return max_bid, max_ask

    for col in activity_rows[0].keys():
        bid_match = re.fullmatch(r"bid_price_(\d+)", col)
        ask_match = re.fullmatch(r"ask_price_(\d+)", col)
        if bid_match:
            max_bid = max(max_bid, int(bid_match.group(1)))
        if ask_match:
            max_ask = max(max_ask, int(ask_match.group(1)))

    return max_bid, max_ask


def build_book_columns(max_bid_layers: int, max_ask_layers: int) -> List[str]:
    cols: List[str] = []
    for level in range(1, max_bid_layers + 1):
        cols.extend([f"bid{level}", f"bid{level}_vol"])
    for level in range(1, max_ask_layers + 1):
        cols.extend([f"ask{level}", f"ask{level}_vol"])
    cols.extend(TAIL_COLUMNS)
    return cols


def extract_book_fields(row: Dict[str, Any], max_bid_layers: int, max_ask_layers: int) -> Dict[str, Any]:
    values: Dict[str, Any] = {}

    for level in range(1, max_bid_layers + 1):
        values[f"bid{level}"] = clean_number(row.get(f"bid_price_{level}"))
        values[f"bid{level}_vol"] = clean_number(row.get(f"bid_volume_{level}"))

    for level in range(1, max_ask_layers + 1):
        values[f"ask{level}"] = clean_number(row.get(f"ask_price_{level}"))
        values[f"ask{level}_vol"] = clean_number(row.get(f"ask_volume_{level}"))

    bid1 = values.get("bid1", "")
    ask1 = values.get("ask1", "")
    values["mid"] = clean_number(row.get("mid_price"))
    values["spread"] = ask1 - bid1 if bid1 != "" and ask1 != "" else ""
    values["pnl"] = clean_number(row.get("profit_and_loss"))
    return values


def trade_side_and_signed_qty(trade: Dict[str, Any]) -> Tuple[str, int]:
    qty = int(clean_number(trade.get("quantity", 0)) or 0)
    buyer = str(trade.get("buyer", ""))
    seller = str(trade.get("seller", ""))

    if buyer == "SUBMISSION":
        return "BUY", qty
    if seller == "SUBMISSION":
        return "SELL", -qty
    return "", 0


def build_trade_summary_and_positions(
    trade_history: List[Dict[str, Any]],
    timestamps: List[int],
    products: List[str],
) -> Tuple[Dict[Tuple[int, str], Dict[str, Any]], Dict[Tuple[int, str], int]]:
    by_time_product: Dict[Tuple[int, str], List[Dict[str, Any]]] = defaultdict(list)

    for trade in trade_history:
        try:
            ts = int(clean_number(trade.get("timestamp")))
        except Exception:
            continue
        product = str(trade.get("symbol", ""))
        if product:
            by_time_product[(ts, product)].append(trade)

    trade_summary: Dict[Tuple[int, str], Dict[str, Any]] = {}
    signed_by_time_product: Dict[Tuple[int, str], int] = defaultdict(int)

    for key, trades in by_time_product.items():
        total_qty = 0
        gross_value = 0.0
        signed_qty = 0
        sides = set()

        for trade in trades:
            price = float(clean_number(trade.get("price", 0)) or 0)
            qty = int(clean_number(trade.get("quantity", 0)) or 0)
            side, signed = trade_side_and_signed_qty(trade)

            if qty:
                total_qty += qty
                gross_value += price * qty
            signed_qty += signed
            if side:
                sides.add(side)

        if total_qty:
            if sides == {"BUY"}:
                side_label = "BUY"
            elif sides == {"SELL"}:
                side_label = "SELL"
            elif sides:
                side_label = "BOTH"
            else:
                side_label = ""

            trade_summary[key] = {
                "trade_price": gross_value / total_qty,
                "trade_qty": total_qty,
                "trade_side": side_label,
            }

        signed_by_time_product[key] += signed_qty

    positions: Dict[Tuple[int, str], int] = {}
    running = {product: 0 for product in products}

    for ts in sorted(set(timestamps)):
        for product in products:
            running[product] += signed_by_time_product.get((ts, product), 0)
            positions[(ts, product)] = running[product]

    return trade_summary, positions


def build_algo_logs(logs: List[Dict[str, Any]]) -> Dict[int, str]:
    out: Dict[int, str] = {}
    for item in logs:
        try:
            ts = int(clean_number(item.get("timestamp")))
        except Exception:
            continue

        parts = []
        for key in ("lambdaLog", "sandboxLog"):
            text = str(item.get(key, "") or "").strip()
            if text:
                parts.append(text)

        out[ts] = " | ".join(parts)

    return out


def convert_log_to_wide_csv(
    input_path: Path,
    output_path: Path,
    prefix_style: str = "abbr",
    product_order: Optional[List[str]] = None,
) -> Path:
    data = read_log_json(input_path)
    activity_rows, first_seen_products = parse_activities_log(data.get("activitiesLog", ""))

    all_products = list(first_seen_products)
    for trade in data.get("tradeHistory", []):
        symbol = str(trade.get("symbol", ""))
        if symbol and symbol not in all_products:
            all_products.append(symbol)

    if product_order:
        ordered = [p for p in product_order if p in all_products]
        ordered += [p for p in all_products if p not in ordered]
        all_products = ordered

    prefixes = make_unique_prefixes(all_products, style=prefix_style)
    max_bid_layers, max_ask_layers = detect_max_book_layers(activity_rows)
    book_columns = build_book_columns(max_bid_layers, max_ask_layers)

    timestamps = sorted({int(clean_number(row.get("timestamp"))) for row in activity_rows})

    book_by_time_product: Dict[Tuple[int, str], Dict[str, Any]] = {}
    for row in activity_rows:
        ts = int(clean_number(row.get("timestamp")))
        product = str(row.get("product", ""))
        book_by_time_product[(ts, product)] = extract_book_fields(row, max_bid_layers, max_ask_layers)

    trade_summary, positions = build_trade_summary_and_positions(
        data.get("tradeHistory", []),
        timestamps,
        all_products,
    )
    algo_logs = build_algo_logs(data.get("logs", []))

    header = ["timestamp"]
    for product in all_products:
        prefix = prefixes[product]
        header.extend([f"{prefix}_{col}" for col in book_columns])
    header.append("algo_log")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()

        for ts in timestamps:
            out_row: Dict[str, Any] = {"timestamp": ts}

            for product in all_products:
                prefix = prefixes[product]
                book = book_by_time_product.get((ts, product), {})
                trades = trade_summary.get((ts, product), {})

                values = {col: book.get(col, "") for col in book_columns}
                values.update({
                    "position": positions.get((ts, product), 0),
                    "trade_price": trades.get("trade_price", ""),
                    "trade_qty": trades.get("trade_qty", ""),
                    "trade_side": trades.get("trade_side", ""),
                })

                for col in book_columns:
                    out_row[f"{prefix}_{col}"] = values.get(col, "")

            out_row["algo_log"] = algo_logs.get(ts, "")
            writer.writerow(out_row)

    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert an IMC Prosperity JSON log into a wide CSV with all bid/ask layers."
    )
    parser.add_argument("input_log", type=Path, help="Path to the .log JSON file.")
    parser.add_argument(
        "output_csv",
        type=Path,
        nargs="?",
        help="Output CSV path. Defaults to <input_stem>_converted.csv",
    )
    parser.add_argument(
        "--prefix-style",
        choices=["abbr", "full"],
        default="abbr",
        help="Column prefix style. Default: abbr.",
    )
    parser.add_argument(
        "--product-order",
        nargs="*",
        default=None,
        help="Optional explicit product order. Missing products are appended after these.",
    )

    args = parser.parse_args()

    output_csv = args.output_csv
    if output_csv is None:
        output_csv = args.input_log.with_name(f"{args.input_log.stem}_converted.csv")

    result = convert_log_to_wide_csv(
        input_path=args.input_log,
        output_path=output_csv,
        prefix_style=args.prefix_style,
        product_order=args.product_order,
    )

    print(f"Wrote: {result}")


if __name__ == "__main__":
    main()
