# Protocole PRÉ-ENREGISTRÉ — FUNDING_XVENUE v0 (récolte du différentiel de funding cross-venue)

Date de gel : 2026-07-18. **Une seule exécution autorisée** (même discipline que
640802c → 019f86d). Tout FAIL classe cette variante `NO_EDGE` définitivement ;
aucun re-tuning des seuils après lecture des résultats. Le gel est matérialisé
par le commit de ce fichier + du script `scripts/test_funding_xvenue_v0.py`
AVANT toute exécution.

## 0. Ce qui a été calculé AVANT le gel (transparence totale)

Uniquement des statistiques **descriptives marginales** du différentiel brut
HL−Binance (aucune règle, aucun coût, aucun PnL, aucun signal→cible) :

- quantiles, moyenne annualisée, AR(1) quotidien, moyennes par année, pour
  BTC/ETH/SOL sur 2024-01 → 2026-06-28 (n=2727 fenêtres 8 h par coin) ;
- une première passe boguée (jointure sur timestamps exacts, ~65 % des fenêtres)
  a été détectée et remplacée par l'alignement par intervalle de settlement réel.

Résultat de la probe (différentiel HL−Binance, bp/8 h, moyenne annualisée) :
BTC +7,5 %/an (2024 +12,3 / 2025 +5,5 / 2026 +2,1), ETH +5,8 %/an (2026 +3,6),
SOL +7,8 %/an (2026 −0,4). Médiane ≈ +0,4 bp/8 h, p25 ≈ 0, AR(1) quotidien ≈ 0,6.
Les seuils du §4 sont calibrés sur cette **distribution marginale** (assumé et
documenté) — pas sur le PnL de la règle, qui n'a jamais été calculé.

Le rapport antérieur `BINANCE_BYBIT_FUNDING_EDGE_REPORT.md` (2026-06) a montré
un spread Binance↔Bybit médian de 0,00 bp (p99 abs 3,37 bp/8 h, en déclin
annuel) : cette paire de venues est **rétrogradée en secondaire non-gating**.
Son usage comme gate du carry existant (CARRY_GATE_V2) est un usage distinct,
non concerné par ce protocole.

## 1. Hypothèse économique

Le funding d'un même perp diverge entre venues parce que les flux y sont
différents : biais long retail structurel sur Hyperliquid (funding horaire,
formule premium), flux plus institutionnel sur Binance (settlement 8 h). Une
paire **short perp sur la venue qui paie le plus + long perp sur l'autre**,
même coin, même notional, encaisse le différentiel sans exposition
directionnelle durable. C'est l'extension adjacente du moteur carry validé
(spot+perp mono-venue) : même famille delta-neutre, aucune course de latence,
fréquence lente. Aucun short nu : chaque short est apparié à un long de même
notional sur le même sous-jacent.

Risque principal assumé : le différentiel décroît d'année en année (compression
par le capital d'arbitrage, cf. §0) — le test juge précisément si le régime
récent reste récoltable après coûts.

## 2. Données (OBSERVÉ 2026-07-18, fichiers locaux de la branche free-derivatives-backfill)

| Source | Contenu | Profondeur |
|---|---|---|
| `data/derivatives_backfill/binance/funding/{SYM}.parquet` | funding réalisé 8 h + mark_price, 9 symboles | 2021-01-01 → 2026-06-28 |
| `data/derivatives_backfill/hyperliquid/funding/{BTC,ETH,SOL}.parquet` | funding réalisé **horaire** + premium | 2024-01-01 → 2026-07-17 |
| `data/derivatives_backfill/bybit/funding/{SYM}.parquet` | funding réalisé 8 h, 9 symboles | 2022-11-03 → 2026-06-28 (cap 4000 pts API) |
| `data/enriched/{SYM}USDT_1h_enriched.parquet` (close) | prix 1 h pour contrôle liquidation | 2017-08 → 2026-07 |
| `reports/liq_cascade/{v12,basis_term}_equity_daily.parquet` | équités moteurs existants (corrélation, secondaire) | — |

Univers primaire : **BTC, ETH, SOL × Binance↔Hyperliquid**, fenêtre
2024-01-01 → 2026-06-28 (fin = dernier settlement Binance backfillé).
OKX exclu (292 pts, 2026-03 → seulement). Bybit secondaire non-gating.

### Limites data (honnêtes)

- Les timestamps de settlement Binance portent des millisecondes → arrondi à
  l'heure obligatoire ; l'alignement se fait **par intervalle entre deux
  settlements réels consécutifs** (robuste aux changements de cadence).
- Les taux utilisés sont les funding **réalisés** (settlés) — point-in-time par
  nature, non révisables. Aucun taux prédit n'est utilisé.
- Pas de L2 historique ni de mark perp horaire cross-venue → slippage et bruit
  de basis perp-perp figés en adders conservateurs (§5), stressés ×2.
- Le contrôle de liquidation utilise les closes 1 h (sous-estime marginalement
  les extrêmes intra-heure ; accepté, documenté).

## 3. Définitions (figées)

- Pour chaque intervalle entre deux settlements Binance consécutifs (t₋, t] :
  `d_t = Σ funding_HL horaires dans (t₋, t] − funding_Binance(t)`, en bp/8 h.
- Annualisation : %/an = bp/8 h × 3 × 365 / 100.
- Convention de signe : d > 0 ⇒ HL paie plus ⇒ la paire « short HL + long
  Binance » encaisse d. Position symétrique si d < 0.
- Notional N par jambe, levier 2× par jambe ⇒ **capital requis = N** (marge
  N/2 × 2 jambes, pas de netting cross-venue). Tous les rendements sont
  rapportés sur le capital requis, pas sur le notional de paire.

## 4. LA règle jugée (figée — cellule centrale)

- Signal : S_t = moyenne glissante de d sur **21 settlements (7 j)**, annualisée,
  connue à t, position effective sur l'intervalle suivant (t, t+1] — décalage
  d'un settlement, aucun lookahead.
- Entrée : |S| ≥ **4 %/an** → position sign(S). Sortie : |S| ≤ **1 %/an** → flat
  (hystérèse). Flip de signe = sortie + entrée (coûts des deux).
- Neutralité : jambes 1:1 en notional, même coin. Le drift de notional entre
  jambes (écart de prix perp-perp < 0,5 % sur majors) est couvert par un
  re-hedge forfaitaire inclus dans le drag (§5) ; marge égalisée entre venues
  chaque semaine (168 h) en position.
- Voisinage (stabilité, PAS optimisation — seule la cellule centrale est
  jugée) : lookback ∈ {9, 21, 42} settlements × θ_in ∈ {2, 4, 6} %/an, avec
  θ_out = θ_in/4. 9 cellules.

## 5. Modèle de coûts (figé, coûts ×1 ; le stress ×2 double tout)

| Poste | Valeur ×1 |
|---|---|
| Fill taker Binance perp (VIP0) | 5 bp × N par fill |
| Fill taker Hyperliquid (tier base) | 5 bp × N par fill |
| Slippage par fill (majors, taille petite) | 2 bp × N |
| Bruit de basis perp-perp par aller-retour | 3 bp × N |
| Drag en position (transferts marge + re-hedge drift) | 1 bp × N / mois |

Aller-retour de paire = 4 fills ⇒ **31 bp × N ×1** (14 entrée + 14 sortie + 3
basis), **62 bp ×2**. Aucune hypothèse maker (conservateur : tout en taker).
Bybit secondaire : taker 5,5 bp par fill, reste identique.

## 6. Critères PASS/FAIL (figés — PASS exige TOUS)

1. Fenêtre complète, coûts ×2 : net > 0 pour **≥ 2 coins sur 3** (Binance↔HL).
2. Sous-périodes calendaires 2024 / 2025 / 2026-YTD, agrégat équipondéré
   3 coins : net ×2 > 0 dans **chacune**.
3. Année glissante 2025-07-01 → 2026-06-28, agrégat : net ×1 ≥ **3 %/an** sur
   capital requis ET net ×2 > 0 (plancher de pertinence : sous 3 %/an le moteur
   ne vaut pas un slot).
4. Sécurité des jambes : aucun mouvement adverse ≥ **35 %** par jambe entre deux
   égalisations hebdo de marge en position (closes 1 h) — sinon liquidation
   simulée à 2× ⇒ FAIL.
5. Churn : ≤ **26 allers-retours/an** par coin (cellule centrale) — au-delà, les
   coûts fixes dominent et le signal n'est pas celui postulé.
6. Concentration : aucune fenêtre de 30 j > **50 %** du PnL net ×1 agrégat.
7. Stress funding-flip : différentiel uniformément réduit de 25 % (d × 0,75) →
   net ×1 ≥ 0 sur la fenêtre complète, agrégat.
8. Voisinage : ≥ **6/9** cellules net ×2 > 0 (agrégat fenêtre complète) ;
   aucune cellule net ×1 < −2 %/an.

FAIL sur n'importe lequel → `NO_EDGE` funding-xvenue v0, classement définitif de
cette variante. Les variantes non testées (OKX quand la profondeur existera,
exécution maker, autres coins HL) seraient des protocoles séparés — pas un
re-tuning de celui-ci.

## 7. Secondaires pré-déclarés (reportés, NON gating)

- Binance↔Bybit, même règle, mêmes coûts (attendu ≈ mort — médiane 0,00 bp).
- Corrélation du PnL quotidien de la règle avec `v12_equity_daily` et
  `basis_term_equity_daily` (seuil de commentaire : 0,5 — au-delà, le bénéfice
  de diversification est à discuter, sans invalider le PASS).
- Part du temps en position, décomposition du net par année, PnL par épisode.

## 8. Effet attendu (HYPOTHÈSE, pas promesse) et suite

Compte tenu de la décroissance observée (§0), l'issue la plus probable est un
verdict serré autour des critères 2-3 : brut 2026 ≈ +2-4 %/an sur BTC/ETH,
négatif sur SOL. Si PASS : phase **paper 30 j** alimentée par le collecteur
ctxs 60 s déjà en service (6e3215e) + funding Binance live, avant tout capital ;
cap exchange-risk Hyperliquid pré-déclaré ≤ 25 % du capital du moteur ; toute
activation reste une décision humaine. Aucune modification de `strategies/`,
des configs de production, du paper 200k ni du sizing dans le cadre de ce test.
