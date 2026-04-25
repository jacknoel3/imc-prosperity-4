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
    # EDA-derived relation to the underlying. This is used only as a guardrail
    # for the selective market-making overlay; the strat22 alpha engine remains
    # the primary signal.
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
    MM = {
        "VEV_4000": {"mode": "obi_wide", "size": 2, "edge": 2.8, "min_spread": 15, "obi": 0.65},
        "VEV_4500": {"mode": "obi_wide", "size": 2, "edge": 2.4, "min_spread": 11, "obi": 0.55},
        "VEV_5000": {"mode": "small_two_sided", "size": 2, "edge": 1.6, "min_spread": 5, "obi": 0.25},
        "VEV_5100": {"mode": "push_5100", "size": 8, "edge": 0.8, "min_spread": 3, "obi": 0.25},
        "VEV_5200": {"mode": "confirm_only", "size": 2, "edge": 1.7, "min_spread": 4, "obi": 0.12},
        "VEV_5300": {"mode": "bid_only", "size": 6, "edge": 0.6, "min_spread": 2, "obi": 0.10},
        "VEV_5400": {"mode": "tiny_bid", "size": 3, "edge": 0.7, "min_spread": 1, "obi": 0.05},
        "VEV_5500": {"mode": "tiny_bid", "size": 3, "edge": 0.9, "min_spread": 1, "obi": 0.00},
    }
    CORE_BASKET = ("VEV_5000", "VEV_5100", "VEV_5200", "VEV_5300", "VEV_5400")
    # Live round has one fewer day to expiry than the day-2 historical logs.
    # Apply this only to the MM overlay so the champion core is not damaged.
    THETA_TTE5 = {
        "VEV_4000": -0.69,
        "VEV_4500": -0.41,
        "VEV_5000": -1.19,
        "VEV_5100": -2.91,
        "VEV_5200": -5.02,
        "VEV_5300": -5.31,
        "VEV_5400": -3.15,
        "VEV_5500": -1.77,
    }
    THETA_BLEND = 0.30

    def bid(self) -> int:
        return 1

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        om = OrderManager(state, self.LIMITS, result)
        cache = self._load_cache(state.traderData)
        timestamp = int(getattr(state, "timestamp", 0))
        self._update_equity_regime(state, cache)

        for product in self.PRODUCTS:
            depth = state.order_depths.get(product)
            if depth is None:
                continue
            if product == self.VE:
                self._trade_mean_reversion(product, depth, cache, om, alpha=0.045, inv=0.030, edge=0.7, size=24, taker=False)
            else:
                self._trade_voucher_stat_arb(product, depth, cache, om, timestamp)

        cache["t"] = timestamp
        return result, 0, json.dumps(cache, separators=(",", ":"))

    def _update_equity_regime(self, state: TradingState, cache: Dict[str, Any]) -> None:
        last_counted = int(cache.get("last_own_trade_ts", -1))
        max_seen = last_counted
        for product, trades in getattr(state, "own_trades", {}).items():
            if product not in self.PRODUCTS:
                continue
            cash_key = f"cash_{product}"
            cash = float(cache.get(cash_key, 0.0))
            for trade in trades:
                trade_ts = int(getattr(trade, "timestamp", -1))
                if trade_ts <= last_counted:
                    continue
                price = float(getattr(trade, "price", 0.0))
                qty = abs(int(getattr(trade, "quantity", 0)))
                buyer = getattr(trade, "buyer", "")
                seller = getattr(trade, "seller", "")
                if buyer == "SUBMISSION":
                    cash -= price * qty
                if seller == "SUBMISSION":
                    cash += price * qty
                if trade_ts > max_seen:
                    max_seen = trade_ts
            cache[cash_key] = round(cash, 6)
        cache["last_own_trade_ts"] = max_seen

        equity = 0.0
        for product in self.PRODUCTS:
            depth = state.order_depths.get(product)
            mid = self._mid(depth)
            if mid is None:
                continue
            pos = int(state.position.get(product, 0))
            equity += float(cache.get(f"cash_{product}", 0.0)) + pos * mid

        prev = cache.get("equity")
        ret = equity - float(prev) if isinstance(prev, (int, float)) else 0.0
        ret_ema = self._ema(cache, "equity_ret_ema", ret, 0.22)
        peak = max(float(cache.get("equity_peak", equity)), equity)
        cache["equity"] = round(equity, 6)
        cache["equity_peak"] = round(peak, 6)
        drawdown = max(0.0, peak - equity)
        cache["equity_dd"] = round(drawdown, 6)

        neg_streak = int(cache.get("neg_equity_streak", 0))
        pos_streak = int(cache.get("pos_equity_streak", 0))
        if ret < -35:
            neg_streak += 1
            pos_streak = 0
        elif ret > 35:
            pos_streak += 1
            neg_streak = 0
        else:
            neg_streak = max(0, neg_streak - 1)
            pos_streak = max(0, pos_streak - 1)
        cache["neg_equity_streak"] = neg_streak
        cache["pos_equity_streak"] = pos_streak

        risk_mult = 1.0
        if drawdown > 1900 and ret_ema < 0:
            risk_mult = 0.35
        elif drawdown > 1350 and (ret_ema < -12 or neg_streak >= 2):
            risk_mult = 0.50
        elif drawdown > 800 and (ret_ema < -25 or neg_streak >= 3):
            risk_mult = 0.70

        momentum_mult = 1.0
        if ret_ema > 35 or pos_streak >= 2:
            momentum_mult = 1.12
        if drawdown > 1500:
            momentum_mult = min(momentum_mult, 1.0)

        cache["risk_mult"] = round(risk_mult, 4)
        cache["momentum_mult"] = round(momentum_mult, 4)

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
        self._trade_property_mm(product, depth, cache, om)

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

        risk_mult = float(cache.get("risk_mult", 1.0))
        momentum_mult = float(cache.get("momentum_mult", 1.0))
        if product in self.VOUCHERS:
            buy_scale = 1.0 if pos < 0 else risk_mult
            sell_scale = 1.0 if pos > 0 else risk_mult
            if risk_mult >= 0.95:
                buy_scale *= momentum_mult
                sell_scale *= momentum_mult
            if product == "VEV_5200" and risk_mult < 0.75:
                buy_scale *= 0.55
                sell_scale *= 0.75
            if product == "VEV_5000" and risk_mult < 0.75:
                buy_scale *= 0.70
            buy_size = int(buy_size * buy_scale) if buy_size > 0 else 0
            sell_size = int(sell_size * sell_scale) if sell_size > 0 else 0
            if 0 < buy_size < 1:
                buy_size = 1
            if 0 < sell_size < 1:
                sell_size = 1

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

    def _trade_property_mm(self, product: str, depth: OrderDepth, cache: Dict[str, Any], om: OrderManager) -> None:
        params = self.MM.get(product)
        if params is None:
            return
        bid, ask, bid_vol, ask_vol = self._best_bid_ask(depth)
        mid = self._mid(depth)
        if bid is None or ask is None or mid is None:
            return
        spread = ask - bid
        if spread < int(params["min_spread"]):
            return

        key = product.replace("_", "").lower()
        anchor = self.ANCHOR[product]
        ema = float(cache.get(f"{key}_ema", mid))
        micro = self._microprice(depth)
        micro_edge = (micro - mid) if micro is not None else 0.0
        champion_fair = 0.72 * anchor + 0.28 * ema + 0.08 * micro_edge

        reg_fair = champion_fair
        ve_mid = float(cache.get("velvetfruitextract_last", 0.0))
        if ve_mid > 0 and product in self.REG:
            a, beta, std = self.REG[product]
            resid = mid - (a + beta * ve_mid)
            resid_center = self._ema(cache, f"{key}_mm_resid", resid, 0.012)
            reg_fair = a + beta * ve_mid + resid_center
            residual_z = (resid - resid_center) / max(std, 0.75)
        else:
            residual_z = 0.0

        obi = self._obi(bid_vol, ask_vol)
        fair = 0.72 * champion_fair + 0.28 * reg_fair
        fair += self.THETA_BLEND * self.THETA_TTE5.get(product, 0.0)
        fair += float(params["obi"]) * obi * min(2.0, spread / 4.0)

        pos = om.pos(product)
        soft = self.SOFT[product]
        if soft <= 0 or abs(pos) > soft * 0.82:
            return
        cache[f"{key}_mm_z"] = round(float(residual_z), 6)
        basket_z = self._cached_basket_z(cache)
        risk_mult = float(cache.get("risk_mult", 1.0))
        momentum_mult = float(cache.get("momentum_mult", 1.0))

        edge = float(params["edge"]) + 0.25 * max(0.0, abs(residual_z) - 1.0)
        size = int(params["size"])
        mode = str(params["mode"])
        if risk_mult < 0.95:
            edge += 0.35 * (1.0 - risk_mult)
            if mode in ("push_5100", "small_two_sided"):
                size = max(1, int(size * risk_mult))
        elif momentum_mult > 1.0 and mode == "push_5100":
            size = int(size * momentum_mult)

        buy_px = min(ask - 1, bid + 1) if spread >= 3 else bid
        sell_px = max(bid + 1, ask - 1) if spread >= 3 else ask
        richness = mid - fair

        if mode == "obi_wide":
            if obi > 0.10 and richness < edge and pos < soft:
                om.add(product, buy_px, min(size, soft - pos))
            if obi < -0.10 and richness > -edge and pos > -soft:
                om.add(product, sell_px, -min(size, pos + soft))
            return

        if mode == "push_5100":
            if basket_z < -0.35 and residual_z < 0.65 and richness < edge and pos < soft:
                boost = 2 if residual_z < -0.70 or basket_z < -0.70 else 1
                om.add(product, buy_px, min(size * boost, soft - pos))
            if basket_z > 0.35 and residual_z > -0.65 and richness > -edge and pos > -soft:
                boost = 2 if residual_z > 0.70 or basket_z > 0.70 else 1
                om.add(product, sell_px, -min(size * boost, pos + soft))
            return

        if mode == "confirm_only":
            if basket_z < -0.85 and residual_z < -0.65 and richness < edge and pos < soft:
                om.add(product, buy_px, min(size, soft - pos))
            if basket_z > 0.85 and residual_z > 0.65 and richness > -edge and pos > -soft:
                om.add(product, sell_px, -min(size, pos + soft))
            return

        if mode in ("bid_only", "tiny_bid"):
            if basket_z < 0.45 and residual_z < 0.35 and richness < edge and pos < soft:
                om.add(product, buy_px, min(size, soft - pos))
            return

        if richness < edge and basket_z < 0.80 and pos < soft:
            om.add(product, buy_px, min(size, soft - pos))
        if richness > -edge and basket_z > -0.80 and pos > -soft:
            om.add(product, sell_px, -min(size, pos + soft))

    def _cached_basket_z(self, cache: Dict[str, Any]) -> float:
        vals = []
        for product in self.CORE_BASKET:
            val = cache.get(f"{product.replace('_', '').lower()}_mm_z")
            if isinstance(val, (int, float)):
                vals.append(float(val))
        if not vals:
            return 0.0
        vals.sort()
        if len(vals) >= 3:
            vals = vals[1:-1]
        return sum(vals) / len(vals)

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

    def _obi(self, bid_vol: int, ask_vol: int) -> float:
        total = bid_vol + ask_vol
        if total <= 0:
            return 0.0
        return (bid_vol - ask_vol) / total

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