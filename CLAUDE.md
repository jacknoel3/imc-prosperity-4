# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# IMC Prosperity 4
> Update at the start of every round.

## Competition Context
- **Competition**: IMC Prosperity 4, 16 days, 5 rounds — currency: XIRECs
- **Goal**: Max PnL, top global rank
- **Round 1 started**: April 14, 2026 12:00 CEST
- **Current phase**: Round 1 — "Trading Groundwork" (planet: Intara)
- **Round objective**: 200,000 XIRECs net profit before beginning of day 3

## Commands
| Task | Command |
|------|---------|
| Backtest | `prosperity3bt trader.py 1` |
| Visualize | https://jmerle.github.io/imc-prosperity-3-visualizer/ |

## Current Round: Round 1

**Submission file: `trader.py`** — `tradertest.py` is Jack's copy (kept for reference)

| Product | Fair Value | Limit | Strategy |
|---|---|---|---|
| ASH_COATED_OSMIUM | 10,000 (fixed, stationary) | 80 | Fixed FV MM + imbalance tilt → see @.claude/rules/products/ash_coated_osmium.md |
| INTARIAN_PEPPER_ROOT | EMA (alpha=0.05), +1000/day trend | 80 | Dynamic FV MM + trend bias → see @.claude/rules/products/intarian_pepper_root.md |

## Data Findings Summary (Round 1)
- **ASH_COATED_OSMIUM**: Fixed FV=10000. Strong lag-1 mean-reversion (ACF=-0.495). Imbalance predictive (r=0.38). Behaves like EMERALDS.
- **INTARIAN_PEPPER_ROOT**: Trends +1000/day linearly. Lag-1 mean-reversion (ACF=-0.501). Imbalance predictive (r=0.385, strengthens at longer lags). NOT a fixed FV product.
- **Cross-product**: Zero correlation (r=0.016), no lead-lag, z-spread non-stationary. Trade independently — no pairs strategy.

## Manual Challenge: "An Intarian Welcome"
- Submit a single limit order (price + qty) for each product — you go last, no changes after
- Clearing price = maximizes volume, ties → higher price
- Buyback after auction (no continuous trading):
  - `DRYLAND_FLAX`: 30/unit (no fees) → bid below 30, profit = (30 - fill_price) × qty
  - `EMBER_MUSHROOM`: 20/unit (fee 0.10/unit) → effective buyback = 19.90 → bid below 19.90

## Key Rules
- See @.claude/rules/submission.md — hard constraints, never break
- See @.claude/rules/round-roadmap.md — future rounds planning
- See @.claude/rules/products/ — per-product strategy details

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
