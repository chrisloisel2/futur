# CTREND v1 — univers point-in-time : **CTREND_REJECTED** (2026-07-17)

**Verdict : l'edge apparent de v0 était intégralement du biais de
survivance. Aucune variante n'est déployable. Famille cross-sectional
trend long-only : REJETÉE en l'état.**

Protocole pré-enregistré (docstring de `scripts/backtest_ctrend_v1.py`),
résultat complet `CTREND_V1_RESULT.json`. Aucun paramètre retouché après
lecture des résultats ; verdict sur le primaire.

## Construction (corrige les 6 limites de v0)

- **Univers point-in-time** : 786 perps USDT (énumération S3 complète,
  délistés inclus, stables/fiat exclus), top 50 par **volume médian
  glissant 30 j décalé à t−1** (≥ 5 M$/j), ≥ 31 j d'historique, barre
  présente. Aucune donnée avant listing ni après delisting ; delisting =
  sortie forcée au dernier close (2 j sans barre) avec coût.
- **Aucun forward-fill de rendement** (`pct_change(fill_method=None)`).
- **Self-financing, cash explicite** : NAV = cash + Σ positions ; frais
  (15 bps/côté, 30 bps A/R) **uniquement sur |Δ positions|**.
- **Exécution barre suivante** (open t+1 = close t en continu) ;
  robustesse +1 barre supplémentaire.
- Overlay pré-enregistré seul : vol-targeting 20 % ann. causal (expo ≤ 1),
  cap 25 %/actif, hystérésis entrée top-5 / sortie rang > 10.
- Signal identique à v0 : moyenne des z-scores cross-section {1,3,7,14,30} j,
  long-only top-5, gate BTC>MA20, rebalance 7 j.

## Résultats principaux (2020-06 → 2026-06, 72 mois)

| run | CAGR | CMGR | moy/méd mensuelle | mois>0 | Sharpe | MaxDD | turnover |
|---|---:|---:|---:|---:|---:|---:|---:|
| primaire ×1 | **−2,1 %** | −0,2 % | +3,0 % / **−3,4 %** | 41,7 % | 0,39 | **−94,5 %** | 40×/an |
| primaire ×2 | −7,8 % | −0,7 % | +2,5 % / −3,8 % | 41,7 % | 0,32 | −95,6 % | 40× |
| délai +1 barre | −5,7 % | −0,5 % | +2,5 % / −0,5 % | 43,1 % | 0,34 | −88,9 % | 40× |
| vol-target 20 % ×1 | +3,4 % | +0,3 % | +1,3 % / −1,8 % | 43,1 % | 0,29 | **−76,1 %** | 22× |
| vol-target 20 % ×2 | −0,0 % | −0,0 % | +1,0 % / −2,0 % | 43,1 % | 0,22 | −79,1 % | 22× |

Par année (primaire ×1) : 2020 +165 % · 2021 +372 % · **2022 −84 %** ·
2023 +66 % · **2024 −36 %** · **2025 −58 %** · 2026 (H1) +1,6 %.

- Le PnL cumulé étant ≈ 0 (−0,126 log-point), la « part du PnL » des top
  mois/jours n'est pas interprétable (ratios divergents dans le JSON) ;
  en absolu : top-3 mois = **+1,58 log-point**, top-10 jours = **+2,03
  log-point**, contre un total de **−0,13** — tout le rendement vit dans
  quelques poussées 2020-2021 que le reste de l'échantillon reconsomme.
- Le vol-targeting fait exactement ce que v0 annonçait : il divise le
  rendement (CAGR +3,4 %) sans ramener le DD sous la limite (−76 % vs
  budget 15 %) — la vol crypto saute trop vite pour une fenêtre causale 60 j.

## DSR / PBO / sensibilité

- **DSR = 0,35** (T=2220, skew +0,5, kurtosis 10,6, N=14 essais = 12
  variantes + primaire + v0). Le Sharpe observé (0,0205 daily) est
  **inférieur** au seuil de déflation SR₀ (0,0285) : indiscernable du
  meilleur d'un tirage aléatoire de 14 essais.
- **PBO = 0,615** (CSCV S=10, 252 combinaisons, 13 variantes) : la
  variante la meilleure in-sample finit dans la moitié BASSE hors-sample
  62 % du temps — pire qu'une pièce.
- Sensibilité (one-at-a-time, ×1) : **aucune variante positive
  exploitable** — CAGR de −28,7 % (lb+90j) à +3,4 % (no_gate, DD −97,5 %),
  DD toujours ≤ −85,8 %. Tableau complet dans le JSON.

## Face aux 3 jambes existantes (2023-01 → 2026-03)

Corrélations quotidiennes faibles (V1.2 +0,17 · STACK_MH +0,06 ·
BASIS_TERM −0,11) — mais la décorrélation ne sauve pas une jambe toxique :

| | ROI ann. | MaxDD | Sharpe |
|---|---:|---:|---:|
| Overlay 3 jambes (frontière courante) | +25,3 % | −3,1 % | 3,62 |
| + CTREND v1 (vol-targeté, la meilleure version) | +22,9 % | **−45,1 %** | 0,68 |

**Contribution marginale : destructrice.** Rien à intégrer.

## Diagnostic

v0 (univers 2026 survivant) : CAGR +110 %, 2023 +456 %. v1 (univers
point-in-time, mêmes signal/mécanique) : CAGR −2 %, 2023 +66 %, 2024-2025
détruits. La différence EST le biais de survivance : dans l'univers réel
de l'époque, le top-5 momentum achetait aussi les futurs délistés et les
memes morts. Confirme (8e fois) la règle du repo : le directionnel long
alts net de frais ne paie plus depuis 2024-2025, et un facteur académique
(JFQA 2024) ne survit pas tel quel à un univers honnête + coûts.

**Décision : ne pas retuner (fishing interdit). La famille est fermée
sauf idée STRUCTURELLEMENT différente (ex : neutralisation bêta-BTC +
long-short — bloqué par SHORT_REJECTED).**

## Environnement

- Données : klines 1d Vision um, 786 symboles, cutoff **2026-06-30**,
  hash (noms+tailles) `72d814f83b2bd8cf`.
- Env : `.venv` Python 3.8.10, pandas/numpy/scipy du projet.
- Commande : `.venv/bin/python scripts/backtest_ctrend_v1.py`
  (~13 min, 17 simulations). Généré 2026-07-17T20:08Z.
