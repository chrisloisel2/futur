# Famille BASIS — hypothèses pré-écrites

*écrit le 2026-09-09, **avant** toute mesure de rendement sur la famille basis.*
*Le spot a été apparié à l'itération 2 ; aucun balayage basis n'a encore été lancé.*

Crible 1 des huit : *payeur falsifiable, écrit avant — qui perd, pourquoi il
continue, ce qui le ferait cesser.* Ce fichier est cette écriture.

## Le prior défavorable, énoncé d'abord

Le dépôt porte déjà une conclusion sur cette famille :
**« funding/basis épuisé par arbitrage 2025-26 »** (round 2 du hunt).

Je l'écris avant de mesurer parce qu'elle rend les hypothèses ci-dessous
*falsifiables dans le bon sens* : si elles portent un edge, il doit être
**décroissant** dans le temps, fort en 2022-2023 et faible ou nul en 2025-2026.
Un edge **stable ou croissant** sur 2025-2026 serait un signe de bug, pas de
découverte — et devrait déclencher une escalade avant toute promotion.

---

## H-BASIS-1 — la prime de portage encombrée

**Le signal.** En transversal : vendre les noms dont le basis
(`perp/spot − 1`) est le plus élevé, acheter ceux dont il est le plus bas.

**Qui perd.** Le particulier à levier. Le perp est, pour lui, la seule façon
d'obtenir une exposition supérieure à son capital sans crédit collatéralisé.
Il paie cette exposition par le funding, dont le basis est la contrepartie au
comptant.

**Pourquoi il continue.** La demande de levier est structurelle et peu sensible
au prix sur la plage observée : le coût du portage (quelques dizaines de bps par
jour au pire) est petit devant la variation qu'il cherche à capter. Il ne
compare pas son coût de portage à une alternative, parce qu'il n'en a pas.

**Ce qui le ferait cesser.**
- un crédit spot collatéralisé aussi accessible et moins cher que le perp
- un produit à levier réglementé sur les mêmes jetons
- l'arrivée d'assez de capital d'arbitrage pour comprimer le basis sous les
  coûts de transaction — *c'est précisément ce que le prior ci-dessus affirme
  être déjà arrivé en 2025-26*

**Ce qui la falsifie.** Un basis élevé ne prédit pas un rendement futur plus
faible. Ou : l'edge brut est inférieur à 2× le coût du symbole (crible 2).

---

## H-BASIS-2 — le désaccord entre ce qu'on paie et comment on est positionné

**Le signal.** `xrank(basis) − xrank(ratio long/short des comptes)`.

**L'idée.** Le basis dit ce que le marché **paie** pour être long. Le ratio
long/short dit **qui est positionné** long. Les deux mesurent la même envie par
deux canaux différents. Quand ils divergent — la foule est longue mais le portage
reste bon marché — c'est que quelqu'un d'assez gros pour tenir le basis est de
l'autre côté du trade de la foule.

**Qui perd.** La foule, quand le désaccord se résout en sa défaveur.

**Pourquoi elle continue.** Elle ne voit pas le basis : il faut apparier deux
marchés pour le calculer, et l'interface de trading n'affiche que le funding, en
taux à 8 h, sur le symbole isolé — jamais en transversal.

**Ce qui le ferait cesser.** Que le basis transversal devienne un indicateur
affiché par défaut.

**Ce qui la falsifie.** Le désaccord n'ajoute rien à ce que le positionnement
seul donne déjà. **C'est le risque principal de cette hypothèse** : le dépôt a
déjà un candidat de positionnement (`lsr_globacct_x`), et le crible 8 (|ρ| < 0,3
en PnL avec toute sleeve admise) est écrit pour l'attraper. Si H-BASIS-2 est
corrélée au candidat positionnement, elle n'est pas une seconde sleeve.

---

## H-BASIS-3 — la compression du portage

**Le signal.** Variation du basis sur 3 / 7 / 20 jours, en transversal :
acheter les noms dont le portage vient de se **comprimer**.

**L'idée.** Une compression rapide du basis est un désengagement du levier long.
Elle est mécanique — des positions se ferment — et non informationnelle. Ce qui
est vendu sous contrainte est vendu trop bas.

**Qui perd.** Le long à levier liquidé ou appelé en marge, qui ferme au prix du
marché sans choisir son moment.

**Pourquoi il continue.** Il n'a pas le choix : c'est la définition d'un appel
de marge. Tant qu'il y a du levier, il y aura des fermetures contraintes.

**Ce qui le ferait cesser.** Rien de structurel — c'est l'hypothèse la plus
robuste de la famille sur le plan du payeur. En revanche l'edge peut être
**capturé par plus rapide que nous** : l'horizon quotidien est très lent pour un
mécanisme dont la contrainte se dénoue en heures.

**Ce qui la falsifie.** L'edge est concentré sur le décile extrême et disparaît
en monotonie (crible 6) — signe qu'on ne capture que quelques épisodes de
liquidation, pas un mécanisme.

---

## H-BASIS-4 — la dispersion du portage comme mesure de stress

**Le signal.** Dispersion **transversale** du basis (écart interquartile du jour),
utilisée comme **état** conditionnant les autres signaux, non comme signal.

**L'idée.** Quand tous les basis se ressemblent, le marché est calme et arbitré.
Quand ils se dispersent, l'arbitrage ne suit plus — et c'est là que les autres
primes devraient payer le mieux.

**Qui perd.** Personne directement : ce n'est pas une hypothèse de rendement,
c'est une hypothèse de **régime**.

**Avertissement écrit d'avance.** Le dépôt a déjà mesuré que le conditionnement
par régime est un tirage à blanc : *« sous nul à états tournés, le meilleur `t`
conditionné tombe sous la médiane du nul, p = 0,575 ; un masque aléatoire bat le
meilleur étage 1 dans 70 % des répétitions »*. **H-BASIS-4 ne sera donc pas
promue comme finaliste**, quel que soit son `t`. Elle est écrite pour mémoire, et
pour que son absence de promotion soit une décision prise d'avance plutôt qu'un
oubli.

---

## Ce que ces hypothèses coûtent

Elles n'ouvrent **aucun** droit de tester. Elles élargissent l'espace exploré,
donc elles **relèvent** le seuil que le prochain candidat devra franchir. C'est
le sens du crible : écrire le payeur d'abord empêche d'inventer l'histoire après
avoir vu le `t`.

Aucune de ces quatre n'est promue par ce document. Elles entrent dans
l'exploration, et devront passer les huit cribles comme tout le reste.
