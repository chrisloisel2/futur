# Derivatives Acquisition Platform — Semaine 1 (2026-06-28)

Objectif : un **radar de stress/levier/liquidations/flux**, pas un simple collecteur.
Architecture 3 couches : RAW DATA LAKE → FEATURE/EVENT LAKE → ALPHA SIGNAL LAKE.

## CONSTRUIT & DÉPLOYÉ (Semaine 1 — DERIVATIVES_LIVE_COLLECTION_PASS)

- **Collecteur production** `src/institutional/data/derivatives_collector/` :
  - WS `!forceOrder@arr` → **liquidations** (avg_price, filled_qty, usd, side) — donnée introuvable en historique.
  - REST poll (300s) → openInterest, premiumIndex (mark/index/funding/next_funding), takerlongshortRatio, globalLongShortRatio.
  - Writer **append-only immutable** + **manifest par partition** (sha256, schema_sha256, rows, start/end ts,
    latency p50/p99, collector_version, validation_status). Écriture atomique.
  - recv_time + latency_ms sur chaque record. Reconnect WS exponentiel.
- **Store RAW** `data/derivatives_raw/exchange=binance/market=usdm/stream=*/symbol=*/date=*/part-*.parquet`.
- **Validation 3 niveaux** (`validate_derivatives_store.py`) : technique (magic/schema/monotone/dup),
  marché (prix>0, OI≥0, funding borné), temporel (latence p50/p99, recv≥event). Rapport quotidien
  `reports/DERIVATIVES_DAILY_<date>.md`. Registry `artifacts/data_registry/derivatives_raw_store.yaml`.
- **Déployé en systemd** : `futur-derivatives.service` (9 actifs, Restart=always) — **collecte 24/7 ACTIVE**.
  Démo : parts écrites, 0 corrompu, manifests OK, gate PASS. 90/90 tests (writer+manifest+OI detector).

## Univers (strict, 9 actifs)
BTC ETH SOL BNB XRP DOGE ADA AVAX LINK. Extension seulement après 30j sans trou, coverage >98%.

## STAGÉ — gated par l'accumulation de données (PAS construit en mock)

Honnêteté (règle "ne pas inventer d'historique, ne pas bâtir de scaffolding vide") : les couches
suivantes traitent des données qui **n'existent pas encore** (le collecteur vient de démarrer ; zéro
liquidation historique). Les construire maintenant = pipelines vides. Elles seront bâties **quand la
donnée existe** :

| Couche | Statut | Débloquée par |
|---|---|---|
| Feature lake (OI/funding/liquidation/taker/basis/liquidity) | STAGED | ~jours de collecte (OI/funding calculables déjà ; liq/taker/depth à accumuler) |
| Néo-signaux (liq_pressure, oi_flush, crowding, funding_stress, taker_exhaustion, liquidity_vacuum, reflexivity, deleveraging_rebound) | STAGED | feature lake |
| Event lake (flush/deleveraging/squeeze/vacuum/dislocation) | STAGED | feature lake + semaines de liquidations |
| IA (anomaly/clustering HDBSCAN/weak-label/ranker) | STAGED | event lake (≥ centaines d'events) |
| Liquidation Event-First engine | STAGED | feed liquidations accumulé OU achat historique |

## Preuve déjà acquise (test rapide)
`BTC_OI_DELEVERAGING` (proxy OI, sans feed liquidations) = **NO EDGE** (PF≤1.01, cf.
`DERIVATIVES_AND_OI_ENGINE_REPORT.md`) → confirme que le **vrai feed liquidations** (maintenant
collecté) est nécessaire. Le raccourci proxy ne marche pas.

## Prochaines étapes (ordre du plan, honnête)
1. **Laisser tourner le collecteur** (déjà actif) — chaque jour acquis. Monitorer `DERIVATIVES_DAILY_*`.
2. Après ~1-2 semaines : `build_derivatives_features.py` + néo-signaux V1 sur données réelles accumulées.
3. Après ~1 mois : event lake + IA clustering + PnL par cluster (NEO_SIGNAL_DISCOVERY).
4. Liquidation Event-First quand assez d'events (ou achat historique pour valider plus vite).
5. En parallèle, socle paper : V1.1 carry 50% (~3.6%/an).

**40-80K/an reste data-gated** : il dépend de l'accumulation (6-12 mois) ou de l'achat d'historique
liquidations multi-actifs, puis de la validation des moteurs offensifs. Le radar est en place ; il
construit l'avantage informationnel jour après jour.
