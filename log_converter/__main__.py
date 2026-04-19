"""CLI entry point for log-converter."""

import sys
from pathlib import Path
from log_converter.converter import parse_to_exact_format


def main():
    """Main CLI function."""
    if len(sys.argv) < 2:
        print("Usage: python -m log_converter <file.log>")
        print("\nExample:")
        print("  python -m log_converter session_123.log")
        sys.exit(1)
    
    log_file = Path(sys.argv[1])
    
    if not log_file.exists():
        sys.exit(f"❌ File not found: {log_file}")
    
    if log_file.suffix != '.log':
        print(f"⚠️  Warning: file does not have .log extension ({log_file.suffix})")
    
    try:
        parse_to_exact_format(log_file)
    except Exception as e:
        sys.exit(f"❌ Conversion error: {e}")


if __name__ == "__main__":
    main()
