Scaffold the transition to a new competition round.

Steps:
1. Ask: round number, new product names, position limits per product, and how many data days are available
2. Update CLAUDE.md:
   - Change "Current phase" to the new round
   - Update the Products table with new products, limits, and strategy stubs
   - Update Data Findings Summary section
   - Update backtest command round number
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
   - Pattern reference in "Adding a New Product"
6. Update `.claude/agents/orchestrator.md` — Round N position limits in Hard Constraints
7. Update `.claude/agents/reviewer.md` — position limit values in checklist
8. Update `.claude/skills/backtest.md` — round number and product list
9. Update `.claude/GUIDE.md` — Round product summary table and backtest command
10. Update `dashboard/README.md` — add new round selectors and data file paths
11. Remind: run researcher agent on new round CSVs before touching trader.py
12. Remind: trader.py must REMOVE previous round products — do not carry them over
