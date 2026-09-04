# Décisions de déploiement paper — candidats VALIDATED_FOR_FORWARD

Date : 2026-09-03. Source des verdicts :
`reports/edge_discovery/validation_2026-09/VALIDATION_AND_FORWARD_SCOREBOARD.md`
et `configs/validation_registry.yaml`.

Trois candidats portaient `validated_for_forward: true` au 2026-09-02. Un
candidat validé n'est pas automatiquement un alpha déployable : la validation
répond à « le mécanisme est-il réel ? », pas à « existe-t-il un véhicule
d'exécution ? ». Les trois ont donc été tranchés séparément.

| candidat | validé le | décision | alpha_id déployé |
|---|---|---|---|
| AMIHUD_ILLIQUIDITY_PREMIUM | 2026-09-02 | **déployé** (l'était déjà, mais ne tournait pas) | `AMIHUD_ILLIQUIDITY_PREMIUM_V1` |
| LIQ_REPEAT_DENSITY | 2026-09-02 | **déployé** (nouveau code) | `LIQ_CASCADE_REPEAT_SYSTEMIC_V1` |
| LIQ_REPEAT_SKEW_OVERLAY | 2026-09-02 | **NON déployé** — pas de véhicule d'exécution | — |

---

## 1. AMIHUD_ILLIQUIDITY_PREMIUM — était figé mais ne tournait pas

Figé le 2026-09-02T11:20:10Z et décrit comme « lancé en forward ». En réalité,
au 2026-09-03 son ledger ne contenait que **24 358 décisions REPLAY s'arrêtant
au 2026-08-26, et zéro décision FORWARD_LIVE** : le runner n'avait tourné qu'une
fois, à la main, et aucune unité systemd ne le relançait.

Cause racine, commune à tout le laboratoire : les 9 producteurs de signal et la
couche portefeuille n'étaient couverts par **aucun timer**. La décision forward
la plus fraîche de tout le laboratoire datait de 38 heures. Un forward-test qui
ne tourne pas n'accumule pas de preuve — l'ETA de confirmation était donc
infini, pas 17 ans.

Corrigé par `scripts/run_live_alpha_lab_cycle.py` + `futur-live-alpha-lab.timer`
(cycle toutes les 15 min). Aucune modification de spec.

## 2. LIQ_REPEAT_DENSITY — déployé comme LIQ_CASCADE_REPEAT_SYSTEMIC_V1

Validé mais sans aucun code : « validé » depuis la veille et incapable
d'accumuler la moindre preuve forward. Implémenté à l'identique de la spec du
validateur (`density_variant.py`), avec le seuil de densité **figé en dur**
(médiane = 1) plutôt que recalculé au runtime — une médiane recalculée ferait
dériver la spec en silence sous le freeze_timestamp.

Contrôle de fidélité à la réimplémentation : sur l'historique complet, le filtre
retient 3 653 des 5 675 événements exhaustion, soit **35,6 % d'événements
isolés** — à comparer aux **33,8 % de lignes à densité nulle** rapportées par le
validateur sur sa propre population. Les deux constructions concordent.

Tourne délibérément **à côté** de son parent `LIQ_CASCADE_REPEAT_V1`, pas à sa
place : le parent trade les deux régimes (systémique +22,1bps, isolé −13,4bps),
la variante ne trade que le premier. L'écart entre les deux ledgers forward est
la mesure directe de la valeur du filtre, sur données jamais vues.

Réserve reportée telle quelle dans le registre : ETA de reconfirmation ~9,4 ans.
Le monitoring forward doit juger sur la survie du signe, du coût et du mécanisme,
**pas** sur une significativité fraîche.

## 3. LIQ_REPEAT_SKEW_OVERLAY — validé, mais délibérément NON déployé

Le rapport de validation conclut `VALIDATED_FOR_FORWARD = TRUE` **avec conditions**,
dont la n°5 est disqualifiante pour un déploiement en position :

> « No direct execution vehicle exists (confirmed) — usable only as a
> filter/monitoring input on `LIQ_CASCADE_REPEAT_V1`'s **onset** events, not
> validated as a direct sizing input on the exhaustion trade itself. »

Ce que le mécanisme prédit est la **probabilité qu'une cascade onset se
répète** — pas un rendement. Le transformer en signal de sizing sur le trade
d'exhaustion reviendrait à déployer quelque chose que la validation dit
explicitement ne pas avoir validé. C'est exactement le glissement que la
discipline du projet interdit.

S'y ajoutent : correction de signe obligatoire par rapport à la découverte
(c'est le skew **complaisant**, bas quartile, qui marque le régime à forte
probabilité de répétition — l'inverse de ce qu'annonçait le rapport d'origine) ;
ETA ~11,4 ans à effet plein, ~46 ans avec haircut 50 % ; effet nettement plus
faible en 2025 ; et 17,6 % de l'historique de cascades sans donnée de skew.

**Décision** : pas d'alpha_id, pas de position. Reste consultable comme lead.
Le remettre en discussion demanderait d'abord de démontrer un véhicule
d'exécution — ce que ce rapport dit ne pas exister.

---

## 4. Relance du laboratoire (2026-09-03, après-midi) — arrêt de l'ancien trading, un seul véhicule 200k

Instruction utilisateur : arrêter tout le trading en cours et relancer depuis zéro
avec la liste « tout le vert + challengers orange », capital virtuel 200k, séparation
stricte VALIDATED_FORWARD / EXPERIMENTAL_SHADOW / OVERLAY_SHADOW.

**Arrêtés et désactivés** (systemd --user) : `futur-paper-v1` (Portfolio V1.1, 100k,
tournait depuis le 12/08), toute la famille `futur-alpha20-*` (tournoi paper-live,
dont le runner `carry_basis_v12`), `futur-paper-mh`, `futur-portfolio-mark`. Aucun
processus de trading hors laboratoire ne tourne plus. Les collecteurs de données
(derivatives, hyperliquid, microstructure, news, positioning, deribit, basis) ne sont
pas touchés : le laboratoire en dépend.

**Relancé** : `futur-live-alpha-lab.timer` (15 min), qui n'avait jamais été activé.
Verrou périmé `.cycle.lock` (10:24, sans détenteur) supprimé. Cycle de vérification
manuel : 10/10 producteurs OK, 5 portefeuilles × 200 000 EUR mark-to-market OK.

**Aucun ledger n'a été effacé.** « Relancer depuis zéro » est lu comme « reconstruire
le roster actif », pas comme « réécrire l'historique » : la règle du registre (aucune
décision passée n'est jamais réécrite ni supprimée) prime. Les compteurs forward de
chaque alpha continuent à partir de leur propre freeze_timestamp.

### 4.1 Ce que la liste de départ affirmait et ce que le registre dit

| item de la liste | réalité au registre / rapports | décision |
|---|---|---|
| `LIQ_CASCADE_REPEAT_V1` « moteur forced-flow principal 🟢 » | FROZEN / SIGNAL_SHADOW, **jamais validé indépendamment** (ALPHA_PORTFOLIO_STATUS.md §2.1), 8 décisions forward, confiance EARLY | reste en shadow, **statut inchangé** — c'est déjà honnête ; ne PAS le présenter comme validé |
| `LIQ_REPEAT_DENSITY` « overlay A/B » | déployé ce matin comme alpha **frère** `LIQ_CASCADE_REPEAT_SYSTEMIC_V1` (§2), pas comme surcouche de sizing | inchangé |
| `LIQ_REPEAT_SKEW_OVERLAY` « overlay A/B » | **NON déployé délibérément** (§3 : pas de véhicule d'exécution, signe à inverser) | **veto respecté** (décision utilisateur explicite, même jour) — pas d'alpha_id, pas de position |
| `LIQ_REPEAT_VOL_GATE` « challenger » | `NEEDS_MORE_RESEARCH` wave 1, ETA 28-38 ans, **aucun code** | non déployé — rien de crédible à lancer, même en challenger |
| `FAR_FROM_LOW` « à retirer du live » | RECONSTRUCTED / SIGNAL_SHADOW, audit wave 2 : t 0,90, signe dépend de l'unité | **`scientific_status -> INVALIDATED_PENDING_RESPEC`** ; capital coupé ; collecte forward maintenue (voir 4.2) |
| `carry_basis_v12` | vit dans le tournoi alpha20 (arrêté), pas dans le laboratoire | **non porté** pour l'instant (décision utilisateur) — candidat à un portage ultérieur comme producteur RELATIVE_VALUE_FAMILY |
| `BTC_LEAD_ALT_CASCADE` « freeze + live shadow » | VALIDATED_FOR_FORWARD wave 2, **aucun code** | **figé et déployé**, voir 4.3 |

### 4.2 Couper le capital sans couper la collecte

`run_portfolio_shadow.py` ne filtrait que sur `operational_status`. Un alpha dont la
preuve est invalidée mais dont le code tourne encore recevait donc du capital. Ajout
de `NO_CAPITAL_SCIENTIFIC_STATUSES = {REJECTED, INVALIDATED, INVALIDATED_PENDING_RESPEC}` :
le producteur continue d'écrire ses décisions (la collecte forward reste informative,
et un éventuel respec validé plus tard aura un historique), mais aucun des 5
portefeuilles ne lui alloue plus rien. `operational_status` n'est pas touché — question
orthogonale (le code tourne toujours). Vérifié : `LIQ_CASCADE_FAR_FROM_LOW_V1 ->
exclu du capital` au cycle suivant.

### 4.3 BTC_LEAD_ALT_CASCADE — déployé comme BTC_LEAD_ALT_CASCADE_V1

Validé le matin même et sans code, donc incapable d'accumuler la moindre preuve
forward. Implémenté à l'identique de la spec du validateur
(`btc_lead_variant.py`) : population A (LONG_CASCADE, alts seulement, >= 2022,
btc_ret_30m non nul), règle de choc **causale** `|btc_ret_30m| >= q90` sur
`[t-365j, t)` avec >= 200 événements antérieurs (sinon écarté, jamais imputé), LONG,
fwd_4h. Le centile glissant est recalculé à chaque événement depuis le seul passé :
c'est la spec préenregistrée, pas une constante ajustée.

Les filtres `label_full` / `fwd_4h non nul` du rapport sont des filtres de LABEL
(issue mesurable), pas des critères d'entrée — les appliquer à la décision serait un
look-ahead. Ils ne sont appliqués que dans le contrôle de fidélité.

Contrôle de fidélité sur le parquet du validateur (`tests/test_liq_cascade_btc_lead_variant.py`) :
population A **26 750**, écartés **200**, shock **2 485**, no_shock **24 065**, net14
pondéré-événement **+41,6** (rapport : +41,70), inconditionnel **+11,26** — identiques.
La convention de fenêtre du validateur (`prior = v[lo:i]`, ex-aequo antérieurs inclus)
est reprise telle quelle ; une borne droite excluant tous les ex-aequo donnerait 2 467,
ce n'est pas la spec validée.

Réserves reportées au registre : 2025 seule année négative (-40 bps, à surveiller en
priorité) ; hors 2024 t 1,41 ; preuve 12× plus mince que publiée (259 épisodes vs
3 097 réclamés) ; ETA ~9,7 ans → monitoring sur survie du signe/coût/mécanisme.
Chevauchement REPEAT_V1 : 22,45 % — même risk_bucket et correlation_family, la
déduplication est faite par la couche portefeuille.

### 4.4 Roster actif après relance

| alpha_id | scientific_status | rôle portefeuille | lecture honnête |
|---|---|---|---|
| AMIHUD_ILLIQUIDITY_PREMIUM_V1 | FROZEN (validé wave 1) | position | VALIDATED_FORWARD |
| BTC_LEAD_ALT_CASCADE_V1 | FROZEN (validé wave 2) | position | VALIDATED_FORWARD |
| LIQ_CASCADE_REPEAT_SYSTEMIC_V1 | FROZEN (validé wave 1, overlay→alpha frère) | position | EXPERIMENTAL_SHADOW — validé comme overlay (`LIQ_REPEAT_DENSITY`), déployé comme alpha frère non relié dans `validation_registry.yaml` (pas de `frozen_alpha_id`) ; fondation REPEAT_V1 non validée |
| LIQ_CASCADE_REPEAT_V1 | FROZEN, non validé indépendamment | position | EXPERIMENTAL_SHADOW |
| CROSS_SECTIONAL_MOMENTUM_LIVE_V2 | RECONSTRUCTED | position | EXPERIMENTAL_SHADOW |
| CROSS_SECTIONAL_MOMENTUM_LIVE_V1 | RECONSTRUCTED | position | EXPERIMENTAL_SHADOW (V1, gardé pour comparaison) |
| FUNDING_BASIS_DISAGREEMENT_V2 | FROZEN, non validé indépendamment | position (multi-leg, non exécuté) | EXPERIMENTAL_SHADOW |
| SHORT_COVERING_CONTINUATION_V1 | RECONSTRUCTED (mécanisme confirmé, produit non tradeable seul) | position (zone B pondérée 0,25) | EXPERIMENTAL_SHADOW — `runners.yaml` `role: position`, il porte toutes les positions ouvertes des 5 portefeuilles ; « overlay » n'était qu'une intention de pondération |
| VOL_FORECAST_LAYER_V1 | FROZEN, non validé indépendamment | overlay de sizing (P1_VOL_OVERLAY) | OVERLAY_SHADOW |
| WHALE_LSR_SCREEN_V1 | RECONSTRUCTED | gate | OVERLAY_SHADOW |
| LIQ_CASCADE_FAR_FROM_LOW_V1 | **INVALIDATED_PENDING_RESPEC** | **aucun capital** | collecte seule |

Correctif 2026-09-03 (soir) — la colonne « lecture honnête » suit désormais la règle
mécanique du dashboard (`frontend_pipeline/lab_api.py::_label`, dérivée des configs,
aucune liste codée en dur) : VALIDATED_FORWARD ssi un candidat de
`configs/validation_registry.yaml` porte `frozen_alpha_id == alpha_id` **et**
`validated_for_forward: true` ; sinon NO_CAPITAL si REJECTED/INVALIDATED* ; sinon GATE /
OVERLAY selon le rôle runner (ce que ce tableau appelait OVERLAY_SHADOW pour
WHALE_LSR_SCREEN_V1 et VOL_FORECAST_LAYER_V1) ; sinon EXPERIMENTAL_SHADOW. Le candidat
`LIQ_REPEAT_DENSITY` (validé) n'a que `existing_live_alpha: LIQ_CASCADE_REPEAT_V1` et pas de
`frozen_alpha_id` : `LIQ_CASCADE_REPEAT_SYSTEMIC_V1` n'est donc pas relié et s'affiche
EXPERIMENTAL_SHADOW. Le dashboard compte **2** VALIDATED_FORWARD (AMIHUD, BTC_LEAD), pas 3.
Décision propriétaire ouverte : ajouter `frozen_alpha_id: LIQ_CASCADE_REPEAT_SYSTEMIC_V1`
(+ `freeze_timestamp: "2026-09-03T08:18:34+00:00"`, valeur du registre live) au candidat
pour qu'il passe VALIDATED_FORWARD ; `LIQ_REPEAT_SKEW_OVERLAY` est dans le même cas
(validé, non relié) mais n'est pas déployé, donc sans effet dashboard.

Trois familles économiques réellement représentées par des positions : cascades de
liquidation (4 alphas, 1 corrélation_family), cross-sectionnel (3), relative value (1,
non exécuté). Compter 10 lignes n'est pas compter 10 paris.

---

## 5. Correctif de sizing — budget par alpha non appliqué (bug, 2026-09-03 soir)

**Le bug.** `aggregate()` (`src/institutional/live_alpha_lab/portfolio.py`) dimensionnait
chaque intent live dédupliqué en `notional = _alpha_budget(...) * frac` (avant correctif :
portfolio.py:190-192). `frac` est documenté comme « fraction du budget alloué à cet alpha »
(`intents.py:45`) et le module promet d'« appliquer les budgets par famille/risk_bucket »
(docstring portfolio.py:5-6). Or rien ne bornait la **somme** des frac d'un même alpha :
K intents simultanés d'UN alpha sommaient à K × son budget. Seuls le plafond par actif
(`max_per_asset_fraction`) et le plafond gross global rattrapaient ensuite — c'est-à-dire
que le budget par alpha/famille n'était en pratique jamais la contrainte active.

**La preuve (état live au 2026-09-03T18:24:54Z, lecture seule des `state.json`).**
P1_EQUAL_RISK : 30 positions ouvertes, **toutes** `owner_alpha = SHORT_COVERING_CONTINUATION_V1`,
gross 200 000 = **100 %** du capital, net +200 000 (100 % LONG) ; budget documenté de cet alpha :
1/6 du capital (`portfolio_config.py:41`, `family_budget_fraction` de LIQUIDATION_FAMILY ; seul
alpha du bucket détenant des positions à cet instant). P3_ALL_CANDIDATES : 30 positions, même propriétaire,
gross 214 747 (plafond 1,5×) ; budget documenté : 5 % (`portfolio_config.py:75`,
`per_alpha_budget_fraction`), soit 10 000 EUR. Écart ≈ 6× (P1) et ≈ 21× (P3) par rapport à la
spec. Les adapters qui somment déjà à 1 (panier cross-sectionnel 1/n, jambes Amihud) n'étaient
pas concernés ; le bug ne mordait que sur les producteurs événementiels émettant un intent
`frac = 1` par symbole (SHORT_COVERING, LIQ_CASCADE_*, BTC_LEAD_*).

**Le correctif** (`SIZING_RULE = "PER_ALPHA_BUDGET_CAP_V2"`, portfolio.py:61). Après screen
et overlay vol, la somme des frac vivantes est accumulée par alpha (portfolio.py:208), un intent
multi-leg compté **une** fois (pas une par jambe), puis
`notional = budget * frac / max(1.0, sum_frac_by_alpha[alpha_id])` (portfolio.py:217) : les
intents d'un alpha ne somment jamais au-dessus de son budget. Plafond par actif et scaling gross
inchangés, appliqués après. Somme ≤ 1 → diviseur 1 → aucun changement pour les adapters déjà
normalisés. La répartition budget famille / n_alphas du bucket (`_alpha_budget`) est inchangée.
Chaque **nouveau** point d'`equity_curve` porte `"sizing_rule": "PER_ALPHA_BUDGET_CAP_V2"`
(portfolio.py:576) ; les points antérieurs ne sont pas réécrits (règle du registre).

**Tests de régression** (`tests/test_portfolio_shadow_layer.py`, à partir de la ligne 1339) :
(a) 1 alpha, 5 intents pleine conviction sur 5 instruments, budget famille 30 % de 100k, plafonds
permissifs → total 30 000 (±1e-6), 6 000 chacun ; (b) 2 alphas du même bucket, 3 intents chacun →
15 000 chacun (budget/n_alphas conservé), total 30 000 ; (c) intent multi-leg compté une fois →
chaque jambe = budget entier, le test historique `test_aggregate_multi_leg_produces_two_opposite_instruments`
reste vert ; (d) panier cross-sectionnel 4 noms via `build_intents` → total == budget, inchangé ;
(e) nouveaux points d'equity porteurs de `sizing_rule`, point préexistant sans le champ laissé intact
(y compris après rechargement). Fichier complet : **57 passed** (52 antérieurs + 5 nouveaux), aucun
test existant modifié ni affaibli.

**Frontière du nouveau segment forward.** Code en place à **2026-09-03T18:36:54Z** ; la règle
s'applique **à partir du prochain cycle après 2026-09-03T18:36:54Z** (`futur-live-alpha-lab.timer`,
15 min, prochain déclenchement attendu ≈ 2026-09-03T18:38:15Z ; aucun cycle n'a été lancé à la main,
rien n'a été redémarré). Le marqueur faisant foi est le **premier point d'`equity_curve` portant
`sizing_rule = PER_ALPHA_BUDGET_CAP_V2`** dans chacun des 5 `state.json` : toute lecture de
performance doit séparer avant/après ce point (les points sans le champ = segment bugué).

**Conséquence mécanique attendue au cycle suivant.** Les positions de l'alpha sur-alloué
(SHORT_COVERING_CONTINUATION_V1) sont ramenées à son budget en un seul step : sur P1/P2, gross
~200 000 → au plus ~33 333 (1/6, moins si d'autres alphas du bucket sont vivants) ; sur P3,
~214 747 → 10 000. Ce delta vend ~5/6 (P1/P2) et ~95 % (P3)
des positions au mark courant, ce qui **réalise** le P&L latent de ces positions et paie une fois
les frais de turnover (5 bps taker + 2 bps slippage sur le notional vendu, ≈ 117 EUR sur P1, ≈ 143 EUR
sur P3). Ce coût est **le coût du bug** (des positions qui n'auraient jamais dû exister à cette taille),
pas celui du correctif. Les autres alphas (paniers cross-sectionnels, Amihud) ne bougent pas.

**Ce qui n'a PAS changé.** Aucune `PortfolioConfig` modifiée (`portfolio_config.py` intact : mêmes
budgets 1/6, 5 %, mêmes plafonds) — le correctif **fait respecter la spec documentée**, il ne la
change pas. Aucun ledger, aucune décision, aucun point d'equity passé réécrit. Aucune spec d'alpha
touchée (pas de nouveau alpha_id : le signal est identique, seul le sizing portefeuille l'est).
