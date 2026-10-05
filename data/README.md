# Historical market data

This directory contains the daily price and trade CSVs already present in the competition workspace. Generated feature tables, combined copies, and tool-bundled duplicate datasets are excluded.

| Directory | Historical days | Products |
| --- | --- | --- |
| `round1/` | −2, −1, 0 | Osmium, Pepper; the original price files may also contain manual products |
| `round2/` | −1, 0, 1 | Osmium and Pepper |
| `round3/` | 0, 1, 2 | Hydrogel, Velvetfruit, and vouchers |
| `round4/` | 1, 2, 3 | Phase 2 products, with counterparty information in trades |
| `round5/` | 2, 3, 4 | 50 products across ten families, with counterparty information in trades |

The files retain their original names, semicolon separators, and values. Round 3 and round 4 price history overlaps on some days; both round-specific layouts are retained so replay commands can select a round explicitly.

Pass `--data data` to the included Python replay CLI. Its reader expects `roundN/prices_round_N_day_D.csv` and matching trade files below this directory.

The round 5 fingerprint strategy configures 40 of the 50 products and needs a full public-trade stream; see the [round 5 replay notes](../rounds/round5/README.md#reproduction-status).
