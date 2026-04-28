# Round 4 Algorithmic Research Journal

Last updated: 2026-04-27

Purpose: this is the living journal for Round 4 algorithmic work. It records
what we tested, what each test answered, what we rejected, what remains open,
and which player/product rules are currently strong enough to guide strategy
design.

## Current Bottom Line

- Best confirmed production-like strategy so far: `strat53_round4_strat48_surgical_filters.py`, PnL `+13749.17`.
- The real `strat52_round4_hybrid_player_surface.py` has now been tested as run `508943`.
- `strat52` did not beat `strat48`: PnL `+11754.90` versus Strat48 `+12604.75`.
- `strat53` beat both by keeping only the validated Strat52 filters and removing the failed VEV_5300 overlay.
- Strongest product-alpha block: `VEV_5000`, `VEV_5100`, `VEV_5200`.
- Strongest clean player edge: fade Mark 38, especially on `HYDROGEL_PACK`, `VEV_4000`, `VEV_4500`.
- Strongest toxicity rule: avoid direct liquidity provision to Mark 14 and Mark 01 unless the product edge is overwhelming.
- Strongest rejected idea: aggressive long `VEV_5400` against Mark 01 / Mark 14 sellers.

## Data And Tooling Built

- Read Round 4 rules: `phase2/round4/algo/data/round4_rules_and_algo_instructions.rtf`.
  - Products and limits match Round 3.
  - New useful data: `buyer` and `seller` fields in trades.
- Updated converter: `log_converter/converter.py`.
  - Added `Buyer` and `Seller` to converted CSV output.
  - Caveat: raw `tradeHistory` remains better for exact trade-product-counterparty profiling.
- Built player log profiler: `phase2/round4/algo/analysis/player_profile_from_logs.py`.
  - Outputs enriched fills, pair metrics, player metrics, network edges, markouts.
- Built dataset profiler: `phase2/round4/algo/analysis/round4_dataset_profile_metrics.py`.
- Built bot summary script: `phase2/round4/algo/analysis/bot_probe_summary_from_profiles.py`.
- Built option surface/edge script: `phase2/round4/algo/analysis/round4_option_surface_edges.py`.
  - Uses optional Numba acceleration.
  - Main outputs in `phase2/round4/algo/data`:
    - `round4_option_surface_edge_timeseries.csv`
    - `round4_option_surface_edge_summary.csv`
    - `round4_option_surface_actionable_edges.csv`
    - `round4_option_surface_fit_quality.csv`

## Dataset Facts

- Round 4 day 1/2 prices match Round 3 day 1/2 prices.
- Round 4 day 1/2 trades match Round 3 on timestamp, symbol, price, quantity, but now include `buyer` and `seller`.
- Main mapping files:
  - `phase2/round4/algo/data/counterparties_types_and_preferences.csv`
  - `phase2/round4/algo/data/counterparties_product_level_mapping.csv`
- Historical player-player structure:
  - Mark 01 buys voucher convexity from Mark 22, especially `VEV_5200` through `VEV_6500`.
  - Mark 14 and Mark 38 dominate `HYDROGEL_PACK` and `VEV_4000`.
  - Mark 55 is mostly `VELVETFRUIT_EXTRACT` liquidity/noise.
  - Mark 67 is a sparse but bullish-looking `VELVETFRUIT_EXTRACT` buyer.

## Player Map

### Mark 14

- Current view: strongest informed/toxic player.
- Products: `HYDROGEL_PACK`, `VEV_4000`, `VEV_4500`, `VELVETFRUIT_EXTRACT`, core vouchers.
- Evidence:
  - Pair/motivation probes show direct Mark 14 exposure often dominates losses.
  - `probe_pair_mark14_mark38.py` and related HGP/VEV_4000 probes confirmed Mark 14 informational strength.
  - In Strat48 logs, Mark 14 generated many of the worst markout trades, especially on `VEV_4000/4500`.
- Action:
  - Hard protect HGP.
  - Avoid deep ITM direct trading with Mark 14.
  - Use Mark 14 as information, not as a counterparty to feed.

### Mark 01

- Current view: informed convexity/VE participant.
- Products: vouchers, especially `VEV_5200+`, plus `VELVETFRUIT_EXTRACT`.
- Evidence:
  - `probe_pair_mark01_mark22.py` showed Mark 01 as strong voucher buyer from Mark 22.
  - `probe_motive_mark01_convexity.py` lost heavily when baiting/following Mark 01 directly.
  - Surface probes showed buying `VEV_5400` from Mark 01 was toxic.
- Action:
  - Do not sell cheap convexity or VE into Mark 01.
  - Use Mark 01 activity as demand signal, not as permission for blind taker trades.

### Mark 38

- Current view: most fadeable bot.
- Products: `HYDROGEL_PACK`, `VEV_4000`, `VEV_4500`; smaller signal on nearby vouchers.
- Evidence:
  - `probe_confirm_mark38_fade.py` positive.
  - `probe_tail_deep_contract_diagnostics.py` showed clean positive Mark 38 markouts on `VEV_4000/4500`.
  - Strat48 logs: Mark 38 fills positive on both sides in HGP and deep ITM vouchers.
- Action:
  - Fade Mark 38 with small-to-medium size.
  - Do not let Mark 38 probes accidentally route us into Mark 14.

### Mark 55

- Current view: noisy/liquidity source on `VELVETFRUIT_EXTRACT`.
- Evidence:
  - Direct VE fills against Mark 55 often have positive markout.
  - `probe_motive_mark55_liquidity.py` lost because broad VE modes were polluted by Mark 01/14, not because Mark 55 itself was bad.
- Action:
  - Trade VE against Mark 55 selectively.
  - Add filters so Mark 55 modules do not become Mark 01/14 exposure.

### Mark 22

- Current view: structural voucher seller/source, weak standalone direct edge.
- Evidence:
  - Player-player data: Mark 22 sells vouchers to Mark 01/14.
  - Direct Mark 22 probes produced too few clean SUBMISSION fills.
- Action:
  - Use Mark 22 only contextually with Mark 01/14 voucher demand.
  - Do not use Mark 22 alone as a production trigger.

### Mark 67

- Current view: sparse bullish VE signal, not enough direct sample.
- Evidence:
  - Historical `Mark 67 -> Mark 49/22` VE buying is bullish.
  - Direct SUBMISSION fills are sparse and mixed.
  - Selling VE to Mark 67 was negative in Strat48-style logs.
- Action:
  - Avoid selling VE to Mark 67.
  - Follow only tiny size until more direct evidence exists.

### Mark 49

- Current view: contextual VE source, mostly useful near Mark 67 flow.
- Action:
  - Treat as secondary liquidity source, not standalone high-confidence alpha.

## Pair Probe Phase

Generated 19 pair probes in `phase2/round4/algo/strategy`:

- `probe_pair_mark01_mark22.py`
- `probe_pair_mark01_mark55.py`
- `probe_pair_mark14_mark22.py`
- `probe_pair_mark14_mark38.py`
- `probe_pair_mark14_mark55.py`
- `probe_pair_mark22_mark38.py`
- `probe_pair_mark22_mark49.py`
- `probe_pair_mark22_mark55.py`
- `probe_pair_mark38_mark14.py`
- `probe_pair_mark38_mark22.py`
- `probe_pair_mark49_mark22.py`
- `probe_pair_mark49_mark55.py`
- `probe_pair_mark55_mark01.py`
- `probe_pair_mark55_mark14.py`
- `probe_pair_mark55_mark22.py`
- `probe_pair_mark55_mark49.py`
- `probe_pair_mark67_mark22.py`
- `probe_pair_mark67_mark49.py`
- `probe_pair_mark67_mark55.py`

What they revealed:

- Mark 14 and Mark 01 are dangerous direct counterparties.
- Mark 38 and Mark 55 are the main exploitable liquidity/noise players.
- Mark 22 and Mark 67 are useful mostly as contextual signals.
- Broad aggressive/taker probing is expensive and should not be reused as a production style.

## Confirmation And Motivation Probes

Files:

- `probe_confirm_mark14_follow_avoid.py`
- `probe_confirm_mark22_voucher_seller.py`
- `probe_confirm_mark38_fade.py`
- `probe_confirm_mark67_ve.py`
- `probe_motive_mark01_convexity.py`
- `probe_motive_mark14_cross_product.py`
- `probe_motive_mark22_source_context.py`
- `probe_motive_mark38_weakness.py`
- `probe_motive_mark49_liquidity_source.py`
- `probe_motive_mark55_liquidity.py`
- `probe_motive_mark67_informed_ve.py`
- `probe_motive_all_players_matrix.py`

Main lessons:

- Mark 14 toxicity is robust.
- Mark 01 baiting is bad.
- Mark 38 is fadeable only if isolated from Mark 14.
- Mark 55 is good only when VE flow is counterparty-specific.
- Mark 67 remains open because direct samples are too sparse.
- Mark 22 is a network source, not a standalone direct edge.

## Product And Option Surface Phase

Main EDA source:

- `phase2/round3/algo/analysis/ve_vev_eda.py`
- Output tables in `phase2/round3/algo/analysis/ve_vev_eda_output/tables`

Round 4 surface output:

- `phase2/round4/algo/data/round4_option_surface_edge_summary.csv`

Surface conclusions:

- `VEV_5300` looks rich and is a sell/short candidate.
- `VEV_5400` looks cheap theoretically, but logs reject aggressive executable long exposure.
- `VEV_5200` is unstable as a curvature leg, despite working inside the Strat48 product engine.
- `VEV_4000/4500` are deep ITM, mostly delta-like, and should be Mark38 fade diagnostics.
- `VEV_6000/6500` should not be bought at 1. Round 3 tail test lost about `-599.77`.

## Surface Strategy Tests

Files:

- `probe_surface_residual_core.py`
- `probe_vertical_5300_5400_relative_value.py`
- `probe_curvature_mean_reversion_5200_5300_5400.py`
- `probe_delta_band_hedged_surface.py`
- `probe_player_filtered_surface.py`
- `probe_tail_deep_contract_diagnostics.py`
- `probe_adaptive_surface_market_maker.py`
- `strat51_round4_surface_player_inventory.py`
- Manifest: `surface_strategy_test_manifest.csv`

Results:

- `probe_surface_residual_core.py`: positive, mainly because short `VEV_5300` worked; long `VEV_5400` lost.
- `probe_vertical_5300_5400_relative_value.py`: positive but inferior to isolating `VEV_5300`; `VEV_5400` leg lost.
- `probe_curvature_mean_reversion_5200_5300_5400.py`: negative; `VEV_5200` curvature leg was bad.
- `probe_delta_band_hedged_surface.py`: negative; naive hedging did not control exposure.
- `probe_player_filtered_surface.py`: negative; filters still allowed toxic `VEV_5400` buys from Mark 01/14.
- `probe_tail_deep_contract_diagnostics.py`: small positive; clean Mark 38 signal on `VEV_4000/4500`.
- `probe_adaptive_surface_market_maker.py`: near-flat positive; safe but low capacity.
- `strat51_round4_surface_player_inventory.py`: negative; dominated by bad long `VEV_5400`.

Rejected:

- Aggressive long `VEV_5400`.
- Curvature basket `5200/5300/5400` as implemented.
- Naive delta-band hedging.
- Blind tail buying at 1.

Still worth testing:

- Standalone short `VEV_5300`.
- Selling `VEV_5400` only into Mark 01/14 demand.
- Mark38-only deep ITM module.
- Post-fill hedging and stop logic.

## Production Strategy Timeline

### `strat46_round4_player_profile.py`

- PnL: about `+9055`.
- Answered: Round 3 product engine still works in Round 4.
- Failure: HGP and toxic Mark 14/01 interactions cost too much.

### `strat47_round4_hard_player_filters.py`

- PnL: about `+2969`.
- Answered: hard guards can fix HGP.
- Failure: broad hard filtering starved the main voucher engine.

### `strat48_round4_hgp_hard_voucher_soft.py`

- PnL: `+12604.75`, best confirmed result.
- Answered: product alpha first, narrow hard guards, soft player skew elsewhere.
- Strong products: `VEV_5000`, `VEV_5100`, `VEV_5200`.
- Weak products: `VEV_4000`, `VEV_4500`, `VEV_5300`, `VEV_5400`.

### `strat49_round4_player_anticipation.py`

- PnL: about `+9080`.
- Lesson: player anticipation can work, but overfit/over-control reduced capacity.

### `strat50_round4_aggressive_inventory_vacuum.py`

- PnL: about `+10299`.
- Lesson: aggression and inventory unload did not beat Strat48.
- Vacuum/one-sided book edge did not reliably reappear.

### `probe_vacuum_one_sided_books.py`

- PnL: `0`.
- Lesson: no exploitable empty/one-sided book activity in that test.

### `probe_effective_vacuum_thin_books.py`

- PnL: about `+328`.
- Lesson: low-capacity but informative; Mark38 and Mark55 remained useful.

### `strat51_round4_surface_player_inventory.py`

- PnL: about `-2947`.
- Lesson: combined surface/player strategy failed because `VEV_5400` long dominated losses.

### `strat52_round4_hybrid_player_surface.py`

- Run: `508943`.
- PnL: `+11754.90`.
- Answered: removing new long `VEV_5400` works, but the `VEV_5300` overlay and VE filters are not yet good enough.
- What improved versus Strat48:
  - `VEV_5400`: `-129.01 -> 0.00`.
  - `VEV_4000`: `-294.77 -> -239.59`.
- What worsened versus Strat48:
  - `VELVETFRUIT_EXTRACT`: `+458.64 -> -270.72`.
  - `VEV_5300`: `-205.92 -> -456.29`.
  - `VEV_4500`: `-851.74 -> -906.05`.
- What stayed identical:
  - `VEV_5000`: `+5608.54`.
  - `VEV_5100`: `+4929.45`.
  - `VEV_5200`: `+2875.57`.
  - `HYDROGEL_PACK`: `+214.00`.
- Key failure:
  - The intended standalone short `VEV_5300` did not become a clean short module. It traded both sides heavily, ended long `+45`, and increased Mark 01 / Mark 14 exposure.
- Key player lesson:
  - Mark 38 remained profitable; Mark 14 and Mark 01 remained toxic. Strat52 did not reduce toxic core exposure enough.

### `strat53_round4_strat48_surgical_filters.py`

- Run: `509471`.
- PnL: `+13749.17`.
- Answered: the right move after Strat52 was surgical filtering, not adding more overlay logic.
- Improvement versus Strat48: `+1144.42`.
- Improvement versus Strat52: `+1994.27`.
- What worked:
  - `VEV_4500`: `-851.74 -> 0.00`.
  - `VEV_4000`: `-294.77 -> +116.65`.
  - `VEV_5400`: `-129.01 -> 0.00`.
  - `VEV_5300`: `-205.92 -> -104.69`.
  - Max drawdown improved versus Strat48: about `-7343` versus `-9050`.
- What worsened:
  - `VELVETFRUIT_EXTRACT`: `+458.64 -> +109.65`. The stricter VE guard reduced both toxic and profitable VE activity.
- What stayed untouched:
  - `VEV_5000`: `+5608.54`.
  - `VEV_5100`: `+4929.45`.
  - `VEV_5200`: `+2875.57`.
  - `HYDROGEL_PACK`: `+214.00`.
- Key player lesson:
  - The Mark38-only deep ITM idea worked. `VEV_4000` had only 3 fills, all Mark38, and all positive markout.
  - Mark14/Mark01 exposure was reduced sharply but still drives the worst residual core voucher markouts.
  - Mark55 VE remained positive, but only 1 direct fill remained after stricter guards; we may have over-filtered VE.
- Next implication:
  - Keep Strat53 as current baseline.
  - Next test should selectively re-add VE against Mark55 only, while preserving no-sell to Mark67 and no-buy from Mark14/01.
  - Do not re-add broad VEV_4500 or VEV_5400.
  - VEV_5300 still needs either tighter long cap or a cleaner one-sided short rule.

## Strat48/Uploaded Strat52 Folder Analysis

Folder:

- `phase2/round4/algo/backtests/strat52_round4_hybrid_player_surface_logs`

Old upload-mismatch status:

- Contains run `503954`.
- Profit: `+12604.7479`.
- Uploaded `.py` is Strat48, not Strat52.
- Profile outputs saved to `phase2/round4/algo/backtests/strat52_round4_hybrid_player_surface_logs/profile`.

Key numbers from that run:

- `VEV_5000`: `+5608.54`
- `VEV_5100`: `+4929.45`
- `VEV_5200`: `+2875.57`
- `VELVETFRUIT_EXTRACT`: `+458.64`
- `HYDROGEL_PACK`: `+214.00`
- `VEV_5400`: `-129.01`
- `VEV_5300`: `-205.92`
- `VEV_4000`: `-294.77`
- `VEV_4500`: `-851.74`

Interpretation:

- The log reconfirms Strat48.
- It does not validate or reject Strat52.
- It supports Strat52's design direction: remove broad `VEV_5400` long, isolate `VEV_5300` short, and make `4000/4500` Mark38-only.

## True Strat52 Run Analysis

Folder:

- `phase2/round4/algo/backtests/strat52_round4_hybrid_player_surface_logs`

Run:

- `508943`

Profile outputs:

- `phase2/round4/algo/backtests/strat52_round4_hybrid_player_surface_logs/profile`

Result:

- Total PnL: `+11754.90`.
- Final positions:
  - `VEV_4000`: `-34`
  - `VEV_4500`: `+14`
  - `VEV_5300`: `+45`
  - `VELVETFRUIT_EXTRACT`: `+86`
  - `VEV_5000`: `+108`
  - `VEV_5100`: `+155`
  - `VEV_5200`: `+168`
  - `HYDROGEL_PACK`: `0`

Product result:

- `VEV_5000`: `+5608.54`
- `VEV_5100`: `+4929.45`
- `VEV_5200`: `+2875.57`
- `HYDROGEL_PACK`: `+214.00`
- `VEV_5400`: `0.00`
- `VEV_4000`: `-239.59`
- `VELVETFRUIT_EXTRACT`: `-270.72`
- `VEV_5300`: `-456.29`
- `VEV_4500`: `-906.05`

Counterparty result:

- Mark 38 remained positive on both sides:
  - total SUBMISSION markout10 around `+5.04` when we bought from Mark 38.
  - total SUBMISSION markout10 around `+4.80` when we sold to Mark 38.
- Mark 55 remained positive on VE:
  - VE buys from Mark 55 had positive markout.
  - VE sells to Mark 55 also had positive markout, but sample was smaller than Strat48.
- Mark 67 remained bad to sell VE to:
  - 2 fills, 12 qty, markout50 around `-5.5`.
- Mark 14 remained the main toxic player:
  - large negative markouts on `VEV_4000/4500`.
  - poor VE buy fills around the 30k-40k timestamp cluster.
- Mark 01 remained toxic on core vouchers and `VEV_5300`.

Strat52 conclusions:

- Keep the `VEV_5400` no-new-long filter.
- Do not keep the current `VEV_5300` overlay. It overtrades and does not isolate the intended short edge.
- VE logic needs a stricter no-buy filter against Mark 14 / Mark 01 and a no-sell filter against Mark 67.
- Mark38 deep ITM fade is still good, but broad `VEV_4000/4500` exposure against Mark14 still dominates the product loss.
- Next candidate should be Strat48 plus:
  - `VEV_5400` no-new-long filter;
  - Mark38-only `VEV_4000/4500`;
  - no `VEV_5300` overlay unless it is strictly one-sided and player-filtered;
  - VE only against Mark55/Mark49 and never against Mark67/Mark14/Mark01 toxic clusters.

## High-Confidence Rules

- Product alpha must remain the base; bot alpha is an execution/filter layer.
- Keep `VEV_5000/5100/5200` as the core profitable block.
- Keep HGP hard protection against Mark 14.
- Fade Mark 38, especially HGP and deep ITM vouchers.
- Use Mark 55 as VE liquidity only with counterparty filters.
- Avoid selling VE to Mark 67.
- Treat Mark 22 as contextual, not standalone.
- Do not pay 1 for `VEV_6000/6500`.
- Do not aggressively buy `VEV_5400` from Mark 01/14.
- Do not reuse broad taker probes as production logic.

## Open Questions

- Can a reduced Strat52 variant beat Strat48 if we keep only the parts that worked?
- Can standalone `VEV_5300` short improve total PnL without hurting core inventory?
- Can selective `VEV_5400` selling to Mark 01/14 demand work?
- What is the maximum safe capacity of Mark38 deep ITM fade?
- What is the best stop/exit rule by product and counterparty?
- Is Mark67 enough to justify more than a no-sell VE rule?
- Can post-fill hedging improve drawdown without destroying edge?

## Stop/Exit Mechanisms To Test

- Markout stop: exit if fill markout is worse than threshold after 5/10 ticks.
- Toxic counterparty stop: reduce after Mark 14 / Mark 01 fills with adverse early markout.
- Inventory age stop: flatten stale positions after N ticks if edge disappeared.
- Residual invalidation: close when model residual no longer beats spread plus risk cost.
- Product drawdown stop: product switches to flatten-only after intraday loss threshold.
- Bot cluster stop: block a product/side after consecutive toxic fills.
- Delta/gamma band: reduce option exposure based on aggregate Greeks.
- Liquidity stop: reduce quoting when spread/book conditions become adverse.

## Next Recommended Work

1. Upload and test the real `strat52_round4_hybrid_player_surface.py`.
2. Compare true Strat52 against Strat48 by product, side, counterparty, and markout.
3. If Strat52 improves, split ablations:
   - Strat48 plus no-new-long `VEV_5400`.
   - Strat48 plus standalone `VEV_5300` short.
   - Strat48 plus Mark38-only `VEV_4000/4500`.
4. Add exit logic using counterparty-specific markout thresholds.
5. Keep this journal updated after every new upload/log analysis.
