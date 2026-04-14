# Prosperity Dashboard

This is a zero-dependency dashboard starter built for this repo's Prosperity CSVs. It aims to cover the most useful parts of the Frankfurt Hedgehogs workflow:

- order book depth over time
- market trade overlays
- backtest trade overlays drawn on top of the market plot
- own-trade highlighting when buyer or seller IDs are present
- hoverable snapshot inspection
- price normalization by `midPrice` or `wallMid`
- indicator overlays
- optional synced logs
- PnL and position panels when your own strategy data is available

## Run It

From the repo root:

```bash
python3 -m http.server 8000
```

Then open:

```text
http://localhost:8000/dashboard/
```

It ships with built-in selectors for:

- `Round 0 / Day -1`
- `Round 0 / Day -2`
- `Round 1 / Day -1`
- `Round 1 / Day -2`
- `Round 1 / Day 0`

## Viewing Your Strategy In The Dashboard

The dashboard is a viewer, not the backtester itself. You do not upload `trader.py`
directly into the dashboard.

The workflow is:

1. Run your strategy in your backtester.
2. Export your strategy fills to a `Backtest Trades CSV`.
3. Open the dashboard and choose the market dataset you want in the background.
4. Load the `Backtest Trades CSV` as an overlay.

### Minimal workflow for the built-in tutorial data

If your strategy was backtested on the tutorial round data already included in this repo,
you usually only need one file:

- `Backtest Trades CSV`

Steps:

1. Start the dashboard:

```bash
python3 -m http.server 8000
```

2. Open:

```text
http://localhost:8000/dashboard/
```

3. In the dashboard, select:

- `Round 0 / Day -1` or
- `Round 0 / Day -2`

4. Upload your `Backtest Trades CSV`.
5. Click `Load Backtest Overlay`.

Your strategy fills will then appear on top of the market plot as diamond markers.
The PnL and Position panels will also use this backtest overlay when possible.

### When you need the other upload fields

You do not need to upload every file.

- `Backtest Trades CSV`: your strategy fills. This is the main file for visualizing your strategy.
- `Price CSV`: only needed if you want to use a custom market dataset instead of the built-in tutorial data.
- `Trade CSV`: only needed if you want to show custom market trades instead of the built-in market trade file.
- `Indicator CSV`: optional. Use this if you want to overlay your own fair values, EMAs, z-scores, spreads, or other indicators.
- `Log File`: optional. Use this if you want timestamp-synced log messages in the log panel.

### Common use cases

Built-in tutorial data:

- upload only `Backtest Trades CSV`

Custom backtest dataset:

- upload `Price CSV`
- click `Load Uploaded Files`
- then upload `Backtest Trades CSV`
- click `Load Backtest Overlay`

Custom indicators/logs:

- upload `Indicator CSV` and/or `Log File` only if you want those panels populated

## Built-In Data Expectations

The dashboard reads the current Prosperity CSVs directly from the `data/` folder in this repo:

- `data/round0/prices_round_0_day_-1.csv`
- `data/round0/prices_round_0_day_-2.csv`
- `data/round0/trades_round_0_day_-1.csv`
- `data/round0/trades_round_0_day_-2.csv`
- `data/round1/prices_round_1_day_-1.csv`
- `data/round1/prices_round_1_day_-2.csv`
- `data/round1/prices_round_1_day_0.csv`
- `data/round1/trades_round_1_day_-1.csv`
- `data/round1/trades_round_1_day_-2.csv`
- `data/round1/trades_round_1_day_0.csv`

Public round-0 trades do not include trader IDs, so the app infers market-trade direction from the price relative to the current book. Own trades only show up when the uploaded trade file includes buyer or seller IDs matching the `Own Trader IDs` field.

For strategy backtests, use the separate `Backtest Trades CSV` input. Those fills are rendered on top of the market plot as a dedicated overlay and can drive the PnL and position panels even when the market trade file has no trader IDs.

## Optional Upload Formats

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
- you can also provide a signed quantity field such as `signed_quantity`
- optional fields: `pnl`, `position`
- supported aliases include `qty`, `trade_price`, `fill_price`, `action`, `direction`, `pos`, `profit_and_loss`

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

Supported column names for the backtest overlay include:

- time: `timestamp`, `time`, `ts`
- product: `product`, `symbol`, `instrument`, `asset`
- price: `price`, `trade_price`, `fill_price`, `execution_price`
- quantity: `quantity`, `qty`, `volume`, `size`, `filled_quantity`
- signed quantity: `signed_quantity`, `signed_qty`, `net_quantity`, `signed_volume`
- side: `side`, `action`, `direction`, `trade_side`, `order_side`
- pnl: `pnl`, `profit_and_loss`, `profit`, `realized_pnl`, `total_pnl`
- position: `position`, `pos`, `inventory`, `net_position`

Notes:

- headers are matched case-insensitively, so `Timestamp` and `timestamp` both work
- if you provide signed quantity, the dashboard can infer buy vs sell automatically
- if you provide `pnl` and `position`, those values are used directly for the lower panels
- if you do not provide `pnl`, the dashboard estimates PnL by marking your trades to mid price
- if you do not provide `position`, the dashboard reconstructs position from your trade flow

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
- The PnL panel uses backtest-overlay `pnl` values when present; otherwise it will mark strategy fills to mid. Without a backtest overlay, it falls back to `profit_and_loss` from the price CSV or detected own trades.
