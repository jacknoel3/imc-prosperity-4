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
- Realized volatility: **40.8% / 41.3% / 41.4%** using the generated EDA's calendar-day annualization (`TTE_days / 365`). The older **34.2%** figure is a trading-day annualization and should not be mixed with `T / 365` Black-Scholes pricing.
- Trade execution: 1,372 trades over 3 days (~457/day), avg qty 6.0, median 6, inter-trade interval ~726 ticks. Buyer/seller fields empty.
- Recommended strategy: (1) Standalone passive MM around FV=5,250 with contrarian OBI tilt. (2) Delta hedge vehicle for VEV voucher positions — short 0.20 VEV per VEV_5400 long.
- Recommended position sizing: Limit = 200. Size conservatively — deep book means bots are active.
- Confidence: high — three-day consistency, clean OBI signal, stable realized vol.

---

## Findings: VEV Vouchers — Vol Surface Summary

**Parameters**: S = VEV day-average mid (~5,250), r = 0, historical raw RV ≈ 41% using calendar-day annualization.
**Historical TTE used**: 8d / 7d / 6d for days 0 / 1 / 2 respectively.
**Live trading TTE assumption**: 5d. This is an extrapolation below the historical minimum TTE, so short-dated OTM/floor-voucher conclusions should be treated conservatively.

| Strike | Moneyness | Historical liquidity / pricing note | Spread | Classification |
|--------|-----------|--------------------------------------|--------|----------------|
| 4000 | 1.31 (deep ITM) | Functionally delta-1 exposure; extra spread cost versus Velvetfruit. | ~21 | Skip |
| 4500 | 1.17 (ITM) | Functionally delta-1 and almost no prints. | ~16 | Skip |
| 5000 | 1.05 (ITM) | One observed trade; pricing is hard to validate after spread. | ~6.0 | Passive only |
| 5100 | 1.03 (ITM) | One observed trade; pricing is hard to validate after spread. | ~4.3 | Passive only |
| 5200 | 1.01 (ATM) | Near-ATM, small historical cheapness at prints, but almost no fills. | ~2.9 | Small passive bid |
| 5300 | 0.99 (ATM) | Most useful liquid near-ATM candidate; historical prints slightly below fitted FV. | ~2.1 | Small passive bid |
| 5400 | 0.97 (OTM) | Most liquid OTM candidate; historical prints slightly below fitted FV, but edge is TTE-sensitive. | ~1.4 | Small passive long-gamma |
| 5500 | 0.95 (OTM) | Liquid but high spread-to-mid; near floor-adjacent behavior. | ~1.1 | Passive only |
| 6000 | 0.87 (deep OTM) | Pinned bid=0/ask=1; apparent IV is a tick-floor artifact. | 1.0 | Tiny passive sell only |
| 6500 | 0.81 (deep OTM) | Pinned bid=0/ask=1; apparent IV is a tick-floor artifact. | 1.0 | Tiny passive sell only |

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

- Historical mean mid = 253.3 / 253.3 / 258.5 across days 0 / 1 / 2 under 8d / 7d / 6d historical TTE.
- Spread: mean 6.0
- Tick volatility: 0.98
- Autocorrelation (lag-1): -0.098 → weak mean-reverting
- Intraday pattern: stable, tracks VEV delta ~0.93
- Trade count: 1 — functionally illiquid.
- Recommended strategy: Passive MM only. Misprice within spread — no reliable edge after costs.
- Confidence: medium — BS fair value clean, but single trade means no fill model.

---

## Findings: VEV_5100

- Historical mean mid = 168.1 / 165.0 / 167.3 across days 0 / 1 / 2 under 8d / 7d / 6d historical TTE.
- Recommended strategy: Passive MM. Marginally cheap but not enough to act on confidently.
- Confidence: medium.

---

## Findings: VEV_5200

- Historical mean mid = 97.5 / 95.1 / 94.0 across days 0 / 1 / 2 under 8d / 7d / 6d historical TTE.
- At historical trade timestamps, fitted-BS fair value averaged about 86.2 versus trade price 85.3, so the edge is small and fill-limited rather than a clear taker trade.
- Recommended strategy: Passive MM / small passive bid only. Watch for convergence, but require edge after spread.
- Confidence: medium.

---

## Findings: VEV_5300

- Historical mean mid = 48.9 / 46.9 / 44.5 across days 0 / 1 / 2 under 8d / 7d / 6d historical TTE.
- At historical trade timestamps, fitted-BS fair value averaged about 45.1 versus trade price 44.2, so the average cheapness is around 1 tick and close to the half-spread.
- Recommended strategy: Passive MM / small passive bid only.
- Confidence: medium.

---

## Findings: VEV_5400

- Historical mean mid = 18.5 / 15.7 / 13.7 across days 0 / 1 / 2 under 8d / 7d / 6d historical TTE.
- At historical trade timestamps, fitted-BS fair value averaged about 15.5 versus trade price 14.9, so the measured cheapness is modest, not the older 3-4 tick headline edge.
- Spread: mean 1.38
- Tick volatility: 0.27
- Autocorrelation (lag-1): -0.254 → mean-reverting
- Trade count: 225 over 3 days — most liquid OTM strike
- Delta: ~0.20 → hedge by shorting 0.20 VEV per unit long
- Recommended strategy: Passive buy only when live 5d BS/fitted-smile fair value clears the bid by at least spread plus safety margin. Delta hedge in VEV. Net risk: gamma + vega.
- Recommended position sizing: small, around 20–40 units unless live fills/markouts confirm the edge.
- Confidence: medium-low — this is the best long-gamma candidate, but the edge is TTE-sensitive and live TTE=5 is outside the historical 8/7/6 sample.

---

## Findings: VEV_5500

- Fair value: BS ≈ 5–12 by day. Market mean ≈ BS + 0.2 (near-fair).
- Spread: narrow
- Recommended strategy: Passive MM — fair value, no directional edge.
- Confidence: medium-high.

---

## Findings: VEV_6000

- Market mid = **0.5** across the sample (pinned at min tick: bid=0, ask=1). Std = 0.000.
- Spread: 1.0 (bid=0, ask=1 — single-sided market)
- Trade count: 284 over 3 days (noise trades at floor price)
- P(VEV > 6000 at expiry from S=5250) is very sensitive to the volatility convention; use live 5d pricing with the same calendar-day annualization as the EDA before sizing this trade.
- Recommended strategy: Passive sell at ask = 1 only in small size. Do not treat the floor artifact as guaranteed edge.
- Recommended position sizing: capped and cautious; fill quality matters more than theoretical mid.
- Confidence: medium — pinned floor is real historically, but live jump/tail risk is out-of-sample.

---

## Findings: VEV_6500

- Market mid = **0.5** across the sample (identical to VEV_6000 — pinned at min tick).
- Spread: 1.0
- Trade count: 284 over 3 days in the generated data summary
- P(VEV > 6500 at expiry) is very sensitive to the volatility convention; use live 5d pricing with the same calendar-day annualization as the EDA before sizing this trade.
- Recommended strategy: Passive sell at ask = 1 only in small size. Same floor-artifact caution as VEV_6000.
- Recommended position sizing: capped and cautious.
- Confidence: medium.

---

## Cross-Product Notes

1. HGP and VEV structurally similar (both stationary FV, both contrarian OBI) but uncorrelated — trade independently.
2. Historical generated EDA uses 8/7/6 days to expiry; live trading uses 5 days, so OTM option fair values need live 5d pricing rather than historical markdown numbers.
3. VEV_5400 is the best long-gamma candidate, but only as a passive, size-capped trade because the old 3-4 tick edge was based on stale TTE assumptions.
4. VEV_6000/6500 are floor-artifact carry candidates, not guaranteed free carry; sell only passively and with capped size.
5. Deep ITM (4000/4500): skip entirely — no option edge, just VEV delta-1 exposure with extra spread cost.
