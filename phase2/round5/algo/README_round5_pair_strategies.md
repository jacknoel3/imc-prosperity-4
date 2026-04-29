# Round 5 pair residual mean-reversion strategy files

These files are generated from the uploaded/sample Round 5 data:
- prices_round_5_day_2.csv
- prices_round_5_day_3.csv
- prices_round_5_day_4.csv

The PnL numbers discussed are offline sample-data backtests, not website/live results.

## Files

Combined strategies:
- round5_core_pair_mr_size10.py
- round5_core_pair_mr_size20.py
- round5_core_pair_mr_size30.py
- round5_core_plus_pebbles_pair_mr_size10.py

Single-pair diagnostic strategies:
- round5_pebbles_anchor_guarded_mr.py
- round5_pebbles_basket_arb.py
- round5_pebbles_l_xl_sum_mr.py
- round5_microchip_circle_rectangle_diff_mr.py
- round5_panel_1x2_2x4_diff_mr.py
- round5_robot_laundry_mopping_sum_mr.py
- round5_pebbles_xl_xs_sum_mr.py

Backtester:
- round5_pair_backtester.py

## Clean sample backtest results from this implementation

Core four pairs only:

| Pair size | Day 2 | Day 3 | Day 4 | Total |
|---:|---:|---:|---:|---:|
| 10 | 55,060 | 57,169 | 60,934 | 173,163 |
| 20 | 107,486 | 112,040 | 118,643 | 338,169 |
| 30 | 159,379 | 166,707 | 173,472 | 499,558 |

The earlier numbers I gave were from the same uploaded sample-data idea, but the cleaned script here gives slightly different values because it uses stricter active-only execution and explicit unfilled-depth accounting.

## Dashboard export commands

Each standalone strategy can write dashboard overlay CSVs into:

```text
phase2/round5/algo/dashboard/examples
```

Examples:

```bash
py phase2/round5/algo/round5_pebbles_l_xl_sum_mr.py --all-days
py phase2/round5/algo/round5_pebbles_anchor_guarded_mr.py --all-days
py phase2/round5/algo/round5_pebbles_basket_arb.py --all-days
py phase2/round5/algo/round5_pebbles_xl_xs_sum_mr.py --all-days
py phase2/round5/algo/round5_robot_laundry_mopping_sum_mr.py --all-days
```

Without `--all-days`, a strategy writes only the default day 2 file.

`round5_pebbles_basket_arb.py` buys the five-pebbles basket when executable
asks are below 50,000 and recycles long inventory when executable bids exceed
the tracked basket entry cost. In the public sample, `sum(best bids) > 50,000`
does not occur, so a strict 50,000 sell trigger only buys early and then sits at
the long limit.

`round5_pebbles_anchor_guarded_mr.py` is the safer strategy candidate from the
uploaded 550929/558717/559103/559350 logs. It keeps the XS/S and L/XL pair
mean-reversion logic, blocks one-leg-dominated trend entries, and uses the full
five-pebbles mid sum near 50,000 as a guard before opening new pair exposure.
The dominant-trend guard is dynamic, not timestamp-based. The current version
uses two trend horizons and only blocks an entry when both agree that the pair
move is one-leg dominated.

On the uploaded 559103 replay, the previous guard finished around +410 after
trading only XS/S and sitting flat after timestamp 73,800. The retuned guard
also trades the early L/XL setup, finishes around +3,530, and ends flat. Its
worst local active-fill replay point on that uploaded window was around -2,130.

Local active-fill replay for `round5_pebbles_anchor_guarded_mr.py` on the
public sample finished flat with approximate PnL:
- day 2: 41,680
- day 3: 30,494
- day 4: 29,920

More notes and postmortems are in `ROUND5_PEBBLES_LEARNINGS.md`.
