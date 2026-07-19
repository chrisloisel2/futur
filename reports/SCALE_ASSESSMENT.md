# Scale Assessment — vers 40-80K/an sur 100K (2026-06-28)

Évaluation honnête du plan 10-phases après exécution du **Bloc A** (épaissir/réparer
les sleeves existants) et de la **fondation derivatives (Phase 4)**.

## Bloc A — résultats mesurés (`reports/tune_portfolio_v1.json`)

| Config | ROI total | annualisé | PF | maxDD |
|---|---:|---:|---:|---:|
| V1 base (carry 20%) | +7.6% | +1.6% | 1.02 | −2.0% |
| V1 réparé (3×fees + univers BTC/ETH/SOL) | +7.0% | +1.5% | 1.02 | −2.5% |
| **V1 carry 35%** | +12.0% | +2.6% | 1.02 | −2.1% |
| **V1 carry 50%** | **+17.2%** | **+3.6%** | 1.02 | **−1.9%** |

**Constats :**
1. **Réparation long book = inefficace** : le filtre 3×fees + univers ne lève pas le PF (1.02).
   Les longs régime-gatés sont **structurellement minces / quasi break-even** → contributeur
   **défensif**, pas générateur de rendement. Ne pas sur-investir à les réparer.
2. **Carry sizing = LE levier** : 20%→50% lève le rendement +7.6%→+17.2% (ann. +3.6%) en gardant
   DD ~2% (delta-neutral). → **V1.1 = carry 50%** ≈ palier 1-2 (~3.6%/an, ~3.6K sur 100K).
3. **Plafond honnête** : carry + longs défensifs **ne dépassent pas ~5-8%/an**. **40-80K/an est
   hors de portée sans moteurs OFFENSIFS** (liquidation, breakout), exactement comme le plan le dit.

## Phase 4 — fondation derivatives : le VRAI blocage est la DONNÉE

`scripts/build_derivatives_store.py` → `DERIVATIVES_STORE_GATE : FAIL` (honnête) :

| Donnée | Disponible ? |
|---|---|
| Liquidations historiques (long/short $) | **NON — nulle part** |
| Open interest | BTC seulement, 2021-2025 (réel, exploitable) ; **alts : non** |
| Taker buy/sell ratio | BTC seulement, **2020-2022 only** |
| Funding rate | OK multi-actifs (déjà utilisé par le carry) |
| OI 2026 | **NON** |

→ **Le chemin vers 40-80K/an n'est pas bloqué par le modèle, mais par l'ACQUISITION de données.**
Les moteurs offensifs (Phase 5 Liquidation Event-First, Phase 6 Breakout dérivés) ont besoin d'OI +
liquidations multi-actifs que nous n'avons pas. Les fabriquer serait gonfler un backtest.

## Ce qui est honnêtement constructible MAINTENANT

- **V1.1 carry 50%** : +3.6%/an, DD 1.9%, robuste multi-régime → premier candidat paper sérieux.
- **BTC-only OI-deleveraging event engine** : seul moteur offensif buildable sur données réelles
  (OI BTC 2021-2025, chutes d'OI 1h jusqu'à −2.8% = deleveraging). Mono-actif, sans 2026 → contribution
  limitée mais réelle. C'est le prochain edge à tester (PF≥1.40 gate).

## Recommandation (ordre réel, honnête)

1. **Lancer le paper-live V1.1 (carry 50%)** — observation 30-60j (le runner est l'étape suivante).
2. **ACQUISITION DE DONNÉES** = action n°1 pour 40-80K : capturer en continu le flux `forceOrder`
   (liquidations) + OI multi-actifs Binance (ou fournisseur payant historique). Sans ça, pas de
   Liquidation/Breakout crédibles.
3. En attendant, construire le **BTC OI-deleveraging engine** (données réelles) comme premier offensif.
4. Ne **pas** scaler le système défensif (carry+long) au-delà de son palier pour simuler de l'agressif.

## Verdict paliers (révisé, honnête)

| Palier | Constructible aujourd'hui ? | Objectif/100K |
|---|---|---:|
| 1 — V1 carry 20% | ✓ | ~1.6K/an |
| 2 — V1.1 carry 50% | ✓ | ~3.6K/an |
| 3 — + Liquidation (BTC OI proxy) | partiel (BTC only) | +qq K, à prouver |
| 4 — + Breakout dérivés | ✗ (données) | bloqué data |
| 5 — 40-80K/an | ✗ | **exige acquisition données + 6-12 mois de preuve live** |

**Règle respectée** : on n'a pas scalé un système défensif faible pour simuler de l'agressif.
Le rendement viendra d'**edges supplémentaires sur données réelles**, pas du sizing seul.
