# Prosperity Round 0 Dashboard

This is the preserved round 0 dashboard for the tutorial products `EMERALDS` and
`TOMATOES`.

If you want the current competition view for Intara, open the round 1 dashboard at
`http://localhost:8000/phase1/round1/algo/dashboard/`.

## Run It

From the repo root:

```bash
python3 -m http.server 8000
```

Then open:

```text
http://localhost:8000/dashboard/
```

Built-in selectors:

- `Round 0 / Day -1 / EMERALDS + TOMATOES`
- `Round 0 / Day -2 / EMERALDS + TOMATOES`

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

## Strategy Workflow

The dashboard is a viewer, not the backtester itself. You do not upload `trader.py`
directly into the dashboard.

Typical flow:

1. Run your strategy in your backtester.
2. Export your fills to a `Backtest Trades CSV`.
3. Pick the round 0 market dataset you want as the background.
4. Load the `Backtest Trades CSV` as an overlay.

If your strategy already ran on the built-in round 0 data, you usually only need:

- `Backtest Trades CSV`

For custom datasets:

- upload `Price CSV`
- optionally upload `Trade CSV`
- click `Load Uploaded Files`
- then load the backtest overlay

Optional extras:

- `Indicator CSV` for fair values, EMAs, z-scores, spreads, or other signals
- `Log File` for timestamp-synced notes

## Built-In Data

The round 0 dashboard reads:

- `data/round0/prices_round_0_day_-1.csv`
- `data/round0/prices_round_0_day_-2.csv`
- `data/round0/trades_round_0_day_-1.csv`
- `data/round0/trades_round_0_day_-2.csv`

Public round 0 trades do not include trader IDs, so market-trade direction is
inferred from trade price versus the current book unless the trade matches one of
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
100,EMERALDS,9998,5,buy,0,5
400,EMERALDS,10002,5,sell,20,0
700,TOMATOES,5009,3,sell,17,2
```

- required fields: `timestamp`, `product` or `symbol`, `price`, and either `quantity` + `side`
- signed quantity fields such as `signed_quantity` also work
- optional fields: `pnl`, `position`

Examples of accepted backtest trade formats:

```text
timestamp,product,price,quantity,side
100,EMERALDS,9998,5,buy
400,EMERALDS,10002,5,sell
```

```text
timestamp,symbol,fill_price,signed_quantity,pnl,position
100,EMERALDS,9998,5,0,5
400,EMERALDS,10002,-5,20,0
```

Indicator CSV:

```text
timestamp,product,name,value
0,EMERALDS,fair_value,10000
100,TOMATOES,ema_fv,5006.4
```

Log file:

- CSV: `timestamp,product,message`
- JSON array or JSONL with `timestamp`, `product`, `message`

## Notes

- `wallMid` is computed from the highest-volume bid and ask levels available in each row.
- The main chart is canvas-based so it stays responsive on large files.
- Backtest fills are drawn after the market layers so they stay visible on top of the chart.
- The PnL panel uses backtest-overlay `pnl` values when present; otherwise it marks strategy fills to mid.
