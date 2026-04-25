from __future__ import annotations

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
        self.start_pos = {product: int(state.position.get(product, 0)) for product in limits}
        self.expected_pos = dict(self.start_pos)
        self.buy_sent = {product: 0 for product in limits}
        self.sell_sent = {product: 0 for product in limits}

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
    VELVET = "VELVETFRUIT_EXTRACT"
    GAMMA_VOUCHERS = ["VEV_5200", "VEV_5300", "VEV_5400"]
    FLOOR_VOUCHERS = ["VEV_6000", "VEV_6500"]
    VOUCHERS = GAMMA_VOUCHERS + FLOOR_VOUCHERS
    STRIKES = {
        "VEV_5200": 5200.0,
        "VEV_5300": 5300.0,
        "VEV_5400": 5400.0,
        "VEV_6000": 6000.0,
        "VEV_6500": 6500.0,
    }
    LIMITS = {
        VELVET: 200,
        "VEV_5200": 300,
        "VEV_5300": 300,
        "VEV_5400": 300,
        "VEV_6000": 300,
        "VEV_6500": 300,
    }

    TTE_YEARS = 5.0 / 365.0
    FAIR_VOL = 0.34
    IV_EMA_ALPHA = 0.08

    LONG_CAP = {
        "VEV_5200": 70,
        "VEV_5300": 130,
        "VEV_5400": 130,
    }
    FLOOR_SHORT_CAP = {
        "VEV_6000": 75,
        "VEV_6500": 75,
    }
    EDGE_TO_BID = {
        "VEV_5200": 1.4,
        "VEV_5300": 1.1,
        "VEV_5400": 0.8,
    }
    ORDER_SIZE = {
        "VEV_5200": 5,
        "VEV_5300": 7,
        "VEV_5400": 8,
    }

    HEDGE_PASSIVE_THRESHOLD = 35.0
    HEDGE_TAKER_THRESHOLD = 115.0
    HEDGE_MAX_SLICE = 18

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        om = OrderManager(state, self.LIMITS, result)
        cache = self._load_cache(state.traderData)

        velvet_depth = state.order_depths.get(self.VELVET)
        spot = self._mid(velvet_depth)
        if spot is None:
            return result, 0, json.dumps(cache, separators=(",", ":"))

        self._update_iv_cache(cache, state, spot)
        deltas: Dict[str, float] = {}

        for product in self.GAMMA_VOUCHERS:
            depth = state.order_depths.get(product)
            if depth is None:
                continue
            fair_vol = self._fair_vol(cache, product)
            fair = self._bs_call(spot, self.STRIKES[product], fair_vol)
            delta = self._bs_delta(spot, self.STRIKES[product], fair_vol)
            deltas[product] = delta
            self._trade_gamma_voucher(product, depth, fair, om)

        for product in self.FLOOR_VOUCHERS:
            depth = state.order_depths.get(product)
            if depth is None:
                continue
            deltas[product] = self._bs_delta(spot, self.STRIKES[product], self.FAIR_VOL)
            self._trade_floor_voucher(product, depth, om)

        if velvet_depth is not None:
            self._trade_velvet_hedge(velvet_depth, deltas, om)

        cache["spot"] = round(float(spot), 4)
        cache["t"] = int(getattr(state, "timestamp", 0))
        return result, 0, json.dumps(cache, separators=(",", ":"))

    def _trade_gamma_voucher(self, product: str, depth: OrderDepth, fair: float, om: OrderManager) -> None:
        bid, ask, _, _ = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return

        pos = om.pos(product)
        cap = self.LONG_CAP[product]
        if pos < cap:
            spread = ask - bid
            bid_px = bid + 1 if spread > 1 else bid
            if bid_px <= fair - self.EDGE_TO_BID[product]:
                qty = min(self.ORDER_SIZE[product], cap - pos)
                om.add(product, bid_px, qty)

        pos = om.pos(product)
        if pos > 0 and bid >= fair + 1.0:
            self._sweep_sell(product, depth, bid, min(5, pos), om)

    def _trade_floor_voucher(self, product: str, depth: OrderDepth, om: OrderManager) -> None:
        bid, ask, _, _ = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return
        pos = om.pos(product)
        short_cap = self.FLOOR_SHORT_CAP[product]

        if ask >= 1 and pos > -short_cap:
            om.add(product, 1, -min(12, pos + short_cap))

        if pos < 0 and bid <= 0:
            om.add(product, 0, min(10, -pos))

    def _trade_velvet_hedge(self, depth: OrderDepth, deltas: Dict[str, float], om: OrderManager) -> None:
        bid, ask, _, _ = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return

        net_delta = float(om.pos(self.VELVET))
        for product, delta in deltas.items():
            net_delta += float(om.pos(product)) * float(delta)

        if abs(net_delta) < self.HEDGE_PASSIVE_THRESHOLD:
            return

        qty = min(self.HEDGE_MAX_SLICE, max(1, int((abs(net_delta) - self.HEDGE_PASSIVE_THRESHOLD) * 0.35)))
        spread = ask - bid
        if net_delta > 0:
            if net_delta > self.HEDGE_TAKER_THRESHOLD:
                om.add(self.VELVET, bid, -qty)
            else:
                price = ask - 1 if spread > 2 else ask
                om.add(self.VELVET, price, -qty)
        else:
            if net_delta < -self.HEDGE_TAKER_THRESHOLD:
                om.add(self.VELVET, ask, qty)
            else:
                price = bid + 1 if spread > 2 else bid
                om.add(self.VELVET, price, qty)

    def _update_iv_cache(self, cache: Dict[str, Any], state: TradingState, spot: float) -> None:
        ivs = cache.setdefault("iv", {})
        if not isinstance(ivs, dict):
            ivs = {}
            cache["iv"] = ivs

        for product in self.GAMMA_VOUCHERS:
            depth = state.order_depths.get(product)
            mid = self._mid(depth)
            if mid is None:
                continue
            iv = self._implied_vol(spot, self.STRIKES[product], mid)
            if iv is None:
                continue
            old = ivs.get(product)
            if isinstance(old, (int, float)):
                iv = (1.0 - self.IV_EMA_ALPHA) * float(old) + self.IV_EMA_ALPHA * iv
            ivs[product] = round(float(iv), 6)

    def _fair_vol(self, cache: Dict[str, Any], product: str) -> float:
        iv = cache.get("iv", {}).get(product)
        if isinstance(iv, (int, float)) and 0.05 <= float(iv) <= 1.0:
            return max(self.FAIR_VOL, float(iv) + 0.04)
        return self.FAIR_VOL

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

    def _load_cache(self, trader_data: str) -> Dict[str, Any]:
        if not trader_data:
            return {}
        try:
            parsed = json.loads(trader_data)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, json.JSONDecodeError):
            return {}

    def _implied_vol(self, spot: float, strike: float, price: float) -> Optional[float]:
        if spot <= 0 or strike <= 0 or price <= 0:
            return None
        lower = max(spot - strike, 0.0)
        if price <= lower + 1e-9 or price >= spot:
            return None
        lo, hi = 1e-6, 3.0
        f_lo = self._bs_call(spot, strike, lo) - price
        f_hi = self._bs_call(spot, strike, hi) - price
        if f_lo * f_hi > 0:
            return None
        for _ in range(50):
            mid = (lo + hi) / 2.0
            f_mid = self._bs_call(spot, strike, mid) - price
            if abs(f_mid) < 1e-7:
                return mid
            if f_lo * f_mid <= 0:
                hi = mid
                f_hi = f_mid
            else:
                lo = mid
                f_lo = f_mid
        return (lo + hi) / 2.0

    def _bs_call(self, spot: float, strike: float, sigma: float) -> float:
        intrinsic = max(spot - strike, 0.0)
        if sigma <= 0 or self.TTE_YEARS <= 0:
            return intrinsic
        vol_t = sigma * math.sqrt(self.TTE_YEARS)
        if vol_t <= 0:
            return intrinsic
        d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * self.TTE_YEARS) / vol_t
        d2 = d1 - vol_t
        return spot * self._norm_cdf(d1) - strike * self._norm_cdf(d2)

    def _bs_delta(self, spot: float, strike: float, sigma: float) -> float:
        if sigma <= 0 or self.TTE_YEARS <= 0:
            return 1.0 if spot > strike else 0.0
        vol_t = sigma * math.sqrt(self.TTE_YEARS)
        if vol_t <= 0:
            return 1.0 if spot > strike else 0.0
        d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * self.TTE_YEARS) / vol_t
        return self._norm_cdf(d1)

    def _norm_cdf(self, value: float) -> float:
        return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))