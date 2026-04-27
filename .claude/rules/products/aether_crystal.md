## AETHER_CRYSTAL — Manual Trading (Round 4)

Round 4 manual challenge: "Vanilla Just Isn't Exotic Enough". Trade `AETHER_CRYSTAL` and a suite of derivatives written on it. This challenge is **completely standalone** — no relationship to the algorithmic products or Round 1.

---

### Simulation Model

- Underlying: `AETHER_CRYSTAL` — Geometric Brownian Motion (GBM)
- Risk-neutral drift: **0** (zero)
- Annualized volatility: **251%** (σ = 2.51)
- Discrete time grid: **4 steps per trading day**, 252 trading days per year
- No continuous barrier monitoring — knock-out conditions checked at discrete grid points only

```python
TRADING_DAYS_PER_YEAR = 252
STEPS_PER_DAY = 4
STEPS_PER_YEAR = TRADING_DAYS_PER_YEAR * STEPS_PER_DAY

def weeks_to_years(weeks: float) -> float:
    # 5 business days per week, annualized to 252 trading days
    return (weeks * 5) / TRADING_DAYS_PER_YEAR

def steps_for_weeks(weeks: float) -> int:
    return int(round(weeks * 5 * STEPS_PER_DAY))
```

- "2 weeks" = `2 * 5 * STEPS_PER_DAY` = **40 steps** = 10 trading days
- "3 weeks" = `3 * 5 * STEPS_PER_DAY` = **60 steps** = 15 trading days

---

### PnL Mechanics

- PnL is the **average across 100 simulations** of the underlying
- PnL is marked to 'fair value' upon expiry
- **Contract size = 3000** — acts as a PnL multiplier on the individual product PnL (scales proportionally to Rounds 3 and 5)
- The "price" column in the UI is cosmetic (investment cost display) — ignore it for trading decisions
- Buy or sell up to the displayed volume in each product
- No intraday trading — submit once at t=0 (start of Round 4), hold till expiry
- Unhedged exposure can lead to large losses — risk management matters

---

### Available Products

#### Underlying
- `AETHER_CRYSTAL` — the spot underlying

#### Vanilla Options
- **2-week call / put** — standard European call/put, expiry in 2 weeks (10 trading days, 40 steps)
- **3-week call / put** — standard European call/put, expiry in 3 weeks (15 trading days, 60 steps)

#### Exotic Derivatives

##### Chooser Option
- Expiry: **3 weeks** (60 steps)
- After **2 weeks** (40 steps), the buyer chooses whether it becomes a call or put — selects whichever is in the money at that time
- Behaves like a standard option for the final week (20 steps) until expiry
- Fair value: at the choice date, value = max(call_value_remaining, put_value_remaining)

##### Binary Put Option
- All-or-nothing payoff
- If the underlying is **below the strike** at expiry → pays the specified fixed amount
- Otherwise → expires worthless
- Fair value: `fixed_payout * N(-d2)` under GBM with zero drift

##### Knock-Out Put Option
- Behaves like a regular put unless the underlying ever trades **below the knockout barrier** before expiry
- If the barrier is breached at any discrete grid point → option immediately becomes **worthless**
- Only discrete-time monitoring — no path-continuous knock-out
- Fair value: down-and-out put price via simulation or analytical formula under GBM

---

### Strategy Considerations

- σ=251% is extremely high — option time value decays quickly and large moves are frequent
- Zero drift means put and call have symmetric expected payoffs on the underlying
- With σ=2.51, even short-dated options have significant vega; gamma dominates near expiry
- Chooser option: at the choice date, it's in the money by definition (holder picks best side) — value ≥ max(call, put) at 2W, making it more expensive than a single vanilla
- Binary put: attractive if you want defined risk/reward with no delta headache at expiry
- Knock-out put: discounted vs vanilla put (barrier kills the position), but with σ=2.51 the barrier breach probability is high — price accordingly
- Hedging: delta-hedge via `AETHER_CRYSTAL` spot if you want to isolate vega/gamma exposure; but with σ=2.51 daily hedging may not be viable — size positions to tolerate unhedged delta risk

---

### Submission
- Input orders in the Manual Challenge Overview window
- Click "Submit"; re-submit until end of round — last submission before deadline is locked in
