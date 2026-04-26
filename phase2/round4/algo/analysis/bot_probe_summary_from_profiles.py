from __future__ import annotations

"""
Build bot-level summaries for Round 4 pair-probe backtests.

This script is a companion to player_profile_from_logs.py. It reads the
enriched trade events created by that profiler, joins each run with its probe
metadata and final JSON result, then writes bot-oriented CSV views.

Usage:
python3 phase2/round4/algo/analysis/bot_probe_summary_from_profiles.py \
  --backtests phase2/round4/algo/backtests \
  --profile phase2/round4/algo/backtests/player_profile_current
"""

import argparse
import ast
import json
import math
import re
from pathlib import Path

import pandas as pd


def safe_mean(values: pd.Series) -> float:
    x = pd.to_numeric(values, errors="coerce").dropna()
    return float(x.mean()) if len(x) else float("nan")


def t_stat(values: pd.Series) -> float:
    x = pd.to_numeric(values, errors="coerce").dropna()
    if len(x) < 2:
        return float("nan")
    std = x.std(ddof=1)
    if not std or math.isnan(std):
        return float("nan")
    return float(x.mean() / (std / math.sqrt(len(x))))


def safe_join(values: pd.Series) -> str:
    return "|".join(sorted(str(x) for x in values.dropna().unique() if str(x)))


def read_probe_meta(py_path: Path) -> dict:
    text = py_path.read_text(encoding="utf-8")

    def find_str(name: str) -> str:
        match = re.search(rf'{name}\s*=\s*"([^"]+)"', text)
        return match.group(1) if match else ""

    products: list[str] = []
    match = re.search(r"TARGET_PRODUCTS\s*=\s*(\[[\s\S]*?\])", text)
    if match:
        products = ast.literal_eval(match.group(1))

    return {
        "target_buyer": find_str("TARGET_BUYER"),
        "target_seller": find_str("TARGET_SELLER"),
        "pair_label": find_str("PAIR_LABEL"),
        "target_products": "|".join(products),
    }


def read_result(json_path: Path) -> dict:
    if not json_path.exists():
        return {}
    with json_path.open(encoding="utf-8") as f:
        return json.load(f)


def format_positions(result: dict) -> str:
    return "|".join(f"{p.get('symbol')}:{p.get('quantity')}" for p in (result.get("positions") or []))


def add_submission_fields(own: pd.DataFrame) -> pd.DataFrame:
    own = own.copy()
    own["submission_side"] = own.apply(lambda r: "BUY" if r["buyer"] == "SUBMISSION" else "SELL", axis=1)
    own["counterparty"] = own.apply(lambda r: r["seller"] if r["buyer"] == "SUBMISSION" else r["buyer"], axis=1)
    own["submission_markout_1"] = own.apply(
        lambda r: r["buyer_markout_1"] if r["buyer"] == "SUBMISSION" else r["seller_markout_1"],
        axis=1,
    )
    own["submission_markout_5"] = own.apply(
        lambda r: r["buyer_markout_5"] if r["buyer"] == "SUBMISSION" else r["seller_markout_5"],
        axis=1,
    )
    own["submission_markout_10"] = own.apply(
        lambda r: r["buyer_markout_10"] if r["buyer"] == "SUBMISSION" else r["seller_markout_10"],
        axis=1,
    )
    own["submission_markout_50"] = own.apply(
        lambda r: r["buyer_markout_50"] if r["buyer"] == "SUBMISSION" else r["seller_markout_50"],
        axis=1,
    )
    own["signed_qty"] = own.apply(lambda r: r["quantity"] if r["buyer"] == "SUBMISSION" else -r["quantity"], axis=1)
    return own


def append_target_rows(rows: list[dict], base: dict, label: str, trades: pd.DataFrame) -> None:
    if trades.empty:
        rows.append({**base, "exposure": label, "fills": 0, "qty": 0})
        return
    for product, g in trades.groupby("symbol", sort=True):
        rows.append(
            {
                **base,
                "exposure": label,
                "product": product,
                "fills": int(len(g)),
                "qty": float(g["quantity"].sum()),
                "avg_qty": float(g["quantity"].mean()),
                "avg_price": float(g["price"].mean()),
                "min_price": float(g["price"].min()),
                "max_price": float(g["price"].max()),
                "avg_buyer_markout_1": safe_mean(g["buyer_markout_1"]),
                "avg_buyer_markout_5": safe_mean(g["buyer_markout_5"]),
                "avg_buyer_markout_10": safe_mean(g["buyer_markout_10"]),
                "avg_buyer_markout_50": safe_mean(g["buyer_markout_50"]),
                "avg_seller_markout_1": safe_mean(g["seller_markout_1"]),
                "avg_seller_markout_5": safe_mean(g["seller_markout_5"]),
                "avg_seller_markout_10": safe_mean(g["seller_markout_10"]),
                "avg_seller_markout_50": safe_mean(g["seller_markout_50"]),
                "first_ts": int(g["timestamp"].min()),
                "last_ts": int(g["timestamp"].max()),
            }
        )


def build_outputs(backtests_dir: Path, profile_dir: Path) -> None:
    trades = pd.read_csv(profile_dir / "trade_events_enriched.csv")

    summary_rows: list[dict] = []
    target_rows: list[dict] = []
    counterparty_rows: list[dict] = []
    product_rows: list[dict] = []
    mode_rows: list[dict] = []

    for log_path in sorted(backtests_dir.glob("probe_*_logs/*.log")):
        run_id = log_path.stem
        bot = log_path.parent.name.replace("_logs", "")
        meta = read_probe_meta(log_path.with_suffix(".py"))
        result = read_result(log_path.with_suffix(".json"))
        df = trades[trades["source_log"] == log_path.name].copy()
        if df.empty:
            continue

        own = add_submission_fields(df[(df["buyer"] == "SUBMISSION") | (df["seller"] == "SUBMISSION")])
        own_buy = own[own["submission_side"] == "BUY"]
        own_sell = own[own["submission_side"] == "SELL"]

        target_buyer = meta["target_buyer"]
        target_seller = meta["target_seller"]
        target_buyer_bought_from_us = own[(own["buyer"] == target_buyer) & (own["seller"] == "SUBMISSION")]
        target_seller_sold_to_us = own[(own["buyer"] == "SUBMISSION") & (own["seller"] == target_seller)]
        target_pair_raw = df[(df["buyer"] == target_buyer) & (df["seller"] == target_seller)]
        reverse_pair_raw = df[(df["buyer"] == target_seller) & (df["seller"] == target_buyer)]

        base = {"bot": bot, "run_id": run_id, "pair_label": meta["pair_label"]}
        summary_rows.append(
            {
                **base,
                **meta,
                "profit": result.get("profit"),
                "final_positions": format_positions(result),
                "all_trade_events": int(len(df)),
                "own_fills": int(len(own)),
                "own_qty": float(own["quantity"].sum()),
                "own_buy_fills": int(len(own_buy)),
                "own_buy_qty": float(own_buy["quantity"].sum()),
                "own_sell_fills": int(len(own_sell)),
                "own_sell_qty": float(own_sell["quantity"].sum()),
                "net_signed_qty": float(own["signed_qty"].sum()),
                "counterparties_touched": safe_join(own["counterparty"]),
                "target_buyer_bought_from_us_fills": int(len(target_buyer_bought_from_us)),
                "target_buyer_bought_from_us_qty": float(target_buyer_bought_from_us["quantity"].sum()),
                "target_seller_sold_to_us_fills": int(len(target_seller_sold_to_us)),
                "target_seller_sold_to_us_qty": float(target_seller_sold_to_us["quantity"].sum()),
                "target_pair_raw_fills": int(len(target_pair_raw)),
                "target_pair_raw_qty": float(target_pair_raw["quantity"].sum()),
                "reverse_pair_raw_fills": int(len(reverse_pair_raw)),
                "reverse_pair_raw_qty": float(reverse_pair_raw["quantity"].sum()),
                "avg_submission_markout_1": safe_mean(own["submission_markout_1"]),
                "avg_submission_markout_5": safe_mean(own["submission_markout_5"]),
                "avg_submission_markout_10": safe_mean(own["submission_markout_10"]),
                "avg_submission_markout_50": safe_mean(own["submission_markout_50"]),
                "tstat_submission_markout_10": t_stat(own["submission_markout_10"]),
                "win_rate_10": safe_mean(own["submission_markout_10"] > 0),
                "first_own_ts": int(own["timestamp"].min()),
                "last_own_ts": int(own["timestamp"].max()),
            }
        )

        append_target_rows(target_rows, base, "target_buyer_bought_from_us", target_buyer_bought_from_us)
        append_target_rows(target_rows, base, "target_seller_sold_to_us", target_seller_sold_to_us)
        append_target_rows(target_rows, base, "target_pair_raw", target_pair_raw)
        append_target_rows(target_rows, base, "reverse_pair_raw", reverse_pair_raw)

        for (counterparty, side), g in own.groupby(["counterparty", "submission_side"], sort=True):
            counterparty_rows.append(
                {
                    **base,
                    "counterparty": counterparty,
                    "submission_side": side,
                    "fills": int(len(g)),
                    "qty": float(g["quantity"].sum()),
                    "products": safe_join(g["symbol"]),
                    "avg_price": float(g["price"].mean()),
                    "avg_price_minus_mid": safe_mean(g["price_minus_mid"]),
                    "avg_price_minus_bid": safe_mean(g["price_minus_bid"]),
                    "avg_price_minus_ask": safe_mean(g["price_minus_ask"]),
                    "avg_spread": safe_mean(g["spread"]),
                    "avg_markout_1": safe_mean(g["submission_markout_1"]),
                    "avg_markout_5": safe_mean(g["submission_markout_5"]),
                    "avg_markout_10": safe_mean(g["submission_markout_10"]),
                    "avg_markout_50": safe_mean(g["submission_markout_50"]),
                    "tstat_markout_10": t_stat(g["submission_markout_10"]),
                    "win_rate_10": safe_mean(g["submission_markout_10"] > 0),
                    "first_ts": int(g["timestamp"].min()),
                    "last_ts": int(g["timestamp"].max()),
                }
            )

        for (product, side), g in own.groupby(["symbol", "submission_side"], sort=True):
            product_rows.append(
                {
                    **base,
                    "product": product,
                    "submission_side": side,
                    "fills": int(len(g)),
                    "qty": float(g["quantity"].sum()),
                    "avg_price": float(g["price"].mean()),
                    "avg_price_minus_mid": safe_mean(g["price_minus_mid"]),
                    "avg_price_minus_bid": safe_mean(g["price_minus_bid"]),
                    "avg_price_minus_ask": safe_mean(g["price_minus_ask"]),
                    "avg_spread": safe_mean(g["spread"]),
                    "avg_markout_1": safe_mean(g["submission_markout_1"]),
                    "avg_markout_5": safe_mean(g["submission_markout_5"]),
                    "avg_markout_10": safe_mean(g["submission_markout_10"]),
                    "avg_markout_50": safe_mean(g["submission_markout_50"]),
                    "tstat_markout_10": t_stat(g["submission_markout_10"]),
                    "win_rate_10": safe_mean(g["submission_markout_10"] > 0),
                }
            )

        for mode, g in own.groupby("probe_mode", sort=True):
            mode_rows.append(
                {
                    **base,
                    "mode": mode,
                    "fills": int(len(g)),
                    "qty": float(g["quantity"].sum()),
                    "net_signed_qty": float(g["signed_qty"].sum()),
                    "avg_price_minus_mid": safe_mean(g["price_minus_mid"]),
                    "avg_price_minus_bid": safe_mean(g["price_minus_bid"]),
                    "avg_price_minus_ask": safe_mean(g["price_minus_ask"]),
                    "avg_spread": safe_mean(g["spread"]),
                    "avg_markout_1": safe_mean(g["submission_markout_1"]),
                    "avg_markout_5": safe_mean(g["submission_markout_5"]),
                    "avg_markout_10": safe_mean(g["submission_markout_10"]),
                    "avg_markout_50": safe_mean(g["submission_markout_50"]),
                    "tstat_markout_10": t_stat(g["submission_markout_10"]),
                    "win_rate_10": safe_mean(g["submission_markout_10"] > 0),
                    "counterparties": safe_join(g["counterparty"]),
                }
            )

    pd.DataFrame(summary_rows).sort_values(["bot"]).to_csv(profile_dir / "bot_run_summary.csv", index=False)
    pd.DataFrame(target_rows).sort_values(["bot", "exposure", "product"]).to_csv(
        profile_dir / "bot_target_exposure.csv",
        index=False,
    )
    pd.DataFrame(counterparty_rows).sort_values(["bot", "counterparty", "submission_side"]).to_csv(
        profile_dir / "bot_counterparty_exposure.csv",
        index=False,
    )
    pd.DataFrame(product_rows).sort_values(["bot", "product", "submission_side"]).to_csv(
        profile_dir / "bot_product_own_fills.csv",
        index=False,
    )
    pd.DataFrame(mode_rows).sort_values(["bot", "mode"]).to_csv(profile_dir / "bot_mode_own_fills.csv", index=False)

    counterparty_df = pd.DataFrame(counterparty_rows)
    product_df = pd.DataFrame(product_rows)
    mode_df = pd.DataFrame(mode_rows)

    if not counterparty_df.empty:
        counterparty_summary = aggregate_weighted(
            counterparty_df,
            group_cols=["counterparty", "submission_side"],
            extra_unique_col="products",
        )
        counterparty_summary.to_csv(profile_dir / "submission_counterparty_summary.csv", index=False)

    if not product_df.empty:
        product_summary = aggregate_weighted(product_df, group_cols=["product", "submission_side"])
        product_summary.to_csv(profile_dir / "submission_product_summary.csv", index=False)

    if not mode_df.empty:
        mode_summary = aggregate_weighted(mode_df, group_cols=["mode"], include_net=True, extra_unique_col="counterparties")
        mode_summary.to_csv(profile_dir / "submission_mode_summary.csv", index=False)


def aggregate_weighted(
    df: pd.DataFrame,
    group_cols: list[str],
    include_net: bool = False,
    extra_unique_col: str | None = None,
) -> pd.DataFrame:
    rows = []
    for key, g in df.groupby(group_cols, sort=True):
        if not isinstance(key, tuple):
            key = (key,)
        fills = float(g["fills"].sum())
        row = {col: value for col, value in zip(group_cols, key)}
        row.update(
            {
                "run_count": int(g["bot"].nunique()),
                "fills": int(g["fills"].sum()),
                "qty": float(g["qty"].sum()),
                "avg_markout_1_w_by_fills": weighted_mean(g, "avg_markout_1", "fills"),
                "avg_markout_5_w_by_fills": weighted_mean(g, "avg_markout_5", "fills"),
                "avg_markout_10_w_by_fills": weighted_mean(g, "avg_markout_10", "fills"),
                "avg_markout_50_w_by_fills": weighted_mean(g, "avg_markout_50", "fills"),
                "avg_win_rate_10_w_by_fills": weighted_mean(g, "win_rate_10", "fills"),
                "avg_price_minus_mid_w_by_fills": weighted_mean(g, "avg_price_minus_mid", "fills")
                if "avg_price_minus_mid" in g
                else float("nan"),
                "avg_price_minus_bid_w_by_fills": weighted_mean(g, "avg_price_minus_bid", "fills")
                if "avg_price_minus_bid" in g
                else float("nan"),
                "avg_price_minus_ask_w_by_fills": weighted_mean(g, "avg_price_minus_ask", "fills")
                if "avg_price_minus_ask" in g
                else float("nan"),
                "avg_spread_w_by_fills": weighted_mean(g, "avg_spread", "fills") if "avg_spread" in g else float("nan"),
            }
        )
        if include_net and "net_signed_qty" in g:
            row["net_signed_qty"] = float(g["net_signed_qty"].sum())
        if extra_unique_col and extra_unique_col in g:
            pieces = set()
            for value in g[extra_unique_col].dropna().astype(str):
                pieces.update(x for x in value.split("|") if x)
            row[extra_unique_col] = "|".join(sorted(pieces))
        if fills > 0:
            rows.append(row)
    return pd.DataFrame(rows)


def weighted_mean(df: pd.DataFrame, value_col: str, weight_col: str) -> float:
    values = pd.to_numeric(df[value_col], errors="coerce")
    weights = pd.to_numeric(df[weight_col], errors="coerce")
    mask = values.notna() & weights.notna() & (weights > 0)
    if not mask.any():
        return float("nan")
    return float((values[mask] * weights[mask]).sum() / weights[mask].sum())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backtests", required=True, help="Directory containing probe_pair_*_logs folders.")
    parser.add_argument("--profile", required=True, help="Directory containing trade_events_enriched.csv.")
    args = parser.parse_args()
    build_outputs(Path(args.backtests), Path(args.profile))
    print(f"Saved bot-level probe summaries to {args.profile}")


if __name__ == "__main__":
    main()
