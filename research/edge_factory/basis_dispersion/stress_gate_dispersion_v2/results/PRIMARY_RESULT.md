# PRIMARY_RESULT — stress_gate_dispersion_v2_reproduction

**Verdict : REPRODUCED_CAUSAL_ASSOCIATION**

- delta primaire (mean loss stress - non_stress) : 0.0029429590471191716
- bootstrap CI95 (blocs calendaires 7j, 10000 resamples, seed 20260721) : [7.86851898549675e-05, 0.005976663718225493]
- HAC de soutien : coef=0.00294296, p=0.03095, lag=16, n=15330
- lignes totales : 16146, seuil disponible : 15330, classées stress : 853

Gate : `delta>0 AND bootstrap_ci95_lower>0 AND HAC même signe et p<0.05`.