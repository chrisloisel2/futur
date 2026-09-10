# P1.1 — Fee Source Audit (2026-09-10)

Objet : remplacer les frais de source tierce de P1 par des sources **officielles** quand elles existent,
séparer trois classes qui ne se confondent jamais, et recalculer le verdict sans modifier VIP0 sans preuve.
Manifeste : `data_lake/manifests/published_fee_schedules_2026-09-10.json` (sha256 `4f1772107b37fc7a…`).
Règle, testée (`research_kernel/tests/test_fee_sources.py`) : **un verdict final ne repose que sur des
entrées `source_class = official`** ; une source tierce n'est admise qu'en repli explicite, jamais dans un
verdict final ; les frais réels du compte ne sont jamais copiés dans le manifeste.

## Les trois classes

| classe | ce que c'est | où |
|---|---|---|
| **published schedule** | barème publié par la venue, lu le jour même, URL citée | manifeste, `source_class: official` |
| **account actual fee** | frais réels du compte, lus sur l'endpoint compte | `scripts/fetch_account_fees.py` → `account_fees_<date>.json` (jamais écrit sans clé) |
| **third-party fallback** | source tierce, admise seulement faute d'officiel, **jamais finale** | manifeste, `source_class: third_party_fallback`, `final: false` |

## Par venue

### Binance — USDⓈ-M
- **Officiel, obtenu** : FAQ *Futures Fee Structure* (`faq/360033544231`) → regular **maker 0,02 % / taker 0,05 %**, remise BNB **10 %**, « volumes futures = 5× spot » ; page `/en/fee/vip` → VIP9 **spot ≥ 4 G USD, ≥ 5 500 BNB**.
- **Officiel, non obtenu** : la table futures par VIP. `/en/fee/futureFee` est rendue côté client (curl : 0 octet), la page VIP institutionnelle rend « No records found », quatre endpoints `bapi` répondent 404.
- **Repli tiers, non final** : VIP9 futures maker 0 / taker 1,7 bps (tradersunion, datawallet).
- **Seuil VIP9 futures — item 6** : **non corrigé à 25 G.** Aucune source officielle ne le confirme ; les tiers se contredisent (25 G / 30 G) et la règle officielle « 5× spot » donnerait **20 G**. Classé `unconfirmed`, les trois candidats consignés. Seul `/fapi/v1/commissionRate` tranchera pour un compte donné.

### OKX — swap USDT
- **Officiel, obtenu** : *Updates to Global Fee Framework* (effectif 2025-11-25) — regular **0,020 / 0,050 %** ; VIP6 0/0,025 ; VIP7 −0,002/0,020 ; **VIP8 −0,005/0,020 (≥ 2 G USD / 30 j)** ; VIP9 −0,005/0,015 (≥ 20 G). *Advance Notice* du **2026-09-09** (15:00-17:00 UTC+8) — VIP7 → −0,001/0,023 ; **VIP8 → −0,0025 / 0,020**.
- **Item 7** : la source officielle confirme un maker **négatif** à VIP8, mais la valeur **applicable le 2026-09-10** est −0,0025 % = **−0,25 bps**, pas −0,5. Le 0,8 (tiers, faux) est remplacé par **−0,25** ; −0,5 est consigné comme valeur en vigueur *jusqu'au* 2026-09-09. VIP9 (−0,5 / 1,5) existe officiellement à ≥ 20 G ; l'avis du 09-09 ne le mentionne pas.

### Bybit — perpétuel USDT
- **Officiel, obtenu** : page *Trading Fee Structure* (« Last updated 2026-09-02 »), rendue serveur, parsée en local (`WebFetch` expirait) : **VIP0 taker 0,0550 % / maker 0,0200 %** ; VIP1-5 (4,0/1,8 → 3,2/1,0 bps) ; seuils dérivés 30 j VIP1 ≥ 10 M … VIP5 ≥ 250 M, Supreme ≥ 500 M (volume API ≤ 20 %) ; onglet Pro (volume API > 20 %) : **Pro 4 ≥ 1,5 G : 0,0200 / 0,0010 % ; Pro 5 ≥ 3 G : 0,0180 / 0,0000 %** ; Pro 6 ≥ 5 G, ligne de frais non capturée proprement.
- **Retenu comme « best »** : **Pro 5 (officiel)** — maker 0 / taker 1,8 bps. Le « Pro 6 = 0 / 1,8 » tiers est cohérent mais n'est pas utilisé.
- Aucune donnée BBO/trades sur disque : Bybit n'a que des frais, pas de spread ni de latence mesurés.

### Hyperliquid
- **Officiel, complet** (docs) : base **taker 0,045 % / maker 0,015 %** ; tiers 14 j jusqu'à tier 6 (> 7 G : 0,024 / 0,000) ; **rebates maker** tier 1-3 (> 0,5 / 1,5 / 3 % du volume maker : −0,001 / −0,002 / −0,003 %) ; endpoint `userFees`.
- « best » = rebate tier 3 + tier 6 : **maker −0,3 / taker 2,4 bps**.

## Endpoints compte — à implémenter, implémentés en lecture seule

`scripts/fetch_account_fees.py` (inerte sans clé ; n'écrit rien tant qu'aucune venue ne répond) :

| venue | endpoint | auth |
|---|---|---|
| Binance | `GET /fapi/v1/commissionRate?symbol=` | HMAC-SHA256 sur la query, `X-MBX-APIKEY` |
| OKX | `GET /api/v5/account/trade-fee?instType=SWAP` | HMAC-SHA256 base64(ts+méthode+path) + passphrase ; **signe inversé** dans la réponse |
| Bybit | `GET /v5/account/fee-rate?category=linear` | HMAC-SHA256(ts+key+recvWindow+query) |
| Hyperliquid | `POST /info {type: userFees, user}` | aucune (adresse) |

Statut : **non exécutés** (aucune clé dans l'environnement). Tant qu'ils ne le sont pas, « VIP0 » reste une
hypothèse de barème, pas le frais du compte.

## Ce que l'audit change au verdict

- **VIP0 : inchangé, fermé** — 2/5, 2/5, 2/5,5, 1,5/4,5 (tous officiels) ; aucun mode, aucune venue sous 0,26 bps.
- **Best institutional maker-only, verdict FINAL (officiel seul)** : **OUI** sur **OKX SOL** (VIP8 −0,25 bps, aller-retour maker **−0,69 bps**, P(fill 30 s) 0,30, ≥ 2 G USD / 30 j) et **Hyperliquid** BTC/ETH/SOL (rebate tier 3, P(fill) 0,16-0,24). Binance SOL passe en **non final** (tier VIP9 de source tierce).
- **Cross-exchange : fermé** à tout tier — jambe disloquée taker + jambe maker : 4,09 bps au mieux (Binance SOL, non final) puis 4,63 (OKX SOL), contre 0,66 requis.
- **Aucun live.** Les tests P0/P1 sont verts (212).
