## VELVETFRUIT_EXTRACT
- Type: Delta-1 spot — also the **underlying** for all VEV vouchers
- Position limit: 200
- Data: `phase2/round3/data/`

### Known
- Vouchers (VEV_4000…VEV_6500) are call options on this product
- Its price process drives option value — must model accurately for BS pricing
- No EDA yet

### Questions to answer from EDA
- Price level and range (sets ATM/OTM/ITM for each voucher strike)
- Daily volatility (σ) — critical input for Black-Scholes
- Trend or mean-reversion? (affects delta-hedging frequency)
- OBI signal direction
- Spread width

### Strategy (placeholder)
- Trade independently as a delta-1 MM
- Also use price + vol estimates as inputs to VEV voucher pricing
- Long exposure here partially offsets short-vega risk from selling vouchers (if applicable)

### Confidence
None yet — update after EDA.
