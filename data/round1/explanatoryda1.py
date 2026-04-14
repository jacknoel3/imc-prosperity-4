import os
from pathlib import Path
import warnings

warnings.filterwarnings("ignore")

BASE_DIR = Path(__file__).resolve().parent
os.environ.setdefault("MPLCONFIGDIR", str(BASE_DIR / ".mplconfig"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from math import sqrt


# =========================
# CONFIG
# =========================
ROUND1_DATA_DIR = BASE_DIR
OUTPUT_DIR = BASE_DIR / "eda_output"
OUTPUT_DIR.mkdir(exist_ok=True)

PRICE_FILES = sorted(ROUND1_DATA_DIR.glob("prices_round_1_day_*.csv"))
TRADE_FILES = sorted(ROUND1_DATA_DIR.glob("trades_round_1_day_*.csv"))

TIME_BLOCKS = 12
ACF_LAGS = 50
ROLL_WINDOWS = [50, 100, 500]
FWD_RET_LAGS = [1, 5, 10]
BOOK_LEVELS = [1, 2, 3]


# =========================
# HELPERS
# =========================
def read_price_file(path):
    df = pd.read_csv(path, sep=";")
    df["source_file"] = os.path.basename(path)
    return df


def read_trade_file(path):
    df = pd.read_csv(path, sep=";")
    df["source_file"] = os.path.basename(path)
    return df


def infer_day_from_source_file(source_file):
    if pd.isna(source_file):
        return np.nan
    source_file = str(source_file)
    for token in ["day_-2", "day_-1", "day_0", "day_1", "day_2"]:
        if token in source_file:
            return int(token.replace("day_", ""))
    return np.nan


def ensure_numeric(df, cols):
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def safe_savefig(name):
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / name, dpi=180, bbox_inches="tight")
    plt.close()


def autocorr_series(x, max_lag=50):
    vals = []
    for lag in range(1, max_lag + 1):
        vals.append(x.autocorr(lag=lag))
    return pd.Series(vals, index=range(1, max_lag + 1))


def corr_safe(a, b):
    tmp = pd.concat([a, b], axis=1).dropna()
    if len(tmp) < 3:
        return np.nan
    return tmp.iloc[:, 0].corr(tmp.iloc[:, 1])


def classify_trade_side(row, mid_col="mid_price"):
    if pd.isna(row.get("price")) or pd.isna(row.get(mid_col)):
        return "unknown"
    if row["price"] > row[mid_col]:
        return "buy_aggr"
    if row["price"] < row[mid_col]:
        return "sell_aggr"
    return "mid"


def make_time_blocks(df, timestamp_col="timestamp", n_blocks=12):
    df = df.copy()
    tmin = df[timestamp_col].min()
    tmax = df[timestamp_col].max()
    if pd.isna(tmin) or pd.isna(tmax) or tmax == tmin:
        df["time_block"] = 0
        return df
    edges = np.linspace(tmin, tmax + 1e-9, n_blocks + 1)
    df["time_block"] = pd.cut(df[timestamp_col], bins=edges, labels=False, include_lowest=True)
    return df


def make_time_blocks_by_day(df, timestamp_col="timestamp", n_blocks=12):
    if "day" not in df.columns:
        return make_time_blocks(df, timestamp_col=timestamp_col, n_blocks=n_blocks)
    frames = []
    for _, day_df in df.groupby("day", sort=True):
        frames.append(make_time_blocks(day_df, timestamp_col=timestamp_col, n_blocks=n_blocks))
    return pd.concat(frames, ignore_index=True)


def top_book_features(df):
    df = df.copy()

    df["spread"] = df["ask_price_1"] - df["bid_price_1"]
    df["valid_quote"] = df["bid_price_1"].notna() & df["ask_price_1"].notna()

    askv = df["ask_volume_1"].abs()
    bidv = df["bid_volume_1"].abs()
    df["top_depth"] = bidv.fillna(0) + askv.fillna(0)

    denom = bidv + askv
    df["imbalance_l1"] = np.where(denom > 0, (bidv - askv) / denom, np.nan)
    df["microprice"] = np.where(
        denom > 0,
        (df["ask_price_1"] * bidv + df["bid_price_1"] * askv) / denom,
        np.nan,
    )
    df["microprice_minus_mid"] = df["microprice"] - df["mid_price"]

    for lvl in [2, 3]:
        bp = f"bid_price_{lvl}"
        bv = f"bid_volume_{lvl}"
        ap = f"ask_price_{lvl}"
        av = f"ask_volume_{lvl}"
        if all(c in df.columns for c in [bp, bv, ap, av]):
            bvv = df[bv].abs()
            avv = df[av].abs()
            denom_l = bvv + avv
            df[f"imbalance_l{lvl}"] = np.where(denom_l > 0, (bvv - avv) / denom_l, np.nan)

    bid_depth_cols = [c for c in ["bid_volume_1", "bid_volume_2", "bid_volume_3"] if c in df.columns]
    ask_depth_cols = [c for c in ["ask_volume_1", "ask_volume_2", "ask_volume_3"] if c in df.columns]
    df["total_bid_depth_3"] = df[bid_depth_cols].abs().sum(axis=1)
    df["total_ask_depth_3"] = df[ask_depth_cols].abs().sum(axis=1)
    depth_denom = df["total_bid_depth_3"] + df["total_ask_depth_3"]
    df["imbalance_l123"] = np.where(
        depth_denom > 0,
        (df["total_bid_depth_3"] - df["total_ask_depth_3"]) / depth_denom,
        np.nan,
    )
    df["depth_skew"] = df["total_bid_depth_3"] - df["total_ask_depth_3"]

    return df


def future_returns(df, price_col="mid_price", horizons=None):
    horizons = horizons or [1, 5, 10]
    df = df.copy()
    group_cols = ["day"] if "day" in df.columns else None
    if group_cols:
        df["dmid"] = df.groupby(group_cols)[price_col].diff()
    else:
        df["dmid"] = df[price_col].diff()
    for h in horizons:
        if group_cols:
            df[f"fwd_ret_{h}"] = df.groupby(group_cols)[price_col].shift(-h) - df[price_col]
        else:
            df[f"fwd_ret_{h}"] = df[price_col].shift(-h) - df[price_col]
    return df


def merge_trades_with_prices(trades_df, prices_df):
    merged_days = []
    for day in sorted(trades_df["day"].dropna().unique()):
        trades_day = trades_df[trades_df["day"] == day].dropna(subset=["timestamp"]).sort_values("timestamp")
        prices_day = prices_df[prices_df["day"] == day].dropna(subset=["timestamp"]).sort_values("timestamp")

        if len(trades_day) == 0:
            continue
        if len(prices_day) == 0:
            merged_days.append(trades_day.copy())
            continue

        merged_day = pd.merge_asof(
            trades_day.reset_index(drop=True),
            prices_day.drop(columns=["day"]).reset_index(drop=True),
            on="timestamp",
            direction="backward",
        )
        merged_day["day"] = day
        merged_days.append(merged_day)

    if not merged_days:
        return trades_df.copy()
    return pd.concat(merged_days, ignore_index=True)


def save_table(df, name, index=True):
    df.to_csv(OUTPUT_DIR / name, index=index)


def print_header(text):
    print("\n" + "=" * 100)
    print(text)
    print("=" * 100)


def best_lead_lag_relationships(wide_df, products, max_lag=10):
    rows = []
    for a in products:
        for b in products:
            if a == b:
                continue
            for lag in range(-max_lag, max_lag + 1):
                corr = corr_safe(wide_df[a], wide_df[b].shift(lag))
                rows.append(
                    {
                        "product_a": a,
                        "product_b": b,
                        "lag_applied_to_b": lag,
                        "corr": corr,
                    }
                )
    return pd.DataFrame(rows)


def arbitrage_diagnostics(wide_mid, products):
    if len(products) != 2:
        return pd.DataFrame()

    a, b = products
    tmp = wide_mid[["day", "timestamp", a, b]].dropna().copy()
    if len(tmp) == 0:
        return pd.DataFrame()

    tmp["a_ret"] = tmp.groupby("day")[a].diff()
    tmp["b_ret"] = tmp.groupby("day")[b].diff()
    tmp["a_z"] = tmp.groupby("day")[a].transform(lambda s: (s - s.mean()) / (s.std() if s.std() else 1.0))
    tmp["b_z"] = tmp.groupby("day")[b].transform(lambda s: (s - s.mean()) / (s.std() if s.std() else 1.0))
    tmp["z_spread"] = tmp["a_z"] - tmp["b_z"]

    z_spread_std = tmp["z_spread"].std()
    z_spread_ac1 = tmp["z_spread"].autocorr(lag=1)
    ret_corr = corr_safe(tmp["a_ret"], tmp["b_ret"])

    rows = [
        {
            "metric": "same_time_return_corr",
            "value": ret_corr,
            "interpretation": "Cross-product return correlation near zero means weak same-tick stat-arb linkage.",
        },
        {
            "metric": "z_spread_std",
            "value": z_spread_std,
            "interpretation": "Smaller is better for pair-trading stability after day-level normalization.",
        },
        {
            "metric": "z_spread_lag1_autocorr",
            "value": z_spread_ac1,
            "interpretation": "High positive autocorr suggests persistence, while negative suggests snapback.",
        },
        {
            "metric": "abs_mean_z_spread",
            "value": tmp["z_spread"].abs().mean(),
            "interpretation": "Large average normalized spread means the products drift independently.",
        },
    ]

    return pd.DataFrame(rows)


def lag_sweep_with_stats(a, b, max_lag=200):
    rows = []
    for lag in range(-max_lag, max_lag + 1):
        shifted = b.shift(lag)
        tmp = pd.concat([a, shifted], axis=1).dropna()
        n = len(tmp)
        corr = tmp.iloc[:, 0].corr(tmp.iloc[:, 1]) if n >= 3 else np.nan
        if pd.notna(corr) and n >= 3 and abs(corr) < 1:
            t_stat = abs(corr) * sqrt((n - 2) / (1 - corr * corr))
        else:
            t_stat = np.nan
        rows.append(
            {
                "lag": lag,
                "corr": corr,
                "n": n,
                "t_stat_abs": t_stat,
                "abs_corr": abs(corr) if pd.notna(corr) else np.nan,
            }
        )
    return pd.DataFrame(rows)


def mutual_information_terciles(a, b):
    tmp = pd.concat([a, b], axis=1).dropna().copy()
    if len(tmp) < 30:
        return np.nan
    try:
        ax = pd.qcut(tmp.iloc[:, 0].rank(method="first"), 3, labels=False)
        bx = pd.qcut(tmp.iloc[:, 1].rank(method="first"), 3, labels=False)
    except ValueError:
        return np.nan
    ct = pd.crosstab(ax, bx, normalize="all")
    px = ct.sum(axis=1)
    py = ct.sum(axis=0)
    mi = 0.0
    for i in ct.index:
        for j in ct.columns:
            p = ct.loc[i, j]
            if p > 0:
                mi += p * np.log(p / (px.loc[i] * py.loc[j]))
    return mi


def conditional_cross_product_analysis(wide_conditional, a_name, b_name, max_lag=50):
    conds = [("all", pd.Series(True, index=wide_conditional.index))]

    if "time_block" in wide_conditional.columns:
        for block in sorted(wide_conditional["time_block"].dropna().unique()):
            conds.append((f"time_block_{int(block)}", wide_conditional["time_block"] == block))

    for product in [a_name, b_name]:
        for field, label, low_q, high_q in [
            ("dmid_abs", "abs_move", 0.90, None),
            ("imb", "imb_high", None, 0.90),
            ("imb", "imb_low", 0.10, None),
            ("spread", "wide_spread", None, 0.90),
            ("spread", "tight_spread", 0.10, None),
            ("top_depth", "deep_book", None, 0.90),
            ("top_depth", "thin_book", 0.10, None),
        ]:
            col = f"{product}_{field}"
            if col not in wide_conditional.columns:
                continue
            series = wide_conditional[col]
            if label in {"imb_high", "wide_spread", "deep_book"}:
                conds.append((f"{product}_{label}", series >= series.quantile(high_q)))
            elif label in {"imb_low", "tight_spread", "thin_book"}:
                conds.append((f"{product}_{label}", series <= series.quantile(low_q)))
            elif label == "abs_move":
                conds.append((f"{product}_{label}_top_decile", series >= series.quantile(low_q)))

    both_big = (
        (wide_conditional[f"{a_name}_dmid_abs"] >= wide_conditional[f"{a_name}_dmid_abs"].quantile(0.90))
        & (wide_conditional[f"{b_name}_dmid_abs"] >= wide_conditional[f"{b_name}_dmid_abs"].quantile(0.90))
    )
    conds.append(("both_abs_move_top_decile", both_big))

    rows = []
    for condition_name, mask in conds:
        sub = wide_conditional[mask.fillna(False)].copy()
        if len(sub) < 200:
            continue
        x = sub[f"{a_name}_dmid"]
        y = sub[f"{b_name}_dmid"]
        sweep = lag_sweep_with_stats(x, y, max_lag=max_lag)
        best_idx = sweep["abs_corr"].idxmax()
        best = sweep.loc[best_idx]
        same_corr = corr_safe(x, y)
        sign_sub = ((np.sign(x) == np.sign(y)) & (x != 0) & (y != 0))
        rows.append(
            {
                "condition": condition_name,
                "n": len(sub),
                "same_corr": same_corr,
                "best_lag": int(best["lag"]),
                "best_corr": best["corr"],
                "best_abs_corr": best["abs_corr"],
                "best_lag_n": int(best["n"]),
                "best_lag_t_stat_abs": best["t_stat_abs"],
                "same_sign_rate_nonzero": sign_sub.mean(),
                "mutual_information_terciles": mutual_information_terciles(x, y),
            }
        )
    return pd.DataFrame(rows).sort_values("best_abs_corr", ascending=False)


def build_text_conclusion(summary_df, lead_lag_df, arb_df, all_lag_df=None, conditional_df=None):
    lines = []
    lines.append("Round 1 EDA conclusion")
    lines.append("======================")
    lines.append("")

    for product, row in summary_df.iterrows():
        lines.append(
            f"{product}: mean spread {row['spread_mean']:.2f}, mean top depth {row['top_depth_mean']:.2f}, "
            f"mid std {row['mid_std']:.2f}, delta-mid lag1 autocorr {row['dmid_autocorr_lag1']:.3f}, "
            f"imbalance to fwd_ret_1 corr {row['corr_imbalance_fwd1']:.3f}."
        )

    lines.append("")
    if not lead_lag_df.empty:
        best = lead_lag_df.assign(abs_corr=lambda x: x["corr"].abs()).sort_values("abs_corr", ascending=False).head(4)
        lines.append("Best lead-lag correlations:")
        for _, row in best.iterrows():
            lines.append(
                f"{row['product_a']} vs {row['product_b']} lag {int(row['lag_applied_to_b'])}: corr {row['corr']:.4f}"
            )
        lines.append("")

    if not arb_df.empty:
        metric_map = dict(zip(arb_df["metric"], arb_df["value"]))
        ret_corr = metric_map.get("same_time_return_corr", np.nan)
        if pd.notna(ret_corr) and abs(ret_corr) < 0.10:
            verdict = (
                "Cross-product arbitrage looks weak: same-time and lead-lag correlations are too small to support a "
                "reliable pair trade. Treat the products as mostly independent and trade them as singles."
            )
        else:
            verdict = (
                "There may be some cross-product structure, but it needs careful validation before using a pair trade."
            )
        lines.append(verdict)
    else:
        lines.append("No pair-trading verdict produced.")

    if all_lag_df is not None and not all_lag_df.empty:
        best_global = all_lag_df.sort_values("abs_corr", ascending=False).head(1).iloc[0]
        lines.append(
            f"Exhaustive lag sweep (-200 to +200): best abs corr was {best_global['abs_corr']:.4f} "
            f"at lag {int(best_global['lag'])}, which is still economically weak."
        )

    if conditional_df is not None and not conditional_df.empty:
        best_cond = conditional_df.iloc[0]
        lines.append(
            f"Best conditional slice was {best_cond['condition']} with abs corr {best_cond['best_abs_corr']:.4f} "
            f"at lag {int(best_cond['best_lag'])} on n={int(best_cond['n'])}; treat as exploratory, not robust."
        )

    lines.append("")
    lines.append("Working hypothesis:")
    lines.append("- ASH_COATED_OSMIUM is the better candidate for short-horizon mean-reversion or market making.")
    lines.append("- INTARIAN_PEPPER_ROOT appears to have a strong day-level drift/regime component and should be modelled separately.")
    lines.append("- Order book imbalance looks useful for both products and deserves strategy testing.")
    return "\n".join(lines)


# =========================
# LOAD DATA
# =========================
prices = pd.concat([read_price_file(f) for f in PRICE_FILES], ignore_index=True)
trades = pd.concat([read_trade_file(f) for f in TRADE_FILES], ignore_index=True)

price_num_cols = [
    "day",
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
trade_num_cols = ["timestamp", "price", "quantity"]

prices = ensure_numeric(prices, price_num_cols)
trades = ensure_numeric(trades, trade_num_cols)
trades["day"] = trades["source_file"].map(infer_day_from_source_file)

prices = prices[prices["mid_price"].notna() & (prices["mid_price"] > 0)].copy()
trades = trades[trades["price"].notna() & trades["quantity"].notna()].copy()

prices = prices.sort_values(["product", "day", "timestamp"]).reset_index(drop=True)
trades = trades.sort_values(["symbol", "day", "timestamp"]).reset_index(drop=True)
products = sorted(prices["product"].dropna().unique())

print_header("BASIC STRUCTURE")
print("Products:", products)
print("\nPrices shape:", prices.shape)
print(prices.head())
print("\nTrades shape:", trades.shape)
print(trades.head())


# =========================
# FEATURE ENGINEERING
# =========================
price_frames = []
for product in products:
    p = prices[prices["product"] == product].copy().sort_values(["day", "timestamp"])
    p = top_book_features(p)
    p = future_returns(p, price_col="mid_price", horizons=FWD_RET_LAGS)
    p = make_time_blocks_by_day(p, "timestamp", TIME_BLOCKS)
    price_frames.append(p)
prices_feat = pd.concat(price_frames, ignore_index=True)

trade_frames = []
for product in products:
    tp = trades[trades["symbol"] == product].copy()
    pp = prices_feat[
        prices_feat["product"] == product
    ][["day", "timestamp", "mid_price", "bid_price_1", "ask_price_1"]].copy()
    merged = merge_trades_with_prices(
        tp.sort_values(["day", "timestamp"]).reset_index(drop=True),
        pp.sort_values(["day", "timestamp"]).reset_index(drop=True),
    )
    merged["trade_minus_mid"] = merged["price"] - merged["mid_price"]
    merged["notional"] = merged["price"] * merged["quantity"]
    merged["aggressor"] = merged.apply(classify_trade_side, axis=1)
    trade_frames.append(merged)
trades_feat = pd.concat(trade_frames, ignore_index=True)


# =========================
# SUMMARY TABLES
# =========================
summary_rows = []
liquidity_rows = []
volume_rows = []

for product in products:
    p = prices_feat[prices_feat["product"] == product].copy()
    t = trades_feat[trades_feat["symbol"] == product].copy()

    summary_rows.append(
        {
            "product": product,
            "n_price_rows": len(p),
            "n_trade_rows": len(t),
            "mid_mean": p["mid_price"].mean(),
            "mid_std": p["mid_price"].std(),
            "mid_min": p["mid_price"].min(),
            "mid_max": p["mid_price"].max(),
            "spread_mean": p["spread"].mean(),
            "spread_median": p["spread"].median(),
            "spread_std": p["spread"].std(),
            "dmid_std": p["dmid"].std(),
            "dmid_autocorr_lag1": p["dmid"].autocorr(lag=1),
            "top_depth_mean": p["top_depth"].mean(),
            "imbalance_l1_mean": p["imbalance_l1"].mean(),
            "imbalance_l1_std": p["imbalance_l1"].std(),
            "microprice_minus_mid_mean": p["microprice_minus_mid"].mean(),
            "corr_imbalance_fwd1": corr_safe(p["imbalance_l1"], p["fwd_ret_1"]),
            "corr_imbalance_fwd5": corr_safe(p["imbalance_l1"], p["fwd_ret_5"]),
            "corr_imbalance_fwd10": corr_safe(p["imbalance_l1"], p["fwd_ret_10"]),
        }
    )

    liquidity_rows.append(
        {
            "product": product,
            "valid_quote_ratio": p["valid_quote"].mean(),
            "spread_p10": p["spread"].quantile(0.10),
            "spread_p50": p["spread"].quantile(0.50),
            "spread_p90": p["spread"].quantile(0.90),
            "top_depth_mean": p["top_depth"].mean(),
            "top_depth_median": p["top_depth"].median(),
            "depth_3_bid_mean": p["total_bid_depth_3"].mean(),
            "depth_3_ask_mean": p["total_ask_depth_3"].mean(),
        }
    )

    volume_rows.append(
        {
            "product": product,
            "trade_count": len(t),
            "total_volume": t["quantity"].sum(),
            "avg_trade_size": t["quantity"].mean(),
            "median_trade_size": t["quantity"].median(),
            "total_notional": t["notional"].sum(),
            "trades_per_day": len(t) / max(p["day"].nunique(), 1),
        }
    )

summary_df = pd.DataFrame(summary_rows).set_index("product")
liquidity_df = pd.DataFrame(liquidity_rows).set_index("product")
volume_df = pd.DataFrame(volume_rows).set_index("product")

print_header("SUMMARY STATS")
print(summary_df.round(4))
save_table(summary_df, "summary_stats_by_product.csv")
save_table(liquidity_df, "liquidity_summary_by_product.csv")
save_table(volume_df, "volume_summary_by_product.csv")


# =========================
# PER-PRODUCT PLOTS & TABLES
# =========================
for product in products:
    print_header(f"PROCESSING {product}")
    p = prices_feat[prices_feat["product"] == product].copy().sort_values(["day", "timestamp"])
    t = trades_feat[trades_feat["symbol"] == product].copy().sort_values(["day", "timestamp"])
    p["row_index"] = np.arange(len(p))

    plt.figure(figsize=(12, 4))
    plt.plot(p["row_index"], p["mid_price"])
    plt.title(f"{product} - mid price")
    plt.xlabel("row index")
    plt.ylabel("mid price")
    safe_savefig(f"{product}_01_mid_price_series.png")

    plt.figure(figsize=(7, 4))
    plt.hist(p["mid_price"].dropna(), bins=40)
    plt.title(f"{product} - mid price histogram")
    plt.xlabel("mid price")
    plt.ylabel("count")
    safe_savefig(f"{product}_02_mid_hist.png")

    plt.figure(figsize=(7, 4))
    plt.hist(p["dmid"].dropna(), bins=40)
    plt.title(f"{product} - delta mid histogram")
    plt.xlabel("delta mid")
    plt.ylabel("count")
    safe_savefig(f"{product}_03_dmid_hist.png")

    plt.figure(figsize=(8, 4))
    p.boxplot(column="mid_price", by="time_block", grid=False)
    plt.title(f"{product} - mid by time block")
    plt.suptitle("")
    plt.xlabel("time block")
    plt.ylabel("mid price")
    safe_savefig(f"{product}_04_mid_box_by_block.png")

    plt.figure(figsize=(12, 4))
    plt.plot(p["row_index"], p["spread"])
    plt.title(f"{product} - spread over time")
    plt.xlabel("row index")
    plt.ylabel("spread")
    safe_savefig(f"{product}_05_spread_series.png")

    plt.figure(figsize=(7, 4))
    plt.hist(p["spread"].dropna(), bins=30)
    plt.title(f"{product} - spread histogram")
    plt.xlabel("spread")
    plt.ylabel("count")
    safe_savefig(f"{product}_06_spread_hist.png")

    first_valid = p.dropna(subset=["bid_price_1", "ask_price_1"]).head(1)
    if len(first_valid) > 0:
        r = first_valid.iloc[0]
        book_prices = []
        book_sizes = []
        book_colors = []
        for lvl in BOOK_LEVELS:
            bp = r.get(f"bid_price_{lvl}", np.nan)
            bv = r.get(f"bid_volume_{lvl}", np.nan)
            ap = r.get(f"ask_price_{lvl}", np.nan)
            av = r.get(f"ask_volume_{lvl}", np.nan)

            if pd.notna(bp) and pd.notna(bv):
                book_prices.append(bp)
                book_sizes.append(abs(bv))
                book_colors.append("green")
            if pd.notna(ap) and pd.notna(av):
                book_prices.append(ap)
                book_sizes.append(abs(av))
                book_colors.append("red")

        plt.figure(figsize=(8, 4))
        plt.bar(book_prices, book_sizes, width=0.8, color=book_colors)
        plt.title(f"{product} - book snapshot at first valid quote")
        plt.xlabel("price")
        plt.ylabel("volume")
        safe_savefig(f"{product}_07_book_snapshot.png")

    plt.figure(figsize=(12, 4))
    plt.plot(p["row_index"], p["imbalance_l1"])
    plt.title(f"{product} - level-1 imbalance")
    plt.xlabel("row index")
    plt.ylabel("imbalance_l1")
    safe_savefig(f"{product}_08_imbalance_series.png")

    plt.figure(figsize=(12, 5))
    plt.plot(p["row_index"], p["mid_price"], label="mid")
    p_plot = p[["day", "timestamp", "row_index", "mid_price"]].copy().sort_values(["day", "timestamp"])
    t_plot = t.copy().sort_values(["day", "timestamp"])
    merged_plot = merge_trades_with_prices(t_plot, p_plot)
    for label, marker in [("buy_aggr", "^"), ("sell_aggr", "v"), ("mid", "o")]:
        sub = merged_plot[merged_plot["aggressor"] == label]
        if len(sub) > 0:
            plt.scatter(sub["row_index"], sub["price"], s=14, marker=marker, label=label)
    plt.title(f"{product} - mid with trades")
    plt.xlabel("row index")
    plt.ylabel("price")
    plt.legend()
    safe_savefig(f"{product}_09_mid_with_trades.png")

    if len(t) > 0:
        plt.figure(figsize=(7, 4))
        plt.hist(t["trade_minus_mid"].dropna(), bins=30)
        plt.axvline(0, linestyle="--")
        plt.title(f"{product} - trade price minus mid")
        plt.xlabel("trade price - mid")
        plt.ylabel("count")
        safe_savefig(f"{product}_10_trade_minus_mid_hist.png")

        plt.figure(figsize=(7, 4))
        plt.hist(t["quantity"].dropna(), bins=min(20, max(t["quantity"].nunique(), 1)))
        plt.title(f"{product} - trade quantity histogram")
        plt.xlabel("quantity")
        plt.ylabel("count")
        safe_savefig(f"{product}_11_trade_quantity_hist.png")

    if len(t) > 1:
        t = t.sort_values(["day", "timestamp"]).copy()
        t["interarrival"] = t.groupby("day")["timestamp"].diff()
        plt.figure(figsize=(7, 4))
        plt.hist(t["interarrival"].dropna(), bins=30, log=True)
        plt.title(f"{product} - interarrival time")
        plt.xlabel("ticks between trades")
        plt.ylabel("count (log scale)")
        safe_savefig(f"{product}_12_interarrival_hist.png")

    acf_vals = autocorr_series(p["dmid"].dropna(), max_lag=ACF_LAGS)
    plt.figure(figsize=(10, 4))
    plt.bar(acf_vals.index, acf_vals.values, width=0.8)
    plt.axhline(0, linewidth=1)
    plt.title(f"{product} - autocorrelation of delta mid")
    plt.xlabel("lag")
    plt.ylabel("autocorrelation")
    safe_savefig(f"{product}_13_dmid_acf.png")

    plt.figure(figsize=(12, 5))
    for w in ROLL_WINDOWS:
        plt.plot(p["dmid"].rolling(w).std(), label=f"window={w}")
    plt.title(f"{product} - rolling std of delta mid")
    plt.xlabel("row index")
    plt.ylabel("rolling std")
    plt.legend()
    safe_savefig(f"{product}_14_rolling_std.png")

    plt.figure(figsize=(7, 5))
    sample = p[["imbalance_l1", "fwd_ret_1"]].dropna()
    if len(sample) > 3000:
        sample = sample.sample(3000, random_state=42)
    plt.scatter(sample["imbalance_l1"], sample["fwd_ret_1"], s=8, alpha=0.5)
    plt.axhline(0, linewidth=1)
    plt.axvline(0, linewidth=1)
    plt.title(f"{product} - imbalance vs future delta mid")
    plt.xlabel("imbalance_l1")
    plt.ylabel("future delta mid (+1)")
    safe_savefig(f"{product}_15_imbalance_vs_fwd1_scatter.png")

    tmp = p[["imbalance_l1"] + [f"fwd_ret_{h}" for h in FWD_RET_LAGS]].dropna().copy()
    if len(tmp) > 20:
        tmp["imb_bucket"] = pd.qcut(tmp["imbalance_l1"], q=10, duplicates="drop")
        bucket_means = tmp.groupby("imb_bucket")[[f"fwd_ret_{h}" for h in FWD_RET_LAGS]].mean()
        bucket_means.plot(kind="bar", figsize=(10, 4))
        plt.title(f"{product} - average future return by imbalance decile")
        plt.xlabel("imbalance decile")
        plt.ylabel("average future return")
        safe_savefig(f"{product}_16_imbalance_bucket_predictiveness.png")
        bucket_means.to_csv(OUTPUT_DIR / f"{product}_imbalance_bucket_predictiveness.csv")

    plt.figure(figsize=(7, 4))
    plt.hist(p["microprice_minus_mid"].dropna(), bins=40)
    plt.title(f"{product} - microprice minus mid")
    plt.xlabel("microprice - mid")
    plt.ylabel("count")
    safe_savefig(f"{product}_17_microprice_minus_mid_hist.png")

    block_liquidity = p.groupby(["day", "time_block"]).agg(
        mid_mean=("mid_price", "mean"),
        spread_mean=("spread", "mean"),
        top_depth_mean=("top_depth", "mean"),
        quote_ratio=("valid_quote", "mean"),
        obs=("mid_price", "size"),
    )
    save_table(block_liquidity, f"{product}_liquidity_by_block.csv")

    daily_volume = t.groupby("day").agg(
        trades=("quantity", "size"),
        total_qty=("quantity", "sum"),
        avg_qty=("quantity", "mean"),
        avg_trade_minus_mid=("trade_minus_mid", "mean"),
    )
    save_table(daily_volume, f"{product}_trade_volume_by_day.csv")

    acf_table = pd.DataFrame(
        {
            "lag": range(1, 11),
            "autocorr_dmid": [p["dmid"].autocorr(lag=i) for i in range(1, 11)],
        }
    ).set_index("lag")
    acf_table.to_csv(OUTPUT_DIR / f"{product}_acf_lag1_to_10.csv")

    signal_corr_df = pd.DataFrame(
        {
            "metric": [
                "corr(imbalance_l1, fwd_ret_1)",
                "corr(imbalance_l1, fwd_ret_5)",
                "corr(imbalance_l1, fwd_ret_10)",
                "corr(microprice_minus_mid, fwd_ret_1)",
                "corr(spread, abs(dmid))",
            ],
            "value": [
                corr_safe(p["imbalance_l1"], p["fwd_ret_1"]),
                corr_safe(p["imbalance_l1"], p["fwd_ret_5"]),
                corr_safe(p["imbalance_l1"], p["fwd_ret_10"]),
                corr_safe(p["microprice_minus_mid"], p["fwd_ret_1"]),
                corr_safe(p["spread"], p["dmid"].abs()),
            ],
        }
    )
    signal_corr_df.to_csv(OUTPUT_DIR / f"{product}_signal_diagnostics.csv", index=False)

    day_summary = p.groupby("day").agg(
        mid_mean=("mid_price", "mean"),
        mid_std=("mid_price", "std"),
        spread_mean=("spread", "mean"),
        spread_std=("spread", "std"),
        top_depth_mean=("top_depth", "mean"),
        dmid_std=("dmid", "std"),
        imb_mean=("imbalance_l1", "mean"),
        n_obs=("mid_price", "size"),
    )
    day_summary.to_csv(OUTPUT_DIR / f"{product}_day_summary.csv")


# =========================
# CROSS-PRODUCT ANALYSIS
# =========================
print_header("CROSS-PRODUCT ANALYSIS")

ret_wide = []
mid_wide = []
for product in products:
    p = prices_feat[prices_feat["product"] == product].copy().sort_values(["day", "timestamp"])
    ret_wide.append(p[["day", "timestamp", "dmid"]].rename(columns={"dmid": product}))
    mid_wide.append(p[["day", "timestamp", "mid_price"]].rename(columns={"mid_price": product}))

from functools import reduce

wide_returns = reduce(lambda left, right: pd.merge(left, right, on=["day", "timestamp"], how="outer"), ret_wide)
wide_mids = reduce(lambda left, right: pd.merge(left, right, on=["day", "timestamp"], how="outer"), mid_wide)
wide_returns = wide_returns.sort_values(["day", "timestamp"]).reset_index(drop=True)
wide_mids = wide_mids.sort_values(["day", "timestamp"]).reset_index(drop=True)
all_lag_df = pd.DataFrame()
conditional_df = pd.DataFrame()

if len(products) >= 2:
    corr_mat = wide_returns[products].corr()
    print("\nReturn correlation matrix:")
    print(corr_mat.round(4))
    corr_mat.to_csv(OUTPUT_DIR / "cross_product_return_corr.csv")

    lead_lag_df = best_lead_lag_relationships(wide_returns, products, max_lag=10)
    lead_lag_df.to_csv(OUTPUT_DIR / "lead_lag_correlations.csv", index=False)

    best_ll = (
        lead_lag_df.assign(abs_corr=lambda x: x["corr"].abs())
        .sort_values("abs_corr", ascending=False)
        .head(20)
    )
    print("\nBest lead-lag relationships by absolute correlation:")
    print(best_ll.round(4))

    plt.figure(figsize=(12, 5))
    for product in products:
        p = prices_feat[prices_feat["product"] == product].copy().sort_values(["day", "timestamp"])
        z = p.groupby("day")["mid_price"].transform(lambda s: (s - s.mean()) / (s.std() if s.std() else 1.0))
        plt.plot(z.reset_index(drop=True), label=product)
    plt.title("Within-day normalized mid prices across products")
    plt.xlabel("row index")
    plt.ylabel("within-day z-score")
    plt.legend()
    safe_savefig("cross_product_normalised_mid_prices.png")

    arb_df = arbitrage_diagnostics(wide_mids, products)
    arb_df.to_csv(OUTPUT_DIR / "arbitrage_diagnostics.csv", index=False)

    if len(products) == 2:
        a, b = products
        wide_conditional = wide_returns[["day", "timestamp", a, b]].copy()
        wide_conditional = wide_conditional.rename(columns={a: f"{a}_dmid", b: f"{b}_dmid"})
        first_product = True
        for product in products:
            cols = ["day", "timestamp", "spread", "top_depth", "imbalance_l1"]
            if first_product:
                cols.append("time_block")
            px = prices_feat[prices_feat["product"] == product][cols].copy()
            px = px.rename(
                columns={
                    "spread": f"{product}_spread",
                    "top_depth": f"{product}_top_depth",
                    "imbalance_l1": f"{product}_imb",
                }
            )
            wide_conditional = wide_conditional.merge(px, on=["day", "timestamp"], how="left")
            first_product = False

        wide_conditional[f"{a}_dmid_abs"] = wide_conditional[f"{a}_dmid"].abs()
        wide_conditional[f"{b}_dmid_abs"] = wide_conditional[f"{b}_dmid"].abs()

        all_lag_df = lag_sweep_with_stats(wide_conditional[f"{a}_dmid"], wide_conditional[f"{b}_dmid"], max_lag=200)
        all_lag_df.to_csv(OUTPUT_DIR / "all_lag_correlation_sweep.csv", index=False)

        by_day_rows = []
        for day, sub in wide_conditional.groupby("day", sort=True):
            sweep = lag_sweep_with_stats(sub[f"{a}_dmid"], sub[f"{b}_dmid"], max_lag=100)
            best = sweep.sort_values("abs_corr", ascending=False).head(1).iloc[0]
            by_day_rows.append(
                {
                    "day": day,
                    "best_lag": int(best["lag"]),
                    "best_corr": best["corr"],
                    "best_abs_corr": best["abs_corr"],
                    "n": int(best["n"]),
                    "t_stat_abs": best["t_stat_abs"],
                }
            )
        by_day_lag_df = pd.DataFrame(by_day_rows)
        by_day_lag_df.to_csv(OUTPUT_DIR / "by_day_best_lag_correlations.csv", index=False)

        conditional_df = conditional_cross_product_analysis(wide_conditional, a, b, max_lag=50)
        conditional_df.to_csv(OUTPUT_DIR / "conditional_cross_product_correlations.csv", index=False)

        x = wide_conditional[f"{a}_dmid"]
        y = wide_conditional[f"{b}_dmid"]
        sign_nonzero = ((np.sign(x) == np.sign(y)) & (x != 0) & (y != 0))
        nonlinear_df = pd.DataFrame(
            [
                {
                    "metric": "same_sign_rate_all",
                    "value": (np.sign(x) == np.sign(y)).mean(),
                },
                {
                    "metric": "same_sign_rate_nonzero",
                    "value": sign_nonzero.mean(),
                },
                {
                    "metric": "mutual_information_terciles",
                    "value": mutual_information_terciles(x, y),
                },
            ]
        )
        nonlinear_df.to_csv(OUTPUT_DIR / "nonlinear_cross_product_diagnostics.csv", index=False)

        tmp = wide_mids[["day", "timestamp", a, b]].dropna().copy()
        tmp["a_z"] = tmp.groupby("day")[a].transform(lambda s: (s - s.mean()) / (s.std() if s.std() else 1.0))
        tmp["b_z"] = tmp.groupby("day")[b].transform(lambda s: (s - s.mean()) / (s.std() if s.std() else 1.0))
        tmp["z_spread"] = tmp["a_z"] - tmp["b_z"]

        plt.figure(figsize=(12, 4))
        plt.plot(tmp["z_spread"].reset_index(drop=True))
        plt.axhline(0, linestyle="--", linewidth=1)
        plt.title(f"Pair diagnostic - within-day normalized spread: {a} - {b}")
        plt.xlabel("row index")
        plt.ylabel("z-spread")
        safe_savefig("pair_zspread_series.png")

        plt.figure(figsize=(7, 4))
        plt.hist(tmp["z_spread"].dropna(), bins=40)
        plt.axvline(0, linestyle="--", linewidth=1)
        plt.title(f"Pair diagnostic - z-spread histogram: {a} - {b}")
        plt.xlabel("z-spread")
        plt.ylabel("count")
        safe_savefig("pair_zspread_hist.png")
else:
    lead_lag_df = pd.DataFrame()
    arb_df = pd.DataFrame()


# =========================
# TRADE FLOW SUMMARY
# =========================
print_header("TRADE FLOW SUMMARY")

flow_rows = []
for product in products:
    t = trades_feat[trades_feat["symbol"] == product].copy()
    if len(t) == 0:
        continue
    flow_rows.append(
        {
            "product": product,
            "n_trades": len(t),
            "avg_quantity": t["quantity"].mean(),
            "median_quantity": t["quantity"].median(),
            "buy_aggr_count": (t["aggressor"] == "buy_aggr").sum(),
            "sell_aggr_count": (t["aggressor"] == "sell_aggr").sum(),
            "mid_count": (t["aggressor"] == "mid").sum(),
            "unknown_count": (t["aggressor"] == "unknown").sum(),
            "trade_minus_mid_mean": t["trade_minus_mid"].mean(),
            "trade_minus_mid_std": t["trade_minus_mid"].std(),
        }
    )

flow_df = pd.DataFrame(flow_rows).set_index("product")
print(flow_df.round(4))
flow_df.to_csv(OUTPUT_DIR / "trade_flow_summary.csv")


# =========================
# FINAL TEXT SUMMARY
# =========================
conclusion_text = build_text_conclusion(summary_df, lead_lag_df, arb_df, all_lag_df, conditional_df)
(OUTPUT_DIR / "eda_summary.txt").write_text(conclusion_text)

print_header("DONE")
print(f"All charts and tables saved in: {OUTPUT_DIR}")
print(conclusion_text)
