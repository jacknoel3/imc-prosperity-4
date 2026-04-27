# Round 4 Probe Findings

Generated from 19 pair-probe backtests, 4 confirm-probes, 3 production-like
strategy tests, and 8 motivation probes in `phase2/round4/algo/backtests`.

## Data Shape

- Total enriched trade events across logs: 24,169.
- SUBMISSION trade events across all logs: 19,959.
- Deduplicated non-SUBMISSION market trades: 196.
- Important caveat: each backtest replays the same market day. External player-player trades are therefore duplicated across logs unless we use the `market_unique_*` outputs.

## Strongest Findings

1. Mark 01 is a strong voucher/convexity buyer.
   - In `probe_pair_mark01_mark22`, Mark 01 bought from SUBMISSION 866 times for 3,031 qty across VEV_5200-6500.
   - Mark 01 buyer markout on those fills is positive across all targeted voucher products, especially VEV_5200 and VEV_5300.
   - Deduplicated market trades show Mark 01 -> Mark 22 on vouchers: 58 trades, 198 qty, avg buyer markout 10 of +0.517.

2. Mark 14 is highly informed on HYDROGEL_PACK and VEV_4000.
   - In `probe_pair_mark14_mark38`, Mark 14 bought from SUBMISSION 316 times for 1,176 qty.
   - Mark 14 buyer markout 10: +8.91 on HYDROGEL_PACK and +9.81 on VEV_4000.
   - Deduplicated Mark 14 -> Mark 38 market trades: 23 trades, 82 qty, avg buyer markout 10 of +7.609.
   - Production-like test update: in `strat46_round4_player_profile`, Mark 14
     remained highly toxic against SUBMISSION on both sides. In
     `strat47_round4_hard_player_filters`, exposure fell sharply, but remaining
     fills were still adverse. The lesson is that Mark 14 protection must be
     pre-emptive, not only reactive after a fill.

3. Mark 38 is weak/fadeable on HYDROGEL_PACK and VEV_4000.
   - Deduplicated Mark 38 -> Mark 14 trades have avg buyer markout 10 of -7.306.
   - SUBMISSION trades against Mark 38 were profitable on both sides in aggregate: avg markout 10 around +3.5 to +3.6 depending on side.
   - Production-like test update: Mark 38 stayed profitable in both strategy
     tests. `strat46` had Mark 38 MO10 around +4.7/+5.1 by side; `strat47`
     had Mark 38 MO10 around +5.9/+4.9. This is one of the most robust signals.

4. Mark 55 is a useful liquidity/noise source in VELVETFRUIT_EXTRACT.
   - When Mark 55 sold to SUBMISSION, our avg markout 10 was +2.77 and win rate 10 was 81.8% in the current samples.
   - When Mark 55 bought from SUBMISSION, our avg markout 10 was +4.30 and win rate 10 was 100%.
   - Deduplicated Mark 55 -> Mark 14 and Mark 55 -> Mark 01 trades have negative buyer markouts, consistent with Mark 55 being weaker than Mark 14/01.

5. Mark 67 appears bullish/informed in player-player VELVETFRUIT_EXTRACT flow, but direct probe evidence is still thin.
   - In our SUBMISSION fills, Mark 67 appeared as seller to us: 13 fills, 52 qty, our avg markout 10 +2.50, avg markout 50 +9.50.
   - Deduplicated market trades: Mark 67 -> Mark 49 has 3 trades, 29 qty, avg buyer markout 10 +1.00; Mark 67 -> Mark 22 has 3 trades, 20 qty, avg buyer markout 10 +0.833.
   - Needs more targeted sampling before using Mark 67 as a high-confidence production trigger, because the direct fills and player-player flow currently point in different directions.
   - Production-like update: direct Mark 67 fills remained sparse. When Mark 67
     bought VE from SUBMISSION in `strat46`, SUBMISSION markout was negative
     (MO10 -1.0, MO50 -5.5), which supports a "do not sell to Mark 67 / tiny
     follow-buy" rule.

6. Mark 22 remains mostly a structural seller/source, but less toxic than Mark 38 and less informative than Mark 14/01.
   - In deduplicated market data Mark 22 has 65 seller trades and only 1 buyer trade.
   - SUBMISSION fills against Mark 22 are mildly positive overall, especially when Mark 22 buys from us, but direct target fill count is small.
   - Production-like update: direct SUBMISSION fills against Mark 22 did not
     confirm standalone cheap convexity. The useful signal is contextual:
     Mark 01/14 buying vouchers from Mark 22 remains informative; Mark 22 alone
     should not override the product-alpha engine.

## Production-Like Strategy Lessons

`strat46_round4_player_profile`:
- Profit +9,055, but HGP ended at -3,664.
- It proved that the Round 3 voucher engine still has strong product-alpha
  value in Round 4.
- It also proved that soft player overlays were not enough against Mark 14/01.

`strat47_round4_hard_player_filters`:
- Profit +2,970 and HGP improved to +214.
- Fill count fell from 417 to 87, so broad hard blocking starved the voucher
  engine.
- Conclusion: use hard guards only where clearly needed, especially HGP vs
  Mark 14; use soft quote skew for vouchers and VE.

`strat48_round4_hgp_hard_voucher_soft`:
- Profit +12,605, best production-like result so far.
- HGP stayed fixed at +214 while voucher PnL recovered strongly.
- Confirms the right architecture: product alpha first, narrow hard guards only
  where proven, soft player skew elsewhere.

## Motivation Probe Lessons

- Mark14: confirmed as informed/toxic. Motivation probes show that direct
  exposure to Mark14 dominates losses when probe design is loose.
- Mark01: confirmed as informed convexity/VE demand. Bait/follow tests against
  Mark01 are negative; use it as a no-cheap-sell and demand signal.
- Mark22: confirmed as structural source in the network, not standalone direct
  edge for SUBMISSION.
- Mark38: confirmed fadeable, but probe design must isolate Mark38 from Mark14.
- Mark55: direct Mark55 VE fills are positive, but broad VE modes lose when
  Mark01/14 are also active.
- Mark67: bullish VE signal remains sparse; avoid selling to it, do not chase
  large follow buys.
- Mark49: contextual VE source around Mark67, limited standalone edge.

Detailed report:
- `phase2/round4/algo/backtests/player_profile_current/motivation_probe_findings.md`

## Probe Mechanics

- Passive/bait modes produced positive markouts:
  - `SYMMETRIC_PASSIVE`: avg markout 10 +4.10.
  - `SELL_BAIT_FOR_TARGET_BUYER`: avg markout 10 +3.47.
  - `BUY_BAIT_FOR_TARGET_SELLER`: avg markout 10 +3.26.
- Taker and flattening modes produced negative markouts:
  - `TAKE_ASK_IDENTIFY_RESTING_SELLER`: avg markout 10 -3.71.
  - `HIT_BID_IDENTIFY_RESTING_BUYER`: avg markout 10 -3.19.
  - `INVENTORY_FLATTEN`: avg markout 10 -3.00.
- Conclusion: for future profiling, use much less taker probing unless the goal is pure identification, and keep passive bait as the main data collection mechanism.

## Tests Still Worth Running

1. `strat49_round4_player_anticipation`.
   - Goal: use learned player timing windows to prepare before high-probability
     bot activity.
   - Success condition: improve PnL retention versus `strat48`, keep HGP
     positive, and reduce toxic Mark14/Mark01 fill share.

2. More Mark 67 VELVETFRUIT_EXTRACT probes.
   - Goal: confirm whether Mark 67 is genuinely informed or just sparse/lucky.
   - Use passive ask bait for Mark 67 and avoid broad VE noise.

3. Dedicated Mark 22 liquidity-source probes on vouchers.
   - Goal: test whether we can buy from Mark 22 cheaply without getting overrun by Mark 01/14.
   - Focus VEV_5200-5500; only touch VEV_6000/6500 at zero/near-zero.

4. Mark 38 fade probes with reduced taker footprint.
   - Goal: confirm Mark 38 weakness while avoiding the large PNL bleed caused by aggressive taker modes.
   - Products: HYDROGEL_PACK, VEV_4000, plus limited VEV_4500-5200.

5. Mark 14 avoidance/following probes.
   - Goal: distinguish whether best action is to follow Mark 14 immediately, avoid quoting against them, or widen sharply.
   - Products: HYDROGEL_PACK, VEV_4000, VELVETFRUIT_EXTRACT.

## Metrics To Add Next

- Per-fill quote distance from fair and from best bid/ask.
- Counterparty-specific adverse selection curve by horizon: 1, 5, 10, 50, 100, 500 ticks.
- Conditional markout by mode, product, side, inventory sign, and timestamp bucket.
- Deduplicated player-player marketout with bootstrap confidence intervals.
- Toxicity score: signed expected markout per unit quantity adjusted by fill count.
- Passive fill probability by quote offset and counterparty.
- Player lead-lag: after Mark X buys/sells, probability and magnitude of mid move over the next N ticks.
- Trigger quality: PnL if we follow/fade each player for 1/5/10/50 ticks.
- Motivation tests: response curve to quote offset, size, persistence, and
  cross-product hedging opportunities after a player appears.

## Strategy Implications

- Follow Mark 01 on vouchers; do not sell convexity to Mark 01 cheaply.
- Follow or protect against Mark 14 on HYDROGEL_PACK, VEV_4000, and probably VELVETFRUIT_EXTRACT.
- Fade Mark 38, especially versus Mark 14-linked products.
- Use Mark 55 as VE liquidity; buys from Mark 55 look attractive, and selling to Mark 55 can also be acceptable in this sample.
- Treat Mark 67 player-player buying as a bullish VE signal, but size small until more direct samples arrive.
- Reduce production use of blind taker probes; they identify counterparties but are expensive.
- Separate product alpha from execution alpha. Product models should decide
  fair value and target inventory; player profiles should decide whether to
  quote, skew, reduce size, follow, or fade.
