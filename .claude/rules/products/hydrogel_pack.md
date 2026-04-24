## HYDROGEL_PACK
- Fair value: **10,000** (stationary — grand mean 9,990.8, std 31.9 across 3 days)
- Position limit: 200
- Spread: mean 15.7, min 7, max 17 — tight and stable
- Tick volatility (std of Δmid): 2.17
- Data: `phase2/round3/data/`

### ACF Structure
- Lag-1: **-0.129** (mean-reverting, weaker than ASH's -0.495)
- Mean-reversion per tick is less reliable than ASH — size more conservatively

### Imbalance Signal
- corr(OBI, fwd_ret_1) = **-0.327** → **CONTRARIAN**
- OBI > +0.15 → price about to fall → suppress bid
- OBI < -0.15 → price about to rise → suppress ask
- Same direction as IPR, OPPOSITE to ASH

### Market Structure
- ~337 trades/day, avg qty 4, inter-trade ~986 ticks
- Buyer/seller fields empty — no bot ID signal
- Book depth at best: ~12.4 units bid/ask (thin, symmetric)
- HGP and VEV structurally similar but NOT correlated — trade independently

### Strategy
- Fixed FV MM at 10,000 (same archetype as ASH but with contrarian OBI)
- Aggressively take: ask < 10,000 (buy), bid > 10,000 (sell)
- Passive MM: post at FV ± EDGE, skewed by inventory ratio
- OBI tilt: OBI > +0.15 → suppress bid; OBI < -0.15 → suppress ask (CONTRARIAN)
- Size scale: `max(0.3, 1.0 - abs(pos/limit) * 0.7)`

### Constants (starting point — tune after backtest)
```python
HGP_FV    = 10000
HGP_LIMIT = 200
HGP_EDGE  = 2
HGP_OBI_THRESH = 0.15
```

### Confidence
Medium-high — stationary FV well-established, OBI contrarian signal clean. ACF weaker than ASH so mean-reversion per tick less reliable.
