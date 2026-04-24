## HYDROGEL_PACK
- Type: Delta-1 spot
- Position limit: 200
- Data: `phase2/round3/algo/data/`

### Known
- No EDA yet — populate after running analysis on Round 3 CSVs

### Questions to answer from EDA
- Is FV fixed, trending, or mean-reverting?
- ACF structure (lag-1 mean-reversion vs momentum)?
- OBI signal direction (directional like ASH or contrarian like IPR)?
- Spread width and stability
- Tick volatility
- Bot patterns — are there aggressive takers or only passive quoters?

### Strategy (placeholder)
- Archetype: standard delta-1 MM — start from fixed FV MM baseline, adjust after EDA
- Do NOT hardcode FV without runtime fallback

### Confidence
None yet — update after EDA.
