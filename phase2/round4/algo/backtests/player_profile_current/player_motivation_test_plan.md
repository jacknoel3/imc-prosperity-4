# Player Motivation Test Plan

This document separates what we can observe from what we can infer.

We cannot directly know a bot's true objective function. We can, however,
design strategies that make competing explanations produce different log
patterns. If one explanation predicts fills, markouts, timing, and cross-product
behavior better than the others, we can use it operationally.

## Motivation Hypotheses

### Informed directional trader

Expected behavior:
- Trades predict future mid-price moves in the trade direction.
- Continues buying/selling even when our quote is not the best possible price.
- Shows positive markout across multiple horizons.
- May react quickly after other market trades or after correlated products move.

Tests:
- Offer small passive bait at multiple offsets.
- After the player buys, follow with tiny same-direction trades for 1/5/10/50
  tick windows.
- Compare follow PnL against neutral timestamps with no player trigger.

Players likely in this bucket:
- Mark 14, Mark 01, Mark 67 on VE.

### Weak/noisy liquidity trader

Expected behavior:
- Fills us on both sides without consistent favorable future movement.
- Loses against informed players.
- Has negative or near-zero markout after trading.
- Is profitable to fade or provide liquidity against.

Tests:
- Quote symmetric passive sizes and measure whether fills have positive
  SUBMISSION markout.
- Widen/narrow quotes to estimate how price-sensitive the player is.
- Compare behavior when Mark 14/01 are active versus inactive.

Players likely in this bucket:
- Mark 38, Mark 55, Mark 49.

### Structural seller/source

Expected behavior:
- Mostly appears on one side.
- May sell for inventory/liquidity reasons rather than directional belief.
- Best edge occurs when informed buyers are simultaneously taking the other
  side, not necessarily when SUBMISSION directly trades against the source.

Tests:
- Buy passively only when the source is selling and Mark 01/14 are also buying
  the same product family.
- Run matched controls: same quote logic without the Mark 01/14 context.
- Compare direct fills against contextual fills.

Players likely in this bucket:
- Mark 22 on vouchers.

### Inventory rebalancer or hedger

Expected behavior:
- Trades in one product shortly after related product moves or after another
  player trades a correlated product.
- Direction may look informed in one product but is better explained by
  cross-product exposure.
- Fill timing clusters around large market moves or around option-equivalent
  delta changes.

Tests:
- Track lead-lag between VE and voucher trades by player.
- After a player trades a voucher, quote VE and neighboring voucher strikes to
  see whether they hedge mechanically.
- Compare markout in outright price versus delta-adjusted basket markout.

Possible players:
- Mark 01/22 voucher network, Mark 14 on HGP/VEV_4000.

## Concrete Probe Designs

### Mark 14 motive probe

Question:
- Is Mark 14 directional, cross-product hedging, or both?

Design:
- Do not trade aggressively.
- Quote tiny passive bait on HGP, VEV_4000, and VE.
- When Mark 14 trades one product, place tiny same-direction follow orders in
  that product and in the related products.
- Compare own-fill markout by trigger source:
  Mark14-HGP trigger, Mark14-VEV4000 trigger, Mark14-VE trigger.

Decision:
- If same-product follow dominates, Mark 14 is mainly directional.
- If cross-product follow dominates, Mark 14 is using a basket/hedge relation.

### Mark 01 / Mark 22 voucher motive probe

Question:
- Is Mark 22 cheap convexity by itself, or only useful when Mark 01/14 are
  actively buying from Mark 22?

Design:
- Passive bids on VEV_5200-6500.
- Split modes:
  `MARK22_ALONE`, `MARK22_AFTER_MARK01_BUY`, `MARK22_AFTER_MARK14_BUY`.
- Keep identical quote offsets and sizes across modes.

Decision:
- If Mark22-alone fills are not profitable but contextual fills are profitable,
  Mark 22 is a source signal, not a standalone edge.

### Mark 67 VE motive probe

Question:
- Is Mark 67 an informed VE buyer or just a sparse buyer in bullish periods?

Design:
- Passive ask bait to let Mark 67 buy directly from SUBMISSION.
- Immediately after a Mark 67 buy, stop selling VE and run tiny follow buys.
- Matched control: same follow-buy logic at random timestamps with no Mark 67.

Decision:
- If Mark67-triggered follow beats random follow, Mark 67 is a usable informed
  trigger.

### Mark 38 weakness probe

Question:
- Is Mark 38 weak because it is uninformed, because it provides liquidity, or
  because it is mechanically fading Mark 14 badly?

Design:
- Quote against Mark 38 in HGP and VEV_4000.
- Split modes by recent Mark14 activity:
  `FADE_MARK38_AFTER_MARK14`, `FADE_MARK38_NO_MARK14`.

Decision:
- If fade works mainly after Mark14 activity, Mark38's weakness is relational.
- If fade works all the time, Mark38 is generally weak/noisy.

## How Motivation Tests Should Feed Strategy Design

Product strategy and player strategy should be separate layers:

- Product layer: estimates fair value, mean reversion, no-arb, delta, carry,
  inventory targets, and product-specific regime.
- Player layer: modifies execution around that product view.

Operationally:
- Strong product edge + weak/noisy counterparty: size up or quote tighter.
- Strong product edge + informed/toxic counterparty: trade only if edge is much
  larger, or follow instead of providing liquidity.
- Weak product edge + toxic counterparty: do not trade.
- Weak product edge + weak counterparty: only passive small size.

The player map should not replace product alpha. It should decide whether the
product alpha is safe to express at the current quote, side, size, and time.
