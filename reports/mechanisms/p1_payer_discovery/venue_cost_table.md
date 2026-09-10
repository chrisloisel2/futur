# P1 — table des coûts par venue (mesurée sur L1 + trades, 3 jours : 2026-09-07 → 09-09)

Frais : barèmes **publiés** chargés depuis `data_lake/manifests/published_fee_schedules_2026-09-10.json` (bps par côté ; classes official / third_party_fallback ; les frais **réels du compte** ne sont pas encore lus — `scripts/fetch_account_fees.py`). Bybit : frais seuls, aucune donnée sur disque.

| venue | symbole | spread coté méd. | lat. méd. | ½-spread eff. exact méd. / moy. | au touch | maker : dérive − ½-spread gagné (30 s) | P(fill 30 s) | RT taker VIP0 / best | RT maker VIP0 / best |
|---|---|---|---|---|---|---|---|---|---|
| binance | BTCUSDT | 0.01 bps | 111 ms | 0.06 / 0.38 | 0.63 | 0.53 − 0.01 = 0.52 | 0.41 | 10.77 / 4.17 | 5.05 / 1.05 |
| binance | ETHUSDT | 0.04 bps | 111 ms | 0.06 / 0.46 | 0.63 | 0.54 − 0.02 = 0.52 | 0.49 | 10.92 / 4.32 | 5.03 / 1.03 |
| binance | SOLUSDT | 0.96 bps | 111 ms | 0.48 / 0.65 | 0.95 | 0.17 − 0.48 = -0.31 | 0.41 | 11.30 / 4.70 | 3.38 / -0.62 |
| okx | BTCUSDT | 0.01 bps | 120 ms | 0.10 / 0.28 | 0.50 | 0.57 − 0.01 = 0.56 | 0.37 | 10.55 / 4.55 | 5.12 / 0.62 |
| okx | ETHUSDT | 0.04 bps | 120 ms | 0.06 / 0.36 | 0.56 | 0.45 − 0.02 = 0.43 | 0.41 | 10.72 / 4.72 | 4.86 / 0.36 |
| okx | SOLUSDT | 0.96 bps | 120 ms | 0.48 / 0.66 | 0.92 | 0.39 − 0.48 = -0.10 | 0.30 | 11.33 / 5.33 | 3.81 / -0.69 |
| hyperliquid | BTCUSDT | 0.13 bps | 332 ms | 0.06 / 0.24 | 0.64 | 0.40 − 0.11 = 0.29 | 0.24 | 9.48 / 5.28 | 3.59 / -0.01 |
| hyperliquid | ETHUSDT | 0.40 bps | 333 ms | 0.20 / 0.46 | 0.68 | 0.59 − 0.26 = 0.33 | 0.17 | 9.91 / 5.71 | 3.66 / 0.06 |
| hyperliquid | SOLUSDT | 0.96 bps | 333 ms | 0.48 / 0.68 | 0.71 | 0.55 − 0.55 = 0.00 | 0.16 | 10.36 / 6.16 | 3.01 / -0.59 |

| venue | maker VIP0 / best | taker VIP0 / best | tier « best » | source VIP0 / best | ce que le tier exige |
|---|---|---|---|---|---|
| binance | 2.0 / 0.00 | 5.0 / 1.70 | VIP9 | official / third_party_fallback (NON FINAL) | VIP9 : 5 500 BNB (officiel) ; volume futures 30 j NON CONFIRMÉ (officiel : 5× spot ⇒ 20 G ; tiers : 25 G / 30 G) |
| okx | 2.0 / -0.25 | 5.0 / 2.00 | VIP8 | official / official | VIP8 : ≥ 2 G USD de volume 30 j (officiel, cadre 2025-11-25) ; VIP9 : ≥ 20 G |
| bybit | 2.0 / 0.00 | 5.5 / 1.80 | Pro 5 | official / official | Pro 5 : ≥ 3 G USD de dérivés 30 j, volume API > 20 % (officiel) ; Pro 6 : ≥ 5 G |
| hyperliquid | 1.5 / -0.30 | 4.5 / 2.40 | rebate tier 3 + volume tier 6 | official / official | rebate tier 3 : > 3 % du volume maker de la plateforme (14 j) ; tier 6 : > 7 G USD / 14 j (officiel) |

## Décision (mur ×3, bruts de référence P0 : microstructure 0.78 bps, cross-exchange 1.97 bps)

- plancher requis : ≤ **0.26 bps** aller-retour (microstructure), ≤ **0.33 bps par jambe** (cross-exchange)
- **microstructure au VIP0 : NON** — aucun mode, aucune venue ne passe sous 0.26 bps à frais publics de base
- **microstructure au meilleur tier publié, verdict FINAL (sources officielles seules) : OUI** — okx/SOLUSDT/best/maker, hyperliquid/BTCUSDT/best/maker, hyperliquid/ETHUSDT/best/maker, hyperliquid/SOLUSDT/best/maker
- non final (tier 'best' de source tierce, non confirmé officiellement) : binance/SOLUSDT/best/maker
- conditions de la réouverture : maker-only, tiers à rebate (binance/SOLUSDT/best/maker : VIP9, P(fill 30 s) 0.41, ½-spread gagné 0.48 bps contre dérive perdue 0.17 bps; okx/SOLUSDT/best/maker : VIP8, P(fill 30 s) 0.30, ½-spread gagné 0.48 bps contre dérive perdue 0.39 bps; hyperliquid/BTCUSDT/best/maker : rebate tier 3 + volume tier 6, P(fill 30 s) 0.24, ½-spread gagné 0.11 bps contre dérive perdue 0.40 bps; hyperliquid/ETHUSDT/best/maker : rebate tier 3 + volume tier 6, P(fill 30 s) 0.17, ½-spread gagné 0.26 bps contre dérive perdue 0.59 bps; hyperliquid/SOLUSDT/best/maker : rebate tier 3 + volume tier 6, P(fill 30 s) 0.16, ½-spread gagné 0.55 bps contre dérive perdue 0.55 bps)
- **cross-exchange (jambe disloquée prise en taker + jambe posée en maker) : NON** — plus bas aller-retour deux jambes : binance/SOLUSDT/best 4.09 bps, okx/SOLUSDT/best 4.63 bps, okx/ETHUSDT/best 5.07 bps
- **lecture** : un plancher maker négatif n'est pas un edge, c'est la marge du market maker — le demi-spread gagné au fill — qui n'existe que si l'ordre est servi et n'est disponible qu'aux tiers à rebate ; la réouverture change l'objet testé (exécution passive), elle ne ressuscite pas le signal de P0.
- plus bas aller-retour maker au meilleur tier publié : okx/SOLUSDT -0.69 bps, binance/SOLUSDT -0.62 bps, hyperliquid/SOLUSDT -0.59 bps

## Brut requis pour passer le mur, par venue / mode (bps)

| clé | brut requis |
|---|---|
| okx/SOLUSDT/best/maker | -2.08 |
| binance/SOLUSDT/best/maker | -1.85 |
| hyperliquid/SOLUSDT/best/maker | -1.78 |
| hyperliquid/BTCUSDT/best/maker | -0.03 |
| hyperliquid/ETHUSDT/best/maker | 0.18 |
| okx/ETHUSDT/best/maker | 1.07 |
| okx/BTCUSDT/best/maker | 1.85 |
| binance/ETHUSDT/best/maker | 3.10 |
| binance/BTCUSDT/best/maker | 3.14 |
| hyperliquid/SOLUSDT/vip0/maker | 9.02 |
| binance/SOLUSDT/vip0/maker | 10.15 |
| hyperliquid/BTCUSDT/vip0/maker | 10.77 |
| hyperliquid/ETHUSDT/vip0/maker | 10.98 |
| okx/SOLUSDT/vip0/maker | 11.42 |
| binance/BTCUSDT/best/taker | 12.51 |
| binance/ETHUSDT/best/taker | 12.95 |
| okx/BTCUSDT/best/taker | 13.66 |
| binance/SOLUSDT/best/taker | 14.12 |
| okx/ETHUSDT/best/taker | 14.14 |
| okx/ETHUSDT/vip0/maker | 14.57 |
| binance/ETHUSDT/vip0/maker | 15.10 |
| binance/BTCUSDT/vip0/maker | 15.14 |
| okx/BTCUSDT/vip0/maker | 15.35 |
| hyperliquid/BTCUSDT/best/taker | 15.83 |
| okx/SOLUSDT/best/taker | 15.98 |
| hyperliquid/ETHUSDT/best/taker | 17.14 |
| hyperliquid/SOLUSDT/best/taker | 18.48 |
| hyperliquid/BTCUSDT/vip0/taker | 28.43 |
| hyperliquid/ETHUSDT/vip0/taker | 29.74 |
| hyperliquid/SOLUSDT/vip0/taker | 31.08 |
| okx/BTCUSDT/vip0/taker | 31.66 |
| okx/ETHUSDT/vip0/taker | 32.15 |
| binance/BTCUSDT/vip0/taker | 32.31 |
| binance/ETHUSDT/vip0/taker | 32.75 |
| binance/SOLUSDT/vip0/taker | 33.91 |
| okx/SOLUSDT/vip0/taker | 33.98 |

Méthode : demi-spread effectif contre la cote EXACTE prévalant au trade, sur 2 h par jour (12-13 h UTC) ; la méthode mid-de-grille-1s le gonflait ×7 (calibré). Limites : L1 seulement (pas de profondeur au-delà du touch) ; fin de file supposée pour P(fill) ; 3 jours ; tailles OKX converties par ctVal public.
