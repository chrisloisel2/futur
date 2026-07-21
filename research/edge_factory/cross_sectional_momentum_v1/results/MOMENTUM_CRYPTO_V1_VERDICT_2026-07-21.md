# Verdict — MOMENTUM_CRYPTO_V1

Formule unique préenregistrée (voir `PREREGISTRATION_CRYPTO_V1_ADDENDUM.md`),
testée une seule fois (`n_trials=1`, aucune grille), univers crypto-only
32 noms (31 classés + BTC hedge), 2020-04-29 → 2026-06-30.

Détail machine-readable :
[MOMENTUM_CRYPTO_V1_BACKTEST_2026-07-21.json](MOMENTUM_CRYPTO_V1_BACKTEST_2026-07-21.json).

## Résultat du backtest complet

| Gate (seuils utilisateur) | Valeur | Seuil | Verdict |
|---|---|---|---|
| CAGR net ×1 | −66,9 %/an | > 12 % | ❌ FAIL |
| Sharpe ×1 | −1,22 | > 1,2 | ❌ FAIL |
| maxDD | −99,9 % | < 15 % | ❌ FAIL |
| Coûts ×2 | −68,9 %/an | net positif | ❌ FAIL |
| Leave-one-year | toutes années négatives (−62 % à −71 %) | positif | ❌ FAIL |
| Concentration max par actif | 14,2 % (ZECUSDT) | ≤ 15 % | ✅ PASS (de justesse) |
| DSR | 0,0013 | > 0 (littéral) | ⚠️ PASS technique, sans valeur — 700× sous le seuil réel du programme (0,95) |
| PBO | non calculé | ≤ 0,10 | n/a — une seule formule, pas de grille |

Toutes les années 2020-2026 sont individuellement négatives (−31 % à −93 %).
Le résultat brut (avant coûts et funding) est déjà négatif : CAGR brut
−55,9 %/an, Sharpe brut −0,87 — ce n'est pas un effet de coûts ou de
churn qui détruit un signal par ailleurs correct.

## Pourquoi : diagnostic IC (pas juste un artefact de compounding)

Avant d'accepter le P&L composé comme verdict final, vérification par
Information Coefficient quotidien (corrélation de rang cross-sectionnelle
entre le signal et le rendement du jour suivant, sur les 2253 jours,
32 noms) :

| Horizon momentum | IC moyen (brut) | t-stat | IC moyen (résiduel bêta) | t-stat |
|---|---|---|---|---|
| 7 jours | −0,0203 | **−3,18** | −0,0189 | −2,98 |
| 30 jours | −0,0059 | −0,90 | −0,0088 | −1,35 |
| 90 jours | −0,0098 | −1,49 | −0,0083 | −1,28 |

L'IC à 7 jours est négatif et statistiquement significatif (t = −3,18) —
et va dans le même sens (négatif) à 30 et 90 jours, résidualisé ou non.
**Ce n'est pas un bug de résidualisation bêta** : le signal — construit
avec ou sans la neutralisation bêta — prédit systématiquement l'inverse
de la continuation à l'horizon quotidien. C'est un résultat honnête, pas
un artefact de portefeuille peu profond (le test IC porte sur tout
l'univers chaque jour, pas sur le portefeuille long-short concentré).

Note de diagnostic écartée du verdict : une variante "momentum brut sans
résidualisation, équipondérée" a été testée en aparté et affiche un CAGR
brut de +183 %/an — un chiffre invraisemblable, révélateur d'un livre
très concentré (n_per_leg ≈ 1 à 6 noms selon l'année, cf. taille de
l'univers) porté par un ou deux gagnants extrêmes, pas un facteur robuste.
Le test IC ci-dessus, sur l'univers complet plutôt que sur ce portefeuille
concentré, est la mesure fiable — et il est négatif.

## Verdict

**NO_EDGE — momentum court/moyen terme (7-90j), rebalance quotidien, sur
cet univers crypto de 31 noms.** Le signal est en réalité un signal de
**reversal** (retournement), pas de continuation, à ces horizons et cette
fréquence. Aucun gate ne passe de façon significative.

## Piste ouverte pour plus tard (non poursuivie maintenant)

La littérature académique classique (Jegadeesh-Titman et suite) construit
le momentum sur des rendements **mensuels avec une période de "skip"**
(généralement 1 semaine à 1 mois entre la fenêtre de formation et l'entrée)
précisément pour éviter la contamination par le reversal court terme — ce
que cette v1 n'a pas fait (rebalance quotidien, aucun skip). C'est une
thèse *différente*, pas un retuning des mêmes seuils, et elle n'est pas
lancée ici : la priorité reste de rapporter ce résultat honnêtement plutôt
que d'enchaîner immédiatement sur une nouvelle variante.
