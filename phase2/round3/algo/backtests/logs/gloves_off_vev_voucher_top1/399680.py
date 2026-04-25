from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ModuleNotFoundError:
    from prosperity3bt.datamodel import Order, OrderDepth, TradingState


Number = float


class Trader:
    """Round 3 VEV/voucher strategy built from the EDA evidence.

    Core ideas:
    - VEV vouchers are calls on VELVETFRUIT_EXTRACT.
    - Historical IV sits far below realized volatility, but continuous hedging is
      too expensive, so gamma exposure is only hedged at coarse thresholds.
    - Deep ITM vouchers occasionally violate the executable lower bound
      C >= S - K. Those are treated as primary no-arb opportunities.
    - VEV_6000 and VEV_6500 were a stable 0/1 floor historically; we only bid 0
      or sell down inventory at 1, never build naked short tail risk.
    - Microstructure alpha on the underlying is real but small, so it biases
      quoting/hedging rather than driving large directional taker trades.
    """

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
    SQRT_TTE = math.sqrt(TTE)
    MIN_SIGMA = 0.05
    MAX_SIGMA = 1.20

    # Strike-specific vols are deliberately conservative. They anchor the fair
    # surface to the observed ~23.5% ATM IV, with a mild right-wing lift, while
    # avoiding fragile overfit from the 3 historical days.
    STRIKE_IV = {
        "VEV_4000": 0.18,
        "VEV_4500": 0.19,
        "VEV_5000": 0.215,
        "VEV_5100": 0.225,
        "VEV_5200": 0.235,
        "VEV_5300": 0.238,
        "VEV_5400": 0.250,
        "VEV_5500": 0.275,
        "VEV_6000": 0.62,
        "VEV_6500": 0.88,
    }

    TAKE_EDGE = {
        "VEV_4000": 1.0,
        "VEV_4500": 1.0,
        "VEV_5000": 1.2,
        "VEV_5100": 1.0,
        "VEV_5200": 0.8,
        "VEV_5300": 0.8,
        "VEV_5400": 0.9,
        "VEV_5500": 1.0,
        "VEV_6000": 99.0,
        "VEV_6500": 99.0,
    }
    PASSIVE_EDGE = {
        "VEV_4000": 2.0,
        "VEV_4500": 1.8,
        "VEV_5000": 1.3,
        "VEV_5100": 1.1,
        "VEV_5200": 0.75,
        "VEV_5300": 0.75,
        "VEV_5400": 0.80,
        "VEV_5500": 0.95,
        "VEV_6000": 99.0,
        "VEV_6500": 99.0,
    }

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        cache = self._load_cache(state.traderData)
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        virtual_pos = {
            product: int(state.position.get(product, 0))
            for product in self.LIMITS
        }

        velvet_depth = state.order_depths.get(self.VELVET)
        velvet_fair: Optional[float] = None
        velvet_signal = 0.0
        if velvet_depth is not None:
            velvet_fair, velvet_signal = self._underlying_fair(
                self.VELVET,
                velvet_depth,
                cache,
                default_decay=0.18,
                inventory=float(state.position.get(self.VELVET, 0)),
            )
            self._trade_underlying(
                state,
                self.VELVET,
                velvet_depth,
                velvet_fair,
                velvet_signal,
                result,
                virtual_pos,
            )

        hydro_depth = state.order_depths.get(self.HYDROGEL)
        if hydro_depth is not None:
            hydro_fair, hydro_signal = self._underlying_fair(
                self.HYDROGEL,
                hydro_depth,
                cache,
                default_decay=0.10,
                inventory=float(state.position.get(self.HYDROGEL, 0)),
            )
            self._trade_hydrogel(
                state,
                hydro_depth,
                hydro_fair,
                hydro_signal,
                result,
                virtual_pos,
            )

        option_state: Dict[str, Dict[str, float]] = {}
        if velvet_depth is not None and velvet_fair is not None:
            for product in self.VOUCHERS:
                depth = state.order_depths.get(product)
                if depth is None:
                    continue
                fair, delta, gamma = self._option_fair_delta_gamma(product, velvet_fair)
                option_state[product] = {
                    "fair": fair,
                    "delta": delta,
                    "gamma": gamma,
                }
                self._trade_voucher(
                    state,
                    product,
                    depth,
                    velvet_fair,
                    fair,
                    delta,
                    gamma,
                    result,
                    virtual_pos,
                )

            self._delta_hedge_velvet(
                state,
                velvet_depth,
                velvet_fair,
                velvet_signal,
                option_state,
                result,
                virtual_pos,
            )

        self._store_fairs(cache, state, velvet_fair)
        trader_data = json.dumps(cache, separators=(",", ":"))
        return result, 0, trader_data

    def _trade_voucher(
        self,
        state: TradingState,
        product: str,
        depth: OrderDepth,
        spot_fair: float,
        fair: float,
        delta: float,
        gamma: float,
        result: Dict[str, List[Order]],
        virtual_pos: Dict[str, int],
    ) -> None:
        best_bid, best_ask, bid_vol, ask_vol = self._best_bid_ask(depth)
        if best_bid is None or best_ask is None:
            return

        pos = int(state.position.get(product, 0))
        strike = self.STRIKES[product]
        intrinsic = max(0.0, spot_fair - strike)

        if product in ("VEV_6000", "VEV_6500"):
            self._trade_floor_tail(product, depth, result, virtual_pos)
            return

        # Primary exploit: executable lower-bound violations C < S - K.
        lb_edge = intrinsic - float(best_ask)
        if intrinsic > 0.0 and lb_edge >= 1.0:
            max_delta_after = self._aggregate_delta_after(product, delta, virtual_pos)
            qty_cap = 55 if strike <= 4500 else 35
            if max_delta_after < 245:
                qty = min(qty_cap, max(6, int(6 + 4 * lb_edge)))
                self._sweep_buy(product, depth, best_ask, qty, result, virtual_pos)

        # Model edge takers. For shorts, require more edge and avoid building
        # large negative convexity near/above the money.
        take_edge = self.TAKE_EDGE[product]
        buy_edge = fair - float(best_ask)
        if buy_edge >= take_edge:
            qty = self._voucher_take_size(product, buy_edge, gamma, side=1)
            self._sweep_buy(product, depth, min(best_ask, int(math.floor(fair - 0.25))), qty, result, virtual_pos)

        sell_edge = float(best_bid) - fair
        short_allowed = strike <= 5400 or pos > 0
        if short_allowed and sell_edge >= take_edge + 0.35:
            qty = self._voucher_take_size(product, sell_edge, gamma, side=-1)
            if pos <= -180:
                qty = min(qty, 8)
            self._sweep_sell(product, depth, max(best_bid, int(math.ceil(fair + 0.25))), qty, result, virtual_pos)

        # Passive residual mean reversion. The EDA found buy-at-bid quality
        # strongest around VEV_5200/5300, so those get tighter passive bids.
        self._quote_voucher_passive(
            product,
            depth,
            fair,
            delta,
            result,
            virtual_pos,
        )

    def _quote_voucher_passive(
        self,
        product: str,
        depth: OrderDepth,
        fair: float,
        delta: float,
        result: Dict[str, List[Order]],
        virtual_pos: Dict[str, int],
    ) -> None:
        best_bid, best_ask, _, _ = self._best_bid_ask(depth)
        if best_bid is None or best_ask is None:
            return

        pos = virtual_pos.get(product, 0)
        edge = self.PASSIVE_EDGE[product]
        inv_skew = 0.010 * pos * max(0.35, delta)
        bid_fair = fair - edge - inv_skew
        ask_fair = fair + edge - inv_skew

        if product in ("VEV_5200", "VEV_5300"):
            base_qty = 18
        elif product in ("VEV_5400", "VEV_5500"):
            base_qty = 14
        elif product in ("VEV_5000", "VEV_5100"):
            base_qty = 12
        else:
            base_qty = 8

        spread = best_ask - best_bid
        bid_price = int(math.floor(bid_fair))
        ask_price = int(math.ceil(ask_fair))

        if spread > 1:
            bid_price = min(best_ask - 1, max(best_bid + 1, bid_price))
            ask_price = max(best_bid + 1, min(best_ask - 1, ask_price))
            if ask_price <= bid_price:
                bid_price = min(best_bid + 1, best_ask - 1)
                ask_price = max(best_bid + 1, best_ask - 1)
        else:
            bid_price = min(bid_price, best_bid)
            ask_price = max(ask_price, best_ask)

        if bid_price <= fair - 0.35:
            qty = self._inventory_scaled_qty(base_qty, virtual_pos.get(product, 0), self.LIMITS[product], side=1)
            self._add_order(product, bid_price, qty, result, virtual_pos)

        # Do not aggressively grow short option inventory unless fair is clearly
        # below the quote or we are reducing an existing long.
        can_quote_ask = product not in ("VEV_5500",) or virtual_pos.get(product, 0) > -80
        if can_quote_ask and ask_price >= fair + 0.35:
            qty = self._inventory_scaled_qty(base_qty, virtual_pos.get(product, 0), self.LIMITS[product], side=-1)
            if virtual_pos.get(product, 0) < -140:
                qty = min(qty, 5)
            self._add_order(product, ask_price, -qty, result, virtual_pos)

    def _trade_floor_tail(
        self,
        product: str,
        depth: OrderDepth,
        result: Dict[str, List[Order]],
        virtual_pos: Dict[str, int],
    ) -> None:
        # Historical VEV_6000/6500 was exactly bid 0 / ask 1, with trades at 0.
        # A zero bid is a free lottery if it fills. Avoid naked shorts.
        pos = virtual_pos.get(product, 0)
        if pos < self.LIMITS[product]:
            qty = min(45, self.LIMITS[product] - pos)
            if qty > 0:
                self._add_order(product, 0, qty, result, virtual_pos)

        if virtual_pos.get(product, 0) > 40:
            qty = min(20, virtual_pos[product])
            self._add_order(product, 1, -qty, result, virtual_pos)

    def _trade_underlying(
        self,
        state: TradingState,
        product: str,
        depth: OrderDepth,
        fair: float,
        signal: float,
        result: Dict[str, List[Order]],
        virtual_pos: Dict[str, int],
    ) -> None:
        best_bid, best_ask, _, _ = self._best_bid_ask(depth)
        if best_bid is None or best_ask is None:
            return

        spread = best_ask - best_bid
        pos = int(state.position.get(product, 0))
        target = self._clip(signal * 38.0, -55.0, 55.0)
        inv_skew = 0.018 * (pos - target)
        adjusted_fair = fair - inv_skew

        if float(best_ask) <= adjusted_fair - 2.3 and spread <= 8:
            qty = min(18, int(4 + 3 * (adjusted_fair - best_ask)))
            self._sweep_buy(product, depth, int(best_ask), qty, result, virtual_pos)

        if float(best_bid) >= adjusted_fair + 2.3 and spread <= 8:
            qty = min(18, int(4 + 3 * (best_bid - adjusted_fair)))
            self._sweep_sell(product, depth, int(best_bid), qty, result, virtual_pos)

        base_qty = 12 if spread <= 6 else 8
        bid_price = int(math.floor(adjusted_fair - 1.2))
        ask_price = int(math.ceil(adjusted_fair + 1.2))
        if spread > 1:
            bid_price = min(best_ask - 1, max(best_bid + 1, bid_price))
            ask_price = max(best_bid + 1, min(best_ask - 1, ask_price))
        else:
            bid_price = min(bid_price, best_bid)
            ask_price = max(ask_price, best_ask)

        if bid_price <= adjusted_fair - 0.4:
            qty = self._inventory_scaled_qty(base_qty, virtual_pos.get(product, 0), self.LIMITS[product], side=1)
            self._add_order(product, bid_price, qty, result, virtual_pos)
        if ask_price >= adjusted_fair + 0.4:
            qty = self._inventory_scaled_qty(base_qty, virtual_pos.get(product, 0), self.LIMITS[product], side=-1)
            self._add_order(product, ask_price, -qty, result, virtual_pos)

    def _trade_hydrogel(
        self,
        state: TradingState,
        depth: OrderDepth,
        fair: float,
        signal: float,
        result: Dict[str, List[Order]],
        virtual_pos: Dict[str, int],
    ) -> None:
        best_bid, best_ask, _, _ = self._best_bid_ask(depth)
        if best_bid is None or best_ask is None:
            return

        spread = best_ask - best_bid
        pos = int(state.position.get(self.HYDROGEL, 0))
        target = self._clip(signal * 30.0, -45.0, 45.0)
        adjusted_fair = fair - 0.035 * (pos - target)

        if float(best_ask) <= adjusted_fair - 6.0:
            qty = min(14, int(3 + 1.5 * (adjusted_fair - best_ask)))
            self._sweep_buy(self.HYDROGEL, depth, int(best_ask), qty, result, virtual_pos)
        if float(best_bid) >= adjusted_fair + 6.0:
            qty = min(14, int(3 + 1.5 * (best_bid - adjusted_fair)))
            self._sweep_sell(self.HYDROGEL, depth, int(best_bid), qty, result, virtual_pos)

        base_qty = 10 if spread <= 18 else 6
        bid_price = int(math.floor(adjusted_fair - 4.0))
        ask_price = int(math.ceil(adjusted_fair + 4.0))
        if spread > 1:
            bid_price = min(best_ask - 1, max(best_bid + 1, bid_price))
            ask_price = max(best_bid + 1, min(best_ask - 1, ask_price))

        if bid_price <= adjusted_fair - 2.0:
            qty = self._inventory_scaled_qty(base_qty, virtual_pos.get(self.HYDROGEL, 0), self.LIMITS[self.HYDROGEL], side=1)
            self._add_order(self.HYDROGEL, bid_price, qty, result, virtual_pos)
        if ask_price >= adjusted_fair + 2.0:
            qty = self._inventory_scaled_qty(base_qty, virtual_pos.get(self.HYDROGEL, 0), self.LIMITS[self.HYDROGEL], side=-1)
            self._add_order(self.HYDROGEL, ask_price, -qty, result, virtual_pos)

    def _delta_hedge_velvet(
        self,
        state: TradingState,
        depth: OrderDepth,
        fair: float,
        signal: float,
        option_state: Dict[str, Dict[str, float]],
        result: Dict[str, List[Order]],
        virtual_pos: Dict[str, int],
    ) -> None:
        best_bid, best_ask, _, _ = self._best_bid_ask(depth)
        if best_bid is None or best_ask is None:
            return

        spread = best_ask - best_bid
        option_delta = 0.0
        for product, metrics in option_state.items():
            option_delta += virtual_pos.get(product, 0) * metrics["delta"]

        net_delta = virtual_pos.get(self.VELVET, 0) + option_delta
        target_delta = self._clip(signal * 48.0, -65.0, 65.0)
        diff = net_delta - target_delta

        threshold = 52.0 if spread <= 6 else 72.0
        if abs(diff) < threshold:
            return

        # Coarse hedging only. EDA showed tick-by-tick hedging destroys gamma.
        hedge_qty = min(35, int(abs(diff) - threshold * 0.35))
        if hedge_qty <= 0:
            return

        if diff > 0 and spread <= 9:
            max_price = int(best_bid)
            if float(best_bid) >= fair - 3.5:
                self._sweep_sell(self.VELVET, depth, max_price, hedge_qty, result, virtual_pos)
        elif diff < 0 and spread <= 9:
            max_price = int(best_ask)
            if float(best_ask) <= fair + 3.5:
                self._sweep_buy(self.VELVET, depth, max_price, hedge_qty, result, virtual_pos)

    def _underlying_fair(
        self,
        product: str,
        depth: OrderDepth,
        cache: Dict[str, Any],
        default_decay: float,
        inventory: float,
    ) -> Tuple[float, float]:
        mid = self._mid(depth)
        if mid is None:
            prev = cache.get("fair", {}).get(product)
            return (float(prev) if prev is not None else 0.0), 0.0

        micro = self._microprice(depth)
        dom_mid = self._dom_mid(depth)
        imbalance = self._top_imbalance(depth)

        fair_book = mid
        signal = 0.0
        if micro is not None:
            fair_book += 0.30 * (micro - mid)
            signal += 0.35 * (micro - mid)
        if dom_mid is not None:
            fair_book += 0.55 * (dom_mid - mid)
            signal += 0.70 * (dom_mid - mid)
        signal += 1.1 * imbalance

        prev_fair = cache.get("fair", {}).get(product)
        if isinstance(prev_fair, (int, float)) and prev_fair > 0:
            fair = (1.0 - default_decay) * float(prev_fair) + default_decay * fair_book
        else:
            fair = fair_book

        if product == self.HYDROGEL:
            fair -= 0.012 * inventory
        return fair, signal

    def _option_fair_delta_gamma(self, product: str, spot: float) -> Tuple[float, float, float]:
        strike = self.STRIKES[product]
        sigma = self._surface_sigma(product, spot)
        intrinsic = max(0.0, spot - strike)

        if spot <= 0.0:
            return intrinsic, 0.0, 0.0
        if product in ("VEV_4000", "VEV_4500") and spot - strike > 450.0:
            # Deep ITM options historically replicate spot - strike almost
            # exactly; keep a tiny time-value cushion but do not hallucinate
            # large BS premia.
            return intrinsic + 0.10, 0.985, 0.0

        call = self._bs_call(spot, strike, sigma, self.TTE)
        delta = self._bs_delta(spot, strike, sigma, self.TTE)
        gamma = self._bs_gamma(spot, strike, sigma, self.TTE)

        # Rounding is structurally important in this market. A small fair-value
        # buffer prevents whipsaw around half ticks.
        fair = max(intrinsic, call)
        return fair, delta, gamma

    def _surface_sigma(self, product: str, spot: float) -> float:
        base = self.STRIKE_IV[product]
        strike = self.STRIKES[product]
        if spot <= 0:
            return base
        log_m = math.log(strike / spot)
        right_wing = max(0.0, log_m)
        left_wing = max(0.0, -log_m)
        sigma = base + 0.40 * right_wing * right_wing + 0.05 * left_wing
        return self._clip(sigma, self.MIN_SIGMA, self.MAX_SIGMA)

    def _bs_call(self, spot: float, strike: float, sigma: float, tte: float) -> float:
        if tte <= 0.0 or sigma <= 0.0:
            return max(0.0, spot - strike)
        vol_t = sigma * math.sqrt(tte)
        if vol_t <= 0.0:
            return max(0.0, spot - strike)
        d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * tte) / vol_t
        d2 = d1 - vol_t
        return spot * self._norm_cdf(d1) - strike * self._norm_cdf(d2)

    def _bs_delta(self, spot: float, strike: float, sigma: float, tte: float) -> float:
        if tte <= 0.0 or sigma <= 0.0:
            return 1.0 if spot > strike else 0.0
        vol_t = sigma * math.sqrt(tte)
        if vol_t <= 0.0:
            return 1.0 if spot > strike else 0.0
        d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * tte) / vol_t
        return self._norm_cdf(d1)

    def _bs_gamma(self, spot: float, strike: float, sigma: float, tte: float) -> float:
        if tte <= 0.0 or sigma <= 0.0 or spot <= 0.0:
            return 0.0
        vol_t = sigma * math.sqrt(tte)
        if vol_t <= 0.0:
            return 0.0
        d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * tte) / vol_t
        return self._norm_pdf(d1) / (spot * vol_t)

    def _sweep_buy(
        self,
        product: str,
        depth: OrderDepth,
        max_price: int,
        max_qty: int,
        result: Dict[str, List[Order]],
        virtual_pos: Dict[str, int],
    ) -> None:
        if max_qty <= 0:
            return
        remaining = max_qty
        for price, volume in sorted(depth.sell_orders.items()):
            if price > max_price or remaining <= 0:
                break
            qty = min(remaining, -int(volume))
            placed = self._add_order(product, int(price), qty, result, virtual_pos)
            remaining -= placed

    def _sweep_sell(
        self,
        product: str,
        depth: OrderDepth,
        min_price: int,
        max_qty: int,
        result: Dict[str, List[Order]],
        virtual_pos: Dict[str, int],
    ) -> None:
        if max_qty <= 0:
            return
        remaining = max_qty
        for price, volume in sorted(depth.buy_orders.items(), reverse=True):
            if price < min_price or remaining <= 0:
                break
            qty = min(remaining, int(volume))
            placed = self._add_order(product, int(price), -qty, result, virtual_pos)
            remaining -= placed

    def _add_order(
        self,
        product: str,
        price: int,
        quantity: int,
        result: Dict[str, List[Order]],
        virtual_pos: Dict[str, int],
    ) -> int:
        if quantity == 0:
            return 0
        limit = self.LIMITS[product]
        pos = virtual_pos.get(product, 0)
        if quantity > 0:
            allowed = max(0, limit - pos)
            qty = min(quantity, allowed)
        else:
            allowed = max(0, limit + pos)
            qty = -min(-quantity, allowed)
        if qty == 0:
            return 0
        result.setdefault(product, []).append(Order(product, int(price), int(qty)))
        virtual_pos[product] = pos + qty
        return abs(qty)

    def _voucher_take_size(self, product: str, edge: float, gamma: float, side: int) -> int:
        if product in ("VEV_5200", "VEV_5300"):
            base = 14
        elif product in ("VEV_5400", "VEV_5500"):
            base = 11
        elif product in ("VEV_4000", "VEV_4500"):
            base = 12
        else:
            base = 10
        gamma_bonus = int(min(8, max(0.0, gamma * 80000.0)))
        edge_bonus = int(min(14, max(0.0, edge * 4.0)))
        qty = base + edge_bonus + gamma_bonus
        if side < 0 and product in ("VEV_5400", "VEV_5500"):
            qty = min(qty, 14)
        return int(max(1, min(35, qty)))

    def _aggregate_delta_after(
        self,
        product: str,
        delta: float,
        virtual_pos: Dict[str, int],
    ) -> float:
        estimate = float(virtual_pos.get(self.VELVET, 0))
        for voucher in self.VOUCHERS:
            pos = virtual_pos.get(voucher, 0)
            if voucher == product:
                estimate += pos * delta
            elif voucher in self.STRIKES:
                estimate += pos * self._rough_delta(voucher)
        return estimate

    def _rough_delta(self, product: str) -> float:
        return {
            "VEV_4000": 0.985,
            "VEV_4500": 0.96,
            "VEV_5000": 0.82,
            "VEV_5100": 0.68,
            "VEV_5200": 0.52,
            "VEV_5300": 0.34,
            "VEV_5400": 0.18,
            "VEV_5500": 0.08,
            "VEV_6000": 0.01,
            "VEV_6500": 0.00,
        }.get(product, 0.0)

    def _inventory_scaled_qty(self, base: int, pos: int, limit: int, side: int) -> int:
        if side > 0:
            pressure = max(0.0, pos / float(limit))
        else:
            pressure = max(0.0, -pos / float(limit))
        scale = 1.0 - 0.65 * pressure
        return max(1, int(round(base * scale)))

    def _best_bid_ask(self, depth: OrderDepth) -> Tuple[Optional[int], Optional[int], int, int]:
        best_bid = max(depth.buy_orders) if depth.buy_orders else None
        best_ask = min(depth.sell_orders) if depth.sell_orders else None
        bid_vol = int(depth.buy_orders[best_bid]) if best_bid is not None else 0
        ask_vol = -int(depth.sell_orders[best_ask]) if best_ask is not None else 0
        return best_bid, best_ask, bid_vol, ask_vol

    def _mid(self, depth: OrderDepth) -> Optional[float]:
        best_bid, best_ask, _, _ = self._best_bid_ask(depth)
        if best_bid is None or best_ask is None:
            return None
        return (best_bid + best_ask) / 2.0

    def _microprice(self, depth: OrderDepth) -> Optional[float]:
        best_bid, best_ask, bid_vol, ask_vol = self._best_bid_ask(depth)
        if best_bid is None or best_ask is None:
            return None
        total = bid_vol + ask_vol
        if total <= 0:
            return (best_bid + best_ask) / 2.0
        return (best_ask * bid_vol + best_bid * ask_vol) / float(total)

    def _dom_mid(self, depth: OrderDepth, levels: int = 3) -> Optional[float]:
        if not depth.buy_orders or not depth.sell_orders:
            return None
        bids = sorted(depth.buy_orders.items(), reverse=True)[:levels]
        asks = sorted(depth.sell_orders.items())[:levels]
        bid_vol = sum(max(0, int(vol)) for _, vol in bids)
        ask_vol = sum(max(0, -int(vol)) for _, vol in asks)
        if bid_vol <= 0 or ask_vol <= 0:
            return self._mid(depth)
        bid_vwap = sum(price * max(0, int(vol)) for price, vol in bids) / float(bid_vol)
        ask_vwap = sum(price * max(0, -int(vol)) for price, vol in asks) / float(ask_vol)
        return (bid_vwap + ask_vwap) / 2.0

    def _top_imbalance(self, depth: OrderDepth) -> float:
        _, _, bid_vol, ask_vol = self._best_bid_ask(depth)
        total = bid_vol + ask_vol
        if total <= 0:
            return 0.0
        return (bid_vol - ask_vol) / float(total)

    def _load_cache(self, trader_data: str) -> Dict[str, Any]:
        if not trader_data:
            return {"fair": {}}
        try:
            data = json.loads(trader_data)
            if not isinstance(data, dict):
                return {"fair": {}}
            if not isinstance(data.get("fair"), dict):
                data["fair"] = {}
            return data
        except (json.JSONDecodeError, TypeError):
            return {"fair": {}}

    def _store_fairs(self, cache: Dict[str, Any], state: TradingState, velvet_fair: Optional[float]) -> None:
        fair_map = cache.setdefault("fair", {})
        for product in (self.HYDROGEL, self.VELVET):
            depth = state.order_depths.get(product)
            if depth is None:
                continue
            mid = self._mid(depth)
            if mid is not None:
                current = velvet_fair if product == self.VELVET and velvet_fair is not None else mid
                fair_map[product] = round(float(current), 5)
        cache["t"] = int(getattr(state, "timestamp", 0))

    def _norm_cdf(self, x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    def _norm_pdf(self, x: float) -> float:
        return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)

    def _clip(self, value: float, low: float, high: float) -> float:
        return max(low, min(high, value))