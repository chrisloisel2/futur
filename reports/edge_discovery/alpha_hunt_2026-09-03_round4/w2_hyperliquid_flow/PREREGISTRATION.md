# W2_HYPERLIQUID_FLOW — PREREGISTRATION
Écrit le 2026-09-03 **avant** tout test d'hypothèse. Seule la reconnaissance de structure de
données (schémas, comptages, couverture de mapping, latence d'observation du collecteur) a été
faite avant ce document ; aucun rendement forward n'a été calculé.

## 0. Découverte structurante faite en reconnaissance (pas un test)

`data/hyperliquid/twap` n'est **pas** un flux de 42 jours. Le collecteur interroge
`twapHistory(user)` pour ~2 215 wallets et reçoit à chaque poll **l'historique complet** de
chaque utilisateur. Après déduplication sur `(user, coin, state.timestamp)` :

- **397 969 TWAP uniques**, 2 215 users, 605 coins, **2024-02-03 → 2026-08-29**.
- Chaque TWAP a un enregistrement `activated` (à la création, `executedSz = 0`) et un
  enregistrement terminal (`finished` 230 420 / `terminated` 126 652 / `error` 34 646 /
  `stopped` 800 / absent 5 451).
- Champs connus **à la création** (PIT-safe) : `coin`, `side` (B/A), `sz` planifiée,
  `minutes` (durée programmée), `reduceOnly`, `randomize`, `user`.
- Champs **non** disponibles à la création (interdits comme signal) : `executedSz`,
  `executedNtl`, `status` final, `end_ms`.

C'est donc un jeu d'événements de **flux futur connu** de 2,5 ans, pas un échantillon
mono-régime. Le round 2 (W5) n'a jamais touché ce fichier — il a travaillé sur `trades`.

**Biais de survie déclaré d'avance** : la liste des 2 215 users vient (vraisemblablement) du
tape de trades live 2026-07/08. L'historique 2024-2025 est donc conditionné à « ce wallet était
encore actif en juillet 2026 ». Pour une étude de *skill de wallet* ce biais serait
disqualifiant ; pour une étude d'**impact de prix d'un ordre programmé** il est faible mais
sera déclaré sur chaque résultat, et le test de stabilité par année sert aussi de garde-fou.

**Latence de détection mesurée (fait, pas hypothèse)** : le collecteur poll toutes les ~76 s
médianes mais tourne sur 2 215 users, donc la latence de *première observation* d'un TWAP créé
pendant la fenêtre live est p25 = 3,9 min, médiane = 171 min. Un système live dédié à une
watchlist courte ferait ~1 min. **Conséquence préenregistrée : tout mécanisme doit être testé
avec un décalage d'entrée de 0 / 5 / 15 / 30 / 60 min. Un mécanisme qui ne survit qu'à lag 0
sera déclaré `DATA_LIMITED` (non déployable avec l'infrastructure de poll réelle).**

## 1. Univers, prix, coûts

- **Track A (exécutable Binance)** : TWAP HL dont le `coin` mappe sur un perp Binance USDM
  présent dans `/home/qbee/futur-data-v2/data_v2/normalized/perp_ohlcv` (barres 5 min).
  Mapping : `@*`, `xyz:*`, `*/USDC` exclus (produits HL-only) ; `kXXX → 1000XXXUSDT` ;
  sinon `COIN+USDT`. **155 symboles, 278 072 événements (69,9 % des TWAP)**.
  Prix Binance disponibles **2024-02 → 2026-08-01** ⇒ les TWAP d'août 2026 (58 121) sont hors
  panel Binance et servent de réserve, pas de test.
- **Track B (HL natif)** : `trades` / `l2` / `ctxs`, 12 coins, 2026-07-18 → 2026-08-29.
- **Coûts** : convention projet `net_bps = gross_bps − 14`, stress `− 28`. Exécution sur
  Binance ⇒ ces coûts sont les bons. Aucun mécanisme sous-cost ne sera appelé mieux que `WEAK`.

## 2. Construction PIT (règles fixées d'avance)

- `event_time = state.timestamp` (ms de création du TWAP).
- Entrée : **open de la première barre 5 min dont `open_time >= event_time + LAG`**, LAG ∈
  {0, 5, 15, 30, 60} min. Le LAG principal préenregistré est **15 min** (compromis réaliste
  entre p25=3,9 min et médiane=171 min du collecteur actuel) ; LAG 0 est rapporté en
  robustesse haute et LAG 60 en robustesse basse.
- Sortie : close de la barre 5 min contenant `event_time + LAG + H`, pour H ∈ {15 min, 30 min,
  1 h, 2 h, 4 h, 24 h} et H = `minutes` (fin programmée du TWAP).
- Notionnel planifié PIT : `sz × close de la dernière barre 5 min close avant event_time`.
- **Aucun champ terminal (`executedNtl`, `final_status`) n'entre dans un signal.** Ils ne
  servent qu'à décrire l'échantillon.

## 3. Statistique primaire : jamais « versus zéro »

Le marché crypto a une dérive inconditionnelle forte. Trois statistiques sont produites, la
**deuxième et la troisième font foi** :

1. `signed_raw` = `dir × r_coin`, `dir = +1` si `side='B'` sinon `−1`. (Descriptif seulement.)
2. `signed_mktneutral` = `dir × (r_coin − r_mkt)` où `r_mkt` = rendement equal-weight des 155
   symboles de l'univers sur **exactement la même fenêtre**. Neutralise la dérive et le facteur
   marché.
3. `spread_BA` = `mean(r_mktneutral | side=B) − mean(r_mktneutral | side=A)` sur la même
   population — le contraste de bras exigé par le briefing §1.3.

## 4. Déclustering — 3 niveaux, décidés d'avance

- **L1 (unité fine naturelle)** : `user × coin × jour` — le piège explicitement signalé dans ma
  mission : le flux d'un même wallet sur un même jour est **un** épisode.
- **L2 (même-symbole / 24 h)** : `coin × jour calendaire`.
- **L3 (macro)** : `jour calendaire`, tous coins confondus.

`t_stat_declustered` **primaire = t sur les moyennes journalières (L3)**, le niveau le plus
conservateur. `bootstrap_ci95` = block-bootstrap, **blocs = jour calendaire**, 2 000 tirages.
Le t sur L1 et L2 est rapporté à titre indicatif seulement.

## 5. Hypothèses préenregistrées

| id | hypothèse | prédiction signée | seuil de succès |
|---|---|---|---|
| **T1** | *Drift pendant l'exécution* : le prix dérive dans le sens du TWAP entre la création et la fin programmée | `signed_mktneutral > 0` | net_bps>0 ET stress28>0 ET t(L3)≥3 |
| **T2** | *Reversion après la fin* : impact temporaire ⇒ le prix revient après la fin du TWAP | `signed_mktneutral` de fin→fin+durée `< 0` | idem, sur le fade |
| **T3** | *Scaling en taille* : l'effet croît avec `size_ratio = notionnel planifié / volume Binance 24 h glissant du symbole` | monotone croissant en quintiles | quintiles définis sur TRAIN (2024-02→2025-08), appliqués sur TEST (2025-09→2026-07) |
| **T4** | *Imbalance TWAP agrégée* : le notionnel TWAP net signé encore à exécuter dans un coin prédit le rendement forward | IC > 0 stable | IC signe stable sur les 2 moitiés de TEST |
| **T5** | *reduceOnly vs opening* : les TWAP non-reduceOnly (ouverture de position) sont plus informés | `spread(nonRO) > spread(RO)` | contraste de bras, pas vs zéro |
| **T6** | *Users informés* : les users dont les TWAP passés (TRAIN) ont un drift post-création positif restent prédictifs en TEST | `spread(top cohort) > spread(reste)` | split chronologique strict |
| **T7** | *Durée* : les TWAP courts (≤15 min) sont plus urgents donc plus informés que les longs (≥240 min) | contraste court−long > 0 | contraste de bras |
| **T8** | *Lead-lag HL→Binance* (Track B) : le rendement HL précède Binance d'un décalage exploitable | lag argmax de la corrélation croisée | **question mesurée, pas un pari** : si lag < 60 s ⇒ non exécutable avec cette stack, verdict honnête |
| **T9** | *Dislocation HL/Binance* : `(mark_HL − mid_Binance)/mid` z-scoré prédit une reversion | z élevé ⇒ rendement HL−Binance négatif | net>0 après coûts sur **deux jambes** (28 bps) |
| **T10** | *Funding HL vs Binance* : divergence de funding comme signal de positionnement | contraste de bras | idem |
| **T11** | *Imbalance de carnet L2 HL persistante* | contraste de bras | 42 j ⇒ attendu `DATA_LIMITED` |

## 6. Capacité (colonne exigée par la mission)

- Track A (exécution Binance) : `capacity_usd_estimate` = 0,5 % du volume quote Binance cumulé
  du symbole sur la fenêtre de détention, médiane sur les épisodes.
- Track B (exécution HL) : `capacity_usd_estimate` = médiane de `bid_depth_usd`/`ask_depth_usd`
  du carnet HL au moment de l'événement (données `l2` directes).

Un edge dont la capacité médiane est < 25 000 USD par épisode sera étiqueté explicitement
`capacity-limited` dans le REPORT même si son bps est bon.

## 7. Grille de verdicts (celle du briefing §3, aucune invention)

`VALIDATED_FOR_FORWARD` / `PROMISING_NEEDS_VALIDATION` / `UNCONFIRMABLE_IN_HORIZON` /
`COST_FRAGILE` / `REGIME_DEPENDENT` / `WEAK` / `DEAD` / `DATA_LIMITED`.

`n_required` = N indépendant pour power 80 %, alpha 5 % bilatéral, sur un edge **haircuté de
50 %** : `n = (1.96+0.84)^2 × sd^2 / (0.5×edge)^2`. `event_rate` mesuré sur les 6 derniers mois
disponibles (2026-02 → 2026-07). `eta = n_required / event_rate`.

## 8. Ce que je m'interdis

- Ajuster un seuil après avoir vu un résultat sans l'étiqueter `REFIT`.
- Utiliser `executedNtl` / `final_status` comme feature.
- Promouvoir un mécanisme dont l'edge disparaît à LAG 15 min.
- Écrire quoi que ce soit hors de mon dossier ; intermédiaires en scratch, < 1 Go, nettoyés.
