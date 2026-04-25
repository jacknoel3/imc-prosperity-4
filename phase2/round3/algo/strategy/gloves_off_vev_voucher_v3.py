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
    LONG_CAP = {
        "VEV_4000": 45,
        "VEV_4500": 45,
        "VEV_5000": 45,
        "VEV_5100": 30,
        "VEV_5200": 45,
        "VEV_5300": 45,
        "VEV_5400": 25,
        "VEV_5500": 15,
        "VEV_6000": 300,
        "VEV_6500": 300,
    }

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        om = OrderManager(state, self.LIMITS, result)
        cache = self._load_cache(state.traderData)

        velvet_depth = state.order_depths.get(self.VELVET)
        spot = self._mid(velvet_depth) if velvet_depth is not None else None
        if spot is not None:
            self._update_ivs(cache, state, spot)

        hydro_depth = state.order_depths.get(self.HYDROGEL)
        if hydro_depth is not None:
            self._trade_hydrogel(hydro_depth, cache, om)

        option_delta: Dict[str, float] = {}
        if spot is not None:
            for product in self.VOUCHERS:
                depth = state.order_depths.get(product)
                if depth is None:
                    continue
                fair, delta = self._option_fair_delta(product, spot, cache)
                option_delta[product] = delta
                self._trade_voucher(product, depth, spot, fair, delta, om)
            if velvet_depth is not None:
                self._hedge_only_if_extreme(velvet_depth, option_delta, om)

        cache["t"] = int(getattr(state, "timestamp", 0))
        if spot is not None:
            cache["spot"] = round(float(spot), 4)
        return result, 0, json.dumps(cache, separators=(",", ":"))

    def _trade_hydrogel(self, depth: OrderDepth, cache: Dict[str, Any], om: OrderManager) -> None:
        bid, ask, _, _ = self._best_bid_ask(depth)
        mid = self._mid(depth)
        if bid is None or ask is None or mid is None:
            return
        fair = self._ema(cache, "hp_fair", mid, 0.22)
        micro = self._microprice(depth)
        dom = self._dom_mid(depth)
        if micro is not None:
            fair += 0.30 * (micro - mid)
        if dom is not None:
            fair += 0.40 * (dom - mid)

        pos = om.pos(self.HYDROGEL)
        fair -= 0.075 * pos
        spread = ask - bid

        # V2 showed Hydrogel is the clean edge. Keep crossing rare, but quote
        # closer and larger than v2.
        if ask <= fair - 7.0:
            self._sweep_buy(self.HYDROGEL, depth, ask, min(18, max(3, int((fair - ask) * 1.6))), om)
        if bid >= fair + 7.0:
            self._sweep_sell(self.HYDROGEL, depth, bid, min(18, max(3, int((bid - fair) * 1.6))), om)

        if spread <= 24:
            bid_px = int(math.floor(fair - 3.25))
            ask_px = int(math.ceil(fair + 3.25))
            if spread > 2:
                bid_px = min(ask - 1, max(bid + 1, bid_px))
                ask_px = max(bid + 1, min(ask - 1, ask_px))
            else:
                bid_px = min(bid_px, bid)
                ask_px = max(ask_px, ask)
            buy_qty = self._scaled_qty(26, pos, self.LIMITS[self.HYDROGEL], 1)
            sell_qty = self._scaled_qty(26, pos, self.LIMITS[self.HYDROGEL], -1)
            if bid_px <= fair - 1.5:
                om.add(self.HYDROGEL, bid_px, buy_qty)
            if ask_px >= fair + 1.5:
                om.add(self.HYDROGEL, ask_px, -sell_qty)

    def _trade_voucher(self, product: str, depth: OrderDepth, spot: float, fair: float, delta: float, om: OrderManager) -> None:
        bid, ask, _, _ = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return
        pos = om.pos(product)

        if product in ("VEV_6000", "VEV_6500"):
            om.add(product, 0, min(20, self.LONG_CAP[product] - pos))
            if pos > 50:
                om.add(product, 1, -min(8, pos))
            return

        if product in ("VEV_4000", "VEV_4500"):
            self._trade_deep_itm(product, depth, spot, fair, om)
            return

        if product in ("VEV_5000", "VEV_5100", "VEV_5200", "VEV_5300"):
            self._trade_gamma_bid(product, depth, fair, om)
            return

        if product in ("VEV_5400", "VEV_5500"):
            self._trade_small_otm(product, depth, fair, om)

    def _trade_deep_itm(self, product: str, depth: OrderDepth, spot: float, fair: float, om: OrderManager) -> None:
        bid, ask, _, _ = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return
        pos = om.pos(product)
        intrinsic = max(spot - self.STRIKES[product], 0.0)
        cap = self.LONG_CAP[product]

        # V2 made money here, but one ask-take at 52900 had bad markout. Prefer
        # inside/passive bids; only cross when the no-arb edge is very large.
        if pos < cap:
            if ask < intrinsic - 4.0 and ask <= fair - 2.0:
                self._sweep_buy(product, depth, ask, min(4, cap - pos), om)
            spread = ask - bid
            bid_px = int(math.floor(intrinsic - 2.5))
            if spread > 1:
                bid_px = min(ask - 1, max(bid + 1, bid_px))
            else:
                bid_px = min(bid, bid_px)
            if bid_px <= intrinsic - 1.5:
                om.add(product, bid_px, min(8, cap - pos))

        if pos > 0 and bid >= fair + 2.5:
            self._sweep_sell(product, depth, bid, min(6, pos), om)

    def _trade_gamma_bid(self, product: str, depth: OrderDepth, fair: float, om: OrderManager) -> None:
        bid, ask, _, _ = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return
        pos = om.pos(product)
        cap = self.LONG_CAP[product]
        if pos >= cap:
            return

        spread = ask - bid
        if spread <= 0:
            return
        # Improve the bid by one tick when it still leaves positive edge to the
        # live-IV fair. This is the main v3 attempt to monetize passive option
        # fills without recreating v1 short-vol.
        edge_needed = {
            "VEV_5000": 1.25,
            "VEV_5100": 1.50,
            "VEV_5200": 0.65,
            "VEV_5300": 0.55,
        }[product]
        bid_px = bid + 1 if spread > 2 else bid
        if bid_px <= fair - edge_needed:
            om.add(product, bid_px, min(7, cap - pos))

        # Rare taker buy only on obvious underpricing.
        if ask <= fair - max(3.5, edge_needed + 2.0):
            self._sweep_buy(product, depth, ask, min(5, cap - om.pos(product)), om)

        # Reduce longs if the market overpays. No naked shorting.
        if om.pos(product) > 0 and bid >= fair + 1.5:
            self._sweep_sell(product, depth, bid, min(5, om.pos(product)), om)

    def _trade_small_otm(self, product: str, depth: OrderDepth, fair: float, om: OrderManager) -> None:
        bid, ask, _, _ = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return
        pos = om.pos(product)
        cap = self.LONG_CAP[product]
        if pos < cap and bid <= fair - 0.75:
            om.add(product, bid, min(4, cap - pos))
        if pos > 0 and bid >= fair + 1.0:
            self._sweep_sell(product, depth, bid, min(4, pos), om)

    def _hedge_only_if_extreme(self, depth: OrderDepth, option_delta: Dict[str, float], om: OrderManager) -> None:
        bid, ask, _, _ = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return
        net = float(om.pos(self.VELVET))
        for product, delta in option_delta.items():
            net += om.pos(product) * delta
        if net > 170:
            om.add(self.VELVET, ask, -min(12, int((net - 170) * 0.30)))
        elif net < -170:
            om.add(self.VELVET, bid, min(12, int((-170 - net) * 0.30)))

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
            ivs[product] = round(0.90 * float(old) + 0.10 * iv, 6) if isinstance(old, (int, float)) else round(iv, 6)

    def _option_fair_delta(self, product: str, spot: float, cache: Dict[str, Any]) -> Tuple[float, float]:
        strike = self.STRIKES[product]
        intrinsic = max(spot - strike, 0.0)
        if product in ("VEV_4000", "VEV_4500") and intrinsic > 350:
            return intrinsic + 0.1, 0.985 if product == "VEV_4000" else 0.955
        sigma = self._iv(cache, product)
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

    def _scaled_qty(self, base: int, pos: int, limit: int, side: int) -> int:
        pressure = max(0.0, pos / limit) if side > 0 else max(0.0, -pos / limit)
        return max(1, int(round(base * (1.0 - 0.55 * pressure))))

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
