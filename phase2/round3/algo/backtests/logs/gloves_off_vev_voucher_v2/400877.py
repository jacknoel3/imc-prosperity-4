from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ModuleNotFoundError:
    from prosperity3bt.datamodel import Order, OrderDepth, TradingState


class OrderManager:
    """Side-aware order limiter.

    Prosperity risk checks buy and sell capacity separately. Netting bid and ask
    orders inside the same tick can pass locally while still being rejected by
    the platform. This manager tracks submitted buy and sell quantity
    independently.
    """

    def __init__(self, state: TradingState, limits: Dict[str, int], result: Dict[str, List[Order]]) -> None:
        self.limits = limits
        self.result = result
        self.start_pos = {product: int(state.position.get(product, 0)) for product in limits}
        self.expected_pos = dict(self.start_pos)
        self.buy_submitted = {product: 0 for product in limits}
        self.sell_submitted = {product: 0 for product in limits}

    def add(self, product: str, price: int, quantity: int) -> int:
        if quantity == 0:
            return 0
        limit = self.limits[product]
        start = self.start_pos.get(product, 0)

        if quantity > 0:
            allowed = max(0, limit - start - self.buy_submitted[product])
            qty = min(int(quantity), allowed)
            if qty <= 0:
                return 0
            self.buy_submitted[product] += qty
        else:
            allowed = max(0, limit + start - self.sell_submitted[product])
            qty = -min(int(-quantity), allowed)
            if qty >= 0:
                return 0
            self.sell_submitted[product] += -qty

        self.expected_pos[product] = self.expected_pos.get(product, start) + qty
        self.result.setdefault(product, []).append(Order(product, int(price), int(qty)))
        return abs(qty)

    def pos(self, product: str) -> int:
        return self.expected_pos.get(product, self.start_pos.get(product, 0))


class Trader:
    HYDROGEL = "HYDROGEL_PACK"
    VELVET = "VELVETFRUIT_EXTRACT"
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
    STRIKES = {
        "VEV_4000": 4000.0,
        "VEV_4500": 4500.0,
        "VEV_5000": 5000.0,
        "VEV_5100": 5100.0,
        "VEV_5200": 5200.0,
        "VEV_5300": 5300.0,
        "VEV_5400": 5400.0,
        "VEV_5500": 5500.0,
        "VEV_6000": 6000.0,
        "VEV_6500": 6500.0,
    }
    LIMITS = {
        HYDROGEL: 200,
        VELVET: 200,
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

    TTE = 5.0 / 365.0

    # Recalibrated from the uploaded-run log. These are fallbacks only; live
    # book IV overrides them through an EMA.
    DEFAULT_IV = {
        "VEV_4000": 0.20,
        "VEV_4500": 0.22,
        "VEV_5000": 0.263,
        "VEV_5100": 0.260,
        "VEV_5200": 0.268,
        "VEV_5300": 0.272,
        "VEV_5400": 0.249,
        "VEV_5500": 0.270,
        "VEV_6000": 0.62,
        "VEV_6500": 0.88,
    }

    # The first live run showed that large near-ATM short-vol positions cause
    # expensive Velvetfruit hedging. Shorts are now capped tightly.
    SHORT_CAP = {
        "VEV_4000": 0,
        "VEV_4500": 0,
        "VEV_5000": -60,
        "VEV_5100": -45,
        "VEV_5200": -30,
        "VEV_5300": -30,
        "VEV_5400": -25,
        "VEV_5500": 0,
        "VEV_6000": 0,
        "VEV_6500": 0,
    }
    LONG_CAP = {
        "VEV_4000": 60,
        "VEV_4500": 60,
        "VEV_5000": 120,
        "VEV_5100": 120,
        "VEV_5200": 160,
        "VEV_5300": 160,
        "VEV_5400": 120,
        "VEV_5500": 80,
        "VEV_6000": 300,
        "VEV_6500": 300,
    }

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        om = OrderManager(state, self.LIMITS, result)
        cache = self._load_cache(state.traderData)

        velvet_depth = state.order_depths.get(self.VELVET)
        spot_mid = self._mid(velvet_depth) if velvet_depth is not None else None

        if spot_mid is not None:
            self._update_live_ivs(cache, state, spot_mid)

        hydro_depth = state.order_depths.get(self.HYDROGEL)
        if hydro_depth is not None:
            self._trade_hydrogel(hydro_depth, cache, om)

        option_state: Dict[str, Tuple[float, float]] = {}
        if spot_mid is not None:
            for product in self.VOUCHERS:
                depth = state.order_depths.get(product)
                if depth is None:
                    continue
                fair, delta = self._option_fair_and_delta(product, spot_mid, cache)
                option_state[product] = (fair, delta)
                self._trade_voucher(product, depth, spot_mid, fair, delta, om)

            if velvet_depth is not None:
                self._passive_delta_hedge(velvet_depth, option_state, om)

        self._store_cache(cache, state, spot_mid)
        return result, 0, json.dumps(cache, separators=(",", ":"))

    def _trade_hydrogel(self, depth: OrderDepth, cache: Dict[str, Any], om: OrderManager) -> None:
        bid, ask, _, _ = self._best_bid_ask(depth)
        mid = self._mid(depth)
        if bid is None or ask is None or mid is None:
            return

        fair = self._ema(cache, "fair_hp", mid, 0.18)
        micro = self._microprice(depth)
        dom = self._dom_mid(depth)
        if micro is not None:
            fair += 0.25 * (micro - mid)
        if dom is not None:
            fair += 0.35 * (dom - mid)

        pos = om.pos(self.HYDROGEL)
        fair -= 0.045 * pos
        spread = ask - bid

        if ask <= fair - 9.0:
            self._sweep_buy(self.HYDROGEL, depth, ask, min(10, int(fair - ask)), om)
        if bid >= fair + 9.0:
            self._sweep_sell(self.HYDROGEL, depth, bid, min(10, int(bid - fair)), om)

        if spread <= 24:
            bid_px = int(math.floor(fair - 5.0))
            ask_px = int(math.ceil(fair + 5.0))
            if spread > 2:
                bid_px = min(ask - 1, max(bid + 1, bid_px))
                ask_px = max(bid + 1, min(ask - 1, ask_px))
            else:
                bid_px = min(bid_px, bid)
                ask_px = max(ask_px, ask)

            buy_qty = self._scaled_qty(14, pos, self.LIMITS[self.HYDROGEL], 1)
            sell_qty = self._scaled_qty(14, pos, self.LIMITS[self.HYDROGEL], -1)
            if bid_px <= fair - 3.0:
                om.add(self.HYDROGEL, bid_px, buy_qty)
            if ask_px >= fair + 3.0:
                om.add(self.HYDROGEL, ask_px, -sell_qty)

    def _trade_voucher(
        self,
        product: str,
        depth: OrderDepth,
        spot_mid: float,
        fair: float,
        delta: float,
        om: OrderManager,
    ) -> None:
        bid, ask, _, _ = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return

        pos = om.pos(product)
        strike = self.STRIKES[product]
        intrinsic = max(spot_mid - strike, 0.0)

        if product in ("VEV_6000", "VEV_6500"):
            # Free lottery only. The previous log showed no useful signed fills,
            # but this cannot lose if price 0 is accepted and filled.
            if pos < self.LONG_CAP[product]:
                om.add(product, 0, min(25, self.LONG_CAP[product] - pos))
            if pos > 60:
                om.add(product, 1, -min(10, pos))
            return

        if product in ("VEV_4000", "VEV_4500"):
            self._trade_deep_itm(product, depth, intrinsic, fair, delta, om)
            return

        # Only take clear underpricing. The first upload proved that selling
        # model-rich near-ATM calls creates bad hedge churn.
        buy_edge = fair - ask
        if buy_edge >= self._buy_take_edge(product) and pos < self.LONG_CAP[product]:
            qty = min(12, self.LONG_CAP[product] - pos, max(3, int(2 + buy_edge * 2)))
            self._sweep_buy(product, depth, ask, qty, om)

        sell_edge = bid - fair
        can_reduce_long = pos > 0 and sell_edge >= 0.5
        can_small_short = (
            pos > self.SHORT_CAP[product]
            and sell_edge >= self._short_take_edge(product)
            and abs(self._portfolio_delta(om, {product: delta})) < 120
        )
        if can_reduce_long or can_small_short:
            qty_cap = pos if pos > 0 else pos - self.SHORT_CAP[product]
            qty = min(8, max(1, int(1 + sell_edge)), qty_cap)
            self._sweep_sell(product, depth, bid, qty, om)

        self._quote_voucher_passive(product, depth, fair, delta, om)

    def _trade_deep_itm(
        self,
        product: str,
        depth: OrderDepth,
        intrinsic: float,
        fair: float,
        delta: float,
        om: OrderManager,
    ) -> None:
        bid, ask, _, _ = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return
        pos = om.pos(product)

        lower_bound_edge = intrinsic - ask
        if lower_bound_edge >= 1.25 and pos < self.LONG_CAP[product]:
            qty = min(8, self.LONG_CAP[product] - pos, max(2, int(lower_bound_edge * 2)))
            if self._portfolio_delta(om, {product: delta}) < 130:
                self._sweep_buy(product, depth, ask, qty, om)

        if pos < self.LONG_CAP[product]:
            bid_px = min(ask - 1, max(bid + 1, int(math.floor(intrinsic - 2.0)))) if ask - bid > 1 else min(bid, int(math.floor(intrinsic - 2.0)))
            if bid_px <= fair - 1.5:
                om.add(product, bid_px, min(4, self.LONG_CAP[product] - pos))

        if pos > 0 and bid >= fair + 2.0:
            self._sweep_sell(product, depth, bid, min(5, pos), om)

    def _quote_voucher_passive(
        self,
        product: str,
        depth: OrderDepth,
        fair: float,
        delta: float,
        om: OrderManager,
    ) -> None:
        bid, ask, _, _ = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return

        pos = om.pos(product)
        spread = ask - bid
        if spread <= 0:
            return

        edge = self._passive_edge(product)
        bid_px = int(math.floor(fair - edge - 0.012 * max(pos, 0)))
        ask_px = int(math.ceil(fair + edge - 0.012 * min(pos, 0)))

        if spread > 1:
            bid_px = min(ask - 1, max(bid, bid_px))
            ask_px = max(bid + 1, min(ask, ask_px))
        else:
            bid_px = min(bid_px, bid)
            ask_px = max(ask_px, ask)

        if pos < self.LONG_CAP[product] and bid_px <= fair - edge * 0.75:
            qty = min(self._passive_qty(product), self.LONG_CAP[product] - pos)
            om.add(product, bid_px, qty)

        # Passive asks are primarily inventory reduction. Avoid recreating the
        # large naked-short book from the first upload.
        if pos > 0 and ask_px >= fair + edge * 0.75:
            om.add(product, ask_px, -min(self._passive_qty(product), pos))
        elif pos > self.SHORT_CAP[product] and product in ("VEV_5000", "VEV_5100", "VEV_5400") and ask_px >= fair + self._short_take_edge(product):
            om.add(product, ask_px, -min(3, pos - self.SHORT_CAP[product]))

    def _passive_delta_hedge(
        self,
        depth: OrderDepth,
        option_state: Dict[str, Tuple[float, float]],
        om: OrderManager,
    ) -> None:
        bid, ask, _, _ = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return

        net_delta = float(om.pos(self.VELVET))
        for product, (_, delta) in option_state.items():
            net_delta += om.pos(product) * delta

        spread = ask - bid
        threshold = 135.0 if spread <= 6 else 160.0
        excess = abs(net_delta) - threshold
        if excess <= 0:
            return

        qty = min(18, int(excess * 0.35))
        if qty <= 0:
            return

        # Hedge passively at the touch. The uploaded log showed that crossing
        # Velvetfruit repeatedly was nearly the entire loss.
        if net_delta < -threshold:
            om.add(self.VELVET, bid, qty)
        elif net_delta > threshold:
            om.add(self.VELVET, ask, -qty)

    def _update_live_ivs(self, cache: Dict[str, Any], state: TradingState, spot_mid: float) -> None:
        iv_cache = cache.setdefault("iv", {})
        for product in self.VOUCHERS:
            depth = state.order_depths.get(product)
            mid = self._mid(depth) if depth is not None else None
            if mid is None:
                continue
            iv = self._implied_vol(spot_mid, self.STRIKES[product], mid)
            if iv is None:
                continue
            old = iv_cache.get(product)
            if isinstance(old, (int, float)) and 0.03 <= old <= 2.0:
                iv_cache[product] = round(0.92 * float(old) + 0.08 * iv, 6)
            else:
                iv_cache[product] = round(iv, 6)

    def _option_fair_and_delta(self, product: str, spot: float, cache: Dict[str, Any]) -> Tuple[float, float]:
        strike = self.STRIKES[product]
        intrinsic = max(spot - strike, 0.0)
        if product in ("VEV_4000", "VEV_4500") and intrinsic > 350:
            return intrinsic + 0.1, 0.985 if product == "VEV_4000" else 0.955

        sigma = self._cached_iv(cache, product)
        fair = max(intrinsic, self._bs_call(spot, strike, sigma))
        delta = self._bs_delta(spot, strike, sigma)
        return fair, delta

    def _cached_iv(self, cache: Dict[str, Any], product: str) -> float:
        raw = cache.get("iv", {}).get(product)
        if isinstance(raw, (int, float)) and 0.03 <= raw <= 2.0:
            return float(raw)
        return self.DEFAULT_IV[product]

    def _portfolio_delta(self, om: OrderManager, overrides: Dict[str, float]) -> float:
        estimate = float(om.pos(self.VELVET))
        for product in self.VOUCHERS:
            delta = overrides.get(product, self._rough_delta(product))
            estimate += om.pos(product) * delta
        return estimate

    def _sweep_buy(self, product: str, depth: OrderDepth, max_price: int, max_qty: int, om: OrderManager) -> None:
        remaining = max(0, int(max_qty))
        for price, volume in sorted(depth.sell_orders.items()):
            if remaining <= 0 or price > max_price:
                break
            qty = min(remaining, -int(volume))
            remaining -= om.add(product, int(price), qty)

    def _sweep_sell(self, product: str, depth: OrderDepth, min_price: int, max_qty: int, om: OrderManager) -> None:
        remaining = max(0, int(max_qty))
        for price, volume in sorted(depth.buy_orders.items(), reverse=True):
            if remaining <= 0 or price < min_price:
                break
            qty = min(remaining, int(volume))
            remaining -= om.add(product, int(price), -qty)

    def _buy_take_edge(self, product: str) -> float:
        return {
            "VEV_5000": 3.0,
            "VEV_5100": 3.0,
            "VEV_5200": 2.2,
            "VEV_5300": 2.0,
            "VEV_5400": 1.4,
            "VEV_5500": 1.2,
        }.get(product, 3.0)

    def _short_take_edge(self, product: str) -> float:
        return {
            "VEV_5000": 5.5,
            "VEV_5100": 6.0,
            "VEV_5200": 7.0,
            "VEV_5300": 7.0,
            "VEV_5400": 3.0,
        }.get(product, 99.0)

    def _passive_edge(self, product: str) -> float:
        return {
            "VEV_5000": 2.4,
            "VEV_5100": 2.1,
            "VEV_5200": 1.4,
            "VEV_5300": 1.2,
            "VEV_5400": 0.9,
            "VEV_5500": 0.9,
        }.get(product, 2.0)

    def _passive_qty(self, product: str) -> int:
        return {
            "VEV_5000": 5,
            "VEV_5100": 5,
            "VEV_5200": 7,
            "VEV_5300": 7,
            "VEV_5400": 6,
            "VEV_5500": 5,
        }.get(product, 4)

    def _rough_delta(self, product: str) -> float:
        return {
            "VEV_4000": 0.985,
            "VEV_4500": 0.955,
            "VEV_5000": 0.84,
            "VEV_5100": 0.72,
            "VEV_5200": 0.56,
            "VEV_5300": 0.38,
            "VEV_5400": 0.20,
            "VEV_5500": 0.09,
            "VEV_6000": 0.01,
            "VEV_6500": 0.00,
        }.get(product, 0.0)

    def _scaled_qty(self, base: int, pos: int, limit: int, side: int) -> int:
        pressure = max(0.0, pos / limit) if side > 0 else max(0.0, -pos / limit)
        return max(1, int(round(base * (1.0 - 0.65 * pressure))))

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

    def _dom_mid(self, depth: OrderDepth) -> Optional[float]:
        if not depth.buy_orders or not depth.sell_orders:
            return None
        bids = sorted(depth.buy_orders.items(), reverse=True)[:3]
        asks = sorted(depth.sell_orders.items())[:3]
        bid_vol = sum(max(0, int(v)) for _, v in bids)
        ask_vol = sum(max(0, -int(v)) for _, v in asks)
        if bid_vol <= 0 or ask_vol <= 0:
            return self._mid(depth)
        bid_vwap = sum(p * max(0, int(v)) for p, v in bids) / bid_vol
        ask_vwap = sum(p * max(0, -int(v)) for p, v in asks) / ask_vol
        return (bid_vwap + ask_vwap) / 2.0

    def _ema(self, cache: Dict[str, Any], key: str, value: float, alpha: float) -> float:
        old = cache.get(key)
        if isinstance(old, (int, float)) and old > 0:
            new = (1.0 - alpha) * float(old) + alpha * value
        else:
            new = value
        cache[key] = round(float(new), 6)
        return float(new)

    def _load_cache(self, trader_data: str) -> Dict[str, Any]:
        if not trader_data:
            return {}
        try:
            data = json.loads(trader_data)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def _store_cache(self, cache: Dict[str, Any], state: TradingState, spot_mid: Optional[float]) -> None:
        if spot_mid is not None:
            cache["fair_ve"] = round(float(spot_mid), 6)
        cache["t"] = int(getattr(state, "timestamp", 0))

    def _implied_vol(self, spot: float, strike: float, price: float) -> Optional[float]:
        if spot <= 0 or strike <= 0 or price <= 0:
            return None
        lower = max(spot - strike, 0.0)
        if price <= lower + 1e-9 or price > spot:
            return None
        lo, hi = 1e-6, 5.0
        f_lo = self._bs_call(spot, strike, lo) - price
        f_hi = self._bs_call(spot, strike, hi) - price
        if f_lo * f_hi > 0:
            return None
        for _ in range(60):
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
        if self.TTE <= 0 or sigma <= 0:
            return max(spot - strike, 0.0)
        vol_t = sigma * math.sqrt(self.TTE)
        if vol_t <= 0:
            return max(spot - strike, 0.0)
        d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * self.TTE) / vol_t
        d2 = d1 - vol_t
        return spot * self._norm_cdf(d1) - strike * self._norm_cdf(d2)

    def _bs_delta(self, spot: float, strike: float, sigma: float) -> float:
        if self.TTE <= 0 or sigma <= 0:
            return 1.0 if spot > strike else 0.0
        vol_t = sigma * math.sqrt(self.TTE)
        if vol_t <= 0:
            return 1.0 if spot > strike else 0.0
        d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * self.TTE) / vol_t
        return self._norm_cdf(d1)

    def _norm_cdf(self, x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))