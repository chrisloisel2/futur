# PARALLEL_50 — ranker câblé : 50 cryptos battent-elles les 9 ? (100K, données réelles)

> Réponse : **NON.** Même avec le ranker fait correctement, trader 50 cryptos rapporte
> **+10,6 % (vs +18,2 % pour 9)**, avec **3× le drawdown**. L'alpha directionnel supplémentaire
> des alts est mangé par les frais. La baseline 9-actifs reste le meilleur book.

## Setup

100 000 $, 2022-11-03 → 2026-06-28 (~3,5 ans). Même config (asset_regime_gate + flip_exit +
intra_gov + carry BTC/ETH 0,50 + hedge). Ranker câblé dans `multileg_backtester.py`
(`enable_ranker` : caps 2/bucket, 1 meme, 5 alt). TRM (BTC/ETH) + LIQUIDATION (data-gated)
identiques partout. Variable = univers PULLBACK (4 vs 49) + mode de sélection.

## Résultats

| config | gain 100K | ROI | /an | PF | maxDD | legs |
|---|---:|---:|---:|---:|---:|---:|
| **BASELINE_9** | **+18 186 $** | **+18,2 %** | +4,8 % | 1,03 | **−1,7 %** | — |
| NAIVE_50 (tout exécuté) | −39 582 $ | −39,6 % | — | 0,92 | −43,7 % | — |
| RANKED3 (caps, max 3, même risque) | −34 182 $ | −34,2 % | — | 0,93 | −38,7 % | 6404 |
| **RANKED7 (max 7, taille/nom réduite, gross borné)** | **+10 585 $** | **+10,6 %** | +2,9 % | 1,01 | −5,3 % | 4991 |
| RANKED3_FEE *(filtre coût INERTE — voir ci-dessous)* | −34 182 $ | −34,2 % | — | 0,93 | −38,7 % | 6404 |

### Décomposition PnL (la mécanique)

| | directionnel | carry | hedge | fees | NET |
|---|---:|---:|---:|---:|---:|
| baseline-9 | +6 098 | +27 751 | +85 | −15 719 | **+18 186** |
| RANKED7 | +16 706 | +25 218 | −578 | −30 735 | **+10 585** |

- L'univers élargi capte **×2,7 d'alpha directionnel** (+16 706 vs +6 098) → les alts ne sont
  pas faux.
- **Mais frais ×2 (−30 735)** → mangent quasiment tout le surplus directionnel.
- Carry quasi inchangé (dimensionné equity). Net : expansion ~neutre-à-négative après coûts.

## Trois enseignements honnêtes

1. **Les caps bucket/meme SEULS ne coupent pas le churn** (RANKED3 = −34 %, 6404 legs ≈ naïf).
   Le slot libéré se remplit du prochain alt → fréquence d'entrée identique. La diversification
   change *quel* alt, pas *combien* de trades.
2. **Ce qui a sauvé RANKED7 = la plus petite taille par nom** (gross borné), pas le ranker en
   soi : moins de frais par trade, exposition étalée → +10,6 %, DD −5,3 %.
3. **Le filtre coût testé est INERTE** : `long_min_er_cost_mult` compare `expected_return` (une
   CONSTANTE 0,025 codée dans le moteur) à 2×frais (0,0028) → jamais déclenché. RANKED3_FEE ≡
   RANKED3. **Le vrai levier coût n'a pas encore été exercé** (il faut un `expected_return`
   par-signal = P(up)×move attendu, pas une constante).

## Verdict

```
PARALLEL_50_NAIVE   : REJECTED (-39.6%, churn)
PARALLEL_50_RANKED3 : REJECTED (-34.2%, caps insuffisants seuls)
PARALLEL_50_RANKED7 : POSITIVE mais INFÉRIEUR baseline (+10.6% < +18.2%, DD 3×)
```

**Conclusion : élargir à 50 cryptos ne crée PAS d'alpha net de frais.** Le surplus directionnel
réel des alts est consommé par les coûts de transaction. La baseline 9-actifs (focus + carry
BTC/ETH) reste le meilleur portefeuille risque-ajusté. Recommandation : **ne pas trader 50.**

**Seul levier non encore testé** qui pourrait changer la donne : un filtre coût RÉEL par-signal
(rejeter les pullbacks alts dont l'edge attendu ne couvre pas les frais) — pourrait conserver
le +16,7K de directionnel tout en coupant la moitié des frais. À tester avant tout enterrement
définitif de l'univers élargi.
