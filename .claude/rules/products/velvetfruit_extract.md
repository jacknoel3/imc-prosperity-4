## VELVETFRUIT_EXTRACT
- Fair value: **~5,250** (stationary — grand mean 5,250.1, std 15.6; creeps +9 ticks/day, negligible)
- Position limit: 200
- Spread: mean 5.0 (tightest of all R3 products), min 1, max 6
- Tick volatility (std of Δmid): 1.13
- Realized vol: **34.2% annualized** (consistent: 33.9%, 34.3%, 34.4% across 3 days) — use as σ for BS
- Data: `phase2/round3/data/`

### ACF Structure
- Lag-1: **-0.159** (mean-reverting, moderate)

### Imbalance Signal
- corr(OBI, fwd_ret_1) = **-0.321** → **CONTRARIAN**
- OBI > +0.15 → suppress bid; OBI < -0.15 → suppress ask

### Market Structure
- ~457 trades/day, avg qty 6, inter-trade ~726 ticks
- Book depth at best: ~37.8 units bid/ask — deepest book in R3
- Buyer/seller fields empty

### Dual Role
1. **Standalone MM**: passive MM around FV=5,250 with contrarian OBI tilt
2. **Option underlying**: price + vol feed into BS pricing for all VEV vouchers

### Delta Hedging (for voucher strategy)
- VEV_5400 long → short **0.15 VEV per voucher** to hedge delta (at IV=22%, TTE=5; recompute live as S and TTE change)
- Earlier figure of 0.20 was wrong — computed with RV (34.2%) not market IV (22%)
- Hedge passively (5-tick spread means aggressive take is expensive but manageable vs IPR's 13-tick)
- Net risk after hedge: gamma + vega

### Constants
```python
VEV_FV       = 5250
VEV_LIMIT    = 200
VEV_EDGE     = 1          # tight spread — 1–2 tick edge
VEV_OBI_THRESH = 0.15
VEV_SIGMA_RV   = 0.342    # realized vol — for risk sizing only
VEV_SIGMA_IV   = 0.22     # market implied vol — use this in BS for option fair value
```

### Confidence
High — three-day consistency, clean signals, stable realized vol. Tight spread makes delta hedging viable.
