# VISION FREE BACKFILL — plan and audit (P6, 2026-09-11)

Goal: fill the 174 H2 launch windows with everything Binance Vision gives for free, so the coverage matrix stops
saying "unusable" for reasons that cost nothing. No return computed, no price column read, no signal, no verdict,
no budget consumed, `capital_deployable` untouched.

## Window and files

- Window per event: `t0 − 30 min → t0 + 6 h`, `t0` = first Vision 1-min bar of the perpetual (P5 universe; 170/174
  cross-checked against `onboardDate` within 5 min). Days and months are computed **in UTC**, matching how Vision cuts files.
- 171 windows fit in one UTC day, 3 span two. `fundingRate` is monthly.

| dataset | priority | role | files needed | exist on Vision (HEAD probe) |
|---|---|---|---|---|
| markPriceKlines 1m | P0 | first mark, mark path | 177 | 177 |
| indexPriceKlines 1m | P0 | first index, external reference | 177 | 147 |
| premiumIndexKlines 1m | P0 | premium = mark − index | 177 | 170 |
| aggTrades | P0 | first trade, tick trades, entry / exit prints | 177 | 177 (1.41 GB) |
| metrics 5-min | P1 | open interest | 177 | 177 |
| fundingRate (monthly) | P1 | funding at first settlements | 174 | 171 |
| bookDepth 1-min | P1 | depth at % levels: capacity, slippage | 177 | 168 |
| klines 1m | P2 | fallback / outcome ruler only | 177 | 177 |
| trades, bookTicker | P2 | optional; bookTicker absent for new symbols | — | not downloaded |

Probe: 1 413 files, 1 364 exist, 49 absent, 1.47 GB total. Cached in `data/vision_backfill/probe_cache.json`.

**Core coverage** = `markPriceKlines` + `aggTrades` + (`indexPriceKlines` **or** `premiumIndexKlines`), every day of the
window. The index is recoverable from mark and premium by the contract identity `index = mark − premium`; that identity is
documented here, not computed by this phase. 29 events have premium but no index file, which is why the two datasets are
one requirement and not two.

## Modules

- `vision_paths.py` — pure: UTC window, days / months spanned, URL, `.CHECKSUM` URL, local path, files per event.
- `vision_manifest.py` — one manifest per window: status per file, bytes, sha256, `checksum_verified`, rows, first / last
  timestamp read **from the time column only**; atomic writes; a manifest whose content changes is archived beside it under a
  name that is never reused, and the rewrite is logged; an unreadable manifest is archived byte-for-byte, never discarded.
- `vision_free_backfill.py` — `--plan` (no network), `--probe` (HEAD + sizes), `--run` (paced, priority order, disk cap,
  quarantine on checksum mismatch), `--coverage` (rescores the P5 matrix from what is on disk).

## Hardening after an adversarial review of this code (3 lenses, 32 findings)

| defect found | consequence | fix |
|---|---|---|
| naive ISO timestamps parsed as **local** time | every `bookDepth` / `metrics` first / last timestamp was 2 h off on this host (345 files) | naive is UTC, verified under three host timezones |
| `http.client.IncompleteRead` is not an `OSError` | a truncated transfer on a large `aggTrades` zip aborted the whole 174-window pass | every exception is caught in the download and HEAD loops |
| `.CHECKSUM` fetched once, any failure silently left the file "verified: unknown" while the report claimed verification | unverified files counted as covered | 3 attempts; verified / unverified counted per dataset and reported as a ratio |
| a checksum mismatch left the bad file on disk, and `if not exists` then blocked any re-download | a single corrupt transfer became a permanent hole | the file is quarantined as `.bad`, so the next pass re-downloads |
| an unreadable archive still counted as `ok` | a corrupt zip counted as coverage | an inspection error sets the status to `error` |
| a 404 on a period Vision has not published yet was cached forever | monthly `fundingRate` of the current month (3 events) would never arrive | status `not_yet_published`, re-probed on every run, not counted as a hole |
| disk cap read and written from 8 threads without a lock | the 20 GB guard could be exceeded by workers × file size | reservation under a lock before each download, corrected to the real size after |
| `--dry-run` overwrote the manifest of a real pass | coverage then reported nothing on disk | a dry run writes no manifest |
| `bytes_total` counted cap-skipped files at their probed size | "GB on disk" overstated | `bytes_on_disk` counts only files actually present |
| `TS_COL['bookTicker']` pointed at a quantity column | a non-timestamp column was read, against this phase's rule | column 5 (`transaction_time`) |
| `manifest_sha256` included `run_id` | every re-run archived all 174 manifests, burying real rewrites | the hash covers content only |
| `--limit` mixed two populations in one report | header and answers described different sets | rows are built from the events passed in |
| `csv.Error` on a NUL byte (Python 3.8 only) escaped `inspect_zip` | would abort a pass on the 3.8 interpreter | caught |
| coverage matrix credited `capacity_present` because the zips existed | 3 unearned points per event; capacity is a derived quantity and P6 computes nothing | no longer credited |
| non-atomic manifest and probe-cache writes | a kill mid-write dropped an event from every report | atomic temp + rename |
| a test wrote into the real append-only journal and the real store | two synthetic records in the ledger; fake files in `data/vision_backfill` | roots resolved at call time; the journal keeps the records and carries a `correction` entry naming them |

Two operator incidents are recorded in the same journal as `correction` entries: the synthetic test records above, and a
`find` pattern of mine (`*AUSDT*`) that deleted 121 archives of real symbols whose names end in `AUSDT`; the next pass
re-downloaded them.

## Storage

`data/vision_backfill/um/<dataset>/<symbol>/<file>.zip` (immutable archives, gitignored), `manifests/<event_id>.json`,
`backfill_log.jsonl` (append-only), `probe_cache.json`. Versioned outputs: the coverage reports and the after-Vision
matrix under `reports/data_acquisition/`.

## What this does not do

It does not open a price. Only timestamp columns are read (`vision_manifest.TS_COL`), to prove a file covers the window.
The after-Vision matrix credits a field only when every day of the window is on disk with a recomputed sha256.
