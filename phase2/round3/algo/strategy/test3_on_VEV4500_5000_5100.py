import math
from typing import Dict, List
from datamodel import OrderDepth, TradingState, Order

class Trader:
    def __init__(self):
        self.ITM_OPTIONS = ["VEV_4000", "VEV_4500", "VEV_5000", "VEV_5100"]
        self.EXTRACT = "VELVETFRUIT_EXTRACT"
        self.EXTRACT_LIMIT = 200
        # Hard-cap basato sull'EDA (I taker eseguono max 3 qty). 20 ci garantisce il 100% del flow
        # preservando il capacity limit del sottostante per tutti e 4 gli strike.
        self.MAX_QUOTE_SIZE = 20 

    def run(self, state: TradingState) -> tuple[Dict[str, List[Order]], int, str]:
        result = {}
        conversions = 0
        trader_data = state.traderData if state.traderData else ""

        # 1. CALCOLO GLOBAL DELTA E POSIZIONI
        ext_pos = state.position.get(self.EXTRACT, 0)
        net_options_delta = 0
        for opt in self.ITM_OPTIONS:
            net_options_delta += state.position.get(opt, 0) 

        # 2. EXTRACT MAKER SKEWING (Ribaltamento del costo di hedging)
        # Invece di pagare lo spread a mercato, ci posizioniamo passivi sull'estratto 
        # in modo asimmetrico per invitare i taker a riportare il nostro Delta a 0.
        if self.EXTRACT in state.order_depths:
            depth_ext = state.order_depths[self.EXTRACT]
            orders_ext = []
            
            if depth_ext.buy_orders and depth_ext.sell_orders:
                best_bid = max(depth_ext.buy_orders.keys())
                best_ask = min(depth_ext.sell_orders.keys())
                
                target_ext_pos = -net_options_delta
                delta_gap = target_ext_pos - ext_pos
                
                # Calcolo della size da piazzare come Maker
                buy_cap = self.EXTRACT_LIMIT - ext_pos
                sell_cap = self.EXTRACT_LIMIT + ext_pos
                
                # SKEWING LOGIC:
                # Se abbiamo troppo Delta Positivo (dobbiamo vendere Estratto), 
                # quotiamo massicciamente all'Ask e togliamo liquidità dal Bid.
                if delta_gap < -5:  
                    if sell_cap > 0:
                        orders_ext.append(Order(self.EXTRACT, best_ask, -sell_cap))
                    # Taker d'emergenza se il rischio è esplosivo (> 40)
                    if delta_gap < -40: 
                        orders_ext.append(Order(self.EXTRACT, best_bid, delta_gap))
                        
                # Se abbiamo troppo Delta Negativo (dobbiamo comprare Estratto)
                elif delta_gap > 5:
                    if buy_cap > 0:
                        orders_ext.append(Order(self.EXTRACT, best_bid, buy_cap))
                    # Taker d'emergenza
                    if delta_gap > 40:
                        orders_ext.append(Order(self.EXTRACT, best_ask, delta_gap))
                        
                # Se siamo bilanciati (Delta vicino a 0), facciamo puro Market Making
                else:
                    if buy_cap > 0:
                        orders_ext.append(Order(self.EXTRACT, best_bid, min(buy_cap, 50)))
                    if sell_cap > 0:
                        orders_ext.append(Order(self.EXTRACT, best_ask, -min(sell_cap, 50)))

                if orders_ext:
                    result[self.EXTRACT] = orders_ext

        # 3. QUOTING BILANCIATO SULLE OPZIONI ITM
        for opt in self.ITM_OPTIONS:
            if opt not in state.order_depths:
                continue
            
            depth = state.order_depths[opt]
            if not depth.buy_orders or not depth.sell_orders:
                continue
                
            pos = state.position.get(opt, 0)
            
            # Qui applichiamo il MAX_QUOTE_SIZE (20) invece del limite totale
            actual_buy_qty = min(self.MAX_QUOTE_SIZE, 300 - pos)
            actual_sell_qty = min(self.MAX_QUOTE_SIZE, 300 + pos)
            
            best_bid = max(depth.buy_orders.keys())
            best_ask = min(depth.sell_orders.keys())
            mid_price = (best_bid + best_ask) / 2.0
            
            if opt == "VEV_4000":
                my_bid = best_bid + 1
                my_ask = best_ask - 1
            else:
                my_bid = math.floor((best_bid + mid_price) / 2.0)
                my_ask = math.ceil((best_ask + mid_price) / 2.0)
                
                if my_bid <= best_bid:
                    my_bid = best_bid + 1
                if my_ask >= best_ask:
                    my_ask = best_ask - 1

            orders = []
            if my_bid < my_ask:
                if actual_buy_qty > 0:
                    orders.append(Order(opt, my_bid, actual_buy_qty))
                if actual_sell_qty > 0:
                    orders.append(Order(opt, my_ask, -actual_sell_qty))
                    
            if orders:
                result[opt] = orders

        return result, conversions, trader_data