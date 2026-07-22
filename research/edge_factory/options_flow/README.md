# Piste 7 — Flux options directionnels (Deribit)

> ## ❌ VERDICT : NO_EDGE (2026-07-17, commit distant `fdfa862`)
>
> Test pré-enregistré sur 3,3 ans de trades Deribit BTC backfillés
> (1 216 jours, machine distante). Primaire (flux OTM signé, z90) :
> NW-t = 0,28, alpha de timing négatif même à coûts ×1, DSR 5,5 %.
> Les 6 secondaires (skew fade, Δskew, P/C, pinning, blocs, 3 j) tous
> morts (|t| < 1). **Fermé au grain quotidien.** Non testés : grain
> intraday (tick dispo) et ETH — ne rouvrir que sur thèse précise.

Signal directionnel et de régime sur BTC/ETH à partir du positionnement
options — **distinct du VRP, qui a échoué** : ici on lit le *flux* et le
*positionnement* (qui achète quoi), pas la prime de variance.

Horizon : 4 h – 7 j.

## Ce que dit la recherche

- Alexander, Deng, Feng & Wan, *Net Buying Pressure and the Information in
  Bitcoin Option Trades* (https://arxiv.org/abs/2109.02776, tick Deribit) :
  les prix des options **ATM sont pilotés par les traders de volatilité**,
  mais les **OTM sont aussi pilotées par des traders directionnels
  informés** — Deribit agrège de l'information directionnelle exploitable.
  C'est l'inverse des marchés options actions US/Asie (offre market-maker
  dominante), donc ne pas transposer les résultats actions.
- *Are Bitcoin option traders speculative or informed?* (Finance Research
  Letters, tick Deribit 2020-2022,
  https://www.sciencedirect.com/science/article/abs/pii/S1544612324007694) :
  distingue flux spéculatif et flux informé selon métriques de trades et
  attention — base méthodologique pour séparer nos deux populations.
- Deribit Insights, 4 ans de régimes de vol
  (https://insights.deribit.com/industry/bitcoin-options-finding-edge-in-four-years-of-volatility-regimes/) :
  quand le skew 25Δ devient **excessivement positif (calls surpayés)**,
  vendre le risk-reversal a offert le meilleur rendement ajusté du risque —
  le skew extrême est contrarian.
- Kaiko : la surface IBIT diverge de Deribit (aile put plus convexe)
  (https://www.kaiko.com/news/kaiko-research-highlights-tail-risk-divergence-between-ibit-and-deribit-options) —
  la dispersion Deribit vs options ETF est elle-même un signal potentiel.

## Sous-signaux à tester

1. **Δ skew 25Δ** (niveau *et* variation) : skew put extrême + détente =
   régime de reprise ; bascule call extrême = euphorie, fade.
2. **Put/call notionnel OTM signé** : pression d'achat OTM directionnelle
   (le canal « informé » du papier ci-dessus), pas le simple ratio de volumes.
3. **Blocs Deribit** : trades `block_trade` gros notionnel, direction et
   strikes — flux institutionnel visible.
4. **Concentration de strikes** : murs d'OI proches du spot (effet
   pinning/max-pain autour des grosses échéances trimestrielles).
5. **Structure par terme IV** : inversion courte (backwardation IV) comme
   marqueur de stress ; re-pentification comme signal de normalisation.

## Données

- ✅ `deribit_options_summary` archivé en 4 h depuis `config/data_sources.yml`
  (`atm_iv`, `put_call_vol_ratio`, `put_call_oi_ratio`, `skew_25d_approx`) —
  vérifier la profondeur d'historique accumulée avant tout backtest.
- À ajouter (API publique Deribit, gratuit) :
  - `public/get_last_trades_by_currency` avec flag `block_trade` ;
  - snapshot chaîne complète (`get_book_summary_by_currency` détaillé par
    instrument) pour concentration de strikes et term structure ;
  - `get_volatility_index_data` (DVOL) en 1 h.
- L'historique tick complet est payant (Tardis) — **rester sur snapshots
  4 h/1 h auto-archivés**, cohérent avec la politique sources gratuites.

## Protocole de rejet

- Le signal doit survivre à : une barre de délai, coûts ×2, et suppression
  des 10 plus gros événements d'échéance (expiries trimestrielles).
- Tester séparément *niveau* vs *variation* du skew : le niveau est un
  régime, la variation un timing — ne pas les confondre dans un seul score.
- Corrélation PnL avec ctrend et liquidation_exhaustion < 0,35 (gates du
  [README parent](../README.md)).
