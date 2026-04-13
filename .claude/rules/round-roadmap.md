## Round Roadmap (P3-based estimates)

| Round | Products | Alpha | Risk |
|-------|----------|-------|------|
| 0 | EMERALDS, TOMATOES | Pure MM | Learn env |
| 1 | +1-2 products | Fixed FV + mean-rev | Don't over-trade noisy product |
| 2 | ETF basket | Stat arb: basket vs synthetic, z-score spread | Basket = linear combo |
| 3 | Options (vouchers) | Black-Scholes IV, dynamic hedge | Unhedged long vega won P3 |
| 4 | Cross-exchange arb | Two-way arb + accumulation | Read fee structure twice |
| 5 | Insider IDs revealed | Copy insider trades, rank by forward PnL | Keep MM running |

## What Kills Teams
- Hardcoding historical prices without fallback
- Overfitting backtest params
- Verbose print() → Lambda timeout
- No backtester ready before Round 1
