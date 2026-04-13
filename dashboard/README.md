# Prosperity Dashboard

This is a zero-dependency dashboard starter built for this repo's Prosperity CSVs. It aims to cover the most useful parts of the Frankfurt Hedgehogs workflow:

- order book depth over time
- market trade overlays
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

## Built-In Data Expectations

The dashboard reads the current Prosperity tutorial CSVs directly:

- [prices_round_0_day_-1.csv](/home/guiro/projects/imc-prosperity-4/data/round0/prices_round_0_day_-1.csv)
- [prices_round_0_day_-2.csv](/home/guiro/projects/imc-prosperity-4/data/round0/prices_round_0_day_-2.csv)
- [trades_round_0_day_-1.csv](/home/guiro/projects/imc-prosperity-4/data/round0/trades_round_0_day_-1.csv)
- [trades_round_0_day_-2.csv](/home/guiro/projects/imc-prosperity-4/data/round0/trades_round_0_day_-2.csv)

Public round-0 trades do not include trader IDs, so the app infers market-trade direction from the price relative to the current book. Own trades only show up when the uploaded trade file includes buyer or seller IDs matching the `Own Trader IDs` field.

## Optional Upload Formats

Price CSV:

- same schema as the built-in `prices_*.csv`

Trade CSV:

- same schema as the built-in `trades_*.csv`
- own trades are detected from `buyer` or `seller`

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
- The PnL panel uses `profit_and_loss` from the price CSV when present; otherwise it will mark to mid from detected own trades.
