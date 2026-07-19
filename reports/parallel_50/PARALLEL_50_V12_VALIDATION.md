# PARALLEL_50 V1.2 — validation RANKED7_EDGE + frontière de sizing (2026-07-03)

> Résultat : la config **V1.2 candidate (RANKED7_EDGE + long×1.5 + carry 75%) fait
> +33,6 % sur 3,65 ans (≈ +8,6 %/an), maxDD −2,3 %, PF 1,03** — près du double de la
> baseline V1.1 (+18,2 %, +4,8 %/an) dans le MÊME budget de risque (DD ≤ 3 %).
> Aucun nouveau moteur, aucun tuning de gate : uniquement la validation demandée par
> `PARALLEL_50_EDGE_GATE_WIN.md` + la mesure de deux frontières de sizing jamais testées.

Scripts : `run_parallel_50_edge_validation.py`, `run_parallel_50_carry_frontier.py`,
`run_parallel_50_v12_candidate.py`. Données réelles, 100K, 2022-11-03 → 2026-06-28.
Gate edge INCHANGÉ partout (min_net=0, min_signals=20 — la variante non-tunée).

## 1. Validation RANKED7_EDGE (les 3 checks exigés avant promotion)

| check | résultat | verdict |
|---|---|---|
| reproduction | +21,7 %, DD −1,5 % — identique au rapport initial | ✓ |
| ventilation annuelle | 2022* −0,4 · 2023 +4,1 · 2024 +12,5 · 2025 +3,3 · 2026 +1,1 | ✓ 4/5 positives |
| coûts ×2 (taker 10, slip 4, maker 2) | **+6,3 %**, PF 1,01, DD −2,6 % | ✓ survit |

*2022 = fenêtre partielle (nov-déc, bear). → **RANKED7_EDGE = VALIDÉ**, prêt pour la
suite de maturité complète puis paper.

## 2. Frontière sizing long (long_fraction, base 0.043)

| config | ROI | maxDD | directionnel | verdict |
|---|---:|---:|---:|---|
| ×1.0 (base) | +21,7 % | −1,5 % | +11 689 | référence |
| **×1.5 (0.0645)** | **+23,5 %** | −2,4 % | +17 536 | ✓ PASS |
| ×2.0 (0.086) | +18,9 % | **−3,9 %** | +18 860 | ✗ ROI baisse ET DD>3 % |

**La frontière long est à ×1.5.** Au-delà, le churn/hedge mange le gain marginal et le
DD sort du budget. Le directionnel sature (+18,9K à ×2 vs +17,5K à ×1.5).

## 3. Frontière carry (carry_fraction, base 0.50 — palier >50 % jamais mesuré)

| config | ROI | maxDD | carry PnL | gross carry |
|---|---:|---:|---:|---:|
| 0.50 (base) | +21,7 % | −1,5 % | +27 606 | 2,0× |
| 0.65 | +27,5 % | −1,4 % | +36 725 | 2,6× |
| **0.75** | **+31,5 %** | **−1,4 %** | +43 035 | 3,0× |

**Le scaling carry reste linéaire et le DD ne bouge PAS** (delta-neutral : PnL = funding,
les jambes prix s'annulent). Confirme et étend la mesure de `tune_portfolio_v1.py`
(20→35→50 % linéaire) — le rendement dormait dans la fraction carry.

## 4. Config combinée V1.2 (long 0.0645 + carry 0.75) — mesurée, pas supposée

| config | gain 100K | ROI | /an | PF | maxDD | verdict |
|---|---:|---:|---:|---:|---:|---|
| BASELINE_9 (V1.1 officielle) | +18 186 $ | +18,2 % | +4,8 % | 1,03 | −1,7 % | référence |
| RANKED7_EDGE (validé §1) | +21 700 $ | +21,7 % | +5,7 % | 1,03 | −1,5 % | PASS |
| **V12_CANDIDATE** | **+33 610 $** | **+33,6 %** | **+8,6 %** | 1,03 | **−2,3 %** | **PASS** |
| V12_COSTX2 (stress) | +8 693 $ | +8,7 % | +2,3 % | 1,01 | −3,9 % | survit, DD>3 % ⚠ |

Par année (V12_CANDIDATE) : 2022* −0,6 % · 2023 +6,1 % · **2024 +19,0 %** · 2025 +4,7 % ·
2026 +1,6 %. PnL : directionnel +18 663, carry +43 060, fees −27 626, hedge −442.

## Réserves d'honnêteté (à lever avant promotion)

1. **Margin non modélisé** : gross au pic ≈ 1,5× spot + 1,5× perp short (carry) + 0,45
   longs ≈ **3,4× equity**. Exécution réelle = portfolio margin obligatoire (spot
   collatéralise le perp). Le backtester ne modélise ni margin call ni liquidation de
   la jambe perp en spike. À documenter/simuler avant paper.
2. **Basis PnL non modélisé** (jambes pricées au même prix) — déjà documenté
   (`carry_return_reconciliation.md`), conservateur, mais l'effet croît avec la taille.
3. **Stress coûts ×2 : DD −3,9 % > 3 %.** Aux coûts réels (5 bps taker) le DD est −2,3 % ;
   sous coûts doublés le ROI reste positif (+8,7 %) mais le budget DD est dépassé.
4. **Concentration carry** : ~65 % du PnL vient du funding BTC/ETH. Un régime de funding
   durablement négatif (2022-style prolongé) ramène V1.2 vers le rendement du book long.
5. Sizing ×1.5/carry 0.75 choisis APRÈS avoir vu la grille → risque léger de sélection.
   Mitigé par : mécanisme monotone explicable, sensibilité documentée des deux côtés
   (×2 échoue, 0.65 marche), gates edge non touchés.

## Le chemin du rendement — arithmétique honnête

V1.2 ≈ **+0,7 %/mois moyen** (médiane mensuelle +0,21 %) à DD −2,3 %. Le ratio
rendement/DD ≈ 3,7×/an par unité de DD est la vraie monnaie du système. Pour viser plus :
- **Plus d'alpha** (pas plus de taille) : moteur événementiel liquidations
  (`futur-derivatives` collecte, 0 event à date), seul candidat offensif sur vraie donnée.
- **Budget DD** : passer le gate de 3 % à X % scale le rendement ~linéairement (décision
  de politique de risque, pas de modèle) — mais PF 1,03 impose un plafond structurel.
- **Temps** : paper forward 30-60j sur V1.2 (comme V1.1) pour débloquer micro-live.

## Verdict

```
RANKED7_EDGE          : VALIDÉ (repro ✓, 4/5 années ✓, coûts×2 ✓)
LONG_FRONTIER         : ×1.5 = frontière (×2 REJECTED : ROI↓, DD 3,9 %)
CARRY_FRONTIER        : 0.75 linéaire, DD insensible (margin à modéliser)
V12_CANDIDATE         : +33,6 % / −2,3 % DD / PF 1,03 → PASS gates backtest
STATUT                : PAPER_CANDIDATE — suite maturité + doc margin requis avant paper
```

Baseline officielle V1.1 **intacte** (figée, paper-live actif). V1.2 = candidate, rien
n'est promu sans suite de maturité + fenêtre paper dédiée.
