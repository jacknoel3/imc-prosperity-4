Run the backtest for the current round and interpret the results.

Steps:
1. Run: `prosperity3bt trader.py 1` (current round = 1; use full venv path if not on PATH)
2. Report per-product PnL breakdown — not just total
3. Flag any product with flat PnL (likely not trading — check order generation)
4. Flag any product with negative PnL (adverse selection or wrong fair value)
5. Compare day-by-day variance — high variance = overfitting risk
6. If any product underperforms, suggest one specific improvement by referencing the product's rule file at `.claude/rules/products/<product>.md` — look at the strategy constants and signals documented there

Round 1 products: ASH_COATED_OSMIUM, INTARIAN_PEPPER_ROOT
