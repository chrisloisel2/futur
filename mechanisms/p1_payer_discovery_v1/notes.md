# p1_payer_discovery_v1 — notes (jamais un résultat)

**Ce que c'est** : une MESURE de coût, pas un alpha. `run.py` ne passe pas par
`run_mechanism` et n'inscrit aucun essai dans aucune famille : il n'y a pas de cible,
donc pas de regard. `spec.json` existe parce qu'un mécanisme sans spec n'existe pas et
que la suite de tests valide chaque spec livrée ; sa famille (`microstructure`) désigne
le terrain mesuré, pas une hypothèse.

**Question** : existe-t-il une venue et un mode (maker-only ou non) où le coût réel
aller-retour descend assez pour qu'un edge brut de l'ordre de ceux mesurés par P0
(0,78 bps microstructure, 1,97 bps cross-exchange) puisse passer le mur ×3 ?

**Données** : BBO + trades `microstructure_reduced/raw` — Binance, OKX, Hyperliquid
(BTC/ETH/SOL). **Bybit n'est pas collecté** : pour Bybit seuls les frais publiés sont
disponibles, aucun spread ni latence mesurés. Frais : barèmes publiés, sourcés et datés,
déclarés « non mesurés sur un compte ».

**Limites déclarées d'avance** : L1 seulement (pas de profondeur au-delà du meilleur
limite → le slippage au-delà du touch n'est pas mesurable, seule la capacité au touch
l'est) ; file d'attente inconnue (hypothèse : fin de file) ; tailles non comparables entre
venues (Binance en unités, OKX en contrats) — les mids le sont.

## 2026-09-10 — ce que la mesure a appris sur elle-même, avant les chiffres finaux

- **Artefact de résolution ×7.** Le demi-spread « effectif » mesuré contre un mid de grille
  1 s donnait 0,52 bps de médiane sur Binance BTC ; contre la cote **exacte** prévalant au trade
  (3,8 M cotes / 160 k trades sur 2 h), il vaut **0,07** (moyenne 0,45, p90 1,10). Le mid ne
  bouge pas la plupart des secondes (médiane |Δmid| 1 s = 0,000 bps) mais bouge *quand il y a des
  trades* (p90 0,64). Corrigé : échantillon exact de 2 h par jour ; le plancher taker utilise la
  **moyenne** exacte, parce que la queue (59 % des trades seulement au touch) est un coût réel.
- **« Adverse selection » négative sur SOL.** La perte du passif mesurée du prix de fill au mid
  futur *inclut* le demi-spread gagné au fill : négligeable sur BTC (spread 0,01 bps), égal à
  la dérive sur SOL (spread ≈ 1 tick ≈ 0,96 bps). Décomposé en `dérive perdue − ½-spread gagné`.
  Un plancher maker négatif n'est donc pas un edge : c'est la marge du market maker,
  conditionnelle au fill (P ≈ 0,3-0,4 en fin de file) et au tier.
- **Cross-exchange.** Un trade de dislocation est taker par nature sur la jambe disloquée ; la
  décision compare désormais `taker + maker`, pas `maker + maker`.
- **Tiers.** Le « −0,5 bps maker OKX » du premier passage est un programme market-maker hors
  barème ; le sommet standard est VIP8 (maker 0,8 / taker 2,0 bps, ~2 G USDT / 30 j). Binance
  VIP9 (maker 0 / taker 1,7) exige 30 G USD de volume futures sur 30 j **et** 5 500 BNB. Ces
  seuils sont la condition réelle de toute réouverture et figurent dans la table.
- **Le run ne relit pas son code** : chaque correction ci-dessus a exigé un nouveau passage ;
  `--recompute` reprend désormais les mesures et ne recalcule que frais, planchers et décision.
