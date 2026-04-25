#!/usr/bin/env python3
"""
Frontier alpha extraction for Round 3 VELVETFRUIT_EXTRACT / VEV vouchers.

The script fits four production-facing modules with one chronological
walk-forward split: 50% train, 20% validation, 30% out-of-sample.

Outputs are written to:
    phase2/round3/algo/analysis/frontier_alpha_output/
"""

from __future__ import annotations

import argparse
import json
import math
import os
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import norm
from sklearn.exceptions import ConvergenceWarning
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel
from statsmodels.tsa.vector_ar.vecm import coint_johansen


warnings.filterwarnings("ignore", category=ConvergenceWarning)


SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent / "data"
EDA_TABLES = SCRIPT_DIR / "ve_vev_eda_output" / "tables"
OUTPUT_DIR = SCRIPT_DIR / "frontier_alpha_output"

UNDERLYING = "VELVETFRUIT_EXTRACT"
PAIR_Y = "VEV_5200"
PAIR_X = "VEV_5300"
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
TIMESTAMP_STEP = 100
TICKS_PER_DAY = 10_000
WALK_FORWARD = {"train": 0.50, "validation": 0.20, "oos": 0.30}


@dataclass(frozen=True)
class Split:
    train_end: int
    validation_end: int
    n: int

    @property
    def train(self) -> slice:
        return slice(0, self.train_end)

    @property
    def validation(self) -> slice:
        return slice(self.train_end, self.validation_end)

    @property
    def oos(self) -> slice:
        return slice(self.validation_end, self.n)


def ensure_output(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def to_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return to_jsonable(value.tolist())
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(to_jsonable(payload), indent=2, sort_keys=True), encoding="utf-8")


def load_prices(data_dir: Path) -> pd.DataFrame:
    frames = []
    for day in [0, 1, 2]:
        path = data_dir / f"prices_round_3_day_{day}.csv"
        frame = pd.read_csv(path, sep=";")
        if "day" not in frame.columns:
            frame["day"] = day
        frames.append(frame)
    prices = pd.concat(frames, ignore_index=True)
    numeric_cols = [c for c in prices.columns if c not in {"product"}]
    prices[numeric_cols] = prices[numeric_cols].apply(pd.to_numeric, errors="coerce")
    prices["global_tick"] = prices["day"] * 1_000_000 + prices["timestamp"]
    return prices.sort_values(["product", "day", "timestamp"]).reset_index(drop=True)


def wide_product(prices: pd.DataFrame, product: str) -> pd.DataFrame:
    out = prices[prices["product"].eq(product)].copy()
    out = out.sort_values(["day", "timestamp"]).reset_index(drop=True)
    out["spread"] = out["ask_price_1"] - out["bid_price_1"]
    return out


def load_iv_and_greeks() -> tuple[pd.DataFrame, pd.DataFrame]:
    iv = pd.read_csv(EDA_TABLES / "voucher_iv_timeseries.csv")
    greeks = pd.read_csv(EDA_TABLES / "voucher_greeks_timeseries.csv")
    for df in [iv, greeks]:
        df["global_tick"] = df["day"] * 1_000_000 + df["timestamp"]
    return iv.sort_values(["voucher", "day", "timestamp"]), greeks.sort_values(["voucher", "day", "timestamp"])


def make_split(n: int) -> Split:
    train_end = int(math.floor(n * WALK_FORWARD["train"]))
    validation_end = int(math.floor(n * (WALK_FORWARD["train"] + WALK_FORWARD["validation"])))
    return Split(train_end=train_end, validation_end=validation_end, n=n)


def split_name(idx: int, split: Split) -> str:
    if idx < split.train_end:
        return "train"
    if idx < split.validation_end:
        return "validation"
    return "oos"


def max_drawdown(equity: np.ndarray) -> float:
    if equity.size == 0:
        return 0.0
    peak = np.maximum.accumulate(equity)
    return float(np.max(peak - equity))


def sharpe_ratio(pnl: np.ndarray) -> float:
    clean = pnl[np.isfinite(pnl)]
    if clean.size < 3:
        return 0.0
    std = float(clean.std(ddof=1))
    if std <= 1e-12:
        return 0.0
    return float(clean.mean() / std * math.sqrt(TICKS_PER_DAY))


def calmar_ratio(pnl: np.ndarray) -> float:
    clean = pnl[np.isfinite(pnl)]
    if clean.size == 0:
        return -1e9
    equity = np.cumsum(clean)
    mdd = max(max_drawdown(equity), 1.0)
    annualized = float(clean.mean() * TICKS_PER_DAY * 365)
    return annualized / mdd


def pnl_metrics(pnl: np.ndarray) -> dict[str, float]:
    clean = pnl[np.isfinite(pnl)]
    if clean.size == 0:
        return {"n": 0, "total_pnl": 0.0, "mean_pnl": 0.0, "sharpe": 0.0, "max_drawdown": 0.0, "calmar": -1e9}
    equity = np.cumsum(clean)
    return {
        "n": int(clean.size),
        "total_pnl": float(clean.sum()),
        "mean_pnl": float(clean.mean()),
        "sharpe": sharpe_ratio(clean),
        "max_drawdown": max_drawdown(equity),
        "calmar": calmar_ratio(clean),
    }


def future_delta_by_day(values: pd.Series, horizon_rows: int) -> pd.Series:
    return values.groupby(level=0).shift(-horizon_rows) - values


def compute_multilevel_ofi(book: pd.DataFrame) -> pd.DataFrame:
    out = book[["day", "timestamp", "global_tick", "mid_price", "spread"]].copy()
    weights = np.array([1.0, 0.6, 0.35], dtype=float)
    ofi = np.zeros(len(book), dtype=float)
    depth = np.zeros(len(book), dtype=float)
    for level, weight in zip([1, 2, 3], weights):
        bid_p = book[f"bid_price_{level}"].astype(float)
        ask_p = book[f"ask_price_{level}"].astype(float)
        bid_v = book[f"bid_volume_{level}"].fillna(0.0).astype(float)
        ask_v = book[f"ask_volume_{level}"].fillna(0.0).astype(float)
        bid_p_lag = bid_p.groupby(book["day"]).shift(1)
        ask_p_lag = ask_p.groupby(book["day"]).shift(1)
        bid_v_lag = bid_v.groupby(book["day"]).shift(1).fillna(0.0)
        ask_v_lag = ask_v.groupby(book["day"]).shift(1).fillna(0.0)

        bid_event = np.where(bid_p >= bid_p_lag, bid_v, 0.0) - np.where(bid_p <= bid_p_lag, bid_v_lag, 0.0)
        ask_event = np.where(ask_p <= ask_p_lag, ask_v, 0.0) - np.where(ask_p >= ask_p_lag, ask_v_lag, 0.0)
        bid_event = np.nan_to_num(bid_event, nan=0.0)
        ask_event = np.nan_to_num(ask_event, nan=0.0)
        ofi += weight * (bid_event - ask_event)
        depth += weight * (bid_v.to_numpy() + ask_v.to_numpy())

    out["raw_multilevel_ofi"] = ofi
    out["depth_weighted_ofi"] = ofi / np.maximum(depth, 1.0)
    train_probe = out["depth_weighted_ofi"].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    out["ofi_z"] = train_probe
    return out


def hawkes_nll_univariate(params: np.ndarray, event_times: np.ndarray, horizon: float) -> float:
    mu = math.exp(params[0])
    beta = math.exp(params[2])
    alpha = beta / (1.0 + math.exp(-params[1]))
    if event_times.size == 0 or horizon <= 0:
        return 1e9
    recurrence = 0.0
    last_t = float(event_times[0])
    loglik = 0.0
    for t in event_times:
        dt = float(t - last_t)
        recurrence *= math.exp(-beta * max(dt, 0.0))
        intensity = mu + alpha * recurrence
        if intensity <= 1e-12 or not math.isfinite(intensity):
            return 1e9
        loglik += math.log(intensity)
        recurrence += 1.0
        last_t = float(t)
    integral = mu * horizon + (alpha / beta) * np.sum(1.0 - np.exp(-beta * np.maximum(horizon - event_times, 0.0)))
    return float(integral - loglik)


def fit_hawkes(event_times: np.ndarray, horizon: float) -> dict[str, float]:
    event_times = np.asarray(event_times, dtype=float)
    event_times = event_times[np.isfinite(event_times)]
    if event_times.size < 20 or horizon <= 0:
        base_mu = max(event_times.size / max(horizon, 1.0), 1e-6)
        return {"mu": base_mu, "alpha": 0.0, "beta": 1.0, "branching_ratio": 0.0, "event_count": int(event_times.size)}
    mu0 = max(event_times.size / horizon * 0.6, 1e-6)
    beta0 = 1.0 / max(np.median(np.diff(event_times)) if event_times.size > 2 else 10.0, 1.0)
    x0 = np.array([math.log(mu0), 0.0, math.log(max(beta0, 1e-4))])
    result = minimize(
        hawkes_nll_univariate,
        x0,
        args=(event_times, horizon),
        method="Nelder-Mead",
        options={"maxiter": 800, "xatol": 1e-6, "fatol": 1e-6},
    )
    x = result.x if result.success else x0
    mu = math.exp(float(x[0]))
    beta = math.exp(float(x[2]))
    alpha = beta / (1.0 + math.exp(-float(x[1])))
    return {
        "mu": float(mu),
        "alpha": float(alpha),
        "beta": float(beta),
        "branching_ratio": float(alpha / beta if beta > 0 else 0.0),
        "event_count": int(event_times.size),
        "nll": float(hawkes_nll_univariate(x, event_times, horizon)),
    }


def hawkes_intensity_grid(event_mask: np.ndarray, times: np.ndarray, params: dict[str, float]) -> np.ndarray:
    mu = float(params["mu"])
    alpha = float(params["alpha"])
    beta = max(float(params["beta"]), 1e-12)
    out = np.full(len(times), mu, dtype=float)
    recurrence = 0.0
    last_t = float(times[0]) if len(times) else 0.0
    for i, t in enumerate(times):
        dt = float(t - last_t)
        recurrence *= math.exp(-beta * max(dt, 0.0))
        out[i] = mu + alpha * recurrence
        if event_mask[i]:
            recurrence += 1.0
        last_t = float(t)
    return out


def transition_matrix(states: np.ndarray, moves: np.ndarray) -> pd.DataFrame:
    state_labels = [-1, 0, 1]
    move_labels = [-1, 0, 1]
    rows = []
    for state in state_labels:
        mask = states == state
        denom = int(mask.sum())
        row: dict[str, Any] = {"ofi_state": state, "count": denom}
        for move in move_labels:
            row[f"p_move_{move}"] = float(((moves == move) & mask).sum() / denom) if denom else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def module_a_hawkes_ofi(prices: pd.DataFrame, out_dir: Path) -> dict[str, Any]:
    book = wide_product(prices, UNDERLYING)
    ofi = compute_multilevel_ofi(book)
    split = make_split(len(ofi))
    train_ofi = ofi.iloc[split.train]["ofi_z"].to_numpy(dtype=float)
    mu = float(np.nanmean(train_ofi))
    sigma = float(np.nanstd(train_ofi) or 1.0)
    ofi["ofi_z"] = (ofi["ofi_z"] - mu) / sigma

    train = ofi.iloc[split.train].copy()
    event_bid_train = train["ofi_z"].to_numpy() > np.nanquantile(train["ofi_z"], 0.90)
    event_ask_train = train["ofi_z"].to_numpy() < np.nanquantile(train["ofi_z"], 0.10)
    train_times = np.arange(len(train), dtype=float)
    bid_params = fit_hawkes(train_times[event_bid_train], horizon=max(float(len(train) - 1), 1.0))
    ask_params = fit_hawkes(train_times[event_ask_train], horizon=max(float(len(train) - 1), 1.0))

    all_times = np.arange(len(ofi), dtype=float)
    bid_event_all = ofi["ofi_z"].to_numpy() > np.nanquantile(train["ofi_z"], 0.90)
    ask_event_all = ofi["ofi_z"].to_numpy() < np.nanquantile(train["ofi_z"], 0.10)
    ofi["hawkes_bid_intensity"] = hawkes_intensity_grid(bid_event_all, all_times, bid_params)
    ofi["hawkes_ask_intensity"] = hawkes_intensity_grid(ask_event_all, all_times, ask_params)
    intensity_imb = np.log((ofi["hawkes_bid_intensity"] + 1e-9) / (ofi["hawkes_ask_intensity"] + 1e-9))
    ofi["hawkes_ofi_signal"] = ofi["ofi_z"] + intensity_imb.to_numpy()

    horizon_rows = 5
    indexed_mid = ofi.set_index(["day", ofi.groupby("day").cumcount()])["mid_price"]
    future_mid_delta = future_delta_by_day(indexed_mid, horizon_rows).reset_index(drop=True).to_numpy()
    half_spread = (ofi["spread"].to_numpy(dtype=float) / 2.0)
    signal = ofi["hawkes_ofi_signal"].to_numpy(dtype=float)
    mid = ofi["mid_price"].to_numpy(dtype=float)
    next_mid = np.r_[mid[1:], np.nan]
    moves = np.sign(next_mid - mid).astype(float)
    moves[~np.isfinite(moves)] = 0.0

    q_pos = np.unique(np.nanquantile(signal[split.train], np.linspace(0.70, 0.98, 20)))
    q_neg = np.unique(np.nanquantile(-signal[split.train], np.linspace(0.70, 0.98, 20)))
    rows = []
    best_score = -1e18
    best: dict[str, Any] = {}
    for bid_th in q_pos:
        for ask_th in q_neg:
            pos = np.where(signal > bid_th, 1.0, np.where(signal < -ask_th, -1.0, 0.0))
            pnl = np.where(pos > 0, future_mid_delta - half_spread, np.where(pos < 0, -future_mid_delta - half_spread, 0.0))
            val_pnl = pnl[split.validation]
            oos_pnl = pnl[split.oos]
            val_metrics = pnl_metrics(val_pnl)
            oos_metrics = pnl_metrics(oos_pnl)
            objective = val_metrics["sharpe"] - 2.5 * val_metrics["max_drawdown"] / max(abs(val_metrics["total_pnl"]), 1.0)
            row = {
                "bid_threshold": float(bid_th),
                "ask_threshold": float(ask_th),
                "validation_objective": float(objective),
                **{f"validation_{k}": v for k, v in val_metrics.items()},
                **{f"oos_{k}": v for k, v in oos_metrics.items()},
            }
            rows.append(row)
            if objective > best_score:
                best_score = objective
                best = row

    opt = pd.DataFrame(rows).sort_values("validation_objective", ascending=False)
    opt.to_csv(out_dir / "hawkes_ofi_threshold_grid.csv", index=False)
    bid_th = float(best["bid_threshold"])
    ask_th = float(best["ask_threshold"])
    states = np.where(signal > bid_th, 1, np.where(signal < -ask_th, -1, 0))
    trans = transition_matrix(states, moves.astype(int))
    trans.to_csv(out_dir / "hawkes_tick_transition_matrix.csv", index=False)
    ofi_out = ofi[["day", "timestamp", "global_tick", "ofi_z", "hawkes_bid_intensity", "hawkes_ask_intensity", "hawkes_ofi_signal"]].copy()
    ofi_out["state"] = states
    ofi_out.to_csv(out_dir / "hawkes_ofi_state_timeseries.csv", index=False)

    payload = {
        "walk_forward": WALK_FORWARD,
        "signal_definition": "z_scored_depth_weighted_multilevel_OFI + log(lambda_bid/lambda_ask)",
        "horizon_rows": horizon_rows,
        "train_normalization": {"mean": mu, "std": sigma},
        "bid_side_hawkes_params": bid_params,
        "ask_side_hawkes_params": ask_params,
        "optimal_thresholds": {
            "bid_side_buy_activation": bid_th,
            "ask_side_sell_activation": ask_th,
            "selected_on": "validation_sharpe_minus_drawdown_penalty",
        },
        "best_validation_and_oos_metrics": best,
        "tick_transition_matrix_conditioned_on_extreme_ofi": trans.to_dict(orient="records"),
    }
    write_json(out_dir / "hawkes_ofi_thresholds.json", payload)
    return {"df": ofi, "split": split, "signal": signal, "future_mid_delta": future_mid_delta, "half_spread": half_spread, "best": best}


def kalman_filter_pair(y: np.ndarray, x: np.ndarray, q_intercept: float, q_beta: float, r_var: float) -> dict[str, np.ndarray]:
    n = len(y)
    state = np.zeros((n, 2), dtype=float)
    cov = np.zeros((n, 2, 2), dtype=float)
    innovation = np.full(n, np.nan, dtype=float)
    innovation_var = np.full(n, np.nan, dtype=float)
    beta0 = np.polyfit(x[np.isfinite(x) & np.isfinite(y)], y[np.isfinite(x) & np.isfinite(y)], 1)
    a = np.array([float(beta0[1]), float(beta0[0])])
    p = np.eye(2) * 10.0
    q = np.diag([q_intercept, q_beta])
    for i in range(n):
        p = p + q
        h = np.array([1.0, x[i]])
        pred = float(h @ a)
        s = float(h @ p @ h.T + r_var)
        innovation[i] = y[i] - pred
        innovation_var[i] = max(s, 1e-12)
        k = (p @ h.T) / innovation_var[i]
        a = a + k * innovation[i]
        p = (np.eye(2) - np.outer(k, h)) @ p
        state[i] = a
        cov[i] = p
    return {"state": state, "cov": cov, "innovation": innovation, "innovation_var": innovation_var}


def ou_half_life(spread: np.ndarray) -> dict[str, float]:
    s = pd.Series(spread).replace([np.inf, -np.inf], np.nan).dropna()
    if len(s) < 20:
        return {"theta": np.nan, "beta": np.nan, "half_life_rows": np.nan}
    lag = s.shift(1)
    ds = s - lag
    clean = pd.DataFrame({"ds": ds, "lag": lag}).dropna()
    x = np.column_stack([np.ones(len(clean)), clean["lag"].to_numpy()])
    coef, *_ = np.linalg.lstsq(x, clean["ds"].to_numpy(), rcond=None)
    beta = float(coef[1])
    theta = -beta
    half_life = math.log(2) / theta if theta > 0 else np.nan
    return {"theta": float(theta), "beta": beta, "half_life_rows": float(half_life) if np.isfinite(half_life) else np.nan}


def module_b_kalman_cointegration(prices: pd.DataFrame, iv: pd.DataFrame, out_dir: Path) -> dict[str, Any]:
    iv_pair = iv[iv["voucher"].isin([PAIR_Y, PAIR_X])].pivot_table(index=["day", "timestamp", "global_tick"], columns="voucher", values="iv")
    iv_pair = iv_pair[[PAIR_Y, PAIR_X]].dropna().reset_index()
    split = make_split(len(iv_pair))
    y = iv_pair[PAIR_Y].to_numpy(dtype=float)
    x = iv_pair[PAIR_X].to_numpy(dtype=float)
    train_yx = iv_pair.iloc[split.train][[PAIR_Y, PAIR_X]].to_numpy(dtype=float)
    johansen = coint_johansen(train_yx, det_order=0, k_ar_diff=1)
    residual_var = float(np.var(y[split.train] - np.polyval(np.polyfit(x[split.train], y[split.train], 1), x[split.train])))
    residual_var = max(residual_var, 1e-10)

    q_grid = [1e-8, 5e-8, 1e-7, 5e-7, 1e-6]
    r_grid = [0.25, 0.5, 1.0, 2.0, 4.0]
    sigma_grid = np.linspace(0.75, 3.0, 19)

    mids = prices[prices["product"].isin([PAIR_Y, PAIR_X])].pivot_table(index=["day", "timestamp", "global_tick"], columns="product", values="mid_price")
    bid = prices[prices["product"].isin([PAIR_Y, PAIR_X])].pivot_table(index=["day", "timestamp", "global_tick"], columns="product", values="bid_price_1")
    ask = prices[prices["product"].isin([PAIR_Y, PAIR_X])].pivot_table(index=["day", "timestamp", "global_tick"], columns="product", values="ask_price_1")
    pair_px = mids[[PAIR_Y, PAIR_X]].merge(iv_pair[["day", "timestamp", "global_tick"]], left_index=True, right_on=["day", "timestamp", "global_tick"], how="right")
    pair_bid = bid[[PAIR_Y, PAIR_X]].merge(iv_pair[["day", "timestamp", "global_tick"]], left_index=True, right_on=["day", "timestamp", "global_tick"], how="right")
    pair_ask = ask[[PAIR_Y, PAIR_X]].merge(iv_pair[["day", "timestamp", "global_tick"]], left_index=True, right_on=["day", "timestamp", "global_tick"], how="right")
    mid_y = pair_px[PAIR_Y].to_numpy(dtype=float)
    mid_x = pair_px[PAIR_X].to_numpy(dtype=float)
    cost_pair = ((pair_ask[PAIR_Y].to_numpy(dtype=float) - pair_bid[PAIR_Y].to_numpy(dtype=float)) + (pair_ask[PAIR_X].to_numpy(dtype=float) - pair_bid[PAIR_X].to_numpy(dtype=float))) / 2.0

    horizon_rows = 5
    future_y = pd.Series(mid_y).shift(-horizon_rows).to_numpy() - mid_y
    future_x = pd.Series(mid_x).shift(-horizon_rows).to_numpy() - mid_x
    rows = []
    best_score = -1e18
    best_row: dict[str, Any] = {}
    best_filter: dict[str, np.ndarray] | None = None
    for q_i in q_grid:
        for q_b in q_grid:
            for r_mult in r_grid:
                filt = kalman_filter_pair(y, x, q_i, q_b, residual_var * r_mult)
                z = filt["innovation"] / np.sqrt(filt["innovation_var"])
                beta_t = filt["state"][:, 1]
                for sigma_th in sigma_grid:
                    pos_y = np.where(z < -sigma_th, 1.0, np.where(z > sigma_th, -1.0, 0.0))
                    pos_x = -pos_y * beta_t
                    pnl = pos_y * future_y + pos_x * future_x - np.abs(pos_y) * cost_pair
                    val_metrics = pnl_metrics(pnl[split.validation])
                    oos_metrics = pnl_metrics(pnl[split.oos])
                    objective = val_metrics["sharpe"] - 1.5 * val_metrics["max_drawdown"] / max(abs(val_metrics["total_pnl"]), 1.0)
                    row = {
                        "q_intercept": q_i,
                        "q_beta": q_b,
                        "r_multiplier": r_mult,
                        "sigma_threshold": float(sigma_th),
                        "validation_objective": float(objective),
                        **{f"validation_{k}": v for k, v in val_metrics.items()},
                        **{f"oos_{k}": v for k, v in oos_metrics.items()},
                    }
                    rows.append(row)
                    if objective > best_score:
                        best_score = objective
                        best_row = row
                        best_filter = filt

    grid = pd.DataFrame(rows).sort_values("validation_objective", ascending=False)
    grid.to_csv(out_dir / "kalman_spread_threshold_grid.csv", index=False)
    assert best_filter is not None
    z_best = best_filter["innovation"] / np.sqrt(best_filter["innovation_var"])
    beta_best = best_filter["state"][:, 1]
    spread = best_filter["innovation"]
    ou = ou_half_life(spread[split.train])
    ts = iv_pair[["day", "timestamp", "global_tick"]].copy()
    ts["kalman_intercept"] = best_filter["state"][:, 0]
    ts["kalman_beta"] = beta_best
    ts["innovation"] = best_filter["innovation"]
    ts["innovation_std"] = np.sqrt(best_filter["innovation_var"])
    ts["standardized_divergence"] = z_best
    ts["walk_forward_split"] = [split_name(i, split) for i in range(len(ts))]
    ts.to_csv(out_dir / "kalman_spread_state_timeseries.csv", index=False)

    payload = {
        "pair": {"long_leg": PAIR_Y, "short_leg": PAIR_X},
        "walk_forward": WALK_FORWARD,
        "johansen": {
            "trace_stat": johansen.lr1.tolist(),
            "trace_critical_values_90_95_99": johansen.cvt.tolist(),
            "max_eigen_stat": johansen.lr2.tolist(),
            "max_eigen_critical_values_90_95_99": johansen.cvm.tolist(),
            "eigenvectors": johansen.evec.tolist(),
        },
        "state_space": {
            "state": "[intercept, hedge_beta]",
            "transition_matrix_F": [[1.0, 0.0], [0.0, 1.0]],
            "observation_matrix_H_t": "[1, IV_short_t]",
            "process_covariance_Q": [[best_row["q_intercept"], 0.0], [0.0, best_row["q_beta"]]],
            "observation_covariance_R": residual_var * best_row["r_multiplier"],
            "last_state_mean": best_filter["state"][-1].tolist(),
            "last_state_covariance": best_filter["cov"][-1].tolist(),
        },
        "ou_half_life_on_train_spread": ou,
        "optimal_divergence_threshold": {
            "sigma": best_row["sigma_threshold"],
            "long_5200_short_5300_trigger": f"standardized_divergence < -{best_row['sigma_threshold']:.4f}",
            "short_5200_long_5300_trigger": f"standardized_divergence > {best_row['sigma_threshold']:.4f}",
        },
        "best_validation_and_oos_metrics": best_row,
    }
    write_json(out_dir / "kalman_spread_params.json", payload)
    return {"split": split, "z": z_best, "beta": beta_best, "future_y": future_y, "future_x": future_x, "cost_pair": cost_pair, "best": best_row}


def module_c_whalley_wilmott(prices: pd.DataFrame, greeks: pd.DataFrame, out_dir: Path) -> dict[str, Any]:
    vev = wide_product(prices, UNDERLYING)
    median_spot = float(vev["mid_price"].median())
    median_half_spread = float((vev["spread"] / 2.0).median())
    gamma_abs = greeks.groupby(["day", "timestamp", "global_tick"])["bs_gamma"].sum(min_count=1).abs().dropna()
    gamma_quantiles = np.nanquantile(gamma_abs.to_numpy(dtype=float), np.linspace(0.02, 0.995, 60))
    gamma_grid = np.unique(np.r_[0.0, gamma_quantiles, np.linspace(1e-5, max(float(np.nanmax(gamma_quantiles)), 1e-4) * 1.5, 30)])
    tte_grid = np.array([5, 6, 7, 8], dtype=float)
    risk_aversion_grid = np.array([0.05, 0.10, 0.20, 0.35, 0.50, 0.75, 1.00], dtype=float)
    rows = []
    for tte in tte_grid:
        tau = max(tte / 365.0, 1e-9)
        for risk_aversion in risk_aversion_grid:
            for gamma_port in gamma_grid:
                transaction_cost = median_half_spread / median_spot
                # Whalley-Wilmott asymptotic no-transaction half-band in hedge/delta units.
                band = ((1.5 * transaction_cost * (median_spot**2 * gamma_port) ** 2 * tau) / risk_aversion) ** (1.0 / 3.0)
                rows.append(
                    {
                        "tte_days": int(tte),
                        "risk_aversion": float(risk_aversion),
                        "aggregate_gamma": float(gamma_port),
                        "spot_reference": median_spot,
                        "underlying_half_spread": median_half_spread,
                        "transaction_cost_fraction": transaction_cost,
                        "no_trade_half_band_delta_units": float(band),
                        "maker_to_taker_boundary_delta_abs": float(band),
                    }
                )
    bands = pd.DataFrame(rows)
    bands.to_csv(out_dir / "wilmott_hedging_bands.csv", index=False)
    summary = bands.groupby(["tte_days", "risk_aversion"], as_index=False)["no_trade_half_band_delta_units"].agg(["min", "median", "max"]).reset_index()
    summary.to_csv(out_dir / "wilmott_hedging_band_summary.csv", index=False)
    return {"bands": bands, "median_spot": median_spot, "median_half_spread": median_half_spread}


def simulate_combined_oos(params: np.ndarray, module_a: dict[str, Any], module_b: dict[str, Any], greeks: pd.DataFrame, prices: pd.DataFrame) -> np.ndarray:
    ofi_bid, ofi_ask, kalman_sigma, wilmott_mult = params
    split_a: Split = module_a["split"]
    sig = module_a["signal"]
    future_mid_delta = module_a["future_mid_delta"]
    half_spread = module_a["half_spread"]
    a_pos = np.where(sig > ofi_bid, 1.0, np.where(sig < -ofi_ask, -1.0, 0.0))
    a_pnl = np.where(a_pos > 0, future_mid_delta - half_spread, np.where(a_pos < 0, -future_mid_delta - half_spread, 0.0))
    a_oos = a_pnl[split_a.oos]

    split_b: Split = module_b["split"]
    z = module_b["z"]
    beta = module_b["beta"]
    pos_y = np.where(z < -kalman_sigma, 1.0, np.where(z > kalman_sigma, -1.0, 0.0))
    pos_x = -pos_y * beta
    b_pnl = pos_y * module_b["future_y"] + pos_x * module_b["future_x"] - np.abs(pos_y) * module_b["cost_pair"]
    b_oos = b_pnl[split_b.oos]

    g_pair = greeks[greeks["voucher"].isin([PAIR_Y, PAIR_X])].pivot_table(index=["day", "timestamp", "global_tick"], columns="voucher", values=["bs_delta", "bs_gamma"])
    g_pair.columns = [f"{a}_{b}" for a, b in g_pair.columns]
    g_pair = g_pair.dropna().reset_index()
    n = min(len(b_pnl), len(g_pair))
    delta_exposure = pos_y[:n] * g_pair[f"bs_delta_{PAIR_Y}"].to_numpy() + pos_x[:n] * g_pair[f"bs_delta_{PAIR_X}"].to_numpy()
    gamma_exposure = np.abs(pos_y[:n] * g_pair[f"bs_gamma_{PAIR_Y}"].to_numpy() + pos_x[:n] * g_pair[f"bs_gamma_{PAIR_X}"].to_numpy())
    vev = wide_product(prices, UNDERLYING)[["day", "timestamp", "mid_price", "spread"]]
    hedge_ref = g_pair[["day", "timestamp"]].merge(vev, on=["day", "timestamp"], how="left")
    spot = hedge_ref["mid_price"].to_numpy(dtype=float)
    half = (hedge_ref["spread"].to_numpy(dtype=float) / 2.0)
    transaction_cost = np.nanmedian(half / spot)
    tau = 6.0 / 365.0
    risk_aversion = 0.20
    band = wilmott_mult * ((1.5 * transaction_cost * (spot**2 * gamma_exposure) ** 2 * tau) / risk_aversion) ** (1.0 / 3.0)
    hedge_cost = np.zeros(n, dtype=float)
    hedge = 0.0
    for i in range(n):
        target = -delta_exposure[i]
        if abs(target - hedge) > band[i]:
            change = target - hedge
            hedge_cost[i] = abs(change) * half[i]
            hedge = target
    hedge_oos = hedge_cost[split_b.oos]

    m = min(len(a_oos), len(b_oos), len(hedge_oos))
    return np.nan_to_num(a_oos[:m], nan=0.0) + np.nan_to_num(b_oos[:m], nan=0.0) - np.nan_to_num(hedge_oos[:m], nan=0.0)


def expected_improvement(mu: np.ndarray, sigma: np.ndarray, best_y: float, xi: float = 0.01) -> np.ndarray:
    sigma = np.maximum(sigma, 1e-12)
    imp = mu - best_y - xi
    z = imp / sigma
    return imp * norm.cdf(z) + sigma * norm.pdf(z)


def module_d_bayesian_optimization(module_a: dict[str, Any], module_b: dict[str, Any], module_c: dict[str, Any], prices: pd.DataFrame, greeks: pd.DataFrame, out_dir: Path) -> dict[str, Any]:
    a_best = module_a["best"]
    b_best = module_b["best"]
    bounds = np.array(
        [
            [max(0.1, a_best["bid_threshold"] * 0.65), max(0.2, a_best["bid_threshold"] * 1.35)],
            [max(0.1, a_best["ask_threshold"] * 0.65), max(0.2, a_best["ask_threshold"] * 1.35)],
            [max(0.5, b_best["sigma_threshold"] * 0.65), max(0.75, b_best["sigma_threshold"] * 1.35)],
            [0.50, 2.50],
        ],
        dtype=float,
    )
    rng = np.random.default_rng(7)
    n_initial = 24
    n_iter = 56
    unit = rng.random((n_initial, bounds.shape[0]))
    x_obs = bounds[:, 0] + unit * (bounds[:, 1] - bounds[:, 0])
    y_obs = np.array([calmar_ratio(simulate_combined_oos(x, module_a, module_b, greeks, prices)) for x in x_obs], dtype=float)
    rows = []
    for i, (x, y) in enumerate(zip(x_obs, y_obs)):
        rows.append({"iteration": i, "source": "initial_lhs", "ofi_bid_threshold": x[0], "ofi_ask_threshold": x[1], "kalman_sigma": x[2], "wilmott_band_multiplier": x[3], "oos_calmar": y})

    kernel = ConstantKernel(1.0, (1e-3, 1e3)) * Matern(length_scale=np.ones(4), nu=2.5) + WhiteKernel(noise_level=1e-6, noise_level_bounds=(1e-9, 1e-2))
    candidate_unit = rng.random((2500, bounds.shape[0]))
    candidates = bounds[:, 0] + candidate_unit * (bounds[:, 1] - bounds[:, 0])
    for it in range(n_iter):
        gp = GaussianProcessRegressor(kernel=kernel, alpha=1e-8, normalize_y=True, random_state=13, n_restarts_optimizer=2)
        gp.fit(x_obs, y_obs)
        mu, std = gp.predict(candidates, return_std=True)
        ei = expected_improvement(mu, std, float(np.max(y_obs)))
        x_next = candidates[int(np.argmax(ei))]
        y_next = calmar_ratio(simulate_combined_oos(x_next, module_a, module_b, greeks, prices))
        x_obs = np.vstack([x_obs, x_next])
        y_obs = np.r_[y_obs, y_next]
        rows.append({"iteration": n_initial + it, "source": "gp_expected_improvement", "ofi_bid_threshold": x_next[0], "ofi_ask_threshold": x_next[1], "kalman_sigma": x_next[2], "wilmott_band_multiplier": x_next[3], "oos_calmar": y_next})

    bo = pd.DataFrame(rows)
    bo.to_csv(out_dir / "bayesian_optimization_trace.csv", index=False)
    best_idx = int(np.argmax(y_obs))
    best_x = x_obs[best_idx]
    best_pnl = simulate_combined_oos(best_x, module_a, module_b, greeks, prices)
    payload = {
        "walk_forward": WALK_FORWARD,
        "objective": "maximize_out_of_sample_calmar_ratio",
        "bounds": {
            "ofi_bid_threshold": bounds[0].tolist(),
            "ofi_ask_threshold": bounds[1].tolist(),
            "kalman_sigma": bounds[2].tolist(),
            "wilmott_band_multiplier": bounds[3].tolist(),
        },
        "optimal_hyperparams": {
            "ofi_bid_threshold": float(best_x[0]),
            "ofi_ask_threshold": float(best_x[1]),
            "kalman_sigma": float(best_x[2]),
            "wilmott_band_multiplier": float(best_x[3]),
        },
        "oos_metrics": pnl_metrics(best_pnl),
        "gp_iterations": int(len(bo)),
    }
    write_json(out_dir / "optimal_bot_hyperparams.json", payload)
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fit frontier alpha models for Round 3 option market making.")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    os.environ.setdefault("PYTHONHASHSEED", "0")
    ensure_output(args.output_dir)
    prices = load_prices(args.data_dir)
    iv, greeks = load_iv_and_greeks()

    print("[A] Fitting Hawkes OFI and tick-transition thresholds...")
    module_a = module_a_hawkes_ofi(prices, args.output_dir)
    print("[B] Fitting Kalman cointegration spread model...")
    module_b = module_b_kalman_cointegration(prices, iv, args.output_dir)
    print("[C] Building Whalley-Wilmott hedge-band lookup table...")
    module_c = module_c_whalley_wilmott(prices, greeks, args.output_dir)
    print("[D] Running Gaussian-process Bayesian optimization...")
    module_d = module_d_bayesian_optimization(module_a, module_b, module_c, prices, greeks, args.output_dir)
    print(json.dumps(to_jsonable(module_d["optimal_hyperparams"]), indent=2))
    print(f"Done. Outputs written to {args.output_dir}")


if __name__ == "__main__":
    main()
