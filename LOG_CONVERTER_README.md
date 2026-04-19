# IMC Prosperity Log Converter

Convert your IMC Prosperity 4 JSON logs to CSV format for easy analysis and data exploration.

## Features

- ✅ Converts JSON logs to structured CSV
- ✅ Automatically handles prices, volumes, positions, and PnL
- ✅ Aggregates trade data by timestamp
- ✅ Supports both products: ASH_COATED_OSMIUM and INTARIAN_PEPPER_ROOT
- ✅ Lossless and readable output

## Installation

### Prerequisites
- Python 3.8 or higher
- pip

### Option 1: Clone and install locally

```bash
git clone https://github.com/jacknoel3/imc-prosperity-4.git
cd imc-prosperity-4

# Install dependencies
pip install pandas
```

## Usage

### Method 1: Terminal (Windows, Mac, Linux)

```bash
# Navigate to the repo
cd imc-prosperity-4

# Convert a log file
python -m log_converter your_session.log

# The CSV will be saved as: your_session_lossless.csv
```

### Method 2: VS Code (Mac/Linux)

1. Open the repo in VS Code
2. Open a `.log` file (or make sure it's the active file)
3. Press `Cmd+Shift+B` (Mac) or `Ctrl+Shift+B` (Linux)
4. Select "Convert Log to CSV"
5. ✅ The CSV will be saved in the same folder with suffix `_lossless.csv`

## Input/Output

### Input
```
your_session.log  (JSON file from IMC Prosperity)
```

### Output
```
your_session_lossless.csv  (CSV with all market data columns)
```

### CSV Structure

The CSV file will contain:
- `timestamp`: Event timestamp
- `ash_coated_osmium_*`: ASH market data (bid/ask/volume/mid/spread/pnl)
- `intarian_pepper_root_*`: IPR market data (bid/ask/volume/mid/spread/pnl)
- `*_trade_price`, `*_trade_qty`, `*_trade_side`: Executed trade details
- `algo_log`: Additional log column (currently empty)

## Complete Example

```bash
# 1. Clone the repo
git clone https://github.com/jacknoel3/imc-prosperity-4.git
cd imc-prosperity-4

# 2. Install dependencies
pip install pandas

# 3. Download your log file from IMC Prosperity
# (save as: session_round2_day1.log)

# 4. Convert to CSV
python -m log_converter session_round2_day1.log

# 5. Open the CSV in Excel/Pandas/etc
# session_round2_day1_lossless.csv ✅
```

## Troubleshooting

### "File not found"
```bash
# Ensure the file exists and is in the correct path
ls -la your_session.log
```

### "activitiesLog is empty"
The JSON file does not contain the `activitiesLog` field. Verify it's a valid IMC Prosperity log.

### "ModuleNotFoundError: pandas"
```bash
# Install pandas
pip install pandas
```

### Task doesn't appear in VS Code
1. Ensure you're in the repo folder
2. Reload VS Code (`Cmd+R`)
3. Open a `.log` file
4. Try `Cmd+Shift+B`

## Project Structure

```
log_converter/
├── __init__.py         # Package initialization
├── __main__.py         # CLI entry point
└── converter.py        # Core conversion logic
```

## Development

### To extend the converter

Edit `log_converter/converter.py`:
- Add new products to the `products` array
- Customize column mapping
- Extend trade aggregation logic

### To test locally

```bash
# Copy a test .log file to the folder
cp ~/Downloads/session_123.log .

# Run the conversion
python -m log_converter session_123.log

# Verify the output
head session_123_lossless.csv
```

## Contributing

To report bugs or suggest features:
1. Open an Issue on GitHub
2. Or submit a Pull Request

## License

MIT License - see the [LICENSE](../LOG_CONVERTER_LICENSE) file for details.

## Support

For questions or issues:
- 🐛 Issues: https://github.com/jacknoel3/imc-prosperity-4/issues
