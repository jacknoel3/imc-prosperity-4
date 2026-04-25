import math
from typing import Dict, List
from datamodel import OrderDepth, TradingState, Order

class Trader:
    def __init__(self):
        self.EXTRACT = "VELVETFRUIT_EXTRACT"
        self.GOLDEN_GOOSE = "VEV_4000"
        self.DEAD_ZONE = ["VEV_4500", "VEV_5000", "VEV_5100"]
        self.VOL_ZONE = ["VEV_5200", "VEV_5300", "VEV_5400", "VEV_5500"]
        
        self.EXTRACT_LIMIT = 200
        self.OPTION_LIMIT = 300
        self.MAX_QUOTE_SIZE = 20
        self.DAYS_IN_YEAR = 365.0
        self.FLAT_IV = 0.233 # Media esatta empirica ATM

    def norm_cdf(self, x: float) -> float:
        return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0

    def bs_metrics(self, S: float, K: float, T: float, sigma: float) -> tuple[float, float]:
        if T <= 0 or sigma <= 0 or S <= 0:
            return max(0.0, S - K), (1.0 if S > K else 0.0)
        d1 = (math.log(S / K) + (0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        fv = S * self.norm_cdf(d1) - K * self.norm_cdf(d2)
        delta = self.norm_cdf(d1)
        return fv, delta

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
        S = self.get_mid(ext_depth)
        if S == 0:
            return result, conversions, trader_data
            
        current_day = state.timestamp // 1000000 
        tte_days_left = 8 - current_day
        T = (tte_days_left - ((state.timestamp % 1000000) / 1000000)) / self.DAYS_IN_YEAR

        net_options_delta = 0.0
        opt_fvs = {}
        
        # 1. PRICING E AGGREGAZIONE DELTA
        all_options = [self.GOLDEN_GOOSE] + self.DEAD_ZONE + self.VOL_ZONE
        for opt in all_options:
            K = float(opt.split('_')[1])
            pos = state.position.get(opt, 0)
            
            # Per le ITM profonde il delta è 1.0 (evitiamo distorsioni matematiche a basso TTE)
            if opt in [self.GOLDEN_GOOSE] + self.DEAD_ZONE:
                fv = max(0.0, S - K)
                delta = 1.0
            else:
                fv, delta = self.bs_metrics(S, K, T, self.FLAT_IV)
                
            opt_fvs[opt] = fv
            net_options_delta += pos * delta

        ext_pos = state.position.get(self.EXTRACT, 0)
        delta_gap = -net_options_delta - ext_pos
        teoric_ext_pos = ext_pos

        # 2. HEDGING ASINTOTICO DI WILMOTT
        orders_ext = []
        best_bid_ext = max(ext_depth.buy_orders.keys())
        best_ask_ext = min(ext_depth.sell_orders.keys())
        spread_ext = best_ask_ext - best_bid_ext
        
        # Tolleranza Delta allargata a 60 (come da matrice Wilmott)
        if abs(delta_gap) >= 60 or (abs(delta_gap) >= 10 and spread_ext == 1):
            hedge_qty = math.floor(delta_gap) if delta_gap > 0 else math.ceil(delta_gap)
            if hedge_qty > 0:
                orders_ext.append(Order(self.EXTRACT, best_ask_ext + 2, hedge_qty))
            elif hedge_qty < 0:
                orders_ext.append(Order(self.EXTRACT, best_bid_ext - 2, hedge_qty))
            teoric_ext_pos += hedge_qty
            
        elif abs(delta_gap) >= 10 and spread_ext >= 3:
            hedge_qty = math.floor(delta_gap) if delta_gap > 0 else math.ceil(delta_gap)
            if hedge_qty > 0:
                orders_ext.append(Order(self.EXTRACT, best_bid_ext + 1, min(15, hedge_qty)))
            elif hedge_qty < 0:
                orders_ext.append(Order(self.EXTRACT, best_ask_ext - 1, max(-15, hedge_qty)))

        if orders_ext:
            result[self.EXTRACT] = orders_ext

        global_buy_capacity = self.EXTRACT_LIMIT + teoric_ext_pos 
        global_sell_capacity = self.EXTRACT_LIMIT - teoric_ext_pos

        # 3. ROUTING DELLA LIQUIDITA E MARKET MAKING
        for opt in all_options:
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
            
            if opt == self.GOLDEN_GOOSE:
                my_bid = best_bid + 1
                my_ask = best_ask - 1
            elif opt in self.DEAD_ZONE:
                my_bid = math.floor((best_bid + mid_price) / 2.0)
                my_ask = math.ceil((best_ask + mid_price) / 2.0)
                if my_bid <= best_bid: my_bid = best_bid + 1
                if my_ask >= best_ask: my_ask = best_ask - 1
            else:
                fv = opt_fvs[opt]
                # Taker Sniping su opzioni VRP disallineate
                if best_ask <= fv - 0.5 and actual_buy_qty > 0:
                    orders.append(Order(opt, best_ask + 5, int(actual_buy_qty)))
                    global_buy_capacity -= actual_buy_qty
                    actual_buy_qty = 0 
                # Asymmetric Volatility Quoting
                my_bid = math.floor(fv - 1.0)
                my_ask = math.ceil(fv + 3.0)

            if actual_buy_qty > 0 and my_bid < my_ask:
                orders.append(Order(opt, my_bid, int(actual_buy_qty)))
                global_buy_capacity -= actual_buy_qty 
            if actual_sell_qty > 0 and my_bid < my_ask:
                orders.append(Order(opt, my_ask, -int(actual_sell_qty)))
                global_sell_capacity -= actual_sell_qty 
                
            if orders:
                result[opt] = orders

        return result, conversions, trader_data