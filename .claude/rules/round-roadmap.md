## Round Roadmap

| Round | Products | Alpha | Risk |
|-------|----------|-------|------|
| 0 ✅ | EMERALDS, TOMATOES | Pure MM | Learn env |
| 1 🔴 | ASH_COATED_OSMIUM (FV=10000, like EMERALDS), INTARIAN_PEPPER_ROOT (+1000/day trend, EMA FV) | Fixed FV MM (ASH) + Trend-EMA MM (IPR) + imbalance tilt both | IPR trend may not persist — monitor direction round-to-round |
| 2 | ETF basket | Stat arb: basket vs synthetic, z-score spread | Basket = linear combo |
| 3 | Options (vouchers) | Black-Scholes IV, dynamic hedge | Unhedged long vega won P3 |
| 4 | Cross-exchange arb | Two-way arb + accumulation | Read fee structure twice |
| 5 | Insider IDs revealed | Copy insider trades, rank by forward PnL | Keep MM running |

## What Kills Teams
- Hardcoding historical prices without fallback
- Overfitting backtest params
- Verbose print() → Lambda timeout
- Carrying Round N-1 products into Round N trader.py (backtest will error or produce 0 PnL)
- Using wrong position limits (check spec every round — limits change)
