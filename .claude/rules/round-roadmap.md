## Round Roadmap

| Round | Products | Alpha | Status |
|-------|----------|-------|--------|
| 0 ✅ | EMERALDS, TOMATOES | Pure MM — learn env | Done |
| 1 ✅ | ASH_COATED_OSMIUM, INTARIAN_PEPPER_ROOT | Fixed FV MM (ASH) + Holt's trend MM (IPR) + imbalance tilt | Done |
| 2 ✅ | ASH_COATED_OSMIUM, INTARIAN_PEPPER_ROOT | Same as R1 + MAF bid + 25% extra market flow + 50k investment budget | Done |
| 3 🔴 | HYDROGEL_PACK, VELVETFRUIT_EXTRACT, VEV_4000…VEV_6500 | Black-Scholes IV, delta hedge or vol arb on vouchers + MM on spot | **Current** |
| 4 | Cross-exchange arb | Two-way arb + accumulation — read fee structure twice | |
| 5 | Insider IDs revealed | Copy insider trades, rank by forward PnL — keep MM running | |

## Round 3 Key Changes vs Round 2
- New planet (Solvenar), leaderboard reset — Phase 2 / GOAT begins
- Round duration: 48 hours (down from 72)
- New products: HYDROGEL_PACK (limit 200), VELVETFRUIT_EXTRACT (limit 200), 10 VEV voucher strikes (limit 300 each)
- Vouchers are call options on VEV with TTE=5 days at R3 start; TTE counts down 1 day per round
- Manual: Celestial Gardeners' Guild — two-bid auction, Bio-Pods sell at 920, uniform reserve 670–920 step 5
- No MAF mechanic in R3+
- `bid()` method safe to include but silently ignored outside Round 2

## What Kills Teams
- Hardcoding historical prices without fallback
- Overfitting backtest params
- Verbose print() → Lambda timeout
- Carrying Round N-1 products into Round N trader.py (backtest will error or produce 0 PnL)
- Using wrong position limits (check spec every round — limits change)
- Forgetting to set a real MAF bid before Round 2 final submission (returning 0 means no extra access)
