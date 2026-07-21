# funding_relative_value_cross_venue_v1

> ## ❌ VERDICT : BINANCE_BYBIT_NO_EDGE (2026-07-21)
>
> Premier test réellement gaté (prix réels des deux côtés, moteur deux
> jambes, `promotion_gate`) de la paire Binance↔Bybit : spread brut
> 1,2–3,2 %/an par actif, net négatif dès les coûts ×1 sur BTC/ETH/SOL/BNB
> (portefeuille −1,10 %/an ×1, −4,16 %/an ×2), DSR ≈ 0, PF = 0,67. Cohérent
> avec l'avertissement déjà documenté dans `FUNDING_XVENUE_PROTOCOL.md`
> (spread Binance↔Bybit "quasi nul" sur un rapport antérieur). Combiné à
> Binance↔Hyperliquid (déjà `NO_EDGE`, `FUNDING_XVENUE_V0`, 2026-07-18/19),
> les deux paires candidates avec cette mécanique sont maintenant fermées.
> Détail : [results/PROMOTION_GATE_VERDICT_2026-07-21.md](results/PROMOTION_GATE_VERDICT_2026-07-21.md).

Moteur de rendement market-neutral : long perp sur la venue au funding le
plus bas, short perp sur la venue au funding le plus élevé, même actif,
delta neutre. Succède à `stress_gate_dispersion_v2_reproduction` (clôturé
`NO_INCREMENTAL_EDGE`) — mécanisme différent (moteur de rendement, pas un
filtre de risque).

## Documents

- [PREREGISTRATION.md](PREREGISTRATION.md) — thèse et mécanisme, écrits
  avant tout accès aux données.
- [DATA_INVENTORY.yaml](DATA_INVENTORY.yaml) — étape 3 : funding réel sur
  les 3 venues, prix Bybit backfillé le 2026-07-21, prix Hyperliquid
  `NOT_CLEANLY_RETRIEVABLE` au-delà de 4 jours (proxy funding-only).
- [backtest_funding_rv_v1.py](backtest_funding_rv_v1.py) — étapes 6-7,
  Binance↔Bybit uniquement, hystérésis reprise sans retouche de
  `FUNDING_XVENUE_V0` (`n_trials=1`, aucune grille testée sur cette paire).
- [results/EVENT_LEVEL_NET_EDGE_2026-07-21.json](results/EVENT_LEVEL_NET_EDGE_2026-07-21.json) —
  chiffres bruts par actif/année.
- [results/PROMOTION_GATE_VERDICT_2026-07-21.md](results/PROMOTION_GATE_VERDICT_2026-07-21.md) —
  verdict `gate_sleeve`/`gate_research`.

## Prochaine étape

Aucune candidate non fermée sur ce mécanisme précis (long bas/short haut,
même actif, deux venues) avec les 3 venues actuellement instrumentées.
Rouvrir nécessiterait soit une 4ᵉ venue candidate, soit une thèse
matériellement différente — pas un retuning des mêmes seuils.
