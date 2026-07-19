# Scan signal OPTIONS_POSITIONING (Deribit trades → BTC forward)

1294 jours, 2023-01-01 → 2026-07-17. 54 tests directionnels ; seuil de sérieux p < 0.002.

```
signal               delai fwd_d      IC         p  Q5-Q1 bps
──────────────────────────────────────────────────────────────
d_skew_25ish             0     1  -0.001   9.8e-01      -16.7
d_skew_25ish             0     3  -0.018   5.1e-01       -9.7
d_skew_25ish             0     7  -0.013   6.5e-01      -27.7
d_skew_25ish             1     1  -0.023   4.2e-01      -17.4
d_skew_25ish             1     3  -0.036   2.1e-01      -75.1
d_skew_25ish             1     7  -0.004   8.7e-01        5.2
skew_25ish               0     1   0.013   6.4e-01      -13.6
skew_25ish               0     3   0.017   5.5e-01       -4.4
skew_25ish               0     7   0.070   1.3e-02       53.1
skew_25ish               1     1  -0.001   9.7e-01        7.5
skew_25ish               1     3   0.041   1.5e-01       18.2
skew_25ish               1     7   0.083   3.3e-03       91.7
d_atm_iv_traded          0     1   0.032   2.6e-01       37.2
d_atm_iv_traded          0     3   0.032   2.5e-01       24.8
d_atm_iv_traded          0     7   0.017   5.4e-01       27.0
d_atm_iv_traded          1     1   0.004   8.9e-01        3.2
d_atm_iv_traded          1     3  -0.010   7.3e-01      -15.1
d_atm_iv_traded          1     7   0.018   5.3e-01        5.7
d_pc_volume_ratio        0     1   0.015   6.0e-01        2.1
d_pc_volume_ratio        0     3  -0.000   9.9e-01       -2.2
d_pc_volume_ratio        0     7  -0.018   5.2e-01       16.8
d_pc_volume_ratio        1     1  -0.026   3.5e-01       -8.9
d_pc_volume_ratio        1     3  -0.014   6.2e-01        0.6
d_pc_volume_ratio        1     7  -0.000   9.9e-01       92.5
pc_volume_ratio          0     1   0.005   8.5e-01       -2.9
pc_volume_ratio          0     3  -0.006   8.2e-01      -14.9
pc_volume_ratio          0     7   0.022   4.4e-01       31.6
pc_volume_ratio          1     1  -0.012   6.7e-01       -1.9
pc_volume_ratio          1     3   0.024   4.0e-01       44.6
pc_volume_ratio          1     7   0.045   1.1e-01       62.1
net_call_flow_btc        0     1   0.004   8.9e-01        2.4
net_call_flow_btc        0     3  -0.003   9.2e-01      -24.3
net_call_flow_btc        0     7   0.017   5.6e-01        6.3
net_call_flow_btc        1     1  -0.007   7.9e-01      -27.3
net_call_flow_btc        1     3   0.023   4.2e-01       -3.9
net_call_flow_btc        1     7  -0.005   8.6e-01      -19.2
net_put_flow_btc         0     1  -0.023   4.0e-01      -15.6
net_put_flow_btc         0     3  -0.003   9.2e-01        5.2
net_put_flow_btc         0     7   0.011   6.9e-01       22.0
net_put_flow_btc         1     1   0.022   4.4e-01       17.6
net_put_flow_btc         1     3   0.011   6.9e-01       18.8
net_put_flow_btc         1     7   0.032   2.6e-01       81.0
top_strike_share         0     1  -0.008   7.6e-01       -8.6
top_strike_share         0     3   0.004   9.0e-01      -15.3
top_strike_share         0     7   0.003   9.3e-01       -1.7
top_strike_share         1     1   0.017   5.5e-01       -8.4
top_strike_share         1     3   0.013   6.4e-01       19.3
top_strike_share         1     7   0.016   5.6e-01       39.6
block_share              0     1  -0.040   1.5e-01      -22.1
block_share              0     3  -0.031   2.8e-01      -71.2
block_share              0     7  -0.019   5.0e-01      -60.2
block_share              1     1   0.003   9.1e-01      -34.0
block_share              1     3   0.017   5.4e-01        2.5
block_share              1     7  -0.024   4.0e-01      -74.5
```

=== Volet FILTRE : |stress positionnement| → risque 7 j suivant ===
```
|z(d_skew_25ish)|>2 : n=72, vol7f 47% vs base 42%, ret7f +256 bps vs +66
|z(d_atm_iv_traded)|>2 : n=68, vol7f 47% vs base 42%, ret7f +206 bps vs +69
|z(d_pc_volume_ratio)|>2 : n=82, vol7f 45% vs base 42%, ret7f +127 bps vs +73
```

## Verdict brut

0 test(s) directionnel(s) sous p<0.002 sur 54. Aucun signal directionnel sérieux — l'info options ne prédit pas le RENDEMENT BTC au quotidien sur 2023-2026 avec ces features v0. Reste le volet filtre/risque ci-dessus.
