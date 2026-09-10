# p1_payer_discovery — MEASUREMENT

| hyperliquid/ETHUSDT/best/taker | 17.14 |
| hyperliquid/SOLUSDT/best/taker | 18.48 |
| hyperliquid/BTCUSDT/vip0/taker | 28.43 |
| hyperliquid/ETHUSDT/vip0/taker | 29.74 |
| hyperliquid/SOLUSDT/vip0/taker | 31.08 |
| okx/BTCUSDT/vip0/taker | 31.66 |
| okx/ETHUSDT/vip0/taker | 32.15 |
| binance/BTCUSDT/vip0/taker | 32.31 |
| binance/ETHUSDT/vip0/taker | 32.75 |
| binance/SOLUSDT/vip0/taker | 33.91 |
| okx/SOLUSDT/vip0/taker | 33.98 |

Méthode : demi-spread effectif contre la cote EXACTE prévalant au trade, sur 2 h par jour (12-13 h UTC) ; la méthode mid-de-grille-1s le gonflait ×7 (calibré). Limites : L1 seulement (pas de profondeur au-delà du touch) ; fin de file supposée pour P(fill) ; 3 jours ; tailles OKX converties par ctVal public.
