from __future__ import annotations

"""
strat55_discord_blueprint.py
============================

Strategia che implementa LETTERALMENTE quanto suggerito nelle immagini di Discord
(Strategy Notes + Priority Algorithm Checklist + Counterparty Behavior Profiles),
SENZA correzioni in base ai nostri findings interni.

Scopo: avere un benchmark "what Discord says" da confrontare con strat56.

Avvertenza esplicita — punti dove Discord è in conflitto con i nostri test:
  1) "HGP trend +15/day, bullish skew" → i nostri 3 giorni mostrano FV stazionario a 10_000.
     Implementato fedelmente: applichiamo bias bullish lentamente.
  2) "OTM 5300/5400/5500 short = KEY ALPHA" → a TTE=4d con IV mercato 22-24%, P(ITM)
     non è trascurabile e RV=34.2%. Discord dà per scontato TTE=7d.
     Implementato fedelmente: short aggressivo full size + delta hedge OTM=0.
  3) "Front-run Mark 67 su VEV_4000" → nei dataset Mark 67 trada SOLO VE (mai VEV_4000).
     Implementato fedelmente: bid passivo aggressivo VEV_4000 quando Mark 67 ha appena
     comprato VE (proxy del segnale Discord).
  4) "Mark 14/38 arb: quote inside on VEV_4000" → Mark 14 è informato (MO10 +9.81 su VEV_4000),
     non è risk-free MM. Implementato fedelmente: quotes inside best bid/ask.
  5) Delta ATM = 0.5 (Discord) — usato fedelmente; in realtà a TTE=4d VEV_5400 ha delta ~0.13.

Architettura:
  TG01 — HGP mean-reversion MM con drift bullish day-over-day
  TG02 — VEF pure MM ±3-5 ticks intorno a EMA(20)
  TG03 — VEV options:
    * VEV_4000, 4500   → trade vicino intrinsic (S - K) con piccolo premio
    * VEV_5000, 5100, 5200 → MM ATM, vendi se time value > 50 (Discord: "VEV_5200 50 XIRECS time value")
    * VEV_5300, 5400, 5500 → SHORT EARLY (key alpha Discord)
    * VEV_6000, 6500   → skip
  Delta hedging: ITM=1.0, ATM=0.5, OTM=0.0 (esattamente come scrive Discord)
  Player overlay: Mark 67 front-run on VEV_4000, Mark 14/38 inside-quote arb on VEV_4000.

NB: questa NON è la strategia finale da uploadare. È un test "Discord faithful" per
misurare quanto i suoi suggerimenti reggano in backtest contro strat48/strat56.
"""

import json
from typing import Any, Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ModuleNotFoundError:
    from prosperity3bt.datamodel import Order, OrderDepth, TradingState


# ============================================================================
# OrderManager — same lifecycle pattern as strat46/48 (position-limit safety)
# ============================================================================
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
    LIMITS = {HGP: 200, VE: 200, **{v: 300 for v in VOUCHERS}}
    STRIKE = {
        "VEV_4000": 4000, "VEV_4500": 4500, "VEV_5000": 5000, "VEV_5100": 5100,
        "VEV_5200": 5200, "VEV_5300": 5300, "VEV_5400": 5400, "VEV_5500": 5500,
        "VEV_6000": 6000, "VEV_6500": 6500,
    }
    # Discord blueprint: ITM=1.0, ATM=0.5, OTM=0.0 (uso fedele dei valori)
    DELTA_DISCORD = {
        "VEV_4000": 1.0, "VEV_4500": 1.0,                   # Deep ITM
        "VEV_5000": 1.0, "VEV_5100": 0.5, "VEV_5200": 0.5,  # ATM
        "VEV_5300": 0.5, "VEV_5400": 0.0, "VEV_5500": 0.0,  # OTM
        "VEV_6000": 0.0, "VEV_6500": 0.0,                   # Deep OTM
    }
    # OTM strikes che Discord dichiara "key alpha — short early"
    OTM_SHORT_TARGETS = ("VEV_5300", "VEV_5400", "VEV_5500")
    ATM_MM = ("VEV_5000", "VEV_5100", "VEV_5200")
    ITM_MM = ("VEV_4000", "VEV_4500")

    # Anchors da Discord: VEF ~5247, HGP ~9995→10007 (drift)
    VEF_ANCHOR = 5247.0
    HGP_BASE = 9995.0
    HGP_DAY_DRIFT = 15.0     # Discord: "raise FV by ~15 per day"
    HGP_SIGMA = 34.6
    HGP_MM_EDGE = 17         # Discord ±15-20

    # Soft caps coerenti col blueprint Discord (fascia ±50 per VEF)
    SOFT = {
        HGP: 100,            # MM passivo, ±50-100 in inventario
        VE: 50,              # Discord: "Position limit ±50"
        "VEV_4000": 25, "VEV_4500": 20,
        "VEV_5000": 80, "VEV_5100": 80, "VEV_5200": 80,
        "VEV_5300": 120, "VEV_5400": 150, "VEV_5500": 150,  # KEY ALPHA short
        "VEV_6000": 0, "VEV_6500": 0,
    }

    PLAYER_WINDOW = 5000  # ttl per il segnale Mark67 / Mark14-38

    def bid(self) -> int:
        return 1

    # ------------------------------------------------------------------ run
    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {p: [] for p in self.LIMITS}
        cache = self._load_cache(state.traderData)
        ts = int(getattr(state, "timestamp", 0))
        cache["now"] = ts
        # Discord assume "day 3 mean > day 1": rough drift proxy basato su step count.
        # Useremo un'approssimazione: ogni 100k ticks = 1 day.
        cache["session_day"] = ts // 100_000

        self._update_player_signals(state, cache, ts)

        om = OrderManager(state, self.LIMITS, result)

        # TG01 - Hydrogel Pack mean-reversion MM con bullish drift
        depth_hgp = state.order_depths.get(self.HGP)
        if depth_hgp is not None:
            self._trade_hgp(depth_hgp, cache, om)

        # TG02 - VEF pure MM tight
        depth_ve = state.order_depths.get(self.VE)
        if depth_ve is not None:
            self._trade_vef(depth_ve, cache, om)

        # TG03 - VEV options come da blueprint
        for product in self.VOUCHERS:
            depth = state.order_depths.get(product)
            if depth is None:
                continue
            if product in ("VEV_6000", "VEV_6500"):
                continue  # Discord: "Don't waste positions here"
            elif product in self.ITM_MM:
                self._trade_itm(product, depth, cache, om)
            elif product in self.ATM_MM:
                self._trade_atm(product, depth, cache, om)
            elif product in self.OTM_SHORT_TARGETS:
                self._trade_otm_short(product, depth, cache, om)

        # Player overlays
        self._mark14_38_inside_quote(state, cache, om, ts)
        self._mark67_frontrun_vev4000(state, cache, om, ts)

        # Delta hedge VEF dopo voucher fills (Discord: "For each VEV position, hedge delta with VEF")
        self._delta_hedge_vef(state, cache, om)

        return result, 0, json.dumps(cache, separators=(",", ":"))

    # ------------------------------------------------------------------ TG01: HGP
    def _trade_hgp(self, depth: OrderDepth, cache: Dict[str, Any], om: OrderManager) -> None:
        bid, ask, _, _ = self._bba(depth)
        mid = self._mid(depth)
        if bid is None or ask is None or mid is None:
            return
        # EMA fair value (Discord: "±15-20 around EMA")
        ema = self._ema(cache, "hgp_ema", mid, 0.05)
        # Discord bullish drift day-over-day
        day = int(cache.get("session_day", 0))
        fair = ema + day * self.HGP_DAY_DRIFT  # bullish skew

        pos = om.pos(self.HGP)
        soft = self.SOFT[self.HGP]
        # Inventory tilt (Discord: "Inventory control critical")
        fair -= 0.05 * pos

        edge = self.HGP_MM_EDGE
        spread = ask - bid
        if spread <= 0:
            return

        # Quote prices: ±edge but inside best bid/ask
        bid_px = max(int(round(fair - edge)), bid + 1) if spread >= 3 else bid
        ask_px = min(int(round(fair + edge)), ask - 1) if spread >= 3 else ask
        if bid_px >= ask_px:
            return

        size = 15
        # Bullish skew on day 3 → bigger buy size
        if day >= 2:
            buy_size, sell_size = size + 4, max(2, size - 4)
        else:
            buy_size = sell_size = size

        # Aggressive take if mid significantly off fair
        if mid > fair + 2 * edge and pos > -soft:
            self._sweep(self.HGP, depth, "SELL", int(round(fair + edge)), min(sell_size, pos + soft), om)
        if mid < fair - 2 * edge and pos < soft:
            self._sweep(self.HGP, depth, "BUY", int(round(fair - edge)), min(buy_size, soft - pos), om)

        if pos < soft:
            om.add(self.HGP, bid_px, min(buy_size, soft - pos))
        if pos > -soft:
            om.add(self.HGP, ask_px, -min(sell_size, pos + soft))

    # ------------------------------------------------------------------ TG02: VEF
    def _trade_vef(self, depth: OrderDepth, cache: Dict[str, Any], om: OrderManager) -> None:
        bid, ask, _, _ = self._bba(depth)
        mid = self._mid(depth)
        if bid is None or ask is None or mid is None:
            return
        # Discord: "rolling 50-tick VWAP or EMA(20)" — usiamo EMA(20) ≈ alpha 0.1
        ema = self._ema(cache, "vef_ema20", mid, 0.10)
        anchor_w = 0.4
        fair = anchor_w * self.VEF_ANCHOR + (1 - anchor_w) * ema
        pos = om.pos(self.VE)
        soft = self.SOFT[self.VE]
        fair -= 0.04 * pos

        # Discord: "Quote ±3-5 ticks from mid"
        edge = 4
        spread = ask - bid
        if spread <= 0:
            return
        bid_px = bid + 1 if spread >= 3 else bid
        ask_px = ask - 1 if spread >= 3 else ask

        size = 25
        if pos > soft * 0.6:
            buy_size, sell_size = max(0, size // 4), int(size * 1.4)
        elif pos < -soft * 0.6:
            buy_size, sell_size = int(size * 1.4), max(0, size // 4)
        else:
            buy_size = sell_size = size

        if pos < soft and bid_px <= fair - 0.5:
            om.add(self.VE, bid_px, min(buy_size, soft - pos))
        if pos > -soft and ask_px >= fair + 0.5:
            om.add(self.VE, ask_px, -min(sell_size, pos + soft))

    # ------------------------------------------------------------------ TG03: ITM near intrinsic
    def _trade_itm(self, product: str, depth: OrderDepth, cache: Dict[str, Any], om: OrderManager) -> None:
        ve_mid_val = float(cache.get("ve_mid", 0.0))
        if ve_mid_val <= 0:
            return
        K = self.STRIKE[product]
        intrinsic = max(ve_mid_val - K, 0.0)  # Discord: "Market price ≈ S - K ± small premium"
        bid, ask, _, _ = self._bba(depth)
        if bid is None or ask is None:
            return
        small_premium = 4 if product == "VEV_4000" else 6
        fair = intrinsic + small_premium
        pos = om.pos(product)
        soft = self.SOFT[product]
        if soft <= 0:
            return
        # Discord: "Just track VEF price and quote accordingly"
        # Take if ask significantly below intrinsic+small_premium
        if pos < soft and ask <= fair - 2:
            qty = min(soft - pos, max(1, -int(depth.sell_orders.get(ask, 0))), 4)
            om.add(product, ask, qty)
        if pos > -soft and bid >= fair + 2:
            qty = min(pos + soft, int(depth.buy_orders.get(bid, 0)), 4)
            om.add(product, bid, -qty)
        # Passive at fair ± 3
        if pos < soft and bid + 1 < ask:
            om.add(product, bid + 1, min(soft - pos, 3))
        if pos > -soft and ask - 1 > bid:
            om.add(product, ask - 1, -min(pos + soft, 3))

    # ------------------------------------------------------------------ TG03: ATM MM (sell time value when expensive)
    def _trade_atm(self, product: str, depth: OrderDepth, cache: Dict[str, Any], om: OrderManager) -> None:
        bid, ask, _, _ = self._bba(depth)
        mid = self._mid(depth)
        if bid is None or ask is None or mid is None:
            return
        ve_mid_val = float(cache.get("ve_mid", 0.0))
        if ve_mid_val <= 0:
            return
        K = self.STRIKE[product]
        intrinsic = max(ve_mid_val - K, 0.0)
        time_value = max(0.0, mid - intrinsic)
        # Discord: "VEV_5200 had 50 XIRECS time value on day 1 — SELL opportunities!"
        # Threshold: vendi se time value > soglia per strike
        sell_threshold = {"VEV_5000": 80, "VEV_5100": 60, "VEV_5200": 50}[product]
        pos = om.pos(product)
        soft = self.SOFT[product]
        if soft <= 0:
            return

        # MM normale ATM: anchor + EMA mid
        key = product.lower().replace("_", "")
        ema = self._ema(cache, f"{key}_ema", mid, 0.04)
        fair = 0.5 * (intrinsic + ema) - 0.05 * pos
        edge = 1.5
        spread = ask - bid
        if spread <= 0:
            return
        bid_px = bid + 1 if spread >= 3 else bid
        ask_px = ask - 1 if spread >= 3 else ask
        size = 25

        # Discord SELL bias when time value high
        if time_value > sell_threshold and pos > -soft:
            sell_extra = int(size * 1.6)
            om.add(product, ask_px, -min(sell_extra, pos + soft))
            return

        if pos < soft and bid_px <= fair - edge:
            om.add(product, bid_px, min(size, soft - pos))
        if pos > -soft and ask_px >= fair + edge:
            om.add(product, ask_px, -min(size, pos + soft))

    # ------------------------------------------------------------------ TG03: OTM aggressive short (KEY ALPHA Discord)
    def _trade_otm_short(self, product: str, depth: OrderDepth, cache: Dict[str, Any], om: OrderManager) -> None:
        bid, ask, _, _ = self._bba(depth)
        mid = self._mid(depth)
        if bid is None or ask is None or mid is None:
            return
        pos = om.pos(product)
        soft = self.SOFT[product]
        if soft <= 0:
            return
        # Discord: "Short these early in the round = collect premium. KEY ALPHA"
        # Vendiamo aggressivamente al best bid/ask
        # Hit any bid > 0 fino a riempire il SOFT cap (full short)
        if pos > -soft:
            target_short = -soft
            need = pos - target_short  # quanti vendere
            # Hit bids
            for bp in sorted(depth.buy_orders.keys(), reverse=True):
                if need <= 0:
                    break
                vol = int(depth.buy_orders[bp])
                qty = min(need, vol, 8)  # batch size 8
                if qty <= 0:
                    break
                placed = -om.add(product, int(bp), -int(qty))
                need += placed
            # Quote passivo aggressivo (sell at ask-1)
            if need > 0 and ask - 1 > bid:
                ask_px = ask - 1
                om.add(product, ask_px, -min(need, 12))

    # ------------------------------------------------------------------ Mark 14/38 inside-quote arb on VEV_4000
    def _mark14_38_inside_quote(self, state: TradingState, cache: Dict[str, Any], om: OrderManager, ts: int) -> None:
        # Discord: "These two flip sides constantly on VEV_4000. Quote in between them for risk-free MM"
        # Implementazione fedele: quotiamo SEMPRE inside best bid/ask di VEV_4000 quando vediamo
        # un trade Mark 14↔Mark 38 negli ultimi 2000 ticks.
        last_event = int(cache.get("m14_38_last_ts", -1))
        market_trades = getattr(state, "market_trades", {}) or {}
        for trade in market_trades.get("VEV_4000", []) or []:
            buyer = getattr(trade, "buyer", "") or ""
            seller = getattr(trade, "seller", "") or ""
            if {buyer, seller} == {"Mark 14", "Mark 38"}:
                last_event = ts
        cache["m14_38_last_ts"] = last_event
        if last_event < 0 or ts - last_event > 2000:
            return
        depth = state.order_depths.get("VEV_4000")
        bid, ask, _, _ = self._bba(depth)
        if bid is None or ask is None or ask - bid < 4:
            return
        pos = om.pos("VEV_4000")
        soft = self.SOFT["VEV_4000"]
        # Quote tighter than current best (inside)
        if pos < soft:
            om.add("VEV_4000", bid + 1, min(2, soft - pos))
        if pos > -soft:
            om.add("VEV_4000", ask - 1, -min(2, pos + soft))

    # ------------------------------------------------------------------ Mark 67 front-run on VEV_4000
    def _mark67_frontrun_vev4000(self, state: TradingState, cache: Dict[str, Any], om: OrderManager, ts: int) -> None:
        # Discord: "Mark 67 aggressively buys VEV_4000. Front-run by acquiring before Mark 67 arrives."
        # Mark 67 nei dataset trada solo VE; usiamo Mark67 BUY VE come proxy del segnale di "arrival".
        last_event = int(cache.get("m67_last_ts", -1))
        market_trades = getattr(state, "market_trades", {}) or {}
        for trade in market_trades.get(self.VE, []) or []:
            if getattr(trade, "buyer", "") == "Mark 67":
                last_event = ts
        cache["m67_last_ts"] = last_event
        if last_event < 0 or ts - last_event > self.PLAYER_WINDOW:
            return
        depth = state.order_depths.get("VEV_4000")
        bid, ask, _, _ = self._bba(depth)
        if bid is None or ask is None:
            return
        pos = om.pos("VEV_4000")
        soft = self.SOFT["VEV_4000"]
        if pos < soft:
            om.add("VEV_4000", ask, min(3, soft - pos))

    # ------------------------------------------------------------------ Delta hedge VEF (Discord exact deltas)
    def _delta_hedge_vef(self, state: TradingState, cache: Dict[str, Any], om: OrderManager) -> None:
        depth_ve = state.order_depths.get(self.VE)
        ve_bid, ve_ask, _, _ = self._bba(depth_ve)
        if ve_bid is None or ve_ask is None:
            return
        net_delta = 0.0
        for v in self.VOUCHERS:
            net_delta += om.pos(v) * self.DELTA_DISCORD.get(v, 0.0)
        ve_pos = om.pos(self.VE)
        target = -net_delta  # neutralizza
        gap = target - ve_pos
        if abs(gap) < 8:
            return
        soft = self.LIMITS[self.VE]  # Discord ±50 ma usiamo limit pieno per hedging
        if gap > 0:
            qty = min(int(gap), max(0, soft - ve_pos), 8)
            if qty > 0:
                om.add(self.VE, int(ve_bid), qty)
        else:
            qty = min(int(-gap), max(0, ve_pos + soft), 8)
            if qty > 0:
                om.add(self.VE, int(ve_ask), -qty)

    # ------------------------------------------------------------------ helpers
    def _update_player_signals(self, state: TradingState, cache: Dict[str, Any], ts: int) -> None:
        # Memorizziamo solo VE mid per delta hedge / intrinsic
        depth_ve = state.order_depths.get(self.VE)
        ve_mid = self._mid(depth_ve)
        if ve_mid is not None:
            cache["ve_mid"] = round(float(ve_mid), 4)

    def _sweep(self, product: str, depth: OrderDepth, side: str, limit_px: int, qty: int, om: OrderManager) -> None:
        rem = max(0, int(qty))
        if side == "BUY":
            for price, vol in sorted(depth.sell_orders.items()):
                if rem <= 0 or price > limit_px:
                    break
                rem -= om.add(product, int(price), min(rem, -int(vol)))
        else:
            for price, vol in sorted(depth.buy_orders.items(), reverse=True):
                if rem <= 0 or price < limit_px:
                    break
                rem -= om.add(product, int(price), -min(rem, int(vol)))

    def _bba(self, depth):
        if depth is None:
            return None, None, 0, 0
        bid = max(depth.buy_orders) if depth.buy_orders else None
        ask = min(depth.sell_orders) if depth.sell_orders else None
        bvol = int(depth.buy_orders[bid]) if bid is not None else 0
        avol = -int(depth.sell_orders[ask]) if ask is not None else 0
        return bid, ask, bvol, avol

    def _mid(self, depth):
        bid, ask, _, _ = self._bba(depth)
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2.0

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


