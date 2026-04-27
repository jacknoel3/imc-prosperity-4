from __future__ import annotations

"""
Motivation probe for Mark 55: VE liquidity/noise vs directional flow.

Why we are testing this:
- Mark 55 repeatedly loses against Mark 01/14 and has been profitable for
  SUBMISSION to trade against in VE.
- We need to know whether Mark 55 is simply a noisy liquidity taker/provider or
  whether its behavior changes around informed Mark 01/14 activity.

How this probe works:
- Quotes VE with rotating offset and size.
- Tracks Mark55 direct fills and recent Mark01/14 VE activity.
- Alternates between symmetric liquidity provision, fade Mark55, and conditional
  fade only after Mark01/14 activity.

What the logs can reveal:
- If Mark55 is profitable in all modes, it is broadly noisy/liquidity-driven.
- If Mark55 is profitable only after Mark01/14 activity, it is being selected
  by stronger players and should be faded contextually.
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
    MAX_POS = 55
    WINDOW = 7000

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        timestamp = int(getattr(state, "timestamp", 0))
        memory = self._read_memory(getattr(state, "traderData", ""))
        self._update_triggers(state, memory, timestamp)
        bid, ask = self._best_bid_ask(state.order_depths.get(self.PRODUCT))
        if bid is None or ask is None:
            return result, 0, json.dumps(memory, separators=(",", ":"))
        pos = int(state.position.get(self.PRODUCT, 0))
        informed_recent = int(memory.get("informed_ve_expires", -1)) >= timestamp
        mode = ["SYMMETRIC_OFFSET_1", "SYMMETRIC_OFFSET_2", "FADE_MARK55", "CONTEXT_FADE_MARK55"][(timestamp // 3500) % 4]
        orders = self._orders(bid, ask, pos, mode, memory, timestamp, informed_recent)
        if orders:
            result[self.PRODUCT].extend(orders)
        fills = self._own_fill_notes(state)
        if fills or timestamp % 3500 == 0:
            print(f"MOTIVE_MARK55_LIQUIDITY|mode={mode}|informed_recent={informed_recent}|t={timestamp}|mem={memory}|fills={';'.join(fills[:10])}|orders={','.join(str(o) for o in orders)}")
        memory["mode"] = mode
        return result, 0, json.dumps(memory, separators=(",", ":"))

    def _orders(self, bid: int, ask: int, pos: int, mode: str, memory: dict, timestamp: int, informed_recent: bool) -> List[Order]:
        if abs(pos) >= int(0.85 * self.MAX_POS):
            return self._flatten(bid, ask, pos, 10)
        if mode.startswith("SYMMETRIC"):
            offset = 1 if mode.endswith("_1") else 2
            return self._two_sided(bid, ask, pos, 4, offset)
        trig = memory.get("mark55", {})
        active = int(trig.get("expires", -1)) >= timestamp
        if mode == "CONTEXT_FADE_MARK55" and not informed_recent:
            return self._two_sided(bid, ask, pos, 2, 2)
        if active:
            if trig.get("direction") == "BUY":
                return self._sell(bid, pos, 4)
            if trig.get("direction") == "SELL":
                return self._buy(ask, pos, 4)
        return self._two_sided(bid, ask, pos, 2, 1)

    def _update_triggers(self, state: TradingState, memory: dict, timestamp: int) -> None:
        for trade in state.market_trades.get(self.PRODUCT, []) + state.own_trades.get(self.PRODUCT, []):
            buyer = getattr(trade, "buyer", "")
            seller = getattr(trade, "seller", "")
            if buyer == "Mark 55":
                memory["mark55"] = {"direction": "BUY", "expires": timestamp + self.WINDOW}
            elif seller == "Mark 55":
                memory["mark55"] = {"direction": "SELL", "expires": timestamp + self.WINDOW}
            if buyer in {"Mark 01", "Mark 14"} or seller in {"Mark 01", "Mark 14"}:
                memory["informed_ve_expires"] = timestamp + self.WINDOW

    def _two_sided(self, bid: int, ask: int, pos: int, qty: int, offset: int) -> List[Order]:
        orders: List[Order] = []
        buy_px = min(ask - 1, bid + offset) if ask - bid >= offset + 2 else bid
        sell_px = max(bid + 1, ask - offset) if ask - bid >= offset + 2 else ask
        bq = min(qty, self._buy_capacity(pos))
        sq = min(qty, self._sell_capacity(pos))
        if bq > 0 and buy_px < ask:
            orders.append(Order(self.PRODUCT, buy_px, bq))
        if sq > 0 and sell_px > bid:
            orders.append(Order(self.PRODUCT, sell_px, -sq))
        return orders

    def _buy(self, ask: int, pos: int, qty: int) -> List[Order]:
        q = min(qty, self._buy_capacity(pos))
        return [Order(self.PRODUCT, ask, q)] if q > 0 else []

    def _sell(self, bid: int, pos: int, qty: int) -> List[Order]:
        q = min(qty, self._sell_capacity(pos))
        return [Order(self.PRODUCT, bid, -q)] if q > 0 else []

    def _flatten(self, bid: int, ask: int, pos: int, qty: int) -> List[Order]:
        if pos > 0:
            return [Order(self.PRODUCT, bid, -min(pos, qty))]
        if pos < 0:
            return [Order(self.PRODUCT, ask, min(-pos, qty))]
        return []

    def _buy_capacity(self, pos: int) -> int:
        return max(0, min(self.LIMIT - pos, self.MAX_POS - pos))

    def _sell_capacity(self, pos: int) -> int:
        return max(0, min(self.LIMIT + pos, self.MAX_POS + pos))

    def _best_bid_ask(self, depth: Optional[OrderDepth]) -> Tuple[Optional[int], Optional[int]]:
        if depth is None or not depth.buy_orders or not depth.sell_orders:
            return None, None
        return max(depth.buy_orders), min(depth.sell_orders)

    def _read_memory(self, trader_data: str) -> dict:
        try:
            parsed = json.loads(trader_data) if trader_data else {}
        except Exception:
            parsed = {}
        return parsed if isinstance(parsed, dict) else {}

    def _own_fill_notes(self, state: TradingState) -> List[str]:
        return [f"{getattr(t,'buyer','')}>{getattr(t,'seller','')}@{getattr(t,'price','')}x{getattr(t,'quantity','')}" for t in state.own_trades.get(self.PRODUCT, [])]
