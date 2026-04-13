## TOMATOES
- Fair value: dynamic (EMA of mid-price, alpha=0.3)
- Position limit: 35
- Mid range: ~4946–5036 across days, no clear autocorrelation
- Spread: mean 13, min 5
- Tick volatility: ~0.79 (~3x EMERALDS)

### Strategy
- Fair value = EMA of (best_bid + best_ask) / 2, persisted in traderData
- Aggressively take: ask < fv-1, bid > fv+1
- Passive MM: quote at int(fv) ± EDGE, skewed by inventory ratio
- Size scale: `max(0.3, 1.0 - abs(pos/limit) * 0.7)` — reduce size at high inventory
- Guard passive quotes against crossing the book before appending
- Current constants: `TOMATOES_LIMIT=35`, `TOMATOES_EDGE=3`
