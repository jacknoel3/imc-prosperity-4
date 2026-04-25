import math
from typing import Dict, List
from datamodel import OrderDepth, TradingState, Order

class Trader:
    def __init__(self):
        self.EXTRACT = "VELVETFRUIT_EXTRACT"
        # Manteniamo SOLO i contratti che hanno dimostrato PnL netto positivo in ogni test.
        # Il 5100 è troppo vicino all'ATM, lo spread si restringe e il rumore aumenta. Lo scartiamo.
        self.ITM_OPTIONS = ["VEV_4000", "VEV_4500", "VEV_5000"]
        
        self.EXTRACT_LIMIT = 200
        self.OPTION_LIMIT = 300
        # Hard-cap prudenziale per ottimizzare il margine per trade.
        self.MAX_QUOTE_SIZE = 20 

    def get_mid(self, depth: OrderDepth) -> float:
        if not depth.buy_orders or not depth.sell_orders:
            return 0.0
        return (max(depth.buy_orders.keys()) + min(depth.sell_orders.keys())) / 2.0

    def run(self, state: TradingState) -> tuple[Dict[str, List[Order]], int, str]:
        result = {}
        conversions = 0
        trader_data = state.traderData if state.traderData else ""
        
        if self.EXTRACT not in state.order_depths:
            return result, conversions, trader_data
            
        ext_depth = state.order_depths[self.EXTRACT]

        # =====================================================================
        # 1. AGGREGAZIONE DELTA (Puro, Assoluto, ITM=1.0)
        # =====================================================================
        net_options_delta = 0.0
        for opt in self.ITM_OPTIONS:
            # Essendo profondamente ITM, l'approssimazione a Delta 1.0 è il modo 
            # più stabile per hedgiare ed evitare il noise di Black-Scholes.
            net_options_delta += state.position.get(opt, 0) * 1.0

        ext_pos = state.position.get(self.EXTRACT, 0)
        delta_gap = -net_options_delta - ext_pos
        teoric_ext_pos = ext_pos

        # =====================================================================
        # 2. HEDGING SUL SOTTOSTANTE (Smart Taker/Maker)
        # =====================================================================
        orders_ext = []
        best_bid_ext = max(ext_depth.buy_orders.keys())
        best_ask_ext = min(ext_depth.sell_orders.keys())
        spread_ext = best_ask_ext - best_bid_ext
        
        # Tolleranza stretta: i margini delle opzioni ITM sono altissimi, non vale 
        # la pena accumulare rischio.
        if abs(delta_gap) >= 5 or (abs(delta_gap) > 0 and spread_ext == 1):
            # TAKER ZONE: Rischio fuori tolleranza o costo di attraversamento nullo.
            hedge_qty = math.floor(delta_gap) if delta_gap > 0 else math.ceil(delta_gap)
            if hedge_qty > 0:
                orders_ext.append(Order(self.EXTRACT, best_ask_ext + 2, hedge_qty))
            elif hedge_qty < 0:
                orders_ext.append(Order(self.EXTRACT, best_bid_ext - 2, hedge_qty))
            teoric_ext_pos += hedge_qty
            
        elif abs(delta_gap) > 0 and spread_ext >= 3:
            # MAKER ZONE: Cattura il rumore per hedgiare a costo zero/positivo.
            hedge_qty = math.floor(delta_gap) if delta_gap > 0 else math.ceil(delta_gap)
            if hedge_qty > 0:
                orders_ext.append(Order(self.EXTRACT, best_bid_ext + 1, min(15, hedge_qty)))
            elif hedge_qty < 0:
                orders_ext.append(Order(self.EXTRACT, best_ask_ext - 1, max(-15, hedge_qty)))

        if orders_ext:
            result[self.EXTRACT] = orders_ext

        global_buy_capacity = self.EXTRACT_LIMIT + teoric_ext_pos 
        global_sell_capacity = self.EXTRACT_LIMIT - teoric_ext_pos

        # =====================================================================
        # 3. LIQUIDITY VAMPIRE (Microstruttura Pura)
        # =====================================================================
        for opt in self.ITM_OPTIONS:
            if opt not in state.order_depths:
                continue
                
            depth = state.order_depths[opt]
            if not depth.buy_orders or not depth.sell_orders:
                continue
                
            pos = state.position.get(opt, 0)
            best_bid = max(depth.buy_orders.keys())
            best_ask = min(depth.sell_orders.keys())
            mid_price = (best_bid + best_ask) / 2.0
            
            actual_buy_qty = min(self.MAX_QUOTE_SIZE, self.OPTION_LIMIT - pos, global_buy_capacity)
            actual_sell_qty = min(self.MAX_QUOTE_SIZE, self.OPTION_LIMIT + pos, global_sell_capacity)
            
            orders = []
            
            if opt == "VEV_4000":
                # Pennying Spietato per forzare la coda
                my_bid = best_bid + 1
                my_ask = best_ask - 1
            else:
                # Hedged Mid-Pegging per le opzioni meno battute
                my_bid = math.floor((best_bid + mid_price) / 2.0)
                my_ask = math.ceil((best_ask + mid_price) / 2.0)
                if my_bid <= best_bid: my_bid = best_bid + 1
                if my_ask >= best_ask: my_ask = best_ask - 1

            if actual_buy_qty > 0 and my_bid < my_ask:
                orders.append(Order(opt, my_bid, int(actual_buy_qty)))
                global_buy_capacity -= actual_buy_qty 
            if actual_sell_qty > 0 and my_bid < my_ask:
                orders.append(Order(opt, my_ask, -int(actual_sell_qty)))
                global_sell_capacity -= actual_sell_qty 
                
            if orders:
                result[opt] = orders

        return result, conversions, trader_data