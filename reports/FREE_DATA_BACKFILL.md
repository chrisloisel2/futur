# Free Data Backfill — Binance (2026-06-28)

Stratégie : **acquisition gratuite via APIs publiques documentées** (hashable, reproductible,
légal — aucun scraping anti-bot). L'edge gratuit le plus sérieux = **divergences cross-exchange**,
même sans historique de liquidations.

## CONSTRUIT : `scripts/backfill_binance_derivatives_free.py` → BINANCE_FREE_BACKFILL 9/9

| Donnée | Résultat |
|---|---|
| **fundingRate (multi-an, paginé)** | **9 actifs × ~6000 pts, 2021-01 → 2026-06 (5.5 ans)** ✓ |
| openInterestHist (1h) | 9 actifs × 500 pts (**dernier mois only**, limite Binance) |
| liquidations historiques | **NON** (indisponibles gratuitement) |

Sortie : `data/derivatives_backfill/binance/{funding,open_interest_hist}/<SYM>.parquet` (atomique)
+ registry `artifacts/data_registry/derivatives_backfill_store.yaml`.

## Gap comblé
- **Funding multi-actifs multi-an** : avant = funding par actif via enriched (BTC/ETH/SOL/BNB) ;
  maintenant = 9 actifs propres 2021-2026 → **carry multi-actifs** + **signal crowding/unwind cross-asset**.
- Démontré : cross-asset funding crowding calculable (ex. SOL funding>0 vs ADA/XRP très négatif =
  shorts qui paient = setup squeeze). Le chemin donnée-gratuite → signal fonctionne.

## Reste data-gated (honnête)
Liquidations historiques + OI multi-an + orderbook = pas gratuits. Le collecteur live
(`futur-derivatives`, déjà déployé) construit cet historique à partir de maintenant.

## Prochaines étapes gratuites (ordre du plan)
1. **Bybit + OKX** funding/OI (REST publics gratuits) → normalisation multi-exchange.
2. **Néo-signaux cross-exchange** : funding spread / OI divergence / premium dislocation /
   crowding consensus — l'edge gratuit le plus sérieux (sans liquidations historiques).
3. Carry multi-actifs sur le funding backfillé ; OI/funding event-proxy ; liquidation engine live-only
   (shadow 3-6 mois sur le collecteur).
