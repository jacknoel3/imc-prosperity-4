import math
from typing import Dict, List
from datamodel import OrderDepth, TradingState, Order

class Trader:
    def __init__(self):
        self.EXTRACT = "VELVETFRUIT_EXTRACT"
        
        # ---------------------------------------------------------------------
        # STRATEGY OVERVIEW: MICROSTRUCTURE ARBITRAGE
        # ---------------------------------------------------------------------
        # We exclusively trade Deep ITM (In-The-Money) options. Data proves these 
        # specific strikes have wide spreads and inefficient opponent bots. 
        # We discard complex theoretical models (like Black-Scholes) and focus 
        # entirely on passive spread capture and strict delta hedging.
        self.ITM_OPTIONS = ["VEV_4000", "VEV_4500", "VEV_5000"]
        
        # Exchange imposed position limits
        self.EXTRACT_LIMIT = 200
        self.OPTION_LIMIT = 300
        
        # Hard-cap for quoting. Instead of quoting 200 units and exposing 
        # ourselves to massive adverse selection, we quote in small chunks (20).
        # This captures 100% of the opponent bot flow (which trades in small sizes)
        # while preserving our global hedging capacity.
        self.MAX_QUOTE_SIZE = 20 

    def get_mid(self, depth: OrderDepth) -> float:
        """Helper to calculate the mid-price of an order book."""
        if not depth.buy_orders or not depth.sell_orders:
            return 0.0
        return (max(depth.buy_orders.keys()) + min(depth.sell_orders.keys())) / 2.0

    def run(self, state: TradingState) -> tuple[Dict[str, List[Order]], int, str]:
        result = {}
        conversions = 0
        trader_data = state.traderData if state.traderData else ""
        
        # Safety check: if the underlying isn't trading yet, do nothing.
        if self.EXTRACT not in state.order_depths:
            return result, conversions, trader_data
            
        ext_depth = state.order_depths[self.EXTRACT]

        # =====================================================================
        # 1. DELTA AGGREGATION (Pure, Absolute, ITM = 1.0)
        # =====================================================================
        net_options_delta = 0.0
        for opt in self.ITM_OPTIONS:
            # Since these options are deeply In-The-Money, they behave almost 
            # exactly like the underlying asset. We assign a hard 1.0 Delta.
            # This avoids the mathematical noise and lag of dynamic Greeks calculation.
            net_options_delta += state.position.get(opt, 0) * 1.0

        ext_pos = state.position.get(self.EXTRACT, 0)
        
        # delta_gap represents how many underlying units we need to buy/sell 
        # to reach a completely neutral directional portfolio (Delta = 0).
        delta_gap = -net_options_delta - ext_pos
        teoric_ext_pos = ext_pos

        # =====================================================================
        # 2. SMART UNDERLYING HEDGING (Maker/Taker Tolerance Band)
        # =====================================================================
        orders_ext = []
        if ext_depth.buy_orders and ext_depth.sell_orders:
            best_bid_ext = max(ext_depth.buy_orders.keys())
            best_ask_ext = min(ext_depth.sell_orders.keys())
            spread_ext = best_ask_ext - best_bid_ext
            
            # TAKER ZONE (Aggressive Hedging)
            # Threshold raised to 15. If bots hit all our options simultaneously 
            # (e.g., +9 delta instantly), we DO NOT panic and pay taker fees.
            # We cross the spread only if risk explodes (>= 15) or if the 
            # spread is 1 tick (meaning the taker fee is mathematically negligible).
            if abs(delta_gap) >= 15 or (abs(delta_gap) > 0 and spread_ext == 1):
                hedge_qty = math.floor(delta_gap) if delta_gap > 0 else math.ceil(delta_gap)
                if hedge_qty > 0:
                    orders_ext.append(Order(self.EXTRACT, best_ask_ext + 2, hedge_qty))
                elif hedge_qty < 0:
                    orders_ext.append(Order(self.EXTRACT, best_bid_ext - 2, hedge_qty))
                teoric_ext_pos += hedge_qty
                
            # MAKER ZONE (Passive Hedging & Spread Capture)
            # If our delta imbalance is small (< 15) but the underlying spread is wide (>= 3),
            # we sit inside the book. We let the market noise come to us, turning 
            # hedging costs into additional profit.
            elif abs(delta_gap) > 0 and spread_ext >= 3:
                hedge_qty = math.floor(delta_gap) if delta_gap > 0 else math.ceil(delta_gap)
                if hedge_qty > 0:
                    orders_ext.append(Order(self.EXTRACT, best_bid_ext + 1, min(15, hedge_qty)))
                elif hedge_qty < 0:
                    orders_ext.append(Order(self.EXTRACT, best_ask_ext - 1, max(-15, hedge_qty)))

            if orders_ext:
                result[self.EXTRACT] = orders_ext

        # DYNAMIC ROUTING: Calculate remaining capacity on the underlying.
        # We must never quote options if we lack the underlying limit to hedge them.
        global_buy_capacity = self.EXTRACT_LIMIT + teoric_ext_pos 
        global_sell_capacity = self.EXTRACT_LIMIT - teoric_ext_pos

        # =====================================================================
        # 3. OPTIONS MARKET MAKING (Liquidity Vampires)
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
            
            # The actual quantity we can quote is the minimum between:
            # 1. Our hard-cap (20)
            # 2. The option's physical limit
            # 3. The underlying's available capacity for hedging
            actual_buy_qty = min(self.MAX_QUOTE_SIZE, self.OPTION_LIMIT - pos, global_buy_capacity)
            actual_sell_qty = min(self.MAX_QUOTE_SIZE, self.OPTION_LIMIT + pos, global_sell_capacity)
            
            orders = []
            
            # PRICING LOGIC
            if opt == "VEV_4000":
                # "Pennying" (Queue Jumping). VEV_4000 has the highest margin. 
                # We simply place our orders 1 tick inside the opponent's spread 
                # to guarantee absolute FIFO execution priority.
                my_bid = best_bid + 1
                my_ask = best_ask - 1
            else:
                # "Hedged Mid-Pegging". For 4500 and 5000, we quote exactly halfway 
                # between the theoretical Mid-Price and the opponent's L1.
                # This ensures a safe, uncrossable internal spread while forcing 
                # takers to pay a premium.
                my_bid = math.floor((best_bid + mid_price) / 2.0)
                my_ask = math.ceil((best_ask + mid_price) / 2.0)
                
                # Failsafe: Ensure we are always beating the bots (FIFO priority check)
                if my_bid <= best_bid: my_bid = best_bid + 1
                if my_ask >= best_ask: my_ask = best_ask - 1

            # ORDER EXECUTION & CAPACITY CONSUMPTION
            if actual_buy_qty > 0 and my_bid < my_ask:
                orders.append(Order(opt, my_bid, int(actual_buy_qty)))
                global_buy_capacity -= actual_buy_qty # Deduct capacity for next options
                
            if actual_sell_qty > 0 and my_bid < my_ask:
                orders.append(Order(opt, my_ask, -int(actual_sell_qty)))
                global_sell_capacity -= actual_sell_qty 
                
            if orders:
                result[opt] = orders

        return result, conversions, trader_data