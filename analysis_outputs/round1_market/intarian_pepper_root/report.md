# INTARIAN_PEPPER_ROOT

## Fair value dynamics
- Approximately stationary: no
- Drifting: yes
- Mean reverting: no
- Jumpy: yes
- Plausibly regime switching: yes
- Average daily drift: 999.4622331691293
- Average rolling volatility: 207.75417712419986

## Order book structure
- Typical spread mode: 13
- One-sided state rate: 0.07706666666666667
- Inner-wall rate: 0.09953333333333333
- Level presence: {'bid_1': 0.9594666666666667, 'bid_2': 0.6471, 'bid_3': 0.014833333333333334, 'ask_1': 0.9616666666666667, 'ask_2': 0.6510333333333334, 'ask_3': 0.015566666666666666}
- Median quote offsets from fair: {'bid_offset_fair_1': -6.0, 'bid_offset_fair_2': -9.0, 'bid_offset_fair_3': -12.5, 'ask_offset_fair_1': 6.0, 'ask_offset_fair_2': 9.0, 'ask_offset_fair_3': 12.94117647058738}

## Trade arrival behaviour
- Trades per active timestamp: 1.0401234567901234
- Active timestamp rate: 0.0324
- Mean gap between active timestamps: 1028.012358393409
- Gap CV: 0.8710612800315088
- Approximately Poisson-like: yes

## Trade size behaviour
- Mean size: 5.1730959446092974
- Median size: 5.0
- Buy median vs sell median: 5.0 vs 5.0
- Dominant discrete sizes: {'6': 219, '3': 198, '7': 195, '5': 195, '4': 161, '8': 42, '2': 1}

## Trade price behaviour
- Mean price minus fair: -0.40152531176453665
- Mean price minus mid: -0.37734915924826906
- Print locations: {'touch': 0.9930761622156281, 'through_or_outside': 0.005934718100890208, 'inside': 0.0009891196834817012}
- Visible sweep rate: 0.0

## Maker fills
- Passive fill rate: None
- Best quote fill rate: None
- Off-best fill rate: None
- Mean fill delay: None
- Queue haircut proxy: None
- Markout 1-step / 5-step: None / None

## Taker fills
- Mean visible depth swept: 20.333333333333332
- Mean taker slippage vs touch: 1.5

## Reduced-form bot interpretation
- Fair process: drifting with regimes
- Suggested latent archetypes: ['tight top-of-book market maker', 'directional repricer / trend follower']
- Quote placement process: quotes concentrate around stable offsets from fair and rounded grid prices
- Trade arrivals: roughly Poisson-like sparse arrivals
- Simplest plausible explanation: INTARIAN_PEPPER_ROOT looks like a market driven by tight top-of-book market maker, directional repricer / trend follower.
