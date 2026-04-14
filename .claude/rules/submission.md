## Submission Hard Constraints
- Class `Trader`, method `def run(self, state: TradingState)` → returns `tuple[dict[str, list[Order]], int, str]`
- Imports: stdlib + `datamodel` only
- Zero `print()` calls
- Cross-tick state: JSON in `traderData` (3rd return value), always wrap deserialization in try/except
- `traderData` must stay small — never grow it unboundedly (e.g. appending price history). Store only scalars or fixed-size dicts. Unbounded growth → Lambda timeout → silent 0 PnL
- Last upload before deadline is locked in
