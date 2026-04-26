from __future__ import annotations

"""
Build historical Round 4 player metrics from public trades_*.csv and prices_*.csv.

This complements the raw-log profiler:
- public CSVs answer "what do players do against each other historically?"
- raw logs answer "who trades against SUBMISSION and how toxic are our fills?"

Outputs:
- dataset_trade_events_enriched.csv
- dataset_pair_product_metrics.csv
- dataset_player_metrics.csv
- dataset_network_edges.csv
- dataset_lead_lag_by_player_product_side.csv
- dataset_lead_lag_by_pair_product.csv
- dataset_summary.json
"""

import argparse
import json
import math
from pathlib import Path

import pandas as pd


HORIZONS = (1, 5, 10, 50, 100, 500)


def safe_join(values: pd.Series) -> str:
    out = []
    for value in values.dropna().astype(str):
        if value and value not in out:
            out.append(value)
    return "|".join(out)


def t_stat(values: pd.Series) -> float:
    x = pd.to_numeric(values, errors="coerce").dropna()
    if len(x) < 2:
        return float("nan")
    std = x.std(ddof=1)
    if not std or math.isnan(std):
        return float("nan")
    return float(x.mean() / (std / math.sqrt(len(x))))


def slope_per_tick(group: pd.DataFrame, y_col: str) -> float:
    x = pd.to_numeric(group["timestamp"], errors="coerce")
    y = pd.to_numeric(group[y_col], errors="coerce")
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


def lag1_autocorr(group: pd.DataFrame, y_col: str) -> float:
    y = pd.to_numeric(group[y_col], errors="coerce").dropna()
    if len(y) < 3:
        return float("nan")
    return float(y.autocorr(lag=1))


def load_trades(data_dir: Path) -> pd.DataFrame:
    frames = []
    for path in sorted(data_dir.glob("trades_round_4_day_*.csv")):
        day = int(path.stem.rsplit("_", 1)[-1])
        df = pd.read_csv(path, sep=";")
        df["day"] = day
        df["source_file"] = path.name
        frames.append(df)
    if not frames:
        raise SystemExit(f"No trades_round_4_day_*.csv files found in {data_dir}")
    trades = pd.concat(frames, ignore_index=True)
    trades = trades.rename(columns={"symbol": "product"})
    for col in ("timestamp", "price", "quantity", "day"):
        trades[col] = pd.to_numeric(trades[col], errors="coerce")
    trades = trades.dropna(subset=["timestamp", "day", "product", "price", "quantity", "buyer", "seller"])
    trades["timestamp"] = trades["timestamp"].astype(int)
    trades["day"] = trades["day"].astype(int)
    trades["price"] = trades["price"].astype(float)
    trades["quantity"] = trades["quantity"].astype(float)
    trades["buyer"] = trades["buyer"].astype(str)
    trades["seller"] = trades["seller"].astype(str)
    trades["currency"] = trades.get("currency", "XIRECS")
    return trades


def load_prices(data_dir: Path) -> pd.DataFrame:
    frames = []
    for path in sorted(data_dir.glob("prices_round_4_day_*.csv")):
        df = pd.read_csv(path, sep=";")
        df["source_file"] = path.name
        frames.append(df)
    if not frames:
        raise SystemExit(f"No prices_round_4_day_*.csv files found in {data_dir}")
    prices = pd.concat(frames, ignore_index=True)
    for col in ("timestamp", "day", "mid_price", "bid_price_1", "ask_price_1"):
        if col in prices.columns:
            prices[col] = pd.to_numeric(prices[col], errors="coerce")
    prices = prices.dropna(subset=["timestamp", "day", "product", "mid_price"])
    prices["timestamp"] = prices["timestamp"].astype(int)
    prices["day"] = prices["day"].astype(int)
    return prices


def add_future_mids(prices: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for _, group in prices.sort_values(["day", "product", "timestamp"]).groupby(["day", "product"], sort=False):
        group = group.copy()
        for horizon in HORIZONS:
            group[f"future_mid_{horizon}"] = group["mid_price"].shift(-horizon)
        frames.append(group)
    return pd.concat(frames, ignore_index=True)


def enrich_trades(trades: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    price_cols = ["day", "timestamp", "product", "bid_price_1", "ask_price_1", "mid_price"]
    price_cols += [f"future_mid_{h}" for h in HORIZONS]
    enriched = trades.merge(prices[price_cols], on=["day", "timestamp", "product"], how="left", validate="many_to_one")
    enriched["spread"] = enriched["ask_price_1"] - enriched["bid_price_1"]
    enriched["price_minus_mid"] = enriched["price"] - enriched["mid_price"]
    enriched["price_minus_bid"] = enriched["price"] - enriched["bid_price_1"]
    enriched["price_minus_ask"] = enriched["price"] - enriched["ask_price_1"]
    for horizon in HORIZONS:
        enriched[f"buyer_markout_{horizon}"] = enriched[f"future_mid_{horizon}"] - enriched["price"]
        enriched[f"seller_markout_{horizon}"] = enriched["price"] - enriched[f"future_mid_{horizon}"]
        enriched[f"buyer_profitable_{horizon}"] = (enriched[f"buyer_markout_{horizon}"] > 0).where(
            enriched[f"buyer_markout_{horizon}"].notna(),
        )
    return enriched


def pair_product_metrics(trades: pd.DataFrame) -> pd.DataFrame:
    rows = []
    opposite_avg = trades.groupby(["buyer", "seller", "product"])["price"].mean().to_dict()
    for (buyer, seller, product), group in trades.sort_values(["day", "timestamp"]).groupby(
        ["buyer", "seller", "product"],
        sort=True,
    ):
        inter = group.sort_values(["day", "timestamp"]).groupby("day")["timestamp"].diff()
        row = {
            "buyer": buyer,
            "seller": seller,
            "product": product,
            "trade_count": int(len(group)),
            "total_qty": float(group["quantity"].sum()),
            "avg_qty": float(group["quantity"].mean()),
            "min_qty": float(group["quantity"].min()),
            "max_qty": float(group["quantity"].max()),
            "unique_qty_values": safe_join(group["quantity"]),
            "avg_price": float(group["price"].mean()),
            "min_price": float(group["price"].min()),
            "max_price": float(group["price"].max()),
            "price_std": float(group["price"].std(ddof=1)) if len(group) > 1 else 0.0,
            "opposite_avg_price": opposite_avg.get((seller, buyer, product), float("nan")),
            "price_trend_slope_per_tick": slope_per_tick(group, "price"),
            "price_lag1_autocorr": lag1_autocorr(group, "price"),
            "intertrade_time_avg": float(inter.mean()) if inter.notna().any() else float("nan"),
            "intertrade_time_min": float(inter.min()) if inter.notna().any() else float("nan"),
            "intertrade_time_max": float(inter.max()) if inter.notna().any() else float("nan"),
            "intertrade_time_std": float(inter.std(ddof=1)) if inter.notna().sum() > 1 else float("nan"),
            "first_timestamp": int(group["timestamp"].min()),
            "last_timestamp": int(group["timestamp"].max()),
            "active_span": int(group["timestamp"].max() - group["timestamp"].min()),
            "days_traded": safe_join(group["day"]),
            "num_days_traded": int(group["day"].nunique()),
        }
        row["implied_opposite_spread"] = (
            row["opposite_avg_price"] - row["avg_price"] if not math.isnan(row["opposite_avg_price"]) else float("nan")
        )
        for horizon in HORIZONS:
            row[f"avg_buyer_markout_{horizon}"] = float(group[f"buyer_markout_{horizon}"].mean())
            row[f"tstat_buyer_markout_{horizon}"] = t_stat(group[f"buyer_markout_{horizon}"])
            row[f"prob_buyer_profitable_{horizon}"] = float(group[f"buyer_profitable_{horizon}"].mean())
            row[f"avg_seller_markout_{horizon}"] = float(group[f"seller_markout_{horizon}"].mean())
        rows.append(row)
    return pd.DataFrame(rows)


def player_metrics(trades: pd.DataFrame) -> pd.DataFrame:
    players = sorted(set(trades["buyer"]) | set(trades["seller"]))
    rows = []
    for player in players:
        buys = trades[trades["buyer"] == player]
        sells = trades[trades["seller"] == player]
        all_side = pd.concat([buys, sells], ignore_index=True)
        row = {
            "player": player,
            "trades_as_buyer": int(len(buys)),
            "trades_as_seller": int(len(sells)),
            "qty_as_buyer": float(buys["quantity"].sum()),
            "qty_as_seller": float(sells["quantity"].sum()),
            "preferred_buy_products": safe_join(buys["product"]) if not buys.empty else "",
            "preferred_sell_products": safe_join(sells["product"]) if not sells.empty else "",
            "buy_counterparties": safe_join(buys["seller"]) if not buys.empty else "",
            "sell_counterparties": safe_join(sells["buyer"]) if not sells.empty else "",
            "avg_size": float(all_side["quantity"].mean()) if not all_side.empty else 0.0,
            "size_std": float(all_side["quantity"].std(ddof=1)) if len(all_side) > 1 else 0.0,
            "active_days": safe_join(all_side["day"]) if not all_side.empty else "",
            "num_active_days": int(all_side["day"].nunique()) if not all_side.empty else 0,
            "first_timestamp": int(all_side["timestamp"].min()) if not all_side.empty else 0,
            "last_timestamp": int(all_side["timestamp"].max()) if not all_side.empty else 0,
        }
        for horizon in HORIZONS:
            row[f"avg_buyer_markout_{horizon}"] = float(buys[f"buyer_markout_{horizon}"].mean()) if not buys.empty else float("nan")
            row[f"avg_seller_markout_{horizon}"] = float(sells[f"seller_markout_{horizon}"].mean()) if not sells.empty else float("nan")
            row[f"buyer_win_rate_{horizon}"] = float(buys[f"buyer_profitable_{horizon}"].mean()) if not buys.empty else float("nan")
            row[f"seller_win_rate_{horizon}"] = float((sells[f"seller_markout_{horizon}"] > 0).mean()) if not sells.empty else float("nan")
        rows.append(row)
    return pd.DataFrame(rows)


def network_edges(trades: pd.DataFrame) -> pd.DataFrame:
    return (
        trades.groupby(["buyer", "seller"], sort=True)
        .agg(
            trade_count=("quantity", "size"),
            total_qty=("quantity", "sum"),
            products=("product", safe_join),
            avg_price=("price", "mean"),
            avg_buyer_markout_10=("buyer_markout_10", "mean"),
            avg_buyer_markout_50=("buyer_markout_50", "mean"),
            buyer_win_rate_10=("buyer_profitable_10", "mean"),
            days=("day", safe_join),
        )
        .reset_index()
    )


def lead_lag_by_player_product_side(trades: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for player in sorted(set(trades["buyer"]) | set(trades["seller"])):
        for product in sorted(trades["product"].unique()):
            for side, mask in (
                ("BUY", (trades["buyer"] == player) & (trades["product"] == product)),
                ("SELL", (trades["seller"] == player) & (trades["product"] == product)),
            ):
                group = trades[mask]
                if group.empty:
                    continue
                row = {
                    "player": player,
                    "product": product,
                    "side": side,
                    "trade_count": int(len(group)),
                    "total_qty": float(group["quantity"].sum()),
                    "avg_price_minus_mid": float(group["price_minus_mid"].mean()),
                    "avg_spread": float(group["spread"].mean()),
                    "days": safe_join(group["day"]),
                }
                for horizon in HORIZONS:
                    col = f"buyer_markout_{horizon}" if side == "BUY" else f"seller_markout_{horizon}"
                    row[f"avg_markout_{horizon}"] = float(group[col].mean())
                    row[f"tstat_markout_{horizon}"] = t_stat(group[col])
                    row[f"win_rate_{horizon}"] = float((group[col] > 0).mean())
                rows.append(row)
    return pd.DataFrame(rows)


def lead_lag_by_pair_product(trades: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (buyer, seller, product), group in trades.groupby(["buyer", "seller", "product"], sort=True):
        row = {
            "buyer": buyer,
            "seller": seller,
            "product": product,
            "trade_count": int(len(group)),
            "total_qty": float(group["quantity"].sum()),
        }
        for horizon in HORIZONS:
            row[f"avg_buyer_markout_{horizon}"] = float(group[f"buyer_markout_{horizon}"].mean())
            row[f"tstat_buyer_markout_{horizon}"] = t_stat(group[f"buyer_markout_{horizon}"])
            row[f"buyer_win_rate_{horizon}"] = float(group[f"buyer_profitable_{horizon}"].mean())
        rows.append(row)
    return pd.DataFrame(rows)


def write_outputs(enriched: pd.DataFrame, prices: pd.DataFrame, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    pair_metrics = pair_product_metrics(enriched)
    players = player_metrics(enriched)
    edges = network_edges(enriched)
    side_lag = lead_lag_by_player_product_side(enriched)
    pair_lag = lead_lag_by_pair_product(enriched)

    enriched.to_csv(out_dir / "dataset_trade_events_enriched.csv", index=False)
    pair_metrics.to_csv(out_dir / "dataset_pair_product_metrics.csv", index=False)
    players.to_csv(out_dir / "dataset_player_metrics.csv", index=False)
    edges.to_csv(out_dir / "dataset_network_edges.csv", index=False)
    side_lag.to_csv(out_dir / "dataset_lead_lag_by_player_product_side.csv", index=False)
    pair_lag.to_csv(out_dir / "dataset_lead_lag_by_pair_product.csv", index=False)

    summary = {
        "trade_events": int(len(enriched)),
        "price_rows": int(len(prices)),
        "missing_mid_trade_events": int(enriched["mid_price"].isna().sum()),
        "unique_days": sorted(int(x) for x in enriched["day"].unique()),
        "unique_products": sorted(enriched["product"].unique().tolist()),
        "unique_players": sorted((set(enriched["buyer"]) | set(enriched["seller"]))),
        "pair_product_rows": int(len(pair_metrics)),
        "player_rows": int(len(players)),
        "network_edges": int(len(edges)),
    }
    (out_dir / "dataset_summary.json").write_text(json.dumps(summary, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True, help="Round 4 data directory.")
    parser.add_argument("--out", required=True, help="Output directory.")
    args = parser.parse_args()

    data_dir = Path(args.data)
    trades = load_trades(data_dir)
    prices = add_future_mids(load_prices(data_dir))
    enriched = enrich_trades(trades, prices)
    write_outputs(enriched, prices, Path(args.out))
    print(f"Saved Round 4 dataset metrics to {args.out}")


if __name__ == "__main__":
    main()
