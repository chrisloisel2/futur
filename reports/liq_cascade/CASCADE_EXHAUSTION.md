# Épuisement de cascade (liquidations réelles) — **NO_EDGE** (2026-07-17)

Expérience séparée (piste #2 Edge Factory), lancée après le verdict
CTREND v1, sur les liquidations RÉELLES cm Vision (`liquidationSnapshot`).
Protocole et note de reformulation dans la docstring de
`scripts/backtest_cascade_exhaustion.py` ; détail JSON `CASCADE_EXHAUSTION.json`.

## Données — 3 découvertes opérationnelles

1. `liquidationSnapshot` n'existe plus QUE pour les futures **COIN-M** ;
   le dossier USD-M a été retiré de Vision.
2. La publication cm s'arrête au **2024-10-14** (aucune clé S3 2025+) →
   fenêtre utile **2023-06-25 → 2024-10-14 (~16 mois)**, 45 PERPs,
   257 874 événements archivés localement (irremplaçables).
3. Les colonnes `taker_buy_*` des parquets enriched 1h sont un
   **PLACEHOLDER** (= quote_volume/2, delta ≡ 0) — inutilisables pour tout
   signal de flux. Le vrai flux taker vient de Vision metrics 5-min
   (`sum_taker_long_short_vol_ratio`).

## Setup testé (pré-déclaré, aucun seuil retouché après les rendements)

cascade long-liq ≥ P99 90 j dans [t−3, t] + ΔOI 4 h ≤ −2 % + funding_z
30 j ≤ 0,5 + absorption (close moitié haute) + retournement taker ratio
(> 1 à t, < 1 sur [t−3, t−1]) → long open t+1, hold 8 h, 30 bps A/R.
9 actifs fleet (DOT exclu : funding manquant), holds non superposés.

## Résultats (net ×1 par événement, hold 8 h)

| étape | n | moyenne nette | lecture |
|---|---:|---:|---|
| baseline inconditionnelle 8 h (brut) | — | +0,06 % | dérive de fond |
| 1. cascade seule | 886 | **−0,06 %** | pas de rebond spontané |
| 1+2 (+chute OI) | 456 | +0,19 % | t≈1, sous le seuil coûts ×2 |
| 1+2+3 (+funding normalisé) | 278 | +0,12 % | bruit |
| 1+2+3+4 (+absorption) | 236 | +0,12 % | bruit (BTC seul : +0,38 %, t=1,1, n=25) |
| **setup complet (+CVD flip)** | **126** | **−0,28 %** | le déclencheur CVD DÉTRUIT le signal |

Pools : majors (BTC/ETH/SOL) −0,53 %/évt (t=−1,06, 39 jours distincts) ·
alts −0,13 % (t=−0,31) · tous −0,28 % (t=−0,88, 67 jours distincts).

Tests de rejet du plan (sur BTC, primaire déjà négatif) : coûts ×2
−0,44 % · délai +1 h +0,09 % · sans top-10 cascades −0,45 %.

## Verdict

**NO_EDGE dans cette fenêtre et cette formulation.** Enseignements :

- Attendre la confirmation CVD (« acheter le premier uptick ») retourne le
  signe : ce qui reste de rebond est déjà consommé à la confirmation —
  cohérent avec le moteur LIQ_CASCADE 5-min existant, qui capte l'edge EN
  entrant PENDANT le stress, pas après confirmation horaire.
- L'étage OI-crash (1+2, +0,19 %/8 h brut de surcoûts) est la seule trace
  positive ; trop faible pour coûts ×2 et t≈1 → pas de suite sans plus de
  données.
- Fenêtre SANS grand régime de capitulation (2023-06→2024-10) et proxy cm ;
  le flux propriétaire um live (forceOrder, LIQUIDATION_INVENTORY) reste
  la seule source croissante — les moteurs événementiels 5-min existants
  restent le bon véhicule pour cet edge.

## Environnement

- Sources : cm liquidationSnapshot (45 PERPs), Vision metrics 5-min (OI,
  taker ratio), funding 8 h backfill, enriched 1h (prix/absorption).
- Env : `.venv` Python 3.8.10. Commande :
  `.venv/bin/python scripts/backtest_cascade_exhaustion.py`
- Généré : 2026-07-17 ~20:30 UTC.
