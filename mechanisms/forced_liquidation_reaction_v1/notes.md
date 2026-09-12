# forced_liquidation_reaction_v1 — notes

RESEARCH_ONLY. Spec écrite avant toute jointure prix ; jamais exécutée ; aucun essai débité. Voir la tape correspondante et son readiness/catalogue.


## 2026-09-10 — first look preregistered (P3B)

Spec rewritten to the sealed definition: entry = first trade >= event + 2 s, continuation in the direction of the forced flow, primary 30 s (5 s, 5 min), universe = frozen tape sha b78d1b50 (24 930 rows, one session), Binance USDT >= 50 k$: 368 events, 51 five-minute clusters. Sister mechanism `forced_liquidation_exhaustion_v1` (>= 250 k$, reversal, 5 min). Family `liquidation`, threshold_t(2) = 1.96. Prices at look time from Binance REST aggTrades (short windows, cached). Harness `first_look.py` pinned; synthetic positive control in `results/positive_control.json`.
