# CTREND v0 — baseline cross-sectional trend (2026-07-17)

**Statut : CANDIDAT, PAS UN EDGE VALIDÉ. Ne pas déployer. Verdict → v1.**

Piste #1 du plan Edge Factory. Protocole pré-enregistré, un seul run, aucun
paramètre optimisé (`scripts/backtest_ctrend_v0.py`, résultat complet dans
`CTREND_V0_RESULT.json`).

Note de traçabilité : un premier run v0 avait été effectué dans la session
du 2026-07-17 après-midi (MaxDD −71,2 %, Sharpe coûts ×2 0,85, 44 % mois
positifs) mais ses artefacts n'ont jamais été écrits sur disque (limite de
session atteinte). Le présent run est la reconstruction pré-enregistrée ;
implémentation différente, profil qualitatif identique.

## Protocole

- Univers : les 50 symboles du collecteur dérivés — **choisis en 2026,
  biais de survivance assumé** (49 chargés, 1 sans données Vision).
- Données : klines 1d Vision um, 2020-01-01 → 2026-06-30,
  hash `47ad97819cfcf9c3`.
- Score : moyenne des z-scores cross-section des rendements {1,3,7,14,30} j.
- Portefeuille : long-only top-5 équipondéré parmi scores > 0, sinon cash.
- Gate régime : BTC > MA20 sinon 100 % cash.
- Rebalance 7 j, exécution barre suivante, 30 bps A/R sur turnover, ×1 et ×2.
- Aucun forward-fill de rendement.

## Résultat (résumé — détail dans le JSON)

| | coûts ×1 | coûts ×2 |
|---|---:|---:|
| CAGR | +110,4 % | +98,5 % |
| CMGR | +6,4 %/mois | +5,9 %/mois |
| Moyenne / médiane mensuelle | +10,2 % / +2,7 % | +9,6 % / +1,9 % |
| Mois positifs | 52 % | 52 % |
| Sharpe (daily, ann.) | 1,39 | 1,30 |
| **MaxDD** | **−66,9 %** | **−69,6 %** |
| 2021 / 2022 / 2023 | +485 % / −46 % / +456 % | +451 % / −48 % / +415 % |
| 2024 / 2025 / 2026 | +286 % / −32 % / −22 % | +261 % / −36 % / −24 % |
| Top-3 mois (part du PnL) | 48 % | 51 % |
| Top-10 jours (part du PnL) | 40 % | 43 % |

## Limites connues (pourquoi ce n'est PAS un edge validé)

1. **Univers survivant fortement biaisé** : les 50 symboles ont été choisis
   en 2026 parmi les vainqueurs. Les +456 %/an de 2023 sont en partie un
   artefact de sélection ; un délisté acheté en tendance n'apparaît jamais.
2. **MaxDD −67/−70 % incompatible** avec la limite projet de 15 % (et le
   budget DD paper de 3 %). Ordre de grandeur : ramener mécaniquement ce DD
   vers −15 % divise aussi le rendement par ~4-5 ; le vol-targeting seul ne
   résout rien.
3. **PnL concentré** : top-3 mois ≈ moitié du PnL total, top-10 jours ≈ 40 %.
   La médiane mensuelle (+2,7 %) est très loin de la moyenne (+10,2 %) —
   le rendement vit dans quelques tendances 2021/2023/2024.
4. **Années récentes négatives** : 2025 −32 %, 2026 −22 %. Cohérent avec le
   diagnostic répété du repo (le directionnel long alts ne paie plus net de
   frais depuis 2025) et avec le rejet xs-momentum du 2026-07-12.
5. **CMGR ≠ moyenne** : 6,4 %/mois de CMGR mais médiane 2,7 % — la moyenne
   arithmétique (10,2 %) surestime ce qu'un mois typique rapporte.
6. Sharpe coûts ×2 de 1,30 ici (0,85 sur le run perdu) : dans les deux cas
   insuffisant pour porter un DD de ~70 %.

## Ce que v0 établit quand même

- Le facteur tendance cross-section EXISTE dans ces données (Sharpe > 1
  même à coûts ×2, sur un univers certes biaisé) — cohérent avec la
  littérature (JFQA 2024). Il justifie le coût de construction de v1.
- La mécanique (score multi-horizon, top-K, gate régime, barre suivante,
  coûts sur turnover) est en place et réutilisable.

## Suite obligatoire (v1 — pré-enregistré, voir CTREND_V1)

Univers point-in-time (top liquidité à t−1, délistés conservés, volume
médian glissant), self-financing avec cash explicite, exécution barre
suivante, coûts ×1/×2, délai +1 barre, DD brut et après risk-targeting,
contributions top mois/jours, DSR/PBO, sensibilité paramètres, corrélation
aux trois jambes existantes. Seul overlay autorisé : vol-targeting + caps
par actif + hystérésis de classement.

## Environnement

- `.venv` Python 3.8.10, pandas/pyarrow du projet.
- Commande : `.venv/bin/python scripts/backtest_ctrend_v0.py`
- Cutoff données : 2026-06-30 (klines mensuelles Vision, J-mois).
- Généré : 2026-07-17T19:42Z.
