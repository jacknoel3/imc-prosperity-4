"""Core conversion logic for JSON logs to CSV format."""

import json
import pandas as pd
import io
from pathlib import Path
import sys


def parse_to_exact_format(log_path: Path):
    """
    Convert IMC Prosperity JSON log to CSV format.
    
    Args:
        log_path: Path to the input JSON log file
        
    Returns:
        None (saves CSV to disk)
    """
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        sys.exit(f"Errore caricamento JSON: {e}")

    # 1. Caricamento Activities (Prezzi, Posizioni, PnL)
    activities_text = data.get("activitiesLog", "").strip()
    if not activities_text:
        sys.exit("activitiesLog vuoto.")
    
    df_act = pd.read_csv(io.StringIO(activities_text), sep=";")
    
    # 2. Caricamento Trade History
    trades = data.get("tradeHistory", [])
    df_trades = pd.DataFrame(trades)
    if not df_trades.empty:
        df_trades['side'] = df_trades.apply(lambda x: 'BUY' if x['buyer'] == 'SUBMISSION' else 'SELL', axis=1)
        df_trades['symbol'] = df_trades['symbol'].replace({'ASH_COATED_OSMIUM': 'ash_coated_osmium', 'INTARIAN_PEPPER_ROOT': 'intarian_pepper_root'})

    # 3. Trasformazione e Pivot
    products = ['ash_coated_osmium', 'intarian_pepper_root']
    final_parts = []

    for prod in products:
        # Filtro per prodotto (case insensitive)
        p_df = df_act[df_act['product'].str.lower() == prod].copy()
        
        # Mappatura nomi colonne base
        mapping = {
            'bid_price_1': f'{prod}_bid1', 'bid_volume_1': f'{prod}_bid1_vol',
            'bid_price_2': f'{prod}_bid2', 'bid_volume_2': f'{prod}_bid2_vol',
            'ask_price_1': f'{prod}_ask1', 'ask_volume_1': f'{prod}_ask1_vol',
            'ask_price_2': f'{prod}_ask2', 'ask_volume_2': f'{prod}_ask2_vol',
            'mid_price': f'{prod}_mid', 'position': f'{prod}_position', 'profit_and_loss': f'{prod}_pnl'
        }
        p_df = p_df.rename(columns=mapping)
        
        # Calcolo Spread
        p_df[f'{prod}_spread'] = p_df[f'{prod}_ask1'] - p_df[f'{prod}_bid1']

        # Aggregazione Trade per Timestamp
        if not df_trades.empty:
            p_t = df_trades[df_trades['symbol'] == prod].copy()
            if not p_t.empty:
                p_t = p_t.groupby('timestamp').agg({
                    'price': 'first', 'quantity': 'sum', 'side': 'first'
                }).reset_index().rename(columns={
                    'price': f'{prod}_trade_price',
                    'quantity': f'{prod}_trade_qty',
                    'side': f'{prod}_trade_side'
                })
                p_df = pd.merge(p_df, p_t, on='timestamp', how='left')
            else:
                for c in [f'{prod}_trade_price', f'{prod}_trade_qty', f'{prod}_trade_side']:
                    p_df[c] = None
        else:
            for c in [f'{prod}_trade_price', f'{prod}_trade_qty', f'{prod}_trade_side']:
                p_df[c] = None

        # Selezione colonne specifiche nell'ordine dell'esempio
        cols = [
            'timestamp', f'{prod}_bid1', f'{prod}_bid1_vol', f'{prod}_bid2', f'{prod}_bid2_vol',
            f'{prod}_ask1', f'{prod}_ask1_vol', f'{prod}_ask2', f'{prod}_ask2_vol',
            f'{prod}_mid', f'{prod}_spread', f'{prod}_position', f'{prod}_pnl',
            f'{prod}_trade_price', f'{prod}_trade_qty', f'{prod}_trade_side'
        ]
        # Teniamo solo le colonne esistenti (alcune bid2/ask2 potrebbero mancare nel log originale)
        existing_cols = [c for c in cols if c in p_df.columns]
        p_df = p_df[existing_cols]
        final_parts.append(p_df.set_index('timestamp'))

    # Unione finale dei prodotti
    final_df = pd.concat(final_parts, axis=1).reset_index()
    final_df['algo_log'] = ""  # Colonna finale richiesta dall'esempio
    
    # Ordinamento colonne esatto come richiesto
    output_path = f"{log_path.stem}_lossless.csv"
    final_df.to_csv(output_path, index=False)
    print(f"✅ File salvato: {output_path}")
