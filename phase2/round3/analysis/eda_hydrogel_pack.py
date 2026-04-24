"""
EDA script for HYDROGEL_PACK — IMC Prosperity 4, Round 3
Run from repo root: python phase2/round3/analysis/eda_hydrogel_pack.py
"""

import os
import sys
import numpy as np
import pandas as pd
from scipy import stats

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
PRICES_CSV = os.path.join(BASE, "phase2/round3/data/prices_round_3_combined.csv")
TRADES_CSV = os.path.join(BASE, "phase2/round3/data/trades_round_3_combined.csv")

# ── Load & filter ──────────────────────────────────────────────────────────────
p_all = pd.read_csv(PRICES_CSV, sep=';')
t_all = pd.read_csv(TRADES_CSV, sep=';')

p = p_all[p_all['product'] == 'HYDROGEL_PACK'].copy().reset_index(drop=True)
t = t_all[t_all['symbol'] == 'HYDROGEL_PACK'].copy().reset_index(drop=True)

# Coerce numeric columns
num_price_cols = [
    'bid_price_1','bid_volume_1','bid_price_2','bid_volume_2','bid_price_3','bid_volume_3',
    'ask_price_1','ask_volume_1','ask_price_2','ask_volume_2','ask_price_3','ask_volume_3',
    'mid_price'
]
for c in num_price_cols:
    p[c] = pd.to_numeric(p[c], errors='coerce')

p['timestamp'] = pd.to_numeric(p['timestamp'], errors='coerce')
t['timestamp'] = pd.to_numeric(t['timestamp'], errors='coerce')
t['price'] = pd.to_numeric(t['price'], errors='coerce')
t['quantity'] = pd.to_numeric(t['quantity'], errors='coerce')

print("=" * 70)
print("HYDROGEL_PACK EDA — IMC Prosperity 4, Round 3")
print(f"  Price rows: {len(p)}  |  Trade rows: {len(t)}")
print(f"  Days: {sorted(p['day'].unique())}")
print("=" * 70)

# ══════════════════════════════════════════════════════════════════════════════
# 1. BASIC PRICE STATS
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("SECTION 1: BASIC PRICE STATS")
print("─" * 70)

# Spread
p['spread'] = p['ask_price_1'] - p['bid_price_1']

print("\n  Per-day mid_price stats:")
for day, grp in p.groupby('day'):
    mid = grp['mid_price'].dropna()
    sp  = grp['spread'].dropna()
    print(f"  Day {day}: mean={mid.mean():.2f}  std={mid.std():.2f}  "
          f"min={mid.min():.2f}  max={mid.max():.2f}  range={mid.max()-mid.min():.2f}")
    print(f"         spread: mean={sp.mean():.2f}  median={sp.median():.2f}  "
          f"std={sp.std():.2f}  p10={sp.quantile(0.1):.2f}  p90={sp.quantile(0.9):.2f}")

mid_all = p['mid_price'].dropna()
sp_all  = p['spread'].dropna()
print(f"\n  OVERALL mid_price: mean={mid_all.mean():.2f}  std={mid_all.std():.2f}  "
      f"min={mid_all.min():.2f}  max={mid_all.max():.2f}")
print(f"  OVERALL spread:    mean={sp_all.mean():.2f}  median={sp_all.median():.2f}  "
      f"std={sp_all.std():.2f}  p10={sp_all.quantile(0.1):.2f}  p90={sp_all.quantile(0.9):.2f}")

# Stationarity test (per-day means)
day_means = p.groupby('day')['mid_price'].mean()
print(f"\n  Day-by-day means: {dict(day_means.round(2))}")
mean_drift = day_means.iloc[-1] - day_means.iloc[0]
print(f"  Day 0→2 drift: {mean_drift:+.2f} ticks  →  "
      + ("TRENDING" if abs(mean_drift) > 5 * mid_all.std() / np.sqrt(len(p)) else
         "STATIONARY" if abs(mean_drift) < mid_all.std() else "MODERATE DRIFT"))

# ══════════════════════════════════════════════════════════════════════════════
# 2. ACF STRUCTURE
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("SECTION 2: ACF STRUCTURE (delta_mid)")
print("─" * 70)

p['delta_mid'] = p['mid_price'].diff()
dm = p['delta_mid'].dropna().values

def acf_lag(series, lag):
    s = series[~np.isnan(series)]
    if len(s) <= lag:
        return np.nan
    return np.corrcoef(s[:-lag], s[lag:])[0, 1]

print("\n  Autocorrelations of delta_mid:")
for lag in range(1, 11):
    r = acf_lag(dm, lag)
    flag = ""
    if lag == 1:
        if r < -0.3:
            flag = " ← STRONG MEAN-REVERSION"
        elif r > 0.3:
            flag = " ← STRONG MOMENTUM"
        elif abs(r) < 0.05:
            flag = " ← RANDOM WALK"
    print(f"  Lag {lag:2d}: {r:+.4f}{flag}")

lag1 = acf_lag(dm, 1)

# ══════════════════════════════════════════════════════════════════════════════
# 3. BOOK SHAPE
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("SECTION 3: BOOK SHAPE")
print("─" * 70)

for side, lvls in [("BID", ['bid_volume_1','bid_volume_2','bid_volume_3']),
                    ("ASK", ['ask_volume_1','ask_volume_2','ask_volume_3'])]:
    print(f"\n  {side} volumes:")
    for col in lvls:
        v = p[col].dropna()
        populated = v[v > 0]
        fill_rate = len(populated) / len(p) * 100
        if len(populated) > 0:
            print(f"    {col}: mean={v.fillna(0).mean():.2f}  std={v.fillna(0).std():.2f}  "
                  f"fill_rate={fill_rate:.1f}%  p50={populated.median():.0f}")
        else:
            print(f"    {col}: not populated")

# Total depth ratio
p['total_bid_depth'] = p[['bid_volume_1','bid_volume_2','bid_volume_3']].fillna(0).sum(axis=1)
p['total_ask_depth'] = p[['ask_volume_1','ask_volume_2','ask_volume_3']].fillna(0).sum(axis=1)
ratio = p['total_bid_depth'].mean() / p['total_ask_depth'].mean()
print(f"\n  Mean total bid depth: {p['total_bid_depth'].mean():.2f}")
print(f"  Mean total ask depth: {p['total_ask_depth'].mean():.2f}")
print(f"  Bid/Ask depth ratio:  {ratio:.4f}")

# ══════════════════════════════════════════════════════════════════════════════
# 4. OBI SIGNAL
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("SECTION 4: ORDER BOOK IMBALANCE (OBI) SIGNAL")
print("─" * 70)

p['OBI'] = (p['total_bid_depth'] - p['total_ask_depth']) / (p['total_bid_depth'] + p['total_ask_depth'])
p['OBI'] = p['OBI'].replace([np.inf, -np.inf], np.nan)

# Forward returns
for h in [1, 5, 10]:
    p[f'fwd_ret_{h}'] = p['mid_price'].shift(-h) - p['mid_price']

def ols_report(x, y, label):
    mask = ~(np.isnan(x) | np.isnan(y))
    xm, ym = x[mask], y[mask]
    if len(xm) < 10:
        print(f"  {label}: insufficient data")
        return
    slope, intercept, r, p_val, se = stats.linregress(xm, ym)
    t_stat = slope / se
    corr = np.corrcoef(xm, ym)[0, 1]
    print(f"  {label}: beta={slope:+.4f}  t={t_stat:+.2f}  p={p_val:.4f}  corr={corr:+.4f}")
    return slope, corr

print("\n  OBI regression vs forward returns:")
slopes = {}
for h in [1, 5, 10]:
    res = ols_report(p['OBI'].values, p[f'fwd_ret_{h}'].values, f"OBI ~ fwd_ret_{h:2d}")
    if res:
        slopes[h] = res[0]

# Quintile bucketing
obi_valid = p[['OBI','fwd_ret_1']].dropna()
obi_valid = obi_valid.copy()
obi_valid['obi_q'] = pd.qcut(obi_valid['OBI'], q=5, labels=False, duplicates='drop')
print("\n  OBI quintile buckets (mean fwd_ret_1):")
bkt = obi_valid.groupby('obi_q')['fwd_ret_1'].agg(['mean','count'])
for idx, row in bkt.iterrows():
    bar = "#" * int(abs(row['mean']) * 3)
    sign = "+" if row['mean'] > 0 else ""
    print(f"  Q{int(idx)+1} (n={int(row['count']):5d}): {sign}{row['mean']:.4f}  {bar}")

avg_slope_1_10 = np.mean([v for k,v in slopes.items()])
obi_direction = "DIRECTIONAL (positive beta)" if avg_slope_1_10 > 0 else "CONTRARIAN (negative beta)"
print(f"\n  OBI signal direction: {obi_direction}  (avg beta h=1..10: {avg_slope_1_10:+.4f})")

# ══════════════════════════════════════════════════════════════════════════════
# 5. MICRO-PRICE Z-SCORE SIGNAL
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("SECTION 5: MICRO-PRICE Z-SCORE SIGNAL")
print("─" * 70)

# Micro-price
p['micro'] = np.where(
    (p['bid_volume_1'] + p['ask_volume_1']) > 0,
    (p['bid_price_1'] * p['ask_volume_1'] + p['ask_price_1'] * p['bid_volume_1'])
    / (p['bid_volume_1'] + p['ask_volume_1']),
    np.nan
)
p['residual'] = p['micro'] - p['mid_price']

# Expanding Z-score with 30-obs warmup
exp_mean = p['residual'].expanding(min_periods=30).mean()
exp_std  = p['residual'].expanding(min_periods=30).std()
p['Z'] = (p['residual'] - exp_mean) / exp_std
p['Z'] = p['Z'].replace([np.inf, -np.inf], np.nan)

print("\n  Micro-price residual stats:")
res_valid = p['residual'].dropna()
print(f"  residual: mean={res_valid.mean():.4f}  std={res_valid.std():.4f}  "
      f"min={res_valid.min():.4f}  max={res_valid.max():.4f}")

z_valid = p[['Z','fwd_ret_10']].dropna()
if len(z_valid) > 30:
    r_z, p_z = stats.pearsonr(z_valid['Z'], z_valid['fwd_ret_10'])
    n_z = len(z_valid)
    t_z = r_z * np.sqrt(n_z - 2) / np.sqrt(1 - r_z**2)
    print(f"\n  corr(Z, fwd_ret_10) = {r_z:+.4f}  t={t_z:+.2f}  p={p_z:.4f}  n={n_z}")

    z_valid2 = z_valid.copy()
    z_valid2['z_q'] = pd.qcut(z_valid2['Z'], q=5, labels=False, duplicates='drop')
    print("\n  Z quintile buckets (mean fwd_ret_10):")
    bkt_z = z_valid2.groupby('z_q')['fwd_ret_10'].agg(['mean','count'])
    for idx, row in bkt_z.iterrows():
        bar = "#" * int(abs(row['mean']) * 2)
        sign = "+" if row['mean'] > 0 else ""
        print(f"  Z-Q{int(idx)+1} (n={int(row['count']):5d}): {sign}{row['mean']:.4f}  {bar}")

    z_dir = "MOMENTUM (Z>0 → price up)" if r_z > 0 else "CONTRARIAN (Z>0 → price down)"
    print(f"\n  Micro-Z direction: {z_dir}")
else:
    print("  Insufficient data for Z analysis")
    r_z = np.nan

# ══════════════════════════════════════════════════════════════════════════════
# 6. TRADE FLOW ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("SECTION 6: TRADE FLOW ANALYSIS")
print("─" * 70)

# Trade stats
print(f"\n  Total trades: {len(t)}")
print(f"  Trades per day: {t.groupby('day').size().to_dict()}")
qty = t['quantity'].dropna()
print(f"  Quantity: mean={qty.mean():.2f}  median={qty.median():.2f}  std={qty.std():.2f}  sum={qty.sum():.0f}")

# Inter-trade intervals
t_sorted = t.sort_values(['day','timestamp'])
t_sorted['dt'] = t_sorted.groupby('day')['timestamp'].diff()
iti = t_sorted['dt'].dropna()
print(f"  Inter-trade interval: mean={iti.mean():.1f}  median={iti.median():.1f}  std={iti.std():.1f} ticks")

# Trades per 1000 timestamps
ts_per_day = 10000  # approximate
tpd = t.groupby('day').size().mean()
tpt = tpd / ts_per_day * 1000
print(f"  Trades per 1000 timestamps: {tpt:.2f}")

# Classify buy/sell trades by joining to price book
# Merge on (day, timestamp) — use merge_asof for nearest match
p_sorted = p.sort_values(['day','timestamp'])
t_sorted2 = t.sort_values(['day','timestamp'])

trade_classified = []
for day_val in t['day'].unique():
    p_day = p_sorted[p_sorted['day'] == day_val][['timestamp','bid_price_1','ask_price_1']].dropna()
    t_day = t_sorted2[t_sorted2['day'] == day_val]
    if len(p_day) == 0 or len(t_day) == 0:
        continue
    merged = pd.merge_asof(t_day.sort_values('timestamp'), p_day, on='timestamp', direction='nearest')
    merged['trade_type'] = np.where(
        merged['price'] >= merged['ask_price_1'], 'buy',
        np.where(merged['price'] <= merged['bid_price_1'], 'sell', 'mid')
    )
    trade_classified.append(merged)

tc = pd.concat(trade_classified, ignore_index=True) if trade_classified else pd.DataFrame()

if len(tc) > 0:
    type_counts = tc['trade_type'].value_counts()
    print(f"\n  Trade type classification:")
    print(f"  {dict(type_counts)}")

    # Forward returns after each trade — per-day merge_asof to avoid global sort issue
    p_fwd = p_sorted[['day','timestamp','fwd_ret_10']].dropna()
    tc2_parts = []
    for dv in tc['day'].unique():
        a = tc[tc['day']==dv].sort_values('timestamp')
        b = p_fwd[p_fwd['day']==dv].sort_values('timestamp')
        merged_fwd = pd.merge_asof(a, b, on='timestamp', direction='nearest', suffixes=('','_pfwd'))
        tc2_parts.append(merged_fwd)
    tc2 = pd.concat(tc2_parts, ignore_index=True) if tc2_parts else pd.DataFrame()

    for ttype in ['buy', 'sell']:
        grp = tc2[tc2['trade_type'] == ttype]['fwd_ret_10'].dropna()
        if len(grp) > 2:
            t_stat_flow, p_val_flow = stats.ttest_1samp(grp, 0)
            print(f"  {ttype.upper()} trades (n={len(grp)}): mean_fwd_10={grp.mean():+.4f}  "
                  f"t={t_stat_flow:+.2f}  p={p_val_flow:.4f}  "
                  + ("INFORMED" if p_val_flow < 0.05 and abs(grp.mean()) > 0.3 else "NOT-INFORMED"))

# ══════════════════════════════════════════════════════════════════════════════
# 7. INTRADAY PATTERN
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("SECTION 7: INTRADAY PATTERN")
print("─" * 70)

for day_val in sorted(p['day'].unique()):
    grp = p[p['day'] == day_val][['timestamp','mid_price']].dropna()
    if len(grp) < 10:
        continue
    x = grp['timestamp'].values
    y = grp['mid_price'].values
    slope, intercept, r, pv, _ = stats.linregress(x, y)
    day_range = y.max() - y.min()
    print(f"  Day {day_val}: slope={slope:+.6f} ticks/ts  R²={r**2:.4f}  "
          f"mid range={y.min():.1f}–{y.max():.1f} ({day_range:.1f} ticks)  "
          f"start={y[:100].mean():.1f}  end={y[-100:].mean():.1f}")

# ══════════════════════════════════════════════════════════════════════════════
# 8. SPREAD OVER TIME
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("SECTION 8: SPREAD OVER TIME")
print("─" * 70)

for day_val in sorted(p['day'].unique()):
    grp = p[p['day'] == day_val]['spread'].dropna()
    n = len(grp)
    n10 = max(1, n // 10)
    open_sp  = grp.iloc[:n10].mean()
    close_sp = grp.iloc[-n10:].mean()
    ratio_sp = close_sp / open_sp if open_sp > 0 else np.nan
    print(f"  Day {day_val}: open_spread={open_sp:.2f}  close_spread={close_sp:.2f}  "
          f"ratio={ratio_sp:.3f}  {'WIDENS' if ratio_sp > 1.02 else 'NARROWS' if ratio_sp < 0.98 else 'STABLE'}")

sp_all_grp = p['spread'].dropna()
n_all = len(sp_all_grp)
n10_all = max(1, n_all // 10)
open_all  = sp_all_grp.iloc[:n10_all].mean()
close_all = sp_all_grp.iloc[-n10_all:].mean()
print(f"  OVERALL: open={open_all:.2f}  close={close_all:.2f}  ratio={close_all/open_all:.3f}")

# ══════════════════════════════════════════════════════════════════════════════
# 9. HIDDEN PATTERNS
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("SECTION 9: HIDDEN PATTERNS")
print("─" * 70)

# 10 intraday blocks per day
print("\n  Mid-price mean per time block (10 blocks per day):")
for day_val in sorted(p['day'].unique()):
    grp = p[p['day'] == day_val][['timestamp','mid_price']].dropna()
    ts_min, ts_max = grp['timestamp'].min(), grp['timestamp'].max()
    bins = np.linspace(ts_min, ts_max, 11)
    grp['block'] = pd.cut(grp['timestamp'], bins=bins, labels=False)
    block_means = grp.groupby('block')['mid_price'].mean()
    vals = "  ".join([f"B{int(i)}={v:.0f}" for i, v in block_means.items()])
    print(f"  Day {day_val}: {vals}")

# Regime volatility detection (3 segments)
print("\n  Volatility by segment (3 equal segments, std of delta_mid):")
for day_val in sorted(p['day'].unique()):
    grp = p[p['day'] == day_val]['delta_mid'].dropna()
    n_seg = len(grp) // 3
    segs = [grp.iloc[:n_seg], grp.iloc[n_seg:2*n_seg], grp.iloc[2*n_seg:]]
    stds = [f"S{i+1}={s.std():.3f}" for i, s in enumerate(segs)]
    print(f"  Day {day_val}: {' | '.join(stds)}")

# ══════════════════════════════════════════════════════════════════════════════
# 10. STRATEGY HYPOTHESIS BLOCK
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "═" * 70)
print("SECTION 10: STRATEGY HYPOTHESIS")
print("═" * 70)

# Determine fair value type
day_means_list = p.groupby('day')['mid_price'].mean().values
day_drift = day_means_list[-1] - day_means_list[0] if len(day_means_list) >= 2 else 0
mid_std_overall = p['mid_price'].std()

if abs(day_drift) > 200:
    fv_type = "trending"
elif abs(day_drift) > 50:
    fv_type = "moderate drift"
else:
    fv_type = "fixed / stationary"

# Spread regime
sp_mean = p['spread'].mean()
spread_regime = "tight (<5)" if sp_mean < 5 else "medium (5–15)" if sp_mean < 15 else "wide (>15)"

# OBI direction
if 'avg_slope_1_10' in dir():
    pass
try:
    obi_s = avg_slope_1_10
    obi_sig = "directional" if obi_s > 0.05 else "contrarian" if obi_s < -0.05 else "weak"
except:
    obi_sig = "unknown"

# Z direction
try:
    z_sig = "momentum" if r_z > 0.1 else "contrarian" if r_z < -0.1 else "weak"
except:
    z_sig = "unknown"

# Trade flow
buy_informed = False
sell_informed = False
if len(tc) > 0:
    try:
        buy_grp = tc2[tc2['trade_type'] == 'buy']['fwd_ret_10'].dropna()
        sell_grp = tc2[tc2['trade_type'] == 'sell']['fwd_ret_10'].dropna()
        if len(buy_grp) > 2:
            _, pv_b = stats.ttest_1samp(buy_grp, 0)
            buy_informed = pv_b < 0.05 and abs(buy_grp.mean()) > 0.3
        if len(sell_grp) > 2:
            _, pv_s = stats.ttest_1samp(sell_grp, 0)
            sell_informed = pv_s < 0.05 and abs(sell_grp.mean()) > 0.3
    except:
        pass
flow_sig = ("buy-informed" if buy_informed and not sell_informed else
            "sell-informed" if sell_informed and not buy_informed else
            "both" if buy_informed and sell_informed else "neither")

# ACF lag-1
lag1_str = f"{lag1:+.3f}"
acf_regime = ("mean-reverting" if lag1 < -0.3 else
              "momentum" if lag1 > 0.3 else "random walk / weak")

# Position limit guess
limit_guess = "80 (assume same as R1/R2)"

# Recommended approach
if fv_type == "trending":
    rec = "Holt trend MM (like IPR)"
elif lag1 < -0.3:
    rec = "Fixed-FV MM with inventory skew (like ASH)"
else:
    rec = "Fixed-FV MM"

print(f"""
=== HYDROGEL_PACK HYPOTHESIS ===
Fair value type:        {fv_type}
  Day means: {dict(zip(range(len(day_means_list)), [round(x,1) for x in day_means_list]))}
  Day 0→2 drift: {day_drift:+.1f} ticks  |  Overall std: {mid_std_overall:.2f}
Spread regime:          {spread_regime}  (mean={sp_mean:.2f}, p10={p['spread'].quantile(0.1):.1f}, p90={p['spread'].quantile(0.9):.1f})
ACF lag-1:              {lag1_str}  → {acf_regime}
OBI signal:             {obi_sig}  (avg beta h=1..10: {avg_slope_1_10:+.4f})
Micro-Z signal:         {z_sig}  (corr(Z, fwd_10)={r_z:+.4f} if available)
Trade flow:             {flow_sig}
  Trades/day: {t.groupby('day').size().mean():.0f}  |  Avg qty: {t['quantity'].mean():.1f}
Recommended approach:   {rec}
Position limit guess:   {limit_guess}
Confidence:             medium — 3 days data, signals cross-validated
""")

print("=" * 70)
print("EDA COMPLETE")
print("=" * 70)
