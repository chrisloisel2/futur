# Formation complète — Maîtriser VAPolicy v5 / TriCam-BiACT

> **Comment lire ce cours.** Chaque formule importante est suivie d'un bloc **Décomposition**, qui reprend chaque symbole un par un. La méthode recommandée : (1) lis la formule sans chercher à tout comprendre d'un coup, (2) lis la décomposition symbole par symbole, (3) relis la formule — elle doit maintenant se lire comme une phrase, pas comme une suite de symboles opaques.

---

## Table des matières

**[Objectif final](#objectif-final)**

**[PARTIE I — Les fondations nécessaires](#partie-i--les-fondations-nécessaires)**
1. [Comprendre ce qu'est réellement une politique robotique](#chapitre-1--comprendre-ce-quest-réellement-une-politique-robotique)
2. [Les tenseurs : langage fondamental du projet](#chapitre-2--les-tenseurs--langage-fondamental-du-projet)
3. [Ce qu'un paramètre neuronal représente](#chapitre-3--ce-quun-paramètre-neuronal-représente)
4. [Gradient descent et backpropagation](#chapitre-4--gradient-descent-et-backpropagation)

**[PARTIE II — Géométrie robotique](#partie-ii--géométrie-robotique)**
5. [Comprendre un repère 3D](#chapitre-5--comprendre-un-repère-3d)
6. [Matrice de rotation SO(3)](#chapitre-6--matrice-de-rotation-so3)
7. [Transformation homogène SE(3)](#chapitre-7--transformation-homogène-se3)
8. [Pourquoi utiliser des transformations relatives](#chapitre-8--pourquoi-utiliser-des-transformations-relatives)
9. [Comprendre l'invariance de repère](#chapitre-9--comprendre-linvariance-de-repère)
10. [Inverser une transformation SE(3)](#chapitre-10--inverser-une-transformation-se3)
11. [Log et Exp de SO(3)](#chapitre-11--log-et-exp-de-so3)
12. [L'action 7D](#chapitre-12--laction-7d)
13. [Quaternions](#chapitre-13--quaternions)
14. [SLERP](#chapitre-14--slerp)

**[PARTIE III — Construction des données](#partie-iii--construction-des-données)**
15. [Pourquoi le dataset est plus important que le réseau](#chapitre-15--pourquoi-le-dataset-est-plus-important-que-le-réseau)
16. [Échantillonnage à 10 Hz](#chapitre-16--échantillonnage-à-10-hz)
17. [Interpolation des positions](#chapitre-17--interpolation-des-positions)
18. [Construction du label](#chapitre-18--construction-du-label)
19. [Confidence tracking](#chapitre-19--confidence-tracking)
20. [Les masks](#chapitre-20--les-masks)
21. [Échantillonnage des événements rares](#chapitre-21--échantillonnage-des-événements-rares)

**[PARTIE IV — Vision](#partie-iv--vision)**
22. [Pourquoi trois caméras](#chapitre-22--pourquoi-trois-caméras)
23. [Pourquoi cinq flux alors qu'il existe trois caméras](#chapitre-23--pourquoi-cinq-flux-alors-quil-existe-trois-caméras)
24. [Pourquoi ne pas envoyer directement 1920×1080](#chapitre-24--pourquoi-ne-pas-envoyer-directement-1920×1080)
25. [Convolution](#chapitre-25--convolution)
26. [Backbone hiérarchique](#chapitre-26--backbone-hiérarchique)
27. [Residual connection](#chapitre-27--residual-connection)
28. [Depthwise convolution](#chapitre-28--depthwise-convolution)
29. [Feature maps P2 et P3](#chapitre-29--feature-maps-p2-et-p3)

**[PARTIE V — Des images aux tokens](#partie-v--des-images-aux-tokens)**
30. [Qu'est-ce qu'un token visuel ?](#chapitre-30--quest-ce-quun-token-visuel-)
31. [Pourquoi ajouter des embeddings](#chapitre-31--pourquoi-ajouter-des-embeddings)

**[PARTIE VI — Attention](#partie-vi--attention)**
32. [Query, Key, Value](#chapitre-32--query-key-value)
33. [Exemple concret](#chapitre-33--exemple-concret)
34. [Multi-head attention](#chapitre-34--multi-head-attention)

**[PARTIE VII — Perceiver](#partie-vii--perceiver)**
35. [Le problème de l'attention complète](#chapitre-35--le-problème-de-lattention-complète)
36. [Bottleneck latent](#chapitre-36--bottleneck-latent)
37. [Cross-attention](#chapitre-37--cross-attention)

**[PARTIE VIII — Proprioception](#partie-viii--proprioception)**
38. [Pourquoi l'image ne suffit pas](#chapitre-38--pourquoi-limage-ne-suffit-pas)
39. [État 22D](#chapitre-39--état-22d)
40. [Twist](#chapitre-40--twist)
41. [Relation bimanuale](#chapitre-41--relation-bimanuale)

**[PARTIE IX — Action Chunking](#partie-ix--action-chunking)**
42. [Pourquoi prédire le futur](#chapitre-42--pourquoi-prédire-le-futur)
43. [Intuition](#chapitre-43--intuition)
44. [Pourquoi K=12 n'est pas sacré](#chapitre-44--pourquoi-k12-nest-pas-sacré)

**[PARTIE X — Queries bras × temps](#partie-x--queries-bras--temps)**
45. [Construction des queries](#chapitre-45--construction-des-queries)
46. [Pourquoi cette formule est importante](#chapitre-46--pourquoi-cette-formule-est-importante)

**[PARTIE XI — CVAE](#partie-xi--cvae)**
47. [Pourquoi la régression déterministe pose problème](#chapitre-47--pourquoi-la-régression-déterministe-pose-problème)
48. [Variable latente](#chapitre-48--variable-latente)
49. [Encoder du CVAE](#chapitre-49--encoder-du-cvae)
50. [Reparameterization trick](#chapitre-50--reparameterization-trick)
51. [KL divergence](#chapitre-51--kl-divergence)
52. [Pourquoi z=0 en production](#chapitre-52--pourquoi-z0-en-production)

**[PARTIE XII — Fonction de perte](#partie-xii--fonction-de-perte)**
53. [Pourquoi la MSE brute est mauvaise ici](#chapitre-53--pourquoi-la-mse-brute-est-mauvaise-ici)
54. [Normalisation](#chapitre-54--normalisation)
55. [Huber Loss](#chapitre-55--huber-loss)
56. [Loss principale](#chapitre-56--loss-principale)
57. [Pondération temporelle](#chapitre-57--pondération-temporelle)
58. [Smoothness loss](#chapitre-58--smoothness-loss)
59. [Phase gripper](#chapitre-59--phase-gripper)
60. [Loss totale](#chapitre-60--loss-totale)

**[PARTIE XIII — Temporal ensembling](#partie-xiii--temporal-ensembling)**
61. [Le problème](#chapitre-61--le-problème)
62. [Mauvaise solution](#chapitre-62--mauvaise-solution)
63. [Bonne solution](#chapitre-63--bonne-solution)
64. [Reconstruction absolue](#chapitre-64--reconstruction-absolue)
65. [Agrégation](#chapitre-65--agrégation)

**[PARTIE XIV — Contrôle](#partie-xiv--contrôle)**
66. [Le réseau ne commande pas directement les moteurs](#chapitre-66--le-réseau-ne-commande-pas-directement-les-moteurs)

**[PARTIE XV — Entraînement](#partie-xv--entraînement)**
67. [AMP](#chapitre-67--amp)
68. [Gradient accumulation](#chapitre-68--gradient-accumulation)
69. [AdamW](#chapitre-69--adamw)
70. [Scheduler](#chapitre-70--scheduler)
71. [Gradient clipping](#chapitre-71--gradient-clipping)
72. [EMA](#chapitre-72--ema)

**[PARTIE XVI — Validation scientifique](#partie-xvi--validation-scientifique)**
73. [Pourquoi regarder uniquement la loss est une erreur](#chapitre-73--pourquoi-regarder-uniquement-la-loss-est-une-erreur)
74. [Métriques offline](#chapitre-74--métriques-offline)
75. [Métriques rollout](#chapitre-75--métriques-rollout)
76. [Ablation study](#chapitre-76--ablation-study)
77. [Ordre des ablations](#chapitre-77--ordre-des-ablations)

**[PARTIE XVII — Architecture logicielle](#partie-xvii--architecture-logicielle)**
78. [Structure](#chapitre-78--structure)
79. [geometry.py](#chapitre-79--geometrypy)
80. [Test géométrique fondamental](#chapitre-80--test-géométrique-fondamental)

**[PARTIE XVIII — Le réseau complet, de bout en bout](#partie-xviii--le-réseau-complet-de-bout-en-bout)**
**[PARTIE XIX — Les shapes que tu dois connaître par cœur](#partie-xix--les-shapes-que-tu-dois-connaître-par-cœur)**
**[PARTIE XX — Les cinq erreurs qui peuvent tuer le projet](#partie-xx--les-cinq-erreurs-qui-peuvent-tuer-le-projet)**
**[PARTIE XXI — Niveau maîtrise : ce que tu dois pouvoir expliquer sans notes](#partie-xxi--niveau-maîtrise--ce-que-tu-dois-pouvoir-expliquer-sans-notes)**
**[PARTIE XXII — Parcours pratique pour atteindre réellement la maîtrise](#partie-xxii--parcours-pratique-pour-atteindre-réellement-la-maîtrise)**
**[PARTIE XXIII — Critère de maîtrise réelle](#partie-xxiii--critère-de-maîtrise-réelle)**

---

## Objectif final

À la fin de ce cours, tu dois être capable de regarder ce schéma :

$$
\text{Caméras} \rightarrow \text{ConvNet} \rightarrow \text{Tokens} \rightarrow \text{Perceiver} \rightarrow \text{CVAE/Decoder} \rightarrow \text{Actions } SE(3) \rightarrow \text{Temporal Ensemble} \rightarrow \text{Robot}
$$

et comprendre précisément :

* ce que contient chaque tenseur ;
* pourquoi il existe ;
* comment il est calculé ;
* comment les gradients le traversent ;
* quelles hypothèses mathématiques sont faites ;
* comment l'implémenter en PyTorch ;
* comment vérifier qu'il fonctionne ;
* comment savoir quel module est responsable d'un échec ;
* quelles modifications sont scientifiquement pertinentes.

La spécification fixe une architecture d'environ **16,43 millions de paramètres, tous entraînables**, utilisant trois caméras, deux ROI haute définition, un ConvNet maison, une fusion de type Perceiver, une représentation locale SE(3), un CVAE et du temporal ensembling.

---

# PARTIE I — Les fondations nécessaires

## Chapitre 1 — Comprendre ce qu'est réellement une politique robotique

En classification d'images, on apprend généralement une fonction très simple : image en entrée, classe en sortie.

$$
f(x) = y
$$

Exemple :

$$
f(\text{photo}) = \text{« chat »}
$$

En robotique visuomotrice, le problème change de nature :

$$
\pi(o_t) = a_t
$$

**Décomposition :**
- $\pi$ — la **policy** : la fonction que le réseau apprend.
- $o_t$ — l'**observation** du robot à l'instant $t$ (images + état du bras).
- $a_t$ — l'**action** choisie par la policy à partir de cette observation.

Une policy est donc simplement une fonction qui transforme ce que le robot perçoit en action.

Dans TriCam-BiACT, l'observation contient principalement :

$$
o_t = \{\, I_{wall},\ I_{left},\ I_{right},\ ROI_{left},\ ROI_{right},\ s_t \,\}
$$

où $s_t$ représente l'état proprioceptif (position, vitesse, ouverture des pinces — voir [Chapitre 39](#chapitre-39--état-22d)).

La sortie n'est pas une seule action : le réseau prédit une séquence future.

$$
12 \text{ instants} \times 2 \text{ bras} \times 7 \text{ valeurs} = 168 \text{ valeurs}
$$

**Décomposition :**
- **12 instants** — l'horizon de prédiction (voir [Chapitre 42](#chapitre-42--pourquoi-prédire-le-futur)).
- **2 bras** — gauche et droit (robot bimanuel).
- **7 valeurs** — l'action complète d'un bras à un instant donné (voir [Chapitre 12](#chapitre-12--laction-7d)).

La policy complète s'écrit donc plus précisément :

$$
\pi(o_t) = [\,a_{t+1},\, a_{t+2},\, \ldots,\, a_{t+12}\,]
$$

On parle de **policy visuomotrice avec action chunking** : à chaque appel, le réseau ne sort pas une action isolée mais un *chunk* (bloc) de 12 actions futures.

---

## Chapitre 2 — Les tenseurs : langage fondamental du projet

Un tenseur n'est pas un concept abstrait : c'est simplement un tableau multidimensionnel.

Une image RGB s'écrit :

$$
I \in \mathbb{R}^{3 \times H \times W}
$$

Pour un batch de $B$ images :

$$
I \in \mathbb{R}^{B \times 3 \times H \times W}
$$

**Décomposition :**
- $B$ — batch size, le nombre d'exemples traités simultanément.
- $3$ — les 3 canaux RGB.
- $H, W$ — hauteur et largeur en pixels.

Exemple pour la caméra `wall` :

```text
[B, 3, 360, 640]
```

Le réseau visuel transforme progressivement cette représentation, en réduisant la résolution spatiale et en augmentant le nombre de canaux :

```text
pixels           [B,  3, H,    W   ]
                       ↓ convolution
features (niv.1) [B, 64, H/4,  W/4 ]
                       ↓ convolution
features (niv.2) [B,128, H/8,  W/8 ]
                       ↓ convolution
features (niv.3) [B,256, H/16, W/16]
                       ↓ convolution
features (niv.4) [B,384, H/32, W/32]
```

Le document prévoit précisément quatre niveaux principaux, avec des canaux :

| Niveau | Canaux | Stride cumulé |
|---|---|---|
| 1 | 64  | 4  |
| 2 | 128 | 8  |
| 3 | 256 | 16 |
| 4 | 384 | 32 |

Maîtriser ce projet exige de pouvoir prendre n'importe quelle couche et répondre immédiatement :

> Quelle est la shape avant ? Quelle est la shape après ?

C'est une compétence essentielle pour déboguer un réseau — la majorité des bugs de deep learning sont des erreurs de shape.

---

## Chapitre 3 — Ce qu'un paramètre neuronal représente

Prenons la couche PyTorch la plus simple :

```python
nn.Linear(256, 512)
```

Elle possède une matrice de poids et un vecteur de biais :

$$
W \in \mathbb{R}^{512 \times 256}, \qquad b \in \mathbb{R}^{512}
$$

Elle calcule :

$$
y = Wx + b
$$

**Décomposition :**
- $x$ — le vecteur d'entrée, de dimension 256.
- $W$ — la matrice apprise ; chaque ligne combine linéairement les 256 entrées.
- $b$ — un décalage appris, ajouté après la multiplication.
- $y$ — le vecteur de sortie, de dimension 512.

Nombre de paramètres de cette seule couche :

$$
256 \times 512 + 512 = 131\,584
$$

**Décomposition :**
- $256 \times 512$ — les poids de $W$ (une valeur par connexion entrée→sortie).
- $+ 512$ — les biais de $b$ (un par sortie).

Quand le document annonce environ **16,43 M** de paramètres, cela signifie simplement qu'il existe environ 16,43 millions de nombres de ce type, tous ajustés automatiquement par descente de gradient.

---

## Chapitre 4 — Gradient descent et backpropagation

Le processus complet d'entraînement est :

$$
\text{observation} \rightarrow \text{réseau} \rightarrow \hat a
$$

On possède l'action réellement effectuée par l'humain (le label), notée $a$. On calcule une erreur :

$$
L(\hat a, a)
$$

Puis PyTorch calcule, pour **chaque** paramètre $\theta$ du réseau, la dérivée de l'erreur par rapport à ce paramètre :

$$
\frac{\partial L}{\partial \theta}
$$

AdamW effectue ensuite, très schématiquement, la mise à jour :

$$
\theta \leftarrow \theta - \eta \, \nabla_\theta L
$$

**Décomposition :**
- $\theta$ — un paramètre quelconque du réseau (un poids, un biais).
- $\nabla_\theta L$ — le gradient de la loss par rapport à $\theta$ : dans quelle direction et de combien il faut bouger $\theta$ pour **augmenter** $L$.
- $\eta$ — le **learning rate** : la taille du pas qu'on prend dans la direction opposée au gradient (d'où le signe $-$, puisqu'on veut **diminuer** $L$).
- $\leftarrow$ — « est remplacé par » (mise à jour, pas égalité mathématique).

La proposition commence avec :

$$
\eta = 2 \times 10^{-4}
$$

optimiseur **AdamW**, weight decay de $0.05$ (voir [Chapitre 69](#chapitre-69--adamw) pour le détail d'AdamW).

La totalité de TriCam-BiACT doit être pensée comme une énorme fonction différentiable : de l'image en entrée jusqu'à la loss en sortie, chaque opération doit pouvoir être dérivée pour que le gradient remonte jusqu'aux tout premiers paramètres.

---

# PARTIE II — Géométrie robotique

Cette partie est probablement la plus importante du projet. Si tu maîtrises mal les repères, tu peux obtenir une loss excellente avec un robot **incapable de fonctionner**.

## Chapitre 5 — Comprendre un repère 3D

Un repère possède :

* une origine ;
* un axe $X$ ;
* un axe $Y$ ;
* un axe $Z$.

Une position seule est un vecteur :

$$
p = \begin{bmatrix} x \\ y \\ z \end{bmatrix}
$$

Mais une pince robotique possède également une **orientation** : deux pinces peuvent être au même endroit tout en pointant dans des directions différentes. Il faut donc représenter simultanément :

$$
\text{position} + \text{orientation}
$$

C'est exactement ce que résout SE(3) (voir [Chapitre 7](#chapitre-7--transformation-homogène-se3)).

---

## Chapitre 6 — Matrice de rotation SO(3)

Une orientation 3D peut être représentée par une matrice $3 \times 3$ :

$$
R = \begin{bmatrix} r_{11} & r_{12} & r_{13} \\ r_{21} & r_{22} & r_{23} \\ r_{31} & r_{32} & r_{33} \end{bmatrix}
$$

Une matrice de rotation **valide** appartient au groupe $SO(3)$, ce qui impose deux contraintes :

$$
R^T R = I \qquad \text{et} \qquad \det(R) = 1
$$

**Décomposition :**
- $R^T R = I$ — la matrice est **orthogonale** : ses colonnes sont des vecteurs unitaires perpendiculaires entre eux (elle ne déforme ni n'étire l'espace, elle ne fait que le faire tourner).
- $\det(R) = 1$ — la matrice ne fait pas d'inversion de chiralité (pas de symétrie miroir cachée dans la rotation).

Ne vois pas $SO(3)$ comme quelque chose de mystérieux — c'est simplement :

> l'ensemble mathématique de **toutes** les rotations 3D valides.

---

## Chapitre 7 — Transformation homogène SE(3)

Pour stocker position et orientation dans un seul objet mathématique, on empile $R$ et $p$ dans une matrice $4\times4$ :

$$
T = \begin{bmatrix} R & p \\ 0 & 1 \end{bmatrix}, \qquad T \in SE(3)
$$

**Décomposition :**
- $R$ — le bloc $3\times3$ en haut à gauche : l'orientation.
- $p$ — le vecteur colonne en haut à droite : la position.
- La ligne du bas $\begin{bmatrix}0 & 0 & 0 & 1\end{bmatrix}$ est fixe ; elle permet d'écrire rotation **et** translation comme une seule multiplication de matrices.

$SE(3)$ représente l'ensemble des **transformations rigides** 3D (rotation + translation, sans déformation). Une pose de pince devient donc simplement :

$$
T_{gripper}(t)
$$

C'est-à-dire : « position et orientation de la pince à l'instant $t$ », encodées dans un seul objet mathématique.

---

## Chapitre 8 — Pourquoi utiliser des transformations relatives

Supposons $T(t)$ la pose actuelle et $T(t+\Delta t)$ la pose future. La transformation nécessaire pour passer de la première à la seconde est :

$$
\boxed{\Delta T = T(t)^{-1}\, T(t + \Delta t)}
$$

**C'est l'équation centrale de TriCam-BiACT.**

**Décomposition :**
- $T(t)^{-1}$ — « annule » la pose actuelle : on se replace virtuellement à l'origine, orienté comme la pince l'est maintenant.
- $T(t+\Delta t)$ — la pose future exprimée dans le repère monde.
- Le produit des deux — la pose future **vue depuis** la pince actuelle, c'est-à-dire le mouvement à effectuer *dans le référentiel de la pince elle-même*.

Exemple intuitif. Ta pince est actuellement orientée vers l'avant. Elle doit :

```text
avancer de 5 cm
monter de 1 cm
tourner de 10°
```

Le réseau apprend ce mouvement **dans le repère de la pince**. Il n'apprend **pas** :

```text
va vers X = 1.424 m, Y = -0.531 m, Z = 0.824 m
```

Il apprend quelque chose de proche de :

```text
avance   +0.05 m
monte    +0.01 m
rotation +0.17 rad
```

C'est beaucoup plus **généralisable** : la même consigne « avance de 5 cm » a un sens quel que soit l'endroit de l'espace où se trouve le robot.

---

## Chapitre 9 — Comprendre l'invariance de repère

Supposons que ton robot réel soit installé avec une rotation différente de celle du simulateur. On applique une transformation globale $G$ (par exemple : « toute la scène est tournée de 15° ») :

$$
T'(t) = G\,T(t), \qquad T'(t+k) = G\,T(t+k)
$$

Calculons l'action relative dans ce nouveau repère :

$$
T'(t)^{-1}\, T'(t+k) = (G\,T(t))^{-1}(G\,T(t+k))
$$

En utilisant la règle $(AB)^{-1} = B^{-1}A^{-1}$ :

$$
= T(t)^{-1}\, G^{-1} G\, T(t+k)
$$

Comme $G^{-1}G = I$ (l'identité), il reste :

$$
\boxed{T(t)^{-1}\, T(t+k)}
$$

**La transformation globale $G$ a complètement disparu.**

**Décomposition — pourquoi c'est important :**
- Que le robot soit tourné, translaté, ou installé différemment (peu importe $G$), l'action relative apprise par le réseau **ne change pas**.
- Concrètement : un mouvement appris en simulation reste valide même si le robot réel est monté avec une orientation de base différente.
- Le document utilise exactement cet argument pour justifier la représentation locale plutôt qu'une représentation en coordonnées absolues (« va vers X=1.424 »). C'est une propriété fondamentale, pas un détail d'implémentation.

---

## Chapitre 10 — Inverser une transformation SE(3)

Pour :

$$
T = \begin{bmatrix} R & p \\ 0 & 1 \end{bmatrix}
$$

son inverse est :

$$
T^{-1} = \begin{bmatrix} R^T & -R^T p \\ 0 & 1 \end{bmatrix}
$$

**Décomposition :**
- $R^T$ — l'inverse d'une rotation est simplement sa transposée (propriété propre à $SO(3)$, beaucoup moins cher à calculer qu'une inversion de matrice générale).
- $-R^T p$ — **attention**, ce n'est **pas** simplement $-p$. Il faut d'abord « dé-tourner » la translation avec $R^T$ avant de l'inverser, car $p$ était exprimée dans le repère tourné par $R$.

Il faut être capable de démontrer cette relation (en vérifiant que $T \cdot T^{-1} = I$) et de l'implémenter sans librairie externe — c'est un des tests fondamentaux du [Chapitre 80](#chapitre-80--test-géométrique-fondamental).

---

## Chapitre 11 — Log et Exp de SO(3)

Le réseau ne va pas directement sortir une matrice $3\times3$ : cela imposerait neuf valeurs avec de fortes contraintes géométriques ($R^TR=I$, $\det(R)=1$) que rien ne garantit en sortie d'un réseau de neurones standard.

À la place, on utilise un **vecteur rotation** (axis-angle) :

$$
\omega = \begin{bmatrix} \omega_x \\ \omega_y \\ \omega_z \end{bmatrix}
$$

**Décomposition :**
- La norme $\theta = \lVert\omega\rVert$ — correspond à l'**angle** de rotation.
- La direction $\dfrac{\omega}{\lVert\omega\rVert}$ — correspond à l'**axe** de rotation.

Trois nombres libres, sans contrainte, suffisent donc à représenter n'importe quelle rotation — bien plus simple à faire sortir d'un réseau que 9 nombres contraints.

Le **logarithme** transforme une matrice de rotation en vecteur rotation :

$$
\omega = \log_{SO(3)}(R)
$$

L'**exponentielle** fait l'inverse :

$$
R = \exp_{SO(3)}(\omega)
$$

C'est pour cela que l'action finale utilise trois valeurs de rotation locale ($\omega_x, \omega_y, \omega_z$) plutôt qu'une matrice complète.

---

## Chapitre 12 — L'action 7D

Pour chaque bras, l'action complète à un instant donné est un vecteur à 7 composantes :

$$
a = [\, \Delta x,\ \Delta y,\ \Delta z,\ \omega_x,\ \omega_y,\ \omega_z,\ g \,]
$$

**Décomposition :**

| Composantes | Nombre | Signification |
|---|---|---|
| $\Delta x, \Delta y, \Delta z$ | 3 | translation relative (voir [Chapitre 8](#chapitre-8--pourquoi-utiliser-des-transformations-relatives)) |
| $\omega_x, \omega_y, \omega_z$ | 3 | rotation relative, en axis-angle (voir [Chapitre 11](#chapitre-11--log-et-exp-de-so3)) |
| $g$ | 1 | ouverture de la pince |

Total par bras :

$$
3 \text{ (translation)} + 3 \text{ (rotation)} + 1 \text{ (gripper)} = 7
$$

Pour deux bras :

$$
2 \times 7 = 14
$$

Pour douze instants (voir [Chapitre 42](#chapitre-42--pourquoi-prédire-le-futur)) :

$$
12 \times 14 = 168
$$

C'est exactement le nombre annoncé au [Chapitre 1](#chapitre-1--comprendre-ce-quest-réellement-une-politique-robotique).

---

## Chapitre 13 — Quaternions

Le projet utilise également des **quaternions** pour interpoler proprement les orientations. Un quaternion possède quatre composantes :

$$
q = (w, x, y, z)
$$

Un problème important : $q$ et $-q$ représentent **exactement la même rotation**. C'est une propriété algébrique du groupe des quaternions (le revêtement double de $SO(3)$), pas un artefact numérique.

Donc si les données contiennent :

```text
frame 1 : q
frame 2 : -q
```

une interpolation naïve peut faire tourner le robot dans la **mauvaise direction** — puisque numériquement $q$ et $-q$ sont des points très éloignés l'un de l'autre, alors qu'ils décrivent la même orientation physique.

Il faut donc assurer la **continuité de signe quaternion** : à chaque nouvelle frame, si le produit scalaire avec le quaternion précédent est négatif, on inverse son signe. Le document exige cette opération **avant** le SLERP.

---

## Chapitre 14 — SLERP

Pour interpoler entre deux orientations $q_0$ et $q_1$, on utilise :

$$
\text{SLERP}(q_0, q_1, \alpha)
$$

plutôt qu'une interpolation linéaire naïve (LERP).

**Pourquoi ?** Parce qu'une rotation vit sur une géométrie courbe (la sphère unité en 4D). LERP prendrait un raccourci en ligne droite *à travers* cette sphère, ce qui fait accélérer et décélérer la rotation de façon non uniforme. SLERP suit le chemin **angulaire** correct, à vitesse de rotation constante.

C'est indispensable lorsque les poses de tracking et les images ne possèdent pas exactement le même timestamp (voir [Chapitre 18](#chapitre-18--construction-du-label)).

---

# PARTIE III — Construction des données

## Chapitre 15 — Pourquoi le dataset est plus important que le réseau

Le document identifie plusieurs problèmes historiques :

* caméras wrist ignorées ;
* timestamps mal alignés ;
* tracking peu fiable ;
* coordonnées globales incohérentes ;
* loss mal normalisée ;
* fins de démonstrations supprimées.

Un réseau neuronal n'a **aucune** capacité magique à réparer des labels incohérents. Si :

```text
image = instant 5.000 s
pose  = instant 5.150 s
```

le réseau reçoit une image représentant une situation, mais une action représentant une **autre** situation, 150 ms plus tard. C'est du bruit de supervision — et un réseau entraîné sur du bruit de supervision apprend, au mieux, une version floue de la bonne politique.

---

## Chapitre 16 — Échantillonnage à 10 Hz

La grille originale peut être à 30 Hz. La policy fonctionne à :

$$
10\ \text{Hz}
$$

donc une prédiction toutes les :

$$
\Delta t = 0.1\ \text{s}
$$

Le document prévoit de créer des ancres d'apprentissage à 10 Hz à partir de la grille 30 Hz (une frame gardée sur trois, ou interpolée — voir [Chapitre 17](#chapitre-17--interpolation-des-positions)). Une séquence devient donc :

```text
t
t + 0.1
t + 0.2
...
t + 1.2
```

---

## Chapitre 17 — Interpolation des positions

Si on connaît $p(t_0)$ et $p(t_1)$, et qu'on cherche une position intermédiaire à :

$$
t = t_0 + \alpha (t_1 - t_0)
$$

on utilise l'interpolation linéaire :

$$
p(t) = (1-\alpha)\, p(t_0) + \alpha\, p(t_1)
$$

**Décomposition :**
- $\alpha \in [0,1]$ — la fraction du trajet entre $t_0$ et $t_1$.
- Quand $\alpha = 0$, on retombe exactement sur $p(t_0)$ ; quand $\alpha = 1$, sur $p(t_1)$.
- Entre les deux, c'est une moyenne pondérée : plus $t$ est proche de $t_1$, plus $p(t_1)$ pèse dans le résultat.

C'est valable pour les **positions** (espace vectoriel plat). Pour les **orientations**, cette formule ne marche pas directement — il faut SLERP (voir [Chapitre 14](#chapitre-14--slerp)).

---

## Chapitre 18 — Construction du label

Le pipeline correct est :

```text
timestamps
    ↓
interpolation position (LERP)
    ↓
interpolation orientation (SLERP)
    ↓
poses exactes aux instants 10 Hz
    ↓
transformation relative  T(t)⁻¹ T(t+k)
    ↓
Log SO(3)
    ↓
action 7D
```

Le document insiste sur un point : **calculer la transformation relative seulement après interpolation des deux poses.** L'ordre est important — interpoler *après* avoir calculé des deltas produirait un résultat géométriquement incorrect, car les deltas ne vivent pas dans un espace où l'interpolation linéaire est valide.

---

## Chapitre 19 — Confidence tracking

Toutes les poses mesurées ne sont pas également fiables. Supposons :

```text
pose A : confidence = 0.98
pose B : confidence = 0.83
pose C : confidence = 0.21
```

Traiter les trois exactement de la même façon serait une erreur : la pose C est probablement du bruit de tracking, pas un vrai mouvement.

La proposition prévoit initialement de **rejeter** les observations courantes sous :

$$
0.7
$$

puis de **pondérer** les cibles futures par leur confiance dans la loss (voir $c_{b,k}$ au [Chapitre 56](#chapitre-56--loss-principale)). La valeur $0.7$ est un point de départ, pas une constante universelle — elle doit être ajustée empiriquement.

---

## Chapitre 20 — Les masks

Supposons qu'une démonstration s'arrête après cinq futurs steps, alors qu'on demande normalement douze actions futures. Il ne faut **pas** supprimer complètement l'exemple (on perdrait des données précieuses en fin de trajectoire). On construit à la place un masque binaire :

```text
mask = [1,1,1,1,1,0,0,0,0,0,0,0]
```

La loss ignore simplement les éléments marqués `0` (voir $m_{b,k}$ au [Chapitre 56](#chapitre-56--loss-principale)). La spécification prévoit un masque de shape :

$$
[12, 2]
$$

pour les douze horizons et les deux bras — chaque combinaison (horizon, bras) a son propre indicateur de validité.

---

## Chapitre 21 — Échantillonnage des événements rares

Dans une manipulation, le robot peut passer longtemps à :

```text
tenir la pince fermée
```

mais très peu de temps à :

```text
fermer / ouvrir / relâcher
```

Sans correction, le modèle devient très bon pour apprendre « ne change rien » — puisque c'est l'exemple le plus fréquent dans les données — et médiocre sur les transitions, qui sont pourtant les instants les plus critiques de la tâche.

Le document prévoit donc de **sur-échantillonner** modérément les périodes proches des transitions d'ouverture, pour rééquilibrer leur poids dans l'entraînement.

---

# PARTIE IV — Vision

## Chapitre 22 — Pourquoi trois caméras

Le système utilise :

```text
wall (caméra murale)
left wrist (caméra poignet gauche)
right wrist (caméra poignet droit)
```

Chaque caméra répond à une question différente :

| Caméra | Question à laquelle elle répond |
|---|---|
| `wall` | Que se passe-t-il globalement dans la scène ? |
| `left wrist` | Que voit précisément la main gauche ? |
| `right wrist` | Que voit précisément la main droite ? |

Le document impose ces trois capteurs dans le contrat final.

---

## Chapitre 23 — Pourquoi cinq flux alors qu'il existe trois caméras

À partir de chaque caméra wrist, on crée également un crop haute résolution centré sur la pince. On obtient donc **cinq flux visuels** au total :

```text
wall_ctx
left_wrist_ctx
right_wrist_ctx
left_jaw_roi
right_jaw_roi
```

Les résolutions prévues sont :

| Flux | Résolution |
|---|---|
| `wall_ctx` | 640 × 360 |
| `left_wrist` | 512 × 288 |
| `right_wrist` | 512 × 288 |
| `left_roi` | 384 × 384 |
| `right_roi` | 384 × 384 |

Les flux `ctx` donnent le contexte large ; les flux `roi` donnent le détail fin autour de la pince, là où se joue le contact avec l'objet.

---

## Chapitre 24 — Pourquoi ne pas envoyer directement 1920×1080

Trois images Full HD contiennent :

$$
3 \times 1920 \times 1080 \approx 6.22\text{M pixels}
$$

Avant même les activations neuronales, trois images RGB en `float32` représentent environ **71 MiB par exemple** — un volume énorme à charger, stocker et faire transiter à chaque step d'entraînement.

Le réseau ne voit finalement qu'environ :

$$
0.82\text{M pixels}
$$

par exemple, soit approximativement **13 %** des pixels des trois images complètes. L'idée directrice :

```text
vision globale → résolution raisonnable (le contexte n'a pas besoin de détail)
contact        → haute résolution      (le détail compte là où ça touche)
```

---

## Chapitre 25 — Convolution

Une convolution apprend un petit filtre, par exemple :

$$
K \in \mathbb{R}^{3\times 3}
$$

qui se déplace sur l'image et calcule, à chaque position, une somme pondérée locale des pixels sous le filtre.

Conceptuellement, les premiers filtres peuvent apprendre à réagir à des motifs simples :

```text
bord vertical
bord horizontal
texture
coin
```

Les filtres plus profonds, en combinant les précédents, peuvent réagir à des concepts plus complexes :

```text
doigt
balle
bord de boîte
contact
occlusion
```

Le document justifie le ConvNet fait maison par le caractère spécialisé et local de la scène robotique — les motifs pertinents (doigts, objets, contact) sont très différents de ceux d'un dataset d'images génériques.

---

## Chapitre 26 — Backbone hiérarchique

Architecture spécifiée :

```text
Stem
  ↓
Stage 0 : 64 canaux   (2 blocs)
  ↓
Stage 1 : 128 canaux  (2 blocs)
  ↓
Stage 2 : 256 canaux  (4 blocs)
  ↓
Stage 3 : 384 canaux  (2 blocs)
```

Le réseau reprend des principes de ResNet et ConvNeXt (connexions résiduelles, convolutions profondes) mais sans poids ni implémentation externe — tout est réécrit et entraîné from scratch.

---

## Chapitre 27 — Residual connection

Un bloc résiduel réalise :

$$
y = x + F(x)
$$

au lieu de simplement :

$$
y = F(x)
$$

**Décomposition :**
- $x$ — l'entrée du bloc, transmise telle quelle par le chemin « raccourci ».
- $F(x)$ — ce que le bloc apprend à *ajouter* à $x$ (une correction), pas à recalculer depuis zéro.
- $y$ — la sortie : l'entrée, plus la correction apprise.

Cela facilite grandement la propagation du gradient à travers un réseau profond. Si $F(x)$ n'a encore rien appris d'utile (début de l'entraînement), le réseau peut conserver approximativement $y \approx x$ — l'information ne se perd pas en traversant un bloc inutile. C'est une idée essentielle des ResNet.

---

## Chapitre 28 — Depthwise convolution

Une convolution classique mélange **simultanément** dimensions spatiales et canaux — chaque filtre de sortie regarde tous les canaux d'entrée à la fois, ce qui coûte cher en calcul.

Une **depthwise convolution** applique un filtre spatial **séparément** sur chaque canal, sans les mélanger. Cela réduit fortement le coût de calcul (le mélange entre canaux est ensuite fait séparément, par une convolution $1\times1$ bien moins chère).

Les blocs du backbone TriCam-BiACT utilisent notamment :

$$
\text{DWConv } 7\times7
$$

puis un MLP (mélange des canaux) et une connexion résiduelle (voir [Chapitre 27](#chapitre-27--residual-connection)).

---

## Chapitre 29 — Feature maps P2 et P3

Le réseau conserve deux niveaux de représentation :

| Niveau | Résolution | Contenu |
|---|---|---|
| **P2** | relativement fine | précision spatiale |
| **P3** | plus faible | information plus sémantique/abstraite |

Les deux sont projetés vers la même dimension :

$$
D = 256
$$

avant d'être convertis en tokens (voir [Chapitre 30](#chapitre-30--quest-ce-quun-token-visuel-)). C'est un compromis classique en vision : P2 donne la précision spatiale nécessaire pour localiser finement (ex. les doigts), P3 donne la compréhension plus globale (ex. « c'est bien une balle »).

---

# PARTIE V — Des images aux tokens

## Chapitre 30 — Qu'est-ce qu'un token visuel ?

Supposons une feature map :

$$
F \in \mathbb{R}^{256 \times 8 \times 8}
$$

On peut considérer chacune des $8 \times 8 = 64$ positions spatiales comme un vecteur :

$$
x_i \in \mathbb{R}^{256}
$$

On obtient ainsi 64 tokens, soit :

$$
X \in \mathbb{R}^{64 \times 256}
$$

Le token n'est rien de magique — c'est simplement :

> un vecteur numérique représentant une zone de l'image.

---

## Chapitre 31 — Pourquoi ajouter des embeddings

Si tu donnes uniquement 400 tokens bruts au réseau, celui-ci n'a aucun moyen de savoir :

```text
ce token vient-il du wall ? du wrist gauche ? du wrist droit ? du ROI ?
de P2 ou de P3 ?
où se trouvait-il dans l'image ?
```

Chaque token reçoit donc trois informations supplémentaires, ajoutées à son contenu :

* **position** 2D (où dans l'image) ;
* **identité du flux** (quelle caméra) ;
* **identité de l'échelle** (P2 ou P3).

C'est explicitement prévu dans la fusion :

$$
x_i = \text{feature}_i + \text{position}_i + \text{camera}_i + \text{scale}_i
$$

**Décomposition :**
- $\text{feature}_i$ — le contenu visuel du token (ce qu'il voit).
- $\text{position}_i$ — où il se trouve dans l'image source.
- $\text{camera}_i$ — de quelle caméra il provient.
- $\text{scale}_i$ — à quel niveau (P2/P3) il appartient.
- L'addition permet au réseau de « lire » ces quatre informations simultanément dans le même vecteur, sans avoir besoin de canaux séparés.

---

# PARTIE VI — Attention

## Chapitre 32 — Query, Key, Value

C'est la base des Transformers. Pour chaque token $x$, on calcule trois projections apprises :

$$
Q = xW_Q, \qquad K = xW_K, \qquad V = xW_V
$$

Puis :

$$
\text{Attention}(Q,K,V) = \text{softmax}\!\left(\frac{QK^T}{\sqrt{d}}\right) V
$$

**Décomposition :**
- $Q$ (**Query**) — « ce que ce token cherche ».
- $K$ (**Key**) — « ce que chaque token propose, à titre d'étiquette consultable ».
- $V$ (**Value**) — « ce que chaque token transmet réellement s'il est sélectionné ».
- $QK^T$ — un score de compatibilité entre chaque query et chaque key.
- $\sqrt{d}$ — un facteur de normalisation ($d$ = dimension des vecteurs) qui évite que les scores explosent pour de grandes dimensions.
- $\text{softmax}(\cdot)$ — transforme les scores en poids positifs qui somment à 1 (une distribution de pertinence).
- Multiplier par $V$ — récupère un **mélange pondéré** des values, pondéré par ces poids de pertinence.

Comprends cette équation comme :

> chaque query mesure quels keys sont pertinents, puis récupère un mélange des values correspondantes.

---

## Chapitre 33 — Exemple concret

Une query pourrait implicitement représenter :

> Où se trouve la balle ?

Les keys décrivent chaque zone disponible :

```text
wall région 1
wall région 2
left wrist région 1
right wrist région 4
...
```

Les scores $QK^T$ mesurent la compatibilité entre la question et chaque zone. Après softmax :

```text
wall token 1      0.02
wall token 8      0.72
left wrist 13     0.18
autres            0.08
```

Le résultat va surtout récupérer l'information du token `wall token 8` — celui qui, selon le réseau, contient effectivement la balle.

---

## Chapitre 34 — Multi-head attention

Au lieu d'une seule attention, on en réalise plusieurs en parallèle. Avec huit têtes :

$$
head_1, \ldots, head_8
$$

Chaque tête a ses propres projections $W_Q, W_K, W_V$, et peut donc apprendre à se spécialiser dans un aspect différent de la scène — par exemple :

```text
tête 1 → position de la balle
tête 2 → pince gauche
tête 3 → relation objet / boîte
```

Ce comportement n'est pas explicitement imposé par construction, mais illustre pourquoi plusieurs têtes sont utiles : elles laissent au réseau la liberté de diviser le travail d'attention. TriCam-BiACT prévoit :

$$
8 \text{ têtes}
$$

pour sa fusion.

---

# PARTIE VII — Perceiver

## Chapitre 35 — Le problème de l'attention complète

Si tu possèdes $N$ tokens et que chacun regarde tous les autres (self-attention complète), le coût est en :

$$
N^2
$$

interactions. Pour $N = 414$ (le nombre de tokens visuels + tokens d'état du projet, voir [Étape 9](#partie-xviii--le-réseau-complet-de-bout-en-bout)) :

$$
414^2 = 171{,}396
$$

relations, **par tête**, avant même les autres opérations (projections, softmax, etc.). Le document considère cette attention globale inutilement coûteuse à cette échelle.

---

## Chapitre 36 — Bottleneck latent

TriCam-BiACT introduit un petit ensemble de latents **appris** (indépendants de l'entrée, comme des paramètres du modèle) :

$$
L = 64 \text{ latents}, \qquad Z \in \mathbb{R}^{64 \times 256}
$$

Ces 64 latents utilisent une **cross-attention** vers les ~414 tokens source (voir [Chapitre 37](#chapitre-37--cross-attention)). Au lieu de $414 \times 414$ interactions, on travaille avec :

$$
64 \times 414 = 26{,}496
$$

interactions — une réduction d'environ ×6.5 par rapport à l'attention complète, tout en donnant au modèle un budget de calcul fixe indépendant du nombre de tokens en entrée.

---

## Chapitre 37 — Cross-attention

Ici, les rôles ne sont plus symétriques :

$$
Q = Z, \qquad K = X_{source}, \qquad V = X_{source}
$$

Donc :

$$
\text{CrossAttention}(Z, X, X)
$$

**Décomposition :**
- $Z$ fournit les **queries** — les latents posent, en quelque sorte, 64 questions à l'ensemble des observations.
- $X_{source}$ fournit à la fois les **keys** et les **values** — les 414 tokens visuels + d'état.
- Chaque latent regarde l'intégralité des observations et en résume ce qui est pertinent pour lui.

Après cela :

$$
Z_{context} \in \mathbb{R}^{64 \times 256}
$$

Le document raffine ensuite ces latents avec **quatre blocs de self-attention** (les 64 latents s'échangent maintenant de l'information entre eux, en plus de ce qu'ils ont extrait des observations).

---

# PARTIE VIII — Proprioception

## Chapitre 38 — Pourquoi l'image ne suffit pas

Deux images identiques peuvent nécessiter deux actions différentes. Exemple :

```text
cas 1 : bras avance déjà rapidement
cas 2 : bras est immobile
```

Visuellement, ces deux situations peuvent sembler presque identiques — l'image seule ne montre pas la vitesse. Le robot doit donc connaître son propre mouvement : c'est le rôle de la **proprioception**.

---

## Chapitre 39 — État 22D

La spécification minimale utilise :

| Composante | Dimension |
|---|---|
| mouvement bras gauche | 6 |
| ouverture + vitesse gauche | 2 |
| mouvement bras droit | 6 |
| ouverture + vitesse droite | 2 |
| relation bimanuale | 6 |

Total :

$$
6 + 2 + 6 + 2 + 6 = 22
$$

---

## Chapitre 40 — Twist

Un **twist** est une représentation instantanée du mouvement rigide. Il contient une vitesse linéaire et une vitesse angulaire, empilées :

$$
\xi = \begin{bmatrix} v \\ \omega \end{bmatrix} \in \mathbb{R}^6
$$

**Décomposition :**
- $v \in \mathbb{R}^3$ — vitesse translationnelle (mètres/seconde).
- $\omega \in \mathbb{R}^3$ — vitesse rotationnelle (radians/seconde, axe-angle).

Le document propose le twist local calculé sur environ les 100 ms précédentes — c'est-à-dire une estimation de vitesse instantanée par différence finie entre deux poses récentes.

---

## Chapitre 41 — Relation bimanuale

On calcule la pose relative d'un bras par rapport à l'autre :

$$
T_{left}^{-1} T_{right}
$$

Puis on la convertit en vecteur à 6 dimensions via le logarithme :

$$
\text{Log}_{SE(3)}\!\left(T_{left}^{-1} T_{right}\right)
$$

Cette information décrit :

> où se trouve la main droite par rapport à la main gauche ?

C'est extrêmement utile pour les tâches coordonnées (par exemple : tenir un objet à deux mains, où la position relative des deux pinces compte plus que leur position absolue).

---

# PARTIE IX — Action Chunking

## Chapitre 42 — Pourquoi prédire le futur

Une policy classique pourrait prédire uniquement l'action suivante :

$$
a_{t+1}
$$

TriCam-BiACT prédit un bloc entier :

$$
a_{t+1:t+12}
$$

Le document fixe comme point de départ $K=12$ à $10\text{Hz}$, soit :

$$
K = 12, \qquad 10\ \text{Hz} \implies 1.2\ \text{s de futur}
$$

---

## Chapitre 43 — Intuition

Action unique :

> avance.

Chunk :

> avance, approche, ralentis, ferme la pince, commence à lever.

En prédisant un bloc entier plutôt qu'une seule action, le réseau apprend mieux la **structure temporelle** d'une micro-tâche — la cohérence entre actions successives est directement encodée dans la sortie, plutôt que reconstruite étape par étape.

---

## Chapitre 44 — Pourquoi K=12 n'est pas sacré

| Chunk | Conséquence |
|---|---|
| trop petit | peu de planification temporelle |
| trop grand | prédiction lointaine difficile, incertitude accrue |

Le document impose donc une **ablation** (voir [Chapitre 76](#chapitre-76--ablation-study)) sur :

$$
K \in \{8, 12, 16\}
$$

avant de conclure sur la valeur optimale.

---

# PARTIE X — Queries bras × temps

## Chapitre 45 — Construction des queries

Le décodeur possède :

$$
12 \times 2 = 24 \text{ queries}
$$

La formule de construction est :

$$
q_{k,b} = e_{time}(k) + e_{arm}(b)
$$

**Décomposition :**
- $k = 1, \ldots, 12$ — l'index de l'horizon temporel.
- $b \in \{\text{left}, \text{right}\}$ — l'identité du bras.
- $e_{time}(k)$ — un embedding appris représentant « cet horizon précis ».
- $e_{arm}(b)$ — un embedding appris représentant « ce bras précis ».
- La somme donne à chaque query une identité unique (quel horizon, quel bras), tout en partageant les mêmes deux jeux d'embeddings pour toutes les combinaisons.

---

## Chapitre 46 — Pourquoi cette formule est importante

Prenons :

```text
query 1 = bras gauche + t+100ms
query 2 = bras droit  + t+100ms
query 3 = bras gauche + t+200ms
...
```

Le contexte visuel (les 64 latents du Perceiver) reste **commun** à toutes les queries. Mais chaque query possède une identité différente, et va donc en extraire une information différente.

Le bras gauche peut apprendre :

> stabilise.

Pendant que le bras droit apprend, à partir du **même** contexte visuel :

> avance vers la balle.

L'architecture permet donc simultanément :

```text
coordination  (contexte partagé)
    +
asymétrie     (queries différentes par bras et par horizon)
```

---

# PARTIE XI — CVAE

## Chapitre 47 — Pourquoi la régression déterministe pose problème

Supposons deux démonstrations humaines également valides pour la même situation :

```text
trajectoire A : passer à gauche de l'objet
trajectoire B : passer à droite de l'objet
```

Une régression entraînée par MSE (erreur quadratique moyenne) a tendance à prédire la **moyenne** des trajectoires observées :

```text
passer directement au milieu
```

— ce qui peut être une trajectoire dangereuse ou tout simplement invalide (elle percute l'objet). C'est le problème de la **multimodalité** : plusieurs comportements valides coexistent pour une même observation, et leur moyenne n'est généralement pas elle-même un comportement valide.

---

## Chapitre 48 — Variable latente

Le CVAE (Conditional Variational AutoEncoder) introduit une variable latente :

$$
z \in \mathbb{R}^{32}
$$

Le document fixe $z = 32$ comme dimension initiale. On peut interpréter $z$ comme un **code invisible** décrivant une variante du comportement — par exemple, implicitement, « passer à gauche » vs « passer à droite ».

---

## Chapitre 49 — Encoder du CVAE

Pendant l'entraînement, le CVAE regarde :

* l'état $s$ ;
* l'action humaine cible $A$ (le chunk complet, pas juste l'observation).

Il calcule une moyenne et une log-variance :

$$
\mu, \quad \log\sigma^2
$$

et produit une distribution approchée :

$$
q_\phi(z \mid A, s) = \mathcal{N}\big(\mu,\ \operatorname{diag}(\sigma^2)\big)
$$

**Décomposition :**
- $q_\phi$ — la distribution apprise par l'encoder (de paramètres $\phi$).
- $A, s$ — ce dont on conditionne : l'action cible et l'état (seulement disponibles à l'entraînement, puisque $A$ est le futur réel).
- $\mathcal{N}(\mu, \operatorname{diag}(\sigma^2))$ — une gaussienne diagonale : chaque composante de $z$ suit sa propre gaussienne indépendante, de moyenne $\mu_i$ et variance $\sigma_i^2$.

C'est exactement la formulation du document.

---

## Chapitre 50 — Reparameterization trick

On veut tirer $z \sim \mathcal{N}(\mu, \sigma^2)$, mais un tirage aléatoire direct n'est **pas différentiable** — le gradient ne peut pas traverser une opération de sampling brute.

On génère à la place un bruit indépendant des paramètres appris :

$$
\epsilon \sim \mathcal{N}(0, I)
$$

puis on calcule $z$ par une transformation déterministe de $\epsilon$ :

$$
\boxed{z = \mu + \sigma\,\epsilon}
$$

**Décomposition :**
- $\epsilon$ — le hasard est isolé ici, dans une variable qui ne dépend d'aucun paramètre appris.
- $\mu, \sigma$ — dépendent des paramètres du réseau (via l'encoder), et sont donc différentiables.
- Le gradient peut désormais atteindre $\mu$ et $\sigma$, puisque $z$ en est une fonction déterministe une fois $\epsilon$ fixé.

---

## Chapitre 51 — KL divergence

Sans contrainte, l'espace latent pourrait devenir complètement désorganisé (chaque exemple encodé n'importe où, sans structure exploitable). On impose donc à la distribution apprise de rester proche d'une gaussienne standard :

$$
q(z \mid A, s) \approx \mathcal{N}(0, I)
$$

via une pénalité de **KL divergence**. Pour des gaussiennes diagonales, elle a une forme fermée :

$$
KL = -\frac{1}{2}\sum_i \left(1 + \log\sigma_i^2 - \mu_i^2 - \sigma_i^2\right)
$$

**Décomposition :**
- La somme $\sum_i$ porte sur les 32 dimensions de $z$.
- $\mu_i^2$ — pénalise un $\mu_i$ trop éloigné de 0.
- $\sigma_i^2$ et $\log\sigma_i^2$ — pénalisent une variance trop éloignée de 1 (trop petite **ou** trop grande).
- Cette expression vaut 0 exactement quand $\mu_i=0$ et $\sigma_i^2=1$ pour toutes les dimensions — c'est-à-dire quand la distribution encodée est exactement $\mathcal{N}(0,I)$.

Le document prévoit un **warmup** progressif jusqu'à environ :

$$
\beta_{KL} \approx 10^{-3}
$$

(le poids de ce terme dans la loss totale démarre à 0 et augmente progressivement — voir [Chapitre 60](#chapitre-60--loss-totale)).

---

## Chapitre 52 — Pourquoi z=0 en production

Pendant l'entraînement, différentes valeurs de $z$ représentent potentiellement différents modes de comportement valides (passer à gauche, passer à droite...). En production :

$$
z = 0
$$

La policy devient **déterministe** :

```text
même observation → même décision
```

Cela simplifie énormément le contrôle du robot en pratique (comportement reproductible, débogable), au prix de renoncer à la diversité de comportement que le CVAE permettait pendant l'entraînement. C'est un choix cohérent puisque $z=0$ est précisément le centre de la distribution imposée par la KL divergence (voir [Chapitre 51](#chapitre-51--kl-divergence)) — donc le point « le plus représentatif » de l'espace latent.

---

# PARTIE XII — Fonction de perte

## Chapitre 53 — Pourquoi la MSE brute est mauvaise ici

Une action contient des grandeurs physiquement incomparables :

```text
translation → mètres
rotation    → radians
gripper     → une autre échelle encore
```

Exemple : $0.01\,\text{m}$ et $0.1\,\text{rad}$ ne sont pas directement comparables — une MSE brute laisserait la composante à plus grande échelle numérique dominer complètement l'optimisation, même si elle n'est pas plus importante physiquement. Le document impose donc une **normalisation** des composantes continues avant optimisation.

---

## Chapitre 54 — Normalisation

On transforme chaque composante approximativement selon :

$$
x_{norm} = \frac{x - \mu}{\sigma}
$$

(ou une variante utilisant des statistiques robustes, comme la médiane et l'IQR, moins sensibles aux valeurs aberrantes). Ainsi, translation, rotation et gripper se retrouvent sur des échelles numériques comparables, et aucune composante ne domine artificiellement la loss simplement à cause de son unité.

---

## Chapitre 55 — Huber Loss

Pour une erreur $e = \hat y - y$, la loss de **Huber** se comporte comme une erreur quadratique près de zéro et comme une erreur absolue pour les grosses erreurs :

$$
L_\delta(e) = \begin{cases} \dfrac{1}{2}e^2 & |e| \le \delta \\[4pt] \delta\left(|e| - \dfrac{1}{2}\delta\right) & |e| > \delta \end{cases}
$$

**Décomposition :**
- $|e| \le \delta$ (petites erreurs) — comportement quadratique, comme une MSE : le gradient est proportionnel à l'erreur, ce qui donne des mises à jour fines et stables près de la convergence.
- $|e| > \delta$ (grosses erreurs) — comportement linéaire, comme une MAE : le gradient est **borné**, indépendant de la taille de l'erreur.

**Pourquoi ?** Une grosse erreur de tracking (label bruité, outlier) ne doit pas produire un gradient gigantesque qui déstabilise tout l'entraînement — ce qui arriverait avec une MSE pure, où le gradient croît linéairement avec l'erreur.

---

## Chapitre 56 — Loss principale

Le document définit conceptuellement :

$$
L_{action} = \sum_{b,k} m_{b,k}\; c_{b,k}\; w_k \Big[\, \text{Huber}(\Delta p) + \lambda_r\, \text{Huber}(\Delta r) + \lambda_g\, \text{Huber}(g) \,\Big]
$$

**Décomposition, un facteur à la fois :**
- $\sum_{b,k}$ — la somme parcourt les 2 bras et les 12 horizons, donc 24 termes.
- $m_{b,k}$ — le **mask** ([Chapitre 20](#chapitre-20--les-masks)) : *cette cible existe-t-elle ?* (0 ou 1)
- $c_{b,k}$ — la **confidence** ([Chapitre 19](#chapitre-19--confidence-tracking)) : *cette cible est-elle fiable ?* (entre 0 et 1)
- $w_k$ — la **pondération temporelle** ([Chapitre 57](#chapitre-57--pondération-temporelle)) : *quelle importance donner à cet horizon ?*
- $\text{Huber}(\Delta p)$ — l'erreur sur la translation.
- $\lambda_r\,\text{Huber}(\Delta r)$ — l'erreur sur la rotation, pondérée par $\lambda_r$ pour équilibrer son échelle face à la translation.
- $\lambda_g\,\text{Huber}(g)$ — l'erreur sur l'ouverture de la pince, pondérée par $\lambda_g$.

---

## Chapitre 57 — Pondération temporelle

Le document suggère une décroissance légère entre les premiers et derniers horizons :

$$
w_k : 1.0 \rightarrow 0.6
$$

**Pourquoi ?** Une prédiction à $100\,\text{ms}$ est plus proche de l'action réellement exécutée (et donc plus fiable, moins spéculative) qu'une prédiction à $1.2\,\text{s}$. Elle mérite donc légèrement plus de poids dans la loss.

---

## Chapitre 58 — Smoothness loss

On veut limiter les changements brutaux entre actions consécutives du même chunk : $a_{k+1} - a_k$. Une loss simple pourrait ressembler à :

$$
L_{smooth} = \sum_k |a_{k+1} - a_k|^2
$$

Le document demande cependant un **poids faible** pour ce terme, afin d'éviter de lisser abusivement des comportements réellement différents (une transition rapide et volontaire — comme un claquement de pince — ne doit pas être pénalisée comme si c'était du bruit).

---

## Chapitre 59 — Phase gripper

Une sortie auxiliaire classe l'état de la pince en trois catégories :

```text
close
hold
open
```

Le document prévoit trois classes équilibrées (rééquilibrage nécessaire, voir [Chapitre 21](#chapitre-21--échantillonnage-des-événements-rares), car `hold` est largement majoritaire dans les données brutes).

**Pourquoi ajouter cette tâche ?** Parce que les transitions de pince sont rares mais cruciales pour la réussite de la tâche. Le réseau reçoit ainsi un signal supplémentaire, indépendant de la régression continue, indiquant :

> tu dois comprendre le moment où commence réellement un grasp ou un release.

---

## Chapitre 60 — Loss totale

On obtient, en assemblant tous les termes précédents :

$$
\boxed{L_{total} = L_{action} + \beta_{KL}\, L_{KL} + \lambda_s\, L_{smooth} + \lambda_{phase}\, L_{phase}}
$$

**Décomposition :**
- $L_{action}$ — le terme principal, l'erreur de reconstruction de l'action ([Chapitre 56](#chapitre-56--loss-principale)).
- $\beta_{KL}\, L_{KL}$ — régularise l'espace latent du CVAE ([Chapitre 51](#chapitre-51--kl-divergence)).
- $\lambda_s\, L_{smooth}$ — pénalise les à-coups entre horizons consécutifs ([Chapitre 58](#chapitre-58--smoothness-loss)).
- $\lambda_{phase}\, L_{phase}$ — supervise auxiliairement la phase de la pince ([Chapitre 59](#chapitre-59--phase-gripper)).

C'est le cœur de l'entraînement : tout ce que le réseau apprend découle de la minimisation de cette unique expression.

---

# PARTIE XIII — Temporal ensembling

## Chapitre 61 — Le problème

À 10 Hz, le réseau réémet une prédiction complète à chaque step :

```text
t = 0.0 → prédiction des 12 prochains steps
t = 0.1 → nouvelle prédiction des 12 prochains
t = 0.2 → nouvelle prédiction
```

Plusieurs chunks prédisent donc **la même position future**. Par exemple, la pose à $t=0.5$ peut avoir été estimée par les chunks émis à $t=0.0,\ 0.1,\ 0.2,\ 0.3,\ 0.4$ — cinq estimations différentes de la même chose.

---

## Chapitre 62 — Mauvaise solution

Prendre seulement la dernière estimation, et ignorer les précédentes. Chaque nouvelle inférence remplace alors brutalement l'ancienne, sans aucun lissage. Le résultat peut osciller :

```text
gauche
droite
gauche
droite
```

et provoquer du **jerk** (à-coups mécaniques), potentiellement dangereux ou simplement inefficace sur un robot réel.

---

## Chapitre 63 — Bonne solution

On conserve **toutes** les prédictions visant le même timestamp, et on les combine. Mais attention : chaque action est **locale** par rapport à son propre point d'ancrage (voir [Chapitre 8](#chapitre-8--pourquoi-utiliser-des-transformations-relatives)). On ne peut donc pas simplement moyenner :

$$
\Delta T_1,\ \Delta T_2,\ \Delta T_3
$$

directement — ce sont des transformations exprimées dans trois repères d'ancrage différents, pas des nombres comparables terme à terme. Le document l'interdit explicitement.

---

## Chapitre 64 — Reconstruction absolue

Chaque action locale est d'abord transformée en pose **désirée absolue**, en la recomposant avec son ancrage :

$$
\boxed{T_{des}(t,k) = T_{anchor}(t)\; \text{Exp}_{SE(3)}\!\big(\hat\xi(t,k)\big)}
$$

**Décomposition :**
- $T_{anchor}(t)$ — la pose du robot au moment où ce chunk a été émis (le repère d'ancrage).
- $\hat\xi(t,k)$ — l'action locale prédite pour l'horizon $k$, sous forme de twist ([Chapitre 40](#chapitre-40--twist)).
- $\text{Exp}_{SE(3)}(\cdot)$ — convertit ce twist local en une transformation SE(3).
- Le produit — replace cette transformation locale dans le repère monde, en la composant avec l'ancrage.

Une fois cette reconstruction faite pour **chaque** chunk qui visait le même timestamp, toutes les prédictions sont exprimées dans le même repère (robot / simulateur). **Elles deviennent enfin comparables** — c'est seulement à ce moment qu'on peut les combiner.

---

## Chapitre 65 — Agrégation

Les **positions** peuvent être combinées par une simple moyenne pondérée :

$$
p = \frac{w_1 p_1 + w_2 p_2 + w_3 p_3}{w_1 + w_2 + w_3}
$$

**Décomposition :**
- $p_1, p_2, p_3$ — les positions absolues reconstruites depuis plusieurs chunks différents, visant le même instant.
- $w_1, w_2, w_3$ — leurs poids respectifs (par exemple, plus de poids aux prédictions les plus récentes, ou aux horizons courts — voir [Chapitre 57](#chapitre-57--pondération-temporelle)).
- Le dénominateur normalise, pour que le résultat reste une moyenne valide même si les poids ne somment pas à 1.

Pour les **rotations**, une moyenne naïve des composantes quaternion n'est pas toujours suffisante (l'espace des rotations n'est pas plat — voir [Chapitre 14](#chapitre-14--slerp)). Le document propose :

* un alignement de signe quaternion avant moyenne (voir [Chapitre 13](#chapitre-13--quaternions)) ;
* ou une agrégation dans l'algèbre de Lie (moyenne des vecteurs $\omega$, puis `Exp`).

---

# PARTIE XIV — Contrôle

## Chapitre 66 — Le réseau ne commande pas directement les moteurs

Le réseau produit une pose désirée $T_{desired}$. Mais un **contrôleur bas niveau** doit ensuite réaliser cette cible physiquement. Il impose notamment :

* workspace bounds (limites de l'espace de travail) ;
* vitesse maximale ;
* accélération maximale ;
* limites articulaires.

C'est une séparation essentielle des responsabilités :

```text
réseau     : où veux-je aller ?
contrôleur : comment puis-je y aller physiquement, en sécurité ?
```

Le réseau n'a pas à connaître les limites physiques exactes du robot ; le contrôleur n'a pas à comprendre la vision. Chacun fait une seule chose.

---

# PARTIE XV — Entraînement

## Chapitre 67 — AMP

Le document vise un GPU 12 Go et prévoit l'entraînement en précision mixte automatique (**AMP**), avec des calculs en `float16` / `bfloat16` plutôt qu'en `float32` complet. Cela réduit principalement :

* la mémoire utilisée ;
* le coût de calcul.

---

## Chapitre 68 — Gradient accumulation

Supposons que la VRAM permette seulement un batch physique de :

$$
\text{batch} = 4
$$

mais qu'on souhaite un batch **effectif** de :

$$
32
$$

On accumule les gradients sur plusieurs mini-batches avant de mettre à jour les poids :

$$
32 / 4 = 8 \text{ mini-batches}
$$

avant d'appeler :

```python
optimizer.step()
```

Le document prévoit précisément un batch physique de 2 à 4, avec accumulation, pour atteindre un batch effectif de 32 — ce qui donne la stabilité statistique d'un grand batch sans dépasser la VRAM disponible.

---

## Chapitre 69 — AdamW

Adam conserve des statistiques glissantes des gradients passés. Très grossièrement :

$$
m_t = \beta_1 m_{t-1} + (1-\beta_1)\, g_t
$$

$$
v_t = \beta_2 v_{t-1} + (1-\beta_2)\, g_t^2
$$

**Décomposition :**
- $g_t$ — le gradient courant au step $t$.
- $m_t$ — une moyenne mobile du gradient (estime sa direction moyenne récente ; amortit le bruit).
- $v_t$ — une moyenne mobile du **carré** du gradient (estime son amplitude typique récente).
- $\beta_1, \beta_2$ — des facteurs de lissage proches de 1 (l'historique compte plus que le gradient instantané).

Ces deux statistiques permettent des mises à jour **adaptatives** : chaque paramètre reçoit un pas de mise à jour ajusté à sa propre échelle de gradient typique, plutôt qu'un pas uniforme. AdamW sépare correctement la décroissance des poids (weight decay) de cette mise à jour Adam — un détail qui améliore la généralisation par rapport à l'Adam original.

---

## Chapitre 70 — Scheduler

Configuration prévue :

```text
warmup 5 %
puis cosine decay
```

**Warmup** : petit learning rate → augmentation progressive (évite les premiers pas erratiques quand le réseau est encore initialisé aléatoirement).

**Cosine decay** ensuite : learning rate élevé → décroissance progressive et douce (en forme de demi-cosinus) jusqu'à la fin de l'entraînement, pour affiner la convergence.

---

## Chapitre 71 — Gradient clipping

Le document prévoit de borner la norme globale du gradient :

$$
\lVert \nabla \rVert_{global} \le 1.0
$$

Lorsque le gradient devient énorme (par exemple à cause d'un batch contenant un label aberrant), il est **ramené** à cette norme maximale plutôt que d'être utilisé tel quel. Cela réduit les explosions de gradient, un risque particulièrement présent avec des séquences et des architectures profondes.

---

## Chapitre 72 — EMA

Une **exponential moving average** des poids peut être maintenue en parallèle des poids d'entraînement :

$$
\theta_{EMA} \leftarrow \alpha\, \theta_{EMA} + (1-\alpha)\, \theta
$$

**Décomposition :**
- $\theta$ — les poids actuels du réseau, mis à jour à chaque step par AdamW.
- $\theta_{EMA}$ — une moyenne mobile lissée de ces poids au fil de l'entraînement.
- $\alpha = 0.999$ dans la proposition — très proche de 1, donc $\theta_{EMA}$ évolue lentement et lisse le bruit step-à-step.

Cela fournit souvent un modèle d'**évaluation** plus stable que les poids bruts, en particulier en fin d'entraînement où les poids instantanés peuvent osciller.

---

# PARTIE XVI — Validation scientifique

## Chapitre 73 — Pourquoi regarder uniquement la loss est une erreur

Supposons :

```text
modèle A : loss = 0.42
modèle B : loss = 0.46
```

Cela ne prouve **pas** que A manipule mieux un objet. La vraie question est :

```text
attrape-t-il la balle ?
la lève-t-il ?
la place-t-il ?
la relâche-t-il correctement ?
```

Le document exige donc des **métriques physiques** et des **rollouts** réels (ou en simulation), en plus de la loss offline.

---

## Chapitre 74 — Métriques offline

À mesurer séparément :

```text
MAE translation
MAE rotation
opening (ouverture de pince)
phase gripper (précision de classification)
```

et cela **par bras** et **par horizon** — pas seulement en moyenne globale. Ne fais jamais uniquement :

```text
global_validation_loss = 0.32
```

Tu perdrais presque toute l'information diagnostique : un modèle peut être excellent sur la translation à court horizon et mauvais sur la rotation à long horizon, ce qu'un seul chiffre agrégé ne révèle jamais.

---

## Chapitre 75 — Métriques rollout

La spécification demande notamment :

```text
task success
jerk (à-coups)
saturation (des commandes)
workspace clamp (fréquence)
joints proches des limites
```

ainsi que des métriques spécifiques de grasp et de placement — les moments critiques d'une tâche de manipulation.

---

## Chapitre 76 — Ablation study

Une ablation consiste à changer **une seule variable à la fois**, toutes choses égales par ailleurs. Exemple :

```text
A0 = wall seulement
A1 = A0 + wrist cameras
```

Si :

```text
A0 success = 37 %
A1 success = 68 %
```

tu peux attribuer raisonnablement une grande partie du gain aux wrist cameras — **parce que c'est la seule chose qui a changé**. Si tu avais changé plusieurs choses simultanément entre A0 et A1, cette attribution serait impossible. La spécification définit les expériences A0 à A9.

---

## Chapitre 77 — Ordre des ablations

Le chemin scientifique prévu est :

```text
A0  données nettoyées + SE(3) local + wall
      ↓
A1  + wrist cameras
      ↓
A2  + ROI
      ↓
A3  MSE vs Huber
      ↓
A4  K = 8 / 12 / 16
      ↓
A5  temporal ensemble
      ↓
A6  déterministe vs CVAE
      ↓
A7  queries partagées vs spécifiques
      ↓
A8  CVAE vs diffusion, si nécessaire
      ↓
A9  recovery data / DAgger-like
```

C'est beaucoup plus important scientifiquement que d'implémenter immédiatement le modèle maximal : chaque étape isole une décision de conception et mesure son impact réel, plutôt que de faire un pari global non-décomposable sur l'architecture complète.

---

# PARTIE XVII — Architecture logicielle

## Chapitre 78 — Structure

La spécification prévoit :

```text
vapolicy_v5/
    config.py
    geometry.py
    cameras.py
    dataset.py

    model/
        vision.py
        attention.py
        state.py
        cvae.py
        policy.py

    losses.py
    train.py
    infer.py
    controller_bridge.py
    metrics.py

    tests/
        test_geometry.py
        test_dataset.py
        test_model_shapes.py
        test_ensemble.py
```

Cette séparation est très bonne : chaque fichier correspond à une responsabilité mathématique précise, ce qui facilite le test isolé de chaque brique (voir [Partie XXII](#partie-xxii--parcours-pratique-pour-atteindre-réellement-la-maîtrise)).

---

## Chapitre 79 — geometry.py

C'est le fichier le plus critique du projet. Il doit contenir, correctement implémenté et testé :

```text
SE(3)
SO(3)
Log
Exp
quaternion
SLERP
transformation relative
```

Une erreur ici peut rendre l'intégralité du dataset faux — puisque tout label d'action passe par ces fonctions (voir [Chapitre 18](#chapitre-18--construction-du-label)). Un bug silencieux à cet endroit n'entraîne pas un crash : il entraîne un entraînement qui converge normalement, sur des labels géométriquement faux.

---

## Chapitre 80 — Test géométrique fondamental

Le document exige un test particulièrement important, qui vérifie directement la propriété démontrée au [Chapitre 9](#chapitre-9--comprendre-linvariance-de-repère) :

1. Prendre une trajectoire $T(t)$.
2. Appliquer une transformation globale aléatoire : $T'(t) = G\,T(t)$.
3. Calculer $T(t)^{-1}\,T(t+k)$ et $T'(t)^{-1}\,T'(t+k)$.
4. Vérifier qu'ils sont **identiques** à la précision numérique près.

Si ce test échoue :

```text
STOP
```

Tu ne dois **pas** entraîner le modèle. Un échec ici signifie que la représentation locale n'est pas réellement invariante par transformation globale, ce qui invalide l'argument central du [Chapitre 9](#chapitre-9--comprendre-linvariance-de-repère) — et donc une bonne partie de la justification géométrique du projet.

---

# PARTIE XVIII — Le réseau complet, de bout en bout

Tu peux maintenant lire toute l'architecture de gauche à droite, étape par étape.

**Étape 1 — Trois images natives**

```text
wall        1920×1080
wrist left  1920×1080
wrist right 1920×1080
```

**Étape 2 — Prétraitement**

```text
undistortion
synchronisation
resize
ROI extraction
normalisation
```

**Étape 3 — Création de cinq entrées** ([Chapitre 23](#chapitre-23--pourquoi-cinq-flux-alors-quil-existe-trois-caméras))

```text
wall_ctx, left_wrist_ctx, right_wrist_ctx, left_jaw_roi, right_jaw_roi
```

**Étape 4** — Chaque flux passe dans le ConvNet partagé ([Chapitre 26](#chapitre-26--backbone-hiérarchique)).

**Étape 5** — Extraction des features P2 et P3 ([Chapitre 29](#chapitre-29--feature-maps-p2-et-p3)).

**Étape 6** — Projection vers $D=256$.

**Étape 7** — Transformation en environ **412 tokens** visuels ([Chapitre 30](#chapitre-30--quest-ce-quun-token-visuel-)).

**Étape 8** — Ajout des embeddings position / caméra / échelle ([Chapitre 31](#chapitre-31--pourquoi-ajouter-des-embeddings)).

**Étape 9** — Ajout de deux tokens d'état. La source devient approximativement **414 tokens**.

**Étape 10** — 64 latents réalisent une cross-attention : $414 \rightarrow 64$ ([Chapitre 36](#chapitre-36--bottleneck-latent)–[37](#chapitre-37--cross-attention)).

**Étape 11** — Quatre blocs de self-attention raffinent ces 64 latents.

**Étape 12** — Pendant l'entraînement, le CVAE regarde le chunk cible et produit $\mu, \log\sigma^2$ ([Chapitre 49](#chapitre-49--encoder-du-cvae)).

**Étape 13** — Échantillonnage $z = \mu + \sigma\epsilon$, avec $z \in \mathbb{R}^{32}$ ([Chapitre 50](#chapitre-50--reparameterization-trick)).

**Étape 14** — Création de 24 queries : $12 \text{ horizons} \times 2 \text{ bras}$ ([Chapitre 45](#chapitre-45--construction-des-queries)).

**Étape 15** — Les queries interrogent le contexte (cross-attention decoder → latents).

**Étape 16** — Chaque query produit sept valeurs : $[\Delta p, \Delta r, g]$ ([Chapitre 12](#chapitre-12--laction-7d)).

**Étape 17** — On obtient une sortie $[12, 2, 7]$.

**Étape 18** — La loss compare cette sortie au chunk humain ([Chapitre 56](#chapitre-56--loss-principale)).

**Étape 19** — À l'inférence : $z=0$ ([Chapitre 52](#chapitre-52--pourquoi-z0-en-production)).

**Étape 20** — Les actions locales sont reconstruites en poses désirées absolues ([Chapitre 64](#chapitre-64--reconstruction-absolue)).

**Étape 21** — Les chunks chevauchants sont agrégés ([Chapitre 65](#chapitre-65--agrégation)).

**Étape 22** — Le contrôleur bas niveau exécute la pose finale ([Chapitre 66](#chapitre-66--le-réseau-ne-commande-pas-directement-les-moteurs)).

C'est TriCam-BiACT.

---

# PARTIE XIX — Les shapes que tu dois connaître par cœur

Le schéma conceptuel doit devenir automatique :

```text
Images
  wall  : [B, 3, 360, 640]
  wrist : [B, 3, 288, 512]
  ROI   : [B, 3, 384, 384]
    ↓
ConvNet
    ↓
feature maps P2/P3
    ↓
projection D=256
    ↓
visual tokens        [B, ~412, 256]
    ↓
+ state tokens        [B, ~414, 256]
    ↓
Perceiver cross-attention (64 latents)
                       [B, 64, 256]
    ↓
4 × self-attention    [B, 64, 256]
    ↓
decoder queries        [B, 24, 256]
    ↓
action head             [B, 24, 7]
    ↓
reshape               [B, 12, 2, 7]
```

Si tu ne sais pas déterminer ces dimensions sans lancer le programme, l'architecture n'est pas encore maîtrisée.

---

# PARTIE XX — Les cinq erreurs qui peuvent tuer le projet

### Erreur 1 — Mauvaise convention de repère

Le réseau apprend des actions mathématiquement cohérentes mais physiquement fausses.

**Symptôme :** offline bon, rollout catastrophique.

### Erreur 2 — Mauvaise synchronisation

Les images montrent une situation différente des labels.

**Symptôme :** actions floues, contact imprécis, erreur importante à horizon court.

### Erreur 3 — Quaternion mal géré

Signes discontinus ou mauvaise convention (voir [Chapitre 13](#chapitre-13--quaternions)).

**Symptôme :** rotations soudaines, orientation aberrante.

### Erreur 4 — Mauvaise normalisation

Une composante domine la loss.

**Symptôme :** rotation correctement apprise, gripper ignoré.

### Erreur 5 — Croire la validation offline

Le modèle reproduit correctement des observations du dataset mais échoue après ses propres erreurs (elles l'amènent dans des états jamais vus à l'entraînement). C'est le problème du **covariate shift**, raison pour laquelle la spécification prévoit éventuellement une expérience DAgger-like ([A9, Chapitre 77](#chapitre-77--ordre-des-ablations)).

---

# PARTIE XXI — Niveau maîtrise : ce que tu dois pouvoir expliquer sans notes

Tu maîtrises réellement TriCam-BiACT lorsque tu peux répondre immédiatement à toutes ces questions.

### Géométrie

- Pourquoi $T^{-1}(t)\,T(t+k)$ est-il préférable à un delta XYZ global ?
- Pourquoi est-il invariant à $T'(t) = G\,T(t)$ ?
- Quelle différence existe entre SO(3), SE(3), quaternion, axis-angle, rotation vector, twist ?
- Pourquoi faut-il interpoler avant de calculer les deltas ?
- Pourquoi SLERP plutôt que LERP ?

### Vision

- Pourquoi garder les données en 1920×1080 tout en utilisant des entrées plus petites ?
- Pourquoi les ROI font-elles 384×384 ?
- Pourquoi partager le trunk entre les caméras ?
- Pourquoi avoir P2 et P3 ?
- Pourquoi utiliser un ConvNet local plutôt qu'un gros modèle pré-entraîné dans cette proposition ?

### Attention

- Que sont $Q, K, V$ ?
- Pourquoi divise-t-on par $\sqrt{d}$ ?
- Pourquoi plusieurs heads ?
- Quelle différence entre self-attention et cross-attention ?
- Pourquoi $64 \times 414$ est-il préférable ici à $414^2$ ?

### CVAE

- Pourquoi un latent ?
- Pourquoi $z = \mu + \sigma\epsilon$ ?
- Pourquoi la KL ?
- Pourquoi un warmup KL ?
- Pourquoi $z=0$ à l'inférence ?

### Actions

- Pourquoi $[12, 2, 7]$ ?
- Pourquoi 24 queries ?
- Pourquoi séparer identité temporelle et identité du bras ?
- Pourquoi prédire 1.2 seconde ?

### Loss

- Pourquoi normaliser ?
- Pourquoi Huber ?
- Pourquoi masks ?
- Pourquoi confidence ?
- Pourquoi une pondération par horizon ?
- Pourquoi une phase gripper auxiliaire ?

### Inférence

- Pourquoi ne pas moyenner directement les deltas locaux ?
- Pourquoi reconstruire $T_{des} = T_{anchor}\,\text{Exp}(\xi)$ ?
- Quelle différence entre replanning fréquent et temporal ensembling ?

### Validation

- Pourquoi une meilleure validation loss ne garantit-elle pas un meilleur robot ?
- Comment isoler le gain des wrist cameras ?
- Comment savoir si le CVAE est réellement nécessaire ?
- Comment choisir K entre 8, 12 et 16 ?

---

# PARTIE XXII — Parcours pratique pour atteindre réellement la maîtrise

La théorie seule ne suffit pas. Il faut reconstruire progressivement le système.

### Niveau 1 — Géométrie pure

Coder sans réseau neuronal :

```text
SO(3) Exp
SO(3) Log
SE(3) composition
SE(3) inverse
relative transform
quaternion continuity
SLERP
```

Puis écrire les tests numériques. Objectif :

$$
\text{Exp}(\text{Log}(R)) \approx R, \qquad T^{-1}T = I
$$

et surtout l'invariance par transformation globale ([Chapitre 80](#chapitre-80--test-géométrique-fondamental)).

### Niveau 2 — Dataset artificiel

Créer une trajectoire synthétique simple :

```text
pince avance sur X, puis tourne, puis ferme
```

Échantillonner à 30 Hz. Reconstruire des ancres à 10 Hz. Créer :

```text
targets [12, 2, 7]
masks
confidence
```

Vérifier manuellement les premières cibles.

### Niveau 3 — ConvNet seul

Implémenter Stem, Stage0, Stage1, Stage2, Stage3. Faire passer une image. Vérifier les dimensions. Puis cinq vues.

### Niveau 4 — Tokenisation

Transformer P2 et P3 en séquences. Ajouter embedding 2D, embedding de vue, embedding d'échelle. Vérifier le nombre total de tokens.

### Niveau 5 — Attention from scratch

Avant le Perceiver, implémenter toi-même :

$$
\text{softmax}(QK^T / \sqrt{d})\, V
$$

Tester sur $B=2$, $N=10$, $D=32$. Puis implémenter multi-head attention.

### Niveau 6 — Perceiver

Créer 64 latents. Cross-attention latents → source tokens. Puis quatre blocs de self-attention. Vérifier :

```text
input  [B, 414, 256]
output [B,  64, 256]
```

### Niveau 7 — Decoder déterministe

Ne mets pas encore le CVAE. Construis 12 time embeddings et 2 arm embeddings, puis :

$$
q_{k,b} = e_k + e_b
$$

Produire `[B, 12, 2, 7]`. Fais volontairement **overfitter dix exemples**. Si le réseau ne peut pas mémoriser dix exemples propres, le pipeline contient probablement un bug — c'est un excellent test de non-régression avant d'ajouter de la complexité.

### Niveau 8 — Loss complète

Ajouter successivement : normalisation, Huber, mask, confidence, horizon weighting, phase loss, smoothness. **Tester séparément chaque contribution.**

### Niveau 9 — CVAE

Ajouter target encoder, $\mu$, $\log\sigma^2$, sampling, KL. Vérifier numériquement la KL. Tester plusieurs valeurs de $z$.

### Niveau 10 — Temporal ensemble

Créer des chunks synthétiques se chevauchant. Reconstruire les poses absolues. Vérifier qu'elles correspondent au même timestamp. Implémenter leur agrégation.

### Niveau 11 — A0 réel

Seulement : wall, SE(3) local, données propres, decoder. Pas de système complet. C'est précisément le début de la stratégie d'ablation prévue par le document ([Chapitre 77](#chapitre-77--ordre-des-ablations)).

### Niveau 12 — A1

Ajouter left wrist et right wrist. Mesurer le gain.

### Niveau 13 — A2

Ajouter left ROI et right ROI. Mesurer spécifiquement : contact, grasp, distance doigts-objet.

### Niveau 14 — Modèle complet

Seulement après validation des étapes précédentes : CVAE, temporal ensembling, full loss.

La page 14 du document recommande explicitement un développement module par module — tests géométriques, tests de dimensions, profiling GPU, puis entraînement A0 → A2 avant activation du CVAE complet.

---

# PARTIE XXIII — Critère de maîtrise réelle

Il existe quatre niveaux.

**Niveau 1 — Compréhension.** Tu peux dire : « Le modèle utilise plusieurs caméras, un ConvNet et un CVAE. » *Insuffisant.*

**Niveau 2 — Compréhension technique.** Tu peux expliquer $T^{-1}T'$, les tokens, l'attention, le CVAE et la loss. *Encore insuffisant.*

**Niveau 3 — Implémentation.** Tu peux coder chaque composant from scratch. *Bon niveau.*

**Niveau 4 — Maîtrise.** Tu peux regarder un comportement raté comme :

> la pince approche correctement mais ferme systématiquement 300 ms trop tard

et construire un diagnostic rationnel :

```text
vérifier timestamp opening
vérifier interpolation
vérifier distribution transition close
vérifier phase loss
vérifier métrique par horizon
vérifier wrist/ROI
vérifier temporal ensemble
```

Tu n'es alors plus seulement capable d'**utiliser** TriCam-BiACT. Tu es capable de **faire de la recherche sur TriCam-BiACT**.

C'est ce quatrième niveau qui doit être l'objectif.
