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
| **ordres qui dépassent la profondeur** | **1658, soit 19.4 %** |
| notionnel exécuté | 9 851 326 $ |
| **notionnel non remplissable au pas demandé** | **6 212 695 $, soit 63.1 %** |
| ordre médian | 19 $ |
| profondeur médiane | 797 $ |

Le plafond passe de mordre **1,0 %** des ordres à en mordre **19.4 %**.

## Par alpha

| alpha | ordres | plafonnés | notionnel | reporté | % reporté |
|---|---|---|---|---|---|
| `LIQ_CASCADE_FAR_FROM_LOW_V1` | 25 | 24 (96.0 %) | 250 602 $ | 222 198 $ | **88.7 %** |
| `LIQ_CASCADE_REPEAT_V1` | 1118 | 396 (35.4 %) | 2 765 739 $ | 2 271 423 $ | **82.1 %** |
| `BTC_LEAD_ALT_CASCADE_V1` | 89 | 79 (88.8 %) | 1 186 627 $ | 959 848 $ | **80.9 %** |
| `SHORT_COVERING_CONTINUATION_V1` | 8371 | 1523 (18.2 %) | 8 227 655 $ | 4 986 713 $ | **60.6 %** |

## Les symboles où ça se joue

| symbole | profondeur | ordre médian | ordres plafonnés | % du notionnel reporté |
|---|---|---|---|---|
| BCHUSDT | 683 $ | 35 $ | 74 | **91.4 %** |
| ARBUSDT | 412 $ | 111 $ | 110 | **89.3 %** |
| ALGOUSDT | 200 $ | 16 $ | 127 | **92.1 %** |
| IMXUSDT | 738 $ | 2 032 $ | 86 | **84.0 %** |
| FILUSDT | 868 $ | 35 $ | 66 | **82.4 %** |
| SEIUSDT | 335 $ | 70 $ | 36 | **94.5 %** |
| GRTUSDT | 598 $ | 267 $ | 96 | **80.6 %** |
| WLDUSDT | 3 323 $ | 12 $ | 18 | **74.9 %** |
| JUPUSDT | 1 132 $ | 64 $ | 10 | **95.4 %** |
| APTUSDT | 558 $ | 21 $ | 52 | **86.1 %** |
| RUNEUSDT | 219 $ | 17 $ | 73 | **92.2 %** |
| SANDUSDT | 326 $ | 59 $ | 64 | **89.5 %** |
| TAOUSDT | 571 $ | 18 $ | 70 | **80.3 %** |
| TIAUSDT | 529 $ | 30 $ | 36 | **90.0 %** |
| HBARUSDT | 805 $ | 35 $ | 44 | **80.6 %** |

_Les 15 symboles au notionnel reporté le plus élevé._

## Le multiple est une hypothèse, et voici ce qu'elle porte

Le plafond au **premier niveau seul est trop strict**, et il faut le dire : la
liquidité traversable n'est pas la meilleure limite. Un ordre valant quelques fois
le premier niveau ne « dépasse pas le carnet », il traverse quelques niveaux et
paie quelques bps de plus. Le bon plafond serait la profondeur **cumulée** jusqu'à
une concession acceptée, la concession étant facturée dans le coût.

**Cette profondeur cumulée n'est mesurable nulle part ici.** Les sondes du
frozen-50 ne portent que le niveau 1 (`bid_qty`, `ask_qty`, `top_*_notional_usd`).
`data/hyperliquid/l2` porte bien une profondeur agrégée, mais sur une autre
plateforme et sans bande de prix déclarée. Le multiple est donc une hypothèse
déclarée, et voici toute la plage qu'elle commande :

| multiple supposé | ordres plafonnés | notionnel reporté | lecture |
|---|---|---|---|
| ×1.0 | 19.4 % | **63.1 %** | premier niveau seul — trop strict |
| ×3.0 | 12.4 % | **47.1 %** |  |
| ×6.4 | 8.8 % | **32.1 %** | ce qu'il faudrait pour une position de 6 667 $ |
| ×10.0 | 5.8 % | **21.6 %** |  |
| ×25.0 | 1.8 % | **6.5 %** |  |
| ×50.0 | 0.3 % | **1.0 %** | revient au comportement de l'ancien plafond open-interest |

**De 63 % à 1 % de report, piloté entièrement par un nombre que je ne peux pas
mesurer.** Remplacer une mesure trop optimiste (l'open interest, 1,0 %) par une
mesure trop pessimiste (le premier niveau, 63 %) ne serait pas un progrès — ça
tuerait des candidats réels. Ce qui est un progrès, c'est que la plage soit
visible et que l'hypothèse porte un nom.

### Ce qu'il faut collecter pour que ça devienne une mesure

Des instantanés de carnet **par niveau de prix** pour le frozen-50 — pas du BBO.
Le collecteur microstructure produit déjà du L2 pour BTC, ETH et SOL ; l'étendre
à l'univers, même à basse cadence, transforme le multiple en profondeur cumulée
observée. C'est une ligne du plan de collecte, pas une constante à mieux deviner.

### La capacité à la taille cible

| grandeur | valeur |
|---|---|
| capital | 200 000 $ |
| panier | 15 long / 15 short, dollar-neutre |
| notionnel par position | **6 667 $** |
| profondeur médiane au premier niveau | 797 $ |
| **rapport** | **×8.4** |

C'est **la première fois que la capacité s'approche d'être contraignante à la
taille cible**. Elle ne l'est pas si la profondeur cumulée vaut au moins 8.4 fois
le premier niveau — ce qui est plausible et non vérifié. À ×8.4 exactement, 25.5 %
du notionnel serait encore reporté.

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

**L'ordre médian ne fait que 19 $.** Le taux de report en notionnel (63.1 %) est
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

