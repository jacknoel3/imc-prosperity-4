# Round 4 Bot Profiling - Current Complete Snapshot

Generated from:
- Public Round 4 CSV data in `phase2/round4/algo/data`.
- Raw/json backtest logs currently present in `phase2/round4/algo/backtests`.

Scope:
- Public datasets measure historical player-player behavior.
- Raw backtest logs measure SUBMISSION fills, probe modes, adverse selection, and bot profitability.
- Current raw logs include 23 tests: 19 original pair probes plus 4 confirm probes.

## Sanity Checks

Dataset profile:
- Trade events: 4,281.
- Price rows: 360,000.
- Missing mid at trade timestamp: 0.
- Pair-product rows: 41.
- Player rows: 7.
- Network edges: 19.

Log profile:
- Enriched trade events: 17,444.
- SUBMISSION trade events: 14,556.
- Deduplicated non-SUBMISSION market trades from logs: 164.
- Pair-product rows: 100.
- Player rows: 8.
- Network edges: 29.
- Bot decision rows: 23.

Win rates exclude rows with missing future-mid values. Positive SUBMISSION markout means the fill moved in our favor; negative means adverse selection.

## Public Dataset - Historical Player Map

| Player | Buyer Trades | Seller Trades | Buyer Qty | Seller Qty | Buyer MO10 | Seller MO10 | Buyer Win10 | Seller Win10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Mark 01 | 1599 | 244 | 6053 | 1375 | +0.928 | +3.000 | 0.892 | 0.852 |
| Mark 14 | 1127 | 1045 | 4510 | 4208 | +6.437 | +6.639 | 0.857 | 0.882 |
| Mark 22 | 42 | 1542 | 206 | 5683 | +1.131 | -0.559 | 0.619 | 0.080 |
| Mark 38 | 733 | 745 | 2493 | 2507 | -8.455 | -8.766 | 0.060 | 0.070 |
| Mark 49 | 17 | 105 | 115 | 1071 | -0.471 | -1.471 | 0.412 | 0.314 |
| Mark 55 | 598 | 600 | 3254 | 3297 | -2.482 | -2.428 | 0.202 | 0.198 |
| Mark 67 | 165 | 0 | 1510 | 0 | +1.445 | n/a | 0.667 | n/a |

Top historical edges:
- Mark 01 -> Mark 22: 1,339 trades, 4,636 qty, VEV_5200-6500, buyer MO10 +0.558, win10 0.910.
- Mark 14 -> Mark 38: 728 trades, 2,447 qty, HGP/VEV_4000, buyer MO10 +8.953, win10 0.922.
- Mark 38 -> Mark 14: 714 trades, 2,445 qty, HGP/VEV_4000, buyer MO10 -8.653, win10 0.057.
- Mark 55 -> Mark 14: 331 trades, 1,763 qty, VE, buyer MO10 -2.295, win10 0.218.
- Mark 14 -> Mark 55: 316 trades, 1,761 qty, VE, buyer MO10 +2.176, win10 0.772.
- Mark 01 -> Mark 55: 260 trades, 1,417 qty, VE, buyer MO10 +2.837, win10 0.800.
- Mark 55 -> Mark 01: 244 trades, 1,375 qty, VE, buyer MO10 -3.000, win10 0.148.
- Mark 67 -> Mark 49: 89 trades, 963 qty, VE, buyer MO10 +1.646, win10 0.674.
- Mark 67 -> Mark 22: 75 trades, 546 qty, VE, buyer MO10 +1.153, win10 0.653.

## Confirm-Probe Results

| Bot | Profit | Fills | Qty | MO10 | MO50 | Win10 | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| probe_confirm_mark14_follow_avoid | +466.888 | 85 | 174 | +3.417 | +4.756 | 0.765 | KEEP_DEVELOP_MARK14_FOLLOW_AVOID |
| probe_confirm_mark22_voucher_seller | -100.350 | 4 | 7 | -0.750 | +1.250 | 0.250 | INCONCLUSIVE_RERUN_MORE_PASSIVE_BIDS |
| probe_confirm_mark38_fade | +452.309 | 47 | 104 | +3.936 | +6.250 | 0.745 | KEEP_DEVELOP_MARK38_FADE |
| probe_confirm_mark67_ve | +326.047 | 26 | 78 | +2.340 | +2.348 | 0.692 | KEEP_SMALL_SIZE_MARK67_VE_CONFIRM |

Confirm details:
- `probe_confirm_mark14_follow_avoid`: strongest on HGP and VEV_4000. Against Mark 38, SUBMISSION MO10 is +5.208 on buys and +5.706 on sells. VE against Mark 55 is also positive but smaller.
- `probe_confirm_mark38_fade`: clean confirmation that Mark 38 is fadeable. HGP and VEV_4000/4500 are strongest; VEV_5100/5200 are weak and should be de-emphasized.
- `probe_confirm_mark67_ve`: profitable as a VE bot, but the strongest fills are against Mark 55. Direct Mark 67 evidence is still thin and mixed: Mark 67 BUY fills MO10 +0.500, Mark 67 SELL fills MO10 -2.167.
- `probe_confirm_mark22_voucher_seller`: only 4 fills, all against Mark 38 on VEV_5200/5300. This does not provide enough evidence about Mark 22 as direct counterparty.

## Original Pair-Probe Lesson

The original 19 pair-probes were useful for sampling but are not trading logic:
- Taker-heavy identification modes generated too much adverse selection.
- Mark 14 and Mark 01 are toxic counterparties to trade against blindly.
- Passive bait/fade logic is much better than aggressive taker probing.
- Repeated VE pair-probes converged to the same negative profile: roughly -3,322 profit, 369 fills, MO10 around -2.004.

## Working Bot Interpretation

- Mark 14: strongest informed player. Follow/avoid logic is confirmed and should be developed further.
- Mark 38: weak/fadeable, especially HGP and VEV_4000/4500. Avoid pushing the fade too far into higher vouchers where the signal decays.
- Mark 01: informed historical buyer, especially vouchers and VE vs Mark 55. Avoid selling cheap convexity into Mark 01.
- Mark 22: structural voucher seller historically, but our direct confirm sample is not large enough. Still likely important, not yet fully quantified from SUBMISSION logs.
- Mark 55: noisy/liquidity source on VE. Good candidate to trade against, especially when not conflicting with Mark 14/67 signals.
- Mark 67: historically bullish VE buyer. Current bot-level VE test is positive, but direct Mark 67 fill count remains too small for a final microstructure rule.
- Mark 49: mostly liquidity/noisy seller in historical VE network; limited direct SUBMISSION evidence.

## Remaining Tests

Required if we want full confidence:
- Rerun Mark 22 voucher-seller probing with wider/more persistent passive bids. Current direct sample is only 4 fills.
- Rerun Mark 67 VE probing if the goal is specifically to validate direct Mark 67 behavior, not just profitable VE behavior around the same regime.

Not urgent:
- Mark 14 follow/avoid is already confirmed by dataset and logs.
- Mark 38 fade is already confirmed by dataset and logs.
- Original pair-probes should not be repeated in the same taker-heavy form.

## Main CSVs

Best single decision file:
- `phase2/round4/algo/backtests/player_profile_current/bot_master_decision_matrix.csv`

Detailed supporting files:
- `trade_events_enriched.csv`: every enriched log trade/fill.
- `submission_trade_events.csv`: every SUBMISSION fill.
- `bot_counterparty_exposure.csv`: bot x counterparty x side.
- `bot_product_own_fills.csv`: bot x product x side.
- `phase2/round4/algo/data/dataset_profile_current/dataset_player_metrics.csv`: public historical player metrics.
- `phase2/round4/algo/data/dataset_profile_current/dataset_network_edges.csv`: public historical buyer-seller network.
