# Running the code

Run commands from the repository root with Python 3.11 or newer. The lightweight environment covers replay, figures, manual models, and the market-profile scripts:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Historical replay

```bash
python -m prosperity3bt rounds/round1/algo/trader.py 1 --data data --no-out --no-progress
python -m prosperity3bt rounds/round2/algo/trader.py 2 --data data --no-out --no-progress
python -m prosperity3bt rounds/round3/algo/trader.py 3 --data data --no-out --no-progress
python -m prosperity3bt rounds/round4/algo/ale61.py 4 --data data --no-out --no-progress
```

Use a selector such as `1-0` for a single historical day. The reader expects round subdirectories below `data/`. To retain a visualizer-compatible log, replace `--no-out` with `--out outputs/replay/round1.log`.

The Python engine is our local adaptation of the Prosperity 3 backtester. It approximates matching and market behaviour, so results can differ from platform exports. It also does not reproduce the market-access auction. Round 5 data is included, but the engines alter the public tape while matching orders; see the strategy's [replay notes](../rounds/round5/README.md#reproduction-status).

## Interactive dashboards

```bash
python3 -m http.server 8000 --bind 127.0.0.1
```

Open <http://localhost:8000/rounds/round5/dashboard/> or another round listed in the [dashboard guide](dashboards.md). Serve from the repository root so the CSV paths resolve. GitHub displays the dashboard source; run the server to use the interactive views.

## Manual models

Round 2's original allocation sensitivity example:

```bash
python rounds/round2/manual/pnl_stresstest.py
```

Round 3's small scenario run, with output separated from historical records:

```bash
python rounds/round3/manual/bid_model.py --simulations 1000 --output-dir outputs/round3/manual
```

The default is 20,000 simulations of the competitor mean, with 4,000 competitors and seed 42. `--competitors` and `--seed` can be changed to study those assumptions. The model's narrow distributions are conditional on its behavioural scenarios.

Round 4's small pricing and portfolio run:

```bash
python rounds/round4/manual/aether_model.py --n-price-paths 10000 --chunk-size 5000 --n-trials 100 --n-optimizer-paths 10000 --sharpe-random-portfolios 1000 --output-prefix outputs/round4/manual/aether
```

This uses reduced samples for a quicker run. Omit the sampling overrides to use the original defaults. Monte Carlo noise can change the selected trades, and exact seeds for the saved outputs still need to be added. Quotes, volume limits, the time grid, and contract size are constants in the source.

## Research scripts and notebook

```bash
python rounds/round1/analysis/market_analysis.py --output-dir outputs/round1
python rounds/round4/analysis/market_profile.py --out outputs/round4/market
```

The broader options analysis and round 2 notebook need additional scientific libraries:

```bash
python -m pip install -r requirements-research.txt
python rounds/round3/analysis/options_analysis.py --data-dir data/round3 --output-dir outputs/round3/options
jupyter lab
```

Open `rounds/round2/analysis/market_analysis.ipynb`. Its data path locates the public repository from either the root or the notebook directory. Regenerated notebook figures go below `outputs/round2/`.

The options analysis generates many tables and figures. It studies pricing models beyond those used in the strategy snapshot.

The round 4 log profiler needs an actual submission/probe log:

```bash
python rounds/round4/analysis/player_profile.py --help
```

## Figures

```bash
python scripts/render_figures.py
```

This regenerates the PnL figures from the saved curves, the round 1 price plot from historical CSVs, and the round 4 manual comparison from mock-game summaries. The round 3 research figures come from the original options analysis and bid model runs.

See the [backtester guide](../tools/replay/README.md) for tool attribution and matching assumptions.
