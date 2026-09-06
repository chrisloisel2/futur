# Piste 9 — Rotation fondamentale protocolaire

> ## ❌ VERDICT : NO_EDGE (2026-07-18, commit distant `eef8646`)
>
> Accélération fees/revenus DefiLlama, 19 protocoles × 228 semaines
> (2022-02 → 2026-06), spread tercile haut vs univers (orthogonal
> marché) : −0,11 %/semaine (t = −0,4), IC = −0,02, signe instable,
> secondaires (revenus, momentum, tercile bas, 2 sem) tous négatifs —
> alors que le biais de survivance aurait dû *aider*. Confirme la
> littérature « fondamentaux non pricés » plutôt que les récits value.

Long des tokens dont les **fondamentaux accélèrent** (fees, revenus,
utilisateurs, volume DEX), univers restreint aux perps liquides.
Horizon : 1–4 semaines. C'est la piste la plus « lente » de la factory —
elle diversifie les horizons courts des pistes 1–8.

## Ce que dit la recherche — et ses pièges

- Liu, Tsyvinski & Wu, *Common Risk Factors in Cryptocurrency* (JF 2022,
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3379131) : trois
  facteurs — **marché, taille, momentum** — capturent l'essentiel de la
  cross-section. Tout signal fondamental doit donc prouver qu'il ajoute
  au-delà de ces trois-là.
- Liu & Tsyvinski, *Risks and Returns of Cryptocurrency* (RFS,
  https://academic.oup.com/rfs/article-abstract/34/6/2689/5912024) : les
  facteurs d'**adoption réseau** (utilisateurs, adresses actives) sont
  pricés ; les facteurs de coût de production ne le sont pas.
- ⚠️ *The surprising irrelevance of total-value-locked* (Economics Letters
  2025, https://www.sciencedirect.com/science/article/abs/pii/S0165176525005105) :
  les portefeuilles triés sur **TVL brute n'ont aucun alpha** une fois le
  bêta marché retiré. La TVL seule est un piège — ne l'utiliser qu'en
  *variation nette* (net de prix) et combinée aux revenus.
- *The return of (I)DeFiX* (https://arxiv.org/abs/2204.00251) : un ratio
  **book-to-market spécifique DeFi** (TVL/market cap) a un pouvoir
  prédictif sur les retours DeFi — le *ratio* valorisation/usage
  fonctionne mieux que le niveau d'usage.
- *What drives DeFi market returns?* (JIMF,
  https://www.sciencedirect.com/science/article/am/pii/S1042443123000549) :
  le facteur marché domine tout le reste — d'où la nécessité d'une
  construction **market-neutre ou bêta-couverte**, sinon on re-teste le bêta.

Synthèse : le signal candidat n'est pas « bons fondamentaux » mais
**accélération des revenus/usage, normalisée par la valorisation, orthogonale
au marché, à la taille et au momentum**.

## Sous-signaux à tester

1. **Accélération des fees/revenus** : Δ 30 j vs Δ 90 j des fees payés au
   protocole et des revenus token-holders (les fees sont difficiles à
   truquer, contrairement à la TVL et aux « utilisateurs »).
2. **Ratio P/S on-chain** : market cap / revenus annualisés, en z-score
   cross-section — la jambe « value » du book-to-market DeFi.
3. **TVL nette** : variation de TVL corrigée de l'effet prix des
   collatéraux (sinon la TVL n'est qu'un proxy retardé du prix).
4. **Pression d'émission** : unlocks + émissions à venir en % du float —
   filtre négatif (ne jamais être long avant un gros unlock).
5. **Volume DEX / market cap** : usage réel de l'actif vs sa valorisation.

## Données (gratuites)

- **DefiLlama API** (gratuit, historique) : `/summary/fees/{protocol}`,
  `/tvl`, `/emissions` — fees, revenus, TVL, unlocks par protocole.
- Mapping protocole → token → perp Binance : à construire et versionner
  (univers point-in-time, même exigence que ctrend v1).
- Volume DEX : DefiLlama `/overview/dexs`.
- Cadence : archivage quotidien suffit (horizon hebdo), même logique
  cron que `bin/archive-derivs`.

## Protocole de rejet

- Alpha résiduel après régression sur les 3 facteurs LTW (marché, taille,
  momentum) — pas seulement un backtest brut.
- Univers point-in-time strict (les protocoles morts disparaissent de
  DefiLlama : biais de survivance majeur si on prend la liste actuelle).
- Coûts ×2 + délai d'une barre + gates du [README parent](../README.md).
