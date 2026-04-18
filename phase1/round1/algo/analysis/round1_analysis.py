from __future__ import annotations

import argparse
import io
import json
import math
import re
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from phase1.round1.algo.analysis.svg_plots import bar_chart, ecdf_chart, heatmap_chart, histogram_chart, line_chart


PRODUCTS = ["ASH_COATED_OSMIUM", "INTARIAN_PEPPER_ROOT"]
POSITION_LIMITS = {"ASH_COATED_OSMIUM": 80, "INTARIAN_PEPPER_ROOT": 80}
MANUAL_PRODUCTS = {"DRYLAND_FLAX", "EMBER_MUSHROOM"}
TIME_STEP = 100
ROLLING_VOL_WINDOW = 50


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        val = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(val) or math.isinf(val):
        return None
    return val


def safe_int(value: Any) -> int | None:
    val = to_float(value)
    if val is None:
        return None
    return int(round(val))


def slugify(name: str) -> str:
    return name.lower().replace(" ", "_")


def safe_qcut(series: pd.Series, labels: list[str]) -> pd.Series:
    clean = series.dropna()
    if clean.empty:
        return pd.Series(index=series.index, dtype=object)
    base = series
    for candidate in (series, series.rank(method="average")):
        try:
            buckets = pd.qcut(candidate, q=min(len(labels), max(1, clean.nunique())), duplicates="drop")
            cat = buckets.cat
            rename = {interval: labels[idx] for idx, interval in enumerate(cat.categories[: len(labels)])}
            return buckets.map(rename)
        except ValueError:
            base = candidate
            continue
    uniq = min(len(labels), max(1, clean.nunique()))
    ranked = base.rank(method="average")
    scaled = np.floor((ranked - 1) / max(len(ranked.dropna()) / uniq, 1)).clip(0, uniq - 1)
    out = pd.Series(index=series.index, dtype=object)
    mask = ranked.notna()
    out.loc[mask] = [labels[int(i)] for i in scaled.loc[mask]]
    return out


def load_round_csvs(root: Path, prefix: str, recursive: bool = False) -> pd.DataFrame:
    pattern = f"**/{prefix}_round_1_day_*.csv" if recursive else f"{prefix}_round_1_day_*.csv"
    paths = sorted(root.glob(pattern))
    frames = []
    for path in paths:
        df = pd.read_csv(path, sep=";")
        df["source_file"] = path.name
        frames.append(df)
    if not frames:
        raise FileNotFoundError(f"No files matching {pattern} in {root}")
    out = pd.concat(frames, ignore_index=True)
    numeric_cols = [col for col in out.columns if col not in {"product", "symbol", "buyer", "seller", "currency", "source_file"}]
    for col in numeric_cols:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    if "day" not in out.columns:
        out["day"] = out["source_file"].str.extract(r"day_(-?\d+)").astype(float)
    out["day"] = out["day"].astype(int)
    return out


def preprocess_prices(prices: pd.DataFrame) -> pd.DataFrame:
    prices = prices.copy()
    prices = prices[prices["product"].isin(PRODUCTS)].sort_values(["product", "day", "timestamp"]).reset_index(drop=True)
    for side in ["bid", "ask"]:
        for level in [1, 2, 3]:
            p_col = f"{side}_price_{level}"
            v_col = f"{side}_volume_{level}"
            if p_col in prices:
                prices[p_col] = pd.to_numeric(prices[p_col], errors="coerce")
            if v_col in prices:
                prices[v_col] = pd.to_numeric(prices[v_col], errors="coerce")
    prices["mid_price"] = pd.to_numeric(prices["mid_price"], errors="coerce")
    prices["one_sided"] = prices["bid_price_1"].isna() | prices["ask_price_1"].isna()
    prices["spread"] = prices["ask_price_1"] - prices["bid_price_1"]
    bid_1 = prices["bid_volume_1"].abs().fillna(0.0)
    ask_1 = prices["ask_volume_1"].abs().fillna(0.0)
    denom = bid_1 + ask_1
    prices["imbalance_l1"] = np.where(denom > 0, (bid_1 - ask_1) / denom, np.nan)
    prices["microprice"] = np.where(
        denom > 0,
        (prices["ask_price_1"] * bid_1 + prices["bid_price_1"] * ask_1) / denom,
        prices["mid_price"],
    )
    prices["fair_value"] = prices["microprice"].fillna(prices["mid_price"]).fillna(prices["bid_price_1"]).fillna(prices["ask_price_1"])
    prices["d_fair"] = prices.groupby(["product", "day"])["fair_value"].diff()
    prices["d_mid"] = prices.groupby(["product", "day"])["mid_price"].diff()
    prices["rolling_vol"] = (
        prices.groupby(["product", "day"])["d_fair"].transform(lambda x: x.rolling(ROLLING_VOL_WINDOW, min_periods=10).std())
    )
    prices["cum_mean_d_fair"] = prices.groupby(["product", "day"])["d_fair"].transform(lambda x: x.expanding().mean())
    prices["cum_var_d_fair"] = prices.groupby(["product", "day"])["d_fair"].transform(lambda x: x.expanding().var())
    for level in [1, 2, 3]:
        prices[f"bid_offset_fair_{level}"] = prices[f"bid_price_{level}"] - prices["fair_value"]
        prices[f"ask_offset_fair_{level}"] = prices[f"ask_price_{level}"] - prices["fair_value"]
        prices[f"bid_offset_mid_{level}"] = prices[f"bid_price_{level}"] - prices["mid_price"]
        prices[f"ask_offset_mid_{level}"] = prices[f"ask_price_{level}"] - prices["mid_price"]
        prices[f"level_present_bid_{level}"] = prices[f"bid_price_{level}"].notna().astype(int)
        prices[f"level_present_ask_{level}"] = prices[f"ask_price_{level}"].notna().astype(int)
    prices["vol_bucket"] = safe_qcut(prices["rolling_vol"].fillna(prices["rolling_vol"].median()), ["q1", "q2", "q3", "q4"])
    prices["spread_bucket"] = safe_qcut(prices["spread"].fillna(prices["spread"].median()), ["tight", "mid1", "mid2", "wide"])
    prices["fair_state_bucket"] = safe_qcut(prices["fair_value"], ["low", "mid_low", "mid_high", "high"])
    return prices


def merge_trades_to_prices(trades: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    trades = trades.copy()
    trades = trades.rename(columns={"symbol": "product"})
    trades = trades[trades["product"].isin(PRODUCTS)].sort_values(["product", "day", "timestamp"]).reset_index(drop=True)
    price_cols = [
        "day",
        "product",
        "timestamp",
        "fair_value",
        "mid_price",
        "spread",
        "imbalance_l1",
        "rolling_vol",
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
    ]
    merged = []
    for (product, day), t_df in trades.groupby(["product", "day"], sort=True):
        p_df = prices[(prices["product"] == product) & (prices["day"] == day)][price_cols].sort_values("timestamp")
        if p_df.empty:
            block = t_df.copy()
        else:
            block = pd.merge_asof(t_df.sort_values("timestamp"), p_df.sort_values("timestamp"), on="timestamp", direction="backward")
        block["product"] = product
        block["day"] = day
        merged.append(block)
    out = pd.concat(merged, ignore_index=True) if merged else trades.copy()
    out["trade_side"] = np.where(out["buyer"].fillna("") == "SUBMISSION", "buy", np.where(out["seller"].fillna("") == "SUBMISSION", "sell", "unknown"))
    out["signed_quantity"] = np.where(out["trade_side"] == "buy", out["quantity"], np.where(out["trade_side"] == "sell", -out["quantity"], np.nan))
    out["price_minus_fair"] = out["price"] - out["fair_value"]
    out["price_minus_mid"] = out["price"] - out["mid_price"]
    out["price_minus_bid1"] = out["price"] - out["bid_price_1"]
    out["price_minus_ask1"] = out["price"] - out["ask_price_1"]
    out["print_location"] = np.where(
        (out["price"] == out["bid_price_1"]) | (out["price"] == out["ask_price_1"]),
        "touch",
        np.where(
            (out["price"] > out["bid_price_1"]) & (out["price"] < out["ask_price_1"]),
            "inside",
            np.where(
                (out["price"] < out["bid_price_1"]) | (out["price"] > out["ask_price_1"]),
                "through_or_outside",
                "unknown",
            ),
        ),
    )
    out["aggressor_guess"] = np.where(
        out["price"] >= out["ask_price_1"],
        "buy_aggr",
        np.where(out["price"] <= out["bid_price_1"], "sell_aggr", np.where(out["price"] > out["mid_price"], "buy_aggr", np.where(out["price"] < out["mid_price"], "sell_aggr", "mid"))),
    )
    out["sweep_depth_visible"] = 1
    out.loc[out["aggressor_guess"] == "buy_aggr", "sweep_depth_visible"] = (
        (out["price"] >= out["ask_price_1"]).astype(int)
        + (out["price"] >= out["ask_price_2"]).fillna(False).astype(int)
        + (out["price"] >= out["ask_price_3"]).fillna(False).astype(int)
    )
    out.loc[out["aggressor_guess"] == "sell_aggr", "sweep_depth_visible"] = (
        (out["price"] <= out["bid_price_1"]).astype(int)
        + (out["price"] <= out["bid_price_2"]).fillna(False).astype(int)
        + (out["price"] <= out["bid_price_3"]).fillna(False).astype(int)
    )
    return out


def autocorr(values: pd.Series, lag: int) -> float | None:
    series = values.dropna()
    if len(series) <= lag + 3:
        return None
    return to_float(series.autocorr(lag=lag))


def fair_value_summary(product_prices: pd.DataFrame) -> tuple[dict[str, Any], dict[str, list[float]]]:
    fair = product_prices["fair_value"]
    d_fair = product_prices["d_fair"].dropna()
    returns = d_fair.to_numpy()
    fair_var = to_float(fair.var())
    d_var = to_float(d_fair.var())
    acf_1 = autocorr(d_fair, 1)
    level_acf_1 = autocorr(fair, 1)
    mr_corr = to_float(pd.concat([d_fair, product_prices["fair_value"].shift(1)], axis=1).dropna().corr().iloc[0, 1])
    jump_rate = float(np.mean(np.abs(returns) >= np.nanpercentile(np.abs(returns), 95))) if len(returns) else 0.0
    block_means = product_prices.groupby(["day", pd.cut(product_prices["timestamp"], bins=6, labels=False)])["fair_value"].mean().dropna()
    regime_span = to_float(block_means.max() - block_means.min()) if not block_means.empty else 0.0
    rolling_mean = product_prices.groupby("day")["fair_value"].transform(lambda x: x.rolling(100, min_periods=20).mean())
    rolling_mean_range = to_float((rolling_mean.groupby(product_prices["day"]).max() - rolling_mean.groupby(product_prices["day"]).min()).mean())
    stationary = bool((fair_var is not None and d_var is not None and fair_var < 3000 and d_var < 12 and abs(level_acf_1 or 0) < 0.999) and (rolling_mean_range or 0) < 50)
    drifting = bool(abs(to_float(product_prices.groupby("day")["fair_value"].apply(lambda x: x.iloc[-1] - x.iloc[0]).mean()) or 0) > 50)
    mean_reverting = bool((mr_corr or 0) < -0.05 and abs(acf_1 or 0) < 0.4)
    jumpy = bool((jump_rate or 0) > 0.03)
    regime_switching = bool((regime_span or 0) > 100 or (rolling_mean_range or 0) > 80)
    text = {
        "stationary": stationary,
        "drifting": drifting,
        "mean_reverting": mean_reverting,
        "jumpy": jumpy,
        "regime_switching": regime_switching,
        "acf_lag1": acf_1,
        "level_acf_lag1": level_acf_1,
        "mean_reversion_corr": mr_corr,
        "jump_rate_95": jump_rate,
        "regime_span": regime_span,
        "rolling_mean_range": rolling_mean_range,
        "daily_drift_mean": to_float(product_prices.groupby("day")["fair_value"].apply(lambda x: x.iloc[-1] - x.iloc[0]).mean()),
        "rolling_vol_mean": to_float(product_prices["rolling_vol"].mean()),
    }
    series = {
        "fair_value": fair.tolist(),
        "d_fair": d_fair.tolist(),
        "rolling_vol": product_prices["rolling_vol"].dropna().tolist(),
        "cum_mean_d_fair": product_prices["cum_mean_d_fair"].dropna().tolist(),
        "cum_var_d_fair": product_prices["cum_var_d_fair"].dropna().tolist(),
        "acf": [autocorr(d_fair, lag) or 0.0 for lag in range(1, 21)],
    }
    return text, series


def quote_structure_summary(product_prices: pd.DataFrame) -> tuple[dict[str, Any], dict[str, Any]]:
    presence = {}
    level_size = {}
    offset_heat = []
    y_labels = []
    for side in ["bid", "ask"]:
        for level in [1, 2, 3]:
            y_label = f"{side}_{level}"
            y_labels.append(y_label)
            presence[y_label] = float(product_prices[f"level_present_{side}_{level}"].mean())
            level_size[y_label] = to_float(product_prices[f"{side}_volume_{level}"].abs().dropna().median())
            offsets = product_prices[f"{side}_offset_fair_{level}"].dropna()
            hist = pd.cut(offsets, bins=[-40, -20, -10, -5, 0, 5, 10, 20, 40], labels=False, include_lowest=True)
            freq = hist.value_counts(normalize=True).sort_index()
            offset_heat.append([float(freq.get(idx, 0.0)) for idx in range(8)])
    spread_dist = product_prices["spread"].dropna().value_counts(normalize=True).sort_index()
    imbalance_dist = pd.cut(product_prices["imbalance_l1"], bins=[-1.01, -0.5, -0.2, 0.2, 0.5, 1.01], labels=["heavy_ask", "ask", "flat", "bid", "heavy_bid"]).value_counts(normalize=True)
    summary = {
        "one_sided_rate": float(product_prices["one_sided"].mean()),
        "spread_mean": to_float(product_prices["spread"].mean()),
        "spread_mode": safe_int(product_prices["spread"].mode().iloc[0] if not product_prices["spread"].mode().empty else None),
        "inner_wall_rate": float(((product_prices["bid_volume_1"].abs() >= product_prices["bid_volume_2"].abs().fillna(0)) & (product_prices["ask_volume_1"].abs() >= product_prices["ask_volume_2"].abs().fillna(0))).mean()),
        "level_presence": presence,
        "level_size_median": level_size,
        "imbalance_distribution": {str(k): float(v) for k, v in imbalance_dist.sort_index().items()},
        "quote_offsets_median": {col: to_float(product_prices[col].median()) for col in [f"bid_offset_fair_{i}" for i in [1, 2, 3]] + [f"ask_offset_fair_{i}" for i in [1, 2, 3]]},
        "spread_histogram": {str(int(k)): float(v) for k, v in spread_dist.items() if pd.notna(k)},
    }
    plot_data = {
        "heatmap": offset_heat,
        "heatmap_x": ["<=-20", "-20:-10", "-10:-5", "-5:0", "0:5", "5:10", "10:20", ">=20"],
        "heatmap_y": y_labels,
        "spread_hist_labels": [str(int(k)) for k in spread_dist.index if pd.notna(k)],
        "spread_hist_values": [float(v) for k, v in spread_dist.items() if pd.notna(k)],
    }
    return summary, plot_data


def arrival_summary(product_trades: pd.DataFrame, product_prices: pd.DataFrame) -> tuple[dict[str, Any], dict[str, Any]]:
    timestamps = product_trades["timestamp"].dropna().astype(int).sort_values()
    active_counts = timestamps.value_counts().sort_index()
    gaps = np.diff(active_counts.index.to_numpy()) if len(active_counts) > 1 else np.array([])
    gap_positive = gaps[gaps > 0]
    arrivals_per_step = product_trades.groupby("timestamp").size()
    day_range = product_prices.groupby("day")["timestamp"].agg(["min", "max"])
    time_of_day = []
    for day, row in day_range.iterrows():
        mask = product_trades["day"] == day
        denom = max(row["max"] - row["min"], 1)
        time_of_day.extend(((product_trades.loc[mask, "timestamp"] - row["min"]) / denom).dropna().tolist())
    spread_arrivals = product_trades.groupby(pd.cut(product_trades["spread"], bins=[-np.inf, 10, 14, 18, np.inf], labels=["<=10", "11-14", "15-18", "19+"]))["timestamp"].count()
    summary = {
        "trades_per_active_step_mean": to_float(arrivals_per_step.mean()),
        "active_timestamp_rate": float(len(active_counts) / max(product_prices["timestamp"].nunique() * product_prices["day"].nunique(), 1)),
        "mean_gap": to_float(np.mean(gap_positive)) if len(gap_positive) else None,
        "gap_cv": to_float(np.std(gap_positive) / np.mean(gap_positive)) if len(gap_positive) and np.mean(gap_positive) else None,
        "poisson_like": bool(to_float(np.std(gap_positive) / np.mean(gap_positive)) is not None and abs((np.std(gap_positive) / np.mean(gap_positive)) - 1.0) < 0.25) if len(gap_positive) else False,
        "time_of_day_trade_mean": to_float(np.mean(time_of_day)) if time_of_day else None,
        "spread_state_dependence": {str(k): int(v) for k, v in spread_arrivals.items()},
    }
    plot_data = {
        "arrivals_per_step": arrivals_per_step.tolist(),
        "gaps": gap_positive.tolist(),
        "time_of_day": time_of_day,
        "buy_sell_balance": product_trades["aggressor_guess"].value_counts().to_dict(),
    }
    return summary, plot_data


def trade_size_summary(product_trades: pd.DataFrame) -> tuple[dict[str, Any], dict[str, Any]]:
    buy = product_trades.loc[product_trades["aggressor_guess"] == "buy_aggr", "quantity"].dropna()
    sell = product_trades.loc[product_trades["aggressor_guess"] == "sell_aggr", "quantity"].dropna()
    all_q = product_trades["quantity"].dropna()
    summary = {
        "size_mean": to_float(all_q.mean()),
        "size_median": to_float(all_q.median()),
        "buy_size_median": to_float(buy.median()),
        "sell_size_median": to_float(sell.median()),
        "discrete_size_top": {str(int(k)): int(v) for k, v in all_q.value_counts().head(8).items()},
    }
    plot_data = {"buy_sizes": buy.tolist(), "sell_sizes": sell.tolist(), "all_sizes": all_q.tolist()}
    return summary, plot_data


def trade_price_summary(product_trades: pd.DataFrame) -> tuple[dict[str, Any], dict[str, Any]]:
    loc = product_trades["print_location"].value_counts(normalize=True)
    sweep_rate = float((product_trades["sweep_depth_visible"] > 1).mean()) if "sweep_depth_visible" in product_trades else 0.0
    summary = {
        "price_minus_fair_mean": to_float(product_trades["price_minus_fair"].mean()),
        "price_minus_mid_mean": to_float(product_trades["price_minus_mid"].mean()),
        "print_location": {str(k): float(v) for k, v in loc.items()},
        "sweep_rate_visible": sweep_rate,
        "touch_rate": float(loc.get("touch", 0.0)),
    }
    plot_data = {
        "price_minus_fair": product_trades["price_minus_fair"].dropna().tolist(),
        "price_minus_mid": product_trades["price_minus_mid"].dropna().tolist(),
        "sweep_depth": product_trades["sweep_depth_visible"].dropna().tolist(),
    }
    return summary, plot_data


def infer_product_story(product: str, fair: dict[str, Any], quote: dict[str, Any], arrival: dict[str, Any], sizes: dict[str, Any], price: dict[str, Any]) -> dict[str, Any]:
    archetypes = []
    if quote["spread_mode"] is not None and quote["spread_mode"] <= 16:
        archetypes.append("tight top-of-book market maker")
    if quote["inner_wall_rate"] and quote["inner_wall_rate"] > 0.5:
        archetypes.append("front-loaded top-level wall keeper")
    if fair["drifting"]:
        archetypes.append("directional repricer / trend follower")
    if arrival["gap_cv"] is not None and arrival["gap_cv"] > 1.2:
        archetypes.append("clustered taker flow")
    if price["sweep_rate_visible"] > 0.08:
        archetypes.append("occasional sweeping flow")
    if not archetypes:
        archetypes.append("stable symmetric market maker")
    fair_type = "drifting with regimes" if fair["drifting"] or fair["regime_switching"] else "approximately stationary"
    return {
        "fair_process": fair_type,
        "latent_archetypes": archetypes,
        "quote_process": "quotes concentrate around stable offsets from fair and rounded grid prices",
        "trade_arrival_process": "state-dependent clustered arrivals" if not arrival["poisson_like"] else "roughly Poisson-like sparse arrivals",
        "trade_size_process": "discrete sizes concentrated on a few values" if len(sizes["discrete_size_top"]) <= 10 else "diffuse trade sizes",
        "trade_price_process": "prints mostly at touch with occasional inside/outside executions",
        "maker_fill_process": "fill chance depends on quote distance, spread, and queue ahead approximation",
        "taker_fill_process": "immediate fills limited by visible depth with sweep depth approximated from top three levels",
        "simplest_explanation": f"{product} looks like a market driven by {', '.join(archetypes[:3])}.",
    }


def fix_jsonish(text: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", text.strip())


def split_pretty_json_objects(text: str) -> list[str]:
    objects = []
    start = None
    level = 0
    in_str = False
    escaped = False
    for idx, char in enumerate(text):
        if in_str:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_str = False
            continue
        if char == '"':
            in_str = True
        elif char == "{":
            if level == 0:
                start = idx
            level += 1
        elif char == "}":
            level -= 1
            if level == 0 and start is not None:
                objects.append(text[start : idx + 1])
    return objects


@dataclass
class ProbeBundle:
    name: str
    activities: pd.DataFrame
    fills: pd.DataFrame
    orders: pd.DataFrame


def parse_probe_log(path: Path) -> ProbeBundle | None:
    text = path.read_text()
    if "Activities log:\n" not in text or "Trade History:\n" not in text:
        return None
    sandbox_section = text.split("Activities log:\n", 1)[0].split("Sandbox logs:\n", 1)[1]
    activities_section = text.split("Activities log:\n", 1)[1].split("Trade History:\n", 1)[0].strip()
    trade_section = text.split("Trade History:\n", 1)[1]

    activities = pd.read_csv(io.StringIO(activities_section), sep=";")
    activities = preprocess_prices(activities.rename(columns={"product": "product"}))
    fills = pd.DataFrame(json.loads(fix_jsonish(trade_section)))
    if fills.empty:
        fills = pd.DataFrame(columns=["timestamp", "buyer", "seller", "symbol", "currency", "price", "quantity"])
    fills["timestamp"] = pd.to_numeric(fills["timestamp"], errors="coerce")
    fills["price"] = pd.to_numeric(fills["price"], errors="coerce")
    fills["quantity"] = pd.to_numeric(fills["quantity"], errors="coerce")
    fills = fills.rename(columns={"symbol": "product"})
    fills = fills[fills["product"].isin(PRODUCTS)].copy()

    order_rows = []
    for raw in split_pretty_json_objects(fix_jsonish(sandbox_section)):
        obj = json.loads(raw)
        lambda_log = obj.get("lambdaLog", "")
        if not lambda_log:
            continue
        payload = json.loads(lambda_log)
        timestamp = safe_int(obj.get("timestamp"))
        for symbol, price, quantity in payload[1]:
            if symbol not in PRODUCTS:
                continue
            order_rows.append(
                {
                    "timestamp": timestamp,
                    "product": symbol,
                    "price": float(price),
                    "quantity": float(quantity),
                    "side": "buy" if quantity > 0 else "sell",
                    "abs_quantity": abs(float(quantity)),
                }
            )
    orders = pd.DataFrame(order_rows)
    if orders.empty:
        orders = pd.DataFrame(columns=["timestamp", "product", "price", "quantity", "side", "abs_quantity"])
    market_cols = [
        "day",
        "timestamp",
        "product",
        "fair_value",
        "mid_price",
        "spread",
        "imbalance_l1",
        "bid_price_1",
        "ask_price_1",
        "bid_volume_1",
        "ask_volume_1",
        "bid_price_2",
        "bid_volume_2",
        "ask_price_2",
        "ask_volume_2",
        "bid_price_3",
        "bid_volume_3",
        "ask_price_3",
        "ask_volume_3",
    ]
    if not orders.empty:
        merged_orders = []
        for product, o_df in orders.groupby("product", sort=True):
            p_df = activities[activities["product"] == product][market_cols].sort_values("timestamp")
            block = pd.merge_asof(o_df.sort_values("timestamp"), p_df.sort_values("timestamp"), on="timestamp", by="product", direction="backward")
            merged_orders.append(block)
        orders = pd.concat(merged_orders, ignore_index=True)
        orders["quote_distance_fair"] = np.where(orders["side"] == "buy", orders["fair_value"] - orders["price"], orders["price"] - orders["fair_value"])
        orders["quote_distance_mid"] = np.where(orders["side"] == "buy", orders["mid_price"] - orders["price"], orders["price"] - orders["mid_price"])
        orders["is_taker"] = np.where(
            (orders["side"] == "buy") & orders["ask_price_1"].notna() & (orders["price"] >= orders["ask_price_1"]),
            True,
            np.where((orders["side"] == "sell") & orders["bid_price_1"].notna() & (orders["price"] <= orders["bid_price_1"]), True, False),
        )
        orders["at_best"] = np.where((orders["side"] == "buy") & (orders["price"] == orders["bid_price_1"]), True, np.where((orders["side"] == "sell") & (orders["price"] == orders["ask_price_1"]), True, False))
    return ProbeBundle(name=path.stem, activities=activities, fills=fills, orders=orders)


def infer_fill_features(bundle: ProbeBundle) -> dict[str, pd.DataFrame]:
    orders = bundle.orders.copy()
    fills = bundle.fills.copy()
    activities = bundle.activities.copy()
    if orders.empty or fills.empty:
        return {
            "passive_posts": pd.DataFrame(),
            "passive_fills": pd.DataFrame(),
            "taker_fills": pd.DataFrame(),
        }

    fills["side"] = np.where(fills["buyer"].fillna("") == "SUBMISSION", "buy", np.where(fills["seller"].fillna("") == "SUBMISSION", "sell", "other"))
    fills = fills[fills["side"].isin(["buy", "sell"])].copy()
    market_cols = [
        "day",
        "timestamp",
        "product",
        "fair_value",
        "mid_price",
        "spread",
        "imbalance_l1",
        "bid_price_1",
        "ask_price_1",
        "bid_volume_1",
        "ask_volume_1",
        "bid_price_2",
        "bid_volume_2",
        "ask_price_2",
        "ask_volume_2",
        "bid_price_3",
        "bid_volume_3",
        "ask_price_3",
        "ask_volume_3",
    ]
    merged_fills = []
    for product, f_df in fills.groupby("product", sort=True):
        p_df = activities[activities["product"] == product][market_cols].sort_values("timestamp")
        block = pd.merge_asof(f_df.sort_values("timestamp"), p_df.sort_values("timestamp"), on="timestamp", by="product", direction="backward")
        merged_fills.append(block)
    fills = pd.concat(merged_fills, ignore_index=True)
    fills["quote_distance_fair"] = np.where(fills["side"] == "buy", fills["fair_value"] - fills["price"], fills["price"] - fills["fair_value"])

    fills["is_taker"] = np.where(
        (fills["side"] == "buy") & fills["ask_price_1"].notna() & (fills["price"] >= fills["ask_price_1"]),
        True,
        np.where((fills["side"] == "sell") & fills["bid_price_1"].notna() & (fills["price"] <= fills["bid_price_1"]), True, False),
    )
    taker_fills = fills[fills["is_taker"]].copy()
    taker_fills["visible_depth"] = np.where(
        taker_fills["side"] == "buy",
        np.where(taker_fills["price"] >= taker_fills["ask_price_1"], taker_fills["ask_volume_1"].abs().fillna(0), 0)
        + np.where(taker_fills["price"] >= taker_fills["ask_price_2"], taker_fills["ask_volume_2"].abs().fillna(0), 0)
        + np.where(taker_fills["price"] >= taker_fills["ask_price_3"], taker_fills["ask_volume_3"].abs().fillna(0), 0),
        np.where(taker_fills["price"] <= taker_fills["bid_price_1"], taker_fills["bid_volume_1"].abs().fillna(0), 0)
        + np.where(taker_fills["price"] <= taker_fills["bid_price_2"], taker_fills["bid_volume_2"].abs().fillna(0), 0)
        + np.where(taker_fills["price"] <= taker_fills["bid_price_3"], taker_fills["bid_volume_3"].abs().fillna(0), 0),
    )
    taker_fills["slippage_vs_touch"] = np.where(
        taker_fills["side"] == "buy",
        taker_fills["price"] - taker_fills["ask_price_1"],
        taker_fills["bid_price_1"] - taker_fills["price"],
    )

    passive_orders = orders[~orders["is_taker"]].copy()
    passive_posts = []
    passive_fills = []
    open_lots: dict[tuple[str, str, float], deque] = defaultdict(deque)
    timestamp_groups = sorted(set(passive_orders["timestamp"].dropna().astype(int).tolist()) | set(fills.loc[~fills["is_taker"], "timestamp"].dropna().astype(int).tolist()))
    orders_by_ts = {ts: df.copy() for ts, df in passive_orders.groupby(passive_orders["timestamp"].astype(int))}
    maker_fills_by_ts = {ts: df.copy() for ts, df in fills.loc[~fills["is_taker"]].groupby(fills.loc[~fills["is_taker"], "timestamp"].astype(int))}

    for ts in timestamp_groups:
        ts_orders = orders_by_ts.get(ts)
        if ts_orders is not None:
            desired = {(row.product, row.side, float(row.price)): row for row in ts_orders.itertuples(index=False)}
            existing_keys = list(open_lots.keys())
            for key in existing_keys:
                if key not in desired:
                    open_lots.pop(key, None)
            for key, row in desired.items():
                current_qty = sum(lot["remaining_qty"] for lot in open_lots.get(key, []))
                desired_qty = float(row.abs_quantity)
                if desired_qty > current_qty + 1e-9:
                    delta = desired_qty - current_qty
                    lot = {
                        "timestamp": int(row.timestamp),
                        "product": row.product,
                        "side": row.side,
                        "price": float(row.price),
                        "posted_qty": delta,
                        "remaining_qty": delta,
                        "quote_distance_fair": to_float(row.quote_distance_fair),
                        "spread": to_float(row.spread),
                        "imbalance_l1": to_float(row.imbalance_l1),
                        "order_size": delta,
                        "at_best": bool(row.at_best),
                    }
                    open_lots[key].append(lot)
                    passive_posts.append({**lot, "eventual_fill": 0, "filled_qty": 0.0, "fill_delay": None})
                elif desired_qty + 1e-9 < current_qty:
                    trim = current_qty - desired_qty
                    while trim > 1e-9 and open_lots.get(key):
                        lot = open_lots[key].pop()
                        if lot["remaining_qty"] > trim + 1e-9:
                            lot["remaining_qty"] -= trim
                            open_lots[key].append(lot)
                            trim = 0.0
                        else:
                            trim -= lot["remaining_qty"]

        ts_fills = maker_fills_by_ts.get(ts)
        if ts_fills is not None:
            for fill in ts_fills.itertuples(index=False):
                key = (fill.product, fill.side, float(fill.price))
                remaining = float(fill.quantity)
                while remaining > 1e-9 and open_lots.get(key):
                    lot = open_lots[key][0]
                    alloc = min(remaining, lot["remaining_qty"])
                    lot["remaining_qty"] -= alloc
                    remaining -= alloc
                    passive_fills.append(
                        {
                            "timestamp": int(fill.timestamp),
                            "product": fill.product,
                            "side": fill.side,
                            "price": float(fill.price),
                            "fill_qty": alloc,
                            "post_timestamp": lot["timestamp"],
                            "fill_delay": int(fill.timestamp) - int(lot["timestamp"]),
                            "quote_distance_fair": lot["quote_distance_fair"],
                            "spread": lot["spread"],
                            "imbalance_l1": lot["imbalance_l1"],
                            "order_size": lot["order_size"],
                            "at_best": lot["at_best"],
                            "markout_1": None,
                            "markout_5": None,
                        }
                    )
                    if lot["remaining_qty"] <= 1e-9:
                        open_lots[key].popleft()
                if open_lots.get(key) and not open_lots[key]:
                    open_lots.pop(key, None)

    if passive_posts:
        posts_df = pd.DataFrame(passive_posts)
        fills_df = pd.DataFrame(passive_fills)
        if not fills_df.empty:
            fill_agg = fills_df.groupby(["post_timestamp", "product", "side", "price"], as_index=False)["fill_qty"].sum()
            posts_df = posts_df.merge(fill_agg, how="left", left_on=["timestamp", "product", "side", "price"], right_on=["post_timestamp", "product", "side", "price"])
            posts_df["fill_qty"] = posts_df["fill_qty"].fillna(0.0)
            posts_df["eventual_fill"] = (posts_df["fill_qty"] > 0).astype(int)
            posts_df["fill_share"] = np.where(posts_df["posted_qty"] > 0, posts_df["fill_qty"] / posts_df["posted_qty"], np.nan)
        else:
            posts_df["fill_qty"] = 0.0
            posts_df["fill_share"] = 0.0
            posts_df["eventual_fill"] = 0
    else:
        posts_df = pd.DataFrame()
        fills_df = pd.DataFrame()

    if not fills_df.empty:
        fair_lookup = activities[["product", "day", "timestamp", "fair_value"]].copy()
        fair_lookup["fair_t_plus_1"] = fair_lookup.groupby(["product", "day"])["fair_value"].shift(-1)
        fair_lookup["fair_t_plus_5"] = fair_lookup.groupby(["product", "day"])["fair_value"].shift(-5)
        fills_df = fills_df.merge(fair_lookup, how="left", on=["product", "timestamp"])
        fills_df["markout_1"] = np.where(
            fills_df["side"] == "buy",
            fills_df["fair_t_plus_1"] - fills_df["price"],
            fills_df["price"] - fills_df["fair_t_plus_1"],
        )
        fills_df["markout_5"] = np.where(
            fills_df["side"] == "buy",
            fills_df["fair_t_plus_5"] - fills_df["price"],
            fills_df["price"] - fills_df["fair_t_plus_5"],
        )

    return {"passive_posts": posts_df, "passive_fills": fills_df, "taker_fills": taker_fills}


def fill_summary(fill_data: dict[str, pd.DataFrame], product: str) -> tuple[dict[str, Any], dict[str, Any]]:
    posts = fill_data["passive_posts"]
    maker = fill_data["passive_fills"]
    taker = fill_data["taker_fills"]
    posts = posts[posts["product"] == product].copy() if not posts.empty else posts
    maker = maker[maker["product"] == product].copy() if not maker.empty else maker
    taker = taker[taker["product"] == product].copy() if not taker.empty else taker
    if posts.empty and taker.empty:
        return {"available": False}, {}

    quote_bucket = pd.cut(posts["quote_distance_fair"], bins=[-np.inf, -5, 0, 5, 10, 20, np.inf], labels=["improved", "inside_0_5", "0_5", "5_10", "10_20", "20+"]) if not posts.empty else pd.Series(dtype=object)
    spread_bucket = pd.cut(posts["spread"], bins=[-np.inf, 10, 14, 18, np.inf], labels=["<=10", "11-14", "15-18", "19+"]) if not posts.empty else pd.Series(dtype=object)
    size_bucket = pd.cut(posts["posted_qty"], bins=[0, 5, 10, 20, 40, np.inf], labels=["1-5", "6-10", "11-20", "21-40", "40+"]) if not posts.empty else pd.Series(dtype=object)
    distance_fill = posts.groupby(quote_bucket)["eventual_fill"].mean().dropna() if not posts.empty else pd.Series(dtype=float)
    spread_fill = posts.groupby(spread_bucket)["eventual_fill"].mean().dropna() if not posts.empty else pd.Series(dtype=float)
    size_fill = posts.groupby(size_bucket)["eventual_fill"].mean().dropna() if not posts.empty else pd.Series(dtype=float)

    summary = {
        "available": True,
        "passive_fill_rate": to_float(posts["eventual_fill"].mean()) if not posts.empty else None,
        "passive_fill_share_mean": to_float(posts["fill_share"].mean()) if not posts.empty else None,
        "best_quote_fill_rate": to_float(posts.loc[posts["at_best"], "eventual_fill"].mean()) if not posts.empty and posts["at_best"].any() else None,
        "off_best_fill_rate": to_float(posts.loc[~posts["at_best"], "eventual_fill"].mean()) if not posts.empty and (~posts["at_best"]).any() else None,
        "fill_delay_mean": to_float(maker["fill_delay"].mean()) if not maker.empty else None,
        "queue_haircut_proxy": to_float(1.0 - posts.loc[posts["at_best"], "fill_share"].median()) if not posts.empty and posts["at_best"].any() else None,
        "taker_slippage_mean": to_float(taker["slippage_vs_touch"].mean()) if not taker.empty else None,
        "taker_visible_depth_mean": to_float(taker["visible_depth"].mean()) if not taker.empty else None,
        "markout_1_mean": to_float(maker["markout_1"].mean()) if not maker.empty else None,
        "markout_5_mean": to_float(maker["markout_5"].mean()) if not maker.empty else None,
    }
    plot_data = {
        "distance_fill_labels": [str(x) for x in distance_fill.index],
        "distance_fill_values": [float(v) for v in distance_fill.values],
        "spread_fill_labels": [str(x) for x in spread_fill.index],
        "spread_fill_values": [float(v) for v in spread_fill.values],
        "size_fill_labels": [str(x) for x in size_fill.index],
        "size_fill_values": [float(v) for v in size_fill.values],
        "fill_delay": maker["fill_delay"].dropna().tolist() if not maker.empty else [],
        "fill_share": posts["fill_share"].dropna().tolist() if not posts.empty else [],
        "taker_sweep_depth": taker["visible_depth"].dropna().tolist() if not taker.empty else [],
        "taker_slippage": taker["slippage_vs_touch"].dropna().tolist() if not taker.empty else [],
    }
    return summary, plot_data


def simulator_parameterization(
    product: str,
    fair: dict[str, Any],
    quote: dict[str, Any],
    arrival: dict[str, Any],
    sizes: dict[str, Any],
    price: dict[str, Any],
    fills: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "product": product,
        "position_limit": POSITION_LIMITS[product],
        "fair_process_type": "drift_regime" if fair.get("drifting") or fair.get("regime_switching") else "stationary_microprice",
        "fair_drift_mean": fair.get("daily_drift_mean"),
        "fair_step_vol": fair.get("rolling_vol_mean"),
        "spread_distribution": quote.get("spread_histogram"),
        "quote_offsets_by_level": quote.get("quote_offsets_median"),
        "level_presence": quote.get("level_presence"),
        "trade_arrival_model": {
            "active_timestamp_rate": arrival.get("active_timestamp_rate"),
            "trades_per_active_step_mean": arrival.get("trades_per_active_step_mean"),
            "gap_cv": arrival.get("gap_cv"),
        },
        "trade_size_distribution": sizes.get("discrete_size_top"),
        "trade_price_process": {
            "touch_rate": price.get("touch_rate"),
            "sweep_rate_visible": price.get("sweep_rate_visible"),
            "price_minus_fair_mean": price.get("price_minus_fair_mean"),
        },
        "maker_fill_approximation": {
            "passive_fill_rate": fills.get("passive_fill_rate") if fills else None,
            "best_quote_fill_rate": fills.get("best_quote_fill_rate") if fills else None,
            "queue_haircut_proxy": fills.get("queue_haircut_proxy") if fills else None,
        },
        "taker_fill_logic": {
            "visible_depth_mean": fills.get("taker_visible_depth_mean") if fills else None,
            "slippage_mean": fills.get("taker_slippage_mean") if fills else None,
        },
    }


def compare_actual_vs_sim(actual_prices: pd.DataFrame, actual_trades: pd.DataFrame, sim_prices: pd.DataFrame | None, sim_trades: pd.DataFrame | None, product: str) -> dict[str, Any]:
    if sim_prices is None or sim_trades is None:
        return {}
    a_p = actual_prices[actual_prices["product"] == product]
    s_p = sim_prices[sim_prices["product"] == product]
    a_t = actual_trades[actual_trades["product"] == product]
    s_t = sim_trades[sim_trades["product"] == product]
    if a_p.empty or s_p.empty:
        return {}
    return {
        "spread_hist": {
            "actual": a_p["spread"].dropna().tolist(),
            "simulated": s_p["spread"].dropna().tolist(),
        },
        "trade_size_hist": {
            "actual": a_t["quantity"].dropna().tolist() if not a_t.empty else [],
            "simulated": s_t["quantity"].dropna().tolist() if not s_t.empty else [],
        },
        "price_minus_fair_hist": {
            "actual": a_t["price_minus_fair"].dropna().tolist() if not a_t.empty else [],
            "simulated": s_t["price_minus_fair"].dropna().tolist() if not s_t.empty else [],
        },
        "fair_paths": {
            "actual": a_p.groupby(["day", "timestamp"])["fair_value"].mean().reset_index(),
            "simulated": s_p.groupby(["day", "timestamp"])["fair_value"].mean().reset_index(),
        },
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


def write_csv(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def product_report_text(product: str, fair: dict[str, Any], quote: dict[str, Any], arrival: dict[str, Any], sizes: dict[str, Any], price: dict[str, Any], fills: dict[str, Any] | None, reduced_form: dict[str, Any]) -> str:
    def yes_no(value: bool | None) -> str:
        if value is None:
            return "unclear"
        return "yes" if value else "no"

    lines = [
        f"# {product}",
        "",
        "## Fair value dynamics",
        f"- Approximately stationary: {yes_no(fair.get('stationary'))}",
        f"- Drifting: {yes_no(fair.get('drifting'))}",
        f"- Mean reverting: {yes_no(fair.get('mean_reverting'))}",
        f"- Jumpy: {yes_no(fair.get('jumpy'))}",
        f"- Plausibly regime switching: {yes_no(fair.get('regime_switching'))}",
        f"- Average daily drift: {fair.get('daily_drift_mean')}",
        f"- Average rolling volatility: {fair.get('rolling_vol_mean')}",
        "",
        "## Order book structure",
        f"- Typical spread mode: {quote.get('spread_mode')}",
        f"- One-sided state rate: {quote.get('one_sided_rate')}",
        f"- Inner-wall rate: {quote.get('inner_wall_rate')}",
        f"- Level presence: {quote.get('level_presence')}",
        f"- Median quote offsets from fair: {quote.get('quote_offsets_median')}",
        "",
        "## Trade arrival behaviour",
        f"- Trades per active timestamp: {arrival.get('trades_per_active_step_mean')}",
        f"- Active timestamp rate: {arrival.get('active_timestamp_rate')}",
        f"- Mean gap between active timestamps: {arrival.get('mean_gap')}",
        f"- Gap CV: {arrival.get('gap_cv')}",
        f"- Approximately Poisson-like: {yes_no(arrival.get('poisson_like'))}",
        "",
        "## Trade size behaviour",
        f"- Mean size: {sizes.get('size_mean')}",
        f"- Median size: {sizes.get('size_median')}",
        f"- Buy median vs sell median: {sizes.get('buy_size_median')} vs {sizes.get('sell_size_median')}",
        f"- Dominant discrete sizes: {sizes.get('discrete_size_top')}",
        "",
        "## Trade price behaviour",
        f"- Mean price minus fair: {price.get('price_minus_fair_mean')}",
        f"- Mean price minus mid: {price.get('price_minus_mid_mean')}",
        f"- Print locations: {price.get('print_location')}",
        f"- Visible sweep rate: {price.get('sweep_rate_visible')}",
    ]
    if fills and fills.get("available"):
        lines.extend(
            [
                "",
                "## Maker fills",
                f"- Passive fill rate: {fills.get('passive_fill_rate')}",
                f"- Best quote fill rate: {fills.get('best_quote_fill_rate')}",
                f"- Off-best fill rate: {fills.get('off_best_fill_rate')}",
                f"- Mean fill delay: {fills.get('fill_delay_mean')}",
                f"- Queue haircut proxy: {fills.get('queue_haircut_proxy')}",
                f"- Markout 1-step / 5-step: {fills.get('markout_1_mean')} / {fills.get('markout_5_mean')}",
                "",
                "## Taker fills",
                f"- Mean visible depth swept: {fills.get('taker_visible_depth_mean')}",
                f"- Mean taker slippage vs touch: {fills.get('taker_slippage_mean')}",
            ]
        )
    lines.extend(
        [
            "",
            "## Reduced-form bot interpretation",
            f"- Fair process: {reduced_form.get('fair_process')}",
            f"- Suggested latent archetypes: {reduced_form.get('latent_archetypes')}",
            f"- Quote placement process: {reduced_form.get('quote_process')}",
            f"- Trade arrivals: {reduced_form.get('trade_arrival_process')}",
            f"- Simplest plausible explanation: {reduced_form.get('simplest_explanation')}",
        ]
    )
    return "\n".join(lines) + "\n"


def plot_product_outputs(
    output_dir: Path,
    product: str,
    product_prices: pd.DataFrame,
    fair_series: dict[str, list[float]],
    quote_plots: dict[str, Any],
    arrival_plots: dict[str, Any],
    size_plots: dict[str, Any],
    price_plots: dict[str, Any],
    fill_plots: dict[str, Any] | None,
    compare_plots: dict[str, Any] | None,
) -> None:
    slug = slugify(product)
    plot_dir = output_dir / slug / "plots"
    plot_dir.mkdir(parents=True, exist_ok=True)

    overlay_series = []
    for day, day_df in product_prices.groupby("day", sort=True):
        overlay_series.append({"label": f"day {day}", "x": day_df["timestamp"].tolist(), "y": day_df["fair_value"].tolist()})
    line_chart(plot_dir / "fair_value_paths.svg", f"{product} Fair Value Proxy", overlay_series, "Timestamp", "Fair value")
    histogram_chart(plot_dir / "fair_value_return_hist.svg", f"{product} Fair Value Change Histogram", [{"label": "actual", "values": fair_series["d_fair"]}], "Fair value change")
    histogram_chart(plot_dir / "rolling_vol_hist.svg", f"{product} Rolling Volatility", [{"label": "rolling vol", "values": fair_series["rolling_vol"]}], "Rolling volatility")
    line_chart(
        plot_dir / "autocorrelation.svg",
        f"{product} Fair Value Change Autocorrelation",
        [{"label": "ACF", "x": list(range(1, len(fair_series["acf"]) + 1)), "y": fair_series["acf"]}],
        "Lag",
        "Autocorrelation",
    )
    heatmap_chart(
        plot_dir / "quote_offset_heatmap.svg",
        f"{product} Quote Offset Heatmap",
        quote_plots["heatmap"],
        quote_plots["heatmap_x"],
        quote_plots["heatmap_y"],
        "Offset bucket vs fair",
        "Book level",
    )
    bar_chart(
        plot_dir / "spread_histogram.svg",
        f"{product} Top-of-Book Spread Histogram",
        quote_plots["spread_hist_labels"],
        quote_plots["spread_hist_values"],
        "Spread",
        "Frequency",
    )
    histogram_chart(
        plot_dir / "trades_per_active_step.svg",
        f"{product} Trades Per Active Timestamp",
        [{"label": "actual", "values": arrival_plots["arrivals_per_step"]}],
        "Trades at active timestamp",
    )
    ecdf_chart(
        plot_dir / "active_gap_ecdf.svg",
        f"{product} Active Timestamp Gap ECDF",
        [{"label": "gap", "values": arrival_plots["gaps"]}],
        "Gap",
    )
    histogram_chart(
        plot_dir / "trade_size_hist.svg",
        f"{product} Trade Size Histogram",
        [{"label": "buy", "values": size_plots["buy_sizes"]}, {"label": "sell", "values": size_plots["sell_sizes"]}],
        "Trade size",
    )
    histogram_chart(
        plot_dir / "trade_price_minus_fair.svg",
        f"{product} Trade Price Minus Fair",
        [{"label": "actual", "values": price_plots["price_minus_fair"]}],
        "Price - fair",
    )
    histogram_chart(
        plot_dir / "trade_price_minus_mid.svg",
        f"{product} Trade Price Minus Mid",
        [{"label": "actual", "values": price_plots["price_minus_mid"]}],
        "Price - mid",
    )

    if fill_plots:
        if fill_plots.get("distance_fill_labels"):
            bar_chart(plot_dir / "passive_fill_vs_distance.svg", f"{product} Passive Fill Rate vs Quote Distance", fill_plots["distance_fill_labels"], fill_plots["distance_fill_values"], "Quote distance bucket", "Fill rate")
        if fill_plots.get("spread_fill_labels"):
            bar_chart(plot_dir / "passive_fill_vs_spread.svg", f"{product} Passive Fill Rate vs Spread", fill_plots["spread_fill_labels"], fill_plots["spread_fill_values"], "Spread bucket", "Fill rate")
        if fill_plots.get("size_fill_labels"):
            bar_chart(plot_dir / "passive_fill_vs_size.svg", f"{product} Passive Fill Rate vs Size", fill_plots["size_fill_labels"], fill_plots["size_fill_values"], "Size bucket", "Fill rate")
        if fill_plots.get("fill_delay"):
            histogram_chart(plot_dir / "maker_fill_delay.svg", f"{product} Passive Fill Delay", [{"label": "delay", "values": fill_plots["fill_delay"]}], "Delay")
        if fill_plots.get("taker_sweep_depth"):
            histogram_chart(plot_dir / "taker_sweep_depth.svg", f"{product} Taker Visible Depth Sweep", [{"label": "depth", "values": fill_plots["taker_sweep_depth"]}], "Visible depth")
        if fill_plots.get("taker_slippage"):
            histogram_chart(plot_dir / "taker_slippage.svg", f"{product} Taker Slippage vs Touch", [{"label": "slippage", "values": fill_plots["taker_slippage"]}], "Slippage")

    if compare_plots:
        histogram_chart(
            plot_dir / "compare_spread_actual_vs_sim.svg",
            f"{product} Spread Actual vs Simulated",
            [{"label": "actual", "values": compare_plots["spread_hist"]["actual"]}, {"label": "simulated", "values": compare_plots["spread_hist"]["simulated"]}],
            "Spread",
        )
        histogram_chart(
            plot_dir / "compare_trade_size_actual_vs_sim.svg",
            f"{product} Trade Size Actual vs Simulated",
            [{"label": "actual", "values": compare_plots["trade_size_hist"]["actual"]}, {"label": "simulated", "values": compare_plots["trade_size_hist"]["simulated"]}],
            "Trade size",
        )
        histogram_chart(
            plot_dir / "compare_trade_price_minus_fair.svg",
            f"{product} Trade Price Minus Fair Actual vs Simulated",
            [{"label": "actual", "values": compare_plots["price_minus_fair_hist"]["actual"]}, {"label": "simulated", "values": compare_plots["price_minus_fair_hist"]["simulated"]}],
            "Price - fair",
        )
        actual_paths = compare_plots["fair_paths"]["actual"]
        sim_paths = compare_plots["fair_paths"]["simulated"]
        line_chart(
            plot_dir / "compare_fair_paths.svg",
            f"{product} Fair Value Paths Actual vs Simulated",
            [
                {"label": "actual", "x": actual_paths["timestamp"].tolist(), "y": actual_paths["fair_value"].tolist()},
                {"label": "simulated", "x": sim_paths["timestamp"].tolist(), "y": sim_paths["fair_value"].tolist()},
            ],
            "Timestamp",
            "Fair value",
        )


def run_analysis(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    data_dir = repo_root / "data" / "round1"
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    prices = preprocess_prices(load_round_csvs(data_dir, "prices"))
    trades = merge_trades_to_prices(load_round_csvs(data_dir, "trades"), prices)

    sim_prices = sim_trades = None
    if args.compare_root:
        compare_root = Path(args.compare_root).resolve()
        sim_prices = preprocess_prices(load_round_csvs(compare_root, "prices", recursive=True))
        sim_trades = merge_trades_to_prices(load_round_csvs(compare_root, "trades", recursive=True), sim_prices)

    probe_logs = []
    if args.probe_log:
        probe_logs.extend(Path(p).resolve() for p in args.probe_log)
    if args.probe_log_glob:
        probe_logs.extend(sorted(repo_root.glob(args.probe_log_glob)))
    probe_bundles = [bundle for path in probe_logs if path.exists() for bundle in [parse_probe_log(path)] if bundle is not None]

    fill_frames = {"passive_posts": [], "passive_fills": [], "taker_fills": []}
    for bundle in probe_bundles:
        result = infer_fill_features(bundle)
        for key, frame in result.items():
            if frame is not None and not frame.empty:
                frame = frame.copy()
                frame["probe_name"] = bundle.name
                fill_frames[key].append(frame)
    fill_data = {key: pd.concat(value, ignore_index=True) if value else pd.DataFrame() for key, value in fill_frames.items()}

    selected_products = PRODUCTS if args.product == "all" else [args.product]
    summary = {"products": {}, "probe_logs_used": [bundle.name for bundle in probe_bundles], "compare_root": args.compare_root}

    for product in selected_products:
        product_dir = output_dir / slugify(product)
        product_dir.mkdir(parents=True, exist_ok=True)
        p_prices = prices[prices["product"] == product].copy()
        p_trades = trades[trades["product"] == product].copy()
        fair, fair_series = fair_value_summary(p_prices)
        quote, quote_plots = quote_structure_summary(p_prices)
        arrival, arrival_plots = arrival_summary(p_trades, p_prices)
        sizes, size_plots = trade_size_summary(p_trades)
        trade_price, price_plots = trade_price_summary(p_trades)
        fills, fill_plots = fill_summary(fill_data, product)
        reduced = infer_product_story(product, fair, quote, arrival, sizes, trade_price)
        params = simulator_parameterization(product, fair, quote, arrival, sizes, trade_price, fills if fills.get("available") else None)
        compare = compare_actual_vs_sim(prices, trades, sim_prices, sim_trades, product) if sim_prices is not None and sim_trades is not None else {}

        plot_product_outputs(output_dir, product, p_prices, fair_series, quote_plots, arrival_plots, size_plots, price_plots, fill_plots if fills.get("available") else None, compare or None)

        summary_tables_dir = product_dir / "tables"
        summary_tables_dir.mkdir(parents=True, exist_ok=True)
        write_csv(summary_tables_dir / "price_snapshots.csv", p_prices.head(2000))
        write_csv(summary_tables_dir / "trade_samples.csv", p_trades.head(2000))
        if fill_data["passive_posts"].shape[0]:
            write_csv(summary_tables_dir / "probe_passive_posts.csv", fill_data["passive_posts"][fill_data["passive_posts"]["product"] == product])
        if fill_data["passive_fills"].shape[0]:
            write_csv(summary_tables_dir / "probe_passive_fills.csv", fill_data["passive_fills"][fill_data["passive_fills"]["product"] == product])
        if fill_data["taker_fills"].shape[0]:
            write_csv(summary_tables_dir / "probe_taker_fills.csv", fill_data["taker_fills"][fill_data["taker_fills"]["product"] == product])

        report = product_report_text(product, fair, quote, arrival, sizes, trade_price, fills if fills.get("available") else None, reduced)
        (product_dir / "report.md").write_text(report)
        write_json(product_dir / "summary.json", {"fair": fair, "quote": quote, "arrival": arrival, "trade_sizes": sizes, "trade_price": trade_price, "fills": fills, "reduced_form": reduced})
        write_json(product_dir / "simulator_parameters.json", params)
        summary["products"][product] = {"fair": fair, "quote": quote, "arrival": arrival, "trade_sizes": sizes, "trade_price": trade_price, "fills": fills, "reduced_form": reduced, "simulator_parameters": params}

    write_json(output_dir / "round1_analysis_summary.json", summary)
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze Prosperity 4 Round 1 market data and probe logs.")
    parser.add_argument("--repo-root", default=".", help="Path to the imc-prosperity-4 repo root.")
    parser.add_argument("--output-dir", default="analysis_outputs/round1_market", help="Where analysis artifacts should be written.")
    parser.add_argument("--product", choices=["all"] + PRODUCTS, default="all", help="Analyze one product or all Round 1 products.")
    parser.add_argument("--probe-log", action="append", help="Path to a probe/backtest log to use for fill inference.")
    parser.add_argument("--probe-log-glob", help="Glob relative to repo root for probe logs, e.g. 'backtests/*.log'.")
    parser.add_argument("--compare-root", help="Optional root directory containing simulated prices/trades CSVs for actual-vs-sim overlays.")
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    run_analysis(args)
