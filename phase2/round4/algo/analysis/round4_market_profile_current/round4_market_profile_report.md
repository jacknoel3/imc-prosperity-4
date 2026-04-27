# Round 4 Market Profile

## Calibration Read

- Use public days as separate regimes, not one pooled expected-value estimate.
- Prefer Rust backtester `--calibration round4` or harsher for strategy ranking.
- Treat high day-to-day mid shifts and unstable OBI correlations as hidden-day risk.

## Most Active Products

| product             |   avg_trade_count_per_day |   avg_trade_qty_per_day |   avg_spread |
|:--------------------|--------------------------:|------------------------:|-------------:|
| VELVETFRUIT_EXTRACT |                  460.333  |                2734.33  |      4.98243 |
| HYDROGEL_PACK       |                  340.667  |                1365.33  |     15.7279  |
| VEV_4000            |                  147.333  |                 292     |     20.7535  |
| VEV_6000            |                  105.667  |                 368.333 |      1       |
| VEV_6500            |                  105.667  |                 368.333 |      1       |
| VEV_5500            |                  102      |                 356.333 |      1.10877 |
| VEV_5400            |                   92      |                 319.667 |      1.30403 |
| VEV_5300            |                   54.6667 |                 182.667 |      1.97307 |
| VEV_5200            |                   15.6667 |                  54     |      2.75783 |
| VEV_4500            |                    1      |                   2     |     15.7938  |
| VEV_5000            |                    1      |                   2     |      5.96423 |
| VEV_5100            |                    1      |                   2     |      4.1744  |

## Largest Day-Mean Shifts

| product             |   day_mean_mid_range |   max_abs_day_drift |   avg_ret_lag1_autocorr |
|:--------------------|---------------------:|--------------------:|------------------------:|
| VEV_5200            |             17.3234  |                49.5 |              -0.12576   |
| VEV_5100            |             17.0442  |                60   |              -0.0972075 |
| VEV_5000            |             16.9186  |                62.5 |              -0.102569  |
| VEV_4500            |             16.2387  |                63.5 |              -0.233408  |
| VEV_4000            |             16.233   |                64   |              -0.294002  |
| VELVETFRUIT_EXTRACT |             16.2301  |                63.5 |              -0.160139  |
| VEV_5300            |             14.7676  |                32   |              -0.214366  |
| HYDROGEL_PACK       |             13.101   |                57   |              -0.124199  |
| VEV_5400            |              7.15335 |                15   |              -0.24907   |
| VEV_5500            |              4.3126  |                 5.5 |              -0.241684  |
| VEV_6000            |              0       |                 0   |             nan         |
| VEV_6500            |              0       |                 0   |             nan         |

## OBI/Next-Return Signal

| product             |   avg_obi_next_ret_corr_5 |   min_obi_next_ret_corr_5 |   max_obi_next_ret_corr_5 |
|:--------------------|--------------------------:|--------------------------:|--------------------------:|
| VEV_5000            |                 0.0999793 |                 0.0825945 |                  0.113934 |
| VEV_5100            |                 0.105611  |                 0.096715  |                  0.116613 |
| VEV_5200            |                 0.133706  |                 0.107053  |                  0.164644 |
| HYDROGEL_PACK       |                 0.138206  |                 0.126571  |                  0.144286 |
| VELVETFRUIT_EXTRACT |                 0.14172   |                 0.140512  |                  0.143677 |
| VEV_5300            |                 0.170149  |                 0.112976  |                  0.21726  |
| VEV_5400            |                 0.207723  |                 0.196453  |                  0.219968 |
| VEV_5500            |                 0.212189  |                 0.192447  |                  0.233001 |
| VEV_4500            |                 0.228617  |                 0.207295  |                  0.245999 |
| VEV_4000            |                 0.297806  |                 0.27399   |                  0.321411 |
| VEV_6000            |               nan         |               nan         |                nan        |
| VEV_6500            |               nan         |               nan         |                nan        |

## Strongest 50-Tick Buyer Markout Players

| player   |   avg_buyer_markout_50 |   buyer_win_rate_50 |   buy_trades | buy_products                                                                            |
|:---------|-----------------------:|--------------------:|-------------:|:----------------------------------------------------------------------------------------|
| Mark 14  |               6.52094  |            0.713398 |         1127 | HYDROGEL_PACK|VELVETFRUIT_EXTRACT|VEV_4000|VEV_5200|VEV_5300|VEV_5400|VEV_5500          |
| Mark 67  |               1.12121  |            0.533333 |          165 | VELVETFRUIT_EXTRACT                                                                     |
| Mark 49  |               0.941176 |            0.411765 |           17 | VELVETFRUIT_EXTRACT                                                                     |
| Mark 01  |               0.926959 |            0.824265 |         1599 | VELVETFRUIT_EXTRACT|VEV_5200|VEV_5300|VEV_5400|VEV_5500|VEV_6000|VEV_6500               |
| Mark 22  |              -0.047619 |            0.52381  |           42 | HYDROGEL_PACK|VELVETFRUIT_EXTRACT|VEV_4000|VEV_4500|VEV_5000|VEV_5100|VEV_5200|VEV_5300 |
| Mark 55  |              -2.10822  |            0.377926 |          598 | VELVETFRUIT_EXTRACT                                                                     |
| Mark 38  |              -9.28523  |            0.180082 |          733 | HYDROGEL_PACK|VEV_4000|VEV_4500|VEV_5000|VEV_5100|VEV_5200|VEV_5300                     |
