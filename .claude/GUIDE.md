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
│   ├── round1/algo/data/                     ← Round 1 CSVs (days -2, -1, 0) ✅
│   ├── round1/algo/analysis/                 ← Round 1 EDA scripts + eda_output/
│   ├── round1/algo/strategy/                 ← Round 1 historical trader variants
│   │
│   ├── round2/algo/data/                     ← Round 2 CSVs (days -1, 0, 1) ← CURRENT DATA
│   ├── round2/algo/analysis/                 ← Round 2 EDA (add scripts here)
│   └── round2/algo/strategy/                 ← Round 2 trader variants (add here)
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

## Current Workflow (Round 2)

### 1. Research Round 2 data
```
@researcher analyze ASH_COATED_OSMIUM and INTARIAN_PEPPER_ROOT
from phase1/round2/algo/data/ — check if signals changed vs Round 1
```
Data: `phase1/round2/algo/data/prices_round_2_day_<D>.csv` and `trades_round_2_day_<D>.csv`
Days available: -1, 0, 1

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

## Round 2 Product Summary

| Product | FV | Limit | Key Signals | Strategy |
|---|---|---|---|---|
| ASH_COATED_OSMIUM | 10,000 (fixed) | 80 | ACF=-0.495, OBI directional r=+0.38 | Fixed FV MM + imbalance tilt |
| INTARIAN_PEPPER_ROOT | Holt's (α=0.20, β=0.10) | 80 | +1000/day ramp, OBI contrarian β=-0.65, Z-momentum r=+0.46 | Passive MM only — no aggressive takes |

MAF: `bid()` in `class Trader` — top 50% of bidders get 25% extra quotes. Bid subtracted from R2 profits if accepted.

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
