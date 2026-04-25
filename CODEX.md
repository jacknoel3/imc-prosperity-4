# Codex Council

This repo has a Codex-side council workflow in `.codex_council/`.

Use it by asking:

```text
codex gather <question>
```

or, when you want real parallel delegation:

```text
use actual sub-agents with the Codex council to evaluate <question>
```

Codex note: this runtime does not support registering arbitrary custom agent types from files the way Claude Code does. The files under `.codex_council/agents/` are reusable role prompts. When real delegation is requested, Codex should spawn available `explorer` / `worker` / default sub-agents and include the appropriate role prompt content in each delegated task.

Primary files:
- `.codex_council/GUIDE.md` — user-facing workflow
- `.codex_council/rules/agents-gather-protocol.md` — exact gather protocol
- `.codex_council/agents/*.md` — Codex-adapted role prompts
- `shared_reasoning.md` — append-only council record
