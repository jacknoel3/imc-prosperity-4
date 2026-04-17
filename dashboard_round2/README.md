# Prosperity Round 2 Dashboard

This is the round 2 dashboard for the Intara market:

- `ASH_COATED_OSMIUM`
- `INTARIAN_PEPPER_ROOT`

If you want the previous Intara view, open `http://localhost:8000/dashboard_round1/`.

## Round 2 Context

- built-in day set: `-1`, `0`, and `1`
- planet: `Intara`
- `ASH_COATED_OSMIUM`: fixed fair value around `10,000`, classical stationary market-making product
- `INTARIAN_PEPPER_ROOT`: trending product with dynamic fair value and roughly `+1000/day` drift across the built-in day set

## Run It

From the repo root:

```bash
python3 -m http.server 8000
```

Then open:

```text
http://localhost:8000/dashboard_round2/
```

Built-in selectors:

- `Round 2 / Day 1 / ASH_COATED_OSMIUM + INTARIAN_PEPPER_ROOT`
- `Round 2 / Day 0 / ASH_COATED_OSMIUM + INTARIAN_PEPPER_ROOT`
- `Round 2 / Day -1 / ASH_COATED_OSMIUM + INTARIAN_PEPPER_ROOT`

## What It Shows

- order book depth over time
- market trade overlays
- backtest trade overlays drawn on top of the market plot
- own-trade highlighting when buyer or seller IDs are present
- hoverable snapshot inspection
- price normalization by `midPrice` or `wallMid`
- indicator overlays
- optional synced logs
- PnL and position panels when your own strategy data is available
- a separate `Synthetic Lab` tab for Monte Carlo-style block-bootstrap scenarios built from the real round 2 tape

## Strategy Workflow

The dashboard is a viewer, not the backtester itself. You do not upload `trader.py`
directly into the dashboard.

Typical flow:

1. Run your round 2 strategy in your backtester.
2. Export your fills to a `Backtest Trades CSV`.
3. Pick the round 2 market dataset you want as the background.
4. Load the `Backtest Trades CSV` as an overlay.

For built-in data, you usually only need:

- `Backtest Trades CSV`

For custom datasets:

- upload `Price CSV`
- optionally upload `Trade CSV`
- click `Load Uploaded Files`
- then load the backtest overlay

For synthetic testing:

- open the `Synthetic Lab` tab
- pick a source window, product, horizon, block length, and seed
- generate a batch of scenarios
- either inspect the ensemble in the lab or click `Open Selected In Replay` to move one scenario into the full replay viewer

Optional extras:

- `Indicator CSV` for fair values, EMAs, trend estimates, spreads, or other signals
- `Log File` for timestamp-synced notes

## Built-In Data

The round 2 dashboard reads:

- `data/round2/prices_round_2_day_1.csv`
- `data/round2/prices_round_2_day_0.csv`
- `data/round2/prices_round_2_day_-1.csv`
- `data/round2/trades_round_2_day_1.csv`
- `data/round2/trades_round_2_day_0.csv`
- `data/round2/trades_round_2_day_-1.csv`

Public trade files do not reliably identify your trader, so market-trade direction
is inferred from trade price versus the current book unless the trade matches one of
your IDs.

## Accepted Upload Formats

Price CSV:

- same schema as the built-in `prices_*.csv`

Trade CSV:

- same schema as the built-in `trades_*.csv`
- own trades are detected from `buyer` or `seller`

Backtest Trades CSV:

```text
timestamp,product,price,quantity,side,pnl,position
200,ASH_COATED_OSMIUM,10003,5,sell,-15,-5
5200,INTARIAN_PEPPER_ROOT,12001,3,buy,23,-1
6300,ASH_COATED_OSMIUM,10008,2,sell,24,-7
```

- required fields: `timestamp`, `product` or `symbol`, `price`, and either `quantity` + `side`
- signed quantity fields such as `signed_quantity` also work
- optional fields: `pnl`, `position`

Examples of accepted backtest trade formats:

```text
timestamp,product,price,quantity,side
200,ASH_COATED_OSMIUM,10003,5,sell
5200,INTARIAN_PEPPER_ROOT,12001,3,buy
```

```text
timestamp,symbol,fill_price,signed_quantity,pnl,position
200,ASH_COATED_OSMIUM,10003,-5,-15,-5
5200,INTARIAN_PEPPER_ROOT,12001,3,23,-1
```

Indicator CSV:

```text
timestamp,product,name,value
0,ASH_COATED_OSMIUM,fair_value,10000
100,INTARIAN_PEPPER_ROOT,trend_fair,12000.1
```

Log file:

- CSV: `timestamp,product,message`
- JSON array or JSONL with `timestamp`, `product`, `message`

## Notes

- `wallMid` is computed from the highest-volume bid and ask levels available in each row.
- The main chart is canvas-based so it stays responsive on large files.
- Backtest fills are drawn after the market layers so they stay visible on top of the chart.
- The PnL panel uses backtest-overlay `pnl` values when present; otherwise it marks strategy fills to mid.
- The synthetic generator uses contiguous block resampling rather than a single fitted parametric process, so it keeps local book/trade texture while reducing the risk of overfitting to one public day.
