# Le null de l'appareil — ce que la chaîne rend quand on n'injecte rien

_Généré par `scripts/audit_zero_injection_null.py`. Ne pas éditer à la main._

Le contrôle positif a rendu une ordonnée à l'origine de **−4,95 bps**. Avant de
lire le placebo, il faut savoir si ce chiffre est une soustraction de coût
(légitime, à déclarer) ou un décalage de l'appareil (et alors le placebo doit
être comparé à −5, pas à 0).

## La réponse courte

**Ce n'est pas le coût.** L'`excess` mesuré par le labelliseur est un chiffre
AVANT coût : `dec_excess_bps = brut − référence de marché`, et la soustraction des
14 bps d'aller-retour n'intervient qu'à la lecture (`net_bps_base`). Une
ordonnée à l'origine sur l'excess ne peut donc pas être un coût.

**Et ce n'est pas non plus un décalage de l'appareil** : le null structurel est nul
à 1e-12 bps près. Le −4,95 est du BRUIT D'ÉCHANTILLONNAGE sur le niveau Δ=0, qui
ne portait que 326 épisodes. Le détail suit.

## 1. L'identité — la référence est-elle bien centrée ?

`excess_i = r_i − moyenne(r)` sur le même ensemble de symboles : la moyenne
transversale de l'excess vaut zéro par construction. Si elle s'en écarte, c'est
que l'ensemble du benchmark et celui de la coupe divergent.

| grandeur | valeur |
|---|---|
| barres mesurées | 5671 |
| symboles par barre (médiane) | 47 |
| **écart max à l'identité** | **1.355e-13 bps** |
| écart médian | 2.721e-15 bps |

Nul à la précision machine. **La référence de marché est exactement centrée sur
la coupe qu'elle sert à normaliser** — pas de symbole compté d'un côté et pas
de l'autre, pas de dérive d'univers entre les deux.

## 2. Le null structurel — toute la coupe, aucun tirage

| mesure | n | moyenne | interprétation |
|---|---|---|---|
| toutes les lignes (symbole × barre) | 265834 | **0.0000 bps** | l'identité, vérifiée sur le pool |
| après decluster (symbole, 24 h) | 282 | **+0.001 bps** | ce que le decluster fait au centrage |

Le decluster repondère les symboles — un symbole tiré six fois dans la journée
devient un épisode, comme celui tiré une fois. Cette repondération déplace le
centre de **+0.001 bps**, avec un intervalle de ±3.18 bps à ce n. C'est indistinguable de zéro.

## 3. Le null du placebo, simulé sur son propre design

Le placebo ne voit pas toute la coupe : il tire 4 symboles à chaque cycle de
15 minutes. Son null n'est donc pas un point mais une DISTRIBUTION, et sa largeur
dépend de deux choses, pas d'une seule : le nombre d'épisodes, ET le nombre de
décisions par épisode — un épisode qui moyenne huit tirages est bien moins
dispersé qu'un épisode qui n'en moyenne qu'un.

C'est pour ça que la simulation rejoue son design exact sur la vraie population
d'excess, au lieu d'appliquer `σ/√n` à un σ emprunté à un autre échantillonnage.

| jours de collecte | épisodes (médiane) | null attendu | écart-type | intervalle 95 % |
|---|---|---|---|---|
| 0.5 j | 46 | -0.06 bps | 7.51 | **[-14.9, +15.4]** |
| 1.0 j | 47 | +0.14 bps | 6.21 | **[-11.3, +11.3]** |
| 2.0 j | 47 | -0.43 bps | 5.42 | **[-11.0, +8.7]** |
| 3.0 j | 47 | +0.14 bps | 4.16 | **[-6.9, +8.8]** |
| 4.0 j | 47 | +0.26 bps | 3.38 | **[-6.1, +6.7]** |
| 5.0 j | 47 | -0.01 bps | 3.11 | **[-5.5, +6.0]** |
| 7.0 j | 48 | -0.51 bps | 3.44 | **[-7.6, +5.7]** |

**Correction d'un chiffre annoncé plus tôt.** J'avais estimé qu'à ~200 épisodes le
placebo certifierait « pas de biais supérieur à ~23 bps ». C'était faux : ce 23
venait d'un σ de 112 bps emprunté au contrôle positif, dont les épisodes ne
moyennent qu'une ou deux décisions parce qu'il tire sur une grille 4 h. Le placebo
tire seize fois plus souvent, ses épisodes sont bien mieux moyennés, et sa
résolution est donc **plusieurs fois meilleure** que ce que j'avais annoncé.

## 4. Donc, le −4,95

Le niveau Δ=0 du contrôle positif a rendu **-5.60 bps** sur 326 épisodes.

L'ordonnée à l'origine de la fonction de transfert hérite directement de ce
point : c'est le niveau Δ=0 qui l'ancre. **Un bruit d'échantillonnage sur un
point de la droite, pas une propriété de la chaîne.**

## 3-bis. Le plafond que la simulation a révélé, et qui change la lecture

Dans le tableau ci-dessus, le nombre d'épisodes **ne bouge pas** : 46 à une
demi-journée, 47 à sept jours. Ce n'est pas une saturation de la simulation,
c'est une propriété de la règle de decluster, et elle est structurelle.

`episodes.decluster` ouvre un nouvel épisode quand l'écart avec l'observation
PRÉCÉDENTE dépasse la fenêtre — pas avec le début de l'épisode. C'est un
chaînage par lien simple : des décisions espacées de 3 h s'enchaînent
indéfiniment dans un seul épisode, quelle que soit la durée totale.

| cas | décisions | épisodes |
|---|---|---|
| un symbole tiré toutes les 3 h pendant 10 j | 80 | **1** |
| le même volume en rafales espacées de 48 h | 80 | **10** |
| placebo réel au 2026-09-06 | 59 | 35 (= 35 symboles distincts) |

**Le placebo tire ~8 fois par symbole et par jour. Ses écarts sont donc toujours
inférieurs à 24 h, et tous ses tirages d'un même symbole fusionnent en UN
épisode.** Son compte d'épisodes est plafonné au nombre de symboles de l'univers,
soit 49 — et il l'a déjà quasiment atteint.

### Ce que ça corrige dans le plan

**Le placebo n'aura pas ~200 épisodes mercredi. Il en aura ~49, et il n'en aura
jamais plus.** Attendre plus longtemps ne fait pas croître son nombre d'épisodes ;
ça densifie chaque épisode, ce qui resserre quand même l'intervalle — de ±15 bps
à une demi-journée à ±6 bps à cinq jours — mais par un autre mécanisme que celui
qu'on croyait, et avec un plancher.

### Et le piège pour la famille qu'on s'apprête à tester

Un livre TRANSVERSAL est déclusterisé sur le temps seul (`cross_sectional=True`,
tous symboles confondus). Avec un rééquilibrage quotidien, l'écart entre deux
rééquilibrages vaut exactement 24 h, ce qui n'est pas STRICTEMENT supérieur à la
fenêtre de 24 h : tout s'enchaîne.

| rééquilibrage | fenêtre de decluster | rééquilibrages sur 6 ans | épisodes |
|---|---|---|---|
| quotidien | 24 h | 2190 | **1** |
| quotidien | 23 h | 2190 | **2190** |
| tous les 2 jours | 24 h | 1095 | **1095** |
| tous les 3 jours | 24 h | 730 | **730** |

Une heure de différence sur un paramètre fait passer la taille d'échantillon de
1 à 2190. **Il faut donc rééquilibrer à un pas STRICTEMENT supérieur à la fenêtre
de decluster**, jamais égal — et le vérifier avant d'écrire une ligne, pas après.

## Comment lire le placebo mercredi

Après trois jours de collecte, un placebo SANS biais porte ~47 épisodes et
tombe dans **[-6.9, +8.8] bps** 95 fois sur 100.

C'est ça, la comparaison à faire — pas « est-ce zéro », mais « est-ce dans cet
intervalle, au nombre d'épisodes qu'il porte réellement ce jour-là ». Lire la
ligne du tableau ci-dessus qui correspond à sa collecte effective, pas une
ligne choisie d'avance.

- **Dedans** → l'appareil n'a pas de biais détectable à cette résolution, et les
  chiffres des vrais alphas sont lisibles tels quels.
- **Dehors** → il y a un biais de mesure de l'ordre de 7 bps, et tous les
  chiffres du lab sont à relire à cette échelle.

Et ce que ça ne dira PAS : rester dans l'intervalle n'exclut qu'un biais
supérieur à ~7 bps. Plus fin que ça reste invisible à ce n.

⚠️ **Une condition d'usage.** Cet intervalle suppose que le placebo tire bien
4 symboles par cycle de 15 min. S'il a tourné moins souvent — cycle en
panne, timer arrêté — ses épisodes moyennent moins de décisions, sa dispersion
monte, et l'intervalle ci-dessus devient trop étroit. Vérifier son nombre de
décisions PAR épisode avant de conclure, pas seulement son nombre d'épisodes.

