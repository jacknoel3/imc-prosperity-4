# IMC Prosperity 4 · PMQuants

We competed in **IMC Prosperity 4 (2026)** as **PMQuants**. This repository contains our algorithms, manual trading models, notebooks, and results across the five rounds.

We finished in the **top 5% overall**, reached **77th globally after round 1**, and found optimal solutions earning **best or joint-best global scores in several manual rounds**. **Over 22,000 teams registered** for the competition.

## Round-by-round retrospective

| Round | Algorithmic approach | Manual challenge | Write-up |
| --- | --- | --- | --- |
| 1 | Mean-reversion market making, Pepper carry, and quotes on empty book sides | Auction research | [Round 1](rounds/round1/README.md) |
| 2 | Refine the phase 1 strategy and choose a market-access bid | Research, sales, and speed allocation | [Round 2](rounds/round2/README.md) |
| 3 | Hydrogel signals and a multi-strike voucher strategy | Two bids, reserve prices, and competitor-mean uncertainty | [Round 3](rounds/round3/README.md) |
| 4 | Counterparty profiling and selective inventory/quote adjustments | Monte Carlo valuation of vanilla and exotic options | [Round 4](rounds/round4/README.md) |
| 5 | Public-trade fingerprints mapped to target positions | Write-up to follow | [Round 5](rounds/round5/README.md) |

The round pages explain the strategies and models, with links to the code and research. We will add more of our decision-making, exact submissions, and official score breakdowns as we work through our notes.

The [saved results](results/README.md) are backtests and simulation exports. Runs have different lengths and should be read separately from our competition standings. Rounds 1–3 include final-labelled strategy snapshots; rounds 4–5 still need confirmation of the exact submitted versions.

## Repository layout

```text
rounds/        Five write-ups, selected algorithms, manual models, and figures
data/          Historical price/trade CSVs for rounds 1–5
results/       Index of recorded test and simulation results
tools/         Python backtester, with its original licence
scripts/       Plot the saved results
docs/          Local setup and dashboard instructions
datamodel.py   Shared Prosperity types for local runs
```

## Run the examples

Use Python 3.11 or newer, and run commands from the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m prosperity3bt rounds/round1/algo/trader.py 1 --data data --no-out --no-progress
```

The local replay package is adapted from the Prosperity 3 backtester; replay results can differ from the original platform exports. See [running the code](docs/reproducing.md) for the manual models, research scripts, and figures.

The [interactive dashboards](docs/dashboards.md) let you explore order books, public trades, counterparty filters, and your own trade overlays for rounds 1, 2, 3, and 5. They run locally with Python's HTTP server.

## Acknowledgements

We used and adapted [Jasper van Merle's backtester](https://github.com/jmerle/imc-prosperity-3-backtester) and visualizer, and [GeyzsoN's Rust backtester](https://github.com/GeyzsoN/prosperity_rust_backtester). The [Frankfurt Hedgehogs retrospective](https://github.com/TimoDiehm/imc-prosperity-3) informed our preparation. The included [Python backtester](tools/replay/README.md) carries its original MIT licence.
