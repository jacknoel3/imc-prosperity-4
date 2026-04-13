# IMC Prosperity 4 — CLAUDE.md
> Update this file at the start of every round. This is the single source of truth.

---

## Competition Context
- **Competition**: IMC Prosperity 4, 16 days, 5 rounds
- **Currency**: XIRECs
- **Goal**: Max PnL, top global rank
- **Round 1 starts**: April 14, 2026 12:00 CEST
- **Current phase**: Round 0 / Tutorial (tomatoes + emeralds only)

---

## Submission Rules (NEVER BREAK THESE)
- Single Python file, must implement `class Trader` with `def run(self, state: TradingState)`
- Returns: `tuple[dict[str, list[Order]], int, str]`
- No external imports beyond stdlib + provided `datamodel`
- **Minimize `print()` calls** — verbose logging causes AWS Lambda timeouts (banned teams in P3)
- Last successfully processed upload before deadline is locked in

---

## Project Structure
```
trader.py           # main submission — the only file that matters
backtester/         # jmerle's prosperity3bt (pip install prosperity3bt)
data/
  prices_round_0_day_-1.csv
  prices_round_0_day_-2.csv
  trades_round_0_day_-1.csv
  trades_round_0_day_-2.csv
analysis/           # notebooks / scripts
CLAUDE.md           # this file
```

**Backtest command**: `prosperity3bt trader.py 0`
**Visualizer**: https://jmerle.github.io/imc-prosperity-3-visualizer/

---

## Current Round: Round 0 (Tutorial)

### Products
| Product   | Fair Value | Behavior         | Position Limit | Strategy          |
|-----------|-----------|-----------------|----------------|-------------------|
| EMERALDS  | 10,000    | Fixed fair value | TBD            | Pure market make  |
| TOMATOES  | ~5000     | Random walk      | TBD            | Mean-rev / MM     |

### Data Findings (from analysis of days -1 and -2)
**EMERALDS** (= P3's RAINFOREST_RESIN analog):
- Mid price: exactly 10,000.00 always (confirmed)
- Book: bid at 9992 (~vol 12), ask at 10008 (~vol 12) — spread = 16
- Occasional: bid/ask touching 10000 (narrower spread = 8)
- Tick volatility: ~0.25 (extremely stable)
- **Strategy: hardcode fair_value=10000, post bids at 9993-9999, asks at 10001-10007**
- Inside spread is +8 per unit vs bot, aggressively take any ask ≤ 9999 or bid ≥ 10001

**TOMATOES** (= P3's KELP analog):
- Mid price: drifts, range ~4946–5036 across days
- Spread: mean 13, min 5 — more variable
- Tick volatility: ~0.79 (3x more volatile than EMERALDS)
- Moves: ~equal pos/neg/zero — no clear autocorrelation
- **Strategy: dynamic fair value via VWAP of best bid/ask; market make with inventory skew**

---

## Strategy Patterns by Product Type

### Fixed Fair Value (EMERALDS)
```python
# Hardcode FV, post tight inside the spread
# Take aggressively when bot shows price inside FV ± 1
fair_value = 10000
# Buy everything at ask < fair_value, sell everything at bid > fair_value
# Post limit orders: bid at fv-1, ask at fv+1 (or fv-2/fv+2 with more volume)
```

### Mean-Reverting / Random Walk (TOMATOES)
```python
# VWAP-based dynamic fair value
# Skew quotes based on inventory position
# Don't go directional — inventory management is the alpha
# Reduce size when position is large, widen spread
```

### Position Management (applies to all)
```python
# Never let position sit at limit — it means you're wrong
# Skew: if long, lower bid price, keep ask aggressive
# If position > 70% of limit: stop adding, only reduce
```

---

## Round Roadmap (based on P3, expect similar)

| Round | New Products | Key Strategy | Watch Out |
|-------|-------------|-------------|-----------|
| 0 | EMERALDS, TOMATOES | Pure MM | Learn the environment |
| 1 | +1-2 products | Fixed FV + mean-rev + volatile product | Don't trade the noisy one too aggressively |
| 2 | ETF basket | Stat arb: basket vs synthetic, z-score spread | Basket = linear combo of constituents |
| 3 | Options (vouchers) | Black-Scholes implied vol, dynamic hedge | Unhedged long vega was top strategy in P3 |
| 4 | Cross-exchange arb | Two-way arb + accumulation regime | Read fee structure twice |
| 5 | Insider IDs revealed | Copy insider trades, rank IDs by forward PnL | Keep MM running on other products |

---

## What Kills Good Teams (from top writeups)
- ❌ Hardcoding to historical data without fallback (teams were banned in P3 for this)
- ❌ Overfitting backtest params — live bots ≠ backtest
- ❌ Trading the noisy volatile product too aggressively in Round 1
- ❌ `print()` spam → AWS Lambda timeout → submission fails
- ❌ Not having backtester ready before Round 1 drops

---

## Key External Resources
- **Backtester**: `pip install prosperity3bt` (jmerle) — wait for prosperity4bt release
- **Visualizer**: https://jmerle.github.io/imc-prosperity-3-visualizer/
- **Top P3 writeup (2nd place)**: https://github.com/TimoDiehm/imc-prosperity-3
- **9th place (Alpha Animals)**: https://github.com/CarterT27/imc-prosperity-3
- **Discord**: Join Prosperity Discord for mid-round signals

---

## Session Workflow
1. `/compact` at 40-50% context, `/clear` for new topic
2. Batch related tasks: e.g. "analyze product + write strategy + backtest" = 1 session
3. Update this CLAUDE.md after each round with new products, findings, position limits
4. Skills files: see `analysis/skills/` for reusable strategy templates (create as needed)

---

## DO NOT
- Use MCPs (token waste for this project)
- Use `AskUserQuestionsTool` (prefer autonomous execution with context from this file)
- Submit with verbose logging
- Hardcode prices from historical data without a runtime fallback
