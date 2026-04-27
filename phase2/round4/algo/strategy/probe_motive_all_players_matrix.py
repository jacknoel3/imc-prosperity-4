from __future__ import annotations

"""
Global motivation matrix probe for all Round 4 players.

Why we are testing this:
- Single-player probes are clean but can miss interaction effects.
- This matrix probe samples all key products with tiny passive quotes and logs
  every player trigger in a consistent format.

How this probe works:
- Quotes tiny, low-risk passive orders across HGP, VE, and core vouchers.
- Rotates quote offsets and sizes to estimate price/size sensitivity.
- Does not attempt to maximize PnL. It is a broad behavioral sampler.

What the logs can reveal:
- Which players fill us at tight vs wide offsets.
- Which players are size-sensitive.
- Which products attract each player when all products are available at once.
- Whether player behavior changes when multiple other bots are active.
"""

import json
from typing import Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ModuleNotFoundError:
    from prosperity3bt.datamodel import Order, OrderDepth, TradingState


class Trader:
    PRODUCTS = ["HYDROGEL_PACK", "VELVETFRUIT_EXTRACT", "VEV_4000", "VEV_4500", "VEV_5000", "VEV_5100", "VEV_5200", "VEV_5300", "VEV_5400"]
    LIMITS = {
        "HYDROGEL_PACK": 200, "VELVETFRUIT_EXTRACT": 200,
        "VEV_4000": 300, "VEV_4500": 300, "VEV_5000": 300, "VEV_5100": 300,
        "VEV_5200": 300, "VEV_5300": 300, "VEV_5400": 300,
    }
    MAX_POS = {
        "HYDROGEL_PACK": 30, "VELVETFRUIT_EXTRACT": 30,
        "VEV_4000": 35, "VEV_4500": 35, "VEV_5000": 35, "VEV_5100": 35,
        "VEV_5200": 35, "VEV_5300": 35, "VEV_5400": 35,
    }

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        timestamp = int(getattr(state, "timestamp", 0))
        mode = ["OFFSET_0_SIZE_1", "OFFSET_1_SIZE_1", "OFFSET_1_SIZE_3", "OFFSET_2_SIZE_2"][(timestamp // 3000) % 4]
        offset = 0 if "OFFSET_0" in mode else (2 if "OFFSET_2" in mode else 1)
        qty = 3 if "SIZE_3" in mode else (2 if "SIZE_2" in mode else 1)
        notes: List[str] = []

        for product in self.PRODUCTS:
            bid, ask = self._best_bid_ask(state.order_depths.get(product))
            if bid is None or ask is None:
                continue
            pos = int(state.position.get(product, 0))
            orders = self._orders(product, bid, ask, pos, qty, offset)
            if orders:
                result[product].extend(orders)
                notes.append(product + ":" + ",".join(str(o) for o in orders))

        fills = self._own_fill_notes(state)
        players = self._market_notes(state)
        if fills or timestamp % 3000 == 0:
            print(f"MOTIVE_ALL_PLAYERS_MATRIX|mode={mode}|t={timestamp}|players={';'.join(players[:12])}|fills={';'.join(fills[:12])}|orders={';'.join(notes[:10])}")
        return result, 0, json.dumps({"probe": "MOTIVE_ALL_PLAYERS_MATRIX", "mode": mode, "t": timestamp}, separators=(",", ":"))

    def _orders(self, product: str, bid: int, ask: int, pos: int, qty: int, offset: int) -> List[Order]:
        if abs(pos) >= int(0.85 * self.MAX_POS[product]):
            return self._flatten(product, bid, ask, pos, 6)
        orders: List[Order] = []
        buy_px = bid if offset == 0 else min(ask - 1, bid + offset)
        sell_px = ask if offset == 0 else max(bid + 1, ask - offset)
        bq = min(qty, self._buy_capacity(product, pos))
        sq = min(qty, self._sell_capacity(product, pos))
        if bq > 0 and buy_px < ask:
            orders.append(Order(product, buy_px, bq))
        if sq > 0 and sell_px > bid:
            orders.append(Order(product, sell_px, -sq))
        return orders

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

    def _own_fill_notes(self, state: TradingState) -> List[str]:
        notes: List[str] = []
        for product in self.PRODUCTS:
            for trade in state.own_trades.get(product, []):
                notes.append(f"{product}:{getattr(trade, 'buyer', '')}>{getattr(trade, 'seller', '')}@{getattr(trade, 'price', '')}x{getattr(trade, 'quantity', '')}")
        return notes

    def _market_notes(self, state: TradingState) -> List[str]:
        notes: List[str] = []
        for product in self.PRODUCTS:
            for trade in state.market_trades.get(product, []):
                notes.append(f"{product}:{getattr(trade, 'buyer', '')}>{getattr(trade, 'seller', '')}@{getattr(trade, 'price', '')}x{getattr(trade, 'quantity', '')}")
        return notes
