# Nouveaux edges hors stack événementiel / trend / BASIS_TERM — faisabilité data (2026-07-17)

Objectif : 6-10 moteurs indépendants. Critères d'un vrai edge : source de données
différente, mécanisme économique différent, corrélation PnL faible avec V1.2 / stack MH /
BASIS_TERM, contribution positive après coûts ×2 + délai, preuve OOS séparée.

Toutes les sources ci-dessous ont été **probées le 2026-07-17** (codes HTTP réels, pas
de suppositions).

## Verdicts par piste

| Piste | Source probée | Verdict data | Historique |
|---|---|---|---|
| **Listing perp** | Binance fapi `exchangeInfo.onboardDate` (654 perps USDT) + data.binance.vision (anti-survivorship, 29 délistés retirés) | **GRATUIT, point-in-time exact** | 2019→auj., 431 listings depuis 2025 |
| **Unlock / supply shock** | DefiLlama `emissions` | **HTTP 402 — passé PAYANT** (defillama.com/subscription) | data-gated |
| **Options positioning** | Deribit `history.deribit.com` trades publics (iv, strike, direction, taille, blocs par trade) | **GRATUIT** — reconstruire skew/flows depuis les trades | ≥ 2019 (probé), 160k trades BTC / 17 j |
| **CEX–DEX / HL lead-lag** | HL `candleSnapshot` (lookback ~jours seulement), archive S3 `hyperliquid-archive` | **403 anonyme = requester-pays AWS** (~qq $/mois de data) ; `fundingHistory` + `premium` horaire gratuits ≥ 2024 | fin = payant léger ; 1h = gratuit |
| **Fundamental rotation** | DefiLlama `overview/fees` (200, 23 MB), `v2/chains`, `protocol/<x>` | **GRATUIT** (fees/revenue/TVL par protocole, séries historiques) | multi-an |
| **Stablecoin liquidity** | `stablecoins.llama.fi/stablecoins` | **GRATUIT** (mcap/chaîne, séries) | multi-an |

## Construit aujourd'hui

1. `scripts/backfill_binance_perp_listings.py` — calendrier listings (onboardDate exact,
   délistés inclus via S3 vision, MISSING_DATA compté) + klines 5m (72 h) / 1h (30 j) /
   funding (30 j) par listing ≥ 2023. Sortie `data/listings_backfill/binance/`.
2. `scripts/test_perp_listing_event_study.py` — event study point-in-time : entrées
   t0+{30m…7j}, horizons 1h…14j, brut / net 40 bps (coûts ×2) / ajusté BTC /
   **short net de funding**, cohortes annuelles, conditionnements sur info disponible
   à l'entrée seulement. Résultats → `reports/LISTING_EVENT_STUDY.md`.
3. `scripts/backfill_deribit_option_trades.py` — trades options Deribit par mois
   (idempotent), BTC 2023→2026 en cours. Couche data du futur moteur
   OPTIONS_POSITIONING (≠ VRP : skew tradé, put/call flow, strikes concentrés, blocs).

## Résultats (2026-07-17, backfills complets)

### Listing engine — event study 518 listings 2023→2026 (`reports/LISTING_EVENT_STUDY.md`)

- **LONG post-listing : NO_EDGE, partout.** Médiane nette (coûts ×2 = 40 bps) négative sur
  TOUTES les cellules delay {30m…7j} × horizon {1h…14j} : −50 à −2100 bps. Hit 31-48 %.
  Cohérent sur les 4 cohortes annuelles. « Acheter l'épuisement » à J+3/J+7 : négatif aussi.
- **La loterie long (mean>0 à J+7→J+21) est du bruit** : bootstrap 2000×, IC95
  [−741, +2439] bps, P(mean>0)=0.62 ; top-5 événements = 259 % du PnL total (un +4745 %).
- **Fade/short : médiane nette DE FUNDING +400 à +1700 bps**, monotone en horizon,
  positive sur 2023/2024/2025/2026 et sous toutes les conditions testées. Signature
  économique réelle (décrue offre/attention). **NON TRADÉ** : SHORT_ENABLED=False (règle
  figée), et le tail risk short est extrême (événements +3200…+4745 % = liquidation).
- **Exploitation retenue (compatible règles) : filtre d'univers** — aucun long sur un perp
  listé depuis < 30 jours (mesuré jusqu'à J+21 ; au-delà = extrapolation, 60-90 j par
  précaution). À brancher dans le ranker 50 / CTREND / edge-gate.

### CEX-DEX lead-lag grossier (1h, gratuit) — `test_hl_premium_leadlag_scan.py`

- Cross-corrélation asymétrique **dans le mauvais sens** : pic à lag −2/−3 h, |lag| ≫ |lead|
  → **Binance mène, le premium HL suit**. Aucune trace de discovery HL→Binance à 1h.
- ETH seul : IC 12-24h faible (0.036-0.054, p<1e-7, ~100 bps bruts déciles extrêmes) —
  vraisemblablement du funding/momentum déguisé, pas un edge autonome. NO_EDGE à 1h ;
  le moteur fin exige tick multi-venue (S3 requester-pays ou collecteur live).

### Options positioning — data OK, signal à tester

Backfill trades BTC 2023→2026 en cours ; features journalières validées (skew tradé,
P/C ratio, flows signés, concentration strikes, blocs). Prochain test : d_skew / d_pc /
top_strike_share → retours forward BTC, corrélation PnL vs stack existant.

## Décisions / gaps honnêtes

- **Unlocks** : la seule source structurée gratuite connue (DefiLlama) est paywallée.
  Options : (a) abonnement DefiLlama Pro, (b) reconstruire on-chain (contrats de
  vesting — lourd), (c) démarrer par les listings (même famille "supply/attention
  shock", data parfaite). → Choix : (c) d'abord, décision (a) à prendre par l'humain.
- **CEX-DEX fin (secondes→minutes)** : pas gratuit rétroactivement. Deux chemins :
  collecteur live dès maintenant (comme fait pour les liquidations) + éventuellement
  S3 requester-pays pour le backtest historique. Pas bloquant pour commencer :
  le `premium` horaire HL gratuit permet un premier scan lead-lag grossier.
- **Survivorship listing** : 29 perps USDT délistés-retirés connus ; ceux sans data
  fapi sont comptés `MISSING_DATA` dans le registry, jamais ignorés en silence.
