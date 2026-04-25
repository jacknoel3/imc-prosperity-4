from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict, Optional
from math import log, sqrt
from statistics import NormalDist
import json

# ── Constants ────────────────────────────────────────────────────────────────
HGP_LIMIT   = 200
HGP_FV      = 10000
HGP_EDGE    = 2

VEV_LIMIT   = 200
VEV_FV      = 5250
VEV_EDGE    = 1

OPT_LIMIT   = 300
SIGMA       = 0.342
TTE_START_DAYS = 5

# Strikes we actively trade
ACTIVE_STRIKES = {5400, 5300, 6000, 6500}

# ── NormalDist CDF ────────────────────────────────────────────────────────────
_N = NormalDist().cdf


class Trader:

    def bid(self):
        return 1

    # ── BS pricer ─────────────────────────────────────────────────────────────
    def _bs_call(self, S: float, K: float, T: float, sigma: float = SIGMA) -> float:
        if T <= 0 or S <= 0:
            return max(0.0, float(S - K))
        d1 = (log(max(S, 0.001) / max(K, 0.001)) + 0.5 * sigma ** 2 * T) / (sigma * sqrt(T))
        d2 = d1 - sigma * sqrt(T)
        return S * _N(d1) - K * _N(d2)

    # ── TTE helper ────────────────────────────────────────────────────────────
    def _tte(self, timestamp: int) -> float:
        day_number = timestamp // 10_000
        return max(0.001, (TTE_START_DAYS - day_number) / 365)

    # ── VEV mid-price helper ──────────────────────────────────────────────────
    def _vev_mid(self, depth: OrderDepth) -> float:
        bids = depth.buy_orders
        asks = depth.sell_orders
        if bids and asks:
            return (max(bids) + min(asks)) / 2.0
        if bids:
            return float(max(bids))
        if asks:
            return float(min(asks))
        return float(VEV_FV)

    # ── Fixed-FV MM (HGP and VEV) ─────────────────────────────────────────────
    def _trade_mm(
        self,
        symbol: str,
        depth: OrderDepth,
        pos: int,
        limit: int,
        fv: int,
        edge: int,
        max_quote_size: Optional[int] = None,
    ) -> List[Order]:
        orders: List[Order] = []
        buy_cap  = limit - pos
        sell_cap = limit + pos

        asks_sorted = sorted(depth.sell_orders.items())   # ascending price
        bids_sorted = sorted(depth.buy_orders.items(), reverse=True)  # descending

        best_bid = bids_sorted[0][0] if bids_sorted else (fv - edge - 1)
        best_ask = asks_sorted[0][0] if asks_sorted else (fv + edge + 1)

        # Aggressive takes
        for ask_px, ask_vol in asks_sorted:
            if ask_px < fv and buy_cap > 0:
                qty = min(-ask_vol, buy_cap)
                orders.append(Order(symbol, int(ask_px), int(qty)))
                pos      += qty
                buy_cap  -= qty
                sell_cap += qty

        for bid_px, bid_vol in bids_sorted:
            if bid_px > fv and sell_cap > 0:
                qty = min(bid_vol, sell_cap)
                orders.append(Order(symbol, int(bid_px), -int(qty)))
                pos      -= qty
                sell_cap -= qty
                buy_cap  += qty

        # Taper size
        scale     = max(1, int(max(0.3, 1.0 - abs(pos / limit) * 0.7) * limit))
        if max_quote_size is not None:
            scale = min(scale, max_quote_size)
        pass_bid  = int(fv) - edge
        pass_ask  = int(fv) + edge

        if buy_cap > 0 and pass_bid < best_ask:
            orders.append(Order(symbol, pass_bid, min(scale, buy_cap)))

        if sell_cap > 0 and pass_ask > best_bid:
            orders.append(Order(symbol, pass_ask, -min(scale, sell_cap)))

        return orders

    # ── VEV_5400 ──────────────────────────────────────────────────────────────
    def _trade_5400(
        self,
        depth: OrderDepth,
        pos: int,
        vev_mid: float,
        tte: float,
        ts: dict,
        timestamp: int,
    ) -> List[Order]:
        orders: List[Order] = []
        buy_cap = OPT_LIMIT - pos

        bs_fair = self._bs_call(vev_mid, 5400, tte)
        pass_bid_px = int(bs_fair - 0.5)

        asks_sorted = sorted(depth.sell_orders.items())
        best_ask_5400 = asks_sorted[0][0] if asks_sorted else None

        # Day-0 early aggressive: buy up to 30 if deeply underpriced
        if (
            best_ask_5400 is not None
            and bs_fair - best_ask_5400 > 1.0
            and timestamp < 100_000
            and buy_cap > 0
        ):
            qty = min(30 - pos, buy_cap, -depth.sell_orders[best_ask_5400])
            if qty > 0:
                orders.append(Order("VEV_5400", int(best_ask_5400), int(qty)))
                pos     += qty
                buy_cap -= qty

        # Passive bid: buy only, never cross the current ask.
        if buy_cap > 0:
            size = min(20, OPT_LIMIT - pos, buy_cap)
            if size > 0:
                if best_ask_5400 is None or pass_bid_px < best_ask_5400:
                    orders.append(Order("VEV_5400", pass_bid_px, int(size)))

        return orders

    # ── VEV_5300 ──────────────────────────────────────────────────────────────
    def _trade_5300(
        self,
        depth: OrderDepth,
        pos: int,
        vev_mid: float,
        tte: float,
    ) -> List[Order]:
        orders: List[Order] = []
        buy_cap = OPT_LIMIT - pos

        bs_fair = self._bs_call(vev_mid, 5300, tte)
        pass_bid_px = int(bs_fair - 0.5)

        asks_sorted = sorted(depth.sell_orders.items())
        best_ask_5300 = asks_sorted[0][0] if asks_sorted else None

        if buy_cap > 0:
            size = min(5, 30 - pos, buy_cap)
            if size > 0:
                if best_ask_5300 is None or pass_bid_px < best_ask_5300:
                    orders.append(Order("VEV_5300", pass_bid_px, int(size)))

        return orders

    # ── VEV_6000 / VEV_6500 ───────────────────────────────────────────────────
    def _trade_otm_call(
        self,
        symbol: str,
        depth: OrderDepth,
        pos: int,
        vev_mid: float,
    ) -> List[Order]:
        orders: List[Order] = []
        # Kill switch: skip if underlying has rallied
        if vev_mid > 5500:
            return orders

        sell_cap = OPT_LIMIT + pos   # pos is negative when short

        size = min(10, 100 - abs(pos), sell_cap)
        if size > 0:
            orders.append(Order(symbol, 1, -int(size)))

        return orders

    # ── Delta hedge for options ───────────────────────────────────────────────
    def _delta_hedge(
        self,
        vev_depth: OrderDepth,
        vev_pos: int,
        hedge_qty: int,
    ) -> List[Order]:
        orders: List[Order] = []

        if hedge_qty <= 0:
            return orders

        sell_cap = VEV_LIMIT + vev_pos
        qty = min(hedge_qty, sell_cap)
        if qty <= 0:
            return orders

        bids = vev_depth.buy_orders
        if not bids:
            return orders

        best_bid = max(bids)
        orders.append(Order("VELVETFRUIT_EXTRACT", int(best_bid), -int(qty)))
        return orders

    # ── Compute new hedge pending from own_trades ─────────────────────────────
    def _compute_hedge_qty(self, state: TradingState) -> int:
        fills_5400 = 0
        for t in state.own_trades.get("VEV_5400", []):
            if t.buyer == "SUBMISSION":
                fills_5400 += t.quantity

        fills_5300 = 0
        for t in state.own_trades.get("VEV_5300", []):
            if t.buyer == "SUBMISSION":
                fills_5300 += t.quantity

        return int(round(fills_5400 * 0.129 + fills_5300 * 0.30))

    # ── Main run ──────────────────────────────────────────────────────────────
    def run(self, state: TradingState):
        # Deserialise persisted state
        try:
            ts: dict = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            ts = {}

        result: Dict[str, List[Order]] = {}
        positions = state.position

        tte = self._tte(state.timestamp)

        # Derive VEV mid from order book
        vev_depth = state.order_depths.get("VELVETFRUIT_EXTRACT")
        if vev_depth:
            vev_mid = self._vev_mid(vev_depth)
        else:
            vev_mid = float(ts.get("vev_mid", VEV_FV))

        # ── HYDROGEL_PACK ──
        if "HYDROGEL_PACK" in state.order_depths:
            pos_hgp = positions.get("HYDROGEL_PACK", 0)
            result["HYDROGEL_PACK"] = self._trade_mm(
                "HYDROGEL_PACK",
                state.order_depths["HYDROGEL_PACK"],
                pos_hgp,
                HGP_LIMIT,
                HGP_FV,
                HGP_EDGE,
            )

        # ── VELVETFRUIT_EXTRACT ──
        vev_orders: List[Order] = []
        pos_vev = positions.get("VELVETFRUIT_EXTRACT", 0)

        # Delta hedge from prior tick
        if vev_depth:
            hedge_qty = self._compute_hedge_qty(state)
            hedge_orders = self._delta_hedge(vev_depth, pos_vev, hedge_qty)
            vev_orders.extend(hedge_orders)
            # Update local pos after hedge sells
            for o in hedge_orders:
                pos_vev += o.quantity

        # MM on underlying
        if vev_depth:
            mm_orders = self._trade_mm(
                "VELVETFRUIT_EXTRACT",
                vev_depth,
                pos_vev,
                VEV_LIMIT,
                VEV_FV,
                VEV_EDGE,
                40,
            )
            vev_orders.extend(mm_orders)

        if vev_orders:
            result["VELVETFRUIT_EXTRACT"] = vev_orders

        # ── VEV_5400 ──
        if "VEV_5400" in state.order_depths:
            pos_5400 = positions.get("VEV_5400", 0)
            result["VEV_5400"] = self._trade_5400(
                state.order_depths["VEV_5400"],
                pos_5400,
                vev_mid,
                tte,
                ts,
                state.timestamp,
            )

        # ── VEV_5300 ──
        if "VEV_5300" in state.order_depths:
            pos_5300 = positions.get("VEV_5300", 0)
            result["VEV_5300"] = self._trade_5300(
                state.order_depths["VEV_5300"],
                pos_5300,
                vev_mid,
                tte,
            )

        # ── VEV_6000 ──
        if "VEV_6000" in state.order_depths:
            pos_6000 = positions.get("VEV_6000", 0)
            result["VEV_6000"] = self._trade_otm_call(
                "VEV_6000",
                state.order_depths["VEV_6000"],
                pos_6000,
                vev_mid,
            )

        # ── VEV_6500 ──
        if "VEV_6500" in state.order_depths:
            pos_6500 = positions.get("VEV_6500", 0)
            result["VEV_6500"] = self._trade_otm_call(
                "VEV_6500",
                state.order_depths["VEV_6500"],
                pos_6500,
                vev_mid,
            )

        # ── Serialise state ──
        ts_out = {
            "h_pos": positions.get("HYDROGEL_PACK", 0),
            "v_pos": positions.get("VELVETFRUIT_EXTRACT", 0),
            "v54_pos": positions.get("VEV_5400", 0),
            "v53_pos": positions.get("VEV_5300", 0),
            "v60_pos": positions.get("VEV_6000", 0),
            "v65_pos": positions.get("VEV_6500", 0),
            "hedge_vev": pos_vev - positions.get("VELVETFRUIT_EXTRACT", 0),
            "vev_mid": vev_mid,
        }
        traderData = json.dumps(ts_out)

        return result, 0, traderData