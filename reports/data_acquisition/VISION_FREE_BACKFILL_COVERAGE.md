# VISION FREE BACKFILL — coverage (2026-09-11T22:46:44 UTC)

174 H2 windows (t0 − 30 min → t0 + 6 h), 174 manifests, 1.47 GB on disk under `data/vision_backfill/` (gitignored). sha256 recomputed for every file; **1364 of 1364 verified against Vision's `.CHECKSUM`** (the rest: no `.CHECKSUM` published for that file). No return computed, no price column read, no signal, no verdict.

| dataset | files ok / expected | checksum verified |
|---|---|---|
| aggTrades | 177 / 177 | 177 |
| bookDepth | 168 / 177 | 168 |
| fundingRate | 171 / 174 | 171 |
| indexPriceKlines | 147 / 177 | 147 |
| klines | 177 / 177 | 177 |
| markPriceKlines | 177 / 177 | 177 |
| metrics | 177 / 177 | 177 |
| premiumIndexKlines | 170 / 177 | 170 |

Core coverage definition: markPriceKlines + aggTrades + (indexPriceKlines or premiumIndexKlines), every day of the window (the index is recoverable from mark and premium by identity index = mark − premium; that identity is documented, not computed here).

Files not yet published by Vision (period not finished + 3 d lag; re-probed on every run, not counted as holes): {'fundingRate@2026-09': 3}

## The seven answers

1. Events covered by free data (core complete for every day of the window): **173 / 174**
2. Events that become clean (≥ 90) with Vision alone: **0** — the announcement body (15 pts) and the actual fee (3 pts) are not on Vision; projected after P7 + P8: **85** clean
3. Events near-usable (70–89) after Vision: **85**; partial (40–69): 89; unusable (< 40): 0
4. Events that still require the announcement body: **174** (P7)
5. Events that still require read-only account fees: **174** (P8)
6. Events that still require cross-venue precedence: **84** (P9)
7. Events that still need a paid provider window (no Vision depth, or no index reference, on the launch day): **9** — LUNA2USDT, BANKUSDT, DEEPUSDT, MEMEFIUSDT, DOLOUSDT, SXTUSDT, B2USDT, ZKJUSDT, SPCXUSDT

Score distribution after Vision: {51: 5, 59: 2, 61: 3, 64: 77, 69: 2, 74: 85}

Projected after P7 (body): {66: 5, 74: 2, 76: 3, 79: 77, 84: 2, 89: 85}

Projected after P7 + P8 (body + fee): {69: 5, 77: 2, 79: 3, 82: 77, 87: 2, 92: 85}
