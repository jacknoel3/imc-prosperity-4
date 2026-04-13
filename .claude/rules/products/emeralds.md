## EMERALDS
- Fair value: exactly 10,000 (hardcoded, never drifts)
- Position limit: 20
- Book: bid ~9992 vol ~12, ask ~10008 vol ~12, spread = 16
- Occasional tighter spread touching 10000 (spread = 8)
- Tick volatility: ~0.25 (extremely stable)

### Strategy
- Aggressively take: any ask < 10000 (buy), any bid > 10000 (sell)
- Passive MM: post bids at 9998–9999, asks at 10001–10002
- Inventory skew: shift quotes by `int(pos/limit * EDGE)` ticks
- Current constants: `EMERALDS_FV=10000`, `EMERALDS_LIMIT=20`, `EMERALDS_EDGE=2`
