## Submission Hard Constraints

### Class / Method
- Class `Trader`, method `def run(self, state: TradingState)` → returns `tuple[dict[str, list[Order]], int, str]`
- **`bid()` method**: include `def bid(self): return <price>` in every submission — required for Round 2, silently ignored in all other rounds. Safe to include always.
- Imports: stdlib + `datamodel` only (`jsonpickle` is also available for traderData serialization)
- Zero `print()` calls

### Position Limit Enforcement (exchange-side)
- The exchange checks the **total aggregated quantity** of all buy (sell) orders submitted in a single iteration
- If `sum(all_buy_qtys) > LIMIT - current_pos` **or** `sum(all_sell_qtys) > LIMIT + current_pos`, **all orders for that side are rejected** — not just the excess, the entire batch
- Correct pattern: track `pos` locally after each aggressive fill, then cap passive order at `LIMIT - pos` (buy) or `LIMIT + pos` (sell). This ensures the total never exceeds capacity.
- Example from official docs: limit=30, pos=-5 → aggregated buy >35 rejects all buys; buy qty=35 is legal

### traderData State
- JSON in `traderData` (3rd return value), always wrap deserialization in try/except
- Hard cap: **50,000 characters** — external framework truncates at this limit → container crash → silent 0 PnL
- Never grow unboundedly (no appending price history). Store only scalars or fixed-size dicts.

### Order Lifecycle
- Orders that cross the book fill immediately against bot quotes in the same iteration
- Remaining passive quantity is visible to bots; if no bot trades against it, the order is **cancelled before the next iteration** — orders do not persist across ticks

### Other
- Last upload before deadline is locked in
