import json
import math
from datamodel import Order, OrderDepth, TradingState
from typing import Dict, List

class Trader:
    """
    Round 1 final submit - ASH_COATED_OSMIUM + INTARIAN_PEPPER_ROOT.
    
    Strategy rationale:
      - Osmium: OU/AR(1) market making + vacuum extortion (Bot C pays any price).
      - Pepper: long-and-hold for +1000/day structural drift, with vacuum probe 
        as free optionality on the ask side (unfilled = zero cost).
    
    Defensive additions:
      - Pepper kill-switch: if mid drops >50 below open, flatten (never triggered 
        on historical data; hedge against regime inversion).
      - Removed latent bugs from baseline: flip-short on small dips and 
        best_ask+4 exit spike trigger.
    
    Vacuum probe on Pepper (added per team discussion):
      - When Pepper ask side is empty AND we're long, post sell 80 at mid+100.
      - Max detrended spike on Pepper historical is ~11 points; max mid-jump 
        during no_ask events is 13. Posting at +100 is never "too cheap to fair".
      - Unfilled orders cancel at end of tick -> zero cost if no vacuum-acceptor.
      - If it fires: ~8000 XIRECs gross, ~7800 net after drift opportunity cost 
        during rebuy.
      - Critically: when posting the vacuum sell, we SKIP the normal rebuild logic 
        that same tick to avoid accidental double-exposure.
    """
    
    def __init__(self):
        self.OSM = "ASH_COATED_OSMIUM"
        self.PEP = "INTARIAN_PEPPER_ROOT"
        self.OSM_LIMIT = 80
        self.PEP_LIMIT = 80
        
        # Osmium parameters
        self.KAPPA = 0.5
        self.EMA_ALPHA = 0.2
        self.GAMMA = 0.05
        self.SIGMA_SQ = 3.72
        
        self.MAKER_EDGE = 3.0
        self.TAKER_EDGE = 1.5
        self.VACUUM_EDGE = 100.0
        self.AVG_HALF_SPREAD = 8.0
        
        # Pepper risk guard
        self.PEP_KILL_THRESHOLD = -50.0
        self.PEP_AVG_HALF_SPREAD = 6.5   # changed from 8.0 -> 6.5 (historical avg)
        self.PEP_VACUUM_EDGE = 100.0
    
    def _osmium_logic(self, order_depth: OrderDepth, position: int, memory: dict) -> List[Order]:
        """Osmium: OU/AR(1) market making + vacuum extortion."""
        orders = []
        has_bids = len(order_depth.buy_orders) > 0
        has_asks = len(order_depth.sell_orders) > 0
        if not has_bids and not has_asks:
            return orders
        
        best_bid = max(order_depth.buy_orders.keys()) if has_bids else None
        best_ask = min(order_depth.sell_orders.keys()) if has_asks else None
        
        if has_bids and has_asks:
            mid_price = (best_ask + best_bid) / 2.0
        elif has_bids:
            mid_price = best_bid + self.AVG_HALF_SPREAD
        else:
            mid_price = best_ask - self.AVG_HALF_SPREAD
        
        prev_ema = memory.get(f"{self.OSM}_ema", mid_price)
        current_ema = self.EMA_ALPHA * mid_price + (1 - self.EMA_ALPHA) * prev_ema
        memory[f"{self.OSM}_ema"] = current_ema
        
        ou_fair_value = mid_price + self.KAPPA * (current_ema - mid_price)
        reservation_price = ou_fair_value - (position * self.GAMMA * self.SIGMA_SQ)
        
        buy_volume = self.OSM_LIMIT - position
        sell_volume = -self.OSM_LIMIT - position
        
        # ---- BUY SIDE ----
        if buy_volume > 0:
            if not has_bids:
                extortion_bid = math.floor(mid_price - self.VACUUM_EDGE)
                orders.append(Order(self.OSM, extortion_bid, buy_volume))
            else:
                if has_asks and best_ask <= (reservation_price - self.TAKER_EDGE):
                    orders.append(Order(self.OSM, best_ask, buy_volume))
                else:
                    ideal_bid = math.floor(reservation_price - self.MAKER_EDGE)
                    my_bid = min(best_bid + 1, ideal_bid)
                    if not has_asks or my_bid < best_ask:
                        orders.append(Order(self.OSM, my_bid, buy_volume))
        
        # ---- SELL SIDE ----
        if sell_volume < 0:
            if not has_asks:
                extortion_ask = math.ceil(mid_price + self.VACUUM_EDGE)
                orders.append(Order(self.OSM, extortion_ask, sell_volume))
            else:
                if has_bids and best_bid >= (reservation_price + self.TAKER_EDGE):
                    orders.append(Order(self.OSM, best_bid, sell_volume))
                else:
                    ideal_ask = math.ceil(reservation_price + self.MAKER_EDGE)
                    my_ask = max(best_ask - 1, ideal_ask)
                    if not has_bids or my_ask > best_bid:
                        orders.append(Order(self.OSM, my_ask, sell_volume))
        
        return orders
    
    def _pepper_logic(self, order_depth: OrderDepth, position: int, memory: dict) -> List[Order]:
        """Pepper: long-and-hold to capture +1000/day drift.
        
        Includes vacuum probe on ask side when we're long: free optionality.
        Kill switch flattens if drift structurally inverts.
        """
        orders = []
        has_bids = len(order_depth.buy_orders) > 0
        has_asks = len(order_depth.sell_orders) > 0
        if not has_bids and not has_asks:
            return orders
        
        best_bid = max(order_depth.buy_orders.keys()) if has_bids else None
        best_ask = min(order_depth.sell_orders.keys()) if has_asks else None
        
        if has_bids and has_asks:
            mid_price = (best_ask + best_bid) / 2.0
        elif has_bids:
            mid_price = best_bid + self.PEP_AVG_HALF_SPREAD
        else:
            mid_price = best_ask - self.PEP_AVG_HALF_SPREAD
        
        if "pep_open" not in memory:
            memory["pep_open"] = mid_price
        
        # ---- KILL SWITCH ----
        drawdown = mid_price - memory["pep_open"]
        if drawdown < self.PEP_KILL_THRESHOLD:
            if position > 0 and has_bids:
                flatten_qty = min(position, order_depth.buy_orders[best_bid])
                orders.append(Order(self.PEP, best_bid, -flatten_qty))
            elif position < 0 and has_asks:
                ask_qty = -order_depth.sell_orders[best_ask]
                flatten_qty = min(-position, ask_qty)
                orders.append(Order(self.PEP, best_ask, flatten_qty))
            return orders
        
        # ---- VACUUM PROBE on ask side when we're long ----
        # If the book has no ask and we have a long position to sell, post extreme ask.
        # Unfilled orders cost nothing; if a vacuum-acceptor exists, we get 100 per unit.
        # We SKIP the normal rebuild logic this tick to prevent accidental over-exposure
        # if both the probe AND a normal buy were to fill simultaneously.
        if not has_asks and position > 0:
            extortion_ask = math.ceil(mid_price + self.PEP_VACUUM_EDGE)
            orders.append(Order(self.PEP, extortion_ask, -position))
            return orders
        
        # ---- NORMAL OPERATION: build and hold long to limit ----
        target_position = self.PEP_LIMIT
        position_gap = target_position - position
        
        if position_gap > 0 and has_asks:
            ask_qty = -order_depth.sell_orders[best_ask]
            take_qty = min(position_gap, ask_qty)
            if take_qty > 0:
                orders.append(Order(self.PEP, best_ask, take_qty))
        elif position_gap < 0 and has_bids:
            bid_qty = order_depth.buy_orders[best_bid]
            take_qty = min(-position_gap, bid_qty)
            if take_qty > 0:
                orders.append(Order(self.PEP, best_bid, -take_qty))
        
        return orders
    
    def run(self, state: TradingState):
        result = {}
        conversions = 0
        
        try:
            memory = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            memory = {}
        
        for product in state.order_depths:
            order_depth = state.order_depths[product]
            position = state.position.get(product, 0)
            
            if product == self.OSM:
                orders = self._osmium_logic(order_depth, position, memory)
            elif product == self.PEP:
                orders = self._pepper_logic(order_depth, position, memory)
            else:
                orders = []
            
            if orders:
                result[product] = orders
        
        return result, conversions, json.dumps(memory)