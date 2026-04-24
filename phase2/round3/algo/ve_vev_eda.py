#!/usr/bin/env python3
"""
Round 3 EDA for VELVETFRUIT_EXTRACT and VEV vouchers.

Run from repo root:
    python phase2/round3/algo/ve_vev_eda.py
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_mpl_config = Path(__file__).resolve().parent / "analysis" / ".mplconfig"
_mpl_config.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(_mpl_config))

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.stats import jarque_bera, kurtosis, norm, skew
from statsmodels.tsa.stattools import acf as compute_acf_vals, adfuller, kpss


SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
OUTPUT_DIR = SCRIPT_DIR / "analysis" / "ve_vev_eda_output"

UNDERLYING = "VELVETFRUIT_EXTRACT"
UNDERLYING_LABEL = "Velvetfruit"
UNDERLYING_FILE_PREFIX = "velvetfruit"
IGNORED_PRODUCT = "HYDROGEL_PACK"
VOUCHERS = [
    "VEV_4000",
    "VEV_4500",
    "VEV_5000",
    "VEV_5100",
    "VEV_5200",
    "VEV_5300",
    "VEV_5400",
    "VEV_5500",
    "VEV_6000",
    "VEV_6500",
]
PRODUCTS = [UNDERLYING] + VOUCHERS
TTE_DAYS = {0: 8, 1: 7, 2: 6}
RISK_FREE_RATE = 0.0
TICKS_PER_DAY = 10_000
TIMESTAMP_STEP = 100


@dataclass
class OutputPaths:
    root: Path
    charts: Path
    tables: Path


def strike(product: str) -> int:
    return int(product.rsplit("_", 1)[1])


def ensure_output(output_dir: Path) -> OutputPaths:
    charts = output_dir / "charts"
    tables = output_dir / "tables"
    charts.mkdir(parents=True, exist_ok=True)
    tables.mkdir(parents=True, exist_ok=True)
    for folder in [charts, tables]:
        for path in folder.iterdir():
            if path.is_file():
                path.unlink()
    report = output_dir / "REPORT.md"
    if report.is_file():
        report.unlink()
    return OutputPaths(output_dir, charts, tables)


def to_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def load_data(data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, bool]:
    price_frames = []
    trade_frames = []

    for day in [0, 1, 2]:
        price_path = data_dir / f"prices_round_3_day_{day}.csv"
        trade_path = data_dir / f"trades_round_3_day_{day}.csv"
        prices = pd.read_csv(price_path, sep=";")
        prices["day"] = day
        price_frames.append(prices)

        trades = pd.read_csv(trade_path, sep=";")
        trades["day"] = day
        trade_frames.append(trades)

    prices = pd.concat(price_frames, ignore_index=True)
    trades = pd.concat(trade_frames, ignore_index=True)

    price_numeric = [
        "timestamp",
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
    for col in price_numeric:
        prices[col] = to_num(prices[col])

    for col in ["timestamp", "price", "quantity"]:
        trades[col] = to_num(trades[col])

    hydrogel_present = bool((prices["product"] == IGNORED_PRODUCT).any() or (trades["symbol"] == IGNORED_PRODUCT).any())
    prices = prices[prices["product"].isin(PRODUCTS)].copy()
    trades = trades[trades["symbol"].isin(PRODUCTS)].copy()

    prices.sort_values(["day", "product", "timestamp"], inplace=True)
    trades.sort_values(["day", "symbol", "timestamp"], inplace=True)
    return prices, trades, hydrogel_present


def save_fig(path: Path) -> None:
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def series_for(prices: pd.DataFrame, product: str) -> pd.DataFrame:
    return prices[prices["product"] == product].sort_values(["day", "timestamp"]).copy()


def add_global_tick(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["global_tick"] = out["day"] * 1_000_000 + out["timestamp"]
    return out


def add_day_dividers(ax: plt.Axes) -> None:
    for day in [1, 2]:
        ax.axvline(day * 1_000_000, color="black", ls="--", alpha=0.35, lw=0.8)


def plot_by_day(ax: plt.Axes, df: pd.DataFrame, y_col: str, label: str | None = None, **kwargs: Any) -> None:
    line_color = kwargs.get("color")
    for day, group in df.sort_values(["day", "timestamp"]).groupby("day"):
        line_kwargs = dict(kwargs)
        if line_color is not None:
            line_kwargs["color"] = line_color
        lines = ax.plot(group["global_tick"], group[y_col], label=label if day == 0 else None, **line_kwargs)
        if line_color is None and lines:
            line_color = lines[0].get_color()


def trades_per_1000_timestamps(trade_count: int, snapshot_count: int) -> float:
    if snapshot_count <= 0:
        return float("nan")
    return float(trade_count) / (float(snapshot_count) / 1000.0)


def log_returns(values: pd.Series, horizon: int = 1) -> pd.Series:
    clean = values.astype(float).replace(0, np.nan)
    return np.log(clean).diff(horizon)


def forward_log_returns(values: pd.Series, horizon: int) -> pd.Series:
    clean = values.astype(float).replace(0, np.nan)
    return np.log(clean).shift(-horizon) - np.log(clean)


def safe_test(fn, values: pd.Series, **kwargs: Any) -> dict[str, Any]:
    vals = values.dropna().astype(float)
    if len(vals) < 50 or vals.nunique() < 3:
        return {"stat": float("nan"), "pvalue": float("nan"), "error": "insufficient data"}
    try:
        result = fn(vals, **kwargs)
        return {"stat": float(result[0]), "pvalue": float(result[1])}
    except Exception as exc:  # noqa: BLE001 - report diagnostics, don't crash EDA
        return {"stat": float("nan"), "pvalue": float("nan"), "error": str(exc)}


def variance_ratio(values: pd.Series, lag: int) -> dict[str, float]:
    vals = values.dropna().astype(float).to_numpy()
    if len(vals) <= lag + 2:
        return {"vr": float("nan"), "z": float("nan")}
    diffs = np.diff(vals)
    var_1 = np.var(diffs, ddof=1)
    lag_diffs = vals[lag:] - vals[:-lag]
    var_q = np.var(lag_diffs, ddof=1) / lag
    vr = var_q / var_1 if var_1 > 0 else float("nan")
    z = (vr - 1.0) * math.sqrt(len(vals)) if np.isfinite(vr) else float("nan")
    return {"vr": float(vr), "z": float(z)}


def hurst_rs(values: pd.Series) -> float:
    vals = values.dropna().astype(float).to_numpy()
    if len(vals) < 100:
        return float("nan")
    max_lag = min(2000, len(vals) // 2)
    lags = np.unique(np.logspace(np.log10(10), np.log10(max_lag), 18).astype(int))
    rs_values = []
    used_lags = []
    for lag in lags:
        chunks = len(vals) // lag
        if chunks < 2:
            continue
        chunk_rs = []
        for idx in range(chunks):
            x = vals[idx * lag : (idx + 1) * lag]
            y = x - np.mean(x)
            z = np.cumsum(y)
            r = np.max(z) - np.min(z)
            s = np.std(x, ddof=1)
            if s > 0:
                chunk_rs.append(r / s)
        if chunk_rs:
            rs_values.append(np.mean(chunk_rs))
            used_lags.append(lag)
    if len(rs_values) < 2:
        return float("nan")
    slope, _ = np.polyfit(np.log(used_lags), np.log(rs_values), 1)
    return float(slope)


def annualized_rv(log_ret: pd.Series, window: int) -> pd.Series:
    return log_ret.rolling(window).std() * math.sqrt(TICKS_PER_DAY * 365)


def bs_call_price(s: float, k: float, t: float, sigma: float, r: float = RISK_FREE_RATE) -> float:
    if t <= 0 or sigma <= 0 or s <= 0 or k <= 0:
        return max(s - k, 0.0)
    vol_sqrt = sigma * math.sqrt(t)
    d1 = (math.log(s / k) + (r + 0.5 * sigma * sigma) * t) / vol_sqrt
    d2 = d1 - vol_sqrt
    return s * norm.cdf(d1) - k * math.exp(-r * t) * norm.cdf(d2)


def bs_greeks(s: float, k: float, t: float, sigma: float, r: float = RISK_FREE_RATE) -> dict[str, float]:
    if t <= 0 or sigma <= 0 or s <= 0 or k <= 0:
        return {"delta": float("nan"), "gamma": float("nan"), "vega": float("nan"), "theta": float("nan")}
    vol_sqrt = sigma * math.sqrt(t)
    d1 = (math.log(s / k) + (r + 0.5 * sigma * sigma) * t) / vol_sqrt
    d2 = d1 - vol_sqrt
    delta = norm.cdf(d1)
    gamma = norm.pdf(d1) / (s * vol_sqrt)
    vega = s * norm.pdf(d1) * math.sqrt(t) / 100.0
    theta = (-(s * norm.pdf(d1) * sigma) / (2 * math.sqrt(t)) - r * k * math.exp(-r * t) * norm.cdf(d2)) / 365.0
    return {"delta": float(delta), "gamma": float(gamma), "vega": float(vega), "theta": float(theta)}


def bs_greeks_vec(s_arr: np.ndarray, k: float, t: float, sigma_arr: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorized BS delta, gamma, vega for fixed k and t."""
    delta = np.full_like(s_arr, np.nan, dtype=float)
    gamma = np.full_like(s_arr, np.nan, dtype=float)
    vega = np.full_like(s_arr, np.nan, dtype=float)
    valid = np.isfinite(s_arr) & np.isfinite(sigma_arr) & (s_arr > 0) & (sigma_arr > 0) & (t > 0) & (k > 0)
    if not valid.any():
        return delta, gamma, vega
    s = s_arr[valid]
    sig = sigma_arr[valid]
    vol_sqrt = sig * np.sqrt(t)
    d1 = (np.log(s / k) + 0.5 * sig**2 * t) / vol_sqrt
    delta[valid] = norm.cdf(d1)
    gamma[valid] = norm.pdf(d1) / (s * vol_sqrt)
    vega[valid] = s * norm.pdf(d1) * np.sqrt(t)
    return delta, gamma, vega


def implied_vol(s: float, k: float, t: float, c: float) -> tuple[float, str]:
    if not np.isfinite([s, k, t, c]).all() or s <= 0 or k <= 0 or t <= 0:
        return float("nan"), "invalid_input"
    lower = max(s - k, 0.0)
    upper = s
    if c < lower - 1e-9:
        return float("nan"), "below_lower_bound"
    if c > upper + 1e-9:
        return float("nan"), "above_upper_bound"
    if c <= lower + 1e-9:
        return float("nan"), "zero_time_value"
    try:
        f = lambda sigma: bs_call_price(s, k, t, sigma) - c
        return float(brentq(f, 1e-6, 5.0, maxiter=100)), "ok"
    except Exception as exc:  # noqa: BLE001
        return float("nan"), f"brent_failed:{exc}"


def write_table(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False)


def clean_markdown_table(df: pd.DataFrame, max_rows: int = 20) -> str:
    view = df.head(max_rows).copy()
    for col in view.columns:
        if pd.api.types.is_float_dtype(view[col]):
            view[col] = view[col].map(lambda x: "" if pd.isna(x) else f"{x:.4g}")
    headers = [str(col) for col in view.columns]
    rows = [[str(value) for value in row] for row in view.astype(object).to_numpy()]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def section1_data_integrity(prices: pd.DataFrame, trades: pd.DataFrame, out: OutputPaths) -> tuple[pd.DataFrame, list[str]]:
    rows = []
    anomalies = []
    expected_timestamps = set(range(0, 999_901, TIMESTAMP_STEP))

    for day in [0, 1, 2]:
        for product in PRODUCTS:
            p = prices[(prices["day"] == day) & (prices["product"] == product)].copy()
            t = trades[(trades["day"] == day) & (trades["symbol"] == product)].copy()
            timestamps = set(p["timestamp"].dropna().astype(int))
            gaps = len(expected_timestamps - timestamps)
            duplicate_ts = int(p.duplicated(["timestamp"]).sum())
            crossed = int((p["bid_price_1"] > p["ask_price_1"]).sum())
            missing_mid = int(p["mid_price"].isna().sum())
            only_l1 = p[["bid_price_2", "ask_price_2", "bid_price_3", "ask_price_3"]].isna().all(axis=1).mean()
            deeper = 1.0 - only_l1

            book = p[["timestamp", "bid_price_1", "ask_price_1"]].rename(columns={"timestamp": "timestamp"})
            merged = t.merge(book, on="timestamp", how="left")
            no_book = int(merged["bid_price_1"].isna().sum())
            outside = int(((merged["price"] < merged["bid_price_1"]) | (merged["price"] > merged["ask_price_1"])).fillna(False).sum())

            if crossed:
                anomalies.append(f"day {day} {product}: {crossed} crossed top books")
            if missing_mid:
                anomalies.append(f"day {day} {product}: {missing_mid} missing mids")
            if duplicate_ts:
                anomalies.append(f"day {day} {product}: {duplicate_ts} duplicate timestamps")
            if no_book:
                anomalies.append(f"day {day} {product}: {no_book} trades without same-timestamp book")
            if outside:
                anomalies.append(f"day {day} {product}: {outside} trades outside top bid/ask")

            rows.append(
                {
                    "day": day,
                    "product": product,
                    "book_snapshots": len(p),
                    "trades": len(t),
                    "timestamp_min": p["timestamp"].min(),
                    "timestamp_max": p["timestamp"].max(),
                    "timestamp_grid_gaps": gaps,
                    "crossed_books": crossed,
                    "missing_mid": missing_mid,
                    "duplicate_timestamps": duplicate_ts,
                    "trades_without_book": no_book,
                    "trades_outside_top_book": outside,
                    "fraction_only_level1": only_l1,
                    "fraction_deeper_book": deeper,
                }
            )

    integrity = pd.DataFrame(rows)
    write_table(integrity, out.tables / "data_integrity.csv")

    plt.figure(figsize=(12, 5))
    for product in PRODUCTS:
        counts = integrity[integrity["product"] == product].sort_values("day")
        plt.plot(counts["day"], counts["trades"], marker="o", label=product)
    plt.yscale("symlog")
    plt.title("Trade Counts By Product And Day")
    plt.xlabel("Day")
    plt.ylabel("Trades (symlog)")
    plt.legend(ncol=3, fontsize=7)
    save_fig(out.charts / "data_integrity_trade_counts.png")

    plt.figure(figsize=(12, 5))
    pivot = integrity.pivot(index="product", columns="day", values="fraction_deeper_book").loc[PRODUCTS]
    plt.imshow(pivot.values, aspect="auto")
    plt.colorbar(label="Fraction deeper than level 1")
    plt.xticks(range(3), [f"day {d}" for d in pivot.columns])
    plt.yticks(range(len(pivot.index)), pivot.index)
    plt.title("Book Depth Availability")
    save_fig(out.charts / "data_integrity_depth_heatmap.png")
    return integrity, anomalies


def section2_vev(prices: pd.DataFrame, trades: pd.DataFrame, out: OutputPaths) -> dict[str, Any]:
    vev = add_global_tick(series_for(prices, UNDERLYING))
    vev_trades = trades[trades["symbol"] == UNDERLYING].copy()

    plt.figure(figsize=(14, 5))
    plt.plot(vev["global_tick"], vev["mid_price"], lw=1)
    for day in [1, 2]:
        plt.axvline(day * 1_000_000, color="black", ls="--", alpha=0.5)
    plt.title("Velvetfruit Mid Price Across Historical Days")
    plt.xlabel("Global tick")
    plt.ylabel("Mid")
    save_fig(out.charts / "velvetfruit_mid_timeseries.png")

    for day in [0, 1, 2]:
        d = vev[vev["day"] == day]
        plt.figure(figsize=(12, 4))
        plt.plot(d["timestamp"], d["mid_price"], lw=1)
        plt.title(f"Velvetfruit Mid - Day {day}, TTE {TTE_DAYS[day]}d")
        plt.xlabel("Timestamp")
        plt.ylabel("Mid")
        save_fig(out.charts / f"velvetfruit_mid_day{day}.png")

    return_rows = []
    for day_label, group in list(vev.groupby("day")) + [("pooled", vev)]:
        for horizon in [1, 10]:
            rets = group.groupby("day")["mid_price"].transform(lambda x: log_returns(x, horizon)) if day_label == "pooled" else log_returns(group["mid_price"], horizon)
            clean = rets.dropna()
            jb = jarque_bera(clean) if len(clean) else (float("nan"), float("nan"))
            return_rows.append(
                {
                    "day": day_label,
                    "horizon_ticks": horizon,
                    "mean": clean.mean(),
                    "std": clean.std(),
                    "skew": skew(clean) if len(clean) else float("nan"),
                    "kurtosis": kurtosis(clean, fisher=True) if len(clean) else float("nan"),
                    "jarque_bera_stat": float(jb.statistic if hasattr(jb, "statistic") else jb[0]),
                    "jarque_bera_pvalue": float(jb.pvalue if hasattr(jb, "pvalue") else jb[1]),
                }
            )

            plt.figure(figsize=(8, 4))
            plt.hist(clean, bins=80, density=True, alpha=0.7)
            mu, sig = clean.mean(), clean.std()
            if sig > 0:
                xs = np.linspace(clean.quantile(0.001), clean.quantile(0.999), 300)
                plt.plot(xs, norm.pdf(xs, mu, sig), color="black", lw=1.5, label="Normal overlay")
            plt.title(f"Velvetfruit log returns h={horizon}, {day_label}")
            plt.legend()
            save_fig(out.charts / f"velvetfruit_return_hist_{day_label}_h{horizon}.png")

    return_stats = pd.DataFrame(return_rows)
    write_table(return_stats, out.tables / "velvetfruit_return_stats.csv")

    stationarity = {
        "adf_mid": safe_test(adfuller, vev["mid_price"], autolag="AIC"),
        "kpss_mid": safe_test(kpss, vev["mid_price"], regression="c", nlags="auto"),
        "variance_ratio": {str(lag): variance_ratio(vev["mid_price"], lag) for lag in [2, 5, 10, 20]},
        "hurst_rs": hurst_rs(vev["mid_price"]),
    }
    adf_p = stationarity["adf_mid"].get("pvalue", float("nan"))
    kpss_p = stationarity["kpss_mid"].get("pvalue", float("nan"))
    hurst = stationarity["hurst_rs"]
    if adf_p < 0.05 and kpss_p > 0.05:
        regime = "stationary"
    elif np.isfinite(hurst) and hurst < 0.45:
        regime = "mean-reverting"
    elif np.isfinite(hurst) and hurst > 0.6:
        regime = "trending"
    elif kpss_p < 0.05:
        regime = "drifting"
    else:
        regime = "regime-switching"
    stationarity["regime_classification"] = regime
    (out.tables / "velvetfruit_stationarity.json").write_text(json.dumps(stationarity, indent=2), encoding="utf-8")

    rv_frames = []
    for day, group in vev.groupby("day"):
        g = group.copy()
        g["log_ret_1"] = log_returns(g["mid_price"], 1)
        for window in [100, 500, 2000]:
            g[f"rv_{window}"] = annualized_rv(g["log_ret_1"], window)
        rv_frames.append(g[["day", "timestamp", "global_tick", "log_ret_1", "rv_100", "rv_500", "rv_2000"]])
        plt.figure(figsize=(12, 4))
        for window in [100, 500, 2000]:
            plt.plot(g["timestamp"], g[f"rv_{window}"], label=f"RV {window}")
        plt.title(f"Velvetfruit Annualized Rolling Realized Vol - Day {day}")
        plt.xlabel("Timestamp")
        plt.ylabel("Annualized vol")
        plt.legend()
        save_fig(out.charts / f"velvetfruit_realized_vol_day{day}.png")
    rv = pd.concat(rv_frames, ignore_index=True)
    write_table(rv, out.tables / "velvetfruit_realized_vol.csv")

    vev["spread"] = vev["ask_price_1"] - vev["bid_price_1"]
    spread_summary = vev.groupby("day")["spread"].agg(["mean", "median", lambda x: x.mode().iloc[0], lambda x: x.quantile(0.95)]).reset_index()
    spread_summary.columns = ["day", "mean", "median", "mode", "p95"]
    write_table(spread_summary, out.tables / "velvetfruit_spread_summary.csv")

    plt.figure(figsize=(8, 4))
    plt.hist(vev["spread"].dropna(), bins=40)
    plt.title("Velvetfruit Top-Of-Book Spread Distribution")
    plt.xlabel("Ask1 - Bid1")
    save_fig(out.charts / "velvetfruit_spread_hist.png")

    merged_rv = rv[["day", "timestamp", "rv_500"]]
    spread_rv = vev.merge(merged_rv, on=["day", "timestamp"], how="left")
    spread_rv["rolling_spread"] = spread_rv.groupby("day")["spread"].transform(lambda x: x.rolling(500).mean())
    fig, ax1 = plt.subplots(figsize=(12, 4))
    plot_by_day(ax1, spread_rv, "rolling_spread", label="Rolling spread", color="tab:blue")
    ax1.set_ylabel("Spread")
    ax2 = ax1.twinx()
    plot_by_day(ax2, spread_rv, "rv_500", label="RV 500", color="tab:orange", alpha=0.7)
    ax2.set_ylabel("Annualized RV")
    add_day_dividers(ax1)
    plt.title("Velvetfruit Rolling Spread vs Rolling Realized Vol (500-tick warmup per day)")
    save_fig(out.charts / "velvetfruit_spread_vs_rv.png")

    depth_cols = ["bid_volume_1", "bid_volume_2", "bid_volume_3", "ask_volume_1", "ask_volume_2", "ask_volume_3"]
    depth_summary = vev.groupby("day")[depth_cols].mean().reset_index()
    write_table(depth_summary, out.tables / "velvetfruit_depth_summary.csv")
    vev["total_depth"] = vev[depth_cols].sum(axis=1, skipna=True)
    vev["imbalance"] = (vev["bid_volume_1"] - vev["ask_volume_1"]) / (vev["bid_volume_1"] + vev["ask_volume_1"])
    vev["fwd_ret_10"] = vev.groupby("day")["mid_price"].transform(lambda x: forward_log_returns(x, 10))
    reg = vev[["imbalance", "fwd_ret_10"]].dropna()
    if len(reg) > 10 and reg["imbalance"].std() > 0:
        beta, alpha = np.polyfit(reg["imbalance"], reg["fwd_ret_10"], 1)
        pred = alpha + beta * reg["imbalance"]
        r2 = 1.0 - ((reg["fwd_ret_10"] - pred) ** 2).sum() / ((reg["fwd_ret_10"] - reg["fwd_ret_10"].mean()) ** 2).sum()
    else:
        beta, alpha, r2 = float("nan"), float("nan"), float("nan")
    pd.DataFrame([{"intercept": alpha, "imbalance_coef": beta, "r2": r2}]).to_csv(out.tables / "velvetfruit_imbalance_regression.csv", index=False)

    plt.figure(figsize=(12, 4))
    plt.plot(vev["global_tick"], vev["total_depth"], lw=0.8)
    plt.title("Velvetfruit Total Resting Book Volume")
    plt.xlabel("Global tick")
    plt.ylabel("Total top-3 volume")
    save_fig(out.charts / "velvetfruit_total_book_depth.png")

    vt = vev_trades.merge(vev[["day", "timestamp", "mid_price", "bid_price_1", "ask_price_1"]], on=["day", "timestamp"], how="left")
    vt["signed_side"] = np.where(vt["price"] > vt["mid_price"], "buy", np.where(vt["price"] < vt["mid_price"], "sell", "ambiguous"))
    vt["trade_minus_mid"] = vt["price"] - vt["mid_price"]
    vt["interarrival"] = vt.groupby("day")["timestamp"].diff()
    bins = np.arange(0, 1_000_001, 1000)
    flow = vt.groupby(["day", pd.cut(vt["timestamp"], bins=bins, include_lowest=True)], observed=False).size().rename("trades").reset_index()
    flow["bucket_start"] = flow["timestamp"].map(lambda x: x.left if pd.notna(x) else np.nan).astype(float)
    write_table(vt, out.tables / "velvetfruit_trade_enriched.csv")

    plt.figure(figsize=(8, 4))
    plt.hist(vt["interarrival"].dropna(), bins=80)
    plt.title("Velvetfruit Trade Inter-Arrival Times")
    save_fig(out.charts / "velvetfruit_trade_interarrival_hist.png")

    plt.figure(figsize=(12, 4))
    for day, group in flow.groupby("day"):
        plt.plot(group["bucket_start"] + day * 1_000_000, group["trades"], label=f"day {day}")
    plt.title("Velvetfruit Trades Per 1000 Timestamps")
    plt.legend()
    save_fig(out.charts / "velvetfruit_trades_per_1000.png")

    plt.figure(figsize=(8, 4))
    plt.hist(vt["quantity"].dropna(), bins=40, log=True)
    plt.title("Velvetfruit Trade Size Distribution")
    save_fig(out.charts / "velvetfruit_trade_size_hist.png")

    plt.figure(figsize=(8, 4))
    for side, group in vt.groupby("signed_side"):
        plt.hist(group["trade_minus_mid"].dropna(), bins=40, alpha=0.55, label=side)
    plt.title("Velvetfruit Trade Price - Mid By Inferred Side")
    plt.legend()
    save_fig(out.charts / "velvetfruit_trade_minus_mid_by_side.png")

    toxicity_rows = []
    book_lookup = vev.set_index(["day", "timestamp"])["mid_price"]
    for horizon in [100, 500, 1000]:
        fwd = vev[["day", "timestamp", "mid_price"]].copy()
        fwd["future_timestamp"] = fwd["timestamp"] + horizon
        future = vev[["day", "timestamp", "mid_price"]].rename(columns={"timestamp": "future_timestamp", "mid_price": "future_mid"})
        fwd = fwd.merge(future, on=["day", "future_timestamp"], how="left")
        tox = vt.merge(fwd[["day", "timestamp", "future_mid"]], on=["day", "timestamp"], how="left")
        tox["forward_return"] = np.log(tox["future_mid"]) - np.log(tox["mid_price"])
        for side, group in tox.groupby("signed_side"):
            toxicity_rows.append({"horizon": horizon, "side": side, "mean_forward_return": group["forward_return"].mean(), "count": len(group)})
    toxicity = pd.DataFrame(toxicity_rows)
    write_table(toxicity, out.tables / "velvetfruit_flow_toxicity.csv")

    trade_mid_frac = float((vt["trade_minus_mid"].abs() < 1e-9).mean()) if len(vt) else float("nan")
    wide_frac = float((vt["trade_minus_mid"].abs() > 0).mean()) if len(vt) else float("nan")

    # --- NEW: ACF of mid price and log returns (lags 1-20) ---
    mid_clean = vev["mid_price"].dropna()
    mid_acf = compute_acf_vals(mid_clean, nlags=20, fft=True)[1:]
    ret_clean = log_returns(vev["mid_price"], 1).dropna()
    ret_acf = compute_acf_vals(ret_clean, nlags=20, fft=True)[1:]
    ci_band = 1.96 / math.sqrt(len(mid_clean))
    acf_df = pd.DataFrame({"lag": range(1, 21), "mid_acf": mid_acf.tolist(), "ret_acf": ret_acf.tolist()})
    write_table(acf_df, out.tables / "velvetfruit_acf.csv")
    fig, axes = plt.subplots(1, 2, figsize=(14, 4))
    for ax, vals, title in zip(axes, [mid_acf, ret_acf], ["Mid Price", "Log Returns"]):
        ax.bar(range(1, 21), vals)
        ax.axhline(0, color="black", lw=0.8)
        ax.axhline(ci_band, ls="--", color="red", alpha=0.7, label="95% CI")
        ax.axhline(-ci_band, ls="--", color="red", alpha=0.7)
        ax.set_title(f"Velvetfruit {title} ACF (Lags 1–20)")
        ax.set_xlabel("Lag")
        ax.legend(fontsize=7)
    save_fig(out.charts / "velvetfruit_acf.png")

    # --- NEW: Quote placement relative to mid ---
    vev["bid_minus_mid"] = vev["bid_price_1"] - vev["mid_price"]
    vev["ask_minus_mid"] = vev["ask_price_1"] - vev["mid_price"]
    plt.figure(figsize=(10, 4))
    plt.hist(vev["bid_minus_mid"].dropna(), bins=30, alpha=0.6, label="bid1 − mid")
    plt.hist(vev["ask_minus_mid"].dropna(), bins=30, alpha=0.6, label="ask1 − mid")
    plt.title("Velvetfruit: Top-of-Book Quote Offset From Mid")
    plt.xlabel("Quote − Mid (ticks)")
    plt.legend()
    save_fig(out.charts / "velvetfruit_quote_offset_from_mid.png")
    quote_offset_df = pd.DataFrame({
        "metric": ["bid1-mid mean", "bid1-mid median", "bid1-mid std", "ask1-mid mean", "ask1-mid median", "ask1-mid std"],
        "value": [
            vev["bid_minus_mid"].mean(), vev["bid_minus_mid"].median(), vev["bid_minus_mid"].std(),
            vev["ask_minus_mid"].mean(), vev["ask_minus_mid"].median(), vev["ask_minus_mid"].std(),
        ],
    })
    write_table(quote_offset_df, out.tables / "velvetfruit_quote_offset_summary.csv")

    # --- NEW: Intraday spread evolution ---
    N_BINS = 20
    intraday_rows: list[dict] = []
    for day, group in vev.groupby("day"):
        group = group.copy()
        bins = np.linspace(group["timestamp"].min(), group["timestamp"].max(), N_BINS + 1)
        group["time_bin_idx"] = pd.cut(group["timestamp"], bins=bins, include_lowest=True, labels=False)
        for bin_idx, bgroup in group.groupby("time_bin_idx", observed=False):
            intraday_rows.append({"day": day, "bin_idx": int(bin_idx), "mean_spread": bgroup["spread"].mean()})
    intraday_spread_df = pd.DataFrame(intraday_rows)
    write_table(intraday_spread_df, out.tables / "velvetfruit_intraday_spread.csv")
    plt.figure(figsize=(10, 4))
    for day, group in intraday_spread_df.groupby("day"):
        plt.plot(group["bin_idx"], group["mean_spread"], marker="o", label=f"Day {day}")
    plt.title("Velvetfruit Intraday Spread Evolution (Early → Late)")
    plt.xlabel("Time bin")
    plt.ylabel("Mean spread")
    plt.legend()
    save_fig(out.charts / "velvetfruit_intraday_spread.png")

    # --- NEW: Trade clustering (Poisson dispersion test) ---
    clustering_rows: list[dict] = []
    for day, group in vev_trades.groupby("day"):
        bins = np.arange(0, 1_000_001, 1000)
        counts = pd.cut(group["timestamp"], bins=bins, include_lowest=True).value_counts().sort_index()
        m = counts.mean()
        v = float(counts.var())
        disp = v / m if m > 0 else float("nan")
        clustering_rows.append({"day": day, "mean_trades_per_1000tick_bin": m, "var_trades": v, "dispersion_index": disp, "interpretation": "clustered" if disp > 1.5 else ("underdispersed" if disp < 0.7 else "Poisson-like")})
    clustering_df = pd.DataFrame(clustering_rows)
    write_table(clustering_df, out.tables / "velvetfruit_trade_clustering.csv")

    # --- NEW: Fair value dynamics ---
    vev["delta_mid"] = vev.groupby("day")["mid_price"].diff()
    fv_dyn_rows: list[dict] = []
    for day, group in vev.groupby("day"):
        dm = group["delta_mid"].dropna()
        fv_dyn_rows.append({
            "day": day,
            "tick_vol_abs": float(dm.std()),
            "drift_per_tick": float(dm.mean()),
            "drift_per_day_ticks": float(dm.mean() * TICKS_PER_DAY),
            "frac_zero_change": float((dm == 0).mean()),
            "frac_positive_change": float((dm > 0).mean()),
            "frac_negative_change": float((dm < 0).mean()),
            "max_up_move": float(dm.max()),
            "max_down_move": float(dm.min()),
        })
    fv_dyn_df = pd.DataFrame(fv_dyn_rows)
    write_table(fv_dyn_df, out.tables / "velvetfruit_fv_dynamics.csv")

    # --- NEW: Trade arrival time summary ---
    arrival_rows: list[dict] = []
    for day, group in vt.groupby("day"):
        ia = group.sort_values("timestamp")["interarrival"].dropna()
        arrival_rows.append({
            "day": day,
            "trade_count": len(group),
            "mean_interarrival_ticks": float(ia.mean()),
            "median_interarrival_ticks": float(ia.median()),
            "std_interarrival_ticks": float(ia.std()),
            "trades_per_1000_ticks": trades_per_1000_timestamps(len(group), len(vev[vev["day"] == day])),
        })
    arrival_df = pd.DataFrame(arrival_rows)
    write_table(arrival_df, out.tables / "velvetfruit_trade_arrival.csv")

    # --- NEW: Trade sizes by side ---
    size_rows: list[dict] = []
    for (day, side), group in vt.groupby(["day", "signed_side"]):
        size_rows.append({
            "day": day,
            "side": side,
            "count": len(group),
            "mean_size": float(group["quantity"].mean()),
            "median_size": float(group["quantity"].median()),
            "std_size": float(group["quantity"].std()),
            "min_size": float(group["quantity"].min()),
            "max_size": float(group["quantity"].max()),
        })
    size_df = pd.DataFrame(size_rows)
    write_table(size_df, out.tables / "velvetfruit_trade_sizes_by_side.csv")
    plt.figure(figsize=(10, 4))
    for (day, side), group in vt.groupby(["day", "signed_side"]):
        plt.hist(group["quantity"], bins=15, alpha=0.4, label=f"D{day} {side}")
    plt.title("Velvetfruit Trade Size Distribution By Side And Day")
    plt.xlabel("Quantity")
    plt.legend(fontsize=7, ncol=3)
    save_fig(out.charts / "velvetfruit_trade_size_by_side.png")

    # --- NEW: Maker and taker fill analysis ---
    # Maker perspective: passive order at bid1 gets filled when someone sells aggressively
    # (trade_minus_mid < 0). Passive at ask1 filled when trade_minus_mid > 0.
    maker_rows: list[dict] = []
    for day, group in vt.groupby("day"):
        fills_at_ask = (group["trade_minus_mid"] > 0).sum()   # ask-side maker fill (passive sell lifted)
        fills_at_bid = (group["trade_minus_mid"] < 0).sum()   # bid-side maker fill (passive buy hit)
        fills_at_mid = (group["trade_minus_mid"] == 0).sum()
        total = len(group)
        maker_rows.append({
            "day": day,
            "total_trades": total,
            "fills_at_ask": int(fills_at_ask),
            "fills_at_bid": int(fills_at_bid),
            "fills_at_mid": int(fills_at_mid),
            "pct_at_ask": float(fills_at_ask / total) if total else float("nan"),
            "pct_at_bid": float(fills_at_bid / total) if total else float("nan"),
            "fill_asymmetry_buy_bias": float((fills_at_ask - fills_at_bid) / total) if total else float("nan"),
        })
    maker_df = pd.DataFrame(maker_rows)
    write_table(maker_df, out.tables / "velvetfruit_maker_fills.csv")

    # Taker fill cost analysis: crossing spread always costs (ask-bid) ticks
    # At ask: taker pays ask1, effective cost vs mid = +half_spread
    # At bid: taker receives bid1, effective cost vs mid = -half_spread
    vev_spread_stats = vev["spread"].describe().to_dict()
    taker_df = pd.DataFrame({
        "metric": ["spread_mean", "spread_median", "spread_p95", "taker_buy_cost_mean", "taker_sell_cost_mean",
                   "l1_depth_mean", "typical_buy_size_mean", "depth_to_size_ratio"],
        "value": [
            vev["spread"].mean(),
            vev["spread"].median(),
            vev["spread"].quantile(0.95),
            vev["spread"].mean() / 2,   # taker pays half-spread vs mid on each side
            vev["spread"].mean() / 2,
            vev["bid_volume_1"].mean(),
            vt[vt["signed_side"] == "buy"]["quantity"].mean() if (vt["signed_side"] == "buy").any() else float("nan"),
            vev["bid_volume_1"].mean() / vt[vt["signed_side"] == "buy"]["quantity"].mean() if (vt["signed_side"] == "buy").any() else float("nan"),
        ],
    })
    write_table(taker_df, out.tables / "velvetfruit_taker_fills.csv")

    # --- NEW: Intraday trade frequency and direction pattern ---
    N_BINS = 20
    intraday_trade_rows: list[dict] = []
    for day, group in vt.groupby("day"):
        bins = np.linspace(0, 999_900, N_BINS + 1)
        group = group.copy()
        group["time_bin_idx"] = pd.cut(group["timestamp"], bins=bins, include_lowest=True, labels=False)
        for bin_idx, bgroup in group.groupby("time_bin_idx", observed=False):
            buy_count = (bgroup["signed_side"] == "buy").sum()
            sell_count = (bgroup["signed_side"] == "sell").sum()
            total = len(bgroup)
            intraday_trade_rows.append({
                "day": day, "bin_idx": int(bin_idx),
                "total_trades": int(total),
                "buy_trades": int(buy_count),
                "sell_trades": int(sell_count),
                "buy_fraction": float(buy_count / total) if total else float("nan"),
            })
    intraday_trade_df = pd.DataFrame(intraday_trade_rows)
    write_table(intraday_trade_df, out.tables / "velvetfruit_intraday_trade_direction.csv")
    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    for day, group in intraday_trade_df.groupby("day"):
        axes[0].plot(group["bin_idx"], group["total_trades"], marker="o", label=f"Day {day}")
        axes[1].plot(group["bin_idx"], group["buy_fraction"], marker="o", label=f"Day {day}")
    axes[0].set_title("Velvetfruit: Trades Per Time Bin (Early → Late)")
    axes[0].set_ylabel("Trade count")
    axes[0].legend()
    axes[1].axhline(0.5, ls="--", color="black", alpha=0.5)
    axes[1].set_title("Buy Fraction Per Time Bin (above 0.5 = buy-heavy)")
    axes[1].set_ylabel("Fraction buys")
    axes[1].set_xlabel("Time bin (early → late)")
    axes[1].legend()
    save_fig(out.charts / "velvetfruit_intraday_trade_direction.png")

    return {
        "regime": regime,
        "imbalance_coef": beta,
        "imbalance_r2": r2,
        "trade_mid_fraction": trade_mid_frac,
        "trade_wide_fraction": wide_frac,
        "toxicity": toxicity,
        "rv": rv,
        "vev": vev,
    }


def section3_vouchers(prices: pd.DataFrame, trades: pd.DataFrame, vev: pd.DataFrame, out: OutputPaths) -> dict[str, pd.DataFrame]:
    rows = []
    money_rows = []
    merged_spot = vev[["day", "timestamp", "mid_price"]].rename(columns={"mid_price": "spot_mid"})

    plt.figure(figsize=(14, 6))
    for product in VOUCHERS:
        v = add_global_tick(series_for(prices, product))
        plt.plot(v["global_tick"], v["mid_price"] / v["mid_price"].median(), lw=0.7, label=product)
    plt.title("Normalized Voucher Mid Complex")
    plt.ylabel("Mid / median mid")
    plt.legend(ncol=3, fontsize=7)
    save_fig(out.charts / "voucher_mid_summary_grid_normalized.png")

    for product in VOUCHERS:
        v = add_global_tick(series_for(prices, product))
        vt = trades[trades["symbol"] == product]
        plt.figure(figsize=(12, 4))
        plt.plot(v["global_tick"], v["mid_price"], lw=0.8)
        for day in [1, 2]:
            plt.axvline(day * 1_000_000, color="black", ls="--", alpha=0.4)
        plt.title(f"{product} Mid Price Across Historical Days")
        save_fig(out.charts / f"voucher_mid_{product}.png")

        v["spread"] = v["ask_price_1"] - v["bid_price_1"]
        plt.figure(figsize=(8, 4))
        plt.hist(v["spread"].dropna(), bins=50)
        plt.title(f"{product} Spread Distribution")
        save_fig(out.charts / f"voucher_spread_{product}.png")

        m = v.merge(merged_spot, on=["day", "timestamp"], how="left")
        k = strike(product)
        m["log_moneyness"] = np.log(m["spot_mid"] / k)
        m["moneyness_class"] = np.where(m["log_moneyness"] > 0.03, "ITM", np.where(m["log_moneyness"] < -0.03, "OTM", "near-ATM"))
        for day, group in m.groupby("day"):
            dominant = group["moneyness_class"].mode().iloc[0]
            money_rows.append(
                {
                    "voucher": product,
                    "strike": k,
                    "day": day,
                    "mean_log_moneyness": group["log_moneyness"].mean(),
                    "dominant_class": dominant,
                    "itm_fraction": (group["moneyness_class"] == "ITM").mean(),
                    "atm_fraction": (group["moneyness_class"] == "near-ATM").mean(),
                    "otm_fraction": (group["moneyness_class"] == "OTM").mean(),
                }
            )

        for day, group in v.groupby("day"):
            t = vt[vt["day"] == day]
            rows.append(
                {
                    "voucher": product,
                    "strike": k,
                    "day": day,
                    "mean_spread": group["spread"].mean(),
                    "median_spread": group["spread"].median(),
                    "p95_spread": group["spread"].quantile(0.95),
                    "mean_mid": group["mid_price"].mean(),
                    "spread_pct_mid": group["spread"].mean() / group["mid_price"].mean() if group["mid_price"].mean() else np.nan,
                    "trades_per_1000_timestamps": trades_per_1000_timestamps(len(t), len(group)),
                    "mean_top_depth": group[["bid_volume_1", "ask_volume_1"]].mean(axis=1).mean(),
                    "fraction_timestamps_with_no_trades": 1.0 - group["timestamp"].isin(t["timestamp"]).mean(),
                }
            )

    liquidity = pd.DataFrame(rows)
    moneyness = pd.DataFrame(money_rows)
    write_table(liquidity, out.tables / "voucher_liquidity.csv")
    write_table(moneyness, out.tables / "voucher_moneyness.csv")
    return {"liquidity": liquidity, "moneyness": moneyness}


def section4_options(prices: pd.DataFrame, vev_ctx: dict[str, Any], out: OutputPaths) -> dict[str, pd.DataFrame]:
    vev = vev_ctx["vev"]
    rv = vev_ctx["rv"]
    spot = vev[["day", "timestamp", "global_tick", "mid_price"]].rename(columns={"mid_price": "spot_mid"})
    iv_rows = []
    option_rows = []

    for product in VOUCHERS:
        v = add_global_tick(series_for(prices, product)).merge(spot, on=["day", "timestamp", "global_tick"], how="left")
        k = strike(product)
        for _, row in v.iterrows():
            t_years = TTE_DAYS[int(row["day"])] / 365.0
            iv, reason = implied_vol(float(row["spot_mid"]), k, t_years, float(row["mid_price"]))
            iv_rows.append(
                {
                    "day": int(row["day"]),
                    "timestamp": int(row["timestamp"]),
                    "global_tick": int(row["global_tick"]),
                    "voucher": product,
                    "strike": k,
                    "spot_mid": row["spot_mid"],
                    "option_mid": row["mid_price"],
                    "tte_days": TTE_DAYS[int(row["day"])],
                    "iv": iv,
                    "iv_status": reason,
                }
            )
    iv_df = pd.DataFrame(iv_rows)
    write_table(iv_df, out.tables / "voucher_iv_timeseries.csv")

    for day in [0, 1, 2]:
        day_iv = iv_df[iv_df["day"] == day]
        sample_ts = np.linspace(day_iv["timestamp"].min(), day_iv["timestamp"].max(), 5).round(-2).astype(int)
        plt.figure(figsize=(10, 5))
        for ts in sample_ts:
            snap = day_iv[day_iv["timestamp"] == ts].sort_values("strike")
            plt.plot(snap["strike"], snap["iv"], marker="o", label=f"t={ts}")
        plt.title(f"IV Smile Snapshots - Day {day}, TTE {TTE_DAYS[day]}d")
        plt.xlabel("Strike")
        plt.ylabel("Implied volatility")
        plt.legend(fontsize=8)
        save_fig(out.charts / f"voucher_iv_smile_day{day}.png")

    atm_rows = []
    for (day, timestamp), group in iv_df.groupby(["day", "timestamp"]):
        good = group.dropna(subset=["iv"]).copy()
        if good.empty:
            continue
        good["distance"] = (good["strike"] - good["spot_mid"]).abs()
        atm_rows.append(good.sort_values("distance").iloc[0].to_dict())
    atm_iv = pd.DataFrame(atm_rows)
    write_table(atm_iv, out.tables / "voucher_atm_iv_timeseries.csv")

    fig, ax = plt.subplots(figsize=(12, 4))
    plot_by_day(ax, atm_iv, "iv", lw=0.8)
    add_day_dividers(ax)
    ax.set_title("ATM IV Term Structure Proxy by Historical Day (nearest-strike proxy)")
    ax.set_ylabel("ATM IV")
    save_fig(out.charts / "voucher_atm_iv_across_days.png")

    fig, ax = plt.subplots(figsize=(14, 5))
    for product in VOUCHERS:
        p = iv_df[iv_df["voucher"] == product]
        plot_by_day(ax, p, "iv", label=product, lw=0.7)
    add_day_dividers(ax)
    ax.set_title("Per-Voucher IV Over Time by Historical Day")
    ax.set_ylabel("IV")
    ax.legend(ncol=3, fontsize=7)
    save_fig(out.charts / "voucher_iv_timeseries_all.png")

    rv_compare = atm_iv.merge(rv[["day", "timestamp", "rv_500"]], on=["day", "timestamp"], how="left")
    rv_compare["iv_minus_rv"] = rv_compare["iv"] - rv_compare["rv_500"]
    write_table(rv_compare, out.tables / "voucher_iv_vs_rv.csv")
    gap = rv_compare.groupby("day")["iv_minus_rv"].mean().reset_index(name="mean_iv_minus_rv")
    write_table(gap, out.tables / "voucher_iv_rv_gap.csv")

    fig, ax = plt.subplots(figsize=(13, 5))
    plot_by_day(ax, rv_compare, "iv", label="ATM IV", lw=1)
    plot_by_day(ax, rv_compare, "rv_500", label="Velvetfruit RV 500", lw=1, alpha=0.8)
    add_day_dividers(ax)
    ax.set_title("Critical Chart: ATM IV vs Velvetfruit Rolling Realized Vol (500-tick RV warmup)")
    ax.set_ylabel("Annualized vol")
    ax.legend()
    save_fig(out.charts / "critical_atm_iv_vs_velvetfruit_rv.png")

    noarb_rows = []
    price_wide = pd.concat([add_global_tick(series_for(prices, p)).assign(strike=strike(p), voucher=p) for p in VOUCHERS])
    price_wide = price_wide.merge(spot, on=["day", "timestamp", "global_tick"], how="left")
    for product, group in price_wide.groupby("voucher"):
        k = strike(product)
        lower_mag = (np.maximum(group["spot_mid"] - k, 0.0) - group["mid_price"]).clip(lower=0)
        upper_mag = (group["mid_price"] - group["spot_mid"]).clip(lower=0)
        for day in [0, 1, 2]:
            gd = group[group["day"] == day]
            lm = lower_mag.loc[gd.index]
            um = upper_mag.loc[gd.index]
            noarb_rows.append({"day": day, "voucher": product, "violation_type": "lower_bound", "count": int((lm > 0).sum()), "avg_magnitude": lm[lm > 0].mean(), "avg_lifetime_ticks": avg_lifetime(lm > 0)})
            noarb_rows.append({"day": day, "voucher": product, "violation_type": "upper_bound", "count": int((um > 0).sum()), "avg_magnitude": um[um > 0].mean(), "avg_lifetime_ticks": avg_lifetime(um > 0)})

    for (day, timestamp), group in price_wide.groupby(["day", "timestamp"]):
        g = group.sort_values("strike")
        mids = g["mid_price"].to_numpy()
        strikes = g["strike"].to_numpy()
        vouchers = g["voucher"].to_numpy()
        mono = mids[1:] - mids[:-1]
        for i, mag in enumerate(mono):
            if mag > 0:
                noarb_rows.append({"day": day, "voucher": f"{vouchers[i]}->{vouchers[i+1]}", "violation_type": "monotonicity", "count": 1, "avg_magnitude": mag, "avg_lifetime_ticks": 1})
        slope_left = (mids[1:-1] - mids[:-2]) / (strikes[1:-1] - strikes[:-2])
        slope_right = (mids[2:] - mids[1:-1]) / (strikes[2:] - strikes[1:-1])
        convexity_mags = slope_left - slope_right
        for i, mag in enumerate(convexity_mags):
            if mag > 0:
                noarb_rows.append({"day": day, "voucher": f"{vouchers[i]},{vouchers[i+1]},{vouchers[i+2]}", "violation_type": "convexity_slope", "count": 1, "avg_magnitude": mag, "avg_lifetime_ticks": 1})

    noarb = pd.DataFrame(noarb_rows)
    noarb_summary = noarb.groupby(["day", "voucher", "violation_type"], as_index=False).agg(
        count=("count", "sum"),
        avg_magnitude=("avg_magnitude", "mean"),
        avg_lifetime_ticks=("avg_lifetime_ticks", "mean"),
    )
    write_table(noarb_summary, out.tables / "noarb_violations.csv")

    greek_rows = []
    for product in VOUCHERS:
        p = iv_df[(iv_df["voucher"] == product) & iv_df["iv"].notna()]
        if p.empty:
            continue
        for label, sample in [
            ("low_moneyness", p.iloc[(np.log(p["spot_mid"] / p["strike"]) - p["strike"].map(lambda _: -0.05)).abs().argsort()[:1]]),
            ("median", p.iloc[[len(p) // 2]]),
            ("high_moneyness", p.iloc[(np.log(p["spot_mid"] / p["strike"]) - 0.05).abs().argsort()[:1]]),
        ]:
            row = sample.iloc[0]
            greeks = bs_greeks(row["spot_mid"], row["strike"], row["tte_days"] / 365.0, row["iv"])
            greek_rows.append({"voucher": product, "strike": row["strike"], "sample": label, **greeks})
    greeks = pd.DataFrame(greek_rows)
    write_table(greeks, out.tables / "voucher_greeks.csv")

    median_greeks = greeks[greeks["sample"] == "median"].sort_values("strike")
    plt.figure(figsize=(10, 4))
    plt.plot(median_greeks["strike"], median_greeks["delta"], marker="o")
    plt.title("Median-Sample BS Delta vs Strike")
    save_fig(out.charts / "voucher_delta_vs_strike.png")
    plt.figure(figsize=(10, 4))
    plt.plot(median_greeks["strike"], median_greeks["gamma"], marker="o")
    plt.title("Median-Sample BS Gamma vs Strike")
    save_fig(out.charts / "voucher_gamma_vs_strike.png")

    # --- NEW: Intrinsic and extrinsic value decomposition ---
    iv_df = iv_df.copy()
    iv_df["intrinsic"] = np.maximum(iv_df["spot_mid"] - iv_df["strike"].astype(float), 0.0)
    iv_df["extrinsic"] = (iv_df["option_mid"] - iv_df["intrinsic"]).clip(lower=0)
    ie_summary = iv_df.groupby(["voucher", "day"])[["option_mid", "intrinsic", "extrinsic"]].mean().reset_index()
    write_table(ie_summary, out.tables / "voucher_intrinsic_extrinsic.csv")
    active_v = [v for v in ["VEV_4000", "VEV_5300", "VEV_5400", "VEV_5500"] if v in iv_df["voucher"].unique()]
    if active_v:
        fig, axes = plt.subplots(len(active_v), 1, figsize=(14, 3 * len(active_v)))
        if len(active_v) == 1:
            axes = [axes]
        for ax, product in zip(axes, active_v):
            sub = iv_df[iv_df["voucher"] == product].sort_values("global_tick")
            ax.fill_between(sub["global_tick"], 0, sub["intrinsic"], label="Intrinsic", alpha=0.75)
            ax.fill_between(sub["global_tick"], sub["intrinsic"], sub["option_mid"], label="Extrinsic", alpha=0.75)
            ax.set_title(f"{product} Intrinsic vs Extrinsic")
            ax.legend(fontsize=7)
        plt.tight_layout()
        save_fig(out.charts / "voucher_intrinsic_extrinsic.png")

    # --- NEW: EMA bands on ATM IV (±1σ, ±2σ) ---
    atm_sorted = atm_iv.sort_values("global_tick").copy()
    span = 2000
    atm_sorted["iv_ema"] = atm_sorted.groupby("day")["iv"].transform(lambda s: s.ewm(span=span, min_periods=100).mean())
    atm_sorted["iv_ema_std"] = atm_sorted.groupby("day")["iv"].transform(lambda s: s.ewm(span=span, min_periods=100).std())
    write_table(atm_sorted[["global_tick", "day", "timestamp", "voucher", "strike", "iv", "iv_ema", "iv_ema_std"]], out.tables / "voucher_atm_iv_ema.csv")
    fig, ax = plt.subplots(figsize=(14, 5))
    for day, group in atm_sorted.groupby("day"):
        g = group.sort_values("timestamp")
        ax.plot(g["global_tick"], g["iv"], lw=0.8, alpha=0.6, label="ATM IV" if day == 0 else None)
        ax.plot(g["global_tick"], g["iv_ema"], lw=1.5, label=f"EMA (span={span})" if day == 0 else None)
        for mult, a in [(1, 0.3), (2, 0.15)]:
            ax.fill_between(
                g["global_tick"].to_numpy(),
                (g["iv_ema"] - mult * g["iv_ema_std"]).to_numpy(),
                (g["iv_ema"] + mult * g["iv_ema_std"]).to_numpy(),
                alpha=a,
                label=f"±{mult}σ band" if day == 0 else None,
            )
    add_day_dividers(ax)
    ax.set_title("ATM IV with Per-Day EMA Bands (±1σ, ±2σ)")
    ax.set_ylabel("Implied Volatility")
    ax.legend()
    save_fig(out.charts / "voucher_atm_iv_ema_bands.png")

    # --- NEW: IV term structure (IV vs TTE at early/mid/late snapshots) ---
    term_rows: list[dict] = []
    for ts_frac, label in [(0.1, "early"), (0.5, "mid"), (0.9, "late")]:
        for product in VOUCHERS:
            p_iv = iv_df[iv_df["voucher"] == product]
            for day in [0, 1, 2]:
                day_p = p_iv[(p_iv["day"] == day)].dropna(subset=["iv"])
                if day_p.empty:
                    continue
                ts = int(day_p["timestamp"].quantile(ts_frac))
                closest = day_p.iloc[(day_p["timestamp"] - ts).abs().argsort()[:1]]
                if closest.empty:
                    continue
                term_rows.append({"session": label, "voucher": product, "strike": strike(product), "day": day, "tte_days": TTE_DAYS[day], "iv": float(closest.iloc[0]["iv"])})
    if term_rows:
        term_df = pd.DataFrame(term_rows)
        write_table(term_df, out.tables / "voucher_iv_term_structure.csv")
        sessions = term_df["session"].unique()
        fig, axes = plt.subplots(1, len(sessions), figsize=(6 * len(sessions), 5))
        if len(sessions) == 1:
            axes = [axes]
        for ax, session in zip(axes, sessions):
            sub = term_df[term_df["session"] == session]
            for prod, g in sub.groupby("voucher"):
                ax.plot(g["tte_days"], g["iv"], marker="o", ms=4, label=prod)
            ax.set_title(f"IV Term Structure ({session})")
            ax.set_xlabel("TTE (days)")
            ax.set_ylabel("IV")
            ax.legend(fontsize=6, ncol=2)
        save_fig(out.charts / "voucher_iv_term_structure.png")

    # --- NEW: IV smile smoothness (R² of quadratic fit per snapshot) ---
    smooth_rows: list[dict] = []
    for (day, timestamp), group in iv_df.groupby(["day", "timestamp"]):
        good = group.dropna(subset=["iv"])
        if len(good) < 4:
            continue
        try:
            strikes_f = good["strike"].astype(float).values
            ivs_f = good["iv"].values
            coeffs = np.polyfit(strikes_f, ivs_f, 2)
            pred = np.polyval(coeffs, strikes_f)
            ss_res = ((ivs_f - pred) ** 2).sum()
            ss_tot = ((ivs_f - ivs_f.mean()) ** 2).sum()
            smooth_rows.append({"day": int(day), "timestamp": int(timestamp), "smile_r2": float(1.0 - ss_res / ss_tot) if ss_tot > 1e-12 else float("nan")})
        except Exception:
            pass
    if smooth_rows:
        smooth_df = pd.DataFrame(smooth_rows)
        write_table(smooth_df, out.tables / "voucher_iv_smile_smoothness.csv")
        plt.figure(figsize=(12, 4))
        for day, g in smooth_df.groupby("day"):
            plt.plot(g["timestamp"] + day * 1_000_000, g["smile_r2"], lw=0.8, label=f"Day {day}")
        plt.title("IV Smile Smoothness (R² of Quadratic Fit) — 1.0 = perfectly smooth parabola")
        plt.ylabel("R²")
        plt.legend()
        save_fig(out.charts / "voucher_iv_smile_smoothness.png")

    # --- NEW: Delta and gamma timeseries per voucher (vectorized) ---
    greeks_ts_frames: list[pd.DataFrame] = []
    for product in VOUCHERS:
        p = iv_df[iv_df["voucher"] == product].copy().sort_values(["day", "timestamp"])
        k = strike(product)
        for day in [0, 1, 2]:
            pd_day = p[p["day"] == day].copy()
            if pd_day.empty:
                continue
            t_years = TTE_DAYS[day] / 365.0
            delta_arr, gamma_arr, vega_arr = bs_greeks_vec(
                pd_day["spot_mid"].values.astype(float), k, t_years, pd_day["iv"].values.astype(float)
            )
            pd_day["bs_delta"] = delta_arr
            pd_day["bs_gamma"] = gamma_arr
            pd_day["bs_vega"] = vega_arr
            greeks_ts_frames.append(pd_day[["day", "timestamp", "global_tick", "voucher", "strike", "spot_mid", "iv", "bs_delta", "bs_gamma", "bs_vega"]])
    greeks_ts = pd.concat(greeks_ts_frames, ignore_index=True) if greeks_ts_frames else pd.DataFrame()
    if not greeks_ts.empty:
        write_table(greeks_ts, out.tables / "voucher_greeks_timeseries.csv")
        fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
        for product in VOUCHERS:
            sub = greeks_ts[greeks_ts["voucher"] == product]
            axes[0].plot(sub["global_tick"], sub["bs_delta"], lw=0.7, label=product)
            axes[1].plot(sub["global_tick"], sub["bs_gamma"], lw=0.7, label=product)
        axes[0].set_title("BS Delta Over Time by Voucher")
        axes[0].set_ylabel("Delta")
        axes[0].legend(ncol=3, fontsize=7)
        axes[1].set_title("BS Gamma Over Time by Voucher")
        axes[1].set_ylabel("Gamma")
        axes[1].set_xlabel("Global tick")
        save_fig(out.charts / "voucher_delta_gamma_timeseries.png")

    # --- NEW: Gamma scalping P&L proxy (dS² accumulation minus theta cost) ---
    if not greeks_ts.empty:
        gamma_scalp_rows: list[dict] = []
        for product in VOUCHERS:
            sub = greeks_ts[greeks_ts["voucher"] == product].sort_values(["day", "timestamp"])
            if sub.empty:
                continue
            ds = sub["spot_mid"].diff().fillna(0).values
            gamma_v = sub["bs_gamma"].values
            theta_v = np.array([
                bs_greeks(row["spot_mid"], row["strike"], TTE_DAYS[int(row["day"])] / 365.0, row["iv"])["theta"]
                if pd.notna(row["iv"]) and row["iv"] > 0 else 0.0
                for _, row in sub.iterrows()
            ])
            pnl_gamma = 0.5 * gamma_v * ds**2
            # theta from bs_greeks is per calendar day; scale to per-tick
            pnl_theta = theta_v / TICKS_PER_DAY
            cumulative_pnl = np.nancumsum(pnl_gamma + pnl_theta)
            gamma_scalp_rows.append({
                "voucher": product,
                "total_gamma_pnl": float(np.nansum(pnl_gamma)),
                "total_theta_cost": float(np.nansum(pnl_theta)),
                "net_gamma_scalp_pnl": float(np.nansum(pnl_gamma + pnl_theta)),
            })
            plt.figure(figsize=(12, 4))
            plt.plot(sub["global_tick"].values, cumulative_pnl, lw=0.8)
            plt.title(f"{product} Cumulative Gamma Scalp P&L Proxy (delta-neutral)")
            plt.ylabel("Cumulative P&L (per 1 option unit)")
            plt.xlabel("Global tick")
            save_fig(out.charts / f"gamma_scalp_pnl_{product}.png")
        gamma_scalp_df = pd.DataFrame(gamma_scalp_rows)
        write_table(gamma_scalp_df, out.tables / "voucher_gamma_scalp_proxy.csv")
        plt.figure(figsize=(10, 4))
        plt.bar(gamma_scalp_df["voucher"], gamma_scalp_df["net_gamma_scalp_pnl"])
        plt.xticks(rotation=30)
        plt.title("Gamma Scalp Net P&L Proxy by Voucher (3-day total)")
        plt.ylabel("Net P&L")
        save_fig(out.charts / "gamma_scalp_pnl_summary.png")

    return {"iv": iv_df, "atm_iv": atm_iv, "iv_rv": rv_compare, "noarb": noarb_summary, "greeks": greeks, "greeks_ts": greeks_ts}


def avg_lifetime(mask: pd.Series) -> float:
    vals = mask.fillna(False).to_numpy(dtype=bool)
    runs = []
    current = 0
    for val in vals:
        if val:
            current += 1
        elif current:
            runs.append(current)
            current = 0
    if current:
        runs.append(current)
    return float(np.mean(runs)) if runs else 0.0


def section5_leadlag(prices: pd.DataFrame, options_ctx: dict[str, pd.DataFrame], out: OutputPaths) -> pd.DataFrame:
    iv_df = options_ctx["iv"]
    spot = add_global_tick(series_for(prices, UNDERLYING))[["day", "timestamp", "global_tick", "mid_price"]].rename(columns={"mid_price": "spot_mid"})
    lead_rows = []
    for product in VOUCHERS:
        v = add_global_tick(series_for(prices, product)).merge(spot, on=["day", "timestamp", "global_tick"], how="left")
        v = v.merge(iv_df[["day", "timestamp", "voucher", "iv"]], left_on=["day", "timestamp", "product"], right_on=["day", "timestamp", "voucher"], how="left")
        v["vev_ret"] = v.groupby("day")["spot_mid"].transform(lambda x: log_returns(x, 1))
        v["voucher_ret"] = v.groupby("day")["mid_price"].transform(lambda x: log_returns(x.clip(lower=1e-9), 1))
        corrs = []
        for lag in range(-10, 11):
            if lag < 0:
                aligned = pd.DataFrame({"voucher": v["voucher_ret"], "vev": v["vev_ret"].shift(-lag)}).dropna()
            else:
                aligned = pd.DataFrame({"voucher": v["voucher_ret"].shift(lag), "vev": v["vev_ret"]}).dropna()
            if len(aligned) > 5 and aligned["voucher"].std() > 0 and aligned["vev"].std() > 0:
                corr = aligned["voucher"].corr(aligned["vev"])
            else:
                corr = np.nan
            corrs.append({"lag": lag, "correlation": corr})
        cdf = pd.DataFrame(corrs)
        best = cdf.iloc[cdf["correlation"].abs().idxmax()] if cdf["correlation"].notna().any() else {"lag": np.nan, "correlation": np.nan}
        plt.figure(figsize=(8, 4))
        plt.plot(cdf["lag"], cdf["correlation"], marker="o")
        plt.axvline(0, color="black", lw=0.8)
        plt.title(f"Lead-Lag Correlation: {product} vs Velvetfruit")
        plt.xlabel("Lag")
        plt.ylabel("Correlation")
        save_fig(out.charts / f"leadlag_corr_{product}.png")

        deltas = []
        for _, row in v.iterrows():
            greeks = bs_greeks(row["spot_mid"], strike(product), TTE_DAYS[int(row["day"])] / 365.0, row["iv"]) if pd.notna(row["iv"]) else {"delta": np.nan}
            deltas.append(greeks["delta"])
        v["delta"] = deltas
        v["delta_hedged_ret"] = v["voucher_ret"] - v["delta"] * v["vev_ret"]
        residual = v["delta_hedged_ret"].dropna()
        plt.figure(figsize=(12, 4))
        plt.plot(v["global_tick"], v["delta_hedged_ret"], lw=0.7)
        plt.title(f"Delta-Hedged Residual Return Series: {product}")
        save_fig(out.charts / f"delta_hedged_residual_series_{product}.png")
        plt.figure(figsize=(8, 4))
        plt.hist(residual, bins=80)
        plt.title(f"Delta-Hedged Residual Distribution: {product}")
        save_fig(out.charts / f"delta_hedged_residual_hist_{product}.png")
        lead_rows.append(
            {
                "voucher": product,
                "best_lag": best["lag"],
                "peak_correlation": best["correlation"],
                "residual_std": residual.std(),
                "residual_autocorr_lag1": residual.autocorr(1) if len(residual) > 2 else np.nan,
            }
        )
    leadlag = pd.DataFrame(lead_rows)
    write_table(leadlag, out.tables / "voucher_leadlag.csv")
    return leadlag


def section7_bot_patterns(prices: pd.DataFrame, trades: pd.DataFrame, out: OutputPaths) -> dict:
    """Infer bot quoting behavior: rounding, volume consistency, quote change rate, counterparty presence."""
    results: dict = {}

    # Counterparty column presence
    results["buyer_column_ever_populated"] = bool(trades["buyer"].notna().any())
    results["seller_column_ever_populated"] = bool(trades["seller"].notna().any())

    # --- Velvetfruit rounding analysis ---
    ve = series_for(prices, UNDERLYING)
    ve = ve[ve["bid_price_1"].notna() & ve["ask_price_1"].notna()].copy()
    ve["bid_mod_1"] = ve["bid_price_1"] % 1
    ve["ask_mod_1"] = ve["ask_price_1"] % 1
    ve["bid_mod_5"] = ve["bid_price_1"] % 5
    ve["ask_mod_5"] = ve["ask_price_1"] % 5
    rounding_df = pd.DataFrame({
        "field": ["bid1%1", "ask1%1", "bid1%5", "ask1%5"],
        "frac_zero": [
            (ve["bid_mod_1"] < 1e-9).mean(),
            (ve["ask_mod_1"] < 1e-9).mean(),
            (ve["bid_mod_5"] < 1e-9).mean(),
            (ve["ask_mod_5"] < 1e-9).mean(),
        ],
    })
    write_table(rounding_df, out.tables / "velvetfruit_rounding.csv")
    plt.figure(figsize=(10, 4))
    plt.subplot(1, 2, 1)
    plt.hist(ve["bid_mod_5"].dropna(), bins=10, alpha=0.6, label="bid1 mod 5")
    plt.hist(ve["ask_mod_5"].dropna(), bins=10, alpha=0.6, label="ask1 mod 5")
    plt.title("Velvetfruit Quote Price mod 5")
    plt.legend()
    plt.subplot(1, 2, 2)
    plt.hist(ve["bid_mod_1"].dropna(), bins=5, alpha=0.6, label="bid1 mod 1 (integer?)")
    plt.hist(ve["ask_mod_1"].dropna(), bins=5, alpha=0.6, label="ask1 mod 1")
    plt.title("Velvetfruit Quote Price mod 1")
    plt.legend()
    save_fig(out.charts / "velvetfruit_quote_rounding.png")

    # --- Quote change rate (how often does top-of-book shift each tick?) ---
    change_rows: list[dict] = []
    for day, group in ve.groupby("day"):
        group = group.sort_values("timestamp")
        bid_change = (group["bid_price_1"].diff() != 0).mean()
        ask_change = (group["ask_price_1"].diff() != 0).mean()
        change_rows.append({"day": day, "bid_price_change_rate": float(bid_change), "ask_price_change_rate": float(ask_change)})
    change_df = pd.DataFrame(change_rows)
    write_table(change_df, out.tables / "velvetfruit_quote_change_rate.csv")
    results["quote_change_rate"] = change_df

    # --- Bot volume consistency (stable size = likely single bot) ---
    vol_cols = ["bid_volume_1", "ask_volume_1", "bid_volume_2", "ask_volume_2", "bid_volume_3", "ask_volume_3"]
    vol_stats = ve.groupby("day")[vol_cols].agg(["mean", "std", "median"]).reset_index()
    write_table(vol_stats, out.tables / "velvetfruit_bot_volume_stats.csv")
    results["vol_stats"] = vol_stats

    # --- NEW: Order book depth profile (stacked L1/L2/L3 bid and ask) ---
    depth_agg = ve.mean(numeric_only=True)
    levels = [1, 2, 3]
    bid_depths = [depth_agg.get(f"bid_volume_{l}", 0) for l in levels]
    ask_depths = [depth_agg.get(f"ask_volume_{l}", 0) for l in levels]
    fig, axes = plt.subplots(1, 2, figsize=(10, 5))
    bars_b = axes[0].bar(["L1", "L2", "L3"], bid_depths, color=["#2196F3", "#64B5F6", "#BBDEFB"])
    axes[0].set_title("Velvetfruit Mean Bid Depth by Level")
    axes[0].set_ylabel("Mean volume")
    for bar, val in zip(bars_b, bid_depths):
        axes[0].text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.3, f"{val:.1f}", ha="center", fontsize=9)
    bars_a = axes[1].bar(["L1", "L2", "L3"], ask_depths, color=["#F44336", "#EF9A9A", "#FFCDD2"])
    axes[1].set_title("Velvetfruit Mean Ask Depth by Level")
    for bar, val in zip(bars_a, ask_depths):
        axes[1].text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.3, f"{val:.1f}", ha="center", fontsize=9)
    plt.suptitle("Order Book Depth Profile (3-day average)", fontsize=11)
    save_fig(out.charts / "velvetfruit_depth_profile.png")

    # --- NEW: Depth profile summary table ---
    depth_profile_df = pd.DataFrame({
        "level": ["L1", "L2", "L3"],
        "mean_bid_vol": bid_depths,
        "mean_ask_vol": ask_depths,
        "bid_ask_ratio": [b / a if a > 0 else float("nan") for b, a in zip(bid_depths, ask_depths)],
    })
    write_table(depth_profile_df, out.tables / "velvetfruit_depth_profile.csv")
    results["depth_profile"] = depth_profile_df

    # --- Voucher bot analysis (rounding + volume) ---
    voucher_bot_rows: list[dict] = []
    for product in VOUCHERS:
        v = series_for(prices, product)
        v = v[v["bid_price_1"].notna() & v["ask_price_1"].notna()].copy()
        if v.empty:
            continue
        voucher_bot_rows.append({
            "voucher": product,
            "bid1_frac_integer": float((v["bid_price_1"] % 1 < 1e-9).mean()),
            "ask1_frac_integer": float((v["ask_price_1"] % 1 < 1e-9).mean()),
            "bid_vol1_mean": float(v["bid_volume_1"].mean()),
            "bid_vol1_std": float(v["bid_volume_1"].std()),
            "ask_vol1_mean": float(v["ask_volume_1"].mean()),
            "ask_vol1_std": float(v["ask_volume_1"].std()),
            "bid_price_change_rate": float((v["bid_price_1"].diff() != 0).mean()),
        })
    voucher_bot_df = pd.DataFrame(voucher_bot_rows)
    write_table(voucher_bot_df, out.tables / "voucher_bot_analysis.csv")
    results["voucher_bot"] = voucher_bot_df
    results["rounding"] = rounding_df
    return results


def section6_trade_analysis(prices: pd.DataFrame, trades: pd.DataFrame, opt_ctx: dict, out: OutputPaths) -> dict:
    """Voucher trade size analysis and trade price vs BS fair value for each voucher."""
    iv_df = opt_ctx["iv"]
    results: dict = {}

    # --- Voucher trade size summary ---
    size_rows: list[dict] = []
    for product in VOUCHERS:
        vt = trades[trades["symbol"] == product].copy()
        if vt.empty:
            continue
        vt["quantity"] = pd.to_numeric(vt["quantity"], errors="coerce")
        for day in [0, 1, 2]:
            group = vt[vt["day"] == day]
            if group.empty:
                continue
            size_rows.append({
                "voucher": product,
                "strike": strike(product),
                "day": day,
                "trade_count": len(group),
                "mean_size": float(group["quantity"].mean()),
                "median_size": float(group["quantity"].median()),
                "std_size": float(group["quantity"].std()),
                "min_size": float(group["quantity"].min()),
                "max_size": float(group["quantity"].max()),
            })
    size_df = pd.DataFrame(size_rows)
    if not size_df.empty:
        write_table(size_df, out.tables / "voucher_trade_sizes.csv")
        results["voucher_trade_sizes"] = size_df
        active = size_df[size_df["trade_count"] >= 5]
        if not active.empty:
            plt.figure(figsize=(12, 5))
            for product, group in active.groupby("voucher"):
                plt.plot(group["day"], group["mean_size"], marker="o", label=product)
            plt.title("Voucher Mean Trade Size By Day (Active Vouchers Only)")
            plt.xlabel("Day")
            plt.ylabel("Mean quantity")
            plt.legend(ncol=3, fontsize=7)
            save_fig(out.charts / "voucher_trade_size_by_day.png")

    # --- Voucher trade price vs BS fair value ---
    # For each voucher trade: compute BS fair at the nearest book snapshot
    spot_lookup = (
        prices[prices["product"] == UNDERLYING][["day", "timestamp", "mid_price"]]
        .rename(columns={"mid_price": "spot_mid"})
        .copy()
    )
    spot_lookup["spot_mid"] = pd.to_numeric(spot_lookup["spot_mid"], errors="coerce")
    iv_lookup = iv_df[["day", "timestamp", "voucher", "iv"]].copy()

    trade_fv_rows: list[dict] = []
    for product in VOUCHERS:
        vt = trades[(trades["symbol"] == product)].copy()
        if vt.empty:
            continue
        vt["price"] = pd.to_numeric(vt["price"], errors="coerce")
        vt["quantity"] = pd.to_numeric(vt["quantity"], errors="coerce")
        k = strike(product)
        for day in [0, 1, 2]:
            group = vt[vt["day"] == day].copy()
            if group.empty:
                continue
            t_years = TTE_DAYS[day] / 365.0
            # Merge with nearest spot and IV at trade timestamp (exact match on timestamp)
            spot_day = spot_lookup[spot_lookup["day"] == day].set_index("timestamp")["spot_mid"]
            iv_day = iv_lookup[(iv_lookup["day"] == day) & (iv_lookup["voucher"] == product)].set_index("timestamp")["iv"]
            for _, row in group.iterrows():
                ts = int(row["timestamp"])
                s = spot_day.get(ts, float("nan"))
                iv_val = iv_day.get(ts, float("nan"))
                bs_fv = bs_call_price(float(s), k, t_years, float(iv_val)) if np.isfinite([s, iv_val]).all() and iv_val > 0 else float("nan")
                trade_fv_rows.append({
                    "voucher": product,
                    "strike": k,
                    "day": day,
                    "timestamp": ts,
                    "trade_price": float(row["price"]),
                    "quantity": float(row["quantity"]),
                    "spot_mid": float(s),
                    "iv_at_trade": float(iv_val),
                    "bs_fair_value": float(bs_fv),
                    "trade_minus_bs_fv": float(row["price"]) - float(bs_fv),
                })
    if trade_fv_rows:
        tfv_df = pd.DataFrame(trade_fv_rows)
        write_table(tfv_df, out.tables / "voucher_trade_vs_bs_fv.csv")
        results["voucher_trade_vs_fv"] = tfv_df
        # Summary: mean trade price vs BS FV by voucher
        tfv_summary = tfv_df.dropna(subset=["trade_minus_bs_fv"]).groupby("voucher").agg(
            trade_count=("trade_price", "count"),
            mean_trade_minus_fv=("trade_minus_bs_fv", "mean"),
            std_trade_minus_fv=("trade_minus_bs_fv", "std"),
            mean_trade_price=("trade_price", "mean"),
            mean_bs_fv=("bs_fair_value", "mean"),
        ).reset_index()
        write_table(tfv_summary, out.tables / "voucher_trade_vs_bs_fv_summary.csv")
        # Voucher maker/taker fill analysis: trade at bid vs ask
        book_lookup = prices[prices["product"].isin(VOUCHERS)][["day", "timestamp", "product", "bid_price_1", "ask_price_1"]].copy()
        for col in ["bid_price_1", "ask_price_1"]:
            book_lookup[col] = pd.to_numeric(book_lookup[col], errors="coerce")
        tfv_full = tfv_df.merge(
            book_lookup.rename(columns={"product": "voucher"}),
            on=["day", "timestamp", "voucher"],
            how="left",
        )
        tfv_full["at_ask"] = (tfv_full["trade_price"] - tfv_full["ask_price_1"]).abs() < 1e-9
        tfv_full["at_bid"] = (tfv_full["trade_price"] - tfv_full["bid_price_1"]).abs() < 1e-9
        voucher_maker_df = tfv_full.groupby("voucher").agg(
            trade_count=("trade_price", "count"),
            fills_at_ask=("at_ask", "sum"),
            fills_at_bid=("at_bid", "sum"),
            spread_at_trade_mean=("ask_price_1", lambda x: (x - tfv_full.loc[x.index, "bid_price_1"]).mean()),
        ).reset_index()
        voucher_maker_df["pct_at_ask"] = voucher_maker_df["fills_at_ask"] / voucher_maker_df["trade_count"]
        voucher_maker_df["pct_at_bid"] = voucher_maker_df["fills_at_bid"] / voucher_maker_df["trade_count"]
        write_table(voucher_maker_df, out.tables / "voucher_maker_taker_fills.csv")
        results["voucher_maker_taker"] = voucher_maker_df
        # Chart: trade_price - BS FV distribution per active voucher
        active_with_data = tfv_df.dropna(subset=["trade_minus_bs_fv"])
        active_vouchers = active_with_data.groupby("voucher").size()
        active_vouchers = active_vouchers[active_vouchers >= 10].index.tolist()
        if active_vouchers:
            plt.figure(figsize=(12, 4))
            for product in active_vouchers:
                sub = active_with_data[active_with_data["voucher"] == product]
                plt.hist(sub["trade_minus_bs_fv"], bins=20, alpha=0.55, label=product, density=True)
            plt.axvline(0, color="black", lw=1)
            plt.title("Voucher: Trade Price − BS Fair Value (positive = bought above FV)")
            plt.xlabel("Trade price − BS FV")
            plt.legend(fontsize=7)
            save_fig(out.charts / "voucher_trade_vs_bs_fv_hist.png")

    return results


def build_report(
    out: OutputPaths,
    hydrogel_present: bool,
    integrity: pd.DataFrame,
    anomalies: list[str],
    vev_ctx: dict[str, Any],
    voucher_ctx: dict[str, pd.DataFrame],
    opt_ctx: dict[str, pd.DataFrame],
    leadlag: pd.DataFrame,
    bot_ctx: dict | None = None,
) -> None:
    liquidity = voucher_ctx["liquidity"]
    noarb = opt_ctx["noarb"]
    iv_gap = pd.read_csv(out.tables / "voucher_iv_rv_gap.csv")
    return_stats = pd.read_csv(out.tables / "velvetfruit_return_stats.csv")
    spread = pd.read_csv(out.tables / "velvetfruit_spread_summary.csv")
    toxicity = vev_ctx["toxicity"]

    liquid = liquidity.groupby("voucher")["trades_per_1000_timestamps"].mean().sort_values(ascending=False)
    tight = liquidity.groupby("voucher")["spread_pct_mid"].mean().sort_values()
    dead = liquid[liquid < 0.05].index.tolist()
    noarb_top = noarb.groupby("voucher")["count"].sum().sort_values(ascending=False).head(5)
    lag_top = leadlag.reindex(leadlag["peak_correlation"].abs().sort_values(ascending=False).index).head(3)
    iv_gap_mean = iv_gap["mean_iv_minus_rv"].mean()

    report = []
    report.append("# Velvetfruit / VEV Voucher EDA\n")
    report.append(
        "Assumptions: Black-Scholes uses `T_years = TTE_days / 365` and risk-free rate `r = 0`. "
        "Historical TTE mapping is day_0=8 days, day_1=7 days, day_2=6 days. "
        "This analysis is retrospective; the live Round 3 has shorter TTE than any historical day.\n"
    )
    if hydrogel_present:
        report.append("HYDROGEL_PACK appears in the input data and was skipped silently for Velvetfruit/voucher analysis.\n")

    report.append("## Section 1 - Data Integrity And Overview\n")
    report.append("![Trade counts](charts/data_integrity_trade_counts.png)\n")
    report.append("![Depth heatmap](charts/data_integrity_depth_heatmap.png)\n")
    report.append(clean_markdown_table(integrity[["day", "product", "book_snapshots", "trades", "timestamp_grid_gaps", "trades_outside_top_book"]], 15))
    report.append("\n\nAnomaly highlights:\n")
    report.extend([f"- {x}\n" for x in anomalies[:20]] or ["- No major top-level anomalies detected.\n"])
    report.append("\n**Strategy implication:** The data is usable for synchronized book-state replay; same-timestamp trade/book joins are reliable enough to study execution, but top-book outside-range flags should be treated as hidden-depth or crossed-time artifacts rather than free arbitrage by default.\n")

    report.append("\n## Section 2 - Velvetfruit Underlying EDA\n")
    report.append("![Velvetfruit mid](charts/velvetfruit_mid_timeseries.png)\n")
    report.append("![Velvetfruit spread vs RV](charts/velvetfruit_spread_vs_rv.png)\n")
    report.append("![Velvetfruit trade side](charts/velvetfruit_trade_minus_mid_by_side.png)\n")
    report.append(f"Regime classification: **{vev_ctx['regime']}**. ADF/KPSS/Hurst/variance-ratio diagnostics are in `tables/velvetfruit_stationarity.json`.\n\n")
    report.append("Note: the blank starts in rolling spread/RV charts are intentional 500-tick rolling-window warmup inside each historical day, not missing data.\n\n")
    report.append(clean_markdown_table(return_stats, 8))
    report.append("\n\nSpread summary:\n")
    report.append(clean_markdown_table(spread, 5))
    report.append(
        f"\n\nImbalance regression coefficient is `{vev_ctx['imbalance_coef']:.4g}` with R² `{vev_ctx['imbalance_r2']:.4g}`. "
        f"Trades at mid fraction `{vev_ctx['trade_mid_fraction']:.2%}`; away-from-mid fraction `{vev_ctx['trade_wide_fraction']:.2%}`.\n"
    )
    report.append("\nFlow toxicity summary:\n")
    report.append(clean_markdown_table(toxicity, 12))
    report.append("\n\n**Strategy implication:** Velvetfruit is best treated as a maker-first underlying for hedging VEV voucher risk; taker flow only deserves attention when trade direction is followed by consistent forward movement because the book spread is still the primary cost gate.\n")

    report.append("\n## Section 3 - Individual Voucher EDA\n")
    report.append("![Voucher normalized grid](charts/voucher_mid_summary_grid_normalized.png)\n")
    report.append(clean_markdown_table(liquidity.sort_values(["day", "strike"]), 20))
    report.append("\n\n**Strategy implication:** Prefer vouchers with both non-trivial trade count and manageable spread-to-mid. Deep OTM floor-price vouchers can look cheap to sell but their 1-tick spread is enormous relative to value; dead strikes should be quoted passively or skipped.\n")

    report.append("\n## Section 4 - Options Cross-Voucher Analysis\n")
    report.append("![Critical IV vs RV](charts/critical_atm_iv_vs_velvetfruit_rv.png)\n")
    report.append("![IV smile day0](charts/voucher_iv_smile_day0.png)\n")
    report.append("![IV all](charts/voucher_iv_timeseries_all.png)\n")
    report.append("Note: IV/RV charts are segmented by historical day because day boundaries reset TTE and session state. The ATM IV line uses the nearest-strike voucher, so strike switches can create real-looking step changes that are proxy mechanics rather than continuous IV moves. Convexity no-arb checks use strike-spacing-adjusted slopes because the voucher strikes are unevenly spaced.\n\n")
    report.append(f"Mean ATM IV minus RV gap across historical days: **{iv_gap_mean:.2%}**.\n\n")
    report.append("Top no-arbitrage violation groups:\n")
    report.append(clean_markdown_table(noarb_top.reset_index(name="violation_count"), 8))
    report.append("\n\n**Strategy implication:** The IV-vs-RV gap is the main option-complex signal. When IV is below RV, favor owning liquid gamma near ATM; when IV is above RV, sell premium only where spread and tail risk are acceptable. No-arb violations should be filtered for persistence and executable spread before trading.\n")

    report.append("\n## Section 5 - Lead-Lag Between Velvetfruit And VEV Vouchers\n")
    report.append(clean_markdown_table(leadlag, 12))
    report.append("\n\n**Strategy implication:** Vouchers with strong zero/near-zero lag correlation are mostly delta exposure; the useful alpha candidates are the ones with autocorrelated delta-hedged residuals and spreads small enough to enter without donating edge.\n")

    report.append("\n## Section 6 - Strategy Hypotheses\n")
    report.append(f"- Velvetfruit should be traded **maker-first, with selective taker hedges**. The classification is {vev_ctx['regime']} and imbalance has low explanatory power unless filtered.\n")
    report.append(f"- IV sits {'above' if iv_gap_mean > 0 else 'below'} RV on average by about **{abs(iv_gap_mean):.2%}** using the ATM proxy and 500-tick annualized RV.\n")
    report.append(f"- Most tradeable vouchers by prints are: {', '.join(liquid.head(4).index)}. Effectively dead or near-dead vouchers by trade rate are: {', '.join(dead) if dead else 'none by the chosen cutoff'}.\n")
    report.append(f"- Lowest spread-to-mid vouchers are: {', '.join(tight.head(4).index)}. Highest spread-to-mid vouchers are: {', '.join(tight.tail(3).index)}.\n")
    report.append(f"- No-arb issues most often involve: {', '.join(noarb_top.index.astype(str))}. Treat these as candidates only if they persist longer than one timestamp and beat bid/ask costs.\n")
    report.append(f"- Strongest lead-lag/residual candidates are: {', '.join(lag_top['voucher'].astype(str))}. The lag is exploitable only if peak lag is non-zero and spread-to-mid is small enough.\n")
    report.append("- Surprise to investigate before strategy: floor-price OTM vouchers can dominate apparent no-arb/IV signals because 0/1 quotes create huge percentage spreads and unstable implied vol.\n")

    # --- New Section 7: FV dynamics, trade analysis, bot patterns, book structure ---
    report.append("\n## Section 7 - Fair Value Dynamics, Trade Analysis, Bot Patterns, Book Structure\n")
    report.append("![ACF](charts/velvetfruit_acf.png)\n")
    report.append("![Intraday trade direction](charts/velvetfruit_intraday_trade_direction.png)\n")
    report.append("![Trade size by side](charts/velvetfruit_trade_size_by_side.png)\n")
    report.append("![Quote offset from mid](charts/velvetfruit_quote_offset_from_mid.png)\n")
    report.append("![Intraday spread](charts/velvetfruit_intraday_spread.png)\n")
    report.append("![Quote rounding](charts/velvetfruit_quote_rounding.png)\n")
    report.append("![Depth profile](charts/velvetfruit_depth_profile.png)\n")

    fv_dyn_path = out.tables / "velvetfruit_fv_dynamics.csv"
    if fv_dyn_path.exists():
        fvd = pd.read_csv(fv_dyn_path)
        report.append("\nFair value dynamics (tick-level price changes):\n")
        report.append(clean_markdown_table(fvd, 5))

    arr_path = out.tables / "velvetfruit_trade_arrival.csv"
    if arr_path.exists():
        arr = pd.read_csv(arr_path)
        report.append("\n\nTrade arrival summary:\n")
        report.append(clean_markdown_table(arr, 5))

    sz_path = out.tables / "velvetfruit_trade_sizes_by_side.csv"
    if sz_path.exists():
        szd = pd.read_csv(sz_path)
        report.append("\n\nTrade sizes by side:\n")
        report.append(clean_markdown_table(szd[["day", "side", "count", "mean_size", "median_size", "std_size", "max_size"]], 10))

    mk_path = out.tables / "velvetfruit_maker_fills.csv"
    if mk_path.exists():
        mk = pd.read_csv(mk_path)
        report.append("\n\nMaker fill breakdown (underlying): trades at ask = passive sell filled; trades at bid = passive buy filled:\n")
        report.append(clean_markdown_table(mk, 5))

    tk_path = out.tables / "velvetfruit_taker_fills.csv"
    if tk_path.exists():
        tk = pd.read_csv(tk_path)
        report.append("\n\nTaker fill cost (underlying): cost of crossing the spread:\n")
        report.append(clean_markdown_table(tk, 10))

    dp_path = out.tables / "velvetfruit_depth_profile.csv"
    if dp_path.exists():
        dp = pd.read_csv(dp_path)
        report.append("\n\nOrder book depth profile (L1/L2/L3):\n")
        report.append(clean_markdown_table(dp, 5))

    acf_path = out.tables / "velvetfruit_acf.csv"
    if acf_path.exists():
        acf_tbl = pd.read_csv(acf_path)
        report.append("\nVelvetfruit ACF (lags 1–5):\n")
        report.append(clean_markdown_table(acf_tbl.head(5), 5))

    clustering_path = out.tables / "velvetfruit_trade_clustering.csv"
    if clustering_path.exists():
        clust = pd.read_csv(clustering_path)
        report.append("\n\nTrade clustering (Poisson dispersion index — >1.5 = clustered, <0.7 = underdispersed):\n")
        report.append(clean_markdown_table(clust, 5))

    rounding_path = out.tables / "velvetfruit_rounding.csv"
    if rounding_path.exists():
        rnd = pd.read_csv(rounding_path)
        report.append("\n\nVelvetfruit quote price rounding fractions:\n")
        report.append(clean_markdown_table(rnd, 6))

    qoff_path = out.tables / "velvetfruit_quote_offset_summary.csv"
    if qoff_path.exists():
        qoff = pd.read_csv(qoff_path)
        report.append("\n\nVelvetfruit quote offset from mid (bid1/ask1 − mid):\n")
        report.append(clean_markdown_table(qoff, 6))

    vbot_path = out.tables / "voucher_bot_analysis.csv"
    if vbot_path.exists():
        vbot = pd.read_csv(vbot_path)
        report.append("\n\nVoucher bot volume and rounding summary:\n")
        report.append(clean_markdown_table(vbot, 12))

    if bot_ctx is not None:
        report.append(f"\n\nCounterparty columns populated: buyer={bot_ctx.get('buyer_column_ever_populated')}, seller={bot_ctx.get('seller_column_ever_populated')}.\n")

    report.append("\n**Strategy implication:** Stable posted volume and consistent price rounding reveal single-bot behaviour; use offset distribution to set competitive passive quote placement. Clustered trade arrival means burst periods carry more fill risk — widen or cap size during high-activity windows.\n")

    # --- New Section 8: Voucher trade analysis ---
    report.append("\n## Section 8 - Voucher Trade Analysis (Sizes, Prices vs Fair Value, Maker/Taker)\n")
    vtsz_path = out.tables / "voucher_trade_sizes.csv"
    if vtsz_path.exists():
        vtsz = pd.read_csv(vtsz_path)
        report.append("\nVoucher trade sizes (active vouchers):\n")
        report.append(clean_markdown_table(vtsz, 15))

    vtfv_path = out.tables / "voucher_trade_vs_bs_fv_summary.csv"
    if vtfv_path.exists():
        vtfv = pd.read_csv(vtfv_path)
        report.append("\n\nVoucher trade price vs BS fair value (mean deviation per voucher; positive = trades above BS FV):\n")
        report.append(clean_markdown_table(vtfv, 12))

    vmtk_path = out.tables / "voucher_maker_taker_fills.csv"
    if vmtk_path.exists():
        vmtk = pd.read_csv(vmtk_path)
        report.append("\n\nVoucher maker/taker fill breakdown:\n")
        report.append(clean_markdown_table(vmtk, 12))

    report.append("\n\n**Strategy implication:** Voucher trades above BS fair value = the market is paying a premium for the option (buy bias). Trades below FV = selling pressure. Use this alongside IV-vs-RV gap to confirm direction before quoting. Fill breakdown tells you which side of the book sees more aggressive flow — bias your passive quotes toward the heavier side.\n")

    # --- New Section 9: Options deep dive summary ---
    report.append("\n## Section 9 - Options Deep Dive Outputs\n")
    report.append("![EMA bands on ATM IV](charts/voucher_atm_iv_ema_bands.png)\n")
    report.append("![Delta/Gamma timeseries](charts/voucher_delta_gamma_timeseries.png)\n")
    report.append("![Intrinsic vs Extrinsic](charts/voucher_intrinsic_extrinsic.png)\n")
    report.append("![IV Term Structure](charts/voucher_iv_term_structure.png)\n")
    report.append("![IV Smile Smoothness](charts/voucher_iv_smile_smoothness.png)\n")
    report.append("![Gamma Scalp P&L](charts/gamma_scalp_pnl_summary.png)\n")

    ie_path = out.tables / "voucher_intrinsic_extrinsic.csv"
    if ie_path.exists():
        ie = pd.read_csv(ie_path)
        report.append("\nIntrinsic vs Extrinsic decomposition (means by voucher/day):\n")
        report.append(clean_markdown_table(ie, 12))

    gs_path = out.tables / "voucher_gamma_scalp_proxy.csv"
    if gs_path.exists():
        gs = pd.read_csv(gs_path)
        report.append("\n\nGamma scalp P&L proxy (per 1 option unit, 3-day cumulative):\n")
        report.append(clean_markdown_table(gs, 12))

    smooth_path = out.tables / "voucher_iv_smile_smoothness.csv"
    if smooth_path.exists():
        sdf = pd.read_csv(smooth_path)
        day_smooth = sdf.groupby("day")["smile_r2"].mean()
        report.append(f"\n\nMean IV smile smoothness (quadratic R²) by day: {day_smooth.to_dict()}. Values near 1.0 = well-behaved parabolic smile; lower values signal distortions.\n")

    report.append("\n**Strategy implication:** Use EMA bands to identify when IV is elevated (sell premium) or depressed (buy gamma). Delta/gamma timeseries directly informs hedge ratios and position sizing. Gamma scalp P&L proxy shows which strikes generate the most delta-hedging edge across the 3-day window.\n")

    (out.root / "REPORT.md").write_text("\n".join(report), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Comprehensive Velvetfruit and VEV voucher EDA for Round 3.")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    out = ensure_output(args.output_dir)

    print("[Section 1/6] Loading data and checking integrity...")
    prices, trades, hydrogel_present = load_data(args.data_dir)
    integrity, anomalies = section1_data_integrity(prices, trades, out)

    print("[Section 2/6] Running Velvetfruit individual EDA...")
    vev_ctx = section2_vev(prices, trades, out)

    print("[Section 3/6] Running voucher individual EDA...")
    voucher_ctx = section3_vouchers(prices, trades, vev_ctx["vev"], out)

    print("[Section 4/6] Running options cross-voucher analysis...")
    opt_ctx = section4_options(prices, vev_ctx, out)

    print("[Section 5/6] Running lead-lag analysis...")
    leadlag = section5_leadlag(prices, opt_ctx, out)

    print("[Section 6/7] Running voucher trade vs fair value analysis...")
    trade_ctx = section6_trade_analysis(prices, trades, opt_ctx, out)

    print("[Section 7/7] Running bot pattern analysis...")
    bot_ctx = section7_bot_patterns(prices, trades, out)

    print("[Section 7/7] Writing plain-English report...")
    build_report(out, hydrogel_present, integrity, anomalies, vev_ctx, voucher_ctx, opt_ctx, leadlag, bot_ctx)
    print(f"Done. Outputs written to {out.root}")


if __name__ == "__main__":
    main()
