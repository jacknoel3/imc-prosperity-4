Run a pre-submission review of trader.py using the reviewer agent checklist.

Steps:
1. Check trader.py exists — if not, stop and report FAIL: file not found
2. Run through the reviewer checklist:
   - Correct return signature on all paths
   - No print() calls (grep explicitly)
   - Both Round 1 products present (ASH_COATED_OSMIUM, INTARIAN_PEPPER_ROOT), no Round 0 leftovers
   - Position limits correct: both = 80
   - sell_orders values negated correctly in qty math
   - traderData deserialization in try/except
   - No book-crossing passive quotes
3. Output PASS or FAIL with specific line references for any issue
4. If FAIL: report the specific issues to the user — they will ask the coder to fix them
