# Motivation Probe Findings

Generated after the 8 motivation probes:

- `probe_motive_mark01_convexity`
- `probe_motive_mark14_cross_product`
- `probe_motive_mark22_source_context`
- `probe_motive_mark38_weakness`
- `probe_motive_mark55_liquidity`
- `probe_motive_mark67_informed_ve`
- `probe_motive_mark49_liquidity_source`
- `probe_motive_all_players_matrix`

Important caveat:
- These probes were not designed to maximize PnL.
- They deliberately provoke fills to separate player-motivation hypotheses.
- Negative PnL can be a useful result if it reveals that a player is toxic or
  that a proposed follow/fade rule is wrong.

## High-Level Results

| Probe | Profit | Main Conclusion |
|---|---:|---|
| probe_motive_mark01_convexity | -7,440.531 | Mark 01 is not a safe counterparty to bait. Follow/convexity tests were toxic overall. |
| probe_motive_mark14_cross_product | +69.333 | Mark 14 trigger handling works only when it routes us into Mark38/Mark55-type counterparties; direct Mark14 remains toxic. Same-product and cross-product follow both positive in this controlled probe. |
| probe_motive_mark22_source_context | -2.296 | Mark22 direct source remains hard to access. Context bid produced only 2 fills, against Mark38, not Mark22. |
| probe_motive_mark38_weakness | -32,015.305 | The initial design accidentally routed us into Mark14, not Mark38. This strongly confirms Mark14 toxicity and rejects aggressive Mark38-probe design that exposes us to Mark14. |
| probe_motive_mark55_liquidity | -5,647.782 | Mark55 itself remains good, but broad VE fade modes are dominated by toxic Mark01/14 flow. Need counterparty-specific VE filtering. |
| probe_motive_mark67_informed_ve | -1,563.406 | Mark67 direct sample remains sparse. Follow-after-Mark67-buy was not profitable because fills mostly came from Mark01/14. |
| probe_motive_all_players_matrix | +838.833 | Broad passive offset/size sampling is useful. Best mode was `OFFSET_1_SIZE_3`, confirming passive small/medium quotes exploit Mark38/Mark55 better than broad taker logic. |
| probe_motive_mark49_liquidity_source | -155.406 | Context bids after Mark67/49 were better than standalone bids, supporting Mark49 as contextual VE source rather than generic edge. |

## Player Conclusions

### Mark 14

Official view:
- Mark 14 is the strongest informed/toxic player.
- Directly trading against Mark14 is bad.
- Mark14 information can still be useful if it routes us toward weaker players
  such as Mark38, but the strategy must avoid becoming Mark14's liquidity.

Evidence:
- Production-like tests: Mark14 remained negative for SUBMISSION in both
  `strat46` and `strat47`.
- Motivation test `probe_motive_mark14_cross_product` had positive overall
  markout because fills came mostly against Mark38 and Mark55, not because
  direct Mark14 fills became good.
- `probe_motive_mark38_weakness` lost heavily because it exposed SUBMISSION to
  Mark14 in HGP/VEV_4000.

Action:
- Hard protect HGP around Mark14.
- On vouchers/VE, use soft skew and reduced toxic-side size.
- Do not blindly follow Mark14 with marketable orders unless the product edge is
  already strong.

### Mark 01

Official view:
- Mark 01 is an informed convexity/VE participant.
- Baiting Mark01 with passive asks is dangerous.
- Mark01 activity is best used as "do not sell cheap convexity" and as a
  contextual signal for voucher demand.

Evidence:
- `probe_motive_mark01_convexity` lost -7,440.531.
- Mark01 direct fills in that probe were adverse on both BUY and SELL sides:
  SUBMISSION BUY from Mark01 MO10 -1.092; SUBMISSION SELL to Mark01 MO10 -0.837.
- Same-strike and adjacent-strike follow modes were negative overall.

Action:
- Avoid selling vouchers/VE cheaply into Mark01 windows.
- Use Mark01 -> Mark22 market trades as a voucher-demand signal, not as a reason
  to directly bait Mark01.

### Mark 22

Official view:
- Mark22 is a structural voucher source in the network.
- Mark22 is not yet proven as a direct standalone edge for SUBMISSION.
- Mark22 matters most when Mark01/14 are buying vouchers from Mark22.

Evidence:
- `probe_motive_mark22_source_context` produced only 2 own fills, both against
  Mark38 on VEV_5200, not Mark22.
- Historical and market-unique logs still show persistent Mark01 -> Mark22
  voucher buying.

Action:
- Treat Mark22 as contextual source signal.
- Do not let Mark22 alone override product fair value.

### Mark 38

Official view:
- Mark38 is still fadeable, but the probe design must avoid routing into Mark14.
- Mark38 weakness is most usable when we can actually identify/fill Mark38
  without becoming Mark14's counterparty.

Evidence:
- `probe_motive_all_players_matrix`: Mark38 fills were strongly positive:
  BUY from Mark38 MO10 +5.065; SELL to Mark38 MO10 +3.935.
- `probe_motive_mark38_weakness` lost heavily because most fills were against
  Mark14, not Mark38.

Action:
- Keep Mark38 fade.
- Combine it with Mark14 avoidance.
- Do not run broad HGP/VEV_4000 probes that allow Mark14 to dominate fills.

### Mark 55

Official view:
- Mark55 remains a useful VE liquidity/noise source.
- Mark55 is good only when isolated from Mark01/14 toxic flow.

Evidence:
- In `probe_motive_mark55_liquidity`, Mark55 direct fills were positive:
  BUY from Mark55 MO10 +1.000; SELL to Mark55 MO10 +1.583.
- The whole probe lost because VE fills against Mark01/14 were strongly toxic.

Action:
- Trade VE against Mark55, but suppress VE quoting when Mark01/14 are active.
- Use Mark55 as liquidity source, not as broad VE direction.

### Mark 67

Official view:
- Mark67 remains a bullish/informed VE signal, but direct exploitation is still
  hard because other toxic players dominate the fills.
- Selling VE to Mark67 is not attractive.

Evidence:
- `probe_motive_mark67_informed_ve`: direct Mark67 fills were sparse and
  adverse for SUBMISSION.
- `ASK_BAIT_MARK67` mode was positive, mostly because it captured Mark55/Mark67
  flow; `FOLLOW_MARK67_BUY` was negative because it routed into Mark01/14.
- Market-network Mark67 buying remains positive.

Action:
- Treat Mark67 buy as "do not sell cheap VE" and a small bullish bias.
- Do not chase with large marketable follow orders.

### Mark 49

Official view:
- Mark49 is a contextual VE source, especially around Mark67.
- Mark49 is not a large direct standalone edge.

Evidence:
- `probe_motive_mark49_liquidity_source`: context bid mode MO10 +2.667 and MO50
  +5.278; standalone bid mode MO10 +0.611 and MO50 -0.611.
- Direct fills were mostly Mark55/Mark67, confirming Mark49 is sparse.

Action:
- Use Mark49 mainly as part of Mark67/VE source context.

## Timing/Hazard Map From Day-3 Backtests

Market-unique day-3 logs show clustered player activity:

- Mark01: mostly vouchers/VE; top 10k buckets 30k, 70k, 90k, 40k.
- Mark22: mostly voucher seller; top 10k buckets 30k, 70k, 40k, 90k.
- Mark14: HGP, VE, VEV_4000; top 10k buckets 30k, 20k, 80k, 90k.
- Mark38: HGP, VEV_4000; top 10k buckets 30k, 20k, 80k, 0k.
- Mark55: VE; top 10k buckets 30k, 80k, 20k, 90k.
- Mark67: VE; top 10k buckets 30k, 70k, 20k, 50k.
- Mark49: VE; top 10k buckets 70k, 90k, 60k.

These are not guaranteed future events, but they are good hazard windows for
pre-positioning and risk reduction.

## Final Operational Map

- Informed/toxic: Mark14, Mark01.
- Fadeable/weak: Mark38.
- Liquidity/noise source: Mark55.
- Contextual source: Mark22 on vouchers, Mark49 on VE.
- Sparse bullish/informed VE signal: Mark67.

The map is operationally strong, but not metaphysically complete. We know how
these players behave in the observed environment and how to exploit or avoid
them. We do not know their true internal utility functions.
