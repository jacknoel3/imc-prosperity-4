# ASH_COATED_OSMIUM

## Fair value dynamics
- Approximately stationary: no
- Drifting: no
- Mean reverting: no
- Jumpy: yes
- Plausibly regime switching: yes
- Average daily drift: -8.26708949964753
- Average rolling volatility: 168.14896126029325

## Order book structure
- Typical spread mode: 16
- One-sided state rate: 0.07853333333333333
- Inner-wall rate: 0.09486666666666667
- Level presence: {'bid_1': 0.9598666666666666, 'bid_2': 0.6532, 'bid_3': 0.025966666666666666, 'ask_1': 0.9599666666666666, 'ask_2': 0.6517333333333334, 'ask_3': 0.0249}
- Median quote offsets from fair: {'bid_offset_fair_1': -8.0, 'bid_offset_fair_2': -10.0, 'bid_offset_fair_3': -10.631578947368325, 'ask_offset_fair_1': 8.0, 'ask_offset_fair_2': 10.0, 'ask_offset_fair_3': 11.5}

## Trade arrival behaviour
- Trades per active timestamp: 1.0541666666666667
- Active timestamp rate: 0.04
- Mean gap between active timestamps: 832.1100917431193
- Gap CV: 0.9224944987868342
- Approximately Poisson-like: yes

## Trade size behaviour
- Mean size: 5.211857707509881
- Median size: 5.0
- Buy median vs sell median: 5.0 vs 5.0
- Dominant discrete sizes: {'6': 230, '5': 224, '3': 179, '4': 172, '2': 160, '7': 78, '9': 76, '10': 75}

## Trade price behaviour
- Mean price minus fair: 0.15440278717923725
- Mean price minus mid: 0.14150197628458497
- Print locations: {'touch': 1.0}
- Visible sweep rate: 0.0

## Reduced-form bot interpretation
- Fair process: drifting with regimes
- Suggested latent archetypes: ['tight top-of-book market maker']
- Quote placement process: quotes concentrate around stable offsets from fair and rounded grid prices
- Trade arrivals: roughly Poisson-like sparse arrivals
- Simplest plausible explanation: ASH_COATED_OSMIUM looks like a market driven by tight top-of-book market maker.
