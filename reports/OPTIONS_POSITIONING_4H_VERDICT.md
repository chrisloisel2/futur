# VERDICT — OPTIONS_POSITIONING_4H (protocole pré-enregistré 1d06580)

Exécution unique 2026-07-18. 7663 buckets 4h (2023-01-01 → 2026-07-01), 1 buckets sans skew calculable (jambe OTM vide).

## VERDICT : **NO_EDGE_DEFINITIF**

```
          signal  delay  horizon_4h    n      ic        p  q5_q1_bps  ic_half1  ic_half2  PASS
       d_skew_4h      0           1 7482 -0.0055 0.633804       -1.9   -0.0059   -0.0031 False
       d_skew_4h      0           2 7481 -0.0133 0.249524       -8.0   -0.0137   -0.0117 False
       d_skew_4h      0           6 7477 -0.0013 0.909304        0.3   -0.0004   -0.0021 False
       d_skew_4h      1           1 7480  0.0178 0.122707        4.1    0.0196    0.0154 False
       d_skew_4h      1           2 7479  0.0099 0.389799        4.5    0.0144    0.0054 False
       d_skew_4h      1           6 7475  0.0040 0.730871        4.9   -0.0011    0.0100 False
     d_atm_iv_4h      0           1 7482  0.0041 0.720793        5.3    0.0181   -0.0098 False
     d_atm_iv_4h      0           2 7481 -0.0056 0.625814        3.4   -0.0008   -0.0113 False
     d_atm_iv_4h      0           6 7477  0.0080 0.489876       20.8    0.0012    0.0142 False
     d_atm_iv_4h      1           1 7480 -0.0040 0.731445       -1.6   -0.0371    0.0274 False
     d_atm_iv_4h      1           2 7479  0.0023 0.844893        3.7   -0.0213    0.0248 False
     d_atm_iv_4h      1           6 7475  0.0082 0.478978       10.6   -0.0015    0.0178 False
net_call_flow_4h      0           1 7483 -0.0051 0.660117        1.0   -0.0193    0.0064 False
net_call_flow_4h      0           2 7482 -0.0142 0.220862        4.7   -0.0283   -0.0034 False
net_call_flow_4h      0           6 7478 -0.0019 0.868476        0.4   -0.0350    0.0309 False
net_call_flow_4h      1           1 7481 -0.0079 0.494889       -1.2   -0.0276    0.0120 False
net_call_flow_4h      1           2 7480  0.0086 0.455386        5.9   -0.0164    0.0321 False
net_call_flow_4h      1           6 7476  0.0034 0.768350       -4.1   -0.0098    0.0173 False
 net_put_flow_4h      0           1 7483  0.0195 0.091935        2.4    0.0302    0.0097 False
 net_put_flow_4h      0           2 7482  0.0103 0.374124        5.1    0.0328   -0.0104 False
 net_put_flow_4h      0           6 7478  0.0052 0.652324        2.2    0.0172   -0.0062 False
 net_put_flow_4h      1           1 7481  0.0033 0.773333       -2.2   -0.0003    0.0073 False
 net_put_flow_4h      1           2 7480  0.0064 0.578494       -5.7   -0.0006    0.0136 False
 net_put_flow_4h      1           6 7476 -0.0036 0.754864       -4.5   -0.0051   -0.0028 False
```

Critère (fixé avant run) : p<0.002 ET |IC|≥0.04 ET même signe sur les deux moitiés (n≥500 chacune). Aucune cellule qualifiée → OPTIONS_POSITIONING est classé DÉFINITIVEMENT NO_EDGE ; aucune variante ultérieure ne sera tentée (règle utilisateur).
