# Recorded runs and their meaning

[recorded_runs.csv](recorded_runs.csv) lists eleven saved backtest and simulation exports. Each has a JSON summary with product PnL and a sampled curve in its round's `results/` directory.

| Round | Recorded backtest / candidate | Recorded longer run |
| --- | ---: | ---: |
| 1 | 11,565.84 (`266953`) | 107,280.53 (`273140`) |
| 2 | 10,469.53 (`356997`) | 95,843.08 (`361780`) |
| 3 | 22,726.03 (`482503`) | 24,505.26 (`485059`) |
| 4 | 12,604.75–21,709.00 (five candidates; [details](../rounds/round4/README.md)) | No identified final export |
| 5 | No saved export | No saved export |

These are test and simulation results, separate from the official leaderboard. The longer runs were stored under `live_simulation_10k_timestamps`; their exact status as competition submissions still needs confirmation.

Curves come from the sampled graph logs, which can stop before the last activity timestamp. The dot marks final PnL from the full export. Product totals use the final activity rows; sampled drawdowns can miss moves between graph samples.

Full exports are available in [Git history](https://github.com/jacknoel3/imc-prosperity-4/tree/eb9cf8e7e32347565bd9d158bdc76f956b2feacc), using the original paths and source commits recorded in the summaries.
