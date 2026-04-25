"""
IMC Prosperity 4 Log Converter

A Python package to convert .log files from IMC Prosperity 4 submissions
into clean CSV files for analysis.

Usage as module:
    from log_converter import convert_log_to_csv, ProsperityLogConverter
    
    # Simple conversion
    prices_path, trades_path = convert_log_to_csv('331920.log')
    
    # Advanced usage
    converter = ProsperityLogConverter('331920.log')
    converter.load_log()
    prices_csv = converter.extract_prices_data()
    trades_list = converter.extract_trades_data()

Usage as CLI:
    python -m log_converter 331920.log
    python -m log_converter 331920.log --prices-output custom.csv
"""

from .converter import (
    ProsperityLogConverter,
    convert_log_to_csv
)

__version__ = '1.0.0'
__author__ = 'IMC Prosperity 4 Team'
__all__ = [
    'ProsperityLogConverter',
    'convert_log_to_csv',
]