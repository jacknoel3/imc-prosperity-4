# Explore the trading dashboards

The dashboards show historical books, public trades, and optional strategy overlays. They use the included CSVs and run locally without installing JavaScript packages.

From the repository root:

```bash
python3 -m http.server 8000 --bind 127.0.0.1
```

| Round | Historical days | Local address |
| --- | --- | --- |
| [1](../rounds/round1/README.md) | −2, −1, 0 | <http://localhost:8000/rounds/round1/dashboard/> |
| [2](../rounds/round2/README.md) | −1, 0, 1 | <http://localhost:8000/rounds/round2/dashboard/> |
| [3](../rounds/round3/README.md) | 0, 1, 2 | <http://localhost:8000/rounds/round3/dashboard/> |
| [5](../rounds/round5/README.md) | 2, 3, 4 | <http://localhost:8000/rounds/round5/dashboard/> |

Open these addresses while the local server is running. Round 4's research is in its [write-up](../rounds/round4/README.md).

## Read the historical market

Choose a day and product, then narrow the timestamp range. The main chart includes three book levels and public-trade markers; hovering reveals a book snapshot. Normalize prices by the midpoint or wall midpoint to compare changes around the book. Downsampling helps when viewing large windows.

Trade filters select quantities, buyer/seller IDs, and inferred aggressive/passive roles. Side and role labels are inferred from available market information, rather than supplied as verified bot classifications. They are useful for exploring hypotheses about the tape.

Round 5 includes 50 products across ten families; the algorithm configures 40 of them.

## Add a strategy overlay

Upload a CSV under **Backtest Trades CSV**, then choose **Load Backtest Overlay**. The expected columns are:

```csv
day,timestamp,product,price,quantity,side,pnl,position
```

`day`, `pnl`, and `position` are optional. A signed quantity can replace `side`; filenames containing a day can identify single-day files. PnL and position charts need corresponding data in the uploaded export or an identifiable own-trade stream. The repository's compact result curves contain aggregate PnL samples, rather than this trade-level overlay format.

The controls also accept custom price/trade CSVs, indicators (`timestamp,product,name,value`), and logs (`timestamp,product,message` or JSONL records). Uploaded files are parsed in the browser.

## Synthetic scenarios

The Synthetic Lab resamples blocks of the historical tape to explore alternative paths. These generated scenarios depend on the selected source window and settings. They are exploratory views, rather than independent evidence of out-of-sample strategy performance.
