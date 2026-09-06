# Ce que le plafond de profondeur refuse

_Généré par `scripts/audit_depth_cap_impact.py`. Ne pas éditer à la main._

Le plafond du simulateur était adossé à l'**open interest** — un stock de
positions, pas une profondeur de carnet. Il mordait 1,0 % du temps. Il est
désormais adossé au notionnel affiché au **meilleur limite**, c'est-à-dire
exactement la taille pour laquelle le spread coté a été observé. Au-delà, le
modèle de coût (mid moins deux bps) ne repose plus sur rien.

**Frontière de segment : `2026-09-07`.** Les ordres antérieurs gardent
la règle sous laquelle ils ont été produits, et chaque ordre porte désormais sa
politique (`cap_policy`). Une série qui traverse cette date doit être segmentée —
mélanger deux régimes d'exécution dans une même courbe d'équité rendrait les deux
illisibles.

## Ce que le passé aurait donné sous la nouvelle règle

| grandeur | valeur |
|---|---|
| ordres rejoués | 8556 |
| dont mesurables (sonde de profondeur disponible) | 8556 |
| sans sonde — **non plafonnés, pas « larges »** | 0 |
| **ordres qui dépassent la profondeur** | **1662, soit 19.4 %** |
| notionnel exécuté | 9 851 326 $ |
| **notionnel non remplissable au pas demandé** | **6 222 594 $, soit 63.2 %** |
| ordre médian | 19 $ |
| profondeur médiane | 797 $ |

Le plafond passe de mordre **1,0 %** des ordres à en mordre **19.4 %**.

## Par alpha

| alpha | ordres | plafonnés | notionnel | reporté | % reporté |
|---|---|---|---|---|---|
| `LIQ_CASCADE_FAR_FROM_LOW_V1` | 25 | 24 (96.0 %) | 250 602 $ | 221 974 $ | **88.6 %** |
| `LIQ_CASCADE_REPEAT_V1` | 1118 | 400 (35.8 %) | 2 765 739 $ | 2 274 872 $ | **82.3 %** |
| `BTC_LEAD_ALT_CASCADE_V1` | 89 | 79 (88.8 %) | 1 186 627 $ | 959 848 $ | **80.9 %** |
| `SHORT_COVERING_CONTINUATION_V1` | 8371 | 1527 (18.2 %) | 8 227 655 $ | 4 996 407 $ | **60.7 %** |

## Les symboles où ça se joue

| symbole | profondeur | ordre médian | ordres plafonnés | % du notionnel reporté |
|---|---|---|---|---|
| BCHUSDT | 683 $ | 35 $ | 74 | **91.4 %** |
| ARBUSDT | 402 $ | 111 $ | 110 | **89.5 %** |
| ALGOUSDT | 191 $ | 16 $ | 127 | **92.4 %** |
| IMXUSDT | 738 $ | 2 032 $ | 86 | **84.0 %** |
| FILUSDT | 835 $ | 35 $ | 70 | **82.9 %** |
| SEIUSDT | 335 $ | 70 $ | 36 | **94.5 %** |
| GRTUSDT | 598 $ | 267 $ | 96 | **80.6 %** |
| WLDUSDT | 3 323 $ | 12 $ | 18 | **74.9 %** |
| JUPUSDT | 1 132 $ | 64 $ | 10 | **95.4 %** |
| APTUSDT | 546 $ | 21 $ | 52 | **86.3 %** |
| RUNEUSDT | 222 $ | 17 $ | 73 | **92.1 %** |
| SANDUSDT | 322 $ | 59 $ | 64 | **89.6 %** |
| TAOUSDT | 571 $ | 18 $ | 70 | **80.3 %** |
| TIAUSDT | 529 $ | 30 $ | 36 | **90.0 %** |
| HBARUSDT | 805 $ | 35 $ | 44 | **80.6 %** |

_Les 15 symboles au notionnel reporté le plus élevé._

## Comment lire ça

**« Reporté », pas « perdu ».** Le plafond rend l'ordre PARTIEL ; le reste se
remplit aux pas suivants, aux prix de ces pas. L'effet sur le PnL n'est donc pas
une amputation du notionnel mais un DÉCALAGE d'exécution : le portefeuille
atteint sa cible plus tard, à un prix qui a bougé entre-temps. Sur un signal à
horizon 4 h, un report de plusieurs pas consomme une part appréciable de
l'horizon — et c'est précisément ce que l'ancienne règle rendait invisible.

**Les chiffres de PnL vont empirer, et c'est le but.** Ce qui passait avant était
une fiction : un ordre de plusieurs milliers de dollars sur un carnet qui en
affiche quelques centaines n'était pas rempli au mid moins deux bps. Toute mesure
d'edge net produite sous l'ancienne règle héritait de cette fiction.

**L'ordre médian ne fait que 19 $.** Le taux de report en notionnel (63.2 %) est
donc porté par une minorité de gros ordres, pas par le flux courant. C'est une
bonne nouvelle pour la faisabilité et une mauvaise pour les mesures passées : ce
sont les grosses positions, celles qui pèsent le plus dans le PnL, qui étaient
les plus fictives.

**Un symbole sans sonde n'est pas un symbole liquide.** Le plafond est fail-open
— aucune sonde, aucun plafond — parce qu'un plafond inventé serait pire qu'aucun.
Mais 0 ordres tombent dans ce cas et leur capacité est INCONNUE, pas large.

**La profondeur au meilleur limite est une borne basse.** Un ordre valant trois
fois le meilleur limite ne paie pas forcément trois fois plus : les niveaux
suivants sont souvent proches. La vraie réponse demande un carnet L2 complet, que
`data/microstructure_reduced` ne capture que pour BTC, ETH et SOL. Ce plafond est
donc conservateur par construction — dans la direction où une porte doit l'être.

