import math
from typing import Dict, List
from datamodel import OrderDepth, TradingState, Order

class Trader:
    def __init__(self):
        # I prodotti target della Dead Zone
        self.DEAD_ZONE = ["VEV_4500", "VEV_5000", "VEV_5100"]
        # Hard-cap rigoroso per isolare il rischio del test
        self.TEST_LIMIT = 10 

    def run(self, state: TradingState) -> tuple[Dict[str, List[Order]], int, str]:
        result = {}
        conversions = 0
        # Preserviamo il traderData per eventuali espansioni future
        trader_data = state.traderData if state.traderData else ""

        for product in self.DEAD_ZONE:
            if product not in state.order_depths:
                continue
                
            depth = state.order_depths[product]
            pos = state.position.get(product, 0)
            
            # Calcolo della quantità disponibile per saturare l'hard-cap di +/- 10
            buy_qty = self.TEST_LIMIT - pos
            sell_qty = -self.TEST_LIMIT - pos
            
            orders = []
            
            if depth.buy_orders and depth.sell_orders:
                best_bid = max(depth.buy_orders.keys())
                best_ask = min(depth.sell_orders.keys())
                
                # Se lo spread è <= 2, non c'è spazio per il mid-pegging frazionario. Si salta.
                if best_ask - best_bid <= 2:
                    continue
                    
                mid_price = (best_bid + best_ask) / 2.0
                
                # Mid-Pegging Frazionario: Ci posizioniamo a metà strada tra i bot e il Fair Value (Mid)
                my_bid = math.floor((best_bid + mid_price) / 2.0)
                my_ask = math.ceil((best_ask + mid_price) / 2.0)
                
                # Fallback di sicurezza: assicuriamoci di battere i bot di almeno 1 tick
                # anche in caso di spread strani o arrotondamenti.
                if my_bid <= best_bid:
                    my_bid = best_bid + 1
                if my_ask >= best_ask:
                    my_ask = best_ask - 1
                    
                # Esecuzione passiva solo se il nostro spread interno è valido
                if my_bid < my_ask:
                    if buy_qty > 0:
                        orders.append(Order(product, my_bid, buy_qty))
                    if sell_qty < 0:
                        orders.append(Order(product, my_ask, sell_qty))
            
            if orders:
                result[product] = orders

        return result, conversions, trader_data