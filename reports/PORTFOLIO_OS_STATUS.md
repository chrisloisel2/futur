# Portfolio OS — Statut officiel (red-team alpha, 2026-06-28)

> Diagnostic : **infrastructure réussie, inventaire d'alpha exécutable insuffisant.**
> Le risk layer réduit les dégâts ; il ne crée pas d'alpha. Aucun passage live autorisé.

## Reproducibility lock (Phase 15)

| Contrôle | Résultat |
|---|---|
| Parquet store `data/enriched` (10 fichiers) | **7 FAIL / 3 OK** — `validate_parquet_store.py` |
| Fichiers CORROMPUS (magic bytes) | **AVAXUSDT, BNBUSDT, DOTUSDT, LINKUSDT** (0 ligne) |
| Gaps géants | BTC 6018h, ETH 7928h → **pré-2020 uniquement**, fenêtre 2024-2026 contiguë (OK) ; SOL 73h (2022) |
| Model registry | 56 entrées, **55 exécutables**, 0 cassée — `artifacts/model_registry/models.yaml` (sha256) |
| **TRM v5** | **MISSING_ARTIFACT — executable=false** (aucun .pkl/.joblib v5 trouvé) |

## Règle absolue

Aucun chiffre de perf cité sans artefact hashé **chargeable** (`executable: true`).
→ Le **+5.88%/mois v5 est un résultat HISTORIQUE non reproductible**. Le moteur
exécutable réel est `TRMFleetLongV4`.

## Ablation 2026 OOS (Phase 18) — `reports/ablation_2026_oos.json`

| Run | ROI | PF | trades | maxDD |
|---|---:|---:|---:|---:|
| G all_raw | **−26.3%** | 0.77 | 627 | −34.3% |
| H +allocator | −7.0% | 0.77 | 369 | −9.5% |
| I +exit | **−16.5%** | 0.66 | 1013 | −17.8% |
| J +governor (full) | −2.4% | 0.65 | 147 | −3.5% |
| **J' governor durci, SANS exit** | **−0.1%** | 0.98 | 50 | **−2.2% ✓** |

- **+allocator** : +19.3 pts (filtre l'utility) — utile.
- **+exit** : **−9.5 pts (churn destructeur)** → exit engine retiré du stack par défaut.
- **+governor** : +14.1 pts (damage control) — utile.
- **governor durci (kill 2.5%)** : DD passe enfin sous 3% (2.2%).

## Alpha autopsy (Phase 17) — `reports/alpha_autopsy_2026_oos.md`

**FAUTE D'ALPHA** : l'alpha brut est déjà négatif. Sur 2026 OOS, **TOUS les moteurs
long sont négatifs** (PF<1) — 5 mois/6 hostiles au long-only, SHORT interdit.

| Moteur | n A (2026) | PF | PnL moy | verdict |
|---|---:|---:|---:|---|
| CARRY_BASIS | 2465 | 0.61 | −0.61% | KILL/REBUILD |
| CROSS_SECTIONAL | 8106 | 0.79 | −0.27% | bloqué data |
| LIQUIDATION | 466 | 0.79 | −0.13% | REBUILD event-first |
| **PULLBACK** | 168 | **0.72** | −0.44% | **ne généralise PAS à 2026** |
| TRM v4 | 5 | 0.01 | −2.18% | trop rare |

⚠ **Correction** : Pullback PF 1.49 / P(PF>1.3)=0.91 venait de 2024-2025. Sur 2026
pur OOS, PF=0.72. La promotion PAPER était prématurée.

## Statut officiel des moteurs

```
PORTFOLIO_OS         : BUILT / PAPER_FRAMEWORK
TRM_V4               : PAPER (exécutable mais ~0.9 trade/mois)
TRM_V5               : MISSING_ARTIFACT / NOT_EXECUTABLE
PULLBACK_LONG        : SHADOW (échoue 2026 OOS — v2 requis, ne pas promouvoir)
LIQUIDATION_REBOUND  : SHADOW_REBUILD_REQUIRED (event-first + vrai feed liq/OI)
CARRY_BASIS          : SHADOW_REBUILD_REQUIRED (rendement de portage, pas directionnel)
CROSS_SECTIONAL_LONG : BLOCKED_BY_DATA_REPAIR (AVAX/BNB/DOT/LINK corrompus)
EXIT_ENGINE          : SHADOW_ATTACHED (retiré du stack — churn, audit requis)
RISK_GOVERNOR        : ACTIVE_PAPER_ONLY (conservative_v1, kill 2.5%, DD gate ✓)
LIVE_TRADING         : DISABLED
MICRO_LIVE           : DISABLED (portefeuille OOS négatif)
```

## Interdits (rappel)

Citer v5 sans artefact · promouvoir sur AUC · garder un moteur négatif "pour la diversification" ·
optimiser des seuils avant de comprendre les pertes · micro-live tant que le portefeuille OOS est négatif ·
sculpter le governor sur une seule période · ignorer les parquets corrompus.

## Prochaines actions (ordre)

1. **Réparer** AVAX/BNB/DOT/LINK enriched (régénérer depuis raw) puis re-valider.
2. **Pullback v2** : ne le retenir que s'il survit 2026 OOS (PF≥1.25, cost×2≥1.05, multi-régime).
3. **Liquidation event-first** : nécessite un vrai feed liquidations/OI (absent d'enriched).
4. **Carry** : backtest portage (funding reçu/payé, basis, coût hedge, gap) — pas directionnel.
5. Étendre la fenêtre d'éval (inclure des régimes haussiers) avant tout verdict définitif sur un moteur.
