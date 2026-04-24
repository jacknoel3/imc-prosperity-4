# Agents Gather — How the Council Works

## The one command

```
agents gather <your question or idea>
```

That's it. One command runs all agents, adds a critique round when they disagree, and always ends with adversarial pressure from both poles. No need to choose between tools.

---

## The Roster

### Perspective agents — core debaters

These participate in Round 1 (and Round 2 if there's disagreement). Each produces a full position from their specialty.

| Agent | Specialty |
|-------|-----------|
| `quant-alpha` | Statistical arbitrage / signal validity skeptic |
| `quant-beta` | Market microstructure / HFT market-making |
| `quant-gamma` | Risk management / sizing / survival |
| `quant-delta` | Execution / realistic fills / implementation shortfall |
| `quant-epsilon` | Derivatives / stochastic processes / volatility (key in Round 3+) |

### Task agents — one-paragraph observations only

These don't debate — they contribute a single observation from their specialty's lens, then stop.

| Agent | Lens |
|-------|------|
| `researcher` | What data or external evidence is relevant here? |
| `coder` | What implementation risk or execution complexity does this introduce? |
| `reviewer` | What could go wrong in submission / validation? |
| `orchestrator` | What coordination or sequencing issue is this raising? |

### Challengers — always run last

These two never participate in the debate. They receive a neutral summary of the emerging consensus and stress-test it from opposite poles.

| Agent | Role |
|-------|------|
| `quant-devils-advocate` | Strongest case AGAINST — tail risk, overfit, edge decay, under-priced assumptions |
| `quant-optimist` | Strongest case FOR going further — under-bet edge, excessive caution, missed upside |

---

## The Protocol

```
Step 1   Discover roster from .claude/agents/ (auto-scaling — new files auto-join)
Step 2   Read CLAUDE.md + rules → compress into context pack
Step 3   Open a new section in shared_reasoning.md
Step 4   Round 1: spawn all debaters + task agents in parallel
Step 4.5 Round 2 (conditional): if agents meaningfully disagree, spawn perspective agents
         again — each sees all Round 1 positions and can refine or double down
         ↳ Skipped if Round 1 reaches full consensus
Step 4.75 Challenger review (always): spawn quant-devils-advocate + quant-optimist in
          parallel with a neutral summary of the emerging consensus
Step 5   Append all outputs to shared_reasoning.md
Step 6   Return a synthesized Decision Memo to the user
```

---

## The Output

```
# Agents gathered — balanced view

Question: ...

Consensus (what agents agreed on): ...
Genuine disagreement (where perspectives split): ...

Devil's advocate (strongest case against): ...
Optimist (strongest case for going further): ...

Balanced recommendation: ...
Suggested next step: researcher / coder / reviewer / none
```

Full reasoning traces are written to `shared_reasoning.md` — the memo is the summary, the file is the record.

---

## When to use it

Use `agents gather` for any question where you want multi-angle input before deciding:
- Strategy questions ("should we add aggressive-take logic to IPR?")
- Parameter choices ("what MAF bid should we submit?")
- Round-transition planning ("pre-game plan for Round 3 options")
- Anything where a wrong answer costs real PnL

Don't use it for: factual lookups, data analysis (use `@researcher`), implementation (use `@coder`).

---

## Adding new agents

- **New debater**: drop a `.md` file in `.claude/agents/` — it auto-joins the next gather in Round 1.
- **New challenger**: same, but set its role in the file to indicate it should run as a challenger (update Step 4.75 in `agents-gather-protocol.md` to spawn it).
- **New task agent**: same auto-join as debaters; it will be prompted for a 1-paragraph observation only.
