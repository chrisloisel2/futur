# STABLECOIN_REGIME v0 — VERDICT : NO_EDGE

Protocole gelé : `reports/STABLECOIN_REGIME_PROTOCOL.md` — exécution unique 2026-07-18.

Fenêtre portefeuille : 2023-01-12 → 2026-03-31

## Critères figés

- c1_dd : FAIL
- c2_roi : PASS
- c3_sharpe : FAIL
- c4_episodes : FAIL
- c5_turnover : PASS
- c6_neighborhood : FAIL
- c7_stat : PASS

## Overlay (règle jugée, coûts ×1 / ×2)

- base : ROI +25.33%/an, maxDD -3.10%, Sharpe 3.62
- overlay ×1 : ROI +24.06%/an, maxDD -3.43%, Sharpe 3.59
- overlay ×2 : ROI +23.79%/an, maxDD -3.63%, Sharpe 3.55
- épisodes RISK_OFF : 7 (améliorés : 2), bascules/an : 4.4, jours off : 14.5%

## Volet statistique

- Bonferroni p<0.0016 sur 32 tests primaires : atteint
- features retenues (|IC|≥0,15, p<0,01, signe train=test) : ['F6->rv_btc_7']

Détail complet : `reports/STABLECOIN_REGIME_VERDICT.json`.