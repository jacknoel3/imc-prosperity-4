# Prosperity Round 4 Dashboard

This dashboard is the round 4 copy of the round 1 market replay viewer. It is
wired to the data in `phase2/round4/algo/data`.

Built-in products:

- `HYDROGEL_PACK`
- `VELVETFRUIT_EXTRACT`
- `VEV_4000`
- `VEV_4500`
- `VEV_5000`
- `VEV_5100`
- `VEV_5200`
- `VEV_5300`
- `VEV_5400`
- `VEV_5500`
- `VEV_6000`
- `VEV_6500`

## Run It

From the repo root:

```bash
python3 -m http.server 8000
```

Then open:

```text
http://localhost:8000/phase2/round4/algo/dashboard/
```

Built-in selectors:

- `Round 4 / Day 1 / HGP + VEF + VEV surface`
- `Round 4 / Day 2 / HGP + VEF + VEV surface`
- `Round 4 / Day 3 / HGP + VEF + VEV surface`

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
- a `Synthetic Lab` tab that block-resamples the real round 4 tape

## Built-In Data

The dashboard reads:

- `../data/prices_round_4_day_1.csv`
- `../data/prices_round_4_day_2.csv`
- `../data/prices_round_4_day_3.csv`
- `../data/trades_round_4_day_1.csv`
- `../data/trades_round_4_day_2.csv`
- `../data/trades_round_4_day_3.csv`

## Strategy Overlay Format

Backtest trade overlays can be loaded from CSV:

```text
timestamp,product,price,quantity,side,pnl,position
4500,VEV_5400,18,2,buy,0,2
7200,VELVETFRUIT_EXTRACT,5248,4,sell,12,-2
```

Required fields: `timestamp`, `product` or `symbol`, `price`, and either
`quantity` plus `side`, or a signed quantity field such as `signed_quantity`.
Optional fields: `pnl`, `position`.

Indicator CSV:

```text
timestamp,product,name,value
0,VELVETFRUIT_EXTRACT,fair_value,5245
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
