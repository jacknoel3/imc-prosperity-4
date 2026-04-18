# Algorithmic Trading — Code Structure Reference

Round-neutral reference for how the `Trader` class works on Prosperity's exchange.

---

## The `Trader` Class

Single file, single class. The exchange calls `run()` every iteration.

```python
from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List

class Trader:

    def bid(self):
        # Round 2 only: Market Access Fee bid (XIRECs).
        # Top 50% of bids get 25% extra market flow; bid is subtracted from R2 profits.
        # Ignored in all other rounds — safe to include always.
        return 15

    def run(self, state: TradingState):
        result = {}

        for product in state.order_depths:
            order_depth: OrderDepth = state.order_depths[product]
            orders: List[Order] = []
            acceptable_price = 10  # compute this per product

            if len(order_depth.sell_orders) != 0:
                best_ask, best_ask_amount = list(order_depth.sell_orders.items())[0]
                if int(best_ask) < acceptable_price:
                    orders.append(Order(product, best_ask, -best_ask_amount))

            if len(order_depth.buy_orders) != 0:
                best_bid, best_bid_amount = list(order_depth.buy_orders.items())[0]
                if int(best_bid) > acceptable_price:
                    orders.append(Order(product, best_bid, -best_bid_amount))

            result[product] = orders

        traderData = ""   # serialise state here (jsonpickle); max 50,000 chars
        conversions = 0
        return result, conversions, traderData
```

---

## Simulation Mechanics

| Property | Value |
|----------|-------|
| Iterations (testing) | 1,000 |
| Iterations (final sim) | 10,000 |
| Time per `run()` call | ≤ 900 ms (avg ≤ 100 ms) |
| traderData max size | 50,000 characters (truncated beyond this → crash) |
| Runtime | AWS Lambda (stateless — no class/global vars persist between calls) |

---

## `TradingState` — Key Fields

```python
class TradingState:
    traderData: str                          # your persisted state from last tick
    timestamp: int                           # current tick (increments by 100)
    order_depths: Dict[Symbol, OrderDepth]   # bot quotes you can trade against
    own_trades:   Dict[Symbol, List[Trade]]  # your fills since last tick
    market_trades: Dict[Symbol, List[Trade]] # other participants' fills since last tick
    position:     Dict[Product, int]         # your current position per product
    observations: Observation                # plainValueObservations + conversionObservations
```

---

## `OrderDepth` — The Order Book

```python
class OrderDepth:
    buy_orders:  Dict[int, int]   # price → +qty  (bids)
    sell_orders: Dict[int, int]   # price → -qty  (asks, quantities are NEGATIVE)
```

**Example:**
```python
buy_orders  = {10: 7, 9: 5}     # 7 units bid at 10, 5 units bid at 9
sell_orders = {11: -4, 12: -8}  # 4 units offered at 11, 8 units offered at 12
```

---

## `Order` — Sending Orders

```python
Order(symbol, price, quantity)
# quantity > 0 → BUY order
# quantity < 0 → SELL order
```

- Crosses the book immediately if price matches a bot quote
- Remaining unfilled quantity is visible to bots for the rest of the tick
- If no bot trades against the remainder → **auto-cancelled before next tick**
- Orders do NOT persist across ticks

---

## Position Limits

- Exchange enforces the limit on the **aggregated quantity** of all orders on a side per tick
- If `sum(all_buy_qtys) > limit - pos` → **entire buy side rejected** (not just the excess)
- Same logic applies to sells: `sum(all_sell_qtys) > limit + pos` → entire sell side rejected

**Example:** limit = 30, pos = -5 → max aggregated buy qty = 35 (legal); 36+ → rejected

**Pattern to stay safe:**
```python
buy_capacity  = limit - pos   # max you can still buy
sell_capacity = limit + pos   # max you can still sell
# cap every passive order to remaining capacity after accounting for aggressive fills
```

---

## `Trade` — Fill Records

```python
class Trade:
    symbol:    str
    price:     int
    quantity:  int
    buyer:     str   # "SUBMISSION" if you bought, "" otherwise
    seller:    str   # "SUBMISSION" if you sold, "" otherwise
    timestamp: int
```

Counterparty IDs are only revealed when you are the counterparty.

---

## Persisting State with `traderData`

AWS Lambda is stateless — class variables reset each tick. Use `traderData` to carry state:

```python
import jsonpickle

# Save
state_dict = {"ema": 10432.5, "trend": 1.2}
traderData = jsonpickle.encode(state_dict)

# Load
try:
    ts = jsonpickle.decode(state.traderData)
except:
    ts = {}   # first tick fallback
```

**Hard limit: 50,000 characters.** Store only scalars or fixed-size dicts. Never append price history.

---

## Conversions (optional)

Return an integer as `conversions` to convert a position via `ConversionObservation` prices.

- Requires an existing long or short position
- Cannot exceed current position size
- Costs: transport fees + import/export tariff
- Send `0` or `None` to skip

---

## Supported Libraries

Standard Python 3.12 + the following external libs:

`pandas` · `numpy` · `statistics` · `math` · `typing` · `jsonpickle`

No other external imports are allowed.

---

## Common Mistakes

| Mistake | Consequence |
|---------|-------------|
| `print()` in submitted code | Lambda timeout → 0 PnL |
| traderData > 50,000 chars | Framework truncates → container crash → 0 PnL |
| Aggregated order qty > remaining capacity | Entire side rejected by exchange |
| Hardcoding historical prices without fallback | Breaks on unseen data |
| Carrying prior-round products into new trader.py | Backtest errors or 0 PnL |
| `bid()` returning 0 in Round 2 | No extra market access |
