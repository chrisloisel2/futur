# Piste 1 — Cross-sectional trend (CTREND)

Classement de 30–80 cryptos liquides par tendance prix-volume multi-horizon ;
long top-K, cash si régime défavorable. Horizon : 1–7 jours.

Le facteur CTREND documente une prédictibilité cross-sectionnelle qui
subsiste dans les grandes cryptos liquides et après coûts (Han, Kang, Li &
Sim, *A Trend Factor for the Cross-Section of Cryptocurrency Returns*, JFQA).

## v0 — `ctrend_v0.py`

Première passe volontairement simple, pour établir une baseline honnête :

- **Univers** : top-40 perpétuels USDT-M par volume 24 h (snapshot du jour).
  ⚠️ *Biais de survivance assumé en v0* : l'univers d'aujourd'hui est
  appliqué au passé. Le snapshot quotidien archivé par `bin/archive-derivs`
  (`binance_futures_universe`) rend l'univers point-in-time possible à
  partir du 2026-07-17 ; v1 devra l'utiliser (ou reconstruire l'historique
  d'univers depuis les volumes quotidiens Binance Vision).
- **Facteurs** : momentum 1 h, 4 h, 24 h, 3 j, 7 j sur closes 1 h, chacun
  normalisé par la volatilité réalisée du symbole, puis z-scoré en
  cross-section ; composite = moyenne des z-scores.
- **Portefeuille** : long-only top-K (K=5) equal-weight, rebalancement
  quotidien 00:00 UTC, entrée seulement si z composite > 0.
- **Régime** : cash si BTC < EMA-20 j (filtre défavorable).
- **Coûts** : 6 bps par côté sur le turnover ; variantes ×1 et ×2.

## Étapes suivantes (v1+)

- Univers point-in-time (supprime le biais de survivance).
- Neutralisation bêta-BTC et concentration sectorielle.
- Confirmation par volume (interaction prix-volume du papier).
- Walk-forward + DSR/PBO avant toute conclusion — les gates du
  [README parent](../README.md) s'appliquent.
