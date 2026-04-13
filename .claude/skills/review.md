Run a pre-submission review of trader.py using the reviewer agent checklist.

Steps:
1. Read trader.py in full
2. Run through the reviewer checklist:
   - Correct return signature on all paths
   - No print() calls (grep explicitly)
   - Position limits respected (aggressive + passive combined)
   - sell_orders values negated correctly in qty math
   - traderData deserialization in try/except
   - No book-crossing passive quotes
3. Output PASS or FAIL with specific line references for any issue
4. If FAIL: hand off to coder agent to fix before submitting
