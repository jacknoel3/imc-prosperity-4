import json
import math
from datamodel import Order, OrderDepth, TradingState
from typing import Dict, List

class Trader:
    def run(self, state: TradingState):
        result = {}
        conversions = 0
        
        product = "ASH_COATED_OSMIUM"
        POSITION_LIMIT = 80
        
        # --- PARAMETRI STRUTTURALI ---
        KAPPA = 0.5          
        EMA_ALPHA = 0.2      
        GAMMA = 0.05         
        SIGMA_SQ = 3.72      
        
        # --- SOGLIE DI ESECUZIONE (EDGES) ---
        MAKER_EDGE = 3.0     # Margine in condizioni normali (Queue Jumping)
        TAKER_EDGE = 1.5     # Margine di attacco aggressivo
        VACUUM_EDGE = 100.0  # Margine di estorsione in caso di Monopolio (Book Vuoto)
        AVG_HALF_SPREAD = 8.0 # Metà dello spread storico (per il calcolo sintetico)

        orders: List[Order] = []
        
        # 1. DECODIFICA MEMORIA
        try:
            memory = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            memory = {}
        
        if product in state.order_depths:
            order_depth = state.order_depths[product]
            
            # 2. CONTROLLO LIQUIDITA' E "VACUUM DETECTION"
            has_bids = len(order_depth.buy_orders) > 0
            has_asks = len(order_depth.sell_orders) > 0
            
            # Se il mercato è TOTALMENTE morto, saltiamo (rarissimo)
            if not has_bids and not has_asks:
                return result, conversions, json.dumps(memory)
                
            best_bid = max(order_depth.buy_orders.keys()) if has_bids else None
            best_ask = min(order_depth.sell_orders.keys()) if has_asks else None
            
            # 3. CALCOLO MID PRICE (Reale o Sintetico)
            if has_bids and has_asks:
                mid_price = (best_ask + best_bid) / 2.0
            elif has_bids:
                # Manca la Lettera (Ask Vuoto) -> Monopolio di Vendita
                mid_price = best_bid + AVG_HALF_SPREAD
            else:
                # Manca il Denaro (Bid Vuoto) -> Monopolio di Acquisto
                mid_price = best_ask - AVG_HALF_SPREAD
            
            # 4. AGGIORNAMENTO MEDIA STORICA (OU)
            prev_ema = memory.get(f"{product}_ema", mid_price)
            current_ema = EMA_ALPHA * mid_price + (1 - EMA_ALPHA) * prev_ema
            memory[f"{product}_ema"] = current_ema
            
            # 5. PREZZAMENTO (OU Fair Value + Skewing)
            position = state.position.get(product, 0)
            ou_fair_value = mid_price + KAPPA * (current_ema - mid_price)
            reservation_price = ou_fair_value - (position * GAMMA * SIGMA_SQ)
            
            buy_volume = POSITION_LIMIT - position
            sell_volume = -POSITION_LIMIT - position 
            
            # --- LATO ACQUISTO (BUY) ---
            if buy_volume > 0:
                if not has_bids:
                    # VACUUM MODE: Estorsione in acquisto (Prezzo infimo)
                    extortion_bid = math.floor(mid_price - VACUUM_EDGE)
                    orders.append(Order(product, extortion_bid, buy_volume))
                else:
                    # NORMAL MODE (Ibrido)
                    if has_asks and best_ask <= (reservation_price - TAKER_EDGE):
                        orders.append(Order(product, best_ask, buy_volume))
                    else:
                        ideal_bid = math.floor(reservation_price - MAKER_EDGE)
                        my_bid = min(best_bid + 1, ideal_bid)
                        if not has_asks or my_bid < best_ask:
                            orders.append(Order(product, my_bid, buy_volume))

            # --- LATO VENDITA (SELL) ---
            if sell_volume < 0:
                if not has_asks:
                    # VACUUM MODE: Estorsione in vendita (Prezzo altissimo)
                    extortion_ask = math.ceil(mid_price + VACUUM_EDGE)
                    orders.append(Order(product, extortion_ask, sell_volume))
                else:
                    # NORMAL MODE (Ibrido)
                    if has_bids and best_bid >= (reservation_price + TAKER_EDGE):
                        orders.append(Order(product, best_bid, sell_volume))
                    else:
                        ideal_ask = math.ceil(reservation_price + MAKER_EDGE)
                        my_ask = max(best_ask - 1, ideal_ask)
                        if not has_bids or my_ask > best_bid:
                            orders.append(Order(product, my_ask, sell_volume))

            result[product] = orders
        
        return result, conversions, json.dumps(memory)