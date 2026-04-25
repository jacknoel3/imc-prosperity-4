from __future__ import annotations

import json
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
    PRODUCTS = [VE] + VOUCHERS
    LIMITS = {
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

    # Empirical anchors from the best official-log sleeve (strat7/strat11).
    # This deliberately treats the vouchers as discrete bot products, not as
    # continuously hedgeable Black-Scholes claims.
    ANCHOR = {
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
        VE: 105,
        "VEV_4000": 18,
        "VEV_4500": 18,
        "VEV_5000": 45,
        "VEV_5100": 55,
        "VEV_5200": 70,
        "VEV_5300": 85,
        "VEV_5400": 130,
        "VEV_5500": 80,
        "VEV_6000": 0,
        "VEV_6500": 0,
    }
    # EDA full-sample relation: voucher_mid = a + beta * VE_mid.
    # These parameters are used for residual relative value only; the champion
    # anchor engine below remains the primary alpha source.
    REG = {
        "VEV_4000": (-3998.27, 1.000, 0.83),
        "VEV_4500": (-4497.00, 0.999, 0.76),
        "VEV_5000": (-4550.49, 0.915, 1.40),
        "VEV_5100": (-3950.96, 0.784, 3.48),
        "VEV_5200": (-2871.36, 0.565, 3.92),
        "VEV_5300": (-1704.69, 0.334, 3.41),
        "VEV_5400": (-644.24, 0.126, 2.81),
        "VEV_5500": (-281.46, 0.055, 1.51),
    }
    RV_PAIRS = (
        ("VEV_5100", "VEV_5200", 6),
        ("VEV_5200", "VEV_5300", 7),
        ("VEV_5300", "VEV_5400", 7),
        ("VEV_5000", "VEV_5100", 4),
    )
    CORE_RV = ("VEV_5000", "VEV_5100", "VEV_5200", "VEV_5300", "VEV_5400")

    def bid(self) -> int:
        return 1

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        om = OrderManager(state, self.LIMITS, result)
        cache = self._load_cache(state.traderData)
        timestamp = int(getattr(state, "timestamp", 0))
        self._update_rv_state(state, cache)

        for product in self.PRODUCTS:
            depth = state.order_depths.get(product)
            if depth is None:
                continue
            if product == self.VE:
                self._trade_mean_reversion(product, depth, cache, om, alpha=0.045, inv=0.030, edge=0.7, size=24, taker=False)
            else:
                self._trade_voucher_stat_arb(product, depth, cache, om, timestamp)
        self._trade_relative_value_overlay(state, cache, om, timestamp)

        cache["t"] = timestamp
        return result, 0, json.dumps(cache, separators=(",", ":"))

    def _update_rv_state(self, state: TradingState, cache: Dict[str, Any]) -> None:
        ve_depth = state.order_depths.get(self.VE)
        ve_mid = self._mid(ve_depth)
        if ve_mid is None:
            return
        fast = self._ema(cache, "rv_ve_fast", ve_mid, 0.16)
        slow = self._ema(cache, "rv_ve_slow", ve_mid, 0.025)
        cache["rv_ve_mom"] = round(fast - slow, 6)
        ve_micro = self._microprice(ve_depth) if ve_depth is not None else None
        cache["rv_ve_micro"] = round((ve_micro - ve_mid) if ve_micro is not None else 0.0, 6)

        basket_vals = []
        for product, (a, beta, std) in self.REG.items():
            depth = state.order_depths.get(product)
            mid = self._mid(depth)
            if mid is None:
                continue
            key = product.replace("_", "").lower()
            resid = mid - (a + beta * ve_mid)
            center = self._ema(cache, f"{key}_rv_center", resid, 0.018)
            z = (resid - center) / max(std, 0.75)
            cache[f"{key}_rv_z"] = round(z, 6)
            if product in self.CORE_RV:
                basket_vals.append(z)
        if basket_vals:
            basket_vals.sort()
            if len(basket_vals) >= 3:
                basket_vals = basket_vals[1:-1]
            cache["rv_basket_z"] = round(sum(basket_vals) / len(basket_vals), 6)

    def _trade_voucher_stat_arb(
        self, product: str, depth: OrderDepth, cache: Dict[str, Any], om: OrderManager, timestamp: int
    ) -> None:
        if product in ("VEV_6000", "VEV_6500"):
            return

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
        elif product == "VEV_5400":
            self._trade_mean_reversion(product, depth, cache, om, alpha=0.045, inv=0.040, edge=0.8, size=18, taker=True)
        else:
            self._trade_mean_reversion(product, depth, cache, om, alpha=0.050, inv=0.055, edge=1.0, size=10, taker=True)

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

        anchor_weight = 0.72 if product in self.VOUCHERS else 0.60
        fair = anchor_weight * anchor + (1.0 - anchor_weight) * ema
        fair += 0.08 * micro_edge
        pos = om.pos(product)
        soft = self.SOFT[product]
        if soft <= 0:
            return
        fair -= inv * pos

        dynamic_edge = edge + min(1.5 * edge, 0.25 * abs_move)
        if spread >= 3:
            buy_px = bid + 1
            sell_px = ask - 1
        else:
            buy_px = bid
            sell_px = ask

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

        # Deep-OBI/momentum is OOS-negative as a standalone taker signal in the
        # EDA. Use it only as a veto against adding positive-delta voucher risk
        # into a short, sharp underlying move.
        if product in ("VEV_5000", "VEV_5200", "VEV_5300", "VEV_5400"):
            ve_mom = float(cache.get("rv_ve_mom", 0.0))
            ve_micro = float(cache.get("rv_ve_micro", 0.0))
            basket_z = float(cache.get("rv_basket_z", 0.0))
            if ve_mom < -2.4 and ve_micro < -0.20 and basket_z > -0.95 and pos >= 0:
                buy_size = max(0, buy_size // 3)
            elif ve_mom > 2.4 and ve_micro > 0.20 and basket_z < 0.95 and pos <= 0:
                sell_size = max(0, sell_size // 3)

        if taker:
            if pos > -soft and bid >= fair + dynamic_edge + spread:
                self._sweep_sell(product, depth, bid, min(sell_size, pos + soft), om)
                pos = om.pos(product)
            if pos < soft and ask <= fair - dynamic_edge - spread:
                self._sweep_buy(product, depth, ask, min(buy_size, soft - pos), om)
                pos = om.pos(product)

        if pos < soft and buy_size > 0 and buy_px <= fair - dynamic_edge:
            om.add(product, buy_px, min(buy_size, soft - pos))
        if pos > -soft and sell_size > 0 and sell_px >= fair + dynamic_edge:
            om.add(product, sell_px, -min(sell_size, pos + soft))

    def _trade_relative_value_overlay(
        self, state: TradingState, cache: Dict[str, Any], om: OrderManager, timestamp: int
    ) -> None:
        if timestamp < 2500:
            return
        basket_z = float(cache.get("rv_basket_z", 0.0))
        ve_mom = float(cache.get("rv_ve_mom", 0.0))
        ve_micro = float(cache.get("rv_ve_micro", 0.0))
        toxic_down = ve_mom < -2.8 and ve_micro < -0.25
        toxic_up = ve_mom > 2.8 and ve_micro > 0.25

        for rich, cheap, base_size in self.RV_PAIRS:
            rich_z = float(cache.get(f"{rich.replace('_', '').lower()}_rv_z", 0.0))
            cheap_z = float(cache.get(f"{cheap.replace('_', '').lower()}_rv_z", 0.0))
            spread_z = rich_z - cheap_z
            if spread_z > 1.25 and rich_z > 0.35 and cheap_z < 0.10:
                self._rv_sell_buy(rich, cheap, base_size, state, om, block_buy=toxic_down and basket_z > -0.75)
            elif spread_z < -1.25 and cheap_z > 0.35 and rich_z < 0.10:
                self._rv_sell_buy(cheap, rich, base_size, state, om, block_buy=toxic_down and basket_z > -0.75)

        # Asymmetric gamma/VRP overlay: only add tiny passive gamma when the
        # option complex is cheap and the underlying is moving, never as a
        # standalone volatility bet.
        if basket_z < -0.90 and not toxic_down and abs(ve_mom) > 1.4:
            for product, qty in (("VEV_5200", 3), ("VEV_5300", 4), ("VEV_5400", 3)):
                self._passive_one_sided(product, state, om, qty, buy=True)
        elif basket_z > 0.95 and not toxic_up and abs(ve_mom) > 1.4:
            for product, qty in (("VEV_5200", 3), ("VEV_5300", 4), ("VEV_5400", 3)):
                self._passive_one_sided(product, state, om, qty, buy=False)

    def _rv_sell_buy(
        self, rich: str, cheap: str, size: int, state: TradingState, om: OrderManager, block_buy: bool
    ) -> None:
        rich_depth = state.order_depths.get(rich)
        cheap_depth = state.order_depths.get(cheap)
        if rich_depth is None or cheap_depth is None:
            return
        _, rich_beta, _ = self.REG[rich]
        _, cheap_beta, _ = self.REG[cheap]
        rich_qty = max(1, int(round(size * cheap_beta / max(rich_beta, 0.05))))
        cheap_qty = size
        self._passive_one_sided(rich, state, om, rich_qty, buy=False)
        if not block_buy:
            self._passive_one_sided(cheap, state, om, cheap_qty, buy=True)

    def _passive_one_sided(self, product: str, state: TradingState, om: OrderManager, qty: int, buy: bool) -> None:
        depth = state.order_depths.get(product)
        bid, ask, _, _ = self._best_bid_ask(depth)
        if bid is None or ask is None or qty <= 0:
            return
        spread = ask - bid
        pos = om.pos(product)
        soft = self.SOFT.get(product, 0)
        if soft <= 0:
            return
        if buy:
            if pos >= soft:
                return
            price = min(ask - 1, bid + 1) if spread >= 2 else bid
            om.add(product, price, min(qty, soft - pos))
        else:
            if pos <= -soft:
                return
            price = max(bid + 1, ask - 1) if spread >= 2 else ask
            om.add(product, price, -min(qty, pos + soft))

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