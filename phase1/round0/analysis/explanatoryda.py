import os
from pathlib import Path
import warnings

warnings.filterwarnings("ignore")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# =========================
# CONFIG
# =========================
BASE_DIR = Path(__file__).resolve().parent
ROUND0_DATA_DIR = BASE_DIR
OUTPUT_DIR = BASE_DIR / "eda_output"
OUTPUT_DIR.mkdir(exist_ok=True)

PRICE_FILES = [
    ROUND0_DATA_DIR / "prices_round_0_day_-2.csv",
    ROUND0_DATA_DIR / "prices_round_0_day_-1.csv",
]

TRADE_FILES = [
    ROUND0_DATA_DIR / "trades_round_0_day_-2.csv",
    ROUND0_DATA_DIR / "trades_round_0_day_-1.csv",
]

TIME_BLOCKS = 10          # for boxplots by time block
ACF_LAGS = 50             # autocorrelation plot depth
ROLL_WINDOWS = [50, 100, 500]
FWD_RET_LAGS = [1, 5, 10] # future return horizons
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
    if "day_-2" in source_file:
        return -2
    if "day_-1" in source_file:
        return -1
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
    # crude aggressor inference using trade price vs current mid
    if pd.isna(row["price"]) or pd.isna(row[mid_col]):
        return "unknown"
    if row["price"] > row[mid_col]:
        return "buy_aggr"
    elif row["price"] < row[mid_col]:
        return "sell_aggr"
    return "mid"


def make_time_blocks(df, timestamp_col="timestamp", n_blocks=10):
    df = df.copy()
    tmin = df[timestamp_col].min()
    tmax = df[timestamp_col].max()
    if tmax == tmin:
        df["time_block"] = 0
        return df
    edges = np.linspace(tmin, tmax + 1e-9, n_blocks + 1)
    df["time_block"] = pd.cut(df[timestamp_col], bins=edges, labels=False, include_lowest=True)
    return df


def top_book_features(df):
    df = df.copy()

    df["spread"] = df["ask_price_1"] - df["bid_price_1"]
    df["top_depth"] = df["bid_volume_1"].fillna(0).abs() + df["ask_volume_1"].fillna(0).abs()

    # round 0 stores visible book volumes as positive sizes on both sides
    askv = df["ask_volume_1"].abs()
    bidv = df["bid_volume_1"].abs()

    denom = bidv + askv
    df["imbalance_l1"] = np.where(denom > 0, (bidv - askv) / denom, np.nan)

    # microprice
    # weighted toward side with less liquidity at the top
    denom2 = bidv + askv
    df["microprice"] = np.where(
        denom2 > 0,
        (df["ask_price_1"] * bidv + df["bid_price_1"] * askv) / denom2,
        np.nan
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
            denom = bvv + avv
            df[f"imbalance_l{lvl}"] = np.where(denom > 0, (bvv - avv) / denom, np.nan)

    # total visible depth across 3 levels if available
    bid_depth_cols = [c for c in ["bid_volume_1", "bid_volume_2", "bid_volume_3"] if c in df.columns]
    ask_depth_cols = [c for c in ["ask_volume_1", "ask_volume_2", "ask_volume_3"] if c in df.columns]
    df["total_bid_depth_3"] = df[bid_depth_cols].abs().sum(axis=1)
    df["total_ask_depth_3"] = df[ask_depth_cols].abs().sum(axis=1)
    depth_denom = df["total_bid_depth_3"] + df["total_ask_depth_3"]
    df["imbalance_l123"] = np.where(
        depth_denom > 0,
        (df["total_bid_depth_3"] - df["total_ask_depth_3"]) / depth_denom,
        np.nan
    )

    return df


def future_returns(df, price_col="mid_price", horizons=[1, 5, 10]):
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


def print_header(text):
    print("\n" + "=" * 100)
    print(text)
    print("=" * 100)


def save_table(df, name):
    df.to_csv(OUTPUT_DIR / name, index=True)


def merge_trades_with_prices(trades_df, prices_df):
    merged_days = []

    for day in sorted(trades_df["day"].dropna().unique()):
        trades_day = (
            trades_df[trades_df["day"] == day]
            .dropna(subset=["timestamp"])
            .sort_values("timestamp")
            .reset_index(drop=True)
        )
        prices_day = (
            prices_df[prices_df["day"] == day]
            .dropna(subset=["timestamp"])
            .sort_values("timestamp")
            .reset_index(drop=True)
        )

        if len(trades_day) == 0:
            continue

        if len(prices_day) == 0:
            merged_days.append(trades_day.copy())
            continue

        merged_day = pd.merge_asof(
            trades_day,
            prices_day.drop(columns=["day"]),
            on="timestamp",
            direction="backward"
        )
        merged_day["day"] = day
        merged_days.append(
            merged_day
        )

    if not merged_days:
        return trades_df.copy()

    return pd.concat(merged_days, ignore_index=True)


def make_time_blocks_by_day(df, timestamp_col="timestamp", n_blocks=10):
    frames = []
    if "day" not in df.columns:
        return make_time_blocks(df, timestamp_col=timestamp_col, n_blocks=n_blocks)
    for _, day_df in df.groupby("day", sort=True):
        frames.append(make_time_blocks(day_df, timestamp_col=timestamp_col, n_blocks=n_blocks))
    return pd.concat(frames, ignore_index=True)


# =========================
# LOAD DATA
# =========================
prices = pd.concat([read_price_file(f) for f in PRICE_FILES], ignore_index=True)
trades = pd.concat([read_trade_file(f) for f in TRADE_FILES], ignore_index=True)

price_num_cols = [
    "day", "timestamp",
    "bid_price_1", "bid_volume_1", "bid_price_2", "bid_volume_2", "bid_price_3", "bid_volume_3",
    "ask_price_1", "ask_volume_1", "ask_price_2", "ask_volume_2", "ask_price_3", "ask_volume_3",
    "mid_price", "profit_and_loss"
]
trade_num_cols = ["timestamp", "price", "quantity"]

prices = ensure_numeric(prices, price_num_cols)
trades = ensure_numeric(trades, trade_num_cols)
trades["day"] = trades["source_file"].map(infer_day_from_source_file)

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

# Merge nearest current mid into trades by day+timestamp where possible
# First build a lookup from prices
trade_frames = []
for product in products:
    tp = trades[trades["symbol"] == product].copy()
    pp = prices_feat[prices_feat["product"] == product][["day", "timestamp", "mid_price", "bid_price_1", "ask_price_1"]].copy()

    tp = tp.sort_values(["day", "timestamp"]).reset_index(drop=True)
    pp = pp.sort_values(["day", "timestamp"]).reset_index(drop=True)

    merged = merge_trades_with_prices(tp, pp)
    merged["trade_minus_mid"] = merged["price"] - merged["mid_price"]
    merged["aggressor"] = merged.apply(classify_trade_side, axis=1)
    trade_frames.append(merged)

trades_feat = pd.concat(trade_frames, ignore_index=True)


# =========================
# SUMMARY TABLES
# =========================
summary_rows = []
for product in products:
    p = prices_feat[prices_feat["product"] == product].copy()
    t = trades_feat[trades_feat["symbol"] == product].copy()

    row = {
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
        "imbalance_l1_mean": p["imbalance_l1"].mean(),
        "imbalance_l1_std": p["imbalance_l1"].std(),
        "microprice_minus_mid_mean": p["microprice_minus_mid"].mean(),
        "trade_price_minus_mid_mean": t["trade_minus_mid"].mean(),
        "trade_price_minus_mid_std": t["trade_minus_mid"].std(),
        "avg_trade_size": t["quantity"].mean(),
    }
    summary_rows.append(row)

summary_df = pd.DataFrame(summary_rows).set_index("product")
print_header("SUMMARY STATS")
print(summary_df.round(4))
save_table(summary_df, "summary_stats_by_product.csv")


# =========================
# PER-PRODUCT PLOTS
# =========================
for product in products:
    print_header(f"PROCESSING {product}")
    p = prices_feat[prices_feat["product"] == product].copy().sort_values(["day", "timestamp"])
    t = trades_feat[trades_feat["symbol"] == product].copy().sort_values(["day", "timestamp"])

    p["row_index"] = np.arange(len(p))

    # 1. mid price over concatenated sample
    plt.figure(figsize=(12, 4))
    plt.plot(p["row_index"], p["mid_price"])
    plt.title(f"{product} — mid price (concatenated sample)")
    plt.xlabel("row index")
    plt.ylabel("mid price")
    safe_savefig(f"{product}_01_mid_price_series.png")

    # 2. mid price histogram
    plt.figure(figsize=(7, 4))
    plt.hist(p["mid_price"].dropna(), bins=40)
    plt.title(f"{product} — mid price histogram")
    plt.xlabel("mid price")
    plt.ylabel("count")
    safe_savefig(f"{product}_02_mid_hist.png")

    # 3. delta mid histogram
    plt.figure(figsize=(7, 4))
    plt.hist(p["dmid"].dropna(), bins=40)
    plt.title(f"{product} — delta mid histogram")
    plt.xlabel("Δ mid")
    plt.ylabel("count")
    safe_savefig(f"{product}_03_dmid_hist.png")

    # 4. boxplot by time block
    plt.figure(figsize=(8, 4))
    p.boxplot(column="mid_price", by="time_block", grid=False)
    plt.title(f"{product} — mid by time block")
    plt.suptitle("")
    plt.xlabel("time block")
    plt.ylabel("mid price")
    safe_savefig(f"{product}_04_mid_box_by_block.png")

    # 5. spread over time
    plt.figure(figsize=(12, 4))
    plt.plot(p["row_index"], p["spread"])
    plt.title(f"{product} — spread over time")
    plt.xlabel("row index")
    plt.ylabel("spread")
    safe_savefig(f"{product}_05_spread_series.png")

    # 6. spread histogram
    plt.figure(figsize=(7, 4))
    plt.hist(p["spread"].dropna(), bins=30)
    plt.title(f"{product} — spread histogram")
    plt.xlabel("spread")
    plt.ylabel("count")
    safe_savefig(f"{product}_06_spread_hist.png")

    # 7. initial book snapshot
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
        plt.title(f"{product} — order book snapshot at first valid row")
        plt.xlabel("price")
        plt.ylabel("volume")
        safe_savefig(f"{product}_07_book_snapshot.png")

    # 8. imbalance time series
    plt.figure(figsize=(12, 4))
    plt.plot(p["row_index"], p["imbalance_l1"])
    plt.title(f"{product} — order book imbalance (level 1)")
    plt.xlabel("row index")
    plt.ylabel("imbalance_l1")
    safe_savefig(f"{product}_08_imbalance_series.png")

    # 9. mid with trades overlaid
    plt.figure(figsize=(12, 5))
    plt.plot(p["row_index"], p["mid_price"], label="mid")

    # map trades to nearest row index for plotting
    p_plot = p[["day", "timestamp", "row_index", "mid_price"]].copy().sort_values(["day", "timestamp"])
    t_plot = t.copy().sort_values(["day", "timestamp"])

    merged_plot = merge_trades_with_prices(t_plot, p_plot)

    for label, marker in [("buy_aggr", "^"), ("sell_aggr", "v"), ("mid", "o")]:
        sub = merged_plot[merged_plot["aggressor"] == label]
        if len(sub) > 0:
            plt.scatter(sub["row_index"], sub["price"], s=14, marker=marker, label=label)

    plt.title(f"{product} — mid price with trades overlaid")
    plt.xlabel("row index")
    plt.ylabel("price")
    plt.legend()
    safe_savefig(f"{product}_09_mid_with_trades.png")

    # 10. trade price minus mid histogram
    if len(t) > 0:
        plt.figure(figsize=(7, 4))
        plt.hist(t["trade_minus_mid"].dropna(), bins=30)
        plt.axvline(0, linestyle="--")
        plt.title(f"{product} — trade price minus mid")
        plt.xlabel("trade price - mid")
        plt.ylabel("count")
        safe_savefig(f"{product}_10_trade_minus_mid_hist.png")

    # 11. trade quantity histogram
    if len(t) > 0:
        plt.figure(figsize=(7, 4))
        plt.hist(t["quantity"].dropna(), bins=min(20, t["quantity"].nunique()))
        plt.title(f"{product} — trade quantity histogram")
        plt.xlabel("quantity")
        plt.ylabel("count")
        safe_savefig(f"{product}_11_trade_quantity_hist.png")

    # 12. interarrival time histogram
    if len(t) > 1:
        t = t.sort_values(["day", "timestamp"]).copy()
        t["interarrival"] = t.groupby("day")["timestamp"].diff()
        plt.figure(figsize=(7, 4))
        plt.hist(t["interarrival"].dropna(), bins=30, log=True)
        plt.title(f"{product} — interarrival time (log y)")
        plt.xlabel("ticks between trades")
        plt.ylabel("count")
        safe_savefig(f"{product}_12_interarrival_hist.png")

    # 13. autocorrelation of delta mid
    acf_vals = autocorr_series(p["dmid"].dropna(), max_lag=ACF_LAGS)
    plt.figure(figsize=(10, 4))
    plt.bar(acf_vals.index, acf_vals.values, width=0.8)
    plt.axhline(0, linewidth=1)
    plt.title(f"{product} — autocorrelation of Δmid")
    plt.xlabel("lag")
    plt.ylabel("autocorrelation")
    safe_savefig(f"{product}_13_dmid_acf.png")

    # 14. rolling std
    plt.figure(figsize=(12, 5))
    for w in ROLL_WINDOWS:
        plt.plot(p["dmid"].rolling(w).std(), label=f"window={w}")
    plt.title(f"{product} — rolling std of Δmid")
    plt.xlabel("row index")
    plt.ylabel("rolling std")
    plt.legend()
    safe_savefig(f"{product}_14_rolling_std.png")

    # 15. imbalance vs future return scatter
    plt.figure(figsize=(7, 5))
    sample = p[["imbalance_l1", "fwd_ret_1"]].dropna()
    if len(sample) > 3000:
        sample = sample.sample(3000, random_state=42)
    plt.scatter(sample["imbalance_l1"], sample["fwd_ret_1"], s=8, alpha=0.5)
    plt.axhline(0, linewidth=1)
    plt.axvline(0, linewidth=1)
    plt.title(f"{product} — imbalance(l1) vs future Δmid (+1)")
    plt.xlabel("imbalance_l1")
    plt.ylabel("future Δmid (+1)")
    safe_savefig(f"{product}_15_imbalance_vs_fwd1_scatter.png")

    # 16. imbalance bucket predictiveness
    tmp = p[["imbalance_l1"] + [f"fwd_ret_{h}" for h in FWD_RET_LAGS]].dropna().copy()
    if len(tmp) > 20:
        tmp["imb_bucket"] = pd.qcut(tmp["imbalance_l1"], q=10, duplicates="drop")
        bucket_means = tmp.groupby("imb_bucket")[[f"fwd_ret_{h}" for h in FWD_RET_LAGS]].mean()

        bucket_means.plot(kind="bar", figsize=(10, 4))
        plt.title(f"{product} — average future return by imbalance decile")
        plt.xlabel("imbalance decile")
        plt.ylabel("average future return")
        safe_savefig(f"{product}_16_imbalance_bucket_predictiveness.png")

        bucket_means.to_csv(OUTPUT_DIR / f"{product}_imbalance_bucket_predictiveness.csv")

    # 17. microprice signal
    plt.figure(figsize=(7, 4))
    plt.hist(p["microprice_minus_mid"].dropna(), bins=40)
    plt.title(f"{product} — microprice minus mid")
    plt.xlabel("microprice - mid")
    plt.ylabel("count")
    safe_savefig(f"{product}_17_microprice_minus_mid_hist.png")

    # 18. lag autocorrelation table
    acf_table = pd.DataFrame({
        "lag": range(1, 11),
        "autocorr_dmid": [p["dmid"].autocorr(lag=i) for i in range(1, 11)]
    }).set_index("lag")
    print(f"\n{product} return autocorrelation (lags 1-10):")
    print(acf_table.round(4))
    acf_table.to_csv(OUTPUT_DIR / f"{product}_acf_lag1_to_10.csv")

    # 19. signal correlation table
    signal_corr = {
        "corr(imbalance_l1, fwd_ret_1)": corr_safe(p["imbalance_l1"], p["fwd_ret_1"]),
        "corr(imbalance_l1, fwd_ret_5)": corr_safe(p["imbalance_l1"], p["fwd_ret_5"]),
        "corr(imbalance_l1, fwd_ret_10)": corr_safe(p["imbalance_l1"], p["fwd_ret_10"]),
        "corr(microprice_minus_mid, fwd_ret_1)": corr_safe(p["microprice_minus_mid"], p["fwd_ret_1"]),
        "corr(spread, abs(dmid))": corr_safe(p["spread"], p["dmid"].abs()),
    }
    signal_corr_df = pd.DataFrame(signal_corr, index=[product]).T
    print(f"\n{product} signal diagnostics:")
    print(signal_corr_df.round(4))
    signal_corr_df.to_csv(OUTPUT_DIR / f"{product}_signal_diagnostics.csv")

    # 20. per-day summary
    day_summary = p.groupby("day").agg(
        mid_mean=("mid_price", "mean"),
        mid_std=("mid_price", "std"),
        spread_mean=("spread", "mean"),
        dmid_std=("dmid", "std"),
        imb_mean=("imbalance_l1", "mean"),
        n_obs=("mid_price", "size"),
    )
    print(f"\n{product} day summary:")
    print(day_summary.round(4))
    day_summary.to_csv(OUTPUT_DIR / f"{product}_day_summary.csv")


# =========================
# CROSS-PRODUCT ANALYSIS
# =========================
print_header("CROSS-PRODUCT ANALYSIS")

# Concatenate by row order per product for simple comparison
ret_wide = []
for product in products:
    p = prices_feat[prices_feat["product"] == product].copy().sort_values(["day", "timestamp"])
    s = p[["day", "timestamp", "dmid"]].copy()
    s = s.rename(columns={"dmid": product})
    ret_wide.append(s)

if len(ret_wide) >= 2:
    from functools import reduce

    wide = reduce(lambda left, right: pd.merge(left, right, on=["day", "timestamp"], how="outer"), ret_wide)
    wide = wide.sort_values(["day", "timestamp"]).reset_index(drop=True)

    # same-time return correlation
    corr_mat = wide[products].corr()
    print("\nReturn correlation matrix:")
    print(corr_mat.round(4))
    corr_mat.to_csv(OUTPUT_DIR / "cross_product_return_corr.csv")

    # lead-lag correlation table
    lead_lag_rows = []
    for a in products:
        for b in products:
            if a == b:
                continue
            for lag in range(-5, 6):
                # corr(a_t, b_{t+lag})
                if lag < 0:
                    c = corr_safe(wide[a], wide[b].shift(-lag))
                else:
                    c = corr_safe(wide[a], wide[b].shift(lag))
                lead_lag_rows.append({
                    "product_a": a,
                    "product_b": b,
                    "lag_applied_to_b": lag,
                    "corr": c
                })
    lead_lag_df = pd.DataFrame(lead_lag_rows)
    lead_lag_df.to_csv(OUTPUT_DIR / "lead_lag_correlations.csv", index=False)

    print("\nBest lead-lag relationships by absolute correlation:")
    best_ll = (
        lead_lag_df.assign(abs_corr=lambda x: x["corr"].abs())
        .sort_values("abs_corr", ascending=False)
        .head(20)
    )
    print(best_ll.round(4))

    # simple visual of price series normalised
    plt.figure(figsize=(12, 5))
    for product in products:
        p = prices_feat[prices_feat["product"] == product].copy().sort_values(["day", "timestamp"])
        x = p["mid_price"]
        z = (x - x.mean()) / (x.std() if x.std() != 0 else 1)
        plt.plot(z.reset_index(drop=True), label=product)
    plt.title("Normalised mid prices across products")
    plt.xlabel("row index")
    plt.ylabel("z-score")
    plt.legend()
    safe_savefig("cross_product_normalised_mid_prices.png")


# =========================
# TRADE FLOW SUMMARY
# =========================
print_header("TRADE FLOW SUMMARY")

flow_rows = []
for product in products:
    t = trades_feat[trades_feat["symbol"] == product].copy()
    if len(t) == 0:
        continue

    row = {
        "product": product,
        "n_trades": len(t),
        "avg_quantity": t["quantity"].mean(),
        "median_quantity": t["quantity"].median(),
        "buy_aggr_count": (t["aggressor"] == "buy_aggr").sum(),
        "sell_aggr_count": (t["aggressor"] == "sell_aggr").sum(),
        "mid_count": (t["aggressor"] == "mid").sum(),
        "trade_minus_mid_mean": t["trade_minus_mid"].mean(),
        "trade_minus_mid_std": t["trade_minus_mid"].std(),
    }
    flow_rows.append(row)

flow_df = pd.DataFrame(flow_rows).set_index("product")
print(flow_df.round(4))
flow_df.to_csv(OUTPUT_DIR / "trade_flow_summary.csv")


# =========================
# FINAL NOTEBOOK-FRIENDLY OUTPUT
# =========================
print_header("DONE")
print(f"All charts and tables saved in: {OUTPUT_DIR}")

print("""
Suggested interpretation order:
1. summary_stats_by_product.csv
2. each product's mid/spread/Δmid charts
3. *_signal_diagnostics.csv
4. imbalance bucket predictiveness
5. lead_lag_correlations.csv

What to look for:
- Stable fair value / strong mean reversion -> market making or fade moves
- Positive imbalance -> positive future return -> microstructure alpha
- Wide spread with low adverse selection -> passive quoting edge
- Strong lead-lag across products -> stat arb / cross-impact
- Trade price consistently far from mid -> aggression / informed flow signal
""")
