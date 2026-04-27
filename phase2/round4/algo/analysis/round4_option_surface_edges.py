#!/usr/bin/env python3
from __future__ import annotations

"""
Round 4 option surface and executable edge calculator.

This script deliberately recomputes the option metrics from Round 4 price data
instead of trusting the Round 3 EDA outputs. The Round 3 EDA is still useful as
a design reference, but the final decision table must be based on the current
Round 4 snapshots.

Outputs:
- round4_option_surface_edge_timeseries.csv
- round4_option_surface_edge_summary.csv
- round4_option_surface_actionable_edges.csv
- round4_option_surface_fit_quality.csv
"""

import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.stats import norm

try:
    from numba import njit

    NUMBA_AVAILABLE = True
except Exception:  # noqa: BLE001 - keep the script usable without numba
    NUMBA_AVAILABLE = False


SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent / "data"

UNDERLYING = "VELVETFRUIT_EXTRACT"
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
ROBUST_CORE_FIT_VOUCHERS = ["VEV_5000", "VEV_5100", "VEV_5200", "VEV_5300", "VEV_5400", "VEV_5500"]
ALL_CONTRACT_FIT_VOUCHERS = VOUCHERS
TTE_DAYS = {1: 7, 2: 6, 3: 5}
RISK_FREE_RATE = 0.0
TICKS_PER_DAY = 10_000


def strike(product: str) -> int:
    return int(product.rsplit("_", 1)[1])


def bs_call_price(s: float, k: float, t: float, sigma: float, r: float = RISK_FREE_RATE) -> float:
    if t <= 0 or sigma <= 0 or s <= 0 or k <= 0:
        return max(s - k, 0.0)
    vol_sqrt = sigma * math.sqrt(t)
    d1 = (math.log(s / k) + (r + 0.5 * sigma * sigma) * t) / vol_sqrt
    d2 = d1 - vol_sqrt
    return s * norm.cdf(d1) - k * math.exp(-r * t) * norm.cdf(d2)


if NUMBA_AVAILABLE:

    @njit(cache=True)
    def _norm_cdf_numba(x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


    @njit(cache=True)
    def _bs_call_price_numba(s: float, k: float, t: float, sigma: float) -> float:
        if t <= 0.0 or sigma <= 0.0 or s <= 0.0 or k <= 0.0:
            return max(s - k, 0.0)
        vol_sqrt = sigma * math.sqrt(t)
        d1 = (math.log(s / k) + 0.5 * sigma * sigma * t) / vol_sqrt
        d2 = d1 - vol_sqrt
        return s * _norm_cdf_numba(d1) - k * _norm_cdf_numba(d2)


    @njit(cache=True)
    def _implied_vol_arrays_numba(
        s_arr: np.ndarray,
        k_arr: np.ndarray,
        t_arr: np.ndarray,
        c_arr: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        n = len(s_arr)
        ivs = np.empty(n, dtype=np.float64)
        codes = np.empty(n, dtype=np.int64)
        for i in range(n):
            s = s_arr[i]
            k = k_arr[i]
            t = t_arr[i]
            c = c_arr[i]
            ivs[i] = np.nan
            codes[i] = 5
            if not (math.isfinite(s) and math.isfinite(k) and math.isfinite(t) and math.isfinite(c)):
                codes[i] = 1
                continue
            if s <= 0.0 or k <= 0.0 or t <= 0.0:
                codes[i] = 1
                continue
            intrinsic = max(s - k, 0.0)
            if c < intrinsic - 1e-9:
                codes[i] = 2
                continue
            if c > s + 1e-9:
                codes[i] = 3
                continue
            if abs(c - intrinsic) < 1e-9:
                ivs[i] = 1e-6
                codes[i] = 4
                continue

            lo = 1e-6
            hi = 5.0
            flo = _bs_call_price_numba(s, k, t, lo) - c
            fhi = _bs_call_price_numba(s, k, t, hi) - c
            if flo > 0.0 or fhi < 0.0:
                codes[i] = 5
                continue
            mid = 0.0
            for _ in range(80):
                mid = 0.5 * (lo + hi)
                fm = _bs_call_price_numba(s, k, t, mid) - c
                if fm > 0.0:
                    hi = mid
                else:
                    lo = mid
            ivs[i] = 0.5 * (lo + hi)
            codes[i] = 0
        return ivs, codes


def implied_vol(s: float, k: float, t: float, c: float) -> tuple[float, str]:
    if not np.isfinite([s, k, t, c]).all() or s <= 0 or k <= 0 or t <= 0:
        return float("nan"), "invalid_inputs"
    intrinsic = max(s - k, 0.0)
    upper = s
    if c < intrinsic - 1e-9:
        return float("nan"), "below_lower_bound"
    if c > upper + 1e-9:
        return float("nan"), "above_upper_bound"
    if abs(c - intrinsic) < 1e-9:
        return 1e-6, "zero_time_value"

    def f(sig: float) -> float:
        return bs_call_price(s, k, t, sig) - c

    try:
        return float(brentq(f, 1e-6, 5.0, maxiter=100)), "ok"
    except ValueError:
        return float("nan"), "root_fail"


def greeks(s: float, k: float, t: float, sigma: float) -> tuple[float, float, float, float]:
    if t <= 0 or sigma <= 0 or s <= 0 or k <= 0:
        return (float("nan"), float("nan"), float("nan"), float("nan"))
    vol_sqrt = sigma * math.sqrt(t)
    d1 = (math.log(s / k) + 0.5 * sigma * sigma * t) / vol_sqrt
    delta = norm.cdf(d1)
    gamma = norm.pdf(d1) / (s * vol_sqrt)
    vega = s * norm.pdf(d1) * math.sqrt(t) / 100.0
    theta = -(s * norm.pdf(d1) * sigma) / (2.0 * math.sqrt(t)) / 365.0
    return float(delta), float(gamma), float(vega), float(theta)


def read_prices() -> pd.DataFrame:
    frames = []
    for day, tte in TTE_DAYS.items():
        path = DATA_DIR / f"prices_round_4_day_{day}.csv"
        df = pd.read_csv(path, sep=";")
        df["day"] = day
        df["tte_days"] = tte
        frames.append(df)
    prices = pd.concat(frames, ignore_index=True)
    numeric = [
        "timestamp",
        "bid_price_1",
        "bid_volume_1",
        "ask_price_1",
        "ask_volume_1",
        "bid_price_2",
        "bid_volume_2",
        "ask_price_2",
        "ask_volume_2",
        "bid_price_3",
        "bid_volume_3",
        "ask_price_3",
        "ask_volume_3",
        "mid_price",
    ]
    for col in numeric:
        prices[col] = pd.to_numeric(prices[col], errors="coerce")
    return prices


def implied_vol_series(opts: pd.DataFrame) -> tuple[np.ndarray, list[str]]:
    if NUMBA_AVAILABLE:
        ivs, codes = _implied_vol_arrays_numba(
            opts["spot_mid"].to_numpy(np.float64),
            opts["strike"].to_numpy(np.float64),
            opts["tte_years"].to_numpy(np.float64),
            opts["option_mid"].to_numpy(np.float64),
        )
        labels = {
            0: "ok",
            1: "invalid_inputs",
            2: "below_lower_bound",
            3: "above_upper_bound",
            4: "zero_time_value",
            5: "root_fail",
        }
        return ivs, [labels[int(code)] for code in codes]

    ivs = []
    statuses = []
    for row in opts.itertuples(index=False):
        iv, status = implied_vol(float(row.spot_mid), float(row.strike), float(row.tte_years), float(row.option_mid))
        ivs.append(iv)
        statuses.append(status)
    return np.asarray(ivs, dtype=float), statuses


def build_surface_rows(prices: pd.DataFrame) -> pd.DataFrame:
    spot = prices[prices["product"] == UNDERLYING][["day", "timestamp", "mid_price", "bid_price_1", "ask_price_1"]].copy()
    spot.rename(
        columns={
            "mid_price": "spot_mid",
            "bid_price_1": "spot_bid",
            "ask_price_1": "spot_ask",
        },
        inplace=True,
    )
    opts = prices[prices["product"].isin(VOUCHERS)].copy()
    opts = opts.merge(spot, on=["day", "timestamp"], how="left")
    opts["strike"] = opts["product"].map(strike)
    opts["tte_years"] = opts["tte_days"] / 365.0
    opts["option_mid"] = opts["mid_price"]
    opts["option_spread"] = opts["ask_price_1"] - opts["bid_price_1"]
    opts["intrinsic"] = np.maximum(opts["spot_mid"] - opts["strike"], 0.0)
    opts["extrinsic_mid"] = opts["option_mid"] - opts["intrinsic"]
    opts["log_moneyness"] = np.log(opts["strike"] / opts["spot_mid"])

    ivs, statuses = implied_vol_series(opts)
    opts["mid_iv"] = ivs
    opts["iv_status"] = statuses
    return opts


def fit_surface(opts: pd.DataFrame, fit_vouchers: list[str], model_name: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    fit_rows = []
    for (day, timestamp), group in opts.groupby(["day", "timestamp"], sort=True):
        fit = group[
            group["product"].isin(fit_vouchers)
            & group["mid_iv"].notna()
            & group["iv_status"].eq("ok")
            & np.isfinite(group["log_moneyness"])
        ].copy()
        quality = {
            "surface_model": model_name,
            "day": day,
            "timestamp": timestamp,
            "fit_points": int(len(fit)),
            "fit_rmse_iv": float("nan"),
            "fit_r2_iv": float("nan"),
            "coef_a": float("nan"),
            "coef_b": float("nan"),
            "coef_c": float("nan"),
        }
        if len(fit) >= 3:
            x = fit["log_moneyness"].to_numpy(float)
            y = fit["mid_iv"].to_numpy(float)
            coef = np.polyfit(x, y, deg=2)
            pred_fit = np.polyval(coef, x)
            resid = y - pred_fit
            ss_res = float(np.sum(resid * resid))
            ss_tot = float(np.sum((y - y.mean()) ** 2))
            quality.update(
                {
                    "fit_rmse_iv": float(math.sqrt(ss_res / len(y))),
                    "fit_r2_iv": float(1.0 - ss_res / ss_tot) if ss_tot > 0 else float("nan"),
                    "coef_a": float(coef[0]),
                    "coef_b": float(coef[1]),
                    "coef_c": float(coef[2]),
                }
            )
            g = group.copy()
            g[f"{model_name}_surface_iv"] = np.clip(np.polyval(coef, g["log_moneyness"].to_numpy(float)), 1e-6, 5.0)
        else:
            g = group.copy()
            g[f"{model_name}_surface_iv"] = np.nan
        fit_rows.append(quality)
        rows.append(g)
    surface = pd.concat(rows, ignore_index=True)
    fit_quality = pd.DataFrame(fit_rows)
    return surface, fit_quality


def add_edges(surface: pd.DataFrame, model_name: str) -> pd.DataFrame:
    fair_values = []
    deltas = []
    gammas = []
    vegas = []
    thetas = []
    iv_col = f"{model_name}_surface_iv"
    for row in surface.itertuples(index=False):
        surface_iv = getattr(row, iv_col)
        if not np.isfinite(surface_iv):
            fair_values.append(float("nan"))
            deltas.append(float("nan"))
            gammas.append(float("nan"))
            vegas.append(float("nan"))
            thetas.append(float("nan"))
            continue
        fair = bs_call_price(float(row.spot_mid), float(row.strike), float(row.tte_years), float(surface_iv))
        delta, gamma, vega, theta = greeks(float(row.spot_mid), float(row.strike), float(row.tte_years), float(surface_iv))
        fair_values.append(fair)
        deltas.append(delta)
        gammas.append(gamma)
        vegas.append(vega)
        thetas.append(theta)
    surface[f"{model_name}_surface_fair"] = fair_values
    surface[f"{model_name}_surface_delta"] = deltas
    surface[f"{model_name}_surface_gamma"] = gammas
    surface[f"{model_name}_surface_vega"] = vegas
    surface[f"{model_name}_surface_theta_per_day"] = thetas

    surface[f"{model_name}_fair_minus_mid"] = surface[f"{model_name}_surface_fair"] - surface["option_mid"]
    surface[f"{model_name}_buy_edge_at_ask"] = surface[f"{model_name}_surface_fair"] - surface["ask_price_1"]
    surface[f"{model_name}_sell_edge_at_bid"] = surface["bid_price_1"] - surface[f"{model_name}_surface_fair"]
    surface["spot_half_spread"] = (surface["spot_ask"] - surface["spot_bid"]) / 2.0
    surface[f"{model_name}_delta_hedge_halfspread_cost"] = surface[f"{model_name}_surface_delta"].abs() * surface["spot_half_spread"]

    # This is a deliberately conservative inventory gate, not a theoretical
    # proof. It requires an executable edge to beat both option spread noise and
    # the cost of hedging delta through the underlying spread.
    surface[f"{model_name}_min_edge_to_use_inventory"] = np.maximum.reduce(
        [
            np.ones(len(surface)),
            0.35 * surface["option_spread"].fillna(0.0).to_numpy(float),
            surface[f"{model_name}_delta_hedge_halfspread_cost"].fillna(0.0).to_numpy(float),
        ]
    )
    surface[f"{model_name}_buy_passes_inventory_gate"] = (
        surface[f"{model_name}_buy_edge_at_ask"] >= surface[f"{model_name}_min_edge_to_use_inventory"]
    )
    surface[f"{model_name}_sell_passes_inventory_gate"] = (
        surface[f"{model_name}_sell_edge_at_bid"] >= surface[f"{model_name}_min_edge_to_use_inventory"]
    )
    surface["tail_floor_contract"] = surface["product"].isin(["VEV_6000", "VEV_6500"])
    surface["robust_core_surface_contract"] = surface["product"].isin(ROBUST_CORE_FIT_VOUCHERS)
    return surface


def summarize(surface: pd.DataFrame, fit_quality: pd.DataFrame) -> pd.DataFrame:
    def q(x: pd.Series, p: float) -> float:
        return float(x.quantile(p))

    summary = surface.groupby("product").agg(
        rows=("timestamp", "count"),
        mean_mid=("option_mid", "mean"),
        mean_spread=("option_spread", "mean"),
        p95_spread=("option_spread", lambda x: q(x, 0.95)),
        mean_mid_iv=("mid_iv", "mean"),
        robust_mean_surface_iv=("robust_core_surface_iv", "mean"),
        all_mean_surface_iv=("all_contracts_surface_iv", "mean"),
        robust_mean_fair_minus_mid=("robust_core_fair_minus_mid", "mean"),
        all_mean_fair_minus_mid=("all_contracts_fair_minus_mid", "mean"),
        robust_p05_fair_minus_mid=("robust_core_fair_minus_mid", lambda x: q(x.dropna(), 0.05)),
        robust_p95_fair_minus_mid=("robust_core_fair_minus_mid", lambda x: q(x.dropna(), 0.95)),
        all_p05_fair_minus_mid=("all_contracts_fair_minus_mid", lambda x: q(x.dropna(), 0.05)),
        all_p95_fair_minus_mid=("all_contracts_fair_minus_mid", lambda x: q(x.dropna(), 0.95)),
        robust_mean_buy_edge_at_ask=("robust_core_buy_edge_at_ask", "mean"),
        robust_max_buy_edge_at_ask=("robust_core_buy_edge_at_ask", "max"),
        robust_buy_edge_positive_rows=("robust_core_buy_edge_at_ask", lambda x: int((x > 0).sum())),
        robust_buy_edge_gate_rows=("robust_core_buy_passes_inventory_gate", "sum"),
        robust_mean_sell_edge_at_bid=("robust_core_sell_edge_at_bid", "mean"),
        robust_max_sell_edge_at_bid=("robust_core_sell_edge_at_bid", "max"),
        robust_sell_edge_positive_rows=("robust_core_sell_edge_at_bid", lambda x: int((x > 0).sum())),
        robust_sell_edge_gate_rows=("robust_core_sell_passes_inventory_gate", "sum"),
        all_mean_buy_edge_at_ask=("all_contracts_buy_edge_at_ask", "mean"),
        all_max_buy_edge_at_ask=("all_contracts_buy_edge_at_ask", "max"),
        all_buy_edge_positive_rows=("all_contracts_buy_edge_at_ask", lambda x: int((x > 0).sum())),
        all_buy_edge_gate_rows=("all_contracts_buy_passes_inventory_gate", "sum"),
        all_mean_sell_edge_at_bid=("all_contracts_sell_edge_at_bid", "mean"),
        all_max_sell_edge_at_bid=("all_contracts_sell_edge_at_bid", "max"),
        all_sell_edge_positive_rows=("all_contracts_sell_edge_at_bid", lambda x: int((x > 0).sum())),
        all_sell_edge_gate_rows=("all_contracts_sell_passes_inventory_gate", "sum"),
        robust_mean_delta=("robust_core_surface_delta", "mean"),
        robust_mean_gamma=("robust_core_surface_gamma", "mean"),
        robust_mean_vega=("robust_core_surface_vega", "mean"),
        robust_mean_theta_per_day=("robust_core_surface_theta_per_day", "mean"),
        robust_mean_min_edge_gate=("robust_core_min_edge_to_use_inventory", "mean"),
        all_mean_delta=("all_contracts_surface_delta", "mean"),
        all_mean_gamma=("all_contracts_surface_gamma", "mean"),
        all_mean_vega=("all_contracts_surface_vega", "mean"),
        all_mean_theta_per_day=("all_contracts_surface_theta_per_day", "mean"),
        all_mean_min_edge_gate=("all_contracts_min_edge_to_use_inventory", "mean"),
    ).reset_index()
    summary["robust_buy_gate_rate"] = summary["robust_buy_edge_gate_rows"] / summary["rows"]
    summary["robust_sell_gate_rate"] = summary["robust_sell_edge_gate_rows"] / summary["rows"]
    summary["all_buy_gate_rate"] = summary["all_buy_edge_gate_rows"] / summary["rows"]
    summary["all_sell_gate_rate"] = summary["all_sell_edge_gate_rows"] / summary["rows"]
    summary["surface_disagreement_mean_abs"] = (
        summary["robust_mean_fair_minus_mid"] - summary["all_mean_fair_minus_mid"]
    ).abs()

    def classify(row: pd.Series) -> str:
        product = row["product"]
        if product in ("VEV_6000", "VEV_6500"):
            return "Tail floor contract; include as diagnostic/relative-value only, never pay 1 blindly."
        if product in ("VEV_4000", "VEV_4500"):
            return "Deep ITM; include in surface diagnostics, but treat mainly as delta/Mark38 fade."
        if row["robust_buy_gate_rate"] > 0.02 and row["robust_sell_gate_rate"] > 0.02:
            return "Two-sided surface relative-value candidate; require player filters."
        if row["robust_buy_gate_rate"] > 0.02:
            return "Buy-underpriced candidate when ask edge survives inventory gate."
        if row["robust_sell_gate_rate"] > 0.02:
            return "Sell-overpriced candidate when bid edge survives inventory gate."
        return "Mostly quote/flow execution product; surface edge rarely executable."

    summary["interpretation"] = summary.apply(classify, axis=1)
    return summary


def main() -> None:
    prices = read_prices()
    opts = build_surface_rows(prices)
    robust_surface, robust_quality = fit_surface(opts, ROBUST_CORE_FIT_VOUCHERS, "robust_core")
    all_surface, all_quality = fit_surface(opts, ALL_CONTRACT_FIT_VOUCHERS, "all_contracts")
    surface = robust_surface.merge(
        all_surface[
            [
                "day",
                "timestamp",
                "product",
                "all_contracts_surface_iv",
            ]
        ],
        on=["day", "timestamp", "product"],
        how="left",
    )
    fit_quality = pd.concat([robust_quality, all_quality], ignore_index=True)
    surface = add_edges(surface, "robust_core")
    surface = add_edges(surface, "all_contracts")
    summary = summarize(surface, fit_quality)

    keep = [
        "day",
        "timestamp",
        "product",
        "strike",
        "tte_days",
        "spot_mid",
        "bid_price_1",
        "ask_price_1",
        "option_mid",
        "option_spread",
        "intrinsic",
        "extrinsic_mid",
        "mid_iv",
        "iv_status",
        "robust_core_surface_iv",
        "robust_core_surface_fair",
        "robust_core_fair_minus_mid",
        "robust_core_buy_edge_at_ask",
        "robust_core_sell_edge_at_bid",
        "robust_core_surface_delta",
        "robust_core_surface_gamma",
        "robust_core_surface_vega",
        "robust_core_surface_theta_per_day",
        "robust_core_delta_hedge_halfspread_cost",
        "robust_core_min_edge_to_use_inventory",
        "robust_core_buy_passes_inventory_gate",
        "robust_core_sell_passes_inventory_gate",
        "all_contracts_surface_iv",
        "all_contracts_surface_fair",
        "all_contracts_fair_minus_mid",
        "all_contracts_buy_edge_at_ask",
        "all_contracts_sell_edge_at_bid",
        "all_contracts_surface_delta",
        "all_contracts_surface_gamma",
        "all_contracts_surface_vega",
        "all_contracts_surface_theta_per_day",
        "all_contracts_delta_hedge_halfspread_cost",
        "all_contracts_min_edge_to_use_inventory",
        "all_contracts_buy_passes_inventory_gate",
        "all_contracts_sell_passes_inventory_gate",
        "tail_floor_contract",
        "robust_core_surface_contract",
    ]
    surface[keep].to_csv(DATA_DIR / "round4_option_surface_edge_timeseries.csv", index=False)
    summary.to_csv(DATA_DIR / "round4_option_surface_edge_summary.csv", index=False)
    fit_quality.to_csv(DATA_DIR / "round4_option_surface_fit_quality.csv", index=False)

    actionable = surface[
        surface["robust_core_buy_passes_inventory_gate"]
        | surface["robust_core_sell_passes_inventory_gate"]
        | surface["all_contracts_buy_passes_inventory_gate"]
        | surface["all_contracts_sell_passes_inventory_gate"]
    ].copy()
    actionable["selected_buy_edge_at_ask"] = np.where(
        actionable["product"].isin(["VEV_6000", "VEV_6500", "VEV_4000", "VEV_4500"]),
        actionable["all_contracts_buy_edge_at_ask"],
        actionable["robust_core_buy_edge_at_ask"],
    )
    actionable["selected_sell_edge_at_bid"] = np.where(
        actionable["product"].isin(["VEV_6000", "VEV_6500", "VEV_4000", "VEV_4500"]),
        actionable["all_contracts_sell_edge_at_bid"],
        actionable["robust_core_sell_edge_at_bid"],
    )
    actionable["best_side"] = np.where(
        actionable["selected_buy_edge_at_ask"] >= actionable["selected_sell_edge_at_bid"], "BUY", "SELL"
    )
    actionable["best_edge"] = np.where(
        actionable["best_side"].eq("BUY"),
        actionable["selected_buy_edge_at_ask"],
        actionable["selected_sell_edge_at_bid"],
    )
    actionable[
        keep + ["selected_buy_edge_at_ask", "selected_sell_edge_at_bid", "best_side", "best_edge"]
    ].sort_values(["best_edge", "day", "timestamp"], ascending=[False, True, True]).to_csv(
        DATA_DIR / "round4_option_surface_actionable_edges.csv", index=False
    )

    print(f"Saved option surface outputs to {DATA_DIR}")
    print(summary.round(6).to_string(index=False))


if __name__ == "__main__":
    main()
