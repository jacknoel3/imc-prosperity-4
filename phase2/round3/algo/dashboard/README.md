# Prosperity Round 3 Dashboard

This dashboard is the round 3 copy of the market replay viewer. It is wired to
the data in `phase2/round3/algo/data`.

Built-in selectors:

- `Round 3 / Day 0 / Full product universe`
- `Round 3 / Day 1 / Full product universe`
- `Round 3 / Day 2 / Full product universe`

The product selector is populated from the loaded CSV and covers the round 3
universe: Hydrogel Packs, Velvetfruit Extract, and the VEV contracts.

## Run It

From the repo root:

```bash
python3 -m http.server 8000
```

Then open:

```text
http://localhost:8000/phase2/round3/algo/dashboard/
```

## What It Shows

- order book depth over time
- market trade overlays
- market trade filters by quantity, trader ID, and buyer/seller aggressive or passive role
- backtest trade overlays drawn on top of the market plot
- own-trade highlighting when buyer or seller IDs are present
- hoverable snapshot inspection
- price normalization by `midPrice` or `wallMid`
- indicator overlays
- optional synced logs
- PnL and position panels when your own strategy data is available
- a `Synthetic Lab` tab that block-resamples the real round 3 tape

## Built-In Data

The dashboard reads:

- `../data/prices_round_3_day_0.csv`
- `../data/prices_round_3_day_1.csv`
- `../data/prices_round_3_day_2.csv`
- `../data/trades_round_3_day_0.csv`
- `../data/trades_round_3_day_1.csv`
- `../data/trades_round_3_day_2.csv`

## Strategy Overlay Format

Backtest trade overlays can be loaded from one CSV or from multiple per-day CSVs:

```text
day,timestamp,product,price,quantity,side,pnl,position
0,4500,VELVETFRUIT_EXTRACT,5250,2,buy,0,2
1,7200,VEV_5400,24,4,sell,12,-2
```

Required fields: `timestamp`, `product` or `symbol`, `price`, and either
`quantity` plus `side`, or a signed quantity field such as `signed_quantity`.
Optional fields: `day`, `pnl`, `position`.

If you upload separate files and they do not include a `day` column, the
dashboard infers the day from filenames containing patterns like `day_0`,
`day-1`, or `day 2`. Day-aware overlays are filtered to the active dataset day,
so day 0, 1, and 2 fills do not get mixed together at the same timestamp.

Indicator CSV:

```text
timestamp,product,name,value
0,VELVETFRUIT_EXTRACT,fair_value,5250
100,VEV_5400,edge,1.4
```

Log file:

- CSV: `timestamp,product,message`
- JSON array or JSONL with `timestamp`, `product`, `message`

## Notes

- The dashboard is static; serve it with `python3 -m http.server` so browser
  fetches can read the CSV data.
- `wallMid` is computed from the highest-volume bid and ask levels available in
  each row.
- Public market-trade direction is inferred from trade price versus the current
  book unless a trade matches one of your configured own trader IDs.
