# WAVE 2 — SCOREBOARD DE VALIDATION

Généré : 2026-09-03 · worker unique (session futur-49) · harnais `_lib/validation_lib.py`

Convention : `net` = coût nominal du mécanisme (14 bps une jambe, 28 bps long-short) ;
`net28` = coût doublé (stress). `t` = t cluster-robuste sur l'unité L3, choisie d'après la
densité de la population : mois calendaire pour les cross-sectionnels, épisode cross-symbole
chaîné < 4 h pour les événements rares, **jour calendaire** pour les panels de barres denses
(`SHORT_COVERING_CONTINUATION` — voir la leçon de déclustering plus bas).

| candidat | famille | verdict | découverte net | validation net | net stress | t_L3 | N_L3 | ETA conservateur | next_step |
|---|---|---|---|---|---|---|---|---|---|
| BTC_LEAD_ALT_CASCADE | liquidation | VALIDATED_FOR_FORWARD | 33.0 | 46.87 | 32.87 | 3.315 | 259 | 3549 days (~9.7 years) | FREEZE_AND_LAUNCH_SHADOW |
| LIQ_CASCADE_FAR_FROM_LOW | liquidation | REJECTED | 15.5 | -6.76 | -20.76 | -1.192 | 1620 | unbounded | DOWNGRADE_LIVE_STATUS |
| XSEC_RESIDUAL_MOMENTUM_14D | cross_sectional | REJECTED | 64.8 | 31.5 | 17.5 | 0.626 | 77 | 145047 days (~397.1 years) | REJECT |
| XSEC_MOMENTUM_HORIZON_EXTENSION | cross_sectional | REJECTED | 199.3 | 51.78 | 37.78 | 0.85 | 77 | 78698 days (~215.5 years) | REJECT |
| SECTOR_ROTATION | relative_value | REJECTED | 103.0 | 13.29 | -0.71 | 0.807 | 74 | 83299 days (~228.1 years) | REJECT |
| SECTOR_RELATIVE_STRENGTH_REVERSAL | relative_value | REJECTED | 46.4 | -21.72 | -35.72 | -1.327 | 74 | unbounded | REJECT |
| OI_COLLAPSE_BOUNCE | liquidation | NEEDS_MORE_RESEARCH | 247.0 | 18.31 | 4.31 | 2.744 | 833 | 5103 days (~14.0 years) | MORE_RESEARCH |
| CVD_SHOCK_DOWN_MEMORY | liquidation | REJECTED | 15.5 | -0.02 | -14.02 | -0.003 | 1133 | unbounded | REJECT |
| PREMIUM_EXTREME_THEN_CASCADE | liquidation | NEEDS_MORE_RESEARCH | 12.1 | 19.69 | 5.69 | 1.455 | 533 | 13982 days (~38.3 years) | MORE_RESEARCH |
| CROWD_WASHOUT_NO_CASCADE | positioning | REJECTED | 10.6 | -6.35 | -20.35 | -1.05 | 1050 | unbounded | REJECT |
| SHORT_COVERING_CONTINUATION | liquidation | NEEDS_MORE_RESEARCH | 9.2 (excess) | **+17.06 (excess)** / +2.53 (produit) | -11.47 | 2.97 | 1582 | non défini | MORE_RESEARCH |

## Lecture

**1 validé sur 11 testés.** `BTC_LEAD_ALT_CASCADE` passe les 5 critères préenregistrés dans les
deux conventions de pondération, avec un contrôle de direction (choc BTC baissier vs haussier)
qui va dans le sens du mécanisme économique.

**Le motif de rejet dominant est le même que celui de la wave 1, et il s'aggrave d'un cran.**
La wave 1 avait établi que le déclustering cross-symbole manquant surestime N de 10-20×. La
wave 2 montre que pour la famille cascade, **la pondération elle-même peut changer le signe** :
`LIQ_CASCADE_FAR_FROM_LOW` est positif par événement et négatif par épisode. La statistique
retenue désormais par défaut est donc **moyenne pondérée par événement + erreur-type
cluster-robuste** — elle conserve l'estimateur de P&L qu'un trader reconnaît tout en donnant
une significativité honnête, et rend le désaccord de convention visible au lieu de le masquer.

**Trois réclamations mesuraient un rendement contre zéro plutôt que contre leur propre univers.**
`XSEC_MOMENTUM_HORIZON_EXTENSION` (+254 raw vs +51.8 excess), `SECTOR_ROTATION` (+89.8 raw vs
+13.3 excess) et `XSEC_RESIDUAL_MOMENTUM_14D` (+225 raw vs +31.5 excess) reproduisent leur
chiffre publié en brut mais s'effondrent dès que le bras B (l'univers éligible équipondéré) est
soustrait. Le test « bras A − bras B, jamais A > 0 » du briefing est ce qui les sépare.

**Deux inversions de signe confirmées** : `SECTOR_RELATIVE_STRENGTH_REVERSAL` (−21.7 vs +46.4
réclamé) et `CVD_SHOCK_DOWN_MEMORY` (contraste A−B à −19.4, Welch −2.66).

**Un alpha live est touché.** `LIQ_CASCADE_FAR_FROM_LOW_V1` tourne en `SIGNAL_SHADOW` sur une
spec dont la preuve ne tient pas une fois la corrélation intra-cascade corrigée →
`DOWNGRADE_LIVE_STATUS` recommandé. À noter : la réimplémentation **reproduit exactement** le
chiffre du freeze_spec, il n'y a aucun bug d'implémentation — le désaccord est purement inférentiel.

**Le seul mécanisme qui se reproduit proprement à part le candidat validé est
`SHORT_COVERING_CONTINUATION`** — et il illustre une distinction qui manquait au vocabulaire du
registre : *mécanisme confirmé* ≠ *produit tradeable*. Son excess vs baseline (+17,06 bps) est
significatif sur les **trois** unités de cluster testées (jour t=2,97 · semaine t=3,22 ·
mois t=2,94), 4/5 années positives, les trois dernières les plus fortes. Mais le bras long seul
rend +2,53 bps pour 14 bps de coût (t=0,41) et −11,47 au stress. Le signal est réel **en
relatif** ; le produit long autonome ne couvre pas ses frais. Sa place est en overlay/filtre,
pas en long directionnel — et son statut live reste `SIGNAL_SHADOW`, ni relevé ni abaissé.

**Leçon de déclustering, deuxième couche.** L'unité L3 préenregistrée pour ce candidat (épisode
cross-symbole chaîné < 4 h, héritée de la famille cascade) s'est révélée **dégénérée** sur un
panel de barres denses : 22 330 signaux se chaînent en 5 clusters. L'unité de déclustering doit
être choisie d'après la **densité de la population** (événements rares → épisode chaîné ; barres
denses → jour/semaine calendaire), jamais copiée d'une autre famille. L'écart au prereg est
déclaré dans le rapport du candidat.

## Ce qui n'a pas été testé dans cette vague

| # liste mission | candidat | raison |
|---|---|---|
| 7 | `XSEC_RELATIVE_LEVERAGE_14D` | nécessite le panel OI notionnel (`binance_vision_metrics`, 629 fichiers) — non assemblé |
| 9 | funding vs quarterly disagreement 30D | données `binance_vision_quarterly` disponibles, non branchées |
| 14 | DVOL shock memory | `options_backfill/deribit` disponible, non branché |
| 15-17 | overlays options RV/IV, far-OTM put, block flow | déjà `ALREADY_LIVE`, hors périmètre de re-validation ici |
| 20 | LIQ vol regime gate | déjà tranché en wave 1 (`NEEDS_MORE_RESEARCH`, ETA 28-38 ans) |

Le harnais (`_lib/`) couvre déjà ces familles : il ne manque que le branchement des sources.
