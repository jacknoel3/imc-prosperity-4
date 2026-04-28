"""
Aether Crystal manual trading challenge - full pricing and portfolio script.

Run:
    python aether_crystal_strategy.py

Optional examples:
    python aether_crystal_strategy.py --seed 123
    python aether_crystal_strategy.py --n-price-paths 1000000 --n-trials 50000 --seed 123
    python aether_crystal_strategy.py --min-edge 0.05

Requires:
    pip install numpy

Main assumptions from the updated wiki/screenshots:
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

# The underlying has no expiry. For portfolio risk simulation, this script marks it
# at the 3-week horizon. Under zero risk-neutral drift, its fair value is still S0.
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
        # If exactly at strike, either side is ATM at decision; choose call by harmless convention.
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


def _copy_trade(row: dict, volume: int | None = None) -> dict:
    """Convert a pricing row into a trade row."""
    tr = dict(row)
    tr["trade_volume"] = int(row["volume_limit"] if volume is None else volume)
    tr["expected_pnl"] = tr["best_edge"] * CONTRACT_SIZE * tr["trade_volume"] if tr["best_side"] != "SKIP" else 0.0
    return tr


def build_max_edge_portfolio(price_rows: List[dict], min_edge: float) -> List[dict]:
    """Simple portfolio: max volume for every positive-edge trade above min_edge."""
    trades = []
    for row in price_rows:
        if row["best_side"] != "SKIP" and row["best_edge"] > min_edge:
            trades.append(_copy_trade(row))
    return trades


def build_scaled_portfolio(price_rows: List[dict], min_edge: float, scale: float) -> List[dict]:
    """Same trades as max-edge, but volume is scaled down.

    This does not improve expected PnL per unit; it is only a risk-control / tournament
    positioning tool when you dislike the left tail of the full max-EV portfolio.
    """
    trades = []
    for row in price_rows:
        if row["best_side"] != "SKIP" and row["best_edge"] > min_edge:
            volume = int(np.floor(row["volume_limit"] * scale))
            if volume > 0:
                trades.append(_copy_trade(row, volume=volume))
    return trades


def build_strong_edge_portfolio(price_rows: List[dict], min_edge: float, mc_se_multiple: float) -> List[dict]:
    """Keep only trades whose standalone edge is safely above Monte Carlo pricing noise."""
    trades = []
    for row in price_rows:
        required_edge = max(min_edge, mc_se_multiple * row["fair_value_mc_se"])
        if row["best_side"] != "SKIP" and row["best_edge"] > required_edge:
            trades.append(_copy_trade(row))
    return trades


def build_risk_adjusted_portfolio(
    price_rows: List[dict],
    min_edge: float,
    paths_per_trial: int,
    min_standalone_z: float,
) -> List[dict]:
    """Keep trades with enough expected score relative to their standalone game-score noise.

    z here is not a formal p-value; it is a simple risk-adjusted score:
        expected_pnl / std(average PnL over paths_per_trial paths)
    for the trade at max volume.
    """
    trades = []
    for row in price_rows:
        if row["best_side"] == "SKIP" or row["best_edge"] <= min_edge:
            continue
        expected = row["best_edge"] * CONTRACT_SIZE * row["volume_limit"]
        score_std = row["payoff_std_per_path"] * CONTRACT_SIZE * row["volume_limit"] / np.sqrt(paths_per_trial)
        standalone_z = expected / score_std if score_std > 0 else np.inf
        if standalone_z >= min_standalone_z:
            tr = _copy_trade(row)
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


def compare_portfolios(
    portfolios: Dict[str, List[dict]],
    n_trials: int,
    paths_per_trial: int,
    seed: int,
) -> Tuple[List[dict], Dict[str, np.ndarray], Dict[str, List[dict]]]:
    """Evaluate several candidate portfolios under the same mock-game seeds.

    This is the part that helps with the 'game theory' concern: you can compare a
    crowded max-EV book against lower-risk alternatives and see the trade-off between
    mean score, downside percentiles, and probability of losing money.
    """
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
        comparison.append({
            "portfolio": name,
            "n_trades": len(trades),
            "gross_volume": int(sum(t["trade_volume"] for t in trades)),
            "sum_edge_expected_pnl": total_expected_from_edges,
            "mock_mean": metrics["mean"],
            "mock_std": metrics["std"],
            "mock_p01": metrics["p01"],
            "mock_p05": metrics["p05"],
            "mock_p50": metrics["p50"],
            "probability_negative": metrics["probability_negative"],
        })

    comparison.sort(key=lambda r: (r["mock_p05"], r["mock_mean"]), reverse=True)
    return comparison, all_scores, all_summaries


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
                        help="Monte Carlo paths for fair values. Default is deliberately serious for stable pricing.")
    parser.add_argument("--chunk-size", type=int, default=100_000,
                        help="Paths simulated at once. Reduce if memory is low.")
    parser.add_argument("--n-trials", type=int, default=20_000,
                        help="Number of repeated mock games for portfolio risk. Default is deliberately serious for stable tail estimates.")
    parser.add_argument("--paths-per-trial", type=int, default=100,
                        help="Paths in each mock game. Challenge uses 100.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--min-edge", type=float, default=0.0,
                        help="Only include standalone trades with best_edge above this value.")
    parser.add_argument("--half-scale", type=float, default=0.50,
                        help="Volume scale used for the lower-risk scaled portfolio.")
    parser.add_argument("--strong-edge-mc-se-multiple", type=float, default=3.0,
                        help="Strong-edge portfolio requires edge > this multiple of the pricing MC standard error.")
    parser.add_argument("--min-standalone-z", type=float, default=0.15,
                        help="Risk-adjusted portfolio keeps trades with expected score / standalone score std above this value.")
    parser.add_argument("--output-prefix", type=str, default="aether_output")
    args = parser.parse_args()

    print("Aether Crystal challenge settings")
    print(f"S0={S0}, sigma={SIGMA}, dt={DT:.10f}, contract_size={CONTRACT_SIZE}")
    print(f"2-week steps={STEPS_2W}, 3-week steps={STEPS_3W}, max_steps={MAX_STEPS}")
    print(f"Pricing paths={args.n_price_paths:,}, trials={args.n_trials:,}, paths/trial={args.paths_per_trial}")

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

    portfolios = {
        "max_ev_full_size": max_ev_trades,
        f"scaled_{args.half_scale:.2f}x": scaled_trades,
        "strong_edge_only": strong_edge_trades,
        "risk_adjusted": risk_adjusted_trades,
    }

    trade_cols = ["contract", "kind", "best_side", "trade_volume", "bid", "ask", "fair_value", "best_edge", "expected_pnl"]
    print_rows("Max-EV full-size portfolio", max_ev_trades, trade_cols)
    print_rows("Strong-edge-only portfolio", strong_edge_trades, trade_cols)
    print_rows("Risk-adjusted portfolio", risk_adjusted_trades, trade_cols)

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
            "mock_mean", "mock_std", "mock_p01", "mock_p05", "mock_p50", "probability_negative",
        ]
        print_rows("Candidate portfolio comparison", comparison, comparison_cols)
        write_csv(f"{args.output_prefix}_portfolio_comparison.csv", comparison)

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
    print("3. Compare max_ev_full_size with the lower-risk portfolios; a lower mean can be worth it if the left tail is much better.")
    print("4. The script cannot know whether the organizers choose an adversarial seed; it only shows the EV/risk trade-off.")


if __name__ == "__main__":
    main()
