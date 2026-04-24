# Agents Gather Protocol

**Trigger**: user message starts with `agents gather <question>` (case-insensitive).
Main Claude executes this in full. Never abbreviate or substitute a direct answer.

---

## Roster rules

Discover agents dynamically: `Glob(".claude/agents/*.md")` — filenames without `.md` are the roster.

Always exclude from the main debate:
- `quant-council` — deleted/deprecated
- `quant-devils-advocate` — runs as challenger in Step 4.75
- `quant-optimist` — runs as challenger in Step 4.75

---

## Steps

### 1 — Build the context pack

Read in parallel: `CLAUDE.md`, `.claude/GUIDE.md`, `.claude/rules/submission.md`, `.claude/rules/round-roadmap.md`. Add any product rules or files the user referenced.

Compress into a **10–20 line plaintext summary** (no raw CSVs, no full trader.py). This pack goes to every agent.

### 2 — Open a gathering entry in shared_reasoning.md

Append (never overwrite):

```
---

# Gathering — <ISO-8601 UTC timestamp>

**Question**: <user's idea, verbatim>
**Round**: <current round>
**Roster**: <comma-separated agent list>
**Challengers**: quant-devils-advocate, quant-optimist
**Context pack**:
<10–20 line summary>

---
```

If `shared_reasoning.md` doesn't exist, create it first — see "Initial file" at the bottom.

### 3 — Round 1: spawn all agents in parallel

One message, N parallel Agent tool calls. Each prompt:

1. `"You are participating in an 'agents gather' protocol. All agents in .claude/agents/ are running in parallel."`
2. User's question, verbatim
3. Context pack
4. `"Produce a full report from your perspective as defined in your agent file. If no format is specified, use the standard format at the bottom of agents-gather-protocol.md."`
5. `"Include a Reasoning trace: (a) assumptions, (b) evidence weighed, (c) inference, (d) conclusion."`
6. `"Under 500 words. Density over verbosity."`

**Task agents** (researcher, coder, reviewer, orchestrator) get one extra line: `"Do NOT execute any tasks. Return a 1-paragraph observation from your specialty's lens only."`

### 4 — Round 2: critique (conditional)

After Round 1, assess: do at least two agents hold genuinely opposing positions on a concrete claim?

- **No** → skip to Step 5
- **Yes** → spawn perspective agents only (quant-alpha/beta/gamma/delta/epsilon) in parallel again. Each prompt:
  1. Original question
  2. Their own Round 1 position quoted back ("Your Round 1 position:")
  3. All other Round 1 positions labeled by agent name ("Other positions:")
  4. `"Round 2: refine, concede, or double down. Be specific about what changed your view or what you still dispute. Under 300 words."`

Task agents do not participate in Round 2.

### 5 — Challenger review (always runs)

After the debate, spawn `quant-devils-advocate` and `quant-optimist` in parallel. Each gets:

1. Original question
2. A neutral 5–8 sentence summary of the emerging consensus and contested points (no agent names)
3. Role instruction:
   - Devil: `"Surface the strongest case AGAINST this recommendation — tail risk, overfit, edge decay, under-priced execution assumptions. Under 200 words."`
   - Optimist: `"Surface the strongest case FOR going further — under-bet edge, excessive caution, missed upside. Under 200 words."`

### 6 — Write to shared_reasoning.md

Append all responses verbatim under the gathering entry:

```
## <agent-name> — Round 1
<response>
---

## <agent-name> — Round 2        ← only if Round 2 ran
<response>
---

## quant-devils-advocate — Challenger
<response>
---

## quant-optimist — Challenger
<response>
---

## Main Claude synthesis
<synthesis>
=== END OF GATHERING ===
```

### 7 — Return synthesis to user

```
# Agents gathered — balanced view

**Question**: <one line>

**Consensus**: <bullets — what everyone agreed on>

**Disagreement**: <the specific tension and which agents split>

**Devil's advocate**: <strongest case against>

**Optimist**: <strongest case for going further>

**Recommendation**: <2–3 sentences — key trade-off and what you'd do>

**Next step**: <researcher / coder / reviewer / none>

**Record**: appended to shared_reasoning.md (<timestamp>)
```

Under 400 words. The detail is in the file.

---

## When NOT to run

- Factual question ("what's the IPR limit?") — answer directly
- `@<agent-name>` — direct invocation, not a gather
- `agents gather` with no question — ask what they want debated
- Trivially simple questions

## Crash handling

If an agent returns nothing, log `## <agent-name>\n(no response)` in shared_reasoning.md and continue. Do not retry.

---

## Initial file header (if shared_reasoning.md doesn't exist)

```
# Shared Reasoning Log

Persistent record of every `agents gather` invocation. Append-only — never compress, reorder, or delete sections.

To search by agent or question: grep this file.
```

---

## Standard fallback format (for agents without their own format)

```
## <agent-name> — gather report

**Lens**: <one-line specialty>

**Position**: <2–4 sentences>

**Reasoning trace**:
1. Assumptions: ...
2. Evidence: ...
3. Inference: ...
4. Conclusion: ...

**Confidence**: high / medium / low — reason

**What would change my view**: <specific trigger>

**Strongest case for**: <one sentence>
**Strongest case against**: <one sentence>
```
