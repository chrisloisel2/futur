# MEXC_VOLUME_TRUST_V1 — spécification déclarée AVANT le calcul

Statut : définitions figées avant toute lecture des sorties. Descriptif, non validé, sans verdict, sans alpha,
sans budget consommé. Le calcul de cette covariable n'est pas un « regard » (aucun rendement post‑t0 n'est lu).

## 1. Question

Le volume MEXC observé avant l'arrivée du perpétuel Binance ressemble‑t‑il à celui d'un marché (volume couplé à
l'amplitude, plancher bas, forte dispersion) ou à celui d'un programme à débit constant (plancher haut, faible CV,
découplé de l'amplitude) ? Réponse par événement sous forme de **rangs intra‑MEXC**, jamais de probabilité.

## 2. Ce que ce n'est pas

- Pas un score calibré : aucun événement de wash étiqueté n'existe sur aucune place ; sensibilité, spécificité et
  taux de faux positifs sont **inconnus**.
- Pas un filtre par événement : un ban par événement sur un score bruité serait un filtre dépendant des données.
  La politique allowed/banned est **globale, par feature** (§8).
- Pas un contrôle par les 24 événements « autre place première » : autre actif, autre marché, autre régime de
  frais, autre époque. Ils ne servent qu'à une différence descriptive de place avec IC (§7).

## 3. Entrées (strictement avant t0 ET avant l'annonce)

- Bougies **horaires** MEXC du marché retenu par P9 (`mexc_market`), `quote_volume` (USDT) uniquement. Le volume
  de base d'un perp MEXC est un nombre de contrats : jamais lu, sauf pour la porte d'intégrité.
- Borne de clôture : une bougie n'existe que si `open + 1 h ≤ t0` (correctif P12, cf. PRE_BINANCE_TAPE_CLOSE_BOUND_FIX).
- Fenêtre W = `[max(mexc_listed_ts + 24 h, t0 − 72 h), floor_hour(publication_ts))` : les 24 premières heures de
  cotation MEXC sont exclues (bougies de lancement), les heures post‑annonce sont exclues (le volume post‑annonce
  est de l'information, pas du wash). Sans `publication_ts` : fin = `floor_hour(t0)` et `announcement_cut = false`.
- Les bougies 5 min sont exclues par règle (disponibles pour 45 événements seulement : entrée cachée interdite).
- Événements de classe `BAD_TIMESTAMP` (P10/P11) : `NOT_COMPUTABLE` par règle, réintégration seulement sur preuve
  manuelle documentée.

## 4. Portes de support (déclarées avant calcul)

- `n_hours(W) ≥ 36` sinon `NOT_COMPUTABLE` (aucune valeur, aucun rang, aucun drapeau).
- Intégrité : pour chaque bougie, `low·(1−0,005) ≤ quote_volume/(volume·mult) ≤ high·(1+0,005)`, `mult` = puissance de
  10 la plus proche de la médiane de `quote_volume/(volume·close)` (perp) ou 1 (spot). Une violation → `INTEGRITY_FAIL`.
- Pas de tape horaire stockée → `NO_TAPE`.
- Un événement non `MEASURED` n'est ni « sain » ni « suspect » : `None` partout.

## 5. Quatre covariables (sur W, quote_volume)

| id | définition | sens de l'anomalie |
|---|---|---|
| F1 `volume_floor` | p10(qv) / médiane(qv) | haut = programme |
| F2 `volume_cv` | écart‑type / moyenne, qv winsorisé à p99 | bas = programme |
| F3 `volume_range_coupling` | Spearman(qv, (high−low)/close) | bas = programme (peut être indéfini si série constante ; alors non rangé) |
| F4 `log10_volume_per_range` | log10 médiane(qv / ((high−low)/close)) sur les bougies d'amplitude > 0 | haut = profond OU gonflé (confondu avec la taille) |

Abandonnés (relecture à trois lentilles, 2026‑09‑12) : part de volumes « ronds » (non identifiable sur des sommes de
bougies, contrats entiers sur perp), autocorrélation/entropie (SE 0,12 à n = 72, signe non diagnostique), ratio
spot/perp (0 actif avec les deux marchés), Benford (nul invalide à 72 bougies), Amihud journalier (fusionné dans F4),
pic pré‑annonce (anticipation ≠ wash, cf. §6).

## 6. Covariables hors drapeau

- `anticipation_ratio` = moyenne horaire de qv sur `[fin − 24 h, fin)` / médiane horaire sur le reste de W ; `None`
  si la base < 24 h. Mesure une anticipation (information), pas un wash : **jamais** dans le rang ni le drapeau.
- `market_structure`, `n_hours_used`, `n_post_announcement_dropped`, `n_before_window_dropped`, bornes de W, `mult`.

## 7. Rangs, drapeaux, contrôles

- Rangs percentiles intra‑MEXC des événements `MEASURED` (F1 desc, F2 asc, F3 asc, F4 desc) ; `volume_anomaly_rank`
  = moyenne des rangs disponibles.
- `wash_volume_suspect` = décile supérieur de `volume_anomaly_rank` (fraction **pré‑déclarée** 10 %, arrondi
  supérieur), calculé seulement si ≥ 10 événements `MEASURED` ; sinon `None`.
- `programme_like` = `F1 ≥ 0,8 ET F2 ≤ 0,4` (règle mécanique séparée). Divulgation : ces deux seuils ont été
  suggérés par un relecteur après lecture de 4 tapes MEXC (GUA, OPENAI, AIO, +1) ; le décile est sans paramètre.
- Contrôle **même actif, second venue, mêmes heures** (KuCoin, Bybit, OKX, Gate ≤ 416 j) : `log10_venue_volume_multiple`
  = log10 médiane_h(qv_MEXC / qv_autre) et `coupling_gap` = F3_MEXC − F3_autre sur les heures communes (≥ 36).
  Seule quantité contrôlée par l'actif ; descriptive.
- Différence de place : moyenne de F4 (MEXC, MEASURED) − moyenne de F4 (24 contrôles autre‑place‑première,
  MEASURED), SE, IC 95 %, demi‑largeur exprimée en ratio (attendu ≈ 2,3–2,5×). Étiquette obligatoire : différence
  de place + marché + frais + époque.

## 8. Politique globale des features (par feature, jamais par événement)

| feature | classe | autorisée comme |
|---|---|---|
| `pre_binance_return_*`, `volatility_7d`, `range_7d`, `max_drawdown_7d`, `pump_score` | prix seul | conditionnement (prereg) |
| `pre_binance_volume_7d`, `volume_24h`, `liquidity_proxy` | volume | covariable descriptive, **jamais** conditionnement ni exclusion pour MEXC_FIRST |
| volumes USD absolus | volume | **bannis** en comparaison inter‑places (rangs intra‑MEXC seulement) |
| `exhaustion_score` | mixte (40 % volume) | covariable |
| F1–F4, `volume_anomaly_rank`, `wash_volume_suspect`, `programme_like` | covariable de wash | robustesse seulement, jamais un filtre |
| `anticipation_ratio` | anticipation | covariable, hors drapeau |
| `venue_volume_multiple`, `coupling_gap` | contrôle même actif | covariable |

## 9. Puissance déclarée

Détecte les programmes à débit constant seulement ; aveugle aux bots qui imitent l'organique ; une inflation
propre à un événement inférieure à ~10× est indétectable depuis des bougies (SD transversale de log10 du volume
horaire ≈ 0,8) ; au‑delà, elle reste confondue avec la taille du jeton. Différence de place : ratio minimal
détectable ≈ 2,5×.

## 10. Ordre d'opposabilité

1. Ce fichier et le code (`data_lake/indices/mexc_volume_trust.py`) sont commités **avant** toute sortie.
2. Les sorties (`reports/wash_volume/`) portent `code_sha` (commit du code) et `spec_sha256` (hash de ce fichier).
3. Aucun fichier de résultat post‑t0 (`*RESULT*`, rendements, Vision) n'est lu par le module ; test dédié.
4. Toute modification ultérieure des seuils ou de la fenêtre est une V2, avec sa propre spec et divulgation.
