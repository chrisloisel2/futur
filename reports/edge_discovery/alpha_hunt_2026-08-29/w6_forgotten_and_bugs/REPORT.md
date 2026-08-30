# W6 — Forgotten data inventory & bug/leakage audit

Read-only investigation. No code or production/live data was modified.

## Executive summary

**Job 1**: Two datasets are genuine dead ends worth closing. `data/session_20260523_*` is **not trading data at all** — it's ~400MB of robotics teleoperation footage (stereo cameras + force sensors, tasks like "put the pens in the box") from an unrelated physical-AI project, with zero code referencing it anywhere in the repo. `data/news_raw` is real crypto news+sentiment but too sparse (54 usable days, 65% is `coingecko_trending` wire chatter not real news) — a fast correlation check against forward BTC returns gave r≈0.15-0.18 on n≈53, statistically indistinguishable from noise, confirming the brief's prior. Two other datasets turned out to be **already fully exploited, not forgotten**: `data/positioning` is consumed by several live feature-engineering modules, and `data/listings_backfill` already has a completed, pre-registered 518-listing event study whose conclusion (LONG post-listing is NO_EDGE everywhere, real fade-signature but SHORT_REJECTED by standing project rule) is already wired into production as `ListingAgeGate` (currently OFF by default). The A15/stablecoin mechanism has **also already been tested end-to-end** with a frozen protocol — verdict `NO_EDGE` for the overlay (only a side-finding survived Bonferroni: depeg predicts forward BTC *volatility*, not returns) — so the newer PIT `stablecoin_daily.parquet` in data-v2 would just re-ask an already-answered question; recommend closing A15 as **NO_EDGE (tested)**, not BLOCKED_DATA. `_corrupt_quarantine` + the `.lock` files are a working self-healing mechanism (atomic writer quarantines corrupt targets instead of crashing), not an active ongoing incident, though `DOTUSDT`'s dependence on a quarantined file is explicitly flagged in `build_data_registry.py`.

**Job 2**: P0.1 (Phase 5.2 fee-doubling bug) and P0.2 (feature-set leakage into A16) are confirmed **fixed and committed** (`b6e7920`, `2042d45`) on `merge/main-rebuild-foundation` (checked out at `/home/qbee/futur-merge-main`) — verified directly in code, not just taken on the commit message's word. P0.3-P0.6 remain **not started**, matching project memory. There is **no standalone repo document** for the 28-section audit that produced P0.1-P0.6 — it exists only inside the agent's session memory (`project_new_edges_phase.md`), never committed to `docs/` or `reports/`, which is itself a finding worth flagging (if that memory were ever lost, this backlog vanishes with no trace in git). A limited spot-check of the bug checklist against `alpha_foundry_v5`/`market_physics_v3` found no new bugs in the areas checked, but several checklist items were not reached given the time budget — explicit list at the end of Job 2.

---

## Job 1 — Forgotten / underexploited data inventory

### 1. `data/session_20260523_*` — robotics teleoperation footage, unrelated to trading

- **Path/size**: 27 directories (not ~40 as hypothesized), 8-25MB each, **399MB total**, all within a 5.5-minute wall-clock window on 2026-05-23.
- **Content**: `mission.json` (task text, e.g. `"Put the 6 pen in the box"` / `"Met les stylos dans le pot"`), `config.json` (3-camera rig: head/left/right, 1920x1200@120fps→30fps h264/VAAPI), `analysis.json` (frame-drop/clock-drift QA), `result.json`, `postprocess.log`, `cameras/` (`.mp4` + timestamp `.jsonl` per camera), `sensors/` (two 60Hz `.jsonl` streams, likely gripper/force). Log paths reference `/home/physicaldata/Desktop/OperatorV2/data/...` — a teleoperated-robot-arm data-capture stack, nothing financial.
- **Code consumption**: none — confirmed zero repo-wide references to `session_20260523`, `OperatorV2`, or `physicaldata`.
- **Redundancy with market_physics_v3**: not applicable — there's no market data here to compare, so no overlap question even arises.
- **Verdict**: no economic mechanism, full stop. Looks like data accidentally synced into the wrong `data/` folder from an unrelated project on the same machine. Not touched or moved, per the no-delete rule — just flagged.

### 2. `data/news_raw/date=*` — real feed, confirmed sparse via fast-triage

- **Schema**: `ts, source, title, url, symbols, sentiment, url_hash` — one row/article (e.g. `2025-12-29 16:10 UTC | decrypt | "...Saylor buys $109M BTC!" | symbols=BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT | sentiment=-0.6155`).
- **Coverage** (1,831 files, 15MB, 5,354 rows): 15 isolated dates scattered Dec-2025/Jan-2026 (1-2 rows/day — an earlier abandoned attempt, as suspected), then a continuous, still-running collection 2026-07-07 → 2026-08-29 ramping to ~10-30 files/day.
- **Sources**: `coingecko_trending` 65% (trending-coin chatter, not editorial news), `cointelegraph` 921, `decrypt` 569, `bitcoinmagazine` 236, `newsbtc` 173. Precomputed sentiment in [-1,1], mean +0.20.
- **Code consumption**: zero anywhere in the repo — genuinely forgotten.
- **Fast-triage performed**: daily mean sentiment (all articles and BTC-tagged only) vs next-day BTC forward return over the continuous July-August window (n=53-54): **corr(sentiment_all, fwd_ret_1d)≈0.154; corr(sentiment_BTC, fwd_ret_1d)≈0.178; corr(news_count, fwd_ret_1d)≈0.099**. With n≈53, need |r|≳0.27 for p<0.05 — this is noise.
- **Verdict**: confirms the brief's prior. Too sparse (53 usable days) and too source-concentrated to support a "news shock → price reaction" protocol as-is. Not worth pre-registering a test on this dataset today.

### 3. `data/positioning` — documented, but already consumed by production code, NOT forgotten

- **Content**: 4 files/symbol × 47 symbols (`{SYM}_global_account.parquet`, `_top_account.parquet`, `_top_position.parquet`, `_taker_vol.parquet`), 5-minute cadence, columns `timestamp, symbol, period, longAccount, shortAccount, longShortRatio` (or `buySellRatio, buyVol, sellVol`). 2026-07-16 → present, 44MB total.
- **Symbols**: 47 majors (BTC, ETH, + alts: AAVE, ADA, ALGO, APT, ARB, AR, ATOM, AVAX, BCH, BNB, DOGE, DOT, ENA, ETC, FET, FIL, GRT, HBAR, ICP, IMX, INJ, JUP, LDO, LINK, LTC, MANA, NEAR, OP, ORDI, PENDLE, PYTH, RUNE, SAND, SEI, SOL, STX, SUI, TAO, TIA, TRX, UNI, VET, WLD, WIF, XRP).
- **PIT**: standard Binance Futures Data API export, no restatement mechanism known, but no explicit PIT-flag column (unlike `stablecoin_daily.parquet`'s `research_available_at`) — worth confirming before load-bearing use.
- **Consumption**: **already active** — `research/edge_factory/ctrend/ctrend_v0.py`, `ai/level_0/feature_engineering.py`, `ai/level_0/features.py`, `data_pipeline/derivatives_positioning.py`, `scripts/archive_binance_positioning.py`, `scripts/backtest_ctrend_v0.py`, `tests/test_positioning_archiver.py`, `tests/test_derivatives_positioning.py`. Not "forgotten" — just documented per the brief's request.

### 4. `data/listings_backfill/binance` — post-listing mechanism already tested end-to-end, deployed as a gate

- **Content**: `listings_calendar.parquet` (683 rows, exact `onboardDate` — PIT by construction), `klines_5m/`+`klines_1h/`+`funding/` (518 symbols each), `event_study_features.parquet` (518 rows), `event_study_returns.parquet` (29,002 rows: full delay×horizon grid). 70MB total.
- **Not a fast-triage candidate — a closed investigation.** `scripts/test_perp_listing_event_study.py` ran the full anti-lookahead event study (518 listings, 2023-01-17→2026-07-03, entry delays 30min-7d × horizons 1h-14d, 40bps round-trip costs) → `reports/LISTING_EVENT_STUDY.md`. **LONG is NO_EDGE everywhere** — net median negative in every delay×horizon cell and all 4 annual cohorts (e.g. delay=1h/horizon=168h: -1,369.5bps net median). The fade/short mirror is real and consistent (+400 to +1,700bps across cohorts) but **not tradable**: standing project rule `SHORT_REJECTED`.
- `scripts/test_listing_age_22_30.py` targeted J+22→J+30 specifically (to avoid extrapolating past J+21): net median -285bps, negative across cohorts → confirms the 30-day cutoff end-to-end.
- **Already deployed**: `src/institutional/portfolio/listing_age_gate.py` (`ListingAgeGate`, blocks LONG on perps < `min_age_days=30`, conservative "unknown=blocked" default), wired into `multileg_backtester` per memory (commit `731ba53`) but **defaults OFF** — activating it in a real frontier config remains an open operational item, separate from this data-inventory question.
- **Verdict**: mechanism already fast-triaged and then some; nothing left to discover on the data side.

### 5. `market_physics_v3/context/stablecoin_daily.parquet` (data-v2) — A15 already tested, verdict NO_EDGE (not merely thin/untested)

- **Schema confirmed**: 3,154 rows, 2017-11-29→2026-07-18, columns `date, all_usd, usdt, usdc, dai, trio, p_usdt, p_usdc, p_dai, research_available_at, {5 series}_chg_{1d,7d,30d}, stablecoin_max_abs_depeg, stablecoin_mean_depeg, source_quality`. `source_quality="PIT_AGGREGATED_T1"` with an explicit T+1-lagged `research_available_at` — genuinely PIT-honest.
- **Same signal family as `data/stablecoins/{supply_daily,prices_daily}.parquet`**, already run through a **pre-registered protocol** (`reports/STABLECOIN_REGIME_PROTOCOL.md`, single execution 2026-07-18, `scripts/test_stablecoin_regime_signal.py`). Verdict (`reports/STABLECOIN_REGIME_VERDICT.md`): **NO_EDGE** — the frozen risk-off overlay rule (gross ×0.5 when 30d-supply z<-1 OR depeg<-50bps 3d) *degrades* the existing 3-leg book (maxDD -3.10%→-3.43%, Sharpe 3.62→3.59, only 2/7 episodes improved). 4/7 frozen criteria FAIL.
- One statistically real survivor (Bonferroni across 32 tests, p=5e-4, same sign train/test): **F6 (7d min depeg USDT/USDC/DAI) → 7d forward BTC realized vol**, IC≈-0.30 — predicts *volatility*, not returns; usable only as a future risk-sizing feature, not a standalone overlay. Memory explicitly says: don't re-tune this rule.
- **Recommendation**: close A15 in the catalog as **NO_EDGE (tested)**, not BLOCKED_DATA/WEAK — the newer data-v2 series is essentially the same underlying DefiLlama trio/peg data with proper PIT tagging and a longer history, and re-triaging it would just re-answer an already-closed question.

### 6. `_corrupt_quarantine` and `.lock` files — working defensive mechanism, not an active incident

- **Quarantine**: 4 files (AVAXUSDT 1.08GB/jun-14, BNBUSDT 1.16GB/jun-17, DOTUSDT 208MB/jun-13, LINKUSDT 539MB/jun-12). All 4 symbols now have healthy, larger, current live files (Aug-29 mtimes) — historical casualties from a mid-June incident window, already superseded.
- **Mechanism**: `src/institutional/data/atomic_parquet.py:108-113` — the atomic writer, on detecting a corrupt existing target before overwrite, renames it to `_corrupt_quarantine/{name}.CORRUPT.parquet` via `os.replace` instead of clobbering or crashing. Working as designed.
- **One documented residual issue**: `scripts/build_data_registry.py:34` — `DROPPED = {"DOTUSDT": "no raw source in data_out/result (quarantined corrupt enriched only)"}` — confirms DOTUSDT had a window where its only artifact was the corrupt one; registry-builder explicitly excludes it rather than silently accepting bad data.
- **`.lock` files**: 10 zero-byte files, all dated 2026-06-28/29 (contemporaneous with the quarantine cleanup) — advisory locks from the atomic-write path, now stale but harmless. Not touched.

---

## Job 2 — Bug/leakage audit

### P0.1-P0.6 status

**No standalone repo document exists** for the "28-section audit" — confirmed via `find`/`grep` across `docs/`, `reports/`, and the whole tree for `P0.1`-`P0.6` and "28 sections"/"audit technique": zero hits in git-tracked files. The only record lives in the agent's own session memory `project_new_edges_phase.md` (lines 287-338), which is **not part of the repo** (`~/.claude/projects/.../memory/`). Flagging this: if that memory file is ever lost, this backlog disappears with no trace in git — worth committing a short doc if the team wants it durable.

Taking that memory record as authoritative (detailed, dated, cross-references verifiable commits):

- **P0.1 — Phase 5.2 fee-doubling bug. FIXED, verified.** `Trade.net_bps` computed `gross_bps - fee_bps` (one fee) when `gross_bps` already reflects a full round trip. Fixed in `b6e7920`; confirmed in `market_physics_v3/phase5_2_execution_economics.py` on `merge/main-rebuild-foundation` (`/home/qbee/futur-merge-main` — this branch is absent from the current `feat/free-derivatives-backfill` worktree, which has no `alpha_foundry_v5`/`market_physics_v3` at all). Verdict unchanged (still dead) but reported cost moved from -4.44bps to honest -9.27bps. Downstream code (`a2rv_execution.py:91-92`) written after the fix is already correct (`gross_bps - 2.0 * one_way_fee_bps`).
- **P0.2 — feature-set leakage into A16. FIXED, verified.** Two bugs: (1) `resolve_feature_columns` only had rules for 4/10 plugin families, leaving A9-A16 with empty feature sets; (2) even a frozen feature set was never enforced — `materialize_features`/`_prepare_xyt` passed the *whole* frame to the plugin, which re-matched its own tokens live, so A16's target column (`exec__post_fill_markout_bps`) would leak straight into X once A16 became runnable. Fixed in `2042d45`; verified directly — `alpha_foundry_v5/labs/registry.py:39-58` now restricts `model_frame` to `asof_ns+symbol+feature_columns` before `plugin.build_features()`, while `readiness()` correctly still sees the full frame.
- **P0.3 — complete A9-A16 infra support. NOT STARTED.** Broader plumbing/testing gap beyond the P0.2 leakage fix — full end-to-end support for 8 lab families still incomplete.
- **P0.4 — ExecutableResearchEngine (signal→position→execution→net PnL). NOT STARTED.** Foundry currently optimizes IC, not realized net PnL through a full pipeline.
- **P0.5 — inner-CV score on net PnL, not IC. NOT STARTED.** Depends on P0.4.
- **P0.6 — DSR/PBO on real portfolio PnL, not `prediction×target`. NOT STARTED.** Same root cause as P0.4/P0.5 — all three are one "score net PnL instead of proxies" project.

None of P0.3-P0.6 were touched or run — documentation only, as instructed.

### Bug-checklist spot-check (partial — time-boxed, all in `/home/qbee/futur-merge-main` since that's the only place Foundry code exists)

**Checked, no new bugs found:**
- One-way vs round-trip fees: `phase5_2_execution_economics.py` and `a2rv_execution.py:91-92` both correctly charge `2×one_way_fee_bps`. Other `TAKER_FEE_BPS` sites (`scripts/execution_economics_market_physics_phase5_2.py`, `scripts/run_a13h_backtest.py`) not opened in detail.
- Feature-set leakage (P0.2 class): re-verified fix directly in `registry.py:39-58` rather than trusting the commit message.
- Global vs causal normalization: `alpha_foundry_v5/labs/plugins.py:149-150` uses `rolling(300, min_periods=50)` — causal, not global. Only this one file checked.
- Horizon in rows vs time: `alpha_foundry_v5/targets.py` uses `horizon_steps` (row counts). Not flagged as a bug since labs operate on frames pre-resampled to a fixed `cadence_ms` grid — but this assumption wasn't verified against every lab's actual input frame.
- Dynamic thresholds using future data: `grep` for `.quantile(`/`qcut(` across `alpha_foundry_v5/*.py` and `labs/*.py` — no hits.
- Survivorship in universe: `scripts/run_a13h_backtest.py` uses a **time-varying** `universe_size` per timestep (`MIN_UNIVERSE=30` floor), consistent with memory's claim that the raw-price-vs-filtered-panel bug was already fixed before the real backtest ran.
- Inverse/linear contract confusion: no `inverse`/`coin_m`/`contract_type` handling found anywhere in `alpha_foundry_v5`/`market_physics_v3` — suggests only linear USDT-margined contracts are handled at all (bug class doesn't currently apply, but also no established convention if inverse contracts are ever added).

**Explicitly not checked** (ran out of time budget):
- Wrong sign of target, per-lab (would need reading all ~16 lab targets against their economic hypothesis individually).
- Spot/perp confusion (distinct from inverse/linear — didn't trace venue price-series selection in the data-plane code).
- Turnover overestimation outside `run_a13h_backtest.py`.
- Timestamp/resampling bugs in `alpha_foundry_v5/data_planes/` — not opened.
- `strict_catalog.py`, `strict_options.py`, `catalog.py`, `base.py` in `labs/` — not read.
- Current `feat/free-derivatives-backfill` branch's own Foundry-adjacent commits (e.g. `912af37`/P0-D) — not applicable, that branch has no Foundry source tree.

## Key file references

`/home/qbee/futur/data/session_20260523_180002/*`, `/home/qbee/futur/data/news_raw/date=*/*.parquet`, `/home/qbee/futur/data/positioning/*.parquet`, `/home/qbee/futur/data/listings_backfill/binance/*`, `/home/qbee/futur/scripts/test_perp_listing_event_study.py`, `/home/qbee/futur/scripts/test_listing_age_22_30.py`, `/home/qbee/futur/scripts/test_stablecoin_regime_signal.py`, `/home/qbee/futur/reports/LISTING_EVENT_STUDY.md`, `/home/qbee/futur/reports/STABLECOIN_REGIME_VERDICT.md`, `/home/qbee/futur/src/institutional/portfolio/listing_age_gate.py`, `/home/qbee/futur/src/institutional/data/atomic_parquet.py:108-113`, `/home/qbee/futur/scripts/build_data_registry.py:34`, `/home/qbee/futur/data/enriched/_corrupt_quarantine/*`, `/home/qbee/futur-data-v2/data/market_physics_v3/context/stablecoin_daily.parquet`, `~/.claude/projects/-home-qbee-futur/memory/project_new_edges_phase.md:287-338`, `/home/qbee/futur-merge-main/market_physics_v3/{phase5_2_execution_economics,a2rv_execution}.py`, `/home/qbee/futur-merge-main/alpha_foundry_v5/labs/registry.py:39-58`, `/home/qbee/futur-merge-main/alpha_foundry_v5/{feature_sets,targets}.py`, `/home/qbee/futur-merge-main/alpha_foundry_v5/labs/plugins.py:149-150`, `/home/qbee/futur-merge-main/scripts/run_a13h_backtest.py`. Commits: `b6e7920` (P0.1), `2042d45` (P0.2).
