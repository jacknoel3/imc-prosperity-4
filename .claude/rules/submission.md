## Submission Hard Constraints
- Class `Trader`, method `def run(self, state: TradingState)` → returns `tuple[dict[str, list[Order]], int, str]`
- Imports: stdlib + `datamodel` only
- Zero `print()` calls
- Cross-tick state: JSON in `traderData` (3rd return value), always wrap deserialization in try/except
- Last upload before deadline is locked in
