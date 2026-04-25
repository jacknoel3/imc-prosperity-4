import math
from typing import Dict, List
from datamodel import OrderDepth, TradingState, Order

class Trader:
    def __init__(self):
        # Mappatura del Delta calcolato empiricamente dall'EDA per ogni strike.
        self.DELTAS = {
            "VEV_4000": 1.0, "VEV_4500": 1.0, "VEV_5000": 1.0, "VEV_5100": 1.0,
            "VEV_5200": 0.6, "VEV_5300": 0.4, "VEV_5400": 0.2, "VEV_5500": 0.1
        }
        
        # Gerarchia di allocazione della liquidità: dai margini certi (Deep ITM) 
        # ai margini speculativi (ATM/OTM).
        self.TARGET_OPTIONS = [
            "VEV_4000", "VEV_4500", "VEV_5000", "VEV_5100",
            "VEV_5200", "VEV_5300", "VEV_5400", "VEV_5500"
        ]
        self.EXTRACT = "VELVETFRUIT_EXTRACT"
        self.EXTRACT_LIMIT = 200
        self.OPTION_LIMIT = 300
        
        # Hard-cap per evitare la saturazione avida e il rischio di single-tick.
        self.MAX_QUOTE_SIZE = 20 

    def run(self, state: TradingState) -> tuple[Dict[str, List[Order]], int, str]:
        result = {}
        conversions = 0
        trader_data = state.traderData if state.traderData else ""

        # =====================================================================
        # 1. CALCOLO GLOBAL DELTA
        # =====================================================================
        ext_pos = state.position.get(self.EXTRACT, 0)
        net_options_delta = 0.0
        
        for opt, delta in self.DELTAS.items():
            net_options_delta += state.position.get(opt, 0) * delta

        target_ext_pos = -net_options_delta
        delta_gap = target_ext_pos - ext_pos

        # =====================================================================
        # 2. EXTRACT EXECUTION (Taker Hedging & Maker Pennying)
        # =====================================================================
        teoric_ext_pos = ext_pos
        
        if self.EXTRACT in state.order_depths:
            depth_ext = state.order_depths[self.EXTRACT]
            orders_ext = []
            
            if depth_ext.buy_orders and depth_ext.sell_orders:
                best_bid_ext = max(depth_ext.buy_orders.keys())
                best_ask_ext = min(depth_ext.sell_orders.keys())
                
                # A. INSTANTANEOUS TAKER HEDGING (Rischio Direzionale Zero)
                # Se il gap delta supera 1.0, incrocia immediatamente lo spread per coprire.
                hedge_qty = 0
                if delta_gap >= 1.0:
                    hedge_qty = math.floor(delta_gap)
                    orders_ext.append(Order(self.EXTRACT, best_ask_ext + 5, hedge_qty))
                elif delta_gap <= -1.0:
                    hedge_qty = math.ceil(delta_gap)
                    orders_ext.append(Order(self.EXTRACT, best_bid_ext - 5, hedge_qty))
                    
                teoric_ext_pos += hedge_qty
                
                # B. EXTRACT PENNYING (Estrazione spread a Rischio Zero)
                # Se il delta è bilanciato, cattura il rumore di mercato scavalcando il FIFO.
                if abs(delta_gap - hedge_qty) < 1.5:
                    spread_ext = best_ask_ext - best_bid_ext
                    if spread_ext >= 2:
                        my_bid_ext = best_bid_ext + 1
                        my_ask_ext = best_ask_ext - 1
                        
                        maker_buy_cap = self.EXTRACT_LIMIT - teoric_ext_pos
                        maker_sell_cap = self.EXTRACT_LIMIT + teoric_ext_pos
                        
                        if my_bid_ext < my_ask_ext:
                            if maker_buy_cap > 0:
                                orders_ext.append(Order(self.EXTRACT, my_bid_ext, min(10, maker_buy_cap)))
                            if maker_sell_cap > 0:
                                orders_ext.append(Order(self.EXTRACT, my_ask_ext, -min(10, maker_sell_cap)))

            if orders_ext:
                result[self.EXTRACT] = orders_ext

        # =====================================================================
        # 3. DYNAMIC CAPACITY ROUTING & GRID TOTALE
        # =====================================================================
        # Vincolo di protezione per evitare il rigetto degli ordini dall'Exchange.
        global_buy_capacity = self.EXTRACT_LIMIT + teoric_ext_pos 
        global_sell_capacity = self.EXTRACT_LIMIT - teoric_ext_pos

        for opt in self.TARGET_OPTIONS:
            if opt not in state.order_depths:
                continue
            
            depth = state.order_depths[opt]
            if not depth.buy_orders or not depth.sell_orders:
                continue
                
            pos = state.position.get(opt, 0)
            
            actual_buy_qty = min(self.MAX_QUOTE_SIZE, self.OPTION_LIMIT - pos, global_buy_capacity)
            actual_sell_qty = min(self.MAX_QUOTE_SIZE, self.OPTION_LIMIT + pos, global_sell_capacity)
            
            best_bid = max(depth.buy_orders.keys())
            best_ask = min(depth.sell_orders.keys())
            mid_price = (best_bid + best_ask) / 2.0
            
            # Assegnazione Strategia di Prezzo
            if opt == "VEV_4000":
                # Dominio Pennying per la Golden Goose
                my_bid = best_bid + 1
                my_ask = best_ask - 1
            else:
                # Hedged Mid-Pegging per tutta la catena rimanente (Espansione)
                my_bid = math.floor((best_bid + mid_price) / 2.0)
                my_ask = math.ceil((best_ask + mid_price) / 2.0)
                
                # Check di priorità FIFO assoluta
                if my_bid <= best_bid:
                    my_bid = best_bid + 1
                if my_ask >= best_ask:
                    my_ask = best_ask - 1

            orders = []
            if my_bid < my_ask:
                if actual_buy_qty > 0:
                    orders.append(Order(opt, my_bid, actual_buy_qty))
                    global_buy_capacity -= actual_buy_qty 
                if actual_sell_qty > 0:
                    orders.append(Order(opt, my_ask, -actual_sell_qty))
                    global_sell_capacity -= actual_sell_qty 
                    
            if orders:
                result[opt] = orders

        return result, conversions, trader_data