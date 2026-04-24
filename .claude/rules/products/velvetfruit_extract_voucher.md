## VELVETFRUIT_EXTRACT_VOUCHER (all 10 strikes)
- Type: European call options on VELVETFRUIT_EXTRACT
- Position limit: 300 per voucher
- Strikes: 4000, 4500, 5000, 5100, 5200, 5300, 5400, 5500, 6000, 6500
- **TTE at R3 start: 5 days** (counts down 1 day per round — expires after R7 equivalent)
- Data: `phase2/round3/data/`

### TTE by round
| Round | TTE |
|-------|-----|
| Tutorial (day 1) | 8d |
| Round 1 (day 2) | 7d |
| Round 2 (day 3) | 6d |
| **Round 3 (day 4)** | **5d** |
| Round 4 (day 5) | 4d |
| Round 5 (day 6) | 3d |

### Pricing framework
Black-Scholes call: `C = S·N(d1) - K·e^(-rT)·N(d2)`
- S = VELVETFRUIT_EXTRACT mid price
- K = strike
- T = TTE in years (use T = days/365)
- r = 0 (no risk-free rate in Prosperity)
- σ = implied or realized vol from VEV price history

### Strategy (placeholder — update after EDA)
- Fit implied vol (IV) for each strike from observed voucher prices
- Compare IV to realized vol of VEV — exploit mispricings
- ATM options have highest vega; deep OTM/ITM have little value
- Do NOT delta-hedge with market orders — crossing spread kills edge
- If selling options, manage gamma risk near expiry

### Questions to answer from EDA
- What is realized daily vol of VEV?
- Are any vouchers systematically mispriced (IV vs realized)?
- Which strikes are most liquid (tightest spread, most trades)?
- Are bots quoting inside BS theoretical value (free lunch) or outside?

### Confidence
None yet — update after EDA.
