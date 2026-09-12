# Correctif de borne pré‑t0 : la clôture, pas l'ouverture (P12)

## Défaut

Les collecteurs P11 (`mexc_pre_binance_tape`, `control_venue_pre_binance_tape`) et `pre_binance_features.strictly_before`
bornaient les bougies sur `open_time < t0`. Une bougie journalière ouverte à 00:00 UTC le jour de t0 (t0 médian ≈ 12 h)
était donc gardée alors qu'elle **clôture après l'arrivée de Binance** et contient les heures post‑lancement.
Mesure sur le stock : **136/136** événements avaient une dernière daily clôturant après t0 (min 0,2 h, médiane 12,2 h,
max 22,0 h) ; 84 bougies horaires MEXC chevauchaient t0 (t0 non aligné sur l'heure). OKX : `bar=1D` est aligné sur
Hong Kong (ouverture 16:00 UTC) ; remplacé par `1Dutc`.

Trouvé par la relecture à trois lentilles du 2026‑09‑12 (lentille microstructure, défaut I23).

## Correctif

- `venue_pre_binance_paths.closed_by(rows, interval, end_ms)` : une bougie n'existe que si `open + intervalle ≤ t0`.
- Les deux collecteurs l'appliquent à la récolte ; `--refilter` l'applique au stock existant, marque le manifeste
  (`close_bounded`, `dropped_straddling`) et journalise une entrée `correction` (append‑only).
- `strictly_before(candles, t0_ms, interval_ms)` refuse (PostT0Leak) toute bougie qui clôture après t0.
- Test dédié : t0 à HH:30 avec daily 00:00 et horaire HH:00 → refusées ; horaire clôturant exactement à t0 → gardée.

Stock refiltré : MEXC 196 bougies retirées (112 daily, 84 horaires) sur 267 fichiers ; contrôles 40 (OKX 14, Bybit 18,
KuCoin 8) ; OKX re‑téléchargé en `1Dutc`. Après correctif : **0** bougie clôturant après t0 sur 339 fichiers.

## Effet sur les descriptifs P11 (MEXC_PRE_BINANCE_FEATURES, 113 événements MEXC‑first)

| feature | n avant → après | médiane avant → après | lignes changées |
|---|---|---|---|
| `pre_binance_return_30d` | 95 → 73 | 0.4788 → 0.3085 | 95 |
| `pre_binance_return_7d` | 95 → 73 | 0.2522 → 0.0918 | 95 |
| `pre_binance_return_3d` | 95 → 73 | 0.1766 → 0.0216 | 95 |
| `pre_binance_return_24h` | 105 → 103 | 0.1926 → 0.1884 | 79 |
| `pre_binance_volume_7d` | 73 → 69 | 10547491.0900 → 5347218.2100 | 73 |
| `pre_binance_volume_24h` | 110 → 105 | 2720647.4750 → 2640009.7600 | 84 |
| `pre_binance_pump_score` | 73 → 69 | 0.7845 → 0.5856 | 73 |
| `pre_binance_volatility_7d` | 73 → 69 | 0.1491 → 0.0940 | 73 |
| `pre_binance_exhaustion_score` | 73 → 69 | 0.1902 → 0.1488 | 38 |
| `pre_binance_liquidity_proxy` | 73 → 69 | 971597.6500 → 792165.7700 | 73 |

Couverture : avant {'full': 110, 'not_collected': 1, 'daily_only': 2} ; après {'full': 95, 'partial': 10, 'not_collected': 8}. Événements avec ≥ 7 daily complètes :
60 → 56 ; avec ≥ 36 horaires : 73 → 73.

## Ce que cela change dans la lecture

La « ruée pré‑lancement » décrite dans P11 (médiane +25 % sur 7 j, +18 % sur 3 j) contenait en partie **le jour du
lancement Binance lui‑même**. Corrigée, elle est de +9 % sur 7 j et +2 % sur 3 j (rendements jusqu'au dernier minuit
UTC avant t0) ; le 24 h horaire (jusqu'à la dernière heure complète avant t0) reste à +19 %. Sémantique nouvelle et
déclarée : les features journalières s'arrêtent au dernier minuit UTC ≤ t0, les horaires à la dernière heure
complète ≤ t0. Ces valeurs étaient descriptives, sans verdict ni test ; aucun budget n'a été consommé sur elles.
