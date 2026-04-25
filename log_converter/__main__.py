"""
CLI entry point for IMC Prosperity 4 Log Converter.

Usage:
    python -m log_converter <log_file>
    
Examples:
    python -m log_converter 331920.log
"""

import argparse
import sys
from pathlib import Path
from converter import convert_log_to_csv


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description='Convert IMC Prosperity 4 .log files to CSV format',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        'log_file',
        type=str,
        help='Path to the .log file to convert'
    )
    
    # Keeping arguments for CLI compatibility but ignoring them as the backend doesn't support them
    parser.add_argument(
        '--prices-output',
        type=str,
        default=None,
        help='Custom output path for prices CSV (Ignored)'
    )
    
    parser.add_argument(
        '--trades-output',
        type=str,
        default=None,
        help='Custom output path for trades CSV (Ignored)'
    )
    
    args = parser.parse_args()
    
    # Validate input file exists
    log_path = Path(args.log_file)
    if not log_path.exists():
        print(f"❌ Error: File not found: {args.log_file}", file=sys.stderr)
        sys.exit(1)
    
    try:
        prices_path, trades_path = convert_log_to_csv(args.log_file)
        
        print(f"\n📁 Output files listed in stdout above.")
        
        return 0
        
    except Exception as e:
        print(f"\n❌ Error during conversion: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
