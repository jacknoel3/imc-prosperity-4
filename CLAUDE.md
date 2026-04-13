# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# IMC Prosperity 4
> Update at the start of every round.

## Competition Context
- **Competition**: IMC Prosperity 4, 16 days, 5 rounds — currency: XIRECs
- **Goal**: Max PnL, top global rank
- **Round 1 starts**: April 14, 2026 12:00 CEST
- **Current phase**: Round 0 / Tutorial

## Commands
| Task | Command |
|------|---------|
| Backtest | `prosperity3bt trader.py 0` (update round number each round) |
| Visualize | https://jmerle.github.io/imc-prosperity-3-visualizer/ |

## Current Round: Round 0

**Submission file: `trader.py`** — `tradertest.py` is Jack's copy (identical as of Round 0, kept for reference)

| Product  | Fair Value | Limit | Strategy |
|----------|-----------|-------|----------|
| EMERALDS | 10,000    | 20    | Fixed FV MM → see @.claude/rules/products/emeralds.md |
| TOMATOES | ~5000 EMA | 35    | Dynamic FV MM → see @.claude/rules/products/tomatoes.md |

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
