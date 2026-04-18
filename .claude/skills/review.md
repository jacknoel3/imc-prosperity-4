Run a pre-submission review of trader.py using the reviewer agent checklist.

Steps:
1. Check trader.py exists at repo root — if not, stop and report FAIL: file not found
2. Run through the reviewer checklist:
   - Correct return signature on all paths: `tuple[dict[str, list[Order]], int, str]`
   - No print() calls (grep explicitly)
   - Both Round 2 products present (ASH_COATED_OSMIUM, INTARIAN_PEPPER_ROOT), no Round 0 leftovers (EMERALDS, TOMATOES)
   - Position limits correct: both = 80
   - `bid()` method present and returns a non-zero integer (MAF — must not be 0 before final submission)
   - `sell_orders` values negated correctly in qty math (`-ask_vol`)
   - `traderData` deserialization wrapped in try/except
   - No book-crossing passive quotes (`bid < best_ask`, `ask > best_bid` guards present)
   - Holt's state (`level`, `trend`) initialized with fallback and persisted in traderData (IPR)
   - No aggressive takes in `_trade_ipr`
   - OBI for IPR is CONTRARIAN: `OBI > threshold → suppress bid` (not ask)
3. Output PASS or FAIL with specific line references for any issue
4. If FAIL: report the specific issues — they will ask coder to fix them
