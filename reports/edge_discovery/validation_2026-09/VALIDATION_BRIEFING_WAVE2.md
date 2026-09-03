# ALPHA VALIDATION FACTORY — WAVE 2 — BRIEFING COMMUN (2026-09-03)

Lis ce fichier EN ENTIER avant de commencer. Il remplace toute supposition.

## 0. Contexte

Wave 1 (2026-09-02, commit `eb84f93`) a validé indépendamment 8 candidats prioritaires du round 3 :
3 `VALIDATED_FOR_FORWARD` (AMIHUD_ILLIQUIDITY_PREMIUM, LIQ_REPEAT_DENSITY, LIQ_REPEAT_SKEW_OVERLAY),
1 `NEEDS_MORE_RESEARCH`, 4 `REJECTED`. Leçons : (i) 3 des 4 rejets venaient d'un déclustering
cross-symbole absent dans la découverte (surestimation 10-20× de N) ; (ii) 2 mécanismes se sont
reproduits avec le SIGNE OPPOSÉ à la réclamation ; (iii) 1 direction de rapport était inversée
(bug de labellisation) ; (iv) les ETA de reconfirmation forward sont souvent rédhibitoires
(9-46 ans) même pour des edges réels.

**Wave 2 valide tout ce qui a été trouvé et jamais validé indépendamment** : les candidats
PROMISING round 2/3 restants ET les 3 alphas déjà en shadow dont le seuil a été RECONSTRUIT
(data-snoopé) sans validation indépendante. Objectif : récupérer un maximum d'alphas réels —
ce qui veut dire autant de KILL honnêtes que de validations.

Lis d'abord, obligatoirement :
- `reports/edge_discovery/validation_2026-09/LIQ_REPEAT_DENSITY/REPORT.md` et
  `reports/edge_discovery/validation_2026-09/AMIHUD_ILLIQUIDITY_PREMIUM/REPORT.md` — le FORMAT
  et le NIVEAU d'exigence attendus (sections 1-7). Reproduis cette structure.
- `configs/validation_registry.yaml` (LECTURE SEULE) — verdicts wave 1, raisons de rejet,
  vocabulaire des statuts.
- `configs/live_alpha_registry.yaml` (LECTURE SEULE) — les 16 alphas déjà figés/en shadow, pour
  les checks de chevauchement.
- `reports/edge_discovery/alpha_hunt_2026-09-03_round4/BRIEFING.md` §1 (discipline
  scientifique) et §8 (discipline ressources) — s'appliquent intégralement ici, avec un
  DURCISSEMENT : **10 workers du round 4 tournent déjà en parallèle (autre session)**, le
  disque est à ~26 Go libres et le watchdog COUPE LES COLLECTEURS LIVE sous 20 Go. Donc :
  scratch ≤ 150 Mo par worker, `df -h /` avant CHAQUE écriture > 20 Mo, et si libre < 23 Go
  tu n'écris plus AUCUN intermédiaire (DuckDB streaming uniquement, résultats en JSON de
  quelques Ko). `SET memory_limit='1200MB'; SET threads=2;`. Ton scratch exclusif :
  `/tmp/claude-1000/-home-qbee-futur/df793692-b596-4e93-91e2-bc55f257c909/scratchpad/<Vn_...>/`.

## 1. Deux concepts stricts, jamais confondus

- `VALIDATED_FOR_FORWARD` = le gate historique/robustesse (§3) est passé par RÉIMPLÉMENTATION
  INDÉPENDANTE depuis la définition économique. Prêt à figer une spec.
- `FORWARD_CONFIRMED` = preuve obtenue APRÈS freeze sur données jamais vues. Aucune
  réimplémentation historique n'est une confirmation forward, même par un worker différent.

Booléen séparé, obligatoire : `confirmable_in_horizon` = ETA_conservative < 3 ans. Un candidat
peut être `VALIDATED_FOR_FORWARD` ET non confirmable dans l'horizon (précédent Amihud, 17 ans) —
on le dit, on ne le cache pas, et la recommandation s'adapte (survie signe/coût/mécanisme sur
plancher 6 mois plutôt que significativité fraîche).

## 2. Protocole d'indépendance — NON NÉGOCIABLE

1. Tu lis le REPORT.md de découverte pour comprendre la RÉCLAMATION (mécanisme économique,
   dataset, horizon, chiffres). Tu n'ouvres JAMAIS les scripts/evidence de la découverte
   (`evidence/`, `*.py` dans le dossier du worker de découverte, `sector_map.py`, etc.). Si tu
   en ouvres un par erreur, dis-le dans le rapport.
2. Pour les alphas déjà en shadow (V1, V2) : tu peux lire `freeze_spec.json` et l'entrée du
   registre live (LECTURE SEULE) pour savoir ce qui TOURNE, mais ta réimplémentation part de la
   définition économique, pas du code `src/institutional/engines/...`. Tu peux réutiliser
   l'infra de PRODUCTION figée en lecture (ex. `detector.py`/`dataset.py` pour comprendre la
   causalité des features, `universe.py::build_pit_eligibility_log()` pour l'éligibilité PIT) —
   à condition de le déclarer et de recomputer indépendamment au moins UNE feature clé et de
   comparer ligne à ligne (checklist §3.2).
3. `PREREGISTRATION.md` AVANT tout calcul de rendement : PRIMARY_SPEC figée (univers, feature,
   règle de seuil, horizon, direction, coût), ≤ 8 perturbations d'ancrage préenregistrées,
   unités de déclustering L1/L2/L3, critères de succès. **Pas de sauvetage de paramètre** : si la
   PRIMARY_SPEC échoue, elle a échoué ; les perturbations sont des tests de robustesse, pas une
   grille de recherche.
4. Si la réclamation ne publie pas de constante (seuil, fenêtre), tu choisis une RÈGLE (ex.
   percentile own-history causal, médiane sur la population éligible) préenregistrée, jamais
   une valeur choisie après avoir vu le résultat.

## 3. Le gate de validation

### 3.1 Résultats à produire (PRIMARY_SPEC + chaque perturbation)
`gross_bps`, `net_bps` (−14), `net_bps_stress28` (−28), `pf`, `n_raw`, `n_independent_L1`
(même symbole / 24 h ou horizon), `n_independent_L2` (jour calendaire tous symboles, ou date de
rebalancement), `n_independent_L3` (unité macro du mécanisme : épisode cross-symbole chaîné,
régime, semaine), `t_stat_declustered` (sur L3), `bootstrap_ci95` (block-bootstrap, blocs = L3),
`year_by_year`, `ex_best_year_net_bps`, `worst_episode_bps`, `max_drawdown_bps_cumule`.

### 3.2 Checklist de vérification (AVANT le premier chiffre de rendement)
- Causalité PIT de chaque feature : au moins une feature clé recomputée from scratch et comparée
  ligne à ligne à la source (reporter le nombre de mismatches).
- Restriction croissance d'univers / âge de listing (≥ 30 j) / renommages / délistages.
- Pièges data (§8.10 du briefing round 4).
- **Chevauchement avec les alphas live** : pour tout candidat de la famille cascade/OI,
  pourcentage d'événements partagés avec `LIQ_CASCADE_REPEAT_V1` / `SHORT_COVERING_CONTINUATION_V1`
  (lire leurs `decisions.parquet` en LECTURE SEULE) ; pour tout cross-sectionnel, corrélation de
  rang avec le momentum 7 j et l'Amihud, et corrélation des rendements de portefeuille.
- Test « bras A − bras B » sur la même population (jamais « A > 0 ») pour tout conditionnement.

### 3.3 Capacité
Dollar-volume 30 j moyen des jambes ; note explicite si non mesurable.

### 3.4 Fréquence, N_required, ETA
`historical_event_rate` (2 ans), `recent_event_rate` (6 mois), `conservative_event_rate` (min),
`n_required_statistical` (block-bootstrap, unilatéral α = 5 %, puissance 80 %, **edge haircuté
de 50 %**), `minimum_calendar_days` (plancher structurel : 182 j hebdo, 60 j événementiel),
`eta_p50`, `eta_conservative` (jours ET années), `confirmable_in_horizon` (< 3 ans).

### 3.5 Verdict (vocabulaire du registre)
`VALIDATED_FOR_FORWARD` / `REJECTED` / `NEEDS_MORE_RESEARCH` / `DATA_BLOCKED` /
`IMPLEMENTATION_BLOCKED`, + tag secondaire round-4 si utile (`UNCONFIRMABLE_IN_HORIZON`,
`COST_FRAGILE`, `REGIME_DEPENDENT`), + `sign_correction_required` (bool), +
`recommended_next_step` ∈ {`FREEZE_AND_LAUNCH_SHADOW`, `OVERLAY_ON_<alpha_id>`,
`UPGRADE_LIVE_STATUS` (pour un alpha shadow RECONSTRUCTED dont la réimplémentation confirme la
spec live), `DOWNGRADE_LIVE_STATUS`, `REJECT`, `MORE_RESEARCH`}.

Politique SHORT : pas de short directionnel standalone. Jambe short acceptable seulement comme
couverture d'un spread relative-value (précédent AMIHUD) — la jambe LONG seule est TOUJOURS
reportée séparément, avec son propre gate.

## 4. Livrables — `reports/edge_discovery/validation_2026-09/<CANDIDATE_ID>/`
1. `PREREGISTRATION.md`
2. `REPORT.md` (structure des rapports wave 1 : 1 Méthodologie, 2 Checklist, 3 Primary +
   perturbations, 4 Déclustering, 5 Capacité, 6 Fréquence/N_required/ETA, 7 Verdict)
3. `RESULTS.json` — UNE entrée par candidat, clés : `candidate_id`, `verdict`,
   `validated_for_forward`, `confirmable_in_horizon`, `sign_correction_required`,
   `discovery_net_bps`, `validation_net_bps`, `validation_net_bps_stress28`, `pf`, `n_raw`,
   `n_independent_L1`, `n_independent_L2`, `n_independent_L3`, `n_validation_independent` (= L3),
   `t_stat_declustered`, `bootstrap_ci95`, `year_by_year`, `ex_best_year_net_bps`,
   `historical_event_rate`, `recent_event_rate`, `conservative_event_rate`,
   `n_required_statistical`, `minimum_calendar_days`, `eta_p50`, `eta_conservative`,
   `capacity_note`, `overlap_with_existing_live`, `long_only_leg` (mêmes champs, si applicable),
   `validation_caveats`, `recommended_next_step`, `validation_report`.
4. `evidence/` — scripts ré-exécutables + JSON bruts.

Tu n'écris JAMAIS dans `configs/validation_registry.yaml` (le coordinateur l'enrichit depuis
ton RESULTS.json) ni dans `configs/live_alpha_registry.yaml`, ni dans `reports/live_alpha_lab/`.
Rien n'est lancé en live/shadow par toi.

Rapport final au coordinateur (ta dernière réponse) : ≤ 40 lignes — table candidat × verdict ×
net14/net28 × N_indep_L3 × ETA × next_step, chemins, bugs/inversions trouvés, incidents.

## 5. Assignations wave 2

### V1 — `SHORT_COVERING_CONTINUATION` (alpha shadow RECONSTRUCTED, jamais validé)
Réclamation : round 2 W2, `reports/edge_discovery/alpha_hunt_2026-08-30/w2_liquidation_leverage/REPORT.md`
(row 2 : prix↑ + OI↓ vs baseline, fwd_4h, excess +9,2 full / +19,0 OOS, t 5,5/4,7, frozen-50).
Live : `reports/live_alpha_lab/SHORT_COVERING_CONTINUATION_V1/freeze_spec.json` (décile
symétrique 0,90/0,10 causal 30 j reconstruit, script original perdu — `scientific_status:
RECONSTRUCTED`), ledger `decisions.parquet` (LECTURE SEULE, 100+ décisions FORWARD réelles).
Tâches : réimplémentation indépendante ; comparer (a) à la réclamation, (b) à la spec live
(la classification reconstruite porte-t-elle l'edge ?), (c) taux d'accord décision-par-décision
entre ta classification et le ledger REPLAY. C'est l'alpha à la plus haute fréquence du projet →
l'ETA le plus prometteur : calcule-le avec le plus grand soin. Next_step attendu :
UPGRADE / DOWNGRADE_LIVE_STATUS.

### V2 — `LIQ_CASCADE_FAR_FROM_LOW` (shadow RECONSTRUCTED) + `BTC_LEAD_ALT_CASCADE` (round 3 W1 a12)
Réclamations : round 2 W2 (row 4 : « far from local low » beats « at the low », liq_cascade_dataset,
fwd_4h, +15,5→+73,3 OOS) ; round 3 `w1_event_sequences/REPORT.md` w1_a12 (choc BTC précède/
co-occurre avec cascade alt, N_indep 3 097, net14 +33,0 / net28 +19,0, pos 5/6). NE PAS ouvrir
`w1_event_sequences/evidence/`. Live FAR_FROM_LOW : seuil `dist_low_24h ≥ 0,05` reconstruit
(75e centile) — préenregistre ta PROPRE règle de seuil. Chevauchement avec
`LIQ_CASCADE_REPEAT_V1` OBLIGATOIRE pour les deux. Optionnel si temps : W1 c08
CROWD_WASHOUT_ESCALATION_CHAIN, c03 PREMIUM_EXTREME_THEN_CASCADE_CHAIN (COST_FRAGILE au
round 3 — verdict au coût 14/28 standard uniquement).

### V3 — `XSEC_MOMENTUM_HORIZON_EXTENSION` (14D_LO, 30D_LO, 14D_LS) + `XSEC_RESIDUAL_MOMENTUM_14D`
Réclamation : round 3 `w2_cross_sectional/REPORT.md` (XSEC_MOM_14D_LO +199,3 net, N_indep 167 ;
XSEC_MOM_30D_LO +462,8, N_indep 78 ; XSEC_MOM_14D_LS +59,5 ; XSEC_RESID_MOM_14D +64,8, 167,
beta-BTC strippé). Data : panel PIT `/home/qbee/futur-data-v2/data_v2/normalized/perp_ohlcv`
+ éligibilité `data/listings_backfill/binance/listings_calendar.parquet`. Connu : 7 j→7 j +89
(shadow V1/V2), Amihud validé +105,7 (ETA 17 ans). OBLIGATOIRE : corrélation de rang et de
rendement avec xsmom 7 j et Amihud (même facteur ou non ?), coût sur turnover réel
(rebalancement 14 j/30 j = turnover plus faible : dis-le), plancher calendaire
(minimum_calendar_days ≥ 182/365), jambe long-only reportée séparément pour le LS.

### V4 — `XSEC_RELATIVE_LEVERAGE_14D` (round 3 W2) + `CROSS_ASSET_OI_BUILDUP_FADE` (round 3 W3)
Réclamations : `w2_cross_sectional/REPORT.md` XSEC_REL_LEVERAGE_14D (proxy levier OI/vol 30 j,
rang → 14 j fwd, long high/short low, +73,3 net, N_indep 121, 5/6 ans) ; `w3_relative_value/
REPORT.md` D-OICHGRANK-G_fade-7D (rang variation OI 7 j, FADE, +32,5 net, N_indep 241, 4/6).
Data : data_v2 perp_ohlcv + OI (`binance_vision_metrics` / event_feature_panel). OBLIGATOIRE :
corrélation entre les deux rangs (même facteur ?), avec xsmom 7 j et Amihud ; jambe long-only
séparée ; capacité.

### V5 — `SECTOR_RELATIVE_STRENGTH_REVERSAL` + `SECTOR_ROTATION` (round 3 W3)
Réclamations : `w3_relative_value/REPORT.md` D-RVSECTOR-C_rev-7D (rang vs_sector_7d dans un
univers taggé par secteur, REVERSAL, +46,4 net, N_indep 324, 5/7, capacité MED $268M) ;
D-SECTOR-ROTATION-D_mom-7D (10 paniers sectoriels rangés par rendement 7 j, continuation,
tiers haut/bas, +103,0 net, N_indep 259, 6/6, caveat low-df). Tu construis ta PROPRE carte
sectorielle (documentée dans PREREGISTRATION.md, jamais lue depuis un `sector_map.py`
existant). Sensibilité à la carte = perturbation obligatoire. Jambe long-only séparée.

### V6 — `BASIS_RICHENING_FADE` (round 3 W3) + `BASIS_FUNDING_AGREEMENT_FADE` (round 2 W9)
Réclamations : `w3_relative_value/REPORT.md` D-BASISMOTION-RANK-J2_fade-7D (rang
basis_z_1d − basis_z_7d = VÉLOCITÉ de basis, fade, +29,7 net, N_indep 324, 5/7, capacité HIGH) ;
round 2 `w9_cross_dataset_interactions/REPORT.md` (basis + funding qui s'ACCORDENT → +18,5 à
+20,6 bps/1 j vs −1,3 à −1,7 quand ils divergent). Data : `data_v2/normalized/basis`,
`derivatives_backfill/{binance_vision_premium,binance_vision_metrics}`, funding. Connu :
funding/basis NIVEAU mean-reversion arbitré 2025-26 → survie 2025-26 EXPLICITE exigée ;
distinct de FUNDING_BASIS_DISAGREEMENT_V2 (quarterly BTC/ETH) — vérifie le chevauchement.

### V7 — `XSMOM_REGIME_META` (round 3 W5 T2.1/T2.3/T2.5, W4 B8) + `FUNDING_CARRY_X_DISPERSION` (W4 B1)
Réclamations : `w5_meta_signals/REPORT.md` (ENABLE xsmom par vol BTC 20 j, delta +179 ; par
breadth, delta +578 ; SELECT_HORIZON 7/14 j par vol, +116 — N = 124-131 hebdo) ;
`w4_regime_conditional/REPORT.md` B8 (xsmom × vol : +63,8 low-vol vs −48,7 high-vol), B1 (carry
funding × dispersion, +46,4 low-disp, N_indep 117, 4/5). NE PAS ouvrir `w5_meta_signals/*.py`
ni `evidence/`. Leçon wave 1 (LIQ_REPEAT_VOL_GATE) : un régime de vol est un état macro LENT →
L3 = épisode de régime, N s'effondre 3-10×. Validation d'un méta-signal = bras A − bras B sur la
même population, déclusterisé par épisode de régime, bootstrap par bloc. Ta propre
réimplémentation du signal de base (xsmom hebdo top-quintile).

### V8 — `OPTIONS_IV_SHOCK_MEMORY` (round 3 W7-033/034/035) + `SPILLOVER_X_DVOL_STRESS` (W8-17a/b)
Réclamations : `w7_event_memory_generalization/REPORT.md` (choc DVOL up × mémoire : N = 30-51,
+400 à +570 bps, PF outlier-driven, PROMISING-WITH-CAVEAT, famille options nouvelle) ;
`w8_cross_asset_interactions/REPORT.md` W8-17a/b (DVOL × continuation de spillover, low-DVOL
t = 3,36 mais 2025-26 négatif). NE PAS ouvrir `w8_cross_asset_interactions/evidence/`. Data :
`data/options_backfill/deribit` (DVOL BTC/ETH, trades), `data/events/spillover_dataset.parquet`.
N mince → `DATA_LIMITED`/`NEEDS_MORE_RESEARCH` probable : pousse quand même jusqu'au verdict,
avec ETA honnête, et dis exactement ce qu'il faudrait pour trancher.
