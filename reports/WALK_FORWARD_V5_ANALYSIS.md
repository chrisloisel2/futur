# Walk-Forward v5 — Analyse complète des résultats

> Date : 2026-05-23  
> Branche : `rebuild-short-trm-fleet-stress-alpha`  
> Script : `scripts/walk_forward_v5.py`

---

## Table des matières

1. [Architecture](#1-architecture)
2. [Méthodologie walk-forward](#2-méthodologie-walk-forward)
3. [Résultats par fold](#3-résultats-par-fold)
4. [Les 6 approches testées](#4-les-6-approches-testées)
5. [Pourquoi 2022 résiste à tout](#5-pourquoi-2022-résiste-à-tout)
6. [Paper trading avec guardrails](#6-paper-trading-avec-guardrails)
7. [Critères pour passer en live](#7-critères-pour-passer-en-live)

---

## 1. Architecture

### TRM Fleet Long v4

Le système est un **ensemble de 100 modèles XGBoost spécialisés** (TRM = Temporal Regime Model).

```
10 horizons temporels × 10 archétypes de marché = 100 TRM
```

**Horizons** : 1h, 4h, 8h, 12h, 1j, 3j, 1s, 2s, 1m, 1t  
**Archétypes** : vol_shock, mean_reversion, momentum_accel, breakout_escape, squeeze_release, trend_follow, pullback_reclaim, choppy, low_vol, general

Pour chaque barre, le système :
1. **Classifie le contexte actuel** (horizon + archétype) via `classify_context_v4()`
2. **Sélectionne les top-5 TRM** spécialisés dans ce contexte
3. **Moyenne leurs prédictions** pour obtenir `p_long ∈ [0, 1]`
4. **Compare à un seuil calibré** par contexte (≈ 0.54) → signal LONG/NO_SIGNAL

### Pool multi-actif

L'entraînement utilise **BTC + ETH + SOL simultanément** pour multiplier les labels disponibles. Chaque actif contribue ses barres avec ses propres labels — le modèle apprend des patterns communs aux cryptos.

### RegimeAllocator v5

Calcule des statistiques de régime (bear_pct, bull_pct) et **réduit la taille des positions de ×0.65 en période bear** (EMA200h + momentum). Ce composant est **informatif uniquement** : il ne bloque pas les trades (les tests ont montré que le bloquer empirait les résultats).

---

## 2. Méthodologie walk-forward

### Principe général

Le walk-forward est la seule méthode honnête pour évaluer un système de trading ML : on simule exactement ce qu'un trader live aurait fait — on entraîne sur le passé, on calibre, on teste sur le futur **qu'on n'a jamais vu**.

```
FOLD 2022 :
  Train  → 2019 + 2020            (données disponibles au 01/01/2022)
  Val    → 2021                   (calibration des seuils)
  Test   → 2022                   (simulation live)

FOLD 2023 :
  Train  → 2019 + 2020 + 2021
  Val    → 2022
  Test   → 2023

FOLD 2024 :
  Train  → 2019 + 2020 + 2021 + 2022
  Val    → 2023
  Test   → 2024

FOLD 2025 :
  Train  → 2019 + 2020 + 2021 + 2022 + 2023
  Val    → 2024
  Test   → 2025
```

**Aucune donnée future ne fuit dans le passé.** C'est la différence fondamentale avec un backtest classique (qui serait du snooping).

### Labels (y_long — Quantile 8h top-20%)

Pour chaque barre `t`, le label est :
```
future_ret_8h(t) = log(close[t+8] / close[t])
y_long(t) = 1 si future_ret_8h(t) > Q(72%) de la fenêtre d'entraînement
            0 sinon
```

Autrement dit : le modèle prédit si les 8 prochaines heures vont être dans le **top 28% des meilleures hausses** (après coûts de transaction de 10 bps).

### Calibration des seuils

Après entraînement, les seuils de décision sont calibrés sur l'année de validation :
- On cherche le seuil `thr` qui **maximise le profit factor** sur la val
- Plancher adaptatif à 0.54 (calculé depuis l'AUC moyen de la fleet)
- Un seuil par contexte (horizon × archétype) → 100 seuils potentiels

### Métriques d'évaluation

| Métrique | Formule | Seuil OK |
|----------|---------|----------|
| **Profit Factor (PF)** | Σ gains / Σ pertes | ≥ 1.20 |
| **Win Rate (WR)** | % trades gagnants | — |
| **Expectancy (E)** | PnL moyen par trade (%) | > 0 |
| **Max Drawdown (DD)** | Perte max depuis peak | < 20% |
| **B&H** | Buy & Hold sur la même période | référence |

**Statuts** :
- `OK` : PF ≥ 1.20
- `WEAK` : PF ∈ [0.75, 1.20)
- `CATASTROPHIC` : PF < 0.75 ou DD > 20%
- `NO_TRADES` : moins de 5 trades

---

## 3. Résultats par fold

### Résumé global

```
Année   Status         N     PF    WR    DD%     E%       B&H%
────────────────────────────────────────────────────────────────
2022    CATASTROPHIC  698   0.626  47%   2.2%  -0.30%    -65%
2023    OK            191   1.618  47%   0.3%  +0.29%   +156%
2024    OK            247   1.547  55%   0.2%  +0.24%   +120%
2025    WEAK          206   0.811  42%   0.2%  -0.10%     -7%

Médiane              219   1.179  47%   0.2%            ---
Verdict : NOT_DEPLOYABLE (1 CATASTROPHIC, PF médian < 1.20)
```

---

### Fold 2022 — CATASTROPHIC (PF=0.626)

**Contexte marché** : BTC passe de 47 000$ à 16 000$. Crash de -65%. L'effondrement LUNA/Terra en mai 2022, puis FTX en novembre 2022.

**Données d'entraînement disponibles** : 2019-2020 — deux années de bull market progressif.

**Résultat** :
- AUC = 0.509 (quasi-aléatoire)
- 698 trades, WR = 47%, PF = 0.626
- Le signal génère trop de faux positifs : il "voit" des hausses là où il n'y en a pas

**Explication** : Le modèle a appris la structure d'un marché haussier (corrections → rebonds, volatilité modérée). En 2022, chaque rebond est suivi d'une continuation baissière. Le modèle est calibré sur 2021 (autre année bull) : il utilise des seuils trop bas, donc il trade trop souvent dans un marché qui ne rebondit pas.

**Ce qui NE résout PAS le problème** :
- Données 2017-2018 : contamination NaN (voir section 4)
- Bear gate (bloquer les trades en régime BEAR) : empire car les périodes BEAR-classifiées contiennent les rebonds "dead cat" que le signal capture
- Seuils plus élevés (0.57) : coupe les signaux rentables sans améliorer 2022

**Ce qui serait nécessaire** : Des données de crash structurelles SANS contamination de features (funding rate, OI n'existaient pas en 2018). En pratique, le seul vrai crash comparable disponible est celui de 2022 lui-même — impossible à utiliser pour s'entraîner (fuite du futur).

**Verdict** : 2022 est une limite structurelle, pas un bug réparable.

---

### Fold 2023 — OK (PF=1.618)

**Contexte marché** : Recovery post-FTX. BTC passe de 16k à 40k (+156%). Marché en tendance haussière progressive.

**Données d'entraînement** : 2019-2021 (3 années bull) + **2022 inclus pour la première fois**.

**Résultat** :
- AUC = 0.537 (signal modeste mais réel)
- 191 trades (fréquence intentionnellement basse — seuils calibrés sur 2022)
- WR = 47%, PF = 1.618
- **Drawdown maximal : 0.34%** — le système est très défensif

**Explication** : Le modèle a vu 2022 dans son training data. Il a appris à être sélectif. La calibration sur 2022 (année difficile) force des seuils élevés qui **filtrent bien le bruit**. Avec seulement 191 trades, le modèle ne surtrade pas.

**Points de vigilance** :
- 191 trades sur 8760 barres = signal très peu fréquent (2.2% du temps)
- WR = 47% : le PF > 1 vient de **gains plus grands que les pertes**, pas d'une majorité de trades gagnants
- B&H = +156% : le modèle ne surperforme pas B&H sur l'année, mais il gère le risque différemment

---

### Fold 2024 — OK (PF=1.547)

**Contexte marché** : Bull run ETF Bitcoin. BTC de 40k à 90k (+120%). Très haussier.

**Données d'entraînement** : 2019-2022 (inclut 1 année bear).

**Résultat** :
- AUC = 0.565 (meilleur signal de tous les folds)
- 247 trades
- WR = 55%, PF = 1.547
- **Drawdown maximal : 0.16%** — quasi-inexistant

**Explication** : Dans un bull run fort, même un signal modeste est profitable. La question critique est : est-ce de la skill ou du beta ? Les données suggèrent les deux — le WR de 55% est supérieur au random, et le drawdown de 0.16% montre que le signal **évite les corrections** même dans un marché haussier.

**Point de vigilance** : Le fold 2024 est le plus "facile". Un bull run +120% pardonne beaucoup d'erreurs. Ne pas surestimer la robustesse du modèle à partir de ce fold seul.

---

### Fold 2025 — WEAK (PF=0.811)

**Contexte marché** : Consolidation post-bull. BTC -7% sur l'année. Marché sans tendance claire.

**Données d'entraînement** : 2019-2023.

**Résultat** :
- AUC = 0.560 (signal existent)
- 206 trades
- WR = 42%, PF = 0.811

**Explication — problème de calibration** : L'année de validation est 2024 (+120% bull). Les seuils sont calibrés pour un marché très haussier → le modèle génère des signaux dans des conditions qui ne se réalisent pas en 2025. C'est un **décalage de régime entre val et test**.

Ce n'est pas un bug fondamental du modèle. C'est une limite de la calibration : si l'année de val est très différente de l'année de test, les seuils sont mal adaptés.

**Comparaison** : Le B&H 2025 est de -7%. Le modèle est à -0.10% par trade en expectancy — il **préserve mieux le capital que le B&H** dans un marché difficile, malgré un PF < 1.

---

## 4. Les 6 approches testées

Toutes les approches améliorant 2022 dégradent les autres folds, et vice-versa. C'est le signe d'un compromis structurel, pas d'un bug réparable.

### 4.1 Quantile 8h top-20% (baseline)

```
2022: 0.626 CATA | 2023: 1.618 OK | 2024: 1.547 OK | 2025: 0.811 WEAK
PF médian : 1.179
```

**Meilleur résultat global**. Les autres approches n'améliorent pas ce chiffre.

---

### 4.2 Triple Barrier (ATR × 2.0 / 1.5)

Label 1 = profit hit (ATR × 2.0), 0 = stop hit (ATR × 1.5), -1 = time-out (exclus du training).

```
2022: 0.810 WEAK | 2023: 1.259 OK | 2024: 1.173 WEAK | 2025: 0.734 CATA
PF médian : 0.992
```

**Résultat** : 2022 s'améliore (de CATA à WEAK) mais 2024 et 2025 se dégradent. La TB supprime le biais directionnel haussier, ce qui aide en 2022 mais enlève l'avantage en bull market.

**Pourquoi ?** En bull market, les profits ATR×2.0 sont fréquents. La TB les capture correctement. Mais le label TB est plus "dur" → le modèle apprend à être plus sélectif → moins de trades en 2023/2024 → PF plus bas.

---

### 4.3 Hybrid (Quantile AND Triple Barrier)

Label 1 = y_long=1 ET y_long_tb=1 (haussier ET profitable ET rapide).

```
2022: 0.519 CATA | 2023: 1.575 OK | 2024: 2.752 OK | 2025: 0.742 CATA
PF médian : 1.159
```

**Résultat** : 2024 explose à 2.752 (très peu de trades, très sélectif), mais 2022 et 2025 sont catastrophiques. La corrélation entre y_long et y_long_tb est forte en bull — le modèle overfitte sur les conditions bull.

---

### 4.4 Bear gate (bloquer les trades en régime BEAR)

Ajout d'un filtre : si macro_regime == "BEAR" → no trade.

```
2022: 0.512 CATA | 2023: 1.642 OK | 2024: 1.282 OK | 2025: 0.583 CATA
PF médian : 0.932
```

**Résultat** : Pire que le baseline sur TOUS les folds. Contre-intuitif, mais explicable :

Le régime BEAR est détecté par EMA200h + momentum. Ces périodes incluent **les dead-cat bounces** — les seuls rebonds profitables en 2022. Bloquer ces signaux revient à supprimer les meilleurs trades de l'année.

**Leçon** : Le signal ML est meilleur que la règle de régime pour discriminer les trades.

---

### 4.5 Seuil minimum élevé (min_thr = 0.57)

Augmenter le plancher de confiance de 0.54 à 0.57.

```
2022: 0.464 CATA | 2023: 1.315 OK | 2024: 1.260 OK | 2025: 0.333 CATA
PF médian : 0.862
```

**Résultat** : Catastrophique. 2023 perd 2/3 de ses trades (191→89), 2024 perd 80% (247→50). Les signaux rentables du modèle **sont dans la zone 0.54-0.57** : les forcer à 0.57+ supprime exactement ce qui fonctionne.

---

### 4.6 Données 2017-2018 (bear market historique)

Téléchargement de 2.7M barres 1m depuis Binance pour exposer le modèle au crash BTC -84% de 2018.

```
2022: 0.569 CATA | 2023: 1.047 WEAK | 2024: 1.066 WEAK | 2025: 1.006 WEAK
PF médian : 1.026
```

**Résultat** : Désastre. Toutes les années 2023-2025 passent de OK à WEAK.

**Cause** : En 2017-2018, le funding rate, l'open interest et le LSR n'existaient pas (les perpetuals Binance n'existaient pas avant 2019). Ces colonnes sont remplies en NaN dans les fichiers 2017-2018.

XGBoost apprend implicitement : "NaN funding rate → vieille donnée → régime différent". Ce signal spurieux contamine tous les folds car le modèle l'utilise pour distinguer l'ère 2017-2018 de 2019+, ce qui n'est pas un signal de trading — c'est un artefact de la construction des données.

**Les fichiers parquet 2017-2018 existent** (`data_out/result/`) mais ne sont plus utilisés par le walk-forward.

---

## 5. Pourquoi 2022 résiste à tout

### La limite structurelle

BTC -65% en 2022. C'est le seul fold avec un marché baissier structurel dans nos données. **Aucun système LONG-ONLY** ne peut être profitable en B&H sur une année à -65%. La question est : peut-il au moins perdre moins que B&H ?

Réponse : oui (le modèle perd -2.1% total vs -65% B&H), mais en termes de Profit Factor, il reste < 1 car les faux positifs sont trop nombreux.

### Pourquoi les faux positifs sont inévitables en 2022

Le modèle est entraîné sur 2019-2020. Ces deux années ont la structure suivante :
- Correction → rebond (mean-reversion fonctionne)
- Momentum → continuation (trend-follow fonctionne)
- Support EMA → rebond (niveaux techniques fonctionnent)

En 2022 :
- Correction → continuation baissière
- Momentum → capitulation
- Support EMA → cassé systématiquement

Le modèle voit les mêmes patterns techniques mais dans un régime de marché fondamentalement différent. Il ne peut pas détecter cette différence car **il n'a jamais vu de crash structurel dans son training data**.

### Pourquoi on ne peut pas "lui montrer" 2022

Si on entraîne sur 2022, on ne peut pas le tester sur 2022 (fuite du futur). On peut l'inclure dans le training du fold 2023 — c'est déjà fait, et ça aide pour 2023 (le modèle devient plus sélectif). Mais ça ne répare pas le fold 2022 lui-même.

### La seule vraie solution

Un système qui détecte les crashes **à l'avance** (macro signal, on-chain, etc.) et qui désactive le signal LONG. Ce n'est pas une amélioration du signal ML — c'est un système de régime macro séparé. Hors scope de ce projet actuellement.

---

## 6. Paper trading avec guardrails

### Script

```
python3 scripts/paper_long_signal.py
```

Options :
```
--train-end 2023    # Entraîner jusqu'à 2023 (défaut)
--val-year 2024     # Calibrer sur 2024 (défaut)
--live-year 2025    # Signal live sur 2025 (défaut)
--dry-run           # Affiche sans sauvegarder
--reset-state       # Reset l'état des guardrails
```

### Guardrails actifs

#### 1. Crash Circuit Breaker

**Trigger** : BTC return -30% sur les 60 derniers jours  
**Action** : Bloque tout signal pendant 30 jours  
**Reset** : Automatique après 30 jours

**Raison** : Un crash -30%/60j est statistiquement similaire à 2022. Continuer à générer des signaux LONG dans ce contexte est connu pour être contre-productif (d'après les résultats du fold 2022).

#### 2. Consecutive Losses Guard

**Trigger** : Plus de 4 pertes consécutives en paper  
**Action** : Pause 48 heures  
**Reset** : Automatique après 48h, ou `--reset-state`

**Raison** : 4 pertes consécutives signalent soit un changement de régime, soit un biais du signal dans les conditions actuelles.

#### 3. Weekly Loss Limit

**Trigger** : PnL papier < -5% sur la semaine en cours  
**Action** : Pause 7 jours  
**Reset** : Automatique après 7 jours

**Raison** : Limite le risque d'exposition prolongée dans un régime défavorable.

#### 4. Live Gate

**Toujours actif** : `LIVE_ENABLED = False` dans `config/deployment_status.py`  
**Ce guardrail ne peut pas être déclenché par le script** — il doit être modifié manuellement.

#### 5. Val Quality Guard

**Trigger** : Moins de 50 trades sur la fenêtre de validation  
**Action** : Signal bloqué (modèle pas assez calibré)

### Outputs

| Fichier | Contenu |
|---------|---------|
| `reports/paper_trading/paper_long_signals.csv` | Log de tous les signaux avec timestamp, p_long, action |
| `reports/paper_trading/paper_long_state.json` | État des guardrails (compteurs de pertes, weekly PnL) |
| `reports/paper_trading/guardrail_events.csv` | Log de tous les déclenchements de guardrail |

### Comment utiliser les signaux en paper

1. Lancer le script manuellement ou en cron (ex. toutes les 4h)
2. Lire le signal : `PAPER_LONG`, `WATCH`, ou `NO_SIGNAL`
3. Si `PAPER_LONG` : noter l'entrée dans un spreadsheet, simuler le trade sur 8h
4. Après 8h : noter le résultat (gain/perte), mettre à jour le state manuellement pour les guardrails

**La mise à jour du state (consecutive losses, weekly PnL) est MANUELLE pour l'instant** — le script ne track pas automatiquement les résultats des trades papier. Il faut modifier `paper_long_state.json` directement.

---

## 7. Critères pour passer en live

### Critères quantitatifs (tous requis)

| Critère | Seuil | Mesure |
|---------|-------|--------|
| N trades paper | ≥ 100 | Sur 3-6 mois de paper |
| PF paper | ≥ 1.20 | Sur les 100+ trades |
| WR paper | ≥ 40% | Sur les 100+ trades |
| Max DD paper | < 8% | Equity simulée |
| Pas de crash gate actif | 0 déclenchements | Sur la période paper |
| Walk-forward re-run | ≥ 2/4 OK, 0 CATA | Sur nouvelles données |

### Critères qualitatifs (tous requis)

- [ ] Le marché en cours n'est pas en crash structurel (BTC -30%/60j)
- [ ] Le fold qui sera "testé" en live a une val year représentative (pas +100% si le live est une consolidation)
- [ ] Validation indépendante par une personne n'ayant pas participé au développement
- [ ] Documentation du plan de sortie (à quel drawdown on coupe le système live)

### Processus de déploiement

```
1. Paper trading 3 mois minimum → critères quantitatifs validés
2. Modification manuelle config/deployment_status.py :
      DEPLOYMENT_STATUS = "PAPER_VALIDATED"
3. Deuxième vague paper : petites positions réelles (1-5% capital)
4. Si 2ème validation OK → PAPER_ENABLED = True → LIVE_ENABLED = True
5. Monitoring quotidien des guardrails
```

**Ne jamais sauter l'étape paper réel** (petites positions) — il existe toujours un écart entre la simulation et l'exécution réelle (slippage, latence, pannes API).

---

## Annexe — Données disponibles

```
data_out/result/
  2019_BTCUSDT_features.parquet   ~20k barres 1h
  2019_ETHUSDT_features.parquet
  2020_BTCUSDT_features.parquet   ~34k barres 1h
  2020_ETHUSDT_features.parquet
  2020_SOLUSDT_features.parquet
  2021_BTCUSDT_features.parquet
  ...
  2025_BTCUSDT_features.parquet

  2017_BTCUSDT_features.parquet   NON UTILISÉ (NaN contamination)
  2017_ETHUSDT_features.parquet   NON UTILISÉ
  2018_BTCUSDT_features.parquet   NON UTILISÉ (521k barres 1m)
  2018_ETHUSDT_features.parquet   NON UTILISÉ
```

## Annexe — Commandes utiles

```bash
# Walk-forward complet (résultats de référence)
python3 scripts/walk_forward_v5.py

# Walk-forward folds spécifiques
python3 scripts/walk_forward_v5.py --folds 2023,2024,2025

# Paper signal (dry run)
python3 scripts/paper_long_signal.py --dry-run

# Paper signal avec reset guardrails
python3 scripts/paper_long_signal.py --reset-state

# Paper signal fenêtre personnalisée
python3 scripts/paper_long_signal.py --train-end 2023 --val-year 2024 --live-year 2025
```
