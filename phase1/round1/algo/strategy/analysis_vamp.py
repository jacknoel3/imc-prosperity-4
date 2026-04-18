"""
Deep-VAMP event study for INTARIAN_PEPPER_ROOT.

This version uses a depth-based VAMP that can lie outside the top of book:

    bid_vwap_3  = sum(bid_price_i * bid_vol_i) / total_bid_vol_3
    ask_vwap_3  = sum(ask_price_i * ask_vol_i) / total_ask_vol_3
    deep_vamp   = (ask_vwap_3 * total_bid_vol_3 + bid_vwap_3 * total_ask_vol_3)
                  / (total_bid_vol_3 + total_ask_vol_3)

That matches the visual behavior in the screenshot:
- deep VAMP can be above best ask
- deep VAMP can be below best bid
"""

import csv
import os
import statistics
from collections import defaultdict

DATA_DIR = "/home/lucas_albanese/imc-prosperity-4/imc-prosperity-4/data/round1_bt/round1"
PRODUCT = "INTARIAN_PEPPER_ROOT"

BUY_SIZE = 6
SELL_SIZE = 7


def to_int(value: str) -> int | None:
    value = (value or "").strip()
    return int(value) if value else None


rows: list[dict] = []
for day in [-2, -1, 0]:
    fname = os.path.join(DATA_DIR, f"prices_round_1_day_{day}.csv")
    with open(fname, "r") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            if row["product"] != PRODUCT:
                continue

            bids: list[tuple[int, int]] = []
            asks: list[tuple[int, int]] = []
            for level in [1, 2, 3]:
                bid_price = to_int(row.get(f"bid_price_{level}", ""))
                bid_volume = to_int(row.get(f"bid_volume_{level}", ""))
                ask_price = to_int(row.get(f"ask_price_{level}", ""))
                ask_volume = to_int(row.get(f"ask_volume_{level}", ""))

                if bid_price is not None and bid_volume is not None:
                    bids.append((bid_price, abs(bid_volume)))
                if ask_price is not None and ask_volume is not None:
                    asks.append((ask_price, abs(ask_volume)))

            if not bids or not asks:
                continue

            total_bid_vol = sum(volume for _, volume in bids)
            total_ask_vol = sum(volume for _, volume in asks)
            bid_vwap = sum(price * volume for price, volume in bids) / total_bid_vol
            ask_vwap = sum(price * volume for price, volume in asks) / total_ask_vol
            deep_vamp = (
                ask_vwap * total_bid_vol + bid_vwap * total_ask_vol
            ) / (total_bid_vol + total_ask_vol)

            rows.append(
                {
                    "day": day,
                    "timestamp": int(row["timestamp"]),
                    "best_bid": bids[0][0],
                    "bid1_vol": bids[0][1],
                    "bid2": bids[1][0] if len(bids) > 1 else None,
                    "best_ask": asks[0][0],
                    "ask1_vol": asks[0][1],
                    "ask2": asks[1][0] if len(asks) > 1 else None,
                    "mid": (bids[0][0] + asks[0][0]) / 2.0,
                    "total_bid_vol": total_bid_vol,
                    "total_ask_vol": total_ask_vol,
                    "bid_vwap": bid_vwap,
                    "ask_vwap": ask_vwap,
                    "deep_vamp": deep_vamp,
                }
            )

print(f"Loaded {len(rows)} snapshots for {PRODUCT}")

for row in rows:
    row["buy_signal"] = row["best_ask"] < row["deep_vamp"] and row["ask1_vol"] <= BUY_SIZE
    row["sell_signal"] = row["best_bid"] > row["deep_vamp"] and row["bid1_vol"] <= SELL_SIZE

by_day: defaultdict[int, list[dict]] = defaultdict(list)
for row in rows:
    by_day[row["day"]].append(row)
for day in by_day:
    by_day[day].sort(key=lambda item: item["timestamp"])

all_rows: list[dict] = []
for day in sorted(by_day):
    all_rows.extend(by_day[day])

for index, row in enumerate(all_rows):
    for horizon in [1, 3, 5, 10]:
        next_index = index + horizon
        if next_index < len(all_rows) and all_rows[next_index]["day"] == row["day"]:
            next_row = all_rows[next_index]
            row[f"fwd_mid_{horizon}"] = next_row["mid"] - row["mid"]
            row[f"exit_buy_bid_{horizon}"] = next_row["best_bid"] - row["best_ask"]
            row[f"exit_sell_ask_{horizon}"] = row["best_bid"] - next_row["best_ask"]
        else:
            row[f"fwd_mid_{horizon}"] = None
            row[f"exit_buy_bid_{horizon}"] = None
            row[f"exit_sell_ask_{horizon}"] = None


def print_forward_stats(label: str, subset: list[dict], sign: int = 1) -> None:
    print(f"{label}: N={len(subset)}")
    if not subset:
        return
    for horizon in [1, 3, 5, 10]:
        values = [
            row[f"fwd_mid_{horizon}"] * sign
            for row in subset
            if row[f"fwd_mid_{horizon}"] is not None
        ]
        if values:
            mean_value = statistics.mean(values)
            positive_rate = 100 * sum(value > 0 for value in values) / len(values)
            print(
                f"  fwd_mid_{horizon:>2}: mean={mean_value:+.3f} "
                f"pct_positive={positive_rate:.1f}% n={len(values)}"
            )


def print_exec_stats(label: str, subset: list[dict], key: str) -> None:
    print(label)
    for horizon in [1, 3, 5, 10]:
        values = [row[f"{key}_{horizon}"] for row in subset if row[f"{key}_{horizon}"] is not None]
        if values:
            mean_value = statistics.mean(values)
            positive_rate = 100 * sum(value > 0 for value in values) / len(values)
            print(
                f"  exec_{horizon:>2}: mean={mean_value:+.3f} "
                f"pct_positive={positive_rate:.1f}% n={len(values)}"
            )


baseline = [row for row in all_rows if row["fwd_mid_1"] is not None]
buy_rows = [row for row in all_rows if row["buy_signal"]]
sell_rows = [row for row in all_rows if row["sell_signal"]]

print("\n" + "=" * 72)
print("BASELINE")
print("=" * 72)
print_forward_stats("all ticks", baseline, sign=1)

print("\n" + "=" * 72)
print("DEEP-VAMP SIGNALS")
print("=" * 72)
print_forward_stats("buy: ask1 < deep_vamp and ask1_vol <= 6", buy_rows, sign=1)
print_forward_stats("sell: bid1 > deep_vamp and bid1_vol <= 7", sell_rows, sign=-1)

print("\n" + "=" * 72)
print("EXECUTABLE ROUND-TRIP CHECK")
print("=" * 72)
print_exec_stats("buy at ask1, exit on later bid1", buy_rows, "exit_buy_bid")
print_exec_stats("sell at bid1, exit on later ask1", sell_rows, "exit_sell_ask")

print("\n" + "=" * 72)
print("FREQUENCY AND GAP")
print("=" * 72)
print(
    f"ask1 < deep_vamp: {sum(row['best_ask'] < row['deep_vamp'] for row in all_rows)} "
    f"({100 * sum(row['best_ask'] < row['deep_vamp'] for row in all_rows) / len(all_rows):.2f}%)"
)
print(
    f"bid1 > deep_vamp: {sum(row['best_bid'] > row['deep_vamp'] for row in all_rows)} "
    f"({100 * sum(row['best_bid'] > row['deep_vamp'] for row in all_rows) / len(all_rows):.2f}%)"
)
print(
    f"buy joint count: {len(buy_rows)} "
    f"({100 * len(buy_rows) / len(all_rows):.2f}%)"
)
print(
    f"sell joint count: {len(sell_rows)} "
    f"({100 * len(sell_rows) / len(all_rows):.2f}%)"
)
print(
    f"mean deep_vamp - ask1 on ask1 < deep_vamp: "
    f"{statistics.mean(row['deep_vamp'] - row['best_ask'] for row in all_rows if row['best_ask'] < row['deep_vamp']):+.3f}"
)
print(
    f"mean bid1 - deep_vamp on bid1 > deep_vamp: "
    f"{statistics.mean(row['best_bid'] - row['deep_vamp'] for row in all_rows if row['best_bid'] > row['deep_vamp']):+.3f}"
)

print("\n" + "=" * 72)
print("VERDICT")
print("=" * 72)
print("Deep-VAMP signals are real and do fire in the replay data.")
print("They predict the next mid move very strongly.")
print("But aggressive entry + short-horizon aggressive exit is still negative.")
print("So the signal is useful as a regime / accumulation overlay, not as a pure scalp by itself.")
