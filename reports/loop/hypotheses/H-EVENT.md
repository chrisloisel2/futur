# H-EVENT — hypothèses écrites depuis le premier regard (2026-09-10), aucune scellée

Contexte : regard unique seq 7 sur le tape officiel (`reports/first_look/EVENT_FIRST_LOOK_RESULTS.md`).
Budget : 2 tests. Chaque hypothèse ci-dessous en consomme 1 à son regard. Aucune n'est scellée ici.

## H-EVENT-1 — pression de délisting, fenêtre forward (H3 forward)
- Mécanisme : identique à `event_delisting_pressure_v1` (short le perp Binance de l'actif à
  publication + 60 s, 60 min, excès vs BTC). INDECIDABLE par concentration (N_eff 21,6, top-1 13 %),
  tout le reste passé (mur 59, t 2,44, placebo 3, fuite −11, 82 % négociable).
- Données : annonces « Binance Will Delist » / « Binance Futures Will Delist » publiées après le
  scellement, collectées par `futur-event-tape.timer` (jamais existantes au scellement : témoin
  physiquement impossible).
- Regard : un seul, quand ≥ 30 nouveaux évènements ont un perp Binance existant 24 h avant.
  Au rythme 2025 (22/an, 82 % négociables) : ≈ 18–20 mois.
- Critères : ceux du prereg H3, plus le coût **mesuré** sur les noms concernés (profondeur à ±10 bps
  à l'annonce, forced-flow tape) au lieu de 4 + 4 bps déclarés — la capacité est la contrainte.

## H-EVENT-2 — fade du listing, version exécutable sur évènements jamais touchés
- Population : les 210 listings perp Binance sans marché spot Binance 24 h avant (jamais résolus,
  jamais pricés par le regard 7). Marché : le perp lui-même, à partir de son lancement.
- Hypothèse : short le perp de lancement + 15 min à lancement + 6 h, excès vs BTC, > 30 bps brut,
  mur 54 bps (perp VIP0). Payeur : l'acheteur à levier de l'ouverture.
- Honnêteté : la direction vient du résultat H2 sur la population spot (+202 bps, t 3,25) ; le
  prereg le dira. Évènements différents, marché différent : ce n'est pas le même candidat.
- Prérequis instrument : l'heure de lancement exacte par `fapi/v1/exchangeInfo.onboardDate`
  (défaut I22 : `trading_start_ts` du tape = date du titre à 00:00). Les perps délistés depuis
  n'y sont plus : compter la perte avant de sceller.

## H-EVENT-3 — délisting cross-venue (Bybit/OKX → marché Binance)
- 403 annonces de délisting Bybit/OKX avec actif (263 perp Bybit, 135 spot Bybit, 5 OKX) ; mesurer
  le marché Binance du même actif (perp si existant, sinon spot), short, 60 min.
- Mécanisme plus faible (flux forcé sur l'autre venue, transmis par arbitrage), plus d'évènements.
  Attendu : entre H4 (zéro) et H3 (fort). À ne sceller qu'après H-EVENT-1 et 2 si le budget le permet.

## Ce qui n'est PAS une hypothèse
- H1 et H4 sont morts sur ce tape à ces horizons : la pompe est dans la première minute, le retard
  cross-venue n'existe pas. Ne pas les recycler sous un autre horizon.
- Re-tester H2 ou H3 sur les mêmes évènements avec un autre horizon = second regard : interdit.


## 2026-09-10 soir — H-EVENT-2 exécutée (regard seq 8) : INDECIDABLE

`event_listing_perp_fade_v1` sur 174 listings perp-first : +251 bps moyen, +230 médian, net taker +228,
capacité 55 M$/fenêtre, mais t 2,12 < 2,3263 (σ 1 546 bps). Tous les autres critères passés. Pas de
variante sur ces évènements (second regard interdit). Budget 1. La dernière piste du jour = P3B.
