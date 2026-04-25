## VELVETFRUIT_EXTRACT_VOUCHER (all 10 strikes)
- Type: European call options on VELVETFRUIT_EXTRACT
- Position limit: 300 per voucher
- Strikes: 4000, 4500, 5000, 5100, 5200, 5300, 5400, 5500, 6000, 6500
- **TTE at R3 start: 5 days**
- σ (market IV): **~22% annualized** — use for BS fair value in trader. This is what the market consistently prices at across all historical days (TTE=8,7,6). Do NOT use RV=34.2% for option fair value — that gives prices 15–30 ticks above market; nobody will fill you.
- σ (realized): **34.2% annualized** — underlying's actual realized vol. Relevant for risk/gamma sizing, not for quoting.
- S (underlying): ~5,250 — track live from VEV mid

### TTE by Round
| Round | TTE |
|-------|-----|
| Tutorial | 8d |
| Round 1 | 7d |
| Round 2 | 6d |
| **Round 3** | **5d** |
| Round 4 | 4d |
| Round 5 | 3d |

### Strike Classification & Strategy

| Strike | Moneyness | IV (avg, hist.) | Strategy |
|--------|-----------|-----------------|----------|
| 4000 | Deep ITM | 79% (erratic) | **Skip** — delta≈1, price≈VEV−K, no option edge |
| 4500 | ITM | 45% (erratic) | **Skip** — same as above |
| 5000 | ATM | **23.3%** | Passive MM at BS(IV=22%) |
| 5100 | ATM | **23.1%** | Passive MM at BS(IV=22%) |
| 5200 | ATM | **23.3%** | Passive MM at BS(IV=22%) |
| 5300 | ATM | **23.6%** | Passive MM at BS(IV=22%) |
| 5400 | ATM | **22.1%** | Passive MM at BS(IV=22%), spread 1.4, 225 trades |
| 5500 | ATM | **24.0%** | Passive MM at BS(IV=22%) |
| 6000 | Deep OTM | ~38%* | **SELL at ask=1** — pinned at min tick, BS≈0, P(ITM)≈0% |
| 6500 | Deep OTM | ~58%* | **SELL at ask=1** — same, sell full limit |

*IV inflated by min-tick floor — not a real signal. Deep OTM IV figures are meaningless; strategy rationale is BS≈0.

**IMPORTANT**: Earlier IV values of 33–35% for ATM strikes were wrong — confused with RV. Actual market IV is ~22% across all historical days. The "−3.8 ticks below BS" VEV_5400 signal was also wrong — it was theta decay (TTE=6→5 price drop), not a misprice vs realized vol.

### Black-Scholes Implementation
```python
from math import log, sqrt, exp
from statistics import NormalDist

N = NormalDist().cdf

def bs_call(S, K, T, sigma, r=0):
    if T <= 0: return max(0, S - K)
    d1 = (log(S/K) + 0.5*sigma**2*T) / (sigma*sqrt(T))
    d2 = d1 - sigma*sqrt(T)
    return S*N(d1) - K*exp(-r*T)*N(d2)

# T = days_remaining / 365
# sigma = 0.22 (market IV — what the bots price at; use this for quoting)
# sigma = 0.342 is realized vol — do NOT use for BS fair value, only for risk sizing
```

### Most Active ATM Strike: VEV_5400
- Spread: 1.4 ticks, 225 trades over 3 historical days — most liquid ATM strike
- Fair value: `bs_call(S, 5400, T, 0.22)` — use market IV (0.22), NOT RV (0.342)
- Strategy: passive MM, bid at fair−0.5, ask at fair+0.5
- Delta at TTE=5, IV=22%: ≈ **0.15** per unit long (hedge short 0.15 VEV per voucher)
- No directional buy thesis — earlier "−3.8 ticks below BS" was theta decay (TTE=6→5 price drop), not a misprice

### Priority Trade: VEV_6000 / VEV_6500 (free carry)
- Market mid = 0.5 (bid=0, ask=1). BS≈0 for both at any reasonable sigma.
- Sell at ask=1 and collect 1 tick. Expires worthless with P(ITM)≈0% at TTE=5, S=5250.
- Risk: VEV spike above 6,000 before expiry — negligible at σ=22% and TTE=5d
- Sell up to full limit (300 each).

### Vol Surface Notes
- ATM vol surface is flat at **~22%** — no skew in ATM bucket
- Market IV (22%) is well below realized vol (34.2%) but this is structural — the bots consistently price at 22%
- IV intraday: declines slightly within each day; use per-day T value (TTE counts down 1 per day)
- Do NOT use RV (34.2%) as sigma in BS for quoting — you'll post prices 15–30 ticks above market and never fill

### Warnings
1. Deep ITM (4000, 4500) — skip. No option edge, just VEV exposure.
2. Deep OTM (6000, 6500) — sell only. Do not buy (BS≈0).
3. Do not use market orders on ATM strikes — passive only.
4. Delta hedge via VEV passively — VEV spread is 5 ticks, aggressive take is expensive.
5. Use sigma=0.22 in BS (market IV), not sigma=0.342 (realized vol).

### Confidence
- ATM passive MM (IV=22%): high — stable, confirmed across all 3 historical days
- VEV_6000/6500 free carry: high — structural, tail risk negligible
- Delta hedge (0.15 VEV per voucher): medium — delta shifts as S and TTE change, recompute live
