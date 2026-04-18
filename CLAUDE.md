# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# IMC Prosperity 4
> Update at the start of every round.

## Competition Context
- **Competition**: IMC Prosperity 4, 16 days, 5 rounds — currency: XIRECs
- **Goal**: Max PnL, top global rank
- **Round 2 started**: April 18, 2026
- **Current phase**: Round 2 — "Growing Your Outpost" (planet: Intara)
- **Round objective**: 200,000 XIRECs net profit total (Rounds 1+2 combined) — qualifier threshold for Phase 2

## Repo Layout

```
imc-prosperity-4/imc-prosperity-4/        ← repo root (run all commands from here)
├── trader.py                              ← SUBMISSION FILE — always root-level
├── tradertest.py                          ← Jack's reference copy, read-only
├── datamodel.py                           ← IMC-provided types, never modify
│
├── misc/
│   ├── ALGO_STRUCTURE.md                  ← Round-neutral API/mechanics reference
│   ├── trader_structure.py                ← Minimal Trader class template
│   └── datamodel.py                       ← Copy of IMC types for local testing
│
├── phase1/
│   ├── round0/                            ← EMERALDS + TOMATOES (archived, Round 0)
│   ├── round1/algo/data/                  ← ASH + IPR Round 1 CSVs (days -2, -1, 0)
│   ├── round1/algo/analysis/              ← Round 1 EDA scripts and outputs
│   ├── round2/algo/data/                  ← Round 2 CSVs (days -1, 0, 1) ← CURRENT
│   └── round2/algo/analysis/              ← Round 2 EDA (add scripts here)
│
├── phase2/                                ← Rounds 3–5 scaffolding (empty until data drops)
└── backtests/                             ← Historical .log files
```

## Commands
| Task | Command |
|------|---------|
| Backtest | `prosperity3bt trader.py 2` (run from repo root) |
| Visualize | https://jmerle.github.io/imc-prosperity-3-visualizer/ |

## Current Round: Round 2

**Submission file: `trader.py` at repo root** — `tradertest.py` is no longer in active use

| Product | Fair Value | Limit | Strategy |
|---|---|---|---|
| ASH_COATED_OSMIUM | 10,000 (fixed, stationary) | 80 | Fixed FV MM + imbalance tilt → see @.claude/rules/products/ash_coated_osmium.md |
| INTARIAN_PEPPER_ROOT | Holt's linear smoothing, +1000/day trend | 80 | Dynamic FV MM + trend bias → see @.claude/rules/products/intarian_pepper_root.md |

## Market Access Fee (MAF) — Round 2 Only
- `bid()` method in `class Trader` sets your MAF bid (XIRECs)
- Top 50% of bids across all participants gain **25% extra market flow** (more quotes to trade against)
- Accepted bids are **subtracted from Round 2 profits** — bid only what the extra flow is worth
- MAF is a one-time, blind auction; median of all bids is the cutoff
- During backtesting, MAF is ignored — only applied in the final Round 2 simulation
- Backtest runs with 80% of all generated quotes (slightly randomized per submission)
- Strategy: bid enough to be top 50%, but not excessively — game-theory optimum is just above median
- **Default bid**: `return 0` will NOT get extra access; update to a reasoned value before submission

## Investment Budget — Round 2 Only
- 50,000 XIRECs to allocate across **three growth pillars** (details TBD from round data)
- Allocation is separate from trading algorithm — manual decision, not in trader.py

## Data Findings Summary (Rounds 1 & 2)
- **ASH_COATED_OSMIUM**: Near-fixed fair value around 10,000. Strong lag-1 mean-reversion (ACF=-0.495). OBI is **directional** (r≈0.38, follow imbalance direction). Classical stationary MM product.
- **INTARIAN_PEPPER_ROOT**: Trends +1000/day linearly, consistent across all days (<3σ). Lag-1 ACF=-0.501. Three independent signals: (1) **OBI is CONTRARIAN** (beta=-0.55 to -0.78, p≈0) — high bid volume predicts price DOWN; (2) **Micro-price Z-score is momentum** (corr≈+0.46, p≈0) — use for quote suppression; (3) **Buy trades are informed** (+2.6 ticks fwd_10, t≈12), sell trades are noise. NEVER use market orders (13-tick spread, signal≈1.5 ticks → guaranteed loss). MM earns 11–12× buy-and-hold.
- **Cross-product**: No robust pairs/arbitrage. Trade independently.

## Key Rules & References
- See @.claude/rules/submission.md — hard constraints, never break
- See @.claude/rules/round-roadmap.md — future rounds planning
- See @.claude/rules/products/ — per-product strategy details
- See `misc/ALGO_STRUCTURE.md` — exchange mechanics, TradingState API, position limit rules
- See `misc/trader_structure.py` — minimal Trader class template (starting point for new files)

## Agents
| Agent | Use when |
|-------|----------|
| orchestrator | Multi-step tasks, round transitions, strategy decisions |
| researcher | Analyzing CSVs, external writeups, data questions |
| reviewer | Pre-submission checks, bug hunting |
| coder | Implementing strategies, running backtests |

## Skills
- `/backtest` — run backtest and interpret per-product PnL
- `/review` — pre-submission checklist
- `/new-round` — scaffold round transition

## External References
- Backtester: `pip install prosperity3bt`
- 2nd place P3: https://github.com/TimoDiehm/imc-prosperity-3
- 9th place P3: https://github.com/CarterT27/imc-prosperity-3

## DO NOT
- Use MCPs
- Verbose print() in submitted code
- Hardcode historical prices without runtime fallback
- Modify datamodel.py
- Submit with `bid()` returning 0 — set a reasoned MAF value before final submission
