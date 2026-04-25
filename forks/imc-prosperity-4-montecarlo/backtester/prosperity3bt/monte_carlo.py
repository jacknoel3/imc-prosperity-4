from __future__ import annotations

import csv
import json
import math
import os
import random
import shutil
import statistics
import subprocess
from datetime import datetime
from importlib import import_module, reload
from pathlib import Path
from typing import Any, Optional

from prosperity3bt.file_reader import FileSystemReader
from prosperity3bt.models import BacktestResult, TradeMatchingMode
from prosperity3bt.runner import run_backtest


DAY_OFFSETS = {-2: 0, -1: 1_000_000}
CHART_POINTS_PER_SERIES = 1500
STATIC_CHART_POINTS = 600
PRODUCT_A = "ASH_COATED_OSMIUM"
PRODUCT_B = "INTARIAN_PEPPER_ROOT"
PRODUCTS = [PRODUCT_A, PRODUCT_B]
ROUND3_PRODUCTS = [
    "HYDROGEL_PACK",
    "VELVETFRUIT_EXTRACT",
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
ROUND3_SPOT_PRODUCTS = {"HYDROGEL_PACK", "VELVETFRUIT_EXTRACT"}
ROUND3_OPTION_PRODUCTS = {product for product in ROUND3_PRODUCTS if product.startswith("VEV_")}
GENERATED_OUTPUT_FILES = {
    "dashboard.json",
    "session_summary.csv",
    "run_summary.csv",
    "run.log",
}
GENERATED_OUTPUT_DIRS = {
    "sample_paths",
    "sessions",
    "static_charts",
}


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def rust_dir() -> Path:
    return project_root() / "rust_simulator"


def default_dashboard_path() -> Path:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return Path.cwd() / "backtests" / f"{timestamp}_monte_carlo" / "dashboard.json"


def normalize_dashboard_path(out: Optional[Path], no_out: bool) -> Optional[Path]:
    if no_out:
        return None

    if out is None:
        return default_dashboard_path()

    if out.suffix.lower() == ".json":
        return out

    return out / "dashboard.json"


def resolve_actual_dir(data_root: Optional[Path]) -> Path:
    if data_root is None:
        return project_root() / "data" / "round1"

    if data_root.name == "round1":
        return data_root

    round1 = data_root / "round1"
    if round1.is_dir():
        return round1

    return data_root


def quantile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")

    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return sorted_values[0]

    index = (len(sorted_values) - 1) * q
    lo = math.floor(index)
    hi = math.ceil(index)
    if lo == hi:
        return sorted_values[lo]

    weight = index - lo
    return sorted_values[lo] * (1.0 - weight) + sorted_values[hi] * weight


def sample_std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    return statistics.stdev(values)


def downside_deviation(values: list[float]) -> float:
    downside = [min(value, 0.0) ** 2 for value in values]
    if not downside:
        return 0.0
    return math.sqrt(sum(downside) / len(downside))


def skewness(values: list[float]) -> float:
    if len(values) < 3:
        return 0.0
    mean = statistics.fmean(values)
    std = sample_std(values)
    if std == 0:
        return 0.0
    return sum(((value - mean) / std) ** 3 for value in values) / len(values)


def correlation(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or len(a) < 2:
        return 0.0
    mean_a = statistics.fmean(a)
    mean_b = statistics.fmean(b)
    std_a = sample_std(a)
    std_b = sample_std(b)
    if std_a == 0 or std_b == 0:
        return 0.0
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b)) / (len(a) - 1)
    return cov / (std_a * std_b)


def summarize_distribution(values: list[float]) -> dict[str, float]:
    if not values:
        return {}

    mean = statistics.fmean(values)
    std = sample_std(values)
    downside = downside_deviation(values)
    q05 = quantile(values, 0.05)
    q01 = quantile(values, 0.01)
    tail_5 = [value for value in values if value <= q05] or [min(values)]
    tail_1 = [value for value in values if value <= q01] or [min(values)]
    ci_half_width = 1.96 * std / math.sqrt(len(values)) if len(values) > 1 else 0.0

    return {
        "count": float(len(values)),
        "mean": mean,
        "std": std,
        "min": min(values),
        "p01": q01,
        "p05": q05,
        "p10": quantile(values, 0.10),
        "p25": quantile(values, 0.25),
        "p50": quantile(values, 0.50),
        "p75": quantile(values, 0.75),
        "p90": quantile(values, 0.90),
        "p95": quantile(values, 0.95),
        "p99": quantile(values, 0.99),
        "max": max(values),
        "positiveRate": sum(value > 0 for value in values) / len(values),
        "negativeRate": sum(value < 0 for value in values) / len(values),
        "zeroRate": sum(value == 0 for value in values) / len(values),
        "var95": q05,
        "cvar95": statistics.fmean(tail_5),
        "var99": q01,
        "cvar99": statistics.fmean(tail_1),
        "meanConfidenceLow95": mean - ci_half_width,
        "meanConfidenceHigh95": mean + ci_half_width,
        "sharpeLike": mean / std if std > 0 else 0.0,
        "sortinoLike": mean / downside if downside > 0 else 0.0,
        "skewness": skewness(values),
    }


def histogram(values: list[float], bins: int = 40) -> dict[str, list[float] | list[int]]:
    if not values:
        return {"binEdges": [], "counts": []}

    lo = min(values)
    hi = max(values)
    if lo == hi:
        lo -= 0.5
        hi += 0.5

    width = (hi - lo) / bins
    edges = [lo + i * width for i in range(bins + 1)]
    counts = [0 for _ in range(bins)]
    for value in values:
        idx = min(int((value - lo) / width), bins - 1)
        counts[idx] += 1

    return {"binEdges": edges, "counts": counts}


def mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def normal_pdf(x: float, mu: float, sigma: float) -> float:
    if sigma <= 0:
        return 0.0
    z = (x - mu) / sigma
    return math.exp(-0.5 * z * z) / (sigma * math.sqrt(2.0 * math.pi))


def fit_r_squared(actual: list[float], predicted: list[float]) -> float:
    if not actual or len(actual) != len(predicted):
        return 0.0
    actual_mean = mean(actual)
    sst = sum((value - actual_mean) ** 2 for value in actual)
    if sst <= 1e-12:
        return 0.0
    sse = sum((a - b) ** 2 for a, b in zip(actual, predicted))
    return max(0.0, 1.0 - sse / sst)


def normal_fit(values: list[float], bins: int = 40, points: int = 200) -> dict[str, Any]:
    hist = histogram(values, bins)
    bin_edges = hist["binEdges"]
    counts = hist["counts"]
    mu = mean(values)
    sigma = sample_std(values)

    if len(bin_edges) < 2:
        return {"mean": mu, "std": sigma, "r2": 0.0, "line": []}

    bin_width = float(bin_edges[1] - bin_edges[0])
    centers = [(bin_edges[index] + bin_edges[index + 1]) / 2.0 for index in range(len(counts))]
    expected_counts = [normal_pdf(center, mu, sigma) * len(values) * bin_width for center in centers]
    lo = float(bin_edges[0])
    hi = float(bin_edges[-1])
    line = []
    if points <= 1:
        points = 2
    for index in range(points):
        x = lo + (hi - lo) * index / (points - 1)
        y = normal_pdf(x, mu, sigma) * len(values) * bin_width
        line.append([x, y])

    return {
        "mean": mu,
        "std": sigma,
        "r2": fit_r_squared([float(count) for count in counts], expected_counts),
        "line": line,
    }


def linear_regression(x_values: list[float], y_values: list[float]) -> dict[str, Any]:
    if len(x_values) != len(y_values) or len(x_values) < 2:
        return {
            "slope": 0.0,
            "intercept": 0.0,
            "r2": 0.0,
            "correlation": 0.0,
            "line": [],
            "diagnosis": "insufficient data",
        }

    x_mean = mean(x_values)
    y_mean = mean(y_values)
    sxx = sum((x - x_mean) ** 2 for x in x_values)
    sxy = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, y_values))
    slope = sxy / sxx if sxx > 1e-12 else 0.0
    intercept = y_mean - slope * x_mean
    corr = correlation(x_values, y_values)
    r2 = corr * corr
    x_min = min(x_values)
    x_max = max(x_values)
    line = [[x_min, intercept + slope * x_min], [x_max, intercept + slope * x_max]]
    strength = abs(corr)
    if strength < 0.1:
        diagnosis = "no meaningful correlation"
    elif strength < 0.3:
        diagnosis = "weak correlation"
    elif strength < 0.6:
        diagnosis = "moderate correlation"
    else:
        diagnosis = "strong correlation"

    return {
        "slope": slope,
        "intercept": intercept,
        "r2": r2,
        "correlation": corr,
        "line": line,
        "diagnosis": diagnosis,
    }


def read_csv_dicts(path: Path, delimiter: str = ",") -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def resolve_round3_actual_dir(data_root: Optional[Path]) -> Optional[Path]:
    candidates: list[Path] = []
    if data_root is not None:
        candidates.extend([data_root, data_root / "round3"])
    candidates.extend(
        [
            project_root() / "data" / "round3",
            project_root().parent.parent / "phase2" / "round3" / "data",
            project_root().parent.parent / "phase2" / "round3" / "algo" / "data",
        ]
    )

    for candidate in candidates:
        if (candidate / "prices_round_3_combined.csv").is_file() or (candidate / "prices_round_3_day_0.csv").is_file():
            return candidate
    return None


def is_round3_data_root(data_root: Optional[Path]) -> bool:
    return resolve_round3_actual_dir(data_root) is not None


def read_round3_rows(actual_dir: Path, kind: str) -> list[dict[str, str]]:
    combined = actual_dir / f"{kind}_round_3_combined.csv"
    if combined.is_file():
        return read_csv_dicts(combined, ";")

    rows: list[dict[str, str]] = []
    for day in [0, 1, 2]:
        path = actual_dir / f"{kind}_round_3_day_{day}.csv"
        if path.is_file():
            rows.extend(read_csv_dicts(path, ";"))
    if not rows:
        raise FileNotFoundError(f"No Round 3 {kind} CSVs found in {actual_dir}")
    return rows


def row_day(row: dict[str, str], fallback: int = 0) -> int:
    value = row.get("day", "")
    if value not in ("", None):
        return int(value)

    trailing = next(reversed(row.values()))
    if "," in trailing:
        maybe_day = trailing.rsplit(",", 1)[-1]
        if maybe_day:
            return int(maybe_day)
    return fallback


def cleaned_number(value: str, default: str = "0") -> str:
    if value in ("", None):
        return ""
    return str(value).split(",", 1)[0] or default


def product_mid(row: dict[str, str]) -> float:
    return float(cleaned_number(row["mid_price"]))


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def round3_stress_profile(name: str) -> dict[str, float | str]:
    profiles: dict[str, dict[str, float | str]] = {
        "none": {
            "name": "none",
            "spreadMultiplier": 1.0,
            "depthMultiplier": 1.0,
            "tradeKeepProbability": 1.0,
            "spotShiftStd": 0.0,
            "surfaceShiftStd": 0.0,
            "surfaceTiltStd": 0.0,
        },
        "conservative": {
            "name": "conservative",
            "spreadMultiplier": 1.15,
            "depthMultiplier": 0.85,
            "tradeKeepProbability": 0.85,
            "spotShiftStd": 3.0,
            "surfaceShiftStd": 2.0,
            "surfaceTiltStd": 1.0,
        },
        "adverse": {
            "name": "adverse",
            "spreadMultiplier": 1.35,
            "depthMultiplier": 0.65,
            "tradeKeepProbability": 0.65,
            "spotShiftStd": 6.0,
            "surfaceShiftStd": 5.0,
            "surfaceTiltStd": 2.5,
        },
    }
    if name not in profiles:
        valid = ", ".join(sorted(profiles))
        raise ValueError(f"Unknown Round 3 stress profile {name!r}; expected one of: {valid}")
    return dict(profiles[name])


def round3_stress_config(
    profile: str,
    spread_multiplier: Optional[float] = None,
    depth_multiplier: Optional[float] = None,
    trade_keep_probability: Optional[float] = None,
    spot_shift_std: Optional[float] = None,
    surface_shift_std: Optional[float] = None,
    surface_tilt_std: Optional[float] = None,
) -> dict[str, float | str]:
    config = round3_stress_profile(profile)
    overrides = {
        "spreadMultiplier": spread_multiplier,
        "depthMultiplier": depth_multiplier,
        "tradeKeepProbability": trade_keep_probability,
        "spotShiftStd": spot_shift_std,
        "surfaceShiftStd": surface_shift_std,
        "surfaceTiltStd": surface_tilt_std,
    }
    for key, value in overrides.items():
        if value is not None:
            config[key] = value
    config["spreadMultiplier"] = max(1.0, float(config["spreadMultiplier"]))
    config["depthMultiplier"] = clamp(float(config["depthMultiplier"]), 0.05, 2.0)
    config["tradeKeepProbability"] = clamp(float(config["tradeKeepProbability"]), 0.0, 1.0)
    config["spotShiftStd"] = max(0.0, float(config["spotShiftStd"]))
    config["surfaceShiftStd"] = max(0.0, float(config["surfaceShiftStd"]))
    config["surfaceTiltStd"] = max(0.0, float(config["surfaceTiltStd"]))
    return config


def vev_strike(product: str) -> Optional[int]:
    if not product.startswith("VEV_"):
        return None
    try:
        return int(product.rsplit("_", 1)[1])
    except ValueError:
        return None


def calibrate_round3_model(actual_dir: Path) -> dict[str, Any]:
    price_rows = read_round3_rows(actual_dir, "prices")
    trade_rows = read_round3_rows(actual_dir, "trades")
    products = sorted({row["product"] for row in price_rows})

    prices_by_day_ts: dict[int, dict[int, dict[str, dict[str, str]]]] = {}
    timestamps_by_day: dict[int, list[int]] = {}
    mids_by_product: dict[str, list[float]] = {product: [] for product in products}
    returns_by_product: dict[str, list[float]] = {product: [] for product in products}
    previous_mid: dict[tuple[int, str], float] = {}

    for row in price_rows:
        day = row_day(row)
        timestamp = int(row["timestamp"])
        product = row["product"]
        prices_by_day_ts.setdefault(day, {}).setdefault(timestamp, {})[product] = row
        mid = product_mid(row)
        mids_by_product[product].append(mid)
        key = (day, product)
        if key in previous_mid:
            returns_by_product[product].append(mid - previous_mid[key])
        previous_mid[key] = mid

    for day, by_ts in prices_by_day_ts.items():
        timestamps_by_day[day] = sorted(ts for ts, product_rows in by_ts.items() if all(p in product_rows for p in products))

    trades_by_day_ts: dict[int, dict[int, list[dict[str, str]]]] = {}
    for row in trade_rows:
        day = row_day(row)
        trades_by_day_ts.setdefault(day, {}).setdefault(int(row["timestamp"]), []).append(row)

    product_stats = {}
    for product in products:
        mids = mids_by_product[product]
        increments = returns_by_product[product]
        product_stats[product] = {
            "meanMid": mean(mids),
            "stdMid": sample_std(mids),
            "stdStep": sample_std(increments),
            "kind": "spot" if product in ROUND3_SPOT_PRODUCTS else "voucher",
        }

    return {
        "products": products,
        "pricesByDayTs": prices_by_day_ts,
        "timestampsByDay": timestamps_by_day,
        "tradesByDayTs": trades_by_day_ts,
        "productStats": product_stats,
    }


def numeric_price(value: str, shift: int = 0) -> str:
    if value in ("", None):
        return ""
    adjusted = int(round(float(cleaned_number(value)) + shift))
    return str(max(0, adjusted))


def adjusted_volume(value: str, depth_multiplier: float) -> str:
    if value in ("", None):
        return ""
    volume = int(float(cleaned_number(value)))
    if volume == 0:
        return "0"
    stressed = int(round(abs(volume) * depth_multiplier))
    stressed = max(1, stressed)
    return str(stressed if volume > 0 else -stressed)


def stressed_price(value: str, mid: float, shift: int, side: str, spread_multiplier: float) -> str:
    if value in ("", None):
        return ""
    raw = float(cleaned_number(value)) + shift
    shifted_mid = mid + shift
    distance = abs(raw - shifted_mid)
    if side == "bid":
        adjusted = shifted_mid - distance * spread_multiplier
        return str(max(0, int(math.floor(adjusted))))
    adjusted = shifted_mid + distance * spread_multiplier
    return str(max(0, int(math.ceil(adjusted))))


def shifted_price_row(
    source: dict[str, str],
    day: int,
    timestamp: int,
    shift: int,
    stress_config: Optional[dict[str, float | str]] = None,
) -> dict[str, str]:
    row = dict(source)
    row["day"] = str(day)
    row["timestamp"] = str(timestamp)
    stress_config = stress_config or round3_stress_profile("none")
    spread_multiplier = float(stress_config["spreadMultiplier"])
    depth_multiplier = float(stress_config["depthMultiplier"])
    mid = float(cleaned_number(source["mid_price"]))
    for key in ["bid_price_1", "bid_price_2", "bid_price_3"]:
        row[key] = stressed_price(row.get(key, ""), mid, shift, "bid", spread_multiplier)
    for key in ["ask_price_1", "ask_price_2", "ask_price_3"]:
        row[key] = stressed_price(row.get(key, ""), mid, shift, "ask", spread_multiplier)
    for key in ["bid_volume_1", "bid_volume_2", "bid_volume_3", "ask_volume_1", "ask_volume_2", "ask_volume_3"]:
        if row.get(key, "") not in ("", None):
            row[key] = adjusted_volume(row[key], depth_multiplier)
    row["mid_price"] = str(max(0.0, float(cleaned_number(row["mid_price"])) + shift))
    row["profit_and_loss"] = "0.0"
    return row


def round3_session_shifts(
    products: list[str],
    product_stats: dict[str, Any],
    rng: random.Random,
    shift_multiplier: float,
    stress_config: dict[str, float | str],
) -> dict[str, int]:
    spot_shift = rng.gauss(0.0, float(stress_config["spotShiftStd"]))
    surface_shift = rng.gauss(0.0, float(stress_config["surfaceShiftStd"]))
    surface_tilt = rng.gauss(0.0, float(stress_config["surfaceTiltStd"]))
    shifts: dict[str, int] = {}
    for product in products:
        base_shift = rng.gauss(0.0, max(0.5, product_stats[product]["stdStep"]) * shift_multiplier)
        if product == "VELVETFRUIT_EXTRACT":
            stress_shift = spot_shift
        elif product == "HYDROGEL_PACK":
            stress_shift = rng.gauss(0.0, float(stress_config["spotShiftStd"]) * 0.5)
        elif product in ROUND3_OPTION_PRODUCTS:
            strike = vev_strike(product) or 5250
            moneyness = clamp((5250 - strike) / 1250.0, -1.0, 1.0)
            delta_like = clamp(0.55 + moneyness * 0.35, 0.05, 0.95)
            stress_shift = surface_shift + delta_like * spot_shift + moneyness * surface_tilt
        else:
            stress_shift = 0.0
        shifts[product] = int(round(base_shift + stress_shift))
    return shifts


def synthetic_round3_session(
    model: dict[str, Any],
    session_dir: Path,
    rng: random.Random,
    ticks_per_day: int,
    output_days: Optional[list[int]] = None,
    shift_multiplier: float = 0.0,
    stress_config: Optional[dict[str, float | str]] = None,
) -> None:
    round_dir = session_dir / "round3"
    round_dir.mkdir(parents=True, exist_ok=True)
    products = model["products"]
    day_values = sorted(model["timestampsByDay"])
    if output_days is None:
        output_days = [2]
    stress_config = stress_config or round3_stress_profile("none")
    configured_block_len = int(os.environ.get("PROSPERITY4MCBT_R3_BLOCK_LEN", "0"))

    price_fields = [
        "day",
        "timestamp",
        "product",
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
    trade_fields = ["timestamp", "buyer", "seller", "symbol", "currency", "price", "quantity"]

    product_shifts = round3_session_shifts(
        products,
        model["productStats"],
        rng,
        shift_multiplier,
        stress_config,
    )
    trade_keep_probability = float(stress_config["tradeKeepProbability"])

    for output_day in output_days:
        source_day = rng.choice(day_values)
        source_timestamps = model["timestampsByDay"][source_day]
        target_len = min(ticks_per_day, len(source_timestamps))
        block_len = configured_block_len if configured_block_len > 0 else target_len
        block_len = max(1, min(block_len, len(source_timestamps)))
        sampled_ts: list[int] = []
        while len(sampled_ts) < target_len:
            max_start = max(0, len(source_timestamps) - block_len)
            start = rng.randint(0, max_start) if max_start > 0 else 0
            sampled_ts.extend(source_timestamps[start : start + block_len])
        sampled_ts = sampled_ts[:target_len]

        with (round_dir / f"prices_round_3_day_{output_day}.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=price_fields, delimiter=";")
            writer.writeheader()
            for index, source_ts in enumerate(sampled_ts):
                timestamp = index * 100
                source_rows = model["pricesByDayTs"][source_day][source_ts]
                for product in products:
                    writer.writerow(
                        shifted_price_row(
                            source_rows[product],
                            output_day,
                            timestamp,
                            product_shifts[product],
                            stress_config,
                        )
                    )

        with (round_dir / f"trades_round_3_day_{output_day}.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=trade_fields, delimiter=";")
            writer.writeheader()
            for index, source_ts in enumerate(sampled_ts):
                timestamp = index * 100
                for trade in model["tradesByDayTs"].get(source_day, {}).get(source_ts, []):
                    if rng.random() > trade_keep_probability:
                        continue
                    row = {field: trade.get(field, "") for field in trade_fields}
                    row["timestamp"] = str(timestamp)
                    row["price"] = numeric_price(row["price"], product_shifts.get(row["symbol"], 0))
                    row["quantity"] = cleaned_number(row["quantity"], "0")
                    row["currency"] = row["currency"] or "XIRECS"
                    writer.writerow(row)


def parse_algorithm_module(algorithm: Path) -> Any:
    import sys

    sys.path.append(str(algorithm.parent))
    module = import_module(algorithm.stem)
    return reload(module)


def final_product_pnl(result: BacktestResult) -> dict[str, float]:
    if not result.activity_logs:
        return {}
    last_timestamp = result.activity_logs[-1].timestamp
    values = {}
    for row in reversed(result.activity_logs):
        if row.timestamp != last_timestamp:
            break
        values[row.columns[2]] = float(row.columns[-1])
    return values


def activity_path(result: BacktestResult, timestamp_offset: int = 0) -> dict[str, dict[str, list[float]]]:
    paths: dict[str, dict[str, list[float]]] = {}
    for row in result.activity_logs:
        product = row.columns[2]
        node = paths.setdefault(
            product,
            {"timestamps": [], "fair": [], "mid": [], "bid1": [], "ask1": [], "position": [], "cash": [], "mtmPnl": []},
        )
        node["timestamps"].append(float(timestamp_offset + row.timestamp))
        node["fair"].append(float(row.columns[15]))
        node["mid"].append(float(row.columns[15]))
        node["bid1"].append(float(row.columns[3]) if row.columns[3] != "" else math.nan)
        node["ask1"].append(float(row.columns[9]) if row.columns[9] != "" else math.nan)
        node["position"].append(0.0)
        node["cash"].append(0.0)
        node["mtmPnl"].append(float(row.columns[-1]))
    return paths


def fitted_path_stats(values: list[float]) -> tuple[float, float]:
    if len(values) < 2:
        return 0.0, 0.0
    x_values = [float(index) for index in range(len(values))]
    fit = linear_regression(x_values, values)
    return float(fit["slope"]), float(fit["r2"])


def downsample_indices(length: int, max_points: int) -> list[int]:
    if length <= max_points:
        return list(range(length))

    if max_points <= 1:
        return [length - 1]

    indices = [min(round(i * (length - 1) / (max_points - 1)), length - 1) for i in range(max_points)]
    deduped: list[int] = []
    seen: set[int] = set()
    for index in indices:
        if index not in seen:
            deduped.append(index)
            seen.add(index)
    if deduped[-1] != length - 1:
        deduped[-1] = length - 1
    return deduped


def downsample_path_node(node: dict[str, list[float] | list[int]], max_points: int) -> dict[str, list[float] | list[int]]:
    indices = downsample_indices(len(node["timestamps"]), max_points)
    return {key: [values[index] for index in indices] for key, values in node.items()}


def svg_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )



def load_session_summaries(output_dir: Path) -> list[dict[str, Any]]:
    rows = read_csv_dicts(output_dir / "session_summary.csv", ",")
    parsed = []
    for row in rows:
        parsed.append(
            {
                "sessionId": int(row["session_id"]),
                "totalPnl": float(row["total_pnl"]),
                "ashPnl": float(row["ash_coated_osmium_pnl"]),
                "pepperPnl": float(row["intarian_pepper_root_pnl"]),
                "ashPosition": int(row["ash_coated_osmium_position"]),
                "pepperPosition": int(row["intarian_pepper_root_position"]),
                "ashCash": float(row["ash_coated_osmium_cash"]),
                "pepperCash": float(row["intarian_pepper_root_cash"]),
                "totalSlopePerStep": float(row.get("total_slope_per_step", 0.0) or 0.0),
                "totalR2": float(row.get("total_r2", 0.0) or 0.0),
                "ashSlopePerStep": float(row.get("ash_coated_osmium_slope_per_step", 0.0) or 0.0),
                "ashR2": float(row.get("ash_coated_osmium_r2", 0.0) or 0.0),
                "pepperSlopePerStep": float(row.get("intarian_pepper_root_slope_per_step", 0.0) or 0.0),
                "pepperR2": float(row.get("intarian_pepper_root_r2", 0.0) or 0.0),
            }
        )
    return parsed


def load_run_summaries(output_dir: Path) -> list[dict[str, Any]]:
    rows = read_csv_dicts(output_dir / "run_summary.csv", ",")
    parsed = []
    for row in rows:
        parsed.append(
            {
                "sessionId": int(row["session_id"]),
                "day": int(row["day"]),
                "totalPnl": float(row["total_pnl"]),
                "ashPnl": float(row["ash_coated_osmium_pnl"]),
                "pepperPnl": float(row["intarian_pepper_root_pnl"]),
                "totalSlopePerStep": float(row.get("total_slope_per_step", 0.0) or 0.0),
                "totalR2": float(row.get("total_r2", 0.0) or 0.0),
                "ashSlopePerStep": float(row.get("ash_coated_osmium_slope_per_step", 0.0) or 0.0),
                "ashR2": float(row.get("ash_coated_osmium_r2", 0.0) or 0.0),
                "pepperSlopePerStep": float(row.get("intarian_pepper_root_slope_per_step", 0.0) or 0.0),
                "pepperR2": float(row.get("intarian_pepper_root_r2", 0.0) or 0.0),
            }
        )
    return parsed


def load_sample_session(session_dir: Path) -> dict[str, Any]:
    round_dir = session_dir / "round1"
    traces_by_product: dict[str, dict[str, list[float]]] = {}
    prices_by_product: dict[str, dict[str, list[float]]] = {}
    day_files = sorted(
        int(path.stem.split("_")[-1])
        for path in round_dir.glob("trace_round_1_day_*.csv")
    )

    for day_index, day in enumerate(day_files):
        trace_rows = read_csv_dicts(round_dir / f"trace_round_1_day_{day}.csv", ";")
        price_rows = read_csv_dicts(round_dir / f"prices_round_1_day_{day}.csv", ";")

        for row in trace_rows:
            product = row["product"]
            if product not in traces_by_product:
                traces_by_product[product] = {
                    "timestamps": [],
                    "fair": [],
                    "position": [],
                    "cash": [],
                    "mtmPnl": [],
                }
            ts = day_index * 1_000_000 + int(row["timestamp"])
            traces_by_product[product]["timestamps"].append(ts)
            traces_by_product[product]["fair"].append(float(row["fair_value"]))
            traces_by_product[product]["position"].append(int(row["position"]))
            traces_by_product[product]["cash"].append(float(row["cash"]))
            traces_by_product[product]["mtmPnl"].append(float(row["mtm_pnl"]))

        for row in price_rows:
            product = row["product"]
            if product not in prices_by_product:
                prices_by_product[product] = {
                    "timestamps": [],
                    "mid": [],
                    "bid1": [],
                    "ask1": [],
                }
            ts = day_index * 1_000_000 + int(row["timestamp"])
            prices_by_product[product]["timestamps"].append(ts)
            prices_by_product[product]["mid"].append(float(row["mid_price"]))
            prices_by_product[product]["bid1"].append(
                float(row["bid_price_1"]) if row["bid_price_1"] not in ("", None) else math.nan
            )
            prices_by_product[product]["ask1"].append(
                float(row["ask_price_1"]) if row["ask_price_1"] not in ("", None) else math.nan
            )

    products = {}
    for product, trace in traces_by_product.items():
        price = prices_by_product.get(product, {"mid": [], "bid1": [], "ask1": []})
        products[product] = {
            "timestamps": trace["timestamps"],
            "fair": trace["fair"],
            "mid": price["mid"],
            "bid1": price["bid1"],
            "ask1": price["ask1"],
            "position": trace["position"],
            "cash": trace["cash"],
            "mtmPnl": trace["mtmPnl"],
        }

    timestamps = products[PRODUCT_A]["timestamps"]
    total_pnl = []
    for idx in range(len(timestamps)):
        total_pnl.append(sum(products[product]["mtmPnl"][idx] for product in products))

    return {
        "sessionId": int(session_dir.name.split("_")[-1]),
        "products": products,
        "total": {
            "timestamps": timestamps,
            "mtmPnl": total_pnl,
        },
    }


def sampled_chart_path(sample: dict[str, Any]) -> dict[str, Any]:
    return {
        "sessionId": sample["sessionId"],
        "products": {
            product: downsample_path_node(node, CHART_POINTS_PER_SERIES)
            for product, node in sample["products"].items()
        },
        "total": downsample_path_node(sample["total"], CHART_POINTS_PER_SERIES),
    }


def write_sample_path_sidecars(output_dir: Path, sample_session_dirs: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    refs: list[dict[str, Any]] = []
    sampled_paths: list[dict[str, Any]] = []
    sidecar_dir = output_dir / "sample_paths"
    sidecar_dir.mkdir(parents=True, exist_ok=True)

    for session_dir in sample_session_dirs:
        sample = load_sample_session(session_dir)
        sampled = sampled_chart_path(sample)
        sampled_paths.append(sampled)
        relative_path = Path("sample_paths") / f"{session_dir.name}.json"
        sidecar_path = output_dir / relative_path
        with sidecar_path.open("w", encoding="utf-8") as handle:
            json.dump(sampled, handle, separators=(",", ":"))
        refs.append(
            {
                "sessionId": sampled["sessionId"],
                "url": relative_path.as_posix(),
            }
        )

    return refs, sampled_paths


def quantile_series(sample_paths: list[dict[str, Any]], value_getter) -> dict[str, list[float]]:
    if not sample_paths:
        return {}

    base_values = value_getter(sample_paths[0])
    indices = downsample_indices(len(base_values), STATIC_CHART_POINTS)
    timestamps = [sample_paths[0]["total"]["timestamps"][index] for index in indices]

    p05: list[float] = []
    p25: list[float] = []
    p50: list[float] = []
    p75: list[float] = []
    p95: list[float] = []
    mean: list[float] = []

    for index in indices:
        values = [value_getter(path)[index] for path in sample_paths]
        p05.append(quantile(values, 0.05))
        p25.append(quantile(values, 0.25))
        p50.append(quantile(values, 0.50))
        p75.append(quantile(values, 0.75))
        p95.append(quantile(values, 0.95))
        mean.append(statistics.fmean(values))

    return {
        "timestamps": timestamps,
        "p05": p05,
        "p25": p25,
        "p50": p50,
        "p75": p75,
        "p95": p95,
        "mean": mean,
    }


def mean_std_band_series(sample_paths: list[dict[str, Any]], value_getter) -> dict[str, list[float]]:
    if not sample_paths:
        return {}

    base_values = value_getter(sample_paths[0])
    indices = downsample_indices(len(base_values), STATIC_CHART_POINTS)
    timestamps = [sample_paths[0]["total"]["timestamps"][index] for index in indices]

    mean_values: list[float] = []
    std1_low: list[float] = []
    std1_high: list[float] = []
    std3_low: list[float] = []
    std3_high: list[float] = []

    for index in indices:
        values = [value_getter(path)[index] for path in sample_paths]
        mu = statistics.fmean(values)
        sigma = sample_std(values)
        mean_values.append(mu)
        std1_low.append(mu - sigma)
        std1_high.append(mu + sigma)
        std3_low.append(mu - 3.0 * sigma)
        std3_high.append(mu + 3.0 * sigma)

    return {
        "timestamps": timestamps,
        "mean": mean_values,
        "std1Low": std1_low,
        "std1High": std1_high,
        "std3Low": std3_low,
        "std3High": std3_high,
    }


def overlay_series(sample_paths: list[dict[str, Any]], value_getter, overlay_count: int = 10) -> dict[str, Any]:
    overlays = []
    for path in sample_paths[:overlay_count]:
        values = value_getter(path)
        indices = downsample_indices(len(values), STATIC_CHART_POINTS)
        overlays.append(
            {
                "sessionId": path["sessionId"],
                "timestamps": [path["total"]["timestamps"][index] for index in indices],
                "values": [values[index] for index in indices],
            }
        )
    return {"overlays": overlays}


def path_chart_svg(
    title: str,
    subtitle: str,
    timestamps: list[float],
    bands: dict[str, list[float]],
    overlays: list[dict[str, Any]] | None = None,
) -> str:
    width = 1200
    height = 420
    left = 64
    right = 24
    top = 56
    bottom = 36
    plot_width = width - left - right
    plot_height = height - top - bottom

    y_values = bands["p05"] + bands["p95"] + bands["mean"]
    if overlays:
        for overlay in overlays:
            y_values.extend(overlay["values"])
    y_min = min(y_values)
    y_max = max(y_values)
    if y_min == y_max:
        y_min -= 1.0
        y_max += 1.0

    x_min = timestamps[0]
    x_max = timestamps[-1]
    x_range = x_max - x_min if x_max != x_min else 1.0
    y_range = y_max - y_min

    def x_pos(ts: float) -> float:
        return left + (ts - x_min) / x_range * plot_width

    def y_pos(value: float) -> float:
        return top + (1.0 - (value - y_min) / y_range) * plot_height

    def polyline(ts_values: list[float], values: list[float]) -> str:
        return " ".join(f"{x_pos(ts):.2f},{y_pos(value):.2f}" for ts, value in zip(ts_values, values))

    def band_polygon(lower: list[float], upper: list[float]) -> str:
        forward = [f"{x_pos(ts):.2f},{y_pos(value):.2f}" for ts, value in zip(timestamps, upper)]
        backward = [f"{x_pos(ts):.2f},{y_pos(value):.2f}" for ts, value in zip(reversed(timestamps), reversed(lower))]
        return " ".join(forward + backward)

    tick_labels = [timestamps[0], timestamps[len(timestamps) // 2], timestamps[-1]]
    y_ticks = [y_min, (y_min + y_max) / 2.0, y_max]

    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#101113"/>',
        f'<text x="{left}" y="28" fill="#f3f4f6" font-size="22" font-family="system-ui, sans-serif">{svg_escape(title)}</text>',
        f'<text x="{left}" y="46" fill="#9ca3af" font-size="13" font-family="system-ui, sans-serif">{svg_escape(subtitle)}</text>',
        f'<rect x="{left}" y="{top}" width="{plot_width}" height="{plot_height}" fill="#141517" stroke="#2c2e33"/>',
    ]

    for tick in y_ticks:
        y = y_pos(tick)
        svg_parts.append(f'<line x1="{left}" y1="{y:.2f}" x2="{left + plot_width}" y2="{y:.2f}" stroke="#25262b" stroke-width="1"/>')
        svg_parts.append(
            f'<text x="{left - 10}" y="{y + 4:.2f}" fill="#9ca3af" font-size="12" text-anchor="end" font-family="system-ui, sans-serif">{tick:.2f}</text>'
        )

    for tick in tick_labels:
        x = x_pos(tick)
        svg_parts.append(f'<line x1="{x:.2f}" y1="{top}" x2="{x:.2f}" y2="{top + plot_height}" stroke="#25262b" stroke-width="1"/>')
        svg_parts.append(
            f'<text x="{x:.2f}" y="{top + plot_height + 18}" fill="#9ca3af" font-size="12" text-anchor="middle" font-family="system-ui, sans-serif">{int(tick)}</text>'
        )

    svg_parts.append(f'<polygon points="{band_polygon(bands["p05"], bands["p95"])}" fill="#60a5fa" opacity="0.18"/>')
    svg_parts.append(f'<polygon points="{band_polygon(bands["p25"], bands["p75"])}" fill="#3b82f6" opacity="0.28"/>')
    svg_parts.append(f'<polyline points="{polyline(timestamps, bands["p50"])}" fill="none" stroke="#f8fafc" stroke-width="2"/>')
    svg_parts.append(f'<polyline points="{polyline(timestamps, bands["mean"])}" fill="none" stroke="#f59e0b" stroke-width="2" stroke-dasharray="6 4"/>')

    if overlays:
        for overlay in overlays:
            svg_parts.append(
                f'<polyline points="{polyline(overlay["timestamps"], overlay["values"])}" fill="none" stroke="#34d399" stroke-width="1.1" opacity="0.24"/>'
            )

    legend_x = left + 12
    legend_y = top + 18
    svg_parts.extend(
        [
            f'<rect x="{legend_x}" y="{legend_y - 10}" width="16" height="10" fill="#60a5fa" opacity="0.18"/>',
            f'<text x="{legend_x + 22}" y="{legend_y}" fill="#d1d5db" font-size="12" font-family="system-ui, sans-serif">P05-P95</text>',
            f'<rect x="{legend_x + 96}" y="{legend_y - 10}" width="16" height="10" fill="#3b82f6" opacity="0.28"/>',
            f'<text x="{legend_x + 118}" y="{legend_y}" fill="#d1d5db" font-size="12" font-family="system-ui, sans-serif">P25-P75</text>',
            f'<line x1="{legend_x + 194}" y1="{legend_y - 5}" x2="{legend_x + 210}" y2="{legend_y - 5}" stroke="#f8fafc" stroke-width="2"/>',
            f'<text x="{legend_x + 216}" y="{legend_y}" fill="#d1d5db" font-size="12" font-family="system-ui, sans-serif">Median</text>',
            f'<line x1="{legend_x + 278}" y1="{legend_y - 5}" x2="{legend_x + 294}" y2="{legend_y - 5}" stroke="#f59e0b" stroke-width="2" stroke-dasharray="6 4"/>',
            f'<text x="{legend_x + 300}" y="{legend_y}" fill="#d1d5db" font-size="12" font-family="system-ui, sans-serif">Mean</text>',
        ]
    )

    svg_parts.append("</svg>")
    return "".join(svg_parts)


def write_static_chart_svgs(output_dir: Path, sampled_paths: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    if not sampled_paths:
        return {}

    charts_dir = output_dir / "static_charts"
    charts_dir.mkdir(parents=True, exist_ok=True)

    chart_specs = {
        product: [
            ("fair_bands", "Fair Price Bands", lambda path, p=product: path["products"][p]["fair"]),
            ("mtm_bands", "MTM PnL Bands", lambda path, p=product: path["products"][p]["mtmPnl"]),
            ("position_bands", "Position Bands", lambda path, p=product: path["products"][p]["position"]),
        ]
        for product in PRODUCTS
    }

    refs: dict[str, list[dict[str, str]]] = {}
    for product, specs in chart_specs.items():
        product_refs: list[dict[str, str]] = []
        product_dir = charts_dir / product.lower()
        product_dir.mkdir(parents=True, exist_ok=True)
        for slug, title, getter in specs:
            bands = quantile_series(sampled_paths, getter)
            overlays = overlay_series(sampled_paths, getter)["overlays"]
            svg = path_chart_svg(
                title=f"{product} {title}",
                subtitle=f"{len(sampled_paths)} persisted session traces • overlays show first {min(10, len(overlays))} sessions",
                timestamps=bands["timestamps"],
                bands=bands,
                overlays=overlays,
            )
            relative_path = Path("static_charts") / product.lower() / f"{slug}.svg"
            chart_path = output_dir / relative_path
            chart_path.write_text(svg, encoding="utf-8")
            product_refs.append({"title": title, "url": relative_path.as_posix()})
        refs[product] = product_refs

    return refs


def build_band_series(sampled_paths: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, list[float]]]]:
    if not sampled_paths:
        return {}

    return {
        product: {
            "fair": mean_std_band_series(sampled_paths, lambda path, p=product: path["products"][p]["fair"]),
            "mtmPnl": mean_std_band_series(sampled_paths, lambda path, p=product: path["products"][p]["mtmPnl"]),
            "position": mean_std_band_series(sampled_paths, lambda path, p=product: path["products"][p]["position"]),
        }
        for product in PRODUCTS
    }


def build_dashboard(output_dir: Path, algorithm: Path, sessions: int, config: dict[str, Any]) -> dict[str, Any]:
    session_rows = load_session_summaries(output_dir)
    run_rows = load_run_summaries(output_dir)
    total = [row["totalPnl"] for row in session_rows]
    ash = [row["ashPnl"] for row in session_rows]
    pepper = [row["pepperPnl"] for row in session_rows]
    ash_pos = [row["ashPosition"] for row in session_rows]
    pepper_pos = [row["pepperPosition"] for row in session_rows]
    ash_cash = [row["ashCash"] for row in session_rows]
    pepper_cash = [row["pepperCash"] for row in session_rows]
    total_profitability = [row["totalSlopePerStep"] for row in run_rows]
    total_stability = [row["totalR2"] for row in run_rows]
    ash_profitability = [row["ashSlopePerStep"] for row in run_rows]
    ash_stability = [row["ashR2"] for row in run_rows]
    pepper_profitability = [row["pepperSlopePerStep"] for row in run_rows]
    pepper_stability = [row["pepperR2"] for row in run_rows]
    session_total_profitability = [row["totalSlopePerStep"] for row in session_rows]
    session_total_stability = [row["totalR2"] for row in session_rows]
    session_ash_profitability = [row["ashSlopePerStep"] for row in session_rows]
    session_ash_stability = [row["ashR2"] for row in session_rows]
    session_pepper_profitability = [row["pepperSlopePerStep"] for row in session_rows]
    session_pepper_stability = [row["pepperR2"] for row in session_rows]

    sample_session_dirs = sorted((output_dir / "sessions").glob("session_*")) if (output_dir / "sessions").exists() else []
    sample_path_refs, sampled_paths = write_sample_path_sidecars(output_dir, sample_session_dirs) if sample_session_dirs else ([], [])
    band_chart_refs = write_static_chart_svgs(output_dir, sampled_paths) if sampled_paths else {}
    band_series = build_band_series(sampled_paths) if sampled_paths else {}

    runs_by_session: dict[int, list[dict[str, Any]]] = {}
    for run in run_rows:
        runs_by_session.setdefault(run["sessionId"], []).append(run)
    for row in session_rows:
        session_runs = runs_by_session.get(row["sessionId"], [])
        if session_runs:
            row["runMeanTotalSlopePerStep"] = statistics.fmean(run["totalSlopePerStep"] for run in session_runs)
            row["runMeanTotalR2"] = statistics.fmean(run["totalR2"] for run in session_runs)
        else:
            row["runMeanTotalSlopePerStep"] = row["totalSlopePerStep"]
            row["runMeanTotalR2"] = row["totalR2"]

    top_sessions = sorted(session_rows, key=lambda row: row["totalPnl"], reverse=True)[:10]
    bottom_sessions = sorted(session_rows, key=lambda row: row["totalPnl"])[:10]
    scatter_fit = linear_regression(ash, pepper)
    total_normal_fit = normal_fit(total)
    ash_normal_fit = normal_fit(ash)
    pepper_normal_fit = normal_fit(pepper)

    return {
        "kind": "monte_carlo_dashboard",
        "meta": {
            "algorithmPath": str(algorithm),
            "sessionCount": sessions,
            "bandSessionCount": len(sample_session_dirs),
            "products": PRODUCTS,
            **config,
        },
        "overall": {
            "totalPnl": summarize_distribution(total),
            "ashPnl": summarize_distribution(ash),
            "pepperPnl": summarize_distribution(pepper),
            "ashPepperCorrelation": correlation(ash, pepper),
        },
        "trendFits": {
            "TOTAL": {
                "profitability": summarize_distribution(total_profitability),
                "stability": summarize_distribution(total_stability),
            },
            PRODUCT_A: {
                "profitability": summarize_distribution(ash_profitability),
                "stability": summarize_distribution(ash_stability),
            },
            PRODUCT_B: {
                "profitability": summarize_distribution(pepper_profitability),
                "stability": summarize_distribution(pepper_stability),
            },
        },
        "aggregateTrendFits": {
            "TOTAL": {
                "profitability": summarize_distribution(session_total_profitability),
                "stability": summarize_distribution(session_total_stability),
            },
            PRODUCT_A: {
                "profitability": summarize_distribution(session_ash_profitability),
                "stability": summarize_distribution(session_ash_stability),
            },
            PRODUCT_B: {
                "profitability": summarize_distribution(session_pepper_profitability),
                "stability": summarize_distribution(session_pepper_stability),
            },
        },
        "normalFits": {
            "totalPnl": total_normal_fit,
            "ashPnl": ash_normal_fit,
            "pepperPnl": pepper_normal_fit,
        },
        "scatterFit": scatter_fit,
        "generatorModel": {
            PRODUCT_A: {
                "name": "Calibrated Stitched Bootstrap",
                "formula": "B_t, F_t ~ stitched bootstrap(real Round 1 book states, fair-continuity anchored)",
                "notes": [
                    "ASH uses shorter continuity-aware blocks around a near-stationary anchor with empirical queue-ahead fills",
                    "Observed book shapes are re-anchored to stitched fair blocks instead of naively jumping between raw states",
                ],
            },
            PRODUCT_B: {
                "name": "Calibrated Stitched Bootstrap",
                "formula": "B_t, F_t ~ stitched bootstrap(real Round 1 book states, trend-aware block selection)",
                "notes": [
                    "PEPPER uses longer trend-aware blocks to preserve regime persistence and directional repricing",
                    "Trade events remain empirical tick-level arrivals and sizes rather than fabricated hidden bots",
                ],
            },
        },
        "products": {
            PRODUCT_A: {
                "pnl": summarize_distribution(ash),
                "finalPosition": summarize_distribution([float(value) for value in ash_pos]),
                "cash": summarize_distribution(ash_cash),
            },
            PRODUCT_B: {
                "pnl": summarize_distribution(pepper),
                "finalPosition": summarize_distribution([float(value) for value in pepper_pos]),
                "cash": summarize_distribution(pepper_cash),
            },
        },
        "histograms": {
            "totalPnl": histogram(total),
            "ashPnl": histogram(ash),
            "pepperPnl": histogram(pepper),
            "totalProfitability": histogram(total_profitability),
            "totalStability": histogram(total_stability),
            "ashProfitability": histogram(ash_profitability),
            "ashStability": histogram(ash_stability),
            "pepperProfitability": histogram(pepper_profitability),
            "pepperStability": histogram(pepper_stability),
        },
        "sessions": session_rows,
        "runs": run_rows,
        "topSessions": top_sessions,
        "bottomSessions": bottom_sessions,
        "samplePaths": [],
        "samplePathRefs": sample_path_refs,
        "bandChartRefs": band_chart_refs,
        "bandSeries": band_series,
    }


def write_round3_sample_sidecars(
    output_dir: Path, sample_paths: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sidecar_dir = output_dir / "sample_paths"
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    refs = []
    sampled = []
    for sample in sample_paths:
        compact = sampled_chart_path(sample)
        sampled.append(compact)
        relative_path = Path("sample_paths") / f"session_{sample['sessionId']}.json"
        with (output_dir / relative_path).open("w", encoding="utf-8") as handle:
            json.dump(compact, handle, separators=(",", ":"))
        refs.append({"sessionId": sample["sessionId"], "url": relative_path.as_posix()})
    return refs, sampled


def build_round3_dashboard(
    output_dir: Path,
    algorithm: Path,
    sessions: int,
    products: list[str],
    session_rows: list[dict[str, Any]],
    run_rows: list[dict[str, Any]],
    sample_paths: list[dict[str, Any]],
    product_stats: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    total = [row["totalPnl"] for row in session_rows]
    product_pnls = {product: [row["productPnl"].get(product, 0.0) for row in session_rows] for product in products}
    product_positions = {product: [row["productPosition"].get(product, 0.0) for row in session_rows] for product in products}
    product_cash = {product: [row["productCash"].get(product, 0.0) for row in session_rows] for product in products}
    total_profitability = [row["totalSlopePerStep"] for row in run_rows]
    total_stability = [row["totalR2"] for row in run_rows]

    product_profitability = {
        product: [row["productSlopePerStep"].get(product, 0.0) for row in run_rows] for product in products
    }
    product_stability = {product: [row["productR2"].get(product, 0.0) for row in run_rows] for product in products}

    sample_path_refs, sampled_paths = write_round3_sample_sidecars(output_dir, sample_paths)
    band_chart_refs = write_static_chart_svgs_for_products(output_dir, sampled_paths, products) if sampled_paths else {}
    band_series = build_band_series_for_products(sampled_paths, products) if sampled_paths else {}

    for row in session_rows:
        matching_runs = [run for run in run_rows if run["sessionId"] == row["sessionId"]]
        row["runMeanTotalSlopePerStep"] = statistics.fmean(run["totalSlopePerStep"] for run in matching_runs) if matching_runs else 0.0
        row["runMeanTotalR2"] = statistics.fmean(run["totalR2"] for run in matching_runs) if matching_runs else 0.0

    top_sessions = sorted(session_rows, key=lambda row: row["totalPnl"], reverse=True)[:10]
    bottom_sessions = sorted(session_rows, key=lambda row: row["totalPnl"])[:10]
    first_product = products[0]
    second_product = products[1] if len(products) > 1 else products[0]

    return {
        "kind": "monte_carlo_dashboard",
        "meta": {
            "algorithmPath": str(algorithm),
            "sessionCount": sessions,
            "bandSessionCount": len(sample_paths),
            "products": products,
            "round": 3,
            "model": "round3_block_bootstrap",
            **config,
        },
        "overall": {
            "totalPnl": summarize_distribution(total),
            "ashPnl": summarize_distribution(product_pnls[first_product]),
            "pepperPnl": summarize_distribution(product_pnls[second_product]),
            "ashPepperCorrelation": correlation(product_pnls[first_product], product_pnls[second_product]),
            "productPnl": {product: summarize_distribution(values) for product, values in product_pnls.items()},
        },
        "trendFits": {
            "TOTAL": {
                "profitability": summarize_distribution(total_profitability),
                "stability": summarize_distribution(total_stability),
            },
            **{
                product: {
                    "profitability": summarize_distribution(product_profitability[product]),
                    "stability": summarize_distribution(product_stability[product]),
                }
                for product in products
            },
        },
        "normalFits": {
            "totalPnl": normal_fit(total),
            "ashPnl": normal_fit(product_pnls[first_product]),
            "pepperPnl": normal_fit(product_pnls[second_product]),
            "productPnl": {product: normal_fit(values) for product, values in product_pnls.items()},
        },
        "scatterFit": linear_regression(product_pnls[first_product], product_pnls[second_product]),
        "generatorModel": {
            product: {
                "name": "Round 3 Block Bootstrap",
                "formula": "Synchronized timestamp blocks sampled from real Round 3 order books; trade arrivals replayed from sampled source ticks",
                "notes": [
                    f"{product_stats[product]['kind']} product; mean mid {product_stats[product]['meanMid']:.2f}, step std {product_stats[product]['stdStep']:.3f}",
                    "All products are sampled on the same timestamp blocks to preserve cross-product book state dependence.",
                ],
            }
            for product in products
        },
        "products": {
            product: {
                "pnl": summarize_distribution(product_pnls[product]),
                "finalPosition": summarize_distribution([float(value) for value in product_positions[product]]),
                "cash": summarize_distribution(product_cash[product]),
            }
            for product in products
        },
        "histograms": {
            "totalPnl": histogram(total),
            "ashPnl": histogram(product_pnls[first_product]),
            "pepperPnl": histogram(product_pnls[second_product]),
            "totalProfitability": histogram(total_profitability),
            "totalStability": histogram(total_stability),
            "productPnl": {product: histogram(values) for product, values in product_pnls.items()},
            "productProfitability": {product: histogram(values) for product, values in product_profitability.items()},
            "productStability": {product: histogram(values) for product, values in product_stability.items()},
        },
        "sessions": session_rows,
        "runs": run_rows,
        "topSessions": top_sessions,
        "bottomSessions": bottom_sessions,
        "samplePaths": [],
        "samplePathRefs": sample_path_refs,
        "bandChartRefs": band_chart_refs,
        "bandSeries": band_series,
    }


def write_static_chart_svgs_for_products(
    output_dir: Path, sampled_paths: list[dict[str, Any]], products: list[str]
) -> dict[str, list[dict[str, str]]]:
    if not sampled_paths:
        return {}

    charts_dir = output_dir / "static_charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    refs: dict[str, list[dict[str, str]]] = {}
    for product in products:
        product_refs = []
        product_dir = charts_dir / product.lower()
        product_dir.mkdir(parents=True, exist_ok=True)
        for slug, title, getter in [
            ("fair_bands", "Fair Price Bands", lambda path, p=product: path["products"][p]["fair"]),
            ("mtm_bands", "MTM PnL Bands", lambda path, p=product: path["products"][p]["mtmPnl"]),
            ("position_bands", "Position Bands", lambda path, p=product: path["products"][p]["position"]),
        ]:
            bands = quantile_series(sampled_paths, getter)
            overlays = overlay_series(sampled_paths, getter)["overlays"]
            svg = path_chart_svg(
                title=f"{product} {title}",
                subtitle=f"{len(sampled_paths)} persisted session traces",
                timestamps=bands["timestamps"],
                bands=bands,
                overlays=overlays,
            )
            relative_path = Path("static_charts") / product.lower() / f"{slug}.svg"
            (output_dir / relative_path).write_text(svg, encoding="utf-8")
            product_refs.append({"title": title, "url": relative_path.as_posix()})
        refs[product] = product_refs
    return refs


def build_band_series_for_products(
    sampled_paths: list[dict[str, Any]], products: list[str]
) -> dict[str, dict[str, dict[str, list[float]]]]:
    if not sampled_paths:
        return {}

    return {
        product: {
            "fair": mean_std_band_series(sampled_paths, lambda path, p=product: path["products"][p]["fair"]),
            "mtmPnl": mean_std_band_series(sampled_paths, lambda path, p=product: path["products"][p]["mtmPnl"]),
            "position": mean_std_band_series(sampled_paths, lambda path, p=product: path["products"][p]["position"]),
        }
        for product in products
        if product in sampled_paths[0]["products"]
    }


def run_round3_python_monte_carlo(
    algorithm: Path,
    dashboard_path: Path,
    data_root: Optional[Path],
    sessions: int,
    seed: int,
    sample_sessions: int,
    ticks_per_day: int,
    fv_mode: str,
    trade_mode: str,
    tomato_support: str,
    r3_stress: str = "none",
    r3_spread_multiplier: Optional[float] = None,
    r3_depth_multiplier: Optional[float] = None,
    r3_trade_keep_probability: Optional[float] = None,
    r3_spot_shift_std: Optional[float] = None,
    r3_surface_shift_std: Optional[float] = None,
    r3_surface_tilt_std: Optional[float] = None,
) -> dict[str, Any]:
    actual_dir = resolve_round3_actual_dir(data_root)
    if actual_dir is None:
        raise RuntimeError("Round 3 data directory was not found")

    output_dir = dashboard_path.parent
    model = calibrate_round3_model(actual_dir)
    products = model["products"]
    output_days = [2]
    shift_multiplier = float(os.environ.get("PROSPERITY4MCBT_R3_SHIFT_MULTIPLIER", "0.0"))
    stress_config = round3_stress_config(
        r3_stress,
        spread_multiplier=r3_spread_multiplier,
        depth_multiplier=r3_depth_multiplier,
        trade_keep_probability=r3_trade_keep_probability,
        spot_shift_std=r3_spot_shift_std,
        surface_shift_std=r3_surface_shift_std,
        surface_tilt_std=r3_surface_tilt_std,
    )
    rng = random.Random(seed)
    trader_module = parse_algorithm_module(algorithm)
    session_rows: list[dict[str, Any]] = []
    run_rows: list[dict[str, Any]] = []
    sample_paths: list[dict[str, Any]] = []

    sessions_dir = output_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    for session_id in range(sessions):
        session_dir = sessions_dir / f"session_{session_id}"
        synthetic_round3_session(
            model,
            session_dir,
            rng,
            ticks_per_day,
            output_days=output_days,
            shift_multiplier=shift_multiplier,
            stress_config=stress_config,
        )
        reader = FileSystemReader(session_dir)
        session_product_pnl = {product: 0.0 for product in products}
        session_product_paths = {
            product: {"timestamps": [], "fair": [], "mid": [], "bid1": [], "ask1": [], "position": [], "cash": [], "mtmPnl": []}
            for product in products
        }
        session_total_path = {"timestamps": [], "mtmPnl": []}

        for day in output_days:
            trader = trader_module.Trader()
            result = run_backtest(
                trader,
                reader,
                3,
                day,
                False,
                TradeMatchingMode.all,
                True,
                False,
            )
            product_pnl = final_product_pnl(result)
            day_total = sum(product_pnl.values())
            product_paths = activity_path(result, day * 1_000_000)
            total_series_by_timestamp: dict[float, float] = {}
            for product in products:
                session_product_pnl[product] += product_pnl.get(product, 0.0)
                node = session_product_paths[product]
                path_node = product_paths.get(product)
                if path_node is None:
                    continue
                for key in node:
                    node[key].extend(path_node[key])
                for timestamp, pnl in zip(path_node["timestamps"], path_node["mtmPnl"]):
                    total_series_by_timestamp[timestamp] = total_series_by_timestamp.get(timestamp, 0.0) + pnl

            total_values = [value for _, value in sorted(total_series_by_timestamp.items())]
            total_slope, total_r2 = fitted_path_stats(total_values)
            product_slopes = {
                product: fitted_path_stats(session_product_paths[product]["mtmPnl"])[0] for product in products
            }
            product_r2 = {product: fitted_path_stats(session_product_paths[product]["mtmPnl"])[1] for product in products}
            run_rows.append(
                {
                    "sessionId": session_id,
                    "day": day,
                    "totalPnl": day_total,
                    "productPnl": product_pnl,
                    "totalSlopePerStep": total_slope,
                    "totalR2": total_r2,
                    "productSlopePerStep": product_slopes,
                    "productR2": product_r2,
                    "ashPnl": product_pnl.get(products[0], 0.0),
                    "pepperPnl": product_pnl.get(products[1], 0.0) if len(products) > 1 else 0.0,
                }
            )

        timestamps = session_product_paths[products[0]]["timestamps"]
        for index, timestamp in enumerate(timestamps):
            session_total_path["timestamps"].append(timestamp)
            session_total_path["mtmPnl"].append(
                sum(session_product_paths[product]["mtmPnl"][index] for product in products)
            )

        total_pnl = sum(session_product_pnl.values())
        total_slope, total_r2 = fitted_path_stats(session_total_path["mtmPnl"])
        session_rows.append(
            {
                "sessionId": session_id,
                "totalPnl": total_pnl,
                "productPnl": dict(session_product_pnl),
                "productPosition": {product: 0 for product in products},
                "productCash": {product: 0.0 for product in products},
                "totalSlopePerStep": total_slope,
                "totalR2": total_r2,
                "ashPnl": session_product_pnl.get(products[0], 0.0),
                "pepperPnl": session_product_pnl.get(products[1], 0.0) if len(products) > 1 else 0.0,
                "ashPosition": 0,
                "pepperPosition": 0,
                "ashCash": 0.0,
                "pepperCash": 0.0,
            }
        )
        if session_id < sample_sessions:
            sample_paths.append({"sessionId": session_id, "products": session_product_paths, "total": session_total_path})

    dashboard = build_round3_dashboard(
        output_dir=output_dir,
        algorithm=algorithm,
        sessions=sessions,
        products=products,
        session_rows=session_rows,
        run_rows=run_rows,
        sample_paths=sample_paths,
        product_stats=model["productStats"],
        config={
            "fvMode": fv_mode,
            "tradeMode": trade_mode,
            "tomatoSupport": tomato_support,
            "seed": seed,
            "sampleSessions": sample_sessions,
            "ticksPerDay": ticks_per_day,
            "outputDays": output_days,
            "shiftMultiplier": shift_multiplier,
            "round3Stress": stress_config,
        },
    )
    with dashboard_path.open("w", encoding="utf-8") as handle:
        json.dump(dashboard, handle, indent=2)
    return dashboard


def run_rust_monte_carlo(
    algorithm: Path,
    output_dir: Path,
    data_root: Optional[Path],
    sessions: int,
    fv_mode: str,
    trade_mode: str,
    tomato_support: str,
    seed: int,
    python_bin: str,
    sample_sessions: int,
    ticks_per_day: int = 10000,
    r3_stress: str = "none",
    r3_spread_multiplier: Optional[float] = None,
    r3_depth_multiplier: Optional[float] = None,
    r3_trade_keep_probability: Optional[float] = None,
    r3_spot_shift_std: Optional[float] = None,
    r3_surface_shift_std: Optional[float] = None,
    r3_surface_tilt_std: Optional[float] = None,
) -> None:
    actual_dir = resolve_actual_dir(data_root)
    simulator_dir = rust_dir()
    if not simulator_dir.is_dir():
        raise RuntimeError(
            f"Rust simulator directory not found at {simulator_dir}. "
            "prosperity4mcbt currently expects a full repository checkout."
        )
    cmd = [
        "cargo",
        "run",
        "--release",
        "--",
        "--strategy",
        str(algorithm.resolve()),
        "--sessions",
        str(sessions),
        "--output",
        str(output_dir.resolve()),
        "--fv-mode",
        fv_mode,
        "--trade-mode",
        trade_mode,
        "--tomato-support",
        tomato_support,
        "--seed",
        str(seed),
        "--python-bin",
        python_bin,
        "--write-session-limit",
        str(sample_sessions),
        "--actual-dir",
        str(actual_dir.resolve()),
        "--ticks-per-day",
        str(ticks_per_day),
    ]
    env = {**os.environ, "PROSPERITY4MCBT_ROOT": str(project_root().resolve())}
    subprocess.run(cmd, cwd=simulator_dir, env=env, check=True)


def run_monte_carlo_mode(
    algorithm: Path,
    dashboard_path: Path,
    data_root: Optional[Path],
    sessions: int,
    fv_mode: str,
    trade_mode: str,
    tomato_support: str,
    seed: int,
    python_bin: str,
    sample_sessions: int,
    ticks_per_day: int = 10000,
    r3_stress: str = "none",
    r3_spread_multiplier: Optional[float] = None,
    r3_depth_multiplier: Optional[float] = None,
    r3_trade_keep_probability: Optional[float] = None,
    r3_spot_shift_std: Optional[float] = None,
    r3_surface_shift_std: Optional[float] = None,
    r3_surface_tilt_std: Optional[float] = None,
) -> dict[str, Any]:
    output_dir = dashboard_path.parent
    if output_dir.exists():
        for name in GENERATED_OUTPUT_FILES:
            path = output_dir / name
            if path.is_file():
                path.unlink()
        for name in GENERATED_OUTPUT_DIRS:
            path = output_dir / name
            if path.is_dir():
                shutil.rmtree(path)
    output_dir.mkdir(parents=True, exist_ok=True)

    if is_round3_data_root(data_root):
        return run_round3_python_monte_carlo(
            algorithm=algorithm,
            dashboard_path=dashboard_path,
            data_root=data_root,
            sessions=sessions,
            seed=seed,
            sample_sessions=sample_sessions,
            ticks_per_day=ticks_per_day,
            fv_mode=fv_mode,
            trade_mode=trade_mode,
            tomato_support=tomato_support,
            r3_stress=r3_stress,
            r3_spread_multiplier=r3_spread_multiplier,
            r3_depth_multiplier=r3_depth_multiplier,
            r3_trade_keep_probability=r3_trade_keep_probability,
            r3_spot_shift_std=r3_spot_shift_std,
            r3_surface_shift_std=r3_surface_shift_std,
            r3_surface_tilt_std=r3_surface_tilt_std,
        )

    run_rust_monte_carlo(
        algorithm=algorithm,
        output_dir=output_dir,
        data_root=data_root,
        sessions=sessions,
        fv_mode=fv_mode,
        trade_mode=trade_mode,
        tomato_support=tomato_support,
        seed=seed,
        python_bin=python_bin,
        sample_sessions=sample_sessions,
        ticks_per_day=ticks_per_day,
        r3_stress=r3_stress,
        r3_spread_multiplier=r3_spread_multiplier,
        r3_depth_multiplier=r3_depth_multiplier,
        r3_trade_keep_probability=r3_trade_keep_probability,
        r3_spot_shift_std=r3_spot_shift_std,
        r3_surface_shift_std=r3_surface_shift_std,
        r3_surface_tilt_std=r3_surface_tilt_std,
    )

    dashboard = build_dashboard(
        output_dir,
        algorithm,
        sessions,
        {
            "fvMode": fv_mode,
            "tradeMode": trade_mode,
            "tomatoSupport": tomato_support,
            "seed": seed,
            "sampleSessions": sample_sessions,
        },
    )
    with dashboard_path.open("w", encoding="utf-8") as handle:
        json.dump(dashboard, handle, indent=2)

    return dashboard
