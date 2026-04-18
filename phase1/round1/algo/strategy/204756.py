from datamodel import Order, TradingState
from typing import Dict, List
import json

PRODUCT   = "INTARIAN_PEPPER_ROOT"
POS_LIMIT = 80
BUY_OFFSET = 0   # post BUY at ask-6


class Trader:

    def bid(self):
        return 15

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}

        try:
            mem = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            mem = {}

        mem.setdefault("ticks_to_full", None)
        mem.setdefault("total_buy_qty", 0)
        mem.setdefault("total_buy_cost", 0.0)

        ts       = state.timestamp
        position = state.position.get(PRODUCT, 0)
        orders: List[Order] = []

        # Track fills
        for trade in state.own_trades.get(PRODUCT, []):
            if trade.quantity > 0:
                mem["total_buy_qty"]  += trade.quantity
                mem["total_buy_cost"] += trade.quantity * trade.price

        if PRODUCT not in state.order_depths:
            result[PRODUCT] = orders
            return result, 0, json.dumps(mem)

        od       = state.order_depths[PRODUCT]
        has_ask  = len(od.sell_orders) > 0

        buy_cap = POS_LIMIT - position

        if buy_cap > 0 and has_ask:
            best_ask = min(od.sell_orders.keys())
            bid_px   = best_ask - BUY_OFFSET

            # Only post if strictly above best_bid (don't cross immediately)
            has_bid  = len(od.buy_orders) > 0
            best_bid = max(od.buy_orders.keys()) if has_bid else 0

            if bid_px > best_bid:
                orders.append(Order(PRODUCT, bid_px, buy_cap))
                if ts % 5000 == 0:
                    print(f"[BUY] ts={ts} | ask={best_ask} posting@{bid_px} | cap={buy_cap} | pos={position}")
            else:
                # bid_px <= best_bid: just lift the ask directly
                orders.append(Order(PRODUCT, best_ask, buy_cap))
                if ts % 5000 == 0:
                    print(f"[LIFT] ts={ts} | ask={best_ask} lifting directly | cap={buy_cap} | pos={position}")

        elif buy_cap == 0 and mem["ticks_to_full"] is None:
            mem["ticks_to_full"] = ts
            avg = mem["total_buy_cost"] / mem["total_buy_qty"] if mem["total_buy_qty"] else 0
            print(f"[FULL] ts={ts} | reached pos=80 | avg_buy={avg:.2f} | total_cost={mem['total_buy_cost']:.0f}")

        result[PRODUCT] = orders
        return result, 0, json.dumps(mem)