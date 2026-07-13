# Recherche stratégies rentables + 2 leviers mesurés + preset ADAPTIVE (2026-07-12)

Objectif de session : inventaire des stratégies crypto les plus rentables (industrie +
littérature académique), mapping sur le projet, implémentation des leviers les plus
prometteurs, déploiement dans le paper live. Exigence utilisateur : 5%/mois.

## 1. Inventaire des stratégies (recherche 2026-07-12)

| famille | rendement documenté | statut dans futur/ |
|---|---|---|
| Funding carry Δ-neutre | 5-15%/an (compressé depuis 2021 : 30-50% → 5-15%) ; SSRN Chan 2025 : 16%/an Sharpe 6,1 (tick-level, levier) ; Sharpe carry devient NÉGATIF en 2025 (arXiv 2510.14435) | ✅ cœur V1.1/V1.2 — nos mesures (+0,37%/mo BTC) cohérentes avec la compression |
| Basis cash-and-carry trimestriel | ~5-10%/an, positif en bear | ✅ BASIS_TERM +8,4%/an mesuré, positif chaque année 2021-2026 |
| Momentum cross-sectionnel (LTW JF 2022, CTREND JFQA 2024) | long-short 1-4 sem significatif ; CTREND survit aux coûts sur coins liquides | ❌ testé ce jour (long-only) : REJETÉ — DD −83%, négatif 2025/2026 (voir §3) |
| Événementiel réversion sur stress (cascades liquidation) | pas de chiffre public consolidé ; edge documenté épisodiquement | ✅ notre stack 3 moteurs +2,9%/an à 2% sizing — notre différenciateur data |
| Stat-arb / pairs cointégrés | Sharpe 1,6-2,5 académique (BTC-ETH 16%/an) — MAIS échantillons courts, coûts 20bps optimistes | ❌ non testé ; proche parent (BTC_SPILLOVER) mort en 2024-25 |
| Market making / HFT LOB | réel mais exige colocation + infra tick ; papers LOB transformers (TLOB 2025) : le préprocessing domine l'architecture | ❌ hors périmètre infra |
| Vol risk premium (options Deribit) | covered calls BTC = yield synthétique documenté (Anchorage 2026, 37k backtests) | ❌ pas d'infra options ; piste future sérieuse |
| RL trading | SSRN 2025 : PPO SOUS-performe buy&hold net de coûts (Sharpe 1,23 vs 1,46) | ❌ écarté — la littérature honnête est négative |
| LLM sentiment | Sharpe ~3 revendiqué sur actions (GPT-3, 2021-23) ; crypto non répliqué proprement | 🟡 collecteur news/F&G accumule ; edge non validé walk-forward |
| ML supervisé sur features (XGBoost/LightGBM) | prédictibilité réelle surtout small-caps (Liu et al. 2023, Cakici 2024) | ✅ c'est notre harnais event engines |

Lecture d'ensemble : les rendements "certains" élevés publiés sont soit compressés
(carry), soit à échantillon court/survivance (stat-arb, momentum), soit exigent une
infra qu'on n'a pas (MM, options). Le différenciateur défendable du projet reste la
**donnée événementielle propriétaire** (liquidations live + metrics 5-min Vision).

## 2. Levier #1 mesuré : breadth circuit-breaker → REJETÉ

`scripts/run_wave_portfolio_breadth_breaker.py` — règle pré-déclarée depuis
TRADE_HISTORY_ANALYSIS (skip si >15 symboles distincts en 60 min, union des flux
complets 3 moteurs, causal ; sensibilités 10/20/25).

| config | ROI 23→26 | %/an | maxDD | PF |
|---|---:|---:|---:|---:|
| baseline (sans breaker) | +10,6% | +2,9% | −3,5% | 1,126 |
| breaker >15 (primaire) | +5,5% | +1,5% | −3,2% | 1,078 |

Dégrade **chaque année**, y compris 2022 et 2026 (années de krach). La
wave-unitization top-3/vague absorbait déjà la concentration des jours de krach ;
couper la breadth haute coupe aussi les rafales profitables (PF 1,31 per deep dive).
7e confirmation de la règle : *filtre localement motivé ≠ alpha portefeuille*.

## 3. Levier #2 mesuré : momentum cross-sectionnel hebdo → REJETÉ en l'état

`scripts/backtest_xs_momentum.py` — top-5 équipondéré, lookback 28j skip 1j, gate
BTC>MA20j, 30 bps A/R, univers enriched 50, long-only (SHORT interdit projet).

Primaire : **+41%/an brut sur 2017-2026 MAIS maxDD −83%**, 2025 **−29%**, 2026
**−38%**. Le facteur académique est réel historiquement (2020 +425%, 2021 +421%,
2023 +298% — gonflés par le biais de survivance de l'univers choisi en 2026) mais le
régime récent est destructeur. Confirme le diagnostic répété du repo : le
directionnel long alts ne paie plus net de frais depuis 2025. Non déployable sous
budget DD≤3% ; ne pas retuner (fishing).

## 4. Implémenté + déployé : preset `adaptive` (paper live 200k€)

Leçon LEVERAGE_FRONTIER/preset-max opérationnalisée : l'allocation optimale dépend
des CONDITIONS COURANTES (funding/basis live vs borrow 8%/an), pas d'un preset figé.

Règles (`paper_portfolio.initialize_strategy(preset="adaptive")`) :
- remplissage glouton par yield live décroissant, budget spot ≤ 1×E sans borrow ;
- levier au-delà de 1×E accordé SEULEMENT si yield live > borrow + 2% ;
- plancher 1%/an (vire les sleeves morts) ;
- longs par régime : BULL 40% / RECOVERY 25% / sinon 0 (+ double gate trend MA20 au mark).

Déployé 2026-07-12 au marché courant : carry BTC 0,40E (+6,1%/an) + basis BTC 0,20E
(+3,5%/an) + longs gatés 0,40E · **gross 1,0× · borrow 0** · carry/basis ETH refusés
(1,2-1,6%/an) · tout levier refusé (< borrow+2%). Domine "max" (borrow drag au
funding bas) et "aggressive" (poids morts ETH) ; converge vers "max" automatiquement
si le funding normalise. `alloc_note` exposé dans `/api/portfolio/live` + bouton UI.

## 5. Face à l'exigence 5%/mois — état mesuré, sans blabla

- Frontière reproductible mesurée : **overlay 3 jambes +21,8%/an brut (~18-19%
  honnête après borrow) ≈ 1,5%/mois, maxDD ~3,2%** — la trajectoire du projet est
  0,4 → 0,7 → 0,95 → 1,65%/mois en 4 itérations.
- Les 2 leviers candidats du jour ont été mesurés et rejetés honnêtement (règle :
  jamais gonfler un backtest). Aucun moteur ≥5%/mois reproductible n'existe à ce
  jour dans le projet ni, net de coûts et à échantillon honnête, dans la littérature
  publique consultée.
- Trajectoire crédible pour continuer à monter : (1) verdict shadow J+30 des event
  engines (~2026-08-09) puis intégration multileg ; (2) learning curve des moteurs
  événementiels (la data liquidations s'accumule, retrain hebdo automatique) ;
  (3) régime de funding : si le funding normalise (euphorie), le book adaptatif
  lève automatiquement le carry — c'est dans ces fenêtres que 3-5%/mois devient
  atteignable par le carry levé ; (4) piste nouvelle la plus sérieuse identifiée par
  la recherche : vol risk premium options (Deribit) — infra à construire.
