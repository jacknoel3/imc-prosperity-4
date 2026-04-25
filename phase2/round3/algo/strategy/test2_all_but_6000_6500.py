import math
from typing import Dict, List
from datamodel import OrderDepth, TradingState, Order

class Trader:
    def __init__(self):
        # Delta empirici (fissi per ottimizzazione computazionale)
        self.DELTAS = {
            "VEV_4000": 1.0, "VEV_4500": 1.0, "VEV_5000": 1.0, "VEV_5100": 1.0,
            "VEV_5200": 0.6, "VEV_5300": 0.4, "VEV_5400": 0.2, "VEV_5500": 0.1
        }
        
        self.TARGET_OPTIONS = [
            "VEV_4000", "VEV_4500", "VEV_5000", "VEV_5100",
            "VEV_5200", "VEV_5300", "VEV_5400", "VEV_5500"
        ]
        self.EXTRACT = "VELVETFRUIT_EXTRACT"
        self.EXTRACT_LIMIT = 200
        self.OPTION_LIMIT = 300
        
        # Hard-cap prudenziale sulle opzioni per eludere il "Gamma Scalp Trap" avversario
        self.MAX_QUOTE_SIZE = 20 

    def run(self, state: TradingState) -> tuple[Dict[str, List[Order]], int, str]:
        result = {}
        conversions = 0
        trader_data = state.traderData if state.traderData else ""

        # =====================================================================
        # 1. CALCOLO GLOBAL DELTA E TARGET SOTTOSTANTE
        # =====================================================================
        ext_pos = state.position.get(self.EXTRACT, 0)
        net_options_delta = 0.0
        
        for opt, delta in self.DELTAS.items():
            net_options_delta += state.position.get(opt, 0) * delta

        target_ext_pos = -net_options_delta
        delta_gap = target_ext_pos - ext_pos
        teoric_ext_pos = ext_pos

        # =====================================================================
        # 2. PURE TAKER HEDGING SULL'ESTRATTO (Rischio Direzionale Zero)
        # =====================================================================
        # Nessun Pennying. Nessun limite passivo. Solo esecuzione aggressiva
        # per blindare il Delta accumulato dalle opzioni.
        if self.EXTRACT in state.order_depths:
            depth_ext = state.order_depths[self.EXTRACT]
            orders_ext = []
            
            if depth_ext.buy_orders and depth_ext.sell_orders:
                best_bid_ext = max(depth_ext.buy_orders.keys())
                best_ask_ext = min(depth_ext.sell_orders.keys())
                
                hedge_qty = 0
                # Tolleranza Delta strettissima (>= 1.0) per impedire il drawdown
                if delta_gap >= 1.0:
                    hedge_qty = math.floor(delta_gap)
                    # +5 garantisce l'attraversamento del L1 avversario
                    orders_ext.append(Order(self.EXTRACT, best_ask_ext + 5, hedge_qty))
                elif delta_gap <= -1.0:
                    hedge_qty = math.ceil(delta_gap)
                    # -5 garantisce il dump sul L1 avversario
                    orders_ext.append(Order(self.EXTRACT, best_bid_ext - 5, hedge_qty))
                    
                teoric_ext_pos += hedge_qty

            if orders_ext:
                result[self.EXTRACT] = orders_ext

        # =====================================================================
        # 3. OPTIONS GRID (Il Motore del Profitto Definitivo)
        # =====================================================================
        global_buy_capacity = self.EXTRACT_LIMIT + teoric_ext_pos 
        global_sell_capacity = self.EXTRACT_LIMIT - teoric_ext_pos

        for opt in self.TARGET_OPTIONS:
            if opt not in state.order_depths:
                continue
            
            depth = state.order_depths[opt]
            if not depth.buy_orders or not depth.sell_orders:
                continue
                
            pos = state.position.get(opt, 0)
            
            # Allocation dinamica rispetto alla capienza residua dell'Hedger
            actual_buy_qty = min(self.MAX_QUOTE_SIZE, self.OPTION_LIMIT - pos, global_buy_capacity)
            actual_sell_qty = min(self.MAX_QUOTE_SIZE, self.OPTION_LIMIT + pos, global_sell_capacity)
            
            best_bid = max(depth.buy_orders.keys())
            best_ask = min(depth.sell_orders.keys())
            mid_price = (best_bid + best_ask) / 2.0
            
            if opt == "VEV_4000":
                # Dominio FIFO massimizzato
                my_bid = best_bid + 1
                my_ask = best_ask - 1
            else:
                # Pegging bilanciato
                my_bid = math.floor((best_bid + mid_price) / 2.0)
                my_ask = math.ceil((best_ask + mid_price) / 2.0)
                
                if my_bid <= best_bid:
                    my_bid = best_bid + 1
                if my_ask >= best_ask:
                    my_ask = best_ask - 1

            orders = []
            if my_bid < my_ask:
                if actual_buy_qty > 0:
                    orders.append(Order(opt, my_bid, int(actual_buy_qty)))
                    global_buy_capacity -= actual_buy_qty 
                if actual_sell_qty > 0:
                    orders.append(Order(opt, my_ask, -int(actual_sell_qty)))
                    global_sell_capacity -= actual_sell_qty 
                    
            if orders:
                result[opt] = orders

        return result, conversions, trader_data