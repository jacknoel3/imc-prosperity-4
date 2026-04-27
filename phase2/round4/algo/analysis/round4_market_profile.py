from __future__ import annotations

"""
Profile Round 4 historical market data for OOS-aware backtester calibration.

This script complements round4_dataset_profile_metrics.py. That script is
counterparty-first; this one is market/product-first:
- product path, spread, volatility, drift, and mean-reversion diagnostics
- OBI versus next-return toxicity
- trade arrival and price-versus-book markouts
- day-by-day robustness flags for hidden-day risk

Usage:
python3 phase2/round4/algo/analysis/round4_market_profile.py
"""

import argparse
import json
import math
from pathlib import Path

import pandas as pd


HORIZONS = (1, 5, 10, 50, 100, 500)


def autocorr(series: pd.Series, lag: int = 1) -> float:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if len(values) <= lag + 1:
        return float("nan")
    return float(values.autocorr(lag=lag))


def corr(left: pd.Series, right: pd.Series) -> float:
    frame = pd.DataFrame({"left": left, "right": right}).dropna()
    if len(frame) < 3:
        return float("nan")
    if frame["left"].std(ddof=1) == 0 or frame["right"].std(ddof=1) == 0:
        return float("nan")
    return float(frame["left"].corr(frame["right"]))


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


def safe_float(value: object) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return value if math.isfinite(value) else float("nan")


def load_prices(data_dir: Path) -> pd.DataFrame:
    frames = []
    for path in sorted(data_dir.glob("prices_round_4_day_*.csv")):
        frame = pd.read_csv(path, sep=";")
        frame["source_file"] = path.name
        frames.append(frame)
    if not frames:
        raise SystemExit(f"No prices_round_4_day_*.csv files found in {data_dir}")
    prices = pd.concat(frames, ignore_index=True)
    numeric_cols = [
        "day",
        "timestamp",
        "mid_price",
        "bid_price_1",
        "ask_price_1",
        "bid_volume_1",
        "ask_volume_1",
    ]
    for col in numeric_cols:
        if col in prices.columns:
            prices[col] = pd.to_numeric(prices[col], errors="coerce")
    prices = prices.dropna(subset=["day", "timestamp", "product", "mid_price"])
    prices["day"] = prices["day"].astype(int)
    prices["timestamp"] = prices["timestamp"].astype(int)
    prices["spread"] = prices["ask_price_1"] - prices["bid_price_1"]
    depth_sum = prices["bid_volume_1"].fillna(0) + prices["ask_volume_1"].fillna(0)
    prices["obi"] = ((prices["bid_volume_1"].fillna(0) - prices["ask_volume_1"].fillna(0)) / depth_sum).where(
        depth_sum > 0
    )
    return add_future_features(prices)


def load_trades(data_dir: Path) -> pd.DataFrame:
    frames = []
    for path in sorted(data_dir.glob("trades_round_4_day_*.csv")):
        day = int(path.stem.rsplit("_", 1)[-1])
        frame = pd.read_csv(path, sep=";")
        frame["day"] = day
        frame["source_file"] = path.name
        frames.append(frame)
    if not frames:
        raise SystemExit(f"No trades_round_4_day_*.csv files found in {data_dir}")
    trades = pd.concat(frames, ignore_index=True).rename(columns={"symbol": "product"})
    for col in ("day", "timestamp", "price", "quantity"):
        trades[col] = pd.to_numeric(trades[col], errors="coerce")
    trades = trades.dropna(subset=["day", "timestamp", "product", "price", "quantity"])
    trades["day"] = trades["day"].astype(int)
    trades["timestamp"] = trades["timestamp"].astype(int)
    trades["buyer"] = trades["buyer"].fillna("").astype(str)
    trades["seller"] = trades["seller"].fillna("").astype(str)
    return trades


def add_future_features(prices: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for _, group in prices.sort_values(["day", "product", "timestamp"]).groupby(["day", "product"], sort=False):
        group = group.copy()
        group["mid_return_1"] = group["mid_price"].diff()
        for horizon in HORIZONS:
            group[f"future_mid_{horizon}"] = group["mid_price"].shift(-horizon)
            group[f"future_return_{horizon}"] = group[f"future_mid_{horizon}"] - group["mid_price"]
        frames.append(group)
    return pd.concat(frames, ignore_index=True)


def enrich_trades(trades: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    book_cols = [
        "day",
        "timestamp",
        "product",
        "bid_price_1",
        "ask_price_1",
        "mid_price",
        "spread",
    ]
    book_cols += [f"future_mid_{h}" for h in HORIZONS]
    enriched = trades.merge(prices[book_cols], on=["day", "timestamp", "product"], how="left", validate="many_to_one")
    enriched["price_minus_mid"] = enriched["price"] - enriched["mid_price"]
    enriched["price_minus_bid"] = enriched["price"] - enriched["bid_price_1"]
    enriched["price_minus_ask"] = enriched["price"] - enriched["ask_price_1"]
    enriched["at_or_below_bid"] = enriched["price"] <= enriched["bid_price_1"]
    enriched["at_or_above_ask"] = enriched["price"] >= enriched["ask_price_1"]
    for horizon in HORIZONS:
        enriched[f"buyer_markout_{horizon}"] = enriched[f"future_mid_{horizon}"] - enriched["price"]
        enriched[f"seller_markout_{horizon}"] = enriched["price"] - enriched[f"future_mid_{horizon}"]
    return enriched


def product_market_metrics(prices: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    trade_counts = trades.groupby(["day", "product"]).agg(
        trade_count=("quantity", "size"),
        trade_qty=("quantity", "sum"),
        avg_trade_qty=("quantity", "mean"),
        taker_at_bid_rate=("at_or_below_bid", "mean"),
        taker_at_ask_rate=("at_or_above_ask", "mean"),
        avg_abs_trade_mid_offset=("price_minus_mid", lambda x: float(pd.to_numeric(x, errors="coerce").abs().mean())),
        buyer_markout_50=("buyer_markout_50", "mean"),
        seller_markout_50=("seller_markout_50", "mean"),
    )
    rows = []
    for (day, product), group in prices.groupby(["day", "product"], sort=True):
        row = {
            "day": int(day),
            "product": product,
            "rows": int(len(group)),
            "first_mid": safe_float(group["mid_price"].iloc[0]),
            "last_mid": safe_float(group["mid_price"].iloc[-1]),
            "min_mid": safe_float(group["mid_price"].min()),
            "max_mid": safe_float(group["mid_price"].max()),
            "mean_mid": safe_float(group["mid_price"].mean()),
            "std_mid": safe_float(group["mid_price"].std(ddof=0)),
            "drift_mid": safe_float(group["mid_price"].iloc[-1] - group["mid_price"].iloc[0]),
            "slope_per_tick": slope_per_tick(group, "mid_price"),
            "avg_spread": safe_float(group["spread"].mean()),
            "median_spread": safe_float(group["spread"].median()),
            "spread_p95": safe_float(group["spread"].quantile(0.95)),
            "ret_lag1_autocorr": autocorr(group["mid_return_1"], 1),
            "mid_lag1_autocorr": autocorr(group["mid_price"], 1),
            "obi_next_ret_corr_1": corr(group["obi"], group["future_return_1"]),
            "obi_next_ret_corr_5": corr(group["obi"], group["future_return_5"]),
            "obi_next_ret_corr_50": corr(group["obi"], group["future_return_50"]),
        }
        if (day, product) in trade_counts.index:
            row.update(trade_counts.loc[(day, product)].to_dict())
        else:
            row.update(
                {
                    "trade_count": 0,
                    "trade_qty": 0.0,
                    "avg_trade_qty": float("nan"),
                    "taker_at_bid_rate": float("nan"),
                    "taker_at_ask_rate": float("nan"),
                    "avg_abs_trade_mid_offset": float("nan"),
                    "buyer_markout_50": float("nan"),
                    "seller_markout_50": float("nan"),
                }
            )
        rows.append(row)
    return pd.DataFrame(rows)


def aggregate_product_metrics(day_metrics: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for product, group in day_metrics.groupby("product", sort=True):
        rows.append(
            {
                "product": product,
                "days": int(group["day"].nunique()),
                "mean_mid": safe_float(group["mean_mid"].mean()),
                "day_mean_mid_range": safe_float(group["mean_mid"].max() - group["mean_mid"].min()),
                "max_abs_day_drift": safe_float(group["drift_mid"].abs().max()),
                "avg_spread": safe_float(group["avg_spread"].mean()),
                "avg_trade_count_per_day": safe_float(group["trade_count"].mean()),
                "avg_trade_qty_per_day": safe_float(group["trade_qty"].mean()),
                "worst_buyer_markout_50_day": safe_float(group["buyer_markout_50"].min()),
                "best_buyer_markout_50_day": safe_float(group["buyer_markout_50"].max()),
                "avg_obi_next_ret_corr_5": safe_float(group["obi_next_ret_corr_5"].mean()),
                "min_obi_next_ret_corr_5": safe_float(group["obi_next_ret_corr_5"].min()),
                "max_obi_next_ret_corr_5": safe_float(group["obi_next_ret_corr_5"].max()),
                "avg_ret_lag1_autocorr": safe_float(group["ret_lag1_autocorr"].mean()),
            }
        )
    return pd.DataFrame(rows)


def player_flow_metrics(enriched: pd.DataFrame) -> pd.DataFrame:
    rows = []
    players = sorted(set(enriched["buyer"]) | set(enriched["seller"]))
    for player in players:
        buys = enriched[enriched["buyer"] == player]
        sells = enriched[enriched["seller"] == player]
        rows.append(
            {
                "player": player,
                "buy_trades": int(len(buys)),
                "sell_trades": int(len(sells)),
                "buy_qty": safe_float(buys["quantity"].sum()),
                "sell_qty": safe_float(sells["quantity"].sum()),
                "net_qty": safe_float(buys["quantity"].sum() - sells["quantity"].sum()),
                "avg_buyer_markout_50": safe_float(buys["buyer_markout_50"].mean()),
                "avg_seller_markout_50": safe_float(sells["seller_markout_50"].mean()),
                "buyer_win_rate_50": safe_float((buys["buyer_markout_50"] > 0).mean()) if len(buys) else float("nan"),
                "seller_win_rate_50": safe_float((sells["seller_markout_50"] > 0).mean()) if len(sells) else float("nan"),
                "buy_products": "|".join(sorted(buys["product"].dropna().unique())),
                "sell_products": "|".join(sorted(sells["product"].dropna().unique())),
            }
        )
    return pd.DataFrame(rows)


def write_report(out_dir: Path, day_metrics: pd.DataFrame, product_metrics: pd.DataFrame, players: pd.DataFrame) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    day_metrics.to_csv(out_dir / "round4_product_day_metrics.csv", index=False)
    product_metrics.to_csv(out_dir / "round4_product_summary.csv", index=False)
    players.to_csv(out_dir / "round4_player_flow_summary.csv", index=False)

    top_products = product_metrics.sort_values("avg_trade_count_per_day", ascending=False)
    risky = product_metrics.sort_values("day_mean_mid_range", ascending=False)
    toxic_players = players.sort_values("avg_buyer_markout_50", ascending=False)

    summary = {
        "products": int(product_metrics["product"].nunique()),
        "days": sorted(int(x) for x in day_metrics["day"].unique()),
        "most_active_products": top_products[["product", "avg_trade_count_per_day", "avg_trade_qty_per_day"]]
        .head(8)
        .to_dict("records"),
        "largest_day_mean_shifts": risky[["product", "day_mean_mid_range", "max_abs_day_drift"]]
        .head(8)
        .to_dict("records"),
        "strongest_buyer_markout_50_players": toxic_players[
            ["player", "avg_buyer_markout_50", "buyer_win_rate_50", "buy_trades"]
        ]
        .head(8)
        .to_dict("records"),
    }
    with (out_dir / "round4_market_profile_summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    lines = [
        "# Round 4 Market Profile",
        "",
        "## Calibration Read",
        "",
        "- Use public days as separate regimes, not one pooled expected-value estimate.",
        "- Prefer Rust backtester `--calibration round4` or harsher for strategy ranking.",
        "- Treat high day-to-day mid shifts and unstable OBI correlations as hidden-day risk.",
        "",
        "## Most Active Products",
        "",
        top_products[["product", "avg_trade_count_per_day", "avg_trade_qty_per_day", "avg_spread"]]
        .head(12)
        .to_markdown(index=False),
        "",
        "## Largest Day-Mean Shifts",
        "",
        risky[["product", "day_mean_mid_range", "max_abs_day_drift", "avg_ret_lag1_autocorr"]]
        .head(12)
        .to_markdown(index=False),
        "",
        "## OBI/Next-Return Signal",
        "",
        product_metrics[["product", "avg_obi_next_ret_corr_5", "min_obi_next_ret_corr_5", "max_obi_next_ret_corr_5"]]
        .sort_values("avg_obi_next_ret_corr_5")
        .to_markdown(index=False),
        "",
        "## Strongest 50-Tick Buyer Markout Players",
        "",
        toxic_players[["player", "avg_buyer_markout_50", "buyer_win_rate_50", "buy_trades", "buy_products"]]
        .head(10)
        .to_markdown(index=False),
        "",
    ]
    (out_dir / "round4_market_profile_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("phase2/round4/algo/data"))
    parser.add_argument("--out", type=Path, default=Path("phase2/round4/algo/analysis/round4_market_profile_current"))
    args = parser.parse_args()

    prices = load_prices(args.data_dir)
    trades = load_trades(args.data_dir)
    enriched = enrich_trades(trades, prices)
    day_metrics = product_market_metrics(prices, enriched)
    product_metrics = aggregate_product_metrics(day_metrics)
    players = player_flow_metrics(enriched)
    write_report(args.out, day_metrics, product_metrics, players)
    print(f"Wrote Round 4 market profile to {args.out}")


if __name__ == "__main__":
    main()
