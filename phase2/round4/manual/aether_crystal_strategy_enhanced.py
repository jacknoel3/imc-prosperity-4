"""
Aether Crystal manual trading challenge - enhanced valuation, portfolio risk, and tournament diagnostics.

Run:
    python aether_crystal_strategy.py

Default run is intentionally serious:
    --n-price-paths 500000
    --n-trials 20000
    --paths-per-trial 100

Optional examples:
    python aether_crystal_strategy.py --seed 123
    python aether_crystal_strategy.py --n-price-paths 1000000 --n-trials 50000 --seed 123
    python aether_crystal_strategy.py --output-prefix my_run

Requires:
    pip install numpy

Main assumptions from the updated wiki/screenshots and Discord clarifications:
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


def _trade_from_row(row: dict, volume: int | None = None) -> dict:
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


def estimate_standalone_100path_score_std(row: dict) -> float:
    """Approximate std of the 100-path average PnL for one full-size trade."""
    vol = row["volume_limit"]
    payoff_std = row["payoff_std_per_path"]
    return payoff_std * CONTRACT_SIZE * vol / np.sqrt(100)


def build_risk_adjusted_portfolio(price_rows: List[dict], min_edge: float, min_standalone_sharpe: float) -> List[dict]:
    """Keep trades with decent expected PnL relative to approximate 100-path score volatility."""
    trades = []
    for r in price_rows:
        if r["best_side"] == "SKIP" or r["best_edge"] <= min_edge:
            continue
        expected = r["best_edge"] * CONTRACT_SIZE * r["volume_limit"]
        std100 = estimate_standalone_100path_score_std(r)
        sharpe100 = expected / std100 if std100 > 0 else np.inf
        if sharpe100 >= min_standalone_sharpe:
            tr = _trade_from_row(r)
            tr["standalone_100path_std"] = std100
            tr["standalone_100path_sharpe"] = sharpe100
            trades.append(tr)
    return trades


def portfolio_pnl_per_path(paths: np.ndarray, trades: List[dict]) -> np.ndarray:
    """Portfolio PnL for each simulated path, scaled by contract size and volume."""
    total = np.zeros(paths.shape[0])
    for tr in trades:
        c = CONTRACT_BY_NAME[tr["contract"]]
        p = payoff(c, paths)
        v = float(tr["trade_volume"])
        side = tr["best_side"]
        if side == "BUY":
            per_unit = p - c.ask
        elif side == "SELL":
            per_unit = c.bid - p
        else:
            continue
        total += per_unit * CONTRACT_SIZE * v
    return total


def simulate_mock_game_scores(trades: List[dict], n_trials: int, paths_per_trial: int, seed: int) -> Tuple[np.ndarray, List[dict]]:
    """Repeated mock games. Each score is the average PnL across paths_per_trial paths."""
    rng = np.random.default_rng(seed)
    scores = np.empty(n_trials)
    for i in range(n_trials):
        paths = simulate_paths(paths_per_trial, MAX_STEPS, rng)
        pnl = portfolio_pnl_per_path(paths, trades)
        scores[i] = float(np.mean(pnl))
    return scores, summarize_scores(scores)


def summarize_scores(scores: np.ndarray) -> List[dict]:
    percentiles = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    summary = [
        {"metric": "mean", "value": float(np.mean(scores))},
        {"metric": "std", "value": float(np.std(scores, ddof=1)) if len(scores) > 1 else 0.0},
        {"metric": "min", "value": float(np.min(scores))},
    ]
    for p in percentiles:
        summary.append({"metric": f"p{p:02d}", "value": float(np.percentile(scores, p))})
    summary += [
        {"metric": "max", "value": float(np.max(scores))},
        {"metric": "probability_negative", "value": float(np.mean(scores < 0))},
    ]
    return summary


def summary_value(summary: List[dict], metric: str) -> float:
    return next(r["value"] for r in summary if r["metric"] == metric)


def expected_portfolio_pnl(trades: List[dict]) -> float:
    return float(sum(t.get("expected_pnl", t["best_edge"] * CONTRACT_SIZE * t["trade_volume"]) for t in trades))


def scenario_pnl_by_final_price(trades: List[dict], final_prices: Iterable[float]) -> List[dict]:
    """Very rough terminal-only stress diagnostic.

    This is exact for vanilla/binary/underlying, but only an approximation for chooser/KO because path matters.
    It is still useful as a quick directional exposure sanity check.
    """
    rows = []
    final_prices = list(final_prices)
    for st in final_prices:
        # Flat synthetic path from S0 to st, just to make payoff() callable. For path-dependent options this is illustrative only.
        paths = np.full((1, MAX_STEPS + 1), st, dtype=float)
        paths[:, 0] = S0
        rows.append({"final_price_scenario": st, "rough_portfolio_pnl": float(portfolio_pnl_per_path(paths, trades)[0])})
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
        if isinstance(x, (np.floating,)):
            return f"{float(x):,.6f}"
        if x is None:
            return ""
        return str(x)

    widths = {col: max(len(col), *(len(fmt(r.get(col, ""))) for r in rows)) for col in columns}
    print("  ".join(col.ljust(widths[col]) for col in columns))
    print("  ".join("-" * widths[col] for col in columns))
    for r in rows:
        print("  ".join(fmt(r.get(col, "")).rjust(widths[col]) for col in columns))


def save_portfolio_outputs(prefix: str, name: str, trades: List[dict], scores: np.ndarray, summary: List[dict]) -> None:
    write_csv(f"{prefix}_{name}_trades.csv", trades)
    write_csv(f"{prefix}_{name}_score_summary.csv", summary)
    write_csv(f"{prefix}_{name}_mock_scores.csv", [{"mock_game_score": float(x)} for x in scores])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-price-paths", type=int, default=500_000,
                        help="Monte Carlo paths for fair values. Default is serious, not quick.")
    parser.add_argument("--chunk-size", type=int, default=100_000,
                        help="Paths simulated at once. Reduce if memory is low.")
    parser.add_argument("--n-trials", type=int, default=20_000,
                        help="Number of repeated mock games for portfolio risk.")
    parser.add_argument("--paths-per-trial", type=int, default=100,
                        help="Paths in each mock game. Challenge uses 100.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--min-edge", type=float, default=0.0,
                        help="Only include standalone trades with best_edge above this value.")
    parser.add_argument("--strong-edge-z", type=float, default=3.0,
                        help="Strong-edge portfolio requires edge >= this many MC standard errors.")
    parser.add_argument("--risk-adjusted-min-sharpe", type=float, default=0.25,
                        help="Risk-adjusted portfolio keeps trades above this approximate standalone 100-path Sharpe.")
    parser.add_argument("--output-prefix", type=str, default="aether_output")
    args = parser.parse_args()

    print("Aether Crystal challenge settings")
    print(f"S0={S0}, sigma={SIGMA}, dt={DT:.10f}, contract_size={CONTRACT_SIZE}")
    print(f"2-week steps={STEPS_2W}, 3-week steps={STEPS_3W}, max_steps={MAX_STEPS}")
    print(f"Pricing paths={args.n_price_paths:,}, trials={args.n_trials:,}, paths/trial={args.paths_per_trial}")
    print("Chooser rule: automatic in effect; at 2 weeks it becomes call if S_decision >= K, else put.")

    price_rows = price_contracts(args.n_price_paths, args.seed, args.chunk_size)
    price_cols = [
        "contract", "kind", "bid", "ask", "volume_limit", "fair_value", "fair_value_mc_se",
        "edge_to_mc_se", "buy_edge", "sell_edge", "best_side", "best_edge", "max_expected_pnl",
    ]
    print_rows("Standalone fair values and edges", price_rows, price_cols)
    write_csv(f"{args.output_prefix}_prices.csv", price_rows)

    portfolios: Dict[str, List[dict]] = {
        "max_ev_full_size": build_max_edge_portfolio(price_rows, args.min_edge),
        "scaled_0.50x": build_scaled_portfolio(price_rows, args.min_edge, 0.50),
        "strong_edge_only": build_strong_edge_portfolio(price_rows, args.min_edge, args.strong_edge_z),
        "risk_adjusted": build_risk_adjusted_portfolio(price_rows, args.min_edge, args.risk_adjusted_min_sharpe),
    }

    comparison_rows = []
    score_by_portfolio: Dict[str, np.ndarray] = {}
    for idx, (name, trades) in enumerate(portfolios.items()):
        print_rows(f"Trades: {name}", trades, ["contract", "kind", "best_side", "trade_volume", "fair_value", "best_edge", "expected_pnl"])
        if not trades:
            continue
        scores, summary = simulate_mock_game_scores(trades, args.n_trials, args.paths_per_trial, args.seed + 1000 + idx)
        score_by_portfolio[name] = scores
        save_portfolio_outputs(args.output_prefix, name, trades, scores, summary)
        comparison_rows.append({
            "portfolio": name,
            "num_trades": len(trades),
            "expected_pnl_from_fv": expected_portfolio_pnl(trades),
            "mock_mean": summary_value(summary, "mean"),
            "mock_std": summary_value(summary, "std"),
            "mock_p01": summary_value(summary, "p01"),
            "mock_p05": summary_value(summary, "p05"),
            "mock_p50": summary_value(summary, "p50"),
            "mock_p95": summary_value(summary, "p95"),
            "mock_p99": summary_value(summary, "p99"),
            "mock_max": summary_value(summary, "max"),
            "prob_negative": summary_value(summary, "probability_negative"),
            "mean_minus_0.25_std": summary_value(summary, "mean") - 0.25 * summary_value(summary, "std"),
            "mean_plus_0.25_std": summary_value(summary, "mean") + 0.25 * summary_value(summary, "std"),
        })

    comparison_cols = [
        "portfolio", "num_trades", "expected_pnl_from_fv", "mock_mean", "mock_std",
        "mock_p05", "mock_p50", "mock_p95", "mock_p99", "prob_negative",
        "mean_minus_0.25_std", "mean_plus_0.25_std",
    ]
    print_rows("Candidate portfolio comparison", comparison_rows, comparison_cols)
    write_csv(f"{args.output_prefix}_portfolio_comparison.csv", comparison_rows)

    # Tournament / game-theory diagnostics: compare right-tail and left-tail, not just mean.
    if comparison_rows:
        by_mean = max(comparison_rows, key=lambda r: r["mock_mean"])
        by_downside = max(comparison_rows, key=lambda r: r["mock_p05"])
        by_right_tail = max(comparison_rows, key=lambda r: r["mock_p99"])
        by_mean_minus_std = max(comparison_rows, key=lambda r: r["mean_minus_0.25_std"])
        by_mean_plus_std = max(comparison_rows, key=lambda r: r["mean_plus_0.25_std"])
        diagnostic_rows = [
            {"objective": "highest_mean", "portfolio": by_mean["portfolio"], "value": by_mean["mock_mean"]},
            {"objective": "best_5pct_downside", "portfolio": by_downside["portfolio"], "value": by_downside["mock_p05"]},
            {"objective": "best_99pct_right_tail", "portfolio": by_right_tail["portfolio"], "value": by_right_tail["mock_p99"]},
            {"objective": "risk_controlled_mean_minus_0.25std", "portfolio": by_mean_minus_std["portfolio"], "value": by_mean_minus_std["mean_minus_0.25_std"]},
            {"objective": "tournament_mean_plus_0.25std", "portfolio": by_mean_plus_std["portfolio"], "value": by_mean_plus_std["mean_plus_0.25_std"]},
        ]
        print_rows("Objective diagnostics", diagnostic_rows, ["objective", "portfolio", "value"])
        write_csv(f"{args.output_prefix}_objective_diagnostics.csv", diagnostic_rows)

    print("\nInterpretation notes")
    print("- If organizers make realized values very close to EV, focus on highest_mean / expected_pnl_from_fv.")
    print("- If they intentionally induce leaderboard variance, also inspect p95/p99 and mean_plus_0.25_std.")
    print("- If you want a robust rank and hate blow-ups, inspect p05 and mean_minus_0.25_std.")
    print("- The chooser is not a manual mid-game decision in this script; it is automatic by the clarified rule.")
    print("\nSaved CSV outputs with prefix:", args.output_prefix)
    print("\nBefore submitting:")
    print("1. Verify all bid/ask/volume values against the live website.")
    print("2. Rerun with more pricing paths if fair values are close to bid/ask.")
    print("3. Compare highest-mean vs right-tail vs downside portfolios depending on competition objective.")


if __name__ == "__main__":
    main()
