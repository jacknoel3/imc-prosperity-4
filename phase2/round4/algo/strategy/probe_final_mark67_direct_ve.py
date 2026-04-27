from __future__ import annotations

"""
Final direct probe for Mark 67 in VELVETFRUIT_EXTRACT.

Question to answer from logs:
- Can SUBMISSION directly observe Mark 67 as an informed VE buyer, and is the
  markout positive after Mark 67 buys from us?

Test design:
- Spend most time offering passive asks at/near the inside to bait Mark 67 buy
  fills directly against SUBMISSION.
- Use smaller bid-bait windows to separate Mark 55/49 liquidity sellers from
  true Mark 67 directional demand.
- If Mark 67 buys from us, briefly follow the signal with small buys so logs
  can measure whether FOLLOW_MARK67_BUY has positive markout.
- Logs are tagged with MARK67_DIRECT_FINAL_PROBE.
"""

import json
from typing import Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ModuleNotFoundError:
    from prosperity3bt.datamodel import Order, OrderDepth, TradingState


class Trader:
    PRODUCT = "VELVETFRUIT_EXTRACT"
    LIMIT = 200
    MAX_POS = 65
    FOLLOW_WINDOW = 7000

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        timestamp = int(getattr(state, "timestamp", 0))
        memory = self._read_memory(getattr(state, "traderData", ""))
        self._update_memory(memory, state, timestamp)

        depth = state.order_depths.get(self.PRODUCT)
        bid, ask = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return result, 0, json.dumps(memory, separators=(",", ":"))

        position = int(state.position.get(self.PRODUCT, 0))
        mode = self._mode(timestamp, memory)
        qty = 3 + ((timestamp // 1000) % 4)
        if abs(position) >= int(0.86 * self.MAX_POS):
            mode = "CONTROLLED_FLATTEN"
            orders = self._flatten(bid, ask, position, qty * 2)
        elif mode == "FOLLOW_MARK67_BUY":
            orders = self._follow_buy(bid, ask, position, max(1, qty // 2))
        elif mode == "ASK_BAIT_INSIDE":
            orders = self._sell_bait(bid, ask, position, qty, offset=1)
        elif mode == "ASK_BAIT_JOIN":
            orders = self._sell_bait_join(bid, ask, position, qty * 2)
        elif mode == "ASK_BAIT_DEEPER":
            orders = self._sell_bait(bid, ask, position, max(1, qty // 2), offset=2)
        else:
            orders = self._buy_bait_for_sellers(bid, ask, position, max(1, qty // 2))

        if orders:
            result[self.PRODUCT].extend(orders)

        fills = self._own_fill_notes(state)
        if fills or timestamp % 3000 == 0:
            print(
                f"MARK67_DIRECT_FINAL_PROBE|mode={mode}|t={timestamp}|pos={position}|bbo={bid}/{ask}"
                + f"|memory={memory}|fills={';'.join(fills[:10])}|orders={','.join(str(o) for o in orders)}"
            )

        memory["mode"] = mode
        memory["t"] = timestamp
        return result, 0, json.dumps(memory, separators=(",", ":"))

    def _mode(self, timestamp: int, memory: dict) -> str:
        if int(memory.get("mark67_buy_expires", -1)) >= timestamp:
            return "FOLLOW_MARK67_BUY"
        phase = (timestamp // 3000) % 5
        return ["ASK_BAIT_INSIDE", "ASK_BAIT_JOIN", "ASK_BAIT_DEEPER", "ASK_BAIT_INSIDE", "BID_BAIT_SELLERS"][phase]

    def _update_memory(self, memory: dict, state: TradingState, timestamp: int) -> None:
        mark67_buy_qty = int(memory.get("mark67_bought_from_us_qty", 0))
        mark55_sell_qty = int(memory.get("mark55_sold_to_us_qty", 0))
        for trade in state.own_trades.get(self.PRODUCT, []):
            buyer = getattr(trade, "buyer", "")
            seller = getattr(trade, "seller", "")
            qty = int(getattr(trade, "quantity", 0))
            if buyer == "Mark 67" and seller == "SUBMISSION":
                mark67_buy_qty += qty
                memory["mark67_buy_expires"] = timestamp + self.FOLLOW_WINDOW
                memory["last_mark67_buy_ts"] = timestamp
            elif buyer == "SUBMISSION" and seller == "Mark 55":
                mark55_sell_qty += qty
                memory["last_mark55_sell_ts"] = timestamp
        memory["mark67_bought_from_us_qty"] = mark67_buy_qty
        memory["mark55_sold_to_us_qty"] = mark55_sell_qty

    def _sell_bait(self, bid: int, ask: int, position: int, qty: int, offset: int) -> List[Order]:
        sell_qty = min(qty, self._sell_capacity(position))
        if ask - bid >= offset + 2:
            price = max(bid + 1, ask - offset)
        else:
            price = ask
        return [Order(self.PRODUCT, price, -sell_qty)] if sell_qty > 0 and price > bid else []

    def _sell_bait_join(self, bid: int, ask: int, position: int, qty: int) -> List[Order]:
        sell_qty = min(qty, self._sell_capacity(position))
        return [Order(self.PRODUCT, ask, -sell_qty)] if sell_qty > 0 else []

    def _buy_bait_for_sellers(self, bid: int, ask: int, position: int, qty: int) -> List[Order]:
        buy_qty = min(qty, self._buy_capacity(position))
        price = min(ask - 1, bid + 1) if ask - bid >= 3 else bid
        return [Order(self.PRODUCT, price, buy_qty)] if buy_qty > 0 and price < ask else []

    def _follow_buy(self, bid: int, ask: int, position: int, qty: int) -> List[Order]:
        buy_qty = min(qty, self._buy_capacity(position))
        return [Order(self.PRODUCT, ask, buy_qty)] if buy_qty > 0 else []

    def _flatten(self, bid: int, ask: int, position: int, qty: int) -> List[Order]:
        if position > 0:
            return [Order(self.PRODUCT, bid, -min(position, qty))]
        if position < 0:
            return [Order(self.PRODUCT, ask, min(-position, qty))]
        return []

    def _buy_capacity(self, position: int) -> int:
        return max(0, min(self.LIMIT - position, self.MAX_POS - position))

    def _sell_capacity(self, position: int) -> int:
        return max(0, min(self.LIMIT + position, self.MAX_POS + position))

    def _read_memory(self, trader_data: str) -> dict:
        try:
            parsed = json.loads(trader_data) if trader_data else {}
        except Exception:
            parsed = {}
        return parsed if isinstance(parsed, dict) else {}

    def _best_bid_ask(self, depth: Optional[OrderDepth]) -> Tuple[Optional[int], Optional[int]]:
        if depth is None or not depth.buy_orders or not depth.sell_orders:
            return None, None
        return max(depth.buy_orders), min(depth.sell_orders)

    def _own_fill_notes(self, state: TradingState) -> List[str]:
        notes: List[str] = []
        for trade in state.own_trades.get(self.PRODUCT, []):
            notes.append(
                f"{getattr(trade, 'buyer', '')}>{getattr(trade, 'seller', '')}"
                + f"@{getattr(trade, 'price', '')}x{getattr(trade, 'quantity', '')}"
            )
        return notes
