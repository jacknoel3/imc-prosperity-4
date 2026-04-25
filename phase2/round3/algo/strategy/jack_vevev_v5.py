from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, Trade, TradingState
except ModuleNotFoundError:
    from prosperity3bt.datamodel import Order, OrderDepth, Trade, TradingState


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
    VOUCHERS = ["VEV_5200", "VEV_5300", "VEV_5400"]
    SURFACE_PRODUCTS = ["VEV_5000", "VEV_5100", "VEV_5200", "VEV_5300", "VEV_5400", "VEV_5500"]
    STRIKES = {
        "VEV_5000": 5000.0,
        "VEV_5100": 5100.0,
        "VEV_5200": 5200.0,
        "VEV_5300": 5300.0,
        "VEV_5400": 5400.0,
        "VEV_5500": 5500.0,
    }
    LIMITS = {
        VELVET: 200,
        "VEV_5200": 300,
        "VEV_5300": 300,
        "VEV_5400": 300,
    }

    TTE_YEARS = 5.0 / 365.0
    FAIR_VOL = 0.34
    IV_EMA_ALPHA = 0.08
    NO_NEW_BUYS_AFTER = 90_000
    CORE_FALLBACK_AFTER = 20_000
    SURFACE_EMA_ALPHA = 0.12

    LONG_CAP = {
        "VEV_5200": 45,
        "VEV_5300": 6,
        "VEV_5400": 3,
    }
    EDGE_TO_BID = {
        "VEV_5200": 1.5,
        "VEV_5300": 2.5,
        "VEV_5400": 2.0,
    }
    SURFACE_EDGE = {
        "VEV_5200": 1.5,
        "VEV_5300": 1.5,
        "VEV_5400": 1.5,
    }
    ORDER_SIZE = {
        "VEV_5200": 5,
        "VEV_5300": 2,
        "VEV_5400": 1,
    }
    PROFIT_TICKS = {
        "VEV_5200": 2.0,
        "VEV_5300": 1.25,
        "VEV_5400": 1.0,
    }
    STOP_ADD_MARKOUT = {
        "VEV_5200": -4.0,
        "VEV_5300": -2.0,
        "VEV_5400": -1.5,
    }

    HEDGE_PASSIVE_THRESHOLD = 55.0
    HEDGE_TAKER_THRESHOLD = 130.0
    HEDGE_MAX_SLICE = 14

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        om = OrderManager(state, self.LIMITS, result)
        cache = self._load_cache(state.traderData)
        self._update_entry_cache(cache, state)

        velvet_depth = state.order_depths.get(self.VELVET)
        spot = self._mid(velvet_depth)
        if spot is None:
            return result, 0, json.dumps(cache, separators=(",", ":"))

        timestamp = int(getattr(state, "timestamp", 0))
        self._update_iv_cache(cache, state, spot)
        surface_fairs = self._surface_fairs(cache, state, spot)
        deltas: Dict[str, float] = {}

        for product in self.VOUCHERS:
            depth = state.order_depths.get(product)
            if depth is None:
                continue
            fair_vol = self._fair_vol(cache, product)
            fair = self._bs_call(spot, self.STRIKES[product], fair_vol)
            delta = self._bs_delta(spot, self.STRIKES[product], fair_vol)
            deltas[product] = delta
            surface_fair = surface_fairs.get(product)
            self._trade_voucher(product, depth, fair, surface_fair, timestamp, cache, om)

        if velvet_depth is not None:
            self._trade_velvet_hedge(velvet_depth, deltas, om)

        self._sync_entry_cache_positions(cache, state)
        cache["spot"] = round(float(spot), 4)
        cache["t"] = timestamp
        return result, 0, json.dumps(cache, separators=(",", ":"))

    def _trade_voucher(
        self,
        product: str,
        depth: OrderDepth,
        fair: float,
        surface_fair: Optional[float],
        timestamp: int,
        cache: Dict[str, Any],
        om: OrderManager,
    ) -> None:
        bid, ask, _, _ = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return

        pos = om.pos(product)
        avg_entry = self._avg_entry(cache, product)
        if pos > 0:
            sell_qty = self._profit_take_qty(product, bid, fair, avg_entry, pos)
            if sell_qty > 0:
                self._sweep_sell(product, depth, bid, sell_qty, om)
                pos = om.pos(product)

        if timestamp >= self.NO_NEW_BUYS_AFTER or pos >= self.LONG_CAP[product]:
            return

        mid = (bid + ask) / 2.0
        if avg_entry is not None and pos > 0 and mid - avg_entry <= self.STOP_ADD_MARKOUT[product]:
            return

        spread = ask - bid
        bid_px = bid + 1 if spread > 1 else bid
        passes_gate = self._passes_surface_gate(product, bid_px, fair, surface_fair)
        if (
            not passes_gate
            and product in ("VEV_5200", "VEV_5300")
            and timestamp >= self.CORE_FALLBACK_AFTER
            and pos == 0
            and bid_px <= fair - self.EDGE_TO_BID[product]
        ):
            passes_gate = True
        if not passes_gate:
            return
        if bid_px <= fair - self.EDGE_TO_BID[product]:
            qty = min(self.ORDER_SIZE[product], self.LONG_CAP[product] - pos)
            om.add(product, bid_px, qty)

    def _passes_surface_gate(
        self, product: str, bid_px: int, fair: float, surface_fair: Optional[float]
    ) -> bool:
        if surface_fair is None:
            return product != "VEV_5400" and bid_px <= fair - self.EDGE_TO_BID[product]
        cheap_to_model = bid_px <= fair - self.EDGE_TO_BID[product]
        if product == "VEV_5400":
            cheap_to_surface = bid_px <= surface_fair - self.SURFACE_EDGE[product]
        else:
            cheap_to_surface = bid_px <= surface_fair + self.SURFACE_EDGE[product]
        return cheap_to_model and cheap_to_surface

    def _profit_take_qty(self, product: str, bid: int, fair: float, avg_entry: Optional[float], pos: int) -> int:
        if pos <= 0:
            return 0
        if avg_entry is not None and bid >= avg_entry + self.PROFIT_TICKS[product]:
            return min(5, max(1, pos // 2))
        if bid >= fair + 0.5:
            return min(4, pos)
        return 0

    def _trade_velvet_hedge(self, depth: OrderDepth, deltas: Dict[str, float], om: OrderManager) -> None:
        bid, ask, _, _ = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return

        net_delta = float(om.pos(self.VELVET))
        for product, delta in deltas.items():
            net_delta += float(om.pos(product)) * float(delta)

        if abs(net_delta) < self.HEDGE_PASSIVE_THRESHOLD:
            return

        qty = min(self.HEDGE_MAX_SLICE, max(1, int((abs(net_delta) - self.HEDGE_PASSIVE_THRESHOLD) * 0.30)))
        spread = ask - bid
        if net_delta > 0:
            price = bid if net_delta > self.HEDGE_TAKER_THRESHOLD else ask - 1 if spread > 2 else ask
            om.add(self.VELVET, price, -qty)
        else:
            price = ask if net_delta < -self.HEDGE_TAKER_THRESHOLD else bid + 1 if spread > 2 else bid
            om.add(self.VELVET, price, qty)

    def _update_entry_cache(self, cache: Dict[str, Any], state: TradingState) -> None:
        entries = cache.setdefault("entry", {})
        if not isinstance(entries, dict):
            entries = {}
            cache["entry"] = entries
        seen = cache.setdefault("seen_trades", [])
        if not isinstance(seen, list):
            seen = []
            cache["seen_trades"] = seen
        seen_set = set(str(x) for x in seen)

        for product in self.VOUCHERS:
            for trade in state.own_trades.get(product, []):
                key = self._trade_key(product, trade)
                if key in seen_set:
                    continue
                signed_qty = self._own_trade_signed_qty(trade)
                if signed_qty == 0:
                    continue
                self._apply_entry_fill(entries, product, float(trade.price), int(signed_qty))
                seen_set.add(key)

        cache["seen_trades"] = list(seen_set)[-80:]

    def _trade_key(self, product: str, trade: Trade) -> str:
        return (
            f"{product}:{getattr(trade, 'timestamp', 0)}:{getattr(trade, 'price', 0)}:"
            f"{getattr(trade, 'quantity', 0)}:{getattr(trade, 'buyer', '')}:{getattr(trade, 'seller', '')}"
        )

    def _own_trade_signed_qty(self, trade: Trade) -> int:
        if getattr(trade, "buyer", None) == "SUBMISSION":
            return int(trade.quantity)
        if getattr(trade, "seller", None) == "SUBMISSION":
            return -int(trade.quantity)
        return 0

    def _apply_entry_fill(self, entries: Dict[str, Any], product: str, price: float, signed_qty: int) -> None:
        item = entries.get(product)
        if not isinstance(item, dict):
            item = {"qty": 0, "avg": 0.0}
        qty = int(item.get("qty", 0))
        avg = float(item.get("avg", 0.0))
        if signed_qty > 0:
            new_qty = qty + signed_qty
            new_avg = ((avg * qty) + price * signed_qty) / new_qty if new_qty > 0 else 0.0
            entries[product] = {"qty": new_qty, "avg": round(new_avg, 6)}
        else:
            new_qty = max(0, qty + signed_qty)
            entries[product] = {"qty": new_qty, "avg": round(avg if new_qty > 0 else 0.0, 6)}

    def _sync_entry_cache_positions(self, cache: Dict[str, Any], state: TradingState) -> None:
        entries = cache.setdefault("entry", {})
        if not isinstance(entries, dict):
            return
        for product in self.VOUCHERS:
            pos = int(state.position.get(product, 0))
            item = entries.get(product)
            if pos <= 0:
                entries[product] = {"qty": 0, "avg": 0.0}
            elif isinstance(item, dict):
                item["qty"] = pos

    def _avg_entry(self, cache: Dict[str, Any], product: str) -> Optional[float]:
        item = cache.get("entry", {}).get(product)
        if isinstance(item, dict) and int(item.get("qty", 0)) > 0:
            avg = float(item.get("avg", 0.0))
            if avg > 0:
                return avg
        return None

    def _update_iv_cache(self, cache: Dict[str, Any], state: TradingState, spot: float) -> None:
        ivs = cache.setdefault("iv", {})
        if not isinstance(ivs, dict):
            ivs = {}
            cache["iv"] = ivs

        for product in self.VOUCHERS:
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

    def _surface_fairs(self, cache: Dict[str, Any], state: TradingState, spot: float) -> Dict[str, float]:
        raw_ivs: Dict[str, float] = {}
        for product in self.SURFACE_PRODUCTS:
            depth = state.order_depths.get(product)
            mid = self._mid(depth)
            if mid is None:
                continue
            iv = self._implied_vol(spot, self.STRIKES[product], mid)
            if iv is not None and 0.05 <= iv <= 1.0:
                raw_ivs[product] = float(iv)

        if len(raw_ivs) < 2:
            return {}

        fit_products = [product for product in self.SURFACE_PRODUCTS if product in raw_ivs]
        strikes = [self.STRIKES[product] for product in fit_products]
        ivs = [raw_ivs[product] for product in fit_products]
        fitted_ivs = self._quadratic_fit_values(strikes, ivs)
        if not fitted_ivs:
            return {}

        surface = cache.setdefault("surface", {})
        if not isinstance(surface, dict):
            surface = {}
            cache["surface"] = surface

        out: Dict[str, float] = {}
        for product, fit_iv in fitted_ivs.items():
            if product not in self.VOUCHERS:
                continue
            fit_iv = min(1.0, max(0.05, float(fit_iv)))
            fit_fair = self._bs_call(spot, self.STRIKES[product], fit_iv)
            old = surface.get(product)
            if isinstance(old, (int, float)):
                fair = (1.0 - self.SURFACE_EMA_ALPHA) * float(old) + self.SURFACE_EMA_ALPHA * fit_fair
            else:
                fair = fit_fair
            surface[product] = round(float(fair), 6)
            out[product] = float(fair)
        return out

    def _quadratic_fit_values(self, strikes: List[float], values: List[float]) -> Dict[str, float]:
        if len(strikes) != len(values) or len(strikes) < 2:
            return {}
        x0 = sum(strikes) / len(strikes)
        xs = [(x - x0) / 100.0 for x in strikes]
        ys = list(values)

        if len(xs) == 2:
            x1, x2 = xs
            y1, y2 = ys
            if abs(x2 - x1) < 1e-9:
                return {}
            slope = (y2 - y1) / (x2 - x1)
            intercept = y1 - slope * x1
            return {
                product: intercept + slope * ((self.STRIKES[product] - x0) / 100.0)
                for product in self.SURFACE_PRODUCTS
            }

        s0 = float(len(xs))
        s1 = sum(xs)
        s2 = sum(x * x for x in xs)
        s3 = sum(x * x * x for x in xs)
        s4 = sum(x * x * x * x for x in xs)
        t0 = sum(ys)
        t1 = sum(x * y for x, y in zip(xs, ys))
        t2 = sum(x * x * y for x, y in zip(xs, ys))
        coeffs = self._solve_3x3([[s0, s1, s2], [s1, s2, s3], [s2, s3, s4]], [t0, t1, t2])
        if coeffs is None:
            return {}
        a, b, c = coeffs
        return {
            product: a + b * ((self.STRIKES[product] - x0) / 100.0) + c * ((self.STRIKES[product] - x0) / 100.0) ** 2
            for product in self.SURFACE_PRODUCTS
        }

    def _solve_3x3(self, matrix: List[List[float]], vector: List[float]) -> Optional[Tuple[float, float, float]]:
        a = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
        for col in range(3):
            pivot = max(range(col, 3), key=lambda row: abs(a[row][col]))
            if abs(a[pivot][col]) < 1e-9:
                return None
            if pivot != col:
                a[col], a[pivot] = a[pivot], a[col]
            scale = a[col][col]
            for j in range(col, 4):
                a[col][j] /= scale
            for row in range(3):
                if row == col:
                    continue
                factor = a[row][col]
                for j in range(col, 4):
                    a[row][j] -= factor * a[col][j]
        return float(a[0][3]), float(a[1][3]), float(a[2][3])

    def _fair_vol(self, cache: Dict[str, Any], product: str) -> float:
        iv = cache.get("iv", {}).get(product)
        if isinstance(iv, (int, float)) and 0.05 <= float(iv) <= 1.0:
            return max(self.FAIR_VOL, float(iv) + 0.04)
        return self.FAIR_VOL

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
