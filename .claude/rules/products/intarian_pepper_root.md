## INTARIAN_PEPPER_ROOT
- Fair value: **NOT stable** — trends +1000/day linearly (day -2 ~10500, day -1 ~11500, day 0 ~12500)
- Position limit: 80
- Intraday ramp: ~+917 ticks across 12 time blocks per day (monotonic linear drift)
- Spread: mean 13.1, median 13.0, std 2.6, p10=11, p90=16 — widens slightly as price rises
- Tick volatility (std of delta-mid around the drift): 3.10

### ACF Structure
- Lag-1 autocorrelation: **-0.501** (strong mean-reversion at lag 1, same as ASH_COATED_OSMIUM)
- Lags 2–10: near zero (max 0.009) — pure noise beyond lag 1

### Imbalance Signal
- corr(imbalance, fwd_ret_1) = **0.385** — similar strength to ASH_COATED_OSMIUM
- Bucketed: negative bucket → -1.66, positive → +1.89
- Signal *strengthens* at longer horizons (fwd_ret_10 corr = 0.393) — trend direction partially captured by persistent imbalance

### Key Pattern: Intraday Linear Ramp
- Price rises ~+917 ticks per day in a near-perfect linear ramp
- Mid-mean by time block rises steadily from block 0 to block 11
- This is not noise — it is structural across all 3 days
- **Do NOT use a fixed FV** — plain EMA(0.05) lags a linear ramp by ~1.9 ticks; use Holt's instead

### Strategy
- Fair value via **Holt's linear double exponential smoothing**:
  - `level = ALPHA * mid + (1 - ALPHA) * (level + trend)`
  - `trend = BETA * (level - prev_level) + (1 - BETA) * trend`
  - `fv = level + trend` (one-step-ahead forecast)
  - Init: `level = first_mid`, `trend = 0.0`; persisted in traderData
  - No hardcoded base — converges to actual slope dynamically
- Residual `r = mid - fv`: mean-reverts fast; drives quote skew
  - r > 1.5 → res_skew = -1 (price rich); r < -1.5 → res_skew = +1 (price cheap)
- Aggressively take: ask < FV-1 → buy; bid > FV+1 → sell
- Passive MM: `int(FV) ± EDGE + combined_skew`
  - `combined_skew = inv_skew + res_skew + imb_tilt`
  - Asymmetric short penalty: if pos < 0 → add +1 (shorts are extra costly in uptrend)
- Imbalance tilt: |imb| > 0.25 → ±1 tick (tie-breaker only, not independent alpha from residual)
- Size scale: buy side `max(0.3, 1.0 - pos/limit * 0.5)` when pos ≥ 0 (lean longer); sell `max(0.3, 1.0 - |pos/limit| * 0.7)`

### Constants
```python
IPR_LIMIT = 80
IPR_EDGE = 3
IPR_ALPHA = 0.05
IPR_BETA = 0.1
IPR_IMB_THRESHOLD = 0.25
```

### Confidence
- Tick-level mean-reversion and imbalance: high confidence
- +1000/day trend: medium confidence — strikingly clean across 3 days but may not persist or may change magnitude/direction. Monitor each round.

### WARNING
Do NOT treat this like EMERALDS. The price drifts +1000/day. A fixed FV will cause massive adverse selection as price moves away from the anchor.
