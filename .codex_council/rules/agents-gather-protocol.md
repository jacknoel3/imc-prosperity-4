# Codex Agents Gather Protocol

## Trigger

Run this when the user says:

```text
codex gather <question>
```

Also run it when the user explicitly asks to use the Codex council or actual sub-agents with the council.

## Important Runtime Constraint

Codex cannot register custom agent types directly from these files in this environment. These files are role prompts. If the user explicitly asks for sub-agents, use available Codex sub-agent types and include the relevant `.codex_council/agents/<name>.md` content in the sub-agent prompt.

If the user does not explicitly ask for sub-agents, run the council in-thread and say that it is council-format, not real sub-agent delegation.

## Step 1 — Build Context Pack

Read in parallel when relevant:
- `CODEX.md`
- `CLAUDE.md`
- `.codex_council/GUIDE.md`
- `.codex_council/rules/agents-gather-protocol.md`
- `.claude/rules/submission.md`
- `.claude/rules/round-roadmap.md`
- files directly referenced by the user

Compress into a 10-20 line plaintext context pack. Do not paste raw CSVs or full strategy files.

## Step 2 — Open Gathering Entry

Append to `shared_reasoning.md`:

```markdown
---

# Gathering — <ISO-8601 timestamp>

**Question**: <user question verbatim>
**Round**: <current round>
**Roster**: <agents>
**Challengers**: quant-devils-advocate, quant-optimist
**Mode**: actual sub-agents | in-thread council
**Context pack**:
<summary>

---
```

Never overwrite or truncate `shared_reasoning.md`.

## Step 3 — Round 1

Run perspective agents and task agents.

For actual sub-agent mode:
- Spawn independent sub-agents for bounded role reports.
- Use `explorer` for codebase/data-reading questions.
- Use `worker` only if the task requires file edits.
- Pass one role prompt from `.codex_council/agents/` to each sub-agent.

For in-thread mode:
- Write each role response yourself using the role prompt as guidance.
- Clearly mark that this was not parallel sub-agent output.

Prompt shape:

```text
You are participating in the Codex council gather protocol.
Role prompt: <agent file content or summary>
Question: <verbatim question>
Context pack: <context>
Return a dense report from your role's lens.
Include reasoning trace: assumptions, evidence, inference, conclusion.
Under 500 words.
```

Task agents (`researcher`, `coder`, `reviewer`, `orchestrator`) return one paragraph only and do not execute tasks unless the user explicitly asked for execution.

## Step 4 — Round 2 If Needed

If at least two perspective agents genuinely disagree on a concrete claim, run a critique round for perspective agents only:

```text
Original question.
Your Round 1 position.
Other Round 1 positions.
Refine, concede, or double down. Under 300 words.
```

## Step 5 — Challengers

Always run:
- `quant-devils-advocate`
- `quant-optimist`

Give them a neutral 5-8 sentence consensus summary. Devil surfaces the strongest case against. Optimist surfaces the strongest case for going further.

## Step 6 — Append Record

Append all role outputs to `shared_reasoning.md`:

```markdown
## <agent> — Round 1
...
---

## <agent> — Round 2
...
---

## quant-devils-advocate — Challenger
...
---

## quant-optimist — Challenger
...
---

## Main Codex synthesis
...

=== END OF GATHERING ===
```

## Step 7 — User Memo

Return under 400 words:

```markdown
**Codex Council**
Question: ...
Mode: actual sub-agents | in-thread council
Consensus: ...
Disagreement: ...
Devil's advocate: ...
Optimist: ...
Recommendation: ...
Next step: researcher | coder | reviewer | none
Record: appended to shared_reasoning.md
```
