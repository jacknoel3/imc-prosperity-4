# Claude Code — Working Guide

## Repo Layout

```
imc-prosperity-4/imc-prosperity-4/           ← repo root (all commands run from here)
│
├── trader.py                                 ← SUBMISSION FILE — always root-level
├── datamodel.py                              ← IMC-provided types, never modify
│
├── misc/
│   ├── ALGO_STRUCTURE.md                     ← Round-neutral API/mechanics reference
│   ├── trader_structure.py                   ← Minimal Trader class template
│   └── datamodel.py                          ← Local copy of IMC types for testing
│
├── phase1/
│   ├── round1/algo/data/                     ← Round 1 CSVs (ASH + IPR, archived)
│   ├── round1/algo/analysis/                 ← Round 1 EDA (archived)
│   ├── round2/algo/data/                     ← Round 2 CSVs (ASH + IPR, archived)
│   └── round2/algo/analysis/                 ← Round 2 EDA (archived)
│
├── phase2/
│   └── round3/
│       ├── data/                             ← Round 3 CSVs (prices + trades combined) ← CURRENT
│       ├── algo/analysis/                    ← Round 3 EDA (add scripts here)
│       └── manual/                           ← Manual trading notes
│
├── backtests/                                ← Historical .log files (timestamped)
└── .claude/                                  ← Agent configs, rules, skills
```

> Round 0 (`phase1/round0/`) is archived — EMERALDS + TOMATOES, done.

---

## Agent Structure

```
You
 └── Claude (main conversation)
      └── @orchestrator  ← coordinates, never does raw work
           ├── @researcher   ← data + external research only
           ├── @coder        ← implements + backtests
           └── @reviewer     ← validates before submit
```

- Quick question or small edit → talk to Claude directly
- Data analysis, strategy decisions, or code changes → use agents

---

## Skills (Slash Commands)

| Command | What it does |
|---------|-------------|
| `/backtest` | Runs `prosperity3bt trader.py 2`, reports per-product PnL, flags regressions |
| `/review` | Pre-submission checklist on trader.py — returns PASS or FAIL |
| `/new-round` | Scaffolds round transition: updates CLAUDE.md, creates product rules file |

---

## Current Workflow (Round 3)

### 1. Research Round 3 data
```
@researcher analyze HYDROGEL_PACK, VELVETFRUIT_EXTRACT, and VEV vouchers
from phase2/round3/data/ — establish FV process for spot, fit Black-Scholes IV for vouchers
```
Data: `phase2/round3/data/prices_round_3_combined.csv` and `trades_round_3_combined.csv`

### 2. Strategy decision
```
@orchestrator researcher found [X]. Should we adjust [param]?
```
Returns spec only — no code.

### 3. Implement
```
@coder implement [change] in trader.py at repo root
```
Reads `trader.py`, minimal diff, runs `prosperity3bt trader.py 2`, iterates on regression, returns PnL table.
New file from scratch → start from `misc/trader_structure.py`.

### 4. Review
```
/review
```
Returns PASS or FAIL. On FAIL → `@coder fix line X` → re-review.

### 5. Submit on PASS
Upload `trader.py` (repo root) to Prosperity portal.

---

## Why Agents Stay Separated

Each agent loads only what it needs:

| Step | What loads |
|------|-----------|
| @researcher | CSV data from `phase1/round2/algo/data/` |
| @orchestrator | 15-line findings summary from researcher |
| @coder | `trader.py` at repo root |
| /review | `trader.py` at repo root |

> Never ask one agent to "research + implement + review" — that collapses the separation.

---

## Round 3 Product Summary

| Product | Type | Limit | Strategy |
|---|---|---|---|
| HYDROGEL_PACK | Delta-1 spot | 200 | TBD — run EDA first |
| VELVETFRUIT_EXTRACT | Delta-1 spot (option underlying) | 200 | TBD — run EDA; track vol for BS pricing |
| VEV_4000…VEV_6500 | Call options (10 strikes) | 300 each | Black-Scholes IV fit; TTE=5d at R3 start |

Manual: Celestial Gardeners' Guild — two-bid auction. Bio-Pods auto-sell at 920. Optimal b1 ≈ 675, b2 TBD based on avg competitor bid estimate.

---

## Files — Never Touch

| File | Why |
|------|-----|
| `datamodel.py` (root) | Provided by IMC, overwritten each round |
| `tradertest.py` | No longer in active use |

**Submit: always `trader.py` at repo root**

---

## Backtest
```bash
# From repo root: imc-prosperity-4/imc-prosperity-4/
prosperity3bt trader.py 2

# If not on PATH:
$(find ~ -name prosperity3bt 2>/dev/null | head -1) trader.py 2
```

## Reference
- `misc/ALGO_STRUCTURE.md` — full API reference (TradingState, OrderDepth, Order, position limits, traderData)
- `misc/trader_structure.py` — minimal Trader class skeleton
