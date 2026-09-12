# Tape pre-Binance du second venue (meme actif, memes heures)

Genere 2026-09-12T17:31:40+00:00. Controle honnete du test anti-wash : le meme jeton, les memes heures de fenetre, sur une autre place que MEXC.

| statut | evenements |
|---|---|
| collected | 65 |
| no_candidate | 13 |
| not_usable | 35 |

| tapes utilisables (>= 36 h dans W) | evenements |
|---|---|
| 0 | 48 |
| 1 | 22 |
| 2 | 43 |

| place marche | tapes utilisables |
|---|---|
| bybit perp | 16 |
| bybit spot | 9 |
| gate perp | 9 |
| gate spot | 11 |
| kucoin perp | 14 |
| kucoin spot | 41 |
| okx perp | 2 |
| okx spot | 6 |

Tentatives : empty 41, ok 149.

Limites : Gate ne sert que ses 10 000 dernieres bougies (~416 j en 1 h) ; Bybit spot et Gate n'ont pas de date de cotation (la donnee decide) ; KuCoin futures ne sert pas tout l'historique.
Borne de cloture : chaque bougie stockee cloture au plus tard a t0. Rien apres t0 n'est demande.
