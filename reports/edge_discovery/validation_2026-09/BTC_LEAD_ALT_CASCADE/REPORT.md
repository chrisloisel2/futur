# BTC_LEAD_ALT_CASCADE — Rapport de validation indépendante

**Validateur :** worker V2, Alpha Validation Factory wave 2, 2026-09-03
**Réclamation :** `round3/w1_event_sequences/REPORT.md` w1_a12 — restreindre le fade des cascades
de liquidation alt au décile supérieur de `|btc_ret_30m|` donne net14 **+33,02** / net28 +19,02,
PF 1,41, N_raw 3 489, N_indep 3 097, positif 5/6 années.
**Discipline d'indépendance :** `w1_event_sequences/evidence/` n'a jamais été ouvert ; seul le
REPORT.md de découverte a été lu. Réimplémentation entière depuis la définition économique.
**Scripts :** `../_lib/exp_v2_cascade.py`, `../_lib/exp_event_weighted_cluster.py`,
`../_lib/validation_lib.py`. Spec figée dans `PREREGISTRATION.md` avant tout chiffre de rendement.

---

## 1. Méthodologie

**Population A** (`data/events/cascade_dataset.parquet`) : `kind == LONG_CASCADE`,
`symbol != BTCUSDT` (BTC ne peut pas se « précéder » lui-même), `label_full == True`,
`fwd_4h` non nul, `event_time >= 2022-01-01 UTC`, `btc_ret_30m` non nul.
→ **n = 26 750 événements, 48 symboles, 2022-01-01 → 2026-08-27.**
Rendement de référence inconditionnel de cette population : **+11,26 bps bruts** (−2,74 net14).

**Règle de choc — causale, préenregistrée.** `shock(t) := |btc_ret_30m| ≥ q90(t)`, où `q90(t)` est
le 90e centile de `|btc_ret_30m|` sur les événements de la population A dans `[t − 365 j, t)`,
avec ≥ 200 événements antérieurs exigés (sinon exclu). **Le centile n'est jamais calculé
in-sample** — la version in-sample de la découverte est reléguée en perturbation BLA-P1.
→ 2 485 événements `shock`, 24 065 `no_shock`, 200 écartés faute d'historique suffisant.

**Trade :** LONG le bras `shock`, horizon `fwd_4h`, une jambe par événement, équipondéré.
Coût : net14 = brut − 14, net28 = brut − 28.

## 2. Checklist de vérification

| Contrôle | Résultat |
|---|---|
| Causalité de la règle de seuil | q90 calculé sur une fenêtre glissante strictement antérieure `[t−365 j, t)`. Aucune information postérieure. |
| Causalité de la feature | `btc_ret_30m` est une variation de prix implicite BTC sur les 30 min **précédant** l'événement (as-of backward, `create_time <= event_time`). |
| Fuite de sélection | Les 200 premiers événements sont **écartés** (historique < 200), jamais imputés. |
| Unités | `fwd_4h` en décimal → ×1e4 en bps. Vérifié : moyenne de population 0,001126 = 11,26 bps. |
| Déclustering | Appliqué aux 3 niveaux, voir §4. **C'est le point critique de ce candidat.** |
| Chevauchement live | Mesuré contre le ledger `LIQ_CASCADE_REPEAT_V1`, voir §5. |
| Bras A − bras B | Testé (`shock − no_shock`), jamais « A > 0 ». |
| Reproduction sur dataset alternatif | BLA-P6 sur `liq_cascade_dataset.parquet` : +46,58 net14, t_L3 3,26 — reproduit. |

## 3. Résultat primaire et perturbations

Moyennes d'**épisode** (unité d'inférence L3), coût 14 bps :

| Spec | net14 | net28 | t_L3 | N_L3 | N_raw | boot p05 | années + |
|---|---|---|---|---|---|---|---|
| **PRIMARY — `shock` seul** | **+46,87** | +32,87 | **3,315** | 259 | 2 485 | +23,70 | 4/5 |
| Bras B — `no_shock` | +17,52 | +3,52 | 6,193 | 2 875 | 24 065 | +12,92 | 5/5 |
| **T2 — `shock` − `no_shock`** | **+29,35** | — | Welch 2,04 | — | — | P(diff≤0)=0,019 | — |
| BLA-P1 décile in-sample | +43,95 | +29,95 | 3,372 | 307 | 2 675 | +22,20 | 4/5 |
| BLA-P2 **down**-shock | +46,36 | +32,36 | 3,226 | 254 | 2 491 | +22,92 | 4/5 |
| BLA-P2 **up**-shock | −8,94 | −22,94 | −1,577 | 1 282 | 2 803 | −18,16 | 2/5 |
| BLA-P4 quintile (q80) | +30,78 | +16,78 | 3,658 | 536 | 4 870 | +16,97 | 4/5 |
| BLA-P6 dataset historique | +46,58 | +32,58 | 3,260 | 256 | 2 435 | +23,59 | 4/5 |
| BLA-P7 hors meilleure année (2024) | +22,90 | +8,90 | 1,407 | 186 | 1 791 | −3,63 | 3/4 |

Année par année (net14) : 2022 **+24,0** · 2023 **+79,9** · 2024 **+108,0** · 2025 **−40,0** ·
2026 **+48,3**. PF 1,782. Pire épisode −536 bps. Drawdown cumulé max −3 302 bps.

**BLA-P2 est le contrôle économique décisif** : le mécanisme prédit que c'est le choc BTC
**baissier** qui porte l'effet (déleveraging forcé corrélé, qui se retourne quand la pression
passe). C'est exactement ce qu'on observe — down +46,4 (t 3,23), up −8,9 (t −1,58). Une
réclamation dont le sens économique se vérifie sur un split non utilisé pour la construire.

## 4. Déclustering — et pourquoi le t publié était surestimé

| Niveau | Définition | N |
|---|---|---|
| brut | événements du bras `shock` | 2 485 |
| L1 | même symbole, chaîne < 24 h | 2 239 |
| L2 | jour calendaire UTC | 261 |
| **L3 (inférence)** | **épisode cross-symbole chaîné, gap < 4 h** | **259** |

Un choc BTC est un événement **market-wide** : des dizaines d'alts cascadent dans les mêmes
minutes. Le déclustering same-symbol de la découverte (N_indep 3 097) laisse donc N surestimé
d'environ **12×**. Statistique de référence, moyenne pondérée par événement (l'estimateur du P&L
réel par trade) avec erreur-type **cluster-robuste** sur les épisodes :

| Bras | n | net14 | t naïf | **t cluster-robuste** | inflation SE | boot p05 |
|---|---|---|---|---|---|---|
| `shock` | 2 485 | +41,70 | 4,10 | **1,85** | ×2,22 | +5,72 |
| `no_shock` | 24 065 | −6,93 | −4,09 | −1,84 | ×2,22 | −12,91 |
| `shock − no_shock` | — | **+48,63** | — | **2,17** | — | — |

L'erreur-type est sous-estimée d'un facteur 2,2 par le comptage naïf. **L'edge survit quand même :**
t = 1,85 ≥ 1,645 en pondération événement, t = 3,32 en pondération épisode, et le contraste
A − B tient dans les deux conventions (2,17 et 2,04). C'est ce qui distingue ce candidat de son
voisin FAR_FROM_LOW, où le même traitement fait tomber le t sous le seuil.

## 5. Chevauchement avec les alphas live

| Contrôle | Résultat |
|---|---|
| `LIQ_CASCADE_REPEAT_V1` (ledger, lecture seule) | 558 / 2 485 événements du bras `shock` appariés à ±5 min = **22,45 %** |
| Critère S5 (≤ 50 %) | **PASSÉ** — le mécanisme n'est pas un doublon du détecteur repeat-cascade |

## 6. Capacité

Événementiel, 48 symboles perp Binance liquides, ~1,15 événement/semaine sur le bras `shock`,
horizon 4 h. La contrainte de capacité est le nombre d'événements, pas la profondeur de carnet :
les noms concernés sont des perps majeurs et un book de $300k par jambe est très en deçà de toute
participation problématique. Non chiffré plus finement ici (noté comme tel).

## 7. Fréquence, N_required, ETA

| Champ | Valeur |
|---|---|
| `historical_event_rate` (2 ans, épisodes L3) | 1,227/semaine |
| `recent_event_rate` (6 mois) | 1,154/semaine |
| `conservative_event_rate` | 1,154/semaine |
| `n_required_statistical` (bootstrap par bloc, α = 5 % unilatéral, puissance 80 %, edge haircuté 50 %) | **585 épisodes** |
| `minimum_calendar_days` | 60 |
| `eta_p50` | 3 337 j (**~9,1 ans**) |
| `eta_conservative` | 3 549 j (**~9,7 ans**) |
| `confirmable_in_horizon` (< 3 ans) | **False** |

## 8. Verdict

**`VALIDATED_FOR_FORWARD`** — tag secondaire **`UNCONFIRMABLE_IN_HORIZON`**.

| Critère | Résultat |
|---|---|
| S1 `shock` seul : net14 > 0, t_L3 ≥ 1,645, bootstrap P(moyenne ≤ 0) < 5 % | **PASSÉ** (+46,87, t 3,32, p05 +23,7 ; +41,70 / t 1,85 en pondération événement) |
| S2 `shock − no_shock` > 0, t ≥ 1,645, P(diff ≤ 0) < 5 % | **PASSÉ** (+29,35, Welch 2,04, P = 0,019 ; +48,63 / t 2,17 clusterisé) |
| S3 net28 > 0 | **PASSÉ** (+32,87) — non fragile au coût |
| S4 ≥ 4/5 années positives et ex-meilleure-année > 0 | **PASSÉ** (4/5 ; ex-2024 +22,90) |
| S5 chevauchement `LIQ_CASCADE_REPEAT_V1` ≤ 50 % | **PASSÉ** (22,45 %) |

`sign_correction_required` : **non**. La direction réclamée est confirmée, et le split signé
non préenregistré par la découverte (down vs up) va dans le sens du mécanisme économique.

**Réserves à porter au registre.**
1. **2025 est négatif (−40,0 bps)** — la seule année négative, et c'est l'année la plus récente
   complète. À surveiller en priorité en forward.
2. Hors meilleure année (2024), t tombe à 1,407 et le p05 bootstrap passe à −3,63 : une part
   notable de la magnitude vient de 2024. L'edge reste positif partout sauf 2025, mais il n'est
   pas uniformément réparti.
3. **ETA de reconfirmation ~9,7 ans** : comme AMIHUD (17 ans), ce candidat ne peut pas être
   reconfirmé en forward par significativité fréquentiste dans un horizon utile. Le monitoring
   forward doit juger sur **survie du signe, du coût et du mécanisme** sur le plancher 60 j,
   pas sur une significativité fraîche.
4. La découverte publiait t sur N_indep 3 097 ; l'inférence honnête porte sur 259 épisodes.
   Magnitude confirmée (+46,9 vs +33,0 réclamé), **preuve 12× plus mince que publiée**.

**`recommended_next_step` : `FREEZE_AND_LAUNCH_SHADOW`** — figer la spec (règle de choc causale
q90/365 j, LONG, fwd_4h, LONG_CASCADE alts) et lancer en SIGNAL_SHADOW, avec le monitoring
orienté survie-du-signe décrit ci-dessus, et une alerte explicite sur le régime 2025.
