# Derivatives Collector + BTC OI-Deleveraging Engine (2026-06-28)

Branche `feat/derivatives-collector-oi-engine`. Décision : collecteur live MAINTENANT
(avantage informationnel) + test rapide du BTC OI-deleveraging (preuve de valeur de la famille).

## 1. Collecteur dérivés live (Phase 1) — CONSTRUIT & PROUVÉ

`src/institutional/data/derivatives_collector/` (writer append-only + REST/WS) +
`scripts/run_derivatives_collector.py` + `validate_derivatives_live_store.py`.

- Réseau Binance Futures **OK** (REST OI/taker testés en direct).
- WS `!forceOrder@arr` = **capture les LIQUIDATIONS** (la donnée introuvable en historique).
- Démo 90s : **18 partitions immutables, 0 corrompu, 9 actifs** (OI + ratios). Pas de liquidation
  sur 90s calmes (événementiel — nécessite une collecte continue).
- Stockage append-only `data/derivatives_live/exchange=binance/stream=*/symbol=*/date=*/part-*.parquet`
  (écriture atomique, jamais de réécriture).

**Déploiement** : `systemctl --user` (comme futur-api) pour tourner en continu. **Chaque jour
collecté est définitivement acquis** ; les liquidations ne se rattrapent pas en historique.

## 2. BTC OI-Deleveraging Engine (Phase 2) — TEST RAPIDE = EDGE NON EXPLOITABLE

Données réelles : BTC `oi_sum` + funding 2021-2025 (185 événements OI-drop + price-drop).
Event-first, pas de short, rule-based (mesure l'edge brut avant ML/collecte).

| Variante | n | WR | PF | avg w/l | total | cost×2 PF |
|---|---:|---:|---:|---:|---:|---:|
| H=2h | 185 | 50% | 0.82 | 0.81 | −19.1% | 0.68 |
| H=4h | 185 | 51% | 0.87 | 0.84 | −16.9% | 0.74 |
| H=8h | 185 | 51% | 1.01 | 0.98 | +1.7% | 0.90 |
| H=8h + funding filter | 136 | 54% | 0.90 | 0.78 | −12.3% | — |
| H=8h capitulation (5%/4%) | 49 | 51% | 0.87 | 0.83 | −5.3% | — |
| H=8h capit + funding | 45 | 51% | 0.94 | 0.90 | −2.0% | — |

By-year (H=4h) : 2021 +1.0%, 2022 −4.4%, **2023 +14.5%, 2024 −20.2%, 2025 −7.8%** → wildly
régime-dépendant, aucune stabilité.

**Gate (PF≥1.35, avg_w/l≥1.5, cost×2≥1.10, non concentré) : ÉCHEC sur toutes les variantes**,
y compris après réparations principielles (over-leveraged flush, capitulation). PF max 1.01.

## Verdict — Cas C du plan : *"OI seul ne suffit pas, vrai feed liquidations obligatoire"*

Le proxy OI-deleveraging **n'a pas d'edge convexe exploitable** sur les données disponibles. Le
"rebond" après chute d'OI n'est pas fiable (parfois le deleveraging est le début d'un krach plus
grand : 2024 −20%). Les réparations principielles ne sauvent pas le signal.

→ **Cela VALIDE l'investissement dans le collecteur** : le raccourci "proxy OI sans collecte" ne
marche pas. Pour tester sérieusement la famille liquidation/deleveraging, il faut :
1. le **vrai feed liquidations** (forceOrder) — maintenant capturé en live,
2. une **classification ML** event-first sur features riches (OI_after/before, premium dislocation,
   taker imbalance, liquidation $ side) — possible seulement quand la donnée existe.

```
DERIVATIVES_COLLECTOR    : BUILT & RUNNING-READY (déployer en systemd)
DERIVATIVES_LIVE_STORE   : accumule (liquidations dès qu'événements)
BTC_OI_DELEVERAGING      : NO_EDGE (proxy OI insuffisant — confirme besoin feed liquidations)
LIQUIDATION_EVENT_FIRST  : EN ATTENTE DE DONNÉES (collecteur en cours)
PORTFOLIO V1.1 (carry50) : socle défensif (~3.6%/an) — paper en attente
40-80K/an                : data-gated → dépend de la collecte/achat liquidations multi-actifs
```

## Prochaines étapes honnêtes
1. **Déployer le collecteur en systemd** (24/7) — démarrer l'accumulation des liquidations.
2. Après quelques semaines : features event-first + ML sur le vrai feed → re-tester l'edge convexe.
3. Si besoin de validation rapide : **acheter l'historique liquidations/OI multi-actifs** (sinon
   attendre ~6-12 mois de collecte). C'est le seul chemin honnête vers les moteurs offensifs.
4. En parallèle : paper-live V1.1 (carry 50%) comme socle.
90/90 tests.
