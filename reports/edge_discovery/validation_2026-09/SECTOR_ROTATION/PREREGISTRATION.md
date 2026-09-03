# PREREGISTRATION — SECTOR_ROTATION + SECTOR_RELATIVE_STRENGTH_REVERSAL

**Worker:** V5, Alpha Validation Factory wave 2. **Écrit :** 2026-09-03, AVANT tout calcul de
rendement (seules la liste des symboles et leur dollar-volume médian 2025 ont été inspectés, pour
construire la carte sectorielle — jamais une colonne de rendement forward).

**Réclamations testées** (`round3/w3_relative_value/REPORT.md`, evidence NON ouverte) :
- `D-SECTOR-ROTATION-D_mom-7D` : 10 paniers sectoriels rangés par rendement 7 j, continuation,
  tiers haut vs bas, **+103,0 bps net**, N_indep 259, 6/6 années, caveat « low-df ».
- `D-RVSECTOR-C_rev-7D` : rang `vs_sector_7d` dans un univers taggé, **REVERSAL**, +46,4 bps net,
  N_indep 324, 5/7, capacité MED $268M.

## 0. Carte sectorielle — construite ici, jamais lue depuis un `sector_map.py` existant

Assignation par nature économique du protocole, décidée avant tout résultat. 10 secteurs
(le nombre de paniers de la réclamation). Tout symbole non listé → `OTHER`, qui est un panier
à part entière (jamais silencieusement supprimé) et dont l'exclusion est la perturbation P3.

| Secteur | Logique |
|---|---|
| `L1` | Chaînes de couche 1 généralistes (BTC/ETH exclus du classement, voir §1) |
| `L2_SCALING` | Rollups et scaling Ethereum |
| `DEFI` | AMM, prêt, dérivés on-chain, LST/restaking |
| `MEME` | Actifs sans utilité revendiquée, dirigés par l'attention |
| `AI` | Calcul/agents/données IA |
| `GAMING_NFT` | Jeu, métavers, places de marché NFT |
| `INFRA_DATA` | Oracles, stockage, indexation, DePIN |
| `PRIVACY` | Confidentialité |
| `PAYMENT_LEGACY` | Paiement/POW historiques et jetons de cycles antérieurs |
| `RWA_STABLE` | Actifs réels tokenisés, or, rendement stable |

La carte complète est dans `_lib/sector_map_v5.py` (écrite avant l'exécution, versionnée).

## 1. PRIMARY_SPEC — figée

| Item | Règle |
|---|---|
| Panel / univers PIT | Identique à `../XSEC_MOMENTUM_HORIZON_EXTENSION/PREREGISTRATION.md` §2 : barres 5 m → jours UTC, âge de listing ≥ 30 j, médiane causale 30 j du dollar-volume ≥ $1 M, `n_eligible ≥ 20`. |
| Exclusion | BTCUSDT et ETHUSDT sont exclus du classement sectoriel : ce sont des paniers à eux seuls et leur inclusion transformerait « rotation sectorielle » en « beta marché ». Déclaré ici, pas après coup. |
| Score de panier | `basket_ret_7d(S, d)` = moyenne équipondérée de `close_d/close_{d−7} − 1` sur les membres éligibles du secteur S. Secteur retenu seulement s'il a ≥ 3 membres éligibles ce jour-là (sinon le secteur est écarté ce jour, compté). |
| Signal par nom | Chaque nom hérite du score de son secteur → le rang cross-sectionnel classe les SECTEURS, pas les noms (construction de la réclamation). |
| Construction | CONTINUATION : LONG le tiers haut des secteurs (par score), équipondéré au niveau du nom à l'intérieur des secteurs retenus. Horizon et rebalancement 7 j, non chevauchant. |
| Bras B | Univers éligible équipondéré, même fenêtre, même règle de sortie. |
| Statistique de verdict | `excess = (R_top_secteurs − R_univers) × 1e4` ; `net14 = excess − 14`, `net28 = excess − 28`. Le brut vs zéro est reporté à côté, pour comparaison avec la réclamation seulement. |
| Sortie / winsorisation / coûts | Identiques au prereg frère (dernier close disponible dans `(d, d+7]`, winsorisation 1 %/99 % sur la cross-section éligible complète). |

`SECTOR_RELATIVE_STRENGTH_REVERSAL` : même panel, signal `vs_sector_7d(i,d)` = rendement 7 j du
nom − rendement 7 j de son panier ; **REVERSAL** → LONG le quintile le PLUS BAS. Même gate.

## 2. Perturbations préenregistrées (≤ 8)

| # | Perturbation | But |
|---|---|---|
| P1 | Carte grossière à 4 secteurs (`MAJOR_L1`, `DEFI_INFRA`, `MEME_RETAIL`, `OTHER`) | **sensibilité à la carte (obligatoire)** |
| P2 | Membres minimum par secteur = 5 au lieu de 3 | robustesse des petits paniers (caveat « low-df ») |
| P3 | Exclure le panier `OTHER` | le résultat dépend-il du fourre-tout ? |
| P4 | Exclure 2021 | concentration de régime (obligatoire) |
| P5 | Coût +50 % | fragilité au coût |
| P6 | Les 7 phases d'ancrage | robustesse de phase |
| P7 | Plancher de liquidité $2 M | sensibilité de cohorte |
| P8 | Quintile haut au lieu du tiers haut | sensibilité au découpage |

## 3. Déclustering

`n_raw` = positions nom × rebalancement ; `L1` = n_raw ; `L2` = période de rebalancement ;
**`L3` = mois calendaire** (unité d'inférence ; ~4,3 périodes/mois à 7 j). t cluster-robuste sur
L3, block bootstrap par mois (10 000 tirages).

Contrôle « même facteur ? » obligatoire : corrélation de rang du score sectoriel avec mom7/mom14
au niveau du nom, et corrélation des rendements de portefeuille avec le momentum cross-sectionnel
7 j et Amihud — un « panier sectoriel » qui reproduit le momentum n'est pas un facteur nouveau.

## 4. Critères de succès (`VALIDATED_FOR_FORWARD`, PRIMARY uniquement)

1. `excess_net14 > 0`, `t_L3 ≥ 1,645`, 5e centile bootstrap `> 0`.
2. `excess_net28 > 0` sinon `COST_FRAGILE` au mieux.
3. ≥ 4/7 années positives ET ex-2021 positif, sinon `REGIME_DEPENDENT`.
4. P1 (carte grossière) garde le signe : si le résultat s'inverse en changeant la carte, le
   « secteur » n'est pas le mécanisme → `REJECTED`.
5. Corrélation des rendements de portefeuille avec le momentum 7 j < 0,8 (sinon = même facteur,
   au mieux `OVERLAY_ON_CROSS_SECTIONAL_MOMENTUM_LIVE_V2`).

`t_L3 < 1,0` → `REJECTED` ; `1,0 ≤ t_L3 < 1,645` avec 2-5 passés → `NEEDS_MORE_RESEARCH`.
Aucun paramètre n'est modifié après avoir vu un résultat.
`confirmable_in_horizon` = `eta_conservative < 1095 j`, plancher `minimum_calendar_days = 182`.
