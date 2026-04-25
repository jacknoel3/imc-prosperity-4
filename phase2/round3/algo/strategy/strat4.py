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
        self.start_pos = {p: int(state.position.get(p, 0)) for p in limits}
        self.expected_pos = dict(self.start_pos)
        self.buy_sent = {p: 0 for p in limits}
        self.sell_sent = {p: 0 for p in limits}

    def add(self, product: str, price: int, qty: int) -> int:
        if qty == 0:
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
    HYDROGEL = "HYDROGEL_PACK"
    VELVET = "VELVETFRUIT_EXTRACT"
    VOUCHERS = ["VEV_4000", "VEV_4500", "VEV_5000", "VEV_5100", "VEV_5200", "VEV_5300", "VEV_5400", "VEV_5500", "VEV_6000", "VEV_6500"]
    STRIKES = {product: float(product.split("_")[1]) for product in VOUCHERS}
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

    HP_SOFT_LIMIT = 14
    VOUCHER_CAP = {"VEV_4000": 10, "VEV_4500": 10, "VEV_5000": 6}

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        om = OrderManager(state, self.LIMITS, result)
        cache = self._load_cache(state.traderData)

        hydro_depth = state.order_depths.get(self.HYDROGEL)
        if hydro_depth is not None:
            self._trade_hydrogel(hydro_depth, cache, om)

        velvet_depth = state.order_depths.get(self.VELVET)
        spot = self._mid(velvet_depth) if velvet_depth is not None else None
        if spot is not None:
            self._update_ivs(cache, state, spot)
            for product in ("VEV_4000", "VEV_4500", "VEV_5000"):
                depth = state.order_depths.get(product)
                if depth is not None:
                    self._trade_voucher_long_only(product, depth, spot, cache, om)

        cache["t"] = int(getattr(state, "timestamp", 0))
        return result, 0, json.dumps(cache, separators=(",", ":"))

    def _trade_hydrogel(self, depth: OrderDepth, cache: Dict[str, Any], om: OrderManager) -> None:
        bid, ask, _, _ = self._best_bid_ask(depth)
        mid = self._mid(depth)
        if bid is None or ask is None or mid is None:
            return

        fair = self._ema(cache, "hp_fair", mid, 0.16)
        micro = self._microprice(depth)
        dom = self._dom_mid(depth)
        if micro is not None:
            fair += 0.18 * (micro - mid)
        if dom is not None:
            fair += 0.25 * (dom - mid)

        pos = om.pos(self.HYDROGEL)
        fair -= 0.10 * pos
        spread = ask - bid

        # Smoothness guard: v2 was profitable, v3 lost smoothness by quoting too
        # close. Keep meaningful edge and a smaller soft inventory band.
        if pos < self.HP_SOFT_LIMIT and ask <= fair - 8.5:
            self._sweep_buy(self.HYDROGEL, depth, ask, min(10, max(2, int((fair - ask) * 1.2))), om)
        if pos > -self.HP_SOFT_LIMIT and bid >= fair + 8.5:
            self._sweep_sell(self.HYDROGEL, depth, bid, min(10, max(2, int((bid - fair) * 1.2))), om)

        if spread > 24:
            return

        # Quote only one side when inventory is leaning. This reduces mark-to-
        # market swings while still harvesting the strong Hydrogel inside-spread edge.
        bid_px = int(math.floor(fair - 5.0))
        ask_px = int(math.ceil(fair + 5.0))
        if spread > 2:
            bid_px = min(ask - 1, max(bid + 1, bid_px))
            ask_px = max(bid + 1, min(ask - 1, ask_px))
        else:
            bid_px = min(bid_px, bid)
            ask_px = max(ask_px, ask)

        base = 12
        if pos < self.HP_SOFT_LIMIT and bid_px <= fair - 3.25:
            buy_qty = min(base, self.HP_SOFT_LIMIT - pos)
            if buy_qty > 0:
                om.add(self.HYDROGEL, bid_px, buy_qty)
        if pos > -self.HP_SOFT_LIMIT and ask_px >= fair + 3.25:
            sell_qty = min(base, pos + self.HP_SOFT_LIMIT)
            if sell_qty > 0:
                om.add(self.HYDROGEL, ask_px, -sell_qty)

    def _trade_voucher_long_only(self, product: str, depth: OrderDepth, spot: float, cache: Dict[str, Any], om: OrderManager) -> None:
        bid, ask, _, _ = self._best_bid_ask(depth)
        mid = self._mid(depth)
        if bid is None or ask is None or mid is None:
            return
        pos = om.pos(product)
        cap = self.VOUCHER_CAP[product]
        if pos >= cap:
            return

        if product in ("VEV_4000", "VEV_4500"):
            intrinsic = max(spot - self.STRIKES[product], 0.0)
            target_bid = int(math.floor(intrinsic - 3.0))
            if ask - bid > 1:
                target_bid = min(ask - 1, max(bid + 1, target_bid))
            else:
                target_bid = min(bid, target_bid)
            # No taker ask in strat4. The v3 ask-like deep-ITM fills hurt smoothness.
            if target_bid <= intrinsic - 1.75:
                om.add(product, target_bid, min(3, cap - pos))
            return

        fair, _ = self._option_fair_delta(product, spot, cache)
        target_bid = bid + 1 if ask - bid > 2 else bid
        if target_bid <= fair - 2.0:
            om.add(product, target_bid, min(3, cap - pos))
        if pos > 0 and bid >= fair + 1.5:
            self._sweep_sell(product, depth, bid, min(3, pos), om)

    def _update_ivs(self, cache: Dict[str, Any], state: TradingState, spot: float) -> None:
        ivs = cache.setdefault("iv", {})
        for product in self.VOUCHERS:
            depth = state.order_depths.get(product)
            mid = self._mid(depth) if depth is not None else None
            if mid is None:
                continue
            iv = self._implied_vol(spot, self.STRIKES[product], mid)
            if iv is None:
                continue
            old = ivs.get(product)
            ivs[product] = round(0.94 * float(old) + 0.06 * iv, 6) if isinstance(old, (int, float)) else round(iv, 6)

    def _option_fair_delta(self, product: str, spot: float, cache: Dict[str, Any]) -> Tuple[float, float]:
        strike = self.STRIKES[product]
        sigma = self._iv(cache, product)
        intrinsic = max(spot - strike, 0.0)
        if product in ("VEV_4000", "VEV_4500") and intrinsic > 350:
            return intrinsic + 0.1, 0.985 if product == "VEV_4000" else 0.955
        return max(intrinsic, self._bs_call(spot, strike, sigma)), self._bs_delta(spot, strike, sigma)

    def _iv(self, cache: Dict[str, Any], product: str) -> float:
        val = cache.get("iv", {}).get(product)
        if isinstance(val, (int, float)) and 0.03 <= float(val) <= 2.0:
            return float(val)
        return self.DEFAULT_IV[product]

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

    def _implied_vol(self, spot: float, strike: float, price: float) -> Optional[float]:
        if spot <= 0 or strike <= 0 or price <= 0:
            return None
        lower = max(spot - strike, 0.0)
        if price <= lower + 1e-9 or price >= spot:
            return None
        lo, hi = 1e-6, 5.0
        f_lo = self._bs_call(spot, strike, lo) - price
        f_hi = self._bs_call(spot, strike, hi) - price
        if f_lo * f_hi > 0:
            return None
        for _ in range(55):
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
        if sigma <= 0 or self.TTE <= 0:
            return max(spot - strike, 0.0)
        vol_t = sigma * math.sqrt(self.TTE)
        if vol_t <= 0:
            return max(spot - strike, 0.0)
        d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * self.TTE) / vol_t
        d2 = d1 - vol_t
        return spot * self._norm_cdf(d1) - strike * self._norm_cdf(d2)

    def _bs_delta(self, spot: float, strike: float, sigma: float) -> float:
        if sigma <= 0 or self.TTE <= 0:
            return 1.0 if spot > strike else 0.0
        vol_t = sigma * math.sqrt(self.TTE)
        if vol_t <= 0:
            return 1.0 if spot > strike else 0.0
        d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * self.TTE) / vol_t
        return self._norm_cdf(d1)

    def _norm_cdf(self, x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
