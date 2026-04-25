from __future__ import annotations

import json
from math import exp, log, sqrt
from statistics import NormalDist
from typing import Any, Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ModuleNotFoundError:
    from prosperity3bt.datamodel import Order, OrderDepth, TradingState


_NDIST = NormalDist()


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
    HGP = "HYDROGEL_PACK"
    VE = "VELVETFRUIT_EXTRACT"
    VOUCHERS = [
        "VEV_4000", "VEV_4500", "VEV_5000", "VEV_5100", "VEV_5200",
        "VEV_5300", "VEV_5400", "VEV_5500", "VEV_6000", "VEV_6500",
    ]
    PRODUCTS = [HGP, VE] + VOUCHERS
    LIMITS = {HGP: 200, VE: 200, **{v: 300 for v in VOUCHERS}}

    HGP_FV = 10000
    HGP_EDGE = 2
    HGP_SOFT = 180
    HGP_OBI_THRESH = 0.15

    ANCHOR = {
        VE: 5262.0,
        "VEV_4000": 1265.0, "VEV_4500": 764.0, "VEV_5000": 266.0,
        "VEV_5100": 175.0, "VEV_5200": 101.0, "VEV_5300": 50.0,
        "VEV_5400": 16.0, "VEV_5500": 6.0,
        "VEV_6000": 0.5, "VEV_6500": 0.5,
    }
    SOFT = {
        VE: 130,
        "VEV_4000": 24, "VEV_4500": 24, "VEV_5000": 60,
        "VEV_5100": 75, "VEV_5200": 90, "VEV_5300": 110,
        "VEV_5400": 180, "VEV_5500": 140,
        "VEV_6000": 120, "VEV_6500": 120,
    }
    REG = {
        "VEV_4000": (-3998.27, 1.000, 0.83), "VEV_4500": (-4497.00, 0.999, 0.76),
        "VEV_5000": (-4550.49, 0.915, 1.40), "VEV_5100": (-3950.96, 0.784, 3.48),
        "VEV_5200": (-2871.36, 0.565, 3.92), "VEV_5300": (-1704.69, 0.334, 3.41),
        "VEV_5400": (-644.24, 0.126, 2.81), "VEV_5500": (-281.46, 0.055, 1.51),
    }
    STRIKE = {
        "VEV_4000": 4000, "VEV_4500": 4500, "VEV_5000": 5000, "VEV_5100": 5100,
        "VEV_5200": 5200, "VEV_5300": 5300, "VEV_5400": 5400, "VEV_5500": 5500,
        "VEV_6000": 6000, "VEV_6500": 6500,
    }
    DELTA_APPROX = {
        "VEV_4000": 1.00, "VEV_4500": 1.00, "VEV_5000": 0.93, "VEV_5100": 0.78,
        "VEV_5200": 0.57, "VEV_5300": 0.33, "VEV_5400": 0.13, "VEV_5500": 0.05,
        "VEV_6000": 0.01, "VEV_6500": 0.00,
    }
    CORE_BASKET = ("VEV_5000", "VEV_5100", "VEV_5200", "VEV_5300", "VEV_5400")

    SIGMA = 0.342
    TTE_DAYS = 5

    LADDER_TIERS_HGP = ((0, 1.0), (1, 0.5), (2, 0.3))
    LADDER_TIERS_VE = ((0, 1.0), (1, 0.5))

    def bid(self) -> int:
        return 1

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {p: [] for p in state.order_depths}
        om = OrderManager(state, self.LIMITS, result)
        cache = self._load_cache(state.traderData)
        self._update_state(state, cache)

        depth_hgp = state.order_depths.get(self.HGP)
        if depth_hgp is not None:
            self._trade_hgp(depth_hgp, cache, om)

        depth_ve = state.order_depths.get(self.VE)
        if depth_ve is not None:
            self._trade_ve(depth_ve, cache, om)

        for product in self.VOUCHERS:
            depth = state.order_depths.get(product)
            if depth is None:
                continue
            self._trade_voucher(product, depth, cache, om, state)

        self._trade_noarb_lower_bound(state, cache, om)
        self._delta_hedge(state, cache, om)

        cache["t"] = int(getattr(state, "timestamp", 0))
        return result, 0, json.dumps(cache, separators=(",", ":"))

    def _update_state(self, state: TradingState, cache: Dict[str, Any]) -> None:
        ve_depth = state.order_depths.get(self.VE)
        ve_mid = self._mid(ve_depth)
        if ve_mid is None:
            return
        cache["ve_mid"] = round(float(ve_mid), 4)
        fast = self._ema(cache, "ve_fast", ve_mid, 0.16)
        slow = self._ema(cache, "ve_slow", ve_mid, 0.025)
        cache["ve_mom"] = round(fast - slow, 6)
        ve_micro = self._microprice(ve_depth) if ve_depth is not None else None
        cache["ve_micro_edge"] = round((ve_micro - ve_mid) if ve_micro is not None else 0.0, 6)

        vals = []
        for product, (a, beta, std) in self.REG.items():
            mid = self._mid(state.order_depths.get(product))
            if mid is None:
                continue
            key = product.replace("_", "").lower()
            resid = mid - (a + beta * ve_mid)
            center = self._ema(cache, f"{key}_rv_center", resid, 0.018)
            z = (resid - center) / max(std, 0.75)
            cache[f"{key}_rv_z"] = round(z, 6)
            if product in self.CORE_BASKET:
                vals.append(z)
        if vals:
            vals.sort()
            trimmed = vals[1:-1] if len(vals) >= 3 else vals
            cache["rv_basket_z"] = round(sum(trimmed) / len(trimmed), 6)

        buy_q = sell_q = 0
        for product in self.CORE_BASKET:
            depth = state.order_depths.get(product)
            bid, ask, _, _ = self._best_bid_ask(depth)
            if bid is None or ask is None:
                continue
            for tr in getattr(state, "market_trades", {}).get(product, []):
                px = int(getattr(tr, "price", 0))
                qty = abs(int(getattr(tr, "quantity", 0)))
                if px >= ask:
                    buy_q += qty
                elif px <= bid:
                    sell_q += qty
        cache["bot_buy_qty"] = buy_q
        cache["bot_sell_qty"] = sell_q

    def _trade_hgp(self, depth: OrderDepth, cache: Dict[str, Any], om: OrderManager) -> None:
        bid, ask, bvol, avol = self._best_bid_ask(depth)
        mid = self._mid(depth)
        if bid is None or ask is None or mid is None:
            return
        spread = ask - bid
        if spread <= 0:
            return

        ema = self._ema(cache, "hgp_ema", mid, 0.05)
        fair = 0.7 * self.HGP_FV + 0.3 * ema
        obi = self._obi(bvol, avol)

        pos = om.pos(self.HGP)
        soft = self.HGP_SOFT
        skew = int(round(pos / max(self.LIMITS[self.HGP], 1) * self.HGP_EDGE * 2))
        fair -= 0.04 * pos

        if pos > -soft:
            for px, vol in sorted(depth.sell_orders.items()):
                if px < self.HGP_FV - 1 and pos < soft:
                    take = min(-int(vol), soft - pos)
                    if take > 0:
                        om.add(self.HGP, int(px), take)
                        pos = om.pos(self.HGP)
                else:
                    break
        if pos < soft:
            for px, vol in sorted(depth.buy_orders.items(), reverse=True):
                if px > self.HGP_FV + 1 and pos > -soft:
                    take = min(int(vol), pos + soft)
                    if take > 0:
                        om.add(self.HGP, int(px), -take)
                        pos = om.pos(self.HGP)
                else:
                    break

        for offset, frac in self.LADDER_TIERS_HGP:
            buy_px = int(self.HGP_FV) - self.HGP_EDGE - offset + skew
            sell_px = int(self.HGP_FV) + self.HGP_EDGE + offset + skew
            base_size = max(8, int(40 * frac))
            buy_size = base_size
            sell_size = base_size
            if obi > self.HGP_OBI_THRESH:
                buy_size = max(0, buy_size // 3)
            elif obi < -self.HGP_OBI_THRESH:
                sell_size = max(0, sell_size // 3)
            if pos > soft * 0.6:
                buy_size = 0
                sell_size = int(sell_size * 1.4)
            elif pos < -soft * 0.6:
                sell_size = 0
                buy_size = int(buy_size * 1.4)
            if buy_size > 0 and pos < soft:
                om.add(self.HGP, buy_px, min(buy_size, soft - pos))
            if sell_size > 0 and pos > -soft:
                om.add(self.HGP, sell_px, -min(sell_size, pos + soft))

    def _trade_ve(self, depth: OrderDepth, cache: Dict[str, Any], om: OrderManager) -> None:
        bid, ask, bvol, avol = self._best_bid_ask(depth)
        mid = self._mid(depth)
        if bid is None or ask is None or mid is None:
            return
        spread = ask - bid
        if spread <= 0:
            return

        ema = self._ema(cache, "ve_ema_slow", mid, 0.045)
        anchor = self.ANCHOR[self.VE]
        fair = 0.55 * anchor + 0.45 * ema
        obi = self._obi(bvol, avol)

        pos = om.pos(self.VE)
        soft = self.SOFT[self.VE]
        fair -= 0.030 * pos
        edge = 0.7

        for offset, frac in self.LADDER_TIERS_VE:
            buy_px = max(bid, int(round(fair - edge - offset)))
            sell_px = min(ask, int(round(fair + edge + offset)))
            buy_px = min(buy_px, ask - 1)
            sell_px = max(sell_px, bid + 1)
            base = max(6, int(24 * frac))
            buy_size = base
            sell_size = base
            if obi > 0.15:
                buy_size = max(0, buy_size // 3)
            elif obi < -0.15:
                sell_size = max(0, sell_size // 3)
            if pos > soft * 0.6:
                buy_size = 0
                sell_size = int(sell_size * 1.4)
            elif pos < -soft * 0.6:
                sell_size = 0
                buy_size = int(buy_size * 1.4)
            if buy_size > 0 and pos < soft and buy_px < fair:
                om.add(self.VE, buy_px, min(buy_size, soft - pos))
            if sell_size > 0 and pos > -soft and sell_px > fair:
                om.add(self.VE, sell_px, -min(sell_size, pos + soft))

    def _trade_voucher(
        self, product: str, depth: OrderDepth, cache: Dict[str, Any], om: OrderManager, state: TradingState
    ) -> None:
        bid, ask, bvol, avol = self._best_bid_ask(depth)
        mid = self._mid(depth)
        if bid is None or ask is None or mid is None:
            return

        if product in ("VEV_6000", "VEV_6500"):
            self._trade_floor_carry(product, depth, cache, om)
            return

        if product in ("VEV_4000", "VEV_4500"):
            self._trade_mr(product, depth, cache, om, alpha=0.025, inv=0.080, edge=4.0, size=8, taker=False)
            return
        if product == "VEV_5000":
            self._trade_mr(product, depth, cache, om, alpha=0.030, inv=0.090, edge=2.3, size=10, taker=True)
        elif product in ("VEV_5100", "VEV_5200", "VEV_5300"):
            self._trade_mr(product, depth, cache, om, alpha=0.035, inv=0.075, edge=1.4, size=14, taker=True)
        elif product == "VEV_5400":
            self._trade_mr(product, depth, cache, om, alpha=0.045, inv=0.040, edge=0.7, size=22, taker=True)
        elif product == "VEV_5500":
            self._trade_mr(product, depth, cache, om, alpha=0.050, inv=0.050, edge=0.6, size=14, taker=True)
        self._trade_property_mm(product, depth, cache, om)
        self._trade_bs_overlay(product, depth, cache, om)

    def _trade_floor_carry(self, product: str, depth: OrderDepth, cache: Dict[str, Any], om: OrderManager) -> None:
        bid, ask, _, _ = self._best_bid_ask(depth)
        if ask is None or ask > 1:
            return
        ve_mid = float(cache.get("ve_mid", 0.0))
        K = self.STRIKE[product]
        if ve_mid > 0 and ve_mid >= K - 200:
            return
        soft = self.SOFT[product]
        pos = om.pos(product)
        if pos <= -soft:
            return
        size_step = 8
        om.add(product, 1, -min(size_step, pos + soft))
        if bid is not None and bid >= 1:
            om.add(product, 2, -min(size_step // 2, pos + soft))

    def _trade_mr(
        self, product: str, depth: OrderDepth, cache: Dict[str, Any], om: OrderManager,
        alpha: float, inv: float, edge: float, size: int, taker: bool,
    ) -> None:
        bid, ask, bvol, avol = self._best_bid_ask(depth)
        mid = self._mid(depth)
        if bid is None or ask is None or mid is None:
            return
        spread = ask - bid
        if spread <= 0:
            return

        key = product.replace("_", "").lower()
        anchor = self.ANCHOR[product]
        ema = self._ema(cache, f"{key}_ema", mid, alpha)
        last = float(cache.get(f"{key}_last", mid))
        abs_move = self._ema(cache, f"{key}_abs", abs(mid - last), 0.08)
        cache[f"{key}_last"] = round(float(mid), 6)
        micro = self._microprice(depth)
        micro_edge = (micro - mid) if micro is not None else 0.0

        anchor_w = 0.70
        fair = anchor_w * anchor + (1 - anchor_w) * ema + 0.08 * micro_edge
        pos = om.pos(product)
        soft = self.SOFT[product]
        if soft <= 0:
            return
        fair -= inv * pos

        dyn_edge = edge + min(1.5 * edge, 0.25 * abs_move)
        if spread >= 3:
            buy_px, sell_px = bid + 1, ask - 1
        else:
            buy_px, sell_px = bid, ask

        richness = mid - fair
        buy_size = sell_size = size
        if richness > dyn_edge:
            buy_size = max(1, size // 4); sell_size = int(size * 1.6)
        elif richness < -dyn_edge:
            buy_size = int(size * 1.6); sell_size = max(1, size // 4)

        if pos > soft * 0.55:
            buy_size = 0; sell_size = int(size * 2.0)
        elif pos < -soft * 0.55:
            buy_size = int(size * 2.0); sell_size = 0

        basket_z = float(cache.get("rv_basket_z", 0.0))
        if product in self.CORE_BASKET:
            if basket_z < -0.85:
                buy_size = int(buy_size * 1.20)
                sell_size = max(1, int(sell_size * 0.80)) if sell_size > 0 else 0
            elif basket_z > 0.85:
                sell_size = int(sell_size * 1.30)
                buy_size = max(1, int(buy_size * 0.70)) if buy_size > 0 else 0

        if taker:
            if pos > -soft and bid >= fair + dyn_edge + spread:
                self._sweep_sell(product, depth, bid, min(sell_size, pos + soft), om)
                pos = om.pos(product)
            if pos < soft and ask <= fair - dyn_edge - spread:
                self._sweep_buy(product, depth, ask, min(buy_size, soft - pos), om)
                pos = om.pos(product)

        if pos < soft and buy_size > 0 and buy_px <= fair - dyn_edge:
            om.add(product, buy_px, min(buy_size, soft - pos))
        if pos > -soft and sell_size > 0 and sell_px >= fair + dyn_edge:
            om.add(product, sell_px, -min(sell_size, pos + soft))

    def _trade_property_mm(self, product: str, depth: OrderDepth, cache: Dict[str, Any], om: OrderManager) -> None:
        bid, ask, bvol, avol = self._best_bid_ask(depth)
        mid = self._mid(depth)
        if bid is None or ask is None or mid is None:
            return
        spread = ask - bid
        if spread < 2:
            return

        key = product.replace("_", "").lower()
        anchor = self.ANCHOR[product]
        ema = float(cache.get(f"{key}_ema", mid))
        ve_mid = float(cache.get("ve_mid", 0.0))

        if ve_mid > 0 and product in self.REG:
            a, beta, std = self.REG[product]
            resid = mid - (a + beta * ve_mid)
            resid_c = self._ema(cache, f"{key}_mm_resid", resid, 0.012)
            reg_fair = a + beta * ve_mid + resid_c
            rz = (resid - resid_c) / max(std, 0.75)
        else:
            reg_fair, rz = 0.7 * anchor + 0.3 * ema, 0.0

        fair = 0.65 * (0.7 * anchor + 0.3 * ema) + 0.35 * reg_fair

        obi = self._obi(bvol, avol)
        fair += 0.20 * obi * min(2.0, spread / 4.0)

        pos = om.pos(product)
        soft = self.SOFT[product]
        if soft <= 0 or abs(pos) > soft * 0.85:
            return

        edge = 1.0 + 0.25 * max(0.0, abs(rz) - 1.0)
        size = 6
        buy_px = bid + 1 if spread >= 3 else bid
        sell_px = ask - 1 if spread >= 3 else ask
        richness = mid - fair

        if richness < -edge and pos < soft:
            om.add(product, buy_px, min(size, soft - pos))
        if richness > edge and pos > -soft:
            om.add(product, sell_px, -min(size, pos + soft))

    def _trade_bs_overlay(self, product: str, depth: OrderDepth, cache: Dict[str, Any], om: OrderManager) -> None:
        if product not in ("VEV_5300", "VEV_5400", "VEV_5500"):
            return
        ve_mid = float(cache.get("ve_mid", 0.0))
        if ve_mid <= 0:
            return
        bid, ask, _, _ = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return
        T = self.TTE_DAYS / 365.0
        K = self.STRIKE[product]
        bs = self._bs_call(ve_mid, K, T, self.SIGMA)
        if bs <= 0:
            return
        pos = om.pos(product)
        soft = self.SOFT[product]
        threshold = 1.5 if product == "VEV_5400" else 2.0
        if ask <= bs - threshold and pos < soft:
            take = min(soft - pos, 6)
            self._sweep_buy(product, depth, ask, take, om)
        if bid >= bs + threshold and pos > -soft:
            take = min(pos + soft, 6)
            self._sweep_sell(product, depth, bid, take, om)

    def _trade_noarb_lower_bound(self, state: TradingState, cache: Dict[str, Any], om: OrderManager) -> None:
        ve_depth = state.order_depths.get(self.VE)
        ve_bid, _, _, _ = self._best_bid_ask(ve_depth)
        if ve_bid is None:
            return
        for product, threshold, qty in (("VEV_4000", 0.0, 6), ("VEV_4500", 0.0, 6)):
            depth = state.order_depths.get(product)
            bid, ask, _, ask_vol = self._best_bid_ask(depth)
            if bid is None or ask is None or ask_vol <= 0:
                continue
            lb = ve_bid - self.STRIKE[product]
            edge = lb - ask
            if edge < threshold:
                continue
            pos = om.pos(product)
            soft = min(self.SOFT[product], 36)
            if pos >= soft:
                continue
            buy_qty = min(qty, ask_vol, soft - pos)
            filled = om.add(product, ask, buy_qty)
            if filled > 0 and om.pos(self.VE) > -150:
                om.add(self.VE, ve_bid, -min(filled, 150 + om.pos(self.VE)))

    def _delta_hedge(self, state: TradingState, cache: Dict[str, Any], om: OrderManager) -> None:
        ve_depth = state.order_depths.get(self.VE)
        ve_bid, ve_ask, _, _ = self._best_bid_ask(ve_depth)
        if ve_bid is None or ve_ask is None:
            return
        net_delta = 0.0
        for v in self.VOUCHERS:
            d = self.DELTA_APPROX.get(v, 0.0)
            net_delta += om.pos(v) * d
        ve_pos = om.pos(self.VE)
        target_ve = -net_delta * 0.5
        gap = target_ve - ve_pos
        if abs(gap) < 12:
            return
        soft = 180
        if gap > 0:
            qty = min(int(gap), max(0, soft - ve_pos), 30)
            if qty > 0:
                om.add(self.VE, int(ve_ask), qty)
        else:
            qty = min(int(-gap), max(0, ve_pos + soft), 30)
            if qty > 0:
                om.add(self.VE, int(ve_bid), -qty)

    def _bs_call(self, S: float, K: float, T: float, sigma: float) -> float:
        if T <= 0 or sigma <= 0:
            return max(0.0, S - K)
        d1 = (log(S / K) + 0.5 * sigma * sigma * T) / (sigma * sqrt(T))
        d2 = d1 - sigma * sqrt(T)
        return S * _NDIST.cdf(d1) - K * _NDIST.cdf(d2)

    def _sweep_buy(self, product: str, depth: OrderDepth, max_price: int, qty: int, om: OrderManager) -> None:
        rem = max(0, int(qty))
        for price, vol in sorted(depth.sell_orders.items()):
            if rem <= 0 or price > max_price:
                break
            rem -= om.add(product, int(price), min(rem, -int(vol)))

    def _sweep_sell(self, product: str, depth: OrderDepth, min_price: int, qty: int, om: OrderManager) -> None:
        rem = max(0, int(qty))
        for price, vol in sorted(depth.buy_orders.items(), reverse=True):
            if rem <= 0 or price < min_price:
                break
            rem -= om.add(product, int(price), -min(rem, int(vol)))

    def _best_bid_ask(self, depth: Optional[OrderDepth]) -> Tuple[Optional[int], Optional[int], int, int]:
        if depth is None:
            return None, None, 0, 0
        bid = max(depth.buy_orders) if depth.buy_orders else None
        ask = min(depth.sell_orders) if depth.sell_orders else None
        bvol = int(depth.buy_orders[bid]) if bid is not None else 0
        avol = -int(depth.sell_orders[ask]) if ask is not None else 0
        return bid, ask, bvol, avol

    def _mid(self, depth: Optional[OrderDepth]) -> Optional[float]:
        bid, ask, _, _ = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2.0

    def _microprice(self, depth: OrderDepth) -> Optional[float]:
        bid, ask, bvol, avol = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return None
        total = bvol + avol
        if total <= 0:
            return (bid + ask) / 2.0
        return (ask * bvol + bid * avol) / total

    def _obi(self, bvol: int, avol: int) -> float:
        total = bvol + avol
        if total <= 0:
            return 0.0
        return (bvol - avol) / total

    def _ema(self, cache: Dict[str, Any], key: str, value: float, alpha: float) -> float:
        old = cache.get(key)
        new = (1 - alpha) * float(old) + alpha * value if isinstance(old, (int, float)) else value
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