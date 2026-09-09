# FORWARD_CROWD_POSITIONING_V1 — préenregistrement du test forward

*Écrit et scellé le 2026-09-09, sur branche orpheline `prereg/forward-crowd-positioning-v1`,
avant qu'un seul jour de la fenêtre de test n'ait été regardé. Un seul regard, à la date
prévue, et jamais avant.*

## 1. Le payeur, en une phrase falsifiable

**Le particulier à effet de levier sur les perpétuels Binance, lorsqu'il est collectivement
le plus long d'un nom relativement aux autres, perd de l'argent sur ce nom dans les jours qui
suivent ; il continue parce que l'effet de levier le contraint à vendre sur les mouvements
adverses au lieu de tenir, et que l'accès au levier sans capital n'a pas d'alternative pour
lui ; il cesserait si le levier retail devenait rationné ou si un crédit collatéralisé
comparable existait.**

Falsifiable : si les noms où la foule est la plus longue ne sous-performent pas les noms où
elle l'est le moins, l'hypothèse est fausse.

## 2. Le mécanisme

Le ratio long/short **par compte** (`count_long_short_ratio`, publié par Binance) mesure la
proportion des comptes longs — c'est la foule, pas les gros porteurs (ceux-ci sont dans les
ratios *top trader*). Une position collectivement longue et à levier est fragile : une baisse
force des ventes (appels de marge, liquidations), qui font baisser davantage. Vendre les noms
où cette fragilité est la plus grande et acheter ceux où elle est la plus faible capture, en
dollar-neutre, la prime que la foule paie pour son levier.

## 3. La direction, imposée

**Acheter le décile de noms où la foule est la MOINS longue ; vendre le décile où elle est la
PLUS longue.** Équipondéré, dollar-neutre. Le harnais n'a plus le droit de choisir un côté
(I13) : celui-ci est le seul admis.

## 4. La configuration, dérivée du mécanisme

Chaque paramètre est justifié par une phrase qui ne mentionne aucun résultat antérieur.

| paramètre | valeur | pourquoi, depuis le mécanisme |
|---|---|---|
| score | `count_long_short_ratio`, moyenne quotidienne des observations 5 min | c'est la variable qui définit le payeur ; la moyenne du jour est la lecture la plus simple d'une série intra-journalière |
| univers | tout perp USDT-M pour lequel Binance publie le ratio **et** dont l'ADV 20 j ≥ 2 M$ | on trade là où le payeur est observable ; 2 M$ = position de 20 000 $ (200 000 $ / 2 paniers de 5) plafonnée à 1 % de l'ADV — une contrainte de capacité, pas un choix de panel |
| paniers | décile haut / décile bas, `k = max(5, N/10)` | « les extrêmes » : un décile est la définition la moins arbitraire des extrêmes d'une coupe ; 5 est le minimum pour qu'un panier soit un panier |
| horizon / rebalance | quotidien, score de `t` exécuté à `close(t+1)`, tenu un jour | un jour = trois règlements de funding, le cycle complet minimal où le payeur paie ; l'exécution au close suivant est la première praticable |
| neutralisation | aucune au-delà de la dollar-neutralité | l'hypothèse est **relative** (la foule plus longue *ici* que *là*) ; le rang transversal est déjà relatif, la dollar-neutralité annule le bêta |
| coût | modèle par symbole du harnais, base 14 bps aller-retour | **hypothèse déclarée** (défaut I7 ouvert) : aucune mesure de spread n'existe sur toute la fenêtre |
| winsor | ±50 % sur le rendement quotidien | protection contre une barre corrompue, pas un réglage |

Le seul chiffre appris d'une donnée passée est la taille d'univers que la règle de capacité
produit (~117 noms en 2022-23, vue lors du test **placebo** du pipeline) ; ce n'est pas une
statistique signal→rendement, et il est déclaré ici.

## 5. Données, fenêtre, seuil, puissance

- **Sources** : `um_klines_1d` (Vision) pour les prix et l'ADV ; `binance_vision_metrics`
  (Vision, 5 min) pour le ratio. **Backfillées après coup** pour couvrir la fenêtre — le
  collecteur live (47 symboles) n'est pas la source du test.
- **Fenêtre** : du **2026-09-09** (scellement) à la date du test. Les jours antérieurs sont
  exclus même s'ils sont collectés : le live lab les a déjà regardés.
- **Hypothèses sur cette fenêtre** : **n = 1**. Seuil **unilatéral** (direction imposée) :
  `threshold_t(1) = 1,6449` sur le `t` de Newey-West (lag 2) du **net** quotidien.
- **Puissance** : à un Sharpe arithmétique vrai de 1,66 — le proxy de planification, celui du
  livre hors échantillon, **pas** une prédiction pour cette hypothèse —, 80 % de puissance
  exigent **819 jours utilisables**, soit au plus tôt le **2028-12-06**.
- **Règle d'arrêt** : un seul test, quand ≥ 819 jours utilisables sont accumulés. Le script
  refuse de tourner avant, et un refus **inscrit** au ledger compte comme un regard.
- **Budget** : l'exécution débite 1 des 3 tests du `LOOP_STATE`.

## 6. La contrainte de queue, fixée avant

Validité = `t_net ≥ 1,6449`. **Déployabilité**, jugée en plus et non à la place :
- perte maximale à 1× brut ≤ **20 %** sur la fenêtre ;
- asymétrie quotidienne du net ≥ **−1,0** ;
- tranche haute faite d'au moins **116 épisodes effectifs** (crible 6, `N_eff = (Σx)²/Σx²`).

Une hypothèse valide mais non déployable est un résultat scientifique et rien d'autre.

## 7. Ce que ce test n'est pas, et les fuites déclarées

- **La cible du forward n'a jamais été regardée** : les rendements n'existent pas encore.
  L'hypothèse, elle, vient de l'exploration historique — c'est un test hors échantillon d'une
  hypothèse sélectionnée, ce qui est le rôle du forward, pas un test d'une hypothèse neuve.
- **Canal de fuite déclaré** : le live lab tourne sur la même fenêtre et note ses engines,
  dont `WHALE_LSR_SCREEN_V1`, un *gate* fondé sur le même ratio. Je m'engage à ne lire
  aucun scoreboard d'engine de positionnement avant la date du test. **C'est une promesse,
  pas un fait**, et elle est déclarée comme telle.
- **Borne déclarée** : aucun code commité ne montre de regard sur la fenêtre forward ; le
  code non commité est indétectable.
- **Interdit après ce scellement** : re-regarder, ajuster un paramètre, essayer une seconde
  implémentation. Les pins ci-dessous rendent toute modification détectable, et le script
  refuse de tourner si elles ne correspondent plus.

## 8. Le code, pinné

Le test est `tools/forward_test_crowd_positioning.py`, exécuté une fois par
`python3 tools/forward_test_crowd_positioning.py --start 2026-09-09 --end <date>`.
Il inscrit le regard au ledger **avant** tout calcul, exige le témoin de cette branche, et
vérifie ces empreintes :

```json pins
{
  "tools/forward_test_crowd_positioning.py": "c52ef2be06421f762f11f855c9983d3755994ca02c334c2e4ca17f36d44aad85",
  "tools/alpha_sweep_v4.py": "30bb11c512887933c567eacd5eb34e2484e0bc9966603e0975142856ec1466f2",
  "tools/build_daily_cache.py": "5774083719df73271713000577988491d93d0c792157cb13f84a885a43000130",
  "tools/look_ledger.py": "c9bb473326a812f27fb53f4b261c565a1fa4df1ad6085c165d87d01a5d058b14",
  "src/institutional/live_alpha_lab/preregistration.py": "bd307deeba684c28662bd6579a65beadd4aec2234d967c530f2b211ab3f0cc39"
}
```
