import csv
import json
from pathlib import Path
from statistics import mean
from typing import Dict, List


def load_overlay(path: str) -> List[dict]:
    rows = []
    with open(path, newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append(
                {
                    "timestamp": int(row["timestamp"]),
                    "product": row["product"],
                    "price": int(float(row["price"])),
                    "quantity": int(float(row["quantity"])),
                    "side": row["side"],
                    "pnl": float(row["pnl"]),
                    "position": int(float(row["position"])),
                }
            )
    return rows


def summarize(rows: List[dict]) -> Dict[str, float]:
    if not rows:
        return {
            "trade_count": 0,
            "buy_count": 0,
            "sell_count": 0,
            "avg_position": 0.0,
            "last_position": 0,
            "max_position": 0,
            "final_pnl": 0.0,
        }

    positions = [r["position"] for r in rows]
    return {
        "trade_count": len(rows),
        "buy_count": sum(1 for r in rows if r["side"] == "buy"),
        "sell_count": sum(1 for r in rows if r["side"] == "sell"),
        "avg_position": round(mean(positions), 4),
        "last_position": positions[-1],
        "max_position": max(positions),
        "final_pnl": rows[-1]["pnl"],
    }


def compare(core60_path: str, core70_path: str) -> Dict[str, object]:
    a = summarize(load_overlay(core60_path))
    b = summarize(load_overlay(core70_path))
    diff = {}
    for key in a.keys():
        diff[key] = round(b[key] - a[key], 4) if isinstance(a[key], float) else b[key] - a[key]
    return {"core60": a, "core70": b, "delta_core70_minus_core60": diff}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Compare PEPPER core60 vs core70 overlay CSVs")
    parser.add_argument("core60")
    parser.add_argument("core70")
    args = parser.parse_args()

    result = compare(args.core60, args.core70)
    print(json.dumps(result, indent=2))
