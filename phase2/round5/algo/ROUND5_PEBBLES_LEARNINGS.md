# Round 5 Pebbles Learnings

Last updated: 2026-04-29

## Current Candidate

Use `round5_pebbles_anchor_guarded_mr.py`.

The strategy is not using hardcoded timestamps. It identifies regimes from rolling market state:
- pair residual z-scores for `PEBBLES_XS + PEBBLES_S` and `PEBBLES_L + PEBBLES_XL`
- a full five-Pebbles mid-sum guard around `50,000`
- a two-horizon dominant-trend guard that blocks one-leg-dominated entries

It actively trades `PEBBLES_XS`, `PEBBLES_S`, `PEBBLES_L`, and `PEBBLES_XL`. It tracks `PEBBLES_M` only inside the five-product basket sum. We are not actively trading `PEBBLES_M` yet because the M-inclusive active prototypes looked strong in residual research but fragile in execution tests.

## Why This Version

The original unguarded pair strategy made good money early but accepted a late `L/XL` short into a one-leg-dominated XL rally. In uploaded-log replay, that ended around `-580` and left `PEBBLES_L = -10`, `PEBBLES_XL = -10`.

The first guarded version was safer but too conservative. It blocked the bad late short, but also missed a good early `L/XL` long. That replay ended around `+410`.

The relaxed guard captured the good early hidden-log `L/XL` trade and still blocked the late dominated short. On uploaded logs `550929`, `558717`, and `559350`, it replayed at about `+3,530`, flat, with 12 fills.

The latest tweak combines the strict and relaxed guards:
- strict view: `lookback=100`, `ticks=250`, `share=0.55`
- relaxed view: `lookback=75`, `ticks=275`, `share=0.65`

An entry is blocked only if both views agree it is fighting a one-leg trend. This keeps the hidden-log result at about `+3,530` while improving the public sample replay.

## Latest Replay Snapshot

Local active-fill replay, after the dual-horizon guard:

| Strategy view | Uploaded 559350 | Public day 2 | Public day 3 | Public day 4 | Public total |
|---|---:|---:|---:|---:|---:|
| strict guard only | 410 | 40,670 | 28,654 | 35,760 | 105,084 |
| relaxed guard only | 3,530 | 33,790 | 26,824 | 29,880 | 90,494 |
| dual guard | 3,530 | 41,680 | 30,494 | 29,920 | 102,094 |
| no guard | -580 | 29,650 | 36,994 | 24,690 | 91,334 |

The official uploaded `559350` graph for the prior relaxed version showed final profit `3,530`, peak near `3,638`, and trough near `-2,131`. The local replay marks the same trade sequence slightly differently, but the final and flat inventory match.

## 50,000 Basket Insight

The five Pebbles mid prices are anchored very tightly around `50,000`. This is real and useful as a regime check.

Strict executable basket arbitrage is too thin to be the whole strategy. The pure basket script buys when combined asks are below `50,000`, but public sample data rarely gives a clean combined-bid sell above `50,000`. The uploaded `558717` style run was basically flat-small, not a real alpha engine.

The current best use of the 50k discovery is as a guard:
- allow pair longs when the full basket is not meaningfully rich
- allow pair shorts when the full basket is not meaningfully cheap
- avoid treating the basket edge alone as enough to justify size

## What Worked

`XS/S + L/XL` same-direction pair mean reversion is the most reliable active setup tested so far.

The `L/XL` pair carries most of the upside. `PEBBLES_XL` is much more volatile than the other Pebbles, so the late failure mode is usually XL dominating the pair move.

The two-horizon guard is the best tradeoff so far because it combines two things we learned separately:
- the strict guard likes the public sample better
- the relaxed guard is needed to catch the hidden-log early `L/XL` long

## What Failed Or Is Not Ready

Time/session controls such as "no new trades after X" can look good in sweeps, but they are hardcoded regime guesses. We should avoid them unless we are intentionally overfitting to a known contest clock behavior.

Residual/profit locks and trailing exits did not solve the late `L/XL` problem. They reduced public sample PnL and still failed to reliably flatten the dangerous short.

The all-five `XL_vs_avg_XS_S_M_L` residual is statistically strong in research:
- summary research found `corr=-0.6982` at horizon 20
- `ok_rate=1.00`
- the recommendation was passive entry near extremes, active only after reversal confirmation

But the active all-five prototype lost on hidden/uploaded replay and on public day 2. Keep this as a passive or confirmation idea, not the current main strategy.

`PEBBLES_M` is not ignored because it is unimportant. It is not actively traded because the current tested active edge is better in the two pair sums, while M is most useful as part of the 50k anchor.

## Next Experiments

Test the dual-horizon guard in the official backtester and compare the chart to `559350`.

Try a passive-first `XL_vs_avg_XS_S_M_L` prototype with small size and reversal confirmation. Do not active-cross the whole five-product residual yet.

Check whether `XS/S` should be smaller or more selective. It contributes in some samples, but it also creates the early drawdown in hidden logs and loses on public day 4.

Keep the dual guard dynamic. If a new uploaded run fails late again, diagnose whether both guard horizons failed or whether the exit logic held a stale pair too long.
