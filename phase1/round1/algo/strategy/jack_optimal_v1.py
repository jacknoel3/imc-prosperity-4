"""
Optimal Round 1 Strategy — Jack
================================
ASH_COATED_OSMIUM : Aggressive-take + front-of-queue market maker
INTARIAN_PEPPER_ROOT : Instant full accumulation + unconditional hold

Design principle
----------------
The single biggest fill-rate lever in this simulator is *queue-ahead*.
If you post at the SAME price as a bot, queue_ahead_factor (0.75 for ASH,
0.65 for Pepper) means 65–75 % of the bot's size sits ahead of you.
If you post ONE TICK better than the best bot quote, you have ZERO
queue-ahead: every incoming taker fills you first.

For ASH this means posting bid = best_bid + 1, ask = best_ask - 1.
Bots sit at roughly fair ± 8 (spread_mode = 16).  We sit at ± 7,
capturing ~14 ticks per round trip vs their 16 — but we are always
filled before them and pay nothing for that priority.

For Pepper, the +0.0999/tick drift accumulates to ~+999/day.
Holding +80 for the full session is worth ≈80 × 999 ≈ 79 900 XIRECs.
The entire spread cost to fill 80 units is only ≈80 × 6.5 = 520.
Nothing beats immediate accumulation + unconditional hold.

Usage (from imc-prosperity-4-montecarlo/):
    prosperity4mcbt jack_optimal_v1.py --quick
    prosperity4mcbt jack_optimal_v1.py --heavy --out backtests/jack_optimal_v1_dashboard.json
"""

try:
    from datamodel import Order, OrderDepth, TradingState
except ImportError:
    from prosperity3bt.datamodel import Order, OrderDepth, TradingState

from typing import Dict, List

ASH   = "ASH_COATED_OSMIUM"
IPR   = "INTARIAN_PEPPER_ROOT"
LIMIT = 80


class Trader:
    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        for symbol, depth in state.order_depths.items():
            pos = state.position.get(symbol, 0)
            if symbol == ASH:
                result[symbol] = self._ash(depth, pos)
            elif symbol == IPR:
                result[symbol] = self._ipr(depth, pos)
        return result, 0, ""

    # ── ASH: fixed-FV market maker, front-of-queue passive quotes ─────────────

    def _ash(self, depth: OrderDepth, pos: int) -> List[Order]:
        orders: List[Order] = []
        if not depth.buy_orders or not depth.sell_orders:
            return orders

        best_bid = max(depth.buy_orders)
        best_ask = min(depth.sell_orders)
        fv = 10_000

        # Aggressive take: any price that crosses FV = pure edge, fill immediately
        for px in sorted(depth.sell_orders):
            if px >= fv:
                break
            qty = min(-depth.sell_orders[px], LIMIT - pos)
            if qty > 0:
                orders.append(Order(ASH, px, qty))
                pos += qty

        for px in sorted(depth.buy_orders, reverse=True):
            if px <= fv:
                break
            qty = min(depth.buy_orders[px], LIMIT + pos)
            if qty > 0:
                orders.append(Order(ASH, px, -qty))
                pos -= qty

        # Imbalance tilt: book imbalance predicts next tick (r ≈ 0.38)
        tbv = sum(depth.buy_orders.values())
        tav = sum(-v for v in depth.sell_orders.values())
        tot = tbv + tav
        imb   = (tbv - tav) / tot if tot > 0 else 0.0
        tilt  = 1 if imb > 0.25 else (-1 if imb < -0.25 else 0)

        # Front-of-queue passive MM:
        # Post 1 tick better than best bot quote → zero queue-ahead, max fill priority
        # Bots post at ~9992 / 10008; we post at ~9993 / 10007, capturing ~14 ticks/round-trip
        our_bid = best_bid + 1 + tilt
        our_ask = best_ask - 1 + tilt

        # Guard: never self-cross or cross the live book
        if our_bid >= our_ask:
            mid = (best_bid + best_ask) // 2
            our_bid, our_ask = mid - 1, mid + 1
        our_bid = min(our_bid, best_ask - 1)
        our_ask = max(our_ask, best_bid + 1)

        # Full remaining capacity: fill as many takers as possible each tick
        buy_cap  = LIMIT - pos
        sell_cap = LIMIT + pos
        if buy_cap  > 0: orders.append(Order(ASH, our_bid,  buy_cap))
        if sell_cap > 0: orders.append(Order(ASH, our_ask, -sell_cap))
        return orders

    # ── IPR: sweep all asks then hold ─────────────────────────────────────────

    def _ipr(self, depth: OrderDepth, pos: int) -> List[Order]:
        orders: List[Order] = []
        if pos >= LIMIT:
            return orders  # At limit: hold, drift earns ~+999/session passively

        best_bid  = max(depth.buy_orders)  if depth.buy_orders  else None
        best_ask  = min(depth.sell_orders) if depth.sell_orders else None
        remaining = LIMIT - pos

        # Cross ALL available ask levels for guaranteed, immediate fills
        if best_ask is not None:
            for ask_px in sorted(depth.sell_orders):
                if remaining <= 0:
                    break
                qty = min(-depth.sell_orders[ask_px], remaining)
                if qty > 0:
                    orders.append(Order(IPR, ask_px, qty))
                    remaining -= qty

        # Front-of-queue passive bid: zero queue-ahead, earns half-spread if a sell
        # taker arrives.  Caps at 20 so we don't over-commit on a single level.
        if remaining > 0 and best_bid is not None:
            pb = best_bid + 1
            if best_ask is None or pb < best_ask:
                orders.append(Order(IPR, pb, min(remaining, 20)))

        return orders
