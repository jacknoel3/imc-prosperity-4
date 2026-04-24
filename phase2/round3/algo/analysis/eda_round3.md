# Round 3 EDA — Full Findings

**Data**: `phase2/round3/data/prices_round_3_combined.csv` + `trades_round_3_combined.csv`
**Date run**: 2026-04-24
**Analyst**: researcher agent

---

## Findings: HYDROGEL_PACK

- Fair value estimate: 10,000 (fixed). Three-day grand mean = 9,990.8, std = 31.9. Stationary around 10,000 — daily means drift at most ±10 ticks from FV.
- Spread: mean 15.7, min 7.0, max 17.0 — tight and stable, nearly identical structure to ASH_COATED_OSMIUM (which had mean 16.2).
- Tick volatility (std of Δmid): 2.17
- Autocorrelation (lag-1): -0.129 → mean-reverting (weaker than ASH's -0.495; noisier but same direction)
- Imbalance signal: corr(OBI, fwd_ret_1) = -0.327 → contrarian. Bucketed: most-negative OBI quintile produces +0.32 avg next-tick return; most-positive OBI produces -0.32. Signal is clean but direction is OPPOSITE to ASH. Do NOT follow OBI direction here — fade it.
- Intraday pattern: stable / mean-reverting around 10,000. No persistent linear drift. Intraday swings are large (±50 ticks) but mean-revert across the day. No open/close effect.
- Book depth at best: bid ~12.4 units, ask ~12.4 units. Symmetric, thin.
- Cross-product note: HGP-VEV instantaneous correlation = 0.22, but 500-tick rolling correlation spans -0.94 to +0.96 — pure regime-switching noise. HGP and VEV are NOT a tradeable pair. Independent instruments.
- Trade execution: 1,010 trades over 3 days (~337/day), avg qty 4.0, median 4, inter-trade interval ~986 ticks. Buyer/seller fields empty — no bot ID signal.
- Recommended strategy: Fixed FV MM at 10,000. Contrarian OBI tilt: OBI > +0.15 → suppress bid; OBI < -0.15 → suppress ask.
- Recommended position sizing: `max(0.3, 1.0 - abs(pos/limit) * 0.7)`. Limit = 200.
- Confidence: medium-high — stationary FV well-established, OBI contrarian signal clean (r=-0.33). ACF weaker than ASH so mean-reversion per tick less reliable.

---

## Findings: VELVETFRUIT_EXTRACT

- Fair value estimate: ~5,250 (stationary). Grand mean = 5,250.1, std = 15.6. Daily means: day0=5,246.5, day1=5,248.4, day2=5,255.4 — creeping +9 ticks over 3 days, no strong linear trend. Treat as stationary.
- Spread: mean 5.0, min 1.0, max 6.0 — tightest of all R3 products.
- Tick volatility (std of Δmid): 1.13
- Autocorrelation (lag-1): -0.159 → mean-reverting (moderate)
- Imbalance signal: corr(OBI, fwd_ret_1) = -0.321 → contrarian. Bucketed: most-negative OBI quintile → +0.29 avg fwd ret; most-positive → -0.22.
- Intraday pattern: noisy but broadly stable. No monotonic ramp — day 1 shows some upside (5240→5262), day 2 similar (5262→5277), but non-monotonic. Not a clean linear trend. Treat as stationary for BS pricing.
- Book depth at best: bid ~37.8 units, ask ~37.8 units. Deepest book in dataset — 3× deeper than HGP.
- Realized volatility: **34.2% annualized** (consistent across all three days: 33.9%, 34.3%, 34.4%). Use as BS input σ.
- Trade execution: 1,372 trades over 3 days (~457/day), avg qty 6.0, median 6, inter-trade interval ~726 ticks. Buyer/seller fields empty.
- Recommended strategy: (1) Standalone passive MM around FV=5,250 with contrarian OBI tilt. (2) Delta hedge vehicle for VEV voucher positions — short 0.20 VEV per VEV_5400 long.
- Recommended position sizing: Limit = 200. Size conservatively — deep book means bots are active.
- Confidence: high — three-day consistency, clean OBI signal, stable realized vol.

---

## Findings: VEV Vouchers — Vol Surface Summary

**Parameters**: S = VEV day-average mid (~5,250), r = 0, σ = 34.2% realized annualized.
**TTE used**: 4.5d / 3.5d / 2.5d for days 0 / 1 / 2 respectively.

| Strike | Moneyness | Mean IV | IV vs RV gap | Avg mkt−BS diff | Spread | Classification |
|--------|-----------|---------|--------------|-----------------|--------|----------------|
| 4000 | 1.31 (deep ITM) | 77.4% | +43.2% | — | — | Skip — delta≈1 |
| 4500 | 1.17 (ITM) | 45.7% | +11.5% | — | — | Skip — delta≈1 |
| 5000 | 1.05 (ITM) | 33.6% | -0.6% | -0.9 | 6.0 | Passive MM |
| 5100 | 1.03 (ITM) | 33.2% | -1.0% | -1.2 | — | Passive MM |
| 5200 | 1.01 (ATM) | 33.6% | -0.6% | -1.8 | 2.9 | Passive MM |
| 5300 | 0.99 (ATM) | 33.9% | -0.3% | -1.1 | 2.1 | Passive MM |
| 5400 | 0.97 (OTM) | 31.8% | -2.4% | **-3.8** | 1.4 | **BUY — top trade** |
| 5500 | 0.95 (OTM) | 34.5% | +0.3% | +0.2 | — | Passive MM |
| 6000 | 0.87 (deep OTM) | 54.6%* | +20.4% | pinned | 1.0 | **SELL at ask=1** |
| 6500 | 0.81 (deep OTM) | 82.7%* | +48.5% | pinned | 1.0 | **SELL at ask=1** |

*IV inflated by min-tick floor artifact — not a real signal.

---

## Findings: VEV_4000

- Fair value: BS ≈ VEV − 4000 (delta = 1.000, time value ≈ 0). Empirical delta = 1.000, corr(VEV_4000, VEV) = 0.989.
- Recommended strategy: Skip. No option edge — functionally equivalent to holding VEV with extra spread cost.
- Confidence: high — three-day consistency.

---

## Findings: VEV_4500

- Fair value: BS ≈ VEV − 4500 (delta ≈ 1.000, time value ≈ 0.01 ticks). Empirical delta = 1.000, corr = 0.991.
- Trade count: 1 over 3 days — essentially illiquid.
- Recommended strategy: Skip. Same as VEV_4000.
- Confidence: high.

---

## Findings: VEV_5000

- Fair value: BS = ~255 (varies 254–258 by day). Market mean = 253.3, avg diff = -0.9 ticks (within spread of 6.0).
- Spread: mean 6.0
- Tick volatility: 0.98
- Autocorrelation (lag-1): -0.098 → weak mean-reverting
- Intraday pattern: stable, tracks VEV delta ~0.93
- Trade count: 1 — functionally illiquid.
- Recommended strategy: Passive MM only. Misprice within spread — no reliable edge after costs.
- Confidence: medium — BS fair value clean, but single trade means no fill model.

---

## Findings: VEV_5100

- Fair value: BS ≈ 200–210 by day. Market mean slightly below BS (-1.2 ticks avg), within spread.
- Recommended strategy: Passive MM. Marginally cheap but not enough to act on confidently.
- Confidence: medium.

---

## Findings: VEV_5200

- Fair value: BS ≈ 150–165 by day. Avg diff = -1.8 ticks, spread = 2.9 — borderline (below half-spread threshold of 1.45).
- Recommended strategy: Passive MM. Watch for convergence — misprice borderline actionable.
- Confidence: medium.

---

## Findings: VEV_5300

- Fair value: BS ≈ 105–125 by day. Avg diff = -1.1 ticks, spread = 2.1 — below half-spread (1.05). Not actionable.
- IV intraday: declines within day (day 0: 31.6% open → 30.5% close; day 2: 38.3% → 35.6%). Mild systematic overpricing at open.
- Recommended strategy: Passive MM.
- Confidence: medium.

---

## Findings: VEV_5400

- Fair value: BS = 13.6–26.1 per day (mean ~19.9 at T=3.5d). Market mean = 16.0. **Avg diff = -3.8 ticks below BS.**
- Spread: mean 1.38
- Tick volatility: 0.27
- Autocorrelation (lag-1): -0.254 → mean-reverting
- Misprice by day: day 0 = -7.6 ticks, day 1 = -3.9 ticks, day 2 = +0.2 ticks (converging)
- Mispriced on 83% of 30-tick buckets (day 0/1)
- Trade count: 225 over 3 days — most liquid OTM strike
- Delta: ~0.20 → hedge by shorting 0.20 VEV per unit long
- Recommended strategy: Passive buy at `bs_call(S, 5400, T, 0.342) − 0.5`. Delta hedge in VEV. Net risk: gamma + vega.
- Entry timing: day-0 edge is largest (-7.6 ticks) — enter early in round.
- Recommended position sizing: up to 50% of limit (150 units). Start at 20–30 given thin book.
- Confidence: medium — misprice persistent on days 0/1 but converges by day 2. May vanish in R4/R5.

---

## Findings: VEV_5500

- Fair value: BS ≈ 5–12 by day. Market mean ≈ BS + 0.2 (near-fair).
- Spread: narrow
- Recommended strategy: Passive MM — fair value, no directional edge.
- Confidence: medium-high.

---

## Findings: VEV_6000

- Fair value: BS ≈ 0.000. Market mid = **0.5** (pinned at min tick: bid=0, ask=1). Std = 0.000.
- Spread: 1.0 (bid=0, ask=1 — single-sided market)
- Trade count: 284 over 3 days (noise trades at floor price)
- P(VEV > 6000 at expiry from S=5250, σ=34.2%, T=5d) ≈ **0.3%**
- Recommended strategy: **Sell at ask = 1.** Collect 1-tick premium. Expires worthless with 99.7% probability.
- Recommended position sizing: sell up to full limit (300). Single-sided market — fill partial.
- Confidence: high — pinned at floor, BS = 0, tail risk negligible.

---

## Findings: VEV_6500

- Fair value: BS ≈ 0.000. Market mid = **0.5** (identical to VEV_6000 — pinned at min tick).
- Spread: 1.0
- Trade count: 0 over 3 days — no observed trades
- P(VEV > 6500 at expiry) ≈ **0.001%**
- Recommended strategy: **Sell at ask = 1.** Same rationale as VEV_6000, even lower tail risk.
- Recommended position sizing: sell up to full limit (300).
- Confidence: high.

---

## Cross-Product Notes

1. HGP and VEV structurally similar (both stationary FV, both contrarian OBI) but uncorrelated — trade independently.
2. ATM vol surface (5000–5500) is flat at ~33–34% IV, slightly below realized (34.2%) — market-wide under-pricing of vol.
3. VEV_5400 is the highest-conviction alpha trade: persistent misprice, liquid, actionable delta hedge.
4. VEV_6000/6500 are the lowest-risk free-carry trade: sell at ask=1, near-zero probability of loss.
5. Deep ITM (4000/4500): skip entirely — no option edge, just VEV delta-1 exposure with extra spread cost.
