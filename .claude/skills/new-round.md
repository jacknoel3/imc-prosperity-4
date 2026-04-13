Scaffold the transition to a new competition round.

Steps:
1. Ask which round number and what new products were announced
2. Update CLAUDE.md:
   - Change "Current phase" to the new round
   - Add new products to the Products table with TBD limits and strategy
   - Clear previous round's Data Findings section (keep structure)
3. Create rules/products/<new_product>.md with empty findings template:
   ```
   ## <PRODUCT>
   - Fair value: TBD
   - Position limit: TBD
   - Spread: TBD
   - Tick volatility: TBD
   ### Strategy
   - TBD — run researcher agent on new data first
   ```
4. Update data/ paths in researcher.md to point to new round CSVs
5. Remind: run researcher agent on new data before touching trader.py
