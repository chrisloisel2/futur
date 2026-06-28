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

---

## Cross-exchange (Bybit + OKX) — CONSTRUIT (tag v0.20)

`backfill_multi_exchange_funding_free.py` : funding Bybit (~4000 pts/actif, 8h, ~3.5y) + OKX
(~290 pts, ~3 mois — limite gratuite OKX), normalisé sur symboles Binance.
`src/institutional/data/derivatives/cross_exchange.py` : panel funding 3-exchanges aligné 8h +
`spread` (max−min), `consensus`, pairwise, `spread_zscore`/`consensus_zscore`.

### Test du signal (`validate_cross_exchange_signals.py`)
- 3 exchanges alignés BTC/ETH/SOL, overlap ~132-140 périodes (~44j, **limité par OKX gratuit**).
- spread funding médian **~0.5 bps**, p99 **~2 bps** → funding bien arbitré, spikes en stress (mesurable).
- spread_zscore → forward 24h BTC : **pas de relation directionnelle propre** sur 44j d'overlap.
  → signal **calculable et sain**, mais son **alpha non validé** (overlap trop court ; OKX limite à ~3 mois).
- 3 tests unitaires (spread/consensus/zscore) PASS. Suite : **93/93**.

### Honnête
L'edge cross-exchange existe conceptuellement (dislocations de funding) mais **validable seulement avec
plus d'overlap** : laisser le collecteur live accumuler Bybit/OKX, ou backfill OKX plus profond si dispo.
Le foundation est posé ; pas d'alpha prouvé à ce stade.
