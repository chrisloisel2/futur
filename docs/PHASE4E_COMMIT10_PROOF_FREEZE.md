# Phase 4E — commit 10: freezing the Phase 4D proof before touching any code

Written and committed **before** commit 11 changes a single line of
`src/institutional/backtest/multileg_backtester.py`. Its only job is to
pin down, with real hashes and real command output, exactly what Phase 4D
commit 9 (`5be4edc`) actually produced -- so that commit 11's "before"
state is independently reproducible and commit 13's "after" replay can be
compared against a fixed, dated baseline instead of a moving target.

## 1. The final replay of `5be4edc` (current HEAD, kept)

Full 40-character git hash of the commit whose tree this freeze describes:

```
$ git rev-parse HEAD
5be4edc42d243bb764f75b8ff8c08f022d524fea
```

### 1.1 Provenance identifiers (docs/PHASE4D_FROZEN_WINDOW_DECISION.md's four, as actually recorded)

| identifier | value |
|---|---|
| shadow execution commit (git HEAD at the moment the replay script actually ran) | `12226250c191f12ce256531e864772e7bd904f87` (commit 8 -- the script ran, and this report/JSONL were generated, **before** commit 9 itself existed; commit 9 then committed the code fixes together with the report/JSONL they produced, which is why this field points at commit 9's own parent, not commit 9) |
| historical experiment commit (`RunnerSpec.git_commit` for `carry_basis_v12`) | `2fe693b` |
| registry config hash (`RunnerSpec.config_hash`) | `9e025f4590c1dd39aec94210` |
| effective serialized config SHA-256 (the real `MultiLegConfig` object actually constructed) | `9a54145d805e2e93bb15f35c7c948c12bef33d80bec5e8de369fa72251fe861d` |

### 1.2 Frozen window (docs/PHASE4D_FROZEN_WINDOW_DECISION.md, unchanged)

- `paper_start` = `2026-05-29`
- `end` = `2026-07-28T21:00:00Z`
- `runner_id` = `carry_basis_v12`, `venue` = `binance_usdm`
- SHA-256 of the frozen-window decision doc itself (proves this doc's rule
  text is the exact text commit 9 replayed against, not a later edit):
  ```
  $ shasum -a 256 docs/PHASE4D_FROZEN_WINDOW_DECISION.md
  df92a936bbcfed6ab07a40c29ef1b4be45a31c8acd25a3631872730793e7ac93  docs/PHASE4D_FROZEN_WINDOW_DECISION.md
  ```

### 1.3 Data manifest (`data/manifests/carry_shadow_data_manifest.json`, commit 2b9ebc1)

```
$ shasum -a 256 data/manifests/carry_shadow_data_manifest.json
bf2e38ff4887a889094ca9ca5606b316f84856843beb8b65bf9744500d31fa60  data/manifests/carry_shadow_data_manifest.json
```

Per-symbol enriched-file content hashes recorded inside that manifest
(these are what the replay actually read from disk, hashed at the moment
the manifest was generated -- 2026-07-28T21:44:09Z):

| symbol | sha256 of `data/enriched/{symbol}_1h_enriched.parquet` | rows | span |
|---|---|---|---|
| BTCUSDT | `3641169f4d13d3397222596d16605452cce60a8cdb5c90adc5c6b62d7a51cc1c` | 18190 | 2024-07-01T00:00:00Z → 2026-07-28T21:00:00Z |
| ETHUSDT | `6ee20463edafd6b046abe3a9087d27fd6604dd6ae0dcc7d22fdb45ad456cac9a` | 18190 | 2024-07-01T00:00:00Z → 2026-07-28T21:00:00Z |

### 1.4 Real ProductSpec registry (`data/venue_specs/binance_btc_eth_product_specs.json`, commit 6)

```
$ shasum -a 256 data/venue_specs/binance_btc_eth_product_specs.json
5afa8e7523ca5dfe9841580018b89dd07cd5551a8d132f15fece6355b9911e58  data/venue_specs/binance_btc_eth_product_specs.json
```

### 1.5 Configuration (`configs/alpha20_runners.yaml`, unchanged by this phase)

```
$ shasum -a 256 configs/alpha20_runners.yaml
7f01a5432ca4b6102eda358c581033411be9c2a278d1e825dc503a29dbf2b15c  configs/alpha20_runners.yaml
```

### 1.6 Differential JSONL and replay report (`data/manifests/carry_shadow_differential.jsonl`, `carry_shadow_replay_report.json`, both committed at `5be4edc`)

```
$ shasum -a 256 data/manifests/carry_shadow_differential.jsonl
fdaad8b3085310c69b2c5e06e96d529b37d218dd953b1b8bcef160c67c54edf7  data/manifests/carry_shadow_differential.jsonl

$ shasum -a 256 data/manifests/carry_shadow_replay_report.json
3135df7a9d70c03e6acf154ef6ee9bed93789f6b897f2fecfb6dfe0f93942c5c  data/manifests/carry_shadow_replay_report.json
```

Report content (492 comparisons total):

```json
{
  "coverage_counts": {
    "spot_leg_opened": 7, "perp_leg_opened": 7,
    "spot_mark": 80, "perp_mark": 80,
    "funding": 7, "fee": 14, "reduction_or_close": 14, "terminal_close": 4
  },
  "missing_coverage": [],
  "differential": {
    "n_rows": 492,
    "classification_counts": {
      "MATCH": 486,
      "UNEXPLAINED_DIVERGENCE": 5,
      "EXPECTED_LEGACY_DIVERGENCE": 1
    }
  },
  "legacy_identical_shadow_on_vs_off": true,
  "verdict": "FAILED_UNEXPLAINED_DIVERGENCE",
  "cause": "5 UNEXPLAINED_DIVERGENCE rows"
}
```

The 5 `UNEXPLAINED_DIVERGENCE` rows are all at the single terminal event
(`cycle-1-borrow-2026-07-28`): `cash`, `nav`, `fees`, `borrow`,
`gross_exposure`. This is the state commit 11 starts from, unmodified,
reproduced independently in this session by re-running
`scripts/run_carry_shadow_replay.py` against the exact same frozen inputs
above and confirming identical coverage counts and classification counts
before any source change was made.

## 2. The old `BLOCKED_COVERAGE` replay at ~61 cycles (abandoned, never committed)

This was an earlier, **abandoned** driver design tried while building
`scripts/run_carry_shadow_replay.py`, before commit 8 (`1222625`) settled
on the single real `decide()` call approach that actually shipped.
Commit 8's own message documents what it was and why it was dropped:

> Fixes the structural blocker found while building
> `scripts/run_carry_shadow_replay.py` [...]: `MultiLegBacktester.run()`
> force-closes every still-open position at its OWN `end` argument when a
> backtest window ends, so "genuinely still open" is structurally
> unobservable through any number of independently truncated re-runs of
> the backtester, at any cadence -- each such call always shows
> everything already closed exactly at that call's own truncation point.
> This invalidated a multi-cycle truncate-and-rerun simulation approach
> and produced zero MARK events end to end.

That "multi-cycle truncate-and-rerun simulation approach" is the ~61-cycle
`BLOCKED_COVERAGE` run: one independently-truncated `MultiLegBacktester`
call per day across the ~60-day frozen window (`paper_start` 2026-05-29 →
`end` 2026-07-28, i.e. ~61 daily truncation points), each re-run showing
every position already force-closed at its own cutoff -- zero MARK
events survived, `missing_coverage` was non-empty, verdict
`BLOCKED_COVERAGE`.

**No git commit, report JSON, or JSONL for this run exists anywhere in
this repository's history** -- `git log --all --follow` on
`scripts/run_carry_shadow_replay.py` shows exactly one version, created
whole in commit 8 already using the corrected single-call design; `git
log --all -p` across every tracked file turns up no other reference to a
61-cycle or `BLOCKED_COVERAGE` run. It was iterated on and discarded
entirely within the uncommitted working state of that development
session. This section records it as historical context (per Phase 4E
commit 10 point 2's instruction to log it separately from the kept
result) precisely because it left no artifact of its own to hash --
nothing here should be read as a verified data point, only as the
documented reason the truncate-and-rerun design was rejected in favor of
the one actually committed.

## 3. Repository hygiene: tracked `.pyc` files

`git ls-files | grep -E '\.py[cod]$'` found 25 compiled bytecode files
tracked by git, none under this phase's own tree (`__pycache__/`,
`ai/__pycache__/`, `frontend_pipeline/__pycache__/`, none under `src/`,
`tests/`, or `data/`) -- all pre-existing, unrelated to Phase 4E. `.gitignore`
already excluded `__pycache__/`, `*.pyc`, `*.pyo` (these files were
tracked before that exclusion was added; a gitignore entry never untracks
an already-tracked file). This commit:

- adds `*.py[cod]` to `.gitignore` (covers `.pyc`/`.pyo`/`.pyd` in the
  one conventional Python glob, on top of the existing explicit entries);
- runs `git rm --cached` on exactly those 25 already-tracked files --
  index only, working-tree copies untouched, nothing else in the tree
  restored or deleted.

No other local modification is touched by this commit.
