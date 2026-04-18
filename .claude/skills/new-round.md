Scaffold the transition to a new competition round.

Steps:
1. Ask: round number, new product names, position limits per product, and how many data days are available
2. Update CLAUDE.md:
   - Change "Current phase" to the new round
   - Update the Products table with new products, limits, and strategy stubs
   - Update Data Findings Summary section
   - Update backtest command round number
   - Update repo layout data path for new round
3. Create `.claude/rules/products/<new_product>.md` for each new product:
   ```
   ## <PRODUCT>
   - Fair value: TBD
   - Position limit: TBD
   - Spread: TBD
   - Tick volatility: TBD
   ### Strategy
   - TBD — run researcher agent on new data first
   ```
4. Update `.claude/rules/round-roadmap.md` — mark previous round ✅, mark new round 🔴
5. Update `.claude/agents/coder.md`:
   - Backtest round number in step 3
   - Product names and position limits in output table
   - Data day range in output table headers
6. Update `.claude/agents/orchestrator.md` — round number and position limits in Hard Constraints
7. Update `.claude/agents/reviewer.md` — round number, product names, and position limit values in checklist
8. Update `.claude/agents/researcher.md` — add new data path entry in Data Location block
9. Update `.claude/skills/backtest.md` — round number, product list, data days, and data path
10. Update `.claude/skills/review.md` — round number and product list in checklist
11. Update `.claude/GUIDE.md` — round product summary table, backtest command, data path
12. Remind: run researcher agent on new round CSVs before touching trader.py
13. Remind: trader.py must REMOVE previous round products — do not carry them over
