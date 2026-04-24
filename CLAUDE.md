# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# IMC Prosperity 4
> Update at the start of every round.

## Competition Context
- **Competition**: IMC Prosperity 4, 16 days, 5 rounds — currency: XIRECs
- **Goal**: Max PnL, top global rank
- **Round 3 started**: April 24, 2026
- **Current phase**: Round 3 — "Gloves Off" (planet: Solvenar) — Phase 2 / GOAT begins
- **Leaderboard reset**: all teams start Round 3 at 0 PnL — Phase 1 results archived
- **Round duration**: 48 hours (Rounds 3–5 are shorter than R1/R2)

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
│   ├── round0/                            ← EMERALDS + TOMATOES (archived)
│   ├── round1/algo/data/                  ← ASH + IPR Round 1 CSVs (days -2, -1, 0)
│   ├── round1/algo/analysis/              ← Round 1 EDA
│   ├── round2/algo/data/                  ← ASH + IPR Round 2 CSVs (archived)
│   └── round2/algo/analysis/              ← Round 2 EDA (archived)
│
├── phase2/
│   └── round3/
│       ├── data/                          ← Round 3 CSVs (prices + trades combined) ← CURRENT
│       ├── algo/analysis/                 ← Round 3 EDA (add scripts here)
│       └── manual/                        ← Manual trading notes
└── backtests/                             ← Historical .log files
```

## Commands
| Task | Command |
|------|---------|
| Backtest | `prosperity3bt trader.py 2` (run from repo root) |
| Visualize | https://jmerle.github.io/imc-prosperity-3-visualizer/ |

## Algo Workflow

Use this workflow every round so research, implementation, and validation stay aligned across the team.

### 1. Update Shared Context First
- Use the IMC wiki and platform information to refresh this file at the start of each round.
- Keep the existing structure intact when updating round context, products, mechanics, limits, and workflow assumptions.
- Confirm exchange mechanics before strategy work:
  - matching and fill behavior
  - cancellation behavior
  - position-limit rejection behavior
  - conversion accounting
  - PnL marking methodology

### 2. Run EDA and Build Product Hypotheses
- Create a hypothesis sheet for each product covering:
  - fair value process
  - book shape
  - spread behavior
  - trade arrival
  - bot patterns
  - whether the edge is maker-driven or taker-driven
- For each product, answer:
  - Is fair value fixed, drifting, mean reverting, regime switching, basket-linked, option-linked, or flow-linked?
  - Are bots only interacting at top-of-book?
  - Where do trades print relative to mid and estimated fair value?
  - Are there stable counterparty or trader-ID patterns?
  - Should we be makers, takers, or hybrid?
  - Are products correlated enough for stat-arb or relative-value trades?

### 3. Start From a Baseline by Product Archetype
- Round 1 style: fair-value market making
- Round 2 style: basket versus synthetic spread trading
- Round 3 style: option mispricing or IV relative value
- Round 4 style: conversion-cost arbitrage or regime switching
- Round 5 style: trader-ID signal copying layered on top of the existing stack
- First implementation goal: one clean, explainable baseline per product archetype.

### 4. Backtest and Visualize Immediately
- Use the backtester fork as soon as a baseline exists.
- Overlay fills on the dashboard instead of trusting only aggregate PnL.
- Diagnose execution quality and inventory path, not just final profit.

### 5. Diagnose Fills, Not Just Alpha
- For every early strategy, explicitly answer:
  - Are we making money from true edge or just favorable drift?
  - Are we over-quoting and getting picked off?
  - Are fills concentrated in bad regimes?
  - Are we leaking PnL when inventory gets stuck?
  - Is one product carrying or killing the round?

### 6. Harden Before Optimizing
- Add robustness before adding complex logic:
  - position caps
  - side-capacity checks
  - fair-value fallback logic
  - product toggles
  - low-log submission mode
  - graceful handling for missing observations or changed products

### 7. Submission Standard
- The first submission should be boring and robust.
- Prefer:
  - one clean baseline per product archetype
  - no heavy logging
  - no fragile hardcoding
  - no overfit thresholds
  - no product included unless we can clearly explain why it should make money

## Current Round: Round 3

**Submission file: `trader.py` at repo root**

| Product | Type | Limit | Notes |
|---|---|---|---|
| HYDROGEL_PACK | Delta-1 spot | 200 | Standard MM product — run EDA to confirm FV process |
| VELVETFRUIT_EXTRACT | Delta-1 spot | 200 | Underlying for vouchers — price drives option value |
| VEV_4000 … VEV_6500 | Call option (×10 strikes) | 300 each | Black-Scholes pricing; TTE=5 days at R3 start |

**Voucher strikes**: 4000, 4500, 5000, 5100, 5200, 5300, 5400, 5500, 6000, 6500
**TTE timeline**: TTE=8d (tutorial) → 7d (R1) → 6d (R2) → **5d (R3)** → 4d (R4) → 3d (R5)

## Manual Trading — Celestial Gardeners' Guild (Round 3)
- Submit **two bids** (b1, b2) against counterparties with reserve prices uniform on {670, 675, …, 920} (increment 5)
- All acquired Bio-Pods auto-sell at **920** next trading day
- **Bid 1**: trade at b1 with any counterparty whose reserve ≤ b1
- **Bid 2**: trade at b2 if reserve ≤ b2 AND b2 ≥ mean of all players' b2 — else penalty factor `((920 - avg_b2) / (920 - b2))^3` applied
- Optimal b1: **just above 670** (capture all counterparties, minimize cost) → set b1 = 671 or 675
- Optimal b2: game-theory — bid just above expected avg_b2; without data on other teams, anchor near 800–850
- Submit via Manual Challenge Overview window; last submission before deadline is locked

## Data Findings Summary (Round 3)
- **HYDROGEL_PACK**: FV≈10,000 (stationary). Spread mean 15.7. ACF lag-1=-0.129 (mean-reverting). OBI is **CONTRARIAN** (r=-0.327) — fade imbalance, do NOT follow it. ~337 trades/day, avg qty 4. Limit=200.
- **VELVETFRUIT_EXTRACT**: FV≈5,250 (stationary, creeps +9 ticks/day — negligible). Spread mean 5.0 (tightest of all). ACF=-0.159. OBI **CONTRARIAN** (r=-0.321). Realized vol=**34.2% annualized** (stable across 3 days). Deep book (~38 units). ~457 trades/day, avg qty 6. Limit=200.
- **VEV_4000/4500** (deep ITM): Delta≈1, price≈VEV−K, zero time value. Skip — no option edge.
- **VEV_5000–5500** (ATM): Flat IV surface ~33–34%, slightly below realized vol (34.2%). VEV_5400 most mispriced: avg −3.8 ticks below BS, spread 1.4, 225 trades — **best single trade**. Delta hedge: short 0.2 VEV per voucher long.
- **VEV_6000/6500** (deep OTM): Pinned at mid=0.5 (min tick), BS≈0. **SELL at ask=1** — free carry, P(ITM at expiry)≈0.3%. Sell full limit.
- **Cross-product**: HGP and VEV structurally similar but uncorrelated — trade independently. Vouchers linked to VEV via BS pricing.

## Key Rules & References
- See @.claude/rules/submission.md — hard constraints, never break
- See @.claude/rules/round-roadmap.md — future rounds planning
- See @.claude/rules/products/ — per-product strategy details (R3: hydrogel_pack.md, velvetfruit_extract.md, velvetfruit_extract_voucher.md)
- See @.claude/rules/agents-gather-protocol.md — trigger phrase `agents gather <idea>` launches every agent in `.claude/agents/` in parallel and appends the reasoning record to `shared_reasoning.md` (auto-scales; new agents join automatically)
- See @.claude/rules/quant-council-guide.md — full explanation of the agents gather protocol, roster, phases, and challenger review
- See `misc/ALGO_STRUCTURE.md` — exchange mechanics, TradingState API, position limit rules
- See `misc/trader_structure.py` — minimal Trader class template (starting point for new files)

## Agents
| Agent | Use when |
|-------|----------|
| orchestrator | Multi-step tasks, round transitions, strategy decisions |
| researcher | Analyzing CSVs, external writeups, data questions |
| reviewer | Pre-submission checks, bug hunting |
| coder | Implementing strategies, running backtests |
| quant-alpha/beta/gamma/delta/epsilon | Core debaters — participate in `agents gather` automatically |
| quant-devils-advocate | Challenger — auto-runs at the end of every `agents gather`; invoke directly when a recommendation feels too rosy |
| quant-optimist | Challenger — auto-runs at the end of every `agents gather`; invoke directly when a recommendation feels too cautious |

**Trigger phrase**: `agents gather <your idea>` → main Claude launches every `.claude/agents/*.md` in parallel, appends reasoning to `shared_reasoning.md`, returns a balanced view. Auto-scales: new agent files auto-join. See @.claude/rules/agents-gather-protocol.md.

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
- Use market orders on wide-spread products (same rule as IPR — crossing spread destroys edge)
- Use Black-Scholes without confirming underlying volatility from actual R3 data
