"""
r3prediction.py

Run from:
    imc-prosperity-4/phase2/round3/manual

Outputs CSVs, PNGs, and a recommendations TXT file into the same folder.

Model:
- Gardeners' reserves are uniformly distributed on {670, 675, ..., 920}.
- You can bid any integer.
- Trade happens only if bid > reserve.
- Bid 1 takes precedence.
- Bid 2 is unpenalised only if b2 > competitor mean bid 2.
- If b2 <= competitor mean, the cubic penalty is applied.
- Competitor mean is generated from an iterative behavioural model.

Core judgement:
- Nash/mechanical anchor is 836.
- In a logical qualified field, very few teams should use the pure no-penalty
  optimum or very noisy/manual heuristics.
- Many teams will realise that missing the mean is costly, so caution pushes
  bid 2 upward.
- Bid 1 does not affect the mean penalty; it should be optimised mechanically
  conditional on bid 2.
- Logical teams also realise that overbidding destroys margin, so behavioural
  archetypes retain varying degrees of anchoring to the no-penalty solution.
- Exact round numbers are downweighted because +1 above a multiple of 5 unlocks the next reserve bucket.
"""

from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# ============================================================
# 1. Core game settings
# ============================================================

SALE_PRICE = 920

# Gardeners' reserve prices: 670, 675, ..., 920 inclusive
RESERVES = np.arange(670, 921, 5)
N_RESERVE_LEVELS = len(RESERVES)

# Mechanical no-penalty optimum.
MECHANICAL_B1_ANCHOR = 751
MECHANICAL_B2_ANCHOR = 836

# Number of qualified competing players whose bid 2 forms the mean
N_COMPETITORS = 4000

# Integer bid range for your own bids
BID_MIN = 671
BID_MAX = 920

# Competitor bid modelling range
COMPETITOR_BID_MIN = 836
COMPETITOR_BID_MAX = 895
COMPETITOR_BID_GRID = np.arange(COMPETITOR_BID_MIN, COMPETITOR_BID_MAX + 1)

# Simulation settings
N_SIMS = 20_000
SEED = 42

# Output folder: same folder as this script
try:
    OUT_DIR = Path(__file__).resolve().parent
except NameError:
    OUT_DIR = Path.cwd()


# ============================================================
# 2. Mechanics functions
# ============================================================

def count_reserves_beaten(bid: int | float) -> int:
    """
    Counts how many reserve levels are strictly below the bid.
    Rule: trade if bid > reserve.
    """
    return int(np.sum(bid > RESERVES))


def penalty_factor(b2: int | float, mean_b2: float) -> float:
    """
    Bid 2 is unpenalised only if b2 > mean_b2.

    If b2 <= mean_b2, apply the cubic penalty.
    """
    if b2 > mean_b2:
        return 1.0

    if b2 >= SALE_PRICE:
        return -np.inf

    return float(((SALE_PRICE - mean_b2) / (SALE_PRICE - b2)) ** 3)


def expected_pnl_per_gardener(b1: int, b2: int, mean_b2: float) -> float:
    """
    Expected PnL per gardener/reserve draw.

    Bid 1 takes precedence.
    Bid 2 applies only to gardeners not already captured by bid 1.
    """
    n1 = count_reserves_beaten(b1)
    n2 = count_reserves_beaten(b2)

    n_second_only = max(n2 - n1, 0)

    pnl_first = n1 * (SALE_PRICE - b1)

    pf = penalty_factor(b2, mean_b2)
    pnl_second = n_second_only * (SALE_PRICE - b2) * pf

    return float((pnl_first + pnl_second) / N_RESERVE_LEVELS)


def total_expected_pnl(b1: int, b2: int, mean_b2: float, n_gardeners: int) -> float:
    return expected_pnl_per_gardener(b1, b2, mean_b2) * n_gardeners


def efficient_b1_candidates(b2: int) -> np.ndarray:
    """
    For bid 1, only bids of the form reserve + 1 matter:
    671, 676, 681, ..., because reserve rule is strict >.
    """
    candidates = []

    for r in RESERVES:
        bid = int(r + 1)
        if BID_MIN <= bid <= BID_MAX and bid < b2:
            candidates.append(bid)

    return np.array(candidates, dtype=int)


def best_b1_given_b2_and_mean(b2: int, mean_b2: float) -> dict | None:
    candidates = efficient_b1_candidates(b2)

    if len(candidates) == 0:
        return None

    values = np.array([
        expected_pnl_per_gardener(int(b1), int(b2), mean_b2)
        for b1 in candidates
    ])

    max_value = values.max()
    best_candidates = candidates[np.isclose(values, max_value)]

    return {
        "best_b1": int(best_candidates[0]),
        "all_tied_b1": list(map(int, best_candidates)),
        "pnl_per_gardener": float(max_value),
    }


def best_response_to_mean(mean_b2: float, force_above_mean: bool = False) -> dict:
    """
    Finds best response to a known mean.

    If force_above_mean=True, only considers b2 > mean_b2.
    """
    best = None

    for b2 in range(BID_MIN, BID_MAX + 1):
        if force_above_mean and not (b2 > mean_b2):
            continue

        result = best_b1_given_b2_and_mean(b2, mean_b2)

        if result is None:
            continue

        value = result["pnl_per_gardener"]

        if best is None or value > best["pnl_per_gardener"]:
            best = {
                "mean_b2": float(mean_b2),
                "b1": int(result["best_b1"]),
                "b2": int(b2),
                "pnl_per_gardener": float(value),
                "all_tied_b1": result["all_tied_b1"],
            }

    if best is None:
        raise RuntimeError(f"No feasible best response found for mean {mean_b2}")

    return best


def minimum_integer_clearing_bid(mean_b2: float) -> int:
    """
    Smallest integer bid 2 that is strictly above the expected mean.
    """
    return int(np.clip(np.floor(mean_b2) + 1, BID_MIN, BID_MAX))


def next_efficient_bid_at_or_above(min_bid: int | float) -> int:
    """
    Smallest reserve+1 bid at or above min_bid.

    These are the prices that unlock a new reserve bucket under the strict
    reserve rule.
    """
    efficient_bids = np.array(
        [int(r + 1) for r in RESERVES if BID_MIN <= r + 1 <= BID_MAX],
        dtype=int,
    )
    eligible = efficient_bids[efficient_bids >= min_bid]

    if len(eligible) == 0:
        return BID_MAX

    return int(eligible[0])


def buffered_level_bid(mean_b2: float, levels: int) -> int:
    """
    Bid just above the expected mean, optionally shifted by reserve buckets.

    levels=0 means the minimum integer above the mean.
    levels=1 means the next efficient reserve+1 bucket.
    levels=2 means one further reserve bucket, and so on.
    """
    min_clear = minimum_integer_clearing_bid(mean_b2)

    if levels <= 0:
        return min_clear

    return next_efficient_bid_at_or_above(min_clear + 5 * (levels - 1))


# ============================================================
# 3. Concentrated competitor bid model
# ============================================================

def tick_preference_weights(bids: np.ndarray) -> np.ndarray:
    """
    Preference by bid modulo 5.

    Since reserve prices are multiples of 5 and the rule is strict >,
    bids ending in 1 are mechanically efficient:
        841 beats reserve 840,
        846 beats reserve 845,
        851 beats reserve 850,
        etc.

    Logical players may also like +2 and +3 because they clear nearby means
    while staying close to the efficient bucket threshold.

    Exact multiples of 5 are deliberately given low weight because they are
    one tick below unlocking the next reserve bucket.
    """
    weights = np.ones(len(bids), dtype=float)

    for i, b in enumerate(bids):
        mod = b % 5

        if mod == 1:
            weights[i] = 1.00
        elif mod == 2:
            weights[i] = 0.82
        elif mod == 3:
            weights[i] = 0.58
        elif mod == 4:
            weights[i] = 0.28
        elif mod == 0:
            weights[i] = 0.14

    return weights


def gaussian_component(
    bids: np.ndarray,
    center: float,
    sigma: float,
    low: int,
    high: int,
    use_tick_preference: bool = True,
) -> np.ndarray:
    """
    Creates a discrete weighted component over integer bids.

    It is centred around `center`, with width `sigma`, truncated to [low, high].
    """
    weights = np.exp(-0.5 * ((bids - center) / sigma) ** 2)
    weights[(bids < low) | (bids > high)] = 0.0

    if use_tick_preference:
        weights *= tick_preference_weights(bids)

    total = weights.sum()

    if total <= 0:
        raise ValueError("Component has zero total probability. Check bounds/center/sigma.")

    return weights / total


def build_competitor_bid_pmf(config: dict) -> pd.DataFrame:
    """
    Builds a probability mass function over competitor bid 2 values.

    Each scenario is a mixture of:
    - nash_anchor: teams anchored close to the 836 mechanical optimum
    - strategic_core: main concentrated cluster
    - cautious: teams shading above the core to avoid missing the mean
    - aggressive_tail: small tail of very defensive teams
    """
    bids = COMPETITOR_BID_GRID.copy()
    pmf = np.zeros(len(bids), dtype=float)

    components = config["components"]

    for component in components:
        mass = component["mass"]
        comp_pmf = gaussian_component(
            bids=bids,
            center=component["center"],
            sigma=component["sigma"],
            low=component["low"],
            high=component["high"],
            use_tick_preference=component.get("use_tick_preference", True),
        )
        pmf += mass * comp_pmf

    pmf = pmf / pmf.sum()

    out = pd.DataFrame({
        "bid2": bids,
        "probability": pmf,
    })

    out["expected_count_out_of_4000"] = out["probability"] * N_COMPETITORS
    out["mod_5"] = out["bid2"] % 5

    return out


def component_reference_mean(expected_mean: float, component: dict) -> float:
    """
    Converts the public expected mean into the mean used by an archetype.

    Qualified teams should not be modelled as random noise around 836, but even
    strong teams retain some margin discipline. `mean_responsiveness` controls
    how fully an archetype updates away from the mechanical no-penalty anchor.
    """
    anchor = float(component.get("anchor", MECHANICAL_B2_ANCHOR))
    responsiveness = float(component.get("mean_responsiveness", 1.0))
    return anchor + responsiveness * (expected_mean - anchor)


def target_bid_for_component(expected_mean: float, component: dict) -> int | float:
    """
    Maps a behavioural archetype to its intended bid 2.
    """
    kind = component["kind"]
    reference_mean = component_reference_mean(expected_mean, component)

    if kind == "fixed":
        return float(component["bid"])

    if kind == "pure_best_response":
        return float(best_response_to_mean(reference_mean, force_above_mean=False)["b2"])

    if kind == "clear_best_response":
        return float(best_response_to_mean(reference_mean, force_above_mean=True)["b2"])

    if kind == "minimum_clear":
        return float(minimum_integer_clearing_bid(reference_mean))

    if kind == "level_buffer":
        return float(buffered_level_bid(reference_mean, int(component.get("levels", 1))))

    if kind == "noisy_qualified":
        return float(reference_mean + component.get("center_offset", 0.0))

    raise ValueError(f"Unknown competitor component kind: {kind}")


def bid_kernel_around_target(
    bids: np.ndarray,
    target_bid: int | float,
    sigma: float,
    low: int,
    high: int,
    use_tick_preference: bool = True,
) -> np.ndarray:
    """
    Builds a small discrete kernel around a target bid.
    """
    if sigma <= 0:
        bid = int(np.clip(round(target_bid), low, high))
        weights = np.zeros(len(bids), dtype=float)
        weights[bids == bid] = 1.0

        if weights.sum() == 0:
            raise ValueError(f"Target bid {bid} is outside competitor grid.")

        return weights

    return gaussian_component(
        bids=bids,
        center=float(target_bid),
        sigma=float(sigma),
        low=low,
        high=high,
        use_tick_preference=use_tick_preference,
    )


def build_behavioral_competitor_bid_pmf(
    config: dict,
    expected_mean: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Builds a competitor bid PMF from behavioural archetypes.

    The PMF is conditional on the field's current expectation of the mean.
    """
    bids = COMPETITOR_BID_GRID.copy()
    pmf = np.zeros(len(bids), dtype=float)
    component_rows = []

    total_mass = sum(float(component["mass"]) for component in config["components"])
    if total_mass <= 0:
        raise ValueError("Scenario component masses must sum to a positive number.")

    for component in config["components"]:
        mass = float(component["mass"]) / total_mass
        target_bid = target_bid_for_component(expected_mean, component)
        low = int(component.get("low", COMPETITOR_BID_MIN))
        high = int(component.get("high", COMPETITOR_BID_MAX))
        sigma = float(component.get("sigma", 0.0))

        comp_pmf = bid_kernel_around_target(
            bids=bids,
            target_bid=target_bid,
            sigma=sigma,
            low=low,
            high=high,
            use_tick_preference=component.get("use_tick_preference", True),
        )
        pmf += mass * comp_pmf

        component_rows.append({
            "component": component["name"],
            "kind": component["kind"],
            "mass": mass,
            "reference_mean": component_reference_mean(expected_mean, component),
            "target_bid": target_bid,
            "expected_component_bid": float((bids * comp_pmf).sum()),
            "sigma": sigma,
        })

    pmf = pmf / pmf.sum()

    pmf_df = pd.DataFrame({
        "bid2": bids,
        "probability": pmf,
    })
    pmf_df["expected_count_out_of_4000"] = pmf_df["probability"] * N_COMPETITORS
    pmf_df["mod_5"] = pmf_df["bid2"] % 5

    component_df = pd.DataFrame(component_rows)
    return pmf_df, component_df


def solve_behavioral_competitor_pmf(config: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Iterates expected mean -> behavioural bids -> implied mean.
    """
    expected_mean = float(config.get("initial_mean", MECHANICAL_B2_ANCHOR))
    damping = float(config.get("damping", 0.45))
    tolerance = float(config.get("tolerance", 1e-4))
    max_iterations = int(config.get("max_iterations", 80))
    history_rows = []

    pmf_df = None
    component_df = None

    for iteration in range(max_iterations):
        pmf_df, component_df = build_behavioral_competitor_bid_pmf(
            config=config,
            expected_mean=expected_mean,
        )
        implied_mean = float((pmf_df["bid2"] * pmf_df["probability"]).sum())

        history_rows.append({
            "iteration": iteration,
            "expected_mean_in": expected_mean,
            "implied_mean_out": implied_mean,
            "absolute_gap": abs(implied_mean - expected_mean),
        })

        if abs(implied_mean - expected_mean) <= tolerance:
            break

        expected_mean = damping * expected_mean + (1.0 - damping) * implied_mean

    if pmf_df is None or component_df is None:
        raise RuntimeError("Behavioural competitor PMF was not generated.")

    history_df = pd.DataFrame(history_rows)
    return pmf_df, component_df, history_df


def simulate_competitor_means_from_pmf(
    pmf_df: pd.DataFrame,
    n_sims: int = N_SIMS,
    n_competitors: int = N_COMPETITORS,
    seed: int = SEED,
) -> np.ndarray:
    """
    Simulates competitor mean bid 2 values from a fixed bid PMF.
    """
    rng = np.random.default_rng(seed)

    bids = pmf_df["bid2"].to_numpy()
    probs = pmf_df["probability"].to_numpy()
    probs = probs / probs.sum()

    means = np.empty(n_sims)

    for s in range(n_sims):
        sampled_bids = rng.choice(bids, size=n_competitors, p=probs)
        means[s] = sampled_bids.mean()

    return means


# ============================================================
# 4. Scenario definitions
# ============================================================

SCENARIO_CONFIGS = {
    "low": {
        "description": (
            "Low-mean behavioural case. Qualified teams mostly best-respond to "
            "the expected mean, but remain strongly margin-disciplined and only "
            "a small minority adds a full reserve-level buffer."
        ),
        "scenario_weight": 0.25,
        "initial_mean": 846.0,
        "damping": 0.50,
        "components": [
            {
                "name": "pure_best_response",
                "kind": "pure_best_response",
                "mass": 0.520,
                "sigma": 0.9,
                "mean_responsiveness": 0.95,
            },
            {
                "name": "minimum_clear",
                "kind": "minimum_clear",
                "mass": 0.230,
                "sigma": 0.8,
                "mean_responsiveness": 0.80,
            },
            {
                "name": "one_level_buffer",
                "kind": "level_buffer",
                "levels": 1,
                "mass": 0.190,
                "sigma": 1.1,
                "mean_responsiveness": 0.70,
            },
            {
                "name": "two_level_buffer",
                "kind": "level_buffer",
                "levels": 2,
                "mass": 0.045,
                "sigma": 1.2,
                "mean_responsiveness": 0.65,
            },
            {
                "name": "pure_no_penalty_anchor",
                "kind": "fixed",
                "bid": MECHANICAL_B2_ANCHOR,
                "mass": 0.010,
                "sigma": 0.8,
            },
            {
                "name": "qualified_noise",
                "kind": "noisy_qualified",
                "mass": 0.005,
                "sigma": 5.0,
                "mean_responsiveness": 0.80,
            },
        ],
    },

    "central": {
        "description": (
            "Main behavioural belief. The qualified field is logical and cautious, "
            "with almost no naive/noisy mass. Most teams best-respond or add one "
            "reserve-level buffer, while still anchoring partly to margin discipline."
        ),
        "scenario_weight": 0.55,
        "initial_mean": 852.0,
        "damping": 0.50,
        "components": [
            {
                "name": "pure_best_response",
                "kind": "pure_best_response",
                "mass": 0.420,
                "sigma": 0.9,
                "mean_responsiveness": 1.00,
            },
            {
                "name": "minimum_clear",
                "kind": "minimum_clear",
                "mass": 0.160,
                "sigma": 0.8,
                "mean_responsiveness": 0.85,
            },
            {
                "name": "one_level_buffer",
                "kind": "level_buffer",
                "levels": 1,
                "mass": 0.290,
                "sigma": 1.1,
                "mean_responsiveness": 0.80,
            },
            {
                "name": "two_level_buffer",
                "kind": "level_buffer",
                "levels": 2,
                "mass": 0.120,
                "sigma": 1.2,
                "mean_responsiveness": 0.70,
            },
            {
                "name": "pure_no_penalty_anchor",
                "kind": "fixed",
                "bid": MECHANICAL_B2_ANCHOR,
                "mass": 0.005,
                "sigma": 0.8,
            },
            {
                "name": "qualified_noise",
                "kind": "noisy_qualified",
                "mass": 0.005,
                "sigma": 5.0,
                "mean_responsiveness": 0.85,
            },
        ],
    },

    "high": {
        "description": (
            "High-mean behavioural case. Qualified teams place heavy weight on "
            "clearing the mean, and a larger share adds one or two reserve-level "
            "buffers. Naive/noisy mass remains tiny."
        ),
        "scenario_weight": 0.20,
        "initial_mean": 858.0,
        "damping": 0.55,
        "components": [
            {
                "name": "pure_best_response",
                "kind": "pure_best_response",
                "mass": 0.300,
                "sigma": 0.9,
                "mean_responsiveness": 1.00,
            },
            {
                "name": "minimum_clear",
                "kind": "minimum_clear",
                "mass": 0.100,
                "sigma": 0.8,
                "mean_responsiveness": 0.95,
            },
            {
                "name": "one_level_buffer",
                "kind": "level_buffer",
                "levels": 1,
                "mass": 0.320,
                "sigma": 1.1,
                "mean_responsiveness": 0.95,
            },
            {
                "name": "two_level_buffer",
                "kind": "level_buffer",
                "levels": 2,
                "mass": 0.200,
                "sigma": 1.2,
                "mean_responsiveness": 0.90,
            },
            {
                "name": "three_level_buffer",
                "kind": "level_buffer",
                "levels": 3,
                "mass": 0.070,
                "sigma": 1.5,
                "mean_responsiveness": 0.85,
            },
            {
                "name": "pure_no_penalty_anchor",
                "kind": "fixed",
                "bid": MECHANICAL_B2_ANCHOR,
                "mass": 0.002,
                "sigma": 0.8,
            },
            {
                "name": "qualified_noise",
                "kind": "noisy_qualified",
                "mass": 0.008,
                "sigma": 5.0,
                "mean_responsiveness": 0.95,
            },
        ],
    },
}


# ============================================================
# 5. Evaluation helpers
# ============================================================

def prob_above_mean(b2: int, simulated_means: np.ndarray) -> float:
    return float(np.mean(b2 > simulated_means))


def shortfall_stats(b2: int, simulated_means: np.ndarray) -> dict:
    shortfalls = np.maximum(simulated_means - b2, 0)
    failed = shortfalls > 0

    return {
        "mean_shortfall_unconditional": float(shortfalls.mean()),
        "mean_shortfall_if_fails": (
            float(shortfalls[failed].mean()) if np.any(failed) else 0.0
        ),
    }


def expected_pnl_over_mean_distribution(
    b1: int,
    b2: int,
    simulated_means: np.ndarray,
) -> float:
    n1 = count_reserves_beaten(b1)
    n2 = count_reserves_beaten(b2)
    n_second_only = max(n2 - n1, 0)

    pnl_first = n1 * (SALE_PRICE - b1)

    penalty_factors = np.ones_like(simulated_means, dtype=float)
    penalised = b2 <= simulated_means

    if np.any(penalised):
        if b2 >= SALE_PRICE:
            penalty_factors[penalised] = -np.inf
        else:
            penalty_factors[penalised] = (
                (SALE_PRICE - simulated_means[penalised]) / (SALE_PRICE - b2)
            ) ** 3

    pnl_second = n_second_only * (SALE_PRICE - b2) * penalty_factors
    values = (pnl_first + pnl_second) / N_RESERVE_LEVELS

    return float(values.mean())


def best_fixed_pair_under_uncertainty(simulated_means: np.ndarray) -> pd.DataFrame:
    """
    Finds best fixed pair (b1, b2) before knowing the realised competitor mean.

    This uses the actual penalty formula if b2 does not clear the mean in a simulation.
    """
    rows = []

    for b2 in range(BID_MIN, BID_MAX + 1):
        b1_candidates = efficient_b1_candidates(b2)

        for b1 in b1_candidates:
            avg_pnl = expected_pnl_over_mean_distribution(b1, b2, simulated_means)
            p_clear = prob_above_mean(b2, simulated_means)
            shortfalls = shortfall_stats(b2, simulated_means)

            rows.append({
                "b1": int(b1),
                "b2": int(b2),
                "expected_pnl_per_gardener": avg_pnl,
                "prob_b2_above_mean": p_clear,
                **shortfalls,
            })

    df = pd.DataFrame(rows)
    df = df.sort_values("expected_pnl_per_gardener", ascending=False).reset_index(drop=True)

    return df


def best_by_b2_table(best_pairs: pd.DataFrame) -> pd.DataFrame:
    """
    For each b2, keep the best b1 for that b2.
    """
    return (
        best_pairs
        .sort_values("expected_pnl_per_gardener", ascending=False)
        .groupby("b2", as_index=False)
        .first()
        .sort_values("b2")
        .reset_index(drop=True)
    )


def scenario_summary_row(
    scenario_name: str,
    config: dict,
    means: np.ndarray,
    best_pairs: pd.DataFrame,
) -> dict:
    best = best_pairs.iloc[0]

    return {
        "scenario": scenario_name,
        "scenario_weight": float(config.get("scenario_weight", 1.0)),
        "mean_of_mean": float(means.mean()),
        "std_of_mean": float(means.std()),
        "p1_mean": float(np.percentile(means, 1)),
        "p5_mean": float(np.percentile(means, 5)),
        "p25_mean": float(np.percentile(means, 25)),
        "median_mean": float(np.percentile(means, 50)),
        "p75_mean": float(np.percentile(means, 75)),
        "p95_mean": float(np.percentile(means, 95)),
        "p99_mean": float(np.percentile(means, 99)),
        "best_b1": int(best["b1"]),
        "best_b2": int(best["b2"]),
        "best_expected_pnl_per_gardener": float(best["expected_pnl_per_gardener"]),
        "prob_best_b2_above_mean": float(best["prob_b2_above_mean"]),
    }


def safe_bid_recommendations(means: np.ndarray) -> pd.DataFrame:
    """
    Quantile-based safety bids:
    Choose b2 = floor(qth percentile of mean) + 1,
    then choose exact best b1 conditional on that b2 and that percentile mean.

    These are not necessarily EV-optimal; they show what it costs to target
    different probabilities of clearing the mean.
    """
    rows = []

    for q in [50, 60, 70, 75, 80, 85, 90, 95, 97.5, 99]:
        mean_q = float(np.percentile(means, q))
        b2 = int(np.floor(mean_q) + 1)

        result = best_b1_given_b2_and_mean(b2, mean_q)
        if result is None:
            continue

        ev = expected_pnl_over_mean_distribution(result["best_b1"], b2, means)

        rows.append({
            "target_clear_percentile": q,
            "mean_quantile": mean_q,
            "suggested_b1": int(result["best_b1"]),
            "suggested_b2": int(b2),
            "expected_pnl_per_gardener_under_distribution": ev,
            "actual_prob_b2_above_mean": prob_above_mean(b2, means),
        })

    return pd.DataFrame(rows)


def robust_candidate_table(
    scenario_outputs: dict,
    candidate_b2s: list[int] | None = None,
) -> pd.DataFrame:
    """
    Evaluates fixed candidate bids across all behavioural scenarios.

    For each b2, choose the b1 that maximises scenario-weighted EV. This keeps
    bid 1 as a conditional payoff choice rather than a risk-aversion lever.
    """
    if candidate_b2s is None:
        candidate_b2s = [
            846, 847, 848, 851, 852, 853, 856, 861,
            866, 871, 876, 881, 883, 886, 891,
        ]

    scenario_best = {
        scenario_name: float(output["best_pairs"].iloc[0]["expected_pnl_per_gardener"])
        for scenario_name, output in scenario_outputs.items()
    }
    raw_scenario_weights = {
        scenario_name: float(output["config"].get("scenario_weight", 1.0))
        for scenario_name, output in scenario_outputs.items()
    }
    total_weight = sum(raw_scenario_weights.values())
    scenario_weights = {
        scenario_name: weight / total_weight
        for scenario_name, weight in raw_scenario_weights.items()
    }

    rows = []

    for b2 in candidate_b2s:
        b1_candidates = efficient_b1_candidates(b2)
        if len(b1_candidates) == 0:
            continue

        b1_rows = []
        for b1 in b1_candidates:
            row = {
                "b1": int(b1),
                "b2": int(b2),
            }
            scenario_evs = []
            scenario_regrets = []
            weighted_ev = 0.0
            weighted_regret = 0.0

            for scenario_name, output in scenario_outputs.items():
                means = output["means"]
                ev = expected_pnl_over_mean_distribution(int(b1), int(b2), means)
                p_clear = prob_above_mean(int(b2), means)
                regret = scenario_best[scenario_name] - ev
                weight = scenario_weights[scenario_name]

                row[f"{scenario_name}_ev"] = ev
                row[f"{scenario_name}_p_clear"] = p_clear
                row[f"{scenario_name}_regret"] = regret
                scenario_evs.append(ev)
                scenario_regrets.append(regret)
                weighted_ev += weight * ev
                weighted_regret += weight * regret

            row["weighted_ev"] = float(weighted_ev)
            row["average_ev"] = float(np.mean(scenario_evs))
            row["min_ev"] = float(np.min(scenario_evs))
            row["weighted_regret"] = float(weighted_regret)
            row["max_regret"] = float(np.max(scenario_regrets))
            b1_rows.append(row)

        best_row = sorted(
            b1_rows,
            key=lambda r: (r["weighted_ev"], r["min_ev"], -r["max_regret"]),
            reverse=True,
        )[0]
        rows.append(best_row)

    df = pd.DataFrame(rows)
    return df.sort_values(
        ["weighted_ev", "min_ev"],
        ascending=False,
    ).reset_index(drop=True)


def bid_distribution_summary(pmf_df: pd.DataFrame) -> pd.DataFrame:
    """
    Summarises the theoretical competitor bid PMF.
    """
    df = pmf_df.copy()

    expected_bid = float((df["bid2"] * df["probability"]).sum())
    mass_mod_1_2_3 = float(df.loc[df["mod_5"].isin([1, 2, 3]), "probability"].sum())
    mass_mod_4_0 = float(df.loc[df["mod_5"].isin([4, 0]), "probability"].sum())
    mass_round = float(df.loc[df["mod_5"].eq(0), "probability"].sum())

    return pd.DataFrame([{
        "expected_competitor_bid": expected_bid,
        "mass_on_mod_1_2_3": mass_mod_1_2_3,
        "mass_on_mod_4_or_0": mass_mod_4_0,
        "mass_on_exact_multiples_of_5": mass_round,
    }])


# ============================================================
# 6. Plotting helpers
# ============================================================

def save_competitor_pmf_plot(scenario_outputs: dict) -> None:
    plt.figure(figsize=(12, 6))

    for scenario_name, output in scenario_outputs.items():
        pmf_df = output["pmf"]
        plt.plot(
            pmf_df["bid2"],
            pmf_df["probability"],
            marker="o",
            markersize=3,
            linewidth=1.2,
            label=scenario_name,
        )

    plt.xlabel("Competitor bid 2")
    plt.ylabel("Probability")
    plt.title("Modelled competitor bid 2 distribution by scenario")
    plt.legend()
    plt.tight_layout()
    plt.savefig(OUT_DIR / "r3_competitor_bid_pmf_by_scenario.png", dpi=200)
    plt.close()


def save_mean_distribution_plot(scenario_outputs: dict) -> None:
    plt.figure(figsize=(12, 6))

    for scenario_name, output in scenario_outputs.items():
        means = output["means"]
        plt.hist(means, bins=60, alpha=0.45, label=scenario_name)

    plt.xlabel("Competitor mean second bid")
    plt.ylabel("Frequency")
    plt.title("Simulated competitor mean bid 2 by scenario")
    plt.legend()
    plt.tight_layout()
    plt.savefig(OUT_DIR / "r3_mean_distribution_by_scenario.png", dpi=200)
    plt.close()


def save_expected_pnl_by_b2_plot(scenario_outputs: dict) -> None:
    plt.figure(figsize=(12, 6))

    for scenario_name, output in scenario_outputs.items():
        best_by_b2 = best_by_b2_table(output["best_pairs"])

        plt.plot(
            best_by_b2["b2"],
            best_by_b2["expected_pnl_per_gardener"],
            label=scenario_name,
        )

    plt.xlabel("Your fixed bid 2")
    plt.ylabel("Expected PnL per gardener")
    plt.title("Expected PnL by fixed bid 2 under each scenario")
    plt.legend()
    plt.tight_layout()
    plt.savefig(OUT_DIR / "r3_expected_pnl_by_b2_by_scenario.png", dpi=200)
    plt.close()


def save_clear_probability_by_b2_plot(scenario_outputs: dict) -> None:
    plt.figure(figsize=(12, 6))

    for scenario_name, output in scenario_outputs.items():
        means = output["means"]
        b2_grid = np.arange(830, 891)
        probs = [prob_above_mean(int(b2), means) for b2 in b2_grid]

        plt.plot(b2_grid, probs, label=scenario_name)

    plt.xlabel("Your bid 2")
    plt.ylabel("Probability bid 2 > competitor mean")
    plt.title("Probability your bid 2 clears the mean")
    plt.legend()
    plt.tight_layout()
    plt.savefig(OUT_DIR / "r3_clear_probability_by_b2.png", dpi=200)
    plt.close()


def save_best_response_plots() -> pd.DataFrame:
    mean_grid = np.arange(830, 881, 0.25)

    rows = []
    for m in mean_grid:
        pure = best_response_to_mean(float(m), force_above_mean=False)
        clear = best_response_to_mean(float(m), force_above_mean=True)
        rows.append({
            "mean_b2": float(m),
            "pure_b1": pure["b1"],
            "pure_b2": pure["b2"],
            "pure_pnl_per_gardener": pure["pnl_per_gardener"],
            "pure_tied_b1": pure["all_tied_b1"],
            "clear_b1": clear["b1"],
            "clear_b2": clear["b2"],
            "clear_pnl_per_gardener": clear["pnl_per_gardener"],
            "clear_tied_b1": clear["all_tied_b1"],
        })

    response_df = pd.DataFrame(rows)
    response_df.to_csv(OUT_DIR / "r3_best_response_to_known_mean.csv", index=False)

    plt.figure(figsize=(12, 6))
    plt.plot(response_df["mean_b2"], response_df["pure_b2"], label="pure formula EV")
    plt.plot(response_df["mean_b2"], response_df["clear_b2"], label="strictly above mean")
    plt.xlabel("Assumed competitor mean bid 2")
    plt.ylabel("Best response bid 2")
    plt.title("Best response bid 2 to known mean")
    plt.legend()
    plt.tight_layout()
    plt.savefig(OUT_DIR / "r3_best_response_b2_to_known_mean.png", dpi=200)
    plt.close()

    plt.figure(figsize=(12, 6))
    plt.plot(response_df["mean_b2"], response_df["pure_b1"], label="pure formula EV")
    plt.plot(response_df["mean_b2"], response_df["clear_b1"], label="strictly above mean")
    plt.xlabel("Assumed competitor mean bid 2")
    plt.ylabel("Optimal bid 1")
    plt.title("Optimal bid 1 conditional on best-response bid 2")
    plt.legend()
    plt.tight_layout()
    plt.savefig(OUT_DIR / "r3_best_response_b1_to_known_mean.png", dpi=200)
    plt.close()

    plt.figure(figsize=(12, 6))
    plt.plot(response_df["mean_b2"], response_df["pure_pnl_per_gardener"], label="pure formula EV")
    plt.plot(response_df["mean_b2"], response_df["clear_pnl_per_gardener"], label="strictly above mean")
    plt.xlabel("Assumed competitor mean bid 2")
    plt.ylabel("Expected PnL per gardener")
    plt.title("Expected PnL per gardener under best response")
    plt.legend()
    plt.tight_layout()
    plt.savefig(OUT_DIR / "r3_best_response_pnl_to_known_mean.png", dpi=200)
    plt.close()

    return response_df


# ============================================================
# 7. Recommendation text
# ============================================================

def make_recommendations_text(
    summary_df: pd.DataFrame,
    scenario_outputs: dict,
    robust_df: pd.DataFrame,
) -> str:
    lines = []

    lines.append("Round 3 bid prediction recommendations")
    lines.append("=" * 45)
    lines.append("")
    lines.append("Core assumptions:")
    lines.append("- Reserve prices are 670, 675, ..., 920 inclusive.")
    lines.append("- Trade requires bid > reserve.")
    lines.append("- Bid 1 takes precedence.")
    lines.append("- Bid 2 is unpenalised only if b2 > competitor mean bid 2.")
    lines.append("- Competitor distribution is scenario-based, not known.")
    lines.append("")

    lines.append("Behavioural modelling update:")
    lines.append("- Nash/mechanical no-penalty anchor is b1=751, b2=836.")
    lines.append("- Very few qualified teams are modelled as pure no-penalty or noisy/manual bidders.")
    lines.append("- Competitors form an expected mean; behavioural archetypes bid from that belief; the implied mean is iterated to a fixed point.")
    lines.append("- Risk aversion affects bid 2 only. Bid 1 is optimised mechanically conditional on bid 2.")
    lines.append("- Caution pushes the mean upward, but margin discipline keeps archetypes partly anchored.")
    lines.append("- Exact round numbers are given low weight; reserve+1 prices remain preferred.")
    lines.append("")

    lines.append("Scenario summary:")
    lines.append(summary_df.to_string(index=False))
    lines.append("")

    lines.append("Top EV bid pair by scenario:")
    for _, row in summary_df.iterrows():
        lines.append(
            f"- {row['scenario']}: b1={int(row['best_b1'])}, "
            f"b2={int(row['best_b2'])}, "
            f"EV/gardener={row['best_expected_pnl_per_gardener']:.4f}, "
            f"P(b2 > mean)={row['prob_best_b2_above_mean']:.3f}"
        )

    lines.append("")
    lines.append("Central scenario top 10:")
    central_top = scenario_outputs["central"]["best_pairs"].head(10)
    lines.append(central_top.to_string(index=False))

    lines.append("")
    lines.append("Central scenario safety table:")
    lines.append(scenario_outputs["central"]["safe_recs"].to_string(index=False))

    lines.append("")
    lines.append("Robust candidate table across scenarios:")
    robust_cols = [
        "b1", "b2", "weighted_ev", "average_ev", "min_ev", "weighted_regret", "max_regret",
        "low_ev", "central_ev", "high_ev",
        "low_p_clear", "central_p_clear", "high_p_clear",
    ]
    existing_cols = [c for c in robust_cols if c in robust_df.columns]
    lines.append(robust_df[existing_cols].to_string(index=False))

    lines.append("")
    lines.append("Interpretation:")
    lines.append(
        "The EV-optimal fixed pair depends strongly on the behavioural fixed point. "
        "A modest miss can sometimes be tolerable because the cubic factor is mild "
        "near the mean, but it becomes expensive several ticks below the field."
    )
    lines.append(
        "The robust table is the best place to choose between candidate bids: it "
        "shows weighted EV using scenario weights low=25%, central=55%, high=20%, "
        "plus worst-scenario EV, regret, and clear probability under each fixed point."
    )
    lines.append(
        "The low scenario represents stronger margin anchoring. The high scenario "
        "represents stronger caution around the strict above-mean rule."
    )

    return "\n".join(lines)


# ============================================================
# 8. Main run
# ============================================================

def main() -> None:
    print(f"Writing outputs to: {OUT_DIR}")
    print(f"Reserve levels: {N_RESERVE_LEVELS}")
    print(f"Reserve range: {RESERVES.min()} to {RESERVES.max()}")
    print(f"Competitors: {N_COMPETITORS}")
    print(f"Simulations per scenario: {N_SIMS}")

    scenario_outputs = {}
    summary_rows = []

    for i, (scenario_name, config) in enumerate(SCENARIO_CONFIGS.items()):
        print(f"\nRunning scenario: {scenario_name}")
        print(config["description"])

        pmf_df, component_df, iteration_df = solve_behavioral_competitor_pmf(config)

        means = simulate_competitor_means_from_pmf(
            pmf_df=pmf_df,
            n_sims=N_SIMS,
            n_competitors=N_COMPETITORS,
            seed=SEED + i,
        )

        best_pairs = best_fixed_pair_under_uncertainty(means)
        safe_recs = safe_bid_recommendations(means)
        pmf_summary = bid_distribution_summary(pmf_df)

        scenario_outputs[scenario_name] = {
            "config": config,
            "pmf": pmf_df,
            "components": component_df,
            "iterations": iteration_df,
            "pmf_summary": pmf_summary,
            "means": means,
            "best_pairs": best_pairs,
            "safe_recs": safe_recs,
        }

        summary_rows.append(scenario_summary_row(scenario_name, config, means, best_pairs))

        # Save scenario-level files
        pmf_df.to_csv(
            OUT_DIR / f"r3_competitor_bid_pmf_{scenario_name}.csv",
            index=False,
        )

        pmf_summary.to_csv(
            OUT_DIR / f"r3_competitor_bid_pmf_summary_{scenario_name}.csv",
            index=False,
        )

        component_df.to_csv(
            OUT_DIR / f"r3_behavioral_components_{scenario_name}.csv",
            index=False,
        )

        iteration_df.to_csv(
            OUT_DIR / f"r3_behavioral_iterations_{scenario_name}.csv",
            index=False,
        )

        pd.DataFrame({"mean_b2": means}).to_csv(
            OUT_DIR / f"r3_simulated_means_{scenario_name}.csv",
            index=False,
        )

        best_pairs.head(100).to_csv(
            OUT_DIR / f"r3_top_100_bid_pairs_{scenario_name}.csv",
            index=False,
        )

        best_by_b2_table(best_pairs).to_csv(
            OUT_DIR / f"r3_best_by_b2_{scenario_name}.csv",
            index=False,
        )

        safe_recs.to_csv(
            OUT_DIR / f"r3_safety_recommendations_{scenario_name}.csv",
            index=False,
        )

    summary_df = pd.DataFrame(summary_rows)
    summary_df.to_csv(OUT_DIR / "r3_scenario_summary.csv", index=False)

    robust_df = robust_candidate_table(scenario_outputs)
    robust_df.to_csv(OUT_DIR / "r3_robust_candidate_bids.csv", index=False)

    # Manual known-mean best response table
    manual_means = [836, 837, 838, 839, 840, 841, 845, 850, 855, 860, 865, 870]
    manual_rows = []

    for m in manual_means:
        pure = best_response_to_mean(float(m), force_above_mean=False)
        clear = best_response_to_mean(float(m), force_above_mean=True)
        manual_rows.append({
            "assumed_mean": m,
            "pure_best_b1": pure["b1"],
            "pure_best_b2": pure["b2"],
            "pure_pnl_per_gardener": pure["pnl_per_gardener"],
            "clear_best_b1": clear["b1"],
            "clear_best_b2": clear["b2"],
            "clear_pnl_per_gardener": clear["pnl_per_gardener"],
        })

    manual_df = pd.DataFrame(manual_rows)
    manual_df.to_csv(OUT_DIR / "r3_manual_best_responses.csv", index=False)

    # Save plots
    save_competitor_pmf_plot(scenario_outputs)
    save_mean_distribution_plot(scenario_outputs)
    save_expected_pnl_by_b2_plot(scenario_outputs)
    save_clear_probability_by_b2_plot(scenario_outputs)
    save_best_response_plots()

    # Save recommendation text
    rec_text = make_recommendations_text(summary_df, scenario_outputs, robust_df)
    (OUT_DIR / "r3_recommendations.txt").write_text(rec_text, encoding="utf-8")

    print("\nScenario summary:")
    print(summary_df.to_string(index=False))

    print("\nManual best responses:")
    print(manual_df.to_string(index=False))

    print("\nRobust candidate bids:")
    print(robust_df.to_string(index=False))

    print("\nCentral scenario competitor bid PMF summary:")
    print(scenario_outputs["central"]["pmf_summary"].to_string(index=False))

    print("\nCentral scenario behavioural components:")
    print(scenario_outputs["central"]["components"].to_string(index=False))

    print("\nCentral scenario top 15 competitor bid probabilities:")
    print(
        scenario_outputs["central"]["pmf"]
        .sort_values("probability", ascending=False)
        .head(15)
        .to_string(index=False)
    )

    print("\nCentral scenario top 15 bid pairs:")
    print(
        scenario_outputs["central"]["best_pairs"]
        .head(15)
        .to_string(index=False)
    )

    print("\nFiles written:")
    for path in sorted(OUT_DIR.glob("r3_*")):
        print(f"- {path.name}")


if __name__ == "__main__":
    main()
