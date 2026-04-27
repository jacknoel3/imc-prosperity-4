# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# IMC Prosperity 4
> Update at the start of every round.

## Competition Context
- **Competition**: IMC Prosperity 4, 16 days, 5 rounds — currency: XIRECs
- **Goal**: Max PnL, top global rank
- **Round 3**: April 24–26, 2026 — "Gloves Off" (planet: Solvenar) — archived
- **Round 4 started**: April 27, 2026
- **Current phase**: Round 4 — "The More The Merrier" — Phase 2 / GOAT continues
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

## Current Round: Round 4 — "The More The Merrier"

**Submission file: `trader.py` at repo root**

| Product | Type | Limit | Notes |
|---|---|---|---|
| `HYDROGEL_PACK` | Delta-1 spot | 200 | Same as R3 — FV≈10,000, contrarian OBI |
| `VELVETFRUIT_EXTRACT` | Delta-1 spot | 200 | Same as R3 — FV≈5,250, option underlying |
| `VEV_4000` … `VEV_6500` | Call option (×10 strikes) | 300 each | **TTE=4 days** in R4; recompute BS fair values live |

**Voucher strikes**: 4000, 4500, 5000, 5100, 5200, 5300, 5400, 5500, 6000, 6500
**TTE timeline**: TTE=8d (tutorial) → 7d (R1) → 6d (R2) → 5d (R3) → **4d (R4)** → 3d (R5)

**Key R4 addition — counterparty IDs**: `Trade.buyer` and `Trade.seller` are now populated with participant names. R1–R3 had these as `None`. Use these to identify systematic bots, signal-copy informed traders, and filter out noise flow.

```python
class Trade:
    def __init__(self, symbol, price, quantity, buyer=None, seller=None, timestamp=0):
        self.symbol = symbol
        self.price: int = price
        self.quantity: int = quantity
        self.buyer = buyer      # now populated — participant name or bot ID
        self.seller = seller    # now populated — participant name or bot ID
        self.timestamp = timestamp
```

## Manual Trading — Aether Crystal (Round 4)

See `.claude/rules/products/aether_crystal.md` for full detail. Summary:

- Trade `AETHER_CRYSTAL` (underlying) + vanilla calls/puts (2W, 3W expiry) + exotic derivatives
- **Exotics**: Chooser option (3W expiry, choose call/put after 2W), Binary Put (all-or-nothing), Knock-Out Put (worthless if barrier breached before expiry)
- Underlying modeled as GBM: zero risk-neutral drift, σ=**251% annualized**, 4 discrete steps/day, 252 trading days/year
- PnL averaged over **100 simulations**; contract size=**3000** (scales PnL proportionally)
- "2 weeks" = 10 trading days = 40 steps; "3 weeks" = 15 trading days = 60 steps
- No continuous barrier monitoring — knock-out only checks at discrete grid points
- Hold till expiry — no intraday trading; submit orders once in Manual Challenge Overview window

```python
TRADING_DAYS_PER_YEAR = 252
STEPS_PER_DAY = 4
STEPS_PER_YEAR = TRADING_DAYS_PER_YEAR * STEPS_PER_DAY

def weeks_to_years(weeks: float) -> float:
    return (weeks * 5) / TRADING_DAYS_PER_YEAR

def steps_for_weeks(weeks: float) -> int:
    return int(round(weeks * 5 * STEPS_PER_DAY))
```

## Manual Trading — Celestial Gardeners' Guild (Round 3, archived)
- Submit **two bids** (b1, b2) against counterparties with reserve prices uniform on {670, 675, …, 920} (increment 5)
- All acquired Bio-Pods auto-sell at **920** next trading day
- **Bid 1**: trade at b1 with any counterparty whose reserve ≤ b1
- **Bid 2**: trade at b2 if reserve ≤ b2 AND b2 ≥ mean of all players' b2 — else penalty factor `((920 - avg_b2) / (920 - b2))^3` applied
- Optimal b1: **just above 670** → set b1 = 671 or 675; Optimal b2: anchor near 800–850

## Data Findings Summary (Round 3)
- **HYDROGEL_PACK**: FV≈10,000 (stationary). Spread mean 15.7. ACF lag-1=-0.129 (mean-reverting). OBI is **CONTRARIAN** (r=-0.327) — fade imbalance, do NOT follow it. ~337 trades/day, avg qty 4. Limit=200.
- **VELVETFRUIT_EXTRACT**: FV≈5,250 (stationary, creeps +9 ticks/day — negligible). Spread mean 5.0 (tightest of all). ACF=-0.159. OBI **CONTRARIAN** (r=-0.321). Realized vol=**34.2% annualized** (stable across 3 days). Deep book (~38 units). ~457 trades/day, avg qty 6. Limit=200.
- **VEV_4000/4500** (deep ITM): Delta≈1, price≈VEV−K, zero time value. Skip — no option edge.
- **VEV_5000–5500** (ATM): Market IV **~22%** flat across strikes (NOT ~33–34% — earlier figure was wrong, confused with RV). RV=34.2% is the underlying's realized vol, not what the market prices options at. IV is stable at ~22% across all 3 historical days (TTE=8,7,6). Use **sigma=0.22** in BS for fair value. VEV_5400: spread 1.4, 225 trades, most active ATM strike. No confirmed buy-side misprice — the earlier "−3.8 ticks below BS" was theta decay (TTE=6→5), not an arb signal. Passive MM only. Delta at live TTE=5 ≈ 0.15 per unit (not 0.20).
- **VEV_6000/6500** (deep OTM): Pinned at mid=0.5 (min tick), BS≈0. **SELL at ask=1** — free carry, P(ITM at expiry)≈0.3%. Sell full limit.
- **Cross-product**: HGP and VEV structurally similar but uncorrelated — trade independently. Vouchers linked to VEV via BS pricing.

## Key Rules & References
- See @.claude/rules/submission.md — hard constraints, never break
- See @.claude/rules/round-roadmap.md — future rounds planning
- See @.claude/rules/products/ — per-product strategy details (R3/R4 algo: hydrogel_pack.md, velvetfruit_extract.md, velvetfruit_extract_voucher.md; R4 manual: aether_crystal.md)
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
- Use Black-Scholes without confirming underlying volatility from actual data (R3 sigma=0.22 for voucher quoting, R4 Aether Crystal sigma=2.51)
- Use sigma=0.342 (realized vol) in BS for voucher quoting — that gives prices 15–30 ticks above market
