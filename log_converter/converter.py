"""Core conversion logic for JSON logs to CSV format (Round 3 Updated)."""

import json
import pandas as pd
import io
from pathlib import Path
import sys

# Definizione rigida dei target del Round 3
ROUND_3_PRODUCTS = [
    'HYDROGEL_PACK', 'VELVETFRUIT_EXTRACT',
    'VEV_4000', 'VEV_4500', 'VEV_5000', 'VEV_5100', 'VEV_5200',
    'VEV_5300', 'VEV_5400', 'VEV_5500', 'VEV_6000', 'VEV_6500'
]

def parse_to_exact_format(log_path: Path):
    """
    Convert IMC Prosperity Round 3 JSON log to a wide-format CSV.
    
    Args:
        log_path: Path to the input JSON log file
        
    Returns:
        None (saves CSV to disk)
    """
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        sys.exit(f"Error loading JSON: {e}")

    # 1. Load Activities (Prices, Positions, PnL)
    activities_text = data.get("activitiesLog", "").strip()
    if not activities_text:
        sys.exit("activitiesLog is empty.")
    
    df_act = pd.read_csv(io.StringIO(activities_text), sep=";")
    df_act['product'] = df_act['product'].str.lower()

    # Gestione dell'indice su più giorni (evita conflitti di timestamp)
    has_day = 'day' in df_act.columns
    idx_cols = ['day', 'timestamp'] if has_day else ['timestamp']

    # 2. Load Trade History
    trades = data.get("tradeHistory", [])
    df_trades = pd.DataFrame(trades)
    
    # 3. Transform and Pivot
    products = [p.lower() for p in ROUND_3_PRODUCTS]
    final_parts = []

    if not df_trades.empty:
        df_trades['side'] = df_trades.apply(lambda x: 'BUY' if x.get('buyer') == 'SUBMISSION' else 'SELL', axis=1)
        if 'symbol' in df_trades.columns:
            df_trades['symbol'] = df_trades['symbol'].str.lower()

    for prod in products:
        p_df = df_act[df_act['product'] == prod].copy()
        if p_df.empty:
            continue
            
        mapping = {
            'bid_price_1': f'{prod}_bid1', 'bid_volume_1': f'{prod}_bid1_vol',
            'bid_price_2': f'{prod}_bid2', 'bid_volume_2': f'{prod}_bid2_vol',
            'bid_price_3': f'{prod}_bid3', 'bid_volume_3': f'{prod}_bid3_vol',
            'ask_price_1': f'{prod}_ask1', 'ask_volume_1': f'{prod}_ask1_vol',
            'ask_price_2': f'{prod}_ask2', 'ask_volume_2': f'{prod}_ask2_vol',
            'ask_price_3': f'{prod}_ask3', 'ask_volume_3': f'{prod}_ask3_vol',
            'mid_price': f'{prod}_mid', 'profit_and_loss': f'{prod}_pnl'
        }
        if 'position' in p_df.columns:
            mapping['position'] = f'{prod}_position'
            
        p_df = p_df.rename(columns=mapping)
        
        if f'{prod}_ask1' in p_df.columns and f'{prod}_bid1' in p_df.columns:
            p_df[f'{prod}_spread'] = p_df[f'{prod}_ask1'] - p_df[f'{prod}_bid1']

        if not df_trades.empty and 'symbol' in df_trades.columns:
            p_t = df_trades[df_trades['symbol'] == prod].copy()
            if not p_t.empty:
                # Use only columns present in p_t for grouping
                actual_trade_idx = [c for c in idx_cols if c in p_t.columns]
                p_t = p_t.groupby(actual_trade_idx).agg({
                    'price': 'first', 'quantity': 'sum', 'side': 'first'
                }).reset_index().rename(columns={
                    'price': f'{prod}_trade_price',
                    'quantity': f'{prod}_trade_qty',
                    'side': f'{prod}_trade_side'
                })
                p_df = pd.merge(p_df, p_t, on=actual_trade_idx, how='left')
            else:
                for c in [f'{prod}_trade_price', f'{prod}_trade_qty', f'{prod}_trade_side']:
                    p_df[c] = None
        else:
            for c in [f'{prod}_trade_price', f'{prod}_trade_qty', f'{prod}_trade_side']:
                p_df[c] = None

        cols = idx_cols + [
            f'{prod}_bid1', f'{prod}_bid1_vol', f'{prod}_bid2', f'{prod}_bid2_vol', f'{prod}_bid3', f'{prod}_bid3_vol',
            f'{prod}_ask1', f'{prod}_ask1_vol', f'{prod}_ask2', f'{prod}_ask2_vol', f'{prod}_ask3', f'{prod}_ask3_vol',
            f'{prod}_mid', f'{prod}_spread', f'{prod}_position', f'{prod}_pnl',
            f'{prod}_trade_price', f'{prod}_trade_qty', f'{prod}_trade_side'
        ]
        
        existing_cols = [c for c in cols if c in p_df.columns]
        p_df = p_df[existing_cols]
        final_parts.append(p_df.set_index(idx_cols))

    if not final_parts:
        sys.exit("Nessun dato trovato.")

    final_df = pd.concat(final_parts, axis=1).reset_index()
    final_df['algo_log'] = ""
    
    output_path = f"{log_path.stem}_lossless.csv"
    final_df.to_csv(output_path, index=False)
    print(f"✅ File saved: {output_path}")

def convert_log_to_csv(log_path):
    """Wrapper function for backward compatibility."""
    log_path = Path(log_path)
    parse_to_exact_format(log_path)
    output_csv = f"{log_path.stem}_lossless.csv"
    return output_csv, output_csv


class ProsperityLogConverter:
    """Public interface for log conversion."""
    
    def __init__(self, log_path):
        """Initialize with a log file path."""
        self.log_path = Path(log_path)
        self.data = None
        
    def load_log(self):
        """Load and parse the JSON log file."""
        try:
            with open(self.log_path, 'r', encoding='utf-8') as f:
                self.data = json.load(f)
        except Exception as e:
            raise RuntimeError(f"Error loading JSON: {e}")
        return self
        
    def convert_to_csv(self):
        """Convert the log to CSV format."""
        parse_to_exact_format(self.log_path)
        return f"{self.log_path.stem}_lossless.csv"
        
    def extract_prices_data(self):
        """Extract prices data from the log."""
        if not self.data:
            self.load_log()
        activities_text = self.data.get("activitiesLog", "").strip()
        if activities_text:
            return pd.read_csv(io.StringIO(activities_text), sep=";")
        return pd.DataFrame()
        
    def extract_trades_data(self):
        """Extract trades data from the log."""
        if not self.data:
            self.load_log()
        trades = self.data.get("tradeHistory", [])
        return pd.DataFrame(trades)
