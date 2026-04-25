# Codex Council — Working Guide

## Command

```text
codex gather <question or strategy idea>
```

This runs the council workflow using the role prompts in `.codex_council/agents/`.

If the user explicitly asks for real agents, Codex should spawn sub-agents and pass the matching role prompt into each sub-agent task. If the user only asks for a council-style view, Codex may run the same roles in-thread and clearly say no sub-agents were spawned.

## Roster

Perspective agents:
- `quant-alpha` — statistical validity / overfit skeptic
- `quant-beta` — market microstructure / fill quality
- `quant-gamma` — risk, sizing, drawdown
- `quant-delta` — execution realism / implementation shortfall
- `quant-epsilon` — derivatives, volatility, options

Task agents:
- `researcher` — data and evidence lens
- `coder` — implementation and backtest lens
- `reviewer` — submission and validation lens
- `orchestrator` — sequencing and decision lens

Challengers:
- `quant-devils-advocate` — strongest case against
- `quant-optimist` — strongest case for going further

## Repo-Specific Defaults

- Submission file is `trader.py` at repo root.
- Current active round is Round 3.
- Current products: `HYDROGEL_PACK`, `VELVETFRUIT_EXTRACT`, and vouchers `VEV_4000` through `VEV_6500`.
- Main analysis lives under `phase2/round3/algo/analysis/`.
- Strategy experiments live under `phase2/round3/algo/strategy/`.
- Backtest logs live under `phase2/round3/algo/backtests/logs/` and root `backtests/`.

## Separation Rule

Use council for strategy decisions. Use coder-style work for implementation. Use reviewer-style work for pre-submit validation. Do not collapse research, implementation, and review into one unstructured answer unless the user asks for a quick direct fix.
