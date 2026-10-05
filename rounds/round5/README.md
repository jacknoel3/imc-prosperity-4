# Round 5 · Public-trade fingerprints

[Overview](../../README.md) · [Previous: round 4](../round4/README.md)

The [combined algorithm](algo/noah_final.py) trades **40 configured products across eight families**: Oxygen Shakes, Snackpacks, Galaxy Sounds, Microchips, Pebbles, Robots, Sleep Pods, and UV Visors.

## Algorithmic trading

### Turn public events into position targets

The algorithm uses rule tables that match a public trade's source product, quantity, inferred side, and a state condition. Conditions include recent price extremes and rolling standardised-price measures.

```text
public trade → fingerprint → state-condition check → target position → L1 orders
```

An event in one product can change the target for another product in the same family. A rule can require several fingerprints together. For example, the Garlic long-entry rule requires quantity-4 buys in **both Garlic and Mint**, with no additional state condition; that event packet sets the Garlic target to +10.

Separate entry and exit fingerprints govern long and short exposure. The execution layer then uses the visible best bid/ask to move toward that target, with a configured position limit of 10 per product.

### Carry state across ticks

Historical prices, recent snapshots, seen-event information, and target state are encoded into compressed `traderData`. The implementation uses JSON, zlib, and base64, with bounded histories to control its size.

The exact submission and result for `noah_final.py` still need confirmation. More detail on how the rules were discovered and validated will be added from our research notes.

## Reproduction status

Historical replay of this strategy needs an engine that supplies the full public market-trade stream, because the rules depend on the original trade quantities. The included Python backtester changes the public tape when matching our orders. The source comments refer to a separate `--raw-csv-market-trades full` configuration; that configuration has not been reproduced here.

The [historical market data](../../data/round5/) includes days 2, 3, and 4, covering 50 products across ten families. The strategy configures 40 of those products; Panels and Translators are outside its configured universe. Explore the prices and public events with the [dashboard](../../docs/dashboards.md).

## Manual trading

Our approach, decisions, and result for the manual challenge still need to be added.
