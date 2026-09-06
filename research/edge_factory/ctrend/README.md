# Piste 1 — Cross-sectional trend (CTREND)

> ## ❌ VERDICT : CTREND_REJECTED (2026-07-17, commit distant `859ebad`)
>
> v1 sur univers **point-in-time** (786 perps délistés inclus, top-50
> par volume médian 30 j décalé) : CAGR −2,1 %, médiane mensuelle
> −3,4 %. Le v0 positif était du **biais de survivance** — exactement le
> piège annoncé ci-dessous. Le verdict vaut pour la famille au grain
> quotidien/hebdo sur cet univers.

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
- **Grille de fréquences de rebalancement : 6 h / 24 h / hebdo.** Le papier
  CTREND travaille en hebdo value-weighted ; la littérature intraday
  Bitcoin (time-series momentum, Reading,
  https://centaur.reading.ac.uk/100181/) trouve des coûts de breakeven de
  **3–10 bps** — le 6 h n'est viable que si le turnover reste sous ce
  plafond. Mesurer le PnL net par fréquence, pas seulement le brut.
- Walk-forward + DSR/PBO avant toute conclusion — les gates du
  [README parent](../README.md) s'appliquent.

## Références

- Han, Kang, Li & Sim, *A Trend Factor for the Cross Section of
  Cryptocurrency Returns* (JFQA) — 28 signaux techniques (momentum, MA,
  volume, vol) sur 3 000+ coins 2015-2022 :
  https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/trend-factor-for-the-cross-section-of-cryptocurrency-returns/4C1509ACBA33D5DCAF0AC24379148178
- Liu, Tsyvinski & Wu, *Common Risk Factors in Cryptocurrency* (JF 2022) —
  marché/taille/momentum comme benchmark à battre :
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3379131
- Momentum court terme crypto et bénéfices de diversification :
  https://eprints.soton.ac.uk/434719/
