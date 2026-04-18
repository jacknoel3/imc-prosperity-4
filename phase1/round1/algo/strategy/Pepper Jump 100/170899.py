from datamodel import Order, TradingState
from typing import Dict, List
import json

PRODUCT        = "INTARIAN_PEPPER_ROOT"
POS_LIMIT      = 80
EDGE           = 100
HALF_SPREAD    = 6.5   # historischer Durchschnitt: mean spread 13.0 / 2
TARGET_LONG    = 80

class Trader:

    def bid(self):
        return 15

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}

        try:
            mem = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            mem = {}

        mem.setdefault("prev_mid", None)

        ts       = state.timestamp
        position = state.position.get(PRODUCT, 0)
        orders: List[Order] = []

        if PRODUCT not in state.order_depths:
            result[PRODUCT] = orders
            return result, 0, json.dumps(mem)

        od       = state.order_depths[PRODUCT]
        has_bids = len(od.buy_orders)  > 0
        has_asks = len(od.sell_orders) > 0

        # --- Fair Value Berechnung ---
        # Beide Seiten vorhanden: echter Mid, als Referenz speichern
        if has_bids and has_asks:
            best_bid        = max(od.buy_orders.keys())
            best_ask        = min(od.sell_orders.keys())
            mem["prev_mid"] = (best_bid + best_ask) / 2.0
            fv = mem["prev_mid"]

        # Nur Bids vorhanden (keine Asks):
        # FV = best_bid + halber historischer Spread
        # Besser als prev_mid weil aktueller Preis direkt sichtbar
        elif has_bids and not has_asks:
            best_bid = max(od.buy_orders.keys())
            fv = best_bid + HALF_SPREAD

        # Nur Asks vorhanden (keine Bids):
        # FV = best_ask - halber historischer Spread
        elif has_asks and not has_bids:
            best_ask = min(od.sell_orders.keys())
            fv = best_ask - HALF_SPREAD

        # Beide Seiten fehlen: auf prev_mid zurückfallen
        else:
            if mem["prev_mid"] is None:
                result[PRODUCT] = orders
                return result, 0, json.dumps(mem)
            fv = mem["prev_mid"]

        # --- Outage-Logik: mindestens eine Seite fehlt ---
        if not has_bids or not has_asks:
            buy_cap  = POS_LIMIT - position
            sell_cap = POS_LIMIT + position

            print(f"[EVENT] ts={ts} bids={has_bids} asks={has_asks} fv={fv:.1f} pos={position}")

            # Keine Asks → aggressive Käufer haben keine Gegenseite
            # → wir posten SELL über FV um sie abzufangen
            if not has_asks and sell_cap > 0:
                px = int(fv + EDGE)
                orders.append(Order(PRODUCT, px, -sell_cap))
                print(f"  SELL {sell_cap}x @ {px}  (no asks: catch buyers)")

            # Keine Bids → aggressive Verkäufer haben keine Gegenseite
            # → wir posten BUY unter FV um sie abzufangen
            if not has_bids and buy_cap > 0:
                px = int(fv - EDGE)
                orders.append(Order(PRODUCT, px, buy_cap))
                print(f"  BUY  {buy_cap}x @ {px}  (no bids: catch sellers)")

        # --- Re-enter to long 80 by only crossing the first ask layer ---
        if has_asks and position < TARGET_LONG:
            best_ask = min(od.sell_orders.keys())
            ask_volume = abs(od.sell_orders[best_ask])
            refill = min(TARGET_LONG - position, ask_volume)
            if refill > 0:
                orders.append(Order(PRODUCT, best_ask, refill))
                print(f"  REFILL {refill}x @ {best_ask}  (restore long {TARGET_LONG})")

        result[PRODUCT] = orders
        return result, 0, json.dumps(mem)
