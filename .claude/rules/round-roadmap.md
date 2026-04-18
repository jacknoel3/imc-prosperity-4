## Round Roadmap

| Round | Products | Alpha | Status |
|-------|----------|-------|--------|
| 0 ✅ | EMERALDS, TOMATOES | Pure MM — learn env | Done |
| 1 ✅ | ASH_COATED_OSMIUM, INTARIAN_PEPPER_ROOT | Fixed FV MM (ASH) + Holt's trend MM (IPR) + imbalance tilt | Done |
| 2 🔴 | ASH_COATED_OSMIUM, INTARIAN_PEPPER_ROOT | Same as R1 + MAF bid + 25% extra market flow + 50k investment budget allocation | **Current** |
| 3 | Options (vouchers) | Black-Scholes IV, dynamic hedge — unhedged long vega won P3 | |
| 4 | Cross-exchange arb | Two-way arb + accumulation — read fee structure twice | |
| 5 | Insider IDs revealed | Copy insider trades, rank by forward PnL — keep MM running | |

## Round 2 Key Changes vs Round 1
- Same products and position limits (ASH=80, IPR=80)
- Add `bid()` method with MAF value — top 50% of bidders get 25% more quotes to trade against
- MAF bid is subtracted from Round 2 profits if accepted (blind auction, cutoff = median bid)
- Backtest uses 80% of quotes (slightly randomized per submission) — MAF ignored during testing
- 50,000 XIRECs investment budget to allocate across three growth pillars (manual, not in trader.py)

## What Kills Teams
- Hardcoding historical prices without fallback
- Overfitting backtest params
- Verbose print() → Lambda timeout
- Carrying Round N-1 products into Round N trader.py (backtest will error or produce 0 PnL)
- Using wrong position limits (check spec every round — limits change)
- Forgetting to set a real MAF bid before Round 2 final submission (returning 0 means no extra access)
