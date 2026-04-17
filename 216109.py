import json
import math
from datamodel import Order, OrderDepth, TradingState
from typing import Dict, List

class Trader:
    def __init__(self):
        # --- PARAMETRI OSMIUM (AR1/MDP) ---
        self.OSM = "ASH_COATED_OSMIUM"
        self.OSM_LIMIT = 80
        self.AR_PHI = -0.5        # Autocorrelazione estratta dai dataset
        self.GAMMA = 0.05         
        self.SIGMA_SQ = 3.72      
        self.MAKER_EDGE = 3.0     
        self.TAKER_EDGE = 1.5     
        self.VACUUM_EDGE = 100.0  
        self.AVG_HALF_SPREAD = 8.0

        # --- PARAMETRI PEPPER ROOT (Directional) ---
        self.PEP = "INTARIAN_PEPPER_ROOT"
        self.PEP_LIMIT = 80

    def run(self, state: TradingState):
        result = {}
        conversions = 0
        try:
            mem = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            mem = {}

        for product in state.order_depths:
            order_depth: OrderDepth = state.order_depths[product]
            position = state.position.get(product, 0)
            orders: List[Order] = []
            
            has_bids = len(order_depth.buy_orders) > 0
            has_asks = len(order_depth.sell_orders) > 0
            if not has_bids and not has_asks:
                continue
                
            best_bid = max(order_depth.buy_orders.keys()) if has_bids else 0
            best_ask = min(order_depth.sell_orders.keys()) if has_asks else 0

            # =================================================================
            # LOGICA: ASH_COATED_OSMIUM (AR(1) Discrete Model)
            # =================================================================
            if product == self.OSM:
                if has_bids and has_asks: mid_price = (best_ask + best_bid) / 2.0
                elif has_bids: mid_price = best_bid + self.AVG_HALF_SPREAD
                else: mid_price = best_ask - self.AVG_HALF_SPREAD
                
                prev_mid = mem.get(f"{product}_prev_mid", mid_price)
                mem[f"{product}_prev_mid"] = mid_price
                
                # Valore atteso basato sul processo Autoregressivo Discreto
                expected_price_delta = self.AR_PHI * (mid_price - prev_mid)
                ar_fair_value = mid_price + expected_price_delta
                
                reservation_price = ar_fair_value - (position * self.GAMMA * self.SIGMA_SQ)
                
                buy_volume = self.OSM_LIMIT - position
                sell_volume = -self.OSM_LIMIT - position 
                
                if buy_volume > 0:
                    if not has_bids:
                        orders.append(Order(product, math.floor(mid_price - self.VACUUM_EDGE), buy_volume))
                    else:
                        if has_asks and best_ask <= (reservation_price - self.TAKER_EDGE):
                            orders.append(Order(product, best_ask, buy_volume))
                        else:
                            ideal_bid = math.floor(reservation_price - self.MAKER_EDGE)
                            my_bid = min(best_bid + 1, ideal_bid)
                            if not has_asks or my_bid < best_ask:
                                orders.append(Order(product, my_bid, buy_volume))

                if sell_volume < 0:
                    if not has_asks:
                        orders.append(Order(product, math.ceil(mid_price + self.VACUUM_EDGE), sell_volume))
                    else:
                        if has_bids and best_bid >= (reservation_price + self.TAKER_EDGE):
                            orders.append(Order(product, best_bid, sell_volume))
                        else:
                            ideal_ask = math.ceil(reservation_price + self.MAKER_EDGE)
                            my_ask = max(best_ask - 1, ideal_ask)
                            if not has_bids or my_ask > best_bid:
                                orders.append(Order(product, my_ask, sell_volume))

            # =================================================================
            # LOGICA: INTARIAN_PEPPER_ROOT
            # =================================================================
            elif product == self.PEP:
                mid_price = (best_ask + best_bid) / 2.0 if (has_bids and has_asks) else (best_bid or best_ask)
                if "pep_open" not in mem: mem["pep_open"] = mid_price
                trend_delta = mid_price - mem["pep_open"]
                
                if trend_delta < -5: target_pos = -self.PEP_LIMIT
                else: target_pos = self.PEP_LIMIT

                trade_cap = target_pos - position
                if trade_cap > 0 and has_asks: orders.append(Order(product, best_ask, trade_cap))
                elif trade_cap < 0 and has_bids: orders.append(Order(product, best_bid, trade_cap))
                    
                if position == self.PEP_LIMIT and has_asks:
                    orders.append(Order(product, best_ask + 4, -self.PEP_LIMIT))
                elif position == -self.PEP_LIMIT and has_bids:
                    orders.append(Order(product, best_bid - 4, self.PEP_LIMIT))

            if orders: result[product] = orders
                
        return result, conversions, json.dumps(mem)