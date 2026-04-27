from __future__ import annotations

"""
Effective-vacuum / thin-book probe.

Why we are testing this:
- The previous vacuum probe was intentionally strict: it only traded when the
  best bid or the best ask was missing. The new logs show that this exact
  condition never happened: every product always had both sides of the book.
- That does not kill the Round 1-style idea completely. It only tells us that
  the Round 4 opportunity, if it exists, is not a literal empty-book vacuum.
  The remaining testable version is an "effective vacuum": the book is present,
  but the spread is wide, the top level is thin, the depth behind it is sparse,
  or one side is so dominant that a small quote from SUBMISSION can become the
  best visible liquidity.

How this probe works:
- It posts small, controlled orders only when the book is fragile:
  * WIDE_SPREAD: quote one tick inside a wide spread.
  * THIN_BID: become the best bid when the current best bid has little size.
  * THIN_ASK: become the best ask when the current best ask has little size.
  * IMBALANCE_BUY / IMBALANCE_SELL: test whether an extreme book imbalance
    predicts bots crossing into our quote.
  * SPARSE_DEPTH: test books where level 2 or level 3 is missing on either side.
- The mode rotates through time buckets so that the same market state is tested
  with different quote styles instead of one hard-coded behavior.
- Sizes are deliberately small. This probe is for information, not PnL.

What the logs can reveal:
- Whether bots trade against a quote that becomes best bid/best ask inside a
  wide or thin book.
- Which products actually support a vacuum-like execution edge.
- Whether the edge is side-specific: sellers hitting our bid, buyers lifting
  our ask, or both.
- Which price offset is too passive, too aggressive, or toxic after markout.
- Whether "vacuum" is mainly about price level, visible size, spread width,
  imbalance, or time/product-specific bot behavior.
"""

import json
from typing import Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ModuleNotFoundError:
    from prosperity3bt.datamodel import Order, OrderDepth, TradingState


class Trader:
    PRODUCTS = [
        "HYDROGEL_PACK",
        "VELVETFRUIT_EXTRACT",
        "VEV_4000",
        "VEV_4500",
        "VEV_5000",
        "VEV_5100",
        "VEV_5200",
        "VEV_5300",
        "VEV_5400",
        "VEV_5500",
        "VEV_6000",
        "VEV_6500",
    ]

    LIMITS = {
        "HYDROGEL_PACK": 200,
        "VELVETFRUIT_EXTRACT": 200,
        "VEV_4000": 300,
        "VEV_4500": 300,
        "VEV_5000": 300,
        "VEV_5100": 300,
        "VEV_5200": 300,
        "VEV_5300": 300,
        "VEV_5400": 300,
        "VEV_5500": 300,
        "VEV_6000": 300,
        "VEV_6500": 300,
    }

    # Conservative static anchors. They are not meant to be perfect fair values;
    # they are guardrails that keep the probe from paying obviously silly prices.
    FAIR = {
        "HYDROGEL_PACK": 10000,
        "VELVETFRUIT_EXTRACT": 5262,
        "VEV_4000": 1265,
        "VEV_4500": 764,
        "VEV_5000": 266,
        "VEV_5100": 175,
        "VEV_5200": 101,
        "VEV_5300": 50,
        "VEV_5400": 16,
        "VEV_5500": 6,
        "VEV_6000": 0,
        "VEV_6500": 0,
    }

    WIDE_SPREAD = {
        "HYDROGEL_PACK": 8,
        "VELVETFRUIT_EXTRACT": 3,
        "VEV_4000": 10,
        "VEV_4500": 8,
        "VEV_5000": 4,
        "VEV_5100": 4,
        "VEV_5200": 3,
        "VEV_5300": 2,
        "VEV_5400": 2,
        "VEV_5500": 2,
        "VEV_6000": 1,
        "VEV_6500": 1,
    }

    THIN_TOP = {
        "HYDROGEL_PACK": 5,
        "VELVETFRUIT_EXTRACT": 5,
        "VEV_4000": 3,
        "VEV_4500": 3,
        "VEV_5000": 5,
        "VEV_5100": 6,
        "VEV_5200": 6,
        "VEV_5300": 8,
        "VEV_5400": 8,
        "VEV_5500": 6,
        "VEV_6000": 6,
        "VEV_6500": 6,
    }

    IMBALANCE_THRESHOLD = 0.70

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        timestamp = int(getattr(state, "timestamp", 0))
        notes: List[str] = []
        order_notes: List[str] = []

        for product in self.PRODUCTS:
            depth = state.order_depths.get(product)
            if depth is None:
                continue
            snapshot = self._snapshot(depth)
            if snapshot is None:
                continue

            mode = self._select_mode(timestamp, product)
            orders, reason = self._orders_for_mode(product, depth, snapshot, state.position.get(product, 0), mode)
            if reason:
                notes.append(reason)
            if orders:
                result[product].extend(orders)
                order_notes.append(product + ":" + mode + ":" + ",".join(str(order) for order in orders))

        fills = self._own_fill_notes(state)
        if order_notes or fills or timestamp % 5000 == 0:
            print(
                "EFFECTIVE_VACUUM_PROBE"
                + f"|t={timestamp}"
                + f"|signals={';'.join(notes[:20])}"
                + f"|fills={';'.join(fills[:20])}"
                + f"|orders={';'.join(order_notes[:20])}"
            )

        payload = {"probe": "EFFECTIVE_VACUUM_THIN_BOOK", "t": timestamp, "signals": notes[:30]}
        return result, 0, json.dumps(payload, separators=(",", ":"))

    def _snapshot(self, depth: OrderDepth) -> Optional[Dict[str, float]]:
        if not depth.buy_orders or not depth.sell_orders:
            return None
        bid_prices = sorted(depth.buy_orders.keys(), reverse=True)
        ask_prices = sorted(depth.sell_orders.keys())
        best_bid = int(bid_prices[0])
        best_ask = int(ask_prices[0])
        if best_bid >= best_ask:
            return None
        bid_vol = sum(max(0, int(v)) for v in depth.buy_orders.values())
        ask_vol = sum(max(0, -int(v)) for v in depth.sell_orders.values())
        top_bid_vol = max(0, int(depth.buy_orders[best_bid]))
        top_ask_vol = max(0, -int(depth.sell_orders[best_ask]))
        total = bid_vol + ask_vol
        imbalance = 0.0 if total <= 0 else (bid_vol - ask_vol) / total
        sparse_bid = 1.0 if len(bid_prices) < 3 else 0.0
        sparse_ask = 1.0 if len(ask_prices) < 3 else 0.0
        return {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": best_ask - best_bid,
            "top_bid_vol": top_bid_vol,
            "top_ask_vol": top_ask_vol,
            "bid_vol": bid_vol,
            "ask_vol": ask_vol,
            "imbalance": imbalance,
            "sparse_bid": sparse_bid,
            "sparse_ask": sparse_ask,
        }

    def _select_mode(self, timestamp: int, product: str) -> str:
        product_offset = sum(ord(ch) for ch in product) % 5
        bucket = (timestamp // 1000 + product_offset) % 5
        if bucket == 0:
            return "WIDE_SPREAD"
        if bucket == 1:
            return "THIN_BID"
        if bucket == 2:
            return "THIN_ASK"
        if bucket == 3:
            return "IMBALANCE"
        return "SPARSE_DEPTH"

    def _orders_for_mode(
        self,
        product: str,
        depth: OrderDepth,
        s: Dict[str, float],
        position: int,
        mode: str,
    ) -> Tuple[List[Order], str]:
        best_bid = int(s["best_bid"])
        best_ask = int(s["best_ask"])
        spread = int(s["spread"])
        fair = self.FAIR[product]
        orders: List[Order] = []

        buy_cap = max(0, self.LIMITS[product] - int(position))
        sell_cap = max(0, self.LIMITS[product] + int(position))
        qty = self._test_size(product, spread)

        if mode == "WIDE_SPREAD" and spread >= self.WIDE_SPREAD[product]:
            buy_price = min(best_ask - 1, max(best_bid + 1, fair - max(1, spread // 3)))
            sell_price = max(best_bid + 1, min(best_ask - 1, fair + max(1, spread // 3)))
            if buy_cap > 0 and buy_price < best_ask:
                orders.append(Order(product, buy_price, min(qty, buy_cap)))
            if sell_cap > 0 and sell_price > best_bid:
                orders.append(Order(product, sell_price, -min(qty, sell_cap)))
            return orders, self._note(product, mode, s)

        if mode == "THIN_BID" and s["top_bid_vol"] <= self.THIN_TOP[product] and spread >= 2:
            price = best_bid + 1
            if price < best_ask and buy_cap > 0 and price <= fair + max(1, spread // 2):
                orders.append(Order(product, price, min(qty + 1, buy_cap)))
            return orders, self._note(product, mode, s)

        if mode == "THIN_ASK" and s["top_ask_vol"] <= self.THIN_TOP[product] and spread >= 2:
            price = best_ask - 1
            if price > best_bid and sell_cap > 0 and price >= fair - max(1, spread // 2):
                orders.append(Order(product, price, -min(qty + 1, sell_cap)))
            return orders, self._note(product, mode, s)

        if mode == "IMBALANCE":
            imbalance = s["imbalance"]
            if imbalance >= self.IMBALANCE_THRESHOLD and spread >= 2:
                price = best_ask - 1
                if price > best_bid and sell_cap > 0:
                    orders.append(Order(product, price, -min(qty, sell_cap)))
                return orders, self._note(product, "IMBALANCE_SELL", s)
            if imbalance <= -self.IMBALANCE_THRESHOLD and spread >= 2:
                price = best_bid + 1
                if price < best_ask and buy_cap > 0:
                    orders.append(Order(product, price, min(qty, buy_cap)))
                return orders, self._note(product, "IMBALANCE_BUY", s)

        if mode == "SPARSE_DEPTH" and (s["sparse_bid"] or s["sparse_ask"]) and spread >= 2:
            if s["sparse_bid"] and buy_cap > 0:
                buy_price = best_bid + 1
                if buy_price < best_ask:
                    orders.append(Order(product, buy_price, min(qty, buy_cap)))
            if s["sparse_ask"] and sell_cap > 0:
                sell_price = best_ask - 1
                if sell_price > best_bid:
                    orders.append(Order(product, sell_price, -min(qty, sell_cap)))
            return orders, self._note(product, mode, s)

        return orders, ""

    def _test_size(self, product: str, spread: int) -> int:
        if product == "HYDROGEL_PACK":
            base = 3
        elif product == "VELVETFRUIT_EXTRACT":
            base = 4
        elif product in ("VEV_4000", "VEV_4500"):
            base = 2
        elif product in ("VEV_5000", "VEV_5100", "VEV_5200"):
            base = 5
        else:
            base = 6
        return min(base + max(0, spread - self.WIDE_SPREAD[product]) // 2, 12)

    def _note(self, product: str, mode: str, s: Dict[str, float]) -> str:
        return (
            f"{product}:{mode}"
            + f":spr={int(s['spread'])}"
            + f":tb={int(s['top_bid_vol'])}"
            + f":ta={int(s['top_ask_vol'])}"
            + f":imb={s['imbalance']:.2f}"
            + f":sparse={int(s['sparse_bid'])}/{int(s['sparse_ask'])}"
        )

    def _own_fill_notes(self, state: TradingState) -> List[str]:
        notes: List[str] = []
        for product in self.PRODUCTS:
            for trade in state.own_trades.get(product, []):
                notes.append(
                    f"{product}:{getattr(trade, 'buyer', '')}>{getattr(trade, 'seller', '')}"
                    + f"@{getattr(trade, 'price', '')}x{getattr(trade, 'quantity', '')}"
                )
        return notes
