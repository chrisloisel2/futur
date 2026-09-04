# ALPHA PORTFOLIO — CE QUI EST RÉELLEMENT UTILISABLE

Établi le 2026-09-03 après la wave 2 de l'Alpha Validation Factory.
Source de vérité : `configs/validation_registry.yaml` + `configs/live_alpha_registry.yaml` +
`WAVE2_SCOREBOARD.md`. Toute ligne ci-dessous est traçable à un `RESULTS.json`.

---

## 1. La réponse courte

**Deux alphas standalone reposent sur une validation indépendante.** Un troisième groupe (2
overlays) est validé mais n'a pas de véhicule d'exécution câblé. Tout le reste est soit rejeté,
soit jamais validé indépendamment, soit un mécanisme réel sans produit tradeable.

| # | Alpha | Statut réel | Edge validé | Utilisable ? |
|---|---|---|---|---|
| 1 | `AMIHUD_ILLIQUIDITY_PREMIUM_V1` | FROZEN · SIGNAL_SHADOW · validé wave 1 | +105,7 bps/rebal, N=332 | **Oui** — seul standalone câblé |
| 2 | `BTC_LEAD_ALT_CASCADE` | validé wave 2, **à figer** | +46,9 bps/event, t 3,32, N_L3=259 | **Oui, après freeze** |
| 3 | `LIQ_REPEAT_DENSITY` | validé wave 1, overlay | +22,1 bps, N=1165 | Overlay — **non câblé** |
| 4 | `LIQ_REPEAT_SKEW_OVERLAY` | validé wave 1, **signe inversé** | direction corrigée, N=579→187 | Overlay — **non câblé** |

Tout le reste : voir §4 (rejeté) et §3 (le trou de fondation).

---

## 2. Trois corrections à la liste de départ

### 2.1 `LIQ_CASCADE_REPEAT_V1` n'a JAMAIS été validé indépendamment

C'était le rang 🥈 « ✅ Frozen / live · meilleur event alpha ». Il est bien `FROZEN` /
`SIGNAL_SHADOW` dans `live_alpha_registry.yaml` — mais **il n'apparaît nulle part dans
`validation_registry.yaml` comme candidat**. Il n'y figure que comme `existing_live_alpha`
des overlays construits par-dessus. Aucune réimplémentation indépendante n'a jamais été faite.

C'est le problème structurel n°1 du portefeuille, parce que **les rangs 3, 4 et 20 sont des
overlays SUR lui** : leur valeur est entièrement conditionnelle à une fondation non testée.

Ce qu'on sait indirectement, via la wave 2 : le fade **inconditionnel** des cascades LONG
mesuré au niveau épisode donne **+20,15 bps net14** (gap 4 h, 2 926 épisodes) — donc la famille
porte bien quelque chose. Mais `LIQ_CASCADE_REPEAT_V1` trade la *répétition* (n≥2), pas le fade
inconditionnel : c'est une spec distincte, non mesurée ici.

### 2.2 Les deux overlays validés ne sont pas lançables tels quels

- `LIQ_REPEAT_DENSITY` : le validateur wave 1 a écrit noir sur blanc **« ne PAS lancer comme
  alpha standalone »** — c'est une couche de sizing/filtre sur le flux de décisions de
  `LIQ_CASCADE_REPEAT_V1`. Le mécanisme d'overlay **n'est pas câblé dans le pipeline live**.
  Edge réel mais moitié du claim (+22,1 vs +39,5 annoncé), ETA ~9,4 ans.
- `LIQ_REPEAT_SKEW_OVERLAY` : **correction de signe obligatoire**. Le rapport de découverte avait
  la direction inversée (bug de labellisation) : c'est le skew **complaisant**, pas put-heavy,
  qui prédit le plus fort taux de répétition. 8/8 perturbations confirment la direction corrigée.
  Pas de véhicule d'exécution direct, non câblé, ETA conservateur ~46 ans. **Ne jamais reprendre
  le signe du rapport d'origine.**

### 2.3 Les chiffres « edge observé » de la liste étaient des bruts, pas des excess

Trois réclamations cross-sectionnelles mesuraient leur rendement **contre zéro** au lieu de
contre leur propre univers éligible. Le brut reproduit bien le chiffre publié ; l'excess
s'effondre :

| Réclamation | brut (vs zéro) | **excess (vs univers)** | t_L3 |
|---|---|---|---|
| RESIDUAL_MOMENTUM_14D (+64,8 annoncé) | +225,2 | **+31,5** | 0,63 |
| MOMENTUM 14D_LO (+199,3 annoncé) | +254,5 | **+51,8** | 0,85 |
| SECTOR_ROTATION_7D (+103,0 annoncé) | +89,8 | **+13,3** | 0,81 |

Aucun n'est significatif une fois le bras B soustrait.

---

## 3. Le problème de concentration : ce ne sont pas 20 paris, c'en est 2

En regroupant par **famille de risque économique** — deux alphas de la même famille tombent
ensemble :

| Famille | Membres de la liste | Validé indépendamment |
|---|---|---|
| `LIQ_CASCADE_DETECTOR` | rangs 2, 3, 4, 5, 11, 12, 18, 20 | **1** (BTC_LEAD, 22,5 % de chevauchement avec REPEAT_V1) |
| `CROSS_SECTIONAL_XSMOM` | rangs 1, 6, 7, 8 | **1** (AMIHUD) |
| `OI_STATE_FAMILY` | rang 10 | 0 (mécanisme oui, produit non) |
| `OPTIONS_DERIBIT_BTC` | rangs 4, 14, 15, 16, 17 | 0 |
| `CALENDAR_BASIS_CURVE` | rang 9 | 0 |
| `CROWDING` / `PREMIUM` | rangs 18, 19 | 0 |

**Huit des vingt lignes sont le même pari sur les cascades de liquidation.** Quatre autres sont
le même pari cross-sectionnel. Compter 20 alphas donne une illusion de diversification que la
structure de corrélation ne supporte pas.

### Le mur commun : aucun alpha validé n'est reconfirmable en forward

| Alpha validé | ETA conservateur de reconfirmation |
|---|---|
| AMIHUD_ILLIQUIDITY_PREMIUM_V1 | ~17,1 ans |
| BTC_LEAD_ALT_CASCADE | ~9,7 ans |
| LIQ_REPEAT_DENSITY | ~9,4 ans |
| LIQ_REPEAT_SKEW_OVERLAY | ~46 ans |

Aucun ne peut être confirmé par significativité fréquentiste dans un horizon utile. Le monitoring
forward doit juger sur **survie du signe, du coût et du mécanisme**, jamais sur une
significativité fraîche. C'est une propriété structurelle de ces edges (rapport signal/bruit
faible × fréquence modeste), pas un défaut de protocole.

---

## 4. Liste complète re-classée

### ✅ Utilisables

| Rang | Alpha | Edge validé | Ce qu'il faut savoir |
|---|---|---|---|
| **1** | `AMIHUD_ILLIQUIDITY_PREMIUM_V1` | +105,7 bps/rebal hebdo, N=332 | Magnitude concentrée en 2021 (ex-2021 : t=1,72, direction survit à +50,9) ; **2025 quasi-plat (−0,2 bps)** ; ETA 17 ans. Seul standalone déjà câblé. |
| **2** | `BTC_LEAD_ALT_CASCADE` | +46,9 net14 / +32,9 net28, t 3,32, N_L3=259 | Passe les 5 critères dans les deux conventions de pondération. Le split signé non préenregistré confirme le mécanisme (choc BTC baissier +46,4 / haussier −8,9). **2025 négatif (−40 bps)**, hors 2024 t tombe à 1,41. Chevauchement REPEAT_V1 : 22,5 %. **→ à figer et lancer en shadow.** |

### 🔧 Validés mais pas lançables en l'état

| Rang | Alpha | Blocage |
|---|---|---|
| 3 | `LIQ_REPEAT_DENSITY` | Overlay sur REPEAT_V1, mécanisme d'overlay non câblé ; interdiction explicite de lancer en standalone |
| 4 | `LIQ_REPEAT_SKEW_OVERLAY` | Idem + **signe à inverser** avant tout câblage ; pas de véhicule d'exécution direct |

### 🟡 Mécanisme confirmé, produit non tradeable

| Rang | Alpha | Résultat |
|---|---|---|
| 10 | `SHORT_COVERING_CONTINUATION` | Excess vs baseline **+17,06 bps, t 2,94–3,22 sur trois unités de cluster**, 4/5 années positives, les 3 dernières les plus fortes. Mais le long seul rend **+2,53 bps pour 14 bps de coût** (t=0,41) et −11,47 au stress. Le score `min()` de la spec live sélectionne exactement la même population → **reconstruction fidèle**. Valeur = overlay/filtre cross-sectionnel, pas long directionnel. Statut live inchangé. |

### 🔬 À reprendre (preuve insuffisante, pas contredite)

| Rang | Alpha | Résultat |
|---|---|---|
| 12 | OI-collapse bounce | Bras seul +18,3 (t 2,74) MAIS **A−B = −0,39 au niveau épisode** : le conditionnement n'ajoute rien au fade inconditionnel qui porte déjà l'edge. Le +247 annoncé ne se reproduit à aucune convention (max +50,8). |
| 18 | Premium extreme → cascade | Seul contraste A−B positif dans les deux conventions (+21,4 épisode) mais t=1,455 sous le seuil. Contrôle de direction propre (queue haute nulle). Preuve concentrée sur quelques gros épisodes. |

### ❌ Rejetés

| Rang | Alpha | Motif |
|---|---|---|
| 11 | Far-from-local-low liquidation | **Alpha live touché.** Reproduit exactement le freeze_spec (+6,84/+20,21 OOS) — aucun bug — mais SE sous-estimée ×1,9–2,6, t tombe à 0,90. Le **signe dépend de l'unité de pondération** → « far bat near » n'est établi dans aucune direction. `far − baseline` négatif à tous les gaps ≥ 1 h. → `DOWNGRADE_LIVE_STATUS` |
| 6 | SECTOR_ROTATION_7D | Excess +13,3 (t 0,81), net28 négatif, s'effondre à ≥5 membres/secteur (−1,6) et hors 2021 (+2,7) |
| 7 | RELATIVE_LEVERAGE_14D | **Non testé** — panel OI notionnel non assemblé |
| 8 | RESIDUAL_MOMENTUM_14D | Corr de rang 0,951 avec le momentum brut, Jaccard 0,82 : **pas un facteur distinct**. Le strip de beta *dégrade* de −21,5 bps |
| 13 | CVD-shock down memory | Contraste A−B **négatif** et significatif (−19,4, Welch −2,66) |
| 19 | Crowd washout sans cascade | 1/5 années positives (annoncé 6/7 stable) ; dataset de 2 200 événements = contrainte dure |
| — | SECTOR_RELATIVE_STRENGTH_REVERSAL | −21,7 vs +46,4 annoncé ; corr 0,86 avec mom7 → momentum inversé |

### ⏸️ Non tranchés dans cette vague

| Rang | Alpha | Raison |
|---|---|---|
| 9 | Funding vs quarterly 30D | `FUNDING_BASIS_DISAGREEMENT_V2` est FROZEN/SIGNAL_SHADOW mais **jamais validé indépendamment**. Données `binance_vision_quarterly` disponibles. |
| 14 | DVOL shock memory | N=30–51 annoncé ; données Deribit disponibles, non branchées |
| 15-17 | Overlays options RV/IV, far-OTM put, block flow | Fusionnés dans `VOL_FORECAST_LAYER_V1` (FROZEN/SIGNAL_SHADOW), **jamais validés indépendamment** — même trou que REPEAT_V1, à une échelle moindre |
| 20 | LIQ vol regime gate | Tranché wave 1 : `NEEDS_MORE_RESEARCH`, 949 trades → 268 épisodes de régime, ETA 28–38 ans |

---

## 5. Ce que je ferais dans l'ordre

1. **Figer et lancer `BTC_LEAD_ALT_CASCADE`** en SIGNAL_SHADOW — c'est le seul gain net de la
   wave 2, avec une alerte explicite sur le régime 2025.
2. **Valider `LIQ_CASCADE_REPEAT_V1`** — c'est la priorité absolue devant tout nouveau candidat.
   Trois lignes du portefeuille en dépendent et sa fondation n'a jamais été testée. Le harnais
   `_lib/` couvre déjà exactement cette famille.
3. **Descendre `LIQ_CASCADE_FAR_FROM_LOW_V1`** en `INVALIDATED_PENDING_RESPEC` (sans couper la
   collecte forward, qui reste informative). Ne PAS basculer sur le bras `near` : ce serait
   choisir la direction après avoir vu le résultat.
4. **Valider `VOL_FORECAST_LAYER_V1`** (rangs 15-17) — trois signaux live sur zéro validation
   indépendante.
5. Ensuite seulement : brancher les sources manquantes pour #7, #9, #14.

Ne rien attendre du reste sans nouvelle preuve.
