# Un `t` calculé sur un net n'est pas un test du signal

_Généré par `scripts/audit_net_tstat_artifact.py`. Ne pas éditer à la main._

## La question

`XSEC_REV_1D_LS` affiche « −27,5 bps à t = −5,89, le résultat le plus
statistiquement significatif de tout le balayage ». Or un t de −5,89 sur la
réversion est un t de +5,89 sur la **continuation** — donc potentiellement un
alpha validé qui attendait dans un rapport. Tout dépendait d'une question
binaire : ce −27,5 est-il brut ou net ?

## La réponse : c'est un net, et le brut est nul

Le brut est enregistré explicitement à la source, pas déduit :

```json
{"mechanism_id":"XSEC_REV_1D_LS","gross_bps":0.5,"n_independent":2355}
```

| grandeur | valeur | d'où elle vient |
|---|---|---|
| brut | **+0.5 bps** | `mech_inventory_r3a.json`, enregistré |
| coût supposé | 28.0 bps | brut − net, et le rapport le dit : « assumed 28bps » |
| net | -27.5 bps | publié |
| t sur le net | -5.89 | publié |
| erreur-type | 4.67 bps | \|net\| / \|t\| — le coût est une constante, il ne change pas la variance |
| σ par épisode | 227 bps | SE × √2355 |
| **t sur le brut** | **+0.107** | brut / erreur-type |

**Le signal n'existe pas — ni dans un sens ni dans l'autre.** La continuation,
qui est la réversion à signe inversé, vaut donc −0,5 bps brut, soit
−14,5 bps nets à 14 bps de coût, avec le même t de −0,11.

Contrôle indépendant : le rejugement du round 4 recalcule ce mécanisme à
−13,5 bps sous une hypothèse de coût de 14 bps. `0,5 − 14 = −13,5` ✓.

## Le mécanisme général, et il ne concerne pas que cette ligne

Quand le brut tend vers zéro, le `t` du net tend vers `coût·√n / σ`, qui croît
**sans borne avec n**. Ici : 28·√2355 / 227 = 6.00, contre 5.89 observé.

Autrement dit : **le « résultat le plus significatif du balayage » est une
mesure de la constante de coût, pas du signal.** Il suffit d'assez d'épisodes
pour rendre n'importe quel mécanisme sans edge « hautement significatif » dans
la direction du coût.

## Combien de lignes sont dans ce cas

| mécanisme | n | brut | net | t (net) | **t (brut)** | statut |
|---|---|---|---|---|---|---|
| `XSEC_REV_1D_LS` | 2355 | +0.5 | -27.5 | -5.89 | **+0.107** | ⚠️ **artefact de coût** |
| `XSEC_REL_LIQUIDITY_7D` | 243 | -67.5 | -95.5 | -3.40 | **-2.403** | ✅ significatif sur le brut |
| `XSEC_AMIHUD_ILLIQ_7D` | 336 | +127.3 | +99.3 | 2.92 | **+3.743** | ✅ significatif sur le brut |
| `XSEC_REL_LIQUIDITY_14D` | 121 | -108.6 | -136.6 | -2.58 | **-2.051** | ✅ significatif sur le brut |
| `XSEC_MOM_CVD_DIVERGENT_7D` | 287 | -122.9 | -150.9 | -2.39 | **-1.947** | ⚠️ **artefact de coût** |
| `XSEC_REV_2D_LS` | 1177 | +6.5 | -21.5 | -2.36 | **+0.713** | ⚠️ **artefact de coût** |
| `XSEC_OI_GROWTH_RANK_7D` | 242 | -20.8 | -48.8 | -2.18 | **-0.929** | ⚠️ **artefact de coût** |
| `XSEC_ACCEL_7D` | 336 | -31.4 | -59.4 | -1.82 | **-0.962** | — |
| `XSEC_SECTOR_NEUTRAL_MOM_14D` | 164 | +129.3 | +101.3 | 1.56 | **+1.991** | ✅ significatif sur le brut |
| `XSEC_MOM_14D_LO` | 167 | +213.3 | +199.3 | 1.52 | **+1.627** | — |
| `XSEC_MOM_3D_LS` | 784 | +7.2 | -20.8 | -1.50 | **+0.519** | — |
| `XSEC_REV_3D_LS` | 784 | +7.2 | -20.8 | -1.50 | **+0.519** | — |
| `XSEC_MOM_30D_LO` | 78 | +476.8 | +462.8 | 1.41 | **+1.453** | — |
| `XSEC_REL_LEVERAGE_14D` | 121 | +101.3 | +73.3 | 1.35 | **+1.866** | — |
| `XSEC_MOM_VOLADJ_7D` | 336 | -13.9 | -41.9 | -1.18 | **-0.391** | — |
| `XSEC_SECTOR_NEUTRAL_MOM_7D` | 330 | -2.9 | -30.9 | -1.10 | **-0.103** | — |
| `XSEC_MOM_OI_DIVERGENT_7D` | 240 | +73.2 | +45.2 | 1.04 | **+1.684** | — |
| `XSEC_MOM_CVD_CONFIRMED_7D` | 308 | +77.8 | +49.8 | 1.03 | **+1.609** | — |

**4 lignes sur 34** affichent `|t| ≥ 1,96` sur le net alors que le brut est
indiscernable de zéro : `XSEC_REV_1D_LS`, `XSEC_MOM_CVD_DIVERGENT_7D`, `XSEC_REV_2D_LS`, `XSEC_OI_GROWTH_RANK_7D`.

Deux lignes survivent sur le brut, et ce sont les seules qui disent quelque
chose du marché plutôt que du modèle de coût : `XSEC_REL_LIQUIDITY_7D` (t=-2.40), `XSEC_AMIHUD_ILLIQ_7D` (t=+3.74), `XSEC_REL_LIQUIDITY_14D` (t=-2.05).

## Ce que ça change, et ce que ça ne change pas

**Ça ne change aucun verdict.** Ces mécanismes ne gagnent pas d'argent après
coûts, et `DEAD` reste juste. Un brut nul moins un coût positif est une perte.

**Ça change la FORCE des affirmations.** « Pas seulement absent, un perdant
structurel confiant » n'est pas soutenu : le mécanisme est absent, point, et
étant absent il perd exactement le coût. La nuance compte, parce que « perdant
confiant » invite à retourner la position — et retourner un zéro ne donne pas
un edge, ça donne le même zéro moins le même coût.

**Ça change ce qu'il faut publier.** Un `t` sur un net mélange une mesure (le
signal) et une hypothèse (le coût), et la significativité qui en sort appartient
à l'hypothèse dès que la mesure est faible. Le `t` doit être calculé sur le
**brut** ; le coût se compare ensuite au brut, en niveau, comme le fait déjà
`breakeven_capture` dans `alpha_foundry_v5`.

## Et la question d'origine

**Il n'y a pas d'alpha de continuation qui attend.** La réversion transversale à
1 jour est plate au brut (t = +0,11 sur 2 355 épisodes indépendants, σ = 227 bps).
Une mesure aussi bien échantillonnée et aussi plate est en fait un résultat
utile : elle **exclut** un edge de continuation supérieur à ~9 bps bruts
(1,96 × 4,67) à ce horizon et sur cette construction. C'est un vrai négatif,
bien mesuré — le premier de cette famille.

