# Piste 3 — Top-trader divergence

> ## ❌ VERDICT : NO_EDGE — effet mort post-2024 (2026-07-18, commit distant `7ef8d83`)
>
> **La prémisse data était fausse** : les ratios top-traders existent en
> historique 5 m depuis 2021-12 dans les fichiers Vision `metrics`
> (la rétention 30 j ne concerne que l'API live). Test immédiat possible
> et fait : panel 49 actifs, 1,4 M obs 1 h, divergence z(top_pos) −
> z(global) → fwd 24 h. Moitié 1 (2021→2024) : t = **14,8**. Moitié 2
> (2024→2026) : t = **−0,75** — l'effet s'est fait arbitrer.
> 31/49 symboles positifs (< gate 2/3). Rien à câbler dans le régime
> actuel ; l'archivage live continue, rééval seulement sur changement
> de régime documenté.

Binance publie séparément, pour les comptes appartenant aux 20 % ayant la
marge la plus élevée :

- `topLongShortAccountRatio` — ratio par **nombre de comptes** ;
- `topLongShortPositionRatio` — ratio par **taille de positions** ;
- `globalLongShortAccountRatio` — ratio retail global ;
- `takerlongshortRatio` — flux taker buy/sell ;
- `openInterestHist` — OI en contrats et en valeur.

**Rétention API : ~30 jours.** L'archivage continu a démarré le 2026-07-17 via
`bin/archive-derivs` (module `data_pipeline/derivatives_positioning.py`),
top 40 symboles par volume, granularité 5 m, format large dans
`data/raw/binance_futures_positioning/futures_um/{symbol}/5m/`.

## Quatre sous-signaux à tester

1. **Lead** : les top traders deviennent longs avant le retail
   (Δ `ls_ratio_top_accounts` devance Δ `ls_ratio_global`).
2. **Conviction** : `ls_ratio_top_positions` monte sans hausse de
   `ls_ratio_top_accounts` → moins de comptes mais plus gros ; taille
   moyenne en hausse.
3. **Distribution** : top traders réduisent pendant que le retail continue
   d'acheter (divergence top ↓ / global ↑) → signal de retournement baissier.
4. **Contrarian extrême** : consensus extrême top+retail simultané, croisé
   avec funding/OI extrêmes → fade.

Un seul sous-signal validé compte comme edge. Les variantes fortement
corrélées restent une même famille.

## Ce que dit la littérature positionnement (à transposer avec prudence)

Pas d'étude académique publiée sur les ratios top-traders Binance
eux-mêmes (c'est précisément l'intérêt de l'archive) ; la littérature COT
sur les futures traditionnels donne le prior :

- Les tests de causalité de Granger montrent **peu de pouvoir prédictif
  direct** des positions sur les retours du même marché ; les positions
  *réagissent* surtout aux prix (Sanders et al., *Smart Money? The
  Forecasting Ability of CFTC Large Traders*,
  https://ideas.repec.org/p/ags/nccsci/37556.html).
- En revanche, le positionnement des money managers prédit **avec délai**
  des actifs liés (JFQA 2023,
  https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/is-there-smart-money-how-information-in-the-commodity-futures-market-is-priced-into-the-cross-section-of-stock-returns-with-delay/1BE53A3150FC165F3A509BCBBC23A692) —
  la diffusion d'information est lente, l'effet est plus fort où
  l'asymétrie d'information est grande (ce qui décrit bien les alts).
- L'exposition spéculative nette élevée tend à la **continuation à court
  horizon**, pas au retournement.

Implications de design : attendre des effets **petits et conditionnels**
(d'où le rôle de filtre/score, jamais de signal autonome) ; tester le
lead-lag en *variations* et pas en niveaux ; privilégier les alts vs BTC ;
et tester la continuation avant le contrarian.

## Contraintes

- Horizons cibles : 15 min à 24 h.
- Ne pas backtester au-delà de la fenêtre archivée (pas d'historique long
  disponible — c'est précisément pourquoi l'archivage devait démarrer tôt).
  Accumuler ≥ 60–90 jours avant première évaluation sérieuse.
- Croiser avec CVD (déjà dans les features 1 m) et liquidations (piste 2).
