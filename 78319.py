from datamodel import OrderDepth, TradingState, Order
import math

class Trader:
    def __init__(self):
        # Traccia l'ultimo mid_price per calcolare la volatilità (proxy semplificata)
        self.past_mids = {"TOMATOES": [], "EMERALDS": []}

    def run(self, state: TradingState):
        result = {}
        
        for product in state.order_depths:
            order_depth = state.order_depths[product]
            orders = []
            
            if len(order_depth.buy_orders) != 0 and len(order_depth.sell_orders) != 0:
                best_bid = max(order_depth.buy_orders.keys())
                best_ask = min(order_depth.sell_orders.keys())
                mid_price = (best_bid + best_ask) / 2.0
                inventory = state.position.get(product, 0)
                
                # Volatility tracking (Finestra di 10 tick)
                self.past_mids[product].append(mid_price)
                if len(self.past_mids[product]) > 10:
                    self.past_mids[product].pop(0)
                
                volatility = 0
                if len(self.past_mids[product]) > 1:
                    diffs = [self.past_mids[product][i] - self.past_mids[product][i-1] for i in range(1, len(self.past_mids[product]))]
                    mean_diff = sum(diffs) / len(diffs)
                    variance = sum((x - mean_diff) ** 2 for x in diffs) / len(diffs)
                    volatility = math.sqrt(variance)
                
                if product == "TOMATOES":
                    # 1. Wall Midpoint (Timo Diehm insight)
                    wall_bid = max(order_depth.buy_orders.items(), key=lambda x: x[1])[0]
                    wall_ask = min(order_depth.sell_orders.items(), key=lambda x: x[1])[0]
                    wall_mid = (wall_bid + wall_ask) / 2.0
                    
                    # 2. Deep Imbalance Contrarian
                    total_bid_vol = sum(order_depth.buy_orders.values())
                    total_ask_vol = sum(abs(v) for v in order_depth.sell_orders.values())
                    imbalance = (total_bid_vol - total_ask_vol) / (total_bid_vol + total_ask_vol + 1e-5)
                    
                    # Se l'imbalance è positivo (troppi compratori esca), FV scende (-2.0 peso).
                    contrarian_adj = -(imbalance * 2.0)
                    
                    # 3. Inventory Skew (Risk factor 0.15)
                    skew = inventory * 0.15
                    
                    pr = wall_mid + contrarian_adj - skew
                    min_edge = 1 if volatility < 0.5 else 2
                    
                elif product == "EMERALDS":
                    # Emeralds è mean-reverting a 10000 puro
                    pr = 10000.0 - (inventory * 0.15)
                    min_edge = 1
                else:
                    continue
                    
                # Constrained Pennying con Volatility Edge
                p_bid = min(best_bid + 1, math.floor(pr) - min_edge)
                p_ask = max(best_ask - 1, math.ceil(pr) + min_edge)
                
                # Sizing ristretto per evitare toxic sweep nativi
                can_buy = min(15, 80 - inventory)
                can_sell = min(15, 80 + inventory)
                
                if can_buy > 0:
                    orders.append(Order(product, p_bid, can_buy))
                if can_sell > 0:
                    orders.append(Order(product, p_ask, -can_sell))
                    
            result[product] = orders
            
        # Serialize state internally if needed later, non usato in v1
        return result, 0, ""