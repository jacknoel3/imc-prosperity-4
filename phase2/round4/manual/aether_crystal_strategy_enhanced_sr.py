"""
Aether Crystal manual trading challenge - valuation, risk, tournament diagnostics,
and a max-Sharpe portfolio search.

Run:
    python aether_crystal_strategy.py

Default run is intentionally serious:
    --n-price-paths 500000
    --n-trials 20000
    --paths-per-trial 100
    --n-optimizer-paths 300000
    --sharpe-random-portfolios 200000

Optional heavier example:
    python aether_crystal_strategy.py --n-price-paths 2000000 --n-trials 100000 --n-optimizer-paths 1000000 --sharpe-random-portfolios 500000 --seed 123

Requires:
    pip install numpy

Assumptions from the updated wiki/screenshots and Discord clarifications:
- S0 = 50
- annualized volatility sigma = 251% = 2.51
- zero risk-neutral drift
- 252 trading days/year
- 4 steps/trading day
- 2 weeks = 10 trading days = 40 steps
- 3 weeks = 15 trading days = 60 steps
- contract size = 3000 for every product, including the underlying
- knock-out barrier monitored only on the discrete simulation grid
- ignore the cosmetic PRICE column
- chooser conversion is automatic in effect: at 2 weeks it becomes the side that is ITM then
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from typing import Dict, Iterable, List, Literal, Optional, Tuple

import numpy as np


# =========================
# Challenge constants
# =========================
S0 = 50.0
SIGMA = 2.51
TRADING_DAYS_PER_YEAR = 252
STEPS_PER_DAY = 4
STEPS_PER_YEAR = TRADING_DAYS_PER_YEAR * STEPS_PER_DAY
DT = 1.0 / STEPS_PER_YEAR
CONTRACT_SIZE = 3000

STEPS_2W = 2 * 5 * STEPS_PER_DAY  # 40
STEPS_3W = 3 * 5 * STEPS_PER_DAY  # 60
MAX_STEPS = STEPS_3W
UNDERLYING_MARK_STEP = STEPS_3W

Side = Literal["BUY", "SELL", "SKIP"]


@dataclass(frozen=True)
class Contract:
    name: str
    kind: str
    bid: float
    ask: float
    volume: int
    strike: Optional[float] = None
    expiry_step: Optional[int] = None
    payout: Optional[float] = None
    barrier: Optional[float] = None
    decision_step: Optional[int] = None


# Values transcribed from your screenshots. Check these against the live table before submitting.
CONTRACTS: List[Contract] = [
    Contract("AC", "underlying", bid=49.975, ask=50.025, volume=200, expiry_step=UNDERLYING_MARK_STEP),

    # 3-week vanilla options
    Contract("AC_50_P", "put", bid=12.00, ask=12.05, volume=50, strike=50, expiry_step=STEPS_3W),
    Contract("AC_50_C", "call", bid=12.00, ask=12.05, volume=50, strike=50, expiry_step=STEPS_3W),
    Contract("AC_35_P", "put", bid=4.33, ask=4.35, volume=50, strike=35, expiry_step=STEPS_3W),
    Contract("AC_40_P", "put", bid=6.50, ask=6.55, volume=50, strike=40, expiry_step=STEPS_3W),
    Contract("AC_45_P", "put", bid=9.05, ask=9.10, volume=50, strike=45, expiry_step=STEPS_3W),
    Contract("AC_60_C", "call", bid=8.80, ask=8.85, volume=50, strike=60, expiry_step=STEPS_3W),

    # 2-week vanilla options
    Contract("AC_50_P_2", "put", bid=9.70, ask=9.75, volume=50, strike=50, expiry_step=STEPS_2W),
    Contract("AC_50_C_2", "call", bid=9.70, ask=9.75, volume=50, strike=50, expiry_step=STEPS_2W),

    # Exotics
    Contract("AC_50_CO", "chooser", bid=22.20, ask=22.30, volume=50, strike=50,
             decision_step=STEPS_2W, expiry_step=STEPS_3W),
    Contract("AC_40_BP", "binary_put", bid=5.00, ask=5.10, volume=50, strike=40,
             payout=10, expiry_step=STEPS_3W),
    Contract("AC_45_KO", "knockout_put", bid=0.150, ask=0.175, volume=500, strike=45,
             barrier=35, expiry_step=STEPS_3W),
]

CONTRACT_BY_NAME: Dict[str, Contract] = {c.name: c for c in CONTRACTS}


def simulate_paths(n_paths: int, n_steps: int, rng: np.random.Generator) -> np.ndarray:
    """Simulate GBM paths under zero risk-neutral drift.

    Returns shape (n_paths, n_steps + 1), including S0 in column 0.
    """
    z = rng.standard_normal(size=(n_paths, n_steps))
    log_returns = (-0.5 * SIGMA ** 2 * DT) + (SIGMA * np.sqrt(DT) * z)
    log_paths = np.cumsum(log_returns, axis=1)
    paths = np.empty((n_paths, n_steps + 1), dtype=float)
    paths[:, 0] = S0
    paths[:, 1:] = S0 * np.exp(log_paths)
    return paths


def payoff(contract: Contract, paths: np.ndarray) -> np.ndarray:
    """Path-by-path payoff for a contract."""
    if contract.expiry_step is None:
        raise ValueError(f"{contract.name}: missing expiry_step")

    s_t = paths[:, contract.expiry_step]

    if contract.kind == "underlying":
        return s_t

    if contract.strike is None:
        raise ValueError(f"{contract.name}: missing strike")
    k = contract.strike

    if contract.kind == "call":
        return np.maximum(s_t - k, 0.0)

    if contract.kind == "put":
        return np.maximum(k - s_t, 0.0)

    if contract.kind == "binary_put":
        if contract.payout is None:
            raise ValueError(f"{contract.name}: missing payout")
        return contract.payout * (s_t < k)

    if contract.kind == "knockout_put":
        if contract.barrier is None:
            raise ValueError(f"{contract.name}: missing barrier")
        path_until_expiry = paths[:, : contract.expiry_step + 1]
        survived = np.min(path_until_expiry, axis=1) >= contract.barrier
        return np.maximum(k - s_t, 0.0) * survived

    if contract.kind == "chooser":
        if contract.decision_step is None:
            raise ValueError(f"{contract.name}: missing decision_step")
        s_decision = paths[:, contract.decision_step]
        call = np.maximum(s_t - k, 0.0)
        put = np.maximum(k - s_t, 0.0)
        # Official clarification: automatic in effect, converts to whichever side is ITM at 2 weeks.
        # At exactly the strike, neither side is ITM; equality is probability-zero under GBM.
        return np.where(s_decision >= k, call, put)

    raise ValueError(f"Unknown contract kind: {contract.kind}")


def price_contracts(n_price_paths: int, seed: int, chunk_size: int) -> List[dict]:
    """Compute Monte Carlo fair values and standalone edges."""
    rng = np.random.default_rng(seed)
    sums = {c.name: 0.0 for c in CONTRACTS}
    sums_sq = {c.name: 0.0 for c in CONTRACTS}

    done = 0
    while done < n_price_paths:
        n = min(chunk_size, n_price_paths - done)
        paths = simulate_paths(n, MAX_STEPS, rng)
        for c in CONTRACTS:
            p = payoff(c, paths)
            sums[c.name] += float(np.sum(p))
            sums_sq[c.name] += float(np.sum(p * p))
        done += n

    rows: List[dict] = []
    for c in CONTRACTS:
        fair_value = sums[c.name] / n_price_paths
        second_moment = sums_sq[c.name] / n_price_paths
        var = max(second_moment - fair_value * fair_value, 0.0)
        payoff_std = float(np.sqrt(var))
        mc_se = payoff_std / np.sqrt(n_price_paths)

        buy_edge = fair_value - c.ask
        sell_edge = c.bid - fair_value

        if buy_edge > 0 and buy_edge >= sell_edge:
            best_side: Side = "BUY"
            best_edge = buy_edge
            trade_price = c.ask
        elif sell_edge > 0:
            best_side = "SELL"
            best_edge = sell_edge
            trade_price = c.bid
        else:
            best_side = "SKIP"
            best_edge = max(buy_edge, sell_edge)
            trade_price = None

        rows.append({
            "contract": c.name,
            "kind": c.kind,
            "bid": c.bid,
            "ask": c.ask,
            "volume_limit": c.volume,
            "fair_value": fair_value,
            "fair_value_mc_se": mc_se,
            "edge_to_mc_se": (best_edge / mc_se) if mc_se > 0 else np.inf,
            "payoff_std_per_path": payoff_std,
            "buy_edge": buy_edge,
            "sell_edge": sell_edge,
            "best_side": best_side,
            "best_edge": best_edge,
            "trade_price": trade_price,
            "max_expected_pnl": best_edge * CONTRACT_SIZE * c.volume if best_side != "SKIP" else 0.0,
        })

    rows.sort(key=lambda r: r["max_expected_pnl"], reverse=True)
    return rows


def _trade_from_row(row: dict, volume: Optional[int] = None) -> dict:
    tr = dict(row)
    tr["trade_volume"] = int(row["volume_limit"] if volume is None else volume)
    tr["expected_pnl"] = tr["best_edge"] * CONTRACT_SIZE * tr["trade_volume"] if tr["best_side"] != "SKIP" else 0.0
    return tr


def build_max_edge_portfolio(price_rows: List[dict], min_edge: float = 0.0) -> List[dict]:
    return [_trade_from_row(r) for r in price_rows if r["best_side"] != "SKIP" and r["best_edge"] > min_edge]


def build_scaled_portfolio(price_rows: List[dict], min_edge: float = 0.0, scale: float = 0.5) -> List[dict]:
    trades = []
    for r in price_rows:
        if r["best_side"] == "SKIP" or r["best_edge"] <= min_edge:
            continue
        v = int(np.floor(r["volume_limit"] * scale))
        if v > 0:
            trades.append(_trade_from_row(r, v))
    return trades


def build_strong_edge_portfolio(price_rows: List[dict], min_edge: float, strong_edge_z: float) -> List[dict]:
    """Keep only trades whose edge is clearly above MC noise in the pricing run."""
    trades = []
    for r in price_rows:
        if r["best_side"] == "SKIP" or r["best_edge"] <= min_edge:
            continue
        if r["fair_value_mc_se"] <= 0 or r["best_edge"] >= strong_edge_z * r["fair_value_mc_se"]:
            trades.append(_trade_from_row(r))
    return trades


def build_risk_adjusted_portfolio(
    price_rows: List[dict],
    min_edge: float,
    paths_per_trial: int,
    min_standalone_z: float,
) -> List[dict]:
    """Keep trades with enough expected score relative to their standalone game-score noise."""
    trades = []
    for r in price_rows:
        if r["best_side"] == "SKIP" or r["best_edge"] <= min_edge:
            continue
        expected = r["best_edge"] * CONTRACT_SIZE * r["volume_limit"]
        score_std = r["payoff_std_per_path"] * CONTRACT_SIZE * r["volume_limit"] / np.sqrt(paths_per_trial)
        standalone_z = expected / score_std if score_std > 0 else np.inf
        if standalone_z >= min_standalone_z:
            tr = _trade_from_row(r)
            tr["standalone_score_z"] = float(standalone_z)
            trades.append(tr)
    return trades


def portfolio_pnl_per_path(paths: np.ndarray, trades: List[dict]) -> np.ndarray:
    """Portfolio PnL for each simulated path, scaled by contract size and volume."""
    total = np.zeros(paths.shape[0])
    for tr in trades:
        c = CONTRACT_BY_NAME[tr["contract"]]
        p = payoff(c, paths)
        v = float(tr["trade_volume"])
        side = tr.get("best_side", tr.get("side"))
        if side == "BUY":
            per_unit = p - c.ask
        elif side == "SELL":
            per_unit = c.bid - p
        else:
            continue
        total += per_unit * CONTRACT_SIZE * v
    return total


def summarize_scores(scores: np.ndarray) -> List[dict]:
    percentiles = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    mean = float(np.mean(scores))
    std = float(np.std(scores, ddof=1)) if len(scores) > 1 else 0.0
    summary = [
        {"metric": "mean", "value": mean},
        {"metric": "std", "value": std},
        {"metric": "sharpe_mean_over_std", "value": mean / std if std > 0 else np.inf},
        {"metric": "min", "value": float(np.min(scores))},
    ]
    for p in percentiles:
        summary.append({"metric": f"p{p:02d}", "value": float(np.percentile(scores, p))})
    summary += [
        {"metric": "max", "value": float(np.max(scores))},
        {"metric": "probability_negative", "value": float(np.mean(scores < 0))},
    ]
    return summary


def simulate_mock_game_scores(
    trades: List[dict],
    n_trials: int,
    paths_per_trial: int,
    seed: int,
) -> Tuple[np.ndarray, List[dict]]:
    """Repeated mock games. Each score is the average PnL across paths_per_trial paths."""
    rng = np.random.default_rng(seed)
    scores = np.empty(n_trials)
    for i in range(n_trials):
        paths = simulate_paths(paths_per_trial, MAX_STEPS, rng)
        pnl = portfolio_pnl_per_path(paths, trades)
        scores[i] = float(np.mean(pnl))
    return scores, summarize_scores(scores)


def _candidate_unit_pnl_matrix(
    candidate_rows: List[dict],
    n_paths: int,
    seed: int,
    chunk_size: int,
) -> np.ndarray:
    """Build matrix of one-contract PnL per path for candidate trades.

    Shape: (n_paths, n_candidates). One unit means trade_volume = 1,
    already scaled by CONTRACT_SIZE.
    """
    rng = np.random.default_rng(seed)
    matrix = np.empty((n_paths, len(candidate_rows)), dtype=float)
    done = 0
    while done < n_paths:
        n = min(chunk_size, n_paths - done)
        paths = simulate_paths(n, MAX_STEPS, rng)
        for j, r in enumerate(candidate_rows):
            c = CONTRACT_BY_NAME[r["contract"]]
            p = payoff(c, paths)
            if r["best_side"] == "BUY":
                per_unit = p - c.ask
            elif r["best_side"] == "SELL":
                per_unit = c.bid - p
            else:
                per_unit = np.zeros(n)
            matrix[done:done + n, j] = per_unit * CONTRACT_SIZE
        done += n
    return matrix


def _volumes_to_trades(candidate_rows: List[dict], volumes: np.ndarray) -> List[dict]:
    trades: List[dict] = []
    for r, v in zip(candidate_rows, volumes):
        iv = int(round(float(v)))
        iv = max(0, min(iv, int(r["volume_limit"])))
        if iv > 0:
            trades.append(_trade_from_row(r, iv))
    return trades


def _scale_to_bounds(weights: np.ndarray, max_volumes: np.ndarray) -> np.ndarray:
    """Scale a non-negative vector until at least one position hits its max volume."""
    w = np.maximum(np.asarray(weights, dtype=float), 0.0)
    if np.all(w <= 0):
        return np.zeros_like(w)
    positive = w > 0
    scale = np.min(max_volumes[positive] / w[positive])
    volumes = w * scale
    return np.minimum(volumes, max_volumes)


def _portfolio_stats_from_matrix(pnl_matrix: np.ndarray, volumes: np.ndarray) -> Tuple[float, float, float]:
    pnl = pnl_matrix @ volumes
    mu = float(np.mean(pnl))
    std = float(np.std(pnl, ddof=1))
    sharpe = mu / std if std > 0 else -np.inf
    return mu, std, sharpe


def build_sharpe_optimized_portfolio(
    price_rows: List[dict],
    min_edge: float,
    n_optimizer_paths: int,
    seed: int,
    chunk_size: int,
    n_random_portfolios: int,
    paths_per_trial: int,
) -> Tuple[List[dict], List[dict]]:
    """Search for a high-Sharpe portfolio among positive-edge trades.

    Important: Sharpe is scale-invariant, so the script scales the winning ratio until
    one included contract reaches its volume cap. This preserves Sharpe while making
    expected PnL meaningful.

    The search uses:
    1. the existing heuristic portfolios as starting points,
    2. a continuous unconstrained max-Sharpe direction using covariance shrinkage,
    3. random bounded portfolios,
    4. a small coordinate/grid clean-up around the best result.
    """
    candidate_rows = [r for r in price_rows if r["best_side"] != "SKIP" and r["best_edge"] > min_edge]
    if not candidate_rows:
        return [], []

    max_vol = np.array([float(r["volume_limit"]) for r in candidate_rows])
    pnl_matrix = _candidate_unit_pnl_matrix(candidate_rows, n_optimizer_paths, seed, chunk_size)
    mu_vec = pnl_matrix.mean(axis=0)
    cov = np.cov(pnl_matrix, rowvar=False)
    if cov.ndim == 0:
        cov = np.array([[float(cov)]])

    starts: List[Tuple[str, np.ndarray]] = []
    starts.append(("full_positive_edge", max_vol.copy()))

    # Strong-edge start.
    strong = np.zeros_like(max_vol)
    for i, r in enumerate(candidate_rows):
        if r["fair_value_mc_se"] <= 0 or r["best_edge"] >= 3.0 * r["fair_value_mc_se"]:
            strong[i] = max_vol[i]
    starts.append(("strong_edge", strong))

    # Risk-adjusted start.
    rz = np.zeros_like(max_vol)
    for i, r in enumerate(candidate_rows):
        expected = r["best_edge"] * CONTRACT_SIZE * r["volume_limit"]
        score_std = r["payoff_std_per_path"] * CONTRACT_SIZE * r["volume_limit"] / np.sqrt(paths_per_trial)
        standalone_z = expected / score_std if score_std > 0 else np.inf
        if standalone_z >= 0.15:
            rz[i] = max_vol[i]
    starts.append(("risk_adjusted", rz))

    # Continuous unconstrained direction with diagonal shrinkage to avoid unstable inverses.
    diag = np.diag(np.diag(cov))
    shrink = 0.10
    cov_shrunk = (1.0 - shrink) * cov + shrink * diag
    cov_shrunk += np.eye(cov_shrunk.shape[0]) * 1e-9
    try:
        direction = np.linalg.solve(cov_shrunk, mu_vec)
        direction = np.maximum(direction, 0.0)
        starts.append(("cov_inverse_direction", _scale_to_bounds(direction, max_vol)))
    except np.linalg.LinAlgError:
        pass

    rng = np.random.default_rng(seed + 777)
    batch_size = 20_000
    best_name = "none"
    best_vol = np.zeros_like(max_vol)
    best_mu = -np.inf
    best_std = np.inf
    best_sharpe = -np.inf

    def consider(name: str, volumes: np.ndarray) -> None:
        nonlocal best_name, best_vol, best_mu, best_std, best_sharpe
        volumes = np.asarray(volumes, dtype=float)
        volumes = np.minimum(np.maximum(volumes, 0.0), max_vol)
        volumes = np.rint(volumes)
        if np.all(volumes <= 0):
            return
        mu, std, sharpe = _portfolio_stats_from_matrix(pnl_matrix, volumes)
        # Tie-break in favor of higher expected PnL if Sharpe is almost the same.
        if (sharpe > best_sharpe + 1e-9) or (abs(sharpe - best_sharpe) <= 1e-9 and mu > best_mu):
            best_name, best_vol, best_mu, best_std, best_sharpe = name, volumes.copy(), mu, std, sharpe

    for name, volumes in starts:
        consider(name, volumes)

    # Random bounded portfolios. We sample ratios, scale to volume caps, then round.
    # The exponent creates a mix of sparse and dense books.
    remaining = n_random_portfolios
    while remaining > 0:
        b = min(batch_size, remaining)
        active = rng.random((b, len(candidate_rows))) < rng.uniform(0.25, 1.0, size=(b, 1))
        raw = rng.random((b, len(candidate_rows))) ** rng.uniform(0.25, 3.0, size=(b, 1))
        raw *= active
        for i in range(b):
            if not np.any(raw[i] > 0):
                continue
            vols = _scale_to_bounds(raw[i], max_vol)
            # Randomly reduce some portfolios too, in case integer bounds create better ratios.
            vols *= rng.uniform(0.35, 1.0)
            consider("random_search", vols)
        remaining -= b

    # Coordinate/grid clean-up around the best result.
    grid_fracs = np.array([0.0, 0.10, 0.20, 0.35, 0.50, 0.65, 0.80, 1.0])
    improved = True
    passes = 0
    while improved and passes < 4:
        improved = False
        passes += 1
        for j in range(len(candidate_rows)):
            current = best_vol.copy()
            local_best = best_vol.copy()
            local_sharpe = best_sharpe
            local_mu = best_mu
            for frac in grid_fracs:
                test = current.copy()
                test[j] = round(max_vol[j] * frac)
                mu, std, sharpe = _portfolio_stats_from_matrix(pnl_matrix, test)
                if (sharpe > local_sharpe + 1e-9) or (abs(sharpe - local_sharpe) <= 1e-9 and mu > local_mu):
                    local_best, local_sharpe, local_mu = test.copy(), sharpe, mu
            if not np.array_equal(local_best, best_vol):
                best_vol = local_best.copy()
                best_mu, best_std, best_sharpe = _portfolio_stats_from_matrix(pnl_matrix, best_vol)
                best_name = "coordinate_refined"
                improved = True

    trades = _volumes_to_trades(candidate_rows, best_vol)
    diagnostics = []
    for r, v, unit_mu in zip(candidate_rows, best_vol, mu_vec):
        diagnostics.append({
            "contract": r["contract"],
            "kind": r["kind"],
            "side": r["best_side"],
            "volume_limit": r["volume_limit"],
            "optimized_volume": int(round(float(v))),
            "best_edge": r["best_edge"],
            "expected_pnl_from_edge": r["best_edge"] * CONTRACT_SIZE * int(round(float(v))),
            "optimizer_unit_mean_pnl": float(unit_mu),
        })

    diagnostics.append({
        "contract": "__optimizer_summary__",
        "kind": best_name,
        "side": "",
        "volume_limit": "",
        "optimized_volume": int(np.sum(best_vol)),
        "best_edge": "",
        "expected_pnl_from_edge": sum(t["expected_pnl"] for t in trades),
        "optimizer_unit_mean_pnl": f"optimizer_path_mean={best_mu}; optimizer_path_std={best_std}; optimizer_path_sharpe={best_sharpe}",
    })
    return trades, diagnostics


def compare_portfolios(
    portfolios: Dict[str, List[dict]],
    n_trials: int,
    paths_per_trial: int,
    seed: int,
) -> Tuple[List[dict], Dict[str, np.ndarray], Dict[str, List[dict]]]:
    """Evaluate candidate portfolios under repeated 100-path mock games."""
    all_scores: Dict[str, np.ndarray] = {}
    all_summaries: Dict[str, List[dict]] = {}
    comparison: List[dict] = []

    for i, (name, trades) in enumerate(portfolios.items()):
        if not trades:
            continue
        scores, summary = simulate_mock_game_scores(trades, n_trials, paths_per_trial, seed + 10_000 * i)
        all_scores[name] = scores
        all_summaries[name] = summary
        metrics = {row["metric"]: row["value"] for row in summary}
        total_expected_from_edges = sum(float(t.get("expected_pnl", t["best_edge"] * CONTRACT_SIZE * t["trade_volume"])) for t in trades)
        std = metrics["std"]
        comparison.append({
            "portfolio": name,
            "n_trades": len(trades),
            "gross_volume": int(sum(t["trade_volume"] for t in trades)),
            "sum_edge_expected_pnl": total_expected_from_edges,
            "mock_mean": metrics["mean"],
            "mock_std": std,
            "mock_sharpe": metrics["mean"] / std if std > 0 else np.inf,
            "mock_p01": metrics["p01"],
            "mock_p05": metrics["p05"],
            "mock_p50": metrics["p50"],
            "mock_p95": metrics["p95"],
            "mock_p99": metrics["p99"],
            "mock_max": metrics["max"],
            "probability_negative": metrics["probability_negative"],
            "mean_minus_0.25_std": metrics["mean"] - 0.25 * std,
            "mean_plus_0.25_std": metrics["mean"] + 0.25 * std,
        })

    comparison.sort(key=lambda r: r["mock_sharpe"], reverse=True)
    return comparison, all_scores, all_summaries


def objective_diagnostics(comparison: List[dict]) -> List[dict]:
    if not comparison:
        return []
    objectives = [
        ("highest_mock_mean", "mock_mean", max),
        ("highest_fv_edge_expected_pnl", "sum_edge_expected_pnl", max),
        ("highest_mock_sharpe", "mock_sharpe", max),
        ("best_5pct_downside", "mock_p05", max),
        ("best_1pct_downside", "mock_p01", max),
        ("best_99pct_right_tail", "mock_p99", max),
        ("best_tournament_gamble_score", "mean_plus_0.25_std", max),
        ("best_risk_controlled_score", "mean_minus_0.25_std", max),
        ("lowest_probability_negative", "probability_negative", min),
    ]
    rows = []
    for objective, metric, fn in objectives:
        best = fn(comparison, key=lambda r: r[metric])
        rows.append({
            "objective": objective,
            "metric_used": metric,
            "best_portfolio": best["portfolio"],
            "metric_value": best[metric],
            "mock_mean": best["mock_mean"],
            "mock_std": best["mock_std"],
            "mock_sharpe": best["mock_sharpe"],
            "mock_p05": best["mock_p05"],
            "mock_p99": best["mock_p99"],
            "probability_negative": best["probability_negative"],
        })
    return rows


def write_csv(filename: str, rows: Iterable[dict]) -> None:
    rows = list(rows)
    if not rows:
        return
    with open(filename, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def print_rows(title: str, rows: List[dict], columns: List[str]) -> None:
    print("\n" + title)
    print("=" * len(title))
    if not rows:
        print("<empty>")
        return

    def fmt(x):
        if isinstance(x, float):
            return f"{x:,.6f}"
        if x is None:
            return ""
        return str(x)

    widths = {col: max(len(col), *(len(fmt(r.get(col, ""))) for r in rows)) for col in columns}
    print("  ".join(col.ljust(widths[col]) for col in columns))
    print("  ".join("-" * widths[col] for col in columns))
    for r in rows:
        print("  ".join(fmt(r.get(col, "")).rjust(widths[col]) for col in columns))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-price-paths", type=int, default=500_000,
                        help="Monte Carlo paths for fair values.")
    parser.add_argument("--chunk-size", type=int, default=100_000,
                        help="Paths simulated at once. Reduce if memory is low.")
    parser.add_argument("--n-trials", type=int, default=20_000,
                        help="Number of repeated mock games for portfolio risk.")
    parser.add_argument("--paths-per-trial", type=int, default=100,
                        help="Paths in each mock game. Challenge uses 100.")
    parser.add_argument("--n-optimizer-paths", type=int, default=300_000,
                        help="Paths used to search for the max-Sharpe portfolio.")
    parser.add_argument("--sharpe-random-portfolios", type=int, default=200_000,
                        help="Random bounded portfolios sampled in the max-Sharpe search.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--min-edge", type=float, default=0.0,
                        help="Only include standalone trades with best_edge above this value.")
    parser.add_argument("--half-scale", type=float, default=0.50,
                        help="Volume scale used for the lower-risk scaled portfolio.")
    parser.add_argument("--strong-edge-mc-se-multiple", type=float, default=3.0,
                        help="Strong-edge portfolio requires edge > this multiple of the pricing MC standard error.")
    parser.add_argument("--min-standalone-z", type=float, default=0.15,
                        help="Risk-adjusted portfolio keeps trades with expected score / standalone score std above this value.")
    parser.add_argument("--skip-sharpe-optimizer", action="store_true",
                        help="Skip the explicit max-Sharpe search.")
    parser.add_argument("--output-prefix", type=str, default="aether_output")
    args = parser.parse_args()

    print("Aether Crystal challenge settings")
    print(f"S0={S0}, sigma={SIGMA}, dt={DT:.10f}, contract_size={CONTRACT_SIZE}")
    print(f"2-week steps={STEPS_2W}, 3-week steps={STEPS_3W}, max_steps={MAX_STEPS}")
    print(f"Pricing paths={args.n_price_paths:,}, trials={args.n_trials:,}, paths/trial={args.paths_per_trial}")
    if not args.skip_sharpe_optimizer:
        print(f"Sharpe optimizer paths={args.n_optimizer_paths:,}, random portfolios={args.sharpe_random_portfolios:,}")

    price_rows = price_contracts(args.n_price_paths, args.seed, args.chunk_size)
    price_cols = [
        "contract", "kind", "bid", "ask", "volume_limit", "fair_value", "fair_value_mc_se",
        "buy_edge", "sell_edge", "best_side", "best_edge", "max_expected_pnl",
    ]
    print_rows("Standalone fair values and edges", price_rows, price_cols)

    max_ev_trades = build_max_edge_portfolio(price_rows, args.min_edge)
    scaled_trades = build_scaled_portfolio(price_rows, args.min_edge, args.half_scale)
    strong_edge_trades = build_strong_edge_portfolio(
        price_rows, args.min_edge, args.strong_edge_mc_se_multiple
    )
    risk_adjusted_trades = build_risk_adjusted_portfolio(
        price_rows, args.min_edge, args.paths_per_trial, args.min_standalone_z
    )

    portfolios: Dict[str, List[dict]] = {
        "max_ev_full_size": max_ev_trades,
        f"scaled_{args.half_scale:.2f}x": scaled_trades,
        "strong_edge_only": strong_edge_trades,
        "risk_adjusted": risk_adjusted_trades,
    }

    sharpe_diagnostics: List[dict] = []
    if not args.skip_sharpe_optimizer:
        sharpe_trades, sharpe_diagnostics = build_sharpe_optimized_portfolio(
            price_rows=price_rows,
            min_edge=args.min_edge,
            n_optimizer_paths=args.n_optimizer_paths,
            seed=args.seed + 555,
            chunk_size=args.chunk_size,
            n_random_portfolios=args.sharpe_random_portfolios,
            paths_per_trial=args.paths_per_trial,
        )
        portfolios["sharpe_optimized"] = sharpe_trades

    trade_cols = ["contract", "kind", "best_side", "trade_volume", "bid", "ask", "fair_value", "best_edge", "expected_pnl"]
    print_rows("Max-EV full-size portfolio", max_ev_trades, trade_cols)
    print_rows("Strong-edge-only portfolio", strong_edge_trades, trade_cols)
    print_rows("Risk-adjusted portfolio", risk_adjusted_trades, trade_cols)
    if not args.skip_sharpe_optimizer:
        print_rows("Sharpe-optimized portfolio", portfolios["sharpe_optimized"], trade_cols)
        write_csv(f"{args.output_prefix}_sharpe_optimizer_diagnostics.csv", sharpe_diagnostics)

    write_csv(f"{args.output_prefix}_prices.csv", price_rows)
    for name, trades in portfolios.items():
        write_csv(f"{args.output_prefix}_{name}_trades.csv", trades)

    nonempty_portfolios = {name: trades for name, trades in portfolios.items() if trades}
    if nonempty_portfolios:
        comparison, all_scores, all_summaries = compare_portfolios(
            nonempty_portfolios, args.n_trials, args.paths_per_trial, args.seed + 1
        )
        comparison_cols = [
            "portfolio", "n_trades", "gross_volume", "sum_edge_expected_pnl",
            "mock_mean", "mock_std", "mock_sharpe", "mock_p01", "mock_p05", "mock_p50",
            "mock_p95", "mock_p99", "probability_negative",
        ]
        print_rows("Candidate portfolio comparison", comparison, comparison_cols)
        write_csv(f"{args.output_prefix}_portfolio_comparison.csv", comparison)

        objectives = objective_diagnostics(comparison)
        objective_cols = [
            "objective", "metric_used", "best_portfolio", "metric_value", "mock_mean",
            "mock_std", "mock_sharpe", "mock_p05", "mock_p99", "probability_negative",
        ]
        print_rows("Objective diagnostics", objectives, objective_cols)
        write_csv(f"{args.output_prefix}_objective_diagnostics.csv", objectives)

        for name, summary in all_summaries.items():
            print_rows(f"Mock 100-path score distribution: {name}", summary, ["metric", "value"])
            write_csv(f"{args.output_prefix}_{name}_score_summary.csv", summary)
            write_csv(f"{args.output_prefix}_{name}_mock_scores.csv", [{"mock_game_score": float(x)} for x in all_scores[name]])
    else:
        print("\nNo positive-edge trades selected.")

    print("\nSaved CSV outputs with prefix:", args.output_prefix)
    print("\nBefore submitting:")
    print("1. Verify all bid/ask/volume values against the live website.")
    print("2. Rerun with more pricing paths if fair values are close to bid/ask.")
    print("3. The Sharpe optimizer is a search heuristic, not a mathematical proof of the global optimum.")
    print("4. Sharpe is scale-invariant, so the optimizer scales the chosen ratio up to use meaningful volume.")
    print("5. The script cannot know whether the organizers choose an adversarial seed; it only shows the EV/risk trade-off.")


if __name__ == "__main__":
    main()
