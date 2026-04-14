## ASH_COATED_OSMIUM
- Fair value: **10,000 exactly** (mean 10000.2, std 5.35 across 3 days — stationary)
- Position limit: 80
- Mid-price range: 9977–10023 across all days
- Spread: mean 16.2, median 16.0, std 2.6, p10=16, p90=19 — very tight and stable
- Tick volatility (std of delta-mid): 3.73

### ACF Structure
- Lag-1 autocorrelation: **-0.495** (strong mean-reversion at lag 1)
- Lags 2–10: near zero (max 0.005) — pure noise beyond lag 1

### Imbalance Signal
- corr(imbalance, fwd_ret_1) = **0.381** — predictive and robust
- Bucketed: most-negative imbalance → -2.09 next tick; most-positive → +2.12
- Signal persists to lag 5 and 10 (0.367, 0.352) — usable for quote skew

### Hidden Pattern
- No hidden pattern. No time-block drift, no trending, no regime shifts.
- Behaves identically to EMERALDS — fixed FV, mean-reverting noise.

### Strategy
- Fixed FV market-making at FV=10000 (hardcoded, never drifts)
- Aggressively take: ask < 10000 (buy), bid > 10000 (sell)
- Passive MM: post at int(FV) ± EDGE, skewed by inventory ratio
- Imbalance tilt: if |imbalance| > 0.25, shift quotes 1 tick toward imbalance direction
- Size scale: `max(0.3, 1.0 - abs(pos/limit) * 0.7)`

### Constants
```python
ASH_FV = 10000
ASH_LIMIT = 80
ASH_EDGE = 2
ASH_IMB_THRESHOLD = 0.25
```

### Confidence
High — three-day consistency, clean ACF, strong imbalance signal.
