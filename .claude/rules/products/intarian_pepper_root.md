## INTARIAN_PEPPER_ROOT
- Fair value: **NOT stable** — trends +1000/day linearly (day -2 ~10500, day -1 ~11500, day 0 ~12500)
- Position limit: 80 (hard), recommended MM inventory cap: long +40 / short -5 (soft)
- Intraday ramp: ~+1000 ticks per day, monotonic, consistent across all 3 days (<3σ variance)
- Spread: mean 13.0, widens intraday (12.6 at open → 13.5 at close, +7%)
- Tick volatility (std of Δmid): 3.10
- ~330 trades/day, avg 5 units each

---

### ACF Structure
- Lag-1 autocorrelation: **-0.501** (strong mean-reversion at lag 1)
- Lags 2–10: near zero — pure noise beyond lag 1

---

### Signal 1: OBI — CONTRARIAN
**Construct**: `OBI = (total_bid_vol_3L - total_ask_vol_3L) / (total_bid_vol_3L + total_ask_vol_3L)`  
(use all 3 book levels)

**Direction: CONTRARIAN — high bid volume predicts price DOWN**
- OBI beta vs fwd_ret: **-0.55 to -0.78** across H=1,5,10,20,50 (p≈0, consistent both days)
- Signal is stable across low/med/high volatility regimes (corr ≈ -0.08 to -0.13)

**Quote skew rule**:
- OBI > +0.15 (more bids posted) → price about to fall → **pull bid, keep ask**
- OBI < -0.15 (more asks posted) → price about to rise → **keep bid, pull ask**
- |OBI| ≤ 0.15 → post both sides normally

> ⚠️ WARNING: The old rule had this BACKWARDS. Do NOT follow OBI direction — it is contrarian.

---

### Signal 2: Micro-Price Z-Score — MOMENTUM
**Construct**:
```
micro = (bid1 × ask_vol1 + ask1 × bid_vol1) / (bid_vol1 + ask_vol1)
residual = micro - mid
Z = (residual - expanding_mean) / expanding_std   # min 30 obs warmup
```

**Direction: MOMENTUM — Z > 0 predicts price UP**
- corr(Z, fwd_10) = **+0.46** (p≈0, both days)
- Z > 0: avg fwd_10 = +1.5 to +2.0 ticks; Z < 0: avg fwd_10 = +0.4 to +0.7 ticks
- Half-life of residual: **0.13–0.14 ticks** (nearly instant reversion to mid)

**Quote skew rule**:
- Z > +1.0 → lean bid-only, pull ask (momentum up)
- Z < -1.0 → lean ask-only, pull bid (momentum down)
- |Z| ≤ 1.0 → post both sides normally

---

### Signal 3: Trade Flow — DIRECTIONAL (follow buys, fade sells)
- Buy trade (lifted ask): **+2.4 to +2.9 ticks fwd_10** (t≈12, p≈0) — informed, follow it
- Sell trade (hit bid): **+0.3 to -0.3 ticks** (p>0.08) — noise, do not follow
- Action: after observing a buy trade, add 1 unit to long via passive bid

---

### Key Pattern: Intraday Linear Ramp
- Price rises ~+1000 ticks/day in a near-perfect linear ramp, consistent across all 3 days
- **Never use a fixed FV** — plain EMA lags the ramp; use Holt's linear smoothing

---

### Strategy
**Core**: Passive market making only. Lean long always. Never go net short.

**Fair value** via Holt's linear double exponential smoothing:
```python
level = ALPHA * mid + (1 - ALPHA) * (level + trend)
trend = BETA * (level - prev_level) + (1 - BETA) * trend
fv    = level + trend   # one-step-ahead forecast
```
Init: `level = first_mid`, `trend = 0.0`; persist `level` and `trend` in traderData.

**Quote placement**:
```
base_bid = int(fv) - EDGE
base_ask = int(fv) + EDGE
combined_skew = inv_skew + obi_skew + z_skew
final_bid = base_bid + combined_skew
final_ask = base_ask + combined_skew
```

**Skew components**:
- `inv_skew`: `int(round(pos / LIMIT * EDGE))` — shift both quotes up when long, down when short
- `obi_skew`: OBI > +0.15 → suppress bid (don't post it); OBI < -0.15 → suppress ask
- `z_skew`: Z > +1.0 → suppress ask; Z < -1.0 → suppress bid
- Asymmetric short penalty: if pos < 0 → add +1 to both quotes (extra aversion to shorts in uptrend)

**Size scale**:
- Buy side: `max(0.3, 1.0 - pos / LIMIT * 0.5)` — lean larger on buy side (trend is friend)
- Sell side: `max(0.3, 1.0 - abs(pos) / LIMIT * 0.7)` — tighten faster when short

**NEVER aggressive take**:
- Crossing the 13-tick spread costs ~13 ticks per trade
- Signal (Z, OBI) only predicts ~1.5 ticks — guaranteed net loss on every market order
- Remove all aggressive take logic from `_trade_ipr`

**Session timing** (optional optimization):
- Spread is 12.6 ticks at open vs 13.5 at close (+7%)
- Execute largest position adjustments early — edge from passive fills is highest then

**Inventory management**:
- Hard limit: 80 / -80 (exchange enforced)
- Soft MM cap: +40 long / -5 short. Long inventory earns trend (+1000/day); short inventory fights trend
- When OBI and Z both agree (e.g. both bullish), increase passive order size by up to 2×

---

### Performance Benchmarks (validated over days -2 and -1)
| Strategy | Day -2 | Day -1 | Notes |
|----------|--------|--------|-------|
| Buy & Hold +80 | ~1,003 ticks | ~1,000 ticks | Passive, no skill |
| Passive MM (simulated) | ~11,039 ticks | ~12,284 ticks | 11–12× B&H |
| Market orders | -9,055 ticks | -9,896 ticks | Lethal — never use |

Target: >2,500 ticks/day (floor: must beat B&H or don't trade)

---

### Constants
```python
IPR_LIMIT       = 80
IPR_EDGE        = 3
IPR_ALPHA       = 0.20   # Holt level smoothing — 0.05 had ~171-tick lag on linear ramp, pinned quotes to best_bid+1
IPR_BETA        = 0.10   # Holt trend smoothing
IPR_OBI_THRESH  = 0.15   # contrarian OBI threshold (was 0.25, direction was wrong)
IPR_Z_THRESH    = 1.0    # micro-price Z threshold for quote suppression
```

---

### Confidence
- +1000/day trend: **high** — consistent across all 3 days to <3σ
- OBI contrarian: **high** — t≈10, p≈0, stable across volatility regimes, both days
- Micro-price Z momentum: **high** — corr≈0.46, p≈0, both days
- Trade flow (buy informed): **high** — t≈12, p≈0
- MM beats B&H: **high** — 11–12× over simulation (fill rate assumptions may vary)

---

### WARNINGS
1. **OBI is CONTRARIAN here** — NOT directional. High bid volume predicts DOWN. Do not follow OBI.
2. **NEVER use market orders** — 13-tick spread destroys any signal edge (~10.9 ticks net loss each)
3. **NEVER go net short** — fighting a +1000/day trend with a crossing cost is doubly lethal
4. Do NOT treat this like EMERALDS. The price drifts +1000/day. A fixed FV causes massive adverse selection.
