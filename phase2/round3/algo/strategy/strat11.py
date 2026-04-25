from __future__ import annotations

"""
Strategy 11 - Lucas HP turbo + strat7 empirical option engine.

This is a high-PnL experimental hybrid. The logs show Lucas' best submission
earned almost all of its PnL from an aggressive fixed-fair HYDROGEL_PACK engine:
take visible prices better than 10000 and quote huge passive size at 9998/10002.
The same logs show our strat7 engine earns much more consistently on VELVET and
the mid-strike vouchers. This strategy combines those two discoveries.

Risk profile: intentionally aggressive. HYDROGEL can run near the full +/-200
limit and will create large drawdowns if the final HP path does not mean-revert.
The option sleeve keeps strat7's empirical mean-reversion framework rather than
Black-Scholes directionality, because the logs proved that was the first options
approach to generate meaningful PnL.
"""

import json
import math
from typing import Any, Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ModuleNotFoundError:
    from prosperity3bt.datamodel import Order, OrderDepth, TradingState


class OrderManager:
    def __init__(self, state: TradingState, limits: Dict[str, int], result: Dict[str, List[Order]]) -> None:
        self.limits = limits
        self.result = result
        self.start_pos = {p: int(state.position.get(p, 0)) for p in limits}
        self.expected_pos = dict(self.start_pos)
        self.buy_sent = {p: 0 for p in limits}
        self.sell_sent = {p: 0 for p in limits}

    def add(self, product: str, price: int, qty: int) -> int:
        if qty == 0 or product not in self.limits:
            return 0
        start = self.start_pos.get(product, 0)
        limit = self.limits[product]
        if qty > 0:
            allowed = max(0, limit - start - self.buy_sent[product])
            placed = min(int(qty), allowed)
            if placed <= 0:
                return 0
            self.buy_sent[product] += placed
        else:
            allowed = max(0, limit + start - self.sell_sent[product])
            placed = -min(int(-qty), allowed)
            if placed >= 0:
                return 0
            self.sell_sent[product] += -placed
        self.expected_pos[product] = self.expected_pos.get(product, start) + placed
        self.result.setdefault(product, []).append(Order(product, int(price), int(placed)))
        return abs(placed)

    def pos(self, product: str) -> int:
        return self.expected_pos.get(product, self.start_pos.get(product, 0))


class Trader:
    HP = "HYDROGEL_PACK"
    VE = "VELVETFRUIT_EXTRACT"
    VOUCHERS = [
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
    PRODUCTS = [HP, VE] + VOUCHERS
    LIMITS = {
        HP: 200,
        VE: 200,
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

    # Empirical anchors from the tradable 1000-step public window, not BS.
    # The experiment is to trade observed mean reversion directly.
    ANCHOR = {
        HP: 10000.0,
        VE: 5262.0,
        "VEV_4000": 1265.0,
        "VEV_4500": 764.0,
        "VEV_5000": 266.0,
        "VEV_5100": 175.0,
        "VEV_5200": 101.0,
        "VEV_5300": 50.0,
        "VEV_5400": 16.0,
        "VEV_5500": 6.0,
        "VEV_6000": 0.5,
        "VEV_6500": 0.5,
    }
    SOFT = {
        HP: 105,
        VE: 105,
        "VEV_4000": 18,
        "VEV_4500": 18,
        "VEV_5000": 45,
        "VEV_5100": 55,
        "VEV_5200": 70,
        "VEV_5300": 85,
        "VEV_5400": 130,
        "VEV_5500": 130,
        "VEV_6000": 220,
        "VEV_6500": 220,
    }

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        om = OrderManager(state, self.LIMITS, result)
        cache = self._load_cache(state.traderData)

        timestamp = int(getattr(state, "timestamp", 0))
        for product in self.PRODUCTS:
            depth = state.order_depths.get(product)
            if depth is None:
                continue
            if product == self.HP:
                self._trade_hydrogel_turbo(depth, om)
            elif product == self.VE:
                self._trade_mean_reversion(product, depth, cache, om, alpha=0.045, inv=0.030, edge=0.7, size=24, taker=False)
            else:
                self._trade_voucher_stat_arb(product, depth, cache, om, timestamp)

        cache["t"] = timestamp
        return result, 0, json.dumps(cache, separators=(",", ":"))

    def _trade_hydrogel_turbo(self, depth: OrderDepth, om: OrderManager) -> None:
        bid, ask, _, _ = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return
        pos = om.pos(self.HP)
        limit = self.LIMITS[self.HP]

        # Lucas-style fixed fair exploit. The public/live logs indicate that
        # HYDROGEL bots repeatedly trade around a stable 10000 anchor.
        for ask_px, ask_vol in sorted(depth.sell_orders.items()):
            if ask_px < 10000 and om.pos(self.HP) < limit:
                om.add(self.HP, int(ask_px), min(-int(ask_vol), limit - om.pos(self.HP)))
        for bid_px, bid_vol in sorted(depth.buy_orders.items(), reverse=True):
            if bid_px > 10000 and om.pos(self.HP) > -limit:
                om.add(self.HP, int(bid_px), -min(int(bid_vol), limit + om.pos(self.HP)))

        pos = om.pos(self.HP)
        buy_cap = limit - pos
        sell_cap = limit + pos
        scale = max(20, int(max(0.25, 1.0 - abs(pos / limit) * 0.65) * limit))
        if buy_cap > 0 and 9998 < ask:
            om.add(self.HP, 9998, min(scale, buy_cap))
        if sell_cap > 0 and 10002 > bid:
            om.add(self.HP, 10002, -min(scale, sell_cap))

    def _trade_voucher_stat_arb(self, product: str, depth: OrderDepth, cache: Dict[str, Any], om: OrderManager, timestamp: int) -> None:
        if product in ("VEV_6000", "VEV_6500"):
            self._trade_floor_option(product, depth, om)
            return

        # Skip the first few ticks for non-floor vouchers unless the price is
        # already far from the empirical anchor. This avoids the strat6 open-buy failure.
        bid, ask, _, _ = self._best_bid_ask(depth)
        mid = self._mid(depth)
        if bid is None or ask is None or mid is None:
            return
        anchor = self.ANCHOR[product]
        if timestamp < 1200 and abs(mid - anchor) < max(2.0, 0.015 * anchor):
            return

        if product in ("VEV_4000", "VEV_4500"):
            self._trade_mean_reversion(product, depth, cache, om, alpha=0.025, inv=0.080, edge=4.0, size=6, taker=False)
        elif product == "VEV_5000":
            self._trade_mean_reversion(product, depth, cache, om, alpha=0.030, inv=0.090, edge=2.3, size=8, taker=True)
        elif product in ("VEV_5100", "VEV_5200", "VEV_5300"):
            self._trade_mean_reversion(product, depth, cache, om, alpha=0.035, inv=0.075, edge=1.4, size=12, taker=True)
        else:
            self._trade_mean_reversion(product, depth, cache, om, alpha=0.045, inv=0.040, edge=0.8, size=18, taker=True)

    def _trade_floor_option(self, product: str, depth: OrderDepth, om: OrderManager) -> None:
        bid, ask, _, _ = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return
        pos = om.pos(product)
        soft = self.SOFT[product]
        # Only sell the floor options if we can receive 1. Never sell at 0.
        if ask <= 1 and pos > -soft:
            om.add(product, 1, -min(25, pos + soft))
        # If somehow short and bid is 0, resting buy at 0 can reduce tail inventory for free.
        if pos < -soft // 2 and bid <= 0:
            om.add(product, 0, min(20, -pos))

    def _trade_mean_reversion(
        self,
        product: str,
        depth: OrderDepth,
        cache: Dict[str, Any],
        om: OrderManager,
        alpha: float,
        inv: float,
        edge: float,
        size: int,
        taker: bool,
    ) -> None:
        bid, ask, _, _ = self._best_bid_ask(depth)
        mid = self._mid(depth)
        if bid is None or ask is None or mid is None:
            return
        spread = ask - bid
        if spread <= 0:
            return

        key = product.replace("_", "").lower()
        anchor = self.ANCHOR[product]
        ema = self._ema(cache, f"{key}_ema", mid, alpha)
        abs_move = self._ema(cache, f"{key}_abs", abs(mid - float(cache.get(f"{key}_last", mid))), 0.08)
        cache[f"{key}_last"] = round(float(mid), 6)
        micro = self._microprice(depth)
        micro_edge = (micro - mid) if micro is not None else 0.0

        # Blend fixed empirical anchor and live EMA. Anchors keep us contrarian;
        # EMA prevents fighting a persistent live repricing forever.
        anchor_weight = 0.72 if product in self.VOUCHERS else 0.60
        fair = anchor_weight * anchor + (1.0 - anchor_weight) * ema
        fair += 0.08 * micro_edge
        pos = om.pos(product)
        soft = self.SOFT[product]
        fair -= inv * pos

        dynamic_edge = edge + min(1.5 * edge, 0.25 * abs_move)
        if product == self.HP and spread >= 10:
            buy_px = bid + 1
            sell_px = ask - 1
        elif spread >= 3:
            buy_px = bid + 1
            sell_px = ask - 1
        else:
            buy_px = bid
            sell_px = ask

        # Directional skew: if rich, offer aggressively and bid tiny; if cheap,
        # bid aggressively and offer tiny.
        richness = mid - fair
        buy_size = size
        sell_size = size
        if richness > dynamic_edge:
            buy_size = max(1, size // 4)
            sell_size = int(size * 1.6)
        elif richness < -dynamic_edge:
            buy_size = int(size * 1.6)
            sell_size = max(1, size // 4)

        if pos > soft * 0.55:
            buy_size = 0
            sell_size = int(size * 2.0)
        elif pos < -soft * 0.55:
            buy_size = int(size * 2.0)
            sell_size = 0

        if taker:
            # Only cross when the visible quote is still beyond fair by more
            # than the full observed spread. This is an experiment, not default flow.
            if pos > -soft and bid >= fair + dynamic_edge + spread:
                self._sweep_sell(product, depth, bid, min(sell_size, pos + soft), om)
            if pos < soft and ask <= fair - dynamic_edge - spread:
                self._sweep_buy(product, depth, ask, min(buy_size, soft - pos), om)

        if pos < soft and buy_size > 0 and buy_px <= fair - dynamic_edge:
            om.add(product, buy_px, min(buy_size, soft - pos))
        if pos > -soft and sell_size > 0 and sell_px >= fair + dynamic_edge:
            om.add(product, sell_px, -min(sell_size, pos + soft))

    def _sweep_buy(self, product: str, depth: OrderDepth, max_price: int, qty: int, om: OrderManager) -> None:
        remaining = max(0, int(qty))
        for price, volume in sorted(depth.sell_orders.items()):
            if remaining <= 0 or price > max_price:
                break
            remaining -= om.add(product, int(price), min(remaining, -int(volume)))

    def _sweep_sell(self, product: str, depth: OrderDepth, min_price: int, qty: int, om: OrderManager) -> None:
        remaining = max(0, int(qty))
        for price, volume in sorted(depth.buy_orders.items(), reverse=True):
            if remaining <= 0 or price < min_price:
                break
            remaining -= om.add(product, int(price), -min(remaining, int(volume)))

    def _best_bid_ask(self, depth: Optional[OrderDepth]) -> Tuple[Optional[int], Optional[int], int, int]:
        if depth is None:
            return None, None, 0, 0
        bid = max(depth.buy_orders) if depth.buy_orders else None
        ask = min(depth.sell_orders) if depth.sell_orders else None
        bid_vol = int(depth.buy_orders[bid]) if bid is not None else 0
        ask_vol = -int(depth.sell_orders[ask]) if ask is not None else 0
        return bid, ask, bid_vol, ask_vol

    def _mid(self, depth: Optional[OrderDepth]) -> Optional[float]:
        bid, ask, _, _ = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2.0

    def _microprice(self, depth: OrderDepth) -> Optional[float]:
        bid, ask, bid_vol, ask_vol = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return None
        total = bid_vol + ask_vol
        if total <= 0:
            return (bid + ask) / 2.0
        return (ask * bid_vol + bid * ask_vol) / total

    def _ema(self, cache: Dict[str, Any], key: str, value: float, alpha: float) -> float:
        old = cache.get(key)
        new = (1.0 - alpha) * float(old) + alpha * value if isinstance(old, (int, float)) else value
        cache[key] = round(float(new), 6)
        return float(new)

    def _load_cache(self, trader_data: str) -> Dict[str, Any]:
        if not trader_data:
            return {}
        try:
            parsed = json.loads(trader_data)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, json.JSONDecodeError):
            return {}
