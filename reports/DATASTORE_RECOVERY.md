# Datastore Recovery — Phase 23-24 (2026-06-28)

**Statut : DATASTORE_RECOVERY_PASS** (branche `fix/datastore-recovery`)

## Cause racine de la corruption

Le service `futur-api` (scheduler horaire) lance `live_data_update.py` qui
**appende aux parquets enriched pendant qu'ils sont lus/écrits**. Une écriture
parquet interrompue (restart API, écriture concurrente) laisse un fichier sans
magic bytes → corruption. Preuve : pendant la validation, LINK est passé
transitoirement à "0 lignes / corrompu" puis est redevenu lisible après le cycle.

→ **Fix requis (non fait, hors scope branche)** : écriture atomique
(`to_parquet` vers tmp + `os.replace`) dans `live_data_update.py`, sinon la
corruption reviendra. La recovery a été faite **service arrêté** pour un snapshot stable.

## Pipeline & sources (discovery)

- Pipeline canonique : `compute_enriched_ohlcv_features` (MTF + sequence ON).
- Rebuild offline reproductible : `scripts/rebuild_enriched_from_origin.py`
  (réutilise le loader de `assemble_enriched_from_dataout`, source
  `data_out/result/{year}_{SYM}_features.parquet`, flags canoniques).
- Référence schéma = ADAUSDT (alt valide, 4060 cols).

## Résultats

| Asset | avant | action | après | validation |
|---|---|---|---|---|
| BNBUSDT | corrompu | rebuild offline | 55,922 × 4141 | PASS |
| AVAXUSDT | corrompu | rebuild offline | 50,499 × 4139 | PASS |
| LINKUSDT | corrompu | rebuild offline | 56,498 × 4141 | PASS |
| DOTUSDT | corrompu | **aucune source raw** | — | **DROPPED (univers)** |
| BTC/ETH/SOL | gap pré-2020 | aucune (warning) | inchangé | PASS (gap hors fenêtre) |

- Store : **9/9 PASS** (`validate_parquet_store.py`).
- Distribution check vs ADA : BNB/AVAX/LINK **PASS** (0-2 cols non-MTF manquantes,
  ≤1 feature plate candidat-only, 0 inf). Deltas MTF lag-variants documentés.
- Data registry hashé : `artifacts/data_registry/enriched_store.yaml` (9 PASS, 1 DROPPED).
- Features critiques moteurs : présentes, non plates, NaN faible.

## Re-run scientifique (Phase 24) — AUCUN changement modèle

Ablation 2026 OOS, store cassé vs store propre :

| Run | ROI avant | ROI après | maxDD après |
|---|---:|---:|---:|
| G_all_raw | −26.3% | **−35.0%** | −40.4% |
| H_all_alloc | −7.0% | −5.8% | −9.1% |
| J_all_full | −2.4% | −2.1% | −3.0% |

Autopsy (PF A_TRADE 2026, store propre) : CARRY 0.60 · CROSS_SECTIONAL 0.74 ·
LIQUIDATION 0.79 · PULLBACK 0.72 · TRM 0.01 (n=5). **Tous < 1.**

## Verdict (Cas A du brief)

Ajouter les actifs propres a rendu l'alpha brut **PIRE** (−26% → −35%), pas
meilleur. → **Les moteurs n'étaient pas affamés par un store cassé : ils sont
réellement négatifs en long-only sur 2026.** La donnée corrompue ne masquait
aucun alpha caché.

**Conséquence** : le prochain chantier n'est pas un nouveau moteur long. C'est
la dimension manquante — **hedge encadré + carry delta-neutral** (Phases 26-27),
et/ou élargir la fenêtre d'éval pour inclure des régimes haussiers. Long-only
2026 ne peut pas atteindre 3-5%/mois ; le governor (conservative_v1) tient le DD
≤ 3% (survie), pas le rendement.
