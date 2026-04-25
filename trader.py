from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, Trade, TradingState
except ModuleNotFoundError:
    try:
        from prosperity3bt.datamodel import Order, OrderDepth, Trade, TradingState
    except ModuleNotFoundError:
        from prosperity4bt.datamodel import Order, OrderDepth, Trade, TradingState

# ---------------------------------------------------------------------------
# Black-Scholes helpers
# ---------------------------------------------------------------------------

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_call(S: float, K: float, T: float, sigma: float) -> float:
    intrinsic = max(0.0, S - K)
    if T <= 0 or sigma <= 0 or S <= 0:
        return intrinsic
    vs = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / vs
    d2 = d1 - vs
    return S * _norm_cdf(d1) - K * _norm_cdf(d2)


def bs_delta(S: float, K: float, T: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0 or S <= 0:
        return 1.0 if S >= K else 0.0
    vs = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / vs
    return _norm_cdf(d1)


# ---------------------------------------------------------------------------
# OrderManager — copied verbatim from jack_vevev_v5.py
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SIGMA = 0.22          # market implied vol
TTE_START = 8         # historical data starts at TTE=8; set 5 for live round

HGP = "HYDROGEL_PACK"
VEV = "VELVETFRUIT_EXTRACT"

HGP_FV = 10000
HGP_LIMIT = 200
HGP_EDGE = 2
HGP_OBI_THRESH = 0.15

VEV_LIMIT = 200
VEV_EDGE = 3          # passive edge around live mid — must exceed half-spread (~2.5 ticks)
VEV_INV_CAP = 50      # soft inventory cap — reduce size aggressively beyond this
VEV_OBI_THRESH = 0.15

MM_VOUCHERS: Dict[str, int] = {"VEV_5300": 5300, "VEV_5400": 5400}
SELL_VOUCHERS: List[str] = ["VEV_5500", "VEV_6000", "VEV_6500"]
VOUCHER_LIMITS: Dict[str, int] = {
    "VEV_5300": 300,
    "VEV_5400": 300,
    "VEV_5500": 300,
    "VEV_6000": 300,
    "VEV_6500": 300,
}
VOUCHER_STRIKES: Dict[str, int] = {
    "VEV_5300": 5300,
    "VEV_5400": 5400,
    "VEV_5500": 5500,
    "VEV_6000": 6000,
    "VEV_6500": 6500,
}

VOUCHER_EDGE = 1        # ticks around BS fair for passive quotes
VOUCHER_SIZE = 15       # units per passive quote
OTM_SELL_SIZE = 30      # units to sell per tick for OTM/sell vouchers

DELTA_HEDGE_THRESH = 10.0  # net VEV-equivalent delta before hedging
DELTA_SLICE = 10           # max VEV units to hedge per tick

ALL_LIMITS: Dict[str, int] = {
    HGP: HGP_LIMIT,
    VEV: VEV_LIMIT,
    **VOUCHER_LIMITS,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _obi(depth: Optional[OrderDepth]) -> float:
    """Order book imbalance over top 3 levels, contrarian signal."""
    if depth is None:
        return 0.0
    bid_vol = sum(list(depth.buy_orders.values())[:3])
    ask_vol = sum(-v for v in list(depth.sell_orders.values())[:3])
    total = bid_vol + ask_vol
    if total == 0:
        return 0.0
    return (bid_vol - ask_vol) / total


def _mid_price(depth: Optional[OrderDepth]) -> Optional[float]:
    if depth is None or not depth.buy_orders or not depth.sell_orders:
        return None
    return (max(depth.buy_orders) + min(depth.sell_orders)) / 2.0


def _best_bid(depth: OrderDepth) -> Optional[int]:
    return max(depth.buy_orders) if depth.buy_orders else None


def _best_ask(depth: OrderDepth) -> Optional[int]:
    return min(depth.sell_orders) if depth.sell_orders else None


# ---------------------------------------------------------------------------
# Trader
# ---------------------------------------------------------------------------

class Trader:

    def bid(self) -> int:
        # Round 2 MAF bid — safe to include in all rounds (silently ignored outside R2)
        return 0

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {}
        om = OrderManager(state, ALL_LIMITS, result)

        # ── TTE / T ──────────────────────────────────────────────────────────
        day = int(state.timestamp) // 1_000_000
        tte = max(1, TTE_START - day)
        T = tte / 365.0

        # ── VEV mid for BS ───────────────────────────────────────────────────
        vev_depth = state.order_depths.get(VEV)
        vev_mid = _mid_price(vev_depth)
        if vev_mid is None:
            vev_mid = 5250.0

        # ── HYDROGEL_PACK MM ─────────────────────────────────────────────────
        hgp_depth = state.order_depths.get(HGP)
        if hgp_depth is not None:
            self._trade_hgp(hgp_depth, om)

        # ── VELVETFRUIT_EXTRACT MM (passive, delta-hedge appended later) ──────
        if vev_depth is not None:
            self._trade_vev_passive(vev_depth, om, vev_mid)

        # ── ATM Voucher passive MM ────────────────────────────────────────────
        net_option_delta = 0.0
        all_voucher_syms = list(MM_VOUCHERS.items()) + [(s, VOUCHER_STRIKES[s]) for s in SELL_VOUCHERS]
        for sym, K in all_voucher_syms:
            depth = state.order_depths.get(sym)
            if depth is None:
                continue
            # Use live market mid as the fair reference; BS is used only for delta
            mkt_mid = _mid_price(depth)
            bs_fair = bs_call(vev_mid, K, T, SIGMA)
            # Classify: OTM if BS fair < 2.0 or market mid < 1.5
            if bs_fair < 2.0 or (mkt_mid is not None and mkt_mid < 1.5):
                self._sell_otm_voucher(sym, depth, om)
            else:
                # Fair = live market mid (avoids BS mismatch penalty)
                fair = mkt_mid if mkt_mid is not None else bs_fair
                self._trade_atm_voucher(sym, depth, fair, om)
            pos_v = om.pos(sym)
            net_option_delta += pos_v * bs_delta(vev_mid, K, T, SIGMA)

        # ── Delta hedge via VEV ───────────────────────────────────────────────
        if vev_depth is not None:
            vev_pos = om.pos(VEV)
            net_total_delta = vev_pos + net_option_delta
            if abs(net_total_delta) > DELTA_HEDGE_THRESH:
                best_bid_vev = _best_bid(vev_depth)
                best_ask_vev = _best_ask(vev_depth)
                if net_total_delta > 0 and best_bid_vev is not None:
                    # Net long delta → sell VEV
                    sell_cap = ALL_LIMITS[VEV] + om.pos(VEV)
                    qty = min(DELTA_SLICE, sell_cap)
                    if qty > 0:
                        om.add(VEV, best_bid_vev, -qty)
                elif net_total_delta < 0 and best_ask_vev is not None:
                    # Net short delta → buy VEV
                    buy_cap = ALL_LIMITS[VEV] - om.pos(VEV)
                    qty = min(DELTA_SLICE, buy_cap)
                    if qty > 0:
                        om.add(VEV, best_ask_vev, qty)

        return result, 0, json.dumps({})

    # -------------------------------------------------------------------------
    # HYDROGEL_PACK
    # -------------------------------------------------------------------------

    def _trade_hgp(self, depth: OrderDepth, om: OrderManager) -> None:
        pos = om.pos(HGP)
        obi = _obi(depth)

        # Aggressive take
        for ask_px in sorted(depth.sell_orders.keys()):
            if ask_px >= HGP_FV:
                break
            vol = -depth.sell_orders[ask_px]
            buy_cap = HGP_LIMIT - om.pos(HGP)
            qty = min(vol, buy_cap)
            if qty > 0:
                om.add(HGP, ask_px, qty)

        for bid_px in sorted(depth.buy_orders.keys(), reverse=True):
            if bid_px <= HGP_FV:
                break
            vol = depth.buy_orders[bid_px]
            sell_cap = HGP_LIMIT + om.pos(HGP)
            qty = min(vol, sell_cap)
            if qty > 0:
                om.add(HGP, bid_px, -qty)

        # Passive quotes
        pos = om.pos(HGP)
        inv_skew = int(round(pos / HGP_LIMIT * HGP_EDGE))
        bid_px = HGP_FV - HGP_EDGE + inv_skew
        ask_px = HGP_FV + HGP_EDGE + inv_skew

        best_bid = _best_bid(depth)
        best_ask = _best_ask(depth)

        size_scale = max(0.3, 1.0 - abs(pos / HGP_LIMIT) * 0.7)
        sz = max(1, int(HGP_LIMIT * 0.15 * size_scale))

        # OBI contrarian: high bid vol predicts down → suppress bid
        post_bid = obi <= HGP_OBI_THRESH
        post_ask = obi >= -HGP_OBI_THRESH

        if post_bid and (best_ask is None or bid_px < best_ask):
            om.add(HGP, bid_px, sz)

        if post_ask and (best_bid is None or ask_px > best_bid):
            om.add(HGP, ask_px, -sz)

    # -------------------------------------------------------------------------
    # VELVETFRUIT_EXTRACT (passive MM around live mid; delta hedge in run())
    # -------------------------------------------------------------------------

    def _trade_vev_passive(self, depth: OrderDepth, om: OrderManager, vev_mid: float) -> None:
        pos = om.pos(VEV)
        obi = _obi(depth)

        best_bid = _best_bid(depth)
        best_ask = _best_ask(depth)

        # Use live mid from order book as FV reference — NOT a fixed price
        ref = int(round(vev_mid))

        # Inventory skew: push quotes away from side we're already heavy on
        inv_skew = int(round(pos / VEV_LIMIT * VEV_EDGE))
        bid_px = ref - VEV_EDGE + inv_skew
        ask_px = ref + VEV_EDGE + inv_skew

        # Guard: must stay outside the existing book (never inside spread)
        if best_bid is not None:
            bid_px = min(bid_px, best_bid)   # no better than market bid
        if best_ask is not None:
            ask_px = max(ask_px, best_ask)   # no better than market ask

        # Scale size by how far we are from inventory cap
        overweight = max(0.0, (abs(pos) - VEV_INV_CAP) / VEV_LIMIT)
        size_scale = max(0.05, 0.5 - overweight * 2.0)
        sz = max(1, int(VEV_LIMIT * size_scale))

        # OBI contrarian: high bid volume predicts down → suppress bid
        post_bid = obi <= VEV_OBI_THRESH
        post_ask = obi >= -VEV_OBI_THRESH

        if post_bid and (best_ask is None or bid_px < best_ask):
            om.add(VEV, bid_px, sz)

        if post_ask and (best_bid is None or ask_px > best_bid):
            om.add(VEV, ask_px, -sz)

    # -------------------------------------------------------------------------
    # ATM voucher passive MM
    # -------------------------------------------------------------------------

    def _trade_atm_voucher(self, sym: str, depth: OrderDepth, fair: float, om: OrderManager) -> None:
        fair_int = int(round(fair))
        bid_px = max(1, fair_int - VOUCHER_EDGE)
        ask_px = fair_int + VOUCHER_EDGE

        best_bid = _best_bid(depth)
        best_ask = _best_ask(depth)

        # Aggressive take: take asks at or below our bid target, bids at or above ask target
        if best_ask is not None and best_ask <= bid_px:
            vol = -depth.sell_orders[best_ask]
            qty = min(vol, VOUCHER_SIZE)
            om.add(sym, best_ask, qty)

        if best_bid is not None and best_bid >= ask_px:
            vol = depth.buy_orders[best_bid]
            qty = min(vol, VOUCHER_SIZE)
            om.add(sym, best_bid, -qty)

        # Passive quotes
        if best_ask is None or bid_px < best_ask:
            om.add(sym, bid_px, VOUCHER_SIZE)

        if best_bid is None or ask_px > best_bid:
            om.add(sym, ask_px, -VOUCHER_SIZE)

    # -------------------------------------------------------------------------
    # OTM / sell-only vouchers
    # -------------------------------------------------------------------------

    def _sell_otm_voucher(self, sym: str, depth: OrderDepth, om: OrderManager) -> None:
        best_bid = _best_bid(depth)
        # Sell at price 1 (or best bid if available and >= 1)
        sell_px = max(1, best_bid) if best_bid is not None else 1
        sell_cap = VOUCHER_LIMITS[sym] + om.pos(sym)
        qty = min(OTM_SELL_SIZE, sell_cap)
        if qty > 0:
            om.add(sym, sell_px, -qty)
