# Reduced-scope microstructure L2 collector — design + estimate (NOT LAUNCHED)

Date: 2026-08-31. Author: design pass requested against `MICROSTRUCTURE_OFI_CLUSTER_V1`
(`configs/live_alpha_registry.yaml`, `operational_status: DATA_BLOCKED`). Scope of this
document: **design and disk estimate only**. Nothing described here has been started.

Priority note (explicit, from the mission brief): this is lower priority than
cross-sectional momentum (+89bps signal, `CROSS_SECTIONAL_MOMENTUM_PIT_V1`/`_LIVE_V1`)
in the current Live Alpha Lab resource allocation. The microstructure cluster's own
source report (`w7_microstructure/REPORT.md`) rates it `NEEDS_FULL_VALIDATION`, not
`PROMISING` — gross edge (0.72-1.63bps) is close to a maker leg but net-of-adverse-selection
viability is unproven. Disk for this collector should not be spent ahead of higher-confidence
work.

---

## 0. Pre-check: is anything already collecting this? (avoid the duplicate-collector mistake)

Per the explicit lesson from earlier today (a duplicate `futur-derivatives.service` unit
caused two collectors to run with conflicting symbol lists because the existing unit wasn't
discovered first), the following was checked **before** any design work:

- `systemctl --user list-units --all 2>/dev/null | grep -iE "physic|micro|market|l2|tick"` →
  **no matches**. No systemd unit (active, inactive, or failed) related to
  market-physics/microstructure/L2/tick collection exists.
- `/home/qbee/.config/systemd/user/*.service` (full listing, 34 units) → no
  `market_physics`/microstructure-named unit. The only L2-adjacent unit is
  `futur-hl-collector.service` (`hl_metaorders_collector.py`), described below — different
  purpose, not a substitute.
- `/home/qbee/futur/deploy/systemd/` → same 34 units mirrored, same result.
- `ps aux | grep -iE "python.*collect|market_physics"` → only
  `run_derivatives_collector.py` (Binance USDM derivatives, unrelated to L2 book data) and
  `hl_metaorders_collector.py` (see below) are running. `collect_market_physics_v3.py`
  itself is **not running anywhere** — the 66GB dataset in `futur-data-v2` is a finished,
  one-off ~28h capture, not an ongoing feed.

**One partial-overlap worth flagging**: `futur-hl-collector.service`
(`scripts/hl_metaorders_collector.py`, active) already opens a Hyperliquid websocket and
subscribes to `l2Book` (polled per-coin via `post`, not a continuous diff stream) and
`trades`, computing best-limit/spread/top-N depth-imbalance features **on the fly** for
metaorder/TWAP detection. It dedups to at most one row per `(coin, time_ms)` millisecond
bucket and does not persist a raw per-tick BBO/trade tape usable for OFI/microprice
reconstruction — it is a different, coarser, derived-feature pipeline for a different
purpose. It is **not a substitute** for the dedicated `bbo` subscription this design calls
for, but the two should not be allowed to open independent, uncoordinated Hyperliquid
connections if this design is later implemented — worth a short coordination check
(reuse the connection / write to a distinct namespace) at implementation time, not before.

**Conclusion: greenfield for microstructure L2 specifically.** No duplicate risk found for
*this* collector, unlike the derivatives incident.

### Disk state (checked live, not assumed)

```
Filesystem      Size  Used Avail Use% Mounted on
/dev/nvme0n1p4  915G  837G   32G  97% /
```

**32GB free, 97% utilized — matches the brief's "~32GB free," not worse, but flagged
prominently anyway: 97% utilization is a whole-system risk independent of this specific
question.** `/home/qbee/futur` alone is 203GB, of which `data/` is 111GB (existing
collectors: `derivatives_raw` 15GB, `hyperliquid` 3.5GB, `options_backfill` 584MB,
`positioning` 46MB — confirms the brief's claim that existing live collectors are much
lighter than a tick-level L2 feed would be).

---

## 1. Current full-scope collector: what it actually captures and why it's 56GB/day

Source: `/home/qbee/futur-data-v2/scripts/collect_market_physics_v3.py` (thin CLI) →
`market_physics_v3.collectors.runtime.run_many` → per-venue subscriptions in
`market_physics_v3/collectors/specs.py`, written via
`market_physics_v3/collectors/writer.py`.

### 1.1 What it subscribes to, per venue (`specs.py`)

| venue | full-depth / deep-book stream (expensive) | dedicated top-of-book stream (cheap) | trades | other |
|---|---|---|---|---|
| binance | `{sym}@depth@100ms` (diff, every 100ms) | `{sym}@bookTicker` | `{sym}@aggTrade` | `markPrice@1s`, `forceOrder` |
| okx | `books` channel (full diff) | `bbo-tbt` (dedicated) | `trades` | `open-interest`, `funding-rate`, `mark-price`, `index-tickers`, `liquidation-orders` |
| hyperliquid | `l2Book` (full snapshot pushes, less frequent) | `bbo` (dedicated) | `trades` | `activeAssetCtx` |
| bybit | `orderbook.50` (**no dedicated top-of-book stream exists on this venue** — confirmed both by `specs.py`'s subscription list and by the W7 report's data notes) | — | `publicTrade` | `allLiquidation`, `tickers` |

### 1.2 What it writes (`writer.py`, `schema.py`)

Two parallel outputs, both JSONL (`json.dumps(..., sort_keys=True)`, one line per record,
`fdatasync` every 512 rows or 1s):

1. **`data/market_physics_v3/raw/{book_events,trades,derivatives}/venue=.../symbol=.../date=.../events.jsonl`**
   — normalized, **exploded per price level**: a single depth-diff websocket message that
   touches N price levels becomes **N separate `BookEvent` JSONL rows**, each carrying the
   full record (`venue`, `symbol`, `event_ts_ns`, `receive_ts_ns`, `sequence_id`,
   `event_type`, `side`, `price`, `qty`, `order_count`, `source_stream`,
   `first_sequence_id`, `previous_sequence_id`, `_record_type`). This is the dominant cost
   driver — verified below.
2. **`data/market_physics_v3/raw_wire/venue=.../date=.../messages.jsonl`** — raw,
   un-exploded websocket payloads (one row per message, verbatim JSON), kept as a replay
   safety net. This is a **separate, additional** ~13.7GB on top of the 66GB headline figure
   (confirmed: `du` of `raw/` alone = 66GB; `raw_wire/` = 7.8GB binance + 3.3GB okx + 2.2GB
   bybit + 441MB hyperliquid ≈ 13.7GB extra, not counted in the "66GB" the mission cites).

### 1.3 Measured byte breakdown (bottom-up, from the actual 66GB dataset)

```
raw/                    66G
├── book_events/        65G   ← 98.5% of the total. This is the cost driver.
│   ├── venue=binance    41G  (BTC+ETH+SOL)
│   ├── venue=okx        17G
│   ├── venue=bybit      6.6G
│   └── venue=hyperliquid 1.1G
├── trades/              1.0G (241M binance, 230M okx, 418M bybit, 115M hyperliquid)
└── derivatives/         689M (138M binance, 314M okx, 53M bybit, 185M hyperliquid)
```

**Full-depth vs. dedicated-BBO split, measured directly on one representative file**
(`venue=binance/symbol=BTCUSDT/date=2026-08-15/events.jsonl`, 9.47M lines, 3.24GB — grepped
by `source_stream`, not estimated):

| venue | full-depth stream bytes | dedicated-BBO stream bytes | BBO share of book_events |
|---|---:|---:|---:|
| binance (`depth` vs `bookTicker`) | 2,707,097,317 (2.71GB) | 524,862,594 (525MB) | **16.2%** |
| okx (`books` vs `bbo-tbt`) | 1,012,307,415 (1.01GB, of 1.11GB file) | 97,019,694 (97MB) | **8.7%** |
| hyperliquid (`l2Book` vs `bbo`) | 45,498,675 (45.5MB, of 89.3MB file) | 43,547,571 (43.5MB) | **48.75%** (HL pushes infrequent full snapshots rather than continuous diffs, so its deep stream is proportionally much cheaper than binance/okx's) |
| bybit | n/a — no dedicated BBO stream exists on this venue; canonical price must be derived from the full `orderbook.50` diff stream itself | n/a | n/a |

This confirms the mission's premise precisely: **the full order-book depth diff stream
(`depth`/`books`/`l2Book`), not the top-of-book stream, is what makes this collector cost
56GB/day.** Binance and OKX in particular spend 84-91% of their book-event bytes on levels
beyond the best price — levels that none of the five target features (OFI, microprice,
best-depth imbalance, spread, aggressive trade flow) require.

### 1.4 Crossed-book bug and how the reduced design avoids it at the source

Per `w7_microstructure/REPORT.md` (crossed/dropped-tick table, all measured on BTCUSDT):

| venue | mechanism | crossed/dropped rate |
|---|---|---:|
| binance | dedicated BBO stream (`bookTicker`) | **0.051%** |
| okx | dedicated BBO stream (`bbo-tbt`) | **0.374%** |
| hyperliquid | dedicated BBO stream (`bbo`) | **0.100%** |
| bybit | deep-book-derived (no dedicated stream) | **2.89%** |

The bug the earlier worker (W7) had to fix in analysis code was mixing the fast dedicated
BBO stream with the slow full-depth diff stream for the *same* price series — the report's
fix was: "price/qty at best bid/ask come **exclusively** from each venue's dedicated
top-of-book stream; the full-depth diff stream is used only for depth-beyond-best-price
features and never touches the canonical price series." **A reduced collector that only
ever subscribes to the dedicated BBO stream in the first place doesn't need this fix at
all — there is no second, slower stream to disagree with it.** This is a strictly better
place to enforce it (source vs. downstream analysis code) and removes a whole class of
future replay bugs, at zero marginal collection cost since the BBO stream was always the
cheap part.

Bybit structurally cannot get this benefit — it has no dedicated top-of-book stream at the
wire level, so its price series is unavoidably the deep book's own best level (with the
crossed-tick drop-and-count discipline W7 already validated). This is a venue limitation,
not a design gap.

---

## 2. Reduced-scope design

### 2.1 Venues (3, ranked by the report's own data-quality evidence)

| priority | venue | why |
|---|---|---|
| 1 | **binance** | dedicated BBO stream, 0.051% crossed rate (best of all 4), largest absolute liquidity/reference venue |
| 2 | **okx** | dedicated BBO stream, 0.374% crossed rate, second CEX confirmation for every W7 mechanism |
| 3 | **hyperliquid** | dedicated BBO stream, 0.100% crossed rate, only DEX in the set, cheapest venue by far (48.75% of its book_events is already BBO) |
| excluded (v1) | **bybit** | no dedicated top-of-book stream exists on this venue at all — its price series structurally requires ingesting the deep diff stream (`orderbook.50`), which reintroduces most of the volume this redesign exists to avoid, *and* still carries the highest crossed-tick rate (2.89%) even after reconstruction. Cutting bybit removes a data-quality problem and a cost problem simultaneously. Can be revisited later as an explicit, separately-budgeted add-on if bybit-specific cross-venue mechanisms (e.g. A1 lead-lag, A6 directional) turn out to need it — none of the three headline #1-3 mechanisms (DEPTH_IMBALANCE_L1/MICROPRICE_OFFSET/OFI_TOB) require bybit; bybit was the outlier/negative-baseline venue on all three anyway. |

### 2.2 Symbols

**BTCUSDT, ETHUSDT, SOLUSDT** — unchanged from the prior sweep and from what
`CROSS_SECTIONAL_MOMENTUM_*` and the rest of the live book are already researching. No
universe expansion.

### 2.3 Streams captured (per venue) — BBO + trades only, no full depth

| venue | subscribe | drop (vs. full-scope collector) |
|---|---|---|
| binance | `{sym}@bookTicker`, `{sym}@aggTrade` | `{sym}@depth@100ms` (full diff — the cost driver), `markPrice@1s`, `forceOrder` |
| okx | `bbo-tbt`, `trades` | `books` (full diff), `open-interest`, `funding-rate`, `mark-price`, `index-tickers`, `liquidation-orders` |
| hyperliquid | `bbo`, `trades` | `l2Book` (full snapshot pushes), `activeAssetCtx` |

This is exactly enough for the five target features:
- **spread, microprice** — directly from bid/ask price+qty on the BBO stream.
- **best-depth imbalance** — bid qty vs. ask qty at best price, both already on the BBO
  stream (Binance `bookTicker` carries `b`/`B`/`a`/`A`; OKX `bbo-tbt` and HL `bbo` carry the
  same at their one level). No full depth needed.
- **OFI (top-of-book)** — computable from consecutive BBO ticks' price/qty deltas at the
  best level; this is the textbook top-of-book OFI construction and does not require levels
  beyond the best price.
- **aggressive trade flow** — from the trades stream (`aggTrade`/`trades`/`trades`),
  signed by aggressor side, unchanged from the full-scope design.

Derivative context fields (funding, OI, mark, index, liquidation) and full depth-beyond-best
(needed for A3 churn/depletion, A4 refill-asymmetry, liquidity-vacuum-style features) are
explicitly **out of scope for this reduced design** — those mechanisms are lower-ranked in
the W7 table (sub-cost, WEAK) versus the three that are actually close to a maker leg
(#1-3, all BBO-only). If a future iteration wants A3/A4/vacuum revalidated at lower cost,
that is a separate, explicitly-budgeted extension, not something to fold in silently here.

### 2.4 Format: batched, compact, not one-file-per-message

The current writer already batches reasonably at the I/O layer (buffered append,
`fdatasync` every 512 rows/1s, not per-message) — that part does not need to change. What
should change for a reduced collector:

- **Keep JSONL-per-record as the wire-to-disk contract during v1** (minimizes new code —
  the existing `BookEvent`/`TradeEvent` schema, `canonical_partition` layout, and
  `AppendOnlyEventWriter` can be reused essentially unmodified, just pointed at a narrower
  `subscriptions()` call), but **write through gzip rotation** (hourly or daily rotation,
  `gzip.open(..., "at")` or an external `gzip -1`-piped tail) instead of raw text.
- **Target/stretch: batched Parquet** (buffer rows in memory, flush a row-group every N
  seconds or M rows, one file per venue/symbol/stream/hour) — better compression via
  columnar dictionary/delta encoding (repeated `venue`/`symbol`/`source_stream` strings,
  monotonic `sequence_id`/timestamps) and directly queryable by `duckdb`/`polars` without a
  decompress+JSON-parse pass, which is how the W7 worker already consumed this data. This is
  more implementation effort than gzip rotation and is not required to hit a sane disk
  budget (see §3) — recommend gzip-JSONL for v1, Parquet as a nice-to-have if/when someone
  actually implements this.
- **No time-based downsampling recommended.** BBO ticks are already the cheap stream (8.7-
  48.75% of a venue's book_events bytes); OFI is defined as a sum of flow *between*
  consecutive updates, so dropping ticks changes what OFI measures, not just its resolution
  — a real fidelity cost, not a free lever. Since dropping the full-depth stream alone
  already gets to a comfortable budget (§3), there is no need to also downsample the cheap
  stream and take on that construction risk. One genuinely free lever, not exercised by the
  current writer: **on-change dedup** (skip writing a BBO row if price and qty are byte-
  identical to the previous tick — venues occasionally resend unchanged state) is lossless
  and worth adding if profiling shows it matters, but wasn't required to reach the estimate
  below.
- **Drop `raw_wire` duplication for v1.** The current design writes both the normalized,
  per-level-exploded `book_events` *and* a raw un-parsed wire copy (`raw_wire/`, 13.7GB
  extra on top of the 66GB headline in the full-scope run). Once the subscription list is
  narrowed to BBO+trades only, `raw_wire` would shrink proportionally too (it scales with
  message count, not exploded rows) — but it's still a second full copy of the same data for
  a replay safety net this reduced, lower-stakes research feed doesn't obviously need.
  Recommend skipping it for v1; revisit if replay-from-raw-wire proves valuable.

---

## 3. Bottom-up GB/day estimate

### 3.1 Baseline (uncompressed JSONL, current schema, reduced subscriptions)

Working from the actual dataset: 66GB was produced over **~28 hours of actual collector
runtime** (the mission's own figure, consistent with `du` = 66G measured on `raw/` and with
the report's 56GB/day framing: 66GB / (28h/24h) = 56.6 GB/day).

Applying the measured BBO-share-of-book_events ratios (§1.3) to each venue's **total**
book_events bytes (all 3 symbols already included in those totals), for the 3 kept venues
only (bybit's 6.6GB dropped entirely):

| venue | book_events total (3 symbols) | BBO share (measured on BTCUSDT) | BBO-only estimate |
|---|---:|---:|---:|
| binance | 41 GB | 16.2% | 6.65 GB |
| okx | 17 GB | 8.7% | 1.48 GB |
| hyperliquid | 1.1 GB | 48.75% | 0.54 GB |
| **subtotal, book events** | | | **8.67 GB** |

Plus trades for the same 3 venues (kept as-is, not reducible — trades aren't exploded
per-level): 241MB (binance) + 230MB (okx) + 115MB (hyperliquid) = **0.586 GB**.

**Reduced-scope total: 8.67 + 0.586 ≈ 9.25 GB, over the same 28h / 1.1667-day window that
produced the original 66GB.**

**→ 9.25 GB / 1.1667 days ≈ 7.9 GB/day, uncompressed JSONL, exact current schema.**

That is already a **~7.1x reduction** from the current 56.6 GB/day, purely from dropping the
full-depth diff channel and bybit — before any format change.

*Caveat on this number*: the BBO-share ratios were measured on BTCUSDT only and applied to
each venue's all-symbol total, assuming ETH/SOL have a broadly similar depth-message-rate-
to-BBO-message-rate ratio as BTC on the same venue. This is a reasonable approximation (same
matching engine, same diff protocol, same subscription cadence) but not verified per-symbol
— flagged as the main source of estimation error in this document, likely ±20-30%, not an
order of magnitude.

### 3.2 With batched compression (recommended)

Measured directly (not assumed) by extracting a 200k-line sample of binance BBO-only rows
and a 200k-line sample of binance trades rows from the real dataset and compressing them:

| sample | raw bytes | gzip -6 | gzip -9 | zstd (default) | zstd -19 |
|---|---:|---:|---:|---:|---:|
| BBO rows (bookTicker) | 66,267,168 | 2,500,749 (**26.5x**) | 2,265,981 (**29.2x**) | 2,673,173 (**24.8x**) | 1,558,405 (**42.5x**) |
| trade rows (aggTrade) | 8,671,478 | — | 497,337 (**17.4x**) | — | — |

Even the cheapest, fastest option (default `gzip -6` or `zstd` default level, no CPU-heavy
setting) gets **~25-27x** compression on this schema — expected, given the repeated field
names, constant `venue`/`symbol`/`source_stream`/`_record_type` values, and
`sort_keys=True` JSON formatting the current writer already uses. Applying a **conservative
planning multiplier of 12x** (well below every measured figure, to leave margin for
less-repetitive full-day/full-week data, non-BTC symbols, and realistic streaming-mode
compression):

**7.9 GB/day / 12 ≈ 0.66 GB/day, compressed.**

This is the number the budget in §4 is built on. If actual compression tracks closer to the
measured 17-27x range, real usage would be roughly 0.3-0.45 GB/day — treat 0.66 GB/day as
the number to plan against, not the optimistic case.

---

## 4. Disk budget recommendation

**Current free space: 32GB on a 915GB volume already at 97% utilization.** This is tight in
absolute terms and the brief notes it is dropping. Recommendation: **do not allocate more
than half of current free space to this collector's forward-test window, and target a
window of 2-4 weeks before revisiting.**

| budget | days of runway at 0.66 GB/day (compressed) | days of runway at 7.9 GB/day (uncompressed, do-not-use) |
|---|---:|---:|
| 16 GB (half of the 32GB free) | **~24 days** | ~2 days |
| 8 GB (quarter of free, more conservative) | **~12 days** | ~1 day |
| 32 GB (all free space — not recommended) | ~48 days | ~4 days |

**Recommendation: budget 16GB (half of current free disk), target rate ≤0.75 GB/day
compressed (rounding up from the 0.66 GB/day estimate for margin) → ~21-24 days of runway**,
comfortably inside a 2-4 week forward-test window while leaving the other 16GB of current
free space untouched for the rest of the system (which is already at 97% utilization and
reportedly dropping). Uncompressed JSONL (7.9 GB/day) is **not viable** at this disk budget
— compression (even the cheap default-level option) is a hard requirement for this design,
not a nice-to-have, given ~2 days of runway otherwise.

If free disk drops further before this is approved, this budget should be re-derived from
the disk state at approval time, not assumed to still hold from 2026-08-31.

---

## 5. Draft registry entry (proposal only — NOT applied to `configs/live_alpha_registry.yaml`)

The registry already has a `MICROSTRUCTURE_OFI_CLUSTER_V1` entry
(`operational_status: DATA_BLOCKED`, `universe: [BTCUSDT, ETHUSDT]`, no SOL). If this
collector is approved and actually launched, the entry would change roughly as follows
(shown here as a diff-style sketch, not written to the real file):

```yaml
# PROPOSED, NOT APPLIED — for reference only if/when a human approves the disk budget
# and the collector is actually started.

  - alpha_id: MICROSTRUCTURE_OFI_CLUSTER_V1
    family: microstructure
    mechanism: "Depth-imbalance / microprice-offset / OFI, cluster de 3 features apparentees."
    version: v1.0
    status: SIGNAL_SHADOW    # was DATA_BLOCKED
    scientific_status: DISCOVERY    # unchanged -- collecting data resolves DATA_BLOCKED,
                                     # it does NOT itself confirm the mechanism
    operational_status: CODE_MISSING    # was DATA_BLOCKED -- next blocker is that
                                         # signal_definition is still NOT_YET_IMPLEMENTED,
                                         # NOT SIGNAL_SHADOW yet (per this registry's own
                                         # CODE_MISSING | DATA_BLOCKED | SIGNAL_SHADOW |
                                         # EXECUTION_SHADOW | READY progression -- data
                                         # unblocks the DATA_BLOCKED stage, it doesn't skip
                                         # the CODE_MISSING stage)
    source_report: reports/edge_discovery/alpha_hunt_2026-08-30/w7_microstructure/REPORT.md
    signal_definition: NOT_YET_IMPLEMENTED    # unchanged until the feature-computation
                                               # code is actually written against the new feed
    universe: [BTCUSDT, ETHUSDT, SOLUSDT]    # SOL added -- matches the reduced collector's
                                              # symbol scope (was BTC/ETH only)
    features: [depth_imbalance_l1, microprice_offset, ofi_tob]
    model_family: logistic
    model_hash: null
    training_window: null    # reset -- new live collector, no historical window to point to
                              # until enough days accumulate
    freeze_timestamp: null
    entry: TBD
    exit: 5s
    horizon: 5s
    execution_style: MAKER
    cost_model: "maker ~1.5bps round-trip"
    risk_bucket: MICROSTRUCTURE_FAMILY
    expected_capacity: null
    correlation_family: MICROSTRUCTURE_TOB
    live_start_timestamp: null    # set at actual collector launch time, not before
    expected_net_bps: null
    expected_gross_bps: 1.63    # unchanged, from source_report -- historical context,
                                 # not a forward target
    data_live: true    # was false -- collector process confirmed running via systemd,
                        # NOT a claim that the signal itself is validated
    notes: >
      Collector relaunched 2026-XX-XX (reduced scope: binance/okx/hyperliquid BBO+trades
      only, no full order-book depth, no bybit -- see
      reports/live_alpha_lab/MICROSTRUCTURE_REDUCED_COLLECTOR_DESIGN.md for the design and
      disk budget this was approved against). data_live=true reflects the collector running,
      NOT that DEPTH_IMBALANCE_L1/MICROPRICE_OFFSET/OFI_TOB are confirmed -- W7's own
      verdict is NEEDS_FULL_VALIDATION (adverse-selection/quote-and-fill simulation not yet
      done), and operational_status stays CODE_MISSING until the feature-computation +
      freeze_spec work is actually done against this feed.
```

Notably: `universe` gains `SOLUSDT` (the source report scoped order-book mechanisms to
BTCUSDT only for depth reasons, but the reduced collector captures all 3 symbols on the BBO
stream at negligible marginal cost — see §2.2/§3 — so ETH/SOL data would exist even before
anyone extends the O(1)-per-venue W7 analysis to those symbols).

---

## 6. Summary

| | full-scope (existing, futur-data-v2) | reduced-scope (this design) |
|---|---:|---:|
| venues | binance, okx, bybit, hyperliquid | binance, okx, hyperliquid |
| symbols | BTC, ETH, SOL | BTC, ETH, SOL (unchanged) |
| streams | full depth diff + BBO + trades + derivatives + raw_wire duplicate | BBO + trades only |
| format | raw JSONL, no compression | JSONL + gzip rotation (parquet as stretch goal) |
| rate | ~56.6 GB/day (measured: 66GB / 28h) | **~7.9 GB/day uncompressed → ~0.66 GB/day compressed (measured 12x-conservative)** |
| crossed-book bug | present downstream, fixed in analysis code only | avoided at the source (BBO-only ingestion never touches the deep stream) |

**Recommended budget: 16GB (half of the 32GB currently free), target ≤0.75 GB/day
compressed, ≈21-24 days of runway** — well inside a 2-4 week forward-test window, leaving
the other half of current free disk untouched.

---

**NOT LAUNCHED — awaiting explicit human go-ahead on the disk budget before starting any
new persistent collector process.** No systemd unit was created or modified, no code was
run, no data was copied/moved/deleted, and `configs/live_alpha_registry.yaml` was not
touched — the entry in §5 is a draft inside this document only.
