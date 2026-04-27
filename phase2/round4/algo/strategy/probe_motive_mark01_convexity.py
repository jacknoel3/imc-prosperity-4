from __future__ import annotations

"""
Motivation probe for Mark 01: informed convexity buyer vs passive voucher demand.

Why we are testing this:
- Mark 01 repeatedly buys voucher convexity, especially from Mark 22.
- We need to know whether Mark 01 is simply a structural buyer or whether its
  buys identify future positive voucher/VE movement.

How this probe works:
- Posts tiny passive asks to let Mark 01 buy vouchers/VE from SUBMISSION.
- Tracks Mark 01 market buys, especially Mark 01 buying from Mark 22.
- Alternates among ask bait, same-strike follow, adjacent-strike follow, and
  VE follow after Mark 01 voucher buying.

What the logs can reveal:
- Same-strike follow profitability implies informed directional/convexity flow.
- Adjacent-strike profitability implies Mark 01 is expressing strip-level view.
- VE follow profitability implies Mark 01 voucher flow predicts underlying VE.
"""

import json
from typing import Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ModuleNotFoundError:
    from prosperity3bt.datamodel import Order, OrderDepth, TradingState


class Trader:
    VE = "VELVETFRUIT_EXTRACT"
    VOUCHERS = ["VEV_5000", "VEV_5100", "VEV_5200", "VEV_5300", "VEV_5400", "VEV_5500", "VEV_6000", "VEV_6500"]
    PRODUCTS = [VE] + VOUCHERS
    LIMITS = {
        "VELVETFRUIT_EXTRACT": 200,
        "VEV_5000": 300, "VEV_5100": 300, "VEV_5200": 300, "VEV_5300": 300,
        "VEV_5400": 300, "VEV_5500": 300, "VEV_6000": 300, "VEV_6500": 300,
    }
    MAX_POS = {
        "VELVETFRUIT_EXTRACT": 35,
        "VEV_5000": 45, "VEV_5100": 45, "VEV_5200": 45, "VEV_5300": 45,
        "VEV_5400": 45, "VEV_5500": 45, "VEV_6000": 45, "VEV_6500": 45,
    }
    WINDOW = 8000

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        timestamp = int(getattr(state, "timestamp", 0))
        memory = self._read_memory(getattr(state, "traderData", ""))
        self._update_triggers(state, memory, timestamp)
        mode = ["ASK_BAIT_MARK01", "SAME_STRIKE_FOLLOW", "ADJACENT_STRIKE_FOLLOW", "VE_FOLLOW_CONTROL"][(timestamp // 4000) % 4]
        notes: List[str] = []

        for product in self.PRODUCTS:
            bid, ask = self._best_bid_ask(state.order_depths.get(product))
            if bid is None or ask is None:
                continue
            pos = int(state.position.get(product, 0))
            orders = self._orders(product, bid, ask, pos, mode, memory, timestamp)
            if orders:
                result[product].extend(orders)
                notes.append(product + ":" + ",".join(str(o) for o in orders))

        fills = self._own_fill_notes(state)
        if fills or timestamp % 4000 == 0:
            print(f"MOTIVE_MARK01_CONVEXITY|mode={mode}|t={timestamp}|mem={memory}|fills={';'.join(fills[:10])}|orders={';'.join(notes[:8])}")
        memory["mode"] = mode
        return result, 0, json.dumps(memory, separators=(",", ":"))

    def _orders(self, product: str, bid: int, ask: int, pos: int, mode: str, memory: dict, timestamp: int) -> List[Order]:
        if abs(pos) >= int(0.85 * self.MAX_POS[product]):
            return self._flatten(product, bid, ask, pos, 8)
        if mode == "ASK_BAIT_MARK01":
            return self._sell_bait(product, bid, ask, pos, 2)
        if mode == "SAME_STRIKE_FOLLOW" and self._active(memory, product, timestamp):
            return self._buy(product, ask, pos, 2)
        if mode == "ADJACENT_STRIKE_FOLLOW" and product in self.VOUCHERS and self._adjacent_active(memory, product, timestamp):
            return self._buy(product, ask, pos, 1)
        if mode == "VE_FOLLOW_CONTROL" and product == self.VE and self._any_voucher_active(memory, timestamp):
            return self._buy(product, ask, pos, 2)
        return []

    def _update_triggers(self, state: TradingState, memory: dict, timestamp: int) -> None:
        trig = memory.setdefault("mark01_buy", {})
        for book_name in ("own_trades", "market_trades"):
            for product in self.PRODUCTS:
                for trade in getattr(state, book_name, {}).get(product, []):
                    if getattr(trade, "buyer", "") == "Mark 01":
                        seller = getattr(trade, "seller", "")
                        trig[product] = {"expires": timestamp + self.WINDOW, "seller": seller}

    def _active(self, memory: dict, product: str, timestamp: int) -> bool:
        return int(memory.get("mark01_buy", {}).get(product, {}).get("expires", -1)) >= timestamp

    def _adjacent_active(self, memory: dict, product: str, timestamp: int) -> bool:
        i = self.VOUCHERS.index(product)
        for j in (i - 1, i + 1):
            if 0 <= j < len(self.VOUCHERS) and self._active(memory, self.VOUCHERS[j], timestamp):
                return True
        return False

    def _any_voucher_active(self, memory: dict, timestamp: int) -> bool:
        return any(self._active(memory, p, timestamp) for p in self.VOUCHERS)

    def _sell_bait(self, product: str, bid: int, ask: int, pos: int, qty: int) -> List[Order]:
        q = min(qty, self._sell_capacity(product, pos))
        px = max(bid + 1, ask - 1) if ask - bid >= 3 else ask
        return [Order(product, px, -q)] if q > 0 and px > bid else []

    def _buy(self, product: str, ask: int, pos: int, qty: int) -> List[Order]:
        q = min(qty, self._buy_capacity(product, pos))
        return [Order(product, ask, q)] if q > 0 else []

    def _flatten(self, product: str, bid: int, ask: int, pos: int, qty: int) -> List[Order]:
        if pos > 0:
            return [Order(product, bid, -min(pos, qty))]
        if pos < 0:
            return [Order(product, ask, min(-pos, qty))]
        return []

    def _buy_capacity(self, product: str, pos: int) -> int:
        return max(0, min(self.LIMITS[product] - pos, self.MAX_POS[product] - pos))

    def _sell_capacity(self, product: str, pos: int) -> int:
        return max(0, min(self.LIMITS[product] + pos, self.MAX_POS[product] + pos))

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
        notes: List[str] = []
        for product in self.PRODUCTS:
            for trade in state.own_trades.get(product, []):
                notes.append(f"{product}:{getattr(trade, 'buyer', '')}>{getattr(trade, 'seller', '')}@{getattr(trade, 'price', '')}x{getattr(trade, 'quantity', '')}")
        return notes
