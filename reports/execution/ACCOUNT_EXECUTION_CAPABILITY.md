# ACCOUNT_EXECUTION_CAPABILITY — ce que le compte peut réellement exécuter (P2C)

*2026-09-10. Lecture seule. Aucun ordre n'est envoyé, aucune clé avec permission de trading n'est utilisée.*

## Pourquoi

P1/P1.1 ont établi le **barème publié** (VIP0 : 2/5, 2/5, 2/5,5, 1,5/4,5 bps — tous officiels).
Un barème n'est pas un coût : le coût à battre est **celui du compte** — ses frais réels, ses fills,
la part maker qu'il obtient, ses rejets, sa latence. Tant qu'il n'est pas lu, chaque verdict de P1
reste conditionnel. Ce document dit ce qui est lisible, comment, et ce qui ne l'est pas.

## Ce qui est lisible sur les endpoints compte (implémenté, inerte sans clé)

| venue | endpoint | donne | permission |
|---|---|---|---|
| Binance USDⓈ-M | `GET /fapi/v1/commissionRate` | maker/taker du compte par symbole | *Enable Reading* seule |
| | `GET /fapi/v1/userTrades` | fills : prix, qty, commission, `maker` (bool), realizedPnl, `time` | |
| | `GET /fapi/v1/allOrders` | ordres : statut, type, TIF, `time`/`updateTime`, exécuté/initial | |
| OKX | `GET /api/v5/account/trade-fee` | niveau, maker/taker (signe inversé) | *Read* |
| | `GET /api/v5/trade/fills-history` | fills : px, sz, fee, `execType` (T/M) | |
| | `GET /api/v5/trade/orders-history-archive` | ordres 3 mois | |
| Bybit | `GET /v5/account/fee-rate` | maker/taker par symbole | *Read-only* |
| | `GET /v5/execution/list` | fills : execPrice, execQty, execFee, `isMaker`, `execType` | |
| | `GET /v5/order/history` | ordres | |
| Hyperliquid | `POST /info userFees` | taux add/cross du compte | adresse seule |
| | `POST /info userFills` | fills : px, sz, fee, `crossed` | |

Scripts : `scripts/fetch_account_fees.py` (P1.1, frais) et `scripts/fetch_account_execution.py`
(P2C, frais + fills + ordres). Sans clé dans l'environnement ils n'appellent rien et n'écrivent
rien. Avec clés read-only : `data_lake/manifests/account_execution_<date>.json` (brut, hashé) et
`reports/mechanisms/p1_payer_discovery/account_actual_fee_summary.json` (frais réels, part maker
observée, nombre de fills).

## Ce que ces endpoints NE donnent PAS

| mesure | pourquoi elle manque | comment l'obtenir |
|---|---|---|
| latence soumission → ack, soumission → fill | l'exchange ne connaît pas l'instant d'envoi | journal **côté client** à l'envoi (P1.3 `execution_shadow_logger`, en paper) |
| slippage réel vs mid à la soumission | idem : il faut le mid **au moment de l'envoi** | même journal, joint au BBO local (`microstructure_reduced`) |
| taux de rejet post-only, cancel success | les rejets n'apparaissent pas dans `userTrades` | user data stream (Binance `ORDER_TRADE_UPDATE`, Bybit `order` privé) en paper |
| fills partiels, file d'attente | partiellement dans `allOrders` (`executedQty`) ; la position en file jamais | shadow logger + L2 (P2, dataset 3) |

## Ordre d'obtention

1. **P1.2 `account_actual_fee_snapshot`** — clés read-only, exécuter `fetch_account_fees.py` puis
   `fetch_account_execution.py` ; le résumé remplace « VIP0 publié » par « frais du compte » dans P1.
2. **P1.3 `execution_shadow_logger`** — en **paper** seulement : journaliser localement chaque
   intention (ts envoi, mid, côté, taille) et la réponse simulée ; jamais un ordre réel.
3. Seulement ensuite : recalculer le mur de coût avec des frais et une part maker **observés**.

## Verdict attendu

« Quel coût réel dois-je battre ? » — sans clé, la réponse honnête reste : **le barème VIP0 publié,
soit 9,5–11,3 bps aller-retour en taker**, jusqu'à preuve du compte. Aucun live.
