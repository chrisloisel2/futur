# Archiveur positioning Binance USD-M — 2026-07-17

## Pourquoi

Les 4 endpoints fapi `/futures/data/*Ratio` (top traders, global, taker) ne
conservent que **30 jours** d'historique. Sans archivage récurrent, cette
donnée disparaît définitivement. Les dumps Vision `metrics` couvrent des
ratios équivalents à 5 min mais en J-2 et sans garantie de pérennité — le
dossier `um/daily/liquidationSnapshot` a déjà été retiré de Vision (constaté
2026-07-17 : il ne reste que `cm`, fenêtre 2023-06-25 → 2026-01-01).
L'archiveur capture la fenêtre J-2 → now et sert d'assurance.

Usage cible : features de positionnement pour les moteurs événementiels
(crowding, épuisement de cascade) et l'edge top-traders du plan Edge Factory.

## Ce qui est archivé

| endpoint | fichier | colonnes |
|---|---|---|
| topLongShortPositionRatio | `{SYM}_top_position.parquet` | longAccount, shortAccount, longShortRatio |
| topLongShortAccountRatio | `{SYM}_top_account.parquet` | idem |
| globalLongShortAccountRatio | `{SYM}_global_account.parquet` | idem |
| takerlongshortRatio | `{SYM}_taker_vol.parquet` | buySellRatio, buyVol, sellVol |

- Univers : les 50 symboles du collecteur dérivés (`futur-derivatives.service`).
- Period `5m`, limit 500 → **41,6 h couvertes par appel**.
- Stockage `data/positioning/` (append + dedup `(timestamp, period)` via
  `atomic_parquet.append_enriched_atomic`, jamais d'écriture directe).
- `registry.json` : état du dernier run (ok/err par symbole×endpoint).

## Récurrence

`deploy/systemd/futur-positioning.{service,timer}` — installés dans
`~/.config/systemd/user/`, timer **toutes les 6 h** (00:20/06:20/12:20/18:20),
`Persistent=true`. Marge : 7× la cadence ; un trou n'apparaît que si le timer
échoue > 41 h d'affilée. Rattrapage manuel possible sous 30 j avec
`--period 1h --limit 720`.

Installation :

```bash
cp deploy/systemd/futur-positioning.* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now futur-positioning.timer
```

Log : `reports/positioning_archiver.log`. Code retour : 1 seulement si TOUS
les endpoints échouent (erreur partielle = rattrapée au run suivant).

## Tests

`tests/test_positioning_archiver.py` — logique pure sans réseau :
normalisation des payloads (types, colonnes, symbol injecté pour taker),
dedup keep-last, invariant couverture ≥ 4× cadence, forme de l'univers.

## Environnement

- Python : `.venv` (3.8), pandas/pyarrow du projet.
- Cutoff premier run : 2026-07-17 ~21:30 UTC+2 (le premier point archivé
  remonte à ~41 h avant, soit ~2026-07-16 04:00 ; tout ce qui précède est
  couvert par Vision metrics J-2).
- Commande : `python3 scripts/archive_binance_positioning.py` (univers 50,
  5m×500 par défaut).
