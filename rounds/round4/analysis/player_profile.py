from __future__ import annotations

"""
Build a counterparty profiling dataset from Prosperity Round 4 logs.

This script is designed for the pair-probe strategies in
rounds/round4/algo, but it also works on normal Round 4 submission
logs as long as the JSON contains activitiesLog and tradeHistory.

Outputs:
- trade_events_enriched.csv: every trade enriched with current/future mid prices.
- pair_product_metrics.csv: metrics by buyer, seller, product.
- player_profile_metrics.csv: metrics by player and product.
- network_edges.csv: graph-style buyer/seller edges.
- day_consistency.csv: pair/product stability across days.

Usage:
python3 rounds/round4/analysis/player_profile.py \\
  --input outputs/round4/probe_logs \\
  --out outputs/round4/probe_logs/profile
"""

import argparse
import io
import json
import math
from pathlib import Path
from typing import Iterable

import pandas as pd


PRODUCT_ALIASES = {
    "HYDROGEL_PACK": "HYDROGEL_PACK",
    "VELVETFRUIT_EXTRACT": "VELVETFRUIT_EXTRACT",
    "VEV_4000": "VEV_4000",
    "VEV_4500": "VEV_4500",
    "VEV_5000": "VEV_5000",
    "VEV_5100": "VEV_5100",
    "VEV_5200": "VEV_5200",
    "VEV_5300": "VEV_5300",
    "VEV_5400": "VEV_5400",
    "VEV_5500": "VEV_5500",
    "VEV_6000": "VEV_6000",
    "VEV_6500": "VEV_6500",
}

HORIZONS = (1, 5, 10, 50)
MODE_NAMES = {
    0: "SYMMETRIC_PASSIVE",
    1: "SELL_BAIT_FOR_TARGET_BUYER",
    2: "BUY_BAIT_FOR_TARGET_SELLER",
    3: "TAKE_ASK_IDENTIFY_RESTING_SELLER",
    4: "HIT_BID_IDENTIFY_RESTING_BUYER",
    5: "INVENTORY_FLATTEN",
}


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def iter_log_paths(input_path: Path) -> Iterable[Path]:
    if input_path.is_file():
        yield input_path
        return
    for suffix in ("*.json", "*.log"):
        yield from sorted(input_path.rglob(suffix))


def read_activities(data: dict, source: str) -> pd.DataFrame:
    text = str(data.get("activitiesLog") or "").strip()
    if not text:
        return pd.DataFrame()
    df = pd.read_csv(io.StringIO(text), sep=";")
    if df.empty:
        return df
    df["source_log"] = source
    if "day" not in df.columns:
        df["day"] = 0
    df["product"] = df["product"].map(lambda x: PRODUCT_ALIASES.get(str(x), str(x)))
    for col in ("timestamp", "day"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    numeric_cols = [
        "bid_price_1",
        "ask_price_1",
        "mid_price",
        "profit_and_loss",
        "bid_volume_1",
        "ask_volume_1",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def infer_log_day(data: dict) -> int:
    text = str(data.get("activitiesLog") or "").strip()
    if not text:
        return 0
    try:
        activities = pd.read_csv(io.StringIO(text), sep=";", usecols=["day"])
    except Exception:
        return 0
    days = pd.to_numeric(activities.get("day"), errors="coerce").dropna().astype(int).unique()
    return int(days[0]) if len(days) == 1 else 0


def read_trades(data: dict, source: str) -> pd.DataFrame:
    trades = data.get("tradeHistory") or []
    if not trades:
        return pd.DataFrame()
    df = pd.DataFrame(trades)
    if df.empty:
        return df
    if "symbol" not in df.columns and "product" in df.columns:
        df["symbol"] = df["product"]
    for col in ("buyer", "seller"):
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("").astype(str)
    if "day" not in df.columns:
        df["day"] = infer_log_day(data)
    df["source_log"] = source
    df["symbol"] = df["symbol"].map(lambda x: PRODUCT_ALIASES.get(str(x), str(x)))
    for col in ("timestamp", "day", "price", "quantity"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["timestamp", "price", "quantity", "symbol"])
    df["timestamp"] = df["timestamp"].astype(int)
    df["day"] = df["day"].fillna(0).astype(int)
    df["price"] = df["price"].astype(float)
    df["quantity"] = df["quantity"].astype(float)
    return df


def t_stat(values: pd.Series) -> float:
    x = pd.to_numeric(values, errors="coerce").dropna()
    n = len(x)
    if n < 2:
        return float("nan")
    std = x.std(ddof=1)
    if not std or math.isnan(std):
        return float("nan")
    return float(x.mean() / (std / math.sqrt(n)))


def safe_join_unique(values: pd.Series) -> str:
    out = []
    for value in values.dropna().astype(str):
        if value and value not in out:
            out.append(value)
    return "|".join(out)


def add_future_mids(activities: pd.DataFrame) -> pd.DataFrame:
    if activities.empty:
        return activities
    frames = []
    for _, g in activities.sort_values(["source_log", "day", "product", "timestamp"]).groupby(
        ["source_log", "day", "product"],
        sort=False,
    ):
        g = g.copy()
        for h in HORIZONS:
            g[f"future_mid_{h}"] = g["mid_price"].shift(-h)
        frames.append(g)
    return pd.concat(frames, ignore_index=True)


def enrich_trades(trades: pd.DataFrame, activities: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return trades
    if activities.empty:
        return trades

    cols = ["source_log", "day", "timestamp", "product", "mid_price", "bid_price_1", "ask_price_1"]
    cols += [f"future_mid_{h}" for h in HORIZONS]
    book = activities[cols].rename(columns={"product": "symbol"})
    enriched = trades.merge(book, on=["source_log", "day", "timestamp", "symbol"], how="left")
    enriched["spread"] = enriched["ask_price_1"] - enriched["bid_price_1"]
    enriched["price_minus_mid"] = enriched["price"] - enriched["mid_price"]
    enriched["price_minus_bid"] = enriched["price"] - enriched["bid_price_1"]
    enriched["price_minus_ask"] = enriched["price"] - enriched["ask_price_1"]
    enriched["at_or_above_ask"] = enriched["price"] >= enriched["ask_price_1"]
    enriched["at_or_below_bid"] = enriched["price"] <= enriched["bid_price_1"]
    enriched["probe_mode"] = ((enriched["timestamp"] // 3000) % 6).map(MODE_NAMES)
    for h in HORIZONS:
        enriched[f"buyer_markout_{h}"] = enriched[f"future_mid_{h}"] - enriched["price"]
        enriched[f"seller_markout_{h}"] = enriched["price"] - enriched[f"future_mid_{h}"]
        enriched[f"buyer_profitable_{h}"] = (enriched[f"buyer_markout_{h}"] > 0).where(
            enriched[f"buyer_markout_{h}"].notna(),
        )
    return enriched


def slope_per_tick(g: pd.DataFrame, y_col: str) -> float:
    x = pd.to_numeric(g["timestamp"], errors="coerce")
    y = pd.to_numeric(g[y_col], errors="coerce")
    mask = x.notna() & y.notna()
    x = x[mask]
    y = y[mask]
    if len(x) < 2 or x.max() == x.min():
        return float("nan")
    x = x - x.mean()
    denom = float((x * x).sum())
    if denom == 0:
        return float("nan")
    return float((x * (y - y.mean())).sum() / denom)


def lag1_autocorr(g: pd.DataFrame, y_col: str) -> float:
    y = pd.to_numeric(g[y_col], errors="coerce").dropna()
    if len(y) < 3:
        return float("nan")
    return float(y.autocorr(lag=1))


def pair_product_metrics(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    rows = []
    grouped = trades.sort_values(["source_log", "day", "timestamp"]).groupby(["buyer", "seller", "symbol"], sort=True)
    opposite_avg = trades.groupby(["buyer", "seller", "symbol"])["price"].mean().to_dict()

    for (buyer, seller, product), g in grouped:
        inter = g.sort_values(["source_log", "day", "timestamp"]).groupby(["source_log", "day"])["timestamp"].diff()
        row = {
            "buyer": buyer,
            "seller": seller,
            "product": product,
            "trade_count": int(len(g)),
            "total_qty": float(g["quantity"].sum()),
            "avg_qty": float(g["quantity"].mean()),
            "min_qty": float(g["quantity"].min()),
            "max_qty": float(g["quantity"].max()),
            "unique_qty_values": safe_join_unique(g["quantity"]),
            "avg_price": float(g["price"].mean()),
            "min_price": float(g["price"].min()),
            "max_price": float(g["price"].max()),
            "price_std": float(g["price"].std(ddof=1)) if len(g) > 1 else 0.0,
            "opposite_avg_price": opposite_avg.get((seller, buyer, product), float("nan")),
            "implied_opposite_spread": float("nan"),
            "price_trend_slope_per_tick": slope_per_tick(g, "price"),
            "price_lag1_autocorr": lag1_autocorr(g, "price"),
            "intertrade_time_avg": float(inter.mean()) if inter.notna().any() else float("nan"),
            "intertrade_time_min": float(inter.min()) if inter.notna().any() else float("nan"),
            "intertrade_time_max": float(inter.max()) if inter.notna().any() else float("nan"),
            "intertrade_time_std": float(inter.std(ddof=1)) if inter.notna().sum() > 1 else float("nan"),
            "first_timestamp": int(g["timestamp"].min()),
            "last_timestamp": int(g["timestamp"].max()),
            "active_span": int(g["timestamp"].max() - g["timestamp"].min()),
            "days_traded": safe_join_unique(g["day"]),
            "num_days_traded": int(g["day"].nunique()),
            "source_logs": safe_join_unique(g["source_log"]),
        }
        if not math.isnan(row["opposite_avg_price"]):
            row["implied_opposite_spread"] = row["opposite_avg_price"] - row["avg_price"]
        for h in HORIZONS:
            col = f"buyer_markout_{h}"
            row[f"avg_buyer_markout_{h}"] = float(g[col].mean()) if col in g else float("nan")
            row[f"tstat_buyer_markout_{h}"] = t_stat(g[col]) if col in g else float("nan")
            prob_col = f"buyer_profitable_{h}"
            row[f"prob_buyer_profitable_{h}"] = float(g[prob_col].mean()) if prob_col in g else float("nan")
        rows.append(row)
    return pd.DataFrame(rows)


def player_profile_metrics(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    players = sorted(set(trades["buyer"].dropna()) | set(trades["seller"].dropna()))
    rows = []
    for player in players:
        if not player:
            continue
        buys = trades[trades["buyer"] == player]
        sells = trades[trades["seller"] == player]
        all_side = pd.concat([buys, sells], ignore_index=True)
        if all_side.empty:
            continue
        buy_mo = buys["buyer_markout_10"].mean() if "buyer_markout_10" in buys else float("nan")
        sell_mo = sells["seller_markout_10"].mean() if "seller_markout_10" in sells else float("nan")
        row = {
            "player": player,
            "trades_as_buyer": int(len(buys)),
            "trades_as_seller": int(len(sells)),
            "qty_as_buyer": float(buys["quantity"].sum()) if not buys.empty else 0.0,
            "qty_as_seller": float(sells["quantity"].sum()) if not sells.empty else 0.0,
            "preferred_buy_products": safe_join_unique(buys["symbol"]) if not buys.empty else "",
            "preferred_sell_products": safe_join_unique(sells["symbol"]) if not sells.empty else "",
            "avg_size": float(all_side["quantity"].mean()),
            "size_variance": float(all_side["quantity"].var(ddof=1)) if len(all_side) > 1 else 0.0,
            "out_counterparties": safe_join_unique(buys["seller"]) if not buys.empty else "",
            "in_counterparties": safe_join_unique(sells["buyer"]) if not sells.empty else "",
            "avg_buyer_markout_10": float(buy_mo) if not pd.isna(buy_mo) else float("nan"),
            "avg_seller_markout_10": float(sell_mo) if not pd.isna(sell_mo) else float("nan"),
            "buyer_win_rate_10": float(buys["buyer_profitable_10"].mean()) if "buyer_profitable_10" in buys and not buys.empty else float("nan"),
            "active_days": safe_join_unique(all_side["day"]),
            "num_active_days": int(all_side["day"].nunique()),
            "first_timestamp": int(all_side["timestamp"].min()),
            "last_timestamp": int(all_side["timestamp"].max()),
            "participation_span": int(all_side["timestamp"].max() - all_side["timestamp"].min()),
        }
        row["role_classification"] = classify_player(row)
        rows.append(row)
    return pd.DataFrame(rows)


def classify_player(row: dict) -> str:
    buy = row["trades_as_buyer"]
    sell = row["trades_as_seller"]
    buy_mo = row.get("avg_buyer_markout_10", float("nan"))
    sell_mo = row.get("avg_seller_markout_10", float("nan"))
    total = buy + sell
    if total == 0:
        return "unknown"
    if buy > 4 * max(1, sell):
        if not pd.isna(buy_mo) and buy_mo > 0:
            return "informed_or_aggressive_structural_buyer"
        return "structural_buyer"
    if sell > 4 * max(1, buy):
        if not pd.isna(sell_mo) and sell_mo > 0:
            return "informed_or_aggressive_structural_seller"
        return "structural_seller_liquidity_source"
    if not pd.isna(buy_mo) and not pd.isna(sell_mo) and buy_mo > 0 and sell_mo > 0:
        return "informed_bidirectional_trader"
    if not pd.isna(buy_mo) and buy_mo < 0 and (pd.isna(sell_mo) or sell_mo < 0):
        return "noise_or_adversely_selected_trader"
    return "bidirectional_or_mixed"


def network_edges(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    return (
        trades.groupby(["buyer", "seller"], sort=True)
        .agg(
            trade_count=("quantity", "size"),
            total_qty=("quantity", "sum"),
            products=("symbol", safe_join_unique),
            avg_price=("price", "mean"),
            avg_buyer_markout_10=("buyer_markout_10", "mean"),
            days_traded=("day", safe_join_unique),
            source_logs=("source_log", safe_join_unique),
        )
        .reset_index()
    )


def day_consistency(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    return (
        trades.groupby(["buyer", "seller", "symbol", "day"], sort=True)
        .agg(
            trade_count=("quantity", "size"),
            total_qty=("quantity", "sum"),
            avg_price=("price", "mean"),
            avg_buyer_markout_10=("buyer_markout_10", "mean"),
        )
        .reset_index()
    )


def write_outputs(trades: pd.DataFrame, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    pair_metrics = pair_product_metrics(trades)
    player_metrics = player_profile_metrics(trades)
    edges = network_edges(trades)
    consistency = day_consistency(trades)
    market_trades = trades[(trades["buyer"] != "SUBMISSION") & (trades["seller"] != "SUBMISSION")].copy()
    market_trades = market_trades.drop_duplicates(
        subset=["day", "timestamp", "buyer", "seller", "symbol", "currency", "price", "quantity"],
    )
    submission_trades = trades[(trades["buyer"] == "SUBMISSION") | (trades["seller"] == "SUBMISSION")].copy()
    market_pair_metrics = pair_product_metrics(market_trades)
    market_player_metrics = player_profile_metrics(market_trades)
    market_edges = network_edges(market_trades)

    trades.to_csv(out_dir / "trade_events_enriched.csv", index=False)
    pair_metrics.to_csv(out_dir / "pair_product_metrics.csv", index=False)
    player_metrics.to_csv(out_dir / "player_profile_metrics.csv", index=False)
    edges.to_csv(out_dir / "network_edges.csv", index=False)
    consistency.to_csv(out_dir / "day_consistency.csv", index=False)
    market_trades.to_csv(out_dir / "market_unique_trade_events.csv", index=False)
    submission_trades.to_csv(out_dir / "submission_trade_events.csv", index=False)
    market_pair_metrics.to_csv(out_dir / "market_unique_pair_product_metrics.csv", index=False)
    market_player_metrics.to_csv(out_dir / "market_unique_player_profile_metrics.csv", index=False)
    market_edges.to_csv(out_dir / "market_unique_network_edges.csv", index=False)

    summary = {
        "trade_events": int(len(trades)),
        "submission_trade_events": int(len(submission_trades)),
        "market_unique_trade_events": int(len(market_trades)),
        "pair_product_rows": int(len(pair_metrics)),
        "player_rows": int(len(player_metrics)),
        "network_edges": int(len(edges)),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="JSON/.log file or directory containing logs.")
    parser.add_argument("--out", required=True, help="Output directory for profiling CSV files.")
    args = parser.parse_args()

    all_activities = []
    all_trades = []
    for path in iter_log_paths(Path(args.input)):
        try:
            data = load_json(path)
        except Exception as exc:
            print(f"SKIP|path={path}|reason={exc}")
            continue
        all_activities.append(read_activities(data, path.name))
        trades = read_trades(data, path.name)
        if not trades.empty:
            all_trades.append(trades)

    if not all_trades:
        raise SystemExit("No tradeHistory rows found. Use raw Prosperity JSON/.log files, not only wide converted CSVs.")

    activities = pd.concat([x for x in all_activities if not x.empty], ignore_index=True) if all_activities else pd.DataFrame()
    activities = add_future_mids(activities)
    trades = pd.concat(all_trades, ignore_index=True)
    enriched = enrich_trades(trades, activities)
    write_outputs(enriched, Path(args.out))
    print(f"Saved player profile outputs to {args.out}")


if __name__ == "__main__":
    main()
