from typing import Dict, List
from datamodel import OrderDepth, TradingState, Order

class Trader:
    def __init__(self):
        # I prodotti "Lotteria" (Deep OTM)
        self.LOTTERY_ZONE = ["VEV_6000", "VEV_6500"]
        self.LIMIT_OPT = 300 

    def run(self, state: TradingState) -> tuple[Dict[str, List[Order]], int, str]:
        result = {}
        conversions = 0
        trader_data = state.traderData if state.traderData else ""

        for product in self.LOTTERY_ZONE:
            if product not in state.order_depths:
                continue
                
            pos = state.position.get(product, 0)
            buy_qty = self.LIMIT_OPT - pos
            
            orders = []
            
            if buy_qty > 0:
                # SCENARIO INGRESSO: Aggiriamo la coda FIFO quotando a 1.
                # Questo garantisce che i taker dumpino le opzioni a noi, non ai bot.
                orders.append(Order(product, 1, buy_qty))
                
            elif pos > 0:
                # SCENARIO MIRACOLO: Se abbiamo raggiunto il limite di 300 e il prezzo 
                # del sottostante esplode all'improvviso, piazziamo ordini di vendita 
                # passivi a 10 XIRECS per prendere profitto.
                orders.append(Order(product, 10, -pos))
                
            if orders:
                result[product] = orders

        return result, conversions, trader_data