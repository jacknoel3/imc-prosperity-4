# Claude Code — How to Work With This Repo

## The Big Picture

```
You
 └── Claude (main conversation)
      └── @orchestrator  ← coordinates everything
           ├── @researcher   ← data + external research
           ├── @coder        ← implements + backtests
           └── @reviewer     ← validates before submit
```

**Rule of thumb:**
- Quick question or small edit → just talk to Claude directly
- Anything involving data, strategy, or code changes → use agents

---

## How to Invoke Agents

Type `@agent-name` followed by your task. Claude Code loads that agent's context and runs it.

```
@orchestrator analyze the new products from Round 1 and update our strategy
@researcher what does the TOMATOES data say about autocorrelation?
@coder tighten the passive spread on EMERALDS from edge=2 to edge=1 and backtest
@reviewer check trader.py before I submit
```

You can go directly to a child agent when the task is clear-cut. Use `@orchestrator` when the task spans multiple steps or you're not sure who should handle it.

---

## How to Use Skills (Slash Commands)

Skills are pre-defined workflows. Run them by typing the slash command:

| Command | What it does |
|---------|-------------|
| `/backtest` | Runs `prosperity3bt trader.py 0`, reports per-product PnL, flags regressions |
| `/review` | Full pre-submission checklist on trader.py — returns PASS or FAIL |
| `/new-round` | Scaffolds a new round: updates CLAUDE.md, creates rules file for new products |

---

## Standard Workflows

### Starting a new session
Just open Claude Code in this repo. `CLAUDE.md` loads automatically — no setup needed.

### Working on a strategy improvement
```
1. @researcher [question about the product data]
2. @coder [implement based on researcher findings]
3. /review
4. Submit
```

### When a new round drops
```
1. /new-round  ← tells you exactly what files to update
2. Drop new CSVs into data/round<N>/
3. @researcher analyze [new products]
4. @coder implement strategy for [new products]
5. /review
6. Submit
```

### Quick fix (no agents needed)
```
Just describe the fix to Claude directly.
Claude reads trader.py, makes the change, done.
```

---

## What Lives Where

| Content | Location | Loaded |
|---------|----------|--------|
| Competition context + commands | `CLAUDE.md` | Always |
| Submission hard constraints | `.claude/rules/submission.md` | On reference |
| EMERALDS strategy + findings | `.claude/rules/products/emeralds.md` | On reference |
| TOMATOES strategy + findings | `.claude/rules/products/tomatoes.md` | On reference |
| Future rounds roadmap | `.claude/rules/round-roadmap.md` | On reference |
| Agent definitions | `.claude/agents/*.md` | On invocation |
| Skill definitions | `.claude/skills/*.md` | On `/command` |

**Key point:** Rules and agents cost zero tokens until you use them. Only `CLAUDE.md` loads every session.

---

## Round Transition Checklist

At the start of each new round:
- [ ] Run `/new-round` to scaffold files
- [ ] Add new CSVs to `data/round<N>/`
- [ ] Update round number in `CLAUDE.md` products table
- [ ] Create `.claude/rules/products/<new_product>.md` with researcher findings
- [ ] Update backtest command round number in `CLAUDE.md`
- [ ] Run `@researcher` on new products before touching `trader.py`

---

## Files You Should Never Touch

| File | Why |
|------|-----|
| `datamodel.py` | Provided by IMC — overwritten on each round |
| `tradertest.py` | Jack's reference copy — read only |
| `.claude/agents/*.md` | Edit only if agent behavior needs to change |

---

## Submission File

**Always submit `trader.py`** — never `tradertest.py` or any other file.

Pre-submit: run `/review` and wait for PASS before uploading.
