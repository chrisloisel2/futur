# Protocole PRÉ-ENREGISTRÉ — STABLECOIN_REGIME v0 (méta-signal de liquidité)

Date de gel : 2026-07-18. **Une seule exécution autorisée** (même discipline que le
protocole options-4h 1d06580 → 684f497). Tout FAIL classe cette variante
`NO_EDGE` définitivement ; aucun re-tuning des seuils après lecture des résultats.
Statut avant exécution : aucun calcul signal→cible n'a été effectué ; seules les
probes de disponibilité data (codes HTTP, profondeur, granularité) ont été faites.

## 1. Hypothèse économique

L'offre agrégée de stablecoins est le collatéral marginal de l'écosystème crypto.
Une contraction nette (redemptions) et/ou un depeg des majors traduit un retrait de
liquidité qui précède les épisodes de volatilité élevée et de drawdown ; une
expansion accompagne les régimes bénins. On teste un **MÉTA-SIGNAL de régime**
(overlay de sizing du portefeuille), pas un alpha directionnel. La direction
BTC/ETH est une cible **secondaire** pré-déclarée, non gating.

## 2. Données (OBSERVÉ 2026-07-18, HTTP 200 réels)

| Source | Contenu | Profondeur |
|---|---|---|
| `stablecoins.llama.fi/stablecoincharts/all` | supply agrégée par pegType, quotidien | 3 154 pts, 2017-11-29 → 2026-07-18 |
| `…/stablecoincharts/all?stablecoin=1` (USDT) | supply agrégée cross-chain | 3 154 pts, 2017-11-29 → |
| `…?stablecoin=2` (USDC) | idem | 2 868 pts, 2018-09-11 → |
| `…?stablecoin=5` (DAI) | idem | 2 434 pts, 2019-11-19 → |
| `…/stablecoinprices` | prix quotidiens par gecko_id | 2 021 pts, 2020-12-30 → 2026-07-18 (1er pt epoch-0 purgé) |
| BTC/ETH enriched (local) | close horaire → resample 1d | 2017-08 → 2026-07-18 |
| Jambes portefeuille (local, cachées) | `v12_equity_daily.parquet`, `{LIQ_CASCADE,CROWDING_REVERSAL,PREMIUM_DISLOCATION}_MH_trades.parquet`, `basis_term_equity_daily.parquet` | v12 2022-11→2026-07-10 ; basis 2021-02→2026-03-31 |

Combiné = produit des équités quotidiennes normalisées, logique **exacte** de
`measure_v12_plus_stack_overlay.py --tapes mh`, fenêtre = intersection des jambes
à partir de 2023-01-01 (attendu ≈ 2023-01 → 2026-03-31 avec le parquet basis actuel).

### Limites data (honnêtes)

- L'API DefiLlama rend l'historique **actuel**, pas un instantané archivé point-in-time.
  Risque de révision faible pour la supply (dérivée on-chain) mais non nul. Mitigation :
  univers restreint à USDT+USDC+DAI (présents sur toute la fenêtre, dominants —
  184,2 + 73,4 + 4,9 Md$ au 2026-07-18), pas d'agrégat all-assets en primaire
  (biais d'inclusion rétroactive d'assets récents).
- Points datés 00:00 UTC ; le point daté J est traité comme connu au plus tôt à
  J+1 (délai +1 jour appliqué partout ; variante +2 jours en robustesse, pré-déclarée).
- Prix (depeg) disponibles seulement depuis ≈2021 → composante depeg inactive avant.
- Trio complet seulement depuis 2019-11-19 (naissance DAI) ; features trio z365
  utilisables dès ≈2020-12. Features USDT-seul utilisables dès ≈2019-01.

## 3. Features (8, figées — aucune autre ne sera calculée)

z365 = z-score roulant 365 j (min 180 j). Aucun paramètre optimisé.

- F1 = z365(Δlog supply trio 7 j) ; F2 = z365(Δlog supply trio 30 j)
- F3 = z365(Δlog USDT 7 j) ; F4 = z365(Δlog USDT 30 j)
- F5 = depeg jour = min(P_USDT, P_USDC, P_DAI) − 1
- F6 = depeg 7 j = min roulant 7 j de F5
- F7 = part USDT du trio (niveau) ; F8 = Δ30 j de F7

Variante secondaire pré-déclarée (reportée, non gating) : F2b = z365(Δlog 30 j de
l'agrégat all-peggedUSD).

## 4. Cibles (figées)

Primaires :
- RV BTC 7 j et 30 j futures (std des rendements 1d × √365) ;
- maxDD 30 j futur du combiné 3 jambes ;
- stress = 1{rendement 7 j futur du combiné < quantile 10 % roulant 730 j passé (min 365 j)}.

Secondaires (non gating) : signe du rendement BTC 7 j/30 j futur, idem ETH.

## 5. Tests statistiques (une passe)

- IC Spearman feature(J) → cible sur [J+1, J+1+h], délai +1 j (variante +2 j).
- Inférence : block-bootstrap 90 j, 2 000 tirages, p bilatéral.
- Split chronologique figé : train → 2023-12-31, test 2024-01-01 → fin.
- Une feature est « retenue » si |IC| ≥ 0,15 avec p < 0,01 sur la fenêtre complète
  ET même signe train/test avec p < 0,05 dans chaque segment.
- 8 features × 4 cibles primaires = 32 tests : la famille exige au moins
  une feature primaire à p < 0,0016 (0,05/32) sur la fenêtre complète.

## 6. Règle overlay candidate (figée — LA règle jugée)

RISK_OFF à J+1 si **(F2 < −1,0) OU (F6 < −0,005 pendant ≥ 3 jours consécutifs)**
→ gross ×0,5, maintenu jusqu'à sortie de condition + 5 jours (hystérèse fixe).
Sinon gross ×1,0.

Coûts de bascule : 10 bps (×1) / 20 bps (×2) par unité de notional tourné, soit
5/10 bps d'équité par bascule (|Δe| = 0,5 ; approximation gross ≈ 1×, documentée —
conservatrice vs taker perp 2,5-5 bps).

Voisinage (stabilité, PAS optimisation — seule la cellule centrale est jugée) :
seuil z ∈ {−0,75, −1,0, −1,25} × multiplicateur ∈ {0,25, 0,5, 0,75}.

## 7. Critères PASS/FAIL (figés)

PASS overlay exige TOUS :
1. maxDD combiné réduit ≥ 20 % relatif (coûts ×1) et ≥ 15 % (coûts ×2) ;
2. ROI annuel net conservé ≥ 85 % du ROI de base (×1 et ×2) ;
3. Sharpe overlay ≥ Sharpe de base ;
4. ≥ 5 épisodes RISK_OFF distincts sur la fenêtre, PnL amélioré vs base sur ≥ 50 % des épisodes ;
5. ≤ 24 bascules/an ;
6. voisinage : ≥ 6/9 cellules avec DD réduit ET ROI ≥ 80 % de la base ; aucune cellule avec ROI < base − 20 % relatif ;
7. volet statistique : F2 ou F6 « retenue » (critère §5) sur ≥ 1 cible primaire.

FAIL sur n'importe lequel → `NO_EDGE` stablecoin-overlay v0, classement définitif
de cette variante. Les composantes non testées ici (flux par chaîne, lending,
DEX depth) resteraient des pistes distinctes, à protocole séparé — pas un
re-tuning de celle-ci.

## 8. Effet attendu (HYPOTHÈSE, pas promesse)

Si PASS : réduction du maxDD du combiné (~3 % → ~2,4 % ou mieux) au prix d'une
fraction ≤ 15 % du ROI, règle explicable et désactivable, activable en shadow
uniquement après décision humaine. Aucune modification de `strategies/`, des
configs de production, du shadow ni du sizing dans le cadre de ce test.
