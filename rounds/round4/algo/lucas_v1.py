from __future__ import annotations

"""
Strat52: Redesigned HGP + strat46 voucher/VE engine.

HGP:
  - Pure passive MM at fixed FV=10000. Position cap +-80.
  - OBI contrarian filter on quote sizing (neutral regime only).
  - No Z-score, no floating anchor, no flip guards.

Vouchers/VE:
  - Anchor formula retained (market prices at BS~TTE=8, not TTE_actual).
  - VEV_6000/6500 sell code present; does not fill in backtest (bots trade at 0).
  - Player overlay (Mark01, Mark22, Mark67, Mark55, Mark49) unchanged from strat46.
"""

import json
import math
from typing import Any, Dict, List, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ModuleNotFoundError:
    from prosperity3bt.datamodel import Order, OrderDepth, TradingState


# ---------------------------------------------------------------------------
# Rolling implied volatility helpers
# ---------------------------------------------------------------------------

_T_MARKET = 8 / 365.0      # bots price at TTE=8 regardless of actual remaining TTE
_SIGMA_REF = 0.22           # fallback / calibration sigma
_IV_EMA_ALPHA = 0.005       # very slow EMA (~200-tick half-life)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs_call(S: float, K: float, T: float, sigma: float) -> float:
    intrinsic = max(0.0, S - K)
    if T <= 0 or sigma <= 0 or S <= 0:
        return intrinsic
    vs = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / vs
    return S * _norm_cdf(d1) - K * _norm_cdf(d1 - vs)


def _bs_invert_sigma(S: float, K: float, T: float, price: float) -> float:
    """Binary search for implied vol from observed price. 40 iterations ~ 1e-12 accuracy."""
    lo, hi = 0.05, 0.80
    if price <= max(0.0, S - K):
        return lo
    for _ in range(40):
        mid = (lo + hi) / 2.0
        if _bs_call(S, K, T, mid) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# ---------------------------------------------------------------------------
# HGP constants
# ---------------------------------------------------------------------------

HGP = "HYDROGEL_PACK"
HGP_FV = 10_000
HGP_LIMIT = 200
HGP_EDGE = 2            # passive quote offset from FV
HGP_BASE_SIZE = 30      # units per passive quote (each side)
HGP_CAP = 80            # hard position cap
HGP_OBI_THRESH = 0.15   # contrarian OBI filter


# ---------------------------------------------------------------------------
# HGP helpers
# ---------------------------------------------------------------------------

def _hgp_obi(depth: OrderDepth) -> float:
    bid_vol = sum(max(0, int(v)) for v in depth.buy_orders.values())
    ask_vol = sum(max(0, -int(v)) for v in depth.sell_orders.values())
    total = bid_vol + ask_vol
    if total <= 0:
        return 0.0
    return (bid_vol - ask_vol) / total


def _run_hgp(state: TradingState, cache: Dict[str, Any]) -> List[Order]:
    depth = state.order_depths.get(HGP)
    if depth is None or not depth.buy_orders or not depth.sell_orders:
        return []

    pos = int(state.position.get(HGP, 0))
    bb = max(depth.buy_orders)
    ba = min(depth.sell_orders)
    obi = _hgp_obi(depth)

    orders: List[Order] = []

    # --- Passive MM: symmetric quotes at FV+-EDGE, inventory-skewed ---
    inv_skew = int(round(pos / HGP_CAP * HGP_EDGE))
    inv_skew = max(-HGP_EDGE, min(HGP_EDGE, inv_skew))

    bid_px = min(HGP_FV - HGP_EDGE - inv_skew, ba - 1)
    ask_px = max(HGP_FV + HGP_EDGE - inv_skew, bb + 1)
    if bid_px >= ask_px or bid_px <= 0:
        return orders

    buy_cap  = max(0, HGP_CAP - pos)
    sell_cap = max(0, HGP_CAP + pos)

    buy_size  = HGP_BASE_SIZE
    sell_size = HGP_BASE_SIZE

    # Contrarian OBI: suppress the side the book is leaning toward
    if obi > HGP_OBI_THRESH:
        buy_size = max(0, buy_size // 3)
    elif obi < -HGP_OBI_THRESH:
        sell_size = max(0, sell_size // 3)

    buy_size  = min(buy_size, buy_cap)
    sell_size = min(sell_size, sell_cap)

    if buy_size > 0:
        orders.append(Order(HGP, int(bid_px), int(buy_size)))
    if sell_size > 0:
        orders.append(Order(HGP, int(ask_px), -int(sell_size)))

    return orders


# ---------------------------------------------------------------------------
# Voucher engine — verbatim from strat46_round4_player_profile.py
# (player-profile overlay kept for VE/vouchers; HGP overlay removed)
# ---------------------------------------------------------------------------

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
    VE = "VELVETFRUIT_EXTRACT"
    VOUCHERS = [
        "VEV_4000", "VEV_4500", "VEV_5000", "VEV_5100", "VEV_5200",
        "VEV_5300", "VEV_5400", "VEV_5500", "VEV_6000", "VEV_6500",
    ]
    LIMITS = {VE: 200, **{v: 300 for v in VOUCHERS}}

    ANCHOR = {
        VE: 5262.0,
        "VEV_4000": 1265.0, "VEV_4500": 764.0, "VEV_5000": 266.0,
        "VEV_5100": 175.0,  "VEV_5200": 101.0, "VEV_5300": 50.0,
        "VEV_5400": 16.0,   "VEV_5500": 6.0,
        "VEV_6000": 0.5,    "VEV_6500": 0.5,
    }
    SOFT = {
        VE: 200,
        "VEV_4000": 24, "VEV_4500": 24,  "VEV_5000": 275,
        "VEV_5100": 280, "VEV_5200": 285, "VEV_5300": 235,
        "VEV_5400": 220, "VEV_5500": 12,
        "VEV_6000": 0,  "VEV_6500": 0,
    }
    REG = {
        "VEV_4000": (-3998.27, 1.000, 0.83), "VEV_4500": (-4497.00, 0.999, 0.76),
        "VEV_5000": (-4550.49, 0.915, 1.40), "VEV_5100": (-3950.96, 0.784, 3.48),
        "VEV_5200": (-2871.36, 0.565, 3.92), "VEV_5300": (-1704.69, 0.334, 3.41),
        "VEV_5400": (-644.24,  0.126, 2.81), "VEV_5500": (-281.46,  0.055, 1.51),
    }
    STRIKE = {
        "VEV_4000": 4000, "VEV_4500": 4500, "VEV_5000": 5000, "VEV_5100": 5100,
        "VEV_5200": 5200, "VEV_5300": 5300, "VEV_5400": 5400, "VEV_5500": 5500,
        "VEV_6000": 6000, "VEV_6500": 6500,
    }
    DELTA_APPROX = {
        "VEV_4000": 1.00, "VEV_4500": 1.00, "VEV_5000": 0.93, "VEV_5100": 0.78,
        "VEV_5200": 0.57, "VEV_5300": 0.33, "VEV_5400": 0.13, "VEV_5500": 0.05,
        "VEV_6000": 0.00, "VEV_6500": 0.00,
    }
    CORE_BASKET = ("VEV_5000", "VEV_5100", "VEV_5200", "VEV_5300", "VEV_5400")

    PLAYER_WINDOW = 7000
    FLOW_MAX_ABS = 3.0
    FLOW_FAIR_SCALE = {
        VE: 1.8,
        "VEV_4000": 3.0, "VEV_4500": 2.2, "VEV_5000": 1.4,
        "VEV_5100": 1.1, "VEV_5200": 0.9, "VEV_5300": 0.7,
        "VEV_5400": 0.45, "VEV_5500": 0.35,
        "VEV_6000": 0.0, "VEV_6500": 0.0,
    }
    ACTIVE_PLAYER_PRODUCTS = (VE, "VEV_4000", "VEV_4500", "VEV_5000", "VEV_5100", "VEV_5200")

    def bid(self) -> int:
        return 1

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {}
        cache = self._load_cache(state.traderData)
        timestamp = int(getattr(state, "timestamp", 0))
        cache["now"] = timestamp

        # --- HGP: passive MM ---
        hgp_orders = _run_hgp(state, cache)
        result[HGP] = hgp_orders

        # --- Player signals for VE/vouchers ---
        self._update_player_profile_signals(state, cache, timestamp)

        # --- Vouchers + VE ---
        for p in self.LIMITS:
            result.setdefault(p, [])
        om = OrderManager(state, self.LIMITS, result)
        self._update_state(state, cache)

        depth_ve = state.order_depths.get(self.VE)
        if depth_ve is not None:
            self._trade_ve_passive(depth_ve, cache, om)
        for product in self.VOUCHERS:
            depth = state.order_depths.get(product)
            if depth is None:
                continue
            self._trade_voucher(product, depth, cache, om)
        self._trade_player_overlay(state, cache, om, timestamp)
        self._trade_noarb_lower_bound(state, cache, om)
        self._delta_hedge_passive(state, cache, om)

        cache["t"] = timestamp
        return result, 0, json.dumps(cache, separators=(",", ":"))

    # --- Player-profile overlay (VE/vouchers only) ---

    def _update_player_profile_signals(self, state: TradingState, cache: Dict[str, Any], timestamp: int) -> None:
        for book_name in ("own_trades", "market_trades"):
            trade_book = getattr(state, book_name, {}) or {}
            for product, trades in trade_book.items():
                if product not in self.LIMITS:
                    continue
                for trade in trades:
                    buyer = getattr(trade, "buyer", "") or ""
                    seller = getattr(trade, "seller", "") or ""
                    if not buyer or not seller:
                        continue
                    self._score_player_trade(cache, product, buyer, seller, timestamp)

    def _score_player_trade(self, cache: Dict[str, Any], product: str, buyer: str, seller: str, timestamp: int) -> None:

        if product in self.VOUCHERS or product == self.VE:
            if buyer == "Mark 01":
                self._register_flow(cache, product, +1.2, timestamp, "LEAN_WITH_MARK01_BUY")
            if seller == "Mark 01":
                self._register_flow(cache, product, -1.0, timestamp, "LEAN_WITH_MARK01_SELL")

        if product in {"VEV_5200", "VEV_5300", "VEV_5400", "VEV_5500", "VEV_6000", "VEV_6500"}:
            if seller == "Mark 22":
                self._register_flow(cache, product, +1.0, timestamp, "MARK22_VOUCHER_SOURCE")
            if buyer == "Mark 22":
                self._register_flow(cache, product, -0.4, timestamp, "MARK22_RARE_BUY")

        if product == self.VE:
            if buyer == "Mark 67":
                self._register_flow(cache, product, +1.4, timestamp, "MARK67_VE_BUY")
            if seller == "Mark 67":
                self._register_flow(cache, product, -0.7, timestamp, "MARK67_VE_SELL")
            if buyer == "Mark 55":
                self._register_flow(cache, product, -0.8, timestamp, "FADE_MARK55_BUY")
            if seller == "Mark 55":
                self._register_flow(cache, product, +0.8, timestamp, "FADE_MARK55_SELL")
            if seller == "Mark 49":
                self._register_flow(cache, product, +0.7, timestamp, "MARK49_VE_SOURCE")

    def _register_flow(self, cache: Dict[str, Any], product: str, bias: float, timestamp: int, reason: str) -> None:
        flows = cache.setdefault("flow", {})
        current = flows.get(product, {})
        old_bias = float(current.get("bias", 0.0)) if isinstance(current, dict) else 0.0
        old_expires = int(current.get("expires", -1)) if isinstance(current, dict) else -1
        if old_expires >= timestamp and old_bias * bias > 0:
            bias = old_bias + bias * 0.35
        bias = max(-self.FLOW_MAX_ABS, min(self.FLOW_MAX_ABS, bias))
        flows[product] = {"bias": round(bias, 4), "expires": timestamp + self.PLAYER_WINDOW, "reason": reason}

    def _flow_bias(self, cache: Dict[str, Any], product: str) -> float:
        timestamp = int(cache.get("now", 0))
        item = cache.get("flow", {}).get(product, {})
        if not isinstance(item, dict) or int(item.get("expires", -1)) < timestamp:
            return 0.0
        return float(item.get("bias", 0.0))

    def _flow_reason(self, cache: Dict[str, Any], product: str) -> str:
        item = cache.get("flow", {}).get(product, {})
        return str(item.get("reason", "")) if isinstance(item, dict) else ""

    def _apply_flow_sizing(self, cache: Dict[str, Any], product: str, buy_size: int, sell_size: int) -> Tuple[int, int]:
        bias = self._flow_bias(cache, product)
        if bias >= 1.0:
            buy_size = int(round(buy_size * (1.0 + min(0.75, 0.18 * bias))))
            sell_size = int(round(sell_size * max(0.25, 1.0 - 0.22 * bias)))
        elif bias <= -1.0:
            sell_size = int(round(sell_size * (1.0 + min(0.75, 0.18 * abs(bias)))))
            buy_size = int(round(buy_size * max(0.25, 1.0 - 0.22 * abs(bias))))
        return max(0, buy_size), max(0, sell_size)

    def _trade_player_overlay(self, state: TradingState, cache: Dict[str, Any], om: OrderManager, timestamp: int) -> None:
        cooldown = cache.setdefault("player_overlay_cd", {})
        for product in self.ACTIVE_PLAYER_PRODUCTS:
            bias = self._flow_bias(cache, product)
            if abs(bias) < 1.8 or int(cooldown.get(product, -1)) > timestamp:
                continue
            depth = state.order_depths.get(product)
            bid, ask, _, _ = self._best_bid_ask(depth)
            if bid is None or ask is None:
                continue
            pos = om.pos(product)
            soft = min(self.SOFT.get(product, self.LIMITS[product]), 70 if product == self.VE else 65)
            reason = self._flow_reason(cache, product)
            qty = 2 if product in (self.VE, "VEV_4000", "VEV_4500") else 3
            if "MARK22" in reason:
                qty = 2
            if bias > 0 and pos < soft:
                om.add(product, ask, min(qty, soft - pos))
                cooldown[product] = timestamp + 900
            elif bias < 0 and pos > -soft:
                om.add(product, bid, -min(qty, pos + soft))
                cooldown[product] = timestamp + 900

    # --- Voucher/VE engine ---

    def _update_state(self, state: TradingState, cache: Dict[str, Any]) -> None:
        ve_depth = state.order_depths.get(self.VE)
        ve_mid = self._mid(ve_depth)
        if ve_mid is None:
            return
        cache["ve_mid"] = round(float(ve_mid), 4)
        self._ema(cache, "ve_slow", ve_mid, 0.025)
        # Rolling implied vol from VEV_5400 (most liquid ATM, 286-413 trades/day)
        ref_mid = self._mid(state.order_depths.get("VEV_5400"))
        if ref_mid is not None and ref_mid > 0.5:
            sigma_raw = _bs_invert_sigma(ve_mid, 5400, _T_MARKET, ref_mid)
            self._ema(cache, "sigma_iv", sigma_raw, _IV_EMA_ALPHA)
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
            trimmed = vals[1:-1] if len(vals) >= 4 else vals
            cache["rv_basket_z"] = round(sum(trimmed) / len(trimmed), 6)

    def _trade_ve_passive(self, depth: OrderDepth, cache: Dict[str, Any], om: OrderManager) -> None:
        bid, ask, bvol, avol = self._best_bid_ask(depth)
        mid = self._mid(depth)
        if bid is None or ask is None or mid is None:
            return
        spread = ask - bid
        if spread <= 0:
            return
        ema = self._ema(cache, "ve_ema", mid, 0.045)
        anchor = self.ANCHOR[self.VE]
        fair = 0.6 * anchor + 0.4 * ema
        fair += self.FLOW_FAIR_SCALE[self.VE] * self._flow_bias(cache, self.VE)
        obi = self._obi(bvol, avol)
        pos = om.pos(self.VE)
        soft = self.SOFT[self.VE]
        fair -= 0.030 * pos
        edge = 0.7
        buy_px = bid + 1 if spread >= 3 else bid
        sell_px = ask - 1 if spread >= 3 else ask
        base = 42
        buy_size = base
        sell_size = base
        if obi > 0.15:
            buy_size = max(0, buy_size // 3)
        elif obi < -0.15:
            sell_size = max(0, sell_size // 3)
        if pos > soft * 0.55:
            buy_size = 0
            sell_size = int(sell_size * 1.4)
        elif pos < -soft * 0.55:
            sell_size = 0
            buy_size = int(buy_size * 1.4)
        buy_size, sell_size = self._apply_flow_sizing(cache, self.VE, buy_size, sell_size)
        if buy_size > 0 and pos < soft and buy_px <= fair - edge:
            om.add(self.VE, buy_px, min(buy_size, soft - pos))
        if sell_size > 0 and pos > -soft and sell_px >= fair + edge:
            om.add(self.VE, sell_px, -min(sell_size, pos + soft))

    def _trade_voucher(self, product: str, depth: OrderDepth, cache: Dict[str, Any], om: OrderManager) -> None:
        # Fix 3: VEV_6000/6500 re-enabled — sell at ask=1, BS fair ~0, free carry
        if product in ("VEV_6000", "VEV_6500"):
            best_bid = max(depth.buy_orders) if depth.buy_orders else None
            sell_px = max(1, best_bid) if best_bid is not None else 1
            sell_cap = self.LIMITS[product] + om.pos(product)
            if sell_cap > 0:
                om.add(product, sell_px, -min(30, sell_cap))
            return
        if product in ("VEV_4000", "VEV_4500"):
            self._trade_mr(product, depth, cache, om, alpha=0.025, inv=0.080, edge=4.0, size=6, taker=False)
            return
        if product == "VEV_5000":
            self._trade_mr(product, depth, cache, om, alpha=0.030, inv=0.080, edge=1.25, size=34, taker=True)
        elif product in ("VEV_5100", "VEV_5200", "VEV_5300"):
            self._trade_mr(product, depth, cache, om, alpha=0.035, inv=0.065, edge=0.65, size=44, taker=True)
        elif product == "VEV_5400":
            self._trade_mr(product, depth, cache, om, alpha=0.045, inv=0.035, edge=0.35, size=50, taker=True)
        elif product == "VEV_5500":
            self._trade_mr(product, depth, cache, om, alpha=0.050, inv=0.060, edge=1.2, size=8, taker=False)

    def _trade_mr(self, product, depth, cache, om, alpha, inv, edge, size, taker):
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
        anchor_fair = 0.72 * anchor + 0.28 * ema + 0.08 * micro_edge
        # Scale fair by rolling IV ratio: prevents over-buying when market vol decays
        sigma_iv = float(cache.get("sigma_iv", _SIGMA_REF))
        fair = anchor_fair * (sigma_iv / _SIGMA_REF)
        fair += self.FLOW_FAIR_SCALE.get(product, 0.0) * self._flow_bias(cache, product)
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
            buy_size = max(1, size // 4)
            sell_size = int(size * 1.6)
        elif richness < -dyn_edge:
            buy_size = int(size * 1.6)
            sell_size = max(1, size // 4)
        if pos > soft * 0.55:
            buy_size = 0
            sell_size = int(size * 2.0)
        elif pos < -soft * 0.55:
            buy_size = int(size * 2.0)
            sell_size = 0
        basket_z = float(cache.get("rv_basket_z", 0.0))
        if product in self.CORE_BASKET:
            if basket_z < -0.85:
                buy_size = int(buy_size * 1.15)
                sell_size = max(1, int(sell_size * 0.80)) if sell_size > 0 else 0
            elif basket_z > 0.85:
                sell_size = int(sell_size * 1.20)
                buy_size = max(1, int(buy_size * 0.70)) if buy_size > 0 else 0
        buy_size, sell_size = self._apply_flow_sizing(cache, product, buy_size, sell_size)
        if product in ("VEV_5400", "VEV_5500") and pos <= 0:
            sell_size = 0
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

    def _trade_noarb_lower_bound(self, state, cache, om):
        ve_depth = state.order_depths.get(self.VE)
        ve_bid, _, _, _ = self._best_bid_ask(ve_depth)
        if ve_bid is None:
            return
        for product, threshold, qty in (("VEV_4000", 1.0, 3), ("VEV_4500", 0.8, 3)):
            depth = state.order_depths.get(product)
            bid, ask, _, ask_vol = self._best_bid_ask(depth)
            if bid is None or ask is None or ask_vol <= 0:
                continue
            lb = ve_bid - self.STRIKE[product]
            edge = lb - ask
            if edge < threshold:
                continue
            pos = om.pos(product)
            soft = min(self.SOFT[product], 24)
            if pos >= soft:
                continue
            buy_qty = min(qty, ask_vol, soft - pos)
            filled = om.add(product, ask, buy_qty)
            if filled > 0 and om.pos(self.VE) > -150:
                om.add(self.VE, ve_bid, -min(filled, 150 + om.pos(self.VE)))

    def _delta_hedge_passive(self, state, cache, om):
        ve_depth = state.order_depths.get(self.VE)
        ve_bid, ve_ask, _, _ = self._best_bid_ask(ve_depth)
        if ve_bid is None or ve_ask is None:
            return
        net_delta = 0.0
        for v in self.VOUCHERS:
            net_delta += om.pos(v) * self.DELTA_APPROX.get(v, 0.0)
        ve_pos = om.pos(self.VE)
        target_ve = -net_delta * 0.5
        gap = target_ve - ve_pos
        if abs(gap) < 12:
            return
        soft = 150
        if gap > 0:
            qty = min(int(gap), max(0, soft - ve_pos), 6)
            if qty > 0:
                om.add(self.VE, int(ve_bid), qty)
        else:
            qty = min(int(-gap), max(0, ve_pos + soft), 6)
            if qty > 0:
                om.add(self.VE, int(ve_ask), -qty)

    def _sweep_buy(self, product, depth, max_price, qty, om):
        rem = max(0, int(qty))
        for price, vol in sorted(depth.sell_orders.items()):
            if rem <= 0 or price > max_price:
                break
            rem -= om.add(product, int(price), min(rem, -int(vol)))

    def _sweep_sell(self, product, depth, min_price, qty, om):
        rem = max(0, int(qty))
        for price, vol in sorted(depth.buy_orders.items(), reverse=True):
            if rem <= 0 or price < min_price:
                break
            rem -= om.add(product, int(price), -min(rem, int(vol)))

    def _best_bid_ask(self, depth):
        if depth is None:
            return None, None, 0, 0
        bid = max(depth.buy_orders) if depth.buy_orders else None
        ask = min(depth.sell_orders) if depth.sell_orders else None
        bvol = int(depth.buy_orders[bid]) if bid is not None else 0
        avol = -int(depth.sell_orders[ask]) if ask is not None else 0
        return bid, ask, bvol, avol

    def _mid(self, depth):
        bid, ask, _, _ = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2.0

    def _microprice(self, depth):
        bid, ask, bvol, avol = self._best_bid_ask(depth)
        if bid is None or ask is None:
            return None
        total = bvol + avol
        if total <= 0:
            return (bid + ask) / 2.0
        return (ask * bvol + bid * avol) / total

    def _obi(self, bvol, avol):
        total = bvol + avol
        if total <= 0:
            return 0.0
        return (bvol - avol) / total

    def _ema(self, cache, key, value, alpha):
        old = cache.get(key)
        new = (1 - alpha) * float(old) + alpha * value if isinstance(old, (int, float)) else value
        cache[key] = round(float(new), 6)
        return float(new)

    def _load_cache(self, trader_data):
        if not trader_data:
            return {}
        try:
            parsed = json.loads(trader_data)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, json.JSONDecodeError):
            return {}