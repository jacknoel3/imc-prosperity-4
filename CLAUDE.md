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
- **ASH_COATED_OSMIUM**: Near-fixed fair value around 10,000 with stronger local noise than pepper. Strong lag-1 mean-reversion in `Δmid` (ACF=-0.495). Imbalance is predictive (r≈0.38). Best treated as the more classical stationary market-making product.
- **INTARIAN_PEPPER_ROOT**: Raw price level is not stationary, but the path is highly structured rather than noisy. It rises almost linearly at about +1000/day and about +0.1002 per tick; after removing that linear trend, residual volatility is low. Also shows lag-1 mean-reversion in `Δmid` (ACF=-0.501) and predictive imbalance (r≈0.385, slightly stronger at longer lags). Not fixed-FV, but steady after detrending.
- **Cross-product**: Same-time correlation is near zero (r≈0.016), exhaustive lag sweeps stay economically weak, and conditional links are exploratory at best. Trade independently — no robust pairs/arbitrage strategy.

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
