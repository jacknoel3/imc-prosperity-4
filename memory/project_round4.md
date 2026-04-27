---
name: Round 4 transition
description: Round 4 "The More The Merrier" context — counterparty IDs, Aether Crystal manual, TTE=4 for vouchers
type: project
---

Round 4 "The More The Merrier" started April 27, 2026. Same algorithmic products as R3 (`HYDROGEL_PACK`, `VELVETFRUIT_EXTRACT`, 10 voucher strikes) but `Trade.buyer`/`Trade.seller` are now populated — counterparty IDs available for signal-copying. Voucher TTE=4 days (was 5). Manual challenge is Aether Crystal: GBM underlying (σ=251%, zero drift, 4 steps/day, 252 days/year), vanilla + exotic options (Chooser, Binary Put, Knock-Out Put), PnL averaged over 100 sims, contract size=3000, hold-till-expiry.

**Why:** R4 revealed new counterparty transparency mechanic and new manual challenge. Docs updated to reflect these.
**How to apply:** When discussing algo strategy, note TTE=4 for voucher BS pricing. When discussing manual, reference aether_crystal.md for full exotic option definitions and GBM params.
