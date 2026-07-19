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

---

## Phase 30-34 — Live writer safe + full-cycle regime (2026-06-28, branche fix/datastore-recovery)

**Atomic write (Phase 30-32) :** `src/institutional/data/atomic_parquet.py` (lock flock +
temp + os.replace + fsync + validation ; quarantaine si existant corrompu, jamais d'écrasement).
Câblé dans `live_data_update.py` (remplace le `to_parquet` direct, cause de corruption).
Tests `tests/test_atomic_parquet.py` : **8/8** (crash-avant-replace, temp invalide, corrupt-jamais-écrasé,
4 writers concurrents, vide refusé). Store **10/10 PASS sous service ACTIF** → `LIVE_WRITE_PATH: SAFE`.

**Full-cycle regime report (Phase 34)** `reports/portfolio_full_cycle_after_datastore_recovery.json` :

| Année | Régime | ROI | PF | Verdict |
|---|---|---:|---:|---|
| 2022 | bear | −20.8% | 0.80 | LOSS |
| 2023 | recovery | +0.7% | 1.01 | FLAT |
| 2024 | bull | **+6.3%** | 1.08 | **WIN** |
| 2025 | mixed | −13.9% | 0.83 | LOSS |
| 2026 | hostile | −5.6% | 0.84 | LOSS |

→ **Long-only RÉGIME-DÉPENDANT** (gagne en bull, saigne en bear/mixed/hostile). Pas mort, mais
incapable seul. Carry = seul moteur positif full-cycle. **Hedge + carry obligatoires.**
⚠ Governor conservative_v1 = **ratchet monotone** sur multi-année (se fige en cash après 1er DD) →
le Hedge Governor doit utiliser un DD en fenêtre glissante / ré-armement par régime.

---

## Phase 35-36 — Hedge Governor V1 + Carry V2 (2026-06-28)

**Hedge Governor V1** `src/institutional/risk/hedge_governor.py` (PAPER_ONLY) :
assurance de portefeuille (≠ short alpha). États NO_HEDGE/REDUCE_LONGS/BTC|ETH_PARTIAL_HEDGE/
CASH_ONLY/KILL. **DD en fenêtre glissante** (corrige le ratchet monotone). Garde-fous
SHORT_DIRECTIONAL=False, NAKED_SHORT=False, HEDGE_SHORT=True ; sizing borné
(min(beta_adj_long×ratio, 30%capital, long_exposure)) ; hedge interdit si long_exposure=0.
Tests `tests/test_hedge_governor.py` : 9/9. Intégration backtester (jambes SHORT_HEDGE) = à câbler.

**Carry V2 delta-neutral** `scripts/backtest_carry_basis.py` — long spot + short perp, récolte
funding (aucun short nu, aucun pari de prix). Backtest 2022-2026 :

| Asset | ret total | ret/mo méd | maxDD | PF | gate |
|---|---:|---:|---:|---:|---|
| **BTCUSDT** | +28.6% | **+0.37%** | **−0.4%** | 9.07 | **PASS** |
| ETHUSDT | +25.4% | +0.34% | −1.9% | 4.26 | FAIL (DD) |
| SOLUSDT | −7.1% | +0.14% | −20% | 0.80 | FAIL |
| BNBUSDT | −26% | −0.33% | −26% | 0.26 | FAIL |

→ **Premier edge propre de tout l'audit : BTC funding carry PASS** (non-directionnel,
+0.37%/mo, DD 0.4%). ETH proche. **Mais stress funding-flip (−1σ) le casse** (BTC → −0.35%/mo)
→ carry doit être **gated par le régime de funding**, pas always-on aveugle. Alts non viables.

**Architecture confirmée** : long-only (opportuniste bull) + carry BTC/ETH (base rendement
non-directionnel, régime-gated) + hedge governor (survie bear) + cash. Pas "plus de longs".

## Statut officiel mis à jour

```
LIVE_WRITE_PATH       : SAFE (atomic, 10/10 sous service actif)
DATASTORE             : CLEAN (9/9, DOT dropped)
TRM_V4                : PAPER_REGIME_DEPENDENT (gagne 2024 bull)
TRM_V5                : HISTORICAL_ONLY_MISSING_ARTIFACT
PULLBACK/LIQ/XS       : SHADOW (négatifs hors bull)
CARRY_V2_BTC          : GATE_PASS (delta-neutral, à gater funding-regime)
HEDGE_GOVERNOR_V1     : PAPER_ONLY (module + tests, intégration backtester à faire)
LIVE / MICRO_LIVE     : DISABLED
```

---

## Doctrine accumulation (2026-06-28, tag v0.24)

**Règle structurelle prouvée** : *un signal utile localement peut être destructeur en exécution
portefeuille* (exit engine, forced-exit régime, CARRY_GATE_V2 — 3 occurrences). Tout signal doit
passer 2 niveaux : (1) relation statistique locale, (2) amélioration portefeuille APRÈS fees/churn/
holding/exits/DD. Signal valide ≠ moteur valide.

```
V1.1_CARRY50_OLD_GATE      : CONFIRMED_BASELINE (+4.8%/an, DD 1.9%, 3.6y) — figé v0.23, paper-live actif
CARRY_GATE_V2 (exécution)  : REJECTED_PORTFOLIO (churn/fees) — désactivé par défaut
CARRY_GATE_V2 (feature)    : KEPT_AS_RESEARCH_FEATURE (contexte/stress, pas de gate exécution)
CROSS_EXCHANGE_DIRECTIONAL : REJECTED
CROSS_EXCHANGE_STRESS      : KEPT_AS_RISK_FEATURE
FUNDING_REFINEMENT         : STOPPED (plafond ~5%/an à DD bas)
DERIVATIVES_COLLECTOR      : ACTIVE_24/7 (fix reconnect-churn : calme ≠ déconnexion)
LIQUIDATION_EVENT_PIPELINE : BUILT (catalogue + forward labels) — DATA_NOT_READY (0 events, calme)
LIQUIDATION_EVENT_ENGINE   : DATA_ACCUMULATION_PHASE (seuils : 100 diag / 300 train / 1000 robuste)
MICRO_LIVE / TARGET_40_80K : DISABLED / DATA_GATED
```

**Pipeline accumulation productive (sans overfit)** : `events/live_event_builder.py` +
`scripts/build_live_liquidation_events.py` + `report_liquidation_event_inventory.py` (verdict
DATA_NOT_READY / EVENT_DIAGNOSTIC_READY / ENGINE_TRAINING_READY). Inventaire hebdo. **Aucun
modèle avant ≥100 events.** Le prochain progrès = 1er moteur événementiel qui passe ses gates sur
de vraies liquidations accumulées. `reports/V1.1_BASELINE.md`.
