import math
from typing import Dict, List
from datamodel import OrderDepth, TradingState, Order

class Trader:
    def __init__(self):
        # Gerarchia di profittabilità per l'allocazione del limite direzionale.
        # L'ordine dell'array determina la priorità di allocazione della capacità di hedging.
        self.ITM_OPTIONS = ["VEV_4000", "VEV_4500", "VEV_5000", "VEV_5100"]
        self.EXTRACT = "VELVETFRUIT_EXTRACT"
        
        self.EXTRACT_LIMIT = 200
        self.OPTION_LIMIT = 300

    def run(self, state: TradingState) -> tuple[Dict[str, List[Order]], int, str]:
        result = {}
        conversions = 0
        trader_data = state.traderData if state.traderData else ""

        # =====================================================================
        # 1. LETTURA POSIZIONI E CALCOLO DELTA GLOBALE (ITM ZONE)
        # =====================================================================
        ext_pos = state.position.get(self.EXTRACT, 0)
        
        net_options_delta = 0
        for opt in self.ITM_OPTIONS:
            # Assumiamo Delta approssimato a 1.0 per l'intero blocco ITM.
            # Long 1 Opzione ITM = +1 Delta.
            net_options_delta += state.position.get(opt, 0) 

        # =====================================================================
        # 2. HEDGING ISTANTANEO (Sottostante)
        # =====================================================================
        # Il target è mantenere l'estratto sempre opposto alla somma delle opzioni.
        target_ext_pos = -net_options_delta
        hedge_qty = target_ext_pos - ext_pos
        
        if hedge_qty != 0 and self.EXTRACT in state.order_depths:
            depth_ext = state.order_depths[self.EXTRACT]
            orders_ext = []
            
            # Taker aggressivo (+/- 5 tick) per garantire il fill e azzerare il Delta
            if hedge_qty > 0 and depth_ext.sell_orders:
                best_ask = min(depth_ext.sell_orders.keys())
                orders_ext.append(Order(self.EXTRACT, best_ask + 5, hedge_qty))
                
            elif hedge_qty < 0 and depth_ext.buy_orders:
                best_bid = max(depth_ext.buy_orders.keys())
                orders_ext.append(Order(self.EXTRACT, best_bid - 5, hedge_qty))
            
            if orders_ext:
                result[self.EXTRACT] = orders_ext
                
        # Calcolo della posizione teorica dell'estratto post-hedge per il routing della capacità
        teoric_ext_pos = ext_pos + hedge_qty

        # =====================================================================
        # 3. DYNAMIC CAPACITY ROUTING (Prevenzione Rigetto Exchange)
        # =====================================================================
        # Capacità di acquisto opzioni (richiede la possibilità di shortare l'Estratto)
        global_buy_capacity = self.EXTRACT_LIMIT + teoric_ext_pos 
        # Capacità di vendita opzioni (richiede la possibilità di andare long sull'Estratto)
        global_sell_capacity = self.EXTRACT_LIMIT - teoric_ext_pos

        # =====================================================================
        # 4. QUOTING A CASCATA (Liquidity Vampires)
        # =====================================================================
        for opt in self.ITM_OPTIONS:
            if opt not in state.order_depths:
                continue
            
            depth = state.order_depths[opt]
            if not depth.buy_orders or not depth.sell_orders:
                continue
                
            pos = state.position.get(opt, 0)
            
            # Spazio fisico consentito per questo specifico contratto
            opt_buy_cap = self.OPTION_LIMIT - pos
            opt_sell_cap = self.OPTION_LIMIT + pos
            
            # Il vero limite è il minimo tra la capienza del contratto e la capienza globale dell'hedger
            actual_buy_qty = min(opt_buy_cap, global_buy_capacity)
            actual_sell_qty = min(opt_sell_cap, global_sell_capacity)
            
            best_bid = max(depth.buy_orders.keys())
            best_ask = min(depth.sell_orders.keys())
            mid_price = (best_bid + best_ask) / 2.0
            
            # Assegnazione Strategia di Prezzo
            if opt == "VEV_4000":
                # La Golden Goose tollera l'estrazione massima del premio (+1 / -1)
                my_bid = best_bid + 1
                my_ask = best_ask - 1
            else:
                # La Dead Zone richiede l'Hedged Mid-Pegging per invogliare l'incrocio
                my_bid = math.floor((best_bid + mid_price) / 2.0)
                my_ask = math.ceil((best_ask + mid_price) / 2.0)
                
                # Check di dominanza assoluta: assicurati di essere sempre in pole position FIFO
                if my_bid <= best_bid:
                    my_bid = best_bid + 1
                if my_ask >= best_ask:
                    my_ask = best_ask - 1

            # Compilazione ed invio Ordini (se lo spread non è incrociato)
            orders = []
            if my_bid < my_ask:
                if actual_buy_qty > 0:
                    orders.append(Order(opt, my_bid, actual_buy_qty))
                    # Consuma la capacità globale per i contratti successivi nella gerarchia
                    global_buy_capacity -= actual_buy_qty 
                
                if actual_sell_qty > 0:
                    orders.append(Order(opt, my_ask, -actual_sell_qty))
                    global_sell_capacity -= actual_sell_qty 
                    
            if orders:
                result[opt] = orders

        return result, conversions, trader_data