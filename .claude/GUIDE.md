# Claude Code — Working Guide

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
- Data, strategy, or code changes → use agents

---

## Skills (Slash Commands)

| Command | What it does |
|---------|-------------|
| `/backtest` | Runs backtest, reports per-product PnL, flags regressions |
| `/review` | Pre-submission checklist on trader.py — returns PASS or FAIL |
| `/new-round` | Scaffolds round transition: updates CLAUDE.md, creates product rules file |

---

## 0 → Strategy (the full flow)

### 1. Research first — no code yet
```
@researcher analyze TOMATOES — fair value, spread, autocorrelation, book depth
```
Returns a ~15 line findings block. Read it, decide if the signal is real.

### 2. Strategy decision via orchestrator
```
@orchestrator researcher found negative autocorrelation on TOMATOES lag-1.
Should we switch from EMA to mean-reversion? What parameters?
```
Returns a spec ("use z-score window=20, entry ±1.5σ"). No code yet — just a decision.

### 3. Coder implements from the spec
```
@coder implement mean-reversion on TOMATOES using z-score window=20, entry ±1.5σ.
Keep EMERALDS untouched.
```
Reads `trader.py`, makes minimal change, runs backtest, iterates if regression, returns PnL summary table.

### 4. Review
```
/review
```
Returns PASS or FAIL with line references. If FAIL → `@coder fix line X` → re-review.

### 5. Submit on PASS
Upload `trader.py` to the Prosperity portal.

---

## Why this flow minimizes cost

Each agent carries only what it needs — no step loads everything at once:

| Step | What loads |
|------|-----------|
| @researcher | Researcher agent + CSV data |
| @orchestrator decision | Orchestrator + 15-line findings summary |
| @coder | Coder agent + trader.py |
| /review | Reviewer agent + trader.py |

> Never ask one agent to "research + implement + review" in one prompt. That collapses the separation and burns tokens.

---

## New Round Flow

```
1. /new-round
2. Drop new CSVs into data/round<N>/
3. @researcher analyze [new products]
4. @coder implement strategy for [new products]
5. /review
6. Submit
```

---

## Files — Never Touch

| File | Why |
|------|-----|
| `datamodel.py` | Provided by IMC, overwritten each round |
| `tradertest.py` | Jack's reference copy, read only |

**Submit file: always `trader.py`**
