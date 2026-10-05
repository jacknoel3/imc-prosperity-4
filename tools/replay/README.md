# Local Python replay backtester

Our local adaptation of [Jasper van Merle's Prosperity 3 backtester](https://github.com/jmerle/imc-prosperity-3-backtester), with its original [MIT licence](LICENSE). Use the root `data/` directory for historical datasets.

Install with the repository's `requirements.txt`, or from the repository root:

```bash
python -m pip install -e tools/replay
python -m prosperity3bt rounds/round1/algo/trader.py 1 --data data --no-out --no-progress
```

The package exposes `prosperity3bt` and `prosperity4bt` CLI aliases. Use `python -m prosperity3bt --help` for options.

Replay approximates matching and bot behaviour. It does not reproduce the market-access bidding mechanism or the manual challenges, and its result need not match a saved platform export. Round 5's fingerprint strategy needs the full original public-trade stream; see its [replay notes](../../rounds/round5/README.md#reproduction-status).
