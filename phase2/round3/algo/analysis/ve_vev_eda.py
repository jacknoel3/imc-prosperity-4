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


def ensure_output(output_dir: Path, clean: bool = True) -> OutputPaths:
    charts = output_dir / "charts"
    tables = output_dir / "tables"
    charts.mkdir(parents=True, exist_ok=True)
    tables.mkdir(parents=True, exist_ok=True)
    if clean:
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
    """Vectorized BS delta, gamma, and vega per 1 vol point for fixed k and t."""
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
    vega[valid] = s * norm.pdf(d1) * np.sqrt(t) / 100.0
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


def cleanup_duplicate_output_artifacts(out: OutputPaths) -> None:
    for folder in [out.charts, out.tables]:
        for duplicate in folder.glob("* 2.*"):
            original = duplicate.with_name(duplicate.name.replace(" 2.", ".", 1))
            if original.is_file() and duplicate.is_file() and duplicate.read_bytes() == original.read_bytes():
                duplicate.unlink()


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


def flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = ["_".join(str(part) for part in col if str(part) != "").strip("_") for col in out.columns]
    return out


def grouped_log_returns(df: pd.DataFrame, value_col: str = "mid_price", horizon: int = 1) -> pd.Series:
    return df.groupby("day")[value_col].transform(lambda x: log_returns(x, horizon))


def safe_corr(x: pd.Series, y: pd.Series) -> float:
    aligned = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(aligned) < 3 or aligned["x"].std() == 0 or aligned["y"].std() == 0:
        return float("nan")
    return float(aligned["x"].corr(aligned["y"]))


def regression_metrics(y: pd.Series | np.ndarray, pred: pd.Series | np.ndarray, k_params: int = 1) -> dict[str, float]:
    y_arr = pd.Series(y).astype(float)
    p_arr = pd.Series(pred).astype(float)
    aligned = pd.DataFrame({"y": y_arr, "pred": p_arr}).replace([np.inf, -np.inf], np.nan).dropna()
    n = len(aligned)
    if n == 0:
        return {"n": 0, "rmse": float("nan"), "mae": float("nan"), "r2": float("nan"), "aic": float("nan"), "bic": float("nan")}
    resid = aligned["y"] - aligned["pred"]
    sse = float((resid**2).sum())
    rmse = math.sqrt(sse / n)
    mae = float(resid.abs().mean())
    denom = float(((aligned["y"] - aligned["y"].mean()) ** 2).sum())
    r2 = 1.0 - sse / denom if denom > 0 else float("nan")
    sigma2 = max(sse / n, 1e-18)
    ll = -0.5 * n * (math.log(2 * math.pi * sigma2) + 1)
    return {
        "n": n,
        "rmse": rmse,
        "mae": mae,
        "r2": float(r2),
        "aic": float(2 * k_params - 2 * ll),
        "bic": float(k_params * math.log(n) - 2 * ll),
    }


def linear_fit(x: pd.DataFrame, y: pd.Series) -> tuple[np.ndarray, pd.Series | np.ndarray, dict[str, float]]:
    data = pd.concat([x, y.rename("target")], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
    if len(data) < len(x.columns) + 2:
        return np.array([]), np.full(len(y), np.nan), regression_metrics([], [])
    x_mat = np.column_stack([np.ones(len(data)), data[x.columns].to_numpy(dtype=float)])
    y_vec = data["target"].to_numpy(dtype=float)
    coef, *_ = np.linalg.lstsq(x_mat, y_vec, rcond=None)
    pred = pd.Series(np.nan, index=y.index, dtype=float)
    pred.loc[data.index] = x_mat @ coef
    return coef, pred, regression_metrics(y, pred, len(coef))


def binary_auc(y_true: pd.Series, score: pd.Series) -> float:
    data = pd.DataFrame({"y": y_true, "score": score}).replace([np.inf, -np.inf], np.nan).dropna()
    if data["y"].nunique() < 2:
        return float("nan")
    data = data.sort_values("score")
    ranks = np.arange(1, len(data) + 1)
    pos = data["y"].astype(bool).to_numpy()
    n_pos = int(pos.sum())
    n_neg = len(data) - n_pos
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    rank_sum_pos = float(ranks[pos].sum())
    return (rank_sum_pos - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


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

    ret_1_by_day = grouped_log_returns(vev, "mid_price", 1)
    ret_clean_for_vr = ret_1_by_day.dropna()
    ret_acf1_for_regime = ret_clean_for_vr.autocorr(1) if len(ret_clean_for_vr) > 3 else float("nan")
    stationarity = {
        "pooled_mid_level": {
            "note": "Pooled price-level tests are reported for reference only; per-day diagnostics avoid artificial day-boundary jumps.",
            "adf_mid": safe_test(adfuller, vev["mid_price"], autolag="AIC"),
            "kpss_mid": safe_test(kpss, vev["mid_price"], regression="c", nlags="auto"),
            "variance_ratio_mid": {str(lag): variance_ratio(vev["mid_price"], lag) for lag in [2, 5, 10, 20]},
            "hurst_rs_mid": hurst_rs(vev["mid_price"]),
        },
        "per_day_mid": {},
        "within_day_returns": {
            "lag1_return_acf": float(ret_acf1_for_regime),
            "adf_return": safe_test(adfuller, ret_clean_for_vr, autolag="AIC"),
            "variance_ratio_return_cumsum": {str(lag): variance_ratio(ret_clean_for_vr.cumsum(), lag) for lag in [2, 5, 10, 20]},
        },
    }
    for day, group in vev.groupby("day"):
        stationarity["per_day_mid"][str(day)] = {
            "adf_mid": safe_test(adfuller, group["mid_price"], autolag="AIC"),
            "kpss_mid": safe_test(kpss, group["mid_price"], regression="c", nlags="auto"),
            "variance_ratio_mid": {str(lag): variance_ratio(group["mid_price"], lag) for lag in [2, 5, 10, 20]},
            "hurst_rs_mid": hurst_rs(group["mid_price"]),
        }
    vr10 = stationarity["within_day_returns"]["variance_ratio_return_cumsum"]["10"]["vr"]
    adf_p = stationarity["within_day_returns"]["adf_return"].get("pvalue", float("nan"))
    if np.isfinite(ret_acf1_for_regime) and ret_acf1_for_regime < -0.05 and np.isfinite(vr10) and vr10 < 1.0:
        regime = "price-persistent with local tick mean reversion"
    elif adf_p < 0.05 and np.isfinite(vr10) and vr10 > 1.05:
        regime = "locally trending returns"
    elif adf_p < 0.05:
        regime = "return-stationary"
    else:
        regime = "regime-switching or weakly drifting"
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

    # --- NEW: ACF of mid price and within-day log returns (lags 1-20) ---
    mid_clean = vev["mid_price"].dropna()
    mid_acf = compute_acf_vals(mid_clean, nlags=20, fft=True)[1:]
    ret_clean = grouped_log_returns(vev, "mid_price", 1).dropna()
    ret_acf = compute_acf_vals(ret_clean, nlags=20, fft=True)[1:]
    ci_band_mid = 1.96 / math.sqrt(len(mid_clean))
    ci_band_ret = 1.96 / math.sqrt(len(ret_clean))
    acf_rows = []
    for lag, mid_val, ret_val in zip(range(1, 21), mid_acf, ret_acf):
        acf_rows.append({"scope": "pooled_level", "lag": lag, "mid_acf": mid_val, "ret_acf": ret_val})
    for day, group in vev.groupby("day"):
        day_mid = group["mid_price"].dropna()
        day_ret = log_returns(group["mid_price"], 1).dropna()
        day_mid_acf = compute_acf_vals(day_mid, nlags=20, fft=True)[1:] if len(day_mid) > 25 else np.full(20, np.nan)
        day_ret_acf = compute_acf_vals(day_ret, nlags=20, fft=True)[1:] if len(day_ret) > 25 else np.full(20, np.nan)
        for lag, mid_val, ret_val in zip(range(1, 21), day_mid_acf, day_ret_acf):
            acf_rows.append({"scope": f"day_{day}", "lag": lag, "mid_acf": mid_val, "ret_acf": ret_val})
    acf_df = pd.DataFrame(acf_rows)
    write_table(acf_df, out.tables / "velvetfruit_acf.csv")
    fig, axes = plt.subplots(1, 2, figsize=(14, 4))
    for ax, vals, title, band in zip(axes, [mid_acf, ret_acf], ["Mid Price", "Within-Day Log Returns"], [ci_band_mid, ci_band_ret]):
        ax.bar(range(1, 21), vals)
        ax.axhline(0, color="black", lw=0.8)
        ax.axhline(band, ls="--", color="red", alpha=0.7, label="95% CI")
        ax.axhline(-band, ls="--", color="red", alpha=0.7)
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
        bid_diff = group["bid_price_1"].diff()
        ask_diff = group["ask_price_1"].diff()
        bid_change = (bid_diff.notna() & (bid_diff != 0)).sum() / max(len(group) - 1, 1)
        ask_change = (ask_diff.notna() & (ask_diff != 0)).sum() / max(len(group) - 1, 1)
        change_rows.append({"day": day, "bid_price_change_rate": float(bid_change), "ask_price_change_rate": float(ask_change)})
    change_df = pd.DataFrame(change_rows)
    write_table(change_df, out.tables / "velvetfruit_quote_change_rate.csv")
    results["quote_change_rate"] = change_df

    # --- Bot volume consistency (stable size = likely single bot) ---
    vol_cols = ["bid_volume_1", "ask_volume_1", "bid_volume_2", "ask_volume_2", "bid_volume_3", "ask_volume_3"]
    vol_stats = flatten_columns(ve.groupby("day")[vol_cols].agg(["mean", "std", "median"]).reset_index())
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


def section7_options_microstructure_surfaces(
    prices: pd.DataFrame,
    trades: pd.DataFrame,
    vev_ctx: dict[str, Any],
    opt_ctx: dict[str, pd.DataFrame],
    out: OutputPaths,
) -> dict[str, pd.DataFrame]:
    """Operational option metrics used by market-making and inventory-control models."""
    iv_df = opt_ctx.get("iv", pd.DataFrame()).copy()
    greeks_ts = opt_ctx.get("greeks_ts", pd.DataFrame()).copy()
    vev = vev_ctx["vev"].copy()
    rv = vev_ctx["rv"].copy()

    if iv_df.empty:
        empty = pd.DataFrame()
        for name in [
            "options_surface_state.csv",
            "options_svi_surface_params.csv",
            "options_portfolio_greeks_timeseries.csv",
            "options_inventory_skew_base_spread.csv",
            "underlying_signed_flow_toxicity.csv",
            "option_l1_obi_predictiveness.csv",
            "trade_sign_autocorrelation.csv",
        ]:
            write_table(empty, out.tables / name)
        return {}

    spot_book = vev[
        [
            "day",
            "timestamp",
            "global_tick",
            "mid_price",
            "bid_price_1",
            "ask_price_1",
            "bid_volume_1",
            "ask_volume_1",
            "spread",
        ]
    ].rename(
        columns={
            "mid_price": "spot_mid_book",
            "bid_price_1": "spot_bid_1",
            "ask_price_1": "spot_ask_1",
            "bid_volume_1": "spot_bid_volume_1",
            "ask_volume_1": "spot_ask_volume_1",
            "spread": "spot_spread",
        }
    )
    option_book = pd.concat(
        [add_global_tick(series_for(prices, product)).assign(voucher=product, strike=strike(product)) for product in VOUCHERS],
        ignore_index=True,
    )
    surface = option_book.merge(
        iv_df[["day", "timestamp", "global_tick", "voucher", "spot_mid", "option_mid", "tte_days", "iv"]],
        on=["day", "timestamp", "global_tick", "voucher"],
        how="left",
    ).merge(
        greeks_ts[["day", "timestamp", "voucher", "bs_delta", "bs_gamma", "bs_vega"]],
        on=["day", "timestamp", "voucher"],
        how="left",
    ).merge(
        spot_book,
        on=["day", "timestamp", "global_tick"],
        how="left",
    ).merge(
        rv[["day", "timestamp", "rv_500"]],
        on=["day", "timestamp"],
        how="left",
    )
    surface["option_spread"] = surface["ask_price_1"] - surface["bid_price_1"]
    surface["l1_obi"] = (surface["bid_volume_1"] - surface["ask_volume_1"]) / (surface["bid_volume_1"] + surface["ask_volume_1"]).replace(0, np.nan)
    surface["option_microprice"] = (
        surface["ask_price_1"] * surface["bid_volume_1"] + surface["bid_price_1"] * surface["ask_volume_1"]
    ) / (surface["bid_volume_1"] + surface["ask_volume_1"]).replace(0, np.nan)
    surface["option_microprice_minus_mid"] = surface["option_microprice"] - surface["option_mid"]
    surface["spot_l1_obi"] = (surface["spot_bid_volume_1"] - surface["spot_ask_volume_1"]) / (
        surface["spot_bid_volume_1"] + surface["spot_ask_volume_1"]
    ).replace(0, np.nan)
    surface["spot_microprice"] = (
        surface["spot_ask_1"] * surface["spot_bid_volume_1"] + surface["spot_bid_1"] * surface["spot_ask_volume_1"]
    ) / (surface["spot_bid_volume_1"] + surface["spot_ask_volume_1"]).replace(0, np.nan)
    surface["underlying_microprice_minus_mid"] = surface["spot_microprice"] - surface["spot_mid_book"]
    surface["tte_years"] = surface["tte_days"] / 365.0
    surface["log_moneyness"] = np.log(surface["strike"].astype(float) / surface["spot_mid"].astype(float))
    surface["total_variance"] = surface["iv"] ** 2 * surface["tte_years"]
    surface["theoretical_mid"] = [
        bs_call_price(float(row.spot_mid), int(row.strike), float(row.tte_years), float(row.iv))
        if np.isfinite([row.spot_mid, row.strike, row.tte_years, row.iv]).all() and row.iv > 0
        else np.nan
        for row in surface.itertuples(index=False)
    ]
    surface["fair_minus_mid"] = surface["theoretical_mid"] - surface["option_mid"]
    surface["spot_half_spread"] = surface["spot_spread"] / 2.0
    surface["delta_hedge_halfspread_cost"] = surface["bs_delta"].abs() * surface["spot_half_spread"]
    per_tick_vol = surface["rv_500"] / math.sqrt(TICKS_PER_DAY * 365)
    spot_std_10ticks = surface["spot_mid"].abs() * per_tick_vol * math.sqrt(10)
    surface["gamma_gap_risk_10ticks"] = 0.5 * surface["bs_gamma"].abs() * (spot_std_10ticks**2)
    surface["base_spread"] = surface["option_spread"] + 2.0 * surface["delta_hedge_halfspread_cost"] + 2.0 * surface["gamma_gap_risk_10ticks"]
    surface["inventory_skew_q100"] = 100.0 * surface["gamma_gap_risk_10ticks"]
    surface["reservation_price_if_long_100"] = surface["theoretical_mid"] - surface["inventory_skew_q100"]
    surface["reservation_price_if_short_100"] = surface["theoretical_mid"] + surface["inventory_skew_q100"]

    keep_cols = [
        "day",
        "timestamp",
        "global_tick",
        "voucher",
        "strike",
        "spot_mid",
        "option_mid",
        "bid_price_1",
        "ask_price_1",
        "option_spread",
        "l1_obi",
        "option_microprice",
        "option_microprice_minus_mid",
        "underlying_microprice_minus_mid",
        "iv",
        "total_variance",
        "theoretical_mid",
        "fair_minus_mid",
        "bs_delta",
        "bs_gamma",
        "bs_vega",
        "rv_500",
        "delta_hedge_halfspread_cost",
        "gamma_gap_risk_10ticks",
        "inventory_skew_q100",
        "base_spread",
        "reservation_price_if_long_100",
        "reservation_price_if_short_100",
    ]
    write_table(surface[keep_cols], out.tables / "options_surface_state.csv")

    svi_rows: list[dict[str, Any]] = []
    for (day, timestamp), group in surface.dropna(subset=["iv", "total_variance", "log_moneyness"]).groupby(["day", "timestamp"]):
        good = group[np.isfinite(group["total_variance"]) & np.isfinite(group["log_moneyness"])]
        if len(good) < 4 or good["log_moneyness"].nunique() < 4:
            continue
        try:
            coeff2, coeff1, coeff0 = np.polyfit(good["log_moneyness"], good["total_variance"], 2)
            pred = coeff2 * good["log_moneyness"] ** 2 + coeff1 * good["log_moneyness"] + coeff0
            ss_res = float(((good["total_variance"] - pred) ** 2).sum())
            ss_tot = float(((good["total_variance"] - good["total_variance"].mean()) ** 2).sum())
            svi_rows.append(
                {
                    "day": int(day),
                    "timestamp": int(timestamp),
                    "global_tick": int(day) * 1_000_000 + int(timestamp),
                    "n_strikes": int(len(good)),
                    "atm_total_variance": float(coeff0),
                    "svi_skew": float(coeff1),
                    "svi_convexity": float(coeff2),
                    "smile_r2": float(1.0 - ss_res / ss_tot) if ss_tot > 1e-18 else np.nan,
                    "mean_iv": float(good["iv"].mean()),
                    "min_iv": float(good["iv"].min()),
                    "max_iv": float(good["iv"].max()),
                }
            )
        except Exception:
            continue
    svi = pd.DataFrame(svi_rows)
    write_table(svi, out.tables / "options_svi_surface_params.csv")

    if not svi.empty:
        fig, axes = plt.subplots(3, 1, figsize=(14, 9), sharex=True)
        for day, group in svi.groupby("day"):
            axes[0].plot(group["global_tick"], group["svi_skew"], lw=0.8, label=f"Day {day}")
            axes[1].plot(group["global_tick"], group["svi_convexity"], lw=0.8, label=f"Day {day}")
            axes[2].plot(group["global_tick"], group["smile_r2"], lw=0.8, label=f"Day {day}")
        for ax in axes:
            add_day_dividers(ax)
            ax.legend(fontsize=7)
        axes[0].set_title("SVI-Style Total-Variance Smile Parameters")
        axes[0].set_ylabel("Skew")
        axes[1].set_ylabel("Convexity")
        axes[2].set_ylabel("R2")
        axes[2].set_xlabel("Global tick")
        save_fig(out.charts / "options_svi_skew_convexity_timeseries.png")

    def surface_3d(z_col: str, path: Path, title: str, z_label: str) -> None:
        pts = surface.dropna(subset=["global_tick", "strike", z_col]).copy()
        if pts.empty:
            return
        step = max(1, len(pts) // 8000)
        pts = pts.iloc[::step]
        fig = plt.figure(figsize=(11, 7))
        ax = fig.add_subplot(111, projection="3d")
        sc = ax.scatter(pts["global_tick"], pts["strike"], pts[z_col], c=pts[z_col], s=5, cmap="viridis", alpha=0.75)
        ax.set_title(title)
        ax.set_xlabel("Global tick")
        ax.set_ylabel("Strike")
        ax.set_zlabel(z_label)
        fig.colorbar(sc, shrink=0.6, pad=0.08)
        save_fig(path)

    surface_3d("iv", out.charts / "options_iv_surface_3d.png", "Option IV Surface Over Time", "IV")
    surface_3d("total_variance", out.charts / "options_total_variance_surface_3d.png", "Option Total Variance Surface Over Time", "Total variance")
    surface_3d("theoretical_mid", out.charts / "options_fair_value_surface_3d.png", "BS Fair Value Surface Over Time", "Fair value")
    surface_3d("bs_gamma", out.charts / "options_gamma_surface_3d.png", "Option Gamma Surface Over Time", "Gamma")

    portfolio_specs = {
        "unit_long_all_vouchers": {voucher: 1.0 for voucher in VOUCHERS},
        "limit_long_all_vouchers": {voucher: 300.0 for voucher in VOUCHERS},
        "near_atm_gamma_100_each": {"VEV_5100": 100.0, "VEV_5200": 100.0, "VEV_5300": 100.0, "VEV_5400": 100.0, "VEV_5500": 100.0},
        "strat4_observed_voucher_book": {"VEV_4000": 9.0, "VEV_4500": 9.0, "VEV_5000": 6.0},
    }
    portfolio_rows: list[dict[str, Any]] = []
    greek_panel = surface.dropna(subset=["bs_delta", "bs_gamma", "bs_vega"]).copy()
    for (day, timestamp, global_tick), group in greek_panel.groupby(["day", "timestamp", "global_tick"]):
        g = group.set_index("voucher")
        for name, weights in portfolio_specs.items():
            total_delta = total_gamma = total_vega = 0.0
            gross_voucher = 0.0
            for voucher, qty in weights.items():
                if voucher not in g.index:
                    continue
                row = g.loc[voucher]
                if isinstance(row, pd.DataFrame):
                    row = row.iloc[0]
                total_delta += qty * float(row["bs_delta"])
                total_gamma += qty * float(row["bs_gamma"])
                total_vega += qty * float(row["bs_vega"])
                gross_voucher += abs(qty)
            hedge_qty = float(np.clip(-total_delta, -200.0, 200.0))
            portfolio_rows.append(
                {
                    "day": int(day),
                    "timestamp": int(timestamp),
                    "global_tick": int(global_tick),
                    "portfolio": name,
                    "gross_voucher_position": gross_voucher,
                    "net_delta": total_delta,
                    "underlying_hedge_qty_clipped": hedge_qty,
                    "delta_after_hedge_limit": total_delta + hedge_qty,
                    "net_gamma": total_gamma,
                    "net_vega": total_vega,
                }
            )
    portfolio = pd.DataFrame(portfolio_rows)
    write_table(portfolio, out.tables / "options_portfolio_greeks_timeseries.csv")
    if not portfolio.empty:
        fig, axes = plt.subplots(3, 1, figsize=(14, 9), sharex=True)
        for name, group in portfolio.groupby("portfolio"):
            if name in {"unit_long_all_vouchers", "near_atm_gamma_100_each", "strat4_observed_voucher_book"}:
                axes[0].plot(group["global_tick"], group["net_delta"], lw=0.8, label=name)
                axes[1].plot(group["global_tick"], group["net_gamma"], lw=0.8, label=name)
                axes[2].plot(group["global_tick"], group["net_vega"], lw=0.8, label=name)
        axes[0].set_title("Net Portfolio Greeks Over Time")
        axes[0].set_ylabel("Delta")
        axes[1].set_ylabel("Gamma")
        axes[2].set_ylabel("Vega")
        axes[2].set_xlabel("Global tick")
        for ax in axes:
            add_day_dividers(ax)
            ax.legend(fontsize=7)
        save_fig(out.charts / "options_portfolio_greeks_timeseries.png")

    inv_cols = [
        "day",
        "timestamp",
        "global_tick",
        "voucher",
        "strike",
        "option_mid",
        "theoretical_mid",
        "option_spread",
        "delta_hedge_halfspread_cost",
        "gamma_gap_risk_10ticks",
        "inventory_skew_q100",
        "base_spread",
        "reservation_price_if_long_100",
        "reservation_price_if_short_100",
    ]
    inventory = surface[inv_cols].copy()
    write_table(inventory, out.tables / "options_inventory_skew_base_spread.csv")
    if not inventory.empty:
        summary = inventory.groupby("voucher", as_index=False)[["option_spread", "base_spread", "inventory_skew_q100"]].median()
        x = np.arange(len(summary))
        width = 0.35
        plt.figure(figsize=(12, 5))
        plt.bar(x - width / 2, summary["option_spread"], width=width, label="Observed spread")
        plt.bar(x + width / 2, summary["base_spread"], width=width, label="Risk-adjusted base spread")
        plt.xticks(x, summary["voucher"], rotation=30)
        plt.title("Median Observed Spread vs Risk-Adjusted Base Spread")
        plt.ylabel("Price ticks")
        plt.legend()
        save_fig(out.charts / "options_base_spread_by_voucher.png")

    option_pred_rows: list[dict[str, Any]] = []
    pred_surface = surface.sort_values(["voucher", "day", "timestamp"]).copy()
    for horizon_rows in [1, 10, 100]:
        pred_surface[f"future_option_ret_{horizon_rows}"] = pred_surface.groupby(["voucher", "day"])["option_mid"].transform(
            lambda x: forward_log_returns(x, horizon_rows)
        )
        pred_surface[f"future_iv_change_{horizon_rows}"] = pred_surface.groupby(["voucher", "day"])["iv"].transform(lambda x: x.shift(-horizon_rows) - x)
    for voucher, group in pred_surface.groupby("voucher"):
        for horizon_rows in [1, 10, 100]:
            for target in [f"future_option_ret_{horizon_rows}", f"future_iv_change_{horizon_rows}"]:
                clean = group[["l1_obi", "option_microprice_minus_mid", "underlying_microprice_minus_mid", target]].replace([np.inf, -np.inf], np.nan).dropna()
                if len(clean) < 20:
                    continue
                coef, _, metrics = linear_fit(clean[["l1_obi", "option_microprice_minus_mid", "underlying_microprice_minus_mid"]], clean[target])
                option_pred_rows.append(
                    {
                        "voucher": voucher,
                        "horizon_rows": horizon_rows,
                        "target": target,
                        "n": int(metrics.get("n", len(clean))),
                        "r2": float(metrics.get("r2", np.nan)),
                        "obi_corr": safe_corr(clean["l1_obi"], clean[target]),
                        "option_microprice_corr": safe_corr(clean["option_microprice_minus_mid"], clean[target]),
                        "underlying_microprice_corr": safe_corr(clean["underlying_microprice_minus_mid"], clean[target]),
                        "coef_l1_obi": float(coef[1]) if len(coef) > 1 else np.nan,
                    }
                )
    option_obi = pd.DataFrame(option_pred_rows)
    write_table(option_obi, out.tables / "option_l1_obi_predictiveness.csv")
    if not option_obi.empty:
        plot_df = option_obi[option_obi["target"].eq("future_option_ret_10")].copy()
        if not plot_df.empty:
            plt.figure(figsize=(12, 5))
            plt.bar(plot_df["voucher"], plot_df["obi_corr"].abs())
            plt.xticks(rotation=30)
            plt.title("Option L1 OBI Absolute Correlation With Future 10-Row Option Return")
            plt.ylabel("|correlation|")
            save_fig(out.charts / "option_l1_obi_predictiveness.png")

    def signed_trades_for(product: str) -> pd.DataFrame:
        tr = trades[trades["symbol"].eq(product)].copy()
        if tr.empty:
            return pd.DataFrame()
        book = prices[prices["product"].eq(product)][["day", "timestamp", "bid_price_1", "ask_price_1", "mid_price"]].copy()
        signed = tr.merge(book, on=["day", "timestamp"], how="left")
        signed["trade_sign"] = np.where(
            signed["price"] >= signed["ask_price_1"],
            1.0,
            np.where(signed["price"] <= signed["bid_price_1"], -1.0, np.sign(signed["price"] - signed["mid_price"])),
        )
        signed.loc[signed["trade_sign"].eq(0), "trade_sign"] = np.nan
        signed["signed_quantity"] = signed["trade_sign"] * signed["quantity"]
        return signed

    flow_rows: list[dict[str, Any]] = []
    vt = signed_trades_for(UNDERLYING)
    if not vt.empty:
        vt["bucket_start"] = (vt["timestamp"] // 1000) * 1000
        for (day, bucket), group in vt.groupby(["day", "bucket_start"]):
            buy_qty = group.loc[group["trade_sign"].gt(0), "quantity"].sum()
            sell_qty = group.loc[group["trade_sign"].lt(0), "quantity"].sum()
            total_qty = buy_qty + sell_qty
            start = vev[(vev["day"].eq(day)) & (vev["timestamp"].eq(bucket))]
            for horizon in [100, 500, 1000, 5000]:
                end = vev[(vev["day"].eq(day)) & (vev["timestamp"].eq(bucket + horizon))]
                fwd_ret = float(np.log(end.iloc[0]["mid_price"]) - np.log(start.iloc[0]["mid_price"])) if len(start) and len(end) else np.nan
                flow_rows.append(
                    {
                        "day": int(day),
                        "bucket_start": int(bucket),
                        "horizon_timestamp": horizon,
                        "trade_count": int(len(group)),
                        "buy_qty": float(buy_qty),
                        "sell_qty": float(sell_qty),
                        "signed_volume_imbalance": float((buy_qty - sell_qty) / total_qty) if total_qty else np.nan,
                        "signed_quantity": float(group["signed_quantity"].sum()),
                        "future_underlying_return": fwd_ret,
                    }
                )
    flow = pd.DataFrame(flow_rows)
    write_table(flow, out.tables / "underlying_signed_flow_toxicity.csv")
    if not flow.empty:
        summary_rows = []
        for horizon, group in flow.groupby("horizon_timestamp"):
            summary_rows.append(
                {
                    "horizon_timestamp": int(horizon),
                    "n": int(group[["signed_volume_imbalance", "future_underlying_return"]].dropna().shape[0]),
                    "corr_signed_imbalance_future_return": safe_corr(group["signed_volume_imbalance"], group["future_underlying_return"]),
                    "mean_future_return_when_buy_imbalanced": float(group[group["signed_volume_imbalance"].gt(0)]["future_underlying_return"].mean()),
                    "mean_future_return_when_sell_imbalanced": float(group[group["signed_volume_imbalance"].lt(0)]["future_underlying_return"].mean()),
                }
            )
        flow_summary = pd.DataFrame(summary_rows)
        write_table(flow_summary, out.tables / "underlying_signed_flow_toxicity_summary.csv")
        plt.figure(figsize=(8, 5))
        plt.bar(flow_summary["horizon_timestamp"].astype(str), flow_summary["corr_signed_imbalance_future_return"])
        plt.axhline(0, color="black", lw=0.8)
        plt.title("Underlying Signed Flow Imbalance vs Future Return")
        plt.xlabel("Horizon timestamp")
        plt.ylabel("Correlation")
        save_fig(out.charts / "underlying_signed_flow_toxicity.png")

    acf_rows: list[dict[str, Any]] = []
    for product in PRODUCTS:
        signed = signed_trades_for(product).sort_values(["day", "timestamp"]).dropna(subset=["trade_sign"])
        for day, group in signed.groupby("day"):
            signs = group["trade_sign"].astype(float)
            for lag in range(1, 21):
                acf_rows.append(
                    {
                        "product": product,
                        "day": int(day),
                        "lag": lag,
                        "trade_count": int(len(signs)),
                        "trade_sign_autocorr": float(signs.autocorr(lag)) if len(signs) > lag + 2 else np.nan,
                    }
                )
    sign_acf = pd.DataFrame(acf_rows)
    write_table(sign_acf, out.tables / "trade_sign_autocorrelation.csv")
    if not sign_acf.empty:
        plot_acf = sign_acf.groupby(["product", "lag"], as_index=False)["trade_sign_autocorr"].mean()
        active_products = plot_acf[plot_acf["product"].isin([UNDERLYING, "VEV_4000", "VEV_4500", "VEV_5000", "VEV_5200", "VEV_5300"])]
        plt.figure(figsize=(12, 5))
        for product, group in active_products.groupby("product"):
            plt.plot(group["lag"], group["trade_sign_autocorr"], marker="o", ms=3, lw=0.8, label=product)
        plt.axhline(0, color="black", lw=0.8)
        plt.title("Trade Sign Autocorrelation by Product")
        plt.xlabel("Trade lag")
        plt.ylabel("Autocorrelation")
        plt.legend(fontsize=7, ncol=2)
        save_fig(out.charts / "trade_sign_autocorrelation.png")

    return {
        "surface": surface,
        "svi": svi,
        "portfolio": portfolio,
        "inventory": inventory,
        "option_obi": option_obi,
        "flow": flow,
        "trade_sign_acf": sign_acf,
    }


def section8_research_and_models(
    prices: pd.DataFrame,
    trades: pd.DataFrame,
    vev_ctx: dict[str, Any],
    voucher_ctx: dict[str, pd.DataFrame],
    opt_ctx: dict[str, pd.DataFrame],
    leadlag: pd.DataFrame,
    trade_ctx: dict,
    bot_ctx: dict,
    out: OutputPaths,
) -> dict[str, pd.DataFrame]:
    """Answer the research checklist and run lightweight model diagnostics."""
    vev = vev_ctx["vev"].copy()
    rv = vev_ctx["rv"].copy()
    iv_df = opt_ctx["iv"].copy()
    iv_rv = opt_ctx["iv_rv"].copy()
    greeks_ts = opt_ctx.get("greeks_ts", pd.DataFrame()).copy()
    liquidity = voucher_ctx["liquidity"].copy()
    tfv = trade_ctx.get("voucher_trade_vs_fv", pd.DataFrame()).copy()

    vev["ret_1"] = grouped_log_returns(vev, "mid_price", 1)
    vev["ret_10"] = grouped_log_returns(vev, "mid_price", 10)
    vev["future_ret_10"] = vev.groupby("day")["mid_price"].transform(lambda x: forward_log_returns(x, 10))
    vev["spread"] = vev["ask_price_1"] - vev["bid_price_1"]
    vev["total_depth"] = vev[["bid_volume_1", "bid_volume_2", "bid_volume_3", "ask_volume_1", "ask_volume_2", "ask_volume_3"]].sum(axis=1, skipna=True)
    vev["imbalance"] = (vev["bid_volume_1"] - vev["ask_volume_1"]) / (vev["bid_volume_1"] + vev["ask_volume_1"])

    atm_iv_mean = float(iv_rv["iv"].mean())
    rv500_mean = float(iv_rv["rv_500"].mean())
    iv_rv_gap = float(iv_rv["iv_minus_rv"].mean())
    ret_acf1 = float(vev["ret_1"].dropna().autocorr(1))
    spread_mean = float(vev["spread"].mean())
    top_gamma = pd.read_csv(out.tables / "voucher_gamma_scalp_proxy.csv").sort_values("net_gamma_scalp_pnl", ascending=False)
    top_gamma_names = ", ".join(top_gamma.head(4)["voucher"].astype(str))
    residual_best = leadlag.reindex(leadlag["residual_autocorr_lag1"].abs().sort_values(ascending=False).index).head(3)
    residual_names = ", ".join(residual_best["voucher"].astype(str))
    noarb = opt_ctx["noarb"].copy()
    noarb_count = int(noarb["count"].sum())
    maker = pd.read_csv(out.tables / "velvetfruit_maker_fills.csv")
    buy_bias = float(maker["pct_at_ask"].mean())
    clustering = pd.read_csv(out.tables / "velvetfruit_trade_clustering.csv")
    clustering_disp = float(clustering["dispersion_index"].mean())

    model_rows: list[dict[str, Any]] = []

    def add_model(model_id: int, name: str, implementation: str, target: str, metrics: dict[str, Any], interpretation: str, status: str = "implemented") -> None:
        metric_items = [(k, v) for k, v in metrics.items() if isinstance(v, (int, float, np.floating)) and np.isfinite(v)]
        preferred = [
            "r2",
            "auc",
            "qlike",
            "rmse",
            "mean_smile_r2",
            "pc1_explained",
            "best_net_gamma_scalp",
            "mean_abs_residual_acf1",
            "mean_lifetime",
            "transition_diag",
            "mean_dispersion",
            "branching_proxy",
            "duration_phi",
            "drift",
            "phi",
            "vr10",
            "hurst",
            "best_lambda",
        ]
        primary = next(((k, metrics[k]) for k in preferred if k in metrics and isinstance(metrics[k], (int, float, np.floating)) and np.isfinite(metrics[k])), metric_items[0] if metric_items else ("n", float("nan")))
        secondary = next(((k, v) for k, v in metric_items if k != primary[0] and k != "n"), ("", float("nan")))
        row = {
            "model_id": f"M{model_id}",
            "model_name": name,
            "implementation": implementation,
            "target": target,
            "primary_metric": primary[0],
            "primary_value": primary[1],
            "secondary_metric": secondary[0],
            "secondary_value": secondary[1],
            "interpretation": interpretation,
            "fit_status": status,
        }
        row.update({f"metric_{k}": v for k, v in metrics.items()})
        model_rows.append(row)

    # M1-M4: underlying time-series models.
    ret = vev["ret_1"].dropna()
    drift = float(ret.mean())
    rw_pred = pd.Series(0.0, index=ret.index)
    add_model(1, "Random walk with drift", "Normal one-step return model with within-day returns.", "VELVETFRUIT_EXTRACT ret_1", regression_metrics(ret, rw_pred, 1) | {"drift": drift}, "Drift is tiny relative to one-tick noise; useful baseline only.")

    ar_df = pd.DataFrame({"ret": vev["ret_1"], "lag1": vev.groupby("day")["ret_1"].shift(1)}).dropna()
    coef, pred, ar_metrics = linear_fit(ar_df[["lag1"]], ar_df["ret"])
    add_model(2, "AR(1) return model", "OLS ret_t on ret_{t-1}; ARMA proxy.", "VELVETFRUIT_EXTRACT ret_1", ar_metrics | {"phi": float(coef[1]) if len(coef) > 1 else float("nan")}, "Negative lag coefficient confirms short-horizon bid/ask bounce or micro-reversion.")

    vr10 = variance_ratio(ret.cumsum(), 10)["vr"]
    hurst_ret = hurst_rs(ret.cumsum())
    add_model(3, "Fractional-noise / ARFIMA proxy", "Variance-ratio and Hurst diagnostics on within-day return cumulative sum.", "VELVETFRUIT_EXTRACT", {"hurst": hurst_ret, "vr10": vr10, "ret_acf1": ret_acf1}, "Long memory is not the first-order edge; local negative autocorrelation dominates.")

    ou_rows = []
    for day, group in vev.groupby("day"):
        x = group["mid_price"] - group["mid_price"].mean()
        dx = x.diff()
        xlag = x.shift(1)
        coef_ou, pred_ou, met_ou = linear_fit(pd.DataFrame({"xlag": xlag}), dx)
        beta = float(coef_ou[1]) if len(coef_ou) > 1 else float("nan")
        theta = -beta if np.isfinite(beta) and beta < 0 else float("nan")
        half_life = math.log(2) / theta if np.isfinite(theta) and theta > 0 else float("nan")
        ou_rows.append({"day": day, "beta": beta, "theta": theta, "half_life_ticks": half_life, **met_ou})
    ou_df = pd.DataFrame(ou_rows)
    write_table(ou_df, out.tables / "model_ou_diagnostics.csv")
    add_model(4, "Ornstein-Uhlenbeck fair-value reversion", "Day-demeaned OU regression dx = a + b x_{t-1}.", "VELVETFRUIT_EXTRACT mid", {"mean_half_life": float(ou_df["half_life_ticks"].mean()), "mean_r2": float(ou_df["r2"].mean())}, "Mid reversion is weak at the day-demeaned level; stronger signal is in one-tick returns.")

    # M5: regime states.
    reg_df = rv.merge(vev[["day", "timestamp", "global_tick", "ret_1"]], on=["day", "timestamp", "global_tick"], how="left")
    reg_df["vol_state"] = pd.qcut(reg_df["rv_500"].rank(method="first"), 3, labels=["low_vol", "mid_vol", "high_vol"])
    reg_df["ret_sign"] = np.where(reg_df["ret_1"] > 0, "up", np.where(reg_df["ret_1"] < 0, "down", "flat"))
    reg_df["state"] = reg_df["vol_state"].astype(str) + "_" + reg_df["ret_sign"]
    trans = pd.crosstab(reg_df["state"].shift(1), reg_df["state"], normalize="index")
    transition_diag = float(np.nanmean(np.diag(trans.to_numpy()))) if trans.shape[0] == trans.shape[1] and len(trans) else float("nan")
    write_table(trans.reset_index(), out.tables / "model_regime_transition_matrix.csv")
    plt.figure(figsize=(10, 4))
    reg_df.groupby(["day", "vol_state"], observed=False).size().unstack(fill_value=0).plot(kind="bar", ax=plt.gca())
    plt.title("Volatility Regime Counts By Day")
    plt.ylabel("Snapshots")
    save_fig(out.charts / "model_regime_states.png")
    add_model(5, "Regime-switching AR proxy", "Discrete low/mid/high volatility states with return sign transitions.", "VELVETFRUIT_EXTRACT", {"transition_diag": transition_diag, "high_vol_share": float((reg_df["vol_state"] == "high_vol").mean())}, "Regime labels are useful for risk controls more than directional alpha.")

    # M6-M10: volatility and filtering models.
    r = ret.to_numpy(dtype=float)
    sq = r**2
    garch_candidates = []
    for alpha in [0.03, 0.05, 0.08, 0.12, 0.18]:
        for beta in [0.70, 0.80, 0.88, 0.92, 0.95]:
            if alpha + beta >= 0.995:
                continue
            var = np.full_like(sq, np.nan)
            var[0] = np.nanvar(r)
            omega = np.nanvar(r) * (1 - alpha - beta)
            for i in range(1, len(sq)):
                var[i] = omega + alpha * sq[i - 1] + beta * var[i - 1]
            qlike = float(np.nanmean(np.log(var[1:]) + sq[1:] / var[1:]))
            garch_candidates.append({"alpha": alpha, "beta": beta, "omega": omega, "qlike": qlike})
    garch_df = pd.DataFrame(garch_candidates).sort_values("qlike")
    write_table(garch_df, out.tables / "model_garch_grid.csv")
    best_garch = garch_df.iloc[0]
    add_model(6, "GARCH(1,1) grid", "Coarse Gaussian QLIKE grid for one-tick returns.", "VELVETFRUIT_EXTRACT volatility", {"qlike": float(best_garch["qlike"]), "alpha": float(best_garch["alpha"]), "beta": float(best_garch["beta"])}, "Volatility is persistent but stable; grid fit is a risk model, not an alpha model.")

    asym_df = pd.DataFrame({"sq": vev["ret_1"] ** 2, "lag_sq": vev.groupby("day")["ret_1"].shift(1) ** 2})
    asym_df["neg_lag_sq"] = asym_df["lag_sq"] * (vev.groupby("day")["ret_1"].shift(1) < 0).astype(float)
    coef_asym, pred_asym, asym_metrics = linear_fit(asym_df[["lag_sq", "neg_lag_sq"]], asym_df["sq"])
    add_model(7, "GJR-GARCH / EGARCH asymmetry proxy", "Squared-return regression on lagged squared return and negative-lag interaction.", "VELVETFRUIT_EXTRACT volatility", asym_metrics | {"asym_coef": float(coef_asym[2]) if len(coef_asym) > 2 else float("nan")}, "Asymmetry is a diagnostic; it should be included only if stable out-of-sample.")

    har = rv.copy()
    har["future_rv_500"] = har.groupby("day")["rv_500"].shift(-500)
    coef_har, pred_har, har_metrics = linear_fit(har[["rv_100", "rv_500", "rv_2000"]], har["future_rv_500"])
    write_table(pd.DataFrame([{"intercept": coef_har[0] if len(coef_har) else np.nan, "rv100_coef": coef_har[1] if len(coef_har) > 1 else np.nan, "rv500_coef": coef_har[2] if len(coef_har) > 2 else np.nan, "rv2000_coef": coef_har[3] if len(coef_har) > 3 else np.nan, **har_metrics}]), out.tables / "model_har_rv.csv")
    add_model(8, "HAR-RV", "Future RV500 regressed on RV100/RV500/RV2000.", "Realized volatility", har_metrics, "Useful for hedge-frequency and option-entry sizing.")

    ewma_rows = []
    for lam in np.linspace(0.80, 0.99, 20):
        var = np.full_like(sq, np.nan)
        var[0] = np.nanvar(r)
        for i in range(1, len(sq)):
            var[i] = lam * var[i - 1] + (1 - lam) * sq[i - 1]
        qlike = float(np.nanmean(np.log(var[1:]) + sq[1:] / var[1:]))
        ewma_rows.append({"lambda": lam, "qlike": qlike})
    ewma_df = pd.DataFrame(ewma_rows).sort_values("qlike")
    write_table(ewma_df, out.tables / "model_ewma_vol.csv")
    plt.figure(figsize=(8, 4))
    plt.plot(ewma_df.sort_values("lambda")["lambda"], ewma_df.sort_values("lambda")["qlike"], marker="o")
    plt.title("EWMA Volatility Lambda Grid")
    plt.xlabel("lambda")
    plt.ylabel("QLIKE")
    save_fig(out.charts / "model_ewma_lambda_grid.png")
    add_model(9, "EWMA volatility", "Lambda grid selected by QLIKE.", "Realized volatility", {"best_lambda": float(ewma_df.iloc[0]["lambda"]), "qlike": float(ewma_df.iloc[0]["qlike"])}, "Fast baseline for live volatility tracking.")

    kal = vev[["day", "timestamp", "global_tick", "mid_price", "spread"]].copy()
    q = float(vev.groupby("day")["mid_price"].diff().var() * 0.05)
    r_obs = float((vev["spread"].median() / 2) ** 2)
    filt_vals = []
    pred_vals = []
    for _, group in kal.groupby("day"):
        x_hat = float(group["mid_price"].iloc[0])
        p_var = r_obs
        for obs in group["mid_price"]:
            pred_vals.append(x_hat)
            p_var = p_var + q
            k_gain = p_var / (p_var + r_obs)
            x_hat = x_hat + k_gain * (float(obs) - x_hat)
            p_var = (1 - k_gain) * p_var
            filt_vals.append(x_hat)
    kal["kalman_pred"] = pred_vals
    kal["kalman_fair"] = filt_vals
    write_table(kal, out.tables / "model_kalman_fair_value.csv")
    add_model(10, "Kalman local-level fair value", "Scalar local-level filter with spread-derived observation noise.", "VELVETFRUIT_EXTRACT fair value", regression_metrics(kal["mid_price"], kal["kalman_pred"], 2), "Use filtered spot for Greeks only if it reduces hedge residuals in backtest.")

    # M11-M16: order-flow and arrival models.
    coef_imb, pred_imb, imb_metrics = linear_fit(pd.DataFrame({"imbalance": vev["imbalance"]}), vev["future_ret_10"])
    add_model(11, "Order-book imbalance linear model", "OLS future 10-tick return on top-level imbalance.", "Forward returns", imb_metrics | {"coef": float(coef_imb[1]) if len(coef_imb) > 1 else float("nan")}, "Standalone imbalance has weak explanatory power.")

    bucket = vev[["imbalance", "total_depth", "future_ret_10"]].dropna().copy()
    bucket["imb_bucket"] = pd.qcut(bucket["imbalance"].rank(method="first"), 5, labels=False)
    bucket["depth_bucket"] = pd.qcut(bucket["total_depth"].rank(method="first"), 5, labels=False)
    bucket_mean = bucket.groupby(["imb_bucket", "depth_bucket"], observed=False)["future_ret_10"].mean().rename("bucket_pred").reset_index()
    bucket = bucket.merge(bucket_mean, on=["imb_bucket", "depth_bucket"], how="left")
    write_table(bucket_mean, out.tables / "model_nonlinear_book_buckets.csv")
    add_model(12, "Nonlinear book-feature bucket model", "5x5 imbalance/depth bucket means.", "Forward returns", regression_metrics(bucket["future_ret_10"], bucket["bucket_pred"], 25), "Nonlinear buckets are better as filters than as direct forecasts.")

    cls = vev[["imbalance", "future_ret_10"]].dropna().copy()
    cls["up"] = (cls["future_ret_10"] > 0).astype(int)
    z = (cls["imbalance"] - cls["imbalance"].mean()) / (cls["imbalance"].std() or 1)
    cls["probit_score"] = norm.cdf(z)
    eps = 1e-9
    logloss = float(-(cls["up"] * np.log(cls["probit_score"].clip(eps, 1 - eps)) + (1 - cls["up"]) * np.log((1 - cls["probit_score"]).clip(eps, 1 - eps))).mean())
    add_model(13, "Ordered/probit direction proxy", "Probit score from standardized imbalance for up/down future move.", "Move direction", {"auc": binary_auc(cls["up"], cls["probit_score"]), "logloss": logloss}, "Classification calibration must beat a 50/50 baseline after costs.")

    vt = pd.read_csv(out.tables / "velvetfruit_trade_enriched.csv")
    count_rows = []
    for day, group in vt.groupby("day"):
        counts = pd.cut(group["timestamp"], bins=np.arange(0, 1_000_001, 1000), include_lowest=True).value_counts().sort_index()
        count_rows.append({"day": day, "mean": counts.mean(), "variance": counts.var(), "dispersion": counts.var() / counts.mean() if counts.mean() else np.nan})
    count_df = pd.DataFrame(count_rows)
    write_table(count_df, out.tables / "model_poisson_trade_arrivals.csv")
    add_model(14, "Poisson / negative-binomial trade arrivals", "1000-timestamp trade-count dispersion.", "Trade arrivals", {"mean_dispersion": float(count_df["dispersion"].mean())}, "Arrival process is close to Poisson, not strongly clustered.")

    hawkes_rows = []
    for day, group in vt.groupby("day"):
        counts = pd.cut(group["timestamp"], bins=np.arange(0, 1_000_001, 1000), include_lowest=True).value_counts().sort_index().astype(float)
        hawkes_rows.append({"day": day, "lag1_count_acf": counts.autocorr(1), "branching_proxy": max(float(counts.autocorr(1)), 0.0) if np.isfinite(counts.autocorr(1)) else np.nan})
    hawkes_df = pd.DataFrame(hawkes_rows)
    write_table(hawkes_df, out.tables / "model_hawkes_proxy.csv")
    add_model(15, "Hawkes trade-clustering proxy", "Lag-1 autocorrelation of binned trade counts as branching proxy.", "Trade arrivals", {"branching_proxy": float(hawkes_df["branching_proxy"].mean())}, "Self-excitation appears modest in these historical days.")

    dur = vt.sort_values(["day", "timestamp"]).copy()
    dur["duration"] = dur.groupby("day")["timestamp"].diff()
    dur["lag_duration"] = dur.groupby("day")["duration"].shift(1)
    coef_dur, pred_dur, dur_metrics = linear_fit(dur[["lag_duration"]], dur["duration"])
    add_model(16, "Autoregressive conditional duration proxy", "Interarrival duration AR(1).", "Trade durations", dur_metrics | {"duration_phi": float(coef_dur[1]) if len(coef_dur) > 1 else float("nan")}, "Duration persistence is a fill-risk feature, not a standalone trade.")

    # M17-M27: option surface, no-arb, residual, and fill models.
    if not tfv.empty:
        fair_stats = regression_metrics(tfv["trade_price"], tfv["bs_fair_value"], 1)
        fair_stats["mean_trade_minus_fv"] = float(tfv["trade_minus_bs_fv"].mean())
        fair_stats["valid_trades"] = int(tfv["trade_minus_bs_fv"].notna().sum())
    else:
        fair_stats = {"valid_trades": 0}
    add_model(17, "Black-Scholes with empirical IV", "Trade prices compared to BS fair value at trade timestamp.", "Voucher fair values", fair_stats, "Good anchor for liquid near-ATM strikes; weak for floor-price OTM vouchers.")

    smooth = pd.read_csv(out.tables / "voucher_iv_smile_smoothness.csv")
    add_model(18, "Constrained quadratic IV smile", "Quadratic smile R2 per timestamp.", "IV surface", {"mean_smile_r2": float(smooth["smile_r2"].mean()), "p05_smile_r2": float(smooth["smile_r2"].quantile(0.05))}, "The smile is smooth enough for surface-fitted fair values.")

    surf = iv_df.dropna(subset=["iv"]).copy()
    surf["log_moneyness"] = np.log(surf["spot_mid"] / surf["strike"])
    surf["sqrt_term"] = np.sqrt(surf["log_moneyness"] ** 2 + 1e-5)
    coef_svi, pred_svi, svi_metrics = linear_fit(surf[["log_moneyness", "sqrt_term"]], surf["iv"])
    add_model(19, "SVI-style smile proxy", "Linearized total-variance proxy using log-moneyness and sqrt curvature term.", "IV surface", svi_metrics, "Useful lightweight alternative to raw per-strike IV.")

    surf["tte_years"] = surf["tte_days"] / 365.0
    surf["m2"] = surf["log_moneyness"] ** 2
    surf["m_t"] = surf["log_moneyness"] * surf["tte_years"]
    coef_sabr, pred_sabr, sabr_metrics = linear_fit(surf[["log_moneyness", "m2", "tte_years", "m_t"]], surf["iv"])
    add_model(20, "SABR-inspired smile proxy", "Polynomial moneyness/TTE proxy for smile and term effects.", "IV surface", sabr_metrics, "Adequate for diagnostics; live strategy should enforce monotonic/convex prices.")

    pca = iv_df.dropna(subset=["iv"]).pivot_table(index=["day", "timestamp"], columns="strike", values="iv", aggfunc="mean")
    pca = pca.dropna(axis=1, thresh=len(pca) * 0.9)
    pca = pca.fillna(pca.median())
    x = pca - pca.mean()
    _, svals, _ = np.linalg.svd(x.to_numpy(), full_matrices=False)
    explained = (svals**2) / (svals**2).sum()
    pca_df = pd.DataFrame({"component": np.arange(1, len(explained) + 1), "explained_variance": explained, "cum_explained_variance": np.cumsum(explained)})
    write_table(pca_df, out.tables / "model_iv_surface_pca.csv")
    plt.figure(figsize=(8, 4))
    plt.bar(pca_df["component"].head(8), pca_df["explained_variance"].head(8))
    plt.plot(pca_df["component"].head(8), pca_df["cum_explained_variance"].head(8), marker="o", color="black")
    plt.title("IV Surface PCA Explained Variance")
    plt.xlabel("Component")
    save_fig(out.charts / "model_iv_surface_pca.png")
    add_model(21, "PCA IV-surface factor model", "SVD on strike-by-timestamp IV matrix.", "IV surface changes", {"pc1_explained": float(explained[0]), "pc3_cum_explained": float(np.cumsum(explained)[min(2, len(explained)-1)])}, "Surface is low-dimensional; factor control can reduce overfitting.")

    means = surf.groupby("voucher")["iv"].mean()
    counts = surf.groupby("voucher")["iv"].count()
    grand = surf["iv"].mean()
    shrink_weight = counts / (counts + 1000)
    shrink = shrink_weight * means + (1 - shrink_weight) * grand
    surf["hier_iv"] = surf["voucher"].map(shrink)
    hier_metrics = regression_metrics(surf["iv"], surf["hier_iv"], len(shrink))
    add_model(22, "Bayesian hierarchical IV shrinkage proxy", "Strike means shrunk toward global IV mean.", "IV surface", hier_metrics, "Shrinkage is useful for sparse strikes and floor-price artifacts.")

    add_model(23, "Delta-hedged residual AR(1)", "Residual autocorrelation from lead-lag section.", "Delta-hedged option residuals", {"mean_abs_residual_acf1": float(leadlag["residual_autocorr_lag1"].abs().mean()), "min_residual_acf1": float(leadlag["residual_autocorr_lag1"].min())}, "Residuals mean-revert; quote edges should be passive and spread-aware.")

    add_model(24, "Gamma-scalping PnL attribution", "0.5*Gamma*dS^2 plus theta proxy.", "Option gamma portfolios", {"best_net_gamma_scalp": float(top_gamma["net_gamma_scalp_pnl"].max()), "positive_strikes": int((top_gamma["net_gamma_scalp_pnl"] > 0).sum())}, "Long near-ATM gamma is the central historical hypothesis.")

    surv = noarb[noarb["count"] > 0].copy()
    write_table(surv, out.tables / "model_noarb_survival.csv")
    plt.figure(figsize=(10, 4))
    plt.bar(surv["voucher"].astype(str) + "_d" + surv["day"].astype(str), surv["avg_lifetime_ticks"])
    plt.xticks(rotation=45, ha="right")
    plt.title("No-Arbitrage Flag Average Lifetime")
    plt.ylabel("Ticks")
    save_fig(out.charts / "model_noarb_persistence.png")
    add_model(25, "No-arbitrage persistence survival proxy", "Counts and average run length of no-arb flags.", "No-arb signals", {"total_flags": noarb_count, "mean_lifetime": float(surv["avg_lifetime_ticks"].mean())}, "Most flags are short-lived midpoint artifacts; persistence filter required.")

    add_model(26, "Constrained cross-sectional option-pricing regression", "Quadratic smile smoothness and no-arb flags as constraints.", "Option cross-section", {"mean_smile_r2": float(smooth["smile_r2"].mean()), "noarb_flags": noarb_count}, "Fit constrained smiles before trading cross-strike discrepancies.")

    fill_frames = []
    for product in VOUCHERS:
        p = series_for(prices, product).copy()
        t = trades[trades["symbol"] == product][["day", "timestamp"]].assign(traded=1)
        p["spread"] = p["ask_price_1"] - p["bid_price_1"]
        p["mean_depth"] = p[["bid_volume_1", "ask_volume_1"]].mean(axis=1)
        f = p.merge(t, on=["day", "timestamp"], how="left")
        f["traded"] = f["traded"].fillna(0)
        f["fill_score"] = f["mean_depth"] / (1.0 + f["spread"])
        f["voucher"] = product
        fill_frames.append(f[["voucher", "day", "timestamp", "traded", "spread", "mean_depth", "fill_score"]])
    fill_df = pd.concat(fill_frames, ignore_index=True)
    fill_auc = binary_auc(fill_df["traded"], fill_df["fill_score"])
    fill_summary = fill_df.groupby("voucher").agg(trade_rate=("traded", "mean"), mean_fill_score=("fill_score", "mean"), mean_spread=("spread", "mean")).reset_index()
    write_table(fill_summary, out.tables / "model_fill_probability.csv")
    plt.figure(figsize=(10, 4))
    plt.scatter(fill_summary["mean_fill_score"], fill_summary["trade_rate"])
    for _, row in fill_summary.iterrows():
        plt.text(row["mean_fill_score"], row["trade_rate"], row["voucher"], fontsize=7)
    plt.title("Voucher Fill Proxy: Trade Rate vs Depth/Spread Score")
    plt.xlabel("Mean depth / (1 + spread)")
    plt.ylabel("Historical trade rate")
    save_fig(out.charts / "model_fill_probability.png")
    add_model(27, "Voucher fill-probability model", "Depth/spread score with trade-at-timestamp labels.", "Voucher fills", {"auc": fill_auc, "mean_trade_rate": float(fill_df["traded"].mean())}, "Fill probability needs more features, but depth/spread is a usable baseline.")

    # M28-M30: strategy-control models.
    add_model(28, "Avellaneda-Stoikov market-making proxy", "Spread capture, RV, and buy-flow asymmetry diagnostics.", "Market-making controls", {"spread_mean": spread_mean, "rv500_mean": rv500_mean, "ask_fill_fraction": buy_bias}, "Underlying can hedge options, but quote width must include adverse-selection and inventory.")

    median_g = greeks_ts.groupby("voucher")[["bs_delta", "bs_gamma"]].median().reset_index() if not greeks_ts.empty else pd.DataFrame()
    if not median_g.empty:
        median_g["abs_delta_capacity"] = median_g["bs_delta"].abs() * 300
        median_g["gamma_capacity"] = median_g["bs_gamma"].abs() * 300
        write_table(median_g, out.tables / "model_lq_inventory_capacity.csv")
        max_gamma_capacity = float(median_g["gamma_capacity"].max())
        max_delta_capacity = float(median_g["abs_delta_capacity"].max())
    else:
        max_gamma_capacity = max_delta_capacity = float("nan")
    add_model(29, "Linear-quadratic Greek inventory control proxy", "Position-limit weighted delta/gamma capacity by voucher.", "Inventory control", {"max_gamma_capacity": max_gamma_capacity, "max_delta_capacity": max_delta_capacity}, "Inventory control must reserve underlying limit for hedging near-ATM gamma.")

    ensemble = pd.DataFrame({
        "signal": ["iv_rv_gap", "gamma_scalp", "residual_mean_reversion", "flow_toxicity", "liquidity"],
        "score": [abs(iv_rv_gap), float(top_gamma["net_gamma_scalp_pnl"].max()) / 30.0, float(leadlag["residual_autocorr_lag1"].abs().mean()), abs(float(vev_ctx["toxicity"]["mean_forward_return"].mean())) * 1000, 1.0 / (1.0 + float(liquidity["spread_pct_mid"].replace([np.inf, -np.inf], np.nan).median()))],
    })
    write_table(ensemble, out.tables / "model_ensemble_signal_scores.csv")
    plt.figure(figsize=(9, 4))
    plt.bar(ensemble["signal"], ensemble["score"])
    plt.xticks(rotation=25, ha="right")
    plt.title("Ensemble Signal Scores")
    save_fig(out.charts / "model_ensemble_signal_scores.png")
    add_model(30, "Walk-forward ensemble proxy", "Combined IV/RV, gamma, residual, flow, and liquidity signal scores.", "Strategy selection", {"mean_signal_score": float(ensemble["score"].mean()), "max_signal_score": float(ensemble["score"].max())}, "Best first production candidate combines long-gamma, passive fills, and strict hedge controls.")

    model_df = pd.DataFrame(model_rows)
    write_table(model_df, out.tables / "model_diagnostics.csv")
    ranked = model_df.copy()
    ranked["rank_score"] = pd.to_numeric(ranked["primary_value"], errors="coerce").abs().rank(pct=True)
    write_table(ranked.sort_values("rank_score", ascending=False), out.tables / "model_rankings.csv")
    plt.figure(figsize=(12, 5))
    plot_df = ranked.sort_values("rank_score", ascending=False)
    plt.bar(plot_df["model_id"], plot_df["rank_score"])
    plt.title("Model Diagnostic Rank Score (Scale-Free, Direction-Agnostic)")
    plt.xlabel("Model")
    plt.ylabel("Rank score")
    save_fig(out.charts / "model_rankings.png")

    # Focused quantitative tests used to answer the research questions.
    research_tables: dict[str, pd.DataFrame] = {}

    def store_research(name: str, df: pd.DataFrame) -> pd.DataFrame:
        write_table(df, out.tables / name)
        research_tables[name] = df
        return df

    def join_future_mid(df: pd.DataFrame, horizon: int) -> pd.DataFrame:
        base = df[["day", "timestamp", "mid_price"]].copy()
        base["future_timestamp"] = base["timestamp"] + horizon
        fut = df[["day", "timestamp", "mid_price"]].rename(columns={"timestamp": "future_timestamp", "mid_price": "future_mid"})
        out_df = base.merge(fut, on=["day", "future_timestamp"], how="left")
        out_df[f"forward_ret_{horizon}"] = np.log(out_df["future_mid"]) - np.log(out_df["mid_price"])
        return out_df[["day", "timestamp", f"forward_ret_{horizon}", "future_mid"]]

    def fit_summary(df: pd.DataFrame, features: list[str], target: str, label: str) -> dict[str, Any]:
        clean = df[features + [target]].replace([np.inf, -np.inf], np.nan).dropna()
        if len(clean) < len(features) + 2:
            return {"label": label, "n": len(clean), "r2": np.nan, "rmse": np.nan, "mae": np.nan}
        _, _, metrics = linear_fit(clean[features], clean[target])
        out_row: dict[str, Any] = {"label": label, **metrics}
        for feature in features:
            out_row[f"corr_{feature}"] = safe_corr(clean[feature], clean[target])
        return out_row

    def draw_bar(df: pd.DataFrame, x_col: str, y_col: str, path: Path, title: str, rotation: int = 30) -> None:
        if df.empty or x_col not in df or y_col not in df:
            return
        plt.figure(figsize=(10, 4))
        plt.bar(df[x_col].astype(str), df[y_col])
        plt.xticks(rotation=rotation, ha="right")
        plt.title(title)
        save_fig(path)

    # IV/RV mean-reversion and EMA signal diagnostics.
    atm_full = opt_ctx["atm_iv"].merge(rv[["day", "timestamp", "rv_100", "rv_500", "rv_2000"]], on=["day", "timestamp"], how="left")
    for window in [100, 500, 2000]:
        atm_full[f"iv_minus_rv_{window}"] = atm_full["iv"] - atm_full[f"rv_{window}"]
    iv_rv_window_gaps = atm_full.groupby("day")[["iv", "rv_100", "rv_500", "rv_2000", "iv_minus_rv_100", "iv_minus_rv_500", "iv_minus_rv_2000"]].mean().reset_index()
    store_research("research_iv_rv_window_gaps.csv", iv_rv_window_gaps)

    gap_rows = []
    for day, group in atm_full.sort_values(["day", "timestamp"]).groupby("day"):
        g = group[["iv_minus_rv_500"]].copy()
        g["lag_gap"] = g["iv_minus_rv_500"].shift(1)
        coef_gap, _, gap_metrics = linear_fit(g[["lag_gap"]], g["iv_minus_rv_500"])
        phi_gap = float(coef_gap[1]) if len(coef_gap) > 1 else np.nan
        half_life = math.log(0.5) / math.log(abs(phi_gap)) if np.isfinite(phi_gap) and 0 < abs(phi_gap) < 1 else np.nan
        gap_rows.append({"day": day, "phi": phi_gap, "half_life_ticks": half_life, **gap_metrics})
    gap_mr = store_research("research_iv_gap_mean_reversion.csv", pd.DataFrame(gap_rows))

    atm_ema = pd.read_csv(out.tables / "voucher_atm_iv_ema.csv")
    ema_signal_rows = []
    if {"iv", "iv_ema", "iv_ema_std"}.issubset(atm_ema.columns):
        signal_df = atm_ema.sort_values(["day", "timestamp"]).copy()
        signal_df["iv_z"] = (signal_df["iv"] - signal_df["iv_ema"]) / signal_df["iv_ema_std"].replace(0, np.nan)
        for horizon in [100, 500, 1000]:
            signal_df[f"future_iv_{horizon}"] = signal_df.groupby("day")["iv"].shift(-horizon // TIMESTAMP_STEP)
            signal_df[f"iv_change_{horizon}"] = signal_df[f"future_iv_{horizon}"] - signal_df["iv"]
            for signal_name, mask, desired_sign in [
                ("iv_below_ema_2std", signal_df["iv_z"] <= -2, 1),
                ("iv_above_ema_2std", signal_df["iv_z"] >= 2, -1),
            ]:
                sub = signal_df[mask & signal_df[f"iv_change_{horizon}"].notna()]
                ema_signal_rows.append({
                    "signal": signal_name,
                    "horizon_timestamp": horizon,
                    "count": int(len(sub)),
                    "mean_iv_change": float(sub[f"iv_change_{horizon}"].mean()) if len(sub) else np.nan,
                    "median_iv_change": float(sub[f"iv_change_{horizon}"].median()) if len(sub) else np.nan,
                    "hit_rate_expected_reversion": float((np.sign(sub[f"iv_change_{horizon}"]) == desired_sign).mean()) if len(sub) else np.nan,
                })
    ema_signals = store_research("research_atm_iv_ema_signals.csv", pd.DataFrame(ema_signal_rows))

    # Underlying microstructure predictive features: top imbalance, microprice, deep VAMP, DOM-mid, spread, depth.
    vev_micro = vev.sort_values(["day", "timestamp"]).copy()
    bid_px = [f"bid_price_{i}" for i in [1, 2, 3]]
    ask_px = [f"ask_price_{i}" for i in [1, 2, 3]]
    bid_vol = [f"bid_volume_{i}" for i in [1, 2, 3]]
    ask_vol = [f"ask_volume_{i}" for i in [1, 2, 3]]
    vev_micro["microprice"] = (
        vev_micro["ask_price_1"] * vev_micro["bid_volume_1"] + vev_micro["bid_price_1"] * vev_micro["ask_volume_1"]
    ) / (vev_micro["bid_volume_1"] + vev_micro["ask_volume_1"])
    deep_weighted = pd.Series(0.0, index=vev_micro.index)
    deep_volume = pd.Series(0.0, index=vev_micro.index)
    bid_weighted = pd.Series(0.0, index=vev_micro.index)
    bid_volume_valid = pd.Series(0.0, index=vev_micro.index)
    ask_weighted = pd.Series(0.0, index=vev_micro.index)
    ask_volume_valid = pd.Series(0.0, index=vev_micro.index)
    for i in [1, 2, 3]:
        bid_v = vev_micro[f"bid_volume_{i}"].where(vev_micro[f"ask_price_{i}"].notna(), 0).fillna(0)
        ask_v = vev_micro[f"ask_volume_{i}"].where(vev_micro[f"bid_price_{i}"].notna(), 0).fillna(0)
        deep_weighted = deep_weighted + vev_micro[f"ask_price_{i}"].fillna(0) * bid_v + vev_micro[f"bid_price_{i}"].fillna(0) * ask_v
        deep_volume = deep_volume + bid_v + ask_v
        bid_side_v = vev_micro[f"bid_volume_{i}"].where(vev_micro[f"bid_price_{i}"].notna(), 0).fillna(0)
        ask_side_v = vev_micro[f"ask_volume_{i}"].where(vev_micro[f"ask_price_{i}"].notna(), 0).fillna(0)
        bid_weighted = bid_weighted + vev_micro[f"bid_price_{i}"].fillna(0) * bid_side_v
        bid_volume_valid = bid_volume_valid + bid_side_v
        ask_weighted = ask_weighted + vev_micro[f"ask_price_{i}"].fillna(0) * ask_side_v
        ask_volume_valid = ask_volume_valid + ask_side_v
    vev_micro["deep_vamp"] = deep_weighted / deep_volume.replace(0, np.nan)
    bid_vwap = bid_weighted / bid_volume_valid.replace(0, np.nan)
    ask_vwap = ask_weighted / ask_volume_valid.replace(0, np.nan)
    vev_micro["dom_mid"] = (bid_vwap + ask_vwap) / 2.0
    vev_micro["microprice_minus_mid"] = vev_micro["microprice"] - vev_micro["mid_price"]
    vev_micro["deep_vamp_minus_mid"] = vev_micro["deep_vamp"] - vev_micro["mid_price"]
    vev_micro["dom_mid_minus_mid"] = vev_micro["dom_mid"] - vev_micro["mid_price"]
    vev_micro["quote_change"] = (
        vev_micro.groupby("day")["bid_price_1"].diff().fillna(0).ne(0).astype(float)
        + vev_micro.groupby("day")["ask_price_1"].diff().fillna(0).ne(0).astype(float)
    )
    for horizon_rows in [10, 100]:
        vev_micro[f"future_ret_{horizon_rows}"] = vev_micro.groupby("day")["mid_price"].transform(lambda x: forward_log_returns(x, horizon_rows))
        vev_micro[f"future_abs_ret_{horizon_rows}"] = vev_micro[f"future_ret_{horizon_rows}"].abs()
    micro_features = ["imbalance", "total_depth", "spread", "microprice_minus_mid", "deep_vamp_minus_mid", "dom_mid_minus_mid", "quote_change"]
    micro_rows = []
    for target in ["future_ret_10", "future_ret_100", "future_abs_ret_10", "future_abs_ret_100"]:
        for feature in micro_features:
            clean = vev_micro[[feature, target]].replace([np.inf, -np.inf], np.nan).dropna()
            coef_feature, _, met_feature = linear_fit(clean[[feature]], clean[target]) if len(clean) else (np.array([]), np.array([]), regression_metrics([], []))
            micro_rows.append({
                "target": target,
                "feature_set": feature,
                "n": int(met_feature.get("n", len(clean))),
                "r2": float(met_feature.get("r2", np.nan)),
                "rmse": float(met_feature.get("rmse", np.nan)),
                "corr": safe_corr(clean[feature], clean[target]) if len(clean) else np.nan,
                "coef": float(coef_feature[1]) if len(coef_feature) > 1 else np.nan,
            })
        micro_rows.append(fit_summary(vev_micro, micro_features, target, f"multivariate_{target}") | {"target": target, "feature_set": "all_micro_features", "corr": np.nan, "coef": np.nan})
    micro_pred = store_research("research_micro_predictive_features.csv", pd.DataFrame(micro_rows))
    micro_plot = micro_pred[micro_pred["target"].eq("future_ret_10") & ~micro_pred["feature_set"].eq("all_micro_features")].copy()
    micro_plot["abs_corr"] = micro_plot["corr"].abs()
    draw_bar(micro_plot.sort_values("abs_corr", ascending=False), "feature_set", "abs_corr", out.charts / "research_micro_feature_abs_corr.png", "Microstructure Feature Absolute Correlation With Future 10-Row Return")

    # De-bounced and filtered realized volatility.
    filt = kal[["day", "timestamp", "global_tick", "kalman_fair"]].merge(vev[["day", "timestamp", "mid_price"]], on=["day", "timestamp"], how="left")
    filt["raw_ret"] = grouped_log_returns(filt.rename(columns={"mid_price": "raw_mid"}), "raw_mid", 1)
    filt["kalman_ret"] = grouped_log_returns(filt, "kalman_fair", 1)
    filt["smooth_mid"] = filt.groupby("day")["mid_price"].transform(lambda x: x.rolling(5, min_periods=1).mean())
    filt["smooth_ret"] = grouped_log_returns(filt, "smooth_mid", 1)
    filt["nonzero_raw_ret"] = filt["raw_ret"].where(filt["raw_ret"].abs() > 0)
    filtered_rv_rows = []
    for ret_col in ["raw_ret", "kalman_ret", "smooth_ret", "nonzero_raw_ret"]:
        for day, group in filt.groupby("day"):
            filtered_rv_rows.append({
                "rv_source": ret_col,
                "day": day,
                "annualized_std": float(group[ret_col].std() * math.sqrt(TICKS_PER_DAY * 365)),
                "zero_fraction": float((group[ret_col].fillna(0) == 0).mean()),
                "n": int(group[ret_col].notna().sum()),
            })
    filtered_rv = store_research("research_filtered_rv.csv", pd.DataFrame(filtered_rv_rows))
    draw_bar(filtered_rv.groupby("rv_source", as_index=False)["annualized_std"].mean(), "rv_source", "annualized_std", out.charts / "research_filtered_rv.png", "Realized Volatility By De-Bouncing Method")

    # Spread/volatility lead diagnostics.
    spread_lead_rows = []
    spread_rv_join = vev[["day", "timestamp", "spread"]].merge(rv[["day", "timestamp", "rv_500"]], on=["day", "timestamp"], how="left")
    spread_rv_join["rolling_spread_500"] = spread_rv_join.groupby("day")["spread"].transform(lambda x: x.rolling(500, min_periods=50).mean())
    for horizon in [0, 100, 500, 1000, 2000]:
        tmp = spread_rv_join.copy()
        tmp["future_timestamp"] = tmp["timestamp"] + horizon
        future_rv = rv[["day", "timestamp", "rv_500"]].rename(columns={"timestamp": "future_timestamp", "rv_500": "future_rv_500"})
        tmp = tmp.merge(future_rv, on=["day", "future_timestamp"], how="left")
        spread_lead_rows.append(fit_summary(tmp, ["spread"], "future_rv_500", f"spread_to_rv_h{horizon}") | {"horizon_timestamp": horizon, "feature": "spread"})
        spread_lead_rows.append(fit_summary(tmp, ["rolling_spread_500"], "future_rv_500", f"rolling_spread_to_rv_h{horizon}") | {"horizon_timestamp": horizon, "feature": "rolling_spread_500"})
    spread_lead = store_research("research_spread_vol_lead.csv", pd.DataFrame(spread_lead_rows))

    # Intraday buy fraction and trade clustering tests.
    intraday_rows = []
    vt_bin = vt.copy()
    vt_bin["bucket_start"] = (vt_bin["timestamp"] // 1000) * 1000
    for (day, bucket_start), group in vt_bin.groupby(["day", "bucket_start"]):
        buy_qty = group.loc[group["signed_side"].eq("buy"), "quantity"].sum()
        sell_qty = group.loc[group["signed_side"].eq("sell"), "quantity"].sum()
        start_mid = vev.loc[(vev["day"].eq(day)) & (vev["timestamp"].eq(bucket_start)), "mid_price"]
        end_mid = vev.loc[(vev["day"].eq(day)) & (vev["timestamp"].eq(bucket_start + 1000)), "mid_price"]
        next_ret = float(np.log(end_mid.iloc[0]) - np.log(start_mid.iloc[0])) if len(start_mid) and len(end_mid) else np.nan
        intraday_rows.append({
            "day": day,
            "bucket_start": int(bucket_start),
            "total_trades": int(len(group)),
            "buy_trades": int(group["signed_side"].eq("buy").sum()),
            "sell_trades": int(group["signed_side"].eq("sell").sum()),
            "buy_fraction": float(group["signed_side"].eq("buy").mean()),
            "signed_quantity_imbalance": float((buy_qty - sell_qty) / (buy_qty + sell_qty)) if (buy_qty + sell_qty) else np.nan,
            "next_1000_return": next_ret,
        })
    intraday_pred = pd.DataFrame(intraday_rows)
    coef_buy, _, buy_metrics = linear_fit(intraday_pred[["buy_fraction", "total_trades", "signed_quantity_imbalance"]], intraday_pred["next_1000_return"])
    intraday_pred["model_r2"] = buy_metrics.get("r2", np.nan)
    intraday_pred["buy_fraction_corr"] = safe_corr(intraday_pred["buy_fraction"], intraday_pred["next_1000_return"])
    intraday_pred["buy_fraction_coef"] = float(coef_buy[1]) if len(coef_buy) > 1 else np.nan
    intraday_buy = store_research("research_intraday_buy_fraction_predictiveness.csv", intraday_pred)
    plt.figure(figsize=(7, 5))
    plt.scatter(intraday_buy["buy_fraction"], intraday_buy["next_1000_return"], alpha=0.7)
    plt.axhline(0, color="black", lw=0.8)
    plt.title("Intraday Buy Fraction vs Next 1000-Timestamp Return")
    plt.xlabel("Buy fraction")
    plt.ylabel("Next 1000-timestamp log return")
    save_fig(out.charts / "research_intraday_buy_fraction_predictiveness.png")

    cluster_rows = []
    for day, group in intraday_buy.groupby("day"):
        cluster_rows.append({
            "day": day,
            "corr_trade_count_next_abs_return": safe_corr(group["total_trades"], group["next_1000_return"].abs()),
            "corr_buy_fraction_next_return": safe_corr(group["buy_fraction"], group["next_1000_return"]),
            "mean_abs_return_top_count_quintile": float(group[group["total_trades"] >= group["total_trades"].quantile(0.8)]["next_1000_return"].abs().mean()),
            "mean_abs_return_other_buckets": float(group[group["total_trades"] < group["total_trades"].quantile(0.8)]["next_1000_return"].abs().mean()),
        })
    trade_cluster_moves = store_research("research_trade_cluster_price_moves.csv", pd.DataFrame(cluster_rows))

    # Underlying trade event studies.
    event_rows = []
    passive_rows = []
    vt_events = vt.copy()
    vt_events["outside_top"] = (vt_events["price"] < vt_events["bid_price_1"]) | (vt_events["price"] > vt_events["ask_price_1"])
    for horizon in [100, 500, 1000]:
        fwd = join_future_mid(vev, horizon).rename(columns={f"forward_ret_{horizon}": "forward_return"})
        e = vt_events.merge(fwd[["day", "timestamp", "forward_return", "future_mid"]], on=["day", "timestamp"], how="left")
        for outside_flag, group in e.groupby("outside_top"):
            event_rows.append({
                "horizon_timestamp": horizon,
                "outside_top": bool(outside_flag),
                "count": int(len(group)),
                "mean_forward_return": float(group["forward_return"].mean()),
                "median_forward_return": float(group["forward_return"].median()),
                "mean_abs_forward_return": float(group["forward_return"].abs().mean()),
                "positive_rate": float((group["forward_return"] > 0).mean()),
            })
        bid_fills = e[e["trade_minus_mid"] < 0].copy()
        ask_fills = e[e["trade_minus_mid"] > 0].copy()
        passive_rows.append({
            "horizon_timestamp": horizon,
            "maker_side": "buy_at_bid_after_sell_pressure",
            "count": int(len(bid_fills)),
            "mean_mid_mark_pnl": float((bid_fills["future_mid"] - bid_fills["price"]).mean()),
            "median_mid_mark_pnl": float((bid_fills["future_mid"] - bid_fills["price"]).median()),
            "positive_rate": float(((bid_fills["future_mid"] - bid_fills["price"]) > 0).mean()) if len(bid_fills) else np.nan,
        })
        passive_rows.append({
            "horizon_timestamp": horizon,
            "maker_side": "sell_at_ask_after_buy_pressure",
            "count": int(len(ask_fills)),
            "mean_mid_mark_pnl": float((ask_fills["price"] - ask_fills["future_mid"]).mean()),
            "median_mid_mark_pnl": float((ask_fills["price"] - ask_fills["future_mid"]).median()),
            "positive_rate": float(((ask_fills["price"] - ask_fills["future_mid"]) > 0).mean()) if len(ask_fills) else np.nan,
        })
    outside_event = store_research("research_outside_top_trade_event_study.csv", pd.DataFrame(event_rows))
    passive_underlying = store_research("research_underlying_passive_pressure.csv", pd.DataFrame(passive_rows))

    # Voucher execution, flow, IV event studies, and side prediction.
    book_lookup = prices[prices["product"].isin(VOUCHERS)][["day", "timestamp", "product", "bid_price_1", "ask_price_1", "bid_volume_1", "ask_volume_1", "mid_price"]].rename(columns={"product": "voucher", "mid_price": "voucher_mid"})
    tfv_exec = tfv.merge(book_lookup, on=["day", "timestamp", "voucher"], how="left") if not tfv.empty else pd.DataFrame()
    if not tfv_exec.empty:
        tfv_exec["at_bid"] = (tfv_exec["trade_price"] - tfv_exec["bid_price_1"]).abs() < 1e-9
        tfv_exec["at_ask"] = (tfv_exec["trade_price"] - tfv_exec["ask_price_1"]).abs() < 1e-9
        tfv_exec["trade_side"] = np.where(tfv_exec["at_ask"], "buyer_initiated", np.where(tfv_exec["at_bid"], "seller_initiated", "inside_or_unknown"))
        tfv_exec["maker_buy_edge"] = tfv_exec["bs_fair_value"] - tfv_exec["bid_price_1"]
        tfv_exec["maker_sell_edge"] = tfv_exec["ask_price_1"] - tfv_exec["bs_fair_value"]
        fav_rows = []
        for product, group in tfv_exec.groupby("voucher"):
            bid_group = group[group["at_bid"]]
            ask_group = group[group["at_ask"]]
            fav_rows.append({
                "voucher": product,
                "maker_side": "buy_at_bid",
                "count": int(len(bid_group)),
                "positive_model_edge_count": int((bid_group["maker_buy_edge"] > 0).sum()),
                "positive_model_edge_rate": float((bid_group["maker_buy_edge"] > 0).mean()) if len(bid_group) else np.nan,
                "mean_model_edge": float(bid_group["maker_buy_edge"].mean()) if len(bid_group) else np.nan,
            })
            fav_rows.append({
                "voucher": product,
                "maker_side": "sell_at_ask",
                "count": int(len(ask_group)),
                "positive_model_edge_count": int((ask_group["maker_sell_edge"] > 0).sum()),
                "positive_model_edge_rate": float((ask_group["maker_sell_edge"] > 0).mean()) if len(ask_group) else np.nan,
                "mean_model_edge": float(ask_group["maker_sell_edge"].mean()) if len(ask_group) else np.nan,
            })
        passive_voucher = store_research("research_voucher_passive_favorable_fills.csv", pd.DataFrame(fav_rows))

        flow_rows = []
        tfv_exec["time_bin_idx"] = pd.cut(tfv_exec["timestamp"], bins=np.linspace(0, 999_900, 21), include_lowest=True, labels=False)
        for (voucher, day, bin_idx), group in tfv_exec.groupby(["voucher", "day", "time_bin_idx"], observed=False):
            if pd.isna(bin_idx):
                continue
            flow_rows.append({
                "voucher": voucher,
                "day": day,
                "bin_idx": int(bin_idx),
                "trade_count": int(len(group)),
                "pct_at_bid": float(group["at_bid"].mean()),
                "pct_at_ask": float(group["at_ask"].mean()),
                "mean_quantity": float(group["quantity"].mean()),
            })
        voucher_flow_time = store_research("research_voucher_flow_timeofday.csv", pd.DataFrame(flow_rows))

        event_iv_rows = []
        for horizon in [100, 500, 1000]:
            fut_iv = iv_df[["day", "timestamp", "voucher", "iv"]].rename(columns={"timestamp": "future_timestamp", "iv": "future_iv"})
            eiv = tfv_exec.copy()
            eiv["future_timestamp"] = eiv["timestamp"] + horizon
            eiv = eiv.merge(fut_iv, on=["day", "future_timestamp", "voucher"], how="left")
            eiv["future_iv_change"] = eiv["future_iv"] - eiv["iv_at_trade"]
            eiv["size_bucket"] = pd.qcut(eiv["quantity"].rank(method="first"), 4, labels=["q1_small", "q2", "q3", "q4_large"])
            for keys, group in eiv.groupby(["voucher", "trade_side", "size_bucket"], observed=False):
                voucher, trade_side, size_bucket = keys
                event_iv_rows.append({
                    "horizon_timestamp": horizon,
                    "voucher": voucher,
                    "trade_side": trade_side,
                    "size_bucket": str(size_bucket),
                    "count": int(len(group)),
                    "mean_iv_change": float(group["future_iv_change"].mean()),
                    "median_iv_change": float(group["future_iv_change"].median()),
                    "positive_iv_change_rate": float((group["future_iv_change"] > 0).mean()) if len(group) else np.nan,
                    "mean_quantity": float(group["quantity"].mean()),
                })
        voucher_trade_iv = store_research("research_voucher_trade_event_iv.csv", pd.DataFrame(event_iv_rows))

        side_rows = []
        side_df = tfv_exec[tfv_exec["trade_side"].isin(["buyer_initiated", "seller_initiated"])].copy()
        side_df["volume_imbalance"] = (side_df["bid_volume_1"] - side_df["ask_volume_1"]) / (side_df["bid_volume_1"] + side_df["ask_volume_1"])
        side_df["target_buyer_initiated"] = side_df["trade_side"].eq("buyer_initiated").astype(int)
        for voucher, group in side_df.groupby("voucher"):
            side_rows.append({
                "voucher": voucher,
                "count": int(len(group)),
                "auc_volume_imbalance_predicts_buyer": binary_auc(group["target_buyer_initiated"], group["volume_imbalance"]),
                "corr_volume_imbalance_side": safe_corr(group["volume_imbalance"], group["target_buyer_initiated"]),
                "mean_volume_imbalance": float(group["volume_imbalance"].mean()),
                "buyer_initiated_rate": float(group["target_buyer_initiated"].mean()),
            })
        voucher_side_pred = store_research("research_voucher_volume_side_prediction.csv", pd.DataFrame(side_rows))
    else:
        passive_voucher = store_research("research_voucher_passive_favorable_fills.csv", pd.DataFrame())
        voucher_flow_time = store_research("research_voucher_flow_timeofday.csv", pd.DataFrame())
        voucher_trade_iv = store_research("research_voucher_trade_event_iv.csv", pd.DataFrame())
        voucher_side_pred = store_research("research_voucher_volume_side_prediction.csv", pd.DataFrame())

    # Delta-hedged residuals, filtered spot impact, and fitted smile residuals.
    residual_frames = []
    residual_day_rows = []
    for product in VOUCHERS:
        v = add_global_tick(series_for(prices, product)).merge(
            vev[["day", "timestamp", "mid_price"]].rename(columns={"mid_price": "spot_mid"}),
            on=["day", "timestamp"],
            how="left",
        )
        g = greeks_ts[greeks_ts["voucher"].eq(product)][["day", "timestamp", "bs_delta", "bs_gamma", "iv"]]
        v = v.merge(g, on=["day", "timestamp"], how="left")
        v["voucher_ret"] = grouped_log_returns(v, "mid_price", 1)
        v["spot_ret"] = grouped_log_returns(v, "spot_mid", 1)
        v["raw_delta_residual"] = v["voucher_ret"] - v["bs_delta"] * v["spot_ret"]
        v["voucher"] = product
        residual_frames.append(v[["voucher", "day", "timestamp", "global_tick", "raw_delta_residual", "bs_delta", "bs_gamma", "spot_mid", "mid_price", "iv"]])
        for day, group in v.groupby("day"):
            rday = group["raw_delta_residual"].dropna()
            if len(rday) == 0:
                continue
            group = group.copy()
            group["time_bin_idx"] = pd.cut(group["timestamp"], bins=np.linspace(0, 999_900, 6), include_lowest=True, labels=False)
            late_std = group[group["time_bin_idx"].eq(4)]["raw_delta_residual"].std()
            early_std = group[group["time_bin_idx"].eq(0)]["raw_delta_residual"].std()
            residual_day_rows.append({
                "voucher": product,
                "day": day,
                "residual_std": float(rday.std()),
                "residual_mean": float(rday.mean()),
                "residual_acf1": float(rday.autocorr(1)) if len(rday) > 2 else np.nan,
                "early_bin_std": float(early_std),
                "late_bin_std": float(late_std),
                "late_minus_early_std": float(late_std - early_std) if np.isfinite(late_std) and np.isfinite(early_std) else np.nan,
            })
    residual_all = pd.concat(residual_frames, ignore_index=True) if residual_frames else pd.DataFrame()
    residual_day = store_research("research_residual_day_stability.csv", pd.DataFrame(residual_day_rows))
    if not residual_all.empty:
        resid_pivot = residual_all.pivot_table(index=["day", "timestamp"], columns="voucher", values="raw_delta_residual", aggfunc="mean")
        resid_corr = resid_pivot.corr().reset_index().rename(columns={"index": "voucher"})
        store_research("research_residual_cross_shock_corr.csv", resid_corr)
        draw_bar(residual_day.groupby("voucher", as_index=False)["residual_std"].mean().sort_values("residual_std", ascending=False), "voucher", "residual_std", out.charts / "research_residual_std_by_voucher.png", "Delta-Hedged Residual Std By Voucher")
    else:
        store_research("research_residual_cross_shock_corr.csv", pd.DataFrame())

    delta_rows = []
    filt_spot = kal[["day", "timestamp", "kalman_fair"]]
    if not greeks_ts.empty:
        delta_df = greeks_ts.merge(filt_spot, on=["day", "timestamp"], how="left")
        for product, group in delta_df.groupby("voucher"):
            k = strike(product)
            raw_delta = group["bs_delta"].to_numpy(dtype=float)
            filt_delta = np.full(len(group), np.nan)
            for day, day_group in group.groupby("day"):
                idx = day_group.index
                d_arr, _, _ = bs_greeks_vec(day_group["kalman_fair"].to_numpy(dtype=float), k, TTE_DAYS[int(day)] / 365.0, day_group["iv"].to_numpy(dtype=float))
                filt_delta[group.index.get_indexer(idx)] = d_arr
            delta_rows.append({
                "voucher": product,
                "mean_abs_delta_change_filtered_minus_raw": float(np.nanmean(np.abs(filt_delta - raw_delta))),
                "p95_abs_delta_change": float(np.nanpercentile(np.abs(filt_delta - raw_delta), 95)),
                "mean_raw_delta": float(np.nanmean(raw_delta)),
                "mean_filtered_delta": float(np.nanmean(filt_delta)),
            })
    filtered_delta = store_research("research_filtered_spot_delta_impact.csv", pd.DataFrame(delta_rows))

    filtered_resid_rows = []
    if not residual_all.empty and not filtered_delta.empty:
        filt_ret = filt[["day", "timestamp", "kalman_ret"]]
        delta_df = greeks_ts.merge(filt_spot, on=["day", "timestamp"], how="left")
        filtered_delta_frames = []
        for product, group in delta_df.groupby("voucher"):
            k = strike(product)
            g = group.copy()
            g["filtered_delta"] = np.nan
            for day, dg in g.groupby("day"):
                d_arr, _, _ = bs_greeks_vec(dg["kalman_fair"].to_numpy(dtype=float), k, TTE_DAYS[int(day)] / 365.0, dg["iv"].to_numpy(dtype=float))
                g.loc[dg.index, "filtered_delta"] = d_arr
            filtered_delta_frames.append(g[["voucher", "day", "timestamp", "filtered_delta"]])
        filtered_delta_ts = pd.concat(filtered_delta_frames, ignore_index=True)
        fres = residual_all.merge(filtered_delta_ts, on=["voucher", "day", "timestamp"], how="left").merge(filt_ret, on=["day", "timestamp"], how="left")
        fres["voucher_ret"] = fres.groupby(["voucher", "day"])["mid_price"].transform(lambda x: log_returns(x, 1))
        fres["filtered_delta_residual"] = fres["voucher_ret"] - fres["filtered_delta"] * fres["kalman_ret"]
        for product, group in fres.groupby("voucher"):
            filtered_resid_rows.append({
                "voucher": product,
                "raw_residual_std": float(group["raw_delta_residual"].std()),
                "filtered_residual_std": float(group["filtered_delta_residual"].std()),
                "std_reduction": float(group["raw_delta_residual"].std() - group["filtered_delta_residual"].std()),
            })
    filtered_resid = store_research("research_filtered_delta_residual_variance.csv", pd.DataFrame(filtered_resid_rows))

    smile_coef_rows = []
    fitted_iv_frames = []
    for (day, timestamp), group in iv_df.dropna(subset=["iv"]).groupby(["day", "timestamp"]):
        g = group.copy()
        g["log_moneyness"] = np.log(g["spot_mid"] / g["strike"])
        if len(g) < 4 or g["log_moneyness"].nunique() < 3:
            continue
        coeffs = np.polyfit(g["log_moneyness"], g["iv"], 2)
        pred_iv = np.polyval(coeffs, g["log_moneyness"])
        ss_res = float(((g["iv"] - pred_iv) ** 2).sum())
        ss_tot = float(((g["iv"] - g["iv"].mean()) ** 2).sum())
        smile_coef_rows.append({
            "day": day,
            "timestamp": timestamp,
            "global_tick": day * 1_000_000 + timestamp,
            "slope": float(coeffs[1]),
            "curvature": float(coeffs[0]),
            "intercept": float(coeffs[2]),
            "smile_r2": 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan,
            "spot_mid": float(g["spot_mid"].iloc[0]),
        })
        tmp = g[["day", "timestamp", "voucher", "strike", "spot_mid", "iv"]].copy()
        tmp["fitted_iv"] = pred_iv
        fitted_iv_frames.append(tmp)
    smile_coef = pd.DataFrame(smile_coef_rows)
    for horizon_rows in [10, 100]:
        smile_coef[f"future_spot_ret_{horizon_rows}"] = smile_coef.groupby("day")["spot_mid"].transform(lambda x: forward_log_returns(x, horizon_rows))
    smile_pred = store_research("research_smile_predictiveness.csv", smile_coef)
    if not smile_pred.empty:
        plt.figure(figsize=(10, 4))
        plt.scatter(smile_pred["slope"], smile_pred["future_spot_ret_10"], s=6, alpha=0.35, label="slope")
        plt.scatter(smile_pred["curvature"], smile_pred["future_spot_ret_10"], s=6, alpha=0.35, label="curvature")
        plt.axhline(0, color="black", lw=0.8)
        plt.title("Smile Slope/Curvature vs Future 10-Row Spot Return")
        plt.legend()
        save_fig(out.charts / "research_smile_predictiveness.png")

    fitted_resid_rows = []
    if fitted_iv_frames:
        fitted_iv_df = pd.concat(fitted_iv_frames, ignore_index=True)
        fitted_greeks = []
        for product, group in fitted_iv_df.groupby("voucher"):
            k = strike(product)
            for day, dg in group.groupby("day"):
                d_arr, _, _ = bs_greeks_vec(dg["spot_mid"].to_numpy(dtype=float), k, TTE_DAYS[int(day)] / 365.0, dg["fitted_iv"].to_numpy(dtype=float))
                out_g = dg[["voucher", "day", "timestamp"]].copy()
                out_g["fitted_delta"] = d_arr
                fitted_greeks.append(out_g)
        fitted_greeks_df = pd.concat(fitted_greeks, ignore_index=True)
        fcomp = residual_all.merge(fitted_greeks_df, on=["voucher", "day", "timestamp"], how="left")
        fcomp["voucher_ret"] = fcomp.groupby(["voucher", "day"])["mid_price"].transform(lambda x: log_returns(x, 1))
        fcomp["spot_ret"] = fcomp.groupby(["voucher", "day"])["spot_mid"].transform(lambda x: log_returns(x, 1))
        fcomp["fitted_delta_residual"] = fcomp["voucher_ret"] - fcomp["fitted_delta"] * fcomp["spot_ret"]
        for product, group in fcomp.groupby("voucher"):
            fitted_resid_rows.append({
                "voucher": product,
                "raw_residual_std": float(group["raw_delta_residual"].std()),
                "fitted_smile_residual_std": float(group["fitted_delta_residual"].std()),
                "std_reduction": float(group["raw_delta_residual"].std() - group["fitted_delta_residual"].std()),
            })
    smile_resid = store_research("research_smile_fitted_residual_variance.csv", pd.DataFrame(fitted_resid_rows))

    # Synthetic forward/deep ITM proxy and theta decomposition.
    forward_rows = []
    for product in ["VEV_4000", "VEV_4500"]:
        proxy = add_global_tick(series_for(prices, product)).merge(
            vev[["day", "timestamp", "mid_price"]].rename(columns={"mid_price": "spot_mid"}),
            on=["day", "timestamp"],
            how="left",
        )
        proxy["synthetic_spot_mid"] = proxy["mid_price"] + strike(product)
        proxy["proxy_minus_spot"] = proxy["synthetic_spot_mid"] - proxy["spot_mid"]
        for day, group in proxy.groupby("day"):
            forward_rows.append({
                "voucher": product,
                "day": day,
                "mean_synthetic_spot_minus_spot": float(group["proxy_minus_spot"].mean()),
                "median_synthetic_spot_minus_spot": float(group["proxy_minus_spot"].median()),
                "std_synthetic_spot_minus_spot": float(group["proxy_minus_spot"].std()),
                "corr_proxy_spot": safe_corr(group["synthetic_spot_mid"], group["spot_mid"]),
            })
    forward_proxy = store_research("research_forward_proxy.csv", pd.DataFrame(forward_rows))

    theta_rows = []
    iv_theta = iv_df.copy()
    iv_theta["intrinsic"] = np.maximum(iv_theta["spot_mid"] - iv_theta["strike"], 0.0)
    iv_theta["extrinsic"] = iv_theta["option_mid"] - iv_theta["intrinsic"]
    iv_theta["bs_theta_per_day"] = [
        bs_greeks(row["spot_mid"], row["strike"], TTE_DAYS[int(row["day"])] / 365.0, row["iv"])["theta"]
        if pd.notna(row["iv"]) and row["iv"] > 0 else np.nan
        for _, row in iv_theta.iterrows()
    ]
    for product in VOUCHERS:
        prod_theta = iv_theta[iv_theta["voucher"].eq(product)]
        for start_day, end_day in [(0, 1), (1, 2)]:
            left = prod_theta[prod_theta["day"].eq(start_day)][["timestamp", "extrinsic", "option_mid", "spot_mid", "bs_theta_per_day"]]
            right = prod_theta[prod_theta["day"].eq(end_day)][["timestamp", "extrinsic", "option_mid", "spot_mid"]].rename(columns={"extrinsic": "next_extrinsic", "option_mid": "next_option_mid", "spot_mid": "next_spot_mid"})
            comp = left.merge(right, on="timestamp", how="inner")
            comp["observed_extrinsic_change"] = comp["next_extrinsic"] - comp["extrinsic"]
            theta_rows.append({
                "voucher": product,
                "from_day": start_day,
                "to_day": end_day,
                "n": int(len(comp)),
                "mean_observed_extrinsic_change": float(comp["observed_extrinsic_change"].mean()),
                "mean_bs_theta_per_day": float(comp["bs_theta_per_day"].mean()),
                "corr_theta_observed_change": safe_corr(comp["bs_theta_per_day"], comp["observed_extrinsic_change"]),
                "mean_spot_change": float((comp["next_spot_mid"] - comp["spot_mid"]).mean()),
            })
    theta_panel = store_research("research_theta_panel.csv", pd.DataFrame(theta_rows))

    # TTE=5 and annualization sensitivity.
    tte_rows = []
    day2 = iv_df[(iv_df["day"].eq(2)) & iv_df["iv"].notna()].copy()
    for _, row in day2.iterrows():
        fair_t6 = bs_call_price(row["spot_mid"], row["strike"], 6 / 365.0, row["iv"])
        fair_t5 = bs_call_price(row["spot_mid"], row["strike"], 5 / 365.0, row["iv"])
        tte_rows.append({
            "voucher": row["voucher"],
            "timestamp": row["timestamp"],
            "spot_mid": row["spot_mid"],
            "strike": row["strike"],
            "iv_day2": row["iv"],
            "fair_tte6": fair_t6,
            "fair_tte5": fair_t5,
            "fair_tte5_minus_tte6": fair_t5 - fair_t6,
        })
    tte_sensitivity = store_research("research_tte5_sensitivity.csv", pd.DataFrame(tte_rows))

    annual_rows = []
    atm_sample = opt_ctx["atm_iv"].dropna(subset=["iv"]).copy()
    for _, row in atm_sample.iterrows():
        t_365 = row["tte_days"] / 365.0
        t_252 = row["tte_days"] / 252.0
        iv_252, status_252 = implied_vol(row["spot_mid"], row["strike"], t_252, row["option_mid"])
        fair_252_same_sigma = bs_call_price(row["spot_mid"], row["strike"], t_252, row["iv"])
        annual_rows.append({
            "day": row["day"],
            "timestamp": row["timestamp"],
            "voucher": row["voucher"],
            "iv_365": row["iv"],
            "iv_252_same_price": iv_252,
            "iv_252_status": status_252,
            "fair_365": row["option_mid"],
            "fair_252_using_iv365": fair_252_same_sigma,
            "fair_252_minus_365_using_iv365": fair_252_same_sigma - row["option_mid"],
        })
    annualization = store_research("research_annualization_sensitivity.csv", pd.DataFrame(annual_rows))

    # Executable no-arbitrage and rounding diagnostics.
    opt_books = pd.concat([add_global_tick(series_for(prices, p)).assign(voucher=p, strike=strike(p)) for p in VOUCHERS], ignore_index=True)
    spot_books = vev[["day", "timestamp", "mid_price"]].rename(columns={"mid_price": "spot_mid"})
    opt_books = opt_books.merge(spot_books, on=["day", "timestamp"], how="left")
    opt_books["intrinsic"] = np.maximum(opt_books["spot_mid"] - opt_books["strike"], 0.0)
    noarb_exec_rows = []
    for day, group in opt_books.groupby("day"):
        lower_exec = group[group["ask_price_1"] < group["intrinsic"]]
        upper_exec = group[group["bid_price_1"] > group["spot_mid"]]
        noarb_exec_rows.append({
            "day": day,
            "test": "lower_bound_buy_call_at_ask",
            "executable_count": int(len(lower_exec)),
            "mean_edge": float((lower_exec["intrinsic"] - lower_exec["ask_price_1"]).mean()) if len(lower_exec) else 0.0,
            "max_edge": float((lower_exec["intrinsic"] - lower_exec["ask_price_1"]).max()) if len(lower_exec) else 0.0,
        })
        noarb_exec_rows.append({
            "day": day,
            "test": "upper_bound_sell_call_at_bid",
            "executable_count": int(len(upper_exec)),
            "mean_edge": float((upper_exec["bid_price_1"] - upper_exec["spot_mid"]).mean()) if len(upper_exec) else 0.0,
            "max_edge": float((upper_exec["bid_price_1"] - upper_exec["spot_mid"]).max()) if len(upper_exec) else 0.0,
        })
    for (day, timestamp), group in opt_books.groupby(["day", "timestamp"]):
        g = group.sort_values("strike")
        for i in range(len(g) - 1):
            low = g.iloc[i]
            high = g.iloc[i + 1]
            edge = high["bid_price_1"] - low["ask_price_1"]
            if edge > 0:
                noarb_exec_rows.append({"day": day, "test": f"monotonicity_{low['voucher']}_{high['voucher']}", "executable_count": 1, "mean_edge": edge, "max_edge": edge})
    noarb_exec = store_research("research_noarb_executable_bidask.csv", pd.DataFrame(noarb_exec_rows))

    rounding_rows = []
    iv_books = iv_df.dropna(subset=["iv"]).merge(
        opt_books[["day", "timestamp", "voucher", "bid_price_1", "ask_price_1"]],
        on=["day", "timestamp", "voucher"],
        how="left",
    )
    for product, group in iv_books.groupby("voucher"):
        raw_fair = np.array([bs_call_price(row["spot_mid"], row["strike"], TTE_DAYS[int(row["day"])] / 365.0, row["iv"]) for _, row in group.iterrows()])
        rounded_fair = np.round(raw_fair)
        bid_edge_raw = raw_fair - group["bid_price_1"].to_numpy(dtype=float)
        bid_edge_rounded = rounded_fair - group["bid_price_1"].to_numpy(dtype=float)
        ask_edge_raw = group["ask_price_1"].to_numpy(dtype=float) - raw_fair
        ask_edge_rounded = group["ask_price_1"].to_numpy(dtype=float) - rounded_fair
        rounding_rows.append({
            "voucher": product,
            "n": int(len(group)),
            "mean_abs_rounding_error": float(np.nanmean(np.abs(rounded_fair - raw_fair))),
            "bid_edge_sign_flip_rate": float(np.nanmean(np.sign(bid_edge_raw) != np.sign(bid_edge_rounded))),
            "ask_edge_sign_flip_rate": float(np.nanmean(np.sign(ask_edge_raw) != np.sign(ask_edge_rounded))),
            "mean_raw_bid_edge": float(np.nanmean(bid_edge_raw)),
            "mean_rounded_bid_edge": float(np.nanmean(bid_edge_rounded)),
            "mean_raw_ask_edge": float(np.nanmean(ask_edge_raw)),
            "mean_rounded_ask_edge": float(np.nanmean(ask_edge_rounded)),
        })
    rounding_effects = store_research("research_rounding_effects.csv", pd.DataFrame(rounding_rows))

    # Option portfolios, hedge-cost sensitivity, and stress tests.
    greek_medians = []
    if not greeks_ts.empty:
        for product, group in greeks_ts.groupby("voucher"):
            sample = iv_df[(iv_df["voucher"].eq(product)) & iv_df["iv"].notna()].iloc[len(group.dropna(subset=["iv"])) // 2] if len(group.dropna(subset=["iv"])) else None
            theta_val = bs_greeks(sample["spot_mid"], sample["strike"], TTE_DAYS[int(sample["day"])] / 365.0, sample["iv"])["theta"] if sample is not None else np.nan
            greek_medians.append({
                "voucher": product,
                "delta": float(group["bs_delta"].median()),
                "gamma": float(group["bs_gamma"].median()),
                "iv": float(group["iv"].median()),
                "theta_per_day": theta_val,
                "spot_mid": float(group["spot_mid"].median()),
                "strike": strike(product),
            })
    greek_med = pd.DataFrame(greek_medians)

    basket_specs: list[tuple[str, dict[str, float]]] = []
    if not top_gamma.empty:
        basket_specs.append(("single_best_gamma_300", {str(top_gamma.iloc[0]["voucher"]): 300.0}))
        basket_specs.append(("top3_gamma_100_each", {str(v): 100.0 for v in top_gamma.head(3)["voucher"]}))
    basket_specs.append(("atm_5200_5300_300_each", {"VEV_5200": 300.0, "VEV_5300": 300.0}))
    basket_specs.append(("deep_itm_proxy_4000_300", {"VEV_4000": 300.0}))

    basket_rows = []
    for name, weights in basket_specs:
        total_delta = total_gamma = total_theta = total_vega_proxy = 0.0
        for voucher, qty in weights.items():
            gm = greek_med[greek_med["voucher"].eq(voucher)]
            if gm.empty:
                continue
            row = gm.iloc[0]
            total_delta += qty * row["delta"]
            total_gamma += qty * row["gamma"]
            total_theta += qty * row["theta_per_day"]
            total_vega_proxy += qty * row["iv"]
        hedge_qty = float(np.clip(-total_delta, -200, 200))
        unhedged_delta_after_limit = total_delta + hedge_qty
        basket_rows.append({
            "basket": name,
            "voucher_weights": json.dumps(weights, sort_keys=True),
            "gross_voucher_position": float(sum(abs(q) for q in weights.values())),
            "total_delta_before_hedge": total_delta,
            "underlying_hedge_qty_clipped_to_limit": hedge_qty,
            "delta_after_hedge_limit": unhedged_delta_after_limit,
            "total_gamma": total_gamma,
            "theta_per_day": total_theta,
            "vega_proxy_iv_units": total_vega_proxy,
            "underlying_limit_used_fraction": abs(hedge_qty) / 200.0,
            "voucher_limit_max_used_fraction": max(abs(q) for q in weights.values()) / 300.0 if weights else 0.0,
        })
    portfolio_baskets = store_research("research_portfolio_greek_baskets.csv", pd.DataFrame(basket_rows))
    draw_bar(portfolio_baskets, "basket", "total_gamma", out.charts / "research_portfolio_greek_baskets.png", "Candidate Basket Gamma")

    gamma_cost_rows = []
    for _, row in top_gamma.iterrows():
        product = row["voucher"]
        g = greeks_ts[greeks_ts["voucher"].eq(product)].sort_values(["day", "timestamp"]).copy()
        if g.empty:
            continue
        g = g.merge(vev[["day", "timestamp", "spread"]], on=["day", "timestamp"], how="left")
        delta_change = g.groupby("day")["bs_delta"].diff().abs().fillna(0)
        hedge_cost_per_unit = delta_change * g["spread"] / 2.0
        gamma_cost_rows.append({
            "voucher": product,
            "midpoint_net_gamma_scalp_pnl": float(row["net_gamma_scalp_pnl"]),
            "estimated_delta_rehedge_cost_per_unit": float(hedge_cost_per_unit.sum()),
            "cost_adjusted_gamma_scalp_proxy": float(row["net_gamma_scalp_pnl"] - hedge_cost_per_unit.sum()),
            "cost_to_midpoint_pnl_ratio": float(hedge_cost_per_unit.sum() / row["net_gamma_scalp_pnl"]) if row["net_gamma_scalp_pnl"] else np.nan,
        })
    gamma_cost = store_research("research_gamma_hedge_cost_sensitivity.csv", pd.DataFrame(gamma_cost_rows))

    threshold_rows = []
    for threshold_delta in [0.00, 0.01, 0.02, 0.05, 0.10, 0.20]:
        costs = []
        errors = []
        for product in VOUCHERS:
            g = greeks_ts[greeks_ts["voucher"].eq(product)].sort_values(["day", "timestamp"]).copy()
            if g.empty:
                continue
            g = g.merge(vev[["day", "timestamp", "spread"]], on=["day", "timestamp"], how="left")
            desired = -g["bs_delta"].fillna(0)
            hedge = desired.copy()
            last = 0.0
            executed = []
            for val in desired:
                if abs(val - last) >= threshold_delta:
                    last = float(val)
                executed.append(last)
            executed_s = pd.Series(executed, index=g.index)
            costs.append(float(executed_s.diff().abs().fillna(0).mul(g["spread"] / 2.0).sum()))
            errors.append(float((desired - executed_s).abs().mean()))
        threshold_rows.append({
            "delta_rehedge_threshold": threshold_delta,
            "mean_estimated_hedge_cost_per_unit": float(np.mean(costs)) if costs else np.nan,
            "mean_abs_delta_error": float(np.mean(errors)) if errors else np.nan,
            "objective_cost_plus_error": float(np.mean(costs) + np.mean(errors)) if costs and errors else np.nan,
        })
    hedge_threshold = store_research("research_hedge_threshold_grid.csv", pd.DataFrame(threshold_rows))

    stress_rows = []
    if not portfolio_baskets.empty and not greek_med.empty:
        current_spot = float(vev[vev["day"].eq(2)]["mid_price"].median())
        for _, basket in portfolio_baskets.iterrows():
            weights = json.loads(basket["voucher_weights"])
            hedge_qty = float(basket["underlying_hedge_qty_clipped_to_limit"])
            for shock in [-500, -300, -200, -100, 100, 200, 300, 500]:
                spot_new = current_spot + shock
                pnl = hedge_qty * shock
                for voucher, qty in weights.items():
                    gm = greek_med[greek_med["voucher"].eq(voucher)]
                    if gm.empty:
                        continue
                    row = gm.iloc[0]
                    old_val = bs_call_price(current_spot, row["strike"], 5 / 365.0, row["iv"])
                    new_val = bs_call_price(spot_new, row["strike"], 5 / 365.0, row["iv"])
                    pnl += qty * (new_val - old_val)
                stress_rows.append({"basket": basket["basket"], "spot_shock": shock, "scenario_pnl": pnl})
    stress_tests = store_research("research_stress_tests.csv", pd.DataFrame(stress_rows))
    if not stress_tests.empty:
        worst_stress = stress_tests.groupby("basket", as_index=False)["scenario_pnl"].min().rename(columns={"scenario_pnl": "worst_scenario_pnl"})
        draw_bar(worst_stress, "basket", "worst_scenario_pnl", out.charts / "research_stress_tests.png", "Worst Scenario PnL By Basket")

    # ATM IV seasonality and walk-forward validation.
    season = opt_ctx["atm_iv"].copy()
    season["time_bin_idx"] = pd.cut(season["timestamp"], bins=np.linspace(0, 999_900, 21), include_lowest=True, labels=False)
    atm_seasonality = store_research("research_atm_iv_intraday_seasonality.csv", season.groupby(["day", "time_bin_idx"], observed=False)["iv"].agg(["count", "mean", "std", "min", "max"]).reset_index())

    wf_rows = []
    for holdout_day in sorted(vev["day"].dropna().unique()):
        train = vev[~vev["day"].eq(holdout_day)]
        test = vev[vev["day"].eq(holdout_day)]
        for features, target, model_name in [
            (["imbalance"], "future_ret_10", "imbalance_ret10"),
            (micro_features, "future_ret_10", "micro_features_ret10"),
        ]:
            source_train = vev_micro.loc[train.index] if model_name.startswith("micro") else train
            source_test = vev_micro.loc[test.index] if model_name.startswith("micro") else test
            clean_train = source_train[features + [target]].replace([np.inf, -np.inf], np.nan).dropna()
            clean_test = source_test[features + [target]].replace([np.inf, -np.inf], np.nan).dropna()
            if len(clean_train) < len(features) + 2 or len(clean_test) < 2:
                wf_rows.append({"model": model_name, "holdout_day": holdout_day, "train_n": len(clean_train), "test_n": len(clean_test), "test_r2": np.nan, "test_corr": np.nan})
                continue
            x_train = np.column_stack([np.ones(len(clean_train)), clean_train[features].to_numpy(dtype=float)])
            coef_wf, *_ = np.linalg.lstsq(x_train, clean_train[target].to_numpy(dtype=float), rcond=None)
            x_test = np.column_stack([np.ones(len(clean_test)), clean_test[features].to_numpy(dtype=float)])
            pred_test = x_test @ coef_wf
            met_test = regression_metrics(clean_test[target], pred_test, len(coef_wf))
            wf_rows.append({"model": model_name, "holdout_day": holdout_day, "train_n": len(clean_train), "test_n": len(clean_test), "test_r2": met_test["r2"], "test_corr": safe_corr(pd.Series(pred_test), clean_test[target].reset_index(drop=True))})
    walk_forward = store_research("research_walk_forward_models.csv", pd.DataFrame(wf_rows))

    # Quantitative answer manifest for Q1-Q100. The question wording is intentionally
    # not embedded here; each row is a measured answer pointer plus numeric metrics.
    atm_proxy_counts = opt_ctx["atm_iv"]["voucher"].value_counts().rename_axis("voucher").reset_index(name="count")
    atm_proxy_counts["fraction"] = atm_proxy_counts["count"] / atm_proxy_counts["count"].sum()
    store_research("research_atm_proxy_counts.csv", atm_proxy_counts)

    ie = pd.read_csv(out.tables / "voucher_intrinsic_extrinsic.csv")
    extrinsic_cv = ie.groupby("voucher")["extrinsic"].agg(["mean", "std"]).reset_index()
    extrinsic_cv["cv"] = extrinsic_cv["std"] / extrinsic_cv["mean"].replace(0, np.nan)
    store_research("research_extrinsic_stability.csv", extrinsic_cv)

    floor_rows = []
    for product in ["VEV_6000", "VEV_6500"]:
        book = series_for(prices, product)
        prod_trades = trades[trades["symbol"].eq(product)]
        iv_status = iv_df[iv_df["voucher"].eq(product)]["iv_status"].value_counts(normalize=True)
        floor_rows.append({
            "voucher": product,
            "unique_mid_count": int(book["mid_price"].nunique()),
            "bid_zero_fraction": float((book["bid_price_1"] == 0).mean()),
            "ask_one_fraction": float((book["ask_price_1"] == 1).mean()),
            "trade_count": int(len(prod_trades)),
            "unique_trade_prices": "|".join(map(str, sorted(prod_trades["price"].dropna().unique().tolist()))),
            "ok_iv_fraction": float(iv_status.get("ok", 0.0)),
            "zero_time_value_iv_fraction": float(iv_status.get("zero_time_value", 0.0)),
            "below_lower_bound_iv_fraction": float(iv_status.get("below_lower_bound", 0.0)),
        })
    floor_state = store_research("research_floor_state.csv", pd.DataFrame(floor_rows))

    guardrail_rows = []
    guardrail_source = atm_full.merge(vev[["day", "timestamp", "spread"]], on=["day", "timestamp"], how="left")
    for metric in ["iv", "rv_500", "iv_minus_rv_500", "spread"]:
        s = guardrail_source[metric].replace([np.inf, -np.inf], np.nan).dropna()
        guardrail_rows.append({
            "metric": metric,
            "p05": float(s.quantile(0.05)),
            "p25": float(s.quantile(0.25)),
            "median": float(s.quantile(0.50)),
            "p75": float(s.quantile(0.75)),
            "p95": float(s.quantile(0.95)),
            "mean": float(s.mean()),
            "std": float(s.std()),
            "n": int(len(s)),
        })
    guardrails = store_research("research_guardrail_thresholds.csv", pd.DataFrame(guardrail_rows))

    model_metric_cols = [col for col in model_df.columns if col.startswith("metric_")]
    model_long = model_df.melt(
        id_vars=["model_id", "model_name", "target", "fit_status"],
        value_vars=model_metric_cols,
        var_name="metric",
        value_name="value",
    ).dropna(subset=["value"])
    write_table(model_long, out.tables / "model_diagnostics_long.csv")
    model_family = model_df.assign(model_family=model_df["model_id"].str.extract(r"M(\d+)")[0].astype(int).map(
        lambda x: "underlying" if x <= 5 else ("vol_filter" if x <= 10 else ("order_flow" if x <= 16 else ("options_surface" if x <= 27 else "strategy_control")))
    ))
    model_family_summary = model_family.groupby("model_family", as_index=False).agg(
        models=("model_id", "count"),
        mean_abs_primary_value=("primary_value", lambda x: pd.to_numeric(x, errors="coerce").abs().mean()),
    )
    write_table(model_family_summary, out.tables / "model_family_summary.csv")

    def finite_mean(df: pd.DataFrame, col: str) -> float:
        if df.empty or col not in df:
            return float("nan")
        return float(pd.to_numeric(df[col], errors="coerce").replace([np.inf, -np.inf], np.nan).mean())

    def finite_max(df: pd.DataFrame, col: str) -> float:
        if df.empty or col not in df:
            return float("nan")
        return float(pd.to_numeric(df[col], errors="coerce").replace([np.inf, -np.inf], np.nan).max())

    def finite_min(df: pd.DataFrame, col: str) -> float:
        if df.empty or col not in df:
            return float("nan")
        return float(pd.to_numeric(df[col], errors="coerce").replace([np.inf, -np.inf], np.nan).min())

    def finite_count(df: pd.DataFrame) -> int:
        return int(len(df)) if isinstance(df, pd.DataFrame) else 0

    def metric_by_filter(df: pd.DataFrame, mask_col: str, mask_val: Any, metric_col: str, agg: str = "mean") -> float:
        if df.empty or mask_col not in df or metric_col not in df:
            return float("nan")
        sub = df[df[mask_col].eq(mask_val)]
        if agg == "max":
            return finite_max(sub, metric_col)
        if agg == "min":
            return finite_min(sub, metric_col)
        return finite_mean(sub, metric_col)

    def model_metric(model_id: str, metric_col: str = "primary_value") -> float:
        row = model_df[model_df["model_id"].eq(model_id)]
        if row.empty or metric_col not in row:
            return float("nan")
        return float(pd.to_numeric(row[metric_col], errors="coerce").iloc[0])

    def best_row(df: pd.DataFrame, metric_col: str, maximize: bool = True) -> pd.Series:
        if df.empty or metric_col not in df:
            return pd.Series(dtype=object)
        vals = pd.to_numeric(df[metric_col], errors="coerce")
        if vals.dropna().empty:
            return pd.Series(dtype=object)
        idx = vals.idxmax() if maximize else vals.idxmin()
        return df.loc[idx]

    answer_rows: list[dict[str, Any]] = []

    def add_answer(
        q_id: int,
        family: str,
        status: str,
        primary_metric: str,
        primary_value: float | int | str | bool,
        evidence_csv: str,
        secondary_metric: str = "",
        secondary_value: float | int | str | bool | None = np.nan,
        sample_size: int | float | None = np.nan,
        evidence_plot: str = "",
        model_id: str = "",
        blocker: str = "",
    ) -> None:
        answer_rows.append({
            "question_id": f"Q{q_id}",
            "answer_family": family,
            "answer_status": status,
            "primary_metric": primary_metric,
            "primary_value": primary_value,
            "secondary_metric": secondary_metric,
            "secondary_value": secondary_value,
            "sample_size": sample_size,
            "evidence_csv": evidence_csv,
            "evidence_plot": evidence_plot,
            "linked_model_id": model_id,
            "blocker_if_not_identified": blocker,
        })

    status_measured = "measured_from_current_data"
    status_proxy = "proxy_measured_needs_execution_validation"
    status_not_id = "not_identified_from_available_snapshots"
    status_out_sample = "out_of_sample_live_condition"

    top_gamma_cost = best_row(gamma_cost, "cost_adjusted_gamma_scalp_proxy", True)
    best_threshold = best_row(hedge_threshold, "objective_cost_plus_error", False)
    best_passive_voucher = best_row(passive_voucher, "positive_model_edge_rate", True)
    best_micro_ret10 = best_row(micro_pred[micro_pred["target"].eq("future_ret_10")], "r2", True)
    best_micro_abs10 = best_row(micro_pred[micro_pred["target"].eq("future_abs_ret_10")], "r2", True)
    best_resid_acf = best_row(residual_day, "residual_acf1", False)
    best_portfolio = best_row(portfolio_baskets, "total_gamma", True)
    worst_stress_value = finite_min(stress_tests, "scenario_pnl")
    best_walk = best_row(walk_forward, "test_corr", True)
    best_ensemble = best_row(ensemble, "score", True)
    gap_acf1 = float(atm_full["iv_minus_rv_500"].dropna().autocorr(1)) if atm_full["iv_minus_rv_500"].dropna().shape[0] > 3 else np.nan
    floor_count = int(floor_state["trade_count"].sum()) if not floor_state.empty else 0
    noarb_exec_count = int(noarb_exec["executable_count"].sum()) if not noarb_exec.empty else 0
    residual_corr_numeric = residual_all.pivot_table(index=["day", "timestamp"], columns="voucher", values="raw_delta_residual", aggfunc="mean").corr() if not residual_all.empty else pd.DataFrame()
    max_residual_cross_corr = float(residual_corr_numeric.where(~np.eye(len(residual_corr_numeric), dtype=bool)).abs().max().max()) if not residual_corr_numeric.empty else np.nan
    side_auc_max = finite_max(voucher_side_pred, "auc_volume_imbalance_predicts_buyer")
    seasonality_range = finite_max(atm_seasonality, "mean") - finite_min(atm_seasonality, "mean") if not atm_seasonality.empty else np.nan
    smile_slope_corr = safe_corr(smile_pred["slope"], smile_pred["future_spot_ret_10"]) if not smile_pred.empty else np.nan
    smile_curv_corr = safe_corr(smile_pred["curvature"], smile_pred["future_spot_ret_10"]) if not smile_pred.empty else np.nan
    annual_mean_diff = finite_mean(annualization, "fair_252_minus_365_using_iv365")
    tte5_mean_diff = finite_mean(tte_sensitivity, "fair_tte5_minus_tte6")
    raw_rv_mean = metric_by_filter(filtered_rv, "rv_source", "raw_ret", "annualized_std")
    kalman_rv_mean = metric_by_filter(filtered_rv, "rv_source", "kalman_ret", "annualized_std")
    nonzero_rv_mean = metric_by_filter(filtered_rv, "rv_source", "nonzero_raw_ret", "annualized_std")
    smooth_rv_mean = metric_by_filter(filtered_rv, "rv_source", "smooth_ret", "annualized_std")
    quote_change_corr = metric_by_filter(micro_pred[micro_pred["target"].eq("future_ret_10")], "feature_set", "quote_change", "corr")
    total_depth_corr = metric_by_filter(micro_pred[micro_pred["target"].eq("future_abs_ret_10")], "feature_set", "total_depth", "corr")
    microprice_r2 = metric_by_filter(micro_pred[micro_pred["target"].eq("future_ret_10")], "feature_set", "microprice_minus_mid", "r2")
    vamp_r2 = metric_by_filter(micro_pred[micro_pred["target"].eq("future_ret_10")], "feature_set", "deep_vamp_minus_mid", "r2")
    imbalance_r2 = metric_by_filter(micro_pred[micro_pred["target"].eq("future_ret_10")], "feature_set", "imbalance", "r2")
    dom_r2 = metric_by_filter(micro_pred[micro_pred["target"].eq("future_ret_10")], "feature_set", "dom_mid_minus_mid", "r2")

    add_answer(1, "vol_iv", status_measured, "mean_iv_minus_rv_500", finite_mean(iv_rv_window_gaps, "iv_minus_rv_500"), "research_iv_rv_window_gaps.csv", "mean_iv_minus_rv_100", finite_mean(iv_rv_window_gaps, "iv_minus_rv_100"), len(iv_rv_window_gaps))
    add_answer(2, "vol_iv", status_proxy, "cost_adjusted_gamma_scalp_proxy_max", finite_max(gamma_cost, "cost_adjusted_gamma_scalp_proxy"), "research_gamma_hedge_cost_sensitivity.csv", "mean_cost_to_midpoint_ratio", finite_mean(gamma_cost, "cost_to_midpoint_pnl_ratio"), len(gamma_cost), model_id="M24")
    add_answer(3, "gamma", status_proxy, "best_cost_adjusted_voucher", top_gamma_cost.get("voucher", ""), "research_gamma_hedge_cost_sensitivity.csv", "best_cost_adjusted_pnl", top_gamma_cost.get("cost_adjusted_gamma_scalp_proxy", np.nan), len(gamma_cost), model_id="M24")
    add_answer(4, "vol_iv", status_measured, "mean_gap_phi", finite_mean(gap_mr, "phi"), "research_iv_gap_mean_reversion.csv", "mean_half_life_ticks", finite_mean(gap_mr, "half_life_ticks"), len(gap_mr))
    add_answer(5, "vol_iv", status_proxy, "ema_signal_hit_rate_max", finite_max(ema_signals, "hit_rate_expected_reversion"), "research_atm_iv_ema_signals.csv", "signal_count_total", int(ema_signals["count"].sum()) if not ema_signals.empty else 0, len(ema_signals), "model_ewma_lambda_grid.png")
    add_answer(6, "atm", status_measured, "top_atm_proxy", atm_proxy_counts.iloc[0]["voucher"] if not atm_proxy_counts.empty else "", "research_atm_proxy_counts.csv", "top_atm_proxy_fraction", atm_proxy_counts.iloc[0]["fraction"] if not atm_proxy_counts.empty else np.nan, len(atm_proxy_counts))
    add_answer(7, "hedge", status_proxy, "mean_cost_to_midpoint_pnl_ratio", finite_mean(gamma_cost, "cost_to_midpoint_pnl_ratio"), "research_gamma_hedge_cost_sensitivity.csv", "underlying_mean_spread", spread_mean, len(gamma_cost))
    add_answer(8, "hedge", status_proxy, "best_delta_rehedge_threshold", best_threshold.get("delta_rehedge_threshold", np.nan), "research_hedge_threshold_grid.csv", "objective_cost_plus_error", best_threshold.get("objective_cost_plus_error", np.nan), len(hedge_threshold))
    add_answer(9, "fills", status_proxy, "best_passive_positive_model_edge_rate", best_passive_voucher.get("positive_model_edge_rate", np.nan), "research_voucher_passive_favorable_fills.csv", "mean_model_edge", best_passive_voucher.get("mean_model_edge", np.nan), len(passive_voucher), "model_fill_probability.png", "M27")
    add_answer(10, "flow", status_measured, "max_pct_at_ask_by_time_bin", finite_max(voucher_flow_time, "pct_at_ask"), "research_voucher_flow_timeofday.csv", "max_pct_at_bid_by_time_bin", finite_max(voucher_flow_time, "pct_at_bid"), len(voucher_flow_time))
    add_answer(11, "flow", status_not_id, "underlying_buy_flow_forward_return_h1000", metric_by_filter(vev_ctx["toxicity"], "side", "buy", "mean_forward_return"), "velvetfruit_flow_toxicity.csv", sample_size=int(vev_ctx["toxicity"]["count"].sum()), blocker="Requires voucher move regression with delta controls around underlying flow timestamps.")
    add_answer(12, "flow", status_proxy, "best_micro_or_bucket_r2", max(finite_max(micro_pred, "r2"), model_metric("M12", "metric_r2")), "research_micro_predictive_features.csv", "linear_imbalance_r2", model_metric("M11", "metric_r2"), len(micro_pred), "research_micro_feature_abs_corr.png", "M11/M12")
    add_answer(13, "micro", status_proxy, "ret_lag1_acf", ret_acf1, "velvetfruit_acf.csv", "half_spread_ticks", spread_mean / 2, int(vev["ret_1"].notna().sum()), blocker="ACF is measured; profitability needs passive/taker execution accounting.")
    add_answer(14, "flow", status_measured, "top_count_quintile_minus_other_abs_return", finite_mean(trade_cluster_moves, "mean_abs_return_top_count_quintile") - finite_mean(trade_cluster_moves, "mean_abs_return_other_buckets"), "research_trade_cluster_price_moves.csv", "corr_trade_count_next_abs_return", finite_mean(trade_cluster_moves, "corr_trade_count_next_abs_return"), len(trade_cluster_moves))
    add_answer(15, "flow", status_measured, "buy_fraction_next_return_corr", finite_mean(intraday_buy, "buy_fraction_corr"), "research_intraday_buy_fraction_predictiveness.csv", "model_r2", finite_mean(intraday_buy, "model_r2"), len(intraday_buy), "research_intraday_buy_fraction_predictiveness.png")
    add_answer(16, "micro", status_measured, "max_spread_to_future_rv_r2", finite_max(spread_lead, "r2"), "research_spread_vol_lead.csv", "max_spread_to_future_rv_corr", finite_max(spread_lead, "corr_spread"), len(spread_lead), "velvetfruit_spread_vs_rv.png")
    add_answer(17, "micro", status_measured, "total_depth_future_abs_return_corr", total_depth_corr, "research_micro_predictive_features.csv", "best_abs_return_feature_r2", best_micro_abs10.get("r2", np.nan), len(micro_pred), "research_micro_feature_abs_corr.png")
    add_answer(18, "micro", status_measured, "microprice_minus_mid_r2", microprice_r2, "research_micro_predictive_features.csv", "best_ret10_feature", best_micro_ret10.get("feature_set", ""), len(micro_pred), "research_micro_feature_abs_corr.png")
    add_answer(19, "micro", status_measured, "quote_change_future_ret_corr", quote_change_corr, "research_micro_predictive_features.csv", "quote_change_bid_rate_mean", pd.read_csv(out.tables / "velvetfruit_quote_change_rate.csv")["bid_price_change_rate"].mean(), len(micro_pred))
    add_answer(20, "micro", status_measured, "outside_top_minus_inside_mean_forward_return", metric_by_filter(outside_event, "outside_top", True, "mean_forward_return") - metric_by_filter(outside_event, "outside_top", False, "mean_forward_return"), "research_outside_top_trade_event_study.csv", "outside_top_count", int(outside_event[outside_event["outside_top"].eq(True)]["count"].sum()) if not outside_event.empty else 0, len(outside_event))
    add_answer(21, "market_making", status_proxy, "best_passive_underlying_mean_mid_mark_pnl", finite_max(passive_underlying, "mean_mid_mark_pnl"), "research_underlying_passive_pressure.csv", "ret_lag1_acf", ret_acf1, len(passive_underlying), blocker="Historical mid-mark proxy only; queue priority is unavailable.")
    add_answer(22, "hedge", status_proxy, "best_delta_rehedge_threshold", best_threshold.get("delta_rehedge_threshold", np.nan), "research_hedge_threshold_grid.csv", "mean_underlying_spread", spread_mean, len(hedge_threshold))
    add_answer(23, "flow", status_proxy, "buy_at_bid_after_sell_pressure_mean_pnl", metric_by_filter(passive_underlying, "maker_side", "buy_at_bid_after_sell_pressure", "mean_mid_mark_pnl"), "research_underlying_passive_pressure.csv", "positive_rate", metric_by_filter(passive_underlying, "maker_side", "buy_at_bid_after_sell_pressure", "positive_rate"), len(passive_underlying))
    add_answer(24, "direction", status_measured, "mean_ret1_by_day_average", finite_mean(vev.groupby("day")["ret_1"].mean().reset_index(), "ret_1"), "velvetfruit_return_stats.csv", "drift_per_tick", drift, int(vev["ret_1"].notna().sum()), model_id="M1")
    add_answer(25, "regime", status_measured, "regime_transition_diag", transition_diag, "model_regime_transition_matrix.csv", "high_vol_share", float((reg_df["vol_state"] == "high_vol").mean()), len(reg_df), "model_regime_states.png", "M5")
    add_answer(26, "residual", status_measured, "most_negative_residual_acf1", best_resid_acf.get("residual_acf1", np.nan), "research_residual_day_stability.csv", "voucher", best_resid_acf.get("voucher", ""), len(residual_day), "research_residual_std_by_voucher.png", "M23")
    add_answer(27, "residual", status_measured, "residual_std_day_cv", residual_day.groupby("voucher")["residual_std"].std().mean() / residual_day.groupby("voucher")["residual_std"].mean().mean(), "research_residual_day_stability.csv", "mean_residual_std", finite_mean(residual_day, "residual_std"), len(residual_day))
    add_answer(28, "residual", status_measured, "max_abs_cross_residual_corr", max_residual_cross_corr, "research_residual_cross_shock_corr.csv", sample_size=residual_corr_numeric.size)
    add_answer(29, "surface", status_measured, "pc1_explained_variance", explained[0] if len(explained) else np.nan, "model_iv_surface_pca.csv", "pc3_cum_explained_variance", np.cumsum(explained)[min(2, len(explained) - 1)] if len(explained) else np.nan, len(pca_df), "model_iv_surface_pca.png", "M21")
    add_answer(30, "surface", status_not_id, "pc1_explained_variance", explained[0] if len(explained) else np.nan, "model_iv_surface_pca.csv", sample_size=len(pca_df), blocker="Future option-return target is not separately estimated by PCA factor versus per-strike IV.")
    add_answer(31, "noarb", status_measured, "executable_noarb_count", noarb_exec_count, "research_noarb_executable_bidask.csv", "max_executable_edge", finite_max(noarb_exec, "max_edge"), len(noarb_exec))
    add_answer(32, "noarb", status_proxy, "mean_noarb_flag_lifetime_ticks", finite_mean(surv, "avg_lifetime_ticks"), "model_noarb_survival.csv", "positive_flag_count", int(surv["count"].sum()) if not surv.empty else 0, len(surv), "model_noarb_persistence.png", "M25")
    add_answer(33, "noarb", status_measured, "day0_convexity_flags", int(noarb[(noarb["day"].eq(0)) & (noarb["violation_type"].eq("convexity"))]["count"].sum()), "noarb_violations.csv", "day1_2_convexity_flags", int(noarb[(noarb["day"].isin([1, 2])) & (noarb["violation_type"].eq("convexity"))]["count"].sum()), len(noarb))
    add_answer(34, "surface", status_measured, "fitted_smile_residual_std_reduction_mean", finite_mean(smile_resid, "std_reduction"), "research_smile_fitted_residual_variance.csv", "mean_smile_r2", finite_mean(smooth, "smile_r2"), len(smile_resid), "voucher_iv_smile_smoothness.png", "M18")
    add_answer(35, "surface", status_not_id, "mean_smile_r2", finite_mean(smooth, "smile_r2"), "voucher_iv_smile_smoothness.csv", sample_size=len(smooth), blocker="Future trade-price prediction from constrained smile is not separately backtested.")
    add_answer(36, "itm", status_measured, "deep_itm_proxy_spot_corr_mean", finite_mean(forward_proxy, "corr_proxy_spot"), "research_forward_proxy.csv", "mean_synthetic_spot_minus_spot", finite_mean(forward_proxy, "mean_synthetic_spot_minus_spot"), len(forward_proxy))
    add_answer(37, "itm", status_proxy, "vev4000_proxy_minus_spot_mean", metric_by_filter(forward_proxy, "voucher", "VEV_4000", "mean_synthetic_spot_minus_spot"), "research_forward_proxy.csv", "underlying_half_spread", spread_mean / 2, len(forward_proxy))
    add_answer(38, "noarb", status_measured, "executable_vertical_monotonicity_count", int(noarb_exec[noarb_exec["test"].astype(str).str.startswith("monotonicity")]["executable_count"].sum()) if not noarb_exec.empty else 0, "research_noarb_executable_bidask.csv", "max_vertical_edge", finite_max(noarb_exec[noarb_exec["test"].astype(str).str.startswith("monotonicity")], "max_edge") if not noarb_exec.empty else np.nan, len(noarb_exec))
    add_answer(39, "surface", status_measured, "deep_itm_forward_proxy_minus_spot_mean", finite_mean(forward_proxy, "mean_synthetic_spot_minus_spot"), "research_forward_proxy.csv", "deep_itm_proxy_spot_corr_mean", finite_mean(forward_proxy, "corr_proxy_spot"), len(forward_proxy))
    add_answer(40, "rounding", status_measured, "mean_abs_rounding_error", finite_mean(rounding_effects, "mean_abs_rounding_error"), "research_rounding_effects.csv", "max_bid_edge_sign_flip_rate", finite_max(rounding_effects, "bid_edge_sign_flip_rate"), len(rounding_effects))
    add_answer(41, "floor", status_measured, "floor_trade_count", floor_count, "research_floor_state.csv", "bid_zero_fraction_mean", finite_mean(floor_state, "bid_zero_fraction"), len(floor_state))
    add_answer(42, "floor", status_proxy, "ask_one_fraction_mean", finite_mean(floor_state, "ask_one_fraction"), "research_floor_state.csv", "floor_trade_count", floor_count, len(floor_state), blocker="Historical tail paths did not move these strikes materially; live jump risk remains outside-sample.")
    add_answer(43, "floor", status_not_id, "zero_time_value_iv_fraction_mean", finite_mean(floor_state, "zero_time_value_iv_fraction"), "research_floor_state.csv", "ok_iv_fraction_mean", finite_mean(floor_state, "ok_iv_fraction"), len(floor_state), blocker="Tail-risk distribution cannot be estimated from no observed floor-strike jumps.")
    add_answer(44, "floor", status_out_sample, "unique_mid_count_mean", finite_mean(floor_state, "unique_mid_count"), "research_floor_state.csv", "historical_trade_count", floor_count, len(floor_state), blocker="Different live paths are not present in the historical sample.")
    add_answer(45, "floor", status_measured, "ok_iv_fraction_mean", finite_mean(floor_state, "ok_iv_fraction"), "research_floor_state.csv", "zero_time_value_iv_fraction_mean", finite_mean(floor_state, "zero_time_value_iv_fraction"), len(floor_state))
    add_answer(46, "tte", status_proxy, "mean_fair_tte5_minus_tte6", tte5_mean_diff, "research_tte5_sensitivity.csv", "sample_rows", len(tte_sensitivity), len(tte_sensitivity))
    add_answer(47, "tte", status_out_sample, "historical_min_tte_days", min(TTE_DAYS.values()), "voucher_iv_term_structure.csv", "live_tte_days", 5, len(pd.read_csv(out.tables / "voucher_iv_term_structure.csv")), blocker="TTE=5 is extrapolated, not observed.")
    add_answer(48, "theta", status_measured, "theta_observed_change_corr_mean", finite_mean(theta_panel, "corr_theta_observed_change"), "research_theta_panel.csv", "mean_bs_theta_per_day", finite_mean(theta_panel, "mean_bs_theta_per_day"), len(theta_panel))
    add_answer(49, "theta", status_measured, "mean_observed_minus_bs_theta", finite_mean(theta_panel, "mean_observed_extrinsic_change") - finite_mean(theta_panel, "mean_bs_theta_per_day"), "research_theta_panel.csv", "mean_spot_change", finite_mean(theta_panel, "mean_spot_change"), len(theta_panel))
    add_answer(50, "theta", status_measured, "lowest_extrinsic_cv", finite_min(extrinsic_cv, "cv"), "research_extrinsic_stability.csv", "mean_extrinsic_cv", finite_mean(extrinsic_cv, "cv"), len(extrinsic_cv))
    add_answer(51, "flow", status_measured, "large_trade_future_iv_change_mean", finite_mean(voucher_trade_iv[voucher_trade_iv["size_bucket"].astype(str).eq("q4_large")], "mean_iv_change") if not voucher_trade_iv.empty else np.nan, "research_voucher_trade_event_iv.csv", "large_trade_positive_iv_change_rate", finite_mean(voucher_trade_iv[voucher_trade_iv["size_bucket"].astype(str).eq("q4_large")], "positive_iv_change_rate") if not voucher_trade_iv.empty else np.nan, len(voucher_trade_iv))
    add_answer(52, "flow", status_measured, "seller_initiated_future_iv_change_mean", metric_by_filter(voucher_trade_iv, "trade_side", "seller_initiated", "mean_iv_change"), "research_voucher_trade_event_iv.csv", "seller_positive_iv_change_rate", metric_by_filter(voucher_trade_iv, "trade_side", "seller_initiated", "positive_iv_change_rate"), len(voucher_trade_iv))
    add_answer(53, "flow", status_measured, "buyer_initiated_future_iv_change_mean", metric_by_filter(voucher_trade_iv, "trade_side", "buyer_initiated", "mean_iv_change"), "research_voucher_trade_event_iv.csv", "buyer_positive_iv_change_rate", metric_by_filter(voucher_trade_iv, "trade_side", "buyer_initiated", "positive_iv_change_rate"), len(voucher_trade_iv))
    add_answer(54, "fills", status_proxy, "mean_voucher_spread", finite_mean(liquidity, "mean_spread"), "voucher_liquidity.csv", "mean_trade_minus_fv_abs", float(tfv["trade_minus_bs_fv"].abs().mean()) if not tfv.empty else np.nan, len(liquidity), blocker="Crossing profitability needs an executable edge threshold per order.")
    add_answer(55, "fills", status_not_id, "fill_score_auc", fill_auc, "model_fill_probability.csv", sample_size=len(fill_df), model_id="M27", blocker="Passive quote distance requires queue-position labels not present in snapshot data.")
    add_answer(56, "fills", status_measured, "fill_score_auc", fill_auc, "model_fill_probability.csv", "mean_trade_rate", finite_mean(fill_summary, "trade_rate"), len(fill_df), "model_fill_probability.png", "M27")
    voucher_bot = pd.read_csv(out.tables / "voucher_bot_analysis.csv")
    bot_cv = (voucher_bot["bid_vol1_std"] / voucher_bot["bid_vol1_mean"].replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)
    quote_strike_corr = safe_corr(voucher_bot["voucher"].map(strike), voucher_bot["bid_price_change_rate"])
    add_answer(57, "bots", status_measured, "median_bid_l1_volume_cv", float(bot_cv.median()), "voucher_bot_analysis.csv", "mean_bid_l1_volume_cv", float(bot_cv.mean()), len(voucher_bot))
    add_answer(58, "bots", status_measured, "max_auc_volume_imbalance_predicts_buyer", side_auc_max, "research_voucher_volume_side_prediction.csv", "mean_side_corr", finite_mean(voucher_side_pred, "corr_volume_imbalance_side"), len(voucher_side_pred))
    add_answer(59, "bots", status_measured, "strike_quote_change_corr", quote_strike_corr, "voucher_bot_analysis.csv", "min_bid_price_change_rate", finite_min(voucher_bot, "bid_price_change_rate"), len(voucher_bot))
    add_answer(60, "bots", status_not_id, "median_bid_l1_volume_cv", float(bot_cv.median()), "voucher_bot_analysis.csv", sample_size=len(voucher_bot), blocker="Stable volume alone does not identify bot IDs.")
    add_answer(61, "data", status_measured, "buyer_column_ever_populated", int(bool(bot_ctx.get("buyer_column_ever_populated"))), "section7_bot_patterns", "seller_column_ever_populated", int(bool(bot_ctx.get("seller_column_ever_populated"))), len(trades))
    add_answer(62, "vol_iv", status_measured, "mean_fair_252_minus_365_using_iv365", annual_mean_diff, "research_annualization_sensitivity.csv", "mean_iv_252_same_price", finite_mean(annualization, "iv_252_same_price"), len(annualization))
    add_answer(63, "vol_iv", status_measured, "mean_iv_365_minus_iv_252_same_price", finite_mean(annualization, "iv_365") - finite_mean(annualization, "iv_252_same_price"), "research_annualization_sensitivity.csv", "calendar_basis_rows", len(annualization), len(annualization))
    add_answer(64, "micro", status_measured, "raw_rv_minus_nonzero_raw_rv", raw_rv_mean - nonzero_rv_mean, "research_filtered_rv.csv", "raw_rv_mean", raw_rv_mean, len(filtered_rv), "research_filtered_rv.png")
    add_answer(65, "micro", status_measured, "raw_rv_minus_smooth_rv", raw_rv_mean - smooth_rv_mean, "research_filtered_rv.csv", "raw_rv_minus_kalman_rv", raw_rv_mean - kalman_rv_mean, len(filtered_rv), "research_filtered_rv.png")
    add_answer(66, "micro", status_measured, "microprice_future_ret10_r2", microprice_r2, "research_micro_predictive_features.csv", "microprice_future_ret10_corr", metric_by_filter(micro_pred[micro_pred["target"].eq("future_ret_10")], "feature_set", "microprice_minus_mid", "corr"), len(micro_pred))
    add_answer(67, "micro", status_measured, "deep_vamp_minus_imbalance_r2", vamp_r2 - imbalance_r2, "research_micro_predictive_features.csv", "deep_vamp_r2", vamp_r2, len(micro_pred))
    add_answer(68, "micro", status_measured, "dom_mid_future_ret10_r2", dom_r2, "research_micro_predictive_features.csv", "dom_mid_future_ret10_corr", metric_by_filter(micro_pred[micro_pred["target"].eq("future_ret_10")], "feature_set", "dom_mid_minus_mid", "corr"), len(micro_pred))
    add_answer(69, "filter", status_measured, "kalman_rmse", model_metric("M10", "metric_rmse"), "model_kalman_fair_value.csv", "kalman_r2", model_metric("M10", "metric_r2"), len(kal), "research_filtered_rv.png", "M10")
    add_answer(70, "filter", status_measured, "mean_abs_delta_change_filtered_minus_raw", finite_mean(filtered_delta, "mean_abs_delta_change_filtered_minus_raw"), "research_filtered_spot_delta_impact.csv", "p95_abs_delta_change", finite_mean(filtered_delta, "p95_abs_delta_change"), len(filtered_delta))
    add_answer(71, "filter", status_measured, "filtered_delta_residual_std_reduction_mean", finite_mean(filtered_resid, "std_reduction"), "research_filtered_delta_residual_variance.csv", "raw_residual_std_mean", finite_mean(filtered_resid, "raw_residual_std"), len(filtered_resid))
    add_answer(72, "surface", status_measured, "fitted_smile_residual_std_reduction_mean", finite_mean(smile_resid, "std_reduction"), "research_smile_fitted_residual_variance.csv", "fitted_smile_residual_std_mean", finite_mean(smile_resid, "fitted_smile_residual_std"), len(smile_resid))
    add_answer(73, "residual", status_proxy, "best_residual_std_reduction", max(finite_mean(filtered_resid, "std_reduction"), finite_mean(smile_resid, "std_reduction")), "research_filtered_delta_residual_variance.csv;research_smile_fitted_residual_variance.csv", "raw_residual_std_mean", finite_mean(residual_day, "residual_std"), len(residual_day))
    add_answer(74, "residual", status_not_id, "most_negative_residual_acf1", best_resid_acf.get("residual_acf1", np.nan), "research_residual_day_stability.csv", sample_size=len(residual_day), blocker="Passive-only monetization needs order-book replay and queue fills.")
    add_answer(75, "residual", status_measured, "max_residual_std_by_voucher_day", finite_max(residual_day, "residual_std"), "research_residual_day_stability.csv", "min_residual_std_by_voucher_day", finite_min(residual_day, "residual_std"), len(residual_day))
    add_answer(76, "residual", status_measured, "mean_late_minus_early_residual_std", finite_mean(residual_day, "late_minus_early_std"), "research_residual_day_stability.csv", "max_late_minus_early_residual_std", finite_max(residual_day, "late_minus_early_std"), len(residual_day))
    add_answer(77, "seasonality", status_measured, "atm_iv_intraday_mean_range", seasonality_range, "research_atm_iv_intraday_seasonality.csv", "atm_iv_bin_std_mean", finite_mean(atm_seasonality, "std"), len(atm_seasonality))
    add_answer(78, "surface", status_measured, "smile_slope_future_spot_ret10_corr", smile_slope_corr, "research_smile_predictiveness.csv", "smile_r2_mean", finite_mean(smile_pred, "smile_r2"), len(smile_pred), "research_smile_predictiveness.png")
    add_answer(79, "surface", status_measured, "smile_curvature_future_spot_ret10_corr", smile_curv_corr, "research_smile_predictiveness.csv", "smile_r2_mean", finite_mean(smile_pred, "smile_r2"), len(smile_pred), "research_smile_predictiveness.png")
    add_answer(80, "portfolio", status_proxy, "best_basket_total_gamma", best_portfolio.get("total_gamma", np.nan), "research_portfolio_greek_baskets.csv", "best_basket_delta_after_hedge", best_portfolio.get("delta_after_hedge_limit", np.nan), len(portfolio_baskets), "research_portfolio_greek_baskets.png")
    add_answer(81, "portfolio", status_proxy, "best_basket_name", best_portfolio.get("basket", ""), "research_portfolio_greek_baskets.csv", "best_basket_underlying_limit_used_fraction", best_portfolio.get("underlying_limit_used_fraction", np.nan), len(portfolio_baskets))
    add_answer(82, "portfolio", status_proxy, "max_voucher_limit_used_fraction", finite_max(portfolio_baskets, "voucher_limit_max_used_fraction"), "research_portfolio_greek_baskets.csv", "gross_voucher_position_at_best_gamma", best_portfolio.get("gross_voucher_position", np.nan), len(portfolio_baskets))
    add_answer(83, "portfolio", status_proxy, "best_basket_hedge_qty", best_portfolio.get("underlying_hedge_qty_clipped_to_limit", np.nan), "research_portfolio_greek_baskets.csv", "best_basket_delta_after_hedge", best_portfolio.get("delta_after_hedge_limit", np.nan), len(portfolio_baskets))
    add_answer(84, "inventory", status_not_id, "max_underlying_limit_used_fraction", finite_max(portfolio_baskets, "underlying_limit_used_fraction"), "research_portfolio_greek_baskets.csv", sample_size=len(portfolio_baskets), blocker="End-of-simulation utility/penalty is not observable from EDA snapshots.")
    add_answer(85, "inventory", status_not_id, "terminal_inventory_model_count", len(portfolio_baskets), "research_portfolio_greek_baskets.csv", sample_size=len(portfolio_baskets), blocker="Forced close versus hold requires final mark-to-market and exchange liquidation rules.")
    add_answer(86, "stress", status_proxy, "worst_scenario_pnl", worst_stress_value, "research_stress_tests.csv", "best_scenario_pnl", finite_max(stress_tests, "scenario_pnl"), len(stress_tests), "research_stress_tests.png")
    add_answer(87, "stress", status_proxy, "max_cost_to_midpoint_pnl_ratio", finite_max(gamma_cost, "cost_to_midpoint_pnl_ratio"), "research_gamma_hedge_cost_sensitivity.csv", "mean_underlying_spread", spread_mean, len(gamma_cost), blocker="Explicit spread-widening paths are approximated by hedge-cost sensitivity only.")
    add_answer(88, "validation", status_measured, "mean_walk_forward_test_r2", finite_mean(walk_forward, "test_r2"), "research_walk_forward_models.csv", "mean_walk_forward_test_corr", finite_mean(walk_forward, "test_corr"), len(walk_forward))
    add_answer(89, "validation", status_measured, "best_walk_forward_test_corr", best_walk.get("test_corr", np.nan), "research_walk_forward_models.csv", "best_walk_forward_model", best_walk.get("model", ""), len(walk_forward))
    add_answer(90, "tte", status_out_sample, "mean_fair_tte5_minus_tte6", tte5_mean_diff, "research_tte5_sensitivity.csv", "historical_min_tte_days", min(TTE_DAYS.values()), len(tte_sensitivity), blocker="Live TTE=5 is not directly observed.")
    add_answer(91, "strategy", status_proxy, "top_ensemble_signal_score", best_ensemble.get("score", np.nan), "model_ensemble_signal_scores.csv", "top_ensemble_signal", best_ensemble.get("signal", ""), len(ensemble), "model_ensemble_signal_scores.png", "M30")
    add_answer(92, "itm", status_measured, "deep_itm_proxy_spot_corr_mean", finite_mean(forward_proxy, "corr_proxy_spot"), "research_forward_proxy.csv", "mean_synthetic_spot_minus_spot", finite_mean(forward_proxy, "mean_synthetic_spot_minus_spot"), len(forward_proxy))
    add_answer(93, "rounding", status_measured, "mean_abs_rounding_error", finite_mean(rounding_effects, "mean_abs_rounding_error"), "research_rounding_effects.csv", "mean_raw_bid_edge", finite_mean(rounding_effects, "mean_raw_bid_edge"), len(rounding_effects))
    add_answer(94, "rounding", status_measured, "max_bid_edge_sign_flip_rate", finite_max(rounding_effects, "bid_edge_sign_flip_rate"), "research_rounding_effects.csv", "max_ask_edge_sign_flip_rate", finite_max(rounding_effects, "ask_edge_sign_flip_rate"), len(rounding_effects))
    add_answer(95, "rounding", status_proxy, "mean_rounded_bid_edge", finite_mean(rounding_effects, "mean_rounded_bid_edge"), "research_rounding_effects.csv", "mean_rounded_ask_edge", finite_mean(rounding_effects, "mean_rounded_ask_edge"), len(rounding_effects), blocker="Best quoting grid needs live queue and adverse-selection calibration.")
    add_answer(96, "validation", status_proxy, "best_passive_positive_model_edge_rate", best_passive_voucher.get("positive_model_edge_rate", np.nan), "research_voucher_passive_favorable_fills.csv", "max_cost_to_midpoint_ratio", finite_max(gamma_cost, "cost_to_midpoint_pnl_ratio"), len(passive_voucher))
    add_answer(97, "simulation", status_not_id, "available_snapshot_rows", len(prices), "data_integrity.csv", "available_trade_rows", len(trades), len(prices), blocker="Joint simulator/replay is not implemented in this EDA.")
    add_answer(98, "validation", status_measured, "walk_forward_test_corr_std", float(pd.to_numeric(walk_forward["test_corr"], errors="coerce").std()) if not walk_forward.empty else np.nan, "research_walk_forward_models.csv", "walk_forward_test_r2_std", float(pd.to_numeric(walk_forward["test_r2"], errors="coerce").std()) if not walk_forward.empty else np.nan, len(walk_forward))
    add_answer(99, "strategy", status_proxy, "top_ensemble_signal", best_ensemble.get("signal", ""), "model_ensemble_signal_scores.csv", "top_ensemble_signal_score", best_ensemble.get("score", np.nan), len(ensemble), "model_ensemble_signal_scores.png", "M30")
    add_answer(100, "risk", status_measured, "iv_minus_rv_500_p95", metric_by_filter(guardrails, "metric", "iv_minus_rv_500", "p95"), "research_guardrail_thresholds.csv", "spread_p95", metric_by_filter(guardrails, "metric", "spread", "p95"), len(guardrails))

    answer_manifest = pd.DataFrame(answer_rows)
    if len(answer_manifest) != 100:
        raise RuntimeError(f"Expected 100 quantitative answer rows, found {len(answer_manifest)}")
    write_table(answer_manifest, out.tables / "research_metric_answers.csv")

    answer_summary = answer_manifest.groupby(["answer_family", "answer_status"]).size().rename("answers").reset_index()
    write_table(answer_summary, out.tables / "research_metric_answer_summary.csv")
    plt.figure(figsize=(10, 4))
    answer_manifest["answer_status"].value_counts().plot(kind="bar")
    plt.title("Quantitative Answer Status Counts")
    plt.ylabel("Q-count")
    plt.xticks(rotation=25, ha="right")
    save_fig(out.charts / "research_answer_status_counts.png")
    plt.figure(figsize=(12, 5))
    answer_manifest.groupby("answer_family").size().sort_values(ascending=False).plot(kind="bar")
    plt.title("Quantitative Answer Coverage By Family")
    plt.ylabel("Q-count")
    plt.xticks(rotation=45, ha="right")
    save_fig(out.charts / "research_answer_family_coverage.png")

    followup_triggers = answer_manifest[answer_manifest["answer_status"].isin([status_not_id, status_out_sample])][[
        "question_id",
        "answer_family",
        "primary_metric",
        "primary_value",
        "evidence_csv",
        "blocker_if_not_identified",
    ]].copy()
    write_table(followup_triggers, out.tables / "research_followup_triggers.csv")

    print("[Research] quantitative Q1-Q100 rows:", len(answer_manifest))
    print(answer_manifest["answer_status"].value_counts().to_string())
    print("[Models] diagnostic rows:", len(model_df), "long-metric rows:", len(model_long))
    print(ranked[["model_id", "model_name", "primary_metric", "primary_value"]].head(8).to_string(index=False))

    return {"answers": answer_manifest, "models": model_df, "followup_triggers": followup_triggers}


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

    # --- New Section 10: Quantitative research answers and model diagnostics ---
    rq_path = out.tables / "research_metric_answers.csv"
    model_path = out.tables / "model_diagnostics.csv"
    followup_path = out.tables / "research_followup_triggers.csv"
    if rq_path.exists() or model_path.exists():
        report.append("\n## Section 10 - Quantitative Research Answers And Model Diagnostics\n")
        report.append("![Answer status counts](charts/research_answer_status_counts.png)\n")
        report.append("![Answer family coverage](charts/research_answer_family_coverage.png)\n")
        report.append("![Model rankings](charts/model_rankings.png)\n")
        report.append("![IV PCA](charts/model_iv_surface_pca.png)\n")
        report.append("![Fill probability](charts/model_fill_probability.png)\n")
        report.append("![No-arb persistence](charts/model_noarb_persistence.png)\n")

    if rq_path.exists():
        rq = pd.read_csv(rq_path)
        report.append("\nQuantitative answer coverage by family and status:\n")
        report.append(clean_markdown_table(rq.groupby(["answer_family", "answer_status"]).size().rename("rows").reset_index(), 30))
        report.append("\n\nFirst quantitative answer rows (no embedded question text):\n")
        report.append(clean_markdown_table(rq[["question_id", "answer_family", "answer_status", "primary_metric", "primary_value", "secondary_metric", "secondary_value", "evidence_csv"]].head(20), 20))

    if model_path.exists():
        models = pd.read_csv(model_path)
        report.append("\n\nModel diagnostics summary:\n")
        report.append(clean_markdown_table(models[["model_id", "model_name", "primary_metric", "primary_value", "interpretation", "fit_status"]], 30))

    if followup_path.exists():
        follow = pd.read_csv(followup_path)
        report.append("\n\nQuantitative blockers requiring extra data or replay:\n")
        report.append(clean_markdown_table(follow.head(20), 20))

    (out.root / "REPORT.md").write_text("\n".join(report), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Comprehensive Velvetfruit and VEV voucher EDA for Round 3.")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--no-clean", action="store_true", help="Keep existing chart/table files instead of deleting the output folders first.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    out = ensure_output(args.output_dir, clean=not args.no_clean)

    print("[Section 1/10] Loading data and checking integrity...")
    prices, trades, hydrogel_present = load_data(args.data_dir)
    integrity, anomalies = section1_data_integrity(prices, trades, out)

    print("[Section 2/10] Running Velvetfruit individual EDA...")
    vev_ctx = section2_vev(prices, trades, out)

    print("[Section 3/10] Running voucher individual EDA...")
    voucher_ctx = section3_vouchers(prices, trades, vev_ctx["vev"], out)

    print("[Section 4/10] Running options cross-voucher analysis...")
    opt_ctx = section4_options(prices, vev_ctx, out)

    print("[Section 5/10] Running lead-lag analysis...")
    leadlag = section5_leadlag(prices, opt_ctx, out)

    print("[Section 6/10] Running voucher trade vs fair value analysis...")
    trade_ctx = section6_trade_analysis(prices, trades, opt_ctx, out)

    print("[Section 7/10] Running advanced option microstructure and surface metrics...")
    section7_options_microstructure_surfaces(prices, trades, vev_ctx, opt_ctx, out)

    print("[Section 8/10] Running bot pattern analysis...")
    bot_ctx = section7_bot_patterns(prices, trades, out)

    print("[Section 9/10] Answering research questions and running model diagnostics...")
    research_ctx = section8_research_and_models(prices, trades, vev_ctx, voucher_ctx, opt_ctx, leadlag, trade_ctx, bot_ctx, out)

    print("[Section 10/10] Writing plain-English report...")
    build_report(out, hydrogel_present, integrity, anomalies, vev_ctx, voucher_ctx, opt_ctx, leadlag, bot_ctx)
    cleanup_duplicate_output_artifacts(out)
    print(f"Done. Outputs written to {out.root}")


if __name__ == "__main__":
    main()
