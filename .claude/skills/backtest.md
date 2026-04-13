Run the backtest for the current round and interpret the results.

Steps:
1. Run: `prosperity3bt trader.py 0` (update round number from CLAUDE.md if needed)
2. Report per-product PnL breakdown — not just total
3. Flag any product with flat PnL (likely not trading — check order generation)
4. Flag any product with negative PnL (adverse selection or wrong fair value)
5. Compare day-by-day variance — high variance = overfitting risk
6. Suggest one specific improvement if any product underperforms
