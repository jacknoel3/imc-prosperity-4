## VELVETFRUIT_EXTRACT_VOUCHER (all 10 strikes)
- Type: European call options on VELVETFRUIT_EXTRACT
- Position limit: 300 per voucher
- Strikes: 4000, 4500, 5000, 5100, 5200, 5300, 5400, 5500, 6000, 6500
- **TTE at R3 start: 5 days**
- σ (realized): **34.2% annualized** — use for BS pricing
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

| Strike | Moneyness | IV (avg) | Strategy |
|--------|-----------|----------|----------|
| 4000 | Deep ITM | 77.4% | **Skip** — delta≈1, price=VEV−K, no option edge |
| 4500 | ITM | 45.7% | **Skip** — same as above |
| 5000 | ATM | 33.6% | Passive MM — IV near fair |
| 5100 | ATM | 33.2% | Passive MM — marginally cheap |
| 5200 | ATM | 33.6% | Passive MM — fair-to-cheap |
| 5300 | ATM | 33.9% | Passive MM — near-fair |
| 5400 | ATM | 31.8% | **BUY** — avg −3.8 ticks below BS, 83% of buckets mispriced, best single trade |
| 5500 | ATM | 34.5% | Passive MM — fair |
| 6000 | Deep OTM | 54.6%* | **SELL at ask=1** — pinned at min tick, BS≈0, P(ITM)≈0.3% |
| 6500 | Deep OTM | 82.7%* | **SELL at ask=1** — same, sell full limit |

*IV inflated by min-tick floor — not a real signal.

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
# sigma = 0.342 (update if realized vol shifts)
```

### Priority Trade: VEV_5400 (highest conviction)
- Avg misprice: −3.8 ticks below BS (market is cheap)
- Spread: 1.4 ticks — edge exceeds half-spread
- 225 trades over 3 days — liquid enough for passive fills
- Delta hedge: short 0.20 VEV per unit long
- Entry: resting bid at `bs_call(S, 5400, T, 0.342) − 0.5`
- Day-0 misprice is largest (−7.6 ticks); converges by day 2 (+0.2) — enter early

### Priority Trade: VEV_6000 / VEV_6500 (free carry)
- Market mid = 0.5 (bid=0, ask=1). BS≈0 for both.
- Sell at ask=1 and collect 1 tick. Expires worthless with 99.7% probability.
- Risk: VEV spike above 6,000 or 6,500 before expiry — at σ=34.2% and TTE=5d, P(VEV>6000)≈0.3%
- Sell up to full limit (300 each).

### Vol Surface Notes
- ATM vol surface is flat at ~33–34% — no skew in ATM bucket
- Market-wide implied vol slightly below realized (34.2%) — the whole ATM surface is cheap
- IV intraday: declines slightly within each day; rises across days (TTE calendar effect) — use per-day T value

### Warnings
1. Deep ITM (4000, 4500) — skip. No option edge, just VEV exposure.
2. Deep OTM (6000, 6500) — sell only. Do not buy (BS≈0).
3. Do not use market orders on ATM strikes — spread is 1–6 ticks but signal is ~1.5 ticks. Passive only.
4. Delta hedge via VEV passively — VEV spread is 5 ticks, aggressive take is expensive.

### Confidence
- VEV_5400 misprice: medium (converges by day 2 — may vanish in R4/R5 as market discovers σ)
- VEV_6000/6500 free carry: high (structural, tail risk negligible)
- ATM vol surface pricing: high — stable σ, clean BS fit
