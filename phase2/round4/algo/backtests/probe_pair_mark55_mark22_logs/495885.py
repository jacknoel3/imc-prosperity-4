from __future__ import annotations

"""
probe_pair_mark55_mark22.py

Pair-specific counterparty probe for Mark 55->Mark 22.

Purpose:
- This is an experimental data-collection strategy, not a production PnL bot.
- It targets only the products historically traded by Mark 55 as buyer and
  Mark 22 as seller in the round 4 public data.
- We cannot directly choose the counterparty on Prosperity. Instead, this bot
  creates market conditions that are likely to expose the pair's behavior:
  passive sell bait for the historical buyer, passive buy bait for the
  historical seller, small taker probes into the visible book, and controlled
  inventory unwind.
- After upload, convert the log and measure fills by counterparty, product,
  side, quoted distance, size, and post-trade markout. The relevant question is
  whether Mark 55 or Mark 22 appears in our own trades, at which prices, and
  whether those fills are toxic or profitable after 10/50/100/500 ticks.

Risk design:
- Small per-order size and a strict probe inventory cap keep the experiment
  from turning into a directional bet.
- Taker probes are deliberately tiny but frequent enough to guarantee log data
  when the book is available.
- If inventory approaches the probe cap, the strategy switches to aggressive
  flattening before continuing the experiment.

Historical hypotheses to confirm or reject:
  - VELVETFRUIT_EXTRACT: observed_trades=14, total_qty=62, avg_price=5255.7143. Mark 55 buys VE from secondary liquidity sources; sample is smaller and less predictive. Use mostly for liquidity mapping, not alpha.
"""

import json
from typing import Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ModuleNotFoundError:
    from prosperity3bt.datamodel import Order, OrderDepth, TradingState


class Trader:
    TARGET_BUYER = "Mark 55"
    TARGET_SELLER = "Mark 22"
    PAIR_LABEL = "Mark 55->Mark 22"
    TARGET_PRODUCTS = [
        "VELVETFRUIT_EXTRACT",
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

    def bid(self) -> int:
        return 15

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        timestamp = int(getattr(state, "timestamp", 0))
        mode = (timestamp // 3000) % 6
        size_bucket = 1 + ((timestamp // 1000) % 3)
        mode_name = [
            "SYMMETRIC_PASSIVE",
            "SELL_BAIT_FOR_TARGET_BUYER",
            "BUY_BAIT_FOR_TARGET_SELLER",
            "TAKE_ASK_IDENTIFY_RESTING_SELLER",
            "HIT_BID_IDENTIFY_RESTING_BUYER",
            "INVENTORY_FLATTEN",
        ][mode]

        fill_notes = self._own_fill_notes(state)
        order_notes: List[str] = []

        for product in self.TARGET_PRODUCTS:
            depth = state.order_depths.get(product)
            if depth is None:
                continue
            bid, ask = self._best_bid_ask(depth)
            if bid is None or ask is None:
                continue

            position = int(state.position.get(product, 0))
            max_probe_pos = self._max_probe_position(product)
            qty = self._base_qty(product, size_bucket)

            orders: List[Order] = []
            if abs(position) >= int(0.75 * max_probe_pos) or mode == 5:
                orders.extend(self._flatten_orders(product, bid, ask, position, qty))
            elif mode == 0:
                orders.extend(self._symmetric_passive(product, bid, ask, position, qty))
            elif mode == 1:
                orders.extend(self._sell_bait(product, bid, ask, position, qty))
            elif mode == 2:
                orders.extend(self._buy_bait(product, bid, ask, position, qty))
            elif mode == 3:
                orders.extend(self._take_ask(product, ask, position, max(1, qty // 2)))
            elif mode == 4:
                orders.extend(self._hit_bid(product, bid, position, max(1, qty // 2)))

            if orders:
                result[product].extend(orders)
                order_notes.append(product + ":" + ",".join(str(order) for order in orders))

        if fill_notes or timestamp % 3000 == 0:
            print(
                "PAIR_PROBE"
                + f"|pair={self.PAIR_LABEL}|mode={mode_name}|t={timestamp}"
                + f"|fills={';'.join(fill_notes[:8])}"
                + f"|orders={';'.join(order_notes[:6])}"
            )

        trader_data = json.dumps(
            {
                "pair": self.PAIR_LABEL,
                "mode": mode_name,
                "t": timestamp,
                "products": self.TARGET_PRODUCTS,
            },
            separators=(",", ":"),
        )
        return result, 0, trader_data

    def _own_fill_notes(self, state: TradingState) -> List[str]:
        notes: List[str] = []
        for product in self.TARGET_PRODUCTS:
            for trade in state.own_trades.get(product, []):
                buyer = getattr(trade, "buyer", "")
                seller = getattr(trade, "seller", "")
                price = getattr(trade, "price", "")
                qty = getattr(trade, "quantity", "")
                notes.append(f"{product}:{buyer}>{seller}@{price}x{qty}")
        return notes

    def _best_bid_ask(self, depth: Optional[OrderDepth]) -> Tuple[Optional[int], Optional[int]]:
        if depth is None or not depth.buy_orders or not depth.sell_orders:
            return None, None
        return max(depth.buy_orders), min(depth.sell_orders)

    def _base_qty(self, product: str, bucket: int) -> int:
        if product == "HYDROGEL_PACK":
            return 4 * bucket
        if product == "VELVETFRUIT_EXTRACT":
            return 3 * bucket
        if product in {"VEV_6000", "VEV_6500"}:
            return 5 * bucket
        if product.startswith("VEV_"):
            return 2 * bucket
        return bucket

    def _max_probe_position(self, product: str) -> int:
        if product == "HYDROGEL_PACK":
            return 60
        if product == "VELVETFRUIT_EXTRACT":
            return 60
        if product in {"VEV_6000", "VEV_6500"}:
            return 90
        return 70

    def _buy_capacity(self, product: str, position: int) -> int:
        return max(0, min(self.LIMITS[product] - position, self._max_probe_position(product) - position))

    def _sell_capacity(self, product: str, position: int) -> int:
        return max(0, min(self.LIMITS[product] + position, self._max_probe_position(product) + position))

    def _inside_prices(self, bid: int, ask: int) -> Tuple[int, int]:
        spread = ask - bid
        if spread >= 4:
            return bid + 1, ask - 1
        if spread == 3:
            return bid + 1, ask - 1
        return bid, ask

    def _symmetric_passive(self, product: str, bid: int, ask: int, position: int, qty: int) -> List[Order]:
        buy_px, sell_px = self._inside_prices(bid, ask)
        orders: List[Order] = []
        buy_qty = min(qty, self._buy_capacity(product, position))
        sell_qty = min(qty, self._sell_capacity(product, position))
        if buy_qty > 0 and buy_px < ask:
            orders.append(Order(product, buy_px, buy_qty))
        if sell_qty > 0 and sell_px > bid:
            orders.append(Order(product, sell_px, -sell_qty))
        return orders

    def _sell_bait(self, product: str, bid: int, ask: int, position: int, qty: int) -> List[Order]:
        _, sell_px = self._inside_prices(bid, ask)
        sell_qty = min(qty * 2, self._sell_capacity(product, position))
        if sell_qty <= 0 or sell_px <= bid:
            return []
        return [Order(product, sell_px, -sell_qty)]

    def _buy_bait(self, product: str, bid: int, ask: int, position: int, qty: int) -> List[Order]:
        buy_px, _ = self._inside_prices(bid, ask)
        buy_qty = min(qty * 2, self._buy_capacity(product, position))
        if buy_qty <= 0 or buy_px >= ask:
            return []
        return [Order(product, buy_px, buy_qty)]

    def _take_ask(self, product: str, ask: int, position: int, qty: int) -> List[Order]:
        buy_qty = min(qty, self._buy_capacity(product, position))
        if buy_qty <= 0:
            return []
        return [Order(product, ask, buy_qty)]

    def _hit_bid(self, product: str, bid: int, position: int, qty: int) -> List[Order]:
        sell_qty = min(qty, self._sell_capacity(product, position))
        if sell_qty <= 0:
            return []
        return [Order(product, bid, -sell_qty)]

    def _flatten_orders(self, product: str, bid: int, ask: int, position: int, qty: int) -> List[Order]:
        if position > 0:
            sell_qty = min(abs(position), max(qty * 2, 1), self.LIMITS[product] + position)
            return [Order(product, bid, -sell_qty)] if sell_qty > 0 else []
        if position < 0:
            buy_qty = min(abs(position), max(qty * 2, 1), self.LIMITS[product] - position)
            return [Order(product, ask, buy_qty)] if buy_qty > 0 else []
        return self._symmetric_passive(product, bid, ask, position, max(1, qty // 2))