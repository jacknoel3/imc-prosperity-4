# Round 1 Market Analysis

Offline analysis entrypoint:

```bash
python3 analyze_round1_market.py \
  --repo-root . \
  --output-dir analysis_outputs/round1_market \
  --probe-log backtests/pepper_hold_v1_round1_day0.log \
  --compare-root ../imc-prosperity-4-montecarlo/backtests/sessions
```

Useful variants:

```bash
python3 analyze_round1_market.py --repo-root . --product ASH_COATED_OSMIUM
python3 analyze_round1_market.py --repo-root . --product INTARIAN_PEPPER_ROOT
python3 analyze_round1_market.py --repo-root . --probe-log-glob 'backtests/*.log'
```

Outputs are written under `analysis_outputs/round1_market/`:

- `<product>/plots/*.svg`
- `<product>/summary.json`
- `<product>/simulator_parameters.json`
- `<product>/report.md`
- `<product>/tables/*.csv`
- `round1_analysis_summary.json`

The analysis is intentionally reduced-form:

- CSVs drive the market-layer inference
- probe logs improve fill / response estimates when available
- latent bots are described as archetypes rather than exact recovered agents
